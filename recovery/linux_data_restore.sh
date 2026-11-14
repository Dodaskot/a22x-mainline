#!/bin/sh
# SPDX-License-Identifier: MIT
# Restores a "Linux data" archive made by the A22x Mainline Recovery (linux_data.py) into the
# running Linux system: accounts, home folders, settings and the list of packages. It does not
# need the recovery or the phone the archive came from: any Linux on ARM (or elsewhere) will do.
#
#   sudo sh restore.sh linux-data-NAME.tar.zst [--parts home,accounts,settings,packages]
#
# The recovery itself runs it inside the installed system as: restore.sh --staged DIR --home-done
set -u
PARTS=home,accounts,settings,packages
STAGED=
HOME_DONE=0
ARCHIVE=
CLEAN=0
ROOT=${A22X_ROOT:-}

usage() {
    echo "usage: sh restore.sh ARCHIVE.tar.zst [--parts home,accounts,settings,packages]"
    exit 1
}
while [ $# -gt 0 ]; do
    case $1 in
        --parts) PARTS=$2; shift 2 ;;
        --staged) STAGED=$2; shift 2 ;;
        --home-done) HOME_DONE=1; shift ;;
        -h|--help) usage ;;
        *) ARCHIVE=$1; shift ;;
    esac
done
say() { echo "a22x: $*"; }
want() { case ",$PARTS," in *",$1,"*) return 0 ;; esac; return 1; }

if [ "${A22X_FAKE_ROOT:-0}" != 1 ] && [ "$(id -u)" != 0 ]; then
    echo "Run this as root: sudo sh restore.sh ARCHIVE.tar.zst"
    exit 1
fi
if [ -z "$STAGED" ]; then
    [ -f "$ARCHIVE" ] || usage
    STAGED=$(mktemp -d /var/tmp/a22x-restore.XXXXXX) || exit 1
    CLEAN=1
    case $ARCHIVE in
        *.enc)
            # Protected with a password (archive_crypt.py): openssl opens it; the last 56 bytes are
            # the recovery's own check and are left out.
            command -v openssl >/dev/null 2>&1 || { echo "This archive is encrypted: install openssl, then run this again."; rm -rf "$STAGED"; exit 1; }
            command -v zstd >/dev/null 2>&1 || { echo "This archive needs zstd: install the package zstd, then run this again."; rm -rf "$STAGED"; exit 1; }
            if [ -z "${A22X_PASSWORD:-}" ]; then
                printf 'Password of the backup: '
                stty -echo 2>/dev/null
                # IFS= keeps spaces at either end: they are part of the password (2026-11-13 review).
                IFS= read -r A22X_PASSWORD
                stty echo 2>/dev/null
                echo
            fi
            export A22X_PASSWORD
            if ! head -c -56 "$ARCHIVE" | openssl enc -d -aes-256-ctr -pbkdf2 -iter 600000 -md sha256 -pass env:A22X_PASSWORD 2>/dev/null |
                    zstd -dc 2>/dev/null | tar -xpf - -C "$STAGED" --numeric-owner 2>/dev/null || [ ! -f "$STAGED/manifest.json" ]; then
                echo "The password does not open this archive, or the archive is damaged."
                rm -rf "$STAGED"
                exit 1
            fi
            ARCHIVE= ;;
    esac
    if [ -z "$ARCHIVE" ]; then :
    elif tar --zstd -xpf "$ARCHIVE" -C "$STAGED" --numeric-owner 2>/dev/null; then :
    elif command -v zstd >/dev/null 2>&1 && zstd -dc "$ARCHIVE" | tar -xpf - -C "$STAGED" --numeric-owner; then :
    else
        echo "This archive needs zstd: install the package zstd, then run this again."
        rm -rf "$STAGED"
        exit 1
    fi
fi
[ -f "$STAGED/manifest.json" ] || { echo "This is not a Linux data archive."; exit 1; }

