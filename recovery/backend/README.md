# A22x recovery storage backend

This backend is intended to run **inside a separate recovery image with a RAM root filesystem**, not as a recovery application on the installed Linux root. It does not require Android userspace, TEE initialization, FBE decryption, BROM, or a changed bootloader.

The current implementation has two explicitly different adapters:

| Adapter | Implemented operations | Write scope |
|---|---|---|
| `--fixture-root ROOT` | Inventory, backup planning/execution, verification, restore planning/execution | Only validated regular files inside an explicit synthetic fixture root |
| `--device`, raw v1 | Live inventory, offline backup planning/execution, verification, restore planning | New files on external backup storage; generic raw/Android physical restore remains disabled |
| `--device`, explicit Linux v2 | Compact ext4 backup/verify; separately qualified manual wipe/restore | Only exact userdata and optional BOOT under the tested build/tool/device capsule and reviewed v2 plan |

`DEVICE_RESTORE_NOT_QUALIFIED` remains the hard boundary on the original generic raw device writer. The separate Linux v2 workflow never removes that guard: it requires a matching implementation qualification capsule, exact allowlisted physical geometry, verified compact backup and fresh explicit plan. Without qualification its physical writer stays inaccessible. Backend tests use owned regular files and simulated topology; they do not write physical partitions. See [LINUX_WORKFLOW.md](LINUX_WORKFLOW.md) for the compact format, qualification process, UI contract and manual wipe/restore failure semantics.

## Backup profiles

| ID | Raw partition images | Meaning |
|---|---|---|
| `linux` | `boot`, `userdata` | Installed Linux boot image and root filesystem |
| `android` | `boot`, `metadata`, `userdata` | Android boot and encrypted data state, with an exact retained-firmware prerequisite |
| `recovery` | `recovery` | Recovery image only |

These are **not full-device backups**. They do not capture or write bootloader, modem, EFS, RPMB, GPT/PIT, or other private calibration/state partitions. Android mode reads `super`, `tee1`, `tzar`, `dtbo`, and `vbmeta` only to compute the retained compatibility baseline; it does not back them up for writing.

### Android compatibility

During an explicit Android backup plan, the production adapter discovers the five fixed retained labels through `/dev/disk/by-partlabel`, then checks the kernel GPT name, partition geometry, same internal eMMC ancestry, absence of mounts and absence of holders. It hashes exactly the reported partition bytes, with progress output. This does not require a host-preconfigured Android fingerprint or a guessed Android version.

The manifest records actual SHA-256 values and geometry. Restore planning recomputes them and rejects changed retained firmware. An optional fingerprint and `androidboot.bootloader` string are informational labels; the byte/geometry comparison is the compatibility gate. These checks do not attest RPMB counters or secure-world state and cannot guarantee that old FBE blobs will be usable after unrelated secure-state changes.

The backend reads only the first 8 KiB of `userdata` to classify filesystem magic. For this A226B stock profile, an obvious ext4 or LUKS image is rejected as an Android backup source, with a suggestion to use the Linux profile. F2FS or opaque data is not called proof of Android. The operator still selects the profile; the backend never mounts or decrypts it.

## Raw v1 backup transaction

1. Resolve exact allowlisted partitions and source size/identity.
2. Prove sources are unmounted, including device-mapper/loop dependencies and all readable mount namespaces.
3. Require the repository on a different physical disk, such as external SD/USB.
4. Bind the plan to device identity, partition objects, repository identity, selected profile and a confirmation token.
5. Acquire one operation lock and revalidate the reviewed plan.
6. Stream raw bytes through zstd, or gzip if zstd is unavailable. Compute raw SHA-256 and compressed SHA-256.
7. Decode and verify every new archive while source handles remain open.
8. Write/fsync a manifest and completion receipt. Publish the entire `.partial` directory using Linux `renameat2(RENAME_NOREPLACE)` and fsync the repository.

The compression estimate is deliberately `null`. The source can be logically 53 GB while the compressed backup fits a smaller SD card. The engine does not require free space equal to raw size. Instead, it enforces a 16 MiB reserve while streaming and propagates actual ENOSPC errors. Failure or interruption leaves an explicitly incomplete `.partial-*` directory and, where space permits, `FAILED.json`; it never publishes a false `COMPLETE.json`. Previous completed backups are not overwritten or deleted. Partial resumption and automatic cleanup are not implemented.

Large-file-capable external filesystems are supported by the device adapter. FAT32 is deliberately excluded because this version does not split archives at the 4 GiB boundary. Compression does not encrypt the backup container.

## Restore transaction in the fixture executor

The implementation performs real restoration of regular-file partition fixtures with **zero decoded image staging**. Before the first target write it verifies the manifest seal, exact profile set, snapshot pairing, device identity, target sizes, firmware compatibility, raw/archived hashes and storage topology. Manifest parsing and seal hashing use the same bounded bytes from one pinned descriptor.

