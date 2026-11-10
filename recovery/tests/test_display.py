from __future__ import annotations

import contextlib
import errno
import fcntl
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import display

BOOT = '11111111-2222-3333-4444-555555555555'


def mode(hz, width=1080, height=2408):
    clock = int(hz * 1100 * 2500 / 1000)
    return display.Mode(struct.pack('<I10HIII32s', clock, width, 1084, 1092, 1100, 0,
                                    height, 2420, 2430, 2500, 0, hz, 0, 8, b'1080x2408'))


class FakeDRM:
    def __init__(self):
        self.modes = [mode(60), mode(90)]
        self.state = {'id': 142, 'fb_id': 143, 'x': 0, 'y': 0, 'mode': self.modes[0]}
        self.calls, self.effects, self.close_calls = [], [], 0
        self.encoder_crtc = 142

    def driver_name(self):
        return 'mediatek'

    def resources(self):
        return {'connectors': [136], 'encoders': [135], 'crtcs': [142], 'fbs': [143]}

    def connector(self, ident):
        return {'id': ident, 'encoder_id': 135, 'type': 16, 'connected': True, 'modes': self.modes}

    def encoder(self, ident):
        return {'id': ident, 'crtc_id': self.encoder_crtc}

    def crtc(self, ident):
        return dict(self.state)

    def set_crtc(self, before, connector_id, target):
        self.calls.append((dict(before), connector_id, target))
        if self.effects:
            self.effects.pop(0)(self, before, target)
        else:
            self.state = {**before, 'mode': target}

    def close(self):
        self.close_calls += 1


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.drm = FakeDRM()
        self.controller = display.Controller(self.drm)

    def error(self, code, function, *args):
        with self.assertRaises(display.DisplayError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_dynamic_ids_and_persistent_60_90_60_same_framebuffer(self):
        self.assertEqual(self.controller.status()['current_hz'], 60)
        self.assertEqual(self.controller.set(90)['current_hz'], 90)
        self.assertEqual(self.controller.status()['current_hz'], 90)
        self.assertEqual(self.controller.set(60)['current_hz'], 60)
        self.assertEqual([call[1] for call in self.drm.calls], [136, 136])
        self.assertEqual(self.drm.state['fb_id'], 143)
        self.assertEqual(self.drm.close_calls, 0)

    def test_already_selected_mode_does_not_issue_a_modeset(self):
        self.assertFalse(self.controller.set(60)['changed'])
        self.assertEqual(self.drm.calls, [])

    def test_unsupported_or_boolean_refresh_is_rejected_before_write(self):
        for value in (120, 30, True, '90', None):
            self.error('UNSUPPORTED_REFRESH', self.controller.set, value)
        self.assertEqual(self.drm.calls, [])

    def test_requires_advertised_modes_and_does_not_fabricate_timings(self):
        self.drm.modes = [mode(60)]
        self.error('REFRESH_MODES_UNAVAILABLE', self.controller.set, 90)
        self.assertEqual(self.drm.calls, [])

    def test_wrong_driver_and_changed_pipeline_are_rejected(self):
        with mock.patch.object(self.drm, 'driver_name', return_value='simpledrm'):
            self.error('WRONG_DRM_DRIVER', display.Controller, self.drm)
        self.drm.encoder_crtc = 999
        self.error('DISPLAY_PIPELINE_CHANGED', self.controller.set, 90)
        self.assertEqual(self.drm.calls, [])

    def test_postquery_framebuffer_mismatch_rolls_back_original_state(self):
        def wrong_fb(drm, before, target):
            drm.state = {**before, 'mode': target, 'fb_id': 999}
        self.drm.effects = [wrong_fb]
        error = self.error('MODESET_FAILED', self.controller.set, 90)
        self.assertTrue(error.details['rollback_succeeded'])
        self.assertEqual(self.drm.state['fb_id'], 143)
        self.assertEqual(self.controller.status()['current_hz'], 60)
        self.assertEqual(len(self.drm.calls), 2)

    def test_postquery_wrong_timings_roll_back_and_never_report_success(self):
        def unchanged(drm, before, target):
            pass
        self.drm.effects = [unchanged]
        error = self.error('MODESET_FAILED', self.controller.set, 90)
        self.assertTrue(error.details['rollback_succeeded'])
        self.assertEqual(self.controller.status()['current_hz'], 60)

    def test_ioctl_failure_still_attempts_verified_rollback(self):
        def fail(*_):
            raise OSError(errno.EIO, 'injected modeset failure')
        self.drm.effects = [fail]
        error = self.error('MODESET_FAILED', self.controller.set, 90)
        self.assertTrue(error.details['rollback_succeeded'])
        self.assertEqual(len(self.drm.calls), 2)

    def test_failed_rollback_reports_live_state_and_disables_further_sets(self):
        def wrong_fb(drm, before, target):
            drm.state = {**before, 'mode': target, 'fb_id': 999}
        def fail(*_):
            raise OSError(errno.EIO, 'rollback failure')
        self.drm.effects = [wrong_fb, fail]
        error = self.error('MODESET_ROLLBACK_FAILED', self.controller.set, 90)
        self.assertFalse(error.details['rollback_succeeded'])
        self.assertFalse(error.details['observed']['supported'])
        self.assertEqual(error.details['observed']['current_hz'], 90)
        self.error('DISPLAY_FAULT', self.controller.set, 60)
        self.assertEqual(len(self.drm.calls), 2)

    def test_low_level_setcrtc_layout_and_setmaster_retry(self):
        drm = object.__new__(display.DRM)
        drm.fd = 27
        seen = []
        def ioctl(number, data):
            seen.append((number, bytes(data)))
            if len(seen) == 1:
                raise OSError(errno.EACCES, 'need DRM master')
            return data
        drm.ioctl = ioctl
        with mock.patch.object(display.fcntl, 'ioctl') as master:
            drm.set_crtc(self.drm.state, 136, self.drm.modes[1])
        master.assert_called_once_with(27, 0x641e, 0)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][0], 0xa2)
        self.assertEqual(len(seen[0][1]), 104)
        self.assertEqual(struct.unpack_from('<7I', seen[0][1], 8), (1, 142, 143, 0, 0, 0, 1))
        self.assertEqual(seen[0][1][36:], self.drm.modes[1].raw)


