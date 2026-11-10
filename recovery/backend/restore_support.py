"""Restore journals and a writer restricted to already-pinned regular files.

No function in this module opens a block device. The caller must also preserve
the engine's fixture-only admission boundary.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import stat

from common import CHUNK, fail, fsync_directory, stamp, write_json_new


class RestoreJournal:
    """Append-only checkpoints; an earlier attempt is never overwritten."""
    def __init__(self, context, plan_id):
        results = context.result_directory()
        self.final_path = results / (plan_id + ".json")
        self.path = results / (plan_id + ".progress")
        if os.path.lexists(self.final_path) or os.path.lexists(self.path):
            fail("RESTORE_ALREADY_STARTED", "This plan already has a restore journal; create a fresh plan")
        self.path.mkdir(mode=0o700)
        fsync_directory(results)
        self.sequence = 0

    def checkpoint(self, phase, receipt):
        write_json_new(self.path / (f"{self.sequence:04d}-" + phase + ".json"),
                       {**receipt, "checkpoint_phase": phase, "checkpoint_sequence": self.sequence})
        self.sequence += 1

    def finish(self, receipt):
        write_json_new(self.final_path, receipt)


def check_fixture_target(target, part):
    """Keep the pinned regular-file target and exact size through every write."""
    st = os.fstat(target.fileno())
    named = os.stat(part["path"], follow_symlinks=False)
    original = part["stamp"]
    if (not stat.S_ISREG(st.st_mode) or not stat.S_ISREG(named.st_mode)
            or (st.st_dev, st.st_ino, st.st_rdev) !=
            (original["device"], original["inode"], original["rdev"])
            or (named.st_dev, named.st_ino) != (st.st_dev, st.st_ino)):
        fail("PARTITION_CHANGED", "Restore target identity changed", label=part["label"])
    if st.st_size != part["size_bytes"]:
        fail("TARGET_SIZE_CHANGED", "Restore target no longer has its admitted size", label=part["label"])


def write_verified_partition(archive, target, part, state, progress=None):
    """Verify input chunks before writing, enforce bounds, fsync and read back."""
    label = part["label"]
    check_fixture_target(target, part)
    if stamp(target.fileno()) != part["stamp"]:
        fail("PARTITION_CHANGED", "Target changed before this partition's first write", label=label)
    target.seek(0)
    count = 0
    with contextlib.closing(archive.verified_chunks()) as chunks:
        for block in chunks:
            if count + len(block) > part["size_bytes"]:
                fail("TARGET_BOUNDS", "Verified write would exceed the admitted partition", label=label)
            view = memoryview(block)
            while view:
                check_fixture_target(target, part)
                if target.tell() != count:
                    fail("TARGET_OFFSET_CHANGED", "Restore target offset changed unexpectedly", label=label)
                written = target.write(view)
                if not isinstance(written, int) or not 0 < written <= len(view):
                    fail("TARGET_SHORT_WRITE", "Target write made no valid progress", label=label)
                count += written
                # Count successful write return values, including short writes.
                # A failing/in-flight syscall may have changed more bytes; the
                # receipt explicitly does not claim the remainder is untouched.
                state["verified_prefix_bytes"] = count
                state["remaining_bytes"] = part["size_bytes"] - count
                view = view[written:]
            if progress:
                progress({"event": "progress", "operation": "restore", "phase": "write", "partition": label,
                          "bytes_done": count, "bytes_total": part["size_bytes"]})
    if count != part["size_bytes"]:
        fail("TARGET_SHORT_WRITE", "Restore did not write the complete admitted target", label=label)
    check_fixture_target(target, part)
    target.flush()
    os.fsync(target.fileno())
    state["fsync_completed"] = True
    before_readback = stamp(target.fileno())
    target.seek(0)
    actual = hashlib.sha256()
    count = 0
    while count < part["size_bytes"]:
        block = target.read(min(CHUNK, part["size_bytes"] - count))
        if not block:
            fail("RESTORE_READBACK_MISMATCH", "Target ended during independent readback", label=label)
        count += len(block)
        actual.update(block)
        if progress:
            progress({"event": "progress", "operation": "restore", "phase": "readback", "partition": label,
                      "bytes_done": count, "bytes_total": part["size_bytes"]})
    check_fixture_target(target, part)
    if target.read(1) or stamp(target.fileno()) != before_readback:
        fail("PARTITION_CHANGED", "Target changed during readback", label=label)
    if actual.hexdigest() != archive.entry["raw_sha256"]:
        fail("RESTORE_READBACK_MISMATCH", "Target readback differs from verified snapshot", label=label)
    state["readback_verified"] = True
    state["readback_sha256"] = actual.hexdigest()
