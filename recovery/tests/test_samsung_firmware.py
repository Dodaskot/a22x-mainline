"""Samsung's stock firmware flashed as Odin would (samsung_firmware.py, 2026-11-10): a fixture with
AP, CSC, HOME_CSC, BL and CP tars as Samsung builds them (LZ4 images, MD5 after the tar)."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import samsung_firmware as sf  # noqa: E402
import test_bootimage as tb  # noqa: E402
from test_flash import boot_image, sparse, vbmeta  # noqa: E402

SIZES = {'boot': 1 << 20, 'dtbo': 1 << 18, 'vbmeta': 1 << 16, 'vbmeta_system': 1 << 16, 'super': 1 << 21,
         'prism': 1 << 20, 'optics': 1 << 18}


def lz4(data):
    return subprocess.run(['lz4', '-c'], input=data, stdout=subprocess.PIPE, check=True).stdout


def samsung_tar(path, members):
    """A tar with its MD5 after it, as Samsung's .tar.md5."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    body = stream.getvalue()
    path.write_bytes(body + (hashlib.md5(body).hexdigest() + '  ' + path.name[:-4] + '\n').encode())


@unittest.skipUnless(shutil.which('lz4'), 'lz4 is needed')
class Firmware(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-samsung-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('usb/fw', 'targets', 'cache', 'data'):
            (self.root / name).mkdir(parents=True)
        targets = {}
        for label, size in SIZES.items():
            (self.root / 'targets' / label).write_bytes(b'\x11' * size)
            targets[label] = {'path': 'targets/' + label, 'size_bytes': size}
        # BOOT holds a mainline Linux for this phone: it is saved on cache before stock BOOT.
        self.linux_boot = boot_image()
        with open(self.root / 'targets/boot', 'r+b') as f:
            f.write(self.linux_boot)
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'}, 'targets': targets}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True},
            {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True},
            {'id': 'userdata', 'path': 'data', 'fs_type': 'ext4', 'mounted': True, 'read_only': False}]}))
        (self.root / 'cache/a22x-recovery').mkdir()
        (self.root / 'cache/a22x-recovery/settings.json').write_text('{}')
        (self.root / 'cache/recovery').mkdir()
        (self.root / 'cache/recovery/last_log').write_text('old')
        payload = bytearray(8192)
        payload[4096:4100] = b'gDla'
        self.images = {'super': sparse(bytes(payload)), 'boot': boot_image(size=4096),
                       'dtbo': tb.dt_table(tb.MAINLINE_TREE), 'vbmeta': vbmeta(b'v' * 900),
                       'vbmeta_system': vbmeta(b's' * 700), 'prism': b'prism' * 3000, 'optics': b'optics' * 700}
        fw = self.root / 'usb/fw'
        self.ap = fw / 'AP_A226BXXSBDZC3_A226BXXSBDZC3_MQB0_REV00_user_low_ship_MULTI_CERT_meta_OS13.tar.md5'
        samsung_tar(self.ap, [('boot.img.lz4', lz4(self.images['boot'])), ('recovery.img.lz4', lz4(b'stock recovery')),
                              ('super.img.lz4', lz4(self.images['super'])), ('userdata.img.lz4', lz4(b'samsung data')),
                              ('vbmeta.img.lz4', lz4(self.images['vbmeta'])), ('dtbo.img.lz4', lz4(self.images['dtbo'])),
                              ('tee-verified.img.lz4', lz4(b'tee')), ('vbmeta_system.img.lz4', lz4(self.images['vbmeta_system'])),
                              ('meta-data/fota.zip', b'PK\x05\x06' + bytes(18))])
        samsung_tar(fw / 'CSC_OXM_A226BOXMBDZC3_MQB0_REV00_user_low_ship_MULTI_CERT.tar.md5', [
            ('A22X_EUR_OPEN.pit', b'pit' * 100), ('cache.img.lz4', lz4(b'cache')), ('omr.img.lz4', lz4(b'omr')),
            ('prism.img.lz4', lz4(self.images['prism'])), ('optics.img.lz4', lz4(self.images['optics']))])
        samsung_tar(fw / 'HOME_CSC_OXM_A226BOXMBDZC3_MQB0_REV00_user_low_ship_MULTI_CERT.tar.md5', [
            ('cache.img.lz4', lz4(b'cache')), ('prism.img.lz4', lz4(b'other prism')), ('optics.img.lz4', lz4(b'other optics'))])
        samsung_tar(fw / 'BL_A226BXXSBDZC3_A226BXXSBDZC3_MQB0_REV00_user_low_ship_MULTI_CERT.tar.md5', [
            ('preloader.img.lz4', lz4(b'preloader')), ('lk-verified.img.lz4', lz4(b'lk')), ('vbmeta.img.lz4', lz4(b'bl vbmeta'))])
        samsung_tar(fw / 'CP_A226BXXSBDZC3_CP0_MQB0_REV00_user_low_ship_MULTI_CERT.tar.md5', [('md1img.img.lz4', lz4(b'modem'))])

    def run_helper(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sf.main(['--fixture', str(self.root), *args])
        return code, json.loads(stdout.getvalue().splitlines()[-1]), stderr.getvalue()

    def token(self):
        import filesystem as fs
        return fs.FixtureContext(self.root).volume('usb-p1')['volume_token']

    def inspect(self, path=None):
        return self.run_helper('inspect', '--volume', 'usb-p1', '--volume-token', self.token(),
                               '--path', '/fw/' + (path or self.ap.name))

    def install(self, plan, path=None):
        return self.run_helper('install', '--volume', 'usb-p1', '--volume-token', self.token(),
                               '--path', '/fw/' + (path or self.ap.name), '--expected-plan', plan, '--confirm')

    def test_the_plan_names_what_happens_to_every_image(self):
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan['build'], plan['blockers'], plan['warnings']), ('A226BXXSBDZC3', [], []))
        self.assertEqual([f['kind'] for f in plan['files']], ['AP', 'CSC', 'BL', 'CP'])  # CSC before HOME_CSC
        self.assertEqual([w['target'] for w in plan['writes']],
                         ['super', 'prism', 'optics', 'dtbo', 'vbmeta_system', 'vbmeta', 'boot'])
        fate = {(r['file'], r['name']): r['action'] for r in plan['rows']}
        self.assertEqual(fate[('AP', 'recovery.img.lz4')], 'kept')
        self.assertEqual(fate[('AP', 'userdata.img.lz4')], 'instead')
        self.assertEqual(fate[('AP', 'tee-verified.img.lz4')], 'refused')
        self.assertEqual(fate[('AP', 'meta-data/fota.zip')], 'skipped')
        self.assertEqual(fate[('CSC', 'A22X_EUR_OPEN.pit')], 'refused')
        self.assertEqual(fate[('CSC', 'cache.img.lz4')], 'instead')
        self.assertEqual(fate[('CSC', 'omr.img.lz4')], 'kept')
        self.assertEqual({fate[('BL', n)] for n in ('preloader.img.lz4', 'lk-verified.img.lz4', 'vbmeta.img.lz4')}, {'refused'})
        self.assertEqual(fate[('CP', 'md1img.img.lz4')], 'kept')
        self.assertEqual(plan['erase'], ['userdata', 'metadata', 'cache'])
        code, wrong, _ = self.inspect(next(p.name for p in (self.root / 'usb/fw').iterdir() if p.name.startswith('CSC_')))
        self.assertEqual((code, wrong['error']['code']), (2, 'AP_REQUIRED'))

    def test_install_writes_android_and_keeps_the_way_back(self):
        _, plan, _ = self.inspect()
        code, refused, _ = self.install('0' * 64)
        self.assertEqual((code, refused['error']['code']), (2, 'PLAN_CHANGED'))
        self.assertEqual((self.root / 'targets/super').read_bytes(), b'\x11' * SIZES['super'])
        code, done, progress = self.install(plan['plan_sha256'])
        self.assertEqual(code, 0, done)
        self.assertEqual(done['md5_checked'], [f['name'] for f in plan['files']])
        self.assertEqual([w['target'] for w in done['written']], [w['target'] for w in plan['writes']])
        for label in ('dtbo', 'vbmeta', 'vbmeta_system', 'prism', 'optics', 'boot'):
            self.assertEqual((self.root / 'targets' / label).read_bytes()[:len(self.images[label])], self.images[label], label)
        self.assertEqual((self.root / 'targets/super').read_bytes()[4096:4100], b'gDla')
        # The Linux BOOT that was there waits on cache, with what it was.
        saved = self.root / 'cache' / done['saved_boot'].lstrip('/')
        self.assertTrue(saved.read_bytes().startswith(b'ANDROID!'))
        self.assertEqual(saved.read_bytes(), self.linux_boot + b'\x11' * (SIZES['boot'] - len(self.linux_boot)))  # the whole partition
        meta = json.loads(Path(str(saved) + '.json').read_text())
        self.assertEqual(meta['sha256'], hashlib.sha256(saved.read_bytes()).hexdigest())
        # Cache wiped but for the recovery's folder; userdata and metadata reset for Android.
        self.assertEqual(sorted(p.name for p in (self.root / 'cache').iterdir()), ['a22x-recovery'])
        self.assertTrue((self.root / 'cache/a22x-recovery/settings.json').exists())
        self.assertTrue((self.root / 'android-reset.json').exists())
        self.assertIn('"phase":"Verifying"', progress)
        self.assertIn('"phase":"Saving BOOT"', progress)

    def test_a_damaged_file_stops_before_anything_is_written(self):
        csc = next(p for p in (self.root / 'usb/fw').iterdir() if p.name.startswith('CSC_'))
        raw = bytearray(csc.read_bytes())
        raw[600] ^= 0xff  # inside the PIT: the MD5 no longer matches
        csc.write_bytes(bytes(raw))
        _, plan, _ = self.inspect()
        code, failed, _ = self.install(plan['plan_sha256'])
        self.assertEqual((code, failed['error']['code']), (2, 'FIRMWARE_DAMAGED'))
        self.assertEqual((self.root / 'targets/super').read_bytes(), b'\x11' * SIZES['super'])
        self.assertTrue((self.root / 'cache/recovery/last_log').exists())
        self.assertFalse((self.root / 'android-reset.json').exists())

    def test_data_for_android_alone(self):
        """Wipe, Data for Android (TWRP's Format Data): only the reset, confirmed, cache's folder kept."""
        code, refused, _ = self.run_helper('reset-data')
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        self.assertFalse((self.root / 'android-reset.json').exists())
        code, done, _ = self.run_helper('reset-data', '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual((done['userdata'], done['metadata']), ('android', 'erased'))
        self.assertTrue((self.root / 'android-reset.json').exists())
        self.assertEqual(sorted(p.name for p in (self.root / 'cache').iterdir()), ['a22x-recovery'])
        self.assertEqual((self.root / 'targets/super').read_bytes(), b'\x11' * SIZES['super'])

    def test_stock_boot_comes_out_of_the_ap_or_its_zip(self):
        """2026-11-10: with a custom kernel on the phone, Samsung's BOOT is taken out of the firmware
        (the AP itself, or the ZIP samfw.com ships, its tars deflated) and kept on cache."""
        import zipfile
        from test_android_gsi import stock_boot
        stock = stock_boot()
        samsung_tar(self.ap, [('boot.img.lz4', lz4(stock)), ('super.img.lz4', lz4(self.images['super']))])
        code, done, _ = self.run_helper('extract-boot', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/fw/' + self.ap.name)
        self.assertEqual(code, 0, done)
        self.assertEqual((done['build'], done['release'], done['volume']), ('A226BXXSBDZC3', '4.14.186-27218697', 'cache'))
        saved = self.root / 'cache' / done['saved_boot'].lstrip('/')
        self.assertEqual(saved.read_bytes(), stock)
        self.assertTrue(saved.name.startswith('stock-A226BXXSBDZC3-'))
        self.assertEqual(Path(str(saved) + '.sha256').read_text().split()[0], hashlib.sha256(stock).hexdigest())
        self.assertEqual(json.loads(Path(str(saved) + '.json').read_text())['kind'], 'android')
        # The same from the ZIP: deflated tars, as samfw.com ships the firmware.
        bundle = self.root / 'usb/fw/SAMFW.COM_SM-A226B_VD2_A226BXXSBDZC3_fac.zip'
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(self.ap, self.ap.name)
            z.writestr('_FirmwareInfo_Samfw.com.txt', 'info')
        saved.unlink()
        code, done, _ = self.run_helper('extract-boot', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/fw/' + bundle.name)
        self.assertEqual(code, 0, done)
        self.assertEqual((self.root / 'cache' / done['saved_boot'].lstrip('/')).read_bytes(), stock)
        # A firmware whose BOOT is a Linux kernel is not stock.
        samsung_tar(self.ap, [('boot.img.lz4', lz4(self.images['boot']))])
        code, refused, _ = self.run_helper('extract-boot', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/fw/' + self.ap.name)
        self.assertEqual((code, refused['error']['code']), (2, 'NOT_STOCK_BOOT'))

    def test_a_samfw_zip_is_unpacked_beside_it_and_installs(self):
        """2026-11-10: the firmware as samfw.com ships it, one ZIP with the AP, BL, CP, CSC and
        HOME_CSC tars deflated: unpacked beside it (the ZIP's CRC checked), then the usual plan."""
        import zipfile
        fw = self.root / 'usb/fw'
        bundle = self.root / 'usb/SAMFW.COM_SM-A226B_VD2_A226BXXSBDZC3_fac.zip'
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
            for item in sorted(fw.iterdir()):
                z.write(item, item.name)
            z.writestr('_FirmwareInfo_Samfw.com.txt', 'info')
        shutil.rmtree(fw)
        volumes = json.loads((self.root / 'filesystem-fixture.json').read_text())
        for v in volumes['volumes']:
            if v['id'] == 'usb-p1':
                v['read_only'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(volumes))
        code, refused, _ = self.run_helper('unpack', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/' + bundle.name)
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        code, done, progress = self.run_helper('unpack', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/' + bundle.name,
                                               '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual(done['folder'], '/SAMFW.COM_SM-A226B_VD2_A226BXXSBDZC3_fac')
        self.assertTrue(done['ap'].endswith('/' + self.ap.name))
        self.assertEqual(len(done['files']), 5)
        self.assertIn('"phase":"Unpacking"', progress)
        # A second run keeps what is already unpacked from the same entries.
        code, again, progress = self.run_helper('unpack', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/' + bundle.name,
                                                '--confirm')
        self.assertEqual(code, 0, again)
        self.assertNotIn('"phase":"Unpacking"', progress)
        code, plan, _ = self.run_helper('inspect', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', done['ap'])
        self.assertEqual(code, 0, plan)
        self.assertEqual(([f['kind'] for f in plan['files']], plan['blockers']), (['AP', 'CSC', 'BL', 'CP'], []))
        code, installed, _ = self.run_helper('install', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', done['ap'],
                                             '--expected-plan', plan['plan_sha256'], '--confirm')
        self.assertEqual(code, 0, installed)
        self.assertEqual((self.root / 'targets/super').read_bytes()[4096:4100], b'gDla')
        # A tar damaged inside the ZIP stops at its CRC; nothing half-written is left.
        raw = bytearray(bundle.read_bytes())
        at = raw.find(b'PK\x03\x04', 10) + 200
        raw[at] ^= 0xff
        broken = self.root / 'usb/broken.zip'
        broken.write_bytes(bytes(raw))
        code, failed, _ = self.run_helper('unpack', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/broken.zip', '--confirm')
        self.assertEqual((code, failed['error']['code']), (2, 'FIRMWARE_DAMAGED'), failed)
        self.assertFalse(any(p.name.startswith('.part-') for p in (self.root / 'usb/broken').iterdir()))

    def test_an_ap_without_super_or_boot_is_blocked(self):
        samsung_tar(self.ap, [('boot.img.lz4', lz4(self.images['boot']))])
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['blockers'], ['AP_INCOMPLETE'])
        code, refused, _ = self.install(plan['plan_sha256'])
        self.assertEqual((code, refused['error']['code']), (2, 'PLAN_BLOCKED'))


if __name__ == '__main__':
    unittest.main()
