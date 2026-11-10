# Streaming restore contract and qualification boundary

## Status

The v0.3 backend restores completed `a22x.backup.v1` gzip/zstd snapshots into validated regular-file partition fixtures. It does not stage decoded partition images. Production inventory, offline backup, verification and restore planning continue to work through the device adapter. **Physical partition restore is disabled** at the engine entry point and the device adapter; the streaming writer also insists that its target descriptor and pathname identify the same regular file.

The implementation does not flash boot, change the partition table, format filesystems, decrypt data or reboot. `boot` in a Linux fixture snapshot is restored last, after the complete `userdata` target passes fsync and independent raw SHA-256 readback. This is a byte restoration engine, not yet a Linux kernel update installer.

## Why two passes and a chunk index

A full decoded temporary image would require roughly 53 GB of staging for this phone's Linux root partition. That does not fit the recovery's RAM root. Checking only the archive's full hash and then opening it again would permit changed input between verification and writing.

The backend pins every archive with a validated regular-file descriptor and shared flock. All targets are independently pinned and exclusively locked. Archive/target aliases are rejected. The first pass hashes the complete bounded archive, decodes its exact admitted raw length, checks the complete raw SHA-256 and trailer, and checks that the open archive's stamp remained unchanged. It collects hashes of canonical 1 MiB raw chunks during this pass. These hashes become immutable trusted input only after the entire pass succeeds.

The second pass decodes the same pinned descriptor. Each chunk is an immutable `bytes` buffer and its SHA-256 must equal the corresponding trusted first-pass hash before the writer receives it. Metadata checks give an additional early refusal when an archive changes; safety does not depend solely on those checks. Tests deliberately suppress timestamp detection and replace a later chunk in a valid gzip stream. The changed chunk is refused before any byte of that chunk reaches the target.

This mechanism preserves the existing v1 backup format. There is no new on-media chunk list to trust, no sidecar file requiring its own sealing protocol, and no need to recompress existing backups. The cost is a second decode during restore plus target readback. Restore planning performs another full verification so the reviewed plan refers to an already verified snapshot.

## Bounds

| Resource | Bound or rule |
|---|---|
| Manifest/plan JSON | 1 MiB bounded regular file; parsing and seal hash use the same bytes |
| Partition selection | Exact known profile, at most three labels |
| Declared raw partition length | Positive integer, at most 128 GiB, exact admitted target size |
| Declared compressed length | Positive integer, at most 256 GiB; actual file size must match |
| Raw decoding/write chunk | 1 MiB, except the exact shorter final chunk |
| Hash index | 32 bytes per raw chunk, at most 4 MiB per partition |
| Decoded staging | Zero bytes |
| zstd window | `-M128MB`; process overhead is additional |
| Target writes | Exact pinned regular file, current size and offset checked before every write |
| Target readback | Exactly the admitted raw length, then EOF/identity/stamp check |

For the device example, `userdata` is 53,393,489,920 bytes, which needs 50,920 chunk hashes or 1,629,440 index bytes. A 32 MiB `boot` adds 1,024 index bytes. The combined Linux index is 1,630,464 bytes, approximately 1.56 MiB. Python/decoder/buffer overhead is additional, and conversion of the temporary index to immutable bytes temporarily duplicates one index. Even the absolute profile limits keep index allocation bounded.

zstd is optional and uses a subprocess. Its diagnostic stderr currently goes to an anonymous temporary error file; only 4 KiB is read into an error message. The packaged gzip-only recovery path does not create this file. A future zstd runtime qualification should additionally bound the diagnostic file and measure actual process memory before treating the whole decoder environment as qualified on the phone.

## Transaction and receipts

The executor revalidates the reviewed plan, manifest seal, profile, device identity, all target geometry/stamps, repository identity, unmounted topology and any retained-firmware requirement. It pins every archive and target, fully verifies all archives and constructs all target-bound indexes. It then rechecks target stamps and current storage admission. No target write occurs during these steps.

