#!/usr/bin/env python3
"""Actual UI input and independent display transport against owned file fixtures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
CASES = ('busy-backup', 'power-focus-hold', 'wake-contact', 'terminal-mtp',
         'input-timestamps', 'slider', 'slider-live', 'slider-cancel', 'no-drm', 'unavailable',
         'startup-blank', 'lost-reply', 'lost-status', 'timeout-reply',
         'brightness-not-applied', 'malformed', 'strict-readback',
         'restore-boot', 'restore-tap', 'restore-locked', 'restore-refused',
         'restore-set-error', 'restore-unavailable', 'stick-choice', 'rate-change', 'transitions',
         'password', 'lock-guard', 'lock-keeps-text', 'access', 'animation-styles', 'blur', 'pad', 'prompt-field', 'settings-retry', 'theme-files', 'phone-details',
         'vblank-stays', 'lock-dark', 'pad-dots', 'pad-round', 'power-menu', 'navigation', 'restore-image', 'account-page', 'clean-install', 'systems-mount', 'format-done', 'check-done', 'wifi', 'installer', 'packages', 'adb')


def flaky_settings(root, env):
    """Two saves meet a busy lease, the third one reaches the disk."""
    tries = root/'tries'
    script = root/'flaky.py'
    script.write_text(f'''import sys
from pathlib import Path
tries = Path({str(tries)!r})
count = len(tries.read_text()) if tries.exists() else 0
tries.write_text('x' * (count + 1))
sys.exit(3 if count < 2 else 0)
''')
    env.update(A22X_TEST_FLAKY_SETTINGS=str(script))


def theme_files(root, env):
    """Good and broken compiled themes side by side, and a stick with files to choose."""
    sys.path.insert(0, str(UI.parent))
    import filesystem as fs
    import themes
    bundled, imported, stick = root/'bundled', root/'ram', root/'stick'
    for folder in (bundled, imported, stick):
        folder.mkdir()
    good = themes.compile_theme((UI.parent/'themes/beaver.a22xtheme').read_bytes())[3]
    (bundled/'beaver.theme').write_bytes(good)
    (bundled/'short.theme').write_bytes(good[:100])
    (bundled/'cut.theme').write_bytes(good[:-1])
    (bundled/'long.theme').write_bytes(good + b'x')
    (bundled/'magic.theme').write_bytes(b'NOTTHEME' + good[8:])
    name = themes.HEADER.size - 128
    (bundled/'endless.theme').write_bytes(good[:name] + b'n' * 48 + good[name + 48:])
    (bundled/'ignored.txt').write_bytes(good)
    colors = {'bg': '#101214', 'panel': '#1c1f24', 'text': '#f5f5f5', 'muted': '#b0b6c0', 'accent': '#ffaa44', 'on_accent': '#201000'}
    plain = {'format': 1, 'code': 'sample', 'name': 'Sample', 'colors': colors}
    (imported/'sample.theme').write_bytes(themes.compile_theme(json.dumps(plain).encode())[3])
    palette = ['#101010', '#ee6666', '#66ee66', '#eeee66', '#6666ee', '#ee66ee', '#66eeee', '#dddddd'] * 2
    terminal = {'format': 1, 'code': 'green-tty', 'name': 'Green', 'terminal': {'background': '#000000', 'foreground': '#33ff33', 'palette': palette}}
    (imported/'green-tty.theme').write_bytes(themes.compile_theme(json.dumps(terminal).encode())[3])
    (stick/'fresh.a22xtheme').write_text(json.dumps({**plain, 'code': 'fresh', 'name': 'Fresh'}))
    (stick/'wrong.a22xtheme').write_text('not a theme')
    (root/'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
        {'id': 'usb-p1', 'path': 'stick', 'fs_type': 'exfat', 'mounted': True, 'read_only': True}]}))
    env.update(A22X_THEMES_BUNDLED=str(bundled), A22X_THEMES_RAM=str(imported), A22X_RECOVERY_THEMES_HELPER=str(UI.parent/'themes.py'),
               A22X_TEST_TOKEN=fs.FixtureContext(root).volume('usb-p1')['volume_token'])


def phone_details(root, env):
    sys.path.insert(0, str(UI.parent))
    from tests.test_sysinfo import phone
    phone(root/'phone', serial='R5PRIVATESERIAL')
    env.update(A22X_RECOVERY_SYSINFO_HELPER=str(UI.parent/'sysinfo.py'), A22X_SYSINFO_ROOT=str(root/'phone'))


def main():
    binary = os.environ.get('A22X_DISPLAY_UI_TEST_BINARY', str(UI/'display-ui-test'))
    prefix = os.environ.get('A22X_DISPLAY_UI_TEST_PREFIX', 'display-controls')
    assert prefix and '/' not in prefix and '..' not in prefix
    reports = []
    with tempfile.TemporaryDirectory(prefix='a22x-display-ui-', dir=os.environ.get('A22X_UI_FIXTURE_BASE', '/dev/shm')) as temporary:
        for case in CASES:
            root = Path(temporary)/case
            root.mkdir()
            state = dict(supported=True, available_hz=[60, 90], current_hz=90,
                         actual_hz=89.9999, service_running=True, reason=None,
                         brightness_raw=163, brightness_max=255, last_nonzero_raw=163)
            if case == 'no-drm':
                state.update(supported=False, available_hz=[], current_hz=None, actual_hz=None,
                             reason='Fixture DRM unavailable; independent backlight remains available.')
            if case == 'unavailable':
                state['fixture_light'] = False
            if case == 'startup-blank':
                state['brightness_raw'] = 0
            if case in ('lost-reply', 'lost-status', 'timeout-reply', 'brightness-not-applied'):
                state['control_behavior'] = case
            if case == 'malformed':
                state['control_behavior'] = 'malformed-status'
            if case.startswith('restore-'):
                state.update(current_hz=60, actual_hz=59.9998)
            if case == 'rate-change':
                state.update(current_hz=60, actual_hz=59.9998)
            if case == 'restore-refused':
                state['behavior'] = 'not-applied'
            if case == 'restore-set-error':
                state['behavior'] = 'set-error'
            if case == 'restore-unavailable':
                state['available_hz'] = [60]
            (root/'display-fixture.json').write_text(json.dumps(state))
            (root/'settings-ram.json').write_text('{}')
            env = dict(os.environ, A22X_TEST_SETTINGS=str(UI.parent/'settings.py'), A22X_SETTINGS_RAM=str(root/'settings-ram.json'))
            if case == 'settings-retry':
                flaky_settings(root, env)
            if case == 'theme-files':
                theme_files(root, env)
            if case == 'phone-details':
                phone_details(root, env)
            run = subprocess.run([binary, str(root), str(UI/'tools/display_fixture.py'), case, sys.executable],
                                 capture_output=True, text=True, timeout=60, env=env)
            (UI/f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI/f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            outcome = json.loads(run.stdout)
            assert outcome['ok'] and outcome['device_io'] is False
            requests = [json.loads(line) for line in (root/'display-requests.jsonl').read_text().splitlines()]
            assert all(not r['lease_env'] and not r['sentinel_fd_inherited'] for r in requests), requests
            ops = [r['operation'] for r in requests]
            assert ops[0] == 'status'
            if case in ('unavailable', 'malformed', 'strict-readback', 'stick-choice', 'transitions', 'password', 'lock-guard',
                        'lock-keeps-text', 'access', 'animation-styles', 'blur', 'pad', 'prompt-field', 'settings-retry', 'theme-files', 'phone-details',
                        'vblank-stays', 'lock-dark', 'pad-dots', 'pad-round', 'power-menu', 'navigation', 'restore-image', 'account-page', 'clean-install', 'systems-mount', 'format-done', 'check-done', 'wifi', 'installer', 'packages', 'adb'):
                expected = ['status']
            elif case in ('slider', 'no-drm'):
                expected = ['status', 'brightness', 'status', 'brightness', 'status']
                assert [r['percent'] for r in requests if r['operation'] == 'brightness'] == [100, 1]
            elif case == 'slider-live':
                expected = ['status', 'brightness', 'status', 'status', 'brightness', 'status']
            elif case == 'brightness-not-applied':
                expected = ['status', 'brightness', 'status']
            elif case in ('restore-boot', 'restore-tap', 'restore-locked', 'restore-refused'):
                expected = ['status', 'brightness', 'status', 'set', 'status']
            elif case == 'restore-set-error':
                expected = ['status', 'brightness', 'status', 'set']
            elif case == 'restore-unavailable':
                expected = ['status', 'brightness', 'status']
            elif case == 'rate-change':
                expected = ['status', 'set', 'status', 'set', 'status']
            elif case == 'startup-blank':
                expected = ['status', 'wake', 'status']
            elif case == 'input-timestamps':
                expected = ['status', 'toggle', 'status']
            else:
                expected = ['status', 'toggle', 'status', 'wake', 'status'] + (['blank', 'status', 'wake', 'status'] if case == 'lost-reply' else [])
            assert ops == expected, (case, ops, expected)
            final = json.loads((root/'display-fixture.json').read_text())
            selected = 90 if case in ('restore-boot', 'restore-tap', 'restore-locked') else (60 if case == 'rate-change' else state['current_hz'])
            assert final['current_hz'] == selected, 'Light controls changed the selected refresh mode'
            if case == 'timeout-reply':
                assert 'operation=toggle exit=-1 observed=0 timeout=1' in run.stderr
            assert 'R5PRIVATESERIAL' not in run.stdout + run.stderr, 'The serial number reached a log'
            if case == 'settings-retry':
                assert (root/'tries').read_text() == 'xxx', 'The save was not repeated until it worked'
            if case != 'phone-details':
                assert 'BACKEND_EXEC_FAILED' not in run.stderr
            reports.append({**outcome, 'operations': ops, 'requests': requests,
                            'storage_lease_and_inherited_fd_absent': True,
                            'refresh_rate_preserved': True})
    files = ['recovery_ui.c', 'test_display_ui.c', 'tools/display_fixture.py', 'tools/test_display_ui.py']
    report = {'schema': 'a22x.ui.display-controls.tests.v1', 'ok': True, 'workflows': len(reports),
              'checks': sum(r['checks'] for r in reports), 'device_io': False,
              'source_sha256': {name: hashlib.sha256((UI/name).read_bytes()).hexdigest() for name in files},
              'cases': reports}
    target = UI/f'{prefix}-results.json'
    target.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'ok': True, 'workflows': report['workflows'], 'checks': report['checks'],
                      'device_io': False, 'report': str(target)}))


if __name__ == '__main__':
    main()
