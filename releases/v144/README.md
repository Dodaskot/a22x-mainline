# v144 boot.img — kernel #107 and minimal charger

This is the exact SM-A226B image tested on 2026-11-04: it was copied for
publication without rebuilding or changing its bytes. Power-off charging
now enters a small charging display in initramfs, and kernel logs stay on
UART instead of the phone screen.

- File: [boot.img](boot.img), **33,554,432 bytes (32 MiB)**.
- SHA-256:
  `b3f59daa37ad50e0ac6bfaed83f6954147536d07119f1b46a9981ee19d9b17ab`.
- [SHA256SUMS](SHA256SUMS) covers the image and
  [component/source manifest](kernel107-provenance.json).
- Source report: [kernel #107 and charger validation](../../docs/KERNEL-SYNC-20261104.md).

For **Samsung Galaxy A22 5G SM-A226B** with an unlocked bootloader.
The image is for the **BOOT partition only** and expects an existing Linux
root filesystem for normal boot.

## What changed

Kernel #107 retains the earlier USB-C/OTG, GPU watchdog and Himax fixes,
and adds the MT6359 power-off/normal-restart changes. The source is commit
`79a09eba25ef8cb4e03a98325c6ec1e606961810`, tagged
`a22x-kernel107` and `a22x-v144`, with this repository's 143 patches
and `kernel/config/a22x-v144.config`.

The charger is a static AArch64 program that becomes PID 1 when stock LK
requests charger mode. Linux still starts, but the root filesystem,
systemd, Phosh, USB networking and initramfs shell do not start in this
branch. The charging drivers remain responsible for charging. The display
shows capacity, automatically dims after 12 seconds, and has a tested
software policy for Power-key wake/normal restart and unplug power-off;
see [charger behavior and tests](../../boot/charger/README.md).

The command line removes `console=tty0` and adds
`fbcon=logo-count:0 vt.global_cursor_default=0`. UART/earlycon are retained.
The image also preserves these explicit opt-in parameters from its tested
kernel #107 profile:

```text
mt6359_regulator.a22x_lp_probe=1 mt6359_regulator.a22x_lp_stock_init=1
```

Those parameters enable experimental RF-LDO observations/initialization.
They are not part of the new generic `mkboot.sh` default, and this release
does not provide cellular modem service.

## Validation and limits

The installed BOOT readback matched the image above. Normal startup reached
the graphical services and SSH with kernel `7.2.0-a22x+ #107` and no failed
systemd units in the postflight check. A subsequent power-off with the cable
attached entered the LK charger boot, with the controller reporting
`pid=1 rootfs=unmounted network=off normal_userspace=off`. Observation
continued through kernel uptime 213 s with no reset; capacity advanced from
96% to 100%.

The owner confirmed the charging display, no visible kernel logs and
noticeably faster startup. No timed boot-speed comparison was recorded.
Separate physical short/long-Power, unplug, full-charge-cycle and SOC-accuracy
validation is not claimed here. Policy, I/O, input and UI tests passed
natively, with ASan/UBSan and under static AArch64/QEMU. The existing optional
connectivity-bundle tests also passed.

The public build scripts retain the repository's newer bundle guard and
root selector; the prebuilt keeps the tested non-bundle init. The five charger
runtime source files are byte-identical to the tested source. Full rebuilt
boot-image byte identity is not promised.

## Contents and source

- Linux 7.2 kernel #107 and `mt6833-samsung-a22x-nfc-experimental.dtb`.
  Kernel sources: [linux-a22x](https://github.com/Dodaskot/linux-a22x/tree/a22x-v144);
  config and full patch series are in this repository.
- Static charger: [source, build helper and tests](../../boot/charger/).
  Controller GPL-2.0-only; policy/UI MIT. Built with GCC 16.2.0 and statically
  linked GNU libc 2.44 (Arch package `aarch64-linux-gnu-glibc 2.44-1`).
  GNU libc is distributed under its upstream licenses, principally
  LGPL-2.1-or-later; see the
  [GNU libc project](https://www.gnu.org/software/libc/) and
  [GNU libc source](https://sourceware.org/git/glibc.git).
  The complete application source and build helper permit recompiling and
  relinking against a compatible replacement static AArch64 libc:
  `env CHARGER_CC=/path/to/aarch64-linux-gnu-gcc python3 boot/charger/build.py`.
- BusyBox: unchanged from v143, Debian `busybox-static 1:1.37.0-6+b9`,
  GPL-2.0; [corresponding source](https://sources.debian.org/src/busybox/1:1.37.0-6/).
- `memtool` and `reboot-download`: unchanged from v143, with source in
  this repository.
- `Himax_firmware_fw_jdi.bin`: unchanged stock Samsung/Himax proprietary
  touch firmware, 131,072 bytes. This binary is not covered by the repository's
  GPL or MIT license.

Compared with the actual v143 ramdisk, only `init` and
`bin/a22x-charger` changed. The image does not contain a root filesystem,
connectivity modules/firmware, device calibration/NV data or the private
modem test environment. It does not install Mesa/rootfs changes.

## Connectivity modules

Build `conn/` against the exact `a22x-v144` kernel source and
`a22x-v144.config`. Modules from the historical v135 archive are not the
matching v144 module set. The development handset used separately installed
modules built for #107; this boot-only release does not bundle them.
See [BUILDING.md](../../docs/BUILDING.md).

## Download and verify

Download the real image from the release assets, or use Git LFS for the
tracked copy. A small Git LFS pointer text file is not a flashable image.

```fish
# In releases/v144 after downloading boot.img, SHA256SUMS and the manifest:
sha256sum -c SHA256SUMS
# With the intended SM-A226B already in Download mode:
heimdall flash --boot boot.img
```

The command permits Heimdall's normal reboot after a successful BOOT flash.
Keep a known-good stock BOOT image for rollback. Older v135/v142/v143 images
and their tags are unchanged.
