# Kernel #107 and v144 minimal-charger publication

Date: 2026-11-04. This publication advances the previous #102 source to the
tested #107 kernel and adds the tested minimal charger and v144 boot image.

## Exact source

| Item | Identity |
|---|---|
| Base | Linux v7.2, `8d3ae59288f1e7d58d76558a6ee96d533bc5019f` |
| Previous kernel head | `f058393c10f79548284ca34618778f19e286fce2` (#102) |
| Current kernel head | `79a09eba25ef8cb4e03a98325c6ec1e606961810` (#107) |
| Current kernel tree | `ed30c94c761a0e49d6206b94751c7b7e74e73d47` |
| Patch series | 0001–0143, in commit order |
| Config | `kernel/config/a22x-v144.config` |
| Config SHA-256 | `aca24ae1a7cd143127968006a6d9329e0eb7458a49016fe096bcb97327a52364` |
| Source tags | `a22x-kernel107`, `a22x-v144` |

The ordered stable patch IDs match all 143 source commits. Applying the full
published series to the declared base produces exactly the tree above.
The previous 137 patch files are retained unchanged; six new files extend
the series. The config changes only `CONFIG_POWER_RESET_A22X_REBOOT=y`
relative to the historical v135 config.

| Patch | Kernel commit | Change |
|---|---|---|
| 0138 | `ce1fe5be77a6` | Opt-in MT6359 RTC power-off |
| 0139 | `db78f6b48a43` | Preserve normal restart with a charger attached |
| 0140 | `5c3459e2e274` | Opt-in RF-LDO LP observations |
| 0141 | `ddfcc3b8f20d` | Opt-in stock RF-LDO LP initialization |
| 0142 | `502df6895d27` | Bind LP initialization to exact OF identities |
| 0143 | `79a09eba25ef` | Continue MT6359 power-off after a valid CLR timeout |

LP diagnostic/initialization changes remain opt-in. Their presence in the
kernel source does not prove the cause of the modem assertion or enable SIM,
calls or mobile data. The v144 image preserves the tested profile's explicit
LP parameters; a new default build does not add them automatically.

## Minimal charger and quiet display

The static AArch64 charger lives under `boot/charger/`. On the exact LK
charger mode and A22x compatible, it becomes PID 1 before networking,
rootfs mounting or normal userspace starts. It displays battery capacity
from the power-supply drivers, with an unknown indicator when no valid
capacity is available. The charging drivers retain their existing control
of current and temperature; the UI does not write charging policy registers.

The UI timeout, power-key handling, disconnect debounce, stale/unknown input
handling and one-shot reboot/power-off policy are documented and tested in
the component directory. The public init keeps its optional connectivity
bundle guard and root selector. Its runtime charger source matches the
tested source byte for byte.

The default boot command line removes `console=tty0` and adds
`fbcon=logo-count:0 vt.global_cursor_default=0`. UART, earlycon and the
existing diagnostic log level are retained. Framebuffer console mapping is
not disabled, because the initial display modeset is still needed.

## Hardware evidence and limits

The published image is the existing qualified image, not a publication
rebuild. Its SHA-256 is
`b3f59daa37ad50e0ac6bfaed83f6954147536d07119f1b46a9981ee19d9b17ab`.

- BOOT readback matched that image after the successful BOOT-only install.
- Normal startup ran `7.2.0-a22x+ #107`, reached the graphical services
  and SSH, and showed zero failed systemd units in the postflight check.
- Active kernel consoles were UART and ramoops, without tty0.
- Power-off with a cable attached entered the genuine LK charger boot.
  The controller reported PID 1, rootfs unmounted, network off and normal
  userspace off; the framebuffer indicator initialized near uptime 2 s.
- Charger observation continued through kernel uptime 213 s without a
  reset. Battery capacity advanced from 96% to 100% during the capture.
- The user confirmed the working charging screen, no kernel logs on the
  display and noticeably quicker startup. That speed observation is
  qualitative; no comparative boot benchmark was recorded.

The long-Power and disconnect actions have offline policy/controller tests.
Separate physical confirmation of those two actions is not part of this
report. The optional connectivity bundle has not gained hardware validation
from the charger test. Device-private captures, identifiers and calibration
data are not included in the public repository.

For the binary contents, checksums, exact command line and installation,
see [v144](../releases/v144/README.md) and its
[manifest](../releases/v144/kernel107-provenance.json).
