#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Partition a removable card, as TWRP's "Partition SD card" (2026-11-11).

    sdcard.py --device|--fixture ROOT list
    sdcard.py --device|--fixture ROOT apply --disk ID --expected-token TOKEN
              [--linux-mib N] [--swap-mib N] --confirm

The medium gets a new MBR table: a FAT32 partition for files first, then on request an ext4
partition for Linux and a swap partition. Everything on the medium is lost. `list` writes
nothing; `apply` writes only to the medium `list` showed, which the token binds: its node, its
size, the card's own identity and the table it had.

Only removable media are listed: the card in the slot (mmcblk1 of type SD) and USB drives. The
phone's own flash is never a target. A medium is refused while one of its partitions is mounted,
held by another block device, used as swap or exported over USB, and while it is write-protected.

A fixture names its media in sdcard-fixture.json. A regular file gets the real table and, as it
has no partition nodes for mkfs, the head of each filesystem. A loop device gets everything a
card gets, with the phone's own tools: that is how the whole path was run on the phone without
erasing a card (2026-11-11).
"""
from __future__ import annotations

import argparse
import contextlib
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
import tempfile
import time

import filesystem as fs
from lp_metadata import drop_cached  # the one way to drop what a node remembers
from filesystem import fail
from operation_lock import operation_lock

SECTOR = 512
ALIGN = 4 << 20            # Starts and sizes fall on 4 MiB, the allocation unit of SD cards.
MIN_FILES = 256 << 20
MBR_BYTES = (1 << 32) * SECTOR  # An MBR table addresses 2 TiB.
HEAD = 1 << 20             # Erased at both ends of the medium and at the head of each partition.
TYPES = {'files': 0x0c, 'linux': 0x83, 'swap': 0x82}
FILESYSTEMS = {'files': 'vfat', 'linux': 'ext4', 'swap': 'swap'}
BLKRRPART, BLKGETSIZE64 = 0x125f, 0x80081272
BUSYBOX = '/bin/busybox'
MKFS = {'files': [BUSYBOX, 'mkdosfs', '-F', '32', '-n', 'A22X'],
        'linux': ['/sbin/mkfs.ext4', '-F', '-q', '-L', 'linux'],
        'swap': [BUSYBOX, 'mkswap', '-L', 'swap']}
FIXTURE = 'sdcard-fixture.json'
GADGETS = Path('/sys/kernel/config/usb_gadget')


def require(condition, code, message, **details):
    if not condition:
        fail(code, message, **details)


def align_up(value):
    return (value + ALIGN - 1) // ALIGN * ALIGN


def layout(size, linux_mib=0, swap_mib=0):
    """The partitions for a medium of SIZE bytes, in the order they lie on it."""
    require(isinstance(linux_mib, int) and isinstance(swap_mib, int) and 0 <= linux_mib <= 1 << 21
            and 0 <= swap_mib <= 1 << 16, 'INVALID_SIZE', 'The sizes for Linux and swap are out of range')
    usable = min(size, MBR_BYTES) // ALIGN * ALIGN
    linux, swap = align_up(linux_mib << 20), align_up(swap_mib << 20)
    files = usable - ALIGN - linux - swap
    require(files >= MIN_FILES, 'CARD_TOO_SMALL', 'The card is too small for these partitions',
            needed_bytes=ALIGN + MIN_FILES + linux + swap, size_bytes=size)
    parts, at = [], ALIGN
    for kind, length in (('files', files), ('linux', linux), ('swap', swap)):
        if length:
            parts.append({'kind': kind, 'start': at, 'bytes': length})
            at += length
    return parts


def mbr(parts, signature):
    """Sector 0: the entries say where each partition lies in sectors (LBA); the old
    cylinder fields carry the value that means "see the LBA fields"."""
    sector = bytearray(SECTOR)
    sector[440:444] = signature
    for index, part in enumerate(parts):
        sector[446 + 16 * index:462 + 16 * index] = struct.pack(
            '<B3sB3sII', 0, b'\xfe\xff\xff', TYPES[part['kind']], b'\xfe\xff\xff',
            part['start'] // SECTOR, part['bytes'] // SECTOR)
    sector[510:512] = b'\x55\xaa'
    return bytes(sector)


def read_mbr(sector):
    """The primary partitions of an MBR, and 'gpt' when it only guards a GPT."""
    if len(sector) < SECTOR or sector[510:512] != b'\x55\xaa':
        return 'none', []
    rows, kind = [], 'mbr'
    for index in range(4):
        _flag, _chs, code, _end, start, count = struct.unpack_from('<B3sB3sII', sector, 446 + 16 * index)
        if code == 0xee:
            kind = 'gpt'
        elif code and count:
            rows.append({'number': index + 1, 'start': start * SECTOR, 'bytes': count * SECTOR})
    return kind, rows


def probe(fd, start, length):
    """The filesystem at START, by its own magic; swap has none that probe_magic knows."""
    data = os.pread(fd, min(8192, length), start)
    info = fs.probe_magic(data)
    if not info['fs_type'] and data[4086:4096] in (b'SWAPSPACE2', b'SWAP-SPACE'):
        return 'swap', ''
    return info['fs_type'], info.get('filesystem_label') or ''


def stand_in(kind):
    """What a fixture file gets in place of mkfs: the head by which the kind is read back."""
    head = bytearray(8192)
    if kind == 'files':
        struct.pack_into('<HBHB', head, 11, 512, 8, 32, 2)
        head[82:90] = b'FAT32   '
        head[67:71] = os.urandom(4)
        head[510:512] = b'\x55\xaa'
    elif kind == 'linux':
        struct.pack_into('<I', head, 1024 + 24, 2)
        struct.pack_into('<I', head, 1024 + 32, 32768)
        head[1080:1082] = b'\x53\xef'
        head[1024 + 104:1024 + 120] = os.urandom(16)
        head[1024 + 120:1024 + 125] = b'linux'
    else:
        head[4086:4096] = b'SWAPSPACE2'
    return bytes(head)


class Media:
    """What both contexts share: describing a medium and writing its new layout."""
    mode = 'device'
    sys_root = Path('/sys')
    dev_root = Path('/dev')

    def block(self, name, ident, title, kind, identity):
        """A whole-disk block device as the kernel shows it in sysfs."""
        real = (self.sys_root / 'class/block' / name).resolve()
        node = self.dev_root / name
        st = node.lstat()
        devno = (real / 'dev').read_text().strip()
        require(stat.S_ISBLK(st.st_mode) and devno == '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev)),
                'BLOCK_IDENTITY_CHANGED', 'A block node does not match the kernel', disk=ident)
        return {'id': ident, 'name': title, 'kind': kind, 'node': str(node), 'sysfs': real, 'devno': devno,
                'size': int((real / 'size').read_text()) * SECTOR, 'identity': identity,
                'sector': int((real / 'queue/logical_block_size').read_text()),
                'read_only': (real / 'ro').read_text().strip() == '1'}

    def disk(self, ident):
        for disk in self.disks():
            if disk['id'] == ident:
                return disk
        fail('DISK_MISSING', 'This card is no longer attached', disk=ident)

    def partitions(self, disk, fd):
        """(table, rows): what the kernel made of the table, or the table itself for a file."""
        table, rows = read_mbr(os.pread(fd, SECTOR, 0))
        if disk['kind'] == 'file':
            return table, [dict(row, devno=None, holders=False) for row in rows]
        found = []
        for child in sorted(disk['sysfs'].iterdir()):
            if not (child / 'partition').is_file():
                continue
            holders = (child / 'holders').is_dir() and any((child / 'holders').iterdir())
            found.append({'number': int((child / 'partition').read_text()), 'start': int((child / 'start').read_text()) * SECTOR,
                          'bytes': int((child / 'size').read_text()) * SECTOR, 'devno': (child / 'dev').read_text().strip(),
                          'name': child.name, 'holders': bool(holders)})
        return table, sorted(found, key=lambda row: row['number'])

    def busy(self, disk, rows):
        """Why the medium cannot be written now: (code, text), or (None, None)."""
        if disk['sector'] != SECTOR:
            return 'UNSUPPORTED_SECTOR', 'Only media with 512-byte sectors are partitioned here'
        if disk['read_only']:
            return 'CARD_READ_ONLY', 'The card is write-protected'
        if disk['size'] < ALIGN + MIN_FILES:
            return 'CARD_TOO_SMALL', 'The card is too small to partition'
        if disk['kind'] == 'file':
            return ('CARD_MOUNTED', 'Unmount the card in Files first') if disk.get('mounted') else (None, None)
        numbers = {disk['devno']} | {row['devno'] for row in rows}
        if any(mount['devno'] in numbers for mount in fs.all_namespace_mounts()):
            return 'CARD_MOUNTED', 'Unmount the card in Files first'
        holders = (disk['sysfs'] / 'holders').is_dir() and any((disk['sysfs'] / 'holders').iterdir())
        if holders or any(row['holders'] for row in rows):
            return 'CARD_HELD', 'Another block device uses this card'
        names = {disk['node']} | {str(self.dev_root / row['name']) for row in rows}
        try:
            swaps = Path('/proc/swaps').read_text().splitlines()[1:]
        except OSError:
            swaps = []
        if any(line.split()[0] in names for line in swaps if line.split()):
            return 'CARD_SWAP_ACTIVE', 'A swap partition of this card is in use'
        for lun in GADGETS.glob('*/functions/mass_storage.*/lun.*/file') if GADGETS.is_dir() else ():
            try:
                if lun.read_text().strip() in names:
                    return 'CARD_EXPORTED', 'Stop sharing the card over USB first'
            except OSError:
                continue
        return None, None

    def describe(self, disk):
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(disk['node'], flags)
        try:
            if disk['kind'] != 'file':
                require(struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0] == disk['size'],
                        'BLOCK_GEOMETRY_CHANGED', 'Kernel and block geometry disagree', disk=disk['id'])
                # mkfs wrote through the partitions, which keep their own cache: what this node
                # remembers of those sectors is dropped before they are read through it.
                with contextlib.suppress(OSError):
                    drop_cached(fd)
            sector0 = os.pread(fd, SECTOR, 0)
            table, rows = self.partitions(disk, fd)
            shown = []
            for row in rows:
                inside = row['start'] + row['bytes'] <= disk['size']
                kind, label = probe(fd, row['start'], row['bytes']) if inside else (None, '')
                shown.append({'number': row['number'], 'start_bytes': row['start'], 'size_bytes': row['bytes'],
                              'fs_type': kind or '', 'label': label})
        finally:
            os.close(fd)
        code, text = self.busy(disk, rows)
        token = fs.digest({'id': disk['id'], 'node': disk['node'], 'devno': disk.get('devno'), 'bytes': disk['size'],
                           'identity': hashlib.sha256(disk['identity']).hexdigest(),
                           'table': hashlib.sha256(sector0).hexdigest(),
                           'partitions': [[row['start'], row['bytes']] for row in rows]})
        return {'id': disk['id'], 'name': disk['name'], 'kind': disk['kind'], 'size_bytes': disk['size'], 'table': table,
                'partitions': shown, 'busy_code': code or '', 'busy': text or '', 'token': token,
                'align_bytes': ALIGN, 'min_files_bytes': MIN_FILES, 'usable_bytes': min(disk['size'], MBR_BYTES) // ALIGN * ALIGN}

    def write_table(self, disk, parts):
        """Erase what would be found again and write the new table; a block device rereads it."""
        block = disk['kind'] != 'file'
        fd = os.open(disk['node'], os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_EXCL if block else 0))
        try:
            st = os.fstat(fd)
            if block:
                require(stat.S_ISBLK(st.st_mode) and disk['devno'] == '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev))
                        and struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0] == disk['size'],
                        'BLOCK_IDENTITY_CHANGED', 'The card changed while it was opened', disk=disk['id'])
            else:
                require(stat.S_ISREG(st.st_mode) and st.st_size == disk['size'], 'BLOCK_IDENTITY_CHANGED',
                        'The fixture medium changed while it was opened', disk=disk['id'])
            zero = bytes(HEAD)
            # An old GPT lies at both ends; an old filesystem begins where a new partition does, or
            # in the gap before the first one, where old tables put their first partition.
            for offset in list(range(0, ALIGN, HEAD)) + [(disk['size'] - HEAD) // SECTOR * SECTOR] + [part['start'] for part in parts]:
                os.pwrite(fd, zero[:min(HEAD, disk['size'] - offset)], offset)
            os.pwrite(fd, mbr(parts, os.urandom(4)), 0)
            os.fsync(fd)
            if block:
                for attempt in range(40):
                    try:
                        fcntl.ioctl(fd, BLKRRPART)
                        break
                    except OSError as error:
                        if error.errno != errno.EBUSY or attempt == 39:
                            fail('TABLE_NOT_READ', 'The kernel could not read the new partition table', errno=error.errno)
                        time.sleep(.25)
        finally:
            os.close(fd)

    def nodes(self, disk, parts, folder):
        """A private node for each new partition, once the kernel shows it where the table put it."""
        deadline = time.monotonic() + 15
        while True:
            fd = os.open(disk['node'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                _table, rows = self.partitions(disk, fd)
            finally:
                os.close(fd)
            if [(row['start'], row['bytes']) for row in rows] == [(part['start'], part['bytes']) for part in parts]:
                break
            require(time.monotonic() < deadline, 'TABLE_NOT_READ', 'The kernel does not show the new partitions',
                    seen=[[row['start'], row['bytes']] for row in rows])
            time.sleep(.2)
        paths = []
        for row in rows:
            major, minor = (int(value) for value in row['devno'].split(':'))
            path = os.path.join(folder, 'p%d' % row['number'])
            os.mknod(path, 0o600 | stat.S_IFBLK, os.makedev(major, minor))
            paths.append(path)
        return paths

    def make_filesystems(self, disk, parts, progress):
        if disk['kind'] == 'file':
            fd = os.open(disk['node'], os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                for index, part in enumerate(parts):
                    progress('card-' + part['kind'], index + 1, len(parts) + 2)
                    os.pwrite(fd, stand_in(part['kind']), part['start'])
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        for tool in {MKFS[part['kind']][0] for part in parts}:
            require(os.access(tool, os.X_OK), 'TOOL_MISSING', 'A tool that makes filesystems is missing', tool=tool)
        # Under /dev, where device nodes work whatever the other mounts allow.
        folder = tempfile.mkdtemp(prefix='.a22x-card-', dir=str(self.dev_root))
        try:
            os.chmod(folder, 0o700)
            for index, (part, node) in enumerate(zip(parts, self.nodes(disk, parts, folder))):
                progress('card-' + part['kind'], index + 1, len(parts) + 2)
                done = subprocess.run([*MKFS[part['kind']], node], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, errors='replace', timeout=1800)
                require(done.returncode == 0, 'FORMAT_FAILED', 'The filesystem could not be created',
                        kind=part['kind'], output=done.stdout[-400:])
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class Device(Media):
    def __init__(self):
        self.fs = fs.DeviceContext()
        self.lock_path = self.fs.lock_path

    def disks(self):
        """The card in the slot and USB drives. The phone's own flash is mmcblk0 and has no name here."""
        found = []
        for entry in sorted((self.sys_root / 'block').iterdir()):
            name = entry.name
            usb_name = re.fullmatch(r'sd[a-z]{1,2}', name) is not None
            if not usb_name and name != 'mmcblk1':
                continue
            real = entry.resolve()
            usb = usb_name and '/usb' in str(real)
            try:
                if usb_name and not usb:
                    continue
                if not usb and (real / 'device/type').read_text().strip() != 'SD':
                    continue
                identity = fs.medium_identity(real, usb)
                if usb:
                    title = ' '.join(part for part in ((real / 'device/vendor').read_text().strip(),
                                                       (real / 'device/model').read_text().strip()) if part)
                else:
                    title = (real / 'device/name').read_text().strip()
                found.append(self.block(name, 'usb-' + name if usb else 'sdcard', title, 'usb' if usb else 'sd', identity))
            except OSError:
                continue
        return found


