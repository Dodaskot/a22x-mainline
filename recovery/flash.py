#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Flash one image to one partition from any mounted volume, without a computer.

targets  lists what this phone has and this tool may write.
check    reads the whole image and reports what it is; nothing is written.
         With --target auto it finds the partition itself: a boot image goes to
         BOOT, a filesystem image to userdata. That is Restore from an image.
write    saves a small partition first when it can, writes, then reads it back.

Only the partitions of a normal Android image are offered. Recovery is left out,
so a flash can never remove the way back. Bootloaders, modem firmware and
calibration data are never reachable from here.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootimage
import filesystem as fs
import install
import lp_metadata as lp
import saved_boot
import sevenzip
from install import require
from operation_lock import operation_lock

TARGETS = (('boot', 'Kernel and ramdisk'), ('dtbo', 'Device tree overlays'),
           ('vbmeta', 'Verified boot data'), ('vbmeta_system', 'Verified boot data for system'),
           ('vbmeta_vendor', 'Verified boot data for vendor'), ('super', 'Android system partitions'),
           ('prism', 'Samsung region data'), ('optics', 'Samsung carrier data'),
           # The recovery runs from RAM, so its own partition can be replaced while it runs.
           ('recovery', 'This recovery, from a new image'))
LABELS = tuple(label for label, title in TARGETS)
# A logical partition inside super, as super/NAME (2026-11-10): a GSI's or a ROM's system.img goes
# into super/system, which grows in the free space of super when the image is larger.
LOGICAL_PREFIX = 'super/'


def logical_name(label):
    """NAME for super/NAME, else None."""
    if not isinstance(label, str) or not label.startswith(LOGICAL_PREFIX):
        return None
    name = label[len(LOGICAL_PREFIX):]
    return name if 0 < len(name) < 36 and all(c.isascii() and (c.islower() or c.isdigit() or c == '_') for c in name) else None


def target_choice(value):
    if value in LABELS + ('userdata', 'auto') or logical_name(value):
        return value
    raise argparse.ArgumentTypeError('unknown partition')
RESTORE_LABELS = ('userdata', 'boot')
LZ4 = '/bin/lz4'
ZSTD = '/usr/bin/zstd'
LZ4_MAGICS = (b'\x04"M\x18', b'\x02!L\x18')
ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
XZ_MAGIC = b'\xfd7zXZ\x00'
CHECKSUM_FILE_LIMIT = 1 << 20
PREFIX = 8192
ROLLBACK_LIMIT = 64 * 1024 * 1024
ROLLBACK_FOLDER = 'A22xPartitionBackups'
BLKFLSBUF = 0x1261


