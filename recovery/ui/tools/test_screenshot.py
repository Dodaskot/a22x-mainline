#!/usr/bin/env python3
"""Decode the pictures the UI writes and compare every pixel: the exact PNG of stored blocks with the
pattern it was given, the filtered rows packed by screenshot.py with the same pattern, and a whole
screenshot taken through that helper (onto a fixture's cache) with the page written the exact way."""
import json, os, subprocess, sys, tempfile, zlib, struct
from pathlib import Path
UI = Path(__file__).resolve().parents[1]
SOURCE = UI.parent
sys.path.insert(0, str(SOURCE))
import screenshot


def rows(data):
    """(width, height, the unfiltered rows) of a PNG of 8-bit RGB with filters none, Sub and Up."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    at, chunks = 8, {}
    while at < len(data):
        n, kind = struct.unpack('>I4s', data[at:at + 8])
        body = data[at + 8:at + 8 + n]
        assert struct.unpack('>I', data[at + 8 + n:at + 12 + n])[0] == zlib.crc32(kind + body), kind
        chunks.setdefault(kind, b''); chunks[kind] += body
        at += 12 + n
    w, h = struct.unpack('>II', chunks[b'IHDR'][:8])
    raw = zlib.decompress(chunks[b'IDAT'])
    assert len(raw) == h * (w * 3 + 1)
    out, above = [], bytes(w * 3)
    for y in range(h):
        kind, line = raw[y * (w * 3 + 1)], raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)]
        if kind == 2:
            line = bytes((a + b) & 255 for a, b in zip(line, above))
        elif kind == 1:
            now = bytearray(line)
            for i in range(3, len(now)):
                now[i] = (now[i] + now[i - 3]) & 255
            line = bytes(now)
        else:
            assert kind == 0, kind
        out.append(line)
        above = line
    return w, h, out


with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
    tmp = Path(tmp)
    fixture = tmp / 'fixture'
    (fixture / 'cache').mkdir(parents=True)
    (fixture / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
        {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}))
    out, raw, page = tmp / 'shot.png', tmp / 'rows.raw', tmp / 'page.png'
    run = subprocess.run([str(UI / 'screenshot-test'), str(out), str(raw), str(fixture), str(SOURCE / 'screenshot.py'), sys.executable, str(page)],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr[-2000:]
    said = json.loads(run.stdout.splitlines()[-1])
    assert said['toast'] == 'Screenshot saved on cache, in a22x-recovery/screenshots', said
    pattern = lambda x, y: ((x * 7) & 255, (y * 5) & 255, (x ^ y) & 255)
    w, h, exact = rows(out.read_bytes())
    assert (w, h) == (301, 257)
    assert all(tuple(exact[y][x * 3:x * 3 + 3]) == pattern(x, y) for y in range(h) for x in range(w))
    # The rows the interface hands to the helper, packed by the helper: the same picture, far smaller.
    packed = screenshot.png(raw.read_bytes(), w, h)
    assert rows(packed)[2] == exact
    assert {line[0] for line in [raw.read_bytes()[y * (w * 3 + 1):y * (w * 3 + 1) + 1] for y in range(h)]} <= {1, 2}
    # A whole screenshot through the helper: on the fixture's cache, which is read-only again, and
    # pixel for pixel the page as the exact writer saved it.
    shots = list((fixture / 'cache/a22x-recovery/screenshots').glob('recovery-*.png'))
    assert len(shots) == 1, shots
    state = json.loads((fixture / '.filesystem-state.json').read_text())['cache']
    assert state['mounted'] and state['read_only'], state
    assert not list((fixture / 'work').glob('shot-*')), 'the rows or the result were left behind'
    saved, whole = shots[0].read_bytes(), page.read_bytes()
    assert rows(saved) == rows(whole)
    assert len(saved) * 8 < len(whole), (len(saved), len(whole))
    print('{"ok": true, "png": [%d, %d], "bytes": %d, "page_bytes": %d, "page_exact_bytes": %d}' % (w, h, len(out.read_bytes()), len(saved), len(whole)))
