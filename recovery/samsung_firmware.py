#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Samsung's stock firmware, flashed by this recovery the way Odin flashes it (2026-11-10).

Samsung ships firmware as tars with their MD5 written after them: AP (Android: super, boot, dtbo,
vbmeta, userdata...), BL (the bootloader chain), CP (the modem) and CSC or HOME_CSC (regional data:
prism, optics, cache; CSC also a PIT). The user chooses the AP; the others are found beside it.
Every image is read in place, through lz4, and checked as flash.py checks any image:

  AP        super, boot, dtbo, vbmeta, vbmeta_system   written
            recovery                                   kept: this recovery stays
            userdata                                   replaced by a fresh Android userdata (below)
            *-verified, tzar                           not written: firmware of the bootloader chain
  CSC       prism, optics                              written
            cache                                      wiped instead; the recovery's own folder stays
            omr                                        kept
            *.pit                                      not written: it would repartition the phone
  BL        everything                                 not written: the bootloader chain stays
  CP        md1img                                     kept: the modem stays as it is

After the images, as Samsung's recovery does after Odin (CSC's recovery command is
--data_resizing): userdata is formatted for Android (f2fs as Samsung's own userdata.img) and the
start of metadata erased, so Android sets up its encryption on its first start. BOOT's old
content, when it is a Linux BOOT, is saved on cache first, so Linux can come back.

    samsung_firmware.py --device|--fixture DIR inspect --volume V --volume-token T --path AP
    samsung_firmware.py ... install --volume V --volume-token T --path AP --expected-plan SHA --confirm
"""
import argparse
import contextlib
import hashlib
import struct
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import types
import zipfile
import zlib

import bootimage
import filesystem as fs
import flash
import install
import saved_boot
from saved_boot import SAVED_BOOT, save_on_cache
from install import require
from operation_lock import ENV_FD, operation_lock

FILE_NAME = re.compile(r'^(AP|BL|CP|CSC|HOME_CSC)_[A-Za-z0-9_.+-]{1,200}\.tar(?:\.md5)?$')
# A226BXXSBDZC3: model, region (XX, OXM...), then the version: bootloader revision B, update D,
# year Z (2024), month C, build 3. Files of one firmware share the version.
BUILD = re.compile(r'A226B[A-Z0-9]{3,4}[A-Z0-9]{5}')
MD5_LINE = re.compile(rb'([0-9a-f]{32})  ?([^\n/]{1,240}\.tar)\n?\Z')
WRITTEN = {'boot.img': 'boot', 'dtbo.img': 'dtbo', 'vbmeta.img': 'vbmeta', 'vbmeta_system.img': 'vbmeta_system',
           'vbmeta_vendor.img': 'vbmeta_vendor', 'super.img': 'super', 'prism.img': 'prism', 'optics.img': 'optics'}
# Odin's order: super first, BOOT last, so an interrupted flash still leaves the old BOOT's kernel.
ORDER = ('super', 'prism', 'optics', 'dtbo', 'vbmeta_system', 'vbmeta_vendor', 'vbmeta', 'boot')
INSTEAD = {'userdata.img': "Formatted for Android instead, as Samsung's recovery does after Odin",
           'cache.img': "Wiped instead; the recovery's own folder stays"}
KEPT = {'recovery.img': 'This recovery stays', 'omr.img': 'Kept as it is', 'md1img.img': 'The modem stays as it is'}
BOOT_CHAIN = re.compile(r'^(preloader.*|lk.*|tee.*|gz.*|sspm.*|scp.*|spmfw.*|mcupm.*|dpm.*|pi_img.*|tzar.*|'
                        r'param\.bin|up_param\.bin|efuse.*|cam_vpu.*|audio_dsp.*)$')
# Samsung's BOOT is the whole 32 MiB partition with its AVB footer; its LZ4 file in the AP ~15 MB.
BOOT_LZ4_LIMIT = 64 << 20
BOOT_LIMIT = 64 << 20
MAX_MEMBERS = 64


def fail(code, message, **details):
    fs.fail(code, message, **details)


def kind_of(name, members):
    """AP, BL, CP, CSC or HOME_CSC: from the name Samsung gives, else from what the tar holds."""
    match = FILE_NAME.match(name)
    if match:
        return match.group(1)
    plain = {m[:-4] if m.endswith('.lz4') else m for m in members}
    if 'super.img' in plain or 'boot.img' in plain:
        return 'AP'
    if any(m.startswith('preloader') for m in plain):
        return 'BL'
    if 'md1img.img' in plain:
        return 'CP'
    if 'prism.img' in plain or 'optics.img' in plain:
        return 'CSC' if any(m.endswith('.pit') for m in plain) else 'HOME_CSC'
    return None


def name_kind(path):
    """AP, BL, CP, CSC or HOME_CSC from Samsung's file name, or None."""
    match = FILE_NAME.match(path.rsplit('/', 1)[-1])
    return match.group(1) if match else None


def build_of(name):
    found = BUILD.search(name)
    return found.group(0) if found else ''


def md5_trailer(fd, size):
    """(MD5, size of the tar before it) for Samsung's .tar.md5, or (None, size) for a plain tar."""
    tail = os.pread(fd, 512, max(0, size - 512))
    found = MD5_LINE.search(tail)
    if not found:
        return None, size
    return found.group(1).decode(), size - (len(tail) - found.start())


def members_of(fd):
    """The regular files in the tar: name, size, where the data starts."""
    with flash.shared_offset(fd) as handle, tarfile.open(fileobj=handle, mode='r:') as archive:
        rows = []
        for info in archive:
            if len(rows) >= MAX_MEMBERS:
                fail('FIRMWARE_UNREADABLE', 'The firmware file holds too many entries')
            if info.isreg():
                rows.append({'name': info.name, 'size': info.size})
        return rows


def member_fate(kind, name):
    """What happens to one image: (action, partition or None, reason)."""
    plain = name[:-4] if name.endswith('.lz4') else name
    if name.startswith('meta-data/') or plain.startswith('meta-data'):
        return 'skipped', None, "Samsung's update data, not an image"
    if kind == 'BL':
        return 'refused', None, 'The bootloader chain is never written by this recovery'
    if plain.endswith('.pit'):
        return 'refused', None, 'A partition table: it would repartition the phone'
    if BOOT_CHAIN.match(plain):
        return 'refused', None, 'Firmware of the bootloader chain stays as it is'
    if plain in WRITTEN:
        return 'write', WRITTEN[plain], None
    if plain in INSTEAD:
        return 'instead', plain[:-4], INSTEAD[plain]
    if plain in KEPT:
        return 'kept', None, KEPT[plain]
    return 'kept', None, 'Not written: unknown to this recovery'


@contextlib.contextmanager
def opened(context, volume, token, path):
    with flash.source_file(context, volume, token, path) as fd:
        yield fd


def read_file(context, volume, token, file_path, name):
    """One firmware file: its kind, version, images, MD5 trailer and identity."""
    with opened(context, volume, token, file_path) as fd:
        st = os.fstat(fd)
        require(os.pread(fd, 5, 257) == b'ustar', 'NOT_SAMSUNG_FIRMWARE', 'This is not a Samsung firmware file (a .tar.md5)')
        try:
            members = members_of(fd)
        except (tarfile.TarError, ValueError) as error:
            fail('FIRMWARE_UNREADABLE', 'The firmware file is damaged', file=name, reason=str(error)[:200])
        md5, body = md5_trailer(fd, st.st_size)
        return {'name': name, 'path': file_path, 'kind': kind_of(name, [m['name'] for m in members]), 'build': build_of(name),
                'size': st.st_size, 'md5': md5, 'tar_bytes': body, 'identity': flash.identity(fd), 'members': members}


def inspect(context, volume, token, path):
    """The plan for one firmware: every image of every file with what happens to it. The AP is
    chosen; one regional file (CSC before HOME_CSC, of the AP's version first), BL and CP are
    found beside it."""
    relative = fs.clean_path(path)
    folder, _, chosen = relative.rpartition('/')
    where = lambda name: '/' + (folder + '/' + name if folder else name).lstrip('/')
    ap = read_file(context, volume, token, where(chosen), chosen)
    require(ap['kind'] == 'AP', 'AP_REQUIRED', 'Choose the AP file of the firmware (AP_....tar.md5)')
    version = ap['build'][-5:]
    with fs.PinnedVolume(context.fs, volume, token) as pin, pin.directory(folder) as directory:
        names = sorted(n for n in os.listdir(directory) if n != chosen and FILE_NAME.match(n))
    warnings, blockers = [], []
    pick = {}
    for name in names:
        kind = FILE_NAME.match(name).group(1)
        if kind == 'AP':
            continue
        group = 'CSC' if kind in ('CSC', 'HOME_CSC') else kind
        rank = (build_of(name)[-5:] != version, kind == 'HOME_CSC', name)
        if group not in pick or rank < pick[group][0]:
            pick[group] = (rank, name)
    files = [ap]
    for group in ('CSC', 'BL', 'CP'):
        if group in pick:
            try:
                files.append(read_file(context, volume, token, where(pick[group][1]), pick[group][1]))
            except fs.FilesystemError as error:
                warnings.append('A firmware file beside the AP could not be read')
    rows, writes = [], {}
    for item in files:
        for member in item['members']:
            action, target, reason = member_fate(item['kind'], member['name'])
            rows.append({'file': item['kind'], 'name': member['name'], 'size': member['size'], 'action': action,
                         'target': target, 'reason': reason})
            if action == 'write':
                if target in writes:
                    blockers.append('DUPLICATE_IMAGE')
                writes[target] = {'target': target, 'file': item['name'], 'path': item['path'], 'member': member['name'],
                                  'size': member['size']}
    if ap['build'] and not ap['build'].startswith('A226B'):
        blockers.append('OTHER_MODEL')
    if not ap['build']:
        warnings.append('The file name does not name the firmware version; BOOT is still checked for this phone')
    for item in files[1:]:
        if item['build'] and version and item['build'][-5:] != version and item['kind'] in ('CSC', 'HOME_CSC'):
            warnings.append('The regional file is of another version than the AP')
    if not any(f['kind'] in ('CSC', 'HOME_CSC') for f in files):
        warnings.append('No CSC file beside the AP: prism and optics stay as they are')
    if any(f['md5'] is None for f in files):
        warnings.append('A file has no MD5 at its end; every image is still checked as it is read')
    if 'super' not in writes or 'boot' not in writes:
        blockers.append('AP_INCOMPLETE')
    ordered = [writes[t] for t in ORDER if t in writes]
    plan = {'files': [{k: f[k] for k in ('name', 'path', 'identity', 'size')} for f in files],
            'writes': [{k: w[k] for k in ('target', 'path', 'member', 'size')} for w in ordered],
            'erase': ['userdata', 'metadata', 'cache']}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'volume': volume, 'path': where(chosen), 'build': ap['build'], 'version': version,
            'files': [{k: f[k] for k in ('name', 'path', 'kind', 'build', 'size', 'md5')} for f in files], 'rows': rows,
            'writes': ordered, 'erase': plan['erase'], 'warnings': warnings, 'blockers': sorted(set(blockers)),
            'plan_sha256': digest, 'write_bytes': sum(w['size'] for w in ordered)}


