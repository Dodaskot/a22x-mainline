#!/bin/sh
# Runs once in a newly installed system: joins Wi-Fi, installs the chosen desktop and
# apps, creates the chosen account and runs the chosen setup script. A failure leaves the
# console system usable and is retried on the next boot, because the configuration stays
# until success. The setup script runs only once.
# With A22X_CHROOT=1 the recovery runs it in the new root before the first start, over its own
# Wi-Fi (2026-11-08): the network is already there, services are not started, and nothing
# reboots. A failure exits 3 and keeps the configuration, so the first start finishes the job.
set -u
# Files written here are read by the greeter and the desktop, not only by root.
umask 022
export DEBIAN_FRONTEND=noninteractive
CONF=/etc/a22x/firstboot.conf
# No package starts its service while this script installs: NetworkManager started by its own
# package took wlan0 from the Wi-Fi this script had joined, mid-download, and both then failed
# (2026-11-09, Ubuntu 26.04 + Lomiri from SD). The reboot at the end starts everything. In
# recovery's chroot the recovery writes this file itself.
if [ -z "${A22X_CHROOT:-}" ] && [ -d /usr/sbin ] && [ ! -e /usr/sbin/policy-rc.d ] && [ ! -L /usr/sbin/policy-rc.d ]; then
    printf '#!/bin/sh\n# a22x-firstboot: no service starts while packages install.\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod 755 /usr/sbin/policy-rc.d
    trap 'grep -q a22x-firstboot /usr/sbin/policy-rc.d 2>/dev/null && rm -f /usr/sbin/policy-rc.d' EXIT
fi
. "$CONF"
LOG=/var/log/a22x-firstboot.log
B=/opt/a22x-conn/busybox
W=/opt/a22x-conn/wifi
# A line of the setup goes once into its log and once to whoever runs it (the recovery reads them
# for its progress), also from functions whose output already goes to the log (2026-11-09).
exec 3>&1
if [ -n "${A22X_CHROOT:-}" ]; then
    say() { echo "a22x-firstboot: $*" >> "$LOG"; echo "a22x-firstboot: $*" >&3; }
else
    say() { echo "a22x-firstboot: $*" | tee -a "$LOG" > /dev/tty1; }
fi

# Manjaro ARM images run their own setup as "oem", who has root without a password.
oem_cleanup() {
    getent passwd oem >/dev/null && [ -f /etc/sudoers.d/g_oem ] || return 0
    rm -f /etc/sudoers.d/g_oem
    usermod -L -s /usr/bin/nologin oem 2>/dev/null || usermod -L oem
}

