#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Recovery preferences and the optional password.

They are kept in the misc partition, twice, with a generation and a SHA-256 (misc_store.py; since
2026-11-14): no filesystem, so no journal, mount or older copy on another volume can show an
older state. A copy in RAM avoids reading misc for every question, and holds a save misc cannot
take at this moment (exit 3). Releases up to 1.0.1 kept them in a file on cache, or on userdata
when cache had no filesystem this recovery could write: while misc holds no record yet, that file
is read as they read it (read_volumes) and copied to misc, which decides from then on; the file and
its marks are then removed (_retire_volumes), so they never come back when misc holds no record.

Exit 0 means yes or done, 1 means no, 2 an error, 3 kept in RAM until reboot,
4 damaged settings. The password also guards SSH, UART and adb, see access_gate.py.

Settings that cannot be read, or are damaged, never mean "no password" (2026-11-12, audit): the
recovery and the gate stay locked, and nothing is saved over them. Damaged ones can be erased only
together with userdata (reset-damaged), as Android's reset does: whoever holds the phone does not
get past the password and keep the data it guarded. Both copies in misc damaged are damaged
settings; one damaged copy leaves the other, which holds the same. Erasing misc itself (in
Download Mode) gives the defaults: whoever can do that can flash another recovery too, which no
password here covers.
"""
import argparse
import contextlib
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import filesystem as fs
import misc_store
from operation_lock import lease

RAM_COPY = Path(os.environ.get('A22X_SETTINGS_RAM', '/run/a22x-recovery/settings.json'))
RELATIVE = Path('a22x-recovery/settings.json')
THEMES = Path('a22x-recovery/themes')
RAM_THEMES = Path(os.environ.get('A22X_THEMES_RAM', '/run/a22x-recovery/themes'))
VOLUMES = ('cache', 'userdata')
ROUNDS = 120000
# Wi-Fi joins at the start with the lock held; settings that cannot be read lock the recovery now.
LOCK_WAIT = 20.0
# timezone: a tz database zone (Europe/Berlin) for the recovery clock with summer time; empty keeps
# the fixed utc_offset (2026-11-09).
DEFAULTS = {'language': 'en', 'theme': 'graphite', 'terminal': 'auto', 'storage': 'sd', 'vibration': 2, 'timeout': 2, 'utc_offset': 0,
            'refresh': 0, 'brightness': 0, 'animation': 'zoom', 'lock': 1, 'access': 1, 'navigation': 0, 'timezone': '',
            'torch': 0,  # the flashlight's level; 0: not chosen yet
            'android_log': ''}  # the id of the failed start of Android the owner has read about (android_log.py)
TEXT_KEYS = ('language', 'theme', 'terminal', 'storage', 'animation', 'timezone', 'android_log')
ANIMATIONS = ('zoom', 'blur', 'slide', 'fade', 'dissolve', 'off')
HEX = re.compile(r'[0-9a-f]+')


# What a look at a volume may fail with: a mount command that does not come back is a
# subprocess.TimeoutExpired, no OSError (2026-11-14 review: it left with a traceback and exit 1,
# which says "wrong password").
FAILS = (OSError, ValueError, fs.FilesystemError, subprocess.SubprocessError)


class Unreadable(OSError):
    """Settings that are there but cannot be read now: nothing is known, so nothing opens."""


class Damaged(ValueError):
    """Settings that are there but cannot be read as settings; LABEL names the volume; MARKS the
    volumes that carry PASSWORD_MARK, which their reset clears too."""
    def __init__(self, message, label='', marks=()):
        super().__init__(message)
        self.label, self.marks = label, tuple(marks)


def checked(record, label=''):
    """RECORD as settings, or Damaged. A password needs its whole hash and its whole salt."""
    if not isinstance(record, dict):
        raise Damaged('The settings are not a record', label)
    digest, salt = record.get('hash'), record.get('salt')
    if digest is not None or salt is not None:
        if not (isinstance(digest, str) and len(digest) == 64 and HEX.fullmatch(digest)
                and isinstance(salt, str) and len(salt) == 32 and HEX.fullmatch(salt)):
            raise Damaged('The password in the settings is damaged', label)
    return record


def valid(key, value):
    if key == 'language':
        return re.fullmatch(r'[a-z]{2}', value) is not None
    if key == 'theme':
        return re.fullmatch(r'[a-z0-9-]{3,23}', value) is not None
    if key == 'terminal':
        return re.fullmatch(r'[a-z0-9-]{3,23}', value) is not None
    if key == 'storage':
        return value in ('sd', 'usb')
    if key == 'animation':
        return value in ANIMATIONS
    if key == 'android_log':
        return re.fullmatch(r'(?:[0-9a-f]{16})?', value) is not None
    if key == 'timezone':
        return value == '' or (len(value) <= 64 and re.fullmatch(r'[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2}', value) is not None)
    if not re.fullmatch(r'-?[0-9]{1,4}', value):
        return False
    number = int(value)
    return {'vibration': 0 <= number <= 3, 'timeout': 0 <= number <= 5,
            'utc_offset': -720 <= number <= 840 and number % 15 == 0,
            'refresh': number in (0, 60, 90), 'brightness': 0 <= number <= 100,
            'lock': 0 <= number <= 2, 'access': number in (0, 1), 'navigation': 0 <= number <= 2,
            'torch': 0 <= number <= 255}.get(key, False)


@contextlib.contextmanager
def volume_root(context, label, mode):
    volume = context.volume(label)
    if volume['mounted']:
        if mode == 'rw' and volume['read_only'] and volume.get('write_supported', True) and volume.get('mount_owner') == 'filesystem':
            # 2026-11-12 review: the list of saved BOOTs leaves cache mounted read-only; a change of
            # the settings then went to userdata, and the older copy on cache won at the next start.
            # The recovery's own read-only mount is opened for writing here and left read-only again.
            context.mount(label, 'rw', True, volume['volume_token'])
            try:
                yield Path(context.volume(label)['mountpoint'])
            finally:
                os.sync()
                with contextlib.suppress(*FAILS):
                    context.mount(label, 'ro', True, context.volume(label)['volume_token'])
            return
        if mode == 'rw' and (volume['read_only'] or not volume['write_supported']):
            raise OSError('Volume is read-only')
        try:
            yield Path(volume['mountpoint'])
        finally:
            if mode == 'rw':
                # An fsync reaches only the journal, and a read-only look does not replay it (noload,
                # norecovery): a reset seconds after a password was set showed the state before it
                # (2026-11-14 review). sync() writes it in place before the save is said done.
                os.sync()
        return
    context.mount(label, mode, mode == 'rw', volume['volume_token'])
    try:
        yield Path(context.volume(label)['mountpoint'])
    finally:
        os.sync()
        context.unmount(label)


def copy_themes(root):
    """Themes wait in RAM, so choosing one needs no mount. A theme that cannot be copied (EIO, a full
    /run) is left out: it says nothing about the settings, and read_disk must not take it for
    settings that cannot be read (2026-11-14 review: the phone stayed locked at every start). Read
    as the settings are, through a link to their folder too; only regular files are copied."""
    folder = root / THEMES
    try:
        if not folder.is_dir():
            return
        RAM_THEMES.mkdir(parents=True, exist_ok=True)
        themes = (*folder.glob('*.theme'), *folder.glob('*.hidden'))
    except OSError:
        return
    for theme in themes:
        with contextlib.suppress(OSError):
            if stat.S_ISREG(os.lstat(theme).st_mode):
                shutil.copyfile(theme, RAM_THEMES / theme.name)


def present(path):
    """Whether PATH is a regular file (following links): False when nothing is there and for anything
    that is no regular file, a loop of links too; a failure to look (EIO, EACCES) is raised
    (2026-11-14 reviews: Path.is_file() answers False to every error). For files this recovery ships
    or keeps in RAM; the settings are looked at by holds()."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        if error.errno == errno.ELOOP:
            return False
        raise


