#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Why Android did not start: its kernel's last log, read from the memory that outlives a restart.

2026-11-12, on the owner's phone: Google's Android 15 image restarted for ever, and the reason stood
in the memory the bootloader keeps for the kernel's console (pstore, ramoops) all the while:

    NetBpfLoad: Android V requires kernel 4.19.
    init: Service bpfloader has 'reboot_on_failure' option and failed, shutting down system.
    reboot: Restarting system with command 'bpfloader-failed'

Android's kernel lays that memory out in its own way, so the recovery's kernel does not show the log
as a file; but it does not write over it either. This helper reads the region named on the kernel
command line (ramoops.mem_address, ramoops.mem_size) through /dev/mem, read-only, takes the lines an
Android kernel of this phone wrote (they carry the processor and the process: "(7)[1:init]"), puts
the ring in order, and says how the last start ended:

  code ""               an ordinary restart (to the recovery, to Download, powered off): nothing to say
  code KERNEL_TOO_OLD   this Android needs a newer kernel than the one in BOOT ("needs": 4.19)
  code INIT_GAVE_UP     Android's init restarted the phone itself ("reason" says why)
  code KERNEL_PANIC     the kernel stopped ("detail" is its line)
  code CUT_SHORT        the log ends without a restart: a watchdog, a held power key, an empty battery

    android_log.py --device|--fixture DIR read [--save FILE]

