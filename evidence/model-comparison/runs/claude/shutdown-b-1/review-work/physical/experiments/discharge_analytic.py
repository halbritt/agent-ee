# Analytic check of the shutdown requirement.
# Circuit (from work/netlist/design.xml): R1 100k between /IN and /OUT,
# C1 100n between /OUT and GND. Scenario: C1 charged to 1 V, /IN tied to
# GND at t=0, no external load -> C1 discharges through R1 only.
import math
R = 100e3   # R1 value saved in schematic ("100k")
C = 100e-9  # C1 value saved in schematic ("100n")
tau = R * C
t = 5e-3
v = 1.0 * math.exp(-t / tau)
req = 0.01
tau_needed = t / math.log(1.0 / req)
r_needed = tau_needed / C
print(f"tau = R*C = {tau*1e3:.3f} ms")
print(f"V(/OUT) at t=5 ms = {v:.6f} V  (requirement <= {req} V)")
print(f"pass: {v <= req}")
print(f"margin factor over limit: {v/req:.1f}x")
print(f"tau needed for 0.01 V at 5 ms: {tau_needed*1e3:.4f} ms")
print(f"=> with C=100n, R1 would need <= {r_needed/1e3:.3f} kOhm")
print(f"time for saved values to reach 0.01 V: {tau*math.log(100)*1e3:.2f} ms")
