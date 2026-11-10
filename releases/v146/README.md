# v146 boot.img — kernel #122

The SM-A226B BOOT image used on the phone from 2026-11-10 on (internally
"BOOT v1r6"). It is v145 with only the kernel replaced; the header, command
line, ramdisk and device tree are byte-identical to v145.

- File: [boot.img](https://github.com/Dodaskot/a22x-mainline/releases/download/v146/boot.img), **33,554,432 bytes (32 MiB)**.
- SHA-256:
  `9701f48ddced60c648e79a502885778935d1cb5610a011d77fc1898eea01d41a`.
- [SHA256SUMS](https://github.com/Dodaskot/a22x-mainline/releases/download/v146/SHA256SUMS) covers the image and the
  [component/source manifest](https://github.com/Dodaskot/a22x-mainline/releases/download/v146/kernel122-provenance.json).

For the **Samsung Galaxy A22 5G SM-A226B** with an unlocked bootloader.
The image is for the **BOOT partition only** and expects a Linux root
filesystem in `userdata`; the [A22x recovery](../../recovery/README.md)
installs one, and can flash this image itself (**Install → Flash a
partition → boot**).

## What changed since v145

Kernel #122 is kernel #121 plus one commit, branch `a22x-boot` of
[linux-a22x](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot), tag
`a22x-boot-kernel122`, also in
[kernel/a22x-boot/0006](../../kernel/a22x-boot/0006-usb-musb-keep-the-device-link-through-system-sleep.patch).
The config is the same as #121
([a22x-boot-k121.config](../../kernel/config/a22x-boot-k121.config)).

**USB stays connected while the phone sleeps.** With v145 the computer lost
the phone during every system sleep (USB error -71), and USB networking came
back about 8 seconds after waking. Now, while a computer is attached, the USB
controller keeps its session, pull-up and interrupts through sleep, so the
computer never sees a disconnect; SSH and file transfers continue.

Because the computer's traffic wakes the phone, a phone attached to a computer
does not stay in deep sleep: it suspends and is woken by the USB interrupt
(the vendor Android driver also keeps the phone awake while a computer is
attached). The screen stays off. On a charger or on battery nothing changes:
the phone sleeps as before, and the device role is re-attached on waking.

## Validation and limits

Flashed with the recovery's **Flash a partition**, which read the partition
back. Fedora 45 (Plasma Mobile, SDDM) booted with `7.2.0-a22x+ #122`; USB
networking, SSH and Wi-Fi worked. PowerDevil's "sleep" (`suspendToRam`)
entered s2idle and the musb interrupt woke the system 0.2 s later; over five
minutes the computer kept the device and its NCM carrier, and every ping was
answered. The brightness slider in the system settings changed the panel
backlight (200 → 52 → 191 of 255).

Not checked with this image: the other distributions (the kernel change is in
the USB controller only; the same systems booted with v145), the cellular modem
(not provided). A restart sometimes lands in the previous boot mode, because
the preloader starts twice and the restart markers in SRAM are lost; this is
not caused by the kernel, see the
[recovery known problems](../../recovery/README.md#known-problems).

## Contents and source

- Linux 7.2 kernel #122 and the v145 device tree. Source:
  [linux-a22x `a22x-boot`](https://github.com/Dodaskot/linux-a22x/tree/a22x-boot),
  with config and patches in this repository. GPL-2.0.
- Ramdisk, `/init`, charger, BusyBox and touch firmware: unchanged from v145,
  see [releases/v145](../v145/README.md#contents-and-source).
