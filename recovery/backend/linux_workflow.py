"""Qualified Linux-only compact Backup / Format userdata / Restore workflow.

The v1 raw/Android physical restore boundary is unchanged. Physical writes here
require a matching build/tool/device qualification capsule, a complete verified
v2 snapshot on separate ext4 storage and a fresh exact confirmation plan. The UI
adds its explicit three-second hold; this module cannot authenticate a touch.
No automatic reboot, repartition, discard, Android decrypt or secure erasure.
"""
from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import os
import select
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from common import (CHUNK, RecoveryError, digest, directory_identity, fail, fsync_directory, hash_file,
                    no_symlinks, open_regular_fd, read_json, read_json_with_hash,
                    rename_noreplace, require_hash, stamp, utc, validate_name, write_json_new)
from storage import operation_lock
from streaming import VerifiedArchive, validate_entry as validate_raw_entry
import qualification as qualified

WORKFLOW = qualified.WORKFLOW
MANIFEST_SCHEMA = "a22x.backup.v2"
PLAN_SCHEMA = "a22x.plan.v2"
DATA_FORMAT = "partclone-ext4-v2+zstd"
LINUX_BOOT_K108 = "f188b56b28a68c2f8087e45ff60e29ea5353cdbd0f3294a7efe89fe9d9477993"
# Kernel #107 BOOT whose /init can skip the charge-only screen (2026-11-09).
LINUX_BOOT_CHARGER_OFF = "b689f2dd5bb4f72a65665e1ebfcd5012b4126a21d7b427fa54c2d3f9cffc27f8"
LINUX_BOOT_K116 = "71a50262a73859e1538e79277f0ce00822be208d52b32328038a5932b513f874"
# Kernel 121: kernel 116 plus the USB replug after system sleep, same modules (2026-11-09).
LINUX_BOOT_K121 = "e3542fcdd905a4b89a10eb828fa983bc9f0837a2635155d0124c692f6c5fbb1f"
LINUX_BOOT_K122 = "9701f48ddced60c648e79a502885778935d1cb5610a011d77fc1898eea01d41a"
# Kernel 138 (BOOT v147, 2026-11-12): the battery percent kept in the PMIC; same modules.
LINUX_BOOT_K138 = "5faafaa0035bff7bd77251ab01e2b4c395798755ad24db7b3240cc3d6139ad0f"
LABELS = ("boot", "userdata")
RESERVE = 64 * 1024 * 1024
METADATA_ALLOWANCE = 1024 * 1024
DESTRUCTIVE_TTL = 5 * 60
BACKUP_TTL = 20 * 60
BLKROGET = 0x125e
BLKGETSIZE64 = 0x80081272


def _compact():
    import partclone_image
    return partclone_image


def _emit(progress, operation, phase, label=None, done=0, total=0, **extra):
    if progress:
        progress({"event": "progress", "operation": operation, "phase": phase,
                  "partition": label, "bytes_done": done, "bytes_total": total, **extra})


def _parts(context):
    result = [context.partition(label) for label in LABELS]
    if context.mode == "device":
        qualified.device_binding(context, result)
    return result


def _identity(part):
    st = part["stamp"]
    return {**{k: part.get(k) for k in ("label", "path", "device", "size_bytes", "partition_number", "start_sector")},
            "object": {k: st[k] for k in ("device", "inode", "rdev")}}


def _media_identity(disk):
    """A stable name for the backup medium: the SD CID, or a USB stick's IDs and serial."""
    disk = Path(disk)
    cid = disk / "device/cid"
    if cid.is_file():
        return "sd-cid-sha256:" + hashlib.sha256(cid.read_bytes().strip()).hexdigest()
    for parent in (disk / "device").resolve().parents:
        if (parent / "idVendor").is_file():
            values = [(parent / name).read_bytes().strip() for name in ("idVendor", "idProduct")]
            if (parent / "serial").is_file():
                values.append((parent / "serial").read_bytes().strip())
            values.append((disk / "size").read_bytes().strip())
            return "usb-sha256:" + hashlib.sha256(b"/".join(values)).hexdigest()
    fail("REPOSITORY_IDENTITY_MISSING", "This workflow needs a backup medium with a stable identity")


