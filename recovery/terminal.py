#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit local root PTY with a lifetime recovery lease and private RAM IPC.

The administrator shell uses a pinned static BusyBox with full UTF-8 editing.
Only metadata is persisted. Terminal input/output remains in bounded RAM buffers.
The supervisor is a child subreaper: Close must reap its complete descendant tree
before releasing the storage lease. An incomplete cleanup remains fail-closed.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import time

from operation_lock import DEVICE_LOCK, ENV_FD, operation_lock

SCHEMA = 'a22x.terminal.service.v1'
RING_BYTES = 256 * 1024
MAX_READ = 32768
MAX_WRITE = 4096
MAX_PENDING = 16384
MAX_JSON = 65536
START_SECONDS = 5.0
STOP_SECONDS = 5.0
COMMAND_SAMPLE_SECONDS = 0.2
COMMAND_HOLD_SECONDS = 0.6
MAX_PROCESSES = 4096
DEVICE_SHELL = Path('/bin/a22x-terminal-shell')
DEVICE_SHELL_SHA256 = 'feb0c40dc4f6f292830d309b8112601ba625bedbac2b29bcdb31c6ceae7a4a9f'
SHELL_MAX_BYTES = 4 * 1024 * 1024
libc = ctypes.CDLL(None, use_errno=True)


class TerminalError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise TerminalError(code, message)


def token(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value),
            'INVALID_TOKEN', 'Expected an exact terminal session token')
    return value


def dimensions(rows, cols):
    require(type(rows) is int and 2 <= rows <= 256 and type(cols) is int and 10 <= cols <= 512,
            'INVALID_SIZE', 'Terminal size must be 2..256 rows and 10..512 columns')
    return rows, cols


def path_without_links(path, *, missing=False):
    path = Path(os.path.abspath(path))
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            require(missing, 'UNSAFE_PATH', 'Terminal path is absent')
        else:
            require(not stat.S_ISLNK(info.st_mode), 'UNSAFE_PATH', 'Terminal state path contains a symlink')
    return path


def private_directory(path):
    path = path_without_links(path, missing=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700, 'UNSAFE_STATE', 'Terminal state directory must be owned and private')
    return path


def open_pinned_shell(path, expected, *, machine=None):
    """Return the verified ELF descriptor; exec uses this inode, never reopens it."""
    token(expected)
    path = path_without_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and before.st_mode & 0o111 and 64 <= before.st_size <= SHELL_MAX_BYTES,
                'UNSAFE_SHELL', 'The terminal shell must be one protected owned executable')
        header = os.pread(fd, 64, 0)
        require(header[:7] == b'\x7fELF\x02\x01\x01'
                and (machine is None or int.from_bytes(header[18:20], 'little') == machine),
                'UNSAFE_SHELL', 'The terminal shell ELF architecture differs')
        digest = hashlib.sha256()
        offset = 0
        while offset < before.st_size:
            data = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
            require(data, 'SHELL_CHANGED', 'The terminal shell changed while read')
            digest.update(data)
            offset += len(data)
        stamp = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
                           st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        require(digest.hexdigest() == expected
                and stamp(before) == stamp(os.fstat(fd)) == stamp(path.lstat()),
                'SHELL_CHANGED', 'The terminal shell bytes or identity differ')
        return fd
    except BaseException:
        os.close(fd)
        raise


def process_info(pid):
    try:
        value = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'state': value[0], 'ppid': int(value[1]), 'start': value[19]}
    except (OSError, ValueError, IndexError):
        return None


def process_live(record):
    pid = record.get('pid')
    current = process_info(pid) if type(pid) is int and pid > 1 else None
    return bool(current and current['state'] != 'Z' and current['start'] == record.get('process_start'))


def inherited_start_guard():
    require(ENV_FD not in os.environ, 'INHERITED_SESSION',
            'Start a persistent terminal outside an administrator SSH lease')


def environment(mode, *, root=None, lease=None):
    value = {'PATH': '/bin:/sbin:/usr/bin:/usr/sbin', 'TERM': 'vt100',
             'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
             'HOME': '/root' if mode == 'device' else str(root),
             'HISTFILE': '/dev/null', 'HISTSIZE': '0', 'PS1': 'a22x# '}
    if mode == 'device':
        value.update(PYTHONHOME='/usr', LD_LIBRARY_PATH='/usr/lib')
    if lease is not None:
        value[ENV_FD] = str(lease)
    return value


