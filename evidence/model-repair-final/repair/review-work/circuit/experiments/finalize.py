from pathlib import Path
import re, json, math
P=Path('/work/experiments')
# Supplemental read-only geometry inspection of the saved thermal relief.
s=Path('/candidate/project/rc_filter.kicad_pcb').read_text()
poly=[tuple(map(float,x)) for x in re.findall(r'\(xy ([\d.-]+) ([\d.-]+)\)',s[s.index('(filled_polygon'):])]
def inside(x,y):
    result=False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]: result=not result
    return result
points={str((100+dx,105.08+dy)):inside(100+dx,105.08+dy) for dx in [-.7,.7] for dy in [-.7,.7]}
(P/'saved-thermal-inspection.json').write_text(json.dumps(dict(diagonal_spoke_points=points,interpretation='Pad center and cardinal relief gaps are outside plane polygon by design. Four diagonal spoke points are in the saved fill; the J1.3 metal pad overlaps these diagonal spokes. Point tests supplement, but do not replace, native DRC connectivity inspection.'),indent=2))
# Preserve all original native schematic lines for precise references.
(P/'native-schematic-numbered.txt').write_text(''.join(f'{i}: {l}\n' for i,l in enumerate(Path('/candidate/project/rc_filter.kicad_sch').read_text().splitlines(),1)))
# Record inspection of every command outcome, rather than inferring from final script exit.
commands=json.loads((P/'commands.json').read_text())
receipts=[]
for c in commands['commands']:
    receipts.append(dict(name=c['name'],exit_code=c['exit_code'],stdout=(P/c['stdout']).read_text(),stderr=(P/c['stderr']).read_text()))
(P/'command-results.json').write_text(json.dumps(receipts,indent=2))
notes='''Review role: circuit. Review date: 2026-10-16.

Scope and assumptions
- Authority: inputs/requirements/fixture-requirements.md, requirements 1 and 2; ideal 1 V positive step, initially equilibrated at zero, unloaded output. No external interface electrical requirements or component MPNs/ratings are supplied.
- Read native schematic, PCB, project and library tables. No candidate files edited; SHA-256 before/after equality is in response-audit.json.
- Artifact citation mapping: /candidate/... -> inputs/...; /evidence/work/... -> work/...; /evidence/checks/... -> checks/...; this directory -> review-work/circuit/experiments/...

Commands and versions
- Main command: python3 /work/experiments/audit.py. Additional read-only inspection: python3 /work/experiments/finalize.py.
- Exact native check and simulation argv, environment overrides and exit codes: commands.json; stdout/stderr: command-results.json and individual files.
- Tools: KiCad CLI 10.0.7, ngspice 42, Python 3.12.3.
- Discovery commands used pwd, rg --files for AGENTS.md under /work /candidate /evidence (no AGENTS.md found), ls on /candidate/project /evidence/work /evidence/checks /work/experiments; command -v; version probes. Read-only cat and rg inspection covered fixture requirements, native project/library tables, original supplied JSON check reports, SPICE deck/control/log, and native Reference/Value/pad/net/segment locations. No credentials read, no external retrieval needed.

Native identity and behavior
- Schematic R1 (UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78) is Device:R, 10k, populated; C1 (UUID 1dcaf7b4-dc79-5865-a3a4-db0c3eaad5d8) is Device:C, 100n, populated.
- Independent native netlist export and direct saved board pad parse agree exactly: IN = J1.1/R1.1; OUT = J1.2/R1.2/C1.1; GND = J1.3/C1.2. The supplied exported netlist agrees as well. R and C use nonpolar passive symbols; connector exclusion removes no electrical element in this ideal unloaded case. Power flags/ground symbols express connectivity/ERC, not electrical supplies.
- The saved board includes a connected OUT T junction at (107.5,100), and ground from C1.2 via the front track to (113,100) via and saved back plane, with thermal relief at J1.3. Pad-center polygon membership alone is not a continuity test: pad copper and diagonal spokes bridge the relief. Supplemental sample points are recorded in saved-thermal-inspection.json. DRC reports no unconnected items or parity issues.
- All 5 native track segments are width 0.25 mm >= 0.20 mm. Geometry is directly parsed from the original native PCB, independent of pcbnew geometry probe warnings. No tool failure or electrical defect inferred from the original geometry console's property enum assertions.

Verification audit
- Original work/rc-step/deck.cir contains exactly C1 OUT 0 100n, R1 IN OUT 10k, and VSTEP IN 0 PWL(0 0 1n 1); no external load, clipping, substitute value, or behavioral model. Ground mapping is correct. Original work/rc-step/control.sp runs the sourced deck and writes the waveform. Original ngspice log includes an initial solution of zero volts and actual .meas results.
- Analytical ideal step: tau=R*C=0.001 s; Vout(t)=1-exp(-t/tau). At 1 ms: 0.6321205588 V, within [0.62,0.65] V. At 5 ms: 0.9932620530 V (no declared 5 ms bound).
- For ramp duration a=1 ns, ramp endpoint y(a)=1+(tau/a)*expm1(-a/tau); afterward y(t)=1-(1-y(a))*exp(-(t-a)/tau). Ideal-ramp voltage at 1 ms: 0.6321203749 V; error versus instantaneous step is about 0.184 microvolt, immaterial to the declared window.
- Read supplied original result.raw binary doubles, verified time/voltage/current column identity, size and monotonic times, and interpolated actual recorded samples. 1 ms = 0.6321201340 V; 5 ms = 0.9932621197 V; maximum sampled waveform error against exact ramp response is 0.763 microvolt. Thus the numerical data support the claimed measurements, not just successful execution.
- Independent ngspice deck uses native-exported values/topology with the same 1 ns ramp, 0.5 us max time step and tighter tolerances. Actual .meas values: 1 ms = 0.6321204 V, 5 ms = 0.9932621 V. See independent.cir, independent-ngspice.log and independent.raw. The scratch deck is verification only and does not modify or repair the candidate.
- Fresh native ERC and DRC JSON show empty violation arrays. DRC also has empty unconnected_items and schematic_parity arrays. Native exclusions/ignored rule categories are preserved in erc.json and drc.json, and were not treated as proofs of all circuit behavior.

Outcome and coverage omissions
- No evidence-supported circuit defect or unresolved concern against the stated fixture requirements.
- Only nominal ideal unloaded positive-step response through 5 ms is established. No tolerance, temperature, aging, source impedance, external loading, ADC sampling, EMI, parasitic extraction, real component nonlinear behavior, manufacturing/assembly or hardware testing.
- No part-specific voltage/current/power ratings are established: generic components have no MPN. No claim of rated hardware suitability is made.
- Falling step, arbitrary precharge, shutdown, reset and brownout cases are untested; no such requirements or active supply/reset circuits are supplied.
- Zone fill inspected as saved; no refill performed. Native ignored ERC/DRC categories remain untested by those checks. No external harness compatibility requirement supplied beyond IN/OUT/GND net identity.
'''
(P/'review-notes.md').write_text(notes)
print(json.dumps(dict(thermal_points=points,command_exit_codes={c['name']:c['exit_code'] for c in commands['commands']},notes='review-notes.md'),indent=2))