account() {
    oem_cleanup
    [ -n "${NEW_USER:-}" ] || return 0
    if ! id "$NEW_USER" >/dev/null 2>&1; then
        # A clean install makes it the first account (UID 1000), as on the phone ports.
        uid=
        [ -n "${PRIMARY_USER:-}" ] && ! getent passwd 1000 >/dev/null && uid="-u 1000"
        useradd -m -s /bin/bash $uid "$NEW_USER" || return 1
    fi
    home=$(getent passwd "$NEW_USER" | cut -d: -f6)
    if [ -n "$home" ] && [ ! -d "$home" ]; then
        mkdir -p "$home" && cp -a /etc/skel/. "$home"/ 2>/dev/null
        chown -R "$NEW_USER": "$home" && chmod 700 "$home"
    fi
    for group in wheel sudo video input render audio; do
        getent group "$group" >/dev/null && usermod -aG "$group" "$NEW_USER"
    done
    [ -z "${NEW_PASSWORD:-}" ] || set_password "$NEW_USER" "$NEW_PASSWORD" || return 1
    # A password prepared in the Android app comes as a SHA-512 hash (2026-11-10).
    [ -z "${NEW_PASSWORD_HASH:-}" ] || printf '%s:%s\n' "$NEW_USER" "$NEW_PASSWORD_HASH" | chpasswd -e || return 1
    # postmarketOS starts its phone shell as /etc/default_user (the recovery writes it for the
    # shared accounts); the account made here is the phone's user.
    if grep -qs /etc/default_user /usr/lib/systemd/system-generators/*; then
        printf '%s\n' "$NEW_USER" > /etc/default_user
    fi
    [ -n "${PRIMARY_USER:-}" ] && [ -n "${NEW_PASSWORD:-}${NEW_PASSWORD_HASH:-}" ] || return 0
    # An image's root password is a published default or empty; a locked one stays locked.
    case "$(getent shadow root | cut -d: -f2)" in
    '!'*|'*'*) ;;
    *) if [ -n "${NEW_PASSWORD:-}" ]; then set_password root "$NEW_PASSWORD"
       else printf 'root:%s\n' "$NEW_PASSWORD_HASH" | chpasswd -e; fi ;;
    esac
}

# The account lives on in the shared /home and is copied into every system installed beside, so
# its password is stored as SHA-512 ($6$), which glibc and musl both read. The distributions'
# default yescrypt ($y$) locked the account out of postmarketOS, whose musl knows no yescrypt
# (2026-11-10). shadow's chpasswd names the method SHA512, busybox's sha512.
set_password() {
    for method in SHA512 sha512; do
        printf '%s:%s\n' "$1" "$2" | chpasswd -c "$method" 2>/dev/null && return 0
    done
    printf '%s:%s\n' "$1" "$2" | chpasswd
}

ipcmd() {
    if command -v ip >/dev/null; then ip "$@"; else $B ip "$@"; fi
}

# NetworkManager drives Wi-Fi only through a supplicant of the system's own. Cloud images ship
# NetworkManager without one; then the program kept in recovery joins the network instead.
# NetworkManager drives Wi-Fi only with its Wi-Fi plugin and a supplicant; Fedora Cloud has
# NetworkManager without either (2026-11-08).
nm_wifi_plugin() {
    for plugin in /usr/lib*/NetworkManager/*/libnm-device-plugin-wifi.so \
                  /usr/lib/*/NetworkManager/*/libnm-device-plugin-wifi.so; do
        [ -e "$plugin" ] && return 0
    done
    return 1
}

nm_has_wifi() {
    command -v NetworkManager >/dev/null || return 1
    # A running NetworkManager says itself whether wlan0 is its Wi-Fi device. Only wlan0
    # counts: without the plugin it still listed p2p0 as Wi-Fi (2026-11-08, Fedora Generic).
    if command -v nmcli >/dev/null &&
       nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | grep '^wlan0:wifi:' | grep -vq ':unmanaged$'; then
        return 0
    fi
    nm_wifi_plugin || return 1
    command -v wpa_supplicant >/dev/null || [ -x /usr/lib/iwd/iwd ] || [ -x /usr/libexec/iwd ]
}

online() {
    ipcmd route show default 2>/dev/null | grep wlan0 | grep -qv linkdown
}

# NetworkManager joins its saved networks by itself. If it has not within a minute, nmcli
# joins the network typed in recovery and picks its security (WPA2 or WPA3) from the scan.
# Returns 3 when NetworkManager has no Wi-Fi device for wlan0: then it can neither scan nor
# join, and the Wi-Fi program kept in recovery does it instead.
nm_wifi() {
    systemctl start NetworkManager.service 2>/dev/null
    n=0
    while [ $n -lt 20 ]; do
        online && return 0
        [ $n -ge 5 ] && ! nmcli -t -f DEVICE,TYPE device 2>/dev/null | grep -qx 'wlan0:wifi' && return 3
        sleep 3; n=$((n + 1))
    done
    [ -n "${WIFI_SSID:-}" ] || return 1
    nmcli device wifi rescan ifname wlan0 >/dev/null 2>&1; sleep 5
    # nmcli -t escapes ":" and "\\"; the plain names are compared.
    seen=$(nmcli -t -f SSID device wifi list ifname wlan0 2>/dev/null | sed 's/\\:/:/g; s/\\\\/\\/g' | sort -u)
    echo "networks seen:"; printf '%s\n' "$seen"
    ssid=$WIFI_SSID hidden=
    if ! printf '%s\n' "$seen" | grep -qxF -- "$WIFI_SSID"; then
        # A hotspot whose name ended in a space was typed without it (2026-11-08): a visible name
        # that differs only in case or surrounding spaces is taken instead.
        key=$(printf '%s' "$WIFI_SSID" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
        near=$(printf '%s\n' "$seen" | while IFS= read -r name; do
            [ "$(printf '%s' "$name" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')" = "$key" ] && printf '%s\n' "$name"
        done)
        if [ -n "$near" ] && [ "$(printf '%s\n' "$near" | wc -l)" -eq 1 ]; then
            ssid=$near
            say "Wi-Fi: using the visible network '$ssid' for '$WIFI_SSID'"
            nmcli connection modify id "$WIFI_SSID" 802-11-wireless.ssid "$ssid" 2>/dev/null &&
                nmcli --wait 60 connection up id "$WIFI_SSID" && online && return 0
        else
            say "Wi-Fi: no network named '$WIFI_SSID' is visible; trying it as a hidden network"
            hidden="hidden yes"
        fi
    fi
    if [ -n "${WIFI_PASSWORD:-}" ]; then
        nmcli --wait 60 device wifi connect "$ssid" password "$WIFI_PASSWORD" ifname wlan0 $hidden
    else
        nmcli --wait 60 device wifi connect "$ssid" ifname wlan0 $hidden
    fi
    n=0
    while [ $n -lt 10 ]; do online && return 0; sleep 3; n=$((n + 1)); done
    say "Wi-Fi: could not join '$WIFI_SSID'; check the network name and password"
    return 1
}

wifi() {
    [ -z "${A22X_CHROOT:-}" ] || return 0  # The recovery is on Wi-Fi.
    for radio in /sys/class/rfkill/rfkill*; do
        [ "$(cat "$radio/type" 2>/dev/null)" = wlan ] && echo 0 > "$radio/soft"
    done
    n=0
    while [ ! -d /sys/class/net/wlan0 ] && [ $n -lt 30 ]; do sleep 2; n=$((n + 1)); done
    # Where NetworkManager exists it alone drives wlan0: a second wpa_supplicant beside it
    # makes both fail (2026-11-08, clean Mobian install).
    if nm_has_wifi; then
        # A program of an earlier try would fight NetworkManager for wlan0.
        systemctl stop a22x-firstboot-wifi.service a22x-firstboot-dhcp.service 2>/dev/null
        nm_wifi
        rc=$?
        [ $rc -eq 3 ] || return $rc
        say "Wi-Fi: NetworkManager has no Wi-Fi device; using the recovery's Wi-Fi program"
    fi
    [ -f /etc/a22x/wpa.conf ] || return 1
    ipcmd link set wlan0 up
    if command -v wpa_supplicant >/dev/null; then
        run=$(command -v wpa_supplicant)
    elif [ -x $W/ld-linux-aarch64.so.1 ]; then
        run="$W/ld-linux-aarch64.so.1 --library-path $W $W/wpa_supplicant"
    elif [ -x $W/wpa_supplicant ]; then
        run=$W/wpa_supplicant
    else
        return 1
    fi
    # Transient units outlive this service: a failed download keeps the network for the retry
    # instead of losing it with this service (Fedora GCE image, 2026-11-08).
    if command -v systemd-run >/dev/null 2>&1; then
        systemctl stop a22x-firstboot-wifi.service a22x-firstboot-dhcp.service 2>/dev/null
        systemd-run --unit=a22x-firstboot-wifi --collect $run -i wlan0 -c /etc/a22x/wpa.conf -D nl80211 || return 1
    else
        $run -B -i wlan0 -c /etc/a22x/wpa.conf -D nl80211 || return 1
    fi
    cat > /opt/a22x-conn/udhcpc.script <<'S'
#!/opt/a22x-conn/busybox sh
[ "$1" = bound ] || [ "$1" = renew ] || exit 0
/opt/a22x-conn/busybox ifconfig "$interface" "$ip" netmask "$subnet"
for r in $router; do /opt/a22x-conn/busybox route add default gw "$r" dev "$interface"; done
rm -f /etc/resolv.conf
for d in $dns; do echo "nameserver $d"; done > /etc/resolv.conf
# Fedora resolves names through systemd-resolved, which does not read this file (2026-11-08).
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns "$interface" $dns
    resolvectl domain "$interface" "~."
fi
S
    chmod 755 /opt/a22x-conn/udhcpc.script
    $B udhcpc -i wlan0 -n -q -t 20 -s /opt/a22x-conn/udhcpc.script || return 1
    # The lease is renewed for as long as the downloads take.
    if command -v systemd-run >/dev/null 2>&1; then
        systemd-run --unit=a22x-firstboot-dhcp --collect $B udhcpc -i wlan0 -f -s /opt/a22x-conn/udhcpc.script >/dev/null
    fi
}

# A step that needs the network failed: the setup runs again in ten minutes, without a reboot.
retry_later() {
    if [ -n "${A22X_CHROOT:-}" ]; then
        say "$1; the system finishes this at its first start"
        [ -z "${initramfs_deferred:-}" ] ||
            sed -i 's/^update_initramfs=no/update_initramfs=yes/' /etc/initramfs-tools/update-initramfs.conf
        exit 3
    fi
    say "$1; trying again in 10 minutes"
    if command -v systemd-run >/dev/null 2>&1; then
        systemctl stop a22x-firstboot-retry.timer 2>/dev/null
        systemd-run --on-active=600 --unit=a22x-firstboot-retry --collect systemctl start a22x-firstboot.service >/dev/null
    fi
    exit 0
}

# Package managers can need more memory than this phone has; a bare image has no swap. The swap
# file takes only what the packages leave, at most 4 GiB: a fixed 4 GiB on a nearly full userdata
# left apt no room for the desktop it was fetching (Debian 13 with GNOME, 2026-11-10).
swap_on() {
    [ -n "$(swapon --show --noheadings 2>/dev/null)" ] && return 0
    free=$(df -Pk /var 2>/dev/null | awk 'NR == 2 {print int($4 / 1024)}')
    size=$(( ${free:-0} - 5120 ))
    [ "$size" -le 4096 ] || size=4096
    if [ "$size" -lt 512 ]; then
        say "no temporary swap: the packages need the free space"
        return 1
    fi
    if dd if=/dev/zero of=/var/a22x-swap bs=1M count="$size" 2>/dev/null && chmod 600 /var/a22x-swap &&
        mkswap /var/a22x-swap >/dev/null && swapon /var/a22x-swap; then
        return 0
    fi
    rm -f /var/a22x-swap  # A file cut short by a full disk would keep the space.
    return 1
}

swap_off() {
    [ -e /var/a22x-swap ] || return 0
    swapoff /var/a22x-swap 2>/dev/null
    rm -f /var/a22x-swap
}

# Packages of one shell for one package family. The sign-in screen brings its own (2026-11-09).
shell_packages() {
    case "$1:$2" in
    phosh:apt) echo "phosh-core network-manager wpasupplicant libgl1-mesa-dri dbus-user-session" ;;
    phosh:dnf) echo "phosh NetworkManager-wifi mesa-dri-drivers" ;;
    phosh:pacman) echo "phosh phoc phosh-mobile-settings stevia networkmanager wpa_supplicant mesa gnome-control-center gnome-console" ;;
    phosh:zypper) echo "phosh phoc squeekboard NetworkManager wpa_supplicant Mesa-dri gnome-control-center gnome-console" ;;
    gnome:apt) echo "gnome-core network-manager" ;;
    gnome:dnf) echo "@gnome-desktop NetworkManager-wifi" ;;
    # GNOME's core, not the "gnome" group: the group's apps (gnome-books, gnome-boxes via tracker,
    # webkit2gtk) needed libsoup, which Arch Linux ARM no longer had, and that one failure stopped
    # the whole install, Phosh included (2026-11-09).
    gnome:pacman) echo "gnome-shell gnome-session gnome-settings-daemon gnome-control-center gnome-console nautilus gnome-keyring xdg-desktop-portal-gnome networkmanager" ;;
    gnome:zypper) echo "patterns-gnome-gnome_basic NetworkManager" ;;
    plasma:apt) echo "plasma-mobile network-manager" ;;
    plasma:dnf) echo "plasma-mobile NetworkManager-wifi" ;;
    plasma:pacman) echo "plasma-mobile networkmanager" ;;
    plasma:zypper) echo "plasma6-mobile NetworkManager" ;;
    # Xfce draws with X11: its X server and startx come along.
    xfce:apt) echo "xfce4 network-manager xserver-xorg-core xserver-xorg-input-libinput xinit" ;;
    xfce:dnf) echo "@xfce-desktop-environment NetworkManager-wifi xorg-x11-xinit" ;;
    xfce:pacman) echo "xfce4 networkmanager xorg-server xorg-xinit" ;;
    xfce:zypper) echo "patterns-xfce-xfce NetworkManager xinit" ;;
    # Sxmo (on Sway) and Lomiri are packaged by Debian and Ubuntu only (2026-11-08).
    sxmo:apt) echo "sxmo-utils network-manager wpasupplicant libgl1-mesa-dri dbus-user-session" ;;
    lomiri:apt) echo "lomiri-desktop-session network-manager wpasupplicant libgl1-mesa-dri dbus-user-session" ;;
    esac
}

# What a shell may also have: each package is installed when the distribution has it.
shell_extras() {
    case "$1:$2" in
    phosh:apt) echo "adwaita-icon-theme librsvg2-common gnome-backgrounds fonts-cantarell phosh-mobile-settings gnome-control-center gnome-console stevia squeekboard" ;;
    sxmo:apt) echo "fonts-noto-color-emoji wvkbd" ;;
    # Lomiri's keyboard (maliit) enables English and emoji by default and crashed (SIGSEGV) without
    # their data packages, which are only recommended; Lomiri restarted with it in a loop (Ubuntu
    # 26.04, 2026-11-09). Its data for the system language comes too, where there is one.
    lomiri:apt) echo "lomiri-system-settings lomiri-terminal-app lomiri-filemanager-app morph-browser lomiri-keyboard-english lomiri-keyboard-emoji $(lomiri_keyboard_language)" ;;
    esac
}

lomiri_keyboard_language() {
    case "$(wanted_locale)" in
    uk_*) echo lomiri-keyboard-ukrainian ;; de_*) echo lomiri-keyboard-german ;;
    fr_*) echo lomiri-keyboard-french ;; pl_*) echo lomiri-keyboard-polish ;;
    es_*) echo lomiri-keyboard-spanish ;; it_*) echo lomiri-keyboard-italian ;;
    pt_*) echo lomiri-keyboard-portuguese ;; nl_*) echo lomiri-keyboard-dutch ;;
    cs_*) echo lomiri-keyboard-czech ;; ro_*) echo lomiri-keyboard-romanian ;;
    tr_*) echo lomiri-keyboard-turkish ;; ru_*) echo lomiri-keyboard-russian ;;
    esac
}

# The sign-in screen chosen in recovery. Automatic sign-in, Phrog and the text login run under
# greetd, which also comes along for the display managers: a display manager that cannot be
# set up falls back to automatic sign-in.
greeter_packages() {
    case "$GREETER:$1" in
    gdm:apt) echo "greetd gdm3" ;;
    gdm:*) echo "greetd gdm" ;;
    sddm:zypper) echo "greetd sddm-qt6" ;;
    sddm:*) echo "greetd sddm" ;;
    lightdm:apt) echo "greetd lightdm lightdm-gtk-greeter xserver-xorg-core xserver-xorg-input-libinput" ;;
    lightdm:dnf) echo "greetd lightdm lightdm-gtk" ;;
    lightdm:pacman) echo "greetd lightdm lightdm-gtk-greeter xorg-server" ;;
    lightdm:zypper) echo "greetd lightdm lightdm-gtk-greeter xorg-x11-server" ;;
    *) echo greetd ;;
    esac
}

greeter_extras() {
    case "$GREETER:$1" in
    phrog:*) echo phrog ;;
    text:pacman) echo "greetd-tuigreet greetd-agreety terminus-font" ;;
    text:dnf) echo "tuigreet greetd-tuigreet greetd-agreety terminus-fonts-console" ;;
    text:*) echo "tuigreet greetd-tuigreet greetd-agreety console-setup-linux" ;;
    # Breeze: without a theme SDDM drew its built-in desktop screen, cut off at the phone's width,
    # with the restart button over the session list (Ubuntu 26.04, 2026-11-09).
    sddm:apt) echo "qml6-module-qtquick-virtualkeyboard qt6-virtualkeyboard-plugin sddm-theme-breeze" ;;
    sddm:dnf) echo "qt6-qtvirtualkeyboard sddm-breeze" ;;
    sddm:*) echo qt6-virtualkeyboard ;;
    # LightDM's X11 login screen needs a keyboard on the screen: onboard, else xvkbd.
    lightdm:*) echo "onboard xvkbd" ;;
    esac
    # SDDM draws its login screen in a Wayland compositor: KWin with Plasma, else Weston.
    case " $SHELLS " in
    *" plasma "*) ;;
    *) [ "$GREETER" != sddm ] || echo weston ;;
    esac
}

packages() {
    family=$(manager)
    [ -n "$family" ] || { say "no supported package manager"; return 1; }
    list= extras=
    for shell in $SHELLS; do
        list="$list $(shell_packages "$shell" "$family")"
        extras="$extras $(shell_extras "$shell" "$family")"
    done
    list="$list $(greeter_packages "$family")"
    extras="$extras $(greeter_extras "$family") $(locale_packages "$family")"
    case "$family" in
    apt)
        export DEBIAN_FRONTEND=noninteractive
        # In the recovery's chroot the image's initramfs is of no use (the phone boots its own
        # kernel), and a trigger rebuilding it there could fail the whole install.
        if [ -n "${A22X_CHROOT:-}" ] && [ -f /etc/initramfs-tools/update-initramfs.conf ] &&
           grep -q '^update_initramfs=yes' /etc/initramfs-tools/update-initramfs.conf; then
            sed -i 's/^update_initramfs=yes/update_initramfs=no/' /etc/initramfs-tools/update-initramfs.conf
            initramfs_deferred=1
        fi
        # Ubuntu images rely on cloud-init for the updates pocket, and that is disabled here.
        . /etc/os-release
        if [ "${ID:-}" = ubuntu ] && [ -f /etc/apt/sources.list.d/ubuntu.sources ] &&
           ! grep -q "${VERSION_CODENAME}-updates" /etc/apt/sources.list.d/ubuntu.sources; then
            sed -i "0,/^Suites: ${VERSION_CODENAME}\$/s//Suites: ${VERSION_CODENAME} ${VERSION_CODENAME}-updates/" /etc/apt/sources.list.d/ubuntu.sources
        fi
        # shellcheck disable=SC2086
        apt-get update && apt-get install -y --no-install-recommends $list || return 1
        # Lomiri's Mir finds its display and renderer as plugins, which are only Recommends:
        # without them it stops at "server-platform: No such file or directory" and greetd
        # gives up (2026-11-09, Ubuntu 26.04 from SD). Debian names them with the ABI number.
        case " $SHELLS " in
        *" lomiri "*)
            for wanted in "mir-platform-graphics-gbm-kms mir-platform-graphics-gbm-kms23 mir-platform-graphics-gbm-kms22" \
                          "mir-platform-rendering-egl-generic mir-platform-rendering-egl-generic23 mir-platform-rendering-egl-generic22" \
                          "mir-platform-input-evdev10 mir-platform-input-evdev9"; do
                done_one=
                for name in $wanted; do
                    apt-cache show "$name" >/dev/null 2>&1 || continue
                    apt-get install -y --no-install-recommends "$name" && { done_one=1; break; }
                done
                [ -n "$done_one" ] || { say "no Mir platform package among: $wanted"; return 1; }
            done ;;
        esac
        ;;
    dnf)
        # shellcheck disable=SC2086
        dnf install -y $list || return 1
        ;;
    pacman)
        pacman_keys || return 1
        # Phrog is built here from Phosh's library where the distribution has no package of it.
        [ "$GREETER" != phrog ] || available pacman phrog || list="$list phosh phoc stevia"
        # sudo too: Arch Linux ARM ships without it, and an account in wheel had no way to
        # administer the phone (2026-11-09). Debian, Ubuntu and Fedora give their first user sudo.
        # shellcheck disable=SC2086
        pacman_upgrade $list sudo || return 1
        if [ -d /etc/sudoers.d ] && ! grep -Eqs '^[[:space:]]*%wheel[[:space:]]+ALL' /etc/sudoers /etc/sudoers.d/*; then
            printf '%%wheel ALL=(ALL:ALL) ALL\n' > /etc/sudoers.d/10-a22x-wheel
            chmod 0440 /etc/sudoers.d/10-a22x-wheel
            visudo -cqf /etc/sudoers.d/10-a22x-wheel >/dev/null 2>&1 || rm -f /etc/sudoers.d/10-a22x-wheel
        fi
        ;;
    zypper)
        # shellcheck disable=SC2086
        zypper --non-interactive --gpg-auto-import-keys refresh &&
            zypper --non-interactive install --no-recommends $list || return 1
        ;;
    esac
    for extra in $extras; do
        available "$family" "$extra" || { say "no $extra package"; continue; }
        for try in 1 2 3; do
            install_one "$family" "$extra" && break
            sleep 5
        done
    done
    # A phone without a terminal is hard to repair; a download failure must not leave it so.
    if [ "$family" = apt ]; then
        command -v kgx >/dev/null || command -v gnome-terminal >/dev/null ||
            apt-get install -y --no-install-recommends -o Acquire::Retries=5 gnome-terminal || return 1
    fi
}

# The recovery's language and clock (2026-11-09): the new system starts in the language chosen in
# recovery, with its clock offset. Recovery knows an offset, not a region, so the zone is a fixed
# one (Etc/GMT-1 for UTC+1); the region can be picked later in the system's settings.
# The language picked in recovery's installer, else the recovery's own one.
wanted_locale() {
    case "${A22X_LOCALE:-}" in
    [a-z][a-z]_[A-Z][A-Z]) echo "$A22X_LOCALE"; return ;;
    esac
    case "${A22X_LANGUAGE:-}" in
    uk) echo uk_UA ;; de) echo de_DE ;; fr) echo fr_FR ;; pl) echo pl_PL ;; es) echo es_ES ;;
    esac
}

system_locale() {
    loc=$(wanted_locale)
    [ -n "$loc" ] || return 0
    if [ -f /etc/locale.gen ]; then
        if ! grep -q "^$loc\.UTF-8 UTF-8" /etc/locale.gen; then
            sed -i "s/^# *\($loc\.UTF-8 UTF-8\)/\1/" /etc/locale.gen
            grep -q "^$loc\.UTF-8 UTF-8" /etc/locale.gen || echo "$loc.UTF-8 UTF-8" >> /etc/locale.gen
        fi
        ! command -v locale-gen >/dev/null || locale-gen >/dev/null 2>&1 || say "locale-gen failed"
    fi
    if command -v locale >/dev/null && ! locale -a 2>/dev/null | grep -qix "$loc\.utf-\{0,1\}8"; then
        say "the $loc locale is not available; the system stays in English"
        return 0
    fi
    printf 'LANG=%s.UTF-8\n' "$loc" > /etc/locale.conf
    [ ! -d /etc/default ] || printf 'LANG=%s.UTF-8\n' "$loc" > /etc/default/locale
}

locale_packages() {
    loc=$(wanted_locale)
    [ -n "$loc" ] || return 0
    case "$1" in
    apt)
        echo locales
        # Ubuntu moves translations into language packs; GNOME stayed English without them
        # (Ubuntu 26.04, es_ES, 2026-11-09). Debian ships them inside the programs.
        if [ "$(os_value ID)" = ubuntu ] && [ "${loc%%_*}" != en ]; then
            echo "language-pack-${loc%%_*}"
            case " $SHELLS " in *" gnome "*|*" phosh "*) echo "language-pack-gnome-${loc%%_*}" ;; esac
            case " $SHELLS " in *" plasma "*) echo "language-pack-kde-${loc%%_*}" ;; esac
        fi ;;
    dnf) echo "glibc-langpack-${loc%%_*}" ;;
    esac
}

system_timezone() {
    # A city picked in recovery's installer, with its summer time, when this system knows it.
    case "${A22X_TIMEZONE:-}" in
    ''|/*|*..*) ;;
    *) if [ -f "/usr/share/zoneinfo/$A22X_TIMEZONE" ]; then
           ln -sfn "../usr/share/zoneinfo/$A22X_TIMEZONE" /etc/localtime
           [ ! -f /etc/timezone ] || echo "$A22X_TIMEZONE" > /etc/timezone
           return 0
       fi
       say "time zone $A22X_TIMEZONE is unknown here; the recovery's offset is used" ;;
    esac
    off=${A22X_UTC_OFFSET:-0}
    case "$off" in ''|*[!0-9-]*) return 0 ;; esac
    [ "$off" -ne 0 ] && [ $((off % 60)) -eq 0 ] || return 0
    h=$((off / 60))
    # Etc/GMT zones carry the opposite sign: UTC+1 is Etc/GMT-1.
    if [ "$h" -gt 0 ]; then zone=Etc/GMT-$h; else zone=Etc/GMT+$((-h)); fi
    [ -e "/usr/share/zoneinfo/$zone" ] || return 0
    ln -sfn "../usr/share/zoneinfo/$zone" /etc/localtime
    [ ! -f /etc/timezone ] || echo "$zone" > /etc/timezone
}

# What the distribution lacks for this phone is built in the new system: phoc's wlroots fix
# for Phosh, and Phrog where no package of it exists (Arch Linux ARM, 2026-11-09).
phrog_ready() {
    command -v phrog >/dev/null 2>&1 || [ -x /usr/local/bin/phrog ]
}

# A build made on this phone is kept with the versions it was made for (/var/cache/a22x-build,
# which recovery carries over to the next installation): when they are the same, it is put in
# place instead of building again (2026-11-09: Phrog took 30-40 minutes each time).
BUILD_CACHE=/var/cache/a22x-build
PHOC_FILES='opt/a22x-phoc'
PHROG_FILES='usr/local/bin/phrog usr/local/lib/a22x-phrog usr/local/share/glib-2.0/schemas/mobi.phosh.phrog.gschema.xml
usr/local/share/glib-2.0/schemas/00_mobi.phosh.Phrog.gschema.override usr/local/share/gnome-session/sessions/phrog.session
usr/local/share/applications/mobi.phosh.Phrog.desktop usr/local/lib/systemd/user/mobi.phosh.Phrog.service
usr/local/lib/systemd/user/mobi.phosh.Phrog.target usr/local/lib/systemd/user/gnome-session@phrog.target.d'
cached_build() {
    kind=$1 key=$2
    [ -n "$key" ] && [ -f "$BUILD_CACHE/$kind.tar" ] && [ "$(cat "$BUILD_CACHE/$kind.key" 2>/dev/null)" = "$key" ] || return 1
    tar -C / -xf "$BUILD_CACHE/$kind.tar" || return 1
    [ "$kind" != phrog ] || glib-compile-schemas /usr/local/share/glib-2.0/schemas
    say "using the $kind build kept for $key"
}
keep_build() {
    kind=$1 key=$2 files=$3 built=$4
    [ -n "$key" ] && [ "$(cat "$built" 2>/dev/null)" = "$key" ] || return 0
    [ "$(cat "$BUILD_CACHE/$kind.key" 2>/dev/null)" = "$key" ] && return 0
    mkdir -p "$BUILD_CACHE"
    # shellcheck disable=SC2086
    tar -C / -cf "$BUILD_CACHE/$kind.tar.new" $files 2>/dev/null &&
        mv -f "$BUILD_CACHE/$kind.tar.new" "$BUILD_CACHE/$kind.tar" && printf '%s\n' "$key" > "$BUILD_CACHE/$kind.key"
    rm -f "$BUILD_CACHE/$kind.tar.new"
}
# Whether an image that brought its own Phosh still needs phoc's wlroots fix built for this phone:
# the build script names the build it would make, and nothing when the image's wlroots is fine.
phone_build_needed() {
    command -v pacman >/dev/null || return 1
    [ -x /usr/bin/phoc ] || return 1
    key=$(sh /opt/a22x-conn/build/a22x-build-phoc.sh --key 2>/dev/null)
    case "$key" in
    ''|a22x-build-phoc:*) return 1 ;;
    esac
    [ "$(cat /opt/a22x-phoc/built-for 2>/dev/null)" != "$key" ]
}
build_for_phone() {
    command -v pacman >/dev/null || return 0
    need=
    case " $SHELLS " in *" phosh "*) need=1 ;; esac
    [ "$GREETER" != phrog ] || need=1
    if [ -n "$need" ] && [ -x /usr/bin/phoc ]; then
        key=$(sh /opt/a22x-conn/build/a22x-build-phoc.sh --key 2>/dev/null)
        cached_build phoc "$key" || say "building phoc's wlroots fix for Phosh; this takes 5-10 minutes"
        if sh /opt/a22x-conn/build/a22x-build-phoc.sh >> "$LOG" 2>&1; then
            keep_build phoc "$key" "$PHOC_FILES" /opt/a22x-phoc/built-for
        else
            say "phoc's wlroots fix was not built; Phosh may not start (see $LOG)"
        fi
    fi
    if [ "$GREETER" = phrog ] && ! phrog_ready; then
        key=$(sh /opt/a22x-conn/build/a22x-build-phrog.sh --key 2>/dev/null)
        cached_build phrog "$key" || say "building the Phrog login screen; this takes 30-40 minutes"
        if sh /opt/a22x-conn/build/a22x-build-phrog.sh >> "$LOG" 2>&1; then
            keep_build phrog "$key" "$PHROG_FILES" /usr/local/lib/a22x-phrog/built-for
        else
            say "Phrog was not built (see $LOG)"
        fi
    fi
    rebuild_service
    return 0
}

# Updates bring new versions of phoc, wlroots and Phosh: what was built for them is built again
# in the background, after such an update and once a day, when the phone charges and has a
# network. Each script does nothing while its build matches the installed versions.
rebuild_service() {
    [ -x /opt/a22x-phoc/bin/phoc ] || [ -f /usr/local/lib/a22x-phrog/built-for ] || return 0
    mkdir -p /usr/local/sbin /etc/pacman.d/hooks /etc/systemd/system
    cat > /usr/local/sbin/a22x-rebuild <<'S'
#!/bin/sh
# Builds again what the A22x recovery built for this phone once an update changed what it was
# built for (phoc's wlroots fix, Phrog). Waits for a running pacman.
while [ -e /var/lib/pacman/db.lck ]; do sleep 20; done
status=0
sh /opt/a22x-conn/build/a22x-build-phoc.sh || status=1
if [ -f /usr/local/lib/a22x-phrog/built-for ]; then
    sh /opt/a22x-conn/build/a22x-build-phrog.sh || status=1
fi
exit $status
S
    chmod 755 /usr/local/sbin/a22x-rebuild
    cat > /etc/systemd/system/a22x-rebuild.service <<'S'
[Unit]
Description=A22x: build phoc's wlroots fix and Phrog again for updated packages
Wants=network-online.target
After=network-online.target
ConditionACPower=true

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/a22x-rebuild
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
S
    cat > /etc/systemd/system/a22x-rebuild.timer <<'S'
[Unit]
Description=A22x: check daily whether phoc's wlroots fix and Phrog need building again

[Timer]
OnBootSec=15min
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
S
    cat > /etc/pacman.d/hooks/90-a22x-rebuild.hook <<'S'
[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = phoc
Target = phosh
Target = wlroots*

[Action]
Description = A22x: phoc's wlroots fix and Phrog are built again in the background
When = PostTransaction
Exec = /usr/bin/systemctl start --no-block a22x-rebuild.service
S
    systemctl enable a22x-rebuild.timer
}

# Sound: the recovery brings this phone's UCM profile and mixer state. They need the
# distribution's shared UCM files (the profile includes them) and alsactl, which applies the
# state at every start; a clean Fedora install had neither and no sound (2026-11-08).
audio() {
    [ -e /usr/share/alsa/ucm2/MediaTek/mt6833 ] || return 0
    if command -v apt-get >/dev/null; then
        for p in alsa-utils alsa-ucm-conf; do
            apt-get install -y --no-install-recommends "$p" || say "no $p package"
        done
    elif command -v dnf >/dev/null; then
        dnf install -y alsa-utils alsa-ucm || say "ALSA packages not installed"
    elif command -v pacman >/dev/null; then
        pacman -S --noconfirm --needed alsa-utils alsa-ucm-conf || say "ALSA packages not installed"
    elif command -v zypper >/dev/null; then
        zypper --non-interactive install --no-recommends alsa-utils alsa-ucm-conf || say "ALSA packages not installed"
    fi
    if command -v alsactl >/dev/null && [ -f /var/lib/alsa/asound.state ]; then
        alsactl restore || true
    fi
    return 0
}

# Images ship the pacman keyring in any state; Manjaro ARM's had no key to sign with
# (2026-11-08). A keyring that cannot sign is made anew, then its keyring packages updated.
pacman_keys() {
    # pacman 7 downloads as its own user alpm, which systemd-sysusers creates at the first start.
    # In recovery's chroot it did not exist yet and every download failed ("switching to sandbox
    # user 'alpm' failed", Arch Linux ARM, 2026-11-09): the system users are created first.
    command -v systemd-sysusers >/dev/null 2>&1 && systemd-sysusers >/dev/null 2>&1
    if ! pacman-key --init; then
        mv /etc/pacman.d/gnupg "/etc/pacman.d/gnupg.image-$(date +%s)" 2>/dev/null
        pacman-key --init || return 1
    fi
    pacman-key --populate || return 1
    keyrings=$(pacman -Qq 2>/dev/null | grep -- '-keyring$')
    [ -z "$keyrings" ] || pacman -Sy --noconfirm --needed $keyrings
}

# Arch does not support new packages on an out-of-date system, so the whole system is
# brought up to date with them. A package the repository dropped can block that, as gitui
# did on Manjaro ARM 2023; it is removed only when nothing else needs it.
pacman_upgrade() {
    for attempt in 1 2 3; do
        out=$(pacman -Syu --noconfirm --needed "$@" 2>&1)
        status=$?
        printf '%s\n' "$out"
        [ $status -ne 0 ] || return 0
        stale=$(printf '%s\n' "$out" | sed -n "s/.*breaks dependency '[^']*' required by \(.*\)$/\1/p" | sort -u)
        [ -n "$stale" ] || return 1
        say "removing outdated packages that block the upgrade: $stale"
        pacman -R --noconfirm $stale || return 1
    done
    return 1
}

# Which package manager installs the apps; empty when there is none.
manager() {
    if command -v apt-get >/dev/null; then echo apt
    elif command -v dnf >/dev/null; then echo dnf
    elif command -v pacman >/dev/null; then echo pacman
    elif command -v zypper >/dev/null; then echo zypper
    fi
}

os_value() {
    sed -n "s/^$1=//p" /etc/os-release /usr/lib/os-release 2>/dev/null | head -1 | tr -d '"'
}

refreshed=
refresh() {
    [ -z "$refreshed" ] || return 0
    case "$1" in
    apt) apt-get update || return 1 ;;
    dnf) dnf makecache -y || return 1 ;;
    pacman) pacman_keys && pacman_upgrade || return 1 ;;
    zypper) zypper --non-interactive --gpg-auto-import-keys refresh || return 1 ;;
    esac
    refreshed=1
}

# The look of the desktop decides which settings, files or terminal program fits it.
look() {
    case "$DESKTOP" in
    plasma) echo plasma ;;
    xfce) echo xfce ;;
    lomiri) echo lomiri ;;
    sxmo) echo sxmo ;;
    phosh|gnome) echo gtk ;;
    *) if command -v plasmashell >/dev/null; then echo plasma
       elif command -v xfce4-session >/dev/null; then echo xfce
       else echo gtk; fi ;;
    esac
}

# Packages for one app, best first. The browser skips Ubuntu's "firefox", which needs snap.
app_packages() {
    app=$1 family=$2 style=$3
    case "$app:$style" in
    browser:lomiri) echo "morph-browser firefox-esr" ;;
    browser:*) case "$family" in apt) echo "firefox-esr epiphany-browser" ;; zypper) echo "MozillaFirefox epiphany" ;; *) echo firefox ;; esac ;;
    settings:lomiri) echo lomiri-system-settings ;;
    files:lomiri) echo lomiri-filemanager-app ;;
    terminal:lomiri) echo lomiri-terminal-app ;;
    terminal:sxmo) echo foot ;;
    files:sxmo) echo "nautilus pcmanfm" ;;
    keyboard:sxmo) echo wvkbd ;;
    keyboard:lomiri) echo lomiri-keyboard ;;
    settings:plasma) case "$family" in dnf) echo plasma-systemsettings ;; zypper) echo "systemsettings6 systemsettings5" ;; *) echo systemsettings ;; esac ;;
    settings:xfce) echo xfce4-settings ;;
    settings:*) echo gnome-control-center ;;
    files:plasma) echo dolphin ;;
    files:xfce) echo thunar ;;
    files:*) echo nautilus ;;
    terminal:plasma) echo "qmlkonsole konsole" ;;
    terminal:xfce) echo xfce4-terminal ;;
    terminal:*) echo "gnome-console gnome-terminal" ;;
    editor:plasma) [ "$family" = pacman ] && echo kate || echo kwrite ;;
    editor:xfce) echo mousepad ;;
    editor:*) echo "gnome-text-editor gedit" ;;
    store:plasma) case "$family" in pacman) echo discover ;; zypper) echo "discover6 discover" ;; *) echo plasma-discover ;; esac ;;
    store:*) echo gnome-software ;;
    keyboard:plasma) echo maliit-keyboard ;;
    # Phosh renamed its keyboard to Stevia; Fedora 45 has only that name (2026-11-08).
    keyboard:*) echo "stevia squeekboard phosh-osk-stub onboard" ;;
    telegram:*) echo telegram-desktop ;;
    ssh:*) [ "$family" = pacman ] && echo openssh || echo openssh-server ;;
    # 2026-11-08: more apps; the first name a distribution has is installed.
    email:plasma) echo kmail ;;
    email:lomiri) echo "lomiri-dekko-app dekko" ;;
    email:*) echo "geary thunderbird" ;;
    music:plasma) echo elisa ;;
    music:lomiri) echo lomiri-music-app ;;
    music:*) echo "lollypop gnome-music rhythmbox" ;;
    video:plasma) echo "haruna mpv" ;;
    video:lomiri) echo lomiri-mediaplayer-app ;;
    video:*) echo "celluloid totem mpv" ;;
    photos:plasma) echo "koko gwenview" ;;
    photos:lomiri) echo lomiri-gallery-app ;;
    photos:*) echo "loupe eog shotwell" ;;
    documents:plasma) echo okular ;;
    documents:lomiri) echo lomiri-docviewer-app ;;
    documents:*) echo "papers evince" ;;
    office:*) echo "libreoffice-writer libreoffice-fresh libreoffice" ;;
    calculator:plasma) echo "kalk kcalc" ;;
    calculator:lomiri) echo lomiri-calculator-app ;;
    calculator:*) echo gnome-calculator ;;
    clock:plasma) echo kclock ;;
    clock:lomiri) echo lomiri-clock-app ;;
    clock:*) echo gnome-clocks ;;
    weather:plasma) echo kweather ;;
    weather:lomiri) echo lomiri-weather-app ;;
    weather:*) echo gnome-weather ;;
    maps:plasma) echo marble ;;
    maps:*) echo gnome-maps ;;
    camera:plasma) echo "plasma-camera megapixels" ;;
    camera:lomiri) echo lomiri-camera-app ;;
    # Debian names GNOME's camera gnome-snapshot; Mobian and Fedora snapshot (2026-11-09).
    camera:*) echo "snapshot gnome-snapshot megapixels" ;;
    contacts:plasma) echo plasma-phonebook ;;
    contacts:lomiri) echo lomiri-addressbook-app ;;
    contacts:*) echo gnome-contacts ;;
    calendar:plasma) echo "calindori merkuro" ;;
    calendar:lomiri) echo lomiri-calendar-app ;;
    calendar:*) echo gnome-calendar ;;
    passwords:*) echo "keepassxc secrets" ;;
    flatpak:*) echo flatpak ;;
    chromium:*) echo chromium ;;
    podcasts:plasma) echo kasts ;;
    podcasts:*) echo gnome-podcasts ;;
    messages:plasma) echo spacebar ;;
    messages:*) echo chatty ;;
    git:*) echo git ;;
    monitor:plasma) echo plasma-systemmonitor ;;
    monitor:*) echo "gnome-system-monitor htop" ;;
    esac
}

available() {
    case "$1" in
    apt) apt-cache show "$2" >/dev/null 2>&1 ;;
    dnf) dnf -q info "$2" >/dev/null 2>&1 ;;
    pacman) pacman -Si "$2" >/dev/null 2>&1 ;;
    zypper) zypper --non-interactive --quiet search --match-exact "$2" >/dev/null 2>&1 ;;
    esac
}

install_one() {
    case "$1" in
    apt) DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends -o Acquire::Retries=3 "$2" ;;
    dnf) dnf install -y "$2" ;;
    pacman) pacman -S --noconfirm --needed "$2" ;;
    zypper) zypper --non-interactive install --no-recommends "$2" ;;
    esac
}

# Telegram where the distribution has no package of its own: the Flathub build.
flathub() {
    command -v flatpak >/dev/null || { available "$2" flatpak && install_one "$2" flatpak; } || return 1
    flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo &&
        flatpak install -y --noninteractive flathub "$1"
}

# The apps chosen in recovery. A missing package skips that app; the others still install.
apps() {
    [ -n "${APPS:-}" ] || return 0
    family=$(manager)
    [ -n "$family" ] || { say "no supported package manager for the apps"; return 1; }
    refresh "$family" || return 1
    style=$(look)
    missing=
    for app in $APPS; do
        installed=
        # Ubuntu's chromium package only installs a snap; the Flathub build works without snapd.
        if [ "$app" = chromium ] && [ "$(os_value ID)" = ubuntu ]; then
            flathub org.chromium.Chromium "$family" && installed=flathub
        fi
        for package in $(app_packages "$app" "$family" "$style"); do
            [ -z "$installed" ] || break
            available "$family" "$package" || continue
            install_one "$family" "$package" && { installed=$package; break; }
        done
        [ -n "$installed" ] || [ "$app" != telegram ] || { flathub org.telegram.desktop "$family" && installed=flathub; }
        if [ -n "$installed" ]; then
            say "app $app: $installed"
        else
            say "app $app: no package"; missing=1
        fi
        if [ "$app" = ssh ] && [ -n "$installed" ]; then
            systemctl enable ssh.service 2>/dev/null || systemctl enable sshd.service
        fi
        if [ "$app" = flatpak ] && [ -n "$installed" ]; then
            flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || say "Flathub not added"
        fi
    done
    [ -z "$missing" ]
}

# Packages found by the search in recovery, by their names in this distribution (2026-11-08).
extra_packages() {
    [ -n "${EXTRA_PACKAGES:-}" ] || return 0
    family=$(manager)
    [ -n "$family" ] || { say "no supported package manager for the packages"; return 1; }
    refresh "$family" || return 1
    say "installing packages: $EXTRA_PACKAGES"
    failed=
    for package in $EXTRA_PACKAGES; do
        if available "$family" "$package" && install_one "$family" "$package"; then
            say "package $package: installed"
        else
            say "package $package: not installed"; failed=1
        fi
    done
    [ -z "$failed" ]
}

# One install command for every supported distribution, kept for later use too.
install_helper() {
    mkdir -p /usr/local/sbin
    cat > /usr/local/sbin/a22x-install <<'S'
#!/bin/sh
# a22x-install PACKAGE...: install packages with this system's package manager.
if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive exec apt-get install -y --no-install-recommends "$@"
elif command -v dnf >/dev/null; then exec dnf install -y "$@"
elif command -v pacman >/dev/null; then exec pacman -S --noconfirm --needed "$@"
elif command -v zypper >/dev/null; then exec zypper --non-interactive install --no-recommends "$@"
fi
echo "a22x-install: no supported package manager" >&2
exit 1
S
    chmod 755 /usr/local/sbin/a22x-install
}

# The script chosen in recovery runs once, as root, after the network, desktop and apps.
# It learns the system and its package manager, and can call a22x-install for packages.
setup_script() {
    script=/etc/a22x/setup-script
    [ -f "$script" ] || return 0
    family=$(manager)
    install_helper
    [ -z "$family" ] || refresh "$family" >> "$LOG" 2>&1 || say "package lists could not be refreshed"
    say "running the setup script (system $(os_value ID), packages ${family:-none})"
    set -- env A22X_USER="${NEW_USER:-${PHONE_USER:-}}" A22X_DESKTOP="$DESKTOP" A22X_SHELLS="${SHELLS:-}" A22X_GREETER="${GREETER:-}" A22X_OS_ID="$(os_value ID)" A22X_OS_LIKE="$(os_value ID_LIKE)" \
        A22X_OS_VERSION="$(os_value VERSION_ID)" A22X_PACKAGE_MANAGER="$family" \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    if [ "$(head -c 2 "$script")" = '#!' ]; then
        "$@" "$script" >> "$LOG" 2>&1
    else
        "$@" sh "$script" >> "$LOG" 2>&1
    fi
    status=$?
    mv -f "$script" "$script.done"
    say "setup script finished with status $status"
}

# Without NetworkManager the network typed in recovery would only work at this first start.
persist_wifi() {
    nm_has_wifi && return 0
    [ -f /etc/a22x/wpa.conf ] || return 0
    if command -v wpa_supplicant >/dev/null && [ -f /usr/lib/systemd/system/wpa_supplicant@.service ] &&
       [ -f /usr/lib/systemd/system/systemd-networkd.service ]; then
        mkdir -p /etc/wpa_supplicant /etc/systemd/network
        cp /etc/a22x/wpa.conf /etc/wpa_supplicant/wpa_supplicant-wlan0.conf && chmod 600 /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
        printf '[Match]\nName=wlan0\n\n[Network]\nDHCP=yes\n' > /etc/systemd/network/30-a22x-wlan0.network
        systemctl enable wpa_supplicant@wlan0.service systemd-networkd.service
        return
    fi
    # The program kept in recovery joins the network on every start; busybox keeps the address.
    # Both units step aside once NetworkManager is installed.
    if [ -x $W/ld-linux-aarch64.so.1 ]; then
        run="$W/ld-linux-aarch64.so.1 --library-path $W $W/wpa_supplicant"
    elif [ -x $W/wpa_supplicant ]; then
        run="$W/wpa_supplicant"
    else
        return 0
    fi
    cp /etc/a22x/wpa.conf /etc/a22x/wifi.conf && chmod 600 /etc/a22x/wifi.conf
    # Both units step aside only when NetworkManager can drive Wi-Fi itself: NetworkManager
    # alone, as on Fedora Cloud, would otherwise leave the phone without a network.
    cat > /opt/a22x-conn/a22x-wifi-needed <<'S'
#!/bin/sh
# Exit 1 skips the unit: NetworkManager with its Wi-Fi plugin and a supplicant is installed.
[ -x /usr/sbin/NetworkManager ] || [ -x /usr/bin/NetworkManager ] || exit 0
command -v wpa_supplicant >/dev/null 2>&1 || [ -x /usr/lib/iwd/iwd ] || [ -x /usr/libexec/iwd ] || exit 0
for plugin in /usr/lib*/NetworkManager/*/libnm-device-plugin-wifi.so \
              /usr/lib/*/NetworkManager/*/libnm-device-plugin-wifi.so; do
    [ -e "$plugin" ] && exit 1
done
exit 0
S
    chmod 755 /opt/a22x-conn/a22x-wifi-needed
    cat > /etc/systemd/system/a22x-wifi.service <<S
[Unit]
Description=A22x Wi-Fi for a system without a network manager
After=a22x-conn.service
StartLimitIntervalSec=0

[Service]
ExecCondition=/opt/a22x-conn/a22x-wifi-needed
# wlan0 appears once a22x-conn has loaded the driver; until then each start fails and is retried.
ExecStart=$run -i wlan0 -c /etc/a22x/wifi.conf -D nl80211
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
S
    cat > /etc/systemd/system/a22x-wifi-dhcp.service <<S
[Unit]
Description=A22x Wi-Fi address
After=a22x-wifi.service
BindsTo=a22x-wifi.service
StartLimitIntervalSec=0

[Service]
ExecCondition=/opt/a22x-conn/a22x-wifi-needed
ExecStart=$B udhcpc -i wlan0 -f -s /opt/a22x-conn/udhcpc.script
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
S
    systemctl enable a22x-wifi.service a22x-wifi-dhcp.service
}

# The shared /home keeps launchers that name programs built for another system.
# Give each missing path a small shim to this system's program; /home stays as it is.
home_shims() {
    for file in /home/*/.local/share/applications/*.desktop; do
        [ -f "$file" ] || continue
        target=$(sed -n 's/^Exec=\(\/[^ ]*\).*/\1/p' "$file" | head -1)
        [ -n "$target" ] && [ ! -e "$target" ] || continue
        case "$target" in /home/*) continue ;; esac
        program=$(PATH=/usr/bin:/usr/sbin command -v "${target##*/}") || continue
        mkdir -p "${target%/*}"
        printf '#!/bin/sh\nexec %s "$@"\n' "$program" > "$target"
        chmod 755 "$target"
    done
}

