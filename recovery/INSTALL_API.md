# A22x BOOT and stock installation API v1

This document freezes the UI contract. The implementation is checked using
owned regular-file targets, real UI/helper transport and interruption tests.
Physical stock installation is not part of the current qualification run.

The helper has two user operations: `flash-boot` and `install-stock`. It does
not accept arbitrary target partitions. It never mounts Android, restores
Android filesystems, formats userdata/metadata, writes bootloader/modem/PIT,
changes the bootloader lock, or reboots automatically.

## Commands

Global arguments precede the command:

```text
python3 install.py --mode device status
python3 install.py --mode device boot-check

python3 install.py --mode device boot-plan \
  --source-volume sdcard-p1 --source-path /images/linux.img \
  --expected-source-token VOLUME_TOKEN \
  --backup-path /mnt/backup/linux-SNAPSHOT \
  --output /run/a22x-recovery/ui/install-UNIQUE.json

python3 install.py --mode device stock-plan \
  --source-volume sdcard-p1 --source-path /stock/manifest.json \
  --expected-source-token VOLUME_TOKEN \
  --backup-path /mnt/backup/linux-SNAPSHOT \
  --output /run/a22x-recovery/ui/install-UNIQUE.json

python3 install.py --mode device execute \
  --plan-file /run/a22x-recovery/ui/install-UNIQUE.json \
  --expected-plan-seal PLAN_SEAL \
  --expected-plan-file-sha256 PLAN_FILE_SHA256 \
  --confirmation EXACT_CONFIRMATION_TOKEN

python3 install.py --mode device resume-plan \
  --journal-volume sdcard-p1 \
  --journal-path /.a22x-installs/TRANSACTION/journal.json \
  --expected-journal-token VOLUME_TOKEN \
  --output /run/a22x-recovery/ui/install-resume-UNIQUE.json
```

Fixture mode adds `--fixture OWNED_DIRECTORY` and admits only owned regular
target files. It cannot open block devices. A fixture does not qualify a
physical device implicitly.

`boot-plan` accepts a regular raw `.img` with the exact A22x BOOT v2 geometry.
`stock-plan` accepts only the regular `manifest.json` of the prepared, pinned
stock profile; its payload files are individually compressed with gzip.
Source paths are absolute **within the selected external volume**, not device
paths. The original volume token from the file picker is required. Source and
journal storage must be a mounted, writable, admitted external ext4 SD volume.

The backup picker must explicitly select an existing Linux snapshot whose
schema/workflow/profile are `a22x.backup.v2`, `linux-partclone-v2`, `linux`.
Its `BackupEntry.path` absolute path can be passed unchanged. The helper
authoritatively verifies completeness, source identity, content and geometry;
the picker filtering is not admission. Stock installation additionally
requires that the backup still represents the current Linux BOOT and userdata.
BOOT-only flashing preserves userdata and saves the current BOOT in the small
platform checkpoint; it requires a verified same-device Linux backup but does
not require its older BOOT to equal the currently selected experimental BOOT.

## Status response

Every response has `operation` equal to the CLI command (`status`, `boot-plan`,
`stock-plan`, `resume-plan`, `execute`), `mode`, and, once context is established,
`boot_id`. Errors have `ok:false` and `error:{code,message,details}`.

`status` is bounded metadata inventory. It does not hash firmware or backups.
Its successful body is:

```json
{
  "ok": true,
  "schema": "a22x.install.status.v1",
  "operation": "status",
  "mode": "device",
  "boot_id": "CURRENT_BOOT_UUID",
  "capabilities": {
    "flash_boot": true,
    "install_stock": true,
    "resume": true,
    "android_mount": false,
    "android_backup_restore": false,
    "userdata_reset": false
  },
  "required_hold_ms": {"flash-boot": 3000, "install-stock": 5000},
  "backup_required": true,
  "stock_profile": {"available": false, "id": null, "firmware": null,
                    "qualified": false, "blockers": ["STOCK_PROFILE_UNAVAILABLE"]},
  "blockers": [],
  "pending": [],
  "auto_reboot": false
}
```

