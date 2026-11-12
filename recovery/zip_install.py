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
  - a busybox shell at /sbin/sh with getprop, as installers expect in a recovery.

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
import lp_metadata as lp
import saved_boot

INSTALLER = 'META-INF/com/google/android/update-binary'
INSTALLER_LIMIT = 32 << 20
# What an installer may write. Never the bootloader chain, the modem, calibration, this recovery or Android's data.
WRITABLE = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'super', 'prism', 'optics')
WATCHED = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor')
LOGICAL = ('system', 'vendor', 'product', 'odm')
ROOM = Path('/run/a22x-recovery/zip-room')
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
for f in $files; do
    [ -f "$f" ] || continue
    value=$(sed -n "s/^$1=//p" "$f" | head -n 1)
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


def mapped_devices():
    """The device-mapper devices the kernel has now: {'dm-0': {...}}."""
    found = {}
    for entry in SYS_BLOCK.glob('dm-*'):
        try:
            found[entry.name] = {'name': (entry / 'dm/name').read_text().strip(), 'uuid': (entry / 'dm/uuid').read_text().strip(),
                                 'devno': (entry / 'dev').read_text().strip(),
                                 'slaves': sorted((s / 'dev').read_text().strip() for s in (entry / 'slaves').iterdir()),
                                 'sectors_written': int(((entry / 'stat').read_text().split() + ['0'] * 7)[6])}
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


def describe(fd, name):
    """What the ZIP at FD is: its SHA-256, its installer (a script or a program) and what it says of itself."""
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
            with archive.open(INSTALLER) as member:
                head = member.read(64)
            module = {}
            if 'module.prop' in names:
                for line in archive.read('module.prop').decode('utf-8', 'replace').splitlines():
                    key, _, value = line.partition('=')
                    module[key.strip()] = value.strip()[:80]
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
        """super's logical partitions as loop devices over super, so an installer can mount them."""
        for name, (offset, _) in logical_extents(self.context).items():
            done = self.run('losetup', '-f', check=False)
            device = done.stdout.strip()
            if done.returncode or not device.startswith('/dev/loop'):
                continue
            if self.run('losetup', '-o', str(offset), device, self.found['super']['path'], check=False).returncode == 0:
                self.loops[name] = device

    def detach(self):
        for device in self.loops.values():
            self.run('losetup', '-d', device, check=False)
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
            # ext4 without its journal replayed: reading a property must not write to super.
            for kind, options in (('erofs', 'ro'), ('ext4', 'ro,noload'), ('f2fs', 'ro')):
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
                     'system_root', 'vendor', 'product', 'odm', 'data', 'cache', 'sdcard', 'mnt', 'system/bin'):
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


def room_child(room, zip_fd, out_fd, kind):
    """The process the installer runs in: new spaces, the room, then update-binary as TWRP calls it."""
    os.setsid()
    os.unshare(os.CLONE_NEWNS | os.CLONE_NEWPID | os.CLONE_NEWIPC | os.CLONE_NEWUTS)
    pid = os.fork()  # the first process of the new process space
    if pid:
        # This process shares the room's mounts and stays outside its root: it stands in for ueventd.
        st = os.stat(room.found['super']['path'])
        mapper = Mapper(room.root / 'dev/block', room.mapped_before, '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev)))
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            with contextlib.suppress(OSError, ValueError):
                mapper.tend()
            time.sleep(.03)
        os._exit(os.waitstatus_to_exitcode(status) & 0xff if os.waitstatus_to_exitcode(status) >= 0 else 137)
    try:
        room.build()
        root = room.root
        with open(root / 'tmp/install.zip', 'wb') as copy:
            at = 0
            while True:
                data = os.pread(zip_fd, 4 << 20, at)
                if not data:
                    break
                copy.write(data)
                at += len(data)
        with zipfile.ZipFile(root / 'tmp/install.zip') as archive, open(root / 'tmp/update-binary', 'wb') as out:
            out.write(archive.read(INSTALLER))
        (root / 'tmp/update-binary').chmod(0o755)
        os.dup2(out_fd, 9)
        os.set_inheritable(9, True)
        os.chroot(root)
        os.chdir('/tmp')
        env = {'PATH': '/sbin:/system/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp', 'ANDROID_ROOT': '/system', 'ANDROID_DATA': '/data',
               'EXTERNAL_STORAGE': '/sdcard', 'SHELL': '/sbin/sh', 'ASH_STANDALONE': '1'}
        command = ['/sbin/sh', '/tmp/update-binary'] if kind == 'script' else ['/tmp/update-binary']
        os.execve(command[0], command + ['3', '9', '/tmp/install.zip'], env)
    except BaseException as error:  # noqa: BLE001 - reported to the parent through the exit status and stderr
        sys.stderr.write('room: %s\n' % error)
        os._exit(126)


def fixture_child(context, found, zip_fd, out_fd, kind):
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
    os.dup2(out_fd, 9)
    os.set_inheritable(9, True)
    os.chdir(root / 'tmp')
    require(kind == 'script', 'ZIP_INSTALLER_INVALID', 'Fixtures run script installers only')
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


def run(context, args, progress):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Running the installer of a ZIP must be confirmed')
    with flash.operation_lock(context.lock_path, context.mode):
        require(not context.guard_path.exists(), 'INSTALL_PENDING', 'Finish the unfinished installation first')
        found = targets(context)
        context.check_offline(list(found.values()))
        ensure_mounted(context, args.volume, args.volume_token)
        with flash.source_file(context, args.volume, args.volume_token, args.path) as fd:
            about = describe(fd, Path(args.path).name)
            require(about['sha256'] == args.expected_sha256, 'ZIP_CHANGED', 'The ZIP changed after it was reviewed')
            # The BOOT there is kept on cache first: the way back from whatever the installer writes.
            kept = saved_boot.keep_current_boot(context, lambda *a: None)
            before = hashes(context, found)
            layout = layout_hash(context, found) if 'super' in found else None
            read_fd, write_fd = os.pipe()
            room = Room(context, found) if context.mode == 'device' else None
            if room:
                room.attach_logical()
            try:
                pid = os.fork()
                if pid == 0:
                    try:
                        os.close(read_fd)
                        null = os.open(os.devnull, os.O_RDWR)
                        os.dup2(null, 0)
                        os.dup2(2, 1)  # what the installer prints goes to the helper's log, never into its JSON
                        if room:
                            room_child(room, fd, write_fd, about['installer'])
                        else:
                            os.setsid()
                            fixture_child(context, found, fd, write_fd, about['installer'])
                    finally:
                        os._exit(127)
                os.close(write_fd)
                try:
                    shown, code = follow(read_fd, pid, lambda text, part: progress(text, {'label': 'zip', 'size_bytes': 1000}, int(part * 1000)),
                                         time.monotonic() + RUN_SECONDS)
                finally:
                    os.close(read_fd)
            finally:
                os.sync()
                written, left = unmap_after(room.mapped_before) if room else ([], [])
                if room:
                    room.detach()
        after = hashes(context, found)
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
                  'changed': changed, 'log': shown[-40:], 'kept_boot': kept.get('kept') if kept else None,
                  'keeps_recovery': bool(marked), 'mappings_left': left}
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
    about['partitions'] = sorted(targets(context))
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
