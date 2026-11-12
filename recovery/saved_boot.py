#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""BOOT images kept on cache (2026-11-10): the way back from any BOOT write.

Before BOOT is written (Flash a partition, Samsung's firmware), the BOOT there is kept when it is
a BOOT of this phone: a mainline Linux kernel, or Samsung's Android kernel (stock, or a custom one
built from it). It goes to cache/a22x-recovery/saved-boot/<kind>-<release>-<sha12>.img, the whole
partition, with NAME.json (what it is) and NAME.sha256 (which Flash a partition compares). Cache
stays through every Android and Linux installation. Samsung's BOOT taken out of a firmware is kept
there too (samsung_firmware.py extract-boot). A full cache never stops a write: the copy is then
skipped, and the result says why.
"""
import contextlib
import hashlib
import json
import os
import re
import struct
import time

import bootimage
import filesystem as fs
from install import require

SAVED_BOOT = 'a22x-recovery/saved-boot'
# Where Flash a partition keeps what a partition held before a write (flash.ROLLBACK_FOLDER).
ROLLBACKS = 'A22xPartitionBackups'
ROLLBACK_NAME = re.compile(r'(.+)-(\d{8}-\d{6})(?:-(\d+))?\.img')


def describe_boot(data):
    """kind ('linux' or 'android', for a BOOT of this phone; else None), the kernel's release, and
    root ('magisk' when Magisk patched it, 'apatch' when its kernel carries KernelPatch)."""
    try:
        info = bootimage.inspect_boot(data)
    except bootimage.ImageError:
        return {'kind': None, 'release': '', 'root': ''}
    if not info['device'].get('this_phone'):
        return {'kind': None, 'release': '', 'root': ''}
    return {'kind': 'linux' if info.get('mainline') else 'android', 'release': info['kernel'].get('release') or '',
            'root': info.get('root') or ''}


# 2026-11-10: Samsung's Android puts its own recovery back at every start: vendor_flash_recovery runs
# install-recovery.sh, which patches recovery from BOOT when the whole BOOT (33554432 bytes) has the
# stock SHA-1 (seen on the phone: stock Android replaced this recovery). A short mark in the BOOT's
# unused tail, after its AVB vbmeta and outside every hash and signature, keeps this recovery: the
# kernel, the ramdisk and the AVB check stay exactly the same.
KEEP_RECOVERY_MARK = b'a22x-recovery: this recovery stays (vendor_flash_recovery needs the stock BOOT)\n'
KEEP_RECOVERY_FROM_END = 4096


def keep_recovery_spot(data):
    """Where the keep-recovery mark goes in an Android BOOT of this phone, and whether it is there:
    (offset, marked), or None when the BOOT has no unused tail after its AVB vbmeta."""
    if describe_boot(data)['kind'] != 'android':
        return None
    footer_at = len(data) - 64
    if len(data) < 64 + KEEP_RECOVERY_FROM_END or data[footer_at:footer_at + 4] != b'AVBf':
        return None
    _, _, original, offset, size = struct.unpack_from('>IIQQQ', data, footer_at + 4)
    spot = len(data) - KEEP_RECOVERY_FROM_END
    if spot < offset + size or spot < original:
        return None
    here = data[spot:spot + len(KEEP_RECOVERY_MARK)]
    if here == KEEP_RECOVERY_MARK:
        return spot, True
    return (spot, False) if here == bytes(len(KEEP_RECOVERY_MARK)) else None


def keep_recovery(context, target):
    """The keep-recovery mark written into the Android BOOT on TARGET and read back; the partition's
    SHA-256 afterwards. Nothing for a Linux BOOT (Android does not start) or a BOOT without the tail."""
    fd = context.open_target(target)
    try:
        data = os.pread(fd, target['size_bytes'], 0)
    finally:
        os.close(fd)
    found = keep_recovery_spot(data)
    if found is None:
        return None
    spot, marked = found
    if not marked:
        fd = context.open_target(target, write=True)
        try:
            context.check_target(fd, target)
            os.pwrite(fd, KEEP_RECOVERY_MARK, spot)
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = context.open_target(target)
        try:
            data = os.pread(fd, target['size_bytes'], 0)
        finally:
            os.close(fd)
        if data[spot:spot + len(KEEP_RECOVERY_MARK)] != KEEP_RECOVERY_MARK:
            fs.fail('KEEP_RECOVERY_FAILED', 'The mark that keeps this recovery did not stay in BOOT')
    return {'offset': spot, 'already': marked, 'sha256': hashlib.sha256(data).hexdigest()}


def boot_kind(data):
    """'linux' or 'android' with the kernel's release, for a BOOT of this phone; else (None, '')."""
    found = describe_boot(data)
    return found['kind'], found['release']