# Each shell recovery sets up is written once for every sign-in screen (2026-11-09):
# /etc/a22x/shells for a22x-shell (automatic sign-in and the text login) and the switch
# launchers, /usr/share/a22x/sessions for Phrog's list, and for a display manager its own.
shell_entry() {
    printf '%s|%s|%s\n' "$1" "$2" "$3" >> /etc/a22x/shells.new
    printf '[Desktop Entry]\nName=%s\nExec=%s\nType=Application\nDesktopNames=%s\n' "$2" "$3" "$4" \
        > "/usr/share/a22x/sessions/a22x-$1.desktop"
}

# The name Mesa knows this phone's GPU path by. Mesa up to 24.1 installs kmsro under the display
# driver's name (mediatek_dri.so) and has no kmsro_dri.so; asked for "kmsro" it finds no driver,
# and the compositor draws in software (Manjaro ARM's image, Mesa 24.0.2, 2026-11-12: phoc said
# "Software rendering detected" and "your system is too slow", and Phosh's buttons answered late
# or not at all; with "mediatek" the same phoc said "GL renderer: Mali-G57 (Panfrost)").
mesa_driver() {
    for dir in /usr/lib/dri /usr/lib64/dri /usr/lib/aarch64-linux-gnu/dri; do
        if [ -e "$dir/mediatek_dri.so" ] && [ ! -e "$dir/kmsro_dri.so" ]; then
            echo mediatek
            return 0
        fi
    done
    echo kmsro
}
# The session programs ask at each start, so an upgrade of Mesa needs no new setup.
gpu_driver_tool() {
    cat > /usr/bin/a22x-gpu-driver <<'S'
#!/bin/sh
# The name of this phone's GPU path in the installed Mesa (A22x recovery).
for dir in /usr/lib/dri /usr/lib64/dri /usr/lib/aarch64-linux-gnu/dri; do
    if [ -e "$dir/mediatek_dri.so" ] && [ ! -e "$dir/kmsro_dri.so" ]; then
        echo mediatek
        exit 0
    fi
done
echo kmsro
S
    chmod 0755 /usr/bin/a22x-gpu-driver
}

