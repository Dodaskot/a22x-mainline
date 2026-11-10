# A22x Mainline Recovery — changelog before 0.6.124

A native Linux recovery for Samsung Galaxy A22 5G **SM-A226B / MT6833**.
The intended installation target is the separate **RECOVERY partition**,
41,943,040 bytes. It has its own initramfs and stays in RAM; installed Android
or Linux in `userdata` is not required to start the interface.

## 0.6: installing other operating systems

- **Operating systems** menu on the home screen: Linux distributions, Android and
  BOOT, and backup restore in one place.
- `tools/a22x-image-import.py` converts an ARM64 ISO, RAW/IMG disk image, qcow2 or
  rootfs archive into an offline bundle on a Linux PC, without root. See
  `docs/INSTALLING_SYSTEMS_UK.md`.
- Screen timeout: 30 s, 1, 2, 5 or 10 minutes, or Never, in **Tools → Display**.
  The choice lasts for the current recovery session.
- One icon grid for all vector icons.
- Accepts BOOT with kernel #107 or #108. Device firmware is never included:
  capture it from your own phone with `tools/a22x-capture-platform.sh`.

Only systemd distributions with a merged `/usr` are supported.

## 0.5: Linux maintenance, system switching and image installation

Source and installed-image acceptance are separate records. The v0.5 source
implements the features below; physical-device results belong in
`LATEST_V05_LIVE.json` and its referenced acceptance directory on the Arch host.
Do not infer the installed version from this README.

- Pixel scrolling and time-based inertia in the **volume list**, directory
  browser, text viewer and text editor. Touching during a fling stops it without
  activating the touched row. File kinds have distinct colors, icons and labels.
- Geometric vector icons rasterized with antialiasing at the native framebuffer
  resolution. Existing 60/90 Hz DRM controls remain available. Render timing is
  recorded separately: selecting 90 Hz does not establish 90 UI frames/second.
- A short side Power press blanks the backlight; the next press restores the
  previous positive brightness. Touches while blank, including a contact held
  through wake, do not activate controls. **Tools → Display** provides a 1–100%
  brightness slider alongside the 60/90 Hz choice. Blanking keeps recovery jobs
  running and does not suspend the system. A separate guard restores visible
  backlight if the display service exits unexpectedly. These preferences are
  retained in RAM for the current recovery boot.
- Compact Linux backups use Partclone's ext4 used-block format plus Zstandard.
  They contain BOOT and the allocated userdata filesystem blocks, including
  Unix metadata. Unallocated partition space is omitted. This is a Linux
  restoration set, not a clone of every firmware partition.
- Guided Linux **Backup → Verify → Wipe userdata → Restore**. Source filesystems
  must be clean and unmounted. Wipe and Restore require an embedded qualification
  capsule for the exact implementation, ARM tools, kernel and device geometry.
  Qualification restores an actual device backup into new regular image files
  on the PC; it does not claim that physical userdata was wiped and restored.
- Explicit USB MTP export of **one already-mounted userdata or SD volume**,
  selected as read-only or read/write. The service is off after boot. Its
  supervisor holds the storage lease until transfers stop, data is synchronized
  and the original NCM gadget is restored. Close PC transfers and select Stop
  before using other storage operations on the recovery screen.
- An explicit on-screen root terminal with a real PTY, EN/UK keyboard,
  control keys, a bounded output history and inertial scrolling. The terminal
  uses a separately pinned static `/bin/a22x-terminal-shell` with Unicode line
  editing; recovery's shared `/bin/busybox` and `/bin/sh` are unchanged. The shell
  is checked and executed through the same file descriptor before each Start.
  It runs in recovery's RAM root. Installed Linux files are under
  `/mnt/volumes/userdata` after mounting; SD files are under `/mnt/backup`.
  It is a basic VT100-style display, not a complete xterm implementation.
- **Linux Systems** imports prepared ARM64 rootfs bundles into separate system
  slots and switches between them with a durable journal. `/home` keeps its
  original inode and is shared; `/etc`, `/var`, installed packages and other
  system files stay with their own slot. The tested kernel #107 BOOT is retained.
- **Install images** provides a reviewed raw BOOT writer and a fixed stock
  Android bundle installer. It requires an explicitly selected verified Linux
  backup, saves the small previous boot platform, skips byte-identical images,
  verifies each changed target after writing, and writes stock RECOVERY last.
  It does not mount Android, back up Android, format userdata, write protected
  bootloader/modem partitions or reboot automatically.

