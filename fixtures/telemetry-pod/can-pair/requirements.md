# CAN routing software probe

can-probe-routing: Route CAN+/CAN- through the declared CAN native class, with
0.25 mm leg width, 0.35 mm nominal gap, native gap range 0.34–0.36 mm,
at most 15.1 mm individual copper lengths and at most 0.15 mm skew. The aligned terminals
are 10 mm apart. A controlled connected five-millimetre leg extension must fail
skew while remaining inside the individual length range.

These are original software-fixture bounds, not an owner-approved pod geometry,
impedance target or physical CAN qualification. This two-terminal probe has no
real transceiver or connectors and its empty schematic does not establish parity.
A complete actual pod with planes or branched CAN connections is outside the
current routing_rules two-layer, zone-free, unbranched topology qualification.

The upper bound is a probe constraint only. No minimum copper length is inferred
from terminal-centre spacing; the native measurement excludes pad offsets.
