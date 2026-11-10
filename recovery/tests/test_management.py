"""Diagnostic export tests against owned fixture directories only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import filesystem as fs
import management as mg


class ManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.volume = self.root / 'volume'
        self.volume.mkdir()
        self.logs = self.root / 'management-logs'
        self.logs.mkdir()
        self.spec = {'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'sdcard-p1', 'path': 'volume', 'fs_type': 'ext4',
             'mounted': True, 'read_only': False}]}
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(self.spec))
        self.ctx = fs.FixtureContext(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_is_complete_atomic_new_archive_with_content_hashes(self):
        (self.logs / 'ui.log').write_text('actual UI exception\n')
        (self.logs / 'ssh-private-key').write_text('SECRET-NEVER-EXPORT')
        (self.volume / 'private-user-file').write_text('USER-SECRET')
        token = self.ctx.volume('sdcard-p1')['volume_token']
        result = mg.export(self.ctx, 'sdcard-p1', token, True)
        output = Path(result['archive_path'])
        raw = output.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['sha256'])
        self.assertEqual(result['bytes'], len(raw))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            self.assertEqual(set(archive.getnames()), {'manifest.json', 'logs/ui.log'})
            manifest = json.load(archive.extractfile('manifest.json'))
            for name, entry in manifest['files'].items():
                data = archive.extractfile(name).read()
                self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(entry['bytes'], len(data))
        self.assertFalse(list(self.volume.glob('.a22x-log-export-*')))
        again = mg.export(self.ctx, 'sdcard-p1', token, True)
        self.assertNotEqual(again['path'], result['path'])
        self.assertEqual(output.read_bytes(), raw)

    def test_export_rejects_readonly_and_stale_selection_and_no_confirmation(self):
        for kwargs, code in [({'confirmed': False}, 'CONFIRMATION_REQUIRED'),
                             ({'confirmed': True, 'expected': '0' * 64}, 'VOLUME_CHANGED')]:
            with self.assertRaises(fs.FilesystemError) as exc:
                mg.export(self.ctx, 'sdcard-p1', **kwargs)
            self.assertEqual(exc.exception.code, code)
        self.ctx.mount('sdcard-p1', 'ro')
        with self.assertRaises(fs.FilesystemError) as exc:
            mg.export(self.ctx, 'sdcard-p1', confirmed=True)
        self.assertEqual(exc.exception.code, 'VOLUME_READ_ONLY')
        self.assertEqual(list(self.volume.iterdir()), [])

    def test_symlink_log_is_not_followed_and_large_log_is_tail_only(self):
        secret = self.root / 'private-key'
        secret.write_text('SENSITIVE')
        (self.logs / 'ui.log').symlink_to(secret)
        (self.logs / 'ui.stdout').write_bytes(b'OLD' + b'x' * mg.MAX_LOG)
        with tarfile.open(fileobj=io.BytesIO(mg.archive_bytes(self.ctx)), mode='r:gz') as archive:
            self.assertNotIn('logs/ui.log', archive.getnames())
            self.assertEqual(archive.extractfile('logs/ui.stdout').read(), b'x' * mg.MAX_LOG)
            manifest = json.load(archive.extractfile('manifest.json'))
            self.assertTrue(manifest['files']['logs/ui.stdout']['tail_only'])

    def test_rotation_retains_last_four_runs_and_bounds_each_file(self):
        for i in range(7):
            (self.logs / 'ui.log').write_text('run-' + str(i))
            mg.rotate(self.ctx)
        self.assertFalse((self.logs / 'ui.log').exists())
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()),
                         ['ui.log.1', 'ui.log.2', 'ui.log.3', 'ui.log.4'])
        for i in range(1, 5):
            self.assertEqual((self.logs / f'ui.log.{i}').read_text(), 'run-' + str(7 - i))
        (self.logs / 'ui.stdout').write_bytes(b'old' + b'x' * 1024 * 1024)
        mg.rotate(self.ctx)
        self.assertEqual((self.logs / 'ui.stdout.1').read_bytes(), b'x' * 1024 * 1024)

    def test_status_cli_is_json(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(mg.main(['--fixture', str(self.root), 'status']), 0)
        value = json.loads(stream.getvalue())
        self.assertTrue(value['ok'])
        self.assertTrue(value['linux_only'])
        self.assertEqual(value['power']['targets'], ['normal', 'recovery', 'download', 'poweroff'])

    def test_missing_process_identity_does_not_report_a_dead_ssh_server_as_running(self):
        with mock.patch.object(mg, 'proc_start', return_value=None):
            self.assertFalse(mg.process_running({'pid': 1234, 'process_start': None}))
            self.assertFalse(mg.process_running({'pid': 1234, 'process_start': '123'}))
        with mock.patch.object(mg, 'proc_start', return_value='123'):
            self.assertTrue(mg.process_running({'pid': 1234, 'process_start': '123'}))
            self.assertFalse(mg.process_running({'pid': 1234, 'process_start': '122'}))


if __name__ == '__main__':
    unittest.main()
