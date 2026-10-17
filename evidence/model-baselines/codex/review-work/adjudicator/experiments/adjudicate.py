"""Read-only candidate audit. Run: /usr/bin/python3 -B adjudicate.py."""
import collections
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

BASE = Path('/work/experiments')
PROJECT = Path('/candidate/project')
EVIDENCE = Path('/evidence/work')
BASE.mkdir(exist_ok=True)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

cfg = BASE / 'kicad-config'
for folder in [cfg, cfg / '10.0']:
    folder.mkdir(exist_ok=True)
    for kind, subdir, suffix in [('sym', 'symbols', '.kicad_sym'), ('fp', 'footprints', '.pretty')]:
        files = sorted(Path('/candidate/stock', subdir).glob('*' + suffix))
        entries = ''.join(f' (lib (name "{p.stem}")(type "KiCad")(uri "{p}")(options "")(descr ""))\n' for p in files)
        (folder / f'{kind}-lib-table').write_text(f'({kind}_lib_table\n (version 7)\n{entries})\n')
env = os.environ.copy()
overrides = dict(KICAD_CONFIG_HOME=str(cfg), KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
                 KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints', KICAD10_TEMPLATE_DIR='/candidate/stock/template')
env.update(overrides)
commands = json.loads((BASE/'commands.json').read_text()) if '--reuse-checks' in sys.argv else []
def run(name, argv):
    start = time.monotonic()
    cp = subprocess.run(argv, cwd=BASE, env=env, capture_output=True, text=True, timeout=180)
    (BASE / (name + '.stdout')).write_text(cp.stdout)
    (BASE / (name + '.stderr')).write_text(cp.stderr)
    commands.append(dict(name=name, argv=argv, cwd=str(BASE), env_overrides=overrides,
                         returncode=cp.returncode, duration_s=round(time.monotonic()-start, 3),
                         stdout=name+'.stdout', stderr=name+'.stderr'))
    (BASE / 'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
    return cp

version = (BASE/'kicad-version.stdout').read_text().strip() if '--reuse-checks' in sys.argv else run('kicad-version', ['kicad-cli', '--version']).stdout.strip()
inputs = [PROJECT / ('Polygonus-Universal-Base.' + ext) for ext in ['kicad_pcb', 'kicad_sch', 'kicad_pro']]
inputs += [PROJECT / 'wbo-can-trans.kicad_sch', PROJECT / 'VR Conditioner.kicad_sch', PROJECT / 'VR conditioner.kicad_sch']
before = {str(p): sha(p) for p in inputs}
if '--reuse-checks' not in sys.argv:
    run('erc', ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm', '-o', str(BASE/'erc.json'), str(inputs[1])])
    run('drc', ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity', '--units', 'mm', '-o', str(BASE/'drc.json'), str(inputs[0])])
    run('netlist', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(BASE/'design.xml'), str(inputs[1])])
drill = BASE/'drill'
drill.mkdir(exist_ok=True)
if '--reuse-checks' not in sys.argv:
    run('drill', ['kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '-o', str(drill)+'/', str(inputs[0])])
else:
    run('drill-separated', ['kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '--excellon-separate-th', '-o', str(drill)+'/', str(inputs[0])])

def counter(v):
    return dict(collections.Counter(x['severity'] for x in v))

erc = json.loads((BASE/'erc.json').read_text())
drc = json.loads((BASE/'drc.json').read_text())
old_erc = json.loads((EVIDENCE/'erc/erc.json').read_text())
old_drc = json.loads((EVIDENCE/'drc/drc.json').read_text())
ev = [v for s in erc['sheets'] for v in s['violations']]
def normalized_erc(d):
    return [(s['path'], s['violations']) for s in d['sheets']]
rules = json.loads(inputs[2].read_text())['board']['design_settings']
audit = dict(python=platform.python_version(), kicad=version, input_sha256=before,
             alias_identical=sha(inputs[-1]) == sha(inputs[-2]),
             rules=rules['rules'], saved_exclusions=rules['drc_exclusions'],
             erc=dict(sheets=len(erc['sheets']), violations=len(ev), severity=counter(ev),
                      exact_violation_match=normalized_erc(erc)==normalized_erc(old_erc),
                      errors=[v for v in ev if v['severity']=='error']),
             drc=dict(violations=len(drc['violations']), parity=len(drc['schematic_parity']),
                      unconnected=len(drc['unconnected_items']), severity=counter(drc['violations']),
                      exact_violation_match=drc['violations']==old_drc['violations'],
                      exact_parity_match=drc['schematic_parity']==old_drc['schematic_parity'],
                      errors=[dict(index=i, **v) for i,v in enumerate(drc['violations']) if v['severity']=='error']))

import pcbnew
board = pcbnew.LoadBoard(str(inputs[0]))  # Never refill or save.
audit['pcbnew'] = pcbnew.Version()
footprints = {f.GetReference(): f for f in board.GetFootprints()}
def vec(v):
    return [pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)]
def pad(p):
    return dict(number=p.GetNumber(), uuid=str(p.m_Uuid.AsString()), position_mm=vec(p.GetPosition()),
                size_mm=vec(p.GetSize()), drill_mm=vec(p.GetDrillSize()),
                attribute=int(p.GetAttribute()), net=p.GetNetname())
audit['holes'] = [dict(**pad(p), plated=p.GetAttribute()==pcbnew.PAD_ATTRIB_PTH,
                      nominal_ring_mm=(pcbnew.ToMM(p.GetSize().x)-pcbnew.ToMM(p.GetDrillSize().x))/2)
                  for p in footprints['J1'].Pads() if p.GetNumber()=='']
audit['ground_pads'] = {ref: [pad(p) for p in footprints[ref].Pads() if p.GetNumber()==num]
                        for ref,num in [('C15','2'),('U7','4'),('C14','2')]}
audit['can_board'] = {ref: dict(dnp=footprints[ref].IsDNP(), value=footprints[ref].GetValue(),
                                pads=[pad(p) for p in footprints[ref].Pads()]) for ref in ['R36','R37','U20','U21']}
audit['ground_zone'] = [dict(uuid=z.m_Uuid.AsString(), net=z.GetNetname(),
                              gap_mm=pcbnew.ToMM(z.GetThermalReliefGap()),
                              spoke_width_mm=pcbnew.ToMM(z.GetThermalReliefSpokeWidth()))
                         for z in board.Zones() if z.m_Uuid.AsString()=='bc540b8b-3ffa-4b00-b211-3a91191a1f6d']

# Original embedded schematic properties, without loading or modifying the schematic.
tokens = iter(re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', (PROJECT/'wbo-can-trans.kicad_sch').read_text()))
def expression(token):
    if token != '(':
        return json.loads(token) if token.startswith('"') else token
    result=[]
    for t in tokens:
        if t==')': return result
        result.append(expression(t))
    raise ValueError('Unclosed expression')
tree=expression(next(tokens))
def children(obj, key):
    return [x for x in obj if isinstance(x,list) and x and x[0]==key]
audit['can_schematic'] = [x for x in children(tree,'symbol') if any(p[1]=='Reference' and p[2]=='R36' for p in children(x,'property'))]

xml=ET.parse(BASE/'design.xml').getroot()
components={x.attrib['ref']:dict(value=x.findtext('value'), dnp=any(p.attrib['name']=='dnp' for p in x.findall('property'))) for x in xml.findall('components/comp')}
nets={x.attrib['name']: sorted(n.attrib['ref']+'.'+n.attrib['pin'] for n in x.findall('node')) for x in xml.findall('nets/net')}
old=json.loads((EVIDENCE/'netlist/connectivity.json').read_text())
audit['netlist']=dict(sheets=len(xml.findall('design/sheet')), components=len(components), nets=len(nets),
                      exact_net_match=nets==old['nets'],
                      can_nets={k:v for k,v in nets.items() if k.startswith('/CAN')},
                      resistor_components={r:components[r] for r in ['R36','R37']})
assignments={pin:net for net,pins in nets.items() for pin in pins}
board_nets={f.GetReference()+'.'+p.GetNumber():p.GetNetname() for f in board.GetFootprints() for p in f.Pads() if p.GetNumber()}
audit['netlist']['pin_count']=len(assignments)
audit['netlist']['board_net_mismatches']={pin:dict(schematic=net,board=board_nets.get(pin)) for pin,net in assignments.items() if board_nets.get(pin)!=net}
audit['can_calculation']=dict(assumption='DNP parts contribute no resistive conductance; no module or harness internals inferred',
                              populated_carrier_termination_conductance_S=0,
                              hypothetical_one_120ohm_ohms=120, hypothetical_two_120ohm_ohms=1/(1/120+1/120),
                              conclusion='Configuration observation only; no declared required termination, bitrate or waveform result establishes failure.')
audit['input_hashes_unchanged'] = all(sha(p)==before[str(p)] for p in inputs)
(BASE/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
print(json.dumps({k:audit[k] for k in ['alias_identical','input_hashes_unchanged']}))
print('ERC',audit['erc']['severity'],'match',audit['erc']['exact_violation_match'])
print('DRC',audit['drc']['severity'],'match',audit['drc']['exact_violation_match'])
print('Netlist',audit['netlist']['pin_count'],'mismatches',audit['netlist']['board_net_mismatches'])
