# R1 stress under exercise condition: /IN = 12 V DC, /OUT clamped to 0 V (external clamp).
# Netlist (work/netlist/design.xml): R1.1 on /IN, R1.2 on /OUT, R1 = 10k (schematic Value).
V = 12.0
R = 10e3
I = V / R
P = V * V / R
print(f"I(R1)  = {I*1e3:.3f} mA")
print(f"P(R1)  = {P*1e3:.3f} mW")
print(f"Limit  = 100 mW (exercise-stated continuous limit)")
print(f"Margin = {0.1/P:.2f}x  -> {'PASS' if P <= 0.1 else 'FAIL'}")
# C1 (100n) sees /OUT clamped to 0 V vs GND -> 0 V DC across it; no stress.
# Connector/track current 1.2 mA; GND net carries no current in this scenario
# (return is through the external clamp, not board GND).