The terminal retains the same storage lease as SSH and MTP until its shell and
owned descendants have stopped. Close the terminal before using Backup, Wipe,
Restore or USB sharing. Commands entered there run as the recovery administrator;
the command prompt is not a restricted recovery command interpreter. Shell
output stays in bounded RAM state and is not copied into diagnostic logs.

MTP exposes ordinary files and directories. Symlinks, device nodes and other
special entries are hidden. Upload defaults are root:root, mode 0644 for files
and 0755 for directories; normal setgid/default-ACL inheritance can affect the
result. Linux can also clear setuid/setgid or file capabilities after content
changes. A PC editor may replace a file instead of modifying the existing inode,
so use the root SFTP shell for deliberate metadata maintenance and the Linux
backup workflow for faithful preservation of ownership, ACLs, extended
attributes, links and special files.
The responder runs in a private mount/IPC/network namespace and a small immutable
RAM chroot, with only the selected export and FunctionFS endpoints visible.

`backend/LINUX_WORKFLOW.md` describes archive formats, source and target checks,
capacity checks, failure journals and qualification. An incomplete operation
never triggers a reboot. There is no automatic resume after power loss and no
secure-erase claim. Generic raw partition restore remains disabled; qualification
admits only the explicit Linux workflow and its BOOT/userdata targets.

### Preparing the user's full recovery exercise

Keep the external ext4 SD available and select the Linux compact backup profile.
Backup ordinarily unmounts userdata before reading it. A complete backup must
verify before Wipe can be offered. Wipe also verifies that the chosen backup
still represents the current BOOT and allocated userdata contents; normal Linux
boot activity can make an older backup stale for that check. Make a fresh backup
in the same recovery session when beginning the destructive exercise.

Wipe formats userdata only. Restore writes userdata first, verifies it, and
handles BOOT last; an already matching BOOT is left unchanged. The recovery
partition, SD archive, partition table and modem/bootloader partitions are not
targets. Keep the durable PC copy as a second copy of the recovery set.

### Linux Systems

Mount Linux userdata read/write and the external SD, then open **Tools → Linux
Systems**. Select the manifest of a prepared ARM64 bundle, review its import
plan, and import it as a separate slot. Switching is a separate confirmed
action. The current system is retained so it can be selected again later.

A generic ARM64 ISO or rootfs archive is not sufficient. The bundle must include
the admitted kernel modules, firmware and device services from the exact
`distro-platform.json` profile, compatible ARM64 executables and their loader
closure, and account IDs compatible with the shared home directory. The profile
includes five external MediaTek connectivity modules even though the kernel
configuration has no ordinary `=m` entries. New distribution graphics, audio,
networking and power management still need a real hardware boot qualification.

The current BOOT selects `/sbin/init` at userdata's root. Switching therefore
uses a journaled sequence of directory renames, and the whole sequence is not
atomic. After an interrupted transition, open Systems and explicitly choose
Resume or Rollback. Normal boot checks the durable journal; Recovery and
Download remain available. `DISTRO_API.md` describes the bundle format, shared
home ownership/ACL checks and the command-line wrapper `a22x-systems`.

### BOOT and stock Android installation

Open **Tools → Install images**, select BOOT or stock installation, choose the
source from the external ext4 SD and explicitly select the Linux snapshot.
Review the complete source, backup and write/skip target list. Confirmation
requires a continuous three-second hold for BOOT or five seconds for stock.
An exact BOOT header and verified bytes do not establish that a new kernel will
boot; its hardware acceptance remains a separate test.

Stock installation accepts the manifest of the exact qualified firmware bundle.
Its target set is SUPER, PRISM, OPTICS, DTBO, VBMETA_SYSTEM, VBMETA, BOOT and
RECOVERY. Protected bootloader/LK/TEE contents are checked read-only against the
signed firmware requirements. CACHE/OMR are excluded: the supplied CSC cache
contains queued stock-recovery commands and is not required by the verified-boot
graph. The current DTBO and VBMETA images already match this stock set, so the
planner can verify and skip them.

The original CSC image bytes are retained. Their first-sector difference from a
generic AVB hashtree check is corrected by the signed FEC parameters; independent
read-only verification and the stock kernel configuration establish that
mechanism. No prefix repair, signature replacement or AVB disabling is applied.

