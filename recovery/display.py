#!/usr/bin/env python3
"""A22x recovery refresh, brightness and backlight blank/wake control.

Clients never open DRM: closing its last descriptor makes this kernel restore
the fbdev mode. Only kernel-advertised DSI timings are used, with the existing
framebuffer, position and CRTC. No storage-operation lease is involved.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import array
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import sys
import time
import uuid

RUN = Path('/run/a22x-recovery')
SOCKET = RUN / 'display.sock'
LOCK = RUN / 'display.lock'
RECORD = RUN / 'display-service.json'
BACKLIGHT_RECORD = RUN / 'display-backlight.json'
BACKLIGHT_CLASS = Path('/sys/class/backlight')
PANEL_COMPATIBLE = b'jdi,hx83112f-a22x'
PANEL_DRIVER = 'panel-jdi-hx83112f'
BACKLIGHT_SCHEMA = 'a22x.display.backlight.v1'
SAFE_PERCENT = 50
MAX_REQUEST = 1024
MAX_RESPONSE = 8192
IPC_TIMEOUT = 2.0
WIDTH, HEIGHT = 1080, 2408
RATES = (60, 90)
MODE_BYTES = 68
REQUEST_SCHEMA = 'a22x.display.request.v1'
SERVICE_SCHEMA = 'a22x.display.service.v1'


class DisplayError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def require(condition, code, message):
    if not condition:
        raise DisplayError(code, message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'INVALID_REQUEST', 'Duplicate JSON keys are not accepted')
        result[key] = value
    return result


def bounded_read(path, limit=65536):
    with open(path, 'rb') as source:
        value = source.read(limit + 1)
    require(len(value) <= limit, 'INPUT_TOO_LARGE', 'Recovery metadata exceeded its size bound')
    return value


def regular_json(path, limit=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and opened.st_uid == 0 and opened.st_nlink == 1
                and not opened.st_mode & 0o022 and opened.st_size <= limit,
                'UNSAFE_STATE_FILE', 'Display state must be a bounded protected root-owned regular file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(limit + 1)
        require(len(raw) <= limit, 'INPUT_TOO_LARGE', 'Display state exceeded its size bound')
        return json.loads(raw, object_pairs_hook=unique_object)
    finally:
        os.close(fd)


def no_symlinks(path):
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'UNSAFE_STATE_PATH', 'Display state paths may not contain symlinks')


def mount_for(path):
    """Read metadata only; never acquire or alter any storage mount."""
    matches = []
    for line in bounded_read('/proc/self/mountinfo', 1024 * 1024).decode().splitlines():
        left, separator, right = line.partition(' - ')
        fields = left.split()
        if not separator or len(fields) < 6:
            continue
        point = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        if point == path or point in path.parents:
            matches.append((len(str(point)), fields[0], fields[2], str(point), right.split()[0]))
    require(matches, 'MOUNT_UNAVAILABLE', 'The display control path has no identified mount')
    return max(matches)


def backlight_unavailable(reason):
    return {'brightness_supported': False, 'blank_supported': False,
            'brightness_percent': None, 'brightness_raw': None, 'brightness_max': None,
            'brightness_requested_raw': None, 'brightness_actual_raw': None,
            'blanked': None, 'last_nonzero_percent': None, 'backlight_reason': reason}


def percent_raw(percent, maximum):
    require(type(percent) is int and 1 <= percent <= 100,
            'INVALID_BRIGHTNESS', 'Brightness must be an integer from 1 to 100 percent')
    return max(1, (maximum * percent + 50) // 100)


def raw_percent(raw, maximum):
    return max(1, (raw * 100 + maximum // 2) // maximum) if raw else 0


class BacklightIO:
    panel_off = False

    """Only the unique native A22x panel, with pinned sysfs attribute FDs."""
    def __init__(self, selected):
        self.path, self.fds, self.identities = Path(selected), {}, {}
        try:
            self.real = self.path.resolve(strict=True)
            require(self.real.is_relative_to('/sys/devices'), 'BACKLIGHT_PATH_INVALID',
                    'Backlight class entry must resolve inside sysfs devices')
            no_symlinks(self.real)
            self.mount = mount_for(self.real)
            require(self.mount[-1] == 'sysfs', 'BACKLIGHT_PATH_INVALID', 'Backlight attributes must reside on sysfs')
            self.device = (self.real / 'device').resolve(strict=True)
            require(self.device.is_relative_to('/sys/devices'), 'BACKLIGHT_PATH_INVALID', 'Unexpected backlight parent')
            compatible = bounded_read(self.device / 'of_node/compatible', 512).split(b'\0')
            require(PANEL_COMPATIBLE in compatible, 'BACKLIGHT_WRONG_PANEL', 'Backlight must belong to the native A22x panel')
            self.driver = (self.device / 'driver').resolve(strict=True)
            require(self.driver.is_relative_to('/sys/bus') and self.driver.name == PANEL_DRIVER,
                    'BACKLIGHT_WRONG_DRIVER', 'Backlight must use the qualified native A22x panel driver')
            self.identity = str(self.real)
            for name in ('brightness', 'actual_brightness', 'max_brightness', 'bl_power'):
                path = self.real / name
                fd = os.open(path, (os.O_RDWR if name == 'brightness' else os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC)
                self.fds[name] = fd
                st = os.fstat(fd)
                require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                        and not st.st_mode & 0o022, 'BACKLIGHT_PATH_INVALID', 'Backlight attribute is not a protected sysfs file')
                self.identities[name] = (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid)
            self.maximum = self.integer('max_brightness')
            require(self.maximum == 255, 'BACKLIGHT_RANGE_INVALID', 'The qualified eight-bit A22x panel must advertise maximum 255')
            require(bounded_read(self.real / 'type', 32).strip() == b'raw',
                    'BACKLIGHT_TYPE_INVALID', 'The native panel must expose a raw backlight')
            self.read()
        except BaseException:
            self.close()
            raise

    def integer(self, name):
        raw = os.pread(self.fds[name], 33, 0)
        require(re.fullmatch(rb'[0-9]{1,6}\n?', raw) is not None,
                'BACKLIGHT_READ_INVALID', 'Backlight returned an invalid bounded integer')
        return int(raw)

    def recheck(self):
        require(self.path.resolve(strict=True) == self.real and mount_for(self.real) == self.mount,
                'BACKLIGHT_CHANGED', 'Backlight device or sysfs mount changed')
        require((self.real / 'device').resolve(strict=True) == self.device,
                'BACKLIGHT_CHANGED', 'Backlight parent changed')
        require((self.device / 'driver').resolve(strict=True) == self.driver,
                'BACKLIGHT_CHANGED', 'Backlight panel driver changed')
        for name, fd in self.fds.items():
            opened, named = os.fstat(fd), (self.real / name).lstat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid)
            require(identity(opened) == identity(named) == self.identities[name],
                    'BACKLIGHT_CHANGED', 'Pinned backlight attribute changed')
        require(self.integer('max_brightness') == self.maximum,
                'BACKLIGHT_CHANGED', 'Panel maximum brightness changed')

    def read(self):
        self.recheck()
        requested, actual = self.integer('brightness'), self.integer('actual_brightness')
        require(0 <= requested <= self.maximum and 0 <= actual <= self.maximum,
                'BACKLIGHT_READ_INVALID', 'Panel brightness is outside its advertised range')
        # The kernel reports 4 while the panel is off. Only this service may have done that.
        require(self.integer('bl_power') == (4 if self.panel_off else 0) or self.integer('bl_power') == 0,
                'BACKLIGHT_POWER_CHANGED', 'Panel power state changed outside backlight control')
        return requested, actual

    def write(self, raw):
        require(type(raw) is int and 0 <= raw <= self.maximum, 'INVALID_BRIGHTNESS', 'Invalid raw brightness')
        self.recheck()
        data = (str(raw) + '\n').encode()
        require(os.pwrite(self.fds['brightness'], data, 0) == len(data),
                'BACKLIGHT_WRITE_FAILED', 'Panel brightness write was incomplete')

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


def discover_backlight_io():
    entries = []
    for entry in BACKLIGHT_CLASS.iterdir():
        require(len(entries) < 16, 'BACKLIGHT_COUNT_LIMIT', 'Too many backlight class entries')
        entries.append(entry)
    candidates = []
    for entry in entries:
        try:
            compatible = bounded_read(entry / 'device/of_node/compatible', 512).split(b'\0')
            if PANEL_COMPATIBLE in compatible:
                candidates.append(entry)
        except FileNotFoundError:
            continue
    require(len(candidates) == 1, 'BACKLIGHT_UNAVAILABLE', 'Exactly one native A22x panel backlight is required')
    return BacklightIO(candidates[0])


def close_except(keep):
    """The crash guard must not keep DRM, IPC, singleton or storage lease FDs."""
    entries = os.listdir('/proc/self/fd')
    require(len(entries) <= 4096, 'FD_LIMIT', 'Too many inherited descriptors for the display guard')
    for entry in entries:
        fd = int(entry)
        if fd not in keep:
            try:
                os.close(fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def backlight_guard_worker(channel, parent_fd, io, last):
    """Private child, no public endpoint or command execution. Death wakes only."""
    result = 0
    try:
        close_except({channel.fileno(), parent_fd, *io.fds.values()})
        channel.send(b'R')
        while True:
            ready, _, _ = select.select([channel, parent_fd], [], [])
            if parent_fd in ready:
                break
            data = channel.recv(6)
            if not data:
                break
            require(len(data) == 5 and data[:1] == b'U', 'GUARD_PROTOCOL', 'Invalid private guard message')
            raw = struct.unpack('<I', data[1:])[0]
            require(1 <= raw <= io.maximum, 'GUARD_PROTOCOL', 'Invalid private restore brightness')
            last = raw
            channel.send(data)
    except BaseException:
        result = 112
    try:
        requested, actual = io.read()
        if requested == 0 or actual == 0:
            io.write(last)
            require(io.read() == (last, last), 'BACKLIGHT_RESTORE_FAILED', 'Crash guard could not confirm a visible backlight')
    except BaseException:
        result = 112
    os._exit(result)


class BacklightGuard:
    def __init__(self, io, last):
        self.pid, self.channel, self.process_start = None, None, None
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent_fd = os.pidfd_open(os.getpid(), 0)
        try:
            self.pid = os.fork()
            if self.pid == 0:
                parent.close()
                backlight_guard_worker(child, parent_fd, io, last)
                os._exit(112)
            child.close()
            os.close(parent_fd)
            parent_fd = None
            self.channel = parent
            self.channel.settimeout(IPC_TIMEOUT)
            self.process_start = process_start(self.pid)
            require(self.channel.recv(2) == b'R', 'BACKLIGHT_GUARD_FAILED', 'Backlight crash guard did not become ready')
        except BaseException:
            parent.close()
            child.close()
            if parent_fd is not None:
                os.close(parent_fd)
            self.close()
            raise

    def alive(self):
        if self.pid is None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.pid = None
            return False
        if pid:
            self.pid = None
            return False
        return True

    def update(self, raw):
        require(self.alive(), 'BACKLIGHT_GUARD_FAILED', 'Backlight crash guard is unavailable; blanking is disabled')
        message = b'U' + struct.pack('<I', raw)
        self.channel.send(message)
        require(self.channel.recv(6) == message, 'BACKLIGHT_GUARD_FAILED', 'Backlight restore value was not acknowledged')

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        deadline = time.monotonic() + IPC_TIMEOUT
        while self.pid is not None and time.monotonic() < deadline:
            if not self.alive():
                break
            time.sleep(0.01)
        # A blocked hardware read must not hang shutdown. The private child has
        # no lease/DRM/server FD and exits after its restore attempt completes.


def wait_previous_guard(record):
    pid, start = record.get('guard_pid'), record.get('guard_start')
    if type(pid) is not int or pid <= 1 or not isinstance(start, str):
        return
    try:
        fd = os.pidfd_open(pid, 0)
    except ProcessLookupError:
        return
    try:
        try:
            if process_start(pid) != start:
                return
        except FileNotFoundError:
            return
        require(bool(select.select([fd], [], [], IPC_TIMEOUT)[0]), 'BACKLIGHT_GUARD_BUSY',
                'The previous display crash guard has not finished restoring brightness')
    finally:
        os.close(fd)


class Backlight:
    def __init__(self, io, boot_id, record_path=None, guard_factory=BacklightGuard):
        self.io, self.boot_id = io, boot_id
        self.record_path = BACKLIGHT_RECORD if record_path is None else Path(record_path)
        self.last = percent_raw(SAFE_PERCENT, io.maximum)
        self.guard, self.fault, self.preferred_hz = None, None, None
        try:
            no_symlinks(self.record_path)
            if self.record_path.exists():
                saved = regular_json(self.record_path, 2048)
                require(isinstance(saved, dict), 'BACKLIGHT_STATE_INVALID', 'Backlight RAM state must be an object')
                if saved.get('boot_id') == boot_id:
                    require(saved.get('schema') == BACKLIGHT_SCHEMA and saved.get('device') == io.identity
                            and saved.get('maximum') == io.maximum and type(saved.get('last_nonzero_raw')) is int
                            and 1 <= saved['last_nonzero_raw'] <= io.maximum,
                            'BACKLIGHT_STATE_INVALID', 'Saved backlight state does not match this boot and panel')
                    require(saved.get('preferred_hz') is None or type(saved.get('preferred_hz')) is int
                            and saved['preferred_hz'] in RATES, 'BACKLIGHT_STATE_INVALID', 'Saved refresh preference is invalid')
                    guard_pid, guard_start = saved.get('guard_pid'), saved.get('guard_start')
                    require((guard_pid is None and guard_start is None) or
                            (type(guard_pid) is int and guard_pid > 1 and isinstance(guard_start, str)
                             and re.fullmatch('[0-9]{1,32}', guard_start) is not None),
                            'BACKLIGHT_STATE_INVALID', 'Saved crash guard identity is invalid')
                    wait_previous_guard(saved)
                    self.last = saved['last_nonzero_raw']
                    self.preferred_hz = saved.get('preferred_hz')
            requested, actual = io.read()
            if requested > 0 and actual > 0:
                self.last = actual
            else:
                io.write(self.last)
                require(io.read() == (self.last, self.last), 'BACKLIGHT_RESTORE_FAILED', 'Startup could not restore a visible backlight')
            try:
                self.guard = guard_factory(io, self.last)
            except (OSError, DisplayError) as error:
                self.fault = 'Crash guard unavailable: ' + str(error)
            self.save()
        except BaseException:
            # With an admitted panel, never intentionally leave a prior zero
            # brightness behind when a RAM record or new guard cannot be used.
            try:
                if 0 in io.read():
                    io.write(self.last)
            finally:
                if self.guard is not None:
                    self.guard.close()
            raise

    def save(self):
        no_symlinks(self.record_path)
        if self.record_path.exists():
            regular_json(self.record_path, 2048)
        guard_pid = self.guard.pid if self.guard else None
        value = {'schema': BACKLIGHT_SCHEMA, 'boot_id': self.boot_id, 'device': self.io.identity,
                 'maximum': self.io.maximum, 'last_nonzero_raw': self.last,
                 'preferred_hz': self.preferred_hz,
                 'guard_pid': guard_pid,
                 'guard_start': self.guard.process_start if guard_pid is not None else None}
        temporary = self.record_path.parent / ('.display-backlight-' + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            raw = json.dumps(value, separators=(',', ':')).encode() + b'\n'
            require(os.write(fd, raw) == len(raw), 'STATE_WRITE_FAILED', 'Backlight RAM state write was incomplete')
            os.fsync(fd)
            os.replace(temporary, self.record_path)
        finally:
            os.close(fd)
            if temporary.exists():
                temporary.unlink()

    def guarded(self):
        return self.guard is not None and self.guard.alive()

    def status(self):
        try:
            requested, actual = self.io.read()
            return {'brightness_supported': True, 'blank_supported': self.guarded(),
                    'brightness_percent': raw_percent(actual, self.io.maximum),
                    'brightness_raw': actual, 'brightness_max': self.io.maximum,
                    'brightness_requested_raw': requested, 'brightness_actual_raw': actual,
                    'backlight_readback': 'kernel-cached',
                    'blanked': actual == 0, 'last_nonzero_percent': raw_percent(self.last, self.io.maximum),
                    'backlight_reason': self.fault}
        except (OSError, DisplayError) as error:
            return backlight_unavailable(str(error))

    def tick(self):
        if not self.guarded():
            self.fault = self.fault or 'Crash guard stopped; blanking is disabled until service restart'
            if 0 in self.io.read():
                self.wake()

    def apply(self, raw):
        before = self.io.read()
        if raw == 0:
            require(self.guarded(), 'BACKLIGHT_GUARD_FAILED', 'A live crash guard is required before blanking')
            self.guard.update(self.last)  # Acknowledge restore value before any zero write.
            self.save()
        try:
            if before != (raw, raw):
                self.io.write(raw)
            require(self.io.read() == (raw, raw), 'BACKLIGHT_VERIFY_FAILED', 'Brightness readback differs from the requested value')
            if raw > 0:
                self.last = raw
                if self.guarded():
                    self.guard.update(raw)
                self.save()
        except (OSError, DisplayError) as error:
            restored = False
            try:
                if 0 in self.io.read():
                    self.io.write(self.last)
                restored = all(value > 0 for value in self.io.read())
            except (OSError, DisplayError):
                pass
            raise DisplayError('BACKLIGHT_CHANGE_FAILED', str(error), visible_restore_confirmed=restored,
                               observed=self.status()) from error
        return {**self.status(), 'changed': before != (raw, raw)}

    def brightness(self, percent):
        return self.apply(percent_raw(percent, self.io.maximum))

    def blank(self):
        requested, actual = self.io.read()
        if actual > 0:
            self.last = actual
        return self.apply(0)

    def wake(self):
        requested, actual = self.io.read()
        return self.apply(actual if requested > 0 and actual > 0 else self.last)

    def toggle(self):
        return self.wake() if self.io.read()[1] == 0 else self.blank()

    def close(self):
        try:
            if 0 in self.io.read():
                self.wake()
        finally:
            if self.guard is not None:
                self.guard.close()
            self.io.close()


def discover_backlight(boot_id):
    io = discover_backlight_io()
    try:
        return Backlight(io, boot_id)
    except BaseException:
        io.close()
        raise


def admission():
    require(os.geteuid() == 0, 'ROOT_REQUIRED', 'Display control requires recovery root')
    require(sys.byteorder == 'little' and struct.calcsize('P') == 8
            and array.array('I').itemsize == 4 and array.array('Q').itemsize == 8,
            'UNSUPPORTED_ABI', 'Display ioctl support requires the native 64-bit little-endian ABI')
    compatible = bounded_read('/sys/firmware/devicetree/base/compatible').split(b'\0')
    require(b'samsung,a22x' in compatible and b'mediatek,mt6833' in compatible,
            'WRONG_DEVICE', 'Display control is qualified only for SM-A226B / MT6833')
    require(b'a22x.recovery=1' in bounded_read('/proc/cmdline').split(),
            'NOT_RECOVERY', 'Display control requires the dedicated recovery boot')
    roots = []
    for line in bounded_read('/proc/self/mountinfo', 1024 * 1024).decode().splitlines():
        left, separator, right = line.partition(' - ')
        fields = left.split()
        if separator and len(fields) >= 6 and fields[4] == '/':
            roots.append(right.split()[0])
    require(len(roots) == 1 and roots[0] in ('rootfs', 'ramfs', 'tmpfs'),
            'NOT_RAM_RECOVERY', 'Display service requires the recovery RAM root')
    require(mount_for(RUN)[-1] in ('rootfs', 'ramfs', 'tmpfs'),
            'NOT_RAM_RECOVERY', 'Display state must remain on the recovery RAM filesystem')
    boot_id = bounded_read('/proc/sys/kernel/random/boot_id', 128).decode().strip()
    marker = regular_json(RUN / 'boot.json')
    require(isinstance(marker, dict) and marker.get('schema') == 'a22x.recovery.boot.v1'
            and marker.get('boot_id') == boot_id, 'BOOT_MISMATCH', 'Recovery boot receipt does not match this boot')
    return boot_id


def framebuffer_geometry():
    fd = os.open('/dev/fb0', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(stat.S_ISCHR(os.fstat(fd).st_mode), 'FRAMEBUFFER_UNAVAILABLE', 'fb0 must be a character device')
        value = bytearray(160)
        fcntl.ioctl(fd, 0x4600, value, True)  # FBIOGET_VSCREENINFO
        width, height = struct.unpack_from('<2I', value)
        require((width, height) == (WIDTH, HEIGHT), 'FRAMEBUFFER_GEOMETRY', 'Expected the active 1080×2408 framebuffer')
        return width, height
    finally:
        os.close(fd)


class Mode:
    def __init__(self, raw):
        self.raw = bytes(raw)
        require(len(self.raw) == MODE_BYTES, 'INVALID_MODE', 'Kernel mode has the wrong ABI size')
        self.values = struct.unpack('<I10HIII32s', self.raw)
        a = self.values
        require(a[0] > 0 and 0 < a[1] <= a[2] < a[3] <= a[4]
                and 0 < a[6] <= a[7] < a[8] <= a[9], 'INVALID_MODE', 'Kernel display timings are invalid')
        self.width, self.height = a[1], a[6]
        # This panel uses progressive single-scan modes. Do not silently use the
        # wrong refresh formula for interlaced/double-scanned modes.
        require(not a[12] & ((1 << 4) | (1 << 5)) and a[10] <= 1,
                'UNSUPPORTED_MODE', 'Only progressive single-scan panel modes are supported')
        self.actual_hz = a[0] * 1000.0 / (a[4] * a[9])
        self.hz = next((rate for rate in RATES if abs(self.actual_hz - rate) < 0.5), None)
        self.name = a[14].split(b'\0', 1)[0].decode('ascii', 'replace')
        # name/type/vrefresh are metadata; compare actual timing fields + flags.
        self.timing_key = self.raw[:24] + self.raw[28:32]

    def same_timings(self, other):
        return isinstance(other, Mode) and self.timing_key == other.timing_key


def vector(count, kind='I', maximum=256):
    require(isinstance(count, int) and 0 <= count <= maximum,
            'DRM_COUNT_LIMIT', 'DRM resource count exceeds its admitted bound')
    return array.array(kind, [0]) * max(count, 1)


class DRM:
    """Native UAPI arrays stay alive until each ioctl returns; no ctypes/libdrm."""
    def __init__(self, path):
        self.path = str(path)
        self.fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require(stat.S_ISCHR(os.fstat(self.fd).st_mode), 'DRM_NODE_INVALID', 'DRM card must be a character device')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def ioctl(self, number, value):
        fcntl.ioctl(self.fd, 0xc0006400 | (len(value) << 16) | number, value, True)
        return value

    def driver_name(self):
        first = self.ioctl(0x00, bytearray(64))  # drm_version, 64-bit native ABI
        length = struct.unpack_from('<Q', first, 16)[0]
        require(0 < length <= 128, 'DRM_DRIVER_NAME', 'DRM driver name is unavailable or oversized')
        name = vector(length, 'B', 128)
        second = bytearray(64)
        struct.pack_into('<QQ', second, 16, length, name.buffer_info()[0])
        self.ioctl(0x00, second)
        require(struct.unpack_from('<Q', second, 16)[0] <= length, 'DRM_CHANGED', 'DRM driver name changed during discovery')
        return bytes(name[:length]).decode('ascii', 'replace').rstrip('\0')

    def resources(self):
        for _ in range(3):
            value = self.ioctl(0xa0, bytearray(64))
            counts = struct.unpack_from('<4I', value, 32)
            vectors = [vector(count) for count in counts]
            for offset, values in zip((0, 8, 16, 24), vectors):
                struct.pack_into('<Q', value, offset, values.buffer_info()[0])
            self.ioctl(0xa0, value)
            actual = struct.unpack_from('<4I', value, 32)
            if all(now <= allocated for now, allocated in zip(actual, counts)):
                return dict(zip(('fbs', 'crtcs', 'connectors', 'encoders'),
                                [list(values[:count]) for values, count in zip(vectors, actual)]))
        raise DisplayError('DRM_CHANGED', 'DRM resources changed repeatedly during discovery')

    def connector(self, ident):
        for _ in range(3):
            value = bytearray(80)
            struct.pack_into('<I', value, 48, ident)
            self.ioctl(0xa7, value)
            nm, np, ne = struct.unpack_from('<3I', value, 32)
            require(nm <= 64, 'DRM_COUNT_LIMIT', 'Connector has too many modes')
            modes, props, values, encoders = (vector(nm * MODE_BYTES, 'B', 64 * MODE_BYTES),
                                            vector(np), vector(np, 'Q'), vector(ne))
            for offset, data in ((0, encoders), (8, modes), (16, props), (24, values)):
                struct.pack_into('<Q', value, offset, data.buffer_info()[0])
            self.ioctl(0xa7, value)
            actual = struct.unpack_from('<3I', value, 32)
            if any(now > allocated for now, allocated in zip(actual, (nm, np, ne))):
                continue
            require(struct.unpack_from('<I', value, 48)[0] == ident, 'DRM_CHANGED', 'Connector identity changed')
            return {'id': ident, 'encoder_id': struct.unpack_from('<I', value, 44)[0],
                    'type': struct.unpack_from('<I', value, 52)[0],
                    'connected': struct.unpack_from('<I', value, 60)[0] == 1,
                    'properties': dict(zip(props[:actual[1]], values[:actual[1]])),
                    'modes': [Mode(modes[i * MODE_BYTES:(i + 1) * MODE_BYTES]) for i in range(actual[0])]}
        raise DisplayError('DRM_CHANGED', 'Connector counts changed repeatedly')

    def encoder(self, ident):
        value = bytearray(20)
        struct.pack_into('<I', value, 0, ident)
        self.ioctl(0xa6, value)
        actual, kind, crtc, possible, clones = struct.unpack('<5I', value)
        require(actual == ident, 'DRM_CHANGED', 'Encoder identity changed')
        return {'id': actual, 'crtc_id': crtc}

    def crtc(self, ident):
        value = bytearray(104)
        struct.pack_into('<I', value, 12, ident)
        self.ioctl(0xa1, value)
        require(struct.unpack_from('<I', value, 12)[0] == ident, 'DRM_CHANGED', 'CRTC identity changed')
        return {'id': ident, 'fb_id': struct.unpack_from('<I', value, 16)[0],
                'x': struct.unpack_from('<I', value, 20)[0], 'y': struct.unpack_from('<I', value, 24)[0],
                'mode': Mode(value[36:104]) if struct.unpack_from('<I', value, 32)[0] else None}

    def property_name(self, ident):
        value = bytearray(64)  # drm_mode_get_property with empty value lists
        struct.pack_into('<I', value, 16, ident)
        self.ioctl(0xaa, value)
        return bytes(value[24:56]).split(b'\0')[0].decode('ascii', 'replace')

    def set_connector_property(self, connector_id, ident, wanted):
        value = bytearray(16)
        struct.pack_into('<QII', value, 0, wanted, ident, connector_id)
        try:
            self.ioctl(0xab, value)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EPERM):
                raise
            fcntl.ioctl(self.fd, 0x641e, 0)  # DRM_IOCTL_SET_MASTER, only when needed
            self.ioctl(0xab, value)

    def set_crtc(self, before, connector_id, mode):
        connectors = vector(1)
        connectors[0] = connector_id
        value = bytearray(104)
        struct.pack_into('<Q7I', value, 0, connectors.buffer_info()[0], 1, before['id'], before['fb_id'],
                         before['x'], before['y'], 0, 1)
        value[36:104] = mode.raw
        try:
            self.ioctl(0xa2, value)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EPERM):
                raise
            fcntl.ioctl(self.fd, 0x641e, 0)  # DRM_IOCTL_SET_MASTER, only when needed
            self.ioctl(0xa2, value)


def modes_for(connector, geometry):
    matches = {rate: {} for rate in RATES}
    for mode in connector['modes']:
        if (mode.width, mode.height) == geometry and mode.hz in matches:
            matches[mode.hz][mode.timing_key] = mode
    require(all(len(values) <= 1 for values in matches.values()),
            'AMBIGUOUS_MODES', 'More than one distinct kernel timing matches a refresh rate')
    return {rate: next(iter(values.values())) for rate, values in matches.items() if values}


def state_summary(state):
    mode = state.get('mode')
    return {'crtc_id': state['id'], 'fb_id': state['fb_id'], 'x': state['x'], 'y': state['y'],
            'current_hz': mode.hz if mode else None,
            'actual_hz': round(mode.actual_hz, 4) if mode else None}


DPMS_ON, DPMS_OFF = 0, 3


class Controller:
    def __init__(self, drm, geometry=(WIDTH, HEIGHT)):
        self.drm, self.geometry, self.fault = drm, geometry, None
        require(geometry == (WIDTH, HEIGHT), 'FRAMEBUFFER_GEOMETRY', 'Expected the 1080×2408 framebuffer')
        require(drm.driver_name() == 'mediatek', 'WRONG_DRM_DRIVER', 'The active card must use the MediaTek DRM driver')
        resources = drm.resources()
        candidates = []
        for ident in resources['connectors']:
            connector = drm.connector(ident)
            if connector['connected'] and connector['type'] == 16 and connector['encoder_id']:
                encoder = drm.encoder(connector['encoder_id'])
                if encoder['crtc_id'] in resources['crtcs']:
                    state = drm.crtc(encoder['crtc_id'])
                    mode = state['mode']
                    if state['fb_id'] and mode and (mode.width, mode.height) == geometry:
                        candidates.append((connector, encoder, state))
        require(len(candidates) == 1, 'DSI_PIPELINE_UNAVAILABLE', 'Exactly one connected active 1080×2408 DSI pipeline is required')
        connector, encoder, state = candidates[0]
        self.connector_id, self.encoder_id, self.crtc_id = connector['id'], encoder['id'], state['id']
        self.modes = modes_for(connector, geometry)
        require(set(self.modes) == set(RATES), 'REFRESH_MODES_UNAVAILABLE', 'The kernel must advertise matching 60 and 90 Hz panel modes')
        self.snapshot()

    def snapshot(self):
        connector = self.drm.connector(self.connector_id)
        require(connector['connected'] and connector['type'] == 16 and connector['encoder_id'] == self.encoder_id,
                'DISPLAY_PIPELINE_CHANGED', 'The selected DSI connector or encoder changed')
        require(self.drm.encoder(self.encoder_id)['crtc_id'] == self.crtc_id,
                'DISPLAY_PIPELINE_CHANGED', 'The active DSI CRTC changed')
        state = self.drm.crtc(self.crtc_id)
        require(state['fb_id'] > 0 and state['mode'] is not None
                and (state['mode'].width, state['mode'].height) == self.geometry,
                'DISPLAY_PIPELINE_CHANGED', 'The active framebuffer or mode geometry changed')
        modes = modes_for(connector, self.geometry)
        require(set(modes) == set(RATES), 'REFRESH_MODES_UNAVAILABLE', 'The kernel refresh modes are no longer available')
        require(any(state['mode'].same_timings(mode) for mode in modes.values()),
                'UNRECOGNIZED_CURRENT_MODE', 'The active mode does not match an advertised panel timing')
        self.modes = modes
        return state

    def panel(self, on):
        """Switch the panel and the display pipeline. A dark backlight alone keeps them
        reading the whole framebuffer from memory on every refresh, for nobody."""
        connector = self.drm.connector(self.connector_id)
        for ident in connector.get('properties', ()):
            if self.drm.property_name(ident) == 'DPMS':
                self.drm.set_connector_property(self.connector_id, ident, DPMS_ON if on else DPMS_OFF)
                return True
        return False

    def status(self):
        try:
            state = self.snapshot()
            result = {'supported': self.fault is None, 'available_hz': sorted(self.modes),
                      'current_hz': state['mode'].hz, 'actual_hz': round(state['mode'].actual_hz, 4),
                      'service_running': True, 'reason': self.fault}
        except (DisplayError, OSError) as error:
            result = unavailable(str(error), True)
        return result

    @staticmethod
    def verify(before, after, mode):
        require(all(after[key] == before[key] for key in ('id', 'fb_id', 'x', 'y'))
                and after['mode'] is not None and after['mode'].same_timings(mode),
                'MODESET_VERIFY_FAILED', 'Mode switch did not preserve the framebuffer/position and exact requested timings')

    def set(self, hz):
        require(type(hz) is int and hz in RATES, 'UNSUPPORTED_REFRESH', 'Only 60 and 90 Hz are supported')
        require(self.fault is None, 'DISPLAY_FAULT', 'A prior rollback could not be confirmed; restart the display service')
        before = self.snapshot()
        target = self.modes[hz]
        if before['mode'].same_timings(target):
            return {**self.status(), 'changed': False}
        try:
            self.drm.set_crtc(before, self.connector_id, target)
            after = self.snapshot()
            self.verify(before, after, target)
        except (OSError, DisplayError) as error:
            rollback_error = None
            try:
                self.drm.set_crtc(before, self.connector_id, before['mode'])
                self.verify(before, self.snapshot(), before['mode'])
            except (OSError, DisplayError) as failed:
                rollback_error = str(failed)
                self.fault = 'The mode switch failed and rollback could not be confirmed'
            raise DisplayError('MODESET_ROLLBACK_FAILED' if rollback_error else 'MODESET_FAILED',
                               str(error), rollback_succeeded=rollback_error is None,
                               rollback_error=rollback_error, original=state_summary(before),
                               observed=self.status()) from error
        return {**self.status(), 'changed': True}


def discover():
    geometry = framebuffer_geometry()
    paths = sorted(path for path in Path('/dev/dri').glob('card*') if re.fullmatch(r'card[0-9]+', path.name))
    require(len(paths) <= 16, 'DRM_COUNT_LIMIT', 'Too many DRM cards were discovered')
    reasons = []
    for path in paths:
        drm = None
        try:
            drm = DRM(path)
            return Controller(drm, geometry)
        except (OSError, DisplayError) as error:
            reasons.append(str(error)[:256])
            if drm:
                drm.close()
    raise DisplayError('DISPLAY_UNAVAILABLE', 'No admitted MediaTek DSI pipeline: ' + '; '.join(reasons[:4]))


def unavailable(reason, running=False):
    return {'supported': False, 'available_hz': [], 'current_hz': None, 'actual_hz': None,
            'service_running': running, 'reason': reason}


def error_reply(operation, error, boot_id=None):
    return {'ok': False, 'operation': operation, 'boot_id': boot_id,
            'error': {'code': getattr(error, 'code', 'DISPLAY_IO_ERROR'), 'message': str(error)[:2048],
                      'details': getattr(error, 'details', {})}}


def display_status(controller, reason, backlight, backlight_reason=None):
    return {**(controller.status() if controller else unavailable(reason or 'Display unavailable', True)),
            **(backlight.status() if backlight else backlight_unavailable(backlight_reason or 'Native panel backlight unavailable'))}


def panel_power(controller, backlight, on):
    """Only saves power: the screen still goes dark or comes back through the backlight if this fails."""
    if controller is None:
        return
    before = backlight.io.panel_off
    backlight.io.panel_off = True
    try:
        controller.panel(on)
        backlight.io.panel_off = not on
    except (OSError, DisplayError) as error:
        backlight.io.panel_off = before
        print(f'panel power {on}: {error}', file=sys.stderr, flush=True)


def light(operation, percent, controller, backlight):
    """The panel comes on before the light and goes off after it, so nobody sees it change."""
    dark = backlight.status().get('blanked') is True
    if operation == 'toggle':
        operation = 'wake' if dark else 'blank'
    if dark and (operation == 'wake' or (operation == 'brightness' and percent)):
        panel_power(controller, backlight, True)
    changed = backlight.brightness(percent) if operation == 'brightness' else getattr(backlight, operation)()
    if operation == 'blank' or (operation == 'brightness' and not percent):
        panel_power(controller, backlight, False)
    return changed


def dispatch(request, boot_id, controller, reason=None, backlight=None, backlight_reason=None):
    operation = request.get('operation') if isinstance(request, dict) else None
    try:
        require(isinstance(request, dict) and set(request) <= {'schema', 'boot_id', 'operation', 'hz', 'percent'},
                'INVALID_REQUEST', 'Unexpected display request structure')
        require(request.get('schema') == REQUEST_SCHEMA, 'INVALID_REQUEST', 'Unknown display request schema')
        require(request.get('boot_id') == boot_id, 'BOOT_MISMATCH', 'Display request belongs to a different boot')
        require(operation in ('status', 'set', 'brightness', 'blank', 'wake', 'toggle'),
                'INVALID_REQUEST', 'Unknown display operation')
        require(set(request) == {'schema', 'boot_id', 'operation'} |
                ({'hz'} if operation == 'set' else {'percent'} if operation == 'brightness' else set()),
                'INVALID_REQUEST', 'Display operation has missing or unexpected arguments')
        if operation == 'status':
            value = display_status(controller, reason, backlight, backlight_reason)
        elif operation == 'set':
            require(controller is not None, 'DISPLAY_UNAVAILABLE', reason or 'Display unavailable')
            value = controller.set(request.get('hz'))
            if backlight is not None:
                backlight.preferred_hz = value['current_hz']
                backlight.save()
            value.update(backlight.status() if backlight else backlight_unavailable(backlight_reason))
        else:
            require(backlight is not None, 'BACKLIGHT_UNAVAILABLE', backlight_reason or 'Native panel backlight unavailable')
            changed = light(operation, request.get('percent'), controller, backlight)
            value = {**display_status(controller, reason, backlight, backlight_reason), **changed}
        return {'ok': True, 'operation': operation, 'boot_id': boot_id, **value}
    except (DisplayError, OSError) as error:
        return error_reply(operation, error, boot_id)


def receive_line(connection, maximum):
    value = bytearray()
    while True:
        chunk = connection.recv(min(512, maximum + 1 - len(value)))
        require(bool(chunk), 'TRUNCATED_REQUEST', 'Display IPC ended before its newline')
        value.extend(chunk)
        require(len(value) <= maximum, 'MESSAGE_TOO_LARGE', 'Display IPC message exceeded its size bound')
        if b'\n' in chunk:
            raw, tail = bytes(value).split(b'\n', 1)
            require(not tail, 'INVALID_REQUEST', 'Only one display message is accepted per connection')
            return json.loads(raw, object_pairs_hook=unique_object)


def send_reply(connection, value):
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode() + b'\n'
    require(len(raw) <= MAX_RESPONSE, 'MESSAGE_TOO_LARGE', 'Display reply exceeded its size bound')
    connection.sendall(raw)


def handle_connection(connection, boot_id, controller, reason=None, backlight=None, backlight_reason=None):
    connection.settimeout(IPC_TIMEOUT)
    try:
        pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(uid == 0, 'ROOT_REQUIRED', 'Display IPC accepts root peers only')
        request = receive_line(connection, MAX_REQUEST)
        reply = dispatch(request, boot_id, controller, reason, backlight, backlight_reason)
    except (DisplayError, OSError, ValueError) as error:
        reply = error_reply(None, error, boot_id)
    try:
        send_reply(connection, reply)
    except (OSError, DisplayError, ValueError):
        pass  # A disconnected or hostile client does not stop the persistent owner.


def process_start(pid):
    return bounded_read('/proc/' + str(pid) + '/stat', 65536).decode().rsplit(') ', 1)[1].split()[19]


def write_service_record(boot_id):
    no_symlinks(RECORD)
    if RECORD.exists():
        regular_json(RECORD)
    value = {'schema': SERVICE_SCHEMA, 'boot_id': boot_id, 'pid': os.getpid(),
             'process_start': process_start(os.getpid())}
    temporary = RUN / ('.display-service-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as output:
            output.write(json.dumps(value).encode() + b'\n')
            output.flush()
        os.fsync(fd)
        os.replace(temporary, RECORD)
    finally:
        os.close(fd)
        if temporary.exists():
            temporary.unlink()
    return RECORD.stat()


def safe_unlink(path, owned):
    try:
        named = path.lstat()
        if (named.st_dev, named.st_ino) == (owned.st_dev, owned.st_ino):
            path.unlink()
    except FileNotFoundError:
        pass


def serve(boot_id):
    no_symlinks(RUN)
    RUN.mkdir(mode=0o700, exist_ok=True)
    no_symlinks(LOCK)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    controller = backlight = server = record_owned = socket_owned = None
    old_signals = {}
    try:
        opened, named = os.fstat(fd), LOCK.lstat()
        require(stat.S_ISREG(opened.st_mode) and opened.st_uid == 0 and opened.st_nlink == 1
                and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino),
                'UNSAFE_LOCK', 'Display singleton lock is not a stable root-owned regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DisplayError('SERVICE_ALREADY_RUNNING', 'Another display service owns the singleton lock')
        reason = backlight_reason = None
        try:
            backlight = discover_backlight(boot_id)
        except (OSError, DisplayError, ValueError) as error:
            backlight_reason = str(error)
        try:
            controller = discover()
            if backlight is not None:
                if backlight.preferred_hz is not None:
                    controller.set(backlight.preferred_hz)
                backlight.preferred_hz = controller.status()['current_hz']
                backlight.save()
        except (OSError, DisplayError) as error:
            reason = str(error)
        no_symlinks(SOCKET)
        if SOCKET.exists():
            existing = SOCKET.lstat()
            require(stat.S_ISSOCK(existing.st_mode) and existing.st_uid == 0,
                    'UNSAFE_SOCKET', 'Existing display socket path is not a root-owned socket')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(str(SOCKET))
                except ConnectionRefusedError:
                    safe_unlink(SOCKET, existing)
                else:
                    raise DisplayError('SOCKET_IN_USE', 'Another listener owns the display socket')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o177)
        try:
            server.bind(str(SOCKET))
        finally:
            os.umask(previous_umask)
        os.chmod(SOCKET, 0o600)
        socket_owned = SOCKET.lstat()
        server.listen(4)
        server.settimeout(0.5)
        record_owned = write_service_record(boot_id)
        running = True

        def stop(*_):
            nonlocal running
            running = False
        for number in (signal.SIGTERM, signal.SIGINT):
            old_signals[number] = signal.signal(number, stop)
        print(json.dumps({'ok': True, 'operation': 'serve', 'boot_id': boot_id,
                          **display_status(controller, reason, backlight, backlight_reason)}), flush=True)
        while running:
            if backlight is not None:
                try:
                    backlight.tick()
                except (OSError, DisplayError) as error:
                    backlight.fault = str(error)
            try:
                connection, address = server.accept()
            except socket.timeout:
                continue
            with connection:
                handle_connection(connection, boot_id, controller, reason, backlight, backlight_reason)
        return 0
    finally:
        for number, handler in old_signals.items():
            signal.signal(number, handler)
        if server is not None:
            server.close()
        if socket_owned is not None:
            safe_unlink(SOCKET, socket_owned)
        if record_owned is not None:
            safe_unlink(RECORD, record_owned)
        try:
            if backlight is not None:
                panel_power(controller, backlight, True)
                backlight.close()
        finally:
            if controller is not None:
                controller.drm.close()  # Only service shutdown closes the persistent DRM FD.
            os.close(fd)


def client(operation, hz, boot_id, percent=None):
    no_symlinks(SOCKET)
    named = SOCKET.lstat()
    require(stat.S_ISSOCK(named.st_mode) and named.st_uid == 0 and stat.S_IMODE(named.st_mode) == 0o600,
            'UNSAFE_SOCKET', 'Display socket must be root-owned with mode 0600')
    record = regular_json(RECORD)
    require(isinstance(record, dict) and record.get('schema') == SERVICE_SCHEMA and record.get('boot_id') == boot_id
            and type(record.get('pid')) is int and record['pid'] > 1,
            'SERVICE_STALE', 'Display service receipt does not match this boot')
    require(record.get('process_start') == process_start(record['pid']),
            'SERVICE_STALE', 'Display service PID no longer identifies its recorded process')
    request = {'schema': REQUEST_SCHEMA, 'boot_id': boot_id, 'operation': operation}
    if operation == 'set':
        request['hz'] = hz
    elif operation == 'brightness':
        request['percent'] = percent
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(IPC_TIMEOUT)
        connection.connect(str(SOCKET))
        pid, uid, gid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(uid == 0 and pid == record['pid'], 'SERVICE_STALE', 'Display socket peer differs from its boot/PID receipt')
        connection.sendall(json.dumps(request, separators=(',', ':')).encode() + b'\n')
        connection.shutdown(socket.SHUT_WR)
        reply = receive_line(connection, MAX_RESPONSE)
    require(isinstance(reply, dict) and reply.get('boot_id') == boot_id and reply.get('operation') == operation
            and type(reply.get('ok')) is bool, 'INVALID_RESPONSE', 'Display service returned an invalid response')
    return reply


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DisplayError('INVALID_ARGUMENT', message)


def main(argv=None):
    operation = 'unknown'
    try:
        parser = Parser(description=__doc__)
        sub = parser.add_subparsers(dest='operation', required=True, parser_class=Parser)
        sub.add_parser('status')
        change = sub.add_parser('set')
        change.add_argument('--hz', required=True, type=int, choices=RATES)
        brightness = sub.add_parser('brightness')
        brightness.add_argument('--percent', required=True, type=int)
        for name in ('blank', 'wake', 'toggle'):
            sub.add_parser(name)
        sub.add_parser('serve')
        args = parser.parse_args(argv)
        operation = args.operation
        boot_id = admission()
        if operation == 'serve':
            return serve(boot_id)
        if operation == 'brightness':
            percent_raw(args.percent, 100)  # Refuse invalid input before even connecting.
        result = client(operation, getattr(args, 'hz', None), boot_id, getattr(args, 'percent', None))
    except (DisplayError, OSError, ValueError) as error:
        result = ({'ok': True, 'operation': 'status', **unavailable(str(error)), **backlight_unavailable(str(error))}
                  if operation == 'status' else error_reply(operation, error))
    print(json.dumps(result, separators=(',', ':'), ensure_ascii=True, allow_nan=False), flush=True)
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
