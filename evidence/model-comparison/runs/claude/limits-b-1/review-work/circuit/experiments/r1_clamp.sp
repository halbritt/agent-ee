* RC low-pass per work/netlist/design.xml; exercise condition:
* /IN = 48 V DC source, /OUT clamped to 0 V by external equipment (ideal 0 V source).
Vin in 0 DC 48
R1 in out 10k
C1 out 0 100n
Vclamp out 0 DC 0
.op
.control
op
print v(in) v(out) i(vin) i(vclamp)
let p_r1 = (v(in)-v(out))*(-i(vin))
print p_r1
.endc
.end