class Ring:
    def __init__(self, capacity=RING_BYTES):
        self.capacity, self.data, self.end = capacity, bytearray(), 0

    @property
    def start(self):
        return self.end - len(self.data)

    def append(self, raw):
        self.end += len(raw)
        if len(raw) >= self.capacity:
            self.data[:] = raw[-self.capacity:]
        else:
            self.data.extend(raw)
            if len(self.data) > self.capacity:
                del self.data[:-self.capacity]

    def read(self, after, maximum):
        require(type(after) is int and 0 <= after <= self.end, 'INVALID_CURSOR',
                'Terminal output cursor is negative or ahead of the stream')
        require(type(maximum) is int and 1 <= maximum <= MAX_READ, 'INVALID_READ_SIZE',
                'Terminal output chunk must be 1..32768 bytes')
        first = max(after, self.start)
        raw = bytes(self.data[first - self.start:first - self.start + maximum])
        return {'data_base64': base64.b64encode(raw).decode('ascii'), 'start_seq': self.start,
                'next_seq': first + len(raw), 'end_seq': self.end, 'lost_bytes': max(0, self.start - after)}


class Context:
    def __init__(self, args):
        self.mode = args.mode
        fixture_shell = getattr(args, 'fixture_shell', None)
        fixture_sha = getattr(args, 'fixture_shell_sha256', None)
        require((fixture_shell is None) == (fixture_sha is None)
                and (self.mode == 'fixture' or fixture_shell is None),
                'INVALID_ARGUMENT', 'A test shell and its SHA are allowed only together in fixture mode')
        self.shell_path = Path(fixture_shell) if fixture_shell is not None else (
            DEVICE_SHELL if self.mode == 'device' else Path('/bin/sh'))
        self.shell_sha256 = token(fixture_sha) if fixture_sha is not None else (
            DEVICE_SHELL_SHA256 if self.mode == 'device' else None)
        require((args.mode == 'fixture') == bool(args.fixture), 'INVALID_ARGUMENT',
                'Fixture mode requires an explicit owned fixture directory')
        if self.mode == 'fixture':
            self.root = path_without_links(args.fixture)
            info = self.root.stat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022,
                    'UNSAFE_FIXTURE', 'Fixture directory must be owned and not publicly writable')
            self.base = self.root / '.terminal'
            identity = hashlib.sha256(os.fsencode(self.root)).hexdigest()
            self.boot_id = identity
            self.socket_dir = Path('/tmp') / ('a22x-terminal-' + identity[:24])
            self.lock_path = self.root / '.operation.lock'
        else:
            require(os.geteuid() == 0 and 'a22x.recovery=1' in Path('/proc/cmdline').read_text().split(),
                    'RECOVERY_REQUIRED', 'The local root terminal is only available in A22x recovery')
            self.root = Path('/')
            self.base = Path('/run/a22x-recovery/terminal')
            self.socket_dir = self.base
            self.lock_path = Path(DEVICE_LOCK)
            self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', self.boot_id),
                    'BOOT_UNAVAILABLE', 'Recovery boot identity is unavailable')
        self.record_path = self.base / 'state.json'
        self.socket_path = self.socket_dir / 'control.sock'
        self.singleton_path = self.base / 'supervisor.lock'

    def open_shell(self):
        if self.shell_sha256 is not None:
            return open_pinned_shell(self.shell_path, self.shell_sha256,
                                     machine=183 if self.mode == 'device' else None), ['ash', '-i']
        # The default host fixture keeps its existing system shell. Production
        # has no fallback to the recovery-wide BusyBox or an unpinned shell.
        fd = os.open('/bin/sh', os.O_RDONLY | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            os.close(fd)
            raise TerminalError('TERMINAL_UNAVAILABLE', 'The fixture system shell is unavailable')
        return fd, ['/bin/sh', '-i']

    def ready(self):
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'),
                'TERMINAL_UNAVAILABLE', 'This runtime lacks safe process identity operations')
        require(os.execve in os.supports_fd, 'TERMINAL_UNAVAILABLE',
                'This runtime cannot execute the verified shell descriptor')
        shell, _ = self.open_shell()
        os.close(shell)
        fd = os.pidfd_open(os.getpid(), 0)
        os.close(fd)
        try:
            with Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').open('rb') as stream:
                children = stream.read(65537)
            require(len(children) <= 65536 and len(children.split()) <= MAX_PROCESSES
                    and all(value.isdigit() and int(value) > 1 for value in children.split()),
                    'TERMINAL_UNAVAILABLE', 'Kernel descendant observation is unavailable')
        except OSError as error:
            raise TerminalError('TERMINAL_UNAVAILABLE', 'Kernel descendant observation is unavailable') from error

    def record(self):
        if not self.record_path.exists():
            return None
        path_without_links(self.record_path)
        fd = os.open(self.record_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192,
                    'UNSAFE_STATE', 'Terminal receipt must be a private owned regular file')
            value = json.loads(os.pread(fd, 8193, 0))
        finally:
            os.close(fd)
        require(isinstance(value, dict) and value.get('schema') == SCHEMA and
                value.get('mode') == self.mode and value.get('boot_id') == self.boot_id,
                'SERVICE_STALE', 'Terminal receipt belongs to another recovery boot')
        return value

    def save(self, value):
        # Only explicit metadata fields are allowed into the receipt.
        safe = {key: value.get(key) for key in ('schema', 'mode', 'boot_id', 'pid', 'process_start',
                'session_token', 'state', 'rows', 'cols', 'shell_pid', 'shell_exit_code', 'clean', 'reason')}
        temporary = self.base / ('.state-' + os.urandom(12).hex())
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w', closefd=False) as stream:
                json.dump(safe, stream, separators=(',', ':'))
                stream.write('\n')
                stream.flush()
            os.fsync(fd)
            os.replace(temporary, self.record_path)
        finally:
            os.close(fd)
            temporary.unlink(missing_ok=True)


