"""Acquisition local copper and source-bound ground-strip reservations."""
import json
from pathlib import Path
import shutil
import uuid

from . import acquisition_local_return as local, acquisition_mechanics as mechanics
from .add_planes import validate_plan
from .catalog import strict_json
from .placement_job import JobRefusal, JobStopped, _check_stop, drc
from .storage import canonical, digest, write_json
from .symbol_library import children, nanometres, parse_sexpr

SCHEMA='agent-ee.acquisition-local-routing/1'


def rectangles(strips,clearance_mm):
    """Reserve the checker's 0.1 mm square-joined strip plus plane clearance."""
    margin=100_000+nanometres(str(clearance_mm));result=set()
    if not isinstance(strips,dict) or not 0<len(strips)<=16:
        raise JobRefusal('local routing: unsupported ground-strip population')
    for points in strips.values():
        if not isinstance(points,list) or not 2<=len(points)<=32:
            raise JobRefusal('local routing: unsupported ground-strip polyline')
        native=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2:
                raise JobRefusal('local routing: malformed ground-strip vertex')
            native.append(tuple(nanometres(str(v)) for v in point))
        for a,b in zip(native,native[1:]):
            if a==b or (a[0]!=b[0] and a[1]!=b[1]):
                raise JobRefusal('local routing: nonorthogonal/empty ground-strip edge')
            result.add((min(a[0],b[0])-margin,min(a[1],b[1])-margin,
                        max(a[0],b[0])+margin,max(a[1],b[1])+margin))
    if not 0<len(result)<=32:raise JobRefusal('local routing: reservation population exceeds 32')
    return sorted(result)


def reservation_bytes(data,recipe,planes):
    mechanics._recipe(recipe)
    plan=validate_plan(strict_json(planes))
    zones=[z for z in plan['zones'] if z['net']=='GND' and z['layer']=='B.Cu']
    if len(zones)!=1:raise JobRefusal('local routing: one declared B.Cu ground plane required')
    model_sha=recipe['declaration']['sources']['analysis/geometry-model.json']
    boxes=rectangles(recipe['model']['ground_strips'],zones[0]['clearance_mm'])
    tree=parse_sexpr(data);nodes=[];rows=[]
    existing={n[1] for node in tree[1:] if isinstance(node,list) for n in children(node,'uuid')}
    for box in boxes:
        identity=str(uuid.uuid5(uuid.NAMESPACE_URL,'agent-ee/ground-strip/'+model_sha+'/'+str(box)))
        if identity in existing:raise JobRefusal('local routing: reservation UUID already present')
        x,y,u,v=box;polygon=[[x,y],[u,y],[u,v],[x,v]]
        rows.append(dict(uuid=identity,rectangle_nm=list(box)))
        points=' '.join(f'(xy {local._mm(a)} {local._mm(b)})' for a,b in polygon)
        nodes.append(f'\n (zone (net "") (layer "B.Cu") (uuid "{identity}") '
            f'(name "acquisition ground strip") (hatch edge 0.5) (connect_pads (clearance 0)) '
            f'(min_thickness 0.25) (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) '
            f'(copperpour allowed) (footprints allowed)) (polygon (pts {points})))\n'.encode())
    end=data.rstrip().rfind(b')');output=data[:end]+b''.join(nodes)+data[end:]
    if parse_sexpr(output)[:-len(rows)]!=tree:
        raise JobRefusal('local routing: reservation writer changed existing nodes')
    return output,dict(model_sha256=model_sha,planes_sha256=local._sha(planes),
                       plane_clearance_mm=zones[0]['clearance_mm'],strip_half_width_nm=100000,areas=rows)


def verify_reservations(board_path,reservations):
    import pcbnew as k
    from .placement_probe import rule_area
    board=k.LoadBoard(str(board_path))
    if board is None:raise JobRefusal('local routing: native reservation board load failed')
    result=[]
    def canonical_ring(points):
        candidates=[]
        for ring in (points,list(reversed(points))):
            candidates.extend(ring[i:]+ring[:i] for i in range(len(ring)))
        return min(candidates)
    for row in reservations['areas']:
        found=[z for z in board.Zones() if z.m_Uuid.AsString()==row['uuid']]
        if len(found)!=1 or not found[0].GetIsRuleArea() or found[0].GetNetname():
            raise JobRefusal('local routing: native reservation identity/type changed')
        native=rule_area(found[0],'board');x,y,u,v=row['rectangle_nm']
        expected=dict(uuid=row['uuid'],name='acquisition ground strip',owner='board',layers=['B.Cu'],
            forbid=dict(footprints=False,tracks=True,vias=True,pads=False,zone_fills=False),
            placement_area=False,outline=dict(status='proven',polygons=[[[x,y],[u,y],[u,v],[x,v]]]))
        outline=native.get('outline',{})
        if outline.get('status')!='proven' or len(outline.get('polygons',[]))!=1:
            raise JobRefusal('local routing: unsupported native reservation outline')
        native['outline']['polygons']=[canonical_ring(outline['polygons'][0])]
        expected['outline']['polygons']=[canonical_ring(expected['outline']['polygons'][0])]
        if native!=expected:raise JobRefusal('local routing: native reservation geometry/policy changed')
        result.append(native)
    return result


