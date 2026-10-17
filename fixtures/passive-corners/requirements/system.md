# Owned RC endpoint qualification fixtures

These are coordinator qualification experiments using the repository's own RC
filter. They are not hardware acceptance or physical bench measurements.

R-response: exactly one 10 kohm resistor from IN to OUT and one 100 nF capacitor
from OUT to GND, ideal unloaded 1 V positive step rising linearly over 1 ns,
initial capacitor voltage zero, observed at 1 ms. Independent resistor and
capacitor relative tolerance intervals are both [-0.1, 0.1].

The control scope requires the complete measured envelope in [0.55, 0.72] V.
The defect scope deliberately requires [0.60, 0.68] V: nominal passes, endpoint
corners violate both bounds. The zero-tolerance experiment uses the control
limits with both intervals [0, 0] and must agree with the nominal simulation
within the declared numerical allowance.

I-netlist: export current native connectivity and preserve component identities.
