#!/usr/bin/env python3
"""Filesystem access for A22x RAM recovery; never a raw-partition writer.

Mounts start read-only. File mutations require a writable admitted volume and
an explicit confirmation. Device mode shares the backup engine's operation
lock. Fixture mode operates only on declared directories, never block devices.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, suppress
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

from operation_lock import operation_lock as recovery_operation_lock

MAX_TEXT = 1024 * 1024
MAX_PAGE = 256
CHUNK = 1024 * 1024
MAX_SYMLINKS = 40
MAX_RESOLVE_PARTS = 256
MAX_TREE_ENTRIES = 10000
MAX_TREE_DEPTH = 64
MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
MOUNT_BASE = Path('/mnt/volumes')
RAM_STATE = Path('/run/a22x-recovery')
BUSYBOX = '/bin/busybox'
BLKROSET, BLKROGET = 0x125d, 0x125e
SUPPORTED_FS = frozenset(('ext4', 'f2fs', 'vfat', 'exfat', 'erofs'))
BACKUP_FILESYSTEMS = frozenset(('ext4', 'exfat'))
# Positive admission happens before opening a partition for a signature probe.
# No bootloader, TEE, modem, calibration or other opaque partition is opened.
FS_LABELS = frozenset(('userdata', 'cache', 'metadata', 'system', 'vendor',
                       'product', 'system_ext', 'odm', 'vendor_dlkm', 'odm_dlkm'))
VERIFIED_LABELS = FS_LABELS - frozenset(('userdata', 'cache', 'metadata'))
RAW_LABELS = frozenset(('boot', 'recovery', 'super', 'vbmeta', 'dtbo'))


# Internal partitions that can be erased, as in TWRP. Everything else stays out of reach.
FORMAT_INTERNAL = frozenset(('userdata', 'cache'))


def medium_identity(disk, usb):
    """Bytes that name the physical medium behind a whole-disk sysfs directory."""
    device = disk / 'device'
    if usb:
        return (device / 'vendor').read_bytes().strip() + b'/' + (device / 'model').read_bytes().strip()
    return (device / 'cid').read_bytes().strip()


def format_admitted(volume, fs_type, external):
    internal = volume.get('id') in FORMAT_INTERNAL
    if not external and not internal:
        fail('FORMAT_FORBIDDEN', 'Only removable storage, userdata and cache can be formatted')
    if volume.get('mounted'):
        fail('VOLUME_MOUNTED', 'Unmount the volume before formatting it')
    if fs_type not in (('ext4', 'f2fs') if internal else ('ext4', 'vfat')):
        fail('UNSUPPORTED_FILESYSTEM', 'This filesystem is not offered for this volume')


# TWRP's "Repair file system": e2fsck for ext2/3/4. The recovery has no fsck for F2FS or FAT.
CHECK_TYPES = frozenset(('ext2', 'ext3', 'ext4'))
E2FSCK = '/sbin/e2fsck'


def check_admitted(volume, external):
    """The volumes a check may repair are the ones that may be formatted, unmounted."""
    if not external and volume.get('id') not in FORMAT_INTERNAL:
        fail('CHECK_FORBIDDEN', 'Only removable storage, userdata and cache can be checked and repaired')
    if volume.get('mounted'):
        fail('VOLUME_MOUNTED', 'Unmount the volume before checking it')
    if volume.get('fs_type') not in CHECK_TYPES:
        fail('UNSUPPORTED_FILESYSTEM', 'This recovery checks and repairs ext2, ext3 and ext4 only')


def run_e2fsck(path, tool=E2FSCK):
    """Force a full check and answer yes to every repair. e2fsck exits 0 for a clean filesystem,
    1 or 2 after repairs, 4 when errors stay and 8 or more when it could not work at all."""
    done = subprocess.run([tool, '-f', '-y', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace')
    rc, output = done.returncode, done.stdout[-600:]
    if rc < 0 or rc >= 8:
        fail('CHECK_FAILED', 'The filesystem could not be checked', exit_code=rc, output=output)
    if rc & 4:
        fail('CHECK_ERRORS_LEFT', 'The check found errors it could not repair', exit_code=rc, output=output)
    return {'result': 'repaired' if rc else 'clean', 'exit_code': rc, 'output': output}


RESIZE2FS = '/usr/sbin/resize2fs'
TUNE2FS = '/usr/sbin/tune2fs'


def ext_bytes(path, tool=TUNE2FS):
    """Bytes the ext2/3/4 filesystem in PATH spans, from its superblock."""
    done = subprocess.run([tool, '-l', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace')
    fields = dict(line.split(':', 1) for line in done.stdout.splitlines() if ':' in line)
    try:
        return int(fields['Block count'].strip()) * int(fields['Block size'].strip())
    except (KeyError, ValueError):
        fail('CHECK_FAILED', 'The filesystem size could not be read', output=done.stdout[-400:])


def run_resize2fs(path, tools=(E2FSCK, RESIZE2FS, TUNE2FS)):
    """TWRP's "Resize File System": check first, then grow ext2/3/4 to fill its partition."""
    e2fsck, resize2fs, tune2fs = tools
    checked = run_e2fsck(path, e2fsck)
    before = ext_bytes(path, tune2fs)
    done = subprocess.run([resize2fs, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace')
    if done.returncode:
        fail('RESIZE_FAILED', 'The filesystem could not be resized', exit_code=done.returncode, output=done.stdout[-600:])
    after = ext_bytes(path, tune2fs)
    return {'result': 'grown' if after > before else 'unchanged', 'check': checked['result'],
            'bytes_before': before, 'bytes_after': after, 'output': done.stdout[-600:]}


class FilesystemError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def fail(code, message, **details):
    raise FilesystemError(code, message, **details)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode()).hexdigest()


def require_hash(value, name='sha256'):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        fail('INVALID_HASH', 'Expected a lowercase SHA-256 value', field=name)


def regular_json(path, maximum=MAX_TEXT):
    fd = open_regular(path)
    try:
        if os.fstat(fd).st_size > maximum:
            fail('INPUT_TOO_LARGE', 'JSON input is too large')
        raw = os.read(fd, maximum + 1)
        if len(raw) > maximum:
            fail('INPUT_TOO_LARGE', 'JSON input is too large')
        return json.loads(raw)
    finally:
        os.close(fd)


def absolute_no_symlinks(path, *, missing=False):
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            st = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(st.st_mode):
            fail('SYMLINK_REJECTED', 'Symlink paths are not admitted', path=str(current))
    return path


def open_regular(path, *, dir_fd=None):
    """Prove type on an O_PATH descriptor before opening data (no FIFO hangs)."""
    handle = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    try:
        original = os.fstat(handle)
        if not stat.S_ISREG(original.st_mode):
            fail('REGULAR_FILE_REQUIRED', 'This operation accepts a real regular file only')
        fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (original.st_dev, original.st_ino):
            os.close(fd)
            fail('FILE_CHANGED', 'File identity changed while opening')
        return fd
    finally:
        os.close(handle)


def stamp(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def hash_fd(fd):
    h, offset = hashlib.sha256(), 0
    while True:
        data = os.pread(fd, CHUNK, offset)
        if not data:
            return h.hexdigest()
        h.update(data)
        offset += len(data)


def mount_unescape(text):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)


def parse_mountinfo(text):
    result = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        if not sep or len(a) < 6 or len(b) < 3:
            fail('INVALID_MOUNTINFO', 'Cannot establish the active mount topology')
        result.append({'mount_id': int(a[0]), 'parent_id': int(a[1]), 'devno': a[2],
                       'root': mount_unescape(a[3]), 'mountpoint': mount_unescape(a[4]),
                       'options': a[5].split(','), 'fs_type': b[0],
                       'source': mount_unescape(b[1])})
    return result


def read_mounts():
    return parse_mountinfo(Path('/proc/self/mountinfo').read_text())


def all_namespace_mounts():
    """Admission checks must also see an offline filesystem mounted elsewhere."""
    result, seen = [], set()
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            namespace = os.readlink(path / 'ns/mnt')
            if namespace in seen:
                continue
            records = (path / 'mountinfo').read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            fail('MOUNT_NAMESPACE_UNREADABLE', 'Cannot prove that the filesystem is unmounted')
        seen.add(namespace)
        result.extend(parse_mountinfo(records))
    return result


def reason_text(code):
    return {
        'LOGICAL_PARTITION_CONTAINER': 'Android logical-partition container; mapping is not implemented',
        'RAW_IMAGE_NOT_FILESYSTEM': 'Raw image; not a filesystem',
        'PROTECTED_FIRMWARE_OR_SERVICE_PARTITION': 'Firmware or service partition; file access is not admitted',
        'PARTITION_HAS_BLOCK_HOLDERS': 'This partition is already used by another block mapping',
        'ENCRYPTED_OR_UNRECOGNIZED_USERDATA': 'Filesystem unavailable; Android data may require decryption',
        'ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM': 'Encrypted or unsupported filesystem',
        'FILESYSTEM_DRIVER_UNAVAILABLE': 'This kernel has no driver for the detected filesystem',
        'VOLUME_MOUNTED_OUTSIDE_MANAGER': 'Mounted outside the recovery filesystem manager',
        'READ_ONLY_FILESYSTEM': 'Read-only filesystem',
        'FSCRYPT_KEYS_UNAVAILABLE': 'Filesystem encryption is enabled; file keys have not been unlocked',
        'VERIFIED_ANDROID_FILESYSTEM_RW_NOT_QUALIFIED': 'Android verified filesystem; writes are not qualified',
        'BLOCK_DEVICE_READ_ONLY': 'The block device was already read-only; this helper will not override it',
        'READ_ONLY_GUARD_LOST': 'The block-level read-only guard changed; remount is required',
    }.get(code, code)


def fd_mount_id(fd):
    text = Path('/proc/self/fdinfo/' + str(fd)).read_text()
    match = re.search(r'^mnt_id:\s*(\d+)$', text, re.M)
    if not match:
        fail('MOUNT_ID_UNAVAILABLE', 'The opened descriptor has no kernel mount identity')
    return int(match[1])


def uuid_text(raw):
    value = raw.hex()
    return '-'.join((value[:8], value[8:12], value[12:16], value[16:20], value[20:]))


def probe_magic(data):
    """Bounded read-only superblock detection; no blkid executable is required.

    Offsets follow ext4_super_block, f2fs_super_block, erofs_super_block and the
    FAT/exFAT on-disk boot sectors. Unknown signatures are never guessed ext4.
    """
    out = {'fs_type': None, 'filesystem_uuid': None, 'filesystem_label': '',
           'fscrypt_feature': None, 'encrypted_container': False,
           'probe': 'bounded-superblock-v1'}
    if data.startswith(b'LUKS\xba\xbe'):
        out.update(fs_type='crypto_LUKS', encrypted_container=True)
        return out
    if len(data) >= 2048 and data[1080:1082] == b'\x53\xef':
        sb = data[1024:2048]
        log_size = struct.unpack_from('<I', sb, 24)[0]
        incompat = struct.unpack_from('<I', sb, 96)[0]
        if log_size <= 6 and struct.unpack_from('<I', sb, 32)[0] != 0:
            out.update(fs_type='ext4', filesystem_uuid=uuid_text(sb[104:120]),
                       filesystem_label=sb[120:136].split(b'\0', 1)[0].decode('utf-8', 'replace'),
                       fscrypt_feature=bool(incompat & 0x10000))
            return out
    for offset in (1024, 5120):
        if len(data) >= offset + 128 and data[offset:offset + 4] == b'\x10\x20\xf5\xf2':
            sb = data[offset:]
            if struct.unpack_from('<I', sb, 16)[0] == 12:  # log_blocksize
                out.update(fs_type='f2fs', filesystem_uuid=uuid_text(sb[108:124]))
                if len(sb) >= 2184:
                    features = struct.unpack_from('<I', sb, 2180)[0]
                    out.update(fscrypt_feature=bool(features & 1),
                               compression_feature=bool(features & 0x2000))
                # The kernel may expose only unencrypted files. This is not a key unlock.
                return out
    if len(data) >= 1152 and data[1024:1028] == b'\xe2\xe1\xf5\xe0':
        sb = data[1024:1152]
        if 9 <= sb[12] <= 16:
            out.update(fs_type='erofs', filesystem_uuid=uuid_text(sb[48:64]),
                       filesystem_label=sb[64:80].split(b'\0', 1)[0].decode('utf-8', 'replace'))
            return out
    if len(data) >= 512 and data[510:512] == b'\x55\xaa':
        if data[3:11] == b'EXFAT   ' and 9 <= data[108] <= 12 and data[110] in (1, 2):
            out.update(fs_type='exfat', filesystem_uuid=f'{struct.unpack_from("<I", data, 100)[0]:08x}')
            return out
        sector = struct.unpack_from('<H', data, 11)[0]
        cluster = data[13]
        if (sector in (512, 1024, 2048, 4096) and cluster in (1, 2, 4, 8, 16, 32, 64, 128)
                and data[16] in (1, 2)):
            if data[82:90] == b'FAT32   ':
                off = 67
            elif data[54:62] in (b'FAT12   ', b'FAT16   '):
                off = 39
            else:
                return out
            out.update(fs_type='vfat', filesystem_uuid=f'{struct.unpack_from("<I", data, off)[0]:08x}')
    return out


# admin_session.py marks each SSH or SFTP session that holds the recovery's lease (2026-11-10).
SSH_SESSIONS = Path('/run/a22x-recovery/ssh')


def ssh_session_active():
    """Whether a computer's SSH or SFTP session holds the lease now: then storage is busy because
    of that session, which the person can close, not because of USB files or an operation."""
    try:
        markers = list(SSH_SESSIONS.glob('session-*.json'))
    except OSError:
        return False
    for marker in markers:
        try:
            pid = int(json.loads(marker.read_text())['pid'])
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return False


def mount_options(fs_type, mode):
    if fs_type not in SUPPORTED_FS or mode not in ('ro', 'rw'):
        fail('UNSUPPORTED_FILESYSTEM', 'The filesystem is not admitted for mounting')
    if fs_type == 'erofs' and mode != 'ro':
        fail('READ_ONLY_FILESYSTEM', 'EROFS is a read-only filesystem')
    options = [mode, 'nodev', 'nosuid', 'noexec']
    if mode == 'ro' and fs_type == 'ext4':
        options.append('noload')
    if mode == 'ro' and fs_type == 'f2fs':
        options.append('norecovery')
    return ','.join(options)


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', value):
        fail('INVALID_VOLUME', 'Invalid volume identifier')
    return value


def path_parts(path):
    if not isinstance(path, str) or '\0' in path or len(os.fsencode(path)) > 4096:
        fail('INVALID_PATH', 'Invalid or excessively long volume-relative path')
    if path in ('', '/'):
        return []
    parts = path.lstrip('/').split('/')
    if any(part in ('', '.', '..') or len(os.fsencode(part)) > 255 for part in parts):
        fail('INVALID_PATH', 'Use a volume-relative path without dot or parent components')
    return parts


def clean_path(path):
    return '/' + '/'.join(path_parts(path))


def atomic_state(path, value):
    path = absolute_no_symlinks(path, missing=True)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.parent / ('.' + path.name + '.' + os.urandom(8).hex())
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        data = (json.dumps(value, ensure_ascii=True, sort_keys=True) + '\n').encode()
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
        os.fsync(fd)
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


class BaseContext:
    def volume(self, volume_id, expected=None):
        safe_id(volume_id)
        candidates = [v for v in self.volumes() if v['id'] == volume_id]
        if len(candidates) != 1:
            fail('VOLUME_UNAVAILABLE', 'The selected volume is not uniquely available', volume=volume_id)
        volume = candidates[0]
        if expected is not None:
            require_hash(expected, 'expected-volume-token')
            if volume['volume_token'] != expected:
                fail('VOLUME_CHANGED', 'The selected device or filesystem changed', volume=volume_id)
        return volume

    @contextmanager
    def operation_lock(self):
        path = absolute_no_symlinks(self.lock_path, missing=True)
        with ExitStack() as stack:
            try:
                # Kept, so that a helper can lend the lease to a step it runs itself (distro.py: BOOT).
                self.lock_fd = stack.enter_context(recovery_operation_lock(path, self.mode))
            except BlockingIOError:
                if ssh_session_active():
                    fail('OPERATION_BUSY', 'A computer is connected over SSH or SFTP: close that session, then try again')
                fail('OPERATION_BUSY', 'Storage is busy: stop USB files, or wait for the running operation to finish')
            except OSError as exc:
                fail('INVALID_LOCK', 'The recovery operation lock is unavailable or changed', errno=exc.errno)
            yield

    def inventory(self):
        return {'volumes': self.volumes(), 'crypto': {
            'android_decryption': False, 'credential_unlock': False,
            'teegris_transport_qualified': False, 'fscrypt_key_installation': False,
            'note': 'A recognizable filesystem or a successful mount does not imply Android data is decrypted.'},
            'capabilities': {'mount': True, 'read_files': True, 'write_files': True,
                             'raw_partition_restore': False, 'logical_super_mapping': False,
                             'copy_directories': True, 'recursive_delete': False,
                             'file_metadata': True, 'chmod': True, 'chown': True,
                             'resolve_symlinks': True}}

    def confirm(self, confirmed):
        if not confirmed:
            fail('CONFIRMATION_REQUIRED', 'This change requires an explicit local confirmation')

    def usb_volumes(self):
        return [{key: volume[key] for key in ('id', 'fs_type', 'mountpoint', 'read_only')} | {'size_bytes': volume.get('size_bytes', 0)}
                for volume in self.volumes() if volume['id'].startswith('usb-') and volume['mounted']]

    def prepare_usb_backup(self, ident, confirmed=False):
        """Open a stick for writing and make its backup folder."""
        volume = self.volume(ident)
        if not ident.startswith('usb-'):
            fail('NOT_USB_VOLUME', 'Only a USB stick can be prepared here')
        if volume['fs_type'] not in BACKUP_FILESYSTEMS:
            fail('BACKUP_FILESYSTEM_UNSUITED', 'Backups need ext4 or exFAT. FAT32 cannot hold files over 4 GiB')
        self.mount(ident, 'rw', confirmed, volume['volume_token'])
        folder = Path(self.volume(ident)['mountpoint']) / 'A22xBackups'
        folder.mkdir(mode=0o700, exist_ok=True)
        return {'repository': str(folder)}

    def eject_usb(self):
        """Unmount every USB volume, so the stick can be pulled out."""
        os.sync()
        for volume in self.usb_volumes():
            self.unmount(volume['id'])
        return {'volumes': self.usb_volumes()}


class FixtureContext(BaseContext):
    mode = 'fixture'

    def __init__(self, root):
        self.root = absolute_no_symlinks(root)
        if not self.root.is_dir():
            fail('INVALID_FIXTURE', 'Fixture root must be a real directory')
        self.config_path = self.root / 'filesystem-fixture.json'
        self.state_path = self.root / '.filesystem-state.json'
        self.lock_path = self.root / '.operation.lock'
        self.config()

    def config(self):
        config = regular_json(self.config_path)
        if config.get('schema') != 'a22x.filesystem.fixture.v1' or not isinstance(config.get('volumes'), list):
            fail('INVALID_FIXTURE', 'Expected a22x.filesystem.fixture.v1')
        return config

    def state(self):
        return regular_json(self.state_path) if self.state_path.exists() else {}

    def volumes(self):
        config, state = self.config(), self.state()
        result, seen = [], set()
        for spec in config['volumes']:
            ident = safe_id(spec['id'])
            if ident in seen:
                fail('INVALID_FIXTURE', 'Duplicate volume identifier')
            seen.add(ident)
            path = Path(spec['path'])
            path = absolute_no_symlinks(path if path.is_absolute() else self.root / path)
            if self.root not in path.parents or not path.is_dir():
                fail('INVALID_FIXTURE', 'Every fixture volume must be a directory beneath its fixture root')
            st = path.stat()
            # A formatted volume holds what it was formatted with (2026-11-11: a userdata of Android
            # that a clean installation formats for Linux), and nothing encrypted any more.
            formatted = state.get(ident, {}).get('formatted')
            filesystem = formatted or spec.get('fs_type', 'ext4')
            token = digest({'fixture': str(self.root), 'id': ident, 'path': str(path),
                            'dev': st.st_dev, 'inode': st.st_ino, 'fs': filesystem})
            mounted = bool(state.get(ident, {}).get('mounted', spec.get('mounted', False)))
            readonly = bool(state.get(ident, {}).get('read_only', spec.get('read_only', True)))
            fscrypt = bool(spec.get('fscrypt_feature', False)) and not formatted
            mountable = filesystem in SUPPORTED_FS
            result.append({'id': ident, 'label': spec.get('label', ident), 'fs_type': filesystem,
                           'mountable': mountable, 'mounted': mounted, 'read_only': readonly,
                           'mountpoint': str(path), 'path': str(path), 'mode': 'fixture',
                           'volume_token': token, 'mount_id': 'fixture:' + token,
                           'mount_owner': 'filesystem', 'fscrypt_feature': fscrypt,
                           'write_supported': mountable and filesystem != 'erofs' and not fscrypt,
                           'reason': None if mountable else reason_text('ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM'),
                           'reason_code': None if mountable else 'ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM',
                           'read_only_reason': 'FSCRYPT_KEYS_UNAVAILABLE' if fscrypt else
                                               ('READ_ONLY_FILESYSTEM' if filesystem == 'erofs' else None)})
        return result

    def mount(self, ident, mode, confirmed=False, expected=None):
        volume = self.volume(ident, expected)
        if not volume['mountable']:
            fail('VOLUME_NOT_MOUNTABLE', volume['reason'])
        if mode == 'rw':
            self.confirm(confirmed)
            if not volume['write_supported']:
                fail('READ_ONLY_FILESYSTEM', 'This volume is not admitted for writes')
        mount_options(volume['fs_type'], mode)
        state = self.state()
        state[ident] = {**self.kept(state, ident), 'mounted': True, 'read_only': mode == 'ro'}
        atomic_state(self.state_path, state)
        return {'volume': self.volume(ident)}

    def format(self, ident, fs_type, confirmed=False, expected=None):
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        format_admitted(volume, fs_type, ident.startswith(('sdcard', 'usb-')))
        state = self.state()
        state[ident] = {**state.get(ident, {}), 'formatted': fs_type}
        atomic_state(self.state_path, state)
        return {'volume': self.volume(ident), 'formatted': fs_type}

    def resize(self, ident, confirmed=False, expected=None):
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        check_admitted(volume, ident.startswith(('sdcard', 'usb-')))
        return {'volume': self.volume(ident), 'checked': volume['fs_type'], 'result': 'unchanged', 'check': 'clean',
                'bytes_before': 0, 'bytes_after': 0, 'output': ''}

    def check(self, ident, confirmed=False, expected=None):
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        check_admitted(volume, ident.startswith(('sdcard', 'usb-')))
        state = self.state()
        state[ident] = {**state.get(ident, {}), 'checked': volume['fs_type']}
        atomic_state(self.state_path, state)
        return {'volume': self.volume(ident), 'checked': volume['fs_type'], 'result': 'clean', 'exit_code': 0, 'output': ''}

    @staticmethod
    def kept(state, ident):
        """What a mount or an unmount leaves of a volume's state: the filesystem it was formatted with."""
        return {key: value for key, value in state.get(ident, {}).items() if key == 'formatted'}

    def unmount(self, ident, expected=None):
        self.volume(ident, expected)
        state = self.state()
        state[ident] = {**self.kept(state, ident), 'mounted': False, 'read_only': True}
        atomic_state(self.state_path, state)
        return {'volume': self.volume(ident)}

    def unmount_all(self):
        unmounted = []
        for volume in self.volumes():
            if volume['mounted']:
                self.unmount(volume['id'])
                unmounted.append(volume['id'])
        return {'unmounted': unmounted}

    def nested_mounts(self, volume, relative):
        return []

    def sync_usb(self):
        return {'mounted': [], 'dropped': [], 'volumes': self.usb_volumes()}


class DeviceContext(BaseContext):
    mode = 'device'

    def __init__(self):
        if os.geteuid() != 0:
            fail('ROOT_REQUIRED', 'Filesystem operations require the RAM recovery root user')
        compatible = Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0')
        if b'samsung,a22x' not in compatible or b'mediatek,mt6833' not in compatible:
            fail('WRONG_DEVICE', 'The live device tree must identify SM-A226B / MT6833')
        if 'a22x.recovery=1' not in Path('/proc/cmdline').read_text().split():
            fail('DEDICATED_RECOVERY_REQUIRED', 'This helper runs only in the dedicated RAM recovery')
        marker = regular_json(RAM_STATE / 'boot.json')
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if marker.get('schema') != 'a22x.recovery.boot.v1' or marker.get('boot_id') != self.boot_id:
            fail('RECOVERY_BOOT_MISMATCH', 'The recovery receipt does not describe this boot')
        roots = [m for m in read_mounts() if m['mountpoint'] == '/']
        if len(roots) != 1 or roots[0]['fs_type'] not in ('rootfs', 'ramfs', 'tmpfs'):
            fail('RAM_ROOT_REQUIRED', 'Internal filesystems may be managed only from RAM root')
        self.state_path = RAM_STATE / 'filesystems.json'
        self.lock_path = Path('/run/lock/a22x-mainline-recovery.lock')

    def state(self):
        if not self.state_path.exists():
            return {'schema': 'a22x.filesystem.mounts.v1', 'boot_id': self.boot_id, 'volumes': {}}
        state = regular_json(self.state_path)
        if state.get('schema') != 'a22x.filesystem.mounts.v1' or state.get('boot_id') != self.boot_id:
            fail('MOUNT_STATE_MISMATCH', 'Mount state does not describe this recovery boot')
        return state

    def volumes(self):
        result, state, mounts = [], self.state(), read_mounts()
        supported = {line.split()[-1] for line in Path('/proc/filesystems').read_text().splitlines() if line.split()}
        for entry in sorted(Path('/sys/class/block').iterdir()):
            usb_name = re.fullmatch(r'sd[a-z]{1,2}[0-9]*', entry.name) is not None
            if not usb_name and not re.fullmatch(r'mmcblk[01]p[0-9]+', entry.name):
                continue
            real = entry.resolve()
            # A USB stick can hold a filesystem on the whole disk, without a partition table.
            whole_disk = usb_name and not (real / 'partition').is_file()
            if whole_disk and any(child.name.startswith(entry.name) for child in real.iterdir()):
                continue
            if not usb_name and not (real / 'partition').is_file():
                continue
            parent = real if whole_disk else real.parent
            usb = usb_name and '/usb' in str(real)
            if usb_name and not usb:
                continue
            try:
                media = 'USB' if usb else (parent / 'device/type').read_text().strip()
                cid = medium_identity(parent, usb)
            except OSError:
                continue
            external = usb or (parent.name == 'mmcblk1' and media == 'SD')
            internal = parent.name == 'mmcblk0' and media == 'MMC'
            if not (external or internal):
                continue
            fields = dict(line.split('=', 1) for line in (real / 'uevent').read_text().splitlines() if '=' in line)
            number = 0 if whole_disk else int((real / 'partition').read_text())
            label = fields.get('PARTNAME', '')
            ident = 'usb-p' + str(number) if usb else 'sdcard-p' + str(number) if external else (label if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', label) else 'partition-' + str(number))
            devno = (real / 'dev').read_text().strip()
            size = int((real / 'size').read_text()) * 512
            start = 0 if whole_disk else int((real / 'start').read_text())
            path = Path('/dev') / entry.name
            st = path.lstat()
            if not stat.S_ISBLK(st.st_mode) or devno != f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}':
                fail('BLOCK_IDENTITY_CHANGED', 'A block node does not match kernel-owned sysfs', volume=ident)
            candidate = external or label in FS_LABELS
            info = {'fs_type': None, 'filesystem_uuid': None, 'fscrypt_feature': None}
            block_read_only = None
            if candidate:
                fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                try:
                    actual = os.fstat(fd)
                    if actual.st_rdev != st.st_rdev or not stat.S_ISBLK(actual.st_mode):
                        fail('BLOCK_IDENTITY_CHANGED', 'Block identity changed during probe', volume=ident)
                    if struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] != size:
                        fail('BLOCK_GEOMETRY_CHANGED', 'Kernel and block geometry disagree', volume=ident)
                    block_read_only = bool(struct.unpack('i', fcntl.ioctl(fd, BLKROGET, bytes(4)))[0])
                    info = probe_magic(os.pread(fd, min(8192, size), 0))
                finally:
                    os.close(fd)
            holders = list((real / 'holders').iterdir()) if (real / 'holders').is_dir() else []
            fs = info['fs_type']
            reason = None
            if not candidate:
                reason = 'LOGICAL_PARTITION_CONTAINER' if label == 'super' else ('RAW_IMAGE_NOT_FILESYSTEM' if label in RAW_LABELS else 'PROTECTED_FIRMWARE_OR_SERVICE_PARTITION')
            elif holders:
                reason = 'PARTITION_HAS_BLOCK_HOLDERS'
            elif fs not in SUPPORTED_FS:
                reason = 'ENCRYPTED_OR_UNRECOGNIZED_USERDATA' if label == 'userdata' else 'ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM'
            elif fs not in supported:
                reason = 'FILESYSTEM_DRIVER_UNAVAILABLE'
            token = digest({'id': ident, 'path': str(path), 'devno': devno, 'start': start, 'bytes': size,
                            'cid_hash': hashlib.sha256(cid).hexdigest(), 'filesystem': fs,
                            'filesystem_uuid': info.get('filesystem_uuid')})
            target = str(MOUNT_BASE / ident)
            current = [m for m in mounts if m['devno'] == devno]
            record = state['volumes'].get(ident, {})
            own_ro_guard = (record.get('volume_token') == token and record.get('block_ro_guard')
                            and record.get('original_block_ro') is False)
            owner = None
            readonly, mounted, mount_id = True, bool(current), None
            if current:
                if len(current) == 1 and current[0]['root'] == '/':
                    mount = current[0]
                    target, mount_id = mount['mountpoint'], mount['mount_id']
                    readonly = 'ro' in mount['options']
                    if external and target == '/mnt/backup':
                        owner = 'storage'
                    elif (target == str(MOUNT_BASE / ident) and record.get('volume_token') == token
                          and record.get('mount_id') == mount_id):
                        owner = 'filesystem'
                if owner is None:
                    reason = 'VOLUME_MOUNTED_OUTSIDE_MANAGER'
                elif owner == 'filesystem' and readonly and record.get('block_ro_guard') and not block_read_only:
                    reason = 'READ_ONLY_GUARD_LOST'
            write_reason = ('BLOCK_DEVICE_READ_ONLY' if block_read_only and not own_ro_guard else
                            ('READ_ONLY_FILESYSTEM' if fs == 'erofs' else
                            ('FSCRYPT_KEYS_UNAVAILABLE' if info.get('fscrypt_feature') else
                             ('VERIFIED_ANDROID_FILESYSTEM_RW_NOT_QUALIFIED' if label in VERIFIED_LABELS else None))))
            volume = {'id': ident, 'label': label or ident, 'path': str(path), 'device': devno,
                      'device_identity': hashlib.sha256(cid).hexdigest(),
                      'partition_number': number, 'size_bytes': size, 'external': external,
                      'mountable': reason is None, 'reason': reason_text(reason), 'reason_code': reason,
                      'mounted': mounted, 'block_read_only': block_read_only,
                      'read_only_guarded': bool(block_read_only),
                      'read_only': readonly, 'mountpoint': target, 'mount_id': mount_id,
                      'mount_owner': owner, 'volume_token': token, 'mode': 'device',
                      'write_supported': reason is None and write_reason is None,
                      'read_only_reason': reason_text(write_reason), 'read_only_reason_code': write_reason, **info}
            result.append(volume)
        if len({v['id'] for v in result}) != len(result):
            fail('DUPLICATE_VOLUME_ID', 'GPT volume identifiers are not unique')
        return result

    def _block_ro(self, volume, value=None):
        """Change only the partition's kernel read-only flag, never its contents."""
        fd = os.open(volume['path'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISBLK(st.st_mode) or f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}' != volume['device']:
                fail('BLOCK_IDENTITY_CHANGED', 'The partition changed before the read-only guard')
            if struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] != volume['size_bytes']:
                fail('BLOCK_GEOMETRY_CHANGED', 'The partition size changed before the read-only guard')
            sysfs = (Path('/sys/dev/block') / volume['device']).resolve()
            # A stick without a partition table is its own whole disk.
            disk = sysfs.parent if (sysfs / 'partition').is_file() else sysfs
            cid = medium_identity(disk, '/usb' in str(sysfs))
            if hashlib.sha256(cid).hexdigest() != volume['device_identity']:
                fail('BLOCK_IDENTITY_CHANGED', 'The physical medium changed before the read-only guard')
            observed_fs = probe_magic(os.pread(fd, min(8192, volume['size_bytes']), 0))
            if (observed_fs['fs_type'], observed_fs['filesystem_uuid']) != (volume['fs_type'], volume['filesystem_uuid']):
                fail('BLOCK_IDENTITY_CHANGED', 'The filesystem identity changed before the read-only guard')
            holders = sysfs / 'holders'
            if holders.is_dir() and any(holders.iterdir()):
                fail('BLOCK_HAS_HOLDERS', 'Another block mapping owns this partition; the guard is retained')
            if value is not None:
                fcntl.ioctl(fd, BLKROSET, struct.pack('i', int(value)))
            observed = bool(struct.unpack('i', fcntl.ioctl(fd, BLKROGET, bytes(4)))[0])
            if value is not None and observed != bool(value):
                fail('BLOCK_RO_GUARD_FAILED', 'The kernel read-only flag did not match the requested state')
            return observed
        finally:
            os.close(fd)

    def _arm_read_only(self, volume):
        if any(m['devno'] == volume['device'] for m in all_namespace_mounts()):
            fail('VOLUME_MOUNTED_ELSEWHERE', 'A filesystem must be unmounted before setting its read-only guard')
        original = self._block_ro(volume)
        state = self.state()
        state['volumes'][volume['id']] = {
            'volume_token': volume['volume_token'], 'mount_id': None,
            'block_ro_guard': True, 'original_block_ro': original, 'guard_stage': 'arming'}
        # Record ownership before the ioctl so a failed mount never loses its restore receipt.
        atomic_state(self.state_path, state)
        if not original:
            self._block_ro(volume, True)
        if not self._block_ro(volume):
            fail('BLOCK_RO_GUARD_FAILED', 'The partition is not read-only')
        state['volumes'][volume['id']]['guard_stage'] = 'armed'
        atomic_state(self.state_path, state)

    def _release_read_only(self, volume):
        state = self.state()
        record = state['volumes'].get(volume['id'], {})
        if not record.get('block_ro_guard'):
            return
        if record.get('volume_token') != volume['volume_token']:
            fail('BLOCK_RO_IDENTITY_MISMATCH', 'The read-only guard belongs to another filesystem identity')
        if any(m['devno'] == volume['device'] for m in all_namespace_mounts()):
            fail('READ_ONLY_GUARD_STILL_MOUNTED', 'The partition remains mounted; its read-only guard is retained')
        if record.get('original_block_ro') is False:
            # Never clear a flag that was already set before this helper acquired it.
            self._block_ro(volume, False)
        elif record.get('original_block_ro') is not True:
            fail('INVALID_BLOCK_RO_RECEIPT', 'The original read-only state is not known')
        state['volumes'].pop(volume['id'], None)
        atomic_state(self.state_path, state)

    def _command(self, action, arguments):
        proc = subprocess.run([BUSYBOX, action, *arguments], capture_output=True, text=True, timeout=30)
        if proc.returncode:
            fail('MOUNT_FAILED' if action == 'mount' else 'UNMOUNT_FAILED',
                 'The kernel refused the filesystem operation', detail=proc.stderr.strip()[:1024],
                 exit_code=proc.returncode)

    def mount(self, ident, mode, confirmed=False, expected=None):
        volume = self.volume(ident, expected)
        if not volume['mountable']:
            fail('VOLUME_NOT_MOUNTABLE', 'This partition cannot be mounted', reason=volume['reason'])
        if mode == 'rw':
            self.confirm(confirmed)
            if not volume['write_supported']:
                fail('WRITE_NOT_QUALIFIED', 'This filesystem is admitted only for reading', reason=volume['read_only_reason'])
        options = mount_options(volume['fs_type'], mode)
        if (volume['mounted'] and volume['read_only'] == (mode == 'ro')
                and (mode == 'rw' or volume['read_only_guarded'])):
            return {'volume': volume}
        if volume['mounted']:
            if volume['mount_owner'] != 'filesystem':
                fail('MOUNT_OWNED_BY_STORAGE_HELPER', 'Unmount the Storage volume before changing its mode')
            self.unmount(ident, volume['volume_token'])
            volume = self.volume(ident, volume['volume_token'])
        elif self.state()['volumes'].get(ident, {}).get('block_ro_guard'):
            # A previous failed mount may have safely left its partition read-only.
            self._release_read_only(volume)
            volume = self.volume(ident, volume['volume_token'])
        if any(m['devno'] == volume['device'] for m in all_namespace_mounts()):
            fail('VOLUME_MOUNTED_ELSEWHERE', 'The partition is mounted in another namespace')
        target = MOUNT_BASE / ident
        absolute_no_symlinks(target, missing=True)
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(target.iterdir()) or any(m['mountpoint'] == str(target) for m in read_mounts()):
            fail('MOUNTPOINT_OCCUPIED', 'The dedicated volume mountpoint is not empty')
        if mode == 'ro':
            self._arm_read_only(volume)
        elif self._block_ro(volume):
            fail('BLOCK_DEVICE_READ_ONLY', 'An existing read-only block flag will not be overridden')
        # Opening a known /dev node rather than an arbitrary argument prevents path injection.
        try:
            self._command('mount', ['-t', volume['fs_type'], '-o', options, volume['path'], str(target)])
        except BaseException:
            if not any(m['devno'] == volume['device'] for m in all_namespace_mounts()):
                self._release_read_only(volume)
            raise
        current = [m for m in read_mounts() if m['mountpoint'] == str(target)]
        if (len(current) != 1 or current[0]['devno'] != volume['device'] or current[0]['root'] != '/'
                or ('ro' in current[0]['options']) != (mode == 'ro')):
            fail('MOUNT_IDENTITY_MISMATCH', 'The observed mount does not match the requested volume')
        state = self.state()
        record = state['volumes'].setdefault(ident, {})
        record.update(volume_token=volume['volume_token'], mount_id=current[0]['mount_id'])
        atomic_state(self.state_path, state)
        result = self.volume(ident, volume['volume_token'])
        if result['mount_owner'] != 'filesystem':
            fail('MOUNT_STATE_MISMATCH', 'The new mount was not admitted')
        return {'volume': result, 'mount_options': options}

    def format(self, ident, fs_type, confirmed=False, expected=None):
        """Erase one removable card or stick. Internal storage is never admitted."""
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        format_admitted(volume, fs_type, volume.get('external') is True)
        label = 'userdata' if ident == 'userdata' else ('cache' if ident == 'cache' else 'A22X')
        tool = {'ext4': ['/sbin/mkfs.ext4', '-F', '-q', '-L', label],
                'f2fs': ['/bin/make_f2fs', '-g', 'android', '-f', '-O', 'project_quota,extra_attr', '-O', 'casefold',
                         '-C', 'utf8', '-O', 'compression', '-O', 'extra_attr'],
                'vfat': ['/bin/busybox', 'mkdosfs', '-F', '32', '-n', 'A22X']}[fs_type]
        done = subprocess.run([*tool, volume['path']], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors='replace')
        if done.returncode:
            fail('FORMAT_FAILED', 'The filesystem could not be created', output=done.stdout[-400:])
        os.sync()
        return {'volume': self.volume(ident), 'formatted': fs_type}

    def resize(self, ident, confirmed=False, expected=None):
        """Grow one unmounted ext2/3/4 volume to its whole partition, as TWRP's "Resize File System"."""
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        check_admitted(volume, volume.get('external') is True)
        result = run_resize2fs(volume['path'])
        os.sync()
        return {'volume': self.volume(ident), 'checked': volume['fs_type'], **result}

    def check(self, ident, confirmed=False, expected=None):
        """Check and repair one unmounted ext2/3/4 volume, as TWRP's "Repair file system"."""
        self.confirm(confirmed)
        volume = self.volume(ident, expected)
        check_admitted(volume, volume.get('external') is True)
        result = run_e2fsck(volume['path'])
        os.sync()
        return {'volume': self.volume(ident), 'checked': volume['fs_type'], **result}

    def unmount(self, ident, expected=None):
        volume = self.volume(ident, expected)
        if not volume['mounted']:
            self._release_read_only(volume)
            return {'volume': self.volume(ident, volume['volume_token'])}
        if volume['mount_owner'] not in ('filesystem', 'storage'):
            fail('UNMANAGED_MOUNT', 'This helper will not unmount an unrelated filesystem')
        if self.nested_mounts(volume, '/'):
            fail('NESTED_MOUNT', 'Nested mounts must be removed before unmounting this volume')
        # Normal unmount flushes filesystem state and fails while busy; never lazy or forced.
        self._command('umount', [volume['mountpoint']])
        self._release_read_only(volume)
        state = self.state()
        state['volumes'].pop(ident, None)
        atomic_state(self.state_path, state)
        result = self.volume(ident, volume['volume_token'])
        if result['mounted']:
            fail('UNMOUNT_NOT_OBSERVED', 'The volume is still mounted')
        return {'volume': result}

    def sync_usb(self):
        """Follow removable USB storage: drop vanished sticks, open new ones read-only."""
        state, dropped, mounted = self.state(), [], []
        for mount in read_mounts():
            target = Path(mount['mountpoint'])
            if (target.parent == MOUNT_BASE and target.name.startswith('usb-')
                    and not (Path('/sys/dev/block') / mount['devno']).exists()):
                # The stick left without an unmount, so nothing more can reach it.
                self._command('umount', ['-l', str(target)])
                state['volumes'].pop(target.name, None)
                dropped.append(target.name)
        if dropped:
            atomic_state(self.state_path, state)
        for volume in self.volumes():
            if volume['id'].startswith('usb-') and volume['mountable'] and not volume['mounted']:
                try:
                    self.mount(volume['id'], 'ro', False, volume['volume_token'])
                except FilesystemError:
                    continue
                mounted.append({'id': volume['id'], 'size_bytes': volume['size_bytes']})
        return {'mounted': mounted, 'dropped': dropped, 'volumes': self.usb_volumes()}

    def unmount_all(self):
        result = []
        for volume in self.volumes():
            if volume['mounted'] and volume['mount_owner'] == 'filesystem':
                self.unmount(volume['id'], volume['volume_token'])
                result.append(volume['id'])
            elif not volume['mounted'] and self.state()['volumes'].get(volume['id'], {}).get('block_ro_guard'):
                self._release_read_only(volume)
        remaining = [m for m in read_mounts() if m['mountpoint'].startswith(str(MOUNT_BASE) + '/')]
        if remaining:
            fail('UNMANAGED_MOUNTS_REMAIN', 'A volume mount remains; refusing to claim a clean unmount')
        return {'unmounted': result}

    def nested_mounts(self, volume, relative):
        base = volume['mountpoint'].rstrip('/') + clean_path(relative).rstrip('/')
        return [m for m in read_mounts() if m['mountpoint'].startswith(base + '/')]


