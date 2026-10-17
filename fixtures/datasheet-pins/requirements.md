# Pin-map fixture requirements

This original, minimal symbol/board fixture tests library pin-name agreement.
For U1 the engineer selects TJA1051T applicability and Table 3 of the already
captured NXP revision 9 datasheet: pin 6 must be CANL and pin 7 must be CANH.
The control symbol agrees; the swapped symbol reverses those two names.
Native ERC, DRC and schematic parity must remain clean in both cases.

The symbol uses passive electrical types and explicit no-connect markers for
all eight pins. Its original eight-pad footprint is populated on an otherwise
empty board. This is a symbol-definition test, not a functional CAN circuit,
physical transceiver model, footprint qualification or fabricated design.
No native violations are excluded to hide the swapped names. KiCad's ordinary
ignored-check lists remain present in the native reports.

The symbol, footprint and board were authored for agent-ee under its MIT terms;
they are not imported KiCad-library or third-party design files. The existing
`references/TJA1051.pdf` is referenced in place, without downloading or copying
another PDF into the repository. Its original provenance and hash remain in
`references/SOURCES.md`. These fixtures grant no new PDF publication rights.
