#!/usr/bin/env python3
"""Settings an Android app or a Linux system hands to the recovery (2026-11-10)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoff  # noqa: E402
import wifi  # noqa: E402


class TestHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-handoff-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'a22x-recovery').mkdir()
        self.path = self.root / handoff.RELATIVE

    def write(self, record):
        self.path.write_text(json.dumps(record))

    def test_the_file_is_taken_once_and_removed(self):
        self.assertIsNone(handoff.take(self.root))
        self.write({'schema': 'a22x.handoff.v1', 'language': 'uk'})
        self.assertEqual(handoff.take(self.root)['language'], 'uk')
        self.assertFalse(self.path.exists())
        self.write({'schema': 'something-else'})
        with self.assertRaises(ValueError):
            handoff.take(self.root)
        self.assertFalse(self.path.exists())  # a strange file goes too
        self.path.write_bytes(b'{' + b' ' * (handoff.LIMIT + 10) + b'}')
        with self.assertRaises(ValueError):
            handoff.take(self.root)
        target = self.root / 'elsewhere.json'
        target.write_text(json.dumps({'schema': 'a22x.handoff.v1'}))
        os.symlink(target, self.path)
        self.assertIsNone(handoff.take(self.root))  # a link is never followed
        self.assertTrue(target.exists())

    def test_only_valid_parts_are_applied_and_no_secret_is_reported(self):
        record = {'schema': 'a22x.handoff.v1', 'source': 'android-app',
                  'wifi': [{'ssid': 'Home', 'password': 'secret-pass'}, {'ssid': '', 'password': 'x' * 10},
                           {'ssid': 'Short', 'password': 'short'}, {'ssid': 'Open net', 'password': ''},
                           {'ssid': 'A' * 40, 'password': 'long-enough'}, 'junk'],
                  'language': 'uk', 'timezone': 'Europe/Kyiv'}
        remembered, stored = [], []
        applied = handoff.apply_record(record, remember=lambda ssid, key: remembered.append((ssid, key)),
                                       load=lambda: {'language': 'en', 'timezone': '', 'theme': 'graphite'},
                                       store=lambda current: stored.append(dict(current)))
        self.assertEqual(remembered, [(b'Open net', ''), (b'Home', wifi.psk(b'Home', 'secret-pass'))])
        self.assertEqual(stored, [{'language': 'uk', 'timezone': 'Europe/Kyiv', 'theme': 'graphite'}])
        self.assertEqual(applied['wifi'], ['Home', 'Open net'])
        self.assertEqual((applied['language'], applied['timezone'], applied['source']), ('uk', 'Europe/Kyiv', 'android-app'))
        self.assertNotIn('secret-pass', json.dumps(applied))

    def test_strange_language_and_zone_change_nothing(self):
        stored = []
        handoff.apply_record({'schema': 'a22x.handoff.v1', 'language': 'xx', 'timezone': '../etc/passwd'},
                             remember=lambda *a: None, load=dict, store=stored.append)
        self.assertEqual(stored, [])

    def test_the_clock_only_moves_forward(self):
        moved = []
        record = {'schema': 'a22x.handoff.v1', 'time_utc': 1794306000}
        self.assertEqual(handoff.advance_clock(record, now=1700000000, setter=moved.append), 1794306000)
        self.assertIsNone(handoff.advance_clock(record, now=1794306000 - 60, setter=moved.append))  # close enough
        self.assertIsNone(handoff.advance_clock(record, now=1794400000, setter=moved.append))  # never back
        for strange in (True, '1794306000', 1000, 5000000000):
            self.assertIsNone(handoff.advance_clock({'time_utc': strange}, now=0, setter=moved.append))
        self.assertEqual(moved, [1794306000])

    def test_at_most_eight_networks(self):
        record = {'schema': 'a22x.handoff.v1', 'wifi': [{'ssid': 'Net %d' % i, 'password': 'password%d' % i} for i in range(12)]}
        remembered = []
        handoff.apply_record(record, remember=lambda ssid, key: remembered.append(ssid), load=dict, store=lambda c: None)
        self.assertEqual(len(remembered), handoff.MAX_WIFI)


if __name__ == '__main__':
    unittest.main()
