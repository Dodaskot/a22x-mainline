# SIM1 investigation and stock RF preparation, 2026-11-03

The latest independently audited modem run still confirms **FS1–1446 and
HS2**. The subsequent READY stage fails, and **SIM1 service is not working**.
Network registration, calls and mobile data remain unavailable. This report
updates the [original HS2 milestone](MODEM-VALIDATION-20261024.md) with the
later RF, exception and transport investigation.

These results belong to the bounded development harness. They do not add
cellular support to the published v143 boot image. The new stock Android
comparison described below is prepared and tested offline; no capture from
that controller has been obtained yet.

## Latest hardware result

The 2026-11-03 run passed the full FS1–1446 prefix, HS2 control ACK,
current-RAM checks and independent LTEH table binding. READY then returned
`EIO` after 838 additional responses. The total of 2,284 published responses
is **not** a newly qualified filesystem prefix: FS1446 remains the latest
confirmed checkpoint.

The captured fault is `digrf_on_irq.c:107`, with parameters `[0, 0, 256]`
and diagnostic PC `0x9041daca`. Earlier ASSERT342 and SerDes ASSERT448
captures remain separate observations. The change of assertion does not
establish which experimental change caused it.

The full post-run cleanup audit passed, including restoration, module
unload, ownership release and AP UI/USB/CPU checks. That result establishes
the checked cleanup sequence, not SIM readiness or protocol quiescence.

## RF power and clock observations

- An MT6360 BUCK2 capture contains 267 regulator samples, including 34 in
  the READY-to-fault interval. They report 1.3 V, enabled, normal mode.
  These are software observations; the voltage waveform at the RFIC and
  equivalence to a stock runtime phase have not been measured.
- The bounded GPIO170 and combined GPIO170/XO force-on experiments still
  ended with ASSERT342. The combined run reached READY and passed its
  FS1446/HS2 checks. Physical 26 MHz output was not measured, so this does
  not prove the complete downstream clock path.
- Source-derived stock initialization masks were compared with 110 saved
  Linux read passes per register across three captures. Fourteen of 22
  registers match the owned bits; eight OP_EN/OP_CFG fields differ across
  VXO22, VRF12, VA09 and VA12. The corresponding digital enable/stable
  monitors were on. Those fields control low-power behavior; their
  differences alone do not establish missing power or justify a voltage
  change. Stock initialization code is not an observed stock runtime.

The schematic helped distinguish RF supply and clock branches, but the
actual board/provider binding remains necessary. Net names and generic
regulator tables are insufficient voltage-setting instructions.

## Exception and PC Monitor analysis

The producer of the saved seven-word `stack_dump` was located in the exact
firmware. It scans stack addresses and packs opcode-qualified return-address
candidates. The field is therefore not a proven seven-frame unwind. Several
call boundaries and a shared outer DigRF interrupt handler are identified,
but the raw DigRF reply and actual callback index have not been recovered.

A hardware PC Monitor capture completed two identical read passes after
STOP. In that capture, STOP finished **21.042154 ms after the passive fault
capture ended**. Equality of the stopped data does not prove retention of
pre-assertion branch history. Ring overwrite remains a possibility, not a
demonstrated event; the exact assertion time is unknown. Source analysis
qualified parts of the record layout and cursor/copy algorithm, while FRC
units and all TAG fields remain unresolved.

For the latest ASSERT107, the source path reaches its diagnostic branch
before the seven-bit callback loop in that interrupt invocation. It does
not demonstrate execution of the later mode1 read/ACK path. A previous IRQ,
stale latch or ACK-order race remains a hypothesis. DigRF parameters and AP
CCIF pending bits belong to different register spaces.

## Post-fault DHL test

A bounded LOOP_BACK48 request was published through the source-qualified
CCB path and its notification completed. All eight polls found no matching
reply and no observed movement of the checked MD-owned cursors. This is
limited to the sampling times; it does not prove that no intermediate
activity occurred or that the RF read failed.

