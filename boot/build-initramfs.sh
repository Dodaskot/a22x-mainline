#!/bin/bash
# Build boot/initramfs.cpio.gz: busybox init that brings up USB NCM
# (172.16.42.1, telnet root shell while it stays in the initramfs) and
# switch_roots to the partition named by a22x.root= (default "userdata").
#
# Not shipped in this repository, provide them yourself:
#   BUSYBOX   static aarch64 busybox binary (e.g. from Alpine busybox-static)
#   HIMAX_FW  Himax_firmware_fw_jdi.bin from the stock /vendor/firmware/
#             (touch controller firmware, Samsung/Himax proprietary)
# memtool is built from tools/memtool.c with an aarch64 cross compiler.
set -euo pipefail
cd "$(dirname "$0")"
: "${BUSYBOX:?set BUSYBOX=/path/to/static/aarch64/busybox}"
: "${HIMAX_FW:?set HIMAX_FW=/path/to/Himax_firmware_fw_jdi.bin}"
# Optional: all five modules built with this KOUT, plus four local conn firmware binaries and two configuration files.
# Omitting CONN_MODULES preserves the historical touch-only image.
if [ -n "${CONN_MODULES:-}" ]; then
    : "${CONN_FIRMWARE:?set CONN_FIRMWARE to the stock connectivity firmware directory}"
    : "${KOUT:?set KOUT to the matching kernel build directory}"
    CONN_MODULES=$(realpath "$CONN_MODULES")
    CONN_FIRMWARE=$(realpath "$CONN_FIRMWARE")
    KOUT=$(realpath "$KOUT")
fi
CC=${CC:-clang --target=aarch64-linux-gnu -fuse-ld=lld}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK"/{bin,sbin,dev,proc,sys,tmp,mnt,etc,lib/firmware}
install -m755 initramfs/init "$WORK/init"
install -m755 initramfs/bin/a22x-boot-bundle "$WORK/bin/"
install -m755 initramfs/bin/reboot-download "$WORK/bin/"
install -m755 "$BUSYBOX" "$WORK/bin/busybox"
install -m644 "$HIMAX_FW" "$WORK/lib/firmware/Himax_firmware_fw_jdi.bin"
if [ -n "${CONN_MODULES:-}" ]; then
    python3 build-connectivity-bundle.py --modules "$CONN_MODULES" \
        --firmware "$CONN_FIRMWARE" --kout "$KOUT" --himax "$HIMAX_FW" \
        --destination "$WORK/a22x-boot"
fi
$CC -Os -static -nostdlib -ffreestanding -fno-stack-protector \
	-o "$WORK/bin/memtool" ../tools/memtool.c

(cd "$WORK" && find . | sort | cpio -o -H newc -R 0:0 --quiet | gzip -9n) > initramfs.cpio.gz
ls -l initramfs.cpio.gz
