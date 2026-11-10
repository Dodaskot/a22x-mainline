#!/usr/bin/env python3
"""Read-only manifest adapter for the real installer UI fixture, never device IO.

Installer fixture snapshots contain explicitly marked regular-image simulation
data. The actual install.py helper verifies their bytes. This adapter supplies
only their already-selected repository listing to exercise the UI backup picker;
it neither creates nor verifies a Partclone backup and never runs mutations.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-root', required=True, type=Path)
    parser.add_argument('operation', choices=['list'])
    parser.add_argument('--repository', required=True, type=Path)
    args = parser.parse_args()
    root, repository = args.fixture_root, args.repository
    assert root.is_absolute() and root.resolve() == root and root.is_dir()
    assert repository.is_absolute() and repository.resolve() == repository and root in repository.parents
    backups = []
    for path in sorted(repository.iterdir()):
        if not path.is_dir() or path.is_symlink() or path.name.startswith('.'):
            continue
        manifest_path = path / 'manifest.json'
        if not manifest_path.is_file():
            continue
        assert manifest_path.resolve() == manifest_path and manifest_path.stat().st_size < 1048576
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('schema') != 'a22x.backup.v2':
            continue
        assert manifest.get('fixture_regular_images') is True
        assert manifest.get('source_mode') == 'fixture'
        backups.append({'path': str(path), 'name': path.name, 'schema': manifest['schema'],
                        'workflow': manifest['workflow'], 'profile': manifest['profile']})
    print(json.dumps({'ok': True, 'operation': 'list', 'repository': str(repository),
                      'backups': backups, 'fixture_backup_picker_adapter': True}))


if __name__ == '__main__':
    main()
