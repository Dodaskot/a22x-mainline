"""Compact offline ext4 images made and checked by upstream Partclone.

There is deliberately no target writer or CLI here. Callers admit and lock
sources/targets, enforce unmounted topology, and retain the operation lease.
Every child inherits the caller's explicit lease descriptors. Creation writes
only an already-open new regular archive, or an explicitly requested pipe.

The container reader understands the bounded Partclone 0002 envelope, not ext4
allocation metadata. libext2fs/partclone select used blocks; e2fsck checks the
filesystem and partclone.chkimg checks the native image's CRCs. Free blocks and
partition tail slack are not recorded and have unspecified contents on restore.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import os
import re
import select
import shutil
import stat
import struct
import subprocess
import threading
import time
import uuid
from pathlib import Path

from common import CHUNK, fail, open_regular_fd, require_hash, stamp

FORMAT = "partclone.ext4.v2"
ARCHIVE = "userdata.partclone.zst"
MAX_PARTITION_BYTES = 128 * 1024 ** 3
MAX_ARCHIVE_BYTES = 2 * MAX_PARTITION_BYTES
ENCODED_CHUNK = 4 * 1024 ** 2
HEADER_BYTES = 110
ZSTD_WINDOW_MIB = 128
DIAGNOSTIC_BYTES = 16 * 1024
DEFAULT_RESERVE = 64 * 1024 ** 2
TOOLS = ("partclone.ext4", "partclone.chkimg", "partclone.restore", "zstd",
         "e2fsck", "blkid", "dumpe2fs")


def _integer(value, field, low=1, high=MAX_PARTITION_BYTES):
    if type(value) is not int or not low <= value <= high:
        fail("COMPACT_SIZE_LIMIT", "Invalid bounded compact-image geometry", field=field, value=value)
    return value


def _fds(values):
    result = tuple(sorted(set(values)))
    for fd in result:
        if type(fd) is not int or fd < 0:
            fail("INVALID_INHERITED_FD", "Inherited operation descriptors must be open integers")
        os.fstat(fd)
    return result


def resolve_tools(tool_paths=None):
    result = {}
    for name in TOOLS:
        value = tool_paths.get(name) if tool_paths is not None else shutil.which(name)
        if not isinstance(value, (str, os.PathLike)) or not Path(value).is_absolute():
            fail("COMPACT_TOOLS_UNAVAILABLE", "A required compact-image tool is unavailable", tool=name)
        path = Path(value).resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            fail("COMPACT_TOOLS_UNAVAILABLE", "A required tool is not executable", tool=name)
        result[name] = str(path)
    return result


class _Child:
    """Drain diagnostics continuously while retaining only a bounded prefix."""
    def __init__(self, argv, *, inherited_fds=(), stdin=None, stdout=subprocess.PIPE):
        # Runtime/tool hashes must not be bypassed through loader injection or
        # e2fsprogs configuration inherited from an interactive maintenance shell.
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
        self.argv = argv
        self.process = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                                        pass_fds=_fds(inherited_fds), close_fds=True,
                                        env=env, bufsize=0)
        self.diagnostic = bytearray()
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        try:
            while True:
                data = self.process.stderr.read(8192)
                if not data:
                    return
                left = DIAGNOSTIC_BYTES - len(self.diagnostic)
                if left > 0:
                    self.diagnostic.extend(data[:left])
        except (OSError, ValueError):
            return

    def wait(self, code="COMPACT_TOOL_FAILED", timeout=60):
        try:
            rc = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            fail("COMPACT_TOOL_TIMEOUT", "A compact-image child did not finish", tool=Path(self.argv[0]).name)
        self.reader.join(timeout=2)
        if rc:
            fail(code, "An upstream compact-image tool returned an error",
                 tool=Path(self.argv[0]).name, exit_code=rc,
                 diagnostic=bytes(self.diagnostic).decode("utf-8", "replace"))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def _small_command(argv, *, inherited_fds=(), timeout=600, limit=64 * 1024, code="COMPACT_TOOL_FAILED",
                   include_stderr=False):
    child = _Child(argv, inherited_fds=inherited_fds)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("COMPACT_TOOL_TIMEOUT", "A filesystem probe exceeded its time limit", tool=Path(argv[0]).name)
            ready, _, _ = select.select([child.process.stdout], [], [], min(1, remaining))
            if not ready:
                continue
            data = child.process.stdout.read(8192)
            if not data:
                break
            if len(output) + len(data) > limit:
                fail("COMPACT_TOOL_OUTPUT_LIMIT", "Filesystem probe output exceeded its bounded buffer")
            output.extend(data)
        child.wait(code=code, timeout=min(60, max(1, int(deadline - time.monotonic()))))
        return bytes(output) + (bytes(child.diagnostic) if include_stderr else b"")
    finally:
        child.close()


def tool_fingerprints(tool_paths=None, inherited_fds=()):
    result = {}
    for name, path in resolve_tools(tool_paths).items():
        fd = open_regular_fd(Path(path))
        try:
            initial = stamp(fd)
            digest = hashlib.sha256()
            while data := os.read(fd, CHUNK):
                digest.update(data)
            if stamp(fd) != initial:
                fail("COMPACT_TOOL_CHANGED", "An executable changed while being identified", tool=name)
        finally:
            os.close(fd)
        flag = "-V" if name in {"e2fsck", "dumpe2fs"} else "--version"
        version = _small_command([path, flag], inherited_fds=inherited_fds, timeout=10, include_stderr=True)
        result[name] = {"path": path, "sha256": digest.hexdigest(),
                        "version": version.decode("utf-8", "replace").strip()[:512]}
    return result


def _source_geometry(fd, partition_bytes):
    _integer(partition_bytes, "partition_bytes")
    value = os.fstat(fd)
    if stat.S_ISREG(value.st_mode):
        actual = value.st_size
    elif stat.S_ISBLK(value.st_mode):
        device_number = f"{os.major(value.st_rdev)}:{os.minor(value.st_rdev)}"
        # Caller admission also checks stacking/topology. This independent check
        # rejects direct use of a mounted filesystem even outside the engine.
        mountinfo = Path("/proc/self/mountinfo").read_text()
        if any(len(fields) > 2 and fields[2] == device_number for fields in
               (line.split() for line in mountinfo.splitlines())):
            fail("SOURCE_MOUNTED", "Compact imaging requires an unmounted block filesystem")
        actual = struct.unpack("=Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
    else:
        fail("COMPACT_SOURCE_TYPE", "Only an admitted regular image or block source can be cloned")
    if actual != partition_bytes:
        fail("TARGET_SIZE_MISMATCH", "Pinned source and admitted partition size differ",
             expected_bytes=partition_bytes, actual_bytes=actual)
    if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
        fail("COMPACT_SOURCE_MODE", "The source descriptor must be readable")
    return stamp(fd)


def _progress(callback, phase, done=0, total=0, operation="backup"):
    if callback:
        callback({"event": "progress", "operation": operation, "phase": phase,
                  "partition": "userdata", "bytes_done": done, "bytes_total": total})


class _ProgressGate:
    def __init__(self, callback, phase, operation="backup"):
        self.callback, self.phase, self.operation = callback, phase, operation
        self.last = 0.0

    def report(self, done, total):
        now = time.monotonic()
        if now - self.last >= 0.5 or (total and done == total):
            _progress(self.callback, self.phase, done, total, self.operation)
            self.last = now


def probe_source(fd, partition_bytes, *, progress=None, tool_paths=None, inherited_fds=()):
    """Read-only upstream checks; the caller still owns mount/lease admission."""
    tools = resolve_tools(tool_paths)
    initial = _source_geometry(fd, partition_bytes)
    inherited = _fds((*inherited_fds, fd))
    source = f"/proc/self/fd/{fd}"
    _progress(progress, "check-ext4")
    exported = _small_command([tools["blkid"], "--probe", "--output", "export", source],
                              inherited_fds=inherited, code="EXT4_PROBE_FAILED").decode("utf-8", "strict")
    fields = dict(line.split("=", 1) for line in exported.splitlines() if "=" in line)
    if fields.get("TYPE") != "ext4":
        fail("COMPACT_EXT4_REQUIRED", "Compact Linux backup requires an offline ext4 filesystem")
    try:
        filesystem_uuid = str(uuid.UUID(fields["UUID"]))
    except (KeyError, ValueError):
        fail("EXT4_PROBE_FAILED", "The ext4 source did not provide a valid filesystem UUID")
    header = _small_command([tools["dumpe2fs"], "-h", source], inherited_fds=inherited,
                            code="EXT4_PROBE_FAILED").decode("utf-8", "strict")
    values = {key.strip(): value.strip() for key, value in
              (line.split(":", 1) for line in header.splitlines() if ":" in line)}
    try:
        block_size = int(values["Block size"])
        block_count = int(values["Block count"])
        free_blocks = int(values["Free blocks"])
    except (KeyError, ValueError):
        fail("EXT4_PROBE_FAILED", "The upstream filesystem probe omitted block geometry")
    _integer(block_size, "block_size", 1024, 65536)
    _integer(block_count, "block_count", 1, MAX_PARTITION_BYTES // 1024)
    _integer(free_blocks, "free_blocks", 0, block_count)
    if block_size & (block_size - 1) or block_size * block_count > partition_bytes:
        fail("EXT4_PROBE_FAILED", "Filesystem geometry does not fit the admitted partition")
    features = values.get("Filesystem features", "").split()
    if values.get("Filesystem state") != "clean" or "needs_recovery" in features:
        fail("EXT4_NOT_CLEAN", "Unmount and cleanly check ext4 before making a compact backup")
    if values.get("Filesystem UUID", "").lower() != filesystem_uuid:
        fail("EXT4_PROBE_FAILED", "The two upstream filesystem probes disagree on UUID")
    # -n opens read-only and answers no to repairs; every nonzero status is fatal.
    _small_command([tools["e2fsck"], "-f", "-n", source], inherited_fds=inherited,
                   code="EXT4_CHECK_FAILED", timeout=1800)
    if _source_geometry(fd, partition_bytes) != initial:
        fail("SOURCE_CHANGED", "The source changed during the offline ext4 check")
    return {"filesystem": "ext4", "uuid": filesystem_uuid, "partition_bytes": partition_bytes,
            "block_size": block_size, "block_count": block_count, "free_blocks": free_blocks,
            "filesystem_state": "clean", "features": features}


def parse_header(header, partition_bytes):
    """Validate only the documented Partclone 0002 envelope before allocation."""
    _integer(partition_bytes, "partition_bytes")
    if len(header) != HEADER_BYTES or header[:16] != b"partclone-image\0" or header[30:34] != b"0002":
        fail("PARTCLONE_HEADER_INVALID", "Only native Partclone image format 0002 is admitted")
    if header[34:36] != b"\xde\xc0":
        fail("PARTCLONE_HEADER_INVALID", "This recovery supports little-endian Partclone images")
    fs = header[36:52].split(b"\0", 1)[0]
    if fs != b"EXTFS":
        fail("PARTCLONE_HEADER_INVALID", "Only Partclone EXTFS images are admitted")
    fs_bytes, total, super_used, used, block_size = struct.unpack_from("<QQQQI", header, 52)
    feature_size, version, cpu_bits, mode, size, blocks, reseed, bitmap, _crc = struct.unpack_from("<IHHHHIBBI", header, 88)
    _integer(block_size, "block_size", 1024, 65536)
    _integer(total, "total_blocks", 1, partition_bytes // block_size)
    _integer(used, "used_blocks", 1, total)
    _integer(super_used, "used_blocks_superblock", 1, total)
    # CLI --checksum-mode=1 maps to native CSM_CRC32=0x20. The upstream
    # IMAGE_FORMATS.md table historically used the CLI value instead.
    if (block_size & (block_size - 1) or fs_bytes != total * block_size or fs_bytes > partition_bytes or
            feature_size != 18 or version != 2 or cpu_bits not in (32, 64) or
            (mode, size, blocks, reseed, bitmap) != (32, 4, 256, 1, 1)):
        fail("PARTCLONE_HEADER_INVALID", "Unsupported geometry or checksum policy in the native image")
    try:
        creator = header[16:30].split(b"\0", 1)[0].decode("ascii", "strict")
    except UnicodeDecodeError:
        fail("PARTCLONE_HEADER_INVALID", "The native creator version is not ASCII")
    if not re.fullmatch(r"[A-Za-z0-9.+_-]{1,14}", creator):
        fail("PARTCLONE_HEADER_INVALID", "The native creator version is malformed")
    bitmap_bytes = (total + 7) // 8
    image_bytes = HEADER_BYTES + bitmap_bytes + 4 + used * block_size + ((used + blocks - 1) // blocks) * size
    return {"image_version": "0002", "creator_version": creator,
            "endian": "little", "filesystem": "EXTFS", "filesystem_bytes": fs_bytes,
            "total_blocks": total, "used_blocks_superblock": super_used, "used_blocks": used,
            "block_size": block_size, "checksum_mode": mode, "checksum_size": size,
            "blocks_per_checksum": blocks, "reseed_checksum": reseed, "bitmap_mode": bitmap,
            "native_image_bytes": image_bytes}


class _NativeMeter:
    def __init__(self, partition_bytes):
        self.partition_bytes = partition_bytes
        self.count = 0
        self.prefix = bytearray()
        self.header = None
        self.full_hash = hashlib.sha256()
        self.body_hash = hashlib.sha256()

    def feed(self, data):
        old_count = self.count
        self.count += len(data)
        self.full_hash.update(data)
        if old_count < HEADER_BYTES:
            self.prefix.extend(data[:HEADER_BYTES - old_count])
            if len(self.prefix) == HEADER_BYTES:
                self.header = parse_header(bytes(self.prefix), self.partition_bytes)
        if self.count > HEADER_BYTES:
            self.body_hash.update(data[max(0, HEADER_BYTES - old_count):])
        limit = self.header["native_image_bytes"] if self.header else HEADER_BYTES
        if self.count > limit:
            fail("PARTCLONE_SIZE_MISMATCH", "The native image exceeds its bounded header geometry")

    def finish(self):
        if self.header is None or self.count != self.header["native_image_bytes"]:
            fail("PARTCLONE_SIZE_MISMATCH", "The native image ended before its declared geometry")
        return {"native_image_bytes": self.count, "native_image_sha256": self.full_hash.hexdigest(),
                "native_body_sha256": self.body_hash.hexdigest(), "partclone": self.header}


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if not 0 < count <= len(view):
            fail("ARCHIVE_WRITE_FAILED", "The archive sink stopped making write progress")
        view = view[count:]


def _clone_args(tools, fd):
    return [tools["partclone.ext4"], "--clone", "--source", f"/proc/self/fd/{fd}",
            "--output", "-", "--checksum-mode=1", "--blocks-per-checksum=256",
            "--buffer_size", str(CHUNK), "--logfile", "/dev/null", "--quiet"]


def _check_args(tools):
    # CHKIMG builds do not implement --quiet. Diagnostics are bounded in RAM.
    return [tools["partclone.chkimg"], "--source", "-", "--logfile", "/dev/null",
            "--buffer_size", str(CHUNK)]


def _capture(fd, partition_bytes, sink, *, progress, tools, inherited_fds, max_archive_bytes):
    initial = _source_geometry(fd, partition_bytes)
    probe = probe_source(fd, partition_bytes, progress=progress, tool_paths=tools,
                         inherited_fds=inherited_fds)
    maximum = MAX_ARCHIVE_BYTES if max_archive_bytes is None else _integer(max_archive_bytes, "max_archive_bytes", 1, MAX_ARCHIVE_BYTES)
    meter = _NativeMeter(partition_bytes)
    reporter = _ProgressGate(progress, "compact-compress")
    errors = []
    clone = compressed = worker = None
    archive_hash = hashlib.sha256()
    count = 0
    try:
        clone = _Child(_clone_args(tools, fd), inherited_fds=(*inherited_fds, fd))
        compressed = _Child([tools["zstd"], "-q", "-T1", "-3", "--stdout"],
                            inherited_fds=inherited_fds, stdin=subprocess.PIPE)

        def pump():
            try:
                while data := clone.process.stdout.read(CHUNK):
                    meter.feed(data)
                    _write_all(compressed.process.stdin.fileno(), data)
                    reporter.report(meter.count, meter.header["native_image_bytes"] if meter.header else 0)
                clone.wait()
                meter.finish()
            except BaseException as error:
                errors.append(error)
            finally:
                compressed.process.stdin.close()

        worker = threading.Thread(target=pump, daemon=True)
        worker.start()
        while data := compressed.process.stdout.read(CHUNK):
            if count + len(data) > maximum:
                fail("BACKUP_SPACE_LIMIT", "Compact archive exceeded its admitted output budget",
                     archive_bytes=count, maximum_bytes=maximum)
            sink(data)
            archive_hash.update(data)
            count += len(data)
        worker.join(timeout=60)
        if worker.is_alive():
            fail("COMPACT_TOOL_TIMEOUT", "The clone producer did not finish after compressor EOF")
        if errors:
            raise errors[0]
        compressed.wait(code="COMPRESSION_FAILED")
        if _source_geometry(fd, partition_bytes) != initial:
            fail("SOURCE_CHANGED", "The source changed while its compact image was created")
        result = meter.finish()
        if (result["partclone"]["block_size"] != probe["block_size"] or
                result["partclone"]["total_blocks"] != probe["block_count"]):
            fail("SOURCE_CHANGED", "Partclone and the filesystem probe disagree on geometry")
        return {"label": "userdata", "format": FORMAT, "archive": ARCHIVE, "compression": "zstd",
                "raw_bytes": partition_bytes, "raw_partition_bytes": partition_bytes,
                "source_probe": probe, **result, "archive_bytes": count,
                "archive_sha256": archive_hash.hexdigest()}
    finally:
        # Kill consumers first to release a producer blocked writing its pipe.
        if compressed is not None:
            compressed.close()
        if clone is not None:
            clone.close()
        if worker is not None:
            worker.join(timeout=10)


def estimate(fd, partition_bytes, *, progress=None, tool_paths=None, inherited_fds=(), max_archive_bytes=None):
    """Count an actual deterministic compression pass without storing its bytes."""
    return _capture(fd, partition_bytes, lambda _data: None, progress=progress,
                    tools=resolve_tools(tool_paths), inherited_fds=_fds(inherited_fds),
                    max_archive_bytes=max_archive_bytes)


def create(fd, output_fd, partition_bytes, *, progress=None, tool_paths=None, inherited_fds=(),
           max_archive_bytes=None, reserve_bytes=DEFAULT_RESERVE, stream_output=False):
    """Create archive bytes only; the caller verifies and atomically publishes."""
    destination = os.fstat(output_fd)
    source = os.fstat(fd)
    regular = stat.S_ISREG(destination.st_mode)
    if regular:
        if destination.st_size or destination.st_nlink != 1 or os.lseek(output_fd, 0, os.SEEK_CUR):
            fail("NEW_ARCHIVE_REQUIRED", "A new empty singly-linked archive descriptor is required")
        if (destination.st_dev, destination.st_ino) == (source.st_dev, source.st_ino):
            fail("ARCHIVE_SOURCE_ALIAS", "The archive must not alias its source")
    elif not (stream_output and (stat.S_ISFIFO(destination.st_mode) or stat.S_ISSOCK(destination.st_mode))):
        fail("ARCHIVE_TARGET_TYPE", "Archive output must be a new regular file or an explicitly selected pipe")
    if fcntl.fcntl(output_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
        fail("ARCHIVE_TARGET_MODE", "The archive descriptor is not writable")
    _integer(reserve_bytes, "reserve_bytes", 0, MAX_ARCHIVE_BYTES)

    def sink(data):
        if regular:
            current = os.fstat(output_fd)
            if (current.st_dev, current.st_ino, current.st_nlink) != (destination.st_dev, destination.st_ino, 1):
                fail("ARCHIVE_CHANGED", "The archive descriptor's identity changed")
            space = os.fstatvfs(output_fd)
            if space.f_bavail * space.f_frsize < reserve_bytes + len(data):
                fail("BACKUP_SPACE_LIMIT", "The archive filesystem's free-space reserve would be crossed")
        _write_all(output_fd, data)

    entry = _capture(fd, partition_bytes, sink, progress=progress, tools=resolve_tools(tool_paths),
                     inherited_fds=_fds(inherited_fds), max_archive_bytes=max_archive_bytes)
    if regular:
        os.fsync(output_fd)
        if os.fstat(output_fd).st_size != entry["archive_bytes"]:
            fail("ARCHIVE_SIZE_MISMATCH", "Stored archive size differs from bytes sent to its descriptor")
    return entry


def validate_entry(entry):
    if (not isinstance(entry, dict) or entry.get("label") != "userdata" or
            entry.get("format") != FORMAT or entry.get("archive") != ARCHIVE or
            entry.get("compression") != "zstd"):
        fail("INVALID_COMPACT_ENTRY", "Compact archives require the exact ext4 format and allowlisted filename")
    partition_bytes = _integer(entry.get("raw_partition_bytes"), "raw_partition_bytes")
    if entry.get("raw_bytes") != partition_bytes or type(entry.get("raw_bytes")) is not int:
        fail("INVALID_COMPACT_ENTRY", "Logical partition length fields must agree")
    _integer(entry.get("archive_bytes"), "archive_bytes", 1, MAX_ARCHIVE_BYTES)
    image_bytes = _integer(entry.get("native_image_bytes"), "native_image_bytes", HEADER_BYTES + 1, MAX_ARCHIVE_BYTES)
    for field in ("native_image_sha256", "native_body_sha256", "archive_sha256"):
        require_hash(entry.get(field), field)
    header = entry.get("partclone")
    if not isinstance(header, dict):
        fail("INVALID_COMPACT_ENTRY", "The native Partclone geometry is required")
    fixed = {"image_version": "0002", "endian": "little", "filesystem": "EXTFS",
             "checksum_mode": 32, "checksum_size": 4, "blocks_per_checksum": 256,
             "reseed_checksum": 1, "bitmap_mode": 1}
    if any(type(header.get(key)) is not type(value) or header.get(key) != value for key, value in fixed.items()):
        fail("INVALID_COMPACT_ENTRY", "Unsupported Partclone checksum or image policy")
    creator = header.get("creator_version")
    if not isinstance(creator, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,14}", creator):
        fail("INVALID_COMPACT_ENTRY", "Invalid native creator version")
    block_size = _integer(header.get("block_size"), "block_size", 1024, 65536)
    total = _integer(header.get("total_blocks"), "total_blocks", 1, partition_bytes // block_size)
    used = _integer(header.get("used_blocks"), "used_blocks", 1, total)
    _integer(header.get("used_blocks_superblock"), "used_blocks_superblock", 1, total)
    if block_size & (block_size - 1) or header.get("filesystem_bytes") != total * block_size:
        fail("INVALID_COMPACT_ENTRY", "Native filesystem geometry is inconsistent")
    expected = HEADER_BYTES + (total + 7) // 8 + 4 + used * block_size + ((used + 255) // 256) * 4
    if image_bytes != expected or header.get("native_image_bytes") != expected:
        fail("INVALID_COMPACT_ENTRY", "Native length does not match its used-block geometry")
    probe = entry.get("source_probe")
    if (not isinstance(probe, dict) or probe.get("filesystem") != "ext4" or
            probe.get("filesystem_state") != "clean" or probe.get("partition_bytes") != partition_bytes or
            probe.get("block_size") != block_size or probe.get("block_count") != total):
        fail("INVALID_COMPACT_ENTRY", "The offline ext4 probe and image geometry disagree")
    _integer(probe.get("free_blocks"), "free_blocks", 0, total)
    try:
        if str(uuid.UUID(probe.get("uuid"))) != probe.get("uuid"):
            raise ValueError("noncanonical UUID")
    except (ValueError, TypeError, AttributeError):
        fail("INVALID_COMPACT_ENTRY", "The ext4 UUID must be canonical")
    features = probe.get("features")
    if (not isinstance(features, list) or len(features) > 128 or
            any(not isinstance(feature, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", feature) for feature in features) or
            "needs_recovery" in features):
        fail("INVALID_COMPACT_ENTRY", "Invalid clean ext4 feature set")


def _pread_exact(fd, size, offset):
    pieces = bytearray()
    while len(pieces) < size:
        data = os.pread(fd, size - len(pieces), offset + len(pieces))
        if not data:
            fail("ARCHIVE_SIZE_MISMATCH", "Pinned archive ended before its declared length")
        pieces.extend(data)
    return bytes(pieces)


class VerifiedPartclone:
    """Whole-image validation followed by checked encoded chunks on every use.

    The immutable 4 MiB encoded-chunk index is derived from the pinned archive
    only after its complete SHA256, exact native geometry/hash and upstream
    chkimg all pass. A changed encoded buffer never reaches the decompressor.
    No decoded filesystem image is staged and no restore target is opened.
    """
    def __init__(self, directory, entry, *, target_bytes=None, tool_paths=None, inherited_fds=()):
        self.entry = copy.deepcopy(entry)
        validate_entry(self.entry)
        self.path = Path(directory) / self.entry["archive"]
        self.target_bytes = target_bytes
        if target_bytes is not None:
            _integer(target_bytes, "target_bytes")
            if target_bytes != self.entry["raw_partition_bytes"]:
                fail("TARGET_SIZE_MISMATCH", "Compact snapshot and admitted target size differ")
        self.tools = resolve_tools(tool_paths)
        self.inherited_fds = _fds(inherited_fds)
        self.source = None
        self.initial_stamp = None
        self.chunk_hashes = None
        self.verified = False

    def __enter__(self):
        fd = open_regular_fd(self.path)
        try:
            self.initial_stamp = stamp(fd)
            if self.initial_stamp["size"] != self.entry["archive_bytes"]:
                fail("ARCHIVE_SIZE_MISMATCH", "Compact archive is missing or truncated")
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("ARCHIVE_BUSY", "Another writer owns the compact archive")
            self.source = os.fdopen(fd, "rb", buffering=0)
        except BaseException:
            os.close(fd)
            raise
        return self

    def __exit__(self, *_):
        if self.source is not None:
            self.source.close()
        self.source = None
        self.chunk_hashes = None
        self.verified = False

    @property
    def index_bytes(self):
        return len(self.chunk_hashes) if self.chunk_hashes is not None else 0

    def _check_unchanged(self):
        if self.source is None or stamp(self.source.fileno()) != self.initial_stamp:
            fail("ARCHIVE_CHANGED", "Pinned compact archive changed during verification or restore")

    def _hash_archive(self):
        self._check_unchanged()
        expected_size = self.entry["archive_bytes"]
        digest = hashlib.sha256()
        index = bytearray()
        for offset in range(0, expected_size, ENCODED_CHUNK):
            data = _pread_exact(self.source.fileno(), min(ENCODED_CHUNK, expected_size - offset), offset)
            digest.update(data)
            index.extend(hashlib.sha256(data).digest())
        if os.pread(self.source.fileno(), 1, expected_size):
            fail("ARCHIVE_SIZE_MISMATCH", "Compact archive exceeds its declared length")
        if digest.hexdigest() != self.entry["archive_sha256"]:
            fail("ARCHIVE_HASH_MISMATCH", "Compact archive bytes do not match the snapshot")
        self._check_unchanged()
        return bytes(index)

    def _decoded_chunks(self, index):
        self._check_unchanged()
        expected_size = self.entry["archive_bytes"]
        if not isinstance(index, bytes) or len(index) != ((expected_size + ENCODED_CHUNK - 1) // ENCODED_CHUNK) * 32:
            fail("ARCHIVE_NOT_VERIFIED", "A complete immutable encoded-chunk index is required")
        decoder = _Child([self.tools["zstd"], "-q", "--decompress", "--stdout",
                          "-M" + str(ZSTD_WINDOW_MIB) + "MB"],
                         inherited_fds=self.inherited_fds, stdin=subprocess.PIPE)
        errors = []

        def feed():
            try:
                for slot, offset in enumerate(range(0, expected_size, ENCODED_CHUNK)):
                    self._check_unchanged()
                    data = _pread_exact(self.source.fileno(), min(ENCODED_CHUNK, expected_size - offset), offset)
                    if hashlib.sha256(data).digest() != index[slot * 32:(slot + 1) * 32]:
                        fail("RESTORE_CHUNK_MISMATCH", "An encoded chunk changed after verification; it was not decoded",
                             offset_bytes=offset, chunk_bytes=len(data))
                    self._check_unchanged()
                    _write_all(decoder.process.stdin.fileno(), data)
                self._check_unchanged()
            except BaseException as error:
                errors.append(error)
            finally:
                decoder.process.stdin.close()

        worker = threading.Thread(target=feed, daemon=True)
        meter = _NativeMeter(self.entry["raw_partition_bytes"])
        worker.start()
        try:
            while meter.count < self.entry["native_image_bytes"]:
                want = min(CHUNK, self.entry["native_image_bytes"] - meter.count)
                data = bytearray()
                while len(data) < want:
                    more = decoder.process.stdout.read(want - len(data))
                    if not more:
                        break
                    data.extend(more)
                if len(data) != want:
                    break
                data = bytes(data)
                meter.feed(data)
                if meter.header != self.entry["partclone"]:
                    fail("PARTCLONE_HEADER_MISMATCH", "Native header differs from the sealed snapshot geometry")
                self._check_unchanged()
                yield data
            if meter.count == self.entry["native_image_bytes"] and decoder.process.stdout.read(1):
                fail("PARTCLONE_SIZE_MISMATCH", "The decoded compact image has trailing data")
            worker.join(timeout=60)
            if worker.is_alive():
                fail("COMPACT_TOOL_TIMEOUT", "The archive feeder did not finish after decoder EOF")
            if errors:
                raise errors[0]
            decoder.wait(code="DECOMPRESSION_FAILED")
            native = meter.finish()
            if (native["native_image_sha256"] != self.entry["native_image_sha256"] or
                    native["native_body_sha256"] != self.entry["native_body_sha256"]):
                fail("NATIVE_HASH_MISMATCH", "Decoded native image does not match the snapshot")
            self._check_unchanged()
        finally:
            decoder.close()
            worker.join(timeout=10)

    def verify(self, progress=None, *, operation="verify", phase="verify", collect_chunks=False):
        self.verified = False
        self.chunk_hashes = None
        if collect_chunks and self.target_bytes is None:
            fail("TARGET_GEOMETRY_REQUIRED", "A restore chunk index needs an admitted target size")
        index = self._hash_archive()
        checker = _Child(_check_args(self.tools), inherited_fds=self.inherited_fds,
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        count = 0
        reporter = _ProgressGate(progress, phase, operation)
        try:
            try:
                with contextlib.closing(self._decoded_chunks(index)) as chunks:
                    for data in chunks:
                        _write_all(checker.process.stdin.fileno(), data)
                        count += len(data)
                        reporter.report(count, self.entry["native_image_bytes"])
            except BrokenPipeError:
                checker.wait(code="PARTCLONE_CHECK_FAILED")
                fail("PARTCLONE_CHECK_FAILED", "Native image checker stopped accepting the complete image")
            checker.process.stdin.close()
            checker.wait(code="PARTCLONE_CHECK_FAILED")
        finally:
            checker.close()
        self._check_unchanged()
        self.chunk_hashes = index if collect_chunks else None
        self.verified = True
        return dict(self.initial_stamp)

    def verified_chunks(self):
        if not self.verified or self.chunk_hashes is None:
            fail("ARCHIVE_NOT_VERIFIED", "Restore needs a complete target-bound native-image verification")
        with contextlib.closing(self._decoded_chunks(self.chunk_hashes)) as chunks:
            yield from chunks


def verify_target(fd, entry, progress=None, *, tool_paths=None, inherited_fds=()):
    """Read-only post-restore check of ext4 and every recorded used block."""
    validate_entry(entry)
    tools = resolve_tools(tool_paths)
    inherited = _fds(inherited_fds)
    initial = _source_geometry(fd, entry["raw_partition_bytes"])
    probe = probe_source(fd, entry["raw_partition_bytes"], progress=progress,
                         tool_paths=tools, inherited_fds=inherited)
    if probe != entry["source_probe"]:
        fail("RESTORE_READBACK_MISMATCH", "Restored ext4 UUID, geometry or clean metadata differs from the snapshot")
    meter = _NativeMeter(entry["raw_partition_bytes"])
    reporter = _ProgressGate(progress, "readback-used-blocks", "restore")
    child = _Child(_clone_args(tools, fd), inherited_fds=(*inherited, fd))
    try:
        while data := child.process.stdout.read(CHUNK):
            meter.feed(data)
            reporter.report(meter.count, entry["native_image_bytes"])
        child.wait(code="RESTORE_READBACK_FAILED")
        actual = meter.finish()
    finally:
        child.close()
    expected_header = {key: value for key, value in entry["partclone"].items() if key != "creator_version"}
    actual_header = {key: value for key, value in actual["partclone"].items() if key != "creator_version"}
    if actual_header != expected_header or actual["native_body_sha256"] != entry["native_body_sha256"]:
        fail("RESTORE_READBACK_MISMATCH", "Restored allocation map or used-block bytes differ from the snapshot")
    if _source_geometry(fd, entry["raw_partition_bytes"]) != initial:
        fail("TARGET_CHANGED", "Restored target changed during its independent used-block readback")
    return {"verified": True, "e2fsck_exit_code": 0, "source_probe": probe,
            "native_image_bytes": actual["native_image_bytes"],
            "native_body_sha256": actual["native_body_sha256"],
            "used_bytes_verified": actual["partclone"]["used_blocks"] * actual["partclone"]["block_size"],
            "unallocated_bytes_verified": False}