def snapshot(context, record=None, *, available=True, reason=None):
    return {'ok': True, 'mode': context.mode, 'boot_id': context.boot_id, 'available': available,
            'state': record['state'] if record else 'stopped',
            'session_token': record.get('session_token') if record else None,
            'rows': record.get('rows') if record else None, 'cols': record.get('cols') if record else None,
            'shell_exit_code': record.get('shell_exit_code') if record else None,
            'command_running': False,
            'reason': reason or (record.get('reason') if record else None),
            'term': 'vt100', 'ring_bytes': RING_BYTES, 'max_write_bytes': MAX_WRITE,
            'shell_path': str(getattr(context, 'shell_path', '/bin/sh')),
            'shell_sha256': getattr(context, 'shell_sha256', None),
            'storage_lease_held': bool(record and not record.get('clean'))}


def error_value(context, error):
    code = 'OPERATION_BUSY' if isinstance(error, BlockingIOError) else getattr(error, 'code', 'IO_ERROR')
    return {'ok': False, 'mode': context.mode, 'boot_id': context.boot_id,
            'error': {'code': code, 'message': str(error) if isinstance(error, TerminalError)
                      else 'Terminal system operation failed'}}


def receive(connection):
    raw = bytearray()
    while b'\n' not in raw:
        block = connection.recv(min(4096, MAX_JSON + 1 - len(raw)))
        if not block:
            break
        raw.extend(block)
        require(len(raw) <= MAX_JSON, 'INVALID_REQUEST', 'Terminal IPC request exceeded its bound')
    require(raw.endswith(b'\n') and raw.count(b'\n') == 1, 'INVALID_REQUEST', 'Expected one terminal JSON request')
    result = json.loads(raw)
    require(isinstance(result, dict), 'INVALID_REQUEST', 'Expected a terminal JSON object')
    return result


def send(connection, value):
    try:
        connection.sendall(json.dumps(value, separators=(',', ':')).encode() + b'\n')
    except OSError:
        pass
    finally:
        connection.close()


def request(context, record, operation, **fields):
    require(process_live(record), 'SERVICE_STALE', 'The terminal supervisor is no longer running')
    path_without_links(context.socket_path)
    info = context.socket_path.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
            'UNSAFE_SOCKET', 'The terminal control socket must be owned and private')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(STOP_SECONDS + 2)
        client.connect(str(context.socket_path))
        pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(uid == os.geteuid() and pid == record['pid'] and process_live(record),
                'SERVICE_STALE', 'Terminal control peer identity changed')
        value = {'operation': operation, 'boot_id': context.boot_id, **fields}
        client.sendall(json.dumps(value, separators=(',', ':')).encode() + b'\n')
        client.shutdown(socket.SHUT_WR)
        result = receive(client)
    require(result.get('mode') == context.mode and result.get('boot_id') == context.boot_id,
            'INVALID_RESPONSE', 'Terminal response belongs to another recovery boot')
    return result


