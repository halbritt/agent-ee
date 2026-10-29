"""Finite acquisition v4 role admission and pre-seal fresh-check production."""
import copy
from decimal import Decimal
from pathlib import Path

from . import block_adaptation, block_composition, parts_manifest, reference_blocks, requirement_map, storage
from .catalog import exact_fields, strict_json

SCHEMA = 'agent-ee.acquisition-role-context/1'
AT = 'reference-roles'
ROLES = {'acq-tps7a2033-power': ('power', {'U1','J1','D1','C1','C2','R5','R6'}),
         'acq-ads1115-two-channel': ('analog', {'U2','J3','C3','C4','C5','R1','R2'}),
         'acq-jst-ph-i2c': ('host', {'J2','R3','R4'})}


def application(document, capacitance):
    exact_fields(document, ('j2_external_load_a','j2_external_drive','bus_capacitance_pf','temperature_c'))
    if (type(document['j2_external_load_a']) not in (int,float) or document['j2_external_load_a'] != 0
            or document['j2_external_drive'] is not False or document['temperature_c'] != 25
            or Decimal(str(document['bus_capacitance_pf'])) != Decimal(100)
            or Decimal(str(capacitance)) != Decimal(100)):
        raise ValueError('v4 roles require nominal25C, exact100pF, J2 zero external load and no external drive')


def read(root, raw, capacitance):
    """Replay actual source qualification; pinned report text cannot grant credit."""
    root=Path(root).absolute()
    if root.resolve(strict=True)!=root:
        raise ValueError('role source root must not contain aliases')
    doc=strict_json(raw)
    exact_fields(doc, ('schema','reference','files','roles','application','host_observation'))
    if doc['schema']!=SCHEMA or doc['reference']!='acquisition-control' or not isinstance(doc['roles'],dict) or set(doc['roles'])!=set(ROLES):
        raise ValueError('role context must select exactly the three acquisition v4 roles')
    application(doc['application'],capacitance)
    if not isinstance(doc['files'],dict) or not 1<=len(doc['files'])<=4096:
        raise ValueError('role context needs a finite captured closure')
    captures={p:parts_manifest._read_blob(root,dict(path=p,sha256=h)) for p,h in doc['files'].items()}
    # Supplied observations use the existing verifier's host API. The coordinator
    # must independently admit this pinned observation; this code never probes CAD.
    exact_fields(doc['host_observation'],('policy_files','runtime_files'))
    from .run_record import policy_files
    if doc['host_observation']['policy_files']!=policy_files():
        raise ValueError('role observation policy differs from current source rules')
    needed=set(); memberships=[]; reports={}
    for rid,entry in doc['roles'].items():
        exact_fields(entry,('block','block_sha256','composition','report','run_locations','repository_locations'))
        path=entry['block']; record=strict_json(captures[path+'/block.json'])
        if storage.digest(root/path/'block.json')!=entry['block_sha256'] or record['id']!=rid or record['version']!='4':
            raise ValueError('role block identity/version differs')
        needed.add(path+'/block.json'); needed.update(path+'/'+n for n in record['files'])
        parts_manifest._blob(entry['composition']); parts_manifest._blob(entry['report'])
        for blob in (entry['composition'],entry['report']):
            if doc['files'].get(blob['path'])!=blob['sha256']:
                raise ValueError('role reference differs from its captured closure')
        needed.update((entry['composition']['path'],entry['report']['path']))
        plan=strict_json(captures[entry['composition']['path']]); block_composition.validate(plan,record)
        if (len(record.get('instances',[]))!=1 or record['instances'][0]['uuid_path']!='/'
                or record['boundary_ports'] or set(record['instances'][0]['references'])!=ROLES[rid][1]):
            raise ValueError('role occurrence or selected physical allocation differs')
        if not record['runs'] or set(entry['run_locations'])!={r['run_id'] for r in record['runs']}:
            raise ValueError('qualified role requires its exact declared source runs')
        report=reference_blocks.validate_block(root/path,entry['run_locations'],
            repository_locations=entry['repository_locations'],host=copy.deepcopy(doc['host_observation']))
        if report!=strict_json(captures[entry['report']['path']]) or report['status']!='verified':
            raise ValueError('role source is unqualified or its pinned report differs')
        deferred=[dict(requirement=r['id'],status='mandatory_on_adaptation',evaluation='not_evaluated',
            source=r['source'],conditions=r['conditions'],checks=r['checks']) for r in record['requirements']
            if r.get('verification_phase')=='adaptation']
        if report['deferred_requirements']!=deferred or len(deferred)!={'power':3,'analog':2,'host':1}[ROLES[rid][0]]:
            raise ValueError('role deferred obligations differ from the mandatory six rows')
        memberships += block_adaptation.root_membership(record)
        reports[rid]=report
    refs=[r['reference'] for r in memberships]
    if len(refs)!=17 or len(set(refs))!=17 or set(refs)!=set.union(*(v[1] for v in ROLES.values())):
        raise ValueError('role reference sets must be disjoint and own all17 physical parts')
    if needed!=set(captures):
        raise ValueError('role closure has missing or unrelated files')
    return doc,captures,reports


