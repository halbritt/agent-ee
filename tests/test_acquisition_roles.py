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
    for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation'):
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
            for key in ('acq-mech-3-return-paths','acq-elec-2-source-evaluation'):
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
        for change in ('unqualified','missing-role','load','drive','bus'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);doc,captures,*_=authored(self,root)
                for p,b in captures.items():
                    target=root/'context'/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
                if change=='missing-role':doc['roles'].pop(next(iter(doc['roles'])))
                elif change=='load':doc['application']['j2_external_load_a']=.001
                elif change=='drive':doc['application']['j2_external_drive']=True
                elif change=='bus':doc['application']['bus_capacitance_pf']=150
                with self.assertRaises(ValueError):roles.read(root/'context',storage.json_bytes(doc),100)

    def test_public_role_options_require_complete_pins_before_source_admission(self):
        from agent_ee.acquisition_prepare import prepare
        for args in (dict(role_context='missing'),dict(role_source='missing'),dict(role_context_sha256='a'*64)):
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,'source root and exact context'):
                prepare(None,None,None,added_sources={},**args)
