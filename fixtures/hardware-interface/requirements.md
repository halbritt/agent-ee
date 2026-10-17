# Original synthetic interface requirements

## Structural interface

The contract explicitly requires a populated load and probe, input on X1.1,
output on X1.2, a direct output conductor to load and probe, distinct conductors
on opposite R1 pins, and direct anonymous connectivity R2.2 to R3.1. R1 may
change from 100k to 10k. Other topology and physical accessibility are unprotected.
This original fixture uses generated pin-only symbol graphics and no donor files.

## Functional response

With R1 10k, C1 100n and load 1Meg, a 1 V step at IN produces approximately
0.629 V at OUT after 1 ms (loaded DC gain 100/101; time constant 0.990099 ms).
R1 100k produces approximately 0.095 V and fails a declared 0.62..0.65 V bound.
Port and probe have explicitly authored connector-class pin-only symbols; the
passive simulator ignores their non-electrical bodies. The component reference
RLOAD is used solely to meet the existing passive simulator's supported naming
contract; interface protection derives exclusively from the owner declarations.
