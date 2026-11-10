"""Compact image checks; every writable object is an owned regular fixture.

Set A22X_TEST_COMPACT_TOOLS to an explicit tool-path JSON for isolated upstream
binaries. No device target, mount, losetup or live partition is used by this suite.
"""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import RecoveryError, CHUNK
import partclone_image as compact


def synthetic_header(*, total=4096, used=128, block=4096, mode=32):
    return struct.pack("<16s14s4sH16sQQQQIIHHHHIBBI", b"partclone-image\0", b"0.3.48",
                       b"0002", 0xC0DE, b"EXTFS", total * block, total, used, used,
                       block, 18, 2, 64, mode, 4, 256, 1, 1, 0)


def synthetic_entry():
    header = compact.parse_header(synthetic_header(), 16 * CHUNK)
    return {"label": "userdata", "format": compact.FORMAT, "archive": compact.ARCHIVE,
            "compression": "zstd", "raw_bytes": 16 * CHUNK, "raw_partition_bytes": 16 * CHUNK,
            "archive_bytes": 1024, "archive_sha256": "1" * 64,
            "native_image_bytes": header["native_image_bytes"], "native_image_sha256": "2" * 64,
            "native_body_sha256": "3" * 64, "partclone": header,
            "source_probe": {"filesystem": "ext4", "uuid": "6f218066-d0ee-4826-8104-82e153fe140b",
                             "partition_bytes": 16 * CHUNK, "block_size": 4096, "block_count": 4096,
                             "free_blocks": 3968, "filesystem_state": "clean", "features": ["extent"]}}


class CompactUnitTests(unittest.TestCase):
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RecoveryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_native_header_geometry_counts_only_used_data_and_checksums(self):
        header = compact.parse_header(synthetic_header(), 16 * CHUNK)
        self.assertEqual(header["native_image_bytes"], 110 + 512 + 4 + 128 * 4096 + 4)
        self.assertLess(header["native_image_bytes"], CHUNK)

    def test_native_header_rejects_checks_disabled_big_geometry_wrong_endian(self):
        invalid = [synthetic_header(mode=0), synthetic_header(total=10**12),
                   synthetic_header(used=4097)]
        endian = bytearray(synthetic_header()); endian[34:36] = b"\xc0\xde"; invalid.append(bytes(endian))
        for raw in invalid:
            with self.subTest(raw=raw[:36]):
                with self.assertRaises(RecoveryError):
                    compact.parse_header(raw, 16 * CHUNK)

    def test_native_meter_refuses_truncation_and_trailing_bytes(self):
        raw = synthetic_header()
        meter = compact._NativeMeter(16 * CHUNK)
        for byte in raw:
            meter.feed(bytes([byte]))
        self.assert_code("PARTCLONE_SIZE_MISMATCH", meter.finish)
        remaining = meter.header["native_image_bytes"] - len(raw)
        meter.feed(bytes(remaining))
        self.assertEqual(meter.finish()["native_image_bytes"], len(raw) + remaining)
        self.assert_code("PARTCLONE_SIZE_MISMATCH", meter.feed, b"x")

    def test_entry_rejects_paths_booleans_sizes_geometry_and_dirty_state(self):
        base = synthetic_entry()
        compact.validate_entry(base)
        for key, value in (("archive", "../userdata.partclone.zst"), ("raw_bytes", True),
                           ("raw_partition_bytes", 10**50), ("native_image_bytes", 5),
                           ("archive_sha256", "not a hash")):
            entry = copy.deepcopy(base); entry[key] = value
            with self.subTest(key=key), self.assertRaises(RecoveryError):
                compact.validate_entry(entry)
        entry = copy.deepcopy(base); entry["source_probe"]["filesystem_state"] = "not clean"
        self.assert_code("INVALID_COMPACT_ENTRY", compact.validate_entry, entry)

    def test_child_environment_does_not_accept_loader_or_fs_configuration_injection(self):
        code = "import json,os; print(json.dumps(dict(os.environ)))"
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/nonexistent/injected.so", "LD_LIBRARY_PATH": "/evil",
                                         "MKE2FS_CONFIG": "/evil/conf", "A22X_NOT_ALLOWED": "secret"}):
            result = json.loads(compact._small_command([sys.executable, "-c", code]))
        self.assertNotIn("LD_PRELOAD", result)
        self.assertNotIn("LD_LIBRARY_PATH", result)
        self.assertNotIn("MKE2FS_CONFIG", result)
        self.assertNotIn("A22X_NOT_ALLOWED", result)
        self.assertEqual(result["LC_ALL"], "C")

    def test_child_inherits_explicit_operation_descriptor(self):
        with tempfile.TemporaryFile() as lease:
            fd = lease.fileno()
            code = "import os; print(os.fstat(" + str(fd) + ").st_ino)"
            result = compact._small_command([sys.executable, "-c", code], inherited_fds=(fd,))
            self.assertEqual(int(result), os.fstat(fd).st_ino)

    def test_nonzero_tool_status_is_fatal_and_diagnostic_is_bounded(self):
        code = "import os; os.write(2,b'E'*100000); raise SystemExit(7)"
        error = self.assert_code("COMPACT_TOOL_FAILED", compact._small_command, [sys.executable, "-c", code])
        self.assertEqual(error.details["exit_code"], 7)
        self.assertLessEqual(len(error.details["diagnostic"]), compact.DIAGNOSTIC_BYTES)

    def test_probe_output_limit_kills_producer(self):
        self.assert_code("COMPACT_TOOL_OUTPUT_LIMIT", compact._small_command,
                         [sys.executable, "-c", "import os; os.write(1,b'A'*1000000)"], limit=512)

    def test_creation_refuses_existing_archive_hardlink_and_character_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.img"
            source_path.write_bytes(bytes(4096))
            output_path = Path(directory) / "output"
            output_path.write_bytes(b"existing")
            with source_path.open("rb") as source, output_path.open("r+b") as output:
                self.assert_code("NEW_ARCHIVE_REQUIRED", compact.create, source.fileno(), output.fileno(), 4096)
            output_path.write_bytes(b"")
            os.link(output_path, Path(directory) / "link")
            with source_path.open("rb") as source, output_path.open("r+b") as output:
                self.assert_code("NEW_ARCHIVE_REQUIRED", compact.create, source.fileno(), output.fileno(), 4096)
            with source_path.open("rb") as source, open(os.devnull, "wb") as output:
                self.assert_code("ARCHIVE_TARGET_TYPE", compact.create, source.fileno(), output.fileno(), 4096,
                                 stream_output=True)


class CompactNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tool_config = os.environ.get("A22X_TEST_COMPACT_TOOLS")
        try:
            cls.tools = compact.resolve_tools(json.loads(Path(tool_config).read_text()) if tool_config else None)
        except (RecoveryError, FileNotFoundError):
            raise unittest.SkipTest("Upstream Partclone test binaries have not been supplied")
        cls.mkfs = shutil.which("mke2fs")
        cls.debugfs = shutil.which("debugfs")
        if not cls.mkfs or not cls.debugfs:
            raise unittest.SkipTest("Fixture e2fsprogs unavailable")
        cls.workspace = tempfile.TemporaryDirectory(prefix="a22x-partclone-fixture-")
        cls.root = Path(cls.workspace.name)
        tree = cls.root / "tree"; tree.mkdir()
        text = tree / "дані.txt"; text.write_text("Перевірка Linux backup\n" * 16384)
        os.chmod(text, 0o640)
        os.link(text, tree / "hardlink")
        (tree / "relative-link").symlink_to("дані.txt")
        (tree / "absolute-link").symlink_to("/дані.txt")
        cls.xattr_set = False
        try:
            os.setxattr(text, "user.a22x-fixture", b"extended metadata\0\xff")
            acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in
                  [(1, 6, 0xffffffff), (2, 4, 54321), (4, 4, 0xffffffff), (16, 4, 0xffffffff), (32, 0, 0xffffffff)])
            os.setxattr(text, "system.posix_acl_access", acl)
            cls.xattr_set = True
        except OSError:
            pass
        sparse = tree / "sparse"
        with sparse.open("wb") as output:
            output.write(b"start"); output.seek(8 * CHUNK); output.write(b"finish")
        # Two encoded chunks are required for the changed-input regression.
        (tree / "random.bin").write_bytes(os.urandom(5 * CHUNK))
        cls.partition_bytes = 64 * CHUNK
        cls.source_path = cls.root / "source.img"
        with cls.source_path.open("wb") as output:
            output.truncate(cls.partition_bytes)
        subprocess.run([cls.mkfs, "-q", "-F", "-t", "ext4", "-U", "6f218066-d0ee-4826-8104-82e153fe140b",
                        "-d", str(tree), str(cls.source_path)], check=True, capture_output=True)
        subprocess.run([cls.debugfs, "-w", "-R", "mknod device c 1 3", str(cls.source_path)],
                       check=True, capture_output=True)
        # The container may have a one-UID user namespace. Set Linux ownership
        # inside the owned image, without depending on host chown mappings.
        for field in ("uid", "gid"):
            subprocess.run([cls.debugfs, "-w", "-R", "set_inode_field /дані.txt " + field + " 12345",
                            str(cls.source_path)], check=True, capture_output=True)
        cls.snapshot = cls.root / "snapshot"; cls.snapshot.mkdir()
        cls.archive_path = cls.snapshot / compact.ARCHIVE
        with cls.source_path.open("rb") as source, cls.archive_path.open("xb") as target:
            cls.entry = compact.create(source.fileno(), target.fileno(), cls.partition_bytes,
                                       tool_paths=cls.tools, reserve_bytes=0)
        cls.native = subprocess.run([cls.tools["zstd"], "-q", "-d", "-c", str(cls.archive_path)],
                                    check=True, capture_output=True).stdout

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "workspace"):
            cls.workspace.cleanup()

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RecoveryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def verifier(self, **kwargs):
        return compact.VerifiedPartclone(self.snapshot, self.entry, tool_paths=self.tools, **kwargs)

    def test_actual_estimate_equals_created_bytes_and_hashes(self):
        with self.source_path.open("rb") as source:
            measured = compact.estimate(source.fileno(), self.partition_bytes, tool_paths=self.tools)
        self.assertEqual(measured, self.entry)
        self.assertEqual(self.entry["archive_bytes"], self.archive_path.stat().st_size)
        self.assertLess(self.entry["native_image_bytes"], self.partition_bytes // 2)

    def test_full_native_crc_verification_and_checked_stream(self):
        with self.verifier(target_bytes=self.partition_bytes) as archive:
            archive.verify(collect_chunks=True)
            self.assertTrue(archive.verified)
            self.assertEqual(archive.index_bytes, ((self.entry["archive_bytes"] + compact.ENCODED_CHUNK - 1) // compact.ENCODED_CHUNK) * 32)
            self.assertEqual(b"".join(archive.verified_chunks()), self.native)

    def test_restore_to_existing_pinned_regular_fd_preserves_ext4_metadata(self):
        target_path = self.root / "restored.img"
        with target_path.open("xb") as output:
            for _ in range(64):
                output.write(b"Z" * CHUNK)
        with target_path.open("r+b", buffering=0) as target, self.verifier(target_bytes=self.partition_bytes) as archive:
            archive.verify(collect_chunks=True)
            child = compact._Child([self.tools["partclone.restore"], "--source", "-", "--overwrite",
                                    f"/proc/self/fd/{target.fileno()}", "--logfile", "/dev/null",
                                    "--buffer_size", str(CHUNK), "--quiet"],
                                   inherited_fds=(target.fileno(),), stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL)
            try:
                for block in archive.verified_chunks():
                    compact._write_all(child.process.stdin.fileno(), block)
                child.process.stdin.close(); child.wait()
            finally:
                child.close()
            os.fsync(target.fileno())
            self.assertEqual(os.fstat(target.fileno()).st_size, self.partition_bytes)
            result = compact.verify_target(target.fileno(), self.entry, tool_paths=self.tools)
            self.assertTrue(result["verified"])
            self.assertFalse(result["unallocated_bytes_verified"])
        for name in ("/дані.txt", "/hardlink", "/relative-link", "/absolute-link", "/sparse", "/device"):
            before = subprocess.run([self.debugfs, "-R", "stat " + name, str(self.source_path)], check=True, capture_output=True).stdout
            after = subprocess.run([self.debugfs, "-R", "stat " + name, str(target_path)], check=True, capture_output=True).stdout
            self.assertEqual(after, before, name)
        if self.xattr_set:
            before = subprocess.run([self.debugfs, "-R", "ea_list /дані.txt", str(self.source_path)], check=True, capture_output=True).stdout
            after = subprocess.run([self.debugfs, "-R", "ea_list /дані.txt", str(target_path)], check=True, capture_output=True).stdout
            self.assertEqual(after, before)
            self.assertIn(b"user.a22x-fixture", after)
            self.assertIn(b"system.posix_acl_access", after)
        self.assertNotEqual(hashlib.sha256(target_path.read_bytes()).digest(),
                            hashlib.sha256(self.source_path.read_bytes()).digest())

    def test_changed_encoded_chunk_never_reaches_decoder(self):
        with self.verifier(target_bytes=self.partition_bytes) as archive:
            archive.verify(collect_chunks=True)
            self.assertGreater(self.entry["archive_bytes"], compact.ENCODED_CHUNK)
            position = compact.ENCODED_CHUNK + 10
            with self.archive_path.open("r+b", buffering=0) as output:
                original = os.pread(output.fileno(), 1, position)
                os.pwrite(output.fileno(), bytes([original[0] ^ 0x80]), position)
                try:
                    prefix = bytearray()
                    with mock.patch.object(archive, "_check_unchanged"), self.assertRaises(RecoveryError) as raised:
                        for chunk in archive.verified_chunks():
                            prefix.extend(chunk)
                    self.assertEqual(raised.exception.code, "RESTORE_CHUNK_MISMATCH")
                    self.assertEqual(bytes(prefix), self.native[:len(prefix)])
                finally:
                    os.pwrite(output.fileno(), original, position)

    def test_upstream_crc_check_rejects_image_even_when_all_outer_hashes_are_updated(self):
        altered = bytearray(self.native)
        header = self.entry["partclone"]
        checksum = 110 + (header["total_blocks"] + 7) // 8 + 4 + header["block_size"] * 256
        altered[checksum] ^= 0x01
        archive_bytes = subprocess.run([self.tools["zstd"], "-q", "-T1", "-3", "-c"],
                                       input=bytes(altered), check=True, capture_output=True).stdout
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            (Path(directory) / compact.ARCHIVE).write_bytes(archive_bytes)
            entry = copy.deepcopy(self.entry)
            entry.update(archive_bytes=len(archive_bytes), archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
                         native_image_sha256=hashlib.sha256(altered).hexdigest(),
                         native_body_sha256=hashlib.sha256(altered[110:]).hexdigest())
            with compact.VerifiedPartclone(directory, entry, tool_paths=self.tools) as verifier:
                self.assert_code("PARTCLONE_CHECK_FAILED", verifier.verify)
                self.assertFalse(verifier.verified)

    def test_archive_wrong_size_and_target_geometry_are_refused_before_decode(self):
        self.assert_code("TARGET_SIZE_MISMATCH", self.verifier, target_bytes=self.partition_bytes - 4096)
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            (Path(directory) / compact.ARCHIVE).write_bytes(b"truncated")
            with self.assertRaises(RecoveryError) as raised:
                with compact.VerifiedPartclone(directory, self.entry, tool_paths=self.tools):
                    self.fail("must not open a truncated archive")
            self.assertEqual(raised.exception.code, "ARCHIVE_SIZE_MISMATCH")

    def test_output_budget_is_enforced_without_publishing_an_entry(self):
        output = self.root / "too-small.zst"
        with self.source_path.open("rb") as source, output.open("xb") as destination:
            self.assert_code("BACKUP_SPACE_LIMIT", compact.create, source.fileno(), destination.fileno(),
                             self.partition_bytes, tool_paths=self.tools, reserve_bytes=0, max_archive_bytes=1024)
        self.assertLessEqual(output.stat().st_size, 1024)

    def test_estimate_stops_at_its_budget(self):
        with self.source_path.open("rb") as source:
            self.assert_code("BACKUP_SPACE_LIMIT", compact.estimate, source.fileno(), self.partition_bytes,
                             tool_paths=self.tools, max_archive_bytes=1024)

    def test_clean_source_refuses_a_failing_readonly_e2fsck(self):
        fail_tool = self.root / "e2fsck-fails"
        fail_tool.write_text("#!/usr/bin/python3\nraise SystemExit(4)\n")
        fail_tool.chmod(0o755)
        tools = dict(self.tools, e2fsck=str(fail_tool))
        with self.source_path.open("rb") as source:
            self.assert_code("EXT4_CHECK_FAILED", compact.probe_source, source.fileno(), self.partition_bytes,
                             tool_paths=tools)

    def test_explicit_pipe_archive_stream_has_same_count_hash_and_no_target_mutation(self):
        read_fd, write_fd = os.pipe()
        received = bytearray()
        def reader():
            while block := os.read(read_fd, CHUNK):
                received.extend(block)
        worker = threading.Thread(target=reader)
        worker.start()
        try:
            with self.source_path.open("rb") as source:
                entry = compact.create(source.fileno(), write_fd, self.partition_bytes, tool_paths=self.tools,
                                       stream_output=True, max_archive_bytes=32 * CHUNK)
        finally:
            os.close(write_fd)
            worker.join(timeout=10)
            os.close(read_fd)
        self.assertFalse(worker.is_alive())
        self.assertEqual(entry, self.entry)
        self.assertEqual(hashlib.sha256(received).hexdigest(), entry["archive_sha256"])
        self.assertEqual(len(received), entry["archive_bytes"])

    def test_progress_cancellation_reaps_native_children_and_returns_no_entry(self):
        children = []
        original_popen = compact.subprocess.Popen
        def tracked_popen(*args, **kwargs):
            child = original_popen(*args, **kwargs); children.append(child); return child
        def cancel(event):
            if event["phase"] == "compact-compress":
                raise KeyboardInterrupt("fixture cancellation")
        with self.source_path.open("rb") as source, mock.patch.object(compact.subprocess, "Popen", tracked_popen):
            with self.assertRaises(KeyboardInterrupt):
                compact.estimate(source.fileno(), self.partition_bytes, tool_paths=self.tools, progress=cancel)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))


if __name__ == "__main__":
    unittest.main()