Installing stock replaces this custom recovery with stock RECOVERY at the last
write. It leaves Linux userdata untouched. The next deliberate step is to boot
stock Recovery and perform its factory reset, which erases Linux userdata and
prepares Android data. Returning to Linux requires entering custom recovery
again and restoring the saved Linux BOOT/userdata set. The exact retained
DTBO/VBMETA platform is unchanged for this qualified stock profile.

The installer journal is on the external SD, with a RAM boot guard for the
current session. Interrupted writes require an explicitly reviewed Resume plan;
there is no automatic retry. `INSTALL_API.md` documents the transaction and
missing-card boot-check policy. `a22x-install` is the corresponding CLI wrapper.
Physical stock installation and a subsequent Android hardware boot are separate
from parser, regular-file write tests and read-only/no-op device admission.

## Previous milestone: 0.4 browser and display controls

The 0.4 source adds direct finger scrolling to the Files list. The viewport moves
by pixels, clips partially visible rows, cancels a pending file action on a drag,
and loads overlapping bounded windows of directory entries. Folders, files,
executables and symbolic links have distinct high-contrast colors, icons and type
labels. Executable classification uses the actual permission bits of regular
files, not a filename guess.

Display settings expose the kernel-advertised 60 and 90 Hz panel modes. A small
RAM service keeps a DRM descriptor open, switches the existing framebuffer and
CRTC to the selected enumerated mode, and verifies the active timings afterward.
The UI reads the service status again before presenting a change as applied.
`actual_hz` in the API is calculated from the current DRM timings; it is not a UI
frame-rate counter or a vblank measurement. The mode choice lasts for the current
recovery boot; the preferred kernel mode is 60 Hz on a fresh boot.

The exact kernel #107 already provides both panel modes; no kernel or panel
firmware change is included in this revision. Build, RAM testing, flash readback
and cold-boot acceptance are separate receipts. Consult `LATEST_LIVE.json` for
the version actually installed on the phone.

## 0.3 Linux maintenance

The 0.3 source adds four parallel feature groups to the working recovery:

- Key-only USB SSH and SFTP on `172.16.42.1:22`, authorized for the existing
  Arch workstation ED25519 public key. No workstation private key is copied.
  The server generates its own host key in RAM each boot; verify the current
  fingerprint shown in Diagnostics before trusting a changed key.
- Diagnostics status, bounded export to an explicitly writable selected volume,
  and four retained UI log generations within a boot. Exports contain an
  allowlist of recovery logs and build metadata, with a SHA-256 manifest.
- Entry metadata, `chmod`, `chown`, explicit volume-confined symlink resolution,
  and bounded recursive copy to a new destination. Existing data is not replaced
  by recursive copy; recursive deletion is deliberately absent.
- All four explicit power targets: Linux, Recovery, Download and Power off.
  Routine backup choices are Linux and Recovery; historical Android archives
  remain readable by the existing backup format reader.

An SSH/SFTP session holds the same cooperative operation lease as the UI and
backup engine. Close the session before starting an operation on the screen.
Python helpers and `recovery-power` called *inside* that shell inherit the lease
so they can run without deadlocking themselves. Authorized SSH is a root repair
shell; this coordination does not restrict a trusted root operator from making
manual system changes.

The ramdisk also includes OpenSSH 10.6p2 and e2fsprogs 1.47.4 from signed
Alpine aarch64 packages. `e2fsck`, `resize2fs`, `tune2fs`, `dumpe2fs`, `e2image`
and related tools are available from the terminal. Their presence does not
mean a screen-based guided repair/resize workflow is implemented or that an
actual repair of the installed Linux filesystem has been performed.

The Restore engine now uses full preverification followed by streaming writes
whose individual 1 MiB buffers are checked against an immutable SHA-256 index
before writing. It needs no partition-sized decoded staging file. It preserves
target bounds, boot-last ordering, independent readback, and failure receipts.
In that revision physical partition Restore remained blocked pending device
qualification; the runnable fixture exercise accepted regular files only. The
v0.5 qualification gate above now governs the separate Linux workflow. A
successful regular-file test must never be described as a successful restoration
of real userdata.

Build validation and installed-device acceptance are separate receipts under
the Arch project artifact directory. Source changes alone do not establish
which version is installed; use `LATEST_LIVE.json` and its referenced receipt.