# What one settings file may hold at most: they are a few hundred bytes. More is no settings (a link
# to /proc/kcore would have filled memory: 2026-11-14 review).
MOST = 1 << 20


def _folder(root, relative):
    """The folder of RELATIVE under ROOT, looked at one part at a time: ('ok', path) when each part is
    a folder on the volume itself, a link to such a folder too (as older releases left them:
    2026-11-14 review); ('missing', None) when one is not there; ('bad', path) when one is something
    else: a file, a dangling link, a loop, or a link out of the volume (2026-11-14 reviews: a link to
    anywhere was followed). A failure to look is raised."""
    device = os.stat(root).st_dev
    path = Path(root)
    for part in Path(relative).parts[:-1]:
        path = path / part
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            try:
                os.lstat(path)
            except FileNotFoundError:
                return 'missing', None
            return 'bad', path
        except OSError as error:
            if error.errno == errno.ELOOP:
                return 'bad', path
            raise
        if not stat.S_ISDIR(st.st_mode) or st.st_dev != device:
            return 'bad', path
    return 'ok', path


def holds(root, relative):
    """Whether the settings (or the saved Wi-Fi list) are at ROOT/RELATIVE: a regular file itself,
    never a link (2026-11-14 review: a link to any file made it a holder, and its damage asked for
    userdata though no password was set), in folders _folder admits. Anything else: False, and
    PASSWORD_MARK decides what that means. A failure to look is raised."""
    state, folder = _folder(root, relative)
    if state != 'ok':
        return False
    try:
        return stat.S_ISREG(os.lstat(folder / Path(relative).name).st_mode)
    except FileNotFoundError:
        return False


class NotRegular(OSError):
    """Where a file should be there is something else."""


def read_bytes(root, relative):
    """The bytes of the regular file at ROOT/RELATIVE (MOST at most, and one byte more when it holds
    more), opened as filesystem.open_regular opens: its type proven on the descriptor before a byte
    is read, never through a link, never a socket or a device (2026-11-14 review); None when holds()
    finds nothing; NotRegular when something else is there; OSError when it cannot be read now, a
    file that holds() still sees but cannot be opened too. One reader for the settings and the saved
    Wi-Fi list."""
    state, folder = _folder(root, relative)
    if state == 'missing':
        return None
    if state == 'bad':
        raise NotRegular(errno.ENOTDIR, 'A folder of the path is no folder of this volume')
    path = folder / Path(relative).name
    try:
        fd = fs.open_regular(path)
    except (FileNotFoundError, NotADirectoryError):
        if holds(root, relative):
            raise OSError(errno.EIO, 'The file is there and could not be opened')
        return None
    except fs.FilesystemError as error:
        if error.code == 'REGULAR_FILE_REQUIRED':
            raise NotRegular(errno.EINVAL, 'No regular file') from error
        raise OSError(errno.EIO, str(error)) from error
    with os.fdopen(fd, 'rb') as stream:
        return stream.read(MOST + 1)


