# Test results

Recovery **0.6.188**, recovery kernel `7.2.0-a22x-recovery+ #127`, 2026-11-12.

Two kinds of evidence are kept apart here. The first is automatic: tests that run on a computer
against fixtures, with no phone attached. The second is what was done on the phone itself
(SM-A226B), by touch, the way an owner would do it. Something that passed only on the computer
is never listed as working on the phone.

## Automatic tests (computer, fixtures)

One full run of every suite on the sources of 0.6.188: **24 of 24 suites passed**.

| Suite | Result | Size |
|---|---|---|
| Helpers (Python): `tests/` | pass | 700 tests |
| Backup backend: `backend/tests/` | pass | 106 tests |
| Interface self-tests: `make test` | pass | 102 self-tests |
| `ui: make integration` | pass | 2 profiles |
| `ui: make files-integration` | pass | 3 workflows |
| `ui: make v03-integration` | pass | 10 workflows |
| `ui: make interaction-integration` | pass | 7 workflows |
| `ui: make motion-integration` | pass | 4 workflows |
| `ui: make usb-ui-integration` | pass | 5 workflows |
| `ui: make terminal-ui-integration` | pass | 13 workflows, 568 checks |
| `ui: make display-ui-integration` | pass | 53 workflows, 1181 checks |
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

## On the phone, 2026-11-11 and 2026-11-12

Done by touch on the phone unless said otherwise.

| What | Build | Result |
|---|---|---|
| Screenshot with Volume down + Power; flashlight on the home page | 176 | works |
| Flashlight brightness: every step, the bar, kept over a restart, off before a restart | 177 | works |
| Copy of Android with its data (10 partitions, 57.3 GiB of partitions into a 4.3 GiB archive) | 178 | created and verified |
| Clean installation of Manjaro ARM Phosh from the catalog, the Linux BOOT written by the installation | 185 | installed; BOOT is release v146; started on kernel `#122`, no failed service, Wi-Fi, time zone and language as chosen |
| The same image added beside it, switched to and started | 187 | Phosh starts by itself through the image's `phosh.service` (on 185 the phone stopped at a text login) |
| Phosh on the phone's GPU in that image | 188 | phoc no longer falls back to software drawing (the journal has no "Software rendering detected" and no "too slow"); with the name the installed Mesa uses, a headless phoc reports "GL renderer: Mali-G57 (Panfrost)"; the lock screen is drawn whole |
| Image for any phone: no SSH key inside, Wi-Fi and adb work | 179 | works |
| Android's reset request: page opens, Not now, Forget | 179 | works (request simulated) |
| Backup page offers the Android copy first on a phone with Android's data | 180 | works |
| Verify the copy with data: every checksum of 10 partitions | 180, 181 | matches, about 9 minutes |
| Several ZIPs in one go: two harmless ZIPs ran in order | 181 | works; a failing ZIP and the restart afterwards only in tests |
| Recovery kernel #124 to #127: starts, Wi-Fi, USB, touch, display | 182 to 188 | works |
| Charge shown: a stale 96 % kept by Android refused at 3.97 V; three restarts began at 66 to 68 % | 184, 185 | works |
| Charge kept in the PMIC for Android and the next start | 185, 188 | **does not hold**: the write reads back, but a reload of the RTC's registers shows the old value at once |
| "Android did not start": the page opened by itself after Google's Android 15 restarted for ever, with the kernel's line "Android V requires kernel 4.19" | 183 | works; read once, not shown again |
| A damaged recovery image is refused before anything is written | 183 | refused (IMAGE_DAMAGED); the partition kept its content |
| Flash the recovery from the recovery, read back byte for byte | 182 to 188 | works |

### Android images tried on this phone

| Image | Result |
|---|---|
| crDroid 12.12 (Treble GSI), without Google apps | starts |
| AlphaDroid 4.6 (Treble GSI), with Google apps | starts |
| LineageOS 22.2 light, without Google apps | does not start: restarts |
| Google's Android 15 GSI | does not start: needs kernel 4.19, the phone's Android kernel is 4.14; removed from the catalog |
| Samsung firmware restored from the recovery's own copy | starts |

### Not proven on the phone yet

- Phosh's buttons under a finger in the Manjaro image (the screen and the GPU were checked from a computer; touches were not sent).
- Restoring Android's data from a copy, and Android opening that data afterwards.
- The Android app on a phone (its scripts were run in the recovery against test devices).
- The ZIP queue stopping at a failing ZIP, and its restart afterwards.

### Found while testing, 2026-11-12

- **A pushed file lost 16 MiB once.** A recovery image sent with `adb push` hashed right from
  memory, and read back with 16 MiB of zeros after the volume was mounted again. It did not
  happen again in two further tries. The recovery refused the image, and its adb service now
  flushes a pushed file to the storage before it says "pushed".
- **Reading every PMIC register at once stops the voltage readings** until the next restart
  (a debugging step, not something the recovery does). While they time out the interface
  pauses for a moment every few seconds.
