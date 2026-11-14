#!/bin/sh
# SPDX-License-Identifier: MIT
# fsck.f2fs and fsck.fat for the recovery (2026-11-13): Arch Linux ARM's own packages, each checked
# against its signature with the key in keys/archlinuxarm.asc and against the SHA-256 pinned here.
# The tools run with the glibc the recovery already carries (glibc-runtime, as make_f2fs does).
#
#   tools/fetch-fsck-tools.sh WORK      writes WORK/glibc-runtime/{bin,lib} and WORK/rt-wrappers
set -eu
WORK=${1:?usage: fetch-fsck-tools.sh WORK}
HERE=$(cd "$(dirname "$0")/.." && pwd)
MIRROR=${A22X_ALARM_MIRROR:-http://mirror.archlinuxarm.org/aarch64}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fetch() {  # repo name sha256
    curl -sSLf --max-time 180 -o "$T/$2" "$MIRROR/$1/$2"
    curl -sSLf --max-time 60 -o "$T/$2.sig" "$MIRROR/$1/$2.sig"
    echo "$3  $T/$2" | sha256sum -c - >/dev/null
    python3 -I - "$HERE" "$T/$2" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import openpgp
openpgp.verify_file(sys.argv[2], open(sys.argv[2] + '.sig', 'rb').read(), open(sys.argv[1] + '/keys/archlinuxarm.asc', 'rb').read())
EOF
    mkdir -p "$T/x" && tar -xJf "$T/$2" -C "$T/x" 2>/dev/null
}
fetch core dosfstools-4.2-5-aarch64.pkg.tar.xz c41120c6c89469f259d5248ea2ed0a7bea19f8a38d493682f7ff82037a862c21
fetch extra f2fs-tools-1.17.0-1-aarch64.pkg.tar.xz 12fd18e3ef163d4c2bdeb15a94c5574832700a9ce305eca32b0217fcab3d7c24
fetch core util-linux-libs-2.42.4-1-aarch64.pkg.tar.xz fc33959229b44ee81ab4c66177783b65cae4390868552a81493dc94be0cf9c60
mkdir -p "$WORK/glibc-runtime/bin" "$WORK/glibc-runtime/lib" "$WORK/rt-wrappers"
cp "$T/x/usr/bin/fsck.f2fs" "$T/x/usr/bin/fsck.fat" "$WORK/glibc-runtime/bin/"
cp "$T/x/usr/lib/libf2fs.so.11.0.0" "$WORK/glibc-runtime/lib/libf2fs.so.11"
cp "$T/x/usr/lib/libuuid.so.1.3.0" "$WORK/glibc-runtime/lib/libuuid.so.1"
for tool in fsck.f2fs fsck.fat; do
    printf '#!/bin/sh\nunset LD_LIBRARY_PATH\nG=/opt/a22x-recovery/glibc\nexec $G/lib/ld-linux-aarch64.so.1 --library-path $G/lib $G/bin/%s "$@"\n' "$tool" > "$WORK/rt-wrappers/$tool"
    chmod 755 "$WORK/rt-wrappers/$tool"
done
sha256sum "$WORK/glibc-runtime/bin/fsck.f2fs" "$WORK/glibc-runtime/bin/fsck.fat" "$WORK/glibc-runtime/lib/libf2fs.so.11" "$WORK/glibc-runtime/lib/libuuid.so.1"
