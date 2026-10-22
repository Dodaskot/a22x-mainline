# Provenance of this tree

Baseline: **accepted v135 boot image, kernel build #95, conn95** (2026-10-10).
Current source adds patches 0132–0135 tested in **v142/#100** (2026-10-13).
Patch 0136 adds the tested #101 watchdog change, published as **v143**.
The latest image is in `releases/v143/` (boot image only);
`releases/v142/` and `releases/v135/` are unchanged.

## Kernel

- Base: Linux `v7.2` (stable tree).
- `kernel/patches/0001`–`0126`: development history up to the LVTS sleep
  fix (kernel #94).
- `0127`–`0131`: the five changes of kernel #95 / boot v135 (ZMC reclaim,
  CPU capacities, big-CPU voltage ceiling, FY CPU/CCI frequencies, GPU DVFS).
- Resulting source tree: `18506838c317511d077c911278dc33fe13721d6b`
  (`git rev-parse HEAD^{tree}` after `git am`).
- `kernel/config/a22x-v135.config`: the `.config` of build #95.
- Git tree: https://github.com/Dodaskot/linux-a22x, tag `a22x-v135`
  (commit `d8e9901c4064dd491e7643cf001d5b32ac75a227`, same tree).

Verification: `mt6833-samsung-a22x-nfc-experimental.dtb` built from this
tree is byte-identical to the DTB inside the flashed v135 image
(SHA-256 `ae0d0683392dc2fcfc19fdd17c2d11cfb966551747bc499938bcd901655cd036`).
The v135 image itself has SHA-256
`ab8b2a521b275f213b27b6a5304595638189c952f95dd4f4d3db18d62a172420`;
its command line is the `mkboot.sh` default including `cma=96M`.
`Image.gz` is not byte-reproducible (build timestamp), so the kernel image was
not compared byte for byte.

## Connectivity (`conn/`)

- Base: connectivity sources of the Samsung SM-A226B GPL release
  `A226BXXSBDYA1` (kernel 4.14).
- Ported to 7.2 in 30 commits, plus the `suspend_hif_wake` change
  (`wlan/core/gen4m/os/linux/hif/axi/axi.c`) used since build #90.
- Removed from the vendor drop: a prebuilt ARM object
  (`wlan/core/gen4m/os/linux/gl_sec.o.new`) that the build does not use.
- See `conn/A22X-CHANGES.md` for the list of changes.

## Not included

The source tree has no Samsung/MediaTek firmware blobs, busybox binaries,
build outputs, test logs or device-specific data (serial numbers,
calibration, Bluetooth address).

Exception: the prebuilt `releases/v135/boot-a22x-v135.img` and
`releases/v142/boot-a22x-v142.img`, plus `releases/v143/boot-a22x-v143.img`,
are images that ran on the test handset,
and their initramfs (identical in all three) contains a static busybox
and the proprietary stock touch firmware `Himax_firmware_fw_jdi.bin`
(listed in `releases/v135/README.md` and `releases/v142/README.md`).

## Fixes after v135

`releases/v135/` stays byte-for-byte as built for v135 (kernel #95, conn95).
These later source fixes are not in its `boot.img` or in
`conn-modules-a22x-v135.tar.gz`:

- `conn/bt/hci/btmtk_stp.c`: on a failed send the driver freed the skb that
  `hci_send_frame()` frees again (double free); a failed allocation of the
  locally answered Read Sync Train Params reply now fails the command
  instead of letting it time out. The prebuilt `btmtk_stp.ko` still has the
  old code; the fix needs conn modules rebuilt for the running kernel.

Scripts that come from the source tree rather than from the image were
also corrected after v135 and now report failures through their exit
status: on the phone `conn/tools/conn-start.sh` and
`tools/phone/a22x-smoke.sh`, on the build host `kernel/build.sh` and
`tools/kconfigcheck.py`. `conn-start.sh` works
with the v135 modules.

## v142 source update (2026-10-13)

Patches 0132–0135 add the carried kexec relocation CFI fix, controller-local
I2C supply lookup, MT6360 VBUS/alert handling, and MUSB/DT USB-C dual-role support.
Applying all 135 patches to the declared Linux v7.2 base produces tree
`e0b3aaca697f1a53d8edb402636450cd4a35a111`.
The accepted CPU/GPU/CCI device-tree settings are preserved. Configuration and
Module.symvers match the preceding kernel; installed connectivity modules were
retained. The kernel mirror remains the historical baseline until separately synced.

`releases/v142/boot-a22x-v142.img` is the tested image (SHA-256
`aa375adf2cfe1852a6422b1cb7c34c17f27e64e82f7980e9ea6c8c2c5071188c`). Its
initramfs and command line are byte-identical to v135; the kernel is build
#100 from the 135-patch tree above with `a22x-v135.config`, and the DTB is
the built NFC DTB listed in the validation report. No connectivity modules
are published for #100. The `btmtk_stp` fix listed above is in the source
but not in any prebuilt module.

See [validation and limits](VALIDATION-20261013.md) for device results, artifact
hashes, sensor observations and the current modem checkpoint. The CCCI test
harness and device-specific NVRAM evidence remain outside this public tree.

## v143 / kernel #101 publication (2026-10-22)

Patch 0136 changes only Panfrost `JOB_TIMEOUT_MS` from 500 to 2000 ms so
valid long-running compute jobs can complete. Timeout recovery is retained.
It applies on top of the v142 source described above; config and DTB are
unchanged. The published image was built on 2026-10-17, not rebuilt for this
release, and kernel `7.2.0-a22x+ #101` was confirmed running on the handset
on 2026-10-22.

The reconstruction of the v142 kernel baseline differed from the original
raw kernel by 78 bytes, limited to GNU build IDs and empty-initramfs mtimes;
executable content was identical. The #101 kernel adds the one-line timeout
change. The release retains the v142 boot header, command line, DTB and
initramfs byte-for-byte. Its SHA-256 is
`0dc6162e0b6608ce5e772738b272d1c2ce8cd31b27189ee8c73cf111ce6d374e`.
Component hashes are in [kernel101-provenance.json](../releases/v143/kernel101-provenance.json).

The [GPU report](GPU-COMPUTE-20261018.md) records complete Geekbench runs
on #101. Private PanVK/Rusticl compiler and task-splitting changes live in
userspace and are not included in this boot image. No connectivity modules,
root filesystem, cellular test harness or device-specific NVRAM are added
by this release. Build connectivity modules for this source/config as in
[BUILDING.md](BUILDING.md).
