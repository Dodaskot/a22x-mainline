#!/usr/bin/env python3
"""BOOT, dtbo and vbmeta judged by their contents instead of a hash list (2026-11-10)."""
import gzip
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootimage  # noqa: E402

PAGE = 2048
PARTITION = 4 * 1024 * 1024


def fdt(model, compatible):
    """A device tree with only a root node holding model and compatible."""
    strings = b'model\0compatible\0'
    props = b''
    for name_offset, value in ((0, model.encode() + b'\0'), (6, b''.join(item.encode() + b'\0' for item in compatible))):
        props += struct.pack('>III', 3, len(value), name_offset) + value + bytes(-len(value) % 4)
    structure = struct.pack('>I', 1) + b'\0\0\0\0' + props + struct.pack('>II', 2, 9)
    off_struct = 40 + 16  # header, then an empty memory reservation map
    off_strings = off_struct + len(structure)
    total = off_strings + len(strings)
    header = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings, 40, 17, 16, 0, len(strings), len(structure))
    return header + bytes(16) + structure + strings


def dt_table(*trees):
    entries, blobs, offset = b'', b'', 32 + 32 * len(trees)
    for tree in trees:
        entries += struct.pack('>8I', len(tree), offset + len(blobs), 0, 0, 0, 0, 0, 0)
        blobs += tree
    total = 32 + len(entries) + len(blobs)
    return struct.pack('>8I', 0xd7b7ab1e, total, 32, 32, len(trees), 32, PAGE, 0) + entries + blobs


def kernel(release='7.2.0-a22x+', appended=b''):
    image = bytearray(64 * 1024)
    image[56:60] = b'ARMd'
    banner = b'Linux version ' + release.encode() + b' (builder) #1 SMP\n'
    image[4096:4096 + len(banner)] = banner
    return gzip.compress(bytes(image), mtime=0) + appended


def cpio(files):
    out = b''
    for index, (name, data) in enumerate(list(files.items()) + [('TRAILER!!!', b'')]):
        mode = 0o100644 if name != 'TRAILER!!!' else 0
        encoded = name.encode() + b'\0'
        fields = [index + 1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(encoded), 0]
        out += b'070701' + b''.join(b'%08x' % value for value in fields) + encoded
        out += bytes(-len(out) % 4)
        out += data
        out += bytes(-len(out) % 4)
    return out


def ramdisk(props=None, extra=None):
    files = {'init': b'#!/bin/sh\n', **(extra or {})}
    if props:
        files['system/etc/ramdisk/build.prop'] = ''.join('%s=%s\n' % item for item in props.items()).encode()
    return gzip.compress(cpio(files), mtime=0)


def boot(kernel_blob, ramdisk_blob=b'', dtb=b'', version=2):
    header = bytearray(PAGE)
    header[:8] = b'ANDROID!'
    struct.pack_into('<10I', header, 8, len(kernel_blob), 0x40000000, len(ramdisk_blob), 0x41000000, 0, 0, 0x40000100,
                     PAGE, version, (12 << 25) | ((2025 - 2000) << 4) | 1)
    if version >= 1:
        struct.pack_into('<I', header, 1644, 1660)
    if version >= 2:
        struct.pack_into('<I', header, 1648, len(dtb))
    out = bytes(header)
    for blob in (kernel_blob, ramdisk_blob, dtb):
        out += blob + bytes(-len(blob) % PAGE)
    return out


def with_footer(image, partition='boot', digest=None):
    """IMAGE padded to the partition with an AVB hash footer (algorithm NONE)."""
    salt = b'\x11' * 32
    digest = digest or hashlib.sha256(salt + image).digest()
    name = partition.encode()
    body = struct.pack('>Q32sIII', len(image), b'sha256', len(name), len(salt), len(digest)) + bytes(64) + name + salt + digest
    body += bytes(-len(body) % 8)
    descriptor = struct.pack('>QQ', 2, len(body)) + body
    aux = descriptor + bytes(-len(descriptor) % 64)
    header = bytearray(256)
    header[:4] = b'AVB0'
    struct.pack_into('>IIQQIQQQQQQQQQQQII', header, 4, 1, 0, 0, len(aux), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, len(descriptor), 0, 0, 0)
    meta = bytes(header) + aux
    offset = (len(image) + 4095) // 4096 * 4096
    data = bytearray(PARTITION)
    data[:len(image)] = image
    data[offset:offset + len(meta)] = meta
    data[-64:] = b'AVBf' + struct.pack('>IIQQQ', 1, 0, len(image), offset, len(meta)) + bytes(28)
    return bytes(data)


def padded(image):
    return image + bytes(PARTITION - len(image))


MAINLINE_TREE = fdt('Samsung Galaxy A22 5G (SM-A226B)', ['samsung,a22x', 'mediatek,mt6833'])


