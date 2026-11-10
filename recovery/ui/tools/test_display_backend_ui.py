#!/usr/bin/env python3
"""Real UI → display.py client/service/guard with owned regular-file hardware.

Run on the host inside `unshare --user --map-root-user --mount -- ...` so the
production root UNIX-peer and RAM-record checks remain enabled. This runner
never admits sysfs, DRM, input, framebuffer, block devices, or a phone.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

BACKEND_SHA = '80f097ea802532c1dd459990380d26ba2803d5669142a910816d1a9e106c48d2'
BACKEND_TEST_SHA = '6b7e52c797d836fdf6b1851d07ac94c5e302cc1d78a7f7d8982981756c09f05d'
CASES = ('busy-backup', 'power-focus-hold', 'wake-contact', 'terminal-mtp',
         'input-timestamps', 'slider', 'slider-cancel', 'no-drm', 'unavailable', 'startup-blank')
WRAPPER = r'''
import hashlib,importlib.util,json,os,sys
from pathlib import Path
root=Path(sys.argv[2]);assert sys.argv[1]=='--fixture' and root.is_absolute()
config=json.loads((root/'fixture-config.json').read_text())
def load(path,expected,name):
 assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
 sys.modules[name]=module;spec.loader.exec_module(module);return module
d=load(config['backend'],config['backend_sha256'],'display')
t=load(config['backend_test'],config['backend_test_sha256'],'display_owned_fixture_tests')
d.RUN=root/'run';d.SOCKET=d.RUN/'d.sock';d.LOCK=d.RUN/'d.lock';d.RECORD=d.RUN/'service.json';d.BACKLIGHT_RECORD=d.RUN/'light.json'
d.admission=lambda:t.BOOT
def discover():
 if config['case']=='no-drm':raise d.DisplayError('FIXTURE_NO_DRM','Owned fixture has no refresh controller')
 c=d.Controller(t.FakeDRM());c.set(90);return c
def backlight(boot):
 if config['case']=='unavailable':raise d.DisplayError('FIXTURE_NO_BACKLIGHT','Owned fixture has no backlight')
 return d.Backlight(t.FileBacklight(root/'backlight',create=False),boot)
d.discover=discover;d.discover_backlight=backlight
if sys.argv[3]!='serve':
 sentinel=os.environ.get('A22X_DISPLAY_FIXTURE_SENTINEL_FD');inherited=False
 if sentinel is not None:
  try:os.fstat(int(sentinel));inherited=True
  except OSError:pass
 with (root/'actual-client-requests.jsonl').open('a') as f:
  f.write(json.dumps({'operation':sys.argv[3],'lease_env':'A22X_RECOVERY_LOCK_FD' in os.environ,'sentinel_fd_inherited':inherited})+'\n')
raise SystemExit(d.main(sys.argv[3:]))
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-test-binary', type=Path, required=True)
    parser.add_argument('--ui-source', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--backend-test', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Use a mapped-root user namespace; do not weaken production peer checks'
    assert sha(args.backend) == BACKEND_SHA and sha(args.backend_test) == BACKEND_TEST_SHA
    assert all(path.is_absolute() for path in (args.ui_test_binary, args.ui_source, args.backend, args.backend_test, args.output))
    args.output.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('A22X_RECOVERY_LOCK_FD', None)
    reports = []
    with tempfile.TemporaryDirectory(prefix='a22x-display-real-', dir='/tmp') as temporary:
        base = Path(temporary)
        wrapper = base/'helper.py'
        wrapper.write_text(WRAPPER)
        for number, case in enumerate(CASES):
            root = base/str(number)
            root.mkdir(mode=0o700)
            (root/'backlight').mkdir(mode=0o700)
            (root/'run').mkdir(mode=0o700)
            for name, value in (('brightness', 163), ('actual_brightness', 163), ('max_brightness', 255), ('bl_power', 0)):
                (root/'backlight'/name).write_text(str(value)+'\n')
            (root/'fixture-config.json').write_text(json.dumps({'case': case, 'backend': str(args.backend),
                'backend_sha256': BACKEND_SHA, 'backend_test': str(args.backend_test), 'backend_test_sha256': BACKEND_TEST_SHA}))
            command = [sys.executable, '-B', str(wrapper), '--fixture', str(root)]
            out_path, err_path = args.output/(case+'-service.stdout.log'), args.output/(case+'-service.stderr.log')
            with out_path.open('xb') as out, err_path.open('xb') as err:
                service = subprocess.Popen(command+['serve'], stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                           env=env, close_fds=True)
                try:
                    deadline = time.monotonic()+8
                    while not out_path.stat().st_size and time.monotonic() < deadline and service.poll() is None:
                        time.sleep(0.02)
                    assert service.poll() is None and out_path.stat().st_size, (case, err_path.read_text())
                    started = json.loads(out_path.read_text())
                    assert started['ok'] and started['operation'] == 'serve'
                    if case == 'startup-blank':
                        before = subprocess.run(command+['blank'], env=env, capture_output=True, text=True, timeout=5)
                        assert before.returncode == 0 and json.loads(before.stdout)['blanked'] is True
                    run = subprocess.run([str(args.ui_test_binary), str(root), str(wrapper), case, sys.executable],
                                         env=env, capture_output=True, text=True, timeout=25)
                    (args.output/(case+'-ui.stdout.log')).write_text(run.stdout)
                    (args.output/(case+'-ui.stderr.log')).write_text(run.stderr)
                    assert run.returncode == 0, (case, run.returncode, run.stdout, run.stderr)
                    value = json.loads(run.stdout)
                    requests = [json.loads(line) for line in (root/'actual-client-requests.jsonl').read_text().splitlines()]
                    assert all(not r['lease_env'] and not r['sentinel_fd_inherited'] for r in requests), requests
                    raw = int((root/'backlight/actual_brightness').read_text())
                    report = {**value, 'real_client_dispatch_service': True, 'real_private_crash_guard': case != 'unavailable',
                              'hardware': 'owned regular files and FakeDRM only', 'operations': [r['operation'] for r in requests],
                              'observed_raw_before_service_shutdown': raw, 'service_pid': service.pid}
                    reports.append(report)
                finally:
                    if service.poll() is None:
                        service.send_signal(signal.SIGTERM)
                        try:
                            service.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            service.kill(); service.wait(timeout=5)
                            raise AssertionError('Owned display fixture service did not exit within its deadline')
                assert service.returncode == 0, (case, service.returncode, err_path.read_text())
                assert int((root/'backlight/actual_brightness').read_text()) > 0
                assert not (root/'run/d.sock').exists() and not (root/'run/service.json').exists()
    result = {'schema': 'a22x.ui.display-controls.real-backend.v1', 'ok': True,
              'workflows': len(reports), 'checks': sum(r['checks'] for r in reports),
              'ui_source_sha256': sha(args.ui_source), 'ui_test_binary_sha256': sha(args.ui_test_binary),
              'display_source_sha256': BACKEND_SHA, 'backend_test_source_sha256': BACKEND_TEST_SHA,
              'real_root_peer_and_service_record_guards': True, 'storage_and_device_io': False,
              'cases': reports}
    target = args.output/'result.json'
    target.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'ok': True, 'workflows': len(reports), 'checks': result['checks'], 'result': str(target)}))


if __name__ == '__main__':
    main()
