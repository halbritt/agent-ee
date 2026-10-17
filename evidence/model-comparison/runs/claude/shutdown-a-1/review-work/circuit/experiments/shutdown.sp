* Shutdown verification: RC per candidate netlist (work/netlist/design.xml)
* /IN = J1.1,R1.1 ; /OUT = C1.1,J1.2,R1.2 ; GND = C1.2,J1.3
* Stimulus: /IN grounded at t=0 (node in tied to 0), C1 IC=1V, no external load
R1 in out 10k
C1 out 0 100n ic=1
Vin in 0 DC 0
.tran 1u 6m uic
.control
run
meas tran vout_at_5ms find v(out) at=5m
let vfinal = v(out)[length(v(out))-1]
print vfinal
.endc
.end
