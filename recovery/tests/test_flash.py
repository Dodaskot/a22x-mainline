"""Flash and settings helpers on fixture files; no block device is ever opened."""
import contextlib
import gzip
import hashlib
import importlib
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import flash

SIZES = {'boot': 1 << 20, 'dtbo': 1 << 18, 'vbmeta': 1 << 16, 'vbmeta_vendor': 1 << 16, 'super': 1 << 21, 'userdata': 1 << 22}


def boot_image(size=8192, *, dtbo=0, board=b'SRPTL28A011'):
    """A small but complete A22x boot (or, with DTBO, recovery) image: gzip kernel with its device
    tree, gzip ramdisk and a DT table, so it passes the content check of bootimage.py."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_bootimage as tb
    kernel, ramdisk, dtb = tb.kernel(appended=tb.MAINLINE_TREE), tb.ramdisk(), tb.dt_table(tb.MAINLINE_TREE)
    pad = lambda blob: blob + bytes(-len(blob) % 2048)
    header = bytearray(2048)
    header[:8] = b'ANDROID!'
    struct.pack_into('<10I', header, 8, len(kernel), 0x40080000, len(ramdisk), 0x51100000, 0, 0, 0x47c80000, 2048, 2, 0)
    header[48:48 + len(board)] = board
    body = pad(kernel) + pad(ramdisk)
    struct.pack_into('<IQI', header, 1632, dtbo, 2048 + len(body) if dtbo else 0, 1660)
    struct.pack_into('<IQ', header, 1648, len(dtb), 0x47c80000)
    return bytes(header) + body + (pad(b'o' * dtbo) if dtbo else b'') + dtb


def vbmeta(tail=b''):
    """A vbmeta header without signature or descriptors, followed by TAIL."""
    header = bytearray(256)
    header[:4] = b'AVB0'
    struct.pack_into('>IIQQIQQQQQQQQQQQII', header, 4, 1, 0, 0, 64, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return bytes(header) + bytes(64) + tail


def sparse(payload):
    """Android sparse with one RAW chunk and one FILL chunk of 4 KiB blocks."""
    blocks = len(payload) // 4096
    head = struct.pack('<IHHHHIIII', 0xed26ff3a, 1, 0, 28, 12, 4096, blocks + 1, 2, 0)
    raw = struct.pack('<HHII', 0xcac1, 0, blocks, 12 + len(payload)) + payload
    return head + raw + struct.pack('<HHII', 0xcac2, 0, 1, 16) + b'\xaa\xaa\xaa\xaa'


class Flash(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-flash-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('usb', 'sd', 'targets'):
            (self.root / name).mkdir()
        targets = {}
        for label, size in SIZES.items():
            (self.root / 'targets' / label).write_bytes(b'\x11' * size)
            targets[label] = {'path': 'targets/' + label, 'size_bytes': size}
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'}, 'targets': targets}))
        self.volumes([{'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True},
                       {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': False}])

    def volumes(self, rows):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': rows}))

    def run_helper(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = flash.main(['--fixture', str(self.root), *args])
        return code, json.loads(stdout.getvalue().splitlines()[-1]), stderr.getvalue()

    def token(self, volume='usb-p1'):
        import filesystem as fs
        return fs.FixtureContext(self.root).volume(volume)['volume_token']

    def image(self, data, name='image.img', volume='usb'):
        (self.root / volume / name).write_bytes(data)
        return '/' + name

    def check(self, target, path, volume='usb-p1'):
        return self.run_helper('check', '--target', target, '--volume', volume, '--volume-token', self.token(volume), '--path', path)

    def write(self, target, path, result, *extra, volume='usb-p1', confirm=True):
        return self.run_helper('write', '--target', target, '--volume', volume, '--volume-token', self.token(volume), '--path', path,
                               '--expected-sha256', result['image']['sha256'], '--expected-identity', result['image']['identity'],
                               *(('--confirm',) if confirm else ()), *extra)

    def test_targets_are_what_the_phone_has(self):
        code, result, _ = self.run_helper('targets')
        labels = [row['label'] for row in result['targets']]
        self.assertEqual(code, 0)
        self.assertEqual(labels, ['boot', 'dtbo', 'vbmeta', 'vbmeta_vendor', 'super'])
        # 2026-11-08: the recovery may replace its own partition, as the user asked.
        self.assertIn('recovery', flash.LABELS)

    def test_recovery_takes_only_a_recovery_image(self):
        fixture = json.loads((self.root / 'install-fixture.json').read_text())
        (self.root / 'targets' / 'recovery').write_bytes(b'\x11' * (1 << 20))
        fixture['targets']['recovery'] = {'path': 'targets/recovery', 'size_bytes': 1 << 20}
        (self.root / 'install-fixture.json').write_text(json.dumps(fixture))
        self.assertIn('recovery', [row['label'] for row in self.run_helper('targets')[1]['targets']])
        code, result, _ = self.check('recovery', self.image(boot_image(), 'boot.img'))
        self.assertEqual((code, result['error']['code']), (2, 'WRONG_IMAGE_TYPE'))
        code, checked, _ = self.check('recovery', self.image(boot_image(dtbo=2048), 'recovery.img'))
        self.assertEqual((code, checked['image']['kind']), (0, 'A22x recovery image'))
        self.assertTrue(any('Download Mode' in note for note in checked['warnings']))
        code, written, _ = self.write('recovery', '/recovery.img', checked)
        self.assertEqual(code, 0, written)
        self.assertEqual((self.root / 'targets' / 'recovery').read_bytes()[:8], b'ANDROID!')

    def ext_image(self, size=1 << 22):
        """A filesystem image as dd saves a partition: a superblock, some data, mostly zeros."""
        data = bytearray(size)
        struct.pack_into('<H', data, 1080, 0xEF53)
        data[size // 2:size // 2 + 65536] = os.urandom(65536)
        return bytes(data)

    def test_restore_finds_the_partition_from_the_image(self):
        for data, label in ((boot_image(), 'boot'), (self.ext_image(), 'userdata')):
            code, result, _ = self.check('auto', self.image(data, name=label + '.img'))
            self.assertEqual((code, result['target']['label']), (0, label), result)
            self.assertTrue(result['target']['offline'])
        code, result, _ = self.check('auto', self.image(b'\x42' * 9000, name='noise.img'))
        self.assertEqual((code, result['error']['code']), (2, 'UNKNOWN_IMAGE_TARGET'))
        # The restore list never offers what Flash a partition leaves out.
        self.assertNotIn('userdata', [row['label'] for row in self.run_helper('targets')[1]['targets']])

    def test_compressed_partition_images_restore_userdata(self):
        raw = self.ext_image()
        zst = self.root / 'usb' / 'userdata.img'
        zst.write_bytes(raw)
        subprocess.run([flash.ZSTD, '-q', '--rm', str(zst)], check=True)
        (self.root / 'usb' / 'userdata.img.xz').write_bytes(lzma.compress(raw))
        (self.root / 'usb' / 'userdata.img.gz').write_bytes(gzip.compress(raw))
        for name in ('userdata.img.zst', 'userdata.img.xz', 'userdata.img.gz'):
            (self.root / 'targets/userdata').write_bytes(b'\x11' * SIZES['userdata'])
            code, checked, _ = self.check('auto', '/' + name)
            self.assertEqual(code, 0, (name, checked))
            self.assertEqual((checked['target']['label'], checked['image']['kind']), ('userdata', 'ext4 filesystem image'))
            self.assertIn('Everything on userdata is replaced by this image', checked['warnings'])
            self.assertEqual(checked['image']['sha256'], hashlib.sha256(raw).hexdigest())
            code, done, _ = self.write('userdata', '/' + name, checked)
            self.assertEqual(code, 0, (name, done))
            self.assertEqual((self.root / 'targets/userdata').read_bytes(), raw)

    def test_a_damaged_copy_is_caught_by_its_saved_checksum(self):
        raw = self.ext_image()
        path = self.root / 'usb' / 'userdata.img'
        path.write_bytes(raw)
        subprocess.run([flash.ZSTD, '-q', '--rm', str(path)], check=True)
        record = self.root / 'usb' / 'userdata.img.zst.transport.json'
        record.write_text(json.dumps({'decoded_bytes': len(raw), 'decoded_sha256': hashlib.sha256(raw).hexdigest()}))
        code, checked, _ = self.check('auto', '/userdata.img.zst')
        self.assertEqual((code, checked['image']['checksum_file']), (0, 'userdata.img.zst.transport.json'))
        record.write_text(json.dumps({'decoded_bytes': len(raw), 'decoded_sha256': '0' * 64}))
        code, checked, _ = self.check('auto', '/userdata.img.zst')
        self.assertEqual((code, checked['error']['code']), (2, 'IMAGE_DAMAGED'))
        # A plain image is compared with the manifest kept beside it.
        image = boot_image() + bytes(SIZES['boot'] - len(boot_image()))
        (self.root / 'usb' / 'arch-boot.img').write_bytes(image)
        (self.root / 'usb' / 'manifest.json').write_text(json.dumps({'arch-boot.img': {'sha256': hashlib.sha256(image).hexdigest()}}))
        code, checked, _ = self.check('auto', '/arch-boot.img')
        self.assertEqual((code, checked['target']['label'], checked['image']['checksum_file']), (0, 'boot', 'manifest.json'))

    def test_mounted_userdata_is_reported_and_not_written(self):
        config = json.loads((self.root / 'install-fixture.json').read_text())
        config['mounted_targets'] = ['userdata']
        (self.root / 'install-fixture.json').write_text(json.dumps(config))
        path = self.image(self.ext_image(), name='userdata.img')
        code, checked, _ = self.check('auto', path)
        self.assertEqual(code, 0, checked)
        self.assertFalse(checked['target']['offline'])
        before = (self.root / 'targets/userdata').read_bytes()
        code, done, _ = self.write('userdata', path, checked)
        self.assertEqual((code, done['error']['code']), (2, 'PARTITION_MOUNTED'))
        self.assertEqual((self.root / 'targets/userdata').read_bytes(), before)

    def test_wrong_files_are_refused_and_nothing_is_written(self):
        before = (self.root / 'targets/boot').read_bytes()
        cases = (('boot', b'not an image' * 100), ('dtbo', b'not an image'), ('vbmeta', b'not an image'), ('super', bytes(8192)))
        for target, data in cases:
            code, result, _ = self.check(target, self.image(data))
            self.assertEqual((code, result['error']['code']), (2, 'WRONG_IMAGE_TYPE'), target)
        code, result, _ = self.check('boot', self.image(boot_image(dtbo=2048)))
        self.assertEqual(result['error']['code'], 'WRONG_IMAGE_TYPE')
        self.assertIn('recovery image', result['error']['message'])
        self.assertEqual((self.root / 'targets/boot').read_bytes(), before)

    def test_image_larger_than_the_partition_is_refused(self):
        code, result, _ = self.check('vbmeta', self.image(vbmeta(bytes(SIZES['vbmeta']))))
        self.assertEqual((code, result['error']['code']), (2, 'IMAGE_TOO_LARGE'))

    def test_flash_from_a_read_only_stick_saves_the_old_content_and_verifies(self):
        data = vbmeta(b'v' * 1000)
        path = self.image(data)
        code, checked, progress = self.check('vbmeta', path)
        self.assertEqual(code, 0, checked)
        self.assertEqual(checked['image']['kind'], 'Verified boot data')
        self.assertEqual(checked['rollback_volume'], 'sdcard-p1')
        self.assertFalse(checked['identical'])
        self.assertIn('Checking the image', progress)
        code, done, progress = self.write('vbmeta', path, checked)
        self.assertEqual(code, 0, done)
        self.assertTrue(done['verified'])
        self.assertEqual(done['sha256'], checked['image']['sha256'])
        # The verified write is noted for the normal-boot check (2026-11-10).
        self.assertTrue(done['recorded'])
        records = json.loads((self.root / '.flash-records.json').read_text())
        self.assertEqual(records['written']['vbmeta']['sha256'], done['sha256'])
        target = (self.root / 'targets/vbmeta').read_bytes()
        self.assertEqual(target[:len(data)], data)
        self.assertEqual(target[len(data):], bytes(SIZES['vbmeta'] - len(data)))
        saved = self.root / 'sd' / done['rollback']['path'].lstrip('/')
        self.assertEqual(saved.read_bytes(), b'\x11' * SIZES['vbmeta'])
        self.assertIn('Reading the partition back', progress)
        code, again, _ = self.check('vbmeta', path)
        self.assertTrue(again['identical'])

    def test_old_content_on_userdata_goes_into_its_image_store(self):
        # Linux systems live at the top of userdata: a saved partition must not land inside one.
        self.assertEqual(flash.rollback_folders({'id': 'mmcblk0p54', 'label': 'userdata'}), ('.a22x-images', flash.ROLLBACK_FOLDER))
        self.assertEqual(flash.rollback_folders({'id': 'sdcard-p1', 'label': 'alarm_root'}), (flash.ROLLBACK_FOLDER,))

    def test_write_needs_confirmation_and_the_checked_file(self):
        path = self.image(vbmeta(b'v' * 100))
        _, checked, _ = self.check('vbmeta', path)
        before = (self.root / 'targets/vbmeta').read_bytes()
        code, result, _ = self.write('vbmeta', path, checked, confirm=False)
        self.assertEqual((code, result['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        self.image(vbmeta(b'w' * 100))
        code, result, _ = self.write('vbmeta', path, checked)
        self.assertEqual((code, result['error']['code']), (2, 'IMAGE_CHANGED'))
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes(), before)

    def test_pending_installation_blocks_a_flash(self):
        path = self.image(vbmeta(b'v' * 100))
        _, checked, _ = self.check('vbmeta', path)
        (self.root / '.install-transaction.json').write_text('{}')
        before = (self.root / 'targets/vbmeta').read_bytes()
        code, result, _ = self.write('vbmeta', path, checked)
        self.assertEqual((code, result['error']['code']), (2, 'INSTALL_PENDING'))
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes(), before)

    def test_no_writable_volume_is_reported_before_the_flash(self):
        self.volumes([{'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}])
        code, checked, _ = self.check('vbmeta', self.image(vbmeta(b'v' * 100)))
        self.assertEqual(code, 0)
        self.assertIsNone(checked['rollback_volume'])
        self.assertIn('The current partition will not be saved first', checked['warnings'])

    def test_compressed_and_sparse_images(self):
        data = vbmeta(b'g' * 5000)
        for name, encoded in (('a.img.gz', gzip.compress(data)),) + ((('a.img.lz4', subprocess.run(
                ['lz4', '-c'], input=data, stdout=subprocess.PIPE, check=True).stdout),) if shutil.which('lz4') else ()):
            path = self.image(encoded, name)
            code, checked, _ = self.check('vbmeta', path)
            self.assertEqual(code, 0, checked)
            self.assertEqual(self.write('vbmeta', path, checked)[0], 0)
            self.assertEqual((self.root / 'targets/vbmeta').read_bytes()[:len(data)], data)
        payload = bytearray(8192)
        payload[4096:4100] = b'gDla'
        path = self.image(sparse(bytes(payload)), 'super.img')
        code, checked, _ = self.check('super', path)
        self.assertEqual(code, 0, checked)
        self.assertEqual(checked['image']['format'], 'sparse')
        self.assertIn('This partition is too large to save first', checked['warnings'] + ['This partition is too large to save first'])
        code, done, _ = self.write('super', path, checked)
        self.assertEqual(code, 0, done)
        written = (self.root / 'targets/super').read_bytes()
        self.assertEqual(written[:8192], bytes(payload))
        self.assertEqual(written[8192:12288], b'\xaa' * 4096)
        self.assertEqual(written[12288:], bytes(SIZES['super'] - 12288))

    def test_unusual_boot_header_warns_but_a_user_may_continue(self):
        code, checked, _ = self.check('boot', self.image(boot_image(board=b'OTHERBOARD')))
        self.assertEqual(code, 0, checked)
        self.assertIn('The header differs from the usual A22x boot layout', checked['warnings'])


class Settings(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-settings-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        os.environ['A22X_SETTINGS_RAM'] = str(self.root / 'ram.json')
        sys.modules.pop('settings', None)
        self.settings = importlib.import_module('settings')
        self.real_read_disk = self.settings.read_disk
        self.disk = {}
        self.writable = True
        patcher = mock.patch.multiple(self.settings, read_disk=lambda: dict(self.disk), write_disk=self.save)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, record):
        if self.writable:
            self.disk = dict(record)
        return self.writable

    def call(self, *args):
        stdout = io.StringIO()
        with mock.patch.object(sys, 'argv', ['settings.py', *args]), contextlib.redirect_stdout(stdout):
            code = self.settings.main()
        return code, stdout.getvalue()

    def test_defaults_then_saved_values(self):
        self.assertEqual(json.loads(self.call('status')[1]), {'language': 'en', 'theme': 'graphite', 'terminal': 'auto', 'storage': 'sd', 'vibration': 2, 'timeout': 2, 'utc_offset': 0,
                                                       'refresh': 0, 'brightness': 0, 'animation': 'zoom', 'lock': 1, 'access': 1,
                                                       'navigation': 0, 'timezone': '', 'password': False, 'password_digits': False})
        self.assertEqual(self.call('set', '--language', 'uk', '--vibration', '3', '--utc-offset', '60')[0], 0)
        self.assertEqual(self.disk, {'language': 'uk', 'vibration': 3, 'utc_offset': 60})
        self.assertEqual(self.call('set', '--refresh', '90', '--brightness', '40', '--terminal', 'tokyo-night', '--storage', 'usb')[0], 0)
        self.assertEqual((self.disk['refresh'], self.disk['brightness'], self.disk['terminal'], self.disk['storage']), (90, 40, 'tokyo-night', 'usb'))
        # The recovery clock's zone (2026-11-09); empty returns to the fixed offset.
        self.assertEqual(self.call('set', '--timezone', 'America/Argentina/Buenos_Aires')[0], 0)
        self.assertEqual(self.disk['timezone'], 'America/Argentina/Buenos_Aires')
        self.assertEqual(self.call('set', '--timezone', '')[0], 0)
        self.assertEqual(self.disk['timezone'], '')
        os.unlink(os.environ['A22X_SETTINGS_RAM'])
        self.assertEqual(json.loads(self.call('status')[1])['language'], 'uk')

    def test_bad_values_are_refused(self):
        for key, value in (('--language', 'ukrainian'), ('--vibration', '9'), ('--timeout', '6'), ('--utc-offset', '61'), ('--utc-offset', '900'),
                           ('--refresh', '75'), ('--brightness', '101'), ('--storage', 'cloud'), ('--terminal', 'X'),
                           ('--timezone', '../../etc/passwd'), ('--timezone', 'europe/berlin'), ('--timezone', 'Europe')):
            self.assertEqual(self.call('set', key, value)[0], 2, (key, value))
        self.assertEqual(self.disk, {})

    def test_no_writable_storage_keeps_values_until_reboot(self):
        self.writable = False
        self.assertEqual(self.call('set', '--language', 'de')[0], 3)
        self.assertEqual(json.loads(self.call('status')[1])['language'], 'de')

    def test_password(self):
        secret = self.root / 'secret'
        secret.write_text('1234')
        self.assertEqual(self.call('verify', '--secret-file', str(secret))[0], 0)
        self.assertEqual(self.call('set-password', '--secret-file', str(secret))[0], 0)
        self.assertTrue(json.loads(self.call('status')[1])['password'])
        self.assertNotIn('1234', json.dumps(self.disk))
        self.assertEqual(self.call('verify', '--secret-file', str(secret))[0], 0)
        secret.write_text('4321')
        self.assertEqual(self.call('verify', '--secret-file', str(secret))[0], 1)
        self.assertEqual(self.call('clear-password')[0], 1)
        self.assertEqual(self.call('clear-password', '--secret-file', str(secret))[0], 1)
        self.assertTrue(json.loads(self.call('status')[1])['password'])
        secret.write_text('1234')
        self.assertEqual(self.call('clear-password', '--secret-file', str(secret))[0], 0)
        self.assertFalse(json.loads(self.call('status')[1])['password'])

    def test_changing_a_password_needs_the_old_one(self):
        old, new = self.root / 'old', self.root / 'new'
        old.write_text('1234')
        new.write_text('abcd')
        self.assertEqual(self.call('set-password', '--secret-file', str(old))[0], 0)
        self.assertEqual(self.call('set-password', '--secret-file', str(new))[0], 1)
        self.assertEqual(self.call('verify', '--secret-file', str(old))[0], 0)
        self.assertEqual(self.call('set-password', '--secret-file', str(new), '--old-secret-file', str(new))[0], 1)
        self.assertEqual(self.call('verify', '--secret-file', str(old))[0], 0)
        self.assertEqual(self.call('set-password', '--secret-file', str(new), '--old-secret-file', str(old))[0], 0)
        self.assertEqual(self.call('verify', '--secret-file', str(new))[0], 0)
        self.assertEqual(self.call('verify', '--secret-file', str(old))[0], 1)

    def test_a_password_survives_every_later_settings_change(self):
        secret = self.root / 'secret'
        secret.write_text('1234')
        self.assertEqual(self.call('set-password', '--secret-file', str(secret))[0], 0)
        self.assertEqual(self.call('set', '--theme', 'aurora', '--lock', '2', '--animation', 'blur', '--access', '0')[0], 0)
        status = json.loads(self.call('status')[1])
        self.assertTrue(status['password'])
        self.assertEqual((status['lock'], status['animation'], status['access']), (2, 'blur', 0))
        self.assertEqual(self.call('verify', '--secret-file', str(secret))[0], 0)

    def test_a_disk_that_cannot_be_read_is_not_an_empty_one(self):
        secret = self.root / 'secret'
        secret.write_text('1234')
        self.assertEqual(self.call('set-password', '--secret-file', str(secret))[0], 0)
        os.unlink(os.environ['A22X_SETTINGS_RAM'])
        saved = dict(self.disk)
        with mock.patch.object(self.settings, 'read_disk', side_effect=BlockingIOError), mock.patch.object(self.settings, 'LOCK_WAIT', 0):
            with self.assertRaises(BlockingIOError):
                self.settings.load()
        self.assertFalse(Path(os.environ['A22X_SETTINGS_RAM']).exists())
        self.assertEqual(self.disk, saved)

    def test_settings_volumes_that_are_missing_are_not_empty(self):
        """2026-11-07: the eMMC came up as mmcblk1 and cache was not found; defaults were shown."""
        import contextlib
        class Context:
            lock_path, mode = self.root / 'lock', 'fixture'
            def volume(self, label):
                raise self_error('VOLUME_NOT_FOUND', label)
        self_error = self.settings.fs.FilesystemError
        @contextlib.contextmanager
        def lock(*args):
            yield
        with mock.patch.object(self.settings.fs, 'DeviceContext', Context), mock.patch.object(self.settings, 'operation_lock', lock):
            with self.assertRaises(OSError):
                self.real_read_disk()
        found = self.root / 'cache-root'
        found.mkdir()
        @contextlib.contextmanager
        def present(context, label, mode):
            yield found
        with mock.patch.object(self.settings.fs, 'DeviceContext', Context), mock.patch.object(self.settings, 'operation_lock', lock), \
                mock.patch.object(self.settings, 'volume_root', present):
            self.assertEqual(self.real_read_disk(), {})

    def test_a_busy_lock_is_waited_for(self):
        self.disk = {'language': 'uk'}
        replies = [BlockingIOError(), BlockingIOError(), dict(self.disk)]
        with mock.patch.object(self.settings, 'read_disk', side_effect=replies), mock.patch.object(self.settings.time, 'sleep'):
            self.assertEqual(self.settings.load(), {'language': 'uk'})

    def test_a_password_of_digits_is_remembered_as_such(self):
        secret = self.root / 'secret'
        for value, digits in (('1234', True), ('12a4', False), ('١٢٣٤', False)):
            secret.write_text(value)
            self.assertEqual(self.call('clear-password', '--secret-file', str(secret))[0] in (0, 1), True)
            self.disk.clear()
            os.unlink(os.environ['A22X_SETTINGS_RAM'])
            self.assertEqual(self.call('set-password', '--secret-file', str(secret))[0], 0)
            self.assertEqual(json.loads(self.call('status')[1])['password_digits'], digits)
        self.assertEqual(self.call('clear-password', '--secret-file', str(secret))[0], 0)
        self.assertEqual(json.loads(self.call('status')[1])['password_digits'], False)

    def test_theme_names_may_hold_digits_and_hyphens(self):
        self.assertEqual(self.call('set', '--theme', 'beaver-2', '--terminal', 'green-tty')[0], 0)
        for bad in ('a', '../x', 'A' * 4, 'x' * 24):
            self.assertEqual(self.call('set', '--theme', bad)[0], 2, bad)

    def test_new_settings_refuse_unknown_values(self):
        self.assertEqual(self.call('set', '--animation', 'spin')[0], 2)
        self.assertEqual(self.call('set', '--lock', '3')[0], 2)
        self.assertEqual(self.call('set', '--access', '2')[0], 2)


if __name__ == '__main__':
    unittest.main()
