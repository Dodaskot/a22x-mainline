# Local terminal frontend

Tools → Terminal opens status only. **Start local shell** explicitly starts the
real `terminal.py` PTY. The shell is root in recovery RAM, initially at `/`.
Already mounted installed Linux is under `/mnt/volumes/userdata`; the recovery SD
backup mount is `/mnt/backup`. The UI does not automatically mount or chroot.

The terminal has its own bounded input queue. It never reuses or overwrites the
file editor buffer. Enter, Tab, Esc, Ctrl+C, Ctrl+D, Backspace and four arrow keys
are raw PTY input; the EN/UK keyboard also supports punctuation, tilde and
backtick. Arrow interpretation, history and completion belong to the shell.
Unconfirmed input is discarded with an explicit error, never automatically sent
again. A gap in the output ring resets only the display/parser, preserving
queued input such as Ctrl+C.

The display is a 48-column × 21-row grid with at most 512 history rows, clipped
pixel scrolling and elapsed-time inertia. A new touch stops inertia without
pressing a key or activating a file. Output reads and input writes do not cancel
a held keyboard contact or a scroll animation. New output follows the bottom
only while the view is live; **Live** returns to the current screen.

`terminal_model.h` handles incremental UTF-8, one combining mark per cell, basic
wide cells, CR/LF/tab/backspace, cursor addressing and saved cursor, erase and
insert/delete operations, scroll margins, basic DEC graphics, normal/alternate
screens, application cursor mode and SGR colors. It supports standard 16-color,
256-color and RGB attributes. OSC/DCS content is ignored. This is a bounded
basic ANSI/VT100 frontend, not a complete xterm implementation; some fullscreen
programs and complex Unicode sequences may render imperfectly. Unsupported
glyphs use a replacement glyph. The generated JetBrains Mono glyph atlas, with Adwaita Mono for rare symbols, has
no runtime FreeType dependency; provenance is in `font-provenance.json`
and its license is covered by `LICENSE.fonts`.

## Lifetime, privacy and exact service identity

The backend holds the recovery storage operation lease for the whole shell
session. Other independent mounts/backups/wipes/USB exports are refused while it
is active. Starting a terminal never implicitly stops MTP. Commands launched
inside the shell inherit the validated helper lease, as specified by the backend.

Leaving the page asks nothing. Back, the gestures and **Exit terminal** go Home.
The session then follows one rule: while `command_running` is true it stays in the
background, and once that is false it closes. So an idle shell closes at once, and
a command left running closes its session when it ends. Keys typed in the last 0.7
seconds count as running, because the shell may not have started them yet.

In the background the UI reads once a second, or sooner when the command has ended
and output remains. Home shows **Running** on the Terminal tile, then **Finished**
when the session has closed with output to see. Opening Terminal then shows that
output, and a new shell starts under it. A flood of output that outruns those reads
shows as an output gap, and a new shell after an idle exit starts on a clean page.

Stop waits for a fresh status of stopped with no token and no lease. A transient
IO_ERROR/SERVICE_STALE immediately after Stop is retried as status only for up to
2.5 seconds. Trouble in the background never touches the page in front: the UI
asks for status again after 5 seconds, and a page that opens the terminal shows it.
Input is never replayed automatically. A still-starting shell is polled using the
same token for a bounded interval, with no second Start. A lock or the power menu
only covers the page, so a shell under them is not left and does not close.

Ctrl+C on the page stops a command. A confirmed power action stops the session in
`init` before unmounting, and the confirmation page says so while a command runs.

Command and output bytes are neither printed to UI stderr nor included in UI
helper-result logs. Terminal helper stderr is suppressed. Read/write result
bodies are cleared before freeing. Only operation names, exit status, scroll
positions and aggregate rendering timing can appear in UI logs. This does not
restrict explicitly typed root commands from writing their own files.

## Integration

Set `--terminal-helper /opt/a22x-recovery/terminal.py`, or the environment override
`A22X_RECOVERY_TERMINAL_HELPER`. The production default is that same path.
Terminal status is requested only upon entering Terminal or explicitly refreshing
it; Home/Tools do not poll terminal state. See `../TERMINAL_API.md` for the PTY
supervisor contract.

### Touch coordinates for owned harmless phone acceptance

All coordinates below use the existing logical 720 × 1600 input transform. The
native display is 1080 × 2408; use the existing evdev harness mapping, not an
assumed raw scale. Start from a fresh UI with EN, no Shift, no symbol layout.

| Action | Logical center |
| --- | --- |
| Home → Tools | 360, 1350 |
| Tools → Terminal | 532, 1363 |
| Start local shell | 360, 1371 |
| `p`, `w`, `d` | 661,1045; 125,1045; 210,1125 |
| Enter | 638,1285 |
| Tab / Esc / Ctrl+C / Ctrl+D / Live | 89,831 / 221,831 / 360,831 / 506,831 / 638,831 |
| Left / Up / Down / Right | 105,893 / 275,893 / 445,893 / 615,893 |
| Exit terminal | 482,1412 |

Wait for the active terminal before typing `pwd` plus Enter. The visible result
should be `/` in recovery RAM. Observe the framebuffer or a screenshot of this
owned harmless command; the transcript is intentionally absent from ui.log.
The scrollable output rectangle is x48..672, y244..748. Gesture logs use:

```text
[recovery-ui:terminal-scroll] offset_px=N first_row=N rows=N tap_suppressed=1
[recovery-ui:kinetic-start] surface=terminal offset_px=N velocity_px_s=N
[recovery-ui:kinetic-stop] surface=terminal offset_px=N reason=... frames=N elapsed_ms=N
```

With `--profile-render`, the final line also records aggregate render costs. It
does not claim rendered 90 FPS merely because a 90 Hz panel mode is selected.

## Local validation scope

`test_terminal_model.c` exercises 59 assertions and 1,270,000 deterministic random
parser bytes. `test_terminal_ui.c` calls the actual frontend and fork/exec CLI
path against an explicitly labeled file-backed helper double with no PTY or
hardware I/O. Its cases include ordinary I/O, busy/unavailable startup, split
output/sequence errors, output gaps, unconfirmed writes, stale Stop, a transient
post-Stop status race, Close during an in-flight write, delayed Start, and queued
Ctrl+C surviving a simultaneous output overflow. It checks log redaction and
preservation of the file editor buffer.

Actual host PTY/lease tests belong to `../tests/test_terminal.py`; physical
on-screen acceptance belongs to the root controller. Native-resolution preview
PNGs are illustrative render checks, never phone captures.
