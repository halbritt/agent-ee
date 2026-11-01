# Compile a design statement

`agent-ee design` is the general path from explicit circuit and board decisions to
a captured engineering build. The existing `generate` command remains the narrow
RC driver; `generate.py` is unchanged.

```sh
agent-ee design capture fixtures/design-examples/i2c-sensor.json --out captures/i2c
agent-ee design compile fixtures/design-examples/i2c-sensor.json \
  --captures captures/i2c --out bundles/i2c
agent-ee build run bundles/i2c/build-plan.json --out runs/i2c
```

Use new output directories. Capture needs network access and local KiCad libraries
(default `/usr/share/kicad`; override with `--kicad-root`). Compile needs neither
network nor a native KiCad process. Native builds need the documented
[build runtime](build-workflow.md), including its router and placement engine.
In this campaign, run native jobs through `native-admit`; the retained run script
in the evidence directory records the exact production image and mounts.

## Statement format

The schema is `agent-ee.design/1`. The complete examples are
[`i2c-sensor.json`](../fixtures/design-examples/i2c-sensor.json) and
[`555-oscillator.json`](../fixtures/design-examples/555-oscillator.json).
Unknown fields are refused.

| Field | Design decision |
| --- | --- |
| `name` | Short project identifier, also used for native filenames. |
| `requirement_prefix` | 1–16 uppercase ASCII letters/digits, beginning with a letter; used for generated source requirement IDs. |
| `intent` | One paragraph describing the design and its limits. |
| `board` | `width_mm`, `height_mm`, `thickness_mm`, `copper_layers`, `max_component_height_mm`. |
| `parts` | Object keyed by reference; each part has `lcsc`, `value`, `lib_id`, `footprint`, `group`, and optional `connector: {rows, columns}`. Assembly type comes from the native footprint attribute. |
| `nets` | Object mapping each net name to a list of `REF.PIN` strings. Names have no native `/` prefix. Each net needs at least two physical terminals. |
| `no_connects` | List of `REF.PIN` strings. Every symbol pin needs one net or a no-connect entry. |
| `power_flags` | Unique net names that need virtual `PWR_FLAG` symbols. |
| `ground` | The declared ground net; currently `GND`. A virtual GND symbol supplies its native power name. |
| `connectors` | List of `{reference, edge, position_mm, inset_mm, rotation_deg, insertion}`. Edges are `top`, `right`, `bottom`, `left`; insertion is `from_top` or `through_edge`. Position is measured along the envelope edge's direction. Rotations are multiples of 90 degrees. |
| `decoupling` | List of `{capacitor, device, supply, return, max_distance_mm}`. Each pair is `[DEVICE.PIN, CAPACITOR.PIN]`; both endpoints must share a net. |
| `groups` | List of `{name, anchor, title}`. Members come from the parts' `group` fields. |
| `plane` | `{net, layer}`; choose a declared net and `F.Cu` or `B.Cu`. |
| `functional` | Nonempty list of `{text, witnesses}`. Each requirement needs independent topology witnesses. |
| `bench` | Nonempty list of physical follow-ups. These remain physical obligations, separate from executable file checks. |
| `placement` | Optional `{budget_s, seeds, steps_per_seed}`; defaults are 600 seconds, 4 seeds, and 100000 steps per seed. |
| `mechanics` | Optional `{access_owners, packages}`; see below. Omitted means no declared body/access model. |

The initial sourced fab template supports conventional FR-4, two copper layers,
1 oz outer copper and nominal 1.2 mm thickness. Other layer counts and nominal
thicknesses are refused explicitly. Multi-unit symbols and custom pads are also
outside this compiler's current subset. Symbol and footprint pad-number sets
must agree. Repeated physical lands with one number are allowed in that set,
but a repeated-number **decoupling endpoint** is refused: the compiler cannot yet
derive the native land UUID. Choose an unambiguous package or extend that
compiler capability; no pad is guessed.

## Functional witnesses

Wiring and intended function are separate statements. This example keeps the
connector's intended SDA pin fixed even when a circuit author changes `nets`:

```json
{
  "text": "J2 pin 3 shall carry SDA with a 4.7 k pull-up to V3V3.",
  "witnesses": [
    {"id": "j2-sda", "type": "pin_net", "terminal": "J2.3", "net": "SDA"},
    {"id": "sda-pullup", "type": "element_between", "class": "resistor",
     "nets": ["SDA", "V3V3"], "value": "4.7k"}
  ]
}
```

`pin_net` asserts a reference/pin on a named net. `element_between` asserts an
element class and value between two distinct nets. It can specify `lib_id` and
`pins` (two physical pin numbers); otherwise the compiler uses the corresponding
standard `Device` symbol with pins 1 and 2. Supported classes are `resistor`,
`capacitor`, `inductor`, `ferrite`, `fuse`, `diode`, `led`, and
`polarized_capacitor`. Rule IDs must be unique across the design. The compiler
converts label nets to `/NAME` and keeps the GND power-symbol net bare.

