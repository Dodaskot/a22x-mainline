# ARM64 systems: offline installation, switching and rollback

`distro.py` adds a root-filesystem manager to the dedicated A22x RAM recovery.
The existing Samsung-compatible BOOT image, kernel #107, device tree, partition
table and userdata filesystem remain in place. Installation takes a prepared
ARM64 rootfs bundle from external SD storage, verifies it, and extracts it into
an inactive directory. It does not execute anything from the bundle.

An ordinary ARM64 ISO, container image, generic rootfs tarball, Android ZIP or
image for another board is **not** automatically a bootable A22x system. The
bundle must match the exact installed platform profile, contain a usable init,
its ELF interpreter and required libraries, the phone's kernel modules and
firmware, device services, and compatible user accounts. The manager validates
those files. Hardware operation of a newly prepared distribution still needs
its own boot acceptance.

## Data that stays and data that changes

The live `/home` directory stays at its original inode on userdata. The manager
does not move, replace, copy or delete its files during a switch. The target
must use the same names, UID numbers, primary GIDs and home paths for accounts
that own shared files or use `/home`. Named users/groups in POSIX ACLs also
participate in this check. Numeric owners absent from the old account database
or conflicting aliases cause an explicit refusal. The manager never performs
an automatic recursive `chown` to make a different distribution appear to fit.

Hardlinks wholly inside `/home` are retained. A hardlink from shared home to a
file elsewhere in userdata is refused: otherwise writing shared data from the
new system could also alter the saved old system. Symlinks remain symlinks;
the manager never follows a home link while enumerating user data.

Each system keeps its own `/etc`, `/var`, `/usr`, `/opt`, `/root`, package
database and other top-level system entries. Switching back restores those
entries from the previous slot, including any changes made while that slot
was active. `/home`, `lost+found`, the manager directory and incomplete setup
directories are shared filesystem-level objects. The userdata root inode and
its metadata also remain common.

Application versions may change shared home configuration when the new system
runs. Switching the OS back does not roll back those user-file changes.
Different account services, SELinux policy, encryption and application formats
may need deliberate migration. The first profile is for a prepared rootfs with
the retained device adapter; this is not an automatic conversion between
arbitrary distributions or init systems.

The Linux compact backup covers the **whole userdata filesystem**, including
shared home, every inactive system and manager state. No special exclusion is
added. A userdata wipe removes all of them; a complete Linux restore restores
all of them. A system slot is therefore useful for changing OS versions, but
is not a substitute for an external backup.

## On-disk layout and crash behaviour

All manager writes are files or same-filesystem renames inside admitted ext4
userdata. No raw-partition write, filesystem format, resizing or repartitioning
exists in this helper.

| Location in userdata | Purpose |
| --- | --- |
| `/.a22x-systems/state.json` | Sealed active-slot identity and generation |
| `/.a22x-systems/slots/ID/slot.json` | Slot identity, source checksum and validation result |
| `/.a22x-systems/slots/ID/root/` | Inactive system entries; only a home template/placeholder remains while active |
| `/.a22x-systems/staging/ID-RANDOM/` | An uncommitted import; existing Linux is still intact |
| `/.a22x-systems/transaction.json` | Durable pending switch journal |
| `/.a22x-systems/history/switch-ID.json` | Completed switch/rollback receipt |
| `/.a22x-systems-setup-RANDOM/` | An interrupted first-time setup; preserved and reported, never broadly removed |

There is no single atomic rename that replaces all top-level directories while
preserving a common home and an unchanged BOOT initramfs. A switch is therefore
**recoverable, not atomic as a whole**:

1. Record the entire operation, source/target identities, original/final state,
   home identity and slot-container identities. Fsync the record and directory.
2. Move the old `/sbin` entry into its inactive slot first. The retained BOOT
   initramfs explicitly invokes `/sbin/init`; it does not select another init
   from the partially moved userdata tree.