class AdmissionAndProtocolTests(unittest.TestCase):
    def admission(self, rootfs='rootfs', marker_boot=BOOT, cmdline=b'a22x.recovery=1'):
        values = {'/sys/firmware/devicetree/base/compatible': b'samsung,a22x\0mediatek,mt6833\0',
                  '/proc/cmdline': cmdline,
                  '/proc/self/mountinfo': ('1 0 0:1 / / rw - ' + rootfs + ' rootfs rw\n').encode(),
                  '/proc/sys/kernel/random/boot_id': BOOT.encode()}
        with mock.patch.object(display.os, 'geteuid', return_value=0), \
             mock.patch.object(display, 'bounded_read', side_effect=lambda path, *args: values[str(path)]), \
             mock.patch.object(display, 'regular_json', return_value={'schema': 'a22x.recovery.boot.v1', 'boot_id': marker_boot}):
            return display.admission()

    def test_dedicated_ram_root_and_current_boot_receipt_are_required(self):
        self.assertEqual(self.admission(), BOOT)
        for kwargs, code in (({'rootfs': 'ext4'}, 'NOT_RAM_RECOVERY'),
                             ({'marker_boot': 'previous-boot'}, 'BOOT_MISMATCH'),
                             ({'cmdline': b'normal-linux'}, 'NOT_RECOVERY')):
            with self.subTest(code=code), self.assertRaises(display.DisplayError) as caught:
                self.admission(**kwargs)
            self.assertEqual(caught.exception.code, code)

    def test_non_root_admission_stops_before_reading_devices(self):
        with mock.patch.object(display.os, 'geteuid', return_value=1000), \
             mock.patch.object(display, 'bounded_read') as read, self.assertRaises(display.DisplayError):
            display.admission()
        read.assert_not_called()

    def test_status_without_service_is_honest_and_never_opens_drm(self):
        output = io.StringIO()
        with mock.patch.object(display, 'admission', return_value=BOOT), \
             mock.patch.object(display, 'client', side_effect=ConnectionRefusedError('service stopped')), \
             mock.patch.object(display, 'discover') as discover, contextlib.redirect_stdout(output):
            self.assertEqual(display.main(['status']), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['ok'])
        self.assertFalse(result['supported'])
        self.assertFalse(result['service_running'])
        self.assertIsNone(result['current_hz'])
        discover.assert_not_called()

    def test_stale_boot_request_and_invalid_refresh_never_change_mode(self):
        drm = FakeDRM()
        controller = display.Controller(drm)
        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': 'stale', 'operation': 'set', 'hz': 90}
        self.assertEqual(display.dispatch(request, BOOT, controller)['error']['code'], 'BOOT_MISMATCH')
        request.update(boot_id=BOOT, hz=120)
        self.assertEqual(display.dispatch(request, BOOT, controller)['error']['code'], 'UNSUPPORTED_REFRESH')
        self.assertEqual(drm.calls, [])

    def test_message_bounds_duplicate_keys_and_extra_messages(self):
        for raw, code in ((b'x' * (display.MAX_REQUEST + 1), 'MESSAGE_TOO_LARGE'),
                          (b'{"x":1,"x":2}\n', 'INVALID_REQUEST'),
                          (b'{}\n{}\n', 'INVALID_REQUEST')):
            connection = mock.Mock()
            chunks = [raw[i:i + 512] for i in range(0, len(raw), 512)]
            connection.recv.side_effect = chunks
            with self.subTest(code=code), self.assertRaises(display.DisplayError) as caught:
                display.receive_line(connection, display.MAX_REQUEST)
            self.assertEqual(caught.exception.code, code)

    def test_peer_credentials_timeout_and_separate_requests_keep_the_controller(self):
        drm = FakeDRM()
        controller = display.Controller(drm)

        def connection(request=None, uid=0, timeout=False):
            peer = mock.Mock()
            peer.getsockopt.return_value = struct.pack('3i', 123, uid, 0)
            if timeout:
                peer.recv.side_effect = TimeoutError('deadline reached')
            else:
                peer.recv.return_value = json.dumps(request or {}).encode() + b'\n'
            display.handle_connection(peer, BOOT, controller)
            peer.settimeout.assert_called_once_with(display.IPC_TIMEOUT)
            return json.loads(peer.sendall.call_args.args[0])

        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': 'set', 'hz': 90}
        self.assertEqual(connection(request, uid=1000)['error']['code'], 'ROOT_REQUIRED')
        self.assertFalse(connection(timeout=True)['ok'])
        self.assertEqual(drm.calls, [])
        self.assertEqual(connection(request)['current_hz'], 90)
        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': 'status'}
        self.assertEqual(connection(request)['current_hz'], 90)
        self.assertEqual(connection(request)['current_hz'], 90)
        self.assertEqual(drm.close_calls, 0)


