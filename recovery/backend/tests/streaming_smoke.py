#!/usr/bin/env python3
"""Real bounded-memory gzip restore in a fresh, automatically removed fixture.

Runs on a host or in recovery. It never accepts a target pathname or opens a
block device; TMPDIR selects where the owned temporary regular files live.
"""
from __future__ import annotations

import argparse
import gzip
import json
import resource
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import restore_support
import streaming
from common import CHUNK, RecoveryError, canonical, hash_file
from demo_fixture import create_fixture
from storage import FixtureContext


def late_chunk_fault(context, snapshot):
    """Test-local fault injection; never relax the production writer boundary."""
    if context.mode != "fixture":
        raise RuntimeError("Fault injection is confined to this newly created fixture")
    archive_path = Path(snapshot) / "userdata.img.gz"
    changed_path = Path(snapshot) / "test-fault.gz"
    # Build another valid gzip stream without holding a raw image in memory.
    with gzip.open(archive_path, "rb") as source, changed_path.open("xb") as output:
        with gzip.GzipFile(filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
            index = 0
            while True:
                data = source.read(CHUNK)
                if not data:
                    break
                if index == 2:
                    data = data[:17] + bytes([data[17] ^ 0x40]) + data[18:]
                compressed.write(data)
                index += 1
    if index < 3:
        raise RuntimeError("Late-chunk test requires at least 3 MiB of fixture data")
    userdata = context.root / "partitions/userdata.img"
    boot = context.root / "partitions/boot.img"
    for target_path in (userdata, boot):
        left = target_path.stat().st_size
        with target_path.open("r+b") as target:
            while left:
                data = b"X" * min(CHUNK, left)
                target.write(data)
                left -= len(data)
    plan = engine.make_restore_plan(context, snapshot)
    original_checkpoint = restore_support.RestoreJournal.checkpoint
    original_stamp_check = streaming.VerifiedArchive._check_unchanged

    def inject(journal, phase, receipt):
        original_checkpoint(journal, phase, receipt)
        if phase == "started":
            # The already-pinned archive inode changes after full verification.
            with changed_path.open("rb") as changed, archive_path.open("r+b") as archive:
                while True:
                    data = changed.read(CHUNK)
                    if not data:
                        break
                    archive.write(data)
                archive.truncate()
            # Deliberately bypass only metadata detection in this test process,
            # to establish that the trusted chunk hashes independently stop it.
            streaming.VerifiedArchive._check_unchanged = lambda self: None

    try:
        restore_support.RestoreJournal.checkpoint = inject
        try:
            engine.restore(context, plan, plan["confirmation_token"])
        except RecoveryError as error:
            if error.code != "RESTORE_CHUNK_MISMATCH":
                raise
            receipt = error.restore_receipt
        else:
            raise RuntimeError("Changed late chunk was accepted")
    finally:
        restore_support.RestoreJournal.checkpoint = original_checkpoint
        streaming.VerifiedArchive._check_unchanged = original_stamp_check
    if receipt["partitions"]["userdata"]["verified_prefix_bytes"] != 2 * CHUNK:
        raise RuntimeError("Failure receipt does not identify the correct verified prefix")
    with userdata.open("rb") as actual, gzip.open(archive_path, "rb") as expected:
        for _ in range(2):
            if actual.read(CHUNK) != expected.read(CHUNK):
                raise RuntimeError("Previously verified prefix was not restored correctly")
        while True:
            data = actual.read(CHUNK)
            if not data:
                break
            if data != b"X" * len(data):
                raise RuntimeError("The rejected chunk or later data was written")
    with boot.open("rb") as target:
        if any(byte != ord("X") for byte in target.read()):
            raise RuntimeError("Boot changed after the userdata integrity failure")
    return {"verified": True, "error": "RESTORE_CHUNK_MISMATCH", "boot_unchanged": True,
            "rejected_and_remaining_bytes_unchanged": True,
            "test_local_stamp_detection_bypass": True, "failure_receipt": receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-mib", type=int, default=64)
    parser.add_argument("--address-space-mib", type=int, default=64)
    parser.add_argument("--fault-late-chunk", action="store_true",
                        help="Also inject a changed chunk after full preverification and require refusal")
    args = parser.parse_args()
    if not 1 <= args.raw_mib <= 1024 or not 48 <= args.address_space_mib <= 512:
        parser.error("Use 1–1024 MiB of fixture data and a 48–512 MiB address-space limit")
    if args.fault_late_chunk and args.raw_mib < 3:
        parser.error("The late-chunk fault test needs at least 3 MiB")
    limit = args.address_space_mib * CHUNK
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    with tempfile.TemporaryDirectory(prefix="a22x-streaming-smoke-") as temporary:
        root = Path(temporary) / "fixture"
        create_fixture(root, userdata_bytes=min(args.raw_mib * CHUNK, 4 * CHUNK))
        path = root / "partitions/userdata.img"
        with path.open("r+b") as source:
            source.truncate(args.raw_mib * CHUNK)
            source.seek(args.raw_mib * CHUNK - 32)
            source.write(b"STREAMING_SMOKE_LAST_BYTES".ljust(32, b"!"))
        config_path = root / "fixture.json"
        config = json.loads(config_path.read_text())
        config["partitions"]["userdata"]["size_bytes"] = args.raw_mib * CHUNK
        config_path.write_bytes(canonical(config) + b"\n")
        context = FixtureContext(root)
        expected = hash_file(path, regular_only=True)
        backup_plan = engine.make_backup_plan(context, "linux", root / "storage/sd", "streaming", "gzip")
        snapshot = engine.backup(context, backup_plan, backup_plan["confirmation_token"])
        # Damage separated regions without allocating an image-sized buffer.
        with path.open("r+b") as target:
            for offset in (0, args.raw_mib * CHUNK // 2, args.raw_mib * CHUNK - 4096):
                target.seek(offset)
                target.write(b"X" * 4096)
        restore_plan = engine.make_restore_plan(context, snapshot["backup_path"])
        result = engine.restore(context, restore_plan, restore_plan["confirmation_token"])
        if hash_file(path, regular_only=True) != expected:
            raise RuntimeError("Independent smoke-test hash does not match the original source")
        fault = late_chunk_fault(context, snapshot["backup_path"]) if args.fault_late_chunk else None
        # Linux getrusage().ru_maxrss can retain a large parent's RSS high-water
        # mark across fork+exec. VmHWM belongs to this executable's current mm,
        # so a combined test runner cannot contaminate this child's measurement.
        status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        peak_rss_kib = int(status["VmHWM"].split()[0])
        print(json.dumps({"schema": "a22x.streaming.smoke.v1", "verified": result["verified"],
                          "userdata_bytes": args.raw_mib * CHUNK,
                          "address_space_limit_bytes": limit,
                          "peak_rss_kib": peak_rss_kib,
                          "peak_rss_source": "/proc/self/status:VmHWM",
                          "rusage_maxrss_kib_including_preexec": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          "index_bytes": result["receipt"]["chunk_index_bytes"],
                          "decoded_staging_bytes": result["receipt"]["decoded_staging_bytes"],
                          "completed_partitions": result["receipt"]["completed_partitions"],
                          "late_chunk_fault": fault,
                          "physical_device_written": False, "auto_reboot": False}, sort_keys=True))


if __name__ == "__main__":
    main()
