#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""The data of a Linux system, without the system (2026-11-13).

The owner's scenario: keep what is one's own (home folders, accounts, settings, the list of
packages), erase the phone, install another distribution or take another ARM device, and put it all
back. This is not a system installed beside another with a shared /home (Systems does that): the
archive lies on a card or a stick and belongs to no system.

    A22xDataBackups/linux-data-ID-DATE.tar.zst
        manifest.json  restore.sh  accounts.tsv  packages.txt  aliases.tsv  source-family
        home/...  root-home/...  settings/etc/...  settings/var/lib/...
    and after zstd's stream the manifest again in a skippable frame (partition_archive's footer).

Each part is chosen by itself, for the backup and again for the restore. The restore is done by
restore.sh from the archive, run inside the installed system (chroot); the same script restores the
archive on any other Linux, without this recovery: sudo sh restore.sh ARCHIVE.

Nothing tied to this phone's hardware is taken: no kernel modules, no firmware, no fstab.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time

import archive_crypt as ac
import file_archive as fa
import filesystem as fs
import partition_archive as pa
from filesystem import fail

SCHEMA = 'a22x.linux-data.v1'
STORE = 'A22xDataBackups'
PARTS = ('home', 'accounts', 'settings', 'packages')
STAGING = 'var/tmp/a22x-restore'
SCRIPT = Path(__file__).with_name('linux_data_restore.sh')
SETTINGS = ('etc/hostname', 'etc/locale.conf', 'etc/default/locale', 'etc/vconsole.conf', 'etc/timezone', 'etc/localtime',
            'etc/NetworkManager/system-connections', 'var/lib/iwd', 'var/lib/bluetooth')
SSH_KEYS = 'etc/ssh'
# arch|debian|fedora: the same program under the names the families give it.
ALIASES = """openssh|openssh-server|openssh-server
networkmanager|network-manager|NetworkManager
firefox|firefox-esr|firefox
python|python3|python3
python-pip|python3-pip|python3-pip
base-devel|build-essential|-
vim|vim|vim-enhanced
bluez-utils|bluez|bluez
alsa-utils|alsa-utils|alsa-utils
noto-fonts|fonts-noto|google-noto-sans-fonts
ttf-dejavu|fonts-dejavu|dejavu-sans-fonts
man-db|man-db|man-db
iw|iw|iw
wpa_supplicant|wpasupplicant|wpa_supplicant
gnome-console|gnome-console|gnome-console
qt5-wayland|qtwayland5|qt5-qtwayland
qt6-wayland|qt6-wayland|qt6-qtwayland
xdg-user-dirs|xdg-user-dirs|xdg-user-dirs
pipewire-pulse|pipewire-pulse|pipewire-pulseaudio
imagemagick|imagemagick|ImageMagick
android-tools|adb|android-tools
noto-fonts-emoji|fonts-noto-color-emoji|google-noto-color-emoji-fonts
mesa|libgl1-mesa-dri|mesa-dri-drivers
vulkan-panfrost|mesa-vulkan-drivers|mesa-vulkan-drivers
which|debianutils|which
p7zip|p7zip-full|p7zip
"""


def require(condition, code, message, **details):
    if not condition:
        fail(code, message, **details)


def os_release(root_fd):
    for path in ('etc/os-release', 'usr/lib/os-release'):
        try:
            fd = open_inside(root_fd, path)
        except OSError:
            continue
        with os.fdopen(fd, 'r', errors='replace') as f:
            found = {}
            for line in f.read(1 << 16).splitlines():
                key, eq, value = line.partition('=')
                if eq:
                    found[key.strip()] = value.strip().strip('"\'')
            return found
    return None


def family_of(release):
    words = (release.get('ID', '') + ' ' + release.get('ID_LIKE', '')).lower().split()
    for family, signs in (('arch', ('arch', 'archarm', 'manjaro')), ('debian', ('debian', 'ubuntu')),
                          ('fedora', ('fedora', 'rhel', 'centos')), ('suse', ('suse', 'opensuse')),
                          ('alpine', ('alpine', 'postmarketos'))):
        if any(w in signs or w.startswith(s) for w in words for s in signs):
            return family
    return 'unknown'


def open_dir(root_fd, relative):
    """RELATIVE below ROOT_FD as a directory, no link followed on the way."""
    fd = os.dup(root_fd)
    try:
        for part in fs.path_parts(relative):
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_inside(root_fd, relative):
    folder, _, name = relative.rpartition('/')
    parent = open_dir(root_fd, folder)
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    finally:
        os.close(parent)


