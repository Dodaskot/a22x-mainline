#!/usr/bin/env python3
"""Fail when a theme's text would be hard to read on its own surfaces (WCAG contrast)."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oklch import contrast

text = (Path(__file__).resolve().parents[1] / 'theme.h').read_text()
failed = False
for block in re.findall(r'\{\.code="([a-z]+)"(.*?)\.bento=', text, re.S):
    name, body = block
    token = lambda key: re.search(r'\.%s=0x([0-9a-f]{6})u' % key, body).group(1)
    pairs = (('text', 'panel', 7), ('text', 'bg', 7), ('muted', 'panel', 4.5), ('muted', 'bg', 4.5), ('accent', 'panel', 3),
             ('on_accent', 'accent', 4.5), ('text', 'panel2', 7), ('text', 'container', 7), ('hero_text', 'hero_top', 3), ('hero_text', 'hero_bottom', 3),
             ('toast_text', 'toast_fill', 7), ('green', 'panel', 3), ('red', 'panel', 3), ('blue', 'panel', 3), ('purple', 'panel', 3))
    for one, two, least in pairs:
        ratio = contrast(token(one), token(two))
        if ratio < least:
            print(f'{name}: {one} on {two} is {ratio:.1f}, needs {least}')
            failed = True
    fills = re.search(r'\.tile_fill=\{([^}]*)\}', body).group(1)
    inks = re.search(r'\.tile_ink=\{([^}]*)\}', body).group(1)
    for fill, ink in zip(re.findall(r'0x([0-9a-f]{6})', fills), re.findall(r'0x([0-9a-f]{6})', inks)):
        if contrast(ink, fill) < 3:
            print(f'{name}: tile {ink} on {fill} is {contrast(ink, fill):.1f}, needs 3')
            failed = True
print('themes checked')
sys.exit(1 if failed else 0)
