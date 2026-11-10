#!/usr/bin/env python3
"""Targeted native UI transport/gesture gates, never a firmware qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parent
sys.path.insert(0, str(ROOT))
CASES = ('boot', 'stock', 'blocked', 'wrong-source', 'wrong-backup', 'wrong-boot',
         'bad-order', 'bad-gate', 'extra-target', 'bad-hold', 'bad-token', 'bad-schema',
         'result-mode', 'tamper', 'uncertain', 'resume', 'pending-change', 'bad-receipt',
         'boot-noop', 'stock-noop', 'stock-data-gate', 'stock-protected-target',
         'stock-omitted-target', 'bad-journal', 'bad-journal-token', 'stock-unqualified',
         'boot-stock-unqualified', 'guard-missing')
spec = importlib.util.spec_from_file_location('install_ui_filesystem', ROOT / 'filesystem.py')
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)


def main():
    binary = os.environ.get('A22X_INSTALL_UI_TEST_BINARY', str(UI / 'install-ui-test'))
    prefix = os.environ.get('A22X_INSTALL_TEST_PREFIX', 'install-ui')
    assert prefix and '/' not in prefix and '..' not in prefix
    cases = os.environ.get('A22X_INSTALL_TEST_CASES', ','.join(CASES)).split(',')
    assert all(case in CASES for case in cases)
    results = []
    with tempfile.TemporaryDirectory(prefix='a22x-install-ui-', dir=os.environ.get('A22X_UI_FIXTURE_BASE', '/dev/shm')) as temporary:
        for case in cases:
            root = Path(temporary) / case
            sd, userdata, ro = (root / 'volumes' / name for name in ('sd', 'userdata', 'readonly'))
            for path in (sd, userdata, ro, root / 'repo'):
                path.mkdir(parents=True)
            config = {'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
                {'id': 'userdata', 'path': 'volumes/userdata', 'fs_type': 'ext4', 'mounted': True, 'read_only': False},
                {'id': 'sdcard-p1', 'path': 'volumes/sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': False},
                {'id': 'sdcard-p2', 'path': 'volumes/readonly', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}
            (root / 'filesystem-fixture.json').write_text(json.dumps(config))
            for name in ('boot.img', 'manifest.json', 'bad.txt'):
                (sd / name).write_bytes(b'Explicit UI transport fixture; not a valid firmware image.\n')
            before = {name: (sd / name).read_bytes() for name in ('boot.img', 'manifest.json', 'bad.txt')}
            inventory = fs.FixtureContext(root).inventory()
            volume = next(item for item in inventory['volumes'] if item['id'] == 'sdcard-p1')
            (root / 'install-fixture.json').write_text(json.dumps({'case': case, 'volume_token': volume['volume_token'],
                'repository': str(root / 'repo'), 'backup_path': str(root / 'repo/linux-v2')}))
            run = subprocess.run([binary, str(root), str(UI / 'tools/install_fixture.py'), str(ROOT / 'filesystem.py'), case, sys.executable], capture_output=True, text=True, timeout=90)
            (UI / f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI / f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            result = json.loads(run.stdout)
            calls = [json.loads(line) for line in (root / 'install-calls.jsonl').read_text().splitlines()]
            executions = [call for call in calls if call['operation'] == 'execute']
            expected = 2 if case == 'uncertain' else int(case in ('boot', 'stock', 'boot-noop', 'stock-noop', 'boot-stock-unqualified', 'tamper', 'resume', 'bad-receipt'))
            assert len(executions) == expected, (case, calls)
            if case == 'uncertain':
                assert executions[0]['plan_file'] != executions[1]['plan_file']
                assert [call['operation'] for call in calls][-5:] == ['status', 'status', 'resume-plan', 'execute', 'status']
            assert before == {name: (sd / name).read_bytes() for name in before}
            assert json.loads((root / 'filesystem-fixture.json').read_text()) == config
            results.append({**result, 'execute_calls': len(executions), 'source_bytes_and_mount_state_unchanged': True})
    report = {'ok': True, 'workflows': len(results), 'checks': sum(item['checks'] for item in results),
              'device_io': False, 'installer_contract_double': True, 'actual_filesystem_backend': True,
              'source_sha256': {name: hashlib.sha256((UI / name).read_bytes()).hexdigest()
                                for name in ('recovery_ui.c', 'install_ui.h', 'install_types.h', 'test_install_ui.c', 'tools/install_fixture.py')},
              'cases': results}
    (UI / f'{prefix}-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('ok', 'workflows', 'checks', 'device_io', 'installer_contract_double')}))


if __name__ == '__main__':
    main()
