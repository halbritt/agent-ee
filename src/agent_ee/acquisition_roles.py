"""Finite acquisition role admission and pre-seal fresh-check production."""
import copy
from pathlib import Path

from . import block_adaptation, block_composition, parts_manifest, reference_blocks, requirement_map, storage
from .catalog import decimal_number, decimal_text, exact_fields, strict_json

SCHEMA = 'agent-ee.acquisition-role-context/1'
AT = 'reference-roles'
ROLES = {'acq-tps7a2033-power': ('power', {'U1','J1','D1','C1','C2','R5','R6'}),
         'acq-ads1115-two-channel': ('analog', {'U2','J3','C3','C4','C5','R1','R2'}),
         'acq-jst-ph-i2c': ('host', {'J2','R3','R4'})}


POLICY_PATH = 'roles/adaptation-policy-150.json'
POLICY_RAW = (Path(__file__).with_name('data')/'acquisition-role-policy-150.json').read_bytes()
POLICY = strict_json(POLICY_RAW)
BRIEF_CAPACITANCE = {'from':'public-brief','policy':POLICY['id']}
COHORTS = {('4','4','4'): '100', ('5','5','5'): '150', ('5','6','5'): '150'}


def _capacitance(value):
    if type(value) not in (int, str):
        raise ValueError('capacitance requires canonical integer or decimal text')
    text = str(value)
    number = decimal_number(text)
    if decimal_text(number) != text or not 100 <= number <= 150:
        raise ValueError('capacitance must be canonical within100..150pF')
    return text


def application(document, capacitance, *, envelope='100'):
    exact_fields(document, ('j2_external_load_a','j2_external_drive','bus_capacitance_pf','temperature_c'))
    brief = _capacitance(capacitance)
    operand=document['bus_capacitance_pf']
    declared = brief if envelope=='150' and operand==BRIEF_CAPACITANCE else _capacitance(operand)
    if (type(document['j2_external_load_a']) not in (int,float) or document['j2_external_load_a'] != 0
            or document['j2_external_drive'] is not False or type(document['temperature_c']) is not int
            or document['temperature_c'] != 25 or declared != brief
            or envelope not in ('100','150') or (envelope == '100' and declared != '100')):
        raise ValueError('role application requires nominal25C, matching admitted capacitance, J2 zero external load and no external drive')
    return declared


def cohort(doc, captures, capacitance):
    """Admit only the three finite source/policy cohorts, before run replay."""
    records={rid:strict_json(captures[entry['block']+'/block.json']) for rid,entry in doc['roles'].items()}
    versions=tuple(records[rid]['version'] for rid in ROLES)
    if versions not in COHORTS:
        raise ValueError('unsupported acquisition role cohort')
    envelope=COHORTS[versions]
    declared=application(doc['application'],capacitance,envelope=envelope)
    prefixes=[entry['block'] for entry in doc['roles'].values()]
    if any(a==b or a.startswith(b+'/') or b.startswith(a+'/') for i,a in enumerate(prefixes) for b in prefixes[i+1:]):
        raise ValueError('role block prefixes must be disjoint')
    for field in ('composition','report'):
        if len({e[field]['path'] for e in doc['roles'].values()}) != 3:
            raise ValueError('role compositions and reports must be isolated')
    if envelope=='150':
        from hashlib import sha256
        policy_ref=dict(path=POLICY_PATH,sha256=sha256(POLICY_RAW).hexdigest())
        for rid,record in records.items():
            prefix=doc['roles'][rid]['block']+'/'
            if captures.get(prefix+POLICY_PATH) != POLICY_RAW:
                raise ValueError('role adaptation policy differs from admitted captured policy')
            app=strict_json(captures[prefix+'roles/'+rid+'/application-contract.json'])
            if app.get('adaptation_policy') != policy_ref or app['selected_boundary']['i2c_line_capacitance'] != dict(max='150',unit='pF',selection='conservative engineering adaptation envelope',source=policy_ref):
                raise ValueError('role application boundary differs from captured150 policy')
            expected=copy.deepcopy(POLICY['base_allocations'][rid])
            if rid=='acq-ads1115-two-channel' and record['version']=='6':
                for row in expected:
                    if row['id'] in ('R1','R2'):
                        row.update({k:POLICY['alternate_analog'][k] for k in ('manufacturer','mpn','lcsc')})
            member_fields=('id','lcsc','manufacturer','mpn','lib_id','footprint')
            members=sorted(({k:m[k] for k in member_fields} for m in app.get('members',[])),key=lambda m:m['id'])
            if members != [{k:m[k] for k in member_fields} for m in expected]:
                raise ValueError('role application members differ from selected policy allocations')
            cap_limits=[v for v in app.get('limits',[]) if v.get('id')=='i2c-capacitance']
            if len(cap_limits)!=1 or cap_limits[0].get('max')!='150' or cap_limits[0].get('source')!=policy_ref:
                raise ValueError('role capacitance limit must cite the admitted150 policy')
            actual=block_adaptation._part_allocations(strict_json(captures[prefix+record['parts_manifest']]),{})
            if actual != expected:
                raise ValueError('role selected allocations differ from finite admitted variant')
            if rid=='acq-jst-ph-i2c':
                check=next(c for c in record['check_set'] if c['id']=='i2c-bus-envelope-intervals')
                template=strict_json(captures[prefix+check['params']['assertions']]);template.pop('requirement_sources')
                if template != POLICY['host_interval_template']:
                    raise ValueError('host role template differs from conservative150 policy')
    return dict(versions=list(versions),declared_bus_capacitance_pf=declared,
        host_role_envelope_capacitance_pf=envelope,host_role_envelope='conservative upper bound; not a physical minimum',
        policy='legacy-v4-100-only' if envelope=='100' else POLICY['id'])


