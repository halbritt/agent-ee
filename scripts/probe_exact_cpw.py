#!/usr/bin/env python3
"""Retain AGENTEE-492's native controls; a fixture emitter, not a production router.

Run inside ae-ci-exec with PYTHONPATH=src:tests. Optional --pod paths are read-only
snapshots: this script records limitations without relocating their RF path.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import pcbnew as k
from agent_ee.rf_geometry import extract, evaluate, parse_declaration, probe_request
from agent_ee.storage import digest, write_json
from rf_exact_fixture import emit


def declaration(net='/RF'):
    return {'schema':'agent-ee.rf-geometry/1','net':net,'ground_net':'GND',
            'signal_layer':'F.Cu','reference_layer':'In1.Cu',
            'cpw':{'width_mm':.293,'gap_mm':.2032,'width_tolerance_mm':0,'gap_tolerance_mm':0}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--pod',type=Path,action='append',default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    results = []
    spec = parse_declaration(json.dumps(declaration()))[1]
    for name,kwargs in [('nominal',{}),('below',{'gap':202200}),('above',{'gap':204200}),
                        ('narrow',{'width':283000}),('wide',{'width':303000}),
                        ('minus-one-nm',{'width':292999}),('plus-one-nm',{'width':293001}),
                        ('diagonal',{'diagonal':True}),('arc',{'arc':True}),
                        ('missing',{'missing_flank':True}),('floating',{'floating':True})]:
        directory = args.output/name
        path = directory/'board.kicad_pcb'
        ids = emit(path,**kwargs)
        before = extract(path,probe_request(spec))
        board = k.LoadBoard(str(path)); k.ZONE_FILLER(board).Fill(board.Zones())
        saved = directory/'refilled.kicad_pcb'; k.SaveBoard(str(saved),board)
        for suffix in ('.kicad_pro','.kicad_dru'):
            shutil.copyfile(path.with_suffix(suffix),saved.with_suffix(suffix))
        after = extract(saved,probe_request(spec))
        board = k.LoadBoard(str(saved)); board.BuildConnectivity()
        pad = next(p for f in board.GetFootprints() for p in f.Pads() if p.m_Uuid.AsString()==ids['anchor'])
        members = {p.m_Uuid.AsString() for p in board.GetConnectivity().GetConnectedItems(pad)}
        evaluated = evaluate(spec,after)
        command = ['kicad-cli','pcb','drc','--format','json','--output',str(directory/'drc.json'),str(saved)]
        proc = subprocess.run(command,capture_output=True,text=True,timeout=60)
        (directory/'drc.stdout').write_text(proc.stdout); (directory/'drc.stderr').write_text(proc.stderr)
        if proc.returncode:
            raise RuntimeError(f'{name} DRC process exit {proc.returncode}: {proc.stderr}')
        drc = json.loads((directory/'drc.json').read_text())
        write_json(directory/'native-before.json',before); write_json(directory/'native-refilled.json',after)
        write_json(directory/'evaluation.json',evaluated); write_json(directory/'declaration.json',declaration())
        record = {'case':name,'input_sha256':digest(path),'refilled_sha256':digest(saved),
                  'geometry_sha256':after['exact_cpw']['geometry_sha256'],
                  'geometry_stable':before['exact_cpw']['geometry_sha256']==after['exact_cpw']['geometry_sha256'],
                  'connected_after_reload':{label:sorted(set(ids[label]) & members) for label in ('flanks','vias','zones')},
                  'ids':ids,'drc_command':command,'drc_exit':proc.returncode,'drc_sha256':digest(directory/'drc.json'),
                  'drc_counts':{key:len(drc[key]) for key in ('violations','unconnected_items','schematic_parity')},
                  'drc_categories':sorted(v['type'] for v in drc['violations']),
                  'cpw_items':{name:item['status'] for name,item in evaluated['sections']['cpw']['items'].items()}}
        write_json(directory/'receipt.json',record); results.append(record)
    for i,path in enumerate(args.pod):
        directory = args.output/f'pod-{i}'; directory.mkdir()
        copy = directory/'input.kicad_pcb'; shutil.copyfile(path,copy)
        for suffix in ('.kicad_pro','.kicad_dru'):
            if path.with_suffix(suffix).exists():
                shutil.copyfile(path.with_suffix(suffix),copy.with_suffix(suffix))
        spec = parse_declaration(json.dumps(declaration('/POD_GNSS_RF')))[1]
        native = extract(copy,probe_request(spec))
        result = evaluate(spec,native)
        write_json(directory/'native.json',native); write_json(directory/'evaluation.json',result)
        records = [{'uuid':t['uuid'],'kind':t['kind'],'layer':t['layer'],'width_nm':t['width_nm'],
                    'start_nm':t['start_nm'],'end_nm':t['end_nm'],
                    'axis_aligned':t['kind']=='segment' and any(t['start_nm'][j]==t['end_nm'][j] for j in (0,1))}
                   for t in native['tracks']]
        write_json(directory/'receipt.json',{'source':str(path),'sha256':digest(copy),'tracks':records,
                    'cpw_status':result['sections']['cpw']['status'],'production_qualification':False})
    write_json(args.output/'summary.json',{'pcbnew_version':k.Version(),'controls':results})
    print(json.dumps({'cases':len(results),'output':str(args.output),'pcbnew':k.Version()}))


if __name__=='__main__':
    main()
