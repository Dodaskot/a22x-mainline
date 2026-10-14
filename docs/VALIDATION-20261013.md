# A22x validation, 2026-10-13–14

Tested device: Samsung SM-A226B. Running image: v142, Linux
`7.2.0-a22x+`, build #100, LLVM 23.1.1. This update publishes source and
results; the tested v142 boot image is published in `releases/v142/`. The
v135 image and connectivity modules are unchanged.

## USB-C OTG

The MT6360 TCPC uses GPIO9 for its interrupt and the charger's 5 V boost
regulator for source VBUS. The driver now enables the standard alert mask
after registering the IRQ. The I2C controller only requests a supply declared
on its own node, avoiding a dependency cycle through the TCPC child.
MUSB PHY power references are balanced across role changes.

Verified on the running phone:

- Automatic device/sink mode, configured NCM networking and charging on a PC.
- Automatic host/source mode with a USB-C mass-storage drive, 5 V VBUS.
- exFAT mounted read-only; one file sample read successfully.
- Normal plug orientation: 256 MiB direct read in 38.5001 seconds, about 7 MB/s.
- Reversed plug orientation: mount and 64 MiB direct read passed.
- After unmounting and reconnecting the PC: device/sink, configured NCM,
  charging restored, boost regulator disabled with zero users, same boot.
- USB device-mode recovery also passed after a subsequent modem-cycle reboot.

The drive negotiated USB high speed (480 Mb/s). The measured read throughput
is one test result, not a maximum-speed claim. No files were written to the
drive. Write throughput, hubs, PD chargers and OTG across suspend remain untested.

## Sensors and desktop

MN78911 responded to covering/uncovering and illumination over 337 samples:

| Observation | Measured result |
|---|---|
| Ambient-light raw counts | 1–15577 |
| Proximity raw counts | 85–321, near threshold 200 |
| Sensor-proxy proximity | far → near → far |
| Actual display backlight | 4–251 of 255 in response to light changes |

The driver uses an estimated scale of 0.5 lux per count. Absolute lux accuracy
was not calibrated against a reference instrument; no calibration values were
changed. Screen blanking during an actual call was not tested.

ICM-40607 raw acceleration reads passed. A transient SPI timeout/FIFO read error
was also recorded on this boot; the cause remains unresolved. Full orientation
transitions were not retested. This is separate from the MN78911 lux calibration.

Phosh/Phoc, Wi-Fi, Bluetooth controller presence and charging were checked.
Audio, NFC transactions, Bluetooth accessory use and suspend power consumption
were not comprehensively retested in this update. The local Phosh session uses
a private GNOME desktop ABI compatibility library after a userspace upgrade;
that library is not included here.

## Modem checkpoint

