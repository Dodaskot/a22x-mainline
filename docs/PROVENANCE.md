# Provenance of this tree

Baseline: **accepted v135 boot image, kernel build #95, conn95** (2026-10-10).

## Kernel

- Base: Linux `v7.2` (stable tree).
- `kernel/patches/0001`–`0126`: development history up to the LVTS sleep
  fix (kernel #94).
- `0127`–`0131`: the five changes of kernel #95 / boot v135 (ZMC reclaim,
  CPU capacities, big-CPU voltage ceiling, FY CPU/CCI frequencies, GPU DVFS).
- Resulting source tree: `18506838c317511d077c911278dc33fe13721d6b`
  (`git rev-parse HEAD^{tree}` after `git am`).
- `kernel/config/a22x-v135.config`: the `.config` of build #95.

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

Samsung/MediaTek firmware blobs, busybox binaries, build outputs, test logs
and device-specific data (serial numbers, calibration, Bluetooth address).
