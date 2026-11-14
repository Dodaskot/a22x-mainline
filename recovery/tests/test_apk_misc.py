"""The Android app and the recovery share their records in misc (2026-11-14): what the app's Java
code (apk/src/.../MiscRecords.java) writes is read by misc_store, settings and wifi here, and what
they write is read by it. Needs a JDK; skipped without one. The app's own checks: MiscRecordsTest."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import misc_store  # noqa: E402
import wifi  # noqa: E402

SECRET = {'hash': 'ab' * 32, 'language': 'en', 'salt': '0f' * 16, 'theme': 'graphite', 'brightness': 61}


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'no JDK on this computer')
class AppAndRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classes = Path(tempfile.mkdtemp(prefix='a22x-apkmisc-', dir=os.environ.get('TMPDIR')))
        src, test = ROOT / 'apk/src/org/a22x/installer', ROOT / 'apk/test/org/a22x/installer'
        subprocess.run(['javac', '-nowarn', '--release', '8', '-d', str(cls.classes),
                        *(str(src / (n + '.java')) for n in ('Json', 'Root', 'Scripts', 'MiscRecords', 'Handoff')),
                        str(test / 'MiscRecordsMain.java')], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.classes, True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='a22x-apkmisc-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.misc = self.tmp / 'misc'
        self.android = os.urandom(misc_store.AREAS['settings'])  # misc below the area: never touched
        self.misc.write_bytes(self.android + bytes(misc_store.END - len(self.android)))
        patcher = mock.patch.dict(os.environ, {'A22X_MISC': str(self.misc)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def app(self, *args):
        """What the app does to the area, as on the phone: the area out of misc, changed, put back."""
        start = misc_store.AREAS['settings']
        data = self.misc.read_bytes()
        (self.tmp / 'in').write_bytes(data[start:])
        done = subprocess.run(['java', '-cp', str(self.classes), 'org.a22x.installer.MiscRecordsMain',
                               str(self.tmp / 'in'), str(self.tmp / 'out'), *args], check=True, capture_output=True, text=True)
        self.misc.write_bytes(data[:start] + (self.tmp / 'out').read_bytes())
        self.assertEqual(self.misc.read_bytes()[:start], self.android)
        return done.stdout.split('\n')

    def wifi_list(self):
        return wifi.networks_of(json.loads(misc_store.read('wifi')))

    def test_the_app_changes_language_and_zone_and_keeps_the_password(self):
        misc_store.write('settings', (json.dumps(SECRET, sort_keys=True) + '\n').encode())
        misc_store.write('wifi', b'{"networks": [], "schema": "a22x.wifi.saved.v1"}\n')
        self.assertIn('settings: written', self.app('uk', 'Europe/Kyiv'))
        record = json.loads(misc_store.read('settings'))
        self.assertEqual(record, {**SECRET, 'language': 'uk', 'timezone': 'Europe/Kyiv'})
        import settings
        self.assertEqual(settings.checked(record), record)
        payload, twice, readable = misc_store.state('settings')
        self.assertTrue(twice and readable)  # both copies, as the recovery writes them
        self.assertIn('settings: unchanged', self.app('uk', 'Europe/Kyiv'))

    def test_one_list_of_networks_and_the_newer_password_wins(self):
        misc_store.write('settings', b'{}\n')
        misc_store.write('wifi', b'{"networks": [], "schema": "a22x.wifi.saved.v1"}\n')
        # Saved in the app: the same WPA key the recovery derives.
        self.assertIn('wifi: written', self.app('', '', 'Home', 'from-the-app', '1000'))
        self.assertEqual(self.wifi_list()[0]['psk'], wifi.psk(b'Home', 'from-the-app'))
        # Changed in the recovery later: an older entry from the app never replaces it.
        with mock.patch.object(wifi, '_settings_volumes', lambda mode, action: ('ok', action(wifi.MISC))), \
                mock.patch.object(wifi, 'SAVED_RAM', self.tmp / 'ram.json'), mock.patch.object(time, 'time', return_value=2000):
            self.assertTrue(wifi.remember(b'Home', wifi.psk(b'Home', 'from-the-recovery')))
        self.assertIn('wifi: kept', self.app('', '', 'Home', 'stale-from-the-app', '1500'))
        self.assertEqual(self.wifi_list()[0]['psk'], wifi.psk(b'Home', 'from-the-recovery'))
        # A newer one from the app replaces it, and other networks stay.
        self.assertIn('wifi: written', self.app('', '', 'Work', 'work-pass-1', '2500'))
        self.assertIn('wifi: written', self.app('', '', 'Home', 'newest-from-app', '3000'))
        listed = {bytes.fromhex(n['ssid_hex']): n['psk'] for n in self.wifi_list()}
        self.assertEqual(listed, {b'Home': wifi.psk(b'Home', 'newest-from-app'), b'Work': wifi.psk(b'Work', 'work-pass-1')})
        # A hand-over file older than a password saved since does not undo it either.
        with mock.patch.object(wifi, '_settings_volumes', lambda mode, action: ('ok', action(wifi.MISC))), \
                mock.patch.object(wifi, 'SAVED_RAM', self.tmp / 'ram.json'):
            self.assertTrue(wifi.remember(b'Home', wifi.psk(b'Home', 'old-handoff'), when=2900))
        self.assertEqual({bytes.fromhex(n['ssid_hex']): n['psk'] for n in self.wifi_list()}[b'Home'],
                         wifi.psk(b'Home', 'newest-from-app'))

    def test_the_app_never_makes_the_first_records(self):
        # Before the recovery's first start (it copies an older release's settings, a password with
        # them) and with an older recovery: the app uses the hand-over file instead.
        before = self.misc.read_bytes()
        self.assertEqual(self.app('uk', 'Europe/Kyiv', 'Home', 'some-pass1', '1000')[:2], ['settings: none', 'wifi: none'])
        self.assertEqual(self.misc.read_bytes(), before)
        data = bytearray(before)
        data[misc_store.AREAS['wifi'] + 5] = 9  # another program's byte in the area
        self.misc.write_bytes(bytes(data))
        self.assertEqual(self.app('uk', '', 'Home', 'some-pass1', '1000')[:2], ['settings: none', 'wifi: none'])
        self.assertEqual(self.misc.read_bytes(), bytes(data))

    def test_damaged_settings_are_left_to_the_recovery(self):
        misc_store.write('settings', (json.dumps(SECRET) + '\n').encode())
        misc_store.write('wifi', b'{"networks": [], "schema": "a22x.wifi.saved.v1"}\n')
        data = bytearray(self.misc.read_bytes())
        for index in (0, 1):
            data[misc_store.AREAS['settings'] + index * misc_store.SLOT + misc_store.SECTOR] ^= 0xff
        self.misc.write_bytes(bytes(data))
        self.assertIn('settings: Damaged', self.app('uk', 'Europe/Kyiv'))
        self.assertEqual(self.misc.read_bytes(), bytes(data))


if __name__ == '__main__':
    unittest.main()
