"""Owner-declared relations, never inferred from candidate naming."""
import copy
import importlib.util
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.hardware_interface import evaluate, validate_contract, read_inventory
from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses, load_baseline
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import run_checks
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/hardware-interface'
spec = importlib.util.spec_from_file_location('interface_fixture', FIXTURE / 'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.contract = read_json(FIXTURE / 'contract.json')

    def test_contract_rejects_empty_predicates(self):
        self.contract['invariants'] = []
        with self.assertRaisesRegex(ValueError, 'invariants'):
            validate_contract(self.contract)

    def test_contract_refuses_ambiguous_or_wildcard_authority(self):
        for mutation in (
            lambda c: c['components']['load']['locations'].append({'sheet':'/','reference':'X1'}),
            lambda c: c['invariants'].append(copy.deepcopy(c['invariants'][0])),
            lambda c: c['allowed_edits'][0].update(property='*'),
            lambda c: c['invariants'][0].update(kind='transparent_resistor'),
            lambda c: c['invariants'][2].update(names=[]),
            lambda c: c['terminals']['in'].update(component='absent'),
            lambda c: c['terminals'].update(another_in=copy.deepcopy(c['terminals']['in'])),
        ):
            contract = copy.deepcopy(self.contract)
            mutation(contract)
            with self.subTest(contract=contract), self.assertRaises(ValueError):
                validate_contract(contract)

    def test_duplicate_json_identity_is_not_silently_overwritten(self):
        from agent_ee.hardware_interface import load_contract
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'contract.json'
            original=(FIXTURE/'contract.json').read_text()
            path.write_text(original.replace('"schema":', '"schema":"ignored", "schema":'))
            with self.assertRaisesRegex(ValueError,'duplicate JSON'):
                load_contract(Path(tmp),'contract.json')


class NativeInterfaceTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', *, resistor='10k', contract=None, mutate_xml=None, schematic=None):
        self.counter += 1
        case = self.root / str(self.counter)
        case.mkdir()
        (case / 'interface.kicad_sch').write_text(schematic if schematic is not None else generator.schematic(variant, resistor))
        (case / 'interface.kicad_pcb').write_bytes((FIXTURE / 'interface.kicad_pcb').read_bytes())
        (case / 'requirements.md').write_bytes((FIXTURE / 'requirements.md').read_bytes())
        write_json(case / 'contract.json', contract or read_json(FIXTURE / 'contract.json'))
        scope = read_json(FIXTURE / 'control.scope.json')
        write_json(case / 'scope.json', scope)
        run = case / 'run'
        run.mkdir()
        capture(case / 'scope.json', run)
        if mutate_xml:
            # Export once with the native producer, then test malformed reader inputs.
            netlist = run_checks(run, time.monotonic()+60)[0]
            self.assertEqual(netlist['execution'], 'ok', netlist)
            xml = run / 'work/netlist/design.xml'
            tree = ET.parse(xml)
            mutate_xml(tree.getroot())
            tree.write(xml)
            from agent_ee.checks import Ctx, check_hardware_interface, CheckError
            manifest = read_json(run / 'candidate.json')
            ctx = Ctx(run, manifest, scope['checks'][1], time.monotonic()+60)
            try:
                check_hardware_interface(ctx)
            except CheckError as exc:
                ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
            return run, {'interface':ctx.result}
        checks = run_checks(run, time.monotonic()+90)
        return run, {c['id']:c for c in checks}

    def test_owned_control_and_defects_match_predeclared_oracle(self):
        expectations = read_json(FIXTURE / 'expectations.json')
        baseline = None
        for variant, expected in expectations.items():
            with self.subTest(variant=variant):
                run, checks = self.run_variant(variant, resistor='100k' if variant=='wrong-value' else '10k')
                result = checks['interface']
                self.assertEqual(checks['netlist']['execution'], 'ok', checks)
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['details']['violations'], expected, result)
                self.assertEqual(result['outcome'], 'defect' if expected else 'pass', result)
                current = load_baseline(run)['baseline_id']
                if baseline is None:
                    baseline = current
                self.assertEqual(current, baseline)
                if variant in ('control', 'wrong-value'):
                    self.assertEqual(checks['response']['execution'], 'ok', checks['response'])
                    self.assertEqual(checks['response']['outcome'], 'pass' if variant=='control' else 'defect', checks['response'])
                    import math
                    r=10000 if variant=='control' else 100000
                    expected_voltage=1e6/(1e6+r)*(1-math.exp(-.001/((r*1e6/(r+1e6))*100e-9)))
                    measured=checks['response']['details']['measurements']['vout_1ms']['measured']
                    self.assertAlmostEqual(measured,expected_voltage,delta=.001)
                freeze_evidence(run, list(checks.values()))
                verify_evidence(run, list(checks.values()), required=True)

    def test_disconnected_terminal_cannot_satisfy_any_conductor_predicate(self):
        contract = read_json(FIXTURE / 'contract.json')
        contract['invariants'].append({
            **copy.deepcopy(contract['invariants'][0]), 'id': 'probe-isolated',
            'kind': 'different_net', 'terminals': ['in', 'probe-tip']})
        del contract['invariants'][-1]['component']
        del contract['invariants'][-1]['populated']
        run, checks = self.run_variant('disconnect', contract=contract)
        records = {r['id']: r for r in checks['interface']['details']['invariants']}
        self.assertEqual(records['probe-isolated']['status'], 'violation')
        self.assertEqual(records['probe-wire']['status'], 'violation')
        inventory = read_inventory(run / 'work/netlist/design.xml')
        code = inventory['memberships'][('PROBE', '1')]
        self.assertEqual(inventory['nets'][code], 'unconnected-(PROBE-Pad1)')
        self.assertEqual(records['probe-isolated']['actual']['terminals'][1]['net_code'], code)

    def test_native_named_nc_lookalike_and_explicit_no_connect(self):
        contract = read_json(FIXTURE / 'contract.json')
        named = copy.deepcopy(contract['invariants'][3])
        named.update(id='probe-name', terminal='probe-tip', names=['unconnected-(PROBE-Pad1)'])
        contract['invariants'].append(named)
        schematic = generator.schematic().replace('(label "OUT"', '(label "unconnected-(PROBE-Pad1)"')
        # KiCad qualifies intentional local labels with the sheet path.
        for invariant in contract['invariants']:
            if invariant['kind'] == 'named_net' and '/OUT' in invariant['names']:
                invariant['names'] = ['/unconnected-(PROBE-Pad1)']
        contract['invariants'][-1]['names'] = ['/unconnected-(PROBE-Pad1)']
        run, checks = self.run_variant(contract=contract, schematic=schematic)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)
        inventory = read_inventory(run / 'work/netlist/design.xml')
        self.assertNotIn(('PROBE', '1'), inventory['disconnected'])
        self.assertEqual(next(r for r in checks['interface']['details']['invariants']
            if r['id'] == 'anonymous-wire')['status'], 'satisfied')
        schematic = generator.schematic('disconnect')
        schematic = schematic[:-2] + (
            f'(no_connect (at 120 50) (uuid "{generator.uid("explicit-nc")}")))\n')
        contract = read_json(FIXTURE / 'contract.json')
        contract['invariants'].append({**named, 'names': ['unconnected-(PROBE-Pad1)']})
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        records = {r['id']: r for r in checks['interface']['details']['invariants']}
        self.assertEqual(records['probe-name']['status'], 'violation')
        self.assertEqual(records['probe-wire']['status'], 'violation')

    def test_native_global_label_exact_nc_name_is_a_conductor(self):
        contract = read_json(FIXTURE / 'contract.json')
        named = copy.deepcopy(next(i for i in contract['invariants'] if i['kind'] == 'named_net'))
        named.update(id='probe-global', terminal='probe-tip', names=['unconnected-(PROBE-Pad1)'])
        contract['invariants'] = [named]
        contract['allowed_edits'] = []
        schematic = generator.schematic('disconnect')
        label = ('(global_label "unconnected-(PROBE-Pad1)" (shape input) (at 120 50 0) '
                 f'(effects (font (size 1 1))) (uuid "{generator.uid("global-nc")}"))')
        schematic = schematic[:-2] + label + ')\n'
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)

    def test_connected_anonymous_nc_lookalike_retains_membership(self):
        def rename(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('Net-'))
            self.assertEqual(len(net.findall('node')), 2)
            net.set('name', 'unconnected-(R2-Pad2)')
        _, checks = self.run_variant(mutate_xml=rename)
        self.assertEqual(checks['interface']['outcome'], 'pass', checks)
        record = next(r for r in checks['interface']['details']['invariants'] if r['id'] == 'anonymous-wire')
        self.assertEqual(record['status'], 'satisfied')
        self.assertEqual(record['actual']['terminals'][0]['net_name'], 'unconnected-(R2-Pad2)')

    def test_named_pin_disconnection_uses_native_symbol_pin_name(self):
        contract = read_json(FIXTURE / 'contract.json')
        relation = copy.deepcopy(next(i for i in contract['invariants'] if i['id'] == 'series-isolation'))
        relation.update(id='probe-isolated', terminals=['in', 'probe-tip'])
        contract['invariants'].append(relation)
        schematic = generator.schematic('disconnect').replace('(name "1"', '(name "TIP"')
        _, checks = self.run_variant(contract=contract, schematic=schematic)
        record = next(r for r in checks['interface']['details']['invariants'] if r['id'] == 'probe-isolated')
        self.assertEqual(record['status'], 'violation')
        self.assertEqual(record['actual']['terminals'][1]['net_name'], 'unconnected-(PROBE-TIP-Pad1)')

    def test_nc_normalization_never_hides_duplicate_or_unknown_nodes(self):
        def duplicate(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('unconnected-'))
            root.findall('./nets/net')[0].append(copy.deepcopy(net.find('node')))
        def unknown(root):
            net = next(n for n in root.findall('./nets/net') if n.get('name').startswith('unconnected-'))
            net.find('node').set('pin', '99')
        for mutation in (duplicate, unknown):
            _, checks = self.run_variant('disconnect', mutate_xml=mutation)
            self.assertEqual(checks['interface']['outcome'], 'unknown', checks)

    def test_value_inventory_distinguishes_missing_duplicate_and_empty(self):
        def missing(root):
            comp = root.find("./components/comp[@ref='R1']")
            comp.remove(comp.find('value'))
        def duplicate(root):
            ET.SubElement(root.find("./components/comp[@ref='R1']"), 'value').text = '10k'
        for mutation in (missing, duplicate):
            with self.subTest(mutation=mutation.__name__):
                _, checks = self.run_variant(mutate_xml=mutation)
                self.assertEqual(checks['interface']['execution'], 'unsupported')
                self.assertEqual(checks['interface']['outcome'], 'unknown')
                self.assertTrue(all(r['status'] == 'not_evaluated'
                    for r in checks['interface']['details']['invariants']))
        def empty(root):
            root.find("./components/comp[@ref='R1']/value").text = None
        _, checks = self.run_variant(mutate_xml=empty)
        self.assertEqual(checks['interface']['execution'], 'ok')
        self.assertEqual(checks['interface']['details']['violations'], ['series-value'])

    def test_duplicate_net_names_and_invalid_unused_sheet_paths_are_unknown(self):
        def duplicate(root):
            nets = root.findall('./nets/net')
            nets[-1].set('name', nets[0].get('name'))
        for mutation in [duplicate] + [
                lambda root, path=path: ET.SubElement(root.find('design'), 'sheet', tstamps=path)
                for path in ('unused', '/unused', 'unused/', '//', '/a//b/')]:
            with self.subTest(mutation=mutation):
                run, checks = self.run_variant(mutate_xml=mutation)
                with self.assertRaises(ValueError):
                    read_inventory(run / 'work/netlist/design.xml')
                self.assertEqual(checks['interface']['outcome'], 'unknown')
                self.assertTrue(all(r['status'] == 'not_evaluated'
                    for r in checks['interface']['details']['invariants']))

    def test_sealed_failed_witness_and_tamper(self):
        run, checks = self.run_variant('delete-load')
        freeze_evidence(run, list(checks.values()))
        witnesses = [{'requirement_id':'interface','check_id':'interface'}]
        citation = [{'path':'checks/interface.json','locator':'load-present'}]
        validate_witnesses(run, witnesses, citation)
        for path in ('work/interface/interface.json','work/netlist/design.xml'):
            target = run / path
            before = target.read_bytes()
            target.write_bytes(before+b' ')
            with self.assertRaises(ValueError):
                verify_evidence(run, list(checks.values()), required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witnesses, citation)
            target.write_bytes(before)
        (run/'inputs/contract.json').write_text('{}')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_unknown_mapping_is_incomplete_even_with_known_violation(self):
        contract = read_json(FIXTURE/'contract.json')
        contract['components']['external']['symbols'] = ['Unknown:Port']
        run, checks = self.run_variant('delete-load', contract=contract)
        check = checks['interface']
        self.assertEqual((check['execution'],check['outcome']), ('unsupported','unknown'), check)
        self.assertIn('load-present', check['details']['violations'])
        freeze_evidence(run, list(checks.values()))
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id':'interface','check_id':'interface'}],
                               [{'path':'checks/interface.json','locator':'load-present'}])

    def test_native_zero_ohm_value_never_merges_direct_conductors(self):
        contract=read_json(FIXTURE/'contract.json')
        contract['allowed_edits'][0]['values'].append('0')
        _,checks=self.run_variant(resistor='0',contract=contract)
        result=checks['interface']
        self.assertEqual(result['outcome'],'pass',result)
        relation=next(r for r in result['details']['invariants'] if r['id']=='series-isolation')
        self.assertEqual(relation['status'],'satisfied',relation)
        self.assertNotEqual(*[t['net_code'] for t in relation['actual']['terminals']])

    def test_missing_pin_metadata_is_unknown_but_known_unconnected_pin_is_defect(self):
        contract = read_json(FIXTURE/'contract.json')
        contract['terminals']['probe-tip']['pin'] = '99'
        _, checks = self.run_variant(contract=contract)
        self.assertEqual(checks['interface']['outcome'], 'unknown', checks)
        _, checks = self.run_variant('disconnect')
        self.assertEqual(checks['interface']['details']['violations'], ['probe-wire'], checks)

    def test_native_inventory_rejects_ambiguity_instead_of_overwriting(self):
        def duplicate_ref(root):
            comp=copy.deepcopy(root.find('./components/comp'))
            comp.find('sheetpath').set('tstamps','/other/')
            root.find('components').append(comp)
        def duplicate_pin(root):
            pins=root.find('./libparts/libpart/pins')
            pins.append(copy.deepcopy(pins[0]))
        def missing_part(root):
            root.find('libparts').remove(root.find('./libparts/libpart'))
        def multiple_nets(root):
            root.findall('./nets/net')[1].append(copy.deepcopy(root.find('./nets/net/node')))
        def unknown_sheet(root):
            root.find('./components/comp/sheetpath').set('tstamps','/unknown/')
        def duplicate_uuid(root):
            comps=root.findall('./components/comp')
            comps[1].find('tstamps').text=comps[0].findtext('tstamps')
        for mutation in (duplicate_ref, duplicate_pin, missing_part, multiple_nets, unknown_sheet, duplicate_uuid):
            with self.subTest(mutation=mutation.__name__):
                _, checks = self.run_variant(mutate_xml=mutation)
                self.assertEqual((checks['interface']['execution'], checks['interface']['outcome']),
                                 ('unsupported','unknown'), checks)

    def test_direct_conductors_do_not_union_passives_and_names_are_exact(self):
        run, _ = self.run_variant()
        contract=read_json(FIXTURE/'contract.json')
        inventory=read_inventory(run/'work/netlist/design.xml')
        for cls, value in [('Device:R','0'),('Device:C','100n'),('Device:L','1m')]:
            inventory['components'][('/','R1')].update(symbol=cls,value=value)
            contract['components']['series']['symbols']=[cls]
            relation=next(i for i in contract['invariants'] if i['id']=='series-isolation')
            relation['kind']='same_net'
            records=evaluate(contract,inventory)
            self.assertEqual(next(r for r in records if r['id']=='series-isolation')['status'],'violation')
        inventory['nets']={code:('arbitrary anonymous spelling' if name.startswith('Net-') else name)
                           for code,name in inventory['nets'].items()}
        self.assertEqual(next(r for r in evaluate(contract,inventory) if r['id']=='anonymous-wire')['status'],'satisfied')
        contract=read_json(FIXTURE/'contract.json')
        contract['invariants'][3]['names'].append('/RENAMED')
        _, checks=self.run_variant('renamed',contract=contract)
        self.assertEqual(checks['interface']['outcome'],'pass',checks)

    def test_contract_changes_create_new_baselines_and_requirement_sources_are_exact(self):
        run,_=self.run_variant()
        baseline=load_baseline(run)['baseline_id']
        for mutation in (lambda c:c['allowed_edits'][0]['values'].append('1k'),
                         lambda c:c['invariants'][3]['names'].append('/RENAMED')):
            contract=read_json(FIXTURE/'contract.json');mutation(contract)
            run,_=self.run_variant(contract=contract)
            self.assertNotEqual(load_baseline(run)['baseline_id'],baseline)
        contract=read_json(FIXTURE/'contract.json')
        contract['invariants'][0]['source']['locator']='invented'
        with self.assertRaisesRegex(ValueError,'requirement source'):
            self.run_variant(contract=contract)

    def test_campaign_write_authority_excludes_contract_and_scope2_also_freezes_it(self):
        from agent_ee.campaign import mutable_inputs
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        baseline=load_baseline(run)
        # Existing campaign helper expects a board field even with explicit mutable_inputs.
        manifest['scope'].update(board='interface.kicad_sch',mutable_inputs=['interface.kicad_sch'])
        self.assertEqual(mutable_inputs(manifest,baseline),['interface.kicad_sch'])
        manifest['scope']['mutable_inputs'].append('contract.json')
        with self.assertRaisesRegex(ValueError,'immutable baseline'):
            mutable_inputs(manifest,baseline)
        scope=copy.deepcopy(manifest['scope'])
        scope['schema']='agent-ee.scope/2'
        for req in scope['baseline']['requirements']:
            del req['intent']
        legacy=compile_baseline(scope,manifest['files'],run/'inputs')
        self.assertIn('contract.json',legacy['files'])

    def test_capability_refuses_uncovered_requirement_and_forward_prerequisite(self):
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        scope=manifest['scope']
        scope['baseline']['requirements'][1]['checks'].append('interface')
        with self.assertRaisesRegex(ValueError,'requirement mappings'):
            compile_baseline(scope,manifest['files'],run/'inputs')
        scope['baseline']['requirements'][1]['checks'].remove('interface')
        scope['checks'][:2]=reversed(scope['checks'][:2])
        with self.assertRaisesRegex(ValueError,'forward'):
            compile_baseline(scope,manifest['files'],run/'inputs')

    def test_one_failed_check_cannot_witness_an_unrelated_satisfied_requirement(self):
        from agent_ee.baseline import compile_baseline
        run,_=self.run_variant()
        manifest=read_json(run/'candidate.json')
        contract=read_json(run/'inputs/contract.json')
        contract['invariants'][0]['requirement']='another'
        scope=manifest['scope']
        scope['baseline']['requirements'].append({
            'id':'another','statement':'A separate load obligation.',
            'source':contract['invariants'][0]['source'],
            'checks':['interface'],'intent':'acceptance'})
        write_json(run/'inputs/contract.json',contract)
        from agent_ee.storage import digest
        manifest['files']['contract.json']=digest(run/'inputs/contract.json')
        with self.assertRaisesRegex(ValueError,'one requirement'):
            compile_baseline(scope,manifest['files'],run/'inputs')

    def test_native_repeated_sheet_instances_use_scoped_symbol_uuids(self):
        case=self.root/'hierarchy'
        case.mkdir()
        main_uuid=generator.uid('hierarchy-root')
        sheets=[generator.uid('sheet-a'),generator.uid('sheet-b')]
        child=generator.schematic()
        references=['X1','R1','C1','RLOAD','PROBE','R2','R3']
        for ref in references:
            before=f'(project "interface" (path "/{generator.uid("root")}" (reference "{ref}") (unit 1)))'
            after='(project "interface" '+''.join(
                f'(path "/{main_uuid}/{sheet}" (reference "{ref}{suffix}") (unit 1))'
                for sheet,suffix in zip(sheets,['A','B']))+')'
            self.assertIn(before,child)
            child=child.replace(before,after)
        (case/'child.kicad_sch').write_text(child)
        sheet_nodes=''.join(f'(sheet (at {40+30*i} 30) (size 20 20) '
            f'(stroke (width 0) (type default)) (fill (color 0 0 0 0)) (uuid "{sheet}") '
            f'(property "Sheetname" "{name}" (at 40 30 0) (effects (font (size 1 1)))) '
            '(property "Sheetfile" "child.kicad_sch" (at 40 50 0) (effects (font (size 1 1)))))'
            for i,(sheet,name) in enumerate(zip(sheets,['First','Second'])))
        (case/'interface.kicad_sch').write_text(f'(kicad_sch (version 20250114) '
            f'(generator "eeschema") (uuid "{main_uuid}") (paper "A4") (lib_symbols) {sheet_nodes})')
        for name in ['interface.kicad_pcb','requirements.md']:
            (case/name).write_bytes((FIXTURE/name).read_bytes())
        contract=read_json(FIXTURE/'contract.json')
        for component in contract['components'].values():
            for location in component['locations']:
                location.update(sheet=f'/{sheets[0]}/',reference=location['reference']+'A')
        for invariant in contract['invariants']:
            if invariant['kind']=='named_net':
                invariant['names']=['/First'+name for name in invariant['names']]
        write_json(case/'contract.json',contract)
        scope=read_json(FIXTURE/'control.scope.json')
        scope['checks']=scope['checks'][:2]
        scope['baseline']['requirements']=scope['baseline']['requirements'][:1]
        scope['inputs'].append({'path':'child.kicad_sch','at':'child.kicad_sch'})
        write_json(case/'scope.json',scope)
        run=case/'run';run.mkdir();capture(case/'scope.json',run)
        checks=run_checks(run,time.monotonic()+90)
        self.assertEqual(checks[0]['details']['sheet_instances'],3,checks)
        self.assertEqual(checks[0]['execution'],'ok',checks)
        self.assertEqual(checks[1]['outcome'],'pass',checks)


