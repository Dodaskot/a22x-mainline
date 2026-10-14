# v142 boot image (kernel #100)

`boot-a22x-v142.img` — the exact image tested on the handset on 2026-10-13
([validation report](../../docs/VALIDATION-20261013.md)).

Boot image SHA-256: `aa375adf2cfe1852a6422b1cb7c34c17f27e64e82f7980e9ea6c8c2c5071188c`

For the **Samsung Galaxy A22 5G, SM-A226B** only, with an unlocked
bootloader. Flash it to the **boot partition only**.

## Contents

- Linux 7.2 (`7.2.0-a22x+`, build #100) from `kernel/patches` 0001–0135 +
  `kernel/config/a22x-v135.config` of this repository (source tree
  `e0b3aaca697f1a53d8edb402636450cd4a35a111`)
- DTB `mt6833-samsung-a22x-nfc-experimental.dtb`
  (SHA-256 `3a87ca9785d91db1bd079ded9cc9833e31aaf927afc2a1e8f4542ef50e5c05a6`)
- initramfs and command line byte-identical to
  [v135](../v135/README.md): busybox (Debian `busybox-static`
  1:1.37.0-6+b9, GPL-2.0, source: https://sources.debian.org/src/busybox/1:1.37.0-6/),
  `memtool`, `reboot-download`, and the stock touch firmware
  `Himax_firmware_fw_jdi.bin` (Samsung/Himax, proprietary, from the stock
  `/vendor/firmware`)

Compared with v135 it adds USB-C OTG (automatic host/device switching,
5 V source VBUS), see the validation report for what was and was not tested.
It does not contain the cellular modem work; there is still no SIM, calls or
mobile data.

The image only contains the kernel and the initramfs. It mounts the ext4
root filesystem from the `userdata` partition (`a22x.root=` selects
another GPT partition name); without a root filesystem it stays in the
initramfs with a shell on the UART and an unauthenticated telnet root
shell over USB networking (172.16.42.1).

## Connectivity modules

No connectivity modules are published for this kernel. On the test handset
v142 ran with the Wi-Fi/Bluetooth modules already installed in
`/opt/a22x-conn/`, built for an earlier kernel with the same configuration;
they loaded, Wi-Fi connected and `hci0` came up, while `conn-start.sh`
warned `modules were built for another kernel build`. For a clean setup
build `conn/` against this source tree (docs/BUILDING.md section 2); that
also picks up the `btmtk_stp` fix made after v135.

## Flashing

```sh
sha256sum -c boot-a22x-v142.img.sha256
heimdall flash --boot boot-a22x-v142.img     # phone in Download mode
```

Keep your stock `boot.img` for rollback. Flashing is at your own risk.
