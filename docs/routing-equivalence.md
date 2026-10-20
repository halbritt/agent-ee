# Routing geometry equivalence

`routing_equivalence` compares the captured `scope.board` with a second captured
routed board. It implements the owner decision that routing determinism means
equal normalized copper geometry multisets, including primitive counts. Board
hashes remain evidence; unequal bytes alone never fail this check. The check does
not run the router or rewrite either board.

```json
{
  "id": "route-repeatability",
  "kind": "routing_equivalence",
  "params": {"reference_board": "references/repeated-route.kicad_pcb"}
}
```

Capture the reference in `scope.inputs` and bind the check to a baseline
requirement, as for other acceptance checks. Scope/2 and scope/3 freeze the
reference board as an immutable verification input. The reference must have a
different captured path from `scope.board`. The candidate board remains mutable.
The check registers the `routing:copper-geometry-equal` predicate for scope/3.
Preflight requires pcbnew; execution uses `/usr/bin/python3` in the ordinary
networkless check sandbox.

The existing native geometry reader extracts these facts from each saved board:

- Straight segments: net name, layer, width and unordered endpoints.
- Arcs: the same facts plus the arc midpoint.
- Uniform vias: net name, layer span, flashed layers, native type, position,
  copper diameter and drill diameter.
- Copper zones, including footprint zones: net name, layers, minimum thickness,
  outline contours and holes, saved filled state, and saved fill contours and
  holes per copper layer. Rule areas have no copper and are excluded.

All lengths and coordinates are integer KiCad board units (nanometres), without
float rounding. Contours ignore their starting vertex, winding and repeated
closing vertex. Element JSON is sorted into a canonical multiset that retains
duplicates; UUIDs and serialization order do not participate. Net codes are
resolved to net names by pcbnew. Equal geometry and element counts pass even when
board hashes differ.

This is equality of saved routed primitives. Splitting or merging segments,
changing zone partitioning or retessellating contours can change the multiset even if
the union of occupied copper area is equivalent. Pads, footprint placement,
copper text and graphical copper are outside this routing criterion. It is not
a connectivity, DRC, completeness or fabrication check; retain those checks.
Unfilled zones are compared as saved definitions, without filling them.
Nonuniform via padstacks and curved zone contours are unsupported and fail the
native call. Missing or changed captured files, invalid board syntax, native
failures, missing reports and empty copper inventories cannot produce a pass.

The retained `work/<check-id>/comparison.json` has schema
`agent-ee.routing-equivalence/1`. It records `geometry_equal`, informational
`byte_equal`, both board SHA-256 hashes, native pcbnew version, both complete
canonical inventories, and `only_board` / `only_reference` elements. A differing
count repeats the excess element in the corresponding difference list.
`board_duplicates` and `reference_duplicates` list each duplicated `element` with
its total occurrence `count`. `count_differences` names each differing `element`
with its `board_count` and `reference_count` (zero when absent). A differing
segment is identified by kind, net, layer, width and coordinates rather than an
unstable UUID. A geometry difference is execution `ok`, outcome `defect`; native
execution failures retain outcome `unknown`. Ordinary execution records and
evidence sealing bind this report to the candidate and check witnesses.

`tests.test_routing_equivalence` exercises the native inventory, registration,
baseline immutability, sealed witnesses and failure boundaries.
`tests.test_kicad_route_native.NativeKicadRouteTest.test_repeated_krt_runs_compare_geometry_and_name_a_moved_segment`
runs the pinned KRT twice on the owned placed RC, compares the routed outputs,
then duplicates one segment exactly and moves one segment, requiring that each
comparison identify the differing element and the duplicate's count.
Set `AGENT_EE_KICAD_ROUTE_CHECKOUT` to the qualified pinned checkout and
`AGENT_EE_REQUIRE_TOOLCHAIN=1` to require all native prerequisites.


AGENTEE-282 retains the entire repeat-run workspace on the first exception,
including executor console/execution records, both routing runs, adapter reports,
native result/attempt files, validation logs, candidates and comparison files
produced before failure. Unittest still reports the original failure; there is
no retry. The console prints `Native routing failure evidence retained at PATH`.
`PATH` is a fresh `agent-ee-route-failure-*` directory under the host temporary
directory (`TMPDIR` when configured), with `failure.json` naming the test and
exception and `workspace/` containing the artifacts. Symbolic links are retained
as links. Successful tests create no archive. Use a persistent job scratch
`TMPDIR` for retained evidence; host temporary-directory cleanup still applies.

The intermittent pin-a132899 repeat-2 adapter exit 65 observed during AGENTEE-276
remains an unresolved historical incident. A passing later run cannot explain
it. A future failure now retains the native evidence needed to distinguish an
exit/status contradiction from missing output or a backend crash. Do not retry a
failure away or relabel missing coverage as a pass.
