#!/usr/bin/env python3
"""Offline ARM64 rootfs import and recoverable OS switching for A22x recovery.

No block write, format, partition, kernel install, archive script or reboot is
implemented. New roots are installed beside the current system. Switching is a
write-ahead-journaled series of same-filesystem renames, NOT one atomic rename.
The original /home inode stays in place. /sbin is removed first and installed
last, so an interrupted switch cannot intentionally boot a mixed root.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
import types

import bootimage
import filesystem as fs

MANAGER = '.a22x-systems'
# Images saved on the phone (2026-11-10): downloaded from the catalog or copied over USB, kept
# beside the systems on userdata. A switch never moves them, and a clean installation keeps them,
# so an SD card is not needed to install, and a download is not lost when the network drops.
STORE_DIR = '.a22x-images'
STORE_META = '.a22x.json'
STORE_FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,199}')
SCRIPT_STORE = 'a22x-recovery/scripts/'
PROFILE_PATH = Path('/etc/a22x-recovery/distro-platform.json')
# The exact files the profile names, kept in the recovery for a system that lost them.
PLATFORM_BUNDLE = Path('/opt/a22x-recovery/platform/platform-files.tar.xz')
# This phone's sound profile and mixer state for a system that has none of its own.
PLATFORM_OPTIONAL_BUNDLE = Path('/opt/a22x-recovery/platform/platform-optional.tar.xz')
SENTINEL_PATH = Path('/run/a22x-recovery/distro-transaction.json')
BOOT_SHA256 = 'b3f59daa37ad50e0ac6bfaed83f6954147536d07119f1b46a9981ee19d9b17ab'
# Kernel #108 repack of the same BOOT: same ramdisk, device tree and command line.
BOOT_K108_SHA256 = 'f188b56b28a68c2f8087e45ff60e29ea5353cdbd0f3294a7efe89fe9d9477993'
# The same kernel #107 BOOT whose /init skips the charge-only screen when the system says
# etc/a22x/charger-mode = off (2026-11-09, artifacts/charger-off-k107-20261109).
BOOT_CHARGER_OFF_SHA256 = 'b689f2dd5bb4f72a65665e1ebfcd5012b4126a21d7b427fa54c2d3f9cffc27f8'
# Kernel 116 (k107 + recovery's USB fixes + Landlock + system splash) in the same container.
BOOT_K116_SHA256 = '71a50262a73859e1538e79277f0ce00822be208d52b32328038a5932b513f874'
# Kernel 121 = 116 + USB replug after system sleep; only the kernel differs from 116, the same
# modules (release 7.2.0-a22x+) serve both (2026-11-09, artifacts/boot-k121-20261109).
BOOT_K121_SHA256 = 'e3542fcdd905a4b89a10eb828fa983bc9f0837a2635155d0124c692f6c5fbb1f'
# BOOT v1r6 (2026-11-10): kernel 122 keeps the USB device link through system sleep.
BOOT_K122_SHA256 = '9701f48ddced60c648e79a502885778935d1cb5610a011d77fc1898eea01d41a'
# BOOT v1r7 (2026-11-12, published as v147): kernel 138 keeps the battery percent in the PMIC, shared
# with the recovery's kernel and Android's gauge; only the kernel differs, the same modules serve it.
BOOT_K138_SHA256 = '5faafaa0035bff7bd77251ab01e2b4c395798755ad24db7b3240cc3d6139ad0f'
ALLOWED_BOOTS = frozenset((BOOT_SHA256, BOOT_K108_SHA256, BOOT_CHARGER_OFF_SHA256, BOOT_K116_SHA256, BOOT_K121_SHA256, BOOT_K122_SHA256, BOOT_K138_SHA256))
BOOT_BYTES = 33554432
USERDATA_BYTES = 53393489920
# A first start of Manjaro ARM grew userdata to the end of the eMMC (sfdisk, 2026-11-08):
# the same start sector, ending at the last usable sector of the 122142720-sector disk.
USERDATA_GROWN_BYTES = (122142720 - 33 - 17850368) * 512
USERDATA_SIZES = (USERDATA_BYTES, USERDATA_GROWN_BYTES)
PROFILE_ID = 'a22x-ext4-v2'
KERNEL_RELEASE = '7.2.0-a22x+'
RESERVED = frozenset(('home', 'lost+found', MANAGER, STORE_DIR))
MAX_JSON = 4 * 1024 * 1024
MAX_ENTRIES = 200000
MAX_PROFILE_ENTRIES = 30000
MAX_SLOTS = 16
STARTED_FILE = 'var/lib/a22x/started'  # written by the system itself: a22x_adapter.STARTED_UNIT
MAX_ARCHIVE = 16 * 1024**3
MAX_UNPACKED = 24 * 1024**3
MAX_FILE = 8 * 1024**3
MAX_PATH = 1024
MAX_PAX = 65536
MAX_ARCHIVE_METADATA = 32 * 1024 * 1024
MAX_METADATA_CHAIN = 16
CHUNK = 1024 * 1024
PLAN_SECONDS = 900
MIN_FREE = 256 * 1024 * 1024
RUNTIME_DIRS = frozenset(('dev', 'proc', 'sys', 'run'))


class DistroError(fs.FilesystemError):
    pass


def fail(code, message, **details):
    raise DistroError(code, message, **details)


def require(value, code, message, **details):
    if not value:
        fail(code, message, **details)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def seal(value):
    value = dict(value)
    value['seal'] = digest(value)
    return value


def check_seal(value):
    require(isinstance(value, dict) and isinstance(value.get('seal'), str),
            'INVALID_RECORD', 'Expected a sealed manager record')
    copy = dict(value)
    expected = copy.pop('seal')
    require(digest(copy) == expected, 'RECORD_CHANGED', 'Manager record seal does not match')
    return value


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', value),
            'INVALID_IDENTIFIER', 'Use 1–48 lowercase letters, digits or hyphens')
    return value


def name_text(value):
    require(isinstance(value, str) and 1 <= len(value) <= 96 and
            all(ord(c) >= 32 for c in value), 'INVALID_NAME', 'Invalid display name')
    return value


def path_parts(value, *, root=False):
    # A backslash is an ordinary Linux name character; systemd unit escapes use it.
    require(isinstance(value, str) and '\0' not in value, 'INVALID_PATH', 'Invalid rootfs path')
    while value.startswith('./'):
        value = value[2:]
    value = value.rstrip('/')
    if value in ('', '.') and root:
        return ()
    require(not value.startswith('/') and value not in ('', '.'),
            'INVALID_PATH', 'Archive paths must be relative to the new root')
    parts = tuple(value.split('/'))
    require(len(parts) <= 64 and len(value.encode('utf-8', 'strict')) <= MAX_PATH and
            all(p not in ('', '.', '..') and len(p.encode()) <= 255 for p in parts),
            'INVALID_PATH', 'Rootfs paths may not contain dot, parent or empty components')
    return parts


def logical_path(value):
    return '/'.join(path_parts(value.lstrip('/')))


def link_destination(parent, target):
    require(isinstance(target, str) and target and '\0' not in target
            and len(target.encode('utf-8', 'strict')) <= MAX_PATH,
            'UNSAFE_LINK', 'Invalid symlink target')
    out = [] if target.startswith('/') else list(parent)
    for part in target.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            require(out, 'UNSAFE_LINK', 'A symlink escapes its root filesystem')
            out.pop()
        else:
            require(len(part.encode()) <= 255, 'UNSAFE_LINK', 'Symlink component is too long')
            out.append(part)
    require(len(out) <= 64, 'UNSAFE_LINK', 'Symlink target is too deep')
    return tuple(out)


def identity(st, target=None):
    out = {'dev': st.st_dev, 'ino': st.st_ino, 'mode': st.st_mode,
           'uid': st.st_uid, 'gid': st.st_gid}
    if not stat.S_ISDIR(st.st_mode):
        out['size'] = st.st_size
        out['mtime_ns'] = st.st_mtime_ns
    if target is not None:
        out['target'] = target
    return out


class Tree:
    """FD-relative tree confined to one pinned mount; archive links are data."""
    def __init__(self, pin, prefix=''):
        self.pin, self.prefix = pin, prefix
        self.fd = os.dup(pin.fd)
        try:
            for part in path_parts(prefix) if prefix else ():
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=self.fd)
                os.close(self.fd)
                self.fd = new
                pin.check_fd(new)
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        os.close(self.fd)

    @contextlib.contextmanager
    def directory(self, path=''):
        fd = os.dup(self.fd)
        try:
            for part in path_parts(path) if path else ():
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
                os.close(fd)
                fd = new
                self.pin.check_fd(fd)
            yield fd
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def parent(self, path):
        parts = path_parts(path)
        with self.directory('/'.join(parts[:-1])) as fd:
            yield fd, parts[-1]

    def lstat(self, path):
        with self.parent(path) as (fd, name):
            return os.stat(name, dir_fd=fd, follow_symlinks=False)

    def exists(self, path):
        try:
            self.lstat(path)
            return True
        except FileNotFoundError:
            return False

    def readlink(self, path):
        with self.parent(path) as (fd, name):
            return os.readlink(name, dir_fd=fd)

    def record(self, path):
        st = self.lstat(path)
        return identity(st, self.readlink(path) if stat.S_ISLNK(st.st_mode) else None)

    def regular(self, path):
        with self.parent(path) as (fd, name):
            result = fs.open_regular(name, dir_fd=fd)
        try:
            self.pin.check_fd(result)
        except BaseException:
            os.close(result)
            raise
        return result

    def read(self, path, maximum=MAX_JSON):
        fd = self.regular(path)
        try:
            before = fs.stamp(os.fstat(fd))
            require(before[3] <= maximum, 'INPUT_TOO_LARGE', 'Input exceeds its size limit', path=path)
            value = os.read(fd, maximum + 1)
            require(len(value) <= maximum and fs.stamp(os.fstat(fd)) == before,
                    'SOURCE_CHANGED', 'File changed while reading', path=path)
            return value
        finally:
            os.close(fd)

    def json(self, path, sealed=False):
        value = json.loads(self.read(path))
        return check_seal(value) if sealed else value

    def hash(self, path):
        fd = self.regular(path)
        try:
            before = fs.stamp(os.fstat(fd))
            result = fs.hash_fd(fd)
            require(fs.stamp(os.fstat(fd)) == before and self.record(path) == identity(os.fstat(fd)),
                    'SOURCE_CHANGED', 'File changed while hashing', path=path)
            return result
        finally:
            os.close(fd)

    def resolve(self, path):
        pending = list(path_parts(path.lstrip('/')))
        done, count = [], 0
        while pending:
            part = pending.pop(0)
            current = '/'.join(done + [part])
            st = self.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                count += 1
                require(count <= 40, 'SYMLINK_LOOP', 'Too many rootfs symlinks')
                pending = list(link_destination(done, self.readlink(current))) + pending
                done = []
            else:
                if pending:
                    require(stat.S_ISDIR(st.st_mode), 'INVALID_ROOTFS', 'Non-directory in rootfs path', path=current)
                done.append(part)
        return '/'.join(done)

    def mkdirs(self, path, mode=0o700):
        fd = os.dup(self.fd)
        try:
            for part in path_parts(path):
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
                new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=fd)
                os.close(fd)
                fd = new
                self.pin.check_fd(fd)
            os.fsync(fd)
        finally:
            os.close(fd)

    def write_json(self, path, value, *, create=False):
        data = canonical(value) + b'\n'
        require(len(data) <= MAX_JSON, 'RECORD_TOO_LARGE', 'Manager metadata exceeds its bound')
        with self.parent(path) as (parent, name):
            old = None
            try:
                old = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require(not create and stat.S_ISREG(old.st_mode) and old.st_nlink == 1,
                        'METADATA_CONFLICT', 'Metadata destination is not an owned regular file')
            except FileNotFoundError:
                pass
            temp = '.' + name + '.tmp-' + os.urandom(8).hex()
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            try:
                view = memoryview(data)
                while view:
                    amount = os.write(fd, view)
                    require(amount > 0, 'SHORT_WRITE', 'Metadata write made no progress')
                    view = view[amount:]
                os.fsync(fd)
                if old is None:
                    fs.rename_noreplace(parent, temp, parent, name)
                else:
                    now = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    require((now.st_dev, now.st_ino) == (old.st_dev, old.st_ino),
                            'METADATA_CHANGED', 'Metadata destination changed')
                    os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(fd)

    def list(self, path=''):
        with self.directory(path) as fd:
            return sorted(os.listdir(fd))

    def rename(self, source, destination):
        self.pin.recheck()
        with self.parent(source) as (src, a), self.parent(destination) as (dst, b):
            fs.rename_noreplace(src, a, dst, b)
            os.fsync(src)
            os.fsync(dst)
        self.pin.recheck()


def require_userdata(pin):
    v = pin.volume
    require(v['id'] == 'userdata' and v['fs_type'] == 'ext4' and not v.get('fscrypt_feature'),
            'LINUX_USERDATA_REQUIRED', 'Systems requires mounted, unencrypted ext4 userdata')
    if pin.context.mode == 'device':
        require(v.get('size_bytes') in USERDATA_SIZES and v.get('path') == '/dev/mmcblk0p54',
                'WRONG_USERDATA', 'The mounted filesystem is not the admitted userdata partition')
        foreign = [m for m in fs.all_namespace_mounts()
                   if m['devno'] == v['device'] and
                   (m['mountpoint'] != v['mountpoint'] or m['root'] != '/')]
        require(not foreign and not pin.context.nested_mounts(v, '/'),
                'FILESYSTEM_IN_USE', 'Unmount nested or other-namespace userdata mounts first')


def boot_identity(context):
    if context.mode == 'fixture':
        return {'profile': PROFILE_ID, 'boot_sha256': BOOT_SHA256, 'fixture': True}
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
    from storage import DeviceContext as BlockContext
    from common import RecoveryError, stamp as block_stamp
    try:
        block = BlockContext()
        block.assert_recovery()
        part = block.partition('boot')
    except RecoveryError as exc:
        # The block helper's refusal reaches the interface as a result, not as a traceback.
        fail(exc.code, str(exc), **getattr(exc, 'details', {}))
    require(part['size_bytes'] == BOOT_BYTES and part.get('partition_number') == 41,
            'BOOT_PROFILE_MISMATCH', 'BOOT geometry differs from the supported boot profile')
    h = hashlib.sha256()
    image = bytearray()
    with block.open_partition(part) as source:
        while len(image) < BOOT_BYTES:
            data = source.read(min(CHUNK, BOOT_BYTES - len(image)))
            require(data, 'SHORT_READ', 'Cannot read the complete current BOOT profile')
            h.update(data)
            image += data
        require(not source.read(1) and block_stamp(source.fileno()) == part['stamp'],
                'BOOT_CHANGED', 'BOOT changed during profile admission')
    # 2026-11-10: a BOOT is admitted by what it holds, not by a list of tested hashes: a complete
    # Linux BOOT for this phone whose kernel release is the one the platform modules were built for.
    release = None if h.hexdigest() in ALLOWED_BOOTS else bootimage.linux_release(bytes(image))
    if h.hexdigest() not in ALLOWED_BOOTS:
        expected = platform_kernel_release(context)
        # One sentence that says what to do (2026-11-11: with Android's kernel on BOOT the plan of a new
        # system stopped here at its last step, in English); what was looked for goes to the details.
        require(release is not None and release == expected, 'BOOT_PROFILE_MISMATCH',
                'BOOT holds no Linux kernel for this phone. Flash a Linux BOOT image first: Install, Flash a partition, boot',
                expected=expected, found=release or 'no such kernel')
    return {'profile': PROFILE_ID, 'boot_sha256': h.hexdigest(), 'fixture': False,
            'device_identity': block.device['identity'], 'kernel_release': release}


def linux_boot_kept(context, sha256=None):
    """A Linux BOOT kept on cache whose kernel release is the one the platform modules were built
    for: the one with SHA256 when one is named, else the newest. What Android's BOOT replaced, or
    what was downloaded. A fixture names its own (linux_boot_cache)."""
    if context.mode == 'fixture':
        kept = [dict(item, **{'from': 'cache', 'path': ''}) for item in context.config().get('linux_boot_cache') or ()]
    else:
        import saved_boot
        expected = platform_kernel_release(context)
        try:
            found = saved_boot.saved_boots(types.SimpleNamespace(fs=context))
        except (OSError, ValueError, fs.FilesystemError):
            return None
        kept = [{'sha256': item['sha256'], 'release': item['release'], 'from': 'cache', 'path': item['path']} for item in found
                if item['kind'] == 'linux' and item['release'] == expected and item['bytes'] == BOOT_BYTES
                and re.fullmatch(r'[0-9a-f]{64}', item['sha256'])]
    for item in kept:
        if sha256 is None or item['sha256'] == sha256:
            return item
    return None


def linux_boot_offered(context):
    """The Linux BOOT the catalog names for this recovery's platform modules: the mainline project's
    release. None when the catalog names none that fits. A fixture names its own (linux_boot_release)."""
    if context.mode == 'fixture':
        offer = context.config().get('linux_boot_release')
        return dict(offer, **{'from': 'download'}) if isinstance(offer, dict) else None
    offer = catalog_builtin(context).get('linux_boot')
    if not isinstance(offer, dict) or offer.get('release') != platform_kernel_release(context) or offer.get('size') != BOOT_BYTES \
            or not re.fullmatch(r'[0-9a-f]{64}', str(offer.get('sha256', ''))) or not re.fullmatch(r'https://[^\s]+', str(offer.get('url', ''))):
        return None
    return {'sha256': offer['sha256'], 'release': offer['release'], 'from': 'download', 'url': offer['url'], 'size': offer['size'],
            'version': str(offer.get('version', ''))[:24]}


def network_joined(context):
    if context.mode == 'fixture':
        return context.config().get('offline') is not True
    try:
        return json.loads(WIFI_STATE.read_text()).get('connected') is True
    except (OSError, ValueError):
        return False


def linux_boot_choice(context):
    """Where a new system takes its Linux BOOT from, and whether the network is missing for it.
    First the release this recovery was made for: kept on cache, else downloaded. Only when that
    cannot be had, another Linux BOOT kept on this phone, which may be older (2026-11-11, on the
    owner's phone: a BOOT kept since kernel 107 was taken before the release with kernel 122, and
    the systems installed with it did not start as they should)."""
    release = linux_boot_offered(context)
    if release:
        kept = linux_boot_kept(context, release['sha256'])
        if kept:
            return kept, False
        if network_joined(context):
            return release, False
    older = linux_boot_kept(context)
    if older:
        return dict(older, older=release is not None), False
    return None, release is not None


def boot_for_linux(context, fresh=False):
    """The BOOT a new Linux system starts from. The one there when it holds the Linux kernel of this
    phone; a clean installation (FRESH) still takes the release this recovery was made for when
    the BOOT there is another one. With Android's kernel on BOOT (2026-11-11, the owner: "I flashed
    BOOT before installing Mobian myself: make this simpler") the installation takes a Linux BOOT
    with it. It is fetched before anything is erased and written when the system is in place; the
    plan is bound to that image by its SHA-256."""
    android = context.mode == 'fixture' and context.config().get('android_boot')
    try:
        if android:
            fail('BOOT_PROFILE_MISMATCH', 'BOOT holds no Linux kernel for this phone. Flash a Linux BOOT image first: Install, Flash a partition, boot')
        there = boot_identity(context)
    except fs.FilesystemError as error:
        if getattr(error, 'code', '') != 'BOOT_PROFILE_MISMATCH':
            raise
        offer, offline = linux_boot_choice(context)
        require(offer is not None or not offline, 'LINUX_BOOT_NEEDS_NETWORK',
                'BOOT holds the kernel of Android, and this phone keeps no Linux BOOT: join Wi-Fi so that it can be downloaded, or flash one in Flash a partition')
        if offer is None:
            raise
        return {'profile': PROFILE_ID, 'boot_sha256': offer['sha256'], 'fixture': context.mode == 'fixture',
                'kernel_release': offer['release'], 'write': True}
    if fresh:
        offer, _ = linux_boot_choice(context)
        if offer and not offer.get('older') and offer['sha256'] != boot_now(context, there):
            return {'profile': PROFILE_ID, 'boot_sha256': offer['sha256'], 'fixture': context.mode == 'fixture',
                    'kernel_release': offer['release'], 'write': True}
    return there


def boot_now(context, identity):
    """The SHA-256 of the BOOT there; a fixture names it itself (boot_sha256), else it is the constant every fixture has."""
    return context.config().get('boot_sha256', identity['boot_sha256']) if context.mode == 'fixture' else identity['boot_sha256']


def boot_outdated(context):
    """Whether BOOT holds a Linux kernel for this phone that is not the release this recovery was
    made for, while that release can be had: a clean installation then writes it."""
    try:
        if context.mode == 'fixture' and context.config().get('android_boot'):
            return False
        there = boot_identity(context)
    except fs.FilesystemError:
        return False
    offer, _ = linux_boot_choice(context)
    return bool(offer and not offer.get('older') and offer['sha256'] != boot_now(context, there))


def linux_boot_fetched(context, boot, progress):
    """The Linux BOOT a plan is bound to, on cache: its path there. One that is not there is
    downloaded from the catalog's address and kept, its SHA-256 checked, before anything is erased."""
    kept = linux_boot_kept(context, boot['boot_sha256'])
    if kept:
        return kept.get('path', '')
    offer = linux_boot_offered(context)
    require(offer is not None and offer['sha256'] == boot['boot_sha256'], 'PLAN_STALE', 'The Linux BOOT of this plan is no longer offered: plan again')
    if context.mode == 'fixture':
        progress('image-boot-download', 1, 1)
        return '/a22x-recovery/saved-boot/fixture-linux.img'
    import saved_boot
    work = Path('/run/a22x-recovery/linux-boot.partial')
    try:
        fetch(context, offer['url'], work, offer['size'], offer['sha256'], lambda phase, done=0, total=0: progress('image-boot-download', done, total))
        image = work.read_bytes()
    finally:
        with contextlib.suppress(OSError):
            work.unlink()
    require(hashlib.sha256(image).hexdigest() == offer['sha256'] and bootimage.linux_release(image) == offer['release'],
            'DOWNLOAD_MISMATCH', 'The downloaded Linux BOOT is not the one the catalog names')
    name = 'linux-' + re.sub(r'[^A-Za-z0-9._+-]', '_', offer['release'])[:40] + '-' + offer['sha256'][:12] + '.img'
    return saved_boot.save_on_cache(types.SimpleNamespace(fs=context), name, image, {'kind': 'linux', 'release': offer['release'],
                                    'version': offer.get('version', '')}, lambda *unused: None)


def linux_boot_written(context, path, boot, progress):
    """The Linux BOOT from cache written to BOOT the way Flash a partition writes it: the BOOT there
    is kept on cache first, the partition is read back. The lease this helper holds is lent to it."""
    if context.mode == 'fixture':
        spec = context.config()
        spec['android_boot'], spec['boot_written'] = False, boot['boot_sha256']
        context.config_path.write_text(json.dumps(spec))
        return {'sha256': boot['boot_sha256'], 'kept_boot': 'fixture'}
    import flash
    import install as installer
    from operation_lock import ENV_FD
    before = os.environ.get(ENV_FD)
    os.environ[ENV_FD] = str(context.lock_fd)
    try:
        writer = installer.DeviceContext()
        cache = writer.fs.volume('cache')
        if not cache['mounted']:
            writer.fs.mount('cache', 'ro', True, cache['volume_token'])
            cache = writer.fs.volume('cache')
        args = types.SimpleNamespace(target='boot', volume='cache', volume_token=cache['volume_token'], path=path, member=None,
                                     image_bytes=None, confirm=True)
        checked = flash.check(writer, args, lambda *unused: None)
        require(checked['image']['sha256'] == boot['boot_sha256'], 'PLAN_STALE', 'The Linux BOOT on cache changed since planning')
        args.expected_sha256, args.expected_identity = checked['image']['sha256'], checked['image']['identity']
        written = flash.write(writer, args, lambda *unused: None)
    finally:
        if before is None:
            os.environ.pop(ENV_FD, None)
        else:
            os.environ[ENV_FD] = before
    boot_identity(context)  # what a start of the system asks of BOOT holds now
    return {'sha256': written['sha256'], 'kept_boot': written.get('kept_boot')}


def platform_kernel_release(context):
    """The kernel release the platform modules in this recovery were built for."""
    try:
        release = fs.regular_json(profile_path(context), MAX_JSON).get('kernel_release')
    except (OSError, ValueError, fs.FilesystemError):
        release = None
    require(isinstance(release, str) and release, 'PLATFORM_PROFILE_MISSING', 'The platform profile names no kernel release')
    return release


def profile_path(context):
    return context.root / 'distro-platform.json' if context.mode == 'fixture' else PROFILE_PATH


