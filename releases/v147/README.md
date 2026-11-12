# v147 boot.img — kernel #138

The SM-A226B BOOT image used on the phone from 2026-11-12 on (internally
"BOOT v1r7"). It is v146 with only the kernel replaced; the header, command
line, ramdisk and device tree are byte-identical to v146.

- File: [boot.img](https://github.com/Dodaskot/a22x-mainline/releases/download/v147/boot.img), **33,554,432 bytes (32 MiB)**.
- SHA-256:
  `5faafaa0035bff7bd77251ab01e2b4c395798755ad24db7b3240cc3d6139ad0f`.
- [SHA256SUMS](https://github.com/Dodaskot/a22x-mainline/releases/download/v147/SHA256SUMS) covers the image and the
  [component/source manifest](https://github.com/Dodaskot/a22x-mainline/releases/download/v147/kernel138-provenance.json).

For the **Samsung Galaxy A22 5G SM-A226B** with an unlocked bootloader.
The image is for the **BOOT partition only** and expects a Linux root
filesystem in `userdata`; the [A22x recovery](../../recovery/README.md)
installs one, and can flash this image itself (**Install → Flash a
partition → boot**). From 0.6.196 on the recovery takes this image for every
new Linux system by itself.

## What changed since v146

Kernel #138 is kernel #122 plus one commit, branch `a22x-boot` of
[linux-a22x](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot), tag
`a22x-boot-kernel138`, also in
[kernel/a22x-boot/0007](../../kernel/a22x-boot/0007-power-supply-mt6359-fuel-gauge-start-from-the-percen.patch).
The config is the same as #121 and #122
([a22x-boot-k121.config](../../kernel/config/a22x-boot-k121.config)).

**One battery percent for every system on the phone.** Until now each system
counted the charge from its own start: the kernel took one reading of the
voltage while the system was starting, and in the flat middle of this cell's
curve that reading can be 30 points off. So Android showed one number, the
recovery another and an installed Linux a third.

Android's gauge keeps the percent it shows in the clock domain of the PMIC
(`RTC_AL_MTH[15:8]`, where the vendor's code keeps its "RTC UI SoC"). The
kernel's gauge now starts its count from that percent, as long as the measured
voltage does not contradict it (the open-circuit voltage the percent stands
for must lie within 70 mV of the measured one), and writes what it shows back
whenever it changes. The recovery's kernel (#137) has the same driver, so the
recovery and the installed systems go on from one another's number.

While the count runs, the voltage corrects it with care, one percent per poll,
and more readily against the direction of the current than along it: a cell
that is drained for minutes sags more than its resistance explains.

## Validation and limits

Flashed with the recovery, which read the partition back. Debian 13, installed
by the recovery from its catalog, booted with `7.2.0-a22x+ #138`: the system
was running with no failed unit, USB networking and SSH worked, Wi-Fi was up.

The charge, both ways: the recovery had kept 94 %, and Debian started from the
stored 94 % (the voltage said 95 %). Under a load of 0.8 A for five minutes
Debian's count went to 93 % and was kept; the recovery then started from the
stored 93 % (the voltage, right after the load, said 90 %).

Not checked with this image: Android picking up the number a Linux system left
(the register and its format are the vendor gauge's, and the value found there
at first was Android's own, 91 %, but Android was not started afterwards); the
other distributions and desktops (the kernel change is in the fuel gauge only;
the same systems booted with v146); the cellular modem (not provided). A
restart sometimes lands in the previous boot mode, because the preloader
starts twice and the restart markers in SRAM are lost; this is not caused by
the kernel, see the
[recovery known problems](../../recovery/README.md#known-problems).

## Contents and source

- Linux 7.2 kernel #138 and the v145 device tree. Source:
  [linux-a22x `a22x-boot`](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot),
  with config and patches in this repository. GPL-2.0.
- Ramdisk, `/init`, charger, BusyBox and touch firmware: unchanged from v145,
  see [releases/v145](../v145/README.md#contents-and-source).
