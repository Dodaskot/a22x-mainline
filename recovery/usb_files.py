#!/usr/bin/env python3
"""Explicit MTP export of one mounted Linux volume; foreground supervisor owns it.

The service holds the recovery operation lease for its complete lifetime. The
native responder inherits the lease and is confined to a private mount/IPC/net
namespace and a read-only RAM chroot containing the one export and FunctionFS.
status/stop use root-only IPC, so stopping never needs the held storage lease.
Fixture mode exercises the same IPC/lease lifecycle without USB or mounts.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations
import argparse
from contextlib import ExitStack
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

import filesystem as fs
from operation_lock import operation_lock, ENV_FD

RUNTIME = Path('/etc/a22x-recovery/usb-files-runtime.json')
GADGET = Path('/sys/kernel/config/usb_gadget/a22x_recovery')
SCHEMA = 'a22x.usb-files.service.v1'
MAX_JSON = 65536
MAX_LOG = 16384
START_SECONDS = 10.0
STOP_SECONDS = 8.0
libc = ctypes.CDLL(None, use_errno=True)


def require(test, code, message):
    if not test:
        fs.fail(code, message)


def owner_json(path):
    path = fs.absolute_no_symlinks(path)
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
            not st.st_mode & 0o022 and st.st_nlink == 1,
            'UNSAFE_STATE', 'USB state must be an owned, private regular file')
    return fs.regular_json(path, MAX_JSON)


def process_start(pid):
    try:
        parts = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if parts[0] == 'Z' else parts[19]
    except (OSError, IndexError):
        return None


def process_live(record):
    return (type(record.get('pid')) is int and record['pid'] > 1 and
            record.get('process_start') is not None and
            process_start(record['pid']) == record['process_start'])


def private_directory(path):
    path = fs.absolute_no_symlinks(path, missing=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and
            not st.st_mode & 0o077, 'UNSAFE_STATE', 'USB runtime directory must be private')
    return path


class Context:
    def __init__(self, args):
        self.fs = fs.FixtureContext(args.fixture) if args.mode == 'fixture' else fs.DeviceContext()
        self.mode = self.fs.mode
        require((self.mode == 'fixture') == bool(args.fixture), 'INVALID_ARGUMENT',
                'Fixture mode requires an explicit fixture root')
        self.base = (self.fs.root / '.usb-files') if self.mode == 'fixture' else fs.RAM_STATE / 'usb-files'
        self.boot_id = getattr(self.fs, 'boot_id', fs.digest({'fixture': str(self.base)}))
        self.record_path = self.base / 'state.json'
        # AF_UNIX pathname length is 108 bytes. Fixture project paths can be
        # longer; use a distinct private temporary IPC directory in that mode.
        self.socket_dir = (Path('/tmp') / ('a22x-usb-fixture-' + fs.digest(str(self.base))[:24])
                           if self.mode == 'fixture' else self.base)
        self.socket_path = self.socket_dir / 'control.sock'
        self.singleton_path = self.base / 'supervisor.lock'

    def record(self):
        if not self.record_path.exists():
            return None
        value = owner_json(self.record_path)
        require(value.get('schema') == SCHEMA and value.get('boot_id') == self.boot_id and
                value.get('mode') == self.mode, 'SERVICE_STALE', 'USB receipt belongs to another boot')
        return value

    def save(self, value):
        fs.atomic_state(self.record_path, value)

    def runtime(self):
        if self.mode == 'fixture':
            return {}
        manifest = owner_json(RUNTIME)
        require(manifest.get('schema') == 'a22x.usb-files.runtime.v1',
                'USB_RUNTIME_UNAVAILABLE', 'USB runtime manifest is not qualified')
        result = {}
        for key, expected_path in (('responder', '/bin/umtprd'), ('sandbox', '/bin/a22x-mtp-sandbox')):
            spec = manifest.get(key, {})
            require(spec.get('path') == expected_path, 'USB_RUNTIME_UNAVAILABLE', 'USB runtime path changed')
            fs.require_hash(spec.get('sha256'))
            fd = fs.open_regular(fs.absolute_no_symlinks(expected_path))
            try:
                st = os.fstat(fd)
                require(st.st_uid == 0 and st.st_nlink == 1 and not st.st_mode & 0o022 and
                        st.st_mode & 0o111 and st.st_size < 8 * 1024 * 1024 and
                        fs.hash_fd(fd) == spec['sha256'], 'USB_RUNTIME_UNAVAILABLE',
                        'The qualified USB executable changed')
            finally:
                os.close(fd)
            result[key] = spec
        return result


def permitted_volume(volume):
    return (volume['id'] == 'userdata' or volume.get('external') is True or
            (volume.get('mode') == 'fixture' and re.fullmatch(r'sdcard-p[0-9]+', volume['id']) is not None))


def mount_token(context, pin):
    v = pin.volume
    return fs.digest({'boot_id': context.boot_id, 'volume_token': v['volume_token'],
                      'mount_id': v['mount_id'], 'mountpoint': v['mountpoint'],
                      'root_device': pin.stat.st_dev, 'root_inode': pin.stat.st_ino,
                      'read_only': v['read_only'], 'owner': v['mount_owner'],
                      'block_read_only': v.get('block_read_only')})


def candidate(context, pin):
    v = pin.volume
    return {'id': v['id'], 'label': v['id'], 'display_label': v['label'],
            'mountpoint': v['mountpoint'], 'actual_access': 'ro' if v['read_only'] else 'rw',
            'volume_token': v['volume_token'], 'mount_token': mount_token(context, pin),
            'new_uid': 0, 'new_gid': 0}


def candidates(context):
    result = []
    for v in context.fs.volumes():
        if not permitted_volume(v) or not v['mounted'] or not v['mountable']:
            continue
        with fs.PinnedVolume(context.fs, v['id'], v['volume_token']) as pin:
            if context.fs.nested_mounts(v, '/'):
                continue
            result.append(candidate(context, pin))
    return result


def snapshot(context, record=None, available=True, reason=None):
    return {'ok': True, 'mode': context.mode, 'boot_id': context.boot_id,
            'available': available, 'state': record['state'] if record else 'stopped',
            'session_token': record.get('session_token') if record else None,
            'exports': record.get('exports', []) if record else [], 'candidates': [],
            'reason': reason or (record.get('reason') if record else None),
            'metadata_note': 'New files: root:root 0644; folders: root:root 0755. '
                             'MTP clients may delete/recreate an edited file. '
                             'Symlinks and special files are hidden; use Linux backup for Unix metadata.'}


def receive(connection, maximum=MAX_JSON):
    raw = bytearray()
    while b'\n' not in raw:
        chunk = connection.recv(min(4096, maximum + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
        require(len(raw) <= maximum, 'INVALID_REQUEST', 'USB IPC request is too large')
    require(raw.endswith(b'\n') and raw.count(b'\n') == 1, 'INVALID_REQUEST', 'Expected one JSON request')
    value = json.loads(raw)
    require(isinstance(value, dict), 'INVALID_REQUEST', 'Expected a JSON object')
    return value


def request(context, record, operation, token=None):
    require(process_live(record), 'SERVICE_STALE', 'The USB supervisor is no longer running')
    path = fs.absolute_no_symlinks(context.socket_path)
    st = path.lstat()
    require(stat.S_ISSOCK(st.st_mode) and st.st_uid == os.geteuid() and
            stat.S_IMODE(st.st_mode) == 0o600, 'UNSAFE_SOCKET', 'USB control socket must be private')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(STOP_SECONDS + 1 if operation == 'stop' else 2)
        client.connect(str(path))
        pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(uid == os.geteuid() and pid == record['pid'], 'SERVICE_STALE', 'USB control peer changed')
        payload = {'operation': operation, 'boot_id': context.boot_id, 'session_token': token}
        client.sendall(json.dumps(payload).encode() + b'\n')
        client.shutdown(socket.SHUT_WR)
        result = receive(client)
    require(result.get('mode') == context.mode and result.get('boot_id') == context.boot_id,
            'INVALID_RESPONSE', 'USB response identity changed')
    return result


def status(context):
    record = context.record()
    if record and process_live(record) and record['state'] in ('active', 'stopping'):
        return request(context, record, 'status')
    if record and not record.get('clean', False):
        return snapshot(context, record, False, 'SERVICE_STALE' if not process_live(record) else None)
    try:
        context.runtime()
        if context.mode == 'device':
            Gadget.validate()
        value = snapshot(context)
        with operation_lock(context.fs.lock_path, context.mode):
            value['candidates'] = candidates(context)
        return value
    except (OSError, fs.FilesystemError) as error:
        code = 'OPERATION_BUSY' if isinstance(error, BlockingIOError) else getattr(error, 'code', 'USB_RUNTIME_UNAVAILABLE')
        return snapshot(context, available=code == 'OPERATION_BUSY', reason=code)


def system_call(function, *args):
    if function(*args):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class Gadget:
    """Own only one new ffs.mtp link/function, restoring the admitted NCM gadget."""
    def __init__(self, session):
        self.session = session
        self.ffs = session / 'ffs'
        self.udc = None
        self.function_created = self.link_created = self.mounted = self.changed = False

    @staticmethod
    def validate():
        fs.absolute_no_symlinks(GADGET)
        config = GADGET / 'configs/c.1'
        links = {p.name: p for p in config.iterdir() if p.is_symlink()}
        # NCM, and the adb interface when the recovery publishes one over USB (2026-11-10); the
        # MTP session leaves that one as it found it.
        expected = {'ncm.usb0'} | ({'ffs.adb'} if 'ffs.adb' in links else set())
        require(set(links) == expected and
                links['ncm.usb0'].resolve() == GADGET / 'functions/ncm.usb0' and
                ('ffs.adb' not in links or links['ffs.adb'].resolve() == GADGET / 'functions/ffs.adb'),
                'USB_GADGET_CHANGED', 'Expected the dedicated NCM recovery gadget')
        require(not (GADGET / 'functions/ffs.mtp').exists(), 'USB_GADGET_CHANGED', 'An MTP function already exists')
        udc = (GADGET / 'UDC').read_text().strip()
        require(re.fullmatch(r'[A-Za-z0-9_.:-]+', udc or '') and (Path('/sys/class/udc') / udc).exists(),
                'USB_GADGET_UNBOUND', 'The recovery gadget must already own its UDC')
        require((GADGET / 'idVendor').read_text().strip().lower() == '0x18d1' and
                (GADGET / 'idProduct').read_text().strip().lower() == '0xd001',
                'USB_GADGET_CHANGED', 'USB device identity differs from the recovery gadget')
        return udc

    def prepare(self):
        self.udc = self.validate()
        self.ffs.mkdir(mode=0o700)
        (GADGET / 'UDC').write_text('\n')
        self.changed = True
        (GADGET / 'functions/ffs.mtp').mkdir()
        self.function_created = True
        system_call(libc.mount, b'mtp', os.fsencode(self.ffs), b'functionfs', ctypes.c_ulong(2 | 4 | 8), None)
        self.mounted = True
        os.symlink(str(GADGET / 'functions/ffs.mtp'), GADGET / 'configs/c.1/ffs.mtp')
        self.link_created = True

    def bind(self, child):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            require(child.poll() is None, 'USB_RESPONDER_FAILED', 'The isolated MTP responder exited during setup')
            if all((self.ffs / f'ep{n}').exists() for n in range(4)):
                (GADGET / 'UDC').write_text(self.udc + '\n')
                require((GADGET / 'UDC').read_text().strip() == self.udc,
                        'USB_BIND_FAILED', 'The UDC did not bind the MTP configuration')
                return
            time.sleep(0.05)
        fs.fail('USB_ENDPOINT_TIMEOUT', 'MTP did not publish FunctionFS descriptors in time')

    def disconnect(self):
        if self.changed:
            observed = (GADGET / 'UDC').read_text().strip()
            require(observed in ('', self.udc), 'USB_GADGET_CHANGED', 'UDC ownership changed during export')
            if observed:
                (GADGET / 'UDC').write_text('\n')

    def restore(self):
        self.disconnect()
        if self.link_created:
            path = GADGET / 'configs/c.1/ffs.mtp'
            require(path.is_symlink() and path.resolve() == GADGET / 'functions/ffs.mtp',
                    'USB_GADGET_CHANGED', 'MTP configuration link changed')
            path.unlink()
            self.link_created = False
        if self.mounted:
            system_call(libc.umount2, os.fsencode(self.ffs), 0)
            self.mounted = False
        if self.function_created:
            (GADGET / 'functions/ffs.mtp').rmdir()
            self.function_created = False
        if self.changed:
            (GADGET / 'UDC').write_text(self.udc + '\n')
            require(self.validate() == self.udc, 'USB_RESTORE_FAILED', 'NCM gadget restoration was not observed')
            self.changed = False


def copy_binary(source, destination, expected):
    fd = fs.open_regular(source)
    try:
        require(fs.hash_fd(fd) == expected, 'USB_RUNTIME_UNAVAILABLE', 'Responder changed before jail creation')
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o555)
        try:
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                position = 0
                while position < len(chunk):
                    position += os.write(out, chunk[position:])
            os.fsync(out)
        finally:
            os.close(out)
    finally:
        os.close(fd)
    verify = fs.open_regular(destination)
    try:
        require(fs.hash_fd(verify) == expected, 'USB_RUNTIME_UNAVAILABLE', 'Jail responder copy changed')
    finally:
        os.close(verify)


def child_start(context, session, pin, lease, access, runtime, gadget):
    minimal_env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C'}
    if context.mode == 'fixture':
        code = 'import ctypes,signal,time,os; p=os.getppid(); ctypes.CDLL(None).prctl(1,9); assert os.getppid()==p; time.sleep(86400)'
        return subprocess.Popen([sys.executable, '-B', '-c', code], pass_fds=(lease,),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=minimal_env)
    root = session / 'root'
    root.mkdir(mode=0o700)
    (root / 'files').mkdir(mode=0o700)
    (root / 'usb').mkdir(mode=0o700)
    copy_binary(runtime['responder']['path'], root / 'umtprd', runtime['responder']['sha256'])
    config = ('usb_functionfs_mode 0x1\nusb_dev_path "/usb/ep0"\n'
              'usb_epin_path "/usb/ep1"\nusb_epout_path "/usb/ep2"\nusb_epint_path "/usb/ep3"\n'
              'loop_on_disconnect 1\nwait 0x1\nshow_hidden_files 1\n'
              'umask 022\ndefault_uid 0\ndefault_gid 0\nsync_when_close 1\n'
              # How a computer names the phone; umtprd's defaults read "Viveris Technologies" (2026-11-09).
              # The serial is fixed and not the phone's own.
              'manufacturer "Samsung"\nproduct "Galaxy A22 5G recovery"\nserial "a22x-recovery"\nfirmware_version "0.6"\n'
              f'storage "/files" "{pin.volume["id"]}" "{access}"\n')
    config_path = root / 'umtprd.conf'
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    try:
        os.write(fd, config.encode('ascii'))
    finally:
        os.close(fd)
    ffs_fd = os.open(gadget.ffs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return subprocess.Popen([runtime['sandbox']['path'], str(root), str(pin.fd), str(ffs_fd), str(lease), access],
                                pass_fds=(pin.fd, ffs_fd, lease), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=minimal_env)
    finally:
        os.close(ffs_fd)


def drain(source, retained):
    try:
        while True:
            chunk = source.read(4096)
            if not chunk:
                break
            retained.extend(chunk[:max(0, MAX_LOG - len(retained))])
    except OSError:
        pass
    finally:
        source.close()


def child_stop(child):
    if child is None:
        return
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
    require(child.poll() is not None, 'USB_RESPONDER_BUSY', 'The MTP process has not exited')


def error_value(context, error):
    code = ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else
            getattr(error, 'code', 'IO_ERROR'))
    return {'ok': False, 'mode': context.mode, 'boot_id': context.boot_id,
            'error': {'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}


def serve(context, args):
    private_directory(context.base)
    private_directory(context.socket_dir)
    fs.require_hash(args.session_token, 'session-token')
    record = {'schema': SCHEMA, 'mode': context.mode, 'boot_id': context.boot_id,
              'session_token': args.session_token, 'pid': os.getpid(),
              'process_start': process_start(os.getpid()), 'state': 'starting',
              'exports': [], 'reason': None, 'clean': False}
    child = gadget = server = socket_identity = session = None
    retained = bytearray()
    error = None
    # All mutations below run with both singleton and storage lease. Borrowing
    # an SSH session lease here would permit its parent to bypass export locking.
    require(ENV_FD not in os.environ, 'INHERITED_SESSION', 'Start USB sharing outside an administrator SSH lease')
    with ExitStack() as stack:
        singleton = os.open(context.singleton_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        stack.callback(os.close, singleton)
        st = os.fstat(singleton)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and st.st_nlink == 1,
                'UNSAFE_STATE', 'USB singleton lock changed')
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = context.record()
        require(not previous or previous.get('clean', False), 'SERVICE_STALE',
                'An unclean previous USB session requires recovery restart')
        try:
            context.save(record)
            lease = stack.enter_context(operation_lock(context.fs.lock_path, context.mode))
            runtime = context.runtime()
            fs.require_hash(args.expected_volume_token, 'expected-volume-token')
            fs.require_hash(args.expected_mount_token, 'expected-mount-token')
            pin = stack.enter_context(fs.PinnedVolume(context.fs, args.volume, args.expected_volume_token))
            require(permitted_volume(pin.volume), 'VOLUME_NOT_ADMITTED', 'USB accepts userdata or an external SD volume only')
            require(not context.fs.nested_mounts(pin.volume, '/'), 'NESTED_MOUNT', 'USB export refuses nested mounts')
            require(mount_token(context, pin) == args.expected_mount_token, 'MOUNT_CHANGED', 'The selected mount changed')
            access = 'ro' if args.read_only or pin.volume['read_only'] else 'rw'
            require(access == 'ro' or pin.volume['write_supported'], 'VOLUME_READ_ONLY', 'Writes are not qualified for this volume')
            entry = candidate(context, pin)
            entry['access'] = access
            record['exports'] = [entry]
            session = private_directory(context.base / ('session-' + args.session_token[:32]))
            record['session_dir'] = str(session)
            if context.mode == 'device':
                gadget = Gadget(session)
                gadget.prepare()
            pin.recheck()
            child = child_start(context, session, pin, lease, access, runtime, gadget)
            thread = threading.Thread(target=drain, args=(child.stdout, retained), daemon=True)
            thread.start()
            record['child_pid'] = child.pid
            record['child_start'] = process_start(child.pid)
            if gadget:
                gadget.bind(child)
            require(child.poll() is None, 'USB_RESPONDER_FAILED', 'The MTP process exited during startup')
            if context.socket_path.exists():
                old = context.socket_path.lstat()
                require(stat.S_ISSOCK(old.st_mode) and old.st_uid == os.geteuid(), 'UNSAFE_SOCKET', 'USB socket path was replaced')
                context.socket_path.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old_mask = os.umask(0o177)
            try:
                server.bind(str(context.socket_path))
            finally:
                os.umask(old_mask)
            socket_identity = context.socket_path.lstat()
            server.listen(4)
            server.settimeout(0.25)
            record['state'] = 'active'
            context.save(record)
            running = True

            def signal_stop(*unused):
                nonlocal running
                running = False
            for number in (signal.SIGTERM, signal.SIGINT):
                signal.signal(number, signal_stop)
            stop_connection = None
            while running:
                require(child.poll() is None, 'USB_RESPONDER_FAILED', 'The MTP responder exited unexpectedly')
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                connection.settimeout(2)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    require(uid == os.geteuid(), 'ACCESS_DENIED', 'USB IPC requires the recovery administrator')
                    command = receive(connection, 4096)
                    require(command.get('boot_id') == context.boot_id, 'BOOT_CHANGED', 'The recovery boot changed')
                    if command.get('operation') == 'stop':
                        require(command.get('session_token') == record['session_token'], 'SESSION_CHANGED', 'The USB export session changed')
                        stop_connection = connection
                        running = False
                        continue
                    require(command.get('operation') == 'status', 'INVALID_REQUEST', 'Unsupported USB operation')
                    response = snapshot(context, record)
                except (OSError, ValueError, fs.FilesystemError) as exc:
                    response = error_value(context, exc)
                try:
                    connection.sendall(json.dumps(response).encode() + b'\n')
                except OSError:
                    pass
                connection.close()
            record['state'] = 'stopping'
            context.save(record)
        except (OSError, ValueError, fs.FilesystemError, subprocess.SubprocessError) as exc:
            error = exc
            stop_connection = None
        finally:
            # Stop USB traffic before terminating the responder; keep the lease
            # until it is reaped, the exported filesystem is flushed and NCM is restored.
            try:
                if gadget:
                    gadget.disconnect()
                child_stop(child)
                if 'thread' in locals():
                    thread.join(timeout=1)
                if 'pin' in locals() and context.mode == 'device':
                    system_call(libc.syncfs, pin.fd)
                if gadget:
                    gadget.restore()
                record['clean'] = True
            except (OSError, fs.FilesystemError, subprocess.SubprocessError) as exc:
                error = error or exc
                # Even on cleanup errors, kill/reap first. The inherited lease
                # remains held by an unkillable child until the kernel ends it.
                try:
                    child_stop(child)
                except (OSError, fs.FilesystemError, subprocess.SubprocessError):
                    pass
            record['state'] = 'failed' if error else 'stopped'
            record['reason'] = error_value(context, error)['error']['code'] if error else None
            record['error'] = error_value(context, error)['error'] if error else None
            if record['clean']:
                record['exports'] = []
            context.save(record)
            if session is not None and retained:
                log = session / 'responder.log'
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, retained)
                finally:
                    os.close(fd)
            if 'stop_connection' in locals() and stop_connection:
                try:
                    value = error_value(context, error) if error else snapshot(context, record)
                    stop_connection.sendall(json.dumps(value).encode() + b'\n')
                except OSError:
                    pass
                stop_connection.close()
            if server:
                server.close()
            if socket_identity is not None:
                current = context.socket_path.lstat()
                if (current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino):
                    context.socket_path.unlink()
            if context.socket_dir != context.base:
                context.socket_dir.rmdir()
    return 2 if error else 0


def start(context, args):
    require(ENV_FD not in os.environ, 'INHERITED_SESSION', 'Start USB sharing outside an administrator SSH lease')
    previous = context.record()
    require(not previous or not process_live(previous), 'USB_EXPORT_ACTIVE', 'Stop the active USB export before starting another')
    require(not previous or previous.get('clean', False), 'SERVICE_STALE', 'An unclean USB session requires recovery restart')
    private_directory(context.base)
    token = os.urandom(32).hex()
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--mode', context.mode]
    if context.mode == 'fixture':
        command += ['--fixture', str(context.fs.root)]
    command += ['serve', '--volume', args.volume, '--expected-volume-token', args.expected_volume_token,
                '--expected-mount-token', args.expected_mount_token, '--session-token', token]
    if args.read_only:
        command += ['--read-only']
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                             env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C'})
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        record = context.record()
        if record and record.get('session_token') == token:
            if record['state'] == 'active':
                return request(context, record, 'status')
            if record['state'] == 'failed':
                fs.fail(record.get('reason') or 'USB_START_FAILED', 'USB export did not start; the selected volume was not shared')
        require(child.poll() is None, 'USB_START_FAILED', 'The USB supervisor exited before readiness')
        time.sleep(0.05)
    return snapshot(context, context.record(), reason='USB_START_PENDING')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fs.fail('INVALID_ARGUMENT', message)


def main(argv=None):
    operation, mode, context = 'unknown', 'device', None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--mode', choices=('device', 'fixture'), default='device')
        parser.add_argument('--fixture', type=Path)
        sub = parser.add_subparsers(dest='operation', required=True, parser_class=Parser)
        sub.add_parser('status')
        for name in ('start', 'serve'):
            p = sub.add_parser(name)
            p.add_argument('--volume', required=True)
            p.add_argument('--expected-volume-token', required=True)
            p.add_argument('--expected-mount-token', required=True)
            p.add_argument('--read-only', action='store_true')
            if name == 'serve':
                p.add_argument('--session-token', required=True)
        p = sub.add_parser('stop')
        p.add_argument('--expected-session-token', required=True)
        args = parser.parse_args(argv)
        operation, mode = args.operation, args.mode
        context = Context(args)
        if operation == 'serve':
            return serve(context, args)
        if operation == 'status':
            result = status(context)
        elif operation == 'start':
            result = start(context, args)
        else:
            fs.require_hash(args.expected_session_token, 'expected-session-token')
            record = context.record()
            require(record and record.get('session_token') == args.expected_session_token,
                    'SESSION_CHANGED', 'The USB export session changed')
            result = request(context, record, 'stop', args.expected_session_token)
            if result.get('ok') and result.get('state') == 'stopped':
                deadline = time.monotonic() + 1
                while process_live(record) and time.monotonic() < deadline:
                    time.sleep(0.02)
                require(not process_live(record), 'USB_STOP_PENDING', 'The USB supervisor is still releasing its lease')
        result['operation'] = operation
    except (OSError, ValueError, TypeError, KeyError, fs.FilesystemError, subprocess.SubprocessError) as error:
        result = (error_value(context, error) if context else
                  {'ok': False, 'mode': mode, 'error': {'code': getattr(error, 'code', 'IO_ERROR'), 'message': str(error)}})
        result['operation'] = operation
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')), flush=True)
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
