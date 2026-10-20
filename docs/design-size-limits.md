# Design-size ceiling audit (AGENTEE-298)

Status: blocked by an existing executable contract. This audit records current
behavior and a withdrawn experiment; it does not expand supported design sizes.

The expanded telemetry pod's native XML contains 327 physical components, 369
nets and 1,128 selected physical pins. The forward-annotation plan fails at the
native inventory stage with execution `failed`, outcome `unknown`, finding code
`native_netlist`, and message `component inventory limit exceeded`. This is an
inventory refusal, before placement feasibility can be evaluated.

## Current inventory bounds and consumers

The audit used `rg` over every `MAX_*`/`*LIMIT*` declaration and reference in
`src/agent_ee`, then inspected the inventory paths and their direct consumers.
No production constants changed in this audit.

| Owner and constant | Current | Proposed, not installed | Reason |
| --- | ---: | ---: | --- |
| `netlist_parity.MAX_COMPONENTS` | 256 | 1,024 | More than three times the real pod's physical component count; covers the roadmap's approximately 492-footprint FOME-MC rung with headroom. |
| `_connectivity_hierarchy.MAX_COMPONENTS` | 128 | 1,024 | Same ceiling before scoped elaboration. |
| `schematic_decompile.MAX_SYMBOLS` | 128 | 1,024 | Same component ceiling; currently also bounds library declarations. |
| `netlist_parity.MAX_TERMINALS` | 8,192 | 8,192 | Keep the finite physical-pin budget. |
| `_connectivity_hierarchy.MAX_TERMINALS` | 8,192 | 8,192 | Keep resolution, post-resolution and receipt validation bounded. |
| `schematic_decompile.MAX_PINS` | 2,048 | 8,192 | Align extracted pins with the shared electrical inventory. |
| Physical net count | No separate constant | 2,048 | Bound graph width; native nonempty nets are already indirectly bounded by the terminal inventory. |
| `schematic_decompile.MAX_WIRES` | 2,048 | 2,048 | Independent geometric segment guard. |
| `schematic_decompile.MAX_POINTS` | 8,192 | 8,192 | Independent coordinate-node guard. |
| `schematic_decompile.MAX_WORK` | 2,000,000 | 2,000,000 | Preserve the coordinate-count times segment-count work guard. |
| `netlist_parity.MAX_SEARCH` | 100,000 | 100,000 | Preserve exact-search exhaustion as unsupported/unknown. |
| `netlist_parity.MAX_BYTES` | 8,000,000 | 8,000,000 | Preserve native XML and parity JSON read guard. |
| `netlist_parity.MAX_LIBRARY_BYTES` | 32 MiB | 32 MiB | Preserve aggregate captured-library reads. |
| `schematic_decompile.MAX_LIBRARY_BYTES` | 32 MiB | 32 MiB | Preserve aggregate captured-library reads. |
| `symbol_library.BYTE_LIMIT` / `NODE_LIMIT` / `DEPTH_LIMIT` | 32 MiB / 2,000,000 / 128 | Unchanged | Shared S-expression parser guards, not component counts. |

`netlist_parity.terminal_inventory` enforces both graph component and terminal
ceilings. `resolved_inventory` reaches it after validating a passing resolved
receipt. `_resolve_captured_intent` separately refuses component overflow before
opening any captured libraries. Native XML flows through
`native_netlist.read_native_inventory` into that same graph boundary.
`forward_annotation._inventory` uses this reader and wraps its refusal in the
existing `native_netlist` finding code. There is no independent annotation
component-count constant to change.

`native_netlist.read_components` and `read_native_instances` deliberately expose
component/identity projections without the electrical parity graph policy. They
retain XML byte, 100,000-node and depth-32 guards. Applying parity bounds to those
projections would change an existing separate contract.

Hierarchy component bounds are checked in `validate_intent`. Its terminal bound
is passed into `connectivity_ir._resolve_flat_intent`, checked on the resolved
output, and rechecked by `validate_resolved`. Flat intent resolution currently
has no component-count ceiling: JSON is bounded to 8 MiB and declaration arrays
to 10,000 entries. Resolved-to-parity conversion applies the graph ceiling later.
The hierarchy also bounds declaration arrays to 10,000, sheet occurrences to 256,
depth to 16 edges, identifiers to 256 UTF-8 bytes and scoped projection to 8 MiB.
These independent guards must remain finite.

