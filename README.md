# agent-ee

**agent-ee turns a captured electronics design into a verified, native KiCad
project and a fabrication package, with tool evidence at every stage.** It is
being built as the engine of an AI-driven PCB generator: an agent and an
engineer settle requirements, parts and circuit intent, and agent-ee produces
the schematic, places and routes the board, runs the checks, and exports
Gerbers, drills, BOM and CPL. The same checks and independent-review machinery
also review and repair existing KiCad projects, which is where the project
started.

For explicit board and circuit decisions, use [design capture and compile](docs/design-compile.md), then [build run](docs/build-workflow.md). For end-user and operator guides, see the [Quickstart Guide](docs/quickstart.md), [Operator Runbook](docs/operator-runbook.md), [Architecture & Pipeline Guide](docs/architecture-pipeline.md), [Benchmarking Guide](docs/benchmarking-guide.md), and [Troubleshooting & FAQ](docs/troubleshooting-faq.md). Core terminology is documented in [GLOSSARY.md](GLOSSARY.md).

The owner's direction since 2026-10-17 is the generator
([decisions](docs/decisions.md)); the review workflow is one of its stages,
not the product. This README describes what the generator can do today and
what it cannot. The ticket-by-ticket history of implemented capabilities up to
2026-10-25 is in the [capability log](docs/capability-log.md), a frozen record.

How its placement and routing compare with stock KiCadRoutingTools on real
boards is measured in the
[matched benchmark](docs/benchmarks/krt-matched-2026-11-09.md). Its newest
"Result in brief" section is the current standing; it does not support a
general claim that agent-ee is better.

## Status at a glance (2026-11-14)

What works through the build entrypoint, `agent-ee build run`, and the synthesis entrypoint, `agent-ee design generate`:

| Stage | What runs | Demonstrated on |
| --- | --- | --- |
| Requirements & Synthesis | `design generate` turns prose requirements (`REQUIREMENTS.md`) into an `agent-ee.design/1` statement with trace citations via a subscription author seat; `build run` validates an authored spec, its byte-located requirement record and requirement-to-check map | `design generate`: led-flasher, load-switch, rs485 (benchmark r5b); `build run`: 17-part acquisition board |
| Parts | Binds authored/selected parts, captured datasheets, symbols and footprints to circuit intent; checks footprint DFM rules | acquisition board; generated r5b designs |
| Scaffold | Creates native project, board outline, stackup, rules and fixed connectors | acquisition board; generated r5b designs |
| Schematic | Emits native schematic from circuit intent, runs ERC and forward-annotates footprints | acquisition board; 130-part emitter benchmark; generated r5b designs |
| Placement and routing | Rust placement search over declared groups, fixed poses, locality and planes; routing by [kicad-route](docs/routing-adapter.md) at the commit each attempt records; native DRC and locality checks on the saved board | acquisition board; generated r5b designs; esp_prog (placement and routing only) |
| Artwork | Moves silk reference fields off conflicts under a receipt; places silkscreen labels | acquisition board; generated r5b designs |
| Package | Checks-only review (ERC, DRC, parity, interval, SPICE, DFM, assembly readiness, copper width), then Gerber/drill/BOM/CPL export bound to final board hash | acquisition board; generated r5b designs |

One real pre-authored design has completed all seven stages to an accepted package: the
17-part TPS7A20 + ADS1115 acquisition board, on 2026-10-24
([acceptance map](docs/system-acceptance.md)). In autonomous requirements-to-board
generation (`agent-ee design generate`), benchmark round r5b (2026-11-04) accepted
three of five boards (`led-flasher`, `load-switch`, `rs485`) from natural-language
requirements briefs; the two unaccepted boards (`current-monitor` and `usb-regulator`)
exposed tool defects (unpaired differential net handling, and pre-existing footprint
NPTH hole clearances) that were subsequently resolved in tooling. Subsequent
engineering reviews of the accepted boards identified design omissions (such as
unterminated bus failsafe bias and unbuffered off-board control inputs), which were
mapped to general check rules rather than board-specific fixes.

How placement and routing compare with stock KiCadRoutingTools on real boards is
measured in the [matched benchmark](docs/benchmarks/krt-matched-2026-11-09.md):
placing and routing on its own, agent-ee delivers 2 of 6 boards clean (and stock
KRT delivers 1 of 6); routing the designer's placement, the two are level.