class PinnedVolume:
    """Pin an admitted mount and walk with openat/O_NOFOLLOW, never resolve symlinks."""
    def __init__(self, context, ident, expected=None, *, writing=False):
        self.context = context
        self.volume = context.volume(ident, expected)
        v = self.volume
        if not v['mounted'] or not v['mountable'] or v['mount_owner'] not in ('filesystem', 'storage'):
            fail('VOLUME_NOT_MOUNTED', 'Mount the selected volume before browsing files', volume=ident)
        if writing and (v['read_only'] or not v['write_supported']):
            fail('VOLUME_READ_ONLY', 'Enable writes on this volume before changing files', volume=ident)
        self.fd = os.open(absolute_no_symlinks(v['mountpoint']), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self.stat = os.fstat(self.fd)
            self.mount_id = fd_mount_id(self.fd)
            if context.mode == 'device' and (self.mount_id != v['mount_id'] or f'{os.major(self.stat.st_dev)}:{os.minor(self.stat.st_dev)}' != v['device']):
                fail('MOUNT_IDENTITY_MISMATCH', 'The open directory does not belong to the admitted mount')
            self.writing = writing
            self.recheck()
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def recheck(self):
        fresh = self.context.volume(self.volume['id'], self.volume['volume_token'])
        if (not fresh['mounted'] or fresh['mount_id'] != self.volume['mount_id']
                or fresh['mountpoint'] != self.volume['mountpoint']
                or fresh['mount_owner'] != self.volume['mount_owner']
                or not fresh['mountable']
                or (self.volume.get('read_only_guarded') and not fresh.get('read_only_guarded'))
                or (self.writing and (fresh['read_only'] or not fresh['write_supported']))):
            fail('MOUNT_CHANGED', 'The volume mount changed during the operation')
        current = os.stat(fresh['mountpoint'], follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (self.stat.st_dev, self.stat.st_ino) or fd_mount_id(self.fd) != self.mount_id:
            fail('MOUNT_CHANGED', 'The mounted root changed during the operation')

    def check_fd(self, fd):
        if os.fstat(fd).st_dev != self.stat.st_dev or fd_mount_id(fd) != self.mount_id:
            fail('CROSS_MOUNT_REJECTED', 'Nested or other filesystem mounts are not traversed')

    @contextmanager
    def directory(self, path):
        fd = os.dup(self.fd)
        try:
            for part in path_parts(path):
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = nxt
                self.check_fd(fd)
            self.recheck()
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def parent(self, path):
        parts = path_parts(path)
        if not parts:
            fail('ROOT_CHANGE_FORBIDDEN', 'The root of a mounted volume cannot be changed')
        with self.directory('/'.join(parts[:-1])) as fd:
            yield fd, parts[-1]


def entry_type(st):
    if stat.S_ISDIR(st.st_mode):
        return 'directory'
    if stat.S_ISREG(st.st_mode):
        return 'file'
    if stat.S_ISLNK(st.st_mode):
        return 'symlink'
    return 'other'


def entry_record(pin, path, st, *, link_target=None):
    """Bind a UI selection to this pathname, inode, owner and exact metadata."""
    path = clean_path(path)
    kind = entry_type(st)
    reason = None
    if path == '/':
        reason = 'The root of a mounted volume is not changed by the file manager'
    elif pin.volume['fs_type'] not in ('ext4', 'f2fs'):
        reason = 'This filesystem has no qualified POSIX permissions interface'
    elif kind not in ('file', 'directory'):
        reason = 'Permissions and ownership changes accept regular files or directories only'
    elif kind == 'file' and st.st_nlink != 1:
        reason = 'Changing this inode would also change its other hard links'
    token = digest({'volume_token': pin.volume['volume_token'],
                    'mount_id': pin.volume['mount_id'], 'path': path,
                    'stat': stamp(st), 'uid': st.st_uid, 'gid': st.st_gid,
                    'nlink': st.st_nlink})
    result = {'name': path.rsplit('/', 1)[-1] or '/', 'path': path,
              'type': kind, 'size': st.st_size, 'mode': stat.S_IMODE(st.st_mode),
              'uid': st.st_uid, 'gid': st.st_gid, 'nlink': st.st_nlink,
              'mtime_ns': st.st_mtime_ns, 'entry_token': token,
              'metadata_supported': reason is None, 'metadata_reason': reason}
    if link_target is not None:
        result['link_target'] = link_target
    return result


def expect_entry(entry, expected, *, required=False):
    if expected is None and not required:
        return
    require_hash(expected, 'expected-entry-token')
    if entry['entry_token'] != expected:
        fail('ENTRY_CHANGED', 'The selected inode or its metadata changed; refresh it before continuing')


def pinned_stat(pin, path):
    relative = clean_path(path)
    if relative == '/':
        pin.recheck()
        return entry_record(pin, relative, os.fstat(pin.fd))
    with pin.parent(relative) as (directory, name):
        handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        try:
            pin.check_fd(handle)
            before = os.fstat(handle)
            # readlinkat with an empty name reads the pinned link itself. Its
            # target is never opened, including for links to a device or FIFO.
            target = os.readlink('', dir_fd=handle) if stat.S_ISLNK(before.st_mode) else None
            pin.recheck()
            verify_named(directory, name, stamp(before))
            return entry_record(pin, relative, before, link_target=target)
        finally:
            os.close(handle)


def stat_entry(context, ident, path, expected=None, expected_entry=None):
    with PinnedVolume(context, ident, expected) as pin:
        entry = pinned_stat(pin, path)
        expect_entry(entry, expected_entry)
        return {'volume': ident, 'path': clean_path(path), 'entry': entry,
                'read_only': pin.volume['read_only'],
                'write_supported': pin.volume['write_supported']}


def resolve_entry(context, ident, path, expected=None, expected_entry=None):
    """Resolve links explicitly as if the selected volume were the path root.

    No ordinary list/read/write path follows symlinks. This read-only operation
    instead returns a canonical, symlink-free path which those APIs can use.
    O_PATH descriptors inspect every component without opening special files.
    """
    original = clean_path(path)
    with PinnedVolume(context, ident, expected) as pin:
        if expected_entry is not None:
            expect_entry(pinned_stat(pin, original), expected_entry)
        pending, resolved, links, proofs = path_parts(original), [], [], []
        stack, final = [os.dup(pin.fd)], None
        try:
            while pending:
                if len(pending) + len(resolved) > MAX_RESOLVE_PARTS:
                    fail('SYMLINK_EXPANSION_LIMIT', 'The expanded path has too many components')
                part = pending.pop(0)
                if part in ('', '.'):
                    continue
                if part == '..':
                    if not resolved:
                        fail('SYMLINK_ESCAPE', 'A relative symlink would escape the selected volume')
                    resolved.pop()
                    os.close(stack.pop())
                    continue
                if len(os.fsencode(part)) > 255:
                    fail('INVALID_SYMLINK_TARGET', 'A symlink component exceeds the filename limit')
                handle = os.open(part, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stack[-1])
                try:
                    pin.check_fd(handle)
                    st = os.fstat(handle)
                    current = '/' + '/'.join(resolved + [part])
                    if len(os.fsencode(current)) > 4096:
                        fail('SYMLINK_EXPANSION_LIMIT', 'The expanded path is too long')
                    if stat.S_ISLNK(st.st_mode):
                        if len(links) >= MAX_SYMLINKS:
                            fail('SYMLINK_LOOP', 'The path has a symlink loop or more than 40 links')
                        target = os.readlink('', dir_fd=handle)
                        if not target or '\0' in target or len(os.fsencode(target)) > 4096:
                            fail('INVALID_SYMLINK_TARGET', 'The symbolic link target is invalid or too long')
                        verify_named(stack[-1], part, stamp(st))
                        links.append({'path': current, 'target': target})
                        proofs.append((current, stamp(st)))
                        if target.startswith('/'):
                            while len(stack) > 1:
                                os.close(stack.pop())
                            resolved.clear()
                        pending = target.split('/') + pending
                    elif pending:
                        if not stat.S_ISDIR(st.st_mode):
                            fail('NOT_A_DIRECTORY', 'A resolved intermediate component is not a directory')
                        resolved.append(part)
                        stack.append(handle)
                        handle = None
                    else:
                        resolved.append(part)
                        final = handle
                        handle = None
                finally:
                    if handle is not None:
                        os.close(handle)
            canonical = '/' + '/'.join(resolved)
            target_st = os.fstat(final if final is not None else stack[-1])
            # Reopen the canonical path with the ordinary strict walker. This
            # catches a moved/replaced directory chain before returning it.
            entry = pinned_stat(pin, canonical)
            if entry['entry_token'] != entry_record(pin, canonical, target_st)['entry_token']:
                fail('ENTRY_CHANGED', 'The resolved target moved or changed during resolution')
            for link_path, before in proofs:
                with pin.parent(link_path) as (directory, name):
                    verify_named(directory, name, before)
            pin.recheck()
            return {'volume': ident, 'path': original, 'resolved_path': canonical,
                    'entry': entry, 'links': links, 'read_only': pin.volume['read_only']}
        finally:
            if final is not None:
                os.close(final)
            for handle in stack:
                os.close(handle)


def permission_mode(value):
    if isinstance(value, str):
        if not re.fullmatch(r'[0-7]{3,4}', value):
            fail('INVALID_MODE', 'Use three or four octal digits, for example 0644')
        value = int(value, 8)
    if type(value) is not int or not 0 <= value <= 0o7777:
        fail('INVALID_MODE', 'Permissions must be an octal mode between 0000 and 7777')
    return value


def numeric_owner(value):
    if isinstance(value, str):
        if not re.fullmatch(r'[0-9]{1,10}', value):
            fail('INVALID_OWNER', 'Use a numeric Linux UID or GID')
        value = int(value, 10)
    if type(value) is not int or not 0 <= value <= 0xfffffffe:
        fail('INVALID_OWNER', 'UID and GID must be between 0 and 4294967294')
    return value


def change_metadata(context, ident, path, *, mode=None, uid=None, gid=None,
                    confirmed=False, expected=None, expected_entry=None):
    context.confirm(confirmed)
    if mode is not None:
        mode = permission_mode(mode)
        if uid is not None or gid is not None:
            fail('INVALID_ARGUMENT', 'Change permissions or owner in separate reviewed operations')
    elif uid is None and gid is None:
        fail('INVALID_OWNER', 'Supply at least one of --uid or --gid')
    if uid is not None:
        uid = numeric_owner(uid)
    if gid is not None:
        gid = numeric_owner(gid)
    with PinnedVolume(context, ident, expected, writing=True) as pin, pin.parent(path) as (directory, name):
        handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        fd, attempted = None, False
        try:
            pin.check_fd(handle)
            before = os.fstat(handle)
            entry_before = entry_record(pin, path, before)
            expect_entry(entry_before, expected_entry, required=True)
            if not entry_before['metadata_supported']:
                fail('METADATA_CHANGE_UNSUPPORTED', entry_before['metadata_reason'])
            # Reopen the already pinned inode, never the mutable pathname.
            fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
            pin.check_fd(fd)
            if stamp(os.fstat(fd)) != stamp(before):
                fail('ENTRY_CHANGED', 'The selected inode changed while opening it')
            target_uid = before.st_uid if uid is None else uid
            target_gid = before.st_gid if gid is None else gid
            changed = (mode != stat.S_IMODE(before.st_mode)) if mode is not None else (
                (target_uid, target_gid) != (before.st_uid, before.st_gid))
            pin.recheck()
            verify_named(directory, name, stamp(before))
            if changed:
                attempted = True
                if mode is not None:
                    os.fchmod(fd, mode)
                else:
                    # The kernel may clear set-ID bits and file capabilities
                    # after an ownership change. Never silently reinstate them.
                    os.fchown(fd, target_uid, target_gid)
                os.fsync(fd)
                os.fsync(directory)
            after = os.fstat(fd)
            if ((after.st_uid, after.st_gid) != (target_uid, target_gid)
                    or (mode is not None and stat.S_IMODE(after.st_mode) != mode)):
                fail('METADATA_CHANGE_NOT_OBSERVED', 'The requested permissions or ownership were not observed')
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                fail('FILE_CHANGED', 'File content changed during the metadata operation')
            verify_named(directory, name, stamp(after))
            pin.recheck()
            return {'volume': ident, 'path': clean_path(path), 'entry_before': entry_before,
                    'entry': entry_record(pin, path, after), 'changed': changed,
                    'cleared_special_bits': stat.S_IMODE(before.st_mode) & 0o6000 & ~stat.S_IMODE(after.st_mode)}
        except FilesystemError as exc:
            if attempted:
                exc.details['metadata_change_attempted'] = True
            raise
        except OSError as exc:
            fail('METADATA_CHANGE_FAILED', str(exc), errno=exc.errno,
                 metadata_change_attempted=attempted)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(handle)


def list_files(context, ident, path='/', offset=0, limit=64, expected=None):
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        fail('INVALID_PAGE', 'Offset must be nonnegative and limit must be 1..256')
    relative = clean_path(path)
    with PinnedVolume(context, ident, expected) as pin, pin.directory(path) as fd:
        entries = []
        for name in os.listdir(fd):
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            entries.append(entry_record(pin, relative.rstrip('/') + '/' + name, st))
        entries.sort(key=lambda e: (e['type'] != 'directory', e['name'].casefold(), e['name']))
        pin.recheck()
        return {'volume': ident, 'path': relative, 'read_only': pin.volume['read_only'],
                'offset': offset, 'total': len(entries), 'has_more': offset + limit < len(entries),
                'entries': entries[offset:offset + limit]}


def read_file(context, ident, path, maximum=MAX_TEXT, expected=None):
    if not 1 <= maximum <= MAX_TEXT:
        fail('INVALID_LIMIT', 'Text read limit must be 1..1048576 bytes')
    with PinnedVolume(context, ident, expected) as pin, pin.parent(path) as (directory, name):
        fd = open_regular(name, dir_fd=directory)
        try:
            pin.check_fd(fd)
            before = stamp(os.fstat(fd))
            content = os.pread(fd, maximum, 0)
            size = os.fstat(fd).st_size
            truncated = size > maximum
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError as exc:
                if truncated and exc.reason == 'unexpected end of data' and exc.end == len(content):
                    text = content[:exc.start].decode('utf-8')
                else:
                    fail('BINARY_OR_NON_UTF8', 'Only UTF-8 text can be opened in the text editor')
            if '\0' in text:
                fail('BINARY_OR_NON_UTF8', 'Binary files are not opened by the text editor')
            # Editing is bounded to one MiB. Large text can be previewed without
            # reading an arbitrarily large file just to produce an unused edit hash.
            sha = hash_fd(fd) if size <= MAX_TEXT else None
            if stamp(os.fstat(fd)) != before:
                fail('FILE_CHANGED', 'File changed while it was read')
            pin.recheck()
            entry = entry_record(pin, path, os.fstat(fd))
            return {'volume': ident, 'path': clean_path(path), 'content': text, 'size': size,
                    'sha256': sha, 'hash_complete': sha is not None,
                    'truncated': truncated, 'read_only': pin.volume['read_only'],
                    'file_mode': entry['mode'],
                    **{key: value for key, value in entry.items()
                       if key in ('uid', 'gid', 'nlink', 'entry_token', 'metadata_supported', 'metadata_reason')}}
        finally:
            os.close(fd)


def text_input(context, path):
    path = absolute_no_symlinks(path)
    if context.mode == 'fixture':
        if context.root not in path.parents:
            fail('TEXT_INPUT_OUTSIDE_FIXTURE', 'Fixture text input must be beneath the fixture root')
    else:
        if not (RAM_STATE in path.parents or Path('/tmp') in path.parents):
            fail('TEXT_INPUT_NOT_RAM', 'Text edit input must be a temporary RAM file')
        candidates = [m for m in read_mounts() if Path(m['mountpoint']) == path or Path(m['mountpoint']) in path.parents]
        if not candidates or max(candidates, key=lambda m: len(Path(m['mountpoint']).parts))['fs_type'] not in ('rootfs', 'tmpfs', 'ramfs'):
            fail('TEXT_INPUT_NOT_RAM', 'Text edit input is not backed by RAM')
    fd = open_regular(path)
    try:
        before = stamp(os.fstat(fd))
        if os.fstat(fd).st_size > MAX_TEXT:
            fail('TEXT_TOO_LARGE', 'Text edits are limited to one MiB')
        data = os.read(fd, MAX_TEXT + 1)
        if len(data) > MAX_TEXT or stamp(os.fstat(fd)) != before:
            fail('TEXT_INPUT_CHANGED', 'Text input changed or exceeds one MiB')
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            fail('BINARY_OR_NON_UTF8', 'Text input must be UTF-8')
        if '\0' in text:
            fail('BINARY_OR_NON_UTF8', 'NUL bytes are not accepted as a text edit')
        return data
    finally:
        os.close(fd)


def rename_noreplace(source_fd, source, destination_fd, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, 'renameat2', None)
    if function is None:
        fail('ATOMIC_RENAME_UNAVAILABLE', 'renameat2(RENAME_NOREPLACE) is required')
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            fail('DESTINATION_EXISTS', 'An existing destination will not be overwritten')
        raise OSError(code, os.strerror(code))


def preserve_metadata(source, target):
    st = os.fstat(source)
    try:
        os.fchown(target, st.st_uid, st.st_gid)
        os.fchmod(target, stat.S_IMODE(st.st_mode))
        def attributes(fd):
            try:
                return {name: os.getxattr(fd, name) for name in os.listxattr(fd)}
            except OSError as exc:
                if exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP):
                    return {}
                raise
        attrs = attributes(source)
        existing = attributes(target)
        # A temporary file may inherit a directory default ACL or LSM label.
        # Leaving those extras behind changes the original file's access policy.
        for attribute in existing.keys() - attrs.keys():
            os.removexattr(target, attribute)
        for attribute, value in attrs.items():
            os.setxattr(target, attribute, value)
        if attributes(target) != attrs:
            fail('METADATA_PRESERVE_FAILED', 'Extended attributes do not match after copying')
        after = os.fstat(target)
        if (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)):
            fail('METADATA_PRESERVE_FAILED', 'Owner or permissions do not match after copying')
    except OSError as exc:
        fail('METADATA_PRESERVE_FAILED', 'Owner, permissions or extended attributes could not be preserved', errno=exc.errno)


