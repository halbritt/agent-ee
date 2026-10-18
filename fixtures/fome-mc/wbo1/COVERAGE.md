# Wideband reference coverage

Source: fome-mc commit `df304f3321296f7e517e0d2e2aa52e1380d52993`,
captured offline from committed Git objects. See `blocks/wbo1/block.json` and
`blocks/wbo2/block.json` for the source paths and every payload SHA-256.
The owner-held rights statement is `fixtures/fome-mc/LICENSE-NOTICE.md`.
The AGENTEE-105 fixture/licence change supplies that shared file.

## Native checks

The original parent sheet binds the ten hierarchical ports to their captured
native names. Each channel has 58 physical components. `+5V` and `GND` enter
through global power symbols, in addition to the ten hierarchical ports.
The original empty board only meets the review API's board-input requirement;
it supplies no heater copper, layout or fabrication evidence.

Native ERC and netlist export execute on this parent and the exact captured
channel bytes. A native ERC defect is retained as a defect; this fixture adds
no waiver and does not alter the source design to pass it.

## Captured native interface

The check retains every wbo1 component, value and terminal on a conductor leaving
the original channel. It also checks U302 pin 15 (PB1) on GND, U402 pin 18 (PA8)
on `+3V3_WBO2`, and distinct conductors for U302 pin 5 (`VDDA_WBO1`), U302 pin 1
(`+3V3_WBO1`) and U402 pin 5 (`VDDA_WBO2`). The native regression test separately
requires U302 pin 18 to be disconnected. The current hardware-interface schema
has no disconnected-pin predicate, so that assertion supplies regression
evidence without claiming a sealed interface witness for an open pin.

## Adaptation

`adapt.py` invokes the public block instantiator, retains its immutable source
and instance record, and edits the mutable child. `wbo1-to-wbo2.json` supplies
references +100, ordered WBO1/WBO2 and sheet-namespace replacements, and the
U402 PB1-open/PA8-high straps. The writer removes the one PB1 wire, replaces
the PA8 no-connect with a global power-net label, and adds the PB1 no-connect.
It refuses changed or ambiguous source strap geometry.

Native tests compare regenerated symbols, values, footprints, population and
physical-pin conductors with the captured wbo2 projection, normalizing generated net names
by terminal membership through the existing block-inventory comparator.
The block-provenance check runs on the new candidate; the captured source
block's status never supplies board acceptance. Wrong references, omitted
renames, and omitted straps must remain visible differences.

## Unknown coverage

These are outstanding obligations, not passing checks:

- Independent initial reviewers and centralized adjudication are not run by
  this implementation worker, whose assignment forbids launching other agents
  or sessions. Checks-only runs remain incomplete.
- The offline Rev C comparison reports all 78 differences as unexplained in
  `upstream-parity.json`, using independently confirmed upstream blob identity.
  A sealed upstream-equivalence witness and independent disposition of those
  differences remain absent; DESIGN.md context supplies no automatic reason.
- The firmware `port.cpp` strap decoder is not a captured requirement source.
  CAN IDs and 500 kbps behavior remain pending, as the block manifest records.
- LSU 4.9 pump-loop behavior, 1.65 V virtual ground, Nernst ESR injection and
  heater control need active models and datasheet evidence.
- The parts list retains LCSC facts, including R310 = 61.9 ohm; no catalog
  snapshot, package/land-pattern, availability or derating approval is present.
- Q301 heater-FET heat-spreader geometry is absent: this capture contains no
  layout. Schematic connectivity cannot establish thermal copper adequacy.
- Port voltage domains/current bounds remain undeclared. Parts remain draft.

The manifest has no source-run references. Block validation must return
`unverified`, including after a passing adaptation regression test.
