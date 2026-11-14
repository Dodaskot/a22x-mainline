#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What a BOOT, dtbo or vbmeta image is, judged from its own contents (2026-11-10).

The recovery used to accept a BOOT only when its SHA-256 was on a list, so every new kernel, every
stock Android BOOT and every vbmeta made for a GSI needed the list to grow. Now a partition is
judged by what is inside it:

BOOT    An Android boot image with header version 0 to 2 (this phone's bootloader has no
        vendor_boot) whose sections all lie inside the partition. Its kernel unpacks to the end
        (the gzip CRC and length are checked, a raw arm64 Image is recognised) and names its release
        ("Linux version ..."). Something in it names this phone: the header's board SRPTL28A011, a
        device tree for samsung,a22x or an SM-A226B model, or the ramdisk's
        ro.product.bootimage.device=a22x. A device tree for the MT6833 alone is not enough
        (2026-11-12 audit: an image for another phone with the same chip, xiaomi,... with
        mediatek,mt6833, passed), and a board compatible of any other maker names another
        phone. An AVB hash footer, when
        there is one, proves the image complete when it matches; a footer left over from before a
        change (Magisk keeps the stock one) is only noted, and the kernel and ramdisk streams decide.
dtbo    An Android DT table whose entries lie inside the partition and are device trees.
vbmeta  An AVB vbmeta image whose blocks lie inside the partition and, when it carries a hash,
        whose hash matches.

A torn write fails these checks: the kernel stream, an AVB digest or a section bound breaks.
"""
import hashlib
import os
import re
import struct
import subprocess
import zlib

BOOT_MAGIC = b'ANDROID!'
FDT_MAGIC = 0xd00dfeed
DT_TABLE_MAGIC = 0xd7b7ab1e
AVB_FOOTER_MAGIC = b'AVBf'
AVB_MAGIC = b'AVB0'
DEVICE = 'a22x'
MODEL_PREFIX = 'SM-A226B'
# Compatible strings that name no phone: the chip's maker and generic ones.
NEUTRAL_VENDORS = ('mediatek,', 'arm,', 'linux,')
DEVICE_COMPATIBLE = 'samsung,a22x'
BOARD = 'SRPTL28A011'  # Samsung's board name in the header of every A22x boot and recovery image
SOC_COMPATIBLE = 'mediatek,mt6833'
KERNEL_LIMIT = 160 << 20
RAMDISK_LIMIT = 256 << 20
PROP_FILES = ('system/etc/ramdisk/build.prop', 'prop.default', 'default.prop')
PROP_KEYS = ('ro.product.bootimage.device', 'ro.product.bootimage.model', 'ro.product.bootimage.name',
             'ro.bootimage.build.fingerprint', 'ro.product.device', 'ro.product.model')
AVB_HASHES = {1: 'sha256', 2: 'sha256', 3: 'sha256', 4: 'sha512', 5: 'sha512', 6: 'sha512'}
LZ4_MAGICS = (b'\x02\x21\x4c\x18', b'\x04\x22\x4d\x18')
LZ4_TOOLS = ('/bin/lz4', '/usr/bin/lz4')


class ImageError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise ImageError(code, message)


def require(value, code, message):
    if not value:
        fail(code, message)


def rounded(size, page):
    return (size + page - 1) // page * page


def text(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


# Device trees -----------------------------------------------------------------------------------

def fdt_root(blob):
    """model and compatible strings of a flattened device tree's root node."""
    require(len(blob) >= 40, 'DTB_INVALID', 'A device tree is cut short')
    magic, total, off_struct, off_strings = struct.unpack_from('>IIII', blob, 0)
    require(magic == FDT_MAGIC, 'DTB_INVALID', 'Not a device tree')
    size_strings, size_struct = struct.unpack_from('>II', blob, 32)
    require(40 <= total <= len(blob) and off_struct + size_struct <= total and off_strings + size_strings <= total,
            'DTB_INVALID', 'A device tree lies outside its own size')
    pos, end, depth, found = off_struct, off_struct + size_struct, 0, {}
    while pos + 4 <= end:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:  # FDT_BEGIN_NODE
            name_end = blob.find(b'\0', pos, end)
            require(name_end >= 0, 'DTB_INVALID', 'A device tree node name is cut short')
            pos = (name_end + 4) & ~3
            depth += 1
        elif token == 2:  # FDT_END_NODE
            depth -= 1
            if depth <= 0:
                break
        elif token == 3:  # FDT_PROP
            require(pos + 8 <= end, 'DTB_INVALID', 'A device tree property is cut short')
            length, name_offset = struct.unpack_from('>II', blob, pos)
            pos += 8
            require(pos + length <= end and name_offset < size_strings, 'DTB_INVALID', 'A device tree property lies outside')
            if depth == 1:
                start = off_strings + name_offset
                name = text(blob[start:off_strings + size_strings])
                if name in ('model', 'compatible'):
                    found[name] = [item.decode('utf-8', 'replace') for item in blob[pos:pos + length].split(b'\0') if item]
            pos = (pos + length + 3) & ~3
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            break
        else:
            fail('DTB_INVALID', 'Unknown device tree token')
    return {'model': (found.get('model') or [''])[0], 'compatible': found.get('compatible', []), 'size': total}