def status(context):
    record = context.record()
    if record and process_live(record):
        return request(context, record, 'status')
    if record and not record.get('clean'):
        return snapshot(context, record, available=False, reason='SERVICE_STALE')
    try:
        context.ready()
        return snapshot(context)
    except (OSError, TerminalError) as error:
        return snapshot(context, available=False, reason=getattr(error, 'code', 'TERMINAL_UNAVAILABLE'))


def child_ids(pid):
    """Only traverse descendants of this supervisor, never scan or signal PID 1."""
    try:
        tasks = list(Path(f'/proc/{pid}/task').iterdir())
    except FileNotFoundError:
        return set()
    require(len(tasks) <= MAX_PROCESSES, 'DESCENDANTS_REMAIN', 'Terminal process tree exceeded its inspection bound')
    result = set()
    for task in tasks:
        try:
            raw = (task / 'children').read_bytes()
        except FileNotFoundError:
            continue
        require(len(raw) <= 65536, 'DESCENDANTS_REMAIN', 'Terminal children listing exceeded its bound')
        result.update(int(value) for value in raw.split())
    return result


def belongs_to(pid, ancestor):
    seen = set()
    while pid > 1 and pid not in seen and len(seen) < MAX_PROCESSES:
        if pid == ancestor:
            return True
        seen.add(pid)
        info = process_info(pid)
        if not info:
            return False
        pid = info['ppid']
    return False


def signal_descendants(ancestor, signum):
    seen, pending = set(), [ancestor]
    while pending:
        parent = pending.pop()
        for pid in child_ids(parent):
            if pid in seen:
                continue
            require(pid > 1 and pid != ancestor, 'DESCENDANTS_REMAIN', 'Invalid terminal descendant identity')
            seen.add(pid)
            require(len(seen) <= MAX_PROCESSES, 'DESCENDANTS_REMAIN', 'Terminal process count exceeded its bound')
            try:
                fd = os.pidfd_open(pid, 0)
            except ProcessLookupError:
                continue
            try:
                if belongs_to(pid, ancestor):
                    pending.append(pid)
                    try:
                        signal.pidfd_send_signal(fd, signum)
                    except ProcessLookupError:
                        pass
            finally:
                os.close(fd)


