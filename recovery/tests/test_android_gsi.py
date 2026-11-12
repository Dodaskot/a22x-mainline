"""A GSI installed from the recovery (android_gsi.py, 2026-11-10): system.img from Google's GSI ZIP
into super/system, its vbmeta.img into vbmeta, userdata reset for Android; refused on a Linux BOOT."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import android_gsi as ag  # noqa: E402
import test_bootimage as tb  # noqa: E402
import test_lp_metadata as tl  # noqa: E402
from test_flash import boot_image, vbmeta  # noqa: E402

MIB = 1 << 20


def stock_boot():
    props = {'ro.product.bootimage.device': 'a22x', 'ro.product.bootimage.model': 'SM-A226B'}
    return tb.boot(tb.kernel('4.14.186-27218697'), tb.ramdisk(props), tb.dt_table(tb.fdt('MT6833', ['mediatek,MT6833'])))


class Gsi(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-gsi-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('targets', 'cache/a22x-recovery', 'data/.a22x-images'):
            (self.root / name).mkdir(parents=True)
        tl.make_super(self.root / 'targets/super')
        boot = bytearray(4 * MIB)
        image = stock_boot()
        boot[:len(image)] = image
        (self.root / 'targets/boot').write_bytes(bytes(boot))
        (self.root / 'targets/vbmeta').write_bytes(vbmeta(b'stock') + bytes((1 << 16) - 325))
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'},
            'targets': {'super': {'path': 'targets/super', 'size_bytes': 64 * MIB}, 'boot': {'path': 'targets/boot', 'size_bytes': 4 * MIB},
                        'vbmeta': {'path': 'targets/vbmeta', 'size_bytes': 1 << 16}}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True},
            {'id': 'userdata', 'path': 'data', 'fs_type': 'ext4', 'mounted': True, 'read_only': False}]}))
        (self.root / 'cache/old.log').write_text('old')
        self.system = bytearray(os.urandom(5 * MIB))
        struct.pack_into('<H', self.system, 1080, 0xEF53)
        self.vbmeta = bytearray(vbmeta(b'g' * 700))
        struct.pack_into('>I', self.vbmeta, 120, 2)  # verification disabled, as Google ships it
        self.gsi = self.root / 'data/.a22x-images/aosp_arm64-exp-TQ3A.zip'
        with zipfile.ZipFile(self.gsi, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('system.img', bytes(self.system))
            z.writestr('vbmeta.img', bytes(self.vbmeta))
            z.writestr('build.prop', 'ro.build.version.release=13\nro.build.version.sdk=33\nro.build.id=TQ3A.230805.001.A3\n'
                                     'ro.build.version.security_patch=2023-08-05\n')

    def run_helper(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = ag.main(['--fixture', str(self.root), *args])
        return code, json.loads(stdout.getvalue().splitlines()[-1]), stderr.getvalue()

    def token(self):
        import filesystem as fs
        return fs.FixtureContext(self.root).volume('userdata')['volume_token']

    def inspect(self):
        return self.run_helper('inspect', '--volume', 'userdata', '--volume-token', self.token(), '--path', '/.a22x-images/' + self.gsi.name)

    def install(self, plan):
        return self.run_helper('install', '--volume', 'userdata', '--volume-token', self.token(), '--path',
                               '/.a22x-images/' + self.gsi.name, '--expected-plan', plan, '--confirm')

    def test_a_google_gsi_goes_into_system_and_vbmeta(self):
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan['android'], plan['sdk'], plan['fits'], plan['boot'], plan['blockers']), ('13', 33, True, 'android', []))
        self.assertEqual([(w['target'], w['member']) for w in plan['writes']], [('super/system', 'system.img'), ('vbmeta', 'vbmeta.img')])
        code, done, _ = self.install(plan['plan_sha256'])
        self.assertEqual(code, 0, done)
        import lp_metadata as lp
        fd = os.open(self.root / 'targets/super', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        written = b''.join(lp.Super(fd).chunks('system'))
        self.assertEqual(written[:len(self.system)], bytes(self.system))
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes()[:len(self.vbmeta)], bytes(self.vbmeta))
        self.assertTrue((self.root / 'android-reset.json').exists())
        self.assertFalse((self.root / 'cache/old.log').exists())
        self.assertTrue((self.root / 'cache/a22x-recovery').is_dir())

    def test_a_linux_boot_asks_for_stock_android_first(self):
        boot = bytearray(4 * MIB)
        image = boot_image()
        boot[:len(image)] = image
        (self.root / 'targets/boot').write_bytes(bytes(boot))
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan['boot'], plan['blockers']), ('linux', ['STOCK_ANDROID_FIRST']))
        code, refused, _ = self.install(plan['plan_sha256'])
        self.assertEqual((code, refused['error']['code']), (2, 'PLAN_BLOCKED'))
        self.assertFalse((self.root / 'android-reset.json').exists())

    def test_over_linux_the_stock_boot_kept_on_cache_goes_on_boot(self):
        """2026-11-10: with Linux on BOOT, Samsung's BOOT kept on cache (taken out of the firmware, or
        saved by the app) is written too, last; the Linux BOOT is kept on cache first."""
        import hashlib
        linux = bytearray(4 * MIB)
        image = boot_image()
        linux[:len(image)] = image
        (self.root / 'targets/boot').write_bytes(bytes(linux))
        stock = bytearray(4 * MIB)
        image = stock_boot()
        stock[:len(image)] = image
        folder = self.root / 'cache/a22x-recovery/saved-boot'
        folder.mkdir(parents=True)
        name = 'stock-A226BXXSBDZC3-' + hashlib.sha256(bytes(stock)).hexdigest()[:12] + '.img'
        (folder / name).write_bytes(bytes(stock))
        (folder / (name + '.json')).write_text(json.dumps({'schema': 'a22x.saved-boot.v1', 'kind': 'android', 'release': '4.14.186-27218697',
                                                          'build': 'A226BXXSBDZC3', 'sha256': hashlib.sha256(bytes(stock)).hexdigest(),
                                                          'bytes': len(stock), 'saved': 1}))
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual((plan['boot'], plan['blockers']), ('linux', []))
        self.assertEqual([w['target'] for w in plan['writes']], ['super/system', 'vbmeta', 'boot'])
        code, done, _ = self.install(plan['plan_sha256'])
        self.assertEqual(code, 0, done)
        self.assertEqual((self.root / 'targets/boot').read_bytes(), bytes(stock))
        kept = [p.name for p in folder.iterdir() if p.name.startswith('linux-') and p.name.endswith('.img')]
        self.assertEqual(len(kept), 1)
        self.assertEqual((folder / kept[0]).read_bytes(), bytes(linux))

    @unittest.skipUnless(shutil.which('7z'), '7-Zip makes the archive')
    def test_a_lineageos_7z_without_vbmeta_gets_verification_disabled(self):
        """2026-11-10: LineageOS ships its GSI as one image in a .7z, without vbmeta.img: the image is
        decoded from the 7z, and an empty vbmeta with verification disabled is written first."""
        import subprocess
        work = self.root / 'src'
        work.mkdir()
        (work / 'LineageOS-23.2-20260524-VANILLA-EXT4-GSI.img').write_bytes(bytes(self.system))
        archive = self.root / 'data/.a22x-images/LineageOS-23.2-20260524-VANILLA-EXT4-GSI.7z'
        subprocess.run(['7z', 'a', '-t7z', '-m0=lzma2', str(archive), 'LineageOS-23.2-20260524-VANILLA-EXT4-GSI.img'], cwd=work,
                       check=True, stdout=subprocess.DEVNULL)
        self.gsi.unlink()
        self.gsi = archive
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['blockers'], [])
        self.assertEqual([(w['target'], w.get('generate')) for w in plan['writes']], [('super/system', None), ('vbmeta', 'vbmeta-disabled')])
        code, done, _ = self.install(plan['plan_sha256'])
        self.assertEqual(code, 0, done)
        vb = (self.root / 'targets/vbmeta').read_bytes()
        self.assertEqual((vb[:4], struct.unpack('>I', vb[120:124])[0]), (b'AVB0', 2))
        import lp_metadata as lp
        fd = os.open(self.root / 'targets/super', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.assertEqual(b''.join(lp.Super(fd).chunks('system'))[:len(self.system)], bytes(self.system))

    def test_android_15_and_newer_is_warned_about_this_phones_kernel(self):
        """2026-11-12, on the owner's phone: Google's Android 15 restarted for ever ("Android V requires
        kernel 4.19"; the phone's Android kernel is 4.14), and the review had said nothing. It is a
        warning, not a refusal: ROMs made for older phones start."""
        for sdk, warned in (('34', False), ('35', True), ('36', True)):
            with zipfile.ZipFile(self.gsi, 'w') as z:
                z.writestr('system.img', bytes(self.system))
                z.writestr('vbmeta.img', b'AVB0' + bytes(252))
                z.writestr('build.prop', 'ro.build.version.release=x\nro.build.version.sdk=%s\n' % sdk)
            code, plan, _ = self.inspect()
            self.assertEqual(code, 0, plan)
            self.assertEqual(ag.OLD_KERNEL_WARNING in plan['warnings'], warned, sdk)
            self.assertNotIn('KERNEL', ' '.join(plan['blockers']))
        self.assertLess(len(ag.OLD_KERNEL_WARNING.encode()), 200)   # the interface reads a warning into 200 bytes
        self.assertTrue(ag.kernel_older('4.14.186-27218697', (4, 19)) and ag.kernel_older('', (4, 19)))
        self.assertFalse(ag.kernel_older('4.19.191-1', (4, 19)) or ag.kernel_older('5.10.209', (4, 19)))

    def test_newer_android_warns_and_an_image_without_vbmeta_needs_it_disabled(self):
        with zipfile.ZipFile(self.gsi, 'w') as z:
            z.writestr('system.img', bytes(self.system))
            z.writestr('build.prop', 'ro.build.version.release=16\nro.build.version.sdk=36\n')
        code, plan, _ = self.inspect()
        self.assertEqual(code, 0, plan)
        self.assertFalse(plan['fits'])
        self.assertIn('Made for newer vendors: it may not start on this phone', plan['warnings'])
        self.assertEqual(plan['blockers'], [])
        self.assertEqual([w['target'] for w in plan['writes']], ['super/system', 'vbmeta'])  # an empty one, verification off
        disabled = bytearray((self.root / 'targets/vbmeta').read_bytes())
        struct.pack_into('>I', disabled, 120, 2)
        (self.root / 'targets/vbmeta').write_bytes(bytes(disabled))
        code, plan, _ = self.inspect()
        self.assertEqual(plan['blockers'], [])
        self.assertEqual([w['target'] for w in plan['writes']], ['super/system'])


if __name__ == '__main__':
    unittest.main()


class RomKind(unittest.TestCase):
    """2026-11-11: a ROM chosen from a file on a card opens the review that installs it."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-romkind-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / 'usb').mkdir()
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'}, 'targets': {}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}]}))

    def kind(self, name):
        import filesystem as fs
        token = fs.FixtureContext(self.root).volume('usb-p1')['volume_token']
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = ag.main(['--fixture', str(self.root), 'classify', '--volume', 'usb-p1', '--volume-token', token, '--path', '/' + name])
        result = json.loads(stdout.getvalue().splitlines()[-1])
        return result['kind'] if code == 0 else result['error']['code']

    def zipped(self, name, members):
        with zipfile.ZipFile(self.root / 'usb' / name, 'w', zipfile.ZIP_DEFLATED) as z:
            for member, data in members.items():
                z.writestr(member, data)
        return name

    def test_each_kind_of_file_is_told_apart(self):
        import gzip
        import lzma
        system = bytearray(2 * MIB)
        struct.pack_into('<H', system, 1080, 0xEF53)
        erofs = bytearray(2 * MIB)
        struct.pack_into('<I', erofs, 1024, 0xE0F5E1E2)
        self.assertEqual(self.kind(self.zipped('lineage-a22x.zip', {
            'META-INF/com/google/android/update-binary': b'\x7fELF', 'META-INF/com/google/android/updater-script': b'', 'system.new.dat.br': b'x'})), 'installer')
        self.assertEqual(self.kind(self.zipped('Magisk-v31.1.zip', {'META-INF/com/google/android/update-binary': b'#!/sbin/sh\n'})), 'installer')
        self.assertEqual(self.kind(self.zipped('aosp_arm64-exp.zip', {'system.img': bytes(system), 'vbmeta.img': b'AVB0'})), 'gsi')
        self.assertEqual(self.kind(self.zipped('SM-A226B_firmware.zip', {'AP_A226BXXSBDYA1.tar.md5': b'x', 'BL_A226B.tar.md5': b'y'})), 'samsung')
        self.assertEqual(self.kind(self.zipped('ota-ab.zip', {'payload.bin': b'CrAU', 'payload_properties.txt': b''})), 'payload')
        self.assertEqual(self.kind(self.zipped('photos.zip', {'a.jpg': b'x', 'vbmeta.img': b'AVB0'})), 'unknown')
        (self.root / 'usb' / 'system.img').write_bytes(bytes(system))
        (self.root / 'usb' / 'lineage-gsi.img.xz').write_bytes(lzma.compress(bytes(erofs)))
        (self.root / 'usb' / 'lineage-gsi.img.gz').write_bytes(gzip.compress(bytes(system)))
        (self.root / 'usb' / 'notes.txt').write_text('no image at all\n' * 4000)
        (self.root / 'usb' / 'movie.gz').write_bytes(gzip.compress(os.urandom(1 << 16)))
        self.assertEqual([self.kind(n) for n in ('system.img', 'lineage-gsi.img.xz', 'lineage-gsi.img.gz')], ['gsi', 'gsi', 'gsi'])
        self.assertEqual([self.kind(n) for n in ('notes.txt', 'movie.gz')], ['unknown', 'unknown'])
        tar = bytearray(1024)
        tar[257:262] = b'ustar'
        (self.root / 'usb' / 'AP_A226B.tar.md5').write_bytes(bytes(tar))
        self.assertEqual(self.kind('AP_A226B.tar.md5'), 'samsung')
        self.assertEqual(self.kind('missing.img'), 'IO_ERROR')  # no such file: the page says so
