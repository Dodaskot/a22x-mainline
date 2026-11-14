#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copy ARM64 tools and their glibc libraries out of an ext4 root image.

usage: collect_runtime.py USERDATA.img OUTPUT_DIR tool...
Nothing is executed. Reads with debugfs, finds DT_NEEDED with readelf.
"""
import re, subprocess, sys
from pathlib import Path

image, out, tools = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
(out / 'bin').mkdir(parents=True)
(out / 'lib').mkdir()


def debugfs(command):
    return subprocess.run(['debugfs', '-R', command, image], capture_output=True, text=True).stdout


def real(path):
    """Resolve a symlink chain inside the image."""
    for _ in range(8):
        text = debugfs(f'stat {path}')
        match = re.search(r'Fast link dest: (.+)', text)
        if match:
            target = match[1].strip()
        elif re.search(r'Type: symlink', text):
            # A target of 60 bytes or more lives in a data block (2026-11-14 review: such a link was
            # dumped as its own text instead of what it names).
            target = debugfs(f'cat {path}').strip()
        else:
            return path
        path = target if target.startswith('/') else str(Path(path).parent / target)
    raise SystemExit('link loop ' + path)


def dump(path, target):
    subprocess.run(['debugfs', '-R', f'dump {real(path)} {target}', image], capture_output=True, check=True)
    target.chmod(0o755)
    if target.stat().st_size == 0:
        raise SystemExit('missing ' + path)


def needed(path):
    text = subprocess.run(['aarch64-linux-gnu-readelf', '-d', str(path)], capture_output=True, text=True).stdout
    return re.findall(r'\(NEEDED\)\s+Shared library: \[(.+?)\]', text)


queue = []
for tool in tools:
    target = out / 'bin' / tool
    dump('/usr/bin/' + tool, target)
    queue.append(target)
dump('/usr/lib/ld-linux-aarch64.so.1', out / 'lib/ld-linux-aarch64.so.1')
seen = set()
while queue:
    for name in needed(queue.pop()):
        if name in seen:
            continue
        seen.add(name)
        target = out / 'lib' / name
        dump('/usr/lib/' + name, target)
        queue.append(target)
print(len(seen), 'libraries')
