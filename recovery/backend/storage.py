"""Storage admission. Fixture mode cannot open a block device for writing.

The production adapter supports inventory and offline backup reads. Its restore
writer is deliberately absent until the recovery image and target admission
have been qualified on the physical device.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import struct
import sys
from pathlib import Path

# userdata grown to the end of the eMMC by an image's first start (Manjaro ARM's resize-fs ran
# sfdisk on 2026-11-08): the same start, ending at the last usable sector of the disk.
GROWN_SIZES = {"userdata": ((122142720 - 33 - 17850368) * 512,)}

from common import (CHUNK, LABELS, RETAINED_LABELS, RecoveryError, beneath, digest, directory_identity, fail,
                    hash_file, no_symlinks, open_regular_fd, read_json, stamp)


class StorageGraph:
    def __init__(self, nodes: dict):
        self.nodes = nodes

    def leaves(self, device: str, seen=frozenset()) -> set[str]:
        if device in seen:
            fail("INVALID_STORAGE_GRAPH", "Cycle in storage dependencies", device=device)
        if device not in self.nodes:
            fail("UNKNOWN_STORAGE_DEVICE", "Storage dependency is unknown", device=device)
        slaves = self.nodes[device].get("slaves", [])
        if not slaves:
            return {device}
        result = set()
        for slave in slaves:
            result.update(self.leaves(slave, seen | {device}))
        return result

    def parents(self, device: str) -> set[str]:
        result = set()
        while self.nodes[device].get("parent"):
            device = self.nodes[device]["parent"]
            if device in result:
                fail("INVALID_STORAGE_GRAPH", "Cycle in partition parent map")
            if device not in self.nodes:
                fail("UNKNOWN_STORAGE_DEVICE", "Partition parent is unknown", device=device)
            result.add(device)
        return result

    def overlaps(self, left: str, right: str) -> bool:
        for a in self.leaves(left):
            for b in self.leaves(right):
                if a == b or a in self.parents(b) or b in self.parents(a):
                    return True
        return False

    def physical_roots(self, device: str) -> set[str]:
        result = set()
        for leaf in self.leaves(device):
            current = leaf
            seen = set()
            while self.nodes[current].get("parent"):
                if current in seen:
                    fail("INVALID_STORAGE_GRAPH", "Cycle in partition parents")
                seen.add(current)
                current = self.nodes[current]["parent"]
                if current not in self.nodes:
                    fail("UNKNOWN_STORAGE_DEVICE", "Unknown physical parent")
            result.add(current)
        return result

    def require_unmounted(self, source: str, mounts: list[dict]):
        for mount in mounts:
            if self.overlaps(source, mount["device"]):
                fail("PARTITION_MOUNTED", "Raw snapshots require an unmounted source/target",
                     device=source, mounted_device=mount["device"],
                     mountpoint=mount.get("mountpoint", "unknown"))

    def require_no_holders(self, source: str):
        for device, node in self.nodes.items():
            if device != source and node.get("slaves") and self.overlaps(source, device):
                fail("PARTITION_HAS_HOLDERS", "Retained firmware has an active block dependency",
                     device=source, holder=device)


class FixtureContext:
    mode = "fixture"
    can_restore = True

    def __init__(self, root):
        self.root = no_symlinks(Path(root))
        if not self.root.is_dir():
            fail("INVALID_FIXTURE", "Fixture root is not a directory")
        self.config_path = self.root / "fixture.json"
        self.config = read_json(self.config_path)
        if self.config.get("schema") != "a22x.fixture.v1":
            fail("INVALID_FIXTURE", "Unsupported fixture schema")
        self.device = self.config["device"]
        if not str(self.device.get("identity", "")).startswith("fixture:"):
            fail("INVALID_FIXTURE", "Fixture identity must be explicitly marked fixture:")
        self.contract = digest(self.config)
        self.graph = StorageGraph(self.config["storage_graph"])
        self.mounts = self.config.get("mounts", [])
        self.lock_path = self.root / ".operation.lock"

    def refresh(self):
        return FixtureContext(self.root)

    def user_path(self, path, *, exists=True):
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        return beneath(path, self.root, must_exist=exists)

    def partition(self, label: str, *, retained=False):
        allowed = RETAINED_LABELS if retained else LABELS
        if label not in allowed:
            fail("PARTITION_FORBIDDEN", "Partition is not in the explicit allowlist", label=label)
        collection = self.config.get("retained_partitions" if retained else "partitions", {})
        item = collection.get(label)
        if not isinstance(item, dict):
            fail("PARTITION_MISSING", "Partition is absent from the admitted device", label=label)
        path = self.user_path(item["path"])
        fd = open_regular_fd(path)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                fail("FIXTURE_REGULAR_ONLY", "Fixture partitions must be regular files", label=label)
            size = item["size_bytes"]
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                fail("INVALID_FIXTURE", "Partition size must be a positive integer")
            if st.st_size != size:
                fail("TARGET_SIZE_MISMATCH", "Partition file size differs from admitted geometry",
                     label=label, expected=size, actual=st.st_size)
            self.graph.leaves(item["device"])
            return {"label": label, "path": str(path), "size_bytes": size,
                    "device": item["device"], "stamp": stamp(fd)}
        finally:
            os.close(fd)

    def repository(self, path, *, writing=False):
        path = self.user_path(path)
        if not path.is_dir():
            fail("INVALID_REPOSITORY", "Repository must be an existing directory")
        candidates = []
        for entry in self.config.get("repositories", []):
            base = self.user_path(entry["path"])
            if base == path or base in path.parents:
                candidates.append((len(base.parts), entry))
        if not candidates:
            fail("UNADMITTED_REPOSITORY", "Fixture repository needs an explicit storage binding")
        item = max(candidates, key=lambda pair: pair[0])[1]
        if writing and item.get("read_only", False):
            fail("REPOSITORY_READ_ONLY", "Backup destination is read-only")
        st = path.stat()
        self.graph.leaves(item["device"])
        return {"path": str(path), "device": item["device"],
                "filesystem": item.get("filesystem", "ext4"),
                "read_only": item.get("read_only", False),
                "identity": directory_identity(st, item.get("filesystem", "ext4"))}

    def assert_offline(self, parts: list[dict], repository: dict):
        check_distinct(parts)
        for part in parts:
            self.graph.require_unmounted(part["device"], self.mounts)
            if self.graph.physical_roots(part["device"]) & self.graph.physical_roots(repository["device"]):
                fail("REPOSITORY_ON_SOURCE_DISK", "Backup storage must be outside the internal source disk",
                     partition=part["label"], repository=repository["path"])

    def assert_recovery(self):
        # Fixture is a simulation, never an admission to a real device.
        return

    def compatibility(self, progress=None, operation="backup-plan"):
        return observed_compatibility(self, progress, operation)

    def source_format(self, part):
        return inspect_source_format(self, part)

    def open_partition(self, part: dict, *, write=False):
        # Checking type on the opened FD is mandatory: fixture mode cannot write devices.
        fd = open_regular_fd(Path(part["path"]), write=write)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stamp(fd) != part["stamp"]:
            os.close(fd)
            fail("PARTITION_CHANGED", "Fixture partition changed since the reviewed plan", label=part["label"])
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "r+b" if write else "rb", buffering=0)

    def staging_directory(self):
        path = self.root / ".restore-staging"
        if not path.exists():
            path.mkdir(mode=0o700)
        return self.user_path(path)

    def result_directory(self):
        path = self.root / "results"
        if not path.exists():
            path.mkdir(mode=0o700)
        return self.user_path(path)


def check_distinct(parts):
    seen = set()
    for part in parts:
        st = part["stamp"]
        identity = (st["device"], st["inode"], st["rdev"])
        if identity in seen:
            fail("ALIASED_PARTITIONS", "Two labels refer to the same underlying object")
        seen.add(identity)


def _mount_unescape(value):
    for escaped, literal in [("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")]:
        value = value.replace(escaped, literal)
    return value


def parse_mountinfo(text):
    result = []
    for line in text.splitlines():
        fields = line.split()
        if "-" not in fields or len(fields) < 10:
            fail("INVALID_MOUNTINFO", "Malformed mountinfo record")
        split = fields.index("-")
        result.append({"device": fields[2], "mountpoint": _mount_unescape(fields[4]),
                       "options": fields[5].split(","), "filesystem": fields[split + 1],
                       "source": _mount_unescape(fields[split + 2])})
    return result


def discover_block_nodes(class_directory=Path("/sys/class/block")):
    nodes = {}
    for link in class_directory.iterdir():
        real = link.resolve()
        dev = (real / "dev").read_text().strip()
        parent = None
        if (real / "partition").exists():
            parent = (real.parent / "dev").read_text().strip()
        slave_directory = real / "slaves"
        if slave_directory.is_dir():
            slaves = [(entry.resolve() / "dev").read_text().strip() for entry in slave_directory.iterdir()]
        elif parent is not None:
            # Linux partition kobjects normally have no 'slaves' directory.
            # Their dependency is the explicit containing-disk edge instead.
            slaves = []
        else:
            fail("UNKNOWN_STORAGE_TOPOLOGY", "Whole block-device dependency directory is missing", device=dev)
        nodes[dev] = {"parent": parent, "slaves": slaves, "sysfs": str(real), "name": link.name}
    return nodes


class DeviceContext:
    """Read-only production source adapter. There is no block restore switch."""
    mode = "device"
    can_restore = False

    def __init__(self, config_path="/etc/a22x-recovery/device.json"):
        self.config_path = no_symlinks(Path(config_path))
        self.config = read_json(self.config_path)
        if self.config.get("schema") != "a22x.device.v1" or self.config.get("model") != "SM-A226B":
            fail("WRONG_DEVICE", "Only the admitted SM-A226B recovery profile is supported")
        self.contract = digest(self.config)
        dt = Path("/sys/firmware/devicetree/base/compatible")
        compatible = dt.read_bytes().split(b"\0") if dt.exists() else []
        if b"samsung,a22x" not in compatible or b"mediatek,mt6833" not in compatible:
            fail("WRONG_DEVICE", "The live device tree does not identify SM-A226B / MT6833")
        self.nodes = discover_block_nodes()
        self.self_mounts = parse_mountinfo(Path("/proc/self/mountinfo").read_text())
        self.mounts = self._all_mounts()
        for mount in self.mounts:
            if mount["device"] not in self.nodes and mount["device"].startswith("0:"):
                self.nodes[mount["device"]] = {"parent": None, "slaves": [], "virtual": True}
        # Loop files inherit their backing filesystem dependency.
        for node in list(self.nodes.values()):
            if not node.get("sysfs"):
                continue
            backing = Path(node["sysfs"]) / "loop/backing_file"
            if backing.exists():
                raw = backing.read_text().strip()
                if raw:
                    path = Path(raw if raw.startswith("/") else "/" + raw)
                    node["slaves"] = [self._mount_for(path)["device"]]
        self.graph = StorageGraph(self.nodes)
        userdata = self._block_path("userdata", retained=False)
        roots = self.graph.physical_roots(userdata["device"])
        if len(roots) != 1:
            fail("WRONG_DEVICE", "userdata does not have one physical eMMC parent")
        disk = self.nodes[next(iter(roots))]
        self.internal_disk = next(iter(roots))
        cid = Path(disk["sysfs"]) / "device/cid"
        if not cid.is_file():
            fail("DEVICE_IDENTITY_MISSING", "A stable eMMC CID is required")
        identity = hashlib.sha256(cid.read_bytes().strip()).hexdigest()
        self.device = {"model": "SM-A226B", "identity": "emmc-cid-sha256:" + identity}
        self.lock_path = Path("/run/lock/a22x-mainline-recovery.lock")

    def refresh(self):
        return DeviceContext(self.config_path)

    def _all_mounts(self):
        mounts = []
        namespaces = set()
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                namespace = os.readlink(path / "ns/mnt")
                if namespace in namespaces:
                    continue
                data = (path / "mountinfo").read_text()
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError:
                fail("MOUNT_NAMESPACE_UNREADABLE", "Cannot prove that all source mounts are absent")
            namespaces.add(namespace)
            mounts.extend(parse_mountinfo(data))
        return mounts

    def _mount_for(self, path):
        candidates = [entry for entry in self.self_mounts
                      if Path(entry["mountpoint"]) == path or Path(entry["mountpoint"]) in path.parents]
        if not candidates:
            fail("UNKNOWN_REPOSITORY_MOUNT", "Cannot identify the backing mount")
        return max(candidates, key=lambda entry: len(Path(entry["mountpoint"]).parts))

    def user_path(self, path, *, exists=True):
        path = Path(path)
        if not path.is_absolute():
            fail("ABSOLUTE_PATH_REQUIRED", "Device-mode paths must be absolute")
        return no_symlinks(path, must_exist=exists)

    def _block_path(self, label, *, retained):
        allowed = RETAINED_LABELS if retained else LABELS
        if label not in allowed:
            fail("PARTITION_FORBIDDEN", "Partition is not allowlisted", label=label)
        item = self.config.get("retained_partitions" if retained else "partitions", {}).get(label)
        if retained:
            item = item if isinstance(item, dict) else {}
        elif not isinstance(item, dict):
            fail("PARTITION_MISSING", "Missing admitted geometry", label=label)
        link = Path("/dev/disk/by-partlabel") / label
        if not link.exists():
            fail("PARTITION_MISSING", "Named block device is unavailable", label=label)
        path = link.resolve(strict=True)
        if path.parent != Path("/dev"):
            fail("UNEXPECTED_BLOCK_PATH", "Resolved block path is unexpected", label=label)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            if not stat.S_ISBLK(st.st_mode):
                fail("NOT_BLOCK_DEVICE", "Named partition is not a block device", label=label)
            device = f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
            node = self.nodes.get(device)
            if not node or not node.get("parent"):
                fail("WHOLE_DISK_FORBIDDEN", "A named partition is required", label=label)
            sysfs = Path(node["sysfs"])
            number = int((sysfs / "partition").read_text())
            start = int((sysfs / "start").read_text())
            size = struct.unpack("Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
            if size <= 0 or size > 128 * 1024 * 1024 * 1024:
                fail("GEOMETRY_MISMATCH", "Partition size is outside the supported A226B range", label=label)
            if not retained and (number != item["number"] or size not in (item["size_bytes"],) + GROWN_SIZES.get(label, ())):
                fail("GEOMETRY_MISMATCH", "Live partition differs from admitted geometry", label=label,
                     size_bytes=size, partition_number=number)
            if retained:
                roots = self.graph.physical_roots(device)
                if roots != {self.internal_disk}:
                    fail("WRONG_RETAINED_DISK", "Retained firmware is not on the same internal eMMC", label=label)
                fields = dict(line.split("=", 1) for line in (sysfs / "uevent").read_text().splitlines() if "=" in line)
                if fields.get("PARTNAME") != label:
                    fail("PARTITION_LABEL_MISMATCH", "Kernel GPT name does not match the requested retained label", label=label)
            if "start_sector" in item and start != item["start_sector"]:
                fail("GEOMETRY_MISMATCH", "Partition start changed", label=label)
            return {"label": label, "path": str(path), "size_bytes": size, "device": device,
                    "partition_number": number, "start_sector": start, "stamp": stamp(fd)}
        finally:
            os.close(fd)

    def partition(self, label, *, retained=False):
        return self._block_path(label, retained=retained)

    def repository(self, path, *, writing=False):
        path = self.user_path(path)
        if not path.is_dir():
            fail("INVALID_REPOSITORY", "Repository must be an existing mounted directory")
        mount = self._mount_for(path)
        if mount["filesystem"] not in {"ext4", "f2fs", "btrfs", "xfs", "exfat", "ntfs3"}:
            fail("UNSUPPORTED_REPOSITORY_FS", "A durable external filesystem with large-file support is required",
                 filesystem=mount["filesystem"])
        read_only = "ro" in mount["options"]
        if writing and read_only:
            fail("REPOSITORY_READ_ONLY", "Backup destination is read-only")
        st = path.stat()
        return {"path": str(path), "device": mount["device"], "filesystem": mount["filesystem"],
                "read_only": read_only, "identity": directory_identity(st, mount["filesystem"])}

    def assert_offline(self, parts, repository):
        check_distinct(parts)
        for part in parts:
            self.graph.require_unmounted(part["device"], self.mounts)
            if self.graph.physical_roots(part["device"]) & self.graph.physical_roots(repository["device"]):
                fail("REPOSITORY_ON_SOURCE_DISK", "Use external SD/USB storage", partition=part["label"])

    def assert_recovery(self):
        root = self._mount_for(Path("/"))
        if root["filesystem"] not in {"rootfs", "ramfs", "tmpfs"}:
            fail("NOT_RAM_RECOVERY", "Device backup execution requires a RAM-root recovery")
        if os.geteuid() != 0:
            fail("ROOT_REQUIRED", "Recovery storage operations require root")
        # Marker is an extra consistency check, never permission for block writes.
        marker = read_json(Path("/run/a22x-recovery/boot.json"))
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if marker.get("schema") != "a22x.recovery.boot.v1" or marker.get("boot_id") != boot_id:
            fail("RECOVERY_IDENTITY_MISSING", "Recovery marker does not describe this boot")

    def compatibility(self, progress=None, operation="backup-plan"):
        return observed_compatibility(self, progress, operation)

    def source_format(self, part):
        return inspect_source_format(self, part)

    def open_partition(self, part, *, write=False):
        if write:
            fail("DEVICE_RESTORE_NOT_QUALIFIED", "Production block restore has not been qualified; no writer is enabled")
        fd = os.open(part["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_EXCL)
        if not stat.S_ISBLK(os.fstat(fd).st_mode) or stamp(fd) != part["stamp"]:
            os.close(fd)
            fail("PARTITION_CHANGED", "Block identity changed since planning", label=part["label"])
        return os.fdopen(fd, "rb", buffering=0)


def inspect_source_format(context, part):
    """Inspect only filesystem magic bytes; never mount or decrypt the volume."""
    with context.open_partition(part) as source:
        data = source.read(min(8192, part["size_bytes"]))
    kind = "opaque-or-unknown"
    if len(data) >= 1082 and data[1080:1082] == b"\x53\xef":
        kind = "ext4"
    elif any(len(data) >= offset + 4 and data[offset:offset + 4] == b"\x10\x20\xf5\xf2"
             for offset in (1024, 5120)):
        kind = "f2fs"
    elif data.startswith(b"LUKS\xba\xbe"):
        kind = "luks"
    return {"detector": "filesystem-magic-v1", "kind": kind, "bytes_inspected": len(data),
            "mounted": False, "decrypted": False, "os_identity_proven": False}


def observed_compatibility(context, progress=None, operation="backup-plan"):
    """Observe a complete retained-firmware baseline without writing any of it."""
    retained = {}
    for label in sorted(RETAINED_LABELS):
        part = context.partition(label, retained=True)
        context.graph.require_unmounted(part["device"], context.mounts)
        context.graph.require_no_holders(part["device"])
        raw_hash = hashlib.sha256()
        count = 0
        with context.open_partition(part) as source:
            while count < part["size_bytes"]:
                data = source.read(min(CHUNK, part["size_bytes"] - count))
                if not data:
                    fail("SOURCE_SHORT_READ", "Retained firmware ended before reported geometry", label=label)
                raw_hash.update(data)
                count += len(data)
                if progress:
                    progress({"event": "progress", "operation": operation, "phase": "hash-retained-firmware",
                              "partition": label, "bytes_done": count, "bytes_total": part["size_bytes"]})
            if source.read(1) or stamp(source.fileno()) != part["stamp"]:
                fail("SOURCE_CHANGED", "Retained firmware changed during hashing", label=label)
        retained[label] = {"sha256": raw_hash.hexdigest(), "size_bytes": count,
                           "partition_number": part.get("partition_number"),
                           "start_sector": part.get("start_sector")}
    bootloader = None
    if context.mode == "device":
        for field in Path("/proc/cmdline").read_text().split():
            if field.startswith("androidboot.bootloader="):
                bootloader = field.split("=", 1)[1]
    return {"firmware_fingerprint": context.config.get("firmware", {}).get("fingerprint"),
            "bootloader_cmdline": bootloader, "labels_are_informational": True,
            "retained_partitions": retained, "evidence": "observed-read-only-sha256",
            "scope": "Exact retained partition bytes and geometry; not RPMB/TEE-state attestation"}


@contextlib.contextmanager
def operation_lock(context):
    path = no_symlinks(context.lock_path, must_exist=False)
    if context.mode == "device":
        # Shared with the UI and authorized SSH/SFTP maintenance sessions. The
        # helper validates an inherited lease and closes its duplicate without
        # unlocking the parent's open-file description. Fixtures never borrow.
        parent = str(Path(__file__).resolve().parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from operation_lock import operation_lock as shared_operation_lock
        with contextlib.ExitStack() as stack:
            try:
                held_fd = stack.enter_context(shared_operation_lock(path, mode="device"))
            except BlockingIOError:
                fail("OPERATION_BUSY", "Another recovery operation owns the storage lock")
            yield held_fd
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("OPERATION_BUSY", "Another recovery operation owns the storage lock")
        yield fd
    finally:
        os.close(fd)