def load_profile(context):
    path = profile_path(context)
    require(path.exists(), 'PLATFORM_PROFILE_MISSING',
            'The recovery image has no audited platform adapter profile')
    raw = fs.regular_json(path, MAX_JSON)
    check_seal(raw)
    require(raw.get('schema') == 'a22x.distro.platform.v1' and raw.get('id') == PROFILE_ID
            and isinstance(raw.get('kernel_release'), str) and raw.get('architecture') == 'aarch64'
            and all(isinstance(item, str) and re.fullmatch(r'[0-9a-f]{64}', item) for item in raw.get('boot_sha256_allowed', ()))
            and raw.get('init_system') in ('systemd', 'openrc'),
            'PLATFORM_PROFILE_MISMATCH', 'Unsupported kernel/userspace platform profile')
    entries = raw.get('entries')
    require(isinstance(entries, list) and 1 <= len(entries) <= MAX_PROFILE_ENTRIES,
            'INVALID_PLATFORM_PROFILE', 'A bounded platform file inventory is required')
    require(raw.get('roles', {}).get('firmware') and raw.get('roles', {}).get('adapter')
            and (raw.get('roles', {}).get('modules') or raw.get('modules_built_in') is True),
            'INVALID_PLATFORM_PROFILE', 'Firmware, device services and module policy are required')
    seen = set()
    for item in entries:
        path_parts(item['path'])
        require(item['path'] not in seen and item.get('kind') in ('file', 'directory', 'symlink'),
                'INVALID_PLATFORM_PROFILE', 'Invalid or duplicate platform entry')
        seen.add(item['path'])
        if item['kind'] == 'file':
            fs.require_hash(item['sha256'])
    commands = raw.get('commands', ['/bin/sh'])
    modules = raw.get('python3_modules', [])
    require(isinstance(commands, list) and 1 <= len(commands) <= 64
            and all(isinstance(p, str) and p.startswith('/') for p in commands),
            'INVALID_PLATFORM_PROFILE', 'Invalid adapter command requirements')
    for command in commands:
        logical_path(command)
    require(isinstance(modules, list) and len(modules) <= 64 and
            all(isinstance(m, str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', m) for m in modules)
            and (not modules or '/usr/bin/python3' in commands),
            'INVALID_PLATFORM_PROFILE', 'Invalid adapter Python requirements')
    return raw


def _walk(tree, base, limit=MAX_ENTRIES):
    stack, count = [base], 0
    while stack:
        path = stack.pop()
        st = tree.lstat(path)
        count += 1
        require(count <= limit, 'TREE_TOO_LARGE', 'Directory inventory exceeds the bounded entry count')
        require(st.st_dev == os.fstat(tree.fd).st_dev, 'CROSS_MOUNT_REJECTED', 'Nested filesystem found')
        yield path, st
        if stat.S_ISDIR(st.st_mode):
            children = tree.list(path)
            require(len(children) <= limit - count, 'TREE_TOO_LARGE', 'Directory has too many children')
            for name in reversed(children):
                path_parts(name)
                stack.append(path + '/' + name)


def capture_profile(context, modules, firmware, adapters, init_system, modules_built_in=False,
                    commands=None, python_modules=None):
    require(bool(modules) != bool(modules_built_in), 'MODULE_POLICY_REQUIRED',
            'Specify module trees or explicitly audited built-in-only modules')
    require(firmware and adapters, 'PLATFORM_PATHS_REQUIRED', 'Specify firmware and device adapter paths')
    boot = boot_identity(context)
    roles, found = {}, {}
    with fs.PinnedVolume(context, 'userdata') as pin, Tree(pin) as tree:
        require_userdata(pin)
        for role, paths in (('modules', modules), ('firmware', firmware), ('adapter', adapters)):
            roles[role] = [logical_path(p) for p in paths]
            for path in roles[role]:
                for name, st in _walk(tree, path, MAX_PROFILE_ENTRIES):
                    require(len(found) < MAX_PROFILE_ENTRIES, 'PROFILE_TOO_LARGE', 'Too many platform files')
                    if name in found:
                        continue
                    item = {'path': name, 'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid}
                    if stat.S_ISREG(st.st_mode):
                        item.update(kind='file', bytes=st.st_size, sha256=tree.hash(name))
                    elif stat.S_ISDIR(st.st_mode):
                        item['kind'] = 'directory'
                    elif stat.S_ISLNK(st.st_mode):
                        item.update(kind='symlink', target=tree.readlink(name))
                        link_destination(path_parts(name)[:-1], item['target'])
                    else:
                        fail('PLATFORM_SPECIAL_FILE', 'Platform profiles may not contain special files', path=name)
                    found[name] = item
                    if len(found) % 64 == 0:
                        pin.recheck()
        pin.recheck()
    profile = seal({'schema': 'a22x.distro.platform.v1', 'id': PROFILE_ID,
                    'architecture': 'aarch64', 'kernel_release': KERNEL_RELEASE,
                    'boot_sha256_allowed': sorted(ALLOWED_BOOTS), 'init_system': init_system,
                    'modules_built_in': modules_built_in, 'roles': roles,
                    'commands': sorted(set(['/bin/sh'] + (commands or []))),
                    'python3_modules': sorted(set(python_modules or [])),
                    'entries': [found[n] for n in sorted(found)],
                    'qualification': 'exact-current-platform-files; target distro boot still needs acceptance'})
    require(len(canonical(profile)) <= MAX_JSON, 'PROFILE_TOO_LARGE', 'Profile exceeds its JSON size bound')
    return {'profile': profile, 'profile_sha256': digest(profile), 'files': len(found),
            'note': 'Review the selected adapter scope before embedding this profile in recovery.'}


def system_path(tree, path):
    path = tree.resolve(path)
    require(path.split('/')[0] not in RESERVED | RUNTIME_DIRS,
            'SHARED_RUNTIME_DEPENDENCY', 'System startup files may not resolve into shared home or runtime directories', path=path)
    return path


def elf_info(tree, path):
    path = system_path(tree, path)
    fd = tree.regular(path)
    try:
        st = os.fstat(fd)
        head = os.pread(fd, 64, 0)
        require(len(head) == 64 and head[:6] == b'\x7fELF\x02\x01'
                and struct.unpack_from('<H', head, 18)[0] == 183,
                'WRONG_ARCHITECTURE', 'A native little-endian ARM64 executable is required', path=path)
        phoff = struct.unpack_from('<Q', head, 32)[0]
        entsize, count = struct.unpack_from('<HH', head, 54)
        require(entsize == 56 and 0 < count <= 128 and phoff + count * entsize <= st.st_size,
                'INVALID_ELF', 'ELF program headers are invalid', path=path)
        loads, interp, dynamic = [], None, None
        for index in range(count):
            p = struct.unpack('<IIQQQQQQ', os.pread(fd, 56, phoff + index * 56))
            typ, unused, offset, address, unused, size, memsize, unused = p
            require(offset + size <= st.st_size, 'INVALID_ELF', 'ELF segment exceeds its file', path=path)
            if typ == 1:
                loads.append((address, address + size, offset))
            elif typ == 3:
                require(1 < size <= 1024, 'INVALID_ELF', 'Invalid ELF interpreter length')
                data = os.pread(fd, size, offset)
                require(data.endswith(b'\0') and data.count(b'\0') == 1, 'INVALID_ELF', 'Invalid interpreter string')
                interp = data[:-1].decode('utf-8', 'strict')
                require(interp.startswith('/'), 'INVALID_ELF', 'ELF interpreter must be an absolute rootfs path')
            elif typ == 2:
                require(size <= 65536 and size % 16 == 0, 'INVALID_ELF', 'Dynamic table is too large')
                dynamic = os.pread(fd, size, offset)
        needed, rpath, runpath, sonames = [], [], [], []
        if dynamic:
            tags = []
            for i in range(0, len(dynamic), 16):
                tag, value = struct.unpack_from('<QQ', dynamic, i)
                if tag == 0:
                    break
                tags.append((tag, value))
            else:
                fail('INVALID_ELF', 'Dynamic table has no terminator', path=path)
            addresses = [v for tag, v in tags if tag == 5]
            sizes = [v for tag, v in tags if tag == 10]
            if addresses or sizes or any(tag in (1, 14, 15, 29) for tag, unused in tags):
                require(len(addresses) == len(sizes) == 1 and sizes[0] <= 2 * CHUNK,
                        'INVALID_ELF', 'Invalid dynamic string table')
                mappings = [(o + addresses[0] - a) for a, b, o in loads
                            if a <= addresses[0] and addresses[0] + sizes[0] <= b]
                require(len(mappings) == 1, 'INVALID_ELF', 'Cannot map the dynamic string table')
                strings = os.pread(fd, sizes[0], mappings[0])
                for tag, value in tags:
                    if tag in (1, 14, 15, 29):
                        require(value < len(strings) and b'\0' in strings[value:],
                                'INVALID_ELF', 'Dynamic string exceeds its table')
                        end = strings.index(b'\0', value)
                        require(end - value <= MAX_PATH, 'INVALID_ELF', 'Dynamic name or search path is too long')
                        s = strings[value:end].decode('utf-8', 'strict')
                        {1: needed, 14: sonames, 15: rpath, 29: runpath}[tag].append(s)
                require(len(sonames) <= 1 and len(rpath) <= 1 and len(runpath) <= 1,
                        'INVALID_ELF', 'Duplicate dynamic name or search path', path=path)
        return {'path': path, 'sha256': tree.hash(path), 'interpreter': interp,
                'needed': needed, 'soname': sonames[0] if sonames else None,
                'rpath': rpath, 'runpath': runpath}
    finally:
        os.close(fd)


def elf_dependencies(tree, executable):
    # glibc maps direct DT_NEEDED objects before visiting their dependencies
    # (elf/dl-deps.c), and reuses already mapped request names / DT_SONAMEs
    # before searching for another file (elf/dl-load.c). A depth-first walk
    # incorrectly rejects systemd's private core/shared sibling libraries.
    pending, seen, names, parents = [], {}, {}, {}
    defaults = ('lib/aarch64-linux-gnu', 'usr/lib/aarch64-linux-gnu', 'lib64', 'usr/lib64', 'lib', 'usr/lib')

    def mapped(path, request, parent):
        logical = system_path(tree, path)
        if logical not in seen:
            require(len(seen) < 256, 'ELF_DEPENDENCIES_TOO_LARGE', 'Too many required ELF libraries')
            info = elf_info(tree, logical)
            seen[logical] = info
            parents[logical] = parent
            pending.append(logical)
            if info['soname']:
                names.setdefault(info['soname'], logical)
        names.setdefault(request, logical)
        return logical

    def paths(info, kind):
        result = []
        origin = '/' + info['path'].rsplit('/', 1)[0] if '/' in info['path'] else '/'
        for rpaths in info[kind]:
            if rpaths == '':
                continue
            for value in rpaths.split(':'):
                value = value.replace('${ORIGIN}', origin).replace('$ORIGIN', origin)
                require(value.startswith('/') and '$' not in value, 'UNSUPPORTED_ELF_SEARCH_PATH',
                        'Runtime library paths must be absolute or use ORIGIN', path=info['path'])
                result.append(logical_path(value))
        return result

    main = mapped(executable, executable, None)
    interpreter = seen[main]['interpreter']
    if interpreter:
        mapped(interpreter, interpreter, None)
    index = 0
    while index < len(pending):
        logical = pending[index]
        index += 1
        info = seen[logical]
        search = []
        if not info['runpath']:
            # Legacy RPATH follows the first loader's ancestor chain. RUNPATH
            # is used only for its object's direct dependencies, never inherited.
            ancestor = logical
            while ancestor is not None:
                parent_info = seen[ancestor]
                if not parent_info['runpath']:
                    search.extend(paths(parent_info, 'rpath'))
                ancestor = parents[ancestor]
        search.extend(paths(info, 'runpath'))
        search.extend(defaults)
        for name in info['needed']:
            require(name and '/' not in name and name not in ('.', '..'),
                    'INVALID_ELF', 'A dependency contains a path', path=logical)
            if name in names:
                continue
            match = None
            for base in search:
                candidate = base + '/' + name
                try:
                    resolved = tree.resolve(candidate)
                    if stat.S_ISREG(tree.lstat(resolved).st_mode):
                        match = resolved
                        break
                except (FileNotFoundError, NotADirectoryError):
                    pass
            require(match, 'MISSING_RUNTIME_LIBRARY', 'A required rootfs library is missing',
                    executable=logical, library=name)
            mapped(match, name, logical)
    return {name: item['sha256'] for name, item in sorted(seen.items())}


def account_tables(tree):
    users, groups = {}, {}
    for line in tree.read(system_path(tree, 'etc/passwd'), CHUNK).decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        p = line.split(':')
        require(len(p) == 7 and p[2].isdecimal() and p[3].isdecimal(), 'INVALID_ACCOUNTS', 'Invalid passwd record')
        require(p[0] not in users, 'INVALID_ACCOUNTS', 'Duplicate account name')
        users[p[0]] = {'uid': int(p[2]), 'gid': int(p[3]), 'home': p[5]}
    for line in tree.read(system_path(tree, 'etc/group'), CHUNK).decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        p = line.split(':')
        require(len(p) == 4 and p[2].isdecimal(), 'INVALID_ACCOUNTS', 'Invalid group record')
        require(p[0] not in groups, 'INVALID_ACCOUNTS', 'Duplicate group name')
        groups[p[0]] = int(p[2])
    require(users.get('root', {}).get('uid') == 0, 'INVALID_ACCOUNTS', 'A root account is required')
    return users, groups


def home_acl(tree, path, st):
    """Named POSIX ACL IDs also participate in shared-home account compatibility."""
    uids, gids, hashes = set(), set(), {}
    if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
        return uids, gids, hashes
    with tree.parent(path) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                     (os.O_DIRECTORY if stat.S_ISDIR(st.st_mode) else 0), dir_fd=parent)
    try:
        tree.pin.check_fd(fd)
        require(identity(os.fstat(fd)) == identity(st), 'HOME_CHANGED', 'Shared home object changed during ACL inspection')
        for key in ('system.posix_acl_access', 'system.posix_acl_default'):
            try:
                raw = os.getxattr(fd, key)
            except OSError as exc:
                if exc.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
                    continue
                raise
            require(4 <= len(raw) <= MAX_PAX and (len(raw) - 4) % 8 == 0
                    and struct.unpack_from('<I', raw)[0] == 2,
                    'UNSUPPORTED_HOME_ACL', 'Shared home contains an unsupported POSIX ACL format', path=path)
            for offset in range(4, len(raw), 8):
                tag, perm, number = struct.unpack_from('<HHI', raw, offset)
                require(tag in (1, 2, 4, 8, 16, 32) and perm <= 7,
                        'UNSUPPORTED_HOME_ACL', 'Invalid POSIX ACL entry', path=path)
                if tag in (2, 8):
                    require(number < 0xffffffff, 'UNSUPPORTED_HOME_ACL', 'Named ACL entry has no numeric ID')
                    (uids if tag == 2 else gids).add(number)
            hashes[key] = hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)
    return uids, gids, hashes


def home_binding(live, target):
    old_users, old_groups = account_tables(live)
    new_users, new_groups = account_tables(target)
    home = live.lstat('home')
    require(stat.S_ISDIR(home.st_mode), 'SHARED_HOME_UNAVAILABLE', '/home must be a real directory on userdata')
    uids, gids, metadata, links = set(), set(), hashlib.sha256(), {}
    count = 0
    for path, st in _walk(live, 'home'):
        count += 1
        uids.add(st.st_uid)
        gids.add(st.st_gid)
        acl_uids, acl_gids, acl_hashes = home_acl(live, path, st)
        uids.update(acl_uids)
        gids.update(acl_gids)
        metadata.update(canonical([path, identity(st, live.readlink(path) if stat.S_ISLNK(st.st_mode) else None),
                                   st.st_nlink, acl_hashes]))
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            key = (st.st_dev, st.st_ino)
            seen, expected = links.get(key, (0, st.st_nlink))
            require(st.st_nlink == expected, 'HOME_CHANGED', 'Home hardlink count changed during inspection')
            links[key] = seen + 1, expected
        if count % 256 == 0:
            live.pin.recheck()
    require(all(seen == expected for seen, expected in links.values()), 'HOME_CROSS_ROOT_HARDLINK',
            'Shared home has a hardlink outside /home; separate that alias before switching systems')
    required = {name: item for name, item in old_users.items()
                if item['uid'] in uids or item['home'] == '/home' or item['home'].startswith('/home/')}
    for name, item in required.items():
        require(new_users.get(name) == item, 'HOME_ACCOUNT_MISMATCH',
                'Shared home requires the same user name, UID, primary GID and home path',
                account=name, expected=item, actual=new_users.get(name))
        require({n for n, v in old_users.items() if v['uid'] == item['uid']} ==
                {n for n, v in new_users.items() if v['uid'] == item['uid']},
                'HOME_UID_ALIAS_MISMATCH', 'A shared UID is assigned to a different account', uid=item['uid'])
    for uid in uids:
        require(any(v['uid'] == uid for v in required.values()), 'HOME_UNKNOWN_UID',
                'Shared files have an owner absent from the current account database', uid=uid)
    for gid in gids:
        old_names = {name for name, value in old_groups.items() if value == gid}
        new_names = {name for name, value in new_groups.items() if value == gid}
        require(old_names and old_names == new_names, 'HOME_GROUP_MISMATCH',
                'Shared files require the same group names and GIDs', gid=gid,
                current=sorted(old_names), target=sorted(new_names))
    return {'home_inode': identity(home), 'entries': count, 'metadata_sha256': metadata.hexdigest(),
            'accounts': required, 'uids': sorted(uids), 'gids': sorted(gids)}


def validate_fstab(tree, uuid):
    try:
        text = tree.read(system_path(tree, 'etc/fstab'), CHUNK).decode('utf-8')
    except FileNotFoundError:
        return {'entries': 0, 'sha256': None}
    rows = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        p = stripped.split()
        require(len(p) >= 4, 'INVALID_FSTAB', 'Invalid fstab row')
        source, target, kind, options = p[:4]
        source, target = fs.mount_unescape(source), fs.mount_unescape(target)
        options = options.split(',')
        if kind == 'swap':
            require(target in ('none', 'swap') and source.startswith('/'),
                    'SWAP_TARGET_FORBIDDEN', 'Only an explicit regular swap file in this OS slot is admitted')
            swap = system_path(tree, source)
            st = tree.lstat(swap)
            require(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
                    'SWAP_TARGET_FORBIDDEN', 'Swap must be a private regular file in the selected rootfs')
            rows += 1
            continue
        require(target.startswith('/') and all(ord(c) >= 32 for c in target) and '\0' not in source,
                'INVALID_FSTAB', 'Mount targets must be absolute paths without control characters')
        normalized = []
        for part in target.split('/'):
            if part in ('', '.'):
                continue
            if part == '..':
                require(normalized, 'INVALID_FSTAB', 'Mount target escapes the filesystem root')
                normalized.pop()
            else:
                normalized.append(part)
        target = '/' + '/'.join(normalized)
        require(target != '/home' and not target.startswith('/home/'), 'SEPARATE_HOME_FORBIDDEN',
                'The bundle may not mask any part of the shared /home tree')
        forbidden = ('/' + MANAGER, '/etc', '/usr', '/bin', '/sbin', '/lib', '/lib64', '/opt', '/root', '/var/lib', '/var/spool')
        require(not any(target == path or target.startswith(path + '/') for path in forbidden),
                'CRITICAL_MOUNT_FORBIDDEN', 'fstab may not mask admitted system or manager files', mountpoint=target)
        if target == '/':
            allowed = {'/dev/mmcblk0p54', 'PARTLABEL=userdata'}
            if uuid:
                allowed.add('UUID=' + uuid)
            require(source in allowed and kind == 'ext4', 'ROOT_FSTAB_MISMATCH',
                    'fstab root must refer to this ext4 userdata filesystem')
        else:
            virtual = {'tmpfs': {'/tmp', '/run', '/dev/shm', '/var/tmp'}, 'proc': {'/proc'},
                       'sysfs': {'/sys'}, 'devpts': {'/dev/pts'}, 'devtmpfs': {'/dev'}}
            if kind in virtual:
                require(target in virtual[kind], 'VIRTUAL_MOUNT_FORBIDDEN',
                        'Virtual filesystems are allowed only at the documented runtime mount points', mountpoint=target)
            else:
                require(any(target == base or target.startswith(base + '/') for base in ('/mnt', '/media'))
                        and ('noauto' in options or 'nofail' in options), 'REQUIRED_FOREIGN_MOUNT',
                        'Other mounts must be optional and confined to /mnt or /media', mountpoint=target)
        rows += 1
    return {'entries': rows, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def validate_root(tree, profile, uuid=None, platform=True, shared_home=False):
    """A bootable root for this phone. PLATFORM=False skips the exact module and firmware files,
    for a system adopted as it was: it already booted here, and its files are left untouched.
    SHARED_HOME: a saved system that was live before keeps no /home of its own (the shared one
    stays at the root of userdata); switching back to it stopped at a missing 'home' (2026-11-09)."""
    for path in ('dev', 'proc', 'sys', 'run', 'etc', 'usr', 'var', 'tmp') + (() if shared_home else ('home',)):
        require(stat.S_ISDIR(tree.lstat(path).st_mode), 'INVALID_ROOTFS',
                'A required rootfs directory is missing or symlinked', path=path)
    init = system_path(tree, 'sbin/init')
    if profile['init_system'] == 'systemd':
        require(init.rsplit('/', 1)[-1] == 'systemd', 'INIT_PROFILE_MISMATCH',
                'This device adapter profile requires systemd')
    for name in ('sbin/init', 'bin/sh'):
        require(tree.lstat(system_path(tree, name)).st_mode & 0o111, 'INIT_NOT_EXECUTABLE',
                'Init and shell must be executable', path=name)
    elf = elf_dependencies(tree, 'sbin/init')
    elf.update(elf_dependencies(tree, 'bin/sh'))
    command_paths = {}
    for command in profile.get('commands', ['/bin/sh']):
        # Debian and Ubuntu keep kmod tools in /usr/sbin even with a merged /usr.
        location = command
        if command.startswith('/usr/bin/') and not tree.exists(command.lstrip('/')):
            location = '/usr/sbin/' + command[len('/usr/bin/'):]
        resolved = system_path(tree, location)
        require(tree.lstat(resolved).st_mode & 0o111, 'ADAPTER_COMMAND_NOT_EXECUTABLE',
                'A device adapter runtime command is not executable', path=command)
        elf.update(elf_dependencies(tree, location))
        command_paths[command] = resolved
    python_files = {}
    if profile.get('python3_modules'):
        py = elf_info(tree, '/usr/bin/python3')
        match = re.search(r'python(3\.[0-9]+)', py['path'])
        if not match:
            match = re.search(r'libpython(3\.[0-9]+)', ' '.join(py['needed']))
        require(match, 'PYTHON_RUNTIME_UNIDENTIFIED', 'Cannot establish the adapter Python standard-library version')
        version = match[1]
        bases = ['usr/lib/python' + version, 'usr/local/lib/python' + version]
        bases = [b for b in bases if tree.exists(b) and stat.S_ISDIR(tree.lstat(b).st_mode)]
        require(len(bases) == 1, 'PYTHON_RUNTIME_UNIDENTIFIED', 'One matching full Python standard library is required')
        base = bases[0]
        # These are CPython core modules. Other requested modules need actual
        # source/package or native-extension payloads; imported code is never run.
        core = {'sys', 'os', 'time', 'errno', 'posix', '_io', '_stat', '_thread', 'builtins'}
        needed = set(profile['python3_modules']) - core
        if 'struct' in needed:
            needed.add('_struct')
        native = tree.list(base + '/lib-dynload') if tree.exists(base + '/lib-dynload') else []
        for module in sorted(needed):
            plain = [base + '/' + module + '.py', base + '/' + module + '/__init__.py']
            found = next((p for p in plain if tree.exists(p)), None)
            if found:
                resolved = system_path(tree, found)
                python_files[resolved] = tree.hash(resolved)
                continue
            candidates = [base + '/lib-dynload/' + n for n in native
                          if n == module + '.so' or n.startswith(module + '.cpython-') and n.endswith('.so')]
            require(len(candidates) == 1, 'PYTHON_MODULE_MISSING',
                    'A standard-library module used by the device adapter is missing', module=module)
            elf.update(elf_dependencies(tree, candidates[0]))
            python_files[candidates[0]] = tree.hash(system_path(tree, candidates[0]))
    checked = []
    for index, item in enumerate(profile['entries'] if platform else ()):
        path = item['path']
        st = tree.lstat(path)
        require(stat.S_IMODE(st.st_mode) == item['mode'] and st.st_uid == item['uid'] and st.st_gid == item['gid'],
                'PLATFORM_METADATA_MISMATCH', 'Platform file permissions or owner differ', path=path)
        if item['kind'] == 'file':
            require(stat.S_ISREG(st.st_mode) and st.st_size == item['bytes'] and tree.hash(path) == item['sha256'],
                    'PLATFORM_FILE_MISMATCH', 'Required kernel, firmware or device adapter file differs', path=path)
            if st.st_mode & 0o111:
                fd = tree.regular(path)
                try:
                    magic = os.read(fd, 4)
                finally:
                    os.close(fd)
                if magic == b'\x7fELF':
                    elf.update(elf_dependencies(tree, path))
        elif item['kind'] == 'symlink':
            require(stat.S_ISLNK(st.st_mode) and tree.readlink(path) == item['target'],
                    'PLATFORM_LINK_MISMATCH', 'Required platform symlink differs', path=path)
        else:
            require(stat.S_ISDIR(st.st_mode), 'PLATFORM_DIRECTORY_MISMATCH', 'Platform directory is missing', path=path)
        checked.append(path)
        if index % 64 == 0:
            tree.pin.recheck()
    users, unused = account_tables(tree)
    require(tree.exists('etc/os-release') or tree.exists('usr/lib/os-release'),
            'OS_IDENTITY_MISSING', 'The rootfs has no os-release identity')
    fstab = validate_fstab(tree, uuid)
    return {'init': init, 'elf_sha256': elf, 'platform_files': len(checked),
            'platform_profile_sha256': digest(profile), 'accounts_sha256': digest(users),
            'fstab': fstab, 'adapter_commands': command_paths, 'python_files': python_files,
            'bundle_programs_executed': False, 'hardware_boot_tested': False}


class BoundedTarInfo(tarfile.TarInfo):
    @classmethod
    def frombuf(cls, buf, encoding, errors):
        value = super().frombuf(buf, encoding, errors)
        require(0 <= value.size <= MAX_FILE, 'ARCHIVE_MEMBER_TOO_LARGE', 'Archive member exceeds the size limit')
        if value.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            require(value.size <= MAX_PAX, 'ARCHIVE_METADATA_TOO_LARGE', 'Tar metadata exceeds its bound')
        require(value.type != tarfile.GNUTYPE_SPARSE, 'UNSUPPORTED_SPARSE_ARCHIVE',
                'Convert legacy GNU sparse archives to an ordinary prepared rootfs tar')
        return value

    def _proc_pax(self, tar):
        with self._metadata(tar):
            value = super()._proc_pax(tar)
            require(not any(k.startswith('GNU.sparse.') for k in value.pax_headers),
                    'UNSUPPORTED_SPARSE_ARCHIVE', 'Sparse tar metadata is not admitted')
            require(pax_bytes(tar.pax_headers) <= MAX_PAX and pax_bytes(value.pax_headers) <= MAX_PAX,
                    'ARCHIVE_METADATA_TOO_LARGE', 'Inherited tar metadata exceeds its per-member bound')
            return value

    def _proc_gnulong(self, tar):
        with self._metadata(tar):
            return super()._proc_gnulong(tar)

    @contextlib.contextmanager
    def _metadata(self, tar):
        depth = getattr(tar, '_a22x_metadata_depth', 0) + 1
        require(depth <= MAX_METADATA_CHAIN, 'ARCHIVE_METADATA_CHAIN', 'Too many chained tar metadata headers')
        tar._a22x_metadata_depth = depth
        tar._a22x_metadata_bytes = getattr(tar, '_a22x_metadata_bytes', 0) + self.size + 512
        require(tar._a22x_metadata_bytes <= MAX_ARCHIVE_METADATA,
                'ARCHIVE_METADATA_TOO_LARGE', 'Aggregate tar metadata exceeds its budget')
        try:
            yield
        finally:
            tar._a22x_metadata_depth -= 1

    def _proc_gnusparse_00(self, next, raw_headers):
        fail('UNSUPPORTED_SPARSE_ARCHIVE', 'GNU sparse archive metadata is not admitted')

    def _proc_gnusparse_01(self, next, pax_headers):
        fail('UNSUPPORTED_SPARSE_ARCHIVE', 'GNU sparse archive metadata is not admitted')

    def _proc_gnusparse_10(self, next, pax_headers, tar):
        fail('UNSUPPORTED_SPARSE_ARCHIVE', 'GNU sparse archive metadata is not admitted')


class ArchiveReader:
    def __init__(self, fd):
        self.fd, self.offset, self.hash = fd, 0, hashlib.sha256()

    def read(self, amount=-1):
        amount = CHUNK if amount < 0 else min(amount, CHUNK)
        data = os.pread(self.fd, amount, self.offset)
        self.offset += len(data)
        require(self.offset <= MAX_ARCHIVE, 'ARCHIVE_TOO_LARGE', 'Compressed rootfs archive is too large')
        self.hash.update(data)
        return data


def pax_bytes(headers):
    return sum(len(str(key).encode('utf-8', 'surrogateescape')) +
               len(str(value).encode('utf-8', 'surrogateescape')) + 64 for key, value in headers.items())


def tar_attributes(item):
    require(0 <= item.uid <= 0x7ffffffe and 0 <= item.gid <= 0x7ffffffe and 0 <= item.mode <= 0o7777,
            'INVALID_ARCHIVE_METADATA', 'Invalid numeric file owner or permissions')
    require(0 <= item.mtime < 2**40, 'INVALID_ARCHIVE_METADATA', 'Invalid file timestamp')
    attributes = {}
    for key, value in item.pax_headers.items():
        require(not key.startswith(('GNU.sparse.', 'SCHILY.acl.')), 'UNSUPPORTED_ARCHIVE_METADATA',
                'ACL and sparse tar extensions need conversion before import', field=key)
        if key.startswith('SCHILY.xattr.'):
            name = key[len('SCHILY.xattr.'):]
            require(name.startswith('user.') or name == 'security.capability',
                    'UNSUPPORTED_ARCHIVE_XATTR', 'Only user xattrs and file capabilities are admitted', attribute=name)
            data = value.encode('utf-8', 'surrogateescape')
            require(len(data) <= 65536 and len(name) <= 255, 'INVALID_ARCHIVE_XATTR', 'Extended attribute is too large')
            attributes[name] = data
    require(len(attributes) <= 64 and sum(map(len, attributes.values())) <= 65536,
            'INVALID_ARCHIVE_XATTR', 'Extended attributes exceed their bound')
    return attributes


def apply_metadata(tree, path, item, attributes):
    with tree.parent(path) as (parent, name):
        os.chown(name, item.uid, item.gid, dir_fd=parent, follow_symlinks=False)
        if item.issym():
            require(not attributes, 'UNSUPPORTED_LINK_XATTR', 'Symlink xattrs are not supported')
            os.utime(name, ns=(int(item.mtime * 10**9),) * 2, dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
            return
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                     (os.O_DIRECTORY if item.isdir() else 0), dir_fd=parent)
        try:
            tree.pin.check_fd(fd)
            os.fchmod(fd, item.mode)
            for key, value in attributes.items():
                os.setxattr(fd, key, value)
            os.utime(fd, ns=(int(item.mtime * 10**9),) * 2)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)


def scan_archive(fd, compression, expected_sha, target=None):
    before = fs.stamp(os.fstat(fd))
    require(before[3] <= MAX_ARCHIVE, 'ARCHIVE_TOO_LARGE', 'Rootfs archive exceeds the supported bound')
    reader = ArchiveReader(fd)
    entries, total, links, dirs, attributes = {}, 0, [], [], {}
    with tarfile.open(fileobj=reader, mode='r|gz' if compression == 'gzip' else 'r|',
                      tarinfo=BoundedTarInfo, encoding='utf-8', errors='surrogateescape') as archive:
        for item in archive:
            require(0 <= item.size <= MAX_FILE, 'ARCHIVE_MEMBER_TOO_LARGE',
                    'Resolved PAX file size exceeds the per-file bound')
            metadata_size = pax_bytes(item.pax_headers)
            require(metadata_size <= MAX_PAX, 'ARCHIVE_METADATA_TOO_LARGE', 'Resolved tar metadata exceeds its bound')
            archive._a22x_metadata_bytes = getattr(archive, '_a22x_metadata_bytes', 0) + metadata_size + len(item.name.encode('utf-8', 'surrogateescape')) + 128
            require(archive._a22x_metadata_bytes <= MAX_ARCHIVE_METADATA,
                    'ARCHIVE_METADATA_TOO_LARGE', 'Aggregate expanded metadata exceeds its budget')
            parts = path_parts(item.name, root=True)
            if not parts:
                require(item.isdir(), 'INVALID_ARCHIVE_ROOT', 'Root archive member must be a directory')
                continue
            name = '/'.join(parts)
            require(name not in entries and len(entries) < MAX_ENTRIES, 'DUPLICATE_OR_LARGE_ARCHIVE',
                    'Duplicate archive path or too many files', path=name)
            require(parts[0] not in (MANAGER, 'lost+found') and not parts[0].startswith('.a22x-systems-'),
                    'RESERVED_ARCHIVE_PATH', 'Archive may not contain recovery management data', path=name)
            require(parts[0] not in RUNTIME_DIRS or len(parts) == 1 and item.isdir(),
                    'LIVE_RUNTIME_CONTENT', 'Rootfs /dev, /proc, /sys and /run must be empty directories', path=name)
            require(item.isdir() or item.isreg() or item.issym() or item.islnk(),
                    'ARCHIVE_SPECIAL_FILE', 'Devices, sockets and FIFOs are not imported', path=name)
            require(not item.sparse, 'UNSUPPORTED_SPARSE_ARCHIVE', 'Sparse archive members are not admitted')
            attrs = tar_attributes(item)
            kind = 'directory' if item.isdir() else 'file' if item.isreg() else 'symlink' if item.issym() else 'hardlink'
            total += item.size if item.isreg() else 0
            require(total <= MAX_UNPACKED, 'ROOTFS_TOO_LARGE', 'Rootfs expansion exceeds the supported limit')
            record = {'kind': kind, 'link': None, 'size': item.size, 'uid': item.uid, 'gid': item.gid, 'mode': item.mode}
            if item.issym():
                link_destination(parts[:-1], item.linkname)
                record['link'] = item.linkname
            elif item.islnk():
                record['link'] = '/'.join(path_parts(item.linkname))
            entries[name] = record
            if target:
                if len(parts) > 1:
                    target.mkdirs('/'.join(parts[:-1]))
                if item.isdir():
                    target.mkdirs(name)
                    dirs.append((name, item, attrs))
                elif item.issym() or item.islnk():
                    links.append((name, item, attrs))
                else:
                    with target.parent(name) as (parent, base):
                        output = os.open(base, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         0o600, dir_fd=parent)
                        try:
                            remaining = item.size
                            stream = archive.extractfile(item)
                            while remaining:
                                data = stream.read(min(CHUNK, remaining))
                                require(data, 'SHORT_ARCHIVE_FILE', 'Archive member ended early', path=name)
                                remaining -= len(data)
                                view = memoryview(data)
                                while view:
                                    written = os.write(output, view)
                                    require(written > 0, 'SHORT_WRITE', 'Rootfs write made no progress')
                                    view = view[written:]
                            require(not stream.read(1), 'OVERLONG_ARCHIVE_FILE', 'Archive member exceeds its declared size')
                            os.fsync(output)
                        finally:
                            os.close(output)
                        os.fsync(parent)
                    apply_metadata(target, name, item, attrs)
                if len(entries) % 128 == 0:
                    target.pin.recheck()
            archive.members.clear()
    while reader.read(CHUNK):
        pass
    require(reader.hash.hexdigest() == expected_sha and fs.stamp(os.fstat(fd)) == before,
            'ARCHIVE_HASH_MISMATCH', 'Rootfs archive checksum or identity changed')
    for name in entries:
        parts = path_parts(name)
        for index in range(1, len(parts)):
            parent = entries.get('/'.join(parts[:index]))
            require(parent is None or parent['kind'] == 'directory', 'ARCHIVE_LINK_PARENT',
                    'An archive path traverses a symlink or non-directory', path=name)
    def hard_target(name, seen=()):
        require(name not in seen and len(seen) < 40 and name in entries,
                'UNSAFE_HARDLINK', 'Hardlink target is missing or cyclic', path=name)
        item = entries[name]
        if item['kind'] == 'hardlink':
            return hard_target(item['link'], seen + (name,))
        require(item['kind'] == 'file', 'UNSAFE_HARDLINK', 'Hardlinks may refer only to regular files', path=name)
        return name
    for name, value in entries.items():
        if value['kind'] == 'hardlink':
            source = entries[hard_target(name)]
            require(all(source[key] == value[key] for key in ('uid', 'gid', 'mode')),
                    'HARDLINK_METADATA_MISMATCH', 'Hardlink metadata would alter another archive entry', path=name)
    if target:
        for name, item, attrs in links:
            with target.parent(name) as (parent, base):
                if item.issym():
                    os.symlink(item.linkname, base, dir_fd=parent)
                else:
                    source = hard_target(name)
                    with target.parent(source) as (src, old):
                        os.link(old, base, src_dir_fd=src, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            if item.issym():
                apply_metadata(target, name, item, attrs)
            else:
                require(not attrs, 'HARDLINK_XATTR_UNSUPPORTED', 'Put hardlink xattrs on the original archive member')
        for name, item, attrs in sorted(dirs, key=lambda x: x[0].count('/'), reverse=True):
            apply_metadata(target, name, item, attrs)
        os.fsync(target.fd)
        target.pin.recheck()
    require('sbin' in entries and 'home' in entries, 'INVALID_ROOTFS', 'The bundle needs /sbin and /home entries')
    return {'entries': len(entries), 'unpacked_bytes': total, 'archive_bytes': before[3],
            'archive_sha256': reader.hash.hexdigest(), 'paths_sha256': digest(entries)}


def read_bundle(source, manifest_path):
    manifest_path = logical_path(manifest_path)
    raw = source.read(manifest_path)
    bundle = json.loads(raw)
    require(bundle.get('schema') == 'a22x.distro.bundle.v1' and bundle.get('architecture') == 'aarch64',
            'INVALID_BUNDLE', 'Select an A22x ARM64 rootfs bundle manifest')
    identifier(bundle.get('id'))
    name_text(bundle.get('name'))
    require(bundle.get('boot_profile') == PROFILE_ID and isinstance(bundle.get('boot_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', bundle['boot_sha256']),
            'BUNDLE_BOOT_PROFILE_MISMATCH', 'This rootfs was not prepared for the retained A22x boot profile')
    fs.require_hash(bundle.get('platform_profile_sha256'), 'platform_profile_sha256')
    archive = bundle.get('rootfs', {})
    require(isinstance(archive, dict) and archive.get('compression') in ('gzip', 'none'),
            'UNSUPPORTED_ARCHIVE', 'Prepared uncompressed tar and tar.gz root filesystems are supported')
    fs.require_hash(archive.get('sha256'))
    name = '/'.join(path_parts(archive.get('file')))
    require('/' not in name, 'INVALID_BUNDLE', 'The archive must be beside its bundle manifest')
    archive_path = manifest_path.rsplit('/', 1)[0] + '/' + name if '/' in manifest_path else name
    return bundle, manifest_path, archive_path, hashlib.sha256(raw).hexdigest()


def state_volume(pin):
    v = pin.volume
    return {'filesystem_uuid': v.get('filesystem_uuid'),
            'device_identity': v.get('device_identity'),
            'fixture_root': str(pin.context.root) if pin.context.mode == 'fixture' else None}


def read_state(tree):
    value = tree.json(MANAGER + '/state.json', sealed=True)
    require(value.get('schema') == 'a22x.distro.state.v1' and value.get('volume') == state_volume(tree.pin)
            and isinstance(value.get('generation'), int) and value['generation'] >= 0,
            'STATE_IDENTITY_MISMATCH', 'Systems metadata belongs to another userdata filesystem')
    identifier(value.get('active'))
    return value


def initialize(tree, profile):
    if tree.exists(MANAGER):
        return read_state(tree)
    validate_root(tree, profile, tree.pin.volume.get('filesystem_uuid'), platform=False)
    prefix = '.a22x-systems-setup-' + os.urandom(8).hex()
    tree.mkdirs(prefix)
    # A slot's /home is only a template/placeholder; the live /home never moves.
    tree.mkdirs(prefix + '/slots/original/root/home', mode=0o755)
    tree.mkdirs(prefix + '/staging')
    tree.mkdirs(prefix + '/history')
    state = seal({'schema': 'a22x.distro.state.v1', 'active': 'original', 'generation': 0,
                  'volume': state_volume(tree.pin), 'platform_profile_sha256': digest(profile)})
    tree.write_json(prefix + '/slots/original/slot.json', seal({
        'schema': 'a22x.distro.slot.v1', 'id': 'original', 'name': 'Original Linux',
        'origin': 'adopted-existing-root', 'platform_profile_sha256': digest(profile),
        'created_utc': int(time.time()), 'hardware_boot_tested': False}), create=True)
    tree.write_json(prefix + '/state.json', state, create=True)
    tree.rename(prefix, MANAGER)
    return read_state(tree)


def sentinel_path(context):
    return context.root / '.distro-transaction.json' if context.mode == 'fixture' else SENTINEL_PATH


def set_sentinel(context, journal):
    value = {'schema': 'a22x.distro.transaction.v1', 'transaction_id': journal['id'],
             'direction': journal.get('direction', 'resume'), 'step': journal['next'],
             'total': len(journal['steps']), 'normal_boot_blocked': True}
    fs.atomic_state(sentinel_path(context), value)


def clear_sentinel(context, transaction_id=None):
    path = sentinel_path(context)
    try:
        current = fs.regular_json(path)
        require(current.get('schema') == 'a22x.distro.transaction.v1' and
                (transaction_id is None or current.get('transaction_id') == transaction_id),
                'SENTINEL_CHANGED', 'Boot guard belongs to another transaction')
        path.unlink()
    except FileNotFoundError:
        pass


def read_transaction(tree):
    path = MANAGER + '/transaction.json'
    if not tree.exists(path):
        return None
    value = tree.json(path, sealed=True)
    require(value.get('schema') == 'a22x.distro.transaction.v1' and value.get('volume') == state_volume(tree.pin),
            'TRANSACTION_IDENTITY_MISMATCH', 'Switch journal belongs to another filesystem')
    identifier(value.get('id'))
    require(isinstance(value.get('steps'), list) and 1 <= len(value['steps']) <= 256
            and isinstance(value.get('next'), int) and 0 <= value['next'] <= len(value['steps']),
            'INVALID_TRANSACTION', 'Invalid switch journal')
    validate_transaction(value)
    set_sentinel(tree.pin.context, value)
    return value


def validate_transaction(value):
    source, target = identifier(value['source']), identifier(value['target'])
    require(source != target and value.get('direction') in ('resume', 'rollback')
            and isinstance(value.get('boot_sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', value['boot_sha256']),
            'INVALID_TRANSACTION', 'Invalid switch identities or boot profile')
    before, after = check_seal(value['before_state']), check_seal(value['after_state'])
    require(before['active'] == source and after['active'] == target
            and after['generation'] == before['generation'] + 1 and before['volume'] == after['volume'] == value['volume'],
            'INVALID_TRANSACTION', 'Invalid active-state transition')
    old_base = MANAGER + '/slots/' + source + '/root/'
    new_base = MANAGER + '/slots/' + target + '/root/'
    old, new = [], []
    for item in value['steps']:
        a, b = item.get('from'), item.get('to')
        require(isinstance(a, str) and isinstance(b, str), 'INVALID_TRANSACTION', 'Invalid rename path')
        pa, pb = path_parts(a), path_parts(b)
        if len(pa) == 1:
            require(not new and pa[0] not in RESERVED and b == old_base + pa[0],
                    'INVALID_TRANSACTION', 'Old root move is not confined to its inactive slot')
            old.append(pa[0])
        else:
            require(len(pb) == 1 and pb[0] not in RESERVED and a == new_base + pb[0],
                    'INVALID_TRANSACTION', 'New root move is not confined to its active root')
            new.append(pb[0])
        stamp = item.get('identity', {})
        require(isinstance(stamp, dict) and all(isinstance(stamp.get(k), int) for k in ('dev', 'ino', 'mode', 'uid', 'gid'))
                and (stat.S_ISREG(stamp['mode']) or stat.S_ISDIR(stamp['mode']) or stat.S_ISLNK(stamp['mode'])),
                'INVALID_TRANSACTION', 'Invalid object identity')
    require(old and new and old == ['sbin'] + sorted(set(old) - {'sbin'})
            and new == sorted(set(new) - {'sbin'}) + ['sbin'],
            'INVALID_TRANSACTION', 'Init must be guarded first and committed last, without duplicate names')
    expected = {MANAGER, MANAGER + '/slots', old_base.rstrip('/'), new_base.rstrip('/')}
    require(isinstance(value.get('containers'), dict) and set(value['containers']) == expected,
            'INVALID_TRANSACTION', 'Switch container identities are missing')


def reconcile_completed_guard(tree, state):
    """Clear only the exact RAM guard of a durably completed archived transaction."""
    path = sentinel_path(tree.pin.context)
    if not path.exists():
        return
    guard = fs.regular_json(path)
    require(guard.get('schema') == 'a22x.distro.transaction.v1', 'SENTINEL_UNRESOLVED', 'Unknown normal-boot guard')
    ident = identifier(guard.get('transaction_id'))
    history = MANAGER + '/history/' + ident + '.json'
    require(tree.exists(history), 'SENTINEL_UNRESOLVED', 'The boot guard has no completed switch receipt')
    journal = tree.json(history, sealed=True)
    validate_transaction(journal)
    require(journal['id'] == ident and journal['volume'] == state_volume(tree.pin)
            and journal.get('status') in ('committed', 'rolled-back'),
            'SENTINEL_UNRESOLVED', 'The switch receipt is not complete')
    forward = journal['status'] == 'committed'
    expected_state = journal['after_state'] if forward else journal['before_state']
    require(state == expected_state and tree.record('home') == journal['home_identity'],
            'SENTINEL_UNRESOLVED', 'The completed receipt does not describe the current system')
    for step in journal['steps']:
        location = step['to'] if forward else step['from']
        require(tree.record(location) == step['identity'], 'SENTINEL_UNRESOLVED',
                'The completed root no longer matches the guarded switch', path=location)
    clear_sentinel(tree.pin.context, ident)


def has_started(tree, root):
    """Whether the system at ROOT has noted its start; one whose /var cannot be walked has not."""
    try:
        return tree.exists(root + STARTED_FILE)
    except (OSError, fs.FilesystemError):
        return False


def slots(tree, state):
    names = tree.list(MANAGER + '/slots')
    require(len(names) <= MAX_SLOTS, 'TOO_MANY_SLOTS', 'Too many saved systems')
    result = []
    for name in names:
        identifier(name)
        item = tree.json(MANAGER + '/slots/' + name + '/slot.json', sealed=True)
        require(item.get('schema') == 'a22x.distro.slot.v1' and item.get('id') == name,
                'INVALID_SLOT', 'Slot metadata does not match its directory')
        # A system that has started on the phone says so in its own /var (a22x_adapter.STARTED_FILE,
        # 2026-11-12); the active one lies at the top of userdata, a saved one in its slot.
        root = '' if name == state['active'] else MANAGER + '/slots/' + name + '/root/'
        started = bool(item.get('hardware_boot_tested')) or has_started(tree, root)
        result.append({'id': name, 'name': item['name'], 'active': name == state['active'],
                       'origin': item.get('origin'), 'hardware_boot_tested': started})
    require(sum(item['active'] for item in result) == 1, 'ACTIVE_SLOT_MISSING', 'The active slot is not recorded')
    return result


def status(context):
    volume = context.volume('userdata')
    base = {'schema': 'a22x.distro.status.v1', 'mounted': volume['mounted'],
            'read_only': volume['read_only'], 'volume_token': volume['volume_token'],
            'active': None, 'slots': [], 'transaction': None,
            'reboot_blocked': sentinel_path(context).exists(), 'empty_userdata': False,
            'shared_paths': ['/home'], 'retained_boot': True, 'whole_switch_atomic': False,
            'platform_profile_available': profile_path(context).exists(), 'foreign': foreign_userdata(context),
            'boot_linux': boot_is_linux(context)}
    # Where a new system would take its Linux BOOT from, when BOOT holds another kernel: cache, download, or nowhere.
    # cache or download; boot_outdated: BOOT is Linux, but not the release this recovery was made for.
    base['boot_outdated'] = base['boot_linux'] and boot_outdated(context)
    base['boot_source'] = (linux_boot_choice(context)[0] or {}).get('from', '') if not base['boot_linux'] or base['boot_outdated'] else ''
    if not volume['mounted']:
        return {**base, 'initialized': None, 'transaction_state': 'unknown-unmounted',
                'normal_boot_safety_known': False,
                'next_action': 'Install a new system: userdata is formatted for Linux first' if base['foreign'] else
                'Mount userdata to inspect installed systems'}
    with fs.PinnedVolume(context, 'userdata') as pin, Tree(pin) as tree:
        require_userdata(pin)
        base['incomplete_setups'] = [name for name in tree.list()
                                     if re.fullmatch(r'\.a22x-systems-setup-[0-9a-f]{16}', name)][:32]
        if not tree.exists(MANAGER):
            empty = userdata_empty(tree)
            return {**base, 'initialized': False, 'transaction_state': 'none', 'empty_userdata': empty,
                    'normal_boot_safety_known': False,
                    'next_action': 'Install a system: userdata is empty' if empty else 'Import a prepared ARM64 bundle from SD'}
        state = read_state(tree)
        tx = read_transaction(tree)
        if not tx:
            reconcile_completed_guard(tree, state)
        listed = slots(tree, state)
        return {**base, 'initialized': True, 'active': state['active'], 'generation': state['generation'],
                'slots': listed, 'transaction': ({'id': tx['id'], 'source': tx['source'], 'target': tx['target'],
                    'step': tx['next'], 'total': len(tx['steps']), 'direction': tx.get('direction', 'resume'),
                    'actions': ['resume', 'rollback']} if tx else None),
                'reboot_blocked': bool(tx), 'transaction_state': 'pending' if tx else 'none',
                'normal_boot_safety_known': not bool(tx),
                'incomplete_imports': tree.list(MANAGER + '/staging')[:32]}


def plan_envelope(context, operation, binding, **fields):
    value = {'schema': 'a22x.distro.plan.v1', 'operation': operation, 'mode': context.mode,
             'boot_id': getattr(context, 'boot_id', 'fixture'), 'created_utc': int(time.time()),
             'expires_utc': int(time.time()) + PLAN_SECONDS, 'binding': binding, **fields}
    return seal(value)


def verify_plan(context, plan, operation):
    check_seal(plan)
    require(plan.get('schema') == 'a22x.distro.plan.v1' and plan.get('operation') == operation
            and plan.get('mode') == context.mode and plan.get('boot_id') == getattr(context, 'boot_id', 'fixture'),
            'PLAN_CONTEXT_MISMATCH', 'Plan does not describe this operation and recovery boot')
    now = int(time.time())
    require(isinstance(plan.get('expires_utc'), int) and isinstance(plan.get('created_utc'), int)
            and plan['created_utc'] <= now + 5 and plan['created_utc'] <= plan['expires_utc']
            and plan['expires_utc'] - plan['created_utc'] <= PLAN_SECONDS and now <= plan['expires_utc'],
            'PLAN_EXPIRED', 'Create and review a fresh operation plan')


def save_plan_result(context, result, output):
    """Write the full plan to one new private RAM file; return a bounded UI summary."""
    path = fs.absolute_no_symlinks(output, missing=True)
    require(path.is_absolute() and path.parent.is_dir(), 'INVALID_PLAN_OUTPUT', 'Use a new file in an existing private directory')
    if context.mode == 'device':
        st = path.parent.stat()
        require(st.st_uid == 0 and not st.st_mode & 0o022, 'INVALID_PLAN_OUTPUT', 'The plan directory must be root-owned and not writable by others')
        mounts = [m for m in fs.read_mounts() if path.parent == Path(m['mountpoint']) or Path(m['mountpoint']) in path.parent.parents]
        require(mounts and max(mounts, key=lambda m: len(m['mountpoint']))['fs_type'] in ('rootfs', 'ramfs', 'tmpfs'),
                'PLAN_OUTPUT_NOT_RAM', 'UI plans must be stored in private RAM, outside installed systems and SD')
    else:
        require(context.root in path.parents and all(Path(v['path']) not in path.parents and Path(v['path']) != path
                for v in context.volumes()), 'INVALID_PLAN_OUTPUT', 'Fixture plans belong outside its volume directories')
    data = canonical(result) + b'\n'
    require(len(data) <= MAX_JSON, 'PLAN_TOO_LARGE', 'The full operation plan exceeds its bound')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            require(count > 0, 'SHORT_WRITE', 'Plan output write made no progress')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    keys = ('schema', 'operation', 'mode', 'seal', 'slot', 'name', 'confirmation', 'created_utc', 'expires_utc',
            'required_bytes', 'available_bytes', 'source_volume', 'manifest_path', 'preserved_paths',
            'whole_switch_atomic', 'interruption_action', 'clean_install', 'erase_userdata', 'format_userdata', 'download_bytes',
            'write_boot')
    return {**{k: v for k, v in result.items() if k != 'plan'},
            'plan': {k: result['plan'][k] for k in keys if k in result['plan']},
            'plan_summary': True, 'plan_path': str(path), 'plan_file_sha256': hashlib.sha256(data).hexdigest()}


def load_plan_file(path, expected_seal=None, expected_file_sha=None):
    require(bool(expected_seal) == bool(expected_file_sha), 'INCOMPLETE_PLAN_BINDING',
            'Provide both expected plan seal and expected plan file SHA256')
    if expected_seal:
        fs.require_hash(expected_seal, 'expected-plan-seal')
        fs.require_hash(expected_file_sha, 'expected-plan-file-sha256')
    path = fs.absolute_no_symlinks(path)
    fd = fs.open_regular(path)
    try:
        before = os.fstat(fd)
        require(before.st_nlink == 1 and before.st_size <= MAX_JSON,
                'INVALID_PLAN_FILE', 'Plan must be one bounded regular file')
        raw = os.pread(fd, MAX_JSON + 1, 0)
        named = path.stat(follow_symlinks=False)
        require(len(raw) == before.st_size and fs.stamp(os.fstat(fd)) == fs.stamp(before)
                and (named.st_dev, named.st_ino) == (before.st_dev, before.st_ino),
                'PLAN_FILE_CHANGED', 'Plan file changed while opening it')
    finally:
        os.close(fd)
    if expected_file_sha:
        require(hashlib.sha256(raw).hexdigest() == expected_file_sha,
                'PLAN_FILE_CHANGED', 'The full plan file differs from the plan shown in the UI')
    value = json.loads(raw)
    require(isinstance(value, dict), 'INVALID_PLAN_FILE', 'Expected a plan JSON object')
    plan = value.get('plan', value)
    check_seal(plan)
    if expected_seal:
        require(plan['seal'] == expected_seal, 'PLAN_FILE_CHANGED', 'The reviewed plan seal changed')
    return plan


def import_plan(context, source_volume, manifest_path, expected_source=None):
    require(source_volume != 'userdata', 'BUNDLE_ON_USERDATA', 'Import bundles from external SD/USB storage')
    profile, boot = load_profile(context), boot_identity(context)
    with fs.PinnedVolume(context, 'userdata', writing=True) as target_pin, Tree(target_pin) as live, \
            fs.PinnedVolume(context, source_volume, expected_source) as source_pin, Tree(source_pin) as source:
        require_userdata(target_pin)
        require(context.mode == 'fixture' or source_pin.volume.get('external') is True,
                'EXTERNAL_BUNDLE_REQUIRED', 'Rootfs imports require external SD/USB storage')
        require(target_pin.stat.st_dev != source_pin.stat.st_dev or context.mode == 'fixture',
                'BUNDLE_ON_USERDATA', 'The source must be a different physical filesystem')
        if live.exists(MANAGER):
            state = read_state(live)
            require(not read_transaction(live), 'SWITCH_PENDING', 'Repair the pending switch before importing')
            require(len(slots(live, state)) < MAX_SLOTS, 'TOO_MANY_SLOTS', 'The slot limit has been reached')
        else:
            state = None
        bundle, manifest_path, archive_path, manifest_sha = read_bundle(source, manifest_path)
        require(bundle['id'] != 'original', 'RESERVED_SLOT', 'The original slot name is reserved')
        require(bundle['platform_profile_sha256'] == digest(profile), 'PLATFORM_PROFILE_MISMATCH',
                'The bundle does not carry this phone’s audited platform adapter profile')
        require(not live.exists(MANAGER + '/slots/' + bundle['id']) if state else True,
                'SLOT_EXISTS', 'Choose a new slot ID; an existing system is never overwritten')
        fd = source.regular(archive_path)
        try:
            archive_stamp = fs.stamp(os.fstat(fd))
            scan = scan_archive(fd, bundle['rootfs']['compression'], bundle['rootfs']['sha256'])
        finally:
            os.close(fd)
        free = os.fstatvfs(target_pin.fd).f_bavail * os.fstatvfs(target_pin.fd).f_frsize
        required = scan['unpacked_bytes'] + scan['entries'] * 8192 + MIN_FREE
        require(free >= required, 'INSUFFICIENT_SPACE', 'There is not enough space beside the retained system',
                available_bytes=free, required_bytes=required)
        binding = {'volume_token': target_pin.volume['volume_token'], 'state_seal': state['seal'] if state else None,
                   'source_volume_token': source_pin.volume['volume_token'], 'manifest_sha256': manifest_sha,
                   'archive_identity': list(archive_stamp), 'scan': scan, 'boot': boot,
                   'platform_profile_sha256': digest(profile)}
        return {'plan': plan_envelope(context, 'import', binding, source_volume=source_volume,
                    manifest_path='/' + manifest_path, slot=bundle['id'], name=bundle['name'],
                    required_bytes=required, available_bytes=free,
                    confirmation='Install alongside the current Linux; do not switch or erase /home'),
                'switches_current_system': False, 'backup_recommended': True}


def install(context, plan, confirmed=False):
    if plan.get('image') is True:
        return install_image(context, plan, confirmed)
    context.confirm(confirmed)
    verify_plan(context, plan, 'import')
    fresh = import_plan(context, plan['source_volume'], plan['manifest_path'], plan['binding']['source_volume_token'])['plan']
    require(fresh['binding'] == plan['binding'] and fresh['slot'] == plan['slot'],
            'PLAN_STALE', 'Archive, filesystem or installation state changed since planning')
    profile = load_profile(context)
    with fs.PinnedVolume(context, 'userdata', plan['binding']['volume_token'], writing=True) as pin, Tree(pin) as live, \
            fs.PinnedVolume(context, plan['source_volume'], plan['binding']['source_volume_token']) as source_pin, Tree(source_pin) as source:
        require_userdata(pin)
        state = initialize(live, profile)
        require(not read_transaction(live), 'SWITCH_PENDING', 'Repair the pending switch before importing')
        bundle, unused, archive_path, unused = read_bundle(source, plan['manifest_path'])
        stage_id = bundle['id'] + '-' + os.urandom(5).hex()
        stage = MANAGER + '/staging/' + stage_id
        live.mkdirs(stage + '/root')
        live.write_json(stage + '/import.json', seal({'schema': 'a22x.distro.import.v1',
                        'id': stage_id, 'slot': bundle['id'], 'plan_seal': plan['seal'], 'status': 'extracting'}), create=True)
        fd = source.regular(archive_path)
        try:
            with Tree(pin, stage + '/root') as target:
                scan = scan_archive(fd, bundle['rootfs']['compression'], bundle['rootfs']['sha256'], target)
                require(scan == plan['binding']['scan'], 'ARCHIVE_CHANGED', 'Archive expansion differs from its plan')
                readiness = validate_root(target, profile, pin.volume.get('filesystem_uuid'))
                home = home_binding(live, target)
        finally:
            os.close(fd)
        live.write_json(stage + '/slot.json', seal({'schema': 'a22x.distro.slot.v1', 'id': bundle['id'],
                        'name': bundle['name'], 'origin': 'prepared-arm64-rootfs',
                        'created_utc': int(time.time()), 'bundle_manifest_sha256': plan['binding']['manifest_sha256'],
                        'rootfs_archive_sha256': bundle['rootfs']['sha256'], 'platform_profile_sha256': digest(profile),
                        'validation': readiness, 'shared_home_accounts': home['accounts'],
                        'hardware_boot_tested': False}), create=True)
        live.write_json(stage + '/import.json', seal({'schema': 'a22x.distro.import.v1',
                        'id': stage_id, 'slot': bundle['id'], 'plan_seal': plan['seal'], 'status': 'complete'}))
        live.rename(stage, MANAGER + '/slots/' + bundle['id'])
        state.pop('seal')
        state['generation'] += 1
        live.write_json(MANAGER + '/state.json', seal(state))
        return {'installed_slot': bundle['id'], 'active': state['active'], 'switched': False,
                'home_preserved': True, 'validation': readiness, 'rootfs': scan}


DESKTOPS = ('image', 'console', 'phosh', 'gnome', 'plasma', 'xfce', 'sxmo', 'lomiri')  # Sxmo, Lomiri: Debian and Ubuntu
# More shells beside the main one, and the sign-in screen that switches between them (2026-11-09):
# automatic sign-in, Phrog (touch login made of Phosh), a text login, GDM, SDDM or LightDM.
SHELLS = DESKTOPS[2:]
GREETERS = ('auto', 'phrog', 'text', 'gdm', 'sddm', 'lightdm')
# The new system's language and time zone (2026-11-09); without a choice the recovery's own.
LOCALES = ('en_US', 'uk_UA', 'de_DE', 'fr_FR', 'pl_PL', 'es_ES', 'it_IT', 'pt_BR', 'nl_NL', 'cs_CZ', 'ro_RO', 'tr_TR', 'ru_RU')
TIMEZONE = re.compile(r'[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2}')
# Optional programs for the first start; a22x-firstboot.sh maps each to the new system's package.
APPS = ('browser', 'settings', 'files', 'terminal', 'editor', 'store', 'keyboard', 'telegram', 'ssh',
        # 2026-11-08: more of what a phone needs; a22x-firstboot.sh names the package of each.
        'email', 'music', 'video', 'photos', 'documents', 'office', 'calculator', 'clock', 'weather', 'maps', 'camera',
        'contacts', 'calendar', 'passwords', 'flatpak', 'chromium', 'podcasts', 'messages', 'git', 'monitor')
PACKAGES_MAX = 40
SCRIPT_LIMIT = 1 << 20
WIFI_TOOLS = ('usr/bin/wpa_supplicant', 'usr/sbin/wpa_supplicant')
IMAGE_FACTOR_FLOOR = 2 * 1024**3
IMAGE_MARGIN = 1024**3
# What the packages of each shell and app take once installed, with their downloads, roughly
# (2026-11-10: Debian 13 with Phosh and GNOME ran out of space while apt fetched them, after the
# plan had said 2.6 GiB were enough).
SHELL_BYTES = {'phosh': 1536 * 1024**2, 'gnome': 2048 * 1024**2, 'plasma': 2560 * 1024**2, 'xfce': 1024 * 1024**2,
               'sxmo': 512 * 1024**2, 'lomiri': 2048 * 1024**2}
APP_BYTES = 192 * 1024**2
COUNT_PACKAGES = None  # None: on the phone only (fixtures live on a small tmpfs); tests set True


def packages_bytes(options):
    """The room the shells and apps of OPTIONS need in the new system."""
    shells = [options['desktop']] + list(options.get('shells') or ())
    return sum(SHELL_BYTES.get(s, 0) for s in shells) + APP_BYTES * (len(options.get('apps') or ()) + len(options.get('packages') or ()))
PLATFORM_EXTRAS = ('opt/a22x-conn/bt_addr', 'opt/a22x-conn/kernel-release',
                   'etc/systemd/system/a22x-conn.service',
                   'etc/systemd/system/multi-user.target.wants/a22x-conn.service',
                   'etc/modules-load.d', 'etc/NetworkManager/conf.d/90-a22x-wifi.conf',
                   'usr/lib/modules/' + KERNEL_RELEASE)


# Sound: the UCM profile for this codec, mixer state and control map. Absent on a bare system.
PLATFORM_OPTIONAL = ('usr/share/alsa/ucm2/conf.d/mt6833_mt6359', 'usr/share/alsa/ucm2/MediaTek/mt6833',
                     'var/lib/alsa/asound.state', 'var/lib/alsa/card1.conf.d',
                     # Vibration patterns, the wake fix for suspend, Power key handling, memory, Wi-Fi rules.
                     'usr/share/feedbackd/themes/samsung,a22x.json', 'usr/lib/systemd/system-sleep/a22x',
                     'etc/systemd/system/systemd-suspend.service.d/50-a22x-wake-cpu.conf',
                     'etc/systemd/logind.conf.d/50-a22x-powerkey.conf', 'etc/sysctl.d/99-zram.conf',
                     'usr/lib/firmware/regulatory.db', 'usr/lib/firmware/regulatory.db.p7s')


def real_path(tree):
    return Path(os.readlink('/proc/self/fd/%d' % tree.fd))


def image_slot(name, live):
    stem = re.sub(r'\.(iso|raw|img|tar|tgz|qcow2|box|vhd|vhdfixed|xz|zst|gz|bz2)$', '', name.lower())
    while stem != (stem := re.sub(r'\.(iso|raw|img|tar|qcow2|vhd|vhdfixed|vagrant\.libvirt|xz|zst|gz|bz2)$', '', stem)):
        pass
    slot = re.sub(r'[^a-z0-9]+', '-', stem).strip('-')[:40] or 'linux'
    candidate, number = slot, 1
    while live.exists(MANAGER + '/slots/' + candidate):
        number += 1
        candidate = '%s-%d' % (slot, number)
    return candidate


SAVED_WIFI_RAM = Path('/run/a22x-recovery/wifi-saved.json')
SAVED_WIFI = Path('a22x-recovery/wifi.json')


def saved_key(context, ssid):
    """The key the recovery saved when it joined SSID: a WPA key in hex, or '' for an open network.
    The interface marks a network joined from the saved list, whose password it never had."""
    def listed(text):
        return [n for n in json.loads(text).get('networks', []) if isinstance(n, dict)]
    ram = context.root / 'wifi-saved.json' if context.mode == 'fixture' else SAVED_WIFI_RAM
    networks = None
    if ram.is_file():
        with contextlib.suppress(OSError, ValueError):
            networks = listed(ram.read_text())
    if networks is None:
        networks = catalog_volumes(context, 'ro', lambda root: listed((root / SAVED_WIFI).read_text())
                                   if (root / SAVED_WIFI).is_file() else []) or []
    for network in networks:
        if network.get('ssid_hex') == ssid.encode().hex():
            key = network.get('psk', '')
            require(key == '' or re.fullmatch(r'[0-9a-f]{64}', key) is not None, 'WIFI_SAVED_INVALID',
                    'The saved key of this network is damaged. Enter its password.')
            return key
    fail('WIFI_NOT_SAVED', 'Recovery has no saved key for this network. Enter its password.')


def wifi_choice(value):
    """The network typed in recovery: a name of 1 to 32 bytes, and no password (open) or a WPA one."""
    if value is None:
        return None
    require(isinstance(value, dict) and set(value) == {'ssid', 'password'}, 'INVALID_WIFI', 'Enter the Wi-Fi network again')
    ssid, password = value['ssid'], value['password']
    printable = lambda text: all(ord(c) >= 32 and ord(c) != 127 for c in text)
    require(isinstance(ssid, str) and 1 <= len(ssid.encode()) <= 32 and printable(ssid),
            'INVALID_WIFI_NAME', 'A network name has 1 to 32 bytes')
    require(isinstance(password, str) and (password == '' or (8 <= len(password.encode()) <= 63 and printable(password))
                                          or re.fullmatch(r'[0-9A-Fa-f]{64}', password) is not None),
            'INVALID_WIFI_PASSWORD', 'A Wi-Fi password has 8 to 63 characters')
    return {'ssid': ssid, 'password': password}


# A password prepared in the Android app arrives only as a SHA-512 crypt hash (2026-11-10).
SHA512_CRYPT = re.compile(r'\$6\$(?:rounds=[1-9][0-9]{3,8}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}')


def image_options(desktop, account, username, password, trial_seconds, authorized_key, wifi=None, apps=(), script=None,
                  packages=(), shells=(), greeter='auto', system_locale=None, timezone=None, password_hash=None):
    require(desktop in DESKTOPS, 'INVALID_DESKTOP', 'Unknown desktop choice')
    chosen = list(shells or ())
    require(all(isinstance(s, str) and s in SHELLS for s in chosen), 'INVALID_SHELL', 'Unknown shell choice')
    shells = [s for s in SHELLS if s in chosen and s != desktop]
    require(not shells or desktop in SHELLS, 'SHELLS_NEED_DESKTOP', 'More shells need a desktop as the main shell')
    greeter = greeter or 'auto'
    require(greeter in GREETERS, 'INVALID_GREETER', 'Unknown sign-in screen')
    require(greeter == 'auto' or desktop in SHELLS, 'GREETER_NEEDS_DESKTOP', 'A sign-in screen needs a desktop')
    require(system_locale is None or system_locale in LOCALES, 'INVALID_LOCALE', 'Unknown system language')
    require(timezone is None or (isinstance(timezone, str) and len(timezone) <= 64 and TIMEZONE.fullmatch(timezone) is not None),
            'INVALID_TIMEZONE', 'Unknown time zone')
    require(account in ('keep', 'new'), 'INVALID_ACCOUNT_CHOICE', 'Choose to keep the accounts or add a new one')
    if account == 'new':
        require(isinstance(username, str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', username),
                'INVALID_USERNAME', 'Use lowercase letters, digits, hyphens or underscores')
        if password_hash is not None:
            require(password is None and isinstance(password_hash, str) and SHA512_CRYPT.fullmatch(password_hash),
                    'INVALID_PASSWORD', 'The prepared password is not a SHA-512 hash')
        else:
            require(isinstance(password, str) and 1 <= len(password) <= 128 and ':' not in password
                    and all(ord(c) >= 32 for c in password), 'INVALID_PASSWORD', 'Enter a password without a colon')
    apps = list(apps or ())
    require(all(item in APPS for item in apps), 'INVALID_APP', 'Unknown app choice')
    # Packages found by the search, named as the distribution names them.
    packages = sorted(set(packages or ()))
    require(len(packages) <= PACKAGES_MAX and all(isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9+._-]{0,99}', name)
                                                  for name in packages), 'INVALID_PACKAGE', 'A package name is not valid')
    if script is not None:
        require(isinstance(script, dict) and set(script) == {'volume', 'token', 'path'}
                and isinstance(script['volume'], str) and script['volume'] and isinstance(script['path'], str)
                and (script['token'] is None or isinstance(script['token'], str)), 'INVALID_SCRIPT', 'Choose the setup script again')
        logical_path(script['path'])
    return {'desktop': desktop, 'account': account, 'username': username if account == 'new' else None,
            'password': password if account == 'new' else None, 'trial_seconds': int(trial_seconds or 0),
            **({'password_hash': password_hash} if account == 'new' and password_hash is not None else {}),
            'authorized_key': authorized_key, 'wifi': wifi_choice(wifi), 'apps': [item for item in APPS if item in apps],
            'script': script, **({'packages': packages} if packages else {}), **({'shells': shells} if shells else {}),
            **({'greeter': greeter} if greeter != 'auto' else {}), **({'locale': system_locale} if system_locale else {}),
            **({'timezone': timezone} if timezone else {})}


def recovery_locale(context):
    """The recovery's language, clock offset in minutes and clock zone (Europe/Berlin, or ''), which
    the new system takes over (2026-11-09). Unreadable settings leave the system's own (English, UTC)."""
    record = {}
    if context.mode == 'fixture':
        path = context.root / 'settings.json'
        if path.is_file():
            with contextlib.suppress(OSError, ValueError):
                record = json.loads(path.read_text())
    else:
        with contextlib.suppress(Exception):
            import settings
            record = settings.load()
    language = record.get('language')
    offset = record.get('utc_offset')
    zone = record.get('timezone')
    return (language if language in ('uk', 'de', 'fr', 'pl', 'es', 'en') else '',
            offset if isinstance(offset, int) and -720 <= offset <= 840 else 0,
            zone if isinstance(zone, str) and TIMEZONE.fullmatch(zone) else '')


def userdata_empty(tree):
    """A freshly formatted userdata: nothing but lost+found, so a clean install replaces nothing."""
    names = [name for name in tree.list() if name not in (DOWNLOAD_DIR, STORE_DIR)]
    return set(names) <= {'lost+found'} and all(stat.S_ISDIR(tree.lstat(name).st_mode) for name in names)


def store_name(image_path):
    """The file name when IMAGE_PATH is an image of the phone's own store, else None."""
    if not isinstance(image_path, str):
        return None
    parts = image_path.lstrip('/').split('/')
    if len(parts) == 2 and parts[0] == STORE_DIR and STORE_FILE.fullmatch(parts[1]) and not parts[1].endswith(STORE_META):
        return parts[1]
    return None


def store_folder(live):
    """The store as a real folder, never a link a system could have placed there."""
    if live.exists(STORE_DIR):
        require(stat.S_ISDIR(live.lstat(STORE_DIR).st_mode), 'STORE_INVALID', 'The image store on userdata is not a folder')
    else:
        live.mkdirs(STORE_DIR)
    return real_path(live) / STORE_DIR


def store_images(live):
    """The saved images, with what the catalog said about them when they were downloaded."""
    if not live.exists(STORE_DIR) or not stat.S_ISDIR(live.lstat(STORE_DIR).st_mode):
        return []
    found = []
    for name in sorted(live.list(STORE_DIR)):
        if not STORE_FILE.fullmatch(name) or name.endswith(STORE_META):
            continue
        st = live.lstat(STORE_DIR + '/' + name)
        if not stat.S_ISREG(st.st_mode):
            continue
        meta = {}
        if live.exists(STORE_DIR + '/' + name + STORE_META):
            with contextlib.suppress(Exception):
                meta = json.loads(live.read(STORE_DIR + '/' + name + STORE_META, 65536).decode('utf-8'))
        meta = meta if isinstance(meta, dict) else {}
        text = lambda key, limit: str(meta.get(key) or '')[:limit]
        found.append({'file': name, 'path': '/' + STORE_DIR + '/' + name, 'size': st.st_size,
                      'catalog_id': text('id', 64), 'name': text('name', 64), 'version': text('version', 32),
                      'family': text('family', 16), 'checked': meta.get('checked') is True and meta.get('size') == st.st_size})
    return found


def store_bytes(live):
    return sum(item['size'] for item in store_images(live))


# The store on a card (2026-11-11). Images are kept beside the Linux systems on userdata; a phone
# whose userdata holds Android has no place of its own for a download, so there the store is the
# same folder on an SD card or a USB drive. Only Android images go to a card: a Linux installation
# keeps its images on the userdata it is installed to.
STORE_CARD_FS = ('ext4', 'exfat', 'vfat', 'f2fs')
FAT_FILE_LIMIT = (4 << 30) - 1
NO_CARD = 'Insert an SD card or attach a USB drive: while Android uses userdata, downloaded images are kept there'


def linux_userdata(context):
    """Whether userdata can hold the store: plain ext4, which is what a Linux system leaves there."""
    try:
        volume = context.volume('userdata')
    except fs.FilesystemError:
        return False
    return volume.get('fs_type') == 'ext4' and not volume.get('fscrypt_feature')


def boot_is_linux(context):
    """Whether BOOT holds the Linux kernel the platform modules were built for: a system installed
    now could start. A fixture says so itself (android_boot)."""
    if context.mode == 'fixture':
        return not context.config().get('android_boot', False)
    try:
        boot_identity(context)
        return True
    except fs.FilesystemError:
        return False


def foreign_userdata(context):
    """Userdata that holds no Linux of this recovery (2026-11-11): Android's data, or nothing that
    can be read. Nothing there can be mounted or kept; a clean installation formats it when the
    confirmed installation starts."""
    return not linux_userdata(context)


# What ext4 leaves of a partition for files: its tables, its journal and the blocks it reserves.
FORMATTED_SHARE = 0.90


def foreign_room(volume):
    """What a userdata that is formatted first can hold."""
    if volume.get('size_bytes'):
        return int(volume['size_bytes'] * FORMATTED_SHARE)
    room = os.statvfs(volume['mountpoint'])  # a fixture's folder
    return room.f_blocks * room.f_frsize


def is_card(context, volume):
    if context.mode == 'device':
        return volume.get('external') is True
    return volume['id'] in context.config().get('external_volumes', ['sdcard-p1'])


def store_place(pin):
    """What a result says of the volume its store is on."""
    volume = pin.volume
    return {'store': '/' + STORE_DIR, 'store_volume': volume['id'], 'store_token': volume['volume_token'],
            'store_label': volume.get('label') or volume['id']}


@contextlib.contextmanager
def card_store(context, writing=False, need=0, holding=None):
    """The store on a card: yields (pin, live), or (None, None) when no card can take it.

    The card that already has the store comes first (with HOLDING, the one that has that file),
    then, for a download, the first with NEED bytes free. A card is mounted when it is not,
    read-only unless a file is written, and the one that holds images stays mounted read-only
    afterwards, so that an image on it can be installed; any other is left as it was found."""
    cards = sorted((v for v in context.volumes() if is_card(context, v) and v.get('mountable') and v.get('fs_type') in STORE_CARD_FS),
                   key=lambda v: (not v['id'].startswith('sdcard'), v['id']))
    opened, seen = [], []
    try:
        for volume in cards:
            if not volume['mounted']:
                context.mount(volume['id'], 'ro', True, volume['volume_token'])
                opened.append(volume['id'])
            with fs.PinnedVolume(context, volume['id']) as pin, Tree(pin) as live:
                has = live.exists(STORE_DIR) and stat.S_ISDIR(live.lstat(STORE_DIR).st_mode)
                wanted = has and (holding is None or live.exists(STORE_DIR + '/' + holding))
                vfs = os.fstatvfs(pin.fd)
                seen.append((volume['id'], bool(wanted), vfs.f_bavail * vfs.f_frsize, bool(has and store_images(live))))
        fits = [row for row in seen if not writing or holding is not None or row[2] >= need]
        chosen = next((row for row in fits if row[1]), None) or (None if holding is not None else next(iter(fits), None))
        for ident in [i for i in opened if chosen is None or i != chosen[0]]:
            context.unmount(ident)
            opened.remove(ident)
        if chosen is None:
            require(not writing or holding is not None or not seen, 'INSUFFICIENT_SPACE', 'There is not enough space on the card for this image',
                    available_bytes=max((row[2] for row in seen), default=0), required_bytes=need)
            yield None, None
            return
        ident = chosen[0]
        before = context.volume(ident)
        reopened = False
        if writing and before['read_only']:
            require(before.get('mount_owner') == 'filesystem', 'STORE_READ_ONLY',
                    'The card is mounted read-only as backup storage: disconnect it in Storage first')
            context.mount(ident, 'rw', True, before['volume_token'])
            reopened = True
        try:
            with fs.PinnedVolume(context, ident, writing=writing) as pin, Tree(pin) as live:
                yield pin, live
                keep = bool(store_images(live))
                os.sync()
        finally:
            if reopened:
                with contextlib.suppress(OSError, fs.FilesystemError):  # Still writable then; Files shows it as it is.
                    context.mount(ident, 'ro', True, context.volume(ident)['volume_token'])
        if ident in opened and not keep:
            context.unmount(ident)
        opened.clear()
    except BaseException:
        for ident in opened:
            with contextlib.suppress(OSError, fs.FilesystemError):
                context.unmount(ident)
        raise


def images(context):
    """The images saved on the phone, or on a card when Android holds userdata, and the free space for more."""
    if linux_userdata(context):
        with fs.PinnedVolume(context, 'userdata') as pin, Tree(pin) as live:
            require_userdata(pin)
            vfs = os.fstatvfs(pin.fd)
            return {'images': store_images(live), 'free_bytes': vfs.f_bavail * vfs.f_frsize, **store_place(pin)}
    with card_store(context) as (pin, live):
        if pin is None:
            return {'images': [], 'free_bytes': 0, 'store': '/' + STORE_DIR, 'store_volume': '', 'store_token': '',
                    'store_label': '', 'store_reason': NO_CARD}
        vfs = os.fstatvfs(pin.fd)
        return {'images': store_images(live), 'free_bytes': vfs.f_bavail * vfs.f_frsize, **store_place(pin)}


def download_progress(name):
    last = [0.0]

    def report(phase, done=0, total=0):
        now = time.monotonic()
        if not PROGRESS_EVENTS or (now - last[0] < 0.5 and done < total):
            return
        last[0] = now
        print(json.dumps({'event': 'progress', 'operation': 'images-download', 'phase': phase, 'partition': name,
                          'bytes_done': int(done), 'bytes_total': int(total)}, separators=(',', ':')), file=sys.stderr, flush=True)
    return report


def store_download(context, live, entry, target, progress):
    """Download a catalog image to TARGET in the store: size and checksum or signature must match."""
    try:
        ensure_clock(context)
        signed = {'url': entry['signature_url'], 'sha256': entry['signature_sha256'], 'key': entry['key']} \
            if 'signature_url' in entry else None
        fetch(context, entry['url'], target, entry['size'], entry.get('sha256') or entry.get('sha512') or '',
              progress, signature=signed)
    except BaseException as error:
        with contextlib.suppress(OSError):
            target.unlink()
        downloaded_differently(context, entry, error)
        raise


def store_save(context, pin, live, entry, where):
    """Download ENTRY into the store on PIN and record what the catalog said of it."""
    vfs = os.fstatvfs(pin.fd)
    free = vfs.f_bavail * vfs.f_frsize
    require(free >= entry['size'] + MIN_FREE, 'INSUFFICIENT_SPACE', 'There is not enough space on %s for this image' % where,
            available_bytes=free, required_bytes=entry['size'] + MIN_FREE)
    require(pin.volume.get('fs_type') != 'vfat' or entry['size'] <= FAT_FILE_LIMIT, 'FILE_TOO_LARGE',
            'A FAT32 card cannot hold a file of 4 GiB or more')
    store = store_folder(live)
    for name in os.listdir(store):  # What a download that was cut off left behind.
        if name.startswith('.part-'):
            (store / name).unlink()
    part = store / ('.part-' + entry['file'])
    store_download(context, live, entry, part, download_progress(entry['file']))
    os.replace(part, store / entry['file'])
    meta = {'schema': 'a22x.image-store.v1', 'id': entry['id'], 'name': entry.get('name', ''),
            'version': entry.get('version', ''), 'family': entry.get('family', ''), 'category': entry.get('category', ''),
            'variant': entry.get('variant', ''), 'size': entry['size'],
            'sha256': entry.get('sha256', ''), 'sha512': entry.get('sha512', ''), 'url': entry['url'], 'checked': True}
    (store / (entry['file'] + STORE_META)).write_text(json.dumps(meta, indent=1) + '\n')
    os.sync()
    vfs = os.fstatvfs(pin.fd)
    return {'saved': entry['file'], 'images': store_images(live), 'free_bytes': vfs.f_bavail * vfs.f_frsize, **store_place(pin)}


def images_download(context, image_id, confirmed=False):
    """Save a catalog image on the phone, for installing later without a network or an SD card.
    On a phone whose userdata holds Android, an Android image is saved on a card instead."""
    context.confirm(confirmed)
    entry = plan_entry(context, image_id)
    if linux_userdata(context) or entry.get('family') != 'android':
        with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
            require_userdata(pin)
            return store_save(context, pin, live, entry, 'userdata')
    with card_store(context, writing=True, need=entry['size'] + MIN_FREE) as (pin, live):
        require(pin is not None, 'STORE_MISSING', NO_CARD)
        return store_save(context, pin, live, entry, 'the card')


@contextlib.contextmanager
def sideload_store(context, size):
    """The image store for a file of SIZE bytes arriving over adb (2026-11-10). userdata is mounted
    for the transfer when the recovery has not mounted it, and stays pinned until the file is in place."""
    volume = context.volume('userdata')
    mounted_here = not volume['mounted']
    # Opened read-only (browsing files, choosing an image), userdata is made writable for the
    # transfer and read-only again after it (2026-11-10: the computer only saw "failed", the
    # reason was in the phone's log).
    reopen = volume['mounted'] and volume['read_only'] and volume.get('mount_owner') == 'filesystem'
    if mounted_here or reopen:
        context.mount('userdata', 'rw', True, volume['volume_token'])
    try:
        with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
            require_userdata(pin)
            vfs = os.fstatvfs(pin.fd)
            free = vfs.f_bavail * vfs.f_frsize
            require(free >= size + MIN_FREE, 'INSUFFICIENT_SPACE', 'There is not enough space on userdata for this file',
                    available_bytes=free, required_bytes=size + MIN_FREE)
            yield store_folder(live)
            os.sync()
    finally:
        if mounted_here:
            os.sync()
            context.unmount('userdata')
        elif reopen:
            os.sync()
            # Still writable if this fails: nothing is lost, and Files shows the mode as it is.
            with contextlib.suppress(OSError, fs.FilesystemError):
                context.mount('userdata', 'ro', True, context.volume('userdata')['volume_token'])


# adb sideload without a Linux userdata (2026-11-11): Android holds userdata, so a file arriving
# over adb goes to a card's store, or to this folder on cache when it fits there. Cache keeps room
# for a copy of BOOT; Wipe cache removes the folder, the recovery's own folder is elsewhere.
SIDELOAD_CACHE_DIR = 'sideload'
CACHE_KEEP = 48 << 20


@contextlib.contextmanager
def cache_place(context, size):
    """The sideload folder on cache, writable for the transfer and read-only afterwards."""
    volume = context.volume('cache')
    require(volume.get('mountable'), 'STORE_MISSING', NO_CARD)
    was_writable = volume['mounted'] and not volume['read_only']
    if not was_writable:
        require(not volume['mounted'] or volume.get('mount_owner') == 'filesystem', 'STORE_READ_ONLY',
                'Cache is mounted elsewhere: unmount it in Files first')
        context.mount('cache', 'rw', True, volume['volume_token'])
    try:
        with fs.PinnedVolume(context, 'cache', writing=True) as pin, Tree(pin) as live:
            vfs = os.fstatvfs(pin.fd)
            free = vfs.f_bavail * vfs.f_frsize
            require(free >= size + CACHE_KEEP, 'INSUFFICIENT_SPACE',
                    'There is no card, and cache has no room for this file: insert an SD card or attach a USB drive',
                    available_bytes=free, required_bytes=size + CACHE_KEEP)
            if live.exists(SIDELOAD_CACHE_DIR):
                require(stat.S_ISDIR(live.lstat(SIDELOAD_CACHE_DIR).st_mode), 'STORE_INVALID', 'The sideload folder on cache is not a folder')
            else:
                live.mkdirs(SIDELOAD_CACHE_DIR)
            yield {'folder': real_path(live) / SIDELOAD_CACHE_DIR, 'volume': 'cache', 'token': pin.volume['volume_token'],
                   'dir': '/' + SIDELOAD_CACHE_DIR}
            os.sync()
    finally:
        if not was_writable:
            # Read-only afterwards, also when it was not mounted: the file is then reviewed and installed from there.
            with contextlib.suppress(OSError, fs.FilesystemError):
                context.mount('cache', 'ro', True, context.volume('cache')['volume_token'])


@contextlib.contextmanager
def sideload_place(context, size):
    """Where a file arriving over adb sideload goes: {'folder', 'volume', 'token', 'dir'}.
    A Linux userdata keeps it in its image store, as before. Otherwise a card's store takes it, or
    cache when there is no card with room and the file fits there."""
    if linux_userdata(context):
        with sideload_store(context, size) as folder:
            yield {'folder': folder, 'volume': 'userdata', 'token': context.volume('userdata')['volume_token'], 'dir': '/' + STORE_DIR}
        return
    entered = False
    try:
        with card_store(context, writing=True, need=size + MIN_FREE) as (pin, live):
            if pin is not None:
                place = {'folder': store_folder(live), 'volume': pin.volume['id'], 'token': pin.volume['volume_token'],
                         'dir': '/' + STORE_DIR}
                entered = True
                yield place
                return
    except fs.FilesystemError as error:
        if entered or error.code != 'INSUFFICIENT_SPACE':
            raise
    with cache_place(context, size) as place:
        yield place


def images_delete(context, name, confirmed=False):
    """Delete a saved image and what the catalog said about it."""
    context.confirm(confirmed)
    require(isinstance(name, str) and STORE_FILE.fullmatch(name) and not name.endswith(STORE_META),
            'INVALID_ARGUMENT', 'Choose an image of the store')

    def drop(pin, live):
        path = STORE_DIR + '/' + name
        require(live.exists(path) and stat.S_ISREG(live.lstat(path).st_mode), 'IMAGE_NOT_FOUND', 'The image is not saved on the phone')
        store = store_folder(live)
        (store / name).unlink()
        with contextlib.suppress(FileNotFoundError):
            (store / (name + STORE_META)).unlink()
        os.sync()
        vfs = os.fstatvfs(pin.fd)
        return {'deleted': name, 'images': store_images(live), 'free_bytes': vfs.f_bavail * vfs.f_frsize, **store_place(pin)}

    if linux_userdata(context):
        with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
            require_userdata(pin)
            return drop(pin, live)
    with card_store(context, writing=True, holding=name) as (pin, live):
        require(pin is not None, 'IMAGE_NOT_FOUND', 'The image is not saved on the phone')
        return drop(pin, live)


def download_before_erase(context, remote, progress):
    """The catalog image of a clean installation, saved in the store before anything is erased.
    None when userdata has no room for it now: then it is formatted and the image comes after."""
    if foreign_userdata(context):
        return None  # nothing of Android's userdata can hold it: formatted first
    with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
        require_userdata(pin)
        vfs = os.fstatvfs(pin.fd)
        if vfs.f_bavail * vfs.f_frsize < remote['size'] + MIN_FREE:
            return None
        store = store_folder(live)
        for name in os.listdir(store):
            if name.startswith('.install-'):
                (store / name).unlink()
        target = store / ('.install-' + remote['file'])
        store_download(context, live, remote, target, progress)
        return target.name


def store_in_use(context):
    """Whether userdata holds saved images that a format would destroy."""
    try:
        with fs.PinnedVolume(context, 'userdata') as pin, Tree(pin) as live:
            return bool(store_images(live))
    except fs.FilesystemError:
        return False


def clear_userdata(live):
    """A clean installation that keeps the image store: every system and /home go, the saved
    images stay (2026-11-10). The partition is not formatted, so the image being installed or the
    download made just before is not lost."""
    root = real_path(live)
    for name in live.list():
        if name in (STORE_DIR, 'lost+found'):
            continue
        target = root / name
        shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
    os.sync()


def has_linux_root(tree):
    return tree.exists('usr') and tree.exists('sbin') and (tree.exists('etc/os-release') or tree.exists('usr/lib/os-release'))


def script_stamp(context, script):
    """The chosen setup script: its bytes, hash and identity. Recovery only copies it."""
    with fs.PinnedVolume(context, script['volume'], script['token']) as pin, Tree(pin) as tree:
        path = logical_path(script['path'])
        # Or one saved on the phone (2026-11-10): cache:/a22x-recovery/scripts, by an Android app or the recovery.
        saved = script['volume'] == 'cache' and path.startswith(SCRIPT_STORE) and '/' not in path[len(SCRIPT_STORE):]
        require(context.mode == 'fixture' or saved or pin.volume.get('external') is True, 'EXTERNAL_SCRIPT_REQUIRED',
                'Setup scripts must be on external SD/USB storage')
        fd = tree.regular(path)
        try:
            stamp = fs.stamp(os.fstat(fd))
        finally:
            os.close(fd)
        require(stamp[3] <= SCRIPT_LIMIT, 'SCRIPT_TOO_LARGE', 'A setup script can be at most 1 MiB')
        data = tree.read(path, SCRIPT_LIMIT)
    require(b'\0' not in data, 'SCRIPT_NOT_TEXT', 'The setup script must be a text file')
    return data, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'identity': list(stamp)}


# The images the installer page offers for download. Each one is pinned by size and checksum. The
# recovery keeps the pins current (2026-11-08): it reads the official image list and checksum file
# of every image over HTTPS, and an image that left the server or changed there is replaced by the
# newest one the server lists. Image data may come from a plain HTTP mirror: its checksum decides.
CATALOG = Path(__file__).resolve().parent / 'catalog.json'
CATALOG_KEYS = ('id', 'name', 'version', 'family', 'size', 'file', 'format', 'minimal', 'notes', 'desktop')
# Android images of the catalog (2026-11-10): GSIs, with their Android version, type (aosp: plain
# AOSP; gms: with Google's apps), region, build, security patch and the size of their system.img.
ANDROID_KEYS = ('id', 'category', 'name', 'version', 'android', 'sdk', 'variant', 'region', 'family', 'maker', 'build', 'patch',
                'size', 'file', 'system_bytes')
# This phone's vendor interface: Samsung's vendor of A226BXXSBDZC3 (and every A22x vendor, launched
# with Android 11 and frozen there) declares target-level 5, read from super/vendor 2026-11-10. A
# GSI fits when its framework still supports that level; Android 16 dropped it.
VENDOR_FCM_LEVEL = 5
PIN_KEYS = ('url', 'file', 'size', 'sha256', 'sha512', 'signature_url', 'signature_sha256')
KEYS = Path(__file__).resolve().parent / 'keys'  # Signing keys of the distributions, pinned here.
CATALOG_STATE = Path('a22x-recovery/catalog.json')  # On the settings volumes, beside the settings.
CATALOG_RAM = Path('/run/a22x-recovery/catalog-state.json')
CATALOG_FRESH = 6 * 3600  # A server checked this recently is not asked again.
PLAN_FRESH = 1800  # The image of a plan is checked again when its pin is older.
CA_FILE = Path('/etc/ssl/certs/ca-certificates.crt')
CLOCK_FLOOR = 1793491200  # 2026-11-01 UTC: a clock before it fails every certificate.
USER_AGENT = 'A22x-Recovery'
DOWNLOAD_DIR = '.a22x-download'
WIFI_STATE = Path('/run/a22x-wifi/state.json')


def https_supported():
    try:
        import ssl  # noqa: F401
    except ImportError:
        return False
    return True


def tls_context():
    import ssl
    return ssl.create_default_context(cafile=str(CA_FILE) if CA_FILE.is_file() else None)


def ensure_clock(context):
    """Certificates need the date. A clock that cannot be right (reset, or read in the form Samsung's
    Android leaves in the hardware clock: the year 2086) asks a time server (SNTP); the hardware
    clock is left alone."""
    import clock
    clock.ensure(context.mode)


def secure_url(context, url):
    """Lists and checksum files are read over HTTPS only; fixtures serve them from this machine."""
    return url.startswith('https://') or (context.mode == 'fixture' and url.startswith('http://127.0.0.1:'))


def url_opener(context, metadata):
    import urllib.error
    import urllib.request

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, address):
            # A list or a checksum must not come from a plain HTTP mirror; image data may.
            if metadata and not secure_url(context, address):
                raise urllib.error.URLError('redirected to a plain HTTP address')
            return super().redirect_request(request, fp, code, message, headers, address)
    handlers = [Redirects()]
    if https_supported():
        handlers.append(urllib.request.HTTPSHandler(context=tls_context()))
    return urllib.request.build_opener(*handlers)


def remote_raw(context, url, limit=1 << 20, signed=False):
    """Bytes of a list or checksum file, over HTTPS only; SIGNED: a signature, which may come over
    plain HTTP because the pinned key decides (Arch Linux ARM has no HTTPS)."""
    import urllib.request
    require(secure_url(context, url) or (signed and url.startswith('http://')), 'CATALOG_INSECURE',
            'The catalog reads image lists over HTTPS only')
    with url_opener(context, not signed).open(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=30) as response:
        data = response.read(limit + 1)
    require(len(data) <= limit, 'CATALOG_TOO_LARGE', 'A list on the server is larger than expected')
    return data


def remote_text(context, url, limit=1 << 20):
    return remote_raw(context, url, limit).decode('utf-8', 'replace')


def pinned_key(context, name):
    require(isinstance(name, str) and re.fullmatch(r'[a-z0-9-]{1,40}\.(asc|gpg)', name) is not None, 'CATALOG_INVALID',
            'The catalog entry is not usable')
    path = (context.root / 'keys' if context.mode == 'fixture' else KEYS) / name
    require(path.is_file(), 'CATALOG_KEY_MISSING', 'The signing key of this image is not in this recovery')
    return path.read_bytes()


def signed_sums(context, base, listed, data, source):
    """The checksum list once its signature verifies with the distribution's pinned key: a
    clearsigned list (Fedora) or one with a detached signature (Ubuntu SHA256SUMS.gpg)."""
    import openpgp
    import urllib.parse
    key = pinned_key(context, source['key'])
    try:
        if source.get('clearsigned'):
            return openpgp.verify_clearsigned(data, key).decode('utf-8', 'replace')
        name = source.get('sums_sig', '{sums}.sig').replace('{sums}', listed)
        signature = remote_raw(context, urllib.parse.urljoin(base, urllib.parse.quote(name)), 1 << 16)
        openpgp.verify_bytes(data, signature, key)
        return data.decode('utf-8', 'replace')
    except openpgp.SignatureError as error:
        fail('CATALOG_SIGNATURE_BAD', 'The checksum list is not signed by its distribution', reason=str(error)[:120])


def remote_size(context, url):
    import urllib.request
    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': USER_AGENT})
    with url_opener(context, False).open(request, timeout=30) as response:
        length = response.headers.get('Content-Length', '')
    return int(length) if length.isdigit() and int(length) > 0 else None


def natural(name):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', name)]


def remote_listing(context, url):
    """The names an index page links to, relative to it."""
    import urllib.parse
    names = set()
    for link in re.findall(r'href="([^"]+)"', remote_text(context, url, 4 << 20)):
        link = urllib.parse.unquote(link.split('#')[0].split('?')[0])
        if link and not link.startswith(('http:', 'https:', '/', '..')):
            names.add(link[2:] if link.startswith('./') else link)
    return names


def parse_sums(text, name):
    """The checksum of NAME in a GNU list (HASH  name, HASH *name) or a BSD one (SHA256 (name) = HASH)."""
    for line in text.splitlines():
        line = line.strip()
        match = re.fullmatch(r'([0-9a-fA-F]{64}|[0-9a-fA-F]{128})\s+\*?(\S.*)', line)
        if match and match.group(2).strip().rsplit('/', 1)[-1] == name:
            return match.group(1).lower()
        match = re.fullmatch(r'SHA(?:256|512) ?\((.+)\) ?= ?([0-9a-fA-F]{64}|[0-9a-fA-F]{128})', line)
        if match and match.group(1) == name:
            return match.group(2).lower()
    return None


def resolve_signed_file(context, source):
    """An image with its own detached signature (Arch Linux ARM): its size, and the signature
    that the download must match; the signature itself is checked after the download."""
    import openpgp
    url, signature_url = source.get('url', ''), source.get('signature', '')
    require(re.fullmatch(r'https?://[^\s]+', url) and re.fullmatch(r'https?://[^\s]+', signature_url)
            and re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', source.get('file', '')), 'CATALOG_INVALID', 'The catalog entry is not usable')
    signature = remote_raw(context, signature_url, 1 << 16, signed=True)
    try:
        issuers = openpgp.read_signature(signature)['issuers']
        keys = {k['keyid'] for k in openpgp.read_keys(pinned_key(context, source['key']))}
    except openpgp.SignatureError as error:
        fail('CATALOG_SIGNATURE_BAD', 'The image signature cannot be read', reason=str(error)[:120])
    require(bool(issuers & keys), 'CATALOG_SIGNATURE_BAD', 'The image is not signed by its distribution')
    size = remote_size(context, url)
    require(size, 'CATALOG_SIZE_UNKNOWN', 'The server does not tell the size of the image')
    return {'url': url, 'file': source['file'], 'size': size, 'signature_url': signature_url,
            'signature_sha256': hashlib.sha256(signature).hexdigest()}


def resolve_github(context, source):
    """The newest release asset of a GitHub project and its .sha256 (Manjaro ARM publishes no
    signature: the checksum comes from github.com over HTTPS)."""
    repo = source.get('repo', '')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) is not None and source.get('file'), 'CATALOG_INVALID',
            'The catalog entry is not usable')
    api = source.get('api', 'https://api.github.com') if context.mode == 'fixture' else 'https://api.github.com'
    releases = json.loads(remote_text(context, '%s/repos/%s/releases?per_page=8' % (api, repo), 4 << 20))
    for release in releases if isinstance(releases, list) else []:
        if release.get('draft') or (release.get('prerelease') and not source.get('prerelease')):
            continue
        assets = {a.get('name'): a for a in release.get('assets', []) if isinstance(a, dict)}
        for name in sorted((n for n in assets if n and re.fullmatch(source['file'], n)), key=natural, reverse=True):
            sums, image = assets.get(name + '.sha256'), assets[name]
            if not sums or not re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', name):
                continue
            checksum = parse_sums(remote_text(context, sums.get('browser_download_url', '')), name)
            if checksum and isinstance(image.get('size'), int) and image['size'] > 0:
                return {'url': image.get('browser_download_url', ''), 'file': name, 'size': image['size'],
                        ('sha512' if len(checksum) == 128 else 'sha256'): checksum,
                        'version': str(release.get('tag_name', ''))[:24]}
    fail('CATALOG_SOURCE_EMPTY', 'The server lists no image of this kind now')


def resolve_source(context, source):
    """The newest image SOURCE describes, from the server's own list and checksum file:
    {url, file, size, sha256 or sha512}. SOURCE_EMPTY when the server lists none."""
    import urllib.parse
    if source.get('kind') == 'signed-file':
        return resolve_signed_file(context, source)
    if source.get('kind') == 'github':
        return resolve_github(context, source)
    index = source.get('index', '')
    require(secure_url(context, index) and index.endswith('/') and source.get('file') and source.get('sums'),
            'CATALOG_INVALID', 'The catalog entry is not usable')
    bases = [index]
    if source.get('dir'):
        found = sorted((n for n in remote_listing(context, index) if re.fullmatch(source['dir'], n)), key=natural, reverse=True)
        bases = [urllib.parse.urljoin(index, urllib.parse.quote(n)) for n in found[:3]]
    for base in bases:
        names = remote_listing(context, base)
        files = sorted((n for n in names if re.fullmatch(source['file'], n)), key=natural, reverse=True)
        sums = sorted((n for n in names if re.fullmatch(source['sums'], n)), key=natural, reverse=True)
        texts = {}
        for name in files[:3]:
            if not re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', name):
                continue
            for listed in sums[:4]:
                if listed not in texts:
                    data = remote_raw(context, urllib.parse.urljoin(base, urllib.parse.quote(listed)))
                    texts[listed] = signed_sums(context, base, listed, data, source) if source.get('key') else data.decode('utf-8', 'replace')
                checksum = parse_sums(texts[listed], name)
                if checksum:
                    url = urllib.parse.urljoin(base, urllib.parse.quote(name))
                    size = remote_size(context, url)
                    require(size, 'CATALOG_SIZE_UNKNOWN', 'The server does not tell the size of the image')
                    return {'url': url, 'file': name, 'size': size, ('sha512' if len(checksum) == 128 else 'sha256'): checksum}
    fail('CATALOG_SOURCE_EMPTY', 'The server lists no image of this kind now')


def catalog_builtin(context):
    path = context.root / 'catalog.json' if context.mode == 'fixture' else CATALOG
    if not path.is_file():
        return {'pinned': 0, 'images': []}
    return json.loads(path.read_text())


def catalog_ram(context):
    return context.root / 'catalog-state.json' if context.mode == 'fixture' else CATALOG_RAM


def catalog_volumes(context, mode, action):
    """ACTION(root) on the cache volume of the settings; the command holds the operation lock.
    Never userdata: the next clean installation erases it, and a file there makes it look used."""
    import settings
    try:
        with settings.volume_root(context, 'cache', mode) as root:
            return action(root)
    except (OSError, ValueError, fs.FilesystemError):
        return None


def catalog_state(context):
    """Pins refreshed from the servers, {id: {url, file, size, sha256|sha512, checked}}, or
    {id: {unavailable, checked}} for an image its server no longer lists. Kept in RAM and on
    cache, so a refresh survives a reboot and a clean installation of userdata."""
    ram = catalog_ram(context)
    if ram.is_file():
        try:
            return json.loads(ram.read_text()).get('images', {})
        except ValueError:
            pass
    def read(root):
        path = root / CATALOG_STATE
        return json.loads(path.read_text()).get('images', {}) if path.is_file() else {}
    images = catalog_volumes(context, 'ro', read) or {}
    write_catalog_ram(context, images)
    return images


def write_catalog_ram(context, images):
    ram = catalog_ram(context)
    try:
        ram.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = ram.with_suffix('.new')
        temporary.write_text(json.dumps({'images': images}, sort_keys=True))
        temporary.replace(ram)
    except OSError:
        pass


def save_catalog_state(context, images):
    write_catalog_ram(context, images)
    data = json.dumps({'schema': 'a22x.catalog.state.v1', 'images': images}, sort_keys=True).encode()
    def write(root):
        path = root / CATALOG_STATE
        path.parent.mkdir(mode=0o700, exist_ok=True)
        temporary = path.with_suffix('.new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        temporary.replace(path)
        return True
    return catalog_volumes(context, 'rw', write) is True


def catalog_entries(context):
    """The built-in entries with the pins a later check of their servers found."""
    built = catalog_builtin(context)
    pinned = int(built.get('pinned', 0))
    state = catalog_state(context)
    entries = []
    for entry in built.get('images', []):
        fresh = state.get(entry.get('id'), {})
        if isinstance(fresh, dict) and int(fresh.get('checked', 0)) > pinned:
            if fresh.get('unavailable') is True:
                entry = {**entry, 'unavailable': True}
            elif all(k in fresh for k in ('url', 'file', 'size')):
                entry = {**{k: v for k, v in entry.items() if k not in PIN_KEYS}, **{k: fresh[k] for k in PIN_KEYS if k in fresh}}
                if fresh.get('version'):
                    entry['version'] = fresh['version']
            entry['checked'] = int(fresh['checked'])
        entries.append(entry)
    return entries


def refresh_catalog(context, only=None, max_age=CATALOG_FRESH):
    """Ask the server of each image (or of ONLY) for its newest version. A network problem leaves
    the pins as they are; a server that no longer lists the image marks it unavailable."""
    ensure_clock(context)
    pinned = int(catalog_builtin(context).get('pinned', 0))
    state = dict(catalog_state(context))
    current = {e['id']: e for e in catalog_entries(context) if 'id' in e}
    now = int(time.time())
    todo = [e for e in catalog_builtin(context).get('images', []) if e.get('source') and (only is None or e.get('id') == only)
            and now - int(state.get(e.get('id'), {}).get('checked', 0)) >= max_age]
    def check(entry):
        try:
            return entry, resolve_source(context, entry['source']), None
        except fs.FilesystemError as error:
            return entry, None, error.code
        except (OSError, ValueError) as error:  # URLError, HTTPError and SSLError are OSErrors.
            if hasattr(error, 'close'):
                error.close()
            reason = getattr(error, 'reason', None) or getattr(error, 'code', None) or error
            return entry, None, '%s: %s' % (type(error).__name__, str(reason)[:70])
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, todo))
        # A server that did not answer is asked once more: just after joining Wi-Fi, names and
        # routes can still be settling (2026-11-08: a first check failed, one a minute later passed).
        again = [entry for entry, found, problem in results if found is None and problem not in ('CATALOG_SOURCE_EMPTY', 'CATALOG_SIGNATURE_BAD')]
        if again:
            time.sleep(3)
            retried = {entry['id']: (entry, found, problem) for entry, found, problem in pool.map(check, again)}
            results = [retried.get(entry['id'], (entry, found, problem)) for entry, found, problem in results]
    counts = {'checked': 0, 'updated': 0, 'unavailable': 0, 'failed': 0, 'problems': {}}
    for entry, found, problem in results:
        if found:
            counts['checked'] += 1
            before = current.get(entry['id'], entry)
            if before.get('unavailable') or any(found.get(k) != before.get(k) for k in PIN_KEYS):
                counts['updated'] += 1
            state[entry['id']] = {**found, 'checked': max(now, pinned + 1)}
        elif problem == 'CATALOG_SOURCE_EMPTY':
            counts['unavailable'] += 1
            state[entry['id']] = {'unavailable': True, 'checked': max(now, pinned + 1)}
        else:
            counts['failed'] += 1
            counts['problems'][entry['id']] = str(problem)[:90]
    if counts['checked'] or counts['unavailable']:
        save_catalog_state(context, state)
    return counts


