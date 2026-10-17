# Qualification reference

For the ideal first-order unloaded RC topology and positive step V, t > 0,
Vout = V(1-exp(-t/(R*C))). Its derivatives with respect to positive R and C
are negative. Independent interval endpoints therefore establish extrema for
this scalar response throughout the rectangular R/C domain. Zero initial
voltage is required. No other topology, objective or correlated variation is
qualified by this argument.

A monotonic 1 ns input ramp lies between ideal steps at zero and 1 ns. Its
response difference is at most V*1e-9/(R*C), using 1-exp(-x) <= x. Each native
measurement must agree with the ideal-step solution within that allowance plus
0.0001*V numerical error. The published envelope expands measured endpoint
extrema by the largest such allowance; numerical uncertainty is retained.

Native tests independently use R=10000 ohm, C=1e-7 F, V=1 V and t=0.001 s.
Expected nominal is 0.6321205588285577 V. The four endpoint time constants are
0.00081, 0.00099, 0.00099 and 0.00121 seconds. No external design or model is used.
