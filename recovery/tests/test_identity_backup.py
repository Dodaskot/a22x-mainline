"""Identity (EFS/NV) backup and restore on fixture files; no block device is opened."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity_backup as ib
import partition_archive as pa

PARTS = {'efs': 1 << 16, 'sec_efs': 1 << 14, 'nvdata': 3 << 16, 'protect1': 1 << 15}
RETIRED = {'param': 1 << 14}


class IdentityBackup(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-identity-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('parts', 'sd', 'usb'):
            (self.root / name).mkdir()
        self.data = {}
        for label, size in PARTS.items():
            self.data[label] = os.urandom(size)
            (self.root / 'parts' / label).write_bytes(self.data[label])
        self.config(identity='fixture:phone-a')
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': False},
            {'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}]}))

    def config(self, identity, busy=()):
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': identity},
            'identity_parts': {label: {'path': 'parts/' + label} for label in PARTS}, 'identity_busy': list(busy)}))

    def run_helper(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ib.main(['--fixture', str(self.root), *args])
        return code, json.loads(out.getvalue().splitlines()[-1])

    def backup(self):
        code, result = self.run_helper('backup', '--volume', 'sdcard-p1')
        self.assertEqual(code, 0, result)
        return result

    def test_list_names_only_identity_partitions(self):
        code, result = self.run_helper('list')
        self.assertEqual(code, 0)
        self.assertEqual([p['label'] for p in result['partitions']], ['efs', 'sec_efs', 'nvdata', 'protect1'])
        self.assertTrue(all(label not in ib.LABELS for label in ('lk', 'preloader', 'tee1', 'md1img', 'super', 'boot')))

    def test_backup_saves_every_partition_with_checksums(self):
        result = self.backup()
        folder = self.root / 'sd' / result['path'].lstrip('/')
        manifest = json.loads((folder / 'manifest.json').read_text())
        self.assertEqual(hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest(), result['manifest_sha256'])
        for row in manifest['partitions']:
            self.assertEqual((folder / row['file']).read_bytes(), self.data[row['label']])
            self.assertEqual(row['sha256'], hashlib.sha256(self.data[row['label']]).hexdigest())
        self.assertEqual(oct(folder.stat().st_mode & 0o777), '0o700')
        # A second backup in the same second gets its own folder.
        self.assertNotEqual(self.backup()['path'], result['path'])

    def test_a_read_only_volume_is_refused(self):
        code, result = self.run_helper('backup', '--volume', 'usb-p1')
        self.assertEqual((code, result['error']['code']), (2, 'VOLUME_READ_ONLY'))

    def test_verify_finds_damage(self):
        result = self.backup()
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertEqual(code, 0)
        self.assertTrue(check['restorable'] and check['same_phone'])
        image = self.root / 'sd' / result['path'].lstrip('/') / 'nvdata.img'
        damaged = bytearray(image.read_bytes())
        damaged[100] ^= 1
        image.write_bytes(bytes(damaged))
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertEqual((code, check['error']['code']), (2, 'BACKUP_DAMAGED'))

    def restore(self, result, *extra):
        return self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'],
                               '--expected-manifest-sha256', result['manifest_sha256'], *extra)

    def test_restore_brings_back_the_content_and_keeps_the_replaced_one(self):
        result = self.backup()
        broken = os.urandom(PARTS['efs'])
        (self.root / 'parts' / 'efs').write_bytes(broken)
        code, refused = self.restore(result)
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        code, done = self.restore(result, '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual(done['restored'], ['efs'])
        self.assertEqual((self.root / 'parts' / 'efs').read_bytes(), self.data['efs'])
        self.assertEqual((self.root / 'sd' / done['before'].lstrip('/') / 'efs.img').read_bytes(), broken)
        code, again = self.restore(result, '--confirm')
        self.assertTrue(again['identical'])

    def test_restore_refuses_another_phone_a_changed_backup_and_a_busy_partition(self):
        result = self.backup()
        (self.root / 'parts' / 'efs').write_bytes(bytes(PARTS['efs']))
        self.config(identity='fixture:phone-b')
        code, refused = self.restore(result, '--confirm')
        self.assertEqual(refused['error']['code'], 'OTHER_PHONE')
        self.config(identity='fixture:phone-a', busy=('efs',))
        code, refused = self.restore(result, '--confirm')
        self.assertEqual(refused['error']['code'], 'TARGET_MOUNTED')
        self.config(identity='fixture:phone-a')
        code, refused = self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'],
                                        '--expected-manifest-sha256', '0' * 64, '--confirm')
        self.assertEqual(refused['error']['code'], 'BACKUP_CHANGED')
        self.assertEqual((self.root / 'parts' / 'efs').read_bytes(), bytes(PARTS['efs']))  # Nothing was written.

    def test_restore_refuses_a_different_layout(self):
        result = self.backup()
        (self.root / 'parts' / 'nvdata').write_bytes(bytes(PARTS['nvdata'] * 2))
        code, refused = self.restore(result, '--confirm')
        self.assertEqual(refused['error']['code'], 'GEOMETRY_MISMATCH')

    def test_a_copy_is_deleted_only_as_listed_and_only_on_a_writable_volume(self):
        """2026-11-11, TWRP's Restore page: delete a copy. Only the folder the page listed."""
        first, second = self.backup(), self.backup()
        folder = self.root / 'sd' / 'A22xIdentityBackups'
        keep = folder / 'notes'
        keep.mkdir()
        (keep / 'mine.txt').write_text('mine')
        code, refused = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', first['path'],
                                        '--expected-manifest-sha256', first['manifest_sha256'])
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        code, refused = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', first['path'],
                                        '--expected-manifest-sha256', '0' * 64, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'BACKUP_CHANGED'))
        for path in ('/A22xIdentityBackups/notes', '/A22xIdentityBackups', '/', '/other/x', '/A22xIdentityBackups/../sd'):
            code, refused = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', path,
                                            '--expected-manifest-sha256', first['manifest_sha256'], '--confirm')
            self.assertEqual(code, 2, path)
        self.assertTrue((keep / 'mine.txt').exists() and (self.root / 'sd' / first['path'].lstrip('/')).is_dir())
        code, done = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', first['path'],
                                     '--expected-manifest-sha256', first['manifest_sha256'], '--confirm')
        self.assertEqual((code, done['deleted'], done['partitions']), (0, first['path'], 4))
        self.assertFalse((self.root / 'sd' / first['path'].lstrip('/')).exists())
        code, listed = self.run_helper('backups', '--volume', 'sdcard-p1')
        self.assertEqual([b['path'] for b in listed['backups']], [second['path']])
        self.assertTrue((keep / 'mine.txt').exists())
        # The same copy on a volume mounted read-only stays.
        shutil.copytree(self.root / 'sd' / 'A22xIdentityBackups', self.root / 'usb' / 'A22xIdentityBackups')
        code, refused = self.run_helper('delete', '--volume', 'usb-p1', '--path', second['path'],
                                        '--expected-manifest-sha256', second['manifest_sha256'], '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'VOLUME_READ_ONLY'))
        self.assertTrue((self.root / 'usb' / second['path'].lstrip('/')).is_dir())

    def test_backups_lists_folders_newest_first(self):
        code, empty = self.run_helper('backups', '--volume', 'sdcard-p1')
        self.assertEqual((code, empty['backups']), (0, []))
        first = self.backup()
        second = self.backup()
        (self.root / 'sd' / 'A22xIdentityBackups' / 'junk').mkdir()
        code, listed = self.run_helper('backups', '--volume', 'sdcard-p1')
        self.assertEqual([b['path'] for b in listed['backups']], sorted([first['path'], second['path']], reverse=True))
        self.assertTrue(all(b['same_phone'] and b['partitions'] == 4 for b in listed['backups']))
        self.assertEqual({b['manifest_sha256'] for b in listed['backups']}, {first['manifest_sha256'], second['manifest_sha256']})

    def test_param_is_left_out_and_never_written_back_from_old_copies(self):
        self.assertNotIn('param', ib.LABELS)
        result = self.backup()
        folder = self.root / 'sd' / result['path'].lstrip('/')
        # An old copy that still holds param: it is checked, then skipped by restore.
        old = os.urandom(RETIRED['param'])
        (folder / 'param.img').write_bytes(old)
        manifest = json.loads((folder / 'manifest.json').read_text())
        manifest['partitions'].append({'label': 'param', 'file': 'param.img', 'size_bytes': len(old), 'partition_number': 99,
                                       'start_sector': 1, 'partuuid': 'x', 'sha256': hashlib.sha256(old).hexdigest()})
        (folder / 'manifest.json').write_text(json.dumps(manifest))
        sha = hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest()
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertTrue(check['restorable'])
        (self.root / 'parts' / 'efs').write_bytes(bytes(PARTS['efs']))
        code, done = self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'],
                                     '--expected-manifest-sha256', sha, '--confirm')
        self.assertEqual((code, done['restored']), (0, ['efs']))

    def test_paths_outside_the_backup_folder_are_refused(self):
        self.backup()
        for path in ('/etc', '/A22xIdentityBackups', '/A22xIdentityBackups/../x', '/other/identity-1'):
            code, refused = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', path)
            self.assertEqual(code, 2)
            self.assertIn(refused['error']['code'], ('INVALID_BACKUP_PATH', 'INVALID_PATH'))


