"""Meaningful filesystem fixtures; never mount or write a physical block device."""
import contextlib
import errno
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('a22x_filesystem', Path(__file__).parents[1] / 'filesystem.py')
fs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fs)


class FilesystemFixtureSupport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a22x-files-')
        self.root = Path(self.temp.name)
        self.data = self.root / 'volumes/data'
        self.sd = self.root / 'volumes/sd'
        self.data.mkdir(parents=True)
        self.sd.mkdir()
        self.config = {'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'volumes/data', 'fs_type': 'ext4', 'mounted': True, 'read_only': False},
            {'id': 'sdcard-p1', 'path': 'volumes/sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': False}]}
        self.write_config()
        self.ctx = fs.FixtureContext(self.root)
        self.source = self.data / 'config.txt'
        self.source.write_text('before\n')
        self.text = self.root / 'input.txt'
        self.text.write_text('after\n')

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(self.config))

    def cli(self, *arguments, ok=True):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rc = fs.main(['--fixture', str(self.root), *arguments])
        result = json.loads(stream.getvalue())
        self.assertEqual(rc == 0, ok, result)
        self.assertEqual(result['ok'], ok, result)
        if ok:
            self.assertEqual(result['mode'], 'fixture', result)
        return result

    def original_hash(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def write(self, **overrides):
        args = dict(context=self.ctx, ident='userdata', path='/config.txt',
                    text_file=self.text, confirmed=True, expected_sha=self.original_hash())
        args.update(overrides)
        return fs.write_file(**args)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(fs.FilesystemError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)


class FilesystemFixtureTest(FilesystemFixtureSupport):
    def test_end_to_end_cli_roundtrip(self):
        self.cli('mount', '--volume', 'userdata', '--mode', 'ro')
        denied = self.cli('mkdir', '--volume', 'userdata', '--path', '/test', '--confirm', ok=False)
        self.assertEqual(denied['error']['code'], 'VOLUME_READ_ONLY')
        self.cli('mount', '--volume', 'userdata', '--mode', 'rw', '--confirm')
        self.cli('mkdir', '--volume', 'userdata', '--path', '/test', '--confirm')
        self.cli('write', '--volume', 'userdata', '--path', '/test/file.txt', '--text-file', str(self.text), '--create', '--confirm')
        read = self.cli('read', '--volume', 'userdata', '--path', '/test/file.txt')
        self.assertEqual(read['content'], 'after\n')
        self.cli('copy', '--volume', 'userdata', '--path', '/test/file.txt', '--destination-volume', 'sdcard-p1', '--destination', '/copy.txt', '--confirm')
        self.assertEqual((self.sd / 'copy.txt').read_bytes(), b'after\n')
        self.cli('rename', '--volume', 'sdcard-p1', '--path', '/copy.txt', '--destination', '/renamed.txt', '--confirm')
        self.cli('delete', '--volume', 'sdcard-p1', '--path', '/renamed.txt', '--confirm')
        self.assertFalse((self.sd / 'renamed.txt').exists())
        self.cli('unmount-all')
        self.assertFalse(any(v['mounted'] for v in self.cli('inventory')['volumes']))

    def test_mutation_requires_confirmation(self):
        self.assert_error('CONFIRMATION_REQUIRED', self.write, confirmed=False)
        self.assert_error('CONFIRMATION_REQUIRED', self.ctx.mount, 'userdata', 'rw')
        self.assertEqual(self.source.read_text(), 'before\n')

    def test_atomic_replace_preserves_owner_mode_xattr(self):
        self.source.chmod(0o640)
        before = self.source.stat()
        xattr = False
        try:
            os.setxattr(self.source, 'user.a22x-test', b'preserve-me')
            xattr = True
        except OSError as exc:
            if exc.errno not in (errno.EOPNOTSUPP, errno.ENOTSUP):
                raise
        result = self.write()
        after = self.source.stat()
        self.assertEqual(self.source.read_text(), 'after\n')
        self.assertNotEqual(before.st_ino, after.st_ino)
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        if xattr:
            self.assertEqual(os.getxattr(self.source, 'user.a22x-test'), b'preserve-me')
        self.assertEqual(result['sha256'], hashlib.sha256(b'after\n').hexdigest())
        self.assertFalse(list(self.data.glob('.a22x-edit-*')))

    def test_stale_hash_refuses_write(self):
        old = self.original_hash()
        self.source.write_text('external change\n')
        self.assert_error('FILE_CHANGED', self.write, expected_sha=old)
        self.assertEqual(self.source.read_text(), 'external change\n')

    def test_atomic_replace_removes_inherited_default_acl(self):
        # Linux ACL xattr ABI: version2 + {tag,permissions,id}; no setfacl dependency.
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, permissions, ident) for tag, permissions, ident in (
            (0x01, 7, 0xffffffff), (0x02, 7, os.getuid()), (0x04, 5, 0xffffffff),
            (0x10, 7, 0xffffffff), (0x20, 0, 0xffffffff)))
        try:
            os.setxattr(self.data, 'system.posix_acl_default', acl)
        except OSError as exc:
            if exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP):
                self.skipTest('This fixture filesystem has no POSIX ACL support')
            raise
        self.assertNotIn('system.posix_acl_access', os.listxattr(self.source))
        self.source.chmod(0o640)
        self.write()
        self.assertNotIn('system.posix_acl_access', os.listxattr(self.source))
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)

    def test_create_never_overwrites(self):
        self.assert_error('DESTINATION_EXISTS', self.write, create=True, expected_sha=None)
        self.assertEqual(self.source.read_text(), 'before\n')

    def test_replace_requires_hash(self):
        self.assert_error('INVALID_HASH', self.write, expected_sha=None)

    def test_hardlink_replace_is_not_silently_broken(self):
        os.link(self.source, self.data / 'hardlink')
        self.assert_error('HARDLINK_REPLACE_REJECTED', self.write)
        self.assertEqual((self.data / 'hardlink').read_text(), 'before\n')

    def test_paths_cannot_escape(self):
        for path in ('../secret', '/etc/../config.txt', '/.', 'a//b', '\0bad'):
            self.assert_error('INVALID_PATH', fs.read_file, self.ctx, 'userdata', path)

    def test_symlink_final_cannot_read(self):
        (self.data / 'link').symlink_to('/etc/passwd')
        self.assert_error('REGULAR_FILE_REQUIRED', fs.read_file, self.ctx, 'userdata', '/link')

    def test_symlink_directory_cannot_traverse(self):
        (self.data / 'outside').symlink_to(self.sd, target_is_directory=True)
        (self.sd / 'secret').write_text('outside\n')
        with self.assertRaises(OSError):
            fs.read_file(self.ctx, 'userdata', '/outside/secret')

    def test_symlink_is_listed_but_not_mutated(self):
        (self.data / 'link').symlink_to(self.source)
        listing = fs.list_files(self.ctx, 'userdata')
        self.assertEqual(next(v for v in listing['entries'] if v['name'] == 'link')['type'], 'symlink')
        self.assert_error('SPECIAL_FILE_REJECTED', fs.delete_entry, self.ctx, 'userdata', '/link', True)
        self.assertTrue(self.source.exists())

    def test_fifo_rejected_without_opening_data(self):
        os.mkfifo(self.data / 'fifo')
        self.assert_error('REGULAR_FILE_REQUIRED', fs.read_file, self.ctx, 'userdata', '/fifo')

    def test_device_symlink_rejected(self):
        (self.data / 'device').symlink_to('/dev/null')
        self.assert_error('REGULAR_FILE_REQUIRED', fs.read_file, self.ctx, 'userdata', '/device')

    def test_binary_text_rejected(self):
        self.source.write_bytes(b'text\0binary')
        self.assert_error('BINARY_OR_NON_UTF8', fs.read_file, self.ctx, 'userdata', '/config.txt')
        self.text.write_bytes(b'\xff\x00')
        self.assert_error('BINARY_OR_NON_UTF8', self.write)

    def test_utf8_truncated_preview_hash(self):
        self.source.write_text('абвг')
        result = fs.read_file(self.ctx, 'userdata', '/config.txt', maximum=5)
        self.assertEqual(result['content'], 'аб')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['sha256'], self.original_hash())

    def test_large_read_does_not_hash_unbounded_file(self):
        self.source.write_bytes(b'x' * (fs.MAX_TEXT + 1))
        result = fs.read_file(self.ctx, 'userdata', '/config.txt', maximum=32)
        self.assertTrue(result['truncated'])
        self.assertIsNone(result['sha256'])
        self.assertFalse(result['hash_complete'])
        self.assertEqual(len(result['content']), 32)

    def test_text_input_is_bounded(self):
        self.text.write_bytes(b'x' * (fs.MAX_TEXT + 1))
        self.assert_error('TEXT_TOO_LARGE', self.write)

    def test_root_directory_changes_rejected(self):
        self.assert_error('ROOT_CHANGE_FORBIDDEN', fs.delete_entry, self.ctx, 'userdata', '/', True)

    def test_copy_and_rename_do_not_overwrite(self):
        (self.sd / 'existing').write_text('keep')
        self.assert_error('DESTINATION_EXISTS', fs.copy_file, self.ctx, 'userdata', '/config.txt', 'sdcard-p1', '/existing', True)
        (self.data / 'existing').write_text('keep')
        self.assert_error('DESTINATION_EXISTS', fs.rename_entry, self.ctx, 'userdata', '/config.txt', '/existing', True)
        self.assertEqual((self.sd / 'existing').read_text(), 'keep')
        self.assertEqual((self.data / 'existing').read_text(), 'keep')

    def test_copy_checks_source_hash(self):
        self.assert_error('FILE_CHANGED', fs.copy_file, self.ctx, 'userdata', '/config.txt', 'sdcard-p1', '/copy', True, expected_sha='0' * 64)
        self.assertFalse((self.sd / 'copy').exists())
        self.assertFalse(list(self.sd.glob('.a22x-copy-*')))

    def test_delete_nonempty_directory_does_not_recurse(self):
        folder = self.data / 'folder'
        folder.mkdir()
        (folder / 'file').write_text('keep')
        with self.assertRaises(OSError):
            fs.delete_entry(self.ctx, 'userdata', '/folder', True)
        self.assertTrue((folder / 'file').exists())

    def test_delete_many_chosen_and_all_but_some(self):
        """2026-11-13: several entries of one folder at once; folders go with what is in them, a
        symlink is removed and never followed, a missing name is reported and the rest still goes."""
        work = self.data / 'many'
        work.mkdir()
        for name in ('a.txt', 'b.txt', 'c.txt', 'keep.txt'):
            (work / name).write_text(name)
        (work / 'folder' / 'deep').mkdir(parents=True)
        (work / 'folder' / 'deep' / 'file').write_text('x')
        outside = self.data / 'outside'
        outside.mkdir()
        (outside / 'precious').write_text('stays')
        (work / 'link').symlink_to(outside)
        done = fs.delete_many(self.ctx, 'userdata', '/many', False, ['a.txt', 'folder', 'link', 'gone.txt'], True)
        self.assertEqual((done['deleted'], done['failed']), (3, [{'name': 'gone.txt', 'code': 'NOT_FOUND'}]))
        self.assertEqual(sorted(p.name for p in work.iterdir()), ['b.txt', 'c.txt', 'keep.txt'])
        self.assertEqual((outside / 'precious').read_text(), 'stays')
        # 2026-11-13 review: a folder that is itself a mount point stays, with what is in it.
        with mock.patch.object(self.ctx, 'is_mountpoint', side_effect=lambda volume, path: path == '/many/c.txt' or path.endswith('/bound')):
            (work / 'bound').mkdir()
            (work / 'bound' / 'mounted').write_text('m')
            done = fs.delete_many(self.ctx, 'userdata', '/many', False, ['bound'], True)
            self.assertEqual(done['failed'], [{'name': 'bound', 'code': 'NESTED_MOUNT'}])
            self.assertTrue((work / 'bound' / 'mounted').exists())
        (work / 'bound' / 'mounted').unlink()
        (work / 'bound').rmdir()
        done = fs.delete_many(self.ctx, 'userdata', '/many', True, ['keep.txt'], True)
        self.assertEqual(done['deleted'], 2)
        self.assertEqual([p.name for p in work.iterdir()], ['keep.txt'])
        self.assert_error('CONFIRMATION_REQUIRED', fs.delete_many, self.ctx, 'userdata', '/many', True, [], False)
        selection = self.root / 'selection.json'
        selection.write_text(json.dumps({'all': False, 'names': ['../outside']}))
        self.assert_error('INVALID_SELECTION', fs.selection_file, selection)
        selection.write_text(json.dumps({'all': True, 'names': []}))
        result = self.cli('delete-many', '--volume', 'userdata', '--path', '/many', '--selection-file', str(selection), '--confirm')
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(list(work.iterdir()), [])
        self.ctx.mount('userdata', 'ro')
        self.assert_error('VOLUME_READ_ONLY', fs.delete_many, self.ctx, 'userdata', '/many', True, [], True)

    def test_rename_into_self_rejected(self):
        (self.data / 'folder').mkdir()
        self.assert_error('INVALID_DESTINATION', fs.rename_entry, self.ctx, 'userdata', '/folder', '/folder/child', True)

    def test_readonly_guard_all_mutations(self):
        self.ctx.mount('userdata', 'ro')
        self.assert_error('VOLUME_READ_ONLY', self.write)
        self.assert_error('VOLUME_READ_ONLY', fs.make_directory, self.ctx, 'userdata', '/dir', True)
        self.assert_error('VOLUME_READ_ONLY', fs.delete_entry, self.ctx, 'userdata', '/config.txt', True)
        self.assert_error('VOLUME_READ_ONLY', fs.rename_entry, self.ctx, 'userdata', '/config.txt', '/other', True)
        self.assert_error('VOLUME_READ_ONLY', fs.copy_file, self.ctx, 'sdcard-p1', '/missing', 'userdata', '/copy', True)

    def test_volume_token_detects_device_replacement(self):
        token = self.ctx.volume('userdata')['volume_token']
        self.data.rename(self.data.with_name('old-data'))
        self.data.mkdir()
        self.assert_error('VOLUME_CHANGED', fs.list_files, self.ctx, 'userdata', expected=token)

    def test_destination_volume_token_checked(self):
        token = self.ctx.volume('userdata')['volume_token']
        self.assert_error('VOLUME_CHANGED', fs.copy_file, self.ctx, 'userdata', '/config.txt', 'sdcard-p1', '/copy', True, expected_destination=token)

    def test_ro_mount_change_during_write_aborts_publication(self):
        original = fs.PinnedVolume.recheck
        calls = 0
        def change(pin):
            nonlocal calls
            if pin.writing:
                calls += 1
                if calls == 3:
                    fs.atomic_state(self.ctx.state_path, {'userdata': {'mounted': True, 'read_only': True}})
            return original(pin)
        with mock.patch.object(fs.PinnedVolume, 'recheck', change):
            self.assert_error('MOUNT_CHANGED', self.write)
        self.assertEqual(self.source.read_text(), 'before\n')
        self.assertFalse(list(self.data.glob('.a22x-edit-*')))

    def test_volume_root_replacement_during_write_aborts(self):
        original = fs.PinnedVolume.recheck
        calls = 0
        def change(pin):
            nonlocal calls
            if pin.writing:
                calls += 1
                if calls == 3:
                    self.data.rename(self.data.with_name('old-data'))
                    self.data.mkdir()
                    self.source.write_text('replacement')
            return original(pin)
        with mock.patch.object(fs.PinnedVolume, 'recheck', change):
            self.assert_error('VOLUME_CHANGED', self.write)
        self.assertEqual(self.source.read_text(), 'replacement')
        self.assertEqual((self.data.with_name('old-data') / 'config.txt').read_text(), 'before\n')

    def test_operation_lock_blocks_another_recovery_operation(self):
        with self.ctx.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli('mount', '--volume', 'userdata', '--mode', 'rw', '--confirm', ok=False)
        self.assertEqual(result['error']['code'], 'OPERATION_BUSY')

    def test_wipe_cache_keeps_the_recoverys_own_folder(self):
        """TWRP's Wipe → Cache (2026-11-10): everything else on cache goes; a22x-recovery (settings,
        keys, Wi-Fi networks, plans, scripts) and lost+found stay; the volume is left as it was."""
        cache = self.root / 'volumes/cache'
        (cache / 'a22x-recovery/scripts').mkdir(parents=True)
        (cache / 'a22x-recovery/settings.json').write_text('{"theme": "daylight"}')
        (cache / 'a22x-recovery/scripts/my-setup.sh').write_text('#!/bin/sh\n')
        (cache / 'lost+found').mkdir()
        (cache / '.a22x-recovery-password').write_text('')  # the mark of a password stays with it (2026-11-14 review)
        (cache / 'recovery').mkdir()
        (cache / 'recovery/last_log').write_text('old log')
        (cache / 'ota.zip').write_bytes(b'PK\x03\x04' + bytes(4000))
        (cache / 'link-out').symlink_to(self.data / 'config.txt')  # removed, never followed
        self.config['volumes'].append({'id': 'cache', 'path': 'volumes/cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True})
        self.write_config()
        refused = self.cli('wipe', '--volume', 'cache', ok=False)
        self.assertEqual(refused['error']['code'], 'CONFIRMATION_REQUIRED')
        done = self.cli('wipe', '--volume', 'cache', '--confirm')
        self.assertEqual((done['removed'], done['kept']), (3, ['a22x-recovery', '.a22x-recovery-password', 'lost+found']))
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ['.a22x-recovery-password', 'a22x-recovery', 'lost+found'])
        self.assertEqual((cache / 'a22x-recovery/scripts/my-setup.sh').read_text(), '#!/bin/sh\n')
        self.assertEqual(self.source.read_text(), 'before\n')  # the symlink's target is untouched
        self.assertTrue(self.ctx.volume('cache')['read_only'])  # read-only again, as it was
        other = self.cli('wipe', '--volume', 'userdata', '--confirm', ok=False)
        self.assertEqual(other['error']['code'], 'WIPE_NOT_ADMITTED')
        self.assertTrue(self.source.exists())

    def test_a_computer_session_is_named_as_the_reason(self):
        """2026-11-10: an SSH or SFTP session holds the lease; the message says so, not USB files."""
        sessions = self.root / 'ssh-sessions'
        sessions.mkdir()
        (sessions / 'session-1.json').write_text(json.dumps({'pid': os.getpid(), 'kind': 'ssh-maintenance'}))
        (sessions / 'session-2.json').write_text('not json')
        with mock.patch.object(fs, 'SSH_SESSIONS', sessions):
            self.assertTrue(fs.ssh_session_active())
            with self.ctx.lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(fs.FilesystemError) as caught:
                    with self.ctx.operation_lock():
                        pass
            self.assertEqual(caught.exception.code, 'OPERATION_BUSY')
            self.assertIn('SSH or SFTP', str(caught.exception))
            # A marker left by a session that ended does not count.
            (sessions / 'session-1.json').write_text(json.dumps({'pid': 2 ** 22 + 7}))
            self.assertFalse(fs.ssh_session_active())

    def test_paginated_listing_covers_every_file(self):
        for n in range(20):
            (self.data / f'file{n:02}').write_text('x')
        seen = []
        for offset in (0, 7, 14):
            page = fs.list_files(self.ctx, 'userdata', offset=offset, limit=7)
            seen.extend(e['name'] for e in page['entries'])
        self.assertEqual(len(seen), 21)
        self.assertEqual(len(set(seen)), 21)
        self.assertFalse(page['has_more'])

    def test_opaque_or_encrypted_volume_not_mountable(self):
        self.config['volumes'][0]['fs_type'] = 'crypto_LUKS'
        self.write_config()
        self.assert_error('VOLUME_NOT_MOUNTABLE', self.ctx.mount, 'userdata', 'ro')
        self.assertFalse(self.ctx.inventory()['crypto']['android_decryption'])

    def test_erofs_and_fscrypt_not_admitted_rw(self):
        self.config['volumes'][0]['fs_type'] = 'erofs'
        self.write_config()
        self.assert_error('READ_ONLY_FILESYSTEM', self.ctx.mount, 'userdata', 'rw', True)
        self.config['volumes'][0]['fs_type'] = 'ext4'
        self.config['volumes'][0]['fscrypt_feature'] = True
        self.write_config()
        self.assert_error('READ_ONLY_FILESYSTEM', self.ctx.mount, 'userdata', 'rw', True)


class MetadataAndSymlinkTest(FilesystemFixtureSupport):
    def entry(self, path='/config.txt'):
        return fs.stat_entry(self.ctx, 'userdata', path)['entry']

    def chmod(self, mode, path='/config.txt', **extra):
        args = dict(mode=mode, confirmed=True)
        args.update(extra)
        if 'expected_entry' not in args:
            args['expected_entry'] = self.entry(path)['entry_token']
        return fs.change_metadata(self.ctx, 'userdata', path, **args)

    def test_metadata_tokens_match_list_stat_read(self):
        listed = next(e for e in fs.list_files(self.ctx, 'userdata')['entries'] if e['name'] == 'config.txt')
        inspected = self.entry()
        read = fs.read_file(self.ctx, 'userdata', '/config.txt')
        for key in ('entry_token', 'uid', 'gid', 'nlink'):
            self.assertEqual(listed[key], inspected[key])
            self.assertEqual(read[key], inspected[key])
        self.assertEqual(listed['mode'], inspected['mode'])
        self.assertEqual(read['file_mode'], inspected['mode'])
        self.assertTrue(inspected['metadata_supported'])

    def test_read_cli_separates_fixture_adapter_from_file_permissions(self):
        self.source.chmod(0o640)
        result = self.cli('read', '--volume', 'userdata', '--path', '/config.txt')
        self.assertEqual(result['mode'], 'fixture')
        self.assertEqual(result['file_mode'], 0o640)
        self.assertEqual(result['content'], 'before\n')
        self.assertEqual((result['uid'], result['gid']), (self.source.stat().st_uid, self.source.stat().st_gid))

    def test_read_device_envelope_uses_adapter_name_with_real_fixture_payload(self):
        # Device admission is mocked; file reading is real and stays in the fixture.
        # This exercises the exact device-mode response assembly without hardware.
        self.source.chmod(0o640)
        payload = fs.read_file(self.ctx, 'userdata', '/config.txt')
        device = mock.Mock(mode='device')
        output = io.StringIO()
        with mock.patch.object(fs, 'DeviceContext', return_value=device), \
                mock.patch.object(fs, 'dispatch', return_value=payload), contextlib.redirect_stdout(output):
            rc = fs.main(['--device', 'read', '--volume', 'userdata', '--path', '/config.txt'])
        result = json.loads(output.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(result['mode'], 'device')
        self.assertEqual(result['file_mode'], 0o640)
        self.assertEqual(result['content'], 'before\n')

    def test_operation_payload_cannot_overwrite_reserved_envelope_fields(self):
        with mock.patch.object(fs, 'dispatch', return_value={'mode': 0o640, 'operation': 'wrong', 'ok': False}):
            result = self.cli('read', '--volume', 'userdata', '--path', '/config.txt')
        self.assertEqual(result['mode'], 'fixture')
        self.assertEqual(result['operation'], 'read')
        self.assertTrue(result['ok'])

    def test_chmod_cli_preserves_data_owner_and_user_xattr(self):
        os.setxattr(self.source, 'user.a22x-metadata', b'keep')
        before = self.source.stat()
        token = self.cli('stat', '--volume', 'userdata', '--path', '/config.txt')['entry']['entry_token']
        result = self.cli('chmod', '--volume', 'userdata', '--path', '/config.txt',
                          '--mode', '0640', '--expected-entry-token', token, '--confirm')
        self.assertEqual(self.source.read_bytes(), b'before\n')
        self.assertEqual((self.source.stat().st_uid, self.source.stat().st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)
        self.assertEqual(os.getxattr(self.source, 'user.a22x-metadata'), b'keep')
        self.assertEqual(result['entry']['mode'], 0o640)
        self.assertTrue(result['changed'])

    def test_metadata_requires_confirmation_and_entry_token(self):
        self.assert_error('CONFIRMATION_REQUIRED', self.chmod, 0o600, confirmed=False)
        self.assert_error('INVALID_HASH', self.chmod, 0o600, expected_entry=None)
        self.assert_error('ENTRY_CHANGED', self.chmod, 0o600, expected_entry='0' * 64)

    def test_old_metadata_token_rejects_inode_replacement(self):
        token = self.entry()['entry_token']
        self.source.rename(self.data / 'previous')
        self.source.write_bytes(b'replacement\n')
        before = self.source.stat()
        self.assert_error('ENTRY_CHANGED', self.chmod, 0o700, expected_entry=token)
        self.assertEqual(self.source.stat().st_mode, before.st_mode)
        self.assertEqual(self.source.read_bytes(), b'replacement\n')

    def test_old_metadata_token_rejects_chmod_since_selection(self):
        token = self.entry()['entry_token']
        self.source.chmod(0o600)
        self.assert_error('ENTRY_CHANGED', self.chmod, 0o700, expected_entry=token)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o600)

    def test_chown_noop_does_not_clear_setid_bits(self):
        self.source.chmod(0o6750)
        before = self.entry()
        result = self.cli('chown', '--volume', 'userdata', '--path', '/config.txt',
                          '--uid', str(before['uid']), '--gid', str(before['gid']),
                          '--expected-entry-token', before['entry_token'], '--confirm')
        self.assertFalse(result['changed'])
        self.assertEqual(result['entry']['mode'], before['mode'])

    def test_chown_requested_numeric_ids_or_clean_permission_failure(self):
        self.source.chmod(0o6750)
        before = self.entry()
        token = before['entry_token']
        def mapped(kind, ident):
            return any(start <= ident < start + count for start, _, count in
                       (map(int, line.split()) for line in Path('/proc/self/' + kind + '_map').read_text().splitlines()))
        if os.geteuid() == 0 and mapped('uid', 12345) and mapped('gid', 12346):
            result = self.cli('chown', '--volume', 'userdata', '--path', '/config.txt',
                              '--uid', '12345', '--gid', '12346', '--expected-entry-token', token, '--confirm')
            self.assertEqual((self.source.stat().st_uid, self.source.stat().st_gid), (12345, 12346))
            self.assertEqual(result['entry']['mode'] & 0o6000, 0)
            self.assertEqual(result['cleared_special_bits'], 0o6000)
        else:
            # Some fixture runners map only UID/GID 0 in their user namespace.
            # Exercise the real failed syscall and prove it does not alter data.
            target = '12345' if os.geteuid() == 0 else '0'
            result = self.cli('chown', '--volume', 'userdata', '--path', '/config.txt',
                              '--uid', target, '--expected-entry-token', token, '--confirm', ok=False)
            self.assertEqual(result['error']['code'], 'METADATA_CHANGE_FAILED')
            self.assertIn(result['error']['details']['errno'], (errno.EINVAL, errno.EPERM))
            self.assertEqual((self.source.stat().st_uid, self.source.stat().st_gid), (before['uid'], before['gid']))
        self.assertEqual(self.source.read_bytes(), b'before\n')

    def test_metadata_rejects_invalid_mode_and_owner_values(self):
        for mode in ('888', '0x644', '-1', 'u+rwx', True, 0o10000):
            self.assert_error('INVALID_MODE', self.chmod, mode)
        for uid in ('root', '-1', '4294967295', True, 0x100000000):
            self.assert_error('INVALID_OWNER', fs.change_metadata, self.ctx, 'userdata', '/config.txt',
                              uid=uid, confirmed=True, expected_entry=self.entry()['entry_token'])

    def test_metadata_directory_and_root_policy(self):
        (self.data / 'directory').mkdir()
        self.chmod(0o750, '/directory')
        self.assertEqual(stat.S_IMODE((self.data / 'directory').stat().st_mode), 0o750)
        self.assert_error('ROOT_CHANGE_FORBIDDEN', self.chmod, 0o750, '/')

    def test_metadata_rejects_hardlinks_symlinks_special_and_fat(self):
        os.link(self.source, self.data / 'alias')
        self.assertFalse(self.entry()['metadata_supported'])
        self.assert_error('METADATA_CHANGE_UNSUPPORTED', self.chmod, 0o600)
        (self.data / 'link').symlink_to('config.txt')
        self.assert_error('METADATA_CHANGE_UNSUPPORTED', self.chmod, 0o600, '/link')
        os.mkfifo(self.data / 'pipe')
        self.assert_error('METADATA_CHANGE_UNSUPPORTED', self.chmod, 0o600, '/pipe')
        self.config['volumes'][0]['fs_type'] = 'exfat'
        self.write_config()
        self.assert_error('METADATA_CHANGE_UNSUPPORTED', self.chmod, 0o600)

    def test_metadata_ro_and_mount_change_guards(self):
        token = self.entry()['entry_token']
        self.ctx.mount('userdata', 'ro')
        self.assert_error('VOLUME_READ_ONLY', fs.change_metadata, self.ctx, 'userdata', '/config.txt',
                          mode=0o600, confirmed=True, expected_entry=token)
        self.ctx.mount('userdata', 'rw', True)
        before = self.source.stat().st_mode
        original, calls = fs.PinnedVolume.recheck, 0
        def change(pin):
            nonlocal calls
            calls += 1
            if calls == 3:
                fs.atomic_state(self.ctx.state_path, {'userdata': {'mounted': True, 'read_only': True}})
            return original(pin)
        with mock.patch.object(fs.PinnedVolume, 'recheck', change):
            self.assert_error('MOUNT_CHANGED', self.chmod, 0o700, expected_entry=token)
        self.assertEqual(self.source.stat().st_mode, before)

    def test_stat_and_resolve_relative_link_cli(self):
        (self.data / 'link').symlink_to('config.txt')
        entry = self.cli('stat', '--volume', 'userdata', '--path', '/link')['entry']
        self.assertEqual(entry['link_target'], 'config.txt')
        result = self.cli('resolve', '--volume', 'userdata', '--path', '/link',
                          '--expected-entry-token', entry['entry_token'])
        self.assertEqual(result['resolved_path'], '/config.txt')
        self.assertEqual(result['entry']['type'], 'file')
        self.assertEqual(result['links'], [{'path': '/link', 'target': 'config.txt'}])
        self.assertEqual(fs.read_file(self.ctx, 'userdata', result['resolved_path'])['content'], 'before\n')

    def test_resolve_absolute_link_is_relative_to_volume_root(self):
        (self.data / 'etc').mkdir()
        (self.data / 'etc/passwd').write_text('inner-volume\n')
        (self.data / 'absolute').symlink_to('/etc/passwd')
        resolved = fs.resolve_entry(self.ctx, 'userdata', '/absolute')
        self.assertEqual(resolved['resolved_path'], '/etc/passwd')
        self.assertEqual(fs.read_file(self.ctx, 'userdata', resolved['resolved_path'])['content'], 'inner-volume\n')

    def test_resolve_parent_components_stay_inside_volume(self):
        (self.data / 'folder').mkdir()
        (self.data / 'folder/link').symlink_to('../config.txt')
        self.assertEqual(fs.resolve_entry(self.ctx, 'userdata', '/folder/link')['resolved_path'], '/config.txt')
        (self.data / 'escape').symlink_to('../sd/secret')
        (self.sd / 'secret').write_text('outside')
        self.assert_error('SYMLINK_ESCAPE', fs.resolve_entry, self.ctx, 'userdata', '/escape')

    def test_resolve_directory_links_and_dot_target(self):
        (self.data / 'usr/bin').mkdir(parents=True)
        (self.data / 'usr/bin/tool').write_text('tool')
        (self.data / 'bin').symlink_to('usr/bin')
        (self.data / 'self').symlink_to('.')
        self.assertEqual(fs.resolve_entry(self.ctx, 'userdata', '/bin/tool')['resolved_path'], '/usr/bin/tool')
        self.assertEqual(fs.resolve_entry(self.ctx, 'userdata', '/bin')['entry']['type'], 'directory')
        self.assertEqual(fs.resolve_entry(self.ctx, 'userdata', '/self')['resolved_path'], '/')

    def test_resolve_loop_is_bounded(self):
        (self.data / 'a').symlink_to('b')
        (self.data / 'b').symlink_to('a')
        self.assert_error('SYMLINK_LOOP', fs.resolve_entry, self.ctx, 'userdata', '/a')

    def test_resolve_special_target_only_inspects_inode(self):
        os.mkfifo(self.data / 'pipe')
        (self.data / 'link').symlink_to('pipe')
        result = fs.resolve_entry(self.ctx, 'userdata', '/link')
        self.assertEqual(result['entry']['type'], 'other')
        self.assertFalse(result['entry']['metadata_supported'])

    def test_resolve_checks_entry_token(self):
        (self.data / 'link').symlink_to('config.txt')
        self.assert_error('ENTRY_CHANGED', fs.resolve_entry, self.ctx, 'userdata', '/link', expected_entry='0' * 64)

    def test_resolve_detects_link_replacement(self):
        link = self.data / 'link'
        link.symlink_to('config.txt')
        original = os.readlink
        def replace(path, *args, **kwargs):
            target = original(path, *args, **kwargs)
            if path == '':
                link.unlink()
                link.symlink_to('other')
            return target
        with mock.patch.object(fs.os, 'readlink', replace):
            self.assert_error('FILE_CHANGED', fs.resolve_entry, self.ctx, 'userdata', '/link')

    def test_resolve_rejects_nested_mount_identity(self):
        (self.data / 'nested').mkdir()
        (self.data / 'link').symlink_to('nested')
        inode = (self.data / 'nested').stat().st_ino
        original = fs.PinnedVolume.check_fd
        def checked(pin, fd):
            original(pin, fd)
            if os.fstat(fd).st_ino == inode:
                fs.fail('CROSS_MOUNT_REJECTED', 'Injected nested mount')
        with mock.patch.object(fs.PinnedVolume, 'check_fd', checked):
            self.assert_error('CROSS_MOUNT_REJECTED', fs.resolve_entry, self.ctx, 'userdata', '/link')

    def test_resolve_detects_target_replacement_before_return(self):
        (self.data / 'link').symlink_to('config.txt')
        original = fs.pinned_stat
        def replace(pin, path):
            if path == '/config.txt':
                self.source.rename(self.data / 'old-config')
                self.source.write_text('replaced')
            return original(pin, path)
        with mock.patch.object(fs, 'pinned_stat', replace):
            self.assert_error('ENTRY_CHANGED', fs.resolve_entry, self.ctx, 'userdata', '/link')


class CopyTreeTest(FilesystemFixtureSupport):
    def setUp(self):
        super().setUp()
        self.tree = self.data / 'tree'
        (self.tree / 'nested').mkdir(parents=True)
        (self.tree / 'empty').mkdir()
        self.tree_file = self.tree / 'nested/data.bin'
        self.tree_file.write_bytes(b'\0Linux tree\xff\n')
        self.tree_file.chmod(0o640)
        self.tree.chmod(0o750)

    def token(self):
        return fs.stat_entry(self.ctx, 'userdata', '/tree')['entry']['entry_token']

    def copy(self, **extra):
        args = dict(confirmed=True, destination_volume='sdcard-p1', destination='/copied')
        args.update(extra)
        if 'expected_entry' not in args:
            args['expected_entry'] = self.token()
        return fs.copy_tree(self.ctx, 'userdata', '/tree', **args)

    def assert_no_stage(self):
        self.assertFalse(any(p.name.startswith('.a22x-tree-') for p in self.sd.iterdir()))

    def test_cli_copies_contents_metadata_links_and_empty_directories(self):
        os.link(self.tree_file, self.tree / 'hardlink')
        (self.tree / 'relative').symlink_to('nested/data.bin')
        (self.tree / 'absolute').symlink_to('/etc/passwd')
        (self.tree / 'outside').symlink_to('../../config.txt')
        os.link(self.tree / 'relative', self.tree / 'linked-symlink', follow_symlinks=False)
        os.setxattr(self.tree_file, 'user.recovery-test', b'exact-value')
        os.setxattr(self.tree / 'empty', 'user.recovery-dir', b'empty')
        when = 1500000000123456789
        os.utime(self.tree_file, ns=(when, when))
        os.utime(self.tree / 'empty', ns=(when, when))
        result = self.cli('copy-tree', '--volume', 'userdata', '--path', '/tree',
                          '--destination-volume', 'sdcard-p1', '--destination', '/copied',
                          '--expected-entry-token', self.token(), '--confirm')
        copied = self.sd / 'copied'
        self.assertEqual((copied / 'nested/data.bin').read_bytes(), self.tree_file.read_bytes())
        self.assertEqual(stat.S_IMODE((copied / 'nested/data.bin').stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o750)
        self.assertEqual(os.getxattr(copied / 'nested/data.bin', 'user.recovery-test'), b'exact-value')
        self.assertEqual(os.getxattr(copied / 'empty', 'user.recovery-dir'), b'empty')
        self.assertEqual((copied / 'nested/data.bin').stat().st_mtime_ns, when)
        self.assertEqual((copied / 'empty').stat().st_mtime_ns, when)
        self.assertEqual(os.readlink(copied / 'absolute'), '/etc/passwd')
        self.assertEqual(os.readlink(copied / 'outside'), '../../config.txt')
        self.assertEqual((copied / 'nested/data.bin').stat().st_ino, (copied / 'hardlink').stat().st_ino)
        self.assertEqual((copied / 'relative').lstat().st_ino, (copied / 'linked-symlink').lstat().st_ino)
        self.assertNotEqual(self.tree_file.stat().st_ino, (copied / 'hardlink').stat().st_ino)
        self.assertTrue((copied / 'empty').is_dir())
        self.assertEqual((result['files'], result['directories'], result['symlinks']), (2, 3, 4))
        self.assertEqual(result['hardlinks_preserved'], 2)
        self.assertEqual(result['bytes_copied'], self.tree_file.stat().st_size)
        self.assertTrue(result['posix_metadata_preserved'])
        self.assertRegex(result['content_manifest_sha256'], r'^[0-9a-f]{64}$')
        self.assert_no_stage()

    def test_copy_accepts_read_only_source(self):
        self.ctx.mount('userdata', 'ro')
        self.copy()
        self.assertEqual((self.sd / 'copied/nested/data.bin').read_bytes(), self.tree_file.read_bytes())

    def test_copy_requires_confirmation_and_current_entry_token(self):
        self.assert_error('CONFIRMATION_REQUIRED', self.copy, confirmed=False)
        self.assert_error('INVALID_HASH', self.copy, expected_entry=None)
        self.assert_error('ENTRY_CHANGED', self.copy, expected_entry='0' * 64)
        token = self.token()
        self.tree.chmod(0o700)
        self.assert_error('ENTRY_CHANGED', self.copy, expected_entry=token)
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_copy_never_overwrites_or_merges(self):
        (self.sd / 'copied').mkdir()
        marker = self.sd / 'copied/keep'
        marker.write_text('existing')
        self.assert_error('DESTINATION_EXISTS', self.copy)
        self.assertEqual(marker.read_text(), 'existing')
        self.assertEqual(list((self.sd / 'copied').iterdir()), [marker])
        self.assert_no_stage()

    def test_copy_rejects_self_descendant_and_alias_destination(self):
        self.assert_error('INVALID_DESTINATION', self.copy, destination_volume='userdata', destination='/tree/new')
        self.assert_error('INVALID_DESTINATION', self.copy, destination_volume='userdata', destination='/tree')
        self.config['volumes'][1]['path'] = 'volumes/data/tree/nested'
        self.write_config()
        self.assert_error('INVALID_DESTINATION', self.copy)
        self.assertFalse((self.tree / 'nested/copied').exists())

    def test_copy_rejects_source_root_destination_root_and_parent_traversal(self):
        self.assert_error('ROOT_CHANGE_FORBIDDEN', self.copy, destination='/')
        self.assert_error('INVALID_PATH', self.copy, destination='/../outside')
        root_token = fs.stat_entry(self.ctx, 'userdata', '/')['entry']['entry_token']
        self.assert_error('ROOT_CHANGE_FORBIDDEN', fs.copy_tree, self.ctx, 'userdata', '/',
                          'sdcard-p1', '/root-copy', True, expected_entry=root_token)

    def test_copy_rejects_destination_symlink_parent_without_following(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.sd / 'redirect').symlink_to(outside)
        result = self.cli('copy-tree', '--volume', 'userdata', '--path', '/tree',
                          '--destination-volume', 'sdcard-p1', '--destination', '/redirect/copied',
                          '--expected-entry-token', self.token(), '--confirm', ok=False)
        self.assertEqual(result['error']['code'], 'SYMLINK_OR_NON_DIRECTORY')
        self.assertEqual(list(outside.iterdir()), [])

    def test_copy_rejects_read_only_fat_and_stale_volume(self):
        self.ctx.mount('sdcard-p1', 'ro')
        self.assert_error('VOLUME_READ_ONLY', self.copy)
        self.ctx.mount('sdcard-p1', 'rw', True)
        self.assert_error('VOLUME_CHANGED', self.copy, expected_destination='0' * 64)
        self.config['volumes'][1]['fs_type'] = 'exfat'
        self.write_config()
        self.assert_error('TREE_METADATA_UNSUPPORTED', self.copy)
        self.assert_no_stage()

    def test_copy_rejects_fifo_and_nested_mount_before_destination_creation(self):
        os.mkfifo(self.tree / 'pipe')
        self.assert_error('SPECIAL_FILE_REJECTED', self.copy)
        (self.tree / 'pipe').unlink()
        with mock.patch.object(self.ctx, 'nested_mounts', return_value=[{'mountpoint': 'nested'}]):
            self.assert_error('NESTED_MOUNT', self.copy)
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_copy_checks_kernel_mount_identity_of_every_source_entry(self):
        inode = self.tree_file.stat().st_ino
        original = fs.PinnedVolume.check_fd
        def checked(pin, fd):
            original(pin, fd)
            if os.fstat(fd).st_ino == inode:
                fs.fail('CROSS_MOUNT_REJECTED', 'Injected mount boundary')
        with mock.patch.object(fs.PinnedVolume, 'check_fd', checked):
            self.assert_error('CROSS_MOUNT_REJECTED', self.copy)
        self.assert_no_stage()

    def test_copy_entry_depth_and_size_limits_are_preflight(self):
        for name, value, code in (('MAX_TREE_ENTRIES', 2, 'TREE_ENTRY_LIMIT'),
                                  ('MAX_TREE_DEPTH', 1, 'TREE_DEPTH_LIMIT'),
                                  ('MAX_TREE_BYTES', 2, 'TREE_BYTE_LIMIT')):
            with mock.patch.object(fs, name, value):
                self.assert_error(code, self.copy)
            self.assertFalse((self.sd / 'copied').exists())
            self.assert_no_stage()

    def test_copy_preflight_insufficient_space(self):
        with mock.patch.object(fs.os, 'fstatvfs', return_value=mock.Mock(f_bavail=0, f_frsize=4096)):
            self.assert_error('INSUFFICIENT_SPACE', self.copy)
        self.assert_no_stage()

    def test_source_file_change_aborts_and_cleans_owned_staging(self):
        original = fs.preserve_metadata
        def change(source, target):
            original(source, target)
            if os.fstat(source).st_ino == self.tree_file.stat().st_ino:
                self.tree_file.write_text('changed while copying')
        with mock.patch.object(fs, 'preserve_metadata', change):
            self.assert_error('FILE_CHANGED', self.copy)
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_new_source_child_detected_by_final_snapshot(self):
        original, calls = fs.tree_snapshot, 0
        def change(pin, directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.tree / 'new-child').write_text('appeared')
            return original(pin, directory)
        with mock.patch.object(fs, 'tree_snapshot', change):
            self.assert_error('FILE_CHANGED', self.copy)
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_destination_created_during_copy_is_not_overwritten(self):
        original = fs.rename_noreplace
        def compete(*args):
            (self.sd / 'copied').mkdir()
            (self.sd / 'copied/keep').write_text('concurrent destination')
            return original(*args)
        with mock.patch.object(fs, 'rename_noreplace', compete):
            self.assert_error('DESTINATION_EXISTS', self.copy)
        self.assertEqual((self.sd / 'copied/keep').read_text(), 'concurrent destination')
        self.assert_no_stage()

    def test_write_failure_cleans_only_its_private_staging(self):
        marker = self.sd / 'existing'
        marker.write_text('unrelated')
        with mock.patch.object(fs.os, 'write', side_effect=OSError(errno.ENOSPC, 'Injected full destination')):
            with self.assertRaises(fs.FilesystemError) as caught:
                self.copy()
        self.assertEqual(caught.exception.code, 'COPY_TREE_FAILED')
        self.assertTrue(caught.exception.details['cleanup_complete'])
        self.assertFalse(caught.exception.details['destination_published'])
        self.assertEqual(marker.read_text(), 'unrelated')
        self.assert_no_stage()

    def test_cleanup_does_not_delete_an_unrecognized_staging_entry(self):
        def intrude(source, target):
            target_path = Path(os.readlink('/proc/self/fd/' + str(target)))
            (target_path.parent / 'unrecognized').write_text('must survive cleanup')
            fs.fail('METADATA_PRESERVE_FAILED', 'Injected metadata failure')
        with mock.patch.object(fs, 'preserve_metadata', intrude):
            with self.assertRaises(fs.FilesystemError) as caught:
                self.copy()
        self.assertFalse(caught.exception.details['cleanup_complete'])
        stage = self.sd / caught.exception.details['staging_name']
        self.assertEqual((stage / 'tree/nested/unrecognized').read_text(), 'must survive cleanup')
        self.assertFalse((self.sd / 'copied').exists())

    def test_staging_wrapper_stays_private_when_root_metadata_is_restored(self):
        original, inspected = fs.preserve_metadata, []
        def inspect(source, target):
            original(source, target)
            target_path = Path(os.readlink('/proc/self/fd/' + str(target)))
            if os.fstat(source).st_ino == self.tree.stat().st_ino:
                inspected.append(stat.S_IMODE(target_path.parent.stat().st_mode))
        with mock.patch.object(fs, 'preserve_metadata', inspect):
            self.copy()
        self.assertEqual(inspected, [0o700])
        self.assert_no_stage()

    def test_final_mount_change_prevents_publication(self):
        original, calls = fs.PinnedVolume.recheck, 0
        def change(pin):
            nonlocal calls
            if pin.writing:
                calls += 1
                if calls == 4:
                    fs.atomic_state(self.ctx.state_path, {'sdcard-p1': {'mounted': True, 'read_only': True}})
            return original(pin)
        with mock.patch.object(fs.PinnedVolume, 'recheck', change):
            self.assert_error('MOUNT_CHANGED', self.copy)
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_source_file_replaced_with_symlink_is_never_followed(self):
        original = fs.open_regular
        outside = self.root / 'outside-secret'
        outside.write_bytes(b'outside must remain untouched')
        def replace(path, **kwargs):
            if path == 'data.bin':
                self.tree_file.unlink()
                self.tree_file.symlink_to(outside)
            return original(path, **kwargs)
        with mock.patch.object(fs, 'open_regular', replace):
            self.assert_error('REGULAR_FILE_REQUIRED', self.copy)
        self.assertEqual(outside.read_bytes(), b'outside must remain untouched')
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_failure_opening_new_stage_removes_only_owned_empty_directories(self):
        original, count = fs.os.open, 0
        def broken(path, flags, *args, **kwargs):
            nonlocal count
            if path == 'tree' and flags & os.O_DIRECTORY:
                count += 1
                if count == 2:
                    raise OSError(errno.EIO, 'Injected staging-root open failure')
            return original(path, flags, *args, **kwargs)
        with mock.patch.object(fs.os, 'open', broken):
            with self.assertRaises(fs.FilesystemError) as caught:
                self.copy()
        self.assertEqual(caught.exception.code, 'COPY_TREE_FAILED')
        self.assertTrue(caught.exception.details['cleanup_complete'])
        self.assertFalse((self.sd / 'copied').exists())
        self.assert_no_stage()

    def test_fsync_failure_after_publication_reports_visible_destination(self):
        original = fs.os.fsync
        def broken(fd):
            if Path(os.readlink('/proc/self/fd/' + str(fd))) == self.sd and (self.sd / 'copied').exists():
                raise OSError(errno.EIO, 'Injected final parent sync failure')
            return original(fd)
        with mock.patch.object(fs.os, 'fsync', broken):
            with self.assertRaises(fs.FilesystemError) as caught:
                self.copy()
        self.assertEqual(caught.exception.code, 'COPY_TREE_FAILED')
        self.assertTrue(caught.exception.details['destination_published'])
        self.assertEqual((self.sd / 'copied/nested/data.bin').read_bytes(), self.tree_file.read_bytes())

    def test_destination_default_acl_does_not_survive_in_copied_tree(self):
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', tag, permissions, ident) for tag, permissions, ident in (
            (0x01, 7, 0xffffffff), (0x02, 7, os.getuid()), (0x04, 5, 0xffffffff),
            (0x10, 7, 0xffffffff), (0x20, 0, 0xffffffff)))
        try:
            os.setxattr(self.sd, 'system.posix_acl_default', acl)
        except OSError as exc:
            if exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP):
                self.skipTest('This fixture filesystem has no POSIX ACL support')
            raise
        self.copy()
        for relative in ('', 'nested', 'empty', 'nested/data.bin'):
            source, target = self.tree / relative, self.sd / 'copied' / relative
            self.assertEqual(set(os.listxattr(source)), set(os.listxattr(target)))
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), stat.S_IMODE(target.stat().st_mode))


