# Modem HS2 milestone, 2026-10-24

The bounded CCCI development harness reached **HS2 after FS1446**. The
independent audit returned `PASS_SAME_BOOT_FS1446`, `HS2=true` and
`AP_rebooted=false`. The phone reported `stage=hs2 result=0`; its managed
run finished with `script_exit=0 passed=1`.

This confirms receipt of the modem's second boot-handshake message. The
observer does not consume or acknowledge that control packet and does not
provide persistent modem operation. SIM service, network registration,
calls and mobile data remain unimplemented. The published boot image is
unchanged; this development harness is separate from the boot release.

## What changed

The final filesystem steps preserve the qualified private file owner through
a WRITE and CLOSE, then answer a native-qualified COMPOUND read of 33 bytes
at offset 64 in a 97-byte file. The original DZC3 helper, dispatcher, parser
and packer were used to qualify that filesystem response. Existing protocol
policies and earlier auditors were retained.

The first FS1446 run captured a valid HS2 packet but the observer rejected
it with `-EPROTO`: its old guard required sequence 1 and a zero BUSY register.
The captured control packet instead has sequence 0, assert bit 0 and an
outstanding FS notification bit (`BUSY=0x10`), despite its FS response having
been fully consumed.

The original stock kernel control dispatcher recognizes message 0 with
reserved 0 as HS2. Its RX sequence checker does not enforce sequence ordering
when the assert bit is clear. Those original source fragments were compiled
with event/log/assert stubs and checked against the captured packet, all
32768 previous sequence values, HS1 discrimination and invalid asserted
sequences. This is source-level qualification, not execution of the entire
stock kernel.

The corrected observer admits this exact packet only after complete FS1446,
with the existing full-prefix state and ring-cursor checks. Only BUSY 0 or
the known FS notification bit is permitted for this case. The final FS TX
read and write cursors both equal 16896.

## Validation and recovery

- The full build and regression suite passed; 1471 test executables were
  archived. The newest full-prefix fixture exercises the actual
  sequence-0/assert-0/BUSY-0x10 case.
- ASan/UBSan admission tests reject 128 packet-bit mutations, 31 unexpected
  BUSY bits, premature FS counts and incorrect size, queue or phase.
- The independent capture audit retains the frozen early-prefix checks,
  all later filesystem audits, reset-claim rejection, release, clock,
  USB and session-health checks.
- The original failed run was preserved and never repeated. After its
  protective reboot, a fresh NORMAL0/two-HS1 bootstrap passed, followed by
  a verified replay of the last successful FS1445 prefix. The corrected
  candidate then ran once, reached HS2 and completed without an AP reboot.

The successful capture is internally identified as `cellular-d4084-20261013`,
using candidate `cellular-d4082-20261013`, modem generation 4. Its sealed
archive SHA-256 is:

`aefa3ede4348f3cde4c79020a703c706d9656019fd87d4668d7b02ecc0f08a7a`

The tested module SHA-256 is:

`be4330afc73272f80e6a58437fe44bffe202b11fd2a5b1074470a094db541072`

Device-specific NVRAM, raw captures and the development harness remain
outside this public tree. These hashes identify retained evidence; the
public documentation alone is not a standalone reproduction package.

## Automation and next work

The verified FS1446 and HS2 cases are recorded in local automation. New
FS1446 candidates receive the bounded control-packet handling and regression
checks before freezing. The phone wrapper and independent host auditor both
verify the captured HS2 bytes. Failed captures are never promoted to passing
checkpoints.

The controller now terminates successfully with `HS2_CONFIRMED`; no further
boot-discovery cycles are required for this milestone. Next work is the
persistent control-channel/READY lifecycle, receive-pointer and ACK handling,
modem exceptions and runtime services needed for cellular connectivity.
