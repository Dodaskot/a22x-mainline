#!/usr/bin/env python3
"""Recovery Wi-Fi: scan results, keys, the recorded state for the interface, and the
control-socket conversation with a stand-in wpa_supplicant."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wifi as w
REAL_SETTINGS_VOLUMES = w._settings_volumes  # before any test replaces it


class TestText(unittest.TestCase):
    def test_ssid_escapes(self):
        self.assertEqual(w.decode_ssid(r'Caf\xc3\xa9 \"A\\B\"'), 'Café "A\\B"'.encode())
        self.assertEqual(w.decode_ssid('Phone hotspot '), b'Phone hotspot ')
        self.assertEqual(w.decode_ssid(r'tab\there'), b'tab\there')

    def test_security(self):
        self.assertEqual(w.security('[WPA2-PSK-CCMP][ESS]'), ('WPA2', True))
        self.assertEqual(w.security('[WPA-PSK-TKIP][ESS]'), ('WPA', True))
        self.assertEqual(w.security('[RSN-SAE-CCMP][ESS]'), ('WPA3', False))
        self.assertEqual(w.security('[WPA2-PSK+SAE-CCMP][ESS]'), ('WPA2', True))
        self.assertEqual(w.security('[WEP][ESS]'), ('WEP', False))
        self.assertEqual(w.security('[WPA2-EAP-CCMP][ESS]'), ('Enterprise', False))
        self.assertEqual(w.security('[ESS]'), ('Open', True))

    def test_scan_keeps_the_strongest_of_each_name(self):
        reply = '\n'.join(['bssid / frequency / signal level / flags / ssid',
                           '02:00:00:00:00:01\t2437\t-70\t[WPA2-PSK-CCMP][ESS]\tHome',
                           '02:00:00:00:00:02\t5180\t-50\t[WPA2-PSK-CCMP][ESS]\tHome',
                           '02:00:00:00:00:03\t2412\t-40\t[ESS]\t',
                           '02:00:00:00:00:04\t2462\t-80\t[ESS]\tCafe',
                           '02:00:00:00:00:05\t2412\t-90\t[WPA2-PSK-CCMP][ESS]\tTest'])
        nets = w.parse_scan(reply, b'Test')
        self.assertEqual([n['ssid'] for n in nets], ['Test', 'Home', 'Cafe'])
        self.assertTrue(nets[0]['connected'])
        self.assertEqual((nets[1]['signal'], nets[1]['frequency']), (-50, 5180))
        self.assertEqual(nets[1]['ssid_hex'], b'Home'.hex())
        self.assertEqual([(n['bars'], n['band']) for n in nets], [(1, '2.4 GHz'), (4, '5 GHz'), (1, '2.4 GHz')])
        self.assertFalse(nets[2]['secure'])

    def test_key(self):
        # IEEE 802.11i test vector.
        self.assertEqual(w.psk(b'IEEE', 'password'), 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e')
        self.assertEqual(w.psk(b'x', 'AB' * 32), 'ab' * 32)

    def test_password_rules(self):
        w.check_password('')
        w.check_password('12345678')
        for bad in ('short', 'x' * 64, 'päss word'):
            with self.assertRaises(w.WifiError):
                w.check_password(bad)


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = w.main(list(args))
    return code, json.loads(out.getvalue())


class TestFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, 'wifi.json').write_text(json.dumps({'available': True, 'on': False, 'connected': '', 'networks': [
            {'ssid': 'Home', 'signal': -48, 'security': 'WPA2', 'password': 'correct horse'},
            {'ssid': 'Cafe', 'signal': -71, 'security': 'Open'}]}))

    def test_scan_connect_disconnect(self):
        code, reply = run('--fixture', self.tmp.name, 'scan')
        self.assertEqual(code, 0)
        self.assertEqual([n['ssid'] for n in reply['networks']], ['Home', 'Cafe'])
        secret = Path(self.tmp.name, 'pw')
        secret.write_text('wrong password')
        code, reply = run('--fixture', self.tmp.name, 'connect', '--ssid-hex', b'Home'.hex(), '--password-file', str(secret))
        self.assertEqual((code, reply['error']['code']), (2, 'WIFI_AUTH_FAILED'))
        self.assertFalse(secret.exists())
        secret.write_text('correct horse')
        code, reply = run('--fixture', self.tmp.name, 'connect', '--ssid', 'Home', '--password-file', str(secret))
        self.assertEqual(code, 0)
        self.assertTrue(reply['connected'] and reply['ssid'] == 'Home' and reply['address'])
        self.assertTrue(run('--fixture', self.tmp.name, 'scan')[1]['networks'][0]['connected'])
        code, reply = run('--fixture', self.tmp.name, 'disconnect')
        self.assertFalse(reply['connected'])

    def test_a_joined_network_is_remembered(self):
        """2026-11-08: the user asked that joined networks are kept and joined again."""
        secret = Path(self.tmp.name, 'pw')
        secret.write_text('correct horse')
        self.assertEqual(run('--fixture', self.tmp.name, 'connect', '--ssid', 'Home', '--password-file', str(secret))[0], 0)
        run('--fixture', self.tmp.name, 'disconnect')
        nets = run('--fixture', self.tmp.name, 'scan')[1]['networks']
        self.assertEqual([n['saved'] for n in nets], [True, False])
        code, reply = run('--fixture', self.tmp.name, 'connect', '--ssid-hex', b'Home'.hex(), '--saved')
        self.assertTrue(code == 0 and reply['connected'])
        run('--fixture', self.tmp.name, 'disconnect')
        self.assertTrue(run('--fixture', self.tmp.name, 'auto')[1]['connected'])
        code, reply = run('--fixture', self.tmp.name, 'forget', '--ssid-hex', b'Home'.hex())
        self.assertEqual((code, reply['saved_count']), (0, 0))
        code, reply = run('--fixture', self.tmp.name, 'connect', '--ssid-hex', b'Cafe'.hex(), '--saved')
        self.assertEqual((code, reply['error']['code']), (2, 'WIFI_NOT_SAVED'))

    def test_bad_arguments_are_a_result(self):
        code, reply = run('--fixture', self.tmp.name, 'connect')
        self.assertEqual((code, reply['error']['code']), (2, 'INVALID_ARGUMENT'))


class FakeSupplicant(threading.Thread):
    """Answers like wpa_supplicant's control socket; the right key for 'Home' is KEY."""
    KEY = w.psk(b'Home', 'correct horse')

    def __init__(self, path):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(path))
        self.attached, self.networks, self.joined, self.stopped = set(), {}, '', False

    def event(self, text):
        for peer in list(self.attached):
            self.sock.sendto(('<3>' + text).encode(), peer)

    def reply(self, peer, command):
        if command == 'ATTACH':
            self.attached.add(peer)
            return 'OK'
        if command == 'SCAN':
            if getattr(self, 'busy', False):
                return 'FAIL-BUSY'
            threading.Timer(0.05, self.event, ('CTRL-EVENT-SCAN-RESULTS ',)).start()
            return 'OK'
        if command == 'SCAN_RESULTS':
            return ('bssid / frequency / signal level / flags / ssid\n'
                    '02:00:00:00:00:01\t2437\t-48\t[WPA2-PSK-CCMP][ESS]\tHome\n'
                    '02:00:00:00:00:02\t2412\t-75\t[ESS]\tCafe\n')
        if command == 'STATUS':
            return 'wpa_state=COMPLETED\nssid=%s\n' % self.joined if self.joined else 'wpa_state=SCANNING\n'
        if command == 'ADD_NETWORK':
            self.networks['0'] = {}
            return '0'
        if command.startswith('SET_NETWORK 0 '):
            key, value = command.split(' ', 3)[2:]
            self.networks['0'][key] = value
            return 'OK'
        if command == 'SELECT_NETWORK 0':
            net = self.networks['0']
            name = bytes.fromhex(net['ssid']).decode()
            if name == 'Home' and net.get('psk') == self.KEY:
                self.joined = name
                threading.Timer(0.05, self.event, ('CTRL-EVENT-CONNECTED - Connection to 02:00:00:00:00:01 completed',)).start()
            elif name == 'Home':
                threading.Timer(0.05, self.event, ('CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="Home" auth_failures=1 duration=10 reason=WRONG_KEY',)).start()
            else:
                for n in range(3):
                    threading.Timer(0.05 * (n + 1), self.event, ('CTRL-EVENT-NETWORK-NOT-FOUND',)).start()
            return 'OK'
        if command in ('REMOVE_NETWORK all', 'DISCONNECT'):
            if command == 'DISCONNECT':
                self.joined = ''
            return 'OK'
        return 'UNKNOWN COMMAND'

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stopped:
            try:
                data, peer = self.sock.recvfrom(4096)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            self.sock.sendto((self.reply(peer, data.decode()) + '\n').encode(), peer)


