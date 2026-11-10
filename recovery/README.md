# A22x Mainline Recovery

A native Linux recovery for the **Samsung Galaxy A22 5G (SM-A226B, MT6833)**.
It lives in the 40 MiB RECOVERY partition, runs entirely from RAM, has its own
touch interface, and installs and manages Linux distributions in `userdata`.
Android installation is planned, but it is not ready yet.

Ukrainian: [README.uk.md](README.uk.md). History before 0.6.124:
[docs/CHANGELOG.md](docs/CHANGELOG.md). Day-by-day progress of 0.6, with every
on-phone check: [docs/PROGRESS.md](docs/PROGRESS.md).

## Status (2026-11-10)

| | |
|---|---|
| Recovery | 0.6.137, SHA-256 `0e8e24b181bdca3f5529f0117febbe57288de7ba821bbe378bc6a6b244435493` |
| Recovery kernel | `7.2.0-a22x-recovery+ #123`: [linux-a22x](https://github.com/Dodaskot/linux-a22x) branch `a22x-recovery`, tag `a22x-recovery-kernel123` |
| Tested with | BOOT v146 (`releases/v146`, kernel `#122`) and v145 (kernel `#121`), branch `a22x-boot` |
| Image | not published: it contains firmware captured from the developer's phone and the developer's SSH key (see [Building](#building)) |

Everything listed as working below was done on the phone by touch, the way a
user would do it, between 2026-11-06 and 2026-11-10. Test accounts and
networks are not part of this repository.

## What works

### Interface

- Lock screen with a PIN; swipe up to unlock.
- Six languages: English, Ukrainian, German, French, Polish and Spanish.
- Themes, animations, 60/90 Hz without a dark flash, brightness, screen timeout.
- Status bar with Wi-Fi, charging, battery and clock. The Power key turns the
  screen off and on. The brightness slider and the screen-off work; the
  recovery blanks the screen but does not suspend, so jobs keep running.
- The start screen and the screen before a restart or power-off follow the
  chosen theme, from the first frame of the kernel's splash on.

### Files and storage

- File manager for every volume (`userdata`, microSD, `cache` and others):
  mount read-only or read-write, browse, copy, rename, edit text, change
  permissions and owner, delete.
- **USB files** (MTP): share one mounted volume with a computer, read-only or
  read-write.
- Terminal.

### Network and remote access

- Wi-Fi: scan, connect, saved networks. Downloads use HTTPS.
- SSH with keys only, and a diagnostics page, over the USB network
  (`172.16.42.1`) and over Wi-Fi.
- **ADB** over USB and over the USB network (`adb connect 172.16.42.1:5555`):
  root shell, `push`/`pull`, and `adb sideload FILE`, which saves the file in
  the image store on `userdata` (a ZIP for later Android installation, or a
  Linux image). A new computer is allowed on the phone screen first and then
  remembered; the recovery password, when set, is asked as for SSH.

### Linux systems

- **Install a new system**: `userdata` is formatted when the installation
  starts.
- **Import a system** ("Add a system"): the new system is saved beside the
  installed ones. Nothing is erased, and it becomes active only when you switch.
- Images come from the **catalog** over Wi-Fi, checked against the published
  checksums and signatures: Debian 13, Ubuntu 24.04 and 26.04, Fedora 45,
  Arch Linux ARM, Manjaro ARM (Minimal, Phosh, Plasma Mobile), and Mobian 13
  (Phosh, Plasma Mobile). They can also come **from a file** on microSD or a
  USB stick: rootfs `tar` (`.gz`/`.xz`), disk images (`.img`, `.img.xz`), or
  `qcow2`. postmarketOS images made for other phones work too.
  An online installation needs no SD card: the image is downloaded into
  `userdata` and deleted after unpacking.
- **Saved on the phone**: catalog images can be downloaded into `userdata`
  (`/.a22x-images`), checked against the catalog, and installed later
  without a network or an SD card; the page shows the free space and deletes
  images. A clean installation keeps this store.
- Choices for each installation:
  - the main desktop and more shells: Phosh, GNOME, Plasma Mobile, Xfce, Sxmo
    and Lomiri (Sxmo and Lomiri only on Debian and Ubuntu);
  - the sign-in screen: automatic, text login, Phrog, GDM, SDDM or LightDM;
  - language and time zone;
  - keep the accounts or create a new user;
  - apps (29 kinds) and package search;
  - Wi-Fi;
  - a setup script that runs once as root.
- **Switch** between the installed systems, with journaling and the choice to
  resume or roll back. `/home` is shared; each system has its own `/etc` and
  `/var`. Systems can also be **removed**.
- The recovery adds kernel modules, firmware and device services by itself. It
  refuses a system that could not start here, for example one with another init,
  missing platform files, or a password format the system cannot read.

### Flashing

- **Flash a partition**: BOOT, RECOVERY, DTBO, vbmeta, vbmeta_system,
  vbmeta_vendor, super, prism and optics, from microSD, a USB stick or
  `userdata`; raw, sparse, gzip, LZ4, XZ or ZSTD. The old content is saved
  first (on the SD card, or in `userdata`'s image store), and the partition is
  read back after writing. RECOVERY and BOOT were flashed this way on the
  phone (2026-11-10).
- Images are judged by their **contents**, not by a list of approved hashes
  (`bootimage.py`): a BOOT or recovery image must be complete (the kernel
  unpacks to its end, or its AVB hash matches) and made for this phone (the
  header board `SRPTL28A011`, the device tree, or the ramdisk's
  `ro.product.bootimage.device=a22x`); the check page shows what is inside,
  for example `Linux 7.2.0-a22x+ / Samsung Galaxy A22 5G (SM-A226B)`. A DTBO
  must be a valid DT table and a vbmeta a valid AVB image. An image for another
  phone, or a damaged one, is refused before anything is written.
- **Restart into the system** checks a changed BOOT, DTBO or vbmeta the same
  way, so a stock Android BOOT, a Magisk-patched one or a new Linux kernel needs
  no update of the recovery. A write that stopped part way is caught by the
  kernel stream or the AVB hash. Linux installations accept any complete Linux
  BOOT for this phone whose kernel release matches the platform modules
  (`7.2.0-a22x+`).

### Power

Restart into the system, the recovery or Samsung Download, and power off.

### Android app (`apk/`)

An installer app for a rooted Android on this phone. It carries a recovery
image and a Linux BOOT image, checks that the phone is a Galaxy A22 5G (from
the Samsung bootloader, the model, the code name and the chip — never a serial
number, so a custom ROM or a GSI passes), and installs the recovery, the BOOT
or both, saving the old partition first and reading it back. It also hands
Wi-Fi networks, the language, the time zone and the clock to the recovery
(`cache:/a22x-recovery/handoff.json`), saves setup scripts the recovery offers,
and restarts into the recovery. Graphite, Daylight and Aurora change its
colours, its launcher icon and its Android 12+ start screen. `apk/build.sh`
builds it without the Android SDK. No APK is published: the images inside it
are built with files from the developer's phone. Its root scripts were run in
the recovery against test devices; the app itself has not run on Android yet.

## Distributions tested on the phone

| Distribution | Source | Shells | Sign-in | Result |
|---|---|---|---|---|
| Debian 13 | catalog | Phosh, Plasma Mobile, Sxmo | Phrog, automatic | works |
| Mobian 13 Phosh | catalog, file | Phosh, GNOME, Lomiri, Sxmo | Phrog, text | works |
| Mobian 13 Plasma Mobile | catalog | Plasma Mobile, Lomiri, Sxmo | SDDM | works |
| Ubuntu 26.04 | catalog, `root.tar.xz` | Plasma Mobile, Phosh, GNOME, Xfce, Sxmo | SDDM, automatic | works |
| Ubuntu 24.04 | catalog | GNOME, Phosh | text | works |
| Fedora 45 | catalog, `qcow2` | GNOME, Plasma Mobile, Phosh | GDM, SDDM | works |
| Arch Linux ARM | catalog, `tar.gz` | Phosh, Plasma Mobile | Phrog | works; GNOME with text login installs, not checked again after the 0.6.115 fix |
| Manjaro ARM Plasma Mobile | file | Plasma Mobile, Xfce | LightDM | works |
| postmarketOS v26.06 Plasma Mobile (PinePhone Pro image) | file | as in the image | its own | works (with a new account) |

Xfce was checked on Ubuntu 26.04 and Manjaro, and text login on Ubuntu 24.04
and Mobian 13.

Eight of these systems were installed side by side on one `userdata` with a
shared `/home`. Switching between them in every direction worked.

## Known problems

1. **A restart sometimes lands in the previous mode.** In this session it
   happened 5 times in about 50 restarts. The preloader starts twice, the
   second run clears the restart markers in SRAM, and LK then boots the mode of
   the previous start. Where LK keeps that mode is not known; it is not in
   `misc` or `para`, in RTC `PDN1`, or in WDT `NONRST_REG2`. Workaround: restart
   again.
2. **Sleep with a computer attached.** With BOOT v146 (kernel #122) the USB
   link to a computer stays up during sleep, but the computer's traffic wakes
   the phone, so it does not stay in deep sleep while attached (the screen
   stays off). On a charger or on battery it sleeps as before.
3. **Lomiri** works since 0.6.118 with a grid unit of 16 px. Mir crashed at
   scale 3 because 2408 is not a multiple of 3. Systems installed earlier keep
   24 px: reinstall them, or set `GRID_UNIT_PX=16`.
4. **Text login font.** On Debian and Ubuntu, greetd's terminal (VT 7) stayed
   at 8×16, and Cyrillic was hard to read. Fixed in 0.6.124; not yet checked
   on a fresh installation.
5. **Automatic sign-in with several shells** is not verified on the phone.
6. **postmarketOS**: musl cannot read a yescrypt password. Since 0.6.122 the
   recovery stops and asks for a **New user** with the same name; since 0.6.123
   the phone shell starts as that user. Passwords are now stored as SHA-512.
   The image's `zram` service fails, and the PinePhone Pro image has no SSH.
7. Plasma keeps its language in the shared `/home`, so a Plasma system shows
   the language chosen in another one.
8. **Android**: installing stock firmware, ROMs or ZIP packages and decrypting
   Android data are not ready. The screens for stock firmware, partition
   flashing, backup and restore exist, but they were not qualified again in 0.6.
9. **Not yet checked on the phone:** Wi-Fi, language and time zone handed
   over by a phone system (`a22x-recovery/handoff.json` on `cache`), and setup
   scripts saved in `cache` (`a22x-recovery/scripts/`).
10. The image leaves 2.3 MiB of the RECOVERY partition free. The kernel also
   supports XZ and ZSTD initramfs, which would free more room.
11. There is no public image yet, because the build needs platform files
    captured from the phone.

## Building

`tools/build-image.sh NN` builds `build-v06-NN/recovery-a22x-mainline-k108-v0.6.img`.
It layers the 0.6 sources, the kernel and the runtimes on top of the
qualified 0.5 base image (`tools/build_v06_image.py`). It then checks that
every Python file and the interface binary in the image equal this tree.
Rebuilt with this script, 0.6.124 came out byte-identical to the image on the
phone.

Inputs (see the header of `tools/build-image.sh`):

- the qualified 0.5 base build; the recovery kernel and `wifi-modules.tar.xz`
  (built from `a22x-recovery`); the device tree with aliases;
- the glibc runtime for the interface, the HTTPS runtime and static
  `wpa_supplicant`;
- **platform files captured from your own phone**: device firmware, the
  Bluetooth address and the sealed platform profile. These are never
  published; `tools/a22x-capture-platform.sh` captures them.

## Tests

```fish
unshare -r python3 -B -m unittest discover -s tests   # 528 tests
cd ui; and make test systems-ui-integration display-ui-integration shells-ui-test locale-ui-test
make -C ui arm64 CROSS_COMPILE=aarch64-linux-gnu-     # static interface for the phone
```

`unshare -r` gives the file-ownership tests a user namespace; no root is used.

## License

New code is MIT ([LICENSE](LICENSE)); fonts are under
[ui/LICENSE.fonts](ui/LICENSE.fonts). An assembled image also contains
separately licensed components: the GPL Linux kernel, BusyBox, GNU libc,
Python, musl, OpenSSL and device firmware. Their licenses still apply.
OrangeFox and TWRP sources, branding and artwork were not used.
