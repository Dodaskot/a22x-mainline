#!/usr/bin/env python3
"""A22x recovery Wi-Fi: the visible networks, joining one, and the connection state.

platform/a22x-wifi.sh brings the driver up (the MediaTek modules built for the recovery
kernel). One wpa_supplicant with a control socket scans and joins; busybox udhcpc keeps the
address. A joined network is remembered (its name and WPA key, never the typed password) beside
the recovery settings, and recovery joins the strongest remembered one by itself at the next start.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

PLATFORM = Path('/opt/a22x-recovery/platform')
SCRIPT = PLATFORM / 'a22x-wifi.sh'
MODULES = PLATFORM / 'wifi-modules.tar.xz'
SUPPLICANT = PLATFORM / 'wpa_supplicant'
BUSYBOX = PLATFORM / 'busybox'
RUN = Path(os.environ.get('A22X_WIFI_RUN', '/run/a22x-wifi'))
RECOVERY_RUN = Path(os.environ.get('A22X_WIFI_RECOVERY_RUN', '/run/a22x-recovery'))
SAVED_RAM = RECOVERY_RUN / 'wifi-saved.json'
SAVED_RELATIVE = Path('a22x-recovery/wifi.json')
SAVED_MAX = 16
LOCK_WAIT = 3.0
IFACE = 'wlan0'
MAX_NETWORKS = 32
SCAN_SECONDS = 12
JOIN_SECONDS = 30
ADDRESS_SECONDS = 40
# wpa_supplicant's printf_encode, reversed.
ESCAPES = {'\\': 0x5c, '"': 0x22, 'e': 0x1b, 'n': 0x0a, 'r': 0x0d, 't': 0x09}


class WifiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise WifiError(code, message)


def decode_ssid(text):
    """The bytes of an SSID as wpa_supplicant prints it (a reply read as latin-1)."""
    out = bytearray()
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == 'x' and re.fullmatch(r'[0-9A-Fa-f]{2}', text[i + 2:i + 4]):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
            if nxt in ESCAPES:
                out.append(ESCAPES[nxt])
                i += 2
                continue
        out += text[i].encode('latin-1', 'replace')
        i += 1
    return bytes(out)


def security(flags):
    """A short name and whether this recovery can join it (WPA/WPA2 personal or open)."""
    if 'WEP' in flags:
        return 'WEP', False
    if 'PSK' in flags:
        return ('WPA2' if 'WPA2' in flags or 'RSN' in flags else 'WPA'), True
    if 'SAE' in flags:
        return 'WPA3', False
    if 'EAP' in flags:
        return 'Enterprise', False
    if 'OWE' in flags or 'WPA' in flags or 'RSN' in flags:
        return 'Other', False
    return 'Open', True


def parse_scan(reply, current=b''):
    """SCAN_RESULTS: the strongest access point of each named network, strongest first."""
    best = {}
    for line in reply.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        bssid, freq, level, flags, raw = parts[:5]
        ssid = decode_ssid(raw)
        if not ssid or not ssid.strip(b'\0'):
            continue
        try:
            signal, frequency = int(level), int(freq)
        except ValueError:
            continue
        name, supported = security(flags)
        # The interface shows bars and a band; it reads no negative numbers.
        bars = 4 if signal >= -55 else 3 if signal >= -66 else 2 if signal >= -77 else 1
        band = '2.4 GHz' if frequency < 3000 else '5 GHz' if frequency < 5925 else '6 GHz'
        entry = {'ssid': ssid.decode('utf-8', 'replace'), 'ssid_hex': ssid.hex(), 'signal': signal,
                 'frequency': frequency, 'bars': bars, 'band': band, 'security': name, 'secure': name != 'Open',
                 'supported': supported, 'connected': bool(current) and ssid == current}
        old = best.get(ssid)
        if old is None or signal > old['signal']:
            best[ssid] = entry
    networks = sorted(best.values(), key=lambda n: (not n['connected'], -n['signal'], n['ssid']))
    return networks[:MAX_NETWORKS]


def psk(ssid, password):
    """The 256-bit key of a WPA passphrase, so no quoting reaches wpa_supplicant."""
    if re.fullmatch(r'[0-9A-Fa-f]{64}', password):
        return password.lower()
    return hashlib.pbkdf2_hmac('sha1', password.encode(), ssid, 4096, 32).hex()


def check_password(password):
    if password and not (8 <= len(password) <= 63 or re.fullmatch(r'[0-9A-Fa-f]{64}', password)):
        fail('WIFI_PASSWORD_INVALID', 'A Wi-Fi password has 8 to 63 characters.')
    if password and not all(32 <= ord(c) < 127 for c in password) and len(password) != 64:
        fail('WIFI_PASSWORD_INVALID', 'A Wi-Fi password uses printable ASCII characters.')


def _beside():
    """The recovery's own modules, importable; its folder is put on the path once (2026-11-14
    review: once more for every write of the saved list)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


