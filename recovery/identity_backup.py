#!/usr/bin/env python3
"""Back up and restore this phone's own identity partitions (EFS, NV data), and its Android
partitions (BOOT, super, dtbo, vbmeta and others), as TWRP does.

IMEI, calibration and modem NV data live in small partitions that no image of Android or
Linux carries; a lost or damaged copy cannot be downloaded again. `backup` reads them into
one folder on a mounted volume with a manifest of sizes and SHA-256 sums and reads the
files back. `restore` writes such a folder back only to the same phone (eMMC identity),
only to partitions with the very same GPT geometry, only while they are not in use, saves
the current content first and reads every partition back. Bootloader partitions are never
listed. Every call prints one JSON line; progress goes to stderr.

--kind android (2026-11-10) works on the partitions of an Android installation instead, into
A22xAndroidBackups: --labels chooses some of them; zero blocks become holes in the copies;
userdata is no destination (Android's factory reset erases it); a copy may go back to any
phone with the very same partition layout, since these partitions hold nothing of one phone.

    identity_backup.py --device [--kind android] list
    identity_backup.py --device backup --volume ID
    identity_backup.py --device backups --volume ID
    identity_backup.py --device verify --volume ID --path /A22xIdentityBackups/NAME
    identity_backup.py --device restore --volume ID --path /A22xIdentityBackups/NAME \\
        --expected-manifest-sha256 H --confirm
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import time

import filesystem as fs
import install
from install import require
from operation_lock import operation_lock

SCHEMA = 'a22x.identity-backup.v1'
FOLDER = 'A22xIdentityBackups'
# Samsung MT6833 names (2026-11-08). Only those present are used. No bootloader, TEE,
# modem firmware or Android system partition is in this list, and none can be added by an argument.
LABELS = ('efs', 'sec_efs', 'nvcfg', 'nvdata', 'nvram', 'persist', 'protect1', 'protect2',
          'protect_f', 'protect_s', 'proinfo', 'keydata', 'keyrefuge')
# In copies made before 2026-11-09: checked with the rest, never written back. param holds the
# bootloader's settings, which LK rewrites on almost every start (found by comparing on the phone).
RETIRED = ('param',)
MAX_PART = 128 * 1024 * 1024
PREFIX = 'identity'
SAME_PHONE = True
ANDROID_LABELS = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'super', 'prism', 'optics')
KINDS = {
    'identity': {'SCHEMA': SCHEMA, 'FOLDER': FOLDER, 'LABELS': LABELS, 'RETIRED': RETIRED, 'MAX_PART': MAX_PART,
                 'PREFIX': 'identity', 'SAME_PHONE': True},
    'android': {'SCHEMA': 'a22x.android-backup.v1', 'FOLDER': 'A22xAndroidBackups', 'LABELS': ANDROID_LABELS,
                'RETIRED': (), 'MAX_PART': 8 << 30, 'PREFIX': 'android', 'SAME_PHONE': False},
}
KIND = 'identity'
CHOSEN = None  # --labels: the partitions to save, all of the kind when None


def use(kind, labels=None):
    """Work on one kind of backup from now on; LABELS limits it to some of its partitions."""
    global KIND, CHOSEN
    require(kind in KINDS, 'INVALID_ARGUMENT', 'Unknown kind of backup')
    globals().update(KINDS[kind])
    KIND = kind
    CHOSEN = None
    if labels:
        chosen = tuple(label for label in labels.split(',') if label)
        require(chosen and all(label in LABELS for label in chosen), 'INVALID_ARGUMENT', 'Choose partitions of this kind of backup')
        CHOSEN = chosen
MAX_MANIFEST = 1 << 16
CHUNK = install.CHUNK
BLKFLSBUF = 0x1261


def emmc_identity():
    """A hash of the eMMC CID: it tells phones apart without storing the serial number."""
    return hashlib.sha256(Path('/sys/block/mmcblk0/device/cid').read_bytes().strip()).hexdigest()


class Device:
    mode = 'device'

    def __init__(self):
        self.install = install.DeviceContext()
        self.fs, self.lock_path = self.install.fs, self.install.lock_path
        self.identity = emmc_identity()

    def parts(self):
        found = []
        raw = self.install.raw
        for label in LABELS:
            link = Path('/dev/disk/by-partlabel') / label
            if not link.exists():
                continue
            path = link.resolve(strict=True)
            st = path.lstat()
            require(stat.S_ISBLK(st.st_mode), 'TARGET_NOT_BLOCK', 'A partition label does not lead to a partition', label=label)
            devno = '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev))
            node = raw.nodes.get(devno)
            require(node and raw.graph.physical_roots(devno) == {raw.internal_disk},
                    'WRONG_INTERNAL_DEVICE', 'The partition is not on the internal eMMC', label=label)
            sysfs = Path(node['sysfs'])
            fields = dict(line.split('=', 1) for line in (sysfs / 'uevent').read_text().splitlines() if '=' in line)
            require(fields.get('PARTNAME') == label, 'GPT_IDENTITY_MISMATCH', 'The partition name changed', label=label)
            size = int((sysfs / 'size').read_text()) * 512
            require(0 < size <= MAX_PART, 'PARTITION_TOO_LARGE', 'A partition is unexpectedly large for this kind of backup', label=label)
            found.append({'label': label, 'path': str(path), 'device': devno, 'size_bytes': size,
                          'partition_number': int((sysfs / 'partition').read_text()),
                          'start_sector': int((sysfs / 'start').read_text()), 'partuuid': fields.get('PARTUUID', ''),
                          'object': [st.st_dev, st.st_ino, st.st_rdev]})
        return found

    def check_offline(self, part):
        self.install.raw.graph.require_unmounted(part['device'], self.install.raw.mounts)
        self.install.raw.graph.require_no_holders(part['device'])

    def open(self, part, write=False):
        fd = os.open(part['path'], (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_EXCL if write else 0))
        try:
            st = os.fstat(fd)
            require(stat.S_ISBLK(st.st_mode) and [st.st_dev, st.st_ino, st.st_rdev] == part['object']
                    and struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] == part['size_bytes'],
                    'TARGET_CHANGED', 'The partition changed', label=part['label'])
            if write:
                require(not struct.unpack('i', fcntl.ioctl(fd, 0x125e, bytes(4)))[0], 'TARGET_READ_ONLY',
                        'The kernel keeps this partition read-only', label=part['label'])
            return fd
        except BaseException:
            os.close(fd)
            raise

    def flush(self, fd):
        os.fsync(fd)
        fcntl.ioctl(fd, BLKFLSBUF)


class Fixture:
    mode = 'fixture'

    def __init__(self, root):
        self.root = fs.absolute_no_symlinks(root)
        self.fs = fs.FixtureContext(self.root)
        self.lock_path = self.root / '.operation.lock'
        config = json.loads((self.root / 'install-fixture.json').read_text())
        self.identity = hashlib.sha256(config['device']['identity'].encode()).hexdigest()
        self.config = config.get('android_parts' if KIND == 'android' else 'identity_parts', {})
        self.busy = set(config.get('identity_busy', ()))

    def parts(self):
        found = []
        for number, label in enumerate(LABELS, 1):
            spec = self.config.get(label)
            if spec:
                path = self.root / spec['path']
                st = path.stat()
                found.append({'label': label, 'path': str(path), 'device': 'fixture:%d' % number, 'size_bytes': st.st_size,
                              'partition_number': number, 'start_sector': number * 2048, 'partuuid': 'fixture-' + label,
                              'object': [st.st_dev, st.st_ino, 0]})
        return found

    def check_offline(self, part):
        require(part['label'] not in self.busy, 'TARGET_MOUNTED', 'The partition is in use', label=part['label'])

    def open(self, part, write=False):
        return os.open(part['path'], (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC | os.O_NOFOLLOW)

    def flush(self, fd):
        os.fsync(fd)


def geometry(part):
    return {k: part[k] for k in ('label', 'size_bytes', 'partition_number', 'start_sector', 'partuuid')}


def read_part(context, part, progress, out=None):
    fd = context.open(part)
    try:
        digest, done = hashlib.sha256(), 0
        while done < part['size_bytes']:
            data = os.pread(fd, min(CHUNK, part['size_bytes'] - done), done)
            require(data, 'TARGET_READ_FAILED', 'The partition could not be read', label=part['label'])
            if out is not None:
                if data.count(0) == len(data):  # A hole: what an unused part of super is mostly made of.
                    os.lseek(out, len(data), os.SEEK_CUR)
                else:
                    install.write_all(out, data)
            digest.update(data)
            done += len(data)
            progress('Reading', part['label'], done, part['size_bytes'])
        if out is not None:
            os.ftruncate(out, part['size_bytes'])
        return digest.hexdigest()
    finally:
        os.close(fd)


def hash_file(folder, name, size, progress, label):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_size == size, 'BACKUP_DAMAGED', 'A file of the backup has the wrong size', label=label)
        digest, done = hashlib.sha256(), 0
        while done < size:
            data = os.pread(fd, min(CHUNK, size - done), done)
            require(data, 'BACKUP_DAMAGED', 'A file of the backup is shorter than recorded', label=label)
            digest.update(data)
            done += len(data)
            progress('Checking the backup', label, done, size)
        return digest.hexdigest()
    finally:
        os.close(fd)


def write_new(folder, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
    try:
        install.write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def save(context, volume, parts, progress, prefix=None):
    """One new folder with every partition and its manifest; the files are read back."""
    prefix = prefix or PREFIX
    with fs.PinnedVolume(context.fs, volume, writing=True) as pin:
        require(KIND != 'android' or (pin.volume.get('label') or pin.volume.get('id')) != 'userdata', 'DESTINATION_NOT_ALLOWED',
                'Save Android partitions to an SD card or a USB stick: a factory reset of Android erases userdata')
        info = os.statvfs(pin.fd)
        need = sum(p['size_bytes'] for p in parts) + CHUNK
        require(info.f_bavail * info.f_frsize >= need, 'NO_SPACE', 'The volume has too little free space for the backup',
                required_bytes=need)
        with contextlib.suppress(FileExistsError):
            os.mkdir(FOLDER, 0o700, dir_fd=pin.fd)
        top = os.open(FOLDER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.fd)
        try:
            pin.check_fd(top)
            stamp = prefix + time.strftime('-%Y%m%d-%H%M%S', time.gmtime())
            for attempt in range(100):
                name = stamp + ('-%d' % attempt if attempt else '')
                try:
                    os.mkdir(name, 0o700, dir_fd=top)
                    break
                except FileExistsError:
                    continue
            else:
                fs.fail('BACKUP_NAME_EXHAUSTED', 'No new backup folder name is free')
            folder = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=top)
            try:
                rows = []
                for part in parts:
                    out = os.open(part['label'] + '.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  0o600, dir_fd=folder)
                    try:
                        value = read_part(context, part, progress, out)
                        os.fsync(out)
                    finally:
                        os.close(out)
                    require(hash_file(folder, part['label'] + '.img', part['size_bytes'], progress, part['label']) == value,
                            'BACKUP_WRITE_MISMATCH', 'The saved copy differs from the partition', label=part['label'])
                    rows.append({**geometry(part), 'file': part['label'] + '.img', 'sha256': value})
                manifest = json.dumps({'schema': SCHEMA, 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                       'device_identity': context.identity, 'partitions': rows},
                                      indent=1, sort_keys=True).encode() + b'\n'
                write_new(folder, 'manifest.json', manifest)
                os.fsync(folder)
            finally:
                os.close(folder)
        finally:
            os.close(top)
        return {'volume': pin.volume.get('label') or volume, 'path': '/%s/%s' % (FOLDER, name),
                'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'partitions': rows,
                'bytes': sum(p['size_bytes'] for p in parts)}


@contextlib.contextmanager
def backup_folder(context, volume, path):
    parts = fs.path_parts(path)
    require(len(parts) == 2 and parts[0] == FOLDER, 'INVALID_BACKUP_PATH', 'Choose a folder inside %s' % FOLDER)
    with fs.PinnedVolume(context.fs, volume) as pin, pin.directory(path) as folder:
        fd = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
        try:
            require(os.fstat(fd).st_size <= MAX_MANIFEST, 'BACKUP_DAMAGED', 'The manifest is too large')
            data = os.read(fd, MAX_MANIFEST + 1)
        finally:
            os.close(fd)
        try:
            manifest = json.loads(data)
        except ValueError:
            fs.fail('BACKUP_DAMAGED', 'The manifest is not readable')
        require(isinstance(manifest, dict) and manifest.get('schema') == SCHEMA and isinstance(manifest.get('partitions'), list)
                and manifest['partitions'], 'BACKUP_DAMAGED', 'This is not a backup of this kind')
        for row in manifest['partitions']:
            require(isinstance(row, dict) and row.get('label') in LABELS + RETIRED and row.get('file') == row['label'] + '.img'
                    and isinstance(row.get('size_bytes'), int) and 0 < row['size_bytes'] <= MAX_PART
                    and isinstance(row.get('sha256'), str) and len(row['sha256']) == 64,
                    'BACKUP_DAMAGED', 'The manifest lists an invalid partition')
        require(len({r['label'] for r in manifest['partitions']}) == len(manifest['partitions']),
                'BACKUP_DAMAGED', 'The manifest lists a partition twice')
        yield folder, manifest, hashlib.sha256(data).hexdigest()


def verify_files(folder, manifest, progress):
    for row in manifest['partitions']:
        require(hash_file(folder, row['file'], row['size_bytes'], progress, row['label']) == row['sha256'],
                'BACKUP_DAMAGED', 'A file of the backup does not match its checksum', label=row['label'])


def backups(context, args):
    """The backup folders on a volume, newest first, with what verify needs to start."""
    rows = []
    with fs.PinnedVolume(context.fs, args.volume) as pin:
        try:
            top = os.open(FOLDER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.fd)
        except FileNotFoundError:
            return {'backups': [], 'folder': FOLDER}
        try:
            pin.check_fd(top)
            names = sorted((n for n in os.listdir(top) if not n.startswith('.')), reverse=True)[:32]
        finally:
            os.close(top)
    for name in names:
        try:
            with backup_folder(context, args.volume, '/%s/%s' % (FOLDER, name)) as (_folder, manifest, manifest_sha):
                rows.append({'path': '/%s/%s' % (FOLDER, name), 'created_utc': manifest.get('created_utc'),
                             'same_phone': manifest.get('device_identity') == context.identity or not SAME_PHONE,
                             'partitions': len(manifest['partitions']), 'manifest_sha256': manifest_sha,
                             'bytes': sum(r['size_bytes'] for r in manifest['partitions'])})
        except (OSError, fs.FilesystemError):
            continue
    return {'backups': rows, 'folder': FOLDER}


def chosen_parts(context):
    parts = context.parts()
    return [p for p in parts if CHOSEN is None or p['label'] in CHOSEN]


def listing(context):
    parts = context.parts()
    return {'partitions': [{'label': p['label'], 'size_bytes': p['size_bytes']} for p in parts],
            'bytes': sum(p['size_bytes'] for p in parts), 'folder': FOLDER}


def backup(context, args, progress):
    with operation_lock(context.lock_path, context.mode):
        parts = chosen_parts(context)
        require(parts, 'NO_IDENTITY_PARTITIONS', 'No partition of this kind was found')
        return save(context, args.volume, parts, progress)


def verify(context, args, progress):
    with backup_folder(context, args.volume, args.path) as (folder, manifest, manifest_sha):
        verify_files(folder, manifest, progress)
        current = {p['label']: p for p in context.parts()}
        same_phone = manifest.get('device_identity') == context.identity or not SAME_PHONE
        wanted = [r for r in manifest['partitions'] if r['label'] not in RETIRED]
        matching = [r['label'] for r in wanted
                    if r['label'] in current and geometry(current[r['label']]) == {k: r.get(k) for k in geometry(current[r['label']])}]
        return {'verified': True, 'manifest_sha256': manifest_sha, 'created_utc': manifest.get('created_utc'),
                'same_phone': same_phone, 'partitions': [{'label': r['label'], 'size_bytes': r['size_bytes']} for r in manifest['partitions']],
                'restorable': same_phone and len(matching) == len(wanted)}


def restore(context, args, progress):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Restoring identity partitions must be confirmed')
    with operation_lock(context.lock_path, context.mode):
        with backup_folder(context, args.volume, args.path) as (folder, manifest, manifest_sha):
            require(manifest_sha == args.expected_manifest_sha256, 'BACKUP_CHANGED', 'The backup changed after it was checked')
            require(manifest.get('device_identity') == context.identity or not SAME_PHONE, 'OTHER_PHONE',
                    'This backup belongs to another phone. Restoring it would give this phone a foreign identity')
            current = {p['label']: p for p in context.parts()}
            targets = []
            for row in manifest['partitions']:
                if row['label'] in RETIRED:
                    continue
                part = current.get(row['label'])
                require(part is not None and geometry(part) == {k: row.get(k) for k in geometry(part)}, 'GEOMETRY_MISMATCH',
                        'The partition layout differs from the backup', label=row['label'])
                context.check_offline(part)
                targets.append((row, part))
            verify_files(folder, manifest, progress)
            changed = []
            for row, part in targets:
                if read_part(context, part, progress) != row['sha256']:
                    changed.append(part)
            if not changed:
                return {'restored': [], 'identical': True, 'before': None, 'manifest_sha256': manifest_sha}
            # The content being replaced is kept: a restore is reversible the same way.
            before = save(context, args.volume, changed, progress, prefix='before-restore')
            done = []
            for row, part in targets:
                if part not in changed:
                    continue
                source = os.open(row['file'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
                writer = context.open(part, write=True)
                try:
                    digest, position = hashlib.sha256(), 0
                    while position < row['size_bytes']:
                        data = os.pread(source, min(CHUNK, row['size_bytes'] - position), position)
                        require(data, 'BACKUP_DAMAGED', 'The backup file ended early', label=row['label'])
                        install.write_all(writer, data, offset=position)
                        digest.update(data)
                        position += len(data)
                        progress('Writing', row['label'], position, row['size_bytes'])
                    context.flush(writer)
                finally:
                    os.close(writer)
                    os.close(source)
                require(digest.hexdigest() == row['sha256'], 'BACKUP_CHANGED', 'The backup changed during the restore',
                        label=row['label'], before=before['path'])
                require(read_part(context, part, progress) == row['sha256'], 'READBACK_MISMATCH',
                        'The partition does not match the backup after writing', label=row['label'], before=before['path'])
                done.append(row['label'])
            return {'restored': done, 'identical': False, 'before': before['path'], 'manifest_sha256': manifest_sha}


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    result.add_argument('--kind', choices=sorted(KINDS), default='identity')
    result.add_argument('--labels', default='')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    commands.add_parser('backup').add_argument('--volume', required=True)
    for name in ('verify', 'restore'):
        command = commands.add_parser(name)
        command.add_argument('--volume', required=True)
        command.add_argument('--path', required=True)
    commands.choices['restore'].add_argument('--expected-manifest-sha256', required=True)
    commands.choices['restore'].add_argument('--confirm', action='store_true')
    commands.add_parser('backups').add_argument('--volume', required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    last = [None, 0.0]

    def progress(phase, label, done, total):
        now = time.monotonic()
        if (phase, label) != last[0] or now - last[1] >= 0.2 or done == total:
            print(json.dumps({'event': 'progress', 'operation': args.command, 'phase': phase, 'partition': label,
                              'bytes_done': done, 'bytes_total': total}, separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [(phase, label), now]

    try:
        use(args.kind, args.labels)
        context = Fixture(args.fixture) if args.fixture else Device()
        result = {'list': lambda: listing(context), 'backup': lambda: backup(context, args, progress),
                  'verify': lambda: verify(context, args, progress), 'restore': lambda: restore(context, args, progress),
                  'backups': lambda: backups(context, args)}[args.command]()
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, fs.FilesystemError) as error:
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