--save writes the whole last start as text. A fixture holds the region as pstore-raw.bin.
"""
import argparse
import contextlib
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import sys

MAX_REGION = 16 << 20
# "[    7.867003]  (6)[861:netbpfload]NetBpfLoad: ..." and "[    9.082616] -(0)[1:init]sec_reboot ..."
LINE = re.compile(rb'\[ {0,5}(\d{1,6})\.(\d{6})\] [ -]\((\d)\)\[(\d+):([^\]\n]{0,24})\]([^\n]{0,600})')
START_BELOW = 0.5      # a time below this after a later one: the kernel started again
ORDINARY = {'', 'recovery', 'download', 'bootloader', 'shutdown', 'normal', 'fota', 'fota_bl', 'nvbackup', 'nvrestore', 'nverase',
            'nvrecovery', 'sud', 'cmd', 'userrequested', 'adb', 'shell', 'power', 'sideload', 'sideload-auto-reboot', 'edl', 'rtc',
            'wipe_data', 'cp_crash', 'manual_reset', 'factory', 'cross_fail', 'recovery-update', 'keys', 'usbrecovery'}
RESTART = re.compile(r"reboot: Restarting system(?: with command '([^']*)')?")
POWER_OFF = re.compile(r'reboot: Power down|sec_power_off|Power off')
INIT_REASON = re.compile(r'init: Reboot start, reason: ([^\n]{0,120}?)(?:, reboot_target: ([^\n]{0,60}))?$')
PANIC = re.compile(r'Kernel panic - not syncing: ([^\n]{0,200})')
NEEDS_KERNEL = re.compile(r'requires kernel (\d+\.\d+)')
VERSION = re.compile(r'Linux version (\d+\.\d+\.\d+)')
WORTH = re.compile(r"requires kernel|reboot_on_failure|Reboot start, reason|Restarting system|Kernel panic|critical process|"
                   r"updatable process|Failed to mount|Could not start|exited \d+ times|init: Service '[^']+' \(pid \d+\) (?:exited with status [1-9]|received signal)")


def region(mode, fixture):
    """The bytes of the persistent store: from the fixture's file, or from /dev/mem at the address
    and size the kernel command line names. Empty when there is none."""
    if mode == 'fixture':
        with contextlib.suppress(OSError):
            return (Path(fixture) / 'pstore-raw.bin').read_bytes()[:MAX_REGION]
        return b''
    try:
        line = Path('/proc/cmdline').read_text()
        address = int(re.search(r'ramoops\.mem_address=(0x[0-9a-fA-F]+|\d+)', line).group(1), 0)
        size = int(re.search(r'ramoops\.mem_size=(0x[0-9a-fA-F]+|\d+)', line).group(1), 0)
    except (OSError, AttributeError, ValueError):
        return b''
    if address <= 0 or address % mmap.PAGESIZE or not 0 < size <= MAX_REGION:
        return b''
    try:
        fd = os.open('/dev/mem', os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return b''
    try:
        with mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ, offset=address) as view:
            return view[:]
    except (OSError, ValueError):
        return b''
    finally:
        os.close(fd)


def lines_of(raw):
    """Every line an Android kernel wrote, in the order of memory: (offset, seconds, text)."""
    found = []
    for match in LINE.finditer(raw):
        text = match.group(6).decode('utf-8', 'replace').rstrip('\r')
        found.append((match.start(), int(match.group(1)) + int(match.group(2)) / 1e6,
                      '[%s:%s]%s' % (match.group(4).decode('ascii', 'replace'), match.group(5).decode('utf-8', 'replace'), text)))
    return found


def last_start(found):
    """The lines of the last start, oldest first. The log is a ring: the newest line lies where the
    time falls back to the middle of an earlier start, not to the beginning of a new one."""
    if not found:
        return []
    seam = len(found) - 1
    for index in range(len(found) - 1):
        if found[index + 1][1] + 1e-6 < found[index][1] and found[index + 1][1] >= START_BELOW:
            seam = index
            break
    ordered = found[seam + 1:] + found[:seam + 1]
    begin = 0
    for index in range(len(ordered) - 1, 0, -1):
        if ordered[index][1] + 1e-6 < ordered[index - 1][1]:
            begin = index
            break
    return ordered[begin:]


def judge(start):
    """How the last start ended."""
    texts = [text for _, _, text in start]
    whole = '\n'.join(texts)
    result = {'found': True, 'code': '', 'restart': None, 'reason': '', 'needs': '', 'detail': '', 'kernel': '',
              'seconds': round(start[-1][1], 1), 'lines': []}
    version = VERSION.search(whole)
    if version:
        result['kernel'] = version.group(1)
    restart = None
    for text in texts:
        match = RESTART.search(text)
        if match:
            restart = match.group(1) or ''
        match = INIT_REASON.search(text)
        if match:
            result['reason'] = match.group(1).strip()[:120]
    powered_off = any(POWER_OFF.search(text) for text in texts[-40:])
    result['restart'] = restart
    panic = PANIC.search(whole)
    needs = NEEDS_KERNEL.search(whole)
    target = (restart or '').split(',')[0]
    if panic:
        result['code'], result['detail'] = 'KERNEL_PANIC', panic.group(1).strip()[:200]
    elif restart == 'bpfloader-failed' or (needs and 'bpfloader' in result['reason']):
        result['code'], result['needs'] = 'KERNEL_TOO_OLD', needs.group(1) if needs else ''
    elif restart is not None and target not in ORDINARY:
        result['code'] = 'INIT_GAVE_UP'
        result['reason'] = result['reason'] or restart
    elif restart is None and not powered_off:
        result['code'] = 'CUT_SHORT'
    if result['code']:
        # What led to it stands before init's decision; what init stops afterwards is only the way down.
        decided = next((index for index, text in enumerate(texts) if INIT_REASON.search(text)), len(texts))
        worth = [text for text in texts[:decided + 1] if WORTH.search(text)][-4:]
        last = next((text for text in reversed(texts) if RESTART.search(text) or PANIC.search(text)), None)
        if last and last not in worth:
            worth.append(last)
        result['lines'] = [text[:180] for text in worth]
    return result


def read(mode, fixture, save=None):
    raw = region(mode, fixture)
    start = last_start(lines_of(raw))
    if not start:
        return {'found': False, 'code': ''}
    result = judge(start)
    text = ''.join('[%12.6f] %s\n' % (seconds, line) for _, seconds, line in start)
    result['id'] = hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:16]
    result['log_lines'] = len(start)
    if save:
        try:
            fd = os.open(save, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', errors='replace') as out:
                out.write(text)
            result['saved'] = str(save)
        except OSError:
            result['saved'] = ''
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('read')
    command.add_argument('--save', type=Path)
    args = parser.parse_args(argv)
    try:
        result = {'ok': True, 'operation': 'read', **read('fixture' if args.fixture else 'device', args.fixture, args.save)}
        code = 0
    except (OSError, ValueError) as error:
        result = {'ok': False, 'operation': 'read', 'found': False, 'code': '', 'error': {'code': 'IO_ERROR', 'message': str(error)[:200]}}
        code = 2
    print(json.dumps(result, separators=(',', ':')), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
