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
