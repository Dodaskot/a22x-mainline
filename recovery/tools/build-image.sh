#!/bin/bash
# Build an A22x recovery 0.6 image: the qualified 0.5 base image with the 0.6 sources, kernel,
# platform files and runtimes layered on top by tools/build_v06_image.py.
#
#   tools/build-image.sh NN          ->  $A22X_RECOVERY_WORK/build-v06-NN/recovery-a22x-mainline-k108-v0.6.img
#
# Inputs, all below A22X_RECOVERY_WORK (default: the directory that holds this source tree):
#   base/                  the qualified 0.5 build (BASE, see README "Building")
#   kernel/                kernel (Image.gz + DTB packaging input) and wifi-modules.tar.xz (KERNEL_DIR)
#   dtb-aliases/dtb        the device tree with the recovery's aliases; since 0.6.177 its torch limit is
#                          175 mA (led-max-microamp), as Samsung's factory torch; dtb-torch100 is the one before
#   glibc-runtime/{lib,bin}, rt-wrappers/, https-runtime/fullroot/, helpers/a22x-wmt,
#   wifi-static/wpa_supplicant (optional)
#   platform-private/      captured from your own phone, never published:
#                          platform-files.tar.xz, distro-platform-v2.json, build-update.json
# PUBLIC=1 builds the image for any phone: see below.
#
# The script stops on the first error, and at the end compares every Python file and the
# interface binary inside the image with this source tree.
set -euo pipefail
NN=${1:?usage: tools/build-image.sh NN}
VERSION=${VERSION:-0.6.$NN}
SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=${A22X_RECOVERY_WORK:-$(dirname "$SRC")}
BASE=${BASE:-$WORK/base}
KERNEL_DIR=${KERNEL_DIR:-$WORK/kernel}
PRIVATE=${PRIVATE:-$WORK/platform-private}
S=${SRC#"$WORK"/}           # the source tree relative to WORK, as the image tool expects
cd "$WORK"

ADD=()
add() { ADD+=(--add "$1=$2"); }
for f in glibc-runtime/lib/* glibc-runtime/bin/*; do add "opt/a22x-recovery/glibc/${f#glibc-runtime/}" "$f"; done
for t in make_f2fs lz4 unsquashfs; do add "bin/$t" "rt-wrappers/$t"; done
for f in "$S"/platform/build/*.sh "$S"/platform/build/*.patch "$S"/platform/build/keys/*.gpg; do
    add "opt/a22x-recovery/platform/build/${f#"$S"/platform/build/}" "$f"
done
tar -C "$S/platform/optional" --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 \
    -cJf build-platform-optional.tar.xz usr var
add opt/a22x-recovery/platform/platform-optional.tar.xz build-platform-optional.tar.xz
if [ -f "$KERNEL_DIR/wifi-modules.tar.xz" ]; then
    add opt/a22x-recovery/platform/wifi-modules.tar.xz "$KERNEL_DIR/wifi-modules.tar.xz"
    add opt/a22x-recovery/platform/a22x-wifi.sh "$S/platform/a22x-wifi.sh"
    add opt/a22x-recovery/wifi.py "$S/wifi.py"
    add opt/a22x-recovery/catalog.json "$S/catalog.json"
    add opt/a22x-recovery/openpgp.py "$S/openpgp.py"
    add opt/a22x-recovery/identity_backup.py "$S/identity_backup.py"
    add opt/a22x-recovery/partition_archive.py "$S/partition_archive.py"
    add opt/a22x-recovery/screenshot.py "$S/screenshot.py"
    ADD+=(--replace "opt/a22x-recovery/ssh_service.py=$S/ssh_service.py")
    add opt/a22x-recovery/keys/archlinuxarm.asc "$S/keys/archlinuxarm.asc"
    add opt/a22x-recovery/keys/ubuntu-cloud.asc "$S/keys/ubuntu-cloud.asc"
    add opt/a22x-recovery/keys/fedora.gpg "$S/keys/fedora.gpg"
    add usr/lib/libssl.so.3 https-runtime/fullroot/usr/lib/libssl.so.3
    add usr/lib/python3.14/lib-dynload/_ssl.cpython-314-aarch64-linux-musl.so \
        https-runtime/fullroot/usr/lib/python3.14/lib-dynload/_ssl.cpython-314-aarch64-linux-musl.so
    add etc/ssl/certs/ca-certificates.crt https-runtime/fullroot/etc/ssl/certs/ca-certificates.crt
    ADD+=(--link etc/ssl/cert.pem=certs/ca-certificates.crt)
fi
add opt/a22x-recovery/platform/platform-files.tar.xz "$PRIVATE/platform-files.tar.xz"
# APatch (2026-11-11): KernelPatch's kptools as apatch/build-kptools.sh builds it from source (the
# APK's own needs Android's linker) and KernelPatch's kpimg as released, the one in the APatch APK.
if [ -x apatch/out/kptools-aarch64 ] && [ -f apatch/out/kpimg ]; then
    echo "eb2e3d16fe69410150efcf6f9425baf8cad9ac0c39f051fc8b40ce57c7143adb  apatch/out/kpimg" | sha256sum -c --quiet -
    add opt/a22x-recovery/apatch/kptools apatch/out/kptools-aarch64
    add opt/a22x-recovery/apatch/kpimg apatch/out/kpimg
fi
[ -f wifi-static/wpa_supplicant ] && add opt/a22x-recovery/platform/wpa_supplicant wifi-static/wpa_supplicant
# An image for any phone (PUBLIC=1, 2026-11-11): no SSH key is built in, so a computer is let in only
# by the request on the phone's screen; PRIVATE then names platform files whose Bluetooth address is
# zeros (platform/conn-start.sh makes one from the phone's own storage chip).
if [ "${PUBLIC:-}" = 1 ]; then
    : > build-no-keys
    ADD+=(--replace "etc/a22x-recovery/authorized_keys=build-no-keys")
fi

rm -rf "build-v06-$NN" build-themes
mkdir build-themes
python3 -B "$S/themes.py" compile "$S/themes/beaver.a22xtheme" build-themes/beaver.theme >/dev/null

R=opt/a22x-recovery
python3 -B "$S/tools/build_v06_image.py" --base "$BASE" --output "build-v06-$NN" --version "$VERSION" \
    --image-name recovery-a22x-mainline-k108-v0.6.img --build-update "$PRIVATE/build-update.json" \
    --replace "bin/recovery-ui=$S/ui/a22x-recovery-ui-aarch64" \
    --kernel "$KERNEL_DIR/kernel" --dtb dtb-aliases/dtb \
    --replace "init=$S/ramdisk/init" \
    --replace "$R/admin_session.py=$S/admin_session.py" --add "$R/access_gate.py=$S/access_gate.py" \
    --replace "$R/filesystem.py=$S/filesystem.py" --replace "$R/usb_files.py=$S/usb_files.py" \
    --replace "$R/display.py=$S/display.py" --replace "$R/terminal.py=$S/terminal.py" \
    --replace "$R/distro.py=$S/distro.py" --replace "$R/install.py=$S/install.py" \
    --replace "$R/backend/linux_workflow.py=$S/backend/linux_workflow.py" \
    --replace "$R/backend/common.py=$S/backend/common.py" --replace "$R/backend/storage.py=$S/backend/storage.py" \
    --replace "$R/backend/engine.py=$S/backend/engine.py" --replace "$R/backend/recoveryctl.py=$S/backend/recoveryctl.py" \
    --replace "etc/a22x-recovery/distro-platform.json=$PRIVATE/distro-platform-v2.json" \
    --add "$R/settings.py=$S/settings.py" --add "$R/themes.py=$S/themes.py" --add "$R/sysinfo.py=$S/sysinfo.py" \
    --add "$R/themes/beaver.theme=build-themes/beaver.theme" --add "$R/flash.py=$S/flash.py" \
    --add "$R/image_import.py=$S/image_import.py" --add "$R/a22x_adapter.py=$S/a22x_adapter.py" \
    --add "$R/platform/a22x-wmt=helpers/a22x-wmt" --add "$R/platform/conn-start.sh=$S/platform/conn-start.sh" \
    --add "$R/platform/a22x-firstboot.sh=$S/platform/a22x-firstboot.sh" --add "$R/adbd.py=$S/adbd.py" --add "$R/handoff.py=$S/handoff.py" --add "$R/bootimage.py=$S/bootimage.py" --add "$R/install_plan.py=$S/install_plan.py" --add "$R/restart_intent.py=$S/restart_intent.py" --add "$R/lp_metadata.py=$S/lp_metadata.py" \
    --add "$R/samsung_firmware.py=$S/samsung_firmware.py" --add "$R/android_gsi.py=$S/android_gsi.py" --add "$R/android_rom.py=$S/android_rom.py" --add "$R/saved_boot.py=$S/saved_boot.py" --add "$R/zip_install.py=$S/zip_install.py" --add "$R/sdcard.py=$S/sdcard.py" --add "$R/sevenzip.py=$S/sevenzip.py" --add "$R/root.py=$S/root.py" --add "$R/clock.py=$S/clock.py" --add "$R/bcb.py=$S/bcb.py" --add "$R/android_log.py=$S/android_log.py" --replace "$R/management.py=$S/management.py" \
    --bytecode "$S/tools/python_bytecode_modules.txt" --link "$R/platform/busybox=/bin/busybox" \
    "${ADD[@]}" | tail -1 | cut -c1-90

python3 - "$NN" "$S" <<'P'
import gzip, importlib.util, struct, sys
from pathlib import Path
nn, src = sys.argv[1], Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('b', src / 'tools/build_v06_image.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
root = Path('build-v06-' + nn); img = root / 'recovery-a22x-mainline-k108-v0.6.img'
d = img.read_bytes()[:2048]; k, _, r = struct.unpack_from('<III', d, 8); page = struct.unpack_from('<I', d, 36)[0]
second = struct.unpack_from('<I', d, 24)[0]; dtbo = struct.unpack_from('<I', d, 1632)[0]; dtb = struct.unpack_from('<I', d, 1648)[0]
pad = lambda n: (n + page - 1) // page * page
print('free in partition', 41873408 - (page + pad(k) + pad(r) + pad(second) + pad(dtbo) + pad(dtb)), '(avb maximum 41873408)')
bad = []
for fields, n, *rest in b.read_cpio(gzip.decompress((root / 'ramdisk.cpio.gz').read_bytes())):
    if n.startswith('opt/a22x-recovery/') and n.endswith('.py'):
        f = src / n[len('opt/a22x-recovery/'):]
        if f.exists() and f.read_bytes() != bytes(rest[-1]):
            bad.append(n)
    if n == 'bin/recovery-ui' and bytes(rest[-1]) != (src / 'ui/a22x-recovery-ui-aarch64').read_bytes():
        bad.append(n)
print('image differs from source in:', bad)
sys.exit(1 if bad else 0)
P
sha256sum "build-v06-$NN/recovery-a22x-mainline-k108-v0.6.img"
