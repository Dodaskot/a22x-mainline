#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""A GSI (Android's generic system image) installed by this recovery (2026-11-10).

A GSI replaces only Android's system: it starts with Samsung's BOOT and vendor, so stock Android
(samsung_firmware.py) comes first. From a GSI ZIP as Google ships it (system.img, vbmeta.img,
build.prop), or one system image (raw, sparse, xz):

  super/system   system.img, decoded straight from the ZIP; system grows when it is larger
  vbmeta         the GSI's vbmeta.img: verification disabled (flags 2), as Google flashes it;
                 an image without one needs vbmeta's verification to be off already
  userdata       formatted for Android, metadata erased, cache wiped (the recovery's folder stays):
                 a GSI never starts on the data of another system

The review uses the plan format of samsung_firmware.py, so the same page shows both.

    android_gsi.py --device|--fixture DIR inspect --volume V --volume-token T --path GSI
    android_gsi.py ... install --volume V --volume-token T --path GSI --expected-plan SHA --confirm
    android_gsi.py ... classify --volume V --volume-token T --path FILE

classify (2026-11-11) says what a file chosen as "a ROM" on a card is, so that the page opens the
review that installs it: a ZIP with its own installer, Samsung's firmware, or a system image.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import sys
import time
import zipfile

import bootimage
import filesystem as fs
import flash
import install
from install import require
import samsung_firmware as sf
import saved_boot
import sevenzip

# The vendor interface levels (FCM) each Android supports; this phone's vendor is level 5
# (distro.VENDOR_FCM_LEVEL). Android 16 dropped level 5.
SUPPORTED_LEVELS = {33: (4, 5, 6, 7), 34: (5, 6, 7, 8), 35: (5, 6, 7, 8, 202404), 36: (6, 7, 8, 202404, 202504),
                    37: (7, 8, 202404, 202504)}
VENDOR_FCM_LEVEL = 5
AVB_VERIFICATION_DISABLED = 2
PROPS = ('ro.build.version.release', 'ro.build.version.sdk', 'ro.build.id', 'ro.build.version.security_patch',
         'ro.system.build.fingerprint', 'ro.product.system.name')


def gsi_source(context, volume, token, path):
    """What the file holds: system.img and vbmeta.img inside a ZIP, or one system image."""
    with flash.source_file(context, volume, token, path) as fd:
        st = os.fstat(fd)
        identity = flash.identity(fd)
        props, members = {}, {}
        if os.pread(fd, 4, 0) == b'PK\x03\x04':
            with flash.shared_offset(fd) as handle, zipfile.ZipFile(handle) as archive:
                infos = {i.filename: i for i in archive.infolist() if not i.is_dir()}
                images = [n for n in infos if n.lower().endswith('.img')]
                system = next((n for n in images if n.rsplit('/', 1)[-1].lower() == 'system.img'), None)
                if system is None and len([n for n in images if 'vbmeta' not in n.lower()]) == 1:
                    system = next(n for n in images if 'vbmeta' not in n.lower())
                require(system, 'NOT_A_GSI', 'The ZIP holds no system image')
                members['system'] = (system, infos[system].file_size)
                vbmeta = next((n for n in images if n.rsplit('/', 1)[-1].lower() == 'vbmeta.img'), None)
                if vbmeta:
                    members['vbmeta'] = (vbmeta, infos[vbmeta].file_size)
                prop = next((n for n in infos if n.rsplit('/', 1)[-1] == 'build.prop' and infos[n].file_size < (1 << 20)), None)
                if prop:
                    for line in archive.read(prop).decode('utf-8', 'replace').splitlines():
                        key, sep, value = line.partition('=')
                        if sep and key.strip() in PROPS:
                            props[key.strip()] = value.strip()[:120]
        elif os.pread(fd, 6, 0) == sevenzip.SIGNATURE:
            # LineageOS's GSIs (2026-11-10): one system image inside a .7z.
            try:
                files, _ = sevenzip.entries(fd)
                system = sevenzip.choose(files)
            except sevenzip.SevenZipError as error:
                fs.fail(error.code, str(error))
            members['system'] = (system['name'], system['size'])
        else:
            compression = flash.container(fd)
            members['system'] = (None, st.st_size if compression == 'none' else (xz_size(fd) if compression == 'xz' else
                                                                            (gz_size(fd) if compression == 'gzip' else 0)))
        return {'identity': identity, 'size': st.st_size, 'members': members, 'props': props}


INSTALLER = 'meta-inf/com/google/android/update-binary'
FIRMWARE_PARTS = ('ap_', 'bl_', 'cp_', 'csc_', 'home_csc_')


def image_prefix(fd, compression, limit=4096):
    """The first bytes of an image as they would go on a partition."""
    largest = 8 << 30
    image_format = flash.layout(fd, compression, largest)
    reader = flash.open_reader(fd, compression, largest)
    prefix = bytearray()
    try:
        for data in install.decode_image(reader, image_format, largest):
            prefix.extend(data)
            if len(prefix) >= limit:
                break
    finally:
        flash.close_reader(reader)
    return bytes(prefix[:limit])


def classify(context, volume, token, path):
    """What the chosen file is, by its content:
    installer  a ZIP with its own update-binary (a custom ROM, Magisk, a kernel): the ZIP page runs it
    samsung    Samsung's firmware: an AP tar, or a ZIP of the AP, BL, CP and CSC files
    gsi        a system image, plain, sparse or compressed, or a ZIP or 7z that holds one
    payload    an OTA for update_engine (payload.bin): made for phones with A/B slots
    unknown    nothing this recovery installs"""
    relative = '/' + fs.clean_path(path).lstrip('/')
    name = relative.rsplit('/', 1)[-1]
    with flash.source_file(context, volume, token, relative) as fd:
        size, head, kind = os.fstat(fd).st_size, os.pread(fd, 8, 0), 'unknown'
        if head[:4] == b'PK\x03\x04':
            try:
                with flash.shared_offset(fd) as handle, zipfile.ZipFile(handle) as archive:
                    names = [i.filename.lower() for i in archive.infolist()[:8192] if not i.is_dir()]
            except (zipfile.BadZipFile, OSError, ValueError):
                names = []
            leaves = [n.rsplit('/', 1)[-1] for n in names]
            if INSTALLER in names:
                kind = 'installer'
            elif any(leaf.startswith(FIRMWARE_PARTS) and '.tar' in leaf for leaf in leaves):
                kind = 'samsung'
            elif 'payload.bin' in leaves:
                kind = 'payload'
            elif any(leaf.endswith('.img') and 'vbmeta' not in leaf for leaf in leaves):
                kind = 'gsi'
        elif head[:6] == sevenzip.SIGNATURE:
            kind = 'gsi'
        elif os.pread(fd, 5, 257) == b'ustar':
            kind = 'samsung'
        else:
            try:
                if flash.image_kind(image_prefix(fd, flash.container(fd))) != 'Raw image':
                    kind = 'gsi'
            except (OSError, ValueError, EOFError, fs.FilesystemError):
                kind = 'unknown'
        return {'kind': kind, 'name': name, 'size': size, 'volume': volume, 'path': relative}


def gz_size(fd):
    """The unpacked size a .gz file states in its last four bytes (LineageOS's .img.gz, 2026-11-11):
    right for one stream below 4 GiB, which a system image is; 0 when the file is too short."""
    try:
        end = os.fstat(fd).st_size
        tail = os.pread(fd, 4, end - 4) if end > 18 else b''
        return struct.unpack('<I', tail)[0] if len(tail) == 4 else 0
    except (OSError, struct.error):
        return 0


def xz_size(fd):
    """The unpacked size of a one-stream .xz file (crDroid's .img.xz) from its index, or 0 when it
    cannot be read that way: then the size is known only once the image is decoded."""
    def varint(data, at):
        value, shift = 0, 0
        while True:
            byte = data[at]
            at += 1
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return value, at
            shift += 7
            if shift > 63:
                raise ValueError('varint')
    try:
        end = os.fstat(fd).st_size
        while end >= 4 and os.pread(fd, 4, end - 4) == bytes(4):  # stream padding
            end -= 4
        footer = os.pread(fd, 12, end - 12)
        if len(footer) != 12 or footer[10:12] != b'YZ':
            return 0
        index_size = (struct.unpack('<I', footer[4:8])[0] + 1) * 4
        index = os.pread(fd, index_size, end - 12 - index_size)
        if len(index) != index_size or index[0] != 0:
            return 0
        records, at = varint(index, 1)
        total, blocks = 0, 0
        for _ in range(records):
            unpadded, at = varint(index, at)
            unpacked, at = varint(index, at)
            total += unpacked
            blocks += (unpadded + 3) // 4 * 4
        # One stream: its header, the blocks and the index fill the file exactly.
        return total if end - 12 - index_size - blocks - 12 == 0 else 0
    except (OSError, ValueError, IndexError, struct.error):
        return 0


# An empty vbmeta with verification disabled, as avbtool make_vbmeta_image --flags 2 makes it:
# GSIs without their own vbmeta.img (LineageOS, crDroid) need verification off to start.
VBMETA_DIR = 'a22x-recovery/vbmeta'
VBMETA_NAME = 'vbmeta-disabled.img'


def disabled_vbmeta():
    header = bytearray(256)
    header[:4] = b'AVB0'
    struct.pack_into('>IIQQIQQQQQQQQQQQII', header, 4, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, AVB_VERIFICATION_DISABLED, 0)
    header[128:128 + 13] = b'avbtool 1.3.0'
    return bytes(header) + bytes(4096 - 256)


def current_boot(context):
    """'android' for Samsung's Android BOOT of this phone, 'linux' for a mainline one, else None."""
    target = context.target('boot')
    fd = context.open_target(target)
    try:
        data = os.pread(fd, target['size_bytes'], 0)
    finally:
        os.close(fd)
    try:
        info = bootimage.inspect_boot(data)
    except bootimage.ImageError:
        return None, ''
    release = info['kernel'].get('release') or ''
    if not info['device'].get('this_phone'):
        return None, release
    return ('linux' if info.get('mainline') else 'android'), release


def vbmeta_flags(context):
    target = context.target('vbmeta')
    fd = context.open_target(target)
    try:
        head = os.pread(fd, 256, 0)
    finally:
        os.close(fd)
    return struct.unpack_from('>I', head, 120)[0] if head[:4] == b'AVB0' else None


KERNEL_FLOOR_SDK, KERNEL_FLOOR = 35, (4, 19)
# One literal on one line: the interface's list of texts is gathered from the sources line by line.
OLD_KERNEL_WARNING = "This is Android 15 or newer. Google's own builds need kernel 4.19 and restart for ever on this phone's Android kernel 4.14; ROMs made for older phones (Treble GSIs) start."


def kernel_older(release, floor):
    """Whether a kernel release ("4.14.186-27218697") is older than FLOOR; one that is not known
    counts as older: this phone's Android kernel is 4.14 whatever the firmware."""
    match = re.match(r'(\d+)\.(\d+)', release or '')
    return not match or (int(match.group(1)), int(match.group(2))) < floor


def inspect(context, volume, token, path):
    relative = '/' + fs.clean_path(path).lstrip('/')
    name = relative.rsplit('/', 1)[-1]
    source = gsi_source(context, volume, token, relative)
    props = source['props']
    warnings, blockers, rows, writes = [], [], [], []
    boot, release = current_boot(context)
    stock = None
    if boot != 'android':
        # 2026-11-10: with Linux (or nothing usable) on BOOT, Samsung's BOOT kept on cache (taken out of
        # the firmware, or saved by the app or a flash) goes on BOOT too; the Linux BOOT is kept then.
        try:
            stock = next((b for b in saved_boot.saved_boots(context) if b['kind'] == 'android'), None)
        except fs.FilesystemError:
            stock = None
        if stock is None:
            blockers.append('STOCK_ANDROID_FIRST')
    sdk = int(props.get('ro.build.version.sdk', '0') or 0) if props.get('ro.build.version.sdk', '').isdigit() else 0
    fits = VENDOR_FCM_LEVEL in SUPPORTED_LEVELS.get(sdk, ())
    if sdk and not fits:
        warnings.append('Made for newer vendors: it may not start on this phone')
    if not sdk:
        warnings.append('The image does not say its Android version')
    # 2026-11-12, on the owner's phone: Google's Android 15 restarted for ever. Its network loader
    # refuses a kernel older than 4.19 ("NetBpfLoad: Android V requires kernel 4.19"), and this
    # phone's Android kernel is 4.14. ROMs made for older phones take that check out (crDroid and
    # AlphaDroid, both Android 16, started here), and a file does not say which kind it is: so
    # this is said, and never refused.
    if sdk >= KERNEL_FLOOR_SDK and kernel_older(release if boot == 'android' else (stock or {}).get('release', ''), KERNEL_FLOOR):
        warnings.append(OLD_KERNEL_WARNING)
    system_name, system_bytes = source['members']['system']
    targets = {t['label']: t for t in flash.targets(context)['targets']}
    room = flash.targets(context).get('super_free_bytes', 0)
    current = targets.get('super/system', {}).get('size_bytes', 0)
    if system_bytes and system_bytes > current + room:
        blockers.append('NO_ROOM_IN_SUPER')
    rows.append({'file': 'GSI', 'name': system_name or name, 'size': system_bytes, 'action': 'write', 'target': 'super/system',
                 'reason': None})
    writes.append({'target': 'super/system', 'path': relative, 'member': system_name, 'size': system_bytes})
    flags = vbmeta_flags(context)
    if 'vbmeta' in flash.write_protected(context):
        # 2026-11-10: LK write-protects vbmeta while the phone runs. Verification already off (the
        # vbmeta Root prepares, or one flashed in Download mode): kept. Otherwise it is flashed
        # in Download mode first - Root on the Android page prepares it.
        if flags is not None and flags & AVB_VERIFICATION_DISABLED:
            rows.append({'file': 'GSI', 'name': 'vbmeta', 'size': 0, 'action': 'kept', 'target': None,
                         'reason': 'Verification is already disabled'})
        else:
            blockers.append('VBMETA_IN_DOWNLOAD_MODE')
    elif 'vbmeta' in source['members']:
        vb_name, vb_bytes = source['members']['vbmeta']
        rows.append({'file': 'GSI', 'name': vb_name, 'size': vb_bytes, 'action': 'write', 'target': 'vbmeta', 'reason': None})
        writes.append({'target': 'vbmeta', 'path': relative, 'member': vb_name, 'size': vb_bytes})
    else:
        if flags is None or not flags & AVB_VERIFICATION_DISABLED:
            # No vbmeta of its own: an empty one with verification disabled goes there instead.
            data = disabled_vbmeta()
            rows.append({'file': 'RECOVERY', 'name': VBMETA_NAME, 'size': len(data), 'action': 'write', 'target': 'vbmeta', 'reason': None})
            writes.append({'target': 'vbmeta', 'volume': 'cache', 'path': '/' + VBMETA_DIR + '/' + VBMETA_NAME, 'member': None,
                           'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'generate': 'vbmeta-disabled'})
        else:
            rows.append({'file': 'GSI', 'name': 'vbmeta', 'size': 0, 'action': 'kept', 'target': None,
                         'reason': 'Verification is already disabled'})
    if stock is not None:
        rows.append({'file': 'CACHE', 'name': stock['name'], 'size': stock['bytes'], 'action': 'write', 'target': 'boot', 'reason': None})
        writes.append({'target': 'boot', 'volume': 'cache', 'path': stock['path'], 'member': None, 'size': stock['bytes'],
                       'sha256': stock['sha256']})
    else:
        rows.append({'file': 'PHONE', 'name': 'boot', 'size': 0, 'action': 'kept', 'target': None,
                     'reason': "Samsung's Android BOOT stays" if boot == 'android' else 'Not Android: flash the Samsung firmware first'})
    plan = {'file': {'path': relative, 'identity': source['identity'], 'size': source['size']},
            'writes': [{k: w.get(k) for k in ('target', 'member', 'size', 'volume', 'path', 'sha256')} for w in writes],
            'erase': ['userdata', 'metadata', 'cache']}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    build = props.get('ro.build.id', '')
    return {'volume': volume, 'path': relative, 'build': build, 'android': props.get('ro.build.version.release', ''), 'sdk': sdk,
            'patch': props.get('ro.build.version.security_patch', ''), 'fits': fits, 'boot': boot or 'unknown', 'boot_release': release,
            'files': [{'name': name, 'path': relative, 'kind': 'GSI', 'build': build, 'size': source['size'], 'md5': None}],
            'rows': rows, 'writes': writes, 'erase': plan['erase'], 'warnings': warnings, 'blockers': sorted(set(blockers)),
            'plan_sha256': digest, 'write_bytes': sum(w['size'] for w in writes)}


def flash_gsi(context, volume, token, write, progress):
    args = type('Args', (), {})()
    if write.get('volume') and write['volume'] != volume:
        # Samsung's BOOT kept on cache: its own volume, mounted read-only by saved_boots.
        volume, token = write['volume'], context.fs.volume(write['volume'])['volume_token']
    args.target, args.volume, args.volume_token, args.path, args.member = write['target'], volume, token, write['path'], write['member']
    args.image_bytes, args.confirm = None, True
    if write.get('sha256'):
        # A file the plan named by its content (Samsung's BOOT or the empty vbmeta on cache).
        with flash.source_file(context, volume, token, write['path']) as fd:
            digest, at = hashlib.sha256(), 0
            while True:
                data = os.pread(fd, 1 << 20, at)
                if not data:
                    break
                digest.update(data)
                at += len(data)
        require(digest.hexdigest() == write['sha256'], 'IMAGE_CHANGED', 'The image kept on cache changed. Review again')
    checked = flash.check(context, args, progress)
    args.expected_sha256, args.expected_identity = checked['image']['sha256'], checked['image']['identity']
    if write['target'].startswith('super/'):
        args.image_bytes = checked['image']['payload_bytes']
    done = flash.write(context, args, progress)
    return {'target': write['target'], 'sha256': done['sha256'], 'kept_boot': done.get('kept_boot')}


def install_gsi(context, volume, token, path, expected_plan, confirmed, progress):
    require(confirmed, 'CONFIRMATION_REQUIRED', 'Installing a GSI must be confirmed')
    with sf.held_lock(context):
        plan = inspect(context, volume, token, path)
        require(plan['plan_sha256'] == expected_plan, 'PLAN_CHANGED', 'The image changed since it was reviewed. Review it again')
        require(not plan['blockers'], 'PLAN_BLOCKED', 'This image cannot be installed', blockers=plan['blockers'])
        token = context.fs.volume(volume)['volume_token']
        if any(w.get('generate') == 'vbmeta-disabled' for w in plan['writes']):
            saved_boot.save_on_cache(context, VBMETA_NAME, disabled_vbmeta(), {'kind': 'vbmeta', 'flags': AVB_VERIFICATION_DISABLED},
                                     progress, keep_mounted=True, folder_name=VBMETA_DIR)
        written = [flash_gsi(context, volume, token, write, progress) for write in plan['writes']]
        reset = sf.android_data(context, progress)
        return {'build': plan['build'], 'android': plan['android'], 'written': written, **reset, 'saved_boot': None,
                'next': 'Restart into Android. The first start takes several minutes.'}


def parser():
    result = argparse.ArgumentParser()
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'install', 'classify'):
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
            print(json.dumps({'event': 'progress', 'operation': 'gsi-' + args.command, 'phase': phase, 'partition': target['label'],
                              'bytes_done': done, 'bytes_total': target['size_bytes']}, separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [phase, now]

    try:
        context = install.FixtureContext(args.fixture) if args.fixture else install.DeviceContext()
        if args.command == 'inspect':
            result = inspect(context, args.volume, args.volume_token, args.path)
        elif args.command == 'classify':
            result = classify(context, args.volume, args.volume_token, args.path)
        else:
            result = install_gsi(context, args.volume, args.volume_token, args.path, args.expected_plan, args.confirm, progress)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - every refusal reaches the page as JSON
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