def read_text(root_fd, relative, limit=64 << 20):
    try:
        fd = open_inside(root_fd, relative)
    except OSError:
        return None
    with os.fdopen(fd, 'rb') as f:
        return f.read(limit).decode('utf-8', 'replace')


def accounts_of(root_fd):
    """The people of the system: UID 1000 and up, with password hash, admin right and other groups."""
    passwd = read_text(root_fd, 'etc/passwd')
    require(passwd is not None, 'NO_LINUX', 'There is no Linux system on userdata')
    hashes = {}
    for line in (read_text(root_fd, 'etc/shadow') or '').splitlines():
        parts = line.split(':')
        if len(parts) > 1:
            hashes[parts[0]] = parts[1]
    groups = {}
    for line in (read_text(root_fd, 'etc/group') or '').splitlines():
        parts = line.split(':')
        if len(parts) > 3:
            for member in parts[3].split(','):
                if member:
                    groups.setdefault(member, []).append(parts[0])
    found = []
    for line in passwd.splitlines():
        parts = line.split(':')
        if len(parts) == 7 and parts[2].isdecimal() and 1000 <= int(parts[2]) < 60000 and '|' not in line:
            mine = groups.get(parts[0], [])
            found.append({'name': parts[0], 'uid': int(parts[2]), 'gid': int(parts[3]), 'home': parts[5], 'shell': parts[6],
                          'admin': any(g in ('wheel', 'sudo') for g in mine), 'hash': hashes.get(parts[0], '!'),
                          'groups': [g for g in mine if g not in ('wheel', 'sudo')]})
    return found


def accounts_table(accounts):
    return ''.join('|'.join((a['name'], str(a['uid']), str(a['gid']), a['home'], a['shell'], '1' if a['admin'] else '0',
                             a['hash'] or '-', ','.join(a['groups']) or '-')) + '\n' for a in accounts).encode()


HARDWARE = re.compile(r'^(linux|linux-.*|.*-keyring|base|base-files|grub.*|mkinitcpio.*|dracut.*|systemd|systemd-.*|uboot-.*|zram-generator|dhcpcd|netctl)$')


def packages_of(root_fd, family):
    found = packages_read(root_fd, family)
    # The kernel, firmware, boot and base of this system belong to it, not to its owner; so does the
    # way it brings its network up.
    return None if found is None else [name for name in found if not HARDWARE.match(name)]


def packages_read(root_fd, family):
    """The packages the owner asked for, by name, as the package manager's own records say; None when
    this recovery cannot read them."""
    if family == 'arch':
        try:
            local = open_dir(root_fd, 'var/lib/pacman/local')
        except OSError:
            return None
        names = []
        try:
            for entry in sorted(os.listdir(local)):
                try:
                    fd = os.open(entry + '/desc', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=local)
                except OSError:
                    continue
                with os.fdopen(fd, 'r', errors='replace') as f:
                    lines = f.read(1 << 20).split('\n')
                field = {lines[i]: lines[i + 1] for i in range(len(lines) - 1) if lines[i].startswith('%')}
                if field.get('%NAME%') and field.get('%REASON%', '0') == '0':
                    names.append(field['%NAME%'])
        finally:
            os.close(local)
        return names
    if family == 'debian':
        status = read_text(root_fd, 'var/lib/dpkg/status')
        if status is None:
            return None
        auto = set()
        for block in (read_text(root_fd, 'var/lib/apt/extended_states') or '').split('\n\n'):
            if 'auto-installed: 1' in block.lower():
                for line in block.splitlines():
                    if line.lower().startswith('package: '):
                        auto.add(line[9:].strip())
        names = []
        for block in status.split('\n\n'):
            field = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
            if (field.get('Status') == 'install ok installed' and field.get('Package') not in auto
                    and field.get('Priority') not in ('required', 'important', 'standard')):
                names.append(field['Package'])
        return sorted(set(names))
    if family == 'alpine':
        # apk's own list of what was asked for: /etc/apk/world, a name per line, perhaps with a version.
        world = read_text(root_fd, 'etc/apk/world')
        if world is None:
            return None
        return sorted({re.split(r'[<>=~@]', line.strip(), maxsplit=1)[0] for line in world.splitlines() if line.strip() and not line.startswith('#')})
    if family == 'fedora':
        # dnf5 keeps why each package is there: packages.toml, reason "User" for what was asked for.
        # (dnf4's history is an SQLite file, which this recovery cannot read.)
        text = read_text(root_fd, 'usr/lib/sysimage/libdnf5/packages.toml')
        if text is None:
            return None
        try:
            import tomllib
            table = tomllib.loads(text).get('packages', {})
        except (ImportError, ValueError):
            return None
        names = set()

        def take(name, value):
            if isinstance(value, dict) and str(value.get('reason', '')).lower() == 'user':
                names.add(name)
        for key, value in table.items() if isinstance(table, dict) else ():
            if isinstance(value, dict) and 'reason' in value:
                # "name.arch" as one key
                take(key.rsplit('.', 1)[0] if '.' in key else key, value)
            elif isinstance(value, dict):
                # name.arch written without quotes: a table inside a table
                for inner in value.values():
                    take(key, inner)
        return sorted(names)
    return None


