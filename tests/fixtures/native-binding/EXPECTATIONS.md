Original owned native-binding expectations, written before the adapter extension
and binding implementation. `generate.py` writes every library, footprint and
schematic; no third-party library material is used.

Captured symbol library `Owned.kicad_sym`: `R` (2 passive), `Tri` (3 passive),
`Quad` (4 passive), `Dual` (unit 0 common pin 5 `V` power_in; unit 1 pins 1, 2;
unit 2 pins 3 and no_connect pin 4 named `~`), `Child` (extends `Tri`), `Conn4`
(4 passive), `GND` (power, 1 power_in). Schematics embed flattened copies, as
KiCad does.

Complete footprint manifest `Owned.pretty`: `R2` (2 SMD), `SOT3` (3 SMD), `SOT4`
(4 SMD), `SOT5EP` (SMD 1-5 plus a second thermal pad 5, a paste-only pad and an
unnumbered NPTH), `PinHeader_1x04`, `PinHeader_1x05` (through-hole).

Control: R1 R/R2; U1 Tri/SOT3; U2 Dual unit A only/SOT5EP; U3 Child/SOT3;
J1 Conn4/PinHeader_1x04 (through_hole, connector 1x4); R2 DNP R/R2;
R3 on_board=no with empty footprint; R4 in_bom=no R/R2; #PWR01 GND power.
Every on-board component is declared with exact native lib_id and footprint.
Expected: pass. R2 DNP is checked, not exempt. R3 is excluded only by its native
board-exclusion property. R4 BOM exclusion is recorded, not a board exclusion.
#PWR01 is omitted by the exporter and accepted only because its embedded
definition is a power symbol. U2 checks all five package pins although only
unit A is placed, and the repeated thermal pad 5 adds no pin.

Seeded variants (all must refuse pass):
- pin4: U1 Quad on SOT3 -> defect, U1, Owned:SOT3, missing pad 4.
- pin4 with U1 removed from the declaration and intent -> unknown (undeclared
  native physical component), never pass.
- extra native R6 not declared -> unknown.
- ghost declaration R9 absent from native -> defect.
- declaration lib_id Quad for native Tri U1 -> defect (lib_id mismatch).
- native U1 footprint edited to SOT4, declaration still SOT3 -> defect
  (footprint mismatch; parity runs on the native footprint).
- R1 and DNP R2 both on SOT3 -> defect for both references.
- R1 empty footprint -> defect empty_footprint; Owned:Nope -> absent_footprint.
- embedded Tri pin 3 retyped power_in, captured library unchanged -> unknown.
- embedded Tri with an extra pin 4 -> unknown.
- `#X1` non-power R omitted by the exporter -> unknown.
- `PW1` power symbol with non-# reference is exported to the board; with an
  empty footprint -> defect.
- J1 on PinHeader_1x05 -> defect (extra pad 5 and connector count).
- captured library drift after declaration -> failed/unknown.
- duplicate native footprint XML elements (either order) -> malformed.

Occurrence identity (repair, written before the resolver change). Owned
`kicad-cli` 10.0.7 probes: for one occurrence path, the last instance entry
wins whatever its project name; entries at paths outside the design's
hierarchy are ignored when the occurrence has its own entry; an occurrence
with no entry of its own takes another entry's reference; `#` omission follows
the selected reference. Project names, and a `.kicad_pro`, change nothing.
Binding must therefore resolve each actual (sheet occurrence, symbol UUID)
from the captured hierarchy and the native identities, never a union of every
instance entry:
- archived-alias: every root symbol also has an archived project entry at
  another root's path (`#PWR01` alias `#GHOST`) -> control declaration pass;
  `#PWR01` declared -> excluded, pass; `#GHOST` declared -> ghost_declaration,
  defect (never excluded).
- archived-same-path (archived `#GHOST` after `#PWR01` at the current path) and
  archived-same-path-first (before) -> occurrence identity ambiguous ->
  unsupported/unknown whichever of the two is declared.
- archived-same-path-exported (`PW9` after `#PWR01` at the current path): the
  exporter exports PW9 -> undeclared PW9 unknown; `#PWR01` is not excluded.
- reference-property-alias: `#PWR01`'s Reference property says `#GHOST`; the
  exporter uses the instance entry (ordinary-reference probes never export the
  property when an entry exists) -> `#GHOST` declared defect, `#PWR01` excluded.
- Two distinct omitted symbols resolving to the same `#` reference ->
  unsupported/unknown, never an overwritten exclusion.
- hierarchy: child sheet used twice, R11/R12 and `#PWR11`/`#PWR12` -> R11 and
  R12 declared pass; also declaring both power references -> excluded, pass.
- hierarchy-duplicate (`#PWR11` in both occurrences) and hierarchy-missing
  (no entry for the second occurrence) -> unsupported/unknown.
- hierarchy-archived (child also lists another root's occurrences, `R99` and
  `#GHOST`) -> pass; `#GHOST` declared -> ghost_declaration, defect.
- Native component identity not found in the captured hierarchy, a captured
  non-`#` occurrence missing from native, or native sheets differing from the
  captured hierarchy -> unsupported/unknown.
