# agent-ee: Python, Rust and PyPy performance

**PyPy cut the telemetry-pod benchmark from 95.5 to 69.9 seconds: 27% less time.** Rust accelerated dense scoring, but made no meaningful difference to the full run.

## Full telemetry-pod run

Same 309-component board and search budget. Includes planning, board writing and native checks; PyPy startup is included. Mean of two runs, with run order reversed.

| Experiment | CPython | Rust | PyPy | Time saved |
| --- | ---: | ---: | ---: | ---: |
| Rust comparison | 94.29 s | 94.06 s | — | 0.25% |
| PyPy comparison | 95.53 s | — | 69.89 s | **26.84%** |

Each experiment used a separate 4-vCPU AMD EPYC 7B13 Spot VM. The CPython column gives that experiment's baseline. PyPy runs the whole planner; KiCad stays in CPython.

## Scorer performance on telemetry geometry

Each timing covers 95,904 candidate scores. Lower is better. “Fixed” uses fixed components as peers; “dense” uses every other component in a hypothetical placement.

**Original Python versus Rust benchmark**

| Peer state | CPython | Rust scalar | Rust batch |
| --- | ---: | ---: | ---: |
| Fixed | 91.640 ms | 164.747 ms | 88.192 ms |
| Dense | 489.551 ms | 191.354 ms | 112.841 ms |

Rust scalar—the interface used by placement—was **1.80x slower** with fixed peers and **2.56x faster** with dense peers.

**Later PyPy benchmark**

| Peer state | CPython | Rust batch | PyPy | PyPy speedup over CPython |
| --- | ---: | ---: | ---: | ---: |
| Fixed | 91.209 ms | 89.692 ms | **18.281 ms** | **4.99x** |
| Dense | 481.182 ms | 115.538 ms | **57.777 ms** | **8.33x** |

These are medians of five measurements after warmup. Scorer timings exclude interpreter startup and snapshot construction; all scores matched exactly.

## Original Python versus Rust packing benchmarks

Complete `pack()` attempts on five test fixtures. These ran on a busy Intel i5-11400F, so compare Python and Rust within each row. The larger pod fixtures are synthetic.

| Fixture | CPython | Rust | Rust speedup |
| --- | ---: | ---: | ---: |
| search-promotion | 2.169 ms | 2.426 ms | 0.89x |
| search-anchor | 2.334 ms | 2.848 ms | 0.82x |
| cluster-three-cap | 117.543 ms | 115.056 ms | 1.02x |
| 90-component pod fixture | 1,851.542 ms | 918.723 ms | **2.02x** |
| 112-component pod fixture | 4,104.536 ms | 1,512.478 ms | **2.71x** |

Median of five runs after one warmup. Below 1x means slower. All results matched; `cluster-three-cap` exhausted its budget in both versions. PyPy was not tested on these fixtures.

## Tradeoffs and limits

- PyPy's first full-run pair recorded child-process peak RSS of **1.79 GiB versus 0.90 GiB** for CPython. Fresh-process scorer jobs took 1.38–1.42 s under PyPy, versus 0.52–0.92 s under CPython.
- Full-run board bytes matched exactly. Existing board defects remained. These timings use the retained telemetry board; the current input refuses placement before scoring because fixed C42 is outside the board.
- PyPy remains opt-in; the Python 3.12 build is beta.

Measured October 22, 2026. [Full timing tables and measurement details](https://pastebin.harm.org/p/y7vmzak35ps56g99) include individual runs, fixture scorer replay, checked mode and fresh-process costs.
