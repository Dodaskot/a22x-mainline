# SPDX-License-Identifier: MIT
"""Extract a downloaded ARM64 image into a directory, on the phone.

Reads ISO, disk images with GPT or MBR, bare filesystems and tar archives, plain
or compressed. Nothing from the image is executed: filesystems are mounted read
only with a loop device, or read by unsquashfs and tarfile.
"""
import bz2
import gzip
import lzma
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import zlib
from pathlib import Path

SECTOR = 2048
CHUNK = 4 << 20
ZEROS = bytes(CHUNK)
SPARSE_STEP = 64 << 10
KEEP_FREE = 256 << 20
BUSYBOX = '/bin/busybox'
ZSTD = '/usr/bin/zstd'
UNSQUASHFS = '/bin/unsquashfs'
LIVE_PATHS = ('LIVEOS/SQUASHFS.IMG', 'LIVEOS/ROOTFS.IMG', 'CASPER/FILESYSTEM.SQUASHFS',
              'CASPER/MINIMAL.SQUASHFS', 'LIVE/FILESYSTEM.SQUASHFS', 'ARCH/AARCH64/AIROOTFS.SFS',
              'ARCH/AARCH64/AIROOTFS.EROFS', 'IMAGES/INSTALL.IMG')
XATTRS = ('user.', 'security.capability')
QCOW2_MAGIC = b'QFI\xfb'
DISK_NAMES = ('*.img', '*.raw', '*.qcow2', '*.vhd', '*.vhdfixed')
SUBVOLUMES = ('root', '@', '@rootfs', 'rootfs')
MOUNTABLE = ('ext', 'btrfs', 'xfs', 'erofs', 'f2fs')
COMPRESSED = {b'\xfd7zXZ\0': 'xz', b'\x28\xb5\x2f\xfd': 'zstd', b'\x1f\x8b': 'gzip', b'BZh': 'bzip2'}


class ImageError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise ImageError(code, message)


def peek(path, offset, size):
    with open(path, 'rb') as f:
        f.seek(offset)
        return f.read(size)


def compression(path):
    head = peek(path, 0, 6)
    return next((name for magic, name in COMPRESSED.items() if head.startswith(magic)), None)


def fs_type(path, offset=0):
    if peek(path, offset + 1080, 2) == b'\x53\xef':
        return 'ext'
    if peek(path, offset + 0x10040, 8) == b'_BHRfS_M':
        return 'btrfs'
    if peek(path, offset, 4) == b'XFSB':
        return 'xfs'
    if peek(path, offset, 4) in (b'hsqs', b'sqsh'):
        return 'squashfs'
    if peek(path, offset + 1024, 4) == struct.pack('<I', 0xE0F5E1E2):
        return 'erofs'
    if peek(path, offset + 1024, 4) == struct.pack('<I', 0xF2F52010):
        return 'f2fs'
    if peek(path, offset + 0x8001, 5) == b'CD001':
        return 'iso'
    return None


def partitions(path):
    """Return [(offset, size)] from a GPT, or from a primary MBR table."""
    sector0, sector1 = peek(path, 0, 512), peek(path, 512, 512)
    found = []
    if sector1[:8] == b'EFI PART':
        lba, count, size = struct.unpack_from('<QII', sector1, 72)
        with open(path, 'rb') as f:
            f.seek(lba * 512)
            table = f.read(min(count, 1024) * size)
        for n in range(min(count, 1024)):
            entry = table[n * size:(n + 1) * size]
            if len(entry) == size and entry[:16] != bytes(16):
                first, last = struct.unpack_from('<QQ', entry, 32)
                found.append((first * 512, (last - first + 1) * 512))
    elif sector0[510:512] == b'\x55\xaa':
        for n in range(4):
            kind = sector0[446 + n * 16 + 4]
            start, count = struct.unpack_from('<II', sector0, 446 + n * 16 + 8)
            if kind and kind != 0xEE and count:
                found.append((start * 512, count * 512))
    return found