def write_bytes(root, relative, data):
    """DATA as ROOT/RELATIVE, whole or not at all: a unique temporary name in the folder (made when
    missing), fsync of the file and of the folder; through a link to a folder of the volume, as
    _folder admits, so what such a folder holds stays with it (2026-11-14 review: the link was
    removed, and the Wi-Fi list and themes behind it with it). NotRegular when the folder is no folder
    of the volume."""
    state, folder = _folder(root, relative)
    if state == 'bad':
        raise NotRegular(errno.ENOTDIR, 'A folder of the path is no folder of this volume')
    if state == 'missing':
        (Path(root) / Path(relative).parent).mkdir(mode=0o700, parents=True, exist_ok=True)
        _sync_folder(root)
        state, folder = _folder(root, relative)
        if state != 'ok':
            raise NotRegular(errno.ENOTDIR, 'The folder of the path could not be made')
    name = Path(relative).name
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        temporary = '.%s.%s' % (name, os.urandom(8).hex())
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=directory)
            raise
        os.fsync(directory)
    finally:
        os.close(directory)


def clear_file(root, relative):
    """Remove ROOT/RELATIVE itself when it is no regular file (a folder, a link, a FIFO), never one of
    its folders: NotRegular when one of them is no folder of the volume (2026-11-14 review: the Wi-Fi
    list removed the folder that also holds the settings, their password with them)."""
    state, folder = _folder(root, relative)
    if state == 'missing':
        return
    if state == 'bad':
        raise NotRegular(errno.ENOTDIR, 'A folder of the path is no folder of this volume')
    target = folder / Path(relative).name
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(target)
    elif not stat.S_ISREG(st.st_mode):
        os.unlink(target)


def clear_entry(root, relative):
    """clear_file, after removing a part of the path that is no folder of the volume (a file, a
    dangling link, a link out of the volume: never what a link points to): for the settings only, on
    the volume that keeps them (their write, their reset); never for the Wi-Fi list."""
    state, bad = _folder(root, relative)
    if state == 'bad':
        os.unlink(bad)
        return
    clear_file(root, relative)


# The mark that a password is set, at a volume's root (filesystem.PASSWORD_MARK, kept by every wipe
# of cache's contents). "No password" needs proof (2026-11-14 reviews: every way of misreading "not
# there" opened the phone): settings read without one while no other volume looked at carries the
# mark, or no settings anywhere and no mark anywhere. See verdict() and settled().
PASSWORD_MARK = Path(fs.PASSWORD_MARK)


def marked(root):
    """Whether ROOT carries PASSWORD_MARK, whatever it is; a failure to look is raised, never "no mark"
    (2026-11-14 review: os.path.lexists said no mark on EIO)."""
    try:
        os.lstat(Path(root) / PASSWORD_MARK)
        return True
    except FileNotFoundError:
        return False