Every archive stays open across two decoding passes. The first pass verifies its complete compressed and raw SHA-256 values and builds an immutable SHA-256 index for canonical 1 MiB decoded chunks. Only a completely verified index can be used. The second pass verifies each immutable decoded buffer against that index **before that buffer reaches the writer**. A corrupt or changed source cannot inject a different chunk between preverification and writing. The original `a22x.backup.v1` gzip/zstd format remains supported; existing backups need no conversion.

The index uses 32 bytes per 1 MiB, allocated only after an entry matches admitted target geometry. The example A226B Linux profile (`userdata` 53,393,489,920 bytes plus a 32 MiB `boot`) needs 1,630,464 bytes of index data. Raw size is capped at 128 GiB per partition, the same ceiling as the production adapter; each individual index is therefore at most 4 MiB. Compression and raw data are streamed through bounded buffers. zstd decoding additionally limits its frame window to 128 MiB. The host test restores 256 MiB under a 64 MiB process address-space limit, using real gzip archives and full readback. This is evidence for the streaming mechanism; it is not a physical 53 GB phone restore test.

All target objects are pinned via `O_PATH`, proven to be regular files, and only then reopened through their own `/proc/self/fd` handle. A FIFO, symlink, replaced pathname or device node cannot turn fixture mode into a physical device write.

The writer rechecks regular-file type, pinned/named identity, exact size and expected offset before every write, including retries after positive short writes. No write may exceed the admitted geometry. Write order is `userdata`, then `metadata` when present, then `boot`; `recovery` is separate. Each target is fsynced and independently read back before the next partition starts.

A restore is **not atomic across partitions**. An append-only, fsynced intent journal is required before the first write. It records partition boundaries, completion and caught failures, and the final receipt separately identifies each partition's verified input prefix, remaining unconfirmed bytes, fsync completion and readback result. I/O failures and interrupts carry the same receipt through the JSON CLI. An earlier journal is never replaced by retrying its plan. `auto_reboot` stays false.

The reported prefix counts successful write return values; the remainder is not a guarantee of untouched storage after an in-flight syscall or power loss. Abrupt termination may leave more writes than the last durable checkpoint. Fixture journals live inside their fixture; a fixture in `/tmp` or `/run` loses its journal on reboot. Persistent physical-device journaling and power-loss acceptance are deliberately not claimed. See [STREAMING_RESTORE.md](STREAMING_RESTORE.md) for the transaction, failure contract and qualification limits.

## CLI contract

Run from the project root. Native UI callers should use `execve`/`fork+exec` argument arrays rather than a shell.

```text
python3 backend/recoveryctl.py (--fixture-root ROOT | --device) inventory
python3 backend/recoveryctl.py (...) list --repository DIR
python3 backend/recoveryctl.py (...) backup-plan --profile linux|android|recovery \
    --repository DIR --name NAME --output PLAN.json [--compression auto|zstd|gzip] [--format raw|partclone-ext4]
python3 backend/recoveryctl.py (...) backup --plan PLAN.json --confirm TOKEN
python3 backend/recoveryctl.py (...) verify --backup BACKUP_DIR
python3 backend/recoveryctl.py (...) restore-plan --backup BACKUP_DIR --output PLAN.json
python3 backend/recoveryctl.py (...) restore --plan PLAN.json --confirm TOKEN
python3 backend/recoveryctl.py (...) wipe-plan --backup LINUX_V2_BACKUP_DIR --output PLAN.json
python3 backend/recoveryctl.py (...) wipe --plan PLAN.json --confirm TOKEN
```

One JSON object is written to stdout. Progress is newline-separated JSON on stderr, throttled to approximately 5 updates/s per active phase, plus phase/final events. A completed listing verifies the manifest's completion seal; `content_verified_this_listing=false` explicitly distinguishes listing from full archive verification.

Plan response:

```json
{
  "ok": true,
  "operation": "restore-plan",
  "plan_path": "/run/a22x-recovery/plans/restore-123.json",
  "plan_id": "sha256-of-plan-content",
  "confirmation_token": "RESTORE-first16hex",
  "can_execute": false,
  "blockers": ["DEVICE_RESTORE_NOT_QUALIFIED"],
  "summary": {
    "profile": "android",
    "partitions": ["boot", "metadata", "userdata"],
    "raw_bytes": 53460598784,
    "compressed_estimate_bytes": null,
    "backup_path": "/mnt/backup/A22xBackups/example",
    "full_device_backup": false,
    "requires_retained_firmware": true,
    "write_targets": [],
    "auto_reboot": false
  }
}
```

The example is structural; actual plan IDs, targets and sizes are generated from inspected inputs. Confirmation tokens bind a user review to one exact plan. They are not secrets or cryptographic authentication.

Error response:

```json
{"ok":false,"operation":"restore","error":{"code":"DEVICE_RESTORE_NOT_QUALIFIED","message":"Physical block restore is not enabled in this prototype","details":{}}}
```