phosh_shell() {
    mkdir -p /etc/phosh
    # The lock screen authenticates through PAM service "phosh".
    if [ ! -e /etc/pam.d/phosh ] && [ -e /etc/pam.d/common-auth ]; then
        printf '@include common-auth\n@include common-account\n' > /etc/pam.d/phosh
    fi
    printf '[output:DSI-1]\nscale=2.75\n' > /etc/phosh/phoc.ini
    cat > /usr/bin/a22x-session <<'S'
#!/bin/sh
# The GPU path needs kmsro on this panel. A session that dies at once retries with software rendering.
export MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver 2>/dev/null || echo kmsro) XWAYLAND_NO_GLAMOR=1
# greetd, unlike a full display manager, does not describe the session to its applications.
export XDG_CURRENT_DESKTOP=Phosh:GNOME XDG_SESSION_DESKTOP=phosh XDG_SESSION_TYPE=wayland DESKTOP_SESSION=phosh
# Phosh locks itself at start unless a login screen started it (GDMSESSION=phosh). Phrog and GDM
# name the session by its file (a22x-phosh), the text login marks it: the password was just given
# (2026-11-09). Automatic sign-in sets neither, and Phosh's own lock screen asks for it.
[ -z "${GDMSESSION:-}${A22X_SIGNED_IN:-}" ] || export GDMSESSION=phosh
mark=/tmp/a22x-session-$(id -u)
if [ -f "$mark" ] && [ $(( $(date +%s) - $(cat "$mark") )) -lt 30 ]; then
    export WLR_RENDERER=pixman