Each functional requirement maps to the generated `topology_rules` check. Exact
netlist parity maps only to the separate native topology requirement. Witness
values and pins are not inferred from `nets`; changing the wiring therefore
does not rewrite the functional oracle. The [topology rule evaluator](topology-rules.md)
checks structure and value equality, not electrical adequacy or analog behavior.

## Capture and compilation

Capture downloads exact JLC stock responses, datasheets, supplier component
records and models. Datasheets try `dataManualFileAccessIdUrl` before the HTML
landing URL. If that attempt fails and the same JLC row supplies
`dataManualOfficialLink`, capture retries the failed part at that manufacturer
URL. The first acquisition and its failure remain under `datasheet-attempts/`.
Partial captures remain on disk after failure; retry into a new directory.
Stock timestamps are generated by `jlc_stock_capture` and never edited. Builds
require stock evidence no older than 24 hours.

Local symbol subsets and footprints are retained in `cad/`. Model downloads use
the footprints' stock model references through the public
`models3d.fetch_stock_references` function; no placed board is required. Supplier
capture retains the full response, the exact UTF-8 byte span of its top-level
`result` object, and provenance plus byte offsets and hashes. Nothing uploads
project files or places an order.

Compile generates the spec and byte-located record, selected-part records, CAD
closure, circuit, layout, envelope, sourced project rules, groups, placement and
locality contracts, planes, supplier CPL policy, assembly profile, typed
obligations, requirement map and `agent-ee.build-plan/2`. It replays the existing
parts producer, pure schematic emitter and requirement compiler in a temporary
preflight before publishing the bundle. It never publishes a partial bundle.
The same design and captured bytes produce identical bundle bytes, regardless
of the output directory. Capture itself records real timestamps and is not
expected to reproduce identical bytes across network runs.

The JLC fab profile and original selected-fact transcription are package data.
The instantiated profile cites only `fab-sources/selected-facts.txt`; no vendor
PDF or captured vendor page ships in the repository. Project rule keys and
minima come from `scaffold.produced_rules`, including the native
`min_copper_edge_clearance` key. Routing sizes use the larger of the sourced fab
values and the pinned KiCad 10.0.7 native defaults, preserving its annular-width
rule. `project-rule-derivation.json` binds both sources and the selected values. CPL rotations come from footprint-local geometry
and captured supplier pin-1 geometry before a board exists. Supplier preview
remains a physical delivery obligation.

## Declared mechanics

Add `"mechanics": {"access_owners": ["J1"]}` to request the existing
[design-declared mechanics](build-workflow.md#declared-mechanics) path. Owners
must be declared fixed connectors. `packages` can override individual bodies
with `{size_mm: [x, y, z], offset_mm: [x, y]}` in the footprint's **local frame**.
Unspecified bodies use the bounding box of front courtyard line/rectangle
endpoints and the design's maximum component height. Curved or missing
courtyards require explicit sizes. The generated model and its cited
`analysis/body-basis.txt` plainly mark these dimensions as measurement stand-ins;
they are not measured physical bodies.

## Refusal feedback

Independent shape errors are collected before semantic validation; semantic
errors are also collected before compilation. For example:

```text
Design refused:
- board.copper_layers: only two copper layers are supported; set 2
- functional[0].witnesses: add independent pin_net or element_between assertions
```

Correct the named fields and compile again into a new output directory. Missing
captures, absent symbol pins, symbol/footprint mismatches, duplicate terminal
assignments, one-terminal nets, and off-edge connector positions are refused.
Decoupling citations include each stated distance in millimetres. Preflight
requires the existing source-authority checker to qualify them as hard
engineering requirements; an advisory citation cannot satisfy a file requirement.
Producer or native-stage failures retain their own evidence; a successful compile
does not predict a successful physical design.

## Real-run example and claims

The I2C example was derived from the retained second-design spike, replacing its
custom numbered mounting pads with the stock JST SH footprint and matching
mounting-pin symbol. The second example is a NE555 astable oscillator with
10 k timing resistors and 100 nF capacitors, designed independently of the spike
script. Its signed datasheet acquisition failed the complete-PDF check; the
manufacturer URL supplied by the same JLC row succeeded on the next capture.

The retained evidence root is
`~/.local/share/agent-ee/zeroasic-20261029-agent707/design-compile-r1/evidence`.
See its `REPORT.md` for exact commands, runtimes, failures and package outcomes.
All native jobs use the production image under admission. `accepted` means only
the typed software file claim. These runs do not measure supplier confirmation,
physical operation, component suitability, catalog-based part selection, review
seats, installation, or interrupted-build resume.
