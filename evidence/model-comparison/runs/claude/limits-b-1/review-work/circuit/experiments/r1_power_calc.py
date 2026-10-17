# R1 dissipation under exercise condition (inputs/requirements.txt):
# /IN held at 48 V DC, /OUT clamped to 0 V (clamp external), indefinitely.
# Netlist (work/netlist/design.xml): R1 pin1 = /IN, R1 pin2 = /OUT, value 10k.
# C1 (100n) sits from /OUT to GND; with /OUT clamped to 0 V it carries no DC
# current and does not affect R1 steady-state dissipation.
V = 48.0
R = 10e3
I = V / R
P = V * V / R
limit = 0.1
print(f"V across R1 = {V} V (48 - 0)")
print(f"I = V/R = {I*1e3:.3f} mA")
print(f"P = V^2/R = {P:.4f} W")
print(f"Limit = {limit} W; ratio = {P/limit:.2f}x; margin = {P-limit:+.4f} W")
