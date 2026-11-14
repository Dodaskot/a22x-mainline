"""Linux v2 orchestration/admission tests; every writable target is owned regular data.

The small compact codec below is explicitly a test double. Native Partclone
format/checksum/pipe tests live in test_partclone_image.py; full-source native
qualification is a separate host acceptance and is never fabricated here.
"""
from __future__ import annotations

import copy
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import engine
import linux_workflow as linux
import qualification
from common import RecoveryError, canonical, digest, stamp
from demo_fixture import create_fixture
from storage import DeviceContext, FixtureContext, operation_lock
from streaming import VerifiedArchive


class FakeArchive:
    def __init__(self, directory, entry, *, target_bytes=None, **kwargs):
        self.entry = copy.deepcopy(entry)
        raw = {"label": "userdata", "archive": "userdata.img.gz", "compression": "gzip",
               "raw_bytes": entry["raw_bytes"], "raw_sha256": entry["native_body_sha256"],
               "archive_bytes": entry["archive_bytes"], "archive_sha256": entry["archive_sha256"]}
        self.inner = VerifiedArchive(directory, raw, target_bytes=target_bytes)
        self.inner.path = Path(directory) / entry["archive"]
    def __enter__(self):
        self.inner.__enter__()
        self.source, self.initial_stamp = self.inner.source, self.inner.initial_stamp
        return self
    def __exit__(self, *args):
        self.inner.__exit__(*args)
    @property
    def index_bytes(self):
        return self.inner.index_bytes
    def verify(self, *args, **kwargs):
        return self.inner.verify(*args, **kwargs)
    def verified_chunks(self):
        return self.inner.verified_chunks()


