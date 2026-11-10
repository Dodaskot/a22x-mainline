#!/usr/bin/env python3
"""Decode the PNG the UI writes and compare every pixel with the pattern it was given."""
import subprocess, sys, tempfile, zlib, struct
from pathlib import Path
UI = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / 'shot.png'
    run = subprocess.run([str(UI / 'screenshot-test'), str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    data = out.read_bytes()
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
    assert (w, h) == (301, 257) and len(raw) == h * (w * 3 + 1)
    for y in range(h):
        row = raw[y * (w * 3 + 1):(y + 1) * (w * 3 + 1)]
        assert row[0] == 0
        for x in range(w):
            assert tuple(row[1 + x * 3:4 + x * 3]) == ((x * 7) & 255, (y * 5) & 255, (x ^ y) & 255), (x, y)
    print('{"ok": true, "png": [%d, %d], "bytes": %d}' % (w, h, len(data)))
