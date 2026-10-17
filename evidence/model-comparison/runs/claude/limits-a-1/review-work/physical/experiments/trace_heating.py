# Sanity check (not width-alone inference): compute current density and IPC-2152-style
# temperature rise for the 0.25 mm F.Cu tracks carrying the clamp current.
# Copper weight is NOT specified in the board file; assume 1 oz (35 um) and note it.
I = 1.2e-3          # A, clamp current through /IN and /OUT tracks
w_mm, t_mm = 0.25, 0.035
A_mm2 = w_mm * t_mm
J = I / A_mm2
print(f"cross-section = {A_mm2*1e6:.0f} um^2, J = {J:.3f} A/mm^2")
# IPC-2221 external: I = 0.048 * dT^0.44 * A_mil2^0.725  -> solve dT
A_mil2 = A_mm2 * (1000/25.4)**2
dT = (I / (0.048 * A_mil2**0.725))**(1/0.44)
print(f"A = {A_mil2:.1f} mil^2, IPC-2221 est. temperature rise at 1.2 mA: {dT:.2e} degC (negligible)")
