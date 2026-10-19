# Actual telemetry pod core input bundle

AGENTEE-207, eight selected core components. This directory contains authored
selection metadata, decisions, complete core pin allocation and a recipe. Raw
vendor documents, supplier responses, stock CAD and source designs remain
external. It does not contain a complete BOM or generated pod.

## Reproduce the retained capture

From the repository root, with the frozen207 source and its reviewed producer
dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 fixtures/telemetry-pod/electrical/build_core.py \
  --out /tmp/pod-core-new \
  --root core=/tmp/agent463-pod-electrical-inputs \
  --root repo=. \
  --root flat=/tmp/agent463-ee176-flat/evidence \
  --root kicad=/usr/share/kicad \
  --root power=/tmp/agent463-pod-power-research
```

The output must not exist. Roots may point to retained copies elsewhere; all 60
input files must match `source-lock.json`. The recipe does no network access and
does not manufacture acquisition receipts. Different library versions or later
supplier captures require explicit new pins and review. A failed run may leave
inspectable inputs but cannot publish a qualified parts manifest.

`parts-core.json` uses existing `agent-ee.parts/1` and exact native acquisition
records. Qualification replays each supplier response, datasheet acquisition
and CAD member. It uses the reviewed 169 declaration for ESP32 pad 41 thermal vias
through `cad.declarations`. Stock values are the observed 2026-10-19 UTC values.

`pin-allocation.json` accounts for all 108 physical core pins, checked against
their captured symbol names/numbers. `part-decisions.json` is compiled against
the unchanged specification. Power and connector decisions remain explicitly
unresolved; the board-layer row is explicitly outside the part-identity
predicate. These records cannot make the full decisions check pass.

`part-refs-core.json` uses 174's typed sidecar. Its eight component identities
include 176's four deterministic CAN references. A complete design must extend
this file and the manifest to cover all physical components. `bundle.json`
indexes the resulting files and keeps `done:false`; it is an informational input
inventory, not a campaign seal or acceptance record.

## Electrical boundaries and remaining work

See [design decisions](design-decisions.md) before using the inputs. In
particular, CAN's source 5 V port maps deliberately to `+5V_CAN`, while sensor
power and ADC reference pickup use `POD_5V_SENSOR` at the connector. These are
different rails. The core ADC inputs are outputs of the proposed divided and
protected analog front end, not direct harness connections.

The full power/protection, analog conditioning, reference, antenna bias, support
passives, programming pads, AMPSEAL/SMA parts and associated assembly decisions
remain to be added.188c direct CSV compilation cannot establish indirect CAN or
analog functionality. Use explicit captured conductor contracts and separate
electrical evidence after the intermediate pins are fixed. No dummy components
or direct MCU-to-CAN-bus aliases are permitted.

The 85 C rating conflict and 776228-1 orientation conflict remain open. Source 149/
204 qualification, native design/ERC/DRC, four-layer routing, full requirements,
owner DFM/placement preview and bench criteria 8/9 remain incomplete.

## Origins

The tracked metadata and recipe are authored for agent-ee. Their hashes and
citations describe external material without redistributing it or granting new
rights. `Pod.kicad_sym` remains the existing owned 168 fixture. The 176 CAN library
and both source sheets remain external with their original custody and unresolved
source-review status. Supplier URLs and manufacturer facts are evidence to
review, not permission to purchase, publish vendor files or approve hardware.
