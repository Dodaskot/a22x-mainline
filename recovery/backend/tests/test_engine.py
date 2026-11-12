from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import engine
from common import RecoveryError, canonical, hash_file, open_regular_fd
from demo_fixture import create_fixture
from storage import FixtureContext, StorageGraph, discover_block_nodes, parse_mountinfo


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "phone"
        create_fixture(self.root, userdata_bytes=2 * 1024 * 1024)
        self.context = FixtureContext(self.root)
        self.repo = self.root / "storage/sd"
        self.serial = 0

    def tearDown(self):
        self.temporary.cleanup()

    def edit_config(self, edit):
        path = self.root / "fixture.json"
        value = json.loads(path.read_text())
        edit(value)
        path.write_bytes(canonical(value) + b"\n")
        self.context = FixtureContext(self.root)

    def snapshot(self, profile="linux", compression="gzip"):
        self.serial += 1
        plan = engine.make_backup_plan(self.context, profile, self.repo, "snapshot-" + str(self.serial), compression)
        result = engine.backup(self.context, plan, plan["confirmation_token"])
        self.assertTrue(result["verified"])
        return Path(result["backup_path"])

    def contents(self):
        return {label: (self.root / "partitions" / (label + ".img")).read_bytes()
                for label in ("boot", "metadata", "userdata", "recovery", "super")}

    def replace_targets(self, labels):
        for label in labels:
            path = self.root / "partitions" / (label + ".img")
            path.write_bytes(b"X" * path.stat().st_size)

    def reseal(self, snapshot, edit):
        path = snapshot / "manifest.json"
        manifest = json.loads(path.read_text())
        edit(manifest)
        path.write_bytes(canonical(manifest) + b"\n")
        complete_path = snapshot / "COMPLETE.json"
        complete = json.loads(complete_path.read_text())
        complete["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        complete_path.write_bytes(canonical(complete) + b"\n")

    def assert_error(self, code, callable, *args, **kwargs):
        with self.assertRaises(RecoveryError) as caught:
            callable(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_linux_backup_verify_restore_round_trip_real_archives(self):
        original = self.contents()
        snapshot = self.snapshot()
        self.assertTrue(engine.verify_backup(self.context, snapshot)["verified"])
        self.replace_targets(["boot", "userdata"])
        plan = engine.make_restore_plan(self.context, snapshot)
        events = []
        result = engine.restore(self.context, plan, plan["confirmation_token"], events.append)
        self.assertTrue(result["verified"])
        self.assertEqual(self.contents(), original)
        writes = [event["partition"] for event in events if event["phase"] == "write"]
        self.assertEqual(writes[0], "userdata")
        self.assertEqual(writes[-1], "boot")
        self.assertFalse(result["auto_reboot"])
        self.assertFalse(result["receipt"]["physical_device_written"])

    def test_android_pair_restored_together_with_boot_last(self):
        original = self.contents()
        snapshot = self.snapshot("android")
        self.replace_targets(["boot", "metadata", "userdata"])
        plan = engine.make_restore_plan(self.context, snapshot)
        self.assertEqual(plan["write_order"], ["userdata", "metadata", "boot"])
        result = engine.restore(self.context, plan, plan["confirmation_token"])
        self.assertEqual(result["receipt"]["completed_partitions"], ["userdata", "metadata", "boot"])
        self.assertEqual(self.contents(), original)

    @unittest.skipUnless(shutil.which("zstd"), "zstd executable not installed")
    def test_zstd_backup_verify_restore_round_trip(self):
        original = self.contents()
        snapshot = self.snapshot(compression="zstd")
        self.replace_targets(["boot", "userdata"])
        plan = engine.make_restore_plan(self.context, snapshot)
        engine.restore(self.context, plan, plan["confirmation_token"])
        self.assertEqual(self.contents(), original)

    def test_a_finished_backup_is_renamed_and_deleted_only_by_its_own_name(self):
        """2026-11-11, TWRP's Restore page: rename and delete. Only a complete snapshot, only with a
        usable name, and deleting only when the snapshot's own name confirms it."""
        first, second = self.snapshot("recovery"), self.snapshot("recovery")
        for name in ("", "../escape", "a/b", ".hidden", "x" * 65, "new.partial-1", "with space", None):
            with self.assertRaises(RecoveryError) as raised:
                engine.manage_backup(self.context, first, "rename", new_name=name)
            self.assertEqual(raised.exception.code, "INVALID_NAME", name)
        self.assertTrue(first.is_dir())
        with self.assertRaises(RecoveryError) as raised:
            engine.manage_backup(self.context, first, "rename", new_name=second.name)
        self.assertEqual(raised.exception.code, "BACKUP_EXISTS")
        done = engine.manage_backup(self.context, first, "rename", new_name="before-root_2026.11")
        renamed = Path(done["backup_path"])
        self.assertTrue(renamed.is_dir() and not first.exists() and renamed.parent == first.parent)
        self.assertTrue(engine.verify_backup(self.context, renamed)["verified"])
        self.assertIn("before-root_2026.11", [b["name"] for b in engine.list_backups(self.context, self.repo)["backups"]])
        with self.assertRaises(RecoveryError) as raised:
            engine.manage_backup(self.context, renamed, "delete", confirm=second.name)
        self.assertEqual(raised.exception.code, "CONFIRMATION_REQUIRED")
        self.assertTrue(renamed.is_dir())
        # A folder that is no finished snapshot is never deleted this way.
        other = self.repo / "photos"
        other.mkdir()
        (other / "keep.txt").write_text("mine")
        with self.assertRaises((RecoveryError, OSError)):
            engine.manage_backup(self.context, other, "delete", confirm="photos")
        self.assertTrue((other / "keep.txt").exists())
        engine.manage_backup(self.context, renamed, "delete", confirm=renamed.name)
        self.assertFalse(renamed.exists())
        self.assertEqual([b["name"] for b in engine.list_backups(self.context, self.repo)["backups"]], [second.name])

    def test_wrong_confirmation_never_creates_snapshot(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "not-created", "gzip")
        self.assert_error("CONFIRMATION_MISMATCH", engine.backup, self.context, plan, "BACKUP-wrong")
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_reviewed_plan_tamper_rejected(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "not-created", "gzip")
        plan["partitions"][0]["path"] = str(self.root / "partitions/super.img")
        self.assert_error("PLAN_CHANGED", engine.backup, self.context, plan, plan["confirmation_token"])
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_source_change_after_plan_rejected(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "not-created", "gzip")
        self.replace_targets(["boot"])
        self.assert_error("PLAN_STALE", engine.backup, self.context, plan, plan["confirmation_token"])

    def test_direct_mount_and_dm_slave_mount_are_rejected(self):
        for device in ("179:54", "253:0", "179:0"):
            with self.subTest(device=device):
                self.edit_config(lambda c: c.update(mounts=[{"device": device, "mountpoint": "/data"}]))
                self.assert_error("PARTITION_MOUNTED", engine.make_backup_plan,
                                  self.context, "linux", self.repo, "no", "gzip")

    def test_mounted_sibling_does_not_overlap_partition(self):
        self.edit_config(lambda c: c.update(mounts=[{"device": "179:43", "mountpoint": "/vendor"}]))
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "sibling", "gzip")
        self.assertTrue(plan["can_execute"])

    def test_repository_on_same_physical_disk_rejected(self):
        self.edit_config(lambda c: c["repositories"][0].update(device="179:42"))
        self.assert_error("REPOSITORY_ON_SOURCE_DISK", engine.make_backup_plan,
                          self.context, "linux", self.repo, "no", "gzip")

    def test_mount_appearing_after_plan_is_rejected(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "no", "gzip")
        self.edit_config(lambda c: c.update(mounts=[{"device": "253:0", "mountpoint": "/data"}]))
        self.assert_error("PLAN_STALE", engine.backup, self.context, plan, plan["confirmation_token"])

    def test_bootloader_labels_are_forbidden(self):
        for label in ("lk", "preloader", "modem", "super", "vbmeta", "efs"):
            with self.subTest(label=label):
                self.assert_error("PARTITION_FORBIDDEN", self.context.partition, label)

    def test_corrupt_second_archive_leaves_every_target_unchanged(self):
        snapshot = self.snapshot()
        self.replace_targets(["boot", "userdata"])
        plan = engine.make_restore_plan(self.context, snapshot)
        before = self.contents()
        path = snapshot / "userdata.img.gz"
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 0x80
        path.write_bytes(data)
        self.assert_error("ARCHIVE_HASH_MISMATCH", engine.restore,
                          self.context, plan, plan["confirmation_token"])
        self.assertEqual(self.contents(), before)

    def test_truncated_archive_rejected_before_any_target_write(self):
        snapshot = self.snapshot("android")
        self.replace_targets(["boot", "metadata", "userdata"])
        before = self.contents()
        path = snapshot / "userdata.img.gz"
        path.write_bytes(path.read_bytes()[:-13])
        self.assert_error("ARCHIVE_SIZE_MISMATCH", engine.make_restore_plan, self.context, snapshot)
        self.assertEqual(self.contents(), before)

    def test_raw_digest_checked_even_when_archive_digest_is_resealed(self):
        snapshot = self.snapshot()
        self.reseal(snapshot, lambda m: m["entries"][1].update(raw_sha256="0" * 64))
        self.assert_error("RAW_HASH_MISMATCH", engine.verify_backup, self.context, snapshot)

    def test_decoded_size_bound_rejects_declared_smaller_image(self):
        snapshot = self.snapshot()
        self.reseal(snapshot, lambda m: m["entries"][1].update(raw_bytes=1024))
        self.assert_error("DECODE_SIZE_MISMATCH", engine.verify_backup, self.context, snapshot)

    def test_mixed_metadata_snapshot_rejected(self):
        snapshot = self.snapshot("android")
        self.reseal(snapshot, lambda m: m["entries"][1].update(snapshot_id="another-snapshot"))
        self.assert_error("MIXED_SNAPSHOT", engine.make_restore_plan, self.context, snapshot)

    def test_missing_android_metadata_rejected(self):
        snapshot = self.snapshot("android")
        self.reseal(snapshot, lambda m: m["entries"].pop(1))
        self.assert_error("PROFILE_PARTITION_MISMATCH", engine.make_restore_plan, self.context, snapshot)

    def test_wrong_device_identity_rejected(self):
        snapshot = self.snapshot()
        self.edit_config(lambda c: c["device"].update(identity="fixture:other-phone"))
        self.assert_error("WRONG_DEVICE", engine.make_restore_plan, self.context, snapshot)

    def test_target_geometry_mismatch_rejected(self):
        snapshot = self.snapshot()
        path = self.root / "partitions/userdata.img"
        path.write_bytes(path.read_bytes()[:-1])
        self.assert_error("TARGET_SIZE_MISMATCH", engine.make_restore_plan, self.context, snapshot)

    def test_changed_retained_super_prevents_android_restore(self):
        snapshot = self.snapshot("android")
        self.replace_targets(["super"])
        self.assert_error("FIRMWARE_MISMATCH", engine.make_restore_plan, self.context, snapshot)

    def test_fingerprint_text_is_informational_when_actual_retained_hashes_match(self):
        snapshot = self.snapshot("android")
        self.edit_config(lambda c: c["firmware"].update(fingerprint="fixture:different-firmware"))
        plan = engine.make_restore_plan(self.context, snapshot)
        self.assertTrue(plan["can_execute"])

    def test_android_baseline_is_observed_without_configured_fingerprint_or_hashes(self):
        self.edit_config(lambda c: c.update(firmware={}))
        events = []
        plan = engine.make_backup_plan(self.context, "android", self.repo, "observed", "gzip", events.append)
        compatibility = plan["compatibility"]
        self.assertIsNone(compatibility["firmware_fingerprint"])
        self.assertEqual(set(compatibility["retained_partitions"]), {"super", "tee1", "tzar", "dtbo", "vbmeta"})
        for label, record in compatibility["retained_partitions"].items():
            path = self.root / "partitions" / (label + ".img")
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(record["size_bytes"], path.stat().st_size)
        self.assertEqual({e["partition"] for e in events}, {"super", "tee1", "tzar", "dtbo", "vbmeta"})

    def test_retained_unmounted_holder_still_blocks_android_plan(self):
        self.edit_config(lambda c: c["storage_graph"].update({"253:9": {"parent": None, "slaves": ["179:43"]}}))
        self.assert_error("PARTITION_HAS_HOLDERS", engine.make_backup_plan,
                          self.context, "android", self.repo, "no", "gzip")

    def test_android_plan_rejects_obvious_plain_linux_ext4(self):
        path = self.root / "partitions/userdata.img"
        with path.open("r+b") as source:
            source.seek(1080)
            source.write(b"\x53\xef")
        self.assert_error("SOURCE_PROFILE_MISMATCH", engine.make_backup_plan,
                          self.context, "android", self.repo, "no", "gzip")
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "linux", "gzip")
        self.assertEqual(plan["source_formats"]["userdata"]["kind"], "ext4")

    def test_android_f2fs_signature_does_not_claim_os_identity(self):
        path = self.root / "partitions/userdata.img"
        with path.open("r+b") as source:
            source.seek(1024)
            source.write(b"\x10\x20\xf5\xf2")
        plan = engine.make_backup_plan(self.context, "android", self.repo, "f2fs", "gzip")
        self.assertEqual(plan["source_formats"]["userdata"]["kind"], "f2fs")
        self.assertFalse(plan["source_formats"]["userdata"]["os_identity_proven"])

    def test_changed_retained_dtbo_prevents_android_restore(self):
        snapshot = self.snapshot("android")
        path = self.root / "partitions/dtbo.img"
        path.write_bytes(b"Q" * path.stat().st_size)
        self.assert_error("FIRMWARE_MISMATCH", engine.make_restore_plan, self.context, snapshot)

    def test_archive_traversal_rejected_without_opening_path(self):
        snapshot = self.snapshot()
        self.reseal(snapshot, lambda m: m["entries"][0].update(archive="../outside.gz"))
        self.assert_error("INVALID_ARCHIVE_PATH", engine.verify_backup, self.context, snapshot)

    def test_symlink_archive_rejected(self):
        snapshot = self.snapshot()
        archive = snapshot / "userdata.img.gz"
        moved = snapshot / "alternate.gz"
        archive.rename(moved)
        archive.symlink_to(moved.name)
        self.assert_error("SYMLINK_REJECTED", engine.verify_backup, self.context, snapshot)

    def test_symlink_fixture_partition_rejected(self):
        path = self.root / "partitions/boot.img"
        path.unlink()
        path.symlink_to("recovery.img")
        self.assert_error("SYMLINK_REJECTED", self.context.partition, "boot")

    def test_nonregular_fixture_cannot_be_opened_for_restore(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        self.assert_error("REGULAR_FILE_REQUIRED", open_regular_fd, fifo, write=True)

    def test_existing_backup_is_never_overwritten(self):
        snapshot = self.snapshot()
        old = (snapshot / "manifest.json").read_bytes()
        self.assert_error("BACKUP_EXISTS", engine.make_backup_plan, self.context, "linux",
                          self.repo, snapshot.name, "gzip")
        self.assertEqual((snapshot / "manifest.json").read_bytes(), old)

    def test_operation_lock_excludes_concurrent_backup(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "no", "gzip")
        with self.context.lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_error("OPERATION_BUSY", engine.backup, self.context, plan, plan["confirmation_token"])

    def test_low_space_leaves_partial_without_complete_marker(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "disk-full", "gzip")
        with mock.patch("engine._check_free", side_effect=RecoveryError("INSUFFICIENT_SPACE", "simulated ENOSPC admission")):
            self.assert_error("INSUFFICIENT_SPACE", engine.backup, self.context, plan, plan["confirmation_token"])
        self.assertFalse((self.repo / "disk-full").exists())
        partial = list(self.repo.glob(".disk-full.partial-*"))
        self.assertEqual(len(partial), 1)
        self.assertTrue((partial[0] / "FAILED.json").is_file())
        self.assertFalse((partial[0] / "COMPLETE.json").exists())
        self.assertEqual(engine.list_backups(self.context, self.repo)["backups"], [])

    def test_interrupt_is_not_published_as_complete(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "interrupt", "gzip")
        def interrupt(event):
            if event["phase"] == "compress":
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            engine.backup(self.context, plan, plan["confirmation_token"], interrupt)
        self.assertFalse((self.repo / "interrupt").exists())
        self.assertFalse(any(self.repo.glob("*/COMPLETE.json")))
        self.assertEqual(engine.list_backups(self.context, self.repo)["backups"], [])

    def test_actual_write_enospc_exception_is_not_complete(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "enospc", "gzip")
        with mock.patch("engine.gzip.GzipFile.write", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError) as error:
                engine.backup(self.context, plan, plan["confirmation_token"])
        self.assertEqual(error.exception.errno, 28)
        self.assertFalse((self.repo / "enospc").exists())
        partial = list(self.repo.glob(".enospc.partial-*"))
        self.assertEqual(len(partial), 1)
        self.assertFalse((partial[0] / "COMPLETE.json").exists())

    def test_compressed_backup_does_not_require_raw_sized_free_space(self):
        with mock.patch("engine.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024 * 1024)):
            # A logical 32MiB sparse root fits the streaming policy with 20MiB free.
            path = self.root / "partitions/userdata.img"
            with path.open("r+b") as f:
                f.truncate(32 * 1024 * 1024)
            self.edit_config(lambda c: c["partitions"]["userdata"].update(size_bytes=32 * 1024 * 1024))
            plan = engine.make_backup_plan(self.context, "linux", self.repo, "compressed", "gzip")
            result = engine.backup(self.context, plan, plan["confirmation_token"])
        self.assertGreater(result["raw_bytes"], 20 * 1024 * 1024)
        self.assertLess(result["archive_bytes"], 20 * 1024 * 1024)

    def test_no_marker_or_flag_can_enable_device_restore(self):
        fake_device = SimpleNamespace(mode="device", can_restore=True)
        self.assert_error("DEVICE_RESTORE_NOT_QUALIFIED", engine.restore, fake_device,
                          {"admitted": True}, "arbitrary")

    def test_restore_verification_finishes_before_first_write(self):
        snapshot = self.snapshot("android")
        self.replace_targets(["boot", "metadata", "userdata"])
        plan = engine.make_restore_plan(self.context, snapshot)
        events = []
        engine.restore(self.context, plan, plan["confirmation_token"], events.append)
        first_write = next(i for i, e in enumerate(events) if e["phase"] == "write")
        verified = {e["partition"] for e in events[:first_write] if e["phase"] == "verify-before-write"
                    and e["bytes_done"] == e["bytes_total"]}
        self.assertEqual(verified, {"boot", "metadata", "userdata"})

    def test_cli_outputs_json_and_executes_fixture_backup(self):
        cli = [sys.executable, str(BACKEND / "recoveryctl.py"), "--fixture-root", str(self.root)]
        plan_path = self.root / "plans/cli-plan.json"
        planned = subprocess.run(cli + ["backup-plan", "--profile", "linux", "--repository", str(self.repo),
                                       "--name", "cli-backup", "--output", str(plan_path), "--compression", "gzip"],
                                 capture_output=True, text=True)
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        result = json.loads(planned.stdout)
        self.assertTrue(result["can_execute"])
        executed = subprocess.run(cli + ["backup", "--plan", str(plan_path), "--confirm", result["confirmation_token"]],
                                  capture_output=True, text=True)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        self.assertTrue(json.loads(executed.stdout)["verified"])
        self.assertTrue(all(json.loads(line)["event"] == "progress" for line in executed.stderr.splitlines()))
        listed = subprocess.run(cli + ["list", "--repository", str(self.repo)], capture_output=True, text=True)
        self.assertEqual(json.loads(listed.stdout)["backups"][0]["name"], "cli-backup")


class GraphTests(unittest.TestCase):
    def test_sysfs_partition_without_slaves_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disk = root / "devices/block/mmcblk0"
            part = disk / "mmcblk0p54"
            part.mkdir(parents=True)
            (disk / "dev").write_text("179:0\n")
            (disk / "slaves").mkdir()
            (part / "dev").write_text("179:54\n")
            (part / "partition").write_text("54\n")
            directory = root / "class/block"
            directory.mkdir(parents=True)
            (directory / "mmcblk0").symlink_to(disk)
            (directory / "mmcblk0p54").symlink_to(part)
            nodes = discover_block_nodes(directory)
            self.assertEqual(nodes["179:54"]["parent"], "179:0")
            self.assertEqual(nodes["179:54"]["slaves"], [])
            self.assertEqual(StorageGraph(nodes).physical_roots("179:54"), {"179:0"})

    def test_nested_dm_loop_and_partition_parent_dependencies(self):
        graph = StorageGraph({"disk": {"parent": None}, "data": {"parent": "disk"},
                              "other": {"parent": "disk"}, "crypt": {"slaves": ["data"]},
                              "loop": {"slaves": ["crypt"]}, "external": {"parent": None}})
        self.assertTrue(graph.overlaps("data", "loop"))
        self.assertTrue(graph.overlaps("disk", "data"))
        self.assertFalse(graph.overlaps("data", "other"))
        self.assertFalse(graph.overlaps("data", "external"))
        self.assertEqual(graph.physical_roots("loop"), {"disk"})

    def test_dependency_cycle_rejected(self):
        graph = StorageGraph({"a": {"slaves": ["b"]}, "b": {"slaves": ["a"]}})
        with self.assertRaises(RecoveryError) as error:
            graph.leaves("a")
        self.assertEqual(error.exception.code, "INVALID_STORAGE_GRAPH")

    def test_mountinfo_handles_escaped_mountpoint(self):
        parsed = parse_mountinfo("42 1 8:1 / /media/my\\040disk rw,nosuid - ext4 /dev/sdb1 rw\n")
        self.assertEqual(parsed[0]["mountpoint"], "/media/my disk")
        self.assertEqual(parsed[0]["device"], "8:1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
