"""Logical partitions inside super (lp_metadata.py, 2026-11-10), on a synthetic super file."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lp_metadata as lp  # noqa: E402

MIB = 1 << 20


def make_super(path, size=64 * MIB, slots=2, max_size=65536, group_max=40 * MIB, minor=0,
               parts=(('system', 8 * MIB), ('vendor', 4 * MIB), ('product', 2 * MIB))):
    """A super laid out as liblp writes it: by default system 8 MiB, vendor 4 MiB, product 2 MiB;
    PARTS in bytes, whole sectors (Samsung's odm of 4,349,952 bytes is not a whole MiB)."""
    first = (lp.RESERVED + 2 * lp.GEOMETRY_SIZE + 2 * slots * max_size + MIB - 1) // MIB * MIB // lp.SECTOR
    layout, parts, extents, at = parts, [], [], first
    for name, size_bytes in layout:
        parts.append({'name': name, 'attributes': lp.ATTR_READONLY, 'first_extent': len(extents), 'extents': 1, 'group': 1})
        extents.append({'sectors': size_bytes // lp.SECTOR, 'type': lp.TARGET_LINEAR, 'start': at, 'device': 0})
        at += size_bytes // lp.SECTOR
    header_size = 128 if minor < 2 else 256
    metadata = {'major': 10, 'minor': minor, 'header_size': header_size, 'header_tail': bytes(header_size - 128),
                'partitions': parts, 'extents': extents,
                'groups': [{'name': 'default', 'flags': 0, 'maximum_size': 0}, {'name': 'main', 'flags': 0, 'maximum_size': group_max}],
                'devices': [{'first_logical_sector': first, 'alignment': MIB, 'alignment_offset': 0, 'size': size,
                             'name': 'super', 'flags': 0}]}
    with open(path, 'wb') as f:
        f.truncate(size)
        zeroed = lp.GEOMETRY.pack(lp.GEOMETRY_MAGIC, lp.GEOMETRY.size, bytes(32), max_size, slots, 4096)
        geometry = lp.GEOMETRY.pack(lp.GEOMETRY_MAGIC, lp.GEOMETRY.size, hashlib.sha256(zeroed).digest(), max_size, slots, 4096)
        for offset in (lp.RESERVED, lp.RESERVED + lp.GEOMETRY_SIZE):
            f.seek(offset)
            f.write(geometry)
        blob = lp.serialize(metadata, max_size)
        geo = {'metadata_max_size': max_size, 'metadata_slot_count': slots, 'logical_block_size': 4096}
        for slot in range(slots):
            for offset in lp.metadata_offsets(geo, slot):
                f.seek(offset)
                f.write(blob)
        # Each partition starts with its own name, as a stand-in for its content.
        for p, e in zip(parts, extents):
            f.seek(e['start'] * lp.SECTOR)
            f.write(p['name'].encode() * 100)
    return first


class Metadata(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-lp-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = self.root / 'super.img'
        self.first = make_super(self.path)

    def open(self, write=False):
        fd = os.open(self.path, os.O_RDWR if write else os.O_RDONLY)
        self.addCleanup(os.close, fd)
        return lp.Super(fd)

    def test_reads_the_partitions_groups_and_free_space(self):
        info = self.open().describe()
        self.assertEqual([(p['name'], p['size'], p['group']) for p in info['partitions']],
                         [('system', 8 * MIB, 'main'), ('vendor', 4 * MIB, 'main'), ('product', 2 * MIB, 'main')])
        self.assertEqual(info['groups'][1], {'name': 'main', 'maximum_size': 40 * MIB, 'used': 14 * MIB})
        self.assertEqual(info['free_bytes'], 64 * MIB - self.first * lp.SECTOR - 14 * MIB)
        self.assertTrue(all(p['readonly'] for p in info['partitions']))

    def test_an_image_goes_into_its_partition_and_the_rest_is_zeroed(self):
        s = self.open(write=True)
        data = os.urandom(3 * MIB + 123)
        digest = s.write_image('vendor', io.BytesIO(data), len(data))
        self.assertEqual(digest, hashlib.sha256(data + bytes(4 * MIB - len(data))).hexdigest())
        self.assertEqual(s.read_partition('vendor'), digest)
        self.assertNotEqual(s.read_partition('system'), digest)  # neighbours untouched
        with open(self.path, 'rb') as f:
            f.seek(s.extents_of(s.partition('system'))[0]['start'] * lp.SECTOR)
            self.assertEqual(f.read(6), b'system')
        with self.assertRaises(lp.MetadataError) as caught:
            s.write_image('product', io.BytesIO(bytes(3 * MIB)), 3 * MIB)
        self.assertEqual(caught.exception.code, 'IMAGE_TOO_LARGE')

    def test_a_larger_gsi_grows_system_in_free_space(self):
        s = self.open(write=True)
        self.assertEqual(s.resize('system', 20 * MIB + 5), 21 * MIB)  # rounded up to the 1 MiB alignment
        again = self.open(write=True)  # every copy re-read from disk, checksums checked
        self.assertEqual(again.size_of(again.partition('system')), 21 * MIB)
        self.assertEqual(again.size_of(again.partition('vendor')), 4 * MIB)
        extents = again.extents_of(again.partition('system'))
        self.assertEqual(len(extents), 2)  # its first extent stays where it was; the rest is after product
        with open(self.path, 'rb') as f:  # vendor's content did not move
            f.seek(again.extents_of(again.partition('vendor'))[0]['start'] * lp.SECTOR)
            self.assertEqual(f.read(6), b'vendor')
        data = os.urandom(21 * MIB)
        self.assertEqual(again.write_image('system', io.BytesIO(data), len(data)), hashlib.sha256(data).hexdigest())

    def test_the_group_limit_and_free_space_are_kept(self):
        s = self.open(write=True)
        with self.assertRaises(lp.MetadataError) as caught:
            s.resize('system', 35 * MIB)  # 35 + 4 + 2 > 40
        self.assertEqual(caught.exception.code, 'GROUP_FULL')
        s.remove('product')
        self.assertEqual([p['name'] for p in self.open().describe()['partitions']], ['system', 'vendor'])
        s = self.open(write=True)
        self.assertEqual(s.resize('system', 36 * MIB), 36 * MIB)  # 36 + 4 = 40: room after product went

    def test_shrinking_and_every_copy_stays_valid(self):
        s = self.open(write=True)
        self.assertEqual(s.resize('system', 3 * MIB), 3 * MIB)
        with open(self.path, 'rb') as f:
            geo = s.geometry
            copies = []
            for slot in range(geo['metadata_slot_count']):
                for offset in lp.metadata_offsets(geo, slot):
                    f.seek(offset)
                    copies.append(lp.parse_metadata(f.read(geo['metadata_max_size'])))
        self.assertEqual(len(copies), 4)
        self.assertTrue(all(c['extents'][0]['sectors'] == 3 * MIB // lp.SECTOR for c in copies))

    def test_a_damaged_primary_copy_falls_back_to_the_backup(self):
        s = self.open()
        primary, _ = lp.metadata_offsets(s.geometry, 0)
        with open(self.path, 'r+b') as f:
            f.seek(primary + 200)
            f.write(b'\xff' * 16)
        self.assertEqual(len(self.open().describe()['partitions']), 3)
        with open(self.path, 'r+b') as f:
            f.seek(lp.RESERVED + 30)
            f.write(b'\x00')
        with self.assertRaises(lp.MetadataError) as caught:
            self.open()
        self.assertEqual(caught.exception.code, 'GEOMETRY_DAMAGED')

    def test_version_10_2_headers_keep_their_tail(self):
        path = self.root / 'super-v2.img'
        make_super(path, minor=2)
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        s = lp.Super(fd)
        self.assertEqual(s.metadata['header_size'], 256)
        s.resize('vendor', 5 * MIB)
        fd2 = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd2)
        self.assertEqual(lp.Super(fd2).metadata['minor'], 2)

    def test_not_a_super(self):
        other = self.root / 'boot.img'
        other.write_bytes(b'ANDROID!' + bytes(16384))
        fd = os.open(other, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(lp.MetadataError) as caught:
            lp.Super(fd)
        self.assertEqual(caught.exception.code, 'NOT_SUPER')


class DropCached(unittest.TestCase):
    def test_a_failed_flush_of_a_block_device_is_raised(self):
        # 2026-11-14 review: it was swallowed, and a readback could pass from the page cache.
        import stat
        from unittest import mock
        import lp_metadata
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        block = os.stat_result((stat.S_IFBLK | 0o600,) + tuple(os.fstat(read))[1:])
        with mock.patch.object(lp_metadata.os, 'fstat', return_value=block), \
                mock.patch.object(lp_metadata.fcntl, 'ioctl', side_effect=OSError(13, 'Permission denied')):
            with self.assertRaises(OSError):
                lp_metadata.drop_cached(read)
        with mock.patch.object(lp_metadata.fcntl, 'ioctl', side_effect=AssertionError('no ioctl on a file')):
            lp_metadata.drop_cached(read)  # not a block device: its pages are let go, quietly


if __name__ == '__main__':
    unittest.main()