fi
date +%s > "$mark"
# The shared /home can hold a ~/.phoshdebug written for another system's compositor. Where
# phoc needs the wlroots fix the recovery built (Arch Linux ARM), its launcher starts phoc.
phoc=/usr/bin/phoc
[ -x /opt/a22x-phoc/bin/phoc ] && phoc=/opt/a22x-phoc/bin/phoc
launcher=$(mktemp /tmp/a22x-phosh.XXXXXX)
sed -e '/\.phoshdebug/,/^fi$/d' -e "s#/usr/bin/phoc#$phoc#g" "$(command -v phosh-session)" > "$launcher"
exec sh "$launcher"
S
    chmod 755 /usr/bin/a22x-session
    shell_entry phosh Phosh /usr/bin/a22x-session 'Phosh;GNOME;'
}

gnome_shell() {
    cat > /usr/bin/a22x-gnome-session <<'S'
#!/bin/sh
# GNOME on Wayland; the GPU path needs kmsro on this panel.
export MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver 2>/dev/null || echo kmsro) XWAYLAND_NO_GLAMOR=1 XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=GNOME
exec gnome-session
S
    chmod 755 /usr/bin/a22x-gnome-session
    shell_entry gnome GNOME /usr/bin/a22x-gnome-session 'GNOME;'
}

# Plasma Mobile starts signed in like the others, with its own lock screen and keyboard; SDDM
# needed an X server for its login screen, which did not start on this phone under Manjaro.
plasma_shell() {
    command=$(sed -n 's/^Exec=//p' /usr/share/wayland-sessions/plasma-mobile.desktop 2>/dev/null | head -1)
    [ -n "$command" ] || command=startplasmamobile
    cat > /usr/bin/a22x-plasma-session <<S
#!/bin/sh
export XDG_SESSION_TYPE=wayland
exec $command
S
    chmod 755 /usr/bin/a22x-plasma-session
    # Only the compositor takes the panel's GPU path through kmsro, as on the Arch that worked
    # (2026-11-09: set for the whole session, Plasma's X11 helpers ksmserver, xembedsniproxy and
    # gmenudbusmenuproxy crashed). KWin runs as a user service in Plasma 6.
    mkdir -p /etc/systemd/user/plasma-kwin_wayland.service.d
    printf '[Service]\nEnvironment=MESA_LOADER_DRIVER_OVERRIDE=%s XWAYLAND_NO_GLAMOR=1\n' "$(mesa_driver)" > /etc/systemd/user/plasma-kwin_wayland.service.d/50-a22x-gpu.conf
    # X11 programs drawn at the panel's scale, like Wayland ones.
    if ! grep -qs '^\[Xwayland\]' /etc/xdg/kwinrc; then
        mkdir -p /etc/xdg
        printf '\n[Xwayland]\nScale=2.75\n' >> /etc/xdg/kwinrc
    fi
    rm -f /etc/sddm.conf.d/50-a22x-autologin.conf
    shell_entry plasma 'Plasma Mobile' /usr/bin/a22x-plasma-session 'KDE;'
}

# Xfce draws with X11. A display manager starts its X server; under greetd startx does, on the
# session's terminal.
# Xorg on this phone: the panel is the MediaTek display device, and the GPU (panfrost) has no
# outputs. Xorg took the GPU as its primary device and found "no screens", so X11 sessions and
# login screens did not start (2026-11-09, Xfce through GDM on Fedora; SDDM on Manjaro).
xorg_display() {
    mkdir -p /etc/X11/xorg.conf.d
    cat > /etc/X11/xorg.conf.d/20-a22x-display.conf <<'S'
# Written by the A22x recovery: the panel is driven by the MediaTek display device.
Section "OutputClass"
    Identifier "A22x panel"
    MatchDriver "mediatek|mediatek-drm"
    Driver "modesetting"
    Option "PrimaryGPU" "yes"
EndSection
S
}
xfce_shell() {
    xorg_display
    # Xfce draws GTK at the panel's scale like the other shells (2 x 1.375 = 2.75).
    cat > /usr/bin/a22x-xfce-start <<'S'
#!/bin/sh
export GDK_SCALE=2 GDK_DPI_SCALE=1.375 XDG_CURRENT_DESKTOP=XFCE
exec startxfce4 "$@"
S
    cat > /usr/bin/a22x-xfce-session <<'S'
#!/bin/sh
exec startx /usr/bin/a22x-xfce-start -- -keeptty "vt${XDG_VTNR:-1}"
S
    chmod 755 /usr/bin/a22x-xfce-session /usr/bin/a22x-xfce-start
    shell_entry xfce Xfce /usr/bin/a22x-xfce-session 'XFCE;'
}

sxmo_shell() {
    cat > /usr/bin/a22x-sxmo-session <<'S'
#!/bin/sh
# Sxmo on Sway; the panel is 1080x2408, so Sway draws at 2.75 like Phosh.
export MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver 2>/dev/null || echo kmsro) XWAYLAND_NO_GLAMOR=1 XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=sway SXMO_SWAY_SCALE=2.75
sxmo_winit.sh
status=$?
# When Sway ends, Sxmo's modem monitor stays in the session, which then never closes and keeps
# Sxmo's user services running under the next sign-in, lisgd still reading the touchscreen
# (2026-11-09, Mobian): stop them and whatever is left of this session.
# Sxmo's modem and network monitors took 90 s to stop, with the VT text console on the screen all
# that time (Ubuntu 26.04, 2026-11-09): wait 10 s, then end them.
timeout 10 systemctl --user stop 'sxmo_*.service' 2>/dev/null || systemctl --user kill --signal=KILL 'sxmo_*.service' 2>/dev/null
systemctl --user reset-failed 'sxmo_*.service' 2>/dev/null
scope=$(sed -n 's/^0:://p' /proc/self/cgroup 2>/dev/null)
if [ -n "$scope" ] && [ -r "/sys/fs/cgroup$scope/cgroup.procs" ]; then
    for pid in $(cat "/sys/fs/cgroup$scope/cgroup.procs"); do
        [ "$pid" = "$$" ] || kill "$pid" 2>/dev/null
    done
fi
exit $status
S
    chmod 755 /usr/bin/a22x-sxmo-session
    # Sxmo binds the power and volume keys to named input devices (PinePhone ones by default):
    # here power and volume down come from the PMIC keys and volume up from the keypad. Without
    # this the screen, once off, could not be woken (2026-11-08, Ubuntu 26.04).
    cat > '/usr/bin/sxmo_deviceprofile_samsung,a22x.sh' <<'S'
#!/bin/sh
# Samsung Galaxy A22 5G (a22x), written by the A22x recovery.
export SXMO_POWER_BUTTON="1:1:mtk-pmic-keys"
export SXMO_VOLUME_BUTTON="0:0:mt6779-keypad 1:1:mtk-pmic-keys"
export SXMO_SWAY_SCALE="2.75"
export SXMO_DISABLE_LEDS="1"
S
    chmod 755 '/usr/bin/sxmo_deviceprofile_samsung,a22x.sh'
    # With a profile Sxmo looks for hooks of this device by name; the generic ones fit (without
    # this link the lock, unlock, gesture and button hooks were missing).
    [ -e '/usr/share/sxmo/default_hooks/samsung,a22x' ] || ln -s unknown '/usr/share/sxmo/default_hooks/samsung,a22x'
    shell_entry sxmo Sxmo /usr/bin/a22x-sxmo-session 'sway;'
}

lomiri_shell() {
    command=$(sed -n 's/^Exec=//p' /usr/share/wayland-sessions/lomiri.desktop 2>/dev/null | head -1)
    [ -n "$command" ] || command=lomiri-session
    cat > /usr/bin/a22x-lomiri-session <<S
#!/bin/sh
# Lomiri sizes its interface by grid units, and Mir scales the output by GRID_UNIT_PX / 8. At 24
# (scale 3) Mir crashed with SIGSEGV as soon as the shell came up (Debian 13 and Ubuntu 26.04,
# 2026-11-09): 2408 / 3 is no whole number, and Mir and QtMir rounded the logical height apart
# (803 and 802). A scale must divide 1080x2408: 16 px (scale 2) is the largest that looks right.
export MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver 2>/dev/null || echo kmsro) XWAYLAND_NO_GLAMOR=1 XDG_SESSION_TYPE=wayland GRID_UNIT_PX=16 DEVICEINFO_DEVICE_NAME=a22x
exec $command
S
    chmod 755 /usr/bin/a22x-lomiri-session
    # lomiri-session takes the grid unit from deviceinfo where it is installed (Ubuntu), whose
    # default for an unknown device is a desktop with 8 px (2026-11-09). Describe this phone.
    model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)
    mkdir -p /etc/deviceinfo/devices
    cat > /etc/deviceinfo/devices/a22x.yaml <<S
