import subprocess, json, pathlib, hashlib, sys, datetime
ROOT = pathlib.Path('/work/experiments')

def run(name, argv):
    p = subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ROOT / (name + '.stdout')).write_text(p.stdout)
    (ROOT / (name + '.stderr')).write_text(p.stderr)
    with (ROOT / 'commands.jsonl').open('a') as f:
        f.write(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'name': name, 'argv': argv, 'cwd': str(ROOT), 'exit_code': p.returncode, 'stdout': name+'.stdout', 'stderr': name+'.stderr'})+'\n')
    print(name, p.returncode, p.stdout[:1500], p.stderr[:500])
    return p

if __name__ == '__main__':
    run('versions', ['bash', '-c', 'kicad-cli version && ngspice --version && /usr/bin/python3 --version'])
    run('api_probe', ['/usr/bin/python3', '-c', "import pcbnew; b=pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb'); print(pcbnew.Version()); c=b.GetConnectivity(); print('connectivity', [x for x in dir(c) if 'Connect' in x or 'Build' in x]); z=list(b.Zones())[0]; p=z.GetFilledPolysList(pcbnew.B_Cu); print('poly', [x for x in dir(p) if any(k in x for k in ['Outline','Hole','Contain','Distance','Vertex'])]); print('chain', [x for x in dir(p.Outline(0)) if any(k in x for k in ['Point','Count','Area'])]); print('board', [x for x in dir(b) if 'Bounding' in x]); print('pad', [x for x in dir(list(b.GetPads())[0]) if 'Shape' in x]);"])
    run('drc', ['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(ROOT/'drc.json'),'/candidate/project/rc_filter.kicad_pcb'])
    run('erc', ['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(ROOT/'erc.json'),'/candidate/project/rc_filter.kicad_sch'])
    run('svg', ['kicad-cli','pcb','export','svg','--layers','F.Cu,B.Cu,F.SilkS,Edge.Cuts','--mode-single','--page-size-mode','2','--exclude-drawing-sheet','-o',str(ROOT/'saved-board.svg'),'/candidate/project/rc_filter.kicad_pcb'])
