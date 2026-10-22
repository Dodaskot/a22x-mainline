# Open ordinary-mode threshold seed extractor

`firmware/himax/seed.c` implements one bounded step between prepared matrix
data and clustering. It accepts a packed16x36 signed16 matrix, then applies
either one unsigned16 threshold or a caller-supplied unsigned16 threshold
per cell. All arrays contain numeric host-endian values.

The comparison is strict `signal > threshold`. An accepted cell receives
label254; all others receive253. Accepted seeds are emitted in row-major
order with one-based row/column coordinates. The list saturates at150 while
classification continues for every matrix cell. Only valid seed slots are
written; unused seed slots retain their previous bytes.

No electrode mapping, factory calibration, original firmware bytes or
panel-specific threshold table is embedded. Required pointers and array
lengths are checked before any output modification. The interface requires
nonoverlapping input/output arrays.

`hx_seed_pick` implements the subsequent pure peak search over those seeds
and a supplied18x38 padded signed16 weight matrix. Only label254 entries
are eligible, weight must be positive, and a strict-greater comparison keeps
the first seed when weights tie. It does not mark labels or grow a cluster.
Invalid counts, matrix bounds and one-based coordinates fail explicitly.
No peak returns1 with output unchanged; a peak returns0.

## Scope of comparison

The recovered stock routine is0x40005564. Its ordinary classification loop
corresponds to0x400055d0..0x4000563c, with row iteration through0x4000568e.
The stock code additionally contains high-density exception/early-exit and
edge-processing policies. They are deliberately outside this pure component.

The vendor-simulator test executes the actual stock routine with:

- GP[-2157]=255, making the per-row high-density early-exit threshold higher
  than the saturated150-entry list;
- GP[-652] bit7 clear, disabling padded-edge postprocessing;
- scalar/per-cell mode selected by the actual0x10007408 bit0;
- otherwise synthetic initialized matrices, thresholds and globals.

This establishes equivalence for ordinary full scans, not for every possible
stock mode. The C result uses packed labels; the test extracts the stock's
16x36 interior from its18x38 padded label matrix before comparison.

## Reproduce

```sh
python3 tools/firmware/test-himax-seed.py
python3 tools/firmware/test-himax-seed-vendor.py \
  --firmware /path/to/Himax_firmware_fw_jdi.bin \
  --toolchain-prefix /path/to/bin/nds32le-elf-
```

The first test needs only a host C compiler. The second requires the user's
exact supported image and historical Andes NDS32 v3 GCC/GDB simulator. It
builds separate temporary stock/open ELFs and compares original CPU execution
against compiled open C, including all576 labels, valid seed entries and
count. It covers scalar/per-cell thresholds, ties, signed input boundaries,
unsigned high thresholds,149/150/151 accepted-cell boundaries, dense frames
and deterministic random matrices. Optional `--output-dir` stores synthetic
result hashes and simulator logs. Proprietary objects remain temporary.

The same script also executes stock block0x40005a14..0x40005a4a and the
compiled-open peak function for64 synthetic cases, including an explicit
stable-tie case, duplicate seed coordinates, already-visited labels,
nonpositive weights and empty lists. It compares both versions to a separate
host maximum-search expression. This tests peak selection separately from
threshold extraction, using an independently supplied weight matrix.

## Next clustering stage

After the now-implemented peak search, stock0x40005984 calls recursive
gradient-constrained growth0x400057dc, not ordinary binary
connected-component labeling. That growth has distinct core/outer moments,
bounds/visited checks and neighbor-gradient conditions. This extractor
does not implement flood fill, centroid normalization,
tracking or final packet production.

Host/simulator checks are CPU algorithm evidence, not physical touch-input
validation. The existing firmware remains necessary for the working device.
