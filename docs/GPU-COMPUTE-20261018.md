# GPU compute development results, 2026-10-18

Private Mesa builds on development kernel #101 completed Geekbench 6.7.2
LinuxARM Preview on the Mali-G57 MC2. These experiments are outside the
published boot-image baseline and do not establish Vulkan conformance.

| API / build | Score | Public result |
|---|---:|---|
| Vulkan r4, hardware counters | 1236 | [6941631](https://browser.geekbench.com/v6/compute/6941631) |
| Vulkan r4, clean repeat | 1233 | [6941639](https://browser.geekbench.com/v6/compute/6941639) |
| Vulkan r4, descriptor cache-flush fix | 1238 | [6942019](https://browser.geekbench.com/v6/compute/6942019) |
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
at 955 MHz; the current investigation concerns shader execution. OpenCL's
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