def _atomic(path, value):
    """VALUE as PATH, whole or not at all: filesystem.atomic_state (a unique temporary name, fsync of
    the file and its folder). 2026-11-14 review: a fixed name.new, no fsync of the folder."""
    _beside()
    import filesystem as fs
    fs.atomic_state(path, value)


def networks_of(value):
    """The remembered networks of a wifi.json, the ones that are whole; a damaged file holds none
    (2026-11-14 review: a list or a non-dict entry raised AttributeError past every handler)."""
    found = value.get('networks') if isinstance(value, dict) else None
    if not isinstance(found, list):
        return []
    whole = []
    for network in found:
        if (isinstance(network, dict) and isinstance(network.get('ssid_hex'), str)
                and re.fullmatch(r'(?:[0-9a-f]{2}){1,32}', network['ssid_hex'])
                and isinstance(network.get('psk', ''), str)):
            whole.append(network)
    return whole


def _drop_ram(folder_only=False):
    """The RAM copy gone, whatever it is (a folder too); with FOLDER_ONLY only a folder, which no
    write could replace."""
    if SAVED_RAM.is_dir() and not SAVED_RAM.is_symlink():
        shutil.rmtree(SAVED_RAM, ignore_errors=True)
    elif not folder_only:
        with contextlib.suppress(OSError):
            SAVED_RAM.unlink()


def _ram_saved(networks):
    """The RAM copy of the saved list, written while the settings lock is held, right after the
    list on disk was read or written under it; when it cannot be written it is removed, and the
    next read takes the disk's (2026-11-14 reviews: written after the lock, two writers left RAM and
    disk apart; a failure, a FilesystemError too, left RAM older than the disk)."""
    _beside()
    import filesystem as fs
    try:
        _drop_ram(folder_only=True)
        _atomic(SAVED_RAM, {'networks': networks})
    except (OSError, fs.FilesystemError):
        _drop_ram()


class _Busy(Exception):
    """The saved list cannot be read now: not the same as none."""


class _Misc:
    """Where the saved list is kept since 2026-11-14: the wifi record in misc (misc_store), two whole
    copies, no filesystem. A folder given in its place is the file of a release up to 1.0.1 (read
    while misc holds no list yet; tests)."""


MISC = _Misc()


def _settings_volumes(mode, action):
    """Run ACTION(MISC) under the lock the settings use (MODE: 'ro' or 'rw', kept for the callers).
    Returns ('ok', value); ('locked', None) when the lease cannot be had now; ('busy', None) when the
    list cannot be read now; ('failed', None) when ACTION's write failed (2026-11-14 reviews: all of
    these were one answer)."""
    _beside()
    import filesystem as fs
    from operation_lock import lease
    context = fs.DeviceContext()
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            with lease(context.lock_path, context.mode):
                try:
                    return 'ok', action(MISC)
                except _Busy:
                    return 'busy', None
                except (OSError, ValueError, fs.FilesystemError):
                    return 'failed', None
        except BlockingIOError:
            if time.monotonic() > deadline:
                return 'locked', None
            time.sleep(0.1)


def _encoded(networks):
    return (json.dumps({'schema': 'a22x.wifi.saved.v1', 'networks': networks}, ensure_ascii=True, sort_keys=True) + '\n').encode()