class InterfacePipelineTests(unittest.TestCase):
    def test_native_defect_and_unknown_centralized_adjudication(self):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import run_review, verify
        from test_capability_pipeline import NativeCapabilityPipelineTests, PROFILE_CALL
        require_toolchain(native=True)
        self.calls=[]
        from agent_ee.cli import runtime_files
        from agent_ee.storage import digest
        runtime = runtime_files()
        for name in ('codex', 'codex_tool_host'):
            runtime[name] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        admission = patch('agent_ee.cli.runtime_files', return_value=runtime)
        admission.start()
        self.addCleanup(admission.stop)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('control','delete-load','unknown'):
                case=Path(tmp)/name
                case.mkdir()
                variant='delete-load' if name=='delete-load' else 'control'
                (case/'interface.kicad_sch').write_text(generator.schematic(variant))
                (case/'interface.kicad_pcb').write_bytes((FIXTURE/'interface.kicad_pcb').read_bytes())
                (case/'requirements.md').write_bytes((FIXTURE/'requirements.md').read_bytes())
                contract=read_json(FIXTURE/'contract.json')
                if name=='unknown':
                    contract['components']['probe']['symbols']=['Unknown:Probe']
                write_json(case/'contract.json',contract)
                scope=read_json(FIXTURE/'control.scope.json')
                scope['review_profile']={'schema':'agent-ee.profile/1','name':'scripted-interface',
                    'reviewers':[{**PROFILE_CALL,'role':'circuit'},{**PROFILE_CALL,'role':'physical'}],
                    'adjudicator':PROFILE_CALL}
                write_json(case/'scope.json',scope)
                def agent(*args,**kwargs):
                    return NativeCapabilityPipelineTests.agent(self,*args,**kwargs)
                with patch('agent_ee.reviewers.dispatch',side_effect=agent), contextlib.redirect_stdout(io.StringIO()):
                    status=run_review(case/'scope.json',case/'run',120)
                self.assertNotIn('error',read_json(case/'run/result.json'))
                result=verify(case/'run')
                self.assertEqual(status,0 if name=='control' else 1,result)
                self.assertEqual(result['outcome'],{'control':'criteria_satisfied',
                    'delete-load':'defects_or_unresolved','unknown':'incomplete'}[name],result)
                accepted=[f for f in result['findings'] if f.get('status')=='accepted']
                self.assertEqual([f['local_id'] for f in accepted],['interface'] if name=='delete-load' else [],result)
                self.assertTrue(all(f['source']=='check' for f in accepted))
                self.assertCountEqual([label for _,label in self.calls[-3:-1]],['circuit','physical'])
                self.assertEqual(self.calls[-1][1],'adjudicator')
