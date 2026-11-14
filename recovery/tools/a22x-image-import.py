#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Turn an ARM64 distribution image into an offline A22x recovery bundle.

Accepted inputs: ISO (squashfs/erofs live media), RAW/IMG disk images (GPT, MBR
or a bare filesystem), qcow2, rootfs tarballs, and any of these compressed with
xz/zstd/gzip/bzip2. Runs on a Linux PC without root: the work happens in an
unprivileged user namespace, so file owners survive. Nothing from the image is
executed. The output directory goes on the microSD card:

    bundle.json  rootfs.tar.gz

An image's own kernel and bootloader are discarded: the phone keeps its BOOT.
A bundle is a candidate until it has booted on the phone.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

CHUNK = 1 << 20
KERNEL = '7.2.0-a22x+'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from a22x_adapter import AdaptError as ImportError_, adapt, die  # noqa: E402


def log(message):
    print(f'[import] {message}', file=sys.stderr, flush=True)


def run(argv, **kw):
    log('$ ' + ' '.join(str(a) for a in argv)[:200])
    return subprocess.run([str(a) for a in argv], check=True, **kw)


def host_run(argv):
    """libguestfs cannot start in the user namespace: the parent process runs it. Run by real root
    there is no namespace and no parent helper: it runs here (2026-11-14 review: KeyError)."""
    if 'A22X_IMPORT_FDS' not in os.environ:
        try:
            run(argv)
        except subprocess.CalledProcessError as error:
            die(f'{argv[0]} failed with status {error.returncode}')
        return
    log('host $ ' + ' '.join(argv)[:200])
    request, reply = (int(n) for n in os.environ['A22X_IMPORT_FDS'].split(','))
    os.write(request, json.dumps(argv).encode() + b'\n')
    answer = b''
    while not answer.endswith(b'\n'):
        block = os.read(reply, 64)
        if not block:
            die('The host helper stopped')
        answer += block
    if answer.strip() != b'0':
        die(f'{argv[0]} failed with status {answer.decode().strip()}')


def bundle_boots(profile):
    """Every BOOT the profile admits, for the bundle to name all of them (2026-11-14 review: the first
    of the sorted list was named as if it were the BOOT the bundle was made for). boot_sha256 keeps
    the profile's own when it has one; distro.py reads it only as a well-formed hash."""
    allowed = sorted(profile.get('boot_sha256_allowed') or [profile['boot_sha256']])
    return profile.get('boot_sha256') or allowed[0], allowed


def host_service(child, request, reply):
    with os.fdopen(request, 'rb') as stream:
        for line in stream:
            done = subprocess.run(json.loads(line), stdout=subprocess.DEVNULL)
            os.write(reply, b'%d\n' % done.returncode)
    return child.wait()


def need(tool):
    if not shutil.which(tool):
        die(f'{tool} is required for this image type')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def magic(path, offset=0, size=8):
    with open(path, 'rb') as f:
        f.seek(offset)
        return f.read(size)


# ---------- format detection -------------------------------------------------

COMPRESSORS = ((b'\xfd7zXZ\0', ['xz', '-dc']), (b'\x28\xb5\x2f\xfd', ['zstd', '-dc']),
               (b'\x1f\x8b', ['gzip', '-dc']), (b'BZh', ['bzip2', '-dc']))


def compressor(path):
    head = magic(path, 0, 6)
    for prefix, argv in COMPRESSORS:
        if head.startswith(prefix):
            return argv
    return None