def catalog(context, refresh=False, force=False):
    """The installer page: the images to download, and whether recovery can reach them now."""
    https = https_supported()
    joined = context.mode == 'fixture'
    if not joined:
        try:
            joined = json.loads(WIFI_STATE.read_text()).get('connected') is True
        except (OSError, ValueError):
            joined = False
    counts = refresh_catalog(context, max_age=0 if force else CATALOG_FRESH) if refresh and joined else None
    images = [{**{k: e[k] for k in CATALOG_KEYS if k in e}, 'unavailable': e.get('unavailable') is True, 'checked': e.get('checked', 0),
               'signed': bool((e.get('source') or {}).get('key')),
               'reachable': (https or e['url'].startswith('http://')) and e.get('unavailable') is not True}
              for e in catalog_entries(context)]
    android = []
    for e in android_entries(context):
        levels = e.get('vendor_levels') or []
        # What a start on this phone showed counts for more than the rule (2026-11-11: LineageOS 22.2
        # vanilla fits by the rule and restarted into the bootloader).
        tried = e.get('tried') if e.get('tried') in ('starts', 'fails') else ''
        fits = VENDOR_FCM_LEVEL in levels and tried != 'fails'
        android.append({**{k: e[k] for k in ANDROID_KEYS if k in e}, 'fits': fits, 'tried': tried,
                        'fit_note': str(e.get('fit_note', ''))[:90] if fits else 'Made for newer vendors: it may not start on this phone',
                        'reachable': https or e['url'].startswith('http://')})
    result = {'images': images, 'android': android, 'https': https, 'network': joined}
    if counts is not None:
        result['refresh'] = counts
    return result


