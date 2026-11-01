# Local acquisition ground returns

The film-capacitor reference now generates a short connection from the ADC and
filter grounds to the bottom ground plane, and a required check measures the
result on the saved board. This addresses a full-review concern that the earlier
candidate left unmeasured. It does not establish an ADC accuracy figure, RF
attenuation or EMC performance.

## What the review found

The previous candidate's front ground tree reached the plane only through the
regulator's exposed pad. Native inspection of that board measured these front
paths to the pad:

| Pad | Path to regulator pad |
| --- | --- |
| U2.3 | 20.549 mm |
| C4.2 | 14.384 mm |
| C5.2 | 27.242 mm |

The existing loop metrics ended at U2.3, so nothing measured the reference at
J3.2 and J3.4. The concern was deferred with empty witnesses. It was not a
proven hardware defect, and no numeric accuracy limit was violated.

## What the generator adds

`acquisition_local_routing.produce` calls `acquisition_ground_ties.seed` when the
geometry model declares `local_plane_returns`. It adds one through via and one
0.25 mm front segment outside each of U2.3, C4.2 and C5.2. Positions come from
the native pad frame and pad edge. The via keeps the existing 0.6 mm diameter and
0.3 mm drill, with the existing 0.2 mm clearance to the pad.

Generation preserves every existing board node and writes a receipt.
`acquisition_ground_ties.verify` checks the saved assignments before native
loading, because KiCad 10.0.7 changed an explicitly foreign net on a via that
touches ground copper to `GND`, during both save and load. It then checks the
loaded via, segment and pad frame against the receipt. Native DRC and the final
measurement are separate obligations.

The film geometry model keeps earlier model and checker revisions selectable
through `geometry-sources-r1.json` and `geometry-sources-r2.json`, so earlier
evidence retains its original identity.

## What the check measures

`engineering-geometry` runs `analysis/geometry_model.py`, which calls
`ground_reference.measure` on the saved board. These limits apply in addition to
every existing limit:

| Metric | Limit | Meaning |
| --- | --- | --- |
| `ground_saved_net_changes` | 0 | Saved via net differs from the loaded via, or a via is missing from either inventory |
| `ground_transition_missing` | 0 | An endpoint has no front-copper path to a ground through via |
| `ground_corridor_missing` | 0 | No constructed bottom-copper witness from the via to a J3 ground pin |
| `ground_plane_components` | exactly 1 | Connected regions of bottom ground copper, after subtracting drills |
| `ground_return_path` | 3 mm | Longest front path plus board thickness |

The 3 mm limit is an engineering selection that makes the plane connection
explicit. It is not a manufacturer requirement. Removing a tie does not always
create a missing transition: the checker then measures the longer real path to
another tie, and the path limit fails instead.

A corridor is a 0.25 mm grid path of 0.1 mm wide strips, with every edge proved
inside the saved copper. It starts on the via annulus and ends on the J3 pad's
own copper, inside the circle inscribed in the oval land and outside the drill.
The search visits at most 5,000 nodes per attempt; failure is reported as a
missing witness, not inferred from the net name.

The report's `ground_reference` section records each path, its conditional DC
resistance and an RF sensitivity table for C4 and C5. The DC estimate assumes
17.5 µm copper, 0.2 mm effective track width, a 20 µm via wall and the retained
0.13 Ω contact allowance. The constructed strip is deliberately narrow, so
parallel plane copper can lower the real resistance. The RF table is a lumped
sensitivity using selected parasitics, not a measured response.

[`ground-return-basis.md`](../fixtures/acquisition-film/ground-return-basis.md)
is the pinned note that reviewers receive. It includes the driver correction:
TI SBAS444E sections 7.5.1 and 9.1.7 state that the ADS1115 never drives SCL, so
only SDA sink current can flow through U2.3. Host-driven SCL and SDA currents
return through J2.1.

## Refreshing assembly stock

`acquisition_film.prepare(..., assembly_catalog=DIRECTORY)` attaches a complete
stock capture for the next candidate without changing `parts.json`, `circuit.json`
or any model-bound source. Create the directory with:

```sh
python3 -m agent_ee.jlc_stock_capture --ids-file ids.json --out NEW_DIRECTORY --allow-network
```

`ids.json` is a JSON list of the selected LCSC identifiers. Preparation raises
`ValueError` if the capture does not cover exactly those identifiers, changes a
selected MPN or package, or fails raw-response validation. The assembly check
keeps its 24-hour age limit. Capture again rather than editing a timestamp.

## Status

This work adds evidence for a new candidate. Fresh candidate checks, independent
judgments and the full engineering review must still evaluate the geometry, the
conditional arithmetic and their limits. Native continuity and DRC do not
establish physical accuracy.
