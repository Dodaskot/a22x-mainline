"""Legacy raw backup/verify/fixture restore and explicit Linux v2 dispatch.

No operation is executed at import. Generic physical raw/Android restore stays
disabled; only linux_workflow owns its separately qualified narrow writer.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from common import (CHUNK, LABELS, PROFILE_NAMES, PROFILES, RESERVE, RETAINED_LABELS, RecoveryError,
                    canonical, digest, fail, fsync_directory, hash_file, read_json, read_json_with_hash,
                    rename_noreplace, require_hash, stamp, utc, validate_name,
                    write_json_new)
from storage import operation_lock
from streaming import VerifiedArchive, index_bytes_for, validate_entry
from restore_support import RestoreJournal, write_verified_partition

MANIFEST_SCHEMA = "a22x.backup.v1"
PLAN_SCHEMA = "a22x.plan.v1"
PLAN_LIFETIME = 60 * 60
RESTORE_ORDER = {"userdata": 0, "metadata": 1, "recovery": 2, "boot": 3}


def emit(progress, operation, phase, partition=None, done=0, total=0, **extra):
    if progress:
        progress({"event": "progress", "operation": operation, "phase": phase,
                  "partition": partition, "bytes_done": done, "bytes_total": total, **extra})


def profile_labels(profile):
    if profile not in PROFILES:
        fail("INVALID_PROFILE", "Unknown backup profile", profile=profile)
    return PROFILES[profile]


def _select_compression(compression):
    if compression == "auto":
        return "zstd" if shutil.which("zstd") else "gzip"
    if compression not in {"gzip", "zstd"}:
        fail("INVALID_COMPRESSION", "Only gzip and zstd are supported")
    if compression == "zstd" and not shutil.which("zstd"):
        fail("ZSTD_UNAVAILABLE", "zstd executable is not installed")
    return compression


def _check_free(path):
    free = shutil.disk_usage(path).free
    if free < RESERVE:
        fail("INSUFFICIENT_SPACE", "Destination has reached the reserved free-space floor",
             free_bytes=free, reserve_bytes=RESERVE)
    return free


def inventory(context):
    parts = []
    for label in ("boot", "recovery", "metadata", "userdata"):
        try:
            part = context.partition(label)
            part.pop("stamp", None)
            try:
                context.graph.require_unmounted(part["device"], context.mounts)
                part.update(available=True, mounted=False)
            except RecoveryError as exc:
                part.update(available=True, mounted=True, mount_blocker=exc.code)
            parts.append(part)
        except RecoveryError as exc:
            parts.append({"label": label, "available": False, "error": exc.code})
    repositories = []
    if context.mode == "fixture":
        candidates = [context.root / item["path"] for item in context.config.get("repositories", [])]
    else:
        candidates = [Path(item["mountpoint"]) for item in context.self_mounts
                      if item["filesystem"] in {"ext4", "f2fs", "btrfs", "xfs", "exfat", "ntfs3"}]
    for candidate in candidates:
        try:
            repo = context.repository(candidate)
            repo["free_bytes"] = shutil.disk_usage(candidate).free
            repo["external"] = not any(
                context.graph.physical_roots(part["device"]) & context.graph.physical_roots(repo["device"])
                for part in parts if part.get("available"))
            repositories.append(repo)
        except (RecoveryError, OSError):
            continue
    from linux_workflow import capabilities as linux_capabilities
    return {"ok": True, "operation": "inventory", "mode": context.mode,
            "device": context.device, "partitions": parts, "repositories": repositories,
            "profiles": [{"id": name, "name": PROFILE_NAMES[name], "partitions": list(labels),
                          "requires_retained_firmware": name == "android",
                          "full_device_backup": False} for name, labels in PROFILES.items()],
            "capabilities": {"inventory": True, "offline_backup": True, "verify": True,
                             "restore_plan": True, "restore": context.mode == "fixture",
                             "device_restore_blocker": None if context.mode == "fixture"
                             else "DEVICE_RESTORE_NOT_QUALIFIED", **linux_capabilities(context)}}


def _seal_plan(plan):
    plan["plan_id"] = digest(plan)
    plan["confirmation_token"] = plan["operation"].upper() + "-" + plan["plan_id"][:16]
    return plan


def _check_plan(context, plan, operation, confirmation=None):
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA or plan.get("operation") != operation:
        fail("INVALID_PLAN", "Unexpected plan schema or operation")
    unsealed = {key: value for key, value in plan.items() if key not in {"plan_id", "confirmation_token"}}
    expected = digest(unsealed)
    if plan.get("plan_id") != expected or plan.get("confirmation_token") != operation.upper() + "-" + expected[:16]:
        fail("PLAN_CHANGED", "The plan differs from its reviewed content")
    if confirmation is not None and confirmation != plan["confirmation_token"]:
        fail("CONFIRMATION_MISMATCH", "Confirmation does not identify this exact plan")
    if time.time() > plan.get("expires_unix", 0):
        fail("PLAN_EXPIRED", "Create and review a fresh plan")
    if plan.get("mode") != context.mode or plan.get("device") != context.device:
        fail("WRONG_DEVICE", "Plan belongs to a different device or execution mode")
    if plan.get("context_contract") != context.contract:
        fail("PLAN_STALE", "Device/storage configuration changed after planning")
    expected_labels = list(profile_labels(plan.get("profile")))
    if [item.get("label") for item in plan.get("partitions", [])] != expected_labels:
        fail("INVALID_PLAN", "Plan partition set does not match the selected profile")
    current = [context.partition(label) for label in expected_labels]
    if current != plan["partitions"]:
        fail("PLAN_STALE", "Partition identity or size changed after planning")
    repository = context.repository(plan["repository"]["path"], writing=operation == "backup")
    if repository != plan["repository"]:
        fail("PLAN_STALE", "Repository identity or mount changed after planning")
    context.assert_offline(current, repository)
    return current, repository


def _same_compatibility(left, right):
    return isinstance(left, dict) and isinstance(right, dict) and (
        left.get("retained_partitions") == right.get("retained_partitions"))


def _source_formats(context, profile, parts):
    formats = {part["label"]: context.source_format(part) for part in parts if part["label"] == "userdata"}
    # The observed A226B stock Android uses F2FS/opaque metadata-encrypted data.
    # An ext4/LUKS header is contrary evidence for this device-specific profile.
    if profile == "android" and formats["userdata"]["kind"] in {"ext4", "luks"}:
        fail("SOURCE_PROFILE_MISMATCH", "This userdata looks like a Linux filesystem/container, not stock A226B Android data",
             source_format=formats["userdata"]["kind"], suggested_profile="linux")
    return formats


def make_backup_plan(context, profile, repository, name, compression="auto", progress=None, *, data_format="raw"):
    if data_format == "partclone-ext4":
        if profile != "linux":
            fail("INVALID_PROFILE", "Used-block ext4 backup is available only for the Linux profile")
        if compression not in {"auto", "zstd"}:
            fail("INVALID_COMPRESSION", "The explicit Linux v2 format requires zstd")
        from linux_workflow import make_backup_plan as make_linux_backup_plan
        return make_linux_backup_plan(context, repository, name, progress)
    if data_format != "raw":
        fail("INVALID_FORMAT", "Unknown backup representation")
    validate_name(name)
    parts = [context.partition(label) for label in profile_labels(profile)]
    repo = context.repository(repository, writing=True)
    context.assert_offline(parts, repo)
    compression = _select_compression(compression)
    path = Path(repo["path"]) / name
    if os.path.lexists(path):
        fail("BACKUP_EXISTS", "Backup name already exists", path=str(path))
    free = _check_free(Path(repo["path"]))
    formats = _source_formats(context, profile, parts)
    compatibility = context.compatibility(progress, "backup-plan") if profile == "android" else None
    blockers = []
    try:
        context.assert_recovery()
    except RecoveryError as exc:
        blockers.append(exc.code)
    except FileNotFoundError:
        blockers.append("RECOVERY_IDENTITY_MISSING")
    now = time.time()
    return _seal_plan({"schema": PLAN_SCHEMA, "operation": "backup", "mode": context.mode,
                       "device": context.device, "context_contract": context.contract,
                       "profile": profile, "partitions": parts, "repository": repo,
                       "name": name, "backup_path": str(path), "compression": compression,
                       "compatibility": compatibility, "source_formats": formats, "created_utc": utc(),
                       "expires_unix": now + PLAN_LIFETIME,
                       "snapshot_id": str(uuid.uuid4()),
                       "space_policy": {"kind": "stream-with-reserve", "reserve_bytes": RESERVE,
                                        "free_bytes_at_plan": free, "compressed_estimate_bytes": None},
                       "can_execute": not blockers, "blockers": blockers})


def save_plan(context, plan, output):
    path = context.user_path(output, exists=False)
    if context.mode == "device" and Path("/run/a22x-recovery/plans") not in path.parents:
        fail("INVALID_PLAN_LOCATION", "Device plans must reside in /run/a22x-recovery/plans")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    context.user_path(path.parent)
    write_json_new(path, plan)
    return plan_result(plan, path)


def plan_result(plan, path):
    if plan.get("schema") == "a22x.plan.v2":
        from linux_workflow import plan_result as linux_plan_result
        return linux_plan_result(plan, path)
    summary = {"profile": plan["profile"], "partitions": [p["label"] for p in plan["partitions"]],
               "raw_bytes": sum(p["size_bytes"] for p in plan["partitions"]),
               "compressed_estimate_bytes": None, "backup_path": plan["backup_path"],
               "full_device_backup": False, "requires_retained_firmware": plan["profile"] == "android",
               "source_formats": plan.get("source_formats", {}),
               "write_targets": [] if plan["operation"] == "backup" else
               [{"label": p["label"], "path": p["path"], "size_bytes": p["size_bytes"]}
                for p in sorted(plan["partitions"], key=lambda p: RESTORE_ORDER[p["label"]])],
               "auto_reboot": False}
    return {"ok": True, "operation": plan["operation"] + "-plan", "plan_path": str(path),
            "plan_id": plan["plan_id"], "confirmation_token": plan["confirmation_token"],
            "can_execute": plan["can_execute"], "blockers": plan["blockers"], "summary": summary}


def load_plan(context, path):
    return read_json(context.user_path(path))


def _copy_to_compressor(source, sink, size, label, directory, progress):
    raw_hash = hashlib.sha256()
    count = 0
    while count < size:
        _check_free(directory)
        block = source.read(min(CHUNK, size - count))
        if not block:
            fail("SOURCE_SHORT_READ", "Source ended before the admitted partition size", label=label)
        sink.write(block)
        raw_hash.update(block)
        count += len(block)
        emit(progress, "backup", "compress", label, count, size)
    if source.read(1):
        fail("SOURCE_SIZE_CHANGED", "Source grew during the snapshot", label=label)
    return raw_hash.hexdigest()


def _compress_partition(source, output: Path, part, compression, progress):
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    process = None
    try:
        with os.fdopen(fd, "wb", closefd=False) as target:
            if compression == "gzip":
                with gzip.GzipFile(filename="", mode="wb", fileobj=target, compresslevel=1, mtime=0) as compressed:
                    raw_hash = _copy_to_compressor(source, compressed, part["size_bytes"],
                                                   part["label"], output.parent, progress)
            else:
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen(["zstd", "-q", "-T1", "-3", "--stdout"],
                                               stdin=subprocess.PIPE, stdout=target, stderr=errors)
                    try:
                        raw_hash = _copy_to_compressor(source, process.stdin, part["size_bytes"],
                                                       part["label"], output.parent, progress)
                        process.stdin.close()
                        code = process.wait(timeout=60)
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
                    if code:
                        errors.seek(0)
                        fail("COMPRESSION_FAILED", errors.read(4096).decode(errors="replace"))
            target.flush()
            os.fsync(fd)
        if stamp(source.fileno()) != part["stamp"]:
            fail("SOURCE_CHANGED", "Source changed while being backed up", label=part["label"])
    finally:
        os.close(fd)
    archive_hash, archive_bytes = hash_file(output, regular_only=True)
    return {"label": part["label"], "archive": output.name, "compression": compression,
            "raw_bytes": part["size_bytes"], "raw_sha256": raw_hash,
            "archive_bytes": archive_bytes, "archive_sha256": archive_hash}


def _manifest(context, plan, entries):
    return {"schema": MANIFEST_SCHEMA, "snapshot_id": plan["snapshot_id"],
            "created_utc": utc(), "device": context.device, "profile": plan["profile"],
            "profile_description": PROFILE_NAMES[plan["profile"]], "full_device_backup": False,
            "source_mode": context.mode, "entries": entries, "compatibility": plan["compatibility"],
            "source_formats": plan["source_formats"],
            "consistency": {"sources_unmounted": True, "all_namespaces_checked": context.mode == "device",
                            "fixture_simulation": context.mode == "fixture",
                            "source_size_and_identity_checked": True},
            "limitations": ["Not a full-device clone; bootloader, modem, EFS and RPMB are not backed up.",
                            "Android encrypted data remains bound to compatible firmware and secure state.",
                            "No decrypt, credential reset, automatic format or repartitioning."]}


def backup(context, plan, confirmation, progress=None):
    if isinstance(plan, dict) and plan.get("schema") == "a22x.plan.v2":
        from linux_workflow import backup as linux_backup
        return linux_backup(context, plan, confirmation, progress)
    with operation_lock(context):
        current = context.refresh()
        parts, repo = _check_plan(current, plan, "backup", confirmation)
        current.assert_recovery()
        if plan["profile"] == "android" and not _same_compatibility(current.compatibility(progress, "backup"), plan["compatibility"]):
            fail("FIRMWARE_MISMATCH", "Retained Android firmware changed after planning")
        if _source_formats(current, plan["profile"], parts) != plan["source_formats"]:
            fail("SOURCE_FORMAT_CHANGED", "Source filesystem signature changed after planning")
        destination = Path(repo["path"]) / plan["name"]
        if str(destination) != plan["backup_path"]:
            fail("INVALID_PLAN", "Backup path differs from repository/name")
        if os.path.lexists(destination):
            fail("BACKUP_EXISTS", "Backup destination already exists")
        temporary = destination.parent / ("." + plan["name"] + ".partial-" + plan["snapshot_id"])
        temporary.mkdir(mode=0o700)
        published = False
        try:
            with contextlib.ExitStack() as stack:
                sources = {p["label"]: stack.enter_context(current.open_partition(p)) for p in parts}
                entries = []
                for part in parts:
                    suffix = ".img.zst" if plan["compression"] == "zstd" else ".img.gz"
                    entry = _compress_partition(sources[part["label"]], temporary / (part["label"] + suffix),
                                                part, plan["compression"], progress)
                    entry["snapshot_id"] = plan["snapshot_id"]
                    entries.append(entry)
                # All sources remain open/locked while decoded archives are checked.
                manifest = _manifest(current, plan, entries)
                for entry in entries:
                    _verify_entry(temporary, entry, progress, operation="backup", phase="verify")
                for part in parts:
                    if stamp(sources[part["label"]].fileno()) != part["stamp"]:
                        fail("SOURCE_CHANGED", "Source changed before snapshot publication", label=part["label"])
                # Reject storage topology changes before publishing a successful backup.
                fresh = current.refresh()
                if fresh.contract != current.contract:
                    fail("STORAGE_CHANGED", "Storage configuration changed during the snapshot")
                fresh_repo = fresh.repository(repo["path"], writing=True)
                if fresh_repo != repo:
                    fail("STORAGE_CHANGED", "Repository changed during the snapshot")
                fresh.assert_offline([fresh.partition(p["label"]) for p in parts], fresh_repo)
                write_json_new(temporary / "manifest.json", manifest)
                manifest_hash, _ = hash_file(temporary / "manifest.json", regular_only=True)
                write_json_new(temporary / "COMPLETE.json", {
                    "schema": "a22x.complete.v1", "snapshot_id": manifest["snapshot_id"],
                    "manifest_sha256": manifest_hash, "verified_utc": utc(),
                    "raw_and_archive_hashes_verified": True})
                fsync_directory(temporary)
                rename_noreplace(temporary, destination)
                published = True
            emit(progress, "backup", "complete", done=sum(p["size_bytes"] for p in parts),
                 total=sum(p["size_bytes"] for p in parts))
            return {"ok": True, "operation": "backup", "backup_path": str(destination),
                    "snapshot_id": plan["snapshot_id"], "profile": plan["profile"],
                    "raw_bytes": sum(e["raw_bytes"] for e in entries),
                    "archive_bytes": sum(e["archive_bytes"] for e in entries),
                    "verified": True, "auto_reboot": False}
        except BaseException as exc:
            if not published and temporary.exists():
                try:
                    # COMPLETE must never survive a failed unpublished backup.
                    complete = temporary / "COMPLETE.json"
                    if complete.exists():
                        complete.unlink()
                    write_json_new(temporary / "FAILED.json", {
                        "schema": "a22x.failed.v1", "utc": utc(),
                        "error": getattr(exc, "code", type(exc).__name__),
                        "restorable": False, "snapshot_id": plan["snapshot_id"]})
                except OSError:
                    pass  # ENOSPC can also prevent an optional failure receipt.
            if isinstance(exc, RecoveryError):
                exc.details.setdefault("partial_path", str(temporary))
            raise


def _load_manifest(context, backup_path):
    path = context.user_path(backup_path)
    if not path.is_dir() or ".partial-" in path.name:
        fail("INCOMPLETE_BACKUP", "A completed snapshot directory is required")
    complete = read_json(path / "COMPLETE.json")
    manifest_path = path / "manifest.json"
    manifest, manifest_hash = read_json_with_hash(manifest_path)
    if isinstance(manifest, dict) and manifest.get("schema") == "a22x.backup.v2":
        from linux_workflow import load_manifest as load_linux_manifest
        return load_linux_manifest(context, backup_path)
    if not isinstance(complete, dict) or complete.get("schema") != "a22x.complete.v1" or complete.get("manifest_sha256") != manifest_hash:
        fail("MANIFEST_HASH_MISMATCH", "Snapshot completion does not seal this manifest")
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("device"), dict):
        fail("INVALID_MANIFEST", "Unsupported snapshot manifest")
    labels = profile_labels(manifest.get("profile"))
    entries = manifest.get("entries", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries) or [e.get("label") for e in entries] != list(labels):
        fail("PROFILE_PARTITION_MISMATCH", "Snapshot must contain the exact profile partition set")
    if not isinstance(manifest.get("snapshot_id"), str) or not manifest["snapshot_id"]:
        fail("INVALID_MANIFEST", "Missing snapshot identity")
    if complete.get("snapshot_id") != manifest["snapshot_id"]:
        fail("MIXED_SNAPSHOT", "Completion and manifest belong to different snapshots")
    if complete.get("raw_and_archive_hashes_verified") is not True:
        fail("INCOMPLETE_BACKUP", "Snapshot has no completed verification receipt")
    if manifest.get("full_device_backup") is not False:
        fail("INVALID_MANIFEST", "This format does not represent a full-device clone")
    for entry in entries:
        label = entry["label"]
        validate_entry(entry)
        if entry.get("snapshot_id") != manifest["snapshot_id"]:
            fail("MIXED_SNAPSHOT", "Partition entries belong to different snapshots", label=label)
        context.user_path(path / entry["archive"])
    if manifest["profile"] == "android":
        compatibility = manifest.get("compatibility")
        if not isinstance(compatibility, dict) or compatibility.get("evidence") != "observed-read-only-sha256":
            fail("FIRMWARE_IDENTITY_MISSING", "Android snapshot lacks firmware compatibility information")
        retained = compatibility.get("retained_partitions", {})
        if set(retained) != set(RETAINED_LABELS):
            fail("FIRMWARE_IDENTITY_MISSING", "Android snapshot must identify all retained firmware partitions")
        for value in retained.values():
            require_hash(value.get("sha256"), "retained_sha256")
        if manifest.get("source_formats", {}).get("userdata", {}).get("kind") in {"ext4", "luks"}:
            fail("SOURCE_PROFILE_MISMATCH", "This Android snapshot describes an incompatible Linux data container")
    return path, manifest, manifest_hash


def _verify_entry(path, entry, progress=None, *, operation="verify", phase="verify"):
    with VerifiedArchive(path, entry) as archive:
        return archive.verify(progress, operation=operation, phase=phase)


def verify_backup(context, backup_path, progress=None):
    path, manifest, manifest_hash = _load_manifest(context, backup_path)
    if manifest["schema"] == "a22x.backup.v2":
        from linux_workflow import verify_backup as verify_linux_backup
        return verify_linux_backup(context, backup_path, progress)
    archive_stamps = {}
    for entry in manifest["entries"]:
        archive_stamps[entry["label"]] = _verify_entry(path, entry, progress)
    return {"ok": True, "operation": "verify", "backup_path": str(path), "verified": True,
            "manifest_sha256": manifest_hash, "manifest": manifest, "archive_stamps": archive_stamps}


def list_backups(context, repository):
    repo = context.repository(repository)
    result = []
    for candidate in sorted(Path(repo["path"]).iterdir(), reverse=True):
        if not candidate.is_dir() or candidate.is_symlink() or candidate.name.startswith("."):
            continue
        try:
            path, manifest, _ = _load_manifest(context, candidate)
            result.append({"path": str(path), "name": path.name, "profile": manifest["profile"],
                           "schema": manifest["schema"], "workflow": manifest.get("workflow"),
                           "created_utc": manifest["created_utc"],
                           "device_identity": manifest["device"]["identity"],
                           "raw_bytes": sum(e["raw_bytes"] for e in manifest["entries"]),
                           "archive_bytes": sum(e["archive_bytes"] for e in manifest["entries"]),
                           "status": "complete", "content_verified_this_listing": False,
                           "same_device": manifest["device"] == context.device})
        except (RecoveryError, OSError, KeyError, TypeError):
            continue
    return {"ok": True, "operation": "list", "repository": repo["path"], "backups": result}


def manage_backup(context, backup_path, action, new_name=None, confirm=None):
    """Rename or delete a finished snapshot (2026-11-11, as TWRP's Restore page does). Only a folder
    that loads as a complete snapshot is touched, in a repository that is mounted for writing and
    while no other backup operation runs; a snapshot is deleted only when its own name is given
    back as the confirmation."""
    with operation_lock(context):
        path, manifest, _ = _load_manifest(context, backup_path)
        context.repository(path.parent, writing=True)
        if action == "rename":
            validate_name(new_name if isinstance(new_name, str) else "")
            if ".partial-" in new_name:
                fail("INVALID_NAME", "This name is kept for unfinished snapshots")
            target = path.parent / new_name
            if target.exists() or target.is_symlink():
                fail("BACKUP_EXISTS", "A backup with this name already exists", path=str(target))
            rename_noreplace(path, target)
            fsync_directory(path.parent)
            return {"ok": True, "operation": "rename-backup", "backup_path": str(target), "name": new_name,
                    "old_name": path.name}
        if confirm != path.name:
            fail("CONFIRMATION_REQUIRED", "Deleting a backup must be confirmed with its name")
        shutil.rmtree(path)
        fsync_directory(path.parent)
        return {"ok": True, "operation": "delete-backup", "deleted": str(path), "name": path.name,
                "profile": manifest.get("profile")}


def make_restore_plan(context, backup_path, progress=None):
    path, manifest, manifest_hash = _load_manifest(context, backup_path)
    if manifest["schema"] == "a22x.backup.v2":
        from linux_workflow import make_destructive_plan
        return make_destructive_plan(context, backup_path, "restore", progress)
    if manifest["device"] != context.device or manifest.get("source_mode") != context.mode:
        fail("WRONG_DEVICE", "Snapshot belongs to another device or execution mode")
    profile = manifest["profile"]
    parts = [context.partition(label) for label in profile_labels(profile)]
    for part, entry in zip(parts, manifest["entries"]):
        if part["size_bytes"] != entry["raw_bytes"]:
            fail("TARGET_SIZE_MISMATCH", "Snapshot and target geometry differ", label=part["label"])
    repo = context.repository(path.parent)
    context.assert_offline(parts, repo)
    if profile == "android" and not _same_compatibility(context.compatibility(progress, "restore-plan"), manifest["compatibility"]):
        fail("FIRMWARE_MISMATCH", "Retained Android firmware differs from the snapshot")
    verified = verify_backup(context, path, progress)
    if verified["manifest_sha256"] != manifest_hash:
        fail("PLAN_STALE", "Snapshot manifest changed during restore planning")
    return _seal_plan({"schema": PLAN_SCHEMA, "operation": "restore", "mode": context.mode,
                       "device": context.device, "context_contract": context.contract,
                       "profile": profile, "partitions": parts, "repository": repo,
                       "backup_path": str(path), "manifest_sha256": manifest_hash,
                       "archive_stamps": verified["archive_stamps"],
                       "compatibility": manifest["compatibility"], "source_formats": manifest.get("source_formats", {}), "created_utc": utc(),
                       "expires_unix": time.time() + PLAN_LIFETIME,
                       "can_execute": context.mode == "fixture",
                       "blockers": [] if context.mode == "fixture" else ["DEVICE_RESTORE_NOT_QUALIFIED"],
                       "write_order": [p["label"] for p in sorted(parts, key=lambda p: RESTORE_ORDER[p["label"]])],
                       "streaming": {"chunk_bytes": CHUNK,
                                     "index_bytes": sum(index_bytes_for(p["size_bytes"]) for p in parts),
                                     "decoded_staging_bytes": 0,
                                     "verify_each_chunk_before_write": True},
                       "atomic_restore": False, "auto_reboot": False})


def restore(context, plan, confirmation, progress=None):
    if isinstance(plan, dict) and plan.get("schema") == "a22x.plan.v2":
        from linux_workflow import execute as execute_linux
        if plan.get("operation") != "restore":
            fail("INVALID_PLAN", "restore requires an explicit restore plan")
        return execute_linux(context, plan, confirmation, progress)
    # This is a hard prototype boundary, not a CLI opt-in or marker check.
    if context.mode != "fixture":
        fail("DEVICE_RESTORE_NOT_QUALIFIED", "Physical block restore is not enabled in this prototype")
    with operation_lock(context):
        current = context.refresh()
        parts, repo = _check_plan(current, plan, "restore", confirmation)
        path, manifest, manifest_hash = _load_manifest(current, plan["backup_path"])
        if manifest_hash != plan["manifest_sha256"]:
            fail("PLAN_STALE", "Snapshot manifest changed after review")
        if manifest["profile"] == "android" and not _same_compatibility(current.compatibility(progress, "restore"), plan["compatibility"]):
            fail("FIRMWARE_MISMATCH", "Retained Android firmware changed after review")
        with contextlib.ExitStack() as stack:
            targets = {p["label"]: stack.enter_context(current.open_partition(p, write=True)) for p in parts}
            admitted = {p["label"]: p for p in parts}
            archives = {}
            for entry in manifest["entries"]:
                label = entry["label"]
                archive = stack.enter_context(VerifiedArchive(path, entry, target_bytes=admitted[label]["size_bytes"]))
                # Pin all archives through both passes. Index allocation is bound
                # to admitted geometry, never to an unadmitted manifest count.
                if any((archive.initial_stamp["device"], archive.initial_stamp["inode"]) ==
                       (p["stamp"]["device"], p["stamp"]["inode"]) for p in parts):
                    fail("ARCHIVE_TARGET_ALIAS", "Archive and restore target are the same regular file", label=label)
                archives[label] = archive
            for entry in manifest["entries"]:
                actual_stamp = archives[entry["label"]].verify(
                    progress, operation="restore", phase="verify-before-write", collect_chunks=True)
                if actual_stamp != plan["archive_stamps"][entry["label"]]:
                    fail("PLAN_STALE", "Archive changed after restore planning", label=entry["label"])
            for part in parts:
                if stamp(targets[part["label"]].fileno()) != part["stamp"]:
                    fail("PARTITION_CHANGED", "Target changed before the first write", label=part["label"])
            fresh = current.refresh()
            if fresh.contract != current.contract:
                fail("STORAGE_CHANGED", "Storage admission changed before writing")
            fresh_repo = fresh.repository(repo["path"])
            if fresh_repo != repo:
                fail("STORAGE_CHANGED", "Repository changed before the first restore write")
            fresh.assert_offline([fresh.partition(p["label"]) for p in parts], fresh_repo)
            completed = []
            receipt = {"schema": "a22x.restore.receipt.v1", "plan_id": plan["plan_id"],
                       "started_utc": utc(), "profile": plan["profile"], "mode": "fixture",
                       "physical_device_written": False, "auto_reboot": False,
                       "all_archives_verified_before_first_write": True, "completed_partitions": completed,
                       "status": "started", "active_partition": None,
                       "chunk_bytes": CHUNK, "chunk_index_bytes": sum(a.index_bytes for a in archives.values()),
                       "decoded_staging_bytes": 0, "verify_each_chunk_before_write": True,
                       "prefix_definition": "Bytes accepted by successful write calls from individually verified chunks; durability requires fsync and full readback",
                       "remaining_definition": "Bytes not yet confirmed written; a failing or in-flight write may have changed additional bytes",
                       "power_loss_definition": "A checkpoint is a lower bound on reported progress; later writes may exist. No automatic resume",
                       "partitions": {p["label"]: {"size_bytes": p["size_bytes"], "verified_prefix_bytes": 0,
                                                  "remaining_bytes": p["size_bytes"], "fsync_completed": False,
                                                  "readback_verified": False} for p in parts}}
            journal = RestoreJournal(current, plan["plan_id"])
            receipt["journal_path"] = str(journal.path)
            try:
                # A durable intent record is mandatory before any target write.
                journal.checkpoint("started", receipt)
                for part in sorted(parts, key=lambda part: RESTORE_ORDER[part["label"]]):
                    label = part["label"]
                    receipt.update(status="writing", active_partition=label)
                    journal.checkpoint("before-" + label, receipt)
                    write_verified_partition(archives[label], targets[label], part,
                                             receipt["partitions"][label], progress)
                    completed.append(label)
                    journal.checkpoint("verified-" + label, receipt)
                receipt.update(status="complete", completed_utc=utc(), active_partition=None)
                journal.checkpoint("complete", receipt)
                journal.finish(receipt)
                return {"ok": True, "operation": "restore", "backup_path": str(path),
                        "receipt": receipt, "verified": True, "auto_reboot": False}
            except BaseException as exc:
                receipt.update(status="failed", failed_utc=utc(), error=getattr(exc, "code", type(exc).__name__),
                               partial_restore_possible=any(p["verified_prefix_bytes"] > 0 for p in receipt["partitions"].values())
                               or receipt["active_partition"] is not None)
                try:
                    journal.checkpoint("failed", receipt)
                    journal.finish(receipt)
                except OSError as journal_error:
                    receipt["failure_receipt_error"] = str(journal_error)
                # Preserve I/O/interrupt exception types; the CLI also receives
                # the exact same failure receipt rather than losing the prefix.
                exc.restore_receipt = receipt
                if isinstance(exc, RecoveryError):
                    exc.details.update(completed_partitions=completed,
                                       partial_restore_possible=receipt["partial_restore_possible"],
                                       auto_reboot=False, receipt=receipt)
                raise


def make_wipe_plan(context, backup_path, progress=None):
    from linux_workflow import make_destructive_plan
    return make_destructive_plan(context, backup_path, "wipe", progress)


def wipe(context, plan, confirmation, progress=None):
    from linux_workflow import execute as execute_linux
    if not isinstance(plan, dict) or plan.get("schema") != "a22x.plan.v2" or plan.get("operation") != "wipe":
        fail("INVALID_PLAN", "Wipe is restricted to an explicit Linux v2 plan")
    return execute_linux(context, plan, confirmation, progress)
