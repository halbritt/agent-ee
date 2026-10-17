import json, re, pathlib, hashlib, collections, xml.etree.ElementTree as ET, platform, subprocess
out=pathlib.Path('/work/experiments')
def parse(path):
    tokens=re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+',pathlib.Path(path).read_text())
    stack=[]
    for t in tokens:
        if t=='(':
            n=[]
            if stack: stack[-1].append(n)
            else: root=n
            stack.append(n)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root
def children(n,key): return [x for x in n if isinstance(x,list) and x and x[0]==key]
def one(n,key,default=None):
    a=children(n,key)
    return a[0][1] if a else default
def props(n): return {p[1]:p[2] for p in children(n,'property')}
report={'python':platform.python_version(),'commands':['python3 /work/experiments/audit.py'],'hashes':{}}
for path in list(pathlib.Path('/candidate/project').glob('*.kicad_*'))+list(pathlib.Path('/evidence/work').glob('*/*.json')):
    report['hashes'][str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
for c in ['erc','drc']:
    d=json.load(open(f'/evidence/work/{c}/{c}.json'))
    vs=[v for s in d['sheets'] for v in s['violations']] if c=='erc' else d['violations']
    report[c]={'version':d['kicad_version'],'count':len(vs),'severity':dict(collections.Counter(v['severity'] for v in vs)),'excluded_count':sum(v.get('excluded',False) for v in vs),'type':dict(collections.Counter(v['type'] for v in vs)),'ignored':d['ignored_checks']}
    report[c]['errors']=[{'sheet':s['path'],'index':i,**v} for s in d['sheets'] for i,v in enumerate(s['violations']) if v['severity']=='error'] if c=='erc' else [{'index':i,**v} for i,v in enumerate(vs) if v['severity']=='error']
    if c=='drc':
        report[c]['parity_count']=len(d['schematic_parity']);report[c]['unconnected_count']=len(d['unconnected_items'])
        report[c]['unexcluded_by_type']=dict(collections.Counter(v['type'] for v in vs if not v.get('excluded',False)))
board=parse('/candidate/project/Polygonus-Universal-Base.kicad_pcb')
fps={props(f).get('Reference'):f for f in children(board,'footprint')}
report['board_selected']={}
for ref in ['U11','U12','U13','U14','U16','U17','U18','U19','RN1','RN2','RN3','RN4','R1','R2','R36','R37']:
    f=fps[ref]
    report['board_selected'][ref]={'properties':props(f),'library':f[1],'attr':children(f,'attr'),'pads':[{'number':p[1],'kind':p[2],'net':children(p,'net'),'uuid':one(p,'uuid')} for p in children(f,'pad')]}
report['mounting_holes']=[]
f=fps['J1']
for p in children(f,'pad'):
    if p[1]=='':
        size=children(p,'size')[0][1:]; drill=children(p,'drill')[0][1:]
        report['mounting_holes'].append({'uuid':one(p,'uuid'),'kind':p[2],'size':size,'drill':drill,'nominal_annulus_mm':(float(size[0])-float(drill[0]))/2,'net':children(p,'net')})
pro=json.load(open('/candidate/project/Polygonus-Universal-Base.kicad_pro'))
settings=pro['board']['design_settings']
report['rules']=settings['rules'];report['saved_exclusions']=settings['drc_exclusions']
report['native_symbols']={}
for file in ['Lowside Adder.kicad_sch','Polygonus-Universal-Base.kicad_sch','wbo-can-trans.kicad_sch']:
    sch=parse('/candidate/project/'+file)
    chosen=[s for s in children(sch,'symbol') if props(s).get('Reference') in ['U11','U12','U13','U14','RN1','RN2','R1','R2','J2','R36']]
    report['native_symbols'][file]=[{'uuid':one(s,'uuid'),'properties':props(s),'dnp':one(s,'dnp'),'in_bom':one(s,'in_bom'),'instances':children(s,'instances')} for s in chosen]
xml=ET.parse('/evidence/work/netlist/design.xml').getroot()
report['netlist']={'sheets':len(xml.findall('./design/sheet')),'components':len(xml.findall('./components/comp')),'nets':len(xml.findall('./nets/net'))}
conn=json.load(open('/evidence/work/netlist/connectivity.json'))
report['selected_nets']={k:v for k,v in conn['nets'].items() if k in ['/LS9','/LS10','/LS11','/LS12','/IGN9','/IGN10','/IGN11','/IGN12','/CAN2+','/CAN2-'] or k.startswith('Net-(RN')}
report['passive_calculation']={'assumptions':'ideal 5V; undriven MCU; no A1/driver input loading; 1k pullup, 1k pulldown, 1k series','open_circuit_V':5*1000/(1000+1000),'Thevenin_ohm':1000+1/(1/1000+1/1000),'operating_margin_at_ideal_GPIO_V':3.3-3.5}
report['alias_identical']=pathlib.Path('/candidate/project/VR Conditioner.kicad_sch').read_bytes()==pathlib.Path('/candidate/project/VR conditioner.kicad_sch').read_bytes()
(out/'audit.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:report[k] for k in ['python','netlist','mounting_holes','passive_calculation','alias_identical']},indent=2))
print('ERC',report['erc']['severity'],'DRC',report['drc']['severity'],'DRC exclusions',report['drc']['excluded_count'],'Unexcluded',report['drc']['unexcluded_by_type'])
# Compare newly exported native connectivity and saved board numbering without editing either.
def mapping(root):
 return {(n.attrib['ref'],n.attrib['pin']):net.attrib['name'] for net in root.findall('./nets/net') for n in net.findall('node')}
original_map=mapping(xml); fresh_map=mapping(ET.parse(out/'fresh.xml').getroot())
parity=[]; checked=0
for ref,f in fps.items():
 for p in children(f,'pad'):
  if not p[1]: continue
  pn=children(p,'net'); actual=pn[0][-1] if pn else None
  expected=original_map.get((ref,p[1]))
  checked+=1
  if actual!=expected: parity.append({'ref':ref,'pad':p[1],'pcb':actual,'schematic':expected})
report['electrical_parity']={'numbered_pad_instances':checked,'mismatches':parity,'fresh_export_matches_packet_nodes':fresh_map==original_map}
# Read the freshly fetched supplier JSON-LD rather than assuming an SKU mapping.
html=(out/'references/LCSC.html').read_text()
ld=[]
for s in re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>',html,re.S):
 try: ld.append(json.loads(s))
 except json.JSONDecodeError: pass
report['supplier_json_ld']=ld
(out/'audit.json').write_text(json.dumps(report,indent=2))
print('Electrical parity:',report['electrical_parity'])
