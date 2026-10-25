# agent-ee

**agent-ee turns a captured electronics design into a verified, native KiCad
project and a fabrication package, with tool evidence at every stage.** It is
being built as the engine of an AI-driven PCB generator: an agent and an
engineer settle requirements, parts and circuit intent, and agent-ee produces
the schematic, places and routes the board, runs the checks, and exports
Gerbers, drills, BOM and CPL. The same checks and independent-review machinery
also review and repair existing KiCad projects, which is where the project
started.

The owner's direction since 2026-10-17 is the generator
([decisions](docs/decisions.md)); the review workflow is one of its stages,
not the product. This README describes what the generator can do today and
what it cannot. The ticket-by-ticket history of implemented capabilities is in
the [capability log](docs/capability-log.md).

## Status at a glance (2026-10-25)

What works through the normal entrypoint, `agent-ee build run`:

| Stage | What runs | Demonstrated on |
| --- | --- | --- |
| Requirements | Validates an authored spec, its byte-located requirement record and the requirement-to-check map; derives mechanical obligations from the board envelope | 17-part acquisition board |
| Parts | Binds authored parts, captured datasheets, symbols and footprints to the authored circuit intent | acquisition board |
| Scaffold | Creates the native project, board outline, stackup, rules and fixed connectors | acquisition board |
| Schematic | Emits the native schematic from the circuit intent, runs ERC and forward-annotates footprints | acquisition board; 130-part emitter benchmark |
| Placement and routing | Rust placement search over declared groups, fixed poses, locality and planes; routing by the pinned [kicad-route](docs/routing-adapter.md); native DRC and locality checks on the saved board | acquisition board; esp_prog (placement and routing only) |
| Artwork | Moves silk reference fields off conflicts under a receipt | acquisition board |
| Package | Checks-only review (ERC, DRC, parity, interval, SPICE, DFM, assembly readiness and others as mapped), then Gerber/drill/BOM/CPL export bound to the final board hash | acquisition board |

One real design has completed all seven stages to an accepted package: the
17-part TPS7A20 + ADS1115 acquisition board, on 2026-10-24
([acceptance map](docs/system-acceptance.md)). The run and its sealed copy are
retained outside the tracked tree. No physical test or supplier preview has
been performed on it. The esp_prog test board completes placement and routing
through `agent-ee place run` with zero native DRC errors and zero open
connections under its project rules; 258 warnings (silkscreen, text height,
library mismatches) remain, so it is not a manufacturing-ready result.

What the normal entrypoint does not do today:

- **It does not synthesize a design from prose.** The spec, the requirement
  record, the requirement-to-check map, the parts manifest, the circuit
  connectivity, the schematic layout, the board envelope, the placement intent,
  the functional groups and the plane plan are JSON and Markdown inputs that an
  agent or engineer authors before the build. The build validates and binds
  them; it does not choose parts, draw circuits or decide placement intent.
- **Functional-group and proximity intent are soft.** They steer the placement
  search as cost terms. Final acceptance checks declared pin locality, fixed
  connector edges, planes and native DRC; it does not check that every part
  landed in its group.
- **Engineering verification is as good as the authored map.** Only checks the
  map names are run; the mandatory set is ERC, DRC, netlist, geometry, parts
  membership, placement, envelope, fab profile, assembly readiness and package.
- **The build is not covered by an automated end-to-end test.** Tests exercise
  stages and diagnostics; the only run that reached an accepted package is the
  retained acquisition run.
- **Independent AI review is not part of the build.** The package stage runs
  checks only. The review and campaign commands provide reviewers and
  adjudication for a captured candidate separately.

## How a build works

```text
spec.md + spec-record.json + requirement-map.json      (authored)
parts.json + circuit.json + layout.json + ratings.json (authored, captured sources)
envelope.json + fab-profile.json                       (authored)
placement.json + groups.json + planes.json             (authored)
        |
        v
agent-ee build run build.json --out RUN
  01 requirements  -> scope with mapped checks and accounting
  02 parts         -> bound connectivity intent and ratings receipt
  03 scaffold      -> empty native project with outline, stackup, rules, fixed parts
  04 schematic     -> .kicad_sch, ERC, netlist, footprints on the board
  05 placement-routing -> Rust placement search, kicad-route routing, saved-board DRC
  06 artwork       -> silk field relocation under receipt
  07 package       -> checks-only review, Gerbers/drills/BOM/CPL bound to board hash
```

Each stage writes a hash-bound candidate directory and a receipt. Resumption
re-checks source and runtime identity. A `CANCEL` file stops before the next
stage. `--through STAGE` stops at a checkpoint for diagnosis. See the
[build workflow](docs/build-workflow.md) for the plan format and
[placement job](docs/placement-job.md) for search, router and budget settings.

## Try it

The demonstrated environment is Linux with KiCad 10.0.7 (including the
`pcbnew` Python bindings on the system interpreter), ngspice, Bubblewrap and
the Rust placement engine built from `crates/placement-engine`. The CLI has no
third-party Python dependencies. See [installation](docs/installation.md);
the installer links the source launcher `bin/agent-ee`, which uses
`/usr/bin/python3`. A launcher installed another way, for example as a tool
in a separate interpreter, will not find `pcbnew` and the build will fail
before its first stage.

```sh
./scripts/install.sh
agent-ee doctor
```

A review of the included RC filter runs the native checks without model calls
and takes about twenty seconds:

```sh
agent-ee review fixtures/closed-loop/control.scope.json \
  --out build/first-checks --checks-only
agent-ee inspect build/first-checks
agent-ee verify build/first-checks
```

