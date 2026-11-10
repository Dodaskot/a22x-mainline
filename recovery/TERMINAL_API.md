# Recovery terminal

`terminal.py` supervises an explicitly started interactive `/bin/sh` PTY. The
on-screen terminal and an administrator SSH session use different entry points;
opening the recovery Terminal screen or querying status does not start a shell.
The shell runs as root in the recovery RAM filesystem. An already mounted Linux
filesystem is accessible under `/mnt/volumes/userdata`; it is not automatically
entered with `chroot`.

## CLI contract

The device form is `python3 -B terminal.py --mode device ACTION ...`. Fixture
tests use `--mode fixture --fixture OWNED_DIRECTORY` before the action. Fixture
mode never uses the recovery device lock or changes mounts, USB or partitions.

| Action | Arguments | Behavior |
| --- | --- | --- |
| `status` | none | Read-only capability and current session metadata. Does not acquire the storage lease. |
| `start` | `--rows N --cols N` | Create one interactive shell and private supervisor. Refuses an inherited administrator SSH lease or an existing/unclean terminal session. |
| `read` | `--expected-session-token TOKEN --after-seq N --max-bytes N` | Read bounded raw output using an absolute byte cursor. |
| `write` | `--expected-session-token TOKEN --data-base64 TEXT` | Accept raw input into the bounded FIFO; no newline is added. |
| `resize` | `--expected-session-token TOKEN --rows N --cols N` | Set the actual PTY window size, including the kernel's normal foreground `SIGWINCH` delivery. |
| `stop` | `--expected-session-token TOKEN` | Terminate and reap the complete supervised process tree, then close the PTY, control endpoint and storage lease. |

`serve` is an internal supervisor entry point. The GUI must not invoke it.

Each public invocation prints exactly one JSON object. Exit status is `0` for
`ok:true`, otherwise `2`. Success objects include `operation`, `mode`, `boot_id`,
`available`, `state`, `session_token`, `rows`, `cols`, `shell_exit_code`,
`command_running`, `reason`, `term`, `ring_bytes`, `max_write_bytes`, and
`storage_lease_held`. Live responses also include `start_seq`, `end_seq` and
`pending_input_bytes`.

`command_running` is true while anything besides the shell at its prompt is alive
under the supervisor: the shell itself running or in uninterruptible sleep, its
children, or orphans it adopted. Zombies do not count, and input still queued for
the shell does. The supervisor samples this five times a second and holds a
positive result for 0.6 seconds, so the moment between two commands of a script
does not read as idle. An inspection that fails reports running. A shell builtin
waiting for input, such as `read`, reads as idle.

Errors use `ok:false`, `operation`, `mode`, `error.code` and `error.message`, plus
`boot_id` when context admission succeeded. Error objects do not require live
session fields. Error strings never include command input or shell output.

The available sizes are 2–256 rows and 10–512 columns. `TERM=vt100` deliberately
advertises a basic terminal; the UI implements a bounded basic ANSI/VT100 view,
not every xterm extension. The shell receives `LANG=LC_ALL=C.UTF-8` and Linux
`IUTF8` line discipline. Device sessions execute the verified ELF descriptor for
`/bin/a22x-terminal-shell` with arguments `ash -i`. This dedicated static BusyBox
1.37.0-6 build supports Unicode through U+10FFFF, including complete Ukrainian
characters during interactive echo, cursor movement, erase and completion.
The original recovery `/bin/busybox` and `/bin/sh` remain unchanged. External
commands resolve through the existing recovery PATH; the dedicated shell has
BusyBox standalone applet lookup disabled.

`status` and operation responses include `shell_path` and `shell_sha256`.
Device admission requires the fixed SHA, a root-owned executable regular ELF
without symlink ancestors or extra hardlinks, and descriptor-based `execve`.
The verified descriptor remains open through fork and closes on successful exec.
Missing or changed shell content reports unavailable; there is no device fallback
to the older shell, whose interactive Unicode limit was U+02FF.

The host fixture keeps `/bin/sh` by default. Tests can explicitly select an owned
static BusyBox using `--fixture-shell PATH --fixture-shell-sha256 SHA` before the
operation. Both arguments are required together and are rejected in device mode.
They propagate only to that fixture's supervisor; they do not accept an arbitrary
device command or change the production shell path. Input and output remain RAM
only; history still uses `/dev/null`.

### Output and input

