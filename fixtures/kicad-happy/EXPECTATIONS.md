# Installed analyzer qualification

Use the original repository-authored RC fixtures; no external design is imported.
Control R1=10 kΩ, C1=100 nF gives τ=0.001 s and
fc=1/(2πRC)=159.154943 Hz. The seeded R1=100 kΩ gives τ=0.01 s
and fc=15.915494 Hz. A declared cutoff interval [150,170] Hz therefore
passes the control and fails the seed, independently of the analyzer's math.
The native ERC/DRC controls and seeded fixtures remain unchanged.

The installed v2.3.1 schematic output rounds cutoff to two decimal places.
The qualified calculation is `schematic/RC-DET`, low-pass topology, exact
component set C1/R1, `cutoff_hz` or `time_constant_s`. It is a calculation
record, not a native violation flag: the coordinator compares the declared bounds.
Unknown, missing, duplicated, suppressed or nonfinite calculation records do not
establish coverage. No absent native violation implies a passing evaluation.
