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
# The Bluetooth address captured from the phone, when the recovery was built with one. A recovery
# built for any phone carries none (or zeros): then a locally administered address is made from this
# phone's own storage chip, the same at every start.
addr=$(cat $D/bt_addr 2>/dev/null)
case "$addr" in
    ''|00:00:00:00:00:00)
        cid=$(sha256sum /sys/block/mmcblk0/device/cid 2>/dev/null | cut -c1-10)
        [ ${#cid} -eq 10 ] && addr=$(echo "$cid" | sed 's/^\(..\)\(..\)\(..\)\(..\)\(..\)$/02:\1:\2:\3:\4:\5/') ;;
esac
insmod $D/btmtk_stp.ko bdaddr=$addr
exit 0
