# HX83112F: offline NDS32 startup and host protocol analysis

2026-10-22. This is a static analysis of the exact image identified in
[the firmware inventory](HIMAX-OPEN-FIRMWARE.md), not a replacement firmware
or a hardware execution test. No controller writes, resets or flashes were used.

## Architecture evidence

The code is **Andes NDS32**, with mixed 16/32-bit instructions, EX9 instruction
table references, and IFC instructions. This identification rests on several
independent, consistent structures, rather than a plausible-looking byte prefix:

- Sixteen four-byte initial branches decode into reset/exception entry points.
- The reset entry initializes GP, FP, SP and the instruction-table register;
  its callees program IVB and ILMB and use NDS32 barriers/system registers.
- Interrupt wrappers save and restore PSW/IPC/IPSW and end with `iret`.
- Little-endian handler pointers match absolute addresses constructed by the
  wrappers; the handlers lead to decodable local routines.
- Expanding EX9 references reveals register accesses and command values that
  match the independent GPL Linux host driver.

The exact CPU core model and full supported ISA revision remain unknown.
The image uses the historical NDS32 instruction set; a modern Andes RISC-V
compiler is not a substitute merely because its package name contains Andes.

Primary encoding references are the
[Andes-contributed GNU opcode definitions](https://github.com/andestech/binutils/blob/nds32-binutils-2.30-branch-open/include/opcode/nds32.h)
and [assembler tables](https://github.com/andestech/binutils/blob/nds32-binutils-2.30-branch-open/opcodes/nds32-asm.c).
The latter identifies user register group 0/index 28 as ITB. The decoder used
here prints its register index numerically.

## Address spaces and startup

Code partition bytes are file `[0x400, 0x10400)`. Its SHA256 is
`4df0f4364a07427615ba1cc3a11e809b7e10b4678df6d3df87ffd8e952cdb339`.

| Item | Address/value | Evidence |
|---|---|---|
| Host upload target | `0x20000000` | Actual partition 0, not generic driver fallback |
| CPU execution base | `0x40000000` | Inferred from vector pointers, absolute branches and IVB initialization |
| Reset entry | `0x4000b5c8` | First vector: signed relative branch |
| Initial GP / FP | `0x4000c8e8` | Startup constant construction and GP-relative FP assignment |
| Initial SP | `0x40010000` | GP plus `0x3718` |
| ITB | `0x4000b918` | GP minus `0xfd0`, written to user register 28 |
| IVB | `0x40000000` | GP minus `0xc8e8`, written to IVB |
| ILMB write value | `0x40000001` | Same base OR 1, written to ILMB |
| Initialization/application entry | `0x4000b162` | Final startup call |
| Repeating application loop | `0x4000b494` | Back edge from `0x4000b5c2` |

The relationship between host upload and CPU execution is strongly supported
by the image, but the hardware remapping mechanism is not documented here.
Do not substitute one address for the other in the upload protocol.
Instructions and EX9 table entries are decoded in big-endian byte order;
handler pointers and the recovered multi-byte switch offsets are little-endian.

The pointer block at CPU `0x40000040` contains nine entries. The dispatch
wrapper indexes its subtable at `0x4000004c`; target stubs call routines at
`0x400081d4`, `0x400087b4`, `0x40008294` and `0x40008c84`, while other
entries return or loop. Peripheral assignments for these interrupts have not
all been established.

## EX9 and embedded data

EX9 replaces a full instruction with a short reference into the ITB table.
Ignoring it hides many MMIO base loads, barriers and state updates.
The selected code ranges contain **2209 EX9 references**, using **343 distinct
indices** up to **382**. Therefore 383 four-byte slots are decoded as the
observed minimum table extent. This is not a claim about its physical maximum.

Examples of recovered table semantics include a base load for `0x90000000`,
a base load for shared RAM `0x10007000`, and a data-synchronization barrier.

Four inline switch tables are separated from instructions using each dispatcher's
bounds, base calculation, load width and indirect jump:

| CPU address | Entries | Entry width | Interpretation |
|---|---:|---:|---|
| `0x40007794` | 18 | 1 byte | Unsigned offset from table base |
| `0x40007ad8` | 6 | 1 byte | Unsigned offset from table base |
| `0x400087f0` | 7 | 2 bytes | Little-endian unsigned offset from table base |
| `0x40009320` | 46 | 2 bytes | Little-endian unsigned offset from table base |

Return-alignment padding and the pointer block are also excluded. Without
these exclusions, a linear decoder produces fake instructions and even a
spurious out-of-image jump from switch-table data.

With these exclusions the listing has **16872 instruction records**, no unknown
instructions, and **925 direct J/JAL edges**, all targeting selected instruction
boundaries. All 77 switch-table entries also point to selected instruction
boundaries. These checks establish internal consistency, not that every
instruction is reachable or that every indirect call has been recovered.

## Firmware behavior matched to the Linux driver

The stop-command path at `0x40008258` reads the 32-bit mailbox at
`0x9000005c` and compares it with `0xa5`. On that branch it invokes a helper,
clears INT_MASK, updates PSW, executes barriers, then writes **`0x87`** back
to the same mailbox. It subsequently loops through a register-maintenance
helper and a delay routine. This agrees with the host driver's stop handshake.
It does not itself prove that all peripherals are stopped: the host subsequently
requests safe mode and checks state separately.

Startup at `0x4000b44e` reads byte `0x90000048`, compares it with `0xec`,
and can write `0xec` there. This matches the low byte of the host's
reload-to-active transaction. The full host-observed `0x01ec` transition has
**not** yet been assigned to a specific firmware/hardware operation.

Routine `0x400078ca` reads halfword `0x90008010` and writes `0x35ca` if the
value differs. It is repeatedly called in the main and stop loops. Nearby
routines access `0x9000800c` with `0xac53` or zero. These are useful watchdog
candidates, but register naming alone is insufficient to prove their complete
hardware semantics or timing.

For the host side, the GPL driver establishes SPI framing (`f2` writes,
`f3 command 00` reads), AHB bridge commands, CRC registers, firmware loading,
and touch-report parsing. For ten supported contacts the normal packet is
56 bytes: ten BE16 X/Y coordinate pairs, ten width bytes, a point count at
byte 52, state bytes 53/54, and a byte-sum checksum. The firmware's scan,
filtering and contact-tracking algorithms have not yet been recovered.

## Reproduce privately

Install radare2 with its NDS32 disassembler, then run:

```sh
rasm2 -L nds32
python3 tools/firmware/test-analyze-himax-nds32.py
python3 tools/firmware/analyze-himax-nds32.py \
  /path/to/Himax_firmware_fw_jdi.bin --output-dir /private/himax-analysis
```

The analyzer rejects any other firmware SHA256 because its region map is
specific to this image. It never contacts the phone. Outputs include full
private disassembly and must not be confused with independently written open
firmware source or copied into a public release.

The decoder tested is rasm2 6.2.0, commit
`e1fc278734ad62f933fc6f91edb29e4ba732f402`. Its NDS32 printer emits some
backward branch targets above 32 bits. The analyzer retains the original
text, applies 32-bit address wrapping in the annotated view, and independently
checks the signed relative J/JAL arithmetic. Synthetic tests cover forward,
backward and wrapping jumps, both EX9 encoding forms, table bounds, truncated
instructions and rejection of an unsupported image.

## Next bounded steps

1. Follow the report-production path and trace scan-complete interrupt data
   into the host event packet; distinguish raw electrodes from tracked contacts.
2. Describe the ADC/TCON register sequences and clocks, using both firmware
   control flow and the GPL driver's independent observations.
3. Establish a reproducible compatible assembler and a minimal diagnostic
   program with a recovery plan before any live replacement experiment.
4. Implement and test scanning, filtering, tracking and power transitions
   incrementally. Existing firmware remains necessary until these work.