3. Move the remaining old system entries into the old slot, then move the new
   entries into the live root. Each rename uses `RENAME_NOREPLACE`, checks its
   exact object identity, fsyncs both parents, then checkpoints the journal.
4. Move the new `/sbin` entry last, commit the active-state record, archive the
   completed journal and clear the matching RAM boot guard.

A power loss between these operations may leave userdata unable to boot Linux
until the user returns to recovery and selects **Systems → Resume** or
**Systems → Rollback**. Recovery stays independent in RAM and does not need the
installed root to start. The journal accounts for a rename completed just
before its checkpoint and for interruption while rolling back. Choosing the
opposite repair direction reconciles the previous durable direction first.

If a planned pathname now contains an unexpected object, repair stops rather
than overwriting or deleting it. Unknown files are not guessed to be temporary
files. A completed journal archived just before loss of its RAM-guard cleanup
is reconciled only after exact state, home and final-location checks.

The RAM guard is `/run/a22x-recovery/distro-transaction.json`. Normal Linux boot
is blocked for a known pending transaction. `boot-check` additionally inspects
the durable journal on an explicit Normal Boot request, including after a cold
recovery start. It uses an existing userdata mount unchanged, or temporarily
mounts userdata read-only through the filesystem helper and normally unmounts
that temporary mount afterwards. Status and recovery startup do not mount
userdata automatically. Recovery/download/power-off remain available according
to the existing power helper's rules.

## Public CLI

Global adapter selection is exactly one of:

```text
distro.py --device OPERATION ...
distro.py --fixture /absolute/filesystem-fixture-root OPERATION ...
```

Fixture mode uses the existing `a22x.filesystem.fixture.v1` configuration and
regular directories. It cannot open a block device for writes. Every command
uses the shared operation lease. An active root terminal or USB file-export
session must be closed before storage work proceeds.

| Operation | Effect |
| --- | --- |
| `status` | Read current mount/slot/transaction state; no automatic mount |
| `boot-check` | Explicit normal-boot journal/init preflight; may make one temporary guarded RO mount |
| `check-platform` | Read-only verification of the current mounted root against the embedded platform profile |
| `capture-profile` | Read-only collection of explicitly selected platform paths; prints a candidate profile for review |
| `import-plan` | Verify a manifest and archive, bind source/target identities, estimate free space |
| `import --plan-file FILE --confirm` | Install an inactive root beside the current Linux |
| `switch-plan --slot ID` | Verify a target slot, shared-home identities and unchanged BOOT |
| `switch --plan-file FILE --confirm` | Apply the reviewed journaled system switch |
| `recover --direction resume\|rollback --expected-transaction ID --confirm` | Repair exactly the displayed pending transaction |

Only the explicit `boot-check` temporarily mounts a volume. Mount userdata RW
and the source SD RO or RW through Files before planning an import or switch.
Import requires external storage on a separate filesystem. Maximum slots: 16.

Example commands for the terminal, using fish syntax:

```fish
set -l systems /opt/a22x-recovery/distro.py
python3 $systems --device status

# The selected SD contains /Systems/debian/bundle.json and its rootfs tar.gz.
# Create a new private RAM directory; retain the exact generated name.
set -l plans (mktemp -d /run/a22x-systems-plans.XXXXXX)
python3 $systems --device import-plan \
    --source-volume sdcard-p1 \
    --manifest /Systems/debian/bundle.json \
    --output $plans/import.json

# Review the returned name, required/free bytes and preservation policy first.
python3 $systems --device import --plan-file $plans/import.json --confirm
python3 $systems --device status

python3 $systems --device switch-plan --slot debian-arm64 \
    --output $plans/switch.json
python3 $systems --device switch --plan-file $plans/switch.json --confirm
python3 $systems --device status
```