class Lz4Reader:
    """install.ImageReader's reads through the lz4 tool, for Samsung .img.lz4 files."""
    def __init__(self, fd):
        os.lseek(fd, 0, os.SEEK_SET)
        self.process = subprocess.Popen((LZ4, '-dc'), stdin=fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.payload_bytes = self.sparse = None
        self.total = 0

    def read(self, count):
        data = bytearray()
        while len(data) < count:
            part = self.process.stdout.read(count - len(data))
            if not part:
                break
            data.extend(part)
        self.total += len(data)
        require(self.total <= install.MAX_SOURCE, 'IMAGE_EXPANSION_LIMIT', 'The image is larger than any partition')
        return bytes(data)

    def exact(self, count):
        data = self.read(count)
        require(len(data) == count, 'SHORT_IMAGE', 'The image ended early')
        return data

    def finish(self):
        require(not self.read(1), 'IMAGE_TRAILING_DATA', 'The image has extra data at its end')
        require(self.process.wait() == 0, 'INVALID_LZ4', 'The lz4 file is damaged')

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.stdout.close()
        self.process.wait()


class DecodedReader:
    """One file decoded as a stream, for zstd, xz and partitions above the installer's 8 GiB source bound.

    zstd and lz4 run as their tools on the pinned file; gzip and xz decode in Python.
    The stream must end exactly where the file ends, and the file must not change.
    """
    def __init__(self, fd, compression, limit):
        self.fd, self.compression, self.limit = fd, compression, limit
        self.before = os.fstat(fd)
        require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1, 'IMAGE_REGULAR_ONLY', 'Choose a regular file')
        self.position = self.total = 0
        self.payload_bytes = self.sparse = None
        self.process = self.decoder = None
        # Where the stream ends in the file: its end, or the end of the image inside a ZIP.
        self.end, self.crc, self.member_crc = self.before.st_size, None, None
        if compression in ('zstd', 'lz4'):
            os.lseek(fd, 0, os.SEEK_SET)
            self.process = subprocess.Popen((ZSTD if compression == 'zstd' else LZ4, '-dc'), stdin=fd,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        elif compression == 'gzip':
            self.decoder = zlib.decompressobj(31)
        elif compression == 'xz':
            self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        elif compression.startswith('tar:'):
            # 2026-11-10: Samsung's firmware (AP_..., CSC_....tar.md5) holds its images as LZ4
            # files inside a tar. The image is read from its place in the tar, through lz4, and
            # nothing is unpacked beside it.
            member = compression[4:]
            self.position, self.end = tar_member(fd, member)
            if member.lower().endswith('.lz4'):
                self.process = subprocess.Popen((LZ4, '-dc'), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL)
                self.fed = 0
                self.feeder = threading.Thread(target=self._feed, daemon=True)
                self.feeder.start()
        elif compression == '7z' or compression.startswith('7z:'):
            # 2026-11-10: LineageOS and other GSIs come as .7z; the image is decoded from it in place.
            try:
                files, info = sevenzip.entries(fd)
                self.sevenzip = sevenzip.EntryReader(fd, info, sevenzip.choose(files, compression[3:] or None))
            except sevenzip.SevenZipError as error:
                fs.fail(error.code, str(error))
        elif compression == 'zip' or compression.startswith('zip:'):
            # 2026-11-10: GSIs come as ZIPs (system.img, vbmeta.img, build.prop); the image is
            # decoded straight from the ZIP, with its CRC, and nothing is unpacked beside it.
            _, self.position, self.end, method, self.member_crc = zip_member(fd, compression[4:] or None)
            self.crc = 0
            self.decoder = zlib.decompressobj(-15) if method == zipfile.ZIP_DEFLATED else None
        else:
            require(compression == 'none', 'UNSUPPORTED_COMPRESSION', 'This compression is not supported')

    def _feed(self):
        """The tar member's bytes into lz4, from where the member starts to where it ends."""
        at = self.position
        try:
            while at < self.end:
                data = os.pread(self.fd, min(1 << 20, self.end - at), at)
                if not data:
                    break
                self.process.stdin.write(data)
                at += len(data)
                self.fed = at - self.position
        except OSError:  # lz4 stopped early: its exit status tells
            pass
        finally:
            with contextlib.suppress(OSError):
                self.process.stdin.close()

    def _file(self, count):
        count = max(0, min(count, self.end - self.position))
        data = os.pread(self.fd, count, self.position) if count else b''
        self.position += len(data)
        return data

    def _decode(self, want):
        if getattr(self, 'sevenzip', None) is not None:
            try:
                return self.sevenzip.read(want)
            except sevenzip.SevenZipError as error:
                fs.fail(error.code, str(error))
            except lzma.LZMAError:
                fs.fail('INVALID_COMPRESSED_IMAGE', 'The compressed file is damaged')
        if self.process:
            return self.process.stdout.read(want)
        if self.decoder is None:
            return self._file(want)
        if isinstance(self.decoder, lzma.LZMADecompressor):
            while not self.decoder.eof:
                data = self._file(1 << 20) if self.decoder.needs_input else b''
                require(data or not self.decoder.needs_input, 'SHORT_IMAGE', 'The image ended early')
                out = self.decoder.decompress(data, max_length=want)
                if out:
                    return out
            return b''
        while not self.decoder.eof:
            data = self.decoder.unconsumed_tail or self._file(1 << 20)
            require(data, 'SHORT_IMAGE', 'The image ended early')
            out = self.decoder.decompress(data, want)
            if out:
                return out
        return b''

    def read(self, count):
        data = bytearray()
        while len(data) < count:
            part = self._decode(count - len(data))
            if not part:
                break
            data.extend(part)
            if self.crc is not None:
                self.crc = zlib.crc32(part, self.crc)
        self.total += len(data)
        require(self.total <= self.limit, 'IMAGE_EXPANSION_LIMIT', 'The image is larger than the partition')
        return bytes(data)

    def exact(self, count):
        data = self.read(count)
        require(len(data) == count, 'SHORT_IMAGE', 'The image ended early')
        return data

    def finish(self):
        require(not self.read(1), 'IMAGE_TRAILING_DATA', 'The image has extra data at its end')
        if getattr(self, 'sevenzip', None) is not None:
            try:
                self.sevenzip.finish()
            except sevenzip.SevenZipError as error:
                fs.fail('INVALID_COMPRESSED_IMAGE' if error.code == 'DAMAGED_7Z' else error.code, str(error))
        elif self.process:
            require(self.process.wait() == 0, 'INVALID_COMPRESSED_IMAGE', 'The compressed file is damaged')
            if getattr(self, 'feeder', None) is not None:
                self.feeder.join()
                require(self.fed == self.end - self.position, 'SHORT_IMAGE', 'The image ended early')
        elif self.decoder is not None:
            require(self.decoder.eof and not self.decoder.unused_data and self.position == self.end,
                    'IMAGE_TRAILING_DATA', 'The image has extra data at its end')
        else:
            require(self.position == self.end, 'SHORT_IMAGE', 'The image ended early')
        if self.member_crc is not None:
            require(self.crc == self.member_crc, 'INVALID_COMPRESSED_IMAGE', 'The image inside the ZIP is damaged')
        require(fs.stamp(os.fstat(self.fd)) == fs.stamp(self.before), 'IMAGE_CHANGED', 'The file changed while it was read')

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.stdout.close()
            self.process.wait()
            if getattr(self, 'feeder', None) is not None:
                self.feeder.join(10)


def tar_member(fd, member):
    """Where MEMBER's data starts and ends inside a tar. Samsung's .tar.md5 is a plain tar with
    its MD5 written after it; tarfile stops at the tar's own end."""
    with shared_offset(fd) as handle, tarfile.open(fileobj=handle, mode='r:') as archive:
        try:
            info = archive.getmember(member)
        except KeyError:
            fs.fail('TAR_MEMBER_MISSING', 'The package has no such image', member=member)
        require(info.isreg() and info.size > 0, 'TAR_MEMBER_MISSING', 'The package has no such image', member=member)
        return info.offset_data, info.offset_data + info.size


@contextlib.contextmanager
def shared_offset(fd):
    """FD as a file read from its start; a dup shares the offset, so it is put back afterwards."""
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        with os.fdopen(os.dup(fd), 'rb') as handle:
            handle.seek(0)
            yield handle
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def zip_member(fd, member=None):
    """The image inside a ZIP: MEMBER when named, else system.img when there is one (a GSI also
    carries vbmeta.img and build.prop), else its only image. Its name, where its data starts and
    ends in the file, its method (stored or deflated) and CRC."""
    with shared_offset(fd) as handle, zipfile.ZipFile(handle) as archive:
        images = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith('.img')]
        if member:
            chosen = next((i for i in images if i.filename == member), None)
            require(chosen is not None, 'ZIP_MEMBER_MISSING', 'The ZIP has no such image', member=member)
        else:
            chosen = next((i for i in images if i.filename.rsplit('/', 1)[-1].lower() == 'system.img'), None)
        if chosen is None:
            require(len(images) == 1, 'ZIP_AMBIGUOUS', 'The ZIP holds no image or several: keep one image in it')
            chosen = images[0]
        require(not chosen.flag_bits & 1, 'ZIP_ENCRYPTED', 'The image inside the ZIP is encrypted')
        require(chosen.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'UNSUPPORTED_COMPRESSION',
                'The ZIP compresses its image in a way this recovery cannot read')
    local = os.pread(fd, 30, chosen.header_offset)
    require(len(local) == 30 and local[:4] == b'PK\x03\x04', 'INVALID_ZIP', 'The ZIP is damaged')
    name_bytes, extra_bytes = struct.unpack_from('<HH', local, 26)
    start = chosen.header_offset + 30 + name_bytes + extra_bytes
    return chosen.filename, start, start + chosen.compress_size, chosen.compress_type, chosen.CRC


