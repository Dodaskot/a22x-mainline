#!/usr/bin/env python3
"""The recovery's own icons as drawables of the Android app (2026-11-10), so that the app shows
exactly what the recovery draws. The interface renders its icon sheet (white icons on black, the
preview page "icon-sheet"); each 96-point cell becomes a white PNG whose transparency is the
drawn brightness, 144 px (48 dp at xxhdpi). The app tints them with each theme's colours.

    tools/export_icons.py APK_RES_DIR
"""
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image

UI = Path(__file__).resolve().parents[1]


def main():
    out = Path(sys.argv[1]) / 'drawable-xxhdpi'
    out.mkdir(parents=True, exist_ok=True)
    source = (UI / 'recovery_ui.c').read_text()
    block = source[source.index('sheet_icons[]={'):source.index('};', source.index('sheet_icons[]={'))]
    names = re.findall(r'\{[A-Z_]+,"([a-z_]+)"\}', block)
    with tempfile.TemporaryDirectory() as temporary:
        ppm = Path(temporary) / 'sheet.ppm'
        subprocess.run([str(UI / 'a22x-recovery-ui'), '--theme', 'graphite', '--preview', 'icon-sheet', '--render-native', str(ppm)],
                       check=True, capture_output=True)
        sheet = Image.open(ppm).convert('L')
    scale = sheet.size[0] / 720
    for n, name in enumerate(names):
        x, y = 8 + (n % 6) * 118, 8 + (n // 6) * 118
        box = tuple(round(v * scale) for v in (x, y, x + 96, y + 96))
        alpha = sheet.crop(box).resize((144, 144), Image.LANCZOS)
        icon = Image.new('RGBA', alpha.size, (255, 255, 255, 0))
        icon.putalpha(alpha)
        icon.save(out / ('ic_%s.png' % name), optimize=True)
    print(len(names), 'icons in', out)


if __name__ == '__main__':
    main()
