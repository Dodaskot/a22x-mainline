#!/usr/bin/env python3
"""Native UI -> actual distro/filesystem helpers, owned directory fixtures only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parent
spec = importlib.util.spec_from_file_location('a22x_distro_fixture', ROOT/'tests/test_distro.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
distro = fixture_module.d
CASES = ('roundtrip', 'tamper', 'resume', 'rollback', 'accounts', 'unmounted', 'readonly', 'no-profile', 'android-plan', 'remove-refused')


def main():
    results = []
    binary = os.environ.get('A22X_SYSTEMS_UI_TEST_BINARY', str(UI/'systems-ui-test'))
    prefix = os.environ.get('A22X_SYSTEMS_TEST_PREFIX', 'systems-ui')
    assert prefix and '/' not in prefix and '..' not in prefix
    with tempfile.TemporaryDirectory(prefix='a22x-systems-ui-', dir=os.environ.get('A22X_UI_FIXTURE_BASE', '/dev/shm')) as temporary:
        for case in CASES:
            root = Path(temporary)/case
            root.mkdir()
            f = fixture_module.Fixture(root)
            # 2026-11-08: a download left ".man" after ".qcow2"; a checksum file sits beside images.
            with open(f.sd/'Fedora-Cloud.qcow2.man', 'wb') as out:
                out.truncate(32 << 20)
            (f.sd/'rootfs.tar.gz.sha256').write_text('0' * 64 + '  rootfs.tar.gz\n')
            if case == 'roundtrip':
                # 2026-11-10: an image saved on the phone; it must stay through both switches.
                (f.live/distro.STORE_DIR).mkdir()
                (f.live/distro.STORE_DIR/'rootfs.tar.gz').write_bytes((f.sd/'rootfs.tar.gz').read_bytes())
            if case in ('resume', 'rollback'):
                plan = f.imported()
                def stop(name, index):
                    if name == 'after-rename' and index == 0:
                        raise fixture_module.StopForTest()
                try:
                    distro.switch(f.context, plan, True, stop)
                except fixture_module.StopForTest:
                    pass
                else:
                    raise AssertionError('Owned interrupted transaction not created')
            elif case == 'accounts':
                passwd = f.new/'etc/passwd'
                passwd.write_text(passwd.read_text().replace('user:x:1000:1000:', 'user:x:1001:1000:'))
                f.repack()
            elif case in ('unmounted', 'readonly'):
                f.context_json['volumes'][0]['mounted' if case == 'unmounted' else 'read_only'] = case == 'readonly'
                (root/'filesystem-fixture.json').write_text(json.dumps(f.context_json))
            elif case == 'no-profile':
                (root/'distro-platform.json').unlink()
            elif case == 'android-plan':
                # 2026-11-10: a plan the Android app left in cache. The catalog has no server
                # addresses here, so planning never asks the network.
                catalog = json.loads((ROOT/'catalog.json').read_text())
                for entry in catalog['images']:
                    entry.pop('source', None)
                (root/'catalog.json').write_text(json.dumps(catalog))
                scripts = root/'cache/a22x-recovery/scripts'
                scripts.mkdir(parents=True)
                (scripts/'setup.sh').write_text('#!/bin/sh\necho from the app\n')
                (root/'cache/a22x-recovery/install-plan.json').write_text(json.dumps({
                    'schema': 'a22x.install-plan.v1', 'source': 'android-app test', 'created_utc': 1794306000,
                    'system': {'catalog': 'ubuntu-24.04'}, 'erase_userdata': False, 'desktop': 'phosh', 'shells': ['gnome'],
                    'sign_in': 'auto', 'apps': ['ssh', 'calculator'],
                    'account': {'username': 'olena', 'password_sha512': '$6$a22xsalt$' + 'D' * 86},
                    'locale': 'uk_UA', 'timezone': 'Europe/Kyiv', 'script': 'setup.sh'}))
                f.context_json['volumes'].append({'id': 'cache', 'path': 'cache', 'mounted': False, 'read_only': False})
                (root/'filesystem-fixture.json').write_text(json.dumps(f.context_json))
            run = subprocess.run([binary, str(root), str(ROOT/'distro.py'), case, sys.executable], capture_output=True, text=True, timeout=90)
            (UI/f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI/f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            result = json.loads(run.stdout)
            assert result['ok']
            f.assert_home(unittest.TestCase())
            if case == 'android-plan':
                assert not (root/'cache/a22x-recovery/install-plan.json').exists()  # discarded on the page
            if case not in ('unmounted', 'readonly', 'no-profile', 'android-plan'):
                status = distro.status(f.context)
                expected = 'new-linux' if case == 'resume' else 'original'
                assert status['active'] == expected and not status['transaction']
                assert (f.live/'var/lib/pkg/state').read_text() == ('new' if case == 'resume' else 'old')
            results.append({**result, 'shared_home_inode_content_preserved': True})
    report = {'ok': True, 'workflows': len(results), 'device_io': False,
              'source_sha256': hashlib.sha256((UI/'recovery_ui.c').read_bytes()).hexdigest(),
              'systems_ui_sha256': hashlib.sha256((UI/'systems_ui.h').read_bytes()).hexdigest(),
              'backend_sha256': hashlib.sha256((ROOT/'distro.py').read_bytes()).hexdigest(), 'cases': results}
    (UI/f'{prefix}-results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'ok': True, 'workflows': len(results), 'checks': sum(r['checks'] for r in results), 'device_io': False}))


if __name__ == '__main__':
    main()
