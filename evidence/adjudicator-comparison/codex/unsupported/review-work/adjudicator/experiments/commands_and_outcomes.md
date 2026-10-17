# J1.3 ground adjudication

Criterion: `/candidate/requirements.txt` requires J1.3 ground. Only saved
connectivity is evaluated. No voltage, performance, or qualification limits
are assumed.

Read-only inspection commands:

- `rg -n -A 10 -B 4 'ddcb81c6|zone|b71a70d7|7deb1fb0' /candidate/project/rc_filter.kicad_pcb`
- `cat /evidence/work/netlist/connectivity.json /evidence/work/drc/drc.json`
- `kicad-cli version` returned 10.0.7.
- Python API docstring inspection identified `GetConnectedItems`.

Verification command:

```
timeout 20s /usr/bin/python3 -B /work/experiments/verify_ground.py > /work/experiments/verify_ground.json 2> /work/experiments/verify_ground.stderr
```

Exit status 0. Python 3.12.3; pcbnew 10.0.7-10.0.7~ubuntu24.04.1.
The board hash matches the packet. The script loads the original board,
traverses KiCad saved physical connectivity from J1.3, and reaches the GND
zone, GND via, C1.2 ground track, and C1.2. The original board explicitly
assigns J1.3 to GND. The exported schematic also groups C1.2 and J1.3 on
GND. Supplied DRC has zero unconnected items, violations, and parity issues.

An initial script attempt used `str(item.m_Uuid)`, which gives an unstable
SWIG proxy representation; that attempt did not complete. The final script
uses `item.m_Uuid.AsString()` for stable visited-node keys. Only the successful
final run is used as connectivity evidence.

Limitations: saved fill only; no refill, board save, repair, or hardware test.
pcbnew printed three PROPERTY_ENUM assertions, also present in the packet's
geometry log. The successful traversal and assertions completed despite these
diagnostics. No claim depends solely on those diagnostics or on net names:
the physical connectivity traversal and supplied DRC independently support
the absence of the alleged disconnection.

Decision: dismiss challenger/one. Its alleged mechanism is contradicted by
the original saved pad assignment and copper connectivity.
