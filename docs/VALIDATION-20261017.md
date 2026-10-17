# A22X development validation, 2026-10-17

These observations concern the development handset on v142 / kernel #100.
They do not change the contents of the published boot images. Experimental
modem and GPU artifacts remain separate from the accepted kernel baseline.

## OpenCL

An unmodified Geekbench 6.7.2 Preview ARM run completed all eight workloads
on Mali-G57 MC2 through Panfrost/Rusticl, Mesa 26.2.4, with a private libclc
22.1.8.3 deployment. Runtime was 1,599 seconds; the process exited successfully
and uploaded [OpenCL score 174](https://browser.geekbench.com/v6/compute/6941037).

**This is not an all-workload correctness pass.** Gaussian Blur scored zero
despite reporting 46.2 Mpixels/s. The retained stderr reports
`workload 407 failed validation`; read-only inspection of the workload-name
mapping identifies 407 as Gaussian Blur. The underlying cause remains unknown.
The original result is preserved. Further diagnostics must compare actual
Gaussian outputs without changing benchmark inputs, validation or scoring.

## Experimental Vulkan graphics and compute

A private PanVK frontend port, based on Mesa 26.3-devel and the experimental
[panvk-g99-jm project](https://github.com/Noysz/panvk-g99-jm), enumerated the
actual Mali-G57 MC2. It retained the mainline Panfrost DRM backend.
A hardware compute dispatch completed and all 64 output integers matched the
independent CPU expectation `i * 3 + 7` exactly; fence timeout was five seconds.

The original private build also passed an offscreen graphics test: all 4,096
clear-color pixels and all 4,032 strictly interior/exterior triangle pixels
matched an independent CPU expectation. The 64 pixels exactly on the triangle
edge were excluded from the raster tie-breaking comparison. A separate CPU
fixture verified the harness; the hardware build explicitly rejects llvmpipe.

A separate build enabling Wayland repeated both pixel checks successfully.
Native `vkcube` on Mali-G57 completed one-frame and three-frame Wayland runs.
A 120-frame attempt timed out while the Phrog login layer obscured the window;
screenshots show the login screen only. This is consistent with occluded-surface
throttling, but the cause is not proven. The phone boot and greeter/compositor
identities remained unchanged, with no observed GPU fault/reset messages.
Visible window rendering and sustained presentation remain unverified.

These are limited hardware graphics/compute checkpoints, not Vulkan conformance
or broad application compatibility. No global Vulkan ICD or system Mesa
replacement was installed. The Wayland build SHA256 is
`52f8672ac5d52fc1b3255b2cc1e4cb245f5a28c8c0bd864e21b8ddaeaf627275`.

## Geekbench 6 Vulkan

The unmodified Geekbench 6.7.2 Preview ARM binary completed all eight Vulkan
workloads on Mali-G57 MC2 using the private Wayland-enabled PanVK build above.
The run took **839 seconds (13 minutes 59 seconds)**, exited with status zero
and uploaded [Vulkan score 989](https://browser.geekbench.com/v6/compute/6941119).
There were no benchmark validation-failure messages, including for Gaussian
Blur. The phone remained on the same boot. No software fallback or benchmark
instrumentation was used; modem cycles were paused during this run.

This is an application-level benchmark result, not Vulkan conformance. The
driver still warns that it is experimental and not a conformant implementation.
The OpenCL score above contains a failed workload, so its ratio to this Vulkan
score should not be interpreted as a clean performance comparison.

The tested driver is available through the per-user `a22x-vulkan-run PROGRAM`
launcher and a Vulkan Test desktop entry. Loader enumeration through the
installed launcher passed on Mali-G57 MC2; global driver selection is unchanged.

## Touchscreen login and shell selection

A private Phrog 0.53 / libPhosh 0.57 greeter was installed with the existing
greetd and ordinary PAM authentication. Actual phone validation covered:

- Touch user/session selection, PIN keypad and alphabetic Stevia keyboard.
- Incorrect-password rejection and successful password authentication.
- Phosh and Plasma Mobile login, then normal logout back to the chooser.
- Phosh native lock/unlock; Plasma lock was not separately tested.
- No greeter-private environment leaking into either desktop session.
- A “Switch shell” launcher using each shell's normal logout confirmation.

The temporary test account and credential were removed after validation.
The chooser was committed and its trial rollback timer cancelled. Existing
native lock actions remain in place. This is a development-device deployment;
the published root filesystem is not automatically updated by these notes.

Phoc was updated to 0.57.0-2 to repair a missing old GNOME Desktop ABI dependency.
The preserved patched Phosh binary separately uses an isolated, signed-package
compatibility library through an explicit dynamic-loader library path.

## Plasma packages and memory

The checked ARM repositories offered KWin/libplasma 6.8 alongside Mobile/Nano
6.7.5 and Workspace 6.7.5. The package solver accepted this combination, but
post-install loader checks exposed libplasma.so.8 versus required .so.7.
All 28 KDE packages were restored from signed cached packages to a coherent
6.7.5 stack. Loader checks and actual Plasma Mobile login then passed.
Six independent updates, including the Phoc repair, were retained.
Future deployment rejects this known mixed ABI before making changes.

**Plasma Mobile 6.8 is not installed.** The previously documented KWin 6.7.5
GPU-buffer leak has not been shown fixed. Short RSS/PSS observations cannot
exclude a DMA-BUF leak or establish long-term memory stability.
The handset has approximately 3.44 GiB usable physical RAM, zstd zram and
4 GiB disk swap. No RAM or swap tuning was applied.

## Modem checkpoint and recovery

The latest fully qualified checkpoint before the Vulkan benchmark is
**28 RPC + 571 filesystem requests**, candidate D1847 / live D1848, modem
generation 9. HS2 remains absent; SIM,
network registration, calls and mobile data are not working.

FS565 passed original-DZC3 qualification, the full observer/sanitizer suite and
module build. In live attempt D1829, the on-phone protocol, claim-release and
clock checks passed, but a final UI check incorrectly required a running Phosh
process while the new Phrog chooser was active. The failure path sealed its
archive and requested a normal reboot. The attempt remains unqualified as a
complete cycle; its failed evidence is preserved unchanged.

After reboot, the build host lost its temporary USB IPv4 address. A persistent
NetworkManager USB profile restored access. The phone returned to NORMAL0
with greetd active. The session-aware checker now verifies the active greetd session, exact shell
and compositor executables, and compositor-owned Wayland socket. Fifteen
targeted tests passed. Recovery bootstrap passed two HS1 exchanges without
rebooting. A fresh FS565 cycle (D1830) then passed complete independent capture
audit, UI/USB checks, claim release and clock stability on the same boot.
The autonomous loop resumed from the next actual request, FS566.

FS566 through FS571 subsequently passed autonomous complete cycles on the same
boot. The loop paused before executing the fully prepared FS572 candidate to
provide a quiet GPU benchmark window. After the completed Vulkan run and GPU
cleanup, the autonomous loop was restarted with that prepared FS572 candidate.
