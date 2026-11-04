# Minimal initramfs charger for a22x

This is the source for the charging screen accepted on SM-A226B with kernel
#107. Linux still boots, then a small static program becomes PID 1 inside the
initramfs. It displays battery percentage and charging state through the
framebuffer without mounting the normal root filesystem or starting
systemd, Phosh, USB networking, or an initramfs shell. Stock LK remains
unchanged.

## Boot selection and normal boot

The init script checks the charger branch immediately after mounting
devtmpfs, procfs and sysfs. Selection requires both the exact
`samsung,a22x` device-tree compatibility and a single exact
`androidboot.mode=charger` command-line marker. Missing, conflicting,
duplicate or malformed markers follow normal boot.

Explicit rescue/debug selections take precedence, including
`a22x.noswitch`, `systemd.unit=`, `rd.systemd.unit=`, systemd break/debug/run
options, and single-user/emergency/rescue/runlevel tokens. The existing
normal path still selects `a22x.root=<GPT partition name>` (default:
`userdata`) and runs the boot-bundled connectivity setup before
`switch_root`. Root selection alone does not override a genuine charger
boot.

Once selected, the controller stays in the initramfs. A stopped controller
or failed power syscall does not start the full system or retry a power
action automatically.

The default boot image command line keeps the UART console, earlycon and
kernel diagnostics, removes the panel's `console=tty0`, and adds
`fbcon=logo-count:0 vt.global_cursor_default=0`. This suppresses kernel text,
the logo and the cursor on the panel while preserving serial diagnosis.
The charger UI draws through fbdev after it becomes available; no Android
animation files, stock UI binaries or rootfs service are required.

## Controller behavior

- The screen starts visible for 12 seconds. A fresh power-key press shows
  it again; holding the key keeps it visible.
- A fresh hold of at least two seconds requests a normal reboot. A key
  already held when the controller starts or resynchronizes must first be
  released.
- Poweroff requires valid, fresh power-supply samples, both charging
  sources reporting offline, a known released power key, the 10-second
  startup grace period, and a continuous three-second offline window.
  Unknown, stale, inconsistent or invalid readings do not authorize
  poweroff.
- Input loss, event overflow and failed synchronization invalidate the
  pending key/unplug state. Event timestamps use the monotonic clock.
- Each power action is consumed before its syscall, so a failed syscall
  cannot trigger a reboot/poweroff loop.

The normal runtime refuses to start unless it is root PID 1 in a genuine
a22x charger boot. `--check-boot` only checks boot metadata;
`--version` only prints the version; `--probe` reads current metadata and
power-supply state without changing hardware.

## Build

Requirements: Python 3 and a Linux AArch64 C compiler with static libc and
Linux userspace headers. The default compiler is
`aarch64-linux-gnu-gcc`. `CHARGER_CC` overrides its command; otherwise
`CROSS_COMPILE` overrides the toolchain prefix. A plain `CC` does not affect
this component because the initramfs already uses it for the separate
freestanding memtool build.

From the repository root (also valid in fish):

```sh
python3 boot/charger/build.py
env CHARGER_CC=aarch64-linux-gnu-gcc python3 boot/charger/build.py --output /tmp/a22x-charger
```

The default output is `boot/charger/build/a22x-charger`, which is ignored by
Git. The helper verifies little-endian ELF64, the AArch64 machine type and
the absence of a dynamic loader or dynamic section before installing the
output. A failed compilation or validation leaves an existing output
untouched.

`boot/build-initramfs.sh` automatically compiles the same source directly
into its temporary image root as `/bin/a22x-charger`; no prebuilt charger
binary is required. Its existing `BUSYBOX`, `HIMAX_FW`, and optional
connectivity-bundle inputs still apply. Firmware and generated initramfs,
test executables and boot images are not part of this source directory.

This makes the component buildable from published source. Rebuilding an
entire boot image is not a claim of byte-identical output: the existing
kernel, firmware, toolchain and initramfs metadata also affect that image.

## Tests

From the repository root:

```sh
python3 boot/charger/test.py
python3 boot/charger/test.py --sanitize --aarch64
python3 boot/tests/test-connectivity-bundle.py
```

The first command runs the four native suites. The second additionally
runs them under AddressSanitizer/UBSan and as static AArch64 binaries under
`qemu-aarch64`. `HOST_CC` selects the native compiler and `QEMU_AARCH64`
can override the emulator command. Temporary executables are removed
after each run.

| Suite | Coverage |
| --- | --- |
| Policy/metadata | Boot selection, explicit overrides, key timing, unplug grace, stale/invalid state and one-shot actions |
| Controller I/O | Actual metadata and sysfs readers against private temporary regular files |
| Controller input | Mocked evdev reads, timestamps, overflow/resynchronization and error handling |
| UI | Geometry, pixel packing, clipping, stride/padding, state changes and render bounds |

Controller test builds exclude the live PID 1 loop and its power syscalls.
UI tests exercise pure helpers and do not open device nodes. These suites
do not replace physical tests of the display, key or cable on a phone.
Normal boot and the charging screen were exercised with kernel #107; the
user confirmed the visible charge display, absence of panel logs and
qualitatively faster startup. Host policy tests alone do not establish
physical long-press or cable-removal results.

## Source and licenses

The controller is GPL-2.0-only; see the repository's
[`LICENSE`](../../LICENSE). The policy and UI files retain their MIT
headers; their license text is in [`LICENSE.MIT`](LICENSE.MIT). Tests retain
their per-file licenses. The static build/test helpers use GPL-2.0-only.

The runtime sources are the same sources used for the accepted minimal
charger. Public integration also preserves this repository's existing
connectivity-bundle path for ordinary boot.

The v144 prebuilt uses GCC 16.2.0 and static GNU libc 2.44. Its library
provenance and application rebuild/relink command are recorded in the
[v144 release](../../releases/v144/README.md#contents-and-source).
A new component build may use a compatible static AArch64 libc/toolchain;
it is not required to reuse the prebuilt executable.