def read(root, raw, capacitance):
    """Replay actual source qualification; pinned report text cannot grant credit."""
    root=Path(root).absolute()
    if root.resolve(strict=True)!=root:
        raise ValueError('role source root must not contain aliases')
    doc=strict_json(raw)
    exact_fields(doc, ('schema','reference','files','roles','application','host_observation'))
    if doc['schema']!=SCHEMA or doc['reference']!='acquisition-control' or not isinstance(doc['roles'],dict) or set(doc['roles'])!=set(ROLES):
        raise ValueError('role context must select exactly the three acquisition roles')
    if not isinstance(doc['files'],dict) or not 1<=len(doc['files'])<=4096:
        raise ValueError('role context needs a finite captured closure')
    for entry in doc['roles'].values():
        exact_fields(entry,('block','block_sha256','composition','report','run_locations','repository_locations'))
    captures={p:parts_manifest._read_blob(root,dict(path=p,sha256=h)) for p,h in doc['files'].items()}
    admission=cohort(doc,captures,capacitance)
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
        if storage.digest(root/path/'block.json')!=entry['block_sha256'] or record['id']!=rid:
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
    return bind_application(doc,capacitance,envelope=admission['host_role_envelope_capacitance_pf']),captures,reports


def bind_application(doc, capacitance, *, envelope):
    """Keep the source template and its exact public-brief-derived application."""
    declared=application(doc['application'],capacitance,envelope=envelope)
    bound=copy.deepcopy(doc)
    if doc['application']['bus_capacitance_pf']==BRIEF_CAPACITANCE:
        bound['application_binding']=dict(source_application=copy.deepcopy(doc['application']),
            capacitance_source='public-brief',policy=POLICY['id'])
        bound['application']['bus_capacitance_pf']=declared
    return bound


def apply(root, plan, overlay, doc, captures):
    """Produce immutable role records/assets and typed derived rows before seal."""
    root=Path(root)
    versions=tuple(strict_json(captures[doc['roles'][rid]['block']+'/block.json'])['version'] for rid in ROLES)
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
        record=dict(schema='agent-ee.block-instance/1',id=ident,block=block,block_id=rid,version=source['version'],
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
    report=dict(application=doc['application'],capacitance_admission=dict(
        declared_bus_capacitance_pf=_capacitance(doc['application']['bus_capacitance_pf']),
        host_role_envelope_capacitance_pf=COHORTS[versions],versions=list(versions),
        policy='legacy-v4-100-only' if COHORTS[versions]=='100' else POLICY['id'],
        envelope_meaning='conservative upper bound; not a physical minimum'),conditions=conditions,instances=sorted(instances),requirements=sorted(mappings),
        remaining=['Fresh native all17 union/UUID and role checks','Fresh engineering judgments','Supplier and physical validation'])
    if 'application_binding' in doc:report['application_binding']=doc['application_binding']
    storage.write_json(root/AT/'admission.json',report)
    for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation','acq-elec-5-host-bus-contract'):
        brief=overlay['judgments'].get('judgments/'+key+'.json')
        if brief is None:raise ValueError('role consumer requires existing fresh engineering judgments')
        brief['sources'].append(AT+'/admission.json')
        brief['questions'].append('Evaluate the current board against reference-roles/admission.json: distinguish declared_bus_capacitance_pf in the fresh full-board checks from host_role_envelope_capacitance_pf, the conservative role upper bound. Preserve captured conditions; no source qualification or R5 judgment discharges this fresh obligation.')
    return report