`install_stock:true` means the planner/operation exists. A missing or
unqualified stock profile is reported in `stock_profile.blockers` and blocks
stock execution. It does not disable BOOT-only planning. Every status profile
includes `available`, `qualified` and `blockers`, including an absent profile.
Top-level `blockers` describes global guard errors for both operations.
Each pending item has `transaction_id`, `operation` (`flash-boot` or
`install-stock`), `journal_volume`, `journal_path`, `journal_volume_token`,
`state`, and `resumable`. The UI must not begin a new operation over a pending
transaction. `resumable:true` allows an explicit `resume-plan`, never an
automatic replay of `execute`.

Resume retains the original absolute Linux backup path, so it requires the
same SD mount location used for the initial review. If Files mounted that SD
at `/mnt/volumes/sdcard-p1`, unmount it there and use the normal Storage mount
at `/mnt/backup` before reviewing a transaction originally started there.
Status, the normal-boot guard and journal/checkpoint lookup use relative
journal paths and can still inspect the card at a temporary read-only mount.

## Compact plan response

`--output` creates one new, private, bounded RAM file containing the full
plan. The CLI returns the following bounded summary. The UI must retain and
match the selected source volume, source path and backup path verbatim.

```json
{
  "ok": true,
  "schema": "a22x.install.plan-result.v1",
  "operation": "stock-plan",
  "mode": "device",
  "boot_id": "CURRENT_BOOT_UUID",
  "plan_summary": true,
  "plan_path": "/run/a22x-recovery/ui/install-UNIQUE.json",
  "plan_file_sha256": "64_LOWERCASE_HEX",
  "plan": {
    "schema": "a22x.install.plan.v1",
    "operation": "install-stock",
    "mode": "device",
    "boot_id": "CURRENT_BOOT_UUID",
    "created_utc": 0,
    "expires_utc": 0,
    "transaction_id": "32_LOWERCASE_HEX",
    "seal": "64_LOWERCASE_HEX",
    "confirmation": "INSTALL-STOCK-16_HEX_PREFIX",
    "required_hold_ms": 5000,
    "can_execute": false,
    "blockers": ["STOCK_PROFILE_NOT_QUALIFIED"],
    "source_volume": "sdcard-p1",
    "source_path": "/stock/manifest.json",
    "source_volume_token": "64_LOWERCASE_HEX",
    "backup_path": "/mnt/backup/linux-SNAPSHOT",
    "backup_manifest_sha256": "64_LOWERCASE_HEX",
    "backup_gate": {
      "complete": true,
      "verified": true,
      "same_device": true,
      "current": true,
      "current_required": true,
      "current_userdata": true,
      "current_userdata_required": true,
      "profile": "linux"
    },
    "stock_profile": {"id": "PROFILE_ID", "firmware": "FIRMWARE_ID"},
    "write_targets": [
      {"label": "boot", "path": "/dev/mmcblk0p41",
       "partition_number": 41, "start_sector": 1434624,
       "size_bytes": 33554432, "action": "write-raw",
       "before_sha256": "64_LOWERCASE_HEX", "after_sha256": "64_LOWERCASE_HEX"},
      {"label": "recovery", "path": "/dev/mmcblk0p42",
       "partition_number": 42, "start_sector": 1500160,
       "size_bytes": 41943040, "action": "write-raw",
       "before_sha256": "64_LOWERCASE_HEX", "after_sha256": "64_LOWERCASE_HEX"}
    ],
    "skip_targets": [],
    "write_order": ["boot", "recovery"],
    "journal_volume": "sdcard-p1",
    "journal_path": "/.a22x-installs/32_LOWERCASE_HEX/journal.json",
    "journal_volume_token": "64_LOWERCASE_HEX",
    "checkpoint": {"path": "/mnt/backup/.a22x-installs/TRANSACTION/platform",
                   "labels": ["boot", "recovery", "dtbo", "vbmeta", "vbmeta_system"],
                   "required_bytes": 84017152},
    "next_action": "stock-recovery-factory-reset",
    "custom_recovery_replaced": true,
    "userdata_written": false,
    "automatic_factory_reset": false,
    "auto_reboot": false,
    "atomic": false,
    "resuming": false
  }
}
```

