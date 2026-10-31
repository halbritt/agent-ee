"""Source-only selector controls; never native or manufacturer qualification."""
import copy
from hashlib import sha256
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_prepare, acquisition_resistor as resistor
from agent_ee import acquisition_selected_cpl, parts_manifest, requirement_map, storage
from tests.test_acquisition_alternative import scoped_guard

CAPTURE = Path(os.environ.get('AGENT_EE_RESISTOR_CAPTURE', '/nonexistent/resistor-capture'))
SOURCE = Path(os.environ.get('AGENT_EE_RESISTOR_SOURCE', '/nonexistent/resistor-source'))


def public(exclude=None):
    return storage.json_bytes(dict(schema='agent-ee.case-context/1', catalog_policy=None if exclude is None else
        dict(kind='evaluation_exclusion', label=resistor.LABEL, exclude=exclude)))


def catalog():
    return dict(parts={k:dict(mpn=v) for k,v in {**resistor.MPNS, 'C468683':'ADS1115IDGST'}.items()})


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())

    def test_absent_and_null_policy_keep_identical_primary_choice(self):
        raw = public()
        self.assertIsNone(resistor.policy(raw, [catalog()]))
        self.assertEqual(resistor.choose(None), 'C22548')
        self.assertEqual(resistor.choose(resistor.policy(raw, [catalog()])), resistor.choose(None))

    def test_both_instances_switch_only_after_exact_labeled_primary_exclusion(self):
        original = catalog()
        before = storage.json_bytes(original)
        policy = resistor.policy(public([dict(lcsc='C22548',mpn='RC0603FR-071KL')]), [original])
        self.assertEqual(resistor.choose(policy), 'C118155')
        self.assertEqual(storage.json_bytes(original), before)
        selected = dict(parts=[dict(id=ref,lcsc='C118155',mpn='ERJ3EKF1001V') for ref in ('R1','R2')])
        resistor.enforce_selected(policy, selected)
        selected['parts'][1].update(lcsc='C22548',mpn='RC0603FR-071KL')
        with self.assertRaisesRegex(ValueError,'excluded selected part: R2'):
            resistor.enforce_selected(policy, selected)

    def test_unknown_mismatched_duplicate_empty_or_ambiguous_policy_refuses(self):
        base = json.loads(public([dict(lcsc='C22548',mpn='RC0603FR-071KL')]))
        changes = [lambda d:d.update(unknown=True), lambda d:d.update(schema='wrong'),
            lambda d:d['catalog_policy'].update(unknown=True), lambda d:d['catalog_policy'].update(kind='stockout'),
            lambda d:d['catalog_policy'].update(label='supplier stockout'), lambda d:d['catalog_policy'].update(exclude=[]),
            lambda d:d['catalog_policy']['exclude'][0].update(lcsc='C999999'),
            lambda d:d['catalog_policy']['exclude'][0].update(mpn='WRONG'),
            lambda d:d['catalog_policy']['exclude'][0].update(extra=True),
            lambda d:d['catalog_policy']['exclude'].append(copy.deepcopy(d['catalog_policy']['exclude'][0]))]
        for change in changes:
            d=copy.deepcopy(base);change(d)
            with self.subTest(d=d), self.assertRaises(ValueError):
                resistor.policy(storage.json_bytes(d), [catalog()])
        for value in ([], '', 1, False):
            with self.subTest(value=value),self.assertRaises(ValueError):
                resistor.policy(storage.json_bytes(dict(schema='agent-ee.case-context/1',catalog_policy=value)),[catalog()])

    def test_exclusion_cannot_select_unqualified_or_exhausted_role(self):
        p=resistor.policy(public([dict(lcsc=k,mpn=v) for k,v in resistor.MPNS.items()]),[catalog()])
        with self.assertRaisesRegex(ValueError,'no qualified filter'):
            resistor.choose(p)
        p=resistor.policy(public([dict(lcsc='C468683',mpn='ADS1115IDGST')]),[catalog()])
        with self.assertRaisesRegex(ValueError,'no admitted qualified alternative'):
            resistor.enforce_selected(p,dict(parts=[dict(id='U2',lcsc='C468683',mpn='ADS1115IDGST')]))

    def test_conflicting_common_catalog_identity_refuses(self):
        other=catalog();other['parts']['C22548']['mpn']='WRONG'
        with self.assertRaisesRegex(ValueError,'contradictory'):
            resistor.policy(public([dict(lcsc='C22548',mpn='RC0603FR-071KL')]),[catalog(),other])

    def test_incomplete_public_or_resistor_api_pins_refuse_before_source_reads(self):
        for args in (dict(public_context='missing'),dict(public_context_sha256='a'*64),
                     dict(resistor_context='missing'),dict(resistor_source='missing'),dict(resistor_context_sha256='a'*64)):
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,'context requires'):
                acquisition_prepare.prepare(None,None,None,added_sources={},**args)

    def test_model_changes_only_two_bodies_preserving_every_limit(self):
        old=dict(packages={ref:dict(basis='YAGEO RC0603 maximum;0.1W;1percent',offset_mm=[0,0],size_mm=[1.7,.9,.55])
                           for ref in ('R1','R2','R3')},limits=['unchanged'],dc_estimate={'unchanged':True})
        revised=json.loads(resistor.model_variant(storage.json_bytes(old)))
        for ref in ('R1','R2'):
            self.assertEqual(revised['packages'][ref],resistor.panasonic_body())
            revised['packages'][ref]=old['packages'][ref]
        self.assertEqual(revised,old)
        old['packages']['R2']['size_mm'][0]=1.6
        with self.assertRaisesRegex(ValueError,'body model differs'):
            resistor.model_variant(storage.json_bytes(old))

    def test_context_requires_pins_and_real_source_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            d=dict(schema=resistor.SCHEMA,reference='acquisition-control',files={},role_contexts={})
            with self.assertRaisesRegex(ValueError,'finite'):
                resistor.read(root,storage.json_bytes(d),100)
            d['files']={'unknown':'a'*64}
            with self.assertRaisesRegex(ValueError,'exact captured'):
                resistor.read(root,storage.json_bytes(d),100)
            alias=root/'alias';alias.symlink_to(root,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'alias'):
                resistor.read(alias,storage.json_bytes(d),100)


