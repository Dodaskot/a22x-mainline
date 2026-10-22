# Arch Linux ARM with Phosh on A22X

This is a developer installation path for Samsung **SM-A226B (a22x)** using
this project's boot image. It is not an official Arch Linux ARM device port,
and a complete fresh installation using this guide has not yet been tested.
The project's existing development rootfs runs Arch Linux ARM with Phosh.
Modem/SIM support is still experimental; do not expect calls or mobile data.

## 1. Choose a root filesystem target

The boot image and the Linux root filesystem are separate. `boot.img` goes
only to `BOOT`; the root filesystem is ext4 on a partition with a GPT name.
The default image looks for `userdata`. Installing there replaces Android
user data and prevents normal Android use with that partition layout.

For an initial setup, a separately prepared microSD GPT/ext4 partition can
keep existing phone data intact. Give that partition a unique GPT name, for
example `a22x-root`, and build a boot image with
`EXTRA_CMDLINE="cma=96M a22x.root=a22x-root"`. The prebuilt default image will
not select that name. Identify the actual card/partition with `lsblk` before
partitioning or mounting; this guide deliberately supplies no guessed
`/dev/mmcblk*` formatting command. Keep backups and the previous boot image.

## 2. Prepare the generic AArch64 userspace

Use the official [Generic AArch64 installation](https://archlinuxarm.org/platforms/armv8/generic),
[downloads](https://archlinuxarm.org/about/downloads) and
[package signing instructions](https://archlinuxarm.org/about/package-signing).
The generic rootfs supplies userspace; its generic kernel and DTBs do not
replace this project's A22X boot image. Verify the downloaded archive using
the published signature before extraction.

Mount the chosen **empty ext4 root partition** at a directory such as
`/mnt/a22x-root`. In a root shell on the host, preserve ownership, ACLs and
extended attributes with the upstream extraction command:

```sh
bsdtar -xpf ArchLinuxARM-aarch64-latest.tar.gz -C /mnt/a22x-root
```

Configure `/etc/fstab` inside that rootfs for the actual root UUID. Do not
mount a generic kernel's `/boot` over the phone's Android boot partition.
An AArch64 rootfs cannot run directly in an x86_64 chroot without separately
configured binfmt/QEMU; the following package commands are for the phone
itself or an already configured AArch64 environment.

## 3. Make initial USB access persistent

The initramfs creates an NCM USB gadget. Its initial address/DHCP daemon are
not a replacement for rootfs network configuration. Before first boot, add
`/etc/systemd/network/20-a22x-usb.network` inside the new rootfs:

```ini
[Match]
Name=usb0

[Network]
Address=172.16.42.1/24
```

Enable `systemd-networkd` and `sshd` for first access (for an offline tree,
`systemctl --root=/mnt/a22x-root enable systemd-networkd sshd`). Set the PC's
USB interface to `172.16.42.2/24`, then SSH to `alarm@172.16.42.1` after boot.
The generic archive documents the initial `alarm`/`alarm` and `root`/`root`
credentials: change both on first use. UART is the recovery alternative.
USB addressing alone does not give the phone Internet access; configure
host forwarding/NAT or use Wi-Fi once connectivity is installed.

## 4. Install hardware support and Phosh

Follow [BUILDING.md, root filesystem](BUILDING.md#4-root-filesystem) for the
matching `conn` modules, units, firmware, audio UCM, haptics and sleep hook.
Alternatively build the opt-in [boot bundle](BOOT-BUNDLE.md); that packaging
path still needs hardware validation and is not present in prebuilt v143.
Install its rootfs dependencies and the NetworkManager filter either way.

On the phone, as root, initialize the keyring, enable `DisableSandbox` in
`/etc/pacman.conf` under `[options]` for this kernel without Landlock, then
update and install the mobile userspace:

```sh
pacman-key --init
pacman-key --populate archlinuxarm
pacman -Syu
pacman -S phosh phoc squeekboard networkmanager bluez bluez-utils \
    pipewire pipewire-pulse wireplumber python kmod mesa
systemctl enable --now NetworkManager bluetooth
```

[Arch Linux ARM publishes Phosh for AArch64](https://archlinuxarm.org/packages/aarch64/phosh).
Use the distribution's Mesa initially; the private GPU benchmark builds in
this project's reports are not installed by the boot image. Preserve the
USB-only `systemd-networkd` configuration and configure NetworkManager not
to manage `usb0` if it tries to claim it.

For the first graphical smoke test, log in as an ordinary user on a local
TTY and run `phosh-session` (the packaged session launcher). Do not run the
desktop as root or start a second compositor from SSH into an occupied seat.
A display manager supporting `/usr/share/wayland-sessions/phosh.desktop`
can be configured later; preserve recovery access until touch and login
work. See the [upstream Phosh project](https://gitlab.gnome.org/World/Phosh/phosh)
and [development documentation](https://world.pages.gitlab.gnome.org/Phosh/phosh/).

## 5. Install boot and verify

Verify the SHA-256 of the chosen image, then follow
[boot-only flashing](BUILDING.md#5-flashing). Use a boot image whose root GPT
name matches step 1. Do not install a generic U-Boot/GRUB recipe on this phone.
After startup check `uname -v`, `journalctl -b -p warning`, touch, display,
USB SSH, Wi-Fi, Bluetooth, charging and a suspend/wake cycle. The sleep mode
must stay `s2idle`; `deep` reboots this firmware.

Recovery key combinations and the known-good-image rollback are documented
in [BUILDING.md](BUILDING.md#6-recovery). A boot upload or a visible desktop
alone does not verify all hardware functionality.
