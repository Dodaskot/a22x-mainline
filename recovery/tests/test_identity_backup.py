"""Identity (EFS/NV) backup and restore on fixture files; no block device is opened."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import identity_backup as ib

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
