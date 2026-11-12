"""7z archives read in place (sevenzip.py, 2026-11-10): archives made by 7-Zip itself (solid
LZMA2, LZMA, stored, compressed headers), the image chosen in them, damage and encryption."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sevenzip  # noqa: E402

TOOL = shutil.which('7z')


@unittest.skipUnless(TOOL, '7-Zip makes the archives')
class SevenZip(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-7z-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        src = self.root / 'src'
        src.mkdir()
        self.files = {'system.img': os.urandom(700000) + bytes(2000000), 'notes.txt': b'GSI notes\n' * 500,
                      'vbmeta.img': b'AVB0' + bytes(4092)}
        for name, data in self.files.items():
            (src / name).write_bytes(data)

    def make(self, name, *options, files=('system.img', 'notes.txt', 'vbmeta.img')):
        subprocess.run([TOOL, 'a', '-t7z', *options, str(self.root / name), *files], cwd=self.root / 'src', check=True,
                       stdout=subprocess.DEVNULL)
        return self.root / name

    def read_all(self, path, member=None):
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        files, info = sevenzip.entries(fd)
        entry = sevenzip.choose(files, member)
        reader, out = sevenzip.EntryReader(fd, info, entry), bytearray()
        while True:
            data = reader.read(65536)
            if not data:
                break
            out.extend(data)
        reader.finish()
        return entry['name'], bytes(out)

    def test_every_method_7zip_uses_for_images(self):
        for name, options in (('solid.7z', ('-m0=lzma2', '-mx=5')), ('lzma.7z', ('-m0=lzma', '-ms=off')),
                              ('stored.7z', ('-m0=copy',)), ('header.7z', ('-m0=lzma2', '-mhc=on'))):
            path = self.make(name, *options)
            self.assertEqual(self.read_all(path), ('system.img', self.files['system.img']), name)
            self.assertEqual(self.read_all(path, 'vbmeta.img')[1], self.files['vbmeta.img'], name)
            self.assertEqual(self.read_all(path, 'notes.txt')[1], self.files['notes.txt'], name)

    def test_the_only_image_is_chosen_and_two_need_a_name(self):
        path = self.make('one.7z', '-m0=lzma2', files=('notes.txt', 'system.img'))
        os.rename(self.root / 'src/system.img', self.root / 'src/LineageOS-23.2-GSI.img')
        path = self.make('lineage.7z', '-m0=lzma2', files=('LineageOS-23.2-GSI.img',))
        self.assertEqual(self.read_all(path)[0], 'LineageOS-23.2-GSI.img')
        path = self.make('two.7z', '-m0=lzma2', files=('LineageOS-23.2-GSI.img', 'vbmeta.img', 'notes.txt'))
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(sevenzip.SevenZipError) as caught:
            sevenzip.choose(sevenzip.entries(fd)[0])
        self.assertEqual(caught.exception.code, '7Z_AMBIGUOUS')

    def test_damage_and_encryption_are_refused(self):
        path = self.make('damaged.7z', '-m0=copy', files=('system.img',))
        raw = bytearray(path.read_bytes())
        raw[32 + 1000] ^= 0xff
        path.write_bytes(bytes(raw))
        with self.assertRaises(sevenzip.SevenZipError) as caught:
            self.read_all(path)
        self.assertEqual(caught.exception.code, 'DAMAGED_7Z')
        path = self.make('secret.7z', '-m0=lzma2', '-psecret', '-mhe=off', files=('system.img',))
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        files, info = sevenzip.entries(fd)
        with self.assertRaises(sevenzip.SevenZipError) as caught:
            sevenzip.EntryReader(fd, info, files[0])
        self.assertEqual(caught.exception.code, 'ENCRYPTED_7Z')
        (self.root / 'plain.txt').write_bytes(b'not 7z' * 10)
        fd = os.open(self.root / 'plain.txt', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(sevenzip.SevenZipError) as caught:
            sevenzip.entries(fd)
        self.assertEqual(caught.exception.code, 'NOT_7Z')


if __name__ == '__main__':
    unittest.main()