def saved_boots(context, folder_name=SAVED_BOOT):
    """The BOOTs kept on cache, newest first: name, path, kind, release, build, sha256, bytes. Cache is
    mounted read-only when it is not mounted, and stays so."""
    volume = context.fs.volume('cache')
    if not volume['mounted']:
        context.fs.mount('cache', 'ro', True, volume['volume_token'])
    found = []
    with fs.PinnedVolume(context.fs, 'cache') as pin:
        try:
            with pin.directory(folder_name) as folder:
                names = sorted(os.listdir(folder))
                for name in names:
                    if not name.endswith('.img') or name + '.json' not in names:
                        continue
                    try:
                        meta_fd = fs.open_regular(name + '.json', dir_fd=folder)
                        try:
                            meta = json.loads(os.pread(meta_fd, 65536, 0).decode('utf-8'))
                        finally:
                            os.close(meta_fd)
                        size = os.stat(name, dir_fd=folder, follow_symlinks=False).st_size
                    except (OSError, ValueError, fs.FilesystemError):
                        continue
                    if not isinstance(meta, dict) or meta.get('bytes') != size:
                        continue
                    found.append({'name': name, 'path': '/' + folder_name + '/' + name, 'kind': str(meta.get('kind', '')),
                                  'release': str(meta.get('release', ''))[:120], 'build': str(meta.get('build', ''))[:40],
                                  'sha256': str(meta.get('sha256', '')), 'bytes': size, 'saved': int(meta.get('saved', 0) or 0),
                                  'root': str(meta.get('root', ''))[:16]})
        except (FileNotFoundError, fs.FilesystemError):
            return []
    return sorted(found, key=lambda item: (item['name'].startswith('stock-'), item['saved']), reverse=True)


def keep_current_boot(context, progress, new_sha256=None):
    """The BOOT now on the phone, kept on cache before it is replaced: {'kept': path or None, ...}."""
    target = context.target('boot')
    fd = context.open_target(target)
    try:
        data, at = bytearray(), 0
        while at < target['size_bytes']:
            part = os.pread(fd, min(4 << 20, target['size_bytes'] - at), at)
            require(part, 'SHORT_READ', 'BOOT could not be read')
            data.extend(part)
            at += len(part)
    finally:
        os.close(fd)
    # Kept as the image itself: without the keep-recovery mark, which flashing puts back (the stock
    # BOOT taken out of the firmware and the one kept here are then the same file).
    found = keep_recovery_spot(bytes(data))
    if found and found[1]:
        data[found[0]:found[0] + len(KEEP_RECOVERY_MARK)] = bytes(len(KEEP_RECOVERY_MARK))
    image = bytes(data)
    digest = hashlib.sha256(image).hexdigest()
    if digest == new_sha256:
        return {'kept': None, 'note': 'identical'}
    kind, release = boot_kind(image)
    if not kind:
        return {'kept': None, 'note': 'not a BOOT of this phone'}
    name = kind + '-' + re.sub(r'[^A-Za-z0-9._+-]', '_', (release.split() or ['boot'])[0])[:48] + '-' + digest[:12] + '.img'
    try:
        meta = {'kind': kind, 'release': release[:120]}
        if describe_boot(image)['root']:
            meta['root'] = describe_boot(image)['root']  # a BOOT with Magisk or APatch: not the stock one to go back to
        path = save_on_cache(context, name, image, meta, progress)
    except (fs.FilesystemError, OSError) as error:
        return {'kept': None, 'note': getattr(error, 'code', None) or 'CACHE_UNAVAILABLE'}
    return {'kept': path, 'kind': kind, 'release': release}


def content_key(data):
    """SHA-256 of a kept BOOT as the image itself: the keep-recovery mark, where it is, counts as zeros."""
    spot = len(data) - KEEP_RECOVERY_FROM_END
    if spot > 0 and data[spot:spot + len(KEEP_RECOVERY_MARK)] == KEEP_RECOVERY_MARK:
        data = data[:spot] + bytes(len(KEEP_RECOVERY_MARK)) + data[spot + len(KEEP_RECOVERY_MARK):]
    return hashlib.sha256(data).hexdigest()