class Session:
    def __init__(self, context, record, lease):
        self.context, self.record, self.lease = context, record, lease
        self.master = None
        self.ring, self.pending = Ring(), bytearray()
        self.pty_eof = False
        self.exit_cleanup_done = False
        self.shutdown = False
        self.command_sampled = self.command_until = 0.0

    def start(self):
        if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), 'Unable to supervise terminal descendants')
        supervisor = os.getpid()
        shell, argv = self.context.open_shell()
        try:
            pid, master = pty.fork()
        except BaseException:
            os.close(shell)
            raise
        if pid == 0:
            try:
                for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
                    signal.signal(signum, signal.SIG_DFL)
                if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != supervisor:
                    os._exit(126)
                os.set_inheritable(self.lease, True)
                os.umask(0o077)
                os.chdir(self.context.root)
                fcntl.ioctl(0, termios.TIOCSWINSZ,
                            struct.pack('HHHH', self.record['rows'], self.record['cols'], 0, 0))
                attrs = termios.tcgetattr(0)
                attrs[0] |= 0x4000  # Linux IUTF8: erase a complete UTF-8 character in canonical mode.
                termios.tcsetattr(0, termios.TCSANOW, attrs)
                os.execve(shell, argv,
                          environment(self.context.mode, root=self.context.root, lease=self.lease))
            except BaseException:
                os._exit(126)
        os.close(shell)
        self.master = master
        os.set_blocking(master, False)
        self.record.update(shell_pid=pid, state='active')
        self.context.save(self.record)

    def drain(self):
        if self.master is None or self.pty_eof:
            return
        # One bounded batch keeps a flooding command from starving Close/input.
        for _ in range(16):
            try:
                raw = os.read(self.master, 16384)
            except BlockingIOError:
                return
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                raw = b''
            if not raw:
                self.pty_eof = True
                return
            self.ring.append(raw)

    def reap(self):
        while True:
            try:
                pid, value = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return True  # ECHILD: no remaining direct/adopted children.
            if pid == 0:
                return False
            if pid == self.record.get('shell_pid'):
                code = os.waitstatus_to_exitcode(value)
                self.record['shell_exit_code'] = code if code >= 0 else 128 - code

    def cleanup_children(self):
        self.pending.clear()
        deadline, kill_at = time.monotonic() + STOP_SECONDS, time.monotonic() + 0.5
        try:
            # An interactive shell ignores SIGTERM but ends on a hangup, as on a real terminal.
            signal_descendants(os.getpid(), signal.SIGHUP)
            while True:
                try:
                    self.drain()
                except OSError:
                    # A broken PTY must not prevent reaping its process tree.
                    self.pty_eof = True
                if self.reap():
                    try:
                        self.drain()
                    except OSError:
                        self.pty_eof = True
                    return True
                signum = signal.SIGTERM if time.monotonic() < kill_at else signal.SIGKILL
                signal_descendants(os.getpid(), signum)
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.025)
        except (OSError, TerminalError, ValueError):
            return False

    def close(self):
        self.record.update(state='stopping', reason=None)
        self.context.save(self.record)
        if not self.cleanup_children():
            self.record.update(state='failed', reason='DESCENDANTS_REMAIN', clean=False)
            self.context.save(self.record)
            return False
        self.record.update(state='stopped', reason=None, clean=True)
        self.context.save(self.record)
        return True

    def command_alive(self):
        """Anything besides the shell waiting at its prompt, including orphans this subreaper adopted.

        A finished background job stays a zombie until the shell's next prompt, so it does not count.
        Uncertainty counts as running: a session is only ever closed for being idle when that is known.
        """
        shell = self.record.get('shell_pid')
        if self.record['state'] != 'active' or shell is None:
            return False
        try:
            info = process_info(shell)
            if info and info['state'] in ('R', 'D'):
                return True
            family = (child_ids(os.getpid()) | child_ids(shell)) - {shell}
            return any((process_info(pid) or {'state': 'Z'})['state'] != 'Z' for pid in family)
        except (OSError, ValueError, TerminalError):
            return True

    def sample_command(self):
        # The hold covers the moment between two commands of a running script.
        now = time.monotonic()
        if now >= self.command_sampled:
            self.command_sampled = now + COMMAND_SAMPLE_SECONDS
            if self.command_alive():
                self.command_until = now + COMMAND_HOLD_SECONDS

    def tick(self):
        self.drain()
        if self.pending and not self.pty_eof:
            try:
                count = os.write(self.master, self.pending)
                del self.pending[:count]
            except BlockingIOError:
                pass
        self.reap()
        if self.record['shell_exit_code'] is not None and not self.exit_cleanup_done:
            self.exit_cleanup_done = True
            if self.cleanup_children():
                self.record.update(state='exited', reason=None)
            else:
                self.record.update(state='failed', reason='DESCENDANTS_REMAIN')
            self.context.save(self.record)
        self.sample_command()

    def result(self):
        value = snapshot(self.context, self.record)
        value.update(start_seq=self.ring.start, end_seq=self.ring.end, pending_input_bytes=len(self.pending),
                     command_running=bool(self.pending) or time.monotonic() < self.command_until)
        return value

    def command(self, value):
        require(value.get('boot_id') == self.context.boot_id, 'BOOT_CHANGED', 'Recovery boot identity changed')
        operation = value.get('operation')
        if operation == 'status':
            return self.result()
        require(token(value.get('session_token')) == self.record['session_token'],
                'SESSION_CHANGED', 'The terminal session changed')
        if operation == 'read':
            return {**self.result(), **self.ring.read(value.get('after_seq'), value.get('max_bytes'))}
        if operation == 'write':
            require(self.record['state'] == 'active' and self.record['shell_exit_code'] is None,
                    'SHELL_EXITED', 'The terminal shell has exited; close this session')
            encoded = value.get('data_base64')
            require(isinstance(encoded, str) and len(encoded) <= 5464, 'INVALID_INPUT', 'Terminal input is too large')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, UnicodeError):
                raise TerminalError('INVALID_INPUT', 'Terminal input is not valid base64')
            require(0 < len(raw) <= MAX_WRITE, 'INVALID_INPUT', 'Terminal input must contain 1..4096 bytes')
            require(len(self.pending) + len(raw) <= MAX_PENDING, 'INPUT_BACKPRESSURE', 'Terminal input queue is full')
            self.pending.extend(raw)
            # This acknowledges bounded FIFO acceptance, not shell execution.
            return {**self.result(), 'bytes_written': len(raw), 'accepted_bytes': len(raw)}
        if operation == 'resize':
            rows, cols = dimensions(value.get('rows'), value.get('cols'))
            fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            self.record.update(rows=rows, cols=cols)
            self.context.save(self.record)
            return self.result()
        require(operation == 'stop', 'INVALID_REQUEST', 'Unknown terminal operation')
        if not self.close():
            raise TerminalError('DESCENDANTS_REMAIN', 'Terminal processes remain; the storage lease is still held')
        return self.result()


