# A22x validation, 2026-10-13

Tested device: Samsung SM-A226B. Running image: v142, Linux
`7.2.0-a22x+`, build #100, LLVM 23.1.1. This update publishes source and
results; the downloadable v135 image and connectivity modules are unchanged.

## USB-C OTG

The MT6360 TCPC uses GPIO9 for its interrupt and the charger's 5 V boost
regulator for source VBUS. The driver now enables the standard alert mask
after registering the IRQ. The I2C controller only requests a supply declared
on its own node, avoiding a dependency cycle through the TCPC child.
MUSB PHY power references are balanced across role changes.

Verified on the running phone:

- Automatic device/sink mode, configured NCM networking and charging on a PC.
- Automatic host/source mode with a USB-C mass-storage drive, 5 V VBUS.
- exFAT mounted read-only; one file sample read successfully.
- Normal plug orientation: 256 MiB direct read in 38.5001 seconds, about 7 MB/s.
- Reversed plug orientation: mount and 64 MiB direct read passed.
- After unmounting and reconnecting the PC: device/sink, configured NCM,
  charging restored, boost regulator disabled with zero users, same boot.
- USB device-mode recovery also passed after a subsequent modem-cycle reboot.

The drive negotiated USB high speed (480 Mb/s). The measured read throughput
is one test result, not a maximum-speed claim. No files were written to the
drive. Write throughput, hubs, PD chargers and OTG across suspend remain untested.

## Sensors and desktop

MN78911 responded to covering/uncovering and illumination over 337 samples:

| Observation | Measured result |
|---|---|
| Ambient-light raw counts | 1–15577 |
| Proximity raw counts | 85–321, near threshold 200 |
| Sensor-proxy proximity | far → near → far |
| Actual display backlight | 4–251 of 255 in response to light changes |

The driver uses an estimated scale of 0.5 lux per count. Absolute lux accuracy
was not calibrated against a reference instrument; no calibration values were
changed. Screen blanking during an actual call was not tested.

ICM-40607 raw acceleration reads passed. A transient SPI timeout/FIFO read error
was also recorded on this boot; the cause remains unresolved. Full orientation
transitions were not retested. This is separate from the MN78911 lux calibration.

Phosh/Phoc, Wi-Fi, Bluetooth controller presence and charging were checked.
Audio, NFC transactions, Bluetooth accessory use and suspend power consumption
were not comprehensively retested in this update. The local Phosh session uses
a private GNOME desktop ABI compatibility library after a userspace upgrade;
that library is not included here.

## Modem checkpoint

The latest verified bounded cycle, D404, consumed **28 RPC and 134 filesystem
responses** on kernel #100. FS122 sought offset 64 in a private RAM copy of
an NVRAM file; FS123 GETSIZE returned 176 bytes without changing the cursor.
FS124 wrote 56 bytes into that private RAM copy, preserving the surrounding
bytes and advancing the cursor to 120. FS125 closed the active handle while
retaining the full RAM copy and the immutable OPEN/SEEK/WRITE snapshots.
FS126–129 answered absent OPOTA configuration-file time queries in the T and S
namespaces. Only independently qualified transport-padding bytes may vary.
FS130 and FS131 each read 48 bytes at offset 64 from a 160-byte NVRAM file and
closed at cursor 112. FS132 opened that file read-only with its cursor at zero,
retaining the earlier private RAM ledgers. FS133 read 64 bytes and advanced the
active cursor to 64; FS134 closed it, preserving immutable OPEN and READ records.
The original DZC3 daemon's behavior
was checked offline; 276 host test PASS lines, including full-prefix observer
and sanitizer checks, preceded the run. The next captured request is FS135,
an OPEN request with flags `0x20010400`, still unhandled.

**HS2 has not been reached.** D401 and D404 stopped at their unhandled next
requests and automatically rebooted to NORMAL0. All eight CPUs, held/powered
modem domain, Phosh/Phoc and USB networking passed recovery checks. Wi-Fi profile
restoration failed because the hotspot could not be found; USB access remained
available.

The earlier D397 automatic reboot returned to charging mode (mode 8), so its
initial recovery check failed. A separate qualified recovery using two volatile
SRAM words and an ordinary normal reboot restored NORMAL0 with all recovery
checks passing. Its original failure is preserved as
`FAIL_RECOVERY_AFTER_132_REPLIES_CONSUMED`. Before D401, comparison of all 20 LK
tags proved identical names, structure and payloads; 838 unused bytes differed.
The new cycle was bound to the exact qualified hash without relaxing the check.

SIM, calls and mobile data remain unavailable. The harness does not write
persistent NVRAM. This repository does not include the device-specific evidence
or the experimental CCCI harness.

## Artifact identity

| Artifact | SHA-256 |
|---|---|
| Tested v142 BOOT | `aa375adf2cfe1852a6422b1cb7c34c17f27e64e82f7980e9ea6c8c2c5071188c` |
| Kernel configuration | `aff2b1c335d2c500ef534cb15e0a39daad272005814e82406bcc2546990a653f` |
| Module.symvers | `da9cee6327b6ea26b1db3f238736825b08620ba7253b40e6f12f73392dfd5f6d` |
| Built NFC DTB | `3a87ca9785d91db1bd079ded9cc9833e31aaf927afc2a1e8f4542ef50e5c05a6` |

The configuration and exported symbols are unchanged from the previous tested
kernel. Existing device-tree values were preserved except for USB `dr_mode`;
149 changed phandle references were compared by their resolved targets.
The carried kexec change limits the CFI exemption to the copied assembly
relocation entry. A new end-to-end kexec test was not part of this OTG update.
