#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Read-only device-mapper "linear" devices: Android's partitions in super, opened as Android does.

Android's first-stage init maps every partition that super's metadata names (system, vendor,
product, odm) onto the extents of super with a "linear" table (fs_mgr, libdm). This module does
the same through the kernel's device-mapper ioctls, and only ever read-only: the table is loaded
with DM_READONLY_FLAG, so the kernel opens super for reading alone and marks the new disk
read-only. While a mapping exists the kernel also holds super exclusively (an O_EXCL open of
super fails) and lists the mapping among super's holders in sysfs, where every helper of this
recovery that writes super looks first (backend/storage.py require_no_holders).

Layouts follow include/uapi/linux/dm-ioctl.h, interface version 4.
"""
import contextlib
import fcntl
import os
from pathlib import Path
import struct

CONTROL = '/dev/mapper/control'
# struct dm_ioctl: version[3], data_size, data_start, target_count, open_count, flags, event_nr,
# padding, dev, name[128], uuid[129], data[7]: 312 bytes.
IOCTL = struct.Struct('<3IIIIiIIIQ128s129s7s')
FIELDS = ('version0', 'version1', 'version2', 'data_size', 'data_start', 'target_count', 'open_count', 'flags',
          'event_nr', 'padding', 'dev', 'name', 'uuid', 'data')
# struct dm_target_spec: sector_start, length, status, next, target_type[16]: 40 bytes.
SPEC = struct.Struct('<QQiI16s')
NAME_LEN, TYPE_LEN = 128, 16
RESULT_SIZE = 16384


def _request(number):
    """_IOWR(DM_IOCTL = 0xfd, NUMBER, struct dm_ioctl)."""
    return (3 << 30) | (IOCTL.size << 16) | (0xfd << 8) | number


DEV_CREATE, DEV_REMOVE, DEV_SUSPEND, TABLE_LOAD, TABLE_STATUS = (
    _request(3), _request(4), _request(6), _request(9), _request(12))
READONLY_FLAG, STATUS_TABLE_FLAG = 1 << 0, 1 << 4


class DmError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def header(name, size=IOCTL.size, start=0, targets=0, flags=0):
    raw = name.encode()
    if not raw or len(raw) >= NAME_LEN or b'\0' in raw or b'/' in raw:
        raise DmError('INVALID_DM_NAME', 'A device-mapper name must be short, without "/"')
    return IOCTL.pack(4, 0, 0, size, start, targets, 0, flags, 0, 0, 0, raw, b'', b'')


def fields(buffer):
    """The dm_ioctl header at the start of BUFFER, by field name."""
    return dict(zip(FIELDS, IOCTL.unpack_from(buffer)))


def table_bytes(table):
    """TABLE ((sector_start, sectors, type, parameters), ...) as DM_TABLE_LOAD reads it: each spec
    with its parameters and a NUL after it, padded to 8 bytes; its "next" is its own size."""
    out = b''
    for start, sectors, kind, parameters in table:
        if not kind or len(kind.encode()) >= TYPE_LEN or sectors <= 0 or start < 0:
            raise DmError('INVALID_DM_TABLE', 'Invalid device-mapper target')
        body = parameters.encode() + b'\0'
        size = SPEC.size + len(body)
        size += -size % 8
        out += SPEC.pack(start, sectors, 0, size, kind.encode()) + body.ljust(size - SPEC.size, b'\0')
    return out


def parse_status(buffer):
    """What DM_TABLE_STATUS (with DM_STATUS_TABLE_FLAG) returned: the table, and whether the disk is
    read-only. Here "next" counts from the first spec, which starts at data_start."""
    head = fields(buffer)
    start, count, flags = head['data_start'], head['target_count'], head['flags']
    table, at = [], start
    for _ in range(count):
        if at + SPEC.size > len(buffer):
            raise DmError('INVALID_DM_STATUS', 'The kernel returned a cut table')
        sector, sectors, _status, following, kind = SPEC.unpack_from(buffer, at)
        parameters = buffer[at + SPEC.size:].split(b'\0', 1)[0]
        table.append((sector, sectors, kind.split(b'\0', 1)[0].decode('ascii', 'replace'),
                      parameters.decode('ascii', 'replace')))
        at = start + following
    return table, bool(flags & READONLY_FLAG)


def linear_table(extents, device):
    """EXTENTS ([physical_start, sectors], ... in 512-byte sectors of DEVICE "major:minor"), laid
    end to end, as libdm's DmTargetLinear lays a logical partition."""
    table, at = [], 0
    for start, sectors in extents:
        table.append((at, sectors, 'linear', '%s %d' % (device, start)))
        at += sectors
    return table


def _call(control, request, buffer):
    data = bytearray(buffer)
    fcntl.ioctl(control, request, data, True)
    return bytes(data)


@contextlib.contextmanager
def _control():
    fd = os.open(CONTROL, os.O_RDWR | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


def create(name, table, read_only=True):
    """Create NAME with TABLE, read-only unless READ_ONLY is false (the ZIP installer's room writes
    the logical partitions through such mappings, 2026-11-13), and make the table live (a resume
    without DM_SUSPEND_FLAG). Returns the new disk's number, "major:minor"."""
    body = table_bytes(table)
    with _control() as control:
        _call(control, DEV_CREATE, header(name))
        try:
            _call(control, TABLE_LOAD, header(name, IOCTL.size + len(body), IOCTL.size, len(table), READONLY_FLAG if read_only else 0) + body)
            done = _call(control, DEV_SUSPEND, header(name))
        except BaseException:
            with contextlib.suppress(OSError):
                _call(control, DEV_REMOVE, header(name))
            raise
    dev = fields(done)['dev']
    return '%d:%d' % (os.major(dev), os.minor(dev))


def remove(name):
    """Remove NAME; the kernel refuses (EBUSY) while anything has it open."""
    with _control() as control:
        _call(control, DEV_REMOVE, header(name))


def status(name):
    """The live table of NAME, and whether its disk is read-only."""
    with _control() as control:
        done = _call(control, TABLE_STATUS, header(name, RESULT_SIZE, 0, 0, STATUS_TABLE_FLAG).ljust(RESULT_SIZE, b'\0'))
    return parse_status(done)


def devices(root=Path('/sys/block')):
    """Every device-mapper disk: name -> (its number "major:minor", its kernel name "dm-N")."""
    found = {}
    for entry in sorted(root.glob('dm-*')):
        with contextlib.suppress(OSError):
            found[(entry / 'dm/name').read_text().strip()] = ((entry / 'dev').read_text().strip(), entry.name)
    return found