class ProbeAndMountTest(unittest.TestCase):
    def test_probe_ext4_uuid_and_encryption_feature(self):
        data = bytearray(8192)
        data[1080:1082] = b'\x53\xef'
        struct.pack_into('<I', data, 1024 + 24, 2)
        struct.pack_into('<I', data, 1024 + 32, 32768)
        data[1128:1144] = bytes(range(16))
        result = fs.probe_magic(data)
        self.assertEqual(result['fs_type'], 'ext4')
        self.assertEqual(result['filesystem_uuid'], '00010203-0405-0607-0809-0a0b0c0d0e0f')
        self.assertFalse(result['fscrypt_feature'])
        struct.pack_into('<I', data, 1024 + 96, 0x10000)
        self.assertTrue(fs.probe_magic(data)['fscrypt_feature'])

    def test_probe_f2fs_offsets(self):
        data = bytearray(8192)
        data[1024:1028] = b'\x10\x20\xf5\xf2'
        struct.pack_into('<I', data, 1024 + 16, 12)
        data[1024 + 108:1024 + 124] = bytes(range(16))
        struct.pack_into('<I', data, 1024 + 2180, 1 | 0x2000)
        result = fs.probe_magic(data)
        self.assertEqual(result['fs_type'], 'f2fs')
        self.assertTrue(result['fscrypt_feature'])
        self.assertTrue(result['compression_feature'])

    def test_probe_erofs_readonly(self):
        data = bytearray(8192)
        data[1024:1028] = b'\xe2\xe1\xf5\xe0'
        data[1036] = 12
        data[1072:1088] = bytes(range(16))
        self.assertEqual(fs.probe_magic(data)['fs_type'], 'erofs')
        with self.assertRaises(fs.FilesystemError):
            fs.mount_options('erofs', 'rw')

    def test_probe_vfat_and_exfat(self):
        data = bytearray(8192)
        data[510:512] = b'\x55\xaa'
        struct.pack_into('<H', data, 11, 512)
        data[13], data[16] = 8, 2
        data[82:90] = b'FAT32   '
        self.assertEqual(fs.probe_magic(data)['fs_type'], 'vfat')
        data[3:11] = b'EXFAT   '
        data[108], data[110] = 9, 1
        self.assertEqual(fs.probe_magic(data)['fs_type'], 'exfat')

    def test_unknown_signature_never_guesses_filesystem(self):
        self.assertIsNone(fs.probe_magic(b'ANDROID!' + bytes(8192))['fs_type'])
        self.assertTrue(fs.probe_magic(b'LUKS\xba\xbe' + bytes(8192))['encrypted_container'])

    def test_ro_mount_options_disable_replay(self):
        self.assertIn('noload', fs.mount_options('ext4', 'ro').split(','))
        self.assertIn('norecovery', fs.mount_options('f2fs', 'ro').split(','))
        self.assertNotIn('noload', fs.mount_options('ext4', 'rw'))

    def test_mountinfo_identity_and_escaped_path(self):
        value = fs.parse_mountinfo('42 2 259:22 / /mnt/volumes/user\\040data ro,nodev - ext4 /dev/mmcblk0p54 ro\n')[0]
        self.assertEqual(value['mount_id'], 42)
        self.assertEqual(value['mountpoint'], '/mnt/volumes/user data')
        self.assertEqual(value['devno'], '259:22')


class BlockReadOnlyGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a22x-ro-guard-')
        self.context = object.__new__(fs.DeviceContext)
        self.context.boot_id = 'fixture-boot'
        self.context.state_path = Path(self.temp.name) / 'state.json'
        self.volume = {'id': 'userdata', 'volume_token': '1' * 64, 'device': '259:22'}
        self.flag = False
        self.changes = []
        def block_ro(volume, value=None):
            if value is not None:
                self.flag = bool(value)
                self.changes.append(self.flag)
            return self.flag
        self.patch_flag = mock.patch.object(self.context, '_block_ro', block_ro)
        self.patch_mounts = mock.patch.object(fs, 'all_namespace_mounts', return_value=[])
        self.patch_flag.start()
        self.mounts = self.patch_mounts.start()

    def tearDown(self):
        self.patch_flag.stop()
        self.patch_mounts.stop()
        self.temp.cleanup()

    def test_guard_sets_and_verifies_before_mount_then_restores_owned_flag(self):
        self.context._arm_read_only(self.volume)
        self.assertTrue(self.flag)
        self.assertEqual(self.changes, [True])
        self.assertFalse(self.context.state()['volumes']['userdata']['original_block_ro'])
        self.context._release_read_only(self.volume)
        self.assertFalse(self.flag)
        self.assertEqual(self.changes, [True, False])
        self.assertEqual(self.context.state()['volumes'], {})

    def test_preexisting_readonly_flag_is_never_cleared(self):
        self.flag = True
        self.context._arm_read_only(self.volume)
        self.context._release_read_only(self.volume)
        self.assertTrue(self.flag)
        self.assertEqual(self.changes, [])

    def test_guard_is_retained_if_partition_still_mounted(self):
        self.context._arm_read_only(self.volume)
        self.mounts.return_value = [{'devno': '259:22'}]
        with self.assertRaises(fs.FilesystemError) as error:
            self.context._release_read_only(self.volume)
        self.assertEqual(error.exception.code, 'READ_ONLY_GUARD_STILL_MOUNTED')
        self.assertTrue(self.flag)
        self.assertTrue(self.context.state()['volumes']['userdata']['block_ro_guard'])

    def test_guard_identity_cannot_be_rebound_to_another_card(self):
        self.context._arm_read_only(self.volume)
        other = dict(self.volume, volume_token='2' * 64)
        with self.assertRaises(fs.FilesystemError) as error:
            self.context._release_read_only(other)
        self.assertEqual(error.exception.code, 'BLOCK_RO_IDENTITY_MISMATCH')
        self.assertTrue(self.flag)

    def test_guard_not_armed_over_existing_mount(self):
        self.mounts.return_value = [{'devno': '259:22'}]
        with self.assertRaises(fs.FilesystemError) as error:
            self.context._arm_read_only(self.volume)
        self.assertEqual(error.exception.code, 'VOLUME_MOUNTED_ELSEWHERE')
        self.assertEqual(self.changes, [])


