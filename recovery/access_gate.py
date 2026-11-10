#!/usr/bin/python3
"""Ask on the phone before a computer gets a shell over SSH, SFTP or the UART console.

The password stays on the phone: a computer never sees it, it only waits for the answer.
This guards the recovery session. It cannot cover Download mode or a removed SD card.
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


class Gate:
    def __init__(self, base=BASE, settings=SETTINGS, poll=0.2):
        self.base, self.settings, self.poll = Path(base), Path(settings), poll

    def required(self, wait=30):
        """The UI writes the settings at startup; until then nobody can say there is no password."""
        deadline = time.monotonic() + wait
        while True:
            try:
                record = json.loads(self.settings.read_text())
                return bool(record.get('hash')) and record.get('access', 1) != 0
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    return False
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
