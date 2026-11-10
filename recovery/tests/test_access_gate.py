"""A computer gets a shell only after the password is confirmed on the phone."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
import access_gate


class Gate(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-gate-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.gate = access_gate.Gate(self.root / 'access', self.root / 'settings.json', poll=0.01)

    def settings(self, **record):
        (self.root / 'settings.json').write_text(json.dumps(record))

    def answer(self, reply):
        def run():
            for _ in range(500):
                requests = sorted(self.gate.base.glob('request-*.json'))
                if requests:
                    reply(requests[0])
                    return
                time.sleep(0.005)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join)

    def test_without_a_password_a_computer_is_let_in(self):
        self.settings(theme='aurora')
        self.assertTrue(self.gate.allow('ssh', 'x', timeout=0))
        self.assertEqual(list(self.gate.base.glob('request-*')), [])

    def test_the_choice_to_stop_asking_is_respected(self):
        self.settings(hash='abc', salt='00', access=0)
        self.assertTrue(self.gate.allow('ssh', 'x', timeout=0))

    def test_with_a_password_nothing_happens_until_the_phone_answers(self):
        self.settings(hash='abc', salt='00')
        self.assertFalse(self.gate.allow('ssh', 'x', timeout=0.05))
        self.assertEqual(list(self.gate.base.glob('request-*')), [], 'A timed out request must not stay behind')

    def test_a_grant_opens_the_door_and_keeps_it_open(self):
        self.settings(hash='abc', salt='00')
        self.answer(lambda request: (self.gate.base / 'granted').write_text('1'))
        self.assertTrue(self.gate.allow('ssh', '172.16.42.2', timeout=3))
        self.assertTrue(self.gate.allow('uart', '', timeout=0))

    def test_a_refusal_closes_at_once(self):
        self.settings(hash='abc', salt='00')
        self.answer(lambda request: (self.gate.base / request.name.replace('request-', 'denied-')).write_text('1'))
        started = time.monotonic()
        self.assertFalse(self.gate.allow('ssh', 'x', timeout=5))
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(list(self.gate.base.iterdir()), [], 'The refusal must be cleaned up')

    def test_a_request_names_the_channel_and_the_computer(self):
        self.settings(hash='abc', salt='00')
        seen = {}
        self.answer(lambda request: (seen.update(json.loads(request.read_text())), (self.gate.base / 'granted').write_text('1')))
        self.gate.allow('ssh', '172.16.42.2', timeout=3)
        self.assertEqual((seen['channel'], seen['peer']), ('ssh', '172.16.42.2'))

    def test_settings_not_written_yet_are_waited_for(self):
        def later():
            time.sleep(0.05)
            self.settings(hash='abc', salt='00')
        thread = threading.Thread(target=later)
        thread.start()
        self.addCleanup(thread.join)
        self.assertTrue(self.gate.required(wait=2))

    def test_settings_that_never_appear_do_not_lock_the_console_out(self):
        self.assertFalse(self.gate.required(wait=0.02))

    def test_a_peer_cannot_write_outside_a_short_line(self):
        self.settings(hash='abc', salt='00')
        self.gate.allow('ssh', 'x' * 500 + '\n../../etc', timeout=0.02)
        seen = {}
        self.answer(lambda request: (seen.update(json.loads(request.read_text())), (self.gate.base / 'granted').write_text('1')))
        self.gate.allow('ssh', 'x' * 500 + '\n../../etc', timeout=3)
        self.assertLessEqual(len(seen['peer']), 64)
        self.assertNotIn('\n', seen['peer'])
