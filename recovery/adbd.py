#!/usr/bin/python3
"""A small ADB daemon for the A22x recovery (2026-11-10).

`adb shell`, `adb push`, `adb pull`, `adb exec-out` and `adb reboot` work as with TWRP. The
transport is TCP on the USB network only (`adb connect 172.16.42.1:5555`); USB FunctionFS can be
added on the same protocol code. A computer must prove a key that was approved on the phone; a new
key is offered to the phone screen first and is stored only after it was allowed there.

Protocol: AOSP packages/modules/adb/protocol.txt and SYNC.TXT (version 0x01000001, sync v1,
legacy shell). Nothing here runs a program sent by the computer except the shell it asked for.
"""
# SPDX-License-Identifier: MIT
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

A_CNXN, A_AUTH, A_OPEN, A_OKAY, A_CLSE, A_WRTE, A_STLS = (0x4e584e43, 0x48545541, 0x4e45504f, 0x59414b4f,
                                                          0x45534c43, 0x45545257, 0x534c5453)
VERSION = 0x01000001
MAX_PAYLOAD = 256 * 1024
AUTH_TOKEN, AUTH_SIGNATURE, AUTH_RSAPUBLICKEY = 1, 2, 3
SHA1_PREFIX = bytes.fromhex('3021300906052b0e03021a05000414')
SYNC_DATA_MAX = 64 * 1024
BANNER = b'recovery::ro.product.name=a22x;ro.product.model=SM-A226B;ro.product.device=a22x;features=\0'


class Closed(Exception):
    pass


def parse_android_key(text):
    """(n, e, comment) of an adb public key line: base64 of RSAPublicKey, then a comment."""
    blob, _, comment = text.strip().partition(' ')
    raw = base64.b64decode(blob.split('\0')[0], validate=True)
    words = struct.unpack_from('<I', raw, 0)[0]
    if not 32 <= words <= 128 or len(raw) < 8 + 8 * words + 4:
        raise ValueError('not an adb public key')
    n = int.from_bytes(raw[8:8 + 4 * words], 'little')
    e = struct.unpack_from('<I', raw, 8 + 8 * words)[0]
    if e not in (3, 65537) or n.bit_length() < 1024:
        raise ValueError('not an adb public key')
    return n, e, comment.strip('\0 ')[:96]


def fingerprint(text):
    blob = text.strip().split(' ')[0].split('\0')[0]
    return 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(blob)).digest()).decode().rstrip('=')


def verify(n, e, token, signature):
    """RSA PKCS#1 v1.5 over the token taken as a SHA-1 digest, as adb signs it."""
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    block = pow(int.from_bytes(signature, 'big'), e, n).to_bytes(size, 'big')
    expected = b'\x00\x01' + b'\xff' * (size - 3 - len(SHA1_PREFIX) - len(token)) + b'\x00' + SHA1_PREFIX + token
    return block == expected


class Keys:
    """Approved computers, one adb public key per line. PERSIST(lines) keeps them past a restart."""
    def __init__(self, path, persist=None):
        self.path, self.persist = Path(path), persist
        self.lock = threading.Lock()

    def lines(self):
        try:
            return [line for line in self.path.read_text().splitlines() if line.strip()]
        except FileNotFoundError:
            return []

    def keys(self):
        found = []
        for line in self.lines():
            try:
                found.append(parse_android_key(line))
            except (ValueError, struct.error):
                continue
        return found

    def merge(self, new_lines):
        """NEW_LINES added to the RAM copy in one replace; True when anything was new. One lock for
        this and add: the load from cache appended to a file add had just replaced (2026-11-12 review)."""
        with self.lock:
            lines = self.lines()
            extra = [line.strip() for line in new_lines if line.strip() and line.strip() not in lines]
            if not extra:
                return False
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.path.with_name('.' + self.path.name + '.new')
            temporary.write_text(''.join(item + '\n' for item in lines + list(dict.fromkeys(extra))))
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            return True

    def add(self, line):
        parse_android_key(line)
        if self.merge([line]) and self.persist:
            self.persist(self.lines())