The latest independently verified bounded cycle, D601, consumed **28 RPC and 195 filesystem
responses** on kernel #100. FS122 sought offset 64 in a private RAM copy of
an NVRAM file; FS123 GETSIZE returned 176 bytes without changing the cursor.
FS124 wrote 56 bytes into that private RAM copy, preserving the surrounding
bytes and advancing the cursor to 120. FS125 closed the active handle while
retaining the full RAM copy and the immutable OPEN/SEEK/WRITE snapshots.
FS126–129 answered absent OPOTA configuration-file time queries in the T and S
namespaces. Only independently qualified transport-padding bytes may vary.
FS130 and FS131 each read 48 bytes at offset 64 from a 160-byte NVRAM file and
closed at cursor 112. FS132 opened that file read-only with its cursor at zero,
retaining the earlier private RAM ledgers. FS133 read 64 bytes and advanced the
active cursor to 64; FS134 closed it, preserving immutable OPEN and READ records.
FS135 reopened the same file with flags `0x20010400`, creating a fresh private
RAM copy of 160 bytes at cursor zero. FS136 sought offset 64; FS137 returned
size 160 without changing that cursor. FS138 wrote 48 received bytes into the
private copy at offset 64, advancing to 112 and preserving the first 64 and
last 48 bytes. FS139 closed it while retaining the immutable OPEN/SEEK/GETSIZE/
WRITE records and full RAM copy. FS140 answered a readonly compound request
for another file: 800 bytes read at offset 64 from an 864-byte file, closed at
cursor 864, with previous RAM ledgers preserved.
The original DZC3 daemon's behavior was checked offline; 407 host test PASS lines, including full-prefix observer
and sanitizer checks, preceded the run. FS141 read 9048 bytes from a 9112-byte file and returned them as three packets.
FS142 (MD144/AP145) read 496 bytes at offset 64 from a 1056-byte file and closed
at cursor 560. FS143 (MD145/AP146) opened it read-only at cursor zero; FS144
(MD146/AP147) read 64 bytes; FS145 (MD147/AP148) closed the active handle at cursor
64, preserving the immutable OPEN and READ snapshots. FS146 (MD148/AP149) opened a fresh private RAM copy of 1056 bytes
with flags `0x20010400` and cursor zero. FS147 (MD149/AP150) sought offset 64;
FS148 (MD150/AP151) returned size 1056 without changing that cursor. FS149
(MD151/AP152) wrote 496 received bytes into the private copy at offset 64,
advancing to 560 and preserving the first 64 and last 496 bytes. FS150
(MD152/AP153) closed the active handle while retaining the full RAM copy and
immutable OPEN/SEEK/GETSIZE/WRITE records. FS151 (MD153/AP154) answered a readonly
compound request for another 144-byte file: 40 bytes read at offset 64, closed
at cursor 104, with the previous modified RAM copy retained. FS152 (MD154/AP155)
opened that 144-byte file read-only at cursor zero; FS153 (MD155/AP156) read
64 bytes; FS154 (MD156/AP157) closed it at cursor 64 while preserving immutable
OPEN and READ records. FS155 (MD157/AP158) opened a fresh private RAM copy of
that 144-byte file with flags `0x20010400` and cursor zero. FS156 (MD158/AP159)
sought offset 64; FS157 (MD159/AP160) returned size 144 without changing that
cursor. FS158 (MD160/AP161) wrote 40 received bytes into the private RAM copy at
offset 64, advancing to 104 and preserving the first 64 and last 40 bytes.
FS159 (MD161/AP162) closed the active handle while retaining the full RAM copy
and immutable OPEN/SEEK/GETSIZE/WRITE records. No persistent NVRAM writes occurred.

**HS2 has not been reached.** D601 automatically recovered to NORMAL0,
with all eight CPUs, held/powered modem domain, Phosh/Phoc and USB networking
passing checks. Its formal result is
`FAIL_UNHANDLED_FS_AFTER_195_REPLIES_CONSUMED`. D589 consumed the CLOSE191 reply, but its
automatic reboot entered charging mode (mode 8). A separate qualified
two-volatile-word recovery restored NORMAL0, with all eight CPUs, held/powered
modem domain, Phosh/Phoc and USB networking passing checks. Its original
formal result remains `FAIL_RECOVERY_AFTER_191_REPLIES_CONSUMED`. D586 had
automatically recovered to NORMAL0. D552 captured the WRITE181 continuation without sending a
filesystem reply, but its automatic reboot entered charging mode (mode 8).
A separate qualified two-volatile-word recovery restored NORMAL0, with all
eight CPUs, modem domain, UI and USB checks passing. Its original formal
result remains `FAIL_RECOVERY_AFTER_180_REPLIES_CONSUMED`. D437's automatic reboot returned to charging mode (mode 8),
so its initial recovery check failed. D476 also returned to mode 8 after
consuming 158 replies. A separate qualified two-volatile-word
SRAM recovery and ordinary normal reboot restored NORMAL0 with all checks
passing. The original failure remains classified as
`FAIL_RECOVERY_AFTER_145_REPLIES_CONSUMED` for D437 and
`FAIL_RECOVERY_AFTER_158_REPLIES_CONSUMED` for D476. Both separately recovered
to NORMAL0 with all checks passing; neither modem cycle was repeated.
Before D440, all 20 LK tags were compared after the separate recovery. Names,
structure and payloads were identical; only 580 unused bytes changed. The new
cycle was bound to the exact qualified hash, preserving the original runs.

Wi-Fi restoration had previously failed because the hotspot could not be found;
subsequent cycles use USB access without retrying that unavailable connection.