@unittest.skipUnless(CAPTURE.is_dir(), 'explicit retained Panasonic capture required')
class CapturedSourceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())
        self.captures={p:(CAPTURE/p).read_bytes() for p in resistor.EVIDENCE}

    def test_exact_pinned_supplier_span_and_part_identity_replay(self):
        for p,h in resistor.EVIDENCE.items():
            self.assertEqual(sha256(self.captures[p]).hexdigest(),h)
        files=resistor.supplier_files(self.captures)
        self.assertEqual(files['supplier/C118155.raweasy.json'],self.captures['request-05.body'][34:21264])
        provenance=json.loads(files['supplier/C118155.provenance.json'])
        self.assertEqual(provenance['path'],'cpl/supplier/C118155.raweasy.json')
        self.assertEqual(provenance['retrieved_at'],'2026-10-29T21:11:02.013999+00:00')
        self.assertEqual(resistor.part_record.qualify_record(json.loads(self.captures['named/C118155.json']),CAPTURE)['catalog/catalog.json'],resistor.EVIDENCE['catalog/catalog.json'])

    def test_supplier_response_span_date_or_alias_drift_refuses(self):
        for mutation in ('body','date','path'):
            captures=dict(self.captures)
            if mutation=='body':captures['request-05.body']=b'x'+captures['request-05.body'][1:]
            else:
                p=json.loads(captures['supplier/C118155.provenance.json'])
                p['retrieved_at' if mutation=='date' else 'path']='2026-10-29T00:00:00+00:00' if mutation=='date' else 'supplier/C22548.raweasy.json'
                captures['supplier/C118155.provenance.json']=storage.json_bytes(p)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                resistor.supplier_files(captures)

    def test_capture_pin_drift_and_unqualified_role_pool_cannot_be_admitted(self):
        from tests.test_acquisition_roles import authored
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scaffold=root/'scaffold';scaffold.mkdir()
            doc,payload,*_=authored(self,scaffold)
            pool=root/'pool';pool.mkdir()
            for p,b in self.captures.items():
                target=pool/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
            entries={}
            for lcsc,at in (('C22548','base'),('C118155','alternate')):
                for p,b in payload.items():
                    target=pool/at/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
                context=at+'/context.json';storage.write_json(pool/context,doc)
                entries[lcsc]=dict(root=at,context=dict(path=context,sha256=storage.digest(pool/context)))
            outer=dict(schema=resistor.SCHEMA,reference='acquisition-control',
                files=requirement_map.input_inventory(pool),role_contexts=entries)
            # The actual source reader sees runs:[]; no forged verified report.
            with self.assertRaisesRegex(ValueError,'qualified role requires its exact declared source runs'):
                resistor.read(pool,storage.json_bytes(outer),100)
            with self.assertRaisesRegex(ValueError,'qualified role requires its exact declared source runs'):
                resistor.apply(root/'never-created',self.captures,None,role_source=pool/'base',
                    role_context=storage.json_bytes(doc),capacitance=100,as_of='2026-10-30T17:20:00+00:00',policy_blob={})
            self.assertFalse((root/'never-created').exists())
            (pool/'request-05.body').write_bytes(b'drift')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                resistor.read(pool,storage.json_bytes(outer),100)

    def test_projected_cpl_copies_both_choices_without_overwriting_original(self):
        original={'supplier/original.json':'a'*64}
        projected=acquisition_selected_cpl.projected_inventory(original,{}, {},resistor_captures=self.captures)
        self.assertEqual(set(projected)-set(original),{'supplier/C118155.raweasy.json','supplier/C118155.provenance.json'})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'old.json').write_bytes(b'old\n')
            original=requirement_map.input_inventory(source)
            projected=acquisition_selected_cpl.projected_inventory(original,{}, {},resistor_captures=self.captures)
            acquisition_selected_cpl.materialize(source,root/'out',{}, {},projected,resistor_captures=self.captures)
            self.assertEqual(requirement_map.input_inventory(source),original)
            self.assertEqual(requirement_map.input_inventory(root/'out'),projected)
        with self.assertRaisesRegex(ValueError,'overwrite'):
            acquisition_selected_cpl.projected_inventory(projected,{}, {},resistor_captures=self.captures)