class Approval:
    """A new key is shown on the phone: a request file, then the answer the interface writes."""
    def __init__(self, base, timeout=120, poll=0.2):
        self.base, self.timeout, self.poll = Path(base), timeout, poll

    def __call__(self, key_line, peer):
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = os.urandom(8).hex()
        request = self.base / f'request-{name}.json'
        allowed, denied = self.base / f'allowed-{name}', self.base / f'denied-{name}'
        _, _, comment = parse_android_key(key_line)
        temporary = self.base / f'.{name}'
        temporary.write_text(json.dumps({'id': name, 'fingerprint': fingerprint(key_line), 'comment': comment,
                                         'peer': peer[:64]}))
        temporary.replace(request)
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                if allowed.exists():
                    return True
                if denied.exists():
                    return False
                time.sleep(self.poll)
            return False
        finally:
            for path in (request, allowed, denied):
                path.unlink(missing_ok=True)


class Connection:
    def __init__(self, transport, keys, approve, peer, log=None, gate=None):
        self.transport, self.keys, self.approve, self.peer, self.gate = transport, keys, approve, peer, gate
        self.write_lock = threading.Lock()
        self.streams, self.next_id, self.lock = {}, 1, threading.Lock()
        self.max_payload, self.online, self.token = MAX_PAYLOAD, False, b''
        self.log = log or (lambda *args: None)

    # -- messages
    def send(self, command, arg0, arg1, data=b''):
        header = struct.pack('<6I', command, arg0, arg1, len(data), sum(data) & 0xffffffff, command ^ 0xffffffff)
        # Header and payload are separate USB transfers: the computer reads 24 bytes first, and a
        # longer transfer overflowed that read ("read failed", on the phone, 2026-11-10).
        with self.write_lock:
            self.transport.write(header)
            if data:
                self.transport.write(data)

    def receive(self):
        header = self.transport.read(24)
        command, arg0, arg1, length, check, magic = struct.unpack('<6I', header)
        if magic != command ^ 0xffffffff or length > 1024 * 1024:
            raise Closed('bad message')
        data = self.transport.read(length) if length else b''
        return command, arg0, arg1, data

    # -- handshake
    def auth_request(self):
        self.token = os.urandom(20)
        self.send(A_AUTH, AUTH_TOKEN, 0, self.token)

    def connected(self, maxdata):
        # The recovery password, when one is set, is asked on the phone as for SSH.
        if self.gate is not None and not self.gate(self.peer):
            raise Closed('access refused on the phone')
        self.max_payload = max(4096, min(MAX_PAYLOAD, maxdata or MAX_PAYLOAD))
        self.online = True
        self.send(A_CNXN, VERSION, MAX_PAYLOAD, BANNER)
        self.log('online', self.peer)

    def serve(self):
        maxdata, offered = MAX_PAYLOAD, False
        try:
            while True:
                command, arg0, arg1, data = self.receive()
                if command == A_CNXN:
                    maxdata = arg1
                    self.online = False
                    self.auth_request()
                elif command == A_AUTH and not self.online:
                    if arg0 == AUTH_SIGNATURE:
                        if self.token and any(verify(n, e, self.token, data) for n, e, _ in self.keys.keys()):
                            self.connected(maxdata)
                        else:
                            self.auth_request()
                    elif arg0 == AUTH_RSAPUBLICKEY and not offered:
                        offered = True
                        line = data.rstrip(b'\0').decode('ascii', 'replace')
                        try:
                            parse_android_key(line)
                        except (ValueError, struct.error):
                            raise Closed('bad key')
                        if self.approve(line, self.peer):
                            self.keys.add(line)
                            self.connected(maxdata)
                        else:
                            raise Closed('key refused')
                elif not self.online:
                    raise Closed('not authorized')
                elif command == A_OPEN:
                    self.open(arg0, data.rstrip(b'\0').decode('utf-8', 'replace'))
                elif command in (A_WRTE, A_OKAY, A_CLSE):
                    stream = self.streams.get(arg1)
                    if stream is None:
                        if command == A_WRTE:
                            self.send(A_CLSE, 0, arg0)
                        continue
                    if command == A_WRTE:
                        stream.incoming(data)
                    elif command == A_OKAY:
                        stream.acknowledged()
                    else:
                        stream.closed_by_host()
        except (Closed, OSError, struct.error):
            pass
        finally:
            for stream in list(self.streams.values()):
                stream.close(notify=False)
            self.transport.close()

    def open(self, remote, service):
        with self.lock:
            local = self.next_id
            self.next_id += 1
        handler = SERVICES.get(service.split(':', 1)[0] + ':') or SERVICES.get(service.split(',', 1)[0])
        if handler is None:
            self.send(A_CLSE, 0, remote)
            return
        stream = Stream(self, local, remote)
        self.streams[local] = stream
        self.send(A_OKAY, local, remote)
        threading.Thread(target=stream.run, args=(handler, service), daemon=True).start()


