"""Samsung firmware archives read in place: tar members, MD5 trailer, AP metadata, refusals."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import samsung_firmware as sf


def fota(version='A226BXXSBDZC3', device='a22x'):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('SYSTEM/build.prop', 'ro.build.PDA=%s\nro.build.version.release=13\n' % version)
        z.writestr('VENDOR/build.prop', 'ro.product.vendor.device=%s\n' % device)
        z.writestr('META/dynamic_partitions_info.txt', 'super_partition_size=7239368704\ndynamic_partition_list=system odm product vendor\n')
    return data.getvalue()


class Archives(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='a22x-samsung-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def archive(self, name, files, md5=True):
        tar_path = self.dir / (name[:-4] if name.endswith('.md5') else name)
        with tarfile.open(tar_path, 'w', format=tarfile.USTAR_FORMAT) as t:
            for member, data in files.items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        raw = tar_path.read_bytes()
        path = self.dir / name
        if md5:
            path.write_bytes(raw + ('%s  %s\n' % (hashlib.md5(raw).hexdigest(), tar_path.name)).encode())
            tar_path.unlink()
        return path

    def inspect(self, path, **kw):
        fd = os.open(path, os.O_RDONLY)
        try:
            return sf.inspect(fd, path.name, **kw)
        finally:
            os.close(fd)

    def error(self, code, path, **kw):
        with self.assertRaises(sf.FirmwareError) as caught:
            self.inspect(path, **kw)
        self.assertEqual(caught.exception.code, code)

    def ap(self, **kw):
        files = {'boot.img.lz4': b'B' * 700, 'super.img.lz4': b'S' * 1200, 'vbmeta.img.lz4': b'V' * 30,
                 'dtbo.img.lz4': b'D' * 50, 'recovery.img.lz4': b'R' * 40, 'userdata.img.lz4': b'U' * 30,
                 'tee-verified.img.lz4': b'T' * 20, 'vbmeta_system.img': b'v' * 10, 'meta-data/fota.zip': fota(**kw)}
        return self.archive('AP_A226BXXSBDZC3_A226BXXSBDZC3_MQB_REV00_user_low_ship_MULTI_CERT_meta_OS13.tar.md5', files)

    def test_ap_members_in_place_and_metadata(self):
        path = self.ap()
        result = self.inspect(path)
        self.assertEqual((result['kind'], result['version'], result['device'], result['android']), ('AP', 'A226BXXSBDZC3', 'a22x', '13'))
        self.assertTrue(result['md5_checked'])
        labels = {image['label']: image for image in result['images']}
        self.assertEqual(set(labels), {'boot', 'super', 'vbmeta', 'dtbo', 'vbmeta_system'})
        self.assertEqual((labels['super']['format'], labels['super']['compression']), ('sparse', 'lz4'))
        self.assertEqual((labels['vbmeta_system']['format'], labels['vbmeta_system']['compression']), ('raw', 'none'))
        raw = path.read_bytes()
        self.assertEqual(raw[labels['boot']['offset']:labels['boot']['offset'] + labels['boot']['size']], b'B' * 700)
        reasons = {item['member']: item['reason'] for item in result['skipped']}
        self.assertIn('recovery stays', reasons['recovery.img.lz4'])
        self.assertIn('bootloader-chain', reasons['tee-verified.img.lz4'])
        self.assertIn('formatted for Android', reasons['userdata.img.lz4'])

    def test_damage_other_phone_and_forbidden_archives(self):
        path = self.ap()
        damaged = bytearray(path.read_bytes())
        damaged[2000] ^= 1
        path.write_bytes(bytes(damaged))
        self.error('ARCHIVE_MD5_MISMATCH', path)
        self.error('FIRMWARE_OTHER_DEVICE', self._other())
        bl = self.archive('BL_A226BXXSBDZC3_test.tar.md5', {'lk-verified.img.lz4': b'L' * 10})
        self.error('FIRMWARE_PART_FORBIDDEN', bl)
        cp = self.archive('CP_A226BXXSBDZC3_test.tar.md5', {'md1img.img.lz4': b'M' * 10})
        self.error('FIRMWARE_PART_FORBIDDEN', cp)
        self.error('NOT_FIRMWARE_ARCHIVE', self.archive('rom.tar', {'boot.img': b'x'}, md5=False))

    def _other(self):
        files = {'boot.img.lz4': b'B', 'super.img.lz4': b'S', 'vbmeta.img.lz4': b'V', 'meta-data/fota.zip': fota(device='a32x')}
        return self.archive('AP_A325FXXU4CWB1_other.tar.md5', files)

    def test_csc_fills_only_prism_and_optics(self):
        csc = self.archive('CSC_OXM_A226BOXMBDZC3_test.tar.md5', {'prism.img.lz4': b'P' * 64, 'optics.img.lz4': b'O' * 64,
                                                                    'cache.img.lz4': b'C' * 8, 'A22X_EUR_OPEN.pit': b'pit'})
        result = self.inspect(csc)
        self.assertEqual(sorted(image['label'] for image in result['images']), ['optics', 'prism'])
        bad = self.archive('HOME_CSC_OXM_test.tar.md5', {'boot.img': b'x'})
        self.error('FIRMWARE_UNEXPECTED_PARTITION', bad)

    def test_unsafe_paths_and_plain_tar(self):
        bad = self.archive('AP_A226BXXSBDZC3_bad.tar', {'../boot.img': b'x'}, md5=False)
        self.error('UNSAFE_ARCHIVE_PATH', bad)
        plain = self.archive('CSC_OXM_plain.tar', {'optics.img': b'o' * 10}, md5=False)
        self.assertFalse(self.inspect(plain)['md5_checked'])

    def test_bootloader_version_and_compatibility(self):
        lk = b'\0' * 100 + b'SM_A226B\0A226BXXSBDZC3\0' + b'\0' * 50
        self.assertEqual(sf.bootloader_version(lk), 'A226BXXSBDZC3')
        self.assertEqual(sf.compatible('A226BXXSBEA12', 'A226BXXSBDZC3'), (True, None))
        self.assertEqual(sf.compatible('A226BXXU9CWA1', 'A226BXXSBDZC3'), (False, 'FIRMWARE_OLDER_THAN_BOOTLOADER'))
        self.assertEqual(sf.compatible('A226BXXSCFA11', 'A226BXXSBDZC3'), (False, 'FIRMWARE_NEEDS_NEWER_BOOTLOADER'))
        self.assertEqual(sf.compatible(None, 'A226BXXSBDZC3')[1], 'FIRMWARE_VERSION_UNKNOWN')
        self.assertEqual(sf.compatible('A226BXXSBDZC3', None)[1], 'BOOTLOADER_VERSION_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
