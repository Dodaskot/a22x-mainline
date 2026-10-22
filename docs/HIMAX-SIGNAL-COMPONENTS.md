# Open Himax signal-processing components

`firmware/himax/signal.c` supplies three independent GPL-2.0-only functions:

- mapped, wrapping signed16 differences for the A22X 16x36 mutual matrix;
- nonnegative weighted moments for a supplied padded-grid cell;
- positive-weight centroid scaling, clipping and per-axis inversion.

These are bounded functions. They do not acquire samples, initialize ADC/DMA,
adapt baselines, correct borders, cluster cells, assign contacts or drive a
controller. The existing stock touch firmware remains necessary.

## Recovered behavior

The original image identified in [the format analysis](HIMAX-OPEN-FIRMWARE.md)
maps cells through an electrode index table, subtracts unsigned16 raw and
baseline values, wraps to16 bits, then interprets that result as signed.
The open implementation explicitly sign-extends the wrapped integer and
validates every mapping index before modifying the output. Its output is a
packed16x36 matrix; the original padded scratch layout is an independent
later integration detail. Arrays must be nonoverlapping and contain numeric
host-endian elements. No factory electrode mapping is embedded.

The weighted-moment function clips negative samples to zero, accumulates
column*weight, row*weight and weight, and uses defined unsigned32 wrap.
The caller chooses which cluster/core/outer record to update and controls
all traversal, peak selection and visited-state logic.

For positive weight, the pure scaling block computes integer divisions:

```
col_axis = clamp(col_weight*2408 /36 /weight -33, 0, 2407)
row_axis = clamp((row_weight*1080 >>4) /weight -33, 0, 1079)
```

Products use64 bits and all divisions truncate. Quotients up to33 clip to0
without unsigned underflow. For larger quotients the stock program stores
the low32 bits of `(quotient-33)` before clipping to the axis maximum; the
open implementation preserves this even for nonphysical synthetic moments.
Optional inversions subtract from2407 and1079.
These are internal column/row axes; the separate report serializer may
swap axes. Weight0 is explicitly rejected; no behavior is claimed for
division by zero in the original program.

## Validation and source basis

```sh
python3 tools/firmware/test-himax-signal.py
```

The standalone test compiles the C implementation as a temporary host
library. It checks a nonidentity electrode mapping, signed16 wrap boundaries,
mapping/output/input bounds with no partial output on rejection, negative
moment weights, unsigned32 wrap, zero-denominator rejection and2000 generated
centroid cases against independently expressed integer formulas.

An optional `--vendor-results synthetic-results.json` also compares recorded
synthetic cases from a separate vendor-simulator run. The development audit
executed the stock centroid instruction block for64 synthetic cases covering
four inversion combinations and64-bit products, then compared this C code
against those results. No firmware payload or disassembly is required by
the ordinary standalone test.

Private static evidence locates mapped subtraction at0x40004164..417e,
moment updates at0x4000584c..5862, and scaling at0x4000248c..2514. The
[Andes simulator source](https://github.com/andestech/gdb/blob/ast-v3_2_5-release-v3/sim/nds32/interp.c)
defines IFCALL/JAL control flow used by the shared division calls.

To execute both the original binary block and compiled open NDS32 C in the
vendor GDB simulator, provide the exact supported image and historical v3
toolchain:

```sh
python3 tools/firmware/test-himax-signal-vendor.py \
  --firmware /path/to/Himax_firmware_fw_jdi.bin \
  --toolchain-prefix /path/to/bin/nds32le-elf-
```

This also exercises quotient32-bit wrap boundaries that the physical moment
domain would not normally reach. The open ELF uses its own `_SDA_BASE_`
global pointer for libgcc; it must not inherit the stock program's GP.
The64-bit division helper is linked from libgcc. Generated firmware objects
stay in a temporary directory; optional `--output-dir` saves only synthetic
results and simulator logs.

Host and simulator checks establish these pure computations; they are not
a flash, touch-input or full open-firmware validation.
