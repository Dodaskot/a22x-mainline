#!/usr/bin/env python3
"""Recovery Wi-Fi: scan results, keys, the recorded state for the interface, and the
control-socket conversation with a stand-in wpa_supplicant."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wifi as w


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
        for name, value in (('RUN', run_dir), ('SAVED_RAM', Path(self.tmp.name, 'saved-ram.json')),
                            ('_settings_volumes', lambda mode, action: action(self.disk)),
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


if __name__ == '__main__':
    unittest.main()
