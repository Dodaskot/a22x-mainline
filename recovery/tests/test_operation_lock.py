import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
from operation_lock import ENV_FD, operation_lock
from admin_session import command_argv


class OperationLockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'lock'

    def tearDown(self):
        self.temp.cleanup()

    def assert_locked(self):
        with self.path.open('rb') as other:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_a_job_waits_for_a_screenshot_only_when_told_and_only_until_then(self):
        # 2026-11-14 review: a job started while a screenshot still held the lease failed OPERATION_BUSY.
        import threading
        import time
        import operation_lock as ol
        holder = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX)
        with mock.patch.dict(os.environ, {ol.WAIT_ENV: ''}):
            with self.assertRaises(BlockingIOError):
                with operation_lock(self.path, 'fixture'):
                    pass
        with mock.patch.dict(os.environ, {ol.WAIT_ENV: '%.3f' % (time.monotonic() + 0.2)}):
            started = time.monotonic()
            with self.assertRaises(BlockingIOError):
                with operation_lock(self.path, 'fixture'):
                    pass
            self.assertGreaterEqual(time.monotonic() - started, 0.15)
        threading.Timer(0.2, lambda: fcntl.flock(holder, fcntl.LOCK_UN)).start()
        with mock.patch.dict(os.environ, {ol.WAIT_ENV: '%.3f' % (time.monotonic() + 5)}):
            with operation_lock(self.path, 'fixture'):
                pass
        for strange in ('nan', 'inf', 'x'):
            with mock.patch.dict(os.environ, {ol.WAIT_ENV: strange}):
                self.assertLessEqual(ol._wait_until(), time.monotonic() + ol.MAX_WAIT)

    def test_a_step_inside_the_lease_of_its_own_process_borrows_it(self):
        # 2026-11-14 review: distro.py held the lease and wifi.py asked for it again in the same
        # process; the second flock was refused and the saved key was never read from the disk.
        import operation_lock as ol
        with operation_lock(self.path, 'fixture'):
            with ol.lease(self.path, 'fixture') as fd:
                self.assertIsNone(fd)
        with ol.lease(self.path, 'fixture') as fd:
            self.assertIsNotNone(fd)
            self.assert_locked()

    def test_a_lease_is_lent_only_within_its_own_thread(self):
        # 2026-11-14 review: adbd's threads borrowed each other's lease; one read a cache the other had
        # just unmounted, found no settings, and let adb in without the password.
        import threading
        import operation_lock as ol
        seen = []
        with operation_lock(self.path, 'fixture'):
            def other():
                try:
                    with ol.lease(self.path, 'fixture'):
                        seen.append('borrowed')
                except BlockingIOError:
                    seen.append('refused')
            thread = threading.Thread(target=other)
            thread.start()
            thread.join()
            # Spelled with '..', the same lock is found in this thread (2026-11-14 review).
            odd = Path(self.temp.name) / 'x' / '..' / 'lock'
            self.assertTrue(ol.held(odd))
        self.assertEqual(seen, ['refused'])

    def test_an_inherited_lease_is_lent_to_one_thread_only(self):
        # 2026-11-14 review: with the lease inherited, a second thread duplicated it and its flock
        # on the same open file succeeded.
        import threading
        import operation_lock as ol
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        seen = []
        with mock.patch.dict(os.environ, {ENV_FD: str(fd)}):
            with operation_lock(self.path, 'device'):
                def other():
                    try:
                        with ol.lease(self.path, 'device'):
                            seen.append('borrowed')
                    except BlockingIOError:
                        seen.append('refused')
                thread = threading.Thread(target=other)
                thread.start()
                thread.join()
        self.assertEqual(seen, ['refused'])

    def test_child_reuses_lease_without_unlocking_parent_and_ui_is_blocked(self):
        with operation_lock(self.path, 'fixture') as fd:
            code = 'from operation_lock import operation_lock; import sys;\nwith operation_lock(sys.argv[1], "device"): print("CHILD_OK")'
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
            env[ENV_FD] = str(fd)
            r = subprocess.run([sys.executable, '-c', code, str(self.path)], env=env,
                               pass_fds=(fd,), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), 'CHILD_OK')
            self.assert_locked()
            with mock.patch.dict(os.environ, {ENV_FD: str(fd)}):
                with self.assertRaises(BlockingIOError):
                    with operation_lock(self.path, 'fixture'):
                        pass
        with operation_lock(self.path, 'fixture'):
            self.assert_locked()

    def test_wrong_descriptor_and_replaced_inode_are_rejected(self):
        other = Path(self.temp.name) / 'other'
        with operation_lock(self.path, 'fixture') as fd:
            with operation_lock(other, 'fixture') as wrong:
                with mock.patch.dict(os.environ, {ENV_FD: str(wrong)}):
                    with self.assertRaises(OSError):
                        with operation_lock(self.path):
                            pass
            self.path.rename(self.path.with_suffix('.old'))
            self.path.touch()
            with mock.patch.dict(os.environ, {ENV_FD: str(fd)}):
                with self.assertRaises(OSError):
                    with operation_lock(self.path):
                        pass

    def test_client_command_dispatch_keeps_sftp_stdout_clean(self):
        self.assertEqual(command_argv('/usr/lib/ssh/sftp-server'), ['/usr/lib/ssh/sftp-server', '-u', '0077'])
        self.assertEqual(command_argv(''), ['/bin/sh', '-l'])
        self.assertEqual(command_argv('uname -a'), ['/bin/sh', '-c', 'uname -a'])


if __name__ == '__main__':
    unittest.main()
