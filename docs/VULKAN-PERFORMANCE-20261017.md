# Vulkan performance investigation, 2026-10-17

The development handset's private PanVK build completed Geekbench 6.7.2 Preview
LinuxARM with [Vulkan score 989](https://browser.geekbench.com/v6/compute/6941119).
All eight workloads completed without a benchmark validation-failure message.
This remains an experimental implementation, with no Vulkan conformance claim.

The Android comparison below is an individual SM-A226B/Mali-G57 MC2 result on
Android 13, [Geekbench 6.5.0 score 1254](https://browser.geekbench.com/v6/compute/5706223).
The Linux sub-scores were supplied from the actual result page. Different OS,
benchmark versions and operating conditions make this an indicative comparison.

| Workload | Linux 6.7.2 Preview | Android 6.5.0 | Linux / Android |
|---|---:|---:|---:|
| Background Blur | 543 | 473 | 114.8% |
| Face Detection | 311 | 319 | 97.5% |
| Horizon Detection | 1558 | 1960 | 79.5% |
| Edge Detection | 1977 | 2770 | 71.4% |
| Gaussian Blur | 1332 | 1315 | 101.3% |
| Feature Matching | 349 | 319 | 109.4% |
| Stereo Matching | 2061 | 3771 | 54.7% |
| Particle Physics | 1835 | 4709 | 39.0% |

Particle Physics and Stereo Matching are the largest deficits. Captured compiler
records associated with these phases show no spills and roughly 22 and 15–20
registers respectively. Static load/store estimates dominate many variants;
this is an investigation direction, not a measured hardware bandwidth diagnosis.
Particle uses FP32 vec2 buffers and shared memory with barriers. Stereo uses
byte storage buffers. Neither observation justifies reducing precision or
removing bounds checks or barriers.

An isolated scheduler experiment forwarded the compiler's workgroup-merging
permission. Seventy-two controlled hardware cases passed exact CPU comparisons
with shader cache disabled and the GPU at an existing 955 MHz operating point;
the original 390 MHz minimum was restored afterward. Performance was mixed and
the change was not promoted. Separate cached tests intermittently failed even
on the original driver; the cause remains unidentified.

A same-source release/O3 build with CPU tuning completed a compiler-diagnostic
run: [score 990](https://browser.geekbench.com/v6/compute/6941162), 845 seconds,
exit 0 and no benchmark validation-failure messages. Compiler statistics and
private shader capture were enabled, so this is not a clean speed A/B. It
establishes no convincing improvement over 989. Mesa's explicit LTO prohibition
was respected.

Kernel review found 49 Panfrost scheduler-timeout events while that diagnostic
ran, including one on the graphics slot. A timeout enters the reset/recovery
path even without a separate reset log. Review of an earlier raw kernel log
also places approximately 51 events within the original 989 run, subject to wall
clock conversion uncertainty. These events were not uniquely attributed to
benchmark processes. A completed benchmark and healthy UI do not establish
fault-free operation. The existing 500 ms scheduler limit was not increased.

A separate Rust/Kraid compiler build passed 21 hardware smoke checks, followed
by exact byte-buffer and FP32/shared-memory checks. A process-local fence-status
observer passed native contract tests and hardware fixture checks: 8 successful
waits and 8 exported sync_file statuses, with no observed fence errors. It records
coverage and races; it does not replace application results. The observer and
pipeline capture are diagnostic tools, not benchmark shortcuts.

An explicit, process-local Fossilize layer captured an independent test pipeline;
BI and Kraid replayed it on the build host using a noop DRM shim with no hardware
GPU execution. Exact pipeline capture preserves specialization constants and
layouts missing from raw SPIR-V. Compiler-only replay can help select future
candidates but does not measure GPU speed or prove numerical correctness.

The accepted private launcher still selects the original tested driver. Candidate
builds, runtime captures and proprietary benchmark shader data remain separate;
these experiments are not installed into published boot images. Android-level
performance has not yet been achieved.

A full Kraid diagnostic is in progress. Early observation found canceled GPU
fences (`ECANCELED`) during Background Blur and corresponding scheduler timeouts.
The candidate is not accepted as stable, regardless of any eventual score.
