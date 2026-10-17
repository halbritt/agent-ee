# Captured symbol context

The offline context tool uses AGENTEE-39's shared bounded parser and inheritance
resolver over explicit captured library bytes. Exact `library:symbol` selection
returns properties, description, every pin's number/name/unit/electrical type,
local nanometre coordinates and orientation, and the resolved source chain with
library path and hash. Common pins and repeated names remain list entries; a
name lookup returns every matching number. A `~` native no-name sentinel remains
in the context and its pin remains accessible by number. Native XML renders that
sentinel as an empty name.

Context JSON is deterministic for the same selected library bytes. The source
chain is consumed in the shared resolver's established order; this tool does
not implement another inheritance resolver. Cycles, missing symbols/parents,
malformed libraries, changed hashes and unsupported inheritance fail with an
unknown outcome. Queries are literal strings, never shell expressions or fuzzy
substitutions. No ambient KiCad library path is consulted.

Bounding boxes are per-unit for body style 1, including common unit/body
primitives. They bound local graphic centrelines and complete pin segments;
rendered text, labels and stroke thickness are excluded. Rectangles, polylines
and circles are supported. An unqualified primitive (including arcs) yields
`coverage: unsupported` with `bounds_nm: null` for that unit. A partial box is
never presented as complete. Missing bbox coverage does not invalidate an
otherwise qualified pin inventory or establish a design defect.

A query JSON has schema `agent-ee.symbol-query/1`, explicit `libraries` entries
`{name, path, sha256}`, and `selection` and `references`. For context, selection
is the exact string and references is `null`. For pin validation, selection is
`null` and references is a nonempty list of `{reference, lib_id, pin}`;
`pin` contains exactly one `name` or `number` selector. A reference cannot
identify more than one selected symbol. This is an explicit selection authority,
not automatic native schematic binding. Selecting all device pins does not
prove that an embedded schematic instance uses the same symbol or unit.

```sh
python3 -m agent_ee.symbol_context query.json --captures captured --out context.json
```

`agent_ee.library_tools` exposes the literal `find-symbol`, `list-pins` and
`find-footprint` operations. Symbol requests contain `{libraries, selection}`;
footprint requests contain `{manifest, query}`. Each answer records the request
hash plus the source-bound context or exact footprint matches. For example:

```sh
python3 -m agent_ee.library_tools list-pins request.json --captures captured --out pins.json
```

The [footprint tool](footprint-libraries.md) also has a literal query option.
These tools do not select parts or verify datasheets. They neither dispatch a
model nor invoke a shell. Output files must be new.

The `symbol_pins` coordinator check takes a single `declaration` parameter naming
a captured pin-validation query. Its captured symbol dependencies are immutable
in scope/2 and scope/3; the query remains candidate data. A missing pin on a
fully resolved selection gives a defect naming the reference, selected symbol,
selector and actual available pins. A missing selected symbol/library gives
failed/unknown coverage, independently of AGENTEE-39's intent-rejection mapping.
Scope/3 covers pin existence only. Native schematic binding, function-to-datasheet
conformance (the separate `pin_map` check), package choice and physical correctness
remain separate obligations.