def open_reader(fd, compression, size=0):
    if size > install.MAX_SOURCE or os.fstat(fd).st_size > install.MAX_SOURCE or compression in ('zstd', 'xz') or compression[:4] in ('zip', 'zip:', 'tar:') or compression[:2] == '7z':
        return DecodedReader(fd, compression, size or install.MAX_SOURCE)
    return Lz4Reader(fd) if compression == 'lz4' else install.ImageReader(fd, compression)


def close_reader(reader):
    if isinstance(reader, (Lz4Reader, DecodedReader)):
        reader.close()


def container(fd, member=None):
    """The file's compression. A ZIP is 'zip' (its system.img, or its only image) or 'zip:NAME'
    for MEMBER, one image chosen inside it (a GSI's vbmeta.img); a tar (Samsung's firmware) is
    'tar:NAME', always with the image named."""
    head = os.pread(fd, 6, 0)
    if head == sevenzip.SIGNATURE:
        return '7z:' + member if member else '7z'
    if head[:4] == b'PK\x03\x04':
        return 'zip:' + member if member else 'zip'
    if os.pread(fd, 5, 257) == b'ustar':
        require(member, 'TAR_MEMBER_REQUIRED', 'Choose an image inside this firmware file')
        return 'tar:' + member
    require(not member, 'NOT_A_ZIP', 'This file is not a ZIP or a tar')
    if head[:2] == b'\x1f\x8b':
        return 'gzip'
    if head[:4] in LZ4_MAGICS:
        return 'lz4'
    if head[:4] == ZSTD_MAGIC:
        return 'zstd'
    return 'xz' if head == XZ_MAGIC else 'none'


def layout(fd, compression, size=0):
    reader = open_reader(fd, compression, size)
    try:
        head = reader.read(4)
    finally:
        close_reader(reader)
    return 'sparse' if len(head) == 4 and struct.unpack('<I', head)[0] == install.SPARSE_MAGIC else 'raw'


def filesystem_kind(prefix):
    if prefix[1080:1082] == b'\x53\xef':
        return 'ext4 filesystem image'
    if prefix[1024:1028] == b'\x10\x20\xf5\xf2':
        return 'F2FS filesystem image'
    return None


def detect(fd, compression):
    """Which partition an image belongs on, from its first bytes."""
    largest = install.GEOMETRY['userdata'][2]
    image_format = layout(fd, compression, largest)
    reader = open_reader(fd, compression, largest)
    prefix = bytearray()
    try:
        for data in install.decode_image(reader, image_format, largest):
            prefix.extend(data)
            if len(prefix) >= PREFIX:
                break
    finally:
        close_reader(reader)
    if bytes(prefix[:8]) == b'ANDROID!':
        return 'boot'
    if filesystem_kind(prefix):
        return 'userdata'
    fs.fail('UNKNOWN_IMAGE_TARGET', 'Choose an image of userdata or BOOT')


