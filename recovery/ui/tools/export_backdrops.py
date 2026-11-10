#!/usr/bin/env python3
"""The recovery's own theme backdrops as drawables of the Android app (2026-11-10), so the app's
background flows exactly as the recovery's does. theme_assets.h holds each backdrop as a small
RGB image (BACKDROP_W x BACKDROP_H, made by tools/theme_assets.c); this writes them unchanged as
PNG into drawable-nodpi. The app stretches them to the screen as canvas_backdrop() does: bilinear,
with the same 8x8 ordered dither (Backdrop.java).

    tools/export_backdrops.py APK_RES_DIR
"""
import re
from pathlib import Path
import sys

from PIL import Image

UI = Path(__file__).resolve().parents[1]


def main():
    out = Path(sys.argv[1]) / 'drawable-nodpi'
    out.mkdir(parents=True, exist_ok=True)
    source = (UI / 'theme_assets.h').read_text()
    width = int(re.search(r'#define BACKDROP_W (\d+)', source).group(1))
    height = int(re.search(r'#define BACKDROP_H (\d+)', source).group(1))
    for name in ('daylight', 'aurora'):
        start = source.index('backdrop_%s[BACKDROP_W*BACKDROP_H*3]={' % name)
        body = source[source.index('{', start) + 1:source.index('};', start)]
        data = bytes(int(v, 0) for v in re.findall(r'0x[0-9a-fA-F]+|\d+', body))
        if len(data) != width * height * 3:
            raise SystemExit('%s: %d bytes, expected %d' % (name, len(data), width * height * 3))
        Image.frombytes('RGB', (width, height), data).save(out / ('backdrop_%s.png' % name), optimize=True)
    print('2 backdrops (%dx%d) in %s' % (width, height, out))


if __name__ == '__main__':
    main()