The sample target list is illustrative. Display the returned exact targets,
including every label/path/size and the `write_order`; do not hardcode the
example. `skip_targets` uses the same target fields with action
`skip-identical`. Byte-identical firmware is verified and skipped.
Changed targets use exactly `write-raw` or `write-sparse`, as returned.

A BOOT plan has operation `flash-boot`, confirmation prefix `FLASH-BOOT-`,
`required_hold_ms:3000`, `next_action:"none"`, stock_profile `null`, and
custom_recovery_replaced `false`. It additionally has `boot_header` with
validated layout fields and `runtime_boot_tested:false`. A valid header is
not a claim that an arbitrary selected kernel boots.

`runtime_boot_tested` is inside `plan.boot_header`. For stock,
`custom_recovery_replaced` is true exactly when RECOVERY is in the changed
write-target list; an already identical or already completed RECOVERY is
skipped. Every plan includes the four exact journal-binding fields shown
above. Match these to the selected pending item for a resume review.

Fresh stock requires `backup_gate.current_required:true` and `current:true`,
plus `current_userdata_required:true` and `current_userdata:true`. Stock
resume keeps current userdata verification but sets `current_required:false`
because the previous attempt may already have changed BOOT; its original
Linux BOOT and checkpoint remain bound to the transaction. BOOT-only plans
have both `current_required:false` and `current_userdata_required:false`;
`current:false` does not mean the stored backup failed verification.

BOOT-only planning requires BOOT unmounted/no holders but permits mounted
userdata. Stock planning/execution require the eight stock targets and
current userdata unmounted/no holders. Neither operation unmounts volumes
implicitly; the external source SD remains mounted writable ext4.

`blockers` is a bounded array of machine codes. `can_execute` must be exactly
true and the array empty before the UI enables the hold. Bad, incomplete,
stale or structurally unsafe input may instead produce `ok:false`.

## Local confirmation and execution

Use a continuous hold of **3000 ms** for BOOT, **5000 ms** for stock. Cancel or
leaving the review cancels the hold. Show the selected source, Linux backup,
write targets and the fact that stock replaces custom recovery. In stock
review, explicitly explain that userdata is unchanged by the installer and
the next deliberate stock-recovery factory reset erases Linux data.

Pass the returned full-file SHA, plan seal and exact confirmation token to
`execute`. The helper cannot attest a touchscreen hold; it enforces the exact
reviewed plan binding and confirmation token. Plans expire after at most
30 minutes and are bound to this recovery boot. Execution rechecks the full
source and destination identities after long reads, before opening targets
for writing. Input chunks are compared with their preverified checksums
before each write. After checkpoint creation, the helper rehashes every
reviewed target preimage, rechecks the exact runtime profile, repeats retained
firmware/active-selector proof, and refreshes source identities before any
writable target FD opens. The previous platform checkpoint and durable journal must
be complete before the first partition write. Every changed target receives
fsync and a complete independent readback. RECOVERY is the final stock write.

Execution progress uses newline JSON on **stderr**, with `event:"progress"`, `operation`,
`phase`, `partition`, `bytes_done`, `bytes_total`. The last line is a result:

Stdout contains exactly one final JSON response, never progress lines.

