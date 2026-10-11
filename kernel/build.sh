#!/bin/bash
# Fetch Linux v7.2, apply the a22x series and build Image.gz + DTBs.
# usage: ./build.sh [linux-dir] [out-dir]     (LLVM/Clang toolchain in PATH)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$here/linux}
OUT=${2:-$here/out}
if [ ! -d "$SRC/.git" ]; then
	git clone --depth 1 --branch v7.2 \
		https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$SRC"
	git -C "$SRC" checkout -b a22x
	git -C "$SRC" am "$here"/patches/*.patch
fi
mkdir -p "$OUT"
[ -f "$OUT/.config" ] || cp "$here/config/a22x-v135.config" "$OUT/.config"
MAKE="make -C $SRC O=$OUT ARCH=arm64 LLVM=1"
$MAKE olddefconfig
$MAKE -j"$(nproc)" Image.gz modules \
	mediatek/mt6833-samsung-a22x.dtb \
	mediatek/mt6833-samsung-a22x-native-display.dtb \
	mediatek/mt6833-samsung-a22x-nfc-experimental.dtb
ls -l "$OUT/arch/arm64/boot/Image.gz" "$OUT"/arch/arm64/boot/dts/mediatek/mt6833-samsung-a22x*.dtb