class Stream:
    """One service: data to the computer waits for OKAY; data from it is acknowledged when taken."""
    def __init__(self, connection, local, remote):
        self.c, self.local, self.remote = connection, local, remote
        self.ready = threading.Event()
        self.ready.set()
        self.inbox, self.inbox_lock, self.inbox_event = [], threading.Lock(), threading.Event()
        self.ended = False

    def write(self, data):
        view = memoryview(data)
        while view.nbytes:
            chunk = bytes(view[:self.c.max_payload])
            view = view[len(chunk):]
            while not self.ready.wait(0.5):
                if self.ended:
                    raise Closed('stream closed')
            if self.ended:
                raise Closed('stream closed')
            self.ready.clear()
            self.c.send(A_WRTE, self.local, self.remote, chunk)

    def acknowledged(self):
        self.ready.set()

    def incoming(self, data):
        # 2026-11-12 review: OKAY was sent on arrival, so the computer sent at link speed and a push
        # to a slower card piled up in RAM until the recovery ran out of memory. The computer sends
        # the next block of a stream only after its OKAY, which now comes when the data is taken.
        with self.inbox_lock:
            self.inbox.append(data)
            self.inbox_event.set()

    def read(self, timeout=None):
        """Bytes from the computer; b'' when the stream ended."""
        while True:
            with self.inbox_lock:
                if self.inbox:
                    data = b''.join(self.inbox)
                    taken = len(self.inbox)
                    self.inbox.clear()
                    self.inbox_event.clear()
                else:
                    taken = 0
                    if self.ended:
                        return b''
            if taken:
                for _ in range(taken):
                    with contextlib.suppress(OSError, Closed):
                        self.c.send(A_OKAY, self.local, self.remote)
                return data
            if not self.inbox_event.wait(timeout):
                return None

    def read_exact(self, size, pending):
        while len(pending) < size:
            data = self.read()
            if not data:
                raise Closed('stream ended')
            pending += data
        return pending[:size], pending[size:]

    def closed_by_host(self):
        self.ended = True
        self.ready.set()
        self.inbox_event.set()
        self.c.streams.pop(self.local, None)

    def close(self, notify=True):
        if not self.ended:
            self.ended = True
            self.ready.set()
            self.inbox_event.set()
            if notify:
                try:
                    self.c.send(A_CLSE, self.local, self.remote)
                except OSError:
                    pass
        self.c.streams.pop(self.local, None)

    def run(self, handler, service):
        try:
            handler(self, service)
        except (Closed, OSError) as exc:
            self.c.log('stream', service.split(':')[0], exc)
        finally:
            self.close()


# -- services
def shell_environment():
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'TERM': 'xterm', 'SHELL': '/bin/sh',
           'LANG': 'C.UTF-8', 'ADB': '1'}
    return env


def service_shell(stream, service):
    command = service.split(':', 1)[1]
    argv = ['/bin/sh', '-l'] if not command else ['/bin/sh', '-c', command]
    pid, master = pty.fork()
    if pid == 0:
        os.chdir('/')
        os.execve(argv[0], argv, shell_environment())
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.05)
            if master in ready:
                try:
                    data = os.read(master, 16384)
                except OSError:
                    data = b''
                if not data:
                    break
                stream.write(data)
            pending = stream.read(timeout=0)
            if pending is None:
                continue
            if pending == b'' and stream.ended:
                break
            if pending:
                os.write(master, pending)
    finally:
        os.close(master)
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)


