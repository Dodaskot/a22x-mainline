#!/usr/bin/env python3
"""Exercise the compiled UI -> argv/JSON -> filesystem helper on fixture directories."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

UI = Path(__file__).resolve().parents[1]
HELPER = UI.parent / "filesystem.py"
EXPECTED_TEXT = "Перевірка UI\nvalue=1\nliteral=$HOME; `uname`\nдописано=так\n"


def fixture(root: Path):
    volumes = []
    for ident, filesystem, mounted, readonly, fscrypt in (
        ("boot", "raw", False, True, False),
        ("userdata", "ext4", False, True, False),
        ("sdcard-p1", "ext4", True, False, False),
        ("android-data", "crypto_LUKS", False, True, False),
        ("system", "erofs", False, True, False),
        ("fscrypt-data", "ext4", False, True, True),
    ):
        path = root / "volumes" / ident
        path.mkdir(parents=True)
        volumes.append(dict(id=ident, path=str(path.relative_to(root)), fs_type=filesystem,
                            mounted=mounted, read_only=readonly, fscrypt_feature=fscrypt))
    data = root / "volumes/userdata"
    for number in range(13):
        (data / f"page-{number:02d}.txt").write_text(f"fixture {number}\n", encoding="utf-8")
    (data / "stale.txt").write_text("Original content must survive.\n", encoding="utf-8")
    # A partial multi-byte character at 64 KiB must never become editable text.
    (data / "large.txt").write_bytes(b"a" * 65535 + "ї".encode("utf-8") * (600 * 1024))
    (data / "binary.bin").write_bytes(b"\x00\xff\x01")
    (data / "symlink").symlink_to("stale.txt")
    (root / "filesystem-fixture.json").write_text(
        json.dumps({"schema": "a22x.filesystem.fixture.v1", "volumes": volumes}), encoding="utf-8")
    work = root / "ui-work"
    work.mkdir(mode=0o700)
    return data, work


def main():
    reports = []
    previews = UI / "previews"
    previews.mkdir(exist_ok=True)
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    with tempfile.TemporaryDirectory(prefix="ui-files-fixtures-", dir=UI) as temp:
        for operation in ("files-roundtrip", "files-stale-write", "files-large-read"):
            root = Path(temp) / operation
            data, work = fixture(root)
            original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in data.iterdir() if p.is_file() and not p.is_symlink()}
            args = [binary, "--fixture-root", str(root), "--workdir", str(work),
                    "--python", sys.executable, "--filesystem-helper", str(HELPER),
                    "--workflow", operation, "--render", str(previews / (operation + ".ppm"))]
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            expected_code = 1 if operation == "files-stale-write" else 0
            if result.returncode != expected_code or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] == (expected_code == 0), outcome
            assert "[files-workflow] failed" not in result.stderr, result.stderr
            assert "Перевірка UI" not in result.stderr and "Original content must survive" not in result.stderr
            assert not list(work.iterdir()), "The staged RAM text file was not removed"
            assert not (data / "UI-файли").exists()
            assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in data.iterdir() if p.is_file() and not p.is_symlink()} == original
            assert (data / "symlink").is_symlink()
            if operation == "files-roundtrip":
                copied = root / "volumes/sdcard-p1/скопійовано.txt"
                assert copied.read_text(encoding="utf-8") == EXPECTED_TEXT
                state = json.loads((root / ".filesystem-state.json").read_text())
                assert not state["userdata"]["mounted"], state
            reports.append({"workflow": operation, "exit": result.returncode,
                            "expected_rejection": expected_code != 0,
                            "other_files_unchanged": True, "text_stage_cleaned": True,
                            "stdout_file_contents_redacted": True, "result": outcome})
        blocked = subprocess.run([binary, "--device", "--workflow", "files-roundtrip"],
                                 capture_output=True, text=True, timeout=10)
        assert blocked.returncode == 2 and "restricted" in blocked.stderr
    report = {"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "workflows": reports, "physical_device_io": False,
              "device_headless_workflow_rejected": True}
    target = UI / "filesystem-test-results.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": True, "report": str(target), "fixture_workflows": len(reports),
                      "physical_device_io": False}))


if __name__ == "__main__":
    main()