def classify(label, prefix, payload):
    """Name the image, or refuse it when it belongs on another partition."""
    if label == 'userdata':
        kind = filesystem_kind(prefix)
        require(kind, 'WRONG_IMAGE_TYPE', 'This file is not a filesystem image for userdata')
        return kind, ['Everything on userdata is replaced by this image']
    if label == 'boot':
        require(prefix[:8] == b'ANDROID!', 'WRONG_IMAGE_TYPE', 'This file is not an Android boot image')
        try:
            install.boot_header(bytes(prefix), payload)
        except fs.FilesystemError as error:
            require(error.code != 'RECOVERY_IMAGE_FOR_BOOT', 'WRONG_IMAGE_TYPE', 'This is a recovery image, not a boot image')
            return 'Android boot image', ['The header differs from the usual A22x boot layout']
        return 'Android boot image', []
    if label == 'recovery':
        require(prefix[:8] == b'ANDROID!', 'WRONG_IMAGE_TYPE', 'This file is not an A22x recovery image')
        try:
            install.boot_header(bytes(prefix), payload, recovery=True)
        except fs.FilesystemError:
            fs.fail('WRONG_IMAGE_TYPE', 'This file is not an A22x recovery image')
        return 'A22x recovery image', ['The new recovery starts at the next restart into recovery. If it does not start, flash a working one from a computer in Download Mode.']
    if label == 'dtbo':
        require(prefix[:4] == b'\xd7\xb7\xab\x1e', 'WRONG_IMAGE_TYPE', 'This file is not a DTBO image')
        return 'Device tree overlays', []
    if label.startswith('vbmeta'):
        require(prefix[:4] == b'AVB0', 'WRONG_IMAGE_TYPE', 'This file is not a vbmeta image')
        return 'Verified boot data', []
    if label == 'super':
        require(prefix[4096:4100] == b'gDla', 'WRONG_IMAGE_TYPE', 'This file is not a super image')
        return 'Android super image', []
    if prefix[1080:1082] == b'\x53\xef':
        return 'ext4 filesystem image', []
    return 'Raw image', ['The file type could not be recognized']


def saved_checksum(context, args, compression, value, target_bytes, payload_bytes):
    """A checksum kept next to the image: a dd transfer record, a manifest or a .sha256 file.

    Only a checksum of exactly the bytes being written is compared. A mismatch means
    a damaged copy, and nothing may be written from it.
    """
    relative = fs.clean_path(args.path)
    name = relative.rsplit('/', 1)[-1]
    found = []
    with fs.PinnedVolume(context.fs, args.volume, args.volume_token) as pin:
        with pin.parent(relative) as (parent, _):
            def load(sibling):
                try:
                    fd = os.open(sibling, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                except OSError:
                    return None
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_size > CHECKSUM_FILE_LIMIT:
                        return None
                    return os.pread(fd, CHECKSUM_FILE_LIMIT, 0).decode('utf-8', 'replace')
                finally:
                    os.close(fd)

            def hexdigest(text):
                text = (text or '').strip().lower()
                return text if len(text) == 64 and all(c in '0123456789abcdef' for c in text) else None

            whole = payload_bytes == target_bytes
            try:
                record = json.loads(load(name + '.transport.json') or 'null')
            except ValueError:
                record = None
            if isinstance(record, dict) and whole and record.get('decoded_bytes') == payload_bytes and hexdigest(record.get('decoded_sha256')):
                found.append((name + '.transport.json', hexdigest(record.get('decoded_sha256'))))
            if compression == 'none' and whole:
                try:
                    manifest = json.loads(load('manifest.json') or 'null')
                except ValueError:
                    manifest = None
                entry = manifest.get(name) if isinstance(manifest, dict) else None
                if isinstance(entry, dict) and hexdigest(entry.get('sha256')):
                    found.append(('manifest.json', hexdigest(entry.get('sha256'))))
                listed = (load(name + '.sha256') or '').split()
                if listed and hexdigest(listed[0]):
                    found.append((name + '.sha256', hexdigest(listed[0])))
    for source, expected in found:
        require(expected == value, 'IMAGE_DAMAGED', 'The image does not match the checksum saved with it', checksum_file=source)
    return found[0][0] if found else None


def identity(fd):
    return hashlib.sha256(repr(fs.stamp(os.fstat(fd))).encode()).hexdigest()


@contextlib.contextmanager
def source_file(context, volume, token, path):
    relative = fs.clean_path(path)
    with fs.PinnedVolume(context.fs, volume, token) as pin:
        with pin.parent(relative) as (parent, name):
            handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                st = os.fstat(handle)
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'IMAGE_REGULAR_ONLY', 'Choose a regular file')
                pin.check_fd(handle)
                fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
            finally:
                os.close(handle)
        try:
            yield fd
        finally:
            os.close(fd)


def rollback_volume(context, size, skip_cache=False, make_room=False):
    """A mounted writable volume for the old content, removable storage first. Cache is small
    (200 MiB): with SKIP_CACHE it is passed over (the BOOT is already kept there once), and with
    MAKE_ROOM what lies there twice is removed before it is found full (2026-11-11)."""
    for volume in sorted(context.fs.volumes(), key=lambda v: (not v.get('external'), v['id'])):
        if (volume.get('mounted') and not volume.get('read_only') and volume.get('write_supported')
                and volume.get('mount_owner') in ('filesystem', 'storage')):
            cache = volume['id'] == 'cache'
            if cache and skip_cache:
                continue
            info = os.statvfs(volume['mountpoint'])
            if cache and make_room and info.f_bavail * info.f_frsize < size + install.CHUNK:
                with contextlib.suppress(fs.FilesystemError, OSError):
                    with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
                        saved_boot.make_room(pin)
                info = os.statvfs(volume['mountpoint'])
            if info.f_bavail * info.f_frsize >= size + install.CHUNK:
                return volume
    return None


