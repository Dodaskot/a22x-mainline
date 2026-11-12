"""A restart that came back to the recovery is tried once more (restart_intent.py, 2026-11-10)."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import restart_intent as ri  # noqa: E402

T0 = 1794310000


class RestartIntent(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-intent-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_quick_return_without_a_system_start_is_restarted_once(self):
        """The preloader started twice and LK came back here about 30 seconds later."""
        self.assertEqual(ri.write(self.root, 'normal', T0, mounts=41), 0)
        self.assertEqual(ri.check(self.root, T0 + 31, mounts=41), {'retry': 'normal', 'reason': 'came-back', 'age': 31})
        # /init writes the intent again before that restart; the retry keeps its count.
        self.assertEqual(ri.write(self.root, 'normal', T0 + 33, mounts=41), 1)
        # Back once more: no loop, and the intent is gone.
        self.assertEqual(ri.check(self.root, T0 + 64, mounts=41)['retry'], None)
        self.assertFalse((self.root / ri.RELATIVE).exists())

    def test_a_system_that_started_counts_its_mount(self):
        """Linux mounted userdata, then came back on purpose quickly (or Download flashed and
        the system started): no retry, however soon."""
        ri.write(self.root, 'normal', T0, mounts=41)
        self.assertEqual(ri.check(self.root, T0 + 25, mounts=42), {'retry': None, 'reason': 'system-started'})
        self.assertFalse((self.root / ri.RELATIVE).exists())
        ri.write(self.root, 'download', T0, mounts=42)
        self.assertEqual(ri.check(self.root, T0 + 40, mounts=43)['reason'], 'system-started')

    def test_a_later_start_is_a_return_on_purpose(self):
        ri.write(self.root, 'normal', T0, mounts=41)
        self.assertEqual(ri.check(self.root, T0 + 600, mounts=41)['reason'], 'old')
        self.assertFalse((self.root / ri.RELATIVE).exists())
        self.assertEqual(ri.check(self.root, T0 + 601, mounts=41), {'retry': None, 'reason': 'none'})

    def test_without_a_mount_count_nothing_is_retried(self):
        """Recovery 139 wrote no count, and an Android userdata has no ext4 superblock."""
        path = self.root / ri.RELATIVE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'target': 'download', 'written_utc': T0, 'attempts': 0}))
        self.assertEqual(ri.check(self.root, T0 + 20, mounts=41)['reason'], 'no-mount-count')
        ri.write(self.root, 'normal', T0, mounts=None)
        self.assertEqual(ri.check(self.root, T0 + 20, mounts=None)['retry'], None)

    def test_download_too_and_a_clock_that_went_back(self):
        ri.write(self.root, 'download', T0, mounts=7)
        self.assertEqual(ri.check(self.root, T0 + 20, mounts=7)['retry'], 'download')
        ri.write(self.root, 'normal', T0, mounts=7)
        self.assertEqual(ri.check(self.root, T0 - 5, mounts=7)['retry'], None)  # an impossible age is not trusted

    def test_a_new_restart_after_the_window_starts_counting_again(self):
        ri.write(self.root, 'normal', T0, mounts=9)
        ri.check(self.root, T0 + 30, mounts=9)
        self.assertEqual(ri.write(self.root, 'normal', T0 + 3600, mounts=9), 0)
        self.assertEqual(ri.check(self.root, T0 + 3630, mounts=9)['retry'], 'normal')

    def test_damaged_or_foreign_files_are_ignored(self):
        path = self.root / ri.RELATIVE
        path.parent.mkdir(parents=True)
        for text in ('{', '[]', json.dumps({'target': 'recovery', 'written_utc': T0, 'attempts': 0, 'userdata_mounts': 1}),
                     json.dumps({'target': 'normal', 'written_utc': True, 'attempts': 0, 'userdata_mounts': 1}),
                     json.dumps({'target': 'normal', 'written_utc': T0, 'attempts': 0, 'userdata_mounts': '1'})):
            path.write_text(text)
            self.assertEqual(ri.check(self.root, T0 + 1, mounts=1), {'retry': None, 'reason': 'none'})
            self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            ri.write(self.root, 'poweroff', T0)

    def test_the_mount_count_comes_from_the_ext4_superblock(self):
        device = self.root / 'userdata'
        block = bytearray(4096)
        block[1024 + 0x34:1024 + 0x36] = (1234).to_bytes(2, 'little')
        block[1024 + 0x38:1024 + 0x3A] = (0xEF53).to_bytes(2, 'little')
        device.write_bytes(bytes(block))
        self.assertEqual(ri.userdata_mounts(device), 1234)
        block[1024 + 0x38] = 0
        device.write_bytes(bytes(block))
        self.assertIsNone(ri.userdata_mounts(device))  # not ext4 (Android's f2fs, an encrypted volume)
        self.assertIsNone(ri.userdata_mounts(self.root / 'missing'))

    def test_android_is_told_apart_by_the_metadata_count(self):
        """2026-11-10: Android's userdata is encrypted f2fs; its first-stage init mounts /metadata (ext4)."""
        def ext4(name, count):
            block = bytearray(4096)
            block[1024 + 0x34:1024 + 0x36] = count.to_bytes(2, 'little')
            block[1024 + 0x38:1024 + 0x3A] = (0xEF53).to_bytes(2, 'little')
            (self.root / name).write_bytes(bytes(block))
            return self.root / name
        f2fs = self.root / 'f2fs'
        f2fs.write_bytes(bytes(4096))
        metadata = ext4('metadata', 41)
        self.assertEqual(ri.system_mounts(f2fs, metadata), ('metadata', 41))
        self.assertEqual(ri.system_mounts(ext4('linux', 7), metadata), ('userdata', 7))
        self.assertEqual(ri.system_mounts(f2fs, f2fs), (None, None))
        # Came back before Android mounted /metadata: once more. Android started: no retry.
        ri.write(self.root, 'normal', T0, 41, 'metadata')
        self.assertEqual(ri.check(self.root, T0 + 40, 41, 'metadata')['retry'], 'normal')
        ri.write(self.root, 'normal', T0 + 100, 41, 'metadata')
        self.assertEqual(ri.check(self.root, T0 + 130, 42, 'metadata')['reason'], 'system-started')
        # A count of another volume is not compared.
        ri.write(self.root, 'normal', T0 + 200, 41, 'metadata')
        self.assertEqual(ri.check(self.root, T0 + 230, 41, 'userdata')['reason'], 'no-mount-count')

    def test_the_age_comes_from_the_hardware_clock(self):
        rtc = self.root / 'since_epoch'
        rtc.write_text('3686515200\n')  # what this kernel reads after Android ran: 2086
        self.assertEqual(ri.rtc_now(rtc), 3686515200)
        self.assertGreater(ri.rtc_now(self.root / 'missing'), 0)


