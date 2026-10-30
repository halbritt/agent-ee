"""Portable authored consumer contracts; never native/source qualification evidence."""
import copy
import tempfile
import unittest
from pathlib import Path

from agent_ee import acquisition_roles as roles, block_adaptation, block_composition, requirement_map, typed_overlay, storage
from agent_ee.catalog import strict_json
from agent_ee.reference_blocks import block_contract
from agent_ee.schematic_emit import Quoted
from agent_ee.symbol_library import children
from tests.test_block_adaptation import dump, parse_quoted


def authored(test, root):
    from tests.test_parts_subset import captured_fixture
    from tests.test_native_binding import GEN, native_graph
    from agent_ee.part_selection import selection_check
    from tests.test_typed_overlay import PodOverlayTests
    document=captured_fixture(test,root)
    refs=sorted(set.union(*(v[1] for v in roles.ROLES.values())))
    req=strict_json((root/'selection-requirements.json').read_bytes())
    req['parts']=[{**copy.deepcopy(req['parts'][0]),'id':ref} for ref in refs]
    storage.write_json(root/'selection-requirements.json',req)
    result=selection_check(root/'snapshot',document['catalog']['manifest_sha256'],req)
    test.assertEqual(result['outcome'],'pass')
    storage.write_json(root/'selection.json',result)
    for key in ('requirements','result'):
        document['selection'][key]['sha256']=storage.digest(root/document['selection'][key]['path'])
    original=copy.deepcopy(document['parts'][0])
    document['parts']=[{**copy.deepcopy(original),'id':ref,'selection_id':ref,'instances':[dict(sheet='/',reference=ref)]} for ref in refs]
    assignments=strict_json((root/'assignments.json').read_bytes())
    assignments['components']=[{**copy.deepcopy(assignments['components'][0]),'reference':ref} for ref in refs]
    storage.write_json(root/'assignments.json',assignments);document['cad']['assignments']['sha256']=storage.digest(root/'assignments.json')
    storage.write_json(root/'parts.json',document)
    tree=parse_quoted(GEN.schematic().encode())
    original_node=next(n for n in children(tree,'symbol') if next(p[2] for p in children(n,'property') if p[1]=='Reference')=='R1')
    for n in list(children(tree,'symbol')):tree.remove(n)
    graph=native_graph();unit=copy.deepcopy(graph['components']['R1']);graph['components']={}
    for ref in refs:
        n=copy.deepcopy(original_node)
        children(n,'uuid')[0][1]=Quoted(GEN.uid('role-'+ref))
        next(p for p in children(n,'property') if p[1]=='Reference')[2]=Quoted(ref)
        for instances in children(n,'instances'):
            for project in children(instances,'project'):
                for path in children(project,'path'):children(path,'reference')[0][1]=Quoted(ref)
        tree.append(n)
        graph['components'][ref]={**copy.deepcopy(unit),'reference':ref,'uuid':GEN.uid('role-'+ref)}
    (root/'project').mkdir();(root/'project/acquisition.kicad_sch').write_bytes(dump(tree))
    (root/'project/acquisition.kicad_pcb').write_text('(kicad_pcb (version 20250114) (generator "owned"))')
    helper=PodOverlayTests();helper.setUp();test.addCleanup(helper.tearDown)
    declaration=copy.deepcopy(helper.map);declaration['scope'].update(schematic='project/acquisition.kicad_sch',board='project/acquisition.kicad_pcb',inputs=[dict(path='.',at='owned')])
    # Individual captures below are authored; the positive tests exercise source
    # accounting and fresh API contracts, not the qualified-run admission gate.
    declaration['scope']['inputs']=[dict(path=n,at=n) for n in ('project','parts.json','requirements.md','snapshot','Owned.kicad_sym','Owned.pretty','cad.json','assignments.json','selection-requirements.json','selection.json','datasheet.txt','datasheet-source.txt')]
    declaration['scope']['mutable_inputs']=['project/acquisition.kicad_sch','project/acquisition.kicad_pcb']
    declaration['scope']['stages']['subjects']['verification']['path']='project/acquisition.kicad_sch'
    for name in ('spec.md','spec-record.json'):(root/name).write_bytes((helper.base/name).read_bytes())
    next(e for e in declaration['requirements'] if e['id']=='POD-AC-3')['checks'].insert(0,dict(id='netlist',kind='netlist',params={},stage='verification'))
    storage.write_json(root/'requirement-map.json',declaration)
    overlay=copy.deepcopy(helper.overlay)
    overlay['mappings']['POD-AC-3']['spans'][0]['checks'].insert(0,'netlist')
    overlay['base']['requirement_map_sha256']=storage.digest(root/'requirement-map.json')
    for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation','acq-elec-5-host-bus-contract'):
        # Reuse the existing authored judgment obligation for this software test.
        overlay['judgments']['judgments/'+key+'.json']=copy.deepcopy(next(iter(overlay['judgments'].values())))
    raw={n:(root/n).read_bytes() for n in requirement_map.input_inventory(root)}
    captures={};entries={}
    for rid,(ident,members) in roles.ROLES.items():
        block='blocks/'+rid;payload=dict(raw)
        subset=copy.deepcopy(document);subset['parts']=[p for p in subset['parts'] if p['id'] in members]
        ap='roles/'+rid+'/assignments.json';ass=copy.deepcopy(assignments);ass['components']=[c for c in ass['components'] if c['reference'] in members]
        payload[ap]=storage.json_bytes(ass);subset['cad']['assignments']=dict(path=ap,sha256=storage.identity(ass))
        # identity() hashes canonical JSON without a newline; use exact bytes.
        from hashlib import sha256
        subset['cad']['assignments']['sha256']=sha256(payload[ap]).hexdigest()
        pp='roles/'+rid+'/parts.json';payload[pp]=storage.json_bytes(subset)
        iface='roles/'+rid+'/interface.json';ir=rid+'-interface';mr=rid+'-members'
        interface=dict(schema='agent-ee.interface/1',allowed_edits=[],terminals={},
            components={ref:dict(locations=[dict(sheet='/',reference=ref)],symbols=['Owned:R']) for ref in members},
            invariants=[dict(id='value-'+ref,kind='value',component=ref,equals='10k',requirement=ir,source=dict(path='requirements.md',locator='line 1')) for ref in sorted(members)])
        payload[iface]=storage.json_bytes(interface)
        selected=[dict(sheet='/',reference=ref,uuids=[GEN.uid('role-'+ref)]) for ref in sorted(members)]
        requirements=[dict(id=ir,statement='Retain authored resistor identities',checks=[ir],source=dict(path='requirements.md',locator='line 1')),
            dict(id=mr,statement='Retain exact allocated purchasing identities',checks=[mr],source=dict(path='requirements.md',locator='line 1'))]
        checks=[dict(id=ir,kind='hardware_interface',params=dict(contract=iface,netlist_check='netlist')),
            dict(id=mr,kind='parts_subset',params=dict(manifest=pp,netlist_check='netlist',requirement_id=mr,reference_set=selected))]
        for i in range({'power':3,'analog':2,'host':1}[ident]):
            cid=rid+'-response'+str(i)
            requirements.append(dict(id=cid,statement='Evaluate authored response',checks=[cid],verification_phase='adaptation',conditions=['J2 zero external load; no external drive; nominal25C'],source=dict(path='requirements.md',locator='line 1')))
            checks.append(dict(id=cid,kind='rc_transient',params=dict(netlist_check='netlist',input_net='P',output_net='P',ground_net='GND',measurements=[dict(name='v',at_ms=1,min=0,max=1)])))
        if ident=='analog':
            rules_path='roles/'+rid+'/ties.json'
            payload[rules_path]=storage.json_bytes(dict(schema='agent-ee.topology-rules/1',classes={'Owned:R':dict(**{'class':'resistor'},terminals={'a':'1','b':'2'})},rules=[dict(id='ground',type='pin_net',requirement='historical-tag',terminal=dict(reference='R1',pin='1'),net='GND')]))
            checks[2]=dict(id=checks[2]['id'],kind='topology_rules',params=dict(rules=rules_path,netlist_check='netlist'))
            for name in ('analysis/test_geometry.py','analysis/test_adapter.py'):payload[name]=b'# Authored source-only contract. Never executed.\n'
            payload['analysis/test_model.json']=b'{}\n'
            checks[3]=dict(id=checks[3]['id'],kind='measurements',params=dict(adapter='analysis/test_adapter.py',parameters={},
                tool=dict(argv=['/usr/bin/python3','/candidate/analysis/test_geometry.py','/candidate','/work/result.json'],
                    inputs=['analysis/test_geometry.py','analysis/test_model.json'],sha256=storage.digest('/usr/bin/python3'),domain='manufacturing'),
                limits={'gap':dict(unit='mm',condition='authored',min=0)}))
        record=dict(schema='agent-ee.block/1',id=rid,version='4',role=ident,source=dict(repository='owned',commit='a'*40,paths={'project/acquisition.kicad_sch':'project/acquisition.kicad_sch'},license='Owned',upstream_origin='authored',upstream_license='Owned'),
            sheet='project/acquisition.kicad_sch',parts_manifest=pp,interface=iface,boundary_ports=[],instances=[dict(path='/',uuid_path='/',sheet='project/acquisition.kicad_sch',references=sorted(members))],requirements=requirements,check_set=checks,runs=[])
        payload['block-contract.json']=storage.json_bytes(block_contract(record));record['files']={n:sha256(b).hexdigest() for n,b in payload.items()}
        payload['block.json']=storage.json_bytes(record)
        captures.update({block+'/'+n:b for n,b in payload.items()})
        cp='compositions/'+rid+'.json'
        comp=dict(schema='agent-ee.block-composition/1',sources=[dict(sheet=record['sheet'],uuid_path='/',sha256=record['files'][record['sheet']],references={r:r for r in sorted(members)},nets={'/P':'P','GND':'GND'},reason='authored')],
            ports=[dict(name=n,net_name=n,direction='passive',voltage_domain='test',current_a=None,alias_of=None,reason='authored') for n in ('P','GND')],reason='authored source-only')
        captures[cp]=storage.json_bytes(comp);rp='reports/'+rid+'.json';captures[rp]=b'{}\n'
        entries[rid]=dict(block=block,block_sha256=sha256(payload['block.json']).hexdigest(),composition=dict(path=cp,sha256=sha256(captures[cp]).hexdigest()),report=dict(path=rp,sha256=sha256(captures[rp]).hexdigest()),run_locations={},repository_locations={})
    doc=dict(schema=roles.SCHEMA,reference='acquisition-control',files={n:sha256(b).hexdigest() for n,b in captures.items()},roles=entries,
        application=dict(j2_external_load_a=0,j2_external_drive=False,bus_capacitance_pf=100,temperature_c=25),host_observation=dict(policy_files=__import__('agent_ee.run_record',fromlist=['policy_files']).policy_files(),runtime_files={}))
    return doc,captures,overlay,declaration,helper.record,helper.spec,graph


