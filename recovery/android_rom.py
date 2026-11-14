#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Android ROM packages for this phone (2026-11-10): what a package holds, and its images.

Nothing from a package is ever executed: update-binary and updater-script are not run, as in
the stock installer. A package is read declaratively, as one of:

  payload  a ZIP with payload.bin, update_engine's full OTA (operations REPLACE, REPLACE_BZ,
           REPLACE_XZ, ZERO, DISCARD); an incremental OTA needs the old partitions and is refused
  block    a block-based OTA: *.new.dat (or .new.dat.br) with *.transfer.list per partition and
           dynamic_partitions_op_list for super
  images   a ZIP of partition images (boot.img, system.img, vendor.img...)
  gsi      one system image: raw, sparse or xz, or a ZIP holding one system image

The partitions of the bootloader chain (preloader, LK, TEE and the firmware they load) and the
modem are refused here: this recovery does not write them. The recovery partition is kept: a
ROM's own recovery would replace this one.

    android_rom.py inspect PACKAGE
    android_rom.py extract PACKAGE --partition NAME --out FILE
"""
import bz2
import contextlib
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import struct
import sys
import zipfile

from lp_metadata import drop_cached

# update_engine's payload: "CrAU", version 2.
PAYLOAD_MAGIC = b'CrAU'
SPARSE_MAGIC = 0xED26FF3A
# InstallOperation types (update_metadata.proto).
REPLACE, REPLACE_BZ, MOVE, BSDIFF, SOURCE_COPY, SOURCE_BSDIFF, ZERO, DISCARD, REPLACE_XZ = 0, 1, 2, 3, 4, 5, 6, 7, 8
FULL_OPERATIONS = {REPLACE: 'replace', REPLACE_BZ: 'replace-bz2', REPLACE_XZ: 'replace-xz', ZERO: 'zero', DISCARD: 'discard'}
# Partitions this recovery writes from a ROM: outside super, and the logical ones inside it.
PHYSICAL = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor')
# MediaTek's boot chain and firmware it loads, the modem, and Samsung's own partitions.
REFUSED = re.compile(r'^(preloader.*|lk\d*|tee\d*|bl\d*|bootloader|gz\d*|sspm.*|scp\d*|spmfw|mcupm.*|dpm.*|audio_dsp|'
                     r'pi_img|vcp\d*|cam_vpu\d*|md\d*img|modem|radio|nvram|nvdata|nvcfg|protect.*|persist|seccfg|'
                     r'efuse|sec\d*|uh|up_param|param|steady|keydata|keyrefuge|otp|frp|misc|metadata|cache|userdata|'
                     r'prism|optics|recovery)$')
REFUSED_WHY = {'recovery': 'kept: a ROM\'s own recovery would replace this one',
               'userdata': 'formatted for Android by its own step, never written from a ROM',
               'cache': 'wiped by its own step, never written from a ROM',
               'prism': 'Samsung\'s regional data stays as it is',
               'optics': 'Samsung\'s carrier data stays as it is'}
DEVICE_NAMES = ('a22x', 'a22xxx', 'SM-A226B', 'SM-A226BR', 'SM-A226BN')
MAX_MANIFEST = 32 << 20


class RomError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def require(condition, code, message, **details):
    if not condition:
        raise RomError(code, message, **details)


# ---------------------------------------------------------------------------- protobuf, read only

def varint(data, at):
    value, shift = 0, 0
    while True:
        require(at < len(data) and shift < 70, 'INVALID_PAYLOAD', 'The payload manifest is damaged')
        byte = data[at]
        at += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def message(data):
    """FIELD → list of values: integers, or bytes for length-delimited fields."""
    fields, at = {}, 0
    while at < len(data):
        key, at = varint(data, at)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, at = varint(data, at)
        elif wire == 1:
            require(at + 8 <= len(data), 'INVALID_PAYLOAD', 'The payload manifest is damaged')
            value, at = struct.unpack_from('<Q', data, at)[0], at + 8
        elif wire == 2:
            length, at = varint(data, at)
            require(at + length <= len(data), 'INVALID_PAYLOAD', 'The payload manifest is damaged')
            value, at = bytes(data[at:at + length]), at + length
        elif wire == 5:
            require(at + 4 <= len(data), 'INVALID_PAYLOAD', 'The payload manifest is damaged')
            value, at = struct.unpack_from('<I', data, at)[0], at + 4
        else:
            raise RomError('INVALID_PAYLOAD', 'The payload manifest is damaged')
        fields.setdefault(number, []).append(value)
    return fields


def first(fields, number, default=None):
    values = fields.get(number)
    return values[0] if values else default


# ---------------------------------------------------------------------------- payload.bin

class Payload:
    """payload.bin inside a ZIP (stored, not compressed: update_engine reads it in place)."""

    def __init__(self, archive, name='payload.bin'):
        self.archive, self.name = archive, name
        info = archive.getinfo(name)
        require(info.compress_type == zipfile.ZIP_STORED, 'PAYLOAD_COMPRESSED', 'payload.bin must be stored without compression')
        with archive.open(name) as f:
            head = f.read(24)
            require(len(head) == 24 and head[:4] == PAYLOAD_MAGIC, 'INVALID_PAYLOAD', 'This is not an Android OTA payload')
            version, manifest_size = struct.unpack_from('>QQ', head, 4)
            require(version == 2, 'PAYLOAD_VERSION', 'Only payload version 2 is read', version=version)
            signature_size = struct.unpack_from('>I', head, 20)[0]
            require(0 < manifest_size <= MAX_MANIFEST, 'INVALID_PAYLOAD', 'The payload manifest is too large or empty')
            manifest = f.read(manifest_size)
            require(len(manifest) == manifest_size, 'INVALID_PAYLOAD', 'The payload ends inside its manifest')
        self.data_start = 24 + manifest_size + signature_size
        self.manifest = message(manifest)
        self.block_size = first(self.manifest, 3, 4096)
        # update_metadata.proto, DeltaArchiveManifest: minor_version 12, partitions 13,
        # dynamic_partition_metadata 15, partial_update 16 (2026-11-12 review: 15 and 16 were read).
        self.minor = first(self.manifest, 12, 0)
        self.partitions = []
        for raw in self.manifest.get(13, []):
            part = message(raw)
            name = first(part, 1, b'').decode('ascii', 'replace')
            new_info = message(first(part, 7, b''))
            old_info = first(part, 6)
            operations = [message(op) for op in part.get(8, [])]
            self.partitions.append({'name': name, 'size': first(new_info, 1, 0), 'sha256': first(new_info, 2, b'').hex(),
                                    'incremental': old_info is not None,
                                    'operations': operations})
        self.groups = []
        dynamic = first(self.manifest, 15)
        if isinstance(dynamic, bytes) and dynamic:
            for raw in message(dynamic).get(1, []):
                group = message(raw)
                self.groups.append({'name': first(group, 1, b'').decode('ascii', 'replace'), 'size': first(group, 2, 0),
                                    'partitions': [n.decode('ascii', 'replace') for n in group.get(3, [])]})

    def full(self, part):
        """Whether PART is written by full operations only (no old content needed)."""
        return not part['incremental'] and all(first(op, 1, 0) in FULL_OPERATIONS for op in part['operations'])

    def extract(self, part, out):
        """Write PART's new content into the open file OUT, checking its SHA-256."""
        require(self.full(part), 'INCREMENTAL_PAYLOAD', 'This OTA updates an installed system; a full ROM is needed',
                partition=part['name'])
        size, block = part['size'], self.block_size
        out.truncate(size)
        with self.archive.open(self.name) as f:
            for op in part['operations']:
                kind = first(op, 1, 0)
                extents = [message(raw) for raw in op.get(6, [])]
                spans = [(first(e, 1, 0) * block, first(e, 2, 0) * block) for e in extents]
                if kind in (ZERO, DISCARD):
                    for start, length in spans:
                        require(start + length <= size, 'INVALID_PAYLOAD', 'An operation writes past its partition')
                        out.seek(start)
                        out.write(bytes(length))
                    continue
                f.seek(self.data_start + first(op, 2, 0))
                data = f.read(first(op, 3, 0))
                require(len(data) == first(op, 3, 0), 'INVALID_PAYLOAD', 'The payload ends inside its data')
                expected = first(op, 8)
                if expected:
                    require(hashlib.sha256(data).digest() == expected, 'PAYLOAD_DAMAGED', 'The payload data is damaged',
                            partition=part['name'])
                if kind == REPLACE_XZ:
                    data = lzma.decompress(data)
                elif kind == REPLACE_BZ:
                    data = bz2.decompress(data)
                # The unpacked data fills its blocks exactly: anything shorter or longer is a damaged
                # or foreign payload, never a partition written in part.
                require(len(data) == sum(length for _, length in spans), 'PAYLOAD_DAMAGED',
                        'An operation of the payload does not fill its blocks', partition=part['name'])
                at = 0
                for start, length in spans:
                    require(start + length <= size, 'INVALID_PAYLOAD', 'An operation writes past its partition')
                    out.seek(start)
                    out.write(data[at:at + length])
                    at += length
        out.flush()
        out.seek(0)
        digest = hashlib.sha256()
        while True:
            chunk = out.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
        if part['sha256']:
            require(digest.hexdigest() == part['sha256'], 'PAYLOAD_DAMAGED', 'The extracted partition does not match the payload',
                    partition=part['name'])
        return digest.hexdigest()


