"""Pinned, bounded-memory archive verification for the v1 backup format.

This module opens only regular archive files and never opens a restore target.
The optional per-chunk index becomes usable only after full archive/raw-hash
verification. A second decoding pass checks each immutable chunk against that
index before exposing it to the caller. No decoded image is staged on disk.
"""
from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import os
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

from common import CHUNK, LABELS, fail, open_regular_fd, require_hash, stamp

# Match the production adapter's admitted geometry ceiling. A manifest cannot
# allocate an unbounded chunk index, even in a malicious synthetic fixture.
MAX_PARTITION_BYTES = 128 * 1024 ** 3
MAX_ARCHIVE_BYTES = 2 * MAX_PARTITION_BYTES
HASH_BYTES = 32
ZSTD_WINDOW_MIB = 128


def index_bytes_for(size):
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_PARTITION_BYTES:
        fail("RESTORE_SIZE_LIMIT", "Partition exceeds the bounded streaming geometry limit",
             size_bytes=size, maximum_bytes=MAX_PARTITION_BYTES)
    return ((size + CHUNK - 1) // CHUNK) * HASH_BYTES


def validate_entry(entry):
    label, compression = entry.get("label"), entry.get("compression")
    suffix = {"gzip": ".img.gz", "zstd": ".img.zst"}.get(compression)
    if label not in LABELS or suffix is None or entry.get("archive") != label + suffix:
        fail("INVALID_ARCHIVE_PATH", "Archive must have its exact allowlisted partition filename")
    for field, maximum in (("raw_bytes", MAX_PARTITION_BYTES), ("archive_bytes", MAX_ARCHIVE_BYTES)):
        size = entry.get(field)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            fail("INVALID_MANIFEST", "Archive sizes must be positive integers", field=field)
        if size > maximum:
            fail("RESTORE_SIZE_LIMIT", "Archive size exceeds the supported geometry limit",
                 field=field, size_bytes=size, maximum_bytes=maximum)
    require_hash(entry.get("raw_sha256"), "raw_sha256")
    require_hash(entry.get("archive_sha256"), "archive_sha256")


class VerifiedArchive:
    def __init__(self, directory, entry, *, target_bytes=None):
        self.entry = dict(entry)
        validate_entry(self.entry)
        self.path = Path(directory) / self.entry["archive"]
        self.target_bytes = target_bytes
        if target_bytes is not None:
            index_bytes_for(target_bytes)
            if target_bytes != self.entry["raw_bytes"]:
                fail("TARGET_SIZE_MISMATCH", "Snapshot and admitted target geometry differ",
                     label=self.entry["label"])
        self.source = None
        self.initial_stamp = None
        self.chunk_hashes = None
        self.verified = False

    def __enter__(self):
        fd = open_regular_fd(self.path)
        try:
            self.initial_stamp = stamp(fd)
            if self.initial_stamp["size"] != self.entry["archive_bytes"]:
                fail("ARCHIVE_SIZE_MISMATCH", "Archive is missing or truncated", label=self.entry["label"])
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("ARCHIVE_BUSY", "Another writer owns the archive", label=self.entry["label"])
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
            fail("ARCHIVE_CHANGED", "Pinned archive changed during verification or restore",
                 label=self.entry["label"])

    def _hash_archive(self):
        self._check_unchanged()
        self.source.seek(0)
        hasher = hashlib.sha256()
        count = 0
        # Never chase an unbounded growing archive to EOF.
        while count < self.entry["archive_bytes"]:
            block = self.source.read(min(CHUNK, self.entry["archive_bytes"] - count))
            if not block:
                fail("ARCHIVE_SIZE_MISMATCH", "Archive ended before its declared length", label=self.entry["label"])
            hasher.update(block)
            count += len(block)
        if self.source.read(1):
            fail("ARCHIVE_SIZE_MISMATCH", "Archive exceeds its declared length", label=self.entry["label"])
        if hasher.hexdigest() != self.entry["archive_sha256"]:
            fail("ARCHIVE_HASH_MISMATCH", "Archive bytes do not match the snapshot", label=self.entry["label"])
        self._check_unchanged()

    @contextlib.contextmanager
    def _decoder(self):
        self._check_unchanged()
        self.source.seek(0)
        process = errors = decoder = None
        try:
            if self.entry["compression"] == "gzip":
                decoder = gzip.GzipFile(fileobj=self.source, mode="rb")
            else:
                if not shutil.which("zstd"):
                    fail("ZSTD_UNAVAILABLE", "zstd is required for this snapshot")
                errors = tempfile.TemporaryFile()
                process = subprocess.Popen(
                    ["zstd", "-q", "--decompress", "--stdout", "-M" + str(ZSTD_WINDOW_MIB) + "MB"],
                    stdin=self.source, stdout=subprocess.PIPE, stderr=errors)
                decoder = process.stdout
            yield decoder
            if process:
                try:
                    code = process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    fail("DECOMPRESSION_FAILED", "zstd did not exit after the bounded image was decoded",
                         label=self.entry["label"])
                if code:
                    errors.seek(0)
                    fail("DECOMPRESSION_FAILED", errors.read(4096).decode(errors="replace"),
                         label=self.entry["label"])
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            fail("DECOMPRESSION_FAILED", str(exc), label=self.entry["label"])
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            if decoder is not None:
                decoder.close()
            if errors is not None:
                errors.close()

    def _decoded_chunks(self):
        """Yield canonical 1 MiB chunks, then validate EOF, raw hash and stamp."""
        count = 0
        raw_hash = hashlib.sha256()
        with self._decoder() as decoder:
            while count < self.entry["raw_bytes"]:
                self._check_unchanged()
                want = min(CHUNK, self.entry["raw_bytes"] - count)
                block = decoder.read(want)
                # Pipes may return a short read even when more bytes will follow.
                if len(block) < want:
                    accumulated = bytearray(block)
                    while len(accumulated) < want:
                        more = decoder.read(want - len(accumulated))
                        if not more:
                            break
                        accumulated.extend(more)
                    if len(accumulated) != want:
                        # Let the zstd exit status report decoder failures before
                        # classifying an otherwise valid but short raw image.
                        break
                    block = bytes(accumulated)
                self._check_unchanged()
                raw_hash.update(block)
                count += len(block)
                yield block
            if count == self.entry["raw_bytes"] and decoder.read(1):
                fail("DECODE_SIZE_MISMATCH", "Decoded archive exceeds declared partition size",
                     label=self.entry["label"])
        if count != self.entry["raw_bytes"] or raw_hash.hexdigest() != self.entry["raw_sha256"]:
            fail("RAW_HASH_MISMATCH", "Decoded partition does not match the snapshot", label=self.entry["label"])
        self._check_unchanged()

    def verify(self, progress=None, *, operation="verify", phase="verify", collect_chunks=False):
        self.verified = False
        self.chunk_hashes = None
        if collect_chunks and self.target_bytes is None:
            fail("TARGET_GEOMETRY_REQUIRED", "A chunk index requires admitted target geometry")
        self._hash_archive()
        index = bytearray(index_bytes_for(self.target_bytes)) if collect_chunks else None
        count = 0
        slot = 0
        # Closing the generator explicitly releases a zstd child on interruption.
        with contextlib.closing(self._decoded_chunks()) as chunks:
            for block in chunks:
                if index is not None:
                    index[slot:slot + HASH_BYTES] = hashlib.sha256(block).digest()
                    slot += HASH_BYTES
                count += len(block)
                if progress:
                    progress({"event": "progress", "operation": operation, "phase": phase,
                              "partition": self.entry["label"], "bytes_done": count,
                              "bytes_total": self.entry["raw_bytes"]})
        # Immutable and trusted only after the whole image, trailer and archive
        # identity passed. No partially verified index can reach the writer.
        self.chunk_hashes = bytes(index) if index is not None else None
        self.verified = True
        return dict(self.initial_stamp)

    def verified_chunks(self):
        """Expose each buffer only after it matches the fully verified first pass."""
        if not self.verified or self.chunk_hashes is None:
            fail("ARCHIVE_NOT_VERIFIED", "Restore requires a complete target-bound verification index")
        offset = 0
        slot = 0
        with contextlib.closing(self._decoded_chunks()) as chunks:
            for block in chunks:
                expected = self.chunk_hashes[slot:slot + HASH_BYTES]
                if hashlib.sha256(block).digest() != expected:
                    fail("RESTORE_CHUNK_MISMATCH", "Decoded chunk changed after full verification; it was not written",
                         label=self.entry["label"], offset_bytes=offset, chunk_bytes=len(block))
                yield block
                offset += len(block)
                slot += HASH_BYTES
