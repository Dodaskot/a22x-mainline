#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""A screenshot of the recovery, packed and put where it can be found (2026-11-11).

The interface hands over the screen as PNG scanlines (a filter byte and RGB for each row). This
helper packs them with zlib and writes NAME.png to the first place that takes it:

  card   A22xScreenshots on an SD card or a USB drive (a USB drive first). One that is mounted for
         writing is used as it is; otherwise it is mounted for writing for the moment and left as
         it was found
  cache  a22x-recovery/screenshots, opened for writing for the moment; the oldest go when they take
         more than 24 MiB together
  ram    the recovery's folder in RAM, gone at the next restart

Nothing is mounted while another operation holds storage: then only a place that is writable
already is used.

    screenshot.py --device|--fixture DIR save --raw FILE --width W --height H --name NAME.png [--result FILE]
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib

import filesystem as fs
from operation_lock import operation_lock

CARD_FOLDER = 'A22xScreenshots'
CACHE_FOLDER = 'a22x-recovery/screenshots'
CACHE_LIMIT = 24 << 20
RAM_FOLDER = Path('/run/a22x-recovery/screenshots')
MAX_SIDE = 4096
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.png')


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png(raw, width, height):
    """The scanlines as a PNG file: 8-bit RGB, packed by zlib."""
    row = width * 3 + 1
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and len(raw) == row * height):
        fs.fail('INVALID_SCREENSHOT', 'The screen was not handed over whole')
    if any(raw[at] > 4 for at in range(0, len(raw), row)):
        fs.fail('INVALID_SCREENSHOT', 'The screen was not handed over as PNG rows')
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def removable(context, volume):
    if context.mode == 'device':
        return volume.get('external') is True
    return volume['id'].startswith(('sdcard', 'usb-'))


def folder_fd(pin, folder):
    """FOLDER on the pinned volume, made when it is not there: an open directory."""
    fd = os.dup(pin.fd)
    try:
        for part in folder.split('/'):
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=fd)
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
            pin.check_fd(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_new(folder, name, data):
    """DATA as NAME in FOLDER, or as NAME-2, NAME-3 when the name is taken: the name it got."""
    stem = name[:-4]
    for attempt in range(1, 100):
        final = name if attempt == 1 else '%s-%d.png' % (stem, attempt)
        try:
            fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
        except FileExistsError:
            continue
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(final, dir_fd=folder)
            raise
        finally:
            os.close(fd)
        return final
    fs.fail('SCREENSHOT_NAME_EXHAUSTED', 'No name is free for this screenshot')


def prune(folder, limit):
    """The oldest screenshots leave FOLDER until the rest takes no more than LIMIT."""
    files = []
    for name in os.listdir(folder):
        if name.endswith('.png'):
            with contextlib.suppress(OSError):
                st = os.stat(name, dir_fd=folder, follow_symlinks=False)
                files.append((st.st_mtime_ns, name, st.st_size))
    files.sort(reverse=True)
    total, removed = 0, 0
    for index, (_, name, size) in enumerate(files):
        total += size
        if total > limit and index:  # the newest always stays
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=folder)
                removed += 1
    return removed


def store(context, volume, folder, name, data, limit=None):
    """DATA in FOLDER on VOLUME, which is opened for writing for the moment when it is not, and
    left as it was found. Refused with a code when the volume cannot take it now.

    A volume that has to be mounted for this is mounted under the recovery's operation lock, taken
    without waiting: while a job holds it (a card being partitioned, cache in use), the screenshot
    goes elsewhere (2026-11-13 review: the mount came between a partition table and its mkfs)."""
    found = context.volume(volume)
    if not found['mounted'] or found['read_only']:
        with operation_lock(context.lock_path, context.mode):
            return store_now(context, volume, folder, name, data, limit, context.volume(volume))
    return store_now(context, volume, folder, name, data, limit, found)


def store_now(context, volume, folder, name, data, limit, found):
    was_mounted, was_read_only = found['mounted'], found['read_only']
    opened = False
    if not was_mounted or was_read_only:
        if not found.get('mountable') or not found.get('write_supported'):
            fs.fail('VOLUME_READ_ONLY', 'The volume cannot be opened for writing')
        if was_mounted and found.get('mount_owner') != 'filesystem':
            fs.fail('VOLUME_READ_ONLY', 'The volume is mounted read-only by another part of the recovery')
        context.mount(volume, 'rw', True, found['volume_token'])
        opened = True
    try:
        with fs.PinnedVolume(context, volume, writing=True) as pin:
            fd = folder_fd(pin, folder)
            try:
                final = write_new(fd, name, data)
                if limit:
                    prune(fd, limit)
                os.fsync(fd)
            finally:
                os.close(fd)
            label = pin.volume.get('label') or volume
    finally:
        if opened:
            with contextlib.suppress(fs.FilesystemError, OSError):
                if was_mounted:
                    context.mount(volume, 'ro', True, context.volume(volume)['volume_token'])
                else:
                    context.unmount(volume)
    return {'volume': volume, 'label': label, 'path': '/%s/%s' % (folder, final), 'bytes': len(data)}


def ram_folder(context):
    return RAM_FOLDER if context.mode == 'device' else context.root / 'ram-screenshots'


def save(context, data, name):
    """The screenshot on a card, else on cache, else in RAM: where it went."""
    cards = [v for v in context.volumes() if removable(context, v) and v.get('volume_token') and (v['mounted'] or v['mountable'])]
    cards.sort(key=lambda v: (not v['id'].startswith('usb'), v['id']))
    refused = []
    for where, volume, folder, limit in [('card', v['id'], CARD_FOLDER, None) for v in cards] + [('cache', 'cache', CACHE_FOLDER, CACHE_LIMIT)]:
        try:
            return {'where': where, **store(context, volume, folder, name, data, limit)}
        except (OSError, fs.FilesystemError) as error:
            refused.append('%s: %s' % (volume, getattr(error, 'code', None) or type(error).__name__))
    folder = ram_folder(context)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        final = write_new(fd, name, data)
        prune(fd, CACHE_LIMIT)
    finally:
        os.close(fd)
    return {'where': 'ram', 'volume': None, 'label': 'RAM', 'path': str(folder / final), 'bytes': len(data), 'refused': refused}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('save')
    command.add_argument('--raw', type=Path, required=True)
    command.add_argument('--width', type=int, required=True)
    command.add_argument('--height', type=int, required=True)
    command.add_argument('--name', required=True)
    command.add_argument('--result', type=Path)
    args = parser.parse_args(argv)
    try:
        if not NAME.fullmatch(args.name):
            fs.fail('INVALID_ARGUMENT', 'The screenshot needs a plain name that ends in .png')
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        data = png(args.raw.read_bytes(), args.width, args.height)
        result = {'ok': True, 'operation': 'save', **save(context, data, args.name)}
        code = 0
    except (OSError, ValueError, fs.FilesystemError) as error:
        result = {'ok': False, 'operation': 'save', 'error': {'code': getattr(error, 'code', None) or 'IO_ERROR', 'message': str(error)}}
        code = 2
    line = json.dumps(result, separators=(',', ':'))
    if args.result:
        with contextlib.suppress(OSError):
            args.result.write_text(line + '\n')
    print(line, flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