def fdts(blob):
    """Every device tree in BLOB: an Android DT table, or device trees one after another."""
    if len(blob) >= 32 and struct.unpack_from('>I', blob, 0)[0] == DT_TABLE_MAGIC:
        return dt_table(blob)['entries']
    found, at = [], 0
    while at + 40 <= len(blob) and struct.unpack_from('>I', blob, at)[0] == FDT_MAGIC:
        tree = fdt_root(blob[at:])
        found.append(tree)
        at += rounded(tree['size'], 4)
        while at < len(blob) and at % 8 and not blob[at]:
            at += 1
    return found


def dt_table(blob):
    """An Android DT table (the dtbo partition, the BOOT header's DTB on this phone)."""
    require(len(blob) >= 32, 'DTBO_INVALID', 'The DT table is cut short')
    magic, total, header_size, entry_size, count, entries, page, version = struct.unpack_from('>8I', blob, 0)
    require(magic == DT_TABLE_MAGIC, 'DTBO_INVALID', 'There is no Android DT table')
    require(32 <= header_size <= total <= len(blob) and entry_size >= 32 and 1 <= count <= 256
            and entries + count * entry_size <= total, 'DTBO_INVALID', 'The DT table lies outside the partition')
    trees = []
    for index in range(count):
        size, offset = struct.unpack_from('>II', blob, entries + index * entry_size)
        require(size and offset + size <= total, 'DTBO_INVALID', 'A device tree of the DT table lies outside it')
        if struct.unpack_from('>I', blob, offset)[0] == FDT_MAGIC:
            tree = fdt_root(blob[offset:offset + size])
            require(tree['size'] <= size, 'DTBO_INVALID', 'A device tree is larger than its DT table entry')
            trees.append(tree)
        else:
            require(version >= 1, 'DTBO_INVALID', 'A DT table entry is not a device tree')
            trees.append({'model': '', 'compatible': [], 'size': size, 'compressed': True})
    return {'entries': trees, 'size': total, 'version': version, 'page_size': page}


# AVB --------------------------------------------------------------------------------------------

