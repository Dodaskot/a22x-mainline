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
CASES = ('roundtrip', 'tamper', 'resume', 'rollback', 'accounts', 'unmounted', 'readonly', 'no-profile')


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
            run = subprocess.run([binary, str(root), str(ROOT/'distro.py'), case, sys.executable], capture_output=True, text=True, timeout=90)
            (UI/f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI/f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            result = json.loads(run.stdout)
            assert result['ok']
            f.assert_home(unittest.TestCase())
            if case not in ('unmounted', 'readonly', 'no-profile'):
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
