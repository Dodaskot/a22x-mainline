"""userdata on every SM-A226B, 64 GB or 128 GB (2026-11-13): Samsung's PIT gives it the rest of the
eMMC, so its admitted sizes follow the eMMC's size instead of one phone's."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qualification  # noqa: E402
import storage  # noqa: E402

GB64, GB128 = 122142720, 244285440


class UserdataSizes(unittest.TestCase):
    def test_the_rule_matches_the_64_gb_phone(self):
        self.assertEqual(storage.userdata_sizes(GB64), (53393489920, (GB64 - 33 - 17850368) * 512))

    def test_on_a_computer_any_size_an_admitted_emmc_can_have(self):
        # Off the phone its eMMC is unknown: any size the phone rule admits on some admitted eMMC,
        # a 128 GB phone's too (2026-11-14 review: only the 64 GB phone's were, so every 128 GB
        # phone's snapshot was refused on a computer).
        with mock.patch.object(qualification, 'PHONE', Path('/nonexistent/device.json')):
            for size in storage.userdata_sizes(GB64) + storage.userdata_sizes(GB128):
                self.assertTrue(qualification.size_admitted('userdata', size))
            self.assertTrue(qualification.size_admitted('userdata', qualification.GEOMETRY['userdata'][1]))
            self.assertFalse(qualification.size_admitted('userdata', storage.userdata_sizes(GB64)[0] + 1))
            # A 32 GB userdata or one larger than a 128 GB eMMC fits no admitted eMMC.
            self.assertFalse(qualification.size_admitted('userdata', 32 << 30))
            self.assertFalse(qualification.size_admitted('userdata', (storage.MAX_DISK - storage.USERDATA_START) * 512))
            self.assertFalse(qualification.size_admitted('boot', 4096))
            self.assertTrue(qualification.size_admitted('boot', qualification.GEOMETRY['boot'][1]))

    def test_on_the_phone_its_own_emmc_decides(self):
        disk = mock.Mock(is_file=lambda: True, read_text=lambda: str(GB128))
        with mock.patch.object(qualification, 'PHONE', Path(__file__)), mock.patch.object(qualification, 'DISK', disk):
            self.assertTrue(qualification.size_admitted('userdata', storage.userdata_sizes(GB128)[1]))
            self.assertTrue(qualification.size_admitted('userdata', storage.userdata_sizes(GB128)[0]))
            # The 64 GB phone's stock size is no userdata of a 128 GB eMMC.
            self.assertFalse(qualification.size_admitted('userdata', storage.userdata_sizes(GB64)[0]))

    def test_the_end_of_userdata_may_differ_a_little_on_another_emmc(self):
        # 2026-11-14 review: only exactly 8192 sectors short of the end was admitted.
        self.assertTrue(storage.userdata_fits((GB128 - 4096 - storage.USERDATA_START) * 512, GB128))
        self.assertTrue(storage.userdata_fits((GB128 - 33 - storage.USERDATA_START) * 512, GB128))
        self.assertFalse(storage.userdata_fits((GB128 - 32 - storage.USERDATA_START) * 512, GB128))
        self.assertFalse(storage.userdata_fits((GB128 - 20000 - storage.USERDATA_START) * 512, GB128))
        self.assertFalse(storage.userdata_fits((GB128 - 4096 - storage.USERDATA_START) * 512 + 1, GB128))
        self.assertFalse(storage.userdata_fits(10 << 30, 60_000_000))

if __name__ == '__main__':
    unittest.main()