The UI passes `--expected-source-token` for the selected SD, and both
`--expected-plan-seal HASH` and `--expected-plan-file-sha256 HASH` when executing
an import or switch. CLI clients should also pass those paired plan guards
when confirmation occurs separately from planning. The helper pins and reads
one regular plan file, verifies its exact raw-byte SHA and displayed seal, then
recomputes the relevant source/target bindings. Plans expire after 15 minutes
and are tied to the current recovery boot. An interrupted transaction uses its
durable journal rather than an expired pre-execution plan.

Do not run normal Linux before a pending transaction is repaired. Do not
format userdata to resolve an interrupted switch: that would erase the shared
data and the saved systems the journal is intended to recover.

## JSON contract for the UI

Every success contains `ok: true`, the requested `operation`, and adapter
`mode: device|fixture`. Errors are:

```json
{"ok":false,"operation":"switch","error":{"code":"PLAN_STALE","message":"…","details":{}}}
```

`status` includes:

```json
{
  "schema":"a22x.distro.status.v1",
  "mounted":true,
  "read_only":false,
  "volume_token":"SHA256",
  "initialized":true,
  "active":"original",
  "slots":[
    {"id":"original","name":"Original Linux","active":true,"origin":"adopted-existing-root","hardware_boot_tested":false},
    {"id":"debian-arm64","name":"Prepared Debian ARM64","active":false,"origin":"prepared-arm64-rootfs","hardware_boot_tested":false}
  ],
  "transaction":null,
  "transaction_state":"none",
  "reboot_blocked":false,
  "shared_paths":["/home"],
  "retained_boot":true,
  "whole_switch_atomic":false,
  "platform_profile_available":true,
  "incomplete_imports":[],
  "incomplete_setups":[]
}
```

For unmounted userdata, `initialized` is null, the active slot is null, slots
are empty, and `transaction_state` is `unknown-unmounted`; this is not proof
that no durable transaction exists. The explicit Normal Boot preflight checks
that condition without imposing startup mounts.

A pending transaction has the following shape:

```json
{
  "id":"switch-0123456789abcdef01234567",
  "source":"original",
  "target":"debian-arm64",
  "step":7,
  "total":28,
  "direction":"resume",
  "actions":["resume","rollback"]
}
```

Refresh status before confirming repair and pass that exact ID. The UI uses a
2-second hold for import and a 3-second hold for switch/resume/rollback.

`import-plan` and `switch-plan` return a full sealed `plan` by default. With
`--output /private/RAM/path.json`, they create that **new** 0600 file exclusively
and return a compact summary for the display, together with:

```json
{
  "plan_summary":true,
  "plan_path":"/private/RAM/path.json",
  "plan_file_sha256":"SHA256-of-the-full-file",
  "plan":{
    "schema":"a22x.distro.plan.v1",
    "operation":"import",
    "mode":"device",
    "seal":"SHA256-of-the-sealed-plan",
    "slot":"debian-arm64",
    "name":"Prepared Debian ARM64",
    "created_utc":0,
    "expires_utc":900,
    "source_volume":"sdcard-p1",
    "manifest_path":"/Systems/debian/bundle.json",
    "required_bytes":0,
    "available_bytes":0,
    "confirmation":"Install alongside the current Linux; do not switch or erase /home"
  }
}
```

The actual timestamps and byte counts are populated from the checked plan.
The compact summary intentionally omits the binding and is not executable as
a full plan. A switch summary has `preserved_paths: ["/home"]`,
`whole_switch_atomic: false`, an interruption instruction, and top-level
`retained_boot: true`, `writes_partition_table: false`. Import has top-level
`switches_current_system: false`.

## Prepared bundle format

The selected JSON manifest and archive must be ordinary files beside each
other on the external source filesystem. The manifest is at most 4 MiB and
uses this format:

