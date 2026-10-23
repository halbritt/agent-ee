# Routing attempt: krt on rc_filter

- Backend: krt 0.23.1 (owned-c2ac657d5617)
- Input: `rc_filter.kicad_pcb` from working tree @ None (fingerprint bf8ad78b4c9cb228)
- Scope: all, 3 nets; protect: existing; budget 60 s
- Candidate: `out/runs/rc_filter/ee103/20261022T193001.116461Z/candidate/project/rc_filter.kicad_pcb`

| Result | Status | Detail |
|---|---|---|
| process | PASS | exit 0, 22.52 s |
| routing | COMPLETE | 0 unconnected in scope, 0 total (input: 4) |
| validation | PASS | no new findings |

| Preservation check | Status |
|---|---|
| project_files | PASS |
| board_outline | PASS |
| layers | PASS |
| stackup | PASS |
| board_setup | PASS |
| nets_and_netclasses | PASS |
| pad_connectivity | PASS |
| footprints | PASS |
| zones | PASS |
| keepouts | PASS |
| copper_graphics | PASS |
| protected_copper | PASS |
| new_copper_netclass_geometry | PASS |
| routing_scope | PASS |
| net_completion | PASS |
| pour_integrity | PASS |

Native DRC (original rules and severities):

```json
{
 "violations_total": 0,
 "parity_total": 0,
 "new_violations": {
  "count": 0,
  "by_type": {},
  "by_severity": {}
 },
 "preexisting_violations": 0,
 "resolved_violations": 0,
 "new_parity": {
  "count": 0,
  "by_type": {},
  "by_severity": {}
 },
 "resolved_parity": 0
}
```

Unsupported or approximated by this backend:

- KRT moves copper gr_text and net-less copper graphics to silkscreen (not switchable on the CLI); the copper_graphics preservation check reports it
- KRT's cleanup may neck wide new segments near foreign pads without a flag; the new_copper_netclass_geometry check reports any new copper below its net class

Route statistics:

```json
{
 "new_tracks": 7,
 "new_vias": 1,
 "new_track_length_mm": 14.362,
 "new_track_length_mm_by_layer": {
  "B.Cu": 0.177,
  "F.Cu": 14.186
 },
 "new_copper_nets": 3,
 "removed_existing_tracks": 0,
 "removed_existing_vias": 0
}
```
