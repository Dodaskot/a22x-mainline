#!/usr/bin/env python3
"""Drive v0.3 UI controls through the real helpers using directory fixtures only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from test_filesystem_integration import fixture

UI = Path(__file__).resolve().parents[1]
PROJECT = UI.parent


def make_fixture(root: Path, no_volume=False):
    data, work = fixture(root)
    (data / "stale.txt").chmod(0o644)
    (data / "hard.txt").write_text("Shared inode must not be changed.\n")
    os.link(data / "hard.txt", data / "hard-again.txt")
    (root / "outside.txt").write_text("Outside volume: never open this file.\n")
    (data / "escape-link").symlink_to("../outside.txt")
    tree = data / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested/settings.conf").write_text("Мова=українська\nbrightness=80\n")
    (tree / "settings-link").symlink_to("nested/settings.conf")
    (tree / "nested/settings.conf").chmod(0o640)
    (tree / "nested").chmod(0o750)
    logs = root / "management-logs"
    logs.mkdir()
    (logs / "ui.log").write_text("Recovery fixture diagnostic line\n")
    (logs / "do-not-export.key").write_text("PRIVATE FIXTURE KEY MUST NOT BE EXPORTED\n")
    (root / "management-fixture.json").write_text(json.dumps({"ssh": {
        "available": True, "running": False, "address": "172.16.42.1", "port": 22,
        "authorized_keys_count": 1, "host_fingerprint": "SHA256:fixture-key-only",
        "host_key_ephemeral": True, "reason": "Fixture: no actual SSH server"}}))
    if no_volume:
        path = root / "filesystem-fixture.json"
        config = json.loads(path.read_text())
        for volume in config["volumes"]:
            volume["mounted"] = False
            volume["read_only"] = True
        path.write_text(json.dumps(config))
    return data, work


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--without-tree", action="store_true", help="Explicitly defer the two tree workflows while their helper is under development")
    selection.add_argument("--only-tree", action="store_true", help="Exercise only newly completed directory-copy integration")
    args = parser.parse_args()
    operations = ["files-metadata", "files-stale-metadata", "files-link", "files-link-escape",
                  "tools-status", "tools-export", "tools-stale-export", "tools-no-volume"]
    if not args.without_tree:
        operations += ["files-copy-tree", "files-stale-tree"]
    if args.only_tree:
        operations = ["files-copy-tree", "files-stale-tree"]
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    previews = UI / "previews"
    previews.mkdir(exist_ok=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix="ui-v03-fixtures-", dir=UI) as temporary:
        for operation in operations:
            root = Path(temporary) / operation
            data, work = make_fixture(root, operation == "tools-no-volume")
            command = [binary, "--fixture-root", str(root), "--workdir", str(work),
                       "--python", sys.executable, "--filesystem-helper", str(PROJECT / "filesystem.py"),
                       "--management-helper", str(PROJECT / "management.py"), "--workflow", operation,
                       "--render-native", str(previews / ("v03-" + operation + "-native.ppm"))]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            rejection = "stale" in operation or operation == "files-link-escape"
            expected_code = int(rejection)
            if result.returncode != expected_code or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] == (not rejection), outcome
            assert "workflow] failed" not in result.stderr, result.stderr
            assert "Original content must survive" not in result.stderr
            assert "Outside volume: never open" not in result.stderr
            assert not list(work.iterdir())
            assert (root / "outside.txt").read_text() == "Outside volume: never open this file.\n"
            assert (data / "stale.txt").read_text() == "Original content must survive.\n"
            expected_mode = 0o640 if operation == "files-metadata" else 0o644
            assert stat.S_IMODE((data / "stale.txt").stat().st_mode) == expected_mode
            assert (data / "hard.txt").stat().st_nlink == 2
            assert (data / "symlink").is_symlink() and (data / "escape-link").is_symlink()
            exported = list((root / "volumes/sdcard-p1").glob("a22x-recovery-logs-*.tar.gz"))
            archive_sha = None
            if operation == "tools-export":
                assert len(exported) == 1
                archive_sha = hashlib.sha256(exported[0].read_bytes()).hexdigest()
                assert stat.S_IMODE(exported[0].stat().st_mode) == 0o600
                with tarfile.open(exported[0], "r:gz") as archive:
                    assert "manifest.json" in archive.getnames() and "logs/ui.log" in archive.getnames()
                    assert not any(".key" in name for name in archive.getnames())
                    assert archive.extractfile("logs/ui.log").read() == b"Recovery fixture diagnostic line\n"
                    manifest = json.load(archive.extractfile("manifest.json"))
                    assert manifest["mode"] == "fixture"
            else:
                assert not exported
            target = root / "volumes/sdcard-p1/copied-tree"
            if operation == "files-copy-tree":
                assert (target / "nested/settings.conf").read_bytes() == (data / "tree/nested/settings.conf").read_bytes()
                assert (target / "settings-link").is_symlink()
                assert os.readlink(target / "settings-link") == "nested/settings.conf"
                assert stat.S_IMODE((target / "nested").stat().st_mode) == 0o750
                assert stat.S_IMODE((target / "nested/settings.conf").stat().st_mode) == 0o640
            else:
                assert not target.exists()
            reports.append({"workflow": operation, "expected_rejection": rejection,
                            "exit": result.returncode, "result": outcome, "archive_sha256": archive_sha})
    report = {"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "physical_device_io": False, "workflows": reports,
              "deferred_tree_workflows": 2 if args.without_tree else 0}
    target = UI / ("v03-tree-test-results.json" if args.only_tree else "v03-test-results.json")
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": True, "report": str(target), "fixture_workflows": len(reports),
                      "deferred_tree_workflows": report["deferred_tree_workflows"], "physical_device_io": False}))


if __name__ == "__main__":
    main()
