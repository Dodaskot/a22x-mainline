"""misc_store: two copies of each record in the last 128 KiB of misc (2026-11-14).

The promise checked over every way a write can stop: a record once whole never reads as none and
never as damaged; it reads as the payload before the write or the one being written. And nothing
outside the record's own 64 KiB is ever written."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import misc_store as ms  # noqa: E402


class Crash(Exception):
    """The power went: nothing after this step happened."""


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='a22x-misc-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.misc = self.tmp / 'misc'
        # Android's part (the bootloader message, the system space) and the rest: bytes that must stay.
        self.outside = os.urandom(ms.END)
        self.misc.write_bytes(self.outside[:ms.AREAS['settings']] + bytes(ms.END - ms.AREAS['settings']))
        patcher = mock.patch.dict(os.environ, {'A22X_MISC': str(self.misc)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_outside_kept(self, name):
        data = self.misc.read_bytes()
        first = ms.AREAS[name]
        self.assertEqual(data[:ms.AREAS['settings']], self.outside[:ms.AREAS['settings']])
        for other, start in ms.AREAS.items():
            if other != name:
                self.assertEqual(data[start:start + 2 * ms.SLOT], bytes(2 * ms.SLOT), other)
        self.assertEqual(len(data), ms.END)
        return data[first:first + 2 * ms.SLOT]

    def test_empty_then_written_then_both_copies_whole(self):
        self.assertIsNone(ms.read('settings'))
        ms.write('settings', b'one')
        self.assertEqual(ms.read('settings'), b'one')
        area = self.assert_outside_kept('settings')
        slots = [ms.classify('settings', area[i * ms.SLOT:(i + 1) * ms.SLOT]) for i in (0, 1)]
        self.assertEqual([s[0] for s in slots], ['valid', 'valid'])  # a first write leaves two copies
        for n in range(2, 7):
            ms.write('settings', b'v%d' % n)
            self.assertEqual(ms.read('settings'), b'v%d' % n)
            area = self.assert_outside_kept('settings')
            slots = [ms.classify('settings', area[i * ms.SLOT:(i + 1) * ms.SLOT]) for i in (0, 1)]
            # Both copies equal after every write (2026-11-14 review: one copy per change let damage
            # to it bring back the state before).
            self.assertEqual([s[2] for s in slots], [b'v%d' % n] * 2)
        self.assertIsNone(ms.read('wifi'))  # one record never reads another's
        ms.write('wifi', b'{}')
        self.assertEqual((ms.read('wifi'), ms.read('settings')), (b'{}', b'v6'))

    def test_every_stop_of_a_write_leaves_the_old_or_the_new_payload(self):
        real_pwrite, real_fsync = os.pwrite, os.fsync
        histories = ([], [b'first'], [b'first', b'second'], [b'first', b'second', b'third'])
        cases = 0
        for history in histories:
            for step in range(12):
                for cut in (None, 0, 1, ms.SECTOR - 1, ms.SECTOR, 3 * ms.SECTOR + 7):
                    self.misc.write_bytes(self.outside[:ms.AREAS['settings']] + bytes(ms.END - ms.AREAS['settings']))
                    for payload in history:
                        ms.write('settings', payload)
                    before = history[-1] if history else None
                    calls = [0]

                    def pwrite(fd, data, offset, cut=cut, step=step):
                        calls[0] += 1
                        if calls[0] - 1 == step:
                            if cut is not None:  # a write the power cut short: a prefix of it landed
                                real_pwrite(fd, data[:cut], offset)
                            raise Crash()
                        return real_pwrite(fd, data, offset)

                    def fsync(fd, step=step):
                        calls[0] += 1
                        if calls[0] - 1 == step:
                            raise Crash()
                        return real_fsync(fd)
                    with mock.patch('os.pwrite', pwrite), mock.patch('os.fsync', fsync):
                        try:
                            ms.write('settings', b'new-' + b'x' * 900)
                            finished = True
                        except Crash:
                            finished = False
                    seen = ms.read('settings')
                    if finished:
                        self.assertEqual(seen, b'new-' + b'x' * 900)
                    elif before is None:
                        self.assertIn(seen, (None, b'new-' + b'x' * 900), (history, step, cut))
                    else:
                        self.assertIn(seen, (before, b'new-' + b'x' * 900), (history, step, cut))
                    self.assert_outside_kept('settings')
                    # And the next write after the stop is whole.
                    ms.write('settings', b'after')
                    self.assertEqual(ms.read('settings'), b'after')
                    cases += 1
        self.assertGreater(cases, 250)

    def test_damage_to_either_copy_leaves_the_newest_and_to_both_is_damaged(self):
        ms.write('wifi', b'{}')  # as on the phone: wifi.py writes its record at the first start
        first = ms.AREAS['settings']
        for spot in (ms.SECTOR, 3, 40):  # the payload, the magic, the hash
            for index in (0, 1):
                with self.subTest(spot=spot, index=index):
                    ms.clear('settings')
                    ms.write('settings', b'no password')
                    ms.write('settings', b'password')
                    data = bytearray(self.misc.read_bytes())
                    data[first + index * ms.SLOT + spot] ^= 0xff
                    self.misc.write_bytes(bytes(data))
                    self.assertEqual(ms.read('settings'), b'password')
                    data[first + (1 - index) * ms.SLOT + spot] ^= 0xff
                    self.misc.write_bytes(bytes(data))
                    with self.assertRaises(ms.Damaged):  # never "no record", never the older one
                        ms.read('settings')
        ms.clear('settings')
        self.assertIsNone(ms.read('settings'))
        # The bound: both headers of the only record in the area lost cannot be told from another's
        # bytes on a phone that never had one; with the Wi-Fi record there, they can.
        ms.clear('wifi')
        ms.write('settings', b'password')
        data = bytearray(self.misc.read_bytes())
        for index in (0, 1):
            data[first + index * ms.SLOT + 3] ^= 0xff
        self.misc.write_bytes(bytes(data))
        self.assertIsNone(ms.read('settings'))

    def test_a_copy_that_cannot_be_read_leaves_the_other(self):
        ms.write('settings', b'whole')
        real_pread = os.pread
        first = ms.AREAS['settings']
        for bad in ({first}, {first + ms.SLOT}, {first, first + ms.SLOT}):
            def pread(fd, size, offset, bad=bad):
                if offset in bad:
                    raise OSError(5, 'Input/output error')
                return real_pread(fd, size, offset)
            with self.subTest(bad=sorted(bad)), mock.patch('os.pread', pread):
                if len(bad) == 1:
                    self.assertEqual(ms.read('settings'), b'whole')
                else:
                    with self.assertRaises(OSError) as caught:
                        ms.read('settings')
                    self.assertNotIsInstance(caught.exception, ms.Foreign)

    def test_bytes_of_another_program_are_never_written_over_and_damage_only_in_an_area_of_ours(self):
        data = bytearray(self.misc.read_bytes())
        data[ms.AREAS['wifi'] + 100] = 7
        self.misc.write_bytes(bytes(data))
        # Nothing of this recovery's in the area: another's bytes, no record (2026-11-14 review: read
        # as damage, a stray byte asked to erase userdata on a phone that never had a password), and
        # the area is never taken while they are there, by either record (2026-11-14 review: the
        # first Wi-Fi write turned another's bytes in the settings slots into damage).
        self.assertEqual((ms.read('wifi'), ms.read('settings')), (None, None))
        for name in ms.AREAS:
            with self.assertRaises(ms.Foreign):
                ms.write(name, b'x')
        self.assertEqual(self.misc.read_bytes(), bytes(data))
        # Once the area is this recovery's, anything else in it is damage, never "no record"
        # (2026-11-14 review: both headers lost read a password as none), and the next write repairs it.
        ms.clear('wifi')
        ms.write('wifi', b'{}')
        ms.write('settings', b'mine')
        first = ms.AREAS['settings']
        data = bytearray(self.misc.read_bytes())
        for index in (0, 1):
            data[first + index * ms.SLOT:first + index * ms.SLOT + 8] = b'OTHERPRG'
        self.misc.write_bytes(bytes(data))
        with self.assertRaises(ms.Damaged):
            ms.read('settings')
        ms.write('settings', b'again')
        self.assertEqual(ms.read('settings'), b'again')
        # A header cut inside its name is a copy of this recovery's cut short, never another's.
        self.assertEqual(ms.classify('settings', ms.MAGIC + b'set' + bytes(ms.SLOT - 11)), ('torn',))
        self.assertEqual(ms.field('settings', 'theme'), '')
        ms.write('settings', b'{"theme": "aurora"}')
        self.assertEqual(ms.field('settings', 'theme'), 'aurora')

    def test_with_a_slot_unreadable_nothing_is_decided_on_ownership_or_repaired(self):
        ms.write('wifi', b'{}')
        ms.write('settings', b'x')
        real_pread, real_pwrite = os.pread, os.pwrite
        wifi = ms.AREAS['wifi']
        bad = {wifi}

        def pread(fd, size, offset):
            if offset in bad:
                raise OSError(5, 'Input/output error')
            return real_pread(fd, size, offset)

        def pwrite(fd, data, offset):
            bad.discard(offset)  # as the eMMC does: a sector written is mapped anew
            return real_pwrite(fd, data, offset)
        with mock.patch('os.pread', pread), mock.patch('os.pwrite', pwrite):
            self.assertEqual(ms.state('settings'), (b'x', True, False))
            # Never past a copy of its own that cannot be read: it may be the newer one (2026-11-14
            # review: the older was written back as the newest, a password set read as none).
            with self.assertRaises(OSError) as refused:
                ms.write('wifi', b'y')
            self.assertNotIsInstance(refused.exception, ms.Foreign)
        self.assertEqual(ms.state('wifi'), (b'{}', True, True))
        del pwrite
        # An area not this recovery's yet is never taken while a slot of it cannot be read: it may
        # hold another's bytes (2026-11-14 review).
        ms.clear('wifi')
        ms.clear('settings')
        settings_first = ms.AREAS['settings']

        def unread(fd, size, offset):
            if offset == settings_first:
                raise OSError(5, 'Input/output error')
            return real_pread(fd, size, offset)
        with mock.patch('os.pread', unread), self.assertRaises(ms.Foreign):
            ms.write('wifi', b'z')  # not taken: the settings stay in the older store
        self.assertIsNone(ms.read('wifi'))

    def test_a_write_stopped_between_the_copies_is_told(self):
        ms.write('settings', b'old')
        self.assertEqual(ms.state('settings'), (b'old', True, True))
        real_pwrite, calls = os.pwrite, [0]

        def pwrite(fd, data, offset):
            calls[0] += 1
            if calls[0] == 3:  # the header of the second copy
                raise Crash()
            return real_pwrite(fd, data, offset)
        with mock.patch('os.pwrite', pwrite), self.assertRaises(Crash):
            ms.write('settings', b'new')
        self.assertEqual(ms.state('settings'), (b'new', False, True))
        ms.write('settings', b'new')
        self.assertEqual(ms.state('settings'), (b'new', True, True))

    def test_limits_and_a_misc_that_cannot_hold_or_be_read(self):
        with self.assertRaises(ValueError):
            ms.write('settings', b'x' * (ms.PAYLOAD_MAX + 1))
        ms.write('settings', b'x' * ms.PAYLOAD_MAX)
        self.assertEqual(len(ms.read('settings')), ms.PAYLOAD_MAX)
        with self.assertRaises(ValueError):
            ms.read('other')
        small = self.tmp / 'small'
        small.write_bytes(bytes(ms.END - 1))
        with mock.patch.dict(os.environ, {'A22X_MISC': str(small)}):
            with self.assertRaises(OSError):
                ms.read('settings')
        with mock.patch.dict(os.environ, {'A22X_MISC': str(self.tmp / 'none')}):
            with self.assertRaises(OSError):
                ms.read('settings')

    def test_a_readback_that_differs_is_a_failed_write(self):
        real_pread = os.pread

        def pread(fd, size, offset):
            data = real_pread(fd, size, offset)
            return data[:-1] + b'\x01' if size == ms.SLOT and calls.append(1) is None and len(calls) > 2 else data
        calls = []
        with mock.patch('os.pread', pread):
            with self.assertRaises(OSError):
                ms.write('settings', b'z')

    def test_the_real_partition_is_only_the_internal_one(self):
        with mock.patch.dict(os.environ, {'A22X_MISC': str(ms.MISC)}), \
                mock.patch.object(ms.os.path, 'realpath', return_value='/dev/sda4'):
            with self.assertRaises(OSError):
                ms.read('settings')


if __name__ == '__main__':
    unittest.main()
