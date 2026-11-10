#!/bin/sh
# MT6833 CONSYS/Wi-Fi/BT bring-up for imported systems; no Python required.
D=/opt/a22x-conn
[ -d /sys/module/wmt_drv ] && exit 0
insmod $D/btif.ko || exit 1
insmod $D/wmt_drv.ko || exit 1
$D/a22x-wmt load hwinit soc set adie init || exit 1
systemctl start a22x-wmt-launcher.service
sleep 1
insmod $D/wmt_chrdev_wifi.ko || exit 1
insmod $D/wlan_6833_axi.ko || exit 1
echo 1 > /dev/wmtWifi || exit 1
insmod $D/btmtk_stp.ko bdaddr=$(cat $D/bt_addr 2>/dev/null)
exit 0
