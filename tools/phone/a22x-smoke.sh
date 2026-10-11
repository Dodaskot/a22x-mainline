#!/bin/sh
# a22x-smoke.sh - post-flash smoke test for SM-A226B (mainline 7.2, Arch).
# Run as root:  sudo a22x-smoke.sh
# Prints one OK/FAIL line per subsystem + up to 10 dmesg error lines
# and saves the report to /var/log/a22x-smoke/smoke-<release>-b<build>-<time>.txt
LOGDIR=/var/log/a22x-smoke
mkdir -p "$LOGDIR"
build=$(uname -v | sed -n 's/^#\([0-9]*\).*/\1/p')
out="$LOGDIR/smoke-$(uname -r)-b${build:-x}-$(date +%Y%m%d-%H%M%S).txt"
n_fail=0

line() {	# line OK|FAIL subsystem detail
	[ "$1" = FAIL ] && n_fail=$((n_fail + 1))
	printf '%-4s %-10s %s\n' "$1" "$2" "$3"
}

report() {
echo "a22x smoke $(uname -r) $(uname -v | cut -c1-40) up=$(cut -d' ' -f1 /proc/uptime)s"

# GPU: panfrost bound, devfreq alive, no MMU/job faults this boot
gpu=$(for c in /sys/class/drm/card*; do basename "$(readlink -f "$c/device/driver" 2>/dev/null)"; done | grep -c '^panfrost$')
gfreq=$(cat /sys/class/devfreq/13000000.gpu/cur_freq 2>/dev/null)
gfault=$(dmesg | grep -c -E 'panfrost.*(fault|timeout|job)')
if [ "$gpu" -ge 1 ] && [ -n "$gfreq" ] && [ "$gfault" -eq 0 ]; then
	line OK GPU "panfrost freq=$((gfreq / 1000000))MHz faults=0"
else
	line FAIL GPU "panfrost=$gpu freq=${gfreq:-none} faults=$gfault"
fi

# Display + backlight
con=$(cat /sys/class/drm/card*-DSI-1/status 2>/dev/null | head -1)
bl=$(ls /sys/class/backlight 2>/dev/null | head -1)
if [ -n "$bl" ]; then
	bv="$(cat /sys/class/backlight/$bl/brightness)/$(cat /sys/class/backlight/$bl/max_brightness)"
fi
if [ "$con" = connected ] && [ -n "$bl" ]; then
	line OK DISPLAY "DSI-1 connected, backlight $bl $bv"
else
	line FAIL DISPLAY "DSI-1=${con:-missing} backlight=${bl:-missing}"
fi

# Sound: ALSA card + UCM profile present
if grep -q mt6833_mt6359 /proc/asound/cards && [ -e /usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833-mt6359.conf ]; then
	line OK SOUND "card mt6833_mt6359 + UCM"
else
	line FAIL SOUND "card=$(grep -c mt6833_mt6359 /proc/asound/cards) ucm=$(ls /usr/share/alsa/ucm2/conf.d/mt6833_mt6359 2>/dev/null | wc -l)"
fi

# Wi-Fi
if [ -d /sys/class/net/wlan0 ]; then
	st=$(cat /sys/class/net/wlan0/operstate)
	ip4=$(ip -4 -o addr show wlan0 2>/dev/null | awk '{print $4}' | head -1)
	if [ "$st" = up ] && [ -n "$ip4" ]; then line OK WIFI "wlan0 up $ip4"; else line FAIL WIFI "wlan0 $st ${ip4:-no-ip}"; fi
else
	line FAIL WIFI "no wlan0 (a22x-conn: $(systemctl is-active a22x-conn))"
fi

# Bluetooth
if [ -d /sys/class/bluetooth/hci0 ]; then
	line OK BT "hci0 $(cat /opt/a22x-conn/bt_addr 2>/dev/null)"
else
	line FAIL BT "no hci0"
fi

# Sensors (IIO): accel, gyro, magnetometer, PMIC ADC
names=$(cat /sys/bus/iio/devices/iio:device*/name 2>/dev/null | tr '\n' ' ')
miss=""
for s in icm40607-accel icm40607-gyro mmc5603 mt6360-adc; do
	echo "$names" | grep -q "$s" || miss="$miss $s"
done
if [ -z "$miss" ]; then line OK SENSORS "$names"; else line FAIL SENSORS "missing:$miss"; fi

# Thermal: 4 LVTS zones, all below 85 C
tz=""; hot=0; nz=0
for z in /sys/class/thermal/thermal_zone*; do
	[ -e "$z/temp" ] || continue
	t=$(($(cat "$z/temp") / 1000)); nz=$((nz + 1))
	tz="$tz $(cat "$z/type" | sed 's/-thermal//')=${t}C"
	[ "$t" -ge 85 ] && hot=1
done
if [ "$nz" -ge 4 ] && [ "$hot" -eq 0 ]; then line OK THERMAL "$tz"; else line FAIL THERMAL "zones=$nz$tz"; fi

# Memory: MemAvailable, Shmem, swap/zram
ma=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
sh=$(awk '/^Shmem:/{print int($2/1024)}' /proc/meminfo)
st=$(awk '/^SwapTotal:/{print int($2/1024)}' /proc/meminfo)
sf=$(awk '/^SwapFree:/{print int($2/1024)}' /proc/meminfo)
zr=$(swapon --show=NAME --noheadings 2>/dev/null | grep -c zram)
oom=$(dmesg | grep -c 'Out of memory')
detail="avail=${ma}M shmem=${sh}M swap=$((st - sf))/${st}M zram=$zr oom=$oom"
if [ "$ma" -ge 300 ] && [ "$oom" -eq 0 ] && [ "$zr" -ge 1 ]; then line OK MEMORY "$detail"; else line FAIL MEMORY "$detail"; fi

# Failed systemd units
fu=$(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
if [ -z "$fu" ]; then line OK UNITS "no failed units"; else line FAIL UNITS "$fu"; fi

# Kernel oops / warnings this boot
ow=$(dmesg | grep -c -E 'Oops|BUG:|WARNING:|Call trace')
if [ "$ow" -eq 0 ]; then line OK KERNEL "no oops/warnings"; else line FAIL KERNEL "oops/warn/trace lines=$ow"; fi

echo "summary: $n_fail FAIL"
echo "--- dmesg err (last 10) ---"
dmesg --level=emerg,alert,crit,err 2>/dev/null | grep -vE 'wlan\]|connlog' | tail -10 | cut -c1-160
}

report | tee "$out"
echo "saved: $out"