class ConsumerTests(unittest.TestCase):
    def test_root_membership_admission_survives_empty_scaffold_without_accepting_it(self):
        from agent_ee.schematic_emit import parse_quoted, format_sexpr
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc,captures,overlay,declaration,record,spec,graph=authored(self,root)
            report=roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
            derived=typed_overlay.derive_map(storage.read_json(root/'requirement-map.json'),overlay)
            obligations=requirement_map.load_obligations(
                storage.json_bytes(typed_overlay.derive_obligations(record,spec,overlay)),record,spec)
            sheet=root/declaration['scope']['schematic']
            tree=parse_quoted(sheet.read_bytes())
            tree[:]=[node for node in tree if not isinstance(node,list) or node[0]!='symbol']
            sheet.write_text('\n'.join(format_sexpr(tree))+'\n')
            files=requirement_map.input_inventory(root)
            scope,_=requirement_map.account_requirements(derived,record,spec,
                files=files,inputs=root,obligations=obligations)
            for instance in report['instances']:
                cid=storage.read_json(root/instance)['id']+'-provenance'
                block_adaptation.capability(dict(instance=instance,netlist_check='netlist'),scope,files,root,cid)
            block_adaptation.root_coverage(root,scope,graph)
            with self.assertRaisesRegex(ValueError,'complete native'):
                block_adaptation.root_coverage(root,scope,dict(graph,components={}))
            wrong=copy.deepcopy(scope)
            check=next(c for c in wrong['checks'] if c['id'].startswith('analog-') and c['kind']=='parts_subset')
            check['params']['reference_set'][0]['uuids']=['77777777-7777-4777-8777-777777777777']
            analog=next(p for p in report['instances'] if p.endswith('/analog.json'))
            with self.assertRaisesRegex(ValueError,'parameters differ'):
                block_adaptation.capability(dict(instance=analog,netlist_check='netlist'),wrong,files,root,'analog-provenance')

    def test_role_checks_precede_fabrication_completion_and_bound_followers(self):
        from agent_ee.baseline import generation_marker, GENERATION_SCHEMA
        from agent_ee.run_record import validate_scope
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc,captures,overlay,declaration,record,spec,_=authored(self,root)
            declaration['scope']['generation']=dict(schema=GENERATION_SCHEMA,done_check='package')
            storage.write_json(root/'requirement-map.json',declaration)
            tail=[dict(id='package',kind='fab_package',params={},stage='verification'),
                  dict(id='dfm',kind='jlc_dfm',params=dict(fab_check='package'),stage='verification'),
                  dict(id='roundtrip',kind='fab_roundtrip',params=dict(package_check='package'),stage='verification')]
            overlay['check_additions']['declare'].extend(tail)
            overlay['mappings']['POD-AC-3']['spans'][0]['checks'].extend(c['id'] for c in tail)
            roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
            derived=typed_overlay.derive_map(storage.read_json(root/'requirement-map.json'),overlay)
            obligations=requirement_map.load_obligations(storage.json_bytes(typed_overlay.derive_obligations(record,spec,overlay)),record,spec)
            scope,receipts=requirement_map.account_requirements(derived,record,spec,
                files=requirement_map.input_inventory(root),inputs=root,obligations=obligations)
            validate_scope(scope)
            self.assertEqual(generation_marker(scope),declaration['scope']['generation'])
            ids=[c['id'] for c in scope['checks']]
            self.assertEqual(ids[-3:],['package','dfm','roundtrip'])
            for rid,cids in overlay['block_requirements'].items():
                self.assertEqual(next(r for r in receipts if r['id']==rid)['bound_ids'],cids)
                self.assertTrue(all(ids.index(cid)<ids.index('package') for cid in cids))
            self.assertEqual(scope['checks'][-3:],tail)
            wrong=copy.deepcopy(scope)
            wrong['checks'][-1]['params']['package_check']='unrelated'
            with self.assertRaisesRegex(ValueError,'done check last'):
                generation_marker(wrong)

    def test_three_authored_roles_transport_all_rows_and_refuse_missing_or_tampered_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'base';root.mkdir();doc,captures,overlay,declaration,record,spec,graph=authored(self,root)
            report=roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
            derived=typed_overlay.derive_map(requirement_map.map_declaration((root/'requirement-map.json').read_bytes()),overlay)
            obligations=requirement_map.load_obligations(storage.json_bytes(typed_overlay.derive_obligations(record,spec,overlay)),record,spec)
            files=requirement_map.input_inventory(root)
            scope,receipts=requirement_map.account_requirements(derived,record,spec,files=files,inputs=root,obligations=obligations)
            rows=requirement_map.block_sources(scope,files,root)
            self.assertEqual({r['id'] for r in rows},set(overlay['block_requirements']))
            self.assertEqual(len([r for r in rows if 'response' in r['id']]),6)
            for instance in report['instances']:
                cid=strict_json((root/instance).read_bytes())['id']+'-provenance'
                block_adaptation.capability(dict(instance=instance,netlist_check='netlist'),scope,files,root,cid)
            block_adaptation.root_coverage(root,scope,graph)
            measured=next(c for c in scope['checks'] if c['kind']=='measurements')
            self.assertEqual(measured['params']['tool']['argv'][1],'/candidate/'+measured['params']['tool']['inputs'][0])
            self.assertEqual(measured['params']['tool']['argv'][2],'/candidate')
            wrong=copy.deepcopy(scope)
            next(c for c in wrong['checks'] if c['kind']=='measurements')['params']['tool']['argv'][1]='/candidate/analysis/test_geometry.py'
            analog=next(p for p in report['instances'] if p.endswith('/analog.json'))
            with self.assertRaisesRegex(ValueError,'parameters differ'):
                block_adaptation.capability(dict(instance=analog,netlist_check='netlist'),wrong,files,root,'analog-provenance')
            rule_check=next(c for c in scope['checks'] if c['kind']=='topology_rules')
            rule_path=root/rule_check['params']['rules'];saved=rule_path.read_bytes()
            rules_doc=strict_json(saved);rules_doc['rules'][0]['requirement']='historical-tag';storage.write_json(rule_path,rules_doc)
            with self.assertRaisesRegex(ValueError,'asset changes'):
                block_adaptation.capability(dict(instance=analog,netlist_check='netlist'),scope,{**files,str(rule_path.relative_to(root)):storage.digest(rule_path)},root,'analog-provenance')
            rule_path.write_bytes(saved)
            # The two production judgment names above are fixture aliases only;
            # retain the POD fixture's actual authored judgment during its seal.
            for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation','acq-elec-5-host-bus-contract'):
                overlay['judgments'].pop('judgments/'+key+'.json')
            overlay_path=Path(tmp)/'overlay.json';storage.write_json(overlay_path,overlay)
            sealed=Path(tmp)/'sealed'
            typed_overlay.prepare(root,overlay_path,sealed,added_sources={})
            sealed_map=strict_json((sealed/'requirement-map.json').read_bytes())
            self.assertEqual(sealed_map['scope']['block_instances'],report['instances'])
            self.assertFalse((sealed/'judgments').exists())
            compiled,receipt=requirement_map.compile_map((sealed/'requirement-map.json').read_bytes(),
                (sealed/'spec-record.json').read_bytes(),(sealed/'spec.md').read_bytes(),
                files=requirement_map.input_inventory(sealed),inputs=sealed,obligations_raw=(sealed/'obligations.json').read_bytes())
            self.assertEqual({r['id'] for r in rows},set(overlay['block_requirements']))
            appended=copy.deepcopy(receipt);appended['requirements'].append({**appended['requirements'][-1],'id':'post-seal-row'})
            with self.assertRaisesRegex(ValueError,'sealed requirements accounting'):
                requirement_map.bind_receipt(appended,spec,source_hashes={},accounting=receipts)
            forged=copy.deepcopy(receipt)
            next(r for r in forged['requirements'] if r.get('source'))['source']={'path':'forged.json','locator':'fabricated'}
            with self.assertRaisesRegex(ValueError,'sealed requirements accounting'):
                requirement_map.bind_receipt(forged,spec,source_hashes={},accounting=receipts)
            source_rows={r['id']:r for r in compiled['baseline']['requirements']}
            self.assertTrue(all(source_rows[r['id']]['source']==r['baseline']['source'] for r in rows))
            # Scoped comparison keeps every selected physical UUID and pin net.
            actual=copy.deepcopy(graph);actual['nets']={'p':dict(name='/P',terminals=tuple((r,'1') for r in actual['components'])),
                'g':dict(name='GND',terminals=tuple((r,'2') for r in actual['components']))}
            actual['memberships']={(r,p):('p' if p=='1' else 'g') for r in actual['components'] for p in ('1','2')}
            members=roles.ROLES['acq-tps7a2033-power'][1]
            selected=[dict(delivered=r,native=copy.deepcopy(actual['components'][r]),no_connects=[],nets={'1':'P','2':'GND'}) for r in sorted(members)]
            selected_scope=[dict(sheet='/',reference=r,uuids=sorted(actual['components'][r]['uuid'].split())) for r in sorted(members)]
            occurrence=dict(path='/',tree=parse_quoted((root/'project/acquisition.kicad_sch').read_bytes()))
            self.assertEqual(block_composition.compare(selected,actual,occurrence,{'P':'/P','GND':'GND'},reference_set=selected_scope),([],[]))
            self.assertEqual(len(block_composition.compare(selected,actual,occurrence,{'P':'/P','GND':'GND'})[0]),10)
            changed_part=copy.deepcopy(actual);changed_part['components']['U1']['value']='FORGED'
            self.assertTrue(block_composition.compare(selected,changed_part,occurrence,{'P':'/P','GND':'GND'},reference_set=selected_scope)[0])
            for change in ('missing','extra','shadow'):
                altered=copy.deepcopy(overlay)
                if change=='missing':altered['block_requirements'].pop(next(iter(altered['block_requirements'])))
                elif change=='extra':altered['block_requirements']['unowned']=['erc']
                else:altered['block_requirements']['POD-AC-3']=['erc']
                with self.subTest(change=change),self.assertRaises(ValueError):
                    d=typed_overlay.derive_map(requirement_map.map_declaration((root/'requirement-map.json').read_bytes()),altered)
                    requirement_map.account_requirements(d,record,spec,files=files,inputs=root,obligations=obligations)
            overlap=copy.deepcopy(scope);overlap['block_instances'].append(scope['block_instances'][0])
            with self.assertRaisesRegex(ValueError,'disjoint'):block_adaptation.root_coverage(root,overlap,graph)
            changed=copy.deepcopy(graph);changed['components']['UNOWNED']=copy.deepcopy(next(iter(changed['components'].values())))
            with self.assertRaisesRegex(ValueError,'complete native'):block_adaptation.root_coverage(root,scope,changed)
            changed=copy.deepcopy(graph);changed['components']['U1']['uuid']='77777777-7777-4777-8777-777777777777'
            with self.assertRaisesRegex(ValueError,'complete native'):block_adaptation.root_coverage(root,scope,changed)

    def test_unqualified_and_incompatible_context_cannot_publish(self):
        for change in ('unqualified','missing-role','load','drive','bus','source-drift'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);doc,captures,*_=authored(self,root)
                for p,b in captures.items():
                    target=root/'context'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
                if change=='source-drift':
                    path=next(iter(captures));(root/'context'/path).write_bytes(captures[path]+b' ')
                elif change=='missing-role':doc['roles'].pop(next(iter(doc['roles'])))
                elif change=='load':doc['application']['j2_external_load_a']=.001
                elif change=='drive':doc['application']['j2_external_drive']=True
                elif change=='bus':doc['application']['bus_capacitance_pf']=150
                with self.assertRaises(ValueError):roles.read(root/'context',storage.json_bytes(doc),100)

    def test_public_role_options_require_complete_pins_before_source_admission(self):
        from agent_ee.acquisition_prepare import prepare
        for args in (dict(role_context='missing'),dict(role_source='missing'),dict(role_context_sha256='a'*64)):
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,'source root and exact context'):
                prepare(None,None,None,added_sources={},**args)


