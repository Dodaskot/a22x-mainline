#!/usr/bin/env python3
"""Focused kinetic scrolling through actual filesystem-helper operations."""
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

from test_filesystem_integration import fixture
from test_v04_interaction import digest_files

UI = Path(__file__).resolve().parents[1]
PROJECT = UI.parent


def main():
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    reports = []
    previews = UI / "previews"
    previews.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ui-v05-motion-", dir=os.environ.get("A22X_UI_FIXTURE_BASE")) as temp:
        for operation in ("files-fling-60", "files-fling-90", "volumes-motion", "files-editor-motion"):
            root = Path(temp) / operation
            data, work = fixture(root)
            document = "".join(f"line-{n:03d}  Ґіїє  Linux configuration\n" for n in range(300))
            if operation.startswith("files-fling"):
                for path in data.iterdir():
                    path.unlink()
                for number in range(200):
                    (data / f"entry-{number:03d}.txt").write_text(f"entry {number}\n")
            elif operation == "files-editor-motion":
                (data / "long-scroll.txt").write_text(document)
            elif operation == "volumes-motion":
                config = json.loads((root / "filesystem-fixture.json").read_text())
                for number in range(74):
                    ident = "zzzz-last" if number == 73 else f"extra-{number:02d}"
                    folder = root / "volumes" / ident
                    folder.mkdir()
                    config["volumes"].append(dict(id=ident, path=str(folder.relative_to(root)),
                                                  fs_type="raw" if number == 73 else "ext4",
                                                  mounted=False, read_only=True))
                (root / "filesystem-fixture.json").write_text(json.dumps(config))
            original = digest_files(data)
            result = subprocess.run(
                [binary, "--fixture-root", str(root), "--workdir", str(work),
                 "--python", sys.executable, "--filesystem-helper", str(PROJECT / "filesystem.py"),
                 "--workflow", operation, "--render-native", str(previews / f"v05-{operation}-native.ppm")],
                capture_output=True, text=True, timeout=60)
            if result.returncode != 0 or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] and "workflow] failed" not in result.stderr, result.stderr
            assert not list(work.iterdir()), "Text staging file leaked"
            starts = re.findall(r"kinetic-start\] surface=(\w+) offset_px=(\d+) velocity_px_s=([\d.-]+)", result.stderr)
            assert starts and all(180 <= abs(float(row[2])) <= 3500 for row in starts), result.stderr
            assert "scroll-stop-tap]" in result.stderr, result.stderr
            extra = {"kinetic_starts": len(starts), "tap_to_stop_consumed": True}
            if operation.startswith("files-fling"):
                assert digest_files(data) == original
                assert result.stderr.count("[recovery-ui:files-read] exit=") == 1
                assert "[recovery-ui:files-stat]" not in result.stderr
                assert "[recovery-ui:files-write]" not in result.stderr
                assert result.stderr.count("[recovery-ui:files-list] exit=") >= 3
                extra.update(files=200, maximum_cache_entries=64, fling_crosses_cache_in_both_directions=True,
                             explicit_file_reads=1, drag_file_reads=0, animation_tick_ms=11 if operation.endswith("90") else 16)
            elif operation == "volumes-motion":
                assert not (root / ".filesystem-state.json").exists(), "Browsing mounted or modified a volume"
                assert digest_files(data) == original
                extra.update(volumes=80, last_volume_accessible=True, mount_operations=0)
            else:
                written = (data / "long-scroll.txt").read_text()
                assert written.count("ґ") == 1 and written.replace("ґ", "") == document
                current = digest_files(data)
                current.pop("long-scroll.txt")
                original.pop("long-scroll.txt")
                assert current == original
                assert result.stderr.count("[recovery-ui:files-write] exit=") == 1
                extra.update(viewer_and_editor_fling=True, drag_preserves_caret=True,
                             keyboard_interrupt_inserts_once=True, saved_utf8_bytes=len(written.encode()))
            log = UI / f"v05-{operation}.log"
            log.write_text(result.stderr)
            reports.append({"workflow": operation, "exit": result.returncode, "result": outcome, **extra})
    target = UI / "v05-interaction-results.json"
    target.write_text(json.dumps({"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  "physical_device_io": False, "fixture_base": os.environ.get("A22X_UI_FIXTURE_BASE", tempfile.gettempdir()),
                                  "source_sha256": hashlib.sha256((UI / "recovery_ui.c").read_bytes()).hexdigest(),
                                  "workflows": reports}, indent=2) + "\n")
    print(json.dumps({"ok": True, "workflows": len(reports), "report": str(target), "physical_device_io": False}))


if __name__ == "__main__":
    main()