def android_entries(context):
    """The Android images of the catalog: pinned, never refreshed (Google's GSI links stay)."""
    return [e for e in catalog_builtin(context).get('android', []) if isinstance(e, dict)]


def catalog_entry(context, image_id):
    for entry in catalog_entries(context) + android_entries(context):
        if entry.get('id') == image_id:
            require(entry.get('unavailable') is not True, 'CATALOG_IMAGE_GONE',
                    'The server no longer offers this image. Choose another one.')
            # Debian publishes SHA-512 only; the others SHA-256; Arch Linux ARM signs the image itself.
            checked = re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', '')) or re.fullmatch(r'[0-9a-f]{128}', entry.get('sha512', ''))
            if not checked and (entry.get('source') or {}).get('kind') == 'signed-file':
                entry = {**entry, 'key': entry['source'].get('key', '')}
                checked = re.fullmatch(r'[0-9a-f]{64}', entry.get('signature_sha256', '')) and \
                    re.fullmatch(r'https?://[^\s]+', entry.get('signature_url', '')) and pinned_key(context, entry['key'])
            require(re.fullmatch(r'https?://[^\s]+', entry.get('url', '')) and checked
                    and isinstance(entry.get('size'), int) and entry['size'] > 0, 'CATALOG_INVALID', 'The catalog entry is not usable')
            require(re.fullmatch(r'[A-Za-z0-9._+-]{1,200}', entry.get('file', '')) is not None, 'CATALOG_INVALID',
                    'The catalog entry is not usable')
            return entry
    fail('CATALOG_IMAGE_UNKNOWN', 'This image is not in the catalog of this recovery')