def is_tar_stream(argv, path):
    """A compressed input is a tarball when ustar appears at offset 257."""
    if argv is None:
        return magic(path, 257, 5) == b'ustar'
    with open(path, 'rb') as f:
        p = subprocess.Popen(argv, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        head = p.stdout.read(512)
        p.kill()
        p.wait()
    return head[257:262] == b'ustar'


def fs_type(path, offset=0):
    if magic(path, offset + 1080, 2) == b'\x53\xef':
        return 'ext'
    if magic(path, offset + 0x10040, 8) == b'_BHRfS_M':
        return 'btrfs'
    if magic(path, offset, 4) == b'XFSB':
        return 'xfs'
    if magic(path, offset, 4) in (b'hsqs', b'sqsh'):
        return 'squashfs'
    if magic(path, offset + 1024, 4) == struct.pack('<I', 0xE0F5E1E2):
        return 'erofs'
    if magic(path, offset + 0x8001, 5) == b'CD001':
        return 'iso'
    return None


def partitions(path):
    """Return (offset, size) of each partition, or an empty list for a bare image."""
    p = subprocess.run(['sfdisk', '-J', str(path)], capture_output=True, text=True)
    if p.returncode:
        return []
    table = json.loads(p.stdout)['partitiontable']
    unit = table.get('sectorsize', 512)
    return [(item['start'] * unit, item['size'] * unit) for item in table['partitions']]


def carve(path, offset, size, out):
    with open(path, 'rb') as src, open(out, 'wb') as dst:
        src.seek(offset)
        left = size
        while left:
            block = src.read(min(CHUNK * 8, left))
            if not block:
                break
            if block.count(0) == len(block):
                dst.seek(len(block), 1)
            else:
                dst.write(block)
            left -= len(block)
        dst.truncate(size)


# ---------- extraction -------------------------------------------------------

def extract_filesystem(path, kind, dest):
    if kind == 'squashfs':
        need('unsquashfs')
        run(['unsquashfs', '-f', '-no-progress', '-d', dest, path], stdout=subprocess.DEVNULL)
    elif kind in ('ext', 'btrfs', 'xfs', 'erofs'):
        # libguestfs keeps setuid bits, owners and file capabilities. debugfs rdump loses them.
        need('guestfish')
        dest.mkdir(parents=True, exist_ok=True)
        tar = dest.parent / (dest.name + '.tar')
        mount = ['mount-vfs', 'ro', 'erofs', '/dev/sda', '/'] if kind == 'erofs' else ['mount', '/dev/sda', '/']
        host_run(['guestfish', '--ro', '-a', str(path), 'run', ':', *mount, ':',
                  'tar-out', '/', str(tar), 'numericowner:true', 'xattrs:true'])
        run(['tar', '--numeric-owner', '--xattrs', '--xattrs-include=*', '-xpf', tar, '-C', dest],
            stderr=subprocess.DEVNULL)
        tar.unlink()
    else:
        die(f'Unsupported filesystem: {kind}')


def looks_like_root(root):
    return any((root / p).exists() or (root / p).is_symlink() for p in ('usr/lib/os-release', 'etc/os-release')) and \
        (root / 'usr').is_dir()


LIVE_PATHS = ('LiveOS/squashfs.img', 'LiveOS/rootfs.img', 'casper/filesystem.squashfs',
              'casper/minimal.squashfs', 'live/filesystem.squashfs', 'arch/aarch64/airootfs.sfs',
              'arch/aarch64/airootfs.erofs', 'images/install.img')


def from_iso(path, work, dest):
    need('bsdtar')
    media = work / 'iso'
    media.mkdir()
    # bsdtar reports the patterns that a given ISO does not have; the search below decides.
    subprocess.run(['bsdtar', '-xf', str(path), '-C', str(media), '--no-same-owner'] +
                   ['--include=' + p for p in LIVE_PATHS] + ['--include=casper/*'], stderr=subprocess.DEVNULL)
    candidates = [media / p for p in LIVE_PATHS if (media / p).is_file()]
    candidates += sorted((media / 'casper').glob('*.squashfs'), key=lambda p: -p.stat().st_size) \
        if (media / 'casper').is_dir() else []
    if not candidates:
        die('No live root filesystem was found on this ISO. A network-only installer has no rootfs to extract.')
    from_image(candidates[0], work, dest, depth=1)
    shutil.rmtree(media)


def from_image(path, work, dest, depth=0):
    if depth > 3:
        die('Too many nested images')
    kind = fs_type(path)
    if kind == 'iso':
        return from_iso(path, work, dest)
    if kind:
        stage = work / f'layer{depth}'
        extract_filesystem(path, kind, stage)
        nested = [stage / n for n in ('LiveOS/rootfs.img', 'LiveOS/ext3fs.img')]
        nested = [p for p in nested if p.is_file()]
        if nested and not looks_like_root(stage):
            from_image(nested[0], work, dest, depth + 1)
            shutil.rmtree(stage)
            return None
        if not looks_like_root(stage):
            die(f'The {kind} filesystem is not a Linux root')
        stage.rename(dest)
        return None
    parts = partitions(path)
    if not parts:
        die('Unknown image format: no filesystem or partition table was recognised')
    for number, (offset, size) in sorted(enumerate(parts), key=lambda item: -item[1][1]):
        kind = fs_type(path, offset)
        if kind not in ('ext', 'btrfs', 'xfs', 'squashfs', 'erofs'):
            continue
        log(f'partition {number + 1}: {kind}, {size >> 20} MiB')
        part = work / f'part{number}.img'
        carve(path, offset, size, part)
        stage = work / f'part{number}'
        extract_filesystem(part, kind, stage)
        part.unlink()
        if looks_like_root(stage):
            stage.rename(dest)
            return None
        shutil.rmtree(stage)
    die('No partition holds a Linux root filesystem')


def extract(source, work, dest):
    argv = compressor(source)
    if is_tar_stream(argv, source):
        dest.mkdir()
        run(['tar', '--numeric-owner', '--xattrs', '--xattrs-include=*', '-xpf', source, '-C', dest],
            stderr=subprocess.DEVNULL)
        if not looks_like_root(dest):
            die('The tar archive is not a Linux root filesystem')
        return 'tar'
    path = source
    if argv:
        path = work / 'decompressed.img'
        with open(source, 'rb') as src, open(path, 'wb') as dst:
            run(argv, stdin=src, stdout=dst)
    if magic(path, 0, 4) == b'QFI\xfb':
        need('qemu-img')
        raw = work / 'converted.raw'
        run(['qemu-img', 'convert', '-O', 'raw', path, raw])
        if path != source:
            path.unlink()
        path = raw
    kind = fs_type(path) or 'disk'
    from_image(path, work, dest)
    if path != source:
        path.unlink()
    return kind


def pack(root, out):
    """Hardlinks are dereferenced: the recovery admits xattrs on regular members only."""
    archive = out / 'rootfs.tar.gz'
    entries = sum(len(d) + len(f) for _, d, f in os.walk(root))
    with open(archive, 'wb') as raw:
        tar = subprocess.Popen(
            ['tar', '--format=pax', '--numeric-owner', '--hard-dereference', '--sparse-version=0',
             '--pax-option=delete=atime,delete=ctime,delete=mtime', '--xattrs',
             '--xattrs-include=security.capability', '--xattrs-include=user.*', '--sort=name',
             '-C', str(root), '-cf', '-', '.'], stdout=subprocess.PIPE)
        compress = 'pigz' if shutil.which('pigz') else 'gzip'
        gz = subprocess.Popen([compress, '-6', '-n'], stdin=tar.stdout, stdout=raw)
        tar.stdout.close()
        if gz.wait() or tar.wait() not in (0, 1):
            die('Could not write the rootfs archive')
    return archive, entries


def build(args):
    meta = json.loads(Path(args.platform_meta).read_text())
    meta.setdefault('shadow', {})
    if args.shadow:
        for line in Path(args.shadow).read_text().splitlines():
            parts = line.split(':')
            if len(parts) > 1:
                meta['shadow'][parts[0]] = parts[1]
    profile = json.loads(Path(args.profile).read_text())
    if args.profile_digest is None:
        canonical = {k: v for k, v in profile.items()}
        args.profile_digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=True, sort_keys=True,
                                                        separators=(',', ':')).encode()).hexdigest()
    final = Path(args.output)
    if final.exists():
        die(f'{final} already exists')
    work = Path(tempfile.mkdtemp(prefix='a22x-import-', dir=args.work))
    try:
        root, out = work / 'root', work / 'out'
        out.mkdir()
        source = extract(Path(args.image).resolve(), work, root)
        identity = adapt(root, args, meta)
        archive, entries = pack(root, out)
        bundle = {
            'schema': 'a22x.distro.bundle.v1', 'architecture': 'aarch64', 'id': args.id,
            'name': args.name or identity.get('PRETTY_NAME', args.id),
            'boot_profile': profile['id'], 'boot_sha256': bundle_boots(profile)[0], 'boot_sha256_allowed': bundle_boots(profile)[1],
            'platform_profile_sha256': args.profile_digest,
            'rootfs': {'file': archive.name, 'compression': 'gzip', 'sha256': sha256(archive),
                       'bytes': archive.stat().st_size},
            'source': {'file': Path(args.image).name, 'format': source, 'sha256': sha256(args.image)},
            'os_release': {k: identity.get(k, '') for k in ('ID', 'VERSION_ID', 'PRETTY_NAME')},
            'entries': entries, 'ssh': meta['ssh'], 'python3': meta['python3'],
            'created_utc': int(time.time()), 'hardware_boot_tested': False,
        }
        (out / 'bundle.json').write_text(json.dumps(bundle, indent=1, sort_keys=True) + '\n')
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(final))
        print(json.dumps(bundle, indent=1, sort_keys=True))
    finally:
        subprocess.run(['chmod', '-R', 'u+rwX', str(work)], check=False)
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image')
    ap.add_argument('--id', required=True, help='Slot ID, for example debian-arm64')
    ap.add_argument('--name')
    ap.add_argument('--platform', required=True, help='Tar captured from this phone: kernel modules and firmware')
    ap.add_argument('--platform-meta', required=True, help='JSON with the shared-home accounts')
    ap.add_argument('--profile', required=True, help='distro-platform.json embedded in recovery')
    ap.add_argument('--profile-digest')
    ap.add_argument('--helpers', required=True, type=Path, help='Directory with a22x-wmt, busybox and conn-start.sh')
    ap.add_argument('--output', required=True, help='New directory')
    ap.add_argument('--work', default=None, help='Scratch directory on a large filesystem')
    ap.add_argument('--shadow', help='Password hashes for the shared accounts')
    ap.add_argument('--authorized-key', help='Public key for root login over USB')
    ap.add_argument('--console', action='store_true', help='Boot to a text console')
    ap.add_argument('--keep-firmware', action='store_true')
    ap.add_argument('--trial-seconds', type=int, default=0,
                    help='Return to recovery after this time unless /var/lib/a22x-accepted exists')
    ap.add_argument('--allow-no-ssh', action='store_true')
    args = ap.parse_args()
    if os.geteuid() != 0 and 'A22X_IMPORT_FDS' not in os.environ:
        request_r, request_w = os.pipe()
        reply_r, reply_w = os.pipe()
        os.environ['A22X_IMPORT_FDS'] = f'{request_w},{reply_r}'
        child = subprocess.Popen(['unshare', '--map-auto', '--map-root-user', '--', sys.executable, *sys.argv],
                                 pass_fds=(request_w, reply_r))
        os.close(request_w)
        os.close(reply_r)
        return host_service(child, request_r, reply_w)
    try:
        build(args)
    except (ImportError_, subprocess.CalledProcessError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
