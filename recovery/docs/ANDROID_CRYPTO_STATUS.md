# Android crypto and filesystem status — A22x recovery

Date: 2026-11-05. Target: Samsung SM-A226B / a22x / MT6833.

## Result

The current mainline recovery can gain useful Linux filesystem mount and file
management functions without changing LK. Android decryption is a separate,
unfinished port. The reused kernel **#107 does not implement the required
combination of fscrypt, Android metadata encryption, and Samsung TEEGRIS
transport**. Loading TZAR would not supply those missing components.

The current physical `userdata` partition is a Linux ext4 filesystem, as
reported by the main runtime investigation. It must not be treated as the old
Android encrypted userdata image. A successful mount or edit of this filesystem
is a Linux filesystem result, not an Android decrypt result.

This audit read host files and kernel sources. It did not open a TEE device,
issue an SMC, access phone UART, flash a partition, attempt decryption, or read
key-blob contents. The new configuration fragment was checked with an isolated
`olddefconfig`; it has not been built into or deployed as a new phone kernel.

## Exact kernel identity and capabilities

The machine-readable record is `kernel/k107-capabilities.json`. Its claims apply
only to this kernel blob:

| Item | Evidence |
| --- | --- |
| Kernel release | `7.2.0-a22x+` |
| Build | `#107 SMP PREEMPT Wed Nov 04 20:39:09 UTC 2026` |
| Kernel SHA-256 | `ee0140c23f808ca482f846b0b97281ee938d6f263b81872977470cb629fed34c` |
| Extracted config SHA-256 | `aca24ae1a7cd143127968006a6d9329e0eb7458a49016fe096bcb97327a52364` |
| Kernel input, Arch host | `<workspace>/artifacts/mainline-recovery-k107-20261105/inputs/working-boot/kernel` |
| Source tree, Arch host | `<workspace>/worktrees/poweroff-clr-k107-20261104` |
| Extraction | The source tree's `scripts/extract-ikconfig` run on the kernel input above |

The source path identifies the inspected worktree. The kernel SHA-256 is a blob
hash, not a Git commit. No generic defconfig was substituted for the embedded
configuration.

| Component | Kernel #107 | Consequence |
| --- | --- | --- |
| ext4, F2FS, EROFS | Built in | Recognized, supported filesystems can be considered for mounts. EROFS remains read-only. |
| `CONFIG_FS_ENCRYPTION` | `n` | No kernel fscrypt key installation or encrypted-file access. |
| `CONFIG_FS_ENCRYPTION_INLINE_CRYPT` | Absent from extracted config | Its parent feature is disabled; this does not mean the source lacks the symbol. |
| `CONFIG_BLK_INLINE_ENCRYPTION` | `y` | Block-layer infrastructure alone does not provide filesystem decryption. |
| `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK` | `n` | No configured software fallback for that block-layer path. |
| `CONFIG_DM_CRYPT` | `y` | This is not a substitute for `dm-default-key`. |
| `CONFIG_DM_DEFAULT_KEY` | No symbol or implementation in inspected source | Android metadata-encryption support requires additional source work. |
| `CONFIG_DM_SNAPSHOT` | `n` | A device-mapper COW test arrangement needs a later kernel or another verified isolation method. |
| `CONFIG_F2FS_FS_COMPRESSION` | `n` | Do not promise access to compressed F2FS files. |
| `CONFIG_F2FS_FS_SECURITY` | `n` | Android security xattr handling also needs attention before promising faithful file modifications. |
| Generic `CONFIG_TEE`, `CONFIG_OPTEE` | `y` | These do not implement Samsung's TEEGRIS ABI. |
| `CONFIG_TZDEV`, `CONFIG_TEEGRIS_TEE_SUPPORT` | No symbols in inspected source | No built-in Samsung `tzdev` implementation in this tree. |
| Binder IPC | `y`, configured device list `"binder"` | This does not establish the required Android HIDL service environment. |
| Binderfs | `n` | Additional Binder device setup cannot be assumed. |

`drivers/md/dm-default-key.c` and `drivers/misc/tzdev` are absent from the
mainline source. `drivers/tee/optee` exists. In the inspected source DT include
`arch/arm64/boot/dts/mediatek/mt6833-samsung-a22x-lk-compat.dtsi`, the
`compatible = "samsung,teegris"` node is disabled. That is a source observation,
not a measurement of the live device tree or of an external module.

