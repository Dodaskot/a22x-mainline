#!/usr/bin/env python3
"""Physical acceptance: read RECOVERY only; create backup/log archives on ext4 SD.

Run explicitly after booting the dedicated recovery. No restore, partition
write, filesystem format or userdata file operation is implemented here.
The expected SHA-256 covers the entire 40 MiB RECOVERY partition, including tail.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tarfile
import time
import uuid

RECOVERY_BYTES = 40 * 1024 * 1024
REPOSITORY = Path('/mnt/backup/A22xBackups')
RUN = Path('/run/a22x-recovery')
LOCK = '/run/lock/a22x-mainline-recovery.lock'
ENV_FD = 'A22X_RECOVERY_LOCK_FD'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--source-root', type=Path, default=Path('/opt/a22x-recovery'))
    result.add_argument('--device-config', type=Path, default=Path('/etc/a22x-recovery/device.json'))
    result.add_argument('--record-dir', type=Path)
    result.add_argument('--expected-recovery-sha256', required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not re.fullmatch(r'[0-9a-f]{64}', args.expected_recovery_sha256):
        parser().error('--expected-recovery-sha256 must be 64 lowercase hexadecimal characters')
    source_root = args.source_root.resolve(strict=True)
    # Backend storage.py and the top-level mount helper have the same basename.
    # Import the backend first; run the mount helper by its explicit pathname.
    sys.path[:0] = [str(source_root / 'backend'), str(source_root)]
    import engine
    from common import CHUNK, no_symlinks, open_regular_fd, stamp, strict_object, write_json_new
    from storage import DeviceContext
    import filesystem as fs
    import management
    from operation_lock import operation_lock

    unique = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    record = args.record_dir or RUN / ('v03-backup-check-' + unique)
    record = Path(os.path.abspath(record))
    name = 'recovery-v03-' + unique
    receipt = {'schema': 'a22x.backup.live.v03', 'ok': False, 'started_utc': unique.split('-')[0],
               'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               'expected_recovery_sha256': args.expected_recovery_sha256,
               'record_dir': str(record), 'backup_name': name, 'profile': 'recovery',
               'raw_partition_writes': False, 'internal_partition_writes': False,
               'userdata_file_operations': False,
               'restore_attempted': False, 'auto_reboot': False, 'steps': []}
    record_created = False
    mount_attempted = False
    initial_volumes = None
    initial_recovery = None
    started = time.monotonic()

    def remember(step, value):
        receipt['steps'].append({'step': step, 'result': value})
        if record_created:
            write_json_new(record / (f'{len(receipt["steps"]):02d}-' + step + '.json'), value)
        return value

    def volumes(context):
        values = context.volumes()
        selected = {key: next((v for v in values if v['id'] == key), None)
                    for key in ('userdata', 'sdcard-p1')}
        require(all(selected.values()), 'Expected userdata and existing SD partition were not discovered')
        return selected

    def flag_summary(selected):
        return {name: {key: item.get(key) for key in
                       ('id', 'mounted', 'read_only', 'block_read_only', 'read_only_guarded',
                        'mount_owner', 'mountpoint', 'volume_token', 'device', 'fs_type')}
                for name, item in selected.items()}

    def recovery_hash(context):
        part = context.partition('recovery')
        require(part['size_bytes'] == RECOVERY_BYTES and part['partition_number'] == 42,
                'RECOVERY must be admitted GPT partition 42 with exactly 40 MiB')
        context.graph.require_unmounted(part['device'], context.mounts)
        context.graph.require_no_holders(part['device'])
        digest = hashlib.sha256()
        done = 0
        with context.open_partition(part) as source:
            block_ro = bool(struct.unpack('i', fcntl.ioctl(source.fileno(), 0x125e, bytes(4)))[0])
            while done < RECOVERY_BYTES:
                data = source.read(min(CHUNK, RECOVERY_BYTES - done))
                require(bool(data), 'RECOVERY ended before the admitted 40 MiB')
                digest.update(data)
                done += len(data)
            require(not source.read(1) and stamp(source.fileno()) == part['stamp'],
                    'RECOVERY geometry or descriptor stamp changed while hashing')
        require(digest.hexdigest() == args.expected_recovery_sha256,
                'The full 40 MiB RECOVERY hash does not match --expected-recovery-sha256')
        return {'partition': part, 'sha256': digest.hexdigest(), 'raw_bytes': done,
                'block_read_only': block_ro, 'read_only_descriptor': True}

    def storage_call(action, lease_fd):
        proc = subprocess.run([sys.executable, str(source_root / 'storage.py'), action],
                              pass_fds=(lease_fd,), capture_output=True, text=True, timeout=45)
        require(len(proc.stdout) <= 65536, 'Storage helper returned an oversized response')
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError('Storage helper returned invalid JSON: ' + proc.stderr[-2048:])
        remember('storage-' + action, {'exit_code': proc.returncode, 'result': data,
                                     'stderr': proc.stderr[-4096:]})
        require(proc.returncode == 0 and data.get('ok') is True,
                'Production storage helper failed: ' + str(data.get('error', action)))
        return data

    def verify_diagnostics(exported):
        path = no_symlinks(Path(exported['archive_path']))
        require(path.parent == REPOSITORY.parent and path.name.startswith('a22x-recovery-logs-')
                and path.name.endswith('.tar.gz'), 'Unexpected diagnostic archive destination')
        fd = open_regular_fd(path)
        members = {}
        manifest = None
        total = 0
        try:
            before = stamp(fd)
            require(before['size'] == exported['bytes'] and 0 < before['size'] <= management.MAX_TOTAL + CHUNK,
                    'Diagnostic compressed archive size is outside its bound')
            with os.fdopen(fd, 'rb', closefd=False) as source:
                digest = hashlib.sha256()
                remaining = before['size']
                while remaining:
                    data = source.read(min(CHUNK, remaining))
                    require(bool(data), 'Diagnostic archive was truncated')
                    digest.update(data)
                    remaining -= len(data)
                require(not source.read(1) and digest.hexdigest() == exported['sha256'],
                        'Diagnostic archive SHA-256 does not match the export receipt')
                source.seek(0)
                with tarfile.open(fileobj=source, mode='r|gz') as archive:
                    for member in archive:
                        pure = PurePosixPath(member.name)
                        require(member.isfile() and not pure.is_absolute() and '..' not in pure.parts
                                and member.name not in members and len(members) < 128,
                                'Diagnostic archive has an unsafe, duplicate or excessive member')
                        require(0 <= member.size <= management.MAX_LOG,
                                'Diagnostic member exceeds the bounded export contract')
                        total += member.size
                        require(total <= management.MAX_TOTAL, 'Diagnostic decoded data exceeds its limit')
                        stream = archive.extractfile(member)
                        require(stream is not None, 'Diagnostic member cannot be read')
                        with stream:
                            data = stream.read(member.size + 1)
                        require(len(data) == member.size, 'Diagnostic member length mismatch')
                        members[member.name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                        if member.name == 'manifest.json':
                            manifest = json.loads(data, object_pairs_hook=strict_object)
                require(stamp(fd) == before, 'Diagnostic archive changed during verification')
        finally:
            os.close(fd)
        require(isinstance(manifest, dict) and manifest.get('schema') == 'a22x.diagnostics.archive.v1'
                and manifest.get('mode') == 'device' and isinstance(manifest.get('files'), dict),
                'Diagnostic manifest is missing or has the wrong schema/mode')
        require(set(manifest['files']) == set(members) - {'manifest.json'},
                'Diagnostic member list does not match its manifest')
        for name, expected in manifest['files'].items():
            require(isinstance(expected, dict) and all(expected.get(key) == members[name][key]
                    for key in ('bytes', 'sha256')), 'Diagnostic member checksum mismatch: ' + name)
        return {'archive_path': str(path), 'bytes': exported['bytes'], 'sha256': exported['sha256'],
                'member_count': len(members), 'decoded_bytes': total, 'members': members,
                'archive_sha256_verified': True, 'manifest_members_verified': True}

    try:
        with operation_lock(LOCK, 'device') as lease_fd:
            previous_fd = os.environ.get(ENV_FD)
            os.environ[ENV_FD] = str(lease_fd)
            try:
                context = DeviceContext(args.device_config)
                context.assert_recovery()
                require(RUN in record.parents or Path('/tmp') in record.parents,
                        'Detailed receipt directory must be a new directory under recovery /run or /tmp')
                no_symlinks(record, must_exist=False)
                require(context._mount_for(record.parent)['filesystem'] in ('rootfs', 'ramfs', 'tmpfs'),
                        'Detailed receipt directory must be backed by RAM')
                record.mkdir(mode=0o700, exist_ok=False)
                record_created = True
                initial_volumes = volumes(fs.DeviceContext())
                for volume in initial_volumes.values():
                    require(not volume['mounted'] and not volume['block_read_only']
                            and not volume['read_only_guarded'], 'Initial userdata and SD must be unmounted with no RO guard')
                    context.graph.require_unmounted(volume['device'], context.mounts)
                require(initial_volumes['sdcard-p1']['fs_type'] == 'ext4', 'Existing SD must already contain ext4')
                remember('initial-volumes', flag_summary(initial_volumes))
                initial_recovery = remember('recovery-before', recovery_hash(context))
                mount_attempted = True
                mounted = storage_call('mount', lease_fd)
                require(mounted.get('mounted') and mounted.get('repository') == str(REPOSITORY),
                        'The production helper did not mount the expected repository')
                context = context.refresh()
                repository = context.repository(REPOSITORY, writing=True)
                require(repository['filesystem'] == 'ext4' and not repository['read_only'],
                        'Backup repository is not the expected writable ext4 SD')
                plan = engine.make_backup_plan(context, 'recovery', REPOSITORY, name, 'gzip')
                require(plan['partitions'] == [initial_recovery['partition']],
                        'The backup plan no longer identifies the pinned RECOVERY inventory')
                remember('backup-plan', plan)
                backup = remember('backup', engine.backup(context, plan, plan['confirmation_token']))
                receipt['backup_path'] = backup['backup_path']
                verified = engine.verify_backup(context.refresh(), backup['backup_path'])
                entries = verified['manifest']['entries']
                require(verified['verified'] and verified['manifest']['profile'] == 'recovery'
                        and len(entries) == 1 and entries[0]['label'] == 'recovery'
                        and entries[0]['raw_bytes'] == RECOVERY_BYTES
                        and entries[0]['raw_sha256'] == args.expected_recovery_sha256,
                        'Verified backup does not contain exactly the expected RECOVERY bytes')
                remember('backup-verify', verified)
                listed = engine.list_backups(context.refresh(), REPOSITORY)['backups']
                own = [item for item in listed if item['path'] == backup['backup_path']]
                require(len(own) == 1 and own[0]['status'] == 'complete', 'Completed backup is missing from listing')
                remember('backup-list', {'total_completed_backups': len(listed), 'owned_backup': own[0]})
                after = remember('recovery-after', recovery_hash(context.refresh()))
                require(after == initial_recovery, 'RECOVERY identity, read-only flag or bytes changed')
                fs_context = fs.DeviceContext()
                sd = fs_context.volume('sdcard-p1', initial_volumes['sdcard-p1']['volume_token'])
                require(sd['mounted'] and sd['mount_owner'] == 'storage' and sd['mountpoint'] == '/mnt/backup'
                        and not sd['read_only'] and not sd['block_read_only'], 'Diagnostics SD is no longer the admitted RW mount')
                exported = remember('diagnostics-export', management.export(
                    fs_context, 'sdcard-p1', sd['volume_token'], confirmed=True))
                diagnostics = remember('diagnostics-verify', verify_diagnostics(exported))
                receipt.update(ok=True, raw_sha256=entries[0]['raw_sha256'], archive_sha256=entries[0]['archive_sha256'],
                               backup_archive_bytes=entries[0]['archive_bytes'], diagnostics_archive=exported['archive_path'],
                               diagnostics_sha256=diagnostics['sha256'], diagnostics_member_count=diagnostics['member_count'])
            except BaseException as error:
                receipt.update(ok=False, error={'type': type(error).__name__, 'code': getattr(error, 'code', None),
                                               'message': str(error)[:4096], 'details': getattr(error, 'details', {})})
            finally:
                try:
                    if mount_attempted:
                        storage_call('umount', lease_fd)
                    if initial_volumes is not None:
                        final_context = DeviceContext(args.device_config)
                        final_volumes = volumes(fs.DeviceContext())
                        receipt['final_flags'] = flag_summary(final_volumes)
                        for name, volume in final_volumes.items():
                            require(volume['volume_token'] == initial_volumes[name]['volume_token']
                                    and not volume['mounted'] and not volume['block_read_only']
                                    and not volume['read_only_guarded'], 'Final volume identity/mount/RO flags differ from admission')
                            final_context.graph.require_unmounted(volume['device'], final_context.mounts)
                        receipt['final_unmounted_and_ro_flags_clear'] = True
                except BaseException as error:
                    receipt.update(ok=False, cleanup_error=str(error)[:4096], final_unmounted_and_ro_flags_clear=False)
                if previous_fd is None:
                    os.environ.pop(ENV_FD, None)
                else:
                    os.environ[ENV_FD] = previous_fd
    except BaseException as error:
        receipt.update(ok=False, admission_error=str(error)[:4096])
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
    if record_created:
        try:
            write_json_new(record / 'result.json', receipt)
        except OSError as error:
            receipt.update(ok=False, receipt_write_error=str(error)[:1024])
    summary = {key: value for key, value in receipt.items() if key not in ('steps', 'final_flags')}
    if 'final_flags' in receipt:
        summary['final_flags'] = {name: {key: value.get(key) for key in
                                       ('mounted', 'block_read_only', 'read_only_guarded')}
                                  for name, value in receipt['final_flags'].items()}
    if 'error' in summary:
        summary['error'] = {key: value for key, value in summary['error'].items() if key != 'details'}
    # Summary is bounded; complete inventories/manifests/receipts remain in RAM.
    print(json.dumps(summary, sort_keys=True, ensure_ascii=True))
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
