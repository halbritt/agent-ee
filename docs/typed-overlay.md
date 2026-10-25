# Typed input overlays

`agent_ee.typed_overlay` derives a typed input revision from an immutable
captured input set without editing it. The overlay declaration
(`agent-ee.typed-overlay/1`) names the exact base (`spec.md`,
`spec-record.json` and `requirement-map.json` hashes), the obligation kind of
every source span, the map/2 dispositions that replace map/1 entries, the
judgment briefs for every declared judgment record, and any analysis file
whose requirement bindings must follow a clause partition. The tool runs no
check, invents no judgment record or witness, and produces no candidate.

```sh
PYTHONPATH=src python3 -m agent_ee.typed_overlay prepare BASE_INPUTS OVERLAY.json \
  --out DERIVED_INPUTS [--compile-against SCAFFOLD_CANDIDATE --receipt RECEIPT.json]
```

`prepare` refuses when the base hashes differ from the declaration, when an
overlay span names a check the base map does not declare, when an obligation
is missing for a captured clause, when a judgment brief and a declared
judgment record do not match one to one, or when the typed accounting itself
refuses (`Refused`, see [requirement-map.md](requirement-map.md)). Refused
or failed preparation leaves no output directory. Exit 1 is a structured
refusal, exit 2 malformed input or I/O.

The derived directory is the base copied byte for byte, with
`requirement-map.json` regenerated as `agent-ee.requirement-map/2`,
`obligations.json` added, each declared `derived_files` entry rewritten, and
`overlay-manifest.json` (`agent-ee.typed-overlay-manifest/1`) recording the
base inventory hash and file count, every unchanged file, every changed file
with base and derived hashes, the added file, the accounting counts by mode
and obligation kind, the judgment records declared versus present (always
empty here), and the scope limitations. Judgment records are not created: a
`judgment` span is declared pending until a real candidate-bound record and
its independent witness exist.

## Map derivation

A `mappings` entry is either `{"keep": true}`, which copies the map/1 entry
verbatim, or `{"spans": [...]}` with one span per obligation span. A span
has `byte_start`, `byte_end`, `id`, `mode` and the mode's payload: `checks`
lists check **ids** resolved from the base map's own declarations (no check
is re-authored), `operational` carries `criterion`, `judgment` carries
`record`, `bench` carries `criterion` and `procedure`, `limitation` and
`context` carry `reason`. The derived map keeps every other entry, the scope
template and the check definitions unchanged.

## Derived analysis bindings

`derived_files` currently supports `{"rebind": {clause id: span id}}` for an
interval-assertion document. The rewrite changes only the `requirement` of
the cited assertions and declared parameters and the `requirement_sources`
key and locator (the span's exact locator JSON, as the compiler emits it);
parameters, expressions, bounds, source path and spec hash stay as authored.
The manifest records both hashes so a reviewer can diff the two documents.

## Compiling against a native scaffold

With `--compile-against`, the derived declaration is compiled exactly as the
scaffold stage compiles it: the given retained scaffold candidate is copied
beside the output, its `requirements-map/` provenance is dropped, the derived
changed and added files are laid over it, and `requirement_map.compile_map`
runs with that inventory. The resulting `agent-ee.requirement-map-receipt/2`
carries `compile_root` (candidate path, overlaid files, inventory hash). The
retained candidate is never modified. This proves the typed accounting
against real native files; it is not a build, a review or an acceptance.