The manifest distinguishes `n` from `absent`. Unknown symbols are not silently
interpreted as enabled. A matching release string alone is insufficient to bind
this manifest to a different kernel image.

## What the historical TEEGRIS tests establish

The verified historical sequence has several distinct layers:

1. The preloader authenticates the `tee1` ATF/TEE image and reserves a TEEGRIS
   region at `0x76a00000`, size `0x4000000`.
2. Stock LK's NORMAL mode (`0`) loads TZAR. Its true RECOVERY mode (`2`) skips
   the corresponding load.
3. The vendor kernel and Samsung transport expose the interfaces required by
   the TEEGRIS userspace services.
4. Android services obtain the appropriate volume and user keys and install
   them in the metadata-encryption and fscrypt layers.

Skipping TZAR is not equivalent to having no secure firmware or no ATF at all.
For example, normal kernel execution and PSCI behavior do not establish that
TEEGRIS `root_task` is running. Conversely, a TZAR load log does not establish
that a particular Android credential-encrypted directory is readable.

### Local evidence

| Evidence on Arch host | What it supports |
| --- | --- |
| `<workspace>/lk-research/work/brom-twrp-continuation-20261105/REPORT.uk.txt` and `RESULT.json` | Current audit of the early-boot sequence, historical modified-LK authentication rejection, and unresolved late-load contract. Its recovery-image inventory predates the later mainline-recovery flash. |
| `<workspace>/logs/normal_vs_recovery20260617-193601.log` | Paired UART: NORMAL around line 3222, TZAR load around 4791–4807; RECOVERY around 8195, skipped TZAR around 9048, kernel entry around 9537. The file contains NULs, so text searches require binary-safe handling. |
| `<workspace>/audits/full-tree-true-recovery-test-20260615-215009/dmesg.txt` and adjacent recovery log | True-recovery `root_task` connection failure (`-111`) and unsuccessful metadata decrypt in that historical test. |
| `<workspace>/logs/v7-runtime-manual-recovery-evidence-20260619-220454/recovery.log` | Historical NORMAL-to-TWRP path: trusted-environment Keymaster, metadata key unwrap, mapped data volume, and user 0 CE decrypt using the default credential. |
| `<workspace>/docs/mainline-android-reference-20261007.md` | Cross-references to the boot-mode and Android-service evidence. |
| `<workspace>/lk-research/work/brom-twrp-continuation-20261105/twrp/DIAGNOSTICS_DESIGN.uk.md` | Prior diagnostic design and the separation of boot state from decrypt proof. |

The successful historical TWRP path establishes that decryption was possible
with a compatible Android/vendor environment and the right boot sequence. It
does not prove PIN/password support, decryption in true RECOVERY mode, or
decryption under mainline #107.

### Why changing one LK condition is not a complete solution

A small offline LK branch candidate was previously identified, but a modified
LK image written in the historical BROM experiment was rejected with
`SBC img auth fail (0x101004)` before its code executed. The branch's apparent
simplicity does not remove authentication or supply a tested execution path.
The stock LK reference SHA-256 is
`ffb85c1fb0fb97dd645f0ef30bb4accc9ef9247c653dec1dc6b8762cb97665e2`.

Late loading from mainline also lacks a verified interface. The historical
staging range `0xffa60000..0xffe5ffff` overlaps the current System RAM map, and
the required ownership, cache maintenance, register arguments, and secure-world
handoff have not been established. Writing TZAR there or making a guessed SMC
would not be a diagnostic capability check.

Replacing or modifying TF-A would be a separate secure boot project: it needs
an authenticated execution path, the vendor handoff ABI, correct reserved
memory, and proven return/interrupt behavior. No source compatibility result or
time estimate for that project has been established. The practical first
offline investigation is to derive and review a boot handoff specification from
the paired logs and existing binary analysis, including mode, image extent,
destination, authentication boundary, and consumer ABI. A bounded log parser
can report `observed`, `absent`, or `unknown` for those facts; it must not turn
that classification into a launcher or a phone operation.

## Concrete vendor reference for a future port

The read-only reference inspected on the Fedora build host is:

