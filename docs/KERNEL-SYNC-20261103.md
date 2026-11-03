# Public kernel source synchronization, 2026-11-03

The public [linux-a22x branch `a22x`](https://github.com/Dodaskot/linux-a22x/tree/a22x)
now contains the source of the deployed kernel #102. It has **137 commits
above Linux v7.2**, matching `kernel/patches/series` in this repository.
The previous public kernel branch stopped at v135/#95.

## Source versions

| Source | Kernel tag | Commit | Tree |
|---|---|---|---|
| v135 / #95 | `a22x-v135` | `d8e9901c4064dd491e7643cf001d5b32ac75a227` | `18506838c317511d077c911278dc33fe13721d6b` |
| v142 / #100 | `a22x-v142` | `c72846356a4f7b5dddf2e4ce95ff461dd22d2527` | `e0b3aaca697f1a53d8edb402636450cd4a35a111` |
| v143 / #101 | `a22x-v143` | `8a4b15a516315c5c72d4bd389f7064ec99537449` | `9ee513e4f38d82b426a785def817daef2330c0b7` |
| #102 | `a22x-kernel102` | `f058393c10f79548284ca34618778f19e286fce2` | `043297180017cae94b2574a598b9075d5cb064dd` |

The existing v135 tag is retained. The later commits extend its history;
the reconstructed commit identifiers do not imply identical original build
metadata. Source tree hashes identify the actual contents.

Patches 0132–0135 carry the previously published kexec, I2C supply and
USB-C/OTG changes. Patch 0136 carries the two-second Panfrost watchdog from
v143. Those five patches were applied without changing their payloads.

## Himax teardown change in #102

Patch 0137 imports the exact three changed files from the retained #102
source. The driver marks a device as being removed, prevents resume and IRQ
re-enable during teardown, and drains IRQs, firmware work and proc callbacks
before freeing their shared state. It also removes the factory proc subtree
and unregisters the input devices.

The publication does not implement open Himax controller firmware. Touch
still requires the existing proprietary zero-flash firmware.

## Validation and limits

- All **94,902 tracked files**, including executable/symlink modes, were
  compared against the retained #102 build source. Relative to the v143
  tree, only `himax_common.c`, `himax_common.h` and `himax_platform.c` differ.
  After importing them, the published tree matches every checked file;
  the retained source has no additional files.
- The retained #102 BOOT SHA-256 is
  `60aa3b3be80a404559d0ed9f587d2a086d35ac651b912e72cbb41b5a84344940`.
  It matches the image identified in the recent mainline SIM1 runs. This
  connects the retained build to the deployed image, without treating
  every driver teardown path as separately hardware-tested.
- Its configuration matches `kernel/config/a22x-v135.config` byte for byte:
  SHA-256 `aff2b1c335d2c500ef534cb15e0a39daad272005814e82406bcc2546990a653f`.
  A fresh `olddefconfig` with Clang 23.1.1 retained the same bytes. All four
  Himax driver objects compiled successfully from the published source.
  The compiler reported missing-prototype warnings and an uninitialized
  `ret` in the existing factory self-test path; these predate this patch
  and remain in the imported source.
- The complete patch series and kernel history were compared in order,
  and whitespace checks passed. This publication did not rebuild the full
  kernel or perform a new phone flash, boot or teardown stress test.

The latest downloadable image remains **v143/#101**. It contains patches
0001–0136 and does not include patch 0137. The `a22x-kernel102` tag publishes
source, not a new binary release. Cellular service remains unavailable;
see the [SIM1 report](MODEM-VALIDATION-20261103.md).
