#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Updates of this recovery and of the Linux BOOT, from Settings (2026-11-12, the owner: "a
separate item in Settings to update BOOT and recovery, conveniently, right there").

    updates.py --device|--fixture DIR check
    updates.py --device|--fixture DIR fetch --what recovery|boot --sha256 H

check   what is installed and what is published: the newest release of the recovery on GitHub
        (its image and the SHA-256 GitHub states for it), and the Linux BOOT the catalog names for
        this recovery. BOOT is offered only when it holds a Linux kernel for this phone: an Android
        BOOT stays as it is (Install > Android for that).
fetch   the image, downloaded and checked (size, SHA-256, a complete boot image that names the
        SM-A226B), kept on cache and left there for Flash a partition, which writes it the usual
        way: the old content saved first, the partition read back. Nothing is written here.

The recovery published on GitHub is the build for any phone, without the developer's SSH key.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import types
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootimage
import filesystem as fs
from install import require

REPO = 'Dodaskot/mainline-recovery'  # renamed from a22x-recovery on 2026-11-12; GitHub redirects the old name
ASSET = re.compile(r'^recovery-a22x-[0-9][0-9.]*\.img$')
RECOVERY_BYTES = 40 << 20
UPDATES = 'a22x-recovery/updates'
BUILD = Path('/etc/a22x-recovery/build.json')


PRE_RELEASE = re.compile(r'-(alpha|beta|rc|preview|pre|dev)\.?([0-9]*)(?=-|$)', re.IGNORECASE)
# The kinds of pre-release in their order: the kind counts before its number (2026-11-14 review:
# beta2 came after rc1, and beta was rc).
PRE_KINDS = {'dev': 0, 'alpha': 1, 'beta': 2, 'pre': 3, 'preview': 3, 'rc': 4}


def version_key(text):
    """0.6.199, v0.6.199 and a build's 0.6.199-public alike, as numbers; a pre-release (-rc1, -beta)
    counts before them, a revision (-r2) after them. () when it is no version. 2026-11-14 reviews:
    the numbers are four, so 1.0 and 1.0.0 are one version and 1.0.0-r1 is not 1.0.0.1; any other
    suffix (-public, -g1a2b-dirty) is a build of the same version, not no version at all; and a
    phone on 1.0.0-rc1 is offered 1.0.0."""
    match = re.fullmatch(r'v?([0-9]+(?:\.[0-9]+){1,3})((?:-[A-Za-z0-9][A-Za-z0-9.+_]*)*)', str(text or '').strip())
    if not match:
        return ()
    numbers = [int(part) for part in match.group(1).split('.')]
    pre = PRE_RELEASE.search(match.group(2))
    stage = (0, PRE_KINDS[pre.group(1).lower()], int(pre.group(2) or 0)) if pre else (1, 0, 0)
    revision = re.search(r'-r([0-9]+)(?:-|$)', match.group(2))
    return tuple(numbers + [0] * (4 - len(numbers))) + stage + (int(revision.group(1)) if revision else 0,)


def version_name(text):
    """The version for a file name, by version_key: 1.0.0, or 1.0.0.0.10 for 1.0.0-r10, so that the
    revision is never read as part of the patch number (2026-11-14 review: 1.0.0-r10 was 1.0.010).
    A pre-release has its version's name: saved_boot.prune_updates keeps names of numbers only, and
    the copy's description says which release it is."""
    key = version_key(text)
    require(key, 'UPDATE_UNUSABLE', 'The release does not name a version')
    parts = list(key[:4]) + [key[-1]]
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return '.'.join(str(part) for part in parts)


def error_code(error, default='UPDATE_UNAVAILABLE'):
    """A code the interface reads: always text (2026-11-14 review: urllib's HTTPError has the HTTP
    status as a number in .code, and the interface showed a failed check as up to date)."""
    code = getattr(error, 'code', None)
    if isinstance(code, str) and code:
        return code
    if isinstance(code, int):
        return 'HTTP_%d' % code
    return default


