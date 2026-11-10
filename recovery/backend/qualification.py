"""Read-only integrity gate for the narrowly admitted Linux v2 workflow.

The generic raw device writer remains disabled. This capsule is an integrity
and qualification record within trusted root recovery, not protection against
a hostile root replacing the running Python interpreter or its source files.
No operation in this module opens a storage target for writing.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from common import (RecoveryError, digest, fail, hash_file, no_symlinks,
                    read_json, require_hash)

WORKFLOW = "linux-partclone-v2"
CAPSULE_PATH = Path("/etc/a22x-recovery/linux-workflow-qualification.json")
TOOLCHAIN_PATH = Path("/etc/a22x-recovery/linux-toolchain.json")
SOURCE_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = (
    "backend/common.py", "backend/storage.py", "backend/engine.py",
    "backend/recoveryctl.py", "backend/streaming.py", "backend/restore_support.py",
    "backend/qualification.py", "backend/linux_workflow.py", "backend/partclone_image.py",
    "backend/qualify_linux.py", "operation_lock.py",
)
TOOL_NAMES = frozenset({"partclone.ext4", "partclone.chkimg", "partclone.restore",
                        "zstd", "e2fsck", "blkid", "dumpe2fs", "mkfs.ext4"})
GEOMETRY = {"boot": (41, 33554432), "userdata": (54, 53393489920)}


def _trusted_regular(path, *, executable=False):
    path = no_symlinks(Path(path))
    st = path.stat(follow_symlinks=False)
    if (not path.is_absolute() or not stat.S_ISREG(st.st_mode)
            or st.st_uid != 0 or st.st_mode & 0o022 or st.st_nlink < 1):
        fail("UNTRUSTED_QUALIFICATION_FILE", "Qualification inputs must be root-owned regular files without group/world writes",
             path=str(path))
    if executable and not st.st_mode & 0o111:
        fail("INVALID_TOOLCHAIN", "Pinned tool is not executable", path=str(path))
    return path


def _sha(path, *, trusted=False, executable=False):
    path = _trusted_regular(path, executable=executable) if trusted else no_symlinks(Path(path))
    return hash_file(path, regular_only=True)[0]


def _validate_toolchain(value, *, trusted):
    if not isinstance(value, dict) or value.get("schema") != "a22x.linux.toolchain.v1":
        fail("INVALID_TOOLCHAIN", "A pinned upstream Linux toolchain manifest is required")
    tools = value.get("tools")
    if not isinstance(tools, dict) or set(tools) != TOOL_NAMES:
        fail("INVALID_TOOLCHAIN", "Toolchain must name the complete fixed Linux tool set")
    paths = {}
    for name, record in tools.items():
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            fail("INVALID_TOOLCHAIN", "Malformed pinned tool", tool=name)
        require_hash(record.get("sha256"), "tool_sha256")
        path = Path(record["path"])
        if not path.is_absolute() or str(path.resolve(strict=True)) != str(path):
            fail("INVALID_TOOLCHAIN", "Tool paths must be absolute, fully resolved paths", tool=name)
        if _sha(path, trusted=trusted, executable=True) != record["sha256"]:
            fail("TOOLCHAIN_CHANGED", "Pinned tool binary changed", tool=name)
        if "executable_path" in record or "executable_sha256" in record:
            executable = record.get("executable_path")
            require_hash(record.get("executable_sha256"), "executable_sha256")
            if not isinstance(executable, str) or not Path(executable).is_absolute() or str(Path(executable).resolve(strict=True)) != executable:
                fail("INVALID_TOOLCHAIN", "Wrapper executable must have an absolute resolved path", tool=name)
            if _sha(Path(executable), trusted=trusted, executable=True) != record["executable_sha256"]:
                fail("TOOLCHAIN_CHANGED", "Pinned wrapper's underlying executable changed", tool=name)
        paths[name] = str(path)
    runtime = value.get("runtime_files")
    if not isinstance(runtime, dict) or not 1 <= len(runtime) <= 256:
        fail("INVALID_TOOLCHAIN", "The runtime dependency/configuration file set must be bounded and nonempty")
    for name, expected in runtime.items():
        require_hash(expected, "runtime_sha256")
        path = Path(name)
        if not path.is_absolute() or str(path.resolve(strict=True)) != str(path):
            fail("INVALID_TOOLCHAIN", "Runtime dependencies need absolute resolved paths")
        if _sha(path, trusted=trusted) != expected:
            fail("TOOLCHAIN_CHANGED", "Pinned runtime dependency changed", path=name)
    config = value.get("mkfs_config")
    if not isinstance(config, str) or config not in runtime:
        fail("INVALID_TOOLCHAIN", "An explicit hashed upstream mke2fs configuration is required")
    return paths


def toolchain(context):
    """Return exact tools; device mode never chooses an executable from PATH."""
    if context.mode == "fixture":
        from partclone_image import resolve_tools
        import shutil
        tools = resolve_tools()
        mkfs = shutil.which("mkfs.ext4")
        if not mkfs:
            fail("LINUX_TOOL_UNAVAILABLE", "mkfs.ext4 is required for the regular-file fixture")
        tools["mkfs.ext4"] = str(Path(mkfs).resolve())
        return {"paths": tools, "mkfs_config": "/etc/mke2fs.conf", "manifest": None,
                "manifest_sha256": None}
    _trusted_regular(TOOLCHAIN_PATH)
    value = read_json(TOOLCHAIN_PATH)
    paths = _validate_toolchain(value, trusted=True)
    return {"paths": paths, "mkfs_config": value["mkfs_config"], "manifest": value,
            "manifest_sha256": digest(value)}


def geometry_record(parts):
    return {p["label"]: {key: p.get(key) for key in
                         ("path", "device", "partition_number", "start_sector", "size_bytes")}
            for p in parts}


def boot_session(context):
    if context.mode == "fixture":
        return "fixture:" + context.contract
    context.assert_recovery()
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def device_binding(context, parts):
    """Stable device contract; boot UUID is separately bound by each plan."""
    if context.mode != "device":
        fail("QUALIFICATION_DEVICE_ONLY", "A production qualification describes a physical source device")
    context.assert_recovery()
    if context.device.get("model") != "SM-A226B" or not str(context.device.get("identity", "")).startswith("emmc-cid-sha256:"):
        fail("WRONG_DEVICE", "Only the identified A226B eMMC is admitted")
    if [p["label"] for p in parts] != ["boot", "userdata"]:
        fail("LINUX_TARGET_FORBIDDEN", "Linux v2 admits exactly BOOT and userdata")
    for part in parts:
        number, size = GEOMETRY[part["label"]]
        if part.get("partition_number") != number or part["size_bytes"] != size:
            fail("GEOMETRY_MISMATCH", "Physical Linux workflow geometry differs from the qualified A226B")
        node = context.nodes[part["device"]]
        if context.graph.physical_roots(part["device"]) != {context.internal_disk}:
            fail("WRONG_DEVICE", "Linux target is not on the identified internal eMMC")
        fields = dict(line.split("=", 1) for line in
                      (Path(node["sysfs"]) / "uevent").read_text().splitlines() if "=" in line)
        if fields.get("PARTNAME") != part["label"]:
            fail("PARTITION_LABEL_MISMATCH", "Linux target GPT name differs from its allowlisted label")
    return {"device": context.device, "context_contract": context.contract,
            "partitions": geometry_record(parts),
            "kernel": {"release": os.uname().release, "version": os.uname().version},
            "dt_compatible_sha256": _sha(Path("/sys/firmware/devicetree/base/compatible"))}


def implementation(context, parts=None, tools=None):
    """Read-only record root can save before its disposable-image acceptance."""
    parts = parts or [context.partition(label) for label in ("boot", "userdata")]
    tools = tools or toolchain(context)
    if context.mode != "device":
        fail("QUALIFICATION_DEVICE_ONLY", "Capture the build/device contract in RAM recovery")
    source_hashes = {name: _sha(SOURCE_ROOT / name, trusted=True) for name in SOURCE_FILES}
    return {"schema": "a22x.linux.implementation.v1", "workflow": WORKFLOW,
            "binding": device_binding(context, parts), "sources": source_hashes,
            "toolchain": tools["manifest"]}


def validate_acceptance(acceptance, record):
    if not isinstance(acceptance, dict) or acceptance.get("schema") != "a22x.linux.acceptance.v1":
        fail("INVALID_QUALIFICATION", "A full real-source disposable-image acceptance receipt is required")
    required_true = ("ok", "real_source_backup", "restore_completed", "all_archives_verified_before_write",
                     "allocated_bytes_and_metadata_verified", "boot_raw_verified")
    if any(acceptance.get(field) is not True for field in required_true):
        fail("INVALID_QUALIFICATION", "Acceptance did not complete every required verification")
    if (acceptance.get("physical_device_written") is not False
            or acceptance.get("target_kind") != "owned-new-regular-files"
            or acceptance.get("target_e2fsck_exit") != 0
            or isinstance(acceptance.get("target_e2fsck_exit"), bool)
            or acceptance.get("userdata_bytes") != GEOMETRY["userdata"][1]
            or acceptance.get("source_device") != record["binding"]["device"]
            or acceptance.get("implementation_sha256") != digest(record)):
        fail("INVALID_QUALIFICATION", "Acceptance does not describe this exact implementation, physical source and owned image target")
    for name in ("backup_manifest_sha256", "boot_raw_sha256", "userdata_archive_sha256",
                 "userdata_native_body_sha256", "restored_native_body_sha256"):
        require_hash(acceptance.get(name), name)
    if acceptance["userdata_native_body_sha256"] != acceptance["restored_native_body_sha256"]:
        fail("INVALID_QUALIFICATION", "Restored allocated blocks and metadata do not match the source image")


def make_capsule(record, acceptance):
    """Build a concrete capsule after acceptance; this function performs no writes."""
    if not isinstance(record, dict) or record.get("schema") != "a22x.linux.implementation.v1" or record.get("workflow") != WORKFLOW:
        fail("INVALID_QUALIFICATION", "Unexpected implementation record")
    validate_acceptance(acceptance, record)
    value = {"schema": "a22x.linux.qualification.v1", "workflow": WORKFLOW,
             "implementation": record, "acceptance": acceptance,
             "linux_boot_sha256": acceptance["boot_raw_sha256"],
             "scope": "ext4 allocated blocks and metadata plus the qualified Linux BOOT; no Android, arbitrary partition writes or power-loss atomicity"}
    value["capsule_id"] = digest(value)
    return value


def admission(context, parts=None, tools=None):
    """Return honest capability status. Missing/changed evidence never enables writes."""
    if context.mode == "fixture":
        return {"status": "fixture-only", "eligible": True, "capsule_id": None,
                "blockers": [], "linux_boot_sha256": None}
    try:
        parts = parts or [context.partition(label) for label in ("boot", "userdata")]
        tools = tools or toolchain(context)
        _trusted_regular(CAPSULE_PATH)
        value = read_json(CAPSULE_PATH)
        if not isinstance(value, dict) or value.get("schema") != "a22x.linux.qualification.v1" or value.get("workflow") != WORKFLOW:
            fail("INVALID_QUALIFICATION", "Unsupported Linux qualification capsule")
        capsule_id = value.get("capsule_id")
        if capsule_id != digest({k: v for k, v in value.items() if k != "capsule_id"}):
            fail("INVALID_QUALIFICATION", "Qualification capsule seal differs from its contents")
        record = implementation(context, parts, tools)
        if value.get("implementation") != record:
            fail("QUALIFICATION_CHANGED", "Code, runtime, kernel, configuration or device differs from the tested build")
        validate_acceptance(value.get("acceptance"), record)
        if value.get("linux_boot_sha256") != value["acceptance"]["boot_raw_sha256"]:
            fail("INVALID_QUALIFICATION", "Capsule boot allowlist differs from qualified Linux BOOT")
        return {"status": "qualified", "eligible": True, "capsule_id": capsule_id,
                "blockers": [], "linux_boot_sha256": value["linux_boot_sha256"]}
    except (RecoveryError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "unqualified", "eligible": False, "capsule_id": None,
                "blockers": [getattr(exc, "code", "LINUX_QUALIFICATION_UNAVAILABLE")],
                "linux_boot_sha256": None}


def require_admission(context, parts, tools, expected):
    actual = admission(context, parts, tools)
    if not actual["eligible"] or (context.mode == "device" and actual["status"] != "qualified"):
        fail("LINUX_WORKFLOW_NOT_QUALIFIED", "The exact Linux build/device workflow has not passed disposable-image qualification",
             blockers=actual["blockers"])
    if actual != expected:
        fail("PLAN_STALE", "Linux qualification changed after this plan was reviewed")
    return actual
