import hashlib, json, pathlib
D=pathlib.Path('/work/experiments')
base=pathlib.Path('/evidence/work')
rows={}
for name in ['erc','drc','netlist','geometry']:
    p=base/name
    ex=json.loads((p/'execution.json').read_text())
    console=(p/'console.log').read_bytes()
    assert hashlib.sha256(console).hexdigest()==ex['console_sha256']
    assert ex['exit_code']==0
    rows[name]={'argv':ex['argv'],'exit_code':ex['exit_code'], 'console_hash_matches':True,'console':console.decode()}
erc=json.loads((base/'erc/erc.json').read_text())
drc=json.loads((base/'drc/drc.json').read_text())
geo=json.loads((base/'geometry/geometry.json').read_text())
assert all(not s['violations'] for s in erc['sheets'])
assert all(not drc[k] for k in ['violations','unconnected_items','schematic_parity'])
assert len(geo['footprints'])==3 and len(geo['pads'])==7
assert len([x for x in geo['tracks'] if x['type']=='track'])==5
assert len([x for x in geo['tracks'] if x['type']=='via'])==1
assert len(geo['zones'])==1 and geo['unconnected_items']==0
rows['erc']['ignored_checks']=[x['key'] for x in erc['ignored_checks']]
rows['drc']['ignored_checks']=[x['key'] for x in drc['ignored_checks']]
rows['geometry']['warning_assessment']='Three PROPERTY_ENUM assertion diagnostics coexist with populated geometry output. Native pad/net counts agree with separately parsed board; these diagnostics do not establish a circuit defect. Geometry is not a transient measurement.'
rows['scope']='All four supplied checks are static/native. None supplies ADC models, switching stimuli, or 1 ms electrical voltage measurements.'
(D/'record_audit.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps(rows,indent=2))