def rollback_folders(volume):
    """Linux systems live at the top of userdata, so an old partition goes into its image store there
    and never into a system's root (2026-11-10); elsewhere it goes to a folder of its own."""
    if (volume.get('label') or volume['id']) == 'userdata':
        return ('.a22x-images', ROLLBACK_FOLDER)
    return (ROLLBACK_FOLDER,)


def save_rollback(context, target, progress, chunks=None, skip_cache=False):
    """The current content of TARGET, kept before it is written. CHUNKS: its bytes in order, for a
    partition inside super (2026-11-10); otherwise they are read from the target's own device.
    On cache only the newest copy of a partition stays: the one written here."""
    if target['size_bytes'] > ROLLBACK_LIMIT:
        return None
    volume = rollback_volume(context, target['size_bytes'], skip_cache, make_room=True)
    if volume is None:
        return None
    stamp = target['label'].replace('/', '-') + time.strftime('-%Y%m%d-%H%M%S', time.gmtime())
    source = context.open_target(target) if chunks is None else None

    def own():
        done = 0
        while done < target['size_bytes']:
            data = os.pread(source, min(install.CHUNK, target['size_bytes'] - done), done)
            require(data, 'TARGET_READ_FAILED', 'The current partition could not be read')
            done += len(data)
            yield data
    try:
        with fs.PinnedVolume(context.fs, volume['id'], writing=True) as pin:
            parts = rollback_folders(volume)
            folder = os.dup(pin.fd)
            try:
                for part in parts:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, 0o700, dir_fd=folder)
                    inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=folder)
                    os.close(folder)
                    folder = inner
                    pin.check_fd(folder)
                # Two flashes within one second must not lose the older copy.
                for attempt in range(100):
                    name = stamp + ('-%d.img' % attempt if attempt else '.img')
                    with contextlib.suppress(FileExistsError):
                        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=folder)
                        break
                else:
                    fs.fail('ROLLBACK_NAME_EXHAUSTED', 'The old content could not be saved')
                try:
                    done = 0
                    for data in (own() if chunks is None else chunks):
                        install.write_all(out, data)
                        done += len(data)
                        progress('Saving the current partition', target, done)
                    require(done == target['size_bytes'], 'TARGET_READ_FAILED', 'The current partition could not be read')
                    os.fsync(out)
                except BaseException:
                    # A copy cut short is not a copy: nothing half-saved stays behind.
                    with contextlib.suppress(OSError):
                        os.unlink(name, dir_fd=folder)
                    raise
                finally:
                    os.close(out)
                if volume['id'] == 'cache':
                    saved_boot.prune_rollbacks(folder, keep=name)
            finally:
                os.close(folder)
    finally:
        if source is not None:
            os.close(source)
    return {'volume': volume.get('label') or volume['id'], 'path': '/' + '/'.join(parts) + '/' + name}


def open_super(context, write=False):
    """super's target, an open descriptor and its parsed metadata."""
    target = context.target('super')
    fd = context.open_target(target, write=write)
    try:
        return target, fd, lp.Super(fd)
    except BaseException:
        os.close(fd)
        raise


def targets(context):
    rows = []
    for label, title in TARGETS:
        try:
            rows.append({'label': label, 'title': title, 'size_bytes': context.target(label)['size_bytes']})
        except (OSError, fs.FilesystemError):
            continue
    extra = {}
    try:
        _, fd, found = open_super(context)
    except (OSError, fs.FilesystemError, lp.MetadataError):
        found = None
    if found is not None:
        try:
            for p in found.metadata['partitions']:
                rows.append({'label': LOGICAL_PREFIX + p['name'], 'title': 'Logical partition in super',
                             'size_bytes': found.size_of(p), 'logical': True, 'room_bytes': found.room(p)})
            extra = {'super_free_bytes': found.describe()['free_bytes']}
        finally:
            os.close(fd)
    return {'targets': rows, **extra}


def image_kind(prefix):
    """What a logical partition image holds, from its first bytes."""
    if len(prefix) >= 1028 and struct.unpack_from('<I', prefix, 1024)[0] == 0xE0F5E1E2:
        return 'EROFS file system image'
    if len(prefix) >= 1082 and struct.unpack_from('<H', prefix, 1080)[0] == 0xEF53:
        return 'ext4 file system image'
    return 'Raw image'