def downloaded_differently(context, remote, error):
    """A download that failed because the server replaced the image updates the catalog, and says so."""
    if not isinstance(error, fs.FilesystemError) or error.code not in ('DOWNLOAD_FAILED', 'DOWNLOAD_CHECKSUM_MISMATCH', 'DOWNLOAD_TOO_LARGE',
                                                                            'DOWNLOAD_SIGNATURE_CHANGED'):
        return
    try:
        refresh_catalog(context, only=remote['id'], max_age=0)
        entry = next((e for e in catalog_entries(context) if e.get('id') == remote['id']), {})
    except fs.FilesystemError:
        return
    if entry.get('unavailable') or any(entry.get(k) != remote.get(k) for k in PIN_KEYS if k in remote or k in entry):
        fail('CATALOG_UPDATED', 'The image changed on its server. The catalog now lists the new version: start the installation again.')


# Package search in the repositories of the chosen distribution, so an extra program is named as
# the distribution names it and is known to exist for aarch64 (2026-11-08).
SEARCH_SITES = {'debian': ('https://packages.debian.org', 'trixie'), 'mobian': ('https://packages.debian.org', 'trixie'),
                'ubuntu': ('https://packages.ubuntu.com', 'resolute')}
PACKAGE_NAME = r'[a-z0-9][a-z0-9+._-]{0,99}'


