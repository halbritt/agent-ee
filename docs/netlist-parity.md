# Netlist parity

The `netlist_parity` check compares a captured golden XML netlist or resolved
connectivity intent with an earlier successful native `netlist` check. The full
`design.xml` is authoritative. The retained navigation `connectivity.json` must
parse and equal the existing native XML projection; it does not supply the
comparison graph. Missing or unreadable source or native artifacts give execution
`failed`; malformed or contradictory inputs give `malformed`. Both have outcome
`unknown` and cannot establish a parity witness.

```json
{
  "id": "parity",
  "kind": "netlist_parity",
  "params": {
    "source": {"kind": "golden_xml", "path": "golden/design.xml"},
    "netlist_check": "netlist",
    "predicate": "topology",
    "multiset": "exactly"
  }
}
```

Capture the source in `inputs`, declare the netlist check earlier, and map parity
to exactly one baseline requirement. The declared source and any referenced
symbol libraries are immutable verification dependencies in scope/2 and scope/3.
The source adapter must be explicit: `golden_xml`, `resolved` for
`agent-ee.resolved-connectivity/1`, or `intent` for
`agent-ee.connectivity-intent/1`. Intent uses the captured-library resolver;
incomplete or rejected resolution cannot establish parity. No host library search
or model call supplies missing input.

A resolved record is not graph authority by itself. Its self-hash and structural
validation do not show that the captured libraries produced its pin inventory.
`resolved` therefore requires the captured intent it came from:

```json
{"source": {"kind": "resolved", "path": "resolved.json", "intent": "intent.json"}}
```

The check resolves that intent against the captured libraries in coordinator code.
The captured record's canonical bytes must equal that resolution exactly; any
difference, including an authored and re-signed edit, is `malformed` with outcome
`unknown`. Library dependencies come from the intent, not from the record's own
claims. The intent, the record and every library are immutable scope/2 and
scope/3 inputs. Reports record `source_provenance`
(`captured_golden_native_xml`, `resolved_from_captured_intent` or
`captured_resolution_rederived_from_captured_intent`) and `intent_source_sha256`.
A golden XML netlist is a separately captured owner reference. It makes no
library-resolution claim.

`topology` compares exact physical-pin incidence and component multiplicity,
independent of reference designators and net names unless explicitly anchored below.
Component keys contain exact library symbol, exact value and native population
state. Identical parts remain separate vertices. Undeclared net labels survive
in diagnostics but do not confer identity.
No pin electrical-type, footprint adequacy or electrical behavior criterion is
inferred. Native singleton nets remain in the graph except generated unconnected
singletons compatible with an explicitly declared unconnected/no-connect pin.
These declared pins have no expected conductor. The report retains each such
normalization, possible logical pins and native marker state. Exact incidence
still checks every required binding, including on indistinguishable parts;
an off-pin label cannot pass merely because its net name exists.

`components` checks the component multiset. `multiset: exactly` rejects missing
and extra entries; `at_least` permits extras but still rejects missing counts.
`at_least` is supported only for this predicate, not topology acceptance.

`instance` requires an explicit `instance_components` list. It evaluates topology
and requires unique correspondence for each selected logical component. For
symmetric circuits, topology may be equivalent while instance correspondence is
unknown. Optional `anchors` map logical component IDs to exact native
`{"sheet":"/", "uuid":"..."}` identities or explicitly fixed references.
These identities must be explicitly declared; logical intent IDs alone never
become native identities. Arbitrary exchanges of truly indistinguishable parts
are graph automorphisms and cannot honestly be promised detectable without anchors.

## Anchored search for repeated channels

AGENTEE-192 adds explicit reference anchors and required-name anchors. Both
`topology` and `instance` can use them; `components` refuses them because it
does not evaluate connectivity. References remain unconstrained unless the
captured scope declares them fixed:

```json
{"anchors": {"channel_1_input": {"reference": "R1"}}}
```

Each component anchor takes either exactly `reference` or exactly `sheet` and
`uuid`. Duplicate identities are refused. Different declarations resolving to
the same native component are a defect. A missing fixed reference, an incompatible
component at that reference, or connectivity contradicting a resolved anchor is
a defect. An unresolved sheet/UUID identity retains the existing unknown result.
An unknown logical component ID is also unknown.

Optional `net_name_policy` names a captured
[`agent-ee.net-name-policy/1` policy](net-names.md). Its requirement ID and source
must match a captured baseline requirement; the policy and its requirement
source become immutable inputs. This reuses AGENTEE-44's required names and
exported reference/pin anchors. The mutable level annotation does not select
parity anchors. Suggested names and names merely shared by the two graphs never
become anchors.

