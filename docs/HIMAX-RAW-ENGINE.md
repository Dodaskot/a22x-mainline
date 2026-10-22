# Open CPU-side raw-engine transaction

`firmware/himax/raw-engine.c` reproduces the eight writes and completion poll
of the stock CPU routine at0x40009048. It is an injected transport component,
not a physical driver or an analog acquisition implementation. No register
sequence has been applied to a phone by these tests.

The address block is0x80030000. Access sizes and ordering are preserved:

| Offset | Access | Value |
|---:|---|---|
| 0xc8 | write32 | supplied parameter |
| 0xd4 | write32 | supplied parameter |
| 0x34 | write8 | 0xd8 |
| 0x38 | write8 | 0xd8 |
| 0xc0 | write8 | 0 |
| 0xb8 | write16 | 0x5be0 |
| 0xbc | write16 | low16 of supplied destination |
| 0x00 | write8 | 1 |
| 0x00 | repeated read8 | finish when0 |

The source/address-like value0x5be0 and low16 destination behavior follow
the actual instructions. Full engine address translation, register roles,
calibration, barriers and timing are not established. The component does
not name this hardware ADC, DMA or DSP based only on its address.

The caller supplies write/read callbacks. This library performs no direct
MMIO. The stock routine polls indefinitely; the open API deliberately adds
a finite polling budget, rejects missing callbacks and reports I/O errors.
Those are explicit API extensions, not claims about stock error recovery.
The caller owns all device permissions, integration and recovery policy.

## Reproducible checks

```sh
python3 tools/firmware/test-himax-raw-engine.py
```

This builds host C and checks every write, destination truncation, completion
read count, callback failures at each write/read, timeout and invalid inputs.

To compare original and compiled-open CPU execution in the vendor NDS32 v3
simulator:

```sh
python3 tools/firmware/test-himax-raw-engine-vendor.py \
  --firmware /path/to/Himax_firmware_fw_jdi.bin \
  --toolchain-prefix /path/to/bin/nds32le-elf-
```

The exact image hash from [the firmware inventory](HIMAX-OPEN-FIRMWARE.md)
is required. The test builds temporary stock/open ELFs, sets the stock EX9
table pointer, and compares64 synthetic parameter/destination/busy-count
cases. It logs stock stores before their instruction and open transport
callback arguments, then independently verifies the final simulated-memory
register values after the actual store instructions executed.

**Completion is explicitly stubbed:** before each CPU read the GDB script
sets the simulated busy byte to1 for a specified number of reads, then0.
These checks establish CPU control flow and register arguments under that
scripted model. They do not model an operational hardware engine, prove its
electrical safety or constitute a touchscreen test. Optional `--output-dir`
saves synthetic traces/results; proprietary code bytes stay temporary.

The next missing integration includes engine setup, mode transitions,
frame-ready interrupt chronology, selectable raw/reference banks and analog
timing. The pure transaction should not be replayed on a physical controller
without resolving those dependencies and a recovery path.
