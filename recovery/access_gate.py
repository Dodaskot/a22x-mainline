#!/usr/bin/python3
"""Ask on the phone before a computer gets a shell over SSH, SFTP or the UART console.

The password stays on the phone: a computer never sees it, it only waits for the answer.
This guards the recovery session. It cannot cover Download mode or a removed SD card.

The door opens without asking only when settings that were read say so: no password, or the
owner's choice not to be asked. Settings the interface has not copied to RAM yet are read from
the disk; settings that cannot be read, or are damaged, keep it closed (2026-11-12, audit: before,
a computer was let in when the copy in RAM did not appear within 30 s).
"""
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

BASE = Path(os.environ.get('A22X_ACCESS_DIR', '/run/a22x-recovery/access'))
SETTINGS = Path(os.environ.get('A22X_SETTINGS_RAM', '/run/a22x-recovery/settings.json'))
WAIT_SECONDS = 120


def device_settings():
    """The settings as settings.py reads them from the disk; it leaves the copy in RAM."""
    import settings
    return settings.load()


class Gate:
    def __init__(self, base=BASE, settings=SETTINGS, poll=0.2, disk=None):
        self.base, self.settings, self.poll = Path(base), Path(settings), poll
        # The disk is read only for the copy in RAM the recovery itself keeps.
        self.disk = disk if disk is not None else (device_settings if Path(settings) == SETTINGS else None)

    def record(self, disk=True):
        """The settings, checked as settings.py checks them; None when they cannot be read now."""
        from settings import checked
        try:
            return checked(json.loads(self.settings.read_text()))
        except (OSError, ValueError):
            pass
        if not disk or self.disk is None:
            return None
        try:
            return checked(self.disk())
        except Exception:  # damaged, unreadable or busy: nothing is known, so nothing opens
            return None

    def required(self, wait=30):
        """Whether a computer must wait for the password: yes unless settings that were read say
        no. The disk is asked once; then the copy in RAM is waited for until WAIT runs out."""
        deadline = time.monotonic() + wait
        disk = True
        while True:
            record = self.record(disk)
            disk = False
            if record is not None:
                return bool(record.get('hash')) and record.get('access', 1) != 0
            if time.monotonic() >= deadline:
                return True
            time.sleep(self.poll)

    def granted(self):
        return (self.base / 'granted').is_file()

    def allow(self, channel, peer, timeout=WAIT_SECONDS):
        if not self.required() or self.granted():
            return True
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = uuid.uuid4().hex
        request, denied = self.base / f'request-{name}.json', self.base / f'denied-{name}.json'
        temporary = self.base / f'.{name}'
        temporary.write_text(json.dumps({'id': name, 'channel': channel, 'peer': ' '.join(peer.split())[:64]}))
        temporary.replace(request)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.granted():
                    return True
                if denied.exists() or time.monotonic() >= deadline:
                    return False
                time.sleep(self.poll)
        finally:
            request.unlink(missing_ok=True)
            denied.unlink(missing_ok=True)


def console():
    """The UART login. A closed door keeps reading the port, it never falls through to a shell."""
    for signum in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
        signal.signal(signum, signal.SIG_IGN)
    gate = Gate()
    # 2026-11-12 review: nothing is asked before someone is at the port. Deciding at once read the
    # settings from the disk at every start, holding the storage lease while init's one-shot steps
    # (flash.py resume, saved_boot.py tidy, handoff.py) needed it.
    print('\nA22x recovery console. Press Enter.', flush=True)
    while not sys.stdin.readline():
        time.sleep(1)
    while gate.required():
        if gate.granted():
            break
        print('\nA22x recovery is protected. Press Enter, then confirm on the phone.', flush=True)
        if not sys.stdin.readline():
            time.sleep(1)
            continue
        print('Waiting for the password on the phone...', flush=True)
        if gate.allow('uart', ''):
            break
        print('Access was not allowed.', flush=True)
    for signum in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
        signal.signal(signum, signal.SIG_DFL)
    os.execv('/bin/sh', ['sh', '-l'])


if __name__ == '__main__':
    if sys.argv[1:] == ['--console']:
        console()
    raise SystemExit(2)