The earlier D397 automatic reboot returned to charging mode (mode 8), so its
initial recovery check failed. A separate qualified recovery using two volatile
SRAM words and an ordinary normal reboot restored NORMAL0 with all recovery
checks passing. Its original failure is preserved as
`FAIL_RECOVERY_AFTER_132_REPLIES_CONSUMED`. Before D401, comparison of all 20 LK
tags proved identical names, structure and payloads; 838 unused bytes differed.
The new cycle was bound to the exact qualified hash without relaxing the check.

D416's first independent verification rejected a previously unlisted Wi-Fi
scan-cancellation STATE message. Driver source confirmed its meaning; only that
exact message was added to the allowed ambient log entries. Reverification of
the same sealed evidence passed, with no repeated modem run. The original
rejection is retained, and kernel-fault checks remain enforced.

FS141's original DZC3 dispatcher and independent response packer were tested
offline (185 and 30 cases). They emit three packets of 3476, 3476 and 2192 bytes;
the first two carry a continuation flag, and each repeats the operation code.
The tested harness stages and verifies all three ring frames before publishing
them, using AP sequences 142, 143 and 144. The phone consumed all three; actual
TX readback was independently checked against the native fragments. Recovery
to NORMAL0 passed.

Tests cover corruption of every byte in the 9200-byte ring transaction, readback
failures at each fragment boundary and the final byte, 75 fault-injection points,
no retry after an uncertain send, and premature HS2 rejection. The existing
4096-byte limit for an individual packet remains unchanged. After FS141, the captured MD sequence became 144 for request ordinal FS142.
FS142 answered it with AP sequence 145; the next MD sequence is 145 for FS143.
The harness and independent audits now track request ordinal and transport
sequence separately, while preserving the prior fragmented transaction.

FS160 has been qualified offline using the original DZC3 dispatcher (185 cases)
and independent packer (30 cases): two packets of 3476 and 2936 bytes. The TX
ring requires a 6448-byte transaction split into 272 bytes at its end and 6176
bytes at its beginning. D482 verified the actual captured wire bytes and consumption
of both packets (AP163/AP164), retaining earlier private RAM and readonly
ledgers. The next request carries MD164, confirming the separate request ordinal
and packet sequence accounting. FS160 closed at cursor 6398 in a 12732-byte file;
no payload reduction was used. The formal result remains
`FAIL_UNHANDLED_FS_AFTER_160_REPLIES_CONSUMED`, with NORMAL0 recovery passing.

D485 also consumed FS161 (MD164), the corresponding readonly compound request
in the Y namespace, returning 6334 bytes as AP165/AP166. The 6448-byte wire
transaction is contiguous at TX6176–12624; the handle closed at cursor 6398
in a 12732-byte file. Both actual wire packets and the earlier FS160 transaction
were independently verified, retaining all prior RAM and readonly ledgers.
D488 consumed FS162 (MD166/AP167), opening the X namespace file read-only
with flags `0x700`, an active cursor of zero and a size of 12732 bytes. The
previous 304 artifacts and all private RAM and readonly ledgers were preserved.
D491 consumed FS163 (MD167/AP168), reading the first 64 bytes and advancing
the active cursor to 64. The immutable FS162 OPEN record remains at cursor
zero; all earlier private RAM, readonly ledgers and fragmented wire captures
were retained. D494 consumed FS164 (MD168/AP169), closing the active handle
at cursor 64 while retaining the immutable OPEN and READ records and 308
earlier artifacts. D497 consumed FS165 (MD169/AP170), reopening that file
with flags `0x10400` and cursor zero. A fresh 12732-byte private RAM copy was
captured and its complete hash independently checked against the qualified
archive snapshot. D500 consumed FS166 (MD170/AP171), seeking to offset 32
without changing the RAM contents. The OPEN descriptor and a snapshot of
the actual RAM after OPEN remain immutable for later comparisons; all 313
earlier artifacts were retained. D505 consumed FS167 (MD171/AP172), writing
32 received bytes at offset 32 in the private RAM copy and advancing the
cursor to 64. Its full 12732-byte capture matches the immutable OPEN copy
with only bytes 32–63 replaced by the actual request payload. Previous
OPEN and SEEK records and all 315 earlier artifacts were retained. The
original DZC3 dispatcher accepted 259 payload variants in 777 descriptor
cases; only the write data may vary. D508 consumed FS168 (MD172/AP173),
closing the active handle at cursor 64. Its entire RAM capture equals the
immutable WRITE record; earlier OPEN, SEEK and WRITE snapshots and all 318
prior artifacts were retained. The next request is FS169 (MD173).
D513 consumed FS169 (MD173/AP174), opening a fresh 12732-byte private RAM
copy in the Y namespace with flags `0x10400` and cursor zero. Its full captured
hash matched the qualified Y snapshot, while the modified X copy and 321
earlier artifacts were retained. The next request is FS170 (MD174), seeking
to offset 32. D513 initially rebooted into mode 8, so the formal result remains
`FAIL_RECOVERY_AFTER_169_REPLIES_CONSUMED`. A separate qualified two-word SRAM
recovery restored NORMAL0 with all CPU, modem, UI and USB checks passing.
The initial automatic recovery failure is retained; no modem cycle was rerun
for that recovery.