def _legacy_list():
    """The list a release up to 1.0.1 kept beside its settings file, read as it read it; _Busy when
    that volume cannot be looked at now."""
    _beside()
    import filesystem as fs
    import settings
    try:
        with settings.settings_root(fs.DeviceContext(), 'ro') as (_, root):
            return _read_list(root)
    except settings.NoSettingsVolume:
        return []
    except (OSError, ValueError, fs.FilesystemError, subprocess.SubprocessError) as error:
        raise _Busy() from error


def _write_legacy_list(networks):
    """NETWORKS as the older release's file, beside its settings (while misc's area is not this
    recovery's)."""
    _beside()
    import filesystem as fs
    import settings
    with settings.settings_root(fs.DeviceContext(), 'rw') as (_, root):
        settings.clear_file(root, SAVED_RELATIVE)
        settings.write_bytes(root, SAVED_RELATIVE, _encoded(networks))


def _retire_legacy_list():
    """The older release's file gone once its list is in misc, so networks forgotten since never come
    back when misc reads as holding none (2026-11-14 review). Best effort."""
    _beside()
    import filesystem as fs
    import settings
    with contextlib.suppress(OSError, ValueError, fs.FilesystemError, subprocess.SubprocessError):
        with settings.settings_root(fs.DeviceContext(), 'rw') as (_, root):
            settings.clear_file(root, SAVED_RELATIVE)
            (root / SAVED_RELATIVE).unlink(missing_ok=True)


def _read_list(root):
    """The saved list. In misc (ROOT is MISC): none when the record is damaged (a damaged list holds no
    network, and the next write replaces it); while misc holds no list, the older release's file,
    copied to misc then; _Busy when misc, or that file, cannot be read now (2026-11-14 review: read as
    none, the next remember wrote one network over all the others). In a folder (the older file):
    read by settings.read_bytes, none when nothing or no list is there, raised when it cannot be read."""
    _beside()
    import settings
    if root is MISC:
        import misc_store
        try:
            data, twice, readable = misc_store.state('wifi')
            if data is not None and not twice and readable:
                with contextlib.suppress(OSError, ValueError):
                    misc_store.write('wifi', data)  # a write stopped between the copies
        except misc_store.Damaged:
            return []
        except (OSError, ValueError) as error:
            raise _Busy() from error
        if data is None:
            networks = _legacy_list()
            with contextlib.suppress(OSError, ValueError):
                misc_store.write('wifi', _encoded(networks))
                _retire_legacy_list()
            return networks
        try:
            return networks_of(json.loads(data.decode()))
        except ValueError:
            return []
    try:
        data = settings.read_bytes(root, SAVED_RELATIVE)
    except settings.NotRegular:
        return []
    if data is None:
        return []
    try:
        return networks_of(json.loads(data.decode()))
    except ValueError:
        return []


def _read_and_keep(root):
    """The list in ROOT, copied to RAM under the same lock."""
    networks = _read_list(root)
    _ram_saved(networks)
    return networks


def saved_known():
    """The remembered networks: [{'ssid_hex', 'psk', 'added'}], newest first; None when they cannot
    be read now (the settings volume is busy or will not open), which is not the same as none."""
    # A copy that is not there, damaged or cannot be read: the disk, which decides, is read and copied
    # again (2026-11-14 reviews: is_file() said no copy on any error; then a copy that was a folder
    # made the list unavailable until a restart).
    try:
        return networks_of(json.loads(SAVED_RAM.read_text()))
    except (OSError, ValueError):
        _drop_ram()
    status, networks = _settings_volumes('ro', _read_and_keep)
    return networks if status == 'ok' else None


def _change(change):
    """CHANGE applied as _update does; the status of _settings_volumes."""
    def update(root):
        import settings
        current = _read_list(root)
        networks = change(current)
        if networks == current:
            _ram_saved(current)
            return True
        # A damaged list that is no file goes; never a folder of its path, which also holds the
        # settings (2026-11-14 reviews): a folder that is no folder of the volume fails the write.
        # Written as the settings are (settings.write_bytes), through a link to a folder of the volume.
        if root is MISC:
            import misc_store
            try:
                misc_store.write('wifi', _encoded(networks))
            except misc_store.Foreign:
                # Another program's bytes in misc's area, which is not this recovery's: the list
                # stays in the older file, read from there too (2026-11-14 review).
                _write_legacy_list(networks)
        else:
            settings.clear_file(root, SAVED_RELATIVE)
            settings.write_bytes(root, SAVED_RELATIVE, _encoded(networks))
        _ram_saved(networks)
        return True
    status, _ = _settings_volumes('rw', update)
    return status


