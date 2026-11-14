# Mainline Linux for the Samsung Galaxy A22 5G (SM-A226B, `a22x`)

Main repository: https://gitlab.com/pessdres/a22x-mainline
(mirror: https://github.com/Dodaskot/a22x-mainline; prebuilt images are also
attached to the GitHub releases).

> **AI-assisted development.** This port was developed with heavy use of
> AI coding assistants (Claude and Codex). Validation includes builds,
> offline checks and the hardware tests stated in each report. New packaging
> features awaiting hardware tests are marked explicitly. See
> [docs/PROVENANCE.md](docs/PROVENANCE.md) for the published baseline.

Linux 7.2 port for the Samsung Galaxy A22 5G, MediaTek MT6833 (Dimensity 700).
It boots from the stock Samsung LK bootloader as an ordinary `boot.img`:
no bootloader, modem or other partition is replaced. The userspace used
during development is Arch Linux ARM with Phosh.

The latest release is **v147 (kernel build #138)**: one battery percent for
every system on the phone. The kernel's fuel gauge starts its count from the
percent kept in the PMIC, where Android's gauge keeps its own, and writes what
it shows back, so an installed Linux, the recovery and Android go on from one
another's number instead of each guessing from the voltage at its start. It
keeps everything v146 (#122) and v145 (#121) added: the USB link kept through
system sleep, USB networking after sleep and during large uploads, 60/90 Hz
changes without a dark flash, a boot splash from the first frame, and skipping
the charging screen when the installed system asks for it. Kernel #138 is
kernel #107 plus seven commits: branch
[`a22x-boot`](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot) and
[kernel/a22x-boot/](kernel/a22x-boot/). See the
[prebuilt v147 boot.img](releases/v147/README.md)
([v146](releases/v146/README.md), [v145](releases/v145/README.md)).

**[Mainline Recovery 1.0.1](recovery/README.md)** ([українською](recovery/README.uk.md))
is a Linux recovery for the RECOVERY partition, with its own kernel #139
(branch [`a22x-recovery`](https://github.com/Dodaskot/linux-a22x/tree/a22x-recovery):
the phone's kernel, branch `a22x`, trimmed for the recovery).
From its touch interface it installs Debian, Ubuntu, Fedora, Arch Linux ARM,
Manjaro, Mobian or postmarketOS from a catalog or a file. You choose the
desktop and extra shells (Phosh, GNOME, Plasma Mobile, Xfce, Sxmo, Lomiri), the
sign-in screen, the language, the account and the apps. Several systems can
stay side by side with a shared `/home` and switch in both directions. The
recovery README lists what works and the known problems, with screenshots and
the [test results](recovery/docs/TEST_RESULTS.md). It also installs Android:
Samsung's firmware, GSIs and ROM files; LineageOS 20 by Loftenth was tested with
it, and BakaSU by Kazaf 132 started. Decrypting Android's data is not available
yet. The recovery image for any SM-A226B and the installer app are published with
the releases of [mainline-recovery](https://github.com/Dodaskot/mainline-recovery/releases).

v144 (kernel #107) added the minimal charging screen, kept kernel logs on
UART instead of the display, and carried the MT6359 power-off and
normal-restart fixes. The
[public kernel branch](https://github.com/Dodaskot/linux-a22x/tree/a22x) and
this repository's **143-patch series** describe kernel #107. See the
[source and validation report](docs/KERNEL-SYNC-20261104.md) and the
[charger implementation](boot/charger/README.md).

Earlier v135, v142, v143 and v144 releases remain unchanged. v144 also retains
their USB-C/OTG and GPU watchdog fixes and the subsequent #102 Himax
teardown fix. See [provenance](docs/PROVENANCE.md) for the release history.

> **Status: experimental.** Usable as a development phone, not as a daily
> phone: no cellular modem, no camera. Flashing anything may brick your
> device. You need an unlocked bootloader. Keep a stock `boot.img` for rollback.

## What works

| Area | State |
|---|---|
| Boot | stock LK, header-v2 boot.img with a 0x180000 shim, UART earlycon |
| Display | native MT6833 DSI (C-PHY) + JDI HX83112F panel, DCS backlight: brightness from the system settings slider (checked live in Plasma Mobile on 2026-11-10) and automatic brightness, 60/90 Hz switched without a dark flash (v145), boot splash (v145), full power-off; simpledrm fallback |
| Touch | Himax HX83112F zero-flash (stock firmware required) |
| GPU | Mali-G57 via Panfrost, DVFS 390–955 MHz |
| CPU / CCI | DVFS, little 2.0 GHz / big 2.203 GHz, CCI 1.131 GHz, PSCI CPU idle |
| RAM | 3.44 GiB usable of 4 GiB (Android ZMC pool reclaimed), 4266 MT/s on both channels |
| Thermal | LVTS sensors, CPU/GPU/SoC zones, restored after system sleep |
| Storage | eMMC, microSD (SDR104) |
| USB | NCM networking and USB-C OTG storage; both plug orientations and return to charging/NCM tested; v145 keeps large uploads and re-attaches after system sleep; v146 keeps the link to a computer through sleep |
| Audio | speaker (SIA8109), earpiece, headphones + jack detection, built-in microphone (ALSA UCM provided) |
| Wi-Fi / Bluetooth | vendor gen4m Wi-Fi port and own HCI-over-STP Bluetooth driver (`conn/`) |
| Battery | MT6359 fuel gauge (coulomb counter), MT6360 charger; v144 adds an initramfs-only power-off charging display; v147 starts the count from the percent kept in the PMIC and keeps it there, shared with the recovery and Android's gauge |
| Sensors | ICM-40607 IMU, MMC5603 magnetometer, MN78911 light/proximity and auto-brightness; absolute lux scale uncalibrated |
| Misc | torch, vibration, volume/power keys, RTC |
| Sleep | s2idle (CPU/cluster off, 26 MHz off) works in everyday use: installed systems suspend by themselves and with the Power key and wake with the Power key; display, Wi-Fi and USB come back. About 11.3 mA asleep (stock Android: about 7.3–7.9 mA). While a computer is attached (v146), its USB traffic keeps the phone awake and the link up |
| NFC | Samsung S3NRN82: reading and writing tags/cards works (e.g. a PolygoCard transit card); no contactless payments; a tag cannot wake the phone from sleep yet |

Not working / not done:

- **Cellular modem and SIM** — in progress, not part of this baseline. The
  bounded development harness reached **HS2 after 1446 verified filesystem
  requests** on 2026-10-24 and revalidated that prefix on 2026-11-03.
  The subsequent READY stage still encounters a DigRF assertion; SIM1,
  network registration, calls and mobile data do not work yet. See the
  [current SIM1 investigation](docs/MODEM-VALIDATION-20261103.md).
- Camera.
- GPS and fingerprint (deferred).

Details: [docs/HARDWARE.md](docs/HARDWARE.md).

The [2026-10-17 development report](docs/VALIDATION-20261017.md) records
OpenCL score 174 with a Gaussian Blur validation failure, an experimental
Vulkan score 989 with no reported workload validation failures, touchscreen
shell selection and the FS582 modem checkpoint. The v143 boot image includes
the kernel #101 watchdog change; Mesa drivers and shell configuration are
installed separately in the root filesystem.

The [2026-10-24 modem report](docs/MODEM-VALIDATION-20261024.md) records
**FS1446 / HS2 confirmed**, with a successful independent capture audit and
no AP reboot during the corrected run. The test infrastructure remains
separate from the published boot release; reaching HS2 does not enable
cellular service in that image.

The [2026-11-03 SIM1 report](docs/MODEM-VALIDATION-20261103.md) records the
latest ASSERT107, RF power/clock checks, captured PC Monitor limits and a
bounded post-fault DHL test. A new stock RF comparison controller has passed
39 host tests; its Android capture is still pending. These diagnostics do
not establish the cause of the fault or add cellular support to v143.

The [2026-10-18 GPU compute report](docs/GPU-COMPUTE-20261018.md) records
**Vulkan 1415** ([result](https://browser.geekbench.com/v6/compute/6942135)),
up from r4's 1236/1233, and **OpenCL 748**
([result](https://browser.geekbench.com/v6/compute/6941745)), up from Rusticl r2's
698. A later instrumented Vulkan run uploaded **1432**
([result](https://browser.geekbench.com/v6/compute/6942162)); its archived
post-run checks are incomplete, so it is listed separately. These results use
private Mesa builds, which are **not bundled in the boot image**. See the report
for per-workload scores, driver changes and validation limits.

## Repository layout

```
kernel/patches/   143 patches on top of Linux v7.2 (git am), with series file
                  (same source as linux-a22x branch a22x / kernel #107)
kernel/a22x-boot/ 7 patches above #107: BOOT kernel #138 (branch a22x-boot)
kernel/a22x-recovery/ 12 patches above #107: recovery kernel #139 and its config (branch a22x-recovery)
kernel/config/    a22x-boot-k121.config, a22x-recovery-k139.config, a22x-v144.config (#107),
                  historical v135 config + fragments
kernel/build.sh   clone v7.2, apply the series, build Image.gz/DTBs/modules
kernel/update-patches.sh  regenerate kernel/patches from the kernel tree
conn/             MediaTek connectivity drivers (WMT, BTIF, gen4m Wi-Fi, BT HCI)
                  ported out-of-tree from the Samsung GPL release, + systemd units
boot/             mkboot.sh (boot.img for stock LK), initramfs init, flash.sh
boot/charger/     static AArch64 minimal charger, host/ARM tests and build helper
recovery/         Mainline Recovery 1.0.1: touch interface, Linux installer, tests (MIT)
rootfs/           ALSA UCM profile, feedbackd theme
tools/            bring-up helpers (memtool, clock/DT generators, smoke test)
docs/             building/flashing guide, hardware notes, provenance
releases/v147/    latest prebuilt boot.img, kernel #138 (Git LFS)
releases/v146/    prebuilt boot.img, kernel #122 (Git LFS)
releases/v145/    prebuilt boot.img, kernel #121 (Git LFS)
releases/v144/    prebuilt boot.img, kernel #107 + minimal charger (Git LFS)
releases/v143/    historical prebuilt boot.img, kernel #101 (Git LFS)
releases/v142/    prebuilt v142 boot.img (Git LFS)
releases/v135/    prebuilt v135 boot.img + matching conn modules (Git LFS)
```

## Quick start

```sh
kernel/build.sh                              # -> kernel/linux, kernel/out
KSRC=$PWD/kernel/linux KOUT=$PWD/kernel/out conn/build.sh
BUSYBOX=... HIMAX_FW=... boot/build-initramfs.sh
OUT=$PWD/kernel/out boot/mkboot.sh local     # -> boot/images/boot-mainline-local.img
boot/flash.sh boot/images/boot-mainline-local.img  # phone in Download mode
```

The prebuilt [v145 boot.img](releases/v145/README.md) contains kernel #121
(patches 0001–0143 plus `kernel/a22x-boot/`) and the tested initramfs. It is
the exact image used on the phone; no publication rebuild was substituted.
The root filesystem is installed separately, most easily with the
[A22x recovery](recovery/README.md).

The prebuilt [v144 boot.img](releases/v144/README.md) contains patches
0001–0143, kernel #107 and the tested minimal charger. Build Wi-Fi/BT
modules against kernel tag `a22x-v144` with `a22x-v144.config` and
`conn/build.sh`.

Historical [v143](releases/v143/README.md), [v142](releases/v142/README.md)
and [v135](releases/v135/README.md) images retain their original contents.
The v135 connectivity-module archive is specific to that older build.

Full instructions, the root filesystem layout and rollback:
[docs/BUILDING.md](docs/BUILDING.md).

## Proprietary firmware

The source tree contains no Samsung/MediaTek firmware. The prebuilt images
`releases/v145/boot.img`, `releases/v144/boot.img`, `releases/v143/boot-a22x-v143.img`,
`releases/v142/boot-a22x-v142.img` and `releases/v135/boot-a22x-v135.img`
are the exception: their initramfs contains the stock touch firmware
`Himax_firmware_fw_jdi.bin` (Samsung/Himax, proprietary), see
[releases/v145](releases/v145/README.md), [releases/v144](releases/v144/README.md), [releases/v143](releases/v143/README.md),
[releases/v142](releases/v142/README.md) and [releases/v135](releases/v135/README.md).

For your own builds, obtain these **five binary firmware files** from the stock
`/vendor/firmware` of the same device:

- touch: `Himax_firmware_fw_jdi.bin` (built into the initramfs)
- connectivity: `soc2_2_ram_mcu_1_1_hdr.bin`, `soc2_2_ram_bt_1_1_hdr.bin`,
  `soc2_2_ram_wifi_1_1_hdr.bin`, `WIFI_RAM_CODE_soc2_2_1_1.bin`


The two text configurations, `WMT_SOC.cfg` and `wifi.cfg`, are now provided
as [documented A22X defaults](conn/firmware/README.md). They do not require
extraction from Android. The optional boot bundle uses them when no local
override is supplied. The five controller firmware binaries remain proprietary.

The [Himax firmware investigation](docs/HIMAX-OPEN-FIRMWARE.md) includes an
open offline format inspector and verified section/CRC analysis. An open
replacement controller program has not yet been implemented.

## License

Kernel patches and `conn/` are GPL-2.0 (Linux kernel and the Samsung/MediaTek
GPL source release respectively). Everything else in this repository is
GPL-2.0-only unless a file says otherwise. See [LICENSE](LICENSE).

## Installation and optional boot bundle

Start with [Arch Linux ARM + Phosh installation](docs/ARCH-PHOSH-INSTALL.md)
and [kernel build/flashing instructions](docs/BUILDING.md). An
[optional boot bundle](docs/BOOT-BUNDLE.md) can carry matching connectivity
modules and locally supplied firmware; it is not yet hardware-validated and
does not replace the proprietary controller firmware with open code.
