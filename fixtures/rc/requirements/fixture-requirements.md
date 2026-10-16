# Declared fixture requirements (RC low-pass review exercise)

These are artificial review requirements declared for this demonstration
fixture. They are not ECU electrical requirements, and they are fixed for the
whole demonstration (recorded before any review run).

1. Step response: with an ideal 1 V step at /IN and no external load, the
   voltage at /OUT at t = 1 ms after the step must satisfy
   0.62 V <= V(/OUT, 1 ms) <= 0.65 V.
   (Intended design: R1 = 10 kohm, C1 = 100 nF, tau = 1 ms,
   V(1 ms) = 1 - exp(-1) = 0.6321 V.)
2. Geometry: every copper track segment on the board must be at least
   0.20 mm wide. This is a declared geometric requirement for the exercise,
   not an ampacity or manufacturing guarantee.

Scope limits: the ideal R/C simulation establishes only the unloaded step
response. Tolerance, temperature, loading, ADC sampling, EMI and hardware
behavior are untested by this fixture.