`<workspace>/android/twrp-12.1-a22x-clean-reconstructed`

Relevant files and directories relative to that root:

| Area | Paths |
| --- | --- |
| Samsung transport | `kernel/samsung/a22x/drivers/misc/tzdev/Kconfig`, `drivers/misc/tzdev/Makefile`, and `drivers/misc/tzdev/4.2.0/` beneath that kernel root |
| Core transport implementation | `drivers/misc/tzdev/4.2.0/core/tzdev.c`, `iwsock.c`, `uiwsock.c`, `iwio.c`, `mem.c`, `umem.c`, `platform.c`, `sysdep.c`, `subsystem.c`, `deploy_tzar.c` |
| Android metadata mapping | `kernel/samsung/a22x/drivers/md/dm-default-key.c` |
| Service order | `device/samsung/a22x/keystore2.recovery.rc` |
| TEE preparation | `device/samsung/a22x/a22x-teegris-init.sh` |
| Recovery gating | `device/samsung/a22x/a22x-recovery-start-gate.sh` and `init.recovery.mt6833.rc` |
| Historical filesystem recipe | `device/samsung/a22x/additional.fstab` and `fstab-profiles/direct/` |

The vendor configuration selects TEEGRIS `4.2.0`, Samsung `TZDEV`, ARM calling
convention, event IRQ 334, and panic IRQ 335. **`CONFIG_TZDEV_DEPLOY_TZAR` is not
enabled in that reference configuration.** The existence of `deploy_tzar.c`
therefore does not prove that late deployment is a working feature on this
device. The reference kernel is a Samsung 4.14-era tree; carrying its transport
to mainline 7.2 requires a real port. No compile result for such a port exists in
this audit.

The historical service chain is approximately:

`TEE preparation → tzdaemon / tzts_daemon → Keymaster → Gatekeeper / keystore2`

Actual readiness is also conditioned on Android property and service managers.
The referenced executables include `/vendor/bin/tzdaemon`,
`/vendor/bin/tzts_daemon`,
`/vendor/bin/hw/android.hardware.keymaster@4.0-service`,
`/vendor/bin/hw/android.hardware.gatekeeper@1.0-service`, and
`/system/bin/keystore2`. Matching Bionic/linker libraries, Binder/HIDL services,
Android init properties, and recovery/vold fscrypt integration are part of the
dependency closure. Copying these binaries into a minimal Linux ramdisk does
not establish a functioning Android runtime.

The old preparation script is not a read-only probe. It mounts EFS read-write,
creates or changes ownership of TEE/keymaster directories, and changes device
permissions. Its preparation begins at `late-init`, before the later recovery
start gate. A late `nodecrypt` toggle cannot be assumed to prevent those earlier
effects. A readiness property set by that script proves only that its
preparation stage ran. Future service experiments must also account for secure
state in RPMB; a read-only EFS mount alone does not prove that secure storage
will remain unchanged.

## Filesystem capability boundaries

The file manager should describe what was actually identified and mounted.
These classifications are not interchangeable:

| Identified object | Safe interpretation | Write policy |
| --- | --- | --- |
| Plain Linux ext4, including current physical userdata | A normal Linux filesystem if identity, feature support, and mount state agree | Explicit read-write mounting and bounded file operations can be implemented. |
| ext4/F2FS with an encryption feature | The volume can contain encrypted directories; the feature bit alone does not identify which paths are locked | Preserve locked/unknown status per path. Do not claim CE decrypt because the root directory lists successfully. |
| Unknown userdata signature | Could be metadata-encrypted, damaged, unsupported, or not a filesystem | No format, repair, forced mount, or automatic read-write fallback. |
| Android `metadata` ext4 | Readable metadata filesystem does not imply that protected volume keys can be unwrapped | Do not expose key-blob contents in browser listings, previews, diagnostics, or logs. |
| `super` | Android logical-partition container with metadata and extents | No direct filesystem mount. Logical mappings require validated bounds and their own mount policy. |
| EROFS logical/system filesystem | Inherently read-only filesystem | No generic read-write mount or edit promise. |
| AVB-covered system/vendor ext4 | Filesystem may be technically writable, but edits can invalidate the authenticated boot relationship | Keep outside automatic general-purpose read-write mounting. |
| Boot, recovery, LK, preloader, `tee1`, TZAR, vbmeta, modem/NV and other raw partitions | Firmware, raw data, or a specialized format | Inventory and separately designed backup operations; not ordinary file-browser write targets. |

