#!/usr/bin/env python3
"""Explicit external microSD mounting for the A22x RAM recovery.

No formatting, filesystem repair, internal partition mounting, or arbitrary path
arguments. The first device version supports the existing ext4 microSD only.
"""
# SPDX-License-Identifier: MIT
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
from operation_lock import operation_lock

DEVICE = Path('/dev/mmcblk1p1')
PARENT = Path('/sys/class/block/mmcblk1')
PART = Path('/sys/class/block/mmcblk1p1')
MOUNTPOINT = Path('/mnt/backup')
STATE = Path('/run/a22x-recovery')


class StorageError(RuntimeError):
    pass


def mount_unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mounts(text=None):
    text = Path('/proc/self/mountinfo').read_text() if text is None else text
    result = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        if not sep or len(a) < 6 or len(b) < 3:
            raise StorageError('MALFORMED_MOUNTINFO')
        result.append({'devno': a[2], 'mountpoint': mount_unescape(a[4]),
                       'options': a[5].split(','), 'filesystem': b[0],
                       'source': mount_unescape(b[1])})
    return result


def admission():
    if os.geteuid() != 0:
        raise StorageError('RECOVERY_ROOT_REQUIRED')
    if 'a22x.recovery=1' not in Path('/proc/cmdline').read_text().split():
        raise StorageError('DEDICATED_RECOVERY_REQUIRED')
    marker = json.loads((STATE / 'boot.json').read_text())
    if marker.get('schema') != 'a22x.recovery.boot.v1' or marker.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise StorageError('RECOVERY_BOOT_RECEIPT_MISMATCH')
    roots = [m for m in mounts() if m['mountpoint'] == '/']
    if len(roots) != 1 or roots[0]['filesystem'] not in ('rootfs', 'ramfs', 'tmpfs'):
        raise StorageError('ROOT_MUST_BE_RAM')


def probe_ext4(fd):
    data = os.pread(fd, 1024, 1024)
    if len(data) != 1024 or data[56:58] != b'\x53\xef':
        raise StorageError('EXT4_MICROSD_REQUIRED_NO_FORMAT_PERFORMED')
    raw_label = data[120:136].split(b'\0', 1)[0].decode('utf-8', 'replace')
    label = ''.join(c if c.isprintable() else '?' for c in raw_label)
    uid = data[104:120].hex()
    return {'filesystem': 'ext4', 'label': label,
            'filesystem_uuid': '-'.join([uid[:8], uid[8:12], uid[12:16], uid[16:20], uid[20:]])}


def inspect_sd():
    if (PARENT / 'device/type').read_text().strip() != 'SD':
        raise StorageError('SECOND_MMC_IS_NOT_AN_SD_CARD')
    if (PART / 'partition').read_text().strip() != '1' or PART.resolve().parent != PARENT.resolve():
        raise StorageError('UNEXPECTED_SD_PARTITION_PARENT')
    fd = os.open(DEVICE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISBLK(st.st_mode):
            raise StorageError('SD_TARGET_IS_NOT_A_BLOCK_DEVICE')
        devno = f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}'
        if devno != (PART / 'dev').read_text().strip():
            raise StorageError('SD_DEVICE_ID_CHANGED')
        values = probe_ext4(fd)
        values.update({'device': str(DEVICE), 'devno': devno,
                       'device_bytes': struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]})
    finally:
        os.close(fd)
    target = [m for m in mounts() if m['devno'] == devno]
    if any(m['mountpoint'] != str(MOUNTPOINT) for m in target):
        raise StorageError('SD_ALREADY_MOUNTED_ELSEWHERE')
    occupied = [m for m in mounts() if m['mountpoint'] == str(MOUNTPOINT)]
    if occupied and (len(occupied) != 1 or occupied[0]['devno'] != devno):
        raise StorageError('BACKUP_MOUNTPOINT_OCCUPIED')
    values.update({'mounted': bool(target), 'mountpoint': str(MOUNTPOINT)})
    if target:
        usage = os.statvfs(MOUNTPOINT)
        values.update({'total_bytes': usage.f_blocks * usage.f_frsize,
                       'free_bytes': usage.f_bavail * usage.f_frsize})
    return values


@contextmanager
def operation_gate(action):
    if action == 'inventory':
        yield
        return
    # Share the backend's lock: orphan backup processes also block unmount/power.
    with ExitStack() as stack:
        try:
            stack.enter_context(operation_lock('/run/lock/a22x-mainline-recovery.lock', 'device'))
        except BlockingIOError as exc:
            raise StorageError('BACKUP_OR_RESTORE_IS_BUSY') from exc
        yield


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('inventory', 'mount', 'umount'))
    args = ap.parse_args()
    try:
        admission()
        STATE.mkdir(mode=0o700, exist_ok=True)
        with operation_gate(args.action), (STATE / 'storage.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # No inserted card is needed to reboot when nothing is mounted here.
            if args.action == 'umount' and not any(m['mountpoint'] == str(MOUNTPOINT) for m in mounts()):
                print(json.dumps({'ok': True, 'action': args.action, 'mounted': False,
                                  'mountpoint': str(MOUNTPOINT)}))
                return 0
            info = inspect_sd()
            if args.action == 'mount' and not info['mounted']:
                MOUNTPOINT.mkdir(mode=0o700, parents=True, exist_ok=True)
                if MOUNTPOINT.is_symlink() or any(MOUNTPOINT.iterdir()):
                    raise StorageError('BACKUP_MOUNTPOINT_MUST_BE_EMPTY_DIRECTORY')
                subprocess.run(['/bin/busybox', 'mount', '-t', 'ext4', '-o', 'rw,nodev,nosuid,noexec',
                                str(DEVICE), str(MOUNTPOINT)], check=True, capture_output=True, timeout=20)
                info = inspect_sd()
                if not info['mounted']:
                    raise StorageError('MOUNT_NOT_OBSERVED')
            elif args.action == 'umount' and info['mounted']:
                os.sync()
                subprocess.run(['/bin/busybox', 'umount', str(MOUNTPOINT)],
                               check=True, capture_output=True, timeout=20)
                info = inspect_sd()
                if info['mounted']:
                    raise StorageError('UNMOUNT_NOT_OBSERVED')
            if args.action == 'mount' and info['mounted']:
                repository = MOUNTPOINT / 'A22xBackups'
                if repository.is_symlink() or (repository.exists() and not repository.is_dir()):
                    raise StorageError('BACKUP_REPOSITORY_MUST_BE_A_REAL_DIRECTORY')
                repository.mkdir(mode=0o700, exist_ok=True)
                info['repository'] = str(repository)
            print(json.dumps({'ok': True, 'action': args.action, **info}))
            return 0
    except (OSError, ValueError, StorageError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'action': args.action, 'error': str(exc),
                          'device': str(DEVICE), 'mountpoint': str(MOUNTPOINT)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
