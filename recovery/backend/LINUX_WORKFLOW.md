# Linux v2 used-block backup and qualified manual wipe/restore

This is a separate `linux-partclone-v2` workflow. Legacy `a22x.backup.v1` raw
archives remain supported for backup/verification and regular-file fixture
restore. The generic device raw/Android restore writer remains hard-disabled.

## What the workflow contains

The exact profile is BOOT plus unmounted clean ext4 userdata. BOOT is gzip raw;
userdata is an upstream Partclone `0002` allocation-map/used-block image inside
zstd. The manifest is `a22x.backup.v2`, with
`data_format: partclone-ext4-v2+zstd`. There is deliberately no raw userdata SHA:
unused blocks are omitted and are neither restored nor compared. File contents
and metadata in allocated ext4 blocks are preserved by native block imaging.

Backup planning performs a real counting compression pass, so its required
space is measured, not inferred from `df`. It adds a 1 MiB manifest/variance
allowance and keeps a 64 MiB free-space floor. If the measured image does not fit,
the plan is returned with `can_execute:false` and `INSUFFICIENT_SPACE`. It never
deletes existing SD data. A stopped or failed backup has no valid completion
seal and is not listed as complete.

Wipe means making a new empty ext4 filesystem in **userdata only**. It uses a
fresh reviewed UUID, explicit 4096-byte blocks, upstream `mke2fs` with
`nodiscard,lazy_itable_init=0,lazy_journal_init=0`, and read-only fsck/probe after
formatting. It preserves BOOT and RECOVERY. This is **not secure erasure**:
previous bytes may remain in unallocated blocks. Wipe requires the fully
verified saved image to still match the current userdata allocation map/used
contents and BOOT, so an old backup cannot silently authorize losing newer
edits.

Restore writes userdata first, fsyncs it, performs `e2fsck -f -n` and independently
reclones it for a canonical allocation-map/used-byte comparison. Only after
that succeeds may BOOT be restored. BOOT is skipped if its full raw SHA already
matches. A written BOOT is fsynced and independently read back. No operation
reboots automatically.

## Physical admission

Physical execution requires all of these together:

- Root in the current A226B/MT6833 RAM recovery with a matching current boot
  receipt; exact eMMC CID/configuration and kernel binding.
- BOOT partition 41, 33,554,432 bytes; userdata partition 54, 53,393,489,920 bytes.
  GPT label, start sector, device number, containing disk and opened FD identity
  are rechecked. Whole disks, RECOVERY, metadata, firmware and bootloader targets
  are never admitted.
- Both source/target partitions unmounted across visible mount namespaces, with
  no active dependent block holders. The separate ext4 SD repository has a
  pinned directory/mount instance and stable SD CID.
- A valid build qualification capsule, sealed v2 backup, full fresh archive
  verification, exact plan confirmation and the UI's three-second hold.
- Target `O_EXCL` claims and the shared operation lease. Tool children inherit
  the lease so an orphaned child continues excluding UI mounts/writers until it
  exits. Existing block read-only flags are refused, never overridden.

The UI must normally unmount an owned volume before planning. It must not force
or lazily unmount a busy filesystem. The backend does not silently unmount.

Every archive is verified before the first target writer opens. The immutable
encoded 4 MiB SHA index is derived only after whole-archive SHA and native image
checks succeed. Every encoded buffer is checked **before** the decompressor
can consume it on the writing pass. Native geometry and image byte count are
bounded by the admitted target. No full decoded image is staged in RAM or on SD.

The capsule is a correctness/integrity gate in trusted recovery, not a security
boundary against a hostile root replacing Python or the running code.

## Plans and UI contract

The stdout plan result includes `schema: a22x.plan.v2`, `plan_id`,
`confirmation_token`, `can_execute`, `blockers`, and `summary`:

```json
{
  "workflow": "linux-partclone-v2",
  "backup_schema": "a22x.backup.v2",
  "data_format": "partclone-ext4-v2+zstd",
  "destructive": true,
  "required_hold_ms": 3000,
  "qualification": {
    "status": "qualified",
    "eligible": true,
    "capsule_id": "<sha256>",
    "blockers": [],
    "linux_boot_sha256": "<qualified Linux BOOT raw sha256>"
  },
  "boot_action": "skip-identical",
  "write_targets": [
    {"label": "userdata", "path": "/dev/mmcblk0p54", "size_bytes": 53393489920, "action": "restore-used-blocks"}
  ],
  "space": {
    "userdata_partition_bytes": 53393489920,
    "userdata_used_bytes": 0,
    "estimated_archive_bytes": 0,
    "free_bytes": 0,
    "reserve_bytes": 67108864
  }
}
```

Numbers shown as zero are illustrative placeholders; actual plans contain the
measured values. Device UI execution requires **all exact discriminators**, a
qualified/eligible nonempty capsule ID and `can_execute:true`; legacy `ok` or
`can_execute` alone is insufficient. The UI also checks the write-target set:
wipe userdata only; restore userdata plus optional BOOT last. Other boot actions
are `preserve`, `restore-last`, and `read-only-backup`.

