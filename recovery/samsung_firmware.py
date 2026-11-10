#!/usr/bin/env python3
"""Samsung firmware archives for the stock installer (2026-11-09).

Samsung ships a22x firmware as tar archives for Odin: AP_ (Android: super, boot, dtbo, vbmeta,
...), CSC_ / HOME_CSC_ (prism, optics, cache), BL_ (bootloader chain) and CP_ (modem). A
.tar.md5 ends with the MD5 of the tar in front of it. This module reads such archives without
unpacking them: the tar members are found in place, the MD5 is checked, and the firmware
version and device come from the AP's meta-data/fota.zip (SYSTEM and VENDOR build.prop).

Only the Android partitions the installer allowlists are taken from an archive. The
bootloader chain, the TEE and other "-verified" firmware, the modem and the PIT are never
written: BL_ and CP_ archives are refused, and their parts inside AP_ are skipped. This
recovery keeps its own RECOVERY. The firmware must belong to the installed bootloader:
Samsung's bootloader refuses Android from an older bootloader revision, and a newer
revision needs its BL_ (from Download mode), which this recovery does not write.

    samsung_firmware.py inspect AP_FILE [CSC_FILE]
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import zipfile

BLOCK = 512
CHUNK = 1 << 20
MAX_MEMBERS = 4096
MAX_ZIP = 1 << 30
# Archive members and the partition each one fills. Everything else stays unwritten.
PARTITIONS = {'super': 'super', 'boot': 'boot', 'dtbo': 'dtbo', 'vbmeta': 'vbmeta', 'vbmeta_system': 'vbmeta_system',
              'prism': 'prism', 'optics': 'optics'}
SPARSE = ('super', 'prism', 'optics')
# Parts of an archive that are left alone on purpose, and why.
SKIPPED = {'userdata': 'userdata is formatted for Android instead', 'cache': 'not used by this system',
           'omr': 'not used by this system', 'recovery': 'this recovery stays installed'}
VERSION = re.compile(rb'A226B[A-Z]{2}[A-Z0-9]{2}[A-Z]{2}[A-Z0-9]{1,2}')
ARCHIVE = re.compile(r'(AP|BL|CP|CSC|HOME_CSC)_[A-Za-z0-9_.-]+\.tar(?:\.md5)?')


class FirmwareError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def require(condition, code, message, **details):
    if not condition:
        raise FirmwareError(code, message, **details)


def archive_kind(name):
    """AP, BL, CP, CSC or HOME_CSC from an Odin archive name; None for anything else."""
    match = ARCHIVE.fullmatch(name)
    return match.group(1) if match else None


def _octal(field):
    text = field.split(b'\0', 1)[0].strip()
    return int(text, 8) if text else 0


def md5_trailer(fd, size):
    """(tar_bytes, md5 hex) when the file ends with Odin's "<md5>  <name>" line, else (size, None)."""
    tail = os.pread(fd, min(size, 512), max(0, size - 512))
    match = re.search(rb'([0-9a-f]{32})  [^\n/]{1,200}\n?\Z', tail)
    if not match:
        return size, None
    return size - (len(tail) - match.start()), match.group(1).decode()


