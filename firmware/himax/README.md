# Independent HX83112F components

This directory is an unfinished open-firmware implementation for the A22X
Himax controller. **The phone still requires its original touch firmware.**
These sources must not be packaged as a working replacement merely because
they compile or pass CPU simulation.

| Component | Implemented boundary | Evidence |
| --- | --- | --- |
| `report.c` | Ten-slot packet serialization and release report |129 original/compiled-C vendor-simulator cases;2000 narrow-interpreter cases |
| `signal.c` | Mapped signed deltas, weighted moments, centroid scaling | Host bounds/wrap tests;280 original/compiled-C centroid cases |
| `raw-engine.c` | Eight register writes plus bounded completion polling |64 CPU trace comparisons with explicitly scripted completion |
| `seed.c` | Ordinary threshold classification and peak selection |67 matrix and64 peak original/compiled-C cases |
| `diagnostic/` | Finite standalone CPU computation and vector branches | Counter/checksum, GIE/IVB and15 vector branches in CPU simulator |

The thresholds, raw values and register callbacks are supplied by the caller.
No stock firmware bytes or electrode calibration tables are embedded. Details,
mode restrictions and reproducible tests are in the linked documents:

- [Report format](../../docs/HIMAX-REPORT-COMPONENT.md)
- [Signal computations](../../docs/HIMAX-SIGNAL-COMPONENTS.md)
- [Raw-engine transaction](../../docs/HIMAX-RAW-ENGINE.md)
- [Thresholds and peaks](../../docs/HIMAX-SEED-COMPONENT.md)
- [CPU diagnostic](diagnostic/README.md)

A complete replacement still needs peripheral initialization and acquisition,
baseline adaptation, gradient-constrained cluster growth, edge policies,
contact tracking, operating-mode/power transitions and reliable host recovery.
CPU comparisons do not simulate the physical sensing hardware. The host Linux
driver is already open source; these files address the separate controller
program.
