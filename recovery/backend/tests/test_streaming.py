from __future__ import annotations

import contextlib
import errno
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import engine
import recoveryctl
import restore_support
import storage
import streaming
from common import CHUNK, RecoveryError, canonical, read_json_with_hash, write_json_new
from demo_fixture import create_fixture
from storage import FixtureContext


class TargetProxy:
    def __init__(self, source, write=None, flush=None):
        self.source = source
        self.write_hook = write
        self.flush_hook = flush

    def __getattr__(self, name):
        return getattr(self.source, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.source.__exit__(*args)

    def write(self, data):
        return self.write_hook(self.source, data) if self.write_hook else self.source.write(data)

    def flush(self):
        if self.flush_hook:
            self.flush_hook(self.source)
        else:
            self.source.flush()


class StreamingRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "fixture"
        create_fixture(self.root, userdata_bytes=2 * CHUNK)
        self.context = FixtureContext(self.root)
        self.repo = self.root / "storage/sd"
        self.original = self.contents()
        backup_plan = engine.make_backup_plan(self.context, "linux", self.repo, "snapshot", "gzip")
        self.snapshot = Path(engine.backup(self.context, backup_plan, backup_plan["confirmation_token"])["backup_path"])
        self.plan = self.prepare()

    def tearDown(self):
        self.temporary.cleanup()

    def path(self, label):
        return self.root / "partitions" / (label + ".img")

    def contents(self):
        return {label: self.path(label).read_bytes() for label in ("boot", "userdata", "metadata", "recovery")}

    def prepare(self):
        for label in ("boot", "userdata"):
            self.path(label).write_bytes(b"X" * self.path(label).stat().st_size)
        return engine.make_restore_plan(self.context, self.snapshot)

    def restore(self, progress=None):
        return engine.restore(self.context, self.plan, self.plan["confirmation_token"], progress)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RecoveryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def final_receipt(self):
        return json.loads((self.root / "results" / (self.plan["plan_id"] + ".json")).read_text())

    def after_preverify(self, action):
        original = restore_support.RestoreJournal.checkpoint

        def checkpoint(journal, phase, receipt):
            original(journal, phase, receipt)
            if phase == "started":
                action()
        return mock.patch.object(restore_support.RestoreJournal, "checkpoint", checkpoint)

    def target_proxy(self, **hooks):
        original = FixtureContext.open_partition

        def open_partition(context, part, *, write=False):
            target = original(context, part, write=write)
            return TargetProxy(target, **hooks) if write and part["label"] == "userdata" else target
        return mock.patch.object(FixtureContext, "open_partition", open_partition)

    def assert_prefix(self, receipt, count):
        state = receipt["partitions"]["userdata"]
        self.assertEqual(state["verified_prefix_bytes"], count)
        self.assertEqual(state["remaining_bytes"], 2 * CHUNK - count)
        self.assertFalse(state["readback_verified"])
        self.assertEqual(receipt["partitions"]["boot"]["verified_prefix_bytes"], 0)
        self.assertEqual(self.path("boot").read_bytes(), b"X" * len(self.original["boot"]))
        self.assertFalse(receipt["auto_reboot"])
        self.assertFalse(receipt["physical_device_written"])

    def test_no_decoded_staging_and_complete_readback_receipt(self):
        with mock.patch.object(FixtureContext, "staging_directory", side_effect=AssertionError("No raw staging")), \
             mock.patch("tempfile.TemporaryFile", side_effect=AssertionError("No temporary file for gzip restore")):
            result = self.restore()
        self.assertEqual(self.contents(), self.original)
        receipt = result["receipt"]
        self.assertEqual(receipt["decoded_staging_bytes"], 0)
        self.assertEqual(receipt["chunk_index_bytes"], 3 * 32)
        self.assertTrue(receipt["verify_each_chunk_before_write"])
        self.assertEqual(receipt["completed_partitions"], ["userdata", "boot"])
        for value in receipt["partitions"].values():
            self.assertTrue(value["readback_verified"])
            self.assertTrue(value["fsync_completed"])
            self.assertEqual(value["verified_prefix_bytes"], value["size_bytes"])
            self.assertEqual(value["remaining_bytes"], 0)
        self.assertEqual(self.final_receipt(), receipt)

    def test_intent_is_fsynced_and_readable_before_first_write(self):
        seen = []

        def progress(event):
            if event["phase"] == "write" and not seen:
                journal = self.root / "results" / (self.plan["plan_id"] + ".progress")
                initial = json.loads((journal / "0000-started.json").read_text())
                before = json.loads((journal / "0001-before-userdata.json").read_text())
                self.assertEqual(initial["status"], "started")
                self.assertEqual(initial["partitions"]["userdata"]["verified_prefix_bytes"], 0)
                self.assertEqual(before["active_partition"], "userdata")
                seen.append(True)
        receipt = self.restore(progress)["receipt"]
        self.assertEqual(seen, [True])
        self.assertEqual(len(list(Path(receipt["journal_path"]).glob("*.json"))), 6)

    def test_changed_archive_after_preverification_is_rejected_before_data_write(self):
        before = self.contents()

        def mutate():
            path = self.snapshot / "userdata.img.gz"
            with path.open("r+b") as source:
                source.seek(12)
                source.write(b"BAD")
        with self.after_preverify(mutate):
            error = self.assert_code("ARCHIVE_CHANGED", self.restore)
        self.assertEqual(self.contents(), before)
        self.assert_prefix(error.restore_receipt, 0)

    def test_chunk_hash_stops_valid_but_changed_gzip_even_without_stamp_detection(self):
        changed = bytearray(self.original["userdata"])
        changed[CHUNK + 17] ^= 0x40

        def mutate():
            (self.snapshot / "userdata.img.gz").write_bytes(gzip.compress(changed, compresslevel=1, mtime=0))
        # Exercise the chunk index independently of timestamp detection. The
        # complete first pass still checks the original archive and raw hashes.
        with self.after_preverify(mutate), mock.patch.object(streaming.VerifiedArchive, "_check_unchanged"):
            error = self.assert_code("RESTORE_CHUNK_MISMATCH", self.restore)
        self.assertEqual(error.details["offset_bytes"], CHUNK)
        self.assertEqual(self.path("userdata").read_bytes(), self.original["userdata"][:CHUNK] + b"X" * CHUNK)
        self.assert_prefix(error.restore_receipt, CHUNK)
        self.assertEqual(self.final_receipt(), error.restore_receipt)

    def test_replaced_archive_path_cannot_redirect_the_pinned_input(self):
        def replace():
            archive = self.snapshot / "userdata.img.gz"
            archive.rename(self.snapshot / "unlinked-original.gz")
            archive.write_bytes(gzip.compress(b"Z" * (2 * CHUNK), mtime=0))
        # Renaming may change inode ctime. Ignore that extra detection here to
        # prove that both passes use the pinned original descriptor, not a path.
        with self.after_preverify(replace), mock.patch.object(streaming.VerifiedArchive, "_check_unchanged"):
            self.restore()
        self.assertEqual(self.contents(), self.original)

    def test_source_change_after_one_write_reports_verified_prefix_and_stops_boot(self):
        def progress(event):
            if event["phase"] == "write" and event["partition"] == "userdata":
                with (self.snapshot / "userdata.img.gz").open("r+b") as archive:
                    archive.seek(15)
                    archive.write(b"bad")
        error = self.assert_code("ARCHIVE_CHANGED", self.restore, progress)
        self.assert_prefix(error.restore_receipt, CHUNK)
        self.assertEqual(self.path("userdata").read_bytes(), self.original["userdata"][:CHUNK] + b"X" * CHUNK)

    def test_target_shrink_stops_without_extending_it_again(self):
        def progress(event):
            if event["phase"] == "write" and event["partition"] == "userdata":
                with self.path("userdata").open("r+b") as target:
                    target.truncate(CHUNK + 512)
        error = self.assert_code("TARGET_SIZE_CHANGED", self.restore, progress)
        self.assertEqual(self.path("userdata").stat().st_size, CHUNK + 512)
        self.assert_prefix(error.restore_receipt, CHUNK)

    def test_target_path_swap_cannot_redirect_remaining_writes(self):
        def progress(event):
            if event["phase"] == "write" and event["partition"] == "userdata":
                self.path("userdata").rename(self.root / "original-target.img")
                self.path("userdata").write_bytes(b"Y" * (2 * CHUNK))
        error = self.assert_code("PARTITION_CHANGED", self.restore, progress)
        self.assertEqual(self.path("userdata").read_bytes(), b"Y" * (2 * CHUNK))
        self.assert_prefix(error.restore_receipt, CHUNK)

    def test_short_write_then_io_failure_reports_the_actual_accepted_prefix(self):
        calls = []

        def write(target, data):
            if not calls:
                calls.append(True)
                return target.write(data[:17])
            raise OSError(errno.EIO, "injected target error")
        with self.target_proxy(write=write), self.assertRaises(OSError) as caught:
            self.restore()
        receipt = caught.exception.restore_receipt
        self.assert_prefix(receipt, 17)
        self.assertEqual(self.path("userdata").read_bytes(), self.original["userdata"][:17] + b"X" * (2 * CHUNK - 17))
        self.assertEqual(self.final_receipt(), receipt)

    def test_positive_short_writes_complete_with_exact_size_and_readback(self):
        with self.target_proxy(write=lambda target, data: target.write(data[:31751])):
            self.restore()
        self.assertEqual(self.contents(), self.original)

    def test_zero_write_stops_and_never_enters_an_infinite_retry(self):
        before = self.contents()
        with self.target_proxy(write=lambda *_: 0):
            error = self.assert_code("TARGET_SHORT_WRITE", self.restore)
        self.assert_prefix(error.restore_receipt, 0)
        self.assertEqual(self.contents(), before)

    def test_readback_corruption_prevents_boot_write(self):
        def corrupt(target):
            target.flush()
            os.pwrite(target.fileno(), b"not-the-snapshot", 4096)
        with self.target_proxy(flush=corrupt):
            error = self.assert_code("RESTORE_READBACK_MISMATCH", self.restore)
        self.assert_prefix(error.restore_receipt, 2 * CHUNK)
        self.assertTrue(error.restore_receipt["partitions"]["userdata"]["fsync_completed"])
        self.assertEqual(error.restore_receipt["completed_partitions"], [])

    def test_interrupt_reports_prefix_and_does_not_start_boot(self):
        def progress(event):
            if event["phase"] == "write":
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.restore(progress)
        self.assert_prefix(caught.exception.restore_receipt, CHUNK)
        self.assertEqual(self.final_receipt(), caught.exception.restore_receipt)

    def test_journal_failure_before_first_write_leaves_all_targets_unchanged(self):
        before = self.contents()
        with mock.patch.object(restore_support, "write_json_new", side_effect=OSError(errno.ENOSPC, "injected journal full")), \
             self.assertRaises(OSError) as caught:
            self.restore()
        self.assertEqual(self.contents(), before)
        self.assertFalse(caught.exception.restore_receipt["partial_restore_possible"])
        self.assertIn("failure_receipt_error", caught.exception.restore_receipt)

    def test_existing_journal_for_same_plan_prevents_any_write(self):
        before = self.contents()
        results = self.context.result_directory()
        (results / (self.plan["plan_id"] + ".progress")).mkdir()
        self.assert_code("RESTORE_ALREADY_STARTED", self.restore)
        self.assertEqual(self.contents(), before)

    def test_hostile_raw_size_is_rejected_without_decoding_or_allocating_index(self):
        manifest_path = self.snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][1]["raw_bytes"] = 10 ** 100
        manifest_path.write_bytes(canonical(manifest) + b"\n")
        complete_path = self.snapshot / "COMPLETE.json"
        complete = json.loads(complete_path.read_text())
        complete["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        complete_path.write_bytes(canonical(complete) + b"\n")
        with mock.patch.object(streaming.VerifiedArchive, "__enter__", side_effect=AssertionError("Must reject before opening")):
            self.assert_code("RESTORE_SIZE_LIMIT", engine.verify_backup, self.context, self.snapshot)

    def test_index_requires_exact_admitted_target_geometry(self):
        manifest = json.loads((self.snapshot / "manifest.json").read_text())
        entry = manifest["entries"][1]
        self.assert_code("TARGET_SIZE_MISMATCH", streaming.VerifiedArchive, self.snapshot, entry, target_bytes=CHUNK)
        with streaming.VerifiedArchive(self.snapshot, entry) as archive:
            self.assert_code("TARGET_GEOMETRY_REQUIRED", archive.verify, collect_chunks=True)
        self.assertEqual(streaming.index_bytes_for(53_460_598_784), 1_631_488)

    def test_cli_io_and_interrupt_errors_keep_failure_receipt(self):
        for index, (error, expected_code) in enumerate(((OSError(errno.EIO, "progress I/O failed"), 3), (KeyboardInterrupt(), 130))):
            with self.subTest(code=expected_code):
                if index:
                    self.plan = self.prepare()
                plan_path = self.root / ("cli-plan-" + str(index) + ".json")
                write_json_new(plan_path, self.plan)

                def progress(event):
                    if event["phase"] == "write":
                        raise error
                output = io.StringIO()
                with mock.patch.object(recoveryctl, "progress", progress), contextlib.redirect_stdout(output):
                    code = recoveryctl.main(["--fixture-root", str(self.root), "restore", "--plan", str(plan_path),
                                             "--confirm", self.plan["confirmation_token"]])
                self.assertEqual(code, expected_code)
                result = json.loads(output.getvalue())
                self.assert_prefix(result["error"]["details"]["receipt"], CHUNK)


class ManifestAndLockTests(unittest.TestCase):
    def test_json_hash_and_parse_refer_to_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            raw = b'{"key": [1,2,3]}\n'
            path.write_bytes(raw)
            value, digest = read_json_with_hash(path)
            self.assertEqual(value, {"key": [1, 2, 3]})
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_fixture_ignores_inherited_device_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            context = type("FixtureLock", (), {"lock_path": Path(directory) / "lock", "mode": "fixture"})()
            with mock.patch.dict(os.environ, {"A22X_RECOVERY_LOCK_FD": "invalid"}):
                with storage.operation_lock(context):
                    with self.assertRaises(RecoveryError) as caught:
                        with storage.operation_lock(context):
                            pass
            self.assertEqual(caught.exception.code, "OPERATION_BUSY")

    def test_device_borrows_valid_lease_and_keeps_parent_locked(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            context = type("DeviceLock", (), {"lock_path": Path(directory) / "lock", "mode": "device"})()
            fd = os.open(context.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            contender = os.open(context.lock_path, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with mock.patch.dict(os.environ, {"A22X_RECOVERY_LOCK_FD": str(fd)}):
                    with storage.operation_lock(context):
                        pass
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(contender)
                os.close(fd)

    def test_device_contention_is_backend_operation_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            context = type("DeviceLock", (), {"lock_path": Path(directory) / "lock", "mode": "device"})()
            with mock.patch.dict(os.environ, {}, clear=True), storage.operation_lock(context):
                with self.assertRaises(RecoveryError) as caught:
                    with storage.operation_lock(context):
                        pass
            self.assertEqual(caught.exception.code, "OPERATION_BUSY")


class MemoryLimitTests(unittest.TestCase):
    def test_256_mib_real_restore_with_64_mib_address_space(self):
        script = BACKEND / "tests" / "streaming_smoke.py"
        result = subprocess.run([sys.executable, str(script), "--raw-mib", "256", "--address-space-mib", "64"],
                                text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["decoded_staging_bytes"], 0)
        self.assertEqual(receipt["userdata_bytes"], 256 * CHUNK)
        self.assertEqual(receipt["index_bytes"], 257 * 32)
        self.assertEqual(receipt["peak_rss_source"], "/proc/self/status:VmHWM")
        self.assertLess(receipt["peak_rss_kib"], 64 * 1024)


if __name__ == "__main__":
    unittest.main()
