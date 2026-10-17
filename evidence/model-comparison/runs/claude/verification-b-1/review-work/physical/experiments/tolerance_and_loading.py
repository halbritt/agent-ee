# Sensitivity of the 0.62..0.65 V @ 1 ms criterion to RC tolerance and ADC loading.
# Nominal: R=10k, C=100n -> tau = 1.000 ms exactly; V(1ms) = 1-exp(-1) = 0.63212 V.
import math

t = 1e-3
nom = 1 - math.exp(-1)
print(f"nominal V(1ms) = {nom:.5f} V (band 0.62 .. 0.65)")

# tau bounds that keep V(1ms) in band (ideal source, no load):
# V = 1 - exp(-t/tau) -> tau = -t/ln(1-V)
tau_hi = -t/math.log(1-0.62)   # upper tau bound (slower -> lower V)
tau_lo = -t/math.log(1-0.65)   # lower tau bound (faster -> higher V)
print(f"tau must lie in [{tau_lo*1e3:.4f}, {tau_hi*1e3:.4f}] ms")
print(f"=> combined RC tolerance window: {100*(tau_lo/1e-3-1):+.2f}% .. {100*(tau_hi/1e-3-1):+.2f}%")

# examples: symmetric RC tolerance that first violates the band
for tol in (0.01, 0.02, 0.0335, 0.05, 0.10):
    v_lo = 1-math.exp(-t/(1e-3*(1+tol)))   # tau high
    v_hi = 1-math.exp(-t/(1e-3*(1-tol)))   # tau low
    ok = (v_lo >= 0.62) and (v_hi <= 0.65)
    print(f" +/-{tol*100:4.1f}% RC: V in [{v_lo:.4f},{v_hi:.4f}]  {'PASS' if ok else 'FAIL'}")

# ADC charge-sharing bound (no ADC model invented; pure bound):
# worst case: sampling cap Cs initially at 0 V connects at t=1ms;
# instantaneous droop dV = V*Cs/(C+Cs). Cs needed to leave band (0.632->0.620):
V = nom
need = V - 0.62
Cs = need*100e-9/(V-need)
print(f"ADC charge-share bound: zero-charged Cs must exceed {Cs*1e9:.2f} nF "
      f"to pull 0.632 V below 0.620 V at the sampling instant")
# continuous load: leakage/ switch R forms divider; R_load needed to drop final value
# steady-state with load Rl: Vf = Rl/(Rl+10k). At t=1ms with load the value drops further;
# bound: even the *final* value stays >0.62 only if Rl > 10k*0.62/0.38
Rl = 10e3*0.62/0.38
print(f"DC-load bound: continuous load resistance below {Rl/1e3:.1f} kOhm makes even the final value < 0.62 V")