```json
{
  "schema":"a22x.distro.bundle.v1",
  "id":"debian-arm64",
  "name":"Prepared Debian ARM64 for A22x",
  "architecture":"aarch64",
  "boot_profile":"a22x-k107-ext4-v1",
  "boot_sha256":"b3f59daa37ad50e0ac6bfaed83f6954147536d07119f1b46a9981ee19d9b17ab",
  "platform_profile_sha256":"CANONICAL-SHA256-OF-EMBEDDED-PROFILE",
  "rootfs":{
    "file":"rootfs.tar.gz",
    "compression":"gzip",
    "sha256":"EXACT-ARCHIVE-SHA256"
  }
}
```

The architecture is verified from ELF files, not just this label. Supported
archives are tar and gzip-compressed tar. Maximum compressed input is 16 GiB,
expanded regular data 24 GiB, one regular file 8 GiB, and declared entries
200,000. Metadata has separate per-header, per-resolved-member, chained-header
and aggregate budgets, so a PAX header cannot bypass file-size limits or
consume unbounded memory without file data. A conservative space reservation
adds 8 KiB per entry and a 256 MiB free-space margin beside all retained slots.

The extractor never calls `tar.extractall`. It uses FD-relative creation with
no-follow checks in a new private staging tree. Duplicate paths, parent/absolute
archive paths, children beneath symlinks, cross-root links, special files,
runtime `/dev`/`/proc`/`/sys`/`/run` contents and management paths are rejected.
Absolute symlinks are allowed as **logical paths inside the new root**, never
followed while extracting. Hardlinks must resolve to a regular file in that
same archive. Numeric ownership, permissions, timestamps, user xattrs and file
capabilities are applied; unsupported ACL/sparse tar extensions are refused
rather than silently discarded. The archive SHA and source identity are
checked before and during installation.

The new root needs real directories for `/dev`, `/proc`, `/sys`, `/run`, `/tmp`,
`/home`, `/etc`, `/usr` and `/var`, plus a native ARM64 `/sbin/init` and `/bin/sh`.
Usr-merged layouts are supported. System startup paths may not resolve into the
inactive home template, the shared manager or volatile runtime directories.
Fstab paths are decoded and normalized; no mount may mask shared home or
admitted system files. Only explicit known runtime mounts and optional media
mounts under `/mnt` or `/media` are accepted. A private regular per-slot swap
file can be used; arbitrary swap partitions are not admitted by this profile.

The importer does not run a package manager, post-install script, kernel hook,
initramfs generator, SSH service or any executable from a bundle. Prepare and
audit the rootfs on the PC, install its required distro packages there, and
package its final files. Scripts in the selected device profile remain normal
OS services to be executed only when the user deliberately boots that system.

## Platform profile and actual A22x dependencies

The read-only profile is installed as
`/etc/a22x-recovery/distro-platform.json`. Its seal and canonical SHA bind:

- The exact retained 32 MiB BOOT hash and `a22x-k107-ext4-v1` identity.
- Selected kernel-module, firmware and device-adapter file bytes, paths,
  ownership, permissions and symlink targets.
- The init-system family and required runtime executable paths.
- Any explicitly declared Python standard-library modules used by the adapter.

Profile capture is read-only and explicit, for example:

```fish
python3 /opt/a22x-recovery/distro.py --device capture-profile \
    --module-tree /opt/a22x-conn/btif.ko \
    --module-tree /opt/a22x-conn/wmt_drv.ko \
    --module-tree /opt/a22x-conn/wmt_chrdev_wifi.ko \
    --module-tree /opt/a22x-conn/wlan_6833_axi.ko \
    --module-tree /opt/a22x-conn/btmtk_stp.ko \
    --firmware-tree /usr/lib/firmware/Himax_firmware_fw_jdi.bin \
    --adapter-path /etc/systemd/system/a22x-conn.service \
    --adapter-path /opt/a22x-conn/conn-start.sh \
    --require-command /usr/bin/python3 \
    --require-command /usr/bin/insmod \
    --require-command /usr/bin/systemctl \
    --python-module fcntl --python-module struct --python-module select \
    --python-module glob
```

