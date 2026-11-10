#!/usr/bin/python3
"""Settings a phone system hands to the recovery (2026-11-10): Wi-Fi networks, language and time zone.

An Android app or a Linux system writes a22x-recovery/handoff.json on the cache volume:

    {"schema": "a22x.handoff.v1", "source": "android-app",
     "wifi": [{"ssid": "Home", "password": "secret-pass"}],
     "language": "uk", "timezone": "Europe/Kyiv", "time_utc": 1794306000}

At startup the recovery reads it once and deletes it before anything else, so a password never stays
on the disk. Networks are kept as WPA keys like the ones chosen on the phone, and the language and
time zone become the recovery's own settings. time_utc (when the file was written) only ever moves
the clock forward: a clock behind it is set to it, a correct clock is left alone. What was taken is
noted, without secrets, in
a22x-recovery/handoff-applied.json. Setup scripts go to a22x-recovery/scripts/ beside it; the install
pages offer them like a script on an SD card.
"""
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

RELATIVE = Path('a22x-recovery/handoff.json')
APPLIED = Path('a22x-recovery/handoff-applied.json')
LANGUAGES = ('en', 'uk', 'de', 'fr', 'pl', 'es')
LIMIT = 64 * 1024
MAX_WIFI = 8
EARLIEST = 1793491200  # 2026-11-01: a time before this release is not believed
LATEST = 4102444800  # 2100-01-01


def take(root):
    """Read the handoff file and remove it at once; None when there is none."""
    path = Path(root) / RELATIVE
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = path.read_bytes()[:LIMIT + 1]
    finally:
        path.unlink(missing_ok=True)
        os.sync()
    if len(data) > LIMIT:
        raise ValueError('The handoff file is too large')
    record = json.loads(data.decode('utf-8'))
    if not isinstance(record, dict) or record.get('schema') != 'a22x.handoff.v1':
        raise ValueError('Not an A22x handoff file')
    return record


def checked(record):
    """The parts that may be applied: (networks as (ssid bytes, password), language, time zone)."""
    import wifi
    networks = []
    for item in (record.get('wifi') or [])[:MAX_WIFI]:
        if not isinstance(item, dict):
            continue
        ssid, password = item.get('ssid'), item.get('password', '')
        if not isinstance(ssid, str) or not isinstance(password, str):
            continue
        raw = ssid.encode('utf-8')
        if not 1 <= len(raw) <= 32:
            continue
        try:
            wifi.check_password(password)
        except Exception:
            continue
        networks.append((raw, password))
    language = record.get('language')
    language = language if language in LANGUAGES else None
    zone = record.get('timezone')
    zone = zone if isinstance(zone, str) and len(zone) <= 64 and \
        re.fullmatch(r'[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2}', zone) else None
    return networks, language, zone


def set_clock(seconds):
    time.clock_settime(time.CLOCK_REALTIME, float(seconds))


def advance_clock(record, now=None, setter=None):
    """Move the clock forward to time_utc when it is behind by more than two minutes; never back."""
    value = record.get('time_utc')
    if not isinstance(value, int) or isinstance(value, bool) or not EARLIEST <= value <= LATEST:
        return None
    now = time.time() if now is None else now
    if now >= value - 120:
        return None
    (setter or set_clock)(value)
    return value


def apply_record(record, remember=None, load=None, store=None):
    """Apply a handoff; returns what was taken, without secrets."""
    import settings
    import wifi
    remember = remember or wifi.remember
    load, store = load or settings.load, store or settings.store
    networks, language, zone = checked(record)
    for ssid, password in reversed(networks):
        remember(ssid, wifi.psk(ssid, password) if password else '')
    changed = {}
    if language or zone:
        current = load()
        if language:
            current['language'] = changed['language'] = language
        if zone:
            current['timezone'] = changed['timezone'] = zone
        store(current)
    return {'applied_utc': int(time.time()), 'source': str(record.get('source') or '')[:32],
            'wifi': [ssid.decode('utf-8', 'replace') for ssid, _ in networks], **changed}


def main(argv=None):
    import filesystem as fs
    import settings
    from operation_lock import operation_lock
    args = (argv if argv is not None else sys.argv[1:]) or ['apply']
    if args != ['apply']:
        print('usage: handoff.py apply', file=sys.stderr)
        return 2
    context = fs.DeviceContext()
    try:
        with operation_lock(context.lock_path, context.mode):
            with settings.volume_root(context, 'cache', 'rw') as root:
                record = take(root)
    except (OSError, ValueError, fs.FilesystemError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:200]}))
        return 1
    if record is None:
        print(json.dumps({'ok': True, 'applied': None}))
        return 0
    try:
        applied = apply_record(record)
        clock = advance_clock(record)
        if clock:
            applied['clock_set_to'] = clock
    except (OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:200]}))
        return 1
    try:
        with operation_lock(context.lock_path, context.mode):
            with settings.volume_root(context, 'cache', 'rw') as root:
                path = root / APPLIED
                path.parent.mkdir(mode=0o700, exist_ok=True)
                path.write_text(json.dumps(applied) + '\n')
    except (OSError, ValueError, fs.FilesystemError):
        pass
    print(json.dumps({'ok': True, 'applied': applied}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
