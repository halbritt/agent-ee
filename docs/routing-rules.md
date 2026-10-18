# Captured routing rules (AGENTEE-63)

`routing_rules` is a selectable coordinator check in scope/2 and scope/3:

```json
{"id": "usb-routing", "kind": "routing_rules", "params": {"routing_rules": "requirements/usb-routing.json"}}
```

Scope/3 validates the captured contract before execution and records each ready
constraint as an executable predicate. This static capability grants no passing
design result; native execution must establish every declared constraint.
The rule document and any captured board sibling `.kicad_pro`/`.kicad_dru` files
are immutable baseline dependencies in both scope versions. Native KiCad CLI,
pcbnew and coordinator Python identities are retained by the existing policy and
runtime binding; inference preflight requires both native KiCad interfaces.

Parameters contain `routing_rules`, the complete candidate-relative JSON path.
That file and the baseline requirement sources must be captured. Each declared
`requirement` must map to this check in the baseline, with no extra mappings.
Exactly one requirement may map to each routing check. Its constraints are
conjunctive: any evaluated violation fails that requirement, and all constraints
must be evaluated before a pass or defect is available. Use separate checks and
rule documents for independent requirements; a satisfied requirement cannot
borrow another requirement's failed constraint as its defect witness.

```json
{
  "schema": "agent-ee.routing-rules/1",
  "constraints": [
    {"id": "usb-skew", "requirement": "board-routing", "kind": "skew",
     "nets": ["USB_D+", "USB_D-"], "max_mm": 0.15},
    {"id": "usb-vias", "requirement": "board-routing", "kind": "via_count",
     "nets": ["USB_D+", "USB_D-"], "max_count": 2}
  ]
}
```

Supported kinds are `length` (`min_mm`, `max_mm`, or `target_mm` with
`tolerance_mm`), `skew` (`max_mm`, across all explicitly selected nets),
`via_count` (`max_count`, integer), and `clearance` (`min_mm`). A two-member
skew set can represent an explicit differential pair; no pair-name inference is
used. Length matching a bus uses one skew constraint naming all members. Length and
skew use native KiCad length calculations in millimetres. Routing preserves
captured project settings, including `use_height_for_length_calcs`; it does not
force planar-only lengths. Capture the board's project file when its length
settings are part of the requirement. These checks do not qualify propagation
delay or an impedance model.
`impedance` with `target_ohm` always remains unsupported: this implementation
contains no qualified impedance solver, even if stackup or solver names appear.
Net expressions, duplicate JSON keys, undeclared fields, nonfinite bounds and
implicit units are refused. Net conditions preserve literal UTF-8; JSON Unicode
escapes are not used as a substitute for native net-name text.

Each constraint runs separately with native KiCad 10 DRC, preventing last-rule
precedence from hiding overlapping limits. Captured project/custom rules are
copied to staging. The generated rule is appended there, relevant native check
severities are enabled, and exclusions are cleared in that staged project.
A reserved tool-only disallow rule must produce a native loading witness;
otherwise silent KiCad custom-rule loading failure yields unknown coverage. Its
deliberate findings never count as design defects. Required native severities and
the loading witness are checked in the report.
Unrelated native DRC violations remain in the raw report; only the generated
rule's findings evaluate the explicitly mapped constraint. Ordinary DRC remains
necessary for general board validity and schematic parity.

Only single-layer SMD terminal pairs on a proved unbranched saved route in a
two-copper-layer board are qualified. Straight tracks, non-reversing bends and
through vias are supported. Every member needs exactly two pads, exact pad-centre
endpoints, positive copper widths and connected copper without branches, stubs
or disconnected islands. Native finite-width shape collision checks refuse
nonincident track intersections, overlaps and pad/via contacts, including contact
with other nets. Backtracking and acute junction angles at a shared endpoint
are also refused.
A one-native-unit collision guard includes exact finite-width tangency.
Arcs, zones anywhere on the board, copper graphics and multilayer topology remain
unsupported because their physical junction coverage is not qualified here.
Unrouted members are never zero-length passes. This is deliberately narrower than arbitrary KiCad connected copper.

`rule-request.json`, `routing-probe.json`, per-constraint staged rules/project,
commands, console, native JSON and `rules-evaluation.json` are retained under the
check's workspace for the normal coordinator seal. Records include requirement
IDs, generated and effective rule hashes, source JSON hash, saved board hash,
native report hash and native item UUIDs. Unsupported inputs/tool failures yield
unknown rather than passing acceptance. Frozen candidate bytes are never edited.

The owned fixture generator in `tests/routing_fixture.py` creates independent
native boards without libraries or third-party design assets. Strict native tests
cover 0.3/0.1 mm skew against 0.15 mm, three vias against two, length and clearance,
overlapping rules, missing routing, impedance and unchanged captured bytes.
Malformed custom rules must leave coverage unknown; a captured project that
ignores the via-count check must still produce a defect after staging enables it.
Run these controls and the shared fabricator runner tests with:

```sh
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_routing_rules tests.test_fab_profile -v
```

Public scope/3 review/verify tests exercise real native measurements with
scripted independent reviewer transports and centralized adjudication, sealed
witness validation, supported pass/defect, partial coverage and deliberate
artifact tampering. Scope/2 and scope/3 capture tests enforce immutable rule
inputs and single-requirement mappings. CLI checks-only runs preserve incomplete
review status even when all routing constraints pass. Tests establish coordinator
and native tool behavior, not live-model quality, hardware or impedance
qualification.

The [fabricator profile check](fab-profile.md) reuses this runner for captured
fabrication limits. Only its constraints stage project minima, add a saved-board
fabrication inventory, or receive outline-gated extra-charge classification.
Routing constraints keep the captured project settings unchanged.

Native rule semantics: [KiCad 10 PCB documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html).
