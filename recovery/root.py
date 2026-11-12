#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Root for Android from this recovery (2026-11-10): the newest Magisk, downloaded and put into BOOT.

The newest release of Magisk is asked from GitHub, which states its APK's SHA-256; the APK is
downloaded over Wi-Fi and checked. BOOT is patched the way the Magisk app patches it: by Magisk's
own boot_patch.sh with its magiskboot, magiskinit, magisk and init-ld, run by its busybox in a
private tmpfs, as in a recovery (BOOTMODE=false: nothing of Android is asked; its abort only reports,
it unmounts nothing here). The patched BOOT is checked (still Android's BOOT for this phone, Magisk in
its ramdisk), kept on cache, and written as any BOOT: the BOOT there is kept on cache first, so root
goes away by flashing it back. Magisk 31 puts no app into BOOT: the Magisk app is installed in Android.

Samsung (2026-11-10, on the phone): with the stock vbmeta (verification on, a hash for boot), a
patched BOOT starts, but Android cannot mount its encrypted data (misc: --prompt_and_wipe_data,
--reason=fs_mgr_mount_all) - the changed boot hash changes the verified-boot state its keys depend
on. The Magisk app's Samsung method is the same here: vbmeta flags 3 (hashtree and verification off,
its TarProcessor's putInt(120, 3)) and Android's data erased once. After that a new BOOT (a Magisk
update) needs no reset. LK write-protects vbmeta while the phone runs ([Part_WP]WP(nvram->btd), seen
on the phone: the write was dropped), so this recovery prepares that vbmeta on cache and it is flashed
in Download mode (Heimdall or Odin); seeing it there, the next install erases Android's data once,
and the prepared copy is removed, so later installs need no reset.

KernelSU and KernelSU Next are not offered: their modules load only into GKI kernels (5.10 and
newer), and this phone's Android kernel is 4.14 (2026-11-10, at the user's word: they do not install).

APatch (2026-11-11, asked for by the phone's owner) puts KernelPatch into the kernel itself, which
the 4.14 kernel allows (CONFIG_KALLSYMS and CONFIG_KALLSYMS_ALL are on). BOOT is patched by the
steps of the APatch app's own boot_patch.sh: kptools unpack, the kernel patched with KernelPatch's
image and the owner's SuperKey, kptools repack. The APK's kptools needs Android's linker, so this
recovery carries kptools built from KernelPatch's source at the version of the APK's kpimg, and
that kpimg: nothing is downloaded. The SuperKey is typed on the phone, reaches this helper in a
file of the private RAM folder, and is kept nowhere; the APatch app in Android asks for it.

    root.py --device|--fixture DIR list
    root.py ... install --id magisk --expected-sha256 SHA --confirm
    root.py ... install --id apatch --expected-sha256 SHA --superkey-file FILE --confirm
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import types
import urllib.request
import zipfile

import bootimage
import clock
import filesystem as fs
import flash
import install
from install import require
import saved_boot

TOOLS = {
    'magisk': {'name': 'Magisk', 'repo': 'topjohnwu/Magisk', 'asset': re.compile(r'^Magisk-v[0-9.]+\.apk$'), 'supported': True},
    'apatch': {'name': 'APatch', 'bundled': True, 'supported': True},
}
# KernelPatch's tool and image, part of this recovery (tools/build-image.sh takes them from apatch/out).
APATCH_DIR = Path('/opt/a22x-recovery/apatch')
# The APatch app's rule for a SuperKey: 8 to 63 characters with a letter and a digit. Here also
# without spaces: it is typed again in Android.
SUPERKEY = re.compile(r'(?=.*[A-Za-z])(?=.*[0-9])[!-~]{8,63}')
# What boot_patch.sh needs beside itself, from the APK: (path in the APK, name in the folder).
MAGISK_FILES = (('lib/arm64-v8a/libmagiskboot.so', 'magiskboot'), ('lib/arm64-v8a/libmagiskinit.so', 'magiskinit'),
                ('lib/arm64-v8a/libmagisk.so', 'magisk'), ('lib/arm64-v8a/libinit-ld.so', 'init-ld'),
                ('lib/arm64-v8a/libbusybox.so', 'busybox'), ('assets/boot_patch.sh', 'boot_patch.sh'),
                ('assets/util_functions.sh', 'util_functions.sh'))
APK_LIMIT = 64 << 20
ROOT_DIR = 'a22x-recovery/root'
WORK = Path('/run/a22x-recovery/root-work')
API = 'https://api.github.com/repos/%s/releases/latest'
PATCH_SECONDS = 300
WORK_BYTES = 384 << 20
# boot_patch.sh sourced as Magisk's recovery installer sources it, with its abort reduced to a report:
# Magisk's recovery_cleanup unmounts /system, /vendor, /metadata and more, which this recovery owns.
DRIVER = '''. ./util_functions.sh
recovery_cleanup() { :; }
abort() { ui_print "$1"; exit 1; }
SOURCEDMODE=true
set -- boot.img
. ./boot_patch.sh
'''


def fail(code, message, **details):
    fs.fail(code, message, **details)


def latest(repo, asset_name):
    """The newest release's asset: name, URL, size and the SHA-256 GitHub states for it."""
    request = urllib.request.Request(API % repo, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'a22x-recovery'})
    with urllib.request.urlopen(request, timeout=30, context=tls_context()) as reply:
        release = json.loads(reply.read(4 << 20))
    for asset in release.get('assets', []):
        digest = (asset.get('digest') or '').removeprefix('sha256:')
        if asset_name.match(asset.get('name', '')) and re.fullmatch(r'[0-9a-f]{64}', digest):
            return {'version': release.get('tag_name', ''), 'file': asset['name'], 'url': asset['browser_download_url'],
                    'size': int(asset['size']), 'sha256': digest, 'published': (release.get('published_at') or '')[:10]}
    fail('ROOT_RELEASE_UNUSABLE', 'The newest release has no file this recovery can use')


