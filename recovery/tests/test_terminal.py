"""Real PTY, private IPC and process/lease lifecycle fixtures on a Linux host.

No phone, USB, mount or block-device operation is performed. Run the functional
cases on a normal Linux host: the cloud executor virtualizes UNIX peer PIDs.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import terminal


class ShellAdmissionTests(unittest.TestCase):
    def shell(self, root):
        raw = bytearray(128)
        raw[:7] = b'\x7fELF\x02\x01\x01'
        raw[18:20] = (183).to_bytes(2, 'little')
        path = Path(root) / 'owned-shell'
        path.write_bytes(raw)
        path.chmod(0o700)
        return path, bytes(raw), hashlib.sha256(raw).hexdigest()

    def test_pinned_descriptor_survives_path_replacement_and_is_cloexec(self):
        with tempfile.TemporaryDirectory(prefix='a22x-shell-pin-') as directory:
            path, raw, digest = self.shell(directory)
            fd = terminal.open_pinned_shell(path, digest, machine=183)
            try:
                self.assertFalse(os.get_inheritable(fd))
                replacement = path.with_name('replacement')
                replacement.write_bytes(b'not the reviewed shell')
                replacement.chmod(0o700)
                replacement.replace(path)
                self.assertEqual(os.pread(fd, 256, 0), raw)
                self.assertNotEqual(path.read_bytes(), raw)
            finally:
                os.close(fd)

    def test_unreviewed_or_writable_shell_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='a22x-shell-pin-') as directory:
            path, raw, digest = self.shell(directory)
            for mutation in ('bytes', 'writable', 'architecture', 'hardlink', 'symlink'):
                path.unlink()
                path.write_bytes(raw)
                path.chmod(0o700)
                extra = path.with_name('extra')
                extra.unlink(missing_ok=True)
                if mutation == 'bytes':
                    path.write_bytes(raw[:-1] + b'x')
                elif mutation == 'writable':
                    path.chmod(0o722)
                elif mutation == 'hardlink':
                    os.link(path, extra)
                elif mutation == 'symlink':
                    path.rename(extra)
                    path.symlink_to(extra)
                machine = 62 if mutation == 'architecture' else 183
                with self.subTest(mutation=mutation), self.assertRaises(terminal.TerminalError):
                    terminal.open_pinned_shell(path, digest, machine=machine)

    def test_fixture_override_never_enters_device_mode(self):
        with tempfile.TemporaryDirectory(prefix='a22x-shell-pin-') as directory:
            path, _, digest = self.shell(directory)
            for mode, shell, sha in [('device', path, digest), ('fixture', path, None),
                                     ('fixture', None, digest)]:
                with self.subTest(mode=mode, shell=bool(shell)), self.assertRaises(terminal.TerminalError) as caught:
                    terminal.Context(argparse.Namespace(mode=mode, fixture=Path(directory),
                        fixture_shell=shell, fixture_shell_sha256=sha))
                self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')

    def test_fixture_pinned_shell_and_utf8_environment_are_explicit(self):
        with tempfile.TemporaryDirectory(prefix='a22x-shell-pin-') as directory:
            path, _, digest = self.shell(directory)
            context = terminal.Context(argparse.Namespace(mode='fixture', fixture=Path(directory),
                fixture_shell=path, fixture_shell_sha256=digest))
            fd, argv = context.open_shell()
            try:
                self.assertEqual(argv, ['ash', '-i'])
                self.assertEqual(terminal.snapshot(context)['shell_sha256'], digest)
            finally:
                os.close(fd)
            env = terminal.environment('fixture', root=Path(directory))
            self.assertEqual((env['LANG'], env['LC_ALL'], env['TERM']), ('C.UTF-8', 'C.UTF-8', 'vt100'))
            self.assertEqual(env['HISTFILE'], '/dev/null')


class RingTests(unittest.TestCase):
    def test_missing_kernel_descendant_observation_refuses_before_start(self):
        with tempfile.TemporaryDirectory(prefix='a22x-terminal-readiness-') as directory:
            context = terminal.Context(argparse.Namespace(mode='fixture', fixture=Path(directory)))
            with mock.patch.object(terminal.Path, 'open', side_effect=FileNotFoundError):
                with self.assertRaises(terminal.TerminalError) as caught:
                    context.ready()
                self.assertEqual(caught.exception.code, 'TERMINAL_UNAVAILABLE')
                value = terminal.status(context)
                self.assertFalse(value['available'])
                self.assertEqual(value['reason'], 'TERMINAL_UNAVAILABLE')
            self.assertFalse(context.base.exists())

    def test_absolute_cursor_and_overflow_are_byte_exact(self):
        ring = terminal.Ring(13)
        raw = 'абвгґдежз'.encode()
        ring.append(raw[:7])
        ring.append(raw[7:])
        result = ring.read(0, 5)
        self.assertEqual(result['lost_bytes'], len(raw) - 13)
        self.assertEqual(base64.b64decode(result['data_base64']), raw[-13:-8])
        rest = ring.read(result['next_seq'], 32768)
        self.assertEqual(base64.b64decode(rest['data_base64']), raw[-8:])
        self.assertEqual(rest['next_seq'], len(raw))
        self.assertEqual(rest['lost_bytes'], 0)

    def test_invalid_cursor_and_read_bounds_refused(self):
        ring = terminal.Ring()
        for after, size in [(-1, 1), (1, 1), (False, 1), (0, 0), (0, 32769), (0, True)]:
            with self.assertRaises(terminal.TerminalError):
                ring.read(after, size)

    def test_pending_input_is_bounded_before_acceptance(self):
        context = argparse.Namespace(mode='fixture', boot_id='test')
        record = {'state': 'active', 'session_token': 'a' * 64, 'shell_exit_code': None,
                  'clean': False}
        session = terminal.Session(context, record, -1)
        request = {'boot_id': 'test', 'operation': 'write', 'session_token': 'a' * 64,
                   'data_base64': base64.b64encode(b'x' * terminal.MAX_WRITE).decode()}
        for _ in range(terminal.MAX_PENDING // terminal.MAX_WRITE):
            result = session.command(request)
            self.assertEqual(result['bytes_written'], terminal.MAX_WRITE)
            self.assertEqual(result['accepted_bytes'], terminal.MAX_WRITE)
        with self.assertRaises(terminal.TerminalError) as caught:
            session.command(request)
        self.assertEqual(caught.exception.code, 'INPUT_BACKPRESSURE')
        self.assertEqual(len(session.pending), terminal.MAX_PENDING)


class CommandDetectionTests(unittest.TestCase):
    SHELL = 77

    def alive(self, states, children=(), adopted=()):
        session = terminal.Session.__new__(terminal.Session)
        session.record = {'state': 'active', 'shell_pid': self.SHELL}
        own = os.getpid()
        with mock.patch.object(terminal, 'process_info', lambda pid: {'state': states[pid]} if pid in states else None), \
                mock.patch.object(terminal, 'child_ids', lambda pid: set(children) if pid == self.SHELL else {self.SHELL, *adopted} if pid == own else set()):
            return session.command_alive()

    def test_shell_waiting_at_its_prompt_is_idle(self):
        self.assertFalse(self.alive({self.SHELL: 'S'}))

    def test_finished_job_the_shell_has_not_reaped_is_idle(self):
        self.assertFalse(self.alive({self.SHELL: 'S', 90: 'Z'}, children=[90]))

    def test_process_that_vanished_during_the_scan_is_idle(self):
        self.assertFalse(self.alive({self.SHELL: 'S'}, children=[90]))

    def test_child_adopted_orphan_and_busy_shell_are_running(self):
        self.assertTrue(self.alive({self.SHELL: 'S', 90: 'S'}, children=[90]))
        self.assertTrue(self.alive({self.SHELL: 'S', 91: 'R'}, adopted=[91]))
        self.assertTrue(self.alive({self.SHELL: 'R'}))
        self.assertTrue(self.alive({self.SHELL: 'D'}))

    def test_failed_inspection_keeps_the_session(self):
        session = terminal.Session.__new__(terminal.Session)
        session.record = {'state': 'active', 'shell_pid': self.SHELL}
        with mock.patch.object(terminal, 'process_info', return_value={'state': 'S'}), \
                mock.patch.object(terminal, 'child_ids', side_effect=terminal.TerminalError('DESCENDANTS_REMAIN', 'bound')):
            self.assertTrue(session.command_alive())

    def test_ended_shell_runs_nothing(self):
        session = terminal.Session.__new__(terminal.Session)
        session.record = {'state': 'exited', 'shell_pid': self.SHELL}
        self.assertFalse(session.command_alive())


class PtyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='a22x-terminal-test-')
        self.root = Path(self.temp.name)
        shell = os.environ.get('A22X_TERMINAL_TEST_SHELL')
        shell_sha = os.environ.get('A22X_TERMINAL_TEST_SHELL_SHA256')
        self.context = terminal.Context(argparse.Namespace(mode='fixture', fixture=self.root,
            fixture_shell=Path(shell) if shell else None, fixture_shell_sha256=shell_sha))
        self.base = [sys.executable, '-B', str(ROOT / 'terminal.py'), '--mode', 'fixture', '--fixture', str(self.root)]
        if shell:
            self.base += ['--fixture-shell', shell, '--fixture-shell-sha256', shell_sha]
        self.session = None
        self.cursor = 0
        self.output = bytearray()
        self.extra_processes = []
        self.env = {key: value for key, value in os.environ.items() if key != terminal.ENV_FD}
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def tearDown(self):
        record = self.context.record()
        if record and terminal.process_live(record):
            # Fault-injection sessions wait for this exact owned fixture marker.
            (self.root / 'allow-cleanup').touch(mode=0o600)
            try:
                self.call('stop', '--expected-session-token', record['session_token'])
            except (AssertionError, subprocess.TimeoutExpired):
                fd = os.pidfd_open(record['pid'])
                try:
                    if terminal.process_live(record):
                        signal.pidfd_send_signal(fd, signal.SIGTERM)
                finally:
                    os.close(fd)
            self.wait_for(lambda: not terminal.process_live(record), timeout=8)
        for process in self.extra_processes:
            process.wait(timeout=8)
        if self.context.socket_dir.exists():
            # The directory is unique to this fixture. No blanket /tmp cleanup.
            self.context.socket_path.unlink(missing_ok=True)
            self.context.socket_dir.rmdir()
        self.temp.cleanup()

    def call(self, *args, ok=True, env=None):
        process = subprocess.run([*self.base, *map(str, args)], env=self.env if env is None else env,
                                 capture_output=True, timeout=9)
        self.assertEqual(process.stderr, b'')
        result = json.loads(process.stdout)
        self.assertEqual(result['ok'], ok, result)
        self.assertEqual(process.returncode, 0 if ok else 2, result)
        self.assertEqual(result['operation'], args[0])
        return result

    def start(self, rows=21, cols=48):
        result = self.call('start', '--rows', rows, '--cols', cols)
        self.assertEqual(result['state'], 'active')
        self.assertTrue(result['available'])
        self.assertTrue(result['storage_lease_held'])
        self.session = result['session_token']
        self.assertRegex(self.session, r'^[0-9a-f]{64}$')
        return result

    def write(self, value):
        raw = value.encode() if isinstance(value, str) else value
        result = self.call('write', '--expected-session-token', self.session,
                           '--data-base64', base64.b64encode(raw).decode())
        self.assertEqual(result['bytes_written'], len(raw))
        self.assertEqual(result['accepted_bytes'], len(raw))
        return result

    def read(self, after=None):
        result = self.call('read', '--expected-session-token', self.session,
                           '--after-seq', self.cursor if after is None else after, '--max-bytes', 32768)
        raw = base64.b64decode(result['data_base64'], validate=True)
        self.assertEqual(result['next_seq'], max(self.cursor if after is None else after, result['start_seq']) + len(raw))
        self.cursor = result['next_seq']
        self.output.extend(raw)
        return result, raw

    def until_output(self, marker, timeout=5):
        marker = marker.encode() if isinstance(marker, str) else marker
        self.wait_for(lambda: marker in self.read()[1] or marker in self.output, timeout=timeout)
        return bytes(self.output)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.025)
        self.fail('The bounded terminal fixture did not complete')

    def lease_busy(self):
        fd = os.open(self.context.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(fd)

    def stop(self):
        result = self.call('stop', '--expected-session-token', self.session)
        self.assertEqual(result['state'], 'stopped')
        self.assertFalse(result['storage_lease_held'])
        self.assertFalse(self.lease_busy())
        self.wait_for(lambda: not terminal.process_live(self.context.record()))
        result = self.call('status')
        self.assertEqual(result['state'], 'stopped')
        self.assertIsNone(result['session_token'])
        self.assertFalse(result['storage_lease_held'])
        return result

    def test_status_is_readonly_and_no_autostart(self):
        result = self.call('status')
        self.assertEqual(result['state'], 'stopped')
        self.assertTrue(result['available'])
        self.assertFalse(self.context.base.exists())
        self.assertFalse(self.context.socket_dir.exists())

    def test_busy_storage_lease_and_inherited_ssh_start_refused(self):
        fd = os.open(self.context.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.call('start', ok=False)
            self.assertEqual(result['error']['code'], 'OPERATION_BUSY')
            self.assertFalse(terminal.process_live(self.context.record()))
        finally:
            os.close(fd)
        env = dict(self.env, **{terminal.ENV_FD: '999'})
        result = self.call('start', ok=False, env=env)
        self.assertEqual(result['error']['code'], 'INHERITED_SESSION')
        self.assertEqual(self.call('status', env=env)['state'], 'stopped')

    def test_real_tty_utf8_directory_dimensions_resize(self):
        self.start()
        self.assertTrue(self.lease_busy())
        self.write("stty -echo; printf 'TTY:'; test -t 0 && printf yes; printf '\\n'; pwd; stty size; printf 'UTF8:ім’я ґанок\\n'\n")
        output = self.until_output('UTF8:ім’я ґанок\r\n')
        self.assertIn(b'TTY:yes\r\n', output)
        self.assertIn(os.fsencode(self.root) + b'\r\n', output)
        self.assertIn(b'21 48\r\n', output)
        result = self.call('resize', '--expected-session-token', self.session, '--rows', 29, '--cols', 73)
        self.assertEqual((result['rows'], result['cols']), (29, 73))
        self.write("stty size; printf 'RESIZE_DONE\\n'\n")
        self.assertIn(b'29 73\r\n', self.until_output('RESIZE_DONE\r\n'))
        self.stop()

    def test_interactive_cat_and_ctrl_c_return_to_shell(self):
        self.start()
        self.write("stty -echo; printf 'READY\\n'; cat\n")
        self.until_output('READY\r\n')
        self.write('ввід у cat\n')
        self.until_output('ввід у cat\r\n')
        self.write(b'\x03')
        self.write("printf 'AFTER_INTERRUPT\\n'\n")
        self.until_output('AFTER_INTERRUPT\r\n')
        self.assertEqual(self.call('status')['state'], 'active')
        self.stop()

    def test_command_running_follows_commands_and_scripts(self):
        self.start()
        running = lambda: self.call('status')['command_running']
        idle = lambda: not running()
        self.wait_for(idle)
        self.write('sleep 1\n')
        self.wait_for(running)
        self.wait_for(idle)
        self.write('sleep 0.3 &\n')
        self.wait_for(running)
        self.wait_for(idle)
        # A loop has no child to find, and a script has moments between its children.
        self.write('while :; do :; done\n')
        self.wait_for(running)
        self.write(b'\x03')
        self.wait_for(idle)
        self.write('for n in 1 2 3 4 5 6 7 8; do sleep 0.2; done\n')
        self.wait_for(running)
        self.assertTrue(all(running() for _ in range(4)))
        self.wait_for(idle)
        self.stop()

    def test_status_without_session_reports_no_command(self):
        self.assertFalse(self.call('status')['command_running'])

    def test_natural_exit_retains_output_and_lease_until_close(self):
        self.start()
        self.write("stty -echo; printf 'FINAL_OUTPUT\\n'; exit 17\n")
        self.wait_for(lambda: self.call('status')['state'] == 'exited')
        output = self.until_output('FINAL_OUTPUT\r\n')
        self.assertIn(b'FINAL_OUTPUT', output)
        result = self.call('status')
        self.assertEqual(result['shell_exit_code'], 17)
        self.assertTrue(result['storage_lease_held'])
        self.assertTrue(self.lease_busy())
        result = self.call('write', '--expected-session-token', self.session, '--data-base64', 'eA==', ok=False)
        self.assertEqual(result['error']['code'], 'SHELL_EXITED')
        self.stop()

    def test_ring_overflow_does_not_block_supervisor(self):
        self.start()
        code = "import os;os.write(1,b'x'*600000+b'\\nRING_COMPLETE\\n')"
        self.write('stty -echo; ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(code) + '\n')
        self.wait_for(lambda: self.call('status')['end_seq'] > 600000, timeout=10)
        result, raw = self.read(after=0)
        self.assertGreater(result['lost_bytes'], 0)
        self.assertLessEqual(result['end_seq'] - result['start_seq'], terminal.RING_BYTES)
        self.assertEqual(len(raw), terminal.MAX_READ)
        self.until_output('RING_COMPLETE\r\n')
        self.stop()

    def test_wrong_token_boot_size_and_payload_refused(self):
        self.start()
        result = self.call('stop', '--expected-session-token', 'f' * 64, ok=False)
        self.assertEqual(result['error']['code'], 'SESSION_CHANGED')
        result = self.call('resize', '--expected-session-token', self.session, '--rows', 1, '--cols', 80, ok=False)
        self.assertEqual(result['error']['code'], 'INVALID_SIZE')
        result = self.call('write', '--expected-session-token', self.session, '--data-base64', '!bad!', ok=False)
        self.assertEqual(result['error']['code'], 'INVALID_INPUT')
        result = self.call('read', '--expected-session-token', self.session, '--after-seq', -1, ok=False)
        self.assertEqual(result['error']['code'], 'INVALID_CURSOR')
        record = self.context.record()
        result = terminal.request(self.context, record, 'read', boot_id='wrong', session_token=self.session,
                                  after_seq=0, max_bytes=1)
        self.assertEqual(result['error']['code'], 'BOOT_CHANGED')
        self.assertTrue(self.lease_busy())
        self.stop()

    def test_private_metadata_contains_no_input_output_or_history(self):
        self.start()
        secret = 'FIXTURE_PRIVATE_' + os.urandom(12).hex()
        self.write("stty -echo; printf '" + secret + "\\n'\n")
        self.until_output(secret + '\r\n')
        for path in (self.context.base, self.context.socket_dir):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.context.record_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.context.socket_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret.encode(), self.context.record_path.read_bytes())
        self.stop()
        files = {path.relative_to(self.root).as_posix() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(files, {'.operation.lock', '.terminal/state.json', '.terminal/supervisor.lock'})

    def spawn_detached_descendant(self):
        path = self.root / 'descendant.json'
        code = ('import os,sys,signal,json,time;'
                'p=os.fork();sys.exit(0) if p else None;os.setsid();'
                'p=os.fork();sys.exit(0) if p else None;'
                'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                'os.close(int(os.environ["A22X_RECOVERY_LOCK_FD"]));'
                'd=os.open("/dev/null",os.O_RDWR);[os.dup2(d,n) for n in (0,1,2)];'
                's=open("/proc/self/stat").read().rsplit(")",1)[1].split()[19];'
                'open(' + repr(str(path)) + ',"w").write(json.dumps({"pid":os.getpid(),"process_start":s}));'
                'time.sleep(60)')
        self.write(shlex.quote(sys.executable) + ' -c ' + shlex.quote(code) + '\n')
        self.wait_for(path.exists)
        record = json.loads(path.read_bytes())
        self.assertTrue(terminal.process_live(record))
        return record

    def test_close_reaps_double_fork_setsid_and_closed_inherited_lease(self):
        self.start()
        child = self.spawn_detached_descendant()
        self.assertTrue(self.lease_busy())
        self.stop()
        self.assertFalse(terminal.process_live(child))

    def test_natural_exit_reaps_detached_descendant_before_exited(self):
        self.start()
        child = self.spawn_detached_descendant()
        self.write('exit 0\n')
        self.wait_for(lambda: self.call('status')['state'] == 'exited', timeout=8)
        self.assertFalse(terminal.process_live(child))
        self.assertTrue(self.lease_busy())
        self.stop()

    def test_supervisor_sigterm_reaps_children_and_releases_lease(self):
        self.start()
        child = self.spawn_detached_descendant()
        record = self.context.record()
        fd = os.pidfd_open(record['pid'])
        try:
            self.assertTrue(terminal.process_live(record))
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        finally:
            os.close(fd)
        self.wait_for(lambda: not terminal.process_live(record), timeout=8)
        self.assertFalse(terminal.process_live(child))
        self.assertFalse(self.lease_busy())
        self.assertEqual(self.call('status')['state'], 'stopped')

    def test_startup_cleanup_failure_retains_control_and_lease(self):
        # Force the reviewed failure path after a real shell fork. The supervisor
        # may not unwind ExitStack while cleanup reports surviving descendants.
        code = '''
import argparse,os,sys
sys.path.insert(0,sys.argv[1])
import terminal
from pathlib import Path
root=Path(sys.argv[2])
original_start=terminal.Session.start
original_cleanup=terminal.Session.cleanup_children
def start(self):
    original_start(self)
    raise terminal.TerminalError('FIXTURE_STARTUP_FAILURE','fixture startup failure')
def cleanup(self):
    return original_cleanup(self) if (root/'allow-cleanup').exists() else False
terminal.Session.start=start
terminal.Session.cleanup_children=cleanup
args=argparse.Namespace(mode='fixture',fixture=root,session_token='b'*64,rows=21,cols=48)
raise SystemExit(terminal.serve(terminal.Context(args),args))
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', code, str(ROOT), str(self.root)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=self.env, close_fds=True)
        self.extra_processes.append(process)
        self.wait_for(lambda: self.context.record() and self.context.record()['state'] == 'failed')
        self.session = 'b' * 64
        self.assertTrue(self.lease_busy())
        result = self.call('status')
        self.assertEqual(result['state'], 'failed')
        self.assertTrue(result['storage_lease_held'])
        result = self.call('stop', '--expected-session-token', self.session, ok=False)
        self.assertEqual(result['error']['code'], 'DESCENDANTS_REMAIN')
        self.assertIsNone(process.poll())
        self.assertTrue(self.lease_busy())
        (self.root / 'allow-cleanup').touch(mode=0o600)
        self.wait_for(lambda: process.poll() is not None, timeout=8)
        self.assertFalse(self.lease_busy())
        self.assertEqual(self.call('status')['state'], 'stopped')


if __name__ == '__main__':
    unittest.main(verbosity=2)
