# Flash helper

`flash.py` writes one image to one partition, from any mounted volume. It reads the
volume through `filesystem.py` and targets through `install.py`, so paths are pinned
and a target must match the fixed A22x partition table.

```
flash.py --device targets
flash.py --device check --target LABEL --volume ID --volume-token T --path /image.img
flash.py --device write --target LABEL --volume ID --volume-token T --path /image.img \
    --expected-sha256 H --expected-identity I --confirm
```

Every call prints one JSON line on stdout. Progress goes to stderr as JSON lines with
`event`, `phase`, `partition`, `bytes_done` and `bytes_total`.

Targets are `boot`, `dtbo`, `vbmeta`, `vbmeta_system`, `vbmeta_vendor`, `super`, `prism`
and `optics`. `recovery` is left out on purpose, and this phone has no `vendor_boot`.

Images may be raw, Android sparse, gzip or lz4. `check` decodes the whole file, refuses
an image that belongs on another partition, and reports the SHA-256 of the complete
partition content, with a zero tail after the image. `write` needs that hash and the
file identity from `check`. It saves partitions up to 64 MiB to `A22xPartitionBackups`
on a mounted writable volume when one exists, writes, flushes, and reads the partition
back. A failed comparison is an error; the old content is not restored automatically.

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

