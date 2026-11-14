#!/usr/bin/env python3
"""A22x recovery status, bounded log rotation and explicit diagnostic export."""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import uuid

import filesystem as fs

RUN = Path('/run/a22x-recovery')
MAX_LOG = 256 * 1024
MAX_TOTAL = 4 * 1024 * 1024
KEEP_LOGS = 4


def read_bounded(path, limit=MAX_LOG):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            fs.fail('REGULAR_FILE_REQUIRED', 'Diagnostic input must be a regular file')
        if st.st_size > limit:
            os.lseek(fd, st.st_size - limit, os.SEEK_SET)
        return os.read(fd, limit), st.st_size > limit
    finally:
        os.close(fd)


def json_file(path, limit=65536):
    try:
        raw, truncated = read_bounded(path, limit)
        return None if truncated else json.loads(raw)
    except (OSError, ValueError, fs.FilesystemError):
        return None


def proc_start(pid):
    try:
        return Path('/proc', str(pid), 'stat').read_text().split(') ', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def process_running(state):
    if not isinstance(state, dict):
        return False
    pid, started = state.get('pid'), state.get('process_start')
    return bool(isinstance(pid, int) and not isinstance(pid, bool) and pid > 1
                and isinstance(started, str) and started and proc_start(pid) == started)


def status(context):
    if context.mode == 'fixture':
        extra = json_file(context.root / 'management-fixture.json') or {}
        ssh = extra.get('ssh', {'available': True, 'running': False,
                               'address': '172.16.42.1', 'port': 22,
                               'authorized_keys_count': 1,
                               'host_fingerprint': 'SHA256:fixture-not-a-host-key',
                               'host_key_ephemeral': True, 'reason': 'Fixture only'})
    else:
        ssh = json_file(RUN / 'ssh/status.json') or {}
        if not isinstance(ssh, dict):
            ssh = {}
        running = process_running(ssh)
        count = 0
        try:
            raw, truncated = read_bounded('/etc/a22x-recovery/authorized_keys', 65536)
            if not truncated:
                count = sum(line.startswith((b'ssh-', b'ecdsa-sha2-nistp256 ')) for line in raw.splitlines())
        except OSError:
            pass
        ssh = {'available': Path('/usr/sbin/sshd').is_file(),
               'running': running, 'address': '172.16.42.1', 'port': 22,
               'authorized_keys_count': count, 'host_key_ephemeral': True,
               'host_fingerprint': ssh.get('host_fingerprint'),
               'reason': ssh.get('reason') if not running else None}
        # Host private keys and authorized key contents never enter this result.
    return {'status': 'ready', 'ssh': ssh,
            'logs': {'export_available': True, 'reason': None,
                     'maximum_bytes': MAX_TOTAL, 'retained_ui_runs': KEEP_LOGS},
            'power': {'targets': ['normal', 'recovery', 'download', 'poweroff']},
            'linux_only': True, 'device_restore_qualified': False,
            'device_restore_scope': 'generic-raw',
            'linux_qualification_source': 'backend inventory capabilities.linux_qualification'}


MANIFEST_RESERVE = 256 * 1024


def _add_input(items, records, name, path, limit=MAX_LOG):
    try:
        data, truncated = read_bounded(path, limit)
    except (OSError, fs.FilesystemError):
        return
    # Room is kept for the manifest written last (2026-11-12 review: with long logs the whole export
    # failed with DIAGNOSTICS_TOO_LARGE, just when there was most to look at).
    if sum(map(len, items.values())) + len(data) > MAX_TOTAL - MANIFEST_RESERVE:
        return
    items[name] = data
    records[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                     'tail_only': truncated}


def archive_bytes(context):
    items, records = {}, {}
    run = context.root / 'management-logs' if context.mode == 'fixture' else RUN
    if context.mode == 'device':
        for name in ('build.json', 'kernel-capabilities.json', 'service-runtime.json',
                     'compact-runtime.json', 'linux-toolchain.json',
                     'linux-workflow-qualification.json', 'usb-files-runtime.json'):
            _add_input(items, records, 'build/' + name, Path('/etc/a22x-recovery') / name)
        _add_input(items, records, 'logs/usb-files-state.json', run / 'usb-files/state.json')
        for name in ('version', 'cmdline', 'self/mountinfo', 'filesystems'):
            # procfs reports zero length, so use a bounded open/read separately.
            try:
                with open('/proc/' + name, 'rb') as f:
                    data = f.read(MAX_LOG)
                label = 'proc/' + name.replace('/', '-')
                items[label] = data
                records[label] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'tail_only': False}
            except OSError:
                pass
        try:
            p = subprocess.run(['/bin/busybox', 'dmesg'], capture_output=True, timeout=5)
            data = p.stdout[-MAX_LOG:]
            items['logs/dmesg.txt'] = data
            records['logs/dmesg.txt'] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'tail_only': len(p.stdout) > MAX_LOG}
        except (OSError, subprocess.SubprocessError):
            pass
    names = ['boot.json', 'ui.log', 'ui.stdout', 'diagnostics.log', 'ssh-start.log',
             'power-volumes.json', 'power-storage.json', 'maintenance.log', 'display.log',
             'android-last.json', 'android-last-kmsg.txt']  # how Android's last start ended (android_log.py)
    names += [f'{n}.{i}' for n in ('ui.log', 'ui.stdout') for i in range(1, KEEP_LOGS + 1)]
    for name in names:
        _add_input(items, records, 'logs/' + name, run / name)
    _add_input(items, records, 'logs/ssh-server.log', run / 'ssh/server.log')
    report = {'schema': 'a22x.diagnostics.archive.v1',
              'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'mode': context.mode, 'status': status(context), 'files': records,
              'contents': 'Allowlisted recovery logs and system metadata; no key files or user file scan'}
    items['manifest.json'] = json.dumps(report, indent=2).encode()
    if sum(map(len, items.values())) > MAX_TOTAL:
        fs.fail('DIAGNOSTICS_TOO_LARGE', 'Diagnostic data exceeded its bounded size')
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, data in sorted(items.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o600, 0
            archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def export(context, volume, expected=None, confirmed=False):
    context.confirm(confirmed)
    with context.operation_lock(), fs.PinnedVolume(context, volume, expected, writing=True) as pin:
        data = archive_bytes(context)
        space = os.fstatvfs(pin.fd)
        if space.f_bavail * space.f_frsize < len(data) + 16 * 1024 * 1024:
            fs.fail('INSUFFICIENT_SPACE', 'Not enough free space for diagnostic export')
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        name = f'a22x-recovery-logs-{now}-{uuid.uuid4().hex[:8]}.tar.gz'
        temporary = '.a22x-log-export-' + uuid.uuid4().hex
        fd = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=pin.fd)
            pin.check_fd(fd)
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError('Diagnostic write made no progress')
                view = view[count:]
            os.fsync(fd)
            pin.recheck()
            named = os.stat(temporary, dir_fd=pin.fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                fs.fail('FILE_CHANGED', 'Diagnostic staging entry changed')
            fs.rename_noreplace(pin.fd, temporary, pin.fd, name)
            os.fsync(pin.fd)
        finally:
            if fd is not None:
                try:
                    named = os.stat(temporary, dir_fd=pin.fd, follow_symlinks=False)
                    if (named.st_dev, named.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                        os.unlink(temporary, dir_fd=pin.fd)
                except FileNotFoundError:
                    pass
                os.close(fd)
        return {'volume': volume, 'archive_path': pin.volume['mountpoint'].rstrip('/') + '/' + name,
                'path': '/' + name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def rotate(context):
    run = context.root / 'management-logs' if context.mode == 'fixture' else RUN
    run.mkdir(parents=True, exist_ok=True)
    rotated = []
    for name in ('ui.log', 'ui.stdout'):
        source = run / name
        if not source.exists():
            continue
        for i in range(KEEP_LOGS, 0, -1):
            origin = source if i == 1 else run / f'{name}.{i - 1}'
            target = run / f'{name}.{i}'
            try:
                data, _ = read_bounded(origin, 1024 * 1024)
            except FileNotFoundError:
                continue
            tmp = run / ('.rotate-' + uuid.uuid4().hex)
            with tmp.open('xb') as f:
                f.write(data)
            tmp.chmod(0o600)
            os.replace(tmp, target)
        source.unlink()
        rotated.append(name)
    return {'rotated': rotated, 'retained': KEEP_LOGS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--device', action='store_true')
    group.add_argument('--fixture', type=Path)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('status')
    sub.add_parser('rotate-logs')
    p = sub.add_parser('export')
    p.add_argument('--volume', required=True)
    p.add_argument('--expected-volume-token')
    p.add_argument('--confirm', action='store_true')
    args = parser.parse_args(argv)
    try:
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        if args.operation == 'status':
            result = status(context)
        elif args.operation == 'rotate-logs':
            result = rotate(context)
        else:
            result = export(context, args.volume, args.expected_volume_token, args.confirm)
        print(json.dumps({'ok': True, 'operation': args.operation, 'mode': context.mode, **result}, separators=(',', ':')))
        return 0
    except fs.FilesystemError as exc:
        error = {'code': exc.code, 'message': exc.message, 'details': exc.details}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        error = {'code': 'MANAGEMENT_ERROR', 'message': str(exc), 'details': {}}
    print(json.dumps({'ok': False, 'operation': args.operation, 'error': error}, separators=(',', ':')))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