def daemon_worker(directory, send):
    display.RUN = Path(directory)
    display.SOCKET = display.RUN / 'display.sock'
    display.RECORD = display.RUN / 'display-service.json'
    display.LOCK = display.RUN / 'display.lock'
    drm = FakeDRM()
    display.discover = lambda: display.Controller(drm)
    display.discover_backlight = lambda boot_id: None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            display.serve(BOOT)
    finally:
        send.send(drm.close_calls)
        send.close()


@unittest.skipUnless(os.geteuid() == 0, 'Root-only UNIX socket credentials are part of the production contract')
class PersistentServiceTests(unittest.TestCase):
    def test_real_socket_persistent_mode_singleton_stale_boot_and_clean_shutdown(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
                pass
        except OSError as error:
            if error.errno in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT):
                self.skipTest('Executor prohibits AF_UNIX sockets; live recovery must run the IPC acceptance')
            raise
        with tempfile.TemporaryDirectory() as directory:
            paths = {'RUN': Path(directory), 'SOCKET': Path(directory) / 'display.sock',
                     'RECORD': Path(directory) / 'display-service.json', 'LOCK': Path(directory) / 'display.lock'}
            receive, send = multiprocessing.Pipe(duplex=False)
            process = multiprocessing.Process(target=daemon_worker, args=(directory, send))
            process.start()
            send.close()
            try:
                deadline = time.monotonic() + 5
                while not paths['RECORD'].exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(paths['RECORD'].exists())
                with mock.patch.multiple(display, **paths):
                    self.assertEqual(display.client('status', None, BOOT)['current_hz'], 60)
                    self.assertEqual(display.client('set', 90, BOOT)['current_hz'], 90)
                    self.assertEqual(display.client('status', None, BOOT)['current_hz'], 90)
                    self.assertEqual(display.client('set', 60, BOOT)['current_hz'], 60)
                    with self.assertRaises(display.DisplayError) as caught:
                        display.client('status', None, 'stale-boot')
                    self.assertEqual(caught.exception.code, 'SERVICE_STALE')
                    with paths['LOCK'].open('rb') as contender, self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bad:
                        bad.settimeout(3)
                        bad.connect(str(paths['SOCKET']))
                        bad.sendall(b'x' * (display.MAX_REQUEST + 1) + b'\n')
                        error = display.receive_line(bad, display.MAX_RESPONSE)
                        self.assertEqual(error['error']['code'], 'MESSAGE_TOO_LARGE')
                    self.assertEqual(display.client('status', None, BOOT)['current_hz'], 60)
            finally:
                if process.is_alive():
                    os.kill(process.pid, signal.SIGTERM)
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(receive.poll(1))
            self.assertEqual(receive.recv(), 1)
            self.assertFalse(paths['SOCKET'].exists())
            self.assertFalse(paths['RECORD'].exists())
            receive.close()