## Previous milestone: 0.2 filesystem access

The 0.1 image was installed and read back byte-for-byte on 2026-11-05,
then booted through `systemctl --reboot-argument=recovery reboot`.
The boot tag confirmed mode 2, RAM root, 1080x2408 framebuffer and Himax
input. The user confirmed that touch works.

The frozen 0.2 filesystem module was then loaded into the running recovery's
RAM and passed 31 physical-device CLI operations: block-guarded RO mounts,
RW creation/editing, stale-hash refusal, Unicode names/content, copy/rename,
persistent readback after unmount/remount, and deletion of only its own test
files/directory. Linux userdata and microSD ended unmounted with their original
block-RO flags restored. The kernel explicitly reported that the RO block guard
prevented ext4 orphan cleanup. Evidence is in the Arch artifact directory under
`control/live-roundtrip-01/full-result.json`. This establishes the filesystem
module, not the new UI's physical touch acceptance or Android decryption.

Version 0.2 adds `filesystem.py` and a native Files interface: explicit RO/RW
mounts, folder browsing, bounded UTF-8 text viewing/editing, creating files
and folders, copying regular files, renaming, and deleting files or empty
directories. Text replacement uses an expected content hash and atomic
publication. Volume/device/mount identity, symlinks, special files, ownership,
mode and extended attributes are checked by the filesystem module.

Supported filesystem detection is ext4, F2FS, VFAT, exFAT and EROFS, subject
to the actual kernel driver. The file manager uses bounded superblock probes;
v0.5 additionally packages a pinned `blkid` for the qualified Linux workflow.
Linux userdata and removable storage can support file writes.
EROFS and verified Android system filesystems remain read-only. Unknown or
encrypted Android userdata is not presented as decrypted. BOOT/RECOVERY/DTBO
are image formats, while SUPER needs a separate logical-partition mapper.
Opaque firmware and secure-state partitions are not treated as directories.

Managed filesystem mounts use `/mnt/volumes/<id>`. The existing microSD
mount at `/mnt/backup` is recognized without a second mount. All filesystem,
storage and backup operations share the same operation lock. Generic raw
partition restore is still disabled; file edits do not enable it.

The supervisor unmounts all managed volumes before power actions. The new
native `recovery-power` helper uses an explicit `normal` restart target,
holds the operation lock and rejects remaining block-backed mounts.

### Android crypto remains incomplete

The exact kernel #107 has `CONFIG_FS_ENCRYPTION=n`; its source also lacks
Android dm-default-key and Samsung TEEGRIS/tzdev. OP-TEE is not TEEGRIS.
Matching Keymaster/Gatekeeper userspace and the secure boot contract are
additional requirements. See `docs/ANDROID_CRYPTO_STATUS.md` for evidence.
`kernel/fscrypt-prerequisites.config` passed an isolated Kconfig check; it
is not applied to this image and is not a completed decrypt implementation.
The build admits `kernel/k107-capabilities.json` only for its pinned kernel
hash; USB diagnostics expose capability observations without reading keys.

### Filesystem terminal API

From the recovery UART console:

```sh
python3 /opt/a22x-recovery/filesystem.py --device inventory
python3 /opt/a22x-recovery/filesystem.py --device mount --volume userdata --mode ro
python3 /opt/a22x-recovery/filesystem.py --device list --volume userdata --path /
python3 /opt/a22x-recovery/filesystem.py --device read --volume userdata --path /usr/lib/os-release
python3 /opt/a22x-recovery/filesystem.py --device unmount --volume userdata
```

Writable mounting requires `--mode rw --confirm`. See `FILESYSTEM_API.md`
for file mutation and fixture APIs. On the Arch UART bridge, verify termios
after connecting: a confirmed failure kept 9600 baud until 921600 was set
explicitly. This does not require changing the phone's bootloader.

## Original 0.1 architecture and baseline tests

The working kernel **7.2.0-a22x+ #107** is reused byte for byte, including its
Samsung boot shim and DT table. A new RAM userspace provides:

- A native framebuffer/evdev touchscreen interface with Backup, Restore,
  Verify, Storage, System and Power screens.
- Python backup engine with streamed gzip compression, optional zstd when
  available, raw/archive SHA-256, incomplete-operation receipts and atomic
  publication of completed backups.