def installed_version(context):
    if context.mode == 'fixture':
        return str(context.config().get('recovery_version', ''))
    try:
        return str(json.loads(BUILD.read_text()).get('version', ''))
    except (OSError, ValueError):
        return ''


def latest_recovery(context):
    """The newest published recovery: version, file, url, size, sha256; raises without network."""
    if context.mode == 'fixture':
        offer = context.config().get('recovery_release')
        require(isinstance(offer, dict), 'UPDATE_UNAVAILABLE', 'No release could be read')
        return offer
    import root
    return root.latest(REPO, ASSET)


def boot_state(context):
    """What BOOT holds and the Linux BOOT this recovery was made for."""
    import distro
    offer = distro.linux_boot_offered(context)
    try:
        if context.mode == 'fixture' and context.config().get('android_boot'):
            fs.fail('BOOT_PROFILE_MISMATCH', 'BOOT holds Android')
        there = distro.boot_identity(context)
        now = distro.boot_now(context, there)
    except fs.FilesystemError as error:
        # Only BOOT that holds no Linux kernel of this phone is Android's; one that cannot be read
        # now is not known (2026-11-14 review: any error, an I/O error too, showed it as Android).
        if error.code == 'BOOT_PROFILE_MISMATCH':
            return {'kind': 'android', 'latest': offer, 'newer': False}
        return {'kind': 'unknown', 'error': error_code(error, 'IO_ERROR'), 'latest': offer, 'newer': False}
    older = {item.get('sha256') for item in distro.catalog_builtin(context).get('linux_boot_older', []) if isinstance(item, dict)}
    if context.mode == 'fixture':
        older |= set(context.config().get('linux_boot_older_sha256', []))
    state = 'current' if offer and now == offer['sha256'] else ('older' if now in older else 'own')
    return {'kind': 'linux', 'sha256': now, 'state': state, 'latest': offer, 'newer': bool(offer) and state == 'older'}


def boot_known(context):
    """boot_state, or what kept it from being read: the recovery's half of the check is never lost
    for it (2026-11-14 review: an I/O error on BOOT made the whole check fail)."""
    try:
        return boot_state(context)
    except (OSError, ValueError, KeyError, TypeError, fs.FilesystemError) as error:
        return {'kind': 'unknown', 'error': error_code(error, 'IO_ERROR'), 'latest': None, 'newer': False}


def check(context):
    installed = installed_version(context)
    recovery = {'installed': installed, 'latest': None, 'newer': False, 'error': ''}
    import distro
    if not distro.network_joined(context):
        # 2026-11-12, on the phone: without Wi-Fi the check waited more than a minute for GitHub.
        recovery['error'] = 'NO_NETWORK'
        return {'recovery': recovery, 'boot': boot_known(context)}
    try:
        import clock
        clock.ensure(context.mode)  # certificates need the date
    except Exception:  # noqa: BLE001 - a clock that cannot be set shows up as a failed download
        pass
    try:
        offer = latest_recovery(context)
        recovery['latest'] = offer
        recovery['newer'] = version_key(offer.get('version')) > version_key(installed) > ()
    except (OSError, ValueError, fs.FilesystemError) as error:
        recovery['error'] = error_code(error)
    return {'recovery': recovery, 'boot': boot_known(context)}


def download(context, offer, limit):
    if context.mode == 'fixture':
        data = (context.root / 'downloads' / offer['file']).read_bytes()
    else:
        import root
        request = urllib.request.Request(offer['url'], headers={'User-Agent': 'a22x-recovery'})
        chunks, done = [], 0
        with urllib.request.urlopen(request, timeout=60, context=root.tls_context()) as reply:
            while True:
                data = reply.read(1 << 20)
                if not data:
                    break
                done += len(data)
                require(done <= limit, 'DOWNLOAD_TOO_LARGE', 'The download is larger than its release says')
                chunks.append(data)
                progress('Downloading', done, offer['size'])
        data = b''.join(chunks)
    require(len(data) == offer['size'] and hashlib.sha256(data).hexdigest() == offer['sha256'], 'DOWNLOAD_CHECKSUM_MISMATCH',
            'The download differs from its release. Try again.')
    return data


