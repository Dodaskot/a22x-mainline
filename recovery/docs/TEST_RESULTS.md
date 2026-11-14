# Test results

Recovery **1.0.2** and **1.0.1**, recovery kernel `7.2.0-a22x-recovery+ #139` (branch `a22x-recovery` of
[linux-a22x](https://github.com/Dodaskot/linux-a22x), `cf097a32`), BOOT v147 (kernel `#138`),
2026-11-14, on a 64 GB SM-A226B. Earlier releases follow below.

Two kinds of evidence are kept apart here. The first is automatic: tests that run on a computer
against fixtures, with no phone attached. The second is what was done on the phone itself
(SM-A226B). Something that passed only on the computer is never listed as working on the phone.

## 1.0.2 (2026-11-14)

Recovery **1.0.2** and its app, on the same kernel (`#139`) and BOOT (v147).

| What | Where | Result |
|---|---|---|
| Helpers (Python) `tests/`, with `tests/test_apk_misc.py`: the app's Java code changes the recovery's records, the recovery reads them, and the other way round | computer | 913 tests pass |
| Backup backend `backend/tests/` | computer | 110 tests pass |
| App build: 68 checks, `MiscRecordsTest` (24, WPA key against IEEE 802.11i), signatures v2 and v3 | computer | pass |
| 1.0.2 written to RECOVERY through Flash, read back, started: settings from `misc`, interface, Wi-Fi | phone | works |
| A hand-over file older than a password saved in the recovery: the password kept; a newer file: replaced (test network, removed after) | phone | works |
| The app's misc scripts in the recovery against the real misc: the area read as it is; a slot written with its own bytes and read back | phone | identical; misc unchanged (same SHA-256) |

Not proven yet: the 1.0.2 app on Android (the test phone has Linux on userdata).

## Automatic tests (computer, fixtures), 1.0.1

| Suite | Result | Size |
|---|---|---|
| Helpers (Python): `tests/` | pass | 909 tests (4 skipped: tools missing on the computer) |
| Backup backend: `backend/tests/` | pass | 110 tests |
| Interface self-tests: `make test` | pass | 106 self-tests |
| 23 interface suites (`ui/Makefile`: `integration` … `linux-integration`) | pass | run on the settings-in-`misc` change; the interface has not changed since |
| Installer app (`apk/build.sh`) | pass | 68 checks; signature v2 and v3 verified |

New in 1.0.1:

- `tests/test_misc_store.py`: the settings store in `misc`. Every write is stopped at every step
  (a crash after each write and each sync, and writes cut after 0, 1, 511, 512 and 1543 bytes)
  after histories of 0 to 3 saves: a read gives the record before or the one being written,
  never none and never damaged. Damage to either copy, a copy that cannot be read, another
  program's bytes in the area, and nothing outside a record's 64 KiB ever written.
- `tests/test_settings_misc.py`: settings in `misc`, the one-time copy from the older file on
  cache or userdata, its removal, the reset of damaged settings, the command line.
- `tests/test_settings_properties.py`: the older store (read once, for the copy) walked over
  every state of both volumes, with a write stopped at every step.
- Code review: the password and settings code went through 26 rounds of review (review-12 to
  review-37); every finding was fixed, or written down as a bound in
  [SETTINGS_API.md](../SETTINGS_API.md) and the changelog.

## On the phone, 1.0.1 (2026-11-14)

| What | Build | Result |
|---|---|---|
| Who writes the last 128 KiB of `misc`: Samsung kernel source, strings of the stock LK, stock Android (`init`, `uncrypt`, `libbootloader_message`, `mtectrl`, MediaTek `libsysenv`), backups of `misc` from 2026-10-07 and the partition now | stock A226B firmware | nobody: Android uses the first 64 KiB, the LK its first 2 KiB, sysenv writes `para`; beyond 64 KiB only zeros |
| The settings store run from RAM before a build: 16 settings and 5 saved networks copied from cache to `misc`, saved, read back, then cleared | 1.0.14 | both copies whole; the first 384 KiB of `misc` unchanged byte for byte |
| First start of a build with the store in `misc` (written through the recovery's own Flash, read back) | 1.0.15 dev | settings (297 bytes) and Wi-Fi list (723 bytes) copied at the start; the interface opened; Wi-Fi joined from the list in `misc`; a save left the newest copy whole |
| Next start | 1.0.16 dev | the boot splash took its theme from `misc`; the older settings file on cache removed; a save left both copies equal |
| The older store looked at on both volumes, flag written | 1.0.16 + RAM | first read 1.47 s, later reads 0.73 s |
| The public image (no key, no capsule, no phone's identity inside) written to RECOVERY, read back, started | **1.0.1** | settings read from `misc`; interface; Wi-Fi joined; adb with a key approved before; Updates shows 1.0.1; the screenshots of the README taken with the screenshot keys |

### Not proven on the phone yet

- A power cut in the middle of a write to `misc`: proven only by the tests above.
- Setting and clearing a password with the store in `misc` on the phone (the test phone has no
  password set): proven by tests only.
- The behaviour on a media error in `misc`, and the 120 s limit of the cache check at the start:
  tests only.
- The installer app 1.0.1 on a phone: built and checked only (68 checks, signatures v2 and v3).
  The app of 0.6.206 was run on the phone (see 0.6.206 below).

### Reported by others

- **LineageOS 20** for A226B by **Loftenth**: tested (installed and started from this recovery).
- **BakaSU** by **Kazaf 132**: started successfully.

### Not available

- **Decrypting Android's userdata** is not available yet: see
  [ANDROID_CRYPTO_STATUS.md](ANDROID_CRYPTO_STATUS.md).

## Earlier: 1.0.0

Recovery **1.0.0**, recovery kernel `7.2.0-a22x-recovery+ #137`, BOOT v147 (kernel `#138`), 2026-11-13,
on a 64 GB SM-A226B.

### Automatic tests (computer, fixtures), 1.0.0

One full run of every suite on the sources of 1.0.0: **all 25 passed**.

| Suite | Result | Size |
|---|---|---|
| Helpers (Python): `tests/` | pass | 797 tests |
| Backup backend: `backend/tests/` | pass | 109 tests |
| Interface self-tests: `make test` | pass | 105 self-tests |
| 22 interface suites (`ui/Makefile`: `integration` … `catalog-chain-test`) | pass | as for 0.6.206, plus the Linux data and password pages |
| `ui: make linux-integration` | pass | 4 workflows; with the x86-64 partclone of the qualification toolchain on the `PATH` |

### On the phone, 0.6.207 to 1.0.0 (2026-11-12 and 2026-11-13)

Done by touch on the phone unless said otherwise.

| What | Build | Result |
|---|---|---|
| Linux snapshot without a qualification capsule: userdata erased in Files, an Ubuntu 26.04 snapshot restored | 234 | restored and verified; Ubuntu started: no failed service, Phosh, network, `/home` with its 402 files |
| A new snapshot made; userdata erased by the snapshot's own wipe; the snapshot restored | 235 | each step verified; Ubuntu started with the same state |
| A card mounted in Files chosen as the backup store | 235 | mounted for copies; before, `SD_ALREADY_MOUNTED_ELSEWHERE` |
| Restore lists the snapshots after the card is mounted on the Storage page | 235 | works; before, the list stayed empty |
| Copy of five Android partitions with a password | 236 | saved encrypted (`Salted__`, opens with openssl on a computer); after a restart shown closed; a wrong password refused; verified; restored: a damaged `vbmeta_system` written back, read back, its hash as before |
| Archive of `/home/user` in Files with the password, then extracted | 236 | 402 files, identical (one hash over all of them), owner and modes kept |
| Linux data with the password, restored by hand on Ubuntu: `restore.sh` taken out with openssl, the password (with a space) typed at its prompt | 236 | accounts kept, homes, 8 settings, 79 of 79 packages; a deleted file came back with its owner |
| An archive that wrote through a hard link out of its folder | 236 | refused (`ARCHIVE_UNSAFE`); the file outside unchanged |
| 1100 folders packed into an archive under a limit of 256 open files | 236 | packed |
| Linux data offers a card that is only inserted as the place to save to | 236 | offered, and let go afterwards |
| The Brotli decoder with bytes after the end of the stream | 236 | refused |
| A USB stick (OTG) as the backup store: a copy of the recovery made on it; Safely remove (done by the owner, read from the recovery's log) | 236 | copy saved and verified (`/A22xBackups/recovery-20261113-204907-359` on the stick); eject done. USB files cannot share the stick itself: the one port is then a host |
| Start: a cut write resumed first, the restart check next, cache tidied after it | 237 | in that order; tidying reads the copies' descriptions |
| vbmeta read for root through the checked loop | 237 | read: verification off on this phone |
| The sound profile entry in the image's platform files | 237 | a link to the phone's UCM folder |
| A screenshot while nothing holds the operation lock | 237 | saved on the card |
| The public image 1.0.0: version, Linux snapshots without a capsule, the update check | 1.0.0 | shows 1.0.0; no SSH key, no capsule; snapshots: backup, restore and wipe offered; v0.6.206 on GitHub is not offered as an update |
| Update from the published 0.6.206 to the published 1.0.0 in Settings > Updates | 0.6.206 → 1.0.0 | 1.0.0 found and downloaded, SHA-256 as GitHub states it, written and read back; after a restart 1.0.0 runs and offers nothing older. With cache full of five saved BOOTs the download first stopped with CACHE_FULL (0.6.206 calls it "no room to keep this BOOT"); one saved BOOT removed, then it went through |
| Stock firmware A226BXXSBDZC3 installed from its ZIP on the card | 218 | unpacked, MD5-checked, written and read back; stock Android started |
| LineageOS 20 for A226B installed by its own `update-binary` | 221 | installed; started after the offered reset of Android's data |
| An AnyKernel3 kernel ZIP (BakaSU + SUSFS) | 220 | installed; Android started after the data reset |
| The KKRT One UI port (block OTA): its installer crashed; the recovery wrote its images itself | 217 | written and read back |
| Linux data: Arch Linux ARM's homes, accounts, settings and packages restored into Ubuntu 26.04 | 227–230 | 372 files identical, owners mapped by name; apt installed what Ubuntu has |
| Several files at once in Files; a hold acts as soon as it is full | 222 | works |
| Check and repair of F2FS and FAT | 231 | on scratch filesystems on the phone |

#### Not proven on the phone yet

- A **128 GB** SM-A226B: the sizes follow Samsung's PIT and are tested on the computer only.
- Check and repair on a real F2FS or FAT volume (done on scratch filesystems on the phone only).
- The installer app 1.0.0 on a phone (its build checks passed: signatures v2/v3, the update order,
  the scripts). The app of 0.6.206 was run on the phone (see 0.6.206 below).

## Earlier: 0.6.206

### Automatic tests (computer, fixtures), 0.6.206

One full run of every suite on the sources of 0.6.206: **24 of 24 suites passed**.

| Suite | Result | Size |
|---|---|---|
| Helpers (Python): `tests/` | pass | 741 tests |
| Backup backend: `backend/tests/` | pass | 106 tests |
| Interface self-tests: `make test` | pass | 103 self-tests |
| `ui: make integration` | pass | 2 profiles |
| `ui: make files-integration` | pass | 3 workflows |
| `ui: make v03-integration` | pass | 10 workflows |
| `ui: make interaction-integration` | pass | 7 workflows |
| `ui: make motion-integration` | pass | 4 workflows |
| `ui: make usb-ui-integration` | pass | 5 workflows |
| `ui: make terminal-ui-integration` | pass | 13 workflows, 568 checks |
| `ui: make display-ui-integration` | pass | 53 workflows, 1208 checks |
| `ui: make locale-ui-test` | pass | 27 checks, 312 time zones |
| `ui: make shells-ui-test` | pass | 32 checks |
| `ui: make charger-ui-test` | pass |  |
| `ui: make screenshot-test` | pass |  |
| `ui: make identity-ui-integration` | pass | 42 checks |
| `ui: make flash-chain-test` | pass |  |
| `ui: make touch-input-test` | pass | 6 workflows, 88 checks |
| `ui: make systems-ui-integration` | pass | 10 workflows, 194 checks |
| `ui: make install-ui-integration` | pass | 28 workflows, 764 checks |
| `ui: make install-backend-integration` | pass | 7 workflows, 123 checks |
| `ui: make card-ui-integration` | pass |  |
| `ui: nav-ui-test` | pass |  |
| `ui: catalog-chain-test` | pass | 10 servers |

How to run them: see [Tests](../README.md#tests). The Python suites need a user namespace
(`unshare -r`) for the file-ownership tests; no root is used. The interface suites drive the
real interface binary through its own input decoder against fixture devices.

The Android app has its own check (`apk/build.sh` runs it at every build): 68 checks of the
scripts that hand images and settings to the recovery, run against test devices.

### On the phone, 2026-11-11 and 2026-11-12

Done by touch on the phone unless said otherwise.

| What | Build | Result |
|---|---|---|
| Screenshot with Volume down + Power; flashlight on the home page | 176 | works |
| Flashlight brightness: every step, the bar, kept over a restart, off before a restart | 177 | works |
| Copy of Android with its data (10 partitions, 57.3 GiB of partitions into a 4.3 GiB archive) | 178 | created and verified |
| Clean installation of Debian 13 over BOOT v146: the installation downloads BOOT v147 from the mainline release before erasing, and writes it | 196 | BOOT v147 written and started (kernel #138); the system noted its start and the list of systems reads it; charge started from the recovery's 95 % |
| A full cache: a test flash of the recovery needed room to keep the old one; the recovery gave up the kept Linux BOOTs v144 and v146 (published, can be downloaded again) and kept v147, the stock and the rooted BOOT | 197 | old recovery kept on cache, 29 → 54 MiB free afterwards |
| Wi-Fi in that system (no NetworkManager, set up "as in the image") | 196, 197 | 196: joined, IPv6 only (the DHCP script was missing); 197: joined with IPv4: internet and name lookup work |
| Clean installation of Debian 13 from the catalog, new account user, the Linux BOOT written by the installation; then BOOT v147 flashed by the recovery's flash helper | 195 | installed, running, no failed service, USB networking and SSH; started with kernel #138 |
| Clean installation of Manjaro ARM Phosh from the catalog, the Linux BOOT written by the installation | 185 | installed; BOOT is release v146; started on kernel `#122`, no failed service, Wi-Fi, time zone and language as chosen |
| The same image added beside it, switched to and started | 187 | Phosh starts by itself through the image's `phosh.service` (on 185 the phone stopped at a text login) |
| Phosh under touches in that image (sent to the touchscreen's input node): the swipe to unlock, the password keypad, Unlock | 188, 191 | answers; Android's first-setup program opens after the unlock |
| Debian 13 added beside the others with Phosh and Plasma Mobile and the Phrog sign-in | 189 | no failed service; Phrog lists exactly the two shells; signing in by touch starts Phosh (GL renderer Mali-G57), its buttons answer, the Switch shell launcher returns to Phrog; Plasma Mobile starts. Its keyboard failed for want of Qt 5's Wayland plugin: fixed in 191, not checked on the phone since |
| Phosh on the phone's GPU in that image | 188 | phoc no longer falls back to software drawing (the journal has no "Software rendering detected" and no "too slow"); with the name the installed Mesa uses, a headless phoc reports "GL renderer: Mali-G57 (Panfrost)"; the lock screen is drawn whole |
| Image for any phone: no SSH key inside, Wi-Fi and adb work | 179 | works |
| Android's reset request: page opens, Not now, Forget | 179 | works (request simulated) |
| Backup page offers the Android copy first on a phone with Android's data | 180 | works |
| Verify the copy with data: every checksum of 10 partitions | 180, 181 | matches, about 9 minutes |
| Several ZIPs in one go: two harmless ZIPs ran in order | 181 | works |
| A queue of three with a failing ZIP in the middle, a restart asked for afterwards | 191 | stops: the first done, the second failed, the third not run; nothing changed, no restart |
| A queue of two good ZIPs with a restart asked for afterwards | 191 | both done, ten seconds counted down, the phone restarted into its Linux system |
| Recovery kernel #124 to #130: starts, Wi-Fi, USB, touch, display | 182 to 192 | works |
| Charge shown: a stale 96 % kept by Android refused at 3.97 V; three restarts began at 66 to 68 % | 184, 185 | works |
| Charge kept in the PMIC for Android and the next start | 185 to 193 | **wrong register**: builds up to 0.6.193 used RTC_AL_HOU, which takes no writes; 0.6.192 and 0.6.193 concluded that the RTC took none at all, a mistake of the test (the alarm it set was disabled, and the RTC driver does not write a disabled alarm) |
| Charge kept in RTC_AL_MTH, where Android's gauge keeps it (recovery kernel #137, BOOT kernel #138) | 194, 195 | **works between the recovery and Linux**: the recovery kept 94 %, Debian 13 started from the stored 94 %, went to 93 % under 0.8 A and kept it, the recovery started from the stored 93 %; a test value written once survived a reload of the RTC's registers and a restart |
| "Android did not start": the page opened by itself after Google's Android 15 restarted for ever, with the kernel's line "Android V requires kernel 4.19" | 183 | works; read once, not shown again |
| A damaged recovery image is refused before anything is written | 183 | refused (IMAGE_DAMAGED); the partition kept its content |
| Flash the recovery from the recovery, read back byte for byte | 182 to 193 | works |
| ROM from a file: Google's Android 15 ZIP chosen on the SD card | 192, 193 | recognised as an Android image with its build; since 193 the review warns that it needs kernel 4.19 (not installed) |
| Restore Android with its data from the copy (10 partitions, 57.3 GiB) over four Linux systems | 191 | boot, super, metadata and userdata written and read back, six partitions already the same; 27 minutes; the Linux BOOT that was there is kept on cache |
| Download an Android image from the catalog to the SD card (crDroid 12.12 with microG, 1.0 GiB) and install it | 191 | downloaded and checked; system written into super and read back, userdata formatted for Android. Not started yet |
| Partitions of super open read-only in Files: system mounted from Files, its build.prop read; super held while one is open; an installation that needs super closes them | 199 | works |
| Cache checked at every start (e2fsck -p): it had been mounted 187 times without a check | 198 | works |
| Access gate without the copy in RAM reads the disk; with storage busy it stays closed | 200 | works |
| The stock BOOT, an APatch BOOT and Linux BOOTs pass the SM-A226B check | 200 | works |
| A write cut short: its journal found at the next start and the write recognised as complete | 200 | works |
| adb over USB comes back after the computer restarts its adb server | 200 | works |
| ZIP installer room probed: no device nodes, no network, rights dropped, the recovery's root out of reach | 201 | works; devtmpfs can still be mounted, which the protected-partition check catches |
| Settings > Updates: GitHub checked, the published recovery downloaded and checked | 202 | works |
| Evolution X 11.11 (Treble GSI) from the catalog: downloaded, installed, started | 204 | starts |
| Root with Magisk on Evolution X, data kept across restarts | 206 | works after the metadata fix; with 204 Android asked for a reset after its first restart |
| Full copy of Evolution X with its data; then Arch Linux ARM with Plasma Mobile and automatic sign-in installed over it; then the copy restored (done by the owner) | 206 | Arch Linux: started the first time. After the restore Evolution X started with the same data: setup done, Magisk root (31.1) and both apps from before the copy, no reset asked |
| Installer app: updates page, Android's BOOT kept on cache by itself | 205 | works |
| Installer app on Evolution X with Magisk: updated itself from 0.6.205 to 0.6.206 from GitHub (SHA-256 checked, `pm install` through Magisk); then installed recovery 0.6.206 from the release, the old recovery saved first | 206 | works: after the restart the recovery partition held exactly the published image |

#### Android images tried on this phone

| Image | Result |
|---|---|
| crDroid 12.12 (Treble GSI), without Google apps | starts |
| AlphaDroid 4.6 (Treble GSI), with Google apps | starts |
| crDroid 12.12 (Treble GSI), with microG | installed from the catalog on 2026-11-12; not started |
| LineageOS 22.2 light, without Google apps | does not start: restarts |
| Google's Android 15 GSI | does not start: needs kernel 4.19, the phone's Android kernel is 4.14; removed from the catalog |
| Samsung firmware restored from the recovery's own copy | starts |

#### Not proven on the phone then

- Phosh under the owner's own finger in the Manjaro image (touches were sent to the touchscreen's input node from a computer).
- crDroid 12.12 with microG, installed on 2026-11-12, has not been started (Evolution X, installed the
  same day, starts: see above).

#### Found while testing, 2026-11-12

- **A pushed file lost 16 MiB once.** A recovery image sent with `adb push` hashed right from
  memory, and read back with 16 MiB of zeros after the volume was mounted again. It did not
  happen again in two further tries. The recovery refused the image, and its adb service now
  flushes a pushed file to the storage before it says "pushed".
- **Reading every PMIC register at once stops the voltage readings** until the next restart
  (a debugging step, not something the recovery does). While they time out the interface
  pauses for a moment every few seconds.
