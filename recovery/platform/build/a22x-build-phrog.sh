#!/bin/sh
# SPDX-License-Identifier: MIT
# a22x-build-phrog.sh: build Phrog, the touch login screen made of Phosh, for a system whose
# distribution has no package of it.
#
# Debian, Ubuntu and Fedora package Phrog; Arch Linux ARM does not, and the AUR has nothing
# either (2026-11-09). Phrog drives Phosh's lock screen through libphosh, a library that
# distributions do not build, so this builds libphosh from the installed Phosh version and
# Phrog 0.53.0 against it, and installs both under /usr/local with the files a greeter session
# needs (the same layout as the distribution packages, so the A22x greeter treats both alike).
#
# Phosh's tarball is checked against its maintainer's signing key, Phrog's archive by its
# SHA-256 and every crate by Phrog's Cargo.lock. The build tools are removed afterwards. It
# takes 30-40 minutes on this phone and needs Phosh installed (its libraries and data).
#
# Run as root after an upgrade (pacman -Syu): installing the build tools must not be a partial
# upgrade.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PHROG_VERSION=0.53.0
PHROG_SHA256=42d3b9153d60cbfc130d19e3531776887755cba9ca5734c4c4e21ec6944d1197
PHROG_URL=https://github.com/samcday/phrog/archive/refs/tags/$PHROG_VERSION.tar.gz
LIBDIR=/usr/local/lib/a22x-phrog
# What Arch builds Phosh with (base-devel and the makedepends of its PKGBUILD), and Rust.
TOOLS='base-devel meson ninja rust glib2-devel wayland-protocols'
log() { echo "a22x-build-phrog: $*"; }

[ "$(id -u)" = 0 ] || { log "run this as root"; exit 2; }
command -v pacman >/dev/null || { log "not a pacman system"; exit 1; }
phosh=$(pacman -Q phosh 2>/dev/null | awk '{print $2}' | sed 's/^[0-9]*://; s/-[^-]*$//')
case "$phosh" in
*[!0-9.]*|'') log "Phosh is not installed (Phrog is built from its library)"; exit 1 ;;
esac
# --key: the build this system needs, for a cached one to be used instead (2026-11-09).
[ "${1:-}" != --key ] || { echo "phrog $PHROG_VERSION phosh $phosh"; exit 0; }
if [ -x /usr/local/bin/phrog ] && [ "$(cat "$LIBDIR/built-for" 2>/dev/null)" = "phrog $PHROG_VERSION phosh $phosh" ]; then
    log "already built: Phrog $PHROG_VERSION with Phosh $phosh"
    exit 0
fi
log "building Phrog $PHROG_VERSION with libphosh from Phosh $phosh"

work=$(mktemp -d /var/tmp/a22x-build-phrog.XXXXXX)
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

url=https://sources.phosh.mobi/releases/phosh/phosh-$phosh.tar.xz
curl -fsSL --retry 5 --retry-delay 3 -o "$work/phosh.tar.xz" "$url"
curl -fsSL --retry 5 --retry-delay 3 -o "$work/phosh.tar.xz.asc" "$url.asc"
gpgv --keyring "$HERE/keys/phosh.gpg" "$work/phosh.tar.xz.asc" "$work/phosh.tar.xz"
curl -fsSL --retry 5 --retry-delay 3 -o "$work/phrog.tar.gz" "$PHROG_URL"
echo "$PHROG_SHA256  $work/phrog.tar.gz" | sha256sum -c - >/dev/null || { log "Phrog's archive is not the expected one"; exit 1; }
tar -xJf "$work/phosh.tar.xz" -C "$work"
tar -xzf "$work/phrog.tar.gz" -C "$work"
psrc=$work/phosh-$phosh
rsrc=$work/phrog-$PHROG_VERSION
[ -d "$psrc" ] && [ -d "$rsrc" ] || { log "unexpected archive layout"; exit 1; }