class Fixture(Media):
    mode = 'fixture'

    def __init__(self, root):
        self.root = Path(root).resolve()
        config = json.loads((self.root / FIXTURE).read_text())
        require(isinstance(config, dict) and config.get('schema') == 'a22x.sdcard.fixture.v1'
                and isinstance(config.get('disks'), list), 'INVALID_FIXTURE', 'The fixture names no media')
        self.rows = config['disks']
        self.lock_path = self.root / 'recovery.lock'

    def disks(self):
        found = []
        for row in self.rows:
            require(isinstance(row, dict) and re.fullmatch(r'[a-z][a-z0-9-]{0,23}', str(row.get('id', ''))),
                    'INVALID_FIXTURE', 'A fixture medium has no usable id')
            path = Path(row['path'])
            path = path if path.is_absolute() else self.root / path
            info = os.stat(path, follow_symlinks=False)
            title = str(row.get('name') or row['id'])
            if stat.S_ISBLK(info.st_mode):
                # Only a loop device stands in for a card: a file the tester made, never a disk of the phone.
                require(re.fullmatch(r'loop[0-9]+', path.name) and path.parent == self.dev_root, 'INVALID_FIXTURE',
                        'Only a loop device can stand in for a card')
                found.append(self.block(path.name, row['id'], title, 'loop', b'fixture:' + row['id'].encode()))
            else:
                require(stat.S_ISREG(info.st_mode), 'INVALID_FIXTURE', 'A fixture medium is a file or a loop device')
                found.append({'id': row['id'], 'name': title, 'kind': 'file', 'node': str(path), 'sysfs': None, 'devno': None,
                              'size': info.st_size, 'identity': b'fixture:' + row['id'].encode(), 'sector': SECTOR,
                              'read_only': bool(row.get('read_only')), 'mounted': bool(row.get('mounted'))})
        return found


