#!/bin/sh
# Bring up MT6833 connectivity (CONSYS/WMT + Wi-Fi + Bluetooth) on the a22x
# mainline kernel. The modules are built out of tree (conn/build.sh) for this
# exact kernel. They cannot be unloaded and reloaded cleanly, so this never
# rmmods: a bring-up that stopped half way is reported and needs a reboot.
D=/opt/a22x-conn
MODS="btif wmt_drv wmt_chrdev_wifi wlan_6833_axi btmtk_stp"
REBOOT="reboot, then check: journalctl -b -u a22x-conn -u a22x-wmt-launcher"

fail() {
	echo "a22x-conn: $*" >&2
	exit 1
}

launcher_up() {
	systemctl start a22x-wmt-launcher.service ||
		fail "cannot start a22x-wmt-launcher.service"
	sleep 1
	systemctl is-active --quiet a22x-wmt-launcher.service ||
		fail "a22x-wmt-launcher.service is not running; $REBOOT"
}

# "1" is a no-op when Wi-Fi is already powered; a failed power-on is -EIO
wifi_on() {
	echo 1 > /dev/wmtWifi || fail "Wi-Fi power-on (/dev/wmtWifi) failed; $REBOOT"
	i=0
	while [ ! -e /sys/class/net/wlan0 ]; do
		[ $i -ge 10 ] && fail "Wi-Fi powered on but no wlan0 after 10 s; $REBOOT"
		sleep 1
		i=$((i + 1))
	done
}

loaded=""
missing=""
for m in $MODS; do
	if [ -d /sys/module/$m ]; then loaded="$loaded $m"; else missing="$missing $m"; fi
done

if [ -n "$loaded" ]; then
	# an earlier run got at least this far; only a complete one is usable
	[ -z "$missing" ] ||
		fail "partially initialized (loaded:$loaded; missing:$missing); the modules cannot be reloaded cleanly: $REBOOT"
	launcher_up
	wifi_on
	exit 0
fi

[ "$(cat $D/kernel-release 2>/dev/null)" = "$(uname -v)" ] || echo "a22x-conn: modules were built for another kernel build" >&2
insmod $D/btif.ko || fail "insmod btif.ko failed"
insmod $D/wmt_drv.ko || fail "insmod wmt_drv.ko failed; $REBOOT"
python3 $D/wmtload.py hwinit soc set adie init || fail "WMT chip init (wmtload.py) failed; $REBOOT"
launcher_up
insmod $D/wmt_chrdev_wifi.ko || fail "insmod wmt_chrdev_wifi.ko failed; $REBOOT"
insmod $D/wlan_6833_axi.ko || fail "insmod wlan_6833_axi.ko failed; $REBOOT"
wifi_on
# Bluetooth: factory address from nvdata (APCFG/APRDEB/BT_Addr), stored once
insmod $D/btmtk_stp.ko bdaddr=$(cat $D/bt_addr 2>/dev/null) ||
	fail "insmod btmtk_stp.ko failed (Wi-Fi is up); $REBOOT"
