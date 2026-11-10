#!/bin/sh
# Wi-Fi in recovery. The MediaTek modules are built for the recovery kernel (wifi-modules.tar.xz);
# the Wi-Fi firmware comes from the platform files this recovery keeps for installed systems; the
# WMT steps are those of conn-start.sh in the installed systems.
#   a22x-wifi.sh up              load the driver and wait for wlan0
#   a22x-wifi.sh connect CONF    join the networks of a wpa_supplicant file and take an address
#   a22x-wifi.sh dhcp            take an address on the joined network, renewed in the background
#   a22x-wifi.sh release         give the address back
#   a22x-wifi.sh down            leave the network (the driver stays loaded)
#   a22x-wifi.sh status          wlan0, address, route and name servers
set -u
P=/opt/a22x-recovery/platform
B=$P/busybox
W=/run/a22x-wifi
# The launcher answers the chip's ROM patch request with the MCU, Bluetooth and Wi-Fi patches
# together; without the Bluetooth one it answers "fail" and the chip never starts (2026-11-08).
FIRMWARE="WIFI_RAM_CODE_soc2_2_1_1.bin WIFI_RAM_CODE_soc2_2_1a_1.bin soc2_2_ram_wifi_1_1_hdr.bin
soc2_2_ram_wifi_1a_1_hdr.bin soc2_2_ram_mcu_1_1_hdr.bin soc2_2_ram_mcu_1a_1_hdr.bin
soc2_2_ram_bt_1_1_hdr.bin soc2_2_ram_bt_1a_1_hdr.bin WMT_SOC.cfg wifi.cfg"

say() { echo "a22x-wifi: $*" >&2; }

loaded() { grep -q "^$1 " /proc/modules; }

load() {
    name=$1
    loaded "$name" && return 0
    insmod "$W/$name.ko" || { say "cannot load $name"; return 1; }
}

up() {
    [ -d /sys/class/net/wlan0 ] && return 0
    mkdir -p "$W" /lib/firmware && chmod 700 "$W" || return 1
    if [ ! -f "$W/wlan_6833_axi.ko" ]; then
        tar -xJf "$P/wifi-modules.tar.xz" -C "$W" || { say "no Wi-Fi modules for this kernel"; return 1; }
    fi
    need=
    for f in $FIRMWARE; do [ -f "/lib/firmware/$f" ] || need="$need usr/lib/firmware/$f"; done
    if [ -n "$need" ]; then
        # shellcheck disable=SC2086
        tar -xJf "$P/platform-files.tar.xz" -C "$W" $need || { say "no Wi-Fi firmware"; return 1; }
        mv "$W"/usr/lib/firmware/* /lib/firmware/ && rm -rf "$W/usr"
    fi
    load btif || return 1
    load wmt_drv || return 1
    if [ ! -f "$W/powered" ]; then
        "$P/a22x-wmt" load hwinit soc set adie init || { say "connectivity power-on failed"; return 1; }
        : > "$W/powered"
    fi
    if ! { [ -f "$W/launcher.pid" ] && kill -0 "$(cat "$W/launcher.pid")" 2>/dev/null; }; then
        # Answers the firmware requests of the connectivity chip, as a22x-wmt-launcher.service does.
        "$P/a22x-wmt" serve > "$W/launcher.log" 2>&1 &
        echo $! > "$W/launcher.pid"
        sleep 1
    fi
    load wmt_chrdev_wifi || return 1
    load wlan_6833_axi || return 1
    echo 1 > /dev/wmtWifi || { say "Wi-Fi power-on failed"; return 1; }
    n=0
    while [ ! -d /sys/class/net/wlan0 ] && [ $n -lt 30 ]; do sleep 1; n=$((n + 1)); done
    [ -d /sys/class/net/wlan0 ] || { say "wlan0 did not appear"; return 1; }
    $B ip link set wlan0 up
}

stop() {
    [ -f "$W/$1.pid" ] && kill "$(cat "$W/$1.pid")" 2>/dev/null
    rm -f "$W/$1.pid"
}

release() {
    stop udhcpc
    [ -d /sys/class/net/wlan0 ] && $B ip -4 addr flush dev wlan0
    return 0
}

dhcp() {
    [ -d /sys/class/net/wlan0 ] || { say "no wlan0"; return 1; }
    release
    mkdir -p "$W" && chmod 700 "$W"
    cat > "$W/udhcpc.script" <<'S'
#!/opt/a22x-recovery/platform/busybox sh
B=/opt/a22x-recovery/platform/busybox
[ "$1" = bound ] || [ "$1" = renew ] || exit 0
$B ifconfig "$interface" "$ip" netmask "$subnet"
for r in $router; do $B route add default gw "$r" dev "$interface" 2>/dev/null; done
for d in $dns; do echo "nameserver $d"; done > /etc/resolv.conf.a22x && mv /etc/resolv.conf.a22x /etc/resolv.conf
S
    chmod 700 "$W/udhcpc.script"
    # Background mode keeps the lease renewed for long downloads.
    $B udhcpc -i wlan0 -b -t 20 -T 3 -A 5 -s "$W/udhcpc.script" -p "$W/udhcpc.pid" >/dev/null 2>&1
}

connect() {
    conf=${1:-}
    [ -f "$conf" ] || { say "no network file"; return 1; }
    up || return 1
    release
    stop wpa
    "$P/wpa_supplicant" -B -i wlan0 -c "$conf" -D nl80211 -P "$W/wpa.pid" || { say "wpa_supplicant failed"; return 1; }
    dhcp || return 1
    n=0
    while [ $n -lt 60 ]; do
        $B ip -4 addr show wlan0 | grep -q inet && return 0
        sleep 1; n=$((n + 1))
    done
    say "no address on Wi-Fi: check the network name and password"
    return 1
}

down() {
    release
    stop wpa
    return 0
}

status() {
    [ -d /sys/class/net/wlan0 ] || { echo "wlan0: absent"; return 1; }
    $B ip -4 addr show wlan0 | sed -n 's/.*inet \([0-9.\/]*\).*/address \1/p'
    $B ip route show default
    sed -n 's/^nameserver /dns /p' /etc/resolv.conf 2>/dev/null
}

case "${1:-}" in
up) up ;;
connect) connect "${2:-}" ;;
dhcp) dhcp ;;
release) release ;;
down) down ;;
status) status ;;
*) echo "usage: a22x-wifi.sh up | connect CONF | dhcp | release | down | status" >&2; exit 2 ;;
esac
