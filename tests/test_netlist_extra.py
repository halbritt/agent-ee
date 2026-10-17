"""Native exports and independent topology requirements, never reference typing."""
import copy
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from agent_ee.netlist_extra import evaluate, validate_policy
from agent_ee.storage import read_json, write_json
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import Ctx, CheckError, check_netlist_extra, run_checks, parse_xml_netlist
from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses, load_baseline
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/netlist-extra'
spec = importlib.util.spec_from_file_location('extra_fixture', FIXTURE/'generate.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class PolicyTests(unittest.TestCase):
    def test_policy_rejects_ambiguous_and_unanchored_authority(self):
        for mutation in (
            lambda p:p['rail_pairs'][0].update(endpoints=['+3V3','GND']),
            lambda p:p['rail_pairs'][0]['endpoints'].__setitem__(1, p['rail_pairs'][0]['endpoints'][0]),
            lambda p:p['classes']['Owned:R'].update(pins=['1','1']),
            lambda p:p['classes']['Owned:R'].update(**{'class':'IC'}),
            lambda p:p['exemptions'].append(copy.deepcopy(p['exemptions'][0])),
        ):
            policy = generator.policy()
            mutation(policy)
            with self.subTest(policy=policy), self.assertRaises((ValueError, TypeError)):
                validate_policy(policy)


class NativeExtraTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', policy=None):
        self.counter += 1
        root = self.root/str(self.counter)
        root.mkdir()
        for child in (None, 'a','b'):
            name = 'topology' if child is None else child
            (root/(name+'.kicad_sch')).write_text(generator.schematic(variant=variant, child=child))
        (root/'requirements.md').write_text('Topology: declared two-terminal pins and supply/ground endpoints must differ.')
        write_json(root/'policy.json', generator.policy() if policy is None else policy)
        paths = ['topology.kicad_sch','a.kicad_sch','b.kicad_sch','requirements.md','policy.json']
        scope = {'schema':'agent-ee.scope/3', 'schematic':'topology.kicad_sch',
            'inputs':[{'path':p,'at':p} for p in paths], 'requirements':['requirements.md'],
            'checks':[{'id':'netlist','kind':'netlist'}, {'id':'extra','kind':'netlist_extra',
                'params':{'policy':'policy.json','netlist_check':'netlist'}}],
            'baseline':{'requirements':[{'id':'topology','statement':'Direct-net topology',
                'source':{'path':'requirements.md','locator':'Topology'},
                'checks':['netlist','extra'],'intent':'acceptance'}],
                'targets':['requirements.md'], 'reference_architectures':['requirements.md']}}
        write_json(root/'scope.json', scope)
        run = root/'run'
        run.mkdir()
        capture(root/'scope.json', run)
        checks = run_checks(run, time.monotonic()+60)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return run, checks

    def rerun_extra(self, run):
        manifest = read_json(run/'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic()+30)
        try:
            check_netlist_extra(ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return ctx.result

    def test_native_control_passive_switch_rail_and_hierarchical_advisory(self):
        for variant, expected in [('control',[]), ('passive-short',['R1']),
                                  ('switch-short',['SW1']), ('rail-merge',['supply-ground'])]:
            with self.subTest(variant=variant):
                run, checks = self.run_variant(variant)
                result = checks[1]
                self.assertEqual(result['execution'], 'ok', result)
                self.assertEqual(result['outcome'], 'defect' if expected else 'pass', result)
                violations = [r for r in result['details']['records'] if r['status']=='violation']
                self.assertEqual([r.get('reference',r.get('id')) for r in violations], expected)
                if variant == 'passive-short':
                    self.assertEqual(violations[0]['pins'], ['1','2'])
                    self.assertEqual({t['net_name'] for t in violations[0]['terminals']}, {'/+3V3'})
                if variant == 'rail-merge':
                    self.assertIn(['R1','1'], violations[0]['witness']['members'])
                    self.assertIn(['R2','2'], violations[0]['witness']['members'])
                groups = {a['label']:a for a in result['details']['advisories']}
                self.assertEqual({n['name'] for n in groups['SDA']['nets']}, {'/a/SDA','/b/SDA'})
                self.assertEqual(groups['SDA']['status'], 'informational')
                self.assertEqual(len({n['net_id'] for n in groups['SDA']['nets']}), 2)
                freeze_evidence(run, checks)
                verify_evidence(run, checks, required=True)

    def test_unknown_class_and_incomplete_coverage_never_inherit_pass_or_defect(self):
        _, checks = self.run_variant('unknown')
        result = checks[1]
        self.assertEqual((result['execution'],result['outcome']), ('unsupported','unknown'), result)
        unknown = [r for r in result['details']['records'] if r['status']=='unknown']
        self.assertEqual([r['reference'] for r in unknown], ['R2'])
        self.assertEqual(unknown[0]['symbol'], 'Unknown:R')
        policy = generator.policy()
        del policy['classes']['Owned:Switch']
        run, checks = self.run_variant('passive-short', policy)
        result = checks[1]
        self.assertEqual((result['execution'],result['outcome']), ('unsupported','unknown'), result)
        self.assertEqual([r['reference'] for r in result['details']['records'] if r['status']=='violation'], ['R1'])
        freeze_evidence(run, checks)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id':'topology','check_id':'extra'}],
                               [{'path':'checks/extra.json','locator':'R1'}])

    def test_anchor_override_exemption_and_pin_inventory_boundaries(self):
        policy = generator.policy()
        policy['overrides'] = [{'component':{'sheet':'/','uuid':generator.uid('R2')},
            'symbol':'Unknown:R','rule':{'class':'resistor','pins':['1','2']}}]
        _, checks = self.run_variant('unknown', policy)
        self.assertEqual(checks[1]['outcome'], 'pass', checks[1])
        self.assertEqual([r['reference'] for r in checks[1]['details']['records'] if r['status']=='excluded'], ['RDEBUG'])
        for alter in (
            lambda p:p['classes']['Owned:R'].update(pins=['1','3']),
            lambda p:p['rail_pairs'][0]['endpoints'][0].update(uuid='missing'),
            lambda p:p['exemptions'][0]['component'].update(uuid='missing'),
            lambda p:p['exemptions'][0].update(symbol='Unknown:R'),
        ):
            policy = generator.policy()
            alter(policy)
            with self.subTest(policy=policy):
                _, checks = self.run_variant(policy=policy)
                self.assertEqual((checks[1]['execution'],checks[1]['outcome']), ('unsupported','unknown'), checks[1])

    def test_missing_failed_malformed_or_changed_prerequisite_is_failed(self):
        run, _ = self.run_variant()
        path = run/'work/netlist/connectivity.json'
        raw = path.read_bytes()
        for replacement in (None, b'{', b'{}'):
            if replacement is None:
                path.unlink()
            else:
                path.write_bytes(replacement)
            result = self.rerun_extra(run)
            self.assertEqual((result['execution'],result['outcome']), ('failed','unknown'), result)
            path.write_bytes(raw)
        prior = run/'checks/netlist.json'
        original = read_json(prior)
        for changed in ({**original, 'execution':'failed'}, {**original, 'candidate_id':'other'}):
            write_json(prior, changed)
            result = self.rerun_extra(run)
            self.assertEqual((result['execution'],result['outcome']), ('failed','unknown'), result)
        write_json(prior, original)

    def test_duplicate_or_contradictory_native_identity_is_never_overwritten(self):
        run, _ = self.run_variant()
        xml = run/'work/netlist/design.xml'
        original = xml.read_bytes()
        for change in (
            lambda root:root.find('components').append(copy.deepcopy(root.find('./components/comp'))),
            lambda root:root.find('nets').append(copy.deepcopy(root.find('./nets/net'))),
            lambda root:root.find('./nets/net').append(copy.deepcopy(root.find('./nets/net/node'))),
            lambda root:root.find('./nets/net[2]').append(copy.deepcopy(root.find('./nets/net/node'))),
        ):
            tree = ET.ElementTree(ET.fromstring(original))
            change(tree.getroot())
            tree.write(xml)
            # Even a matching lossy projection cannot authorize ambiguous XML.
            write_json(run/'work/netlist/connectivity.json', parse_xml_netlist(xml))
            result = self.rerun_extra(run)
            self.assertEqual(result['outcome'], 'unknown', result)
            self.assertEqual(result['execution'], 'failed', result)
        xml.write_bytes(original)

    def test_sealed_witness_policy_and_input_tampering(self):
        run, checks = self.run_variant('passive-short')
        baseline = load_baseline(run)
        self.assertIn('policy.json', baseline['files'])
        self.assertNotIn('topology.kicad_sch', baseline['files'])
        freeze_evidence(run, checks)
        witness = [{'requirement_id':'topology','check_id':'extra'}]
        citation = [{'path':'checks/extra.json','locator':'R1 pins 1,2'}]
        validate_witnesses(run, witness, citation)
        for rel in ('work/extra/topology.json','work/netlist/design.xml','work/netlist/connectivity.json'):
            path = run/rel
            raw = path.read_bytes()
            path.write_bytes(raw+b' ')
            with self.subTest(path=rel), self.assertRaises(ValueError):
                verify_evidence(run, checks, required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witness, citation)
            path.write_bytes(raw)
        for rel in ('policy.json','topology.kicad_sch'):
            path = run/'inputs'/rel
            raw = path.read_bytes()
            path.write_bytes(raw+b' ')
            with self.assertRaises(ValueError):
                verify_snapshot(run)
            path.write_bytes(raw)
        control, control_checks = self.run_variant()
        freeze_evidence(control, control_checks)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(control, witness, citation)