def chroot_lines(root, command, timeout=3600):
    """COMMAND inside the system at ROOT, as its first-start setup is run; (status, lines)."""
    import distro
    lines = []
    status = distro.chroot_script(Path(root), lambda line: lines.append(line.rstrip('\n')), timeout=timeout, command=command)
    return status, lines


@contextlib.contextmanager
def linux_root(context, write=False):
    """userdata with a Linux system at its top: (pin, the root's path). Mounted when it is not, for
    reading unless WRITE, and left as it was found."""
    volume = context.volume('userdata')
    require(volume.get('fs_type') == 'ext4' and not volume.get('fscrypt_feature') and volume.get('mountable'), 'NO_LINUX',
            'There is no Linux system on userdata')
    owned = not volume['mounted']
    reopened = False
    if owned:
        context.mount('userdata', 'rw' if write else 'ro', True, volume['volume_token'])
    elif write and volume['read_only']:
        require(volume.get('mount_owner') == 'filesystem', 'VOLUME_READ_ONLY', 'userdata is in use read-only')
        context.mount('userdata', 'rw', True, volume['volume_token'])
        reopened = True
    try:
        with fs.PinnedVolume(context, 'userdata', writing=write) as pin:
            release = os_release(pin.fd)
            require(release is not None and read_text(pin.fd, 'etc/passwd') is not None, 'NO_LINUX',
                    'There is no Linux system on userdata')
            yield pin, os.readlink('/proc/self/fd/%d' % pin.fd), release
    finally:
        with contextlib.suppress(fs.FilesystemError, OSError):
            now = context.volume('userdata')
            if owned and now['mounted']:
                context.unmount('userdata', now['volume_token'])
            elif reopened:
                context.mount('userdata', 'ro', True, now['volume_token'])


def collect(pin, parts, accounts):
    """(entries for file_archive.write_archive, fds to close, bytes of each part). The folders it
    opened are closed again when it fails (2026-11-13 review)."""
    entries = []
    try:
        return collect_into(pin, parts, accounts, entries)
    except BaseException:
        for e in entries:
            if e[1] is None:
                with contextlib.suppress(OSError):
                    os.close(e[0])
        raise


def collect_into(pin, parts, accounts, entries):
    sizes = {}
    device = pin.stat.st_dev
    # What can be fetched again is left out: every account's cache, passed over while walking
    # (2026-11-14 review: a big ~/.cache made a home too large to copy before it was left out).
    caches = tuple(a['home'].strip('/') + '/.cache/' for a in accounts) + ('root-home/.cache/',)

    # One limit for the whole archive, files and folders together, checked once more at the end as
    # inspect adds it up: what one archive holds in memory stays bounded (2026-11-14 review of a
    # limit for each part).
    def add(parent_fd, name, arcname):
        before = len(entries)
        try:
            fa.walk(parent_fd, name, device, arcname, entries, skip=caches)
        except FileNotFoundError:
            return 0
        return sum(e[2].st_size for e in entries[before:] if e[1] is not None and stat.S_ISREG(e[2].st_mode))

    if 'home' in parts:
        sizes['home'] = add(pin.fd, 'home', 'home') + add(pin.fd, 'root', 'root-home')
    if 'settings' in parts:
        total = 0
        for path in SETTINGS:
            folder, _, name = path.rpartition('/')
            try:
                parent = open_dir(pin.fd, folder)
            except OSError:
                continue
            entries.append((parent, None, None, None))
            total += add(parent, name, 'settings/' + path)
        with contextlib.suppress(OSError):
            ssh = open_dir(pin.fd, SSH_KEYS)
            entries.append((ssh, None, None, None))
            for name in sorted(os.listdir(ssh)):
                if name.startswith('ssh_host_') and 'key' in name:
                    total += add(ssh, name, 'settings/' + SSH_KEYS + '/' + name)
        sizes['settings'] = total
    require(len(entries) <= fa.MAX_ENTRIES, 'SELECTION_TOO_LARGE', 'Too many files are selected at once')
    files = [e for e in entries if e[1] is not None]
    return files, [e[0] for e in entries if e[1] is None], sizes