class FakeCompact:
    VerifiedPartclone = FakeArchive
    MAX_ARCHIVE_BYTES = 256 * 1024 ** 3
    @staticmethod
    def validate_entry(entry):
        if entry.get("archive") != "userdata.partclone.zst" or entry.get("format") != "partclone.ext4.v2":
            raise RecoveryError("INVALID_COMPACT_ENTRY", "Test format mismatch")
    @staticmethod
    def estimate(fd, size, **kwargs):
        data = os.pread(fd, size, 0)
        encoded = gzip.compress(data, compresslevel=1, mtime=0)
        sha = hashlib.sha256(data).hexdigest()
        return {"label": "userdata", "format": "partclone.ext4.v2", "archive": "userdata.partclone.zst",
                "compression": "zstd", "raw_bytes": size, "raw_partition_bytes": size,
                "native_image_bytes": size, "native_image_sha256": sha, "native_body_sha256": sha,
                "archive_bytes": len(encoded), "archive_sha256": hashlib.sha256(encoded).hexdigest(),
                "partclone": {"used_blocks": size // 4096, "block_size": 4096},
                "source_probe": {"filesystem": "ext4", "uuid": "11111111-1111-4111-8111-111111111111",
                                 "partition_bytes": size, "block_size": 4096, "block_count": size // 4096,
                                 "free_blocks": 0, "filesystem_state": "clean", "features": ["extent"]}}
    @classmethod
    def create(cls, fd, output_fd, size, **kwargs):
        value = cls.estimate(fd, size)
        os.write(output_fd, gzip.compress(os.pread(fd, size, 0), compresslevel=1, mtime=0))
        return value
    @staticmethod
    def verify_target(fd, entry, progress=None, **kwargs):
        actual = hashlib.sha256(os.pread(fd, entry["raw_bytes"], 0)).hexdigest()
        if actual != entry["native_body_sha256"]:
            raise RecoveryError("RESTORE_READBACK_MISMATCH", "Fixture allocated bytes changed")
        return {"verified": True, "e2fsck_exit_code": 0, "native_body_sha256": actual,
                "source_probe": entry["source_probe"]}


WRITER = '''#!/usr/bin/python3
import os,sys
path=sys.argv[sys.argv.index('--overwrite')+1]
fd=os.open(path,os.O_WRONLY)
while True:
    data=os.read(0,65536)
    if not data: break
    view=memoryview(data)
    while view:
        n=os.write(fd,view)
        view=view[n:]
os.fsync(fd)
os.close(fd)
'''


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "phone"
        create_fixture(self.root, userdata_bytes=3 * 1024 * 1024)
        config = json.loads((self.root / "fixture.json").read_text())
        config["storage_graph"].pop("253:0")
        (self.root / "fixture.json").write_bytes(canonical(config))
        self.context = FixtureContext(self.root)
        self.repo = self.root / "storage/sd"
        self.writer = self.base / "native-writer-test-double.py"
        self.writer.write_text(WRITER)
        self.writer.chmod(0o700)
        self.tools = {"paths": {"partclone.restore": str(self.writer)}, "mkfs_config": "/etc/mke2fs.conf",
                      "manifest": None, "manifest_sha256": None}
        self.patches = [mock.patch.object(linux, "_compact", return_value=FakeCompact),
                        mock.patch.object(qualification, "toolchain", return_value=self.tools)]
        for patch in self.patches:
            patch.start()
        self.counter = 0

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(RecoveryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def snapshot(self):
        self.counter += 1
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "linux-" + str(self.counter),
                                       data_format="partclone-ext4")
        return Path(engine.backup(self.context, plan, plan["confirmation_token"])["backup_path"])

    def data(self, label):
        return (self.root / "partitions" / (label + ".img")).read_bytes()

    def change(self, label):
        path = self.root / "partitions" / (label + ".img")
        path.write_bytes(b"X" * path.stat().st_size)

    def edit_config(self, function):
        path = self.root / "fixture.json"
        config = json.loads(path.read_text())
        function(config)
        path.write_bytes(canonical(config))
        self.context = FixtureContext(self.root)

    def test_compact_dispatch_summary_and_legacy_are_distinct(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "compact", data_format="partclone-ext4")
        result = engine.plan_result(plan, self.root / "plans/test.json")
        self.assertEqual(result["schema"], "a22x.plan.v2")
        self.assertEqual(result["summary"]["workflow"], "linux-partclone-v2")
        self.assertEqual(result["summary"]["required_hold_ms"], 0)
        self.assertFalse(result["summary"]["destructive"])
        self.assertEqual(result["summary"]["qualification"]["status"], "fixture-only")
        raw = engine.make_backup_plan(self.context, "linux", self.repo, "raw", "gzip")
        self.assertEqual(raw["schema"], "a22x.plan.v1")
        self.error("INVALID_PROFILE", engine.make_backup_plan, self.context, "android", self.repo, "bad", data_format="partclone-ext4")
        self.error("INVALID_COMPRESSION", engine.make_backup_plan, self.context, "linux", self.repo, "bad", "gzip", data_format="partclone-ext4")

    def test_backup_verify_list_and_restore_boot_last(self):
        original = {label: self.data(label) for label in ("boot", "userdata", "recovery", "metadata")}
        backup = self.snapshot()
        verified = engine.verify_backup(self.context, backup)
        self.assertEqual(verified["manifest"]["schema"], "a22x.backup.v2")
        self.assertEqual(engine.list_backups(self.context, self.repo)["backups"][0]["workflow"], linux.WORKFLOW)
        self.change("userdata")
        self.change("boot")
        plan = engine.make_restore_plan(self.context, backup)
        self.assertEqual(plan["boot_action"], "restore-last")
        self.assertEqual(plan["write_order"], ["userdata", "boot"])
        self.assertEqual(plan["required_hold_ms"], 3000)
        result = engine.restore(self.context, plan, plan["confirmation_token"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["receipt"]["completed_partitions"], ["userdata", "boot"])
        self.assertFalse(result["receipt"]["physical_device_written"])
        self.assertFalse(result["auto_reboot"])
        self.assertTrue(Path(result["receipt"]["journal_path"], "result.json").is_file())
        for label, data in original.items():
            self.assertEqual(self.data(label), data)

    def test_restore_identical_boot_does_not_open_boot_writer(self):
        backup = self.snapshot()
        self.change("userdata")
        plan = engine.make_restore_plan(self.context, backup)
        self.assertEqual(plan["boot_action"], "skip-identical")
        opened = []
        original = linux._open_target
        def capture(context, part, *args):
            opened.append(part["label"])
            return original(context, part, *args)
        with mock.patch.object(linux, "_open_target", side_effect=capture):
            result = engine.restore(self.context, plan, plan["confirmation_token"])
        self.assertEqual(opened, ["userdata"])
        self.assertEqual(result["receipt"]["partitions"]["boot"]["action"], "skip-identical")

    def test_wipe_requires_copy_of_current_userdata_and_current_boot(self):
        backup = self.snapshot()
        self.change("userdata")
        self.error("RESTORE_READBACK_MISMATCH", engine.make_wipe_plan, self.context, backup)
        self.change("boot")
        self.error("BACKUP_NO_LONGER_CURRENT", engine.make_wipe_plan, self.context, backup)

    def test_wipe_plan_is_userdata_only_and_rechecks_backup_before_writing(self):
        backup = self.snapshot()
        plan = engine.make_wipe_plan(self.context, backup)
        self.assertEqual(plan["boot_action"], "preserve")
        self.assertEqual(plan["write_order"], ["userdata"])
        self.assertFalse(plan["secure_erasure"])
        before = self.data("userdata")
        archive = backup / "userdata.partclone.zst"
        raw = bytearray(archive.read_bytes())
        raw[-8] ^= 1
        archive.write_bytes(raw)
        with mock.patch.object(linux, "_format_userdata") as writer:
            self.error("ARCHIVE_HASH_MISMATCH", engine.wipe, self.context, plan, plan["confirmation_token"])
            writer.assert_not_called()
        self.assertEqual(self.data("userdata"), before)

    def test_completed_wipe_keeps_boot_recovery_and_allows_fresh_restore(self):
        backup = self.snapshot()
        saved = {label: self.data(label) for label in ("boot", "userdata", "recovery")}
        plan = engine.make_wipe_plan(self.context, backup)
        def format_owned(target, part, tools, lease, plan, state, guard, progress):
            guard(True)
            state["write_started"] = True
            os.pwrite(target.fileno(), b"Z" * part["size_bytes"], 0)
            os.fsync(target.fileno())
            state.update(readback_verified=True, fsync_completed=True)
            guard(True)
        with mock.patch.object(linux, "_format_userdata", side_effect=format_owned):
            wiped = engine.wipe(self.context, plan, plan["confirmation_token"])
        self.assertTrue(wiped["verified"])
        self.assertEqual(self.data("boot"), saved["boot"])
        self.assertEqual(self.data("recovery"), saved["recovery"])
        restore = engine.make_restore_plan(self.context, backup)
        engine.restore(self.context, restore, restore["confirmation_token"])
        self.assertEqual(self.data("userdata"), saved["userdata"])

    def test_wrong_confirmation_expired_and_tampered_plan_never_open_writer(self):
        backup = self.snapshot()
        self.change("userdata")
        plan = engine.make_restore_plan(self.context, backup)
        with mock.patch.object(linux, "_open_target") as writer:
            self.error("CONFIRMATION_MISMATCH", engine.restore, self.context, plan, "wrong")
            changed = copy.deepcopy(plan)
            changed["expires_boottime"] = 1  # the boot clock times a plan (2026-11-13 review)
            self.error("PLAN_CHANGED", engine.restore, self.context, changed, changed["confirmation_token"])
            changed = {k: v for k, v in changed.items() if k not in {"plan_id", "confirmation_token"}}
            linux._seal(changed)
            self.error("PLAN_EXPIRED", engine.restore, self.context, changed, changed["confirmation_token"])
            writer.assert_not_called()

    def test_bad_archive_before_execution_changes_no_targets(self):
        backup = self.snapshot()
        self.change("userdata")
        self.change("boot")
        plan = engine.make_restore_plan(self.context, backup)
        before = (self.data("userdata"), self.data("boot"))
        path = backup / "userdata.partclone.zst"
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(data)
        self.error("ARCHIVE_HASH_MISMATCH", engine.restore, self.context, plan, plan["confirmation_token"])
        self.assertEqual(before, (self.data("userdata"), self.data("boot")))

    def test_remount_after_full_verify_refuses_before_any_target_open(self):
        backup = self.snapshot()
        self.change("userdata")
        plan = engine.make_restore_plan(self.context, backup)
        original = linux._verify_archives
        def mounted(*args, **kwargs):
            result = original(*args, **kwargs)
            self.edit_config(lambda config: config.update(mounts=[{"device": "179:54", "mountpoint": "/data"}]))
            return result
        with mock.patch.object(linux, "_verify_archives", side_effect=mounted), mock.patch.object(linux, "_open_target") as writer:
            self.error("STORAGE_CHANGED", engine.restore, self.context, plan, plan["confirmation_token"])
            writer.assert_not_called()

    def test_active_holder_is_rejected_even_without_mount(self):
        self.edit_config(lambda config: config["storage_graph"].update({"253:0": {"parent": None, "slaves": ["179:54"]}}))
        self.error("PARTITION_HAS_HOLDERS", engine.make_backup_plan, self.context, "linux", self.repo, "no", data_format="partclone-ext4")

    def test_same_internal_disk_repository_is_rejected(self):
        self.edit_config(lambda config: config["repositories"][0].update(device="179:42"))
        self.error("REPOSITORY_ON_SOURCE_DISK", engine.make_backup_plan, self.context, "linux", self.repo, "no", data_format="partclone-ext4")

    def test_insufficient_space_returns_reviewable_blocked_estimate(self):
        with mock.patch.object(linux.shutil, "disk_usage", return_value=SimpleNamespace(free=linux.RESERVE + 512 * 1024)):
            plan = engine.make_backup_plan(self.context, "linux", self.repo, "too-small", data_format="partclone-ext4")
            self.assertFalse(plan["can_execute"])
            self.assertIn("INSUFFICIENT_SPACE", plan["blockers"])
            self.assertGreater(plan["space"]["estimated_archive_bytes"] + linux.RESERVE, plan["space"]["free_bytes"])
            self.error("PLAN_BLOCKED", engine.backup, self.context, plan, plan["confirmation_token"])
        self.assertFalse((self.repo / "too-small").exists())

    def renumber_repository(self):
        """The directory comes back under a new inode number, as exFAT does after a cache drop."""
        old = self.repo.with_name(self.repo.name + "-old")
        self.repo.rename(old)
        self.repo.mkdir(mode=0o700)
        old.rmdir()

    def test_exfat_directory_may_get_a_new_inode_number_after_planning(self):
        self.edit_config(lambda config: config["repositories"][0].update(filesystem="exfat"))
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "renumbered", data_format="partclone-ext4")
        self.renumber_repository()
        result = engine.backup(self.context, plan, plan["confirmation_token"])
        self.assertTrue((self.repo / "renumbered").is_dir(), result)

    def test_ext4_directory_replaced_after_planning_is_refused(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "replaced", data_format="partclone-ext4")
        self.renumber_repository()
        self.error("PLAN_STALE", engine.backup, self.context, plan, plan["confirmation_token"])

    def test_plan_stops_as_soon_as_the_archive_cannot_fit(self):
        refused = RecoveryError("BACKUP_SPACE_LIMIT", "budget reached")
        with mock.patch.object(FakeCompact, "estimate", side_effect=refused):
            self.error("INSUFFICIENT_SPACE", engine.make_backup_plan, self.context, "linux", self.repo, "full", data_format="partclone-ext4")
        self.assertFalse((self.repo / "full").exists())

    def test_plan_budget_follows_free_space_within_the_archive_limit(self):
        real = FakeCompact.estimate
        floor = linux.RESERVE + linux.METADATA_ALLOWANCE
        for free, budget in ((floor + 4096, 4096), (linux.RESERVE, 1), (1 << 60, FakeCompact.MAX_ARCHIVE_BYTES)):
            with self.subTest(free=free), mock.patch.object(linux.shutil, "disk_usage", return_value=SimpleNamespace(free=free)), \
                    mock.patch.object(FakeCompact, "estimate", side_effect=real) as estimate:
                engine.make_backup_plan(self.context, "linux", self.repo, "budget", data_format="partclone-ext4")
                self.assertEqual(estimate.call_args.kwargs["max_archive_bytes"], budget)

    def test_space_removed_after_plan_never_creates_archive(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "lost-space", data_format="partclone-ext4")
        with mock.patch.object(linux.shutil, "disk_usage", return_value=SimpleNamespace(free=linux.RESERVE)):
            self.error("INSUFFICIENT_SPACE", engine.backup, self.context, plan, plan["confirmation_token"])
        self.assertFalse((self.repo / "lost-space").exists())

    def test_readback_failure_records_partial_and_leaves_boot_untouched(self):
        backup = self.snapshot()
        self.change("userdata")
        self.change("boot")
        plan = engine.make_restore_plan(self.context, backup)
        before = self.data("boot")
        with mock.patch.object(FakeCompact, "verify_target", side_effect=RecoveryError("RESTORE_READBACK_MISMATCH", "injected")):
            error = self.error("RESTORE_READBACK_MISMATCH", engine.restore, self.context, plan, plan["confirmation_token"])
        receipt = error.details["receipt"]
        self.assertTrue(receipt["partial_restore_possible"])
        self.assertFalse(receipt["partitions"]["boot"]["write_started"])
        self.assertEqual(self.data("boot"), before)
        self.assertEqual(json.loads(Path(receipt["journal_path"], "result.json").read_text())["status"], "failed")

    def test_interruption_receipt_does_not_claim_native_input_is_target_prefix(self):
        backup = self.snapshot()
        self.change("userdata")
        self.change("boot")
        plan = engine.make_restore_plan(self.context, backup)
        before = self.data("boot")
        def interrupt(event):
            if event["phase"] == "write-used-blocks":
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as caught:
            engine.restore(self.context, plan, plan["confirmation_token"], interrupt)
        receipt = caught.exception.restore_receipt
        self.assertTrue(receipt["partial_restore_possible"])
        self.assertEqual(receipt["partitions"]["userdata"]["native_bytes_delivered"], 1024 * 1024)
        self.assertNotIn("verified_prefix_bytes", receipt["partitions"]["userdata"])
        self.assertEqual(self.data("boot"), before)

    def test_protected_target_in_resealed_plan_still_refused(self):
        backup = self.snapshot()
        plan = engine.make_restore_plan(self.context, backup)
        plan["write_targets"].append({"label": "recovery", "path": str(self.root / "partitions/recovery.img"), "size_bytes": 65536, "action": "restore"})
        plan = {k: v for k, v in plan.items() if k not in {"plan_id", "confirmation_token"}}
        linux._seal(plan)
        with mock.patch.object(linux, "_open_target") as writer:
            self.error("INVALID_PLAN", engine.restore, self.context, plan, plan["confirmation_token"])
            writer.assert_not_called()

    def test_generic_device_writer_is_still_hard_disabled(self):
        device = object.__new__(DeviceContext)
        self.error("DEVICE_RESTORE_NOT_QUALIFIED", device.open_partition, {}, write=True)
        self.error("DEVICE_RESTORE_NOT_QUALIFIED", engine.restore, device, {"schema": "a22x.plan.v1"}, "anything")

    def test_physical_target_open_requires_qualification_before_os_open(self):
        device = SimpleNamespace(mode="device")
        part = self.context.partition("userdata")
        plan = {"operation": "restore", "write_targets": [{"label": "userdata"}], "qualification": {}}
        with mock.patch.object(linux, "_parts", return_value=[]), mock.patch.object(qualification, "require_admission", side_effect=RecoveryError("LINUX_WORKFLOW_NOT_QUALIFIED", "blocked")), mock.patch.object(linux.os, "open") as opened:
            with self.assertRaises(RecoveryError) as raised:
                with linux._open_target(device, part, self.tools, plan):
                    self.fail("unreachable")
            self.assertEqual(raised.exception.code, "LINUX_WORKFLOW_NOT_QUALIFIED")
            opened.assert_not_called()

    def test_child_retains_lease_if_parent_context_closes(self):
        with operation_lock(self.context) as lease:
            child = linux._WriterChild([sys.executable, "-c", "import time; time.sleep(1.0)"],
                                       inherited_fds=(lease,), input_pipe=False)
        try:
            self.error("OPERATION_BUSY", lambda: self._take_lock())
            child.wait(lambda: None, timeout=5)
        finally:
            child.close()
        self._take_lock()

    def test_failed_compact_creation_is_never_published_complete(self):
        plan = engine.make_backup_plan(self.context, "linux", self.repo, "interrupted", data_format="partclone-ext4")
        with mock.patch.object(FakeCompact, "create", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                engine.backup(self.context, plan, plan["confirmation_token"])
        self.assertFalse((self.repo / "interrupted").exists())
        partials = list(self.repo.glob(".interrupted.partial-*"))
        self.assertEqual(len(partials), 1)
        self.assertFalse((partials[0] / "COMPLETE.json").exists())
        self.assertFalse(json.loads((partials[0] / "FAILED.json").read_text())["restorable"])

    def test_host_qualification_rejects_fixture_as_real_source(self):
        backup = self.snapshot()
        output = self.base / "must-not-exist"
        self.error("QUALIFICATION_REAL_SOURCE_REQUIRED", linux.qualify_to_images, backup, output, {}, {})
        self.assertFalse(output.exists())

    def test_host_image_harness_new_files_capsule_binding_and_reuse_refusal(self):
        # Deliberately simulated physical-source envelope with reduced test
        # geometry. No production option admits this fixture as a device.
        backup = self.snapshot()
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(source_mode="device", device={"model": "SM-A226B", "identity": "emmc-cid-sha256:" + "8" * 64})
        manifest_path.write_bytes(canonical(manifest))
        complete_path = backup / "COMPLETE.json"
        complete = json.loads(complete_path.read_text())
        complete["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        complete_path.write_bytes(canonical(complete))
        record = {"schema": "a22x.linux.implementation.v1", "workflow": linux.WORKFLOW,
                  "binding": {"device": manifest["device"], "context_contract": self.context.contract,
                              "partitions": manifest["source_geometry"]},
                  "sources": {name: hashlib.sha256((qualification.SOURCE_ROOT / name).read_bytes()).hexdigest()
                              for name in qualification.SOURCE_FILES}}
        config = self.base / "test-mkfs.conf"
        config.write_text("test-only-unused-config\n")
        toolchain = {"schema": "a22x.linux.toolchain.v1", "tools": {}, "runtime_files": {
            str(config): hashlib.sha256(config.read_bytes()).hexdigest()}, "mkfs_config": str(config)}
        for name in qualification.TOOL_NAMES:
            path = self.writer if name == "partclone.restore" else Path("/usr/bin/true").resolve()
            toolchain["tools"][name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        output = self.base / "owned-qualification"
        sizes = {"boot": (41, len(self.data("boot"))), "userdata": (54, len(self.data("userdata")))}
        with mock.patch.object(qualification, "GEOMETRY", sizes):
            acceptance = linux.qualify_to_images(backup, output, record, toolchain)
            self.assertTrue(acceptance["allocated_bytes_and_metadata_verified"])
            self.assertFalse(acceptance["physical_device_written"])
            self.assertEqual((output / "userdata.img").read_bytes(), self.data("userdata"))
            self.assertEqual((output / "boot.img").read_bytes(), self.data("boot"))
            self.error("QUALIFICATION_NEW_DIRECTORY_REQUIRED", linux.qualify_to_images, backup, output, record, toolchain)
            capsule = qualification.make_capsule(record, acceptance)
            capsule_path = self.base / "capsule.json"
            capsule_path.write_bytes(canonical(capsule))
            with mock.patch.object(qualification, "CAPSULE_PATH", capsule_path), mock.patch.object(qualification, "implementation", return_value=record):
                result = qualification.admission(SimpleNamespace(mode="device"), parts=[{}], tools={})
                self.assertTrue(result["eligible"])
                changed = copy.deepcopy(record)
                changed["sources"]["backend/linux_workflow.py"] = "0" * 64
                self.assertEqual(result["basis"], "capsule")
                # 2026-11-13: a capsule of another build or phone no longer blocks; the build's own
                # admission decides (the model, the layout and the pinned tools), and refuses another layout.
                own = {"status": "qualified", "eligible": True, "capsule_id": "b" * 64, "basis": "built-in",
                       "blockers": [], "linux_boot_sha256": None}
                with mock.patch.object(qualification, "implementation", return_value=changed), \
                        mock.patch.object(qualification, "built_in_admission", return_value=own):
                    self.assertEqual(qualification.admission(SimpleNamespace(mode="device"), parts=[{}], tools={}), own)
                with mock.patch.object(qualification, "implementation", return_value=changed), \
                        mock.patch.object(qualification, "device_binding", side_effect=RecoveryError("GEOMETRY_MISMATCH", "other layout")):
                    refused = qualification.admission(SimpleNamespace(mode="device"), parts=[{}], tools={})
                self.assertFalse(refused["eligible"])
                self.assertEqual(refused["blockers"], ["GEOMETRY_MISMATCH"])
        self.assertFalse((output / "userdata.img").is_symlink())

    def _take_lock(self):
        with operation_lock(self.context):
            pass

    @unittest.skipUnless(shutil.which("mkfs.ext4") and shutil.which("e2fsck") and shutil.which("dumpe2fs"), "upstream e2fsprogs unavailable")
    def test_actual_mkfs_uses_owned_regular_fd_no_discard_and_clean_uuid(self):
        import partclone_image
        filename = self.base / "actual-owned-ext4.img"
        with filename.open("w+b", buffering=0) as target:
            target.truncate(64 * 1024 * 1024)
            part = {"label": "userdata", "path": str(filename), "size_bytes": target.tell(), "stamp": stamp(target.fileno())}
            part["size_bytes"] = 64 * 1024 * 1024
            tools = {"paths": {name: str(Path(shutil.which(name) or "/usr/bin/true").resolve()) for name in qualification.TOOL_NAMES},
                     "mkfs_config": "/etc/mke2fs.conf"}
            plan = {"new_filesystem_uuid": "dcd8aa55-7791-44b7-a042-b974f85e1201"}
            state = {}
            def guard(force=False):
                linux._check_target_fd(target, part, physical=False)
            with mock.patch.object(linux, "_compact", return_value=partclone_image), operation_lock(self.context) as lease:
                linux._format_userdata(target, part, tools, lease, plan, state, guard, None)
            self.assertTrue(state["readback_verified"])
            self.assertEqual(state["new_filesystem"]["uuid"], plan["new_filesystem_uuid"])
            self.assertFalse(state["secure_erasure"])


class QualificationTests(unittest.TestCase):
    def test_capsule_requires_completed_real_source_and_matching_implementation(self):
        record = {"schema": "a22x.linux.implementation.v1", "workflow": linux.WORKFLOW,
                  "binding": {"device": {"model": "SM-A226B", "identity": "emmc-cid-sha256:" + "1" * 64}}}
        acceptance = {"schema": "a22x.linux.acceptance.v1", "ok": True, "real_source_backup": True,
                      "restore_completed": True, "all_archives_verified_before_write": True,
                      "allocated_bytes_and_metadata_verified": True, "boot_raw_verified": True,
                      "physical_device_written": False, "target_kind": "owned-new-regular-files", "target_e2fsck_exit": 0,
                      "userdata_bytes": qualification.GEOMETRY["userdata"][1], "source_device": record["binding"]["device"],
                      "implementation_sha256": digest(record), "backup_manifest_sha256": "2" * 64,
                      "boot_raw_sha256": "3" * 64, "userdata_archive_sha256": "4" * 64,
                      "userdata_native_body_sha256": "5" * 64, "restored_native_body_sha256": "5" * 64}
        capsule = qualification.make_capsule(record, acceptance)
        self.assertEqual(capsule["linux_boot_sha256"], acceptance["boot_raw_sha256"])
        self.assertEqual(capsule["capsule_id"], digest({k: v for k, v in capsule.items() if k != "capsule_id"}))
        for key, bad in (("real_source_backup", False), ("target_e2fsck_exit", 1), ("target_e2fsck_exit", False),
                         ("restored_native_body_sha256", "6" * 64), ("implementation_sha256", "7" * 64),
                         ("physical_device_written", True), ("target_kind", "block-device")):
            with self.subTest(field=key):
                with self.assertRaises(RecoveryError):
                    qualification.make_capsule(record, dict(acceptance, **{key: bad}))

    def test_missing_capsule_cannot_enable_device_workflow(self):
        with mock.patch.object(qualification, "CAPSULE_PATH", Path("/does-not-exist/a22x.json")):
            result = qualification.admission(SimpleNamespace(mode="device"), parts=[{}], tools={})
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "unqualified")
        self.assertIsNone(result["capsule_id"])


class NativeWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("A22X_TEST_COMPACT_TOOLS"), "Explicit native Partclone tool mapping not supplied")
    def test_real_compact_backup_wipe_restore_owned_64mib_files(self):
        import partclone_image
        native = partclone_image.resolve_tools(json.loads(Path(os.environ["A22X_TEST_COMPACT_TOOLS"]).read_text()))
        mkfs = shutil.which("mkfs.ext4")
        if not mkfs:
            self.skipTest("Host e2fsprogs not installed")
        tools = {"paths": dict(native, **{"mkfs.ext4": str(Path(mkfs).resolve())}),
                 "mkfs_config": "/etc/mke2fs.conf", "manifest": None, "manifest_sha256": None}
        with tempfile.TemporaryDirectory(prefix="a22x-linux-native-") as temporary:
            base = Path(temporary)
            root = base / "owned-phone"
            create_fixture(root, userdata_bytes=64 * 1024 * 1024)
            config_path = root / "fixture.json"
            config = json.loads(config_path.read_text())
            config["storage_graph"].pop("253:0")
            config_path.write_bytes(canonical(config))
            tree = base / "source-tree"
            tree.mkdir()
            (tree / "linux-data.txt").write_text("Перевірка повного циклу Linux\n" * 4096)
            (tree / "link").symlink_to("linux-data.txt")
            os.link(tree / "linux-data.txt", tree / "hard-link")
            with (tree / "sparse.bin").open("wb") as output:
                output.write(b"start")
                output.seek(8 * 1024 * 1024)
                output.write(b"end")
            os.setxattr(tree / "linux-data.txt", "user.a22x", b"native metadata")
            userdata = root / "partitions/userdata.img"
            subprocess.run([mkfs, "-q", "-F", "-t", "ext4", "-b", "4096", "-d", str(tree),
                            "-E", "nodiscard,lazy_itable_init=0,lazy_journal_init=0", str(userdata)],
                           check=True, capture_output=True)
            context = FixtureContext(root)
            boot_before = (root / "partitions/boot.img").read_bytes()
            recovery_before = (root / "partitions/recovery.img").read_bytes()
            with mock.patch.object(qualification, "toolchain", return_value=tools):
                plan = engine.make_backup_plan(context, "linux", root / "storage/sd", "real-native", data_format="partclone-ext4")
                backup = engine.backup(context, plan, plan["confirmation_token"])
                self.assertLess(backup["archive_bytes"], 64 * 1024 * 1024)
                verified = engine.verify_backup(context, backup["backup_path"])
                original_uuid = verified["manifest"]["entries"][1]["source_probe"]["uuid"]
                wipe = engine.make_wipe_plan(context, backup["backup_path"])
                wiped = engine.wipe(context, wipe, wipe["confirmation_token"])
                self.assertNotEqual(wiped["receipt"]["partitions"]["userdata"]["new_filesystem"]["uuid"], original_uuid)
                restore = engine.make_restore_plan(context, backup["backup_path"])
                self.assertEqual(restore["boot_action"], "skip-identical")
                restored = engine.restore(context, restore, restore["confirmation_token"])
                self.assertEqual(restored["receipt"]["partitions"]["userdata"]["readback"]["source_probe"]["uuid"], original_uuid)
                self.assertFalse(restored["receipt"]["physical_device_written"])
                self.assertFalse(restored["auto_reboot"])
                self.assertTrue(restored["verified"])
            self.assertEqual((root / "partitions/boot.img").read_bytes(), boot_before)
            self.assertEqual((root / "partitions/recovery.img").read_bytes(), recovery_before)


if __name__ == "__main__":
    unittest.main()
