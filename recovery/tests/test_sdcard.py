# SPDX-License-Identifier: MIT
"""Partition a card (2026-11-11, TWRP's "Partition SD card"): the layout, the table and the guards.

A regular file stands in for the card; the part that needs a kernel (rereading the table, mkfs on
the partitions) runs on the phone with a loop device.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sdcard  # noqa: E402

MIB = 1 << 20
GIB = 1 << 30
SIZE = 512 * MIB  # The fixture card: sparse, and small enough to hash in a moment.


class Layout(unittest.TestCase):
    def test_files_take_what_linux_and_swap_leave_on_four_mebibyte_borders(self):
        parts = sdcard.layout(64 * GIB)
        self.assertEqual(parts, [{'kind': 'files', 'start': 4 * MIB, 'bytes': 64 * GIB - 4 * MIB}])
        parts = sdcard.layout(64 * GIB + 12345, linux_mib=16 * 1024, swap_mib=2048)
        self.assertEqual([p['kind'] for p in parts], ['files', 'linux', 'swap'])
        self.assertEqual((parts[1]['bytes'], parts[2]['bytes']), (16 * GIB, 2 * GIB))
        self.assertEqual(parts[0]['bytes'], 64 * GIB - 4 * MIB - 18 * GIB)
        for index, part in enumerate(parts):
            self.assertEqual((part['start'] % sdcard.ALIGN, part['bytes'] % sdcard.ALIGN), (0, 0))
            if index:
                self.assertEqual(part['start'], parts[index - 1]['start'] + parts[index - 1]['bytes'])
        self.assertLessEqual(parts[-1]['start'] + parts[-1]['bytes'], 64 * GIB + 12345)
        # A size between two borders is rounded up, never down.
        self.assertEqual(sdcard.layout(8 * GIB, linux_mib=1, swap_mib=5)[1:], [
            {'kind': 'linux', 'start': 8 * GIB - 12 * MIB, 'bytes': 4 * MIB},
            {'kind': 'swap', 'start': 8 * GIB - 8 * MIB, 'bytes': 8 * MIB}])

    def test_a_card_too_small_and_sizes_out_of_range_are_refused(self):
        for size, linux, swap in ((200 * MIB, 0, 0), (8 * GIB, 8 * 1024, 0), (8 * GIB, 7 * 1024, 1024), (8 * GIB, -1, 0),
                                  (8 * GIB, 0, 1 << 17), (8 * GIB, 1.5, 0)):
            with self.assertRaises(sdcard.fs.FilesystemError, msg=(size, linux, swap)):
                sdcard.layout(size, linux, swap)
        self.assertEqual(sdcard.layout(260 * MIB)[0]['bytes'], 256 * MIB)

    def test_a_medium_beyond_two_tebibytes_is_used_up_to_what_the_table_addresses(self):
        parts = sdcard.layout(3 << 40, linux_mib=1024)
        self.assertEqual(parts[-1]['start'] + parts[-1]['bytes'], 1 << 41)
        self.assertLess(max((p['start'] + p['bytes']) // 512 for p in parts) - 1, 1 << 32)

    def test_the_table_is_read_back_as_written(self):
        parts = sdcard.layout(32 * GIB, linux_mib=8192, swap_mib=1024)
        sector = sdcard.mbr(parts, b'\x01\x02\x03\x04')
        self.assertEqual((len(sector), sector[510:512], sector[440:444]), (512, b'\x55\xaa', b'\x01\x02\x03\x04'))
        self.assertEqual([sector[446 + 16 * n + 4] for n in range(4)], [0x0c, 0x83, 0x82, 0])
        kind, rows = sdcard.read_mbr(sector)
        self.assertEqual(kind, 'mbr')
        self.assertEqual([(r['number'], r['start'], r['bytes']) for r in rows],
                         [(n + 1, p['start'], p['bytes']) for n, p in enumerate(parts)])
        self.assertEqual(sdcard.read_mbr(bytes(512)), ('none', []))
        guard = bytearray(512)
        guard[446:462] = struct.pack('<B3sB3sII', 0, b'\0\x02\0', 0xee, b'\xff\xff\xff', 1, 0xffffffff)
        guard[510:512] = b'\x55\xaa'
        self.assertEqual(sdcard.read_mbr(bytes(guard)), ('gpt', []))


class Card(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-sdcard-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.card = self.root / 'card.img'
        self.make(SIZE)

    def make(self, size, **row):
        with open(self.card, 'wb') as out:
            out.truncate(size)
        (self.root / sdcard.FIXTURE).write_text(json.dumps({'schema': 'a22x.sdcard.fixture.v1', 'disks': [
            {'id': 'card', 'path': 'card.img', 'name': 'Fixture card', **row}]}))

    def run_helper(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sdcard.main(['--fixture', str(self.root), *args])
        return code, json.loads(out.getvalue().splitlines()[-1]), err.getvalue()

    def digest(self):
        value = hashlib.sha256()
        with open(self.card, 'rb') as stream:
            for block in iter(lambda: stream.read(8 * MIB), b''):
                value.update(block)
        return value.hexdigest()

    def piece(self, offset, length):
        with open(self.card, 'rb') as stream:
            stream.seek(offset)
            return stream.read(length)

    def listed(self):
        code, result, _ = self.run_helper('list')
        self.assertEqual(code, 0, result)
        return result['disks'][0]

    def test_an_empty_card_is_listed_and_nothing_is_written(self):
        before = self.digest()
        disk = self.listed()
        self.assertEqual((disk['id'], disk['name'], disk['kind'], disk['size_bytes'], disk['table'], disk['partitions'], disk['busy_code']),
                         ('card', 'Fixture card', 'file', SIZE, 'none', [], ''))
        self.assertEqual((disk['align_bytes'], disk['min_files_bytes'], disk['usable_bytes']), (4 * MIB, 256 * MIB, SIZE))
        self.assertEqual(self.digest(), before)

    def test_the_card_gets_files_linux_and_swap_and_old_tables_are_gone(self):
        # What the card held: a GPT at both ends and a filesystem where the old partition began.
        with open(self.card, 'r+b') as out:
            out.seek(512)
            out.write(b'EFI PART' + bytes(84))
            out.seek(SIZE - 512)
            out.write(b'EFI PART' + bytes(84))
            out.seek(4 * MIB + 1080)
            out.write(b'\x53\xef')
        disk = self.listed()
        code, done, progress = self.run_helper('apply', '--disk', 'card', '--expected-token', disk['token'],
                                               '--linux-mib', '128', '--swap-mib', '64', '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual([(c['kind'], c['size_bytes']) for c in done['created']],
                         [('files', SIZE - 4 * MIB - 192 * MIB), ('linux', 128 * MIB), ('swap', 64 * MIB)])
        after = self.listed()
        self.assertEqual(after['table'], 'mbr')
        self.assertEqual([(p['number'], p['fs_type'], p['size_bytes']) for p in after['partitions']],
                         [(1, 'vfat', SIZE - 196 * MIB), (2, 'ext4', 128 * MIB), (3, 'swap', 64 * MIB)])
        self.assertEqual(after['partitions'][1]['label'], 'linux')
        self.assertEqual(after['partitions'][0]['start_bytes'], 4 * MIB)
        self.assertNotEqual(after['token'], disk['token'])
        self.assertEqual((self.piece(512, 8), self.piece(SIZE - 512, 8)), (bytes(8), bytes(8)))
        data = self.digest()
        self.assertIn('card-table', progress)
        self.assertIn('card-check', progress)
        # The same request with the token of the old table is refused: the card is not what was shown.
        code, refused, _ = self.run_helper('apply', '--disk', 'card', '--expected-token', disk['token'], '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'DISK_CHANGED'))
        self.assertEqual(self.digest(), data)

    def test_nothing_is_written_without_the_confirmation_the_token_or_a_free_card(self):
        disk = self.listed()
        before = self.digest()
        code, refused, _ = self.run_helper('apply', '--disk', 'card', '--expected-token', disk['token'])
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        code, refused, _ = self.run_helper('apply', '--disk', 'card', '--expected-token', '0' * 64, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'DISK_CHANGED'))
        code, refused, _ = self.run_helper('apply', '--disk', 'other', '--expected-token', disk['token'], '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'DISK_MISSING'))
        code, refused, _ = self.run_helper('apply', '--disk', 'card', '--expected-token', disk['token'],
                                           '--linux-mib', '4096', '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'CARD_TOO_SMALL'))
        self.assertEqual(self.digest(), before)
        for row, expected in (({'mounted': True}, 'CARD_MOUNTED'), ({'read_only': True}, 'CARD_READ_ONLY')):
            self.make(SIZE, **row)
            disk = self.listed()
            self.assertEqual(disk['busy_code'], expected)
            self.assertTrue(disk['busy'])
            code, refused, _ = self.run_helper('apply', '--disk', 'card', '--expected-token', disk['token'], '--confirm')
            self.assertEqual((code, refused['error']['code']), (2, expected))
            self.assertEqual(self.digest(), before)
        self.make(100 * MIB)
        self.assertEqual(self.listed()['busy_code'], 'CARD_TOO_SMALL')

    def test_a_fixture_takes_a_file_or_a_loop_device_and_nothing_else(self):
        for path in ('/dev/null', '/dev/zero'):
            (self.root / sdcard.FIXTURE).write_text(json.dumps({'schema': 'a22x.sdcard.fixture.v1', 'disks': [{'id': 'card', 'path': path}]}))
            code, refused, _ = self.run_helper('list')
            self.assertEqual((code, refused['error']['code']), (2, 'INVALID_FIXTURE'), path)
        (self.root / sdcard.FIXTURE).write_text(json.dumps({'schema': 'other', 'disks': []}))
        self.assertEqual(self.run_helper('list')[1]['error']['code'], 'INVALID_FIXTURE')


class Admission(unittest.TestCase):
    """Which disks of the phone are offered at all: the card in the slot and USB drives."""

    def test_only_the_card_slot_and_usb_drives_are_listed(self):
        root = Path(tempfile.mkdtemp(prefix='a22x-sdcard-sys-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, root, True)
        devices = root / 'devices'

        def disk(node, where, **files):
            folder = devices / where / 'block' / node
            (folder / 'device').mkdir(parents=True)
            for key, value in files.items():
                (folder / 'device' / key).write_text(value + '\n')
            (root / 'block').mkdir(exist_ok=True)
            (root / 'block' / node).symlink_to(folder)

        disk('mmcblk0', 'platform/soc/11230000.mmc/mmc_host/mmc0/mmc0:0001', type='MMC', name='DX68MB', cid='15' * 16)
        disk('mmcblk1', 'platform/soc/11240000.mmc/mmc_host/mmc1/mmc1:aaaa', type='SD', name='SD64G', cid='1b' * 16)
        disk('mmcblk2', 'platform/soc/11250000.mmc/mmc_host/mmc2/mmc2:bbbb', type='SD', name='SDIO', cid='1c' * 16)
        disk('sda', 'platform/soc/11200000.usb/usb1/1-1/1-1:1.0/host0/target0:0:0/0:0:0:0', vendor='Kingston', model='DataTraveler')
        disk('sdb', 'platform/soc/11270000.ufs/host1/target1:0:0/1:0:0:0', vendor='Internal', model='Flash')
        disk('loop0', 'virtual', name='loop')
        context = object.__new__(sdcard.Device)
        context.sys_root = root
        seen = []
        context.block = lambda name, ident, title, kind, identity: seen.append((name, ident, title, kind)) or {'id': ident}
        self.assertEqual([d['id'] for d in context.disks()], ['sdcard', 'usb-sda'])
        self.assertEqual(seen, [('mmcblk1', 'sdcard', 'SD64G', 'sd'), ('sda', 'usb-sda', 'Kingston DataTraveler', 'usb')])
        # The slot holding an eMMC instead of a card is not a card.
        (devices / 'platform/soc/11240000.mmc/mmc_host/mmc1/mmc1:aaaa/block/mmcblk1/device/type').write_text('MMC\n')
        seen.clear()
        self.assertEqual([d['id'] for d in context.disks()], ['usb-sda'])


if __name__ == '__main__':
    unittest.main()