The review command exits 1 here by design: a checks-only run leaves the
independent review unfinished even when every check passes.

A build needs an authored input bundle and a plan:

```json
{
  "schema": "agent-ee.build-plan/1",
  "source": "inputs",
  "requirements": {"map": "requirement-map.json", "record": "spec-record.json", "spec": "spec.md"},
  "parts": {"manifest": "parts.json", "circuit": "circuit.json", "layout": "layout.json"},
  "envelope": "envelope.json",
  "placement": {
    "intent": "placement.json",
    "groups": "groups.json",
    "budget_s": 1200,
    "search": {"seeds": [{"seed": 0, "start": "clustered"}], "steps_per_seed": 100000, "shortlist": 1},
    "router": {"checkout": "/opt/kicad-route"}
  },
  "verification_timeout_s": 3600
}
```

```sh
agent-ee build run build.json --out build/my-board
agent-ee build run build.json --out build/my-board --resume
agent-ee build review build/my-board --out build/my-board-review
```

`build review` independently re-checks the last completed candidate and
always reports `accepted: false`; read its `report.json`.

## The review workflow

For an existing KiCad project, `agent-ee review SCOPE` captures the design and
its requirements, runs the declared checks, asks independent circuit and
physical-design reviewers to examine the same evidence, and validates a central
adjudication of their findings. `agent-ee campaign` gives one implementation
agent a disposable copy, submits every change to fresh checks and review, and
stops on satisfied criteria, a stall or an exhausted budget. The
[recorded demonstration](evidence/README.md) shows a wrong resistor value that
passed ERC and DRC, was caught by simulation and review, and was repaired under
fresh review. See the [operator guide](docs/operator-guide.md),
[campaign guide](docs/campaign-guide.md) and
[scope reference](docs/scope-reference.md).

| Review outcome | Meaning |
| --- | --- |
| `criteria_satisfied` | Required checks and reviews completed with no accepted defects or unresolved findings. |
| `defects_or_unresolved` | Review completed with defects or open questions. |
| `incomplete` | A tool, reviewer, evidence gate, adjudication or work budget left required work unfinished. |

## Qualification designs

| Design | Role | State |
| --- | --- | --- |
| RC low-pass filter (`fixtures/rc`, `fixtures/closed-loop`) | Review and campaign demonstration; rung-1 generation driver | Reviewed, repaired and regenerated with evidence |
| Acquisition board (17 parts, TPS7A20, ADS1115, three JST connectors) | First complete build through all seven stages | Accepted package 2026-10-24; no physical validation |
| esp_prog (from KiCadRoutingTools) | Owner-named tooling test board for placement and routing | Routes clean of errors under project rules; warnings undisposed |
| Telemetry pod (fome-mc) | Target product board | Held by the owner; no attempts until tooling is accepted |
| Bounded routing articles (pairs, impedance, stripline, buses, length and delay matching, planes) | Feature qualification against stock KiCadRoutingTools on small synthetic boards | See the [acceptance map](docs/system-acceptance.md) |

## Repository map

| Path | Contents |
| --- | --- |
| `src/agent_ee/` | The CLI and all stages, checks and adapters (Python, standard library only) |
| `crates/placement-engine/` | Rust placement search engine (`agent-ee-placement`) |
| `tests/` | Unit and native integration tests; native-dependent tests skip without the toolchain, or fail under `AGENT_EE_REQUIRE_TOOLCHAIN=1` |
| `fixtures/` | RC, closed-loop, stage and fome-mc fixtures with provenance |
| `docs/` | The [manual](docs/README.md), [acceptance map](docs/system-acceptance.md), [capability log](docs/capability-log.md), qualification records and benchmarks |
| `evidence/` | Retained demonstration evidence; most run files live in the owner's private store ([relocation](docs/reproducing-evidence.md)) |
| `docker/`, `Dockerfile`, `action.yml` | Pinned toolchain image and a checks-only GitHub Action |
| `skills/agent-ee/` | Agent discovery skill for the CLI |

Development:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
python3 scripts/check_docs.py
```

See the [maintainer guide](docs/maintainer-guide.md), [security](docs/security.md)
and the [enhancement roadmap](docs/enhancement-roadmap.md).

## License

agent-ee is licensed under the [MIT License](LICENSE), declared in the package
metadata. The license covers material written for agent-ee, outputs of its runs
on that material, the RC fixture copied from FOME-MC and the logic in
`src/agent_ee/checks.py` adapted from FOME-MC. The owner approved MIT for these
on 2026-10-22 ([decision](docs/decisions.md#mit-license-approved-agentee-14-2026-10-22),
relayed by the coordinator). The RC fixture embeds stock KiCad library data,
which keeps its own license.

The MIT grant does not cover files that come from other projects. These include
donor ECU design files and their libraries, stock KiCad libraries
inside the evidence archives, datasheets and fetched web pages. The
[file-origin inventory](docs/file-origins.json) classifies every tracked file by
origin, upstream license and publication status, and
[third-party origins](docs/third-party-origins.md) summarizes it for review.
Publication rights for those files are unresolved, and each origin needs an
owner decision. The public repository already contains many of them. That does
not establish a right to publish them.
The fome-mc block fixtures under `fixtures/fome-mc/` have a separate recorded
owner decision (2026-10-18). The owner holds the rights to
fome-mc. Those files are still outside the MIT grant. See their
[licence notice](fixtures/fome-mc/LICENSE-NOTICE.md).
