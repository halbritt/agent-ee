import hashlib,json,pathlib,subprocess
root=pathlib.Path('/work/experiments')
records=[]
for layer in ['F_Cu','B_Cu']:
    argv=['rsvg-convert','--width','1200','--output',str(root/f'{layer}.png'),str(root/f'plots/rc_filter-{layer}.svg')]
    p=subprocess.run(argv,capture_output=True,cwd=root)
    records.append(dict(argv=argv,exit_code=p.returncode,stdout=p.stdout.decode(),stderr=p.stderr.decode()))
hashes={str(p.relative_to('/candidate')):hashlib.sha256(p.read_bytes()).hexdigest() for p in
        [pathlib.Path('/candidate/project/rc_filter.kicad_pcb'),pathlib.Path('/candidate/project/rc_filter.kicad_sch')]}
(root/'render_and_hashes.json').write_text(json.dumps(dict(commands=records,candidate_hashes_after_checks=hashes),indent=2)+'\n')
