# Declaring and extending review coverage

The executable examples are `fixtures/rc/control.scope.json`,
`fixtures/rc/seeded.scope.json`, `scopes/donor.json`, and
`scopes/donor-case-alias.json`. The last two are project-specific input/scope
choices; the orchestration and checks contain no FOME-MC requirements.

## Existing checks

| Kind | Criterion and evidence |
| --- | --- |
| `measurements` | Pinned installed producer, captured parser, candidate-bound scalar metrics and coordinator bounds; requires independently qualified models and parser semantics. |
| `coupled_margins` | Same-condition results from distinct declared domains; conjunctive limits, retaining calibrated source intervals and source facts. |
| `erc` | Native all-severity ERC plus complete hierarchy coverage. Missing child files prevent a clean result. |
| `drc` | Native all-severity DRC, saved fill, and schematic parity. Reports retain exclusions and native items. A violation is distinct from execution failure. |
| `netlist` | Complete-hierarchy native XML export, with derived connectivity, DNP, native symbol identity, and sheet/UUID provenance. Informational rather than functional acceptance. |
| `geometry` | Saved-board pcbnew extraction. `params.min_track_width_mm` retains its historical straight-segment meaning; `params.exclude_nets` records explicit exemptions. Opt-in `params.criterion: "copper-width/1"` covers straight tracks and native arcs with explicit coverage evidence (see below). |
| `rc_transient` | Prior `params.netlist_check` supplies actual native connectivity/values. A supported populated passive R/C/L network receives the declared `input_net`, `output_net`, `ground_net`, positive `step_v`, `stop_ms`, and named measurements with `at_ms`, optional `min`/`max`. |

For `rc_transient`, omitted `step_v` uses 1.0 V and omitted `stop_ms` uses
5.0 ms. The result records the effective amplitude in `details.step_v` without
adding defaults to the captured scope. Omitted `step_v` and explicit `1.0`
produce the same metric condition, but their different scopes retain distinct
candidate identities.

Condition text preserves historical explicit amplitude spellings: `1`, `1.0`
and `"1.00"` remain `1V`, `1.0V` and `1.00V`. Measurement times retain the
existing float conversion (`1`, `1.0` and `"1.00"` become `1.0ms`). There is no
condition or empirical-observation migration; use the original condition text
when matching retained observations. This implementation change alters the
runtime policy hash, so historical results require their original policy for
verification and do not become fresh evidence under the new implementation.

For broader routing-width coverage, declare all four parameters:

```json
{
  "criterion": "copper-width/1",
  "covered_primitives": ["PCB_TRACK", "PCB_ARC"],
  "min_track_width_mm": 0.20,
  "exclude_nets": []
}
```

`copper-width/1` evaluates the native board track collection, including arcs.
The covered class list must contain exactly `PCB_TRACK` and `PCB_ARC`.
Exclusions match net names exactly, including an explicitly listed empty net
name. Vias are counted separately; pads, zones and graphic copper shapes are
outside this routing-width criterion. Via drill/annular-ring and zone geometry
need separate criteria.

`work/<check-id>/copper-width.json` retains candidate ID, board hash, pcbnew
version, primitive UUID/type/net/layer/width/limit/status, all proven offenders,
and eligible, checked, excluded and unsupported counts. Eligible means a
non-via track item not exempted by a known excluded net; it includes unsupported
items. Unsupported is a subset of eligible, so eligible = checked + unsupported.
The comparison uses native integer nanometres without the legacy report's
four-decimal rounding. Any unsupported or malformed eligible item, or an empty
eligible set, yields `execution: unsupported`, `outcome: unknown`, even when
other checked items are narrow. Complete coverage with a proven below-limit
track or arc yields a defect; complete coverage without offenders passes.

Checks without the new criterion retain straight-segment semantics, including
existing scope/2 baselines. Changing a criterion, its class coverage or exclusions
changes the captured baseline identity. Broader campaign acceptance therefore
requires a new baseline; an old passing review never gains arc coverage.
The native regression tests create actual `PCB_ARC` boards through pcbnew and
run the sandboxed probe. Malformed and future-class cases use synthetic parser
inputs; they do not claim native support for those classes or physical validation.

The passive generator excludes DNP parts and identifies passives through native
symbol class, rather than assuming a designator proves component identity.
Unmodeled active devices are unsupported, not replaced by an ideal approximation.
An unbounded measurement is informative. A bounded measurement is an explicit
criterion. Missing/non-finite measurements, invalid raw traces and solver errors
are unknown execution coverage rather than a passing experiment.

Values use raw SPICE scalar syntax, not general schematic display notation:
`Meg` denotes mega, while `M` follows SPICE's milli interpretation. Embedded-value
notations such as `4k7` are unsupported. Check the recorded original value,
parsed value and generated deck against the intended component/model before
using the simulation as electrical evidence. The demonstrations use unambiguous
`10k`, `100k` and `100n` values; broader value normalization is not demonstrated.

