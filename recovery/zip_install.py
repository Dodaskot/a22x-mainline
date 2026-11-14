#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""ZIPs that install themselves (2026-11-11): what TWRP calls Install.

A flashable ZIP carries its own installer, META-INF/com/google/android/update-binary: a shell
script (Magisk, AnyKernel3, modules) or a program (edify). TWRP runs it as root with the whole
phone in reach. Here it runs as root too, as its owner asked, but in a room of its own:

  - its own mount, process, IPC and host-name spaces, and a root made of tmpfs;
  - a /dev with only the partitions an installer may write: boot, dtbo, the vbmeta partitions,
    super, prism and optics, and super's logical partitions (system, vendor, product, odm) as
    devices of their own. The bootloader chain (preloader, lk, tee, tzar ...), the modem, the
    calibration and identity partitions, this recovery, userdata and metadata have no device
    there: an installer that names them finds nothing;
  - /cache and /data are empty folders in RAM, so the recovery's settings, keys and Wi-Fi
    networks on cache cannot be wiped, and Android's encrypted data is not touched;
  - a busybox shell at /sbin/sh with getprop, as installers expect in a recovery;
  - (2026-11-12 audit) the room is entered by pivot_root in a mount space of its own, with the
    recovery's root detached, not by chroot, which root leaves again at will; no network; and
    without the capabilities that reach past the room's /dev: making device nodes (mknod, as
    of the preloader's mmcblk0boot0), loading kernel modules, raw I/O, ptrace, restarting the
    phone, setting the clock, chroot and the network's. CAP_SYS_ADMIN stays, as mount and the
    device mapper need it, and with it a determined installer could still mount devtmpfs; so
    the bootloader chain, the modem, the identity partitions and this recovery are hashed before
    and after, and a change of any of them fails the run as PROTECTED_PARTITION_CHANGED.

The BOOT there is kept on cache first. Afterwards the partitions are compared with what they
were: the result names what changed, an Android BOOT gets the mark that keeps this recovery, and
the usual check before a restart judges the new BOOT by its contents.

What the phone is, an installer asks Android's properties. A script calls getprop, which is a
script here too. A program (edify's getprop("ro.product.device")) reads the shared property area,
so the room has one at /dev/__properties__: a single file in the form every bionic reads when
that path is no folder, holding this phone's names and what the installed Android's build.prop
files say (2026-11-11).

A ROM package for dynamic partitions maps super's partitions itself (edify's map_partition and
update_dynamic_partitions, through the device mapper) and then waits for the nodes that ueventd
makes in Android. The room has /dev/device-mapper, and while the installer runs, each mapping it
makes gets its node and its by-uuid link there, provided it lies on super alone. Afterwards the
mappings are removed, and the result names the logical partitions that were written and whether
super's layout changed (2026-11-11).

    zip_install.py --device|--fixture DIR inspect --volume V --volume-token T --path P
    zip_install.py ... run --volume V --volume-token T --path P --expected-sha256 SHA --confirm