UNPACK_STORE = 'A22xFirmware'


def same_member(out, info):
    """A file already unpacked from this very ZIP entry: its size and the CRC kept beside it."""
    try:
        marker_fd = fs.open_regular(info.filename + '.crc', dir_fd=out)
        try:
            return (os.stat(info.filename, dir_fd=out, follow_symlinks=False).st_size == info.file_size
                    and os.pread(marker_fd, 64, 0).decode().strip() == '%08x' % info.CRC)
        finally:
            os.close(marker_fd)
    except (OSError, fs.FilesystemError):
        return False


def unpack_into(pin, folder, out_name, archive, members, total, progress):
    """The tars of the ZIP into FOLDER/OUT_NAME on the pinned volume; what is left of a failed
    run never looks unpacked (.part- files and the CRC written last)."""
    unpacked, done = [], 0
    with pin.directory(folder) as parent:
        with contextlib.suppress(FileExistsError):
            os.mkdir(out_name, 0o700, dir_fd=parent)
        out = os.open(out_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            pin.check_fd(out)
            for info in members:
                if same_member(out, info):
                    done += info.file_size
                    unpacked.append(info.filename)
                    continue
                part, marker = '.part-' + info.filename, info.filename + '.crc'
                for stale in (part, marker):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(stale, dir_fd=out)
                target = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=out)
                try:
                    with archive.open(info) as stream:  # zipfile checks the CRC at the member's end
                        while True:
                            data = stream.read(4 << 20)
                            if not data:
                                break
                            view, at = memoryview(data), 0
                            while at < len(data):
                                at += os.write(target, view[at:])
                            done += len(data)
                            progress('Unpacking', {'label': name_kind(info.filename), 'size_bytes': total}, done)
                    os.fsync(target)
                except BaseException as error:
                    os.close(target)
                    with contextlib.suppress(OSError):
                        os.unlink(part, dir_fd=out)
                    if isinstance(error, (zlib.error, zipfile.BadZipFile, EOFError)):
                        fail('FIRMWARE_DAMAGED', 'The firmware file is damaged', file=info.filename, reason=str(error)[:200])
                    raise
                os.close(target)
                os.rename(part, info.filename, src_dir_fd=out, dst_dir_fd=out)
                side = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=out)
                try:
                    os.write(side, ('%08x\n' % info.CRC).encode())
                    os.fsync(side)
                finally:
                    os.close(side)
                unpacked.append(info.filename)
            os.fsync(out)
        finally:
            os.close(out)
    return unpacked


