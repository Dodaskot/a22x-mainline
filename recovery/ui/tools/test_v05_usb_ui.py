#!/usr/bin/env python3
"""UI/USB argv and JSON contract tests; deliberately no MTP service or USB I/O."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

UI = Path(__file__).resolve().parents[1]


def main():
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    reports = []
    with tempfile.TemporaryDirectory(prefix="ui-v05-usb-", dir=os.environ.get("A22X_UI_FIXTURE_BASE")) as temporary:
        for operation in ("usb-roundtrip", "usb-stale-start", "usb-stale-stop", "usb-unavailable", "usb-not-applied"):
            root = Path(temporary) / operation
            root.mkdir()
            work = root / "work"
            work.mkdir(mode=0o700)
            config = dict(candidates=[dict(id="userdata", label="userdata", mountpoint="/fixture/userdata", actual_access="rw",
                                           volume_token="1" * 64, mount_token="2" * 64, new_uid=0, new_gid=0)])
            if operation == "usb-unavailable":
                config.update(available=False, reason="UI test double: service unavailable")
            if operation == "usb-not-applied":
                config["behavior"] = "not-applied"
            (root / "usb-fixture.json").write_text(json.dumps(config))
            result = subprocess.run([binary, "--fixture-root", str(root), "--workdir", str(work),
                                     "--python", sys.executable, "--usb-files-helper", str(UI / "tools/usb_fixture.py"),
                                     "--workflow", operation, "--render-native", str(UI / "previews" / f"v05-{operation}-native.ppm")],
                                    capture_output=True, text=True, timeout=30)
            expected = 1 if operation == "usb-not-applied" else 0
            if result.returncode != expected or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] == (not expected)
            assert "[usb-workflow] failed" not in result.stderr, result.stderr
            requests = [json.loads(line) for line in (root / "usb-requests.jsonl").read_text().splitlines()]
            operations = [r["operation"] for r in requests]
            assert operations[0] == "status"
            if operation == "usb-unavailable":
                assert operations == ["status"]
            elif operation == "usb-not-applied":
                assert operations == ["status", "start", "status"]
            else:
                state = json.loads((root / "usb-fixture-state.json").read_text()) if (root / "usb-fixture-state.json").exists() else {"state": "stopped"}
                assert state["state"] == "stopped", "UI test left a synthetic sharing session"
                if operation == "usb-roundtrip":
                    starts = [r for r in requests if r["operation"] == "start"]
                    assert [r["read_only"] for r in starts] == [True, False]
                    assert all(r["volume"] == "userdata" and r["expected_volume_token"] == "1" * 64 and r["expected_mount_token"] == "2" * 64 for r in starts)
                elif operation == "usb-stale-start":
                    assert "MOUNT_CHANGED" not in result.stdout
                    assert operations == ["status", "start", "status"]
                else:
                    stops = [r for r in requests if r["operation"] == "stop"]
                    assert len(stops) == 2 and stops[0]["expected_session_token"] == "0" * 64 and stops[1]["expected_session_token"] != "0" * 64
            (UI / f"v05-{operation}.log").write_text(result.stderr)
            reports.append(dict(workflow=operation, result=outcome, expected_rejection=bool(expected), requests=operations))
    target = UI / "v05-usb-ui-results.json"
    target.write_text(json.dumps(dict(ok=True, tested_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     source_sha256=hashlib.sha256((UI / "recovery_ui.c").read_bytes()).hexdigest(),
                                     helper="explicit file-backed UI contract test double", usb_or_ipc_io=False,
                                     physical_device_io=False, workflows=reports), indent=2) + "\n")
    print(json.dumps(dict(ok=True, workflows=len(reports), report=str(target), physical_device_io=False, real_mtp_test=False)))


if __name__ == "__main__":
    main()
