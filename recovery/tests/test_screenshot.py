"""Screenshots packed and placed by screenshot.py (2026-11-11): on a card, on cache, or in RAM."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import filesystem as fs
import screenshot as sh

W, H = 37, 23


def pixels():
    return [[((x * 7) & 255, (y * 11) & 255, (x * y) & 255) for x in range(W)] for y in range(H)]


def scanlines(image, kinds=(0, 1, 2)):
    """The rows as the interface hands them over: none, Sub or Up, in turn."""
    out, above = bytearray(), bytes(W * 3)
    for y, row in enumerate(image):
        now = bytes(c for pixel in row for c in pixel)
        kind = kinds[y % len(kinds)]
        if kind == 0:
            line = now
        elif kind == 1:
            line = bytes((now[i] - (now[i - 3] if i >= 3 else 0)) & 255 for i in range(len(now)))
        else:
            line = bytes((now[i] - above[i]) & 255 for i in range(len(now)))
        out += bytes([kind]) + line
        above = now
    return bytes(out)


def decode(data):
    """The pixels of a PNG as this helper writes it."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    at, chunks = 8, {}
    while at < len(data):
        size, kind = struct.unpack('>I4s', data[at:at + 8])
        body = data[at + 8:at + 8 + size]
        assert struct.unpack('>I', data[at + 8 + size:at + 12 + size])[0] == zlib.crc32(kind + body)
        chunks[kind] = chunks.get(kind, b'') + body
        at += 12 + size
    width, height, depth, colour = struct.unpack('>IIBB', chunks[b'IHDR'][:10])
    assert (depth, colour) == (8, 2)
    raw, row, above, image = zlib.decompress(chunks[b'IDAT']), width * 3 + 1, bytes(width * 3), []
    for y in range(height):
        kind, line = raw[y * row], raw[y * row + 1:(y + 1) * row]
        now = bytearray(line)
        for i in range(len(now)):
            if kind == 1:
                now[i] = (now[i] + (now[i - 3] if i >= 3 else 0)) & 255
            elif kind == 2:
                now[i] = (now[i] + above[i]) & 255
        image.append([tuple(now[x * 3:x * 3 + 3]) for x in range(width)])
        above = bytes(now)
    return image