@unittest.skipUnless(CAPTURE.is_dir() and SOURCE.is_dir(), 'explicit retained acquisition sources required')
class ProducerSourceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))/'revision'
        shutil.copytree(SOURCE,self.root)
        mapping=json.loads((self.root/'requirement-map.json').read_bytes())
        stock_path=resistor.acquisition_alternative._assembly_check(mapping)['params']['catalog']
        self.source_stock=json.loads((self.root/stock_path).read_bytes())
        self.captures={p:(CAPTURE/p).read_bytes() for p in resistor.EVIDENCE}
        self.as_of=max(self.source_stock['completed_at'],
                       json.loads(self.captures['catalog/catalog.json'])['completed_at'],
                       key=datetime.fromisoformat)
        for p,b in self.captures.items():
            target=self.root/resistor.AT/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
        self.value=resistor.policy(public([dict(lcsc='C22548',mpn='RC0603FR-071KL')]),[catalog()])
        self.policy_blob=dict(path='preparation/public-context.json',sha256=sha256(public(self.value['exclude'])).hexdigest())
        (self.root/self.policy_blob['path']).write_bytes(public(self.value['exclude']))
        # Isolated source transformation input, not a fabricated qualified report.
        # Production admission is independently guarded by read()/role verifier.
        self.unit_record={'id':'AUTHORED-UNIT-ONLY-NOT-QUALIFICATION'}

    def test_both_selected_manifest_records_stock_custody_and_body_are_rebuilt(self):
        before=parts_manifest.load_manifest(self.root,'parts.json')
        circuit=(self.root/'circuit.json').read_bytes()
        raw={p:storage.digest(self.root/p) for p in requirement_map.input_inventory(self.root) if '/responses/' in p}
        result=resistor._revise(self.root,self.captures,self.value,self.unit_record,self.as_of,self.policy_blob)
        after=parts_manifest.load_manifest(self.root,'parts.json');parts_manifest.qualify_manifest(after,self.root)
        for a,b in zip(before['parts'],after['parts']):
            for key in ('id','role','instances','lib_id','footprint','required'):
                self.assertEqual(a[key],b[key])
            if b['id'] in ('R1','R2'):
                self.assertEqual((b['lcsc'],b['mpn'],b['manufacturer']),('C118155','ERJ3EKF1001V','PANASONIC'))
                self.assertIn(resistor.AT,b['datasheet']['artifact']['path'])
            else:self.assertEqual((a['lcsc'],a['mpn'],a['manufacturer']),(b['lcsc'],b['mpn'],b['manufacturer']))
        self.assertEqual((self.root/'circuit.json').read_bytes(),circuit)
        self.assertTrue(all(storage.digest(self.root/p)==h for p,h in raw.items()))
        model=json.loads((self.root/'analysis/geometry-model.json').read_bytes())
        self.assertEqual(model['packages']['R1'],resistor.panasonic_body())
        self.assertEqual(model['packages']['R2'],resistor.panasonic_body())
        decision=json.loads((self.root/result['decision']['path']).read_bytes())
        self.assertEqual({r['instance']['reference'] for r in decision['affected']},{'R1','R2'})
        self.assertEqual(decision['catalog_policy']['label'],resistor.LABEL)
        stock=json.loads((self.root/result['catalog']['path']).read_bytes())
        captured_stock=json.loads(self.captures['catalog/catalog.json'])
        self.assertEqual(stock['parts']['C118155']['last_seen'],
                         captured_stock['parts']['C118155']['last_seen'])
        self.assertEqual(stock['as_of'],min((self.source_stock['as_of'],captured_stock['as_of']),
                                           key=datetime.fromisoformat))
        self.assertEqual(stock['completed_at'],max((self.source_stock['completed_at'],captured_stock['completed_at']),
                                                  key=datetime.fromisoformat))

    def test_selected_cpl_has_full_union_and_labeled_roster_delta(self):
        result=resistor._revise(self.root,self.captures,self.value,self.unit_record,self.as_of,self.policy_blob)
        cpl=self.root/'cpl'
        for name,raw in resistor.supplier_files(self.captures).items():
            target=cpl/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        original=(self.root/'history/acquisition-preparation-original/assembly-release-ids.json').read_bytes()
        (self.root/'assembly-release-ids.json').write_bytes(original)
        project=Path(__file__).resolve().parents[1]
        overlay=json.loads((project/'fixtures/acquisition-typed/overlay.json').read_bytes())
        resistor.bind_judgments(overlay,result)
        self.assertTrue(any(resistor.PDF in s for s in overlay['judgments']['judgments/acq-elec-2-source-evaluation.json']['sources']))
        report=acquisition_selected_cpl.finalize(self.root,overlay,cpl,resistor_selection=result,
            public_policy={**self.policy_blob,**self.value})
        selected={p['lcsc'] for p in parts_manifest.load_manifest(self.root,'parts.json')['parts']}
        self.assertEqual(set(report['supplier_footprints']),selected)
        self.assertEqual(set(json.loads((self.root/'assembly-release-ids.json').read_bytes())),selected)
        self.assertIn('C118155',report['roster']['added'])
        self.assertIn('C22548',report['roster']['removed'])
        self.assertEqual(report['catalog_policy']['label'],resistor.LABEL)
        self.assertIn(resistor.LABEL,overlay['judgments']['judgments/acq-mech-4-assembly-applicability.json']['questions'])

    def test_cpl_cannot_accept_panasonic_without_admitted_selection(self):
        resistor._revise(self.root,self.captures,self.value,self.unit_record,self.as_of,self.policy_blob)
        for name,raw in resistor.supplier_files(self.captures).items():
            target=self.root/'cpl'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        project=Path(__file__).resolve().parents[1]
        overlay=json.loads((project/'fixtures/acquisition-typed/overlay.json').read_bytes())
        with self.assertRaisesRegex(ValueError,'admitted resistor decision'):
            acquisition_selected_cpl.finalize(self.root,overlay,self.root/'cpl')

    def test_source_generation_has_no_circular_qualification_or_policy_dependency(self):
        destination=self.root.parent/'generated-source'
        pin=storage.identity(requirement_map.input_inventory(SOURCE))
        report=resistor.derive_source(SOURCE,destination,source_inventory_sha256=pin,capture_source=CAPTURE)
        self.assertEqual(report['qualification'],'unqualified')
        self.assertEqual(report['native_invocations'],0)
        generated=(destination/'project/acquisition.kicad_sch').read_bytes()
        self.assertIn(b'ERJ3EKF1001V',generated)
        self.assertIn(b'C118155',generated)
        for name in report['retained_native_seed']:
            self.assertEqual((destination/name).read_bytes(),(SOURCE/name).read_bytes())
        self.assertEqual(storage.identity(requirement_map.input_inventory(SOURCE)),pin)
        decision=json.loads((destination/report['revision']['decision']['path']).read_bytes())
        self.assertTrue(decision['source_derivation'])
        self.assertIsNone(decision['qualification_record'])
        self.assertIsNone(decision['catalog_policy'])
        self.assertIsNone(decision['policy'])
        self.assertEqual(decision['affected'],[])
        for name,raw in resistor.supplier_files(self.captures).items():
            target=destination/'cpl'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        project=Path(__file__).resolve().parents[1]
        overlay=json.loads((project/'fixtures/acquisition-typed/overlay.json').read_bytes())
        with self.assertRaisesRegex(ValueError,'unqualified resistor source derivation'):
            acquisition_selected_cpl.finalize(destination,overlay,destination/'cpl',resistor_selection=report['revision'])
        bad=self.root.parent/'bad-seed-pin'
        with self.assertRaisesRegex(ValueError,'seed inventory differs'):
            resistor.derive_source(SOURCE,bad,source_inventory_sha256='0'*64,capture_source=CAPTURE)
        self.assertFalse(bad.exists())

    def clean_seed(self, name):
        seed=self.root.parent/name
        shutil.copytree(SOURCE,seed)
        for name in ('ENGINEERING-REVIEW.md','history/acquisition-preparation-original/ENGINEERING-REVIEW.md'):
            path=seed/name
            self.assertTrue(path.is_file())
            path.unlink()
        return seed

    def test_clean_source_derivation_neither_needs_nor_recreates_review(self):
        seed=self.clean_seed('clean-source-seed');before=requirement_map.input_inventory(seed)
        destination=self.root.parent/'clean-source-output'
        report=resistor.derive_source(seed,destination,source_inventory_sha256=storage.identity(before),capture_source=CAPTURE)
        self.assertEqual(report['qualification'],'unqualified')
        self.assertEqual(requirement_map.input_inventory(seed),before)
        inventory=requirement_map.input_inventory(destination)
        self.assertNotIn('ENGINEERING-REVIEW.md',inventory)
        self.assertNotIn('history/acquisition-preparation-original/ENGINEERING-REVIEW.md',inventory)
        self.assertTrue((destination/'OPERATING.md').is_file())
        self.assertIn(b'ERJ3EKF1001V',(destination/'project/acquisition.kicad_sch').read_bytes())

    def test_clean_source_missing_operating_refuses_and_leaves_no_output(self):
        seed=self.clean_seed('missing-operating-seed');(seed/'OPERATING.md').unlink()
        before=requirement_map.input_inventory(seed);destination=self.root.parent/'missing-operating-output'
        with self.assertRaises(FileNotFoundError):
            resistor.derive_source(seed,destination,source_inventory_sha256=storage.identity(before),capture_source=CAPTURE)
        self.assertFalse(destination.exists())
        self.assertEqual(requirement_map.input_inventory(seed),before)

    def test_target_mode_still_requires_its_review_input(self):
        (self.root/'ENGINEERING-REVIEW.md').unlink()
        with self.assertRaises(FileNotFoundError):
            resistor._revise(self.root,self.captures,self.value,self.unit_record,self.as_of,self.policy_blob)

    def test_mechanics_accepts_only_exact_body_derivative_and_all_source_custody(self):
        from agent_ee import acquisition_mechanics
        result=resistor._revise(self.root,self.captures,self.value,self.unit_record,self.as_of,self.policy_blob)
        declaration=acquisition_mechanics.declaration(self.root)
        path=self.root/'placement-mechanics.json';storage.write_json(path,declaration)
        loaded=acquisition_mechanics.load(path)
        self.assertEqual(loaded['model']['packages']['R1']['size_mm'],[1.75,.95,.55])
        self.assertIn(resistor.AT+'/'+resistor.PDF,declaration['sources'])
        self.assertEqual(declaration['sources'][resistor.AT+'/'+resistor.PDF],resistor.EVIDENCE[resistor.PDF])
        baseline=copy.deepcopy(declaration)
        declaration['sources']['unknown-source']='a'*64;storage.write_json(path,declaration)
        with self.assertRaisesRegex(ValueError,'source custody'):
            acquisition_mechanics.load(path)
        storage.write_json(path,baseline)
        guide=self.root/resistor.AT/'request-11.body';guide.write_bytes(b'drift')
        with self.assertRaisesRegex(ValueError,'source changed'):
            acquisition_mechanics.load(path)
        guide.write_bytes(self.captures['request-11.body'])
        model=self.root/'analysis/geometry-model.json';data=json.loads(model.read_bytes())
        data['packages']['R3']['size_mm'][0]=1.8;storage.write_json(model,data)
        declaration=acquisition_mechanics.declaration(self.root);storage.write_json(path,declaration)
        with self.assertRaisesRegex(ValueError,'unsupported acquisition model'):
            acquisition_mechanics.load(path)

    def test_stale_capture_and_wrong_original_allocation_refuse(self):
        with self.assertRaisesRegex(ValueError,'stale'):
            later=(datetime.fromisoformat(self.as_of)+timedelta(hours=25)).isoformat()
            resistor._revise(self.root,self.captures,self.value,self.unit_record,later,self.policy_blob)
        self.assertEqual({p['lcsc'] for p in parts_manifest.load_manifest(self.root,'parts.json')['parts'] if p['id'] in ('R1','R2')},{'C22548'})

