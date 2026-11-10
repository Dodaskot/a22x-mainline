"""Backups on a USB stick: filesystem rules and the stable identity of the medium."""
import ast
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
import filesystem as fs
import linux_workflow
from common import RecoveryError


class Contexts(unittest.TestCase):
    """The fixture stands in for the phone, so a method only it has passes here and crashes there."""

    def test_every_method_a_context_calls_exists_on_the_device_context(self):
        tree = ast.parse((ROOT / 'filesystem.py').read_text())
        missing = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in ('BaseContext', 'DeviceContext'):
                owner, name = fs.DeviceContext, 'self'
            elif isinstance(node, ast.FunctionDef) and node.name in ('dispatch', 'main') or (
                    isinstance(node, ast.FunctionDef) and 'context' in [a.arg for a in node.args.args]):
                owner, name = fs.DeviceContext, 'context'
            else:
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == name
                        and not hasattr(owner, call.func.attr)):
                    missing.add((node.name, call.func.attr))
        self.assertEqual(missing, set())

    def test_both_contexts_offer_the_same_operations(self):
        for name in ('usb_volumes', 'sync_usb', 'prepare_usb_backup', 'eject_usb', 'unmount_all', 'mount', 'unmount'):
            self.assertTrue(callable(getattr(fs.DeviceContext, name, None)), name)
            self.assertTrue(callable(getattr(fs.FixtureContext, name, None)), name)


class Stick(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-usb-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        rows = []
        for ident, kind in (('usb-p1', 'exfat'), ('usb-p2', 'vfat'), ('usb-p3', 'ext4'), ('sdcard-p1', 'ext4')):
            (self.root / ident).mkdir()
            rows.append({'id': ident, 'path': ident, 'fs_type': kind, 'mounted': True, 'read_only': True})
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': rows}))
        self.context = fs.FixtureContext(self.root)

    def test_exfat_and_ext4_become_writable_with_a_backup_folder(self):
        for ident in ('usb-p1', 'usb-p3'):
            result = self.context.prepare_usb_backup(ident, True)
            self.assertTrue(Path(result['repository']).is_dir())
            self.assertEqual(Path(result['repository']).name, 'A22xBackups')
            self.assertFalse(self.context.volume(ident)['read_only'])

    def test_preparing_twice_keeps_existing_backups(self):
        folder = Path(self.context.prepare_usb_backup('usb-p1', True)['repository'])
        (folder / 'old-backup').mkdir()
        self.context.prepare_usb_backup('usb-p1', True)
        self.assertTrue((folder / 'old-backup').is_dir())

    def test_fat32_is_refused_before_anything_changes(self):
        with self.assertRaises(fs.FilesystemError) as caught:
            self.context.prepare_usb_backup('usb-p2', True)
        self.assertEqual(caught.exception.code, 'BACKUP_FILESYSTEM_UNSUITED')
        self.assertTrue(self.context.volume('usb-p2')['read_only'])
        self.assertFalse((self.root / 'usb-p2/A22xBackups').exists())

    def test_only_a_stick_can_be_prepared_and_writes_need_confirmation(self):
        with self.assertRaises(fs.FilesystemError) as caught:
            self.context.prepare_usb_backup('sdcard-p1', True)
        self.assertEqual(caught.exception.code, 'NOT_USB_VOLUME')
        with self.assertRaises(fs.FilesystemError):
            self.context.prepare_usb_backup('usb-p1', False)
        self.assertFalse((self.root / 'usb-p1/A22xBackups').exists())

    def test_eject_unmounts_sticks_and_leaves_the_sd_alone(self):
        self.assertEqual(self.context.eject_usb()['volumes'], [])
        for ident in ('usb-p1', 'usb-p2', 'usb-p3'):
            self.assertFalse(self.context.volume(ident)['mounted'])
        self.assertTrue(self.context.volume('sdcard-p1')['mounted'])

    def test_sync_reports_mounted_sticks_only(self):
        ids = [item['id'] for item in self.context.sync_usb()['volumes']]
        self.assertEqual(ids, ['usb-p1', 'usb-p2', 'usb-p3'])


class Identity(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-id-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def stick(self, serial, size='60825600'):
        usb = self.root / ('usb-' + serial)
        scsi = usb / '1-1:1.0/host0/0:0:0:0'
        disk = scsi / 'block/sda'
        disk.mkdir(parents=True)
        (disk / 'device').symlink_to(scsi)
        (disk / 'size').write_text(size + '\n')
        (usb / 'idVendor').write_text('0781\n')
        (usb / 'idProduct').write_text('5567\n')
        (usb / 'serial').write_text(serial + '\n')
        return disk

    def test_sd_keeps_its_cid(self):
        disk = self.root / 'mmcblk1'
        (disk / 'device').mkdir(parents=True)
        (disk / 'device/cid').write_text('0123\n')
        self.assertTrue(linux_workflow._media_identity(disk).startswith('sd-cid-sha256:'))

    def test_a_stick_is_named_by_its_ids_and_serial(self):
        one = linux_workflow._media_identity(self.stick('AAA'))
        self.assertTrue(one.startswith('usb-sha256:'))
        self.assertEqual(one, linux_workflow._media_identity(self.root / 'usb-AAA/1-1:1.0/host0/0:0:0:0/block/sda'))
        self.assertNotEqual(one, linux_workflow._media_identity(self.stick('BBB')))

    def test_a_medium_without_identity_is_refused(self):
        disk = self.root / 'loop0'
        (disk / 'device').mkdir(parents=True)
        with self.assertRaises(RecoveryError) as caught:
            linux_workflow._media_identity(disk)
        self.assertEqual(caught.exception.code, 'REPOSITORY_IDENTITY_MISSING')


class Filesystem(unittest.TestCase):
    def check(self, filesystem):
        class Graph:
            def require_no_holders(self, device):
                pass

        class Context:
            mode, graph = 'device', Graph()
            def assert_recovery(self):
                pass
            def assert_offline(self, parts, repo):
                pass
        linux_workflow._offline(Context(), [], {'filesystem': filesystem})

    def test_ext4_and_exfat_hold_a_large_archive_and_fat32_does_not(self):
        self.check('ext4')
        self.check('exfat')
        with self.assertRaises(RecoveryError) as caught:
            self.check('vfat')
        self.assertEqual(caught.exception.code, 'LINUX_REPOSITORY_UNQUALIFIED_FS')


if __name__ == '__main__':
    unittest.main()
