# Building, installing and flashing

Everything is cross-compiled with LLVM/Clang (`LLVM=1`). The reference
builds used Clang 23.1.1 (kernel.org LLVM toolchain). Host tools needed:
`git`, `make`, `clang`/`lld`/`llvm-strip`, `python3`, `cpio`, `gzip`,
`mkbootimg`/`unpack_bootimg`/`avbtool` (AOSP), `heimdall`.

## 1. Kernel

```sh
kernel/build.sh [linux-dir] [out-dir]     # defaults: kernel/linux, kernel/out
```

The script shallow-clones `v7.2` from the stable tree, applies
`kernel/patches/*.patch` with `git am` and builds with
`kernel/config/a22x-v135.config`. It produces `Image.gz`, the DTBs and
`Module.symvers` (needed by `conn/`).

`a22x-v135.config` is the exact configuration of the accepted kernel #95
and remains unchanged for the tested v142/#100 and v143/#101 source updates.
`slim.config` and `pmos.config` are the fragments it was derived from
(`defconfig` + both fragments with `merge_config.sh`, all `=m` turned into
`=y`, then `CONFIG_MODULES=y` for the out-of-tree connectivity modules).

Device trees (each one includes the previous):

- `mt6833-samsung-a22x.dtb` — base DT, simpledrm on the LK framebuffer.
- `mt6833-samsung-a22x-native-display.dtb` — adds the native DSI/panel
  pipeline.
- `mt6833-samsung-a22x-nfc-experimental.dtb` — adds the NFC controller;
  this is the DTB of the accepted v135 image and the `mkboot.sh` default.

All of them include `mt6833-samsung-a22x-lk-compat.dtsi`, generated from the
stock kernel DTB with `tools/gen-lk-compat-dtsi.py`: LK looks up stock nodes
and applies the stock `dtbo` overlay before starting the kernel, and refuses
to boot when they are missing. Every DTB must be built with symbols
(`DTC_FLAGS_<name> := -@`); `tools/validate-a22x-nfc-dtb.py` checks a DTB
against the stock `dtbo` before you flash it.

## 2. Connectivity modules

```sh
KSRC=$PWD/kernel/linux KOUT=$PWD/kernel/out conn/build.sh   # -> conn/out/*.ko
```

The modules must come from **the same kernel build** as the running
`Image.gz`. `conn/out/kernel-release` stores that build's `uname -v`, which
`conn-start.sh` compares at boot.

## 3. Initramfs and boot image

```sh
BUSYBOX=/path/to/busybox.static HIMAX_FW=/path/to/Himax_firmware_fw_jdi.bin \
    boot/build-initramfs.sh                    # -> boot/initramfs.cpio.gz
OUT=$PWD/kernel/out boot/mkboot.sh v143        # -> boot/images/boot-mainline-v143.img
```

The initramfs starts USB NCM networking (phone `172.16.42.1`, PC
`172.16.42.2`), then `switch_root`s to the partition whose GPT name is given
by `a22x.root=` (default `userdata`, ext4). Add `a22x.noswitch` to stay in
the initramfs; it then offers a root shell on the UART and an
**unauthenticated telnet root shell over USB**.

`mkboot.sh` builds what LK expects: header v2, page size 2048, stock load
addresses, the DTB as an Android DT table, a 0x180000-byte shim in front of
the kernel (LK insists on loading at 0x40080000; mainline needs 2 MiB
alignment), and an AVB hash footer padded to the 32 MiB boot partition.
Default command line additions: `cma=96M` (required by the native display;
Phosh does not start with less) and `clk_ignore_unused pd_ignore_unused`.
`EXTRA_CMDLINE="cma=96M a22x.root=<partname>"` boots from another partition,
for example a root filesystem on the microSD card.

## 4. Root filesystem

Any aarch64 distribution works; development used Arch Linux ARM with Phosh
on the `userdata` partition (ext4). Install:

- `/opt/a22x-conn/`: `conn/out/*.ko`, `conn/out/kernel-release`,
  `conn/tools/{conn-start.sh,wmtload.py,wmtlaunch.py}`, and optionally
  `bt_addr` (the factory Bluetooth address from nvdata).
- `conn/tools/a22x-conn.service`, `conn/tools/a22x-wmt-launcher.service` →
  `/etc/systemd/system/`, then `systemctl enable a22x-conn`.
- connectivity firmware → `/usr/lib/firmware/` (see README).
- `conn/tools/90-a22x-wifi.conf` → `/etc/NetworkManager/conf.d/`
  (the vendor driver creates extra interfaces that NM must ignore).
- `rootfs/ucm/MediaTek` → `/usr/share/alsa/ucm2/MediaTek`, plus
  `/usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833_mt6359.conf` →
  symlink to `../../MediaTek/mt6833/mt6359-sia8109/mt6833-mt6359.conf`.
- `rootfs/usr/share/feedbackd/themes/samsung,a22x.json` (haptics theme).
- `tools/phone/a22x-sleep-hook` → `/usr/lib/systemd/system-sleep/a22x`
  (re-attaches USB networking after s2idle).
- `tools/phone/a22x-smoke.sh` → `/usr/local/bin/` (post-flash check).

Keep `mem_sleep` on `s2idle`: `deep` (PSCI SYSTEM_SUSPEND) cold-reboots on
this firmware. The kernel has no Landlock, so pacman needs `DisableSandbox`.

## 5. Flashing

Flash **only the boot partition**. Never flash bootloader, modem, PIT,
preloader, LK, super or vbmeta with these images.

1. Enter Download mode: hold both volume keys while plugging in USB, or run
   `reboot-download` from the initramfs/UART shell.
2. `boot/flash.sh boot/images/boot-mainline-<tag>.img`
3. After boot check `uname -v`, install matching `conn` modules and run
   `sudo a22x-smoke.sh`.

A successful upload does not prove LK accepted the DTB: verify that the new
kernel actually runs.

## 6. Recovery

- Hung phone: hold **Power + Volume Down** for about 10 s.
- Download mode: hold **both volume keys** and plug in USB, then flash a
  known-good image (a previous mainline build or the stock `boot.img`).
- Android will not boot from stock `boot.img` while `userdata` contains the
  Linux root filesystem.

## 7. UART

The debug UART runs at 921600 8N1 (`console=ttyS0,921600n8`). The kernel
logs at `loglevel=8`, so OOPS and OOM reports reach the UART.
`tools/uartcmd.py` sends commands through a serial-to-TCP bridge.