def _repo_pin(context, repo):
    """Bind the directory, mount instance and physical SD/USB medium."""
    fd = os.open(repo["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        result = {"directory": directory_identity(os.fstat(fd), repo["filesystem"])}
        if context.mode == "device":
            fields = dict(line.split(":", 1) for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines() if ":" in line)
            result["mount_id"] = int(fields["mnt_id"].strip())
            roots = context.graph.physical_roots(repo["device"])
            if len(roots) != 1:
                fail("REPOSITORY_TOPOLOGY_UNQUALIFIED", "Linux v2 needs a single separate physical backup medium")
            result["physical_roots"] = sorted(roots)
            node = context.nodes[next(iter(roots))]
            # Only a real SD or USB medium qualifies, never a dm, network or loop mount.
            result["media_identity"] = _media_identity(node["sysfs"])
        return result
    finally:
        os.close(fd)


def _offline(context, parts, repo):
    context.assert_recovery()
    context.assert_offline(parts, repo)
    for part in parts:
        context.graph.require_no_holders(part["device"])
    # A compact archive can pass 4 GiB, which rules out FAT32.
    if context.mode == "device" and repo["filesystem"] not in ("ext4", "exfat"):
        fail("LINUX_REPOSITORY_UNQUALIFIED_FS", "Linux backups need external storage formatted as ext4 or exFAT")


def _free(repo, required=0):
    free = shutil.disk_usage(repo["path"]).free
    if free < RESERVE + required:
        fail("INSUFFICIENT_SPACE", "External storage has insufficient space including the mandatory reserve",
             free_bytes=free, required_archive_bytes=required, reserve_bytes=RESERVE,
             missing_bytes=RESERVE + required - free)
    return free


def _hash_fd(fd, size, *, progress=None, operation="verify", phase="hash", label="boot"):
    total, hasher = 0, hashlib.sha256()
    while total < size:
        data = os.pread(fd, min(CHUNK, size - total), total)
        if not data:
            fail("SOURCE_SHORT_READ", "Pinned partition ended before its exact admitted size", label=label)
        hasher.update(data)
        total += len(data)
        _emit(progress, operation, phase, label, total, size)
    return hasher.hexdigest()


def _fingerprint(context, part):
    with context.open_partition(part) as source:
        count = min(CHUNK, part["size_bytes"])
        data = os.pread(source.fileno(), count, 0) + os.pread(source.fileno(), count, part["size_bytes"] - count)
        if len(data) != count * 2:
            fail("SOURCE_SHORT_READ", "Cannot pin target boundary signatures", label=part["label"])
        return hashlib.sha256(data).hexdigest()


def _boot_hash(context, part, progress=None, operation="plan"):
    with context.open_partition(part) as source:
        return _hash_fd(source.fileno(), part["size_bytes"], progress=progress, operation=operation)


def _seal(plan):
    plan["plan_id"] = digest(plan)
    plan["confirmation_token"] = plan["operation"].upper() + "-" + plan["plan_id"][:16]
    return plan


def _plan_base(context, operation, parts, repo, tools):
    return {"schema": PLAN_SCHEMA, "workflow": WORKFLOW, "operation": operation,
            "mode": context.mode, "device": context.device, "context_contract": context.contract,
            "boot_id": qualified.boot_session(context), "profile": "linux", "partitions": parts,
            "repository": repo, "repository_pin": _repo_pin(context, repo),
            "toolchain_sha256": tools["manifest_sha256"],
            "qualification": qualified.admission(context, parts, tools),
            "created_utc": utc(), "expires_unix": time.time() + (BACKUP_TTL if operation == "backup" else DESTRUCTIVE_TTL),
            "destructive": operation != "backup", "required_hold_ms": 0 if operation == "backup" else 3000,
            "backup_schema": MANIFEST_SCHEMA, "data_format": DATA_FORMAT,
            "full_device_backup": False, "auto_reboot": False}


def _check_plan(context, plan, operation, confirmation, tools):
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA or plan.get("workflow") != WORKFLOW or plan.get("operation") != operation:
        fail("INVALID_PLAN", "Only the explicit Linux v2 workflow is admitted here")
    unsealed = {k: v for k, v in plan.items() if k not in {"plan_id", "confirmation_token"}}
    expected = digest(unsealed)
    if plan.get("plan_id") != expected or plan.get("confirmation_token") != operation.upper() + "-" + expected[:16]:
        fail("PLAN_CHANGED", "The reviewed Linux plan changed")
    if confirmation != plan["confirmation_token"]:
        fail("CONFIRMATION_MISMATCH", "Confirmation does not identify this exact Linux plan")
    if not isinstance(plan.get("expires_unix"), (int, float)) or time.time() > plan["expires_unix"]:
        fail("PLAN_EXPIRED", "Create and review a fresh Linux plan")
    if plan.get("mode") != context.mode or plan.get("device") != context.device:
        fail("WRONG_DEVICE", "Linux plan belongs to another device or execution mode")
    if plan.get("context_contract") != context.contract or plan.get("boot_id") != qualified.boot_session(context):
        fail("PLAN_STALE", "Device contract or recovery boot changed after planning")
    if plan.get("profile") != "linux" or plan.get("data_format") != DATA_FORMAT or plan.get("backup_schema") != MANIFEST_SCHEMA:
        fail("INVALID_PLAN", "Linux v2 cannot be used for Android or arbitrary raw partitions")
    parts = _parts(context)
    if parts != plan.get("partitions"):
        fail("PLAN_STALE", "Target identity, geometry or regular-file contents changed after planning")
    repo = context.repository(plan["repository"]["path"], writing=True)
    if repo != plan["repository"] or _repo_pin(context, repo) != plan.get("repository_pin"):
        fail("PLAN_STALE", "External backup medium, mount or directory changed after planning")
    _offline(context, parts, repo)
    if plan.get("toolchain_sha256") != tools["manifest_sha256"]:
        fail("PLAN_STALE", "The pinned Linux toolchain changed after planning")
    if operation != "backup":
        qualified.require_admission(context, parts, tools, plan["qualification"])
        if plan.get("required_hold_ms") != 3000 or plan.get("destructive") is not True:
            fail("INVALID_PLAN", "Destructive Linux plan lacks its explicit review contract")
    if plan.get("can_execute") is not True:
        fail("PLAN_BLOCKED", "The reviewed plan has unresolved blockers", blockers=plan.get("blockers", []))
    return parts, repo


class _CountingSink:
    def __init__(self):
        self.count = 0
    def write(self, value):
        self.count += len(value)
        return len(value)
    def flush(self):
        pass


def _gzip_boot(source, part, sink, progress=None, operation="backup-plan", guard=None):
    source.seek(0)
    count, raw_hash = 0, hashlib.sha256()
    with gzip.GzipFile(filename="", fileobj=sink, mode="wb", compresslevel=1, mtime=0) as compressor:
        while count < part["size_bytes"]:
            if guard:
                guard()
            data = source.read(min(CHUNK, part["size_bytes"] - count))
            if not data:
                fail("SOURCE_SHORT_READ", "BOOT ended before its admitted length")
            compressor.write(data)
            raw_hash.update(data)
            count += len(data)
            _emit(progress, operation, "compress-boot", "boot", count, part["size_bytes"])
    if source.read(1) or stamp(source.fileno()) != part["stamp"]:
        fail("SOURCE_CHANGED", "BOOT changed during compression")
    return raw_hash.hexdigest()


def _same_source(context, parts, repo, pin):
    fresh = context.refresh()
    if fresh.contract != context.contract or fresh.device != context.device:
        fail("STORAGE_CHANGED", "Storage contract changed during the operation")
    actual = _parts(fresh)
    if actual != parts:
        fail("SOURCE_CHANGED", "Pinned source identity or contents changed during the operation")
    actual_repo = fresh.repository(repo["path"], writing=True)
    if actual_repo != repo or _repo_pin(fresh, actual_repo) != pin:
        fail("STORAGE_CHANGED", "External backup storage changed")
    _offline(fresh, actual, actual_repo)


def make_backup_plan(context, repository, name, progress=None):
    validate_name(name)
    with operation_lock(context) as lease:
        context = context.refresh()
        parts = _parts(context)
        repo = context.repository(repository, writing=True)
        _offline(context, parts, repo)
        tools = qualified.toolchain(context)
        pin = _repo_pin(context, repo)
        destination = Path(repo["path"]) / name
        if os.path.lexists(destination):
            fail("BACKUP_EXISTS", "Backup name already exists", path=str(destination))
        free = _free(repo)
        with contextlib.ExitStack() as stack:
            sources = {p["label"]: stack.enter_context(context.open_partition(p)) for p in parts}
            # Stop as soon as the archive cannot fit, instead of finishing a long measuring pass.
            budget = max(1, min(free - RESERVE - METADATA_ALLOWANCE, _compact().MAX_ARCHIVE_BYTES))
            try:
                estimate = _compact().estimate(sources["userdata"].fileno(), parts[1]["size_bytes"],
                                               progress=progress, tool_paths=tools["paths"], inherited_fds=(lease,),
                                               max_archive_bytes=budget)
            except RecoveryError as error:
                if error.code == "BACKUP_SPACE_LIMIT":
                    fail("INSUFFICIENT_SPACE", "The backup does not fit on this storage. Choose one with more free space",
                         free_bytes=free, reserve_bytes=RESERVE)
                raise
            counter = _CountingSink()
            boot_sha = _gzip_boot(sources["boot"], parts[0], counter, progress)
        _same_source(context, parts, repo, pin)
        archive_bytes = estimate["archive_bytes"] + counter.count + METADATA_ALLOWANCE
        free = shutil.disk_usage(repo["path"]).free
        blockers = ["INSUFFICIENT_SPACE"] if free < archive_bytes + RESERVE else []
        plan = _plan_base(context, "backup", parts, repo, tools)
        plan.update(name=name, backup_path=str(destination), snapshot_id=str(uuid.uuid4()),
                    compact_estimate=estimate, source_boot_sha256=boot_sha,
                    boot_action="read-only-backup", write_targets=[], can_execute=not blockers, blockers=blockers,
                    source_formats={"userdata": estimate["source_probe"]},
                    space={"userdata_partition_bytes": parts[1]["size_bytes"],
                           "userdata_used_bytes": estimate["partclone"]["used_blocks"] * estimate["partclone"]["block_size"],
                           "estimated_archive_bytes": archive_bytes, "free_bytes": free, "reserve_bytes": RESERVE})
        return _seal(plan)


def _manifest(context, plan, entries):
    return {"schema": MANIFEST_SCHEMA, "workflow": WORKFLOW, "data_format": DATA_FORMAT,
            "snapshot_id": plan["snapshot_id"], "created_utc": utc(), "profile": "linux",
            "device": context.device, "source_mode": context.mode, "source_contract": context.contract,
            "source_geometry": qualified.geometry_record(plan["partitions"]), "source_boot_id": plan["boot_id"],
            "full_device_backup": False, "entries": entries, "source_formats": plan["source_formats"],
            "consistency": {"sources_unmounted": True, "no_holders": True,
                            "all_namespaces_checked": context.mode == "device", "fixture_simulation": context.mode == "fixture"},
            "limitations": ["Free userdata blocks are intentionally omitted; this is not a forensic raw image.",
                            "BOOT plus ext4 userdata only; no Android, bootloader, recovery, EFS or RPMB restore.",
                            "No secure erasure, atomic restore or automatic reboot."]}


def _linux_boot_in_backup(directory, entry):
    """Whether a backup's BOOT holds a complete Linux BOOT for this phone (2026-11-10: judged by its
    contents with bootimage.py instead of a list of tested hashes)."""
    try:
        with gzip.open(Path(directory) / entry["archive"], "rb") as archive:
            raw = archive.read(int(entry["raw_bytes"]) + 1)
    except (OSError, EOFError, ValueError, KeyError, TypeError):
        return False
    if len(raw) != entry["raw_bytes"] or hashlib.sha256(raw).hexdigest() != entry.get("raw_sha256"):
        return False
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import bootimage
    return bootimage.linux_release(raw) is not None


def load_manifest(context, backup_path):
    path = context.user_path(backup_path)
    if not path.is_dir() or ".partial-" in path.name:
        fail("INCOMPLETE_BACKUP", "A complete Linux snapshot directory is required")
    manifest, manifest_sha = read_json_with_hash(path / "manifest.json")
    complete = read_json(path / "COMPLETE.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("workflow") != WORKFLOW or manifest.get("data_format") != DATA_FORMAT:
        fail("INVALID_MANIFEST", "Unsupported Linux used-block manifest")
    if (not isinstance(complete, dict) or complete.get("schema") != "a22x.complete.v2"
            or complete.get("manifest_sha256") != manifest_sha or complete.get("all_entries_verified") is not True):
        fail("MANIFEST_HASH_MISMATCH", "Linux backup completion does not seal this fully verified manifest")
    if manifest.get("profile") != "linux" or manifest.get("full_device_backup") is not False:
        fail("INVALID_MANIFEST", "Only the Linux BOOT/ext4 userdata profile is admitted")
    if (not isinstance(manifest.get("snapshot_id"), str) or not manifest["snapshot_id"]
            or len(manifest["snapshot_id"]) > 64 or complete.get("snapshot_id") != manifest["snapshot_id"]):
        fail("MIXED_SNAPSHOT", "Missing or inconsistent Linux snapshot identity")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != 2 or not all(isinstance(e, dict) for e in entries) or [e.get("label") for e in entries] != list(LABELS):
        fail("PROFILE_PARTITION_MISMATCH", "Linux v2 requires exactly BOOT then userdata entries")
    validate_raw_entry(entries[0])
    if entries[0].get("compression") != "gzip":
        fail("INVALID_MANIFEST", "The qualified Linux BOOT encoding is gzip")
    # Structural validation must not look up or execute any tool.
    _compact().validate_entry(entries[1])
    for entry in entries:
        if entry.get("snapshot_id") != manifest["snapshot_id"]:
            fail("MIXED_SNAPSHOT", "Linux partition entry belongs to another snapshot")
        context.user_path(path / entry["archive"])
    if not isinstance(manifest.get("device"), dict) or manifest.get("source_mode") not in {"device", "fixture"}:
        fail("INVALID_MANIFEST", "Linux snapshot lacks a source device identity")
    if manifest.get("source_mode") == "device":
        if manifest["device"].get("model") != "SM-A226B" or not str(manifest["device"].get("identity", "")).startswith("emmc-cid-sha256:"):
            fail("INVALID_MANIFEST", "Physical Linux snapshot must identify the A226B eMMC")
    return path, manifest, manifest_sha


def _archives(stack, directory, manifest, parts, tools, lease):
    admitted = {p["label"]: p for p in parts}
    result = {}
    for entry in manifest["entries"]:
        label = entry["label"]
        if label == "boot":
            archive = VerifiedArchive(directory, entry, target_bytes=admitted[label]["size_bytes"])
        else:
            archive = _compact().VerifiedPartclone(directory, entry, target_bytes=admitted[label]["size_bytes"],
                                                   tool_paths=tools["paths"], inherited_fds=(lease,) if lease is not None else ())
        result[label] = stack.enter_context(archive)
        if any((archive.initial_stamp["device"], archive.initial_stamp["inode"]) ==
               (p["stamp"]["device"], p["stamp"]["inode"]) for p in parts if "stamp" in p):
            fail("ARCHIVE_TARGET_ALIAS", "Backup archive aliases a Linux target")
    return result


def _verify_archives(archives, progress, operation, expected=None):
    result = {}
    for label in LABELS:
        result[label] = archives[label].verify(progress, operation=operation, phase="verify-before-write", collect_chunks=True)
        if expected is not None and result[label] != expected[label]:
            fail("PLAN_STALE", "Linux backup archive changed since review", label=label)
    return result


def verify_backup(context, backup_path, progress=None):
    with operation_lock(context) as lease:
        context = context.refresh()
        path, manifest, manifest_sha = load_manifest(context, backup_path)
        tools = qualified.toolchain(context)
        # Verification may be requested with the target mounted; it reads only
        # the pinned archive and does not claim current target consistency.
        parts = [{"label": e["label"], "size_bytes": e["raw_bytes"]} for e in manifest["entries"]]
        with contextlib.ExitStack() as stack:
            archives = _archives(stack, path, manifest, parts, tools, lease)
            stamps = _verify_archives(archives, progress, "verify")
        return {"ok": True, "operation": "verify", "workflow": WORKFLOW, "backup_path": str(path),
                "verified": True, "manifest_sha256": manifest_sha, "manifest": manifest,
                "archive_stamps": stamps, "auto_reboot": False}


def backup(context, plan, confirmation, progress=None):
    with operation_lock(context) as lease:
        context = context.refresh()
        tools = qualified.toolchain(context)
        parts, repo = _check_plan(context, plan, "backup", confirmation, tools)
        _free(repo, plan["space"]["estimated_archive_bytes"])
        destination = Path(repo["path"]) / plan["name"]
        validate_name(plan["name"])
        if str(destination) != plan["backup_path"] or os.path.lexists(destination):
            fail("BACKUP_EXISTS", "Reviewed backup destination is invalid or already exists")
        temporary = destination.parent / ("." + plan["name"] + ".partial-" + plan["snapshot_id"])
        temporary.mkdir(mode=0o700)
        published = False
        try:
            with contextlib.ExitStack() as stack:
                sources = {p["label"]: stack.enter_context(context.open_partition(p)) for p in parts}
                boot_path = temporary / "boot.img.gz"
                with boot_path.open("xb", buffering=0) as output:
                    os.chmod(boot_path, 0o600)
                    boot_sha = _gzip_boot(sources["boot"], parts[0], output, progress, "backup", lambda: _free(repo))
                    os.fsync(output.fileno())
                if boot_sha != plan["source_boot_sha256"]:
                    fail("SOURCE_CHANGED", "BOOT differs from the reviewed compact backup plan")
                boot_archive_sha, boot_bytes = hash_file(boot_path, regular_only=True)
                boot = {"label": "boot", "archive": "boot.img.gz", "compression": "gzip",
                        "raw_bytes": parts[0]["size_bytes"], "raw_sha256": boot_sha,
                        "archive_bytes": boot_bytes, "archive_sha256": boot_archive_sha}
                data_path = temporary / "userdata.partclone.zst"
                fd = os.open(data_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    data = _compact().create(sources["userdata"].fileno(), fd, parts[1]["size_bytes"], progress=progress,
                                             tool_paths=tools["paths"], inherited_fds=(lease,),
                                             max_archive_bytes=plan["compact_estimate"]["archive_bytes"] + METADATA_ALLOWANCE,
                                             reserve_bytes=RESERVE)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if data["native_body_sha256"] != plan["compact_estimate"]["native_body_sha256"] or data["source_probe"] != plan["compact_estimate"]["source_probe"]:
                    fail("SOURCE_CHANGED", "userdata allocated contents changed since compact backup planning")
                entries = [dict(entry, snapshot_id=plan["snapshot_id"]) for entry in (boot, data)]
                manifest = _manifest(context, plan, entries)
                archives = _archives(stack, temporary, manifest, parts, tools, lease)
                _verify_archives(archives, progress, "backup")
                _same_source(context, parts, repo, plan["repository_pin"])
                write_json_new(temporary / "manifest.json", manifest)
                manifest_sha = hash_file(temporary / "manifest.json", regular_only=True)[0]
                write_json_new(temporary / "COMPLETE.json", {"schema": "a22x.complete.v2", "snapshot_id": plan["snapshot_id"],
                                                            "manifest_sha256": manifest_sha, "verified_utc": utc(),
                                                            "all_entries_verified": True})
                fsync_directory(temporary)
                rename_noreplace(temporary, destination)
                published = True
            return {"ok": True, "operation": "backup", "workflow": WORKFLOW, "backup_path": str(destination),
                    "snapshot_id": plan["snapshot_id"], "profile": "linux", "verified": True,
                    "raw_bytes": sum(e["raw_bytes"] for e in entries), "archive_bytes": sum(e["archive_bytes"] for e in entries),
                    "userdata_used_bytes": data["partclone"]["used_blocks"] * data["partclone"]["block_size"], "auto_reboot": False}
        except BaseException as exc:
            if not published and temporary.exists():
                with contextlib.suppress(OSError):
                    (temporary / "COMPLETE.json").unlink(missing_ok=True)
                    write_json_new(temporary / "FAILED.json", {"schema": "a22x.failed.v2", "utc": utc(),
                                                              "snapshot_id": plan["snapshot_id"], "restorable": False,
                                                              "error": getattr(exc, "code", type(exc).__name__)})
            if isinstance(exc, RecoveryError):
                exc.details["partial_path"] = str(temporary)
            raise


def make_destructive_plan(context, backup_path, operation, progress=None):
    if operation not in {"wipe", "restore"}:
        fail("INVALID_OPERATION", "Only wipe or restore uses the destructive Linux planner")
    with operation_lock(context) as lease:
        context = context.refresh()
        path, manifest, manifest_sha = load_manifest(context, backup_path)
        if manifest["device"] != context.device or manifest["source_mode"] != context.mode or manifest.get("source_contract") != context.contract:
            fail("WRONG_DEVICE", "Linux backup does not belong to this exact device/storage contract")
        parts = _parts(context)
        if qualified.geometry_record(parts) != manifest.get("source_geometry"):
            fail("TARGET_SIZE_MISMATCH", "Linux backup partition identity and geometry differ from this device")
        for part, entry in zip(parts, manifest["entries"]):
            if part["size_bytes"] != entry["raw_bytes"]:
                fail("TARGET_SIZE_MISMATCH", "Backup and target partition sizes differ")
        repo = context.repository(path.parent, writing=True)
        _offline(context, parts, repo)
        _free(repo, METADATA_ALLOWANCE)
        tools = qualified.toolchain(context)
        pin = _repo_pin(context, repo)
        qualification = qualified.admission(context, parts, tools)
        with contextlib.ExitStack() as stack:
            archives = _archives(stack, path, manifest, parts, tools, lease)
            archive_stamps = _verify_archives(archives, progress, operation + "-plan")
        boot_sha = _boot_hash(context, parts[0], progress, operation + "-plan")
        backup_boot_sha = manifest["entries"][0]["raw_sha256"]
        blockers = list(qualification["blockers"])
        if context.mode == "device" and qualification["eligible"] and backup_boot_sha not in (qualification["linux_boot_sha256"], LINUX_BOOT_K108, LINUX_BOOT_CHARGER_OFF, LINUX_BOOT_K121, LINUX_BOOT_K116, LINUX_BOOT_K122, LINUX_BOOT_K138) \
                and not _linux_boot_in_backup(path, manifest["entries"][0]):
            blockers.append("LINUX_BOOT_NOT_QUALIFIED")
        if operation == "wipe":
            if boot_sha != backup_boot_sha:
                fail("BACKUP_NO_LONGER_CURRENT", "Wipe requires a verified backup of the current BOOT")
            with context.open_partition(parts[1]) as source:
                _compact().verify_target(source.fileno(), manifest["entries"][1], progress,
                                         tool_paths=tools["paths"], inherited_fds=(lease,))
            boot_action = "preserve"
        else:
            boot_action = "skip-identical" if boot_sha == backup_boot_sha else "restore-last"
        _same_source(context, parts, repo, pin)
        plan = _plan_base(context, operation, parts, repo, tools)
        data = manifest["entries"][1]
        targets = [{"label": "userdata", "path": parts[1]["path"], "size_bytes": parts[1]["size_bytes"],
                    "action": "format-ext4" if operation == "wipe" else "restore-used-blocks"}]
        if boot_action == "restore-last":
            targets.append({"label": "boot", "path": parts[0]["path"], "size_bytes": parts[0]["size_bytes"], "action": "restore-raw-last"})
        plan.update(backup_path=str(path), manifest_sha256=manifest_sha, archive_stamps=archive_stamps,
                    source_formats=manifest.get("source_formats", {}), qualification=qualification,
                    boot_action=boot_action, source_boot_sha256=boot_sha, write_targets=targets,
                    target_fingerprints={p["label"]: _fingerprint(context, p) for p in parts},
                    write_order=[p["label"] for p in targets], can_execute=not blockers and qualification["eligible"], blockers=blockers,
                    space={"userdata_partition_bytes": parts[1]["size_bytes"],
                           "userdata_used_bytes": data["partclone"]["used_blocks"] * data["partclone"]["block_size"],
                           "estimated_archive_bytes": sum(e["archive_bytes"] for e in manifest["entries"]),
                           "free_bytes": shutil.disk_usage(repo["path"]).free, "reserve_bytes": RESERVE},
                    atomic_restore=False, secure_erasure=False)
        if operation == "wipe":
            plan["new_filesystem_uuid"] = str(uuid.uuid4())
        return _seal(plan)


def plan_result(plan, path):
    fields = ("workflow", "backup_schema", "data_format", "destructive", "required_hold_ms", "qualification",
              "boot_action", "write_targets", "space", "source_formats")
    summary = {name: plan[name] for name in fields}
    summary.update(profile="linux", partitions=list(LABELS), raw_bytes=sum(p["size_bytes"] for p in plan["partitions"]),
                   compressed_estimate_bytes=plan["space"]["estimated_archive_bytes"], backup_path=plan["backup_path"],
                   full_device_backup=False, requires_retained_firmware=False, auto_reboot=False,
                   secure_erasure=False, atomic_restore=False)
    return {"ok": True, "schema": PLAN_SCHEMA, "operation": plan["operation"] + "-plan", "plan_path": str(path),
            "plan_id": plan["plan_id"], "confirmation_token": plan["confirmation_token"],
            "can_execute": plan["can_execute"], "blockers": plan["blockers"], "summary": summary}


def capabilities(context):
    try:
        tools = qualified.toolchain(context)
        qualification = qualified.admission(context, tools=tools)
        available = True
    except (RecoveryError, OSError, ValueError, KeyError, TypeError) as exc:
        available = False
        qualification = {"status": "unqualified", "eligible": False, "capsule_id": None,
                         "blockers": [getattr(exc, "code", "LINUX_TOOLCHAIN_UNAVAILABLE")], "linux_boot_sha256": None}
    return {"linux_workflow": WORKFLOW, "linux_used_block_backup": available,
            "linux_restore": available and qualification["eligible"],
            "linux_wipe": available and qualification["eligible"], "linux_qualification": qualification}


class _Journal:
    """Mandatory append-only intent/results on the separate durable SD."""
    def __init__(self, repo, plan):
        base = Path(repo["path"]) / ".a22x-linux-operations"
        no_symlinks(base, must_exist=False)
        base.mkdir(mode=0o700, exist_ok=True)
        st = base.stat(follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
            fail("UNSAFE_JOURNAL_DIRECTORY", "Linux operation journal directory is not private")
        self.path = base / plan["plan_id"]
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError:
            fail("LINUX_OPERATION_ALREADY_STARTED", "This exact plan already has an intent journal; review a new plan")
        fsync_directory(base)
        fsync_directory(base.parent)
        self.sequence = 0
        write_json_new(self.path / "plan.json", plan)

    def checkpoint(self, phase, receipt):
        write_json_new(self.path / (f"{self.sequence:04d}-" + phase + ".json"),
                       {**receipt, "checkpoint_sequence": self.sequence, "checkpoint_phase": phase})
        self.sequence += 1

    def finish(self, receipt):
        write_json_new(self.path / "result.json", receipt)


def _check_target_fd(target, part, *, physical):
    st = os.fstat(target.fileno())
    named = os.stat(part["path"], follow_symlinks=False)
    expected = part["stamp"]
    if ((st.st_dev, st.st_ino, st.st_rdev) != (expected["device"], expected["inode"], expected["rdev"])
            or (named.st_dev, named.st_ino, named.st_rdev) != (st.st_dev, st.st_ino, st.st_rdev)):
        fail("PARTITION_CHANGED", "Pinned Linux target identity changed", label=part["label"])
    if physical:
        if not stat.S_ISBLK(st.st_mode) or not stat.S_ISBLK(named.st_mode):
            fail("NOT_BLOCK_DEVICE", "A qualified device target must remain its exact block partition")
        size = struct.unpack("=Q", fcntl.ioctl(target.fileno(), BLKGETSIZE64, bytes(8)))[0]
        if struct.unpack("=i", fcntl.ioctl(target.fileno(), BLKROGET, bytes(4)))[0]:
            fail("TARGET_READ_ONLY", "An existing block read-only guard is never overridden by Linux restore")
    else:
        if not stat.S_ISREG(st.st_mode) or not stat.S_ISREG(named.st_mode):
            fail("REGULAR_FILE_REQUIRED", "Fixture/qualification writers can write only regular files")
        size = st.st_size
    if size != part["size_bytes"]:
        fail("TARGET_SIZE_CHANGED", "Linux target size changed", label=part["label"])


@contextlib.contextmanager
def _open_target(context, part, tools, plan):
    if part["label"] not in {p["label"] for p in plan["write_targets"]} or part["label"] not in LABELS:
        fail("LINUX_TARGET_FORBIDDEN", "This reviewed Linux operation does not write that partition")
    if plan["operation"] == "wipe" and part["label"] != "userdata":
        fail("LINUX_TARGET_FORBIDDEN", "Wipe can only format userdata; BOOT and RECOVERY are preserved")
    if context.mode == "fixture":
        with context.open_partition(part, write=True) as target:
            _check_target_fd(target, part, physical=False)
            yield target
        return
    # There is deliberately no call to DeviceContext.open_partition(write=True)
    # and no enable flag on that generic raw/Android API.
    qualified.require_admission(context, _parts(context), tools, plan["qualification"])
    number, size = qualified.GEOMETRY[part["label"]]
    if part.get("partition_number") != number or part["size_bytes"] != size:
        fail("LINUX_TARGET_FORBIDDEN", "Physical target is outside the fixed Linux partition allowlist")
    fd = os.open(part["path"], os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
    try:
        target = os.fdopen(fd, "r+b", buffering=0, closefd=False)
        _check_target_fd(target, part, physical=True)
        if stamp(fd) != part["stamp"]:
            fail("PARTITION_CHANGED", "Physical target changed before the exclusive writer opened")
        yield target
    finally:
        if "target" in locals():
            target.close()
        os.close(fd)


class _TargetGuard:
    def __init__(self, context, parts, repo, plan, targets):
        self.context, self.parts, self.repo, self.plan, self.targets = context, parts, repo, plan, targets
        self.last_full = 0.0

    def __call__(self, force=False):
        for label, target in self.targets.items():
            part = next(p for p in self.parts if p["label"] == label)
            _check_target_fd(target, part, physical=self.context.mode == "device")
        now = time.monotonic()
        if not force and now - self.last_full < 0.5:
            return
        self.last_full = now
        fresh = self.context.refresh()
        if fresh.device != self.context.device or fresh.contract != self.context.contract or qualified.boot_session(fresh) != self.plan["boot_id"]:
            fail("STORAGE_CHANGED", "Recovery/device identity changed while Linux targets were open")
        current = _parts(fresh)
        if [_identity(p) for p in current] != [_identity(p) for p in self.parts]:
            fail("PARTITION_CHANGED", "Linux target geometry changed while writing")
        repo = fresh.repository(self.repo["path"], writing=True)
        if repo != self.repo or _repo_pin(fresh, repo) != self.plan["repository_pin"]:
            fail("STORAGE_CHANGED", "Backup storage identity or mount changed during the Linux operation")
        _offline(fresh, current, repo)
        _free(repo, METADATA_ALLOWANCE)


class _WriterChild:
    """Pinned tool, inherited storage lease, bounded diagnostics, no shell."""
    def __init__(self, argv, *, inherited_fds, input_pipe, mkfs_config=None):
        env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}
        if mkfs_config:
            env["MKE2FS_CONFIG"] = mkfs_config
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0,
                                        close_fds=True, pass_fds=tuple(sorted(set(inherited_fds))), env=env)
        self.diagnostic = bytearray()
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        if input_pipe:
            os.set_blocking(self.process.stdin.fileno(), False)

    def _drain(self):
        try:
            while True:
                block = self.process.stderr.read(8192)
                if not block:
                    return
                left = 16 * 1024 - len(self.diagnostic)
                if left > 0:
                    self.diagnostic.extend(block[:left])
        except (OSError, ValueError):
            return

    def write(self, block, guard):
        view, deadline = memoryview(block), time.monotonic() + 120
        while view:
            guard()
            if self.process.poll() is not None:
                self.wait(guard, 1)
                fail("RESTORE_TOOL_EARLY_EXIT", "Partclone exited before all verified image bytes were delivered")
            if time.monotonic() >= deadline:
                fail("RESTORE_TOOL_TIMEOUT", "Native restore stopped consuming verified image bytes")
            _, writable, _ = select.select([], [self.process.stdin], [], 0.5)
            if not writable:
                continue
            try:
                count = os.write(self.process.stdin.fileno(), view)
            except BlockingIOError:
                continue
            if count <= 0:
                fail("RESTORE_TOOL_SHORT_WRITE", "Native restore input pipe made no progress")
            view = view[count:]

    def wait(self, guard, timeout=120):
        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            guard()
            if time.monotonic() >= deadline:
                fail("RESTORE_TOOL_TIMEOUT", "The Linux target-writing tool did not finish")
            try:
                self.process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        self.reader.join(timeout=2)
        if self.process.returncode:
            fail("RESTORE_TOOL_FAILED", "The Linux target-writing tool reported a failure",
                 exit_code=self.process.returncode, diagnostic=bytes(self.diagnostic).decode("utf-8", "replace"))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stderr):
            if stream:
                stream.close()


def _write_native(archive, target, part, tools, lease, state, guard, progress=None):
    """Partclone consumes only bytes derived from prechecked encoded chunks."""
    guard(True)
    fd = target.fileno()
    argv = [tools["paths"]["partclone.restore"], "--source", "-", "--overwrite", f"/proc/self/fd/{fd}",
            "--logfile", "/dev/null", "--buffer_size", str(CHUNK), "--quiet"]
    inherited = (fd,) + ((lease,) if lease is not None else ())
    state["write_started"] = True
    child = _WriterChild(argv, inherited_fds=inherited, input_pipe=True)
    try:
        with contextlib.closing(archive.verified_chunks()) as chunks:
            for block in chunks:
                if state["native_bytes_delivered"] + len(block) > archive.entry["native_image_bytes"]:
                    fail("TARGET_BOUNDS", "Native restore input exceeds its verified exact image length")
                child.write(block, guard)
                state["native_bytes_delivered"] += len(block)
                state["remaining_native_bytes"] = archive.entry["native_image_bytes"] - state["native_bytes_delivered"]
                _emit(progress, "restore", "write-used-blocks", "userdata", state["native_bytes_delivered"], archive.entry["native_image_bytes"])
        if state["native_bytes_delivered"] != archive.entry["native_image_bytes"]:
            fail("TARGET_SHORT_WRITE", "Native restore image was not completely delivered")
        child.process.stdin.close()
        child.wait(guard)
        guard(True)
        os.fsync(fd)
        state["fsync_completed"] = True
    finally:
        child.close()
    guard(True)
    verified = _compact().verify_target(fd, archive.entry, progress, tool_paths=tools["paths"],
                                        inherited_fds=(lease,) if lease is not None else ())
    guard(True)
    state.update(readback_verified=True, readback=verified)
    return verified


def _write_raw(archive, target, part, state, guard, progress=None):
    guard(True)
    count = 0
    state["write_started"] = True
    with contextlib.closing(archive.verified_chunks()) as chunks:
        for block in chunks:
            if count + len(block) > part["size_bytes"]:
                fail("TARGET_BOUNDS", "BOOT restore would exceed the admitted partition")
            view = memoryview(block)
            while view:
                guard()
                written = os.pwrite(target.fileno(), view, count)
                if not 0 < written <= len(view):
                    fail("TARGET_SHORT_WRITE", "BOOT write made no valid progress")
                count += written
                view = view[written:]
                state["verified_prefix_bytes"] = count
            _emit(progress, "restore", "write-boot", "boot", count, part["size_bytes"])
    if count != part["size_bytes"]:
        fail("TARGET_SHORT_WRITE", "BOOT restore did not write its exact admitted length")
    guard(True)
    os.fsync(target.fileno())
    state["fsync_completed"] = True
    before_readback = stamp(target.fileno())
    actual = _hash_fd(target.fileno(), part["size_bytes"], progress=progress, operation="restore", phase="readback-boot")
    guard(True)
    if stamp(target.fileno()) != before_readback:
        fail("PARTITION_CHANGED", "BOOT changed during its independent readback")
    if actual != archive.entry["raw_sha256"]:
        fail("RESTORE_READBACK_MISMATCH", "BOOT readback differs from the verified Linux image")
    state.update(readback_verified=True, readback_sha256=actual)


def _format_userdata(target, part, tools, lease, plan, state, guard, progress):
    block_size = 4096
    if part["size_bytes"] % block_size:
        fail("GEOMETRY_MISMATCH", "Qualified userdata format requires exact 4096-byte block geometry")
    guard(True)
    fd = target.fileno()
    argv = [tools["paths"]["mkfs.ext4"], "-t", "ext4", "-F", "-q", "-b", str(block_size),
            "-U", plan["new_filesystem_uuid"], "-L", "linux-wiped", "-E",
            "nodiscard,lazy_itable_init=0,lazy_journal_init=0", f"/proc/self/fd/{fd}", str(part["size_bytes"] // block_size)]
    state["write_started"] = True
    child = _WriterChild(argv, inherited_fds=(lease, fd), input_pipe=False, mkfs_config=tools["mkfs_config"])
    try:
        child.wait(guard, timeout=1800)
    finally:
        child.close()
    guard(True)
    os.fsync(fd)
    state["fsync_completed"] = True
    probe = _compact().probe_source(fd, part["size_bytes"], progress=progress, tool_paths=tools["paths"], inherited_fds=(lease,))
    if (probe["uuid"] != plan["new_filesystem_uuid"] or probe["block_size"] != block_size
            or probe["block_count"] * block_size != part["size_bytes"]):
        fail("WIPE_READBACK_MISMATCH", "New ext4 UUID or exact filesystem geometry differs from the reviewed format")
    guard(True)
    state.update(readback_verified=True, new_filesystem=probe, secure_erasure=False)


def execute(context, plan, confirmation, progress=None):
    operation = plan.get("operation") if isinstance(plan, dict) else None
    if operation not in {"restore", "wipe"}:
        fail("INVALID_OPERATION", "A reviewed Linux wipe or restore plan is required")
    with operation_lock(context) as lease:
        context = context.refresh()
        tools = qualified.toolchain(context)
        parts, repo = _check_plan(context, plan, operation, confirmation, tools)
        path, manifest, manifest_sha = load_manifest(context, plan["backup_path"])
        if (manifest_sha != plan["manifest_sha256"] or manifest["device"] != context.device
                or manifest["source_mode"] != context.mode or manifest.get("source_contract") != context.contract
                or manifest.get("source_geometry") != qualified.geometry_record(parts)):
            fail("PLAN_STALE", "The sealed source backup or target contract changed after review")
        backup_boot = manifest["entries"][0]["raw_sha256"]
        if context.mode == "device" and backup_boot not in (plan["qualification"]["linux_boot_sha256"], LINUX_BOOT_K108, LINUX_BOOT_CHARGER_OFF, LINUX_BOOT_K116, LINUX_BOOT_K121, LINUX_BOOT_K122, LINUX_BOOT_K138) \
                and not _linux_boot_in_backup(path, manifest["entries"][0]):
            fail("LINUX_BOOT_NOT_QUALIFIED", "This backup BOOT image is outside the tested Linux boot allowlist")
        for part in parts:
            if _fingerprint(context, part) != plan["target_fingerprints"][part["label"]]:
                fail("PLAN_STALE", "Target boundary signatures changed after review", label=part["label"])
        if _boot_hash(context, parts[0], progress, operation) != plan["source_boot_sha256"]:
            fail("PLAN_STALE", "Current BOOT changed after review")
        with contextlib.ExitStack() as stack:
            archives = _archives(stack, path, manifest, parts, tools, lease)
            _verify_archives(archives, progress, operation, plan["archive_stamps"])
            # Wipe is gated by a current complete copy, not merely any old valid
            # archive. Re-clone/read-only verification catches intervening edits.
            if operation == "wipe":
                if backup_boot != plan["source_boot_sha256"]:
                    fail("BACKUP_NO_LONGER_CURRENT", "Wipe requires an unchanged saved BOOT")
                with context.open_partition(parts[1]) as source:
                    _compact().verify_target(source.fileno(), manifest["entries"][1], progress,
                                             tool_paths=tools["paths"], inherited_fds=(lease,))
            _same_source(context, parts, repo, plan["repository_pin"])
            # Revalidate the exact tested code/runtime immediately before the
            # first target can be opened for writing, after all long reads.
            qualified.require_admission(context, parts, qualified.toolchain(context), plan["qualification"])
            expected_action = "preserve" if operation == "wipe" else ("skip-identical" if backup_boot == plan["source_boot_sha256"] else "restore-last")
            expected_targets = [{"label": "userdata", "path": parts[1]["path"], "size_bytes": parts[1]["size_bytes"],
                                 "action": "format-ext4" if operation == "wipe" else "restore-used-blocks"}]
            if expected_action == "restore-last":
                expected_targets.append({"label": "boot", "path": parts[0]["path"], "size_bytes": parts[0]["size_bytes"], "action": "restore-raw-last"})
            if plan["boot_action"] != expected_action or plan["write_targets"] != expected_targets or plan["write_order"] != [p["label"] for p in expected_targets]:
                fail("INVALID_PLAN", "Destructive target order differs from the exact admitted Linux operation")
            journal = _Journal(repo, plan)
            states = {"userdata": {"write_started": False, "fsync_completed": False, "readback_verified": False,
                                   "native_bytes_delivered": 0, "remaining_native_bytes": manifest["entries"][1]["native_image_bytes"]},
                      "boot": {"write_started": False, "fsync_completed": False, "readback_verified": expected_action != "restore-last",
                               "verified_prefix_bytes": 0, "action": expected_action}}
            receipt = {"schema": "a22x.linux.operation.receipt.v1", "workflow": WORKFLOW, "operation": operation,
                       "plan_id": plan["plan_id"], "capsule_id": plan["qualification"]["capsule_id"],
                       "mode": context.mode, "started_utc": utc(), "boot_id": plan["boot_id"],
                       "backup_path": str(path), "manifest_sha256": manifest_sha,
                       "journal_path": str(journal.path), "status": "started", "active_partition": None,
                       "physical_device_written": False, "all_archives_verified_before_first_write": True,
                       "partitions": states, "completed_partitions": [], "auto_reboot": False,
                       "atomic_restore": False, "secure_erasure": False,
                       "progress_definition": "Native bytes delivered are verified input, not a contiguous target prefix. Until readback succeeds, written extents are unknown.",
                       "power_loss_definition": "Journal checkpoints are lower bounds; later writes may exist. No automatic resume or reboot."}
            targets = {}
            guard = _TargetGuard(context, parts, repo, plan, targets)
            try:
                journal.checkpoint("started", receipt)
                guard(True)
                receipt.update(status="writing", active_partition="userdata")
                journal.checkpoint("before-userdata", receipt)
                with _open_target(context, parts[1], tools, plan) as target:
                    targets["userdata"] = target
                    guard(True)
                    receipt["physical_device_written"] = context.mode == "device"
                    if operation == "restore":
                        _write_native(archives["userdata"], target, parts[1], tools, lease, states["userdata"], guard, progress)
                    else:
                        _format_userdata(target, parts[1], tools, lease, plan, states["userdata"], guard, progress)
                    targets.pop("userdata")
                receipt["completed_partitions"].append("userdata")
                journal.checkpoint("verified-userdata", receipt)
                if _boot_hash(context.refresh(), parts[0], progress, operation) != plan["source_boot_sha256"]:
                    fail("BOOT_CHANGED", "BOOT changed while userdata was being processed; no BOOT write is allowed")
                if expected_action == "restore-last":
                    receipt["active_partition"] = "boot"
                    journal.checkpoint("before-boot", receipt)
                    with _open_target(context.refresh(), parts[0], tools, plan) as target:
                        targets["boot"] = target
                        _write_raw(archives["boot"], target, parts[0], states["boot"], guard, progress)
                        targets.pop("boot")
                    receipt["completed_partitions"].append("boot")
                    journal.checkpoint("verified-boot", receipt)
                else:
                    states["boot"]["readback_sha256"] = plan["source_boot_sha256"]
                guard(True)
                receipt.update(status="complete", completed_utc=utc(), active_partition=None, partial_restore_possible=False)
                journal.checkpoint("complete", receipt)
                journal.finish(receipt)
                return {"ok": True, "operation": operation, "workflow": WORKFLOW, "verified": True,
                        "backup_path": str(path), "receipt": receipt, "auto_reboot": False, "secure_erasure": False}
            except BaseException as exc:
                receipt.update(status="failed", failed_utc=utc(), error=getattr(exc, "code", type(exc).__name__),
                               partial_restore_possible=any(s["write_started"] for s in states.values()))
                receipt["physical_device_written"] = context.mode == "device" and receipt["partial_restore_possible"]
                try:
                    journal.checkpoint("failed", receipt)
                    journal.finish(receipt)
                except OSError as journal_error:
                    receipt["failure_receipt_error"] = str(journal_error)
                exc.restore_receipt = receipt
                if isinstance(exc, RecoveryError):
                    exc.details.update(receipt=receipt, partial_restore_possible=receipt["partial_restore_possible"], auto_reboot=False)
                raise


def qualify_to_images(backup_path, output_directory, implementation_record, toolchain_manifest, progress=None):
    """Restore a real-device snapshot ONLY into two exclusively new regular files.

    This host acceptance API has no target-path override, reuse option, fixture
    identity conversion or physical-device mode. It cannot activate a writer.
    The returned receipt can be reviewed and sealed into a build capsule by root.
    """
    from common import beneath

    class ArchiveContext:
        def __init__(self, path):
            self.root = no_symlinks(Path(path))
        def user_path(self, path, *, exists=True):
            return beneath(Path(path), self.root, must_exist=exists)

    context = ArchiveContext(backup_path)
    path, manifest, manifest_sha = load_manifest(context, context.root)
    if manifest["source_mode"] != "device":
        fail("QUALIFICATION_REAL_SOURCE_REQUIRED", "Acceptance requires a full backup from the identified physical Linux source")
    record = implementation_record
    if (not isinstance(record, dict) or record.get("schema") != "a22x.linux.implementation.v1"
            or record.get("workflow") != WORKFLOW or manifest["device"] != record.get("binding", {}).get("device")
            or manifest.get("source_contract") != record["binding"].get("context_contract")
            or manifest.get("source_geometry") != record["binding"].get("partitions")):
        fail("QUALIFICATION_SOURCE_MISMATCH", "Physical backup does not match the recorded build/device implementation")
    if set(record.get("sources", {})) != set(qualified.SOURCE_FILES):
        fail("QUALIFICATION_CHANGED", "Implementation record lacks the fixed source file set")
    for name, expected in record["sources"].items():
        if hash_file(qualified.SOURCE_ROOT / name, regular_only=True)[0] != expected:
            fail("QUALIFICATION_CHANGED", "Host qualification code differs from the recorded recovery source", source=name)
    paths = qualified._validate_toolchain(toolchain_manifest, trusted=False)
    tools = {"paths": paths, "mkfs_config": toolchain_manifest["mkfs_config"]}
    destination = no_symlinks(Path(output_directory), must_exist=False)
    if os.path.lexists(destination) or destination == path or path in destination.parents:
        fail("QUALIFICATION_NEW_DIRECTORY_REQUIRED", "Use an exclusively new output directory outside the source backup")
    if not destination.parent.is_dir():
        fail("PATH_MISSING", "Qualification output parent directory must already exist")
    data = manifest["entries"][1]
    required = data["partclone"]["used_blocks"] * data["partclone"]["block_size"] + manifest["entries"][0]["raw_bytes"] + RESERVE
    free = shutil.disk_usage(destination.parent).free
    if free < required:
        fail("INSUFFICIENT_SPACE", "Host lacks room for the restored allocated filesystem blocks", free_bytes=free, required_bytes=required)
    parts = [{"label": entry["label"], "size_bytes": entry["raw_bytes"]} for entry in manifest["entries"]]
    for part in parts:
        if part["size_bytes"] != qualified.GEOMETRY[part["label"]][1]:
            fail("GEOMETRY_MISMATCH", "Qualification requires the full admitted A226B partition geometry")
    with contextlib.ExitStack() as stack:
        archives = _archives(stack, path, manifest, parts, tools, None)
        _verify_archives(archives, progress, "qualify-image")
        destination.mkdir(mode=0o700)
        fsync_directory(destination.parent)
        targets = {}
        for part in parts:
            filename = destination / (part["label"] + ".img")
            fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    fail("REGULAR_FILE_REQUIRED", "Qualification cannot create a physical target")
                os.ftruncate(fd, part["size_bytes"])
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                target = os.fdopen(fd, "r+b", buffering=0)
            except BaseException:
                os.close(fd)
                raise
            targets[part["label"]] = stack.enter_context(target)
            part.update(path=str(filename), stamp=stamp(target.fileno()))

        def guard(force=False):
            for part in parts:
                _check_target_fd(targets[part["label"]], part, physical=False)

        state = {"userdata": {"write_started": False, "native_bytes_delivered": 0,
                              "remaining_native_bytes": data["native_image_bytes"], "fsync_completed": False, "readback_verified": False},
                 "boot": {"write_started": False, "verified_prefix_bytes": 0, "fsync_completed": False, "readback_verified": False}}
        intent = {"schema": "a22x.linux.image-qualification.intent.v1", "started_utc": utc(),
                  "backup_path": str(path), "backup_manifest_sha256": manifest_sha,
                  "implementation_sha256": digest(record), "output_directory": str(destination),
                  "physical_device_written": False, "auto_reboot": False}
        write_json_new(destination / "intent.json", intent)
        try:
            verified = _write_native(archives["userdata"], targets["userdata"], parts[1], tools, None, state["userdata"], guard, progress)
            _write_raw(archives["boot"], targets["boot"], parts[0], state["boot"], guard, progress)
            acceptance = {"schema": "a22x.linux.acceptance.v1", "ok": True, "completed_utc": utc(),
                          "implementation_sha256": digest(record), "source_device": manifest["device"],
                          "backup_manifest_sha256": manifest_sha, "real_source_backup": True,
                          "restore_completed": True, "all_archives_verified_before_write": True,
                          "target_kind": "owned-new-regular-files", "output_directory": str(destination),
                          "userdata_bytes": data["raw_bytes"], "userdata_archive_sha256": data["archive_sha256"],
                          "userdata_native_body_sha256": data["native_body_sha256"],
                          "restored_native_body_sha256": verified["native_body_sha256"],
                          "target_e2fsck_exit": verified["e2fsck_exit_code"],
                          "allocated_bytes_and_metadata_verified": verified["verified"],
                          "boot_raw_verified": state["boot"]["readback_verified"],
                          "boot_raw_sha256": state["boot"]["readback_sha256"],
                          "host_toolchain_sha256": digest(toolchain_manifest), "physical_device_written": False,
                          "auto_reboot": False, "unallocated_bytes_verified": False,
                          "scope": "Independent clean ext4 check plus canonical allocation-map/allocated-byte comparison including file metadata; free blocks intentionally omitted",
                          "state": state}
            qualified.validate_acceptance(acceptance, record)
            write_json_new(destination / "acceptance.json", acceptance)
            return acceptance
        except BaseException as exc:
            failure = {**intent, "ok": False, "failed_utc": utc(), "error": getattr(exc, "code", type(exc).__name__),
                       "state": state, "partial_regular_images_possible": any(s["write_started"] for s in state.values())}
            with contextlib.suppress(OSError):
                write_json_new(destination / "FAILED.json", failure)
            if isinstance(exc, RecoveryError):
                exc.details["qualification_failure"] = failure
            raise
