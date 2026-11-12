#!/usr/bin/env python3
"""Exercise native UI's process/JSON/confirmation bridge on regular files only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


UI = Path(__file__).resolve().parents[1]
BACKEND = UI.parent / "backend"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, expected: int = 0):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode != expected:
        raise AssertionError((args, result.returncode, result.stdout, result.stderr))
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    results = []
    previews = UI / "previews"
    previews.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ui-fixtures-", dir=UI) as temporary:
        for profile, labels in (("linux", ("boot", "userdata")),
                                ("android", ("boot", "metadata", "userdata"))):
            root = Path(temporary) / profile
            run(sys.executable, str(BACKEND / "demo_fixture.py"), str(root))
            workdir = root / "plans/ui"
            workdir.mkdir(mode=0o700)
            command = [str(UI / "a22x-recovery-ui"), "--fixture-root", str(root),
                       "--repository", str(root / "storage/sd"), "--workdir", str(workdir),
                       "--python", sys.executable, "--backend", str(BACKEND / "recoveryctl.py")]
            original = {name: digest(root / "partitions" / (name + ".img")) for name in labels}
            retained = digest(root / "partitions/super.img")
            inventory = run(*command, "--workflow", "inventory",
                            "--render", str(previews / (profile + "-inventory.ppm")))
            assert inventory["ok"]
            backup = run(*command, "--workflow", "backup-" + profile,
                         "--render", str(previews / (profile + "-backup-result.ppm")))
            assert backup["ok"] and backup["progress_events"] > 0
            verification = run(*command, "--workflow", "verify-first")
            assert verification["ok"] and verification["progress_events"] > 0
            for name in labels:
                with (root / "partitions" / (name + ".img")).open("r+b") as stream:
                    stream.seek(0)
                    stream.write(b"MODIFIED AFTER BACKUP FOR RESTORE TEST\n")
            assert all(digest(root / "partitions" / (n + ".img")) != original[n] for n in labels)
            restore = run(*command, "--workflow", "restore-first",
                          "--render", str(previews / (profile + "-restore-result.ppm")))
            assert restore["ok"] and restore["progress_events"] > 0
            restored = {name: digest(root / "partitions" / (name + ".img")) for name in labels}
            assert restored == original
            assert digest(root / "partitions/super.img") == retained
            # A damaged archive must be presented as failed verification, not success.
            archive = next(path for path in (root / "storage/sd").rglob("*")
                           if path.is_file() and path.suffix in {".gz", ".zst"})
            with archive.open("r+b") as stream:
                value = stream.read(1)
                stream.seek(0)
                stream.write(bytes([value[0] ^ 0x40]))
            damaged = run(*command, "--workflow", "verify-first",
                          "--render", str(previews / (profile + "-damaged-result.ppm")), expected=1)
            assert not damaged["ok"]
            assert {name: digest(root / "partitions" / (name + ".img")) for name in labels} == original
            # 2026-11-11: the backup, damaged as it is, is renamed and deleted on its page; nothing else goes.
            keep = root / "storage/sd/notes.txt"
            keep.write_text("mine")
            managed = run(*command, "--workflow", "manage-first",
                          "--render", str(previews / (profile + "-manage-result.ppm")))
            assert managed["ok"] and managed["backend_operation"] == "list"
            assert [path.name for path in (root / "storage/sd").iterdir()] == ["notes.txt"] and keep.read_text() == "mine"
            assert {name: digest(root / "partitions" / (name + ".img")) for name in labels} == original
            results.append({"profile": profile, "inventory": inventory, "backup": backup, "verify": verification,
                            "restore": restore, "restored_sha256": restored,
                            "retained_super_unchanged": True,
                            "damaged_archive_ui_failed": True, "renamed_and_deleted": managed,
                            "writes": "regular files only"})
        run(str(UI / "a22x-recovery-ui"), "--device", "--workflow", "restore-first", expected=2)
    report = {"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "ui_self_tests": run(str(UI / "a22x-recovery-ui"), "--self-test"),
              "fixture_workflows": results, "physical_device_io": False,
              "device_headless_workflow_rejected": True}
    target = UI / "test-results.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": True, "report": str(target), "profiles": len(results),
                      "physical_device_io": False}))


if __name__ == "__main__":
    main()
