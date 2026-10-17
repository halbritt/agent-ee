# Explicit native stackup RC control

`rc-explicit-stackup.kicad_pcb` derives only from the repository's original
[RC control](../../../fixtures/rc/PROVENANCE.md). On 2026-10-17 a scratch copy
received an explicit saved native stackup: F.Cu 0.035 mm, dielectric 1 core
1.53 mm, B.Cu 0.035 mm. KiCad pcbnew 10.0.7 loaded, saved and reloaded the
board in `agent-ee:integrated-11-13` (image `90896f8f91f0`), with an explicit
stackup flag and native total thickness of 1600000 nm asserted.

The envelope used with this control names its core `dielectric 1`, as the
native file does. No external design assets or library copies were added.
The existing envelope's nominal 1 oz copper remains an unproved manufacturing
request: this fixture proves saved structural thickness, never a conversion
from thickness to ounces or the material of a fabricated board.
