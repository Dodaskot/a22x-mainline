#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Put a theme description and a picture into one .a22xtheme file. Runs on a computer, needs Pillow.

    pack_theme.py theme.json --image picture.png --output my.a22xtheme
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import zlib

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import themes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    theme = json.loads(args.source.read_text())
    if args.image:
        size = (themes.IMAGE_WIDTH, themes.IMAGE_HEIGHT)
        picture = Image.open(args.image).convert('RGB')
        scale = max(size[0] / picture.width, size[1] / picture.height)
        picture = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
        left, top = (picture.width - size[0]) // 2, (picture.height - size[1]) // 2
        picture = picture.crop((left, top, left + size[0], top + size[1]))
        theme['image'] = {'width': size[0], 'height': size[1], 'data': base64.b64encode(zlib.compress(picture.tobytes(), 9)).decode()}
    data = json.dumps(theme, ensure_ascii=False, separators=(',', ':')).encode()
    code, name, _, compiled = themes.compile_theme(data, args.output.stem)
    args.output.write_bytes(data)
    print(f'{args.output}: {code} ({name}), file {len(data)} bytes, compiled {len(compiled)} bytes')


if __name__ == '__main__':
    main()
