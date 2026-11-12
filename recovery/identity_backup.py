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
A22xAndroidBackups: --labels chooses some of them; userdata is no destination (Android's factory
reset erases it); a copy may go back to any phone with the very same partition layout, since
these partitions hold nothing of one phone.

Since 2026-11-11 an Android copy is one compressed archive, NAME.tar.zst (partition_archive.py):
every chosen partition and the manifest in a tar, packed by zstd. `restore` of an archive checks
the whole archive first, then writes only the pieces that differ and reads each partition back.
Recovery and cache are in no copy, so they stay; the BOOT that was there is kept on cache, and an
Android BOOT gets the mark that keeps this recovery (saved_boot.py). dtbo and vbmeta are locked by
the bootloader while the phone runs: when they differ they are left as they are. `copies` lists
the copies on every mounted SD card and USB drive. Folder copies made before stay readable.

    identity_backup.py --device [--kind android] list
    identity_backup.py --device backup --volume ID
    identity_backup.py --device backups --volume ID
    identity_backup.py --device verify --volume ID --path /A22xIdentityBackups/NAME
    identity_backup.py --device restore --volume ID --path /A22xIdentityBackups/NAME \\
        --expected-manifest-sha256 H --confirm
    identity_backup.py --device --kind android copies
    identity_backup.py --device --kind android [--labels boot,super] restore --volume ID \\
        --path /A22xAndroidBackups/NAME.tar.zst --expected-manifest-sha256 H --confirm
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time

import filesystem as fs
import install
from install import require
from operation_lock import operation_lock
import partition_archive as archive

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
# Android's own data as it lies on the phone, encrypted (2026-11-11, the owner: "so that the data
# too can be saved and put back without decryption"): metadata holds the key userdata is encrypted
# with, itself wrapped by the phone's secure hardware. The two are saved and written back whole and
# only together, never read, and only when they are asked for by name. What the cell erased reads
# as zeros, so a copy takes about what Android's data takes. It goes back to this phone alone, and
# Android can read it only while the phone's secure hardware still holds the keys it was made with.
DATA_LABELS = ('metadata', 'userdata')
ANDROID_LABELS = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'super', 'prism', 'optics') + DATA_LABELS
# ARCHIVE: a new copy is one compressed archive instead of a folder of images.
# OPTIONAL: partitions that are in a copy, and go back, only when --labels names them.
ARCHIVE = False
OPTIONAL = ()
KINDS = {
    'identity': {'SCHEMA': SCHEMA, 'FOLDER': FOLDER, 'LABELS': LABELS, 'RETIRED': RETIRED, 'MAX_PART': MAX_PART,
                 'PREFIX': 'identity', 'SAME_PHONE': True, 'ARCHIVE': False, 'OPTIONAL': ()},
    'android': {'SCHEMA': 'a22x.android-backup.v1', 'FOLDER': 'A22xAndroidBackups', 'LABELS': ANDROID_LABELS,
                'RETIRED': (), 'MAX_PART': 512 << 30, 'PREFIX': 'android', 'SAME_PHONE': False, 'ARCHIVE': True,
                'OPTIONAL': DATA_LABELS},
}
# What an archive needs free beyond its estimated size.
MIN_ARCHIVE_ROOM = 64 << 20
# The estimate (2026-11-11): a partition up to SAMPLE_WHOLE counts in full; of a larger one
# SAMPLE_PIECES evenly spread pieces are packed. ESTIMATE_MARGIN covers what sampling cannot see.
SAMPLE_PIECES = 384
SAMPLE_WHOLE = 48 << 20
ESTIMATE_MARGIN = 1.06
FAT_FILE_LIMIT = (4 << 30) - 1
BLKZEROOUT = 0x127f
COPIES_SHOWN = 24
AVB_VERIFICATION_DISABLED = 2
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

    def zero(self, fd, start, length):
        """The range made zeros by the kernel, which this phone's eMMC does by trimming it: far
        quicker than writing, and no wear. False when the kernel cannot."""
        try:
            fcntl.ioctl(fd, BLKZEROOUT, struct.pack('QQ', start, length))
            return True
        except OSError:
            return False

    def locked(self):
        """The partitions the bootloader keeps from being written while the phone runs."""
        import flash
        return flash.WRITE_PROTECTED

    def boot_access(self, part):
        """The context saved_boot keeps and marks BOOT with."""
        return self.install

    def recorded(self, label, sha256):
        return install.record_flash(self.install, label, sha256)