class Screenshot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-shot-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('sd', 'usb', 'cache', 'data'):
            (self.root / name).mkdir()
        self.raw = self.root / 'shot.raw'
        self.raw.write_bytes(scanlines(pixels()))
        self.volumes()

    def volumes(self, sd=(True, False), usb=None, cache=(True, True)):
        """Each of sd, usb, cache: (mounted, read_only), or None when it is not there."""
        rows = []
        for ident, path, state, kind in (('sdcard-p1', 'sd', sd, 'ext4'), ('usb-p1', 'usb', usb, 'exfat'), ('cache', 'cache', cache, 'ext4')):
            if state is not None:
                rows.append({'id': ident, 'path': path, 'fs_type': kind, 'mounted': state[0], 'read_only': state[1]})
        rows.append({'id': 'userdata', 'path': 'data', 'fs_type': 'f2fs', 'fscrypt_feature': True, 'mounted': False})
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': rows}))
        with contextlib.suppress(FileNotFoundError):
            (self.root / '.filesystem-state.json').unlink()

    def save(self, name='recovery-20261111-120000.png', raw=None, width=W, height=H):
        out = io.StringIO()
        result = self.root / 'result.json'
        with contextlib.redirect_stdout(out):
            code = sh.main(['--fixture', str(self.root), 'save', '--raw', str(raw or self.raw), '--width', str(width),
                            '--height', str(height), '--name', name, '--result', str(result)])
        printed = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(json.loads(result.read_text()), printed)  # the interface reads the file
        return code, printed

    def state(self, ident):
        volume = fs.FixtureContext(self.root).volume(ident)
        return volume['mounted'], volume['read_only']

    def test_the_png_holds_every_pixel_and_is_packed(self):
        code, done = self.save()
        self.assertEqual((code, done['where'], done['volume'], done['path']), (0, 'card', 'sdcard-p1', '/A22xScreenshots/recovery-20261111-120000.png'))
        data = (self.root / 'sd' / done['path'].lstrip('/')).read_bytes()
        self.assertEqual(len(data), done['bytes'])
        self.assertEqual(decode(data), pixels())
        flat = self.root / 'flat.raw'
        flat.write_bytes(scanlines([[(18, 22, 30)] * W for _ in range(H)], kinds=(2,)))
        code, done = self.save('flat.png', raw=flat)
        self.assertLess(done['bytes'], 200)  # a plain screen takes almost nothing

    def test_a_second_shot_in_the_same_second_gets_its_own_name(self):
        first, second = self.save()[1], self.save()[1]
        self.assertEqual(second['path'], '/A22xScreenshots/recovery-20261111-120000-2.png')
        self.assertNotEqual(first['path'], second['path'])

    def test_a_card_is_opened_for_the_moment_and_left_as_it_was(self):
        self.volumes(sd=(True, True))  # mounted read-only
        code, done = self.save()
        self.assertEqual((code, done['where']), (0, 'card'))
        self.assertEqual(self.state('sdcard-p1'), (True, True))
        self.assertTrue((self.root / 'sd/A22xScreenshots/recovery-20261111-120000.png').is_file())
        self.volumes(sd=(False, True))  # not mounted
        code, done = self.save('second.png')
        self.assertEqual((code, done['where']), (0, 'card'))
        self.assertEqual(self.state('sdcard-p1')[0], False)
        self.assertTrue((self.root / 'sd/A22xScreenshots/second.png').is_file())

    def test_a_usb_drive_comes_before_the_card_and_cache_after_both(self):
        self.volumes(sd=(True, False), usb=(True, False))
        self.assertEqual(self.save()[1]['volume'], 'usb-p1')
        self.volumes(sd=None, usb=None, cache=(True, True))
        code, done = self.save()
        self.assertEqual((done['where'], done['volume'], done['path']), ('cache', 'cache', '/a22x-recovery/screenshots/recovery-20261111-120000.png'))
        self.assertEqual(self.state('cache'), (True, True))  # read-only again
        self.assertEqual(decode((self.root / 'cache' / done['path'].lstrip('/')).read_bytes()), pixels())

    def test_nothing_is_mounted_while_storage_is_busy(self):
        """While a job holds the lease a mount is refused: a place that is writable already is used,
        else RAM, and no volume changes its state."""
        self.volumes(sd=(True, True), cache=(True, True))
        busy = fs.FilesystemError('OPERATION_BUSY', 'Storage is busy')
        with mock.patch.object(fs.FixtureContext, 'mount', side_effect=busy):
            code, done = self.save()
        self.assertEqual((code, done['where']), (0, 'ram'))
        self.assertEqual(done['refused'], ['sdcard-p1: OPERATION_BUSY', 'cache: OPERATION_BUSY'])
        self.assertEqual(decode(Path(done['path']).read_bytes()), pixels())
        self.assertEqual((self.state('sdcard-p1'), self.state('cache')), ((True, True), (True, True)))
        self.volumes(sd=(True, False), cache=(True, True))  # the card is writable already: no mount is needed
        with mock.patch.object(fs.FixtureContext, 'mount', side_effect=busy):
            code, done = self.save('busy.png')
        self.assertEqual((code, done['where']), (0, 'card'))

    def test_old_screenshots_leave_cache_when_they_take_too_much(self):
        self.volumes(sd=None, cache=(True, False))
        folder = self.root / 'cache/a22x-recovery/screenshots'
        folder.mkdir(parents=True)
        for n in range(4):
            (folder / ('old-%d.png' % n)).write_bytes(bytes(8 << 20))
            os.utime(folder / ('old-%d.png' % n), (1000 + n, 1000 + n))
        (folder / 'notes.txt').write_text('kept: not a screenshot')
        code, done = self.save()
        self.assertEqual((code, done['where']), (0, 'cache'))
        left = sorted(p.name for p in folder.iterdir())
        # 24 MiB: the new one and the two newest of 8 MiB stay; the two oldest go.
        self.assertEqual(left, ['notes.txt', 'old-2.png', 'old-3.png', 'recovery-20261111-120000.png'])

    def test_what_is_no_screen_is_refused(self):
        code, done = self.save(width=W + 1)
        self.assertEqual((code, done['error']['code']), (2, 'INVALID_SCREENSHOT'))
        bad = self.root / 'bad.raw'
        data = bytearray(scanlines(pixels()))
        data[0] = 9  # no PNG filter
        bad.write_bytes(bytes(data))
        code, done = self.save(raw=bad)
        self.assertEqual((code, done['error']['code']), (2, 'INVALID_SCREENSHOT'))
        for name in ('../x.png', 'shot.jpg', '.hidden.png', 'a/b.png'):
            code, done = self.save(name)
            self.assertEqual((code, done['error']['code']), (2, 'INVALID_ARGUMENT'), name)
        self.assertFalse((self.root / 'sd/A22xScreenshots').exists())


if __name__ == '__main__':
    unittest.main()