def progress(phase, done, total):
    print(json.dumps({'event': 'progress', 'phase': phase, 'bytes_done': done, 'bytes_total': total}), file=sys.stderr, flush=True)


def fetch(context, what, sha256):
    import saved_boot
    holder = types.SimpleNamespace(fs=context)
    if what == 'recovery':
        offer = latest_recovery(context)
        require(offer.get('sha256') == sha256, 'UPDATE_CHANGED', 'A newer release appeared meanwhile: check again')
        require(offer.get('size') == RECOVERY_BYTES, 'UPDATE_UNUSABLE', 'The release does not hold a recovery image of this phone')
        image = download(context, offer, RECOVERY_BYTES)
        info = bootimage.inspect('recovery', image)
        require(info['device']['this_phone'], 'BOOT_PHONE_UNKNOWN', 'Nothing in this boot image names the Galaxy A22 5G (SM-A226B)')
        name = 'recovery-%s.img' % version_name(offer.get('version', ''))
        path = saved_boot.save_on_cache(holder, name, image, {'kind': 'recovery', 'version': offer.get('version', '')},
                                        lambda *unused: None, keep_mounted=True, folder_name=UPDATES)
        prune(context, name)
    else:
        import distro
        state = boot_state(context)
        offer = state.get('latest')
        require(state['kind'] == 'linux' and offer and offer['sha256'] == sha256, 'UPDATE_CHANGED',
                'BOOT or the offered Linux BOOT changed: check again')
        path = distro.linux_boot_fetched(context, {'boot_sha256': sha256}, lambda phase, done=0, total=0: progress('Downloading', done, total))
        volume = context.volume('cache')
        if not volume['mounted']:
            context.mount('cache', 'ro', False, volume['volume_token'])
    volume = context.volume('cache')
    return {'what': what, 'volume': 'cache', 'volume_token': volume['volume_token'], 'path': path,
            'target': 'recovery' if what == 'recovery' else 'boot', 'sha256': sha256}


def prune(context, keep):
    """Only the newest downloaded recovery stays on cache, by the rule the start of the recovery
    uses (saved_boot.prune_updates; 2026-11-14 review: two rules for one folder). save_on_cache
    has just left cache mounted read-only for Flash a partition: it is opened for writing here
    and left read-only again (2026-11-12 review: the old copies were never removed)."""
    import saved_boot
    with contextlib.suppress(OSError, fs.FilesystemError):
        volume = context.volume('cache')
        if not volume['mounted']:
            return
        reopen = volume['read_only']
        if reopen:
            context.mount('cache', 'rw', True, volume['volume_token'])
        try:
            with fs.PinnedVolume(context, 'cache', writing=True) as pin:
                with pin.directory(UPDATES) as folder:
                    # The image just downloaded is the one Flash a partition is about to write: the
                    # rule runs only when it is the newest there, so it never removes that one.
                    def number(name):
                        found = re.fullmatch(r'recovery-([0-9.]+)\.img', name)
                        return tuple(int(part) for part in found.group(1).split('.') if part) if found else None
                    others = [number(name) for name in os.listdir(folder) if name != keep and number(name) is not None]
                    if number(keep) is not None and all(other < number(keep) for other in others):
                        saved_boot.prune_updates(folder)
                    os.fsync(folder)
        finally:
            if reopen:
                context.mount('cache', 'ro', True, context.volume('cache')['volume_token'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    command = commands.add_parser('fetch')
    command.add_argument('--what', choices=('recovery', 'boot'), required=True)
    command.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    try:
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        if args.command == 'check':
            result = check(context)
        else:
            require(re.fullmatch(r'[0-9a-f]{64}', args.sha256), 'INVALID_ARGUMENT', 'A SHA-256 is needed')
            with context.operation_lock():
                result = fetch(context, args.what, args.sha256)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, KeyError, fs.FilesystemError) as error:  # bootimage.ImageError is a ValueError
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': error_code(error, 'IO_ERROR'), 'message': str(error)[:300]}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