def tls_context():
    import distro
    return distro.tls_context()


def apatch_bundle(context):
    """KernelPatch's kptools and kpimg that come with this recovery: their paths, the version and
    SHA-256 of kpimg; None when this build has none."""
    folder = context.root / 'apatch' if context.mode == 'fixture' else APATCH_DIR
    tool, image = folder / 'kptools', folder / 'kpimg'
    try:
        data = image.read_bytes()
        usable = tool.is_file() and os.access(tool, os.X_OK)
    except OSError:
        return None
    version = bootimage.kernelpatch_version(data[:64])
    if not usable or not version:
        return None
    return {'kptools': tool, 'kpimg': image, 'version': version, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def tools(context):
    """The root tools with their newest version; without network, the names and why they need it."""
    # Certificates need the date: on the phone, a clock that cannot be right asks a time server first.
    clock.ensure(context.mode)
    found = []
    for ident, tool in TOOLS.items():
        row = {'id': ident, 'name': tool['name'], 'supported': tool['supported'], 'reason': tool.get('reason', '')}
        if tool.get('bundled'):
            bundle = apatch_bundle(context)
            if bundle:
                row.update({'version': "KernelPatch " + bundle['version'], 'file': 'kpimg', 'size': bundle['size'],
                            'sha256': bundle['sha256'], 'bundled': True, 'needs_key': True})
            else:
                row.update({'supported': False, 'reason': 'This build of the recovery has no APatch'})
        elif context.mode == 'fixture':
            row.update((context.config.get('root') or {}).get(ident, {}))
        else:
            try:
                row.update(latest(tool['repo'], tool['asset']))
            except (OSError, ValueError, fs.FilesystemError) as error:
                row['error'] = getattr(error, 'code', None) or 'NETWORK_UNAVAILABLE'
        found.append(row)
    try:
        stock = next((b for b in saved_boot.saved_boots(context) if b['kind'] == 'android' and not b['root']), None)
    except fs.FilesystemError:
        stock = None
    cache = context.fs.volume('cache')
    return {'tools': found, 'boot': boot_state(context), 'stock_boot': stock['path'] if stock else '',
            'cache_token': cache['volume_token'] if stock else '', 'verification': verification_on(context),
            'needs_data_reset': bool(prepared_in_place(context))}


def vbmeta_image(context):
    target = context.target('vbmeta')
    fd = context.open_target(target)
    try:
        return target, os.pread(fd, target['size_bytes'], 0)
    finally:
        os.close(fd)


def verification_on(context):
    """Whether vbmeta still has Android's verification on (flags without bit 2): a patched BOOT then
    changes the verified-boot state, and Android's data must be erased once."""
    try:
        _, data = vbmeta_image(context)
    except (OSError, KeyError, fs.FilesystemError):
        return False
    return data[:4] == b'AVB0' and not struct.unpack_from('>I', data, 120)[0] & 2


def boot_state(context):
    target = context.target('boot')
    fd = context.open_target(target)
    try:
        data = os.pread(fd, target['size_bytes'], 0)
    finally:
        os.close(fd)
    found = saved_boot.describe_boot(data)
    return {'kind': found['kind'] or 'unknown', 'release': found['release'], 'magisk': found['root'] == 'magisk',
            'root': found['root']}


def download(context, url, size, sha256, target, progress):
    """The APK into TARGET, its size and SHA-256 as GitHub states them."""
    if context.mode == 'fixture':
        source = context.root / 'root-downloads' / url.rsplit('/', 1)[-1]
        shutil.copyfile(source, target)
    else:
        request = urllib.request.Request(url, headers={'User-Agent': 'a22x-recovery'})
        with urllib.request.urlopen(request, timeout=60, context=tls_context()) as reply, open(target, 'wb') as out:
            done = 0
            while True:
                data = reply.read(1 << 20)
                if not data:
                    break
                done += len(data)
                require(done <= APK_LIMIT, 'DOWNLOAD_TOO_LARGE', 'The download is larger than its release says')
                out.write(data)
                progress('Downloading', {'label': 'magisk', 'size_bytes': size}, done)
    digest = hashlib.sha256(Path(target).read_bytes()).hexdigest()
    require(os.path.getsize(target) == size and digest == sha256, 'DOWNLOAD_CHECKSUM_MISMATCH',
            'The download differs from its release. Try again.')


def patch_magisk(apk, boot, work):
    """BOOT patched by Magisk's own boot_patch.sh, in WORK; the new image's bytes and the log."""
    with zipfile.ZipFile(apk) as archive:
        for member, name in MAGISK_FILES:
            try:
                data = archive.read(member)
            except KeyError:
                fail('MAGISK_APK_INCOMPLETE', 'This Magisk APK lacks a file the patch needs', file=member)
            (work / name).write_bytes(data)
            (work / name).chmod(0o755)
    (work / 'boot.img').write_bytes(boot)
    (work / 'a22x-patch.sh').write_text(DRIVER)
    # PREINITDEVICE: what the Magisk app's own patch chose on this phone (magisk --preinit-device: cache,
    # seen in the .backup/.magisk of a BOOT it patched); outside Android the patch cannot ask.
    env = {'PATH': str(work) + ':/bin:/sbin:/usr/bin', 'ASH_STANDALONE': '1', 'BOOTMODE': 'false', 'OUTFD': '1',
           'KEEPVERITY': 'true', 'KEEPFORCEENCRYPT': 'true', 'PATCHVBMETAFLAG': 'false', 'RECOVERYMODE': 'false',
           'LEGACYSAR': 'false', 'PREINITDEVICE': 'cache', 'HOME': str(work), 'TMPDIR': str(work)}
    done = subprocess.run([str(work / 'busybox'), 'sh', 'a22x-patch.sh'], cwd=work, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=PATCH_SECONDS)
    log = done.stdout.decode('utf-8', 'replace')
    lines = [line[len('ui_print '):] for line in log.splitlines() if line.startswith('ui_print ') and line[len('ui_print '):].strip()]
    require(done.returncode == 0 and (work / 'new-boot.img').is_file(), 'MAGISK_PATCH_FAILED', 'Magisk could not patch BOOT',
            log=lines[-8:] or log[-600:])
    return (work / 'new-boot.img').read_bytes(), lines


def patch_apatch(context, bundle, boot, work, superkey):
    """BOOT patched as the APatch app's boot_patch.sh patches it, in WORK; the new image's bytes and
    the log. kptools runs without privileges on the phone: it needs nothing but the files in WORK."""
    shutil.copyfile(bundle['kptools'], work / 'kptools')
    (work / 'kptools').chmod(0o755)
    shutil.copyfile(bundle['kpimg'], work / 'kpimg')
    (work / 'boot.img').write_bytes(boot)
    identity = {}
    if context.mode == 'device':
        for path in (work, work / 'kptools', work / 'kpimg', work / 'boot.img'):
            os.chown(path, 65534, 65534)
        identity = {'user': 65534, 'group': 65534, 'extra_groups': []}
    log = []

    def run(*args, need=True):
        # By its name in WORK: without privileges the folders above WORK cannot be passed (2026-11-11, on the phone).
        done = subprocess.run(['./kptools', *args], cwd=work, env={'PATH': '/bin:/usr/bin', 'HOME': '.'},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=PATCH_SECONDS,
                              **identity)
        text = done.stdout.decode('utf-8', 'replace')
        # The SuperKey is an argument of one step: it never reaches a log.
        log.extend(line.replace(superkey, '***') for line in text.splitlines() if line.strip())
        require(done.returncode == 0 or not need, 'APATCH_PATCH_FAILED', 'The kernel in BOOT could not be patched for APatch', step=args[0],
                log=[line.replace(superkey, '***') for line in text.splitlines()[-6:]])
        return text

    run('unpack', 'boot.img')
    require((work / 'kernel').is_file(), 'APATCH_PATCH_FAILED', 'No kernel was found in BOOT')
    require('CONFIG_KALLSYMS=y' in run('-i', 'kernel', '-f', need=False), 'APATCH_KERNEL_UNSUITABLE',
            'This kernel was built without CONFIG_KALLSYMS, which APatch needs')
    os.rename(work / 'kernel', work / 'kernel.ori')
    run('-p', '-i', 'kernel.ori', '-S', superkey, '-k', 'kpimg', '-o', 'kernel')
    run('repack', 'boot.img')
    require((work / 'new-boot.img').is_file(), 'APATCH_PATCH_FAILED', 'The patched BOOT was not written')
    return (work / 'new-boot.img').read_bytes(), log


VBMETA_DIR = 'a22x-recovery/vbmeta'


def prepared_in_place(context):
    """The vbmeta this recovery prepared for root (on cache), when it is now in the vbmeta partition:
    Android's data then still has to be erased once. Its path, or ''."""
    try:
        _, data = vbmeta_image(context)
    except (OSError, KeyError, fs.FilesystemError):
        return ''
    name = 'vbmeta-flags3-' + hashlib.sha256(data).hexdigest()[:12] + '.img'
    try:
        kept = {b['name'] for b in saved_boot.saved_boots(context, VBMETA_DIR)}
    except fs.FilesystemError:
        return ''
    return '/' + VBMETA_DIR + '/' + name if name in kept else ''


def prepare_vbmeta(context, progress):
    """vbmeta as it is with flags 3 (as the Magisk app writes it for Samsung), kept on cache for
    Download mode, since the bootloader locks the partition while the phone runs."""
    target, data = vbmeta_image(context)
    require(data[:4] == b'AVB0', 'VBMETA_INVALID', 'There is no AVB vbmeta in the vbmeta partition')
    patched = bytearray(data)
    struct.pack_into('>I', patched, 120, 3)
    digest = hashlib.sha256(bytes(patched)).hexdigest()
    path = saved_boot.save_on_cache(context, 'vbmeta-flags3-' + digest[:12] + '.img', bytes(patched),
                                    {'kind': 'vbmeta', 'flags': 3, 'for': 'root'}, progress, folder_name=VBMETA_DIR)
    return {'vbmeta': path, 'sha256': digest, 'bytes': len(patched),
            'command': 'heimdall flash --vbmeta ' + path.rsplit('/', 1)[-1]}


def install_root(context, ident, expected_sha256, confirmed, progress, erase_data=False, superkey=None):
    require(confirmed, 'CONFIRMATION_REQUIRED', 'Installing root must be confirmed')
    require(not verification_on(context), 'ROOT_NEEDS_VBMETA',
            'Root on this Samsung needs vbmeta with verification off, flashed in Download mode: prepare it first')
    prepared = prepared_in_place(context)
    reset = bool(prepared)
    require(not reset or erase_data, 'ROOT_NEEDS_DATA_RESET',
            'On this Samsung, root needs verification off in vbmeta and Android data erased once')
    tool = TOOLS.get(ident)
    require(tool is not None, 'ROOT_UNKNOWN', 'This root tool is unknown')
    require(tool['supported'], 'ROOT_UNSUPPORTED', tool.get('reason', 'Not for this phone'))
    state = boot_state(context)
    require(state['kind'] == 'android', 'ROOT_NEEDS_ANDROID_BOOT', 'Root goes into the BOOT of Android: install Android first')
    # Magisk lives in the ramdisk and APatch in the kernel: one BOOT carries one of them.
    require(state['root'] in ('', ident), 'ROOT_OTHER_PRESENT',
            'This BOOT already has another root: flash the stock BOOT back first (Remove root)', present=state['root'])
    if tool.get('bundled'):
        require(isinstance(superkey, str) and SUPERKEY.fullmatch(superkey), 'SUPERKEY_INVALID',
                'The SuperKey needs 8 to 63 characters without spaces, with a letter and a digit')
    release = next(t for t in tools(context)['tools'] if t['id'] == ident)
    require(release.get('supported'), 'ROOT_UNSUPPORTED', release.get('reason') or 'Not for this phone')
    require(release.get('sha256') == expected_sha256, 'ROOT_RELEASE_CHANGED', 'A newer release came out since it was shown. Review again')
    work = WORK if context.mode == 'device' else context.root / 'root-work'
    with work_folder(context, work):
        result = patch_and_flash(context, ident, tool, release, work, progress, superkey)
    if reset:
        import samsung_firmware
        result['data'] = samsung_firmware.reset_data(context, True, progress)
        # The reset that verification off needed is done: later installs need none.
        with contextlib.suppress(fs.FilesystemError, OSError):
            saved_boot.remove_from_cache(context, prepared)
    return result


@contextlib.contextmanager
def work_folder(context, work):
    """A private folder for the patch; on the phone a tmpfs of its own, which allows running Magisk's tools."""
    if context.mode == 'device' and os.path.ismount(work):
        subprocess.run([fs.BUSYBOX, 'umount', str(work)], capture_output=True, timeout=30)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, mode=0o700)
    mounted = False
    try:
        if context.mode == 'device':
            done = subprocess.run([fs.BUSYBOX, 'mount', '-t', 'tmpfs', '-o', 'mode=0700,nosuid,nodev,size=%d' % WORK_BYTES, 'a22x-root',
                                   str(work)], capture_output=True, text=True, timeout=30)
            require(done.returncode == 0, 'ROOT_WORK_UNAVAILABLE', 'No room in RAM for the patch', detail=done.stderr.strip()[:300])
            mounted = True
        yield work
    finally:
        if mounted:
            subprocess.run([fs.BUSYBOX, 'umount', str(work)], capture_output=True, timeout=30)
        shutil.rmtree(work, ignore_errors=True)