def control_step(context, server, session, timeout=0.025):
    """Return a successful Stop connection without replying before lease close."""
    readable, _, _ = select.select([server], [], [], timeout)
    if not readable:
        return None, None
    connection, _ = server.accept()
    connection.settimeout(0.5)
    try:
        _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(uid == os.geteuid(), 'ACCESS_DENIED', 'Terminal control requires its owner')
        value = receive(connection)
        response = session.command(value)
        if value.get('operation') == 'stop' and session.record['clean']:
            return connection, response
    except (OSError, ValueError, TypeError, KeyError, TerminalError) as error:
        response = error_value(context, error)
    send(connection, response)
    return None, None


def serve(context, args):
    inherited_start_guard()
    context.ready()
    private_directory(context.base)
    private_directory(context.socket_dir)
    record = {'schema': SCHEMA, 'mode': context.mode, 'boot_id': context.boot_id,
              'pid': os.getpid(), 'process_start': process_info(os.getpid())['start'],
              'session_token': token(args.session_token), 'state': 'starting',
              'rows': args.rows, 'cols': args.cols, 'shell_pid': None, 'shell_exit_code': None,
              'clean': False, 'reason': None}
    stop_connection = stop_result = None
    server = session = None
    socket_identity = None
    with ExitStack() as stack:
        singleton = os.open(context.singleton_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        stack.callback(os.close, singleton)
        info = os.fstat(singleton)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1,
                'UNSAFE_STATE', 'Terminal singleton lock changed')
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = context.record()
        require(not previous or (previous.get('clean') and not process_live(previous)),
                'SERVICE_STALE', 'An existing or unclean terminal session requires review')
        try:
            lease = stack.enter_context(operation_lock(context.lock_path, context.mode))
            context.save(record)
            if context.socket_path.exists():
                path_without_links(context.socket_path)
                old = context.socket_path.lstat()
                require(stat.S_ISSOCK(old.st_mode) and old.st_uid == os.geteuid(), 'UNSAFE_SOCKET', 'Unexpected terminal IPC entry')
                context.socket_path.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old_mask = os.umask(0o177)
            try:
                server.bind(str(context.socket_path))
            finally:
                os.umask(old_mask)
            info = context.socket_path.lstat()
            socket_identity = (info.st_dev, info.st_ino)
            server.listen(4)
            server.setblocking(False)
            session = Session(context, record, lease)
            def stop_signal(*unused):
                session.shutdown = True
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                signal.signal(signum, stop_signal)
            session.start()
            while True:
                try:
                    session.tick()
                    if session.shutdown:
                        session.shutdown = False
                        if session.close():
                            break
                    stop_connection, stop_result = control_step(context, server, session)
                    if stop_connection:
                        break
                except (OSError, ValueError, TerminalError):
                    # Keep the lease and control endpoint if cleanup cannot prove ECHILD.
                    if session.close():
                        break
                    record.update(state='failed', reason='DESCENDANTS_REMAIN')
                    context.save(record)
                    time.sleep(0.05)
        except (OSError, ValueError, TerminalError) as error:
            record.update(state='failed', clean=session is None,
                          reason='OPERATION_BUSY' if isinstance(error, BlockingIOError) else getattr(error, 'code', 'IO_ERROR'))
            try:
                context.save(record)
            except OSError:
                pass
        finally:
            if session and not record['clean']:
                # This guard remains INSIDE ExitStack. Even a setup/publication
                # error after fork must not unwind the lifetime storage lease
                # while a direct or adopted descendant survives. Keep root IPC
                # responsive so Status and another Close can inspect/retry it.
                while not session.cleanup_children():
                    record.update(state='failed', reason='DESCENDANTS_REMAIN', clean=False)
                    try:
                        context.save(record)
                    except OSError:
                        pass
                    if server:
                        try:
                            stop_connection, stop_result = control_step(context, server, session, timeout=0.1)
                            if stop_connection:
                                break
                        except (OSError, ValueError, TerminalError):
                            pass
                    else:
                        time.sleep(0.1)
                record.update(clean=True)
                try:
                    context.save(record)
                except OSError:
                    pass
            if session and session.master is not None and record['clean']:
                os.close(session.master)
            if server:
                server.close()
            if socket_identity is not None:
                info = context.socket_path.lstat()
                require((info.st_dev, info.st_ino) == socket_identity, 'UNSAFE_SOCKET', 'Terminal IPC path changed')
                context.socket_path.unlink()
    # A successful Close reply is sent only after the lease and singleton close.
    if stop_connection:
        stop_result['storage_lease_held'] = False
        send(stop_connection, stop_result)
    return 0 if record['clean'] else 2


