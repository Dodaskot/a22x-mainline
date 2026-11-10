#!/usr/bin/env python3
"""Native UI -> real Linux-v2 backend cycle on owned regular ext4 images only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

UI = Path(__file__).resolve().parents[1]
BACKEND = UI.parent / "backend"
sys.path.insert(0, str(BACKEND))
from demo_fixture import create_fixture
import partclone_image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture(root):
    create_fixture(root, userdata_bytes=64 * 1024 * 1024)
    cfg = json.loads((root / "fixture.json").read_text())
    cfg["storage_graph"].pop("253:0")
    (root / "fixture.json").write_text(json.dumps(cfg))
    tree = root / "source-tree"
    tree.mkdir()
    sentinel = "Файли Linux після відновлення\n" * 1024
    (tree / "sentinel.txt").write_text(sentinel)
    (tree / "sentinel.txt").chmod(0o640)
    (tree / "current").symlink_to("sentinel.txt")
    os.link(tree / "sentinel.txt", tree / "same-inode")
    (tree / "random.bin").write_bytes(os.urandom(1024 * 1024))
    image = root / "partitions/userdata.img"
    subprocess.run([shutil.which("mkfs.ext4"), "-q", "-F", "-b", "4096", "-d", str(tree), str(image)],
                   check=True, capture_output=True)
    volumes = []
    for ident, path in (("userdata", root / "mounted-userdata"), ("sdcard-p1", root / "storage/sd")):
        path.mkdir(exist_ok=True)
        volumes.append(dict(id=ident, path=str(path.relative_to(root)), fs_type="ext4", mounted=True,
                            read_only=False))
    (root / "filesystem-fixture.json").write_text(json.dumps({"schema": "a22x.filesystem.fixture.v1", "volumes": volumes}))
    work = root / "plans/ui"
    work.mkdir(mode=0o700)
    return image, work, sentinel


def main():
    binary = os.environ.get("A22X_UI_BINARY", str(UI / "a22x-recovery-ui"))
    # Native tools must already be installed or explicitly supplied via PATH.
    tools = partclone_image.resolve_tools()
    assert shutil.which("mkfs.ext4") and shutil.which("debugfs")
    reports = []
    with tempfile.TemporaryDirectory(prefix="ui-v05-linux-", dir=os.environ.get("A22X_UI_FIXTURE_BASE")) as temp:
        root = Path(temp) / "phone"
        image, work, sentinel = make_fixture(root)
        originals = {path.name: digest(path) for path in (root / "partitions").iterdir()}
        command = [binary, "--fixture-root", str(root), "--repository", str(root / "storage/sd"),
                   "--workdir", str(work), "--python", sys.executable, "--backend", str(BACKEND / "recoveryctl.py"),
                   "--filesystem-helper", str(UI.parent / "filesystem.py")]
        archive_hashes = None
        for operation in ("linux-prep-stale", "linux-backup", "linux-wipe", "linux-restore"):
            result = subprocess.run(command + ["--workflow", operation, "--render-native",
                                               str(UI / "previews" / f"v05-{operation}-native.ppm")],
                                    capture_output=True, text=True, timeout=120)
            expected = 1 if operation == "linux-prep-stale" else 0
            if result.returncode != expected or not result.stdout:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
            outcome = json.loads(result.stdout)
            assert outcome["ok"] == (not expected)
            assert "[linux-workflow] failed" not in result.stderr, result.stderr
            (UI / f"v05-{operation}.log").write_text(result.stderr)
            extra = {}
            if expected:
                assert "[recovery-ui:backup-plan]" not in result.stderr
                assert not (root / ".filesystem-state.json").exists()
                assert digest(image) == originals[image.name]
                extra["no_plan_or_writes_after_stale_unmount"] = True
            else:
                state = json.loads((root / ".filesystem-state.json").read_text())
                assert not state["userdata"]["mounted"]
                assert "sdcard-p1" not in state or state["sdcard-p1"]["mounted"]
                for path in (root / "partitions").iterdir():
                    if path.name != "userdata.img":
                        assert digest(path) == originals[path.name], path.name
                if operation == "linux-backup":
                    assert digest(image) == originals[image.name]
                    assert result.stderr.index("[recovery-ui:files-unmount]") < result.stderr.index("[recovery-ui:backup-plan]")
                    backup = next(path.parent for path in (root / "storage/sd").glob("*/manifest.json"))
                    manifest = json.loads((backup / "manifest.json").read_text())
                    assert manifest["schema"] == "a22x.backup.v2"
                    archive_hashes = {p.name: digest(p) for p in backup.iterdir() if p.is_file()}
                    extra.update(regular_image_bytes=image.stat().st_size, saved_archive_bytes=sum(e["archive_bytes"] for e in manifest["entries"]),
                                 explicit_normal_unmount_before_plan=True, userdata_left_offline=True)
                elif operation == "linux-wipe":
                    assert digest(image) != originals[image.name]
                    listing = subprocess.run([shutil.which("debugfs"), "-R", "ls -p /", str(image)], capture_output=True, check=True, text=True)
                    assert "sentinel.txt" not in listing.stdout
                    extra.update(userdata_empty=True, boot_preserved=True)
                else:
                    entry = next(e for e in manifest["entries"] if e["label"] == "userdata")
                    with image.open("rb") as source:
                        verified = partclone_image.verify_target(source.fileno(), entry, tool_paths=tools)
                    text = subprocess.run([shutil.which("debugfs"), "-R", "cat /sentinel.txt", str(image)], capture_output=True, check=True, text=True)
                    assert text.stdout == sentinel
                    extra.update(native_body_sha256=verified["native_body_sha256"], independently_recloned=True, boot_preserved=True)
                if archive_hashes:
                    assert {p.name: digest(p) for p in backup.iterdir() if p.is_file()} == archive_hashes
            reports.append({"workflow": operation, "result": outcome, "expected_rejection": bool(expected), **extra})
    target = UI / "v05-linux-results.json"
    target.write_text(json.dumps({"ok": True, "tested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  "physical_device_io": False, "mount_states": "filesystem helper fixture only; no real mounts",
                                  "source_sha256": digest(UI / "recovery_ui.c"), "native_tools": tools,
                                  "workflows": reports}, indent=2) + "\n")
    print(json.dumps({"ok": True, "workflows": len(reports), "report": str(target), "physical_device_io": False}))


if __name__ == "__main__":
    main()