def vbmeta(blob, verify=True):
    """An AVB vbmeta image: its sizes, descriptors and (when it has one) checked hash."""
    require(len(blob) >= 256 and blob[:4] == AVB_MAGIC, 'VBMETA_INVALID', 'There is no AVB vbmeta header')
    fields = struct.unpack_from('>IIQQIQQQQQQQQQQQII', blob, 4)
    major, _, auth_size, aux_size, algorithm = fields[:5]
    hash_offset, hash_size = fields[5], fields[6]
    descriptors_offset, descriptors_size, flags = fields[13], fields[14], fields[16]
    require(major == 1 and auth_size % 64 == 0 and aux_size % 64 == 0 and 256 + auth_size + aux_size <= len(blob)
            and descriptors_offset + descriptors_size <= aux_size and hash_offset + hash_size <= auth_size,
            'VBMETA_INVALID', 'The vbmeta blocks lie outside the image')
    aux = blob[256 + auth_size:256 + auth_size + aux_size]
    checked = flags_changed = False
    if verify and algorithm in AVB_HASHES:
        digest = hashlib.new(AVB_HASHES[algorithm], blob[:256] + aux).digest()
        stored = blob[256 + hash_offset:256 + hash_offset + hash_size]
        if stored != digest and flags & 2:
            # 2026-11-10: a signed vbmeta whose flags alone were set (verification off: Magisk's
            # putInt(120, 3) for Samsung, this recovery's root): its hash fits the header with flags 0.
            original = bytearray(blob[:256])
            struct.pack_into('>I', original, 120, 0)
            flags_changed = hashlib.new(AVB_HASHES[algorithm], bytes(original) + aux).digest() == stored
            if flags_changed:
                digest = stored
        require(hash_size == len(digest) and stored == digest,
                'VBMETA_DAMAGED', 'The vbmeta hash does not match its contents')
        checked = True
    descriptors, at, end = [], descriptors_offset, descriptors_offset + descriptors_size
    while at + 16 <= end:
        tag, following = struct.unpack_from('>QQ', aux, at)
        require(at + 16 + following <= end, 'VBMETA_INVALID', 'A vbmeta descriptor lies outside')
        body = aux[at + 16:at + 16 + following]
        if tag == 2 and len(body) >= 116:  # hash descriptor
            image_size, hash_name, name_len, salt_len, digest_len = struct.unpack_from('>Q32sIII', body, 0)
            start = 116
            require(start + name_len + salt_len + digest_len <= len(body), 'VBMETA_INVALID', 'A hash descriptor is cut short')
            descriptors.append({'kind': 'hash', 'image_size': image_size, 'algorithm': text(hash_name),
                                'partition': body[start:start + name_len].decode('utf-8', 'replace'),
                                'salt': body[start + name_len:start + name_len + salt_len],
                                'digest': body[start + name_len + salt_len:start + name_len + salt_len + digest_len]})
        else:
            descriptors.append({'kind': {0: 'property', 1: 'hashtree', 3: 'kernel_cmdline', 4: 'chain'}.get(tag, 'other')})
        at += 16 + following
    return {'size': 256 + auth_size + aux_size, 'algorithm': algorithm, 'hash_checked': checked, 'flags': flags,
            'flags_changed': flags_changed, 'descriptors': descriptors}


def avb_footer(data):
    """The AVB footer at the end of a partition image and its vbmeta, or None without one."""
    if len(data) < 64 or data[-64:-60] != AVB_FOOTER_MAGIC:
        return None
    major, _, original, offset, size = struct.unpack_from('>IIQQQ', data, len(data) - 60)
    require(major == 1 and original <= offset and offset + size <= len(data) - 64, 'AVB_FOOTER_INVALID',
            'The AVB footer points outside the partition')
    try:
        meta = vbmeta(data[offset:offset + size])
    except ImageError as error:
        if error.code != 'VBMETA_DAMAGED':
            raise
        # 2026-11-13, LineageOS 20 for A226B: a BOOT changed after avbtool signed it (its vbmeta
        # hash and its hash descriptor both differ). Samsung's unlocked bootloader starts it; it is
        # read as an image changed after signing (avb state 'mismatch'), its kernel and ramdisk
        # still checked by decoding them, not refused as no BOOT at all.
        meta = vbmeta(data[offset:offset + size], verify=False)
        meta['signature'] = 'mismatch'
    return {'original_size': original, 'vbmeta': meta}


def avb_image_state(data, footer, partition):
    """'match' when PARTITION's hash descriptors cover these bytes exactly, 'mismatch' when one does
    not (an incomplete write, or an image changed after signing), 'none' without a descriptor."""
    state = 'none'
    for item in footer['vbmeta']['descriptors']:
        if item['kind'] != 'hash' or item['partition'] != partition:
            continue
        if item['algorithm'] not in ('sha256', 'sha512') or item['image_size'] > footer['original_size']:
            return 'mismatch'
        digest = hashlib.new(item['algorithm'], item['salt'] + data[:item['image_size']]).digest()
        if digest != item['digest']:
            return 'mismatch'
        state = 'match'
    return state


