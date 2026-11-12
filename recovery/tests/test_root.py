"""Root from the recovery (root.py, 2026-11-10): the newest Magisk checked by its SHA-256, BOOT
patched, the stock BOOT kept on cache first, refused on a Linux BOOT. The patch itself is
Magisk's boot_patch.sh on the phone; here a stand-in marks the BOOT as Magisk does.
APatch (2026-11-11): KernelPatch's kptools and kpimg come with the recovery; here a stand-in
kptools walks the same steps on a BOOT the test prepared with KernelPatch's magic in its kernel."""
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import root as rt  # noqa: E402
from test_android_gsi import stock_boot  # noqa: E402
import test_bootimage as tb  # noqa: E402
from test_flash import boot_image  # noqa: E402

MIB = 1 << 20


class Root(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-root-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('targets', 'cache/a22x-recovery', 'root-downloads'):
            (self.root / name).mkdir(parents=True)
        self.boot(stock_boot())
        apk = self.root / 'root-downloads/Magisk-v31.1.apk'
        with zipfile.ZipFile(apk, 'w') as z:
            z.writestr('assets/boot_patch.sh', '#!/sbin/sh\n')
        data = apk.read_bytes()
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'},
            'targets': {'boot': {'path': 'targets/boot', 'size_bytes': 4 * MIB}},
            'root': {'magisk': {'version': 'v31.1', 'file': apk.name, 'url': 'https://github.com/topjohnwu/Magisk/releases/download/v31.1/' + apk.name,
                                'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}))
        self.sha = hashlib.sha256(data).hexdigest()
        props = {'ro.product.bootimage.device': 'a22x', 'ro.product.bootimage.model': 'SM-A226B'}
        patched = tb.boot(tb.kernel('4.14.186-27218697'), tb.ramdisk(props, {'.backup/.magisk': b'KEEPVERITY=true\n'}),
                          tb.dt_table(tb.fdt('MT6833', ['mediatek,MT6833'])))
        (self.root / 'root-downloads/patched-boot.img').write_bytes(patched)

    KPIMG = b'KP1158\0\0' + bytes([0, 9, 13, 0]) + bytes(116)

    def apatch(self, kallsyms=True):
        """KernelPatch's files as the recovery carries them: kpimg, and a stand-in kptools that takes
        the steps of the APatch app's boot_patch.sh and repacks to a BOOT with KernelPatch in its kernel."""
        folder = self.root / 'apatch'
        folder.mkdir(exist_ok=True)
        (folder / 'kpimg').write_bytes(self.KPIMG)
        image = bytearray(64 * 1024)
        image[56:60] = b'ARMd'
        banner = b'Linux version 4.14.186-27218697 (builder) #1 SMP\n'
        image[4096:4096 + len(banner)] = banner
        image[32768:32768 + len(self.KPIMG)] = self.KPIMG
        props = {'ro.product.bootimage.device': 'a22x', 'ro.product.bootimage.model': 'SM-A226B'}
        patched = tb.boot(gzip.compress(bytes(image), mtime=0), tb.ramdisk(props), tb.dt_table(tb.fdt('MT6833', ['mediatek,MT6833'])))
        (folder / 'patched.img').write_bytes(patched)
        (folder / 'kptools').write_text('#!/bin/sh\ncase "$1" in\n'
                                        'unpack) [ -f "$2" ] || exit 4; grep -q "this recovery stays" "$2" && exit 6; echo kernel > kernel;;\n'
                                        '-i) case "$3" in -f) %s;; -l) echo patched=false;; esac;;\n'
                                        '-p) [ "$2$4$6$8" = "-i-S-k-o" ] && [ -f "$3" ] && [ -f "$7" ] || exit 3; echo "patching with $5"; cat "$3" "$7" > "$9";;\n'
                                        'repack) [ -f kernel ] && [ -f kernel.ori ] || exit 5; cp %s new-boot.img;;\n'
                                        '*) exit 9;;\nesac\n' % ('echo CONFIG_KALLSYMS=y; echo CONFIG_KALLSYMS_ALL=y' if kallsyms else 'true',
                                                                 folder / 'patched.img'))
        (folder / 'kptools').chmod(0o755)
        return hashlib.sha256(self.KPIMG).hexdigest()

    def key_file(self, key):
        path = self.root / 'superkey'
        path.write_text(key + '\n')
        return str(path)

    def boot(self, image):
        data = bytearray(4 * MIB)
        data[:len(image)] = image
        (self.root / 'targets/boot').write_bytes(bytes(data))
        return bytes(data)

    def run_helper(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = rt.main(['--fixture', str(self.root), *args])
        return code, json.loads(stdout.getvalue().splitlines()[-1]), stderr.getvalue()

    def test_the_list_names_what_fits_this_kernel(self):
        code, listed, _ = self.run_helper('list')
        self.assertEqual(code, 0, listed)
        rows = {t['id']: t for t in listed['tools']}
        self.assertEqual(list(rows), ['magisk', 'apatch'])  # KernelSU and KernelSU Next do not install on the 4.14 kernel
        self.assertTrue(rows['magisk']['supported'])
        self.assertEqual(rows['magisk']['version'], 'v31.1')
        self.assertEqual((listed['boot']['kind'], listed['boot']['magisk'], listed['boot']['root']), ('android', False, ''))
        # APatch needs the tool and image this recovery carries: a build without them says so.
        self.assertEqual((rows['apatch']['supported'], rows['apatch']['reason']), (False, 'This build of the recovery has no APatch'))
        sha = self.apatch()
        row = {t['id']: t for t in self.run_helper('list')[1]['tools']}['apatch']
        self.assertEqual((row['supported'], row['version'], row['sha256'], row['needs_key'], row['bundled']),
                         (True, 'KernelPatch 0.13.9', sha, True, True))

    def test_apatch_goes_into_the_kernel_with_the_owners_superkey(self):
        """2026-11-11: APatch beside Magisk. The steps of its app's boot_patch.sh with the recovery's
        own kptools; the SuperKey comes in a file, is read once and reaches no log or result."""
        sha = self.apatch()
        stock = (self.root / 'targets/boot').read_bytes()
        for key in (None, 'short1A', 'lettersonly', '12345678', 'with space 1a'):
            args = ('--superkey-file', self.key_file(key)) if key else ()
            code, refused, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm', *args)
            self.assertEqual((code, refused['error']['code']), (2, 'SUPERKEY_INVALID'), key)
            self.assertEqual((self.root / 'targets/boot').read_bytes(), stock)
        code, refused, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', '0' * 64, '--confirm',
                                           '--superkey-file', self.key_file('Sup3rKey-2026'))
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_RELEASE_CHANGED'))
        path = self.key_file('Sup3rKey-2026')
        code, done, progress = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm', '--superkey-file', path)
        self.assertEqual(code, 0, done)
        self.assertFalse(os.path.exists(path))  # read once, then gone
        self.assertNotIn('Sup3rKey-2026', json.dumps(done) + progress)
        self.assertIn('patching with ***', done['log'])
        boot = (self.root / 'targets/boot').read_bytes()
        self.assertEqual(rt.saved_boot.describe_boot(boot)['root'], 'apatch')
        self.assertEqual((done['root'], done['name'], done['version'], done['apk']), ('apatch', 'APatch', 'KernelPatch 0.13.9', ''))
        self.assertTrue(done['boot'].startswith('/a22x-recovery/root/apatch-KernelPatch_0.13.9-'))
        self.assertEqual((self.root / 'cache' / done['kept_boot'].lstrip('/')).read_bytes(), stock)
        self.assertFalse((self.root / 'root-work').exists())
        listed = self.run_helper('list')[1]
        self.assertEqual((listed['boot']['root'], listed['boot']['magisk']), ('apatch', False))
        # One BOOT carries one root: Magisk is refused on a BOOT with APatch, and the other way round.
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual((code, refused['error']['code'], refused['error']['details']['present']), (2, 'ROOT_OTHER_PRESENT', 'apatch'))
        self.boot(stock_boot())
        self.assertEqual(self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')[0], 0)
        code, refused, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm',
                                           '--superkey-file', self.key_file('Sup3rKey-2026'))
        self.assertEqual((code, refused['error']['code'], refused['error']['details']['present']), (2, 'ROOT_OTHER_PRESENT', 'magisk'))

    def test_the_mark_that_keeps_this_recovery_is_not_patched_into_the_image(self):
        """2026-11-11, on the phone: the stock BOOT carries this recovery's mark in its tail; with it
        kptools repacked a BOOT larger than the partition. The patch gets the image without the mark."""
        from test_keep_recovery import signed
        sha = self.apatch()
        stock = bytearray(signed(stock_boot()))
        spot = len(stock) - rt.saved_boot.KEEP_RECOVERY_FROM_END
        stock[spot:spot + len(rt.saved_boot.KEEP_RECOVERY_MARK)] = rt.saved_boot.KEEP_RECOVERY_MARK
        (self.root / 'targets/boot').write_bytes(bytes(stock))
        self.assertEqual(rt.saved_boot.keep_recovery_spot(bytes(stock)), (spot, True))
        code, done, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm',
                                        '--superkey-file', self.key_file('Sup3rKey-2026'))
        self.assertEqual(code, 0, done)
        self.assertEqual(rt.saved_boot.describe_boot((self.root / 'targets/boot').read_bytes())['root'], 'apatch')

    def test_apatch_stops_before_writing_when_the_kernel_or_the_patch_does_not_fit(self):
        sha = self.apatch(kallsyms=False)
        stock = (self.root / 'targets/boot').read_bytes()
        code, refused, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm',
                                           '--superkey-file', self.key_file('Sup3rKey-2026'))
        self.assertEqual((code, refused['error']['code']), (2, 'APATCH_KERNEL_UNSUITABLE'))
        self.assertEqual((self.root / 'targets/boot').read_bytes(), stock)
        # A repack that is not a BOOT of this phone with KernelPatch in it is not flashed.
        self.apatch()
        (self.root / 'apatch/patched.img').write_bytes(stock_boot())
        code, refused, _ = self.run_helper('install', '--id', 'apatch', '--expected-sha256', sha, '--confirm',
                                           '--superkey-file', self.key_file('Sup3rKey-2026'))
        self.assertEqual((code, refused['error']['code']), (2, 'APATCH_PATCH_FAILED'))
        self.assertEqual((self.root / 'targets/boot').read_bytes(), stock)
        self.assertFalse((self.root / 'root-work').exists())

    def test_magisk_goes_into_boot_and_the_stock_one_stays_on_cache(self):
        stock = (self.root / 'targets/boot').read_bytes()
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', '0' * 64, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_RELEASE_CHANGED'))
        code, done, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual(code, 0, done)
        boot = (self.root / 'targets/boot').read_bytes()
        self.assertEqual(rt.saved_boot.describe_boot(boot)['root'], 'magisk')
        self.assertTrue(done['boot'].startswith('/a22x-recovery/root/magisk-v31.1-'))
        kept = self.root / 'cache' / done['kept_boot'].lstrip('/')
        self.assertEqual(kept.read_bytes(), stock)
        code, listed, _ = self.run_helper('list')
        self.assertTrue(listed['boot']['magisk'])
        code, refused, _ = self.run_helper('install', '--id', 'kernelsu', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_UNKNOWN'))

    def test_with_verification_on_root_turns_it_off_and_resets_android_data_once(self):
        """2026-11-10 on the phone: stock vbmeta + a patched BOOT = Android cannot mount its data
        (misc: --prompt_and_wipe_data). As the Magisk app does for Samsung: vbmeta flags 3, data erased
        once. LK write-protects vbmeta while the phone runs, so it is prepared here for Download mode."""
        import struct
        from test_flash import vbmeta
        config = json.loads((self.root / 'install-fixture.json').read_text())
        config['targets']['vbmeta'] = {'path': 'targets/vbmeta', 'size_bytes': 1 << 16}
        config['write_protected'] = ['dtbo', 'vbmeta']
        (self.root / 'install-fixture.json').write_text(json.dumps(config))
        stock = vbmeta(b'samsung') + bytes((1 << 16) - 327)
        (self.root / 'targets/vbmeta').write_bytes(stock)
        misc = bytearray(4096)
        misc[:13] = b'boot-recovery'
        misc[64:64 + 64] = b'recovery\n--prompt_and_wipe_data\n--reason=fs_mgr_mount_all:M02R\n'.ljust(64, b'\0')
        (self.root / 'misc').write_bytes(bytes(misc))
        (self.root / 'data').mkdir()
        volumes = json.loads((self.root / 'filesystem-fixture.json').read_text())
        volumes['volumes'].append({'id': 'userdata', 'path': 'data', 'fs_type': 'f2fs', 'mounted': False, 'read_only': True})
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(volumes))
        listed = self.run_helper('list')[1]
        self.assertEqual((listed['verification'], listed['needs_data_reset']), (True, False))
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm', '--erase-data')
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_NEEDS_VBMETA'))
        code, prepared, _ = self.run_helper('prepare-vbmeta')
        self.assertEqual(code, 0, prepared)
        made = (self.root / 'cache' / prepared['vbmeta'].lstrip('/')).read_bytes()
        self.assertEqual(struct.unpack_from('>I', made, 120)[0], 3)
        self.assertEqual(made[:120] + made[124:], stock[:120] + stock[124:])
        self.assertEqual(prepared['command'], 'heimdall flash --vbmeta ' + prepared['vbmeta'].rsplit('/', 1)[-1])
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes(), stock)  # locked here: nothing written
        (self.root / 'targets/vbmeta').write_bytes(made)  # flashed in Download mode
        listed = self.run_helper('list')[1]
        self.assertEqual((listed['verification'], listed['needs_data_reset']), (False, True))
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_NEEDS_DATA_RESET'))
        code, done, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm', '--erase-data')
        self.assertEqual(code, 0, done)
        self.assertTrue((self.root / 'android-reset.json').exists())
        self.assertTrue(done['data']['request_cleared'])
        self.assertFalse(any((self.root / 'misc').read_bytes()[:2048]))
        self.assertFalse((self.root / 'cache' / prepared['vbmeta'].lstrip('/')).exists())
        listed = self.run_helper('list')[1]
        self.assertEqual((listed['verification'], listed['needs_data_reset']), (False, False))  # later updates: no reset

    def test_a_linux_boot_is_refused_and_a_changed_download_stops(self):
        self.boot(boot_image())
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'ROOT_NEEDS_ANDROID_BOOT'))
        self.boot(stock_boot())
        (self.root / 'root-downloads/Magisk-v31.1.apk').write_bytes(b'PK tampered')
        code, refused, _ = self.run_helper('install', '--id', 'magisk', '--expected-sha256', self.sha, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'DOWNLOAD_CHECKSUM_MISMATCH'))


if __name__ == '__main__':
    unittest.main()
