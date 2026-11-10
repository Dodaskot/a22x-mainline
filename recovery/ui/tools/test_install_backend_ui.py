#!/usr/bin/env python3
"""Actual native UI -> installer/filesystem CLI, owned regular-image fixtures.

The fixture snapshot simulation is not Partclone qualification. Its deliberately
small raw files let this suite exercise real writer/readback/checkpoint/journal
behavior without any physical device, mounted filesystem or target execution.
"""
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
spec = importlib.util.spec_from_file_location('install_backend_fixture', ROOT / 'tests/test_install.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
installer = fixture_module.i
CASES = ('boot', 'boot-noop', 'stock', 'stock-noop', 'tamper', 'source-change', 'resume')


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_uid, s.st_gid,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def refresh_backup_boot(fixture):
    """Create a matching synthetic snapshot, never weaken the current gate."""
    archive = fixture.backup / 'boot.raw'
    archive.write_bytes(fixture.target_paths['boot'].read_bytes())
    manifest_path = fixture.backup / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    entry = next(item for item in manifest['entries'] if item['label'] == 'boot')
    entry.update(raw_bytes=archive.stat().st_size, archive_bytes=archive.stat().st_size,
                 raw_sha256=checksum(archive))
    manifest_path.write_bytes(installer.canonical(manifest) + b'\n')
    complete_path = fixture.backup / 'COMPLETE.json'
    complete = json.loads(complete_path.read_text())
    complete['manifest_sha256'] = checksum(manifest_path)
    complete_path.write_bytes(installer.canonical(complete) + b'\n')


def main():
    binary = os.environ.get('A22X_INSTALL_BACKEND_UI_BINARY', str(UI / 'install-backend-ui-test'))
    prefix = os.environ.get('A22X_INSTALL_BACKEND_PREFIX', 'install-backend-ui')
    assert prefix and '/' not in prefix and '..' not in prefix
    cases = os.environ.get('A22X_INSTALL_BACKEND_CASES', ','.join(CASES)).split(',')
    assert all(case in CASES for case in cases)
    results = []
    source_files = [ROOT / 'install.py', ROOT / 'tests/test_install.py', ROOT / 'filesystem.py']
    source_before = {str(path.relative_to(ROOT)): checksum(path) for path in source_files}
    with tempfile.TemporaryDirectory(prefix='a22x-install-backend-ui-', dir=os.environ.get('A22X_UI_FIXTURE_BASE', '/dev/shm')) as temporary:
        for case in cases:
            root = Path(temporary) / case
            f = fixture_module.Fixture(root)
            if case == 'boot-noop':
                f.boot_source.write_bytes(f.target_paths['boot'].read_bytes())
            elif case == 'stock-noop':
                for label, path in f.target_paths.items():
                    path.write_bytes(f.new_bytes[label])
                refresh_backup_boot(f)
            elif case == 'resume':
                f.interrupt_stock()
            before = {label: path.read_bytes() for label, path in f.target_paths.items()}
            before_identity = {label: identity(path) for label, path in f.target_paths.items()}
            userdata = f.userdata.read_bytes()
            userdata_identity = identity(f.userdata)
            source_files = [f.boot_source, *sorted((f.sd / 'stock').iterdir()), *sorted(f.backup.iterdir())]
            sd_before = {str(path.relative_to(f.sd)): path.read_bytes() for path in source_files if path.is_file()}
            mount_state = (root / 'filesystem-fixture.json').read_bytes()
            source = '/' + (f.manifest if case.startswith('stock') or case == 'resume' else f.boot_source).relative_to(f.sd).as_posix()
            run = subprocess.run([binary, str(root), str(ROOT / 'install.py'),
                str(UI / 'tools/install_backup_picker_fixture.py'), str(ROOT / 'filesystem.py'),
                case, sys.executable, str(f.backup.parent), source],
                capture_output=True, text=True, timeout=120)
            (UI / f'{prefix}-{case}.stdout.log').write_text(run.stdout)
            (UI / f'{prefix}-{case}.stderr.log').write_text(run.stderr)
            if run.returncode:
                raise AssertionError((case, run.returncode, run.stdout, run.stderr))
            result = json.loads(run.stdout)
            assert result['ok'] and result['actual_install_backend'] and result['device_io'] is False
            denied = case in ('tamper', 'source-change')
            noop = case in ('boot-noop', 'stock-noop')
            expected = dict(before)
            if not denied and not noop:
                if case in ('stock', 'resume'):
                    expected = {**f.new_bytes, 'recovery': before['recovery']}
                else:
                    expected['boot'] = f.boot_source.read_bytes()
            assert {label: path.read_bytes() for label, path in f.target_paths.items()} == expected
            assert userdata == f.userdata.read_bytes() and userdata_identity == identity(f.userdata)
            for label, path in f.target_paths.items():
                if denied or noop or (case == 'boot' and label != 'boot'):
                    assert before_identity[label] == identity(path), (case, label)
            for relative, data in sd_before.items():
                expected_source = data + b' ' if case == 'source-change' and relative == f.boot_source.name else data
                assert (f.sd / relative).read_bytes() == expected_source, (case, relative)
            assert mount_state == (root / 'filesystem-fixture.json').read_bytes()
            status = installer.status(installer.FixtureContext(root))
            assert status['ok'] and not status['pending'] and not status['blockers']
            assert not (root / '.install-transaction.json').exists()
            journals = sorted((f.sd / '.a22x-installs').glob('*/journal.json'))
            if denied:
                assert not journals
            else:
                assert len(journals) == 1
                journal = json.loads(journals[0].read_text())
                assert journal['state'] == 'completed' and journal['checkpoint_complete']
                labels = installer.STOCK_WRITE if case.startswith('stock') or case == 'resume' else ('boot',)
                assert journal['readback'] == {label: hashlib.sha256(expected[label]).hexdigest() for label in labels}
                checkpoint = journals[0].parent / 'platform'
                checkpoint_labels = installer.CHECKPOINT_LABELS if case.startswith('stock') or case == 'resume' else ('boot',)
                for label in checkpoint_labels:
                    assert (checkpoint / (label + '.img')).read_bytes() == before[label]
            results.append({**result, 'full_target_bytes_verified': True,
                'no_op_target_identity_unchanged': noop, 'userdata_bytes_metadata_unchanged': True,
                'original_sources_backup_and_mounts_unchanged': case != 'source-change',
                'source_tamper_was_owned_fixture_only': case == 'source-change',
                'durable_checkpoint_and_journal_verified': not denied,
                'physical_partition_writes': False})
    source_after = {name: checksum(ROOT / name) for name in source_before}
    assert source_before == source_after, 'The helper changed during this acceptance run; repeat against a frozen source.'
    runtime = ('recovery_ui.c', 'install_ui.h', 'install_types.h', 'test_install_backend_ui.c',
               'tools/install_backup_picker_fixture.py', 'tools/test_install_backend_ui.py')
    report = {'ok': True, 'workflows': len(results), 'checks': sum(item['checks'] for item in results),
              'device_io': False, 'actual_install_backend': True, 'actual_filesystem_backend': True,
              'backup_picker_manifest_adapter': True, 'backup_partclone_qualification': False,
              'source_sha256': {name: checksum(UI / name) for name in runtime},
              'backend_sha256': source_before, 'cases': results}
    (UI / f'{prefix}-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('ok', 'workflows', 'checks', 'device_io', 'actual_install_backend')}))


if __name__ == '__main__':
    main()
