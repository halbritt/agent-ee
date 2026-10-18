# Telemetry pod AMPSEAL-23 harness connector (AGENTEE-169)

The pod's harness connector is a TE AMPSEAL 23-position header, the same family
as the ECU's J1 and J2. This fixture binds the stock `Connector_Generic:Conn_01x23`
symbol to fome-mc's `ampseal-23` footprint, converted from KiCad 5 syntax, and
compares that footprint with TE customer drawing 776228.

## Files

| File | What it is |
| --- | --- |
| [`../../fome-mc/ampseal/request.json`](../../fome-mc/ampseal/request.json) | Capture request: fome-mc commit `df304f3`, the source path, the KiCad version |
| `../../fome-mc/ampseal/capture/` | Output of `agent_ee.footprint_capture`: the converted `ampseal.pretty`, its manifest and `capture.json` (commit, path, Git blob id, SHA-256, conversion records) |
| [`drawing-776228.json`](drawing-776228.json) | Dimension values read from TE drawing 776228 rev C14, the drawing copy's SHA-256, the coordinate frame, and the proposed part number |
| [`compare_land.py`](compare_land.py) | Land comparison: drawing positions from the dimension chain against the footprint's pads and peg holes |
| [`land-comparison.json`](land-comparison.json) | Output of `compare_land.py` for the captured footprint |
| [`footprint-libraries.json`](footprint-libraries.json), [`assignment.json`](assignment.json) | `footprint_assignment` inputs: the captured footprint mounted at `ampseal-capture/` and J1 as `Conn_01x23` from the stock library at `stock/symbols/` |

## Which footprint

Two footprints were candidates.

- fome-mc `libs/footprints/ampseal.pretty/ampseal-23.kicad_mod` has 23 plated
  holes of 1.75 mm drill in rows 1–8, 9–15 and 16–23 on a 4 mm grid, the middle row
  offset by 2 mm, and four 2 mm NPTH peg holes. That is drawing 776228's
  recommended board layout, a vertical header. Its 3D model points at a STEP file
  that is not in fome-mc, so the reference stays unresolved.
- Stock `Connector_TE-Connectivity:TE_AMPSEAL_1-776087-x_3Rows_23_P0.4mm_Horizontal`
  has the same contact grid but two 2.85 mm pegs. It is the horizontal 776087
  header, a different part. The comparison reports it as a mismatch.

The fome-mc file is the same Git blob (`2f09e3a`) as FOME-Tech Polygonus
`ampseal.pretty/ampseal-23.kicad_mod` at `b3176cb`. `docs/file-origins.json`
records both origins (T16 and T8). Under the owner's decision on derived upstream
content, the origin check reports it as derived.

## Acceptance

- `footprint_assignment` passes for J1 (`Conn_01x23`) on `ampseal:ampseal-23`. All 23
  pins match pads 1–23 one to one. The four unnumbered NPTH pegs get role
  `mechanical` from the index's native rule (AGENTEE-116). A `""` declaration for
  them would be stale and is refused.
- Pads 1, 8, 16 and 23, measured from the upper-left peg, equal the drawing to
  within 0.05 mm. They agree exactly, and so do all 23 contacts and the four pegs.
- The capture reads committed blobs at one full commit only. It refuses `HEAD`,
  branch names, short or unknown ids, and untracked, staged or linked files.
  Working-tree edits never enter the capture.
- A second, forced `kicad-cli fp upgrade` of the converted file gives the same
  bytes. A fresh first conversion differs only in new random UUIDs.

## Owner gate

The header part number `776228-1` (black, tin) is proposed, not confirmed. The
owner must confirm it before AGENTEE-167 records it as the pod's part. The
drawing PDF is a vendor document and is not committed. Its SHA-256 is in
`drawing-776228.json`.

## Reproduce

```sh
PYTHONPATH=src python3 -m agent_ee.footprint_capture capture fixtures/fome-mc/ampseal/request.json \
    --repo ~/git/fome-mc --out /tmp/ampseal-capture
PYTHONPATH=src python3 -m agent_ee.footprint_capture verify fixtures/fome-mc/ampseal/capture --repository ~/git/fome-mc
PYTHONPATH=src python3 fixtures/pod/ampseal/compare_land.py fixtures/fome-mc/ampseal/capture
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONPATH=src python3 -m unittest tests.test_pod_ampseal -v
```