def set_mark(root):
    """PASSWORD_MARK at ROOT, a regular empty file: whatever else stands there goes first (a FIFO
    would have hung the open under the lease: 2026-11-14 review)."""
    clear_file(root, PASSWORD_MARK)
    try:
        fd = os.open(Path(root) / PASSWORD_MARK, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_folder(root)


def drop_mark(root):
    """PASSWORD_MARK gone from ROOT, whatever it is; a failure to remove it is raised, never taken for
    done (2026-11-14 review: os.path.lexists said gone on EIO)."""
    clear_file(root, PASSWORD_MARK)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(Path(root) / PASSWORD_MARK)
    _sync_folder(root)


def _sync_folder(root):
    folder = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def read_regular(root, relative, label):
    """The text of the settings file at ROOT/RELATIVE on LABEL: None when no regular file is there
    (see holds); Damaged when it is no text, or more than settings ever are; Unreadable when it
    cannot be read now."""
    try:
        data = read_bytes(root, relative)
    except NotRegular:
        return None
    except OSError as error:
        raise Unreadable('The settings on %s cannot be read now' % label) from error
    if data is None:
        return None
    if len(data) > MOST:
        raise Damaged('The settings on %s are damaged' % label, label)
    try:
        return data.decode()
    except UnicodeDecodeError as error:
        raise Damaged('The settings on %s are damaged' % label, label) from error


# A filesystem this recovery never writes: its read_only_reason_code (filesystem.py) is a property of
# the filesystem, not of the moment (Android's encryption, erofs, a verified Android filesystem). Any
# other reason a volume cannot be written now passes (2026-11-14 review: write_supported is also
# False while another owner has it mounted, or a guard was lost).
NEVER_WRITABLE = frozenset({'FSCRYPT_KEYS_UNAVAILABLE', 'READ_ONLY_FILESYSTEM', 'VERIFIED_ANDROID_FILESYSTEM_RW_NOT_QUALIFIED'})
# A partition that holds no filesystem this recovery can look into (Android's encrypted userdata, a
# cache never formatted, a driver this kernel lacks): nothing can be on it, as on a volume erased.
NO_FILESYSTEM = frozenset({'ENCRYPTED_OR_UNRECOGNIZED_USERDATA', 'ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM',
                           'FILESYSTEM_DRIVER_UNAVAILABLE'})


class NoSettingsVolume(OSError):
    """No volume is there that could hold the settings."""


def _volume(context, label):
    """The volume LABEL as filesystem.py lists it, or None when its partition holds no filesystem
    this recovery can look into. Cache and userdata are partitions of this phone's GPT: they never
    leave it. So any other answer (the list failing while a card or a stick comes or goes, the
    partition missing from it after a sysfs error, block holders, a reason not known here) is
    Unreadable, never "not there" (2026-11-14 review: each was read as a volume erased, which opened
    the phone, or asked for userdata). One classifier for every read and write (2026-11-14 review:
    three copies)."""
    try:
        volume = context.volume(label)
    except FAILS as error:
        # The list reads sysfs and /dev, and fails with a bare OSError when a card or a stick goes
        # while it is read (2026-11-14 review: that passed every "except Unreadable" and locked a
        # phone with no password).
        raise Unreadable('The settings volume %s cannot be found now' % label) from error
    if volume.get('mountable') or volume.get('mounted'):
        return volume
    if volume.get('reason_code') in NO_FILESYSTEM:
        return None
    raise Unreadable('The settings volume %s cannot be used now' % label)


def _its_root(root, volume):
    """ROOT, when it is the root of VOLUME's own filesystem; Unreadable when the mountpoint shows
    something else (2026-11-14 review: a volume mounted elsewhere, twice or by a bind of a folder,
    left an empty mountpoint, read as no settings and no mark)."""
    device = volume.get('device')
    if device:
        major, minor = (int(part) for part in str(device).split(':'))
        st = os.stat(root)
        if (os.major(st.st_dev), os.minor(st.st_dev)) != (major, minor):
            raise Unreadable('The mountpoint shows another filesystem than %s' % volume.get('id'))
    return root


def verdict(looks):
    """Where the settings are, by what the volumes showed: LOOKS in the order of VOLUMES, each
    {'label', 'there', 'known', 'holds', 'marked'} ('there': a filesystem to look into; 'known': it
    could be looked at). Returns (holder or None, the labels looked at that carry PASSWORD_MARK).
    A volume without a filesystem is passed over: nothing is on it, as on one erased (only Download
    Mode, or a format, brings that about: see the module); a volume that could not be looked at
    before the holder is Unreadable; with no holder, a mark anywhere is Damaged (2026-11-14 reviews).
    That holds after an empty cache too, though a recovery that never saved settings stays locked
    while its userdata cannot be looked at: releases 0.6.180 to 0.6.206 saved to userdata whenever
    cache could not be opened for writing at that moment, so a password may be there beside a
    cache that is whole, writable and empty (2026-11-14 review: passing such a userdata over read
    that password as none, and the next save made it so)."""
    holder = None
    for look in looks:
        if not look['there']:
            continue
        if not look['known']:
            raise Unreadable('The settings volume %s cannot be read now' % look['label'])
        if look['holds']:
            holder = look['label']
            break
    marks = [look['label'] for look in looks if look['there'] and look['known'] and look['marked']]
    if holder is None and marks:
        raise Damaged('The settings are missing, and %s says a password was set' % marks[0], marks[0], marks)
    return holder, marks


def settled(holder, record, marks):
    """RECORD as read from HOLDER, when it may stand: a password always; no password only when no
    other volume looked at carries PASSWORD_MARK (an older copy otherwise: Damaged, 2026-11-14
    review). A volume after the holder that could not be looked at is no reason to lock a phone with
    no password (2026-11-14 review: a userdata that would not mount locked it for good): a mark
    could be there only if the settings had been kept there while cache had no filesystem, and cache
    holds settings again only when restored from an older image (see the module)."""
    if record.get('hash'):
        return record
    others = [label for label in marks if label != holder]
    if others:
        raise Damaged('The settings on %s hold no password, and %s says one was set' % (holder, others[0]),
                      others[0], marks)
    return record


def _read_look(context, label, opened):
    """What LABEL shows for reading: {'label', 'there', 'known', 'holds', 'marked', 'write', 'root'},
    its root left open in OPENED."""
    look = {'label': label, 'there': True, 'known': False, 'holds': False, 'marked': False,
            'write': 'later', 'root': None}
    try:
        volume = _volume(context, label)
    except Unreadable:
        return look
    if volume is None:
        look['there'] = False
        return look
    if volume.get('read_only_reason_code') in NEVER_WRITABLE:
        look['write'] = 'never'
    try:
        root = _its_root(opened.enter_context(volume_root(context, label, 'ro')), volume)
        look.update(root=root, holds=holds(root, RELATIVE), marked=marked(root), known=True)
    except FAILS:
        pass
    return look


def read_volumes(details=None):
    """The settings as releases up to 1.0.1 kept them, on cache or userdata: read while misc holds no
    record yet (read_disk), never written since but by tests that build its states (write_volumes).
    The settings, by verdict() and settled(). Volumes are looked at in order, each once, until one
    holds the settings or cannot be looked at; when the holder's settings hold no password, the rest
    are looked at too, for a mark (2026-11-14 reviews: a mark after the holder was never seen; then
    every volume was mounted at every read). Empty only when no volume holds settings and none carries
    the mark. DETAILS, a dict, gets {'holder', 'marked'} (2026-11-14 review: a global).

    A volume that cannot be found or read is not an empty one: taking it for one
    would show the phone unlocked and let the next save replace the password. Nor is a
    file that is there but damaged (2026-11-12, audit): before, the next volume was
    asked, and userdata without settings unlocked the phone.
    """
    context = fs.DeviceContext()
    with lease(context.lock_path, context.mode):
        opened = contextlib.ExitStack()
        try:
            looks = []
            for label in VOLUMES:
                look = _read_look(context, label, opened)
                looks.append(look)
                if look['there'] and (not look['known'] or look['holds']):
                    break
            # No filesystem on either partition (Android's encrypted userdata beside a cache never
            # formatted): nothing can be kept, so nothing is: the defaults, never a recovery locked
            # for good. A partition that is not found at all is Unreadable in _volume (2026-11-07:
            # the eMMC came up as mmcblk1 and the defaults were shown).
            holder, marks = verdict(looks)
            roots = {look['label']: look['root'] for look in looks}
            # The themes of the volume that keeps the settings, or would keep them: written there and
            # nowhere else, so one left elsewhere never comes back (2026-11-14 reviews).
            source = holder or next((look['label'] for look in looks if look['known'] and look['write'] != 'never'), None)
            if source is not None:
                copy_themes(roots[source])
            if holder is None:
                if details is not None:
                    details.update(holder=None, marked=False)
                return {}
            # 2026-11-12 review: a file that cannot be read now (EIO, a busy card) is not a
            # damaged one: it is no reason to offer erasing userdata, and still opens nothing.
            try:
                text = read_regular(roots[holder], RELATIVE, holder)
                if text is None:
                    raise Unreadable('The settings on %s changed while they were read' % holder)
                record = checked(json.loads(text), holder)
            except Damaged as error:
                raise Damaged(str(error), holder, marks) from error  # its reset clears every mark (2026-11-14 review)
            except ValueError as error:
                raise Damaged('The settings on %s are damaged' % holder, holder, marks) from error
            if not record.get('hash'):
                for label in VOLUMES[len(looks):]:
                    look = _read_look(context, label, opened)
                    looks.append(look)
                    if look['there'] and look['known'] and look['marked']:
                        marks.append(label)
            record = settled(holder, record, marks)
            if details is not None:
                details.update(holder=holder, marked=holder in marks)
            return record
        except (Damaged, Unreadable, NoSettingsVolume):
            raise
        except FAILS as error:
            raise Unreadable('The settings volume cannot be read now') from error
        finally:
            _close(opened)


def choose(states, mode):
    """The volume the settings are written to ('rw') or a file beside them read from ('ro'), from
    STATES: in the order of VOLUMES, (label, there, holds, write) for each volume looked at, where
    HOLDS is True, False or None (the volume could not be looked at) and WRITE is 'now', 'later'
    (not writable at this moment, for whatever reason) or 'never' (NEVER_WRITABLE). The holder;
    else the first volume there that can ever take them, the first there when none can and only
    reading; a volume that could not be looked at before that is Unreadable. Settings are written
    only there, and only when it can be written now: a volume that can take them only later is no
    reason to put them elsewhere (Unreadable: the caller keeps them in RAM and tries again)."""
    target = fallback = None
    for label, there, found, write in states:
        if not there:
            continue
        if found is None:
            raise Unreadable('The settings volume %s cannot be read now' % label)
        if found:
            target = (label, write)
            break
        if target is None and write != 'never':
            target = (label, write)
        fallback = fallback or (label, write)
    if target is None and mode == 'ro':
        target = fallback
    if target is None:
        raise NoSettingsVolume('No volume is there for the settings')
    label, write = target
    if mode != 'ro' and write != 'now':
        raise Unreadable('The settings volume %s cannot be written now' % label)
    return label


def _close(opened):
    """Close a volume this call no longer needs: a failure to unmount it is no failure of the work."""
    with contextlib.suppress(*FAILS):
        opened.close()


def _look(context, label, mode, relative, opened):
    """(there, holds, write, root, marked) of LABEL, for writing: ROOT is open in MODE in OPENED when
    it could be opened so: only a volume that may ever be written is tried for writing, and any
    failure to open it so is 'later'; whether it holds the file, and carries PASSWORD_MARK, is then
    looked at read-only. A volume that cannot be looked at gives holds None."""
    try:
        volume = _volume(context, label)
    except Unreadable:
        return True, None, None, None, False
    if volume is None:
        return False, False, None, None, False
    write = 'never' if volume.get('read_only_reason_code') in NEVER_WRITABLE else 'later'
    if mode == 'rw' and write == 'later':
        try:
            root = opened.enter_context(volume_root(context, label, 'rw'))
        except FAILS:
            _close(opened)
        else:
            try:
                root = _its_root(root, volume)
                return True, holds(root, relative), 'now', root, marked(root)
            except OSError:
                return True, None, 'now', None, False
    try:
        root = _its_root(opened.enter_context(volume_root(context, label, 'ro')), volume)
        found, mark = holds(root, relative), marked(root)
    except FAILS:
        _close(opened)
        return True, None, write, None, False
    if mode != 'ro':
        _close(opened)
        root = None
    return True, found, write, root, mark


@contextlib.contextmanager
def settings_root(context, mode, relative=RELATIVE, seen=None):
    """(label, root) of the volume choose() gives, open in MODE; RELATIVE: the file whose holder
    decides (the settings). Volumes are looked at in order until one holds the file or cannot be
    looked at, each opened once; every other is closed before the caller works in the chosen one,
    and all of them whatever fails. SEEN, a dict, gets {label: carries PASSWORD_MARK} of each volume
    looked at, seen while it was open. When the caller's work is done, a failure to close a volume
    afterwards does not undo it (2026-11-14 review). Called under the operation lock."""
    everything = contextlib.ExitStack()
    try:
        looked = []
        for label in VOLUMES:
            opened = contextlib.ExitStack()
            everything.callback(opened.close)
            there, found, write, root, mark = _look(context, label, mode, relative, opened)
            looked.append((label, there, found, write, root, opened))
            if seen is not None and there and found is not None:
                seen[label] = mark
            if found is not False:  # the holder, or one that cannot be looked at: choose stops there
                break
        label = choose([entry_[:4] for entry_ in looked], mode)
        root = next(entry_[4] for entry_ in looked if entry_[0] == label)
        for entry_ in looked:
            if entry_[0] != label:
                _close(entry_[5])
        if root is None:  # never by choose's contract; said, not asserted (python -O drops asserts)
            raise Unreadable('The settings volume %s was not opened' % label)
    except BaseException:
        _close(everything)
        raise
    try:
        yield label, root
    except BaseException:
        # The caller's own error stands: a volume that fails to close never replaces it (2026-11-14
        # review: Damaged settings became Unreadable, and their reset was never offered).
        _close(everything)
        raise
    _close(everything)


def _drop_other_marks(context, keep, seen):
    """PASSWORD_MARK gone from every volume with a filesystem but KEEP: SEEN says what settings_root
    saw while each was open; one it did not look at (after the holder) is looked at read-only now. A
    volume with a mark is opened for writing, and one that cannot be written raises: nothing is said
    cleared while a mark it saw may stay. One that cannot be looked at now is passed over: a mark
    could be there only if the settings had been kept there while cache had no filesystem, and they
    are read from there again in that case (see settled)."""
    for label in VOLUMES:
        if label == keep:
            continue
        found = seen.get(label)
        if found is None:
            try:
                volume = _volume(context, label)
                if volume is None:
                    continue
                with volume_root(context, label, 'ro') as root:
                    found = marked(_its_root(root, volume))
            except FAILS:
                continue
        if found:
            volume = _volume(context, label)
            with volume_root(context, label, 'rw') as root:
                drop_mark(_its_root(root, volume))


def write_volumes(record):
    """RECORD on the volume settings_root chooses, in the order that keeps both promises whatever
    stops it (2026-11-14 reviews):
      with a password: the settings, then PASSWORD_MARK beside them; a stop between leaves the
      password on disk, never a mark beside no settings (which would ask for userdata to go);
      without one: first the mark of every other volume, then the settings, then this volume's
      mark; a stop leaves the older password in force, or settings without one beside their own
      mark only, which read as no password.
    A save is done only when the mark agrees with the settings: a password without its mark would read
    as none once the settings were lost, and a mark left beside settings without one would ask for
    userdata then (both found by tests/test_settings_properties.py). So a mark that cannot be set or
    dropped is a save not done (exit 3), which the interface tries again; the settings already on disk
    stay meanwhile, and read as they are (2026-11-14 review: "kept in RAM" meant "not yet whole")."""
    context = fs.DeviceContext()
    with lease(context.lock_path, context.mode):
        try:
            seen = {}
            with settings_root(context, 'rw', seen=seen) as (label, root):
                if not record.get('hash'):
                    _drop_other_marks(context, label, seen)
                # The volume chosen holds no regular settings file: whatever else stands in their
                # way (a file or a link out of the volume for their folder, a folder for the file)
                # goes, or they could never be saved there (2026-11-14 review).
                clear_entry(root, RELATIVE)
                write_bytes(root, RELATIVE, (json.dumps(record, ensure_ascii=True, sort_keys=True) + '\n').encode())
                if record.get('hash'):
                    set_mark(root)
                else:
                    drop_mark(root)
            return True
        except FAILS:
            return False


def _encode(record):
    return (json.dumps(record, ensure_ascii=True, sort_keys=True) + '\n').encode()


def _copy_themes(context):
    """The themes to RAM from the volume themes.py keeps them on (settings_root's choice); best
    effort: a volume that cannot be looked at now leaves the bundled themes only."""
    with contextlib.suppress(*FAILS):
        with settings_root(context, 'ro') as (_, root):
            copy_themes(root)


# In the record in misc: 'gone' once the older store was emptied on every volume (_retire_volumes).
OLDER_STORE = 'older_store'
WIFI_RELATIVE = Path('a22x-recovery/wifi.json')  # wifi.SAVED_RELATIVE: the list of releases up to 1.0.1


def _older_store_on(root):
    """Whether ROOT still holds anything of the older store: its settings, its mark, its Wi-Fi list."""
    return (holds(root, RELATIVE) or marked(root) or os.path.lexists(root / RELATIVE)
            or os.path.lexists(root / WIFI_RELATIVE))


def _wifi_in_misc():
    try:
        return misc_store.read('wifi') is not None
    except (misc_store.Damaged, *FAILS):
        return False


def _retire_volumes(context):
    """The older store emptied once its record is in misc: the settings file and PASSWORD_MARK gone
    from every volume, so neither can come back when misc reads as holding none (2026-11-14 review:
    after misc was erased, or its damaged copies reset, the older file brought back a password
    cleared since, or the one the reset was for). Only once misc holds the Wi-Fi list too: wifi.py
    finds the older list beside the older settings file (2026-11-14 review: removed first, the list
    was never copied). Each volume on its own, best effort. True when every volume was looked at and
    holds none of it now: the record then says so (OLDER_STORE), and no later start looks again;
    until then each start does (2026-11-14 review: only the volume of the themes was looked at, and
    a mark left on another came back as damage)."""
    if not _wifi_in_misc():
        return False
    done = True
    for label in VOLUMES:
        try:
            volume = _volume(context, label)
            if volume is None:
                # No filesystem seen now is no proof that none of the older store is there: it may
                # come back (2026-11-14 review: the flag was set, and the volume never looked at again).
                done = False
                continue
            with volume_root(context, label, 'ro') as root:
                root = _its_root(root, volume)
                if not _older_store_on(root):
                    continue
            with volume_root(context, label, 'rw') as root:
                root = _its_root(root, volume)
                # The mark first: a stop between leaves settings that read as they are, never a mark
                # without them, which reads as damage (2026-11-14 review).
                drop_mark(root)
                clear_entry(root, RELATIVE)
                (root / RELATIVE).unlink(missing_ok=True)
                # The older Wi-Fi list too, now that misc holds its own: networks forgotten since
                # never come back from it.
                clear_file(root, WIFI_RELATIVE)
                (root / WIFI_RELATIVE).unlink(missing_ok=True)
        except FAILS:
            done = False
    return done


def read_disk(details=None):
    """The settings: the record in misc (misc_store); while misc holds none, what read_volumes reads
    from a release that kept them on cache or userdata, copied to misc then, which decides from
    then on (when misc cannot take it now, it is read so again at the next start). Unreadable when
    misc cannot be read now; Damaged when both copies there are damaged. DETAILS: see read_volumes."""
    context = fs.DeviceContext()
    with lease(context.lock_path, context.mode):
        try:
            data, twice, readable = misc_store.state('settings')
            if data is not None and not twice and readable:
                # A write stopped between the two copies: the second is written again before
                # anything relies on two (2026-11-14 review: the older store was retired beside one
                # copy, whose damage then read as no record).
                with contextlib.suppress(*FAILS):
                    misc_store.write('settings', data)
                    twice = True
        except misc_store.Damaged as error:
            raise Damaged('The settings in misc are damaged', 'misc') from error
        except FAILS as error:
            raise Unreadable('The settings in misc cannot be read now') from error
        if data is not None:
            try:
                record = checked(json.loads(data.decode()), 'misc')
            except ValueError as error:
                if isinstance(error, Damaged):
                    raise
                raise Damaged('The settings in misc are damaged', 'misc') from error
            _copy_themes(context)
            if twice:
                record = _retired(context, record)
            return record
        record = read_volumes(details)
        with contextlib.suppress(*FAILS):
            misc_store.write('settings', _encode(record))
            record = _retired(context, record)
        return record


def _retired(context, record):
    """RECORD, marked OLDER_STORE 'gone' once _retire_volumes has emptied the older store everywhere
    (written to misc then; best effort: else the next start tries again)."""
    if record.get(OLDER_STORE) == 'gone' or not _retire_volumes(context):
        return record
    record = {**record, OLDER_STORE: 'gone'}
    with contextlib.suppress(*FAILS):
        misc_store.write('settings', _encode(record))
    return record


def write_disk(record):
    """RECORD in misc: the older copy replaced, so the newest stays whole until the new one is
    (misc_store.write). Never over damaged settings. False when misc cannot take it now (exit 3:
    kept in RAM, tried again)."""
    context = fs.DeviceContext()
    with lease(context.lock_path, context.mode):
        try:
            misc_store.read('settings')
            misc_store.write('settings', _encode(record))
            return True
        except misc_store.Foreign:
            # Another program's bytes in the area, which is not this recovery's: never taken; the
            # settings stay in the older store, read from there too (2026-11-14 review: no save
            # survived a restart).
            return write_volumes(record)
        except (misc_store.Damaged, *FAILS):
            return False


def remember(record):
    RAM_COPY.parent.mkdir(parents=True, exist_ok=True)
    temporary = RAM_COPY.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, json.dumps(record).encode())
    finally:
        os.close(fd)
    temporary.replace(RAM_COPY)


