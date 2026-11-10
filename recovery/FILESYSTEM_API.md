# Filesystem access API — v0.3

`filesystem.py` supplies real filesystem mounts and file operations for the
dedicated SM-A226B RAM recovery. It is separate from the raw-image backup engine.
It has no format, raw partition write, Android credential unlock, or bootloader
operation. Device mode requires the A22x/MT6833 device tree, the current recovery
boot receipt, `a22x.recovery=1`, root privileges and a RAM root filesystem.

## Commands

The global selector is either `--device` or `--fixture ROOT`, before the command.
All commands emit one JSON object on stdout. Success has `ok: true`, `operation`
and `mode`. Top-level `mode` is always the adapter name, `"device"` or `"fixture"`;
it never contains numeric file permissions. These three envelope fields are
reserved and cannot be overwritten by an operation payload. Errors have `ok: false` and
`error: {code, message, details}`; exit status is nonzero. File contents are never
logged to stderr.

| Command | Arguments | Result fields |
| --- | --- | --- |
| `inventory` | — | `volumes`, `crypto`, `capabilities` |
| `mount` | `--volume ID --mode ro\|rw` | `volume`, `mount_options` in device mode |
| `unmount` | `--volume ID` | `volume` |
| `unmount-all` | — | `unmounted` IDs; only mounts owned by this helper |
| `list` | `--volume ID --path /path --offset N --limit N` | `entries`, `offset`, `total`, `has_more`, `read_only` |
| `read` | `--volume ID --path /file --max-bytes N` | `content`, `size`, `sha256`, `hash_complete`, `truncated`, `read_only`, numeric `file_mode` |
| `stat` | `--volume ID --path /entry` | `entry`, `read_only`, `write_supported` |
| `resolve` | `--volume ID --path /link` | `resolved_path`, `entry`, `links`, `read_only` |
| `chmod` | `--volume ID --path /entry --mode 0644 --expected-entry-token HASH` | `entry_before`, `entry`, `changed`, `cleared_special_bits` |
| `chown` | `--volume ID --path /entry --uid N --gid N --expected-entry-token HASH` | Same; at least one of `--uid` / `--gid` is required |
| `write` | `--volume ID --path /file --text-file RAM_FILE --expected-sha256 HASH` | `bytes_written`, `sha256`, `created` |
| `write` new file | Same, with `--create` instead of `--expected-sha256` | Same |
| `mkdir` | `--volume ID --path /directory` | `created` |
| `rename` | `--volume ID --path /source --destination /destination` | `path`, `destination` |
| `copy` | `--volume ID --path /file --destination-volume ID --destination /destination` | `bytes_copied`, `sha256`, `posix_metadata_preserved` |
| `copy-tree` | `--volume ID --path /directory --destination-volume ID --destination /new-directory --expected-entry-token HASH` | `bytes_copied`, `files`, `directories`, `symlinks`, `hardlinks_preserved`, `posix_metadata_preserved`, `content_manifest_sha256`, `published` |
| `delete` | `--volume ID --path /entry` | `deleted_type` |

`mount --mode rw` and every file-changing command require `--confirm`. The UI
should obtain that confirmation from a deliberate local hold on its review
screen. `unmount` uses a normal unmount and fails if busy; there is no force or
lazy option. RO→RW changes use an ordinary unmount and a fresh RW mount.

Every selected-volume command accepts `--expected-volume-token HASH`. The UI
passes the token from the selected inventory entry. `copy` and `copy-tree` additionally accept
`--expected-destination-volume-token HASH`. `rename`, `copy` and `delete` accept
an optional `--expected-sha256 HASH` for an already opened regular file.
`stat` and `resolve` accept an optional `--expected-entry-token HASH`; `chmod`,
`chown` and `copy-tree` require it. Entry tokens come from `stat`, `list` or `read`.
They bind the selected path, inode, mode, owner, link count and modification
stamp to its volume and mount. They are distinct from both content hashes and
volume tokens. A directory entry token identifies that directory itself, not a
recursive content snapshot; `copy-tree` takes and verifies its own tree snapshot
while running.

Paths are relative to the selected volume; an initial slash is accepted. Root
is `/`. Dot/parent components, duplicate interior separators, NUL bytes and
overlong names are rejected. The filesystem root cannot be renamed or deleted.

### Inventory

Each volume includes:

