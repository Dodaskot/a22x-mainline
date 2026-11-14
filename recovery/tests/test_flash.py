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
import tarfile
import tempfile
import unittest
import zipfile
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

    def journals(self):
        return sorted(p.name for p in (self.root / 'sd' / flash.ROLLBACK_FOLDER).glob('*' + flash.PENDING_SUFFIX))

    def test_a_failed_write_puts_the_saved_copy_back(self):
        """2026-11-12 audit: the old content came back only by hand."""
        before = (self.root / 'targets' / 'boot').read_bytes()
        code, checked, _ = self.check('boot', self.image(boot_image(), 'boot.img'))
        real, calls = flash.read_back, []

        def first_wrong(*args):
            calls.append(1)
            return '0' * 64 if len(calls) == 1 else real(*args)
        with mock.patch.object(flash, 'read_back', first_wrong):
            code, done, _ = self.write('boot', '/boot.img', checked)
        self.assertEqual((code, done['error']['code'], done['error']['details']['rollback_restored']), (2, 'READBACK_MISMATCH', True))
        self.assertEqual((self.root / 'targets' / 'boot').read_bytes(), before)
        self.assertEqual(self.journals(), [])

    def test_a_partition_written_another_way_after_a_left_journal_is_left_alone(self):
        """2026-11-12 review: a journal left behind, then BOOT flashed with Odin (or a second flash).
        Its content is neither this write's nor the saved copy's: resume leaves it, and the journal goes."""
        code, checked, _ = self.check('boot', self.image(boot_image(), 'boot.img'))
        with mock.patch.object(flash, 'end_pending', lambda *a: None):
            code, done, _ = self.write('boot', '/boot.img', checked)
        self.assertEqual((code, len(self.journals())), (0, 1))
        odin = bytearray((self.root / 'targets' / 'boot').read_bytes())
        odin[:len(odin) // 2] = bytes(range(256)) * (len(odin) // 2 // 256)
        (self.root / 'targets' / 'boot').write_bytes(bytes(odin))
        code, result, _ = self.run_helper('resume')
        self.assertEqual([r['outcome'] for r in result['resumed']], ['changed'])
        self.assertEqual(((self.root / 'targets' / 'boot').read_bytes(), self.journals()), (bytes(odin), []))

    def test_a_journal_that_cannot_be_read_goes_and_does_not_hide_the_others(self):
        code, checked, _ = self.check('boot', self.image(boot_image(), 'boot.img'))
        with mock.patch.object(flash, 'end_pending', lambda *a: None):
            code, done, _ = self.write('boot', '/boot.img', checked)
        self.assertEqual(len(self.journals()), 1)
        (self.root / 'sd' / flash.ROLLBACK_FOLDER / ('aaa-20260101-000000.img' + flash.PENDING_SUFFIX)).write_text('')
        code, result, _ = self.run_helper('resume')
        self.assertEqual(sorted(r['outcome'] for r in result['resumed']), ['completed', 'unreadable'])
        self.assertEqual(self.journals(), [])

    def test_a_write_cut_short_is_finished_or_undone_at_the_next_start(self):
        before = (self.root / 'targets' / 'boot').read_bytes()
        code, checked, _ = self.check('boot', self.image(boot_image(), 'boot.img'))
        with mock.patch.object(flash, 'end_pending', lambda *a: None):  # the power went before the journal went
            code, done, _ = self.write('boot', '/boot.img', checked)
        self.assertEqual(code, 0, done)
        self.assertEqual(len(self.journals()), 1)
        written = (self.root / 'targets' / 'boot').read_bytes()
        code, result, _ = self.run_helper('resume')
        self.assertEqual((code, [r['outcome'] for r in result['resumed']]), (0, ['completed']))
        self.assertEqual(((self.root / 'targets' / 'boot').read_bytes(), self.journals()), (written, []))
        # Cut in the middle of a write of another image: its first half new, the rest still old.
        # The saved copy comes back.
        other = boot_image() + bytes(range(256)) * 64  # the same boot image with 16 KiB more after it
        code, checked2, _ = self.check('boot', self.image(other, 'boot2.img'))
        self.assertEqual(code, 0, checked2)
        with mock.patch.object(flash, 'end_pending', lambda *a: None):
            code, done, _ = self.write('boot', '/boot2.img', checked2)
        self.assertEqual(code, 0, done)
        new = (self.root / 'targets' / 'boot').read_bytes()
        self.assertNotEqual(new, written)
        changed = [at for at in range(0, len(new), 4096) if new[at:at + 4096] != written[at:at + 4096]]
        self.assertGreater(len(changed), 2)
        torn = bytearray(new)
        half = changed[len(changed) // 2]
        torn[half:] = written[half:]
        torn[half - 100:half] = b'\x77' * 100  # one page caught in the middle
        (self.root / 'targets' / 'boot').write_bytes(bytes(torn))
        if bytes(torn) != new and bytes(torn) != written:
            code, result, _ = self.run_helper('resume')
            self.assertEqual([r['outcome'] for r in result['resumed']], ['restored'])
            self.assertEqual(((self.root / 'targets' / 'boot').read_bytes(), self.journals()), (written, []))
        self.assertEqual(self.run_helper('resume')[1]['resumed'], [])
        self.assertNotEqual(before, written)

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

    def test_an_image_inside_a_zip_is_written_with_its_crc(self):
        """2026-11-10: GSIs and many images come as ZIPs; the image is decoded from the ZIP itself,
        its CRC checked, and nothing is unpacked beside it."""
        data = vbmeta(b'z' * 3000)
        with zipfile.ZipFile(self.root / 'usb/vbmeta.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('vbmeta.img', data)
            z.writestr('README.txt', 'notes')
        code, checked, _ = self.check('vbmeta', '/vbmeta.zip')
        self.assertEqual(code, 0, checked)
        self.assertEqual(checked['image']['compression'], 'zip')
        code, done, _ = self.write('vbmeta', '/vbmeta.zip', checked)
        self.assertEqual(code, 0, done)
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes()[:len(data)], data)
        with zipfile.ZipFile(self.root / 'usb/two.zip', 'w') as z:
            z.writestr('vbmeta.img', data)
            z.writestr('dtbo.img', b'x' * 100)
        code, result, _ = self.check('vbmeta', '/two.zip')
        self.assertEqual((code, result['error']['code']), (2, 'ZIP_AMBIGUOUS'))
        # A GSI's own vbmeta.img, chosen by its name beside its system.img.
        disabled = vbmeta(b'g' * 3000)
        with zipfile.ZipFile(self.root / 'usb/gsi.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('system.img', bytes(4096))
            z.writestr('vbmeta.img', disabled)
        code, checked, _ = self.run_helper('check', '--target', 'vbmeta', '--volume', 'usb-p1', '--volume-token', self.token('usb-p1'),
                                           '--path', '/gsi.zip', '--member', 'vbmeta.img')
        self.assertEqual((code, checked['image']['compression']), (0, 'zip:vbmeta.img'), checked)
        code, done, _ = self.write('vbmeta', '/gsi.zip', checked, '--member', 'vbmeta.img')
        self.assertEqual(code, 0, done)
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes()[:len(disabled)], disabled)
        code, result, _ = self.run_helper('check', '--target', 'vbmeta', '--volume', 'usb-p1', '--volume-token', self.token('usb-p1'),
                                          '--path', '/gsi.zip', '--member', 'boot.img')
        self.assertEqual((code, result['error']['code']), (2, 'ZIP_MEMBER_MISSING'))
        # A stored image with one byte changed: the CRC stops it before anything is written.
        stored = vbmeta(b's' * 3000)
        with zipfile.ZipFile(self.root / 'usb/bad.zip', 'w', zipfile.ZIP_STORED) as z:
            z.writestr('vbmeta.img', stored)
        raw = bytearray((self.root / 'usb/bad.zip').read_bytes())
        at = raw.find(stored[:64]) + 2000
        raw[at] ^= 0xff
        (self.root / 'usb/bad.zip').write_bytes(bytes(raw))
        before = (self.root / 'targets/vbmeta').read_bytes()
        code, result, _ = self.check('vbmeta', '/bad.zip')
        self.assertEqual((code, result['error']['code']), (2, 'INVALID_COMPRESSED_IMAGE'))
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes(), before)

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

    @unittest.skipUnless(shutil.which('lz4'), 'lz4 is needed')
    def test_samsung_firmware_images_are_read_from_their_tar(self):
        """2026-11-10: Samsung's AP_....tar.md5 holds LZ4 images (super sparse inside); each is
        read from its place in the tar, through lz4, and nothing is unpacked beside it."""
        lz4 = lambda data: subprocess.run(['lz4', '-c'], input=data, stdout=subprocess.PIPE, check=True).stdout
        payload = bytearray(8192)
        payload[4096:4100] = b'gDla'
        meta = vbmeta(b'm' * 3000)
        members = [('boot.img.lz4', lz4(b'not used here' * 100)), ('super.img.lz4', lz4(sparse(bytes(payload)))),
                   ('vbmeta.img.lz4', lz4(meta)), ('meta-data/fota.zip', b'PK')]
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        body = stream.getvalue()
        name = 'AP_A226BXXSBDZC3_TEST.tar.md5'
        (self.root / 'usb' / name).write_bytes(body + (hashlib.md5(body).hexdigest() + '  ' + name[:-4] + '\n').encode())
        helper = lambda command, target, member, *extra: self.run_helper(
            command, '--target', target, '--volume', 'usb-p1', '--volume-token', self.token('usb-p1'), '--path', '/' + name,
            '--member', member, *extra)
        code, checked, _ = helper('check', 'super', 'super.img.lz4')
        self.assertEqual(code, 0, checked)
        self.assertEqual((checked['image']['compression'], checked['image']['format']), ('tar:super.img.lz4', 'sparse'))
        code, done, _ = helper('write', 'super', 'super.img.lz4', '--expected-sha256', checked['image']['sha256'],
                               '--expected-identity', checked['image']['identity'], '--confirm')
        self.assertEqual(code, 0, done)
        written = (self.root / 'targets/super').read_bytes()
        self.assertEqual((written[:8192], written[8192:12288]), (bytes(payload), b'\xaa' * 4096))
        code, checked, _ = helper('check', 'vbmeta', 'vbmeta.img.lz4')
        self.assertEqual(code, 0, checked)
        code, done, _ = helper('write', 'vbmeta', 'vbmeta.img.lz4', '--expected-sha256', checked['image']['sha256'],
                               '--expected-identity', checked['image']['identity'], '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes()[:len(meta)], meta)
        code, result, _ = helper('check', 'vbmeta', 'vbmeta_system.img.lz4')
        self.assertEqual((code, result['error']['code']), (2, 'TAR_MEMBER_MISSING'))
        code, result, _ = self.check('vbmeta', '/' + name)
        self.assertEqual((code, result['error']['code']), (2, 'TAR_MEMBER_REQUIRED'))
        # One byte of the LZ4 stream changed: lz4 stops, and nothing is written.
        raw = bytearray((self.root / 'usb' / name).read_bytes())
        at = raw.find(lz4(meta)[:32]) + 40
        raw[at] ^= 0x55
        (self.root / 'usb' / name).write_bytes(bytes(raw))
        before = (self.root / 'targets/vbmeta').read_bytes()
        code, result, _ = helper('check', 'vbmeta', 'vbmeta.img.lz4')
        self.assertEqual(code, 2, result)
        self.assertIn(result['error']['code'], ('INVALID_COMPRESSED_IMAGE', 'WRONG_IMAGE_TYPE', 'SHORT_IMAGE'))
        self.assertEqual((self.root / 'targets/vbmeta').read_bytes(), before)

    def test_the_boot_that_was_there_is_kept_on_cache(self):
        """2026-11-10: before BOOT is written, a BOOT of this phone there (Linux here) is kept on
        cache, the way back; without a cache the write goes on."""
        linux = boot_image()
        with open(self.root / 'targets/boot', 'r+b') as f:
            f.write(linux)
        before = (self.root / 'targets/boot').read_bytes()
        (self.root / 'cache').mkdir()
        self.volumes([{'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True},
                      {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}])
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_android_gsi import stock_boot
        path = self.image(stock_boot(), 'stock.img')
        code, checked, _ = self.check('boot', path)
        self.assertEqual(code, 0, checked)
        code, done, _ = self.write('boot', path, checked)
        self.assertEqual(code, 0, done)
        kept = self.root / 'cache' / done['kept_boot'].lstrip('/')
        self.assertTrue(kept.name.startswith('linux-7.2.0-a22x'))
        self.assertEqual(kept.read_bytes(), before)
        self.assertEqual(json.loads(Path(str(kept) + '.json').read_text())['kind'], 'linux')
        # Without a cache volume and without a card that can take a copy, there is no way back: the
        # write is refused (2026-11-12, on the phone: BOOT was written with no copy at all).
        self.volumes([{'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}])
        path = self.image(linux, 'linux.img')
        code, checked, _ = self.check('boot', path)
        written = (self.root / 'targets/boot').read_bytes()
        code, done, _ = self.write('boot', path, checked)
        self.assertEqual((code, done['error']['code']), (2, 'ROLLBACK_UNAVAILABLE'))
        self.assertEqual((self.root / 'targets/boot').read_bytes(), written)

    def test_an_archive_says_which_images_it_holds_and_where_they_go(self):
        """2026-11-13, the owner: a ROM's ZIP should be opened and its images offered; its BOOT then
        goes on BOOT straight from the ZIP."""
        import zipfile
        path = self.root / 'usb' / 'rom.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('META-INF/com/google/android/update-binary', '#!/sbin/sh\n')
            z.writestr('system.transfer.list', '4\n1\n0\n0\nnew 2,0,1\n')
            z.writestr('boot.img', boot_image())
            z.writestr('lk.img', b'x' * 4096)
        code, listed, _ = self.run_helper('members', '--volume', 'usb-p1', '--volume-token', self.token(), '--path', '/rom.zip')
        self.assertEqual(code, 0, listed)
        self.assertEqual((listed['archive'], listed['installer'], listed['block_ota']), ('zip', True, True))
        self.assertEqual({m['name']: m['target'] for m in listed['members']}, {'boot.img': 'boot', 'lk.img': ''})
        code, checked, _ = self.run_helper('check', '--target', 'boot', '--volume', 'usb-p1', '--volume-token', self.token(),
                                           '--path', '/rom.zip', '--member', 'boot.img')
        self.assertEqual(code, 0, checked)
        code, refused, _ = self.run_helper('members', '--volume', 'usb-p1', '--volume-token', self.token(),
                                           '--path', self.image(boot_image(), 'plain.img'))
        self.assertEqual(refused['error']['code'], 'NOT_AN_ARCHIVE')

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
        self.real_read_disk = self.settings.read_volumes  # the volume store; misc: test_settings_misc
        self.disk = {}
        self.writable = True
        patcher = mock.patch.multiple(self.settings, read_disk=lambda details=None: dict(self.disk), write_disk=self.save)
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
                                                       'navigation': 0, 'timezone': '', 'torch': 0, 'android_log': '', 'password': False, 'password_digits': False})
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
        with mock.patch.object(self.settings.fs, 'DeviceContext', Context), mock.patch.object(self.settings, 'lease', lock):
            with self.assertRaises(OSError):
                self.real_read_disk()
        found = self.root / 'cache-root'
        found.mkdir()
        @contextlib.contextmanager
        def present(context, label, mode):
            yield found
        class Found(Context):
            def volume(self, label):
                return {'mounted': False, 'mountable': True, 'volume_token': 't'}
        with mock.patch.object(self.settings.fs, 'DeviceContext', Found), mock.patch.object(self.settings, 'lease', lock), \
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

    def test_the_flashlight_level_is_kept(self):
        """2026-11-11: the level of the flashlight chosen in the interface; 0 means none was chosen."""
        self.assertEqual(self.call('set', '--torch', '9')[0], 0)
        self.assertEqual(self.disk['torch'], 9)
        self.assertEqual(json.loads(self.call('status')[1])['torch'], 9)
        self.assertEqual(self.call('set', '--language', 'uk')[0], 0)  # another change keeps it
        self.assertEqual(self.disk['torch'], 9)
        for bad in ('-1', '256', 'bright', ''):
            self.assertEqual(self.call('set', '--torch', bad)[0], 2, bad)
        self.assertEqual(self.disk['torch'], 9)

    def test_the_failed_start_of_android_that_was_read_about_is_kept(self):
        """2026-11-12: the page that says why Android did not start opens once for each failed start;
        the id of the one the owner has read about is kept with the settings."""
        self.assertEqual(self.call('set', '--android-log', 'bb9507f313de2d66')[0], 0)
        self.assertEqual(self.disk['android_log'], 'bb9507f313de2d66')
        self.assertEqual(json.loads(self.call('status')[1])['android_log'], 'bb9507f313de2d66')
        self.assertEqual(self.call('set', '--torch', '3')[0], 0)  # another setting leaves it
        self.assertEqual(self.disk['android_log'], 'bb9507f313de2d66')
        for bad in ('BB9507F313DE2D66', 'bb95', 'bb9507f313de2d66a', '../etc/passwd', 'zz9507f313de2d66'):
            self.assertEqual(self.call('set', '--android-log', bad)[0], 2, bad)
        self.assertEqual(self.call('set', '--android-log', '')[0], 0)
        self.assertEqual(self.disk['android_log'], '')

    def test_new_settings_refuse_unknown_values(self):
        self.assertEqual(self.call('set', '--animation', 'spin')[0], 2)
        self.assertEqual(self.call('set', '--lock', '3')[0], 2)
        self.assertEqual(self.call('set', '--access', '2')[0], 2)


if __name__ == '__main__':
    unittest.main()


class FlashLogical(unittest.TestCase):
    """Logical partitions inside super (2026-11-10): a GSI's system.img into super/system."""
    MIB = 1 << 20

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_lp_metadata as tl
        self.root = Path(tempfile.mkdtemp(prefix='a22x-flash-lp-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('usb', 'sd', 'targets'):
            (self.root / name).mkdir()
        tl.make_super(self.root / 'targets/super')
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'},
            'targets': {'super': {'path': 'targets/super', 'size_bytes': 64 * self.MIB}}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True},
            {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': False}]}))

    run_helper = Flash.run_helper
    token = Flash.token
    image = Flash.image
    check = Flash.check

    def write(self, target, path, result, image_bytes=None):
        return self.run_helper('write', '--target', target, '--volume', 'usb-p1', '--volume-token', self.token(), '--path', path,
                               '--expected-sha256', result['image']['sha256'], '--expected-identity', result['image']['identity'],
                               '--confirm', *(('--image-bytes', str(image_bytes)) if image_bytes is not None else ()))

    def partition(self, name):
        import lp_metadata as lp
        fd = os.open(self.root / 'targets/super', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        s = lp.Super(fd)
        return s, s.partition(name)

    def test_targets_list_the_logical_partitions(self):
        code, result, _ = self.run_helper('targets')
        rows = {row['label']: row for row in result['targets']}
        self.assertEqual(code, 0)
        self.assertEqual(rows['super/system']['size_bytes'], 8 * self.MIB)
        self.assertEqual(rows['super/product']['size_bytes'], 2 * self.MIB)
        self.assertEqual(rows['super/vendor']['room_bytes'], 26 * self.MIB)  # the group of 40 MiB holds 14
        self.assertTrue(rows['super/vendor']['logical'])
        self.assertIn('super_free_bytes', result)

    def test_a_smaller_image_fills_the_partition_with_zeros(self):
        data = bytearray(os.urandom(3 * self.MIB))
        struct.pack_into('<H', data, 1080, 0xEF53)
        path = self.image(bytes(data), 'vendor.img')
        code, result, _ = self.check('super/vendor', path)
        self.assertEqual(code, 0, result)
        self.assertEqual((result['target']['size_bytes'], result['target']['new_size_bytes']), (4 * self.MIB, 4 * self.MIB))
        self.assertEqual(result['image']['kind'], 'ext4 file system image')
        code, done, _ = self.write('super/vendor', path, result, result['image']['payload_bytes'])
        self.assertEqual(code, 0, done)
        self.assertTrue(done['verified'])
        s, p = self.partition('vendor')
        self.assertEqual(s.read_partition('vendor'), hashlib.sha256(bytes(data) + bytes(self.MIB)).hexdigest())
        self.assertNotEqual(s.read_partition('system'), done['partition_sha256'])

    def test_a_larger_sparse_gsi_grows_system(self):
        payload = os.urandom(20 * self.MIB)
        path = self.image(sparse(payload), 'system.img')
        code, result, _ = self.check('super/system', path)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['image']['format'], 'sparse')
        grown = result['target']['new_size_bytes']
        self.assertEqual(grown, 21 * self.MIB)  # 20 MiB + one fill block, rounded to 1 MiB
        code, done, _ = self.write('super/system', path, result, result['image']['payload_bytes'])
        self.assertEqual(code, 0, done)
        self.assertEqual((done['target']['size_bytes'], done['target']['resized_from']), (21 * self.MIB, 8 * self.MIB))
        s, p = self.partition('system')
        self.assertEqual(s.size_of(p), 21 * self.MIB)
        self.assertEqual(s.read_partition('system'), done['partition_sha256'])

    def test_a_gsi_zip_goes_into_system(self):
        """2026-11-10: Google's GSI ZIP holds system.img, vbmeta.img and build.prop; system.img is
        decoded from it straight into super/system, with the rest of the partition zeroed."""
        data = bytearray(os.urandom(5 * self.MIB))
        struct.pack_into('<H', data, 1080, 0xEF53)
        with zipfile.ZipFile(self.root / 'usb/aosp_arm64-exp-TEST.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('system.img', bytes(data))
            z.writestr('vbmeta.img', b'AVB0' + bytes(60))
            z.writestr('build.prop', 'ro.build.version.release=13\n')
        code, result, _ = self.check('super/system', '/aosp_arm64-exp-TEST.zip')
        self.assertEqual(code, 0, result)
        self.assertEqual((result['image']['compression'], result['image']['payload_bytes'], result['target']['new_size_bytes']),
                         ('zip', len(data), 8 * self.MIB))
        self.assertEqual(result['image']['kind'], 'ext4 file system image')
        code, done, _ = self.write('super/system', '/aosp_arm64-exp-TEST.zip', result, result['image']['payload_bytes'])
        self.assertEqual(code, 0, done)
        s, _ = self.partition('system')
        self.assertEqual(s.read_partition('system'), hashlib.sha256(bytes(data) + bytes(3 * self.MIB)).hexdigest())

    def test_its_own_size_keeps_the_partition_and_the_old_content_is_saved(self):
        """2026-11-10, on the phone: odm holds 4,349,952 bytes, not a whole MiB, and its own image
        was shown "growing" to 5 MiB, with "too large to save first". An image that fits keeps
        the size, the same content is recognised, and a small partition is saved before writing."""
        import test_lp_metadata as tl
        odm = 4349952
        tl.make_super(self.root / 'targets/super', parts=(('system', 8 * self.MIB), ('odm', odm), ('vendor', 4 * self.MIB)))
        s, _ = self.partition('odm')
        current = b''.join(s.chunks('odm'))
        self.assertEqual(len(current), odm)
        code, result, _ = self.check('super/odm', self.image(current, 'odm.img'))
        self.assertEqual(code, 0, result)
        self.assertEqual((result['target']['size_bytes'], result['target']['new_size_bytes']), (odm, odm))
        self.assertTrue(result['identical'])
        self.assertEqual(result['warnings'], [])
        changed = bytearray(current)
        changed[100] ^= 0xff
        path = self.image(bytes(changed), 'odm-changed.img')
        code, result, _ = self.check('super/odm', path)
        self.assertFalse(result['identical'])
        self.assertEqual((result['rollback_volume'], result['target']['new_size_bytes']), ('sdcard-p1', odm))
        code, done, progress = self.write('super/odm', path, result, result['image']['payload_bytes'])
        self.assertEqual(code, 0, done)
        self.assertEqual((done['target']['size_bytes'], done['target']['resized_from']), (odm, odm))
        self.assertIn('Saving the current partition', progress)
        saved = self.root / 'sd' / done['rollback']['path'].lstrip('/')
        self.assertTrue(saved.name.startswith('super-odm-'), saved.name)
        self.assertEqual(saved.read_bytes(), current)
        s, _ = self.partition('odm')
        self.assertEqual(b''.join(s.chunks('odm')), bytes(changed))
        # A large one is not saved: system here stands in for the phone's 4.3 GiB.
        with mock.patch.object(flash, 'ROLLBACK_LIMIT', 4 * self.MIB):
            code, result, _ = self.check('super/system', self.image(bytes(self.MIB), 'small.img'))
        self.assertEqual(result['warnings'][:1], ['This partition is too large to save first'])

    def test_a_failed_write_inside_super_puts_the_saved_copy_back(self):
        """2026-11-12 review: a write into odm that failed after it began left odm half written."""
        import test_lp_metadata as tl
        import lp_metadata as lp
        odm = 4349952
        tl.make_super(self.root / 'targets/super', parts=(('system', 8 * self.MIB), ('odm', odm), ('vendor', 4 * self.MIB)))
        s, _ = self.partition('odm')
        current = b''.join(s.chunks('odm'))
        changed = bytearray(current)
        changed[100:200] = bytes(range(100))
        path = self.image(bytes(changed), 'odm-changed.img')
        code, result, _ = self.check('super/odm', path)
        self.assertEqual(code, 0, result)
        real = lp.Super.read_partition
        calls = []

        def wrong_once(self_, name):
            calls.append(name)
            return '0' * 64 if len(calls) == 1 else real(self_, name)
        with mock.patch.object(lp.Super, 'read_partition', wrong_once):
            code, done, _ = self.write('super/odm', path, result, result['image']['payload_bytes'])
        self.assertEqual((code, done['error']['code'], done['error']['details']['rollback_restored']), (2, 'READBACK_MISMATCH', True))
        s, _ = self.partition('odm')
        self.assertEqual(b''.join(s.chunks('odm')), current)

    def test_too_large_or_unchecked_is_refused(self):
        path = self.image(bytes(30 * self.MIB), 'huge.img')
        code, result, _ = self.check('super/system', path)  # 8 + 26 of room = 34 MiB would fit
        self.assertEqual(code, 0, result)
        path = self.image(bytes(36 * self.MIB), 'larger.img')
        code, result, _ = self.check('super/system', path)
        self.assertEqual((code, result['error']['code']), (2, 'IMAGE_TOO_LARGE'))
        small = self.image(bytes(self.MIB), 'small.img')
        code, result, _ = self.check('super/odm', small)
        self.assertEqual((code, result['error']['code']), (2, 'NO_SUCH_PARTITION'))
        code, result, _ = self.check('super/vendor', small)
        code, done, _ = self.write('super/vendor', small, result)  # no --image-bytes
        self.assertEqual((code, done['error']['code']), (2, 'INVALID_ARGUMENT'))
        code, done, _ = self.write('super/vendor', small, result, 2 * self.MIB)  # not the checked size
        self.assertEqual((code, done['error']['code']), (2, 'IMAGE_CHANGED'))
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                flash.parser().parse_args(['--fixture', 'x', 'check', '--target', 'super/../boot', '--volume', 'v',
                                           '--volume-token', 't', '--path', '/p'])
