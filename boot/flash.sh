#!/bin/bash
# Flash a mainline boot image to the BOOT partition only.
# usage: ./flash.sh images/boot-mainline-<tag>.img
#
# The phone must already be in Download mode: hold both volume keys and plug
# in USB, or run "reboot-download" on the initramfs/UART shell, or
# "sudo reboot-download" on the running system after copying the helper.
# Never flash any other partition with these images.
set -euo pipefail
IMG=${1:?usage: flash.sh <boot image>}
[ -f "$IMG.sha256" ] && sha256sum -c "$IMG.sha256"
echo "waiting for a device in Download mode..."
timeout 90 bash -c 'until heimdall detect >/dev/null 2>&1; do sleep 2; done'
if heimdall flash --boot "$IMG"; then
	echo "flashed $IMG to boot; verify the running kernel after boot (uname -v)"
else
	echo "heimdall failed (exit $?); the BOOT partition may be unchanged" >&2
	exit 1
fi