Qualification status is `qualified`, `unqualified`, or `fixture-only`. Fixture
mode can only write its owned regular files, never become device mode. Inventory
adds `linux_workflow`, `linux_used_block_backup`, `linux_restore`, `linux_wipe`
and `linux_qualification` under `capabilities`; generic device `restore` remains
false. A toolchain alone enables compact backup, never physical wipe/restore.

Destructive plans admit execution within five minutes of review. Backup plans
use twenty minutes. Once admitted, the lease is held through complete
reverification and writing; the operation does not release it just because a
large verification takes longer. Plans bind recovery boot ID, configuration,
toolchain, qualification, target objects/geometry, archive stamps and sealed
manifest SHA, current BOOT SHA and target boundary signatures. Wipe additionally
rechecks all saved allocated userdata before formatting. Reboots, changed media,
remounts, changed code/tools or edited archives require a new plan.

## Root/build qualification inputs

Device toolchain: `/etc/a22x-recovery/linux-toolchain.json`:

```json
{
  "schema": "a22x.linux.toolchain.v1",
  "tools": {
    "partclone.ext4": {"path": "/usr/sbin/partclone.ext4", "sha256": "<sha256>"},
    "partclone.chkimg": {"path": "/usr/sbin/partclone.chkimg", "sha256": "<sha256>"},
    "partclone.restore": {"path": "/usr/sbin/partclone.restore", "sha256": "<sha256>"},
    "zstd": {"path": "/usr/bin/zstd", "sha256": "<sha256>"},
    "e2fsck": {"path": "/usr/sbin/e2fsck", "sha256": "<sha256>"},
    "blkid": {"path": "/usr/sbin/blkid", "sha256": "<sha256>"},
    "dumpe2fs": {"path": "/usr/sbin/dumpe2fs", "sha256": "<sha256>"},
    "mkfs.ext4": {"path": "/usr/sbin/mke2fs", "sha256": "<sha256>"}
  },
  "runtime_files": {"/etc/mke2fs.conf": "<sha256>", "/usr/lib/<resolved-library>": "<sha256>"},
  "mkfs_config": "/etc/mke2fs.conf"
}
```

Paths above are examples: root must record the **actual fully resolved upstream
executables**, never a BusyBox applet selected accidentally through PATH.
Device mode verifies root ownership, permissions and file hashes. Runtime files
must include the actual packaged dynamic dependencies and the exact mke2fs
configuration. Tool subprocesses use a minimal fixed environment. Files are
never fetched or installed by this backend.

`recoveryctl.py --device qualification-record` returns the implementation record
under `implementation`. It binds the fixed source file set in
`qualification.SOURCE_FILES`, the complete device toolchain manifest, current
kernel release/version, DT identity, eMMC CID and exact partition geometry.
Capture this only after sources/tools are frozen.

On the host, use the same frozen sources with a corresponding explicit native
host toolchain manifest. The host-only command accepts an exclusively new
directory, never an existing target image or a block path:

```sh
python3 backend/qualify_linux.py images --backup /absolute/sealed-backup --output-directory /absolute/new-images --implementation /absolute/implementation.json --toolchain /absolute/host-toolchain.json
```

This reuses the verified native writer, creates sparse regular target images of
the full admitted sizes, verifies every source archive before writes, restores
and checks userdata and BOOT, then writes `acceptance.json`. Used ext4 blocks and
their metadata must compare exactly; unknown/unused bytes are not claimed.
Source identity must be the real device, not a fixture. The script checks its
current source hashes against the recorded recovery sources. It does not enable
or deploy anything.

After reviewing successful real-source acceptance, construct the candidate:

```sh
python3 backend/qualify_linux.py capsule --implementation /absolute/implementation.json --acceptance /absolute/new-images/acceptance.json --output /absolute/new-linux-workflow-qualification.json
```

Root embeds that exact capsule as
`/etc/a22x-recovery/linux-workflow-qualification.json` in the final matching
build. It qualifies the implementation/tool/device contract. Every new backup
can then become eligible through normal complete in-recovery verification;
there is **no per-backup host approval marker**. This release admits only the
Linux BOOT raw SHA tested by the capsule, so changing BOOT requires new explicit
qualification.

## Failure receipts and limitations

Before target writes a mandatory journal is created under
`<external-repository>/.a22x-linux-operations/<plan_id>/`. It records the plan,
durable pre-write/verified checkpoints and final result. Reusing a started plan
is refused. A new plan and complete source verification are required to retry.

Partclone writes selected extents: `native_bytes_delivered` measures verified
input handed to the child, **not** a contiguous target prefix or durability.
Until fsync plus complete readback pass, target extents are uncertain. BOOT
`verified_prefix_bytes` counts successful raw write return values; a failed or
in-flight syscall may have changed more. Power loss can leave writes after the
last durable checkpoint. The recovery and SD remain available for manual retry;
there is no atomic transaction across userdata and BOOT and no automatic resume.

Qualification here is a full real-source backup restored to disposable regular
images. It does not claim a destructive phone cycle or power-loss test occurred.
The user performs the physical wipe/restore manually after reviewing the plan.
