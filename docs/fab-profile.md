# Captured fabricator profiles (AGENTEE-75)

`agent_ee.fab_profile.check_fab_profile(ctx)` evaluates an explicitly captured
`agent-ee.fab-profile/1` document using the routing compiler's staged native runner.
`capability(params, scope, files, inputs, cid)` reports immutable dependencies and
minimum-limit predicates. The integrator must register the handler, capability
and baseline dependency discovery before it becomes a CLI check. No preset,
network lookup, pricing service or default fabricator is supplied.

Parameters contain `fab_profile`, the complete candidate-relative JSON path.
A check requires one mapped fabrication requirement covering its declared limits,
matching the existing coordinator witness authority contract. The descriptor
includes captured sibling project/custom-rule criteria as immutable inputs.

For example, an **owned synthetic assumption**, not a real fabricator's limit:

```json
{
  "schema": "agent-ee.fab-profile/1",
  "name": "owned_standard_2026-10",
  "as_of": "2026-10-17",
  "limits": [{
    "id": "trace", "requirement": "fabrication", "key": "min_trace_mm",
    "value": 0.1, "unit": "mm", "tier": "minimum",
    "source": {
      "path": "sources/owned-capabilities.txt",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "source": "Owned synthetic assumptions",
      "as_of": "2026-10-17", "page": 1, "quote": "Minimum trace: 0.1 mm"
    }
  }]
}
```

Replace the placeholder hash with the captured source's SHA-256. Each limit needs
its own source identity, capture path/hash, date, page and exact quote. Dates must
match the profile's pinned date. The v1 source dialect is UTF-8 text with form-feed
page boundaries; PDF citations need a separately qualified extraction adapter and
are unsupported here. Citation verification establishes exact occurrence, not
source authenticity, applicability or correct interpretation of a table. The
engineer still owns the typed value and its interpretation of the cited quote.
No live fabricator fact is inferred from a name, example or website.

Supported minimum/preferred keys:

| Key | Unit/value | Evaluation |
| --- | --- | --- |
| `min_trace_mm` | mm | Native track/arc width |
| `min_clearance_mm` | mm | Native copper clearance |
| `min_via_drill_mm` | mm | Native via hole size |
| `min_via_pad_mm` | mm | Native via diameter |
| `min_edge_clearance_mm` | mm | Native board edge clearance |
| `min_hole_to_hole_mm` | mm | Native drilled-hole clearance |
| `max_layers` | integer count | Saved board copper layer count |
| `max_board_mm` | `[x,y]` mm | Closed straight-edge outline extents, fixed board axes |
| `supports_smt` | boolean | Presence of saved SMD pads |

Assembly coverage is limited to the declared presence of SMD pads. It does not
establish assembly process compatibility, component sourcing or factory approval.
Curved/open outlines leave board-size coverage unknown. Geometry violations cite
board, edge or pad UUIDs in `actual`/`offender_ids`; these computed comparisons
are distinct from the raw DRC `native_findings` records. Via/track violations retain the native offender UUIDs.

Sourced physical minima generate custom DRC rules and staged `.kicad_pro`
`board.design_settings.rules` limits. Every limit runs separately, preserving the
captured candidate and avoiding rule precedence between profile limits.
Minimum violations are defects. Preferred violations become `extra_charge`
records only for offender IDs not already shown illegal under the evaluated
minimum for the same key. A preferred limit without that minimum remains unknown.
A 0.25 mm drill under an explicitly sourced 0.2 mm minimum and 0.3 mm preferred
threshold therefore reports via IDs as extra charges. These numbers are test
assumptions, not a bundled standard preset or quote from any fabricator.

The source profile hash is in `rules-evaluation.json`; source citations are
retained in each expected limit. Effective/generated rules, staged project hashes,
native JSON and geometry facts remain coordinator evidence under the check's
workspace. Missing/invalid profiles never select defaults. A missing fact leaves
that limit `execution=unsupported` and the aggregate outcome unknown; other
established violations remain visible in the per-limit records. A changed profile
or source document cannot reuse the captured candidate's acceptance.

Owned native fixtures test 0.09 mm trace failure, 0.15 mm legal/preferred warning,
undersized drills, 0.25 mm drill extra charges, excessive layer counts/outline
extents, remaining native rule mappings, invalid source support and open outlines.
The same tests seal actual native reports using production coordinator evidence
logic, validate defect witnesses, and reject changed sealed artifacts and inputs.
Owner review and peer review remain separate from these checks.