# name|uid|gid|home|shell|admin|hash|groups
if want accounts && [ -f "$STAGED/accounts.tsv" ]; then
    while IFS='|' read -r name uid gid home shell admin hash groups; do
        [ -n "$name" ] || continue
        made=0
        if getent passwd "$name" >/dev/null 2>&1; then
            # An account that is already here keeps its own password and shell (2026-11-13, on the
            # phone: the password set at the installation was replaced by the archive's).
            say "account $name kept"
        else
            [ -x "$ROOT$shell" ] || { [ -x "$ROOT/bin/bash" ] && shell=/bin/bash || shell=/bin/sh; }
            if command -v useradd >/dev/null 2>&1; then
                if getent passwd "$uid" >/dev/null 2>&1; then useradd -m -s "$shell" "$name"
                else useradd -m -u "$uid" -s "$shell" "$name"; fi
            else
                adduser -D -s "$shell" "$name"
            fi || { say "account $name failed"; continue; }
            say "account $name created"
            made=1
        fi
        case $made$hash in
            0*|1|1-|'1!'*|'1*') ;;
            *) usermod -p "$hash" "$name" 2>/dev/null || echo "$name:$hash" | chpasswd -e 2>/dev/null ;;
        esac
        if [ "$admin" = 1 ]; then
            for g in wheel sudo; do
                getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$name" 2>/dev/null
            done
        fi
        if [ "$groups" != - ]; then
            for g in $(echo "$groups" | tr ',' ' '); do
                getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$name" 2>/dev/null
            done
        fi
    done < "$STAGED/accounts.tsv"
fi

if want home; then
    if [ "$HOME_DONE" = 0 ]; then
        if [ -d "$STAGED/home" ]; then
            mkdir -p "$ROOT/home" && cp -a "$STAGED/home/." "$ROOT/home/" && say "home copied"
            [ "$CLEAN" = 1 ] && rm -rf "$STAGED/home"
        fi
        if [ -d "$STAGED/root-home" ]; then
            mkdir -p "$ROOT/root" && cp -a "$STAGED/root-home/." "$ROOT/root/"
        fi
    fi
    # A home whose owner has another number in this system goes to the account of the same name.
    if [ -f "$STAGED/accounts.tsv" ]; then
        while IFS='|' read -r name uid gid home shell admin hash groups; do
            [ -n "$name" ] || continue
            now=$(id -u "$name" 2>/dev/null) || continue
            [ -d "$ROOT$home" ] || continue
            if [ "$now" != "$uid" ] || [ "$(id -g "$name" 2>/dev/null)" != "$gid" ]; then
                chown -R "$name:$(id -gn "$name")" "$ROOT$home" && say "owner $home"
            fi
        done < "$STAGED/accounts.tsv"
    fi
fi

