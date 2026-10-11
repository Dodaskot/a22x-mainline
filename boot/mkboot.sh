#!/bin/bash
# Pack a mainline boot.img for SM-A226B (a22x) with the stock LK layout:
# header v2, page 2048, stock load addresses, DTB in the header-v2 dtb section,
# then an AVB hash footer (algorithm NONE) padded to the 32 MiB boot partition.
#
# usage: OUT=<kernel build dir> ./mkboot.sh [tag]
#   KERNEL, DTB, RAMDISK, IMAGES and EXTRA_CMDLINE override the defaults.
# The default DTB is the one used by the accepted v135 image.
set -euo pipefail
cd "$(dirname "$0")"

OUT=${OUT:-out}
KERNEL=${KERNEL:-$OUT/arch/arm64/boot/Image.gz}
DTB=${DTB:-$OUT/arch/arm64/boot/dts/mediatek/mt6833-samsung-a22x-nfc-experimental.dtb}
RAMDISK=${RAMDISK:-initramfs.cpio.gz}
# The native 1080x2408 swapchain needs 96 MiB CMA; Phosh does not start with
# the 32 MiB default.
EXTRA_CMDLINE=${EXTRA_CMDLINE-cma=96M}
BOOT_PART_SIZE=33554432
TAG=${1:-test}
IMAGES=${IMAGES:-images}
IMG=$IMAGES/boot-mainline-$TAG.img

mkdir -p "$IMAGES"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# LK parses the header-v2 dtb section as an Android DT table (like dtbo.img),
# exactly as in the stock boot.img: one entry, 0x20-byte header and entry.
python3 - "$DTB" "$WORK/dtb.table" <<'PY'
import struct, sys
dtb = open(sys.argv[1], 'rb').read()
hdr, ent = 0x20, 0x20
off = hdr + ent
total = off + len(dtb)
table = struct.pack('>8I', 0xd7b7ab1e, total, hdr, ent, 1, hdr, 2048, 0)
table += struct.pack('>8I', len(dtb), off, 0, 0, 0, 0, 0, 0)
open(sys.argv[2], 'wb').write(table + dtb)
PY

# LK hard-asserts that the kernel is loaded at 0x40080000 (s_boot.c:701), but
# mainline arm64 has text_offset 0 and must start on a 2 MiB boundary. Put a
# 0x180000-byte shim in front: a valid arm64 Image header whose first
# instruction is "b +0x180000", so the real Image runs from 0x40200000 with
# x0..x3 untouched.
python3 - "$KERNEL" "$WORK/Image.gz" <<'PY'
import gzip, struct, sys
img = gzip.open(sys.argv[1]).read()
assert img[0x38:0x3c] == b'ARMd'
pad = 0x180000
text_offset, image_size, flags = struct.unpack('<QQQ', img[8:32])
assert text_offset == 0
hdr = struct.pack('<IIQQQQQQII', 0x14000000 | (pad >> 2), 0, 0,
                  pad + image_size, flags, 0, 0, 0, 0x644d5241, 0)
shim = hdr + bytes(pad - len(hdr))
with gzip.open(sys.argv[2], 'wb', compresslevel=9) as f:
    f.write(shim + img)
PY

# Fallback for LK's decap_dtb_from_kernel path: same DTB appended to Image.gz.
cat "$WORK/Image.gz" "$DTB" > "$WORK/Image.gz-dtb"
# LK appends its own bootargs (console=ttyS0,921600n1 etc.) after these.
# Opt-in cleanup test; default images retain the bring-up clock policy.
CLK_RETENTION_ARG=" clk_ignore_unused"
case ${GATE_UNUSED_CLOCKS:-0} in
    0) ;;
    1) CLK_RETENTION_ARG="" ;;
    *) echo 'GATE_UNUSED_CLOCKS must be 0 or 1' >&2; exit 2 ;;
esac
CMDLINE="bootopt=64S3,32N2,64N2 earlycon=mt6833uart,0x11002000 console=ttyS0,921600n8 console=tty0 loglevel=8 ignore_loglevel${CLK_RETENTION_ARG} pd_ignore_unused printk.devkmsg=on mem_sleep_default=s2idle${EXTRA_CMDLINE:+ $EXTRA_CMDLINE}"

mkbootimg \
	--header_version 2 \
	--os_version 12.0.0 --os_patch_level 2025-01 \
	--kernel "$WORK/Image.gz-dtb" --ramdisk "$RAMDISK" --dtb "$WORK/dtb.table" \
	--pagesize 2048 \
	--base 0x40000000 \
	--kernel_offset 0x00080000 \
	--ramdisk_offset 0x11100000 \
	--tags_offset 0x07c80000 \
	--dtb_offset 0x07c80000 \
	--board SRPTL28A011 \
	--cmdline "$CMDLINE" \
	-o "$IMG.raw"

raw_size=$(stat -c%s "$IMG.raw")
if [ "$raw_size" -gt $((BOOT_PART_SIZE - 65536)) ]; then
	echo "image too large: $raw_size" >&2
	exit 1
fi

cp "$IMG.raw" "$IMG"
avbtool add_hash_footer --image "$IMG" --partition_name boot \
	--partition_size $BOOT_PART_SIZE --algorithm NONE \
	--salt "$(sha256sum "$IMG.raw" | cut -d' ' -f1)"
rm "$IMG.raw"

unpack_bootimg --boot_img "$IMG" --out "$(mktemp -d)" | grep -E "header version|kernel load|ramdisk load|tags load|dtb (size|address)|page size|command line"
ls -l "$IMG"
sha256sum "$IMG" | tee "$IMG.sha256"
