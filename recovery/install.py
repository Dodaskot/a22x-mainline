#!/usr/bin/env python3
"""Narrow A22x BOOT/stock installation support.

Image decoding is bounded and deterministic. Android sparse DONT_CARE extents
expand to zero bytes, so a comparison or write never silently retains old data.
No executable supplied by a bundle is run. Android filesystem mounting, userdata reset,
bootloader/modem/PIT writes and automatic reboot are outside this helper.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import zlib

import bootimage
import filesystem as fs
from lp_metadata import drop_cached
from operation_lock import operation_lock, lease as borrowed_lease

CHUNK = 1024 * 1024
MAX_JSON = 2 * CHUNK
MAX_SOURCE = 8 * 1024 * 1024 * 1024
# Samsung firmware packs its images as LZ4 frames; the recovery decodes them with lz4(1).
LZ4 = os.environ.get('A22X_LZ4', '/bin/lz4')
# A whole partition decoded from a file, userdata of a 128 GB phone included (about 116 GiB);
# source files keep MAX_SOURCE.
MAX_TARGET = 128 * 1024 * 1024 * 1024
MAX_SPARSE_CHUNKS = 65536
MAX_HEADER = 4096
MAX_IMAGES = 8
PLAN_SECONDS = 30 * 60
MODEL = 'SM-A226B'
BOARD = 'SRPTL28A011'
SCHEMA = 'a22x.install.plan.v1'
BUNDLE_SCHEMA = 'a22x.stock.bundle.v1'
PROFILE_SCHEMA = 'a22x.install.stock-profile.v1'
PROFILE_PATH = Path('/etc/a22x-recovery/install-stock-profile.json')
RAM_GUARD = Path('/run/a22x-recovery/install-transaction.json')
JOURNAL_BASE = '/.a22x-installs'
RESERVE = 64 * CHUNK
LINUX_BOOT = 'b3f59daa37ad50e0ac6bfaed83f6954147536d07119f1b46a9981ee19d9b17ab'
LINUX_BOOT_K108 = 'f188b56b28a68c2f8087e45ff60e29ea5353cdbd0f3294a7efe89fe9d9477993'
# Kernel #107 BOOT with the charger-off switch in /init (2026-11-09).
LINUX_BOOT_CHARGER_OFF = 'b689f2dd5bb4f72a65665e1ebfcd5012b4126a21d7b427fa54c2d3f9cffc27f8'
LINUX_BOOT_K116 = '71a50262a73859e1538e79277f0ce00822be208d52b32328038a5932b513f874'
# Kernel 121: kernel 116 plus the USB replug after system sleep, same modules (2026-11-09).
LINUX_BOOT_K121 = 'e3542fcdd905a4b89a10eb828fa983bc9f0837a2635155d0124c692f6c5fbb1f'
LINUX_BOOT_K122 = '9701f48ddced60c648e79a502885778935d1cb5610a011d77fc1898eea01d41a'
# Kernel 138 (BOOT v147, 2026-11-12): the battery percent kept in the PMIC; same modules.
LINUX_BOOT_K138 = '5faafaa0035bff7bd77251ab01e2b4c395798755ad24db7b3240cc3d6139ad0f'
MMC_SECTORS = 122142720  # the 64 GB phone, for fixtures
# Every SM-A226B, 64 GB or 128 GB: the eMMC and userdata are judged by backend/storage.py's rule.


def disk_admitted(sectors):
    return _backend_storage().disk_admitted(sectors)


def userdata_fits(size, disk_sectors):
    return _backend_storage().userdata_fits(size, disk_sectors)


def userdata_sizes(disk_sectors):
    return _backend_storage().userdata_sizes(disk_sectors)
MMC_BOOT_BYTES = 4 * CHUNK
PLATFORM_BASELINE = {
    'dtbo': '97ed1a566cc30418477d790b3b2f4b21af4f0307bc466769c77d2b4edc21fa16',
    'vbmeta': '48dabb96a580de5166129ad5a507ce42a6f216d1a57b0fca3cbecd0d00cf65fa',
    'vbmeta_system': '22b6c3abbc5dc56a3ff1d75c88655b97ae446f96885d2b3a5f9b979e2855c56e',
}
CHECKPOINT_LABELS = ('boot', 'recovery', 'dtbo', 'vbmeta', 'vbmeta_system')
PROTECTED_GEOMETRY = {'lk': (17, 415744, 2097152), 'tee1': (14, 370688, 2097152)}
PROTECTED_HASHES = {
    'bootloader': (362496, '76b7f9754cc54a208b4e2d9d792d59aac16457b7407f071582d64b5a655bd057',
                   '0f9349eee28dd4db854f8ded73eb465046f3480b548cb7d1771f619393b4c77d'),
    'lk': (1666304, 'bda80cbf7cafeb3c03ee7919d55c4173aea728edc2db929607015d4f26983f50',
           '9a5551c9326b6683eb5b80157c6c1f01addd3bd94f08ba42641f4f3dda78252f'),
    'tee1': (1031904, '073c140e32e89a871ebd6ac50f8145d05aa9d3448188ca29deee16f363a51e01',
             '4a1050a7c856adbd83d331f9b0a49357e89f568e50b5e7f77345bed6d4aa4ec8'),
}

# Exact observed 64GB A226B PIT/GPT geometry, in 512-byte sectors. No caller can
# add a target by passing a pathname, modifying a plan or selecting an archive.
GEOMETRY = {
    'dtbo': (20, 423936, 8388608),
    'vbmeta': (21, 440320, 65536),
    'boot': (41, 1434624, 33554432),
    'recovery': (42, 1500160, 41943040),
    'vbmeta_system': (43, 1582080, 65536),
    'vbmeta_vendor': (44, 1582208, 65536),
    'super': (48, 1605632, 7239368704),
    'prism': (49, 15745024, 738197504),
    'optics': (50, 17186816, 25165824),
    # Restored only from a partition image chosen in Restore; never part of a stock plan.
    'userdata': (54, 17850368, 53393489920),
}
STOCK_ORDER = ('super', 'prism', 'optics', 'dtbo',
               'vbmeta_system', 'vbmeta', 'boot', 'recovery')
# This recovery stays installed: Android boots without stock RECOVERY, and it is
# the way back to Linux. The manifest still verifies the stock RECOVERY image.
STOCK_WRITE = tuple(label for label in STOCK_ORDER if label != 'recovery')
# /data as Samsung's own userdata.img of the stock firmware makes it (AP_A226BXXSBDZC3, read
# 2026-11-10): f2fs with encrypt, extra_attr, project_quota, quota, verity and compression
# (features 0x2499), labelled "data", no casefold. The stock kernel 4.14 mounts exactly that;
# Android then encrypts it in place on its first start (metadata encryption).
RESET_MAKE_F2FS = ('/bin/make_f2fs', '-g', 'android', '-f', '-O', 'quota', '-O', 'compression', '-O', 'extra_attr',
                   '-l', 'data')
ANDROID_DATA_FEATURES = 0x2499
# An empty ext4 on metadata, as the phone's fstab describes it (2026-11-12, on the phone): zeros
# there were not formatted by first-stage init under a GSI, which then mounts tmpfs on /metadata
# (TrebleDroid's 00-fix-metadata.sh); userdata's encryption key landed in RAM, and after the next
# restart Android found no key (vold: No key found in /metadata/vold/metadata_encryption/key) and
# asked for a reset again, with or without root.
RESET_MAKE_METADATA = ('/sbin/mkfs.ext4', '-F', '-q', '-b', '4096', '-L', 'metadata')
EXT4_MAGIC_AT = 1080
F2FS_MAGIC = b'\x10\x20\xf5\xf2'
F2FS_FEATURE_AT = 1024 + 2180  # struct f2fs_super_block.feature, the superblock at 1 KiB
METADATA_CLEAR_BYTES = 16 * CHUNK
STOCK_FORMAT = {label: ('sparse' if label in ('super', 'prism', 'optics') else 'raw')
                for label in STOCK_ORDER}
SPARSE_MAGIC = 0xed26ff3a
RAW, FILL, DONT_CARE, CRC32 = 0xcac1, 0xcac2, 0xcac3, 0xcac4


def require(condition, code, message, **details):
    if not condition:
        fs.fail(code, message, **details)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def number(value, low, high, name):
    require(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
            'INVALID_IMAGE_METADATA', 'A numeric field is outside its admitted bound', field=name)
    return value


def sha(value, name='sha256'):
    fs.require_hash(value, name)
    return value


def same_object(st):
    return [st.st_dev, st.st_ino, st.st_rdev]


class ImageReader:
    """One regular-file raw/gzip/lz4 input, at most one gzip member and no trailer.

    Reads use a pinned descriptor and positional offsets. Neither compressed
    chunks nor decompressed buffers exceed the stated bound. Gzip CRC/ISIZE are
    checked by zlib; EOF must consume the exact admitted source bytes. A byte
    range (offset, length) reads one member of an archive in place, as an
    Odin tar keeps it; lz4(1) decodes an LZ4 range fed with exactly its bytes.
    """
    def __init__(self, fd, compression, *, maximum=MAX_SOURCE, offset=0, length=None):
        self.fd = fd
        self.before = os.fstat(fd)
        require(stat.S_ISREG(self.before.st_mode) and self.before.st_nlink == 1,
                'IMAGE_REGULAR_ONLY', 'Image sources must be regular files without hardlink aliases')
        require(compression in ('none', 'gzip', 'lz4'), 'UNSUPPORTED_COMPRESSION', 'Use a raw file, one gzip stream or LZ4')
        self.limit = number(maximum, 1, MAX_SOURCE, 'maximum')
        self.start = number(offset, 0, self.before.st_size, 'offset')
        self.size = number(self.before.st_size - offset if length is None else length, 1, self.limit, 'source_size')
        require(self.start + self.size <= self.before.st_size, 'SHORT_IMAGE', 'The image range runs past the end of its file')
        self.compression = compression
        self.decoder = zlib.decompressobj(31) if compression == 'gzip' else None
        self.lz4 = self.feeder = None
        self.feed_error = None
        self.pending = b''
        self.encoded_bytes = self.input_bytes = 0
        self.encoded_hash, self.input_hash = hashlib.sha256(), hashlib.sha256()
        self.finished = False
        self.payload_bytes = None
        self.sparse = None

    def _compressed(self):
        remaining = self.size - self.encoded_bytes
        if not remaining:
            return b''
        block = os.pread(self.fd, min(65536, remaining), self.start + self.encoded_bytes)
        require(block, 'SHORT_IMAGE', 'Image source ended before its admitted size')
        self.encoded_bytes += len(block)
        self.encoded_hash.update(block)
        return block

    def _feed(self):
        try:
            while True:
                block = self._compressed()
                if not block:
                    break
                self.lz4.stdin.write(block)
        except BaseException as error:  # reported by read() after the decoder stops
            self.feed_error = error
        finally:
            try:
                self.lz4.stdin.close()
            except OSError:
                pass

    def _lz4_read(self, count):
        if self.lz4 is None:
            self.lz4 = subprocess.Popen([LZ4, '-d', '-c', '-q'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, close_fds=True)
            self.feeder = threading.Thread(target=self._feed, daemon=True)
            self.feeder.start()
        block = self.lz4.stdout.read1(count) if hasattr(self.lz4.stdout, 'read1') else self.lz4.stdout.read(count)
        if block:
            return block
        code = self.lz4.wait()
        self.feeder.join()
        if isinstance(self.feed_error, fs.FilesystemError):
            raise self.feed_error
        require(self.feed_error is None, 'SHORT_IMAGE', 'The LZ4 source could not be read completely')
        require(code == 0, 'INVALID_LZ4', 'The LZ4 stream is damaged or has trailing data')
        return b''

    def close(self):
        if self.lz4 is None:
            return
        if self.lz4.poll() is None:
            self.lz4.kill()
            self.lz4.wait()
        self.feeder.join()
        self.lz4.stdout.close()

    def _finish(self):
        require(self.encoded_bytes == self.size and not self.pending,
                'IMAGE_TRAILING_DATA', 'The image stream has trailing or unconsumed data')
        require(fs.stamp(os.fstat(self.fd)) == fs.stamp(self.before),
                'IMAGE_CHANGED', 'Image source changed during verification')
        self.finished = True

    def read(self, count):
        number(count, 0, CHUNK, 'read_size')
        if not count or self.finished:
            return b''
        out = bytearray()
        while len(out) < count and not self.finished:
            if self.compression == 'lz4':
                block = self._lz4_read(count - len(out))
                if not block:
                    self._finish()
                    break
            elif self.decoder is None:
                remaining = self.size - self.encoded_bytes
                if not remaining:
                    self._finish()
                    break
                block = os.pread(self.fd, min(count - len(out), remaining), self.start + self.encoded_bytes)
                require(block, 'SHORT_IMAGE', 'Raw image ended before its admitted size')
                self.encoded_bytes += len(block)
                self.encoded_hash.update(block)
            else:
                if self.decoder.eof:
                    require(not self.decoder.unused_data, 'IMAGE_TRAILING_DATA',
                            'Concatenated gzip members and trailing bytes are not admitted')
                    self._finish()
                    break
                data = self.pending or self._compressed()
                require(data, 'TRUNCATED_GZIP', 'The gzip stream did not reach its checked footer')
                try:
                    block = self.decoder.decompress(data, count - len(out))
                except zlib.error:
                    fs.fail('INVALID_GZIP', 'The gzip stream or its CRC/length check is invalid')
                self.pending = self.decoder.unconsumed_tail
                if self.decoder.eof:
                    require(not self.decoder.unused_data and not self.pending,
                            'IMAGE_TRAILING_DATA', 'Concatenated gzip members and trailing bytes are not admitted')
            self.input_bytes += len(block)
            require(self.input_bytes <= self.limit, 'IMAGE_EXPANSION_LIMIT',
                    'Decompressed container exceeds its admitted bound')
            self.input_hash.update(block)
            out.extend(block)
        return bytes(out)

    def exact(self, count):
        number(count, 0, CHUNK, 'exact_read_size')
        data = self.read(count)
        require(len(data) == count, 'SHORT_IMAGE', 'Image header or payload ended early')
        return data

    def finish(self):
        require(not self.read(1), 'IMAGE_TRAILING_DATA', 'Image contains undeclared payload bytes')
        require(self.finished, 'SHORT_IMAGE', 'Image verification did not reach a checked end')


def zero_chunks(size, pattern=b'\0\0\0\0'):
    number(size, 0, MAX_TARGET, 'fill_bytes')
    require(len(pattern) == 4, 'INVALID_FILL', 'Sparse fill has exactly four bytes')
    block = pattern * (CHUNK // 4)
    while size:
        count = min(size, CHUNK)
        yield block[:count]
        size -= count


def decode_image(reader, image_format, target_bytes):
    """Yield bounded raw bytes; sparse unknown chunks/header extensions refuse."""
    number(target_bytes, 1, MAX_TARGET, 'target_bytes')
    emitted, crc = 0, 0
    if image_format == 'raw':
        while True:
            data = reader.read(CHUNK)
            if not data:
                break
            emitted += len(data)
            require(emitted <= target_bytes, 'IMAGE_TOO_LARGE', 'Raw image exceeds the admitted partition')
            yield data
        reader.payload_bytes = emitted
    else:
        require(image_format == 'sparse', 'UNSUPPORTED_IMAGE_FORMAT', 'Only raw or Android sparse images are admitted')
        magic, major, minor, file_header, chunk_header, block_size, blocks, chunks, checksum = \
            struct.unpack('<IHHHHIIII', reader.exact(28))
        require(magic == SPARSE_MAGIC and major == 1 and minor == 0,
                'UNSUPPORTED_SPARSE', 'Expected Android sparse format version 1.0')
        require(28 <= file_header <= MAX_HEADER and 12 <= chunk_header <= MAX_HEADER,
                'INVALID_SPARSE_HEADER', 'Sparse header sizes exceed the supported bound')
        require(4 <= block_size <= CHUNK and block_size % 4 == 0,
                'INVALID_SPARSE_BLOCK', 'Sparse block size must be a bounded multiple of four')
        number(blocks, 1, MAX_TARGET // block_size, 'sparse_blocks')
        number(chunks, 1, MAX_SPARSE_CHUNKS, 'sparse_chunks')
        expanded = blocks * block_size
        require(expanded <= target_bytes, 'IMAGE_TOO_LARGE', 'Sparse expansion exceeds the admitted partition')
        require(not any(reader.exact(file_header - 28)), 'UNSUPPORTED_SPARSE_EXTENSION',
                'Nonzero sparse header extensions are not admitted')
        reader.sparse = {'block_size': block_size, 'blocks': blocks, 'chunks': chunks,
                         'expanded_bytes': expanded, 'checksum': checksum}
        for index in range(chunks):
            kind, reserved, chunk_blocks, total_size = struct.unpack('<HHII', reader.exact(12))
            require(reserved == 0 and not any(reader.exact(chunk_header - 12)),
                    'UNSUPPORTED_SPARSE_EXTENSION', 'Sparse reserved fields must be zero')
            size = chunk_blocks * block_size
            require(emitted + size <= expanded, 'SPARSE_OUTPUT_BOUNDS', 'Sparse chunks exceed total block count')
            if kind == CRC32:
                require(chunk_blocks == 0 and total_size == chunk_header + 4,
                        'INVALID_SPARSE_CRC', 'A CRC chunk must contain only its four-byte checksum')
                require(struct.unpack('<I', reader.exact(4))[0] == crc & 0xffffffff,
                        'SPARSE_CRC_MISMATCH', 'Sparse prefix checksum differs from decoded bytes')
                continue
            require(chunk_blocks > 0, 'INVALID_SPARSE_CHUNK', 'Data chunks must make output progress')
            if kind == RAW:
                require(total_size == chunk_header + size, 'INVALID_SPARSE_CHUNK',
                        'RAW sparse length does not match its block count')
                remaining = size
                while remaining:
                    data = reader.exact(min(CHUNK, remaining))
                    remaining -= len(data)
                    crc = zlib.crc32(data, crc)
                    emitted += len(data)
                    yield data
            elif kind in (FILL, DONT_CARE):
                require(total_size == chunk_header + (4 if kind == FILL else 0),
                        'INVALID_SPARSE_CHUNK', 'FILL/DONT_CARE length is inconsistent')
                pattern = reader.exact(4) if kind == FILL else b'\0' * 4
                for data in zero_chunks(size, pattern):
                    crc = zlib.crc32(data, crc)
                    emitted += len(data)
                    yield data
            else:
                fs.fail('UNSUPPORTED_SPARSE_CHUNK', 'Unknown sparse chunk type', chunk=index, kind=kind)
        require(emitted == expanded, 'SPARSE_OUTPUT_BOUNDS', 'Sparse chunks do not fill the declared image')
        require(not checksum or checksum == crc & 0xffffffff,
                'SPARSE_CRC_MISMATCH', 'Sparse whole-image checksum differs from decoded bytes')
        reader.payload_bytes = emitted
    reader.finish()
    # Odin/AP images such as DTBO and VBMETA can be shorter than their GPT slot.
    # The reviewed target always includes an explicit deterministic zero tail.
    for data in zero_chunks(target_bytes - emitted):
        yield data


def fixed_chunks(iterator):
    pending = bytearray()
    for data in iterator:
        require(0 < len(data) <= CHUNK, 'INTERNAL_CHUNK_BOUND', 'Decoder produced an invalid chunk size')
        position = 0
        while position < len(data):
            count = min(CHUNK - len(pending), len(data) - position)
            pending.extend(data[position:position + count])
            position += count
            if len(pending) == CHUNK:
                yield bytes(pending)
                pending.clear()
    if pending:
        yield bytes(pending)


def boot_header(prefix, payload_bytes, *, recovery=False):
    require(len(prefix) >= 1660 and prefix[:8] == b'ANDROID!', 'INVALID_BOOT_MAGIC',
            'A raw Android BOOT header is required')
    kernel, kernel_addr, ramdisk, ramdisk_addr, second, second_addr, tags, page, version, os_version = \
        struct.unpack_from('<10I', prefix, 8)
    board = prefix[48:64].split(b'\0', 1)[0]
    dtbo, dtbo_offset, header_size = struct.unpack_from('<IQI', prefix, 1632)
    dtb, dtb_addr = struct.unpack_from('<IQ', prefix, 1648)
    require(version == 2 and header_size == 1660 and page == 2048 and board == BOARD.encode(),
            'BOOT_PROFILE_MISMATCH', 'BOOT must use the exact A22x v2/2048/SRPTL28A011 header')
    require(kernel_addr == 0x40080000 and ramdisk_addr == 0x51100000
            and tags == 0x47c80000 and dtb_addr == 0x47c80000 and second == 0,
            'BOOT_ADDRESS_MISMATCH', 'BOOT load addresses or second-stage layout differ from A22x')
    number(kernel, 1, GEOMETRY['boot'][2], 'kernel_bytes')
    number(ramdisk, 1, GEOMETRY['recovery' if recovery else 'boot'][2], 'ramdisk_bytes')
    number(dtb, 1, 8 * CHUNK, 'dtb_bytes')
    aligned = lambda n: (n + page - 1) // page * page
    end = page + aligned(kernel) + aligned(ramdisk)
    if recovery:
        require(dtbo > 0 and dtbo_offset == end, 'RECOVERY_LAYOUT_MISMATCH',
                'Stock recovery DTBO must follow its aligned kernel and ramdisk')
        end += aligned(dtbo)
    else:
        require(dtbo == 0 and dtbo_offset == 0, 'RECOVERY_IMAGE_FOR_BOOT',
                'A recovery image must not be written to BOOT')
    end += dtb
    require(end <= payload_bytes <= GEOMETRY['recovery' if recovery else 'boot'][2],
            'BOOT_PAYLOAD_BOUNDS', 'BOOT component sizes exceed its complete raw image')
    command = prefix[64:576].split(b'\0', 1)[0] + prefix[608:1632].split(b'\0', 1)[0]
    require(all(32 <= value < 127 for value in command), 'INVALID_BOOT_COMMAND_LINE',
            'BOOT command line contains unsupported control or non-ASCII bytes')
    return {'header_version': version, 'header_size': header_size, 'page_size': page,
            'board': board.decode(), 'kernel_bytes': kernel, 'ramdisk_bytes': ramdisk,
            'kernel_address': kernel_addr, 'ramdisk_address': ramdisk_addr,
            'tags_address': tags, 'dtb_address': dtb_addr, 'dtb_bytes': dtb,
            'recovery_dtbo_bytes': dtbo, 'recovery_dtbo_offset': dtbo_offset,
            'os_version': os_version, 'cmdline': command.decode(),
            'layout_validated': True, 'runtime_boot_tested': False}


def inspect_image(fd, compression, image_format, target_bytes, *, label=None, progress=None, offset=0, length=None):
    reader = ImageReader(fd, compression, offset=offset, length=length)
    try:
        return _inspect(reader, compression, image_format, target_bytes, label, progress)
    finally:
        reader.close()


def _inspect(reader, compression, image_format, target_bytes, label, progress):
    digest, chunks, prefix, count = hashlib.sha256(), [], bytearray(), 0
    for data in fixed_chunks(decode_image(reader, image_format, target_bytes)):
        if len(prefix) < 2048:
            prefix.extend(data[:2048 - len(prefix)])
        count += len(data)
        digest.update(data)
        chunks.append(hashlib.sha256(data).hexdigest())
        if progress:
            progress({'event': 'progress', 'operation': 'inspect-image', 'phase': 'verify-source',
                      'partition': label, 'bytes_done': count, 'bytes_total': target_bytes})
    require(count == target_bytes, 'IMAGE_SIZE_MISMATCH', 'Image expansion differs from its target length')
    result = {'compression': compression, 'format': image_format,
              'encoded_bytes': reader.encoded_bytes, 'encoded_sha256': reader.encoded_hash.hexdigest(),
              'input_bytes': reader.input_bytes, 'input_sha256': reader.input_hash.hexdigest(),
              'payload_bytes': reader.payload_bytes, 'target_bytes': target_bytes,
              'target_sha256': digest.hexdigest(), 'chunk_bytes': CHUNK, 'chunk_sha256': chunks}
    if reader.sparse:
        result['sparse'] = reader.sparse
    if label in ('boot', 'recovery'):
        require(image_format == 'raw', 'BOOT_RAW_REQUIRED', 'BOOT and recovery images must use raw Android layout')
        result['boot_header'] = boot_header(prefix, reader.payload_bytes, recovery=label == 'recovery')
    return result


def strict_json(raw):
    require(len(raw) <= MAX_JSON, 'JSON_TOO_LARGE', 'Installer JSON exceeds its bound')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'DUPLICATE_JSON_KEY', 'Duplicate JSON keys are not admitted')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda x: fs.fail('INVALID_JSON', 'Non-finite JSON numbers are not admitted'))
    except (UnicodeError, ValueError, RecursionError):
        fs.fail('INVALID_JSON', 'Installer JSON is invalid or too deeply nested')
    require(isinstance(value, dict), 'INVALID_JSON', 'Expected a JSON object')
    return value


def json_fd(fd):
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= MAX_JSON,
            'INVALID_JSON_FILE', 'Installer records must be bounded regular files without hardlinks')
    raw = os.pread(fd, MAX_JSON + 1, 0)
    require(len(raw) == before.st_size and fs.stamp(os.fstat(fd)) == fs.stamp(before),
            'JSON_CHANGED', 'Installer record changed during reading')
    return strict_json(raw), hashlib.sha256(raw).hexdigest()


def hash_fd(fd, size, *, salt=b'', progress=None, label=None, phase='verify-target'):
    digest = hashlib.sha256(salt)
    offset = 0
    while offset < size:
        block = os.pread(fd, min(CHUNK, size - offset), offset)
        require(block, 'TARGET_SHORT_READ', 'Partition or fixture ended before its exact bound', label=label)
        digest.update(block)
        offset += len(block)
        if progress:
            progress({'event': 'progress', 'operation': 'install', 'phase': phase,
                      'partition': label, 'bytes_done': offset, 'bytes_total': size})
    return digest.hexdigest()


def _backend_storage():
    # The frozen backend is used only for its read-only graph/admission adapter.
    import importlib.util
    base = Path(__file__).resolve().parent / 'backend'
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    name = 'a22x_install_readonly_storage'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, base / 'storage.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class FixtureContext:
    mode = 'fixture'
    def __init__(self, root):
        self.root = fs.absolute_no_symlinks(root)
        self.fs = fs.FixtureContext(self.root)
        self.config_path = self.root / 'install-fixture.json'
        fd = fs.open_regular(self.config_path)
        try:
            self.config, self.contract = json_fd(fd)
        finally:
            os.close(fd)
        require(self.config.get('schema') == 'a22x.install.fixture.v1', 'INVALID_FIXTURE', 'Installer fixture schema mismatch')
        self.device = self.config.get('device')
        require(isinstance(self.device, dict) and self.device.get('model') == MODEL
                and str(self.device.get('identity', '')).startswith('fixture:'),
                'INVALID_FIXTURE', 'Fixture identity must be explicitly marked fixture:')
        self.boot_id = self.config.get('boot_id', 'fixture-install-boot')
        self.lock_path = self.root / '.operation.lock'
        self.profile_path = self.root / 'install-stock-profile.json'
        self.guard_path = self.root / '.install-transaction.json'

    def reset_android(self):
        (self.root / 'android-reset.json').write_text('{"reset": true}')

    def fresh(self):
        return FixtureContext(self.root)

    def target(self, label, *, protected=False):
        require(label in (PROTECTED_GEOMETRY if protected else GEOMETRY), 'TARGET_FORBIDDEN', 'Target label is not allowlisted')
        spec = self.config.get('protected' if protected else 'targets', {}).get(label)
        require(isinstance(spec, dict), 'TARGET_UNAVAILABLE', 'Fixture lacks the required target', label=label)
        path = fs.absolute_no_symlinks(self.root / spec['path'])
        require(self.root in path.parents, 'FIXTURE_ESCAPE', 'Fixture targets stay beneath their owned directory')
        fd = fs.open_regular(path)
        try:
            before = os.fstat(fd)
            require(before.st_nlink == 1 and before.st_size == spec['size_bytes'], 'TARGET_SIZE_CHANGED', 'Fixture target size/links changed')
            return {'label': label, 'path': str(path), 'size_bytes': before.st_size,
                    'partition_number': spec.get('partition_number', (PROTECTED_GEOMETRY if protected else GEOMETRY)[label][0]),
                    'start_sector': spec.get('start_sector', (PROTECTED_GEOMETRY if protected else GEOMETRY)[label][1]),
                    'partuuid': spec.get('partuuid', 'fixture-' + label),
                    'device': 'fixture:' + label, 'object': same_object(before)}
        finally:
            os.close(fd)

    def check_offline(self, targets):
        mounted = self.config.get('mounted_targets', [])
        require(not any(target['label'] in mounted for target in targets), 'PARTITION_MOUNTED', 'An installer target is mounted')
        require(len({tuple(target['object']) for target in targets}) == len(targets),
                'ALIASED_TARGETS', 'Fixture targets must be distinct regular files')

    def open_target(self, target, *, write=False):
        require(target['label'] in GEOMETRY, 'TARGET_FORBIDDEN', 'Only installer targets can be opened here')
        require(self.target(target['label']) == target, 'TARGET_CHANGED', 'Fixture target identity changed')
        # Prove a regular inode with O_PATH before any writable open. A symlink,
        # FIFO, character or block node cannot turn fixture mode into hardware IO.
        handle = os.open(target['path'], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(handle)
            require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and same_object(st) == target['object'],
                    'FIXTURE_REGULAR_ONLY', 'Fixture writes require the exact single regular-file inode')
            fd = os.open('/proc/self/fd/' + str(handle), (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC)
        finally:
            os.close(handle)
        self.check_target(fd, target)
        return fd

    def check_target(self, fd, target):
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and same_object(st) == target['object']
                and st.st_size == target['size_bytes'], 'TARGET_CHANGED', 'Pinned fixture target changed')
        named = os.stat(target['path'], follow_symlinks=False)
        require(same_object(named) == target['object'] and stat.S_ISREG(named.st_mode),
                'TARGET_CHANGED', 'Named fixture target no longer matches its pinned inode')


class DeviceContext:
    mode = 'device'
    def __init__(self):
        self.fs = fs.DeviceContext()
        self.raw = _backend_storage().DeviceContext()
        self.raw.assert_recovery()
        self.device, self.contract = self.raw.device, self.raw.contract
        self.boot_id = self.fs.boot_id
        self.lock_path = self.fs.lock_path
        self.profile_path, self.guard_path = PROFILE_PATH, RAM_GUARD

    def fresh(self):
        return DeviceContext()

    def target(self, label, *, protected=False):
        geometry = PROTECTED_GEOMETRY if protected else GEOMETRY
        require(label in geometry, 'TARGET_FORBIDDEN', 'Partition is outside the dedicated installer allowlist')
        number, start, size = geometry[label]
        expected = Path('/dev/mmcblk0p' + str(number))
        link = Path('/dev/disk/by-partlabel') / label
        require(link.resolve(strict=True) == expected, 'GPT_IDENTITY_MISMATCH', 'GPT label resolves to an unexpected device', label=label)
        st = expected.lstat()
        require(stat.S_ISBLK(st.st_mode), 'TARGET_NOT_BLOCK', 'Admitted device path is not a block partition')
        devno = str(os.major(st.st_rdev)) + ':' + str(os.minor(st.st_rdev))
        node = self.raw.nodes.get(devno)
        require(node and self.raw.graph.physical_roots(devno) == {self.raw.internal_disk},
                'WRONG_INTERNAL_DEVICE', 'Target is not on the identified internal eMMC')
        sysfs = Path(node['sysfs'])
        fields = dict(line.split('=', 1) for line in (sysfs / 'uevent').read_text().splitlines() if '=' in line)
        require(fields.get('PARTNAME') == label and fields.get('PARTUUID')
                and int((sysfs / 'partition').read_text()) == number
                and int((sysfs / 'start').read_text()) == start
                and (int((sysfs / 'size').read_text()) * 512 == size if label != 'userdata'
                     else userdata_fits(int((sysfs / 'size').read_text()) * 512, int((sysfs.parent / 'size').read_text()))),
                'GPT_GEOMETRY_MISMATCH', 'Kernel GPT identity/geometry differs from the fixed A22x profile', label=label)
        size = int((sysfs / 'size').read_text()) * 512
        return {'label': label, 'path': str(expected), 'size_bytes': size,
                'partition_number': number, 'start_sector': start, 'partuuid': fields['PARTUUID'],
                'device': devno, 'object': same_object(st)}

    def reset_android(self):
        """Format /data as stock recovery does and clear /metadata, which Android recreates."""
        paths = {}
        for label in ('userdata', 'metadata'):
            path = Path('/dev/disk/by-partlabel').joinpath(label).resolve(strict=True)
            st = path.lstat()
            require(stat.S_ISBLK(st.st_mode), 'TARGET_NOT_BLOCK', 'Reset target is not a block partition', label=label)
            devno = str(os.major(st.st_rdev)) + ':' + str(os.minor(st.st_rdev))
            require(devno in self.raw.nodes and self.raw.graph.physical_roots(devno) == {self.raw.internal_disk},
                    'WRONG_INTERNAL_DEVICE', 'Reset target is not on the internal eMMC', label=label)
            self.raw.graph.require_unmounted(devno, self.raw.mounts)
            self.raw.graph.require_no_holders(devno)
            paths[label] = path
        fd = os.open(paths['metadata'], os.O_WRONLY | os.O_CLOEXEC | os.O_EXCL)
        try:
            for offset in range(0, METADATA_CLEAR_BYTES, CHUNK):
                write_all(fd, bytes(CHUNK), offset=offset)
            os.fsync(fd)
        finally:
            os.close(fd)
        done = subprocess.run([*RESET_MAKE_METADATA, str(paths['metadata'])], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors='replace')
        require(done.returncode == 0, 'METADATA_RESET_FAILED', 'mkfs.ext4 could not format metadata', output=done.stdout[-400:])
        fd = os.open(paths['metadata'], os.O_RDONLY | os.O_CLOEXEC)
        try:
            magic = os.pread(fd, 2, EXT4_MAGIC_AT)
        finally:
            os.close(fd)
        require(magic == b'\x53\xef', 'METADATA_RESET_FAILED', 'metadata holds no ext4 after formatting')
        done = subprocess.run([*RESET_MAKE_F2FS, str(paths['userdata'])], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors='replace')
        require(done.returncode == 0, 'USERDATA_RESET_FAILED', 'make_f2fs could not format userdata', output=done.stdout[-400:])
        fd = os.open(paths['userdata'], os.O_RDONLY | os.O_CLOEXEC)
        try:
            head = os.pread(fd, 4096, 0)
        finally:
            os.close(fd)
        features = struct.unpack_from('<I', head, F2FS_FEATURE_AT)[0] if len(head) >= F2FS_FEATURE_AT + 4 else None
        require(head[1024:1028] == F2FS_MAGIC and features == ANDROID_DATA_FEATURES, 'USERDATA_RESET_FEATURES',
                "userdata was formatted with other features than Samsung's", features=features)

    def close_logical(self):
        """Partitions of super that Files opened are closed before super itself is read or written
        (2026-11-12): their read-only mapping holds super, so the checks below would refuse it, and
        nothing may read a partition while super changes under it. Inside the caller's lease when
        it holds one, else under a lease of its own; while another operation runs, nothing is closed
        and the checks refuse super."""
        try:
            with borrowed_lease(self.lock_path, self.mode):  # the one borrowing rule (2026-11-14 review)
                closed = self.fs.close_logical()
        except BlockingIOError:
            return []
        if closed:
            # The graph and the mounts were read when this context was made.
            self.raw = _backend_storage().DeviceContext()
            self.raw.assert_recovery()
        return closed

    def check_offline(self, targets):
        if any(target['label'] == 'super' for target in targets):
            self.close_logical()
        for target in targets:
            self.raw.graph.require_unmounted(target['device'], self.raw.mounts)
            self.raw.graph.require_no_holders(target['device'])
        require(len({tuple(target['object']) for target in targets}) == len(targets),
                'ALIASED_TARGETS', 'Installer targets overlap the same block node')

    def open_target(self, target, *, write=False):
        require(target['label'] in GEOMETRY and self.target(target['label']) == target,
                'TARGET_CHANGED', 'The exact allowlisted partition identity changed')
        self.check_offline([target])
        fd = os.open(target['path'], (os.O_RDWR if write else os.O_RDONLY) |
                     os.O_CLOEXEC | os.O_NOFOLLOW | os.O_EXCL)
        try:
            self.check_target(fd, target)
            require(not bool(struct.unpack('i', fcntl.ioctl(fd, 0x125e, bytes(4)))[0]) or not write,
                    'TARGET_READ_ONLY', 'Kernel has marked this target read-only')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def check_target(self, fd, target):
        require(target['label'] in GEOMETRY, 'TARGET_FORBIDDEN', 'Target escaped the installer allowlist')
        st = os.fstat(fd)
        named = os.stat(target['path'], follow_symlinks=False)
        require(stat.S_ISBLK(st.st_mode) and stat.S_ISBLK(named.st_mode)
                and same_object(st) == target['object'] == same_object(named)
                and struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] == target['size_bytes'],
                'TARGET_CHANGED', 'Block identity or exact size changed')


class Source:
    """Pinned external ext4 directory and regular source inodes."""
    def __init__(self, context, volume, token=None, *, writing=True):
        self.context = context
        self.pin = fs.PinnedVolume(context.fs, volume, token, writing=writing)
        try:
            v = self.pin.volume
            external = v.get('external') if context.mode == 'device' else volume in context.config.get('external_volumes', ['sdcard-p1'])
            require(external and v['fs_type'] == 'ext4', 'EXTERNAL_EXT4_REQUIRED',
                    'Installer sources/checkpoints require the admitted external ext4 SD')
            self.volume, self.token = volume, v['volume_token']
            self.files = {}
        except BaseException:
            self.pin.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        for fd, original in self.files.values():
            os.close(fd)
        self.pin.close()

    def path(self, relative):
        return Path(self.pin.volume['mountpoint']) / fs.clean_path(relative).lstrip('/')

    def open(self, relative):
        relative = fs.clean_path(relative)
        if relative in self.files:
            self.recheck_file(relative)
            return self.files[relative][0]
        with self.pin.parent(relative) as (parent, name):
            handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                st = os.fstat(handle)
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'IMAGE_REGULAR_ONLY',
                        'Installer source must be a regular file without aliases')
                self.pin.check_fd(handle)
                fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
                require(same_object(os.fstat(fd)) == same_object(st), 'IMAGE_CHANGED', 'Source inode changed while opening')
            finally:
                os.close(handle)
        self.files[relative] = fd, fs.stamp(st)
        return fd

    def recheck_file(self, relative):
        fd, original = self.files[relative]
        require(fs.stamp(os.fstat(fd)) == original, 'IMAGE_CHANGED', 'Source inode changed after verification')
        with self.pin.parent(relative) as (parent, name):
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1 and fs.stamp(named) == original,
                    'IMAGE_CHANGED', 'Selected source path no longer names the verified regular inode')

    def recheck(self):
        self.pin.recheck()
        for relative in self.files:
            self.recheck_file(relative)

    def binding(self):
        v = self.pin.volume
        return {'volume': self.volume, 'volume_token': self.token, 'mount_id': v['mount_id'],
                'mountpoint': v['mountpoint'], 'directory': same_object(os.fstat(self.pin.fd)),
                'device_identity': v.get('device_identity'),
                'files': {p: list(stamp) for p, (fd, stamp) in sorted(self.files.items())}}


def stock_profile_template(manifest, manifest_sha256, *, qualified=False, protected_prefixes=None, blockers=None):
    require(manifest.get('schema') == BUNDLE_SCHEMA and manifest.get('model') == MODEL,
            'INVALID_STOCK_BUNDLE', 'Profile builder requires the exact model stock manifest')
    return {'schema': PROFILE_SCHEMA, 'model': MODEL, 'id': manifest['profile_id'],
            'firmware': manifest['firmware'], 'manifest_sha256': sha(manifest_sha256),
            'qualified': bool(qualified), 'blockers': list(blockers or []),
            'protected_prefixes': protected_prefixes or {},
            'platform_baseline': dict(PLATFORM_BASELINE),
            'qualification': manifest.get('qualification', {})}


def load_stock_profile(context, required=False):
    if not context.profile_path.exists():
        require(not required, 'STOCK_PROFILE_UNAVAILABLE', 'A qualified pinned stock profile is not installed')
        return None
    path = fs.absolute_no_symlinks(context.profile_path)
    fd = fs.open_regular(path)
    try:
        st = os.fstat(fd)
        if context.mode == 'device':
            require(st.st_uid == 0 and not st.st_mode & 0o022, 'UNTRUSTED_STOCK_PROFILE',
                    'Runtime stock profile must be root-owned and not writable by others')
        profile, file_sha = json_fd(fd)
    finally:
        os.close(fd)
    require(profile.get('schema') == PROFILE_SCHEMA and profile.get('model') == MODEL
            and isinstance(profile.get('qualified'), bool)
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', str(profile.get('id', '')))
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', str(profile.get('firmware', ''))),
            'INVALID_STOCK_PROFILE', 'Unsupported runtime stock profile')
    sha(profile.get('manifest_sha256'))
    require(isinstance(profile.get('blockers'), list) and len(profile['blockers']) <= 16
            and all(re.fullmatch(r'[A-Z0-9_]{1,80}', str(x)) for x in profile['blockers']),
            'INVALID_STOCK_PROFILE', 'Stock profile blockers are malformed')
    require(not profile['qualified'] or not profile['blockers'], 'INVALID_STOCK_PROFILE',
            'A qualified stock profile cannot retain qualification blockers')
    profile['file_sha256'] = file_sha
    return profile


def ready(context):
    profile = load_stock_profile(context)
    return {'available': True, 'stock_profile': {'available': bool(profile),
            'id': profile['id'] if profile else None, 'firmware': profile['firmware'] if profile else None,
            'qualified': bool(profile and profile['qualified']),
            'blockers': (profile['blockers'] or ['STOCK_PROFILE_NOT_QUALIFIED']) if profile and not profile['qualified']
                        else [] if profile else ['STOCK_PROFILE_UNAVAILABLE']},
            'blockers': []}


class MMCReadCommand(ctypes.Structure):
    """Exact audited Linux MMC_IOC_CMD UAPI; only fixed CMD8 is issued."""
    _fields_ = [('write_flag', ctypes.c_int), ('is_acmd', ctypes.c_int),
        ('opcode', ctypes.c_uint32), ('arg', ctypes.c_uint32), ('response', ctypes.c_uint32 * 4),
        ('flags', ctypes.c_uint32), ('blksz', ctypes.c_uint32), ('blocks', ctypes.c_uint32),
        ('postsleep_min_us', ctypes.c_uint32), ('postsleep_max_us', ctypes.c_uint32),
        ('data_timeout_ns', ctypes.c_uint32), ('cmd_timeout_ms', ctypes.c_uint32),
        ('__pad', ctypes.c_uint32), ('data_ptr', ctypes.c_uint64)]


def decode_ext_csd(raw):
    require(isinstance(raw, bytes) and len(raw) == 512, 'INVALID_EXT_CSD', 'EXT_CSD register must contain exactly 512 bytes')
    sectors = int.from_bytes(raw[212:216], 'little')
    area_bytes = raw[226] * 128 * 1024
    selector = (raw[179] >> 3) & 7
    require(disk_admitted(sectors) and area_bytes == MMC_BOOT_BYTES and 5 <= raw[192] <= 9,
            'EXT_CSD_GEOMETRY_MISMATCH', 'Live EXT_CSD geometry/revision differs from the fixed internal eMMC')
    require(selector in (1, 2), 'ACTIVE_BOOT_SELECTOR_UNSUPPORTED', 'The active eMMC boot selection is not a hardware boot area')
    return {'method': 'mmc-cmd8-readonly-v1', 'ext_csd_sha256': hashlib.sha256(raw).hexdigest(),
            'ext_csd_revision': raw[192], 'partition_config': raw[179], 'boot_partition_enable': selector,
            'partition_access': raw[179] & 7, 'boot_ack': bool(raw[179] & 64),
            'active_boot_area': '/dev/mmcblk0boot' + str(selector - 1),
            'boot_area_bytes': area_bytes, 'user_sector_count': sectors}


def read_ext_csd_fd(fd):
    """One read-only SEND_EXT_CSD, no caller-controlled opcode or argument.

    The caller proves the internal whole-device identity before supplying its
    O_RDONLY descriptor. Raw register bytes are never saved or logged.
    """
    require(ctypes.sizeof(MMCReadCommand) == 72 and MMCReadCommand.data_ptr.offset == 64
            and MMCReadCommand.flags.offset == 32, 'MMC_UAPI_MISMATCH', 'MMC ioctl layout differs from the audited kernel ABI')
    require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
            'MMC_READ_ONLY_REQUIRED', 'The selector reader requires an O_RDONLY descriptor')
    buffer = ctypes.create_string_buffer(512)
    command = MMCReadCommand()
    command.opcode, command.flags, command.blksz, command.blocks = 8, 0xb5, 512, 1
    command.data_ptr = ctypes.addressof(buffer)
    request = bytearray(bytes(command))
    fcntl.ioctl(fd, 0xc048b300, request, True)
    response = struct.unpack_from('<4I', request, 16)
    require(not response[0] & 0xFFF9A000, 'MMC_READ_FAILED', 'The eMMC reported an error while reading EXT_CSD')
    return decode_ext_csd(buffer.raw)


def read_active_boot_selector(context):
    require(context.mode == 'device', 'DEVICE_ONLY', 'Real MMC selector reads are unavailable in fixture mode')
    node = Path('/sys/class/block/mmcblk0').resolve(strict=True)
    card = (node / 'device').resolve(strict=True)
    devno = (node / 'dev').read_text().strip()
    cid = hashlib.sha256((card / 'cid').read_bytes().strip()).hexdigest()
    require(context.raw.internal_disk == devno and (card / 'type').read_text().strip() == 'MMC'
            and context.device['identity'] == 'emmc-cid-sha256:' + cid
            and disk_admitted(int((node / 'size').read_text())),
            'WRONG_INTERNAL_DEVICE', 'Live selector source is not the admitted whole internal eMMC')
    path = Path('/dev/mmcblk0')
    named = path.lstat()
    require(stat.S_ISBLK(named.st_mode) and f'{os.major(named.st_rdev)}:{os.minor(named.st_rdev)}' == devno,
            'MMC_IDENTITY_CHANGED', 'Whole-device node differs from the identified internal MMC')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISBLK(opened.st_mode) and same_object(opened) == same_object(named)
                and struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] == int((node / 'size').read_text()) * 512,
                'MMC_IDENTITY_CHANGED', 'Opened MMC descriptor differs from the pinned whole device')
        observed = read_ext_csd_fd(fd)
        # 2026-11-14 review: both used to equal one fixed number; now each is only in a range, so the
        # register and the block device must name the same eMMC size.
        require(observed['user_sector_count'] == int((node / 'size').read_text()), 'MMC_IDENTITY_CHANGED',
                'The eMMC register and the block device differ in size')
        require(same_object(path.lstat()) == same_object(opened)
                and hashlib.sha256((card / 'cid').read_bytes().strip()).hexdigest() == cid
                and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == context.boot_id,
                'MMC_IDENTITY_CHANGED', 'Internal card or recovery boot changed during selector read')
        return {**observed, 'parent_cid_sha256': cid}
    finally:
        os.close(fd)


def check_active_selector(mapping, observed):
    expected = mapping.get('active_selector')
    require(mapping.get('active_selector_proven') is True and isinstance(expected, dict)
            and expected.get('method') == 'mmc-cmd8-readonly-v1'
            and expected.get('boot_partition_enable') in (1, 2),
            'ACTIVE_BOOT_SELECTOR_UNPROVEN', 'A single boot-area mapping requires a qualified read-only selector proof')
    keys = ('method', 'parent_cid_sha256', 'boot_partition_enable', 'boot_ack',
            'boot_area_bytes', 'user_sector_count', 'ext_csd_revision')
    require(all(observed.get(key) == expected.get(key) for key in keys)
            and mapping['paths'] == [observed['active_boot_area']],
            'ACTIVE_BOOT_SELECTOR_CHANGED', 'The fresh active boot-area selection differs from the qualified stock profile')
    # Exclude transient status/access fields and the full register hash from
    # review equality. Every admission still performs a fresh fixed CMD8 read.
    return {key: observed[key] for key in keys}


def verify_protected(context, profile):
    if context.mode == 'fixture':
        require(context.config.get('protected_firmware_matches') is True,
                'PROTECTED_FIRMWARE_MISMATCH', 'Synthetic retained firmware does not match the fixture profile')
        return {'fixture_only': True, 'matched': True}
    mappings = profile.get('protected_prefixes')
    require(isinstance(mappings, dict) and set(mappings) == set(PROTECTED_HASHES),
            'PROTECTED_FIRMWARE_PROOF_REQUIRED', 'All retained signed firmware prefixes require an established read-only mapping')
    proof = {}
    parent = Path('/sys/class/block/mmcblk0').resolve(strict=True)
    card = (parent / 'device').resolve(strict=True)
    parent_cid = hashlib.sha256((card / 'cid').read_bytes().strip()).hexdigest()
    require(context.device['identity'] == 'emmc-cid-sha256:' + parent_cid,
            'WRONG_INTERNAL_DEVICE', 'Protected firmware reads require the admitted internal MMC')
    for label, (count, salt, expected) in PROTECTED_HASHES.items():
        mapping = mappings[label]
        require(isinstance(mapping, dict) and mapping.get('mapping_proven') is True,
                'PROTECTED_MAPPING_UNPROVEN', 'Retained firmware mapping has not been independently established', label=label)
        require(mapping.get('parent_cid_sha256') == parent_cid, 'PROTECTED_MAPPING_MISMATCH',
                'Protected mapping belongs to another internal MMC')
        if label != 'bootloader':
            target = context.target(label, protected=True)
            require(mapping.get('path') == target['path'] and mapping.get('offset', 0) == 0,
                    'PROTECTED_MAPPING_MISMATCH', 'Retained GPT mapping differs from the fixed profile')
            context.raw.graph.require_unmounted(target['device'], context.raw.mounts)
            context.raw.graph.require_no_holders(target['device'])
            paths, offset, size = [target['path']], 0, target['size_bytes']
        else:
            paths = mapping.get('paths')
            require(isinstance(paths, list) and 1 <= len(paths) <= 2 and len(set(paths)) == len(paths)
                    and all(x in ('/dev/mmcblk0boot0', '/dev/mmcblk0boot1') for x in paths),
                    'PROTECTED_MAPPING_MISMATCH', 'Only the separately proven internal eMMC boot areas may be read')
            if len(paths) == 1:
                proof['bootloader_selector'] = check_active_selector(mapping, read_active_boot_selector(context))
            offset = number(mapping.get('offset'), 0, 65536, 'boot_area_offset')
            size = number(mapping.get('partition_bytes'), count, 4 * CHUNK, 'boot_area_bytes')
            require(offset % 512 == 0 and offset + count <= size,
                    'PROTECTED_MAPPING_MISMATCH', 'Signed prefix exceeds the proved boot area')
        observed = []
        for path in paths:
            node = Path('/sys/class/block') / Path(path).name
            require(node.exists(), 'PROTECTED_FIRMWARE_UNAVAILABLE', 'Required retained firmware block source is absent')
            fields = dict(line.split('=', 1) for line in (node / 'uevent').read_text().splitlines() if '=' in line)
            if label == 'bootloader':
                require(node.resolve().parent == parent and (node / 'device').resolve(strict=True) == parent
                        and int((node / 'size').read_text()) * 512 == size,
                        'PROTECTED_MAPPING_MISMATCH', 'eMMC boot area parent/size differs from its proof')
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                st = os.fstat(fd)
                require(stat.S_ISBLK(st.st_mode) and str(os.major(st.st_rdev)) == fields.get('MAJOR')
                        and str(os.minor(st.st_rdev)) == fields.get('MINOR')
                        and struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))[0] == size,
                        'PROTECTED_MAPPING_MISMATCH', 'Protected read-only FD differs from kernel identity')
                actual = hashlib.sha256(bytes.fromhex(salt))
                position = 0
                while position < count:
                    data = os.pread(fd, min(CHUNK, count - position), offset + position)
                    require(data, 'PROTECTED_SHORT_READ', 'Retained firmware prefix ended early')
                    actual.update(data)
                    position += len(data)
                require(actual.hexdigest() == expected, 'PROTECTED_FIRMWARE_MISMATCH',
                        'Retained firmware differs from the signed stock dependency', label=label)
                observed.append({'path': path, 'object': same_object(st), 'offset': offset,
                                 'prefix_bytes': count, 'salted_sha256': expected})
            finally:
                os.close(fd)
        proof[label] = observed
    return proof


def backup_binding(context, source, backup_path, lease, *, current_data=False, current_boot=False, progress=None):
    path = fs.absolute_no_symlinks(backup_path)
    base = Path(source.pin.volume['mountpoint'])
    require(str(path) == backup_path and base in path.parents and path.is_dir(),
            'BACKUP_LOCATION_MISMATCH', 'Select an absolute complete Linux snapshot on the source SD volume')
    relative = '/' + path.relative_to(base).as_posix()
    manifest, manifest_sha = json_fd(source.open(relative + '/manifest.json'))
    complete, complete_sha = json_fd(source.open(relative + '/COMPLETE.json'))
    require(manifest.get('schema') == 'a22x.backup.v2' and manifest.get('workflow') == 'linux-partclone-v2'
            and manifest.get('profile') == 'linux' and manifest.get('device') == context.device
            and manifest.get('source_mode') == context.mode and complete.get('manifest_sha256') == manifest_sha
            and complete.get('all_entries_verified') is True,
            'LINUX_BACKUP_REQUIRED', 'A complete verified Linux v2 backup from this exact device is required')
    entries = manifest.get('entries')
    require(isinstance(entries, list) and [x.get('label') for x in entries] == ['boot', 'userdata'],
            'LINUX_BACKUP_REQUIRED', 'Backup must contain exactly Linux BOOT and userdata')
    for entry in entries:
        filename = entry.get('archive')
        require(isinstance(filename, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', filename)
                and filename not in ('.', '..'), 'INVALID_BACKUP_ARCHIVE', 'Backup archive filename is invalid')
        source.open(relative + '/' + filename)
    data_matches, boot_matches = False, False
    if context.mode == 'fixture':
        require(manifest.get('fixture_regular_images') is True, 'INVALID_FIXTURE_BACKUP',
                'Installer fixture requires explicitly marked regular-image backup simulation')
        for entry in entries:
            fd = source.open(relative + '/' + entry['archive'])
            require(os.fstat(fd).st_size == entry['raw_bytes'] and hash_fd(fd, entry['raw_bytes']) == entry['raw_sha256'],
                    'BACKUP_HASH_MISMATCH', 'Fixture backup data differs from its sealed checksum')
        target = context.target('boot')
        fd = context.open_target(target)
        try:
            boot_matches = hash_fd(fd, target['size_bytes']) == entries[0]['raw_sha256']
        finally:
            os.close(fd)
        if current_data:
            item = context.config.get('userdata')
            require(isinstance(item, dict), 'INVALID_FIXTURE', 'Fixture lacks a read-only userdata reference')
            data_path = fs.absolute_no_symlinks(context.root / item['path'])
            require(context.root in data_path.parents, 'FIXTURE_ESCAPE', 'Fixture userdata stays under its root')
            fd = fs.open_regular(data_path)
            try:
                data_matches = os.fstat(fd).st_size == entries[1]['raw_bytes'] and hash_fd(fd, entries[1]['raw_bytes']) == entries[1]['raw_sha256']
            finally:
                os.close(fd)
            require(data_matches, 'BACKUP_NO_LONGER_CURRENT', 'Fixture userdata changed after its complete backup')
        qualification = 'fixture-regular-images-only'
    else:
        # Reuse the immutable Linux verifier and its pinned native toolchain,
        # never its writer. The caller already holds the shared storage lease.
        import importlib
        base_backend = Path(__file__).resolve().parent / 'backend'
        if str(base_backend) not in sys.path:
            sys.path.insert(0, str(base_backend))
        workflow = importlib.import_module('linux_workflow')
        qualified = importlib.import_module('qualification')
        require(Path(workflow.__file__).resolve() == base_backend / 'linux_workflow.py'
                and Path(qualified.__file__).resolve() == base_backend / 'qualification.py',
                'BACKEND_MODULE_MISMATCH', 'Linux verifier resolved outside the frozen backend')
        _, loaded, checked_sha = workflow.load_manifest(context.raw, path)
        require(loaded == manifest and checked_sha == manifest_sha, 'BACKUP_CHANGED', 'Backup manifest changed during admission')
        parts = [context.raw.partition(label) for label in ('boot', 'userdata')]
        require(manifest.get('source_contract') == context.raw.contract
                and manifest.get('source_geometry') == qualified.geometry_record(parts),
                'BACKUP_DEVICE_MISMATCH', 'Linux backup geometry/storage contract differs from this device')
        for part in parts:
            if part['label'] == 'boot' or current_data:
                context.raw.graph.require_unmounted(part['device'], context.raw.mounts)
                context.raw.graph.require_no_holders(part['device'])
        tools = qualified.toolchain(context.raw)
        admission = qualified.admission(context.raw, parts, tools)
        require(admission['eligible'] and admission['status'] == 'qualified'
                and entries[0]['raw_sha256'] == admission['linux_boot_sha256'],
                'LINUX_RETURN_NOT_QUALIFIED', 'The exact Linux restore path has not passed its owned-image acceptance',
                blockers=admission['blockers'])
        with contextlib.ExitStack() as stack:
            archives = workflow._archives(stack, path, manifest, parts, tools, lease)
            workflow._verify_archives(archives, progress, 'install-backup-verify')
        with context.raw.open_partition(parts[0]) as fd:
            boot_matches = hash_fd(fd.fileno(), parts[0]['size_bytes'], progress=progress, label='boot') == entries[0]['raw_sha256']
        if current_data:
            with context.raw.open_partition(parts[1]) as fd:
                workflow._compact().verify_target(fd.fileno(), entries[1], progress,
                    tool_paths=tools['paths'], inherited_fds=(lease,))
            data_matches = True
        qualification = admission['capsule_id']
    require(not current_boot or boot_matches, 'BACKUP_NO_LONGER_CURRENT', 'Stock installation requires a backup of the current Linux BOOT')
    source.recheck()
    return {'manifest_sha256': manifest_sha, 'complete_sha256': complete_sha,
            'snapshot_id': manifest.get('snapshot_id'), 'linux_boot_sha256': entries[0]['raw_sha256'],
            'qualification': qualification,
            'gate': {'complete': True, 'verified': True, 'same_device': True,
                     'current': boot_matches and data_matches, 'current_required': current_boot and current_data,
                     'current_userdata': data_matches, 'current_userdata_required': current_data, 'profile': 'linux'}}


def image_sources(context, source, source_path, operation, targets, *, progress=None):
    source_path = fs.clean_path(source_path)
    profile, proof, records = None, None, []
    if operation == 'flash-boot':
        require(source_path.lower().endswith('.img'), 'BOOT_IMG_REQUIRED', 'Select a raw .img BOOT file')
        fd = source.open(source_path)
        image = inspect_image(fd, 'none', 'raw', targets['boot']['size_bytes'], label='boot', progress=progress)
        records.append({'label': 'boot', 'path': source_path, 'image': image})
    else:
        require(operation == 'install-stock' and source_path.rsplit('/', 1)[-1] == 'manifest.json',
                'STOCK_MANIFEST_REQUIRED', 'Select the prepared stock manifest.json')
        profile = load_stock_profile(context, required=True)
        bundle, bundle_sha = json_fd(source.open(source_path))
        require(bundle_sha == profile['manifest_sha256'] and bundle.get('schema') == BUNDLE_SCHEMA
                and bundle.get('model') == MODEL and bundle.get('profile_id') == profile['id']
                and bundle.get('firmware') == profile['firmware'] and bundle.get('write_order') == list(STOCK_ORDER),
                'STOCK_BUNDLE_MISMATCH', 'Selected stock bundle differs from the pinned runtime profile')
        images = bundle.get('images')
        require(isinstance(images, list) and len(images) == MAX_IMAGES
                and [entry.get('label') for entry in images] == list(STOCK_ORDER),
                'STOCK_TARGET_MISMATCH', 'Stock bundle must contain the exact ordered target set')
        parent = source_path.rsplit('/', 1)[0]
        for entry in images:
            label, filename = entry['label'], entry.get('file')
            require(isinstance(filename, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', filename),
                    'INVALID_IMAGE_NAME', 'Stock payload paths must be simple regular-file names')
            image_path = parent + '/' + filename
            actual = inspect_image(source.open(image_path), 'gzip', STOCK_FORMAT[label],
                                   targets[label]['size_bytes'], label=label, progress=progress)
            require(actual == entry.get('image'), 'STOCK_PAYLOAD_MISMATCH',
                    'Stock payload, decoded checksum or chunk map differs from its pinned manifest', label=label)
            records.append({'label': label, 'path': image_path, 'image': actual})
        if profile['qualified']:
            proof = verify_protected(context, profile)
    source.recheck()
    return records, profile, proof


def seal_plan(plan):
    value = {k: v for k, v in plan.items() if k not in ('seal', 'confirmation')}
    plan['seal'] = hashlib.sha256(canonical(value)).hexdigest()
    plan['confirmation'] = plan['operation'].upper() + '-' + plan['seal'][:16]
    return plan


def check_plan(context, plan, confirmation=None):
    require(isinstance(plan, dict) and plan.get('schema') == SCHEMA
            and plan.get('operation') in ('flash-boot', 'install-stock'), 'INVALID_INSTALL_PLAN', 'Unexpected installer plan')
    original = dict(plan)
    expected = seal_plan(dict(plan))
    require(original == expected, 'INSTALL_PLAN_CHANGED', 'Installer plan seal or confirmation differs from reviewed content')
    require(plan.get('mode') == context.mode and plan.get('boot_id') == context.boot_id
            and plan.get('device') == context.device and plan.get('context_contract') == context.contract,
            'INSTALL_PLAN_CONTEXT_CHANGED', 'Plan belongs to another mode, recovery boot, device or storage contract')
    now = int(time.time())
    created = number(plan.get('created_utc'), 0, now + 5, 'created_utc')
    expiry = number(plan.get('expires_utc'), created, created + PLAN_SECONDS, 'expires_utc')
    require(now <= expiry, 'INSTALL_PLAN_EXPIRED', 'Create and review a fresh installer plan')
    require(plan.get('required_hold_ms') == (3000 if plan['operation'] == 'flash-boot' else 5000),
            'INVALID_INSTALL_PLAN', 'Installer confirmation hold differs from the fixed operation')
    require(re.fullmatch(r'[0-9a-f]{32}', str(plan.get('transaction_id', ''))),
            'INVALID_INSTALL_PLAN', 'Installer transaction identifier is malformed')
    require(plan.get('journal_path') == JOURNAL_BASE + '/' + plan['transaction_id'] + '/journal.json'
            and plan.get('journal_volume') == plan.get('source_volume')
            and plan.get('journal_volume_token') == plan.get('source_volume_token'),
            'INVALID_INSTALL_PLAN', 'Journal is not bound to the selected source volume and exact transaction')
    if confirmation is not None:
        require(confirmation == plan['confirmation'] and plan.get('can_execute') is True and not plan.get('blockers'),
                'INSTALL_CONFIRMATION_REQUIRED', 'The exact executable reviewed plan confirmation is required')


FLASH_RECORDS = Path('a22x-recovery/flash-records.json')
FLASH_RECORDS_SCHEMA = 'a22x.flash-records.v1'


@contextlib.contextmanager
def flash_records_file(context, mode):
    """Where this recovery notes the partitions it wrote and read back itself (2026-11-10): the cache
    volume on the phone, the fixture's own folder in tests."""
    if context.mode == 'fixture':
        yield context.root / '.flash-records.json'
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import settings
    with settings.volume_root(context.fs, 'cache', mode) as root:
        yield root / FLASH_RECORDS


