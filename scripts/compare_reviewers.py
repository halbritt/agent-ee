#!/usr/bin/python3
"""Bounded real subscription comparison. Outputs are measurements, not model votes."""
import argparse
import concurrent.futures
import json
import shutil
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from agent_ee.cli import run_review, save_result, verify
from agent_ee.findings import collect_findings
from agent_ee.reviewers import build_packet, initial_reviews
from agent_ee.storage import read_json, write_json, digest

CASES = [
 ('mapping-a', 'External connector contract: J1 pin 1 is the input, pin 2 the filtered output, pin 3 ground. Verify saved connectivity.', 'control', 'control', 'development'),
 ('mapping-b', 'External connector contract: J1 pin 2 is the input, pin 1 the filtered output, pin 3 ground. Verify saved connectivity.', 'control', 'defect', 'development'),
 ('limits-a', 'Exercise assumption: the fitted R1 has a continuous power limit of 0.1 W. External test equipment holds /IN at 12 V DC and clamps /OUT to 0 V indefinitely. Evaluate R1 under this condition only. The clamp is outside the board.', 'control', 'control', 'development'),
 ('limits-b', 'Exercise assumption: the fitted R1 has a continuous power limit of 0.1 W. External test equipment holds /IN at 48 V DC and clamps /OUT to 0 V indefinitely. Evaluate R1 under this condition only. The clamp is outside the board.', 'control', 'defect', 'development'),
 ('shutdown-a', 'Ideal shutdown: C1 initially charged to 1 V, /IN grounded at t=0, no external load. Require /OUT <= 0.01 V at 5 ms. Evaluate saved component values and connectivity. Ideal passive components only.', 'control', 'control', 'held-out'),
 ('shutdown-b', 'Ideal shutdown: C1 initially charged to 1 V, /IN grounded at t=0, no external load. Require /OUT <= 0.01 V at 5 ms. Evaluate saved component values and connectivity. Ideal passive components only.', 'seeded', 'defect', 'held-out'),
 ('width-a', 'Every saved copper track segment must have width >= 0.20 mm. This is a geometric exercise requirement, not an ampacity assertion.', 'control', 'control', 'held-out'),
 ('width-b', 'Every saved copper track segment must have width >= 0.30 mm. This is a geometric exercise requirement, not an ampacity assertion.', 'control', 'defect', 'held-out'),
 ('population-a', 'Review the saved assembly population: a fitted resistive DC path must connect /IN to /OUT. Do not assume an unpopulated footprint conducts. No alternate assembly override exists.', 'control', 'control', 'held-out'),
 ('population-b', 'Review the saved assembly population: a fitted resistive DC path must connect /IN to /OUT. Do not assume an unpopulated footprint conducts. No alternate assembly override exists.', 'dnp', 'defect', 'held-out'),
 ('verification-a', 'Evaluate only the ideal unloaded response to a 1 V step at /IN, initially discharged capacitor. At 1 ms require /OUT between 0.62 and 0.65 V. Ideal passive components only.', 'control', 'control', 'held-out'),
 ('verification-b', 'Evaluate whether /OUT settles between 0.62 and 0.65 V at 1 ms after a 1 V step while connected to a switched ADC input. ADC sampling capacitance, switch resistance, initial charge and sampling timing have not been specified. Separate established defects from missing coverage; do not invent ADC behavior.', 'control', 'uncertain', 'held-out')]


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    expected = []
    for name, requirement, source, expectation, split in CASES:
        case = out/'cases'/name
        case.mkdir(parents=True)
        shutil.copytree(ROOT/'fixtures/rc'/('control' if source == 'dnp' else source),case/'native')
        if source == 'dnp':
            # Native pcbnew changes the board population; the schematic instance
            # containing R1 gets the matching native DNP flag.
            import subprocess
            subprocess.run(['/usr/bin/python3','-c',
                'import pcbnew,sys; p=sys.argv[1]; b=pcbnew.LoadBoard(p); b.FindFootprintByReference("R1").SetDNP(True); pcbnew.SaveBoard(p,b)', str(case/'native/rc_filter.kicad_pcb')],check=True)
            p=case/'native/rc_filter.kicad_sch'
            s=p.read_text(); ref=s.index('(property "Reference" "R1"'); start=s.rfind('(dnp no)',0,ref)
            if start<0 or ref-start>500: raise ValueError('R1 DNP locator ambiguous')
            s=s[:start]+s[start:].replace('(dnp no)','(dnp yes)',1);p.write_text(s)
        (case/'requirements.txt').write_text(requirement+'\nOnly the stated exercise criterion is being evaluated; do not impose unstated ratings or qualification requirements. Native rule violations remain separately reportable.\n')
        scope=read_json(ROOT/'fixtures/rc/control.scope.json')
        scope.update(name='Engineering comparison '+name,scope=requirement,limitations=['Artificial exercise requirements; no ECU qualification.'],requirements=['requirements.txt'])
        scope['inputs']=[x for x in scope['inputs'] if x['at'] not in ('project','requirements')]
        scope['inputs'] += [{'path':'native','at':'project'},{'path':'requirements.txt','at':'requirements.txt'}]
        scope['checks']=[c for c in scope['checks'] if c['kind']!='rc_transient']
        scope['checks'][-1]['params']={}
        write_json(case/'scope.json',scope)
        expected.append({'case':name,'expected':expectation,'split':split})
    write_json(out/'expectations.json',{'plan_sha256':digest(ROOT/'docs/model-evaluation-plan.md'),'cases':expected})


def campaign(out,harness):
    trials=[(name,1) for name,*_ in CASES]+[(name,2) for name,_,_,_,split in CASES if split=='development']
    records=[]
    for name,trial in trials:
        run=out/'runs'/harness/f'{name}-{trial}'
        scope=out/'cases'/name/'scope.json'
        run_review(scope,run,1200,checks_only=True,profile=ROOT/'profiles'/f'{harness}.json')
        result=read_json(run/'result.json')
        if result.get('error') or any(c['execution']!='ok' for c in result['checks']):
            records.append({'case':name,'trial':trial,'state':'native_failure','run':str(run)});break
        packet=build_packet(run,result['checks'],result['run_id'])
        reviews,executions=initial_reviews(run,packet,time.monotonic()+1200)
        result.update(reviews=reviews,review_executions=executions,findings=collect_findings(reviews,result['checks']))
        result['limitations'].append('Comparative initial judgments only: no adjudication; not an acceptance run.')
        save_result(run,result);verify(run)
        records.append({'case':name,'trial':trial,'run':str(run),'executions':executions})
        write_json(out/f'{harness}-progress.json',{'completed':records,'planned':len(trials),'state':'running'})
        print(harness,name,trial,[r['execution'] for r in executions],flush=True)
        if any(r['execution']!='ok' for r in executions): break
    write_json(out/f'{harness}-progress.json',{'completed':records,'planned':len(trials),'state':'complete' if len(records)==len(trials) and all(e['execution']=='ok' for e in records[-1].get('executions',[])) else 'incomplete'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--prepare-only',action='store_true');p.add_argument('--prepared',action='store_true');args=p.parse_args()
    out=Path(args.out).resolve()
    if not args.prepared: prepare(out)
    if not args.prepare_only:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            for f in [pool.submit(campaign,out,h) for h in ('claude','codex','agy')]: f.result()
if __name__=='__main__':main()