class TestBoot(unittest.TestCase):
    def error(self, code, data, label='boot'):
        with self.assertRaises(bootimage.ImageError) as caught:
            bootimage.inspect(label, data)
        self.assertEqual(caught.exception.code, code)

    def test_a_mainline_boot_for_this_phone_is_accepted_and_named(self):
        data = with_footer(boot(kernel(appended=MAINLINE_TREE), ramdisk(), dt_table(MAINLINE_TREE)))
        info = bootimage.inspect('boot', data)
        self.assertEqual((info['kernel']['release'], info['device']['this_phone'], info['avb']['state'], info['mainline']),
                         ('7.2.0-a22x+', True, 'match', True))
        self.assertEqual(info['android'], {'version': '12.0.0', 'patch_level': '2025-01'})
        self.assertEqual(bootimage.linux_release(data), '7.2.0-a22x+')

    def test_a_stock_boot_is_known_by_its_ramdisk_properties(self):
        props = {'ro.product.bootimage.device': 'a22x', 'ro.product.bootimage.model': 'SM-A226B'}
        data = with_footer(boot(kernel('4.14.186-27218697'), ramdisk(props), dt_table(fdt('MT6833', ['mediatek,MT6833']))))
        info = bootimage.inspect('boot', data)
        self.assertEqual((info['device']['device'], info['mainline']), ('a22x', False))
        self.assertIsNone(bootimage.linux_release(data))  # Android's kernel cannot run a Linux system

    def test_a_write_that_stopped_part_way_is_refused(self):
        image = boot(kernel(), ramdisk(), dt_table(MAINLINE_TREE))
        torn = bytearray(padded(image))
        torn[PAGE + 40:PAGE + 400] = bytes(360)
        self.error('KERNEL_DAMAGED', bytes(torn))

    def test_a_changed_image_with_an_old_footer_is_judged_by_its_streams(self):
        image = boot(kernel(appended=MAINLINE_TREE), ramdisk(), dt_table(MAINLINE_TREE))
        info = bootimage.inspect('boot', with_footer(image, digest=b'\0' * 32))  # like Magisk's repacked BOOT
        self.assertEqual(info['avb']['state'], 'mismatch')

    def test_another_phone_and_an_unknown_one_are_refused(self):
        self.error('BOOT_OTHER_PHONE', padded(boot(kernel(), ramdisk({'ro.product.bootimage.device': 'a32x'}))))
        self.error('BOOT_OTHER_PHONE', padded(boot(kernel(), ramdisk(), dt_table(fdt('Galaxy A32', ['samsung,a32x', 'mediatek,mt6853'])))))
        self.error('BOOT_PHONE_UNKNOWN', padded(boot(kernel(), ramdisk())))
        info = bootimage.inspect('boot', padded(boot(kernel(), ramdisk(), dt_table(fdt('MT6833', ['mediatek,MT6833'])))))
        self.assertEqual((info['device']['this_phone'], info['device']['soc']), (False, True))

    def test_headers_this_bootloader_cannot_use_are_refused(self):
        self.error('BOOT_HEADER_UNSUPPORTED', padded(boot(kernel(), ramdisk(), dt_table(MAINLINE_TREE), version=3)))
        self.error('NOT_A_BOOT_IMAGE', bytes(PARTITION))
        image = boot(kernel(), ramdisk(), dt_table(MAINLINE_TREE))
        self.error('BOOT_SECTIONS_OUTSIDE', image[:len(image) - PAGE])


class TestTables(unittest.TestCase):
    def test_dtbo_and_vbmeta(self):
        table = dt_table(fdt('overlay', ['samsung,a22x']))
        self.assertEqual(bootimage.inspect('dtbo', table + bytes(1024))['entries'], 1)
        broken = bytearray(table)
        struct.pack_into('>I', broken, 36, 10 ** 6)  # the entry points past the table
        with self.assertRaises(bootimage.ImageError):
            bootimage.inspect('dtbo', bytes(broken))
        header = bytearray(256)
        header[:4] = b'AVB0'
        aux = bytes(64)
        struct.pack_into('>IIQQIQQQQQQQQQQQII', header, 4, 1, 0, 64, len(aux), 1, 0, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        auth = hashlib.sha256(bytes(header) + aux).digest() + bytes(32)
        meta = bytes(header) + auth + aux
        self.assertTrue(bootimage.inspect('vbmeta', meta + bytes(4096))['hash_checked'])
        damaged = bytearray(meta)
        damaged[-1] = 1
        with self.assertRaises(bootimage.ImageError) as caught:
            bootimage.inspect('vbmeta_system', bytes(damaged))
        self.assertEqual(caught.exception.code, 'VBMETA_DAMAGED')


if __name__ == '__main__':
    unittest.main()