Current pipeline capabilities and limitations:

- **Prose synthesis is provided by `agent-ee design generate`, not `build run`.**
  `agent-ee build run` requires structured JSON and Markdown inputs authored before
  the run. `agent-ee design generate` accepts prose requirements up to 32 KiB and
  queries an LLM author seat (`codex:gpt-6.1-sol:high` or `agy`) to synthesize
  `agent-ee.design/1` statements with quoted trace citations before compiling and
  building the candidate.
- **Build and routing times depend on search budgets.** Typical placement and
  routing passes require several to tens of minutes depending on board density and
  seed count; claims of "manufacturing-ready in under five minutes" do not describe
  non-trivial boards.
- **Functional-group and proximity intent are soft.** They steer the placement
  search as cost terms. Final acceptance checks declared pin locality, fixed
  connector edges, planes and native DRC; it does not check that every part
  landed in its group.
- **Engineering verification is as good as the authored map.** Only checks the
  map names are run; the mandatory set is ERC, DRC, netlist, geometry, parts
  membership, placement, envelope, fab profile, assembly readiness and package.
- **Automated testing runs on the host.** Host-native tests run via `mise` with
  Bubblewrap sandboxing; the full suite runs nightly on the host. Hosted CI
  validates commit recency rather than running tests or building images. Sample
  tables in documentation (such as the "21/25" PCBSchemaGen summary in the
  benchmarking guide) illustrate schema output formats, not verified measurements.
- **Independent AI review is not part of the build.** The package stage runs
  automated checks only. The review and campaign commands provide reviewers and
  adjudication for a candidate separately.

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
re-checks the plan, the input source and the native tool runtime (KiCad, Python).
A changed agent-ee source does not refuse a resume; the report records it in
`tool_changes`. A `CANCEL` file stops before the next
stage. `--through STAGE` stops at a checkpoint for diagnosis. See the
[build workflow](docs/build-workflow.md) for the plan format and
[placement job](docs/placement-job.md) for search, router and budget settings.

### Core generator commands

| Command | Purpose | Reference |
| --- | --- | --- |
| `agent-ee build run PLAN` | Run the complete 7-stage generative build pipeline | [build-workflow](docs/build-workflow.md) |
| `agent-ee build review RUN` | Independently re-check completed build candidate | [build-workflow](docs/build-workflow.md) |
| `agent-ee place run CONFIG` | Execute standalone placement search and routing | [placement-job](docs/placement-job.md) |
| `agent-ee place metrics BOARD` | Evaluate native placement quality metrics and wirelengths | [placement-job](docs/placement-job.md) |
| `agent-ee parts compile ARGS` | Compile part selections and bind footprint/symbol sources | [parts-stage](docs/parts-stage.md) |
| `agent-ee design compile DESIGN` | Validate and compile typed design statement to build bundle | [design-compile](docs/design-compile.md) |
| `agent-ee design capture DESIGN` | Capture circuit and envelope into design schema | [design-compile](docs/design-compile.md) |
| `agent-ee design generate REQUIREMENTS` | Synthesize design schema statement from engineering requirements document | [design-compile](docs/design-compile.md) |
| `agent-ee design benchmark REQUIREMENTS_DIR` | Run automated benchmark suite across requirements documents | [benchmarking-guide](docs/benchmarking-guide.md) |

## Try it

The demonstrated environment is Linux with a KiCad 10.x release (including the
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
| `TEST_INFRA.md` | [5-Tier E2E test suite infrastructure specification](TEST_INFRA.md) |
| `GLOSSARY.md` | [Domain glossary defining 18 core circuit and placement concepts](GLOSSARY.md) |

Development:

```sh
mise tasks ls                              # the test and check entry points
mise run test:integration                  # the 5-tier end-to-end suite (tests/e2e/run_tests.sh)
mise run test:unit -- test_doctor          # the unit tests for what you changed, by module
mise run check                             # documentation, file origins, conflict markers
```

The whole unit suite takes about two hours and runs nightly; see
[AGENTS.md](AGENTS.md#working-here) for the other tasks and when to use each.

See [TEST_INFRA.md](TEST_INFRA.md) for test tier breakdowns, runner options, and coverage mappings. See the [maintainer guide](docs/maintainer-guide.md), [security](docs/security.md)
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