def tidy_folder(folder):
    """2026-11-10: cache is 200 MiB and filled up with the same BOOT under several names (from the
    firmware, kept before a flash, once with the keep-recovery mark). Copies with the same content
    are removed but one: the one taken out of the firmware (stock-), else the oldest. Copies that
    differ are all kept. Returns {name: content key} of what stays."""
    entries = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith('.img') or name + '.json' not in os.listdir(folder):
            continue
        try:
            fd = fs.open_regular(name, dir_fd=folder)
            try:
                size = os.fstat(fd).st_size
                data, at = bytearray(), 0
                while at < size:
                    part = os.pread(fd, min(4 << 20, size - at), at)
                    if not part:
                        break
                    data.extend(part)
                    at += len(part)
            finally:
                os.close(fd)
            meta_fd = fs.open_regular(name + '.json', dir_fd=folder)
            try:
                saved = int(json.loads(os.pread(meta_fd, 65536, 0).decode('utf-8')).get('saved', 0) or 0)
            finally:
                os.close(meta_fd)
        except (OSError, ValueError, AttributeError, fs.FilesystemError):
            continue
        entries.append((content_key(bytes(data)), not name.startswith('stock-'), saved, name))
    kept = {}
    for key, _, _, name in sorted(entries):
        if key in kept.values():
            for suffix in ('', '.json', '.sha256'):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name + suffix, dir_fd=folder)
            continue
        kept[name] = key
    return kept


def prune_rollbacks(folder, keep=None):
    """2026-11-11: cache is 200 MiB, and two flashes of the recovery left 80 MiB of old recoveries
    in A22xPartitionBackups; a root install then found no room for its BOOT. Of each partition's
    copies kept before a write, only the newest stays (KEEP, the copy just written, when given: the
    clock may have been wrong when an older one was named). Returns the bytes freed."""
    groups = {}
    for name in os.listdir(folder):
        match = ROLLBACK_NAME.fullmatch(name)
        if match:
            # A date this recovery cannot have seen (Samsung's clock form, year 2086) sorts as oldest.
            sane = '2026' <= match.group(2)[:4] < '2050'
            groups.setdefault(match.group(1), []).append((name == keep, sane, match.group(2), int(match.group(3) or 0), name))
    freed = 0
    for copies in groups.values():
        for *_, name in sorted(copies)[:-1]:
            with contextlib.suppress(OSError):
                size = os.stat(name, dir_fd=folder, follow_symlinks=False).st_size
                os.unlink(name, dir_fd=folder)
                freed += size
    return freed


def prune_rooted(folder):
    """Of the kept BOOTs that carry root (Magisk or APatch patched them here, and Root makes them
    again), only the newest stays; a BOOT without root, the stock one above all, is never removed
    this way (2026-11-11: every change of root left another 32 MiB on cache)."""
    rooted = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith('.img'):
            continue
        try:
            fd = fs.open_regular(name + '.json', dir_fd=folder)
            try:
                meta = json.loads(os.pread(fd, 65536, 0).decode('utf-8'))
            finally:
                os.close(fd)
        except (OSError, ValueError, fs.FilesystemError):
            continue
        if isinstance(meta, dict) and meta.get('root'):
            rooted.append((int(meta.get('saved', 0) or 0), name))
    for _, name in sorted(rooted)[:-1]:
        for suffix in ('', '.json', '.sha256'):
            with contextlib.suppress(OSError):
                os.unlink(name + suffix, dir_fd=folder)


def downloadable_boots():
    """{SHA-256: release} of the Linux BOOTs published before the current one, which can be
    downloaded again: the catalog's linux_boot_older. The catalog beside this file is read."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog.json'), encoding='utf-8') as source:
            catalog = json.load(source)
    except (OSError, ValueError):
        return {}
    found = {}
    for item in catalog.get('linux_boot_older') or ():
        if isinstance(item, dict) and re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', ''))) \
                and str(item.get('url', '')).startswith('https://'):
            found[item['sha256']] = str(item.get('version', ''))[:24]
    return found


def prune_downloadable(folder, downloadable):
    """Kept Linux BOOTs that are older published releases go: they can be downloaded again
    (2026-11-12: three Linux BOOTs, the stock BOOT and a rooted one filled 160 of cache's 200 MiB,
    and a recovery image no longer fitted). A Linux BOOT that is not such a release, a build of
    one's own, is the only copy of it and stays."""
    for name in sorted(os.listdir(folder)):
        if not name.endswith('.img'):
            continue
        try:
            fd = fs.open_regular(name + '.json', dir_fd=folder)
            try:
                meta = json.loads(os.pread(fd, 65536, 0).decode('utf-8'))
            finally:
                os.close(fd)
        except (OSError, ValueError, fs.FilesystemError):
            continue
        if isinstance(meta, dict) and meta.get('kind') == 'linux' and meta.get('sha256') in downloadable:
            for suffix in ('', '.json', '.sha256'):
                with contextlib.suppress(OSError):
                    os.unlink(name + suffix, dir_fd=folder)


