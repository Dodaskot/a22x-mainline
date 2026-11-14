#!/usr/bin/env python3
"""Settings an Android app or a Linux system hands to the recovery (2026-11-10)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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
        applied = handoff.apply_record(record, remember=lambda ssid, key: remembered.append((ssid, key)) or True,
                                       load=lambda: {'language': 'en', 'timezone': '', 'theme': 'graphite'},
                                       store=lambda current: stored.append(dict(current)))
        self.assertEqual(remembered, [(b'Open net', ''), (b'Home', wifi.psk(b'Home', 'secret-pass'))])
        self.assertEqual(stored, [{'language': 'uk', 'timezone': 'Europe/Kyiv', 'theme': 'graphite'}])
        self.assertEqual(applied['wifi'], ['Home', 'Open net'])
        self.assertEqual((applied['language'], applied['timezone'], applied['source']), ('uk', 'Europe/Kyiv', 'android-app'))
        self.assertNotIn('secret-pass', json.dumps(applied))

    def test_a_busy_saved_list_is_waited_for_and_what_was_not_saved_is_named(self):
        # 2026-11-14: remember() changes nothing while the list cannot be read; the hand-off is taken
        # already, so it tries again, and names a network it still could not save.
        record = {'schema': 'a22x.handoff.v1', 'wifi': [{'ssid': 'Home', 'password': 'secret-pass'}]}
        answers = [None, None, True]
        with mock.patch.object(handoff.time, 'sleep'):
            applied = handoff.apply_record(record, remember=lambda ssid, key: answers.pop(0),
                                           load=dict, store=lambda current: None)
        self.assertNotIn('wifi_not_saved', applied)
        # A list that could not be written is not tried again (2026-11-14 review).
        calls = []
        with mock.patch.object(handoff.time, 'sleep', side_effect=AssertionError('no wait for a failed write')):
            applied = handoff.apply_record(record, remember=lambda ssid, key: calls.append(ssid) or False,
                                           load=dict, store=lambda current: None)
        self.assertEqual((applied['wifi_not_saved'], len(calls)), (['Home'], 1))

    def test_the_start_waits_for_a_busy_list_a_short_while_in_all(self):
        # 2026-11-14 review: each network waited about 30-60 s, before the interface came up.
        record = {'schema': 'a22x.handoff.v1', 'wifi': [{'ssid': 'Net %d' % i, 'password': 'password%d' % i} for i in range(8)]}
        now, tried = [0.0], []

        def busy(ssid, key):
            tried.append(ssid)
            now[0] += 3  # each try waits for the lock itself
        with mock.patch.object(handoff.time, 'monotonic', lambda: now[0]), \
                mock.patch.object(handoff.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                mock.patch.object(wifi, 'psk', wraps=wifi.psk) as psk:
            applied = handoff.apply_record(record, remember=busy, load=dict, store=lambda current: None)
        # One deadline for all: no network waits past it, nor gets a try of its own after it.
        self.assertLessEqual(now[0], handoff.WAIT_SECONDS + 3)
        self.assertEqual(set(tried), {b'Net 7'})
        self.assertEqual(len(applied['wifi_not_saved']), 8)
        self.assertEqual(psk.call_count, 1)  # derived once, and not for the networks past the deadline

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

    def test_wifi_that_is_no_list_does_not_lose_the_rest(self):
        """2026-11-12 review: an object as "wifi" raised TypeError after the file was gone."""
        for value in ({'ssid': 'Home', 'password': 'secret-pass'}, 7, 'Home'):
            stored = []
            applied = handoff.apply_record({'schema': 'a22x.handoff.v1', 'wifi': value, 'language': 'uk'},
                                           remember=lambda ssid, key: None, load=lambda: {'language': 'en'},
                                           store=lambda current: stored.append(dict(current)))
            self.assertEqual((applied['wifi'], stored), ([], [{'language': 'uk'}]))

    def test_at_most_eight_networks(self):
        record = {'schema': 'a22x.handoff.v1', 'wifi': [{'ssid': 'Net %d' % i, 'password': 'password%d' % i} for i in range(12)]}
        remembered = []
        handoff.apply_record(record, remember=lambda ssid, key: remembered.append(ssid) or True, load=dict, store=lambda c: None)
        self.assertEqual(len(remembered), handoff.MAX_WIFI)


if __name__ == '__main__':
    unittest.main()