# ---------------------------------------------------------------------------- what a package is

def metadata(archive):
    """META-INF/com/android/metadata: key=value lines (device, version, OTA type)."""
    try:
        text = archive.read('META-INF/com/android/metadata').decode('utf-8', 'replace')
    except KeyError:
        return {}
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep:
            values[key.strip()] = value.strip()
    return values


def device_check(meta):
    """True for this phone, False for another one, None when the package does not say."""
    devices = [d for d in re.split(r'[|,\s]+', meta.get('pre-device', '')) if d]
    if not devices:
        return None
    return any(d in DEVICE_NAMES for d in devices)


def classify(name, logical):
    """Where a ROM's partition goes: physical, logical (inside super), or refused, with why."""
    if REFUSED.match(name):
        return 'refused', REFUSED_WHY.get(name, 'part of the bootloader chain or the modem: not written by this recovery')
    if name in PHYSICAL:
        return 'physical', None
    if name in logical or name in ('system', 'system_ext', 'product', 'vendor', 'odm', 'vendor_dlkm', 'odm_dlkm', 'system_dlkm'):
        return 'logical', None if name in logical else 'not in this phone\'s super yet: it is created there'
    return 'refused', 'unknown to this phone'


IMAGE_NAME = re.compile(r'^(?:.*/)?([A-Za-z0-9_.+-]+)\.img$')