def parse_flash_records(path):
    if not path.is_file() or path.is_symlink():
        return {}
    value = json.loads(path.read_bytes()[:65536].decode('utf-8'))
    if not isinstance(value, dict) or value.get('schema') != FLASH_RECORDS_SCHEMA or not isinstance(value.get('written'), dict):
        return {}
    return {label: item['sha256'] for label, item in value['written'].items()
            if isinstance(label, str) and re.fullmatch(r'[a-z0-9_]{1,32}', label) and isinstance(item, dict)
            and isinstance(item.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', item['sha256'])}


def read_flash_records(context):
    """{label: sha256} of the last verified write of each partition by Flash a partition; empty when unknown."""
    try:
        with flash_records_file(context, 'ro') as path:
            return parse_flash_records(path)
    except (OSError, ValueError, fs.FilesystemError):
        return {}


def record_flash(context, label, sha256):
    """Note a verified write, so that a normal boot accepts the partition while it holds exactly this."""
    try:
        with flash_records_file(context, 'rw') as path:
            path.parent.mkdir(mode=0o700, exist_ok=True)
            try:
                value = json.loads(path.read_bytes()[:65536].decode('utf-8')) if path.is_file() and not path.is_symlink() else {}
            except ValueError:
                value = {}
            written = value.get('written') if isinstance(value, dict) and value.get('schema') == FLASH_RECORDS_SCHEMA else None
            written = dict(written) if isinstance(written, dict) else {}
            written[label] = {'sha256': sha256, 'utc': int(time.time())}
            part = path.with_name(path.name + '.part')
            part.write_text(json.dumps({'schema': FLASH_RECORDS_SCHEMA, 'written': written}, indent=1, sort_keys=True) + '\n')
            os.replace(part, path)
            os.sync()
        return True
    except (OSError, ValueError, fs.FilesystemError):
        return False


def target_bytes(context, target, limit=64 << 20):
    """A whole small partition (BOOT, dtbo, vbmeta) in memory, to judge it by its contents."""
    require(target['size_bytes'] <= limit, 'TARGET_TOO_LARGE', 'The partition is too large to judge by its contents')
    fd = context.open_target(target)
    try:
        data = bytearray()
        while len(data) < target['size_bytes']:
            chunk = os.pread(fd, min(CHUNK, target['size_bytes'] - len(data)), len(data))
            require(chunk, 'TARGET_READ_FAILED', 'The partition could not be read')
            data += chunk
        context.check_target(fd, target)
        return bytes(data)
    finally:
        os.close(fd)


def target_hash(context, target, progress=None):
    fd = context.open_target(target)
    try:
        value = hash_fd(fd, target['size_bytes'], progress=progress, label=target['label'])
        context.check_target(fd, target)
        return value
    finally:
        os.close(fd)


def _plan(context, source, source_path, backup_path, operation, lease, progress=None, *, transaction=None, fixed_id=None):
    labels = ('boot',) if operation == 'flash-boot' else STOCK_ORDER
    target_labels = sorted(set(labels) | set(CHECKPOINT_LABELS if operation == 'install-stock' else ('boot',)))
    targets = {label: context.target(label) for label in target_labels}
    context.check_offline(list(targets.values()))
    records, profile, protected = image_sources(context, source, source_path, operation, targets, progress=progress)
    records = [record for record in records if record['label'] != 'recovery']
    backup = backup_binding(context, source, backup_path, lease,
        current_data=operation == 'install-stock', current_boot=operation == 'install-stock' and transaction is None, progress=progress)
    source_objects = {tuple(same_object(os.fstat(fd))) for fd, stamp in source.files.values()}
    require(not any(tuple(target['object']) in source_objects for target in targets.values()),
            'SOURCE_TARGET_ALIAS', 'A selected image or backup cannot also be an installation target')
    old_hashes = {label: target_hash(context, target, progress) for label, target in targets.items()}
    blockers = []
    if profile and not profile['qualified']:
        blockers = list(profile['blockers'] or ['STOCK_PROFILE_NOT_QUALIFIED'])
    if profile and context.mode == 'device':
        # This release preserves the proven Linux-return AVB/DTBO baseline.
        # A different future signed platform requires a separate qualification.
        if profile.get('platform_baseline') != PLATFORM_BASELINE or any(old_hashes[label] != expected for label, expected in PLATFORM_BASELINE.items()):
            blockers.append('LINUX_RETURN_PLATFORM_CHANGED')
    writes, skips = [], []
    for record in records:
        label, image = record['label'], record['image']
        target = targets[label]
        same = old_hashes[label] == image['target_sha256']
        item = {key: target[key] for key in ('label', 'path', 'size_bytes', 'partition_number', 'start_sector')}
        item.update(action='skip-identical' if same else 'write-' + image['format'],
                    before_sha256=old_hashes[label], after_sha256=image['target_sha256'])
        (skips if same else writes).append(item)
    identifier = transaction['id'] if transaction else fixed_id or os.urandom(16).hex()
    checkpoint_labels = list(CHECKPOINT_LABELS if operation == 'install-stock' else ('boot',))
    checkpoint_bytes = sum(targets[label]['size_bytes'] for label in checkpoint_labels)
    free = os.fstatvfs(source.pin.fd).f_bavail * os.fstatvfs(source.pin.fd).f_frsize
    if free < (0 if transaction else checkpoint_bytes) + RESERVE + 4 * MAX_JSON:
        blockers.append('INSUFFICIENT_CHECKPOINT_SPACE')
    source.recheck()
    fresh = context.fresh()
    require(fresh.device == context.device and fresh.contract == context.contract and fresh.boot_id == context.boot_id,
            'DEVICE_CHANGED', 'Device identity changed during long source verification')
    require({label: fresh.target(label) for label in targets} == targets,
            'TARGET_CHANGED', 'Target identity changed during long source verification')
    fresh.check_offline(list(targets.values()))
    path = JOURNAL_BASE + '/' + identifier
    created = int(time.time())
    plan = {'schema': SCHEMA, 'operation': operation, 'mode': context.mode, 'boot_id': context.boot_id,
            'device': context.device, 'context_contract': context.contract, 'created_utc': created,
            'expires_utc': created + PLAN_SECONDS, 'transaction_id': identifier,
            'required_hold_ms': 3000 if operation == 'flash-boot' else 5000,
            'can_execute': not blockers, 'blockers': list(dict.fromkeys(blockers)),
            'source_volume': source.volume, 'source_path': fs.clean_path(source_path), 'source_volume_token': source.token,
            'backup_path': backup_path, 'backup_manifest_sha256': backup['manifest_sha256'], 'backup_gate': backup['gate'],
            'stock_profile': {'id': profile['id'], 'firmware': profile['firmware']} if profile else None,
            'stock_profile_file_sha256': profile['file_sha256'] if profile else None,
            'protected_firmware': protected, 'targets': targets, 'target_hashes': old_hashes,
            'images': records, 'source_binding': source.binding(), 'backup_binding': backup,
            'write_targets': writes, 'skip_targets': skips, 'write_order': [x['label'] for x in writes],
            'journal_volume': source.volume, 'journal_volume_token': source.token, 'journal_path': path + '/journal.json',
            'checkpoint': {'path': str(source.path(path + '/platform')), 'labels': checkpoint_labels,
                           'required_bytes': checkpoint_bytes},
            'next_action': 'first-android-boot' if operation == 'install-stock' else 'none',
            'custom_recovery_replaced': False,
            'userdata_written': operation == 'install-stock', 'automatic_factory_reset': False, 'auto_reboot': False, 'atomic': False,
            'resuming': bool(transaction), 'resume_journal_sha256': transaction.get('file_sha256') if transaction else None,
            'original_plan_seal': transaction['original_plan_seal'] if transaction else None}
    if operation == 'flash-boot':
        plan['boot_header'] = records[0]['image']['boot_header']
    return seal_plan(plan)


SUMMARY_KEYS = ('schema', 'operation', 'mode', 'boot_id', 'created_utc', 'expires_utc', 'seal', 'confirmation',
    'transaction_id', 'required_hold_ms', 'can_execute', 'blockers', 'source_volume', 'source_path', 'source_volume_token',
    'backup_path', 'backup_manifest_sha256', 'backup_gate', 'stock_profile', 'write_targets', 'skip_targets', 'write_order',
    'journal_volume', 'journal_volume_token', 'journal_path', 'checkpoint', 'next_action', 'custom_recovery_replaced',
    'userdata_written', 'automatic_factory_reset', 'auto_reboot', 'atomic', 'resuming', 'boot_header')


def plan_result(context, plan, command, output=None):
    result = {'ok': True, 'schema': 'a22x.install.plan-result.v1', 'operation': command, 'mode': context.mode,
              'boot_id': context.boot_id, 'plan': plan}
    if output is None:
        return result
    path = fs.absolute_no_symlinks(output, missing=True)
    require(path.parent.is_dir(), 'INVALID_PLAN_OUTPUT', 'Use a new file in an existing private RAM directory')
    parent = path.parent.stat()
    if context.mode == 'device':
        require(parent.st_uid == 0 and not parent.st_mode & 0o022,
                'INVALID_PLAN_OUTPUT', 'Plan directory must be root-owned and not writable by others')
        mounts = [m for m in fs.read_mounts() if path.parent == Path(m['mountpoint']) or Path(m['mountpoint']) in path.parent.parents]
        require(mounts and max(mounts, key=lambda m: len(m['mountpoint']))['fs_type'] in ('rootfs', 'tmpfs', 'ramfs'),
                'PLAN_OUTPUT_NOT_RAM', 'Plans must be saved in private RAM')
    else:
        require(context.root in path.parents and all(Path(v['path']) not in path.parents for v in context.fs.volumes()),
                'INVALID_PLAN_OUTPUT', 'Fixture plans must stay outside their volume directories')
    data = canonical(result) + b'\n'
    require(len(data) <= MAX_JSON, 'PLAN_TOO_LARGE', 'Full installer plan exceeds its bound')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return {**result, 'plan': {k: plan[k] for k in SUMMARY_KEYS if k in plan},
            'plan_summary': True, 'plan_path': str(path), 'plan_file_sha256': hashlib.sha256(data).hexdigest()}


def load_plan_file(path, expected_seal, expected_file_sha256):
    sha(expected_seal, 'expected-plan-seal')
    sha(expected_file_sha256, 'expected-plan-file-sha256')
    path = fs.absolute_no_symlinks(path)
    fd = fs.open_regular(path)
    try:
        value, actual = json_fd(fd)
    finally:
        os.close(fd)
    require(actual == expected_file_sha256 and isinstance(value.get('plan'), dict)
            and value['plan'].get('seal') == expected_seal,
            'INSTALL_PLAN_FILE_CHANGED', 'The full plan file differs from the exact reviewed UI result')
    return value['plan']


def write_all(fd, data, *, offset=None):
    view = memoryview(data)
    done = 0
    while view:
        written = os.write(fd, view) if offset is None else os.pwrite(fd, view, offset + done)
        require(isinstance(written, int) and 0 < written <= len(view), 'SHORT_WRITE', 'Write made no valid progress')
        done += written
        view = view[written:]
    return done


class Store:
    """Private journal paths relative to an already pinned external volume."""
    def __init__(self, source):
        self.source, self.pin = source, source.pin

    def exists(self, path):
        try:
            with self.pin.parent(path) as (parent, name):
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def mkdir(self, path, *, exist=False):
        with self.pin.parent(path) as (parent, name):
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                require(exist, 'INSTALL_ALREADY_STARTED', 'This transaction directory already exists')
            st = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077,
                    'UNTRUSTED_JOURNAL_DIRECTORY', 'Journal directories must be private and owned by the recovery user')

    def open(self, path, *, create=False):
        with self.pin.parent(path) as (parent, name):
            if create:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            else:
                handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                try:
                    st = os.fstat(handle)
                    require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1,
                            'INVALID_JOURNAL_FILE', 'Journal/checkpoint files must be single regular inodes')
                    fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
                finally:
                    os.close(handle)
            self.pin.check_fd(fd)
            st = os.fstat(fd)
            require(st.st_uid == os.geteuid() and not st.st_mode & 0o077 and st.st_nlink == 1,
                    'UNTRUSTED_JOURNAL_FILE', 'Journal/checkpoint files must be private and owned')
            return fd

    def read(self, path):
        fd = self.open(path)
        try:
            return json_fd(fd)
        finally:
            os.close(fd)

    def write(self, path, value, *, create=False):
        raw = canonical(value) + b'\n'
        require(len(raw) <= MAX_JSON, 'JOURNAL_TOO_LARGE', 'Journal exceeds its metadata bound')
        with self.pin.parent(path) as (parent, name):
            temporary = '.' + name + '-' + os.urandom(8).hex()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            try:
                write_all(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            if create:
                rename_new(parent, temporary, parent, name)
            else:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
                        and not before.st_mode & 0o077, 'UNTRUSTED_JOURNAL_FILE', 'Journal update target changed')
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        return hashlib.sha256(raw).hexdigest()


