#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Repack a qualified recovery image: same kernel, DTB, header and cmdline,
with listed ramdisk files replaced. Entry order, modes and owners are kept."""
import argparse, gzip, hashlib, importlib.util, json, re, subprocess, sys
from importlib._bootstrap_external import _code_to_hash_pyc
from pathlib import Path


def read_cpio(raw):
    out, at = [], 0
    while True:
        assert raw[at:at + 6] == b'070701', at
        fields = [int(raw[at + 6 + 8 * n:at + 14 + 8 * n], 16) for n in range(13)]
        namesize, filesize = fields[11], fields[6]
        start = at + 110
        name = raw[start:start + namesize - 1].decode()
        data_at = (start + namesize + 3) & ~3
        data = raw[data_at:data_at + filesize]
        at = (data_at + filesize + 3) & ~3
        if name == 'TRAILER!!!':
            return out
        out.append([fields, name, data])


def write_cpio(entries):
    blob = bytearray()

    def add(fields, name, data):
        encoded = name.encode() + b'\0'
        fields = list(fields)
        fields[6], fields[11] = len(data), len(encoded)
        blob.extend(b'070701' + b''.join(b'%08X' % f for f in fields) + encoded)
        blob.extend(b'\0' * (-len(blob) % 4))
        blob.extend(data)
        blob.extend(b'\0' * (-len(blob) % 4))
    for fields, name, data in entries:
        add(fields, name, data)
    # newc fields: ino, mode, uid, gid, nlink, mtime, ...; the trailer has nlink 1 as cpio(1) writes it
    # (2026-11-14 review: the 1 had landed on mtime).
    add([0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], 'TRAILER!!!', b'')
    blob.extend(b'\0' * (-len(blob) % 512))
    return bytes(blob)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True, help='Qualified build directory')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--replace', action='append', default=[], help='ramdisk/path=host/file')
    ap.add_argument('--add', action='append', default=[], help='new ramdisk/path=host/file')
    ap.add_argument('--link', action='append', default=[], help='new ramdisk/path=target')
    ap.add_argument('--remove', action='append', default=[], help='ramdisk/path to leave out of the image')
    ap.add_argument('--scrub', action='append', default=[], help='TEXT=REPLACEMENT in the build records etc/a22x-recovery/*.json')
    ap.add_argument('--bytecode', type=Path, help='list of standard library sources to compile into the image')
    ap.add_argument('--kernel', type=Path, help='kernel payload to use instead of the one in the base image')
    ap.add_argument('--dtb', type=Path, help='DT table to use instead of the one in the base image')
    ap.add_argument('--version', required=True)
    ap.add_argument('--image-name', required=True)
    ap.add_argument('--build-update', type=Path, help='JSON merged into etc/a22x-recovery/build.json')
    a = ap.parse_args()
    a.output.mkdir(parents=True)
    entries = read_cpio(gzip.decompress((a.base / 'ramdisk.cpio.gz').read_bytes()))
    byname = {e[1]: e for e in entries}
    report = {}
    for item in a.replace:
        name, host = item.split('=', 1)
        data = Path(host).read_bytes()
        report[name] = {'before': hashlib.sha256(byname[name][2]).hexdigest(), 'after': hashlib.sha256(data).hexdigest()}
        byname[name][2] = data
    for name in a.remove:
        # 2026-11-13: what the base image holds of one phone (its qualification capsule) stays out.
        if name in byname:
            report[name] = {'before': hashlib.sha256(byname[name][2]).hexdigest(), 'after': None}
            entries.remove(byname.pop(name))
    template = byname['opt/a22x-recovery/distro.py'][0]
    dirmode = byname['opt/a22x-recovery'][0]
    inode = max(e[0][0] for e in entries) + 1

    def add(name, mode, data):
        nonlocal inode
        fields = list(template)
        fields[0], fields[1], fields[4] = inode, mode, 1
        inode += 1
        entry = [fields, name, data]
        entries.append(entry)
        byname[name] = entry

    def ensure_dir(name):
        if name and name not in byname:
            ensure_dir(name.rpartition('/')[0])
            add(name, dirmode[1], b'')
    for item in a.add:
        name, host = item.split('=', 1)
        if name in byname:
            # 2026-11-14 review: a second entry of the same name stayed beside the base's one.
            sys.exit('--add %s: the base image has it already; use --replace' % name)
        ensure_dir(name.rpartition('/')[0])
        path = Path(host)
        add(name, 0o100755 if path.stat().st_mode & 0o111 else 0o100644, path.read_bytes())
        report[name] = {'before': None, 'after': hashlib.sha256(path.read_bytes()).hexdigest()}
    for item in a.link:
        name, target = item.split('=', 1)
        if name in byname:
            sys.exit('--link %s: the base image has it already' % name)
        ensure_dir(name.rpartition('/')[0])
        add(name, 0o120777, target.encode())
    if a.bytecode:
        # Every helper call would otherwise compile about 30 modules again: 0.5 s on a big core,
        # 1.2 s on a little one. A checked hash means a source that differs is simply recompiled.
        if sys.implementation.cache_tag != 'cpython-314':
            sys.exit('bytecode must come from CPython 3.14, the version in the image')
        names = [line.strip() for line in a.bytecode.read_text().splitlines() if line.strip() and not line.startswith('#')]
        names += sorted(name for name in byname if re.fullmatch(r'opt/a22x-recovery/(backend/)?[^/]+\.py', name))
        for name in names:
            source = byname[name][2]
            code = compile(source, '/' + name, 'exec', dont_inherit=True)
            parent, _, base = name.rpartition('/')
            ensure_dir(parent + '/__pycache__')
            pyc = f'{parent}/__pycache__/{base[:-3]}.cpython-314.pyc'
            data = bytes(_code_to_hash_pyc(code, importlib.util.source_hash(source), True))
            if pyc in byname:
                byname[pyc][2] = data  # the base's own bytecode is replaced, never doubled
            else:
                add(pyc, 0o100644, data)
        report['bytecode'] = {'modules': len(names), 'magic': importlib.util.MAGIC_NUMBER.hex()}
    build = json.loads(byname['etc/a22x-recovery/build.json'][2])
    build['version'] = a.version
    if a.build_update:
        build.update(json.loads(a.build_update.read_text()))
    byname['etc/a22x-recovery/build.json'][2] = json.dumps(build, indent=2, sort_keys=True).encode() + b'\n'
    # 2026-11-13: the build records name the folders of the computer they were made on (a user's
    # home); a public image gets them replaced. Nothing checks these records against a hash.
    # 2026-11-14 review: a folder such as "/" (WORK at the root) replaced every slash, and the sealed
    # platform profile was changed with the rest. Only a real folder of two levels or more is
    # replaced, never in a sealed record, and every record must still be JSON afterwards.
    for item in a.scrub:
        text, _, replacement = item.partition('=')
        parts = [part for part in text.split('/') if part]
        if not text.startswith('/') or len(parts) < 2 or '..' in parts:
            sys.exit('--scrub %r: only an absolute folder of two levels or more is replaced' % text)
        for name, entry in byname.items():
            if not (name.startswith('etc/a22x-recovery/') and name.endswith('.json')) or text.encode() not in entry[2]:
                continue
            if 'distro-platform' in name or b'"seal"' in entry[2]:
                sys.exit('--scrub: %s is sealed and names %s; it cannot be changed here' % (name, text))
            entry[2] = entry[2].replace(text.encode(), replacement.encode())
            json.loads(entry[2])
            report.setdefault('scrubbed', []).append(name)
    ramdisk = a.output / 'ramdisk.cpio.gz'
    with open(ramdisk, 'wb') as f:
        f.write(gzip.compress(write_cpio(entries), 9, mtime=0))
    argv = json.loads((a.base / 'logs/mkbootimg.command.json').read_text())['argv']
    image = a.output / a.image_name
    argv[argv.index('--ramdisk') + 1] = str(ramdisk)
    if a.kernel:
        argv[argv.index('--kernel') + 1] = str(a.kernel.resolve())
    if a.dtb:
        argv[argv.index('--dtb') + 1] = str(a.dtb.resolve())
    argv[argv.index('--output') + 1 if '--output' in argv else argv.index('-o') + 1] = str(image)
    subprocess.run(argv, check=True)
    avb = json.loads((a.base / 'logs/avb-add.command.json').read_text())['argv']
    avb[avb.index('--image') + 1] = str(image)
    subprocess.run(avb, check=True)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (a.output / 'SHA256SUMS').write_text(f'{digest}  {a.image_name}\n')
    (a.output / 'delta.json').write_text(json.dumps({'version': a.version, 'sha256': digest, 'bytes': image.stat().st_size, 'files': report}, indent=1))
    print(digest, image.stat().st_size)


if __name__ == '__main__':
    sys.exit(main())