def members(fd, tar_bytes):
    """Regular files of a ustar/GNU tar in place: name, data offset and size."""
    found, offset = [], 0
    long_name = None
    while offset + BLOCK <= tar_bytes:
        header = os.pread(fd, BLOCK, offset)
        require(len(header) == BLOCK, 'SHORT_ARCHIVE', 'The archive ended inside a header')
        if header == bytes(BLOCK):
            break
        checksum = _octal(header[148:156])
        require(sum(header[:148]) + 8 * 32 + sum(header[156:]) == checksum, 'ARCHIVE_DAMAGED',
                'A tar header checksum is wrong', offset=offset)
        size = _octal(header[124:136])
        kind = header[156:157]
        name = long_name or header[:100].split(b'\0', 1)[0].decode('utf-8', 'replace')
        if header[257:262] == b'ustar' and header[345:500].split(b'\0', 1)[0] and not long_name:
            name = header[345:500].split(b'\0', 1)[0].decode('utf-8', 'replace') + '/' + name
        long_name = None
        data = offset + BLOCK
        require(data + size <= tar_bytes, 'SHORT_ARCHIVE', 'A member runs past the end of the archive', member=name)
        if kind == b'L':  # GNU long name for the next member
            long_name = os.pread(fd, min(size, 4096), data).split(b'\0', 1)[0].decode('utf-8', 'replace')
        elif kind in (b'0', b'\0', b'7'):
            require(not name.startswith('/') and '..' not in name.split('/'), 'UNSAFE_ARCHIVE_PATH',
                    'The archive contains an unsafe path', member=name)
            found.append({'name': name, 'offset': data, 'size': size})
            require(len(found) <= MAX_MEMBERS, 'ARCHIVE_TOO_LARGE', 'The archive lists too many files')
        offset = data + (size + BLOCK - 1) // BLOCK * BLOCK
    return found


def verify_md5(fd, tar_bytes, expected, progress=None):
    digest, position = hashlib.md5(), 0
    while position < tar_bytes:
        data = os.pread(fd, min(8 * CHUNK, tar_bytes - position), position)
        require(data, 'SHORT_ARCHIVE', 'The archive ended before its checksum')
        digest.update(data)
        position += len(data)
        if progress:
            progress(position, tar_bytes)
    require(digest.hexdigest() == expected, 'ARCHIVE_MD5_MISMATCH', 'The archive does not match its MD5: it is damaged')


class MemberFile(io.RawIOBase):
    """One tar member as a read-only seekable file, for zipfile."""
    def __init__(self, fd, offset, size):
        super().__init__()
        self.fd, self.offset, self.size, self.position = fd, offset, size, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, position, whence=0):
        base = {0: 0, 1: self.position, 2: self.size}[whence]
        self.position = max(0, min(self.size, base + position))
        return self.position

    def readinto(self, buffer):
        count = min(len(buffer), self.size - self.position)
        if count <= 0:
            return 0
        data = os.pread(self.fd, count, self.offset + self.position)
        buffer[:len(data)] = data
        self.position += len(data)
        return len(data)


def properties(text):
    values = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def fota_info(fd, member):
    """Version, device and dynamic partitions from the AP's meta-data/fota.zip."""
    require(member['size'] <= MAX_ZIP, 'ARCHIVE_TOO_LARGE', 'meta-data/fota.zip is too large')
    info = {}
    with zipfile.ZipFile(io.BufferedReader(MemberFile(fd, member['offset'], member['size']))) as z:
        names = set(z.namelist())
        def read(name, limit=1 << 20):
            if name not in names or z.getinfo(name).file_size > limit:
                return ''
            return z.read(name).decode('utf-8', 'replace')
        system = properties(read('SYSTEM/build.prop'))
        vendor = properties(read('VENDOR/build.prop'))
        dynamic = properties(read('META/dynamic_partitions_info.txt'))
        info['version'] = system.get('ro.build.PDA') or system.get('ro.build.version.incremental')
        info['android'] = system.get('ro.build.version.release')
        info['device'] = vendor.get('ro.product.vendor.device') or vendor.get('ro.product.board')
        if dynamic.get('super_partition_size', '').isdigit():
            info['super_bytes'] = int(dynamic['super_partition_size'])
        if dynamic.get('dynamic_partition_list'):
            info['dynamic_partitions'] = dynamic['dynamic_partition_list'].split()
    return info


def partition_of(name):
    """The partition a member fills, the reason it is skipped, or None for unknown members."""
    base = name.rsplit('/', 1)[-1]
    compression = 'lz4' if base.endswith('.lz4') else 'none'
    stem = base[:-4] if compression == 'lz4' else base
    if not stem.endswith('.img'):
        return None, None, compression
    stem = stem[:-4]
    if stem.endswith('-verified') or stem in ('tzar',):
        return None, 'bootloader-chain firmware is never written', compression
    if stem in SKIPPED:
        return None, SKIPPED[stem], compression
    return PARTITIONS.get(stem), None, compression