def package_search(context, family, release, query, limit=24):
    """[{name, version, summary, available}] for QUERY in FAMILY's repositories; available means
    built for arm64 (Debian, Ubuntu) or listed by Arch Linux ARM; None when it cannot be told."""
    import html as markup
    import urllib.parse
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+._-]{1,39}', query or '') is not None, 'INVALID_QUERY',
            'Search for 2 to 40 letters, digits, + . _ or -')
    require(family in ('debian', 'mobian', 'ubuntu', 'fedora', 'arch', 'manjaro'), 'SEARCH_UNSUPPORTED',
            'Package search is not available for this system')
    ensure_clock(context)
    term = urllib.parse.quote(query.lower())
    results = []
    if family in SEARCH_SITES:
        base, suite = SEARCH_SITES[family]
        suite = release if release and re.fullmatch(r'[a-z]{3,20}', release) else suite
        page = remote_text(context, '%s/search?keywords=%s&searchon=names&suite=%s&section=all' % (base, term, suite), 4 << 20)
        for name, body in re.findall(r'<h3>Package ([a-z0-9][a-z0-9+.-]*)</h3>(.*?)</ul>', page, re.S):
            entry = re.search(r'<li class="%s">.*?\):\s*(.*?)\s*<br>\s*([^<]+)' % re.escape(suite), body, re.S)
            if not entry:
                continue
            versions = [line.strip() for line in entry.group(2).splitlines() if ':' in line]
            archs = ' '.join(v.split(':', 1)[1] for v in versions)
            # Ubuntu marks the section in the text: "[<strong class="pmarker">universe</strong>]".
            summary = re.sub(r'<[^>]+>', '', entry.group(1))
            summary = re.sub(r'\[\s*(universe|multiverse|restricted|main|contrib|non-free[a-z-]*)\s*\]', '', summary)
            results.append({'name': name, 'version': versions[0].split(':', 1)[0].strip() if versions else '',
                            'summary': markup.unescape(re.sub(r'\s+', ' ', summary)).strip()[:120],
                            'available': bool(re.search(r'\b(arm64|all)\b', archs))})
    elif family == 'fedora':
        number = release if release and re.fullmatch(r'\d{2}', release) else '45'
        page = remote_text(context, 'https://packages.fedoraproject.org/search?query=%s&releases=Fedora+%s' % (term, number), 4 << 20)
        seen = set()
        for name, summary in re.findall(r'href="/pkgs/[^/"]+/([^/"]+)/?">\s*<span>[^<]*</span></a>\s*-\s*([^<\n]*)', page):
            if name not in seen and re.fullmatch(PACKAGE_NAME, name):
                seen.add(name)
                results.append({'name': name, 'version': '', 'summary': markup.unescape(summary).strip()[:120], 'available': None})
    else:
        data = json.loads(remote_text(context, 'https://archlinux.org/packages/search/json/?q=%s' % term, 4 << 20))
        names = []
        for item in data.get('results', []):
            if item.get('pkgname') not in names and re.fullmatch(PACKAGE_NAME, item.get('pkgname', '')):
                names.append(item['pkgname'])
                results.append({'name': item['pkgname'], 'version': item.get('pkgver', ''),
                                'summary': (item.get('pkgdesc') or '')[:120], 'available': None})
        # Arch Linux ARM builds most of them; its own package page tells.
        def listed(entry):
            import urllib.request
            request = urllib.request.Request('https://archlinuxarm.org/packages/aarch64/%s' % urllib.parse.quote(entry['name']),
                                             method='HEAD', headers={'User-Agent': USER_AGENT})
            try:
                with url_opener(context, True).open(request, timeout=15) as response:
                    entry['available'] = response.status == 200
            except OSError as error:
                if getattr(error, 'code', None) == 404:
                    entry['available'] = False
                if hasattr(error, 'close'):
                    error.close()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(listed, results[:12]))
    # The name asked for first, then names that start with it or have it as a word, then the rest.
    word = query.lower()
    rank = lambda r: (r['name'] != word, not r['name'].startswith(word), word not in re.split(r'[-+._]', r['name']))
    results = sorted(results, key=rank)
    return {'family': family, 'query': query, 'results': results[:limit]}


def linux_entry(context, image_id):
    """A catalog entry for a Linux installation: an Android image is never unpacked as a system."""
    entry = plan_entry(context, image_id)
    require(entry.get('family') != 'android', 'CATALOG_NOT_LINUX', 'This is an Android image: install it from the Android page')
    return entry


def plan_entry(context, image_id):
    """The catalog entry of a plan, checked with its server when the pin is old. Without network
    the pin stays; the download then tells."""
    try:
        refresh_catalog(context, only=image_id, max_age=PLAN_FRESH)
    except fs.FilesystemError:
        pass
    return catalog_entry(context, image_id)


FETCH_TRIES_MAX = 200


def fetch(context, url, target, size, checksum, progress, attempts=4, signature=None):
    """Download URL to TARGET, resuming after a dropped connection; the size and SHA-256 must match."""
    import http.client
    import urllib.error
    import urllib.request
    require(url.startswith('http://') or https_supported(), 'HTTPS_UNAVAILABLE',
            'This recovery cannot download over HTTPS yet. Choose another image or a file.')
    opener = url_opener(context, False)
    done, problem = 0, None
    # ATTEMPTS counts tries in a row that brought nothing: on a weak network every try that adds bytes
    # resumes from there (2026-11-08: Debian stopped at 183 of 338 MB after four short tries).
    stalls, tries = 0, 0
    while stalls < attempts and tries < FETCH_TRIES_MAX:
        tries += 1
        before = done
        headers = {'User-Agent': 'A22x-Recovery'}
        if done:
            headers['Range'] = 'bytes=%d-' % done
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                if done and response.status != 206:
                    done = 0  # The server sends the whole file again.
                with open(target, 'r+b' if done else 'wb') as out:
                    out.seek(done)
                    out.truncate()
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        require(done <= size, 'DOWNLOAD_TOO_LARGE', 'The download is larger than the catalog says')
                        progress('image-download', done, size)
                    out.flush()
                    os.fsync(out.fileno())
            if done == size:
                break
        except urllib.error.HTTPError as error:
            problem = error
            error.close()
            if error.code in (404, 410):
                break  # Gone from the server: the catalog check finds what replaced it.
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as error:
            problem = error  # IncompleteRead too: the reply ended before its announced length.
        stalls = 0 if done > before else stalls + 1
        time.sleep(min(2 + 3 * stalls, 15))
    if done != size:
        fail('DOWNLOAD_FAILED', 'The image could not be downloaded. Check the Wi-Fi connection and try again.',
             reason=str(problem)[:200] if problem else 'incomplete')
    if signature is not None:
        # The image is signed itself: its signature must be the planned one and verify with the pinned key.
        import openpgp
        data = remote_raw(context, signature['url'], 1 << 16, signed=True)
        require(hashlib.sha256(data).hexdigest() == signature['sha256'], 'DOWNLOAD_SIGNATURE_CHANGED',
                'The image changed on its server. Try again.')
        progress('image-verify', 0, 1)
        try:
            openpgp.verify_file(target, data, pinned_key(context, signature['key']))
        except openpgp.SignatureError as error:
            fail('DOWNLOAD_SIGNATURE_BAD', 'The downloaded image is not signed by its distribution. Try again.', reason=str(error)[:120])
        progress('image-verify', 1, 1)
        return
    digest_ = hashlib.sha512() if len(checksum) == 128 else hashlib.sha256()
    with open(target, 'rb') as data:
        while block := data.read(1 << 20):
            digest_.update(block)
    require(digest_.hexdigest() == checksum, 'DOWNLOAD_CHECKSUM_MISMATCH',
            'The downloaded image differs from the catalog. Try again.')


def image_plan(context, source_volume, image_path, options, expected_source=None, wipe=False, remote=None):
    import image_import
    saved = store_name(image_path) if remote is None and source_volume == 'userdata' else None
    if remote is None and saved is None:
        require(source_volume != 'userdata', 'BUNDLE_ON_USERDATA', 'Choose an image on external SD/USB storage')
    profile, boot = load_profile(context), boot_for_linux(context, fresh=wipe)
    # 2026-11-11: Android's userdata (or one that cannot be read) holds nothing to mount or keep. A
    # clean installation is planned on the partition as it will be once it is formatted for Linux.
    foreign = foreign_userdata(context)
    require(not foreign or (wipe and saved is None), 'LINUX_USERDATA_REQUIRED',
            'Userdata holds no Linux system: only a new system, which formats it, can be installed')
    with contextlib.ExitStack() as stack:
        if foreign:
            target_pin = live = None
            target_volume = context.volume('userdata')
        else:
            target_pin = stack.enter_context(fs.PinnedVolume(context, 'userdata', writing=True))
            live = stack.enter_context(Tree(target_pin))
            target_volume = target_pin.volume
        source_pin = source = None
        if saved is not None:
            source = live
        elif remote is None:
            source_pin = stack.enter_context(fs.PinnedVolume(context, source_volume, expected_source))
            source = stack.enter_context(Tree(source_pin))
        if not foreign:
            require_userdata(target_pin)
        require(remote is not None or saved is not None or context.mode == 'fixture' or source_pin.volume.get('external') is True,
                'EXTERNAL_BUNDLE_REQUIRED', 'Images must be on external SD/USB storage')
        state = read_state(live) if live is not None and live.exists(MANAGER) else None
        if state and not wipe:
            require(not read_transaction(live), 'SWITCH_PENDING', 'Repair the pending switch before installing')
            require(len(slots(live, state)) < MAX_SLOTS, 'TOO_MANY_SLOTS', 'The slot limit has been reached')
        # The installer page erases userdata first, when the installation starts.
        clean = wipe or (state is None and userdata_empty(live))
        if wipe:
            state = None
        if state is None and not clean:
            require(has_linux_root(live), 'USERDATA_NOT_EMPTY',
                    'Userdata holds files but no Linux system. Format it in Files for a clean install.')
        if clean:
            require(options['account'] == 'new', 'ACCOUNT_REQUIRED',
                    'A clean install needs a new account: userdata has none to keep')
            needs_network = options['desktop'] not in ('console', 'image') or bool(options.get('apps')) or bool(options.get('packages'))
            require(not needs_network or options.get('wifi'), 'WIFI_REQUIRED',
                    'Enter a Wi-Fi network: the desktop and apps are downloaded at the first start')
        script = script_stamp(context, options['script'])[1] if options.get('script') else None
        if remote is None:
            path = logical_path(image_path)
            fd = source.regular(path)
            try:
                stamp = fs.stamp(os.fstat(fd))
                try:
                    info = image_import.describe(os.readlink('/proc/self/fd/%d' % fd))
                except image_import.ImageError as exc:
                    fail(exc.code, str(exc))
            finally:
                os.close(fd)
            name = path.rsplit('/', 1)[-1]
        else:
            path = None
            stamp = ('remote', remote.get('sha256') or remote.get('sha512') or remote['signature_sha256'], 0, remote['size'])
            info = {'format': remote.get('format', 'image'), 'compression': remote.get('compression'),
                    'factor': int(remote.get('factor', 6))}
            name = remote['file']
        if foreign:
            free = foreign_room(target_volume)
        else:
            vfs = os.fstatvfs(target_pin.fd)
            # Erased first, the whole partition is free but for the saved images; a download stays
            # until it is unpacked.
            free = vfs.f_blocks * vfs.f_frsize - store_bytes(live) if wipe else vfs.f_bavail * vfs.f_frsize
        if info.get('used_bytes'):
            # An ext image unpacks sparsely, then its files are copied out: twice what it holds.
            required = 2 * info['used_bytes'] + IMAGE_MARGIN + MIN_FREE
        else:
            required = max(stamp[3] * info['factor'], IMAGE_FACTOR_FLOOR) + MIN_FREE
        if remote is not None:
            required += remote['size']
        if COUNT_PACKAGES or (COUNT_PACKAGES is None and context.mode == 'device'):
            required += packages_bytes(options)
        require(free >= required, 'INSUFFICIENT_SPACE', 'There is not enough space beside the retained system',
                available_bytes=free, required_bytes=required)
        slot = image_slot(name, live) if not wipe else image_slot(name, types.SimpleNamespace(exists=lambda _: False))
        binding = {'volume_token': target_volume['volume_token'], 'state_seal': state['seal'] if state else None,
                   'source_volume_token': source_pin.volume['volume_token'] if source_pin else
                   (target_volume['volume_token'] if saved is not None and not wipe else None),
                   'image_identity': list(stamp), 'format': info, 'boot': boot, 'platform_profile_sha256': digest(profile),
                   'options': options, 'clean': clean, 'script': script, 'wipe': wipe,
                   'remote': {k: remote[k] for k in ('id', 'url', 'sha256', 'sha512', 'size', 'file', 'signature_url', 'signature_sha256', 'key')
                              if k in remote} if remote else None}
        return {'plan': plan_envelope(context, 'import', binding, source_volume=source_volume if remote is None else None,
                    image=True, manifest_path='/' + path if path else None, slot=slot, name=name[:96],
                    required_bytes=required, available_bytes=free, clean_install=clean, erase_userdata=wipe,
                    format_userdata=foreign, download_bytes=remote['size'] if remote else 0, write_boot=boot.get('write') is True,
                    confirmation='Format userdata for Linux, then install as the only system' if foreign else
                    'Erase userdata, then install as the only system' if wipe else
                    ('Install as the only system on the empty userdata' if clean else
                     'Install alongside the current system; nothing is switched or erased')),
                'switches_current_system': False, 'backup_recommended': not clean}


def system_roots(live):
    """The active system first, then every saved one."""
    roots = ['']
    if live.exists(MANAGER + '/slots'):
        roots += [MANAGER + '/slots/' + slot + '/root' for slot in live.list(MANAGER + '/slots')]
    return roots


def platform_source(live, state):
    """Device modules and firmware are copied from any installed Linux system; None when none has them."""
    for root in system_roots(live):
        if live.exists((root + '/' if root else '') + 'opt/a22x-conn/wmt_drv.ko'):
            return root
    return None


def platform_matches(tree, profile):
    """Whether a system still holds exactly the audited kernel modules, firmware and adapter files."""
    try:
        for item in profile['entries']:
            if item['kind'] == 'file':
                st = tree.lstat(item['path'])
                if not stat.S_ISREG(st.st_mode) or st.st_size != item['bytes'] or tree.hash(item['path']) != item['sha256']:
                    return False
        return True
    except (OSError, fs.FilesystemError):
        return False


def platform_bundle_path(context):
    return context.root / 'platform-files.tar.xz' if context.mode == 'fixture' else PLATFORM_BUNDLE


def optional_bundle_path(context):
    return context.root / 'platform-optional.tar.xz' if context.mode == 'fixture' else PLATFORM_OPTIONAL_BUNDLE


def unpack_bundle(path, target):
    """A platform archive of this recovery: plain files, directories and links, nothing outside."""
    target.mkdir(mode=0o700)
    with tarfile.open(path, 'r:xz') as archive:
        members = []
        for member in archive.getmembers():
            name = member.name.lstrip('./') if member.name not in ('.', './') else ''
            if not name:
                continue
            require(not name.startswith('/') and '..' not in name.split('/') and (member.isfile() or member.isdir() or member.issym()),
                    'PLATFORM_BUNDLE_INVALID', 'The platform files in this recovery are not a plain archive', path=name)
            member.name = name
            members.append(member)
        archive.extractall(target, members=members, numeric_owner=True, filter='fully_trusted')


def optional_platform(context, target):
    """The optional platform files kept in this recovery (sound profile, mixer state), unpacked
    as the fallback source: a clean install has no installed system to take them from
    (2026-11-08, a clean Fedora install had no sound)."""
    path = optional_bundle_path(context)
    if not path.is_file():
        return None
    unpack_bundle(path, target)
    return target


def bundled_platform(context, target, profile):
    """The audited platform files kept in this recovery, unpacked for the adapter.

    A restored older backup keeps the modules it was made with, which need not match
    the kernel on BOOT. Then these files are used instead, and only after every file
    the profile names has been compared with it.
    """
    path = platform_bundle_path(context)
    require(path.is_file(), 'PLATFORM_FILE_MISMATCH', 'Required kernel, firmware or device adapter file differs')
    unpack_bundle(path, target)
    for item in profile['entries']:
        if item['kind'] != 'file':
            continue
        file = target / item['path']
        inside = os.path.realpath(file).startswith(str(target.resolve()) + os.sep)
        ok = inside and file.is_file() and file.stat().st_size == item['bytes']
        if ok:
            digest_value = hashlib.sha256()
            with open(file, 'rb') as handle:
                for block in iter(lambda: handle.read(1 << 20), b''):
                    digest_value.update(block)
            ok = digest_value.hexdigest() == item['sha256']
        require(ok, 'PLATFORM_BUNDLE_INVALID', 'The platform files in this recovery do not match its profile', path=item['path'])
    return target


def wifi_source(live):
    """Cloud images carry neither saved networks nor Wi-Fi tools, so look through every system."""
    for root in system_roots(live):
        prefix = root + '/' if root else ''
        if live.exists(prefix + 'usr/bin/wpa_supplicant') and live.exists(prefix + 'etc/NetworkManager/system-connections') \
                and live.list(prefix + 'etc/NetworkManager/system-connections'):
            return root
    return None


def read_accounts(root):
    """Accounts of the shared /home with their password hashes."""
    hashes, wheel = {}, set()
    if (root / 'etc/shadow').is_file():
        for line in (root / 'etc/shadow').read_text(errors='replace').splitlines():
            parts = line.split(':')
            if len(parts) > 1:
                hashes[parts[0]] = parts[1]
    for line in (root / 'etc/group').read_text(errors='replace').splitlines():
        parts = line.split(':')
        if parts[0] in ('wheel', 'sudo') and len(parts) > 3:
            wheel.update(n for n in parts[3].split(',') if n)
    accounts = []
    for line in (root / 'etc/passwd').read_text(errors='replace').splitlines():
        parts = line.split(':')
        if len(parts) == 7 and parts[2].isdecimal() and 1000 <= int(parts[2]) < 60000:
            accounts.append({'name': parts[0], 'uid': int(parts[2]), 'gid': int(parts[3]), 'home': parts[5],
                             'shell': parts[6], 'admin': parts[0] in wheel})
    return {'accounts': accounts, 'shadow': {a['name']: hashes.get(a['name'], '!') for a in accounts}}


# Password hashes a C library reads by itself; yescrypt ($y$, $7$, $gy$) needs libxcrypt.
PORTABLE_HASHES = ('$1$', '$5$', '$6$', '$2a$', '$2b$', '$2y$')
LIBXCRYPT = ('usr/lib/libcrypt.so.1', 'usr/lib/libcrypt.so.2', 'usr/lib64/libcrypt.so.1', 'usr/lib64/libcrypt.so.2',
             'usr/lib/aarch64-linux-gnu/libcrypt.so.1', 'usr/lib/aarch64-linux-gnu/libcrypt.so.2',
             'lib/aarch64-linux-gnu/libcrypt.so.1')


def unreadable_passwords(root, meta, options):
    """2026-11-10: the shared account kept its yescrypt hash in postmarketOS, whose musl has no
    libxcrypt, and nobody could sign in. Stop before the system is saved, unless a password for
    that account is given again (the first start then stores it as SHA-512)."""
    if any(os.path.lexists(root / path) for path in LIBXCRYPT):
        return
    for item in meta.get('accounts', ()):
        secret = meta.get('shadow', {}).get(item['name'], '!')
        if secret[:1] in ('!', '*', '') or secret.startswith(PORTABLE_HASHES):
            continue
        if options.get('username') == item['name'] and (options.get('password') or options.get('password_hash')):
            continue
        fail('PASSWORD_FORMAT_UNSUPPORTED',
             'This system cannot read the saved password of this account. Choose New user with the same name and enter the password again.',
             account=item['name'])


def has_wifi_tool(root):
    return any(os.path.lexists(root / path) for path in WIFI_TOOLS)


def keyfile_text(value):
    """A NetworkManager keyfile string: backslashes doubled, a leading space kept."""
    value = value.replace('\\', '\\\\')
    return '\\s' + value[1:] if value.startswith(' ') else value


def nm_connection(entered):
    """A saved connection that NetworkManager joins by itself on every start."""
    import uuid
    ssid, password = entered['ssid'], entered['password']
    plain = ssid == ssid.strip() and not any(c in ssid for c in ';\\')
    lines = ['[connection]', 'id=' + keyfile_text(ssid), 'uuid=' + str(uuid.uuid4()), 'type=wifi', 'autoconnect=true', '',
             '[wifi]', 'mode=infrastructure',
             'ssid=' + (keyfile_text(ssid) if plain else ''.join('%d;' % byte for byte in ssid.encode())), '']
    if password:
        lines += ['[wifi-security]', 'key-mgmt=wpa-psk', 'psk=' + keyfile_text(password), '']
    lines += ['[ipv4]', 'method=auto', '', '[ipv6]', 'method=auto', '']
    return '\n'.join(lines)


def wpa_network(ssid, password):
    """A wpa_supplicant network block: the name in hex and the derived key, so nothing needs quoting."""
    raw = ssid.encode()
    if not password:
        return 'network={\n ssid=%s\n scan_ssid=1\n key_mgmt=NONE\n}\n' % raw.hex()
    key = password.lower() if re.fullmatch(r'[0-9A-Fa-f]{64}', password) else \
        hashlib.pbkdf2_hmac('sha1', password.encode(), raw, 4096, 32).hex()
    # scan_ssid: a hidden network is found too.
    return 'network={\n ssid=%s\n scan_ssid=1\n psk=%s\n}\n' % (raw.hex(), key)


def write_script(root, data):
    """The setup script chosen in recovery; the first start runs it once as root."""
    path = root / 'etc/a22x/setup-script'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    path.write_bytes(data)
    os.chown(path, 0, 0)
    path.chmod(0o700)


CHARGER_MODE = 'etc/a22x/charger-mode'


def charger_mode_default(root):
    """New systems start Linux when LK picks charge-only mode (2026-11-09): after a reset that loses
    the restart markers the phone otherwise waits in the charger screen for a Power hold.
    BOOT's /init reads this file; Settings in recovery turns the charger screen back on."""
    from a22x_adapter import write
    if not (root / CHARGER_MODE).exists():
        write(root, CHARGER_MODE, 'off\n', 0o644)


def charger_mode(context, value=None):
    """Read or set the charge-only boot of the active system on userdata."""
    require(value in (None, 'on', 'off'), 'INVALID_CHARGER_MODE', 'Use on or off')
    with fs.PinnedVolume(context, 'userdata', writing=value is not None) as pin:
        with pin.directory('/') as top:
            try:
                etc = os.open('etc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=top)
            except FileNotFoundError:
                fs.fail('NO_ACTIVE_SYSTEM', 'There is no installed system on userdata')
            try:
                pin.check_fd(etc)
                if value is not None:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir('a22x', 0o755, dir_fd=etc)
                try:
                    a22x = os.open('a22x', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=etc)
                except FileNotFoundError:
                    a22x = None
                try:
                    current = 'on'
                    if a22x is not None:
                        pin.check_fd(a22x)
                        try:
                            fd = os.open('charger-mode', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=a22x)
                            with os.fdopen(fd) as f:
                                current = 'off' if f.read(16).startswith('off') else 'on'
                        except FileNotFoundError:
                            pass
                    if value is not None and value != current:
                        temp = 'charger-mode.new'
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(temp, dir_fd=a22x)
                        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=a22x)
                        try:
                            os.fchmod(fd, 0o644)
                            os.write(fd, (value + '\n').encode())
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        os.replace(temp, 'charger-mode', src_dir_fd=a22x, dst_dir_fd=a22x)
                        os.fsync(a22x)
                        current = value
                finally:
                    if a22x is not None:
                        os.close(a22x)
            finally:
                os.close(etc)
    return {'charger_mode': current}


