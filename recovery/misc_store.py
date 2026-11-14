#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small records of the recovery in the misc partition, each kept twice (2026-11-14).

The settings and the saved Wi-Fi list lived in files on cache, or on userdata: every way a
filesystem can show an older state (a journal not replayed, a volume that will not mount, a copy
restored, a release that wrote elsewhere) became a way to read a password as none. Here they are
kept as U-Boot keeps its redundant environment, and LUKS2 its two headers: two copies of each
record, each with a generation and a SHA-256, on the raw partition. A read takes the whole copy of
the newest generation; a write replaces the other copy, so a write that stops halfway leaves the
one before it whole. No filesystem, no mount, no journal.

Where: the last 128 KiB of misc (512 KiB on this phone). Android uses only its first 64 KiB
(bootloader_message.h: 0-2K the bootloader message, 2K-16K vendor, 16K-32K the wipe package,
32K-64K system space; seen on the phone: the bootloader message at 0, memtag and kcmdline
messages at 32K); the LK writes only the bootloader message (strings of lk.img: "boot-recovery",
"--wipe_data"); the kernel never touches misc (searched: drivers/samsung, drivers/misc/mediatek,
the partition code). Backups of misc from 2026-10-07 and the partition now: nothing beyond 64 KiB
was ever written. A record is written only where it finds its own copies or zeros: bytes there
that are no copy of this recovery's are never written over (Foreign).

Each record: two slots of 32 KiB. A slot: its first sector holds the header (magic, name,
generation, length, SHA-256 of header and payload), the payload starts at 512. The header is
written first and made durable, then the payload: a slot cut short has the magic and a wrong
hash, never a payload that looks like another's. Every write fills both slots with the same
payload, the older one first and read back before the other (2026-11-14 review: with one slot
per change, damage to the newer one brought back the state before it, a password with it or
without). So whole records always have two equal copies: one damaged copy, or one that cannot
be read, leaves the newest; a copy with the newest generation beside a different older one is
a write that stopped between them.

The size is the stock PIT's (A22X_EUR_OPEN.pit: misc 1024 blocks of 512 bytes, one PIT for every
storage size of the model).
"""
import contextlib
import errno
import hashlib
import os
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lp_metadata import drop_cached

MISC = Path('/dev/disk/by-partlabel/misc')
MAGIC = b'A22XMSC1'
SECTOR = 512
SLOT = 32 << 10
PAYLOAD_MAX = SLOT - SECTOR
# name -> offset of its first slot; the second follows it.
AREAS = {'settings': 384 << 10, 'wifi': 448 << 10}
END = 512 << 10
HEADER = struct.Struct('<8s8sQII')  # magic, name, generation, length, flags (0)


class Damaged(ValueError):
    """Both copies are there and neither is whole."""


class Foreign(OSError):
    """Bytes that are no copy of this recovery's stand where a record would go: never written over."""


def path():
    """misc of the internal eMMC; A22X_MISC names a file instead (tests)."""
    return Path(os.environ.get('A22X_MISC', str(MISC)))


def _internal(target):
    """Whether TARGET is a partition of the internal eMMC (bcb.internal: a card or stick with an
    Android layout has a misc of its own, and by-partlabel may name it)."""
    if target != MISC:
        return True
    return re.fullmatch(r'/dev/mmcblk0p[0-9]+', os.path.realpath(target)) is not None