def produce(candidate_dir,destination,*,expected_inputs,mechanics_binding,planes,clock,cancel_dir=None):
    _check_stop(clock,cancel_dir)
    source,destination=Path(candidate_dir).absolute(),Path(destination).absolute()
    if destination.resolve()!=destination or destination.exists() or destination.is_relative_to(source):
        raise JobRefusal('local routing: output exists/aliases/overlaps source')
    if (mechanics_binding.get('schema')!=mechanics.BINDING
            or mechanics_binding.get('binding_sha256')!=mechanics._identity(mechanics_binding)):
        raise JobRefusal('local routing: mechanics binding changed')
    if local._identity(local._capture(source))!=expected_inputs:
        raise JobRefusal('local routing: original input identity changed')
    recipe=strict_json(canonical(mechanics_binding['recipe']))
    mechanics._recipe(recipe)
    if not isinstance(planes,bytes):raise JobRefusal('local routing: captured plane bytes required')
    validate_plan(strict_json(planes))
    helper_sha=digest(Path(__file__));destination.mkdir(parents=True)
    record=dict(schema=SCHEMA,status='running',inputs=expected_inputs,helper_sha256=helper_sha,
                mechanics_binding_sha256=mechanics_binding['binding_sha256'],recipe=recipe,
                planes_raw=planes.decode('utf-8'),record=str(destination/'record.json'),acceptance_assessed=False)
    def custody():
        _check_stop(clock,cancel_dir)
        if local._identity(local._capture(source))!=expected_inputs or digest(Path(__file__))!=helper_sha:
            raise JobRefusal('local routing: original source/helper drift')
    try:
        write_json(destination/'record.json',record)
        loops=local.produce_loops(source,destination/'loops',expected_inputs=expected_inputs,clock=clock,cancel_dir=cancel_dir)
        record['loops']=loops;current=Path(loops['output']);captured=local._capture(current)
        local.verify_seed(current/local.FILES[0],loops,clock=clock,cancel_dir=cancel_dir)
        baseline_raw=local._report_bytes(current);baseline=strict_json(baseline_raw)
        board,reservations=reservation_bytes(captured[local.FILES[0]],recipe,planes)
        record['reservations']=reservations
        trial=destination/'reservation';trial.mkdir()
        expected={**captured,local.FILES[0]:board}
        for name,raw in expected.items():
            if raw is not None:(trial/name).write_bytes(raw)
        verify_reservations(trial/local.FILES[0],reservations)
        report,execution=drc(trial/local.FILES[0],trial,clock=clock,cancel_dir=cancel_dir)
        report_sha=local._raw_report(trial,report,execution)
        record['native']=dict(execution=execution,report_sha256=report_sha)
        if not local._legal(report) or local._warnings(report)!=local._warnings(baseline):
            raise JobRefusal('local routing: ground reservations fail native checks')
        custody()
        local.verify_seed(trial/local.FILES[0],loops,clock=clock,cancel_dir=cancel_dir)
        if (local._capture(trial)!=expected or local._capture(current)!=captured
                or local._report_bytes(current)!=baseline_raw):
            raise JobRefusal('local routing: reservation/source/report drift')
        pending=destination/'.accepted';shutil.copytree(trial,pending)
        custody()
        if (local._capture(pending)!=expected or local._capture(trial)!=expected
                or local._sha(local._report_bytes(pending))!=report_sha
                or local._sha(local._report_bytes(trial))!=report_sha):
            raise JobRefusal('local routing: reservation adoption changed')
        verify_reservations(pending/local.FILES[0],reservations)
        local.verify_seed(pending/local.FILES[0],loops,clock=clock,cancel_dir=cancel_dir)
        custody()
        if (local._capture(pending)!=expected or local._capture(trial)!=expected
                or local._sha(local._report_bytes(pending))!=report_sha
                or local._sha(local._report_bytes(trial))!=report_sha):
            raise JobRefusal('local routing: final reservation adoption drift')
        pending.rename(destination/'accepted')
        record.update(status='seeded',output=str(destination/'accepted'),output_inputs=local._identity(expected))
        write_json(destination/'record.json',record)
        return record
    except (JobRefusal,JobStopped,OSError,ValueError,KeyError,TypeError) as exc:
        record.update(status='stopped' if isinstance(exc,JobStopped) else 'refused',reason=str(exc))
        write_json(destination/'record.json',record);raise


def verify(board_path,record,*,clock,cancel_dir=None):
    _check_stop(clock,cancel_dir)
    receipt=Path(record['record']);raw=receipt.read_bytes();source=Path(record['output'])
    if (record.get('schema')!=SCHEMA or record.get('status')!='seeded' or receipt.is_symlink()
            or canonical(strict_json(raw))!=canonical(record)
            or local._identity(local._capture(source))!=record['output_inputs']
            or local._sha(local._report_bytes(source))!=record['native']['report_sha256']):
        raise JobRefusal('local routing: retained operation/seed/report changed')
    before=digest(Path(board_path))
    loops=local.verify_seed(board_path,record['loops'],clock=clock,cancel_dir=cancel_dir)
    prior=local._capture(Path(record['loops']['output']))
    expected,reservations=reservation_bytes(prior[local.FILES[0]],record['recipe'],record['planes_raw'].encode('utf-8'))
    if (reservations!=record['reservations']
            or local._identity({**prior,local.FILES[0]:expected})!=record['output_inputs']
            or record['loops']['inputs']!=record['inputs']):
        raise JobRefusal('local routing: generated reservation provenance changed')
    native=verify_reservations(board_path,reservations)
    _check_stop(clock,cancel_dir)
    if (digest(Path(board_path))!=before or loops['board_sha256']!=before or receipt.read_bytes()!=raw
            or local._identity(local._capture(source))!=record['output_inputs']
            or local._sha(local._report_bytes(source))!=record['native']['report_sha256']):
        raise JobRefusal('local routing: verification custody changed')
    return dict(status='pass',board_sha256=before,record_sha256=local._sha(raw),loops=loops,
                reservations=native,owned_zone_uuids=[r['uuid'] for r in reservations['areas']],acceptance_assessed=False)
