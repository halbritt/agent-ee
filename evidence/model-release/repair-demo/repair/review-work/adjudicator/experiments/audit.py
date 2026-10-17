from pathlib import Path
import hashlib, json, math, platform, re
root = Path('/candidate')
e = Path('/evidence/work')
paths = ['requirements/fixture-requirements.md', 'project/rc_filter.kicad_sch', 'project/rc_filter.kicad_pcb']
sch = (root / paths[1]).read_text()
pcb = (root / paths[2]).read_text()
segments = re.findall(r'\n\t\(segment\n(.*?)\n\t\)', pcb, re.S)
widths = [float(re.search(r'\(width ([\d.]+)\)', s).group(1)) for s in segments]
c = json.loads((e / 'netlist/connectivity.json').read_text())
assert c['nets'] == {'/IN': ['J1.1', 'R1.1'], '/OUT': ['C1.1', 'J1.2', 'R1.2'], 'GND': ['C1.2', 'J1.3']}
for ref, value in [('R1', '10k'), ('C1', '100n')]:
    assert c['components'][ref]['value'] == value
    assert f'(property "Value" "{value}"' in sch
    assert f'(property "Value" "{value}"' in pcb
assert len(widths) == 5 and min(widths) >= 0.2
assert not json.loads((e / 'erc/erc.json').read_text())['sheets'][0]['violations']
drc = json.loads((e / 'drc/drc.json').read_text())
assert all(not drc[k] for k in ['violations', 'unconnected_items', 'schematic_parity'])
log = (e / 'rc-step/ngspice.log').read_text()
measurements = {k: float(v) for k, v in re.findall(r'(vout_\dms)\s*=\s*([\d.eE+-]+)', log)}
assert 0.62 <= measurements['vout_1ms'] <= 0.65
assert abs(measurements['vout_1ms'] - (1-math.exp(-1))) < 1e-6
deck = (e / 'rc-step/deck.cir').read_text()
assert 'C1 OUT 0 100n' in deck and 'R1 IN OUT 10k' in deck
assert 'VSTEP IN 0 PWL(0 0 1n 1)' in deck
out = dict(python=platform.python_version(), native_sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}, native_track_widths_mm=widths, exported_nets=c['nets'], measurements_V=measurements, analytic_1ms_V=1-math.exp(-1), erc_violations=0, drc_violations=0, drc_unconnected_items=0, drc_schematic_parity=0, supplied_finding_ids=[], outcome='Requirements corroborated; no finding IDs supplied to adjudicate.')
Path('/work/experiments/audit.json').write_text(json.dumps(out, indent=2)+'\n')
print('All evidence audit assertions passed.')
