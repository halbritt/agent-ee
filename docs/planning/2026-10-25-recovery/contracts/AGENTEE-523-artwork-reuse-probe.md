# AGENTEE-523: bounded artwork reuse probe

Agent-564, 2026-10-25. Inspected source:
`903721fbcc4d7d9cc056759b0e45906cbbfd64d6`. One isolated native attempt,
followed by a read-only token comparison; no production repair or accepted
board is delivered here. This is execution finding REC-31, under the existing
finalization item, separate from the original audits.

## Observation and first failure

The existing `routed_artwork.produce` was invoked on the retained esp_prog
board whose SHA-256 is
`dfffc042e9f4c60dce9da0a25523443dd63dd3ecf56ee50a0a744a6cbdde92a1`.
The native runtime was the pinned KiCad 10.0.7 image
`sha256:6533216670ed0bbfabf5dc9d535ce25d59a35bb6c352dda2f99937484b958e7e`,
with source/input mounts read-only, no network and a 180-second bound.
The call was `produce(source, Path('/output/article'))`, with `targets=None`
explicitly allowing the existing graphic-translation path for this diagnostic.
This is broader than the build workflow's field-only target list; it is not
a new production authorization for arbitrary graphics.

After 6.602 seconds the producer refused with `ValueError: silk identity changed`
in `routed_artwork.write_scope`. The proposal had been saved but was not
qualified. The original bytes remained unchanged. The retained wrapper exits
normally after recording the exception; its process exit code is not success
of the artwork operation. Native import diagnostics remain in `run.log`.

The before and after files contain the same 791 unique UUIDs, item kinds and
nearest UUID-bearing owners. Native serialization changed the sibling position
of 131 identities: 121 `fp_line`, four `fp_text`, six `fp_circle`. The comparison
currently zips child lists positionally and therefore compares different UUIDs.
Its identity refusal is appropriate for that comparison, but the comparison
does not account for observed native sibling ordering.

For diagnosis only, `localize.py` aligns unique UUID-bearing siblings within
their unchanged parent and invokes the **unchanged** `write_scope` on the
aligned raw tokens. It preserves raw atom spelling and unkeyed positions;
it does not rewrite the retained proposal. The existing proof then reports
`allowed`, with 85 translated identities. This establishes that sibling order
is the first failing proof boundary in this article. Equal UUID sets alone
would not establish authorized modifications; the existing content checks
remain necessary.

## Selected repair boundary and remaining finalization

A narrow comparison repair may match supported UUID-bearing sibling items by
identity while proving the same parent, kind, unique identity set and every
non-authorized raw token. Unknown structure, duplicate IDs, changed owners,
additions/removals, altered copper/pads, unauthorized fields, style/content
changes and non-translation edits must still refuse. Field-pose requests must
still match their exact requested destinations. This recommendation does not
authorize sorting arbitrary syntax or weakening archival/stage evidence.

The producer's translation proof alone does not establish usable artwork.
AGENTEE-523 still needs explicit classification of movable fields, graphics,
rigid graphic groups and any drop-eligible artwork; component outlines and
multi-part logos must not be treated as freely disposable primitives. Final
native DRC, readable renders and the selected via/pad assembly assessment remain
separate acceptance requirements. Neither this historical board nor the
diagnostic proposal is M1 acceptance. No routing or placement search ran.

## Retained evidence

Private directory:
`/home/halbritt/.cache/agent-evidence/agent-ee-564-integration/finalization-artwork-probe/`.

| File | SHA-256 |
| --- | --- |
| `probe.py` | `202b073696746d239120dc902042629ef1bcad4690be157498d12e3195fe71e0` |
| `summary.json` | `b0847e4a18ae8765f1a5b466e375fe68eb62669748265e4f22aba28b62a3f13c` |
| `localize.py` | `efddb67afd0c9c711851f616c4a2038e7c651520253bfea49f234bd271e06025` |
| `identity-order-localization.json` | `7a053504e833e0bcb03f2c66036fb09ea928ef00547e62b640100dc309350094` |
| `run.log` | `0b46c17f6e33b99fa8881316cf8c31de2cfe6592545b8343da2ffe0ee01e0536` |
| `article/placement/placed/candidate.kicad_pcb` (unqualified proposal) | `128c7967e7775873472297e927f6867ca652bff0f5b80590e6d6194b3e5982e6` |

The isolated baseline is `article/placement/placed/baseline/candidate.kicad_pcb`
with the original hash above. The scripts and both boards are retained for the
focused repair's regression; no third-party board bytes were added to Git.