def patch_and_flash(context, ident, tool, release, work, progress, superkey=None):
    bundle = apatch_bundle(context) if tool.get('bundled') else None
    if not bundle:
        apk = work / release['file']
        download(context, release['url'], release['size'], release['sha256'], apk, progress)
    target = context.target('boot')
    fd = context.open_target(target)
    try:
        boot = os.pread(fd, target['size_bytes'], 0)
    finally:
        os.close(fd)
    # Patched as the image itself, without the mark that keeps this recovery: flashing puts it back.
    # With the mark in the tail kptools found no unused tail and repacked a BOOT larger than its
    # partition (2026-11-11, on the phone).
    marked = saved_boot.keep_recovery_spot(boot)
    if marked and marked[1]:
        boot = boot[:marked[0]] + bytes(len(saved_boot.KEEP_RECOVERY_MARK)) + boot[marked[0] + len(saved_boot.KEEP_RECOVERY_MARK):]
    progress('Patching', {'label': 'boot', 'size_bytes': 1}, 0)
    failed = 'APATCH_PATCH_FAILED' if bundle else 'MAGISK_PATCH_FAILED'
    if bundle:
        patched, log = patch_apatch(context, bundle, boot, work, superkey)
    elif context.mode == 'fixture':
        patched, log = fixture_patch(context, boot, work)
    else:
        patched, log = patch_magisk(apk, boot, work)
    try:
        info = bootimage.inspect_boot(patched)
    except bootimage.ImageError as error:
        fail(failed, 'The patched BOOT is not a complete boot image', reason=str(error))
    require(info['device'].get('this_phone') and not info.get('mainline'), failed, 'The patched BOOT is not for this phone')
    require(info.get('root') == ident, failed, 'The patched BOOT has no APatch in it' if bundle else 'Magisk is not in the patched BOOT')
    require(len(patched) <= target['size_bytes'], failed, 'The patched BOOT does not fit its partition')
    digest = hashlib.sha256(patched).hexdigest()
    name = ident + '-' + re.sub(r'[^A-Za-z0-9._+-]', '_', release['version'])[:24] + '-' + digest[:12] + '.img'
    path = saved_boot.save_on_cache(context, name, patched, {'kind': 'android', 'root': ident, 'version': release['version'],
                                                              'release': info['kernel'].get('release', '')},
                                    progress, keep_mounted=True, folder_name=ROOT_DIR)
    cache = context.fs.volume('cache')
    args = types.SimpleNamespace(target='boot', volume='cache', volume_token=cache['volume_token'], path=path, member=None,
                                 image_bytes=None, confirm=True)
    checked = flash.check(context, args, progress)
    args.expected_sha256, args.expected_identity = checked['image']['sha256'], checked['image']['identity']
    written = flash.write(context, args, progress)
    # The patched BOOT is in the partition now and can be made again: cache keeps only the ways back.
    with contextlib.suppress(fs.FilesystemError, OSError):
        saved_boot.remove_from_cache(context, path)
    return {'root': ident, 'name': tool['name'], 'version': release['version'], 'apk': '' if bundle else release['file'], 'boot': path,
            'sha256': written['sha256'], 'kept_boot': written.get('kept_boot'), 'log': log[-12:]}