D516 consumed FS170 (MD174/AP175), seeking to offset 32 in the Y private
RAM copy without changing its contents. The initial OPEN descriptor and RAM
snapshot, the modified X copy and all 324 earlier artifacts were retained.
Automatic NORMAL0 recovery passed. The next request, FS171 (MD175), writes
32 bytes at that cursor. D519 consumed FS171 (MD175/AP176), writing the
received 32 bytes at offset 32 in the Y private RAM copy and advancing to 64.
Its entire 12732-byte capture was independently verified against the immutable
Y OPEN snapshot and actual payload, preserving the first 32 and final 12668
bytes. The modified X copy and all 326 earlier artifacts were retained.
Original DZC3 argument and dispatcher checks cover 259 payloads and 777
descriptor cases for this Y write. Automatic NORMAL0 recovery passed; the
next request, FS172 (MD176), closed that handle in D522 (AP177). Its entire
12732-byte RAM capture equals the immutable Y WRITE record; the earlier
OPEN, SEEK and WRITE descriptors, both modified copies and all 329 prior
artifacts were retained. Automatic NORMAL0 recovery passed. The next request
was FS173 (MD177/AP178), consumed in D525. It reopened the X file with flags
`0x21010400` and cursor zero. The new complete 12732-byte RAM snapshot equals
the current modified X copy from the earlier CLOSE, preserving the header
written by FS167 rather than reverting it to the original archive contents.
Both X and Y histories and all 332 prior artifacts were retained. Automatic
NORMAL0 recovery passed. FS174 (MD178/AP179) then sought offset 64 in D528,
changing only the active cursor while retaining the complete modified X RAM
copy and immutable OPEN record. All 335 prior artifacts were preserved, and
automatic NORMAL0 recovery passed. FS175 (MD179/AP180) returned size 12732
in D531 without changing the active cursor or RAM. All 337 prior artifacts
were preserved, and automatic NORMAL0 recovery passed. The next request,
FS176 (MD180), starts a fragmented WRITE declaring 6334 bytes. D534 consumed
only its first 3476-byte packet, moving RX from 17480 to 496 across the ring end,
and captured the final 2924-byte packet without acknowledging it. Both packets
use MD sequence 180. The probe sent no FS176 response and changed no file RAM.
All 339 prior artifacts were retained; automatic NORMAL0 recovery passed. Its
formal result is `CAPTURED_FS176_CONTINUATION_AFTER_175_REPLIES_CONSUMED`.
Offline D535 executed the original receive block on both actual packets and
reassembled 6380 bytes. The original argument parser and WRITE dispatcher
confirmed a 6334-byte write at offset 64, advancing to 6398 with a native
40-byte reply. Qualification covers 259 payload cases, 777 dispatcher cases
and 512 padding variants. D537 then consumed the FS176 response (AP181),
advancing the active X cursor from 64 to 6398. The complete 12732-byte RAM
snapshot equals the previous modified X copy with only bytes 64–6397 replaced
by the received 6334-byte payload. The first 64 and last 6334 bytes, immutable
OPEN/SEEK/GETSIZE records and all 339 prior artifacts were preserved.
Automatic NORMAL0 recovery passed. FS177 (MD181/AP182) then closed the active
X handle at cursor 6398 in D540. Its complete 12732-byte RAM capture equals
the immutable fragmented WRITE record, and all 343 prior artifacts were
retained. Automatic NORMAL0 recovery passed. FS178 (MD182/AP183) reopened
the Y file with flags `0x21010400` and cursor zero in D543. Its complete
12732-byte immutable OPEN snapshot equals the actual modified Y copy from
CLOSE172. It preserves both Y histories and the independently modified X
WRITE176/CLOSE177 records; all 346 prior artifacts were retained. Automatic
NORMAL0 recovery passed. FS179 (MD183/AP184) sought offset 64 in D546,
changing only the active Y cursor and retaining its immutable OPEN-at-zero
record and full RAM. All 349 prior artifacts were preserved, and automatic
NORMAL0 recovery passed. FS180 (MD184/AP185) returned size 12732 in D549,
retaining cursor 64 and the immutable OPEN/SEEK records and full RAM. All 351
prior artifacts were preserved, and automatic NORMAL0 recovery passed. The
first fragment of WRITE181 (MD185) declares 6334 bytes. D552 separately
consumed that first fragment without a filesystem reply and captured its
2924-byte continuation, retaining all 353 prior artifacts and both RAM
histories. The actual fragments are 3476/2924 bytes with the same MD185;
original DZC3 reassembly produces a 6380-byte request while retaining the
final header size word 2924. D553 qualified the original parser and dispatcher
offline: 259 payload variants, 512 padding variants and 777 dispatcher cases
produce status zero/count 6334 and cursor 6398. No WRITE181 reply was sent during that capture and qualification. D552's initial charging-mode recovery failure remains
recorded despite successful separate NORMAL0 recovery. D555 then consumed the
actual WRITE181 reply after both incoming fragments were independently checked.
Only the current Y RAM copy was updated at bytes 64–6397; its cursor advanced
to 6398, the full 12732-byte immutable WRITE snapshot was captured, and all
353 prior artifacts and OPEN/SEEK/GETSIZE records were retained. Automatic
NORMAL0 recovery passed with all CPU, modem-domain, UI and USB checks. D559 consumed CLOSE182 (MD186/AP187), closing the active Y handle at cursor
6398 and capturing its complete RAM copy equal to WRITE181. All 357 prior
artifacts were retained, and automatic NORMAL0 recovery passed. D559 stopped at the next unhandled request after 182 replies. D558's
original archive lacked the CLOSE182 RAM endpoint and failed independent
verification; it is retained. The capture script was corrected for a separate
D559 cycle, and preflight now requires every module blob to be archived once.
The next request, FS183, is a readonly compound operation on LTEK_022:
5196 bytes read at offset 64 from a 10456-byte file, closing at cursor 5260.
D560 qualified the original DZC3 behavior offline: its 5252-byte logical
response is split into 3476/1796-byte packets (AP188/AP189). Live handling
of FS183 was then verified in D562: both packets were consumed, their complete
5312-byte ring readback matched the qualified frames, and the readonly handle
closed at cursor 5260. All 360 prior artifacts, including the complete X and Y
RAM histories, were retained. Automatic NORMAL0 recovery passed. D562 stopped at the next unhandled request after 183 replies.
Readonly OPEN184 (MD189/AP190) for the same 10456-byte LTEK_022 file
was qualified offline in D563 and verified on the phone in
D565. It returned handle 1 with flags `0x700` and cursor zero, retaining an
immutable OPEN record and all 363 prior artifacts, including both earlier
RAM histories and FS183's two-packet wire capture. Automatic NORMAL0 recovery
passed. READ185 (MD190/AP191) was then verified in D568: the first 64 bytes
matched the qualified snapshot, only the active cursor advanced to 64, and
an immutable READ record was retained alongside OPEN184 at cursor zero.
All 365 prior artifacts, including the complete X/Y RAM histories and FS183
wire capture, were preserved. Automatic NORMAL0 recovery passed. CLOSE186 (MD191/AP192) was then verified in D571: the active
readonly handle closed at cursor 64, retaining immutable OPEN184 at zero and
READ185 at 64. All 367 prior artifacts and earlier X/Y RAM histories were
preserved, and automatic NORMAL0 recovery passed. OPEN187 (MD192/AP193) was
then verified in D574: LTEK_022 reopened with flags `0x20010400` at cursor
zero, creating a private 10456-byte RAM copy equal to the qualified DZC3
snapshot. Its immutable OPEN-at-zero record and complete initial RAM snapshot
were captured separately from the active copy. All 369 prior artifacts and
readonly OPEN/READ/CLOSE records and earlier X/Y RAM histories were preserved.
Automatic NORMAL0 recovery passed. SEEK188 (MD193/AP194) was then verified
in D577: only the active cursor changed to 64, with an immutable SEEK record
retained. The full 10456-byte RAM copy and OPEN187 at zero remained unchanged,
along with all 372 prior artifacts and earlier X/Y histories. Automatic
NORMAL0 recovery passed. GETSIZE189 (MD194/AP195) was then verified in D580:
it returned 10456 without changing cursor 64 or either RAM copy, retaining
OPEN187 at zero and SEEK188 at 64. All 374 prior artifacts and earlier
histories were preserved, and automatic NORMAL0 recovery passed. The current
formal result is `FAIL_UNHANDLED_FS_FRAGMENT_AFTER_189_REPLIES_CONSUMED`.
The next captured packet is WRITE190's first fragment (MD195), 3476 bytes,
declaring a 5196-byte write. D583 separately consumed that first fragment
without a filesystem reply and captured its 1784-byte continuation, retaining
all 376 prior artifacts and unchanged RAM. Both fragments carry MD195.
Original DZC3 reassembly produces a 5240-byte logical request with the final
header size word 1784; its 5196-byte payload has no transport padding. D584
qualified the original parser and dispatcher offline: 259 payload variants
and 777 dispatcher cases produce status zero/count 5196 and cursor 5260.
D586 then consumed the actual WRITE190 reply after both incoming fragments
were checked. Only the current LTEK_022 RAM copy changed at bytes 64–5259,
its cursor advanced to 5260, and the complete 10456-byte immutable WRITE
snapshot was captured. OPEN187 at zero, SEEK188/GETSIZE189 at 64 and all
376 prior artifacts and earlier X/Y histories were retained. Automatic
NORMAL0 recovery passed. Its formal result was
`FAIL_UNHANDLED_FS_AFTER_190_REPLIES_CONSUMED`. D589 then consumed CLOSE191
(MD196/AP197), closing the current handle at cursor 5260. Its complete
10456-byte closed RAM capture equals the same cycle's WRITE190 capture;
OPEN187, SEEK188, GETSIZE189, WRITE190 and 380 prior artifacts were retained.
Before device execution, 399 host checks passed and an independent audit
rejected 10531 corruptions of the CLOSE request, reply, complete RAM and
committed state. Automatic recovery entered charging mode 8; separate qualified
recovery restored NORMAL0 while preserving the failed initial result. The next
request, readonly compound192 for LTEM_001, reads 2540 bytes at offset 64
from a 5144-byte file and closes at cursor 2604. Original DZC3 behavior was
qualified offline in D590. D592 then consumed the actual reply and captured
the exact 2540-byte archived slice, closing at cursor 2604. Its 2616-byte
frame crossed the TX ring boundary: 928 bytes at the end and 1688 at the
start, with TX advancing from 19552 to 1688. Complete readback of both spans
was checked before ACK using a 256-byte stack buffer. All 401 host checks
passed before execution, and 383 prior artifacts were preserved in the
verified cycle. Automatic NORMAL0 recovery passed. The next packet is
readonly OPEN193 for LTEM_001 (MD198/AP199); original DZC3 behavior was
qualified offline in D593. D595 then consumed the actual OPEN reply,
retaining an immutable OPEN record at cursor zero and all 385 prior artifacts.
All 403 host checks passed before execution, and automatic NORMAL0 recovery
passed. The next request is READ194 (MD199/AP200), reading the first 64 bytes
from LTEM_001. Original DZC3 behavior was qualified offline in D596.
D598 consumed the READ194 reply and advanced only the active readonly
handle to cursor 64, preserving OPEN193 at zero and the immutable READ64
record. All 405 host checks passed before execution, and all 387 prior
artifacts and automatic NORMAL0 recovery passed verification. The next
request is CLOSE195 (MD200/AP201), closing the readonly handle at cursor
64. Original DZC3 behavior was qualified offline in D599. D601 then
consumed the CLOSE195 reply, closing the active readonly handle at cursor
64 while preserving immutable OPEN193 and READ194 records. All 407 host
checks passed before execution, all 389 prior artifacts were retained, and
automatic NORMAL0 recovery passed. The next packet is private OPEN196
for LTEM_001 (MD201/AP202), flags `0x20010400`, creating a fresh 5144-byte
RAM copy at cursor zero. Original DZC3 behavior was qualified offline in
D602; live OPEN196 handling remains unverified. D581 had qualified
the original first-fragment receive block before the D583 capture. D580's
initial verifier rejected the new first-fragment checkpoint; its diagnostic
is retained. Read-only verification then passed with the exact packet hash,
header and ring positions checked. Before D555, all 20 LK tag payloads,
names, offsets and sizes were independently confirmed unchanged after the
separate recovery; 472 unused bytes differed and no used bytes changed.
The fresh preflight was bound to that exact qualified hash. The initial D549 verifier
rejected this new first-fragment checkpoint because it only supported the
earlier X fragment; its diagnostic is retained. Verification then passed
with the exact D549 packet hash, header and ring positions checked. After the separate mode-8 recovery,
all 20 LK tag payloads, names, offsets and sizes were independently confirmed
unchanged; only 220 unused bytes differed, and the next preflight remained
bound to the exact newly qualified raw hash.