if __name__ == '__main__':
    unittest.main()


class CheckRepairTest(FilesystemFixtureSupport):
    """TWRP's "Repair file system": e2fsck on an unmounted ext2/3/4 volume the helper may format."""
    def test_fixture_admission(self):
        self.cli('check', '--volume', 'sdcard-p1', '--confirm', ok=False)  # mounted
        self.config['volumes'][1]['mounted'] = False
        self.config['volumes'].append({'id': 'usb-1', 'path': 'volumes/sd', 'fs_type': 'exfat', 'mounted': False, 'read_only': False})
        self.config['volumes'].append({'id': 'usb-2', 'path': 'volumes/sd', 'fs_type': 'vfat', 'mounted': False, 'read_only': False})
        self.config['volumes'].append({'id': 'system', 'path': 'volumes/data', 'fs_type': 'ext4', 'mounted': False, 'read_only': True})
        self.write_config()
        self.assertEqual(self.cli('check', '--volume', 'sdcard-p1', ok=False)['error']['code'], 'CONFIRMATION_REQUIRED')
        done = self.cli('check', '--volume', 'sdcard-p1', '--confirm')
        self.assertEqual((done['checked'], done['result'], done['exit_code']), ('ext4', 'clean', 0))
        self.assertEqual(self.cli('check', '--volume', 'usb-1', '--confirm', ok=False)['error']['code'], 'UNSUPPORTED_FILESYSTEM')
        self.assertEqual(self.cli('check', '--volume', 'system', '--confirm', ok=False)['error']['code'], 'CHECK_FORBIDDEN')
        # 2026-11-13: F2FS and FAT are checked too; Android's encrypted F2FS is left to Android.
        self.assertEqual(self.cli('check', '--volume', 'usb-2', '--confirm')['checked'], 'vfat')
        fs.check_admitted({'id': 'userdata', 'mounted': False, 'fs_type': 'f2fs', 'fscrypt_feature': False}, False)
        self.assert_error('CHECK_FORBIDDEN', fs.check_admitted, {'id': 'userdata', 'mounted': False, 'fs_type': 'f2fs', 'fscrypt_feature': True}, False)

    def test_f2fs_and_fat_tools_by_their_exit_codes(self):
        tools = {}
        for name, code in (('clean', 0), ('fixed', 1), ('left', 4), ('broken', 8)):
            path = self.root / name
            path.write_text('#!/bin/sh\necho "$0 $*" > "%s.args"\necho checked\nexit %d\n' % (path, code))
            path.chmod(0o755)
            tools[name] = str(path)
        done = fs.run_fsck('/dev/x', 'f2fs', {'f2fs': tools['clean'], 'vfat': tools['fixed'], 'ext': tools['broken']})
        self.assertEqual((done['result'], done['exit_code']), ('clean', 0))
        self.assertTrue((self.root / 'clean.args').read_text().strip().endswith('-f -y /dev/x'))
        # fsck.fat says 1 for "repaired" and for "could not read it": a pass that changes nothing decides.
        flaky = self.root / 'fat'
        flaky.write_text('#!/bin/sh\necho "$*" >> "%s.args"\ncase $1 in -n) exit ${FAT_AGAIN:-0} ;; esac\nexit 1\n' % flaky)
        flaky.chmod(0o755)
        done = fs.run_fsck('/dev/y', 'vfat', {'f2fs': tools['clean'], 'vfat': str(flaky), 'ext': tools['broken']})
        self.assertEqual((done['result'], done['exit_code']), ('repaired', 1))
        self.assertEqual((self.root / 'fat.args').read_text().split('\n')[:2], ['-a -w /dev/y', '-n /dev/y'])
        os.environ['FAT_AGAIN'] = '1'
        self.addCleanup(os.environ.pop, 'FAT_AGAIN', None)
        self.assert_error('CHECK_FAILED', fs.run_fsck, '/dev/y', 'vfat', {'f2fs': '', 'vfat': str(flaky), 'ext': ''})
        os.environ.pop('FAT_AGAIN')
        self.assert_error('CHECK_ERRORS_LEFT', fs.run_fsck, '/dev/x', 'f2fs', {'f2fs': tools['left'], 'vfat': '', 'ext': ''})
        self.assert_error('CHECK_FAILED', fs.run_fsck, '/dev/x', 'vfat', {'f2fs': '', 'vfat': tools['broken'], 'ext': ''})
        self.assert_error('CHECK_UNAVAILABLE', fs.run_fsck, '/dev/x', 'f2fs', {'f2fs': str(self.root / 'none'), 'vfat': '', 'ext': ''})

    def test_mounted_volume_is_refused_with_its_reason(self):
        result = self.cli('check', '--volume', 'userdata', '--confirm', ok=False)
        self.assertEqual(result['error']['code'], 'VOLUME_MOUNTED')

    def test_real_e2fsck_repairs_then_reports_clean_and_refuses_garbage(self):
        import shutil
        import subprocess
        tools = [shutil.which(name) for name in ('mkfs.ext4', 'debugfs', 'e2fsck')]
        if not all(tools):
            self.skipTest('e2fsprogs are not installed on this host')
        mkfs, debugfs, e2fsck = tools
        image = self.root / 'card.img'
        with open(image, 'wb') as handle:
            handle.truncate(16 << 20)
        subprocess.run([mkfs, '-q', '-F', '-L', 'A22X', str(image)], check=True)
        self.assertEqual(fs.run_e2fsck(image, tool=e2fsck)['result'], 'clean')
        # A wrong link count on the root directory is an error e2fsck must repair.
        subprocess.run([debugfs, '-w', '-R', 'set_inode_field <2> links_count 7', str(image)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        repaired = fs.run_e2fsck(image, tool=e2fsck)
        self.assertEqual(repaired['result'], 'repaired', repaired)
        self.assertIn(repaired['exit_code'], (1, 2))
        self.assertEqual(fs.run_e2fsck(image, tool=e2fsck)['result'], 'clean')
        garbage = self.root / 'zeros.img'
        with open(garbage, 'wb') as handle:
            handle.truncate(4 << 20)
        with self.assertRaises(fs.FilesystemError) as caught:
            fs.run_e2fsck(garbage, tool=e2fsck)
        self.assertEqual(caught.exception.code, 'CHECK_FAILED')
        self.assertGreaterEqual(caught.exception.details['exit_code'], 8)


class ResizeTest(FilesystemFixtureSupport):
    """TWRP's "Resize File System": check, then grow ext2/3/4 to fill its partition."""
    def test_fixture_admission_matches_check(self):
        self.config['volumes'][1]['mounted'] = False
        self.write_config()
        self.assertEqual(self.cli('resize', '--volume', 'sdcard-p1', ok=False)['error']['code'], 'CONFIRMATION_REQUIRED')
        self.assertEqual(self.cli('resize', '--volume', 'sdcard-p1', '--confirm')['result'], 'unchanged')
        self.assertEqual(self.cli('resize', '--volume', 'userdata', '--confirm', ok=False)['error']['code'], 'VOLUME_MOUNTED')

    def test_real_resize2fs_grows_a_small_filesystem_once(self):
        import shutil
        import subprocess
        tools = [shutil.which(name) for name in ('mkfs.ext4', 'e2fsck', 'resize2fs', 'tune2fs')]
        if not all(tools):
            self.skipTest('e2fsprogs are not installed on this host')
        mkfs, e2fsck, resize2fs, tune2fs = tools
        image = self.root / 'grow.img'
        with open(image, 'wb') as handle:
            handle.truncate(32 << 20)
        subprocess.run([mkfs, '-q', '-F', str(image), '16M'], check=True)
        grown = fs.run_resize2fs(image, tools=(e2fsck, resize2fs, tune2fs))
        self.assertEqual((grown['result'], grown['check'], grown['bytes_before'], grown['bytes_after']),
                         ('grown', 'clean', 16 << 20, 32 << 20))
        again = fs.run_resize2fs(image, tools=(e2fsck, resize2fs, tune2fs))
        self.assertEqual((again['result'], again['bytes_after']), ('unchanged', 32 << 20))
        # A lost primary superblock is repaired from a backup (e2fsck exits 1); no filesystem at all fails.
        with open(image, 'r+b') as handle:
            handle.write(bytes(4096))
        self.assertEqual(fs.run_resize2fs(image, tools=(e2fsck, resize2fs, tune2fs))['check'], 'repaired')
        empty = self.root / 'empty.img'
        with open(empty, 'wb') as handle:
            handle.truncate(8 << 20)
        with self.assertRaises(fs.FilesystemError) as caught:
            fs.run_resize2fs(empty, tools=(e2fsck, resize2fs, tune2fs))
        self.assertEqual(caught.exception.code, 'CHECK_FAILED')
