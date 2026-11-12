# A22x Recovery 0.6: development log

This is the English version of the recovery's working journal, from the first
0.6 build (2026-11-06) to 0.6.137 (2026-11-10). It keeps only what concerns
the recovery: its builds, the checks done on the phone, the problems found and
how they were fixed. Kernel notes are kept where they decide what the recovery
can do (USB after sleep, BOOT images). Test accounts, network names,
addresses and personal paths are left out. Image hashes are SHA-256.

Each entry separates what was **verified on the phone** from what was only
built or tested on a computer. "Run N" is one complete installation driven by
touch on the phone, the way a user would do it.

## 2026-11-06: the 0.6 base

Base: the qualified 0.5 recovery with kernel #108; `source-v11` adds the
following.

Done and checked:

- `tools/a22x-image-import.py`: turns ISO (erofs/squashfs), raw images,
  `img.xz` and `tar.gz` into a `bundle.json` and `rootfs.tar.gz` on a
  computer, without root. Owners, setuid bits and capabilities are kept. The
  images were checked by signature or checksum; four bundles passed
  `distro.scan_archive`.
- `platform/a22x-wmt.c`: a static replacement for `wmtload`/`wmtlaunch`; on
  the phone it started the connectivity subsystem and `wlan0` came up.
- Platform profile `a22x-ext4-v2`: accepts BOOT #107 and #108. Adapter files
  are no longer pinned by hash; modules and firmware stay pinned.
- Interface: Operating systems menu, level icons, screen timeout. The timeout
  was checked on the phone: it locked after 2 minutes.

**Verified on the phone:**

- Arch Linux ARM installed into a slot by `distro.py`, booted with kernel
  #107: systemd running, no failed units, SSH over USB, shared `/home`,
  console on the panel. `systemd-resolved` crashed once at start (cause not
  found; seen again later on Fedora, then active).
- `systemctl reboot --reboot-argument=recovery` from the slot landed in the
  charger instead of the recovery; the restart markers had not been written.
  The adapter got an SRAM guard: normal markers from early boot, `a22x-reboot`,
  clearing only at power-off.
- RECOVERY 0.6 flashed with Heimdall (readback matched, BOOT unchanged).
  Leaving a slot for Download and for the recovery with the SRAM markers worked
  2 of 2 times.
- Debian 13: the import first failed (`insmod` lives in `/usr/sbin`; fixed),
  then booted to multi-user with USB networking and Wi-Fi modules, but
  `ssh.service` failed for lack of host keys. Fixed in the importer
  (`a22x-sshkeys`).

## 2026-11-06: an autonomous recovery

The recovery no longer needs a computer to prepare systems:

- `image_import.py`: ISO, GPT/MBR, ext/btrfs/xfs/erofs/f2fs/squashfs, tar,
  and xz/zstd/gzip/bzip2. Detection checked on four real images; loop mounts
  only on the phone.
- `distro.py`: `import-plan` takes an image plus `--desktop` and `--account`
  and installs into a slot without a computer. Modules, firmware, accounts and
  Wi-Fi come from the installed Linux.
- `a22x-firstboot.sh`: Wi-Fi, shell installation with the package manager,
  the account.
- `install.py`: stock Android installation does not write RECOVERY, formats
  `userdata` with the stock recovery's parameters (`make_f2fs`) and clears
  `metadata` (not checked physically).
- Interface: the Install options wizard.

**Verified on the phone (build 02/03):**

- Debian 13: running, no failed units, booted in 9 s; `a22x-reboot recovery`
  works without buttons.
- Ubuntu 24.04 from a raw `img.xz`: installed in 3 min 40 s on the phone,
  booted in 9.5 s (degraded by `multipathd` and `rpi-eeprom`, masked since).
- Fedora 45 from a raw ISO (erofs): installed in 7 minutes, booted in 14 s,
  Wi-Fi came up through NetworkManager by itself. GDM and GNOME Shell ran on
  the GPU. Fedora with Phosh: `dnf` over Wi-Fi, greetd, `running` after a clean
  reboot.

What an Arch installation carries over to other systems automatically: sound
(UCM, mixer), the feedbackd theme, the system-sleep hook, CPU affinity for
suspend, `HandlePowerKey=ignore`, zram sysctl, `regulatory.db`. Not carried
over: Arch-only builds of phoc, Vulkan and GNOME Settings.

USB stick (OTG), first attempt: host role, VBUS and PHY were on, but the MUSB
stayed in `a_wait_vrise` and no device appeared. Fixed in later builds (see
build 21–25).

Not implemented at this point: custom ROMs on `super`/GSI, ZIP and
`payload.bin`; stock AP tar on the phone; settings across restarts.

## 2026-11-07: builds 14–27 — interface, settings, USB stick, smoothness

**Build 14** (`bd3a2b93…`). Verified on the phone: the new 1080×2400
renderer on the real framebuffer; language and time offset applied after a
recovery restart; `settings.py` writes to `cache` and reads back after the RAM
copy is gone; `flash.py check` recognised the real `vbmeta` and refused the
same file for `dtbo`; the haptic motor vibrates. Only on a computer: writing
with `flash.py` (raw, sparse, gzip, lz4, saving the old content, readback).
Fixed: pixelation (a 720×1600 canvas was stretched without filtering); the
brightness slider (every request cancelled the touch and frames came every
70 ms); USB sticks (`device/cid` does not exist for USB media).

**Build 17** (`4dec052c…`). Themes Graphite, Daylight and Aurora, a bento grid,
glass surfaces, a battery icon, automatic detection of a USB stick. Frame
render time on the phone: Graphite 6.1 ms, Daylight 9.1 ms, Aurora 9.2 ms.
Found: binding the gadget while a stick is inserted drops the host role, so
USB files stops as soon as the cable is gone.

**Build 18** (`11462788…`). Arcs at card corners fixed (the shadow template had
one radius for all cards).

**Build 21** (`3d168ee1…`). Smoothness, measured by injecting touches: frames
went from 33–50 ms to 16–17 ms. The interface ran on a little core with the big
cluster at 725 MHz; it is now pinned to the fastest cluster, and child
processes get all cores back. Read-only jobs no longer grey out the page.
Backup to a USB stick: exFAT and ext4 allowed, FAT32 refused (checked on a
computer).

**Build 23** (`b7192e88…`). Settings for refresh rate, brightness, terminal
scheme and storage; nine terminal schemes; the Storage page (SD and sticks,
Safe removal); a progress bar with percent and time left for every long job;
a backup plan stops as soon as the archive would not fit. A backup plan on the
SD card stopped correctly with `INSUFFICIENT_SPACE`.

A correction kept from that day: one entry first claimed the user had set the
brightness to 90 %. That was wrong. At start with `refresh=90` the interface
opened the Display page by itself, and one of my blind taps landed on the
slider. Since then every tap is made only after the screen was checked.

**Build 24** (`e2590d76…`). Restoring the refresh rate and brightness at start
no longer opens any page; this also closed a way past the password screen
(the phone had no password set, so it was never used). Verified on the
phone: start at 90 Hz stays on the home screen; 60/90 Hz buttons change the
panel and `settings.json` together; dragging the slider changes nothing but
the slider and its label; scrolling at 90 Hz runs at 45–70 frames per second;
terminal colours and Cyrillic correct; the Storage page; Cancel on a backup
plan stops `partclone` and `zstd` within one second. A side effect of my tests:
raw 10 MB screenshots filled `/run` for a few minutes; they are no longer kept
there.

**USB stick fix (build 25).** On the phone the stick was mounted but the list
failed with `'DeviceContext' object has no attribute 'usb_volumes'`: the
methods existed only in the test context, so the tests passed and the phone
failed. They moved to the shared base class, and a new test checks that every
method the device context calls exists. On exFAT a directory's inode changes
after every cache drop, so backups no longer compare inodes there.

**Build 25** (`04c156bc…`). Changing the refresh rate no longer touches other
controls; the 60/90 Hz buttons moved into a card. The panel still went dark
for about 0.5 s on a refresh change (a full modeset) — fixed later in the
kernel (v145).

**Builds 26–27** (`3ce85c02…`). Animated transitions (pages open from the
tile that was touched, themes spread in a circle), faster drawing (NEON spans,
corner tables): home screen 10.8 → 7.9 ms. Python bytecode is now compiled at
build time: helpers start in 170–270 ms instead of 420–500 ms. Build 26 had
broken animations (a time taken before the start overflowed); fixed in 27.
Measured on the phone: opening a page 20 frames in 323 ms at 60 Hz, scrolling
at 90 Hz with 11–12 ms frames.

Once, after build 26, a restart into the recovery landed in the charger: the
markers had been written, but the preloader found the SRAM header invalid and
cleared it. The way out was a long Power press.

## 2026-11-07: builds 30–40 — seamless 60/90 Hz, boot splash, lock screen, smaller kernel

