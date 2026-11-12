#!/usr/bin/python3
"""Where the recovery meant to restart to, so it can try once more (2026-11-10).

A restart from the recovery into the installed system now and then comes back to the recovery:
the preloader starts twice, the second start finds its SRAM header damaged ("sram header is not
match, format all"), the restart markers are gone, and LK starts the mode of the last start. It
happened in about one restart of ten (six times by 2026-11-10); restarting again always worked.

Before such a restart /init writes a22x-recovery/restart-intent.json on cache, with the mount
count of userdata's ext4 superblock. Any Linux system started from userdata mounts it with write
access, which counts up; so at the next start of the recovery, the same count and an intent less
than WINDOW seconds old (by the RTC) mean the restart came back here without starting anything:
/init restarts once more, without the interface. A changed count means the system did start (or
the phone was flashed from Download and started it), and the intent is removed; so is an intent
older than WINDOW, or one without a count (written by recovery 139). A second return in a row is
left alone, so a system that really cannot start does not loop.

2026-11-10: Android keeps userdata as encrypted f2fs, which has no such count (the intent said
"no-mount-count" and a restart that came back was not tried again; it happened twice on the phone).
Android's first-stage init mounts /metadata (ext4) with write access at every start, so its count
stands in. The age is taken from the hardware clock (RTC): the system clock may be set from the
internet in between, the RTC is not.

    restart_intent.py write normal|download
    restart_intent.py check      prints {"retry": "normal"|"download"|null, ...}
"""
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

RELATIVE = Path('a22x-recovery/restart-intent.json')
USERDATA = Path('/dev/disk/by-partlabel/userdata')
METADATA = Path('/dev/disk/by-partlabel/metadata')
RTC = Path('/sys/class/rtc/rtc0/since_epoch')
TARGETS = ('normal', 'download')
WINDOW = 60  # A return after a double preloader start took about 30 seconds.
RETRIES = 1


def userdata_mounts(device=USERDATA):
    """The mount count of userdata's ext4 superblock, or None (no ext4 there, or unreadable)."""
    try:
        with open(device, 'rb') as f:
            f.seek(1024)
            block = f.read(0x3A)
    except OSError:
        return None
    if len(block) < 0x3A or int.from_bytes(block[0x38:0x3A], 'little') != 0xEF53:
        return None
    return int.from_bytes(block[0x34:0x36], 'little')


def system_mounts(userdata=USERDATA, metadata=METADATA):
    """(which, count): the ext4 the installed system mounts with write access at every start -
    userdata for Linux, metadata for Android (its userdata is encrypted f2fs) - or (None, None)."""
    for which, device in (('userdata', userdata), ('metadata', metadata)):
        count = userdata_mounts(device)
        if count is not None:
            return which, count
    return None, None


def rtc_now(path=RTC):
    """Seconds by the hardware clock, which a time server never changes; the system clock without one."""
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return int(time.time())


def read(root):
    path = Path(root) / RELATIVE
    if not path.is_file() or path.is_symlink():
        return None
    try:
        record = json.loads(path.read_text()[:4096])
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get('target') not in TARGETS:
        return None
    for key in ('written_utc', 'attempts'):
        if not isinstance(record.get(key), int) or isinstance(record.get(key), bool):
            return None
    mounts = record.get('userdata_mounts')
    if mounts is not None and (not isinstance(mounts, int) or isinstance(mounts, bool)):
        return None
    if record.get('mounts_of', 'userdata') not in ('userdata', 'metadata'):
        return None
    return record


def save(root, record):
    path = Path(root) / RELATIVE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    part.write_text(json.dumps(record) + '\n')
    os.replace(part, path)


def write(root, target, now=None, mounts=None, mounts_of='userdata'):
    """Note the restart about to happen, with the MOUNTS count of MOUNTS_OF (userdata or metadata).
    A retry keeps its attempt count, so it is never repeated."""
    if target not in TARGETS:
        raise ValueError('target')
    now = int(time.time() if now is None else now)
    old = read(root)
    attempts = 0
    if old and old['target'] == target and 0 <= now - old['written_utc'] <= WINDOW:
        attempts = old['attempts']
    save(root, {'target': target, 'written_utc': now, 'attempts': attempts, 'userdata_mounts': mounts, 'mounts_of': mounts_of})
    return attempts


def check(root, now=None, mounts=None, mounts_of='userdata'):
    """At the start of the recovery, with the MOUNTS count of MOUNTS_OF now: the mode to restart
    into once more, or None."""
    now = int(time.time() if now is None else now)
    record = read(root)
    path = Path(root) / RELATIVE
    if record is None:
        path.unlink(missing_ok=True)
        return {'retry': None, 'reason': 'none'}
    if record.get('userdata_mounts') is None or mounts is None or record.get('mounts_of', 'userdata') != mounts_of:
        path.unlink(missing_ok=True)
        return {'retry': None, 'reason': 'no-mount-count'}
    if mounts != record['userdata_mounts']:
        path.unlink(missing_ok=True)
        return {'retry': None, 'reason': 'system-started'}
    age = now - record['written_utc']
    if age < 0 or age > WINDOW:
        path.unlink(missing_ok=True)
        return {'retry': None, 'reason': 'old', 'age': age}
    if record['attempts'] >= RETRIES:
        path.unlink(missing_ok=True)
        return {'retry': None, 'reason': 'retried', 'age': age, 'target': record['target']}
    save(root, {**record, 'attempts': record['attempts'] + 1, 'written_utc': now})
    return {'retry': record['target'], 'reason': 'came-back', 'age': age}


def main(argv=None):
    import filesystem as fs
    import settings
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ('write', 'check') or (args[0] == 'write' and (len(args) != 2 or args[1] not in TARGETS)):
        print('usage: restart_intent.py write normal|download | check', file=sys.stderr)
        return 2
    try:
        context = fs.DeviceContext()
        with settings.volume_root(context, 'cache', 'rw') as root:
            which, mounts = system_mounts()
            now = rtc_now()
            if args[0] == 'write':
                result = {'written': args[1], 'attempts': write(root, args[1], now, mounts, which or 'userdata'),
                          'mounts': mounts, 'mounts_of': which}
            else:
                result = {**check(root, now, mounts, which or 'userdata'), 'mounts_of': which}
    except (OSError, ValueError, fs.FilesystemError) as exc:
        print(json.dumps({'ok': False, 'retry': None, 'error': str(exc)[:200]}, separators=(',', ':')))
        return 1
    print(json.dumps({'ok': True, **result}, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
