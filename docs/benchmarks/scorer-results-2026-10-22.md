# Telemetry-pod scorer benchmark

**Rust completed the full workload in 56.45 seconds on average: 1.70× faster than CPython and 1.24× faster than PyPy.**

One workload, four implementations, four measured runs each. All used the same frozen telemetry-pod board, netlist, models, constraints and search budgets on one isolated 4-vCPU AMD EPYC 7B13 Spot VM. Execution order rotated each round; every run started a fresh process.

| Implementation | Run 1 | Run 2 | Run 3 | Run 4 | Mean | Speedup vs CPython |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Python (CPython) | 96.26 s | 95.67 s | 95.68 s | 95.95 s | 95.89 s | 1.000× |
| CPython + original C scorer | 97.01 s | 96.17 s | 95.74 s | 95.28 s | 96.05 s | 0.998× |
| Greenfield Rust planner | 55.97 s | 56.10 s | 57.98 s | 55.74 s | 56.45 s | 1.699× |
| Python (PyPy) | 70.29 s | 70.34 s | 70.13 s | 69.83 s | 70.15 s | 1.367× |

Times cover the complete placement call: capture, planning, board writing and native validation. Backend initialization and Rust/PyPy process startup and communication are included. Rust implements the complete pure planner independently, including geometry, block arrangement, search, ranking and grading. PyPy runs the original Python planner. The C row uses the original compiled scorer under CPython. KiCad capture, board application and validation are common to all four.

All 16 runs produced byte-identical boards and matching complete plans, apart from recorded elapsed time. Each returned 309 footprint placements using 4,277 search steps. Validation findings also matched; no fallback occurred. The candidate's 9 existing silkscreen conflicts remain.

Measured October 22, 2026 (PDT; October 23 UTC). Source: `0995743eb6a1546f31bf47048043aaeb6efc69cf`. The interrupted attempt is preserved separately and excluded from this table. Four observations on this board establish this result; they do not establish performance on every board. The Spot VM and boot disk were deleted after evidence retrieval.