def copy_wifi(platform_tree, source_root, root, entered=None, helpers=None):
    """Networks for the first start and a program to join them. The network typed in recovery comes
    first; saved networks of an installed system follow. Returns whether both are in place."""
    from a22x_adapter import copy_entry, write
    entries = []
    if entered:
        name = re.sub(r'[^A-Za-z0-9_-]+', '-', entered['ssid']).strip('-')[:40] or 'wifi'
        write(root, 'etc/NetworkManager/system-connections/a22x-%s.nmconnection' % name, nm_connection(entered), 0o600)
        entries.append(wpa_network(entered['ssid'], entered['password']))
    networks = source_root / 'etc/NetworkManager/system-connections' if source_root is not None else None
    if networks is not None and networks.is_dir():
        for path in sorted(networks.glob('*.nmconnection')):
            copy_entry(path, root / 'etc/NetworkManager/system-connections' / path.name)
            values = dict(line.split('=', 1) for line in path.read_text(errors='replace').splitlines() if '=' in line)
            if values.get('ssid') and values.get('psk') and not (entered and values['ssid'] == entered['ssid']):
                entries.append('network={\n ssid="%s"\n psk="%s"\n}\n' % (values['ssid'], values['psk']))
    if not entries:
        return False
    write(root, 'etc/a22x/wpa.conf', ''.join(entries), 0o600)
    if has_wifi_tool(root):
        return True
    target = root / 'opt/a22x-conn/wifi'
    if platform_tree is not None and platform_tree.exists('usr/bin/wpa_supplicant'):
        target.mkdir(parents=True, exist_ok=True)
        files = list(elf_dependencies(platform_tree, 'usr/bin/wpa_supplicant')) + ['usr/bin/wpa_supplicant']
        for path in files:
            shutil.copyfile(source_root / path, target / path.rsplit('/', 1)[-1])
            (target / path.rsplit('/', 1)[-1]).chmod(0o755)
        library_names(source_root / 'usr/lib', target)
        return True
    bundled = helpers / 'wpa_supplicant' if helpers is not None else None
    if bundled is not None and bundled.is_file():
        # A static build kept in this recovery for images that carry no Wi-Fi program.
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled, target / 'wpa_supplicant')
        (target / 'wpa_supplicant').chmod(0o755)
        return True
    return False


def library_names(library_dir, target):
    """Programs ask for short names such as libssl.so.4; give the copied files those names too."""
    copied = {path.name for path in target.iterdir()}
    for _ in range(3):
        for entry in os.scandir(library_dir):
            if entry.is_symlink():
                name = os.path.basename(os.readlink(entry.path))
                if name in copied and entry.name not in copied:
                    os.symlink(name, target / entry.name)
                    copied.add(entry.name)


PROGRESS_EVENTS = False


def import_progress(plan):
    """Progress lines for the interface: the phase, the image being unpacked, bytes done of bytes total."""
    last = [None, 0.0]

    def report(phase, done=0, total=0):
        now = time.monotonic()
        if not PROGRESS_EVENTS or (phase == last[0] and now - last[1] < 0.5 and done < total):
            return
        last[:] = [phase, now]
        print(json.dumps({'event': 'progress', 'operation': 'import', 'phase': phase, 'partition': plan.get('name') or '',
                          'bytes_done': int(done), 'bytes_total': int(total)}, separators=(',', ':')), file=sys.stderr, flush=True)
    return report


# Packages, desktop, apps and the setup script of the first start are installed by the recovery
# itself when it is on Wi-Fi (2026-11-08): a22x-firstboot.sh runs in the new root (chroot), and
# the new system starts ready. Without Wi-Fi, or when that fails, the first start does it.
# Up to 150 minutes: on Arch Linux ARM Phrog and phoc's wlroots fix are built on the phone (2026-11-09).
PROVISION_TIMEOUT = 150 * 60
PROVISION_SCRIPT = '/opt/a22x-conn/a22x-firstboot.sh'
PROVISION_STEPS = (('start:', 0), ('installing the desktop', 1), ('installing apps', 2), ('building', 3),
                   ('running the setup script', 4), ('finished', 5))
PROVISION_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8',
                 'TERM': 'dumb', 'A22X_CHROOT': '1', 'DEBIAN_FRONTEND': 'noninteractive'}
# The devices of the chroot: no block device, so no package script can reach a partition.
PROVISION_DEVICES = (('null', 1, 3), ('zero', 1, 5), ('full', 1, 7), ('random', 1, 8), ('urandom', 1, 9), ('tty', 5, 0))


PROVISION_RUNNER = None  # Tests put a stand-in for the chroot here.


def recovery_online():
    try:
        return json.loads(WIFI_STATE.read_text()).get('connected') is True
    except (OSError, ValueError):
        return False


def inside(root, relative):
    """ROOT/RELATIVE as a real directory of ROOT: an image's symlink must not lead a mount
    or a write outside of it."""
    path = root / relative
    real = os.path.realpath(path)
    return path if real == os.path.join(os.path.realpath(root), relative) and os.path.isdir(real) else None


def provision_step(line, progress):
    if not line.startswith('a22x-firstboot: '):
        return
    text = line[len('a22x-firstboot: '):].strip()
    for marker, step in PROVISION_STEPS:
        if text.startswith(marker):
            progress('image-provision', step, len(PROVISION_STEPS) - 1)
            return


def chroot_processes(root):
    real = os.path.realpath(root)
    if real == '/':
        return  # Never the recovery itself.
    for pid in os.listdir('/proc'):
        if pid.isdigit() and int(pid) != os.getpid():
            try:
                if os.path.realpath('/proc/%s/root' % pid) == real:
                    yield int(pid)
            except OSError:
                continue


def chroot_script(root, on_line, timeout=PROVISION_TIMEOUT):
    """Run the first-start script in ROOT with proc, a read-only sys, a /dev without block
    devices, a fresh /run and the recovery's resolver. Everything is undone afterwards, also
    on failure or cancel. Returns the script's exit status (None: it could not start)."""
    mounts, restore, created = [], [], []

    def mount(*argv):
        subprocess.run(['mount', *argv], check=True, capture_output=True)
        mounts.append(argv[-1])
    try:
        places = {name: inside(root, name) for name in ('proc', 'sys', 'dev', 'run', 'etc')}
        missing = sorted(name for name, path in places.items() if path is None)
        if missing:
            # Said, not swallowed: a silent refusal looked like a setup never tried (2026-11-08).
            raise OSError('folders of the new system missing or linked: /' + ', /'.join(missing))
        # The recovery mounts userdata noexec,nosuid: the new root gets a mount of its own where
        # its programs run, for as long as this lasts (2026-11-08: the first real run stopped here).
        real_root = os.path.realpath(root)
        mount('--bind', real_root, real_root)
        subprocess.run(['mount', '-o', 'remount,bind,exec,suid,nodev', real_root], check=True, capture_output=True)
        mount('-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', str(places['proc']))
        mount('-t', 'sysfs', '-o', 'ro,nosuid,nodev,noexec', 'sysfs', str(places['sys']))
        dev = places['dev']
        mount('-t', 'tmpfs', '-o', 'mode=0755,nosuid,size=8m', 'a22x-dev', str(dev))
        for name, major, minor in PROVISION_DEVICES:
            try:
                os.mknod(dev / name, 0o666 | stat.S_IFCHR, os.makedev(major, minor))
                os.chmod(dev / name, 0o666)
            except PermissionError:
                # Without CAP_MKNOD (a user namespace): the recovery's own node, bound alone.
                (dev / name).touch(mode=0o666)
                mount('--bind', '/dev/' + name, str(dev / name))
        (dev / 'pts').mkdir()
        (dev / 'shm').mkdir()
        os.chmod(dev / 'shm', 0o1777)
        for name, target in (('ptmx', 'pts/ptmx'), ('fd', '/proc/self/fd'), ('stdin', '/proc/self/fd/0'),
                             ('stdout', '/proc/self/fd/1'), ('stderr', '/proc/self/fd/2')):
            os.symlink(target, dev / name)
        mount('-t', 'devpts', '-o', 'newinstance,ptmxmode=0666,mode=0620', 'devpts', str(dev / 'pts'))
        mount('-t', 'tmpfs', '-o', 'mode=0755,nosuid,nodev', 'a22x-run', str(places['run']))
        # The image's resolver (often a link into /run) gives way to the recovery's for the time.
        resolv = places['etc'] / 'resolv.conf'
        if resolv.is_symlink():
            restore.append(('link', resolv, os.readlink(resolv)))
            resolv.unlink()
        elif resolv.exists():
            restore.append(('file', resolv, resolv.read_bytes(), resolv.stat().st_mode & 0o7777))
            resolv.unlink()
        else:
            restore.append(('none', resolv))
        fd = os.open(resolv, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        # Explicit: under the recovery's umask 077 it was 0600, and apt, which downloads as the
        # user _apt, could resolve no name (2026-11-08, Debian: "Temporary failure resolving").
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'wb') as out:
            out.write(Path('/etc/resolv.conf').read_bytes())
        # Debian and Ubuntu start no daemon from a package while this file says so.
        sbin = inside(root, 'usr/sbin')
        if sbin is not None and not (sbin / 'policy-rc.d').exists() and not (sbin / 'policy-rc.d').is_symlink():
            with contextlib.suppress(OSError):
                fd = os.open(sbin / 'policy-rc.d', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o755)
                os.fchmod(fd, 0o755)
                created.append(sbin / 'policy-rc.d')
                with os.fdopen(fd, 'w') as out:
                    out.write('#!/bin/sh\n# Written by the A22x recovery while it installs packages here.\nexit 101\n')
        real = os.path.realpath(root)

        def enter():
            os.chroot(real)
            os.chdir('/')
            # The recovery's own umask (077) made files of the new system root-only: phoc could not
            # read /etc/phrog/phoc.ini and the login screen never came (2026-11-08, Fedora).
            os.umask(0o022)
        process = subprocess.Popen(['/bin/sh', PROVISION_SCRIPT], env=PROVISION_ENV, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, preexec_fn=enter,
                                   start_new_session=True)
        import threading
        timer = threading.Timer(timeout, lambda: os.killpg(process.pid, signal.SIGKILL))
        timer.start()
        try:
            for raw in process.stdout:
                on_line(raw.decode('utf-8', 'replace'))
            return process.wait()
        finally:
            timer.cancel()
    finally:
        # Daemons a package started anyway, and the script itself after a cancel.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            pids = list(chroot_processes(root))
            for pid in pids:
                with contextlib.suppress(OSError):
                    os.kill(pid, signum)
            if pids and signum == signal.SIGTERM:
                time.sleep(2)
        swap = os.path.realpath(root / 'var/a22x-swap')
        with contextlib.suppress(OSError):
            if any(line.split()[0] == swap for line in Path('/proc/swaps').read_text().splitlines()[1:] if line.split()):
                subprocess.run(['swapoff', swap], check=False, capture_output=True)
        for path in created:
            with contextlib.suppress(OSError):
                path.unlink()
        for item in restore:
            with contextlib.suppress(OSError):
                if item[1].exists() or item[1].is_symlink():
                    item[1].unlink()
                if item[0] == 'link':
                    os.symlink(item[2], item[1])
                elif item[0] == 'file':
                    fd = os.open(item[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, item[3])
                    with os.fdopen(fd, 'wb') as out:
                        out.write(item[2])
        for target in reversed(mounts):
            with contextlib.suppress(OSError):
                if subprocess.run(['umount', target], check=False, capture_output=True).returncode:
                    subprocess.run(['umount', '-l', target], check=False, capture_output=True)


def provision(context, root, progress, runner=None, network=True):
    """Install what the first start would, now, from the recovery. Returns whether the new
    system starts ready; otherwise its first start does the rest. NETWORK: the chosen desktop,
    apps or setup script need the internet, which the recovery has only on Wi-Fi."""
    conf = root / 'etc/a22x/firstboot.conf'
    if not conf.is_file():
        return {'provisioned': False, 'provision': 'nothing'}
    if runner is None:
        if context.mode == 'fixture':
            return {'provisioned': False, 'provision': 'first-start'}
        if network and not recovery_online():
            return {'provisioned': False, 'provision': 'first-start', 'provision_error': 'Recovery was not connected to Wi-Fi.'}
        runner = chroot_script
    progress('image-provision', 0, len(PROVISION_STEPS) - 1)
    # The new root is a folder the recovery made (0700) and the archive's own "./" entry is not
    # applied: every user but root was denied "/". pacman downloads as its user alpm, which then
    # could not open its download folder ("Permission denied", 2026-11-09, Arch Linux ARM); apt
    # had quietly fallen back to unsandboxed downloads. A root is 0755; the folders above it stay
    # private.
    os.chmod(root, 0o755)
    # Everything the setup prints, kept in the recovery's RAM: the new root may lack a /var/log.
    keep = context.root / 'provision.log' if context.mode == 'fixture' else Path('/run/a22x-recovery/provision.log')
    try:
        fd = os.open(keep, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        kept = os.fdopen(fd, 'w', buffering=1)
    except OSError:
        kept = None

    def on_line(line):
        if kept is not None:
            with contextlib.suppress(OSError):
                kept.write(line if line.endswith('\n') else line + '\n')
        provision_step(line, progress)
    try:
        status = runner(root, on_line)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        # The recovery could not prepare the chroot: the first start still does everything.
        return {'provisioned': False, 'provision': 'first-start', 'provision_status': None, 'provision_error': str(error)[:200]}
    finally:
        if kept is not None:
            kept.close()
    done = status == 0 and not conf.exists()
    found = None if done else setup_failure(root)
    return {'provisioned': done, 'provision': 'recovery' if done else 'first-start', 'provision_status': status,
            **({'provision_error': found} if found else {})}


# Why a setup in recovery stopped, from the new system's own log (2026-11-10: the page said "A
# package download failed" where apt had found no room: "You don't have enough free space").
SETUP_FAILURES = (
    (("You don't have enough free space", 'No space left on device', 'not enough free space'),
     'There was not enough free space on userdata for the packages.'),
    (('Temporary failure resolving', 'Could not resolve', 'Failed to resolve', 'Network is unreachable'),
     'The package servers could not be reached.'),
    (('Hash Sum mismatch', 'checksum mismatch', 'signature is invalid', 'NO_PUBKEY'),
     'A package download did not pass its check.'),
)


def setup_failure(root):
    """The reason of a failed setup that the first-start log names, or None."""
    path = Path(root) / 'var/log/a22x-firstboot.log'
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - (256 << 10)))
            text = f.read().decode('utf-8', 'replace')
    except OSError:
        return None
    for needles, message in SETUP_FAILURES:
        if any(needle in text for needle in needles):
            return message
    return None


# Builds the phone made for a system (phoc's wlroots, Phrog: up to 40 minutes, 2026-11-09). Its
# first start keeps them in var/cache/a22x-build with the versions they were made for; recovery
# carries them over an erased userdata on its own cache partition, and a pacman system gets them,
# using one only when the versions match.
BUILD_CACHE = 'var/cache/a22x-build'
KEPT_BUILDS = {'phoc': ('opt/a22x-phoc',),
               'phrog': ('usr/local/bin/phrog', 'usr/local/lib/a22x-phrog', 'usr/local/share/glib-2.0/schemas',
                         'usr/local/share/gnome-session/sessions', 'usr/local/share/applications',
                         'usr/local/lib/systemd/user')}
MAX_KEPT_BUILD = 160 << 20


def kept_build_valid(path, kind):
    """Only plain files and folders under the build's own paths: the first start extracts it at /."""
    try:
        if path.is_symlink() or path.stat().st_size > MAX_KEPT_BUILD:
            return False
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                name = member.name
                if (name.startswith('/') or '..' in name.split('/') or not (member.isfile() or member.isdir())
                        or not any(name == prefix or name.startswith(prefix + '/') for prefix in KEPT_BUILDS[kind])):
                    return False
        return True
    except (OSError, tarfile.TarError):
        return False


@contextlib.contextmanager
def build_store(context):
    if context.mode == 'fixture':
        store = context.root / 'build-cache'
        store.mkdir(exist_ok=True)
        yield store
        return
    volume = context.volume('cache')
    mounted_here = not volume['mounted']
    if mounted_here:
        context.mount('cache', 'rw', True, volume['volume_token'])
    elif volume['read_only']:
        raise OSError('The cache partition is read-only')
    try:
        store = Path(context.volume('cache')['mountpoint']) / 'a22x-recovery/build-cache'
        store.mkdir(parents=True, exist_ok=True)
        yield store
    finally:
        if mounted_here:
            context.unmount('cache', context.volume('cache')['volume_token'])


def keep_builds(context, root):
    """Before userdata is erased: the old system's builds go to the recovery's store."""
    source, kept = Path(root) / BUILD_CACHE, []
    kinds = [kind for kind in KEPT_BUILDS if (source / (kind + '.tar')).is_file() and (source / (kind + '.key')).is_file()]
    if not kinds:
        return kept
    with contextlib.suppress(Exception):  # a cache never stops an installation
        with build_store(context) as store:
            for kind in kinds:
                tar, key = source / (kind + '.tar'), source / (kind + '.key')
                if key.is_symlink() or not kept_build_valid(tar, kind):
                    continue
                shutil.copyfile(tar, store / (kind + '.tar.new'))
                os.replace(store / (kind + '.tar.new'), store / (kind + '.tar'))
                (store / (kind + '.key')).write_text(key.read_text(errors='replace')[:200])
                kept.append(kind)
    return kept


def give_builds(context, root):
    """A pacman system gets the kept builds; its first start uses one only for the same versions."""
    root, given = Path(root), []
    if not (root / 'usr/bin/pacman').exists():
        return given
    with contextlib.suppress(Exception):
        with build_store(context) as store:
            for kind in KEPT_BUILDS:
                tar, key = store / (kind + '.tar'), store / (kind + '.key')
                if not key.is_file() or not kept_build_valid(tar, kind):
                    continue
                target = root / BUILD_CACHE
                target.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tar, target / (kind + '.tar'))
                shutil.copyfile(key, target / (kind + '.key'))
                given.append(kind)
    return given


def erase_userdata(context):
    """The clean install of the installer page: userdata is formatted ext4 when the confirmed
    installation starts, not before (2026-11-08)."""
    if context.mode == 'fixture' and not foreign_userdata(context):
        return  # The fixture empties its userdata folder once it is pinned.
    volume = context.volume('userdata')
    if volume['mounted']:
        context.unmount('userdata', volume['volume_token'])
    context.format('userdata', 'ext4', True, context.volume('userdata')['volume_token'])
    context.mount('userdata', 'rw', True, context.volume('userdata')['volume_token'])


def install_image(context, plan, confirmed=False):
    """A system from an image. When its plan takes a Linux BOOT with it (boot_for_linux), that BOOT is
    on cache before anything is erased and is written once the system is in place."""
    boot = plan['binding'].get('boot') or {}
    if not boot.get('write'):
        return install_image_system(context, plan, confirmed)
    context.confirm(confirmed)
    progress = import_progress(plan)
    verify_plan(context, plan, 'import')
    path = linux_boot_fetched(context, boot, progress)
    result = install_image_system(context, plan, confirmed)
    progress('image-boot', 0, 1)
    written = linux_boot_written(context, path, boot, progress)
    progress('image-boot', 1, 1)
    return {**result, 'boot_written': written['sha256'], 'kept_boot': written.get('kept_boot')}


def install_image_system(context, plan, confirmed=False):
    context.confirm(confirmed)
    progress = import_progress(plan)
    verify_plan(context, plan, 'import')
    import image_import
    from a22x_adapter import AdaptError, adapt, adopt_first_account
    options = plan['binding']['options']
    wipe, remote = plan['binding'].get('wipe') is True, plan['binding'].get('remote')
    entry = catalog_entry(context, remote['id']) if remote else None
    fresh = image_plan(context, plan['source_volume'], plan['manifest_path'], options,
                       plan['binding']['source_volume_token'], wipe=wipe, remote=entry)['plan']
    require(fresh['binding'] == plan['binding'], 'PLAN_STALE', 'The image or installation state changed since planning')
    profile = load_profile(context)
    clean = plan['binding'].get('clean') is True
    saved = store_name(plan.get('manifest_path')) if remote is None and plan.get('source_volume') == 'userdata' else None
    predownload = None
    if wipe and remote is not None:
        # Downloaded before anything is erased (2026-11-10): when the network drops, the installed
        # systems are still there. Without room for it, the partition is formatted first, as before.
        predownload = download_before_erase(context, remote, progress)
    # Formatted only when nothing saved would be lost; otherwise the store stays and the rest goes.
    keep_store = saved is not None or predownload is not None or (wipe and store_in_use(context))
    if wipe:
        progress('image-erase', 0, 1)
        old = context.volume('userdata')
        if old.get('mounted') and old.get('mountpoint'):
            keep_builds(context, old['mountpoint'])
        if not keep_store:
            erase_userdata(context)
        elif context.mode != 'fixture':  # the fixture empties its folder once it is pinned
            with fs.PinnedVolume(context, 'userdata', writing=True) as erase_pin, Tree(erase_pin) as erase_live:
                require_userdata(erase_pin)
                clear_userdata(erase_live)
        progress('image-erase', 1, 1)
    script = None
    if options.get('script'):
        script, seen = script_stamp(context, options['script'])
        require(seen == plan['binding']['script'], 'SCRIPT_CHANGED', 'The setup script changed since planning')
    helpers = Path(os.environ.get('A22X_PLATFORM_HELPERS', '/opt/a22x-recovery/platform'))
    with contextlib.ExitStack() as stack:
        # A formatted userdata is a new filesystem: its token is the one it has now.
        pin = stack.enter_context(fs.PinnedVolume(context, 'userdata', None if wipe else plan['binding']['volume_token'], writing=True))
        live = stack.enter_context(Tree(pin))
        source = None
        if saved is not None:
            source = live
        elif remote is None:
            source_pin = stack.enter_context(fs.PinnedVolume(context, plan['source_volume'], plan['binding']['source_volume_token']))
            source = stack.enter_context(Tree(source_pin))
        require_userdata(pin)
        if wipe and context.mode == 'fixture':
            for entry_name in os.listdir(real_path(live)):
                if entry_name not in (('lost+found', STORE_DIR) if keep_store else ('lost+found',)):
                    target = real_path(live) / entry_name
                    shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
        if clean:
            # Built in a setup directory; the manager record appears only once the system is in place.
            require(not live.exists(MANAGER) and userdata_empty(live), 'USERDATA_NOT_EMPTY', 'Userdata changed since planning')
            state, setup = None, '.a22x-systems-setup-' + os.urandom(8).hex()
        else:
            state, setup = initialize(live, profile), None
            require(not read_transaction(live), 'SWITCH_PENDING', 'Repair the pending switch before importing')
        slot = plan['slot']
        stage_id = slot + '-' + os.urandom(5).hex()
        stage = (setup or MANAGER) + '/staging/' + stage_id
        live.mkdirs(stage + '/root')
        live.mkdirs(stage + '/work')
        live.write_json(stage + '/import.json', seal({'schema': 'a22x.distro.import.v1', 'id': stage_id, 'slot': slot,
                        'plan_seal': plan['seal'], 'status': 'extracting'}), create=True)
        stage_path = real_path(live) / stage
        discard = real_path(live) / setup if clean else stage_path
        root = stage_path / 'root'
        try:
            if remote is not None and predownload is not None:
                fd = os.open(real_path(live) / STORE_DIR / predownload, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            elif remote is not None:
                # Downloaded beside the setup on userdata, checked against the catalog, then unpacked.
                live.mkdirs(DOWNLOAD_DIR)
                download = real_path(live) / DOWNLOAD_DIR / remote['file']
                try:
                    ensure_clock(context)
                    signed = {'url': remote['signature_url'], 'sha256': remote['signature_sha256'], 'key': remote['key']} \
                        if 'signature_url' in remote else None
                    fetch(context, remote['url'], download, remote['size'], remote.get('sha256') or remote.get('sha512') or '',
                          progress, signature=signed)
                except BaseException as error:
                    shutil.rmtree(real_path(live) / DOWNLOAD_DIR, ignore_errors=True)
                    downloaded_differently(context, remote, error)
                    raise
                fd = os.open(download, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            else:
                fd = source.regular(logical_path(plan['manifest_path']))
            try:
                if remote is None:
                    require(list(fs.stamp(os.fstat(fd))) == plan['binding']['image_identity'], 'IMAGE_CHANGED', 'The image changed')
                progress('image-unpack', 0, os.fstat(fd).st_size)
                image_import.extract(os.readlink('/proc/self/fd/%d' % fd), stage_path / 'work', root, progress)
            finally:
                os.close(fd)
                if predownload is not None:
                    with contextlib.suppress(FileNotFoundError):
                        (real_path(live) / STORE_DIR / predownload).unlink()
                elif remote is not None:
                    shutil.rmtree(real_path(live) / DOWNLOAD_DIR, ignore_errors=True)
            desktop = options['desktop']
            networks = None if clean else wifi_source(live)
            if desktop not in ('console', 'image') or options.get('apps') or options.get('packages'):
                require(has_wifi_tool(root) or (helpers / 'wpa_supplicant').is_file() or networks is not None,
                        'NO_WIFI_TOOLS', 'This image has no Wi-Fi program, so the desktop and apps cannot be '
                        'downloaded at the first start. Choose Console without apps, or another image.')
            current = False
            if clean:
                # Nothing to share: the new system gets the account typed in recovery.
                base, source_root, meta = None, None, {'accounts': [], 'shadow': {}}
            else:
                base = platform_source(live, state)
                source_root = real_path(live) / base if base else real_path(live)
                meta = read_accounts(source_root)
                # Accounts come from the running system; kernel modules and firmware only when they still match.
                if base is not None:
                    with Tree(pin, base) if base else contextlib.nullcontext(live) as platform_tree:
                        current = platform_matches(platform_tree, profile)
            progress('image-adapt')
            platform_root = source_root if current else bundled_platform(context, stage_path / 'platform', profile)
            paths = [e['path'] for e in profile['entries'] if e['kind'] != 'directory' and e['path'] != 'lib']
            paths += ['opt/a22x-conn/' + p.name for p in (platform_root / 'opt/a22x-conn').glob('*.ko')]
            paths = sorted(set(paths))
            firstboot = None
            if desktop != 'console' or options['account'] == 'new' or options.get('apps') or script is not None \
                    or options.get('wifi') or options.get('packages'):
                entered = options.get('wifi') or {}
                language, offset, zone = recovery_locale(context)
                # The phone's user: the typed account, else the first shared one. An added system
                # had none, so its setup script saw no A22X_USER and automatic sign-in only found
                # the account because it was called "user" (2026-11-10).
                shared = sorted((a for a in meta['accounts'] if str(a.get('home', '')).startswith('/home/')), key=lambda a: a['uid'])
                phone_user = options['username'] or (shared[0]['name'] if shared else '')
                lines = {'DESKTOP': desktop, 'EXTRA_SHELLS': ' '.join(options.get('shells') or ()),
                         'GREETER': options.get('greeter') or 'auto',
                         'A22X_LANGUAGE': language, 'A22X_UTC_OFFSET': str(offset),
                         'A22X_LOCALE': options.get('locale') or '', 'A22X_TIMEZONE': options.get('timezone') or zone,
                         'NEW_USER': options['username'] or '', 'NEW_PASSWORD': options['password'] or '',
                         'NEW_PASSWORD_HASH': options.get('password_hash') or '',
                         'PHONE_USER': phone_user,
                         'APPS': ' '.join(options.get('apps') or ()), 'PRIMARY_USER': '1' if clean else '',
                         'EXTRA_PACKAGES': ' '.join(options.get('packages') or ()),
                         'WIFI_SSID': entered.get('ssid', ''), 'WIFI_PASSWORD': entered.get('password', '')}
                firstboot = ''.join('%s=%s\n' % (k, shlex.quote(v)) for k, v in lines.items())
            args = types.SimpleNamespace(
                id=slot, platform=str(platform_root), platform_paths=paths + list(PLATFORM_EXTRAS),
                platform_optional=PLATFORM_OPTIONAL, helpers=helpers,
                platform_fallback=optional_platform(context, stage_path / 'platform-optional'),
                keep_firmware=False, console=desktop in ('console', 'phosh', 'gnome', 'plasma', 'xfce'),
                allow_no_ssh=True, trial_seconds=options['trial_seconds'], authorized_key=options['authorized_key'],
                firstboot=firstboot)
            if clean and (root / 'home').is_dir() and not (root / 'home').is_symlink():
                # The adapter empties /home, which is shared beside other systems. A clean install
                # keeps the image's own: it holds the home of its first account.
                (root / 'home').rename(stage_path / 'image-home')
            identity = adapt(root, args, meta)
            renamed = None
            if clean:
                if (stage_path / 'image-home').is_dir():
                    (root / 'home').rmdir()
                    (stage_path / 'image-home').rename(root / 'home')
                renamed = adopt_first_account(root, options['username'])
            if not clean:
                unreadable_passwords(root, meta, options)
            if script is not None:
                write_script(root, script)
            if networks is not None:
                with Tree(pin, networks) if networks else contextlib.nullcontext(live) as wifi_tree:
                    wifi = copy_wifi(wifi_tree, real_path(live) / networks if networks else real_path(live), root,
                                     options.get('wifi'), helpers)
            else:
                wifi = copy_wifi(None, None, root, options.get('wifi'), helpers)
            charger_mode_default(root)
            give_builds(context, root)
            shutil.rmtree(stage_path / 'work')
            shutil.rmtree(stage_path / 'platform', ignore_errors=True)
            provisioned = provision(context, root, progress, PROVISION_RUNNER,
                                    desktop not in ('console', 'image') or bool(options.get('apps')) or bool(options.get('packages'))
                                    or script is not None)
            subprocess.run(['sync'], check=False)
            progress('image-check')
            with Tree(pin, stage + '/root') as target:
                readiness = validate_root(target, profile, pin.volume.get('filesystem_uuid'))
                home = {'accounts': []} if clean else home_binding(live, target)
            progress('image-finish')
            name = identity.get('PRETTY_NAME', slot)[:96] or slot
            record = seal({'schema': 'a22x.distro.slot.v1', 'id': slot, 'name': name,
                           'origin': 'image-' + plan['binding']['format']['format'], 'created_utc': int(time.time()),
                           'platform_profile_sha256': digest(profile), 'validation': readiness, 'clean_install': clean,
                           'shared_home_accounts': home['accounts'], 'hardware_boot_tested': False})
            if clean:
                activate_clean(live, setup, stage, slot, record, profile)
        except (image_import.ImageError, AdaptError) as exc:
            shutil.rmtree(discard, ignore_errors=True)
            fail(getattr(exc, 'code', 'IMAGE_NOT_INSTALLABLE'), str(exc))
        except BaseException:
            # Includes Cancel from the UI: SIGTERM arrives here as SystemExit.
            shutil.rmtree(discard, ignore_errors=True)
            raise
        result = {'installed_slot': slot, 'name': name, 'desktop': desktop, 'apps': options.get('apps') or [],
                  'shells': options.get('shells') or [], 'greeter': options.get('greeter') or 'auto',
                  'wifi_for_first_boot': wifi, 'setup_script': script is not None, 'validation': readiness, **provisioned}
        if clean:
            return {**result, 'active': slot, 'switched': True, 'clean_install': True, 'home_preserved': False,
                    'renamed_account': renamed}
        live.write_json(stage + '/slot.json', record, create=True)
        live.write_json(stage + '/import.json', seal({'schema': 'a22x.distro.import.v1', 'id': stage_id, 'slot': slot,
                        'plan_seal': plan['seal'], 'status': 'complete'}))
        live.rename(stage, MANAGER + '/slots/' + slot)
        state.pop('seal')
        state['generation'] += 1
        live.write_json(MANAGER + '/state.json', seal(state))
        return {**result, 'active': state['active'], 'switched': False, 'clean_install': False, 'home_preserved': True}


def activate_clean(live, setup, stage, slot, record, profile):
    """Move the checked root into place on the empty userdata. /sbin moves last: until then there is
    no init to start, so an interruption cannot boot half a system. A failure moves everything back."""
    source = stage + '/root'
    names = [name for name in live.list(source) if name != 'lost+found']
    require('sbin' in names, 'ROOT_INIT_GUARD_MISSING', '/sbin is required as the boot commit guard')
    for name in names:
        path_parts(name)
        require(not live.exists(name), 'USERDATA_NOT_EMPTY', 'Userdata changed during installation', path=name)
    moved = []
    try:
        for name in sorted(set(names) - {'sbin'}) + ['sbin']:
            live.rename(source + '/' + name, name)
            moved.append(name)
    except BaseException:
        for name in reversed(moved):
            with contextlib.suppress(OSError, fs.FilesystemError, DistroError):
                live.rename(name, source + '/' + name)
        raise
    live.mkdirs(setup + '/slots/' + slot + '/root', mode=0o755)
    live.mkdirs(setup + '/history')
    live.write_json(setup + '/slots/' + slot + '/slot.json', record, create=True)
    live.write_json(setup + '/state.json', seal({'schema': 'a22x.distro.state.v1', 'active': slot, 'generation': 0,
                    'volume': state_volume(live.pin), 'platform_profile_sha256': digest(profile)}), create=True)
    shutil.rmtree(real_path(live) / stage)
    live.rename(setup, MANAGER)
    require(live.resolve('sbin/init'), 'INIT_MISSING', 'The installed root has no init')


def remove_slot(context, slot, confirmed=False):
    """Delete a saved system that is not running. /home and the active system are not touched."""
    context.confirm(confirmed)
    identifier(slot)
    require(slot != 'original', 'RESERVED_SLOT', 'The original system is never removed here')
    profile = load_profile(context)
    with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
        require_userdata(pin)
        state = read_state(live)
        require(not read_transaction(live), 'SWITCH_PENDING', 'Repair the pending switch first')
        require(state['active'] != slot, 'SLOT_ACTIVE', 'Switch to another system before removing this one')
        path = MANAGER + '/slots/' + slot
        require(live.exists(path), 'SLOT_UNAVAILABLE', 'The selected system does not exist')
        shutil.rmtree(real_path(live) / path)
        os.sync()
        state.pop('seal')
        state['generation'] += 1
        live.write_json(MANAGER + '/state.json', seal(state))
        return {'removed_slot': slot, 'active': state['active'], 'platform_profile_sha256': digest(profile)}


def top_entries(tree, *, live=False):
    names = tree.list()
    require(len(names) <= 128, 'TOO_MANY_ROOT_ENTRIES', 'The root has too many top-level entries')
    result = {}
    for name in names:
        path_parts(name)
        if name in RESERVED:
            continue
        if re.fullmatch(r'\.a22x-systems-setup-[0-9a-f]{16}', name):
            # An interrupted first-time setup never moved the live OS. Preserve
            # its directory in place and expose it in status; do not guess that
            # arbitrary contents are ours to delete or move into an OS slot.
            continue
        require(not name.startswith('.a22x-systems-'), 'INCOMPLETE_MANAGER_SETUP',
                'An unfinished manager setup requires review before switching', path=name)
        st = tree.lstat(name)
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode),
                'ROOT_SPECIAL_FILE', 'A root-level special file prevents a safe switch', path=name)
        result[name] = tree.record(name)
    require('sbin' in result, 'ROOT_INIT_GUARD_MISSING', '/sbin is required as the boot commit guard')
    return result