def room_for(pin, folder, out_name, members):
    """(bytes still to unpack into FOLDER/OUT_NAME, bytes free there)."""
    with pin.directory(folder) as parent:
        try:
            out = os.open(out_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        except FileNotFoundError:
            need = sum(i.file_size for i in members)
            room = os.fstatvfs(parent)
        else:
            try:
                need = sum(i.file_size for i in members if not same_member(out, i))
                room = os.fstatvfs(out)
            finally:
                os.close(out)
    return need, room.f_bavail * room.f_frsize


def writable(context, volume):
    """The volume mounted for writing, or None when it cannot be (encrypted, unsupported, busy)."""
    now = context.fs.volume(volume)
    if not now['mountable'] or not now['write_supported']:
        return None
    if now['mounted'] and not now['read_only']:
        return now
    if now['mounted'] and now.get('mount_owner') != 'filesystem':
        return None
    try:
        context.fs.mount(volume, 'rw', True, now['volume_token'])
    except fs.FilesystemError:
        return None
    return context.fs.volume(volume)


def unpack_zip(context, volume, token, path, confirmed, progress):
    """Samsung's firmware as one ZIP (samfw.com and others: AP, BL, CP, CSC and HOME_CSC tars, each
    deflated) unpacked beside it into a folder named after it, each tar checked by the ZIP's CRC.
    The tars are then installed as any firmware folder; a file already unpacked from the same ZIP
    entry is kept. When there is no room beside it (2026-11-13, on the phone: a 5.8 GB ZIP needs
    7.2 GB more on a 16 GB card), they go to userdata (A22xFirmware/), which the installation
    formats for Android at its end anyway; userdata encrypted by Android cannot take them."""
    require(confirmed, 'CONFIRMATION_REQUIRED', 'Unpacking the firmware must be confirmed')
    relative = fs.clean_path(path)
    folder, _, name = relative.rpartition('/')
    require(name.lower().endswith('.zip'), 'NOT_A_ZIP', 'This file is not a ZIP or a tar')
    # Writable before the ZIP is opened: a mounted volume is remounted for writing, which needs it idle.
    volume_now = context.fs.volume(volume, token)
    if volume_now['mounted'] and volume_now['read_only'] and volume_now.get('mount_owner') == 'filesystem':
        context.fs.mount(volume, 'rw', True, token)
    out_name = name[:-4]
    with flash.source_file(context, volume, token, '/' + relative) as fd, flash.shared_offset(fd) as handle, \
            zipfile.ZipFile(handle) as archive:
        members = [i for i in archive.infolist() if not i.is_dir() and name_kind(i.filename)
                   and i.filename.lower().endswith(('.tar.md5', '.tar'))]
        require(sum(1 for i in members if name_kind(i.filename) == 'AP') == 1, 'AP_NOT_IN_ZIP', 'The ZIP holds no AP file, or several')
        require(len(members) <= 8 and all('/' not in i.filename.strip('/') for i in members), 'FIRMWARE_UNREADABLE',
                'The firmware file holds too many entries')
        total = sum(i.file_size for i in members)
        reserve = 64 << 20
        with fs.PinnedVolume(context.fs, volume, token, writing=True) as pin:
            need, free = room_for(pin, folder, out_name, members)
            if free > need + reserve:
                unpacked = unpack_into(pin, folder, out_name, archive, members, total, progress)
                where_volume, where = volume, '/' + (folder + '/' if folder else '') + out_name
        if free <= need + reserve:
            data = writable(context, 'userdata') if volume != 'userdata' else None
            if data is None:
                message = 'There is not enough space beside the ZIP to unpack the firmware, and userdata is encrypted by Android. Free space beside the ZIP, or format userdata in Wipe (the installation formats it for Android anyway), then choose the ZIP again'  # noqa: E501 - one text for the translations
                fail('INSUFFICIENT_SPACE', message, required_bytes=need, available_bytes=free)
            with fs.PinnedVolume(context.fs, 'userdata', data['volume_token'], writing=True) as pin:
                with pin.directory('') as parent:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(UNPACK_STORE, 0o700, dir_fd=parent)
                need, free = room_for(pin, UNPACK_STORE, out_name, members)
                require(free > need + reserve, 'INSUFFICIENT_SPACE',
                        'There is not enough space beside the ZIP or on userdata to unpack the firmware',
                        required_bytes=need, available_bytes=free)
                unpacked = unpack_into(pin, UNPACK_STORE, out_name, archive, members, total, progress)
                where_volume, where = 'userdata', '/' + UNPACK_STORE + '/' + out_name
    ap = next(n for n in unpacked if name_kind(n) == 'AP')
    return {'folder': where, 'ap': where + '/' + ap, 'files': unpacked, 'bytes': total,
            'volume': where_volume, 'volume_token': context.fs.volume(where_volume)['volume_token']}


def verify_md5(context, volume, token, item, progress):
    """Odin's check: the MD5 of the tar before its trailer."""
    with opened(context, volume, token, item['path']) as fd:
        md5, body = md5_trailer(fd, os.fstat(fd).st_size)
        if md5 is None:
            return None
        digest, at = hashlib.md5(), 0
        while at < body:
            data = os.pread(fd, min(4 << 20, body - at), at)
            require(data, 'SHORT_FIRMWARE', 'The firmware file ended early', file=item['name'])
            digest.update(data)
            at += len(data)
            progress('Verifying', {'label': item['kind'], 'size_bytes': body}, at)
        require(digest.hexdigest() == md5, 'FIRMWARE_DAMAGED', 'The firmware file is damaged: its MD5 does not match',
                file=item['name'])
        return md5


def ap_member(context, volume, token, path, wanted, limit):
    """WANTED's bytes from the firmware's AP: in place from AP_....tar.md5, or from Samsung's firmware
    as one ZIP (AP, BL, CP and CSC tars, each deflated, as samfw.com ships them), where the AP is read
    from its start up to WANTED (BOOT comes first). Also the AP's name and version."""
    relative = '/' + fs.clean_path(path).lstrip('/')
    name = relative.rsplit('/', 1)[-1]
    with flash.source_file(context, volume, token, relative) as fd:
        if os.pread(fd, 5, 257) == b'ustar':
            start, end = flash.tar_member(fd, wanted)
            require(end - start <= limit, 'FIRMWARE_UNREADABLE', 'The image in the firmware is too large')
            return os.pread(fd, end - start, start), name, build_of(name)
        require(os.pread(fd, 4, 0) == b'PK\x03\x04', 'NOT_SAMSUNG_FIRMWARE', 'This is not a Samsung firmware file (a .tar.md5)')
        with flash.shared_offset(fd) as handle, zipfile.ZipFile(handle) as archive:
            aps = [i for i in archive.infolist() if name_kind(i.filename) == 'AP']
            require(len(aps) == 1, 'AP_NOT_IN_ZIP', 'The ZIP holds no AP file, or several')
            info = aps[0]
            ap_name = info.filename.rsplit('/', 1)[-1]
            with archive.open(info) as stream, tarfile.open(fileobj=stream, mode='r|') as tar:
                for member in tar:
                    if member.name == wanted and member.isreg():
                        require(member.size <= limit, 'FIRMWARE_UNREADABLE', 'The image in the firmware is too large')
                        return tar.extractfile(member).read(), ap_name, build_of(ap_name)
            fail('TAR_MEMBER_MISSING', 'The package has no such image', member=wanted)


def lz4_bounded(packed, limit, seconds=60):
    """PACKED decoded by lz4, at most LIMIT bytes and SECONDS long (2026-11-13 review: a damaged or
    hostile boot.img.lz4 could fill the recovery's RAM with gigabytes before its size was checked)."""
    import tempfile
    with tempfile.TemporaryFile() as source:
        source.write(packed)
        source.seek(0)
        proc = subprocess.Popen((flash.LZ4, '-dc'), stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, deadline = bytearray(), time.monotonic() + seconds
        try:
            while True:
                require(time.monotonic() < deadline, 'INVALID_COMPRESSED_IMAGE', 'The compressed file takes too long to unpack')
                data = proc.stdout.read(1 << 20)
                if not data:
                    break
                out.extend(data)
                require(len(out) <= limit, 'INVALID_COMPRESSED_IMAGE', 'The compressed file unpacks larger than a BOOT can be')
            code = proc.wait(timeout=max(1, deadline - time.monotonic()))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
    require(code == 0 and len(out) > 0, 'INVALID_COMPRESSED_IMAGE', 'The compressed file is damaged')
    return bytes(out)


def extract_boot(context, volume, token, path, progress):
    """Samsung's BOOT taken out of the firmware and kept on cache: the way back from a custom kernel."""
    packed, source, build = ap_member(context, volume, token, path, 'boot.img.lz4', BOOT_LZ4_LIMIT)
    progress('Unpacking', {'label': 'boot', 'size_bytes': len(packed)}, 0)
    image = lz4_bounded(packed, BOOT_LIMIT)
    try:
        info = bootimage.inspect_boot(image)
    except bootimage.ImageError as error:
        fail('NOT_STOCK_BOOT', 'This firmware has no BOOT for this phone', reason=str(error)[:200])
    require(info['device'].get('this_phone') and not info.get('mainline'), 'NOT_STOCK_BOOT', 'This firmware has no BOOT for this phone')
    avb = info.get('avb') or {}
    require(not avb.get('present') or avb.get('state') == 'match', 'BOOT_DAMAGED', 'The BOOT in the firmware is damaged: its AVB hash does not match')
    release = info['kernel'].get('release') or ''
    digest = hashlib.sha256(image).hexdigest()
    name = 'stock-' + re.sub(r'[^A-Za-z0-9._+-]', '_', build or 'boot')[:40] + '-' + digest[:12] + '.img'
    saved = save_on_cache(context, name, image, {'kind': 'android', 'release': release[:120], 'build': build, 'source': source[:200]},
                          progress, keep_mounted=True)
    cache = context.fs.volume('cache')
    return {'saved_boot': saved, 'build': build, 'release': release, 'sha256': digest, 'bytes': len(image), 'source': source,
            'volume': 'cache', 'volume_token': cache['volume_token']}


def flash_one(context, volume, token, write, progress):
    """One image through flash.py: checked, written, read back."""
    args = types.SimpleNamespace(target=write['target'], volume=volume, volume_token=token, path=write['path'],
                                 member=write['member'], image_bytes=None, confirm=True)
    checked = flash.check(context, args, progress)
    args.expected_sha256, args.expected_identity = checked['image']['sha256'], checked['image']['identity']
    done = flash.write(context, args, progress)
    return {'target': write['target'], 'sha256': done['sha256'], 'kind': checked['image'].get('kind', ''),
            'warnings': checked.get('warnings', [])}


def protected_state(context, volume, token, write, progress):
    """For a partition the bootloader locks while the phone runs (dtbo, vbmeta): 'same' when it
    already holds this image, 'verification-off' when it is this vbmeta with verification switched
    off (flashed in Download mode for root: kept), else 'differs' (only Download mode can write it)."""
    args = types.SimpleNamespace(target=write['target'], volume=volume, volume_token=token, path=write['path'],
                                 member=write['member'], image_bytes=None, confirm=True)
    image = flash.check(context, args, progress)['image']['sha256']
    target = context.target(write['target'])
    now = install.target_bytes(context, target)  # 2026-11-13 review: a short read is refused
    if hashlib.sha256(now).hexdigest() == image:
        return 'same'
    if write['target'] == 'vbmeta' and now[:4] == b'AVB0' and struct.unpack_from('>I', now, 120)[0] & 2:
        stock = bytearray(now)
        struct.pack_into('>I', stock, 120, 0)
        if hashlib.sha256(bytes(stock)).hexdigest() == image:
            return 'verification-off'
    return 'differs'


@contextlib.contextmanager
def held_lock(context):
    """The recovery's operation lock for the whole installation; helpers inside inherit it."""
    if context.mode != 'device':
        yield
        return
    with operation_lock(context.lock_path, context.mode) as fd:
        before = os.environ.get(ENV_FD)
        os.environ[ENV_FD] = str(fd)
        try:
            yield
        finally:
            if before is None:
                os.environ.pop(ENV_FD, None)
            else:
                os.environ[ENV_FD] = before


def install_firmware(context, volume, token, path, expected_plan, confirmed, progress):
    require(confirmed, 'CONFIRMATION_REQUIRED', 'Installing firmware must be confirmed')
    with held_lock(context):
        plan = inspect(context, volume, token, path)
        require(plan['plan_sha256'] == expected_plan, 'PLAN_CHANGED',
                'The firmware files changed since they were reviewed. Review them again')
        require(not plan['blockers'], 'PLAN_BLOCKED', 'This firmware cannot be installed', blockers=plan['blockers'])
        token = context.fs.volume(volume)['volume_token']
        checked = [item['name'] for item in plan['files'] if verify_md5(context, volume, token, item, progress)]
        # 2026-11-10: dtbo and vbmeta are locked by the bootloader while the phone runs. Checked before
        # anything is written: the same image (or vbmeta with verification off) stays; another one
        # stops the installation here, untouched, for Odin.
        protected = flash.write_protected(context)
        kept = {}
        for write in plan['writes']:
            if write['target'] in protected:
                kept[write['target']] = protected_state(context, volume, token, write, progress)
                require(kept[write['target']] != 'differs', 'PROTECTED_DIFFERS',
                        "This firmware's dtbo or vbmeta differ from the phone's, and the bootloader locks them here: "
                        'flash the AP in Download mode (Odin)', partition=write['target'])
        # The BOOT there (a Linux kernel, or a custom one) is kept on cache before stock BOOT replaces it.
        saved = saved_boot.keep_current_boot(context, progress)['kept']
        written = [flash_one(context, volume, token, write, progress) for write in plan['writes'] if write['target'] not in kept]
        written += [{'target': label, 'sha256': '', 'kind': 'kept', 'kept': state} for label, state in kept.items()]
        reset = android_data(context, progress)
        return {'build': plan['build'], 'md5_checked': checked, 'written': written, 'saved_boot': saved, **reset,
                'next': 'Restart into Android. The first start takes several minutes.'}


def android_data(context, progress):
    """What Samsung's recovery does after Odin, and TWRP's Format Data: cache wiped but for the
    recovery's folder, userdata formatted for Android (f2fs as Samsung's), metadata's start erased."""
    progress('Wiping', {'label': 'cache', 'size_bytes': 1}, 0)
    wiped = fs.wipe_contents(context.fs, 'cache', confirmed=True)
    progress('Formatting', {'label': 'userdata', 'size_bytes': 1}, 0)
    userdata = context.fs.volume('userdata')
    if userdata['mounted']:
        try:
            context.fs.unmount('userdata', userdata['volume_token'])
        except fs.FilesystemError as error:
            fail('USERDATA_BUSY', 'userdata is still in use, so it was not formatted. Close what uses it, then use Wipe, '
                 'Data for Android', reason=error.code)
    # A fresh context reads the mounts as they are now: the one from the start of the installation
    # still saw userdata mounted (2026-11-10, on the phone: PARTITION_MOUNTED after every image).
    context.fresh().reset_android()
    progress('Formatting', {'label': 'userdata', 'size_bytes': 1}, 1)
    # 2026-11-10: Android's own request for this (boot-recovery, --prompt_and_wipe_data in misc) is done.
    import bcb
    try:
        cleared = bcb.clear_wipe_request(context)
    except OSError:
        cleared = False
    # 2026-11-13 review: the vbmeta prepared for root, now in its partition, asks for this very reset
    # until its copy goes; left there, root asked to erase the freshly set up data once more.
    import root
    import saved_boot
    with contextlib.suppress(OSError, fs.FilesystemError):
        prepared = root.prepared_in_place(context)
        if prepared:
            saved_boot.remove_from_cache(context, prepared)
    os.sync()
    return {'cache_removed': wiped['removed'], 'userdata': 'android', 'metadata': 'erased', 'request_cleared': cleared}


def reset_data(context, confirmed, progress):
    """Wipe, Data for Android: only the reset, for a GSI or an Android that lost its data."""
    require(confirmed, 'CONFIRMATION_REQUIRED', 'Formatting data must be confirmed')
    with held_lock(context):
        return android_data(context, progress)


def parser():
    result = argparse.ArgumentParser()
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    unpack = commands.add_parser('unpack', help="Samsung's firmware ZIP unpacked beside it, checked by its CRC")
    unpack.add_argument('--volume', required=True)
    unpack.add_argument('--volume-token', required=True)
    unpack.add_argument('--path', required=True)
    unpack.add_argument('--confirm', action='store_true')
    boot = commands.add_parser('extract-boot', help="Samsung's BOOT out of the firmware (AP or its ZIP), kept on cache")
    boot.add_argument('--volume', required=True)
    boot.add_argument('--volume-token', required=True)
    boot.add_argument('--path', required=True)
    reset = commands.add_parser('reset-data', help="Format userdata and metadata for Android (TWRP's Format Data)")
    reset.add_argument('--confirm', action='store_true')
    for name in ('inspect', 'install'):
        command = commands.add_parser(name)
        command.add_argument('--volume', required=True)
        command.add_argument('--volume-token', required=True)
        command.add_argument('--path', required=True)
    commands.choices['install'].add_argument('--expected-plan', required=True)
    commands.choices['install'].add_argument('--confirm', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    last = [None, 0.0]

    def progress(phase, target, done):
        now = time.monotonic()
        if phase != last[0] or now - last[1] >= 0.2 or done == target['size_bytes']:
            print(json.dumps({'event': 'progress', 'operation': 'samsung-' + args.command, 'phase': phase,
                              'partition': target['label'], 'bytes_done': done, 'bytes_total': target['size_bytes']},
                             separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [phase, now]

    try:
        context = install.FixtureContext(args.fixture) if args.fixture else install.DeviceContext()
        if args.command == 'inspect':
            result = inspect(context, args.volume, args.volume_token, args.path)
        elif args.command == 'unpack':
            result = unpack_zip(context, args.volume, args.volume_token, args.path, args.confirm, progress)
        elif args.command == 'extract-boot':
            result = extract_boot(context, args.volume, args.volume_token, args.path, progress)
        elif args.command == 'reset-data':
            result = reset_data(context, args.confirm, progress)
        else:
            result = install_firmware(context, args.volume, args.volume_token, args.path, args.expected_plan,
                                      args.confirm, progress)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - every refusal reaches the page as JSON (RecoveryError too)
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