# glibc 2.43 returns a const pointer from strrchr() for a const string (Phosh 0.57.0, src/util.c).
sed -i 's/^\([[:space:]]*\)char \*last_component;/\1const char *last_component;/' "$psrc/src/util.c"
# Phrog's Rust bindings are generated already; the GObject introspection data that
# bindings-lib otherwise also builds is not needed (it would need gobject-introspection).
sed -i 's/^if enable_introspection or bindings_lib$/if enable_introspection/' "$psrc/src/meson.build"
# Phrog lists the sessions in hash order, which changes from start to start; sorted by name the
# list stays put (2026-11-09).
sed -i 's/^    sessions\.values()\.cloned()\.collect()$/    let mut list: Vec<SessionObject> = sessions.values().cloned().collect();\n    list.sort_by_key(|s| s.name());\n    list/' "$rsrc/src/sessions.rs"
grep -q 'list.sort_by_key' "$rsrc/src/sessions.rs" || log "sessions stay in Phrog's own order (no sort patch for this version)"
api=$(sed -n "s/^libphosh_api_version = '\([0-9.]*\)'.*/\1/p" "$psrc/meson.build")
[ -n "$api" ] || { log "no libphosh API version in Phosh $phosh"; exit 1; }
# --prefix=/usr: the library then finds the installed Phosh's plugins, translations and data.
meson setup "$work/pbuild" "$psrc" --prefix=/usr --buildtype=release -Dwerror=false --wrap-mode=nodownload \
    -Dbindings-lib=true -Dintrospection=false -Dvapi=false -Dtests=false -Dphoc_tests=disabled \
    -Dgtk_doc=false -Dman=false -Dtools=false -Dsearchd=false -Dabi-check=false -Dcallui-i18n=false \
    -Dlockscreen-plugins=false -Dquick-setting-plugins=false -Dstatus-icon-plugins=false
jobs=$(nproc 2>/dev/null || echo 4)
ninja -C "$work/pbuild" -j "$jobs" "src/libphosh-$api.so.0"
lib=$work/pbuild/src/libphosh-$api.so.0
[ -f "$lib" ] || { log "the build made no libphosh-$api.so.0"; exit 1; }
[ -e "$work/pbuild/src/libphosh-$api.so" ] || ln -s "libphosh-$api.so.0" "$work/pbuild/src/libphosh-$api.so"

# Phrog finds the library through meson's uninstalled pkg-config files. Rust needs up to about
# 1.5 GB per compiler process, so fewer jobs than cores.
cargo_jobs=$(( jobs > 4 ? 4 : jobs ))
(
    cd "$rsrc"
    export CARGO_HOME="$work/cargo" HOME="$work/home" CARGO_TARGET_DIR="$work/target"
    export PKG_CONFIG_PATH="$work/pbuild/meson-uninstalled"
    export RUSTFLAGS="-C link-arg=-Wl,-rpath,$LIBDIR -L native=$work/pbuild/src"
    mkdir -p "$HOME"
    cargo build --release --locked -j "$cargo_jobs"
)
bin=$work/target/release/phrog
[ -x "$bin" ] || { log "the build made no phrog"; exit 1; }
LD_LIBRARY_PATH="$work/pbuild/src" ldd "$bin" | grep -q 'not found' && { log "phrog lacks libraries"; exit 1; }

mkdir -p "$LIBDIR" /usr/local/bin /usr/local/share/glib-2.0/schemas /usr/local/share/gnome-session/sessions \
    /usr/local/share/applications /usr/local/lib/systemd/user/gnome-session@phrog.target.d
cp "$lib" "$LIBDIR/libphosh-$api.so.0.new"
strip --strip-unneeded "$LIBDIR/libphosh-$api.so.0.new"
mv -f "$LIBDIR/libphosh-$api.so.0.new" "$LIBDIR/libphosh-$api.so.0"
cp "$bin" /usr/local/bin/phrog.new
strip --strip-unneeded /usr/local/bin/phrog.new
mv -f /usr/local/bin/phrog.new /usr/local/bin/phrog
d=$rsrc/data
cp "$d/mobi.phosh.phrog.gschema.xml" "$d/00_mobi.phosh.Phrog.gschema.override" /usr/local/share/glib-2.0/schemas/
glib-compile-schemas /usr/local/share/glib-2.0/schemas
cp "$d/phrog.session" /usr/local/share/gnome-session/sessions/phrog.session
sed 's#^Exec=phrog$#Exec=/usr/local/bin/phrog#' "$d/mobi.phosh.Phrog.desktop" > /usr/local/share/applications/mobi.phosh.Phrog.desktop
sed 's#^ExecStart=phrog$#ExecStart=/usr/local/bin/phrog#' "$d/mobi.phosh.Phrog.service" > /usr/local/lib/systemd/user/mobi.phosh.Phrog.service
cp "$d/mobi.phosh.Phrog.target" /usr/local/lib/systemd/user/mobi.phosh.Phrog.target
cp "$d/systemd-session.conf" /usr/local/lib/systemd/user/gnome-session@phrog.target.d/session.conf
echo "phrog $PHROG_VERSION phosh $phosh" > "$LIBDIR/built-for"
/usr/local/bin/phrog --version >/dev/null
log "done: /usr/local/bin/phrog $PHROG_VERSION with $LIBDIR/libphosh-$api.so.0"