def inspect(path, logical=('system', 'vendor', 'product', 'odm')):
    """The package at PATH: its format, what it says about itself, and each partition's fate."""
    path = Path(path)
    require(path.is_file(), 'PACKAGE_MISSING', 'The package file is not there')
    with open(path, 'rb') as f:
        head = f.read(8)
    result = {'file': path.name, 'size': path.stat().st_size, 'metadata': {}, 'device_ok': None, 'partitions': [],
              'groups': [], 'warnings': []}
    if head[:4] == b'PK\x03\x04':
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            meta = metadata(archive)
            result['metadata'] = {k: meta[k] for k in ('pre-device', 'post-build', 'post-sdk-level', 'ota-type', 'post-timestamp')
                                  if k in meta}
            result['device_ok'] = device_check(meta)
            if 'payload.bin' in names:
                payload = Payload(archive)
                result['format'] = 'payload'
                result['groups'] = payload.groups
                for part in payload.partitions:
                    where, why = classify(part['name'], logical)
                    if where != 'refused' and not payload.full(part):
                        where, why = 'refused', 'an incremental update: a full ROM is needed'
                    result['partitions'].append({'name': part['name'], 'size': part['size'], 'target': where, 'reason': why})
            elif any(n.endswith('.transfer.list') for n in names):
                result['format'] = 'block'
                for name in sorted(n for n in names if n.endswith('.transfer.list')):
                    part = name.rsplit('/', 1)[-1][:-len('.transfer.list')]
                    data = next((n for n in (part + '.new.dat', part + '.new.dat.br') if n in names), None)
                    where, why = classify(part, logical)
                    if where != 'refused' and data and data.endswith('.br') and not os.path.exists(BROTLI):
                        where, why = 'refused', 'compressed with Brotli, which this recovery cannot read yet'
                    if where != 'refused' and not data:
                        where, why = 'refused', 'its data file is missing'
                    result['partitions'].append({'name': part, 'size': transfer_size(archive.read(name)), 'target': where,
                                                 'reason': why})
                for image in names:
                    match = IMAGE_NAME.match(image)
                    if match and not any(p['name'] == match.group(1) for p in result['partitions']):
                        where, why = classify(match.group(1), logical)
                        result['partitions'].append({'name': match.group(1), 'size': archive.getinfo(image).file_size,
                                                     'target': where, 'reason': why})
                if 'dynamic_partitions_op_list' in names:
                    result['groups'] = op_list_groups(archive.read('dynamic_partitions_op_list').decode('utf-8', 'replace'))
            else:
                images = [(IMAGE_NAME.match(n).group(1), n) for n in names if IMAGE_NAME.match(n)]
                require(images, 'UNKNOWN_PACKAGE', 'This ZIP holds no Android ROM: no payload.bin, transfer lists or images')
                # A GSI comes as one system image, named as its maker likes (system-td-arm64-ab-vanilla.img).
                if len(images) == 1 and images[0][0].lower().startswith(('system', 'aosp', 'lineage', 'gsi')):
                    result['format'] = 'gsi'
                    images = [('system', images[0][1])]
                else:
                    result['format'] = 'images'
                for part, name in images:
                    where, why = classify(part, logical)
                    result['partitions'].append({'name': part, 'size': archive.getinfo(name).file_size, 'target': where,
                                                 'reason': why, 'file': name})
            if any(n.startswith('META-INF/com/google/android/update-binary') for n in names):
                result['warnings'].append('The package carries an update-binary; it is not run here, the package is read as data')
    else:
        # One image: a GSI's system.img, raw, sparse or xz.
        kind = 'xz' if head[:6] == b'\xfd7zXZ\x00' else ('sparse' if struct.unpack_from('<I', head, 0)[0] == SPARSE_MAGIC else 'raw')
        result.update(format='gsi', compression=kind)
        result['partitions'].append({'name': 'system', 'size': result['size'], 'target': 'logical', 'reason': None})
    if result['device_ok'] is False:
        result['warnings'].append('The package is made for another phone')
    result['logical_bytes'] = sum(p['size'] for p in result['partitions'] if p['target'] == 'logical')
    return result