def externals(context, mount=False, opened=None):
    """SD cards and sticks; with MOUNT, one that is not mounted is mounted read-only to be looked at,
    and its id put in OPENED, so that it is let go again (2026-11-13, on the phone: a card left mounted
    here kept the backup store from taking it)."""
    found = []
    for volume in context.volumes():
        if not volume['id'].startswith(('sdcard', 'usb')) or not volume.get('mountable'):
            continue
        if mount and not volume['mounted']:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.mount(volume['id'], 'ro', False, volume['volume_token'])
                volume = context.volume(volume['id'])
                if opened is not None:
                    opened.append(volume['id'])
        if volume['mounted']:
            found.append(volume)
    return found


def archives(context, mount=False):
    opened = []
    try:
        return archives_on(context, externals(context, mount, opened))
    finally:
        for ident in opened:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.unmount(ident, context.volume(ident)['volume_token'])


def archives_on(context, volumes):
    found = []
    for volume in volumes:
        try:
            with fs.PinnedVolume(context, volume['id']) as pin, pin.directory(STORE) as folder:
                for name in sorted(os.listdir(folder), reverse=True):
                    if not name.endswith(pa.SUFFIXES) or name.startswith('.'):
                        continue
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
                    locked = None
                    try:
                        size = os.fstat(fd).st_size
                        try:
                            raw = pa.footer(fd)
                        except pa.ArchiveError as error:
                            raw, locked = None, error.code
                    finally:
                        os.close(fd)
                    if locked in ('PASSWORD_REQUIRED', 'WRONG_PASSWORD') and name.startswith('linux-data-'):
                        # Protected with a password: listed closed, by what its name says.
                        words = name.split('-')
                        stamp = re.search(r'-(\d{8})-(\d{6})\.', name)
                        made = int(time.mktime(time.strptime(stamp.group(1) + stamp.group(2), '%Y%m%d%H%M%S'))) if stamp else 0
                        found.append({'volume': volume['id'], 'volume_token': volume['volume_token'],
                                      'label': volume.get('label') or volume['id'], 'path': '/' + STORE + '/' + name, 'name': name,
                                      'bytes': size, 'created': made, 'os': {'name': words[2] if len(words) > 2 else ''}, 'parts': [],
                                      'accounts': [], 'packages': 0, 'machine': '', 'encrypted': True, 'locked': True,
                                      'wrong_password': locked == 'WRONG_PASSWORD'})
                        continue
                    try:
                        about = json.loads(raw.decode('utf-8')) if raw else None
                    except ValueError:
                        about = None
                    if isinstance(about, dict) and about.get('schema') == SCHEMA:
                        found.append({'volume': volume['id'], 'volume_token': volume['volume_token'],
                                      'label': volume.get('label') or volume['id'], 'path': '/' + STORE + '/' + name, 'name': name,
                                      'bytes': size, 'created': about.get('created', 0), 'os': about.get('os', {}),
                                      'parts': [p for p in PARTS if p in about.get('parts', [])],
                                      'accounts': about.get('accounts', [])[:8], 'packages': about.get('packages', 0),
                                      'machine': about.get('machine', ''), 'encrypted': name.endswith(ac.SUFFIX), 'locked': False})
        except (FileNotFoundError, fs.FilesystemError, OSError):
            continue
    return found[:16]


