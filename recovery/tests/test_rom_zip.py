"""ROM ZIP inspection on generated archives; nothing from a ZIP is run or written."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rom_zip


class RomZip(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='a22x-romzip-', dir=os.environ.get('TMPDIR')))

    def tearDown(self):
        for p in sorted(self.dir.rglob('*'), reverse=True):
            p.unlink()
        self.dir.rmdir()

    def make(self, files, name='rom.zip'):
        path = self.dir / name
        with zipfile.ZipFile(path, 'w') as z:
            for member, data in files.items():
                z.writestr(member, data)
        return path

    def test_payload_ota_and_its_device(self):
        path = self.make({'payload.bin': b'CrAU', 'payload_properties.txt': b'FILE_HASH=x\n',
                          'META-INF/com/android/metadata': b'ota-type=AB\npre-device=a22x\n'})
        r = rom_zip.inspect(path)
        self.assertEqual((r['kind'], r['devices'], r['for_this_phone']), ('payload', ['a22x'], True))

    def test_block_ota_needs_its_transfer_list(self):
        r = rom_zip.inspect(self.make({'system.new.dat.br': b'x', 'system.transfer.list': b'4\n',
                                       'META-INF/com/google/android/updater-script':
                                       b'assert(getprop("ro.product.device") == "a22x" || abort("no"));\n'}))
        self.assertEqual((r['kind'], r['block_partitions'], r['for_this_phone']), ('block-ota', ['system'], True))
        lonely = rom_zip.inspect(self.make({'system.new.dat.br': b'x', 'META-INF/com/google/android/update-binary': b'#!'}, 'b.zip'))
        self.assertEqual(lonely['kind'], 'script-only')

    def test_images_and_forbidden_ones(self):
        r = rom_zip.inspect(self.make({'boot.img': b'ANDROID!', 'super.img': b'x', 'vbmeta.img': b'AVB0',
                                       'lk.img': b'x', 'preloader.img': b'x', 'md1img.img': b'x'}))
        self.assertEqual(r['kind'], 'images')
        self.assertEqual(r['images'], ['boot', 'super', 'vbmeta'])
        self.assertEqual(r['forbidden_images'], ['lk', 'md1img', 'preloader'])
        self.assertTrue(any('never written' in n for n in r['notes']))

    def test_samsung_parts_in_a_zip(self):
        r = rom_zip.inspect(self.make({'AP_A226BXXSBDZC3.tar.md5': b'x', 'BL_A226B.tar.md5': b'x', 'CSC_OXM.tar.md5': b'x'}))
        self.assertEqual((r['kind'], len(r['samsung_parts'])), ('samsung-tar', 3))

    def test_script_only_and_other_phone(self):
        r = rom_zip.inspect(self.make({'META-INF/com/google/android/update-binary': b'#!/sbin/sh\n',
                                       'META-INF/com/google/android/updater-script': b'#MAGISK\n'}))
        self.assertEqual(r['kind'], 'script-only')
        other = rom_zip.inspect(self.make({'boot.img': b'x', 'META-INF/com/android/metadata': b'pre-device=beyond1lte\n'}, 'o.zip'))
        self.assertFalse(other['for_this_phone'])

    def test_unsafe_paths_and_non_zips_are_refused(self):
        bad = self.dir / 'bad.zip'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr(zipfile.ZipInfo('../etc/passwd'), b'x')
        with self.assertRaises(rom_zip.ZipError) as caught:
            rom_zip.inspect(bad)
        self.assertEqual(caught.exception.code, 'UNSAFE_ZIP_PATH')
        plain = self.dir / 'plain.zip'
        plain.write_bytes(b'not a zip')
        with self.assertRaises(rom_zip.ZipError) as caught:
            rom_zip.inspect(plain)
        self.assertEqual(caught.exception.code, 'NOT_A_ZIP')


if __name__ == '__main__':
    unittest.main()