def service_exec(stream, service):
    """Raw output without a terminal (adb exec-out, adb exec-in)."""
    command = service.split(':', 1)[1]
    process = subprocess.Popen(['/bin/sh', '-c', command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, cwd='/', env=shell_environment())

    def feed():
        try:
            while True:
                data = stream.read()
                if not data:
                    break
                process.stdin.write(data)
                process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
    threading.Thread(target=feed, daemon=True).start()
    try:
        while True:
            data = process.stdout.read1(SYNC_DATA_MAX)
            if not data:
                break
            stream.write(data)
    finally:
        process.kill()
        process.wait()


def sync_reply(stream, ident, payload=b''):
    stream.write(ident + struct.pack('<I', len(payload)) + payload)


def sync_fail(stream, message):
    sync_reply(stream, b'FAIL', message.encode('utf-8', 'replace')[:1024])


def service_sync(stream, service):
    pending = b''
    while True:
        header, pending = stream.read_exact(8, pending)
        ident, length = header[:4], struct.unpack('<I', header[4:])[0]
        if ident == b'QUIT':
            return
        if length > 4096:
            raise Closed('sync path too long')
        raw, pending = stream.read_exact(length, pending)
        path = raw.decode('utf-8', 'surrogateescape')
        if ident == b'STAT':
            try:
                st = os.lstat(path)
                stream.write(b'STAT' + struct.pack('<3I', st.st_mode & 0xffffffff, st.st_size & 0xffffffff, int(st.st_mtime) & 0xffffffff))
            except OSError:
                stream.write(b'STAT' + struct.pack('<3I', 0, 0, 0))
        elif ident == b'LIST':
            try:
                names = sorted(os.listdir(path))
            except OSError:
                names = []
            out = bytearray()
            for name in ['.', '..'] + names:
                try:
                    st = os.lstat(os.path.join(path, name))
                except OSError:
                    continue
                encoded = name.encode('utf-8', 'surrogateescape')
                out += b'DENT' + struct.pack('<4I', st.st_mode & 0xffffffff, st.st_size & 0xffffffff,
                                             int(st.st_mtime) & 0xffffffff, len(encoded)) + encoded
                if len(out) > SYNC_DATA_MAX:
                    stream.write(bytes(out))
                    out.clear()
            out += b'DONE' + bytes(16)
            stream.write(bytes(out))
        elif ident == b'RECV':
            try:
                fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            except OSError as exc:
                sync_fail(stream, f'{path}: {exc.strerror}')
                continue
            try:
                while True:
                    data = os.read(fd, SYNC_DATA_MAX)
                    if not data:
                        break
                    sync_reply(stream, b'DATA', data)
            finally:
                os.close(fd)
            stream.write(b'DONE' + bytes(4))
        elif ident == b'SEND':
            target, _, mode = path.rpartition(',')
            try:
                mode = int(mode) & 0o7777 if mode else 0o644
            except ValueError:
                target, mode = path, 0o644
            temporary = target + '.adb-part'
            problem, fd = None, None
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, mode)
            except OSError as exc:
                problem = f'{target}: {exc.strerror}'
            mtime = 0
            try:
                while True:
                    header, pending = stream.read_exact(8, pending)
                    ident, length = header[:4], struct.unpack('<I', header[4:])[0]
                    if ident == b'DATA':
                        if length > SYNC_DATA_MAX:
                            raise Closed('sync data too large')
                        data, pending = stream.read_exact(length, pending)
                        if fd is not None and problem is None:
                            try:
                                os.write(fd, data)
                            except OSError as exc:
                                problem = f'{target}: {exc.strerror}'
                    elif ident == b'DONE':
                        mtime = length
                        break
                    else:
                        raise Closed('unexpected sync message')
            except BaseException:
                # 2026-11-12 review: a push cut short (Ctrl-C, cable) left the file open in this
                # long-lived daemon and the part file on the volume, which then could not be unmounted.
                if fd is not None:
                    os.close(fd)
                    with contextlib.suppress(OSError):
                        os.unlink(temporary)
                raise
            if fd is not None:
                # 2026-11-12, on the owner's phone: a pushed recovery image read back with 16 MiB of
                # zeros in its middle after the volume was mounted again, though adb had said "pushed"
                # and the file hashed right from memory. What cannot be written must be said here.
                if problem is None:
                    try:
                        os.fsync(fd)
                    except OSError as exc:
                        problem = f'{target}: {exc.strerror}'
                os.close(fd)
            if problem is None:
                try:
                    os.replace(temporary, target)
                    if mtime:
                        os.utime(target, (mtime, mtime))
                except OSError as exc:
                    problem = f'{target}: {exc.strerror}'
            if problem is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                sync_fail(stream, problem)
            else:
                stream.write(b'OKAY' + bytes(4))
        else:
            sync_fail(stream, 'unsupported sync request')
            return