def verify_named(directory, name, before):
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if stamp(current) != before:
        fail('FILE_CHANGED', 'The selected file changed during the operation')


def check_absent(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    fail('DESTINATION_EXISTS', 'An existing destination will not be overwritten')


def write_file(context, ident, path, text_file, confirmed=False, expected_sha=None,
               create=False, expected=None):
    context.confirm(confirmed)
    if create and expected_sha is not None:
        fail('INVALID_ARGUMENT', 'Use --create or --expected-sha256, not both')
    if not create:
        require_hash(expected_sha, 'expected-sha256')
    data = text_input(context, text_file)
    with PinnedVolume(context, ident, expected, writing=True) as pin, pin.parent(path) as (directory, name):
        source, before = None, None
        temporary = '.a22x-edit-' + os.urandom(12).hex()
        if create:
            check_absent(directory, name)
        else:
            source = open_regular(name, dir_fd=directory)
            pin.check_fd(source)
            before = stamp(os.fstat(source))
            if os.fstat(source).st_nlink != 1:
                os.close(source)
                fail('HARDLINK_REPLACE_REJECTED', 'Atomic text replacement would break an existing hard link')
            if hash_fd(source) != expected_sha:
                os.close(source)
                fail('FILE_CHANGED', 'The file changed since it was opened in the editor')
        target = None
        try:
            target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(target, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
            if source is not None:
                preserve_metadata(source, target)
            os.fsync(target)
            pin.recheck()
            if source is not None:
                verify_named(directory, name, before)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                rename_noreplace(directory, temporary, directory, name)
            os.fsync(directory)
            return {'volume': ident, 'path': clean_path(path), 'bytes_written': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'created': create}
        finally:
            if source is not None:
                os.close(source)
            if target is not None:
                os.close(target)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def make_directory(context, ident, path, confirmed=False, expected=None):
    context.confirm(confirmed)
    with PinnedVolume(context, ident, expected, writing=True) as pin, pin.parent(path) as (directory, name):
        check_absent(directory, name)
        pin.recheck()
        os.mkdir(name, mode=0o755, dir_fd=directory)
        os.fsync(directory)
        return {'volume': ident, 'path': clean_path(path), 'created': True}


def selected_entry(pin, directory, name, expected_sha=None):
    st = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        fail('SPECIAL_FILE_REJECTED', 'Symlinks and special files are not changed')
    if expected_sha is not None:
        require_hash(expected_sha, 'expected-sha256')
        if not stat.S_ISREG(st.st_mode):
            fail('REGULAR_FILE_REQUIRED', 'A hash-bound action requires a regular file')
        fd = open_regular(name, dir_fd=directory)
        try:
            pin.check_fd(fd)
            if hash_fd(fd) != expected_sha or stamp(os.fstat(fd)) != stamp(st):
                fail('FILE_CHANGED', 'The selected file changed')
        finally:
            os.close(fd)
    if stat.S_ISDIR(st.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        try:
            pin.check_fd(fd)
        finally:
            os.close(fd)
    return st


def rename_entry(context, ident, path, destination, confirmed=False, expected=None, expected_sha=None):
    context.confirm(confirmed)
    source_path, destination_path = clean_path(path), clean_path(destination)
    if source_path == destination_path or destination_path.startswith(source_path.rstrip('/') + '/'):
        fail('INVALID_DESTINATION', 'The destination must differ and cannot be inside the selected directory')
    with PinnedVolume(context, ident, expected, writing=True) as pin:
        with pin.parent(path) as (source_dir, source), pin.parent(destination) as (target_dir, target):
            before = selected_entry(pin, source_dir, source, expected_sha)
            if stat.S_ISDIR(before.st_mode) and context.nested_mounts(pin.volume, path):
                fail('NESTED_MOUNT', 'A directory containing another mount cannot be moved')
            check_absent(target_dir, target)
            pin.recheck()
            verify_named(source_dir, source, stamp(before))
            rename_noreplace(source_dir, source, target_dir, target)
            os.fsync(source_dir)
            os.fsync(target_dir)
            return {'volume': ident, 'path': source_path, 'destination': destination_path}


def copy_file(context, ident, path, destination_volume, destination, confirmed=False,
              expected=None, expected_destination=None, expected_sha=None):
    context.confirm(confirmed)
    if expected_sha is not None:
        require_hash(expected_sha, 'expected-sha256')
    with PinnedVolume(context, ident, expected) as source_pin, PinnedVolume(context, destination_volume, expected_destination, writing=True) as target_pin:
        with source_pin.parent(path) as (source_dir, name), target_pin.parent(destination) as (target_dir, target_name):
            check_absent(target_dir, target_name)
            source = open_regular(name, dir_fd=source_dir)
            temporary = '.a22x-copy-' + os.urandom(12).hex()
            target, total, hasher = None, 0, hashlib.sha256()
            try:
                source_pin.check_fd(source)
                before = stamp(os.fstat(source))
                usage = os.fstatvfs(target_dir)
                if usage.f_bavail * usage.f_frsize < os.fstat(source).st_size + 1024 * 1024:
                    fail('INSUFFICIENT_SPACE', 'The destination lacks space for this file and a small reserve')
                target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=target_dir)
                while True:
                    data = os.read(source, CHUNK)
                    if not data:
                        break
                    hasher.update(data)
                    view = memoryview(data)
                    while view:
                        wrote = os.write(target, view)
                        view = view[wrote:]
                    total += len(data)
                sha = hasher.hexdigest()
                if expected_sha is not None and sha != expected_sha:
                    fail('FILE_CHANGED', 'The copied source does not match its selected hash')
                if stamp(os.fstat(source)) != before:
                    fail('FILE_CHANGED', 'The source changed while copying')
                # FAT/exFAT have no POSIX owner/xattr model. Content copy is still useful;
                # existing-file text replacement never silently discards its metadata.
                posix_metadata = target_pin.volume['fs_type'] not in ('vfat', 'exfat')
                if posix_metadata:
                    preserve_metadata(source, target)
                os.fsync(target)
                source_pin.recheck()
                target_pin.recheck()
                verify_named(source_dir, name, before)
                rename_noreplace(target_dir, temporary, target_dir, target_name)
                os.fsync(target_dir)
                return {'volume': ident, 'path': clean_path(path), 'destination_volume': destination_volume,
                        'destination': clean_path(destination), 'bytes_copied': total, 'sha256': sha,
                        'posix_metadata_preserved': posix_metadata}
            finally:
                os.close(source)
                if target is not None:
                    os.close(target)
                try:
                    os.unlink(temporary, dir_fd=target_dir)
                except FileNotFoundError:
                    pass


def tree_identity(st):
    return st.st_dev, st.st_ino, stat.S_IFMT(st.st_mode)


def tree_snapshot(pin, root):
    """Bounded descriptor walk; even symlink contents are inspected without following."""
    records, unique, total = {}, set(), 0

    def record(relative, st, target=None):
        nonlocal total
        if len(records) >= MAX_TREE_ENTRIES:
            fail('TREE_ENTRY_LIMIT', 'Directory copy exceeds the 10000-entry limit')
        if len(relative.split('/')) > MAX_TREE_DEPTH or len(os.fsencode(relative)) > 4096:
            fail('TREE_DEPTH_LIMIT', 'Directory copy exceeds the path or 64-level depth limit')
        kind = entry_type(st)
        if kind == 'other':
            fail('SPECIAL_FILE_REJECTED', 'A directory containing devices, sockets or FIFOs is not copied', path=relative)
        item = {'stat': st, 'type': kind}
        if target is not None:
            item['target'] = target
        records[relative] = item
        if kind == 'file' and tree_identity(st) not in unique:
            unique.add(tree_identity(st))
            total += st.st_size
            if total > MAX_TREE_BYTES:
                fail('TREE_BYTE_LIMIT', 'Directory copy exceeds the 8-GiB content limit')

    def walk(directory, relative):
        pin.check_fd(directory)
        before = os.fstat(directory)
        record(relative, before)
        names = []
        # scandir lets us reject oversized directories without an unbounded listdir.
        with os.scandir(directory) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) + len(records) > MAX_TREE_ENTRIES:
                    fail('TREE_ENTRY_LIMIT', 'Directory copy exceeds the 10000-entry limit')
        for name in sorted(names):
            child = relative + '/' + name if relative else name
            handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            try:
                pin.check_fd(handle)
                st = os.fstat(handle)
                if stat.S_ISDIR(st.st_mode):
                    fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                    try:
                        if stamp(os.fstat(fd)) != stamp(st):
                            fail('FILE_CHANGED', 'A source directory changed during inspection')
                        walk(fd, child)
                    finally:
                        os.close(fd)
                else:
                    target = os.readlink('', dir_fd=handle) if stat.S_ISLNK(st.st_mode) else None
                    record(child, st, target)
                verify_named(directory, name, stamp(st))
            finally:
                os.close(handle)
        if stamp(os.fstat(directory)) != stamp(before):
            fail('FILE_CHANGED', 'A source directory changed during inspection')

    walk(root, '')
    return records, total


def snapshot_identity(records):
    return {path: (stamp(item['stat']), item['stat'].st_uid, item['stat'].st_gid,
                   item['stat'].st_nlink, item.get('target')) for path, item in records.items()}


@contextmanager
def tree_directory(pin, root, relative):
    """Open a directory relative to an already pinned source/staging root."""
    fd = os.dup(root)
    try:
        for part in path_parts(relative):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            pin.check_fd(fd)
        yield fd
    finally:
        os.close(fd)


def symlink_metadata(source_dir, source_name, target_dir, target_name, before):
    # Python xattr APIs have no dir_fd parameter. /proc/self/fd keeps the parent
    # pinned; follow_symlinks=False ensures even an absolute link is never read.
    source = '/proc/self/fd/' + str(source_dir) + '/' + source_name
    target = '/proc/self/fd/' + str(target_dir) + '/' + target_name
    def attributes(path):
        try:
            return {name: os.getxattr(path, name, follow_symlinks=False)
                    for name in os.listxattr(path, follow_symlinks=False)}
        except OSError as exc:
            if exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP):
                return {}
            raise
    try:
        os.chown(target_name, before.st_uid, before.st_gid, dir_fd=target_dir, follow_symlinks=False)
        attrs, existing = attributes(source), attributes(target)
        for name in existing.keys() - attrs.keys():
            os.removexattr(target, name, follow_symlinks=False)
        for name, value in attrs.items():
            os.setxattr(target, name, value, follow_symlinks=False)
        os.utime(target_name, ns=(before.st_atime_ns, before.st_mtime_ns), dir_fd=target_dir, follow_symlinks=False)
        after = os.stat(target_name, dir_fd=target_dir, follow_symlinks=False)
        if ((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode), after.st_mtime_ns)
                != (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode), before.st_mtime_ns)
                or attributes(target) != attrs):
            fail('METADATA_PRESERVE_FAILED', 'Symbolic-link metadata could not be preserved exactly')
    except OSError as exc:
        fail('METADATA_PRESERVE_FAILED', 'Symbolic-link metadata could not be preserved', errno=exc.errno)


