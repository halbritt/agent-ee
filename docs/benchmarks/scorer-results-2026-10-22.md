# Telemetry-pod scorer benchmark

**PyPy completed the same workload in 70.18 seconds versus CPython’s 95.35: 26.4% less time.**

One test, four implementations. Each ran four times on the same isolated 4-vCPU AMD EPYC 7B13 Spot VM, using the same frozen telemetry-pod board, netlist, models and search budget. Execution order rotated each round.

| Implementation | Run 1 | Run 2 | Run 3 | Run 4 | Mean | Speedup vs CPython |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Python (CPython) | 95.85 s | 94.76 s | 95.63 s | 95.15 s | 95.35 s | 1.000× |
| C (original kernel) | 95.08 s | 94.82 s | 95.91 s | 95.24 s | 95.26 s | 1.001× |
| Rust | 94.71 s | 94.10 s | 96.00 s | 94.36 s | 94.79 s | 1.006× |
| Python (PyPy) | 70.11 s | 70.00 s | 70.70 s | 69.91 s | 70.18 s | 1.359× |

C and Rust changed mean total time by less than 1%.

Times cover the full placement call, including planner execution, board writing and native checks. Library loading and PyPy startup are included. C and Rust replace the scorer under CPython; PyPy runs the whole pure Python planner, with KiCad operations remaining in CPython. C uses the original kernel with a fast adapter; full-output parity comparison runs outside the timer.

All 16 runs produced byte-identical boards and matching normalized placement plans and validation findings. Each placed 309 components using 4,277 search steps. No fallback occurred. Existing board defects remained unchanged.

Measured October 22, 2026. Source: `1ce28a5c2332`. All 24 preflight tests passed. The Spot VM and boot disk were deleted after the evidence was retrieved and verified.
