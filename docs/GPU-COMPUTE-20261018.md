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
