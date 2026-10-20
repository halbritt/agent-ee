# Supported design-size ceilings (AGENTEE-298)

Electrical graph readers share finite limits from `agent_ee.design_limits`:
**1,024 components, 2,048 nets and 8,192 physical terminals**. These cover the
expanded telemetry pod (327 physical components, 369 nets, 1,128 terminals)
with more than threefold component headroom and the roadmap's approximately
492-footprint FOME-MC rung with more than twofold component headroom. They are
capacity bounds, not a promise of a passing check or tractable exact search.

## Inventory boundaries and consumers

| Owner | Enforced inventory limits | Consumers |
| --- | --- | --- |
| `netlist_parity.terminal_inventory` | Shared component, net and terminal limits | `resolved_inventory`, native XML reader, parity normalization and comparison inputs |
| `netlist_parity._resolve_captured_intent` | Shared component and net limits, before library reads | Parity intent and resolved-source checks |
| `_connectivity_hierarchy.validate_intent` | Shared component and local net-segment limits, before library resolution | `connectivity_ir.resolve_intent` schema/2; resolved receipt validation reconstructs this boundary |
| `_connectivity_hierarchy.resolve` / `validate_resolved` | Shared terminal limit during and after symbol resolution | Hierarchical emitter, binding, roundtrip and parity consumers |
| `schematic_decompile` | `MAX_SYMBOLS` = shared component limit (also bounds library declarations); `MAX_PINS` = shared terminal limit; shared extracted net limit | `schematic_roundtrip`; `fp_calibration` imports only the pin-position helper |
| `native_netlist.read_native_inventory` | Shared graph limits via `terminal_inventory` | `forward_annotation`, binding, parts, net and package checks |

Flat schema/1 intent resolution and structural receipt validation retain their
existing bounded grammar and do not impose the downstream electrical graph
policy. Their output is evaluated against the same shared ceilings by
`resolved_inventory`, before it can be used for parity. A passing resolution
receipt alone is not a passing parity result. This preserves the explicit
`BindingScaleBoundaryTests.test_resolved_record_overflow_stays_unsupported`
contract: a structurally valid over-capacity flat receipt is still unsupported
at graph conversion. Hierarchy counts local segments before port equivalence;
this also bounds the work needed to build the physical nets.

`forward_annotation._inventory` uses the shared native reader; it has no separate
component ceiling. `native_netlist.read_components` and `read_native_instances`
remain identity/BOM projections with their existing byte/structure bounds;
they do not perform electrical graph qualification.

Direct parity importers are `baseline`, `capabilities`, `checks`, `native_netlist`,
`native_binding`, `netlist_extra`, `package_consistency`, `rung1_qualified`,
`schematic_lint` and `schematic_roundtrip`. Hierarchy is dispatched through
`connectivity_ir`. Test consumers also include binding fill, block composition,
symbol capture, datasheet integration, emitters, schematic presentation and
staging. No registration, schema or dependency changes are needed.

## Independent resource guards

| Guard | Limit, unchanged |
| --- | ---: |
| Parity and native XML source reads | 8,000,000 bytes |
| Aggregate captured symbol libraries (parity/decompiler) | 32 MiB |
| Exact graph search | 100,000 candidate visits |
| Shared S-expression parser | 32 MiB; 2,000,000 nodes; depth 128 |
| Native XML structure | 100,000 nodes; depth 32 |
| Flat intent JSON / declaration arrays | 8 MiB / 10,000 entries |
| Hierarchy occurrences / depth / identifier bytes / scoped projection | 256 / 16 edges / 256 UTF-8 bytes / 8 MiB |
| Decompiled source / wires / coordinate nodes / coordinate-segment work | 8 MiB / 2,048 / 8,192 / 2,000,000 |

Over-limit inventory remains typed `Unsupported`, or a sealed
`unsupported/unknown` resolver/decompiler report. The annotation plan preserves
its `failed/unknown` wrapper and `native_netlist` finding on native inventory
refusal. Search exhaustion and unresolved automorphisms remain unknown. Exact
assignment traversal uses an explicit depth-first stack so a uniquely matched
1,024-component design does not depend on Python's recursion limit; enumeration
order and per-candidate search accounting remain unchanged.

Other design-related count/resource constants found by the repository-wide scan
remain distinct policies, not aliases of the component ceiling:

