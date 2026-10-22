# CPU-only diagnostic, not a flashable firmware

These independent sources build a finite NDS32 program. They contain no
original firmware bytes, electrode calibration or peripheral initialization.
Do not install the raw binary under a stock firmware filename.

Startup disables GIE, points IVB at the diagnostic's own sixteen four-byte
vectors and sets a stack. It explicitly initializes every mailbox field it
uses; it has no EX9/IFC dependency. The program sums1..4096, records completion
and CPU PSW/IVB, then stays in a branch loop. Non-reset vectors branch to an
interrupt-disabled trap. Neither loop feeds a hardware watchdog.

The inferred CPU SRAM model is0x40000000..0x4000ffff. Code starts at0x40000100,
a28-byte result mailbox is at0x4000f000 and the stack top at0x40010000. These
are link/simulator addresses. Host upload aliases, ROM entry state, ILMB/cache
setup, watchdog timing and actual exception delivery remain unverified.

Run the build and CPU simulator test without any proprietary firmware:

```sh
python3 tools/firmware/test-himax-diagnostic.py \
  --toolchain /path/to/nds32le-elf-newlib-v3
```

It starts with GIE set, verifies its clear state after execution, checks the
mailbox counter4096 and checksum8390656, reads back IVB through an actual MFSR
instruction, and checks all15 non-reset vector branches. The simulator's GDB
register interface does not expose IVB directly. The test reads the mailbox
value instead; it does not substitute a debugger convenience variable.

CPU simulation is not hardware validation. Before any device execution,
stock-only recovery must work independently of SSH, and rail retention,
SRAM alias/readback, startup and watchdog behavior must be resolved. No
hardware installation command is supplied by this diagnostic.

Instruction basis: the official Andes compiler's `__nds32__gie_dis()` emits
`setgie.d; dsb`. The [Linux Andes port](https://github.com/torvalds/linux/blob/v5.17/arch/nds32/include/asm/nds32.h)
uses that intrinsic for interrupt disable. Device-specific initialization
is intentionally outside this CPU test.
