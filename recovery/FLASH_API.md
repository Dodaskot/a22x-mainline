# Flash helper

`flash.py` writes one image to one partition, from any mounted volume. It reads the
volume through `filesystem.py` and targets through `install.py`, so paths are pinned
and a target must match the fixed A22x partition table.

```
flash.py --device targets
flash.py --device check --target LABEL --volume ID --volume-token T --path /image.img
flash.py --device write --target LABEL --volume ID --volume-token T --path /image.img \
    --expected-sha256 H --expected-identity I --confirm
flash.py --device resume
```

Every call prints one JSON line on stdout. Progress goes to stderr as JSON lines with
`event`, `phase`, `partition`, `bytes_done` and `bytes_total`.

Targets are `boot`, `dtbo`, `vbmeta`, `vbmeta_system`, `vbmeta_vendor`, `super`, `prism`,
`optics` and `recovery`, and `super/NAME` for one partition inside super. This phone has no
`vendor_boot`. Bootloader, preloader, LK, modem, EFS and other firmware partitions are never
targets. `targets` lists the ones present in the fixed A22x partition table.

`recovery` replaces this recovery itself. It runs from RAM, so its own partition can be written
while it runs; the new one starts at the next restart into recovery. `check` takes only an
Android boot image that `install.boot_header(..., recovery=True)` accepts as an A22x recovery
image and that `bootimage.inspect('recovery')` judges complete and made for this phone; a BOOT
image is `WRONG_IMAGE_TYPE`. Its warning says what to do when it does not start: flash a working
recovery from a computer in Download Mode. The old recovery (40 MiB) is saved first like any
partition up to 64 MiB, so a failed write puts it back (below); a power loss in the middle of
writing recovery cannot be undone by the recovery itself, because the phone can then no longer
start it.

A BOOT or recovery image must name this phone (2026-11-12 audit): the header's board
`SRPTL28A011`, a device tree for `samsung,a22x` or an `SM-A226B` model, or the ramdisk's
`ro.product.*device=a22x` / `ro.product.*model=SM-A226B...`. A device tree for the MT6833 chip
alone is `BOOT_PHONE_UNKNOWN`; a board compatible of another maker or another Samsung phone is
`BOOT_OTHER_PHONE`.

Images may be raw, Android sparse, gzip or lz4. `check` decodes the whole file, refuses
an image that belongs on another partition, and reports the SHA-256 of the complete
partition content, with a zero tail after the image. `write` needs that hash and the
file identity from `check`. It saves partitions up to 64 MiB to `A22xPartitionBackups`
on a mounted writable volume when one exists, writes, flushes, and reads the partition
back.

## When a write fails or is cut short (2026-11-12 audit)

The saved copy carries its SHA-256, and beside it `NAME.pending.json` names the partition
(label, PARTUUID, size), the copy and the hash of the image being written, until the write is
over. When the write itself fails (an I/O error, the file changing, a read-back that differs),
the copy is put back at once, read back, and the error carries `rollback_restored`.
When the write is cut short by a power loss or a crash, `resume` runs at the next start of the
recovery, before the interface: for each journal it reads the partition; the new image whole is
`completed`, the old content `unchanged`, anything else gets the copy back (`restored`) when its
hash is still the saved one, else the journal stays (`kept`) and the home page asks to flash the
partition again. `init` leaves the answer in `/run/a22x-recovery/flash-resume.json`. Cache keeps
a copy with an open journal when it prunes older copies. Partitions above 64 MiB (super,
userdata) have no copy and no automatic way back.

## Restore from a partition image

`check --target auto` picks the partition from the decoded image: an Android boot
image goes to `boot`, an ext4 or F2FS filesystem to `userdata`; anything else is
`UNKNOWN_IMAGE_TARGET`. `userdata` (partition 54, 53 393 489 920 bytes in the fixed
A22x table) is reachable only this way and is not listed by `targets`. `write`
takes the label that `check` reported.

Images may also be zstd or xz. Partitions above 8 GiB and zstd/xz files are decoded
as one stream (`DecodedReader`) that must end exactly at the end of the file.
A checksum stored beside the image is compared with the decoded bytes when it
covers exactly them: `NAME.transport.json` (`decoded_bytes`, `decoded_sha256`),
an entry in `manifest.json`, or `NAME.sha256` for an uncompressed file. A mismatch
is `IMAGE_DAMAGED` and nothing is written. `check` reports `target.offline`; a
mounted target is refused by `write` as before. Userdata is too large for the
rollback copy, so its old content is not kept.

