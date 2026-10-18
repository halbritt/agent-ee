# Original design-diff fixture

`pair.kicad_pcb` was hand-authored for AGENTEE-51 and then re-saved once with
`kicad-cli pcb upgrade --force` (KiCad 10.0.7), so its bytes are KiCad's own
writer output. It contains no donor design and no stock footprint graphics: two
inline two-pad footprints `R1` (1k) and `R2` (2k2) on nets `/A`, `/B` and `GND`,
one 0.25 mm `/B` segment and a 14 x 8 mm outline. Its UUIDs are `uuid5` values
under a fixed fixture namespace.

The fixture exists to exercise the [canonical design diff](../../docs/design-diff.md):
UUID churn, footprint moves above and below the declared precision, and net
renames. It is not an electrically complete design and qualifies nothing beyond
the diff.