- Explicit external **ext4 microSD** mounting at `/mnt/backup`. No formatting.
- Profiles: Linux (`boot` + `userdata`), Android state (`boot` + `metadata` +
  `userdata`), and recovery image (`recovery`).
- Android retained-firmware verification for `super`, `tee1`, `tzar`, `dtbo`
  and `vbmeta`. These partitions are read, never restored by these profiles.
- A read-only USB NCM diagnostic endpoint and the physical UART console.

No FBE decrypt, Android userspace, Keymaster, Gatekeeper or TEE bootstrap is
part of this recovery. Android state is copied as opaque block data. Existing
Android data can still depend on the original device's secure state when
Android itself later tries to decrypt it.

### Historical 0.1/0.2 acceptance table

This table records the original bring-up scope. It is not the current v0.5
acceptance result; newer feature and installed-image records are described above.

| Feature | Status |
| --- | --- |
| Native host UI and backend integration | Tested on real regular-file fixtures |
| Linux and Android fixture backup/verify/restore | Byte-for-byte round trips tested |
| Corrupt/truncated archive rejection | Tested before any fixture restore write |
| ARM64 runtime and native UI | Cross-build / QEMU checks are in the build receipts |
| Stock RECOVERY geometry and AVB hash footer | Checked by the image builder |
| Boot in actual RECOVERY mode2 | 0.1 hardware PASS on 2026-11-05 |
| Display and touch in 0.1 | Runtime detected; user confirmed touch |
| 0.2 filesystem module on physical Linux userdata | 31-operation RO/RW/file round trip PASS; own test files removed |
| 0.2 microSD guarded RO mount/list/unmount | Physical device PASS |
| 0.2 new UI touch and packaged image | Requires the separate 0.2 image acceptance receipt |
| microSD backup/restore on the device | Requires separate acceptance |
| Physical-device backup implementation | Implemented; not yet qualified live |
| Physical-device restore | **Disabled in code, `DEVICE_RESTORE_NOT_QUALIFIED`** |
| Import of historical TWRP / Nov3 backup formats | Not implemented in version 0.1 |
| USB-OTG storage / FAT32 split archives | Not implemented in version 0.1 |

The original bring-up image intentionally refused all physical Restore writes.
The current source keeps that generic path disabled and has no bypass switch;
only the separately qualified Linux workflow can admit its specific writes.

## Storage model

Backups belong on external media. A backup inside the very same `userdata`
that will be restored would be lost during restoration. Version 0.1 supports
the existing ext4 microSD; the mount helper verifies that `mmcblk1` is an SD
card and that its first partition is ext4 before an explicit mount action.

The Linux and Android profiles are **not full-device clones**. Android state
restoration also depends on the retained firmware identity. Physical restore
must keep the independent recovery available, verify all inputs before the
first write, write `boot` last and report a partial failure without rebooting.
Regular-file tests implement and exercise that ordering; they do not prove
53 GB production restore behaviour or power-failure recovery.

The v0.5 host qualifier additionally restores the actual full-size userdata and
BOOT backup into disposable regular files and checks the result. Physical
storage failures and power-loss recovery remain outside that test's scope.

Compressed size is not known from the raw partition size. Backup checks free
space during streaming and leaves an explicit incomplete result if space runs
out. It does not require 53 GB of free space for a compressed 53 GB source.

## Build from the verified local inputs

On the Arch control host, the staged inputs are:

```text
$A22X_WORK/artifacts/mainline-recovery-k107-20261105/
```

The build is entirely offline. It compiles a static AArch64 UI, copies the
verified ARM Python runtime, creates a new deterministic CPIO.gz, uses the
stock v2/page2048/load addresses, adds a `recovery` AVB hash footer and then
unpacks the resulting image to compare every component.

Fish-compatible invocation, using the independently recorded platform profiles
and a new output directory:

```fish
set -l a22x_base $A22X_WORK/artifacts/mainline-recovery-k107-20261105
set -l a22x_distro (python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["ok"]; print(r["profile"]); print(r["file_sha256"])' $a22x_base/LATEST_DISTRO_PROFILE_V05.json)
set -l a22x_stock (python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["ok"]; print(r["profile"]); print(r["file_sha256"])' $a22x_base/LATEST_STOCK_PROFILE_V05.json)
python3 tools/build_recovery.py \
  --inputs $a22x_base \
  --source (pwd) \
  --output $a22x_base/build-local \
  --distro-profile $a22x_distro[1] --distro-profile-sha256 $a22x_distro[2] \
  --stock-profile $a22x_stock[1] --stock-profile-sha256 $a22x_stock[2]
```

