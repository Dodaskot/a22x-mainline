# GPU compute development results, 2026-10-18

Private Mesa builds on development kernel #101 completed Geekbench 6.7.2
LinuxARM Preview on the Mali-G57 MC2. These experiments are outside the
published boot-image baseline and do not establish Vulkan conformance.

| API / build | Score | Public result |
|---|---:|---|
| Vulkan r4, hardware counters | 1236 | [6941631](https://browser.geekbench.com/v6/compute/6941631) |
| Vulkan r4, clean repeat | 1233 | [6941639](https://browser.geekbench.com/v6/compute/6941639) |
| Vulkan r4, descriptor cache-flush fix | 1238 | [6942019](https://browser.geekbench.com/v6/compute/6942019) |
| Vulkan r7, task grouping, hardware counters | 1287 | [6941804](https://browser.geekbench.com/v6/compute/6941804) |
| Vulkan, clean combination plus late byte vectorization | 1345 | [6942096](https://browser.geekbench.com/v6/compute/6942096) |
| Vulkan r4, shader diagnostic | 1238 | [6941712](https://browser.geekbench.com/v6/compute/6941712) |
| OpenCL, system Rusticl on #101 | 351 | [6941685](https://browser.geekbench.com/v6/compute/6941685) |
| OpenCL, private Rusticl r2 | 698 | [6941706](https://browser.geekbench.com/v6/compute/6941706) |
| OpenCL, private signed-offset vectorization candidate | 748 | [6941745](https://browser.geekbench.com/v6/compute/6941745) |

The clean Vulkan repeat and private OpenCL run exited successfully and completed
all eight workloads. The OpenCL run reported no validation failures. Diagnostic
runs include instrumentation and are not clean performance comparisons.

Vulkan r4 combines runtime loop unrolling, load scheduling and a second
load/store vectorization pass after unrolling. Relative to the earlier 1007
development runs, the largest observed gain is in Stereo Matching. An r5
experiment scored [1214](https://browser.geekbench.com/v6/compute/6941662) and
was not promoted.

The private Rusticl build changes how JM splits two-dimensional compute grids
into tasks. In measured workloads the old configuration assigned work to one
of two shader cores; the candidate used both. A representative integer filter
passed exact CPU-reference checks. The complete OpenCL score increased from
351 to 698, and reported execution time decreased from about 34 to 18 minutes.
This is a development comparison, without a controlled multi-run median.

Kernel #101 separately increases the Panfrost job timeout from 500 to 2000 ms.
The historical OpenCL score of 174 on #100 included a Gaussian Blur validation
failure and must not serve as a valid performance baseline. On #101 that
workload passes validation.

Particle Physics remains a major Vulkan optimization target: about 82 FPS in
the r4 runs. An earlier claim of substantial GPU idle time included idle time
after the test ended. During the actual workload the GPU is about 99.9% active
at 955 MHz; activity alone does not establish efficient task grouping or full
occupancy. The separate r7 result below reports 102.8 FPS. OpenCL's
Gaussian and Stereo workloads also remain substantially slower than Vulkan.
Further compiler experiments are pending validation and have no new promoted
benchmark result yet.

Extended independent Vulkan checks found one shared-memory mismatch on the r4
baseline. A twelve-case flag comparison and a subsequent 176-case history soak
passed. Repeating the original, uninstrumented test sequence also passed all
104 cases, so the initial failure remains unexplained. These repetitions do not
establish a fix. The experimental driver is not a fully validated general
compute implementation, even when Geekbench's own workload checks pass.

The next OpenCL signed-affine address experiment passed 42 independent
CPU-reference cases, including signed offsets, carry/wrap, shared-memory
barriers and image boundaries. Some scalar reads combined into 64-bit loads,
but repeated warm timings were about 5–11% slower in the tested larger filter
cases. It was rejected without a complete Geekbench run. A Vulkan unroll-factor
comparison passed 38 Particle-style reference/timing cases; factor eight
showed only a small, size-dependent benefit and was not promoted. Neither
experiment has a new published Geekbench score.

A separate OpenCL candidate changes vectorizer address analysis while retaining
the original lowest-address expression for the merged load. It avoids the
extra runtime 64-bit address arithmetic of the rejected signed-affine rewrite.
It passed 42 CPU-reference cases and 14 native IR checks. Fixed-frequency
microbenchmarks showed mixed, small effects, rather than a consistent gain.

One unmodified full Geekbench run with this candidate scored 748 in 1031 seconds
(about 17 minutes). All eight workloads completed, exit status was zero, stderr
was empty, and the post-run kernel log contained no new GPU fault, timeout or
reset. The loaded library hash was verified before and after the run; the sole
OpenCL device was the Mali-G57 MC2, with no CPU fallback. The UI remained healthy
and benchmark processes were gone afterward. The run used the normal
390–955 MHz governor range, without a frequency lock. Its score is about 7%
above the earlier 698, but this is a single development observation, without a
matched repeated comparison. The candidate remains private and is not promoted
as the system driver. Per-workload scores have not yet been retrieved.

A further opt-in Vulkan vectorization candidate reduced instruction counts in
captured Edge/Horizon shaders without changing their requested byte footprint.
Hardware qualification stopped on an owned byte-buffer fixture with the new
pass disabled. Follow-up case comparisons also failed on the earlier r4 build;
some returned untouched output sentinels. CPU fixture checks passed, but these
hardware observations remain unexplained. No new GPU fault or UI change was
observed. The candidate has no accepted Geekbench score and remains unpromoted;
static instruction reductions do not establish a performance improvement.

A source-provenance audit now ties the accepted Vulkan r4 binary to clean
commit `1096b54505`. Rebuilding with the historical generated version label
reproduces every file byte except the 20-byte GNU build ID; code, read-only
data, relocations and symbols match. The version-label override affects only
build metadata. No final ELF bytes were patched. The later `9ff8` optimization
artifact came from a shared tree that also included rejected r5 speculation
enabled by default. It has been retired from promotion consideration; matching
a few shader outputs had not established whole-driver baseline identity.

The accepted r4 and retired `9ff8` binaries also lack the earlier retained-fence
error-reporting change. Their JM `gpu_queue_check_status` returns success
unconditionally, and the common fence-wait path does not independently inspect
the submitted sync-file error status. A successful API wait therefore does not
prove that the submitted GPU job completed without error. No submitted-status
payload was captured in the earlier uninstrumented failures, so this finding
does not establish cancellation or explain those missing writes. The unchanged
kernel fault log does not resolve that question either. This is a reporting gap and
an investigation limit, not a demonstrated cause or a correctness fix. No
additional benchmark result or driver promotion follows from this audit.

A later passive-observer diagnostic captured the exact submitted fence and its
mapped waits in four owned byte-buffer/echo cases. All four retained submitted
fences completed with positive status `+1`, yet three output comparisons failed.
These instrumented cases therefore did not reproduce cancellation or an active
submitted fence after the wait. Positive fence completion still does not prove
correct shader execution, descriptors or CPU visibility; instrumentation also
changes timing.

On the unchanged accepted r4 binary, a cache-mapping A/B and a repeated ABBA
comparison reproduced byte case 5 failing with the default internal mappings
and passing with process-local `PANVK_DEBUG=no_wb_mmap`. Both echo controls
passed. Input/output allocations and fixture data remained unchanged. The
option changes internal buffer mappings together, so these observations do
not identify a particular allocation or establish a cache-maintenance fault.

With the same diagnostic option and no observer loaded, the unchanged owned
byte-buffer fixture then passed all 512 cases and 32,768 checked output/guard
words with access groups of three or four bytes and robustness settings 0/1.
All processes exited; boot and UI identity remained unchanged, with no new GPU
fault events.
This is a diagnostic workaround for these fixtures, not a general correctness
fix or performance result. No new Geekbench score, system-driver change or
default promotion follows from these tests. The underlying execution and
cache-maintenance path remains under investigation.

The subsequent source audit identified a missing descriptor cache flush in the
accepted r4 port. `descriptor_set_flush` calculated its dirty range, but a stub
comment replaced the cache-maintenance call before the dirty markers were
cleared. A private build from clean commit `1096b54505` replaces that stub with
the existing `panvk_priv_bo_flush` call; other tracked sources are unchanged.
The old build fails the isolated byte case on both sides of the comparison,
while the fixed build passes with normal cached mappings and with kernel cache
maintenance. The latter trace now includes the previously omitted descriptor
storage BO. The unchanged full fixture also passes all 512 cases and 32,768
output/guard words with the fixed driver, normal cached mappings and no observer.

One unmodified full Geekbench Vulkan run with this fix scored 1238 in 722.6
seconds. All eight workloads completed and the upload succeeded. Live library
mapping and start/end hashes identify the private fixed driver. It used the
normal 390–955 MHz governor range, without a frequency lock, shader tracing or
preloaded instrumentation. The phone retained its boot and UI identities;
the complete kernel window contained no new GPU fault, timeout or reset, and
all benchmark processes exited. The only stderr line was PanVK's existing
nonconformance warning. The score is close to the earlier 1233–1236 results;
this single observation does not establish a performance gain. The fix remains
a private development candidate rather than a system-driver promotion or a
claim of general Vulkan conformance.

The user transcribed these subscores from result 6942019; the CLI supplies only
the total. Particle Physics remains at the earlier r4 level of 82.3 FPS.

| Workload | Score | Throughput |
|---|---:|---:|
| Background Blur | 608 | 2.51 images/s |
| Face Detection | 311 | 1.01 images/s |
| Horizon Detection | 1613 | 50.2 Mpixels/s |
| Edge Detection | 1975 | 73.2 Mpixels/s |
| Gaussian Blur | 1839 | 80.1 Mpixels/s |
| Feature Matching | 493 | 19.4 Mpixels/s |
| Stereo Matching | 5406 | 5.14 Gpixels/s |
| Particle Physics | 1871 | 82.3 FPS |

The user also transcribed the subscores for the earlier r7 result 6941804.
Its phone log records total 1287, all eight workloads and exit zero. This run
included a separate hardware-counter sampler, despite the launcher metadata
labeling it uninstrumented. The comparison below uses the reported scores,
not phase durations from that sampler.

| Workload | r4 + cache fix | r7 | Score change | r7 throughput |
|---|---:|---:|---:|---:|
| Background Blur | 608 | 656 | +7.9% | 2.71 images/s |
| Face Detection | 311 | 310 | -0.3% | 1.01 images/s |
| Horizon Detection | 1613 | 1612 | -0.1% | 50.2 Mpixels/s |
| Edge Detection | 1975 | 2013 | +1.9% | 74.7 Mpixels/s |
| Gaussian Blur | 1839 | 1835 | -0.2% | 80.0 Mpixels/s |
| Feature Matching | 493 | 492 | -0.2% | 19.4 Mpixels/s |
| Stereo Matching | 5406 | 5408 | +0.04% | 5.14 Gpixels/s |
| Particle Physics | 1871 | 2336 | +24.9% | 102.8 FPS |
| Total | 1238 | 1287 | +4.0% | |

The largest observed changes are Particle Physics and Background Blur;
most other workloads remain near the r4 level. Particle throughput rises
from 82.3 to 102.8 FPS. This supports investigating task grouping, but these
single historical runs are not a controlled repeated A/B comparison.

The tested 1238 driver changes descriptor cache maintenance only and retains
the r4 dispatch path. The separate r7 source adds task-axis/increment selection
for direct dispatch, but lacks that descriptor cache fix. Its tree also has
other changes relative to clean r4. Neither result therefore represents a
qualified combination of both fixes, and the entire r7 tree is not an isolated
test of task grouping. A later isolated combination has now passed the owned
hardware checks described below; matched full-benchmark performance remains
a separate measurement.

## Remaining Android gaps and isolated diagnosis

The user supplied the complete Android 11 / Geekbench 6.0.2 result
[284380](https://browser.geekbench.com/v6/compute/284380), total 1228. A newer
Android 13 / Geekbench 6.5.0 result on the same SM-A226B / Mali-G57 MC2 is
[5706223](https://browser.geekbench.com/v6/compute/5706223), total 1254.
The latter is the preferred comparison here: Geekbench explicitly states
that [6.7 scores are comparable with 6.5](https://www.geekbench.com/blog/2026/04/geekbench-67/).
These are different phones and single historical results, rather than a
controlled comparison between operating systems on one device.

| Workload | Android 6.0.2 | Android 6.5.0 | Linux r7 | r7 versus Android 6.5.0 |
|---|---:|---:|---:|---:|
| Background Blur | 470 | 473 | 656 | +38.7% |
| Face Detection | 314 | 319 | 310 | -2.8% |
| Horizon Detection | 1777 | 1960 | 1612 | -17.8% |
| Edge Detection | 2763 | 2770 | 2013 | -27.3% |
| Gaussian Blur | 1281 | 1315 | 1835 | +39.5% |
| Feature Matching | 309 | 319 | 492 | +54.2% |
| Stereo Matching | 3702 | 3771 | 5408 | +43.4% |
| Particle Physics | 4877 | 4709 | 2336 | -50.4% |
| Total | 1228 | 1254 | 1287 | +2.6% |

The small Face Detection difference needs repeated measurements before being
treated as a distinct driver problem. Particle, Edge and Horizon are the
substantial remaining gaps.

Two captured Edge/Horizon specializations use 128×4, or 512, threads per
workgroup. The r7 grouping formula still selects increment one for those
groups, whereas the 32-thread Particle kernel selects four. That change
therefore does not address the byte-reading shaders' compiler inefficiency.

A new private diagnostic build starts from clean `1096b54505`, with the
descriptor cache flush, only the r7 task-grouping change, and an opt-in late
memory-vectorization pass. It excludes rejected r5 speculation and broad LICM.
The shared source checkout and system driver were left unchanged.

The compiler only ran this late vectorizer when loop unrolling had changed
the shader. The affected Edge/Horizon shaders have no loops, so adjacent byte
reads remained separate even after address simplification made merging
possible. Enabling the pass independently reduces the three captured shader
instruction counts from 101 to 91, 99 to 89, and 193 to 163. Their static
load/store estimates change from 14 to 10, 14 to 10, and 24 to 12 cycles.
Robustness rules, access ranges and the existing merge predicate remain intact;
there are no added reads through holes between addresses.

On the phone, both settings passed 1,024 owned byte cases and 65,536 checked
output/guard words, including three- and four-byte groups and robustness 0/1.
An independent 262,144-element byte test then showed 16–59% higher throughput
depending on group width and stride. Each configuration used 40 measured
submissions in ABBA order at the same 955 MHz GPU frequency, with exact CPU
references. These are submit-through-fence fixture timings, not Geekbench
subscores. They establish a real optimization opportunity but do not measure
how much of the Android gap it explains.

For Particle, task grouping explains a confirmed part of the earlier r4
limitation. In the owned 4,096-particle test, changing the increment from one
workgroup to the r7-derived default of four reduced the fixed-frequency median
from 43.840 to 18.636 ms. Increasing the increment further, up to 64, changed
that median by less than 1%. Smaller tasks are noisier and do not justify a
global override.

Two separate owned Particle probes also failed to explain the remaining gap.
Hoisting an invariant reciprocal reduced 396 instructions to 386, but the
4,096-particle median changed from 18.675 to 18.665 ms. Retaining each thread's
partial reduction in registers reduced static load/store cost from 53 to 41
cycles, but the corresponding median changed from 18.680 to 18.663 ms. Both
passed exact output and boundary checks. Static instruction or cycle counts
alone therefore remain insufficient evidence for an optimization claim.

Modern Arm Mali Offline Compiler 2026.5, targeting G57 with compiler r51p0,
also compiled the captured SPIR-V. Its Particle output uses 25 registers,
384 bytes of shared memory, no spilling and no FP16 arithmetic. This is a
comparison with a modern offline compiler, not the actual Android driver or
its machine code. The complete cause of the remaining Particle gap is still
unresolved. All bounded phone trials retained boot and UI identity, introduced
no new observed GPU kernel errors, and restored the normal 390–955 MHz range
after the temporary frequency locks.

## Full result with late byte vectorization

The isolated combination completed an unmodified full Geekbench run with
**1345 Vulkan**, result [6942096](https://browser.geekbench.com/v6/compute/6942096).
All eight workloads completed, exit status was zero and upload succeeded.
Driver and executable hashes matched before and after execution, the live
driver mapping was verified, and there were no validation-error messages or
new GPU kernel errors. Boot and UI identity were preserved and all benchmark
processes exited. The run took 583 seconds with the normal 390–955 MHz governor
range and no hardware-counter sampler. The system driver remains unchanged.

The user transcribed the following per-workload results from its public page.
The total is about 4.5% above historical r7; this is a single development
comparison, not a controlled repeated median.

| Workload | r7 | New candidate | Change from r7 | Versus Android 6.5.0 |
|---|---:|---:|---:|---:|
| Background Blur | 656 | 657 | +0.2% | +38.9% |
| Face Detection | 310 | 311 | +0.3% | -2.5% |
| Horizon Detection | 1612 | 1991 | +23.5% | +1.6% |
| Edge Detection | 2013 | 2299 | +14.2% | -17.0% |
| Gaussian Blur | 1835 | 1841 | +0.3% | +40.0% |
| Feature Matching | 492 | 492 | unchanged | +54.2% |
| Stereo Matching | 5408 | 5408 | unchanged | +43.4% |
| Particle Physics | 2336 | 2336 | unchanged | -50.4% |

Horizon now reaches the Android reference level and Edge closes part of its
gap, consistent with the captured-shader changes and owned byte tests.
Particle remains at 102.8 FPS and needs a separate explanation.

Additional bounded Particle checks found almost no large-case improvement
from disabling unrolling or changing its factor to two with task grouping
enabled. Reusing a position vector in an owned fixture gave about 30% higher
throughput, but the actual captured Geekbench shader already reads that vector
once. The extra scalar read was specific to the fixture's guard, so that
improvement does not explain the real Particle workload and is not a proposed
Geekbench optimization. The remaining major targets are Particle and Edge;
Face's 2.5% deficit still needs repeated measurements to distinguish it from
ordinary variation.