def check_logical(context, args, progress, name):
    """An image for a logical partition: it may be smaller than the partition (the rest is
    zeroed) or larger, when super and the partition's group have room for it to grow."""
    with source_file(context, args.volume, args.volume_token, args.path) as fd:
        size = os.fstat(fd).st_size
        compression = container(fd, getattr(args, 'member', None))
        super_target, sfd, found = open_super(context)
        try:
            p = found.partition(name)
            current, room = found.size_of(p), found.room(p)
            limit = current + room
            image_format = layout(fd, compression, limit)
            reader = open_reader(fd, compression, limit)
            digest, prefix, count = hashlib.sha256(), bytearray(), 0
            shown = {'label': args.target, 'size_bytes': limit}
            try:
                # The decoder adds zeros up to LIMIT after the image; the image ends where it
                # has counted its own bytes (payload_bytes), and only that is checked.
                for data in install.decode_image(reader, image_format, limit):
                    if reader.payload_bytes is not None:
                        break
                    if len(prefix) < PREFIX:
                        prefix.extend(data[:PREFIX - len(prefix)])
                    digest.update(data)
                    count += len(data)
                    progress('Checking the image', shown, count)
            finally:
                close_reader(reader)
            align = max(found.metadata['devices'][0]['alignment'], lp.SECTOR)
            # An image that fits keeps the partition as it is; only a larger one makes it grow, to
            # super's alignment (2026-11-10: odm of 4,349,952 bytes, not a whole MiB, and its own
            # image would have been shown "growing" to 5 MiB).
            grown = current if count <= current else -(-count // align) * align
            require(count > 0, 'IMAGE_EMPTY', 'The image is empty')
            # As for the partitions outside super: a small one is compared with the image (and
            # the zeros after it), and its content is saved before it is written.
            small = current <= ROLLBACK_LIMIT
            identical = False
            if small and count <= current:
                padded, left = digest.copy(), current - count
                while left:
                    padded.update(bytes(min(left, 1 << 20)))
                    left -= min(left, 1 << 20)
                identical = found.read_partition(name) == padded.hexdigest()
        finally:
            os.close(sfd)
        volume = rollback_volume(context, current) if small else None
        warnings = []
        if not identical and volume is None:
            warnings.append('The current partition will not be saved first' if small else 'This partition is too large to save first')
        try:
            context.check_offline([super_target])
            offline = True
        except (OSError, fs.FilesystemError):
            offline = False
            warnings.append('The partition is mounted. Unmount it in Files before writing')
        return {'target': {'label': args.target, 'size_bytes': current, 'new_size_bytes': grown, 'offline': offline, 'logical': True},
                'image': {'name': Path(args.path).name[:200], 'kind': image_kind(prefix), 'compression': compression,
                          'format': image_format, 'file_bytes': size, 'payload_bytes': count, 'sha256': digest.hexdigest(),
                          'identity': identity(fd), 'checksum_file': None},
                'identical': identical, 'warnings': warnings, 'contents': None,
                'rollback_volume': (volume.get('label') or volume['id']) if volume else None}


def write_logical(context, args, progress, name):
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Writing a partition must be confirmed')
    require(isinstance(args.image_bytes, int) and args.image_bytes > 0, 'INVALID_ARGUMENT', 'The size of the checked image is required')
    with operation_lock(context.lock_path, context.mode):
        require(not context.guard_path.exists(), 'INSTALL_PENDING', 'Finish the unfinished installation first')
        super_target = context.target('super')
        context.check_offline([super_target])
        with source_file(context, args.volume, args.volume_token, args.path) as fd:
            require(identity(fd) == args.expected_identity, 'IMAGE_CHANGED', 'The file changed after it was checked')
            compression = container(fd, getattr(args, 'member', None))
            _, sfd, found = open_super(context, write=True)
            try:
                current = found.size_of(found.partition(name))
                # The old content first, before super's metadata or the partition changes.
                rollback = save_rollback(context, {'label': args.target, 'size_bytes': current}, progress, found.chunks(name))
                if args.image_bytes > current:
                    found.resize(name, args.image_bytes)  # The metadata, every copy, read back.
                total = found.size_of(found.partition(name))
                image_format = layout(fd, compression, total)
                reader = open_reader(fd, compression, total)
                shown = {'label': args.target, 'size_bytes': total}
                image, length = hashlib.sha256(), 0

                def stream():
                    nonlocal length
                    for data in install.decode_image(reader, image_format, total):
                        if reader.payload_bytes is None:  # The image itself; then the zeros to the end.
                            image.update(data)
                            length += len(data)
                        yield data
                try:
                    _, _, whole_sha = found.write_chunks(name, stream(), lambda done, all_: progress('Writing', shown, done))
                finally:
                    close_reader(reader)
                image_sha = image.hexdigest()
                if context.mode == 'device':
                    fcntl.ioctl(sfd, BLKFLSBUF)
                if length != args.image_bytes or image_sha != args.expected_sha256:
                    fs.fail('IMAGE_CHANGED', 'The file changed during the write. Flash the partition again from a good image',
                            rollback=rollback)
                observed = found.read_partition(name)
                if observed != whole_sha:
                    fs.fail('READBACK_MISMATCH', 'The partition does not match the image after writing', rollback=rollback)
            finally:
                os.close(sfd)
        return {'target': {'label': args.target, 'size_bytes': total, 'logical': True, 'resized_from': current},
                'sha256': image_sha, 'partition_sha256': observed, 'verified': True, 'rollback': rollback, 'recorded': False}


def judge(label, data):
    """What a BOOT, recovery, dtbo or vbmeta image holds; an image for another phone, or one that
    is damaged or cut short, is refused before anything is written."""
    try:
        info = bootimage.inspect(label, data)
    except bootimage.ImageError as exc:
        fail_codes = {'BOOT_OTHER_PHONE': 'IMAGE_FOR_ANOTHER_PHONE', 'BOOT_PHONE_UNKNOWN': 'IMAGE_FOR_ANOTHER_PHONE'}
        require(False, fail_codes.get(exc.code, 'IMAGE_DAMAGED'), str(exc), check=exc.code)
    if info['kind'] != 'boot':
        return {'kind': info['kind']}
    android = info['android'] or {}
    return {'kind': 'boot', 'linux': info['kernel']['release'], 'mainline': info['mainline'],
            'android': android.get('version'), 'patch_level': android.get('patch_level'),
            'phone': info['device']['model'] or info['device']['device'], 'this_phone': info['device']['this_phone'],
            'avb': info['avb']['state']}


def check(context, args, progress):
    name = logical_name(args.target)
    if name:
        return check_logical(context, args, progress, name)
    with source_file(context, args.volume, args.volume_token, args.path) as fd:
        size = os.fstat(fd).st_size
        compression = container(fd, getattr(args, 'member', None))
        label = detect(fd, compression) if args.target == 'auto' else args.target
        target = context.target(label)
        image_format = layout(fd, compression, target['size_bytes'])
        reader = open_reader(fd, compression, target['size_bytes'])
        digest, prefix, count = hashlib.sha256(), bytearray(), 0
        # BOOT, recovery, dtbo and vbmeta are judged by their contents as well (2026-11-10).
        whole = bytearray() if label in bootimage.INSPECT and target['size_bytes'] <= ROLLBACK_LIMIT else None
        try:
            for data in install.fixed_chunks(install.decode_image(reader, image_format, target['size_bytes'])):
                if len(prefix) < PREFIX:
                    prefix.extend(data[:PREFIX - len(prefix)])
                if whole is not None:
                    whole.extend(data)
                digest.update(data)
                count += len(data)
                progress('Checking the image', target, count)
        finally:
            close_reader(reader)
        require(count == target['size_bytes'], 'IMAGE_SIZE_MISMATCH', 'The image does not fit this partition')
        kind, warnings = classify(label, prefix, reader.payload_bytes)
        contents = judge(label, bytes(whole)) if whole is not None else None
        value = digest.hexdigest()
        checksum = saved_checksum(context, args, compression, value, target['size_bytes'], reader.payload_bytes)
        try:
            context.check_offline([target])
            offline = True
        except (OSError, fs.FilesystemError):
            offline = False
            warnings.append('The partition is mounted. Unmount it in Files before writing')
        small = target['size_bytes'] <= ROLLBACK_LIMIT
        identical = small and install.target_hash(context, target) == value
        volume = rollback_volume(context, target['size_bytes']) if small else None
        # A BOOT of this phone is always kept on cache as a saved BOOT (saved_boot.keep_current_boot):
        # the page said it would not be saved (2026-11-11, on the phone).
        kept = bool(label == 'boot' and not identical and volume is None
                    and saved_boot.boot_kind(install.target_bytes(context, target))[0])
        if not identical and volume is None and not kept:
            warnings.append('The current partition will not be saved first' if small else
                            'This partition is too large to save first')
        return {'target': {'label': label, 'size_bytes': target['size_bytes'], 'offline': offline},
                'image': {'name': Path(args.path).name[:200], 'kind': kind, 'compression': compression, 'format': image_format,
                          'file_bytes': size, 'payload_bytes': reader.payload_bytes, 'sha256': value, 'identity': identity(fd),
                          'checksum_file': checksum},
                'identical': identical, 'warnings': warnings, 'contents': contents,
                'rollback_volume': (volume.get('label') or volume['id']) if volume else ('cache' if kept else None)}


# 2026-11-10, seen in LK's log on the phone: at every start LK write-protects the eMMC from nvram to
# btd ([Part_WP]WP(nvram->btd)), which holds dtbo and vbmeta. Writes there from a running system are
# dropped without an error (vbmeta read back unchanged). They change only in Samsung Download mode.
WRITE_PROTECTED = ('dtbo', 'vbmeta')


def write_protected(context):
    """The partitions the bootloader locks on this phone; a fixture names its own (none by default)."""
    if context.mode == 'device':
        return WRITE_PROTECTED
    return tuple((getattr(context, 'config', None) or {}).get('write_protected', ()))


def read_back(context, target, progress):
    fd = context.open_target(target)
    try:
        digest, done = hashlib.sha256(), 0
        while done < target['size_bytes']:
            data = os.pread(fd, min(install.CHUNK, target['size_bytes'] - done), done)
            require(data, 'TARGET_READ_FAILED', 'The partition could not be read back')
            digest.update(data)
            done += len(data)
            progress('Reading the partition back', target, done)
        return digest.hexdigest()
    finally:
        os.close(fd)


def write(context, args, progress):
    require(args.target != 'auto', 'INVALID_ARGUMENT', 'Name the partition to write')
    name = logical_name(args.target)
    if name:
        return write_logical(context, args, progress, name)
    require(args.confirm, 'CONFIRMATION_REQUIRED', 'Writing a partition must be confirmed')
    with operation_lock(context.lock_path, context.mode):
        require(not context.guard_path.exists(), 'INSTALL_PENDING', 'Finish the unfinished installation first')
        target = context.target(args.target)
        context.check_offline([target])
        if args.target in write_protected(context):
            # Already this image: nothing to write (the stock dtbo and vbmeta of a firmware). Another
            # image: refused before writing, since the bootloader drops the write.
            now = read_back(context, target, progress)
            if now == args.expected_sha256:
                return {'target': {'label': args.target, 'size_bytes': target['size_bytes']}, 'sha256': now, 'verified': True,
                        'unchanged': True, 'rollback': None, 'recorded': install.record_flash(context, args.target, now),
                        'kept_boot': None, 'kept_boot_note': None, 'keeps_recovery': False}
            reason = 'The bootloader locks this partition while the phone runs: flash it in Samsung Download mode (Odin or Heimdall)'
            fs.fail('PROTECTED_BY_BOOTLOADER', reason, partition=args.target)
        # 2026-11-10: the BOOT there (Linux, or Samsung's own) is kept on cache first, the way back.
        # Before the image is opened: it may itself lie on cache, which is remounted for the copy
        # (on the phone the copy failed while the image held cache open).
        kept = saved_boot.keep_current_boot(context, progress, args.expected_sha256) if args.target == 'boot' else None
        with source_file(context, args.volume, args.volume_token, args.path) as fd:
            require(identity(fd) == args.expected_identity, 'IMAGE_CHANGED', 'The file changed after it was checked')
            compression = container(fd, getattr(args, 'member', None))
            image_format = layout(fd, compression, target['size_bytes'])
            # A BOOT kept on cache as a saved BOOT is not kept there a second time.
            rollback = save_rollback(context, target, progress, skip_cache=bool(kept and (kept['kept'] or kept.get('note') == 'identical')))
            writer = context.open_target(target, write=True)
            reader = open_reader(fd, compression, target['size_bytes'])
            digest, position = hashlib.sha256(), 0
            try:
                for data in install.fixed_chunks(install.decode_image(reader, image_format, target['size_bytes'])):
                    context.check_target(writer, target)
                    install.write_all(writer, data, offset=position)
                    digest.update(data)
                    position += len(data)
                    progress('Writing', target, position)
                os.fsync(writer)
                if context.mode == 'device':
                    fcntl.ioctl(writer, BLKFLSBUF)
            finally:
                close_reader(reader)
                os.close(writer)
        require(digest.hexdigest() == args.expected_sha256, 'IMAGE_CHANGED',
                'The file changed during the write. Flash the partition again from a good image', rollback=rollback)
        observed = read_back(context, target, progress)
        require(observed == args.expected_sha256, 'READBACK_MISMATCH',
                'The partition does not match the image after writing', rollback=rollback)
        # 2026-11-10: an Android BOOT gets the mark that keeps this recovery (saved_boot.keep_recovery):
        # Samsung's Android otherwise puts its own recovery back at its next start.
        marked = saved_boot.keep_recovery(context, target) if args.target == 'boot' else None
        # A normal boot then knows this BOOT (or vbmeta, dtbo) was put there on purpose.
        recorded = install.record_flash(context, args.target, marked['sha256'] if marked else observed)
        return {'target': {'label': args.target, 'size_bytes': target['size_bytes']}, 'sha256': observed,
                'verified': True, 'rollback': rollback, 'recorded': recorded, 'kept_boot': kept['kept'] if kept else None,
                'kept_boot_note': kept.get('note') if kept and not kept['kept'] else None,
                'keeps_recovery': bool(marked)}


def parser():
    result = argparse.ArgumentParser()
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('targets')
    for name in ('check', 'write'):
        command = commands.add_parser(name)
        command.add_argument('--target', type=target_choice, required=True)
        command.add_argument('--volume', required=True)
        command.add_argument('--volume-token', required=True)
        command.add_argument('--path', required=True)
        command.add_argument('--member', help='An image inside a ZIP or a tar, by its name there (a ZIP: system.img or its only image by default)')
    write_command = commands.choices['write']
    write_command.add_argument('--expected-sha256', required=True)
    write_command.add_argument('--expected-identity', required=True)
    write_command.add_argument('--confirm', action='store_true')
    write_command.add_argument('--image-bytes', type=int, help='A logical partition: the size of the checked image')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    last = [None, 0.0]

    def progress(phase, target, done):
        now = time.monotonic()
        if phase != last[0] or now - last[1] >= 0.2 or done == target['size_bytes']:
            print(json.dumps({'event': 'progress', 'operation': args.command, 'phase': phase, 'partition': target['label'],
                              'bytes_done': done, 'bytes_total': target['size_bytes']}, separators=(',', ':')),
                  file=sys.stderr, flush=True)
            last[:] = [phase, now]

    try:
        context = install.FixtureContext(args.fixture) if args.fixture else install.DeviceContext()
        if args.command == 'targets':
            result = targets(context)
        elif args.command == 'check':
            result = check(context, args, progress)
        else:
            result = write(context, args, progress)
        print(json.dumps({'ok': True, 'operation': args.command, **result}, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, fs.FilesystemError) as error:
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {
            'code': code, 'message': str(error), 'details': getattr(error, 'details', {})}}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
