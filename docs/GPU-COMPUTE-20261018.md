# GPU compute development results, 2026-10-18

Private Mesa builds on development kernel #101 completed Geekbench 6.7.2
LinuxARM Preview on the Mali-G57 MC2. These experiments are outside the
published boot-image baseline and do not establish Vulkan conformance.

| API / build | Score | Public result |
|---|---:|---|
| Vulkan r4, hardware counters | 1236 | [6941631](https://browser.geekbench.com/v6/compute/6941631) |
| Vulkan r4, clean repeat | 1233 | [6941639](https://browser.geekbench.com/v6/compute/6941639) |
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