That abbreviated example explains the interface; it is **not a complete phone
profile**. The final captured profile must include the files and enabled links
established by the platform audit. In particular, the current connectivity
service calls `conn-start.sh`, which loads five out-of-tree modules from
`/opt/a22x-conn`, calls `wmtload.py`, starts `a22x-wmt-launcher.service`, and reads
the retained Bluetooth address. `wmtlaunch.py` refers to the selected
`soc2_2_ram_{mcu,bt,wifi}_…_hdr.bin` firmware and `WMT_SOC.cfg`. A kernel config
with no in-tree `=m` entries does **not** prove the system has no external
modules. The current profile must carry the working external modules too.

Historical `a22x-*-fallback` timers are not ordinary hardware support: inspected
examples invoke old `boot-trial.py rollback` scripts that can replace BOOT.
They must not be enabled or copied as a new-distribution platform adapter.
One-off Phosh stress/test helpers are also distinct from required startup
services. Full existing `/etc` and `/var` remain in the original slot for return;
the new bundle should contain only the deliberately prepared services.

Matching platform file hashes and ELF dependency closure are necessary checks,
not a proof that a different distribution's networking, display manager, audio,
power policy or modem stack works. Device services may need porting to different
paths or a different init system. Update the audited profile and qualify that
adapter deliberately; the manager does not pretend that copying one systemd
unit makes an OpenRC or SELinux-based rootfs compatible.

The bounded ELF check maps direct `DT_NEEDED` dependencies before their children
and reuses already loaded request names and `DT_SONAME` aliases. This matters
for systemd: init can load its private core and shared libraries with its own
`RUNPATH`, after which core reuses the mapped shared object without another
path search. `RUNPATH` is not inherited by children; legacy `RPATH` follows the
first loader's ancestor chain and is suppressed by that object's `RUNPATH`.
Search entries retain their original order. These rules follow glibc's
[`_dl_map_object_deps`](https://codebrowser.dev/glibc/glibc/elf/dl-deps.c.html)
and [object lookup/path search](https://raw.githubusercontent.com/bminor/glibc/master/elf/dl-load.c).
The check does not execute a loader or imported code, resolve symbols, emulate
environment overrides/preloads, or add directories from `ld.so.conf`/cache.
Unsupported layouts still require an explicit adapter qualification; copying
all private directories into a global search list is not an admission rule.

## Validation scope

`tests/test_distro.py` uses owned regular-directory root filesystems with
synthetic ARM64 ELF headers. It exercises actual archive extraction, actual
metadata operations and renames, a shared user file and inode-preserving switch
and return, unknown-path refusal, foreign/expired/changed plan refusal, UID/GID
and ACL accounting, cross-home hardlink refusal, archive traversal/metadata
limits, fstab masking refusal and explicit normal-boot mount-state handling.

Every forward rename is interrupted before, immediately after, and after its
durable checkpoint, then independently resumed or rolled back. Additional
cases interrupt rollback, reverse its direction, and interrupt state/history
commit. These are fault-injection tests of the userspace protocol. They are
not a claim of physical power-cut acceptance, a booted Debian/Ubuntu/Alpine
system, or a destructive test of the phone's installed Linux.

The parent integration runs native ARM checks in disposable RAM or regular-file
fixtures. The phone's current root remains unchanged until the user explicitly
imports a compatible bundle and confirms a switch. Full compact Linux backup
qualification is independent and continues to cover all filesystem blocks.

## Reference background

- [Linux kernel: ramfs, rootfs and initramfs](https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html)
- [Debian: installing from an existing Unix/Linux system](https://www.debian.org/releases/oldstable/arm64/apds03.en.html)
- [Alpine: init-system overview](https://wiki.alpinelinux.org/wiki/Alpine_Linux%3AOverview)

Those explain general boot/rootfs preparation. The exact retained BOOT
`working-init.reference`, pinned image hash, hardware inventory and validation
receipts in this project's build artifacts define the A22x-specific contract.