class FixtureBoot:
    """What saved_boot asks of a context, on the fixture's BOOT file."""
    mode = 'fixture'

    def __init__(self, context, part):
        self.context, self.part, self.fs = context, part, context.fs

    def target(self, label):
        require(label == 'boot', 'TARGET_FORBIDDEN', 'Only BOOT is kept')
        return self.part

    def open_target(self, target, *, write=False):
        return self.context.open(target, write)

    def check_target(self, fd, target):
        pass


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
        self.locked_labels = tuple(config.get('write_protected', ()))
        self.no_zeroing, self.zeroed = bool(config.get('no_zeroing')), []

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

    def zero(self, fd, start, length):
        if self.no_zeroing:
            return False
        self.zeroed.append((start, length))
        done = 0
        while done < length:
            size = min(CHUNK, length - done)
            os.pwrite(fd, bytes(size), start + done)
            done += size
        return True

    def locked(self):
        return self.locked_labels

    def boot_access(self, part):
        return FixtureBoot(self, part)

    def recorded(self, label, sha256):
        return False


def geometry(part):
    return {k: part[k] for k in ('label', 'size_bytes', 'partition_number', 'start_sector', 'partuuid')}


def read_part(context, part, progress, out=None, sink=None):
    fd = context.open(part)
    try:
        digest, done = hashlib.sha256(), 0
        while done < part['size_bytes']:
            data = os.pread(fd, min(CHUNK, part['size_bytes'] - done), done)
            require(data, 'TARGET_READ_FAILED', 'The partition could not be read', label=part['label'])
            if sink is not None:
                sink(data)
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


def free_bytes(fd):
    info = os.statvfs(fd)
    return info.f_bavail * info.f_frsize