def load():
    """An unread disk is an error. Taking it for an empty one would lose the password on the next save.
    A damaged copy in RAM is read again from the disk."""
    # Not there is the only reason to read the disk; a copy that cannot be read now is an error,
    # never replaced by the older one on disk (2026-11-14 review: is_file() said no copy on any error).
    try:
        return checked(json.loads(RAM_COPY.read_text()))
    except (FileNotFoundError, ValueError):
        pass
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            record = read_disk()
            break
        except BlockingIOError:
            if time.monotonic() > deadline:
                raise
            time.sleep(.1)
    remember(record)
    return record


def store(record):
    remember(record)
    try:
        return 0 if write_disk(record) else 3
    except FAILS:
        return 3


def reset_damaged():
    """Erase damaged settings together with userdata (both copies in misc, or the file of an older
    release with its marks). Exit 1 when the settings are not damaged:
    settings that cannot be read now are no proof. Every volume besides userdata that must change
    (the one that holds them, each that carries PASSWORD_MARK) is opened for writing first, so one
    that cannot be written stops the reset before anything is erased (2026-11-14 review: a mark that
    could not be removed afterwards asked for the reset again). Userdata is erased before the
    settings, so a stop halfway leaves the data gone, never the password."""
    try:
        read_disk()
        return 1
    except Damaged as damaged:
        label, marks = damaged.label, damaged.marks
    except OSError:
        return 1
    context = fs.DeviceContext()
    with lease(context.lock_path, context.mode):
        with contextlib.ExitStack() as stack:
            names = {label, *marks} - {'userdata', 'misc'}
            others = {name: stack.enter_context(volume_root(context, name, 'rw'))
                      for name in sorted(names)}
            volume = context.volume('userdata')
            if volume['mounted']:
                context.unmount('userdata', volume['volume_token'])
                volume = context.volume('userdata')
            context.format('userdata', 'ext4' if volume.get('fs_type') == 'ext4' else 'f2fs', True, volume['volume_token'])
            for root in others.values():
                # Whatever stands there goes: a folder, a link, a file where a folder should be
                # (2026-11-14 review: unlink failed on those after userdata was erased, every time).
                clear_entry(root, RELATIVE)
                (root / RELATIVE).unlink(missing_ok=True)
                drop_mark(root)
            if label == 'misc':
                # An empty record, not none: the older store is never read again (it would bring
                # back the password the reset was for), so no volume but userdata is needed for the
                # reset (2026-11-14 reviews); later starts empty it (_retired).
                misc_store.write('settings', _encode({}))
    RAM_COPY.unlink(missing_ok=True)
    os.sync()
    return 0