D511 did not pass independent verification because its FS169 RAM capture
endpoint was missing. Its original failed archive is retained. The preflight
capture audit now also requires every data and wire endpoint declared by the
qualified policies, and an ownership audit rejects contradictory handler
constraints before execution. D513 uses the corrected capture registration.

D503 did not pass independent verification because a duplicate debugfs
capture index produced an empty FS167 request file. Its original failed
evidence is retained. D505 uses a corrected module and additionally checks
that all debugfs capture slots are assigned once before device execution
(405 slots in D601), including the named request, response and data mappings.

FS174 integration initially failed its offline full-prefix test because a numeric
substitution changed the historical FS41 cursor along with the new ring pointer.
The historical cursor was restored before device execution; the corrected full
suite passed all 356 checks. No device cycle used the rejected candidate.

D531 initially failed the independent verifier because it required an
unfragmented next request and a numerically increasing RX pointer. The original
archive and error log were retained. A separate strict check of the exact
unhandled first fragment and wrapped RX position verified FS175 and recovery
without accepting FS176 or HS2. Offline execution of the original DZC3 receive
block confirmed that the first fragment is buffered pending continuation;
filesystem dispatch was not executed. The complete WRITE has since passed offline qualification in D535;
D537 subsequently verified its live execution and NORMAL0 recovery.

SIM, calls and mobile data remain unavailable. The harness does not write
persistent NVRAM. This repository does not include the device-specific evidence
or the experimental CCCI harness.

## Artifact identity

| Artifact | SHA-256 |
|---|---|
| Tested v142 BOOT | `aa375adf2cfe1852a6422b1cb7c34c17f27e64e82f7980e9ea6c8c2c5071188c` |
| Kernel configuration | `aff2b1c335d2c500ef534cb15e0a39daad272005814e82406bcc2546990a653f` |
| Module.symvers | `da9cee6327b6ea26b1db3f238736825b08620ba7253b40e6f12f73392dfd5f6d` |
| Built NFC DTB | `3a87ca9785d91db1bd079ded9cc9833e31aaf927afc2a1e8f4542ef50e5c05a6` |

The configuration and exported symbols are unchanged from the previous tested
kernel. Existing device-tree values were preserved except for USB `dr_mode`;
149 changed phandle references were compared by their resolved targets.
The carried kexec change limits the CFI exemption to the copied assembly
relocation entry. A new end-to-end kexec test was not part of this OTG update.