@unittest.skipUnless(SOURCE.is_dir(), 'explicit retained acquisition sources required')
class DefaultPreparationTests(unittest.TestCase):
    """Reconstruct only exact pinned historical bytes; no synthetic production data."""
    def setUp(self):
        self.enterContext(scoped_guard())
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.recipe=self.root/'recipe';self.recipe.mkdir()
        self.base=self.recipe/'inputs';self.base.mkdir()
        project=Path(__file__).resolve().parents[1]
        pins=json.loads((project/'fixtures/recovery/inputs.json').read_bytes())['files']
        original=Path(os.environ.get('AGENT_EE_ACQUISITION_ORIGINAL','/home/halbritt/git/agent-ee/build/acquisition-release'))
        for n,pin in pins.items():
            prefix='build/acquisition-release/inputs/'
            if not n.startswith(prefix):continue
            rel=n.removeprefix(prefix)
            candidates=[original/'inputs'/rel,SOURCE/'history/acquisition-preparation-original'/rel,SOURCE/rel]
            selected=next((p for p in candidates if p.is_file() and storage.digest(p)==pin['sha256']),None)
            if selected is None:raise AssertionError('exact retained original source unavailable: '+rel)
            target=self.base/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(selected.read_bytes())
        self.plan=self.recipe/'build.json';self.plan.write_bytes((original/'build.json').read_bytes())
        self.assertEqual(storage.digest(self.plan),pins['build/acquisition-release/build.json']['sha256'])
        self.sources={}
        overlay=json.loads((project/'fixtures/acquisition-typed/overlay.json').read_bytes())
        for name in ('cpl','manufacturer-text'):
            target=self.recipe/name;target.mkdir();self.sources[name]=target
            for rel,h in overlay['added_inputs'][name]['files'].items():
                raw=(SOURCE/name/rel).read_bytes();self.assertEqual(sha256(raw).hexdigest(),h)
                path=target/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        self.brief=self.root/'brief.md';self.brief.write_bytes((acquisition_prepare.ADOPTION+'\nTotal I2C line capacitance shall be at most 100 pF per line, including board, host and cable.\n').encode())

    def prepare(self,name,policy_raw=None,**args):
        if policy_raw is not None:
            path=self.root/(name+'.json');path.write_bytes(policy_raw)
            args.update(public_context=path,public_context_sha256=storage.digest(path))
        return acquisition_prepare.prepare(self.brief,self.plan,self.root/name,added_sources=self.sources,**args)

    def test_preparation_keeps_selection_within_the_admitted_role_carrier(self):
        from agent_ee import acquisition_alternative_context, acquisition_roles, jlc_stock_capture
        # Retained identity inputs with explicitly authored restocked observations.
        # Qualification readers are the test seam; no native/source acceptance or
        # prepared bundle is produced. The real selector emits the observed parts.
        original_context = SOURCE/'preparation/alternative-context.json'
        context, captured = acquisition_alternative_context.read(
            SOURCE/'preparation/alternative-context', original_context.read_bytes())
        alternative = self.root/'restocked'; alternative.mkdir()
        for name, raw in captured.items():
            target=alternative/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        parents=[]
        for ref in context['parents']:
            path=alternative/ref['path'];parent=json.loads(path.read_bytes())
            for request in parent['requests']:
                if request['lcsc'] not in ('C37593','C468683'):continue
                response=alternative/Path(context['catalog']['path']).parent/request['response']['path']
                raw=json.loads(response.read_bytes())
                row=next(r for r in raw['data']['componentPageInfo']['list'] if r['componentCode']==request['lcsc'])
                row['stockCount']=100000;response.write_bytes(storage.json_bytes(raw))
                request['response'].update(sha256=storage.digest(response),size_bytes=response.stat().st_size)
                _,parsed=jlc_stock_capture.parse_search_response(response.read_bytes(),request['lcsc'])
                parent['parts'][request['lcsc']]={**parsed,'last_seen':request['fetched_at']}
            path.write_bytes(storage.json_bytes(parent));ref['sha256']=storage.digest(path);parents.append(parent)
        union={**parents[0], 'requests':parents[0]['requests']+parents[1]['requests'],
               'parts':{**parents[0]['parts'],**parents[1]['parts']},
               'completed_at':max(p['completed_at'] for p in parents)}
        (alternative/context['catalog']['path']).write_bytes(storage.json_bytes(union))
        derivation=json.loads((alternative/context['derivation']['path']).read_bytes())
        for row,ref in zip(derivation['parents'],context['parents']):row['sha256']=ref['sha256']
        (alternative/context['derivation']['path']).write_bytes(storage.json_bytes(derivation))
        for key in ('catalog','derivation'):context[key]['sha256']=storage.digest(alternative/context[key]['path'])
        context['files']={name:storage.digest(alternative/name) for name in captured}
        context_path=alternative/'context.json';context_path.write_bytes(storage.json_bytes(context))
        acquisition_alternative_context.read(alternative,context_path.read_bytes())
        anchor=datetime.fromisoformat(union['completed_at'])+timedelta(hours=1)
        pool=self.root/'role-pool';pool.mkdir();cohorts={};entries={}
        for choice,lcsc in ((resistor.PRIMARY,'C468683'),(resistor.ALTERNATIVE,'C37593')):
            root=pool/choice;root.mkdir()
            captures={'analog/block.json':storage.json_bytes(dict(parts_manifest='parts.json')),
                      'analog/parts.json':storage.json_bytes(dict(parts=[dict(id='U2',lcsc=lcsc)]))}
            doc=dict(roles={'acq-ads1115-two-channel':dict(block='analog')},application={},
                     files={name:sha256(raw).hexdigest() for name,raw in captures.items()})
            path=root/'context.json';path.write_bytes(storage.json_bytes(doc))
            cohorts[choice]=(doc,captures,{})
            entries[choice]=dict(root=choice,context=dict(path=choice+'/context.json',sha256=storage.digest(path)))
        evidence={name:(CAPTURE/name).read_bytes() for name in resistor.EVIDENCE}
        self.assertEqual({name:sha256(raw).hexdigest() for name,raw in evidence.items()},resistor.EVIDENCE)
        pool_doc=dict(files=copy.deepcopy(resistor.EVIDENCE),role_contexts=entries)
        pool_context=pool/'context.json';pool_context.write_bytes(storage.json_bytes(pool_doc))
        resistor_args=dict(resistor_source=pool,resistor_context=pool_context,
                           resistor_context_sha256=storage.digest(pool_context))
        def direct(choice):
            path=pool/choice/'context.json'
            return dict(role_source=path.parent,role_context=path,role_context_sha256=storage.digest(path))
        cases=[('unrestricted',{},None,'C37593'),
               ('direct-alternate',direct(resistor.PRIMARY),None,'C468683'),
               ('direct-primary',direct(resistor.ALTERNATIVE),None,'C37593'),
               ('pool-default',resistor_args,None,'C468683'),
               ('pool-selected',resistor_args,public([dict(lcsc=resistor.PRIMARY,mpn=resistor.MPNS[resistor.PRIMARY])]),'C37593')]
        real_apply=acquisition_alternative_context.apply
        class SelectionObserved(Exception):pass
        before=requirement_map.input_inventory(self.base)
        for name,args,policy,expected in cases:
            observed={}
            def select(revised,*positional,**keywords):
                result=real_apply(revised,*positional,**keywords)
                observed['selected']=result['selected']
                observed['part']=next(p['lcsc'] for p in parts_manifest.load_manifest(revised,'parts.json')['parts'] if p['id']=='U2')
                raise SelectionObserved()
            with self.subTest(path=name),patch('agent_ee.acquisition_roles.read',
                    side_effect=lambda root,*unused:cohorts[Path(root).name]), \
                    patch('agent_ee.acquisition_resistor.read',return_value=(pool_doc,evidence,cohorts)), \
                    patch('agent_ee.acquisition_alternative_context.apply',side_effect=select), \
                    patch('agent_ee.acquisition_prepare.datetime') as clock:
                clock.now.return_value=anchor
                with self.assertRaises(SelectionObserved):
                    self.prepare(name,policy,alternative_source=alternative,alternative_context=context_path,
                                 alternative_context_sha256=storage.digest(context_path),**args)
                self.assertEqual(observed,dict(selected=expected,part=expected))
                self.assertFalse((self.root/name).exists())
        self.assertEqual(requirement_map.input_inventory(self.base),before)

    def test_absent_null_policy_keep_identical_selected_parts_and_raw_stock(self):
        before=requirement_map.input_inventory(self.base)
        a=self.prepare('absent');b=self.prepare('null',public())
        for rel in ('parts.json','circuit.json','component-selections.json','assembly-release-ids.json'):
            self.assertEqual((self.root/'absent/inputs'/rel).read_bytes(),(self.root/'null/inputs'/rel).read_bytes())
        self.assertEqual(requirement_map.input_inventory(self.base),before)
        for rel,h in before.items():
            if '/responses/' in rel or rel.endswith('/catalog.json'):
                self.assertEqual(storage.digest(self.root/'absent/inputs'/rel),h)
                self.assertEqual(storage.digest(self.root/'null/inputs'/rel),h)
        self.assertIsNone(b['public_context']['catalog_policy'])

    def test_absent_null_with_actual_adc_and_r6_context_keep_identical_selection_bytes(self):
        context=SOURCE/'preparation/alternative-context.json'
        options=dict(alternative_context=context,alternative_context_sha256=storage.digest(context),
                     alternative_source=SOURCE/'preparation/alternative-context')
        adc=json.loads((SOURCE/'preparation/alternative-context'/
                        json.loads(context.read_bytes())['catalog']['path']).read_bytes())
        power=json.loads((SOURCE/'power-repair/catalog/catalog.json').read_bytes())
        # Compare the retained preparations once both actual captures completed.
        anchor=max(datetime.fromisoformat(c['completed_at']) for c in (adc,power))
        with patch('agent_ee.acquisition_prepare.datetime') as clock:
            clock.now.return_value=anchor
            a=self.prepare('adc-absent',**options)
            b=self.prepare('adc-null',public(),**options)
        for rel in ('parts.json','circuit.json','component-selections.json','assembly-release-ids.json'):
            self.assertEqual((self.root/'adc-absent/inputs'/rel).read_bytes(),(self.root/'adc-null/inputs'/rel).read_bytes())
        # The refreshed retained catalog has stock of the unrestricted primary.
        # Carrier restrictions have separate coverage above.
        self.assertEqual(a['alternative']['selected'],'C37593')
        self.assertEqual(b['alternative']['selected'],'C37593')
        sources={**self.sources,'source-supplement':SOURCE/'source-supplement','power-repair':SOURCE/'power-repair'}
        # The source helper uses this exact existing repair source, without native execution.
        old_sources=self.sources;self.sources=sources
        try:
            with patch('agent_ee.acquisition_prepare.datetime') as clock:
                clock.now.return_value=anchor
                self.prepare('repair-absent',**options)
                self.prepare('repair-null',public(),**options)
        finally:self.sources=old_sources
        for rel in ('parts.json','circuit.json','component-selections.json','assembly-release-ids.json'):
            self.assertEqual((self.root/'repair-absent/inputs'/rel).read_bytes(),(self.root/'repair-null/inputs'/rel).read_bytes())
        selected=parts_manifest.load_manifest(self.root/'repair-null/inputs','parts.json')
        self.assertEqual(next(p['lcsc'] for p in selected['parts'] if p['id']=='R6'),'C114669')

    def test_unqualified_exclusion_and_bad_hash_publish_nothing(self):
        raw=public([dict(lcsc='C22548',mpn='RC0603FR-071KL')])
        with self.assertRaisesRegex(ValueError,'no admitted qualified alternative'):
            self.prepare('excluded',raw)
        self.assertFalse((self.root/'excluded').exists())
        path=self.root/'policy.json';path.write_bytes(public())
        with self.assertRaisesRegex(ValueError,'public context hash mismatch'):
            self.prepare('bad-hash',public_context=path,public_context_sha256='0'*64)
        self.assertFalse((self.root/'bad-hash').exists())


if __name__=='__main__':
    unittest.main()