if __name__ == '__main__':
    unittest.main()


class AndroidPartitionsBackup(unittest.TestCase):
    """2026-11-10: the same machinery for an Android installation's partitions (--kind android)."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-android-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('parts', 'sd', 'data'):
            (self.root / name).mkdir()
        self.data = {'boot': os.urandom(1 << 16), 'vbmeta': os.urandom(1 << 12),
                     'super': bytes(1 << 21) + os.urandom(1 << 12) + bytes(1 << 21)}
        for label, data in self.data.items():
            (self.root / 'parts' / label).write_bytes(data)
        self.config('fixture:phone-a')
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': False},
            {'id': 'userdata', 'path': 'data', 'mounted': True, 'read_only': False}]}))

    def config(self, identity):
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': identity},
            'identity_parts': {}, 'android_parts': {label: {'path': 'parts/' + label} for label in self.data}}))

    def run_helper(self, *args, labels=None):
        out, err = io.StringIO(), io.StringIO()
        extra = ['--labels', labels] if labels else []
        # The folder copies made before 2026-11-11 stay readable: these tests make one as it was made then.
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(ib.KINDS['android'], {'ARCHIVE': False}):
            code = ib.main(['--fixture', str(self.root), '--kind', 'android', *extra, *args])
        return code, json.loads(out.getvalue().splitlines()[-1])

    def test_chosen_partitions_are_saved_with_holes_and_checked(self):
        code, listed = self.run_helper('list')
        self.assertEqual([p['label'] for p in listed['partitions']], ['boot', 'vbmeta', 'super'])
        code, result = self.run_helper('backup', '--volume', 'sdcard-p1', labels='boot,super')
        self.assertEqual(code, 0, result)
        self.assertTrue(result['path'].startswith('/A22xAndroidBackups/android-'))
        folder = self.root / 'sd' / result['path'].lstrip('/')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['boot.img', 'manifest.json', 'super.img'])
        self.assertEqual((folder / 'super.img').read_bytes(), self.data['super'])
        self.assertLess((folder / 'super.img').stat().st_blocks * 512, len(self.data['super']) // 2)  # holes
        code, verified = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertTrue(verified['verified'] and verified['restorable'], verified)

    def test_userdata_is_no_destination(self):
        code, result = self.run_helper('backup', '--volume', 'userdata')
        self.assertEqual((code, result['error']['code']), (2, 'DESTINATION_NOT_ALLOWED'))
        code, result = self.run_helper('list', labels='lk')
        self.assertEqual((code, result['error']['code']), (2, 'INVALID_ARGUMENT'))  # never the bootloader

    def test_a_copy_goes_back_to_a_phone_with_the_same_layout(self):
        code, result = self.run_helper('backup', '--volume', 'sdcard-p1')
        self.assertEqual(code, 0, result)
        self.config('fixture:phone-b')  # another A22x: nothing of one phone is in these partitions
        code, listed = self.run_helper('backups', '--volume', 'sdcard-p1')
        self.assertTrue(listed['backups'][0]['same_phone'])
        sha = listed['backups'][0]['manifest_sha256']
        code, same = self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'], '--expected-manifest-sha256', sha, '--confirm')
        self.assertTrue(same['identical'], same)
        (self.root / 'parts' / 'boot').write_bytes(os.urandom(1 << 16))
        code, done = self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'], '--expected-manifest-sha256', sha, '--confirm')
        self.assertEqual((code, done['restored']), (0, ['boot']), done)
        self.assertEqual((self.root / 'parts' / 'boot').read_bytes(), self.data['boot'])
        self.assertTrue(done['before'].startswith('/A22xAndroidBackups/before-restore-'))
        ib.use('identity')


def avb_vbmeta(flags, size=1 << 12):
    """A vbmeta as far as this helper reads it: the magic and the flags."""
    data = bytearray(os.urandom(size))
    data[:4] = b'AVB0'
    struct.pack_into('>I', data, 120, flags)
    return bytes(data)


@unittest.skipUnless(shutil.which('zstd'), 'zstd is not installed')
class AndroidArchive(unittest.TestCase):
    """2026-11-11: an Android copy is one compressed archive, restored piece by piece."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-archive-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(ib.use, 'identity')
        for name in ('parts', 'sd', 'usb', 'data', 'cache'):
            (self.root / name).mkdir()
        self.data = {'boot': os.urandom(3 << 20), 'vbmeta': avb_vbmeta(2),
                     'super': bytes(4 << 20) + os.urandom(1 << 20) + bytes(3 << 20)}
        for label, data in self.data.items():
            (self.root / 'parts' / label).write_bytes(data)
        self.config()
        self.volumes(usb_mounted=False)

    def config(self, locked=()):
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:phone-a'},
            'identity_parts': {}, 'android_parts': {label: {'path': 'parts/' + label} for label in self.data},
            'write_protected': list(locked)}))

    def volumes(self, usb_mounted, sd_read_only=False):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'sdcard-p1', 'path': 'sd', 'label': 'card', 'mounted': True, 'read_only': sd_read_only},
            {'id': 'usb-p1', 'path': 'usb', 'fs_type': 'exfat', 'mounted': usb_mounted, 'read_only': True},
            {'id': 'cache', 'path': 'cache', 'mounted': True, 'read_only': True},
            {'id': 'userdata', 'path': 'data', 'mounted': True, 'read_only': False}]}))

    def run_helper(self, *args, labels=None):
        out, err = io.StringIO(), io.StringIO()
        extra = ['--labels', labels] if labels else []
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ib.main(['--fixture', str(self.root), '--kind', 'android', *extra, *args])
        self.progress = [json.loads(line) for line in err.getvalue().splitlines() if line.startswith('{')]
        return code, json.loads(out.getvalue().splitlines()[-1])

    def backup(self, **kwargs):
        code, result = self.run_helper('backup', '--volume', 'sdcard-p1', **kwargs)
        self.assertEqual(code, 0, result)
        return result

    def restore(self, result, **kwargs):
        return self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'],
                               '--expected-manifest-sha256', result['manifest_sha256'], '--confirm', **kwargs)

    def part(self, label):
        return (self.root / 'parts' / label).read_bytes()

    def test_a_copy_is_one_archive_that_tar_and_zstd_unpack(self):
        result = self.backup()
        self.assertTrue(result['path'].startswith('/A22xAndroidBackups/android-') and result['path'].endswith('.tar.zst'), result)
        folder = self.root / 'sd' / 'A22xAndroidBackups'
        self.assertEqual([p.name for p in folder.iterdir()], [result['path'].rsplit('/', 1)[-1]])  # nothing half-written is left
        file = self.root / 'sd' / result['path'].lstrip('/')
        self.assertEqual(result['file_bytes'], file.stat().st_size)
        self.assertLess(result['file_bytes'], result['bytes'] // 2)  # the empty space of super takes almost none
        # Any computer unpacks it: zstd skips the frame that holds the manifest at the end.
        plain = subprocess.run(['zstd', '-q', '-d', '-c', str(file)], stdout=subprocess.PIPE, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
            self.assertEqual(tar.getnames(), ['boot.img', 'vbmeta.img', 'super.img', 'manifest.json'])
            for label, data in self.data.items():
                self.assertEqual(tar.extractfile(label + '.img').read(), data)
            inside = tar.extractfile('manifest.json').read()
        with open(file, 'rb') as handle:
            self.assertEqual(pa.footer(handle.fileno()), inside)
        self.assertEqual(hashlib.sha256(inside).hexdigest(), result['manifest_sha256'])
        manifest = json.loads(inside)
        self.assertEqual(manifest['container'], 'tar.zst')
        self.assertEqual({r['label']: r['sha256'] for r in manifest['partitions']},
                         {label: hashlib.sha256(data).hexdigest() for label, data in self.data.items()})
        code, verified = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertTrue(code == 0 and verified['verified'] and verified['restorable'] and verified['archive'], verified)
        self.assertIn('Checking the backup', {p['phase'] for p in self.progress})
        # Saving names its last step too: what zstd still holds goes to the card then.
        code, again = self.run_helper('backup', '--volume', 'sdcard-p1')
        self.assertEqual([p['phase'] for p in self.progress if p['phase'] != 'Reading'][0], 'Finishing the archive')

    def test_userdata_and_a_read_only_card_take_no_archive(self):
        code, result = self.run_helper('backup', '--volume', 'userdata')
        self.assertEqual((code, result['error']['code']), (2, 'DESTINATION_NOT_ALLOWED'))
        self.volumes(usb_mounted=True)
        code, result = self.run_helper('backup', '--volume', 'usb-p1')
        self.assertEqual((code, result['error']['code']), (2, 'VOLUME_READ_ONLY'))

    def test_copies_come_from_every_mounted_card_and_name_the_others(self):
        first = self.backup()
        with mock.patch.dict(ib.KINDS['android'], {'ARCHIVE': False}):  # a folder copy, as made before
            old = self.backup(labels='boot')
        code, found = self.run_helper('copies')
        self.assertEqual(code, 0, found)
        self.assertEqual({(row['path'], row['archive']) for row in found['backups']}, {(first['path'], True), (old['path'], False)})
        row = next(r for r in found['backups'] if r['archive'])
        self.assertEqual((row['volume'], row['volume_label'], row['writable'], row['labels'], row['partitions']),
                         ('sdcard-p1', 'card', True, ['boot', 'vbmeta', 'super'], 3))
        self.assertEqual(row['manifest_sha256'], first['manifest_sha256'])
        self.assertEqual(row['file_bytes'], first['file_bytes'])
        self.assertEqual([(v['volume'], v['fs_type']) for v in found['unmounted']], [('usb-p1', 'exfat')])
        self.assertNotIn('userdata', {r['volume'] for r in found['backups']})
        # The same copies on the stick appear once it is mounted; a card mounted read-only says so.
        shutil.copytree(self.root / 'sd' / 'A22xAndroidBackups', self.root / 'usb' / 'A22xAndroidBackups')
        self.volumes(usb_mounted=True)
        code, found = self.run_helper('copies')
        self.assertEqual(sorted((r['volume'], r['writable']) for r in found['backups']),
                         [('sdcard-p1', True), ('sdcard-p1', True), ('usb-p1', False), ('usb-p1', False)])
        self.assertEqual(found['unmounted'], [])

    def test_a_damaged_or_cut_archive_is_found(self):
        result = self.backup()
        file = self.root / 'sd' / result['path'].lstrip('/')
        good = file.read_bytes()
        damaged = bytearray(good)
        damaged[len(good) // 3] ^= 0x40
        file.write_bytes(bytes(damaged))
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertEqual((code, check['error']['code']), (2, 'BACKUP_DAMAGED'), check)
        # Nothing is written from an archive that does not check out.
        (self.root / 'parts' / 'boot').write_bytes(bytes(3 << 20))
        code, refused = self.restore(result)
        self.assertEqual((code, refused['error']['code']), (2, 'BACKUP_DAMAGED'), refused)
        self.assertEqual(self.part('boot'), bytes(3 << 20))
        # Cut short, it has no manifest at its end and is no copy at all.
        file.write_bytes(good[:len(good) - 40])
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertEqual((code, check['error']['code']), (2, 'BACKUP_DAMAGED'))
        code, found = self.run_helper('copies')
        self.assertEqual(found['backups'], [])

    def test_restore_writes_only_the_pieces_that_differ(self):
        result = self.backup()
        code, same = self.restore(result)
        self.assertEqual(code, 0, same)
        self.assertTrue(same['identical'] and same['restored'] == [] and same['written_bytes'] == 0, same)
        self.assertEqual(sorted(same['unchanged']), ['boot', 'super', 'vbmeta'])
        changed = bytearray(self.data['super'])
        changed[(5 << 20) + 17] ^= 1  # one byte in the sixth megabyte
        (self.root / 'parts' / 'super').write_bytes(bytes(changed))
        (self.root / 'parts' / 'boot').write_bytes(os.urandom(3 << 20))
        code, done = self.restore(result)
        self.assertEqual(code, 0, done)
        self.assertEqual((done['restored'], done['unchanged'], done['left_locked']), (['boot', 'super'], ['vbmeta'], []))
        self.assertEqual(done['written_bytes'], (3 << 20) + (1 << 20))
        for label, data in self.data.items():
            self.assertEqual(self.part(label), data)
        self.assertEqual([p['phase'] for p in self.progress if p['partition'] == 'super' and p['bytes_done'] == p['bytes_total']],
                         ['Checking the backup', 'Writing', 'Reading'])

    def test_chosen_partitions_only(self):
        result = self.backup()
        (self.root / 'parts' / 'boot').write_bytes(bytes(3 << 20))
        (self.root / 'parts' / 'super').write_bytes(bytes(8 << 20))
        code, done = self.restore(result, labels='boot')
        self.assertEqual((code, done['restored']), (0, ['boot']), done)
        self.assertEqual(self.part('boot'), self.data['boot'])
        self.assertEqual(self.part('super'), bytes(8 << 20))
        code, refused = self.restore(result, labels='dtbo')  # not in this copy
        self.assertEqual((code, refused['error']['code']), (2, 'INVALID_ARGUMENT'))

    def test_what_the_bootloader_locks_is_left_as_it_is(self):
        result = self.backup()
        self.config(locked=('vbmeta',))
        code, same = self.restore(result)
        self.assertEqual((code, same['left_locked'], same['identical']), (0, [], True), same)
        # Another vbmeta on the phone, verification off: Android starts with it, so the rest is restored.
        other = avb_vbmeta(3)
        (self.root / 'parts' / 'vbmeta').write_bytes(other)
        (self.root / 'parts' / 'boot').write_bytes(bytes(3 << 20))
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertEqual((check['locked'], check['vbmeta_blocks']), (['vbmeta'], False), check)
        code, done = self.restore(result)
        self.assertEqual((code, done['restored'], done['left_locked'], done['identical']), (0, ['boot'], ['vbmeta'], False), done)
        self.assertEqual(self.part('vbmeta'), other)
        # Verification on: the copy would not start, and nothing is written.
        strict = avb_vbmeta(0)
        (self.root / 'parts' / 'vbmeta').write_bytes(strict)
        (self.root / 'parts' / 'boot').write_bytes(bytes(3 << 20))
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertTrue(check['vbmeta_blocks'], check)
        code, refused = self.restore(result)
        self.assertEqual((code, refused['error']['code']), (2, 'VBMETA_IN_DOWNLOAD_MODE'), refused)
        self.assertEqual(self.part('boot'), bytes(3 << 20))

    def test_a_changed_copy_and_another_layout_are_refused(self):
        result = self.backup()
        code, refused = self.run_helper('restore', '--volume', 'sdcard-p1', '--path', result['path'],
                                        '--expected-manifest-sha256', '0' * 64, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'BACKUP_CHANGED'))
        (self.root / 'parts' / 'vbmeta').write_bytes(avb_vbmeta(2, 1 << 13))  # another size: another layout
        code, refused = self.restore(result)
        self.assertEqual((code, refused['error']['code']), (2, 'GEOMETRY_MISMATCH'))
        code, check = self.run_helper('verify', '--volume', 'sdcard-p1', '--path', result['path'])
        self.assertTrue(check['verified'] and not check['restorable'], check)

    def test_delete_removes_the_archive(self):
        result = self.backup()
        self.volumes(usb_mounted=False, sd_read_only=True)
        code, refused = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', result['path'],
                                        '--expected-manifest-sha256', result['manifest_sha256'], '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'VOLUME_READ_ONLY'))
        self.volumes(usb_mounted=False)
        code, gone = self.run_helper('delete', '--volume', 'sdcard-p1', '--path', result['path'],
                                     '--expected-manifest-sha256', result['manifest_sha256'], '--confirm')
        self.assertEqual((code, gone['deleted'], gone['partitions']), (0, result['path'], 3), gone)
        self.assertEqual(list((self.root / 'sd' / 'A22xAndroidBackups').iterdir()), [])

    def test_a_restored_android_boot_keeps_this_recovery_and_the_old_boot(self):
        from test_android_gsi import stock_boot
        from test_flash import boot_image
        from test_keep_recovery import signed
        import saved_boot
        stock, linux = signed(stock_boot()), signed(boot_image())
        self.data['boot'] = stock
        (self.root / 'parts' / 'boot').write_bytes(stock)
        self.config()
        result = self.backup()
        (self.root / 'parts' / 'boot').write_bytes(linux)  # Linux was installed since
        code, done = self.restore(result)
        self.assertEqual((code, done['restored'], done['keeps_recovery']), (0, ['boot'], True), done)
        now = self.part('boot')
        spot = len(stock) - saved_boot.KEEP_RECOVERY_FROM_END
        mark = saved_boot.KEEP_RECOVERY_MARK
        self.assertEqual(now[spot:spot + len(mark)], mark)
        self.assertEqual(now[:spot] + bytes(len(mark)) + now[spot + len(mark):], stock)  # nothing else differs
        # The Linux BOOT that was there is on cache, the way back.
        self.assertTrue(done['kept_boot'] and done['kept_boot'].startswith('/a22x-recovery/saved-boot/'), done)
        self.assertEqual((self.root / 'cache' / done['kept_boot'].lstrip('/')).read_bytes(), linux)
        # Restored once more, the mark is all that differs: nothing is written, and the mark stays.
        code, again = self.restore(result)
        self.assertEqual((code, again['restored'], again['identical'], again['written_bytes']), (0, [], True, 0), again)
        self.assertEqual(self.part('boot'), now)


class AndroidData(AndroidArchive):
    """2026-11-11, the owner: "so that the data too can be saved and put back without decryption".
    metadata and userdata are in a copy only when asked for by name, only together, and go back the
    same way. Everything a copy without them does holds on a phone that has them (the tests above)."""
    def setUp(self):
        super().setUp()
        # Android's data as the cell holds it: a little that is written, the rest erased.
        self.extra = {'metadata': os.urandom(1 << 20) + bytes(1 << 20),
                      'userdata': os.urandom(2 << 20) + bytes(9 << 20) + os.urandom(1 << 20) + bytes(4 << 20)}
        for label, data in self.extra.items():
            (self.root / 'parts' / label).write_bytes(data)
        self.config()

    def config(self, locked=(), **more):
        labels = list(self.data) + list(getattr(self, 'extra', ()))
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:phone-a'},
            'identity_parts': {}, 'android_parts': {label: {'path': 'parts/' + label} for label in labels},
            'write_protected': list(locked), **more}))

    ALL = 'boot,vbmeta,super,metadata,userdata'

    def test_the_list_marks_the_data_as_optional(self):
        code, result = self.run_helper('list')
        self.assertEqual(code, 0, result)
        self.assertEqual([(p['label'], p['optional']) for p in result['partitions']],
                         [('boot', False), ('vbmeta', False), ('super', False), ('metadata', True), ('userdata', True)])
        self.assertEqual(result['bytes'], sum(len(d) for d in self.data.values()))  # what a plain copy holds

    def test_a_plain_copy_leaves_the_data_out_and_a_plain_restore_leaves_it_alone(self):
        result = self.backup()
        self.assertEqual([r['label'] for r in result['partitions']], ['boot', 'vbmeta', 'super'])
        whole = self.backup(labels=self.ALL)
        (self.root / 'parts/userdata').write_bytes(os.urandom(16 << 20))
        (self.root / 'parts/boot').write_bytes(os.urandom(3 << 20))
        changed = self.part('userdata')
        code, done = self.restore(whole)  # no --labels: the data is not asked for
        self.assertEqual((code, done['restored']), (0, ['boot']), done)
        self.assertEqual(self.part('userdata'), changed)

    def test_the_data_is_saved_and_goes_back_only_together(self):
        for labels in ('boot,userdata', 'metadata', 'boot,vbmeta,super,userdata'):
            code, result = self.run_helper('backup', '--volume', 'sdcard-p1', labels=labels)
            self.assertEqual((code, result['error']['code']), (2, 'DATA_PAIR'), labels)
        self.assertFalse((self.root / 'sd/A22xAndroidBackups').exists() and list((self.root / 'sd/A22xAndroidBackups').iterdir()))
        whole = self.backup(labels=self.ALL)
        self.assertEqual([r['label'] for r in whole['partitions']], ['boot', 'vbmeta', 'super', 'metadata', 'userdata'])
        for labels in ('userdata', 'boot,metadata'):
            code, result = self.restore(whole, labels=labels)
            self.assertEqual((code, result['error']['code']), (2, 'DATA_PAIR'), labels)
        # A copy made without the data has none to give.
        plain = self.backup()
        code, result = self.restore(plain, labels='boot,metadata,userdata')
        self.assertEqual((code, result['restored'], result['unchanged']), (0, [], ['boot']), result)

    def test_the_data_alone_is_a_copy_too(self):
        result = self.backup(labels='metadata,userdata')
        self.assertEqual([r['label'] for r in result['partitions']], ['metadata', 'userdata'])
        self.assertLess(result['file_bytes'], 5 << 20)  # 4 MiB are written of 18: what was erased takes nothing

    def test_data_put_back_over_another_system_zeros_what_was_erased(self):
        """Android's data restored over what a Linux system wrote there: the written pieces are
        written, the erased ones are made zeros by the kernel in whole runs."""
        whole = self.backup(labels=self.ALL)
        (self.root / 'parts/userdata').write_bytes(os.urandom(16 << 20))
        (self.root / 'parts/metadata').write_bytes(os.urandom(2 << 20))
        seen = []
        real = ib.Fixture.zero

        def zero(context, fd, start, length):
            seen.append((start, length))
            return real(context, fd, start, length)

        with mock.patch.object(ib.Fixture, 'zero', zero):
            code, done = self.restore(whole, labels='metadata,userdata')
        self.assertEqual((code, done['restored'], done['unchanged']), (0, ['metadata', 'userdata'], []), done)
        self.assertEqual({label: self.part(label) for label in self.extra}, self.extra)
        self.assertEqual(seen, [(1 << 20, 1 << 20), (2 << 20, 9 << 20), (12 << 20, 4 << 20)])
        self.assertEqual(done['written_bytes'], (2 << 20) + (16 << 20))
        self.assertEqual(self.part('boot'), self.data['boot'])  # not asked for, not touched
        # Again over itself: nothing is written, nothing zeroed.
        seen.clear()
        with mock.patch.object(ib.Fixture, 'zero', zero):
            code, done = self.restore(whole, labels='metadata,userdata')
        self.assertEqual((code, done['identical'], done['written_bytes'], seen), (0, True, 0, []), done)

    def test_a_kernel_that_cannot_zero_gets_the_zeros_written(self):
        whole = self.backup(labels=self.ALL)
        (self.root / 'parts/userdata').write_bytes(os.urandom(16 << 20))
        self.config(no_zeroing=True)
        code, done = self.restore(whole, labels='metadata,userdata')
        self.assertEqual((code, done['restored']), (0, ['userdata']), done)
        self.assertEqual(self.part('userdata'), self.extra['userdata'])

    def test_a_long_run_of_zeros_is_made_in_pieces(self):
        whole = self.backup(labels=self.ALL)
        (self.root / 'parts/userdata').write_bytes(os.urandom(16 << 20))
        seen = []
        with mock.patch.object(ib, 'ZERO_RUN', 4 << 20), \
                mock.patch.object(ib.Fixture, 'zero', lambda context, fd, start, length: seen.append((start, length)) or False):
            code, done = self.restore(whole, labels='metadata,userdata')
        self.assertEqual(code, 0, done)
        self.assertEqual(seen, [(2 << 20, 4 << 20), (6 << 20, 4 << 20), (10 << 20, 1 << 20), (12 << 20, 4 << 20)])
        self.assertEqual(self.part('userdata'), self.extra['userdata'])

    def test_data_in_use_is_not_copied(self):
        self.config(identity_busy=['userdata'])
        code, result = self.run_helper('backup', '--volume', 'sdcard-p1', labels=self.ALL)
        self.assertEqual((code, result['error']['code']), (2, 'TARGET_MOUNTED'), result)
        self.assertEqual(self.backup()['archive'], True)  # the partitions of Android are, as before