`results/<plan-id>.progress/` is created exclusively. A fsynced `started` record is mandatory before the first write. A record precedes each partition and follows each successfully fsynced/read-back partition. Caught errors append a failed record and write `results/<plan-id>.json` where possible. A complete restore appends a complete checkpoint and writes the final receipt. Any pre-existing journal or final receipt for that plan blocks another execution. An operator must inspect the failure and make a fresh plan; there is no automatic resume.

The receipt retains the existing `a22x.restore.receipt.v1` schema and adds explicit per-partition state:

| Field | Meaning |
|---|---|
| `verified_prefix_bytes` | Sum of successful write return values, all from input buffers checked against the trusted chunk index |
| `remaining_bytes` | Bytes not yet confirmed written; not proof that those bytes are untouched after a failing/in-flight syscall |
| `fsync_completed` | The full partition write finished and target fsync returned successfully |
| `readback_verified` | Full independent bounded raw readback matched the original SHA-256 and target identity/stamp remained stable |
| `completed_partitions` | Partitions that finished both fsync and readback, in write order |
| `active_partition` | Partition active at the failure, or `null` on successful completion |
| `decoded_staging_bytes` | Always zero for this implementation |
| `verify_each_chunk_before_write` | True; the second decode must pass each stored chunk hash |
| `auto_reboot` | Always false |

Positive short writes are retried while recording every accepted byte. Zero or invalid progress stops the transaction. An exception during the first data partition prevents later `boot` writes. A source change after an earlier partition completes may leave that completed partition restored while the remaining partitions retain their previous or partial state. The transaction is not atomic.

Both `RecoveryError` and operating-system/interrupt exceptions carry the failure receipt back to the CLI. A failed attempt may also fail to persist a receipt, for example after journal ENOSPC or media loss. The returned receipt includes `failure_receipt_error` when that secondary failure is observed. The mandatory initial intent checkpoint prevents starting a write without any journal at all.

## Abrupt termination and concurrency

The journal has partition checkpoints, not an atomic log of each write syscall. SIGKILL, sudden disconnect or power loss can leave more bytes written than the last journal checkpoint reports. A checkpoint is a lower bound on acknowledged progress, not an authoritative map of the disk. There is no rollback, replay, automatic resume or automatic reboot. A journal on a RAM filesystem is lost at reboot. These limitations are material for physical restore and are why a successful fixture restore does not enable the device adapter.

The operation lock is cooperative. UI writers, backups and authorized root SSH/SFTP sessions share the same device lease. A helper in a maintenance session can borrow the validated inherited descriptor without unlocking its parent. Fixture locks are independent and ignore inherited descriptors. Uncooperative root writers, kernel failures and storage that lies about flush completion remain outside the software consistency guarantee. Final readback detects ordinary incorrect writes but cannot prove later persistence through arbitrary hardware failure.

## Tested and not yet qualified

Host tests cover real gzip/zstd round trips; all-archive preverification before the first write; archive corruption, truncation and replacement; changed valid gzip after preverification with timestamp detection deliberately bypassed; bounds against hostile sizes; target shrink and pathname replacement; positive/zero short writes; I/O and interruption receipts; initial-journal failure; data corruption detected by readback before boot; inherited session locks; and a real 256 MiB restore with a 64 MiB process address-space limit.

The small `tests/streaming_smoke.py` harness creates a fresh owned regular-file fixture, runs backup, preverification, streaming restore and independent readback, and can inject a later source chunk change after preverification. Its JSON output includes the negative-test receipt and confirms that the rejected chunk, later fixture bytes and fixture boot remained unchanged. It accepts no target path and cannot switch to the device adapter.

Physical-device restore still needs independently reviewed target admission at write time, durable external journals, controlled I/O and interruption acceptance on owned disposable block media, external source-removal behavior, actual full-size performance/memory measurement, and deliberate power-loss recovery procedures. None of those gates is bypassed by this implementation. Linux boot-image updates additionally need their own model/header/geometry/rootfs compatibility validation and update/rollback contract; a raw restore routine alone is insufficient.