def packed_estimate(context, parts, progress):
    """About how much the archive of PARTS takes, before a byte of it is written (2026-11-11: a copy
    ran out of room on the card after minutes of writing). Evenly spread pieces of each large
    partition are packed as the archive packs them. The answer: the likely size, and the room to ask
    for, which adds twice the error of the sampling and a margin for what sampling cannot see."""
    likely = most = 0
    for part in parts:
        size = part['size_bytes']
        if size <= SAMPLE_WHOLE:
            likely += size
            most += size
            continue
        pieces = min(SAMPLE_PIECES, size // CHUNK)
        seen = {'read': 0, 'filled': 0}
        fd = context.open(part)
        try:
            def feed(fd=fd, part=part, size=size, pieces=pieces, seen=seen):
                for k in range(pieces):
                    data = os.pread(fd, CHUNK, (size - CHUNK) * k // max(1, pieces - 1) // 4096 * 4096)
                    require(len(data) == CHUNK, 'TARGET_READ_FAILED', 'The partition could not be read', label=part['label'])
                    seen['read'] += CHUNK
                    seen['filled'] += data.count(0) != CHUNK
                    progress('Estimating the size', part['label'], (k + 1) * CHUNK, pieces * CHUNK)
                    yield data
            packed = archive.packed_bytes(feed())
        finally:
            os.close(fd)
        share = min(1.0, packed / seen['read'])
        # The pieces that hold anything are a share of all that sampling knows to about sqrt(p(1-p)/n).
        filled = seen['filled'] / pieces
        within = min(1.0, packed / (seen['filled'] * CHUNK)) if seen['filled'] else 0.0
        error = 2 * within * (filled * (1 - filled) / pieces) ** 0.5
        likely += int(size * share)
        most += int(size * min(1.0, share + error))
    return likely, int(most * ESTIMATE_MARGIN) + MIN_ARCHIVE_ROOM


def save_archive(context, volume, parts, progress):
    """One new compressed archive with every partition and its manifest (2026-11-11). It is written
    under a hidden name, read back from the volume, and only then gets its name."""
    with fs.PinnedVolume(context.fs, volume, writing=True) as pin:
        require((pin.volume.get('label') or pin.volume.get('id')) != 'userdata', 'DESTINATION_NOT_ALLOWED',
                'Save Android partitions to an SD card or a USB stick: a factory reset of Android erases userdata')
        likely, need = packed_estimate(context, parts, progress)
        free = free_bytes(pin.fd)
        require(free >= need, 'NO_SPACE', 'The volume has too little free space for the backup',
                required_bytes=need, free_bytes=free, estimated_bytes=likely)
        require(pin.volume.get('fs_type') != 'vfat' or likely <= FAT_FILE_LIMIT, 'FILE_TOO_LARGE',
                'This volume cannot hold a file this large: FAT32 stops at 4 GiB. Use exFAT or ext4, or leave super out',
                estimated_bytes=likely)
        with contextlib.suppress(FileExistsError):
            os.mkdir(FOLDER, 0o700, dir_fd=pin.fd)
        top = os.open(FOLDER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.fd)
        try:
            pin.check_fd(top)
            stamp = PREFIX + time.strftime('-%Y%m%d-%H%M%S', time.gmtime())
            taken = set(os.listdir(top))
            for attempt in range(100):
                name = stamp + ('-%d' % attempt if attempt else '') + archive.SUFFIX
                partial = '.' + name + '.partial'
                if name in taken or partial in taken:
                    continue
                try:
                    out = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=top)
                    break
                except FileExistsError:
                    continue
            else:
                fs.fail('BACKUP_NAME_EXHAUSTED', 'No new backup name is free')
            try:
                rows, writer = [], archive.Writer(out)
                try:
                    for part in parts:
                        writer.begin(part['label'] + '.img', part['size_bytes'])
                        value = read_part(context, part, progress, sink=writer.write)
                        rows.append({**geometry(part), 'file': part['label'] + '.img', 'sha256': value})
                    manifest = json.dumps({'schema': SCHEMA, 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                           'device_identity': context.identity, 'container': 'tar.zst', 'partitions': rows},
                                          indent=1, sort_keys=True).encode() + b'\n'
                    # What zstd still holds goes to the card now, which can take a while on a slow one.
                    progress('Finishing the archive', parts[-1]['label'], parts[-1]['size_bytes'], parts[-1]['size_bytes'])
                    writer.finish(manifest)
                except BaseException:
                    writer.abort()
                    raise
                # Read back from the volume itself, not from what the kernel still remembers of it.
                with contextlib.suppress(OSError, AttributeError):
                    os.posix_fadvise(out, 0, 0, os.POSIX_FADV_DONTNEED)
                require(archive.footer(out) == manifest, 'BACKUP_WRITE_MISMATCH', 'The saved archive lost its manifest')
                verify_archive(out, json.loads(manifest), manifest, progress)
                size = os.fstat(out).st_size
                rename_new(top, partial, name)
                os.fsync(top)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(partial, dir_fd=top)
                raise
            finally:
                os.close(out)
        finally:
            os.close(top)
        return {'volume': pin.volume.get('label') or volume, 'path': '/%s/%s' % (FOLDER, name),
                'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'partitions': rows,
                'bytes': sum(p['size_bytes'] for p in parts), 'archive': True, 'file_bytes': size, 'estimated_bytes': likely}


def rename_new(folder, old, new):
    """OLD becomes NEW in FOLDER, never over a name that is there."""
    try:
        fs.rename_noreplace(folder, old, folder, new)
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise
        # A filesystem without RENAME_NOREPLACE: the name was free when the copy began.
        require(not os.access(new, os.F_OK, dir_fd=folder), 'BACKUP_NAME_EXHAUSTED', 'The backup name was taken meanwhile')
        os.rename(old, new, src_dir_fd=folder, dst_dir_fd=folder)


def save(context, volume, parts, progress, prefix=None):
    """One new folder with every partition and its manifest; the files are read back."""
    if ARCHIVE and prefix is None:
        return save_archive(context, volume, parts, progress)
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
        yield folder, checked_manifest(data), hashlib.sha256(data).hexdigest()


def checked_manifest(data):
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
    return manifest


def is_archive(path):
    return ARCHIVE and isinstance(path, str) and path.endswith(archive.SUFFIX)


@contextlib.contextmanager
def backup_archive(context, volume, path):
    """An archive copy, opened: its descriptor, its manifest, the manifest's SHA-256 and bytes."""
    parts = fs.path_parts(path)
    require(len(parts) == 2 and parts[0] == FOLDER and parts[1].endswith(archive.SUFFIX) and not parts[1].startswith('.'),
            'INVALID_BACKUP_PATH', 'Choose an archive inside %s' % FOLDER)
    with fs.PinnedVolume(context.fs, volume) as pin, pin.directory('/' + FOLDER) as top:
        fd = os.open(parts[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=top)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'BACKUP_DAMAGED', 'The archive is not a plain file')
            raw = archive.footer(fd)
            require(raw is not None, 'BACKUP_DAMAGED', 'The archive has no manifest at its end: it is incomplete, or no copy made here')
            yield fd, checked_manifest(raw), hashlib.sha256(raw).hexdigest(), raw
        finally:
            os.close(fd)


def archive_rows(fd, manifest, raw, progress, phase):
    """(row, chunks) for each partition of the archive as it comes; chunks gives (offset, data) and
    checks the checksum at its end. The manifest inside must be the one at the archive's end."""
    rows = {row['file']: row for row in manifest['partitions']}
    seen = set()
    with archive.members(fd) as walk:
        for name, size, stream in walk:
            if name == 'manifest.json':
                require(size == len(raw) and stream.read(size + 1) == raw, 'BACKUP_DAMAGED',
                        'The manifest inside the archive differs from the one at its end')
                seen.add(name)
                continue
            row = rows.get(name)
            require(row is not None and name not in seen and size == row['size_bytes'], 'BACKUP_DAMAGED',
                    'The archive holds a file its manifest does not list')
            seen.add(name)

            def chunks(row=row, stream=stream, size=size):
                digest, done = hashlib.sha256(), 0
                while done < size:
                    data = stream.read(min(CHUNK, size - done))
                    require(data, 'BACKUP_DAMAGED', 'A file of the backup is shorter than recorded', label=row['label'])
                    yield done, data
                    digest.update(data)
                    done += len(data)
                    progress(phase, row['label'], done, size)
                require(digest.hexdigest() == row['sha256'], 'BACKUP_DAMAGED',
                        'A file of the backup does not match its checksum', label=row['label'])

            reader = chunks()
            yield row, reader
            for _ in reader:  # What the caller left unread is read and checked all the same.
                pass
    require(seen == set(rows) | {'manifest.json'}, 'BACKUP_DAMAGED', 'The archive lacks a file its manifest lists')


def verify_archive(fd, manifest, raw, progress):
    for _row, chunks in archive_rows(fd, manifest, raw, progress, 'Checking the backup'):
        for _ in chunks:
            pass


def verify_files(folder, manifest, progress):
    for row in manifest['partitions']:
        require(hash_file(folder, row['file'], row['size_bytes'], progress, row['label']) == row['sha256'],
                'BACKUP_DAMAGED', 'A file of the backup does not match its checksum', label=row['label'])


def copy_row(context, path, manifest, manifest_sha, file_bytes=None):
    """What a list says of one copy; FILE_BYTES is the size of an archive on its volume."""
    return {'path': path, 'created_utc': manifest.get('created_utc'),
            'same_phone': manifest.get('device_identity') == context.identity or not SAME_PHONE,
            'partitions': len(manifest['partitions']), 'manifest_sha256': manifest_sha,
            'bytes': sum(r['size_bytes'] for r in manifest['partitions']),
            'labels': [r['label'] for r in manifest['partitions']],
            'archive': file_bytes is not None, 'file_bytes': file_bytes}


def volume_copies(context, volume):
    """The copies on one volume, folders and archives, by name from the newest."""
    rows = []
    with fs.PinnedVolume(context.fs, volume) as pin:
        try:
            top = os.open(FOLDER, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.fd)
        except (FileNotFoundError, NotADirectoryError):
            return rows
        try:
            pin.check_fd(top)
            names = sorted((n for n in os.listdir(top) if not n.startswith('.')), reverse=True)[:32]
        finally:
            os.close(top)
    for name in names:
        path = '/%s/%s' % (FOLDER, name)
        try:
            if is_archive(path):
                with backup_archive(context, volume, path) as (fd, manifest, manifest_sha, _raw):
                    rows.append(copy_row(context, path, manifest, manifest_sha, os.fstat(fd).st_size))
            else:
                with backup_folder(context, volume, path) as (_folder, manifest, manifest_sha):
                    rows.append(copy_row(context, path, manifest, manifest_sha))
        except (OSError, ValueError, fs.FilesystemError):
            continue
    return rows


def backups(context, args):
    """The copies on a volume, newest first, with what verify needs to start."""
    return {'backups': volume_copies(context, args.volume), 'folder': FOLDER}


def removable(context, volume):
    if context.mode == 'device':
        return volume.get('external') is True
    return volume['id'].startswith(('sdcard', 'usb-'))


def copies(context):
    """Every copy of this kind on the SD cards and USB drives that are mounted, newest first, and
    the cards that are not mounted yet (2026-11-11: the Restore page finds the copies itself)."""
    rows, waiting = [], []
    for volume in context.fs.volumes():
        if not removable(context, volume) or not volume.get('volume_token'):
            continue
        place = {'volume': volume['id'], 'volume_token': volume['volume_token'],
                 'volume_label': volume.get('label') or volume['id']}
        if not volume['mounted']:
            if volume['mountable']:
                waiting.append({**place, 'fs_type': volume.get('fs_type') or '', 'size_bytes': volume.get('size_bytes', 0)})
            continue
        try:
            found = volume_copies(context, volume['id'])
        except (OSError, fs.FilesystemError):
            continue
        writable = not volume['read_only'] and bool(volume.get('write_supported'))
        rows += [{**row, **place, 'writable': writable} for row in found]
    rows.sort(key=lambda row: row.get('created_utc') or '', reverse=True)
    return {'backups': rows[:COPIES_SHOWN], 'unmounted': waiting, 'folder': FOLDER}


def delete(context, args):
    """Remove one copy from its volume (2026-11-11, as TWRP's Restore page does): only the folder
    whose manifest the page showed, on a volume mounted for writing."""
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Deleting a copy must be confirmed')
    with operation_lock(context.lock_path, context.mode):
        packed = is_archive(args.path)
        with (backup_archive if packed else backup_folder)(context, args.volume, args.path) as opened:
            manifest, manifest_sha = opened[1], opened[2]
            require(manifest_sha == args.expected_manifest_sha256, 'BACKUP_CHANGED', 'The copy changed after it was listed')
            size = sum(row['size_bytes'] for row in manifest['partitions'])
            count = len(manifest['partitions'])
        name = fs.path_parts(args.path)[1]  # backup_folder or backup_archive admitted exactly FOLDER/name.
        with fs.PinnedVolume(context.fs, args.volume, writing=True) as pin, pin.directory('/' + FOLDER) as top:
            if packed:
                os.unlink(name, dir_fd=top)
            else:
                shutil.rmtree(name, dir_fd=top)  # By descriptor: a symlink is removed, never followed.
            os.fsync(top)
    return {'deleted': '/%s/%s' % (FOLDER, name), 'partitions': count, 'bytes': size}


def together(labels, there):
    """Android's data is its key and what the key opens: both of them, or neither."""
    asked = [label for label in OPTIONAL if label in labels]
    require(not asked or asked == [label for label in OPTIONAL if label in there], 'DATA_PAIR',
            "Android's data needs userdata and metadata together: choose both, or neither")


def chosen_parts(context):
    """The partitions a copy is made of: all but the optional ones, or the ones --labels names."""
    parts = context.parts()
    if CHOSEN is None:
        return [p for p in parts if p['label'] not in OPTIONAL]
    together(CHOSEN, [p['label'] for p in parts])
    return [p for p in parts if p['label'] in CHOSEN]


def listing(context):
    parts = context.parts()
    return {'partitions': [{'label': p['label'], 'size_bytes': p['size_bytes'], 'optional': p['label'] in OPTIONAL} for p in parts],
            'bytes': sum(p['size_bytes'] for p in parts if p['label'] not in OPTIONAL), 'folder': FOLDER}


def backup(context, args, progress):
    with operation_lock(context.lock_path, context.mode):
        parts = chosen_parts(context)
        require(parts, 'NO_IDENTITY_PARTITIONS', 'No partition of this kind was found')
        for part in parts:
            if part['label'] in OPTIONAL:  # A filesystem in use changes while it is read.
                context.check_offline(part)
        return save(context, args.volume, parts, progress)


def facts(context, manifest, manifest_sha, progress):
    """What a checked copy is and whether it can go back to this phone. For an Android copy also
    the partitions the bootloader locks here whose content differs: a restore leaves them."""
    current = {p['label']: p for p in context.parts()}
    same_phone = manifest.get('device_identity') == context.identity or not SAME_PHONE
    wanted = [r for r in manifest['partitions'] if r['label'] not in RETIRED]
    matching = [r['label'] for r in wanted
                if r['label'] in current and geometry(current[r['label']]) == {k: r.get(k) for k in geometry(current[r['label']])}]
    found = {'manifest_sha256': manifest_sha, 'created_utc': manifest.get('created_utc'), 'same_phone': same_phone,
             'partitions': [{'label': r['label'], 'size_bytes': r['size_bytes']} for r in manifest['partitions']],
             'restorable': same_phone and len(matching) == len(wanted)}
    if KIND == 'android':
        held = locked_rows(context, [r for r in wanted if r['label'] in matching], current, progress)
        found['locked'] = held
        found['vbmeta_blocks'] = 'vbmeta' in held and not vbmeta_off(context, current)
    return found


def locked_rows(context, wanted, current, progress):
    """The partitions of a copy that the bootloader keeps from being written while the phone runs
    (flash.WRITE_PROTECTED) and that differ from the copy now: a restore leaves them as they are."""
    held = []
    for row in wanted:
        if row['label'] in context.locked() and row['label'] in current \
                and read_part(context, current[row['label']], progress) != row['sha256']:
            held.append(row['label'])
    return held


def vbmeta_off(context, current):
    """Whether the vbmeta on the phone has verification disabled; None when it is no AVB vbmeta."""
    part = current.get('vbmeta')
    if part is None:
        return None
    fd = context.open(part)
    try:
        head = os.pread(fd, 256, 0)
    finally:
        os.close(fd)
    if head[:4] != b'AVB0' or len(head) < 124:
        return None
    return bool(struct.unpack_from('>I', head, 120)[0] & AVB_VERIFICATION_DISABLED)


def verify(context, args, progress):
    if is_archive(args.path):
        with backup_archive(context, args.volume, args.path) as (fd, manifest, manifest_sha, raw):
            verify_archive(fd, manifest, raw, progress)
            return {'verified': True, **facts(context, manifest, manifest_sha, progress), 'archive': True,
                    'file_bytes': os.fstat(fd).st_size}
    with backup_folder(context, args.volume, args.path) as (folder, manifest, manifest_sha):
        verify_files(folder, manifest, progress)
        return {'verified': True, **facts(context, manifest, manifest_sha, progress), 'archive': False}


ZERO_RUN = 256 << 20


def write_changed(context, part, chunks):
    """A stream written over a partition, leaving every piece that is there already: the number of
    bytes written. Most of a system that is restored over itself needs no write at all. Pieces of
    zeros that follow one another are made by the kernel in one go (2026-11-11: Android's data put
    back over a Linux system is mostly that), and written out only where the kernel cannot."""
    writer = context.open(part, write=True)
    try:
        written = 0
        run = [0, 0]  # where the zeros that wait begin, and how many

        def zeros():
            start, length = run
            run[1] = 0
            if length and not context.zero(writer, start, length):
                done = 0
                while done < length:
                    size = min(CHUNK, length - done)
                    install.write_all(writer, bytes(size), offset=start + done)
                    done += size
            return length

        for offset, data in chunks:
            same = os.pread(writer, len(data), offset) == data
            if not same and data.count(0) == len(data) and (not run[1] or (run[0] + run[1] == offset and run[1] < ZERO_RUN)):
                if not run[1]:
                    run[0] = offset
                run[1] += len(data)
                continue
            written += zeros()
            if not same:
                if data.count(0) == len(data):
                    run[:] = [offset, len(data)]
                else:
                    install.write_all(writer, data, offset=offset)
                    written += len(data)
        written += zeros()
        if written:
            context.flush(writer)
        return written
    finally:
        os.close(writer)


def keep_boot(context, part, new_sha256, progress):
    """The BOOT on the phone kept on cache before another takes its place: its path there, or None."""
    import saved_boot
    try:
        kept = saved_boot.keep_current_boot(context.boot_access(part),
                                            lambda phase, target, done: progress(phase, 'boot', done, target['size_bytes']), new_sha256)
    except (OSError, ValueError, fs.FilesystemError):
        return None
    return kept.get('kept')


def mark_boot(context, part):
    """The mark that keeps this recovery, put into a restored Android BOOT (saved_boot.keep_recovery):
    Samsung's Android otherwise puts its own recovery back at its next start. The partition's
    SHA-256 afterwards, or None when the BOOT takes no mark."""
    import saved_boot
    access = context.boot_access(part)
    marked = saved_boot.keep_recovery(access, access.target('boot'))
    return marked['sha256'] if marked else None


def marked_copy(context, part, sha256):
    """Whether BOOT on the phone is the copy's BOOT with the mark that keeps this recovery: then
    nothing of it needs writing, and the mark stays."""
    import saved_boot
    fd = context.open(part)
    try:
        data = bytearray()
        while len(data) < part['size_bytes']:
            piece = os.pread(fd, min(CHUNK, part['size_bytes'] - len(data)), len(data))
            if not piece:
                return False
            data.extend(piece)
    finally:
        os.close(fd)
    found = saved_boot.keep_recovery_spot(bytes(data))
    if not found or not found[1]:
        return False
    data[found[0]:found[0] + len(saved_boot.KEEP_RECOVERY_MARK)] = bytes(len(saved_boot.KEEP_RECOVERY_MARK))
    return hashlib.sha256(data).hexdigest() == sha256


# What a normal start looks at (install.py boot-check): a restored one is noted as written on purpose.
RECORDED = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system')


def restore_archive(context, args, progress):
    """An archive written back (2026-11-11): the whole archive is checked before the first write;
    each partition gets only the pieces that differ and is read back. Recovery and cache are in no
    copy. CHOSEN (--labels) limits the restore to some partitions of the copy."""
    with backup_archive(context, args.volume, args.path) as (fd, manifest, manifest_sha, raw):
        require(manifest_sha == args.expected_manifest_sha256, 'BACKUP_CHANGED', 'The backup changed after it was checked')
        require(manifest.get('device_identity') == context.identity or not SAME_PHONE, 'OTHER_PHONE',
                'This backup belongs to another phone. Restoring it would give this phone a foreign identity')
        current = {p['label']: p for p in context.parts()}
        # Android's data goes back only when it is asked for by name, and only whole.
        wanted = [row for row in manifest['partitions'] if row['label'] not in RETIRED
                  and (row['label'] in CHOSEN if CHOSEN is not None else row['label'] not in OPTIONAL)]
        require(wanted, 'INVALID_ARGUMENT', 'Choose partitions that this copy holds')
        together([row['label'] for row in wanted], [row['label'] for row in manifest['partitions']])
        targets = {}
        for row in wanted:
            part = current.get(row['label'])
            require(part is not None and geometry(part) == {k: row.get(k) for k in geometry(part)}, 'GEOMETRY_MISMATCH',
                    'The partition layout differs from the backup', label=row['label'])
            context.check_offline(part)
            targets[row['label']] = part
        held = locked_rows(context, wanted, current, progress)
        require('vbmeta' not in held or vbmeta_off(context, current), 'VBMETA_IN_DOWNLOAD_MODE',
                "The copy has another vbmeta, the bootloader locks vbmeta while the phone runs, and verification is on: "
                "flash the copy's vbmeta in Download mode first")
        # A locked partition is never opened for writing: one that matches needs none, one that differs stays.
        same = [label for label in targets if label in context.locked() and label not in held]
        for label in context.locked():
            targets.pop(label, None)
        verify_archive(fd, manifest, raw, progress)
        kept = None
        if 'boot' in targets:
            boot_sum = next(r['sha256'] for r in wanted if r['label'] == 'boot')
            # The copy's BOOT is there already, as it is or with the mark that keeps this recovery:
            # nothing of it is written, and no second copy of it goes to cache.
            if read_part(context, targets['boot'], progress) == boot_sum or marked_copy(context, targets['boot'], boot_sum):
                same.append('boot')
                del targets['boot']
            else:
                kept = keep_boot(context, targets['boot'], boot_sum, progress)
        done, written = [], 0
        for row, chunks in archive_rows(fd, manifest, raw, progress, 'Writing'):
            part = targets.get(row['label'])
            if part is None:
                continue
            count = write_changed(context, part, chunks)
            require(read_part(context, part, progress) == row['sha256'], 'READBACK_MISMATCH',
                    'The partition does not match the backup after writing. Restore it again', label=row['label'])
            (done if count else same).append(row['label'])
            written += count
        sums = {row['label']: row['sha256'] for row in wanted}
        marked = None
        if 'boot' in targets:
            with contextlib.suppress(OSError, ValueError, fs.FilesystemError):
                marked = mark_boot(context, targets['boot'])
                sums['boot'] = marked or sums['boot']
        for label in done:
            if label in RECORDED:
                context.recorded(label, sums[label])
        return {'restored': done, 'unchanged': same, 'left_locked': held, 'identical': not done and not held, 'before': None,
                'manifest_sha256': manifest_sha, 'written_bytes': written, 'kept_boot': kept, 'keeps_recovery': bool(marked),
                'archive': True}


def restore(context, args, progress):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Restoring identity partitions must be confirmed')
    with operation_lock(context.lock_path, context.mode):
        if is_archive(args.path):
            return restore_archive(context, args, progress)
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
            marked = None
            if KIND == 'android' and 'boot' in done:
                # 2026-11-11: as Flash a partition does, or Samsung's Android puts its own recovery back.
                with contextlib.suppress(OSError, ValueError, fs.FilesystemError):
                    marked = mark_boot(context, current['boot'])
            return {'restored': done, 'identical': False, 'before': before['path'], 'manifest_sha256': manifest_sha,
                    'keeps_recovery': bool(marked)}


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
    for name in ('verify', 'restore', 'delete'):
        command = commands.add_parser(name)
        command.add_argument('--volume', required=True)
        command.add_argument('--path', required=True)
    for name in ('restore', 'delete'):
        commands.choices[name].add_argument('--expected-manifest-sha256', required=True)
        commands.choices[name].add_argument('--confirm', action='store_true')
    commands.add_parser('backups').add_argument('--volume', required=True)
    commands.add_parser('copies')
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
                  'backups': lambda: backups(context, args), 'delete': lambda: delete(context, args),
                  'copies': lambda: copies(context)}[args.command]()
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, fs.FilesystemError) as error:
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
