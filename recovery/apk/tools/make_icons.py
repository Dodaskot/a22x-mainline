#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Launcher icons and splash symbols in the recovery's three themes (2026-11-10): Daylight and
Aurora use their hero gradients with a white layers symbol, Graphite its dark panels with the
orange accent. Drawn at four times the size and scaled down. Writes res/mipmap-*/ic_launcher_<theme>.png
and res/drawable-xxhdpi/splash_<theme>.png (the Android 12+ splash icon, symbol only)."""
from pathlib import Path
import sys
from PIL import Image, ImageDraw

SIZES = {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}
THEMES = {
    # name: (gradient top-left, bottom-right, symbol colour), from ui/theme.h of the recovery
    'daylight': ((0x6f, 0x4e, 0xf5), (0x28, 0x3d, 0xde), (255, 255, 255)),
    'aurora': ((0x00, 0x87, 0x81), (0x37, 0x3b, 0xa7), (255, 255, 255)),
    'graphite': ((0x2b, 0x2f, 0x37), (0x0e, 0x10, 0x13), (0xff, 0x9a, 0x4b)),
}

# The splash symbol uses each theme's accent on its background.
SPLASH = {'daylight': (0x6f, 0x4e, 0xf5), 'aurora': (0x4a, 0xe6, 0xd6), 'graphite': (0xff, 0x9a, 0x4b)}


def icon(size, theme):
    TOP, BOTTOM, SYMBOL = THEMES[theme]
    big = size * 4
    gradient = Image.new('RGB', (big, big))
    pixels = gradient.load()
    for y in range(big):
        for x in range(big):
            t = (x + y) / (2 * (big - 1))
            pixels[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    mask = Image.new('L', (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 0.23), fill=255)
    out = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    out.paste(gradient, (0, 0), mask)
    symbol(out, big, SYMBOL, 1.0)
    return out.resize((size, size), Image.LANCZOS)


def symbol(out, big, colour, scale):
    draw = ImageDraw.Draw(out)
    big = big * scale
    offset = out.size[0] / 2 - big / 2
    c, w, h, line = offset + big / 2, big * 0.27, big * 0.13, round(big * 0.055)
    top = c - big * 0.12
    # The closed path starts and ends in the middle of a straight edge, so no joint shows a notch.
    start = (c - w / 2, top - h / 2)
    diamond = [start, (c, top - h), (c + w, top), (c, top + h), (c - w, top), start]
    draw.line(diamond, fill=colour, width=line, joint='curve')
    for shift in (big * 0.13, big * 0.26):
        draw.line([(c - w, top + shift), (c, top + h + shift), (c + w, top + shift)], fill=colour, width=line, joint='curve')
    for x, y in [(c - w, top + s) for s in (big * 0.13, big * 0.26)] + [(c + w, top + s) for s in (big * 0.13, big * 0.26)]:
        r = line / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def splash(theme):
    # 288 dp at xxhdpi; Android shows what lies inside the middle circle of 192 dp.
    size = 864
    out = Image.new('RGBA', (size * 2, size * 2), (0, 0, 0, 0))
    symbol(out, size * 2, SPLASH[theme], 0.62)
    return out.resize((size, size), Image.LANCZOS)


def main(root):
    for theme in THEMES:
        for density, size in SIZES.items():
            folder = Path(root) / ('res/mipmap-' + density)
            folder.mkdir(parents=True, exist_ok=True)
            icon(size, theme).save(folder / ('ic_launcher_%s.png' % theme), optimize=True)
        folder = Path(root) / 'res/drawable-xxhdpi'
        folder.mkdir(parents=True, exist_ok=True)
        splash(theme).save(folder / ('splash_%s.png' % theme), optimize=True)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