def preserve_times(fd, before):
    os.utime(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.fstat(fd)
    if (after.st_atime_ns, after.st_mtime_ns) != (before.st_atime_ns, before.st_mtime_ns):
        fail('METADATA_PRESERVE_FAILED', 'Access and modification times could not be preserved exactly')


def cleanup_tree_stage(pin, parent, name, root, created):
    """Remove only entries created by this copy, never recursively delete strangers."""
    complete = True
    for relative in sorted((p for p in created if p), key=lambda p: p.count('/'), reverse=True):
        directory, _, leaf = relative.rpartition('/')
        try:
            with tree_directory(pin, root, directory) as fd:
                st = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                if tree_identity(st) != created[relative]:
                    complete = False
                    continue
                if stat.S_ISDIR(st.st_mode):
                    os.rmdir(leaf, dir_fd=fd)
                else:
                    os.unlink(leaf, dir_fd=fd)
        except FileNotFoundError:
            continue
        except (FilesystemError, OSError):
            complete = False
    try:
        st = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if tree_identity(st) != created.get(''):
            return False
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    except (FilesystemError, OSError):
        return False
    return complete


def remove_empty_stage(parent, name, identity):
    try:
        if tree_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity:
            return False
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
        return True
    except OSError:
        return False


def copy_tree(context, ident, path, destination_volume, destination, confirmed=False,
              expected=None, expected_destination=None, expected_entry=None):
    """Copy a bounded Linux tree to one new name, then publish it atomically."""
    context.confirm(confirmed)
    require_hash(expected_entry, 'expected-entry-token')
    source_path, destination_path = clean_path(path), clean_path(destination)
    if ident == destination_volume and (destination_path == source_path or destination_path.startswith(source_path.rstrip('/') + '/')):
        fail('INVALID_DESTINATION', 'A directory cannot be copied into itself')
    with PinnedVolume(context, ident, expected) as source_pin, PinnedVolume(context, destination_volume, expected_destination, writing=True) as target_pin:
        if target_pin.volume['fs_type'] not in ('ext4', 'f2fs'):
            fail('TREE_METADATA_UNSUPPORTED', 'Directory copy requires an ext4 or F2FS destination')
        with source_pin.parent(source_path) as (source_parent, source_name), target_pin.parent(destination_path) as (target_parent, target_name):
            check_absent(target_parent, target_name)
            source = os.open(source_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_parent)
            stage, container = None, None
            stage_name, created, published = '.a22x-tree-' + os.urandom(12).hex(), {}, False
            container_identity, container_created = None, False
            try:
                source_pin.check_fd(source)
                root_before = os.fstat(source)
                expect_entry(entry_record(source_pin, source_path, root_before), expected_entry, required=True)
                if context.nested_mounts(source_pin.volume, source_path):
                    fail('NESTED_MOUNT', 'A directory containing another mount cannot be copied')
                records, total_bytes = tree_snapshot(source_pin, source)
                if any(len(os.fsencode(base.rstrip('/') + '/' + relative)) > 4096
                       for relative in records for base in (source_path, destination_path)):
                    fail('TREE_DEPTH_LIMIT', 'A copied path would exceed the file-manager path limit')
                target_identity = tree_identity(os.fstat(target_parent))
                if target_identity in {tree_identity(r['stat']) for r in records.values() if r['type'] == 'directory'}:
                    fail('INVALID_DESTINATION', 'The destination parent is inside the selected source tree')
                usage = os.fstatvfs(target_parent)
                reserve = MAX_TEXT + len(records) * max(usage.f_frsize, 4096)
                if usage.f_bavail * usage.f_frsize < total_bytes + reserve:
                    fail('INSUFFICIENT_SPACE', 'The destination lacks space for the tree and its metadata reserve')
                source_pin.recheck()
                target_pin.recheck()
                verify_named(source_parent, source_name, stamp(root_before))
                # Keep a separate root-owned private wrapper. The copied root
                # can then receive its original UID/mode without exposing the
                # staged tree to that UID before the final atomic rename.
                os.mkdir(stage_name, mode=0o700, dir_fd=target_parent)
                container_created = True
                container_identity = tree_identity(os.stat(stage_name, dir_fd=target_parent, follow_symlinks=False))
                container = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=target_parent)
                target_pin.check_fd(container)
                if tree_identity(os.fstat(container)) != container_identity:
                    fail('FILE_CHANGED', 'The private staging wrapper changed')
                os.mkdir('tree', mode=0o700, dir_fd=container)
                created[''] = tree_identity(os.stat('tree', dir_fd=container, follow_symlinks=False))
                stage = os.open('tree', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=container)
                target_pin.check_fd(stage)
                if tree_identity(os.fstat(stage)) != created['']:
                    fail('FILE_CHANGED', 'The copied-tree staging root changed')
                linked, hashes, copied_bytes, hardlinks = {}, {}, 0, 0
                for relative, item in records.items():
                    if not relative:
                        continue
                    directory, _, name = relative.rpartition('/')
                    before = item['stat']
                    with tree_directory(source_pin, source, directory) as source_dir, tree_directory(target_pin, stage, directory) as target_dir:
                        verify_named(source_dir, name, stamp(before))
                        inode = tree_identity(before)
                        if item['type'] == 'directory':
                            os.mkdir(name, mode=0o700, dir_fd=target_dir)
                            created[relative] = tree_identity(os.stat(name, dir_fd=target_dir, follow_symlinks=False))
                        elif inode in linked:
                            os.link(linked[inode], name, src_dir_fd=stage, dst_dir_fd=target_dir, follow_symlinks=False)
                            created[relative] = tree_identity(os.stat(name, dir_fd=target_dir, follow_symlinks=False))
                            if created[relative] != created[linked[inode]]:
                                fail('FILE_CHANGED', 'A staged hard-link target changed')
                            hardlinks += 1
                        elif item['type'] == 'symlink':
                            os.symlink(item['target'], name, dir_fd=target_dir)
                            created[relative] = tree_identity(os.stat(name, dir_fd=target_dir, follow_symlinks=False))
                            if os.readlink(name, dir_fd=target_dir) != item['target']:
                                fail('FILE_CHANGED', 'A copied symbolic link changed')
                            symlink_metadata(source_dir, name, target_dir, name, before)
                            linked[inode] = relative
                        else:
                            read_fd = open_regular(name, dir_fd=source_dir)
                            write_fd = None
                            try:
                                source_pin.check_fd(read_fd)
                                if stamp(os.fstat(read_fd)) != stamp(before):
                                    fail('FILE_CHANGED', 'A source file changed before copying')
                                write_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=target_dir)
                                created[relative] = tree_identity(os.fstat(write_fd))
                                target_pin.check_fd(write_fd)
                                hasher, count = hashlib.sha256(), 0
                                while count < before.st_size:
                                    data = os.read(read_fd, min(CHUNK, before.st_size - count))
                                    if not data:
                                        fail('FILE_CHANGED', 'A source file became shorter while copying')
                                    hasher.update(data)
                                    view = memoryview(data)
                                    while view:
                                        wrote = os.write(write_fd, view)
                                        if wrote <= 0:
                                            fail('COPY_TREE_FAILED', 'A destination write made no progress')
                                        view = view[wrote:]
                                    count += len(data)
                                if stamp(os.fstat(read_fd)) != stamp(before):
                                    fail('FILE_CHANGED', 'A source file changed while copying')
                                sha = hasher.hexdigest()
                                if hash_fd(write_fd) != sha:
                                    fail('COPY_VERIFY_FAILED', 'Copied file content did not verify')
                                preserve_metadata(read_fd, write_fd)
                                preserve_times(write_fd, before)
                                os.fsync(write_fd)
                                copied_bytes += count
                                hashes[relative] = sha
                                linked[inode] = relative
                            finally:
                                os.close(read_fd)
                                if write_fd is not None:
                                    os.close(write_fd)
                        verify_named(source_dir, name, stamp(before))
                # Restore directory metadata only after children are complete;
                # until then the unpublished tree is private and writable.
                for relative, item in reversed(list(records.items())):
                    if item['type'] != 'directory':
                        continue
                    with tree_directory(source_pin, source, relative) as source_dir, tree_directory(target_pin, stage, relative) as target_dir:
                        if stamp(os.fstat(source_dir)) != stamp(item['stat']):
                            fail('FILE_CHANGED', 'A source directory changed while copying')
                        preserve_metadata(source_dir, target_dir)
                        before = item['stat']
                        preserve_times(target_dir, before)
                        os.fsync(target_dir)
                source_pin.recheck()
                target_pin.recheck()
                fresh, _ = tree_snapshot(source_pin, source)
                if snapshot_identity(fresh) != snapshot_identity(records):
                    fail('FILE_CHANGED', 'The selected directory changed during the copy')
                verify_named(source_parent, source_name, stamp(root_before))
                with target_pin.parent(destination_path) as (current_parent, _):
                    if tree_identity(os.fstat(current_parent)) != target_identity:
                        fail('DESTINATION_CHANGED', 'The destination directory moved or changed during the copy')
                if (tree_identity(os.stat(stage_name, dir_fd=target_parent, follow_symlinks=False)) != container_identity
                        or tree_identity(os.stat('tree', dir_fd=container, follow_symlinks=False)) != created['']):
                    fail('FILE_CHANGED', 'The private staging directory changed')
                source_pin.recheck()
                target_pin.recheck()
                rename_noreplace(container, 'tree', target_parent, target_name)
                published = True
                os.fsync(container)
                os.fsync(target_parent)
                os.rmdir(stage_name, dir_fd=target_parent)
                os.fsync(target_parent)
                counts = {kind: sum(item['type'] == kind for item in records.values()) for kind in ('file', 'directory', 'symlink')}
                manifest = {relative: {'type': item['type'],
                            'sha256': hashes[linked[tree_identity(item['stat'])]] if item['type'] == 'file' else None,
                            'target': item.get('target')} for relative, item in records.items()}
                return {'volume': ident, 'path': source_path, 'destination_volume': destination_volume,
                        'destination': destination_path, 'bytes_copied': copied_bytes,
                        'files': counts['file'], 'directories': counts['directory'], 'symlinks': counts['symlink'],
                        'hardlinks_preserved': hardlinks, 'posix_metadata_preserved': True,
                        'content_manifest_sha256': digest(manifest), 'published': True}
            except (FilesystemError, OSError) as exc:
                details = {'destination_published': published}
                if container_created:
                    complete = True
                    if not published and stage is not None:
                        complete = cleanup_tree_stage(target_pin, container, 'tree', stage, created)
                    elif not published and '' in created:
                        complete = remove_empty_stage(container, 'tree', created[''])
                    wrapper_removed = remove_empty_stage(target_parent, stage_name, container_identity)
                    complete = complete and wrapper_removed
                    details['cleanup_complete'] = complete
                    if not complete:
                        details['staging_name'] = stage_name
                if isinstance(exc, FilesystemError):
                    exc.details.update(details)
                    raise
                fail('COPY_TREE_FAILED', str(exc), errno=exc.errno, **details)
            finally:
                os.close(source)
                if stage is not None:
                    os.close(stage)
                if container is not None:
                    os.close(container)