def saved_read():
    """The remembered networks, or none when they cannot be read now."""
    return saved_known() or []


def _update(change):
    """CHANGE(list) -> the new list, applied to the list on disk and written back in one hold of
    the settings lock, then copied to RAM (2026-11-14 review: read from RAM outside the lock and
    written under another hold, two writers lost one's network). True when written; None when the
    volume or lock cannot be had now (nothing changed; try again); False when the write failed."""
    status = _change(change)
    return True if status == 'ok' else (False if status == 'failed' else None)


def remember(ssid, key):
    """True when saved; None when the saved list cannot be had now and nothing was changed
    (2026-11-14 review: with the settings volume busy, the list read as empty, and the new network
    alone replaced every other one); False when it could not be written."""
    added = {'ssid_hex': ssid.hex(), 'psk': key, 'added': int(time.time())}
    return _update(lambda current: ([added] + [n for n in current if n.get('ssid_hex') != ssid.hex()])[:SAVED_MAX])


def saved_key(ssid):
    for network in saved_read():
        if network.get('ssid_hex') == ssid.hex():
            return network.get('psk', '')
    fail('WIFI_NOT_SAVED', 'This network is not saved.')


def forget(ssid):
    """Removed in one locked read and write of the list on disk, copied to RAM (2026-11-14 reviews: a
    stale RAM copy decided). When the lease cannot be had, that is said at once (no second wait);
    when only the volume cannot be written now, the disk read-only says whether the network is saved
    at all: one it does not hold is forgotten already (2026-11-14 review: else it failed for good on
    a cache never writable now), and RAM is set right."""
    kept = []

    def without(networks):
        kept[:] = [n for n in networks if n.get('ssid_hex') != ssid.hex()]
        return kept
    status = _change(without)
    if status == 'busy':
        # Not writable now: a network the disk does not hold is forgotten already; one it holds, or a
        # list that cannot be read, is not, and is said so (2026-11-14 review: a saved network was
        # reported forgotten, and an unreadable list as one not written).
        status, current = _settings_volumes('ro', _read_and_keep)
        if status == 'ok' and all(n.get('ssid_hex') != ssid.hex() for n in current):
            return {**state(), 'saved_count': len(current)}
        fail('WIFI_SAVED_UNAVAILABLE', 'The saved networks cannot be read now. Try again in a moment.')
    if status == 'locked':
        fail('WIFI_SAVED_UNAVAILABLE', 'The saved networks cannot be read now. Try again in a moment.')
    # A failed write is said: the network would come back at the next start (2026-11-14 review).
    if status == 'failed':
        fail('WIFI_SAVED_UNWRITTEN', 'The saved networks could not be written. Try again in a moment.')
    return {**state(), 'saved_count': len(kept)}


def write_state(result):
    """The interface follows this file, also when recovery joined a network by itself."""
    try:
        RUN.mkdir(mode=0o700, exist_ok=True)
        keys = ('available', 'on', 'connected', 'ssid', 'ssid_hex', 'address')
        temporary = RUN / 'state.new'
        temporary.write_text(json.dumps({k: result[k] for k in keys if k in result}))
        temporary.replace(RUN / 'state.json')
    except OSError:
        pass
    follow_ssh(result)


SSH_SERVICE = '/opt/a22x-recovery/ssh_service.py'