def inspect(context):
    result = {'linux': False, 'reason': '', 'archives': [], 'destinations': []}
    # A card that is only inserted is looked at for archives and for room alike, then let go
    # (2026-11-13 review: it showed its archives but was missing as a place to save to).
    opened = []
    try:
        volumes = externals(context, True, opened)
        result['archives'] = archives_on(context, volumes)
        for volume in volumes:
            if volume.get('write_supported'):
                with contextlib.suppress(OSError):
                    room = os.statvfs(volume['mountpoint'])
                    result['destinations'].append({'volume': volume['id'], 'label': volume.get('label') or volume['id'],
                                                   'free_bytes': room.f_bavail * room.f_frsize})
    finally:
        for ident in opened:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.unmount(ident, context.volume(ident)['volume_token'])
    try:
        with linux_root(context) as (pin, _, release):
            accounts = accounts_of(pin.fd)
            family = family_of(release)
            names = packages_of(pin.fd, family)
            # 2026-11-14 review: a home with more entries than one archive takes made the whole page
            # say there was no Linux system. The system is shown; its sizes are unknown and REASON
            # says why the homes cannot be copied in one archive.
            # Each part counted on its own (2026-11-14 review: a home too large to count made the
            # settings unknown too, though a backup of them alone works), with what it adds to an
            # archive: the backup holds the parts chosen in one archive under one limit, and the
            # interface offers no choice whose parts together pass it (2026-11-14 reviews: a limit
            # for each part; then a home hidden that fits alone).
            sizes, entries = {}, {}
            for part in ('home', 'settings'):
                try:
                    files, folders, counted = collect(pin, (part,), accounts)
                    for fd in folders:
                        os.close(fd)
                    sizes[part], entries[part] = counted[part], len(files) + len(folders)
                except fs.FilesystemError as error:
                    sizes[part] = -1
                    result['reason'] = result.get('reason') or error.code
            result.update(entries=entries, max_entries=fa.MAX_ENTRIES)
            result.update(linux=True, os={'id': release.get('ID', ''), 'name': release.get('PRETTY_NAME') or release.get('NAME', ''),
                                          'family': family},
                          accounts=[a['name'] for a in accounts],
                          parts={'home': sizes.get('home', 0), 'settings': sizes.get('settings', 0), 'accounts': len(accounts),
                                 'packages': len(names) if names is not None else -1})
    except fs.FilesystemError as error:
        result['reason'] = error.code
    return result


def writable(context, ident, token):
    volume = context.volume(ident, token)
    require(volume.get('write_supported'), 'VOLUME_READ_ONLY', 'This volume cannot be written')
    if not volume['mounted'] or volume['read_only']:
        require(not volume['mounted'] or volume.get('mount_owner') == 'filesystem', 'VOLUME_READ_ONLY', 'This volume is in use read-only')
        context.mount(ident, 'rw', True, volume['volume_token'])
    return context.volume(ident)