def iso_files(path):
    """Map upper-case paths to (offset, size). ISO9660 names are enough to find live images."""
    pvd = peek(path, 16 * SECTOR, SECTOR)
    if pvd[1:6] != b'CD001' or pvd[0] != 1:
        fail('INVALID_ISO', 'This ISO has no primary volume descriptor')
    files, pending = {}, [('', struct.unpack_from('<I', pvd, 156 + 2)[0], struct.unpack_from('<I', pvd, 156 + 10)[0], 0)]
    with open(path, 'rb') as f:
        while pending:
            prefix, lba, size, depth = pending.pop()
            f.seek(lba * SECTOR)
            data = f.read(min(size, 1 << 24))
            at = 0
            while at < len(data):
                length = data[at]
                if not length:
                    at = (at // SECTOR + 1) * SECTOR
                    continue
                extent, bytes_ = struct.unpack_from('<I', data, at + 2)[0], struct.unpack_from('<I', data, at + 10)[0]
                flags, name_length = data[at + 25], data[at + 32]
                name = data[at + 33:at + 33 + name_length]
                at += length
                if name in (b'\0', b'\1'):
                    continue
                text = name.decode('ascii', 'replace').split(';')[0].rstrip('.').upper()
                if flags & 2:
                    if depth < 6:
                        pending.append((prefix + text + '/', extent, bytes_, depth + 1))
                else:
                    files.setdefault(prefix + text, (extent * SECTOR, bytes_))
    return files


def run(argv):
    done = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    if done.returncode:
        fail('EXTRACT_FAILED', f'{Path(str(argv[0])).name} failed: {done.stdout[-300:]}')
    return done.stdout


def copy_xattrs(source, target):
    try:
        names = os.listxattr(source, follow_symlinks=False)
    except OSError:
        return
    for name in names:
        if name.startswith(XATTRS):
            os.setxattr(target, name, os.getxattr(source, name, follow_symlinks=False), follow_symlinks=False)


def copy_tree(source, target, progress=None):
    """Copy owners, modes, times, hardlinks, capabilities. Devices and sockets are skipped.

    PROGRESS hears the bytes copied against what the source filesystem holds.
    """
    links, directories, count, copied = {}, [], 0, 0
    try:
        info = os.statvfs(source)
        total = (info.f_blocks - info.f_bfree) * info.f_frsize
    except OSError:
        total = 0
    stack = [(Path(source), Path(target))]
    while stack:
        src, dst = stack.pop()
        dst.mkdir(exist_ok=True)
        directories.append((src, dst))
        with os.scandir(src) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                out = dst / entry.name
                count += 1
                if progress and count % 500 == 0:
                    progress('image-copy', copied, total)
                if stat.S_ISDIR(info.st_mode):
                    if info.st_dev == os.stat(source).st_dev:
                        stack.append((Path(entry.path), out))
                elif stat.S_ISLNK(info.st_mode):
                    os.symlink(os.readlink(entry.path), out)
                    os.chown(out, info.st_uid, info.st_gid, follow_symlinks=False)
                elif stat.S_ISREG(info.st_mode):
                    key = (info.st_dev, info.st_ino)
                    if info.st_nlink > 1 and key in links:
                        os.link(links[key], out)
                        continue
                    with open(entry.path, 'rb') as a, open(out, 'wb') as b:
                        shutil.copyfileobj(a, b, CHUNK)
                    copied += info.st_size
                    os.chown(out, info.st_uid, info.st_gid)
                    os.chmod(out, stat.S_IMODE(info.st_mode))
                    copy_xattrs(entry.path, out)
                    os.utime(out, ns=(info.st_atime_ns, info.st_mtime_ns))
                    if info.st_nlink > 1:
                        links[key] = out
    for src, dst in reversed(directories):
        info = os.stat(src)
        os.chown(dst, info.st_uid, info.st_gid)
        os.chmod(dst, stat.S_IMODE(info.st_mode))
        copy_xattrs(src, dst)
        os.utime(dst, ns=(info.st_atime_ns, info.st_mtime_ns))
    if progress:
        progress('image-copy', max(total, copied), max(total, copied))
    return count


def is_root(path):
    return (Path(path) / 'usr').is_dir() and any(os.path.lexists(Path(path) / p) for p in ('etc/os-release', 'usr/lib/os-release'))


class Loop:
    """Read-only loop mount. A failed unmount is reported and never forced."""
    def __init__(self, image, offset, kind, mountpoint):
        self.image, self.offset, self.kind, self.mountpoint = image, offset, kind, Path(mountpoint)

    def __enter__(self):
        self.mountpoint.mkdir(parents=True, exist_ok=True)
        names = {'ext': 'ext4'}
        options = f'ro,loop,offset={self.offset}'
        if self.kind == 'ext':
            options += ',noload'
        run([BUSYBOX, 'mount', '-t', names.get(self.kind, self.kind), '-o', options, self.image, self.mountpoint])
        return self.mountpoint

    def __exit__(self, *unused):
        run([BUSYBOX, 'umount', self.mountpoint])
        self.mountpoint.rmdir()


def kernel_reads(kind):
    """Whether the running kernel mounts this filesystem; the recovery kernel has no btrfs or xfs."""
    try:
        names = {line.split()[-1] for line in Path('/proc/filesystems').read_text().splitlines() if line.strip()}
    except OSError:
        return True
    return {'ext': 'ext4'}.get(kind, kind) in names


def from_filesystem(path, offset, kind, work, root, depth, progress):
    if kind == 'squashfs':
        stage = work / f'squash{depth}'
        run([UNSQUASHFS, '-f', '-no-progress', '-o', offset, '-d', stage, path])
        if is_root(stage):
            for child in stage.iterdir():
                child.rename(root / child.name)
            shutil.copystat(stage, root)
            os.chown(root, 0, 0)
            stage.rmdir()
            return True
        nested = next((stage / n for n in ('LiveOS/rootfs.img', 'LiveOS/ext3fs.img') if (stage / n).is_file()), None)
        found = nested is not None and locate(nested, work, root, depth + 1, progress)
        shutil.rmtree(stage)
        return found
    if kind not in MOUNTABLE:
        return False
    if not kernel_reads(kind):
        fail('FILESYSTEM_UNSUPPORTED', 'This image keeps its system on %s, which this recovery cannot read' % kind)
    with Loop(path, offset, kind, work / f'mount{depth}') as mounted:
        if is_root(mounted):
            copy_tree(mounted, root, progress)
            return True
        # btrfs keeps the system in a subvolume: "root" on Fedora, "@" on Manjaro and Ubuntu.
        for name in SUBVOLUMES if kind == 'btrfs' else ():
            inner = mounted / name
            if inner.is_dir() and not inner.is_symlink() and is_root(inner):
                copy_tree(inner, root, progress)
                copy_subvolumes(mounted, root, progress)
                return True
        nested = next((mounted / n for n in ('LiveOS/rootfs.img', 'LiveOS/ext3fs.img') if (mounted / n).is_file()), None)
        return nested is not None and locate(nested, work, root, depth + 1, progress)


# Subvolumes the system mounts at these places belong to it: Fedora 45 Cloud keeps /var in one,
# and without it the new root had no /var/log, /var/cache or /var/lib/dnf (2026-11-08).
SUBVOLUME_PLACES = ('/var', '/home', '/opt', '/srv', '/usr/local')


def fstab_subvolumes(root):
    """[(subvolume, place)] of the btrfs mounts the image's /etc/fstab lists under SUBVOLUME_PLACES."""
    found = []
    try:
        lines = (Path(root) / 'etc/fstab').read_text(errors='replace').splitlines()
    except OSError:
        return found
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[0].startswith('#') or fields[2] != 'btrfs':
            continue
        place = fields[1].rstrip('/') or '/'
        if not any(place == p or place.startswith(p + '/') for p in SUBVOLUME_PLACES) or '..' in place.split('/'):
            continue
        for option in fields[3].split(','):
            if option.startswith('subvol='):
                name = option[len('subvol='):].strip('/')
                if name and '..' not in name.split('/'):
                    found.append((name, place))
    return sorted(found, key=lambda item: item[1].count('/'))


def copy_subvolumes(mounted, root, progress):
    """Copy the subvolumes of the image's fstab into their places in ROOT."""
    for name, place in fstab_subvolumes(root):
        source = Path(mounted) / name
        target = Path(root) / place.lstrip('/')
        if not source.is_dir() or source.is_symlink():
            continue
        # The place must be a directory of ROOT itself, never a link out of it.
        walked = Path(root)
        for part in Path(place.lstrip('/')).parts:
            walked = walked / part
            if walked.is_symlink():
                break
        else:
            copy_tree(source, target, progress)


def disk_format(path):
    """A virtual machine disk to unpack first: 'qcow2' or a dynamic 'vhd'. A fixed VHD is a raw
    disk with a footer at its end and is read as it is."""
    head = peek(path, 0, 512)
    if head[:4] == QCOW2_MAGIC:
        return 'qcow2'
    if head[:8] == b'conectix' and len(head) >= 64 and struct.unpack_from('>I', head, 60)[0] in (3, 4):
        return 'vhd'
    return None


def write_cluster(out, offset, data):
    """Only data that is not all zero is written, so the raw disk stays sparse."""
    if data.count(0) != len(data):
        out.seek(offset)
        out.write(data)


def zstd_cluster(packed, size):
    """One zstd frame; the recovery's Python has no zstd module, so libzstd is called directly."""
    try:
        from compression import zstd
        return zstd.ZstdDecompressor().decompress(packed, size)
    except ImportError:
        pass
    import ctypes
    try:
        lib = ctypes.CDLL('libzstd.so.1')
    except OSError:
        fail('IMAGE_UNSUPPORTED', 'This qcow2 image uses zstd compression, which this recovery cannot read')
    lib.ZSTD_findFrameCompressedSize.restype = lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_findFrameCompressedSize.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.ZSTD_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
    lib.ZSTD_isError.restype, lib.ZSTD_isError.argtypes = ctypes.c_uint, [ctypes.c_size_t]
    frame = lib.ZSTD_findFrameCompressedSize(packed, len(packed))
    out = ctypes.create_string_buffer(size)
    done = lib.ZSTD_decompress(out, size, packed, frame) if not lib.ZSTD_isError(frame) else frame
    if lib.ZSTD_isError(done):
        fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
    return out.raw[:done]


def inflate(packed, method, size):
    if method == 'zstd':
        return zstd_cluster(packed, size)
    return zlib.decompressobj(-15).decompress(packed, size)


def qcow2_to_raw(path, out, report=None):
    """QEMU qcow2 (version 2 or 3) as a sparse raw disk: plain and compressed clusters."""
    with open(path, 'rb') as f:
        head = f.read(112)
        if len(head) < 72:
            fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
        (version, backing, cluster_bits, size, crypt, l1_size, l1_offset) = struct.unpack_from('>IQ4xIQIIQ', head, 4)
        if version not in (2, 3) or not 9 <= cluster_bits <= 21:
            fail('IMAGE_UNSUPPORTED', 'This qcow2 version is not supported')
        if backing:
            fail('IMAGE_UNSUPPORTED', 'This qcow2 image needs a backing file')
        if crypt:
            fail('IMAGE_UNSUPPORTED', 'Encrypted qcow2 images are not supported')
        method = 'zlib'
        if version == 3:
            incompatible = struct.unpack_from('>Q', head, 72)[0]
            if incompatible & ~0b1001:
                fail('IMAGE_UNSUPPORTED', 'This qcow2 image uses features this recovery cannot read')
            if incompatible & 0b1000 and struct.unpack_from('>I', head, 100)[0] > 104 and head[104] == 1:
                method = 'zstd'
        cluster = 1 << cluster_bits
        per_table = cluster // 8
        if l1_size > (size + cluster * per_table - 1) // (cluster * per_table) + 1:
            fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
        f.seek(l1_offset)
        l1 = struct.unpack('>%dQ' % l1_size, f.read(8 * l1_size))
        span = 62 - (cluster_bits - 8)
        for index, entry in enumerate(l1):
            table = entry & 0x00fffffffffffe00
            if not table:
                continue
            f.seek(table)
            l2 = struct.unpack('>%dQ' % per_table, f.read(cluster))
            for slot, value in enumerate(l2):
                guest = (index * per_table + slot) * cluster
                if guest >= size:
                    break
                if value & (1 << 62):
                    host = value & ((1 << span) - 1)
                    sectors = ((value >> span) & ((1 << (cluster_bits - 8)) - 1)) + 1
                    f.seek(host)
                    data = inflate(f.read(sectors * 512 - (host & 511)), method, cluster)
                else:
                    host = value & 0x00fffffffffffe00
                    if not host or (version == 3 and value & 1):
                        continue
                    f.seek(host)
                    data = f.read(cluster)
                write_cluster(out, guest, data[:min(cluster, size - guest)])
            if report:
                report(f.tell())
        out.truncate(size)


def vhd_to_raw(path, out, report=None):
    """A dynamic VHD as a sparse raw disk; its blocks are listed in the block allocation table."""
    with open(path, 'rb') as f:
        footer = f.read(512)
        if struct.unpack_from('>I', footer, 60)[0] == 4:
            fail('IMAGE_UNSUPPORTED', 'A differencing VHD needs its parent image')
        size = struct.unpack_from('>Q', footer, 48)[0]
        f.seek(struct.unpack_from('>Q', footer, 16)[0])
        header = f.read(1024)
        if header[:8] != b'cxsparse':
            fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
        table, entries, block = struct.unpack_from('>Q', header, 16)[0], *struct.unpack_from('>II', header, 28)
        if not block or block % 512 or entries > (size + block - 1) // block + 1:
            fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
        bitmap = (block // 512 // 8 + 511) // 512 * 512
        f.seek(table)
        allocation = struct.unpack('>%dI' % entries, f.read(4 * entries))
        for index, sector in enumerate(allocation):
            if sector == 0xFFFFFFFF or index * block >= size:
                continue
            f.seek(sector * 512 + bitmap)
            done, length = 0, min(block, size - index * block)
            while done < length:
                data = f.read(min(CHUNK, length - done))
                if not data:
                    fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
                write_cluster(out, index * block + done, data)
                done += len(data)
            if report:
                report(f.tell())
        out.truncate(size)


def locate(path, work, root, depth, progress):
    if depth > 3:
        fail('IMAGE_TOO_NESTED', 'Too many nested images')
    disk = disk_format(path)
    if disk:
        # A virtual machine disk (also inside a Vagrant box) is unpacked to a sparse raw disk.
        raw = work / f'disk{depth}.raw'
        total = Path(path).stat().st_size
        report = (lambda done: progress('image-unpack', done, total)) if progress else None
        try:
            with open(raw, 'wb') as out:
                (qcow2_to_raw if disk == 'qcow2' else vhd_to_raw)(path, out, report)
            return locate(raw, work, root, depth + 1, progress)
        finally:
            raw.unlink(missing_ok=True)
    kind = fs_type(path)
    if kind == 'iso':
        files = iso_files(path)
        names = [n for n in LIVE_PATHS if n in files]
        names += sorted((n for n in files if n.startswith('CASPER/') and n.endswith('.SQUASHFS') and n not in names),
                        key=lambda n: -files[n][1])
        for name in names:
            offset = files[name][0]
            inner = fs_type(path, offset)
            if inner and from_filesystem(path, offset, inner, work, root, depth + 1, progress):
                return True
        fail('NO_ROOT_ON_ISO', 'This ISO has no live system to copy. A network installer cannot be used.')
    if kind:
        return from_filesystem(path, 0, kind, work, root, depth, progress)
    for offset, size in sorted(partitions(path), key=lambda item: -item[1]):
        inner = fs_type(path, offset)
        if inner and from_filesystem(path, offset, inner, work, root, depth, progress):
            return True
    return False


def open_stream(path, kind):
    """A readable decompressed stream and an optional helper process."""
    if kind == 'zstd':
        process = subprocess.Popen([ZSTD, '-dc', str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return process.stdout, process
    if kind:
        return {'xz': lzma.open, 'gzip': gzip.open, 'bzip2': bz2.open}[kind](path, 'rb'), None
    return open(path, 'rb'), None


def ext_usage(path, kind):
    """Size and used bytes of an ext filesystem at the start of a compressed image, else None.

    A whole partition saved with dd is mostly empty: the plan must count what it holds,
    not what it measures packed or unpacked.
    """
    stream, process = open_stream(path, kind)
    try:
        head = stream.read(2048)
    finally:
        stream.close()
        if process:
            process.kill()
            process.wait()
    if len(head) < 2048 or struct.unpack_from('<H', head, 1080)[0] != 0xEF53:
        return None
    block = 1024 << struct.unpack_from('<I', head, 1048)[0]
    blocks = struct.unpack_from('<I', head, 1028)[0] | struct.unpack_from('<I', head, 1024 + 0x150)[0] << 32
    free = struct.unpack_from('<I', head, 1036)[0] | struct.unpack_from('<I', head, 1024 + 0x158)[0] << 32
    if not blocks or free > blocks or block > 65536:
        return None
    return blocks * block, (blocks - free) * block


def open_counted(path, kind):
    """The decompressed stream, plus how far into the packed file it has read."""
    raw = open(path, 'rb')
    if kind == 'zstd':
        # zstd reads the same open file, so its offset is the packed position.
        process = subprocess.Popen([ZSTD, '-dc'], stdin=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return process.stdout, process, raw, lambda: os.lseek(raw.fileno(), 0, os.SEEK_CUR)
    if not kind:
        return raw, None, raw, raw.tell
    stream = {'xz': lzma.open, 'gzip': gzip.open, 'bzip2': bz2.open}[kind](raw, 'rb')
    return stream, None, raw, raw.tell


def write_sparse(stream, out, report=None):
    """Copy a decompressed image, leaving every empty chunk as a hole.

    The free part of a saved partition then costs no space, and a filesystem that
    would fill the volume stops the copy with room still left for the system.
    """
    size = 0
    while True:
        block = stream.read(CHUNK)
        if not block:
            break
        if block == ZEROS[:len(block)]:
            out.seek(len(block), os.SEEK_CUR)
        else:
            # Metadata is scattered through a filesystem: only its non-empty 64 KiB pieces are written.
            view = memoryview(block)
            for start in range(0, len(block), SPARSE_STEP):
                piece = view[start:start + SPARSE_STEP]
                if piece == ZEROS[:len(piece)]:
                    out.seek(len(piece), os.SEEK_CUR)
                else:
                    out.write(piece)
            info = os.fstatvfs(out.fileno())
            if info.f_bavail * info.f_frsize < KEEP_FREE:
                fail('INSUFFICIENT_SPACE', 'The unpacked image does not fit on this volume')
        size += len(block)
        if report:
            report(size)
    out.truncate(size)
    return size


def tar_stream(path, kind):
    stream, process = open_stream(path, kind)
    head = stream.read(512)
    stream.close()
    if process:
        process.kill()
        process.wait()
    return head[257:262] == b'ustar'


def member_name(value):
    """A tar member's path inside the root: leading "./" and "/" go, "." parts go; None for an
    empty name or one with "..", which could leave the root."""
    parts = [part for part in str(value).split('/') if part not in ('', '.')]
    if not parts or '..' in parts:
        return None
    return '/'.join(parts)


def extract_tar(path, kind, root, progress=None):
    stream, process, raw, position = open_counted(path, kind)
    try:
        total, members = os.stat(path).st_size, 0
        with tarfile.open(fileobj=stream, mode='r|', errors='surrogateescape') as archive:
            archive.extraction_filter = tarfile.fully_trusted_filter
            for item in archive:
                members += 1
                if progress and members % 200 == 0:
                    progress('image-unpack', position(), total)
                # 2026-11-12 review: lstrip('./') turned "/etc/shadow" and "../../etc/shadow" into
                # "etc/shadow" for the checks, while tarfile wrote the member under its own name: out of
                # ROOT, as root. The member is written under the checked name only, and never through a link.
                name = member_name(item.name)
                if name is None or item.isdev() or item.isfifo():
                    continue
                item.name = name
                # The recovery runs as root: no member may write through a symlink out of ROOT.
                walked = root
                for part in Path(name).parts[:-1]:
                    walked = walked / part
                    if walked.is_symlink():
                        fail('UNSAFE_ARCHIVE', f'Archive path passes through a link: {name}')
                if item.islnk():
                    link = member_name(item.linkname)
                    if link is None:
                        fail('UNSAFE_ARCHIVE', f'Unsafe hardlink: {name}')
                    walked = root
                    for part in Path(link).parts:
                        walked = walked / part
                        if walked.is_symlink():
                            fail('UNSAFE_ARCHIVE', f'Unsafe hardlink: {name}')
                    item.linkname = link
                place = root / name
                if place.is_symlink() or (place.exists() and not place.is_dir()):
                    place.unlink()  # a later member replaces an earlier one, never writes through it
                elif place.is_dir() and not item.isdir():
                    fail('UNSAFE_ARCHIVE', f'A file replaces a folder: {name}')
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                archive.extract(item, root, numeric_owner=True)
                target = root / name
                for key, value in item.pax_headers.items():
                    if key.startswith('SCHILY.xattr.') and key[13:].startswith(XATTRS) and not item.issym():
                        os.setxattr(target, key[13:], value.encode('utf-8', 'surrogateescape'))
    finally:
        # A refused member must not leave the packed file or the decompressor behind.
        if process:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
        raw.close()


def describe(path):
    """A quick look for the plan: no mount and no extraction."""
    path = Path(path)
    kind = compression(path)
    if tar_stream(path, kind):
        return {'format': 'tar', 'compression': kind, 'factor': 4}
    if kind:
        info = {'format': 'compressed-image', 'compression': kind, 'factor': 6}
        usage = ext_usage(path, kind)
        if usage:
            info.update(unpacked_bytes=usage[0], used_bytes=usage[1])
        return info
    disk = disk_format(path)
    if disk:
        return {'format': disk, 'compression': None, 'factor': 6 if disk == 'qcow2' else 3}
    inner = fs_type(path)
    if inner == 'iso':
        return {'format': 'iso', 'compression': None, 'factor': 4}
    if inner:
        return {'format': inner, 'compression': None, 'factor': 2}
    if partitions(path):
        return {'format': 'disk', 'compression': None, 'factor': 2}
    fail('UNKNOWN_IMAGE', 'This file is not a recognised system image. Supported: ISO, raw or img disk images, qcow2, VHD, Vagrant box and tar archives, also compressed with xz, zstd, gzip or bzip2.')


CORRUPT_MESSAGE = ('The image file is damaged or incomplete. Copy it again, then eject the stick safely '
                   'before unplugging it.')


def extract(path, work, root, progress=None):
    """Fill the empty directory ROOT from the image at PATH; a damaged file is named as such."""
    try:
        return _extract(path, work, root, progress)
    except (lzma.LZMAError, zlib.error, EOFError, gzip.BadGzipFile, tarfile.ReadError) as exc:
        # 2026-11-08: a copy cut short on an exFAT stick stopped the import with a bare LZMAError.
        raise ImageError('IMAGE_CORRUPT', CORRUPT_MESSAGE) from exc
    except OSError as exc:
        if 'Invalid data stream' in str(exc):
            raise ImageError('IMAGE_CORRUPT', CORRUPT_MESSAGE) from exc
        raise


def _extract(path, work, root, progress=None):
    path, work, root = Path(path), Path(work), Path(root)
    kind = compression(path)
    if tar_stream(path, kind):
        extract_tar(path, kind, root, progress)
        if not is_root(root):
            # Android-style phone images ship a rootfs image inside the archive.
            # A Vagrant box holds box.img (qcow2); other archives hold a raw, qcow2 or VHD disk.
            images = sorted((p for name in DISK_NAMES for p in root.glob(name) if p.is_file() and not p.is_symlink()),
                            key=lambda p: -p.stat().st_size)
            if images:
                image = work / 'rootfs.img'
                images[0].rename(image)
                # 2026-11-12 review: a folder beside the image (include/, boot/) failed with
                # IsADirectoryError; folders go whole, never through a link.
                for child in list(root.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                if not locate(image, work, root, 0, progress):
                    fail('NO_ROOT_FILESYSTEM', 'No Linux root filesystem was found in this archive')
                image.unlink()
    else:
        image = path
        if kind:
            image = work / 'decompressed.img'
            stream, process, raw, position = open_counted(path, kind)
            total = path.stat().st_size
            report = (lambda unpacked: progress('image-unpack', position(), total)) if progress else None
            try:
                with open(image, 'wb') as out:
                    write_sparse(stream, out, report)
            except BaseException:
                # The decompressor must not outlive a copy that stopped.
                if process:
                    process.kill()
                    process.wait()
                raw.close()
                raise
            failed = bool(process and process.wait())
            raw.close()
            if failed:
                fail('IMAGE_CORRUPT', CORRUPT_MESSAGE)
        if not locate(image, work, root, 0, progress):
            fail('NO_ROOT_FILESYSTEM', 'No Linux root filesystem was found in this image')
        if kind:
            image.unlink()
    if not is_root(root):
        fail('NO_ROOT_FILESYSTEM', 'This archive is not a Linux root filesystem')
