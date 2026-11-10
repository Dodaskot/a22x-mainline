#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Themes from a file.

A .a22xtheme file is JSON with colors, terminal colors, a picture, or all of them;
THEMES_API.md describes it. A file from a stick is not trusted: it is checked here
and compiled into the plain layout theme_files.h reads, so the interface parses
nothing a stranger wrote. A theme nobody can read would stay after every reboot,
so the check refuses one, instead of leaving the owner in front of a blank screen.
"""
import argparse
import base64
import binascii
import contextlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import filesystem as fs
from operation_lock import operation_lock
import settings

MAGIC = b'A22XTHM1'
SOURCE_LIMIT = 8 * 1024 * 1024
IMAGE_WIDTH, IMAGE_HEIGHT = 540, 1204
THUMB_WIDTH, THUMB_HEIGHT = 117, 261
INSTALLED_LIMIT = 8
BUNDLED = Path(os.environ.get('A22X_THEMES_BUNDLED', '/opt/a22x-recovery/themes'))
FLAG_UI, FLAG_TERMINAL, FLAG_BENTO, FLAG_LIGHT = 1, 2, 4, 8
COLORS = ('bg', 'panel', 'panel2', 'line', 'edge', 'text', 'muted', 'accent', 'container', 'container_edge', 'on_accent',
          'icon_fill', 'knob', 'green', 'red', 'blue', 'purple', 'danger', 'toast_fill', 'toast_text', 'shadow',
          'hero_top', 'hero_bottom', 'hero_text', 'hero_sub')
REQUIRED = ('bg', 'panel', 'text', 'muted', 'accent', 'on_accent')
# The names the interface already uses for its own themes and terminal colors.
RESERVED = {'graphite', 'daylight', 'aurora', 'auto', 'catppuccin-mocha', 'catppuccin-latte', 'dracula', 'nord', 'tokyo-night',
            'gruvbox-dark', 'gruvbox-light', 'solarized-dark', 'solarized-light'}
# What cannot be read is refused. Tighter limits for the built-in themes are in ui/tools/check_themes.py.
READABLE = (('text', 'bg', 4.5), ('text', 'panel', 4.5), ('text', 'panel2', 4.5), ('text', 'container', 4.5), ('muted', 'panel', 3),
            ('muted', 'bg', 3), ('on_accent', 'accent', 3), ('accent', 'panel', 2), ('hero_text', 'hero_top', 3), ('hero_text', 'hero_bottom', 3),
            ('toast_text', 'toast_fill', 4.5))
HEADER = struct.Struct('<8s' + 'I' + 'I' * (len(COLORS) + 12 + 2 + 16) + 'III' + 'II' + 'IIII' + '24s48s80s')


class ThemeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise ThemeError(code, message)


def color(value, name):
    require(isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value), 'THEME_BAD_COLOR', f'{name} must look like #rrggbb')
    return int(value[1:], 16)


def mix(one, two, amount):
    return sum(round(((one >> shift) & 255) * (1 - amount) + ((two >> shift) & 255) * amount) << shift for shift in (16, 8, 0))


def luminance(value):
    channels = []
    for shift in (16, 8, 0):
        channel = ((value >> shift) & 255) / 255
        channels.append(channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4)
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]


def contrast(one, two):
    light, dark = sorted((luminance(one), luminance(two)), reverse=True)
    return (light + .05) / (dark + .05)


def text_field(value, limit, name):
    require(isinstance(value, str) and value.strip() and value.isprintable(), 'THEME_BAD_TEXT', f'{name} must be plain text')
    encoded = value.strip().encode()
    require(len(encoded) < limit, 'THEME_BAD_TEXT', f'{name} is too long')
    return encoded


def interface_colors(source, dark):
    values = source.get('colors')
    require(isinstance(values, dict), 'THEME_BAD_FORMAT', 'colors must be an object')
    known = set(COLORS) | {'tile_fill', 'tile_ink', 'terminal_bg', 'terminal_fg'}
    unknown = sorted(set(values) - known)
    require(not unknown, 'THEME_BAD_FORMAT', f'Unknown color: {unknown[0]}' if unknown else '')
    for name in REQUIRED:
        require(name in values, 'THEME_BAD_FORMAT', f'The color {name} is required')
    c = {name: color(value, name) for name, value in values.items() if name not in ('tile_fill', 'tile_ink')}
    bg, panel, text, accent = c['bg'], c['panel'], c['text'], c['accent']
    defaults = {
        'panel2': mix(panel, text, .1), 'line': mix(panel, text, .16), 'edge': mix(panel, text, .1), 'container': mix(bg, accent, .22),
        'container_edge': mix(bg, accent, .36), 'icon_fill': mix(panel, accent, .18), 'knob': text,
        'green': 0x79d7ae if dark else 0x007948, 'red': 0xff8b8b if dark else 0xc51d28,
        'blue': 0x99c8ff if dark else 0x0060c1, 'purple': 0xc9a3ff if dark else 0x8632b6,
        'toast_fill': text, 'toast_text': bg, 'shadow': 0 if dark else mix(bg, 0, .7), 'hero_top': mix(bg, accent, .55),
        'hero_bottom': mix(bg, accent, .3), 'hero_text': 0xffffff if dark else 0x000000,
    }
    for name, value in defaults.items():
        c.setdefault(name, value)
    c.setdefault('danger', mix(bg, c['red'], .2))
    c.setdefault('hero_sub', mix(c['hero_text'], c['hero_bottom'], .2))
    c.setdefault('terminal_bg', mix(bg, 0, .3) if dark else bg)
    c.setdefault('terminal_fg', text)
    tiles = []
    for name, fallback in (('tile_fill', c['container']), ('tile_ink', accent)):
        items = values.get(name, [])
        require(isinstance(items, list) and len(items) in (0, 6), 'THEME_BAD_FORMAT', f'{name} needs six colors')
        tiles.append([color(item, name) for item in items] or [fallback] * 6)
    for one, two, least in READABLE:
        ratio = contrast(c[one], c[two])
        require(ratio >= least, 'THEME_UNREADABLE', f'{one} on {two} would be too hard to read ({ratio:.1f}, needs {least})')
    for ink, fill in zip(tiles[1], tiles[0]):
        require(contrast(ink, fill) >= 2, 'THEME_UNREADABLE', 'A tile icon would be too hard to read')
    return c, tiles


def terminal_colors(source):
    value = source.get('terminal')
    require(isinstance(value, dict), 'THEME_BAD_FORMAT', 'terminal must be an object')
    palette = value.get('palette')
    require(isinstance(palette, list) and len(palette) == 16, 'THEME_BAD_FORMAT', 'The terminal palette needs 16 colors')
    background, foreground = color(value.get('background'), 'background'), color(value.get('foreground'), 'foreground')
    require(contrast(background, foreground) >= 4.5, 'THEME_UNREADABLE', 'The terminal text would be too hard to read')
    return background, foreground, [color(item, 'palette') for item in palette]


def picture(value):
    require(isinstance(value, dict), 'THEME_BAD_FORMAT', 'image must be an object')
    width, height, data = value.get('width'), value.get('height'), value.get('data')
    require(type(width) is int and type(height) is int and 1 <= width <= IMAGE_WIDTH and 1 <= height <= IMAGE_HEIGHT, 'THEME_BAD_IMAGE',
            f'The picture may be at most {IMAGE_WIDTH} x {IMAGE_HEIGHT}')
    require(isinstance(data, str), 'THEME_BAD_IMAGE', 'The picture data must be text')
    try:
        packed = base64.b64decode(data, validate=True)
        reader = zlib.decompressobj()
        pixels = reader.decompress(packed, width * height * 3 + 1)
    except (binascii.Error, ValueError, zlib.error):
        raise ThemeError('THEME_BAD_IMAGE', 'The picture cannot be read') from None
    require(len(pixels) == width * height * 3 and reader.eof, 'THEME_BAD_IMAGE', 'The picture has the wrong size')
    return width, height, pixels


def thumbnail(width, height, pixels):
    """A small copy for the theme list. Four samples per point keep thin lines from vanishing."""
    out = bytearray()
    for y in range(THUMB_HEIGHT):
        rows = [min(height - 1, (2 * y + k) * height // (2 * THUMB_HEIGHT)) for k in (0, 1)]
        for x in range(THUMB_WIDTH):
            columns = [min(width - 1, (2 * x + k) * width // (2 * THUMB_WIDTH)) for k in (0, 1)]
            spots = [(row * width + column) * 3 for row in rows for column in columns]
            out += bytes(sum(pixels[spot + channel] for spot in spots) // 4 for channel in range(3))
    return bytes(out)


def pixel(pixels, width, x, y):
    spot = (y * width + x) * 3
    return pixels[spot] << 16 | pixels[spot + 1] << 8 | pixels[spot + 2]


def compile_theme(data, fallback_code=''):
    """Return (code, name, flags, compiled bytes) for the contents of a theme file."""
    require(len(data) <= SOURCE_LIMIT, 'THEME_TOO_LARGE', 'The theme file is too large')
    try:
        source = json.loads(data)
    except (ValueError, RecursionError):
        raise ThemeError('THEME_NOT_JSON', 'This is not a theme file') from None
    require(isinstance(source, dict) and source.get('format') == 1, 'THEME_NOT_JSON', 'This is not a theme file')
    code = source.get('code', fallback_code)
    require(isinstance(code, str) and re.fullmatch(r'[a-z0-9-]{3,23}', code), 'THEME_BAD_CODE', 'code must use 3 to 23 letters, digits or hyphens')
    require(code not in RESERVED, 'THEME_BAD_CODE', 'This name belongs to a built-in theme')
    name = text_field(source.get('name'), 48, 'name')
    note = text_field(source['note'], 80, 'note') if 'note' in source else b''
    has_colors, has_terminal = 'colors' in source, 'terminal' in source
    require(has_colors or has_terminal, 'THEME_BAD_FORMAT', 'The file has neither interface nor terminal colors')
    flags = 0
    colors, tiles, alphas = dict.fromkeys(COLORS, 0), [[0] * 6, [0] * 6], [255, 255, 0]
    colors.update(terminal_bg=0, terminal_fg=0)
    width = height = 0
    pixels = thumb = b''
    if has_colors:
        flags |= FLAG_UI
        raw = source['colors'].get('bg') if isinstance(source['colors'], dict) else None
        dark = source.get('dark', luminance(color(raw, 'bg')) < .25 if raw is not None else True)
        require(isinstance(dark, bool), 'THEME_BAD_FORMAT', 'dark must be true or false')
        flags |= 0 if dark else FLAG_LIGHT
        colors, tiles = interface_colors(source, dark)
        if 'image' in source:
            width, height, pixels = picture(source['image'])
            thumb = thumbnail(width, height, pixels)
        glass = source.get('glass', {})
        require(isinstance(glass, dict), 'THEME_BAD_FORMAT', 'glass must be an object')
        # Glass needs something behind it, so a theme without a picture stays solid unless it asks.
        alphas = [glass.get('surface_alpha', 176 if pixels else 255), glass.get('edge_alpha', 110 if pixels else 255), glass.get('shadow_strength', 70 if pixels else 0)]
        require(all(type(item) is int and 0 <= item <= 255 for item in alphas) and alphas[0] >= 96, 'THEME_BAD_FORMAT', 'glass values must be 0 to 255, surface_alpha at least 96')
        layout = source.get('layout', 'tiles')
        require(layout in ('tiles', 'list'), 'THEME_BAD_FORMAT', 'layout must be tiles or list')
        flags |= FLAG_BENTO if layout == 'tiles' else 0
    palette = [0] * 16
    if has_terminal:
        flags |= FLAG_TERMINAL
        colors['terminal_bg'], colors['terminal_fg'], palette = terminal_colors(source)
    top, bottom = colors['bg'], colors['bg']
    if pixels:
        top, bottom = pixel(pixels, width, width // 2, height // 10), pixel(pixels, width, width // 2, height * 9 // 10)
    header = HEADER.pack(MAGIC, flags, *(colors[name] for name in COLORS), *tiles[0], *tiles[1], colors['terminal_bg'], colors['terminal_fg'], *palette,
                         *alphas, top, bottom, width, height, THUMB_WIDTH if thumb else 0, THUMB_HEIGHT if thumb else 0, code.encode(), name, note)
    return code, name.decode(), flags, header + thumb + pixels


@contextlib.contextmanager
def chosen_file(context, volume, token, path):
    relative = fs.clean_path(path)
    with fs.PinnedVolume(context, volume, token) as pin:
        with pin.parent(relative) as (parent, name):
            handle = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                st = os.fstat(handle)
                require(stat.S_ISREG(st.st_mode), 'THEME_NOT_A_FILE', 'Choose a regular file')
                pin.check_fd(handle)
                fd = os.open('/proc/self/fd/' + str(handle), os.O_RDONLY | os.O_CLOEXEC)
            finally:
                os.close(handle)
        try:
            yield fd
        finally:
            os.close(fd)


def read_chosen(context, volume, token, path):
    with chosen_file(context, volume, token, path) as fd:
        require(os.fstat(fd).st_size <= SOURCE_LIMIT, 'THEME_TOO_LARGE', 'The theme file is too large')
        with os.fdopen(os.dup(fd), 'rb') as source:
            return source.read(SOURCE_LIMIT + 1)


def write_file(folder, name, data):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = folder / (name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    temporary.replace(folder / name)


def on_disk(context, change):
    """Call change(folder) on the volume that keeps the settings. False when none took it."""
    if context.mode != 'device':
        change(context.root / settings.THEMES)
        return True
    try:
        with operation_lock(context.lock_path, context.mode):
            for label in settings.VOLUMES:
                try:
                    volume = context.volume(label)
                    # Browsing this volume read-only: the next one would not be read at the next start.
                    if volume['mounted'] and volume['read_only']:
                        return False
                    with settings.volume_root(context, label, 'rw') as root:
                        change(root / settings.THEMES)
                        return True
                except (OSError, fs.FilesystemError):
                    continue
    except OSError:
        pass
    return False


def install(context, code, data):
    """True when the theme will still be here after a reboot; it works in RAM either way."""
    settings.RAM_THEMES.mkdir(parents=True, exist_ok=True)
    present = {path.stem for path in settings.RAM_THEMES.glob('*.theme')}
    require(code in present or len(present) < INSTALLED_LIMIT, 'THEME_LIMIT', f'Remove a theme first: only {INSTALLED_LIMIT} fit')
    write_file(settings.RAM_THEMES, code + '.theme', data)
    (settings.RAM_THEMES / (code + '.hidden')).unlink(missing_ok=True)

    def keep(folder):
        write_file(folder, code + '.theme', data)
        (folder / (code + '.hidden')).unlink(missing_ok=True)
    return on_disk(context, keep)


def remove(context, code):
    """A theme that comes with the image cannot leave it, so a note hides it instead."""
    require(re.fullmatch(r'[a-z0-9-]{3,23}', code), 'THEME_BAD_CODE', 'This is not a theme name')
    bundled = (BUNDLED / (code + '.theme')).is_file()
    (settings.RAM_THEMES / (code + '.theme')).unlink(missing_ok=True)
    if bundled:
        write_file(settings.RAM_THEMES, code + '.hidden', b'')

    def forget(folder):
        (folder / (code + '.theme')).unlink(missing_ok=True)
        if bundled:
            write_file(folder, code + '.hidden', b'')
    return on_disk(context, forget)


def restore(context):
    """Bring back the themes of the image that were removed."""
    for note in settings.RAM_THEMES.glob('*.hidden'):
        note.unlink()

    def forget(folder):
        for note in folder.glob('*.hidden'):
            note.unlink()
    return on_disk(context, forget)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('compile', help='turn a theme file into what the interface loads')
    build.add_argument('source', type=Path)
    build.add_argument('output', type=Path)
    add = commands.add_parser('import')
    add.add_argument('--volume', required=True)
    add.add_argument('--expected-volume-token', required=True)
    add.add_argument('--path', required=True)
    commands.add_parser('remove').add_argument('--code', required=True)
    commands.add_parser('restore')
    args = parser.parse_args(argv)
    try:
        if args.command == 'compile':
            code, name, flags, data = compile_theme(args.source.read_bytes(), args.source.stem)
            args.output.write_bytes(data)
            result = {}
        else:
            context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
            if args.command == 'remove':
                result = {'kept': remove(context, args.code)}
                code, name, flags = args.code, '', 0
            elif args.command == 'restore':
                result = {'kept': restore(context)}
                code, name, flags = '', '', 0
            else:
                stem = re.sub(r'[^a-z0-9-]+', '-', Path(args.path).stem.lower()).strip('-')[:23]
                code, name, flags, data = compile_theme(read_chosen(context, args.volume, args.expected_volume_token, args.path), stem)
                result = {'kept': install(context, code, data)}
        print(json.dumps({'ok': True, 'operation': args.command, 'code': code, 'name': name, 'interface': bool(flags & FLAG_UI),
                          'terminal': bool(flags & FLAG_TERMINAL), **result}, ensure_ascii=False, separators=(',', ':')), flush=True)
        return 0
    except (OSError, ValueError, ThemeError, fs.FilesystemError) as error:
        code = getattr(error, 'code', None) or ('OPERATION_BUSY' if isinstance(error, BlockingIOError) else 'IO_ERROR')
        print(json.dumps({'ok': False, 'operation': args.command, 'error': {'code': code, 'message': str(error)}}, ensure_ascii=False, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
