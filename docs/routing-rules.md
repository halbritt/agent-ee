# Captured routing rules (AGENTEE-63)

`agent_ee.routing_rules.check_routing_rules(ctx)` is a coordinator handler;
`capability(params, scope, files, inputs, cid)` describes its immutable input
and executable predicates. Shared check-kind registration and baseline discovery
are integration responsibilities. Until those are registered, this handler is
not a selectable CLI check and grants no scope/3 acceptance coverage.

Parameters contain `routing_rules`, the complete candidate-relative JSON path.
That file and the baseline requirement sources must be captured. Each declared
`requirement` must map to this check in the baseline, with no extra mappings.

```json
{
  "schema": "agent-ee.routing-rules/1",
  "constraints": [
    {"id": "usb-skew", "requirement": "usb", "kind": "skew",
     "nets": ["USB_D+", "USB_D-"], "max_mm": 0.15},
    {"id": "clock-vias", "requirement": "clock", "kind": "via_count",
     "nets": ["CLK"], "max_count": 2}
  ]
}
```

Supported kinds are `length` (`min_mm`, `max_mm`, or `target_mm` with
`tolerance_mm`), `skew` (`max_mm`, across all explicitly selected nets),
`via_count` (`max_count`, integer), and `clearance` (`min_mm`). A two-member
skew set can represent an explicit differential pair; no pair-name inference is
used. Length matching a bus uses one skew constraint naming all members.
`impedance` with `target_ohm` always remains unsupported: this implementation
contains no qualified impedance solver, even if stackup or solver names appear.
Net expressions, undeclared fields, nonfinite bounds and implicit units are refused.

Each constraint runs separately with native KiCad 10 DRC, preventing last-rule
precedence from hiding overlapping limits. Captured project/custom rules are
copied to staging. The generated rule is appended there, relevant native check
severities are enabled, and exclusions are cleared in that staged project.
Unrelated native DRC violations remain in the raw report; only the generated
rule's findings evaluate the explicitly mapped constraint. Ordinary DRC remains
necessary for general board validity and schematic parity.

Only single-layer SMD terminal pairs on a proved unbranched saved route are
qualified. Straight tracks, arcs, and two-layer through vias are supported.
Every member needs exactly two pads, exact pad-centre endpoints, connected
copper and no branches, stubs, disconnected islands or zone participation.
Other topologies remain missing coverage. Unrouted members are never zero-length
passes. This is deliberately narrower than arbitrary KiCad connected copper.

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
Tests establish tool behavior, not hardware or impedance qualification.

Native rule semantics: [KiCad 10 PCB documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html).