def backup(context, parts, dest_volume, dest_token, confirmed, progress):
    context.confirm(confirmed)
    parts = [p for p in PARTS if p in parts]
    require(parts, 'NOTHING_SELECTED', 'Nothing is selected')
    target_volume = writable(context, dest_volume, dest_token)
    with linux_root(context) as (pin, _, release):
        accounts = accounts_of(pin.fd)
        family = family_of(release)
        files, opened, sizes = collect(pin, parts, accounts)
        try:
            extra, names = [], None
            if 'packages' in parts:
                names = packages_of(pin.fd, family)
                require(names is not None, 'PACKAGES_UNREADABLE',
                        'The package list of this system cannot be read here. Leave Packages out')
                extra.append(('packages.txt', ('\n'.join(names) + '\n').encode(), 0o644))
                extra.append(('aliases.tsv', ALIASES.encode(), 0o644))
            if 'accounts' in parts or 'home' in parts:
                # With the homes alone the table still says whose they were; without hashes then.
                table = accounts if 'accounts' in parts else [dict(a, hash='-') for a in accounts]
                extra.append(('accounts.tsv', accounts_table(table), 0o600))
            extra.append(('source-family', (family + '\n').encode(), 0o644))
            extra.append(('restore.sh', SCRIPT.read_bytes(), 0o755))
            total = sum(e[2].st_size for e in files if stat.S_ISREG(e[2].st_mode))
            created = int(time.time())
            manifest = json.dumps({
                'schema': SCHEMA, 'created': created, 'machine': os.uname().machine, 'parts': parts,
                'os': {'id': release.get('ID', ''), 'name': release.get('PRETTY_NAME') or release.get('NAME', ''), 'family': family},
                'accounts': [a['name'] for a in accounts] if ('accounts' in parts or 'home' in parts) else [],
                'packages': len(names) if names is not None else 0, 'bytes': total, 'entries': len(files),
                'sizes': sizes}, ensure_ascii=False, separators=(',', ':')).encode()
            extra.insert(0, ('manifest.json', manifest, 0o644))
            stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(created))
            name = 'linux-data-%s-%s' % (''.join(c for c in release.get('ID', 'linux') if c.isalnum() or c in '-_')[:24] or 'linux', stamp) + pa.new_suffix()
            with fs.PinnedVolume(context, dest_volume, target_volume['volume_token'], writing=True) as target:
                room = os.fstatvfs(target.fd)
                # zstd rarely makes a home larger; an archive that outgrows the room fails and is removed.
                require(room.f_bavail * room.f_frsize > min(total, total // 3 + (64 << 20)), 'INSUFFICIENT_SPACE',
                        'There is not enough space for the archive', required_bytes=total, available_bytes=room.f_bavail * room.f_frsize)
                with contextlib.suppress(FileExistsError):
                    os.mkdir(STORE, 0o700, dir_fd=target.fd)
                with target.directory(STORE) as folder:
                    part = '.part-' + name
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(part, dir_fd=folder)
                    out = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
                    crypt, left_out = contextlib.ExitStack(), []
                    try:
                        dest = crypt.enter_context(ac.sink(out, pa.PASSWORD if pa.ENCRYPT else None))
                        fa.write_archive(dest, files, total, lambda done, all_, label: progress('Packing', done, all_, label), extra,
                                         left_out=left_out)
                        fa.footer(dest, manifest)
                        crypt.close()
                        os.fsync(out)
                        size = os.fstat(out).st_size
                    except BaseException as error:
                        late = fa.close_sink(crypt, error)
                        os.close(out)
                        with contextlib.suppress(OSError):
                            os.unlink(part, dir_fd=folder)
                        fa.raise_cause(late, error)
                        raise
                    os.close(out)
                    os.rename(part, name, src_dir_fd=folder, dst_dir_fd=folder)
                    os.fsync(folder)
        finally:
            for fd in opened:
                with contextlib.suppress(OSError):
                    os.close(fd)
    return {'archive': {'volume': dest_volume, 'path': '/' + STORE + '/' + name, 'bytes': size}, 'parts': parts, 'bytes': total,
            'entries': len(files), 'encrypted': name.endswith(ac.SUFFIX), 'accounts': len(accounts) if 'accounts' in parts else 0,
            'packages': len(names) if names is not None else 0, 'skipped': len(left_out)}


UPGRADE_FAILED = 'Packages were not installed: the system could not be upgraded first (pacman -Syu). Upgrade it, then restore Packages alone.'


def root_link(member):
    """A hard link inside root-home points into /root, where that folder is unpacked."""
    if member.islnk() and (member.linkname == 'root-home' or member.linkname.startswith('root-home/')):
        return 'root' + member.linkname[9:]
    return member.linkname


def unpack_into(archive_fd, root, parts, progress):
    """Home folders straight to their place in ROOT, the rest into the staging folder there. When it
    fails, the staging folder goes at once: it holds password hashes and keys (2026-11-13 review)."""
    staging = Path(root) / STAGING
    try:
        return unpack_staged(archive_fd, root, parts, progress)
    except BaseException:
        if not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def unpack_staged(archive_fd, root, parts, progress):
    staging = Path(root) / STAGING
    if staging.is_symlink() or staging.exists():
        require(not staging.is_symlink(), 'ARCHIVE_UNSAFE', 'The staging folder of the system is a link')
        shutil.rmtree(staging)
    os.makedirs(staging, 0o700)
    total = os.fstat(archive_fd).st_size
    os.lseek(archive_fd, 0, os.SEEK_SET)
    crypt = contextlib.ExitStack()
    plain = crypt.enter_context(ac.source(archive_fd, pa.PASSWORD))
    proc = subprocess.Popen([pa.zstd_path(), '-q', '-d', '-c'], stdin=plain, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    finished, count = False, 0
    # Each where it belongs and nowhere else (2026-11-13 review: 'home/../etc/x' or a hard link to a
    # file of the system would have reached outside /home). The three places are fixed before the
    # first entry (2026-11-14 review: made for every entry, a link 'home' -> '.' moved them).
    homes, root_home, staged = (fa.safe_filter(Path(root) / 'home'), fa.safe_filter(Path(root) / 'root'),
                                fa.safe_filter(staging))
    try:
        with tarfile.open(fileobj=proc.stdout, mode='r|', bufsize=fa.CHUNK) as tar:
            for member in tar:
                name = member.name
                if name == 'home' or name.startswith('home/'):
                    if 'home' in parts:
                        tar.extract(member, root, numeric_owner=True, filter=homes)
                elif name == 'root-home' or name.startswith('root-home/'):
                    if 'home' in parts:
                        tar.extract(member.replace(name='root' + name[9:], linkname=root_link(member), deep=False), root,
                                    numeric_owner=True, filter=root_home)
                else:
                    tar.extract(member, staging, numeric_owner=True, filter=staged)
                count += 1
                if count % 64 == 0:
                    progress('Unpacking', os.lseek(archive_fd, 0, os.SEEK_CUR), total, name)
        finished = True
        while proc.stdout.read(fa.CHUNK):
            pass
    except tarfile.FilterError as error:
        fail('ARCHIVE_UNSAFE', 'The archive holds an entry that would land outside the system', reason=str(error)[:200])
    except (tarfile.TarError, EOFError) as error:
        fail('ARCHIVE_DAMAGED', 'The archive is damaged', reason=str(error)[:200])
    finally:
        if not finished and proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
        code = proc.wait()
        with contextlib.suppress(ac.CryptError):
            crypt.close()
    require(code == 0, 'ARCHIVE_DAMAGED', 'The archive is damaged')
    return staging


def restore(context, volume, token, path, parts, confirmed, progress):
    context.confirm(confirmed)
    parts = [p for p in PARTS if p in parts]
    require(parts, 'NOTHING_SELECTED', 'Nothing is selected')
    # A card that is only inserted, as the page found it, is opened read-only for the restore and let
    # go again (2026-11-14 review: its backups were listed and could not be restored).
    found = context.volume(volume, token)
    opened = not found['mounted']
    if opened:
        context.mount(volume, 'ro', False, found['volume_token'])
        token = context.volume(volume)['volume_token']
    try:
        return restore_from(context, volume, token, path, parts, progress)
    finally:
        if opened:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.unmount(volume, context.volume(volume)['volume_token'])


def restore_from(context, volume, token, path, parts, progress):
    clean = fs.clean_path(path)
    folder, _, name = clean.rpartition('/')
    with fs.PinnedVolume(context, volume, token) as source, source.directory(folder) as directory:
        archive_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        try:
            raw = pa.footer(archive_fd)
            try:
                about = json.loads(raw.decode('utf-8')) if raw else None
            except ValueError:
                about = None
            require(isinstance(about, dict) and about.get('schema') == SCHEMA, 'NOT_A_DATA_BACKUP', 'This file is not a Linux data backup')
            parts = [p for p in parts if p in about.get('parts', [])]
            require(parts, 'NOTHING_SELECTED', 'The backup holds none of the chosen parts')
            if ac.is_encrypted(archive_fd):
                # Every byte against its HMAC before one file reaches the system.
                key = ac.open_key(archive_fd, pa.PASSWORD)
                try:
                    ac.verify(archive_fd, key, lambda done, size: progress('Checking the backup', done, size, ''))
                finally:
                    key.close()
            with linux_root(context, write=True) as (pin, root, release):
                staging = unpack_into(archive_fd, root, parts, progress)
                lines, status, note = [], None, ''
                # From here on the staging folder (password hashes, keys) goes whatever happens
                # (2026-11-14 review: a failure while the script was put in left it behind).
                try:
                    # The recovery's own script does the work here; the archive's copy is for other devices.
                    script = staging / 'restore.sh'
                    if script.is_dir() and not script.is_symlink():
                        shutil.rmtree(script)
                    script.unlink(missing_ok=True)
                    fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o700)
                    with os.fdopen(fd, 'wb') as out:
                        out.write(SCRIPT.read_bytes())
                    # And its own table of names: it grows with what the phones have taught it.
                    if (staging / 'aliases.tsv').is_file() and not (staging / 'aliases.tsv').is_symlink():
                        (staging / 'aliases.tsv').write_bytes(ALIASES.encode())
                    run_parts = list(parts)
                    if 'packages' in run_parts and context.mode == 'device':
                        import distro
                        if not distro.recovery_online():
                            run_parts.remove('packages')
                            note = 'Packages need Wi-Fi in the recovery: connect, then restore Packages alone.'
                    progress('Restoring', 0, 1, '')
                    command = ['/bin/sh', '/' + STAGING + '/restore.sh', '--staged', '/' + STAGING, '--home-done', '--parts', ','.join(run_parts)]
                    if context.mode == 'device':
                        status, lines = chroot_lines(root, command)
                    else:
                        # A fixture has no system to enter: the script works on the folder, with the
                        # test's stand-ins for the system's programs.
                        env = dict(os.environ, A22X_ROOT=root, A22X_FAKE_ROOT='1')
                        if os.environ.get('A22X_LINUX_DATA_PATH'):
                            env['PATH'] = os.environ['A22X_LINUX_DATA_PATH'] + ':' + env.get('PATH', '')
                        done = subprocess.run(['/bin/sh', str(staging / 'restore.sh'), '--staged', str(staging), '--home-done',
                                               '--parts', ','.join(run_parts)], env=env, capture_output=True, text=True)
                        status, lines = done.returncode, (done.stdout + done.stderr).splitlines()
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
                os.sync()
        finally:
            os.close(archive_fd)
    said = [line[6:] for line in lines if line.startswith('a22x: ')]
    require(status == 0 and 'done' in said, 'RESTORE_FAILED', 'The restore inside the system did not finish',
            exit=status, log=[line[:200] for line in lines[-12:]])
    return {'parts': parts, 'into': {'id': release.get('ID', ''), 'name': release.get('PRETTY_NAME') or release.get('NAME', '')},
            **script_result(said, note)}


def script_result(said, note=''):
    """What restore.sh SAID (its "a22x: " lines), as the result reads it."""
    # Only the line of two counts (2026-11-14 review: "packages upgrade failed" is three words too, and
    # int('upgrade') ended the helper with a traceback after everything else had been restored).
    count = re.compile(r'[0-9]{1,9}')  # ASCII digits only: '²'.isdigit() is True and int('²') raises (2026-11-14 review)
    packages = next((s.split()[1:] for s in said if s.startswith('packages ') and len(s.split()) == 3
                     and all(count.fullmatch(part) for part in s.split()[1:])), None)
    if 'packages upgrade failed' in said:
        # 2026-11-14 review: the script said why and the result lost it.
        note = UPGRADE_FAILED
    return {'accounts_created': [s.split()[1] for s in said if s.startswith('account ') and s.endswith(' created')],
            'accounts_kept': [s.split()[1] for s in said if s.startswith('account ') and s.endswith(' kept')],
            'owners_changed': sum(1 for s in said if s.startswith('owner ')),
            'packages_installed': int(packages[0]) if packages else 0, 'packages_missing': int(packages[1]) if packages else 0,
            'missing': [s[8:] for s in said if s.startswith('missing ')][:40],
            'packages_skipped': next((int(s.split()[1]) for s in said if s.startswith('packages-skipped ')
                                      and len(s.split()) == 2 and count.fullmatch(s.split()[1])), 0),
            'packages_unsupported': 'packages unsupported' in said, 'note': note}


def parser():
    p = fs.Parser(description=__doc__)
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument('--device', action='store_true')
    kind.add_argument('--fixture')
    commands = p.add_subparsers(dest='operation', required=True, parser_class=fs.Parser)
    p.add_argument('--password-file')
    commands.add_parser('inspect')
    for name in ('backup', 'restore'):
        command = commands.add_parser(name)
        command.add_argument('--volume', required=True)
        command.add_argument('--expected-volume-token')
        command.add_argument('--parts', required=True)
        command.add_argument('--confirm', action='store_true')
        command.add_argument('--encrypt', action='store_true')
        if name == 'restore':
            command.add_argument('--path', required=True)
    return p


def main(argv=None):
    operation = 'unknown'
    try:
        args = parser().parse_args(argv)
        operation = args.operation
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        pa.PASSWORD = ac.read_password(args.password_file)
        pa.ENCRYPT = bool(getattr(args, 'encrypt', False))
        require(not pa.ENCRYPT or pa.PASSWORD, 'PASSWORD_REQUIRED', 'A protected backup needs a password')
        last = [0.0, '']

        def progress(phase, done, total, label):
            now = time.monotonic()
            if phase != last[1] or now - last[0] >= 0.2 or done >= total:
                last[:] = [now, phase]
                print(json.dumps({'event': 'progress', 'operation': 'linux-data-' + operation, 'phase': phase,
                                  'partition': label[-100:], 'bytes_done': done, 'bytes_total': max(total, 1)},
                                 separators=(',', ':')), file=sys.stderr, flush=True)
        if operation == 'inspect':
            # Only a look: it waits out a short job of the recovery's own (2026-11-13, on the phone:
            # OPERATION_BUSY right after the start).
            for attempt in range(30):
                try:
                    with context.operation_lock():
                        value = inspect(context)
                    break
                except fs.FilesystemError as error:
                    if error.code != 'OPERATION_BUSY' or attempt == 29:
                        raise
                    time.sleep(0.5)
        with context.operation_lock() if operation != 'inspect' else contextlib.nullcontext():
            if operation == 'inspect':
                pass
            elif operation == 'backup':
                value = backup(context, args.parts.split(','), args.volume, args.expected_volume_token, args.confirm, progress)
            else:
                value = restore(context, args.volume, args.expected_volume_token, args.path, args.parts.split(','), args.confirm, progress)
        print(json.dumps({**value, 'ok': True, 'operation': operation, 'mode': context.mode}, ensure_ascii=True, separators=(',', ':')))
        return 0
    except fs.FilesystemError as exc:
        error = {'code': exc.code, 'message': exc.message, 'details': exc.details}
    except (pa.ArchiveError, ac.CryptError) as exc:
        error = {'code': exc.code, 'message': str(exc), 'details': {}}
    except (OSError, subprocess.SubprocessError) as exc:
        error = {'code': 'IO_ERROR', 'message': str(exc), 'details': {}}
    print(json.dumps({'ok': False, 'operation': operation, 'error': error}, ensure_ascii=True, separators=(',', ':')))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
