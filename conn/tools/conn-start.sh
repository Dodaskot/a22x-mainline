#!/bin/sh
# Bring up MT6833 connectivity (CONSYS/WMT + Wi-Fi) on the a22x mainline kernel.
# The modules are built out of tree (conn/build.sh) for this exact kernel.
D=/opt/a22x-conn
# already up (the modules cannot be reloaded cleanly)
[ -d /sys/module/wmt_drv ] && exit 0
[ "$(cat $D/kernel-release 2>/dev/null)" = "$(uname -v)" ] || echo "a22x-conn: modules were built for another kernel build" >&2
insmod $D/btif.ko || exit 1
insmod $D/wmt_drv.ko || exit 1
python3 $D/wmtload.py hwinit soc set adie init || exit 1
systemctl start a22x-wmt-launcher.service
sleep 1
insmod $D/wmt_chrdev_wifi.ko || exit 1
insmod $D/wlan_6833_axi.ko || exit 1
echo 1 > /dev/wmtWifi
# Bluetooth: factory address from nvdata (APCFG/APRDEB/BT_Addr), stored once
insmod $D/btmtk_stp.ko bdaddr=$(cat $D/bt_addr 2>/dev/null)