Exit codes: `0` success, `2` input/admission/integrity failure, `3` operating-system I/O failure, `4` unqualified physical restore, `130` interruption. Errors never count as a successful backup/restore.

## Device integration

Install all `backend/*.py` files together, including `streaming.py` and `restore_support.py`, and place the shared project `operation_lock.py` in the backend directory's parent. Copy `device.example.json` to `/etc/a22x-recovery/device.json` after confirming the geometry. Python 3 needs its normal stdlib plus `zlib`, `fcntl`, `_ctypes`/libffi and hashing modules. zstd is optional; gzip remains a functional fallback. `renameat2` must be supplied by libc for atomic, non-replacing snapshot publication.

Device operations use the same cooperative lease as UI filesystem operations and authorized SSH/SFTP maintenance sessions. An inherited `A22X_RECOVERY_LOCK_FD` is accepted only by the shared helper after checking that the descriptor names the exact lock inode and can hold its flock. Closing a borrowed duplicate never explicitly unlocks the parent session. Fixture mode ignores the inherited descriptor and retains an independent fixture lock.

The native adapter requires a DT compatible list containing `samsung,a22x` and `mediatek,mt6833`, eMMC CID identity, kernel GPT by-label links and sysfs geometry. Backup execution additionally requires UID0, a real `rootfs`/`ramfs`/`tmpfs` root, and:

```json
{"schema":"a22x.recovery.boot.v1","boot_id":"current /proc/sys/kernel/random/boot_id"}
```

at `/run/a22x-recovery/boot.json`. This marker is a consistency check in addition to real mount/geometry checks; it is never permission for physical restore. The init process must create `/run/lock` and `/run/a22x-recovery/plans`. Device plans may only be stored in the latter directory.

External media mounting belongs to a separate explicit helper. The backend does not mount anything. An example configured repository is `/mnt/backup/A22xBackups` on `/dev/mmcblk1p1` ext4. It checks the actual backing mount and disk ancestry rather than trusting that pathname.

## Reproducible local demonstration (fish)

These commands create only synthetic regular files and operate inside that new directory.

```fish
set demo /tmp/a22x-recovery-demo-(date +%s)
python3 backend/demo_fixture.py $demo
python3 backend/recoveryctl.py --fixture-root $demo inventory
python3 backend/recoveryctl.py --fixture-root $demo backup-plan \
    --profile linux --repository $demo/storage/sd --name linux-demo \
    --output $demo/plans/backup.json
set token (python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["confirmation_token"])' $demo/plans/backup.json)
python3 backend/recoveryctl.py --fixture-root $demo backup --plan $demo/plans/backup.json --confirm $token
python3 backend/recoveryctl.py --fixture-root $demo verify --backup $demo/storage/sd/linux-demo
python3 backend/recoveryctl.py --fixture-root $demo restore-plan \
    --backup $demo/storage/sd/linux-demo --output $demo/plans/restore.json
set token (python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["confirmation_token"])' $demo/plans/restore.json)
python3 backend/recoveryctl.py --fixture-root $demo restore --plan $demo/plans/restore.json --confirm $token
```

## Tests and remaining limits

```text
python3 -m unittest discover -s backend/tests -v
```

The tests exercise actual gzip/zstd archives and regular-file writes/readbacks, corruption and truncation before writing, changed valid gzip after preverification, pinned archive replacement, target shrink/replacement, positive short writes, zero writes, I/O failure receipts, readback corruption stopping boot, interrupts, journal ENOSPC, hostile manifest size bounds, inherited lock semantics and a 256 MiB restore under a 64 MiB address-space limit. The earlier profile, firmware, topology, symlink/FIFO and backup-transaction coverage remains included. Synthetic topology tests are not evidence that a physical restore has run on the phone.

For a small acceptance run on a host or in recovery, use the owned regular-file harness. This needs approximately 50 MiB free in `TMPDIR` for the 48 MiB case, with additional operating headroom. It creates and removes only its new fixture, accepts no device target, and includes the failure receipt in its JSON output:

```fish
env TMPDIR=/tmp python3 backend/tests/streaming_smoke.py \
    --raw-mib 48 --address-space-mib 64 --fault-late-chunk
```

The fault stage deliberately bypasses metadata-change detection **only inside this test process after full preverification**. It changes a later chunk of a valid gzip stream and requires the independent per-chunk SHA guard to reject that chunk, preserve later fixture bytes and leave fixture boot untouched. This fault injection is not a production flag, and it never changes the physical restore guards.

Every release image still needs its own ARM runtime and storage acceptance. Independent raw writers or malicious root processes are outside the cooperative consistency model of the minimal recovery. SHA-256 receipts provide integrity, not signed provenance. Existing historical TWRP/raw backup formats are not silently imported; they need a separately validated converter. Android FBE restore usability and persistent secure state remain separate from byte-perfect raw restoration.