"""
import argparse
import contextlib
import copy
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
import stat
import struct
import subprocess
import sys
import time
import zipfile

import filesystem as fs
import flash
import install
from install import require
import dm_linear
import lp_metadata as lp
import saved_boot

INSTALLER = 'META-INF/com/google/android/update-binary'
INSTALLER_LIMIT = 32 << 20
# What an installer may write. Never the bootloader chain, the modem, calibration, this recovery or Android's data.
WRITABLE = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'super', 'prism', 'optics')
WATCHED = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor')
LOGICAL = ('system', 'vendor', 'product', 'odm')
ROOM = Path('/run/a22x-recovery/zip-room')
# Partitions no installer may change, checked before and after it runs (2026-11-12 audit): the
# preloader in the eMMC's boot areas, the bootloader chain, the modem, the identity and
# calibration partitions, and this recovery.
PROTECTED_AREAS = (('preloader-boot0', '/dev/mmcblk0boot0'), ('preloader-boot1', '/dev/mmcblk0boot1'))
PROTECTED = ('lk', 'tee1', 'tzar', 'sspm_1', 'scp1', 'spmfw', 'gz1', 'mcupm_1', 'dpm_1', 'pi_img', 'md1img', 'efs', 'sec_efs',
             'nvram', 'nvdata', 'nvcfg', 'proinfo', 'protect1', 'protect2', 'persist', 'seccfg', 'efuse', 'keydata', 'keyrefuge',
             'sec1', 'steady', 'up_param', 'param', 'uh', 'recovery')
# Capabilities the installer loses (linux/capability.h). CAP_SETPCAP (8), which dropping needs,
# goes last. CAP_SYS_ADMIN (21) stays for mount and the device mapper.
DROPPED_CAPABILITIES = (12, 13, 16, 17, 18, 19, 20, 22, 25, 27, 29, 30, 32, 33, 34, 35, 36, 37, 38, 39, 40, 8)
PR_CAPBSET_DROP, SYS_PIVOT_ROOT, MNT_DETACH = 24, 41, 2
RUN_SECONDS = 1800
LOG_LINES = 400
SECTOR = 512
PROPS = {'ro.product.device': 'a22x', 'ro.build.product': 'a22x', 'ro.product.name': 'a22xnsxx', 'ro.product.model': 'SM-A226B',
         'ro.product.brand': 'samsung', 'ro.product.manufacturer': 'samsung', 'ro.product.cpu.abi': 'arm64-v8a',
         'ro.product.cpu.abilist': 'arm64-v8a,armeabi-v7a,armeabi', 'ro.product.cpu.abilist64': 'arm64-v8a',
         'ro.product.cpu.abilist32': 'armeabi-v7a,armeabi', 'ro.board.platform': 'mt6833', 'ro.hardware': 'mt6833',
         'ro.boot.dynamic_partitions': 'true', 'ro.build.ab_update': 'false', 'ro.bootmode': 'recovery', 'ro.twrp.boot': '1'}
GETPROP = '''#!/sbin/sh
# getprop for installers: the values the recovery knows, then Android's build.prop files when mounted.
files="/default.prop /system/build.prop /system_root/system/build.prop /vendor/build.prop /product/etc/build.prop"
if [ -z "$1" ]; then
    for f in $files; do [ -f "$f" ] && sed -n 's/^\\([^#=][^=]*\\)=\\(.*\\)$/[\\1]: [\\2]/p' "$f"; done
    exit 0
fi
# The name is compared as text, never put into a pattern: '.' matched any character and '/'
# broke the expression (2026-11-14 review).
for f in $files; do
    [ -f "$f" ] || continue
    value=$(awk -v name="$1" 'index($0, name "=") == 1 { print substr($0, length(name) + 2); exit }' "$f")
    [ -n "$value" ] && { echo "$value"; exit 0; }
done
echo "$2"
'''


SYS_BLOCK = Path('/sys/block')
MAPPER_CONTROL = '/dev/device-mapper'
DM_IOCTL = struct.Struct('<3I3Ii3IQ128s129s7s')  # struct dm_ioctl of <linux/dm-ioctl.h>, 312 bytes
DM_DEV_REMOVE = (3 << 30) | (DM_IOCTL.size << 16) | (0xfd << 8) | 4
MAPPER_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,120}')
LAYOUT_BYTES = 1 << 20  # super's geometry and its first metadata copies lie here
PROPERTY_MAGIC, PROPERTY_VERSION, PROPERTY_VALUE, PROPERTY_AREA = 0x504f5250, 0xfc6ed0ab, 92, 128 << 10
PROPERTY_NAME = re.compile(r'[A-Za-z0-9_@:-]+(\.[A-Za-z0-9_@:-]+)*')
PROPERTY_FILES = (('system', ('system/build.prop', 'build.prop')), ('vendor', ('build.prop',)), ('product', ('etc/build.prop', 'build.prop')),
                  ('odm', ('etc/build.prop', 'build.prop')))
PROPERTY_LIMIT = 1500


def fail(code, message, **details):
    fs.fail(code, message, **details)


def sectors_written(stat_file):
    """The sectors the kernel has written to a block device, from its stat file (the seventh field).
    The one place that reads it (2026-11-14 review: two copies of the field's index)."""
    return int((Path(stat_file).read_text().split() + ['0'] * 7)[6])


def device_written(path):
    """sectors_written of the block device at PATH."""
    st = os.stat(path)
    return sectors_written('/sys/dev/block/%d:%d/stat' % (os.major(st.st_rdev), os.minor(st.st_rdev)))


def mapped_devices():
    """The device-mapper devices the kernel has now: {'dm-0': {...}}."""
    found = {}
    for entry in SYS_BLOCK.glob('dm-*'):
        try:
            found[entry.name] = {'name': (entry / 'dm/name').read_text().strip(), 'uuid': (entry / 'dm/uuid').read_text().strip(),
                                 'devno': (entry / 'dev').read_text().strip(),
                                 'slaves': sorted((s / 'dev').read_text().strip() for s in (entry / 'slaves').iterdir()),
                                 'sectors_written': sectors_written(entry / 'stat')}
        except (OSError, ValueError):
            continue
    return found


class Mapper:
    """What ueventd does for an installer that maps super's partitions itself: every mapping made
    during the run gets its node in the room and the links libdm waits for. A mapping that lies
    on anything but super gets none."""
    def __init__(self, block, before, super_devno):
        self.block, self.before, self.super, self.seen = Path(block), set(before), super_devno, {}

    def tend(self):
        for kernel, device in mapped_devices().items():
            if kernel in self.before or kernel in self.seen or not device['slaves']:
                continue  # No table yet: the next look finds it.
            allowed = set(device['slaves']) == {self.super} and bool(MAPPER_NAME.fullmatch(device['name']))
            self.seen[kernel] = allowed
            if not allowed or not self.block.is_dir():
                continue
            major, minor = (int(part) for part in device['devno'].split(':'))
            with contextlib.suppress(FileExistsError):
                os.mknod(self.block / kernel, 0o600 | stat.S_IFBLK, os.makedev(major, minor))
            links = [self.block / 'mapper' / device['name']]
            if MAPPER_NAME.fullmatch(device['uuid']):
                links.append(self.block / 'mapper/by-uuid' / device['uuid'])
            for link in links:
                with contextlib.suppress(OSError):  # A loop node of that name stays what it is.
                    link.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink('/dev/block/' + kernel, link)


def remove_mapping(name):
    """DM_DEV_REMOVE by name; False when the kernel still holds the device. The recovery makes no
    node for the device mapper's control device at its start, so it is made here when missing."""
    request = bytearray(DM_IOCTL.pack(4, 0, 0, DM_IOCTL.size, 0, 0, 0, 0, 0, 0, 0, name.encode(), b'', b''))
    try:
        if not os.path.exists(MAPPER_CONTROL):
            major, minor = (int(part) for part in Path('/sys/class/misc/device-mapper/dev').read_text().strip().split(':'))
            os.mknod(MAPPER_CONTROL, 0o600 | stat.S_IFCHR, os.makedev(major, minor))
        fd = os.open(MAPPER_CONTROL, os.O_RDWR | os.O_CLOEXEC)
    except (OSError, ValueError):
        return False
    try:
        fcntl.ioctl(fd, DM_DEV_REMOVE, request)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def unmap_after(before):
    """The mappings an installer left: which were written to, and which could not be removed."""
    written, left = [], []
    for kernel, device in sorted(mapped_devices().items()):
        if kernel in before:
            continue
        if device['sectors_written']:
            written.append(device['name'])
        for attempt in range(30):
            if remove_mapping(device['name']) or kernel not in mapped_devices():
                break
            time.sleep(.1)
        else:
            left.append(device['name'])
    return written, left


def layout_hash(context, found):
    """What super says of its own layout: the bytes its geometry and metadata begin with."""
    del context
    fd = os.open(found['super']['path'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return hashlib.sha256(os.pread(fd, LAYOUT_BYTES, 0)).hexdigest()
    finally:
        os.close(fd)


def property_area(values):
    """Android's property area as one file (bionic's prop_area with its trie of prop_bt and
    prop_info): a name's dotted parts make the levels, and the parts under one parent form a
    binary tree ordered by length, then bytes. A value longer than 91 bytes has no place in
    this form and is left out, as are names bionic would not look up."""
    data = bytearray(20 + PROPERTY_VALUE)  # The root node, then the room a writer keeps for a value in change.

    def new_node(part):
        offset = len(data)
        data.extend(struct.pack('<5I', len(part), 0, 0, 0, 0) + part + b'\0')
        data.extend(bytes(-len(data) % 4))
        return offset

    def child(parent, part):
        at = struct.unpack_from('<I', data, parent + 16)[0]
        if not at:
            at = new_node(part)
            struct.pack_into('<I', data, parent + 16, at)
            return at
        while True:
            length = struct.unpack_from('<I', data, at)[0]
            other = bytes(data[at + 20:at + 20 + length])
            if (len(part), part) == (length, other):
                return at
            field = at + (8 if (len(part), part) < (length, other) else 12)
            following = struct.unpack_from('<I', data, field)[0]
            if not following:
                following = new_node(part)
                struct.pack_into('<I', data, field, following)
                return following
            at = following

    for name, value in values.items():
        raw = value.encode('utf-8', 'replace')
        if not PROPERTY_NAME.fullmatch(name) or len(raw) >= PROPERTY_VALUE or b'\0' in raw:
            continue
        at = 0
        for part in name.encode().split(b'.'):
            at = child(at, part)
        if struct.unpack_from('<I', data, at + 4)[0]:
            continue  # The first value given for a name stands.
        struct.pack_into('<I', data, at + 4, len(data))
        data.extend(struct.pack('<I', len(raw) << 24) + raw.ljust(PROPERTY_VALUE, b'\0') + name.encode() + b'\0')
        data.extend(bytes(-len(data) % 4))
    area = struct.pack('<4I', len(data), 0, PROPERTY_MAGIC, PROPERTY_VERSION) + bytes(112) + bytes(data)
    return area.ljust(max(PROPERTY_AREA, -(-len(area) // 4096) * 4096), b'\0')


def property_lines(text, found):
    """name=value lines of a build.prop; the first value given for a name stands."""
    for line in text.splitlines():
        name, mark, value = line.partition('=')
        name = name.strip()
        if mark and name and not name.startswith('#') and name not in found and len(found) < PROPERTY_LIMIT:
            found[name] = value.strip()


def read_inside(folder, relative, limit=512 << 10):
    """A regular file below FOLDER, never through a symlink: the image is not trusted to stay inside itself."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        parts = relative.split('/')
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = following
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return None
            return os.read(file_fd, limit).decode('utf-8', 'replace')
        finally:
            os.close(file_fd)
    except OSError:
        return None
    finally:
        os.close(fd)


def describe(fd, name, check_members=True):
    """What the ZIP at FD is: its SHA-256, its installer (a script or a program, and its own SHA-256)
    and what it says of itself. CHECK_MEMBERS reads every member against its CRC; the run of a ZIP
    whose SHA-256 is the reviewed one does not need that again (2026-11-14 review: a ROM of 2.5 GB
    was read four times before its installer started)."""
    size = os.fstat(fd).st_size
    digest = hashlib.sha256()
    at = 0
    while at < size:
        data = os.pread(fd, 4 << 20, at)
        require(data, 'ZIP_UNREADABLE', 'The ZIP could not be read to its end')
        digest.update(data)
        at += len(data)
    try:
        with zipfile.ZipFile(os.fdopen(os.dup(fd), 'rb')) as archive:
            names = set(archive.namelist())
            require(INSTALLER in names, 'ZIP_NO_INSTALLER',
                    'This ZIP has no installer of its own. Images and ROM packages go through Android or Flash a partition')
            info = archive.getinfo(INSTALLER)
            require(0 < info.file_size <= INSTALLER_LIMIT, 'ZIP_INSTALLER_INVALID', 'The installer in this ZIP has an unusable size')
            program = archive.read(INSTALLER)
            head = program[:64]
            module = {}
            if 'module.prop' in names:
                for line in archive.read('module.prop').decode('utf-8', 'replace').splitlines():
                    key, _, value = line.partition('=')
                    module[key.strip()] = value.strip()[:80]
            if check_members:
                require(archive.testzip() is None, 'ZIP_DAMAGED', 'A file in this ZIP does not match its checksum')
    except zipfile.BadZipFile:
        fail('ZIP_DAMAGED', 'This is not a complete ZIP archive')
    if head[:2] == b'#!':
        kind, arch = 'script', ''
    elif head[:4] == b'\x7fELF':
        machine = int.from_bytes(head[18:20], 'little')
        kind, arch = 'program', {0x28: 'arm', 0xb7: 'arm64'}.get(machine, 'other')
        require(arch != 'other', 'ZIP_INSTALLER_INVALID', 'The installer in this ZIP is a program for another processor')
    else:
        fail('ZIP_INSTALLER_INVALID', 'The installer in this ZIP is neither a script nor a program')
    if module.get('name'):
        what, title = 'module', (module['name'] + ' ' + module.get('version', '')).strip()
    elif 'assets/boot_patch.sh' in names or 'lib/arm64-v8a/libmagiskboot.so' in names:
        what, title = 'magisk', 'Magisk'
    elif 'anykernel.sh' in names:
        what, title = 'kernel', 'AnyKernel3'
    else:
        what, title = 'other', ''
    warnings = []
    if kind == 'program':
        warnings.append('This installer is a program: it can map and rewrite the logical partitions of super by itself')
    if what == 'module':
        warnings.append('A module needs Android data, which stays closed here: install it in the Magisk or APatch app')
    return {'name': name[:200], 'sha256': digest.hexdigest(), 'size': size, 'installer': kind, 'arch': arch, 'what': what,
            'installer_sha256': hashlib.sha256(program).hexdigest(),
            'title': title[:120], 'files': len(names), 'warnings': warnings, 'writable': list(WRITABLE), 'logical': list(LOGICAL)}


def targets(context):
    """The partitions an installer may write, as this phone has them: {label: target}."""
    found = {}
    for label in WRITABLE:
        with contextlib.suppress(fs.FilesystemError, OSError, KeyError):
            found[label] = context.target(label)
    require('boot' in found, 'ZIP_NO_BOOT', 'BOOT could not be identified on this phone')
    return found


def hashes(context, found):
    return {label: install.target_hash(context, found[label]) for label in WATCHED if label in found}


ROOM_MAPPING = 'a22x-zip-'
ROOM_NODES = Path('/run/a22x-recovery/zip-logical')


def package_sizes(fd):
    """The logical partitions a block-based OTA in the ZIP at FD rewrites whole, with the size its
    transfer list needs: {name: bytes}. A transfer list that moves or patches old blocks (an
    incremental update) needs the old layout and gives nothing here."""
    import android_rom
    sizes = {}
    with os.fdopen(os.dup(fd), 'rb') as raw, zipfile.ZipFile(raw) as archive:
        names = set(archive.namelist())
        for name in LOGICAL:
            listing = name + '.transfer.list'
            if listing not in names or not ({name + '.new.dat', name + '.new.dat.br'} & names):
                continue
            info = archive.getinfo(listing)
            if info.file_size > (4 << 20):
                continue
            data = archive.read(listing)
            commands = {line.split(' ', 1)[0] for line in data.decode('utf-8', 'replace').splitlines()[4:] if line.strip()}
            if commands <= {'new', 'zero', 'erase'}:
                sizes[name] = android_rom.transfer_size(data)
    return sizes


# 2026-11-14 review: the installer of the ZIP runs first; the recovery writes a block ROM's images
# itself only when that installer stopped without changing anything (the KKRT case), never over
# what an installer left half done.
NATIVE_NOTE = 'If the installer of this ZIP stops without changing anything, the recovery writes the images of this ROM itself'
NATIVE_STOPPED = 'The installer of this ZIP stopped without changing anything; the recovery wrote the images of this ROM itself and read them back'
NATIVE_REFUSED = 'The installer of this ZIP changed partitions before it stopped, so the recovery did not write the images itself'
NATIVE_LEFT_OUT = 'Not written by the recovery, as the script of the ZIP would have:'


def native_possible(fd, sizes):
    """Whether the recovery can write this block-based ROM itself: every image plain or Brotli with
    the decoder at hand."""
    import android_rom
    if not sizes:
        return False
    with os.fdopen(os.dup(fd), 'rb') as raw, zipfile.ZipFile(raw) as archive:
        names = set(archive.namelist())
    return all(name + '.new.dat' in names or os.path.exists(android_rom.BROTLI) for name in sizes)


def native_install(context, room, fd, sizes, found, progress):
    """2026-11-13, the KKRT One UI port on the phone: its own updater, a program made for Android
    8.1, wrote nothing in the room and crashed. A full block-based OTA is data this recovery can
    write itself: each image through the transfer list into its logical partition, read back, then
    the ROM's boot.img when it is a BOOT of this phone. Returns (lines for the result, exit code)."""
    import android_rom
    import bootimage
    require(room is not None, 'ZIP_ROOM_FAILED', 'The room for the installer could not be prepared')
    shown = []
    parts = sorted(sizes)
    try:
        with os.fdopen(os.dup(fd), 'rb') as raw, zipfile.ZipFile(raw) as archive:
            names = set(archive.namelist())
            for index, name in enumerate(parts):
                require(name in room.loops, 'ZIP_SUPER_LAYOUT', 'super has no logical partition ' + name)
                node = os.open(room.loops[name], os.O_RDWR | os.O_CLOEXEC)
                try:
                    size = os.lseek(node, 0, os.SEEK_END)
                    # Real bytes of each image (2026-11-13, the owner: "why only 1000 B?"), so the
                    # screen shows sizes and speed; a script's installer gives only a fraction.
                    done = android_rom.write_block_image(
                        archive, name, node, size,
                        lambda part, at, total: progress('Writing', {'label': 'super/' + part, 'size_bytes': total}, at))
                finally:
                    os.close(node)
                shown.append('%s: %d MiB written and read back' % (name, done['blocks'] * android_rom.BLOCK >> 20))
            if 'boot.img' in names and 'boot' in found:
                target = found['boot']
                info = archive.getinfo('boot.img')
                require(info.file_size <= target['size_bytes'], 'BOOT_TOO_LARGE', 'The BOOT image of this ROM is larger than BOOT')
                data = archive.read('boot.img')
                try:
                    this_phone = bootimage.inspect('boot', data)['device']['this_phone']
                except (ValueError, KeyError):
                    this_phone = False
                if not this_phone:
                    shown.append('boot.img: not a BOOT of this phone, left out')
                else:
                    progress('Writing', target, 0)
                    image = data + bytes(target['size_bytes'] - len(data))
                    writer = context.open_target(target, write=True)
                    try:
                        at = 0
                        while at < len(image):
                            at += os.pwrite(writer, image[at:at + (1 << 20)], at)
                        os.fsync(writer)
                        lp.drop_cached(writer)
                    finally:
                        os.close(writer)
                    require(install.target_hash(context, target) == hashlib.sha256(image).hexdigest(), 'READBACK_MISMATCH',
                            'BOOT does not match the image after writing')
                    shown.append('boot: written and read back')
            # What the script would also have written is named, not passed over in silence.
            left = sorted(n for n in names if n.endswith('.img') and n[:-4] in WRITABLE and n != 'boot.img')
            if left:
                shown.append(NATIVE_LEFT_OUT)
                shown.append(', '.join(left))
    except android_rom.RomError as error:
        fail(error.code, error.message, **error.details)
    return shown, 0


def fit_plan(opened, sizes):
    """2026-11-13, a One UI 6 port for this phone (KKRT, made from the A06): its odm is 263 MB where
    this phone's is 4.3 MB, and it carries no dynamic_partitions_op_list, so its installer would
    write past odm's end. Each partition such a package rewrites whole is made exactly its size
    first, the larger ones before the smaller grow, when one of them is too small:
    {name: [now, then]}, {} when all fit. Refused when super cannot hold them."""
    present = {p['name'] for p in opened.metadata['partitions']}
    missing = sorted(name for name in sizes if name not in present)
    require(not missing, 'ZIP_SUPER_LAYOUT', 'This ROM writes logical partitions super does not have: ' + ', '.join(missing),
            missing=missing)
    now = {name: opened.size_of(opened.partition(name)) for name in sizes}
    if all(now[name] >= need for name, need in sizes.items()):
        return {}
    device = opened.metadata['devices'][0]
    unit = max(device['alignment'], lp.SECTOR)
    then = {name: -(-need // unit) * unit for name, need in sizes.items()}
    groups = opened.metadata['groups']
    for index, group in enumerate(groups):
        if not group['maximum_size']:
            continue
        used = sum(then.get(p['name'], opened.size_of(p)) for p in opened.metadata['partitions'] if p['group'] == index)
        require(used <= group['maximum_size'], 'ZIP_SUPER_TOO_SMALL',
                'super cannot hold the partitions of this ROM: its group %s would need %d MiB of %d' %
                (group['name'], used >> 20, group['maximum_size'] >> 20), group=group['name'])
    plan = {name: [now[name], then[name]] for name in sizes if now[name] != then[name]}
    # Tried first on a copy of the metadata, exactly as it will be written: a sum of free space
    # missed what the alignment of each new extent costs (2026-11-13 review).
    trial = copy.copy(opened)
    trial.metadata = copy.deepcopy(opened.metadata)
    try:
        resize_all(trial, plan)
        # 2026-11-14 review: metadata too large for super came out only when apply_fit wrote it.
        lp.serialize(trial.metadata, trial.geometry['metadata_max_size'])
    except lp.MetadataError as error:
        if error.code == 'METADATA_TOO_LARGE':
            fail('ZIP_SUPER_METADATA', 'The new layout of super would not fit in its metadata', reason=error.code)
        fail('ZIP_SUPER_TOO_SMALL', 'super has not enough free space for the partitions of this ROM', reason=error.code)
    return plan


def resize_all(opened, plan):
    """The partitions that shrink first, then those that grow; in memory only."""
    for name, (now, then) in sorted(plan.items(), key=lambda item: item[1][1] - item[1][0]):
        opened.resize(name, then, commit=False)


def apply_fit(context, plan):
    """The layout of FIT_PLAN written in one step: every change made in memory, then the metadata
    written once, so a failure leaves super as it was (2026-11-13 review: each resize was written
    by itself, and one that failed left the others done)."""
    _, fd, opened = flash.open_super(context, write=True)
    try:
        resize_all(opened, plan)
        opened.commit()
    finally:
        os.close(fd)


def logical_extents(context):
    """super's logical partitions that lie in one piece: {name: (offset bytes, size bytes)}."""
    try:
        target, fd, opened = flash.open_super(context)
    except (fs.FilesystemError, lp.MetadataError, OSError, KeyError):
        return {}
    try:
        pieces = {}
        for name in LOGICAL:
            with contextlib.suppress(lp.MetadataError):
                part = opened.partition(name)
                extents = opened.extents_of(part)
                if len(extents) == 1 and extents[0]['type'] == lp.TARGET_LINEAR and extents[0]['device'] == 0:
                    pieces[name] = (extents[0]['start'] * SECTOR, extents[0]['sectors'] * SECTOR)
        return pieces
    finally:
        os.close(fd)


class Room:
    """The installer's room on the phone: a tmpfs root with the allowed devices, entered by chroot
    inside mount and process spaces of its own."""
    def __init__(self, context, found):
        self.context, self.found, self.loops = context, found, {}
        self.root = ROOM
        self.mapped_before = set(mapped_devices())

    def run(self, *args, check=True):
        done = subprocess.run([fs.BUSYBOX, *args], capture_output=True, text=True, timeout=60)
        require(done.returncode == 0 or not check, 'ZIP_ROOM_FAILED', 'The room for the installer could not be prepared',
                step=' '.join(args[:3]), detail=done.stderr.strip()[:200])
        return done

    def attach_logical(self):
        """super's logical partitions as devices of their own, through the device mapper over their
        exact extents (2026-11-13: loop devices from a partition's start ran to the end of super, so
        an installer writing past a partition's end overwrote the ones after it). The mappings are
        known before the installer starts, so they are not taken for its own."""
        try:
            _, fd, opened = flash.open_super(self.context)
        except (fs.FilesystemError, lp.MetadataError, OSError, KeyError):
            return
        try:
            layout = {}
            for name in LOGICAL:
                with contextlib.suppress(lp.MetadataError):
                    extents = opened.extents_of(opened.partition(name))
                    if extents and all(e['type'] == lp.TARGET_LINEAR and e['device'] == 0 for e in extents):
                        layout[name] = [(e['start'], e['sectors']) for e in extents]
        finally:
            os.close(fd)
        st = os.stat(self.found['super']['path'])
        device = '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev))
        ROOM_NODES.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name, extents in layout.items():
            mapping = ROOM_MAPPING + name
            with contextlib.suppress(OSError):
                dm_linear.remove(mapping)  # one left by a run that was cut short
            try:
                devno = dm_linear.create(mapping, dm_linear.linear_table(extents, device), read_only=False)
            except OSError:
                continue
            major, minor = (int(part) for part in devno.split(':'))
            node = ROOM_NODES / name
            try:
                with contextlib.suppress(FileNotFoundError):
                    node.unlink()
                os.mknod(node, 0o600 | stat.S_IFBLK, os.makedev(major, minor))
            except OSError:
                # 2026-11-14 review: the mapping stayed, unknown to detach, and held the partition.
                with contextlib.suppress(OSError):
                    dm_linear.remove(mapping)
                continue
            self.loops[name] = str(node)
            self.mapped_before.add('dm-%d' % minor)

    def written(self):
        """The logical partitions written through the room's own mappings."""
        names = []
        for name in self.loops:
            with contextlib.suppress(OSError, ValueError):
                if device_written(self.loops[name]):
                    names.append(name)
        return names

    def detach(self):
        for name, node in self.loops.items():
            for attempt in range(30):
                try:
                    dm_linear.remove(ROOM_MAPPING + name)
                    break
                except OSError:
                    time.sleep(.1)
            with contextlib.suppress(OSError):
                os.unlink(node)
        self.loops = {}

    def android_properties(self):
        """What the installed Android says of itself: each logical partition is mounted read-only
        for the moment its build.prop is read, so that an installer may ask before it mounts anything."""
        found = {}
        for name, files in PROPERTY_FILES:
            device = self.loops.get(name)
            if not device:
                continue
            point = self.root / 'mnt' / ('props-' + name)
            point.mkdir(exist_ok=True)
            # ext4 without its journal replayed, f2fs without its roll-forward recovery (2026-11-14
            # review): reading a property must not write to super.
            for kind, options in (('erofs', 'ro'), ('ext4', 'ro,noload'), ('f2fs', 'ro,norecovery')):
                if self.run('mount', '-t', kind, '-o', options, device, str(point), check=False).returncode:
                    continue
                try:
                    for relative in files:
                        text = read_inside(str(point), relative)
                        if text is not None:
                            property_lines(text, found)
                finally:
                    self.run('umount', str(point), check=False)
                break
            with contextlib.suppress(OSError):
                point.rmdir()
        return found

    def build(self):
        """Called inside the new mount space: the tmpfs root, its folders, tools and devices."""
        root = self.root
        self.run('mount', '--make-rprivate', '/')
        root.mkdir(parents=True, exist_ok=True)
        self.run('mount', '-t', 'tmpfs', '-o', 'mode=0755,size=768m', 'a22x-zip', str(root))
        for name in ('sbin', 'dev/block/by-name', 'dev/block/mapper', 'dev/block/bootdevice', 'proc', 'sys', 'tmp', 'etc', 'system',
                     'system_root', 'vendor', 'product', 'odm', 'prism', 'optics', 'data', 'cache', 'sdcard', 'mnt',
                     'system/bin'):  # prism and optics: Samsung ROM scripts mount them there (2026-11-13)
            (root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fs.BUSYBOX, root / 'sbin/busybox')
        (root / 'sbin/busybox').chmod(0o755)
        for applet in self.run('--list').stdout.split():
            if applet not in ('busybox', 'getprop'):
                with contextlib.suppress(FileExistsError):
                    os.symlink('busybox', root / 'sbin' / applet)
        (root / 'sbin/getprop').write_text(GETPROP)
        (root / 'sbin/getprop').chmod(0o755)
        os.symlink('sbin', root / 'bin')
        os.symlink('/sbin/sh', root / 'system/bin/sh')
        self.default_prop = root / 'default.prop'
        self.default_prop.write_text(''.join('%s=%s\n' % item for item in PROPS.items()))
        for name, major, minor in (('null', 1, 3), ('zero', 1, 5), ('full', 1, 7), ('random', 1, 8), ('urandom', 1, 9), ('tty', 5, 0)):
            os.mknod(root / 'dev' / name, 0o666 | 0o020000, os.makedev(major, minor))
        os.symlink('/proc/self/fd', root / 'dev/fd')
        os.symlink('/dev/block/by-name', root / 'dev/block/bootdevice/by-name')
        # The paths ROM scripts of MediaTek phones name partitions by.
        for alias in ('dev/block/platform/bootdevice', 'dev/block/platform/11230000.mmc'):
            (root / alias).mkdir(parents=True, exist_ok=True)
            os.symlink('/dev/block/by-name', root / alias / 'by-name')
        # The device mapper, for an installer that maps super's partitions itself; Mapper adds the nodes.
        with contextlib.suppress(OSError, ValueError):
            major, minor = (int(part) for part in Path('/sys/class/misc/device-mapper/dev').read_text().strip().split(':'))
            os.mknod(root / 'dev/device-mapper', 0o600 | stat.S_IFCHR, os.makedev(major, minor))
        (root / 'dev/block/mapper/by-uuid').mkdir(parents=True, exist_ok=True)
        lines = []
        for label, target in self.found.items():
            st = os.stat(target['path'])
            node = root / 'dev/block' / Path(target['path']).name
            os.mknod(node, 0o600 | 0o060000, st.st_rdev)
            os.symlink('/dev/block/' + node.name, root / 'dev/block/by-name' / label)
        for name, device in self.loops.items():
            st = os.stat(device)
            os.mknod(root / 'dev/block/mapper' / name, 0o600 | 0o060000, st.st_rdev)
            os.symlink('/dev/block/mapper/' + name, root / 'dev/block/by-name' / name)
            lines.append('/dev/block/mapper/%s %s auto ro 0 0\n' % (name, '/system_root' if name == 'system' else '/' + name))
        (root / 'etc/fstab').write_text(''.join(lines))
        (root / 'etc/recovery.fstab').write_text(''.join(lines))
        # The phone's own names first, then what the installed Android adds; both ways of asking see the same.
        values = dict(PROPS)
        for name, value in self.android_properties().items():
            values.setdefault(name, value)
        self.default_prop.write_text(''.join('%s=%s\n' % item for item in values.items() if '\n' not in item[1]))
        area = root / 'dev/__properties__'
        area.write_bytes(property_area(values))
        area.chmod(0o444)
        self.run('mount', '-t', 'proc', 'proc', str(root / 'proc'))
        self.run('mount', '-t', 'sysfs', '-o', 'ro', 'sysfs', str(root / 'sys'))


def protected_paths(context):
    if context.mode == 'fixture':
        folder = context.root / 'protected'
        return [(path.name, str(path)) for path in sorted(folder.iterdir())] if folder.is_dir() else []
    return list(PROTECTED_AREAS) + [(label, '/dev/disk/by-partlabel/' + label) for label in PROTECTED]


def protected_hashes(context):
    """The SHA-256 of every protected partition that is there."""
    found = {}
    for label, path in protected_paths(context):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            digest, at = hashlib.sha256(), 0
            while True:
                data = os.pread(fd, 1 << 20, at)
                if not data:
                    break
                digest.update(data)
                at += len(data)
            found[label] = digest.hexdigest()
        finally:
            os.close(fd)
    return found


def enter_room(root):
    """pivot_root into ROOT in a mount space of its own, the recovery's root detached and gone
    from reach; chroot would leave it within reach of root."""
    libc = ctypes.CDLL(None, use_errno=True)
    os.unshare(os.CLONE_NEWNS | os.CLONE_NEWNET)
    os.chdir(root)
    os.mkdir('.old-root', 0o700)
    if libc.syscall(SYS_PIVOT_ROOT, b'.', b'.old-root') != 0:
        raise OSError(ctypes.get_errno(), 'pivot_root')
    os.chdir('/')
    if libc.umount2(b'/.old-root', MNT_DETACH) != 0:
        raise OSError(ctypes.get_errno(), 'umount the old root')
    os.rmdir('/.old-root')


def drop_capabilities():
    """Out of the bounding set: root keeps no more than this after it starts the installer."""
    libc = ctypes.CDLL(None, use_errno=True)
    for capability in DROPPED_CAPABILITIES:
        if libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0 and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), 'capability %d' % capability)


def report_start(fd, error=None):
    """On the started channel: E just before the installer is started (the channel closes with exec),
    F and the error when the room could not be made (2026-11-14 review: that failure, ZIP_CHANGED too,
    exited 126 like an installer that stopped without changing anything, and the recovery then wrote
    the ROM itself)."""
    if fd is None:
        return
    with contextlib.suppress(OSError):
        if error is None:
            os.write(fd, b'E')
        else:
            os.write(fd, b'F' + json.dumps({'code': getattr(error, 'code', None) or 'ZIP_ROOM_FAILED',
                                             'message': str(error)[:300]}).encode())


def room_failed(reason=None):
    """The error of a room that could not be made (one text, 2026-11-14 review)."""
    return {'code': 'ZIP_ROOM_FAILED', 'message': 'The room for the installer could not be prepared',
            **({'reason': reason[:240]} if reason else {})}


def read_start(fd):
    """(ready, error) from the started channel, read after the run ended: READY when E came first
    (the room was made and exec was called), ERROR the F that followed (exec failed) or came alone
    (the room could not be made), or a room failure when nothing came; see started()."""
    data = b''
    os.set_blocking(fd, False)
    while True:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        data += chunk
    ready = data.startswith(b'E')
    error = None if ready else room_failed()
    at = data.find(b'F')
    if at >= 0:
        # An F that cannot be read after E is still an exec that failed, never a room failure
        # (2026-11-14 review: the room's text went into the installer's log).
        error = {'code': 'ZIP_INSTALLER_NOT_STARTED', 'message': 'exec failed'} if ready else room_failed()
        with contextlib.suppress(ValueError):
            value = json.loads(data[at + 1:])
            if isinstance(value, dict) and isinstance(value.get('code'), str) and isinstance(value.get('message'), str):
                error = value
    return ready, error


def started(ready, error, kind):
    """(installer started, room error, installer error) from read_start and the installer's KIND.
    A failed exec of a binary updater is the ZIP's own: an installer that stopped without changing
    anything, its reason kept (2026-11-14 review: it was said as a room that could not be made, then
    its reason was dropped). A failed exec of a script's is the room's own shell: the room failed
    (2026-11-14 review), never a reason for the recovery to write the ROM itself."""
    if not ready:
        return False, error, None
    if error is None:
        return True, None, None
    if kind == 'script':
        return False, room_failed(error['message']), None
    return True, None, error


def check_installer(path, expected):
    """The update-binary taken out for the run is the one reviewed (2026-11-14 review: only the
    ZIP's device, inode and size were compared, so its bytes could be changed in place)."""
    require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected, 'ZIP_CHANGED', 'The ZIP changed after it was reviewed')


def room_child(room, zip_fd, out_fd, kind, expected, started_fd=None):
    """The process the installer runs in: new spaces, the room, then update-binary as TWRP calls it."""
    os.setsid()
    os.unshare(os.CLONE_NEWNS | os.CLONE_NEWPID | os.CLONE_NEWIPC | os.CLONE_NEWUTS)
    # 2026-11-13, a whole ROM on the phone: the installer's own messages went into a pipe, buffered,
    # and were lost when it ended by a signal; that end was reported as 137 whatever the signal. Its
    # output goes through a terminal now, line by line, and the way it ended is said.
    master, slave = os.openpty()
    pid = os.fork()  # the first process of the new process space
    if pid:
        # This process shares the room's mounts and stays outside its root: it stands in for ueventd.
        os.close(slave)
        if started_fd is not None:
            os.close(started_fd)
        os.set_blocking(master, False)
        st = os.stat(room.found['super']['path'])
        mapper = Mapper(room.root / 'dev/block', room.mapped_before, '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev)))

        def drain():
            while True:
                try:
                    data = os.read(master, 65536)
                except (BlockingIOError, OSError):
                    return
                if not data:
                    return
                with contextlib.suppress(OSError):
                    os.write(2, data)
        while True:
            drain()
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            with contextlib.suppress(OSError, ValueError):
                mapper.tend()
            time.sleep(.03)
        drain()
        code = os.waitstatus_to_exitcode(status)
        if code < 0:
            with contextlib.suppress(OSError):
                os.write(2, ('room: the installer ended by signal %d (%s)\n' % (-code, signal.Signals(-code).name)).encode())
        os._exit(code & 0xff if code >= 0 else 128 + (-code & 0x7f))
    os.close(master)
    # The terminal takes 1 and 2 at once: its number may be 9, which the screen channel takes below.
    os.dup2(slave, 1)
    os.dup2(slave, 2)
    if slave > 2:
        os.close(slave)
    try:
        room.build()
        root = room.root
        # 2026-11-13, on the phone: the ZIP was copied into the room's RAM disk of 768 MB, and a whole
        # ROM (2.5 GB, KKRT) stopped with "No space left on device". The reviewed file itself is
        # bound into the room, read-only, and checked to be that very file.
        place = root / 'tmp/install.zip'
        place.touch(0o444)
        source = os.readlink('/proc/self/fd/%d' % zip_fd)
        room.run('mount', '--bind', source, str(place))
        room.run('mount', '-o', 'remount,bind,ro', str(place))
        bound, reviewed = os.stat(place), os.fstat(zip_fd)
        require((bound.st_dev, bound.st_ino, bound.st_size) == (reviewed.st_dev, reviewed.st_ino, reviewed.st_size),
                'ZIP_CHANGED', 'The ZIP changed after it was reviewed')
        with zipfile.ZipFile(place) as archive, open(root / 'tmp/update-binary', 'wb') as out:
            out.write(archive.read(INSTALLER))
        check_installer(root / 'tmp/update-binary', expected)
        (root / 'tmp/update-binary').chmod(0o755)
        os.dup2(out_fd, 9)
        os.set_inheritable(9, True)
        enter_room(root)
        drop_capabilities()
        os.chdir('/tmp')
        env = {'PATH': '/sbin:/system/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp', 'ANDROID_ROOT': '/system', 'ANDROID_DATA': '/data',
               'EXTERNAL_STORAGE': '/sdcard', 'SHELL': '/sbin/sh', 'ASH_STANDALONE': '1'}
        command = ['/sbin/sh', '/tmp/update-binary'] if kind == 'script' else ['/tmp/update-binary']
        report_start(started_fd)
        os.execve(command[0], command + ['3', '9', '/tmp/install.zip'], env)
    except BaseException as error:  # noqa: BLE001 - reported to the parent through the started channel and stderr
        sys.stderr.write('room: %s\n' % error)
        report_start(started_fd, error)
        os._exit(126)


def fixture_child(context, found, zip_fd, out_fd, kind, expected, started_fd=None):
    """Tests: the installer as a plain process in a folder of its own; its devices are the fixture's
    partition files under $A22X_ZIP_ROOT/dev/block/by-name."""
    root = context.root / 'zip-room'
    shutil.rmtree(root, ignore_errors=True)
    (root / 'dev/block/by-name').mkdir(parents=True)
    (root / 'tmp').mkdir()
    for label, target in found.items():
        os.symlink(target['path'], root / 'dev/block/by-name' / label)
    with open(root / 'tmp/install.zip', 'wb') as copy:
        copy.write(os.pread(zip_fd, os.fstat(zip_fd).st_size, 0))
    with zipfile.ZipFile(root / 'tmp/install.zip') as archive:
        (root / 'tmp/update-binary').write_bytes(archive.read(INSTALLER))
    check_installer(root / 'tmp/update-binary', expected)
    os.dup2(out_fd, 9)
    os.set_inheritable(9, True)
    os.chdir(root / 'tmp')
    require(kind == 'script', 'ZIP_INSTALLER_INVALID', 'Fixtures run script installers only')
    report_start(started_fd)
    os.execve('/bin/sh', ['/bin/sh', 'update-binary', '3', '9', str(root / 'tmp/install.zip')],
              {'PATH': '/usr/bin:/bin', 'A22X_ZIP_ROOT': str(root), 'TMPDIR': str(root / 'tmp')})


def follow(read_fd, pid, progress, deadline):
    """The installer's lines for the screen (ui_print ...) until it ends; the last lines and its exit code."""
    shown, buffer, fraction = [], b'', 0.0
    os.set_blocking(read_fd, False)
    status = None
    while True:
        try:
            data = os.read(read_fd, 65536)
        except BlockingIOError:
            data = None
        if data:
            buffer += data
            *lines, buffer = buffer.split(b'\n')
            for raw in lines:
                line = raw.decode('utf-8', 'replace').rstrip('\r')
                if line.startswith('ui_print'):
                    text = line[len('ui_print'):].strip()
                    if text:
                        shown.append(text[:240])
                        del shown[:-LOG_LINES]
                        progress(text[:120], fraction)
                elif line.startswith(('progress ', 'set_progress ')):
                    with contextlib.suppress(ValueError, IndexError):
                        fraction = max(0.0, min(1.0, float(line.split()[1])))
        elif data == b'' or status is not None:
            break
        if status is None:
            done, code = os.waitpid(pid, os.WNOHANG)
            if done:
                status = os.waitstatus_to_exitcode(code)
                continue
            if time.monotonic() > deadline:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
                _, code = os.waitpid(pid, 0)
                return shown, -1
            if data is None:
                time.sleep(0.05)
    while status is None:
        # The installer closed its channel to the screen and is still ending.
        done, code = os.waitpid(pid, os.WNOHANG)
        if done:
            status = os.waitstatus_to_exitcode(code)
        elif time.monotonic() > deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return shown, -1
        else:
            time.sleep(0.05)
    return shown, status


def write_counters(context, found):
    """Sectors written so far to super and the partitions beside it, as the kernel counts them:
    super's count grows with every write through a mapping of its partitions too."""
    if context.mode != 'device':
        return {}
    counts = {}
    for label in ('super', 'prism', 'optics'):
        if label in found:
            with contextlib.suppress(OSError, ValueError):
                counts[label] = device_written(found[label]['path'])
    return counts


def untouched(context, found, room, before, layout, counters, now):
    """Whether the installer left every partition in its reach as it found it: the watched
    partitions by their hashes, super's layout, the kernel's count of sectors written to super,
    prism and optics, and the mappings in the room and those the installer made itself."""
    if now != before or write_counters(context, found) != counters:
        return False
    if layout is not None and layout_hash(context, found) != layout:
        return False
    if room is not None:
        if room.written() or any(device['sectors_written'] for kernel, device in mapped_devices().items()
                                 if kernel not in room.mapped_before):
            return False
    return True


def run(context, args, progress):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Running the installer of a ZIP must be confirmed')
    with flash.operation_lock(context.lock_path, context.mode):
        require(not context.guard_path.exists(), 'INSTALL_PENDING', 'Finish the unfinished installation first')
        found = targets(context)
        context.check_offline(list(found.values()))
        ensure_mounted(context, args.volume, args.volume_token)
        with flash.source_file(context, args.volume, args.volume_token, args.path) as fd:
            # Its members' CRC were read at the review; the same SHA-256 means the same bytes.
            about = describe(fd, Path(args.path).name, check_members=False)
            require(about['sha256'] == args.expected_sha256, 'ZIP_CHANGED', 'The ZIP changed after it was reviewed')
            # The BOOT there is kept on cache first: the way back from whatever the installer writes.
            kept = saved_boot.keep_current_boot(context, lambda *a: None)
            before = hashes(context, found)
            guarded = protected_hashes(context)
            layout = layout_hash(context, found) if 'super' in found else None
            fitted, sizes = {}, {}
            if 'super' in found and context.mode == 'device':
                sizes = package_sizes(fd)
            if sizes:
                # Only for a ROM that writes logical partitions whole (2026-11-13 review: every ZIP,
                # a Magisk one too, failed on a super without readable metadata).
                _, sfd, opened = flash.open_super(context)
                try:
                    fitted = fit_plan(opened, sizes)
                finally:
                    os.close(sfd)
                if fitted:
                    apply_fit(context, fitted)
            # The layout the installer starts from, after the recovery's own fit; LAYOUT stays the
            # one found, so the result still names super when its layout changed.
            layout_run = layout_hash(context, found) if 'super' in found else None
            room = Room(context, found) if context.mode == 'device' else None
            if room:
                room.attach_logical()
            counters = write_counters(context, found)
            # 2026-11-13 review: the recovery used to write a full block-based ROM itself in place of
            # its script, and so left out what else the script does (dtbo, vbmeta, ...). The script
            # runs first. 2026-11-14 review: the recovery writes the images itself only when the
            # script stopped without changing anything; over a layout the script had changed, its
            # mappings (made before) pointed elsewhere, and what it had written was left unsaid.
            native = (room is not None or context.mode == 'fixture') and native_possible(fd, sizes)
            installer_code, native_error, native_ran, room_error, seen = None, None, False, None, None
            # The installer's channels, closed whatever happens (2026-11-14 review: an error before
            # the run left them open).
            channels = {}

            def close_channel(name):
                number = channels.pop(name, None)
                if number is not None:
                    os.close(number)
            try:
                read_fd, write_fd = os.pipe()
                channels.update(read=read_fd, write=write_fd)
                start_read, first = os.pipe2(os.O_CLOEXEC)
                channels.update(start_read=start_read, first=first)
                # Above the numbers the installer's channels take (9 for the screen, 0-2).
                start_write = fcntl.fcntl(first, fcntl.F_DUPFD_CLOEXEC, 10)
                channels['start_write'] = start_write
                close_channel('first')
                pid = os.fork()
                if pid == 0:
                    try:
                        os.close(read_fd)
                        os.close(start_read)
                        null = os.open(os.devnull, os.O_RDWR)
                        os.dup2(null, 0)
                        os.dup2(2, 1)  # what the installer prints goes to the helper's log, never into its JSON
                        if room:
                            room_child(room, fd, write_fd, about['installer'], about['installer_sha256'], start_write)
                        else:
                            os.setsid()
                            fixture_child(context, found, fd, write_fd, about['installer'], about['installer_sha256'], start_write)
                    except BaseException as error:  # noqa: BLE001 - told to the parent below
                        report_start(start_write, error)
                    finally:
                        os._exit(127)
                close_channel('write')
                close_channel('start_write')
                try:
                    shown, code = follow(read_fd, pid, lambda text, part: progress(text, {'label': 'zip', 'size_bytes': 1000}, int(part * 1000)),
                                         time.monotonic() + RUN_SECONDS)
                finally:
                    close_channel('read')
                ran, room_error, exec_error = started(*read_start(start_read), about['installer'])
                close_channel('start_read')
                installer_code = code if ran else None
                if exec_error is not None:
                    shown = list(shown) + ['update-binary: ' + exec_error['message'][:200]]  # as the installer's own lines
                if code != 0 and native and ran:
                    seen = hashes(context, found)
                    if untouched(context, found, room, before, layout_run, counters, seen):
                        native_ran = True
                        try:
                            written_natively, code = native_install(context, room, fd, sizes, found, progress)
                            # The last lines are what the result page shows: the recovery's own last.
                            shown = list(shown)[-8:] + written_natively + [NATIVE_STOPPED]
                        except Exception as error:  # noqa: BLE001 - reported below with the run's account
                            # Accounted for below like any other end of the run (2026-11-14 review:
                            # this skipped what changed, the mark on BOOT and the protected check).
                            native_error = error
                    else:
                        shown = list(shown) + [NATIVE_REFUSED]
            finally:
                for name in list(channels):
                    with contextlib.suppress(OSError):
                        close_channel(name)
                os.sync()
                written, left = unmap_after(room.mapped_before) if room else ([], [])
                if room:
                    written = list(written) + room.written()
                    room.detach()
        # The hashes just read serve when nothing was written since (2026-11-14 review: BOOT, dtbo and
        # every vbmeta were read twice in a row).
        after = seen if seen is not None and not native_ran else hashes(context, found)
        changed = sorted(label for label in after if after[label] != before.get(label))
        # What an installer did to super through the device mapper: the logical partitions it
        # wrote to, and a layout that is no longer the one it found.
        changed += ['super/' + name for name in sorted(set(written))]
        if layout is not None and layout_hash(context, found) != layout:
            changed.append('super')
        marked = None
        if 'boot' in changed:
            with contextlib.suppress(fs.FilesystemError, OSError):
                marked = saved_boot.keep_recovery(context, found['boot'])
        for label in changed:
            if label in after:
                with contextlib.suppress(Exception):
                    install.record_flash(context, label, marked['sha256'] if label == 'boot' and marked else after[label])
        result = {'zip': about['name'], 'sha256': about['sha256'], 'title': about['title'], 'what': about['what'], 'exit': code,
                  'installer_exit': installer_code, 'written_by_recovery': native_ran and native_error is None,
                  'changed': changed, 'log': shown[-40:], 'kept_boot': kept.get('kept') if kept else None,
                  'keeps_recovery': bool(marked), 'mappings_left': left,
                  # 2026-11-13, the owner, on stock and custom builds alike: Android starts with a
                  # BOOT changed by a kernel, Magisk or APatch ZIP only after its data is erased.
                  'android_boot_changed': bool(marked),
                  'super_fitted': {name: {'from': now, 'to': then} for name, (now, then) in fitted.items()}}
        now = protected_hashes(context)
        touched = sorted(label for label, digest in guarded.items() if now.get(label) != digest)
        require(not touched, 'PROTECTED_PARTITION_CHANGED',
                'The installer of this ZIP changed a partition it may not write: ' + ', '.join(touched), touched=touched, **result)
        if room_error is not None:
            # The installer never ran: the room's own reason, never the recovery's writing in its place.
            fail(room_error['code'], room_error['message'], **result, **({'reason': room_error['reason']} if 'reason' in room_error else {}))
        if native_error is not None:
            fail(getattr(native_error, 'code', None) or 'IO_ERROR', str(native_error),
                 **{**(getattr(native_error, 'details', None) or {}), **result})
        require(code == 0, 'ZIP_INSTALLER_FAILED' if code > 0 else 'ZIP_INSTALLER_STOPPED',
                'The installer of this ZIP ended with an error' if code > 0 else 'The installer of this ZIP did not end in time and was stopped',
                **result)
        return result


def ensure_mounted(context, volume, token):
    """The ZIP's volume, mounted read-only when it is not (2026-11-11: a ZIP sideloaded over adb is
    reviewed at once, from a volume the transfer may have left unmounted)."""
    found = context.fs.volume(volume, token)
    if not found['mounted'] and found.get('mountable'):
        context.fs.mount(volume, 'ro', True, token)


def inspect(context, args, progress):
    ensure_mounted(context, args.volume, args.volume_token)
    with flash.source_file(context, args.volume, args.volume_token, args.path) as fd:
        about = describe(fd, Path(args.path).name)
        sizes = package_sizes(fd)
    about['partitions'] = sorted(targets(context))
    about['super_fit'] = {}
    if sizes and context.mode == 'device':
        _, sfd, opened = flash.open_super(context)
        try:
            plan = fit_plan(opened, sizes)
        finally:
            os.close(sfd)
        about['super_fit'] = {name: {'from': now, 'to': then} for name, (now, then) in plan.items()}
        if plan:
            about['warnings'].append('super is laid out for this ROM first: ' + ', '.join(
                '%s %d -> %d MiB' % (name, now >> 20, then >> 20) for name, (now, then) in sorted(plan.items())))
    if sizes:
        with flash.source_file(context, args.volume, args.volume_token, args.path) as fd:
            if native_possible(fd, sizes):
                # The installer runs first, with its warning (2026-11-14 review: the review said the
                # images had been written, in the past, before anything ran).
                about['warnings'].append(NATIVE_NOTE)
    return about


def parser():
    result = argparse.ArgumentParser()
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--volume', required=True)
        command.add_argument('--volume-token', required=True)
        command.add_argument('--path', required=True)
        if name == 'run':
            command.add_argument('--expected-sha256', required=True)
            command.add_argument('--confirm', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    last = [None, 0.0]

    def progress(phase, target, done):
        now = time.monotonic()
        if phase != last[0] or now - last[1] >= 0.2:
            print(json.dumps({'event': 'progress', 'operation': 'zip-' + args.command, 'phase': phase, 'partition': target['label'],
                              'bytes_done': done, 'bytes_total': target['size_bytes']}, separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [phase, now]

    try:
        context = install.FixtureContext(args.fixture) if args.fixture else install.DeviceContext()
        result = inspect(context, args, progress) if args.command == 'inspect' else run(context, args, progress)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - every refusal reaches the page as JSON
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