```json
{
  "ok": true,
  "schema": "a22x.install.result.v1",
  "operation": "execute",
  "install_operation": "install-stock",
  "mode": "device",
  "boot_id": "CURRENT_BOOT_UUID",
  "transaction_id": "32_LOWERCASE_HEX",
  "readback_verified": true,
  "journal_path": "/mnt/backup/.a22x-installs/TRANSACTION/journal.json",
  "checkpoint_path": "/mnt/backup/.a22x-installs/TRANSACTION/platform",
  "next_action": "stock-recovery-factory-reset",
  "userdata_written": false,
  "automatic_factory_reset": false,
  "auto_reboot": false,
  "atomic": false
}
```

Do not replay an uncertain execution. Refresh `status`, then review a fresh
`resume-plan` for the same exact transaction. An interrupted write is not
atomic; a damaged RECOVERY may require Download mode and external recovery
flashing. The small checkpoint is installer rollback material, separate from
the Linux BOOT/userdata snapshot. It does not imply a complete Android backup
or that bootloader anti-rollback permits arbitrary firmware downgrades.

## Normal-boot preflight

`boot-check` holds the shared lease and checks the RAM sentinel
`/run/a22x-recovery/install-transaction.json` plus durable transaction journals
on the admitted external SD. A known pending or malformed journal blocks an
intentional Normal boot. Recovery and Download are separate recovery actions.
An available unmounted ext4 SD can be temporarily mounted read-only with
`noload` for this explicit check; its previous mount state is restored.

Ordinary known Linux is allowed after hashing full BOOT and
DTBO/VBMETA/VBMETA_SYSTEM against the pinned tested baseline, provided no
available pending/malformed journal blocks it. If the SD is absent, the result
explicitly returns `journal_storage_present:false`,
`durable_state_checked:false`, `known_linux_boot_safe:true`; it does not claim
that the missing journal is absent. An unrelated empty SD cannot hide an
interrupted installation. For changed BOOT, the helper requires a completed
transaction on its original media token with all relevant current target
hashes matching the completed record. It returns that transaction identifier
in `verified_completed_transaction`; otherwise `BOOT_SAFETY_UNPROVEN` blocks
Normal boot. BOOT-only completion additionally requires the retained platform
baseline. The independent distro boot-check still checks the current Linux
rootfs.

Completion of a stock operation never triggers a reset or reboot. It leaves
the explicit stock-recovery factory-reset action to the user. A physical
power cut/bootloader boot is outside the recovery's intentional-boot guard.

## Stock profile and exclusions

The current fixed intended profile uses BOOT, DTBO, SUPER, VBMETA,
VBMETA_SYSTEM, PRISM, OPTICS and stock RECOVERY. It retains bootloader/LK/TEE,
modem, calibration, EFS, RPMB, PIT, metadata and userdata. Source AVB graph and
the retained firmware compatibility proof must qualify before a stock plan
can execute. No AVB flag disabling, content repair, signature replacement or
anti-rollback bypass is inferred from an unlocked bootloader.

CACHE/OMR are excluded from writes. Supplied CSC CACHE contains queued
`--data_resizing`/`--delete_apn_changes` recovery commands, and neither
partition is an AVB dependency of the audited profile. They must not silently
queue additional recovery behavior. Original PRISM/OPTICS bytes are retained;
their signed hashtree/FEC coherence was verified on write-sealed anonymous
host files. The complete source graph and retained firmware evidence are
recorded in the qualified runtime profile; an unqualified profile stays blocked.

When only one hardware boot area matches the stock BOOTLOADER dependency,
each stock admission and the final prewrite gate read the current selector
using a fixed `MMC_IOC_CMD` SEND_EXT_CSD (CMD8) on the proven internal whole
MMC opened `O_RDONLY`. Opcode, argument, flags and 512-byte length are fixed;
there is no command-selection API or boot-configuration write. The reader
checks CID, device geometry, boot-area size and the qualified active selector.
Raw EXT_CSD register bytes are not persisted. The small profile metadata
contains the captured proof and expected selector; a historical boolean alone
does not authorize a future installation.