def lz4_complete(blob):
    """Whether the lz4 tool decodes BLOB to its end; None when the tool is missing."""
    tool = next((path for path in LZ4_TOOLS if os.path.exists(path)), None)
    if tool is None:
        return None
    try:
        result = subprocess.run([tool, '-d', '-c', '-q'], input=blob, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


# BOOT -------------------------------------------------------------------------------------------

def boot_header(data):
    require(len(data) >= 2048 and data[:8] == BOOT_MAGIC, 'NOT_A_BOOT_IMAGE', 'There is no Android boot image header')
    kernel, _, ramdisk, _, second, _, _, page, version, os_version = struct.unpack_from('<10I', data, 8)
    require(version <= 2, 'BOOT_HEADER_UNSUPPORTED',
            'Boot header version %d needs vendor_boot, which this phone does not use' % version)
    require(page in (2048, 4096, 8192, 16384), 'BOOT_HEADER_INVALID', 'The boot image has an unusual page size')
    recovery_dtbo = struct.unpack_from('<I', data, 1632)[0] if version >= 1 else 0
    dtb = struct.unpack_from('<I', data, 1648)[0] if version >= 2 else 0
    header_size = struct.unpack_from('<I', data, 1644)[0] if version >= 1 else page
    offset, sections = rounded(max(header_size, 1), page), {}
    for name, size in (('kernel', kernel), ('ramdisk', ramdisk), ('second', second), ('recovery_dtbo', recovery_dtbo), ('dtb', dtb)):
        sections[name] = (offset, size)
        offset += rounded(size, page)
    os_info = None
    if os_version:
        number, level = os_version >> 11, os_version & 0x7ff
        os_info = {'version': '%d.%d.%d' % (number >> 14, (number >> 7) & 0x7f, number & 0x7f),
                   'patch_level': '%04d-%02d' % ((level >> 4) + 2000, level & 0xf)}
    return {'version': version, 'page_size': page, 'sections': sections, 'end': offset, 'os': os_info,
            'name': text(data[48:64]), 'cmdline': (text(data[64:576]) + ' ' + text(data[608:1632])).strip()}


def kernel_info(blob):
    """Compression, release and appended device trees of a kernel section."""
    require(blob, 'KERNEL_MISSING', 'The boot image has no kernel')
    appended = b''
    if blob[:2] == b'\x1f\x8b':
        stream = zlib.decompressobj(31)
        try:
            image = stream.decompress(blob, KERNEL_LIMIT)
        except zlib.error:
            fail('KERNEL_DAMAGED', 'The kernel does not unpack: the image is damaged or incomplete')
        require(stream.eof and not stream.unconsumed_tail, 'KERNEL_DAMAGED',
                'The kernel does not unpack to its end: the image is damaged or incomplete')
        compression, appended, verified = 'gzip', stream.unused_data, True
    elif blob[56:60] == b'ARMd':
        compression, image, verified = 'none', blob, True
    elif blob[:4] in LZ4_MAGICS:
        compression, image, verified = 'lz4', b'', lz4_complete(blob) is True
    else:
        fail('KERNEL_UNKNOWN', 'The kernel is neither gzip, LZ4 nor an arm64 Image')
    if image:
        require(image[56:60] == b'ARMd', 'KERNEL_UNKNOWN', 'The kernel is not an arm64 Image')
    release = re.search(rb'Linux version (\S{1,64}) ', image) if image else None
    trees = fdts(appended.lstrip(b'\0')) if appended.lstrip(b'\0')[:4] == FDT_MAGIC.to_bytes(4, 'big') else []
    return {'compression': compression, 'verified': verified, 'trees': trees,
            'release': release.group(1).decode('utf-8', 'replace') if release else None,
            'kernelpatch': kernelpatch_version(image) if image else None}


KERNELPATCH_MAGIC = b'KP1158\0\0'


def kernelpatch_version(image):
    """The version of KernelPatch (what APatch puts into a kernel, 2026-11-11) in an unpacked arm64
    Image, or None: its image starts with the magic, then the version as bytes (0, patch, minor, major)."""
    at = image.find(KERNELPATCH_MAGIC)
    if at < 0 or at + 12 > len(image):
        return None
    _, patch, minor, major = image[at + 8:at + 12]
    return '%d.%d.%d' % (major, minor, patch)


def cpio_files(raw, wanted):
    """The WANTED regular files of a newc cpio archive."""
    found, at = {}, 0
    while at + 110 <= len(raw) and raw[at:at + 6] in (b'070701', b'070702'):
        try:
            fields = [int(raw[at + 6 + i * 8:at + 14 + i * 8], 16) for i in range(13)]
        except ValueError:
            break
        size, name_size = fields[6], fields[11]
        name = raw[at + 110:at + 110 + name_size].rstrip(b'\0').decode('utf-8', 'replace').lstrip('./')
        start = (at + 110 + name_size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        if name in wanted and fields[1] & 0o170000 == 0o100000:
            found[name] = raw[start:start + size]
        at = (start + size + 3) & ~3
    return found


def cpio_names(raw):
    """The names of a newc cpio archive, as written there."""
    at = 0
    while at + 110 <= len(raw) and raw[at:at + 6] in (b'070701', b'070702'):
        try:
            size, name_size = int(raw[at + 54:at + 62], 16), int(raw[at + 94:at + 102], 16)
        except ValueError:
            return
        name = raw[at + 110:at + 110 + name_size].rstrip(b'\0')
        if name == b'TRAILER!!!':
            return
        yield name
        at = (((at + 110 + name_size + 3) & ~3) + size + 3) & ~3


def ramdisk_info(blob):
    """Compression and the build properties of a ramdisk."""
    if not blob:
        return {'compression': None, 'verified': True, 'props': {}, 'magisk': False}
    if blob[:2] == b'\x1f\x8b':
        stream = zlib.decompressobj(31)
        try:
            raw = stream.decompress(blob, RAMDISK_LIMIT)
        except zlib.error:
            fail('RAMDISK_DAMAGED', 'The ramdisk does not unpack: the image is damaged or incomplete')
        require(stream.eof and not stream.unconsumed_tail, 'RAMDISK_DAMAGED',
                'The ramdisk does not unpack to its end: the image is damaged or incomplete')
        compression, verified = 'gzip', True
    elif blob[:6] in (b'070701', b'070702'):
        raw, compression, verified = blob, 'none', True
    elif blob[:4] in LZ4_MAGICS:
        state = lz4_complete(blob)
        require(state is not False, 'RAMDISK_DAMAGED', 'The ramdisk does not unpack: the image is damaged or incomplete')
        raw, compression, verified = b'', 'lz4', state is True
    else:
        raw, compression, verified = b'', 'other', False
    props = {}
    for data in cpio_files(raw, PROP_FILES).values():
        for line in data.decode('utf-8', 'replace').splitlines():
            key, _, value = line.partition('=')
            if key.strip() in PROP_KEYS and key.strip() not in props:
                props[key.strip()] = value.strip()[:160]
    # Magisk keeps its config at .backup/.magisk in the ramdisk it patched (2026-11-10).
    magisk = any(name in (b'.backup/.magisk', b'./.backup/.magisk') for name in cpio_names(raw)) if raw else None
    return {'compression': compression, 'verified': verified, 'props': props, 'magisk': magisk}


def device_of(trees, props, board=''):
    """Whether the image names this phone, only its SoC, or another phone."""
    models = [tree['model'] for tree in trees if tree.get('model')]
    compatible = [item.lower() for tree in trees for item in tree.get('compatible', [])]
    named = props.get('ro.product.bootimage.device') or props.get('ro.product.device')
    model = props.get('ro.product.bootimage.model') or props.get('ro.product.model') or ''
    this_phone = (board == BOARD or DEVICE_COMPATIBLE in compatible or any(MODEL_PREFIX in item for item in models)
                  or named == DEVICE or model.startswith(MODEL_PREFIX))
    soc = SOC_COMPATIBLE in compatible
    other = (named is not None and named != DEVICE) or (model and not model.startswith(MODEL_PREFIX)) or any(
        ',' in item and item != DEVICE_COMPATIBLE and not item.startswith(NEUTRAL_VENDORS) for item in compatible) or (
        board.startswith('SRP') and board != BOARD)
    return {'this_phone': this_phone, 'soc': soc, 'other_phone': bool(other) and not this_phone,
            'model': next((item for item in models if MODEL_PREFIX in item), model or (models[0] if models else '')),
            'device': named or (DEVICE if DEVICE_COMPATIBLE in compatible else '')}


def inspect_boot(data, partition='boot'):
    """Everything the recovery needs to know about a BOOT (or recovery) image; raises ImageError
    when it is not a complete boot image for this phone."""
    header = boot_header(data)
    require(header['end'] <= len(data), 'BOOT_SECTIONS_OUTSIDE', 'The boot image is larger than the partition')
    footer = avb_footer(data)
    if footer:
        require(header['end'] <= footer['original_size'], 'BOOT_SECTIONS_OUTSIDE',
                'The boot image is larger than the part its AVB footer covers')
    def section(name):
        offset, size = header['sections'][name]
        return data[offset:offset + size]
    kernel = kernel_info(section('kernel'))
    ramdisk = ramdisk_info(section('ramdisk'))
    trees = kernel['trees'] + (fdts(section('dtb')) if header['sections']['dtb'][1] else [])
    device = device_of(trees, ramdisk['props'], header['name'])
    avb_state = avb_image_state(data, footer, partition) if footer else 'none'
    complete = avb_state == 'match' or (kernel['verified'] and ramdisk['verified'])
    require(complete, 'BOOT_INCOMPLETE_UNPROVEN',
            'Neither an AVB hash nor the kernel and ramdisk streams show that the image is complete')
    require(not device['other_phone'], 'BOOT_OTHER_PHONE',
            'This boot image is for another phone (%s)' % (device['device'] or device['model'] or 'unknown'))
    require(device['this_phone'], 'BOOT_PHONE_UNKNOWN',
            'Nothing in this boot image names the Galaxy A22 5G (SM-A226B)')
    linux = kernel['release'] or ''
    return {'kind': 'boot', 'header_version': header['version'], 'page_size': header['page_size'],
            'android': header['os'], 'kernel': {key: kernel[key] for key in ('compression', 'verified', 'release')},
            'ramdisk_compression': ramdisk['compression'], 'props': ramdisk['props'],
            'device': device, 'avb': {'present': bool(footer), 'state': avb_state},
            'mainline': linux.startswith('7.') or '-a22x' in linux, 'image_bytes': header['end'],
            # Root in this BOOT: Magisk lives in the ramdisk, APatch (KernelPatch) in the kernel.
            'root': 'magisk' if ramdisk['magisk'] else ('apatch' if kernel['kernelpatch'] else ''),
            'kernelpatch': kernel['kernelpatch'] or ''}


def inspect_dtbo(data):
    table = dt_table(data)
    return {'kind': 'dtbo', 'entries': len(table['entries']), 'size': table['size']}


def inspect_vbmeta(data):
    meta = vbmeta(data)
    return {'kind': 'vbmeta', 'size': meta['size'], 'algorithm': meta['algorithm'], 'flags': meta['flags'],
            'hash_checked': meta['hash_checked']}


INSPECT = {'boot': inspect_boot, 'recovery': lambda data: inspect_boot(data, 'recovery'), 'dtbo': inspect_dtbo,
           'vbmeta': inspect_vbmeta, 'vbmeta_system': inspect_vbmeta, 'vbmeta_vendor': inspect_vbmeta}


def inspect(label, data):
    """Judge partition LABEL holding DATA; raises ImageError when it is not usable on this phone."""
    require(label in INSPECT, 'UNSUPPORTED_PARTITION', 'This partition is not judged by its contents')
    return INSPECT[label](data)


def linux_release(data):
    """The release of a mainline Linux BOOT for this phone, or None for anything else."""
    try:
        info = inspect_boot(data)
    except ImageError:
        return None
    return info['kernel']['release'] if info['device']['this_phone'] and info['mainline'] else None


if __name__ == '__main__':
    import json
    import sys
    label, path = sys.argv[1], sys.argv[2]
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        result = inspect(label, raw)
        print(json.dumps({'ok': True, **result}, default=str, indent=1))
    except ImageError as exc:
        print(json.dumps({'ok': False, 'code': exc.code, 'message': str(exc)}))
        raise SystemExit(1)
