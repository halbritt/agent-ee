# Original adapter qualification fixture

These simplified original symbols, pads and requirements are test artifacts,
not manufacturer designs, production layouts or device models.

## Ideal fixture assumptions

The ideal adjustable three-pin LDO uses Vref=1.25 V, VIN=12 V and declared
load=0.1 A; its reference omits adjust-pin current. Rtop=720 ohm and
Rbottom=240 ohm give 5 V. The feedback seed Rtop=1000 gives 6.458333333 V.
Accepted ideal output range is 4.9–5.1 V. The native lookup is corroboration,
not the authority for the captured ideal reference assumption.

Power coverage is the complete non-ground native power_in/power_out pin
inventory: J2.1 source on +12V; U1.1 sink on +12V; U1.2 source on +5V;
U2.1 sink on +5V. Ground is explicitly GND. Loads are declared, not inferred.
USB J1 VBUS is +5V. A recognized protection component is required on that net;
this is a component-presence heuristic, not a surge/withstand rating test.
C1 must be a power/ground ceramic X7R capacitor with operating voltage 5 V,
maximum operating/rated fraction 0.5. Control 16 V gives 0.3125; seed 6 V gives
0.833333333. Neither capacitance tolerance nor DC-bias behavior is qualified.

Thermal criterion is explicitly a heuristic package-table estimate, accepting
native cap-proxy 0.1 A load, VIN 12 V, VOUT 5 V, ambient 25 C and generic
SOT-223 Rtheta=60 C/W (no board correction): 25 + (12-5)*0.1*60 = 67 C.
Maximum proxy temperature is 70 C. Ambient 40 C gives 82 C with the same
criterion. The native default Tjmax=125 C is retained but is not an acceptance
limit or a device-specific rating. No physical thermal performance is implied.

EMC criterion is the upstream ES-001 placement heuristic, only for native
supported protection classes with known footprint positions and an electrically
associated uniquely nearest connector within 50 mm. Maximum distance is the
native 15 mm threshold. Control RV1 at (52,30), J1 at (50,30): 2 mm. Seed RV1
at (70,30): 20 mm. D1 is exported as diode and upstream skips it for ES-001;
RV1 is an original varistor proxy supported by that branch. Empty via inventory
provides no ES-002 coverage. Trace routing, ESD clamping and EMC certification
are outside these proxy criteria.