def apply(root, plan, overlay, doc, captures):
    """Produce immutable role records/assets and typed derived rows before seal."""
    root=Path(root)
    for path,raw in captures.items():
        target=root/AT/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
    storage.write_json(root/AT/'context.json',doc)
    declaration=requirement_map.map_declaration((root/'requirement-map.json').read_bytes())
    if declaration['scope'].get('block_instances') or 'block_requirements' in overlay:
        raise ValueError('role consumer cannot overwrite existing role mappings')
    instances=[]; mappings={}; conditions={}
    operative_parts=parts_manifest.load_manifest(root,plan['parts']['manifest'])
    allocated=[(i['sheet'],i['reference']) for p in operative_parts['parts'] for i in p['instances']]
    expected={('/',r) for r in set.union(*(v[1] for v in ROLES.values()))}
    if len(allocated)!=17 or set(allocated)!=expected:
        raise ValueError('operative acquisition parts must be exactly the17 owned root references')
    for rid,entry in doc['roles'].items():
        ident=ROLES[rid][0]; block=AT+'/'+entry['block']; source=strict_json((root/block/'block.json').read_bytes())
        composition=AT+'/'+entry['composition']['path']
        composed=block_adaptation._source(root,block,composition)
        names={p:block+'/'+p for p in source['files']}
        names.update({'/candidate/'+p:'/candidate/'+block+'/'+p for p in source['files']})
        bindings={p['name']:('GND' if p['name']=='GND' else '/'+p['name']) for p in composed['boundary_ports']}
        for p in composed['boundary_ports']:
            names[p['net_name']]=bindings[p['name']]
            names['/'+p['net_name'].lstrip('/')]=bindings[p['name']]
        requirements={r['id']:ident+'-'+r['id'] for r in source['requirements']}
        checks={c['id']:ident+'-'+c['id'] for c in source['check_set']}
        checks.update(netlist='netlist',drc='drc')
        record=dict(schema='agent-ee.block-instance/1',id=ident,block=block,block_id=rid,version='4',
            sha256=source['files'][source['sheet']],sheet=declaration['scope']['schematic'],instance_path='/',bindings=bindings,
            requirements={r['id']:[checks[c] for c in r['checks']] for r in source['requirements']},
            composition=dict(path=composition,sha256=storage.digest(root/composition),emitted_sha256=source['files'][source['sheet']]))
        instance=AT+'/instances/'+ident+'.json'; storage.write_json(root/instance,record);instances.append(instance)
        for template in source['check_set']:
            fresh=dict(id=checks[template['id']],kind=template['kind'],stage='verification',
                params=block_adaptation._rebound(template['params'],names,requirements,checks))
            kind=template['kind'];params=fresh['params']
            owners=[r for r in source['requirements'] if template['id'] in r['checks']]
            asset_field={'hardware_interface':'contract','interval_assertions':'assertions','topology_rules':'rules','parts_subset':'manifest'}.get(kind)
            if kind=='active_spice':params['contract']=template['params']['contract']
            if asset_field:
                original=strict_json((root/block/template['params'][asset_field]).read_bytes())
                target=AT+'/fresh/'+ident+'/'+template['id']+'.json'
                if kind=='hardware_interface':
                    rewritten=block_adaptation._interface_addresses(original,names,requirements)
                    for row in rewritten['invariants']:
                        old=next(r['id'] for r in source['requirements'] if requirements[r['id']]==row['requirement'])
                        row['source']=dict(path=block+'/block.json',locator='requirements/'+old)
                elif kind=='parts_subset':
                    rewritten=copy.deepcopy(operative_parts)
                    rewritten['parts']=[p for p in rewritten['parts'] if p['id'] in ROLES[rid][1]]
                    if {p['id'] for p in rewritten['parts']}!=ROLES[rid][1]:raise ValueError('operative role purchasing allocation differs')
                    assignments=strict_json((root/operative_parts['cad']['assignments']['path']).read_bytes())
                    assignments['components']=[c for c in assignments['components'] if c['reference'] in ROLES[rid][1]]
                    assignment_path=AT+'/fresh/'+ident+'/assignments.json';storage.write_json(root/assignment_path,assignments)
                    rewritten['cad']['assignments']=dict(path=assignment_path,sha256=storage.digest(root/assignment_path))
                    rewritten['declaration_source']=dict(path=block+'/block.json',locator='requirements/'+owners[0]['id'],sha256=storage.digest(root/block/'block.json'))
                else:
                    rewritten=block_adaptation._rebound(original,names,requirements,checks)
                    if kind=='interval_assertions':
                        rewritten['requirement_sources']={requirements[r['id']]:dict(path=block+'/block.json',locator='requirements/'+r['id'],sha256=storage.digest(root/block/'block.json'))
                            for r in source['requirements'] if requirements[r['id']] in rewritten['requirement_sources']}
                    else:
                        if len(owners)!=1:raise ValueError('rules require one declared role owner')
                        for row in rewritten['rules']:row['requirement']=requirements[owners[0]['id']]
                storage.write_json(root/target,rewritten);params[asset_field]=target
            overlay['check_additions']['declare'].append(fresh)
        for row in block_adaptation.baseline_rows(record,composed):mappings[row['id']]=row['checks']
        mappings[ident+'-boundary']=[ident+'-provenance']
        overlay['check_additions']['declare'].append(dict(id=ident+'-provenance',kind='block_provenance',stage='verification',params=dict(instance=instance,netlist_check='netlist')))
        conditions[ident]={r['id']:r.get('conditions',[]) for r in source['requirements']}
    declaration['scope']['block_instances']=sorted(instances)
    declaration['scope']['inputs'].append(dict(path=AT,at=AT))
    storage.write_json(root/'requirement-map.json',declaration)
    overlay['base']['requirement_map_sha256']=storage.digest(root/'requirement-map.json')
    overlay['block_requirements']=mappings
    report=dict(application=doc['application'],conditions=conditions,instances=sorted(instances),requirements=sorted(mappings),
        remaining=['Fresh native all17 union/UUID and role checks','Fresh engineering judgments','Supplier and physical validation'])
    storage.write_json(root/AT/'admission.json',report)
    for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation'):
        brief=overlay['judgments'].get('judgments/'+key+'.json')
        if brief is None:raise ValueError('role consumer requires existing fresh engineering judgments')
        brief['sources'].append(AT+'/admission.json')
        brief['questions'].append('Evaluate the current board against reference-roles/admission.json and its captured role conditions; no source qualification or R5 judgment discharges this fresh obligation.')
    return report
