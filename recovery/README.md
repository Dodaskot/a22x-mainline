# A22x Mainline Recovery

A native Linux recovery for the **Samsung Galaxy A22 5G (SM-A226B, MT6833)**.
It lives in the 40 MiB RECOVERY partition, runs entirely from RAM, has its own
touch interface, and installs and manages Linux distributions in `userdata`.
It also installs Android: Samsung's firmware, GSIs from the internet and ROM
files, and tells why an Android did not start.

Ukrainian: [README.uk.md](README.uk.md). History before 0.6.124:
[docs/CHANGELOG.md](docs/CHANGELOG.md). Day-by-day progress of 0.6, with every
on-phone check: [docs/PROGRESS.md](docs/PROGRESS.md).

## Status (2026-11-12)

| | |
|---|---|
| Recovery | 0.6.188, SHA-256 `28edd82f1b0d353e7a14d63a260d95702df830bae22f50a19e274c8440032f02` |
| Recovery kernel | `7.2.0-a22x-recovery+ #127`: [linux-a22x](https://github.com/Dodaskot/linux-a22x) branch `a22x-recovery`, tag `a22x-recovery-kernel127` |
| Tested with | BOOT v146 (`releases/v146`, kernel `#122`) and v145 (kernel `#121`), branch `a22x-boot` |
| Image and app | [Releases](https://github.com/Dodaskot/a22x-recovery/releases): the recovery image for any phone (the same build without the developer's SSH key and without the phone's Bluetooth address, see [Building](#building)) and the installer app that carries it |
| Test results | [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md): every automatic suite, and what was done on the phone |

Everything listed as working below was done on the phone by touch, the way a
user would do it, between 2026-11-06 and 2026-11-12. Test accounts and
networks are not part of this repository.

## Screenshots

Rendered by the interface itself from its preview fixtures, so no phone data
is in them. The three built-in themes, Graphite, Daylight and Aurora:

![The three themes](docs/screenshots/themes.jpg)

The pages, in Aurora (each one singly: [a22x-recovery/docs/screenshots/en](https://github.com/Dodaskot/a22x-recovery/tree/main/docs/screenshots/en)):

![Home, installing Linux and Android](docs/screenshots/pages-en-1.jpg)
![Android, backup, files, terminal, flashing](docs/screenshots/pages-en-2.jpg)
![Storage, network, settings, power](docs/screenshots/pages-en-3.jpg)

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
  the image store on `userdata`: a ZIP for later Android installation, a Linux
  image, or an image of one of the phone's partitions (BOOT, recovery, vbmeta,
  DTBO, sparse or super images, named by what they are) for **Flash a
  partition**. A new computer is allowed on the phone screen first and then
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
- **Logical partitions inside super** (`system`, `odm`, `product`, `vendor`):
  an image (a GSI's `system.img`, raw or sparse) goes into one of them; a
  larger image makes the partition grow into super's free space within its
  group, every copy of super's metadata is rewritten and read back. A small
  partition's content is saved first, and the same image is recognised as
  already there. Checked on the phone on `odm`: written, read back, restored
  from the saved copy, super's metadata unchanged (2026-11-10).
- **Wipe** (Install → Wipe), as in TWRP: cache without the recovery's own
  folder (its settings, keys, Wi-Fi networks, plans and scripts stay), or a
  whole volume in Files. Checked on the phone (2026-11-10).
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

### Android (0.6.151, covered by tests; on the phone next)

- **Images from archives.** An image is flashed straight from a ZIP
  (`system.img`, its only image, or one chosen by name, its CRC checked) and
  from Samsung's firmware tars (`AP_….tar.md5`: read in place through lz4);
  nothing is unpacked beside it.
- **Samsung firmware** (Install → Android), the way Odin flashes it: the AP
  file is chosen, and the CSC (or HOME_CSC), BL and CP beside it are found.
  Every file's MD5 is checked; `super`, `boot`, `dtbo`, `vbmeta` and
  `vbmeta_system` are written from the AP and `prism`, `optics` from the CSC,
  each image checked as it is read and read back after writing. This recovery
  stays; the bootloader chain (BL, `*-verified`, `tzar`), the modem and the PIT
  are never written. Then, as stock recovery does after Odin, `userdata` is
  formatted for Android (f2fs with the features of Samsung's own
  `userdata.img`: encrypt, extra_attr, project_quota, quota, verity,
  compression, checked after formatting), the start of `metadata` is erased
  (Android creates it again) and cache is wiped but for the recovery's folder.
  A Linux BOOT is saved on cache first.
- **The image catalog has filters**: category (Linux / Android), Android
  version, type (AOSP / Google apps), region (when there are several) and "For
  this phone / All phones". It lists Google's GSIs for Android 13 to 17; 13 to
  15 support this phone's vendor (Samsung's Android 12 vendor, FCM level 5), 16
  and 17 are made for newer vendors and are marked "May not start". An image is
  downloaded into the store, its SHA-256 checked.
- **GSI installation**: `system.img` from the ZIP goes into `super/system`,
  `vbmeta.img` (verification disabled, as Google flashes it) into `vbmeta`, and
  `userdata` is formatted for Android. A GSI starts with Samsung's BOOT and
  vendor, so Samsung's firmware comes first; otherwise the installation does
  not start.
- **`android_rom.py`** reads ROM packages as data, never running update-binary:
  `payload.bin` (full OTAs: REPLACE, REPLACE_XZ, REPLACE_BZ, ZERO, DISCARD, every
  operation's and partition's SHA-256 checked), block-based OTAs, image ZIPs and
  GSIs; the bootloader chain and the modem are refused.
- **Root** (Android → Root, 0.6.158): the recovery asks GitHub for the newest
  Magisk release, downloads the APK checked against the SHA-256 GitHub states,
  and patches the BOOT on the phone with Magisk's own `boot_patch.sh`, its
  `magiskboot` and `busybox` (as in a recovery: `BOOTMODE=false`, `KEEPVERITY`
  and `KEEPFORCEENCRYPT` on; `abort` only reports and unmounts nothing) in a
  private tmpfs. The patched BOOT is checked (same phone and kernel,
  `.backup/.magisk` in the ramdisk), kept on cache and written as any BOOT; the
  BOOT that was there is kept on cache first, so "Remove root" flashes the stock
  one back. Magisk 31 puts no app into BOOT: the Magisk app is installed in
  Android, and Magisk modules in it. KernelSU and KernelSU Next are not offered:
  their modules load only into GKI kernels (5.10+), and this phone's Android
  kernel is 4.14.

- **On the phone (2026-11-11):** Magisk 31.1 installed from Root; Android
  started and `su` gave a root shell. On this Samsung the first root needs
  verification off in vbmeta and Android's data erased once (shown before the
  hold). After that one reset BOOT went Magisk, stock, APatch, stock, Magisk
  and Android kept its data every time.
- **APatch** (0.6.162): KernelPatch goes into the kernel inside BOOT, by the
  steps of the APatch app's own `boot_patch.sh`. The APK's `kptools` needs
  Android's linker, so the recovery carries `kptools` built from KernelPatch's
  source (`apatch/build-kptools.sh`, inputs pinned by SHA-256) and its `kpimg`;
  nothing is downloaded. The SuperKey is typed on the phone, reaches the
  helper in a file of the private RAM folder and is kept nowhere. Checked on
  the phone: KernelPatch 0.13.9 answered from the running Android kernel and
  accepted only the right SuperKey.
- **ZIP with an installer** (0.6.164, TWRP's Install): a flashable ZIP's own
  `update-binary` runs as root in a room of its own (new mount and process
  spaces, a tmpfs root, a busybox shell with `getprop`). Its `/dev` holds only
  boot, dtbo, the vbmeta partitions, super, prism, optics and super's logical
  partitions; the bootloader chain, the modem, this recovery, `userdata` and
  the recovery's folder on cache are out of its reach. The BOOT there is kept
  on cache first, and the result names the partitions that changed. Checked on
  the phone with Magisk's ZIP. For an installer that is a program (edify) the
  room holds Android's property area at `/dev/__properties__` (0.6.167), with
  the phone's names and what the installed Android's `build.prop` files say;
  a ROM package that maps super's partitions itself (edify's
  `map_partition`, `update_dynamic_partitions`) finds the device mapper and
  the nodes it waits for (0.6.172), and what it leaves mapped is removed
  afterwards. Checked on the phone with the updater of a LineageOS 23.2
  package and scripts of our own that write nothing; a whole ROM package has
  not been installed this way yet.
- **The catalog lists LineageOS** 22.2 (plain and with Google apps) and 23.2,
  the GSIs AndyYan publishes, each pinned by SHA-256.
- **Cache looks after itself** (0.6.162): when room is needed, the second copy
  of the same BOOT, older copies of a partition kept before a write and older
  BOOTs with root are removed; the stock BOOT, a Linux BOOT, settings, keys
  and Wi-Fi networks stay.
- **Back returns to the page before** (0.6.161), from every page: the pages
  passed are remembered, a Back pressed while a page is still being read is
  made after the read, and a read that ends for a page already left opens
  nothing.

- **Restore and Verify have two categories, Linux and Android** (0.6.174).
  Copies of Android's partitions are looked for on every mounted SD card and
  USB drive; a card that is not mounted is a row of its own, and a tap mounts
  it read-only and searches it. Without storage for Linux snapshots the page
  opens on Android. The page of a copy shows its partitions (a tap leaves one
  out), checks the copy, restores it by a hold and deletes it.
- **A copy of Android is one compressed archive** (0.6.174): `NAME.tar.zst`
  in `A22xAndroidBackups`, a tar of the partition images and a manifest,
  packed by zstd on every core. A skippable frame after the zstd stream holds
  the manifest again, so a list of copies reads a few kilobytes, and on a
  computer plain `tar --zstd -xf` unpacks the archive. A restore checks the
  whole archive first, writes only the pieces that differ and reads each
  partition back. Recovery and cache are in no copy. The BOOT that was on the
  phone is kept on cache, and a restored Android BOOT gets the mark that
  keeps this recovery. The bootloader locks dtbo and vbmeta while the phone
  runs: when they differ they are left as they are (and with verification on
  in vbmeta the restore refuses before it writes). Folder copies made before
  stay readable. On the phone: super, 6.9 GiB, became 2.93 GiB in about two
  minutes; the seven other partitions, 768 MiB, became 410 MiB; restoring an
  identical copy wrote nothing (the partitions' write counters did not move);
  after the stock BOOT was flashed, a restore rewrote 15 MiB of 32 and the
  SHA-256 of BOOT matched the copy again.
- **"From a file" opens a sheet that asks where the file is** (0.6.174): the
  SD card and USB drives; a tap mounts the one chosen read-only and opens its
  folders, and Back from its top folder returns to the sheet. On a phone whose
  userdata holds Android nothing needs mounting: a clean Linux installation
  is planned on the partition as it will be once formatted, and the format
  itself waits for the confirmed start of the installation. When BOOT holds
  Android's kernel, the New system page says so at once and leads to Flash a
  partition (0.6.175): Linux starts with its own BOOT. Since 0.6.178 the
  installation takes that BOOT with it (see below).
- **"ROM from a file"** in the Android menu (0.6.174): the file is chosen on
  a card, a stick or cache, and the recovery opens the review that installs
  it: a ZIP with an installer, a system image (raw, sparse, xz, gz, or a ZIP
  or 7z that holds one) or Samsung's firmware; a `payload.bin` update for
  phones with two slots is refused with an explanation. Checked on the phone
  up to the review pages.
- **The catalog shows the category of what is being installed** (0.6.174):
  only Linux from New system, only Android from the Android menu, both from
  Home.
- **`adb sideload` also works with Android on userdata** (0.6.173): the file
  goes to the store on a card (or to `cache:/sideload` without one), and a ZIP
  opens its review at once, as TWRP's sideload does. Checked on the phone to
  the end of a test installer.
- **An Android image is downloaded on a phone that runs Android** (0.6.170).
  Saved images lived only beside the Linux systems on userdata, so a phone
  with Android there had no place for a download. Now the store is the same
  folder, `.a22x-images`, on an SD card or a USB drive when userdata holds no
  Linux: the page says "Download to the card", the helper mounts the card for
  the download and leaves it read-only, and the installation is reviewed from
  that card. Linux images keep their place on userdata. Checked on the phone:
  LineageOS 22.2 downloaded to the SD card, its SHA-256 equal to the
  catalog's, and the review of its installation opened; the installation
  itself, which erases Android's data, has not been run.
- **A backup is renamed and deleted on its page** (0.6.167, as on TWRP's
  Restore page): Restore > a backup > Rename, or hold Delete for 3 seconds. The
  backend touches only a folder that loads as a finished backup, and deletes
  it only under its own name. A copy of phone partitions is deleted the same
  way from its list. Checked on the phone on the SD card.
- **Settings are reset** (0.6.167, TWRP's Restore defaults) by a hold at the
  end of Settings: theme, terminal colours, animation, navigation, vibration,
  screen timeout, brightness and refresh rate. The language, the time zone,
  the password with its lock mode, Wi-Fi networks and keys stay.
- **A card is partitioned** (0.6.167, TWRP's Partition SD card): Install >
  Wipe > Partition a card. `sdcard.py` writes a new MBR table with a FAT32
  partition for files and, on request, ext4 for Linux and swap, on 4 MiB
  borders, and makes the filesystems with the tools in the image. Only the
  card in the slot and USB drives are offered; a card that is mounted, held,
  shared over USB or write-protected is refused, and the apply is bound to the
  card as it was shown. On the phone the whole path was run on a loop device
  over a file in RAM (table, kernel reread, `mkdosfs`, `mkfs.ext4`, `mkswap`,
  then `blkid`, mounts and `fdisk -l`); no real card has been erased yet.

### Added in 0.6.176 to 0.6.181

- **Screenshot** (0.6.176): Volume down and Power together, in either order.
  The PNG is packed and goes to `A22xScreenshots` on a USB drive or an SD
  card, else to cache, else to RAM until the restart.
- **Flashlight** (0.6.176, brightness in 0.6.177): the camera's LED from the
  home page, the power menu and Settings, in every level the LED has (13, up
  to 175 mA, the current of Samsung's own factory torch). The level is kept;
  a power action switches the light off first.
- **Battery level** (0.6.178): the interface keeps its own estimate. The
  kernel's gauge starts from one voltage reading taken under load, which can
  be 30 points off in the flat middle of the curve; the estimate takes the
  changes from the kernel's charge counter and its position from the voltage
  corrected for the current, and learns the phone's real series resistance.
- **A copy of Android says how much room it needs** (0.6.178) before the
  first byte is written: samples of each large partition are packed the way
  the archive packs them. Too little room is refused at once, with numbers.
- **Android's data without decryption** (0.6.178): `metadata` and `userdata`
  can go into a copy as they lie on the phone, encrypted, only together and
  only when chosen. What the cell erased reads as zeros, so a copy takes
  about what the data takes. A restore zeroes ranges through the kernel.
  Android opens restored data only while the phone's secure hardware still
  holds the keys it was made with; a start of Android has not proved this yet.
- **The Linux BOOT comes by itself** (0.6.178): a new system on a phone whose
  BOOT holds Android's kernel takes a Linux BOOT kept on cache, else the
  mainline project's release, checked before anything is erased and written
  once the system is in place.
- **What started on this phone** (0.6.178): catalog entries of GSIs carry the
  result of a start on an SM-A226B where one was made.
- **A factory reset asked for in Android's settings** (0.6.179) opens the
  page that erases Android's data by itself; before, the request stayed in
  `misc` and the bootloader came back to the recovery at every start. BOOT is
  not touched, so a root in it stays for the fresh Android.
- **Several ZIPs in one go** (0.6.181), as TWRP adds more ZIPs: each is
  reviewed when it is chosen and joins a list of up to six; one hold runs
  their installers in order and stops at the first that fails, and the result
  says what became of each. A restart of the system afterwards can be asked
  for; it counts down ten seconds and can be stopped.

### Added in 0.6.182 to 0.6.188

- **Why Android did not start** (0.6.183). The log of Android's kernel
  outlives a restart in the phone's memory. The recovery reads it at its start
  and, when Android's last start ended by itself, opens a page that says why,
  with the kernel's own lines: an Android that needs a newer kernel than the
  phone's (Google's Android 15 image asks for 4.19, this phone's Android
  kernel is 4.14), Android's init giving up, or a kernel panic. The page opens
  once for each such start, and the whole log goes with Export diagnostics.
  Before this the reason could only be read over a serial cable.
- **The right Linux BOOT for a new system** (0.6.182). An installation takes
  the BOOT release this recovery was made for: kept on cache, else downloaded.
  An older Linux BOOT that the phone keeps is used only when the release cannot
  be had, and a clean installation replaces an older Linux BOOT that is there.
  Before, the newest BOOT on cache won, and that could be one from an old test.
- **Charge** (0.6.182 to 0.6.188, recovery kernel #124 to #127). The kernel's
  gauge judges its start against the cell's curve in volts, refuses a percent
  that the voltage contradicts, and corrects its count with care; the interface
  shows that number as it is. See Known problems for what is still open.
- **A pushed file is flushed** (0.6.185). `adb push` to the recovery says
  "pushed" only after the file is on the storage.
- **An image that brings its own Phosh** (0.6.186 to 0.6.188), found with
  Manjaro ARM Phosh on the phone. Such an image has no greetd, which the
  recovery uses for the automatic sign-in, so Phosh stayed disabled and the
  phone stopped at a text login: the image's own `phosh.service` is now used
  for the chosen account. Its Mesa (24.0) keeps this phone's GPU path under the
  name `mediatek`, not `kmsro`: asked for the wrong name it drew in software,
  and Phosh's buttons answered late or not at all. Every session now asks the
  installed Mesa for the name at its start. And phoc's wlroots fix for this
  phone, which was built only where the recovery installed the desktop, is
  now built for such an image too when its phoc needs it.
- **Catalog.** Google's Android 15 images are gone from it: they cannot start
  on this phone's Android kernel.

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
builds it without the Android SDK. Since 0.6.188 the APK is published with the
releases, with the recovery image for any phone inside. Its root scripts were
run in the recovery against test devices; the app itself has not run on
Android yet.

## Distributions tested on the phone

| Distribution | Source | Shells | Sign-in | Result |
|---|---|---|---|---|
| Debian 13 | catalog | Phosh, GNOME, Plasma Mobile, Sxmo | Phrog, automatic, text | works; Phosh + GNOME with text login set up by the recovery, switching both ways checked (2026-11-10) |
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
   `misc` or `para`, in RTC `PDN1`, or in WDT `NONRST_REG2`. Since 0.6.141 the
   recovery notices a restart into the system or Download that came straight
   back (less than a minute later, and userdata's ext4 mount count unchanged,
   so no system started) and restarts once more by itself. A restart from a
   Linux system into the recovery that lands in Linux still needs another
   restart.
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
8. **Android.** Checked on the phone: Samsung's stock firmware installed from
   the recovery, root with Magisk and with APatch, a ZIP with a script
   installer, and partition images. Not yet checked on the phone: installing
   a GSI or a custom ROM (LineageOS, crDroid and the others of the catalog),
   since it erases Android's data and waits for the owner's word. Not done:
   decrypting Android's data (put off), and with it a file-level backup of
   `/data`; mounting system and vendor from super in Files.
   A ZIP whose installer is a program (edify) was run on the phone with the
   updater of a LineageOS 23.2 package: it read the phone's properties and
   mapped super's partitions. A whole ROM package installed by its own updater
   has not been tried: that replaces the system.
9. **Restoring a Linux backup on the phone is refused.** The backup itself is
   made and verified, but `restore` and `wipe` ask for a qualification capsule
   that matches the code, the kernel and the partition sizes; the one in the
   image is of 0.5 and of the older userdata size (`GEOMETRY_MISMATCH`). A new
   one needs a Linux system on userdata. A partition image is restored through
   Restore > Restore a partition image, and phone partitions through Backup >
   Phone partitions.
10. **Not yet checked on the phone:** Wi-Fi, language and time zone handed
   over by a phone system (`a22x-recovery/handoff.json` on `cache`), and setup
   scripts saved in `cache` (`a22x-recovery/scripts/`).
11. The image leaves 1.3 MiB of the RECOVERY partition free. The kernel also
   supports XZ and ZSTD initramfs, which would free more room.
12. **The charge kept for Android does not hold.** The recovery's gauge writes
    its percent where Android's gauge keeps its own (the PMIC's RTC domain),
    and reads it back, but the write never reaches the RTC's core (a reload
    of the registers shows the old value at once), so after a restart the
    register holds what Android wrote before. Android and the recovery still
    count from their own starts, and may show different numbers.
13. **The setup of an installed system cannot be run again.** A system whose
    desktop was set up by an older recovery keeps that setup; the fixes above
    reach it only by installing it again (beside the others or clean).
14. The manifests inside the published image name build folders of the
    developer's computer. No key, address or network is in them.

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

`PUBLIC=1 PRIVATE=platform-public tools/build-image.sh NN-public` builds the
image for any phone (0.6.180): no SSH key is built in, so a computer is let in
only by the request on the phone's screen, and the Bluetooth address in the
platform files is zeros: a system installed from it makes its address from the
phone's own storage chip. That image still carries the device firmware an
SM-A226B needs (touch, Wi-Fi, Bluetooth), and its build manifests name the
paths of the machine it was built on.

## Tests

```fish
unshare -r python3 -B -m unittest discover -s tests   # 700 tests
cd ui; and make test systems-ui-integration display-ui-integration shells-ui-test locale-ui-test
make -C ui arm64 CROSS_COMPILE=aarch64-linux-gnu-     # static interface for the phone
```

`unshare -r` gives the file-ownership tests a user namespace; no root is used.
The last full run, suite by suite: [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md).

## License

New code is MIT ([LICENSE](LICENSE)); fonts are under
[ui/LICENSE.fonts](ui/LICENSE.fonts). An assembled image also contains
separately licensed components: the GPL Linux kernel, BusyBox, GNU libc,
Python, musl, OpenSSL and device firmware. Their licenses still apply.
OrangeFox and TWRP sources, branding and artwork were not used.