class CohortTests(unittest.TestCase):
    def context(self, versions=('5','5','5'), capacitance=125):
        doc=dict(roles={},application=dict(j2_external_load_a=0,j2_external_drive=False,temperature_c=25,bus_capacitance_pf=capacitance))
        captures={}
        from hashlib import sha256
        pref=dict(path=roles.POLICY_PATH,sha256=sha256(roles.POLICY_RAW).hexdigest())
        for (rid,(_,members)),version in zip(roles.ROLES.items(),versions):
            prefix='blocks-v'+version+'/'+rid
            doc['roles'][rid]=dict(block=prefix,composition=dict(path='compositions-v'+version+'/'+rid+'.json'),report=dict(path='reports-v'+version+'/'+rid+'.json'))
            app=dict(adaptation_policy=pref,selected_boundary=dict(i2c_line_capacitance=dict(max='150',unit='pF',selection='conservative engineering adaptation envelope',source=pref)))
            captures[prefix+'/'+roles.POLICY_PATH]=roles.POLICY_RAW
            captures[prefix+'/roles/'+rid+'/application-contract.json']=storage.json_bytes(app)
            parts=copy.deepcopy(roles.POLICY['base_allocations'][rid])
            if version=='6':
                for row in parts:
                    if row['id'] in ('R1','R2'):row.update(manufacturer='PANASONIC',mpn='ERJ3EKF1001V',lcsc='C118155')
            app['members']=[{k:m[k] for k in ('id','lcsc','manufacturer','mpn','lib_id','footprint')} for m in parts]
            app['limits']=[dict(id='i2c-capacitance',max='150',source=pref)]
            captures[prefix+'/roles/'+rid+'/application-contract.json']=storage.json_bytes(app)
            captures[prefix+'/parts.json']=storage.json_bytes(dict(parts=parts))
            checks=[]
            if rid=='acq-jst-ph-i2c':
                model=copy.deepcopy(roles.POLICY['host_interval_template']);model['requirement_sources']={}
                captures[prefix+'/interval.json']=storage.json_bytes(model)
                checks=[dict(id='i2c-bus-envelope-intervals',params=dict(assertions='interval.json'))]
            captures[prefix+'/block.json']=storage.json_bytes(dict(version=version,parts_manifest='parts.json',check_set=checks))
        return doc,captures

    def test_finite_base_and_alternate_cohorts_preserve_exact_selected_allocations(self):
        for versions in (('5','5','5'),('5','6','5')):
            for value in (100,125,150,'125.5'):
                with self.subTest(versions=versions,value=value):
                    doc,captures=self.context(versions,value)
                    result=roles.cohort(doc,captures,value)
                    self.assertEqual(result['versions'],list(versions))
                    self.assertEqual(result['declared_bus_capacitance_pf'],str(value))
                    self.assertEqual(result['host_role_envelope_capacitance_pf'],'150')
        doc,captures=self.context(('4','4','4'),100)
        self.assertEqual(roles.cohort(doc,captures,100)['host_role_envelope_capacitance_pf'],'100')
        with self.assertRaises(ValueError):roles.cohort(doc,captures,150)
        for versions in (('4','6','4'),('5','4','5'),('6','6','6'),('5','6','4')):
            doc,captures=self.context(versions)
            with self.subTest(versions=versions),self.assertRaises(ValueError):roles.cohort(doc,captures,125)

    def test_numeric_context_and_j2_are_strict_and_equal_to_brief(self):
        for value in (True,100.0,'1e2','NaN','100.0','99','151','125.12345678901234567890123456789'):
            doc,captures=self.context(capacitance=value)
            with self.subTest(value=value),self.assertRaises(ValueError):roles.cohort(doc,captures,125)
        for field,value in (('temperature_c',25.0),('temperature_c',True),('j2_external_load_a',False),('j2_external_load_a',.001),('j2_external_drive',0),('j2_external_drive',True)):
            doc,captures=self.context();doc['application'][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):roles.cohort(doc,captures,125)
        doc,captures=self.context()
        with self.assertRaises(ValueError):roles.cohort(doc,captures,100)

    def test_stale_template_mixed_policy_source_and_one_resistor_changes_refuse(self):
        for change in ('template','policy','boundary','member','limit-source','part','provided-package','overlap','report'):
            doc,captures=self.context(('5','6','5'))
            rid='acq-jst-ph-i2c';prefix=doc['roles'][rid]['block']+'/'
            if change=='template':
                model=strict_json(captures[prefix+'interval.json']);model['parameters']['bus_c']['max']='100e-12 F';captures[prefix+'interval.json']=storage.json_bytes(model)
            elif change=='policy':captures[prefix+roles.POLICY_PATH]+=b' '
            elif change=='boundary':
                path=prefix+'roles/'+rid+'/application-contract.json';app=strict_json(captures[path]);app['selected_boundary']['i2c_line_capacitance']['max']='200';captures[path]=storage.json_bytes(app)
            elif change in ('member','limit-source'):
                path=prefix+'roles/'+rid+'/application-contract.json';app=strict_json(captures[path])
                if change=='member':app['members'][0]['lcsc']='C22548'
                else:app['limits'][0]['source']={'path':'spec.md','locator':'old100pF'}
                captures[path]=storage.json_bytes(app)
            elif change in ('part','provided-package'):

                path=doc['roles']['acq-ads1115-two-channel']['block']+'/parts.json';parts=strict_json(captures[path]);row=next(r for r in parts['parts'] if r['id']=='R2');row['lcsc' if change=='part' else 'package']='C22548' if change=='part' else '0805';captures[path]=storage.json_bytes(parts)
            elif change=='overlap':doc['roles'][rid]['block']=doc['roles']['acq-tps7a2033-power']['block']
            else:doc['roles'][rid]['report']['path']=doc['roles']['acq-tps7a2033-power']['report']['path']
            with self.subTest(change=change),self.assertRaises(ValueError):roles.cohort(doc,captures,125)

    def test_authored_v5_transport_propagates_versions_and_labels_judgment_context(self):
        # Source-only authored graphs exercise the existing typed APIs. They do
        # not have native runs and cannot pass read() qualification admission.
        from hashlib import sha256
        for value in (100,125,150):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'base';root.mkdir();doc,captures,overlay,_,record,spec,_=authored(self,root)
                doc['application']['bus_capacitance_pf']=value
                for entry in doc['roles'].values():
                    path=entry['block']+'/block.json';source=strict_json(captures[path]);source['version']='5'
                    contract=entry['block']+'/block-contract.json';captures[contract]=storage.json_bytes(block_contract(source))
                    source['files']['block-contract.json']=sha256(captures[contract]).hexdigest()
                    captures[path]=storage.json_bytes(source);entry['block_sha256']=sha256(captures[path]).hexdigest()
                doc['files']={p:sha256(raw).hexdigest() for p,raw in captures.items()}
                report=roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
                self.assertEqual(report['capacitance_admission']['declared_bus_capacitance_pf'],str(value))
                self.assertEqual(report['capacitance_admission']['host_role_envelope_capacitance_pf'],'150')
                for path in report['instances']:self.assertEqual(strict_json((root/path).read_bytes())['version'],'5')
                host=overlay['judgments']['judgments/acq-elec-5-host-bus-contract.json']
                self.assertIn(roles.AT+'/admission.json',host['sources'])
                self.assertTrue(any('declared_bus_capacitance_pf' in q and 'host_role_envelope_capacitance_pf' in q for q in host['questions']))
                derived=typed_overlay.derive_map(requirement_map.map_declaration((root/'requirement-map.json').read_bytes()),overlay)
                obligations=requirement_map.load_obligations(storage.json_bytes(typed_overlay.derive_obligations(record,spec,overlay)),record,spec)
                scope,_=requirement_map.account_requirements(derived,record,spec,files=requirement_map.input_inventory(root),inputs=root,obligations=obligations)
                for path in report['instances']:
                    ident=strict_json((root/path).read_bytes())['id']
                    block_adaptation.capability(dict(instance=path,netlist_check='netlist'),scope,requirement_map.input_inventory(root),root,ident+'-provenance')

                for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation','acq-elec-5-host-bus-contract'):
                    overlay['judgments'].pop('judgments/'+key+'.json')
                overlay_path=root.parent/('overlay-'+str(value)+'.json');storage.write_json(overlay_path,overlay)
                sealed=root.parent/('sealed-'+str(value))
                typed_overlay.prepare(root,overlay_path,sealed,added_sources={})
                compiled,_=requirement_map.compile_map((sealed/'requirement-map.json').read_bytes(),
                    (sealed/'spec-record.json').read_bytes(),(sealed/'spec.md').read_bytes(),
                    files=requirement_map.input_inventory(sealed),inputs=sealed,obligations_raw=(sealed/'obligations.json').read_bytes())
                self.assertEqual(compiled['block_instances'],report['instances'])

    def test_inactive_sibling_application_does_not_override_own_role_policy(self):
        doc,captures=self.context(('5','6','5'))
        for rid,entry in doc['roles'].items():
            for sibling in roles.ROLES:
                if sibling!=rid:captures[entry['block']+'/roles/'+sibling+'/application-contract.json']=b'{"inactive_historical_context":true}\n'
        self.assertEqual(roles.cohort(doc,captures,125)['versions'],['5','6','5'])


    def test_common_policy_template_binds_exactly_to_each_public_brief(self):
        for versions in (('5','5','5'),('5','6','5')):
            doc,captures=self.context(versions,copy.deepcopy(roles.BRIEF_CAPACITANCE))
            original=storage.json_bytes(doc)
            for value in (100,125,150):
                result=roles.cohort(doc,captures,value)
                bound=roles.bind_application(doc,value,envelope=result['host_role_envelope_capacitance_pf'])
                self.assertEqual(bound['application']['bus_capacitance_pf'],str(value))
                self.assertEqual(bound['application_binding']['source_application'],doc['application'])
                self.assertEqual(storage.json_bytes(doc),original)
                self.assertNotEqual(storage.json_bytes(bound),original)
            for wrong in ({'from':'public-brief','policy':'other'},{**roles.BRIEF_CAPACITANCE,'max':200}):
                altered=copy.deepcopy(doc);altered['application']['bus_capacitance_pf']=wrong
                with self.assertRaises(ValueError):roles.cohort(altered,captures,125)
        doc,captures=self.context(('4','4','4'),copy.deepcopy(roles.BRIEF_CAPACITANCE))
        with self.assertRaises(ValueError):roles.cohort(doc,captures,100)


