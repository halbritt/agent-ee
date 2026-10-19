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

Only single-layer SMD terminal pairs on a proved unbranched saved track/via
route are qualified for numeric measurements. Enabled copper layers are read
from the native board stack. Straight tracks, non-reversing bends and through
vias joining two distinct incident layers are supported. Every member needs
exactly two pads, distinct degree-one endpoints inside actual native pad copper,
positive copper widths and connected copper without branches, stubs
or disconnected islands. Native finite-width shape collision checks refuse
nonincident track intersections, overlaps and pad/via contacts, including contact
with other nets. Backtracking and acute junction angles at a shared endpoint
are also refused.
A one-native-unit collision guard includes exact finite-width tangency.
Arcs, blind/buried and micro vias, copper graphics, and footprint-owned copper
zones remain unsupported. Fresh board-owned planes on unrelated nets do not
prevent track-path grading; zone-assisted nets have connectivity evidence only.
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
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src:tests python3 -m unittest tests.test_routing_rules tests.test_fab_profile tests.test_fab_profile_conjunctive -v
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

## Native pad contacts (AGENTEE-185)

KRT may stop a route inside an SMD pad instead of at its centre. Coverage now
requires exactly one graph vertex inside each native pad shape on its copper
layer, with degree one and distinct terminal assignments. A bounding box,
endcap-only contact, multiple endpoints in a pad or incidental contact with
other copper cannot establish this path. Existing branch, intersection,
backtracking, pad-overlap and unsupported topology refusals remain.

This changes only path coverage, not length measurement. Native KiCad DRC
continues to evaluate saved copper length with the captured project settings.
The CAN probe's 10 mm pad-centre separation produces 9.8125 mm of straight copper
per member; no pad-centre offset is added by the checker. See
[the CAN pair probe](pod-can-routing.md) for the controlled 5 mm skew negative.

## Filled planes and multilayer connectivity (AGENTEE-206)

The native probe distinguishes `complete` connectivity from
`measurement_qualified`. A ground net connected through a native filled plane
can be complete while remaining unqualified for length/skew. Numeric rule
grading requires both fields; connectivity never supplies an inferred length.
Native DRC still owns the saved copper length and captured via-height settings.
Four-pad or branched CAN paths remain outside numeric qualification.

For plane nets the probe builds KiCad connectivity from the saved board and uses
`GetConnectedItems` to require every pad and routed primitive of that net in the
same native connected component. Separate islands within one zone are not
joined by zone UUID. Thermal connections are taken from native connectivity;
this checker does not infer thermal spokes, current capacity or impedance.
Repeated native copper identities refuse qualification.

Before relying on any saved board-owned copper zone, the checker compares its
exact filled contours (including holes, keyed by zone UUID and copper layer)
against a native refill of a private copy. The copy uses the captured PCB,
project and custom rules; source hashes and saved/refilled contours are retained
in `routing-probe.json`. A second save/load/refill must produce identical
contours. Missing fill, stale fill, unstable refill, duplicate zone identity or
native failure cannot pass. `NeedRefill` alone is insufficient: KiCad can clear
it on load while retaining obsolete polygons. The refreshed board is evidence
only and is never adopted. Rule-area keepouts need no fill and remain active
during native refilling, including antenna keepouts.

`tests/test_routing_layers.py` includes four-layer inner tracks and through vias,
a removed-via negative, thermal plane connectivity, disconnected islands cut
by one rule area, unfilled planes, and outline/pad edits saved without refilling.
The native four-layer CAN probe uses the declared 183 plane producer then the 185
routing adapter: original maximum 15.1 mm length, 0.15 mm skew and 0.34–0.36 mm
gap rules remain intact. The delivered board passes, adding 5 mm fails skew,
and plane-connected GND remains unknown for a numeric length requirement.
This owned software fixture does not qualify actual pod branching, circuitry,
impedance, source captures, bench behavior or fabrication.