class InitWiring(unittest.TestCase):
    """The recovery's /init checks once per start, before the interface, and writes the intent
    right before each restart into the system or Download."""
    def test_order(self):
        text = (ROOT / 'ramdisk/init').read_text()
        check, ui, write, power = (text.index('restart_intent.py check'), text.index('/bin/recovery-ui --device'),
                                   text.index('restart_intent.py write "$target"'), text.index('/bin/recovery-power "$target"'))
        self.assertLess(check, ui)
        self.assertLess(write, power)
        self.assertIn('intent.done', text[check - 200:check])  # once per start
        self.assertIn('if [ -n "${auto_rc:-}" ]; then\n        rc=$auto_rc\n        auto_rc=\n    else', text)
        self.assertIn('case "$target" in normal|download)', text)
        # On the phone the retry met the Wi-Fi join, which holds the operation lock: no join
        # before a retry, and the checks wait out a lock held for a moment.
        self.assertIn('if [ -z "${auto_rc:-}" ] && [ ! -e /run/a22x-recovery/wifi-auto.started ]', text)
        self.assertIn('busy_wait /run/a22x-recovery/power-install.json', text)
        self.assertIn('busy_wait /run/a22x-recovery/power-systems.json', text)
        self.assertLess(text.index('busy_wait() {'), text.index('busy_wait /run/a22x-recovery/power-install.json'))


class TorchOff(unittest.TestCase):
    """torch_off from /init, run in a shell on a folder of LEDs: the flashlight goes dark before the
    power action (2026-11-11), and no other LED is touched."""
    def test_only_the_flash_led_goes_dark_before_the_power_action(self):
        import subprocess
        import tempfile
        text = (ROOT / 'ramdisk/init').read_text()
        self.assertLess(text.index('torch_off() {'), text.index('                torch_off\n'))
        self.assertLess(text.index('                torch_off\n'), text.index('/bin/recovery-power "$target"'))
        function = text[text.index('torch_off() {'):text.index('\n}\n', text.index('torch_off() {')) + 3]
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            leds = Path(tmp)
            for name, value in (('white:flash', '9'), ('torch-1', '3'), ('green:status', '1')):
                (leds / name).mkdir()
                (leds / name / 'brightness').write_text(value + '\n')
            r = subprocess.run(['sh', '-c', function + 'torch_off "$1"; echo rc=$?', 'sh', str(leds)], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.stdout.strip(), r.stderr), ('rc=0', ''))
            self.assertEqual([(leds / n / 'brightness').read_text().strip() for n in ('white:flash', 'torch-1', 'green:status')], ['0', '0', '1'])
            # A phone without such an LED: nothing to do, and no complaint.
            r = subprocess.run(['sh', '-c', function + 'torch_off "$1"; echo rc=$?', 'sh', str(leds / 'none')], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.stdout.strip(), r.stderr), ('rc=0', ''))


class BusyWait(unittest.TestCase):
    """busy_wait from /init, run in a shell: it retries only while the lock is busy."""
    def run_sh(self, body):
        import subprocess
        text = (ROOT / 'ramdisk/init').read_text()
        function = text[text.index('busy_wait() {'):text.index('\n}\n', text.index('busy_wait() {')) + 3]
        script = function + 'sleep() { :; }\n' + body
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=30)

    def test_retries_while_busy_only(self):
        out = self.root / 'out.json'
        counter = self.root / 'n'
        body = ('step() { n=$(cat %s 2>/dev/null || echo 0); n=$((n + 1)); echo $n > %s; '
                '[ $n -ge 3 ] && { echo ok; return 0; }; echo OPERATION_BUSY; return 2; }\n'
                'busy_wait %s step && echo passed\n') % (counter, counter, out)
        r = self.run_sh(body)
        self.assertEqual((r.stdout.strip(), counter.read_text().strip()), ('passed', '3'))
        counter.unlink()
        r = self.run_sh('step() { echo OTHER_ERROR; return 2; }\nbusy_wait %s step || echo refused\n' % out)
        self.assertEqual(r.stdout.strip(), 'refused')
        self.assertEqual(out.read_text().strip(), 'OTHER_ERROR')

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-busy-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)


if __name__ == '__main__':
    unittest.main()