```json
{
  "id": "userdata",
  "label": "userdata",
  "fs_type": "ext4",
  "mountable": true,
  "mounted": true,
  "read_only": true,
  "mountpoint": "/mnt/volumes/userdata",
  "mount_owner": "filesystem",
  "block_read_only": true,
  "read_only_guarded": true,
  "write_supported": true,
  "volume_token": "...64 lowercase hex characters...",
  "reason": null,
  "reason_code": null
}
```

`reason` and `read_only_reason` are human-readable; their `_code` counterparts
are machine-readable. A volume can be mounted RO while `write_supported` is
true: the user has not enabled writes. Existing external microSD mounted by the
Storage screen at `/mnt/backup` is recognized as `mount_owner: "storage"` and
can be browsed without a second mount. Changing that mount's mode requires
unmounting it first. `unmount-all` leaves the Storage-owned mount to `storage.py`.

Directory entries contain `name`, `path`, `type`, `size`, numeric permission
`mode`, numeric `uid` / `gid`, `nlink`, `mtime_ns`, `entry_token`,
`metadata_supported` and `metadata_reason`. `mode` remains an integer: `420`
means octal `0644`; the UI formats it as four octal digits. Types are `directory`,
`file`, `symlink`, and `other`. `read` includes numeric permissions as `file_mode`,
alongside `uid`, `gid`, `nlink`, `entry_token`, `metadata_supported` and
`metadata_reason`. Its top-level `mode` remains the adapter name. In `stat` and
directory listings, permissions remain `entry.mode` / `entries[].mode`.
Pagination limit is 1–256; the native UI uses seven entries
per page.

`stat` returns one entry with the same fields. A symlink also has `link_target`
containing its literal target. Inspecting a symlink or special file uses an
`O_PATH` descriptor; no target, FIFO or device is opened for data. Normal
`list`, `read` and mutation paths continue to reject intermediate symlinks.

Capabilities `file_metadata`, `chmod`, `chown`, `resolve_symlinks` and
`copy_directories` are true in v0.3. They describe implemented APIs, not writable
permission for every volume or entry. Check the selected volume and entry too.

Text operations use UTF-8 without NUL bytes. The editor writes at most 1 MiB.
`read --max-bytes` is also capped at 1 MiB. For files up to 1 MiB, `sha256` is the
hash of the whole file even when a smaller preview is requested. Larger files
have a bounded preview, `truncated: true`, `sha256: null`, and
`hash_complete: false`; they cannot be saved through the editor. A truncated
preview must never be treated as complete replacement text. Production edit
inputs must be regular RAM files under `/run/a22x-recovery` or `/tmp`.

### Explicit symlink resolution

`resolve` is a read-only operation returning a canonical volume-relative path;
it does not mount another volume or change the policy of `read` / `list`.
Absolute link targets are interpreted relative to the selected volume root.
For example, `/bin/tool -> /usr/bin/tool` resolves to `/usr/bin/tool` inside that
volume, never to the recovery RAM filesystem's `/usr/bin/tool`.

Relative `..` components are allowed only while they remain inside the volume.
An attempted escape is rejected. The resolver admits at most 40 links and 256
expanded components, checks every descriptor's mount identity, and revalidates
the link inodes and final target before returning. `links` is an ordered list of
`{path, target}` objects. `entry.path` equals `resolved_path`; its type lets the UI
offer a separate explicit Open action for a file or directory. Loops, dangling
targets and nested mounts are errors. A resolved special file is inspectable
metadata only; ordinary text reading still rejects it.

The optional expected entry token selects a final entry whose parent path is
already canonical. To resolve a path containing intermediate symlinks directly
from the CLI, omit that optional token; the resolver still validates the entire
walk before returning.

### Linux permissions and ownership

`chmod` accepts three or four octal digits, from `0000` to `7777`. `chown` accepts
decimal UID/GID values from 0 through 4294967294; names and the kernel's
leave-unchanged sentinel are not accepted. Omitting one owner field preserves
that field. Permissions and ownership are separate reviewed commands.

Both operations require an RW ext4/F2FS volume, `--confirm`, and the current
entry token. They accept one regular file with one hard link or one directory;
they do not recurse. Mounted roots, symlinks, special files, multiply linked
regular files and other filesystem types are rejected.
`metadata_supported: false` and `metadata_reason` explain this per-entry policy.

