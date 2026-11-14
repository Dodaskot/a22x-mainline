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
# NN names the output folder, which is removed first: only a build number, with an optional plain
# suffix ("197-public"), so that it can never reach outside WORK (2026-11-12 audit: "1/../.." did).
if ! [[ "$NN" =~ ^[0-9]{1,5}(-[a-z0-9]{1,16})?$ ]]; then
    echo "build-image.sh: NN must be a build number such as 199 or 199-public, not '$NN'" >&2
    exit 2
fi
VERSION=${VERSION:-0.6.$NN}
SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=${A22X_RECOVERY_WORK:-$(dirname "$SRC")}
BASE=${BASE:-$WORK/base}
KERNEL_DIR=${KERNEL_DIR:-$WORK/kernel}
PRIVATE=${PRIVATE:-$WORK/platform-private}
S=${SRC#"$WORK"/}           # the source tree relative to WORK, as the image tool expects
if [ -z "$WORK" ] || [ "$WORK" = / ] || [ ! -d "$WORK" ] || [ "$SRC" = "$S" ]; then
    echo "build-image.sh: A22X_RECOVERY_WORK must be the folder that holds this source tree" >&2
    exit 2
fi
cd "$WORK"

ADD=()
add() { ADD+=(--add "$1=$2"); }
for f in glibc-runtime/lib/* glibc-runtime/bin/*; do add "opt/a22x-recovery/glibc/${f#glibc-runtime/}" "$f"; done
for t in make_f2fs lz4 unsquashfs fsck.f2fs fsck.fat; do add "bin/$t" "rt-wrappers/$t"; done
# The Brotli decoder for block-based ROM packages (*.new.dat.br): tools/brotli_dec.c with c/common and
# c/dec of google/brotli 1.1.0, built static for arm64 (2026-11-13). Without it such ROMs are refused.
if [ -f helpers/brotli-dec ]; then add bin/brotli-dec helpers/brotli-dec; fi
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
    add opt/a22x-recovery/archive_crypt.py "$S/archive_crypt.py"
    add opt/a22x-recovery/file_archive.py "$S/file_archive.py"
    add opt/a22x-recovery/linux_data.py "$S/linux_data.py"
    add opt/a22x-recovery/linux_data_restore.sh "$S/linux_data_restore.sh"
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
# What a former public build left is never taken for this build's (2026-11-14 review).
rm -f build-public-forbidden.txt
if [ "${PUBLIC:-}" = 1 ]; then
    : > build-no-keys
    ADD+=(--replace "etc/a22x-recovery/authorized_keys=build-no-keys")
    # 2026-11-13: the base's stock profile (the pinned DZC3 bundle) was qualified on the developer's
    # phone and names its eMMC (parent_cid_sha256). A public image gets it unqualified, without the
    # mapping of that phone's boot areas; Samsung firmware from its tar or ZIP does not use it.
    python3 - "$BASE" "$S" <<'Q'
import gzip, importlib.util, json, sys
from pathlib import Path
base, src = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('b', src / 'tools/build_v06_image.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
entries = {n: bytes(rest[-1]) for fields, n, *rest in b.read_cpio(gzip.decompress((base / 'ramdisk.cpio.gz').read_bytes()))}
profile = json.loads(entries['etc/a22x-recovery/install-stock-profile.json'])
cids = set()
def find(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == 'parent_cid_sha256' and isinstance(v, str):
                cids.add(v)
            find(v)
    elif isinstance(o, list):
        for v in o:
            find(v)
find(profile)
profile.update(qualified=False, blockers=['STOCK_PROFILE_OF_ANOTHER_PHONE'], protected_prefixes={})
Path('build-public-stock-profile.json').write_text(json.dumps(profile, indent=2, sort_keys=True) + '\n')
Path('build-public-forbidden.txt').write_text(''.join(c + '\n' for c in sorted(cids)))
Q
    ADD+=(--replace "etc/a22x-recovery/install-stock-profile.json=build-public-stock-profile.json")
    # The build records name this computer's folders; a public image says only /build.
    # Only folders of two levels or more: "/" or "/src" would replace far more than a folder name
    # (2026-11-14 review); build_v06_image.py refuses such a scrub itself too.
    for folder in "$(dirname "$WORK")" "$HOME"; do
        case "$folder" in /*/*) ADD+=(--scrub "$folder=/build") ;; esac
    done
