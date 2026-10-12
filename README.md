# Mainline Linux for the Samsung Galaxy A22 5G (SM-A226B, `a22x`)

Main repository: https://gitlab.com/pessdres/a22x-mainline
(mirror: https://github.com/Dodaskot/a22x-mainline; prebuilt images are also
attached to the GitHub releases).

> **AI-assisted development.** This port was developed with heavy use of
> AI coding assistants (Claude and Codex). Every change was built and
> tested on the real phone; see [docs/PROVENANCE.md](docs/PROVENANCE.md)
> for how the published baseline was verified.

Linux 7.2 port for the Samsung Galaxy A22 5G, MediaTek MT6833 (Dimensity 700).
It boots from the stock Samsung LK bootloader as an ordinary `boot.img`:
no bootloader, modem or other partition is replaced. The userspace used
during development is Arch Linux ARM with Phosh.

This tree is the **accepted v135 baseline** (kernel build #95). See
[docs/PROVENANCE.md](docs/PROVENANCE.md) for how it was reproduced and
verified against the image that runs on the test handset.

> **Status: experimental.** Usable as a development phone, not as a daily
> phone: no cellular modem, no camera. Flashing anything may brick your
> device. You need an unlocked bootloader. Keep a stock `boot.img` for rollback.

## What works

| Area | State |
|---|---|
| Boot | stock LK, header-v2 boot.img with a 0x180000 shim, UART earlycon |
| Display | native MT6833 DSI (C-PHY) + JDI HX83112F panel, DCS backlight, 60/90 Hz, full power-off; simpledrm fallback |
| Touch | Himax HX83112F zero-flash (stock firmware required) |
| GPU | Mali-G57 via Panfrost, DVFS 390–955 MHz |
| CPU / CCI | DVFS, little 2.0 GHz / big 2.203 GHz, CCI 1.131 GHz, PSCI CPU idle |
| RAM | 3.44 GiB usable of 4 GiB (Android ZMC pool reclaimed), 4266 MT/s on both channels |
| Thermal | LVTS sensors, CPU/GPU/SoC zones, restored after system sleep |
| Storage | eMMC, microSD (SDR104) |
| USB | peripheral (NCM networking); host/OTG not done |
| Audio | speaker (SIA8109), earpiece, headphones + jack detection, built-in microphone (ALSA UCM provided) |
| Wi-Fi / Bluetooth | vendor gen4m Wi-Fi port and own HCI-over-STP Bluetooth driver (`conn/`) |
| Battery | MT6359 fuel gauge (coulomb counter), MT6360 charger |
| Sensors | ICM-40607 IMU (auto-rotate), MMC5603 magnetometer, MN78911 light/proximity (uncalibrated) |
| Misc | torch, vibration, volume/power keys, RTC |
| Sleep | deep s2idle (CPU/cluster off, 26 MHz off) works in instrumented test cycles at about 11.3 mA (stock Android: about 7.3–7.9 mA); not yet enabled automatically for everyday use |
| NFC | Samsung S3NRN82: reading and writing tags/cards works (e.g. a PolygoCard transit card); no contactless payments; a tag cannot wake the phone from sleep yet |

Not working / not done:

- **Cellular modem and SIM** — in progress, not part of this baseline. The
  modem is powered and started, passes the first handshake (HS1) and its
  early service requests are being answered, but it does not finish booting
  (HS2) yet, so there is no SIM, calls or mobile data.
- Camera, USB host/OTG.
- GPS and fingerprint (deferred).

Details: [docs/HARDWARE.md](docs/HARDWARE.md).

## Repository layout

```
kernel/patches/   131 patches on top of Linux v7.2 (git am), with series file
                  (as a git tree: github.com/Dodaskot/linux-a22x, branch a22x)
kernel/config/    full accepted .config (a22x-v135.config) + config fragments
kernel/build.sh   clone v7.2, apply the series, build Image.gz/DTBs/modules
kernel/update-patches.sh  regenerate kernel/patches from the kernel tree
conn/             MediaTek connectivity drivers (WMT, BTIF, gen4m Wi-Fi, BT HCI)
                  ported out-of-tree from the Samsung GPL release, + systemd units
boot/             mkboot.sh (boot.img for stock LK), initramfs init, flash.sh
rootfs/           ALSA UCM profile, feedbackd theme
tools/            bring-up helpers (memtool, clock/DT generators, smoke test)
docs/             building/flashing guide, hardware notes, provenance
releases/v135/    prebuilt boot.img + matching conn modules (Git LFS)
```

## Quick start

```sh
kernel/build.sh                              # -> kernel/linux, kernel/out
KSRC=$PWD/kernel/linux KOUT=$PWD/kernel/out conn/build.sh
BUSYBOX=... HIMAX_FW=... boot/build-initramfs.sh
OUT=$PWD/kernel/out boot/mkboot.sh v135      # -> boot/images/boot-mainline-v135.img
boot/flash.sh boot/images/boot-mainline-v135.img   # phone in Download mode
```

A prebuilt v135 `boot.img` and matching Wi-Fi/BT modules are in
[releases/v135](releases/v135/README.md). They are kept exactly as built for
v135 and do not contain source fixes made after it
([docs/PROVENANCE.md](docs/PROVENANCE.md#fixes-after-v135)).

Full instructions, the root filesystem layout and rollback:
[docs/BUILDING.md](docs/BUILDING.md).

## Proprietary firmware

The source tree contains no Samsung/MediaTek firmware. The prebuilt
`releases/v135/boot-a22x-v135.img` is the exception: its initramfs contains
the stock touch firmware `Himax_firmware_fw_jdi.bin` (Samsung/Himax,
proprietary), see [releases/v135](releases/v135/README.md).

For your own builds copy these from the stock firmware (`/vendor/firmware`
of the same device):

- touch: `Himax_firmware_fw_jdi.bin` (built into the initramfs)
- connectivity: `soc2_2_ram_mcu_1_1_hdr.bin`, `soc2_2_ram_bt_1_1_hdr.bin`,
  `soc2_2_ram_wifi_1_1_hdr.bin`, `WIFI_RAM_CODE_soc2_2_1_1.bin`,
  `WMT_SOC.cfg`, `wifi.cfg`

## License

Kernel patches and `conn/` are GPL-2.0 (Linux kernel and the Samsung/MediaTek
GPL source release respectively). Everything else in this repository is
GPL-2.0-only unless a file says otherwise. See [LICENSE](LICENSE).
