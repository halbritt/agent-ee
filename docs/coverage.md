# Declaring and extending review coverage

The executable examples are `fixtures/rc/control.scope.json`,
`fixtures/rc/seeded.scope.json`, `scopes/donor.json`, and
`scopes/donor-case-alias.json`. The last two are project-specific input/scope
choices; the orchestration and checks contain no FOME-MC requirements.

## Existing checks

| Kind | Criterion and evidence |
| --- | --- |
| `erc` | Native all-severity ERC plus complete hierarchy coverage. Missing child files prevent a clean result. |
| `drc` | Native all-severity DRC, saved fill, and schematic parity. Reports retain exclusions and native items. A violation is distinct from execution failure. |
| `netlist` | Complete-hierarchy native XML export, with derived connectivity, DNP, native symbol identity, and sheet/UUID provenance. Informational rather than functional acceptance. |
| `geometry` | Saved-board pcbnew extraction. `params.min_track_width_mm` optionally applies a declared minimum to track segments; `params.exclude_nets` records explicit exemptions. Via diameters are not track-width acceptance criteria. |
| `rc_transient` | Prior `params.netlist_check` supplies actual native connectivity/values. A supported populated passive R/C/L network receives the declared `input_net`, `output_net`, `ground_net`, positive `step_v`, `stop_ms`, and named measurements with `at_ms`, optional `min`/`max`. |

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
There is no automatic provider or account fallback.

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