if want settings && [ -d "$STAGED/settings" ]; then
    count=0
    for f in etc/hostname etc/locale.conf etc/default/locale etc/vconsole.conf etc/timezone etc/localtime; do
        if [ -e "$STAGED/settings/$f" ] || [ -L "$STAGED/settings/$f" ]; then
            mkdir -p "$ROOT/$(dirname "$f")"
            rm -f "$ROOT/$f"
            cp -a "$STAGED/settings/$f" "$ROOT/$f" && count=$((count + 1))
        fi
    done
    # Only for a program this system has: its folder is the sign.
    for pair in etc/NetworkManager/system-connections:etc/NetworkManager var/lib/iwd:var/lib/iwd \
                var/lib/bluetooth:var/lib/bluetooth etc/ssh:etc/ssh; do
        d=${pair%%:*}
        sign=${pair##*:}
        if [ -d "$STAGED/settings/$d" ] && [ -d "$ROOT/$sign" ]; then
            mkdir -p "$ROOT/$d" && cp -a "$STAGED/settings/$d/." "$ROOT/$d/" && count=$((count + 1))
        fi
    done
    say "settings $count"
fi

if want packages && [ -f "$STAGED/packages.txt" ]; then
    ID=
    ID_LIKE=
    [ -f "$ROOT/etc/os-release" ] && . "$ROOT/etc/os-release"
    case " $ID $ID_LIKE " in
        *" arch "*|*archarm*|*manjaro*) target=arch ;;
        *debian*|*ubuntu*) target=debian ;;
        *fedora*|*rhel*|*centos*) target=fedora ;;
        *suse*) target=suse ;;
        *alpine*|*postmarketos*) target=alpine ;;
        *) target=unknown ;;
    esac
    source=$(cat "$STAGED/source-family" 2>/dev/null)
    col() { case $1 in arch) echo 1 ;; debian) echo 2 ;; fedora) echo 3 ;; *) echo 0 ;; esac; }
    names=$(awk -F'|' -v s="$(col "$source")" -v t="$(col "$target")" '
        NR == FNR { if (s && t && $s != "-" && $t != "-") m[$s] = $t; next }
        { print (($1 in m) ? m[$1] : $1) }' "$STAGED/aliases.tsv" "$STAGED/packages.txt" | sort -u)
    case $target in
        arch)
            have() { pacman -Si "$1" >/dev/null 2>&1; }
            add() { pacman -S --noconfirm --needed "$@"; }
            has() { pacman -Qq "$1" >/dev/null 2>&1; }
            # The system is brought up to date once, before any package: Arch does not support a
            # partial upgrade (2026-11-13 review). Only when a package is to be installed, never as a
            # side effect of a list already there (2026-11-14 review). When it cannot go through,
            # nothing is installed onto an older system, and the result says why.
            prepare() { pacman -Syu --noconfirm >/dev/null 2>&1 || { say "packages upgrade failed"; return 1; }; } ;;
        debian)
            DEBIAN_FRONTEND=noninteractive
            export DEBIAN_FRONTEND
            have() { apt-cache show "$1" 2>/dev/null | grep -q '^Package:'; }
            # Never at the price of what is installed (2026-11-13, on the phone: dhcpcd and netctl from
            # an Arch list took network-manager and phosh-core away).
            add() { apt-get install -y -q --no-install-recommends --no-remove "$@"; }
            has() { dpkg -s "$1" 2>/dev/null | grep -q '^Status: install ok installed'; }
            prepare() { apt-get update -q >/dev/null 2>&1; return 0; } ;;
        fedora)
            have() { dnf -q info "$1" >/dev/null 2>&1; }
            add() { dnf -y install "$@"; }
            has() { rpm -q "$1" >/dev/null 2>&1; }
            prepare() { return 0; } ;;
        suse)
            have() { zypper -n info "$1" 2>/dev/null | grep -q '^Name'; }
            add() { zypper -n install --no-force-resolution "$@"; }
            has() { rpm -q "$1" >/dev/null 2>&1; }
            prepare() { return 0; } ;;
        alpine)
            have() { apk info -e "$1" >/dev/null 2>&1 || apk search -x "$1" 2>/dev/null | grep -q .; }
            add() { apk add "$@"; }
            has() { apk info -e "$1" >/dev/null 2>&1; }
            prepare() { apk update >/dev/null 2>&1; return 0; } ;;
        *)
            say "packages unsupported"
            names= ;;
    esac
    ok=
    want=
    installed=0
    missing=0
    for n in $names; do
        # The kernel, firmware, boot and base of the system the archive came from stay there.
        case $n in
            linux|linux-*|*-keyring|base|base-files|grub*|mkinitcpio*|dracut*|systemd|systemd-*|uboot-*|zram-generator|dhcpcd|netctl) continue ;;
        esac
        if has "$n"; then installed=$((installed + 1)); else want="$want $n"; fi
    done
    # The package lists are made ready (Arch: upgraded) only when something is missing. When that
    # fails, what is missing is counted apart: not installed now, though the system has it
    # (2026-11-14 reviews: counted as none, then as not in this system).
    if [ -n "$want" ] && ! prepare; then
        say "packages-skipped $(echo $want | wc -w)"
        want=
    fi
    for n in $want; do
        if have "$n"; then ok="$ok $n"
        else missing=$((missing + 1)); say "missing $n"; fi
    done
    if [ -n "$ok" ]; then
        say "installing $(echo $ok | wc -w)"
        # One that conflicts must not keep the others out.
        add $ok >/dev/null 2>&1 || for n in $ok; do add "$n" >/dev/null 2>&1; done
        for n in $ok; do
            if has "$n"; then installed=$((installed + 1)); else missing=$((missing + 1)); say "missing $n"; fi
        done
    fi
    [ "$target" = unknown ] || say "packages $installed $missing"
fi

[ "$CLEAN" = 1 ] && rm -rf "$STAGED"
say "done"
exit 0
