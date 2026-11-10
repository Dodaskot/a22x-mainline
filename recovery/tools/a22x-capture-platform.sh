#!/bin/sh
# Run in recovery with userdata mounted: saves this phone's kernel modules and
# firmware for a22x-image-import. The result is device-specific and proprietary:
# keep it private and never publish it.
set -eu
out=${1:-/mnt/backup/a22x-platform.tar}
root=/mnt/volumes/userdata
[ -f "$root/opt/a22x-conn/wmt_drv.ko" ] || { echo "Mount userdata first" >&2; exit 1; }
list=$(python3 -B -c '
import json
p=json.load(open("/etc/a22x-recovery/distro-platform.json"))
print(" ".join(e["path"] for e in p["entries"] if e["kind"]!="directory"))')
cd "$root"
tar -cf "$out" --numeric-owner $list opt/a22x-conn/bt_addr opt/a22x-conn/kernel-release \
    opt/a22x-conn/*.ko etc/systemd/system/a22x-conn.service \
    etc/systemd/system/multi-user.target.wants/a22x-conn.service \
    etc/modules-load.d etc/NetworkManager/conf.d/90-a22x-wifi.conf usr/lib/modules/7.2.0-a22x+
echo "$out"