def service_reboot(stream, service):
    target = service.split(':', 1)[1] or 'system'
    command = os.environ.get('A22X_ADB_REBOOT', '/usr/bin/a22x-reboot')
    stream.write(b'')
    stream.close()
    os.sync()
    if os.path.exists(command):
        subprocess.run([command, {'system': 'normal', '': 'normal', 'bootloader': 'download'}.get(target, target)],
                       check=False, timeout=30)


SIDELOAD_LIMIT = 16 << 30
SIDELOAD_EXTENSIONS = {('tar', 'gzip'): '.tar.gz', ('tar', 'xz'): '.tar.xz', ('tar', 'zstd'): '.tar.zst',
                       ('tar', 'bzip2'): '.tar.bz2', ('tar', None): '.tar', ('compressed-image', 'xz'): '.img.xz',
                       ('compressed-image', 'gzip'): '.img.gz', ('compressed-image', 'zstd'): '.img.zst',
                       ('compressed-image', 'bzip2'): '.img.bz2', ('qcow2', None): '.qcow2', ('iso', None): '.iso'}


@contextlib.contextmanager
def sideload_store(size, log=None):
    """Where a file of SIZE bytes arriving over adb goes, under the recovery's operation lock like
    any other storage job: the image store on a Linux userdata, else a card's store or cache
    (2026-11-11, while Android holds userdata). Yields {'folder', 'volume', 'token', 'dir'}, or None
    when there is no place with room, or another job holds the storage."""
    override = os.environ.get('A22X_ADB_SIDELOAD_DIR')
    if override:
        yield {'folder': Path(override), 'volume': os.environ.get('A22X_ADB_SIDELOAD_VOLUME', ''), 'token': '', 'dir': ''}
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import distro
    import filesystem as fs
    with contextlib.ExitStack() as stack:
        place = None
        try:
            context = fs.DeviceContext()
            stack.enter_context(context.operation_lock())
            place = stack.enter_context(distro.sideload_place(context, size))
        except (OSError, ValueError, fs.FilesystemError) as exc:
            if log:
                log('sideload refused:', getattr(exc, 'code', '') or type(exc).__name__, str(exc)[:160])
        yield place


def partition_image(head):
    """Which of the phone's partitions an image is for, from its first bytes (2026-11-10): adb
    sideload keeps such an image in the store for "Flash a partition", as TWRP's Install Image.
    A recovery image carries its own DTBO after kernel and ramdisk (boot header v1 and v2)."""
    if head[:8] == b'ANDROID!':
        version = struct.unpack_from('<I', head, 40)[0] if len(head) >= 44 else 0
        dtbo = struct.unpack_from('<I', head, 1632)[0] if len(head) >= 1636 else 0
        return 'recovery' if version in (1, 2) and dtbo > 0 else 'boot'
    if head[:8] == b'VNDRBOOT':
        return 'vendor_boot'
    if head[:4] == b'AVB0':
        return 'vbmeta'
    if head[:4] == b'\xd7\xb7\xab\x1e':
        return 'dtbo'
    if head[:4] == b'\x3a\xff\x26\xed':
        return 'sparse'
    if head[4096:4100] == b'gDla':
        return 'super'
    return None


def sideload_name(path):
    """A file name for what came in: a ZIP for Android, an image of one of the phone's partitions,
    or a system image the installer reads."""
    with open(path, 'rb') as f:
        head = f.read(4100)
    stamp = time.strftime('sideload-%Y%m%d-%H%M%S', time.gmtime())
    if head[:4] == b'PK\x03\x04':
        return stamp + '.zip'
    kind = partition_image(head)
    if kind:
        return '%s-%s.img' % (stamp, kind)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import image_import
    try:
        info = image_import.describe(path)
    except Exception:
        return None
    return stamp + SIDELOAD_EXTENSIONS.get((info.get('format'), info.get('compression')), '.img')