# Written by the A22x recovery.
a22x:
  Names:
    - ${model:-Samsung Galaxy A22 5G}
  PrettyName: Samsung Galaxy A22 5G
  DeviceType: phone
  GridUnit: 16
  PrimaryOrientation: Portrait
  WebkitDpr: 2
S
    shell_entry lomiri Lomiri /usr/bin/a22x-lomiri-session 'Lomiri;'
}

# A distribution may point display-manager.service at its own greeter (Manjaro ARM: greetd
# with its OEM setup); "systemctl enable" then fails, so the link is replaced (2026-11-08).
use_display_manager() {
    rm -f /etc/systemd/system/display-manager.service
    for other in gdm.service gdm3.service lightdm.service sddm.service greetd.service phosh.service; do
        [ "$other" = "$1" ] || systemctl disable "$other" 2>/dev/null
    done
    systemctl enable --force "$1"
    default_display_manager "$1"
}

# Debian and Ubuntu: a display manager's unit starts only when /etc/X11/default-display-manager
# names it (ExecStartPre), and the last one installed claims that file. GNOME's gdm3 took it from
# SDDM, whose check then failed five times in a row: no login screen (Ubuntu 26.04, 2026-11-09).
default_display_manager() {
    for file in /etc/systemd/system/$1 /usr/lib/systemd/system/$1 /lib/systemd/system/$1; do
        [ -f "$file" ] && break
    done
    [ -f "$file" ] && grep -q default-display-manager "$file" || return 0
    bin=$(sed -n 's/^ExecStart=[-@!+:]*\([^ ]*\).*/\1/p' "$file" | head -1)
    [ -n "$bin" ] || return 0
    mkdir -p /etc/X11
    printf '%s\n' "$bin" > /etc/X11/default-display-manager
    if command -v debconf-set-selections >/dev/null; then
        echo "shared/default-x-display-manager select ${bin##*/}" | debconf-set-selections 2>/dev/null
    fi
    say "Debian's display manager choice: $bin"
}

unit_exists() {
    [ -f "/usr/lib/systemd/system/$1" ] || [ -f "/lib/systemd/system/$1" ]
}

# greetd's configuration, on the terminal its service reserves (another one may hold a getty):
# greetd_write USER COMMAND [AUTOLOGIN]. Mobian starts greetd with a file of its own
# (/etc/greetd/phrog.toml), which gets the same.
# A session greetd opens is of logind type "tty" unless its greeter says otherwise, and the text
# greeters and automatic sign-in do not. GNOME Shell, started as a user service, looks for the
# user's graphical session and gave up: "Session '5' is not a graphical session (type: 'tty')"
# (Ubuntu 24.04, text login, 2026-11-09). Every shell signed in through greetd is a Wayland one
# (Xfce's Xorg does not mind), so greetd's sessions say "wayland" from the start: pam_env sets it
# before pam_systemd opens the session, as the first session line of greetd's PAM service.
# greetd 0.9 opens its default session, which is the shell itself with automatic sign-in, with
# the greetd-greeter service where there is one: after "Switch to GNOME" that session was "tty"
# again and GNOME gave up twice (Ubuntu 24.04, 2026-11-10), so that service gets the line too.
greetd_session_type() {
    [ -f /etc/pam.d/greetd ] || return 0
    mkdir -p /etc/security
    printf 'XDG_SESSION_TYPE DEFAULT=wayland\n' > /etc/security/a22x-greetd-env.conf
    line='session optional pam_env.so readenv=0 user_readenv=0 conffile=/etc/security/a22x-greetd-env.conf'
    for pam in /etc/pam.d/greetd /etc/pam.d/greetd-greeter; do
        [ -f "$pam" ] || continue
        grep -q 'a22x-greetd-env.conf' "$pam" && continue
        if head -n 1 "$pam" | grep -q '^#%PAM'; then
            sed -i "1a $line" "$pam"
        else
            sed -i "1i $line" "$pam"
        fi
    done
}

greetd_write() {
    greetd_session_type
    vt=$(sed -n 's/.*getty@tty\([0-9][0-9]*\).*/\1/p' /usr/lib/systemd/system/greetd.service 2>/dev/null | head -1)
    mkdir -p /etc/greetd
    [ ! -f /etc/greetd/config.toml ] || cp -n /etc/greetd/config.toml /etc/greetd/config.toml.image
    {
        printf '[terminal]\nvt = %s\n' "${vt:-1}"
        [ -z "${3:-}" ] || printf '[initial_session]\ncommand = "%s"\nuser = "%s"\n' "$2" "$3"
        printf '[default_session]\ncommand = "%s"\nuser = "%s"\n' "$2" "$1"
    } > /etc/greetd/config.toml
    if [ -f /etc/greetd/phrog.toml ]; then
        cp -n /etc/greetd/phrog.toml /etc/greetd/phrog.toml.image
        cp /etc/greetd/config.toml /etc/greetd/phrog.toml
    fi
}

# The account greetd shows its login screen as: the distribution's, else one made here.
greeter_user() {
    for name in $(awk '/^\[/ {s=$0} s=="[default_session]" && /^user *=/ {gsub(/^user *= *"|".*$/, ""); print}' \
                      /etc/greetd/config.toml.image 2>/dev/null) greeter _greetd greetd; do
        [ "$name" != "${NEW_USER:-}" ] && [ "$name" != "${PHONE_USER:-}" ] && [ "$name" != root ] || continue
        id "$name" >/dev/null 2>&1 && { echo "$name"; return 0; }
    done
    useradd -r -M -d /var/lib/a22x-greeter -s "$(command -v nologin || echo /bin/false)" greeter >/dev/null 2>&1 &&
        echo greeter
}

# Phrog: a touch login screen made of Phosh, with the on-screen keyboard. It lists exactly the
# shells recovery set up, and works with a distribution's Phrog package as with the one built
# here; this is the setup of the original Arch system (2026-10-17) for every distribution.
phrog_greeter() {
    login=$1
    user=$(greeter_user)
    [ -n "$user" ] || return 1
    # Phrog lists the sessions in no fixed order and takes the first one unless it remembers one
    # (2026-11-09): the main shell and the account are its defaults, beside its own schema.
    main=$(head -n 1 /etc/a22x/shells | cut -d'|' -f1)
    for dir in /usr/local/share/glib-2.0/schemas /usr/share/glib-2.0/schemas; do
        [ -f "$dir/mobi.phosh.phrog.gschema.xml" ] || continue
        printf "[mobi.phosh.phrog]\nlast-session='a22x-%s'\nlast-user='%s'\n" "$main" "$login" > "$dir/90_a22x-phrog.gschema.override"
        glib-compile-schemas "$dir"
    done
    for group in video input render; do
        getent group "$group" >/dev/null && usermod -aG "$group" "$user"
    done
    mkdir -p /var/lib/a22x-greeter /etc/phrog /usr/local/libexec
    chmod 700 /var/lib/a22x-greeter
    chown "$user": /var/lib/a22x-greeter
    printf '[output:DSI-1]\nscale=2.75\n' > /etc/phrog/phoc.ini
    cat > /usr/local/libexec/a22x-greeter <<'S'
#!/bin/sh
# Phrog on phoc for greetd, written by the A22x recovery.
: "${GREETD_SOCK:?started by greetd}" "${XDG_RUNTIME_DIR:?}"
export HOME=/var/lib/a22x-greeter
export XDG_CONFIG_HOME=$HOME/.config XDG_CACHE_HOME=$HOME/.cache XDG_DATA_HOME=$HOME/.local/share XDG_STATE_HOME=$HOME/.local/state
export XDG_CURRENT_DESKTOP=Phrog:Phosh:GNOME XDG_SESSION_TYPE=wayland GDK_BACKEND=wayland MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver 2>/dev/null || echo kmsro) XWAYLAND_NO_GLAMOR=1
export PATH=/usr/local/bin:/usr/bin:/bin
export GNOME_SESSION_AUTOSTART_DIR=/usr/share/phrog/autostart:/usr/local/share/phrog/autostart:/etc/phrog/autostart
# Phrog lists every session it finds: it sees the system's data without the distribution's
# session lists, and the shells recovery set up as the only ones.
run=$XDG_RUNTIME_DIR/a22x-greeter
rm -rf "$run"
mkdir -p "$run/sessions/wayland-sessions" "$run/local" "$run/usr"
for entry in /usr/share/a22x/sessions/*.desktop; do
    [ -f "$entry" ] && ln -s "$entry" "$run/sessions/wayland-sessions/"
done
for item in /usr/local/share/* /usr/share/*; do
    case "${item##*/}" in wayland-sessions|xsessions|\*) continue ;; esac
    case "$item" in /usr/local/*) ln -s "$item" "$run/local/" ;; *) ln -s "$item" "$run/usr/" ;; esac
done
export XDG_DATA_DIRS=$run/sessions:$run/local:$run/usr
phoc=/usr/bin/phoc
[ -x /opt/a22x-phoc/bin/phoc ] && phoc=/opt/a22x-phoc/bin/phoc
dbus=
if [ -S "$XDG_RUNTIME_DIR/bus" ]; then
    export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
else
    dbus=dbus-run-session
fi
log=
command -v systemd-cat >/dev/null 2>&1 && log="systemd-cat --identifier=a22x-greeter"
exec $log $dbus "$phoc" -S -C /etc/phrog/phoc.ini -E /usr/local/libexec/a22x-greeter-session
S
    cat > /usr/local/libexec/a22x-greeter-session <<'S'
#!/bin/sh
# The login screen's own session: Phrog, the on-screen keyboard, media keys and power handling.
# The environment goes to the session's services first: gsettings starts the settings service,
# which otherwise wrote to "/.config" (the greeter account's home is "/") and Phrog could not
# remember the last user and shell (2026-11-09).
vars="HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_DATA_DIRS XDG_CURRENT_DESKTOP
      XDG_SESSION_TYPE WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS GREETD_SOCK GDK_BACKEND
      MESA_LOADER_DRIVER_OVERRIDE GNOME_SESSION_AUTOSTART_DIR PATH"
if command -v dbus-update-activation-environment >/dev/null 2>&1; then
    dbus-update-activation-environment --systemd $vars 2>/dev/null
else
    systemctl --user import-environment $vars 2>/dev/null
fi
gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled true 2>/dev/null
exec gnome-session --session=phrog
S
    chmod 755 /usr/local/libexec/a22x-greeter /usr/local/libexec/a22x-greeter-session
    # Phrog before 0.47 reads the sessions only from /usr/share/wayland-sessions and xsessions,
    # not from XDG_DATA_DIRS (Debian 13 has 0.46, 2026-11-09): there it listed the distribution's
    # Plasma desktop and X11 sessions and missed the remembered shell. The greeter account's
    # service manager, which starts Phrog for gnome-session, then sees the shells recovery set up
    # in their place (greetd's own namespace did not reach Phrog). Newer Phrog keeps the view above.
    rm -f /etc/systemd/system/greetd.service.d/90-a22x-sessions.conf
    rmdir /etc/systemd/system/greetd.service.d 2>/dev/null
    uid=$(id -u "$user" 2>/dev/null)
    for old in /etc/systemd/system/user@*.service.d/90-a22x-sessions.conf; do
        [ -f "$old" ] && rm -f "$old" && rmdir "${old%/*}" 2>/dev/null
    done
    bin=$(command -v phrog 2>/dev/null)
    if [ -n "$uid" ] && [ -n "$bin" ] && grep -q /usr/share/wayland-sessions "$bin"; then
        dropin=/etc/systemd/system/user@$uid.service.d/90-a22x-sessions.conf
        mkdir -p "${dropin%/*}" /usr/share/xsessions
        printf '[Service]\nBindReadOnlyPaths=/usr/share/a22x/sessions:/usr/share/wayland-sessions\nTemporaryFileSystem=/usr/share/xsessions:ro\n' > "$dropin"
        say "old Phrog: the login screen sees only the shells set up here"
    fi
    greetd_write "$user" /usr/local/libexec/a22x-greeter
}

# A text login for a USB or Bluetooth keyboard; it starts the shell chosen last.
text_greeter() {
    user=$(greeter_user)
    [ -n "$user" ] || return 1
    if command -v tuigreet >/dev/null; then
        greetd_write "$user" "tuigreet --cmd 'env A22X_SIGNED_IN=1 /usr/bin/a22x-shell' --remember --asterisks --time"
    elif command -v agreety >/dev/null; then
        greetd_write "$user" "agreety --cmd 'env A22X_SIGNED_IN=1 /usr/bin/a22x-shell'"
    else
        return 1
    fi
    # The text login on this panel (Arch, 2026-11-09): the boot splash, which stops when a
    # compositor takes the display, kept moving over it for its 300 s limit, and the console's
    # 8x16 font was tiny at 1080x2408.
    mkdir -p /etc/systemd/system/greetd.service.d
    printf '[Service]\nExecStartPre=-/bin/sh -c "echo 1 > /sys/module/mediatek_drm/parameters/boot_splash_stop"\n' \
        > /etc/systemd/system/greetd.service.d/80-a22x-text-login.conf
    for font in /usr/share/kbd/consolefonts/ter-132b.psf.gz /usr/lib/kbd/consolefonts/ter-132b.psf.gz \
                /usr/share/consolefonts/Uni2-Terminus32x16.psf.gz; do
        [ -f "$font" ] || continue
        name=${font##*/}; name=${name%.psf.gz}
        # greetd runs on its own VT (7 on Debian), which the boot-time font setup skips
        # (console-setup: ACTIVE_CONSOLES /dev/tty[1-6]): the login stayed 8x16, and its Cyrillic
        # was unreadable (Mobian, 2026-11-10). greetd sets the font on its VT before it starts.
        vt=$(sed -n 's/^vt *= *\([0-9][0-9]*\).*/\1/p' /etc/greetd/config.toml 2>/dev/null | head -1)
        setfont=
        for tool in /usr/bin/setfont /bin/setfont /usr/sbin/setfont; do
            [ -x "$tool" ] && setfont=$tool && break
        done
        if [ -n "$vt" ] && [ -n "$setfont" ]; then
            printf 'ExecStartPre=-%s -C /dev/tty%s %s\n' "$setfont" "$vt" "$font" \
                >> /etc/systemd/system/greetd.service.d/80-a22x-text-login.conf
        fi
        touch /etc/vconsole.conf
        if grep -q '^FONT=' /etc/vconsole.conf; then sed -i "s|^FONT=.*|FONT=$name|" /etc/vconsole.conf
        else printf 'FONT=%s\n' "$name" >> /etc/vconsole.conf; fi
        # Debian and Ubuntu set the console font with console-setup, which ignores vconsole.conf:
        # the text login stayed at 8x16 (Ubuntu 24.04, 2026-11-09).
        if [ -f /etc/default/console-setup ]; then
            sed -i -e 's/^FONTFACE=.*/FONTFACE="Terminus"/' -e 's/^FONTSIZE=.*/FONTSIZE="16x32"/' /etc/default/console-setup
            grep -q '^FONTFACE=' /etc/default/console-setup || printf 'FONTFACE="Terminus"\n' >> /etc/default/console-setup
            grep -q '^FONTSIZE=' /etc/default/console-setup || printf 'FONTSIZE="16x32"\n' >> /etc/default/console-setup
        fi
        return 0
    done
    say "text login: no large console font package; the login text stays small"
}

