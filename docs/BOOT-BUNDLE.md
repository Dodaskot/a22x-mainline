# Optional connectivity bundle in boot

The Linux Himax and MediaTek connectivity drivers in this repository have
source code. The controller firmware is still proprietary: packaging the
firmware in an initramfs does not make it open source. A fresh installation
can nevertheless carry all seven firmware/config files and all five matching
connectivity modules in its boot image, instead of copying them into Android
or into the Linux root filesystem separately.

This is an **opt-in packaging implementation**. Synthetic AArch64 module
fixtures and rejection paths are tested; this bundle has not yet been booted
on hardware. The published v143 image is unchanged and does not contain this
new connectivity bundle. Keep a tested image available before hardware trials.

## Build

First build the kernel and `conn/` against the same kernel output directory
as described in [BUILDING.md](BUILDING.md). Supply the seven local files listed
under [proprietary firmware](../README.md#proprietary-firmware). No firmware
is downloaded or committed by these scripts. If `WMT_SOC.cfg` or `wifi.cfg`
is absent from the supplied directory, its repository `conn/firmware/`
configuration is used; an explicit local configuration takes priority.

From the repository root, use absolute paths:

```sh
BUSYBOX=/absolute/path/to/static-aarch64-busybox \
HIMAX_FW=/absolute/path/to/Himax_firmware_fw_jdi.bin \
CONN_MODULES="$PWD/conn/out" \
CONN_FIRMWARE=/absolute/path/to/stock/vendor/firmware \
KOUT="$PWD/kernel/out" boot/build-initramfs.sh
OUT="$PWD/kernel/out" boot/mkboot.sh bundled-test
```

The bundle builder rejects missing/empty/symlink inputs, mismatched module
build markers and module vermagic releases. It records SHA-256 hashes in
`manifest.json`; exact build markers supplement vermagic because vermagic
alone does not identify a kernel build. Use a single clean kernel output
for both kernel and modules; the marker cannot prove their build history.
`mkboot.sh`/AVB packing must succeed within the 32 MiB boot partition before
flashing. Omit `CONN_MODULES` for the historical touch-only initramfs.

## After switch_root

The initramfs copies the bundle into a tmpfs at `/run/a22x-boot`, then moves
that `/run` mount into the real root. It supplies runtime systemd units and
drop-ins for `a22x-conn` and `a22x-wmt-launcher`. Existing files in `/opt`,
`/etc/systemd/system` and `/usr/lib/firmware` are not overwritten. Runtime
drop-ins select the bundled scripts even when older units exist in `/etc`.
The kernel firmware search override and WMT launcher both use the bundled
firmware directory. On the next boot without a bundle the runtime files are
gone and the installed configuration applies again.

The root filesystem still needs systemd, `/usr/bin/python3`, shell/core
utilities, `kmod` (`insmod`), NetworkManager and its device filtering config
from `conn/tools/90-a22x-wifi.conf`. A desktop also needs Mesa, Phosh/Phoc and
its other userspace packages. This is one boot image for the hardware support
files, not a root filesystem packed into boot.

No `bt_addr` or NVRAM is embedded. An existing per-device
`/opt/a22x-conn/bt_addr`, when present, is read by the bundled startup script;
otherwise the existing driver default applies. Keep factory identity outside
redistributable images.

## Validation on a future hardware trial

```sh
uname -v
cat /run/a22x-boot/manifest.json
cat /sys/module/firmware_class/parameters/path
systemctl cat a22x-conn a22x-wmt-launcher
systemctl status a22x-conn a22x-wmt-launcher
journalctl -b -u a22x-conn -u a22x-wmt-launcher
ip link show wlan0
bluetoothctl list
```

Also check touch, a Wi-Fi connection, Bluetooth discovery, suspend/resume and
power-key wake. A successful package build is not proof of those checks.
The current modem experiments are separate and are not included in this bundle.

Host-side fixture test:

```sh
python3 boot/tests/test-connectivity-bundle.py
```

## Offline build check (2026-10-22)

All five connectivity modules were freshly built against an isolated copy
of the exact kernel #101 build output. Their release is `7.2.0-a22x+` and
the build marker is `#101 SMP PREEMPT Sat Oct 17 18:00:53 CEST 2026`.
An actual bundled initramfs is 4,212,638 bytes. Replacing only the ramdisk
in the tested #101 image produced a 33,554,432-byte candidate with
8,667,136 bytes free before the AVB reserve. Kernel and DTB bytes stayed
identical; AVB hash/footer verification and all 16 bundled file hashes
passed. The candidate has **not been flashed or hardware-tested** and is
not published as a replacement for v143. Boot handoff, connectivity and
suspend/wake still require the hardware checks above.