def service_sideload(stream, service):
    """adb sideload FILE (sideload-host:SIZE:BLOCK): the phone asks for every block in turn and keeps
    the file in its image store; an Android ZIP waits there for the Android installer (2026-11-10)."""
    try:
        _, size, block = service.split(':')[:3]
        size, block = int(size), int(block)
    except ValueError:
        raise Closed('bad sideload request')
    if not 0 < size <= SIDELOAD_LIMIT or not 4096 <= block <= MAX_PAYLOAD:
        stream.write(b'FAILFAIL')
        return
    with sideload_store(size, stream.c.log) as place:
        if place is None:
            stream.write(b'FAILFAIL')
            return
        folder = place['folder']
        if os.environ.get('A22X_ADB_SIDELOAD_DIR'):
            vfs = os.statvfs(folder)
            if vfs.f_bavail * vfs.f_frsize < size + (256 << 20):
                stream.write(b'FAILFAIL')
                return
        name = receive_sideload(stream, folder, size, block)
        if name and place['volume']:
            announce_sideload(getattr(stream.c.approve, 'base', None), place, name, size)


def announce_sideload(base, place, name, size):
    """Tell the interface what arrived (2026-11-11): it opens a ZIP's review at once, as TWRP's
    sideload installs it, and names any other file. sideload-<time>.json beside the key requests."""
    if base is None:
        return
    kind = 'zip' if name.endswith('.zip') else ('image' if name.endswith('.img') else 'system')
    record = {'file': name, 'volume': place['volume'], 'token': place['token'], 'path': place['dir'] + '/' + name,
              'kind': kind, 'size': size}
    base = Path(base)
    with contextlib.suppress(OSError):
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = '%d-%s' % (time.time_ns(), os.urandom(3).hex())
        temporary = base / ('.sideload-' + stamp)
        temporary.write_text(json.dumps(record))
        temporary.replace(base / ('sideload-%s.json' % stamp))