The passive ASSERT107 capture ended **0.926769 ms before** the DHL operation
began. This is a capture-end-to-operation interval, not an exact timestamp
of the assertion instruction. The later request cannot explain the already
recorded fault. The native cleanup completed, but tail restoration does
not establish cancellation of the request or protocol quiescence.

Earlier host replays used synthetic reply sinks. They qualified the path
to a handler and its descriptor model, not a real firmware nonce reply.
The hardware no-reply result is reported separately from those host tests.

## Stock RF comparison controller v3r2

The new observer reads 42 fixed PMIC fields twice: **84 reads**, with a
one-second native budget and no PMIC register writes. It covers selectors,
raw calibration/high-voltage fields, low-power controls and digital
monitors. This adds information that the earlier selector-only baseline
does not supply. Raw calibration fields are not converted into an asserted
physical voltage without a qualified conversion.

The controller requires the exact stock kernel/BOOT, one boot epoch and
per-load token, SIM1 LOADED, and an independent `md1:4` READY indication
before and after the observation. Public readiness alone does not prove
that stock and Linux are in an identical internal RF phase.

Two host-side issues were corrected before any Android execution:

1. A transport timeout used to reject the capture without independently
   proving remote cleanup. The current controller makes one bounded cleanup
   attempt, checks boot/token ownership, and reports cleanup as VERIFIED or
   UNKNOWN separately from the RF result. Shared locking and a per-run RAM
   cancellation marker prevent a delayed stage/observer from recreating a
   cleaned run. A timed-out capture remains unqualified even when cleanup
   succeeds.
2. The shell accepted only comma-separated `LOADED,*`, while Python also
   accepted bare `LOADED`. The mismatch was reproduced offline before
   `insmod`. v3r2 accepts `LOADED|LOADED,*` in the shell and retains the
   separate MD1 READY gate. The actual stock property format has not yet
   been observed.

All **39 host tests passed**: 11 decoder, 12 phase binder and 16 lifecycle
cases. Lifecycle fixtures now execute the production shell phase with
modeled device inputs. They cover bare LOADED, rejection without MD1 READY,
rejection when only the second SIM is loaded, and the existing timeout,
ownership and cleanup cases. Mocked module operations are not hardware
loads. The native module and read core are unchanged from v3r1; the earlier
27-symbol ABI audit and 261 O2 / 261 ASan/UBSan core cases were inherited by
exact hash, not rerun for the one-line shell fix.

The handset's current Arch userdata backup was verified before uploading
the stock observer BOOT. At the last recorded transition check, the phone
was in Download mode; Android recovery/setup and owner-authorized ADB were
still pending. A successful BOOT upload is not an Android boot or a stock
RF capture. The next measurement is the prepared stock baseline after that
setup, followed by comparison of the observed low-power/calibration state
with the retained Linux captures.

## Retained evidence identifiers

These SHA-256 values identify the local evidence used for this report.
The raw device captures, NVRAM, userdata backup, firmware and development
harness remain outside this public tree. This report is not a standalone
reproduction package.

| Evidence | SHA-256 |
|---|---|
| Latest FS1446/HS2/ASSERT107/DHL runtime archive | `0d61dd10a439de4c324502c88c3ef1d36389dad5ce0a49ce931f81ad7e13ea38` |
| Independent full runtime audit result | `5d125386faad7d5b29ab7ed432f80eb62119f5094093a4462b69fd5281a37dd9` |
| Saved Linux RF low-power comparison manifest | `4558f3e1d19994e3dc69a7cc35f80b5e5064b39e2a9c6c34b2a8d9b9dec54ee5` |
| Exception stack producer proof manifest | `379b4f9a5dfe69f56e1667f3fd4caa594203e3f1daa9bc80eeee1d329ae5a23b` |
| PC Monitor format analysis manifest | `1e934fcf45c39444a7ffd82e1afdd521e901de8e2223c7709f7b6e297867ace3` |
| Stock RF v3r2 preparation manifest | `2b3d4ff17068c1f885695581a659deac1759b3a290a40fdf1705e7e10ad108b5` |
| Unchanged stock RF native module | `db8dd7a1c64d3f12293b4069dc9cf577bdb2eb287c51c6b010b9c582bff24300` |
