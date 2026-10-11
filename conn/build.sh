#!/bin/bash
# Build the MT6833 connectivity modules (WMT, BTIF, gen4m Wi-Fi, BT HCI)
# against an already built a22x kernel tree. The modules must be rebuilt for
# every kernel build: conn-start.sh compares uname -v with kernel-release.
# usage: KSRC=<linux tree> KOUT=<kernel build dir> ./build.sh [make args]
set -euo pipefail
cd "$(dirname "$0")"
: "${KSRC:?set KSRC to the patched linux source tree}"
: "${KOUT:?set KOUT to the kernel build (O=) directory}"
make -C "$KSRC" O="$KOUT" ARCH=arm64 LLVM=1 M="$PWD" -j"$(nproc)" \
	MTK_COMBO_CHIP=SOC2_1X1 WLAN_CHIP_ID=6833 CONFIG_MTK_COMBO_WIFI_HIF=axi \
	CONFIG_MTK_PLATFORM=mt6833 TARGET_BUILD_VARIANT=userdebug \
	CONFIG_MTK_COMBO_WIFI=m KBUILD_MODPOST_FAIL_ON_WARNINGS=n MTK_WLAN_SERVICE=yes MTK_ANDROID_WMT=y MTK_ANDROID_EMI=y \
	modules "$@"
mkdir -p out
for m in btif/common/btif.ko common/wmt_drv.ko wlan/adaptor/wmt_chrdev_wifi.ko wlan/core/gen4m/wlan_6833_axi.ko bt/hci/btmtk_stp.ko; do
	llvm-strip --strip-debug -o "out/$(basename "$m")" "$m"
done
sed -n 's/^#define UTS_VERSION "\(.*\)"$/\1/p' "$KOUT/include/generated/utsversion.h" > out/kernel-release
ls -l out