def switch_binding(context, live, slot, profile):
    state = read_state(live)
    require(not read_transaction(live), 'SWITCH_PENDING', 'Resume or roll back the pending transaction first')
    require(slot != state['active'], 'ALREADY_ACTIVE', 'This system is already active')
    available = {item['id']: item for item in slots(live, state)}
    require(slot in available, 'SLOT_UNAVAILABLE', 'The selected installed system is unavailable')
    meta = live.json(MANAGER + '/slots/' + slot + '/slot.json', sealed=True)
    require(meta['platform_profile_sha256'] == digest(profile), 'PLATFORM_PROFILE_MISMATCH',
            'This system was set up for another kernel and firmware. Install it again, or remove it')
    with Tree(live.pin, MANAGER + '/slots/' + slot + '/root') as target, \
            Tree(live.pin, MANAGER + '/slots/' + state['active'] + '/root') as current:
        require(set(current.list()) <= {'home'}, 'ACTIVE_SLOT_NOT_EMPTY',
                'The active slot’s storage directory contains unexpected files')
        # Systems made here carry the exact platform files; an adopted one keeps the files it booted with.
        root_check = validate_root(target, profile, live.pin.volume.get('filesystem_uuid'),
                                   platform=meta.get('origin') != 'adopted-existing-root', shared_home=True)
        old_check = validate_root(live, profile, live.pin.volume.get('filesystem_uuid'), platform=False)
        shared = home_binding(live, target)
        binding = {'volume_token': live.pin.volume['volume_token'], 'state_seal': state['seal'],
                   'target_slot_seal': meta['seal'], 'active': state['active'], 'target': slot,
                   'live_entries': top_entries(live, live=True), 'target_entries': top_entries(target),
                   'home': shared, 'target_validation': root_check, 'current_validation': old_check,
                   'boot': boot_identity(context), 'platform_profile_sha256': digest(profile)}
        return binding


def switch_plan(context, slot):
    identifier(slot)
    profile = load_profile(context)
    with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
        require_userdata(pin)
        binding = switch_binding(context, live, slot, profile)
        return {'plan': plan_envelope(context, 'switch', binding, slot=slot, name=slot,
                    preserved_paths=['/home'], whole_switch_atomic=False,
                    confirmation='Switch the root filesystem and retain the previous system for rollback',
                    interruption_action='Return to recovery → Systems → Resume or Rollback; never format userdata'),
                'retained_boot': True, 'writes_partition_table': False}


def _write_journal(live, journal):
    value = dict(journal)
    value.pop('seal', None)
    journal = seal(value)
    live.write_json(MANAGER + '/transaction.json', journal)
    set_sentinel(live.pin.context, journal)
    return journal


def _at_location(live, path, expected):
    try:
        value = live.record(path)
    except FileNotFoundError:
        return False
    require(value == expected, 'TRANSACTION_PATH_CHANGED',
            'A switch path now names an unexpected object; nothing will be overwritten', path=path)
    return True


def _move_step(live, step, reverse=False):
    source, target = (step['to'], step['from']) if reverse else (step['from'], step['to'])
    a, b = _at_location(live, source, step['identity']), _at_location(live, target, step['identity'])
    require(a != b, 'TRANSACTION_LOCATION_AMBIGUOUS',
            'A planned object is missing or appears at both locations', source=source, target=target)
    if a:
        live.rename(source, target)
    require(_at_location(live, target, step['identity']), 'TRANSACTION_MOVE_FAILED', 'Rename readback failed')


def run_transaction(context, live, journal, direction, checkpoint=None):
    require(direction in ('resume', 'rollback'), 'INVALID_DIRECTION', 'Select resume or rollback')
    validate_transaction(journal)
    require(journal['volume'] == state_volume(live.pin), 'TRANSACTION_IDENTITY_MISMATCH', 'Wrong filesystem')
    require(read_state(live)['seal'] in (journal['before_state']['seal'], journal['after_state']['seal']),
            'STATE_CHANGED', 'The active-system record changed outside this transaction')
    for path, expected in journal['containers'].items():
        require(live.record(path) == expected, 'TRANSACTION_CONTAINER_CHANGED',
                'A slot directory was replaced after the transaction began', path=path)
    # Reconcile the previously selected direction before changing direction.
    # Only one rename can be ahead of its durable checkpoint.
    if journal.get('direction') == 'rollback' and journal['next']:
        step = journal['steps'][journal['next'] - 1]
        source = _at_location(live, step['from'], step['identity'])
        target = _at_location(live, step['to'], step['identity'])
        if source and not target:
            journal['next'] -= 1
            journal = _write_journal(live, journal)
    elif journal['next'] < len(journal['steps']):
        step = journal['steps'][journal['next']]
        source = _at_location(live, step['from'], step['identity'])
        target = _at_location(live, step['to'], step['identity'])
        if not source and target:
            journal['next'] += 1
            journal = _write_journal(live, journal)
    journal['direction'] = direction
    journal = _write_journal(live, journal)
    if direction == 'resume':
        while journal['next'] < len(journal['steps']):
            index = journal['next']
            if checkpoint:
                checkpoint('before-rename', index)
            _move_step(live, journal['steps'][index])
            if checkpoint:
                checkpoint('after-rename', index)
            journal['next'] += 1
            journal = _write_journal(live, journal)
            if checkpoint:
                checkpoint('after-checkpoint', index)
        final_state = journal['after_state']
    else:
        while journal['next']:
            index = journal['next'] - 1
            if checkpoint:
                checkpoint('before-rollback', index)
            _move_step(live, journal['steps'][index], reverse=True)
            if checkpoint:
                checkpoint('after-rollback', index)
            journal['next'] -= 1
            journal = _write_journal(live, journal)
        final_state = journal['before_state']
    with Tree(live.pin, MANAGER + '/slots/' + final_state['active'] + '/root') as active_storage:
        require(set(active_storage.list()) <= {'home'}, 'TRANSACTION_NOT_COMPLETE', 'The active root is still split')
    require(live.record('home') == journal['home_identity'], 'HOME_CHANGED', 'Shared /home identity changed')
    require(live.resolve('sbin/init'), 'INIT_MISSING', 'The completed root has no init')
    if checkpoint:
        checkpoint('before-state-commit', journal['next'])
    live.write_json(MANAGER + '/state.json', final_state)
    if checkpoint:
        checkpoint('after-state-commit', journal['next'])
    journal['status'] = 'committed' if direction == 'resume' else 'rolled-back'
    journal = _write_journal(live, journal)
    live.rename(MANAGER + '/transaction.json', MANAGER + '/history/' + journal['id'] + '.json')
    if checkpoint:
        checkpoint('after-history-commit', journal['next'])
    clear_sentinel(context, journal['id'])
    return {'transaction_id': journal['id'], 'active': final_state['active'], 'status': journal['status'],
            'home_preserved': True, 'retained_boot': True, 'reboot_blocked': False}


def switch(context, plan, confirmed=False, checkpoint=None):
    context.confirm(confirmed)
    verify_plan(context, plan, 'switch')
    profile = load_profile(context)
    with fs.PinnedVolume(context, 'userdata', plan['binding']['volume_token'], writing=True) as pin, Tree(pin) as live:
        require_userdata(pin)
        binding = switch_binding(context, live, identifier(plan['slot']), profile)
        require(binding == plan['binding'], 'PLAN_STALE', 'Root, home, platform or slot changed since planning')
        before = read_state(live)
        after = dict(before)
        after.pop('seal')
        after.update(active=plan['slot'], generation=before['generation'] + 1)
        old = MANAGER + '/slots/' + before['active'] + '/root/'
        new = MANAGER + '/slots/' + plan['slot'] + '/root/'
        steps = []
        for name in ['sbin'] + sorted(set(binding['live_entries']) - {'sbin'}):
            steps.append({'from': name, 'to': old + name, 'identity': binding['live_entries'][name]})
        for name in sorted(set(binding['target_entries']) - {'sbin'}) + ['sbin']:
            steps.append({'from': new + name, 'to': name, 'identity': binding['target_entries'][name]})
        journal = {'schema': 'a22x.distro.transaction.v1', 'id': 'switch-' + os.urandom(12).hex(),
                   'volume': state_volume(pin), 'source': before['active'], 'target': plan['slot'],
                   'before_state': before, 'after_state': seal(after), 'steps': steps, 'next': 0,
                   'direction': 'resume', 'status': 'pending', 'home_identity': live.record('home'),
                   'containers': {path: live.record(path) for path in
                       (MANAGER, MANAGER + '/slots', old.rstrip('/'), new.rstrip('/'))},
                   'plan_seal': plan['seal'], 'boot_sha256': binding['boot']['boot_sha256']}
        journal = seal(journal)
        # This durable record precedes every root rename; no foreign destination is overwritten.
        live.write_json(MANAGER + '/transaction.json', journal, create=True)
        set_sentinel(context, journal)
        return run_transaction(context, live, journal, 'resume', checkpoint)


def recover(context, direction, expected_transaction, confirmed=False, checkpoint=None):
    context.confirm(confirmed)
    identifier(expected_transaction)
    boot_identity(context)
    with fs.PinnedVolume(context, 'userdata', writing=True) as pin, Tree(pin) as live:
        require_userdata(pin)
        journal = read_transaction(live)
        require(journal and journal['id'] == expected_transaction, 'TRANSACTION_CHANGED',
                'Refresh the exact interrupted transaction before repairing it')
        return run_transaction(context, live, journal, direction, checkpoint)


def android_boot_installed():
    """True when BOOT holds Android's own BOOT for this phone (stock, or a custom Android kernel):
    a normal start then starts Android, and there is no Linux system on userdata to check."""
    import bootimage
    try:
        with open('/dev/disk/by-partlabel/boot', 'rb') as f:
            data = f.read(64 << 20)
        info = bootimage.inspect_boot(data)
    except (OSError, ValueError):
        return False
    return bool(info['device'].get('this_phone')) and not info.get('mainline')


def boot_check(context):
    """Explicit Normal Boot preflight; never an automatic cold-start mount."""
    # 2026-11-10: with Android installed (Samsung's firmware or a GSI), userdata is Android's
    # encrypted f2fs and BOOT is Android's: nothing of Linux is checked, the start goes on.
    if context.mode == 'device' and android_boot_installed():
        return {'android': True, 'linux_checked': False}
    volume = context.volume('userdata')
    owned = not volume['mounted']
    mounted = False
    try:
        if owned:
            context.mount('userdata', 'ro', False, volume['volume_token'])
            mounted = True
        with fs.PinnedVolume(context, 'userdata', volume['volume_token']) as pin, Tree(pin) as tree:
            require_userdata(pin)
            if tree.exists(MANAGER):
                state = read_state(tree)
                tx = read_transaction(tree)
                require(not tx, 'SWITCH_PENDING', 'Return to Systems and resume or roll back the interrupted OS switch',
                        transaction_id=tx['id'] if tx else None)
                slots(tree, state)
                reconcile_completed_guard(tree, state)
                boot_identity(context)
            else:
                state = None
                require(not sentinel_path(context).exists(), 'SENTINEL_UNRESOLVED',
                        'The boot guard is unresolved; inspect the last Systems transaction')
            try:
                init = tree.resolve('sbin/init')
                st = tree.lstat(init)
                require(stat.S_ISREG(st.st_mode) and st.st_mode & 0o111,
                        'LINUX_INIT_MISSING', 'Install or restore Linux before normal boot')
                executable = elf_info(tree, init)
            except FileNotFoundError:
                fail('LINUX_INIT_MISSING', 'Install or restore Linux before normal boot')
            return {'normal_boot_allowed': True, 'active': state['active'] if state else 'unmanaged-linux',
                    'init': executable['path'], 'temporary_read_only_mount': owned,
                    'preserved_existing_mount': not owned}
    finally:
        if owned and mounted:
            context.unmount('userdata', volume['volume_token'])


def check_platform(context):
    profile = load_profile(context)
    boot = boot_identity(context)
    with fs.PinnedVolume(context, 'userdata') as pin, Tree(pin) as tree:
        require_userdata(pin)
        return {'profile_sha256': digest(profile), 'boot': boot,
                'validation': validate_root(tree, profile, pin.volume.get('filesystem_uuid')),
                'writes_userdata': False, 'programs_executed': False}


def parser():
    p = fs.Parser(description=__doc__)
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument('--device', action='store_true')
    kind.add_argument('--fixture', type=Path)
    commands = p.add_subparsers(dest='operation', required=True, parser_class=fs.Parser)
    commands.add_parser('status')
    commands.add_parser('boot-check')
    commands.add_parser('check-platform')
    cap = commands.add_parser('capture-profile')
    cap.add_argument('--module-tree', action='append', default=[])
    cap.add_argument('--modules-built-in', action='store_true')
    cap.add_argument('--firmware-tree', action='append', default=[], required=True)
    cap.add_argument('--adapter-path', action='append', default=[], required=True)
    cap.add_argument('--init-system', choices=('systemd', 'openrc'), default='systemd')
    cap.add_argument('--require-command', action='append', default=[])
    cap.add_argument('--python-module', action='append', default=[])
    charge = commands.add_parser('charger-mode')
    charge.add_argument('--set', choices=('on', 'off'))
    search = commands.add_parser('package-search')
    search.add_argument('--family', required=True)
    search.add_argument('--release', default='')
    search.add_argument('--query', required=True)
    commands.add_parser('images')
    keep = commands.add_parser('images-download')
    keep.add_argument('--remote', required=True, help='An image of the catalog, saved on the phone')
    keep.add_argument('--confirm', action='store_true')
    drop = commands.add_parser('images-delete')
    drop.add_argument('--file', required=True)
    drop.add_argument('--confirm', action='store_true')
    cat = commands.add_parser('catalog')
    cat.add_argument('--refresh', action='store_true', help='Check the servers of the images (over Wi-Fi)')
    cat.add_argument('--force', action='store_true', help='Check even the servers checked recently')
    imp = commands.add_parser('import-plan')
    imp.add_argument('--source-volume')
    imp.add_argument('--manifest')
    imp.add_argument('--remote', help='An image of the catalog, downloaded during the installation')
    imp.add_argument('--wipe', action='store_true', help='Erase userdata when the installation starts')
    imp.add_argument('--expected-source-token')
    imp.add_argument('--desktop', choices=DESKTOPS, default='image')
    imp.add_argument('--account', choices=('keep', 'new'), default='keep')
    imp.add_argument('--username')
    imp.add_argument('--password-file', type=Path)
    imp.add_argument('--password-hash-file', type=Path, help='Private file: a SHA-512 crypt hash instead of the password')
    imp.add_argument('--trial-seconds', type=int, default=0)
    imp.add_argument('--authorized-key-file', type=Path)
    imp.add_argument('--wifi-file', type=Path, help='Private file: the network name, then its password, one per line')
    imp.add_argument('--app', action='append', choices=APPS, default=[])
    imp.add_argument('--package', action='append', default=[], help='A package found by package-search')
    imp.add_argument('--shell', action='append', choices=SHELLS, default=[], help='Another shell beside the desktop')
    imp.add_argument('--greeter', choices=GREETERS, default='auto', help='The sign-in screen')
    imp.add_argument('--locale', choices=LOCALES, help="The new system's language")
    imp.add_argument('--timezone', help="The new system's time zone, as Europe/Kyiv")
    imp.add_argument('--script-volume')
    imp.add_argument('--script')
    imp.add_argument('--expected-script-token')
    imp.add_argument('--output', type=Path, help='New private RAM file for the full sealed plan; stdout is a compact summary')
    for action in ('import', 'switch'):
        ap = commands.add_parser(action)
        ap.add_argument('--plan-file', type=Path, required=True)
        ap.add_argument('--confirm', action='store_true')
        ap.add_argument('--expected-plan-seal')
        ap.add_argument('--expected-plan-file-sha256')
    sw = commands.add_parser('switch-plan')
    sw.add_argument('--slot', required=True)
    sw.add_argument('--output', type=Path, help='New private RAM file for the full sealed plan; stdout is a compact summary')
    rm = commands.add_parser('remove-slot')
    rm.add_argument('--slot', required=True)
    rm.add_argument('--confirm', action='store_true')
    rec = commands.add_parser('recover')
    rec.add_argument('--direction', choices=('resume', 'rollback'), required=True)
    rec.add_argument('--expected-transaction', required=True)
    rec.add_argument('--confirm', action='store_true')
    return p


def main(argv=None):
    global PROGRESS_EVENTS
    PROGRESS_EVENTS = True  # The command line feeds the interface; library callers stay quiet.
    signal.signal(signal.SIGTERM, lambda *unused: sys.exit(143))
    operation = 'unknown'
    try:
        args = parser().parse_args(argv)
        operation = args.operation
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        with context.operation_lock():
            if operation == 'status':
                result = status(context)
            elif operation == 'boot-check':
                result = boot_check(context)
            elif operation == 'check-platform':
                result = check_platform(context)
            elif operation == 'capture-profile':
                result = capture_profile(context, args.module_tree, args.firmware_tree, args.adapter_path,
                                         args.init_system, args.modules_built_in, args.require_command, args.python_module)
            elif operation == 'catalog':
                result = catalog(context, args.refresh, args.force)
            elif operation == 'images':
                result = images(context)
            elif operation == 'images-download':
                result = images_download(context, args.remote, args.confirm)
            elif operation == 'images-delete':
                result = images_delete(context, args.file, args.confirm)
            elif operation == 'charger-mode':
                result = charger_mode(context, args.set)
            elif operation == 'package-search':
                result = package_search(context, args.family, args.release, args.query)
            elif operation == 'import-plan':
                require(args.remote or (args.source_volume and args.manifest), 'INVALID_ARGUMENT',
                        'Choose an image file or an image of the catalog')
                if not args.remote and args.manifest.endswith('.json'):
                    result = import_plan(context, args.source_volume, args.manifest, args.expected_source_token)
                else:
                    secret = args.password_file.read_text().rstrip('\n') if args.password_file else None
                    hashed = args.password_hash_file.read_text().strip() if args.password_hash_file else None
                    key = args.authorized_key_file.read_text() if args.authorized_key_file else None
                    wifi = None
                    if args.wifi_file:
                        lines = args.wifi_file.read_text().split('\n')
                        wifi = {'ssid': lines[0], 'password': lines[1] if len(lines) > 1 else ''}
                        if len(lines) > 2 and lines[2] == 'saved':
                            wifi['password'] = saved_key(context, wifi['ssid'])
                    script = {'volume': args.script_volume or args.source_volume, 'token': args.expected_script_token,
                              'path': args.script} if args.script else None
                    result = image_plan(context, args.source_volume, args.manifest,
                                        image_options(args.desktop, args.account, args.username, secret,
                                                      args.trial_seconds, key, wifi, args.app, script, args.package,
                                                      args.shell, args.greeter, args.locale, args.timezone, hashed),
                                        args.expected_source_token, wipe=args.wipe,
                                        remote=linux_entry(context, args.remote) if args.remote else None)
            elif operation == 'remove-slot':
                result = remove_slot(context, args.slot, args.confirm)
            elif operation == 'switch-plan':
                result = switch_plan(context, args.slot)
            elif operation in ('import', 'switch'):
                plan = load_plan_file(args.plan_file, args.expected_plan_seal, args.expected_plan_file_sha256)
                result = (install if operation == 'import' else switch)(context, plan, args.confirm)
            else:
                result = recover(context, args.direction, args.expected_transaction, args.confirm)
            if operation in ('import-plan', 'switch-plan') and args.output:
                result = save_plan_result(context, result, args.output)
        print(json.dumps({**result, 'ok': True, 'operation': operation, 'mode': context.mode}, ensure_ascii=True, separators=(',', ':')))
        return 0
    except fs.FilesystemError as exc:
        error = {'code': exc.code, 'message': exc.message, 'details': exc.details}
        code = 2
    except (OSError, EOFError, ValueError, KeyError, TypeError, struct.error, tarfile.TarError) as exc:
        error = {'code': 'IO_OR_INPUT_ERROR', 'message': str(exc), 'details': {'errno': getattr(exc, 'errno', None)}}
        code = 3
    print(json.dumps({'ok': False, 'operation': operation, 'error': error}, ensure_ascii=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
