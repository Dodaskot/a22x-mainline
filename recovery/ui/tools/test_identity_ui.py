#!/usr/bin/env python3
"""The Phone identity page against fixture partitions and volumes; no block device is opened."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
UI = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='a22x-identity-ui-') as tmp:
    root = Path(tmp)
    for name in ('parts', 'sd', 'userdata', 'privateplans'):
        (root / name).mkdir()
    for label, size in (('efs', 1 << 16), ('nvdata', 1 << 17), ('protect1', 1 << 15)):
        (root / 'parts' / label).write_bytes(os.urandom(size))
    (root / 'install-fixture.json').write_text(json.dumps({'schema': 'a22x.install.fixture.v1',
        'device': {'model': 'SM-A226B', 'identity': 'fixture:ui'},
        'identity_parts': {l: {'path': 'parts/' + l} for l in ('efs', 'nvdata', 'protect1')},
        'android_parts': {l: {'path': 'parts/' + l} for l in ('boot', 'vbmeta', 'super')}}))
    for label, data in (('boot', os.urandom(1 << 16)), ('vbmeta', os.urandom(1 << 12)), ('super', bytes(1 << 20) + os.urandom(4096))):
        (root / 'parts' / label).write_bytes(data)
    (root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
        {'id': 'userdata', 'path': 'userdata', 'mounted': True, 'read_only': False},
        {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': False}]}))
    run = subprocess.run([str(UI / 'identity-ui-test'), str(root), str(UI.parent), sys.executable], capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise SystemExit(run.stdout + run.stderr[-3000:])
    print(run.stdout.strip())
