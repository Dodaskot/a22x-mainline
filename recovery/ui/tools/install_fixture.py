#!/usr/bin/env python3
"""Explicit UI transport double. No block I/O, mounts, or firmware writes.

The separate actual install.py integration suite is the writer acceptance.
This double supplies intentionally malformed receipts and lost acknowledgments
that a correct production backend does not normally emit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

BOOT = '12345678-1234-1234-1234-123456789abc'
TX = '1' * 32


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['fixture'])
    p.add_argument('--fixture', type=Path)
    p.add_argument('--fixture-root', type=Path)
    p.add_argument('operation', choices=['status', 'boot-plan', 'stock-plan', 'resume-plan', 'execute', 'list'])
    for key in ('source-volume', 'source-path', 'expected-source-token', 'backup-path', 'output',
                'journal-volume', 'journal-path', 'expected-journal-token', 'plan-file',
                'expected-plan-seal', 'expected-plan-file-sha256', 'confirmation', 'repository'):
        p.add_argument('--' + key)
    args = p.parse_args()
    root = args.fixture or args.fixture_root
    if root is None or not root.is_absolute() or root.is_symlink():
        raise SystemExit('An explicit owned fixture root is required')
    config = json.loads((root / 'install-fixture.json').read_text())
    state_path = root / 'install-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'status_count': 0, 'pending': config['case'] in ('resume', 'pending-change')}
    calls = root / 'install-calls.jsonl'
    with calls.open('a') as stream:
        stream.write(json.dumps(vars(args), default=str) + '\n')
    common = {'ok': True, 'mode': 'fixture', 'operation': args.operation, 'boot_id': BOOT}
    case = config['case']
    volume_token = config['volume_token']
    journal_relative = f'/.a22x-installs/{TX}/journal.json'
    checkpoint = str(root / 'volumes/sd' / journal_relative.lstrip('/').replace('journal.json', 'platform'))
    backup = config['backup_path']

    def save():
        state_path.write_text(json.dumps(state))

    def pending():
        return {'transaction_id': TX, 'operation': 'install-stock', 'journal_volume': 'sdcard-p1',
                'journal_path': journal_relative, 'journal_volume_token': volume_token,
                'state': 'writing', 'resumable': True}

    if args.operation == 'list':
        assert args.repository == config['repository']
        result = {**common, 'backups': [
            {'path': backup, 'name': 'linux-explicit-snapshot', 'profile': 'linux', 'schema': 'a22x.backup.v2', 'workflow': 'linux-partclone-v2'},
            {'path': str(root / 'repo/legacy'), 'name': 'legacy-must-not-select', 'profile': 'linux', 'schema': 'a22x.backup.v1'},
            {'path': str(root / 'repo/android'), 'name': 'android-must-not-select', 'profile': 'android', 'schema': 'a22x.backup.v2', 'workflow': 'linux-partclone-v2'}]}
    elif args.operation == 'status':
        state['status_count'] += 1
        unqualified = case in ('stock-unqualified', 'boot-stock-unqualified')
        entries = [pending()] if state['pending'] else []
        if case == 'pending-change' and state['status_count'] > 1:
            entries[0]['journal_volume_token'] = 'f' * 64
        result = {**common, 'schema': 'a22x.install.status.v1',
                  'capabilities': {'flash_boot': True, 'install_stock': True, 'resume': True,
                                   'android_mount': False, 'android_backup_restore': False, 'userdata_reset': True},
                  'required_hold_ms': {'flash-boot': 3000, 'install-stock': 5000}, 'backup_required': True,
                  'stock_profile': {'available': True, 'id': 'fixture-fixed-stock', 'firmware': 'FIXTURE-DZC3',
                                    'qualified': not unqualified,
                                    'blockers': ['STOCK_PROFILE_NOT_QUALIFIED'] if unqualified else []},
                  'blockers': ['INSTALL_GUARD_WITHOUT_AVAILABLE_JOURNAL'] if case == 'guard-missing' else [],
                  'pending': entries, 'auto_reboot': False}
    elif args.operation.endswith('-plan'):
        resumed = args.operation == 'resume-plan'
        stock = args.operation != 'boot-plan'
        source = '/manifest.json' if stock else '/boot.img'
        if resumed:
            assert state['pending']
            assert (args.journal_volume, args.journal_path, args.expected_journal_token) == ('sdcard-p1', journal_relative, volume_token)
        else:
            assert not state['pending']
            assert (args.source_volume, args.source_path, args.expected_source_token, args.backup_path) == ('sdcard-p1', source, volume_token, backup)
        now = int(time.time())
        labels = ['super', 'prism', 'optics', 'dtbo', 'vbmeta_system', 'vbmeta', 'boot'] if stock else ['boot']
        targets = [{'label': label, 'path': str(root / 'targets' / label), 'partition_number': 41 + n,
                    'start_sector': 1000 + n * 100, 'size_bytes': 1048576 + n * 512,
                    'action': 'write-raw', 'before_sha256': 'b' * 64, 'after_sha256': 'a' * 64}
                   for n, label in enumerate(labels)]
        skip = []
        if stock:
            identical = targets.pop(3)
            identical.update(action='skip-identical', before_sha256='a' * 64)
            skip.append(identical)
        if case in ('boot-noop', 'stock-noop'):
            for target in targets:
                target.update(action='skip-identical', before_sha256='a' * 64)
            skip.extend(targets)
            targets = []
        plan = {'schema': 'a22x.install.plan.v1', 'operation': 'install-stock' if stock else 'flash-boot',
                'mode': 'fixture', 'boot_id': BOOT, 'created_utc': now, 'expires_utc': now + 1800,
                'seal': 'c' * 64, 'confirmation': ('INSTALL-STOCK-' if stock else 'FLASH-BOOT-') + 'd' * 16,
                'required_hold_ms': 5000 if stock else 3000, 'can_execute': case != 'blocked',
                'blockers': ['INSUFFICIENT_CHECKPOINT_SPACE'] if case == 'blocked' else [],
                'source_volume': 'sdcard-p1', 'source_path': source, 'source_volume_token': volume_token,
                'backup_path': backup, 'backup_manifest_sha256': 'e' * 64,
                'backup_gate': {'complete': True, 'verified': True, 'same_device': True, 'current': not resumed,
                                'current_required': stock and not resumed, 'current_userdata': True,
                                'current_userdata_required': stock, 'profile': 'linux'},
                'boot_header': None if stock else {'layout_validated': True, 'runtime_boot_tested': False},
                'stock_profile': {'id': 'fixture-fixed-stock', 'firmware': 'FIXTURE-DZC3'} if stock else None,
                'write_targets': targets, 'skip_targets': skip, 'write_order': [item['label'] for item in targets],
                'checkpoint': {'path': checkpoint, 'labels': ['boot', 'recovery', 'dtbo', 'vbmeta', 'vbmeta_system'], 'required_bytes': 84017152},
                'transaction_id': TX, 'journal_volume': 'sdcard-p1', 'journal_path': journal_relative,
                'journal_volume_token': volume_token, 'next_action': 'first-android-boot' if stock else 'none',
                'custom_recovery_replaced': False,
                'userdata_written': stock, 'automatic_factory_reset': False,
                'auto_reboot': False, 'atomic': False, 'resuming': resumed, 'runtime_boot_tested': False}
        state['plan'] = plan
        output = Path(args.output)
        assert output.parent == root / 'privateplans' and not output.exists()
        raw = (json.dumps({'plan': plan}) + '\n').encode()
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            assert os.write(fd, raw) == len(raw)
        finally:
            os.close(fd)
        result = {**common, 'schema': 'a22x.install.plan-result.v1', 'plan_summary': True,
                  'plan_path': str(output), 'plan_file_sha256': digest(raw), 'plan': json.loads(json.dumps(plan))}
        # Deliberately invalid UI receipts, without changing the actual saved plan.
        bad = result['plan']
        if case == 'wrong-source': bad['source_path'] = '/unselected.img'
        elif case == 'wrong-backup': bad['backup_path'] = str(root / 'repo/unselected')
        elif case == 'wrong-boot': bad['boot_id'] = '00000000-0000-0000-0000-000000000000'
        elif case == 'bad-order': bad['write_order'].reverse()
        elif case == 'bad-gate': bad['backup_gate']['same_device'] = False
        elif case == 'stock-data-gate': bad['backup_gate']['current_userdata'] = False
        elif case == 'stock-protected-target': bad['write_targets'][0]['label'] = bad['write_order'][0] = 'userdata'
        elif case == 'stock-omitted-target': bad['skip_targets'] = []
        elif case == 'bad-journal': bad['journal_path'] = '/.a22x-installs/' + '2' * 32 + '/journal.json'
        elif case == 'bad-journal-token': bad['journal_volume_token'] = 'f' * 64
        elif case == 'extra-target': bad['write_targets'] *= 17
        elif case == 'bad-hold': bad['required_hold_ms'] = 0
        elif case == 'bad-token': bad['confirmation'] = 'CONFIRM-ANYTHING'
        elif case == 'bad-schema': result['schema'] = 'a22x.install.plan-result.v0'
        elif case == 'result-mode': result['mode'] = 'device'
    else:
        plan = state['plan']
        raw = Path(args.plan_file).read_bytes()
        if digest(raw) != args.expected_plan_file_sha256:
            result = {**common, 'ok': False, 'error': {'code': 'PLAN_FILE_CHANGED', 'message': 'The exact reviewed plan file changed.'}}
        else:
            assert args.expected_plan_seal == plan['seal'] and args.confirmation == plan['confirmation']
            assert plan['can_execute'] and not plan['blockers']
            if case == 'uncertain' and not state.get('lost_once'):
                state['pending'] = True
                state['lost_once'] = True
                result = {**common, 'ok': False, 'error': {'code': 'FIXTURE_ACK_LOST', 'message': 'Simulated uncertain execution; inspect status.'}}
            else:
                state['pending'] = False
                result = {**common, 'schema': 'a22x.install.result.v1', 'install_operation': plan['operation'],
                          'transaction_id': TX, 'readback_verified': True,
                          'journal_path': str(Path(checkpoint).parent / 'journal.json'), 'checkpoint_path': checkpoint,
                          'next_action': plan['next_action'], 'userdata_written': plan['userdata_written'],
                          'automatic_factory_reset': False, 'auto_reboot': False, 'atomic': False}
                if case == 'bad-receipt': result['transaction_id'] = '2' * 32
    save()
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
