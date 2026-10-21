"""Recreate experiment inputs from retained iteration-7 evidence.

Usage: python export_inputs.py placement [--source-root DIR] [--output-dir DIR]
       <qualified native engine python> export_inputs.py routing-native [options]
Repo paths are configurable with --agent-ee-repo and --kicad-route-repo.
Defaults target the original local checkouts and write /tmp/agent510-* outputs.
Metadata records observed Git revisions, dirty state and imported module hashes.
Native binary hash/build-manifest claims are separate from Python source revision.
Replay with changed source is a new experiment, not proof of historical parity.
"""
import argparse
import gzip
from pathlib import Path


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_provenance(repo):
    import subprocess
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True)
    return dict(repository=str(repo.resolve()), revision=revision, dirty=bool(status))


def module_sources(names):
    import sys
    return {name: dict(path=str(Path(sys.modules[name].__file__).resolve()),
                       sha256=sha256(sys.modules[name].__file__)) for name in names}


def native_binary_provenance():
    import json
    import grid_router
    binary = Path(grid_router.__file__).resolve()
    observed_hash = sha256(binary)
    result = dict(path=str(binary), sha256=observed_hash,
                  source_revision_verified=False)
    for parent in binary.parents:
        manifest_path = parent / 'manifest.json'
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        krt = manifest.get('krt', {})
        expected_hash = krt.get('extension_module', {}).get('sha256')
        if expected_hash is None:
            continue
        if observed_hash != expected_hash:
            raise RuntimeError('Loaded native binary SHA256 differs from generation manifest')
        result.update(generation=parent.name, manifest_path=str(manifest_path),
                      manifest_sha256=sha256(manifest_path), binary_matches_manifest=True,
                      manifest_reported_source_commit=krt.get('commit'),
                      manifest_reported_source_sha256=krt.get('source_sha256'))
        return result
    result['manifest_status'] = 'No matching enclosing generation manifest found'
    return result