The inode is pinned before the metadata syscall, and path/mount identity is
checked before and after. A no-op avoids the syscall. An ownership change may
make the kernel clear set-ID bits or file capabilities; the helper does not
silently restore those privileges. The result contains the actual new metadata
and `cleared_special_bits`, an integer mask of cleared setuid/setgid bits.
An inode metadata update is not an atomic replacement: a later sync or identity
failure can occur after the syscall. Such an error carries
`metadata_change_attempted: true`; refresh `stat` to inspect the observed state.

## Filesystem and encryption scope

The device adapter discovers eMMC/microSD partitions through kernel-owned
sysfs, validates block major/minor numbers and geometry, and probes only
filesystem candidates. It does not require `blkid`, which is absent from the
current BusyBox runtime. Bounded superblock parsing recognizes ext4, F2FS,
EROFS, FAT and exFAT, including UUID/serial identity. The detected filesystem
must also appear in `/proc/filesystems`.

Userdata, cache, metadata and named Android filesystem partitions are potential
filesystem candidates. EROFS is always RO. Android verified system/vendor-like
filesystems remain RO until their write/AVB behavior has been qualified.
Encryption-enabled ext4/F2FS are admitted for RO access to whatever the kernel
can read, but RW is withheld while file keys remain unqualified. An encrypted
or unrecognized userdata image is shown as unavailable, never silently mounted
as a guessed type. Reading a filesystem root is not evidence that Android CE
data has been decrypted.

Boot, recovery and vbmeta are raw images. Super is a logical-partition
container; this helper does not map its contents yet. Bootloader, modem, TEE,
calibration and other service partitions are not opened for probing or mounted.
`crypto.android_decryption`, credential unlock and fscrypt key installation
remain false. These capabilities are not enabled by a writable Linux mount.

## Read-only guarantee and coordination

The exact #107 ext4 source can temporarily clear `SB_RDONLY` during orphan
cleanup even with `ro,noload`. Consequently, the helper sets **the selected
partition's** block-level RO flag with `BLKROSET(1)`, verifies it with `BLKROGET`,
and only then requests an RO mount. It also uses `noload` for ext4 and
`norecovery` for F2FS. It does not set the entire eMMC disk read-only.

The prior block RO state is recorded in RAM, tied to the current boot ID and
the physical/filesystem volume token. Only a flag that this helper originally
changed from writable is restored after a verified normal unmount. A preexisting
RO flag is never cleared. A busy mount, changed device, or active block holder
keeps the guard intact. A failed mount leaves a recoverable receipt; an unknown
mount is never forcibly removed. Source references for the additional guard:
the current kernel tree's `fs/ext4/orphan.c` (`bdev_read_only` early return and
temporary `SB_RDONLY` clearing) and `fs/ext4/super.c` (separate orphan cleanup).

All operations share `/run/lock/a22x-mainline-recovery.lock` through the shared
`operation_lock.py` helper with the existing raw-image backend and Storage helper.
Device mode can borrow an inherited maintenance-session lock descriptor through
`A22X_RECOVERY_LOCK_FD`; the descriptor must identify the same regular lock file
and acquire the same nonblocking flock. Closing a borrowed descriptor does not
unlock the parent session. Fixture mode ignores this environment variable.
Raw snapshots already refuse mounted
source partitions. Each file operation pins its mounted root and walks with
directory descriptors and `O_NOFOLLOW`; mount IDs, filesystem identity, file
inodes and modification stamps are rechecked before publishing a change.
Nested mounts are not traversed. The lock coordinates these recovery tools;
it is not a security boundary against an unrelated hostile root process.

## Write behavior

Text replacement is hash-bound and uses a temporary file plus atomic rename.
Owner, mode and extended attributes—including POSIX ACL and security labels—
are preserved and verified. Destination-only inherited attributes are removed,
so a parent default ACL cannot silently grant access absent from the original.
An inability to preserve metadata fails before replacement. Hard-linked files
are rejected for text replacement rather than silently breaking link semantics.

`copy` streams one regular file and verifies its source remained unchanged.
Copy and rename never overwrite an existing destination. Cross-volume copy is
supported; cross-volume rename is not. FAT/exFAT destinations cannot preserve
POSIX ownership and extended attributes, and copy reports that explicitly.
Delete accepts a regular file or an empty directory only. Recursive delete,
partition formatting and filesystem repair are not implemented.

