# BOOT and stock installer frontend

Open **Tools**, swipe the tools body upward, then choose **Install images**.
The Tools body now scrolls by pixels with inertia; a drag beginning on a button
does not activate it. At scroll offset zero, Display, USB files, Terminal and
Linux systems retain their previous positions. Home and Tools do not poll the
installer or read firmware automatically.

The helper defaults to `/opt/a22x-recovery/install.py`. Set `--install-helper FILE`
or `A22X_RECOVERY_INSTALL_HELPER` to override it. The helper owns all image
decoding, device admission, backups, checkpoints, journals and writes. The C
frontend never opens a partition for writing.

A missing or unqualified Android profile blocks **Stock Android** only. Its
status is shown separately, so a usable BOOT operation is not disabled by an
Android qualification problem. Global installer guard or journal errors block
both new operations and intentional Normal boot in the UI.

## Selecting and reviewing

1. Select **Flash BOOT** or **Stock Android**.
2. Choose a source on an already mounted, writable ext4 microSD volume. BOOT
   accepts a regular `.img`; stock accepts the prepared bundle's `manifest.json`.
   The chooser uses the normal Files browser and exact entry/volume tokens.
   A source chooser cannot edit, delete, mount, or follow a symlink. A long
   source path must be reviewed through its final page before selection.
3. Explicitly select a Linux `a22x.backup.v2` / `linux-partclone-v2` snapshot in
   the backup picker. The helper verifies its actual content and device binding.
   Stock planning also requires current userdata offline and still matching the
   snapshot. BOOT-only flashing may use an earlier same-device Linux snapshot.
4. Choose **Review exact plan**. Swipe through the full selected source and
   backup paths, backup gate, exact target paths and sizes, write order,
   identical targets to skip, and platform checkpoint. The confirmation is
   disabled until the review reaches its end.
5. Hold continuously for **3 seconds** for BOOT or **5 seconds** for stock.
   Leaving the button cancels the hold. No execute request is sent on an early
   release, an expired plan, a blocker, or an invalid receipt.

The small platform checkpoint is separate from the full Linux backup. Stock
installation leaves userdata unchanged and has no automatic reset or reboot.
The review explicitly explains that a later deliberate factory reset in stock
recovery erases Linux data. A completed byte-level verification is not evidence
that an experimental BOOT image or the installed Android system boots.

## Exact admission and interrupted operations

The UI validates the status/plan schema and execution mode, recovery boot ID,
selected source volume/path/token, selected backup, both backup-current gates,
exact transaction and journal location, plan expiry, confirmation prefix,
hold duration and returned private plan path. BOOT plans must declare that
their layout was validated and that runtime boot has not been tested. Stock
plans must describe each of the eight fixed targets exactly once, using only
`write-raw`, `write-sparse` or `skip-identical`. Changed targets follow the fixed
SUPER → PRISM → OPTICS → DTBO → VBMETA_SYSTEM → VBMETA → BOOT → RECOVERY order;
identical targets are omitted from the write order. A BOOT no-op has one skipped
BOOT target and no writes.

The full plan stays in a new owner-only, single-link regular file in the private
RAM workdir. Execution passes the exact full-file SHA-256, plan seal and
confirmation token. The helper rechecks all these values and the source/device
state before mutation. The UI does not treat its own checks as device admission.

An unfinished transaction blocks a new installation and intentional Normal boot
in the UI. Choosing its pending row first refreshes status and matches the exact
transaction/operation/journal/volume token, then requests a fresh Resume plan.
That plan needs a new full review and hold. Failed or unconfirmed execution is
never replayed automatically. Recovery and Download remain separate power
choices; PID1 and the power helper enforce independent boot checks.

The result screen requires an exact successful receipt: matching transaction,
operation, boot ID, checkpoint and journal, independent readback confirmation,
and no userdata write, reset or reboot. A malformed receipt stays uncertain and
asks for a status refresh. The frontend cannot make a multi-partition write
atomic; an interrupted RECOVERY write can require external flashing in Download
mode.

## Local verification and scope

`make install-ui-integration` runs the actual C frontend, event handling,
fork/exec transport and filesystem helper against a labelled installer contract
double. Its 28 workflows / 764 assertions cover source and backup selection,
clipped Tools scrolling and tap suppression, review inertia and stop-touch,
short/outside holds, old hit maps, BOOT and stock no-ops, malformed policy/target
lists, source/backup/boot mismatch, changed plan, missing acknowledgement,
fresh exact Resume, an invalid final receipt, independent BOOT availability
with an unqualified stock profile, and a fresh guard with no journal. The same scenarios pass with
AddressSanitizer and UndefinedBehaviorSanitizer. Leak detection is disabled in
this environment; this is not a LeakSanitizer result.

`make install-backend-integration` runs the separate frontend-to-real-installer
harness in `test_install_backend_ui.c`.
It uses only explicitly owned regular-image fixtures and a read-only backup
manifest-listing adapter. Seven workflows / 123 frontend assertions passed:
BOOT write and no-op, stock write and no-op, changed plan/source refusal, and
fresh Resume after an interrupted stock write. Python independently compared
every target byte, unchanged userdata bytes/metadata, original sources and
backup, saved platform checkpoint, and completed journal/readback hashes.
The final helper-bound receipt is `install-backend-ui-final-results.json`.
These results are recorded separately, without
claiming Partclone qualification from a synthetic backup or physical Android
installation from a host test. The complete Linux backup qualification and
phone acceptance are owned by the root controller.

Native 1080×2408 previews are illustrative renders marked **PREVIEW**. They are
not phone screenshots. The final source hashes and exact successful test runs
are in the machine-readable reports alongside this file.
