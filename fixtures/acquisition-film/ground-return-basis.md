# Local acquisition ground returns

The previous generated board connected the ADC and filter ground tree to the
bottom ground plane only through the regulator's exposed pad. Native inspection
measured 20.549 mm from U2.3 to that pad, and 14.384/27.242 mm from C4.2/C5.2.
The former local-loop measurements ended at U2.3 and did not evaluate the
measurement reference at J3.2/J3.4. The full review deferred this concern; it
did not establish a violated numeric ADC accuracy or RF attenuation limit.

The generator now adds one ground via outside each of U2.3, C4.2 and C5.2,
connected by a 0.25 mm front trace. Positions derive from native pad frames,
pad edges and the existing 0.2 mm clearance. Vias retain the existing 0.6 mm
diameter and 0.3 mm drill. The engineering-selected 3 mm maximum counts both
the front path and board thickness. It makes the local plane connection
explicit; it is not a manufacturer accuracy guarantee. Original component
poses, capacitor-to-device loops, circuit, parts and operating constraints
remain protected by their existing checks.

The final geometry check measures actual pad/trace contacts and through-vias,
then combines native bottom ground fill, pads and vias and subtracts drills.
It compares saved via net assignments with the loaded objects because native
KiCad can infer a different via net from touching copper during load or save.
It requires one connected copper region and constructs 0.1 mm wide copper
corridors to both adjacent J3 ground pins. J3.2 and J3.4 are oval lands with a
round drill, so each corridor ends inside the circle inscribed in the land and
outside the drill: on that pad's own copper, not on plane copper beside it.
The checker refuses to measure unless each is a plated GND land on the bottom
layer, so a corridor cannot end under a pad it does not connect to.
Every corridor segment must lie inside that saved copper. A bounded search
failure is a missing witness, not permission to infer a path from the net name. The numerical DC estimate uses
the retained 17.5 um copper and 0.2 mm effective trace-width assumptions,
an explicitly assumed 20 um via wall, and the full retained 0.13 ohm allowance
for joints and contacts. Parallel plane copper can lower the resistance of
the constructed path; the assumed material/contact values are not supplier
guarantees. Results include drop per 1 mA and a 25 mA network sensitivity.
The latter is an analysis stimulus, not a changed supply-current limit or
a newly guaranteed board-error bound.

Driver identity matters. TI ADS111x SBAS444E sections 7.5.1 and 9.1.7 state
that the ADC never drives SCL. Only SDA acknowledgements and transmitted low
bits add pull-up sink current through U2.3. Host-driven SCL and SDA currents
return through J2.1. The captured pull-up maximum is 1.545455 mA per line;
charging two such currents to U2.3 is incorrect. ADC current, R6 discharge
current and connector-plane currents still need evaluation. The source's
8 SPS current/error test conditions must not silently become 128 SPS guarantees.

The RF table is a conditional lumped-element sensitivity: native local trace
length, 0.25/1/5 nH per mm, 0.5/1/2 nH via inductance, 9.5/10.5 nF, and 990 ohm
series resistance. These inductances are selected sensitivity values, not
measured or guaranteed parasitics. The table excludes distributed plane
impedance, capacitor package ESL, mutual coupling and external EMI excitation.
It must not be used as guaranteed RF attenuation or antialias performance.
TI sections 10.2 and 11.1 support short low-impedance connections, plane
connections and consideration of trace resistance/inductance; they do not
validate this numerical model. The original DC–10 Hz scope remains operative.

The ground term belongs alongside source loading and applicable ADC error.
It is not absorbed by the ADC's typical noise or offset specification. Fresh
candidate-bound judgments must assess the geometry, conditional arithmetic and
remaining applicability limits. Native continuity and DRC do not establish
physical accuracy, EMC performance or a calibrated result.

The generator also adds two 0.81 mm B.Cu bars, one across each pair of U1
thermal holes. Those holes keep the supplier's 0.6096 mm land and 0.3048 mm drill,
which gives a 0.1524 mm land ring. The captured JLCPCB page allows that for a via
but gives, for a 2-layer 1 oz plated hole, "Recommended 0.25 mm or above; absolute
minimum 0.18 mm". The output does not settle which class the supplier applies, so
the bars widen the copper to about 0.25 mm on B.Cu; on F.Cu the holes sit inside
the exposed pad. The holes stay 0.3048 mm with an open mask, below the page's
recommended 0.5 mm plated hole size, and supplier preview has not been performed.
The check measures unbroken saved copper around every plated pad hole on both outer
layers and requires at least the 0.18 mm minimum.

Primary source: captured `manufacturer-text/ads1115.txt`, TI SBAS444E,
sections 5.5, 7.5.1, 9.1.7, 10.2 and 11.1;
https://www.ti.com/lit/ds/symlink/ads1115.pdf.