def transfer_size(data):
    """The size of a partition from its block-based OTA transfer list (4096-byte blocks)."""
    lines = data.decode('utf-8', 'replace').splitlines()
    blocks = 0
    for line in lines[4:]:
        command, _, ranges = line.partition(' ')
        if command in ('new', 'zero', 'erase'):
            numbers = [int(n) for n in ranges.split(',')]
            for start, end in zip(numbers[1::2], numbers[2::2]):
                blocks = max(blocks, end)
    return blocks * 4096


BROTLI = os.environ.get('A22X_BROTLI', '/bin/brotli-dec')
BLOCK = 4096


def transfer_commands(data):
    """A full block-based OTA's transfer list: [(command, [(start, end), ...])] in 4096-byte blocks.
    Only new, zero and erase: a list that moves or patches old blocks is an incremental update."""
    lines = data.decode('utf-8', 'replace').splitlines()
    require(len(lines) >= 4 and lines[0].strip() in ('1', '2', '3', '4'), 'TRANSFER_LIST_INVALID', 'The transfer list of this ROM is not understood')
    commands = []
    for line in lines[(2 if lines[0].strip() == '1' else 4):]:
        if not line.strip():
            continue
        command, _, ranges = line.partition(' ')
        require(command in ('new', 'zero', 'erase'), 'TRANSFER_LIST_INCREMENTAL',
                'This ROM is an incremental update: a full ROM is needed', command=command[:16])
        try:
            numbers = [int(n) for n in ranges.split(',')]
        except ValueError:
            numbers = []
        require(len(numbers) >= 3 and numbers[0] == len(numbers) - 1 and numbers[0] % 2 == 0, 'TRANSFER_LIST_INVALID',
                'The transfer list of this ROM is not understood')
        pairs = list(zip(numbers[1::2], numbers[2::2]))
        require(all(0 <= a < b for a, b in pairs), 'TRANSFER_LIST_INVALID', 'The transfer list of this ROM is not understood')
        commands.append((command, pairs))
    return commands


