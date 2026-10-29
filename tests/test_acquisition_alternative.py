"""Portable authored source contracts, never manufacturer/native qualification."""
import contextlib
import hashlib
import copy
from dataclasses import replace
import importlib.abc
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_alternative as alternative
from agent_ee import jlc_stock_capture, part_record, parts_manifest, requirement_map, storage
from tests.test_jlc_stock_capture import row, search
from tests.test_native_binding import GEN


class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pcbnew', '_pcbnew', 'kicad', 'kipy', 'eeschema'}:
            raise AssertionError('native import forbidden: ' + fullname)


@contextlib.contextmanager
def scoped_guard():
    guard = NoNative()
    sys.meta_path.insert(0, guard)
    missing = object()
    native_names = ('pcbnew', '_pcbnew', 'kicad', 'kipy', 'eeschema')
    previous = {name: sys.modules.get(name, missing) for name in native_names}
    for name in native_names:
        sys.modules[name] = None
    try:
        with contextlib.ExitStack() as stack:
            for name in ('subprocess.Popen', 'subprocess.run', 'os.system',
                         'socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect'):
                stack.enter_context(patch(name, side_effect=AssertionError('external operation forbidden: ' + name)))
            yield
    finally:
        sys.meta_path.remove(guard)
        for name, module in previous.items():
            if module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def authored_fixture(root):
    """Original ten-pin CAD and supplier/PDF *substitutions*, no vendor data."""
    def put(path, raw):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw if isinstance(raw, bytes) else storage.json_bytes(raw))
        return dict(path=path, sha256=storage.digest(target))
    name = 'TSSOP-10_3x3mm_P0.5mm'
    symbol = GEN._definition('ADS1115IDGS', [(1, [(str(i), i * 2.54) for i in range(1, 11)])])
    library = put('Analog_ADC.kicad_sym', ('(kicad_symbol_lib (version 20241209) (generator "authored") ' + symbol + ')').encode())
    fp = put('Package_SO.pretty/' + name + '.kicad_mod', GEN.footprint(name, [GEN.smd(str(i), i) for i in range(1, 11)]))
    cad = put('cad.json', dict(schema='agent-ee.footprint-libraries/1', complete=True,
        libraries=[dict(name='Package_SO', members=[fp])]))
    declarations = [dict(reference=ref, lib_id='Analog_ADC:ADS1115IDGS', footprint='Package_SO:' + name,
                         assembly='smd', connector=None) for ref in ('U2', 'J1', 'J2')]
    assignment = put('assignments.json', dict(schema='agent-ee.footprint-assignments-input/1',
        libraries=[dict(name='Analog_ADC', **library)], components=declarations))
    source = {**put('requirements.md', b'Authored source fixture only.\n'), 'locator':'line 1'}
    stamp = '2026-10-18T00:00:00+00:00'
    parts = []
    catalogs = []
    for lcsc, mpn, stock in [('C37593', 'ADS1115IDGSR', 50), ('C131334', 'AUTHORED-CONNECTOR', 50),
                             ('C468683', 'ADS1115IDGST', 70)]:
        raw = search(row(lcsc, stock, library='expand', componentModelEn=mpn,
            componentBrandEn='Texas Instruments', componentSpecificationEn='VSSOP-10-0.5mm', firstSortName='Authored'))
        prefix = 'original/' + lcsc
        blob = put(prefix + '/responses/' + lcsc + '.json', raw)
        _, parsed = jlc_stock_capture.parse_search_response(raw, lcsc)
        catalog = dict(schema=jlc_stock_capture.SCHEMA, as_of=stamp, completed_at=stamp, stale_before=None,
            source=dict(endpoint=jlc_stock_capture.DEFAULT_ENDPOINT, method='POST', parser=jlc_stock_capture.PARSER,
                        request_body=jlc_stock_capture.request_body('{lcsc}').decode(), min_interval_seconds=5),
            requests=[dict(lcsc=lcsc, fetched_at=stamp, status='found', http_status=200, reason=None,
                response=dict(path='responses/' + lcsc + '.json', sha256=blob['sha256'], size_bytes=len(raw)))],
            parts={lcsc:{**parsed, 'last_seen':stamp}})
        catalogs.append(catalog)
        catblob = put(prefix + '/catalog.json', catalog)
        pdf = put(prefix + '/datasheet.pdf', b'%PDF-1.4\nAuthored envelope; not TI data.\n%%EOF\n')
        url = 'https://example.org/authored-' + lcsc + '.pdf'
        sheets = put(prefix + '/manifest.json', dict(schema='agent-ee.datasheet-capture/1', outcome='captured',
            bindings_sha256=storage.identity([dict(id='adc', datasheet_url=url)]),
            parts=[dict(id='adc', url=url, final_url=url, fetched_at=stamp, sha256=pdf['sha256'], path='datasheet.pdf', missing=False, reason=None)]))
        record = part_record.prepare_record(root, lcsc, catblob, sheets, 'adc')
        recblob = put(prefix + '/record.json', record)
        if lcsc == 'C468683':
            continue
        for ref in (('U2',) if lcsc == 'C37593' else ('J1','J2')):
            parts.append(dict(id=ref, role='authored', required=True, instances=[dict(sheet='/',reference=ref)],
                selection_id=ref, lcsc=lcsc, mpn=mpn, manufacturer='Texas Instruments', package=record['package'], stock=stock,
                lib_id='Analog_ADC:ADS1115IDGS', footprint='Package_SO:' + name,
                selection=dict(kind='named',lcsc=lcsc,part_record=recblob['path'],part_record_sha256=recblob['sha256'],decision_ref=source),
                datasheet=dict(status='captured',required=True,artifact=pdf,provenance={**sheets,'locator':'parts[id=adc]'})))
    document = dict(schema='agent-ee.parts/1', declaration_source=source, catalog=None, selection=None,
        cad=dict(manifest=cad,assignments=assignment,members_sha256=storage.identity(sorted([library,fp],key=lambda b:b['path']))),
        property_fields=dict(lcsc='LCSC',mpn='MPN',manufacturer='Manufacturer'),population='native-dnp-optional-only/1',parts=parts)
    put('parts.json',document)
    put('circuit.json',dict(schema='agent-ee.connectivity-intent/1', libraries=[],
        components=[dict(id=p['id'],reference=p['id'],lib_id=p['lib_id'],value=p['mpn'],footprint='',units=[1]) for p in parts],
        nets=[],no_connects=[dict(component='U2',pin={'number':'2'})],current_a=None))
    put('component-selections.json',[dict(reference=p['id'],lcsc=p['lcsc'],value=p['mpn']) for p in parts])
    put('requirement-map.json',dict(requirements=[dict(checks=[dict(kind='assembly_readiness',params=dict(catalog='original/C37593/catalog.json',boards=1,max_age_hours=24))])]))
    put('OPERATING.md', b'Authored unchanged limits.\n')
    union = copy.deepcopy(catalogs[0]); union['requests'] += catalogs[1]['requests'] + catalogs[2]['requests']; union['parts'].update(catalogs[1]['parts']); union['parts'].update(catalogs[2]['parts'])
    for catalog in catalogs:
        lcsc = catalog['requests'][0]['lcsc']
        raw = (root / 'original' / lcsc / 'responses' / (lcsc + '.json')).read_bytes()
        if lcsc == 'C37593':
            doc=json.loads(raw); doc['data']['componentPageInfo']['list'][0]['stockCount']=0; raw=storage.json_bytes(doc)
            req=union['requests'][0]; req['response'].update(sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
            _, parsed=jlc_stock_capture.parse_search_response(raw,lcsc); union['parts'][lcsc]={**parsed,'last_seen':stamp}
        put('preparation/catalog/responses/' + lcsc + '.json',raw)
    put('preparation/catalog/catalog.json',union)
    put('preparation/ti/TI-ADS1115IDGST.html', b'https://www.ti.com/product/ADS1115/part-details/ADS1115IDGST OPN=ADS1115IDGSR These products are exactly the same but come in a different carrier type')
    put('manufacturer-text/ads1115.txt', b'ADS1115IDGSR Active Production VSSOP (DGS) | 10 2500 | LARGE T&R Yes NIPDAU Level-2-260C-1 YEAR -40 to 125 BOGI\nADS1115IDGST Active Production VSSOP (DGS) | 10 250 | SMALL T&R Yes NIPDAU Level-2-260C-1 YEAR -40 to 125 BOGI\n')
    return document


class PortableCaptureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.original_parts = authored_fixture(self.root)
        self.original = json.loads((self.root / 'circuit.json').read_bytes())
        self.context = self.context_for()

    def blob(self, path):
        return dict(path=path, sha256=storage.digest(self.root / path))

    def context_for(self):
        return alternative.CapturedContext(requirement_map.input_inventory(self.root),
            self.blob('preparation/catalog/catalog.json'),
            self.blob('preparation/ti/TI-ADS1115IDGST.html'),
            self.blob('manufacturer-text/ads1115.txt'), '2026-10-18T01:00:00+00:00')

    def synthetic_row(self, lcsc, **changes):
        """Rebuild a *synthetic* internally consistent raw capture boundary case."""
        path = self.root / self.context.catalog['path']
        catalog = json.loads(path.read_bytes())
        req = next(r for r in catalog['requests'] if r['lcsc'] == lcsc)
        rawpath = path.parent / req['response']['path']
        raw = json.loads(rawpath.read_bytes())
        row = next(r for r in raw['data']['componentPageInfo']['list'] if r['componentCode'] == lcsc)
        row.update(changes)
        rawpath.write_bytes(storage.json_bytes(raw))
        req['response']['sha256'] = storage.digest(rawpath)
        req['response']['size_bytes'] = rawpath.stat().st_size
        _, parsed = jlc_stock_capture.parse_search_response(rawpath.read_bytes(), lcsc)
        catalog['parts'][lcsc] = {**parsed, 'last_seen': req['fetched_at']}
        path.write_bytes(storage.json_bytes(catalog))
        self.context = self.context_for()

    def test_authored_capture_chooses_alternative_and_preserves_constraints(self):
        result = alternative.revise(self.root, self.context)
        self.assertEqual(result['selected'], 'C468683')
        manifest = parts_manifest.load_manifest(self.root, 'parts.json')
        adc = next(p for p in manifest['parts'] if p['lcsc'] == 'C468683')
        self.assertEqual(adc['mpn'], 'ADS1115IDGST')
        self.assertEqual(adc['stock'], 70)  # retained observation, no production constant
        self.assertEqual(adc['package'], 'VSSOP-10-0.5mm')
        circuit = json.loads((self.root / 'circuit.json').read_bytes())
        for component in circuit['components']:
            if component['reference'] == 'U2':
                component['value'] = 'ADS1115IDGSR'
        self.assertEqual(circuit, self.original)
        for before, after in zip(self.original_parts['parts'], manifest['parts']):
            for field in ('instances', 'lib_id', 'footprint', 'datasheet', 'role', 'required'):
                self.assertEqual(before[field], after[field])
        self.assertEqual(len(json.loads((self.root / alternative.PREFIX / 'part-refs.json').read_bytes())['components']), 3)
        alternative.audit_selection(self.root, result)
        record = json.loads((self.root / adc['selection']['part_record']).read_bytes())
        self.assertEqual(record['fetched_at'], '2026-10-18T00:00:00+00:00')
        self.assertEqual(result['catalog'], self.context.catalog)

    def test_purchasing_selection_preserves_authored_membership_capability(self):
        from agent_ee.parts_subset import capability
        before = copy.deepcopy(self.original_parts['declaration_source'])
        alternative.revise(self.root, self.context)
        path = self.root / 'parts.json'
        document = json.loads(path.read_bytes())
        self.assertEqual(document['declaration_source'], before)
        params = dict(manifest='parts.json', netlist_check='netlist', requirement_id='authored-membership')
        # An authored CPU scope over the fixture's actual declaration, not a
        # production mapping or an engineering acceptance claim.
        scope = dict(checks=[dict(id='netlist', kind='netlist'),
            dict(id='parts', kind='parts_subset', params=params)], baseline=dict(requirements=[dict(
                id='authored-membership', checks=['parts'],
                source={k: before[k] for k in ('path', 'locator')})]))
        result = capability(params, requirement_map.input_inventory(self.root), self.root, scope, 'parts')
        self.assertIn('parts.json', result['immutable_inputs'])
        document['declaration_source']['locator'] = 'parts'
        path.write_bytes(storage.json_bytes(document))
        with self.assertRaisesRegex(ValueError, 'parts membership must map one requirement'):
            capability(params, requirement_map.input_inventory(self.root), self.root, scope, 'parts')

    def test_synthetic_primary_retained_even_with_more_alternative_stock(self):
        self.synthetic_row('C37593', stockCount=10)
        self.assertEqual(alternative.revise(self.root, self.context)['selected'], 'C37593')

    def test_synthetic_stocked_incompatible_alternative_is_not_selected(self):
        self.synthetic_row('C468683', componentModelEn='ADS1015IDGST')
        with self.assertRaisesRegex(ValueError, 'no eligible.*incompatible_identity_package'):
            alternative.revise(self.root, self.context)

    def test_synthetic_none_available(self):
        self.synthetic_row('C468683', stockCount=0)
        with self.assertRaisesRegex(ValueError, 'no eligible.*low_stock'):
            alternative.revise(self.root, self.context)

    def test_synthetic_package_incompatible(self):
        self.synthetic_row('C468683', componentSpecificationEn='X2QFN-10')
        with self.assertRaisesRegex(ValueError, 'no eligible.*incompatible_identity_package'):
            alternative.revise(self.root, self.context)

    def test_shared_connector_quantity_is_counted_across_board(self):
        self.synthetic_row('C131334', stockCount=1)
        with self.assertRaisesRegex(ValueError, 'full-board stock exhausted: C131334'):
            alternative.revise(self.root, self.context)

    def test_source_quantity_policy_is_not_relaxed(self):
        path = self.root / 'requirement-map.json'
        mapping = json.loads(path.read_bytes())
        alternative._assembly_check(mapping)['params']['boards'] = 4000
        path.write_bytes(storage.json_bytes(mapping))
        with self.assertRaisesRegex(ValueError, 'no eligible.*low_stock'):
            alternative.revise(self.root, self.context_for())

    def test_stale_source_refuses_without_clock_reset(self):
        with self.assertRaisesRegex(ValueError, 'stale'):
            alternative.revise(self.root, replace(self.context, as_of='2026-10-30T08:00:00+00:00'))

    def test_missing_alternative_is_unknown_and_refuses(self):
        catalogpath = self.root / self.context.catalog['path']
        catalog = json.loads(catalogpath.read_bytes())
        catalog['requests'] = [r for r in catalog['requests'] if r['lcsc'] != 'C468683']
        del catalog['parts']['C468683']
        catalogpath.write_bytes(storage.json_bytes(catalog))
        with self.assertRaisesRegex(ValueError, 'lacks an observation'):
            alternative.revise(self.root, self.context_for())

    def test_source_drift_refuses_before_writes(self):
        (self.root / 'OPERATING.md').write_bytes(b'source drift')
        with self.assertRaisesRegex(ValueError, 'source closure drift'):
            alternative.revise(self.root, self.context)
        self.assertFalse((self.root / alternative.PREFIX).exists())

    def test_changed_stock_after_selection_refuses_old_decision(self):
        result = alternative.revise(self.root, self.context)
        self.synthetic_row('C468683', stockCount=0)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            alternative.audit_selection(self.root, result)

    def test_missing_ti_correspondence_cannot_be_caller_qualified(self):
        (self.root / self.context.carrier_page['path']).write_text('qualified:true')
        with self.assertRaisesRegex(ValueError, 'TI same-device'):
            alternative.revise(self.root, self.context_for())


class GuardIsolationTests(unittest.TestCase):
    def test_guard_restores_unrelated_interpreter_state(self):
        before = list(sys.meta_path)
        popen, connection = subprocess.Popen, socket.create_connection
        with scoped_guard():
            self.assertIsNot(subprocess.Popen, popen)
            self.assertIsNot(socket.create_connection, connection)
        self.assertEqual(sys.meta_path, before)
        self.assertIs(subprocess.Popen, popen)
        self.assertIs(socket.create_connection, connection)


class CapturedContextTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        authored_fixture(self.root)
        from agent_ee import acquisition_alternative_context
        self.context_module = acquisition_alternative_context
        catalog_path = 'preparation/catalog/catalog.json'
        catalog = json.loads((self.root/catalog_path).read_bytes())
        def put(path, doc):
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(storage.json_bytes(doc))
            return dict(path=path,sha256=storage.digest(target))
        parents=[]
        for index,ids in enumerate((['C37593','C131334'],['C468683']),1):
            doc={**catalog,'requests':[r for r in catalog['requests'] if r['lcsc'] in ids],
                 'parts':{k:v for k,v in catalog['parts'].items() if k in ids}}
            parents.append(put('parents/'+str(index)+'.json',doc))
        derivation=put('parents/derivation.json',dict(kind='offline union; NOT a fresh capture',parents=[{'sha256':p['sha256']} for p in parents]))
        def blob(path):return dict(path=path,sha256=storage.digest(self.root/path))
        refs=dict(catalog=blob(catalog_path),carrier_page=blob('preparation/ti/TI-ADS1115IDGST.html'),
                  ordering_text=blob('manufacturer-text/ads1115.txt'),derivation=derivation)
        paths=[b['path'] for b in [*refs.values(),*parents]]
        paths += ['preparation/catalog/'+r['response']['path'] for r in catalog['requests']]
        self.context=dict(schema=acquisition_alternative_context.SCHEMA,reference='acquisition-control',
                          files={p:storage.digest(self.root/p) for p in paths},parents=parents,**refs)

    def test_finite_union_replays_exact_observations_and_copies_before_selection(self):
        context,captures=self.context_module.read(self.root,storage.json_bytes(self.context))
        before={p:raw for p,raw in captures.items()}
        report=self.context_module.apply(self.root,context,captures,'2026-10-18T01:00:00+00:00')
        self.assertEqual(report['selected'],'C468683')
        for name,raw in before.items():
            self.assertEqual((self.root/self.context_module.AT/name).read_bytes(),raw)

    def test_unrelated_context_file_and_reset_clock_are_refused(self):
        original=copy.deepcopy(self.context)
        self.context['files']['OPERATING.md']=storage.digest(self.root/'OPERATING.md')
        with self.assertRaisesRegex(ValueError,'unrelated'):
            self.context_module.read(self.root,storage.json_bytes(self.context))
        self.context=original
        path=self.root/self.context['catalog']['path']
        catalog=json.loads(path.read_bytes());catalog['completed_at']='2026-10-18T00:01:00+00:00'
        path.write_bytes(storage.json_bytes(catalog))
        pin=storage.digest(path);self.context['catalog']['sha256']=pin;self.context['files'][self.context['catalog']['path']]=pin
        with self.assertRaisesRegex(ValueError,'exact conservative'):
            self.context_module.read(self.root,storage.json_bytes(self.context))


class SelectedCplClosureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(scoped_guard())
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        authored_fixture(self.root)
        from agent_ee import acquisition_selected_cpl as selected_cpl
        from tests.land_pattern_fixtures import component,pad
        self.module=selected_cpl
        self.cpl=self.root/'cpl-source';self.cpl.mkdir()
        self.original_roster=['C131334','C37593']
        (self.root/'assembly-release-ids.json').write_bytes(storage.json_bytes(self.original_roster))
        def supplier(lcsc,mpn):
            raw=json.loads(component(lcsc,[pad('RECT',3990+i*4,3010,2,4,str(i+1),ident='g'+str(i)) for i in range(5)] +
                [pad('RECT',4006-i*4,2990,2,4,str(i+6),ident='h'+str(i)) for i in range(5)]))
            raw['dataStr']['head']['c_para']['Manufacturer Part']=mpn
            return storage.json_bytes(raw)
        self.context={};self.captures={}
        for lcsc,mpn in [('C37593','ADS1115IDGSR'),('C131334','AUTHORED-CONNECTOR'),('C468683','ADS1115IDGST')]:
            raw=supplier(lcsc,mpn)
            stamp='2026-10-18T00:00:00+00:00';url='https://example.org/authored/'+lcsc
            prov=dict(schema='agent-ee.supplier-footprint/1',format='easyeda-std-component/1',lcsc=lcsc,
                path='cpl/supplier/'+lcsc+'.raweasy.json',sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),
                source_url=url,retrieved_at=stamp,retrieval='Authored CPU substitution, no network observation')
            if lcsc!='C468683':
                folder=self.cpl/'supplier';folder.mkdir(exist_ok=True)
                (folder/(lcsc+'.raweasy.json')).write_bytes(raw)
                (folder/(lcsc+'.provenance.json')).write_bytes(storage.json_bytes(prov))
            else:
                body=b'{"result":'+raw+b'}'
                receipt=dict(outcome='captured_and_parsed',result_span_bytes=[10,10+len(raw)],
                    requests=[dict(sha256=hashlib.sha256(body).hexdigest(),url=url,ended_at=stamp)])
                documents={'provenance':storage.json_bytes(prov),'raw':raw,'http_response':body,'receipt':storage.json_bytes(receipt)}
                self.context['supplier_capture']={}
                for key,data in documents.items():
                    self.captures[key]=data;self.context['supplier_capture'][key]=dict(path=key,sha256=hashlib.sha256(data).hexdigest())
        self.overlay=dict(check_additions=dict(declare=[dict(kind='cpl_rotation',params=dict(export_check='export',geometry_check='geometry',
            policy='cpl/policy.json',source_tolerance_nm='200',supplier_footprints=['cpl/supplier/'+i+'.provenance.json' for i in self.original_roster]))]),
            judgments={'judgments/acq-mech-4-assembly-applicability.json':dict(questions=['Check assembly-release ids and applicability.'],sources=['assembly-release-ids.json','stock-assembly-release'])})
        ctx=alternative.CapturedContext(requirement_map.input_inventory(self.root),
            dict(path='preparation/catalog/catalog.json',sha256=storage.digest(self.root/'preparation/catalog/catalog.json')),
            dict(path='preparation/ti/TI-ADS1115IDGST.html',sha256=storage.digest(self.root/'preparation/ti/TI-ADS1115IDGST.html')),
            dict(path='manufacturer-text/ads1115.txt',sha256=storage.digest(self.root/'manufacturer-text/ads1115.txt')),'2026-10-18T01:00:00+00:00')
        alternative.revise(self.root,ctx)

    def test_missing_selected_supplier_refuses_instead_of_reusing_primary(self):
        with self.assertRaisesRegex(ValueError,'exact supplier.*C468683'):
            self.module.finalize(self.root,self.overlay,self.cpl)
        self.assertEqual(json.loads((self.root/'assembly-release-ids.json').read_bytes()),self.original_roster)

    def test_exact_capture_closes_selected_list_and_declares_roster_delta(self):
        files=self.module.supplier_files(self.context,self.captures)
        for path,raw in files.items():(self.cpl/path).write_bytes(raw)
        before=copy.deepcopy(self.overlay['check_additions']['declare'][0]['params'])
        report=self.module.finalize(self.root,self.overlay,self.cpl)
        params=self.overlay['check_additions']['declare'][0]['params']
        self.assertEqual(set(report['supplier_footprints']),{'C131334','C468683'})
        self.assertEqual(report['roster']['added'],['C468683']);self.assertEqual(report['roster']['removed'],['C37593'])
        self.assertEqual({k:v for k,v in params.items() if k!='supplier_footprints'},
                         {k:v for k,v in before.items() if k!='supplier_footprints'})
        self.assertNotIn('cpl/supplier/C37593.provenance.json',params['supplier_footprints'])
        question=self.overlay['judgments']['judgments/acq-mech-4-assembly-applicability.json']
        self.assertIn('not supplier approval',question['questions'][0])
        self.assertTrue(any('SHA256 '+storage.digest(self.root/'assembly-release-ids.json') in x for x in question['sources']))

    def test_relabelled_or_changed_supplier_capture_is_refused(self):
        prov=json.loads(self.captures['provenance']);prov['lcsc']='C37593'
        self.captures['provenance']=storage.json_bytes(prov)
        with self.assertRaisesRegex(ValueError,'LCSC id differs'):
            self.module.supplier_files(self.context,self.captures)

    def test_eligible_primary_keeps_its_supplier_even_when_alternative_capture_exists(self):
        root=self.root/'primary';root.mkdir();authored_fixture(root)
        catpath=root/'preparation/catalog/catalog.json';catalog=json.loads(catpath.read_bytes())
        req=next(r for r in catalog['requests'] if r['lcsc']=='C37593')
        rawpath=catpath.parent/req['response']['path'];doc=json.loads(rawpath.read_bytes())
        doc['data']['componentPageInfo']['list'][0]['stockCount']=10
        rawpath.write_bytes(storage.json_bytes(doc));req['response'].update(sha256=storage.digest(rawpath),size_bytes=rawpath.stat().st_size)
        _,parsed=jlc_stock_capture.parse_search_response(rawpath.read_bytes(),'C37593')
        catalog['parts']['C37593']={**parsed,'last_seen':req['fetched_at']};catpath.write_bytes(storage.json_bytes(catalog))
        def blob(path):return dict(path=path,sha256=storage.digest(root/path))
        ctx=alternative.CapturedContext(requirement_map.input_inventory(root),blob('preparation/catalog/catalog.json'),
            blob('preparation/ti/TI-ADS1115IDGST.html'),blob('manufacturer-text/ads1115.txt'),'2026-10-18T01:00:00+00:00')
        self.assertEqual(alternative.revise(root,ctx)['selected'],'C37593')
        (root/'assembly-release-ids.json').write_bytes(storage.json_bytes(self.original_roster))
        for path,raw in self.module.supplier_files(self.context,self.captures).items():(self.cpl/path).write_bytes(raw)
        result=self.module.finalize(root,self.overlay,self.cpl)
        self.assertEqual(set(result['supplier_footprints']),set(self.original_roster))
        self.assertEqual(result['roster']['added'],[]);self.assertEqual(result['roster']['removed'],[])

    def test_exact_id_with_wrong_supplier_manufacturer_part_refuses(self):
        raw=json.loads(self.captures['raw']);raw['dataStr']['head']['c_para']['Manufacturer Part']='ADS1015IDGST'
        self.captures['raw']=storage.json_bytes(raw)
        prov=json.loads(self.captures['provenance']);prov.update(sha256=hashlib.sha256(self.captures['raw']).hexdigest(),size_bytes=len(self.captures['raw']))
        self.captures['provenance']=storage.json_bytes(prov)
        with self.assertRaisesRegex(ValueError,'incompatible manufacturer part'):
            self.module.supplier_files(self.context,self.captures)
