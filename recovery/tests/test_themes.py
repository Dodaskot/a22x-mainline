"""Theme files: what is refused, and what reaches the interface. No device is touched."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import filesystem as fs
import themes

PALETTE = ['#101010', '#ee6666', '#66ee66', '#eeee66', '#6666ee', '#ee66ee', '#66eeee', '#dddddd'] * 2


def colors(**changes):
    return {'bg': '#101214', 'panel': '#1c1f24', 'text': '#f5f5f5', 'muted': '#b0b6c0', 'accent': '#ffaa44', 'on_accent': '#201000', **changes}


def theme(**changes):
    return {'format': 1, 'code': 'sample', 'name': 'Sample', 'colors': colors(), **changes}


def picture(width=4, height=6, pixels=None):
    raw = pixels if pixels is not None else bytes(range(width * height * 3))
    return {'width': width, 'height': height, 'data': base64.b64encode(zlib.compress(raw)).decode()}


def field(data, name):
    """Read one value of the compiled header by its place in the format."""
    values = themes.HEADER.unpack(data[:themes.HEADER.size])
    place = 2 + len(themes.COLORS) + 12 + 2 + 16
    return {'flags': values[1], 'bg': values[2], 'text': values[2 + themes.COLORS.index('text')], 'accent': values[2 + themes.COLORS.index('accent')],
            'palette': values[2 + len(themes.COLORS) + 14:place], 'surface_alpha': values[place], 'image_w': values[place + 5], 'image_h': values[place + 6],
            'thumb_w': values[place + 7], 'thumb_h': values[place + 8], 'code': values[place + 9].rstrip(b'\0'), 'name': values[place + 10].rstrip(b'\0')}[name]


class WriteFile(unittest.TestCase):
    def test_a_failed_write_leaves_no_temporary_file(self):
        # 2026-11-14 review: each failed import left its own temporary file on a full cache.
        from unittest import mock
        folder = Path(tempfile.mkdtemp(dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, folder, True)
        with mock.patch.object(themes.os, 'fsync', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                themes.write_file(folder, 'x.theme', b'data')
        self.assertEqual(list(folder.iterdir()), [])
        themes.write_file(folder, 'x.theme', b'data')
        self.assertEqual([p.name for p in folder.iterdir()], ['x.theme'])


class Compile(unittest.TestCase):
    def build(self, source):
        return themes.compile_theme(json.dumps(source).encode())

    def refused(self, code, source):
        with self.assertRaises(themes.ThemeError) as caught:
            self.build(source)
        self.assertEqual(caught.exception.code, code)

    def test_a_few_colors_are_enough(self):
        code, name, flags, data = self.build(theme())
        self.assertEqual((code, name), ('sample', 'Sample'))
        self.assertEqual(flags, themes.FLAG_UI | themes.FLAG_BENTO)
        self.assertEqual(len(data), themes.HEADER.size)
        self.assertEqual((field(data, 'bg'), field(data, 'accent'), field(data, 'text')), (0x101214, 0xffaa44, 0xf5f5f5))
        self.assertEqual(field(data, 'surface_alpha'), 255, 'without a picture nothing shows through glass')

    def test_the_header_matches_the_interface(self):
        self.assertEqual(themes.HEADER.size, 420)
        declared = (ROOT / 'ui/theme_files.h').read_text()
        self.assertIn('sizeof(ThemeFileHeader)==420', declared)
        self.assertIn(f'THEME_THUMB_W {themes.THUMB_WIDTH}u', declared)
        self.assertIn(f'THEME_IMAGE_H {themes.IMAGE_HEIGHT}u', declared)

    def test_built_in_names_are_reserved(self):
        header = (ROOT / 'ui/theme.h').read_text()
        self.assertEqual(themes.RESERVED, set(re.findall(r'\{\.code="([a-z-]+)"', header)))
        self.refused('THEME_BAD_CODE', theme(code='graphite'))
        self.refused('THEME_BAD_CODE', theme(code='auto'))

    def test_a_picture_comes_with_a_thumbnail(self):
        _, _, _, data = self.build(theme(image=picture()))
        thumb = themes.THUMB_WIDTH * themes.THUMB_HEIGHT * 3
        self.assertEqual((field(data, 'image_w'), field(data, 'image_h'), field(data, 'thumb_w')), (4, 6, themes.THUMB_WIDTH))
        self.assertEqual(len(data), themes.HEADER.size + thumb + 4 * 6 * 3)
        self.assertEqual(data[-72:], bytes(range(72)))
        self.assertLess(field(data, 'surface_alpha'), 255)

    def test_terminal_colors_alone_are_a_theme_file_too(self):
        _, _, flags, data = self.build({'format': 1, 'code': 'green-tty', 'name': 'Green', 'terminal': {'background': '#000000', 'foreground': '#33ff33', 'palette': PALETTE}})
        self.assertEqual(flags, themes.FLAG_TERMINAL)
        self.assertEqual(field(data, 'palette')[1], 0xee6666)

    def test_what_nobody_could_read_is_refused(self):
        self.refused('THEME_UNREADABLE', theme(colors=colors(text='#101214')))
        self.refused('THEME_UNREADABLE', theme(colors=colors(muted='#22252a')))
        self.refused('THEME_UNREADABLE', theme(colors=colors(on_accent='#ffaa44')))
        self.refused('THEME_UNREADABLE', theme(colors=colors(toast_fill='#808080', toast_text='#888888')))
        self.refused('THEME_UNREADABLE', {'format': 1, 'code': 'dim', 'name': 'Dim', 'terminal': {'background': '#000000', 'foreground': '#111111', 'palette': PALETTE}})

    def test_broken_files_are_refused(self):
        with self.assertRaises(themes.ThemeError) as caught:
            themes.compile_theme(b'\x89PNG\r\n')
        self.assertEqual(caught.exception.code, 'THEME_NOT_JSON')
        self.refused('THEME_NOT_JSON', [1, 2])
        self.refused('THEME_NOT_JSON', theme(format=2))
        self.refused('THEME_BAD_COLOR', theme(colors=colors(accent='orange')))
        self.refused('THEME_BAD_FORMAT', theme(colors={'bg': '#000000'}))
        self.refused('THEME_BAD_FORMAT', theme(colors=colors(glow='#ffffff')))
        self.refused('THEME_BAD_FORMAT', theme(colors=colors(tile_fill=['#000000'])))
        self.refused('THEME_BAD_FORMAT', {'format': 1, 'code': 'empty', 'name': 'Empty'})
        self.refused('THEME_BAD_FORMAT', theme(glass={'surface_alpha': 10}))
        self.refused('THEME_BAD_CODE', theme(code='../evil'))
        self.refused('THEME_BAD_CODE', theme(code='a' * 24))
        self.refused('THEME_BAD_TEXT', theme(name='x' * 48))
        self.refused('THEME_BAD_TEXT', theme(name='bad\nname'))
        self.refused('THEME_BAD_TEXT', theme(note=7))

    def test_a_picture_must_be_what_it_says(self):
        self.refused('THEME_BAD_IMAGE', theme(image=picture(pixels=b'short')))
        self.refused('THEME_BAD_IMAGE', theme(image=picture(themes.IMAGE_WIDTH + 1, 2, bytes((themes.IMAGE_WIDTH + 1) * 6))))
        self.refused('THEME_BAD_IMAGE', theme(image={'width': 2, 'height': 2, 'data': '!!!'}))
        self.refused('THEME_BAD_IMAGE', theme(image={'width': 2, 'height': 2, 'data': base64.b64encode(b'not deflate').decode()}))
        self.refused('THEME_BAD_IMAGE', theme(image={'width': True, 'height': 2, 'data': ''}))

    def test_a_small_file_cannot_become_a_huge_picture(self):
        bomb = zlib.compress(bytes(200 * 1024 * 1024), 9)
        self.refused('THEME_BAD_IMAGE', theme(image={'width': 2, 'height': 2, 'data': base64.b64encode(bomb).decode()}))

    def test_a_file_too_large_is_not_read(self):
        with self.assertRaises(themes.ThemeError) as caught:
            themes.compile_theme(b' ' * (themes.SOURCE_LIMIT + 1))
        self.assertEqual(caught.exception.code, 'THEME_TOO_LARGE')

    def test_the_beaver_is_a_theme_like_any_other(self):
        code, name, flags, data = themes.compile_theme((ROOT / 'themes/beaver.a22xtheme').read_bytes())
        self.assertEqual((code, name), ('beaver', 'Beaver'))
        self.assertEqual(flags, themes.FLAG_UI | themes.FLAG_TERMINAL | themes.FLAG_BENTO)
        self.assertEqual((field(data, 'image_w'), field(data, 'image_h')), (themes.IMAGE_WIDTH, themes.IMAGE_HEIGHT))


class Files(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-themes-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'stick').mkdir()
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'usb-p1', 'path': 'stick', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}]}))
        original = themes.settings.RAM_THEMES
        themes.settings.RAM_THEMES = self.root / 'ram'
        self.addCleanup(setattr, themes.settings, 'RAM_THEMES', original)

    def helper(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = themes.main(['--fixture', str(self.root), *args])
        return code, json.loads(stdout.getvalue().splitlines()[-1])

    def bring(self, name, source=None):
        (self.root / 'stick' / name).write_text(json.dumps(source or theme()))
        token = fs.FixtureContext(self.root).volume('usb-p1')['volume_token']
        return self.helper('import', '--volume', 'usb-p1', '--expected-volume-token', token, '--path', '/' + name)

    def test_import_keeps_a_compiled_copy(self):
        code, reply = self.bring('sample.a22xtheme')
        self.assertEqual(code, 0, reply)
        self.assertEqual((reply['code'], reply['interface'], reply['terminal'], reply['kept']), ('sample', True, False, True))
        self.assertTrue((self.root / 'ram/sample.theme').is_file())
        self.assertTrue((self.root / 'a22x-recovery/themes/sample.theme').is_file())

    def test_the_name_may_come_from_the_file(self):
        source = theme()
        del source['code']
        code, reply = self.bring('Мій Beaver Dark.a22xtheme', source)
        self.assertEqual((code, reply['code']), (0, 'beaver-dark'))

    def test_remove_takes_it_from_ram_and_storage(self):
        self.bring('sample.a22xtheme')
        code, reply = self.helper('remove', '--code', 'sample')
        self.assertEqual((code, reply['kept']), (0, True))
        self.assertFalse((self.root / 'ram/sample.theme').exists())
        self.assertFalse((self.root / 'a22x-recovery/themes/sample.theme').exists())
        self.assertEqual(self.helper('remove', '--code', '../settings')[0], 2)

    def test_a_theme_of_the_image_is_hidden_and_can_come_back(self):
        bundled = self.root / 'bundled'
        bundled.mkdir()
        (bundled / 'beaver.theme').write_bytes(b'x')
        original = themes.BUNDLED
        themes.BUNDLED = bundled
        self.addCleanup(setattr, themes, 'BUNDLED', original)
        note = 'a22x-recovery/themes/beaver.hidden'
        self.assertEqual(self.helper('remove', '--code', 'beaver')[0], 0)
        self.assertTrue((self.root / 'ram/beaver.hidden').is_file())
        self.assertTrue((self.root / note).is_file())
        self.assertEqual(self.helper('restore')[0], 0)
        self.assertFalse((self.root / 'ram/beaver.hidden').exists())
        self.assertFalse((self.root / note).exists())
        self.helper('remove', '--code', 'beaver')
        self.assertEqual(self.bring('b.a22xtheme', theme(code='beaver'))[0], 0)
        self.assertFalse((self.root / 'ram/beaver.hidden').exists(), 'bringing a file with this name brings the theme back')
        self.assertFalse((self.root / note).exists())

    def test_there_is_a_limit(self):
        for number in range(themes.INSTALLED_LIMIT):
            self.assertEqual(self.bring('t.a22xtheme', theme(code=f'theme-{number}'))[0], 0)
        code, reply = self.bring('t.a22xtheme', theme(code='one-too-many'))
        self.assertEqual((code, reply['error']['code']), (2, 'THEME_LIMIT'))
        self.assertEqual(self.bring('t.a22xtheme', theme(code='theme-0', name='Again'))[0], 0, 'replacing one fits')

    def test_a_link_on_the_stick_is_not_followed(self):
        os.symlink('/etc/passwd', self.root / 'stick/link.a22xtheme')
        token = fs.FixtureContext(self.root).volume('usb-p1')['volume_token']
        code, reply = self.helper('import', '--volume', 'usb-p1', '--expected-volume-token', token, '--path', '/link.a22xtheme')
        self.assertEqual(code, 2)
        self.assertFalse(reply['ok'])

    def test_a_stale_volume_is_refused(self):
        (self.root / 'stick/sample.a22xtheme').write_text(json.dumps(theme()))
        code, reply = self.helper('import', '--volume', 'usb-p1', '--expected-volume-token', '0' * 64, '--path', '/sample.a22xtheme')
        self.assertEqual(code, 2)
        self.assertFalse(reply['ok'])
        self.assertFalse((self.root / 'ram/sample.theme').exists())


if __name__ == '__main__':
    unittest.main()
