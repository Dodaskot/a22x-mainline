#!/usr/bin/env python3
"""Look inside an Android ROM or flashable ZIP without running anything from it.

TWRP installs a ZIP by running its updater binary. This recovery never runs a program
from a bundle, so it first has to know what a ZIP holds and whether that content can be
written by its own checked paths. `inspect` reads only the central directory and small
metadata members and reports one kind:

  payload        A/B OTA (payload.bin + payload_properties.txt)
  block-ota      non-A/B block OTA (NAME.new.dat[.br|.xz] + NAME.transfer.list)
  images         raw/sparse partition images (boot.img, super.img, vbmeta.img, ...)
  samsung-tar    Samsung firmware parts (AP_/BL_/CP_/CSC_ .tar.md5) inside a ZIP
  script-only    only an updater/script (Magisk, mods): cannot be installed here
  unknown

It also names the device the ZIP declares (ro.product.device / getprop asserts) so a ROM
for another phone is refused before anything is written.

    rom_zip.py inspect FILE
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import PurePosixPath

DEVICE_NAMES = ('a22x', 'a22xnsxx', 'SM-A226B', 'a22x5g')
IMAGE_NAMES = ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'super', 'system', 'vendor', 'product',
               'odm', 'system_ext', 'recovery', 'prism', 'optics', 'userdata')
# Never written from a ZIP: the bootloader chain and modem firmware.
FORBIDDEN = ('preloader', 'lk', 'lk2', 'bootloader', 'tee', 'tee1', 'tee2', 'sspm', 'scp', 'gz', 'mcupm', 'dpm',
             'spmfw', 'md1img', 'md1dsp', 'pi_img', 'efs', 'sec_efs', 'nvdata', 'nvram', 'persist')
MAX_MEMBERS = 4096
MAX_META = 1 << 20


class ZipError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def small(z, info):
    if info.file_size > MAX_META:
        return b''
    with z.open(info) as member:
        return member.read(MAX_META)


def declared_devices(z, members):
    """Devices the ZIP says it is for: OTA metadata, updater-script asserts, build.prop."""
    found = set()
    for name in ('META-INF/com/android/metadata', 'META-INF/com/google/android/updater-script', 'system/build.prop'):
        info = members.get(name)
        if not info:
            continue
        text = small(z, info).decode('utf-8', 'replace')
        found.update(re.findall(r'pre-device=([\w.,-]+)', text))
        found.update(re.findall(r'getprop\("ro\.product\.device"\) == "([\w.-]+)"', text))
        found.update(re.findall(r'^ro\.product\.(?:system\.)?device=([\w.-]+)', text, re.M))
    devices = set()
    for value in found:
        devices.update(v for v in value.split(',') if v)
    return sorted(devices)


def inspect(path):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise ZipError('NOT_A_ZIP', 'This file is not a ZIP archive') from error
    with z:
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ZipError('ZIP_TOO_LARGE', 'The archive lists too many files')
        members = {}
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if name.startswith('/') or '..' in parts or '\\' in name:
                raise ZipError('UNSAFE_ZIP_PATH', 'The archive contains an unsafe path')
            if not info.is_dir():
                members[name] = info
        names = set(members)
        top = {PurePosixPath(n).name for n in names}
        stems = {}
        for n in names:
            base = PurePosixPath(n).name
            m = re.fullmatch(r'([a-z0-9_]+)\.(img|img\.lz4|img\.gz|img\.xz|img\.zst|sparse)', base)
            if m:
                stems[m.group(1)] = n
        images = {k: v for k, v in stems.items() if k in IMAGE_NAMES}
        forbidden = sorted(k for k in stems if k in FORBIDDEN)
        block = sorted(m.group(1) for n in names
                       if (m := re.fullmatch(r'([a-z0-9_]+)\.new\.dat(?:\.br|\.xz)?', PurePosixPath(n).name))
                       and any(PurePosixPath(x).name == m.group(1) + '.transfer.list' for x in names))
        samsung = sorted(n for n in top if re.fullmatch(r'(AP|BL|CP|CSC|HOME_CSC)_.+\.tar(\.md5)?', n))
        script = any(n in names for n in ('META-INF/com/google/android/update-binary', 'META-INF/com/google/android/updater-script'))
        if 'payload.bin' in top and 'payload_properties.txt' in top:
            kind = 'payload'
        elif block:
            kind = 'block-ota'
        elif samsung:
            kind = 'samsung-tar'
        elif images:
            kind = 'images'
        elif script:
            kind = 'script-only'
        else:
            kind = 'unknown'
        devices = declared_devices(z, members)
        matches = not devices or any(d.lower() in (n.lower() for n in DEVICE_NAMES) for d in devices)
        notes = []
        if kind == 'script-only':
            notes.append('Only a script inside: recovery does not run programs from a ZIP')
        if forbidden:
            notes.append('Bootloader, modem or identity images are inside; they are never written')
        if not matches:
            notes.append('The ZIP is for another phone')
        return {'kind': kind, 'devices': devices, 'for_this_phone': matches,
                'images': sorted(images), 'block_partitions': block, 'samsung_parts': samsung,
                'forbidden_images': forbidden, 'has_updater_script': script,
                'members': len(names), 'bytes': sum(i.file_size for i in members.values()), 'notes': notes}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != 'inspect':
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        result = {'ok': True, 'operation': 'inspect', **inspect(argv[1])}
    except ZipError as error:
        result = {'ok': False, 'operation': 'inspect', 'error': {'code': error.code, 'message': str(error)}}
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