def start(context, args):
    inherited_start_guard()
    dimensions(args.rows, args.cols)
    context.ready()
    previous = context.record()
    require(not previous or not process_live(previous), 'TERMINAL_ACTIVE', 'Close the existing terminal session first')
    require(not previous or previous.get('clean'), 'SERVICE_STALE', 'An unclean terminal session requires review')
    session_token = os.urandom(32).hex()
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--mode', context.mode]
    if context.mode == 'fixture':
        command += ['--fixture', str(context.root)]
        if context.shell_sha256 is not None:
            command += ['--fixture-shell', str(context.shell_path),
                        '--fixture-shell-sha256', context.shell_sha256]
    command += ['serve', '--session-token', session_token, '--rows', str(args.rows), '--cols', str(args.cols)]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True, env=environment(context.mode, root=context.root))
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline:
        record = context.record()
        if record and record.get('session_token') == session_token:
            if record['state'] in ('active', 'exited') and process_live(record):
                return request(context, record, 'status')
            if record['state'] == 'failed':
                raise TerminalError(record.get('reason') or 'START_FAILED', 'The terminal session could not start')
        if child.poll() is not None:
            raise TerminalError('START_FAILED', 'The terminal supervisor exited before becoming ready')
        time.sleep(0.025)
    if record and record.get('session_token') == session_token and process_live(record):
        return snapshot(context, record, reason='START_PENDING')
    raise TerminalError('START_UNCERTAIN', 'Terminal startup is uncertain; inspect status before another Start')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('device', 'fixture'), default='device')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--fixture-shell', type=Path)
    parser.add_argument('--fixture-shell-sha256')
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('status')
    for action in ('start', 'serve'):
        item = sub.add_parser(action)
        item.add_argument('--rows', type=int, default=24)
        item.add_argument('--cols', type=int, default=80)
        if action == 'serve':
            item.add_argument('--session-token', required=True)
    for action in ('read', 'write', 'resize', 'stop'):
        item = sub.add_parser(action)
        item.add_argument('--expected-session-token', required=True)
        if action == 'read':
            item.add_argument('--after-seq', type=int, required=True)
            item.add_argument('--max-bytes', type=int, default=16384)
        elif action == 'write':
            item.add_argument('--data-base64', required=True)
        elif action == 'resize':
            item.add_argument('--rows', type=int, required=True)
            item.add_argument('--cols', type=int, required=True)
    args = parser.parse_args(argv)
    context = None
    try:
        if args.operation in ('start', 'serve'):
            inherited_start_guard()
            dimensions(args.rows, args.cols)
        context = Context(args)
        if args.operation == 'serve':
            return serve(context, args)
        if args.operation == 'start':
            result = start(context, args)
        elif args.operation == 'status':
            result = status(context)
        else:
            expected = token(args.expected_session_token)
            record = context.record()
            require(record and record.get('session_token') == expected, 'SESSION_CHANGED', 'The terminal session changed')
            fields = {'session_token': expected}
            for key in ('after_seq', 'max_bytes', 'data_base64', 'rows', 'cols'):
                if hasattr(args, key):
                    fields[key] = getattr(args, key)
            result = request(context, record, args.operation, **fields)
        result['operation'] = args.operation
    except (OSError, ValueError, TypeError, KeyError, TerminalError, subprocess.SubprocessError) as error:
        result = error_value(context, error) if context else {
            'ok': False, 'mode': args.mode, 'error': {'code': getattr(error, 'code', 'IO_ERROR'),
            'message': str(error) if isinstance(error, TerminalError) else 'Terminal admission failed'}}
        result['operation'] = args.operation
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')), flush=True)
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