A blanket promise that every partition will mount read-write is technically
incorrect. The useful immediate feature is verified filesystem access with
accurate limitations and separate handling of raw images and Android containers.

### Bounded signature offsets verified against #107 headers

These are parsing facts, not permission to mount or modify a block device.
Read integers with the declared little-endian format and validate the complete
bounded header before trusting any field.

| Format | Superblock start | Struct size | UUID offset within superblock | Other verified offsets |
| --- | --- | --- | --- | --- |
| F2FS | 1024 bytes | 3072 bytes | 108 | `block_count=36`, `feature=2180`, `encryption_level=2184` |
| EROFS | 1024 bytes | 144 bytes | 48 | `feature_compat=8`, `volume_name=64`, `feature_incompat=80` |

F2FS's encryption feature is `0x00000001`; its compression feature is
`0x00002000`. These values and offsets were verified using the actual
`include/linux/f2fs_fs.h` and `fs/erofs/erofs_fs.h` definitions from the #107
source, with a temporary host-only structure-layout check. No device data was
read for that check.

## Fail-closed runtime diagnostics contract

Diagnostics must report independent observations and leave unknowns explicit.
They must not attempt to make a missing capability appear available.

1. **Bind build evidence.** Include the pinned manifest only when the recovery
   builder's kernel blob hash matches it. Record runtime boot ID and release;
   compare `/proc/config.gz` if available. A manifest plus a release string is
   build provenance, not secure attestation of a running image. Report a
   mismatch or unverifiable identity as such.
2. **Identify volumes before use.** Resolve known partition labels through
   sysfs/partition inventory; record major:minor, size, and observed UUID or
   signature. Compare `/proc/self/mountinfo` and current device-mapper identity.
   Do not assume a name such as `userdata` implies Android or a particular
   filesystem.
3. **Probe bounded metadata only.** Read only the minimum header ranges needed
   for format classification. Unknown magic must remain unknown. Avoid fsck
   repair, `check`/`formattable` fstab behavior, discard, wipe, key-file reads,
   and opportunistic mount attempts.
4. **Observe transport without calling it.** Check whether `/dev/tzdev` and
   related nodes exist and whether corresponding drivers/modules appear in
   sysfs. Do not open them, issue ioctls, invoke SMCs, start the old preparation
   services, or infer working TEEGRIS from `CONFIG_TEE`, `CONFIG_OPTEE`, a node,
   or a property string.
5. **Distinguish mapping targets.** If available, a read-only target inventory
   such as `dmsetup targets` can establish registered target names. Never use
   key-revealing table dumps. `crypt` is not `default-key`; unknown target
   support is not a reason to create a fallback mapping.
6. **Keep Android decrypt disabled until proven.** With #107,
   `android_decrypt.supported` is false. Report the independent blockers from
   the manifest and observed runtime. No automatic retry may silently create
   or modify keys, remount EFS, write secure storage, restore a backup, or
   treat encrypted data as a blank filesystem.
7. **Scope future success to a volume and user.** A future PASS needs both the
   required metadata-encryption layer and appropriate per-user fscrypt key
   installation, followed by reading a known non-sensitive control file in
   that user's CE directory. Bind the result to boot ID, kernel image identity,
   mapped device, snapshot identity, and user. DE readability or a mounted
   root directory is insufficient. Credentials must never appear in logs.