def bootloader_version(data):
    """The firmware version Samsung's LK carries, as A226BXXSBDZC3; None when absent."""
    found = VERSION.search(data)
    return found.group(0).decode() if found else None


def compatible(firmware, bootloader):
    """Whether the installed bootloader starts this firmware: same model, same bootloader revision
    (the 9th character, B in A226BXXSBDZC3). Returns (ok, reason code)."""
    if not firmware or not re.fullmatch(r'A226B[A-Z0-9]{8,9}', firmware):
        return False, 'FIRMWARE_VERSION_UNKNOWN'
    if not bootloader:
        return False, 'BOOTLOADER_VERSION_UNKNOWN'
    if firmware[8] < bootloader[8]:
        return False, 'FIRMWARE_OLDER_THAN_BOOTLOADER'
    if firmware[8] > bootloader[8]:
        return False, 'FIRMWARE_NEEDS_NEWER_BOOTLOADER'
    return True, None


def inspect(fd, name, *, check_md5=True, progress=None):
    """What one archive holds: its kind, members mapped to partitions, skipped parts and, for AP,
    the firmware version and device."""
    kind = archive_kind(name)
    require(kind is not None, 'NOT_FIRMWARE_ARCHIVE', 'Choose a Samsung AP_ or CSC_ .tar.md5 archive')
    require(kind not in ('BL', 'CP'), 'FIRMWARE_PART_FORBIDDEN',
            'BL_ (bootloader) and CP_ (modem) archives are never written by this recovery')
    size = os.fstat(fd).st_size
    tar_bytes, md5 = md5_trailer(fd, size)
    require(md5 or name.endswith('.tar'), 'ARCHIVE_MD5_MISSING', 'A .tar.md5 archive lacks its checksum line')
    if md5 and check_md5:
        verify_md5(fd, tar_bytes, md5, progress)
    images, skipped = [], []
    meta = None
    for member in members(fd, tar_bytes):
        if member['name'] == 'meta-data/fota.zip':
            meta = member
            continue
        label, reason, compression = partition_of(member['name'])
        if label:
            images.append({'label': label, 'member': member['name'], 'offset': member['offset'], 'size': member['size'],
                           'compression': compression, 'format': 'sparse' if label in SPARSE else 'raw'})
        elif reason:
            skipped.append({'member': member['name'], 'reason': reason})
    require(len({image['label'] for image in images}) == len(images), 'ARCHIVE_DUPLICATE_PARTITION',
            'The archive holds one partition twice')
    result = {'archive': name, 'kind': kind, 'md5_checked': bool(md5 and check_md5), 'tar_bytes': tar_bytes,
              'images': images, 'skipped': skipped}
    if kind == 'AP':
        require(meta is not None, 'FIRMWARE_METADATA_MISSING', 'The AP archive lacks meta-data/fota.zip')
        result.update(fota_info(fd, meta))
        if not result.get('version'):
            match = re.match(r'AP_(A226B[A-Z0-9]{8,9})_', name)
            result['version'] = match.group(1) if match else None
        require(result.get('device') == 'a22x', 'FIRMWARE_OTHER_DEVICE',
                'This firmware is not for the Galaxy A22 5G (a22x)', device=result.get('device'))
        require({'super', 'boot', 'vbmeta'} <= {image['label'] for image in images}, 'FIRMWARE_INCOMPLETE',
                'The AP archive lacks super, boot or vbmeta')
    else:
        require(all(image['label'] in ('prism', 'optics') for image in images), 'FIRMWARE_UNEXPECTED_PARTITION',
                'A CSC archive may only fill prism and optics')
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (2, 3) or argv[0] != 'inspect':
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        result = {'ok': True, 'archives': []}
        for path in argv[1:]:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                result['archives'].append(inspect(fd, os.path.basename(path)))
            finally:
                os.close(fd)
    except FirmwareError as error:
        result = {'ok': False, 'error': {'code': error.code, 'message': str(error), 'details': error.details}}
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
