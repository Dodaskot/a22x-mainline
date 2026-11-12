"""What Android asks of the recovery through misc (bcb.py): read, done on arrival, or forgotten at
the owner's word (2026-11-11)."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bcb


def message(command=b'', recovery=b'', rest=0x5a):
    """A bootloader message as Android writes it, and what lies after it in misc."""
    head = command.ljust(32, b'\0') + bytes(32) + recovery.ljust(768, b'\0') + bytes(32) + bytes(1184)
    assert len(head) == bcb.SIZE
    return head + bytes([rest]) * 4096


class Request(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-bcb-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.misc = self.root / 'misc'

    def run_helper(self, command):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bcb.main(['--fixture', str(self.root), command])
        return code, json.loads(out.getvalue())

    def test_nothing_asked(self):
        self.misc.write_bytes(message())
        code, found = self.run_helper('read')
        self.assertEqual((code, found['command'], found['wipe'], found['cleared']), (0, '', False, False))
        self.assertEqual(self.run_helper('forget'), (0, {**found}))
        self.assertEqual(self.misc.read_bytes(), message())

    def test_a_plain_start_of_the_recovery_is_done_by_arriving(self):
        self.misc.write_bytes(message(b'boot-recovery', b'recovery\n'))
        code, found = self.run_helper('arrive')
        self.assertEqual((code, found['command'], found['wipe'], found['cleared']), (0, 'boot-recovery', False, True))
        self.assertEqual(self.misc.read_bytes(), message())  # what lies after the message stays

    def test_a_reset_from_the_settings_stays_until_it_is_done_or_forgotten(self):
        """Android's Factory data reset: --wipe_data. Arriving here does not clear it."""
        asked = message(b'boot-recovery', b'recovery\n--wipe_data\n--reason=MasterClearConfirm\n--locale=uk_UA\n')
        self.misc.write_bytes(asked)
        code, found = self.run_helper('arrive')
        self.assertEqual((code, found['wipe'], found['cleared'], found['reason']), (0, True, False, 'MasterClearConfirm'))
        self.assertIn('--wipe_data', found['recovery'])
        self.assertEqual(self.misc.read_bytes(), asked)
        # The owner's word: nothing is erased, the request is gone, and what follows it in misc stays.
        code, found = self.run_helper('forget')
        self.assertEqual((code, found['command'], found['wipe'], found['cleared']), (0, '', False, True))
        self.assertEqual(self.misc.read_bytes(), message())

    def test_data_that_cannot_be_opened_asks_with_a_prompt(self):
        self.misc.write_bytes(message(b'boot-recovery', b'recovery\n--prompt_and_wipe_data\n--reason=fs_mgr_mount_all:M02R\n'))
        code, found = self.run_helper('read')
        self.assertEqual((code, found['wipe'], found['reason']), (0, True, 'fs_mgr_mount_all:M02R'))
        self.assertIn('--prompt_and_wipe_data', found['recovery'])

    def test_forget_leaves_other_requests_alone(self):
        asked = message(b'boot-fastboot', b'recovery\n--fastboot\n')
        self.misc.write_bytes(asked)
        code, found = self.run_helper('forget')
        self.assertEqual((code, found['command'], found['cleared']), (0, 'boot-fastboot', False))
        self.assertEqual(self.misc.read_bytes(), asked)

    def test_a_phone_without_misc_asks_nothing(self):
        code, found = self.run_helper('read')
        self.assertEqual((code, found['command'], found['wipe']), (0, '', False))


if __name__ == '__main__':
    unittest.main()