fi
# The base image of 0.5 carries the qualification capsule of the phone it was made on, with a hash of
# that phone's eMMC identity. Linux snapshots no longer need it (backend/qualification.py
# built_in_admission), so it is left out unless CAPSULE names one.
[ -n "${CAPSULE:-}" ] || ADD+=(--remove "etc/a22x-recovery/linux-workflow-qualification.json")
# CAPSULE=path (relative to WORK): the Linux snapshot qualification of one phone and this exact build
# (backend/LINUX_WORKFLOW.md), made by backend/qualify_linux.py from a real snapshot of that phone. It
# names the phone's storage chip, so it never goes into a public image.
if [ -n "${CAPSULE:-}" ]; then
    if [ "${PUBLIC:-}" = 1 ]; then echo "build-image.sh: a qualification capsule belongs to one phone, not to a public image" >&2; exit 2; fi
    [ -f "$CAPSULE" ] || { echo "build-image.sh: no capsule at $WORK/$CAPSULE" >&2; exit 2; }
    ADD+=(--replace "etc/a22x-recovery/linux-workflow-qualification.json=$CAPSULE")
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
    --replace "$R/backend/common.py=$S/backend/common.py" --replace "$R/backend/storage.py=$S/backend/storage.py" --replace "$R/storage.py=$S/storage.py" \
    --replace "$R/backend/engine.py=$S/backend/engine.py" --replace "$R/backend/recoveryctl.py=$S/backend/recoveryctl.py" \
    --replace "$R/backend/qualification.py=$S/backend/qualification.py" \
    --replace "etc/a22x-recovery/distro-platform.json=$PRIVATE/distro-platform-v2.json" \
    --add "$R/settings.py=$S/settings.py" --add "$R/misc_store.py=$S/misc_store.py" --add "$R/themes.py=$S/themes.py" --add "$R/sysinfo.py=$S/sysinfo.py" \
    --add "$R/themes/beaver.theme=build-themes/beaver.theme" --add "$R/flash.py=$S/flash.py" \
    --add "$R/image_import.py=$S/image_import.py" --add "$R/a22x_adapter.py=$S/a22x_adapter.py" \
    --add "$R/platform/a22x-wmt=helpers/a22x-wmt" --add "$R/platform/conn-start.sh=$S/platform/conn-start.sh" \
    --add "$R/platform/a22x-firstboot.sh=$S/platform/a22x-firstboot.sh" --add "$R/adbd.py=$S/adbd.py" --add "$R/handoff.py=$S/handoff.py" --add "$R/bootimage.py=$S/bootimage.py" --add "$R/install_plan.py=$S/install_plan.py" --add "$R/restart_intent.py=$S/restart_intent.py" --add "$R/lp_metadata.py=$S/lp_metadata.py" --add "$R/dm_linear.py=$S/dm_linear.py" --add "$R/updates.py=$S/updates.py" \
    --add "$R/samsung_firmware.py=$S/samsung_firmware.py" --add "$R/android_gsi.py=$S/android_gsi.py" --add "$R/android_rom.py=$S/android_rom.py" --add "$R/saved_boot.py=$S/saved_boot.py" --add "$R/zip_install.py=$S/zip_install.py" --add "$R/sdcard.py=$S/sdcard.py" --add "$R/sevenzip.py=$S/sevenzip.py" --add "$R/root.py=$S/root.py" --add "$R/clock.py=$S/clock.py" --add "$R/bcb.py=$S/bcb.py" --add "$R/android_log.py=$S/android_log.py" --replace "$R/management.py=$S/management.py" --replace "$R/operation_lock.py=$S/operation_lock.py" \
    --bytecode "$S/tools/python_bytecode_modules.txt" --link "$R/platform/busybox=/bin/busybox" \
    "${ADD[@]}" | tail -1 | cut -c1-90

python3 - "$NN" "$S" <<'P'
import gzip, importlib.util, os, struct, sys
from pathlib import Path
nn, src = sys.argv[1], Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('b', src / 'tools/build_v06_image.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
root = Path('build-v06-' + nn); img = root / 'recovery-a22x-mainline-k108-v0.6.img'
with img.open('rb') as f:
    d = f.read(2048)  # the header only (2026-11-14 review: the whole 40 MiB were read)
k, _, r = struct.unpack_from('<III', d, 8); page = struct.unpack_from('<I', d, 36)[0]
second = struct.unpack_from('<I', d, 24)[0]; dtbo = struct.unpack_from('<I', d, 1632)[0]; dtb = struct.unpack_from('<I', d, 1648)[0]
pad = lambda n: (n + page - 1) // page * page
print('free in partition', 41873408 - (page + pad(k) + pad(r) + pad(second) + pad(dtbo) + pad(dtb)), '(avb maximum 41873408)')
bad = []
blob = gzip.decompress((root / 'ramdisk.cpio.gz').read_bytes())  # once, for both checks below
for fields, n, *rest in b.read_cpio(blob):
    if n.startswith('opt/a22x-recovery/') and n.endswith('.py'):
        f = src / n[len('opt/a22x-recovery/'):]
        if f.exists() and f.read_bytes() != bytes(rest[-1]):
            bad.append(n)
    if n == 'bin/recovery-ui' and bytes(rest[-1]) != (src / 'ui/a22x-recovery-ui-aarch64').read_bytes():
        bad.append(n)
print('image differs from source in:', bad)
# A public image must not carry the developer phone's eMMC identity anywhere (2026-11-13). Checked
# for every PUBLIC=1 build, whatever its name (2026-11-14 review), from this build's own list.
forbidden = Path('build-public-forbidden.txt')
if os.environ.get('PUBLIC') == '1':
    if not forbidden.exists():
        print('the list of the base phone identities was not made'); sys.exit(1)
    found = [c[:12] for c in forbidden.read_text().split() if c.encode() in blob]
    print('phone identities in the public image:', found)
    bad += found
sys.exit(1 if bad else 0)
P
sha256sum "build-v06-$NN/recovery-a22x-mainline-k108-v0.6.img"