That invocation deliberately builds without physical Linux Wipe/Restore
admission. For the qualified image, pass both the capsule path and its recorded
SHA-256 from the completed `LATEST_QUALIFICATION_V05.json` receipt:

```fish
set -l a22x_qualification (python3 -c 'import json,sys; r=json.load(open(sys.argv[1]))["result"]; assert r["ok"]; print(r["capsule"]); print(r["capsule_sha256"])' $a22x_base/LATEST_QUALIFICATION_V05.json)
python3 tools/build_recovery.py \
  --inputs $a22x_base \
  --source (pwd) \
  --output $a22x_base/build-local-qualified \
  --distro-profile $a22x_distro[1] --distro-profile-sha256 $a22x_distro[2] \
  --stock-profile $a22x_stock[1] --stock-profile-sha256 $a22x_stock[2] \
  --qualification $a22x_qualification[1] \
  --qualification-sha256 $a22x_qualification[2]
```

The builder checks the complete capsule seal, every admitted backend source,
the exact ARM toolchain and the separately tested UI binary. It also admits the
pinned static MTP package and installs its upstream license. The package's full
upstream source, applied patches and build evidence remain alongside the image
inputs and are included in the source deliverable.

The exact kernel input is about 20.28 MB; the maximum compressed initramfs is
21,272,576 bytes with the retained DT table, recovery DTBO and AVB allocation.
The builder refuses an over-size image and never opens a phone or block device.

## Tests and interface previews

See `backend/README.md` for fixture creation and CLI examples. Backend tests
run against regular files in temporary directories:

```fish
python3 -m unittest discover -s backend/tests -v
make -C ui test
make -C ui integration
```

`ui/test-results.json` records the tests that were run.
`ui/previews/` contains rendered screenshots of the real native drawing code,
not photographs or screenshots of a phone running the new recovery.

## Runtime diagnostics

The image remains in RAM. It never executes the normal BOOT image's
`switch_root` branch and never automatically mounts internal filesystems.

- UI: `/bin/recovery-ui`
- Backend: `/opt/a22x-recovery/backend/recoveryctl.py`
- Runtime log: `/run/a22x-recovery/ui.log`
- Read-only status over USB: `http://172.16.42.1:8081/status`
- Physical UART: 921600 baud, existing 1.8 V connection

The USB endpoint has no write, shell, flash or reboot API. Power actions in
the local UI require a hold, an idle backend and successful normal unmount of
the external volume. Their final behaviour still depends on kernel #107.

## Before the first device test

Use the completed `image-validation.json` and the exact stock recovery
rollback image recorded there. Initial hardware acceptance is: actual mode2
boot, RAM root, native display, touch, USB diagnostics and no internal mounts.
Then a small recovery-partition backup can qualify the native read path.
Production restore needs its own checked implementation and acceptance tests.

The user's current instruction is recovery-only Heimdall writes with
`--recovery` and normal automatic reboot, without `--no-reboot`. This
repository has no automatic flashing entry point. Preserve the old image,
compare stock geometry, hold the shared phone transition lock and verify
the full installed recovery hash after normal Linux returns.
The current stock Android snapshot may still restore stock recovery on its
next normal boot. Persistence of this custom recovery across that transition
has not been proved and must be resolved before claiming seamless switching.

## Source and licenses

This is an independent implementation inspired by recovery workflows.
OrangeFox/TWRP source, branding and artwork were not copied. New integration
and UI code is MIT; the font notices are in `ui/LICENSE.fonts`.

The assembled image also contains separately licensed components: the user's
GPL Linux kernel and shim, BusyBox, GNU libc linked into the UI, Python,
musl and the runtime libraries, plus the existing Himax firmware. Input
manifests preserve exact provenance. The MIT notice does not relicense those
components. A public binary release must carry the corresponding source and
notices required by those component licenses.

Primary technical references:

- https://wiki.orangefox.tech/root
- https://wiki.orangefox.tech/dev/building
- https://docs.kernel.org/fb/api.html
- https://cdn.kernel.org/doc/html/latest/input/multi-touch-protocol.html