def follow_ssh(result):
    """Key-only SSH also on the Wi-Fi address while recovery has one (2026-11-08), so the computer
    can work without the cable; a new computer still needs the confirmation on the phone."""
    address = result.get('address') if result.get('connected') else None
    try:
        old = json.loads(Path('/run/a22x-recovery/ssh/wifi.json').read_text())
    except (OSError, ValueError):
        old = {}
    if (old.get('address') if old.get('running') else None) == address:
        return
    if not os.path.exists(SSH_SERVICE):
        return
    try:
        subprocess.run(['/usr/bin/python3', SSH_SERVICE, 'wifi', 'on' if address else 'off'],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        pass


class Control:
    """A datagram client of wpa_supplicant's control socket. An attached one receives events."""

    def __init__(self, attach=False, timeout=5.0):
        RUN.mkdir(mode=0o700, exist_ok=True)
        self.local = RUN / ('ui-%d-%s' % (os.getpid(), 'events' if attach else 'requests'))
        self.local.unlink(missing_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.timeout = timeout
        try:
            self.sock.bind(str(self.local))
            self.sock.connect(str(RUN / 'ctrl' / IFACE))
            if attach and self.request('ATTACH') != 'OK':
                fail('WIFI_CONTROL_FAILED', 'wpa_supplicant refused event delivery')
        except OSError:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.sock.close()
        self.local.unlink(missing_ok=True)

    def request(self, command, timeout=None):
        self.sock.settimeout(timeout or self.timeout)
        self.sock.send(command.encode('latin-1'))
        while True:
            data = self.sock.recv(65536).decode('latin-1')
            if not data.startswith('<'):
                return data.strip() if '\n' not in data.strip() else data

    def events(self, seconds):
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(65536).decode('latin-1')
            except (socket.timeout, TimeoutError):
                return
            if data.startswith('<') and '>' in data:
                yield data[data.index('>') + 1:]


def ok(reply, what):
    if reply != 'OK':
        fail('WIFI_CONTROL_FAILED', 'wpa_supplicant refused %s' % what)


def available():
    return SCRIPT.is_file() and MODULES.is_file() and SUPPLICANT.is_file()


def interface_up():
    return (Path('/sys/class/net') / IFACE).exists()


def driver_up():
    if interface_up():
        return
    if not available():
        fail('WIFI_UNAVAILABLE', 'This recovery has no Wi-Fi driver.')
    run = subprocess.run(['/bin/sh', str(SCRIPT), 'up'], capture_output=True, text=True, timeout=120)
    if run.returncode != 0 or not interface_up():
        reason = (run.stderr.strip().splitlines() or ['The Wi-Fi driver did not start.'])[-1]
        fail('WIFI_START_FAILED', reason.replace('a22x-wifi: ', '').capitalize())


def supplicant_running():
    try:
        os.kill(int((RUN / 'wpa.pid').read_text().split()[0]), 0)
    except (OSError, ValueError, IndexError):
        return False
    return (RUN / 'ctrl' / IFACE).exists()


def start_supplicant():
    if supplicant_running():
        return
    RUN.mkdir(mode=0o700, exist_ok=True)
    conf = RUN / 'wpa.conf'
    # The static wpa_supplicant of this recovery cannot write its configuration, so it does not
    # know update_config and refuses a file that names it (2026-11-08).
    conf.write_text('ctrl_interface=DIR=%s GROUP=0\nap_scan=1\n' % (RUN / 'ctrl'))
    conf.chmod(0o600)
    (RUN / 'ctrl' / IFACE).unlink(missing_ok=True)
    run = subprocess.run([str(SUPPLICANT), '-B', '-i', IFACE, '-c', str(conf), '-D', 'nl80211',
                          '-P', str(RUN / 'wpa.pid')], capture_output=True, text=True, timeout=30)
    deadline = time.monotonic() + 5
    while run.returncode == 0 and time.monotonic() < deadline and not (RUN / 'ctrl' / IFACE).exists():
        time.sleep(0.1)
    if not supplicant_running():
        fail('WIFI_START_FAILED', 'wpa_supplicant did not start.')


def address():
    if not interface_up():
        return ''
    run = subprocess.run([str(BUSYBOX), 'ip', '-4', 'addr', 'show', IFACE], capture_output=True, text=True, timeout=10)
    found = re.search(r'inet ([0-9.]+)', run.stdout)
    return found.group(1) if found else ''


def stop_dhcp():
    subprocess.run(['/bin/sh', str(SCRIPT), 'release'], capture_output=True, timeout=20)


def dhcp():
    subprocess.run(['/bin/sh', str(SCRIPT), 'dhcp'], capture_output=True, timeout=30)
    deadline = time.monotonic() + ADDRESS_SECONDS
    while time.monotonic() < deadline:
        if found := address():
            return found
        time.sleep(1)
    return ''


def current():
    """The joined network's SSID bytes, or b''."""
    if not (interface_up() and supplicant_running()):
        return b''
    with Control() as ctl:
        values = dict(line.split('=', 1) for line in ctl.request('STATUS').splitlines() if '=' in line)
    return decode_ssid(values.get('ssid', '')) if values.get('wpa_state') == 'COMPLETED' else b''


def state(joined=None):
    joined = current() if joined is None else joined
    return {'available': available(), 'on': interface_up(), 'connected': bool(joined),
            'ssid': joined.decode('utf-8', 'replace'), 'ssid_hex': joined.hex(), 'address': address() if joined else ''}


def scan():
    driver_up()
    start_supplicant()
    with Control() as ctl, Control(attach=True) as monitor:
        started = False
        for _ in range(6):
            reply = ctl.request('SCAN')
            if reply == 'OK':
                started = True
                break
            if reply != 'FAIL-BUSY':
                fail('WIFI_SCAN_FAILED', 'Wi-Fi could not search for networks.')
            time.sleep(1)
        fresh = False
        for event in monitor.events(SCAN_SECONDS):
            if event.startswith('CTRL-EVENT-SCAN-RESULTS'):
                fresh = True
                break
            if event.startswith('CTRL-EVENT-SCAN-FAILED'):
                break
        # 2026-11-14 review: a driver that stayed busy, with no search of its own finishing, gave the
        # last results as new ones.
        if not started and not fresh:
            fail('WIFI_SCAN_FAILED', 'Wi-Fi could not search for networks.')
        reply = ctl.request('SCAN_RESULTS', timeout=10)
    joined = current()
    known = {n.get('ssid_hex') for n in saved_read()}
    networks = parse_scan(reply, joined)
    for network in networks:
        network['saved'] = network['ssid_hex'] in known
    return {**state(joined), 'networks': networks}


def connect(ssid, password, hidden, use_saved=False, keep=True):
    if not 1 <= len(ssid) <= 32:
        fail('WIFI_NAME_INVALID', 'A network name has 1 to 32 bytes.')
    if use_saved:
        key = saved_key(ssid)
    else:
        check_password(password)
        key = psk(ssid, password) if password else ''
    driver_up()
    start_supplicant()
    stop_dhcp()
    with Control() as ctl, Control(attach=True) as monitor:
        ctl.request('DISCONNECT')
        ok(ctl.request('REMOVE_NETWORK all'), 'the old network')
        network = ctl.request('ADD_NETWORK')
        if not network.isdigit():
            fail('WIFI_CONTROL_FAILED', 'wpa_supplicant refused a new network')
        ok(ctl.request('SET_NETWORK %s ssid %s' % (network, ssid.hex())), 'the network name')
        if key:
            ok(ctl.request('SET_NETWORK %s psk %s' % (network, key)), 'the password')
        else:
            ok(ctl.request('SET_NETWORK %s key_mgmt NONE' % network), 'an open network')
        if hidden:
            ok(ctl.request('SET_NETWORK %s scan_ssid 1' % network), 'a hidden network')
        ok(ctl.request('SELECT_NETWORK %s' % network), 'the network')
        outcome, missing = None, 0
        for event in monitor.events(JOIN_SECONDS):
            if event.startswith('CTRL-EVENT-CONNECTED'):
                outcome = 'joined'
                break
            if event.startswith('CTRL-EVENT-SSID-TEMP-DISABLED') and 'WRONG_KEY' in event:
                outcome = 'password'
                break
            if event.startswith('CTRL-EVENT-NETWORK-NOT-FOUND'):
                missing += 1
                if missing >= 3:
                    outcome = 'missing'
                    break
        if outcome != 'joined':
            ctl.request('REMOVE_NETWORK all')
            if outcome == 'password':
                fail('WIFI_AUTH_FAILED', 'The password is wrong.')
            if outcome == 'missing':
                fail('WIFI_NOT_FOUND', 'The network is not in range.')
            fail('WIFI_TIMEOUT', 'The network did not answer in time.')
    if not dhcp():
        fail('WIFI_NO_ADDRESS', 'Joined the network, but it gave no address.')
    # 2026-11-10: the exact time from the internet, without making the join wait for it.
    import clock
    clock.sync_in_background()
    result = state(ssid)
    if keep:
        # Joined either way; whether it was saved is said (2026-11-14 review: it was not, silently).
        result['remembered'] = remember(ssid, key) is True
    return result


def auto():
    """At the start of recovery: join the strongest remembered network in range. A network joined
    already stays (2026-11-14 review: a later try of the start went on to another saved network
    and so undid the owner's own choice made meanwhile)."""
    networks = saved_read()
    if not networks:
        return {**state(b''), 'saved_count': 0}
    now = state()
    if now['connected']:
        return {**now, 'saved_count': len(networks), 'tried': 0}
    found = scan()
    if found['connected']:
        return {**state(), 'saved_count': len(networks), 'tried': 0}
    tried = []
    for network in found['networks']:
        if network.get('saved') and network['supported'] and not network['connected']:
            tried.append(network['ssid'])
            try:
                return {**connect(bytes.fromhex(network['ssid_hex']), None, False, use_saved=True), 'saved_count': len(networks)}
            except WifiError:
                continue
    return {**state(), 'saved_count': len(networks), 'tried': len(tried)}


def disconnect():
    if interface_up() and supplicant_running():
        stop_dhcp()
        with Control() as ctl:
            ctl.request('DISCONNECT')
            ctl.request('REMOVE_NETWORK all')
    return state(b'')


# A recorded state for interface tests and previews: networks, and the one joined.
class Fixture:
    def __init__(self, root):
        self.path = Path(root) / 'wifi.json'
        self.data = json.loads(self.path.read_text()) if self.path.is_file() else {
            'available': True, 'on': False, 'connected': '', 'networks': []}

    def save(self):
        self.path.write_text(json.dumps(self.data))

    def state(self):
        joined = self.data.get('connected') or ''
        return {'available': self.data.get('available', True), 'on': self.data.get('on', False),
                'connected': bool(joined), 'ssid': joined, 'ssid_hex': joined.encode().hex(),
                'address': '10.42.0.23' if joined else ''}

    def scan(self):
        if not self.data.get('available', True):
            fail('WIFI_UNAVAILABLE', 'This recovery has no Wi-Fi driver.')
        self.data['on'] = True
        self.save()
        joined = (self.data.get('connected') or '').encode()
        known = set(self.data.get('saved', {}))
        lines = ['bssid / frequency / signal level / flags / ssid']
        for n, net in enumerate(self.data.get('networks', [])):
            flags = {'Open': '[ESS]', 'WPA': '[WPA-PSK-TKIP][ESS]', 'WPA2': '[WPA2-PSK-CCMP][ESS]',
                     'WPA3': '[RSN-SAE-CCMP][ESS]', 'WEP': '[WEP][ESS]'}[net.get('security', 'Open')]
            lines.append('02:00:00:00:00:%02x\t%d\t%d\t%s\t%s' % (n, net.get('frequency', 2437), net.get('signal', -60),
                                                                   flags, net['ssid']))
        networks = parse_scan('\n'.join(lines), joined)
        for network in networks:
            network['saved'] = network['ssid'] in known
        return {**self.state(), 'networks': networks}

    def connect(self, ssid, password, hidden, use_saved=False, keep=True):
        name = ssid.decode('utf-8', 'replace')
        saved = self.data.setdefault('saved', {})
        if use_saved:
            if name not in saved:
                fail('WIFI_NOT_SAVED', 'This network is not saved.')
            password = saved[name]
        check_password(password)
        for net in self.data.get('networks', []):
            if net['ssid'] == name:
                if net.get('password', '') != (password or ''):
                    fail('WIFI_AUTH_FAILED', 'The password is wrong.')
                self.data.update(on=True, connected=name)
                if keep:
                    saved[name] = password or ''
                self.save()
                return self.state()
        fail('WIFI_NOT_FOUND', 'The network is not in range.')

    def forget(self, ssid):
        self.data.setdefault('saved', {}).pop(ssid.decode('utf-8', 'replace'), None)
        self.save()
        return {**self.state(), 'saved_count': len(self.data['saved'])}

    def auto(self):
        saved = self.data.get('saved', {})
        for net in sorted(self.data.get('networks', []), key=lambda n: -n.get('signal', -100)):
            if net['ssid'] in saved:
                return {**self.connect(net['ssid'].encode(), None, False, use_saved=True), 'saved_count': len(saved)}
        return {**self.state(), 'saved_count': len(saved)}

    def disconnect(self):
        self.data['connected'] = ''
        self.save()
        return self.state()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WifiError('INVALID_ARGUMENT', message)


def read_password(path):
    if not path:
        return ''
    file = Path(path)
    try:
        value = file.read_text()
    finally:
        file.unlink(missing_ok=True)
    if '\n' in value.rstrip('\n'):
        fail('WIFI_PASSWORD_INVALID', 'A Wi-Fi password is one line.')
    return value.rstrip('\n')


def main(argv=None):
    operation = 'unknown'
    backend = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--fixture')
        sub = parser.add_subparsers(dest='operation', required=True, parser_class=Parser)
        for name in ('status', 'scan', 'disconnect', 'auto'):
            sub.add_parser(name)
        join = sub.add_parser('connect')
        name = join.add_mutually_exclusive_group(required=True)
        name.add_argument('--ssid')
        name.add_argument('--ssid-hex')
        join.add_argument('--password-file')
        join.add_argument('--hidden', action='store_true')
        join.add_argument('--saved', action='store_true', help='use the remembered key of this network')
        drop = sub.add_parser('forget')
        drop.add_argument('--ssid-hex', required=True)
        args = parser.parse_args(argv)
        operation = args.operation
        backend = Fixture(args.fixture) if args.fixture else None
        if backend is None:
            # One Wi-Fi operation at a time: the join at start and a search from the page do not
            # bring the driver up twice.
            RUN.mkdir(mode=0o700, exist_ok=True)
            lock = open(RUN / 'wifi.lock', 'w')
            fcntl.flock(lock, fcntl.LOCK_EX)
        if operation == 'connect':
            try:
                ssid = bytes.fromhex(args.ssid_hex) if args.ssid_hex is not None else args.ssid.encode()
            except ValueError:
                fail('WIFI_NAME_INVALID', 'The network name is not valid.')
            password = read_password(args.password_file)
            result = (backend or sys.modules[__name__]).connect(ssid, password, args.hidden, use_saved=args.saved)
        elif operation == 'forget':
            try:
                ssid = bytes.fromhex(args.ssid_hex)
            except ValueError:
                fail('WIFI_NAME_INVALID', 'The network name is not valid.')
            result = (backend or sys.modules[__name__]).forget(ssid)
        elif operation == 'auto':
            result = backend.auto() if backend else auto()
        elif operation == 'status':
            result = backend.state() if backend else state()
        elif operation == 'scan':
            result = backend.scan() if backend else scan()
        else:
            result = backend.disconnect() if backend else disconnect()
        reply = {'ok': True, 'operation': operation, **result}
        if backend is None:
            write_state(reply)
    except WifiError as error:
        reply = {'ok': False, 'operation': operation, 'error': {'code': error.code, 'message': str(error)}}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        reply = {'ok': False, 'operation': operation, 'error': {'code': 'WIFI_FAILED', 'message': str(error)}}
    if not reply['ok'] and backend is None and operation in ('connect', 'auto', 'disconnect', 'scan'):
        # 2026-11-14 review: a failed join had already let the old network go, and the interface and
        # SSH went on following a connection that was gone. What is joined now is written down.
        try:
            now = state()
            reply.update(now)
            write_state({**now, 'ok': False})
        except (OSError, WifiError, subprocess.SubprocessError, ValueError):
            pass
    print(json.dumps(reply, separators=(',', ':'), ensure_ascii=True), flush=True)
    return 0 if reply['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