def _open(write):
    target = path()
    if not _internal(target):
        raise OSError(errno.ENODEV, 'misc is not on the internal storage')
    fd = os.open(target, (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC)
    try:
        if os.lseek(fd, 0, os.SEEK_END) < END:
            raise OSError(errno.ENOSPC, 'misc is smaller than the records need')
    except BaseException:
        os.close(fd)
        raise
    return fd


def _name(name):
    if name not in AREAS:
        raise ValueError('No such record: %s' % name)
    return name.encode().ljust(8, b'\0')


def _read_slot(fd, offset):
    data = os.pread(fd, SLOT, offset)
    if len(data) != SLOT:
        raise OSError(errno.EIO, 'misc ended before a record')
    return data


def classify(name, data):
    """('empty',) all zeros; ('valid', generation, payload); ('torn',) this record's magic and
    name, not whole; ('foreign',) anything else; ('unreadable',) when DATA is None."""
    if data is None:
        return ('unreadable',)
    if not any(data):
        return ('empty',)
    magic, label, generation, length, flags = HEADER.unpack_from(data)
    if magic != MAGIC:
        # A first write cut short in the first bytes of its header: a beginning of the magic and
        # nothing else (the test found it read as another's, and every write refused for good).
        start = next(index for index in range(len(MAGIC) + 1) if index == len(MAGIC) or data[index] != MAGIC[index])
        return ('torn',) if start and not any(data[start:]) else ('foreign',)
    if label != _name(name):
        # This recovery's magic is this recovery's: a header cut inside its name, or one that is
        # not whole, is a copy cut short (2026-11-14 review: read as another's).
        return ('torn',)
    if flags or length > PAYLOAD_MAX:
        return ('torn',)
    payload = data[SECTOR:SECTOR + length]
    digest = hashlib.sha256(data[:HEADER.size] + payload).digest()
    if data[HEADER.size:HEADER.size + 32] != digest:
        return ('torn',)
    return ('valid', generation, payload)


def _slots(fd, name):
    """Both slots classified; one that cannot be read is 'unreadable', the other still counts
    (2026-11-14 review: an EIO in one copy failed the read of the whole one beside it)."""
    found = []
    for index in (0, 1):
        try:
            found.append(classify(name, _read_slot(fd, AREAS[name] + index * SLOT)))
        except OSError:
            found.append(classify(name, None))
    return found


def _area(fd):
    """{name: its two slots} for every record."""
    return {name: _slots(fd, name) for name in AREAS}


def owned(area):
    """Whether the area is this recovery's: a copy of its own is in it, whole or cut short; other bytes
    in it are then damage to it (2026-11-14 reviews: read as no record, both headers lost read a
    password as none). The area is taken only while no bytes of another are in it (write): a stray
    byte on a phone that never had a record is no damage then, and asks for no erase (2026-11-14
    reviews). An area that is not this recovery's is never written: the settings go to the older
    store then (settings.write_disk)."""
    return any(slot[0] in ('valid', 'torn') for pair in area.values() for slot in pair)


def decide(slots, is_owned=True):
    """The payload the two SLOTS hold (classify's answers): the newest whole copy. Without one: None
    when no record was ever whole (both empty; one empty beside one cut short, which only a first
    write that stopped leaves; or another's bytes in an area that is not this recovery's,
    IS_OWNED False); OSError when a copy cannot be read now; else Damaged."""
    whole = [slot for slot in slots if slot[0] == 'valid']
    if whole:
        return max(whole, key=lambda slot: slot[1])[2]
    kinds = sorted(slot[0] for slot in slots)
    if 'unreadable' in kinds:
        raise OSError(errno.EIO, 'A copy of the record cannot be read now')
    if kinds in (['empty', 'empty'], ['empty', 'torn']):
        return None
    if not is_owned and 'torn' not in kinds:
        return None
    raise Damaged('Neither copy of the record is whole')


def state(name):
    """(payload, twice, readable): read's payload; whether both slots hold it whole (a write that
    stopped between them leaves one, which read_disk writes again); whether every slot of the area
    could be read (no repair without it: 2026-11-14 review, the older copy was written over the
    newer one that failed to read once). When the answer depends on whether the area is this
    recovery's and a slot of it cannot be read now, OSError (2026-11-14 review)."""
    _name(name)
    fd = _open(False)
    try:
        area = _area(fd)
        slots = area[name]
        readable = not any(slot[0] == 'unreadable' for pair in area.values() for slot in pair)
        if not readable and not any(slot[0] == 'valid' for slot in slots) and any(slot[0] == 'foreign' for slot in slots):
            raise OSError(errno.EIO, 'A slot of misc cannot be read now')
        payload = decide(slots, owned(area))
        twice = all(slot[0] == 'valid' for slot in slots) and slots[0][2] == slots[1][2]
        return payload, twice, readable
    finally:
        os.close(fd)


def read(name):
    """The payload of record NAME, bytes; None when none was ever kept. OSError when misc cannot be
    read now; Damaged when both copies are damaged."""
    return state(name)[0]


def _write_slot(fd, name, index, generation, payload):
    offset = AREAS[name] + index * SLOT
    head = HEADER.pack(MAGIC, _name(name), generation, len(payload), 0)
    head += hashlib.sha256(head + payload).digest()
    sector = head.ljust(SECTOR, b'\0')
    body = payload.ljust(SLOT - SECTOR, b'\0')
    os.pwrite(fd, sector, offset)
    os.fsync(fd)
    os.pwrite(fd, body, offset + SECTOR)
    os.fsync(fd)
    drop_cached(fd)
    if os.pread(fd, SLOT, offset) != sector + body:
        raise OSError(errno.EIO, 'misc did not keep the record')


def write(name, payload):
    """PAYLOAD (bytes) as record NAME, in both slots: first over the one that is not the newest whole
    copy, read back, then over that one, so a stop at any point leaves a whole copy of the record
    before or after. Called under the operation lock. OSError when misc cannot be written, or a copy
    of the record cannot be read now; Foreign when the area is not this recovery's and holds
    another's bytes, or a slot that cannot be read now (see owned)."""
    _name(name)
    if len(payload) > PAYLOAD_MAX:
        raise ValueError('The %s record is too large for misc' % name)
    fd = _open(True)
    try:
        area = _area(fd)
        slots = area[name]
        if not owned(area):
            # Taken only when every slot of the area was seen and none holds another's bytes: one
            # that cannot be read may hold them. Not taken, the settings stay in the older store
            # (Foreign; 2026-11-14 reviews).
            if any(slot[0] in ('unreadable', 'foreign') for pair in area.values() for slot in pair):
                raise Foreign(errno.EEXIST, 'The area of the %s record is not this recovery\'s' % name)
        if any(slot[0] == 'unreadable' for slot in slots):
            # Never past a copy of its own that cannot be read: it may be the newer one, of a save
            # that stopped between the copies, and the older would be written back as the newest
            # (2026-11-14 review: a password set read as none for good). The save stays in RAM
            # (exit 3) until the copy reads again; a lasting media error there is one of the
            # bounds in SETTINGS_API.md.
            raise OSError(errno.EIO, 'A copy of the %s record cannot be read now' % name)
        whole = [index for index in (0, 1) if slots[index][0] == 'valid']
        newest = max(whole, key=lambda index: slots[index][1]) if whole else None
        generation = slots[newest][1] if newest is not None else 0
        first = 0 if newest is None else 1 - newest
        _write_slot(fd, name, first, generation + 1, payload)
        _write_slot(fd, name, 1 - first, generation + 2, payload)
    finally:
        os.close(fd)


def clear(name):
    """Record NAME gone, both slots zeroed: the reset of damaged settings, which the owner asked for
    after userdata was erased; whatever stands there goes, another's bytes too."""
    _name(name)
    fd = _open(True)
    try:
        for index in (0, 1):
            os.pwrite(fd, bytes(SLOT), AREAS[name] + index * SLOT)
            os.fsync(fd)
        drop_cached(fd)
        for index in (0, 1):
            if any(_read_slot(fd, AREAS[name] + index * SLOT)):
                raise OSError(errno.EIO, 'misc did not clear the record')
    finally:
        os.close(fd)


def field(name, key):
    """One text field of a JSON record, for /init's boot splash (the theme): '' when there is none,
    or none can be had."""
    import json
    try:
        value = json.loads(read(name).decode()).get(key)
    except (OSError, ValueError, AttributeError, TypeError):
        return ''
    return value if isinstance(value, str) else ''


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'field':
        print(field(sys.argv[2], sys.argv[3]))
        sys.exit(0)
    with contextlib.suppress(BrokenPipeError):
        for record in AREAS:
            try:
                value = read(record)
                print(record, 'none' if value is None else '%d bytes' % len(value))
            except (OSError, ValueError) as error:
                print(record, 'error:', error)
