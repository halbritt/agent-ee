# Original decompiler fixtures

`Owned.kicad_sym` and `imu.kicad_sch` were authored for AGENTEE-42 in this
repository. The invented four-pin sensor interface, host header, two-pin
capacitor and one-pin graph-control symbol contain no third-party design data.
They use the repository's MIT license. There is no manufacturer claim, MPN,
purchase approval or real sensor electrical specification.

The IMU-style reference deliberately connects every pin: VCC connects U1.1,
J1.1 and C1.1; GND connects U1.2, J1.2 and C1.2; SCL connects U1.3 and J1.3;
SDA connects U1.4 and J1.4. Sensor library pin positions are (0,0), (−2,−2),
(1,−4), (3,−6) mm in library coordinates. All orientations are zero; the source
instance is at (20,20) mm. Expected source endpoints are (20,20), (18,22),
(21,24), (23,26) mm. The host header and capacitor have vertical two-millimetre
pin spacing. All instance values differ so no accidental instance symmetry can
hide a pin swap. UUID anchors additionally require exact source correspondence.

`tests/schematic_decompile_fixture.py` constructs additional original native
controls solely for tests. Expectations are explicit pin-number memberships,
checked first against independent KiCad export, then against extraction. It is
not an application schematic emitter. No SchGen code or other third-party
source was copied; the implementation uses the existing shared S-expression
reader and resolver with an independently implemented bounded union-find graph.
