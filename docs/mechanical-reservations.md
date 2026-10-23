# Captured mechanical reservations

AGENTEE-489 adds optional mechanical reservations to the accepted board envelope.
They describe selected engineering restrictions for placement and a fresh check
of saved native coordinates. They do not establish enclosure, assembly,
manufacturing or routed-board qualification. The estimated 90 mm cowl height is
not an implicit height cap.

An envelope opts in with:

```json
{
  "mechanical_reservations": {
    "path": "mechanics/reservations.json",
    "sha256": "<SHA-256 of the exact captured document bytes>"
  }
}
```

The document, its cited source, and its original-board snapshot must all be
immutable captured inputs with matching hashes. The snapshot is a separate
unchanged board file, not the mutable candidate board. Missing captures, stale
hashes and mutable dependencies refuse binding. An envelope without this field
retains its existing behavior. A packing policy cannot supply this field itself;
the coordinator derives the internal binding from the envelope.

## Producer document

The closed schema is `agent-ee.mechanical-reservations/1`, with these top-level
fields:

| Field | Meaning |
| --- | --- |
| `schema` | The exact schema above. |
| `original_board_path`, `original_board_sha256` | Captured immutable native board snapshot and its raw digest. |
| `source` | `{path, sha256, locator, classification, author}` identifying the captured engineering declaration. `classification` must be `engineering`. |
| `reservations` | At most 1024 reservation records, enough to represent each component and its additional access spaces without dropping unresolved entries. |

The source bytes and attribution are retained. A locator or an author's label
does not convert an engineering choice into a manufacturer specification or an
owner decision. Any numeric derivation and uncertainty remain the producer's
explicit responsibility.

The provisional AGENTEE-486 reservation format is not this consumer schema.
Its producer must materialize supported cylinder or pad-access recipes into
conservative integer polygons, bind the original native identities, and preserve
unknown coverage. Renaming that document or copying its historical world
coordinates does not establish a supported reservation.

Each reservation has these exact fields; `owner` is present only for attached
frames:

| Field | Accepted values and meaning |
| --- | --- |
| `id`, `required` | Unique nonempty identifier and explicit Boolean. |
| `kind` | `solid` or `access`. |
| `frame` | `board`, `footprint`, or `pad`. |
| `owner` | Attached frame: `{ref, uuid}`; a pad frame also has `pad: {number, uuid}`. Both reference and native UUID must resolve. |
| `geometry` | `{polygons_nm: [...]}`: 1–64 simple polygons in exact integer nanometres, or `{polygons_nm: null}` for unresolved geometry. |
| `faces` | Board frame: nonempty subset of `F`, `B`. Attached frame: nonempty subset of `same`, `opposite`, relative to the owner's current face. |
| `height_nm` | Exact outward-from-face interval `[low, high]`, with `0 <= low < high`, or `null`. |
| `phases` | Nonempty list of applicable assembly-phase names. |
| `self_exclusion` | Explicit Boolean; board-fixed rows require `false`. Attached rows may exclude their exact native owner. |
| `coverage` | `{projection: "conservative-per-face", height: "covered" or "unknown", access: "covered" or "unknown", basis: "..."}`. |
| `uncertainty` | List of retained uncertainty statements; unresolved material coverage must also be marked `unknown` or `null`. |

Board-frame polygons stay in board coordinates. Attached polygons use the
original native footprint or pad's local frame. Translation and quarter-turn
rotation follow the current owner; a side flip reflects local Y and exchanges
the applicable faces. The binding checks the original UUID, pad roster,
shape/size/drill/layers, rigid pad poses, and captured copper offsets and their
coverage reason. This is not a complete native padstack or primitive equivalence
proof; the native writer's independent pad-inventory guard remains separate.
An attached owner containing a custom pad (`shape == 6` or `custom_geometry`)
is unsupported and refuses binding. A footprint's captured face is not permission
to fix its face or pose. Bound footprint and pad rotations must be exact native
quarter turns; other pad angles are unsupported and refuse binding.

This implementation uses conservative face projections. A producer must justify
that the projection covers the declared height/access interval; the consumer
does not infer a missing height from a courtyard or 3D model. Required unknown
geometry or coverage cannot pass. Projection overlap may conservatively reject
a layout even where a fuller three-dimensional model could show clearance.
The consumer checks solid–solid and access–solid projection pairs on shared
faces during shared phases. Two access spaces alone are not physical obstacles.
Same-owner exclusion applies to a pair only when both records explicitly allow
it. Even disjoint Z intervals may be conservatively rejected; interval metadata
is not a three-dimensional collision proof.

## Search and saved checks

The bound document retains both its raw captured digest and a canonical parsed
digest. Candidate policy identities include the binding. Partial searches may
temporarily omit unplaced owners; complete and saved checks require every bound
owner. Fixed reservations remain fixed, and attached reservations follow native
owner transforms with explicit self-exclusion.

Stage A packs blocks on a synthetic local canvas and defers board restrictions
to global arrangement. It does not search internal mechanical layouts. A
locally packed candidate alone is not mechanical acceptance. Global arrangement,
residual packing, member refinement and saved validation retain the complete
binding and functional block constraints. A rigid candidate that cannot meet
the restrictions may exhaust the finite search; this is not proof that no legal
internal layout exists. Producers must regenerate candidates when the complete
policy binding changes, rather than patching a candidate's policy digest.

The envelope derives a `mechanical-access` requirement. Bind it to a placement
check with exactly these parameters:

```json
{
  "criterion": "mechanical-access/1",
  "drc_check": "native-drc-check-id",
  "envelope": "mechanics/envelope.json"
}
```

The check binds the immutable original snapshot, then evaluates fresh native
facts from the saved candidate. It checks all declared phases; a caller cannot
select a convenient phase to omit another requirement. A successful native DRC
execution does not itself prove mechanical clearance. Final routed-coordinate
replay and any unresolved physical qualification remain separate obligations.