## Input and packet choices

`inputs` selects the actual native project, relevant external libraries, custom
rules, requirements, models and reference documents. Capture is explicit; it
does not discover every possible external dependency automatically. Keep the
selection current when adding symbols, footprints or models. Modified selected
files are captured as bytes even if their repository HEAD is unchanged.

`packet_inputs` selects original native text for the initial reviewers.
`requirements` selects captured requirement files. `packet_max_bytes` is a total
text budget between 1,024 and 1,000,000 bytes; each text artifact is additionally
limited to 100,000 bytes. `packet_artifacts` names required generated artifacts to
place early in the packet. Missing required artifacts fail the run; with no
explicit declaration, available evidence and failed-check diagnostics are supplied.
Every omitted binary and partial text extent is recorded. A reviewer cannot
claim to have inspected omitted data just because its hash/path is present.

Reviewers and adjudicator use explicit [profiles](reviewer-profiles.md). Claude,
Codex and Agy subscription adapters support ordinary investigative tools. Every
declared role must complete. More agents or model families do not prove independence.
There is no automatic provider or account fallback. Initial review scheduling
runs at most two calls concurrently while retaining role-specific schemas,
contexts and sandbox mounts. Scripted sleeping subprocess tests exercise overlap,
asymmetric failure/timeouts, retained artifacts, queued harness stops and the
adjudication barrier. These tests establish scheduling behavior, not live-model
latency or engineering review quality.

## Adding an engineering check

1. Write independent expected outcomes, applicable operating conditions and
   evidence requirements before implementing the check. Specify which claims a
   calculation, datasheet, model, native geometry or measurement can establish.
2. Implement a handler in `src/agent_ee/checks.py` and register its kind in
   `CHECK_FUNCS`. Use `Ctx` to record native commands/results and
   `executor.execute` for isolated subprocess work. Candidate files are at
   `/candidate`, writable scratch at `/work`; Python helpers are available at
   `/app`. Dependencies/configuration that affect results need recorded identity.
3. Retain original tool/source artifacts. An extraction must identify its source
   through the candidate manifest and useful native locators such as component,
   pin, net, UUID, document page or measurement. Captured PDF-derived facts need
   a path/page relationship to the captured original, not merely a model summary.
4. Return separate `execution` and design `outcome`, evidence paths, details and
   limitations. Raise `CheckError` for unsupported, failed or malformed work;
   `run_checks` persists each completed result. Never substitute successful
   completion for an unmeasured requirement.
5. Exercise the useful success case and consequential failure boundaries. Then
   review a fresh candidate with actual agent judgments. Tests of the coordinator
   do not demonstrate the electrical truth of a new check.

Changing checks, limits, models, exclusions or requirements creates new evidence
obligations. This implementation conservatively reruns instead of selectively
reusing acceptance. Its current correction command requires the complete new
review to satisfy criteria, even if one local repair's affected checks pass; it
does not close individual repairs while other blocking findings remain.

## Coverage that remains outside the demonstrations

The RC demonstration does not establish active-device behavior, component
tolerances, thermal limits, manufacturing approval, EMC or vehicle transients.
The donor's modules and assembled harness introduce behavior beyond the captured
carrier. Startup/reset/brownout safety, ignition energy, wideband loop stability,
potting temperatures and bench performance need dedicated requirements, sources,
checks/models and physical validation. Record these as limits or material open
questions; do not turn an unrequested condition into a speculative blocking
defect on a narrower valid control.

## Scope/2 and empirical coverage

The [baseline contract](scope-reference.md#version-2-baseline) requires every check
and requirement ID to be mapped. It preserves complete normative sources and
criteria, but does not infer missing engineering requirements. Scope/2 accepted
defects must be coordinator check findings backed by sealed evidence.

The [campaign](campaign-guide.md) provides bounded iteration and regression
protection. [Bench feedback](bench-feedback.md) adds measured residual envelopes
at declared operating conditions; generated replay cases test those retained
observations against unchanged limits. Neither facility invents unmeasured fault
conditions or qualifies a new physical model from provenance text alone.

Use [generic adapters](measurement-adapters.md) when an installed solver or
captured manufacturing/telemetry tool can establish an additional criterion.
Record what it actually computes and what remains out of scope. A domain string
such as `thermal` is declared tool routing metadata, not independent proof of
thermal qualification.

## Test environment coverage

Native-dependent tests preflight their sandbox and installed prerequisites.
Unavailable prerequisites produce explicit skips by default;
`AGENT_EE_REQUIRE_TOOLCHAIN=1` makes them fail. A skipped test supplies no native
execution evidence. Parser, orchestration and failure tests that do not require
those tools continue to run. See the [maintainer guide](maintainer-guide.md#development-and-checks)
for the strict command. Production missing-tool classification is unchanged.
