# v145 boot.img — kernel #121

This is the exact SM-A226B BOOT image used on the phone from 2026-11-09 on
(internally "BOOT v1r5"). It was copied for publication without rebuilding or
changing its bytes. Every Linux system installed by the
[A22x recovery](../../recovery/README.md) during the 2026-11-09/10 tests booted
with it.

- File: [boot.img](boot.img), **33,554,432 bytes (32 MiB)**.
- SHA-256:
  `e3542fcdd905a4b89a10eb828fa983bc9f0837a2635155d0124c692f6c5fbb1f`.
- [SHA256SUMS](SHA256SUMS) covers the image and the
  [component/source manifest](kernel121-provenance.json).

For the **Samsung Galaxy A22 5G SM-A226B** with an unlocked bootloader.
The image is for the **BOOT partition only** and expects a Linux root
filesystem in `userdata`; the recovery installs one.

## What changed since v144

Kernel #121 is kernel #107 (`a22x-v144`) plus five commits, branch `a22x-boot`
of [linux-a22x](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot), tag
`a22x-boot-kernel121`. The same commits are in
[kernel/a22x-boot/](../../kernel/a22x-boot/):

1. musb gadget: OUT packets that arrive before the next request are no longer
   flushed. This loss stalled large uploads over USB networking.
2. f_ncm: the TX timer retries its flush while u_ether is busy. A stuck last
   packet stalled SSH for tens of seconds.
3. musb/mediatek: the USB device role is re-attached after every system sleep.
   USB networking stayed "not attached" after s2idle until the cable was
   replugged.
4. drm/mediatek: 60 ↔ 90 Hz changes only the DSI front porch, so the panel no
   longer goes dark for half a second.
5. drm/mediatek: a boot splash in the kernel. A logo with a moving light shows
   from the first frame until a compositor or greeter takes the display.

The config is [a22x-boot-k121.config](../../kernel/config/a22x-boot-k121.config).
Compared with v144 it only adds the boot splash options and Landlock (with
`SECURITY_NETWORK` and `SECURITY_PATH`), which pacman 7 on Arch Linux ARM needs.

The initramfs changes only in `/init`. When LK starts the phone in charger mode
(power-off with the cable attached, or a reset that lost the restart markers),
`/init` reads `/etc/a22x/charger-mode` from `userdata` read-only and without
replaying the journal. If the active system says `off`, Linux starts normally
instead of showing the charging screen. Systems installed by the recovery get
`off`, and the recovery's settings switch it. The charger, BusyBox, `memtool`,
`reboot-download` and the Himax firmware are byte-identical to v144.

The command line adds `fbcon=vc:1-62` to the v144 line.

## Validation and limits

The BOOT readback matched the image. The kernel `7.2.0-a22x+ #121` booted
Ubuntu 26.04 and 24.04, Debian/Mobian 13 (Phosh and Plasma Mobile), Manjaro
ARM, Arch Linux ARM, postmarketOS v26.06 and Fedora 45. The vendor Wi-Fi and
Bluetooth modules loaded, Wi-Fi connected, and USB networking and SSH worked.
The charger-off path was observed with the same `/init` in an earlier image
(v1r3): power-off with the cable, LK charger mode, then "charge-only boot is
off" and a normal start without the Power key.

Not verified with this image: USB networking after suspend inside a running
installed system (it was verified with the same driver code in the recovery);
the cellular modem (not provided). A restart sometimes lands in the previous
boot mode, because the preloader starts twice and the restart markers in SRAM
are lost; this is not caused by the kernel, see the
[recovery known problems](../../recovery/README.md#known-problems).

## Contents and source

- Linux 7.2 kernel #121 and the v144 device tree. Source:
  [linux-a22x `a22x-boot`](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot),
  with config and patches in this repository. GPL-2.0.
- `/init`: [boot/initramfs/init](../../boot/initramfs/init). The repository
  version also keeps the optional connectivity-bundle guard that the prebuilt
  does not use.
- Static charger: [boot/charger](../../boot/charger/), unchanged from v144.
- BusyBox: Debian `busybox-static 1:1.37.0-6+b9`, GPL-2.0,
  [source](https://sources.debian.org/src/busybox/1:1.37.0-6/).
- Touch firmware `Himax_firmware_fw_jdi.bin`: Samsung stock `/vendor/firmware`,
  proprietary, unchanged from v144.