Decompilation enforces symbol/library, physical-pin, segment, coordinate-node and
work bounds in `_components`, `_nets` and the CLI manifest-read boundary. Its
consumers are `schematic_roundtrip`, and `fp_calibration` (which imports only the
pin-position helper). Exact parity policy is registered through `capabilities`,
`baseline` and `checks`. Other direct parity importers are `native_netlist`,
`schematic_roundtrip`, `rung1_qualified`, `netlist_extra`, `package_consistency`,
`native_binding`, `schematic_lint` and `rf_review` (the latter consumers also use
the existing typed exception). Downstream native inventory consumers include
forward annotation, binding and parts checks; changing the shared graph boundary
therefore needs regression validation beyond the parity module.

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

## Contract conflict requiring resolution

Three pre-existing tests pass on the assigned integration stack and failed when
the proposed higher ceilings are installed:

- `tests.test_binding_fill.BindingScaleBoundaryTests.test_resolved_record_overflow_stays_unsupported`:
  a valid 257-component flat resolved receipt must be refused by
  `resolved_inventory`. Raising its component ceiling admits that input.
- `tests.test_netlist_parity.FailureBoundaryTests.test_input_bounds_fail_unknown_before_unbounded_reads_or_resolution`:
  an old-ceiling component-overflow fixture must refuse before library reads;
  with the higher ceiling it reaches other validation instead.
- `tests.test_connectivity_hierarchy.HierarchyTests.test_repeated_file_and_resource_limits_are_unsupported`:
  the 129-component fixture must be unsupported. Once below the new limit it
  reaches invalid terminal references and becomes malformed.

A fourth existing test,
`tests.test_binding_fill.BindingScaleBoundaryTests.test_component_boundary_and_pre_resolution_refusal`,
also explicitly accepts 256 components and refuses 257, including before library
resolution. Its source independently confirms the same incompatible ceiling;
it was not run against the withdrawn experiment.

The assignment explicitly requires stopping when an existing expected verdict
would change, prohibits rewriting the test, and permits only named stricter
verdict rulings. No such ruling was supplied. No existing test was edited. The
experimental production changes were withdrawn; the coordinator must resolve
this conflict before implementation can be qualified.

## Withdrawn experiment and measurements

A local, uncommitted experiment used one shared constants module, aligned the
three readers, added a net guard, and replaced parity's recursive assignment
traversal with an iterative depth-first traversal. This last change is necessary
at 1,024 components: a unique exact mapping must not crash at Python's recursion
limit. Candidate order and the 100,000-step search budget must stay unchanged.
The experiment was withdrawn after the contract failures; these measurements do
not establish supported behavior on this branch.

An original generated inventory reached all three proposed limits simultaneously:
1,024 components, 2,048 nets and 8,192 pins. Python `tracemalloc` measured peak
allocations including fixture construction. `time.perf_counter` measured elapsed
wall time. Measurements are single runs on this host, with tracing enabled.

| Probe | Seconds | Peak traced Python bytes | Observed result |
| --- | ---: | ---: | --- |
| Combined ceiling inventory construction | 0.0381 | 2,912,676 | Accepted; 1,025 components separately refused. |
| 2,048 nets / 4,096 pins | 0.0878 | 1,546,250 | Accepted; 2,049 nets separately refused. |
| 8,192 pins | 0.2741 | 1,667,066 | Accepted; 8,193 pins separately refused. |
| Combined ceiling construction and exact topology comparison | 4.7628 | 8,920,251 | `ok/pass`, 1,024 search steps. |
| Decompile 1,024 colocated original terminal symbols | 0.4930 | Not measured separately | `ok/pass`; 1,025 symbols refused as `unsupported/unknown`. |

Maximum process RSS for that probe process was 44,616 KiB. This measures one
unambiguous topology and one compact decompilation fixture, not worst-case
search, library diversity or geometric work. Byte, terminal and search guards
were not raised. Decompiler pin/net boundary regression tests and exhaustive
consumer validation were not completed after the stop condition.

On the same experiment the real pod passed the native netlist stage and reached
`failed/unknown`, code `footprint_library_coverage`, message
`unsupported native pad type/geometry`. That independent refusal was preserved;
no footprints, metadata or source design files were changed to bypass it.
