# Explicit USB file access (MTP)

`usb_files.py` exports one explicitly selected, already mounted `userdata` or
external SD filesystem. It never mounts a data volume, changes its original
mount mode, opens a raw partition for writing, or silently adds a later mount.
The default state after boot is stopped. The existing NCM function is retained;
starting/stopping MTP briefly re-enumerates the composite USB device.

## UI / CLI contract

```text
python3 usb_files.py --mode device status
python3 usb_files.py --mode device start --volume userdata \
  --expected-volume-token <SHA256> --expected-mount-token <SHA256> [--read-only]
python3 usb_files.py --mode device stop --expected-session-token <SHA256>
```

Start requires a deliberate local UI action; the UI holds its confirmation for
two seconds and protects an unsaved editor buffer. Stop is explicit and bound to
the exact current session. A persistent Start from an inherited administrator
SSH lease is refused (`INHERITED_SESSION`); the touchscreen or UART can start it.
During export, a new administrator SSH session and storage operations are busy.
The touchscreen/UART Stop uses private IPC and does not acquire the export's
already held storage lease.

Successful replies include:

```json
{
  "ok": true,
  "mode": "device",
  "operation": "status",
  "available": true,
  "state": "stopped",
  "session_token": null,
  "exports": [],
  "candidates": [{
    "id": "userdata",
    "label": "userdata",
    "display_label": "userdata",
    "mountpoint": "/mnt/volumes/userdata",
    "actual_access": "rw",
    "volume_token": "<64 lowercase hex>",
    "mount_token": "<64 lowercase hex>",
    "new_uid": 0,
    "new_gid": 0
  }],
  "reason": null
}
```

`mount_token` binds boot, volume identity, mount ID/path, root device/inode,
mount ownership, actual RO/RW mode and block RO state. Active `exports[0]` keeps
the candidate identity and both original tokens, and adds `access: "ro"|"rw"`.
`--read-only` narrows only the isolated bind; its original mount token is
preserved. No argument promotes an existing RO mount to RW.

States are `stopped`, `starting`, `active`, `stopping`, `failed`. Start waits
up to 10 seconds and reports a real pending state rather than inventing success.
Active means the isolated responder is running, its FunctionFS descriptors are
published, and the admitted UDC has bound the configuration; it does not prove
that a particular PC's file manager has accepted MTP. UI performs a fresh status
read after Start/Stop and verifies session, volume, mount token and access.

While active, candidates is empty; exports is the exact admitted selection.
Stable refusal codes include `VOLUME_CHANGED`, `MOUNT_CHANGED`,
`SESSION_CHANGED`, `OPERATION_BUSY`, `USB_EXPORT_ACTIVE`,
`USB_RUNTIME_UNAVAILABLE`, `INHERITED_SESSION`, `SERVICE_STALE`.
Errors have `ok:false`, `mode`, `boot_id`, `operation` and
`error:{code,message,details}`. Runtime absence is not simulated availability.

## Filesystem behavior and limits

- Only ordinary files and directories are visible. Symlinks, special nodes and
  nested mounts are refused, including symlinks pointing inside the same volume.
- New files are UID/GID 0:0, mode 0644; directories are 0755. An existing file
  overwritten in place retains its inode, UID/GID, mode, hardlinks and xattrs.
  If the PC client deletes and recreates it, it is a new root-owned inode.
- MTP provides file access, not faithful Unix backup. It does not preserve the
  PC's requested Unix ownership, ACLs, xattrs, symlinks or hardlink topology.
  Use the Linux/Partclone backup workflow for a complete filesystem snapshot;
  administrator SFTP remains available after stopping MTP.
- Writes report ENOSPC, incomplete/overlong data, failed seeks and flush errors.
  A failed/cancelled transfer can leave a partial file; it is not transactional.
  Unknown-size `0xffffffff` SendObjectInfo transfers remain supported for large
  files, while known-size transfers must receive the complete declared length.
- Deleting a directory containing a hidden symlink/special node can partially
  remove ordinary children and then fail; hidden nodes are not silently removed.

## Isolation and lifecycle

The qualified static responder is pinned to upstream uMTP-Responder 1.8.1,
commit `8f87f7b19034055e2665eaaf07f84a41aa5f25e0`, plus the supplied GPL patch.
`usb/patch_umtp.py`, `mtp_guard.c` and `mtp_sendobject.c` apply deterministic
changes to a fresh source tree. All file I/O uses a fixed `/files` root with
`openat2` BENEATH, NO_SYMLINKS, NO_MAGICLINKS and NO_XDEV; there is no unsafe
fallback. Dynamic upstream storage-add/unlock IPC is disabled.

`mtp_sandbox.c` creates private mount, IPC and network namespaces, then a RAM
chroot containing only the chosen bind, FunctionFS endpoints, an immutable
static responder and its immutable config. Export binds are nodev/nosuid/noexec;
an RO export is kernel-enforced. There is no procfs, host root or raw device
node. Only DAC_OVERRIDE and FOWNER remain; seccomp denies escape/mount/network,
foreign-process control, process creation and flock. The responder cannot
unlock the inherited storage lease. Its parent-death signal is SIGKILL.

The supervisor and responder both retain the lease. Stop disconnects USB,
terminates/reaps the responder, syncs the pinned source filesystem, restores
only its own added FunctionFS link/function and the original NCM UDC binding,
then releases the lease. Failure is recorded as failed/unclean, never as a clean
stop. A supervisor crash or failed gadget restoration can require UART/recovery
restart before another export; it does not permit unsafe automatic reconfiguration.
Private service receipts and a bounded responder log remain in RAM.

## Packaging and verification

The overlay contains `/bin/umtprd`, `/bin/a22x-mtp-sandbox` and
`/etc/a22x-recovery/usb-files-runtime.json` (schema `a22x.usb-files.runtime.v1`,
`responder`/`sandbox` objects with exact path and SHA-256). Both binaries are
static AArch64 ELF. The helper hashes them before use and copies the responder
into the new jail with an independently verified hash. Root adds only the new
helper and UI argument `--usb-files-helper /opt/a22x-recovery/usb_files.py`;
there is no automatic daemon start or extra initial gadget function.

`--mode fixture --fixture ROOT` uses the existing filesystem fixture JSON and
the actual supervisor IPC/lease lifecycle, with a harmless waiting child and no
USB/mount/block operations. The full fixture suite requires a normal Linux host
with AF_UNIX and matching process IDs in procfs (`A22X_USB_TEST_IPC=1`). The cloud
exec container cannot supply that; its UI tests use an explicitly marked stub.
`usb/tests/transfer_errors.c` and `session_close.c` test the actual patched MTP
operation/teardown code without USB. The separate RAM-only native sandbox probe
is for root-owned hardware acceptance; it is not installed in the recovery image.