# The shells as sessions of a display manager; Xfce as an X11 one, which it starts itself.
dm_sessions() {
    mkdir -p /usr/local/share/wayland-sessions /usr/local/share/xsessions
    rm -f /usr/local/share/wayland-sessions/a22x-*.desktop /usr/local/share/xsessions/a22x-*.desktop
    while IFS='|' read -r id name command; do
        if [ "$id" = xfce ]; then
            printf '[Desktop Entry]\nName=%s\nExec=/usr/bin/a22x-xfce-start\nType=Application\nDesktopNames=XFCE;\n' "$name" \
                > /usr/local/share/xsessions/a22x-xfce.desktop
        else
            cp "/usr/share/a22x/sessions/a22x-$id.desktop" /usr/local/share/wayland-sessions/
        fi
    done < /etc/a22x/shells
}

# The display managers draw on this panel's GPU path too, and preselect the main shell.
dm_common() {
    mkdir -p /etc/environment.d /var/lib/AccountsService/users
    printf 'MESA_LOADER_DRIVER_OVERRIDE=%s\n' "$(mesa_driver)" > /etc/environment.d/90-a22x-gpu.conf
    main=$(head -n 1 /etc/a22x/shells | cut -d'|' -f1)
    printf '[User]\nSession=a22x-%s\nXSession=a22x-%s\nSystemAccount=false\n' "$main" "$main" \
        > "/var/lib/AccountsService/users/$1"
}

gdm_greeter() {
    unit=
    for name in gdm.service gdm3.service; do
        unit_exists "$name" && { unit=$name; break; }
    done
    [ -n "$unit" ] || return 1
    # GDM also reads the distribution's session lists: its entries are hidden there.
    for entry in /usr/share/wayland-sessions/*.desktop /usr/share/xsessions/*.desktop; do
        [ -f "$entry" ] || continue
        kind=${entry%/*}
        kind=${kind##*/}
        name=${entry##*/}
        case "$name" in a22x-*) continue ;; esac
        printf '[Desktop Entry]\nName=%s\nExec=true\nType=Application\nHidden=true\n' "${name%.desktop}" \
            > "/usr/local/share/$kind/$name"
    done
    dm_common "$1"
    use_display_manager "$unit"
}

sddm_greeter() {
    unit_exists sddm.service || return 1
    # The login screen should come out at about 2.2. KWin scales this panel by itself (2.7), and
    # Qt's factor multiplies that: 2.75 and then 2 gave 7.4 and 5.4, a desktop layout cut off at
    # the panel's width (Ubuntu 26.04, 2026-11-09; Breeze measured in test mode at 2.0, 2.43, 2.7).
    # At 2.43 a long date ("segunda-feira, 9 de novembro de 2026") and the last power button were
    # still cut at the edges (Mobian, 2026-11-10). Weston draws at 1.
    compositor= scale=
    if command -v kwin_wayland >/dev/null; then
        compositor='kwin_wayland --drm --no-lockscreen --no-global-shortcuts --locale1' scale=0.8
    elif command -v weston >/dev/null; then
        compositor='weston --shell=kiosk' scale=2.2
    fi
    mkdir -p /etc/sddm.conf.d
    {
        printf '[General]\n'
        if [ -n "$compositor" ]; then
            printf 'DisplayServer=wayland\nInputMethod=qtvirtualkeyboard\n'
            printf 'GreeterEnvironment=QT_WAYLAND_SHELL_INTEGRATION=layer-shell,QT_SCALE_FACTOR=%s,MESA_LOADER_DRIVER_OVERRIDE=%s\n' "$scale" "$(mesa_driver)"
            printf '[Wayland]\nCompositorCommand=%s\n' "$compositor"
        else
            printf '[Wayland]\n'
        fi
        printf 'SessionDir=/usr/local/share/wayland-sessions\n[X11]\nSessionDir=/usr/local/share/xsessions\n'
        # Ubuntu's built-in default names a theme it does not ship (ubuntu-theme).
        if [ -f /usr/share/sddm/themes/breeze/metadata.desktop ]; then printf '[Theme]\nCurrent=breeze\n'; fi
    } > /etc/sddm.conf.d/10-a22x.conf
    if [ ! -f /usr/share/sddm/themes/breeze/metadata.desktop ]; then
        say "SDDM: no Breeze theme; its built-in screen is made for desktops"
    fi
    # SDDM starts with the last session it remembers, else the first by name (GNOME before Plasma).
    main=$(head -n 1 /etc/a22x/shells | cut -d'|' -f1)
    session=/usr/local/share/wayland-sessions/a22x-$main.desktop
    [ -f "$session" ] || session=/usr/local/share/xsessions/a22x-$main.desktop
    mkdir -p /var/lib/sddm
    printf '[Last]\nUser=%s\nSession=%s\n' "$1" "$session" > /var/lib/sddm/state.conf
    chown sddm:sddm /var/lib/sddm /var/lib/sddm/state.conf 2>/dev/null
    # Without a Wayland compositor SDDM draws its screen with Xorg.
    [ -n "$compositor" ] || xorg_display
    rm -f /etc/sddm.conf.d/50-a22x-autologin.conf
    dm_common "$1"
    use_display_manager sddm.service
}

lightdm_greeter() {
    unit_exists lightdm.service || return 1
    xorg_display
    main=$(head -n 1 /etc/a22x/shells | cut -d'|' -f1)
    mkdir -p /etc/lightdm/lightdm.conf.d
    # sessions-directory is a [LightDM] key: under [Seat:*] LightDM ignored it, did not find our
    # sessions and started the first one of the distribution, Plasma's desktop session instead of
    # Plasma Mobile (Manjaro, 2026-11-09).
    # The GTK greeter draws GTK at 2 like Xfce (fonts 132 dpi x 2 = 2.75); at 1 its sign-in box was
    # a small window in the middle of the panel.
    mkdir -p /usr/local/libexec
    cat > /usr/local/libexec/a22x-lightdm-greeter <<'S'
#!/bin/sh
export GDK_SCALE=2
exec "$@"
S
    chmod 755 /usr/local/libexec/a22x-lightdm-greeter
    {
        printf '[LightDM]\nsessions-directory=/usr/local/share/wayland-sessions:/usr/local/share/xsessions\n'
        printf '[Seat:*]\nuser-session=a22x-%s\ngreeter-wrapper=/usr/local/libexec/a22x-lightdm-greeter\n' "$main"
        [ ! -f /usr/share/xgreeters/lightdm-gtk-greeter.desktop ] || printf 'greeter-session=lightdm-gtk-greeter\n'
    } > /etc/lightdm/lightdm.conf.d/50-a22x.conf
    # A touch login: a keyboard on the screen from the start, and text sized for this panel
    # (2.75 x 96 dpi). The greeter reads conf.d before its own file, which leaves these unset.
    keyboard=
    if command -v onboard >/dev/null; then
        keyboard='onboard --xid'
    elif command -v xvkbd >/dev/null; then
        keyboard='xvkbd -xdm -compact'
    fi
    mkdir -p /etc/xdg/lightdm/lightdm-gtk-greeter.conf.d
    {
        printf '[greeter]\nxft-dpi=132\n'
        [ -z "$keyboard" ] || printf 'keyboard=%s\na11y-states=+keyboard\n' "$keyboard"
    } > /etc/xdg/lightdm/lightdm-gtk-greeter.conf.d/50-a22x.conf
    [ -n "$keyboard" ] || say "LightDM: no on-screen keyboard package; a USB or Bluetooth keyboard is needed to sign in"
    dm_common "$1"
    use_display_manager lightdm.service
}

# The sign-in screen chosen in recovery; one that cannot be set up gives way to automatic
# sign-in, which starts the main shell with its own lock screen.
greeter() {
    login=$1
    case "$GREETER" in
    phrog)
        if phrog_ready && phrog_greeter "$login"; then use_display_manager greetd.service; return; fi
        say "the Phrog login screen is not available; signing in automatically instead"
        GREETER=auto ;;
    text)
        if text_greeter; then use_display_manager greetd.service; return; fi
        say "no text login program is installed; signing in automatically instead"
        GREETER=auto ;;
    gdm|sddm|lightdm)
        dm_sessions
        if "${GREETER}_greeter" "$login"; then return; fi
        say "$GREETER is not installed; signing in automatically instead"
        GREETER=auto ;;
    esac
    # Automatic sign-in goes through greetd. An image that brings Phosh without it (Manjaro ARM
    # Phosh, whose own setup program would have enabled phosh.service) is started through that
    # unit instead, as the same user and through a22x-shell; nothing is downloaded for it
    # (2026-11-12, on the phone: "unit greetd.service does not exist", and a text login).
    if ! greetd_installed && phosh_unit_file >/dev/null; then
        case " $SHELLS " in
        " phosh "*)
            say "this image has no greetd: Phosh starts through its own phosh.service"
            phosh_unit "$login"
            use_display_manager phosh.service
            return ;;
        esac
    fi
    greetd_write "$login" /usr/bin/a22x-shell "$login"
    use_display_manager greetd.service
}

greetd_installed() {
    command -v greetd >/dev/null 2>&1 || [ -f /usr/lib/systemd/system/greetd.service ] || [ -f /lib/systemd/system/greetd.service ]
}

phosh_unit_file() {
    for unit in /etc/systemd/system/phosh.service /usr/lib/systemd/system/phosh.service /lib/systemd/system/phosh.service; do
        [ -f "$unit" ] && { echo "$unit"; return 0; }
    done
    return 1
}

# The image's phosh.service signs in the first account and runs phosh-session; here it signs in
# the account chosen in the recovery and runs the shell picked last, as greetd would.
phosh_unit() {
    mkdir -p /etc/systemd/system/phosh.service.d
    printf '[Service]\nUser=%s\nExecStart=\nExecStart=/usr/bin/a22x-shell\n' "$1" > /etc/systemd/system/phosh.service.d/10-a22x.conf
}