def receive_sideload(stream, folder, size, block):
    part = folder / '.sideload-part'
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, 0o644)
    done = False
    try:
        pending = b''
        for number in range((size + block - 1) // block):
            stream.write(b'%08d' % number)
            data, pending = stream.read_exact(min(block, size - number * block), pending)
            os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = None
        name = sideload_name(part)
        if name is None:
            stream.write(b'FAILFAIL')
            return
        # Two files in the same second keep both: the later one is numbered.
        target, stem, suffix, number = folder / name, name.split('.', 1)[0], name.split('.', 1)[1], 2
        while target.exists():
            target = folder / ('%s-%d.%s' % (stem, number, suffix))
            number += 1
        os.replace(part, target)
        done = True
        stream.write(b'DONEDONE')
        return target.name
    finally:
        if fd is not None:
            os.close(fd)
        if not done:
            part.unlink(missing_ok=True)


def service_root(stream, service):
    stream.write(b'adbd is already running as root\n')


SERVICES = {'shell:': service_shell, 'exec:': service_exec, 'sync:': service_sync, 'reboot:': service_reboot,
            'sideload-host:': service_sideload,
            'root:': service_root, 'unroot:': service_root, 'remount:': service_root}


class SocketTransport:
    def __init__(self, sock):
        self.sock = sock

    def read(self, size):
        out = bytearray()
        while len(out) < size:
            data = self.sock.recv(size - len(out))
            if not data:
                raise Closed('disconnected')
            out += data
        return bytes(out)

    def write(self, data):
        self.sock.sendall(data)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# USB FunctionFS (2026-11-10): the interface adb looks for (class 0xff, subclass 0x42, protocol 1)
# with one bulk OUT and one bulk IN endpoint, as in AOSP adbd. Descriptors v2 with full, high and
# super speed sets; ep1 is OUT (computer to phone), ep2 is IN.
FFS_DESCRIPTORS_MAGIC_V2, FFS_STRINGS_MAGIC = 3, 2
FFS_HAS_FS, FFS_HAS_HS, FFS_HAS_SS = 1, 2, 4
FFS_BIND, FFS_UNBIND, FFS_ENABLE, FFS_DISABLE, FFS_SETUP, FFS_SUSPEND, FFS_RESUME = range(7)


def ffs_descriptors():
    interface = struct.pack('<9B', 9, 4, 0, 0, 2, 0xff, 0x42, 1, 1)

    def endpoint(address, size):
        return struct.pack('<4BHB', 7, 5, address, 2, size, 0)
    companion = struct.pack('<4BH', 6, 0x30, 0, 0, 0)
    full = interface + endpoint(0x01, 64) + endpoint(0x82, 64)
    high = interface + endpoint(0x01, 512) + endpoint(0x82, 512)
    sup = interface + endpoint(0x01, 1024) + companion + endpoint(0x82, 1024) + companion
    body = struct.pack('<3I', 3, 3, 5) + full + high + sup
    return struct.pack('<3I', FFS_DESCRIPTORS_MAGIC_V2, 12 + len(body), FFS_HAS_FS | FFS_HAS_HS | FFS_HAS_SS) + body


def ffs_strings():
    body = struct.pack('<H', 0x0409) + b'ADB Interface\0'
    return struct.pack('<4I', FFS_STRINGS_MAGIC, 16 + len(body), 1, 1) + body


class EndpointTransport:
    """adb messages over the two bulk endpoints. A read returns one USB transfer at most."""
    def __init__(self, out_fd, in_fd):
        self.out_fd, self.in_fd = out_fd, in_fd
        self.lock, self.detached, self.closed = threading.Lock(), False, False

    def detach(self):
        """From the control thread, when the USB configuration goes away: the endpoints are replaced
        by /dev/null under the same numbers, so the serving thread's next read ends the connection.
        Closing them here let another thread's new file take a number the serving thread then wrote
        adb messages into and closed (2026-11-12 review)."""
        with self.lock:
            if self.detached or self.closed:
                return
            self.detached = True
            null = os.open('/dev/null', os.O_RDWR | os.O_CLOEXEC)
            try:
                for fd in (self.out_fd, self.in_fd):
                    with contextlib.suppress(OSError):
                        os.dup2(null, fd, inheritable=False)
            finally:
                os.close(null)

    def read(self, size):
        data = bytearray()
        empty = 0
        while len(data) < size:
            chunk = os.read(self.out_fd, size - len(data))
            if not chunk:
                # The computer ends a transfer of whole packets with a zero-length packet: one empty
                # read is that, never two in a row (a 20 MB push stopped on it, 2026-11-10).
                empty += 1
                if empty > 1:
                    raise Closed('USB disconnected')
                continue
            empty = 0
            data += chunk
        return bytes(data)

    def write(self, data):
        view = memoryview(data)
        while view.nbytes:
            written = os.write(self.in_fd, view[:16384])
            view = view[written:]

    def close(self):
        """Only by the serving thread, once."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for fd in (self.out_fd, self.in_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)


def serve_ffs(folder, keys, approve, log=None, gate=None, ready=None):
    """Publish the adb interface on FUNCTIONFS FOLDER, then serve a computer whenever the USB
    configuration is enabled; a disable or unbind ends that connection."""
    folder = Path(folder)
    control = os.open(folder / 'ep0', os.O_RDWR | os.O_CLOEXEC)
    os.write(control, ffs_descriptors())
    os.write(control, ffs_strings())
    if ready:
        ready()
    session = {'enabled': False, 'current': None, 'thread': None}

    def sessions():
        # 2026-11-12, on the owner's phone: a connection that ended while the cable stayed in (the
        # password refused on the phone, or the computer's adb server restarted) left nobody reading
        # the endpoints, and the computer saw the phone "offline" until the cable was pulled. While
        # the configuration is enabled, every ended connection is followed by a new one.
        while session['enabled']:
            try:
                out_fd = os.open(folder / 'ep1', os.O_RDONLY | os.O_CLOEXEC)
                in_fd = os.open(folder / 'ep2', os.O_WRONLY | os.O_CLOEXEC)
            except OSError:
                time.sleep(0.5)
                continue
            session['current'] = Connection(EndpointTransport(out_fd, in_fd), keys, approve, 'usb', log, gate)
            session['current'].serve()
            session['current'] = None
            time.sleep(0.2)
    while True:
        event = os.read(control, 12)
        if len(event) < 12:
            continue
        kind, request_type = event[8], event[0]
        if kind == FFS_ENABLE and not session['enabled']:
            session['enabled'] = True
            if session['thread'] is None or not session['thread'].is_alive():
                session['thread'] = threading.Thread(target=sessions, daemon=True)
                session['thread'].start()
        elif kind in (FFS_DISABLE, FFS_UNBIND) and session['enabled']:
            session['enabled'] = False
            current = session['current']
            if current is not None:
                current.transport.detach()
        elif kind == FFS_SETUP:
            # No control requests of its own: acknowledge, as AOSP adbd does.
            try:
                os.write(control, b'') if request_type & 0x80 else os.read(control, 0)
            except OSError:
                pass


def serve_tcp(address, port, keys, approve, log=None, ready=None, gate=None):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # The USB network address appears once the gadget is bound: wait for it.
    for attempt in range(600):
        try:
            server.bind((address, port))
            break
        except OSError:
            if attempt == 599:
                raise
            time.sleep(1)
    server.listen(4)
    if ready:
        ready(server.getsockname()[1])
    while True:
        sock, peer = server.accept()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 2026-11-12 review: a computer that went away without a word (cable pulled, laptop asleep)
        # held its connection for ever, and a sideload with it the storage lease. Keepalive ends it
        # within about a minute.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 60000)
        connection = Connection(SocketTransport(sock), keys, approve, '%s:%d' % peer, log, gate)
        threading.Thread(target=connection.serve, daemon=True).start()


KEYS_RELATIVE = Path('a22x-recovery/adb_keys')


def cache_keys(mode, action):
    """ACTION(path of the keys) on the recovery's cache volume, as the settings are kept; None when
    that volume cannot be used now (the keys then last until the recovery restarts)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import filesystem as fs
    import settings
    from operation_lock import operation_lock
    context = fs.DeviceContext()
    try:
        with operation_lock(context.lock_path, context.mode):
            with settings.volume_root(context, 'cache', mode) as root:
                return action(root / KEYS_RELATIVE)
    except (OSError, ValueError, fs.FilesystemError):
        return None


def store_keys(lines):
    def write(path):
        path.parent.mkdir(mode=0o700, exist_ok=True)
        # 2026-11-12 review: the RAM list replaced the file, so when the load at start had failed
        # (storage busy), approving one computer erased every computer kept before. Kept ones stay.
        kept = [line.strip() for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
        lines_all = kept + [line for line in lines if line not in kept]
        temporary = path.with_name('.adb_keys.new')
        temporary.write_text(''.join(line + '\n' for line in lines_all))
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        return True
    return cache_keys('rw', write)


def load_keys(keys):
    """The approved computers saved on cache, into the RAM copy the daemon reads."""
    lines = cache_keys('ro', lambda path: path.read_text().splitlines() if path.is_file() else [])
    if lines is None:
        return False
    good = []
    for line in lines:
        try:
            parse_android_key(line)
        except (ValueError, struct.error):
            continue
        good.append(line)
    keys.merge(good)
    return True


def load_keys_soon(keys, tries=30, pause=2.0):
    """The load at start, again until it works: init's one-shot steps hold the storage then."""
    for _ in range(tries):
        if load_keys(keys):
            return
        time.sleep(pause)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--address', default='172.16.42.1', help='Listen only here (the USB network)')
    parser.add_argument('--port', type=int, default=5555)
    parser.add_argument('--keys', default=os.environ.get('A22X_ADB_KEYS', '/run/a22x-recovery/adb/adb_keys'))
    parser.add_argument('--requests', default=os.environ.get('A22X_ADB_REQUESTS', '/run/a22x-recovery/adb'))
    parser.add_argument('--persist', action='store_true', help='Keep approved computers on the cache volume')
    parser.add_argument('--ffs', help='Also serve adb over USB on this FunctionFS mount (written before the UDC binds)')
    args = parser.parse_args(argv)
    log = lambda *items: print('adbd:', *items, file=sys.stderr, flush=True)
    keys = Keys(args.keys, store_keys if args.persist else None)

    # 2026-11-12 review: the recovery's password was asked only with --persist; an approved key
    # gives a root shell, so the access settings are asked always, as SSH and the console do.
    def gate(peer):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import access_gate
        return access_gate.Gate().allow('adb', peer)
    if args.persist:
        load_keys(keys) or threading.Thread(target=load_keys_soon, args=(keys,), daemon=True).start()
    # The USB interface is published first: init binds the UDC only once it is there.
    if args.ffs:
        threading.Thread(target=serve_ffs, args=(args.ffs, keys, Approval(args.requests), log, gate),
                         daemon=True).start()
    serve_tcp(args.address, args.port, keys, Approval(args.requests), log, gate=gate)


if __name__ == '__main__':
    raise SystemExit(main())