def make_room(pin, downloadable=None):
    """Space on cache from what is there twice or can be made again: the same BOOT under two names,
    older BOOTs with root, older published Linux BOOTs (DOWNLOADABLE, else the catalog's), and all
    but the newest copy of each partition kept before a write. What is the only copy of something
    stays: the stock BOOT, a Linux BOOT that cannot be downloaded again, settings, keys, Wi-Fi."""
    if downloadable is None:
        downloadable = downloadable_boots()
    for name, tidy in ((SAVED_BOOT, tidy_folder), (SAVED_BOOT, prune_rooted),
                       (SAVED_BOOT, lambda folder: prune_downloadable(folder, downloadable)), (ROLLBACKS, prune_rollbacks)):
        with contextlib.suppress(FileNotFoundError, NotADirectoryError, fs.FilesystemError, OSError):
            with pin.directory(name) as folder:
                tidy(folder)


def remove_from_cache(context, path):
    """PATH (a file this recovery made on cache, with its .json and .sha256) removed; cache is left
    mounted as it was found."""
    volume = context.fs.volume('cache')
    mounted_here = not volume['mounted']
    reopen = volume['mounted'] and volume['read_only'] and volume.get('mount_owner') == 'filesystem'
    if mounted_here or reopen:
        context.fs.mount('cache', 'rw', True, volume['volume_token'])
    try:
        with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
            folder_name, _, name = path.strip('/').rpartition('/')
            with pin.directory(folder_name) as folder:
                for suffix in ('', '.json', '.sha256'):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(name + suffix, dir_fd=folder)
            os.sync()
    finally:
        if mounted_here:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.fs.unmount('cache')
        elif reopen:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.fs.mount('cache', 'ro', True, context.fs.volume('cache')['volume_token'])


def save_on_cache(context, name, image, meta, progress, keep_mounted=False, folder_name=SAVED_BOOT):
    """IMAGE as SAVED_BOOT/NAME on cache, with NAME.json (what it is) and NAME.sha256 (which Flash a
    partition compares). Cache is mounted for writing when needed and left as it was found, or, with
    KEEP_MOUNTED, read-only, so the copy can be flashed right away."""
    digest = hashlib.sha256(image).hexdigest()
    end = len(image)
    volume = context.fs.volume('cache')
    mounted_here = not volume['mounted']
    reopen = volume['mounted'] and volume['read_only'] and volume.get('mount_owner') == 'filesystem'
    if mounted_here or reopen:
        context.fs.mount('cache', 'rw', True, volume['volume_token'])
    try:
        with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
            room = os.fstatvfs(pin.fd)
            folder = os.dup(pin.fd)
            try:
                for part in folder_name.split('/'):
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=folder)
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
                    os.close(folder)
                    folder = nxt
                    pin.check_fd(folder)
                try:
                    old = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
                except FileNotFoundError:
                    old = None
                if old is not None:
                    try:
                        same = os.fstat(old).st_size == end and fs.hash_fd(old) == digest
                    finally:
                        os.close(old)
                    if same:
                        return '/' + folder_name + '/' + name
                    os.unlink(name, dir_fd=folder)
                if folder_name == SAVED_BOOT:
                    # The same BOOT is not kept twice: the copy there is the way back already.
                    kept = tidy_folder(folder)
                    twin = next((other for other, key in kept.items() if key == content_key(image)), None)
                    if twin:
                        return '/' + folder_name + '/' + twin
                    room = os.fstatvfs(pin.fd)
                if room.f_bavail * room.f_frsize <= end + (8 << 20):
                    # 2026-11-11, on the phone: a root install stopped here with copies of the same
                    # BOOT and of older recoveries filling cache. They go first.
                    make_room(pin)
                    room = os.fstatvfs(pin.fd)
                require(room.f_bavail * room.f_frsize > end + (8 << 20), 'CACHE_FULL', 'Cache has no room to keep this BOOT')
                out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
                try:
                    view, at = memoryview(image), 0
                    while at < end:
                        at += os.write(out, view[at:at + (1 << 20)])
                        progress('Saving BOOT', {'label': 'boot', 'size_bytes': end}, at)
                    os.fsync(out)
                finally:
                    os.close(out)
                notes = {'schema': 'a22x.saved-boot.v1', **meta, 'sha256': digest, 'bytes': end, 'saved': int(time.time())}
                for suffix, text in (('.json', json.dumps(notes, indent=1) + '\n'), ('.sha256', digest + '  ' + name + '\n')):
                    side = os.open(name + suffix, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                   dir_fd=folder)
                    try:
                        os.write(side, text.encode())
                        os.fsync(side)
                    finally:
                        os.close(side)
            finally:
                os.close(folder)
            os.sync()
    finally:
        if mounted_here and not keep_mounted:
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.fs.unmount('cache')
        elif reopen or (mounted_here and keep_mounted):
            with contextlib.suppress(fs.FilesystemError, OSError):
                context.fs.mount('cache', 'ro', True, context.fs.volume('cache')['volume_token'])
    return '/' + folder_name + '/' + name
