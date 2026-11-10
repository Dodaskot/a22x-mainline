# Linux systems frontend

Tools → **Linux systems** is backed by `distro.py`, not an archive-extraction
shortcut in C. It shows mounted/read-only status, the active root filesystem,
saved slots, whether a slot has been boot-tested, and any interrupted transaction.
Up to 16 slots use a clipped pixel list with inertia. Refresh invalidates the
previous hit map and held contact before a new list can be used.

The default helper is `/opt/a22x-recovery/distro.py`; override it with
`--distro-helper FILE` or `A22X_RECOVERY_DISTRO_HELPER`. No Systems status call is
made automatically on Home or Tools. Mount userdata with write access in Files
first. The helper does not automatically mount a volume.

## Install alongside the existing Linux

1. Open Systems after mounting userdata RW and the microSD source.
2. Choose **Import bundle from microSD**, then a mounted microSD volume.
3. Browse folders and select a regular `.json` bundle manifest. The chooser uses
   the existing file browser and stat helper; editing/deleting/mounting is not
   available within this chooser. No symlink is accepted as the manifest.
4. Review the exact manifest path, source volume, slot ID, required space and
   available space. Long paths have review pages and cannot be confirmed before
   their final page.
5. Hold **Install** for two seconds. Installation adds a slot without switching
   the active system. Failure is shown with the backend's concrete reason.

Only a prepared ARM64 bundle carrying this phone's audited platform profile is
admitted by the backend. A generic ISO/rootfs is not advertised as bootable. An
imported slot remains labeled **boot not yet tested** until actual qualification.
If import validation leaves a staging directory, Systems reports incomplete
import state; it does not automatically remove unknown directories.

## Switch and recover

Select a nonactive slot to obtain a fresh switch plan. The review names the
current and target system, shared `/home`, retained BOOT and separate `/etc` and
`/var`. It requires a three-second hold. There is no automatic reboot afterward.

The switch is a durable journal of root-directory renames, not one atomic swap.
A pending transaction shows source, target and journal progress. Normal Linux
boot is disabled when the UI knows such a transaction is pending; the independent
PID1/power helper checks remain authoritative even before opening Systems.
**Review Resume** or **Review Rollback** first obtains fresh status for the exact
transaction. The confirmation requires a three-second hold and passes that exact
transaction ID to the backend. A changed transaction is refused, with no automatic
retry. A failed or incomplete result is followed only by inspection of actual
status, not by replaying a mutation.

The shared `/home` requires compatible account names, UIDs and GIDs. The UI shows
`HOME_ACCOUNT_MISMATCH` rather than inventing an account mapping or changing file
ownership. Full Linux used-block backup includes the entire userdata filesystem,
including retained slots and shared home.

## Plan identity and limits

Import/switch planning passes `--output` under the existing private RAM workdir.
The helper saves the full sealed plan exclusively; stdout contains a bounded
summary. The UI verifies summary schema, context, exact output path, intended
operation/slot/source, preservation policy, expiry, hashes, and a single-link
owner-only regular plan file. Execution passes both `--expected-plan-seal` and
`--expected-plan-file-sha256`; a changed private file is refused before mutation.

The UI never extracts an archive, implements root swaps, changes BOOT, repartitions
storage or treats the helper's planning success as proof of hardware bootability.
No boot result is fabricated from directory-fixture tests.

## Coordinates and validation

Tools → Systems is at logical `(360,1452)` at Tools scroll offset zero. Files /
mounts is `(188,1320)` and Refresh systems is `(532,1320)`. Pending transaction
review buttons are Resume `(188,1113)` and Rollback `(532,1113)`; final holds are
centered `(360,1384)`. The slot viewport is x28..692, y422..1124.

`test_systems_ui.c` drives the actual frontend and real filesystem/distro CLI
against owned regular-directory fixtures containing synthetic ARM64 ELF headers.
The eight workflows cover import→switch→return, changed-plan refusal, interrupted
Resume, interrupted Rollback, account mismatch, unmounted and read-only userdata,
and missing platform profile. They verify shared-home inode/content preservation,
per-system state, exact holds and Linux-boot gating, plus slot inertia and tap-stop.
No image, filesystem mount, partition, reboot or phone action occurs in these
tests. See `systems-ui-results.json` and sanitizer reports for exact tested hashes.
