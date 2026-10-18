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
independent of reference designators and net names. Component keys contain exact
library symbol, exact value and native population state. Identical parts remain
separate vertices. Net labels survive in diagnostics but do not confer identity.
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
`{"sheet":"/", "uuid":"..."}` identities. Reference names cannot be anchors.
These identities must be explicitly declared; logical intent IDs alone never
become native identities. Arbitrary exchanges of truly indistinguishable parts
are graph automorphisms and cannot honestly be promised detectable without anchors.

Joint color refinement precedes bounded exact bijection search. `max_search`
defaults to 100000 assignment attempts and may be reduced, never increased beyond
that bound. Inputs are bounded to 8 MB, 128 components and 8192 physical terminals;
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
