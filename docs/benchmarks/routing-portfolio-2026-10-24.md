# Routing portfolio and esp_prog native result

The ordinary placement entrypoint now completes the esp_prog placement/routing
gate. This is a bounded tooling result, not manufacturing acceptance or completion
of the whole-system goal. Live telemetry-pod attempts remain held.

## Integrated correction

Agent-ee `d87edb494ab810061cce1d5a1705f054a2d693b8` consumes kicad-route
`be1f3a831ac275bb0dc5f8b70bfb9a1afa5e9bfa`. Explicit coupled-pair policy now
reaches the router's short-leg decision, and generated tails trim to exact
same-layer pad anchors while protecting input and locked geometry. The
controller tries distinct native-eligible shortlist candidates after routing or
final-check rejection, recording every trial and the chosen routed incumbent.
Resume identity includes controller, router and native-tool identities.
These are shared implementations; no esp_prog reference or pose substitution
implements the repair. Project identity derives from the input filename.

## Native exercise

```sh
ae-ci-exec . -- python3 -m agent_ee.cli place run \
  build/agent545/board-config-portfolio.json --out build/agent545/board-run-portfolio
```

The configuration retains the original native board, project, groups, constraints
and six seeds. Each search receives 100,000 steps within a 1,200-second total
budget. This agent-selected exploration budget differs from the earlier failed
30,000-step experiment and cannot establish performance superiority. Total
observed runtime was 720.816 seconds, including 673.582 seconds of search while
other verification used CPU resources.

Seed 6 had the lowest feasible engine score but introduced two native
`solder_mask_bridge` errors. The native gate rejected it. Seed 11 passed placement
verification and its first routing trial completed; seed 10 was also placement
eligible but was not routed. Thus this full-board run establishes native
placement selection and successful routing, not native multi-trial recovery.
Controller fault injection separately exercises rejection followed by retry.

| Saved-output observation | Result |
| --- | --- |
| Native DRC violation errors / open connections | 0 / 0 |
| Declared connector edges | 3 / 3 pass; edge identity coverage only |
| Native geometry and identity preservation | Preserved |
| Copper length / tracks / vias | 389.4331 mm / 211 / 30 |
| Pair result | Coupled, no pad-net swaps, zero measured gap violations |
| Pair P / N length and skew | 43.433451 / 40.469922 / 2.963529 mm |
| Warnings | 21 library, 3 silk-edge, 49 silk-over-copper, 165 silk-overlap, 20 text-height |
| Geometric via-in-pad sites | 8; manufacturing disposition pending |

No impedance or skew acceptance follows from the pair status. Group metrics,
all-side pin relationships, access/height restrictions, electrical function,
silkscreen and via-in-pad manufacturing policy still require their own checks.
The job retains `accepted:false`, `clean:false`, and no superiority claim.

The final native PCB is `board-run-portfolio/final-a1/board.kicad_pcb`, SHA-256
`dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1`.
The final native DRC report SHA-256 is
`bffb4f4adb4e21675cce31c02c66a5068eefc928293edce1e045d974af798ccf`.
Its selected placement PCB hash is
`8b3e27fc1729a4901c5fc492a847a1764a1b48f517bc71d60c2ae2c52f796715`.

The executor is `agent-ee:exec-7f62229a0122839e`, image ID
`sha256:96ff8b55a6affdfb6e617f183e2b8eefec146f865e4083da17ef16e91802c4ff`.
KiCad is 10.0.7, Python 3.12.3, Rust 1.99.1, owned grid router 0.23.1.
The run loaded the controller before two reporting-only edits: the label for
an alternate routing trial and an explanatory phrase. The exact launch source
is retained in `portfolio-launch-source/placement_job.py`; recomputing the
identity from its bytes matches the run identity
`9a932d5e0cec94acf7c95938f0d0440628ef3c505b209feab5a89def586f188e`.
Current-source tests cover those later edits.

## Verification and previews

Required native/contract invocations passed 39 dependency-pin checks and 39
placement job/guard/refusal checks, with zero skips. Three focused controller
checks pass on the final source. Counts overlap. The new retry test fails on
the previous controller, rejects an edge-invalid intermediate placement, retains
the first route failure, and selects the later passing candidate. This is
controller fault injection, explicitly distinct from native routing evidence.
Cancellation/resume and changed-runtime refusal are also exercised.

KiCad 10.0.7 rendered top and bottom views from a separate preview copy. Only
legacy model paths with exact matching installed basenames were replaced;
native board geometry and the authoritative PCB bytes were unchanged. Five
custom models are unavailable (CON1, CON2, USB1, U2, Y1), so the previews omit
their bodies. The underside visibly retains overlapping silkscreen. Neither
preview establishes assembly or mechanical acceptance.

```sh
kicad-cli pcb render --side top --width 1800 --height 1200 \
  --quality high --background opaque --floor --rotate 325,0,25 \
  --output build/agent545/portfolio-renders/top.png \
  build/agent545/portfolio-renders/project/board.kicad_pcb
```

Use `--side bottom` and the bottom output path for the other view. The initial
negative-angle argument spelling was rejected by the CLI; its log is retained.
The successful commands, model and image hashes, omissions and source hash are
in `portfolio-renders/provenance.json`.

Working evidence is under `build/agent545/`. The durable copy is
`~/.cache/agent-ee-qualification/agent545/routing-portfolio-20261024/`, with
`SHA256SUMS`, integration identity, launch source, exact inputs/configuration,
full native run, tests and previews. Replay in a fresh output directory after
restoring captured inputs. No manufacturing package is accepted by this result.

The next whole-system action is the missing requirements-to-parts/circuit
producer and public resumable build workflow. The earlier matched stock/owned
pair failures remain the current comparison evidence; this new owned-engine
success has not yet been compared with stock on matched inputs.
