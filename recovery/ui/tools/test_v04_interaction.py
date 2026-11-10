#!/usr/bin/env python3
"""Targeted v0.4 gesture/cache and verified display-mode integration checks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from test_filesystem_integration import EXPECTED_TEXT, fixture

UI = Path(__file__).resolve().parents[1]
PROJECT = UI.parent


def digest_files(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.iterdir() if p.is_file() and not p.is_symlink()}


def main():
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    previews = UI / "previews"
    previews.mkdir(exist_ok=True)
    reports = []
    operations = ("files-scroll", "files-roundtrip", "display-roundtrip", "display-unsupported",
                  "display-no-service", "display-not-applied", "display-set-error")
    # Keep mutable test volumes outside the source workspace. An explicit tmpfs
    # root also isolates execution from workspace synchronization/watchers.
    with tempfile.TemporaryDirectory(prefix="ui-v04-fixtures-", dir=os.environ.get("A22X_UI_FIXTURE_BASE")) as temp:
        for operation in operations:
            root = Path(temp) / operation
            data, work = fixture(root)
            if operation == "files-scroll":
                for path in data.iterdir():
                    path.unlink()
                for number in range(200):
                    (data / f"entry-{number:03d}.txt").write_text(f"entry {number}\n")
            original = digest_files(data)
            state = dict(supported=True, available_hz=[60, 90], current_hz=60,
                         actual_hz=59.9998, service_running=True, reason=None)
            if operation == "display-unsupported":
                state["available_hz"] = [60]
            elif operation == "display-no-service":
                state.update(supported=False, available_hz=[], current_hz=None, actual_hz=None,
                             service_running=False, reason="Fixture: display service is unavailable")
            elif operation in ("display-not-applied", "display-set-error"):
                state["behavior"] = operation.removeprefix("display-")
            (root / "display-fixture.json").write_text(json.dumps(state))
            command = [binary, "--fixture-root", str(root), "--workdir", str(work),
                       "--python", sys.executable, "--filesystem-helper", str(PROJECT / "filesystem.py"),
                       "--display-helper", str(UI / "tools/display_fixture.py"), "--workflow", operation,
                       "--render-native", str(previews / ("v04-" + operation + "-native.ppm"))]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            expected_error = operation == "display-not-applied"
            if result.returncode != int(expected_error) or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] == (not expected_error), outcome
            assert "workflow] failed" not in result.stderr, result.stderr
            assert original == digest_files(data), "Unrelated file content changed"
            assert not list(work.iterdir()), "UI left a staged text file"
            extra = {}
            if operation == "files-scroll":
                gestures = [tuple(map(int, match)) for match in re.findall(
                    r"offset_px=(\d+) first_index=(\d+) total=(\d+) cached_first=(\d+) cached_count=(\d+) tap_suppressed=1",
                    result.stderr)]
                assert len(gestures) >= 40 and gestures[0][:3] == (500, 4, 200)
                assert gestures[1][0] == 0 and max(row[0] for row in gestures) == 22382
                assert all(row[4] <= 64 for row in gestures) and max(row[3] for row in gestures) >= 160
                assert result.stderr.count("[recovery-ui:files-read] exit=") == 2
                assert "[recovery-ui:files-stat]" not in result.stderr
                assert "[recovery-ui:files-write]" not in result.stderr
                assert json.loads((root / ".filesystem-state.json").read_text())["userdata"]["read_only"]
                extra = {"files": 200, "gestures": len(gestures), "maximum_cache_entries": 64,
                         "explicit_file_reads": 2, "drag_file_reads": 0, "drag_metadata_actions": 0,
                         "verified_offsets": [0, 21, 500, 22382], "file_contents_unchanged": True}
            elif operation == "files-roundtrip":
                assert (root / "volumes/sdcard-p1/скопійовано.txt").read_text() == EXPECTED_TEXT
                assert not (data / "UI-файли").exists()
                assert not json.loads((root / ".filesystem-state.json").read_text())["userdata"]["mounted"]
            else:
                requests = [json.loads(line) for line in (root / "display-requests.jsonl").read_text().splitlines()]
                sequence = [(r["operation"], r["hz"]) for r in requests]
                expected = [("status", None)]
                if operation == "display-roundtrip":
                    expected += [("set", 90), ("status", None), ("set", 60), ("status", None)]
                elif operation in ("display-not-applied", "display-set-error"):
                    expected += [("set", 90), ("status", None)]
                assert sequence == expected, sequence
                assert json.loads((root / "display-fixture.json").read_text())["current_hz"] == state["current_hz"]
                extra = {"helper_requests": requests, "separate_status_verification": True}
            reports.append({"workflow": operation, "exit": result.returncode,
                            "expected_rejection": expected_error, "result": outcome, **extra})
    target = UI / "v04-interaction-results.json"
    target.write_text(json.dumps({"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  "physical_device_io": False, "fixture_base": os.environ.get("A22X_UI_FIXTURE_BASE", tempfile.gettempdir()),
                                  "source_sha256": hashlib.sha256((UI / "recovery_ui.c").read_bytes()).hexdigest(),
                                  "workflows": reports}, indent=2) + "\n")
    print(json.dumps({"ok": True, "workflows": len(reports), "report": str(target), "physical_device_io": False}))


if __name__ == "__main__":
    main()