def delete_entry(context, ident, path, confirmed=False, expected=None, expected_sha=None):
    context.confirm(confirmed)
    with PinnedVolume(context, ident, expected, writing=True) as pin, pin.parent(path) as (directory, name):
        before = selected_entry(pin, directory, name, expected_sha)
        if stat.S_ISDIR(before.st_mode) and context.nested_mounts(pin.volume, path):
            fail('NESTED_MOUNT', 'A directory containing another mount cannot be deleted')
        pin.recheck()
        verify_named(directory, name, stamp(before))
        if stat.S_ISDIR(before.st_mode):
            os.rmdir(name, dir_fd=directory)
        else:
            os.unlink(name, dir_fd=directory)
        os.fsync(directory)
        return {'volume': ident, 'path': clean_path(path), 'deleted_type': entry_type(before)}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fail('INVALID_ARGUMENT', message)


def parser():
    p = Parser(description=__doc__)
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument('--device', action='store_true')
    kind.add_argument('--fixture', type=Path)
    commands = p.add_subparsers(dest='operation', required=True, parser_class=Parser)
    commands.add_parser('inventory')
    commands.add_parser('unmount-all')
    commands.add_parser('usb-sync')
    commands.add_parser('usb-eject')
    usb_backup = commands.add_parser('usb-backup')
    usb_backup.add_argument('--volume', required=True)
    usb_backup.add_argument('--confirm', action='store_true')
    erase = commands.add_parser('format')
    erase.add_argument('--volume', required=True)
    erase.add_argument('--expected-volume-token')
    erase.add_argument('--fs', choices=('ext4', 'vfat', 'f2fs'), required=True)
    erase.add_argument('--confirm', action='store_true')
    for name in ('check', 'resize', 'wipe'):
        repair = commands.add_parser(name)
        repair.add_argument('--volume', required=True)
        repair.add_argument('--expected-volume-token')
        repair.add_argument('--confirm', action='store_true')
    for action in ('mount', 'unmount', 'list', 'read', 'stat', 'resolve', 'chmod', 'chown',
                   'write', 'mkdir', 'rename', 'copy', 'copy-tree', 'delete'):
        command = commands.add_parser(action)
        command.add_argument('--volume', required=True)
        command.add_argument('--expected-volume-token')
        if action not in ('mount', 'unmount'):
            command.add_argument('--path', default='/' if action == 'list' else None, required=action != 'list')
        if action in ('mount', 'write', 'mkdir', 'rename', 'copy', 'copy-tree', 'delete', 'chmod', 'chown'):
            command.add_argument('--confirm', action='store_true')
        if action == 'mount':
            command.add_argument('--mode', choices=('ro', 'rw'), default='ro')
        if action == 'list':
            command.add_argument('--offset', type=int, default=0)
            command.add_argument('--limit', type=int, default=64)
        if action == 'read':
            command.add_argument('--max-bytes', type=int, default=MAX_TEXT)
        if action in ('stat', 'resolve', 'chmod', 'chown', 'copy-tree'):
            command.add_argument('--expected-entry-token', required=action in ('chmod', 'chown', 'copy-tree'))
        if action == 'chmod':
            command.add_argument('--mode', type=permission_mode, required=True)
        if action == 'chown':
            command.add_argument('--uid', type=numeric_owner)
            command.add_argument('--gid', type=numeric_owner)
        if action == 'write':
            command.add_argument('--text-file', type=Path, required=True)
            command.add_argument('--create', action='store_true')
        if action in ('write', 'rename', 'copy', 'delete'):
            command.add_argument('--expected-sha256')
        if action in ('rename', 'copy', 'copy-tree'):
            command.add_argument('--destination', required=True)
        if action in ('copy', 'copy-tree'):
            command.add_argument('--destination-volume', required=True)
            command.add_argument('--expected-destination-volume-token')
    return p


