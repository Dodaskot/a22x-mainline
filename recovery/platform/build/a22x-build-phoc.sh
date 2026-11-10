#!/bin/sh
# SPDX-License-Identifier: MIT
# a22x-build-phoc.sh: give phoc the wlroots patch that phoc itself ships.
#
# Phoc builds its own wlroots with one patch ("Revert layer-shell: error on 0 dimension
# without anchors") until Phosh no longer needs it. Arch Linux ARM links phoc against its plain
# wlroots package instead, so Phosh's home bar ends every session with "height 0 requested
# without setting top and bottom anchors" (2026-11-09: phoc 0.57.0-2, wlroots0.20 0.20.2-1).
#
# This builds the installed wlroots version with that patch into /opt/a22x-phoc and writes
# /opt/a22x-phoc/bin/phoc, which starts the system's phoc with it. No package of the system is
# changed, so updates keep working, and the launcher returns to the system library as soon as
# that accepts Phosh's surfaces. The release tarball is checked against the signing key of
# wlroots; the build tools this needs are removed again afterwards.
#
# Run as root in the installed system or in the recovery's chroot of it, after an upgrade
# (pacman -Syu): installing the build tools must not be a partial upgrade.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/a22x-phoc
REJECT='height 0 requested without setting top and bottom anchors'
# What Arch builds wlroots with: base-devel (compiler, make, pkgconf, patch, gettext...) and the
# makedepends of its PKGBUILD; hwdata for the DRM backend.
TOOLS='base-devel meson ninja hwdata glslang vulkan-headers wayland-protocols xorg-xwayland'
log() { echo "a22x-build-phoc: $*"; }

[ "$(id -u)" = 0 ] || { log "run this as root"; exit 2; }
command -v pacman >/dev/null || { log "not a pacman system; nothing to do"; exit 0; }
[ -x /usr/bin/phoc ] || { log "phoc is not installed; nothing to do"; exit 0; }
# "	libwlroots-0.20.so => /usr/lib/libwlroots-0.20.so (0x...)"
soname=$(ldd /usr/bin/phoc | sed -n 's/^[[:space:]]*\(libwlroots[^ ]*\) => .*/\1/p' | head -1)
system=$(ldd /usr/bin/phoc | sed -n 's/^[[:space:]]*libwlroots[^ ]* => \([^ ]*\) .*/\1/p' | head -1)
if [ -z "$soname" ] || [ ! -f "$system" ]; then
    log "phoc does not use a shared wlroots; nothing to do"; exit 0
fi
if ! grep -aqF "$REJECT" "$system"; then
    log "$system already accepts Phosh's surfaces; nothing to do"
    exit 0
fi
owner=$(pacman -Qqo "$system")
version=$(pacman -Q "$owner" | awk '{print $2}' | sed 's/^[0-9]*://; s/-[^-]*$//')
case "$version" in
*[!0-9.]*|'') log "unexpected $owner version '$version'"; exit 1 ;;
esac
# --key: the build this system needs, for a cached one to be used instead (2026-11-09).
[ "${1:-}" != --key ] || { echo "$soname $version"; exit 0; }
if [ -f "$PREFIX/lib/$soname" ] && [ -x "$PREFIX/bin/phoc" ] &&
   [ "$(cat "$PREFIX/built-for" 2>/dev/null)" = "$soname $version" ]; then
    log "already built for $owner $version"
    exit 0
fi
log "building $owner $version with phoc's layer-shell patch"

work=$(mktemp -d /var/tmp/a22x-build-phoc.XXXXXX)
before=$work/before
pacman -Qq | sort > "$before"
added=
cleanup() {
    status=$?
    if [ -n "$added" ]; then
        # shellcheck disable=SC2086
        pacman -Rns --noconfirm $added >/dev/null 2>&1 || log "build tools left installed: $added"
    fi
    rm -rf "$work"
    exit $status
}
trap cleanup EXIT INT TERM
# shellcheck disable=SC2086
pacman -S --needed --noconfirm --asdeps $TOOLS
added=$(pacman -Qq | sort | comm -13 "$before" - | tr '\n' ' ')