### Directory copy

`copy-tree` copies a selected directory to a **new destination name** on ext4 or
F2FS. An existing name, including an empty directory or dangling symlink, is
never overwritten or merged. The source may be RO. A mounted root cannot be
selected, and the destination cannot be inside the selected tree, including
through an aliased fixture-volume root.

The complete source is inspected before creating a destination. Limits are
10,000 entries including the root, 64 levels below it, and 8 GiB of unique
regular-file logical content. Paths must still fit the 4096-byte file-manager
limit. Capacity checks include a metadata reserve. Sparse files are streamed
as logical bytes, so sparse allocation is not preserved.

Regular files, directories and symlinks are supported. Symlinks are copied
literally and never followed, including absolute, dangling or outward targets.
Hard links between entries within the copied tree are preserved; external hard
links remain outside the copy. FIFO, socket and device entries, and any nested
mount, reject the operation before publication. This is a file-management
operation, not a complete Linux root-filesystem backup/restore format.

Files are streamed with a fixed-size buffer, content is SHA-256 checked against
the new file, and owner, mode, exact extended attributes/ACLs, atime and mtime
are preserved. Directory metadata is applied after its children. Copying uses
a separate private wrapper owned by the recovery process, so restoring a
copied directory's original UID and permissions cannot expose the staging tree
before publication. A second source snapshot must match the first before an
atomic `renameat2(RENAME_NOREPLACE)` publishes the complete tree.

`files` and `symlinks` count directory entries, including hard-link aliases;
`directories` includes the copied root. `hardlinks_preserved` counts aliases
beyond the first copied inode. `bytes_copied` counts unique regular-file bytes.
`content_manifest_sha256` hashes the canonical JSON mapping of relative paths
to type, file content hash and literal symlink target; it does not claim to be
a metadata or disk-image hash. A successful response has `published: true`.

Failed copies remove only staging entries whose identities match entries
created by that copy. They never recursively delete unrecognized content.
Error details report `destination_published`; a final sync error can report true
because the new tree is already visible and is not rolled back. If staging was
created, `cleanup_complete` reports cleanup and sync status. When false,
`staging_name` identifies the wrapper that may need inspection, relative to the
destination's parent. Refresh the destination before retrying an interrupted or
failed copy. Power loss can leave private staging behind; no automatic garbage
collector deletes such directories on the next boot.

## Fixtures and verification

Fixture mode uses real regular files under declared test directories:

```json
{
  "schema": "a22x.filesystem.fixture.v1",
  "volumes": [
    {"id":"userdata", "path":"volumes/data", "fs_type":"ext4", "mounted":true, "read_only":false},
    {"id":"sdcard-p1", "path":"volumes/sd", "fs_type":"ext4", "mounted":true, "read_only":false}
  ]
}
```

Save this as `ROOT/filesystem-fixture.json`; both volume directories must be
beneath ROOT, without symlinks. Fixture text inputs must also be beneath ROOT.
Mount state is simulated in `.filesystem-state.json`; no mount syscall or block
device write is possible in fixture mode. The operation lock is ROOT's
`.operation.lock`.

Run the meaningful regression suite with:

```fish
python3 -m unittest discover -s tests -p test_filesystem.py -v
```

The implementation has 91 fixture/regression tests, including actual file
roundtrips, stale hashes, mount/root replacement before publication, traversal,
symlink/FIFO rejection, default-ACL preservation, RO flag ownership and lock
contention. v0.3 adds metadata-selection token checks, explicit symlink
resolution/escape rejection, tree content and hard-link preservation, bounded
copy admission, concurrent source/destination changes, private staging,
identity-bound cleanup, errors after publication and separation of adapter mode
from file permissions in both fixture/device JSON envelopes. This fixture environment
maps only UID/GID 0: changing to an unmapped owner is tested as a clean syscall
failure; assigning another valid Linux owner needs a device or wider user
namespace. Fixture results do not substitute for physical-device acceptance;
the parent integration task records live RO/RW and file tests separately.

Runtime imports: `argparse`, `contextlib`, `ctypes`, `errno`, `fcntl`, `hashlib`,
`json`, `os`, `pathlib`, `re`, `stat`, `struct`, `subprocess`, `sys`, and the adjacent
project module `operation_lock`. The only
external commands are BusyBox `mount` and `umount`.