def rename_new(source_fd, source, destination_fd, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def seal_record(value):
    value = dict(value)
    value.pop('seal', None)
    value['seal'] = hashlib.sha256(canonical(value)).hexdigest()
    return value


def stable_target(target):
    return {key: target[key] for key in ('label', 'path', 'size_bytes', 'partition_number', 'start_sector', 'partuuid')}


def load_journal(context, source, path):
    require(re.fullmatch(re.escape(JOURNAL_BASE) + r'/[0-9a-f]{32}/journal\.json', path),
            'INVALID_JOURNAL_PATH', 'Select an exact installer transaction journal')
    store = Store(source)
    journal, file_sha = store.read(path)
    require(journal == seal_record(journal) and journal.get('schema') == 'a22x.install.journal.v1'
            and journal.get('id') == path.split('/')[-2] and journal.get('mode') == context.mode
            and journal.get('device') == context.device and journal.get('source_volume_token') == source.token,
            'INVALID_INSTALL_JOURNAL', 'Journal seal, device, media or transaction identity differs')
    require(journal.get('state') in ('preparing', 'ready', 'writing', 'verifying', 'completed')
            and isinstance(journal.get('checkpoint_complete'), bool)
            and isinstance(journal.get('readback'), dict),
            'INVALID_INSTALL_JOURNAL', 'Journal lifecycle state is invalid')
    original, original_sha = store.read(path.rsplit('/', 1)[0] + '/plan.json')
    require(original_sha == journal.get('original_plan_file_sha256') and original == seal_plan(dict(original))
            and original.get('seal') == journal.get('original_plan_seal')
            and original.get('schema') == SCHEMA and original.get('transaction_id') == journal['id']
            and original.get('mode') == context.mode and original.get('device') == context.device
            and original.get('operation') == journal.get('operation')
            and original.get('operation') in ('flash-boot', 'install-stock')
            and original.get('source_volume_token') == source.token and original.get('journal_path') == path,
            'INVALID_ORIGINAL_PLAN', 'Journal does not bind its original reviewed operation')
    labels = set(('boot',) if original['operation'] == 'flash-boot' else STOCK_ORDER)
    require(set(original.get('targets', {})) == labels
            and [record.get('label') for record in original.get('images', [])] ==
                (['boot'] if original['operation'] == 'flash-boot' else list(STOCK_WRITE)),
            'INVALID_ORIGINAL_PLAN', 'Original plan has an unexpected target set')
    for label, target in original['targets'].items():
        require(stable_target(context.target(label)) == stable_target(target),
                'JOURNAL_TARGET_CHANGED', 'Original transaction target geometry/device changed')
    require(set(journal['readback']) <= labels and journal.get('active_label') in labels | {None},
            'INVALID_INSTALL_JOURNAL', 'Journal names an unexpected write target')
    expected = {record['label']: record['image']['target_sha256'] for record in original['images']}
    require(all(value == expected[label] for label, value in journal['readback'].items()),
            'INVALID_INSTALL_JOURNAL', 'Journal readback checkpoint differs from its original image')
    journal['file_sha256'] = file_sha
    return journal, original


def journal_update(store, path, journal, **values):
    journal = {k: v for k, v in journal.items() if k not in ('seal', 'file_sha256')}
    journal.update(values)
    journal['sequence'] = number(journal.get('sequence', 0), 0, 1000000, 'journal_sequence') + 1
    journal = seal_record(journal)
    store.write(path, journal)
    return journal


def set_guard(context, source, journal):
    value = {'schema': 'a22x.install.sentinel.v1', 'transaction_id': journal['id'],
             'boot_id': context.boot_id, 'device': context.device,
             'original_plan_seal': journal['original_plan_seal'],
             'journal_volume': source.volume, 'journal_volume_token': source.token,
             'journal_path': JOURNAL_BASE + '/' + journal['id'] + '/journal.json'}
    if context.guard_path.exists():
        existing = fs.regular_json(context.guard_path, MAX_JSON)
        require(existing.get('transaction_id') == journal['id']
                and existing.get('original_plan_seal') == journal['original_plan_seal']
                and existing.get('device') == context.device,
                'INSTALL_GUARD_CONFLICT', 'A different or malformed installer transaction guards this boot')
    fs.atomic_state(context.guard_path, value)


def clear_guard(context, journal):
    if not context.guard_path.exists():
        return
    existing = fs.regular_json(context.guard_path, MAX_JSON)
    require(existing.get('transaction_id') == journal['id']
            and existing.get('original_plan_seal') == journal['original_plan_seal']
            and journal.get('state') == 'completed', 'INSTALL_GUARD_CONFLICT', 'Only exact completed installation may clear its boot guard')
    context.guard_path.unlink()
    fd = os.open(context.guard_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def checkpoint_platform(context, source, original, journal, progress=None, hook=None, *, verify_only=False):
    store = Store(source)
    base = original['journal_path'].rsplit('/', 1)[0] + '/platform'
    if not verify_only:
        store.mkdir(base, exist=True)
    expected_labels = list(CHECKPOINT_LABELS if original['operation'] == 'install-stock' else ('boot',))
    require(original['checkpoint']['labels'] == expected_labels, 'INVALID_CHECKPOINT', 'Checkpoint target scope changed')
    for label in expected_labels:
        target = context.target(label)
        expected = original['target_hashes'][label]
        destination = base + '/' + label + '.img'
        if not store.exists(destination):
            require(not verify_only, 'PLATFORM_CHECKPOINT_MISSING', 'A completed platform checkpoint is missing')
            require(not journal['checkpoint_complete'] and not journal['readback']
                    and journal['state'] == 'preparing', 'PLATFORM_CHECKPOINT_MISSING', 'A checkpoint needed for an existing write disappeared')
            require(target_hash(context, target) == expected, 'CHECKPOINT_SOURCE_CHANGED', 'Original platform changed before its checkpoint completed')
            temporary = base + '/.' + label + '-' + os.urandom(8).hex() + '.partial'
            output = store.open(temporary, create=True)
            source_fd = context.open_target(target)
            try:
                position, digest = 0, hashlib.sha256()
                while position < target['size_bytes']:
                    data = os.pread(source_fd, min(CHUNK, target['size_bytes'] - position), position)
                    require(data, 'CHECKPOINT_SHORT_READ', 'Platform checkpoint source ended early')
                    write_all(output, data)
                    digest.update(data)
                    position += len(data)
                    if hook:
                        hook('checkpoint-data', label, position)
                require(digest.hexdigest() == expected, 'CHECKPOINT_SOURCE_CHANGED', 'Platform changed while copying its checkpoint')
                os.fsync(output)
                require(hash_fd(output, target['size_bytes']) == expected, 'CHECKPOINT_READBACK_FAILED', 'Platform checkpoint readback failed')
            finally:
                os.close(source_fd)
                os.close(output)
            with source.pin.parent(destination) as (parent, name):
                rename_new(parent, temporary.rsplit('/', 1)[-1], parent, name)
                os.fsync(parent)
        fd = store.open(destination)
        try:
            require(os.fstat(fd).st_size == target['size_bytes'] and hash_fd(fd, target['size_bytes']) == expected,
                    'CHECKPOINT_READBACK_FAILED', 'Retained platform checkpoint size/hash differs')
        finally:
            os.close(fd)
    source.pin.recheck()
    if verify_only:
        return journal
    return journal_update(store, original['journal_path'], journal, checkpoint_complete=True,
                          state='ready' if journal['state'] == 'preparing' else journal['state'])


KEPT_JOURNALS = 64


def prune_journals(context, source, store):
    """2026-11-12 review: every installation leaves its journal on the card, nothing removed them,
    and from the 256th on every status, plan and normal-start check failed. Before a new one is
    made, only the newest KEPT_JOURNALS completed journals stay. A journal not completed, the one the
    guard names, and a folder without a journal (never guessed at) are never removed."""
    with source.pin.directory(JOURNAL_BASE) as directory:
        names = [name for name in os.listdir(directory) if re.fullmatch(r'[0-9a-f]{32}', name)]
    guard = fs.regular_json(context.guard_path, MAX_JSON) if context.guard_path.exists() else {}
    done = []
    for name in names:
        path = JOURNAL_BASE + '/' + name + '/journal.json'
        if not store.exists(path) or guard.get('transaction_id') == name:
            continue
        try:
            journal, _ = load_journal(context, source, path)
        except (OSError, ValueError, fs.FilesystemError):
            continue
        if journal['state'] == 'completed' and journal['id'] == name:
            with source.pin.directory(JOURNAL_BASE) as directory:
                done.append((os.stat(name + '/journal.json', dir_fd=directory).st_mtime_ns, name))
    for _, name in sorted(done)[:-KEPT_JOURNALS] if len(done) > KEPT_JOURNALS else ():
        with source.pin.directory(JOURNAL_BASE) as directory:
            shutil.rmtree(name, dir_fd=directory)
            os.fsync(directory)


def pending_transactions(context, *, completed=None):
    pending, incomplete, present = [], [], False
    volumes = context.fs.volumes()
    candidates = [v for v in volumes if v.get('external') or context.mode == 'fixture' and v['id'] in context.config.get('external_volumes', ['sdcard-p1'])]
    require(len(candidates) <= 8, 'TOO_MANY_JOURNAL_VOLUMES', 'External journal inventory exceeds its bound')
    for volume in candidates:
        if not volume['mounted'] or volume['fs_type'] != 'ext4':
            continue
        present = True
        with Source(context, volume['id'], volume['volume_token'], writing=False) as source:
            store = Store(source)
            if not store.exists(JOURNAL_BASE):
                continue
            with source.pin.directory(JOURNAL_BASE) as directory:
                names = os.listdir(directory)
            require(len(names) <= 256, 'TOO_MANY_INSTALL_JOURNALS', 'Installer journal history exceeds its bounded inventory')
            for name in names:
                if name == 'owner.json':
                    continue
                require(re.fullmatch(r'[0-9a-f]{32}', name), 'INVALID_INSTALL_JOURNAL', 'Unexpected object in private installer journal directory')
                path = JOURNAL_BASE + '/' + name + '/journal.json'
                if not store.exists(path):
                    # No writer can run before this record is durable. Preserve
                    # orphan preparation directories; never delete guessed data.
                    incomplete.append({'journal_volume': volume['id'], 'transaction_id': name})
                    continue
                journal, original = load_journal(context, source, path)
                guarded = False
                if context.guard_path.exists():
                    guard = fs.regular_json(context.guard_path, MAX_JSON)
                    guarded = guard.get('transaction_id') == journal['id']
                if journal['state'] != 'completed' or guarded:
                    pending.append({'transaction_id': journal['id'], 'operation': journal['operation'],
                        'journal_volume': source.volume, 'journal_path': path, 'journal_volume_token': source.token,
                        'state': 'completion-unconfirmed' if journal['state'] == 'completed' else journal['state'],
                        'resumable': True})
                elif completed is not None:
                    expected = {item['label']: item['image']['target_sha256'] for item in original['images']}
                    require(journal['checkpoint_complete'] and journal['readback'] == expected
                            and journal['active_label'] is None, 'INVALID_INSTALL_JOURNAL',
                            'Completed journal lacks all verified final target hashes')
                    require(len(completed) < 256, 'TOO_MANY_INSTALL_JOURNALS',
                            'Completed boot proof inventory exceeds its bound')
                    completed.append({'id': journal['id'], 'operation': journal['operation'],
                                      'targets': original['targets'], 'hashes': expected})
    require(len(pending) <= 16, 'TOO_MANY_PENDING_INSTALLS', 'Pending install inventory exceeds its bound')
    return pending, incomplete[:16], present


def make_plan(context, source_volume, source_path, expected_source_token, backup_path, operation, progress=None):
    with operation_lock(context.lock_path, context.mode) as lease:
        context = context.fresh()
        pending, _, _ = pending_transactions(context)
        require(not pending and not context.guard_path.exists(), 'INSTALL_PENDING', 'Review and resume the existing installer transaction first')
        with Source(context, source_volume, expected_source_token) as source:
            return _plan(context, source, source_path, backup_path, operation, lease, progress)


def resume_plan(context, journal_volume, journal_path, expected_journal_token, progress=None):
    with operation_lock(context.lock_path, context.mode) as lease:
        context = context.fresh()
        with Source(context, journal_volume, expected_journal_token) as source:
            journal, original = load_journal(context, source, journal_path)
            plan = _plan(context, source, original['source_path'], original['backup_path'], original['operation'],
                         lease, progress, transaction=journal)
            require(plan['images'] == original['images'] and plan['backup_manifest_sha256'] == original['backup_manifest_sha256'],
                    'RESUME_SOURCE_CHANGED', 'Resume source or original Linux snapshot differs from the interrupted operation')
            if not journal['checkpoint_complete']:
                require(journal['state'] == 'preparing' and plan['target_hashes'] == original['target_hashes'],
                        'CHECKPOINT_SOURCE_CHANGED', 'Incomplete checkpoint cannot be rebuilt after target changes')
            else:
                checkpoint_platform(context, source, original, journal, verify_only=True)
            return seal_plan(plan)


def execute(context, plan, confirmation, progress=None, hook=None):
    check_plan(context, plan, confirmation)
    with operation_lock(context.lock_path, context.mode) as lease:
        context = context.fresh()
        check_plan(context, plan, confirmation)
        with Source(context, plan['source_volume'], plan['source_volume_token']) as source:
            store = Store(source)
            transaction, original = (None, plan)
            if plan['resuming']:
                transaction, original = load_journal(context, source, plan['journal_path'])
                require(transaction['file_sha256'] == plan['resume_journal_sha256']
                        and transaction['original_plan_seal'] == plan['original_plan_seal'],
                        'RESUME_JOURNAL_CHANGED', 'The reviewed interrupted transaction changed')
            else:
                pending, _, _ = pending_transactions(context)
                require(not pending and not context.guard_path.exists(), 'INSTALL_PENDING', 'An installer transaction is already pending')
            fresh = _plan(context, source, plan['source_path'], plan['backup_path'], plan['operation'],
                          lease, progress, transaction=transaction, fixed_id=plan['transaction_id'])
            compare = ('images', 'targets', 'target_hashes', 'source_binding', 'backup_binding', 'stock_profile_file_sha256',
                       'protected_firmware', 'write_targets', 'skip_targets', 'write_order', 'can_execute', 'blockers')
            require(all(fresh[key] == plan[key] for key in compare), 'INSTALL_PLAN_STALE',
                    'Source, backup, target contents or qualification changed after review')
            if transaction:
                require(plan['images'] == original['images'] and plan['backup_manifest_sha256'] == original['backup_manifest_sha256'],
                        'RESUME_SOURCE_CHANGED', 'Interrupted installation is bound to different source content')
            # Everything is fully verified before a journal/target writer opens.
            if hook:
                hook('preverified', None, 0)
            if transaction is None:
                store.mkdir(JOURNAL_BASE, exist=True)
                owner = {'schema': 'a22x.install.owner.v1', 'device': context.device, 'mode': context.mode}
                if store.exists(JOURNAL_BASE + '/owner.json'):
                    require(store.read(JOURNAL_BASE + '/owner.json')[0] == owner, 'JOURNAL_OWNER_MISMATCH', 'Journal directory belongs to another device')
                else:
                    with source.pin.directory(JOURNAL_BASE) as directory:
                        require(not os.listdir(directory), 'UNOWNED_JOURNAL_DIRECTORY', 'A nonempty installer directory lacks its ownership record')
                    store.write(JOURNAL_BASE + '/owner.json', owner, create=True)
                prune_journals(context, source, store)
                base = plan['journal_path'].rsplit('/', 1)[0]
                store.mkdir(base)
                original_sha = store.write(base + '/plan.json', plan, create=True)
                transaction = seal_record({'schema': 'a22x.install.journal.v1', 'id': plan['transaction_id'],
                    'mode': context.mode, 'device': context.device, 'operation': plan['operation'],
                    'source_volume_token': source.token, 'original_plan_seal': plan['seal'],
                    'original_plan_file_sha256': original_sha, 'state': 'preparing', 'sequence': 0,
                    'checkpoint_complete': False, 'active_label': None, 'readback': {}})
                store.write(plan['journal_path'], transaction, create=True)
            set_guard(context, source, transaction)
            try:
                transaction = checkpoint_platform(context, source, original, transaction, progress, hook)
                if hook:
                    hook('checkpoint-complete', None, 0)
                source.recheck()
                admitted = context.fresh()
                require(admitted.device == context.device and admitted.boot_id == context.boot_id
                        and admitted.contract == context.contract
                        and {label: admitted.target(label) for label in plan['targets']} == plan['targets'],
                        'TARGET_CHANGED', 'Partition or device identity changed before opening writers')
                admitted.check_offline(list(plan['targets'].values()))
                # Checkpoint creation can take time. Recheck the complete
                # reviewed preimage after it, before any writable target FD
                # opens; same inode/geometry alone cannot prove unchanged data.
                require(all(target_hash(admitted, plan['targets'][label], progress) == expected
                            for label, expected in plan['target_hashes'].items()),
                        'TARGET_CONTENT_CHANGED', 'Reviewed target contents changed before opening writers')
                if plan['operation'] == 'install-stock':
                    profile = load_stock_profile(admitted, required=True)
                    require(profile['file_sha256'] == plan['stock_profile_file_sha256'],
                            'STOCK_PROFILE_CHANGED', 'Qualified stock profile changed during long prewrite verification')
                    require(verify_protected(admitted, profile) == plan['protected_firmware'],
                            'PROTECTED_FIRMWARE_CHANGED', 'Retained firmware or its fresh active selector changed before writing')
                source.recheck()
                with contextlib.ExitStack() as stack:
                    writers = {}
                    # Pre-open every changed target before the first write, so a
                    # later busy/read-only target cannot surprise a partial job.
                    # A no-op plan never opens a target with write=True.
                    for label in plan['write_order']:
                        fd = admitted.open_target(plan['targets'][label], write=True)
                        writers[label] = fd
                        stack.callback(os.close, fd)
                    for record in plan['images']:
                        label, entry = record['label'], record['image']
                        if label not in writers:
                            require(target_hash(admitted, plan['targets'][label], progress) == entry['target_sha256'],
                                    'SKIP_TARGET_CHANGED', 'An identical skipped target changed before completion')
                            transaction['readback'][label] = entry['target_sha256']
                            transaction = journal_update(store, plan['journal_path'], transaction)
                            continue
                        transaction = journal_update(store, plan['journal_path'], transaction, state='writing', active_label=label)
                        if hook:
                            hook('before-write', label, 0)
                        reader = ImageReader(source.open(record['path']), entry['compression'])
                        position, index = 0, 0
                        for data in fixed_chunks(decode_image(reader, entry['format'], entry['target_bytes'])):
                            require(index < len(entry['chunk_sha256']) and hashlib.sha256(data).hexdigest() == entry['chunk_sha256'][index],
                                    'SOURCE_CHUNK_CHANGED', 'Input chunk differs from its fully preverified checksum', label=label, chunk=index)
                            require(position + len(data) <= plan['targets'][label]['size_bytes'], 'WRITE_BOUNDS', 'Verified write exceeds exact target size')
                            admitted.check_target(writers[label], plan['targets'][label])
                            if hook:
                                hook('before-chunk', label, position)
                            write_all(writers[label], data, offset=position)
                            position += len(data)
                            index += 1
                            if hook:
                                hook('after-chunk', label, position)
                            if progress:
                                progress({'event': 'progress', 'operation': 'install', 'phase': 'write',
                                    'partition': label, 'bytes_done': position, 'bytes_total': entry['target_bytes']})
                        require(index == len(entry['chunk_sha256']) and position == entry['target_bytes']
                                and reader.encoded_hash.hexdigest() == entry['encoded_sha256']
                                and reader.input_hash.hexdigest() == entry['input_sha256'],
                                'SOURCE_CHANGED', 'Complete stream differs from its preverified image')
                        os.fsync(writers[label])
                        drop_cached(writers[label])  # read back from the eMMC, not the page cache (2026-11-14 review)
                        transaction = journal_update(store, plan['journal_path'], transaction, state='verifying')
                        require(hash_fd(writers[label], entry['target_bytes'], progress=progress, label=label, phase='readback') == entry['target_sha256'],
                                'INSTALL_READBACK_MISMATCH', 'Complete target readback differs from the verified image', label=label)
                        admitted.check_target(writers[label], plan['targets'][label])
                        transaction['readback'][label] = entry['target_sha256']
                        transaction = journal_update(store, plan['journal_path'], transaction, state='ready', active_label=None)
                        if hook:
                            hook('after-readback', label, position)
                source.recheck()
                require(set(transaction['readback']) == {record['label'] for record in plan['images']},
                        'INSTALL_INCOMPLETE', 'Not every intended partition has a verified final readback')
                if plan['operation'] == 'install-stock':
                    # Stock fstab has no formattable flag on /data: Android cannot create it.
                    # Repeating this after an interruption is safe: Linux is already gone.
                    if progress:
                        progress({'event': 'progress', 'operation': 'install', 'phase': 'reset-userdata',
                                  'partition': 'userdata', 'bytes_done': 0, 'bytes_total': 1})
                    context.reset_android()
                if hook:
                    hook('before-complete', None, 0)
                transaction = journal_update(store, plan['journal_path'], transaction, state='completed', active_label=None)
                if hook:
                    hook('after-complete', None, 0)
                clear_guard(context, transaction)
                if hook:
                    hook('after-clear', None, 0)
                return {'ok': True, 'schema': 'a22x.install.result.v1', 'operation': 'execute',
                    'install_operation': plan['operation'], 'mode': context.mode, 'boot_id': context.boot_id,
                    'transaction_id': plan['transaction_id'], 'readback_verified': True,
                    'journal_path': str(source.path(plan['journal_path'])), 'checkpoint_path': original['checkpoint']['path'],
                    'next_action': plan['next_action'], 'userdata_written': plan['userdata_written'], 'automatic_factory_reset': False,
                    'auto_reboot': False, 'atomic': False, 'physical_partition_writes': context.mode == 'device' and bool(writers)}
            except BaseException as error:
                # Keep the last durable phase and RAM guard. No automatic retry,
                # rollback, reboot, or claim about untouched trailing bytes.
                try:
                    transaction = journal_update(store, plan['journal_path'], transaction,
                        error_code=getattr(error, 'code', type(error).__name__))
                except BaseException:
                    pass
                error.install_transaction_id = plan['transaction_id']
                error.install_journal_path = str(source.path(plan['journal_path']))
                raise


def status(context):
    with operation_lock(context.lock_path, context.mode):
        context = context.fresh()
        readiness = ready(context)
        pending, incomplete, present = pending_transactions(context)
        blockers = list(readiness['blockers'])
        if context.guard_path.exists() and not pending:
            blockers.append('INSTALL_GUARD_WITHOUT_AVAILABLE_JOURNAL')
        return {'ok': True, 'schema': 'a22x.install.status.v1', 'operation': 'status',
                'mode': context.mode, 'boot_id': context.boot_id,
                'capabilities': {'flash_boot': True, 'install_stock': True, 'resume': True,
                    'android_mount': False, 'android_backup_restore': False, 'userdata_reset': True},
                'required_hold_ms': {'flash-boot': 3000, 'install-stock': 5000},
                'backup_required': True, 'stock_profile': readiness['stock_profile'],
                'blockers': blockers, 'pending': pending, 'incomplete_preparations': incomplete,
                'journal_storage_present': present, 'auto_reboot': False}


def boot_check(context):
    """Intentional Normal boot only; no guard against physical loss of power."""
    with operation_lock(context.lock_path, context.mode):
        context = context.fresh()
        owned = []
        try:
            require(not context.guard_path.exists(), 'INSTALL_PENDING',
                    'An installer transaction still guards this boot; review its exact resume plan')
            for volume in context.fs.volumes():
                external = volume.get('external') if context.mode == 'device' else volume['id'] in context.config.get('external_volumes', ['sdcard-p1'])
                if external and not volume['mounted'] and volume['fs_type'] == 'ext4' and volume['mountable']:
                    context.fs.mount(volume['id'], 'ro', False, volume['volume_token'])
                    owned.append((volume['id'], volume['volume_token']))
            context = context.fresh()
            completed = []
            pending, _, present = pending_transactions(context, completed=completed)
            require(not pending, 'INSTALL_PENDING', 'The SD contains an interrupted installer transaction',
                    transaction_ids=[item['transaction_id'] for item in pending])
            baseline = {**PLATFORM_BASELINE, 'boot': LINUX_BOOT}
            if context.mode == 'fixture':
                baseline = context.config.get('known_linux_baseline', {})
            require(set(baseline) == {'boot', 'dtbo', 'vbmeta', 'vbmeta_system'},
                    'BOOT_SAFETY_UNPROVEN', 'A complete tested Linux baseline is required for the normal-boot guard')
            observed = {}
            def current(label):
                if label not in observed:
                    observed[label] = target_hash(context, context.target(label))
                return observed[label]
            known_linux = all(current(label) == expected or (label == 'boot' and context.mode == 'device'
                                                              and current(label) in (LINUX_BOOT_K108, LINUX_BOOT_CHARGER_OFF, LINUX_BOOT_K116, LINUX_BOOT_K121, LINUX_BOOT_K122, LINUX_BOOT_K138))
                              for label, expected in baseline.items())
            verified_transaction = None
            verified_flash, verified_contents = [], []
            if not known_linux:
                # Merely inserting an unrelated empty card is not proof that
                # the original installation completed. Bind an exact durable
                # completed transaction to its current target contents instead.
                for candidate in completed:
                    if not all(current(label) == expected for label, expected in candidate['hashes'].items()):
                        continue
                    if candidate['operation'] == 'flash-boot' and not all(
                            current(label) == baseline[label] for label in ('dtbo', 'vbmeta', 'vbmeta_system')):
                        continue
                    verified_transaction = candidate['id']
                    break
                if verified_transaction is None:
                    # 2026-11-10: a changed partition is accepted while it holds exactly what Flash a
                    # partition wrote and read back there, or when its contents show a complete image
                    # for this phone (bootimage.py): an Android or Linux BOOT, a DT table, a vbmeta.
                    # A write that stopped part way breaks the kernel stream or an AVB hash instead.
                    records = read_flash_records(context)
                    changed = [label for label, expected in baseline.items() if current(label) != expected and not (
                        label == 'boot' and context.mode == 'device' and current(label) in (
                            LINUX_BOOT_K108, LINUX_BOOT_CHARGER_OFF, LINUX_BOOT_K116, LINUX_BOOT_K121, LINUX_BOOT_K122, LINUX_BOOT_K138))]
                    problems = {}
                    for label in changed:
                        if records.get(label) == current(label):
                            verified_flash.append(label)
                            continue
                        try:
                            bootimage.inspect(label, target_bytes(context, context.target(label)))
                            verified_contents.append(label)
                        except bootimage.ImageError as exc:
                            problems[label] = exc.code
                    require(changed and not problems, 'BOOT_SAFETY_UNPROVEN',
                            'Changed %s is not a complete image for this phone (%s); flash a good image or use Recovery repair'
                            % (', '.join(sorted(problems)) or 'BOOT', ', '.join(sorted(set(problems.values()))) or 'no proof'),
                            partitions=problems)
                require(verified_transaction is not None or verified_flash or verified_contents, 'BOOT_SAFETY_UNPROVEN',
                        'Changed BOOT lacks a completed matching transaction; use Recovery repair or reinsert its source SD')
            return {'ok': True, 'schema': 'a22x.install.boot-check.v1', 'operation': 'boot-check',
                    'mode': context.mode, 'boot_id': context.boot_id, 'normal_boot_allowed': True,
                    'journal_storage_present': present, 'durable_state_checked': present,
                    'known_linux_boot_safe': known_linux, 'verified_completed_transaction': verified_transaction,
                    'verified_recovery_flash': verified_flash, 'verified_by_contents': verified_contents,
                    'pending': False, 'auto_reboot': False}
        finally:
            for ident, token in reversed(owned):
                context.fs.unmount(ident, token)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fs.fail('INVALID_ARGUMENT', message)


def parser():
    result = Parser(description=__doc__)
    result.add_argument('--mode', choices=('device', 'fixture'), required=True)
    result.add_argument('--fixture')
    commands = result.add_subparsers(dest='command', required=True, parser_class=Parser)
    commands.add_parser('status')
    commands.add_parser('boot-check')
    for name in ('boot-plan', 'stock-plan'):
        command = commands.add_parser(name)
        command.add_argument('--source-volume', required=True)
        command.add_argument('--source-path', required=True)
        command.add_argument('--expected-source-token', required=True)
        command.add_argument('--backup-path', required=True)
        command.add_argument('--output')
    command = commands.add_parser('resume-plan')
    command.add_argument('--journal-volume', required=True)
    command.add_argument('--journal-path', required=True)
    command.add_argument('--expected-journal-token', required=True)
    command.add_argument('--output')
    command = commands.add_parser('execute')
    command.add_argument('--plan-file', required=True)
    command.add_argument('--expected-plan-seal', required=True)
    command.add_argument('--expected-plan-file-sha256', required=True)
    command.add_argument('--confirmation', required=True)
    return result


def main(argv=None):
    context, operation, mode = None, 'unknown', 'unknown'
    last = [None, 0.0]
    def progress(value):
        value = {**value, 'operation': operation}
        key = (value.get('phase'), value.get('partition'))
        now = time.monotonic()
        if key != last[0] or now - last[1] >= 0.2 or value.get('bytes_done') == value.get('bytes_total'):
            with contextlib.suppress(OSError, ValueError):  # best effort: a write never depends on it (2026-11-12 review)
                print(json.dumps(value, separators=(',', ':')), file=sys.stderr, flush=True)
            last[:] = [key, now]
    try:
        args = parser().parse_args(argv)
        operation, mode = args.command, args.mode
        require(bool(args.fixture) == (mode == 'fixture'), 'INVALID_ARGUMENT', 'Only fixture mode requires an owned fixture directory')
        context = FixtureContext(args.fixture) if mode == 'fixture' else DeviceContext()
        if operation == 'status':
            result = status(context)
        elif operation == 'boot-check':
            result = boot_check(context)
        elif operation in ('boot-plan', 'stock-plan'):
            sha(args.expected_source_token, 'expected-source-token')
            plan = make_plan(context, args.source_volume, args.source_path, args.expected_source_token,
                             args.backup_path, 'flash-boot' if operation == 'boot-plan' else 'install-stock', progress)
            result = plan_result(context, plan, operation, args.output)
        elif operation == 'resume-plan':
            sha(args.expected_journal_token, 'expected-journal-token')
            plan = resume_plan(context, args.journal_volume, args.journal_path, args.expected_journal_token, progress)
            result = plan_result(context, plan, operation, args.output)
        else:
            plan = load_plan_file(args.plan_file, args.expected_plan_seal, args.expected_plan_file_sha256)
            result = execute(context, plan, args.confirmation, progress)
        print(json.dumps(result, separators=(',', ':'), ensure_ascii=True), flush=True)
        return 0
    except BaseException as error:
        if isinstance(error, (SystemExit, GeneratorExit)):
            raise
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError)
            else 'INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'IO_ERROR' if isinstance(error, OSError) else 'INVALID_INPUT')
        details = dict(getattr(error, 'details', {}))
        details['auto_reboot'] = False
        if hasattr(error, 'install_transaction_id'):
            details.update(transaction_id=error.install_transaction_id, journal_path=error.install_journal_path,
                           partial_install_possible=True)
        result = {'ok': False, 'operation': operation, 'mode': mode,
                  'error': {'code': code, 'message': str(error), 'details': details}}
        if context is not None:
            result['boot_id'] = context.boot_id
        print(json.dumps(result, separators=(',', ':'), ensure_ascii=True), flush=True)
        return 130 if isinstance(error, KeyboardInterrupt) else 2


if __name__ == '__main__':
    sys.exit(main())