**Build 30** (`c8e03efb…`, recovery kernel #110):

- **Seamless refresh change.** The two panel modes differ only in the vertical
  front porch; the stock kernel writes only that register after start of frame,
  without DCS commands or a PLL change. The kernel now does the same instead of
  a full modeset. On the phone: four switches over 530 frames, the longest gap
  16.9 ms (it was about 500 ms of black).
- **Boot splash in the kernel** (`mtk_boot_splash.c`): drawn into the
  framebuffer from 1.8 s; the interface starts at 3.9 s, takes over the
  progress bar and fades into its first page. The user confirmed the logo
  appears right after the Samsung screen.
- Six animation styles (zoom, zoom with blur, slide, fade, blur, off),
  measured at 90 Hz: 22–37 frames per transition.
- **Security, checked by touch:** a password set from the keyboard and stored
  as a hash in `cache`; SSH without permission is held and the phone asks;
  refusing ends the session; a wrong password does not let in. Turning the
  screen off locks it (blurred background, clock, swipe up); a touch does not
  unlock; locking revokes the computer's permission; the access request shows
  over the lock screen. While locked, no page but the lock pages can open, and
  actions are dropped. Five wrong passwords start a growing pause.
- A password that "was not saved": `settings.py` returned "storage busy" and
  the password stayed in RAM without a retry; saving is retried now.

Limits stated then and still true: Download mode, a removed SD card and the
Linux systems are not protected by the recovery password. A forgotten password
is reset by deleting `a22x-recovery/settings.json` from `cache`.

**Build 33** (`6540ddb5…`, recovery kernel #111). A correction: build 30's
"418,856 bytes free" was wrong (the count missed the DTB and the AVB limit);
the build script now counts from the AVB limit. On the user's request the
recovery kernel was trimmed to what the recovery uses (sound, Wi-Fi, Bluetooth,
NFC, GPU, network filesystems, other SoCs removed): 3004 → 1805 built-in
options, the kernel file 20.3 → 11.1 MB, 9 MB free in the partition. Checked on
#111: display, 90 Hz, brightness, touch, Power key, battery and charging,
temperatures, CPU frequency, USB networking and SSH, SD read-only and
read-write, `cache` read-write, restart into recovery and Download, FunctionFS.

- Number pad for the password without letters, a switch to the full
  keyboard, buttons that say what happens ("Unlock", "Set password", …).
- **Themes from a file** (`.a22xtheme`, `THEMES_API.md`): checked in Python,
  unreadable colours refused; the C loader ran under ASan/UBSan on 4000 damaged
  files without errors. On the phone: import from SD, kept in `cache`, still
  there after a restart, hide and restore built-in themes. A Beaver theme.
- **About the phone** (`sysinfo.py`): model, bootloader, hardware; the serial
  number never reaches the logs (a test checks it).
- A saving bug: while a terminal or SSH session held the storage, a change
  stayed in RAM and was lost at restart. Saving now retries every 10 s; and an
  unreadable disk is no longer taken for an empty one (that would have erased
  the password hash). A restart with a password kept theme, password and lock.

**Build 37** (`c5b727dc…`). The user checked build 33: USB stick, USB files,
restart, power-off and vibration work.

- Frame pacing: the trimmed kernel had no GPU, so the panel became `card0`
  while the interface opened `card1`, and one vblank error closed the device
  for good. The card is now found by its DSI connector, and an error skips
  one frame.
- **Screen off saves power:** the panel itself is turned off through DPMS
  (backlight first), unused GPU and audio power domains are released 20 s after
  start. Estimated system draw: screen on about 316 mA, old screen-off 154 mA,
  now about 65 mA. Waking takes 0.68 s.
- Deep sleep in the recovery is not possible with the stock LK: in recovery
  mode LK skips loading the SPM firmware (`unsupported SPM firmware` in all 47
  recovery boots, `SPM firmware=0x4` in all 52 normal boots). s2idle itself
  works in the recovery (2 successful suspends). Not pursued.
- Gestures (swipe up home, from the edge back, swipe and hold for the
  terminal), optional navigation buttons, a long Power press opens the power
  menu (also when locked), the terminal starts directly.

**Builds 38–40** (`4fdc2ff4…`). The user checked build 37: everything works,
"much smoother".

- The terminal keeps running in the background while a command runs; an idle
  session closes at once; output stays on the page. A confirmed power action
  stops a background session first. Checked on the phone with `sleep 20` and a
  restart while `sleep 300` ran.
- Rubber-band gestures, wider handle, round shadows for the number pad.
- **mmc incident (build 39):** after a restart the kernel gave `mmcblk0` to the
  SD card, the recovery did not find `cache` and showed default settings.
  Build 40 adds `mmc0`/`mmc1` aliases to the device tree; eMMC is `mmcblk0`
  again and the user's settings were intact.

## 2026-11-07/08: builds 41–50 — translations, restore from an image, clean installation

**Build 41** (`dc4f147b…`). All 80 page previews reviewed in Daylight
(Ukrainian), several in Graphite and Aurora and in English, plus 21 pages on
the phone with real data: no broken layout. Fixed untranslated restore targets,
raw file types in the file menu, and `settings.read_disk` treating an unfound
volume as empty (a test repeats the mmcblk1 incident). One restart into
Download landed in the recovery; the second attempt worked.

**Build 42** (`380c7eff…`). German, Spanish, French and Polish completed
(964 strings each); placeholders checked by a script.

**Build 43.** Importing a compressed partition image (a `dd` of the whole
53 GB `userdata`, 3.5 GB as `.zst`) used to unpack it whole. Zero chunks now
stay holes (sparse write), unpacking stops cleanly when space runs out, and
the plan reads the ext superblock for the used size instead of guessing.

**Build 44** (`09db633e…`). **Restore from a partition image**: pick a file on
the SD card or a stick, `flash.py check --target auto` names the partition from
the contents (an Android boot image → BOOT, ext4/F2FS → `userdata`), shows the
type, SHA-256 and the checksum saved beside it, then a 3-second hold writes it
and reads it back. **Verified by the user on 2026-11-08:** an Arch Linux backup
was restored this way (`userdata` from `userdata.img.zst`, then BOOT), and the
phone booted into Arch.

**Builds 45–46** (`4a146339…`). Importing Mobian stopped with
`PLATFORM_FILE_MISMATCH`: the restored Arch had older platform files than the
recovery's profile. The image now carries the 29 platform files and gives them
to a new slot after checking every SHA-256; adopting the existing system no
longer compares them. **The user installed Mobian from a USB stick, switched to
it and signed in** (Wi-Fi joined, Bluetooth up). The interface gained one-page
account creation and import progress with stages, bytes and time left.

**Build 47** (`4e329fc3…`, recovery kernel #112 with btrfs):

- **Clean installation**: on an empty `userdata` a system is assembled in a
  setup folder, checked, then moved into place (`/sbin` last; a failure moves
  everything back). The image's first account (UID 1000: mobian, alarm,
  manjaro) is renamed to the one entered, with its groups and settings; the
  first-boot service starts before the sign-in screen.
- Wi-Fi from the recovery: a NetworkManager key file and a `wpa_supplicant`
  configuration (PSK derived with PBKDF2).
- Apps to choose (browser, settings, files, terminal, editor, store, keyboard,
  Telegram, SSH server) with package names for apt, dnf, pacman and zypper.
- A setup script (up to 1 MiB, hash in the plan) runs once after the shell and
  apps, with `A22X_OS_ID`, `A22X_PACKAGE_MANAGER`, `A22X_USER`,
  `A22X_DESKTOP` and the `a22x-install` helper.
- Roots inside btrfs subvolumes (`root`, `@`, `@rootfs`) are imported.

**Build 48.** After formatting `userdata` in Files, the interface stayed on
"Working" (the format branch never refreshed the volumes; an old bug). Fixed,
with a `format-done` test.

**Build 49.** "Plasma" now installs Plasma Mobile with SDDM automatic sign-in
instead of Plasma Desktop.

**Build 50.** A clean installation from a stick stopped with
`LZMAError: Corrupt input data`; the image file was damaged (most likely the
stick was removed before the copy finished). Damaged xz/gzip/bzip2/zstd data
and cut tar archives now end with a clear `IMAGE_CORRUPT`.

## 2026-11-08: builds 51–58 — first-boot Wi-Fi, Manjaro, Fedora, own Wi-Fi program

**First-boot Wi-Fi on Mobian (builds 51–52).** After a clean installation the
phone booted, but the first-boot service found no network: the driver only
scanned and never joined. Looking through the UART console showed why: the
access point's name ended with a space that was invisible in the network list,
so the saved profile did not match. Build 51 stops starting a second
`wpa_supplicant` beside NetworkManager and connects with `nmcli` (security
taken from the scan, a hidden network tried as well); build 52 also accepts a
visible name that differs only in case or surrounding spaces when it is the
only match, fixes the profile, and resumes the first boot as soon as Wi-Fi
connects. **The clean installation of Mobian with Plasma Mobile then finished
on the phone:** Firefox ESR, QMLKonsole, Maliit keyboard, Telegram from
Flathub and an SSH server installed; after the restart SDDM signed the user in
automatically, Wi-Fi reconnected by itself.

**Manjaro ARM (build 53).** A clean installation of Manjaro (Plasma Mobile)
showed three traps:

- the pacman keyring of the image could not sign (`no writable public
  keyring`); the first boot now re-creates the keyring, updates the keyring
  packages, and retries `pacman -Syu`, removing a package that blocks the
  upgrade and that nothing needs;
- Manjaro's OEM wizard (greetd + sway as user `oem` with password-less sudo)
  came up instead of the desktop; the first boot now locks `oem`, removes its
  sudo rule and starts Plasma Mobile through greetd;
- refused power actions during a backup only returned to the recovery; now
  the reason is written and shown on a page.

Build 53 also added the "Installation complete" page (restart into the new
system / open Systems / home) and imports qcow2 v2/v3 (zlib or zstd
compressed), dynamic and fixed VHD, Vagrant `.box` and tar files holding such
disks — checked byte for byte against `qemu-img`.

**Own Wi-Fi program (build 54).** A static `wpa_supplicant` 2.11 with libnl
(sources checked against Alpine's sha512) is carried in the recovery and
given to systems that lack a Wi-Fi program, with `udhcpc` for DHCP. On the
phone it connected in 3–4 seconds and got a lease; NetworkManager reconnected
afterwards.

**`WRONG_USERDATA` (builds 55–56).** After Manjaro the next installation stopped
with `WRONG_USERDATA`. Manjaro's `resize.service` had grown partition 54 by
about 4 MB into the free space before the backup GPT (other partitions
unchanged; the same service also explains the broken keyring). The partition
table was not changed back. The recovery now accepts both `userdata` sizes,
masks root-growing services (`resize.service`, `growpart`,
`systemd-growfs@`, …) in installed systems, and shows the reason on the
Systems page instead of a generic error.

**Fedora 45 (build 57).** Fedora Cloud Base (GCE `tar.gz`) installed cleanly
with Plasma Mobile, SDDM automatic sign-in, Wi-Fi, Telegram and SSH:

- `dnf` could not resolve names at first: `udhcpc` wrote `resolv.conf`, but
  Fedora asks systemd-resolved; DNS is now handed to `resolvectl`, cloud
  agents (google-*, waagent, nm-cloud-setup, …) are masked, network and
  package failures retry every 10 minutes;
- **sound**: the UCM profile and mixer state were taken only from the running
  system, so a clean installation had none and PipeWire played into the
  loopback card. The recovery now carries them (`platform-optional.tar.xz`),
  installs `alsa-ucm`/`alsa-utils`, restores the mixer, and disables the
  loopback card in WirePlumber. The user confirmed sound works on Fedora.
- the file picker accepts a known image extension followed by one extra
  suffix (a `.qcow2.man` download was refused before).

Not fixed (postponed by the user): Plasma Mobile with Telegram ran out of
memory; pinned GPU buffers (about 820 MB, plasmashell 422 MB) cannot go to
swap. The same was seen on Arch.

**Fedora Generic `qcow2` with Phosh (build 58).** A regression of build 57:
`nmcli` listed `p2p0` as a Wi-Fi device in an image without NetworkManager's
Wi-Fi plugin, so the first boot chose NetworkManager and found nothing. Now
only `wlan0` counts, and after 15 s the recovery's own Wi-Fi program takes
over; hidden networks get `scan_ssid=1`. Found too: directories created under
the recovery's umask 077 were 0700 (`/etc/systemd/user.conf.d`, WirePlumber's
`conf.d`), which broke the user manager and the sound rule; new parent
directories are now 0755. Fedora 45 includes `stevia` as its on-screen
keyboard.

## 2026-11-08: builds 59–69 — Wi-Fi in the recovery, catalog, HTTPS, setup inside the recovery

**A user installation without help.** The user installed Fedora Cloud Base
Azure (`vhdfixed.xz`) with Phosh and nine apps on build 58, with no
intervention (state only read over SSH): Wi-Fi through the recovery's own
program, Phosh and the apps installed, automatic restart; afterwards greetd +
Phrog, Wi-Fi through NetworkManager, sound, Telegram.

**Wi-Fi inside the recovery (builds 59–62, recovery kernel #113).** The device
trees LK passes in normal and recovery mode were compared: the connectivity
and Wi-Fi nodes and reserved memory are identical; in recovery mode LK only
skips the SPM firmware and the modem memory, neither needed for Wi-Fi. Kernel
#113 adds cfg80211 and rfkill; the vendor modules were built for the recovery
kernel. The first attempt failed on a missing wireless-extensions symbol
(kernel rebuilt with `CFG80211_WEXT`), the second on the firmware server
answering only when the Bluetooth patches were present. **Then the user
connected to a network from the recovery's own Wi-Fi page:** DNS, ping and
HTTP worked. `wifi.py` scans, connects (WPA2 PSK derived with PBKDF2, hidden
networks), disconnects; the page lists networks with signal bars and security,
and the connected network is offered to new installations. Build 62 also
added `recovery` as a target of Flash a partition (only an A22x recovery image
is accepted there).

**Build 63.** Nine equal tiles on the home screen.

**Build 64.** Saved networks: after a successful connection the network is
remembered as SSID plus WPA key (never the typed password) in `cache`
(0600, up to 16); at start `wifi.py auto` joins the strongest saved network.
**Verified after a restart: the recovery joined the saved network by itself.**

**Build 65.** The installer: "Install a new system" → from the internet (a
catalog) or from a file; `userdata` is erased only when the confirmed
installation starts. HTTPS in the recovery (Alpine's OpenSSL 3.5.9 and CA
bundle, packages verified with `apk verify`); on the phone HTTPS to the
Fedora and Mobian servers answered, and an expired certificate was refused.
The catalog pins each image (URL, size, checksum) and refreshes itself over
HTTPS from the official indexes, keeping the newest release whose checksum is
published. A keyboard with key previews and a repeating Backspace.

**Builds 66–67.** Sxmo and Lomiri in the installer (Debian and Ubuntu). Build 67
runs the setup **inside the recovery** in a chroot of the new system (proc,
read-only sysfs, a private `/dev` with only safe nodes, devpts, temporary
`resolv.conf`, `policy-rc.d` while it runs, a 90-minute limit, every process
killed and everything unmounted afterwards), so the system starts ready; if
that fails, the first boot finishes the work.

**Build 68.** The first real setup in the recovery did not run: volumes are
mounted `noexec`, so the new root's `/bin/sh` could not start, and the work was
silently left to the first boot. The root is now bind-remounted with `exec`
for the chroot (a test reproduces it on a `noexec` tmpfs). Also: a saved
network appeared as "Open" in the installer and its empty password went into
the new system; the saved WPA key is passed now.

**Build 69.** OpenPGP signature checks in pure Python (`openpgp.py`; RSA ≥ 2048,
SHA-2 only), with pinned keys for Arch Linux ARM, Ubuntu's image signing and
Fedora 45, checked against real signatures; forged data and SHA-1 refused.
Debian, Mobian and Manjaro publish only checksums, so HTTPS plus checksum is
used for them. The catalog grew to ten images; all ten were checked against
the real servers. 29 kinds of apps and a package search (Debian, Ubuntu,
Fedora, Arch) whose results go into the plan.

**First real setup inside the recovery: Ubuntu 26.04 with Sxmo (build 69).** The
user installed Ubuntu Server 26.04 from the catalog with Sxmo, several apps and
packages found by search; the interface log shows download, unpacking,
adaptation, setup steps and the final check all finishing.

The new system's log (written inside the recovery's chroot) lists every app and
both searched packages as installed and ends with `finished`; the first-boot
configuration was removed, so the system started ready: greetd signed the user
into Sxmo, and Wi-Fi used the saved WPA key.

## 2026-11-08 evening: builds 70–76 — Sxmo device profile, Fedora's `/var`, umask

**Ubuntu 26.04 + Sxmo on the phone.** `systemctl is-system-running` was
`running`, greetd signed the user into Sxmo at the right scale (2.75), Wi-Fi
and PipeWire worked. But Sxmo binds the Power and volume keys of a PinePhone,
so a turned-off screen could not be woken. A device profile
(`sxmo_deviceprofile_samsung,a22x.sh`: Power and volume keys of this phone,
scale 2.75, no LEDs) fixed the keys, but then Sxmo looked for hooks in a
folder named after the device and lost its gestures and lock hooks; a link to
the default hooks fixed that. Both are written by the first boot since build
72. On Ubuntu, `chromium` is only a snap wrapper, so Chromium comes from
Flathub there.

**Fedora 45 from the catalog (builds 73–76).**

- The setup inside the recovery had not run at all, and the reason was not
  shown. Now the completion page and the log say why (for example "Recovery
  was not connected to Wi-Fi").
- Mounting `userdata` from the "New system" page did nothing (the mount
  actions were handled only on the Systems page); fixed for all three pages,
  and a catalog choice made before mounting continues after it (build 74).
- The setup then failed at the first `dnf` call: Fedora Cloud keeps `/var` in
  a separate btrfs subvolume, and only `root` was imported. The importer now
  reads the image's `/etc/fstab` and copies subvolumes mounted at `/var`,
  `/home`, `/opt`, `/srv` and `/usr/local` (build 75). **After that the setup
  inside the recovery ran to `finished`.**
- Fedora then started without a sign-in screen: files created by the setup
  inherited the recovery's umask 077 (`/etc/phrog/phoc.ini` unreadable for
  the greeter). The chroot and the first-boot script now use umask 022, and a
  test checks the modes under umask 077 (build 76). The user confirmed Fedora
  works after the repair.

## 2026-11-08 evening: live tests by touch

- A weak network: a download gave up after four retries even though each one
  made progress; now only retries without progress count, and a cut response
  resumes.
- apt inside the chroot could not resolve names: the temporary `resolv.conf`
  was created 0600 under umask 077, and apt downloads as `_apt`. Now 0644.
- "Mount read-write" on the New system page jumped back to Systems; fixed,
  with an integration test that fails without the fix.
- The catalog said "Servers did not answer" although the helper returned
  success: finishing the list started the server check, and the old job's
  output buffer, already owned by the new job, was freed. Found on the phone
  from a log line; a test now runs the real list → check chain.
- **SSH over Wi-Fi**: a second `sshd` with the same rules (keys only, a new
  computer confirmed on the phone) listens on the Wi-Fi address while Wi-Fi is
  connected. Checked on the phone.
- **Identity backup** (TWRP's "EFS"): `identity_backup.py` saves only known
  identity partitions (efs, sec_efs, nvdata, nvcfg, persist, protect1/2 —
  never the bootloader, TEE or modem) with a manifest and SHA-256, reads them
  back, and restores only to the same phone (eMMC CID hash) with the same
  partition layout, saving the current content first.
- Translations no longer leak English where raw text was drawn (the
  translation now happens before raw mode). All 440 Python tests pass under
  umask 077.
- **A live installation by touch: Debian 13 + Phosh** (catalog → Phosh → new
  user → 7 apps → package search for `htop` → review → hold): set up inside the
  recovery (`provision=recovery ready=1`), Phosh, greetd, htop, Flatpak,
  calculator, Squeekboard and Firefox ESR installed. "Restart into the new
  system" → Debian `running` with no failed units, Phrog as greeter, Wi-Fi.
- **Build 77** (`3b0d98b9…`): all of the above. **Identity backup by touch:**
  12 partitions, 279 MiB, saved and verified (`same_phone`).

**USB networking lost the end of long transfers — fixed in recovery kernel
#114 (build 78).** A test of 20 × 600 MB over USB SSH failed 7 times, always
exactly 32 KiB short, then the link stayed silent for seconds. In `f_ncm.c` the
TX timer flushes an incomplete NTB with `ndo_start_xmit(NULL)`; when all IN
requests are in flight, `u_ether` answers `NETDEV_TX_BUSY` and nobody retried.
The timer now re-arms in that case. Same test on #114: **20/20 without a
failure, about 15 MB/s.** (The same fix went into BOOT kernel #116 and later.)

**Offline installation from the SD card by touch** (Wi-Fi off): Ubuntu 26.04
`root.tar.xz` with Lomiri. Found and fixed: without a connection the saved
network in range could not be chosen (it is now offered with its key), and the
clean installation showed the text meant for adding a system. Installed in about
a minute; the setup was left to the first boot ("Recovery was not connected to
Wi-Fi"), and the first boot connected and installed packages by itself — after
one more fix: our `wpa_supplicant` was connected when the network-manager
package's post-install script started NetworkManager with its own supplicant,
and the download broke. Services are now held with `policy-rc.d` while packages
install during the first boot.

**A restart that ended in the charger.** A normal restart from Ubuntu wrote the
SRAM markers, but the preloader started twice; the second start found the SRAM
header invalid, cleared it, and LK chose the charger. The way out was a long
Power press. Other restarts in the same log worked. This is the "previous mode"
problem listed in the README; it is in the boot chain, not in Linux.

Done offline while the phone waited for a Power press:

- **Identity restore from the interface**: a list of copies (newest first,
  copies of another phone marked), a 3-second hold; a test damages a fixture
  partition, restores it through the page and compares the bytes.
- **Screenshot like TWRP**: Power + volume down saves a PNG (1080×2408) to
  `A22xScreenshots` on a writable SD or stick, otherwise to RAM. The PNG is
  written without libraries, and a test decodes it independently.
- `rom_zip.py inspect FILE` names what a ROM ZIP holds (A/B payload, block OTA,
  images, Samsung tar, script-only) and the device it declares, without running
  anything from the archive. Android installation itself waits until the Linux
  side is stable, as the user asked.

## 2026-11-09: charger screen off, Lomiri, Arch and Landlock (builds 83–85)

- **Lomiri** failed with "server-platform: No such file or directory": packages
  are installed without recommends, and Mir's graphics, rendering and input
  platforms are only recommended. The first boot now installs one package of
  each group and treats their absence as a failure.
- **Charger screen off** (user's request). BOOT v1r3 changes only `/init`: in
  charger mode it mounts `userdata` read-only without replaying the journal and
  reads `/etc/a22x/charger-mode`; `off` starts the system normally. **Checked:**
  power-off with the cable attached → LK charger mode → "charge-only boot is off;
  starting the system" → Ubuntu started without the Power key. Every system the
  recovery installs gets `off`, and a Settings card switches it (checked on the
  phone: it read `off`, wrote `on`, then `off`).
- **Screenshot on the phone** (build 83): Power + volume down produced a PNG that
  decodes.
- **Identity restore on the phone:** before the hold, the partitions were
  compared with the copy read-only: only `param` differed (Samsung bootloader
  settings that LK rewrites almost at every start). `param` was removed from
  identity copies (old copies check it but never write it). The restore then
  finished without writing anything — everything already matched.
- A regression from the new BOOT: the installer refused it
  (`BOOT_PROFILE_MISMATCH`), because BOOT hashes were listed in the recovery.
  The hash was added (build 85). (Since 0.6.133 BOOT images are judged by their
  contents instead; see 2026-11-10.)
- **Live installations by touch on build 85:** Debian 13 + Sxmo (set up inside
  the recovery; Sxmo with its gestures, Wi-Fi on 5 GHz); Fedora 45 + Phosh
  (set up inside the recovery, no failed units, Phrog); Arch Linux ARM + Phosh
  installed, but pacman 7 failed inside the recovery: it switches to the
  sandbox user `alpm`, which exists only after the first start, and it needs
  Landlock. The fix runs `systemd-sysusers` before pacman. The kernel lacked
  `CONFIG_SECURITY_LANDLOCK`, so pacman 7 also failed in the installed Arch and
  Manjaro; the user chose to enable Landlock in the BOOT kernel rather than
  weaken pacman's sandbox.
- **BOOT kernel #116**: kernel #107 plus the recovery's USB fixes, a boot
  splash with a moving light (until a compositor or greeter takes the display),
  and Landlock. BOOT v1r4 adds `fbcon=vc:1-62` so the text console does not draw
  over the splash.

## 2026-11-09: Arch with Phosh, a greeter of your choice, several shells (builds 86–95)

- **BOOT v1r4 on the phone** (kernel #116): Landlock up, vendor Wi-Fi modules
  load, the boot splash runs until a compositor takes over. **The first boot of
  Arch now installed its packages** (pacman with Landlock).
- **Arch + Phosh did not start:** greetd hit its start limit. Reproduced
  without root on the phone with a headless phoc: Phosh's home surface asks
  for height 0 without top and bottom anchors, and Arch Linux ARM's phoc links
  the system wlroots, which rejects that; phoc carries a wlroots patch for it,
  and Debian and Fedora build phoc with the patched copy. The working Arch from
  October had a locally rebuilt wlroots without the check.
- **The recovery carries no binaries for this, only recipes and keys:** inside
  the new system's chroot it downloads the wlroots release that phoc links
  against, verifies its signature, applies phoc's patch, builds it, checks that
  every `wlr_*` symbol phoc imports exists, and installs it under
  `/opt/a22x-phoc` with a wrapper that loads it only for phoc (system packages
  stay unchanged). Phrog is built the same way (libphosh from the installed
  phosh version, signature checked; Phrog 0.53.0 by SHA-256 and `Cargo.lock`).
  Recovery kernel #117 adds Landlock so pacman also works inside the chroot.
- The first boot now gives Arch `sudo` for the first user (a checked
  `sudoers.d` rule), as on the other distributions.
- **Sign-in and several shells:** a main shell plus extra shells, and the sign-in
  screen: automatic, Phrog, text, GDM, SDDM or LightDM. A shared greeter wrapper
  shows only the installed sessions; `a22x-shell` starts the shell chosen last
  and falls back to the main one if it crashes twice within 20 s; "Change shell"
  shortcuts.
- **Live test, build 90:** the setup inside the recovery failed at the first
  `pacman -Sy` with "Permission denied": the new root was created 0700, and
  pacman 7 downloads as user `alpm`. The new root is now 0755 during the setup
  (apt had silently downloaded without its sandbox, so Debian never showed it).
- **Live test, build 92, all by touch:** Arch + Phosh + Plasma Mobile + Phrog set
  up inside the recovery in 24 minutes (709 packages; wlroots built with phoc's
  patch; libphosh and Phrog built in 11 minutes; build tools removed afterwards).
  After the restart Phrog showed the user; the first sign-in failed because my
  tap landed on the user row and became the first digit; the second worked.
  **Phosh ran with the patched phoc and did not crash.** "Change shell" → Phrog
  → Plasma Mobile worked, and back.
- **Fixed in build 93:** Phosh asked for the PIN again after Phrog (it unlocks
  only with `GDMSESSION=phosh`; the wrapper sets it after a real sign-in, while
  automatic sign-in keeps the lock); Phrog's default session was random (a
  setting now names the main shell and the user); Phrog did not remember the
  choice (its dconf wrote to `/.config` because the session bus was activated
  before `HOME`); language and time zone of the new system.
- **Live test, build 93:** Arch + Phosh + Plasma Mobile + Phrog in 23 minutes;
  Ukrainian locale and the chosen time offset in the new system; sign-in through
  Phrog by touch at the first attempt; Phosh unlocked at once and in Ukrainian;
  switching shells both ways, Phrog remembered the last one.

**Sleep in an installed system (BOOT #116).** Plasma Mobile put the phone into
s2idle after some idle time; the computer lost the USB device (`error -71`);
bytes and BREAK over the UART did not wake it; the user woke it with the Power
key. Wi-Fi came back by itself, but the USB gadget stayed `not attached` until
a restart. Fixed later in kernels #120 (recovery) and #121 (BOOT).

**Build 95** (`31e8ead1…`): sorted Phrog sessions, language and time-zone choice.
Notes from that day: the recovery boots with the stock `vbmeta` (flags 0) on
this unlocked phone, so writing stock `vbmeta`/`dtbo` from an Android firmware
should not disable it (not checked physically); `samsung_firmware.py` (offline,
not in the image) reads Odin archives in place and maps their members to the
partitions the installer may write, skipping the bootloader chain, `recovery`,
`userdata` and `cache`.

## 2026-11-09: the test runs (builds 95–99)

From here on every run is a complete installation by touch with as many
functions as possible: a new user, a language and city (different each time),
a different set of apps, a package search, a test setup script (it writes a
marker file), a saved Wi-Fi network; afterwards the sign-in screen, every
shell and switching, language and time, the apps, the script marker, Wi-Fi,
SSH and restarts are checked.

**Live check of the install options (build 95):** the "Language and time" card
showed the recovery's language and zone; the time-zone page has translated
regions and winter/summer offsets; the review matched the choices. Phrog's note
"Arch builds it on the phone: 30–40 more minutes" was shown for Debian too;
now only for Arch, Manjaro or a file of unknown family.

**Run 1 — Debian 13 (catalog) + Phosh + Plasma Mobile, Phrog, Ukrainian,
Europe/Berlin.** Installed in 11 minutes; "Restart into the new system" → Debian
in 30 s; first boot log, locale and time zone as chosen; `systemctl` running
with no failed units; Phrog 0.46 from Debian with an on-screen keyboard;
sign-in by touch; Plasma Mobile and Phosh start; Phosh unlocked and in
Ukrainian; switching shells works. Found: Phrog 0.46 reads sessions only from
`/usr/share/wayland-sessions`, so our session list did not apply and the
default was random, and the distribution's Plasma session lacked the
`kmsro` driver override. Fixed by showing Phrog only our sessions (later moved
to the greeter user's service manager, build 99).

**Filesystem repair (a TWRP function):** `filesystem.py check` runs
`e2fsck -f -y` on an unmounted ext2/3/4 volume and reports clean, repaired, errors
left or failed; tested on a real ext4 image with a damaged inode. A hold button
on the volume page.

**Run 2 — Mobian 13 Phosh (catalog) + Sxmo + Lomiri, Phrog, German,
Europe/Athens, 14 apps, htop, the setup script (build 96).** Installed in 9
minutes; the 14 apps mapped to packages; htop installed; the script returned 0
and its marker shows the chosen language and zone. Sound works; Sxmo works.
Fixed in build 99: Phrog 0.46 still listed all distribution sessions (a second
gnome-session runs in the greeter user's service manager, so the session-list
override moved there); Sxmo left services reading the touchscreen after
signing out (they are stopped now); the full name of the renamed first
account still showed the image's account name (now replaced). Lomiri through
the distribution's own session restarted every few seconds; it was checked
again later through our session.

Also in build 99: reading a tar member's byte range and LZ4 images; LightDM gets
an on-screen keyboard and the right DPI; **"Hold: grow to the whole
partition"** (`e2fsck` then `resize2fs`); **the recovery's own time zone with
summer time** (a POSIX rule from TZif data; "As in the recovery" passes the
zone to a new system).

**Run 3 (offline) — Mobian `img.xz` from the SD card, recovery Wi-Fi off
(build 99).** The recovery's time zone was set by touch to Berlin and showed up
as the default in the installer. Disconnect Wi-Fi → Install → from a file on the
microSD card → Plasma Mobile + Phosh, Sxmo, Lomiri, Phrog, Portuguese,
Asia/Bangkok, 16 apps, the script. The texts were right ("installed during the
first start", the saved network goes to the new system); installed in 4.5
minutes. Found: Debian's camera app is `gnome-snapshot`; the candidate list
now has both names.

**App icons in the chooser** (user's request): for every shell style and
distribution family the first package candidate that exists in the real
package index is chosen, and its icon is taken from the distribution's
AppStream data, scaled to 72×72: 55 icons.

**Result of run 3** (first start, one restart, then a verification script):
the first boot finished; for Plasma it installed the KDE apps (System Settings,
QMLKonsole, Discover, Maliit, KMail, Elisa, Haruna, Koko, Okular, Kasts,
Spacebar, System Monitor) plus Firefox ESR, KeePassXC, Flatpak and an SSH server;
the script ran in the chosen zone; Portuguese locale; sound. Confirmed: the full
name is the new user's; Phrog shows only our four sessions and starts Plasma by
default; Sxmo now closes completely. Found:

- **Lomiri does not work on Debian/Mobian 13** (Mir crashes on this display;
  with the distribution's session a different restart loop). Since then Sxmo and
  Lomiri are offered for the Debian family only, and Lomiri was later fixed
  (build 118, see 2026-11-10 runs).
- **X11 programs under Plasma** crashed creating dumb buffers. Following the old
  working Arch setup (a hint from the user), the `kmsro` override now applies to
  KWin only, Xwayland gets scale 2.75, and later `XWAYLAND_NO_GLAMOR=1` made
  the X11 helpers start (Wayland apps are not affected).

**Build 102** (`009f4228…`): app icons, the KWin-only override, Sxmo clean-up,
the image family guessed from the file name.

**Run 4 — Fedora 45 `qcow2` sent over "USB files" (MTP), build 102.** Sharing the
SD card read-write by touch; the computer copied 597 MB in 84 s. Found: while MTP
runs, the admin SSH is refused (the storage is locked), and the computer saw the
default name of the MTP daemon — it now shows "Galaxy A22 5G recovery" with a
fixed, non-device serial. Install from the file with GNOME + Phosh, Plasma
Mobile, Xfce, sign-in GDM, French, America/Anchorage, 3 apps, the script: set up
inside the recovery in 12 minutes. First start: `running` without failures,
GDM on seat 0 showing only our sessions, Wi-Fi on 5 GHz, SSH. **The user signed
in through GDM**, and logging out returned to GDM.

A **build cache** for phoc and Phrog (user's request): a successful build is kept
and reused for the same wlroots version, survives a clean installation (moved
to `cache` before `userdata` is erased), and archives are checked (only regular
files under the build paths, at most 160 MB).

User permission (2026-11-09): in test systems `sudo` only for reading the current
frame from `/dev/dri` (screenshots) and for a virtual keyboard (`/dev/uinput`).
With these, sign-in screens were tested by touch with screenshots from then on.

- **Fedora, sign-in screens and shells by touch:** GDM with the GNOME on-screen
  keyboard → GNOME (French); GDM's session menu lists exactly GNOME, Phosh,
  Plasma Mobile and Xfce and remembers the last one; Plasma Mobile starts
  through GDM.
- **Xfce (X11) through GDM** did not start: Xorg took the GPU without outputs as
  primary. An `OutputClass` now makes the MediaTek display primary, and Xfce runs
  at scale 2.75.
- My mistake while testing: KEY_POWER in GNOME means "suspend"; the phone went to
  sleep and USB networking did not come back (the BOOT kernel's USB problem after
  sleep). Since then GNOME is woken by touch or a volume key.

**Run 5 — Ubuntu 26.04 from the catalog (Plasma Mobile + GNOME + Sxmo + Lomiri,
SDDM, Polish, Europe/Warsaw), build 104.** Installed with every shell's packages
in 15 minutes. Verified: first boot finished, the script ran, locale and zone,
apps, Wi-Fi 5 GHz, SSH, sound — but **SDDM failed**: Ubuntu's unit refuses to
start unless `/etc/X11/default-display-manager` names it, and the GNOME packages
had installed `gdm3` later. The recovery now writes the chosen manager into that
file and debconf. (Ubuntu was reinstalled with the fix in run 5b.)

**USB after sleep — cause confirmed in the recovery, fixed in the kernel.** In
the recovery an RTC-timed s2idle reproduced it: before sleep `configured`,
afterwards `not attached`, and switching the role `none → device` brought USB
back. The first kernel fix (#118) re-attached only when the session was gone,
but the session register stayed set after sleep; **recovery kernel #120
re-attaches the device role after every sleep** while the gadget is bound.
**Verified in recovery 106:** "re-attaching the device role after system sleep",
`configured`, and the computer pinged the phone about 1 s after waking.

**BOOT kernel #121** (the same fix for the BOOT kernel; BOOT v1r5, published as
v145) was flashed from recovery 106 through Download mode by touch and booted
Ubuntu (`#121`, vendor modules loaded, Wi-Fi, USB, SSH). Recovery 107 then hit
`PLATFORM_PROFILE_MISMATCH` because the sealed platform profile listed BOOT
hashes; the profile was re-sealed (build 108). (Replaced on 2026-11-10 by
judging the BOOT by its contents.)

**Run 5b — Ubuntu 26.04 again (Plasma Mobile + Phosh, GNOME, Xfce, Sxmo, Lomiri,
SDDM, Spanish, America/Buenos Aires).** Installed with six shells in 15 minutes;
the SDDM fix worked, but Ubuntu's default SDDM theme is missing, so SDDM showed
its built-in desktop theme with the restart button drawn over the session list
(my tap restarted the phone). SDDM now gets the Breeze theme and remembers the
user and the main shell (build 109). **Signing in to each session through SDDM**
(password typed with the virtual keyboard): Plasma Mobile ✓, Xfce (X11) ✓ (the
display made primary by our Xorg rule), Phosh ✓, GNOME ✓ (the screen lock
needs GDM — GNOME's own limit), Sxmo ✓; Lomiri ✗ (its keyboard crashed without
its language plugins). Also found: Xfce showed the internal Android partitions
on the desktop; GNOME was not translated (no language packs).

Fixed in build 110: Lomiri's keyboard plugins and a device-info file for the
grid unit; Ubuntu language packs for GNOME and KDE; internal eMMC partitions
hidden from udisks; Sxmo stops its services within 10 s. **Run 5c** (Ubuntu
once more): GNOME in Spanish, Lomiri's keyboard no longer crashes, but Mir
crashed about 6 s later (fixed in build 118). The SDDM screen was still too big:
KWin already scales this panel by 2.7 and `QT_SCALE_FACTOR` multiplies it; a
factor of 0.9 for KWin was measured on the phone to fit.

**Coverage on 2026-11-09 evening:** Debian 13 (catalog; Phosh, Plasma, Sxmo;
Phrog and automatic sign-in), Mobian 13 (catalog and offline from SD; Phosh,
Plasma, Sxmo), Fedora 45 (qcow2 over MTP and catalog; GNOME, Plasma, Phosh, Xfce;
GDM, SDDM), Ubuntu 26.04 (catalog and offline; all six shells; SDDM, greetd),
Arch Linux ARM (catalog; Phosh with its own phoc, Plasma; Phrog built on the
phone). Recovery functions verified on the phone by then: the catalog with
signatures and checksums, clean installation online and offline, installation
from tar, qcow2 and img.xz, USB files (MTP), identity backup and restore,
screenshots, the recovery's time zone, SSH over USB and Wi-Fi, the PIN lock,
restarts into Download and recovery, flashing RECOVERY and BOOT through Download
mode from the recovery and from Linux, USB after sleep (recovery kernel #120).

## 2026-11-09 night: systems side by side (builds 111–116)

**Run 6 — Manjaro Plasma Mobile through "Import a system" (beside Ubuntu).**
"Import a system" adds a slot beside the current Linux; the image came from the
computer over USB files (MTP). A small helper pressed "Stop sharing" when the
copy was complete, so the transfer needed no hands. Import by touch: Plasma
Mobile, keep my account, Italian / Europe/Rome, also Xfce, sign-in LightDM, two
apps, the script; the review said "add a new slot beside the current Linux; BOOT
and the shared /home are kept". Imported in 9 minutes; switched to Manjaro by a
3-second hold; restarted. First start: `running`, no failures, the script ran,
LightDM active. Found and fixed (build 112):

1. LightDM did not see our sessions (`sessions-directory` belongs in
   `[LightDM]`, not `[Seat:*]`) and started the distribution's Plasma Desktop
   session, which failed on the phone. Fixed, and the greeter got scale 2.75.
2. Manjaro's `pinephone-post-install.service` ran at the first start: it grew
   the root filesystem, created a `kde` user with a well-known password in
   `wheel` (SSH with a password was open over Wi-Fi), set a temporary root
   password, forced `en_US`, and enabled SDDM. The recovery now masks these
   PinePhone setup services. On this phone the `kde` user was removed and root
   locked (with the user's permission, only these two commands as root).

Xfce on pacman worked; the internal Android partitions no longer appeared on
the desktop.

**Run 7 — Arch Linux ARM through "Import a system" (third slot).** Phosh, keep my
account, Dutch / Australia/Sydney (summer time), also GNOME, text sign-in, the
camera app, the script.

- The first attempt failed: the `gnome` group pulled packages whose
  dependencies are gone from Arch Linux ARM, and the whole transaction was
  dropped. GNOME on pacman is now its core packages without the group.
- **New: "Remove this system"** (a TWRP-level function): a page with a red
  warning (the root filesystem with its programs, `/etc` and `/var` is erased;
  `/home`, the active system and BOOT stay), a 3-second hold, and the list
  refreshes. **Verified on the phone:** the failed Arch slot was removed;
  Manjaro (active) and Ubuntu stayed.
- The second attempt was ready in 8 minutes, and **the build cache was written**
  (phoc's wlroots fix). After switching and restarting: `running`, Dutch,
  summer time correct, greetd with tuigreet, Phosh 0.57, GNOME 51.
- Text sign-in, fixed in build 115: signing in ended at once because the shell
  list lived in the private `/etc/a22x` (a public copy now lives in
  `/usr/share/a22x/shells`; this also affected automatic sign-in with several
  shells, which had been tested only as root); the boot splash kept moving over
  tuigreet (stopped now); the console font was 8×16 (a large Terminus font is
  installed now).
- **Import from the catalog beside the installed systems:** "Import a system"
  now opens "Add a system" (green "Nothing is erased") with "From the internet"
  and "From a file".
- **Switching back** to the first system failed with `[Errno 2] No such file or
  directory: 'home'`: the slot of a system that had been active has no own
  `/home` (the shared one stays at the top of `userdata`), but the switch checked
  it like a fresh image. Reproduced by a test (clean install → import beside →
  switch there and back) and fixed (build 116). **Verified on the phone:** Arch →
  Ubuntu → restart: Ubuntu `running`, SDDM active, `/home/user` intact, its own
  language kept. Three systems on one `userdata` with one shared `/home`,
  switching in every direction, and removing a slot work.

**Run 8 — Ubuntu 24.04 from the catalog through "Add a system" (fifth slot).**
GNOME + Phosh, text sign-in, Turkish / Europe/Istanbul, calculator and clocks,
the script: downloaded and ready in 9 minutes. Ubuntu 24.04 has no tuigreet, so
greetd's own `agreety` is used. A restart after switching landed in the recovery
again (the preloader started twice); the second attempt worked.

**Ubuntu 24.04 on the phone:** `running`, Turkish locale, Europe/Istanbul; the
public shell list and the splash stop were in place (the build 115 fixes
confirmed). **Text sign-in (agreety) worked** and started Phosh in Turkish.
GNOME through the text sign-in did not: "Session is not a graphical session
(type: tty)" — sessions opened by greetd's text sign-in are `tty` sessions.
The first boot now adds `pam_env` with `XDG_SESSION_TYPE DEFAULT=wayland`
before `pam_systemd` in greetd's PAM stack; and on Debian/Ubuntu the console
font comes from `console-setup`, so the large font is set there too (build 117).

**Run 9 — Mobian 13 Plasma from the catalog through "Add a system" + Sxmo +
Lomiri, SDDM, Portuguese / America/Sao_Paulo.** Ready in 12 minutes; five
systems on one `userdata`, and the Systems list scrolls. This image has no SSH
server, so checks went through the UART console. SDDM with the 0.9 factor was
usable, only the long date and the last button were cut, so the factor became
0.8. SDDM's session menu showed exactly Lomiri, Plasma Mobile and Sxmo. Plasma
spoke Spanish: Plasma keeps its language per user in the shared `/home`, and
it came from Ubuntu — personal settings win over the system language, as they
should with a shared `/home`.

**Lomiri — the cause of the crash found.** Mir crashed 0.17 s after the shell was
ready, in a loop; with the keyboard replaced by `sleep` it crashed the same way.
With grid unit 8 (scale 1) and 16 (scale 2) Lomiri **worked**; with 24 (scale 3)
it crashed: 2408 is not divisible by 3, and Mir and QtMir rounded the logical
height differently (803 vs 802). Ubuntu's Lomiri had worked in the morning
because its device-info returned 8. Fixed with grid unit 16 (build 118).

**Run 10 — postmarketOS v26.06 Plasma Mobile (an image for the PinePhone Pro)
through "Add a system" → "From a file".** systemd, musl, busybox; the image's own
user has UID 10000. Four problems, each fixed in turn:

1. `PLATFORM_METADATA_MISMATCH` on `lib`: the image's `/lib → usr/lib` link is
   owned by `root:1000`; the adapter now gives the image's link the owner from
   the profile in both import paths (builds 119–120). The error page now shows
   the path next to the code.
2. `HOME_GROUP_MISMATCH`: the image's user `user` (10000) was renumbered to the
   shared 1000, but its group of the same name stayed at 10000; the group now
   takes the shared number too (build 121). Then the import passed.
3. Signing in was impossible: the shared account came with a yescrypt hash
   (`$y$`, as Ubuntu's `chpasswd` saved it), and musl without libxcrypt cannot
   read it. Passwords are now stored as SHA-512, which glibc and musl read, and
   "keep my account" into a system without libxcrypt stops before anything is
   saved with "This system cannot read the saved password of this account.
   Choose New user with the same name and enter the password again." (build
   122). **On the phone:** that message appeared; with a new user of the same
   name the import passed and signing in on the UART worked.
4. `plasma-mobile.service` looped with "Failed to determine credentials for user
   '10000'": postmarketOS's generator reads `/etc/default_user`; the adapter now
   writes it (build 123).

**postmarketOS v26.06 + Plasma Mobile works on the A22x:** kernel #121, vendor
Wi-Fi/Bluetooth modules, Wi-Fi connected with the profile from the recovery,
Plasma Mobile running as the user. Known: its `zram` service fails; the image
has no SSH.

**Run 11 — Manjaro Plasma Mobile again: LightDM + Xfce, German / Europe/Vienna.**
The old Manjaro slot was removed with "Remove this system" first. **Build 112's
fixes confirmed:** the PinePhone setup service masked, no `kde` user, the German
locale kept; LightDM's session menu lists exactly Plasma Mobile and Xfce; the
greeter at scale 2 with the on-screen keyboard. Plasma Mobile ✓ and Xfce ✓
through LightDM (Xfce at scale 2, no Android partitions on the desktop).
`pp-prepare-fstab.service` (PinePhone Pro partitions) failed and is masked now.

**Run 12 — Mobian 13 Phosh from a file: Phosh + GNOME + Lomiri, text sign-in,
Ukrainian / Europe/Kyiv.** Text sign-in in Ukrainian without the splash ✓; **GNOME
through the text sign-in ✓** (the session-type fix confirmed); **Lomiri through
the text sign-in ✓** (scale 2, no restarts, keyboard works — the grid-unit fix
confirmed on a fresh installation). Found: the text sign-in font was still
8×16, because `console-setup` sets fonts on tty1–6 and greetd uses VT 7; a
greetd drop-in now runs `setfont` on its VT (build 124).

**Run 13 — Fedora 45 (qcow2 from SD) with Plasma Mobile and SDDM, Czech /
Europe/Prague (build 124).** Eight systems on `userdata` with a shared `/home`.
A restart into Fedora landed in the recovery once more (the fourth time that
night). Measured right after such a start (read-only): the watchdog
non-reset registers, the PMIC RTC spare registers and the `misc`/`para`
partitions hold no boot-mode request, so where LK keeps "the previous mode" is
still unknown. **Fedora:** `running`, Czech, Prague; **SDDM with the 0.8 factor
fits the panel** (full date, power buttons, Breeze) ✓; password → Plasma Mobile ✓.
Then PowerDevil's sleep (like the Sleep button) put the phone into s2idle; the
user woke it with the Power key the next morning, and USB networking answered
8 seconds after waking (BOOT kernel #121).

**Publication (2026-11-10).** linux-a22x: branches `a22x-boot` (kernel #121) and
`a22x-recovery` (kernel #120) on top of kernel #107. a22x-mainline: the patch
series, configs, the v145 BOOT image with its provenance manifest, and the
recovery 0.6.124 sources without logs, binaries and test results; private data
(local paths, the test password) removed; the recovery image is not published
(device firmware, Bluetooth address, SSH key).

## 2026-11-10: images on the phone, ADB, flashing from the recovery, BOOT by contents (builds 125–135)

The user chose TWRP-level functions (over Alpine with musl or NixOS) as the next
direction.

**Images saved on the phone (0.6.125).** Installing without an SD card: an
online installation already downloaded into `userdata` and deleted the image
after unpacking, but "From a file" accepted only external storage, and a clean
installation from the catalog formatted `userdata` before downloading. Now
`/.a22x-images` on `userdata` is a store (reserved: switching never moves it):
`images`, `images-download` (size and SHA-256 or signature checked, written
through a `.part-` file with a small description beside it), `images-delete`; a
stored image is a source for importing or a clean installation; a clean
installation keeps the store, and a clean installation from the catalog
downloads into it before anything is erased. The interface has a third card
"Saved on the phone" and a "Saved images" page (list, free space, download from
the catalog, delete with a 2-second hold, install by touch).

**ADB (0.6.126–0.6.131).** `adbd.py`, in Python without other libraries: the AOSP
protocol (CNXN/AUTH/OPEN/OKAY/WRTE/CLSE), RSA sign-in (a new key only after
"Allow" on the phone screen; the recovery password, when set, is asked as for
SSH), `shell:` with a pty, `exec:`, `sync:` (push/pull/ls/stat), `reboot:`,
`root:`, and `sideload-host:` (TWRP's `adb sideload`): the file goes into the
image store, named by its contents (a ZIP for later Android installation, or a
Linux image); an unknown file is removed and the client gets `FAILFAIL`. Over
the USB network (`adb connect 172.16.42.1:5555`) and over USB through
FunctionFS beside NCM; if the controller cannot bind both functions, NCM goes on
alone.

On the phone:

- build 128: the approval page appeared and "Allow" worked; shell (root),
  push/pull over the USB network. Over USB the computer dropped the link at
  once: header and payload went in one USB transfer, while the computer reads
  the 24-byte header on its own; they are separate transfers now.
- build 130: over USB a 20 MB push stopped: after a transfer of whole 512-byte
  packets the computer sends a zero-length packet, which `adbd` took for a
  disconnect. Sideload over the network was refused because `userdata` was not
  mounted.
- **build 131, verified:** `adb devices` over USB shows `a22x-recovery
  recovery`; 20 MB pushed and pulled identically; `adb sideload test-rom.zip`
  finished (1.00x), the file appeared in "Saved on the phone" and was deleted
  there by touch; `userdata` was mounted for the transfer and unmounted after.
  **Downloading a catalog image into the phone without an SD card:** Ubuntu
  Server 24.04 (206 MiB) in about 40 s, "checked against the catalog".

**Hand-over from a phone system (0.6.127, not yet checked on the phone).** An
Android app or a Linux system may leave `cache:/a22x-recovery/handoff.json` with
Wi-Fi networks, a language and a time zone; at start the recovery reads it once
and deletes it at once (a password never stays on the disk), keeps networks as
WPA keys and takes the language and zone. Setup scripts in
`cache:/a22x-recovery/scripts/` can be chosen like those on an SD card.

**Flashing from the recovery itself — verified.** Recovery 131 was written with
"Install → Flash a partition → recovery" from a file on the microSD card: the
check page recognised "A22x recovery image" with its SHA-256; after a 3-second
hold "recovery written and read back; every byte matches", the old content saved
on the SD card; the partition hash matched; the phone restarted into 131. BOOT
v1r6 (kernel #122) was then flashed the same way from the image store on
`userdata`, and later recovery 132, 133 and 135.

**BOOT kernel #122 (BOOT v1r6, published as v146): USB stays connected through
sleep.** While a computer is attached, the USB controller keeps its session,
pull-up and interrupts during sleep. **Checked on Fedora 45:** PowerDevil's sleep
entered s2idle; the musb interrupt woke the system 0.2 s later; over five minutes
the computer never lost the device or its NCM carrier, and every ping was
answered; the screen stayed off. So with a computer attached the phone does not
stay in deep sleep (as with the vendor driver); on a charger or battery it sleeps
as before. **Brightness in the system settings was checked live** on the same
system: the slider plus "Apply" changed the panel backlight 200 → 52 → 191 of 255.

**Restart refused after flashing BOOT, and the fix.** "Restart into the system"
refused the new BOOT: the normal-boot guard accepted only listed BOOT hashes or a
completed installer transaction. Build 132: a verified write by "Flash a
partition" leaves a record (`cache:/a22x-recovery/flash-records.json`, partition
→ SHA-256), and the guard accepts a changed partition only while it holds
exactly that; old partition copies on `userdata` now go into the image store
instead of the active system's root.

**BOOT judged by its contents (builds 133–135, user's request).** No more lists of
approved BOOT hashes to update for every new kernel, stock Android or GSI
flashing. `bootimage.py` accepts a BOOT when it is a complete boot image for this
phone: header version 0–2, every section inside the partition, a kernel that
unpacks to its end and names its release, and something naming this phone (the
header board `SRPTL28A011`, a device tree for `samsung,a22x` / SM-A226, or the
ramdisk's `ro.product.bootimage.device=a22x`); an MT6833 device tree alone is
enough unless the image names another phone. An AVB footer proves completeness
when it matches; a stale one (Magisk) is only noted. DTBO must be a valid DT
table, vbmeta a valid AVB image. Checked against real images: the stock BOOT
(Android 12, kernel 4.14), a Magisk-patched BOOT, v145/v146, the stock and our
recovery, stock dtbo and vbmeta; a BOOT with 4 MB zeroed inside the kernel is
refused (`KERNEL_DAMAGED`). Used by the normal-boot guard, the Linux installer
(any complete Linux BOOT whose kernel release matches the platform modules,
`7.2.0-a22x+`), Linux backups and "Flash a partition" (an image for another
phone or a damaged one is refused; the check page shows what is inside, for
example "Linux 7.2.0-a22x+ / Samsung Galaxy A22 5G (SM-A226B)").

**Verified on the phone (recovery 133/135):** a test BOOT that is on no list
(v146 with one extra field changed, a stale AVB footer) was flashed through
"Flash a partition"; the guard accepted it through the flash record and, with
the record removed, by its contents; Fedora started with it. The check page
of recovery 135 showed the green line with the kernel and phone, and v146 was
written back. 528 Python tests, interface self-test 95, display 53 workflows /
1177 checks, systems 8 / 145 pass.

**Build 136** (`4eea5ee3…`, the published source): sample Wi-Fi names and
addresses in the interface's preview pages and a comment in the first-boot
script were replaced with neutral examples, so the published tree carries no
private network data.

## 2026-11-10 evening: the Android installer app and themed splash screens (build 137)

**The installer app (`apk/`).** Built without the Android SDK (aapt2, d8 and apksig
from Google's Maven repository, API stubs from Maven Central, framework resources
from Debian), signed with v2 and v3 schemes. It checks compatibility from the
Samsung bootloader (its version starts with the model and no ROM replaces it), the
model, the code name and the chip, never from a serial number; a computer-side test
covers stock Android, a custom ROM, a GSI and the SM-A226BR (all pass), and another
Samsung renamed as an A22, an A32 and another MT6833 phone (all refused). It installs
the recovery, the Linux BOOT or both: the old partition is copied first, and the
partition is read back after writing; nothing more is written after a failure. Its
root scripts were run in the recovery against loop devices in RAM: a wrong checksum
and an image larger than the partition were refused before writing, a real write kept
the old content and read back correctly, and the hand-over was written to a test
`cache` filesystem (0600, valid JSON, the private mount removed). The app also hands
Wi-Fi networks typed by the user, the language, the time zone and the clock to the
recovery, keeps them in sync when Android's language, zone or time change, saves
setup scripts, and restarts into the recovery. Graphite, Daylight and Aurora change
its colours, its launcher icon (three launcher entries, one enabled) and its Android
12+ start screen. It has not run on Android yet: the phone runs Linux.

**Splash screens in the chosen theme (build 137, recovery kernel #123).** The
kernel's splash now has the logo of all three themes. `/init` reads the saved theme
from `cache` at the very start (read-only, without replaying the journal) and passes
it to the kernel, which waits up to 400 ms for it before the first picture; the
interface starts in the same theme, and the screen before a restart or power-off
follows the theme too (it used to be dark always). **On the phone:** the kernel
logged "boot splash: daylight theme" 1.8 s after boot, and a screenshot of
"Restarting recovery" shows the Daylight theme. Recovery 137 was flashed from
recovery 136 with "Flash a partition".

## 2026-11-10 night: installations prepared in the Android app, Android partition backups (builds 138 to 141)

**An installation prepared in the app.** The app's new card chooses a system from the
recovery's own catalog (shipped inside the app), the main shell and more shells, the
sign-in, apps, the system language and time zone, the account and a setup script saved
in `cache`, and warns in red that userdata is erased. It writes
`cache:/a22x-recovery/install-plan.json` and can restart into the recovery. The account's
password leaves the app only as a SHA-512 crypt hash (the app's Java implementation
matches `openssl passwd -6`, including non-ASCII and passwords over 64 bytes);
`distro.py` takes it with `--password-hash-file` and the first start sets it with
`chpasswd -e`. In the recovery, `install_plan.py` checks the plan strictly (only known
catalog images, shells, sign-ins, apps and languages; unknown fields, a plain password,
an MD5 hash or a path in the script name make it unusable), the plan page opens by
itself once after startup, and the Install page shows it first, highlighted. "Review
installation" mounts userdata with write access and `cache` read-only for the script,
fills the usual install pages with the plan and opens their usual review, where Install
is held as for any other installation; the plan is deleted after that installation, or
when it is discarded with a hold.

**A second guard in the app's flash script.** Besides the compatibility check on the
page, the root script itself now stops with `NOT_THIS_PHONE` before reading or writing
anything unless the partition has exactly the A22x size (recovery 40 MiB, boot 32 MiB)
and the platform properties name MT6833 (checked with a stand-in `getprop` reporting a
Qualcomm phone: it stopped first). The Wi-Fi card can fill in the name of the network
the phone is on; passwords saved in Android are not read, the password is typed once.

**Android partition backups.** "Phone partitions" in Backup now has two kinds: the
phone's identity (EFS, IMEI, NV data) and the partitions of an Android installation
(BOOT, super, dtbo, vbmeta, prism, optics), any of them chosen. Zero blocks become holes
in the copies, so a mostly empty `super` takes only its data; userdata is not accepted
as the destination because a factory reset of Android erases it; such a copy may go
back to any phone with the same partition layout, after the partitions it replaces are
saved.

**Fixes from the run on Ubuntu 24.04 with Phosh and GNOME.** The "Switch to ..."
launchers were hidden in the wrong shells: Phosh calls itself `Phosh:GNOME`, so
`NotShowIn=GNOME` hid "Switch to GNOME" in Phosh and "Switch to Phosh" in GNOME. Now the
running shell's own launcher is hidden for the user at each start, and the launchers
show in Phosh's phone app grid. After signing out, greetd 0.9 opens its next session
through the `greetd-greeter` PAM service, which did not mark it as a Wayland session,
so GNOME Shell refused to start twice (the switcher then went back to Phosh, as
designed); that service now gets the same line. A system added beside another one now
knows the shared account's name for automatic sign-in and for its setup script.

**On the phone (build 138).** A plan written by the app's own code was placed in `cache`
(beside the existing systems, so nothing was erased). After unlocking, the recovery opened
the plan by itself; "Review installation" led to the usual review, the installation of
Ubuntu 24.04 from the catalog took about seven minutes, and the plan was gone from `cache`
afterwards. The new system took the account with the password that had arrived only as a
hash (SSH and Phosh's keypad accepted it); "Switch to GNOME" was in Phosh's normal app grid
and started GNOME Shell, and "Switch to Phosh" in GNOME's grid brought Phosh back.

**A restart that comes back is tried once more (builds 139 to 141).** The first restart into the
new system came back to the recovery again (the sixth time: the preloader started twice,
`sram header is not match, format all`, and the restart markers were lost). Before a
restart into the system or Download, `/init` now notes the target in
`cache:/a22x-recovery/restart-intent.json` together with the mount count of userdata's
ext4 superblock. Every Linux system started from userdata mounts it with write access,
which counts up; so a recovery that starts less than 60 seconds later (such a return takes
about 30) with the same count restarts once more without the interface, and only once, so
a system that really cannot start does not make it loop. A changed count means the system
did start (also after flashing from Download), and nothing is repeated. Tried on the phone
by writing an intent and restarting the recovery: the new recovery decided to restart after
5.4 seconds, but its first check met the Wi-Fi join that the recovery starts in the
background, which holds the operation lock; the join now waits until after such a restart,
and the checks before a restart wait up to 15 seconds for a lock held only for a moment.

**The app's theme change (0.6.138-r2).** Changing the theme closed the app on a OnePlus:
it disabled the launcher entry it was running from and restarted through another one.
The page is now built again in place and the new theme grows from the tapped button as a
circle, like in the recovery; the launcher entry (the icon) changes only when the app is
left and no root work runs.

## 2026-11-10 late night: flashing from the recovery itself, logical partitions, the app in the recovery's look (builds 142 and 143, app 0.6.142-r2)

**The recovery flashed from the recovery.** Build 142 went onto the phone without Download
Mode: the image was copied over ADB into the image store on userdata, chosen on
"Flash a partition" → `recovery`, reviewed and written with a hold. The next start ran the
new recovery (its partition read back with the image's hash, kernel #123), and the restart
check found nothing to repeat.

**Room for the shells of a new system.** A Debian 13 installation with two shells ran out
of space during its first start: the temporary 4 GiB swap file took the room the packages
needed. The swap now takes at most the free space minus 5 GiB (none below 512 MiB), a
partial file is removed, and the room the chosen shells and apps need (Phosh 1.5 GiB,
GNOME 2 GiB, Plasma 2.5 GiB, Xfce 1 GiB, Sxmo 0.5 GiB, Lomiri 2 GiB, 192 MiB per app) is
added to the space an installation requires before it starts.

**Logical partitions inside super (build 143).** `lp_metadata.py` reads and writes the
metadata Android's liblp keeps at the start of `super`: the geometry, the primary and backup
copy of every metadata slot, their SHA-256 checksums, headers 10.0 and 10.2. On the phone it
read the real layout without writing: `system` 4361 MiB, `product` 1333 MiB, `vendor`
1043 MiB and `odm` 4 MiB in the group `main` (6741 of 6900 MiB used), 162 MiB free.
"Flash a partition" gets a `super` page listing the logical partitions. An image (sparse
too) is written into one of them; the partition first grows or shrinks within its group and
the free space, so a larger GSI fits into `system`; every copy of the metadata is rewritten
and read back; only the image's own bytes count against the room and the rest of the
partition is zeroed. Build 143 also tells why a first start failed, from the new system's
`/var/log/a22x-firstboot.log`. It is built and tested on the computer, not yet on the phone.

**The app in the recovery's look (0.6.142-r2).** The Android app now looks like the
recovery: a home page of tiles (the "Install" hero, "Recovery and BOOT", "Compatibility",
"Wi-Fi", "Language and time", and small cells for scripts, theme and restart), each opening
a page of its own with a round back button and the recovery's zoom-and-fade change instead
of one long scrolling page, tiles that sink while pressed, the recovery's own icons
(rendered by the interface on an icon sheet and exported by `ui/tools/export_icons.py`),
buttons that act only after a hold, and short messages at the bottom. Preparing an
installation follows the recovery's own install pages: system, install options, shells and
sign-in, network and apps, review. The app still does not install systems, flash partitions
or make backups: those stay in the recovery. Every text is translated into the recovery's
languages (Ukrainian, German, French, Polish, Spanish besides English), with the shells,
sign-ins and apps named as the recovery's translations name them, and the clock and date on
the home page follow the phone's language, date order, time zone and 12/24-hour setting.

**The recovery's backdrop and surfaces in the app (0.6.142-r4).** The app now paints what
the recovery paints. The Daylight and Aurora backdrops, the same 135 × 300 images exported
from the recovery's theme assets by `ui/tools/export_backdrops.py`, are stretched over the
whole window and behind Android's transparent bars by a Java port of the recovery's
`canvas_backdrop()`: bilinear, with the same 8 × 8 ordered dither. A build-time test runs the
recovery's C function next to it, and all 2,592,000 pixels of each backdrop match. Cards are
drawn like the recovery's surfaces: a soft shadow in the theme's colour and strength, the
lighter edge under a fill that lets the backdrop through, a dark veil while pressed. The
"Install" hero runs top to bottom in the theme's two colours, with the recovery's two soft
discs and a shadow in its lower colour, and the theme cards show the real backdrops. The home
page adapts to the screen: tiles keep a minimum height and share any extra room, so the page
fits a 360 × 800 dp screen, a OnePlus 15 and a 1.3 font scale without scrolling. The clock and
the "Mainline Recovery" line left the top of the page. Every page was checked in an Android 14
emulator, in three themes and three languages.

## 2026-11-10 evening, on the phone: images over adb sideload, logical partitions written and restored (builds 144 to 147)

**Flashed from the recovery, again.** Build 144 went onto the phone the way a person would do
it: Files → userdata → allow writing, the image copied over ADB into the image store, then
Install → Flash a partition → recovery, the review and a hold. The next start ran it. Build 146
then came over `adb sideload`, which now keeps images of the phone's own partitions too (boot,
recovery, vbmeta, dtbo, sparse and super images, named by what they are, as TWRP's Install
Image takes them), and was flashed by 144 itself.

**A computer's session named as such.** An SSH or SFTP session holds the recovery's lease, so
the interface cannot change storage while it is open. The message said "stop USB files";
it now says that a computer is connected over SSH or SFTP and that closing that session
lets the operation run.

**Logical partitions, tested on the real super.** Samsung's `odm` holds 4,349,952 bytes, not
a whole MiB, and its own image was shown "growing" to 5 MiB; an image that fits now keeps the
partition as it is. Partitions inside super now get what the others have: a small one's
content is saved before it is written, and the very same image is recognised, with nothing
to flash. On the phone, a copy of odm with one changed byte was sent over `adb sideload` and
flashed into super; the old content was saved to the image store, the partition read back, and
super's metadata stayed byte for byte as before. The saved copy then went back the same way,
and choosing it once more showed that the image is already on the partition.

**Smaller fixes found on the way.** `adb sideload` failed with nothing but "failed" on the
computer when userdata was open read-only after choosing an image; it is now opened for
writing during the transfer and read-only again after it. The result page offers "Back to
recovery" only where it leads home, and "Back to files" only from the file pages themselves.

**Wipe, as in TWRP (build 148).** The Install page gets a fourth card, Wipe. Cache empties
everything on the cache volume except the recovery's own folder (its settings and keys, saved
Wi-Fi networks, plans from the Android app and setup scripts) and lost+found; a review lists
what stays and a hold starts it. A whole volume leads to Files, where each volume is erased
with a hold. Files are removed by descriptor, so a symbolic link is removed and never
followed, and the volume is left as it was found. On the phone it removed eight leftovers of
Android (update and backup folders, Magisk's log, the stock recovery's logs, 8.1 MiB) and kept
the recovery's folder file for file.

**Removing a system the switch refuses (build 149).** A system set up for another kernel and
firmware is refused by a switch, as it should be, but the only Remove button sat on the switch
page, so exactly such an old system could not be removed. The refusal now offers "Remove this
system", the removal page names the system tapped in the list, and the message says what to
do: install it again, or remove it. Two old test systems were removed this way on the phone.

**Run 16: Debian 13 with Phosh and GNOME and a text login.** Added beside the other systems
from the catalog (the review counted 6.6 of 7.7 GiB needed with the new estimate for the
shells), set up inside the recovery in about nine minutes, switched to and started: no failed
units, Ukrainian, Kyiv time. The text login on VT7 uses Terminus 32x16 and its Cyrillic reads
well; signing in with a virtual keyboard started Phosh, "Switch to GNOME" in Phosh's grid led
back to the login and then to GNOME Shell 48, and the running shell's launcher hides itself.
One cosmetic issue stays: the password prompt comes from Linux-PAM inside greetd, which does
not set a locale, so it reads "Password:" in English.

**A restart after flashing (build 150).** After a flash of the recovery or BOOT, the result
page offers the restart, as TWRP's Reboot System does, through the same hold as the Power page.