class ArchiveRoom(unittest.TestCase):
    """2026-11-11, the owner: a copy ran out of room on the card after minutes of writing. The size
    is estimated from samples before the first byte is written."""
    config, volumes, run_helper, backup = AndroidArchive.config, AndroidArchive.volumes, AndroidArchive.run_helper, AndroidArchive.backup

    def setUp(self):
        AndroidArchive.setUp(self)
        # A super large enough to be sampled: a part that packs to nothing, a part that does not pack.
        self.data['super'] = bytes(40 << 20) + os.urandom(24 << 20)
        (self.root / 'parts/super').write_bytes(self.data['super'])

    def test_the_estimate_is_near_what_the_archive_takes(self):
        with mock.patch.object(ib, 'SAMPLE_WHOLE', 8 << 20), mock.patch.object(ib, 'SAMPLE_PIECES', 32):
            result = self.backup()
        self.assertTrue(any(p['phase'] == 'Estimating the size' and p['partition'] == 'super' for p in self.progress))
        self.assertFalse(any(p['phase'] == 'Estimating the size' and p['partition'] == 'boot' for p in self.progress))
        self.assertLess(abs(result['estimated_bytes'] - result['file_bytes']), 4 << 20, result)

    def test_too_little_room_is_said_before_anything_is_written(self):
        with mock.patch.object(ib, 'SAMPLE_WHOLE', 8 << 20), mock.patch.object(ib, 'SAMPLE_PIECES', 32), \
                mock.patch.object(ib, 'free_bytes', return_value=20 << 20):
            code, result = self.run_helper('backup', '--volume', 'sdcard-p1')
        self.assertEqual((code, result['error']['code']), (2, 'NO_SPACE'), result)
        details = result['error']['details']
        self.assertEqual(details['free_bytes'], 20 << 20)
        self.assertGreater(details['required_bytes'], details['estimated_bytes'] + ib.MIN_ARCHIVE_ROOM - 1)
        self.assertLess(abs(details['estimated_bytes'] - (27 << 20)), 5 << 20, details)  # boot 3 MiB + what does not pack of super
        self.assertFalse((self.root / 'sd/A22xAndroidBackups').exists())
        self.assertFalse(any(p['phase'] == 'Reading' for p in self.progress))

    def test_the_room_asked_for_covers_the_sampling_error(self):
        """Few pieces of a partition that is mostly empty say little: the room asked for grows by
        twice the error, never beyond the partition itself."""
        ib.use('android')
        context = ib.Fixture(self.root)
        parts = [p for p in context.parts() if p['label'] == 'super']
        with mock.patch.object(ib, 'SAMPLE_WHOLE', 8 << 20), mock.patch.object(ib, 'SAMPLE_PIECES', 16):
            likely, need = ib.packed_estimate(context, parts, lambda *a: None)
        size = len(self.data['super'])
        self.assertLess(abs(likely - (24 << 20)), 6 << 20)
        self.assertGreater(need, likely + ib.MIN_ARCHIVE_ROOM)
        self.assertLessEqual(need, int(size * ib.ESTIMATE_MARGIN) + ib.MIN_ARCHIVE_ROOM)

    def test_fat_is_told_before_the_copy_starts(self):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'sdcard-p1', 'path': 'sd', 'label': 'card', 'fs_type': 'vfat', 'mounted': True, 'read_only': False},
            {'id': 'cache', 'path': 'cache', 'mounted': True, 'read_only': True},
            {'id': 'userdata', 'path': 'data', 'mounted': True, 'read_only': False}]}))
        with mock.patch.object(ib, 'FAT_FILE_LIMIT', 16 << 20), mock.patch.object(ib, 'SAMPLE_WHOLE', 8 << 20):
            code, result = self.run_helper('backup', '--volume', 'sdcard-p1')
        self.assertEqual((code, result['error']['code']), (2, 'FILE_TOO_LARGE'), result)
        self.assertFalse((self.root / 'sd/A22xAndroidBackups').exists())