def fixture_patch(context, boot, work):
    """Tests: a stand-in for boot_patch.sh, a BOOT the test prepared with Magisk's marker in its ramdisk."""
    return (context.root / 'root-downloads/patched-boot.img').read_bytes(), ['- Unpacking boot image', '- Repacking boot image']


def parser():
    result = argparse.ArgumentParser()
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    commands.add_parser('prepare-vbmeta')
    command = commands.add_parser('install')
    command.add_argument('--id', required=True)
    command.add_argument('--expected-sha256', required=True)
    command.add_argument('--confirm', action='store_true')
    command.add_argument('--erase-data', action='store_true')
    command.add_argument('--superkey-file', type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    last = [None, 0.0]

    def progress(phase, target, done):
        now = time.monotonic()
        if phase != last[0] or now - last[1] >= 0.2 or done == target['size_bytes']:
            print(json.dumps({'event': 'progress', 'operation': 'root-' + args.command, 'phase': phase, 'partition': target['label'],
                              'bytes_done': done, 'bytes_total': target['size_bytes']}, separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [phase, now]

    try:
        context = install.FixtureContext(args.fixture) if args.fixture else install.DeviceContext()
        if args.command == 'list':
            result = tools(context)
        elif args.command == 'prepare-vbmeta':
            result = prepare_vbmeta(context, progress)
        else:
            superkey = None
            if args.superkey_file:
                # Typed on the phone and handed over in the private RAM folder: read once, then gone.
                superkey = args.superkey_file.read_text(encoding='utf-8').split('\n', 1)[0]
                args.superkey_file.unlink(missing_ok=True)
            result = install_root(context, args.id, args.expected_sha256, args.confirm, progress, args.erase_data, superkey)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - every refusal reaches the page as JSON
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