# Switching shells. a22x-shell starts the shell this user picked last, else the main one; a
# picked shell that ends within 20 seconds twice in a row gives way to the main one, so a
# broken shell cannot hold the phone in a loop. With automatic sign-in and the text login a
# launcher per shell picks it and signs out; with a login screen that lists the shells, one
# launcher signs out. The running shell's own launcher is hidden for the user at each start:
# Phosh also calls itself GNOME, so NotShowIn hid "Switch to GNOME" in Phosh and "Switch to
# Phosh" in GNOME, and neither could be reached (Ubuntu with Phosh and GNOME, 2026-11-10).
shell_switch() {
    cat > /usr/bin/a22x-shell <<'S'
#!/bin/sh
# Starts the shell picked last with "Switch to ...", else the main one (A22x recovery).
list=/usr/share/a22x/shells
state=${XDG_CONFIG_HOME:-$HOME/.config}/a22x
choice=
[ ! -f "$state/shell" ] || choice=$(head -c 32 "$state/shell" | tr -cd 'a-z')
main=$(head -n 1 "$list" | cut -d'|' -f1)
mark=/tmp/a22x-shell-$(id -u)
fails=0
if [ -n "$choice" ] && [ "$choice" != "$main" ] && [ -f "$mark" ]; then
    read -r last when count < "$mark"
    if [ "$last" = "$choice" ] && [ $(( $(date +%s) - ${when:-0} )) -lt 20 ]; then
        fails=$(( ${count:-0} + 1 ))
    fi
    if [ "$fails" -ge 2 ]; then
        rm -f "$state/shell"
        choice=
        fails=0
    fi
fi
line=
[ -z "$choice" ] || line=$(grep "^$choice|" "$list" | head -n 1)
[ -n "$line" ] || line=$(head -n 1 "$list")
id=${line%%|*}
echo "$id $(date +%s) $fails" > "$mark"
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
for entry in /usr/local/share/applications/a22x-switch-*.desktop; do
    [ -f "$entry" ] || continue
    own=$apps/${entry##*/}
    if [ "${entry##*/}" = "a22x-switch-$id.desktop" ]; then
        mkdir -p "$apps" && printf '[Desktop Entry]\nType=Application\nName=%s\nExec=/usr/bin/a22x-switch-shell %s\nNoDisplay=true\nHidden=true\nX-A22x-Running-Shell=true\n' "$id" "$id" > "$own"
    elif grep -q '^X-A22x-Running-Shell=true' "$own" 2>/dev/null; then
        rm -f "$own"
    fi
done
exec ${line##*|}
S
    cat > /usr/bin/a22x-switch-shell <<'S'
#!/bin/sh
# a22x-switch-shell [ID]: start shell ID at the next sign-in, then sign out (A22x recovery).
if [ -n "${1:-}" ]; then
    grep -q "^$1|" /usr/share/a22x/shells 2>/dev/null || { echo "a22x-switch-shell: no shell '$1'" >&2; exit 2; }
    running=$(cut -d' ' -f1 "/tmp/a22x-shell-$(id -u)" 2>/dev/null)
    [ "$1" != "$running" ] || exit 0  # Already this shell: nothing to sign out of.
    state=${XDG_CONFIG_HOME:-$HOME/.config}/a22x
    mkdir -p "$state" && printf '%s\n' "$1" > "$state/shell"
    rm -f "/tmp/a22x-shell-$(id -u)"
fi
case ":${XDG_CURRENT_DESKTOP:-}:" in
*:Phosh:*|*:GNOME:*)
    gdbus call --session --dest org.gnome.SessionManager --object-path /org/gnome/SessionManager \
        --method org.gnome.SessionManager.Logout 1 >/dev/null 2>&1 && exit 0 ;;
*:KDE:*)
    gdbus call --session --dest org.kde.Shutdown --object-path /Shutdown \
        --method org.kde.Shutdown.logout >/dev/null 2>&1 && exit 0 ;;
*:XFCE:*) xfce4-session-logout --logout --fast >/dev/null 2>&1 && exit 0 ;;
*:sway:*)
    # Sway closes its socket before answering "exit", so swaymsg reports a failure even then;
    # terminating the session instead would cut Sxmo's cleanup short (2026-11-09).
    if swaymsg -t get_version >/dev/null 2>&1; then swaymsg exit >/dev/null 2>&1; exit 0; fi ;;
esac
[ -z "${XDG_SESSION_ID:-}" ] || exec loginctl terminate-session "$XDG_SESSION_ID"
exec loginctl terminate-user "$(id -un)"
S
    chmod 755 /usr/bin/a22x-shell /usr/bin/a22x-switch-shell
    rm -f /usr/local/share/applications/a22x-switch-*.desktop
    [ "$(wc -l < /etc/a22x/shells)" -ge 2 ] || return 0
    mkdir -p /usr/local/share/applications
    case "$GREETER" in
    auto|text)
        while IFS='|' read -r id name command; do
            cat > "/usr/local/share/applications/a22x-switch-$id.desktop" <<S
[Desktop Entry]
Type=Application
Name=Switch to $name
Name[uk]=Перейти до $name
Name[de]=Zu $name wechseln
Name[fr]=Passer à $name
Name[pl]=Przełącz na $name
Name[es]=Cambiar a $name
Comment=Sign out and start $name
Comment[uk]=Вийти й запустити $name
Exec=/usr/bin/a22x-switch-shell $id
Icon=system-log-out
Terminal=false
Categories=System;
X-Purism-FormFactor=Workstation;Mobile;
S
        done < /etc/a22x/shells ;;
    *)
        cat > /usr/local/share/applications/a22x-switch-shell.desktop <<'S'
[Desktop Entry]
Type=Application
Name=Change shell
Name[uk]=Змінити оболонку
Name[de]=Oberfläche wechseln
Name[fr]=Changer d'interface
Name[pl]=Zmień powłokę
Name[es]=Cambiar de entorno
Comment=Sign out; the login screen lists the shells
Comment[uk]=Вийти; екран входу покаже оболонки
Exec=/usr/bin/a22x-switch-shell
Icon=system-log-out
Terminal=false
Categories=System;
S
        ;;
    esac
}

session() {
    home_shims
    gpu_driver_tool
    login=${NEW_USER:-${PHONE_USER:-user}}
    for group in video input render audio; do
        getent group "$group" >/dev/null && usermod -aG "$group" "$login"
    done
    mkdir -p /etc/a22x /usr/share/a22x/sessions
    : > /etc/a22x/shells.new
    rm -f /usr/share/a22x/sessions/a22x-*.desktop
    for shell in $SHELLS; do
        "${shell}_shell" || say "the $shell shell was not set up"
    done
    if [ ! -s /etc/a22x/shells.new ]; then
        rm -f /etc/a22x/shells.new
        say "no shell was set up"
        return 1
    fi
    mv -f /etc/a22x/shells.new /etc/a22x/shells
    # The sign-in runs a22x-shell as the user, and /etc/a22x is private (0700): reading the list
    # there failed, a22x-shell ran an empty command, and the text login returned at once
    # (Arch, 2026-11-09). The user side reads this copy.
    mkdir -p /usr/share/a22x
    cp -f /etc/a22x/shells /usr/share/a22x/shells
    chmod 0644 /usr/share/a22x/shells
    # X11 programs under Xwayland asked the display device for scanout buffers through glamor and
    # DRI3 and crashed: "DRM_IOCTL_MODE_CREATE_DUMB failed: Permission denied" (Plasma's ksmserver
    # and tray proxies, xwaylandvideobridge; 2026-11-09). Without glamor they draw in software and
    # run; Wayland programs are untouched. Only Xwayland reads this; the user's services (KWin,
    # GNOME Shell) take it from environment.d, the session wrappers export it themselves.
    mkdir -p /etc/environment.d
    printf 'XWAYLAND_NO_GLAMOR=1\n' > /etc/environment.d/90-a22x-xwayland.conf
    greeter "$login"
    shell_switch
    systemctl enable NetworkManager.service
    systemctl set-default graphical.target
}

resolv_link=$(readlink /etc/resolv.conf 2>/dev/null)
# A booted system gets its /var skeleton from systemd-tmpfiles; a root just unpacked in the
# recovery's chroot may not have it yet (Fedora 45 Cloud, 2026-11-08).
mkdir -p /var/log
if [ -n "${A22X_CHROOT:-}" ] && command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create --prefix=/var >/dev/null 2>&1
fi
say "start: desktop=$DESKTOP shells=${EXTRA_SHELLS:-none} sign-in=${GREETER:-auto} apps=${APPS:-none}"
account || say "account step failed"
# Sxmo and Lomiri come from Debian and Ubuntu (and Debian-based images); elsewhere Phosh.
case "$DESKTOP" in
sxmo|lomiri)
    if ! command -v apt-get >/dev/null; then
        say "$DESKTOP is packaged for Debian and Ubuntu only; installing Phosh instead"
        DESKTOP=phosh
    fi ;;
esac
# An image that already has Phosh gets the same start path as an installed one.
if [ "$DESKTOP" = image ] && command -v phosh-session >/dev/null; then
    DESKTOP=phosh
    OFFLINE=1
fi
# The shells to set up: the main one first, then those added in recovery (2026-11-09). Sxmo
# and Lomiri only where Debian or Ubuntu packages them.
shells=
for shell in $DESKTOP ${EXTRA_SHELLS:-}; do
    case "$shell" in
    phosh|gnome|plasma|xfce) ;;
    sxmo|lomiri) command -v apt-get >/dev/null || { say "$shell is packaged for Debian and Ubuntu only; skipped"; continue; } ;;
    *) continue ;;
    esac
    case " $shells " in *" $shell "*) continue ;; esac
    shells="$shells $shell"
done
SHELLS=${shells# }
case "${GREETER:-auto}" in
auto|phrog|text|gdm|sddm|lightdm) GREETER=${GREETER:-auto} ;;
*) GREETER=auto ;;
esac
desktop_packages=
[ "$DESKTOP" = console ] || [ "$DESKTOP" = image ] || [ -n "${OFFLINE:-}" ] || desktop_packages=1
# 2026-11-12, the owner: "why is phoc no longer built by itself for this phone?" The build ran
# only where the desktop was installed here; an image that brings Phosh (Manjaro ARM Phosh, chosen
# "as in the image") skipped it. Such an image gets the build too, when the network is there.
phone_build=
if [ -z "$desktop_packages" ] && [ "$DESKTOP" = phosh ] && phone_build_needed; then
    phone_build=1
fi
if [ -n "$desktop_packages" ] || [ -n "${APPS:-}" ] || [ -n "${EXTRA_PACKAGES:-}" ]; then
    wifi || retry_later "Wi-Fi unavailable"
    swap_on || say "continuing without a temporary swap"
    if [ -n "$desktop_packages" ]; then
        # The login prompt is already on screen: say that the setup is still at work.
        if [ -n "${A22X_CHROOT:-}" ]; then
            say "installing the desktop and apps; this takes 10-30 minutes"
        else
            say "installing the desktop and apps; this takes 10-30 minutes, then the phone restarts by itself"
        fi
        packages >> "$LOG" 2>&1 || { swap_off; retry_later "package install failed (see $LOG)"; }
        refreshed=1
    fi
    [ -z "${APPS:-}" ] || say "installing apps: $APPS"
    apps >> "$LOG" 2>&1 || say "some apps were not installed; see $LOG"
    extra_packages >> "$LOG" 2>&1 || say "some packages were not installed; see $LOG"
    audio >> "$LOG" 2>&1
    if [ -n "$desktop_packages" ]; then
        build_for_phone
    elif [ -n "$phone_build" ]; then
        if refresh pacman >> "$LOG" 2>&1; then
            build_for_phone
        else
            say "the system could not be brought up to date, so phoc's fix for this phone is not built; Phosh may not start (see $LOG)"
        fi
    fi
    swap_off
elif [ -n "$phone_build" ]; then
    # Nothing else needs the network: without it the system still gets its session, and says so.
    if wifi; then
        swap_on || say "continuing without a temporary swap"
        say "building for this phone what the image's Phosh needs; this takes 5-10 minutes"
        if refresh pacman >> "$LOG" 2>&1; then
            build_for_phone
        else
            say "the system could not be brought up to date, so phoc's fix for this phone is not built; Phosh may not start (see $LOG)"
        fi
        swap_off
    else
        say "no network: phoc's fix for this phone is not built; Phosh may not start"
    fi
elif [ -f /etc/a22x/setup-script ]; then
    wifi || say "no network for the setup script"
fi
system_locale >> "$LOG" 2>&1
system_timezone >> "$LOG" 2>&1
if [ "$DESKTOP" != console ] && [ "$DESKTOP" != image ]; then
    session >> "$LOG" 2>&1 || { say "session setup failed; see $LOG"; exit 0; }
elif [ "$DESKTOP" = image ]; then
    home_shims
fi
setup_script
persist_wifi >> "$LOG" 2>&1 || say "Wi-Fi for later starts is not set up; see $LOG"
if [ -n "${initramfs_deferred:-}" ]; then
    sed -i 's/^update_initramfs=no/update_initramfs=yes/' /etc/initramfs-tools/update-initramfs.conf
fi
if [ -n "${A22X_CHROOT:-}" ]; then
    # The recovery puts the resolver back and unmounts; the system starts ready.
    rm -f "$CONF" /etc/a22x/wpa.conf /etc/NetworkManager/dispatcher.d/90-a22x-firstboot
    say "finished"
    exit 0
fi
# The units made for this setup end here; installed ones (or NetworkManager) take over.
systemctl stop a22x-firstboot-dhcp.service a22x-firstboot-wifi.service 2>/dev/null
systemctl start a22x-wifi.service a22x-wifi-dhcp.service 2>/dev/null
# DHCP above wrote a plain resolv.conf; the system's own resolver link comes back.
[ -z "$resolv_link" ] || [ -L /etc/resolv.conf ] || ln -sfn "$resolv_link" /etc/resolv.conf
rm -f "$CONF" /etc/a22x/wpa.conf /etc/NetworkManager/dispatcher.d/90-a22x-firstboot
say "finished"
[ "$DESKTOP" = console ] || [ "$DESKTOP" = image ] || exec /usr/bin/a22x-reboot normal