# Wipe → Cache, as TWRP's (2026-11-10): everything on the volume except what the recovery keeps there
# (its settings, keys, saved Wi-Fi networks, plans and scripts in a22x-recovery).
WIPE_KEEP = {'cache': ('a22x-recovery', 'lost+found')}


def wipe_contents(context, ident, confirmed=False, expected=None):
    """Delete everything at the top of IDENT except WIPE_KEEP. The volume is mounted for writing
    when needed and left as it was found (not mounted, or read-only)."""
    context.confirm(confirmed)
    if ident not in WIPE_KEEP:
        fail('WIPE_NOT_ADMITTED', 'Only cache is wiped this way. Erase other volumes in Files', volume=ident)
    volume = context.volume(ident, expected)
    mounted_here = not volume['mounted']
    reopen = volume['mounted'] and volume['read_only'] and volume.get('mount_owner') == 'filesystem'
    if mounted_here or reopen:
        context.mount(ident, 'rw', True, volume['volume_token'])
    removed = 0
    try:
        with PinnedVolume(context, ident, writing=True) as pin:
            before = os.fstatvfs(pin.fd)
            for name in sorted(os.listdir(pin.fd)):
                if name in WIPE_KEEP[ident]:
                    continue
                pin.recheck()
                info = os.stat(name, dir_fd=pin.fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    shutil.rmtree(name, dir_fd=pin.fd)  # By descriptor: a symlink is removed, never followed.
                else:
                    os.unlink(name, dir_fd=pin.fd)
                removed += 1
            os.sync()
            after = os.fstatvfs(pin.fd)
            freed = max(0, (after.f_bavail - before.f_bavail) * after.f_frsize)
    finally:
        if mounted_here:
            context.unmount(ident)
        elif reopen:
            with suppress(OSError, FilesystemError):  # Still writable then; Files shows it as it is.
                context.mount(ident, 'ro', True, context.volume(ident)['volume_token'])
    return {'volume': ident, 'removed': removed, 'freed_bytes': freed, 'kept': list(WIPE_KEEP[ident])}


def dispatch(context, args):
    action = args.operation
    with context.operation_lock():
        if action == 'inventory':
            return context.inventory()
        if action == 'unmount-all':
            return context.unmount_all()
        if action == 'usb-sync':
            return context.sync_usb()
        if action == 'usb-eject':
            return context.eject_usb()
        if action == 'usb-backup':
            return context.prepare_usb_backup(args.volume, args.confirm)
        v, expected = args.volume, args.expected_volume_token
        if action == 'mount':
            return context.mount(v, args.mode, args.confirm, expected)
        if action == 'unmount':
            return context.unmount(v, expected)
        if action == 'format':
            return context.format(v, args.fs, args.confirm, expected)
        if action == 'wipe':
            return wipe_contents(context, v, args.confirm, expected)
        if action == 'check':
            return context.check(v, args.confirm, expected)
        if action == 'resize':
            return context.resize(v, args.confirm, expected)
        if action == 'list':
            return list_files(context, v, args.path, args.offset, args.limit, expected)
        if action == 'read':
            return read_file(context, v, args.path, args.max_bytes, expected)
        if action == 'stat':
            return stat_entry(context, v, args.path, expected, args.expected_entry_token)
        if action == 'resolve':
            return resolve_entry(context, v, args.path, expected, args.expected_entry_token)
        if action == 'chmod':
            return change_metadata(context, v, args.path, mode=args.mode,
                                   confirmed=args.confirm, expected=expected,
                                   expected_entry=args.expected_entry_token)
        if action == 'chown':
            return change_metadata(context, v, args.path, uid=args.uid, gid=args.gid,
                                   confirmed=args.confirm, expected=expected,
                                   expected_entry=args.expected_entry_token)
        if action == 'write':
            return write_file(context, v, args.path, args.text_file, args.confirm,
                              args.expected_sha256, args.create, expected)
        if action == 'mkdir':
            return make_directory(context, v, args.path, args.confirm, expected)
        if action == 'rename':
            return rename_entry(context, v, args.path, args.destination, args.confirm,
                                expected, args.expected_sha256)
        if action == 'copy':
            return copy_file(context, v, args.path, args.destination_volume, args.destination,
                             args.confirm, expected, args.expected_destination_volume_token,
                             args.expected_sha256)
        if action == 'copy-tree':
            return copy_tree(context, v, args.path, args.destination_volume, args.destination,
                             args.confirm, expected, args.expected_destination_volume_token,
                             args.expected_entry_token)
        return delete_entry(context, v, args.path, args.confirm, expected, args.expected_sha256)


def main(argv=None):
    operation = 'unknown'
    try:
        args = parser().parse_args(argv)
        operation = args.operation
        context = FixtureContext(args.fixture) if args.fixture else DeviceContext()
        value = dispatch(context, args)
        # The envelope identifies the selected adapter, never file permissions.
        # Operation payloads cannot overwrite these reserved protocol fields.
        print(json.dumps({**value, 'ok': True, 'operation': operation, 'mode': context.mode}, ensure_ascii=True, separators=(',', ':')))
        return 0
    except FilesystemError as exc:
        error = {'code': exc.code, 'message': exc.message, 'details': exc.details}
        rc = 2
    except (OSError, subprocess.SubprocessError) as exc:
        code = 'FILESYSTEM_KEY_UNAVAILABLE' if isinstance(exc, OSError) and exc.errno in (getattr(errno, 'ENOKEY', 126), getattr(errno, 'EKEYREVOKED', 128)) else 'IO_ERROR'
        if isinstance(exc, OSError) and exc.errno in (errno.ELOOP, errno.ENOTDIR):
            code = 'SYMLINK_OR_NON_DIRECTORY'
        error = {'code': code, 'message': str(exc), 'details': {'errno': getattr(exc, 'errno', None)}}
        rc = 3
    except (ValueError, TypeError, KeyError) as exc:
        error = {'code': 'INVALID_INPUT', 'message': str(exc), 'details': {}}
        rc = 2
    print(json.dumps({'ok': False, 'operation': operation, 'error': error}, ensure_ascii=True, separators=(',', ':')))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