def block_stream(archive, name):
    """The bytes of NAME.new.dat in ARCHIVE; a .br member goes through the Brotli decoder. Returns
    (read(n), finish()); finish raises when the decoder did not end well."""
    member = archive.open(name)
    if not name.endswith('.br'):
        return member.read, member.close
    import subprocess
    import threading
    process = subprocess.Popen([BROTLI], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def feed():
        try:
            while True:
                data = member.read(1 << 20)
                if not data:
                    break
                process.stdin.write(data)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                process.stdin.close()
    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()

    def finish():
        if process.poll() is None:
            process.kill()
        code = process.wait()
        process.stdout.close()
        feeder.join(10)
        member.close()
        require(code == 0, 'ROM_DATA_DAMAGED', 'The compressed image inside this ROM is damaged', member=name)
    return process.stdout.read, finish


def write_block_image(archive, part, fd, size, progress=None):
    """Partition PART of a full block-based OTA written to the block device FD of SIZE bytes (2026-11-13,
    a One UI port whose own updater wrote nothing in the installer's room): the new blocks from
    PART.new.dat[.br] go where the transfer list says, zero ranges are zeroed, and everything written
    is read back. Returns {'blocks', 'sha256'} of the new data."""
    names = set(archive.namelist())
    data_name = next((n for n in (part + '.new.dat.br', part + '.new.dat') if n in names), None)
    require(data_name and part + '.transfer.list' in names, 'ROM_PART_MISSING', 'The ROM has no image of this partition', partition=part)
    commands = transfer_commands(archive.read(part + '.transfer.list'))
    top = max(end for _, pairs in commands for _, end in pairs)
    require(top * BLOCK <= size, 'ROM_PART_TOO_LARGE', 'The image is larger than the partition', partition=part,
            needed=top * BLOCK, size=size)
    total = sum(end - start for command, pairs in commands if command == 'new' for start, end in pairs)
    read, finish = block_stream(archive, data_name)
    digest, done, zero = hashlib.sha256(), 0, bytes(1 << 20)
    try:
        for command, pairs in commands:
            for start, end in pairs:
                at, stop = start * BLOCK, end * BLOCK
                while at < stop and command != 'erase':
                    want = min(1 << 20, stop - at)
                    if command == 'new':
                        data = b''
                        while len(data) < want:
                            more = read(want - len(data))
                            require(more, 'ROM_DATA_SHORT', 'The image inside this ROM ends early', partition=part)
                            data += more
                        digest.update(data)
                        done += len(data)
                    else:
                        data = zero[:want]
                    view = memoryview(data)
                    while view:
                        written = os.pwrite(fd, view, at)
                        at += written
                        view = view[written:]
                    if progress and command == 'new':
                        progress(part, done, total * BLOCK)
        require(not read(1), 'ROM_DATA_LONG', 'The image inside this ROM is longer than its transfer list says', partition=part)
    finally:
        finish()
    os.fsync(fd)
    drop_cached(fd)
    check = hashlib.sha256()
    for command, pairs in commands:
        if command != 'new':
            continue
        for start, end in pairs:
            at, stop = start * BLOCK, end * BLOCK
            while at < stop:
                data = os.pread(fd, min(1 << 20, stop - at), at)
                require(data, 'READBACK_MISMATCH', 'The partition does not read back', partition=part)
                check.update(data)
                at += len(data)
    require(check.digest() == digest.digest(), 'READBACK_MISMATCH', 'The partition does not match the image after writing', partition=part)
    return {'blocks': total, 'sha256': digest.hexdigest()}


def op_list_groups(text):
    """dynamic_partitions_op_list: the groups the ROM asks for in super, with their sizes."""
    groups = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) >= 3 and words[0] in ('add_group', 'resize_group'):
            groups[words[1]] = int(words[2])
    return [{'name': name, 'size': size, 'partitions': []} for name, size in groups.items()]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == 'inspect':
            value = inspect(args[1])
        elif len(args) == 6 and args[0] == 'extract' and args[2] == '--partition' and args[4] == '--out':
            with zipfile.ZipFile(args[1]) as archive:
                payload = Payload(archive)
                part = next((p for p in payload.partitions if p['name'] == args[3]), None)
                require(part, 'NO_SUCH_PARTITION', 'The payload has no such partition', partition=args[3])
                where, why = classify(part['name'], ('system', 'vendor', 'product', 'odm'))
                require(where != 'refused', 'PARTITION_REFUSED', why or 'This partition is not written', partition=args[3])
                fd = os.open(args[5], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                with os.fdopen(fd, 'w+b') as out:
                    value = {'partition': args[3], 'size': part['size'], 'sha256': payload.extract(part, out)}
        else:
            print('usage: android_rom.py inspect PACKAGE | extract PACKAGE --partition NAME --out FILE', file=sys.stderr)
            return 2
        print(json.dumps({'ok': True, **value}, ensure_ascii=True, separators=(',', ':')))
        return 0
    except RomError as error:
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': error.message, 'details': error.details}},
                         ensure_ascii=True, separators=(',', ':')))
        return 2
    except (OSError, zipfile.BadZipFile, lzma.LZMAError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': {'code': 'PACKAGE_UNREADABLE', 'message': str(error)[:300], 'details': {}}},
                         ensure_ascii=True, separators=(',', ':')))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
