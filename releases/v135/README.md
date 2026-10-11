# v135 boot image (kernel #95)

`boot-a22x-v135.img` — the exact image that runs on the test handset.
`conn-modules-a22x-v135.tar.gz` — matching connectivity modules (conn95).

Boot image SHA-256: `ab8b2a521b275f213b27b6a5304595638189c952f95dd4f4d3db18d62a172420`

For the **Samsung Galaxy A22 5G, SM-A226B** only, with an unlocked
bootloader. Flash it to the **boot partition only**.

## Contents

- Linux 7.2 (`7.2.0-a22x+`, build #95) from `kernel/patches` +
  `kernel/config/a22x-v135.config` of this repository
- DTB `mt6833-samsung-a22x-nfc-experimental.dtb`
- initramfs from `boot/initramfs`: busybox (Debian `busybox-static`
  1:1.37.0-6+b9, GPL-2.0, source: https://sources.debian.org/src/busybox/1:1.37.0-6/),
  `memtool`, `reboot-download`, and the stock touch firmware
  `Himax_firmware_fw_jdi.bin` (Samsung/Himax, proprietary, from the stock
  `/vendor/firmware`)
- command line: `... clk_ignore_unused pd_ignore_unused mem_sleep_default=s2idle cma=96M`

The image only contains the kernel and the initramfs. It mounts the ext4
root filesystem from the `userdata` partition (`a22x.root=` selects
another GPT partition name); without a root filesystem it stays in the
initramfs with a shell on the UART and an unauthenticated telnet root
shell over USB networking (172.16.42.1).

`conn-modules-a22x-v135.tar.gz` holds the Wi-Fi/Bluetooth modules built for
exactly this kernel (`uname -v`: `#95 SMP PREEMPT Sat Oct 10 16:20:39 CEST 2026`).
Install them into `/opt/a22x-conn/` together with `conn/tools` and the
connectivity firmware, as described in docs/BUILDING.md section 4.
Modules from any other kernel build will not match.

## Flashing

```sh
sha256sum -c boot-a22x-v135.img.sha256
heimdall flash --boot boot-a22x-v135.img     # phone in Download mode
```

Keep your stock `boot.img` for rollback. Flashing is at your own risk.