The output ring retains at most 262,144 raw bytes. A `read` accepts 1–32,768 bytes
and returns `data_base64`, `start_seq`, `next_seq`, `end_seq`, and `lost_bytes`.
`next_seq` is the exact cursor to use next. If the client fell behind the ring,
`lost_bytes` reports the skipped prefix and the next data starts at `start_seq`.
The caller should reset any partial UTF-8/escape sequence when bytes were lost.
Bytes are neither decoded nor rewritten by the supervisor.

Each `write` accepts 1–4,096 bytes. The pending FIFO holds at most 16,384 bytes;
an over-capacity write is rejected before any of that request is accepted.
`bytes_written` and its explicit alias `accepted_bytes` report the number of
bytes accepted into this FIFO. They do **not** acknowledge command execution.
The GUI must not replay input after an uncertain result: it cannot infer whether
the previous bytes reached the PTY. Read-only status/read polling may be retried.

Control keys are ordinary PTY input bytes, for example Enter `0x0d`, Ctrl+C
`0x03`, Tab `0x09`, Esc `0x1b`, or a terminal cursor escape sequence. Ctrl+C
therefore uses the kernel's foreground process-group behavior; it does not send
an unchecked PID signal from the GUI.

## Session and storage lifetime

The service owns the existing cooperative recovery operation lease for the
entire session. Backup, wipe, restore and another independent administrator
session wait until it closes. Terminal `status`, `read`, `write`, `resize` and
`stop` use a separate private control socket, so closing does not deadlock on the
lease that it needs to release. Starting while `A22X_RECOVERY_LOCK_FD` is inherited
is refused. Trusted commands launched from the terminal inherit its already held
lease for existing recovery helpers.

| State | Meaning |
| --- | --- |
| `stopped` | No active terminal token; a new session may be started if available and the storage lease is free. |
| `starting` | Startup is pending. Inspect status; do not issue another Start. |
| `active` | Shell accepts input. |
| `exited` | Shell exited; output and exit code remain visible and the storage lease is still held until Close, which the UI sends when the page is left. |
| `stopping` | Close is reaping descendants. |
| `failed` | Session needs attention. A failed cleanup retains the supervisor, control endpoint and storage lease for another Close attempt. |

An exact 256-bit session token and recovery boot ID bind every data/mutating IPC
request. The socket and metadata are owner-only in a private directory. Both
client and server verify UNIX peer credentials, and the client matches the live
supervisor's PID and process start time. A stale unclean receipt blocks a new
session instead of claiming successful cleanup.

The supervisor is a Linux child subreaper. Close signals only descendants whose
ancestry is verified after opening a pidfd, and reaps adopted daemon descendants
as well as the interactive shell. Termination starts with one SIGHUP, which ends
an interactive shell that ignores SIGTERM, and escalates from SIGTERM to SIGKILL
within the bounded cleanup interval. A successful Stop reply is sent **after**
the lease closes. The UI must then obtain fresh `status` showing `stopped`, no
active token and no lease before leaving the screen. Natural shell exit first
cleans descendants, then preserves its final output in `exited` state.
Readiness checks require pidfds and readable per-task `/proc` children lists
before any shell is forked; a kernel lacking these facilities reports unavailable.

This is an administrator shell, not a command sandbox. Cooperative leases prevent
other recovery operations from racing it; they do not constrain commands typed
by the root user. As with other root processes, forcibly killing the supervisor
with SIGKILL or rebooting does not run a graceful Close sequence.

## Storage and privacy

Command input and output live only in bounded process memory. The service writes
only session metadata under `/run/a22x-recovery/terminal`, and shell history is
disabled with `HISTFILE=/dev/null` and `HISTSIZE=0`. The GUI must suppress terminal
helper result bodies and stderr in `ui.log` and diagnostics. Metadata-only status
is suitable for diagnostics; the terminal transcript is not exported.

## Validation

Run on a normal Linux host with Python supporting pidfds:

```fish
python3 -B -m unittest discover -s tests -p test_terminal.py -v
```

The fixtures exercise real PTY input/output, UTF-8, window size and resize,
interactive `cat` and Ctrl+C, overflow/backpressure, exact token/boot validation,
lease refusal/release, no history or transcript persistence, natural exit,
double-fork/setsid descendants that close the inherited lease, supervisor SIGTERM,
and an injected post-fork startup failure that must retain IPC plus the lease
until descendant cleanup succeeds. Device acceptance is a separate RAM-only run
by the root controller; host tests do not claim phone hardware acceptance.