For filesystem discovery, mount flags alone do not prove absence of storage
writes. The live runtime investigation reported ext4's `orphan cleanup on
readonly fs` even with `ro,noload`. This audit then confirmed the exact #107
source path:

- `fs/ext4/super.c:5511` skips journal loading when `NOLOAD` is set, but the
  separate `ext4_orphan_cleanup(sb, es)` at line 5705 has no `NOLOAD` guard.
- `fs/ext4/orphan.c:396–399` returns early if `bdev_read_only(sb->s_bdev)`.
- Otherwise, lines 420–422 print that message and temporarily clear
  `SB_RDONLY`; line 506 restores the saved flags after cleanup.

Thus `ro,noload` suppresses journal replay but does not itself prevent orphan
cleanup from using a writable block device. A block-level read-only guard must
be set and verified on the selected partition before claiming that the recovery
read mount is write-blocked. Preserve the original device read-only state,
never clear another owner's guard, and never substitute a whole-disk toggle.
The observed message alone does not quantify which bytes, if any, changed.
Unsupported or dirty volumes should produce an explicit result rather than
escalate to repair automatically. A successful guard is not permission for
unrelated firmware or secure-storage operations.

## Validated work delivered now

`kernel/fscrypt-prerequisites.config` provides generic fscrypt, inline fallback,
filesystem security xattrs, loop, and device-mapper snapshot prerequisites. It
deliberately does not set nonexistent `CONFIG_TZDEV` or
`CONFIG_DM_DEFAULT_KEY` symbols. It does not contain an Android decrypt
implementation.

Validation used the exact extracted #107 config in a new temporary `O=`
directory, enabled the fragment's 14 requested options with `scripts/config`,
and ran `make ... ARCH=arm64 LLVM=1 olddefconfig`. The operation returned 0 and
all 14 requested options remained `y`. Dependencies included
`FS_ENCRYPTION_ALGS`, AES/CTS/XTS, SHA-512 library support, and DM buffer support.
Hashes of the nine relevant source Makefile/Kconfig files were unchanged. The
temporary output directory was removed. Only host Kconfig tools were built;
there was no kernel image build or deployment.

Recorded host request IDs for reproducibility:

- `recovery-teegris-audit-20261105-05`: exact kernel/config inspection.
- `recovery-teegris-kconfig-only-20261105-01`: isolated configuration validation.
- `recovery-teegris-fs-abi-20261105-01`: header structure-layout verification.

## Practical continuation

The current release can implement plain filesystem discovery, explicit mounts,
file listing, and controlled file changes on supported Linux volumes. The
separate crypto work should advance through evidence-producing stages:

1. Build a later recovery kernel with the validated generic fragment and prove
   fscrypt behavior using synthetic disposable filesystem images and test keys.
   That establishes the kernel facility independently of Samsung secure state.
2. Port and review Android `dm-default-key` and Samsung TEEGRIS transport against
   the current kernel APIs. Review memory reservations and IRQ/SMC ABI before
   any live transport exercise. No compile or hardware success is claimed yet.
3. Package and verify the matching Android service/library closure, startup
   order, Binder devices, and recovery integration. Treat its filesystem and
   secure-storage writes as explicit operations, not background diagnostics.
4. Establish a supported TZAR/secure-world initialization path separately from
   the userspace port. The historical normal-boot reference is evidence; it is
   not permission to overwrite the current Linux installation or a proven
   true-recovery solution.
5. Test against coherent, isolated Android userdata and metadata copies, with
   an explicit secure-state plan and known control files. Prevent hard-coded
   Android `by-name` references from reaching the physical Linux userdata.

The existing Android archive is at
`<workspace>/backups/android-before-linux-return-20261103/`.
Its `manifest.json`, `result.json`, and `RESULTS.uk.md` record saved userdata,
metadata, and boot with a successful exact-stream/archive verification.
That establishes saved bytes, not a tested restore or decrypt result. A future
test must account for matching metadata and current secure state; a raw image
hash cannot establish that RPMB or credential state has not changed. No full
53 GB image re-read, decompression, restore, or decrypt was performed here.

## Primary external references

- [Android file-based encryption](https://source.android.com/docs/security/features/encryption/file-based): Android's DE/CE model and the userspace/kernel roles.
- [Android metadata encryption](https://source.android.com/docs/security/features/encryption/metadata): the separate metadata layer, `dm-default-key`, and KeyMint/Keymaster involvement.
- [Linux fscrypt documentation](https://docs.kernel.org/filesystems/fscrypt.html): filesystem encryption and userspace key provisioning; generic fscrypt is not a vendor hardware-key unwrapping service.
- [Android dynamic partition implementation](https://source.android.com/docs/core/ota/dynamic_partitions/implement): `super` metadata and logical partition mapping.

Device-specific conclusions above come from the named local sources and logs.
General Android documentation does not establish that this Samsung device or
this particular recovery build has satisfied those requirements.