url=https://gitlab.freedesktop.org/wlroots/wlroots/-/releases/$version/downloads/wlroots-$version.tar.gz
curl -fsSL --retry 5 --retry-delay 3 -o "$work/wlroots.tar.gz" "$url"
curl -fsSL --retry 5 --retry-delay 3 -o "$work/wlroots.tar.gz.sig" "$url.sig"
gpgv --keyring "$HERE/keys/wlroots.gpg" "$work/wlroots.tar.gz.sig" "$work/wlroots.tar.gz"
tar -xzf "$work/wlroots.tar.gz" -C "$work"
src=$work/wlroots-$version
[ -d "$src" ] || { log "the tarball has no wlroots-$version folder"; exit 1; }
patch -d "$src" -p1 --forward < "$HERE/wlroots-phoc-layer-shell.patch"

# The same features as the distribution's build (arch-meson enables every optional one).
jobs=$(nproc 2>/dev/null || echo 4)
# Warnings stay warnings: a newer compiler must not stop the build.
if ! meson setup "$work/build" "$src" --prefix=/usr --buildtype=release -Dwerror=false \
        -Dexamples=false --auto-features=enabled --wrap-mode=nodownload; then
    rm -rf "$work/build"
    meson setup "$work/build" "$src" --prefix=/usr --buildtype=release -Dwerror=false \
        -Dexamples=false --wrap-mode=nodownload
fi
ninja -C "$work/build" -j "$jobs"
lib=$work/build/$soname
[ -f "$lib" ] || { log "the build made no $soname"; exit 1; }

# Every wlroots function phoc calls must be in the new library.
nm -D --undefined-only /usr/bin/phoc | awk '{print $NF}' | sed 's/@.*//' | grep '^wlr_' | sort -u > "$work/needed"
nm -D --defined-only "$lib" | awk '{print $NF}' | sed 's/@.*//' | sort -u > "$work/defined"
missing=$(comm -23 "$work/needed" "$work/defined" | tr '\n' ' ')
[ -z "$missing" ] || { log "the new library lacks: $missing"; exit 1; }
grep -aqF "$REJECT" "$lib" && { log "the patch did not take effect"; exit 1; }

loader=$(readelf -l /usr/bin/phoc | sed -n 's/.*program interpreter: \(.*\)]$/\1/p')
[ -x "$loader" ] || { log "phoc's program interpreter '$loader' is missing"; exit 1; }
mkdir -p "$PREFIX/lib" "$PREFIX/bin"
cp "$lib" "$PREFIX/lib/$soname.new"
strip --strip-unneeded "$PREFIX/lib/$soname.new"
chmod 0644 "$PREFIX/lib/$soname.new"
mv -f "$PREFIX/lib/$soname.new" "$PREFIX/lib/$soname"
# The loader's --library-path reaches phoc only; Phosh and the apps it starts keep the
# system's libraries.
cat > "$PREFIX/bin/phoc.new" <<EOF
#!/bin/sh
# phoc with the wlroots patch phoc ships itself; written by a22x-build-phoc.sh for
# $owner $version. The system's library is used again once it accepts Phosh's surfaces.
if [ -f $PREFIX/lib/$soname ] && grep -aqF '$REJECT' $system 2>/dev/null; then
    exec $loader --library-path $PREFIX/lib --argv0 phoc /usr/bin/phoc "\$@"
fi
exec /usr/bin/phoc "\$@"
EOF
chmod 0755 "$PREFIX/bin/phoc.new"
mv -f "$PREFIX/bin/phoc.new" "$PREFIX/bin/phoc"
echo "$soname $version" > "$PREFIX/built-for"
# A library phoc no longer uses (phoc moved to a newer wlroots series) goes.
for old in "$PREFIX"/lib/libwlroots*.so*; do
    [ "$old" = "$PREFIX/lib/$soname" ] || rm -f "$old"
done
"$PREFIX/bin/phoc" --version >/dev/null
log "done: $PREFIX/lib/$soname for $owner $version"