| Owner | Current bounds, all unchanged | Why kept separate |
| --- | --- | --- |
| `native_binding`, `part_binding` | 4,096 occurrences each | Native sheet-instance traversal. |
| `schematic_lint` | 256 sheets; 4,000,000 comparisons per sheet | Hierarchy and geometric rule work. |
| `schematic_emitter` | 64 presentation groups | Authored layout grouping. |
| `symbol_capture.LIMITS` | 32 MiB source; 8,000,000 scan nodes; 64 closure symbols; 4 MiB / 1,000,000 closure nodes | Captured symbol definitions, reusable across instances. |
| `board_envelope` | 32 copper layers; 1 MiB; 1,024 vertices; 1,024 items | Board envelope declaration grammar. |
| `packet_index` | 16,000,000 source bytes; 50,000 entities | Navigation projection budget. |
| `package_consistency` | 8 MiB table; 100,000 entries | Captured package lookup inventory. |
| `fab_consistency` | 4,096 files; 64 MiB text; 50 citations | Package traversal and report budget. |
| `easyeda_footprint` | 16 MiB; 20,000 records | One footprint import, not design multiplicity. |
| `kicad_embedded` | 64 MiB decompressed content | Embedded resource guard. |
| `schematic_render` | 16,000,000 pixels | Rendered image area. |
| `design_diff` | 400 rendered entries | Report display, not graph inventory. |
| `pour_budget` | 100,000 vias | Explicit route budget. |
| `topology_rules`, `datasheet_footprint` | 1,000,000 visits; 200,000 search steps | Rule-specific finite traversal. |
| `interval_assertions` | 12 arithmetic symbols; 256 entries; 512 expression characters; depth 64 | Arithmetic expression grammar, not schematic symbols. |
| `datasheet_capture`, `datasheet_cascade`, `jlc_stock_capture`, `digikey_record` | 256 part bindings; 64 cascade parts; 256 stock IDs; 100 distributor queries | Bounded acquisition batches, not full design inventory readers. |
| `catalog`, `catalog_jlcparts` | 512 MiB per volume; 1 GiB combined; 8 GiB expanded archive | Offline component catalog storage. |

Other `MAX_*` values bound numeric precision, field/token length, query length,
timeouts, result counts or retry/presentation budgets. They do not count design
components, nets or physical pins and need no ceiling expansion.

## Capacity ruling and executable qualification

Coordinator agent-449 ruled that increasing a supported capacity causes the
same checks to evaluate the newly supported inputs; it never gives an automatic
pass. Exactly four pre-existing tests move their overflow fixtures to the new
ceilings, preserving all refusal, pre-read and pre-resolution assertions:

- `tests.test_binding_fill.BindingScaleBoundaryTests.test_resolved_record_overflow_stays_unsupported`
- `tests.test_binding_fill.BindingScaleBoundaryTests.test_component_boundary_and_pre_resolution_refusal`
- `tests.test_netlist_parity.FailureBoundaryTests.test_input_bounds_fail_unknown_before_unbounded_reads_or_resolution`
- `tests.test_connectivity_hierarchy.HierarchyTests.test_repeated_file_and_resource_limits_are_unsupported`

`tests.test_design_limits` exercises exactly-at and one-above component, net and
terminal boundaries through flat resolved graph conversion, hierarchical
resolution, native XML parsing and full schematic decompilation. It also checks
pre-library net refusal, exact assignment order/budget, and a combined-ceiling
1,024-component / 2,048-net / 8,192-terminal topology comparison. Fixtures are
original and generated without truncation, splitting or limit monkeypatches.

## Real pod and measurements

The native netlist from the ticket's pinned fome-mc source is read without design
edits. It reaches the annotation plan's next independent refusal verbatim:

```text
execution: failed
outcome: unknown
code: footprint_library_coverage
message: unsupported native pad type/geometry
```

That footprint coverage issue belongs to AGENTEE-300. Passing the native inventory
stage makes no claim about complete placement or plan success.

Runtime and memory measurements use `time.perf_counter`, `tracemalloc` and
`resource.getrusage` on generated ceiling fixtures; the worker result records
exact commands. A single traced host run measured:

| Probe | Wall seconds | Peak traced Python bytes | Process max RSS (KiB) | Result |
| --- | ---: | ---: | ---: | --- |
| flat resolution + exact topology at combined ceiling | 19.308 | 31,551,152 | 87,020 | ok/pass, 1,024 search visits |
| decompile at terminal ceiling | 15.498 | 36,485,509 | 99,272 | ok/pass |

These include fixture construction and run with tracing enabled while focused
consumer validation also used the host. RSS is the process high-water mark, not
an isolated allocation delta. The first probe resolves and validates both graph
inputs, then compares all three inventory ceilings together. The decompile probe
uses 1,024 components with eight pins each and eight coordinate nets.
These probes cover an unambiguous topology and compact geometry, not worst-case symmetry, diverse libraries or dense wire work.
The independent finite work guards remain necessary.
