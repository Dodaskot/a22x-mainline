#!/usr/bin/env python3
"""Actual UI event/transport tests using the explicit no-PTY contract double."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
CASES = ('roundtrip', 'busy', 'unavailable', 'bad-sequence', 'gap', 'bad-ack', 'stale-stop', 'stop-race', 'close-during-write', 'background', 'reopen-closing', 'start-pending', 'gap-inflight')


def main():
    binary = os.environ.get('A22X_TERMINAL_UI_TEST_BINARY', str(UI/'terminal-ui-test'))
    results = []
    prefix = os.environ.get('A22X_TERMINAL_TEST_PREFIX', 'terminal-ui')
    assert prefix and '/' not in prefix and '..' not in prefix
    with tempfile.TemporaryDirectory(prefix='a22x-terminal-ui-', dir=os.environ.get('A22X_UI_FIXTURE_BASE', '/dev/shm')) as temporary:
        for case in CASES:
            root = Path(temporary)/case
            root.mkdir()
            (root/'terminal-fixture.json').write_text(json.dumps({'case': case}))
            run = subprocess.run([binary, str(root), str(UI/'tools/terminal_fixture.py'), case, sys.executable],
                                 capture_output=True, text=True, timeout=40)
            (UI/f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI/f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            assert 'UI-TERMINAL-PRIVATE' not in run.stderr and 'untouched editor' not in run.stderr
            output = json.loads(run.stdout)
            assert output['ok']
            requests = [json.loads(line) for line in (root/'terminal-requests.jsonl').read_text().splitlines()]
            ops = [r['operation'] for r in requests]
            assert ops[:1] == ['status']
            if case == 'unavailable':
                assert ops == ['status']
            elif case == 'busy':
                assert ops == ['status', 'start']
            else:
                state = json.loads((root/'terminal-fixture-state.json').read_text())
                assert state['state'] == 'stopped' and state['output'] == ''
                # Those two cases open a second shell, and each shell is stopped exactly once.
                twice = case in ('background', 'reopen-closing')
                assert ops.count('start') == (2 if twice else 1)
                assert ops.count('stop') == (2 if twice else 1)
                assert ops[-1] == 'status'
                if case == 'stop-race':
                    assert ops[-3:] == ['stop', 'status', 'status']
                if case in ('bad-ack', 'close-during-write'):
                    assert ops.count('write') == 1, 'An uncertain or superseded input was replayed'
                if case == 'bad-sequence':
                    assert 'write' not in ops
            results.append({**output, 'operations': ops, 'privacy_log_check': True})
    report = {'ok': True, 'workflows': len(results), 'device_io': False, 'pty_io': False,
              'source_sha256': hashlib.sha256((UI/'recovery_ui.c').read_bytes()).hexdigest(),
              'terminal_ui_sha256': hashlib.sha256((UI/'terminal_ui.h').read_bytes()).hexdigest(),
              'model_sha256': hashlib.sha256((UI/'terminal_model.h').read_bytes()).hexdigest(), 'cases': results}
    (UI/f'{prefix}-results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'ok': True, 'workflows': len(results), 'checks': sum(r['checks'] for r in results),
                      'device_io': False, 'pty_io': False, 'report': str(UI/f'{prefix}-results.json')}))


if __name__ == '__main__':
    main()