class FileBacklight:
    """Owned regular files model the kernel's bounded brightness attributes."""
    def __init__(self, directory, create=True):
        self.root = Path(directory)
        self.identity, self.maximum, self.writes = str(self.root), 255, []
        self.panel_off = False  # As the device backlight: the panel is on until the display service turns it off.
        if create:
            for name, value in (('brightness', 200), ('actual_brightness', 200), ('max_brightness', 255), ('bl_power', 0)):
                (self.root / name).write_text(str(value) + '\n')
        self.fds = {name: os.open(self.root / name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                    for name in ('brightness', 'actual_brightness', 'max_brightness', 'bl_power')}
        self.identities = {name: (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for name, fd in self.fds.items()}

    def read(self):
        for name, fd in self.fds.items():
            st = (self.root / name).lstat()
            display.require((st.st_dev, st.st_ino) == self.identities[name], 'BACKLIGHT_CHANGED', 'Fixture path changed')
        return tuple(int(os.pread(self.fds[name], 32, 0)) for name in ('brightness', 'actual_brightness'))

    def write(self, raw):
        self.read()
        self.writes.append(raw)
        for name in ('brightness', 'actual_brightness'):
            data = (str(raw) + '\n').encode()
            os.pwrite(self.fds[name], data, 0)
            os.ftruncate(self.fds[name], len(data))

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


class FakeGuard:
    def __init__(self, io, last):
        self.pid, self.process_start, self.last, self.live = None, None, last, True
        self.updates = []

    def alive(self):
        return self.live

    def update(self, raw):
        display.require(self.live, 'BACKLIGHT_GUARD_FAILED', 'Fixture guard stopped')
        self.last = raw
        self.updates.append(raw)

    def close(self):
        self.live = False


@unittest.skipUnless(os.geteuid() == 0, 'RAM service records are root-owned in the production contract')
class BacklightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.io = FileBacklight(self.root)
        self.record = self.root / 'state.json'
        self.light = display.Backlight(self.io, BOOT, self.record, FakeGuard)

    def tearDown(self):
        self.light.close()
        self.tmp.cleanup()

    def test_real_file_off_restore_and_percent_bounds_without_any_storage_lock(self):
        with mock.patch.object(display.fcntl, 'flock', side_effect=AssertionError('No storage lock is permitted')):
            self.assertEqual(self.light.brightness(40)['brightness_raw'], 102)
            self.assertEqual(self.light.blank()['brightness_percent'], 0)
            self.assertEqual(self.io.read(), (0, 0))
            self.assertEqual(json.loads(self.record.read_text())['last_nonzero_raw'], 102)
            self.assertEqual(self.light.wake()['brightness_raw'], 102)
            self.assertFalse(self.light.wake()['changed'])
            self.assertTrue(self.light.toggle()['blanked'])
            self.assertFalse(self.light.toggle()['blanked'])
            self.assertEqual(self.light.brightness(1)['brightness_raw'], 3)
            self.assertEqual(self.light.brightness(100)['brightness_raw'], 255)
        self.assertEqual(set(x.name for x in self.root.iterdir()),
                         {'brightness', 'actual_brightness', 'max_brightness', 'bl_power', 'state.json'})

    def test_invalid_values_and_extra_protocol_arguments_cannot_write(self):
        before = list(self.io.writes)
        for value in (0, -1, 101, True, False, 1.5, '40', None):
            with self.subTest(value=value), self.assertRaises(display.DisplayError):
                self.light.brightness(value)
        for op, fields in (('blank', {'percent': 50}), ('status', {'hz': 90}), ('brightness', {'percent': 50, 'hz': 90})):
            response = display.dispatch({'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': op, **fields},
                                        BOOT, display.Controller(FakeDRM()), backlight=self.light)
            self.assertEqual(response['error']['code'], 'INVALID_REQUEST')
        self.assertEqual(self.io.writes, before)

    def test_brightness_wakes_but_blank_does_not_change_mode_or_last_positive(self):
        drm = FakeDRM()
        controller = display.Controller(drm)
        controller.set(90)
        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': 'blank'}
        response = display.dispatch(request, BOOT, controller, backlight=self.light)
        self.assertEqual(response['current_hz'], 90)
        self.assertTrue(response['blanked'])
        self.assertEqual(len(drm.calls), 1)
        request.update(operation='brightness', percent=80)
        response = display.dispatch(request, BOOT, controller, backlight=self.light)
        self.assertFalse(response['blanked'])
        self.assertEqual(response['brightness_percent'], 80)
        self.assertEqual(response['current_hz'], 90)
        self.assertEqual(len(drm.calls), 1)

    def test_guard_loss_wakes_and_refuses_new_blank(self):
        self.light.blank()
        self.light.guard.live = False
        self.light.tick()
        self.assertEqual(self.io.read(), (200, 200))
        self.assertFalse(self.light.status()['blank_supported'])
        with self.assertRaises(display.DisplayError):
            self.light.blank()
        self.assertEqual(self.io.read(), (200, 200))

    def test_shutdown_and_restart_restore_ram_last_nonzero(self):
        self.light.brightness(37)
        self.light.preferred_hz = 90
        self.light.blank()
        raw = self.light.last
        self.light.close()
        self.assertEqual(int((self.root / 'brightness').read_text()), raw)
        self.io = FileBacklight(self.root, create=False)
        self.io.write(0)
        self.light = display.Backlight(self.io, BOOT, self.record, FakeGuard)
        self.assertEqual(self.io.read(), (raw, raw))
        self.assertEqual(self.light.preferred_hz, 90)

    def test_readback_failure_restores_visible_state_and_reports_failure(self):
        real_write = self.io.write
        def wrong(raw):
            real_write(raw)
            if raw == 0:
                fd = self.io.fds['actual_brightness']
                os.pwrite(fd, b'1\n', 0)
                os.ftruncate(fd, 2)
        with mock.patch.object(self.io, 'write', side_effect=wrong), self.assertRaises(display.DisplayError) as caught:
            self.light.blank()
        self.assertEqual(caught.exception.code, 'BACKLIGHT_CHANGE_FAILED')
        self.assertTrue(caught.exception.details['visible_restore_confirmed'])
        self.assertEqual(self.io.read(), (200, 200))

    def test_replaced_attribute_refuses_before_mutation(self):
        replacement = self.root / 'replacement'
        replacement.write_text('200\n')
        os.replace(replacement, self.root / 'brightness')
        self.assertFalse(self.light.status()['brightness_supported'])
        with self.assertRaises(display.DisplayError):
            self.light.blank()
        self.assertEqual((self.root / 'brightness').read_text(), '200\n')
        # Restore the exact pinned inode for ordinary fixture teardown.
        self.light.close = self.io.close

    def test_non_sysfs_and_symlink_escape_are_not_admitted(self):
        for path in (self.root, self.root / 'alias'):
            if path.name == 'alias':
                path.symlink_to(self.root, target_is_directory=True)
            with self.subTest(path=path), self.assertRaises(display.DisplayError) as caught:
                display.BacklightIO(path)
            self.assertEqual(caught.exception.code, 'BACKLIGHT_PATH_INVALID')
        self.assertEqual(self.io.writes, [])

    def test_refresh_preference_saved_without_backlight_mutation(self):
        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': 'set', 'hz': 90}
        response = display.dispatch(request, BOOT, display.Controller(FakeDRM()), backlight=self.light)
        self.assertTrue(response['ok'])
        self.assertEqual(json.loads(self.record.read_text())['preferred_hz'], 90)
        self.assertEqual(self.io.writes, [])

    def test_exact_upgrade_seed_preserves_visible_raw_and_accepts_null_guard(self):
        self.light.close()
        self.record.write_text(json.dumps({'schema': display.BACKLIGHT_SCHEMA, 'boot_id': BOOT,
            'device': str(self.root), 'maximum': 255, 'last_nonzero_raw': 140, 'preferred_hz': 90,
            'guard_pid': None, 'guard_start': None}))
        self.io = FileBacklight(self.root, create=False)
        self.light = display.Backlight(self.io, BOOT, self.record, FakeGuard)
        self.assertEqual(self.io.read(), (200, 200))
        self.assertEqual(self.light.last, 200)
        self.assertEqual(self.light.preferred_hz, 90)
        self.assertEqual(self.io.writes, [])

    def test_cli_zero_is_rejected_without_connecting(self):
        with mock.patch.object(display, 'admission', return_value=BOOT), \
             mock.patch.object(display, 'client') as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(display.main(['brightness', '--percent', '0']), 2)
        client.assert_not_called()

    def test_backlight_control_remains_available_without_refresh_controller(self):
        request = {'schema': display.REQUEST_SCHEMA, 'boot_id': BOOT, 'operation': 'brightness', 'percent': 25}
        response = display.dispatch(request, BOOT, None, reason='No refresh modes', backlight=self.light)
        self.assertTrue(response['ok'])
        self.assertFalse(response['supported'])
        self.assertTrue(response['brightness_supported'])
        self.assertEqual(response['brightness_percent'], 25)


def crash_guard_parent(directory, sender):
    io = FileBacklight(directory, create=False)
    lease = os.open(Path(directory) / 'unrelated-storage-lock', os.O_CREAT | os.O_RDWR, 0o600)
    drm = os.open(Path(directory) / 'unrelated-drm', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    guard = display.BacklightGuard(io, 200)
    guard.update(180)
    io.write(0)
    sender.send(guard.pid)
    sender.close()
    while True:
        signal.pause()


class RealCrashGuardTests(unittest.TestCase):
    def setUp(self):
        try:
            first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            first.close(); second.close()
        except OSError as error:
            if error.errno in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT):
                self.skipTest('Executor prohibits private AF_UNIX sockets; run this owned-file case on Arch')
            raise
        reader, writer = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(writer)
            os.read(reader, 1)
            os._exit(0)
        os.close(reader)
        try:
            display.process_start(child)
            os.readlink('/proc/' + str(child) + '/fd/' + str(reader))
        except (FileNotFoundError, PermissionError):
            self.skipTest('Executor hides metadata of a deliberately live child; run pidfd/FD assertions on Arch')
        finally:
            os.close(writer)
            os.waitpid(child, 0)

    def test_sigkill_service_restores_last_raw_and_guard_has_no_inherited_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            io = FileBacklight(directory)
            receiver, sender = multiprocessing.Pipe(duplex=False)
            process = multiprocessing.Process(target=crash_guard_parent, args=(directory, sender))
            process.start(); sender.close()
            pidfd = None
            try:
                self.assertTrue(receiver.poll(5))
                guard_pid = receiver.recv()
                pidfd = os.pidfd_open(guard_pid, 0)
                targets = [os.readlink(p) for p in Path('/proc', str(guard_pid), 'fd').iterdir()]
                self.assertFalse(any('unrelated-' in target for target in targets))
                self.assertFalse(any(target in ('/dev/null', '/dev/urandom') for target in targets))
                self.assertEqual(io.read(), (0, 0))
                os.kill(process.pid, signal.SIGKILL)
                process.join(5)
                self.assertEqual(process.exitcode, -signal.SIGKILL)
                self.assertTrue(display.select.select([pidfd], [], [], 3)[0])
                self.assertEqual(io.read(), (180, 180))
                with (Path(directory) / 'unrelated-storage-lock').open('rb') as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                if process.is_alive():
                    process.kill(); process.join(5)
                if pidfd is not None:
                    os.close(pidfd)
                receiver.close(); io.close()

    def test_graceful_channel_close_restores_zero_and_reaps_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            io = FileBacklight(directory)
            guard = display.BacklightGuard(io, 200)
            try:
                io.write(0)
                guard.close()
                self.assertEqual(io.read(), (200, 200))
                self.assertIsNone(guard.pid)
            finally:
                guard.close(); io.close()

    def test_invalid_private_packet_also_restores_before_guard_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            io = FileBacklight(directory)
            guard = display.BacklightGuard(io, 200)
            try:
                io.write(0)
                guard.channel.send(b'not-an-update')
                guard.close()
                self.assertEqual(io.read(), (200, 200))
                self.assertIsNone(guard.pid)
            finally:
                guard.close(); io.close()


class PanelPower(unittest.TestCase):
    """The panel comes on before the light and goes off after it."""
    class Light:
        def __init__(self, order, dark=False):
            self.order, self.dark, self.io = order, dark, type('IO', (), {'panel_off': False})()

        def status(self):
            return {'blanked': self.dark}

        def blank(self):
            self.order.append('light off')
            self.dark = True
            return {}

        def wake(self):
            self.order.append('light on')
            self.dark = False
            return {}

        def brightness(self, percent):
            self.order.append(f'light {percent}')
            self.dark = percent == 0
            return {}

    class Panel:
        def __init__(self, order, broken=False):
            self.order, self.broken = order, broken

        def panel(self, on):
            if self.broken:
                raise OSError('no panel')
            self.order.append('panel on' if on else 'panel off')
            return True

    def run_light(self, operation, percent=None, dark=False, broken=False):
        order = []
        light = self.Light(order, dark)
        display.light(operation, percent, self.Panel(order, broken), light)
        return order, light

    def test_order(self):
        self.assertEqual(self.run_light('blank')[0], ['light off', 'panel off'])
        self.assertEqual(self.run_light('wake', dark=True)[0], ['panel on', 'light on'])
        self.assertEqual(self.run_light('toggle')[0], ['light off', 'panel off'])
        self.assertEqual(self.run_light('toggle', dark=True)[0], ['panel on', 'light on'])
        self.assertEqual(self.run_light('brightness', 40, dark=True)[0], ['panel on', 'light 40'])
        self.assertEqual(self.run_light('brightness', 40)[0], ['light 40'])
        self.assertEqual(self.run_light('brightness', 0)[0], ['light 0', 'panel off'])

    def test_the_service_remembers_turning_the_panel_off(self):
        self.assertTrue(self.run_light('blank')[1].io.panel_off)
        self.assertFalse(self.run_light('wake', dark=True)[1].io.panel_off)

    def test_a_panel_that_refuses_changes_nothing_else(self):
        order, light = self.run_light('blank', broken=True)
        self.assertEqual(order, ['light off'])
        self.assertFalse(light.io.panel_off)
        order, _ = self.run_light('wake', dark=True, broken=True)
        self.assertEqual(order, ['light on'])

    def test_power_state_is_only_excused_while_the_panel_is_off(self):
        io = display.BacklightIO.__new__(display.BacklightIO)
        values = {'brightness': 0, 'actual_brightness': 0, 'bl_power': 4}
        io.maximum, io.recheck, io.integer = 255, lambda: None, values.__getitem__
        with self.assertRaises(display.DisplayError):
            io.read()
        io.panel_off = True
        self.assertEqual(io.read(), (0, 0))
        values['bl_power'] = 1
        with self.assertRaises(display.DisplayError):
            io.read()


if __name__ == '__main__':
    unittest.main()