def placement(args):
    import json,pathlib,hashlib,sys,time
    import numpy as np
    sys.path.insert(0,str(args.agent_ee_repo / 'src'))
    from agent_ee import placer
    root=args.source_root
    source=root/'evidence/placement-probe-pads.json';planpath=root/'evidence/placement-plan.json'
    a=json.loads(source.read_text());plan=json.loads(planpath.read_text());poses={x['ref']:x for x in plan['placements']}
    grounds=plan['policy'].get('ground_nets',[])
    print('ground related policy', {k:v for k,v in plan['policy'].items() if 'ground' in k})
    if not grounds: grounds=plan['policy'].get('grounds',[])
    records=[]
    for f in a['footprints']:
     p=poses[f['ref']];o=p['position_nm'];records.append(dict(ref=f['ref'],side=f['side'],position_nm=o,rotation_deg=p['rotation_deg'],pads=[dict(number=q['number'],net=q['net'],position_nm=q['position_nm'],offset_nm=[q['position_nm'][i]-o[i] for i in [0,1]]) for q in f['pads']]))
    target='U23';f=next(x for x in records if x['ref']==target);o=f['position_nm'];peers={}
    for other in records:
     if other['ref']==target: continue
     for p in other['pads']:
      if p['net'] and p['net'] not in grounds: peers.setdefault(p['net'],[]).append(p['position_nm'])
    fp={'position_nm':o,'rotation_deg':f['rotation_deg'],'pads':f['pads']}
    axes=list(range(-2000000,2000001,250000));queries=[]
    for rot in [0,90,180,270]:
     for dx in axes:
      for dy in axes: queries.append(dict(ref=target,position_nm=[o[0]+dx,o[1]+dy],rotation_deg=rot))
    t=time.perf_counter();expected=[placer._wire_cost(placer._pad_nets(fp,q['position_nm'],q['rotation_deg'],set(grounds)),peers) for q in queries];cpu=time.perf_counter()-t
    # Vectorized per net, same min-over-peer inside each source-pad then sum.
    t=time.perf_counter();scores=np.zeros(len(queries),np.int64)
    for idx,rot in enumerate([0,90,180,270]):
     ps=placer._pad_nets(fp,(0,0),rot,set(grounds));positions=np.array([q['position_nm'] for q in queries[idx*289:(idx+1)*289]],dtype=np.int64)
     for net,x,y in ps:
      if net in peers:
       d=np.abs(positions[:,None,:]+np.array([x,y])-np.array(peers[net])[None,:,:]).sum(axis=2).min(axis=1)
       scores[idx*289:(idx+1)*289]+=d
    numpy_time=time.perf_counter()-t
    assert expected==scores.tolist()
    # Bounds/ranked diagnostic, unconstrained offset lattice, not a real scan.
    bounds=[]
    for rot in [0,90,180,270]:
     xs=[o[0]+d for d in axes];ys=[o[1]+d for d in axes]
     bx,by=placer._wire_bounds(placer._pad_nets(fp,(0,0),rot,set(grounds)),peers,xs,ys)
     bounds.extend((bx[x]+by[y],x,y,rot) for x in xs for y in ys)
    lazy={}
    for n in [1,10,100,1156]:
     calls=[0]
     def exact(k):
      calls[0]+=1;_,x,y,r=k
      return (placer._wire_cost(placer._pad_nets(fp,(x,y),r,set(grounds)),peers),x,y,r)
     it=placer._ranked(bounds,exact)
     for _ in range(n): next(it)
     lazy[str(n)]=calls[0]
    out=dict(schema='agent510.actual-pad-score-input/1',sources=[dict(path=str(s),sha256=hashlib.sha256(s.read_bytes()).hexdigest()) for s in [source,planpath]],agent_ee_revision=git_provenance(args.agent_ee_repo)['revision'],kicad_route_revision=git_provenance(args.kicad_route_repo)['revision'],python_source=git_provenance(args.agent_ee_repo),python_modules=module_sources(['agent_ee.placer']),ground_nets=grounds,footprints=records,queries=queries,expected_score_nm=expected,score_semantics='sum over nonempty nonground source pads having external same-net peers of min_peer(abs(dx)+abs(dy)); int64 nm; source target excluded from peers',query_scope='U23 actual-pad geometry, artificial +/-2mm 0.25mm offset lattice and 4 quarter turns; geometry feasibility NOT applied',cpu_diagnostic=dict(python_eager_s=cpu,numpy_eager_s=numpy_time,lazy_exact_calls_by_emitted=lazy))
    (args.output_dir / 'agent510-gpu-input.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
    print(json.dumps({k:out[k] for k in ['ground_nets','cpu_diagnostic']}));print('export records',len(records),'queries',len(queries),'target pads',len(f['pads']))


def routing_native(args):
    source = args.source_root / 'candidate.kicad_pcb.gz'
    candidate = args.output_dir / 'agent510-candidate.kicad_pcb'
    candidate.write_bytes(gzip.decompress(source.read_bytes()))
    import sys,json,pathlib,hashlib,time
    import numpy as np
    sys.path.insert(0,str(args.kicad_route_repo / 'src/kicad_route/router/py_router'))
    from kicad_parser import parse_kicad_pcb
    from routing_config import GridRouteConfig,GridCoord
    from obstacle_map import build_base_obstacle_map
    native = native_binary_provenance()
    p=parse_kicad_pcb(str(candidate));root=next(q for q in p.footprints['U23'].pads if q.pad_number=='1');nid=root.net_id
    cfg=GridRouteConfig(grid_step=.1,track_width=.1,clearance=.1,layers=['F.Cu'])
    t=time.perf_counter();m=build_base_obstacle_map(p,cfg,[nid],static_base=True);print('built',time.perf_counter()-t,flush=True)
    blocked=np.fromiter((m.is_blocked(x,y,0) for y in range(651) for x in range(901)),dtype=np.uint8,count=651*901).reshape(651,901)
    # Closed finite graph; fence is separate from sampled native occupancy.
    blocked[[0,-1],:]=1;blocked[:,[0,-1]]=1
    coord=GridCoord(.1);rootxy=list(coord.to_grid(root.global_x,root.global_y));assert not blocked[rootxy[1],rootxy[0]],rootxy
    terms=[]
    for q in p.pads_by_net[nid]:
     if any(l in ['F.Cu','*.Cu'] for l in q.layers):
      x,y=coord.to_grid(q.global_x,q.global_y)
      if 0<x<900 and 0<y<650:terms.append(dict(ref=q.component_ref,pad=q.pad_number,xy=[x,y],free=not bool(blocked[y,x])))
    queries=[rootxy+q['xy'] for q in terms if q['free'] and q['xy']!=rootxy][:32]
    synthetic=[]
    for f in p.footprints.values():
     for q in f.pads:
      x,y=coord.to_grid(q.global_x,q.global_y)
      if 0<x<900 and 0<y<650 and blocked[y,x]==0 and [x,y]!=rootxy:synthetic.append(rootxy+[x,y])
    synthetic=synthetic[:32]
    np.savez_compressed(args.output_dir / 'agent510-gpu-route-native-input.npz',blocked=blocked,costs=np.ones_like(blocked,dtype=np.int32),queries=np.array(queries,dtype=np.int32).reshape(-1,4),synthetic_queries=np.array(synthetic,dtype=np.int32).reshape(-1,4))
    provenance = git_provenance(args.kicad_route_repo)
    meta = dict(
        schema='agent510.native-base-graph/1', source=str(source),
        source_sha256=sha256(source), uncompressed_sha256=sha256(candidate),
        kicad_route_revision=provenance['revision'], python_source=provenance,
        python_modules=module_sources(['kicad_parser', 'routing_config', 'obstacle_map', 'routing_utils']),
        native_binary=native,
        graph='single F.Cu 2D 4-neighbor, no vias; blocked uint8[y,x], cost int32[y,x]=1',
        grid_step_mm=cfg.grid_step, grid_origin_mm=[0, 0], shape_yx=list(blocked.shape),
        track_width_mm=cfg.track_width, clearance_mm=cfg.clearance,
        root=dict(ref=root.component_ref, pad=root.pad_number, net=root.net_name,
                  net_id=nid, xy=rootxy), root_net_excluded=True, terminals=terms,
        actual_same_net_queries=len(queries), synthetic_probe_queries=len(synthetic),
        endpoint_free=all(not blocked[q[1], q[0]] and not blocked[q[3], q[2]]
                          for q in queries + synthetic),
        blocked_cell_count=int(blocked.sum()),
        foreign_pad_count=sum(pad.net_id != nid for fp in p.footprints.values() for pad in fp.pads),
        builder='owned build_base_obstacle_map PCBData/config [root_net] static_base=True; exported GridObstacleMap.is_blocked(x,y,0)',
        limitations=[
            'Native base map graph, not fully prepared current per-net route snapshot; no history/proximity/attraction costs',
            'Single F.Cu .1mm grid, 4-neighbor unitcost graph, no vias; cannot compare semantics to weighted turn-aware Rust A*',
            'Fixed .1mm width/clearance config, no matching project custom/class-rule import; native builder may apply file-specific rule areas/holes it recognizes',
            'Extra finite rectangular fence x0..90,y0..65mm; no fullboard infeasibility implication',
            'Synthetic probes are real pad centers under root mask, not same-net obligations',
            'No physical routeability or native DRC acceptance implied',
            'Python checkout revision and installed binary build are recorded separately; manifest commit is a build provenance claim, not proof of current-checkout compiled parity',
        ])
    meta['native_generation'] = meta['native_binary'].get('generation')
    (args.output_dir / 'agent510-gpu-route-native-input.json').write_text(json.dumps(meta, indent=2)+'\n')
    print('export',len(queries),len(synthetic),int(blocked.sum()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['placement', 'routing-native'])
    parser.add_argument('--agent-ee-repo', type=Path, default=Path('/home/halbritt/git/agent-ee'))
    parser.add_argument('--kicad-route-repo', type=Path, default=Path('/home/halbritt/git/kicad-route'))
    parser.add_argument('--source-root', type=Path, default=Path('/home/halbritt/git/fome-mc/telemetry-pod/expansion/placement-candidate/iteration-7'))
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    placement(args) if args.mode == 'placement' else routing_native(args)