def derive(secret, salt):
    return hashlib.pbkdf2_hmac('sha256', secret.encode(), bytes.fromhex(salt), ROUNDS).hex()


def matches(record, path):
    """Whether this file holds the current password; with no password anything does."""
    if not record.get('hash'):
        return True
    if path is None:
        return False
    return hmac.compare_digest(derive(path.read_text().rstrip('\n'), record['salt']), record['hash'])


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    change = commands.add_parser('set')
    for key in DEFAULTS:
        change.add_argument('--' + key.replace('_', '-'))
    commands.add_parser('verify').add_argument('--secret-file', type=Path, required=True)
    password = commands.add_parser('set-password')
    password.add_argument('--secret-file', type=Path, required=True)
    password.add_argument('--old-secret-file', type=Path)
    commands.add_parser('clear-password').add_argument('--secret-file', type=Path)
    commands.add_parser('reset-damaged')
    args = parser.parse_args()
    if args.command == 'reset-damaged':
        return reset_damaged()
    record = load()
    if args.command == 'status':
        print(json.dumps({**DEFAULTS, **{k: v for k, v in record.items() if k in DEFAULTS}, 'password': bool(record.get('hash')), 'password_digits': bool(record.get('digits'))}))
        return 0
    if args.command == 'set':
        for key in DEFAULTS:
            value = getattr(args, key)
            if value is not None:
                if not valid(key, value):
                    return 2
                record[key] = value if key in TEXT_KEYS else int(value)
        return store(record)
    if args.command == 'clear-password':
        if not matches(record, args.secret_file):
            return 1
        record.pop('hash', None)
        record.pop('salt', None)
        record.pop('digits', None)
        return store(record)
    secret = args.secret_file.read_text().rstrip('\n')
    if args.command == 'set-password':
        if not secret:
            return 2
        if not matches(record, args.old_secret_file):
            return 1
        salt = os.urandom(16).hex()
        record.update(salt=salt, hash=derive(secret, salt), digits=secret.isascii() and secret.isdigit())
        return store(record)
    return 0 if matches(record, args.secret_file) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Damaged as error:
        print(error, file=sys.stderr)
        sys.exit(4)
    except FAILS as error:
        print(error, file=sys.stderr)
        sys.exit(2)