```json
{"net_name_policy": "verification/required-names.json"}
```

For each declared required name, the source must have one net with that basename.
A missing or ambiguous source net is unknown. Native names and reference/pin
terminals are evaluated with the existing net-name policy evaluator: absent,
duplicate, displaced, split or conflicting required names are defects. Unresolved
terminal anchors without an established mismatch are unknown. Hierarchical names
use native basenames, as AGENTEE-44 does; repeated basenames cannot select one net.

Resolved component and net anchors seed joint color refinement before the exact
bijection search. Only vertices compatible with those constraints remain search
candidates. Refinement proving incompatible color multiplicities establishes a
defect without enumerating diagnostic permutations. Otherwise full physical-pin
incidence and anchored net correspondence must still match exactly. Anchors do
not raise the search bound or turn an exhausted search into a pass.

The report's `anchors.components` and `anchors.nets` record resolved declarations
and native identities; `anchor_mismatches` records established contradictions.
`net_name_policy_sha256` binds the optional name policy. `search_steps` counts
attempted component assignments, including used candidates, under the same bound.
Early component-multiset failures precede anchor resolution.

The owned `test_four_channel_symmetry_anchored` fixture has four identical RC
ladders (48 components). Its deliberately scrambled native order exhausts 100000
steps without anchors; four required-name anchors resolve it in 48 steps. Fixed
references also resolve selected instance correspondence. Native tests verify
anchor defects, witness sealing and policy immutability.

## Bounds and reports

Joint color refinement precedes bounded exact bijection search. `max_search`
defaults to 100000 assignment attempts and may be reduced, never increased beyond
that bound. Inputs are bounded to 8 MB, 1,024 components, 2,048 nets and
8,192 physical terminals, using the [shared inventory ceilings](design-size-limits.md);
raw-intent captured libraries have a combined 32 MiB byte limit. Native XML must
use UTF-8.
XML also has depth/node limits. Exhaustion produces unsupported execution and
unknown outcome. Approximate scores never decide conformance.

`work/<id>/parity.json` records hashes of the source, native XML and connectivity
JSON. It includes component inventories and match candidates, expected/native
net names, matched/missing/extra nets,
merge/split relations, changed or missing terminal bindings, native and intent
locators, and descriptive Jaccard/precision/recall/F1 scores when a full mapping
exists. Counts can establish a component mismatch before a graph mapping exists.
Resolved-source runs also retain `resolved-intent.json` and its intent identity.
The public review CLI retains its existing requirement for a captured board
alongside the schematic; parity itself evaluates only schematic connectivity.
The ordinary coordinator seal covers the report and native prerequisite artifacts.
An unsuccessful export, missing file, malformed inventory or unresolved predicate
cannot produce a passing witness. Only the configured predicate is evaluated;
one parity check cannot witness multiple independently scoped requirements.

For captured intent, single-unit, single-pin power/flag symbols with `#`
references, no footprint and a connected `power_in` or `power_out` pin are
projected out of the physical graph, matching KiCad 10's XML export. The report
lists each one in `normalized_power_symbols` with its logical ID, reference,
library symbol, pin, net and source locator. Every physical terminal remains in
its declared net; the component count and instance predicates cover physical
components. An instance predicate targeting an omitted marker is unknown.
Unqualified virtual symbols, disconnected markers and any net left with fewer
than two physical terminals are unsupported. This normalization cannot prove
that a power symbol or flag is present, or establish a power-drive declaration;
native ERC must grade the emitted schematic separately.

The owned test circuit declares two identical resistors and a numbered port.
Expectations were committed before native execution. Native tests exercise full
renaming, reference exchange, port pin swap, label merge, a label displaced 1 mm,
a non-isomorphic same-part mutation and missing/extra parts. Pure tests cover
symmetric correspondence, anchors and search exhaustion. These are software and
native-connectivity checks, not physical validation or owner acceptance.


AGENTEE-298 expands the component ceiling introduced by AGENTEE-174 and aligns
hierarchy and decompiler inventory bounds. Component and net overflow are refused
before captured-intent library reads. The terminal, XML-byte and bounded-search
guards remain finite. Newly supported designs undergo the same checks; capacity
alone never establishes a passing result. Exact assignments use an explicit
stack to support 1,024 components without depending on Python recursion depth.