class TestControlSocket(unittest.TestCase):
    def setUp(self):
        # A socket path has at most 107 bytes; a long temporary directory gives way to /tmp.
        base = tempfile.gettempdir()
        self.tmp = tempfile.TemporaryDirectory(dir=base if len(base) < 60 else '/tmp')
        self.addCleanup(self.tmp.cleanup)
        run_dir = Path(self.tmp.name, 'run')
        (run_dir / 'ctrl').mkdir(parents=True)
        self.fake = FakeSupplicant(run_dir / 'ctrl' / 'wlan0')
        self.fake.start()
        self.addCleanup(setattr, self.fake, 'stopped', True)
        self.addCleanup(self.fake.sock.close)
        self.disk = Path(self.tmp.name, 'disk')
        self.disk.mkdir()

        def volumes(mode, action):
            # As wifi._settings_volumes: a failure of the action on the volume is 'failed'.
            import filesystem as fs
            try:
                return 'ok', action(self.disk)
            except (OSError, ValueError, fs.FilesystemError):
                return 'failed', None
        for name, value in (('RUN', run_dir), ('SAVED_RAM', Path(self.tmp.name, 'saved-ram.json')),
                            ('_settings_volumes', volumes),
                            ('interface_up', lambda: True), ('supplicant_running', lambda: True),
                            ('driver_up', lambda: None), ('start_supplicant', lambda: None), ('stop_dhcp', lambda: None),
                            ('dhcp', lambda: '10.42.0.23'), ('address', lambda: '10.42.0.23'),
                            ('available', lambda: True), ('JOIN_SECONDS', 3), ('SCAN_SECONDS', 3)):
            patcher = mock.patch.object(w, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_scan(self):
        result = w.scan()
        self.assertEqual([n['ssid'] for n in result['networks']], ['Home', 'Cafe'])
        self.assertFalse(result['connected'])
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name, 'run').iterdir()), ['ctrl'])

    def test_wrong_then_right_password(self):
        with self.assertRaises(w.WifiError) as caught:
            w.connect(b'Home', 'wrong password', False)
        self.assertEqual(caught.exception.code, 'WIFI_AUTH_FAILED')
        result = w.connect(b'Home', 'correct horse', False)
        self.assertTrue(result['connected'] and result['ssid'] == 'Home')
        self.assertEqual(self.fake.networks['0']['psk'], FakeSupplicant.KEY)
        # Remembered on the settings volume as the key, never as the typed password.
        stored = (self.disk / 'a22x-recovery/wifi.json').read_text()
        self.assertIn(FakeSupplicant.KEY, stored)
        self.assertNotIn('correct horse', stored)
        self.assertEqual(oct((self.disk / 'a22x-recovery/wifi.json').stat().st_mode & 0o777), '0o600')
        self.assertFalse(w.disconnect()['connected'])
        Path(self.tmp.name, 'saved-ram.json').unlink()
        self.assertTrue(w.connect(b'Home', None, False, use_saved=True)['connected'])
        self.assertEqual(w.state()['ssid'], 'Home')
        self.assertFalse(w.disconnect()['connected'])

    def test_missing_network(self):
        with self.assertRaises(w.WifiError) as caught:
            w.connect(b'Elsewhere', '', True)
        self.assertEqual(caught.exception.code, 'WIFI_NOT_FOUND')
        self.assertEqual(self.fake.networks['0'].get('scan_ssid'), '1')
        self.assertEqual(self.fake.networks['0'].get('key_mgmt'), 'NONE')


    def test_a_driver_that_stays_busy_is_not_a_fresh_search(self):
        # 2026-11-14 review: six FAIL-BUSY and no results of its own gave the old list as new.
        self.fake.busy = True
        with mock.patch.object(w.time, 'sleep', lambda seconds: None):
            with self.assertRaises(w.WifiError) as caught:
                w.scan()
        self.assertEqual(caught.exception.code, 'WIFI_SCAN_FAILED')

    def test_a_failed_join_writes_down_that_the_old_network_is_gone(self):
        # 2026-11-14 review: the old network had been let go, and state.json still said joined.
        self.assertTrue(w.connect(b'Home', 'correct horse', False)['connected'])
        written = []
        password = Path(self.tmp.name, 'pw')
        password.write_text('wrong password')
        with mock.patch.object(w, 'write_state', written.append), mock.patch('builtins.print'):
            self.assertEqual(w.main(['connect', '--ssid', 'Home', '--password-file', str(password)]), 2)
        self.assertEqual(len(written), 1)
        self.assertFalse(written[0]['connected'])

    def test_auto_keeps_a_network_joined_already(self):
        # 2026-11-14 review: auto went on to the next saved network and undid the owner's choice.
        self.assertTrue(w.connect(b'Home', 'correct horse', False)['connected'])
        with mock.patch.object(w, 'connect', side_effect=AssertionError('no second join')):
            result = w.auto()
        self.assertTrue(result['connected'] and result['ssid'] == 'Home')

    def test_saved_networks_unknown_are_not_replaced(self):
        # 2026-11-14 review: with the settings volume busy the list read as empty, and the next
        # network alone replaced all the others on the disk.
        (self.disk / 'a22x-recovery').mkdir()
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': [
            {'ssid_hex': b'Other'.hex(), 'psk': 'a' * 64, 'added': 1}]}))
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('busy', None)):
            self.assertIsNone(w.remember(b'Home', FakeSupplicant.KEY))
            with self.assertRaises(w.WifiError) as caught:
                w.forget(b'Other')
            self.assertEqual(caught.exception.code, 'WIFI_SAVED_UNAVAILABLE')
        self.assertFalse(Path(self.tmp.name, 'saved-ram.json').exists())
        self.assertTrue(w.remember(b'Home', FakeSupplicant.KEY))
        names = [bytes.fromhex(n['ssid_hex']) for n in json.loads((self.disk / 'a22x-recovery/wifi.json').read_text())['networks']]
        self.assertEqual(names, [b'Home', b'Other'])
        self.assertEqual([p.name for p in (self.disk / 'a22x-recovery').iterdir()], ['wifi.json'])

    def test_a_save_that_fails_is_said(self):
        # 2026-11-14 review: connect() and forget() reported success while nothing was saved.
        (self.disk / 'a22x-recovery').mkdir()
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': [
            {'ssid_hex': b'Other'.hex(), 'psk': 'a' * 64, 'added': 1}]}))
        self.assertTrue(w.connect(b'Home', 'correct horse', True)['remembered'])
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('ok', action(self.disk)) if mode == 'ro' else ('failed', None)):
            with self.assertRaises(w.WifiError) as caught:
                w.forget(b'Other')
            self.assertEqual(caught.exception.code, 'WIFI_SAVED_UNWRITTEN')
            # Nothing changed, RAM neither: a second try writes again and fails again (2026-11-14 review).
            self.assertIn(b'Other'.hex(), [n['ssid_hex'] for n in w.saved_known()])
            with self.assertRaises(w.WifiError):
                w.forget(b'Other')
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('busy', None)):
            joined = w.connect(b'Home', 'correct horse', True)
        self.assertTrue(joined['connected'])
        self.assertFalse(joined['remembered'])

    def test_a_ram_copy_that_cannot_be_written_goes_and_the_disk_is_read(self):
        # 2026-11-14 review: the RAM copy was written after the lock, and a failure left it older
        # than the disk.
        ram = Path(self.tmp.name, 'saved-ram.json')
        ram.write_text(json.dumps({'networks': []}))
        real = w._atomic

        def no_ram(path, value):
            if Path(path) == ram:
                raise OSError(28, 'No space left on device')
            return real(path, value)
        with mock.patch.object(w, '_atomic', no_ram):
            self.assertTrue(w.remember(b'Home', FakeSupplicant.KEY))
        self.assertFalse(ram.exists())
        self.assertEqual([bytes.fromhex(n['ssid_hex']) for n in w.saved_known()], [b'Home'])
        self.assertTrue(ram.exists())

    def test_a_change_reads_the_disk_under_the_lock_and_a_busy_list_is_told_apart(self):
        # 2026-11-14 review: remember read the list from RAM outside the lock, so a network another
        # writer had just saved was lost; and a busy volume was said as a failed write, which the
        # hand-off never tries again.
        (self.disk / 'a22x-recovery').mkdir()
        ram = Path(self.tmp.name, 'saved-ram.json')
        ram.write_text(json.dumps({'networks': [{'ssid_hex': b'Old'.hex(), 'psk': 'a' * 64}]}))
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': [
            {'ssid_hex': b'Old'.hex(), 'psk': 'a' * 64}, {'ssid_hex': b'Other'.hex(), 'psk': 'b' * 64}]}))
        self.assertTrue(w.remember(b'Home', FakeSupplicant.KEY))
        names = [bytes.fromhex(n['ssid_hex']) for n in json.loads((self.disk / 'a22x-recovery/wifi.json').read_text())['networks']]
        self.assertEqual(names, [b'Home', b'Old', b'Other'])
        self.assertEqual([bytes.fromhex(n['ssid_hex']) for n in w.saved_known()], [b'Home', b'Old', b'Other'])
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('busy', None)):
            self.assertIsNone(w.remember(b'Next', FakeSupplicant.KEY))
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('failed', None)):
            self.assertIs(w.remember(b'Next', FakeSupplicant.KEY), False)

    def test_a_list_that_cannot_be_looked_at_is_never_replaced(self):
        # 2026-11-14 reviews: a stat that failed read as no list, and remember wrote one network over
        # five. Through the real path (settings_root, _look, choose): the volume cannot be looked at,
        # so the list is busy, nothing is written, and the hand-off may try again.
        import contextlib
        import settings
        import filesystem as fs
        (self.disk / 'a22x-recovery').mkdir()
        listed = {'networks': [{'ssid_hex': ('Net%d' % n).encode().hex(), 'psk': 'a' * 64} for n in range(5)]}
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps(listed))
        Path(self.tmp.name, 'saved-ram.json').unlink(missing_ok=True)

        class Context:
            lock_path, mode = '/nonexistent', 'fixture'

            def volume(self, label):
                return {'mounted': False, 'mountable': label == 'cache', 'write_supported': True, 'read_only_reason_code': None,
                        'reason_code': None if label == 'cache' else 'ENCRYPTED_OR_UNRECOGNIZED_USERDATA'}

        @contextlib.contextmanager
        def volume_root(context, label, mode):
            yield self.disk
        real = os.open

        def failing(path, *args, **kwargs):
            if str(path).endswith('wifi.json'):
                raise OSError(5, 'Input/output error')
            return real(path, *args, **kwargs)
        with mock.patch.object(settings, 'volume_root', volume_root), mock.patch.object(settings.os, 'open', failing), \
                mock.patch('operation_lock.lease', lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(fs, 'DeviceContext', Context), mock.patch.object(w, '_settings_volumes', REAL_SETTINGS_VOLUMES), \
                mock.patch.dict(os.environ, {'A22X_MISC': str(Path(self.tmp.name, 'misc'))}):
            # Since 2026-11-14 the list is in misc; while misc holds none, this older file is read and
            # copied. It cannot be read: the list is busy, nothing is written, the hand-off tries again.
            Path(self.tmp.name, 'misc').write_bytes(bytes(512 << 10))
            self.assertIsNone(w.remember(b'Home', FakeSupplicant.KEY))
            import misc_store
            self.assertIsNone(misc_store.read('wifi'))
        self.assertEqual(json.loads((self.disk / 'a22x-recovery/wifi.json').read_text()), listed)

    def test_a_list_that_is_no_file_holds_no_network_and_is_replaced(self):
        # 2026-11-14 review: a folder or a link where wifi.json should be failed every change for good.
        (self.disk / 'a22x-recovery/wifi.json').mkdir(parents=True)
        Path(self.tmp.name, 'saved-ram.json').unlink(missing_ok=True)
        self.assertEqual(w.saved_known(), [])
        self.assertTrue(w.remember(b'Home', FakeSupplicant.KEY))
        self.assertEqual([bytes.fromhex(n['ssid_hex']) for n in json.loads((self.disk / 'a22x-recovery/wifi.json').read_text())['networks']],
                         [b'Home'])

    def test_the_list_never_removes_the_folder_that_holds_the_settings(self):
        # 2026-11-14 review: clearing the path of wifi.json removed a22x-recovery itself when it was a
        # link (to a real folder) or a file, the settings and their password with it.
        real = self.disk / 'real-folder'
        real.mkdir()
        (real / 'settings.json').write_text('{"hash": "kept"}')
        (self.disk / 'a22x-recovery').symlink_to('real-folder')
        Path(self.tmp.name, 'saved-ram.json').unlink(missing_ok=True)
        # A link to a folder of the volume itself is followed, as older releases left it: the list is
        # written there, and the link and the settings stay (2026-11-14 review: the link was removed).
        self.assertIs(w.remember(b'Home', FakeSupplicant.KEY), True)
        self.assertTrue((self.disk / 'a22x-recovery').is_symlink())
        self.assertEqual((real / 'settings.json').read_text(), '{"hash": "kept"}')
        self.assertTrue((real / 'wifi.json').is_file())
        (self.disk / 'a22x-recovery').unlink()
        (self.disk / 'a22x-recovery').write_text('a file')
        self.assertIs(w.remember(b'Home', FakeSupplicant.KEY), False)
        self.assertEqual((self.disk / 'a22x-recovery').read_text(), 'a file')

    def test_a_copy_in_ram_that_is_a_folder_is_set_right_from_the_disk(self):
        # 2026-11-14 review: the list stayed unavailable until a restart.
        (self.disk / 'a22x-recovery').mkdir()
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': [{'ssid_hex': b'Home'.hex(), 'psk': 'a' * 64}]}))
        ram = Path(self.tmp.name, 'saved-ram.json')
        ram.unlink(missing_ok=True)
        ram.mkdir()
        self.assertEqual([bytes.fromhex(n['ssid_hex']) for n in w.saved_known()], [b'Home'])
        self.assertTrue(ram.is_file())

    def test_forget_refreshes_a_stale_copy_in_ram(self):
        # 2026-11-14 review: forget found the network gone from the disk and left RAM still naming it.
        (self.disk / 'a22x-recovery').mkdir()
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': []}))
        Path(self.tmp.name, 'saved-ram.json').write_text(json.dumps({'networks': [{'ssid_hex': b'X'.hex(), 'psk': 'a' * 64}]}))
        self.assertEqual(w.forget(b'X')['saved_count'], 0)
        self.assertEqual(w.saved_known(), [])

    def test_forget_asks_the_disk_not_a_stale_copy_in_ram(self):
        # 2026-11-14 review: a RAM copy without a network on disk made forget report it gone.
        (self.disk / 'a22x-recovery').mkdir()
        (self.disk / 'a22x-recovery/wifi.json').write_text(json.dumps({'networks': [{'ssid_hex': b'Home'.hex(), 'psk': 'a' * 64}]}))
        Path(self.tmp.name, 'saved-ram.json').write_text(json.dumps({'networks': []}))
        self.assertEqual(w.forget(b'Home')['saved_count'], 0)
        self.assertEqual(json.loads((self.disk / 'a22x-recovery/wifi.json').read_text())['networks'], [])

    def test_a_volume_that_fails_to_close_after_the_action_keeps_its_result(self):
        # 2026-11-14 review: an unmount that failed after the write was said busy, and the write was
        # done again by the hand-off.
        import contextlib
        import settings
        import filesystem as fs

        @contextlib.contextmanager
        def closing_fails(context, mode, relative=None):
            yield 'cache', self.disk
            raise fs.FilesystemError('UNMOUNT_FAILED', 'busy')
        with mock.patch.object(settings, 'settings_root', closing_fails), \
                mock.patch('operation_lock.lease', lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(fs, 'DeviceContext', lambda: mock.Mock(lock_path='/nonexistent', mode='fixture')):
            self.assertEqual(REAL_SETTINGS_VOLUMES('rw', lambda root: 'written'), ('ok', 'written'))
            self.assertEqual(REAL_SETTINGS_VOLUMES('rw', lambda root: (_ for _ in ()).throw(OSError(5, 'eio'))), ('failed', None))

    def test_a_lease_held_elsewhere_is_said_at_once_when_forgetting(self):
        # 2026-11-14 reviews: a lease held elsewhere was waited for twice, rw then ro; a volume that
        # only cannot be written now is read to tell a network not saved, which needs no write.
        for status, modes in (('locked', ['rw']), ('busy', ['rw', 'ro'])):
            asked = []
            with mock.patch.object(w, '_settings_volumes', lambda mode, action: (asked.append(mode), (status, None))[1]):
                with self.assertRaises(w.WifiError) as caught:
                    w.forget(b'Unknown')
            self.assertEqual((caught.exception.code, asked), ('WIFI_SAVED_UNAVAILABLE', modes))
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('busy', None) if mode == 'rw' else ('ok', [])):
            self.assertEqual(w.forget(b'Unknown')['saved_count'], 0)

    def test_a_saved_network_is_never_said_forgotten_when_nothing_was_written(self):
        # 2026-11-14 review: with the volume not writable now, a network still on disk was reported forgotten.
        on_disk = [{'ssid_hex': b'Home'.hex(), 'psk': 'a' * 64}]
        with mock.patch.object(w, '_settings_volumes', lambda mode, action: ('busy', None) if mode == 'rw' else ('ok', on_disk)):
            with self.assertRaises(w.WifiError) as caught:
                w.forget(b'Home')
        self.assertEqual(caught.exception.code, 'WIFI_SAVED_UNAVAILABLE')

    def test_a_socket_where_the_list_should_be_holds_no_network(self):
        # 2026-11-14 review: opening it gave ENXIO and broke the list for good.
        import socket
        import settings
        (self.disk / 'a22x-recovery').mkdir()
        here = os.getcwd()
        os.chdir(self.disk / 'a22x-recovery')
        self.addCleanup(os.chdir, here)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind('wifi.json')
            with self.assertRaises(settings.NotRegular):
                settings.read_bytes(self.disk, w.SAVED_RELATIVE)
            self.assertEqual(w._read_list(self.disk), [])

    def test_a_damaged_saved_list_holds_no_network(self):
        # 2026-11-14 review: a list, or entries that are no objects, raised AttributeError.
        ram = Path(self.tmp.name, 'saved-ram.json')
        for damaged in ('["x"]', '{"networks": ["x", 3]}', '{"networks": [{"ssid_hex": "zz"}]}', '{"networks": 5}'):
            ram.write_text(damaged)
            self.assertEqual(w.saved_read(), [], damaged)
        ram.write_text(json.dumps({'networks': ['x', {'ssid_hex': b'Home'.hex(), 'psk': 'k'}]}))
        self.assertEqual(w.saved_read(), [{'ssid_hex': b'Home'.hex(), 'psk': 'k'}])



class SavedInMisc(unittest.TestCase):
    """The saved list in misc (2026-11-14), through the real lock and record."""
    def setUp(self):
        import filesystem as fs
        self.tmp = Path(tempfile.mkdtemp(prefix='a22x-wmisc-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.misc = self.tmp / 'misc'
        self.misc.write_bytes(bytes(512 << 10))
        self.legacy = []
        self.retired = []
        context = mock.Mock(lock_path=str(self.tmp / 'lock'), mode='fixture')
        for patcher in (mock.patch.dict(os.environ, {'A22X_MISC': str(self.misc)}),
                        mock.patch.object(w, 'SAVED_RAM', self.tmp / 'saved-ram.json'),
                        mock.patch.object(fs, 'DeviceContext', lambda: context),
                        mock.patch.object(w, '_legacy_list', lambda: self.legacy_list()),
                        mock.patch.object(w, '_retire_legacy_list', lambda: self.retired.append(1))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def legacy_list(self):
        if isinstance(self.legacy, Exception):
            raise self.legacy
        return list(self.legacy)

    def names(self, networks):
        return [bytes.fromhex(n['ssid_hex']) for n in networks]

    def test_remember_and_forget_keep_the_list_in_misc(self):
        import misc_store
        self.assertTrue(w.remember(b'Home', 'a' * 64))
        self.assertTrue(w.remember(b'Work', 'b' * 64))
        self.assertEqual(self.names(json.loads(misc_store.read('wifi'))['networks']), [b'Work', b'Home'])
        (self.tmp / 'saved-ram.json').unlink()
        self.assertEqual(self.names(w.saved_known()), [b'Work', b'Home'])
        self.assertEqual(w.forget(b'Home')['saved_count'], 1)
        self.assertEqual(self.names(json.loads(misc_store.read('wifi'))['networks']), [b'Work'])

    def test_an_older_file_is_copied_once_and_one_that_cannot_be_read_is_busy(self):
        import misc_store
        self.legacy = w._Busy()
        self.assertIsNone(w.saved_known())
        self.assertIsNone(w.remember(b'Home', 'a' * 64))
        self.assertIsNone(misc_store.read('wifi'))
        self.legacy = [{'ssid_hex': b'Old'.hex(), 'psk': 'c' * 64}]
        self.assertEqual(self.names(w.saved_known()), [b'Old'])
        self.assertEqual(self.retired, [1])  # and the older file goes (2026-11-14 review)
        self.legacy = []  # read once: misc decides from then on
        (self.tmp / 'saved-ram.json').unlink()
        self.assertEqual(self.names(w.saved_known()), [b'Old'])
        self.assertEqual(self.retired, [1])

    def test_a_misc_that_cannot_be_read_or_written_is_said(self):
        import misc_store
        with mock.patch.dict(os.environ, {'A22X_MISC': str(self.tmp / 'missing')}):
            self.assertIsNone(w.saved_known())
            self.assertIsNone(w.remember(b'Home', 'a' * 64))
        w.remember(b'Home', 'a' * 64)
        with mock.patch.object(misc_store, 'write', side_effect=OSError(5, 'Input/output error')):
            self.assertIs(w.remember(b'Work', 'b' * 64), False)
            with self.assertRaises(w.WifiError) as caught:
                w.forget(b'Home')
            self.assertEqual(caught.exception.code, 'WIFI_SAVED_UNWRITTEN')
        data = bytearray(self.misc.read_bytes())
        for index in (0, 1):
            data[misc_store.AREAS['wifi'] + index * misc_store.SLOT + misc_store.SECTOR] ^= 0xff
        self.misc.write_bytes(bytes(data))
        (self.tmp / 'saved-ram.json').unlink(missing_ok=True)
        self.assertEqual(w.saved_known(), [])  # a damaged list holds no network...
        self.assertTrue(w.remember(b'Work', 'b' * 64))  # ... and the next write replaces it
        self.assertEqual(self.names(json.loads(misc_store.read('wifi'))['networks']), [b'Work'])


    def test_an_area_with_another_program_s_bytes_leaves_the_list_in_the_older_file(self):
        import misc_store
        data = bytearray(self.misc.read_bytes())
        data[misc_store.AREAS['settings'] + 9] = 1
        self.misc.write_bytes(bytes(data))
        written = []
        with mock.patch.object(w, '_write_legacy_list', lambda networks: written.append(networks)):
            self.assertTrue(w.remember(b'Home', 'a' * 64))
        self.assertEqual([self.names(n) for n in written], [[b'Home']])
        self.assertEqual(self.misc.read_bytes(), bytes(data))


if __name__ == '__main__':
    unittest.main()
