#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""The bootloader message (BCB) in misc: what Android asked of the recovery (2026-11-10).

When Android cannot mount its data it writes "boot-recovery" with "--prompt_and_wipe_data" and a
reason to the start of misc (seen on the phone: --reason=fs_mgr_mount_all:M02R, after a BOOT that
changed the verified-boot state). Samsung's and AOSP's recovery act on it and clear it; this one
reads it for the screen and clears it once Android's data was reset, so a request that was done
does not stay behind.

    bcb.py --device|--fixture DIR read
    bcb.py --device|--fixture DIR arrive    at the recovery's start: a plain "boot-recovery" is done and cleared
    bcb.py --device|--fixture DIR forget    the owner's word: a request to erase data is dropped, nothing is erased
"""
import argparse
import json
import os
from pathlib import Path
import sys

MISC = Path('/dev/disk/by-partlabel/misc')
SIZE = 2048  # struct bootloader_message: command[32] status[32] recovery[768] stage[32] reserved[1184]
WIPE_ARGUMENTS = ('--prompt_and_wipe_data', '--wipe_data')


def misc_path(context):
    return context.root / 'misc' if context.mode == 'fixture' else MISC


def read(path):
    """{'command', 'recovery': [lines], 'reason', 'wipe'}; an empty command when nothing is asked."""
    try:
        with open(path, 'rb') as f:
            data = f.read(SIZE)
    except OSError:
        return {'command': '', 'recovery': [], 'reason': '', 'wipe': False}
    def text(start, size):
        return data[start:start + size].split(b'\0', 1)[0].decode('utf-8', 'replace')
    command = text(0, 32)
    lines = [line for line in text(64, 768).splitlines() if line]
    reason = next((line.split('=', 1)[1] for line in lines if line.startswith('--reason=')), '')
    return {'command': command[:32], 'recovery': lines[:16], 'reason': reason[:128],
            'wipe': command == 'boot-recovery' and any(line in WIPE_ARGUMENTS for line in lines)}


def clear(path):
    """The message cleared, as recovery does once it acted on it; True when there was one."""
    if not read(path)['command']:
        return False
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        os.pwrite(fd, bytes(SIZE), 0)
        os.fsync(fd)
        left = os.pread(fd, SIZE, 0)
    finally:
        os.close(fd)
    if any(left):
        raise OSError('the bootloader message did not clear')
    return True


def clear_wipe_request(context):
    """After Android's data was reset: a wipe request in misc is done, and is cleared."""
    path = misc_path(context)
    if read(path)['wipe']:
        return clear(path)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    parser.add_argument('command', choices=('read', 'arrive', 'forget'))
    args = parser.parse_args(argv)
    path = args.fixture / 'misc' if args.fixture else MISC
    found = read(path)
    cleared = False
    if args.command == 'arrive' and found['command'] == 'boot-recovery' and not found['wipe']:
        # "Start the recovery" (adb reboot recovery, Android's power menu) is done by arriving here:
        # left in misc it would be read again at every later start (2026-11-11, seen after
        # adb reboot recovery). A request to erase data stays until the data is erased.
        try:
            cleared = clear(path)
        except OSError:
            cleared = False
    if args.command == 'forget' and found['wipe']:
        try:
            cleared = clear(path)
        except OSError:
            print(json.dumps({'ok': False, **found, 'cleared': False}, separators=(',', ':')))
            return 2
        found = read(path)
    print(json.dumps({'ok': True, **found, 'cleared': cleared}, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
