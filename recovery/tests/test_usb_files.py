"""MTP lifecycle fixtures: real IPC/leases, no USB, mounts or block devices.

Run on a normal Linux host; cloud exec's socket/PID virtualization cannot test
the private AF_UNIX control channel. Native path guards also run in cloud exec.
"""
import argparse
import ctypes
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import usb_files as usb
from operation_lock import operation_lock


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='a22x-mtp-native-')
        cls.base = Path(cls.temp.name)
        cls.export = cls.base / 'files'
        cls.export.mkdir()
        library = cls.base / 'guard.so'
        subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-DA22X_EXPORT_ROOT="' + str(cls.export) + '"',
                        str(ROOT / 'usb/mtp_guard.c'), '-o', str(library)], check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library), use_errno=True)
        cls.lib.a22x_open.argtypes = (ctypes.c_char_p, ctypes.c_int, ctypes.c_uint)
        cls.lib.a22x_open.restype = ctypes.c_int
        cls.lib.a22x_stat.argtypes = (ctypes.c_char_p, ctypes.c_void_p)
        cls.lib.a22x_stat.restype = ctypes.c_int
        cls.lib.a22x_remove.argtypes = (ctypes.c_char_p,)
        cls.lib.a22x_remove.restype = ctypes.c_int
        cls.lib.a22x_rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
        cls.lib.a22x_rename.restype = ctypes.c_int
        cls.lib.a22x_truncate.argtypes = (ctypes.c_char_p, ctypes.c_longlong)
        cls.lib.a22x_truncate.restype = ctypes.c_int
        cls.lib.a22x_mkdir.argtypes = (ctypes.c_char_p, ctypes.c_uint)
        cls.lib.a22x_mkdir.restype = ctypes.c_int
        cls.lib.a22x_name_ok.argtypes = (ctypes.c_char_p,)
        cls.lib.a22x_name_ok.restype = ctypes.c_int
        cls.lib.a22x_write_all.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t)
        cls.lib.a22x_write_all.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case = self.export / self._testMethodName
        self.case.mkdir()

    def test_names_are_single_nonempty_components(self):
        for name in ('', '.', '..', '../outside', '/absolute', 'a/b', 'a\\b', 'x' * 256):
            self.assertEqual(self.lib.a22x_name_ok(name.encode()), 0, name)
        for name in ('hello.txt', 'конфіг.txt', '.hidden', 'with space'):
            self.assertEqual(self.lib.a22x_name_ok(name.encode()), 1, name)

    def test_symlinks_parent_and_leaf_cannot_read_or_truncate(self):
        outside = self.base / 'outside'
        outside.write_bytes(b'untouched')
        (self.case / 'file-link').symlink_to(outside)
        (self.case / 'dir-link').symlink_to(self.base, target_is_directory=True)
        for path in (self.case / 'file-link', self.case / 'dir-link/outside'):
            for flags in (os.O_RDONLY, os.O_WRONLY | os.O_TRUNC):
                self.assertEqual(self.lib.a22x_open(os.fsencode(path), flags, 0), -1)
        self.assertEqual(outside.read_bytes(), b'untouched')

    def test_in_export_symlinks_also_refused(self):
        data = self.case / 'regular'
        data.write_bytes(b'original')
        link = self.case / 'link'
        link.symlink_to('regular')
        self.assertEqual(self.lib.a22x_open(os.fsencode(link), os.O_WRONLY | os.O_TRUNC, 0), -1)
        self.assertEqual(self.lib.a22x_stat(os.fsencode(link), ctypes.create_string_buffer(512)), -1)
        self.assertEqual(data.read_bytes(), b'original')

    def test_dotdot_and_lookalike_prefix_refused(self):
        outside = self.base / 'elsewhere'
        outside.write_bytes(b'original')
        for path in (str(self.export) + '/../elsewhere', str(self.export) + '-other/item', '/etc/passwd'):
            self.assertEqual(self.lib.a22x_open(os.fsencode(path), os.O_WRONLY | os.O_TRUNC, 0), -1)
        self.assertEqual(outside.read_bytes(), b'original')

    def test_fifo_filtered_and_never_blocks(self):
        fifo = self.case / 'fifo'
        os.mkfifo(fifo)
        self.assertEqual(self.lib.a22x_open(os.fsencode(fifo), os.O_RDONLY, 0), -1)
        self.assertEqual(self.lib.a22x_stat(os.fsencode(fifo), ctypes.create_string_buffer(512)), -1)
        self.assertEqual(self.lib.a22x_remove(os.fsencode(fifo)), -1)
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_in_place_write_preserves_inode_mode_and_hardlinks(self):
        path = self.case / 'file'
        path.write_bytes(b'old')
        path.chmod(0o640)
        other = self.case / 'hardlink'
        os.link(path, other)
        before = path.stat()
        fd = self.lib.a22x_open(os.fsencode(path), os.O_WRONLY | os.O_TRUNC, 0)
        self.assertGreaterEqual(fd, 0)
        try:
            data = b'new utf8 \xd1\x96\xd0\xbc\xd1\x8f'
            self.assertEqual(self.lib.a22x_write_all(fd, data, len(data)), 0)
            os.fsync(fd)
        finally:
            os.close(fd)
        after = path.stat()
        self.assertEqual((before.st_ino, before.st_uid, before.st_gid, before.st_mode, before.st_nlink),
                         (after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_nlink))
        self.assertEqual(other.read_bytes(), data)

    def test_create_rename_truncate_delete_under_export(self):
        directory = self.case / 'new'
        self.assertEqual(self.lib.a22x_mkdir(os.fsencode(directory), 0o755), 0)
        path = directory / 'text'
        fd = self.lib.a22x_open(os.fsencode(path), os.O_WRONLY | os.O_CREAT, 0o644)
        self.assertGreaterEqual(fd, 0)
        os.write(fd, b'abcdef')
        os.close(fd)
        target = directory / 'renamed'
        self.assertEqual(self.lib.a22x_rename(os.fsencode(path), os.fsencode(target)), 0)
        self.assertEqual(self.lib.a22x_truncate(os.fsencode(target), 3), 0)
        self.assertEqual(target.read_bytes(), b'abc')
        self.assertEqual(self.lib.a22x_remove(os.fsencode(target)), 0)
        self.assertEqual(self.lib.a22x_remove(os.fsencode(directory)), 0)

    def test_rename_delete_and_mkdir_do_not_traverse_link(self):
        target = self.base / 'outside-directory'
        target.mkdir()
        outside = target / 'file'
        outside.write_bytes(b'outside')
        link = self.case / 'link'
        link.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.lib.a22x_remove(os.fsencode(link / 'file')), -1)
        self.assertEqual(self.lib.a22x_remove(os.fsencode(link)), -1)
        self.assertEqual(self.lib.a22x_mkdir(os.fsencode(link / 'new'), 0o755), -1)
        source = self.case / 'inside'
        source.write_bytes(b'inside')
        self.assertEqual(self.lib.a22x_rename(os.fsencode(source), os.fsencode(link / 'file')), -1)
        self.assertEqual(outside.read_bytes(), b'outside')
        self.assertEqual(source.read_bytes(), b'inside')

    def test_full_device_is_a_real_write_error(self):
        fd = os.open('/dev/full', os.O_WRONLY)
        try:
            self.assertEqual(self.lib.a22x_write_all(fd, b'bytes', 5), -1)
            self.assertEqual(ctypes.get_errno(), errno.ENOSPC)
        finally:
            os.close(fd)


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('A22X_USB_TEST_IPC') != '1':
            raise unittest.SkipTest('Set A22X_USB_TEST_IPC=1 on a normal Linux host with AF_UNIX and matching /proc PIDs')
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.close()
        assert usb.process_start(os.getpid()) is not None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a22x-usb-lifecycle-')
        self.root = Path(self.temp.name)
        for name in ('data', 'sd', 'cache'):
            (self.root / name).mkdir()
        self.config = {'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'data', 'mounted': True, 'read_only': False},
            {'id': 'sdcard-p1', 'path': 'sd', 'mounted': True, 'read_only': True},
            {'id': 'cache', 'path': 'cache', 'mounted': True, 'read_only': False}]}
        self.write_config()
        (self.root / 'data/untouched').write_bytes(b'original')
        self.context = usb.Context(argparse.Namespace(mode='fixture', fixture=self.root))

    def tearDown(self):
        record = self.context.record()
        if record and usb.process_live(record):
            try:
                self.cli('stop', '--expected-session-token', record['session_token'])
            except Exception:
                # The supervisor may end between the look and the stop (it does once its responder
                # has exited): nothing is left to stop then.
                try:
                    os.kill(record['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if self.context.socket_dir.exists():
            for path in self.context.socket_dir.iterdir():
                path.unlink()
            self.context.socket_dir.rmdir()
        self.temp.cleanup()

    def write_config(self):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(self.config))

    def cli(self, *args, success=True, env=None):
        run = subprocess.run([sys.executable, str(ROOT / 'usb_files.py'), '--mode', 'fixture',
                              '--fixture', str(self.root), *args], text=True, capture_output=True,
                             timeout=15, env=env)
        value = json.loads(run.stdout)
        self.assertEqual(value['ok'], success, (run.stdout, run.stderr))
        self.assertEqual(run.returncode == 0, success, (run.stdout, run.stderr))
        return value

    def start(self, readonly=False, selected=None, success=True):
        if selected is None:
            selected = self.cli('status')['candidates'][0]
        args = ['start', '--volume', selected['id'], '--expected-volume-token', selected['volume_token'],
                '--expected-mount-token', selected['mount_token']]
        return self.cli(*(args + (['--read-only'] if readonly else [])), success=success)

    def stop(self, value):
        return self.cli('stop', '--expected-session-token', value['session_token'])

    def test_ro_rw_lifetime_lease_and_exact_stop(self):
        before = self.cli('status')
        self.assertEqual([x['id'] for x in before['candidates']], ['userdata', 'sdcard-p1'])
        for ro in (True, False):
            active = self.start(readonly=ro)
            self.assertEqual(active['state'], 'active')
            self.assertEqual(active['exports'][0]['access'], 'ro' if ro else 'rw')
            self.assertEqual(active['exports'][0]['label'], 'userdata')
            with self.assertRaises(BlockingIOError):
                with operation_lock(self.context.fs.lock_path, 'fixture'):
                    pass
            self.assertEqual(self.cli('status')['state'], 'active')
            stale = self.cli('stop', '--expected-session-token', '0' * 64, success=False)
            self.assertEqual(stale['error']['code'], 'SESSION_CHANGED')
            self.assertEqual(self.cli('status')['state'], 'active')
            self.assertEqual(self.stop(active)['state'], 'stopped')
            with operation_lock(self.context.fs.lock_path, 'fixture'):
                pass
        self.assertEqual((self.root / 'data/untouched').read_bytes(), b'original')

    def test_stale_mount_and_volume_before_child_start(self):
        selected = self.cli('status')['candidates'][0]
        self.config['volumes'][0]['read_only'] = True
        self.write_config()
        stale = self.start(selected=selected, success=False)
        self.assertEqual(stale['error']['code'], 'MOUNT_CHANGED')
        self.assertTrue(self.context.record()['clean'])
        self.assertNotIn('child_pid', self.context.record())
        selected = self.cli('status')['candidates'][0]
        selected['volume_token'] = '0' * 64
        self.assertEqual(self.start(selected=selected, success=False)['error']['code'], 'VOLUME_CHANGED')

    def test_other_operation_and_second_start_are_blocked(self):
        selected = self.cli('status')['candidates'][0]
        with operation_lock(self.context.fs.lock_path, 'fixture'):
            blocked = self.start(selected=selected, success=False)
        self.assertEqual(blocked['error']['code'], 'OPERATION_BUSY')
        active = self.start()
        second = self.start(selected=selected, success=False)
        self.assertEqual(second['error']['code'], 'USB_EXPORT_ACTIVE')
        self.stop(active)

    def test_missing_binary_does_not_affect_fixture_and_non_admitted_volume_refused(self):
        selected = self.cli('status')['candidates'][0]
        selected['id'] = 'cache'
        v = self.context.fs.volume('cache')
        with usb.fs.PinnedVolume(self.context.fs, 'cache') as pin:
            selected.update(volume_token=v['volume_token'], mount_token=usb.mount_token(self.context, pin))
        self.assertEqual(self.start(selected=selected, success=False)['error']['code'], 'VOLUME_NOT_ADMITTED')

    def test_inherited_administrator_lease_cannot_start_persistent_export(self):
        selected = self.cli('status')['candidates'][0]
        env = dict(os.environ, A22X_RECOVERY_LOCK_FD='123')
        result = self.cli('start', '--volume', selected['id'], '--expected-volume-token', selected['volume_token'],
                          '--expected-mount-token', selected['mount_token'], success=False, env=env)
        self.assertEqual(result['error']['code'], 'INHERITED_SESSION')
        self.assertFalse(self.context.record_path.exists())

    def test_child_exit_is_observed_and_lease_released(self):
        active = self.start()
        record = self.context.record()
        os.kill(record['child_pid'], signal.SIGTERM)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.context.record().get('clean'):
                break
            time.sleep(.05)
        record = self.context.record()
        self.assertTrue(record['clean'])
        self.assertEqual(record['reason'], 'USB_RESPONDER_FAILED')
        with operation_lock(self.context.fs.lock_path, 'fixture'):
            pass

    def test_supervisor_death_kills_child_but_never_claims_clean_stop(self):
        self.start()
        record = self.context.record()
        os.kill(record['pid'], signal.SIGKILL)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                with operation_lock(self.context.fs.lock_path, 'fixture'):
                    break
            except BlockingIOError:
                time.sleep(.05)
        with operation_lock(self.context.fs.lock_path, 'fixture'):
            pass
        status = self.cli('status')
        self.assertFalse(status['available'])
        self.assertEqual(status['reason'], 'SERVICE_STALE')
        self.assertFalse(self.context.record()['clean'])


class StopAndSignalTests(unittest.TestCase):
    """2026-11-14 review: status while stopping, a stop that outlasts its wait, and a signal during setup."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a22x-usb-stop-')
        self.root = Path(self.temp.name)
        for name in ('data', 'cache'):
            (self.root / name).mkdir()
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'data', 'mounted': True, 'read_only': False},
            {'id': 'cache', 'path': 'cache', 'mounted': True, 'read_only': False}]}))
        self.context = usb.Context(argparse.Namespace(mode='fixture', fixture=self.root))
        self.addCleanup(self.temp.cleanup)

    def test_a_stopping_supervisor_is_not_asked(self):
        record = {'state': 'stopping', 'pid': os.getpid(), 'process_start': usb.process_start(os.getpid()),
                  'session_token': 'a' * 64, 'exports': [], 'clean': False}
        from unittest import mock
        with mock.patch.object(self.context, 'record', return_value=record), \
                mock.patch.object(usb, 'request', side_effect=AssertionError('no request while stopping')):
            self.assertEqual(usb.status(self.context)['state'], 'stopping')
        record['state'] = 'active'
        with mock.patch.object(self.context, 'record', return_value=record), \
                mock.patch.object(usb, 'request', side_effect=TimeoutError('timed out')):
            found = usb.status(self.context)
        self.assertEqual((found['ok'], found['state'], found['reason']), (True, 'active', 'SERVICE_BUSY'))

    def test_a_stop_that_outlasts_its_wait_is_pending_not_failed(self):
        from unittest import mock
        record = {'state': 'stopping', 'pid': os.getpid(), 'process_start': usb.process_start(os.getpid()),
                  'session_token': 'b' * 64, 'exports': [], 'clean': False}
        out = []
        with mock.patch.object(usb.Context, 'record', return_value=record), \
                mock.patch.object(usb, 'request', side_effect=TimeoutError('timed out')), \
                mock.patch('builtins.print', lambda text, **k: out.append(text)):
            code = usb.main(['--mode', 'fixture', '--fixture', str(self.root), 'stop', '--expected-session-token', 'b' * 64])
        found = json.loads(out[-1])
        self.assertEqual((code, found['ok'], found['reason']), (0, True, 'USB_STOP_PENDING'))

    def test_a_signal_during_setup_unwinds_through_the_cleanup(self):
        from unittest import mock
        selected = usb.status(self.context)['candidates'][0]
        args = argparse.Namespace(volume=selected['id'], expected_volume_token=selected['volume_token'],
                                  expected_mount_token=selected['mount_token'], read_only=True, session_token='c' * 64)
        real = usb.process_start

        def started_then_signal(pid):
            # The responder is running and the gadget would be unbound: SIGTERM arrives now.
            value = real(pid)
            if pid != os.getpid():
                os.kill(os.getpid(), signal.SIGTERM)
            return value
        old = [signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)]
        self.addCleanup(lambda: [signal.signal(n, h) for n, h in zip((signal.SIGTERM, signal.SIGINT), old)])
        with mock.patch.object(usb, 'process_start', started_then_signal), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(usb.ENV_FD, None)
            self.assertEqual(usb.serve(self.context, args), 2)
        record = self.context.record()
        self.assertEqual((record['state'], record['clean'], record['reason']), ('failed', True, 'USB_STOP_REQUESTED'))
        with operation_lock(self.context.fs.lock_path, 'fixture'):
            pass

    def test_a_second_signal_during_the_cleanup_does_not_cut_it_short(self):
        # 2026-11-14 review: a second SIGTERM while the export was torn down raised inside the cleanup,
        # the gadget was never restored and the record stayed unclean.
        from unittest import mock
        selected = usb.status(self.context)['candidates'][0]
        args = argparse.Namespace(volume=selected['id'], expected_volume_token=selected['volume_token'],
                                  expected_mount_token=selected['mount_token'], read_only=True, session_token='d' * 64)
        real_start, real_stop = usb.process_start, usb.child_stop

        def started_then_signal(pid):
            value = real_start(pid)
            if pid != os.getpid():
                os.kill(os.getpid(), signal.SIGTERM)
            return value

        def stop_with_another_signal(child):
            os.kill(os.getpid(), signal.SIGTERM)
            return real_stop(child)
        old = [signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)]
        self.addCleanup(lambda: [signal.signal(n, h) for n, h in zip((signal.SIGTERM, signal.SIGINT), old)])
        with mock.patch.object(usb, 'process_start', started_then_signal), \
                mock.patch.object(usb, 'child_stop', stop_with_another_signal), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(usb.ENV_FD, None)  # restored when the patch ends (2026-11-14 review)
            self.assertEqual(usb.serve(self.context, args), 2)
        record = self.context.record()
        self.assertEqual((record['state'], record['clean']), ('failed', True))
        self.assertEqual([signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)], old)


if __name__ == '__main__':
    unittest.main()
