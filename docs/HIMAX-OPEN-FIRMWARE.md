# Towards open HX83112F controller firmware

The Linux host driver is already GPL-2.0 source, included by
[patch 0010](../kernel/patches/0010-Input-add-Himax-HX83112F-zero-flash-SPI-touchscreen-.patch).
The closed component is `Himax_firmware_fw_jdi.bin`, the program/data uploaded
to the zero-flash touch controller. Rebuilding the host driver or embedding
the binary into C does not replace that firmware with open source.

This document records the first reproducible offline analysis, dated 2026-10-22.
It does not claim a working replacement. No controller experiment was performed.

## Offline inspection

Run the read-only inspector against a firmware image you already possess:

```sh
python3 tools/firmware/inspect-himax.py /path/to/Himax_firmware_fw_jdi.bin
python3 tools/firmware/test-inspect-himax.py
```

Optional `--output inventory.json` writes structural metadata and hashes.
The tool never contacts a device or extracts firmware payloads. Its synthetic
tests need no proprietary image. Unsupported layouts fail explicitly; this is
an inspector for the old-format descriptor layout used by this A22X image,
not a general firmware authenticity checker.

The examined file is 131072 bytes, SHA256
`fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a`.
It matches the touch firmware retained in the published v143 boot ramdisk.

The checksummed descriptor table relocates the partition table to **0x10400**.
The chipset default **0x10000** is incorrect for this specific image.
Its ten partitions are:

| Index | File offset | Bytes | Controller target |
|---|---:|---:|---:|
| 0 | 0x400 | 65536 | 0x20000000 |
| 1 | 0x12400 | 120 | 0x10007000 |
| 2 | 0x124fe | 528 | 0x10007084 |
| 3 | 0x12e00 | 128 | 0x10007300 |
| 4 | 0x12e80 | 48 | 0x10007380 |
| 5 | 0x13748 | 840 | 0x10007b00 |
| 6 | 0x13a90 | 1260 | 0x10006a00 |
| 7 | 0x14400 | 1536 | 0x10007500 |
| 8 | 0x12de0 | 32 | 0x100073f0 |
| 9 | 0x12dd0 | 16 | 0x100072f0 |

The driver treats partition 0 as firmware SRAM and the remaining partitions as
configuration data assembled in DSRAM. These are host-driver roles, not a
recovered specification of the controller's algorithms or calibration fields.

All ten partition payloads give zero with the driver's raw reflected CRC32C
(polynomial `0x82f63b78`, initial `0xffffffff`, no final XOR). The function
processes whole four-byte words, matching the driver. These offline checks
demonstrate consistency, not hardware execution or cryptographic authenticity.
Whether controller ROM enforces further authentication remains unknown.

## Source basis

The relevant functions are in the source supplied by patch 0010:

- `himax_ic_HX83112.c`, `hx83112_chip_init`: default version/table addresses.
- `himax_ic_core.h`, active `bin_desc_map_table`: descriptor IDs.
- `himax_ic_incell_core.c`, `hx_bin_desc_data_get` and `hx_mcu_bin_desc_get`:
  descriptor checksums and relocated-address selection.
- `himax_mcu_Calculate_CRC_with_AP`: the offline CRC algorithm.
- `himax_zf_part_info`: partition parsing, SRAM upload and configuration assembly.

[Himax's published HX83112 host driver](https://github.com/HimaxSoftware/HX83112_Android_Driver)
provides additional primary source material. The exact ported source in patch
0010 is the basis of the byte-level conclusions above.

## Remaining work

The instruction set is now identified as **Andes NDS32**, based on matching
startup/vector structures, EX9 expansion and the host stop-command handshake.
See [the reproducible ISA analysis](HIMAX-NDS32-ANALYSIS.md) for evidence,
address mappings, validation and remaining uncertainties. The exact core model
and full ISA revision are still unknown.

1. Establish a compatible assembler/compiler for the observed NDS32 extensions.
2. Extend the recovered startup/interrupt map into scanning and report production,
   marking uncertain register semantics explicitly.
3. Derive a host/controller protocol specification and separate panel-specific
   configuration from algorithms.
4. Establish a minimal SRAM diagnostic program and recovery procedure before
   hardware experiments; avoid interfering with ongoing modem testing.
5. Implement scanning, filtering, multitouch reporting and suspend/resume in
   independently testable stages.

Until those stages succeed, existing boot images still require the stock
Himax controller firmware. No firmware payload or device calibration is
included with this analysis tooling.