def listing(context):
    return {'disks': [context.describe(disk) for disk in context.disks()]}


def apply(context, args, progress):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Partitioning a card must be confirmed')
    with operation_lock(context.lock_path, context.mode):
        disk = context.disk(args.disk)
        before = context.describe(disk)
        require(before['token'] == args.expected_token, 'DISK_CHANGED', 'The card changed after it was shown')
        require(not before['busy_code'], before['busy_code'] or 'CARD_BUSY', before['busy'])
        parts = layout(disk['size'], args.linux_mib, args.swap_mib)
        progress('card-table', 0, len(parts) + 2)
        context.write_table(disk, parts)
        context.make_filesystems(disk, parts, progress)
        os.sync()
        progress('card-check', len(parts) + 1, len(parts) + 2)
        after = context.describe(context.disk(args.disk))
        made = [(row['start_bytes'], row['size_bytes'], row['fs_type']) for row in after['partitions']]
        require(after['table'] == 'mbr' and made == [(part['start'], part['bytes'], FILESYSTEMS[part['kind']]) for part in parts],
                'CARD_NOT_AS_WRITTEN', 'The card does not show the partitions that were written', seen=made)
        progress('card-check', len(parts) + 2, len(parts) + 2)
        return {'disk': after, 'created': [{'kind': part['kind'], 'size_bytes': part['bytes']} for part in parts]}


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    command = commands.add_parser('apply')
    command.add_argument('--disk', required=True)
    command.add_argument('--expected-token', required=True)
    command.add_argument('--linux-mib', type=int, default=0)
    command.add_argument('--swap-mib', type=int, default=0)
    command.add_argument('--confirm', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)

    def progress(phase, done, total):
        print(json.dumps({'event': 'progress', 'operation': args.command, 'phase': phase,
                          'bytes_done': done, 'bytes_total': total}, separators=(',', ':')), file=sys.stderr, flush=True)

    try:
        context = Fixture(args.fixture) if args.fixture else Device()
        result = listing(context) if args.command == 'list' else apply(context, args, progress)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, fs.FilesystemError) as error:
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