class ProducerBoundaryTests(unittest.TestCase):
    def test_adc_carrier_comes_from_the_selected_captured_manifest(self):
        # Authored accessor input, not evidence that these source roles qualify.
        doc = dict(roles={'acq-ads1115-two-channel': dict(block='selected-analog')})
        for lcsc in ('C37593', 'C468683'):
            with self.subTest(lcsc=lcsc):
                captures = {'selected-analog/block.json': storage.json_bytes(dict(parts_manifest='parts.json')),
                    'selected-analog/parts.json': storage.json_bytes(dict(parts=[dict(id='U2',lcsc=lcsc)])),
                    'inactive-analog/parts.json': storage.json_bytes(dict(parts=[dict(id='U2',lcsc='C404027')]))}
                self.assertEqual(roles.adc_carrier(doc,captures),lcsc)
                for members in ([], [dict(id='R1',lcsc=lcsc)], [dict(id='U2',lcsc=lcsc)]*2):
                    captures['selected-analog/parts.json'] = storage.json_bytes(dict(parts=members))
                    with self.assertRaisesRegex(ValueError, 'exactly one U2'):
                        roles.adc_carrier(doc,captures)

    def test_public_parser_decimal_reaches_role_admission_and_preserves_json_strictness(self):
        from decimal import Decimal
        from agent_ee.acquisition_prepare import ADOPTION, parse_brief
        helper=CohortTests()
        for spelling,canonical in (('100','100'),('00100.0','100'),('125.50','125.5'),('150.0','150')):
            parsed=parse_brief((ADOPTION+'\nTotal I2C line capacitance shall be at most '+spelling+' pF per line, including board, host and cable.\n').encode())['value']
            self.assertIs(type(parsed),Decimal)
            for versions in (('5','5','5'),('5','6','5')):
                for operand in (canonical,copy.deepcopy(roles.BRIEF_CAPACITANCE)):
                    with self.subTest(spelling=spelling,versions=versions,operand=operand):
                        doc,captures=helper.context(versions,operand)
                        result=roles.cohort(doc,captures,parsed)
                        self.assertEqual(result['declared_bus_capacitance_pf'],canonical)
                        bound=roles.bind_application(doc,parsed,envelope='150')
                        self.assertEqual(bound['application']['bus_capacitance_pf'],canonical)
            # Keep JSON operands canonical even when the public parser accepts
            # padded prose. The Python Decimal is allowed only as brief input.
            for operand in (Decimal(canonical),canonical+'.0' if '.' not in canonical else '125.50'):
                doc,captures=helper.context(capacitance=operand)
                with self.subTest(json_operand=operand),self.assertRaises(ValueError):roles.cohort(doc,captures,parsed)
        for value in (Decimal('NaN'),Decimal('sNaN'),Decimal('Infinity'),Decimal('99'),Decimal('151'),Decimal('125.12345678901234567890123456789'),True,125.0):
            doc,captures=helper.context(capacitance=copy.deepcopy(roles.BRIEF_CAPACITANCE))
            with self.subTest(invalid_brief=value),self.assertRaises(ValueError):roles.cohort(doc,captures,value)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);doc,captures,*_=authored(self,root)
            for p,b in captures.items():
                target=root/'context'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
            parsed=parse_brief((ADOPTION+'\nTotal I2C line capacitance shall be at most 100.0 pF per line, including board, host and cable.\n').encode())['value']
            with self.assertRaisesRegex(ValueError,'qualified role requires its exact declared source runs'):
                roles.read(root/'context',storage.json_bytes(doc),parsed)

    def test_preseal_source_and_operative_carrier_mismatch_refuses_both_directions(self):
        from hashlib import sha256
        identities={'C22548':dict(lcsc='C22548',mpn='RC0603FR-071KL',manufacturer='YAGEO'),
                    'C118155':dict(lcsc='C118155',mpn='ERJ3EKF1001V',manufacturer='PANASONIC')}
        for source_id,operative_id,property_drift in (('C22548','C118155',False),('C118155','C22548',False),
                ('C22548','C22548',False),('C118155','C118155',False),('C22548','C22548',True)):
            with self.subTest(source=source_id,operative=operative_id,property_drift=property_drift),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);doc,captures,overlay,*_=authored(self,root)
                operative=strict_json((root/'parts.json').read_bytes())
                for row in operative['parts']:
                    if row['id'] in ('R1','R2'):row.update(identities[operative_id])
                if property_drift:operative['property_fields']['manufacturer']='Maker'
                storage.write_json(root/'parts.json',operative)
                for rid,entry in doc['roles'].items():
                    path=entry['block']+'/block.json';record=strict_json(captures[path])
                    record['version']='6' if rid=='acq-ads1115-two-channel' and source_id=='C118155' else '5'
                    if rid=='acq-ads1115-two-channel':
                        pp=entry['block']+'/'+record['parts_manifest'];parts=strict_json(captures[pp])
                        for row in parts['parts']:
                            if row['id'] in ('R1','R2'):row.update(identities[source_id])
                        captures[pp]=storage.json_bytes(parts);record['files'][record['parts_manifest']]=sha256(captures[pp]).hexdigest()
                    contract=entry['block']+'/block-contract.json';captures[contract]=storage.json_bytes(block_contract(record));record['files']['block-contract.json']=sha256(captures[contract]).hexdigest()
                    captures[path]=storage.json_bytes(record);entry['block_sha256']=sha256(captures[path]).hexdigest()
                doc['files']={p:sha256(raw).hexdigest() for p,raw in captures.items()}
                if source_id!=operative_id or property_drift:
                    with self.assertRaisesRegex(ValueError,'operative role (purchasing allocation|property fields) differ'):
                        roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
                    self.assertFalse((root/roles.AT/'admission.json').exists())
                    self.assertNotIn('block_requirements',overlay)
                else:
                    report=roles.apply(root,dict(parts={'manifest':'parts.json'}),overlay,doc,captures)
                    self.assertTrue((root/roles.AT/'admission.json').exists())
                    fresh=next(c for c in overlay['check_additions']['declare'] if c['kind']=='parts_subset' and c['id'].startswith('analog-'))
                    selected=strict_json((root/fresh['params']['manifest']).read_bytes())
                    self.assertEqual({p['lcsc'] for p in selected['parts'] if p['id'] in ('R1','R2')},{operative_id})
                    self.assertEqual(report['capacitance_admission']['versions'],['5','6' if source_id=='C118155' else '5','5'])
