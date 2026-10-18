"""Net-name provenance (AGENTEE-44) over native exports of generated schematics.

Native designs are emitted from original connectivity-intent/2 declarations
with the owned generation library, then judged only through the coordinator's
sandboxed kicad-cli netlist export and the shared strict XML reader.
"""
import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_net_names.

from agent_ee.baseline import freeze_evidence, load_baseline, validate_witnesses, verify_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.capabilities import describe_check
from agent_ee.checks import Ctx, CheckError, check_net_names, run_checks
from agent_ee.net_names import evaluate, validate_levels, validate_policy
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.storage import read_json, write_json
from test_schematic_emitter import captures, load, native_project, tables
from toolchain import require_toolchain

LAYOUT = {'schema':'agent-ee.schematic-layout/1', 'namespace':'0b6c1f0e-9d55-5e77-8a0e-6f2d3c4b5a69',
          'project':'names', 'transforms':{}}
REQUIREMENT = {'id':'names', 'source':{'path':'requirements.md', 'locator':'Net names'}}
CONTROL_LEVELS = {'VBUS':'required', 'SDA':'required', 'GND':'suggested', 'USB_P':'suggested',
                  'USB_N':'suggested', 'LOCAL':'suggested'}


def comp(sheet, cid, ref, lib='ASYM', value='ASYM'):
    return dict(sheet=sheet, id=cid, reference=ref, lib_id='OwnedGen:' + lib, value=value, footprint='', units=[1])


def net(sheet, nid, name, *terms):
    return dict(sheet=sheet, id=nid, name=name, terminals=[dict(component=c, pin=dict(number=p)) for c, p in terms])


def port(child, pid, name, parent_net, child_net):
    return dict(child=child, id=pid, name=name, direction='passive', parent_net=parent_net, child_net=child_net)


def intent(variant='control'):
    """Root connector and capacitor; children ``a`` and ``b`` each hold two four-pin parts.

    control: SDA joins both children through a root pass-through; VBUS reaches ``a``.
    split-sda: each child keeps its own local SDA, giving two native SDA nets.
    merged-5v: the port into ``a`` and its segment are named 5V, so one native net
    carries the parent VBUS label and the child 5V label.
    USB_P/USB_N repeat on both children in every variant (suggested collisions).
    """
    power = '5V' if variant == 'merged-5v' else 'VBUS'
    nets = [net('root', 'vbus', 'VBUS', ('j', '1'), ('c', '1')), net('root', 'gnd', 'GND', ('j', '3'), ('c', '2')),
            net('a', 'pwr', power, ('u1', '4'), ('u2', '4')), net('b', 'loc', 'LOCAL', ('u1', '4'), ('u2', '4'))]
    for child in ('a', 'b'):
        nets += [net(child, 'sda', 'SDA', ('u1', '1'), ('u2', '1')), net(child, 'p', 'USB_P', ('u1', '2'), ('u2', '2')),
                 net(child, 'n', 'USB_N', ('u1', '3'), ('u2', '3'))]
    ports = [port('a', 'pwr', power, 'vbus', 'pwr')]
    if variant != 'split-sda':
        nets.append(net('root', 'sda', 'SDA'))
        ports += [port(child, 'sda', 'SDA', 'sda', 'sda') for child in ('a', 'b')]
    return {'schema':'agent-ee.connectivity-intent/2', 'libraries':load('two_level.intent.json')['libraries'],
            'hierarchy':[dict(id='root', parent=None, local_id='root', file='names.kicad_sch'),
                         dict(id='a', parent='root', local_id='a', file='a.kicad_sch'),
                         dict(id='b', parent='root', local_id='b', file='b.kicad_sch')],
            'components':[comp('root', 'j', 'J1', 'CONN3', 'CONN3'), comp('root', 'c', 'C1', 'CAP', '10u'),
                          comp('a', 'u1', 'U1'), comp('a', 'u2', 'U2'), comp('b', 'u1', 'U3'), comp('b', 'u2', 'U4')],
            'nets':nets, 'no_connects':[dict(sheet='root', component='j', pin=dict(number='2'))], 'ports':ports}


def policy(*extra):
    required = [{'name':'VBUS', 'anchors':[{'reference':'J1', 'pin':'1'}]},
                {'name':'SDA', 'anchors':[{'reference':'U1', 'pin':'1'}, {'reference':'U3', 'pin':'1'}]}]
    return {'schema':'agent-ee.net-name-policy/1', 'requirement':copy.deepcopy(REQUIREMENT),
            'required':required + list(extra)}


def levels(**changes):
    values = dict(CONTROL_LEVELS, **changes)
    return {'schema':'agent-ee.net-name-levels/1', 'levels':{k: v for k, v in values.items() if v is not None}}


def graph(nets, components=None):
    """Synthetic strict-reader shape: nets {code: (name, terminals)}."""
    out = {'nets':{}, 'memberships':{}, 'components':{}}
    for code, (name, terminals) in nets.items():
        out['nets'][code] = {'name':name, 'terminals':tuple(terminals), 'locator':f'net {code}'}
        for ref, pin in terminals:
            out['memberships'][ref, pin] = code
            out['components'].setdefault(ref, {'pins':()})
            out['components'][ref]['pins'] += (pin,)
    out['components'].update(components or {})
    return out


def by_name(report):
    return {r['name']: r for r in report['records']}


class PureEvaluationTests(unittest.TestCase):
    def control(self):
        return graph({'1':('/VBUS', [('J1', '1'), ('U1', '4')]), '2':('/SDA', [('U1', '1'), ('U3', '1')]),
                      '3':('/GND', [('J1', '3'), ('C1', '2')]), '4':('/a/USB_P', [('U1', '2'), ('U2', '2')]),
                      '5':('/b/USB_P', [('U3', '2'), ('U4', '2')]), '6':('/a/USB_N', [('U1', '3'), ('U2', '3')]),
                      '7':('/b/USB_N', [('U3', '3'), ('U4', '3')]), '8':('/b/LOCAL', [('U3', '4'), ('U4', '4')]),
                      '9':('unconnected-(J1-P2-Pad2)', [('J1', '2')])})

    def test_control_passes_with_paired_suggested_disambiguation(self):
        report = evaluate(policy(), levels()['levels'], self.control())
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        records = by_name(report)
        self.assertEqual(report['disambiguated'], ['USB_N', 'USB_P'])
        self.assertEqual([p['proposed'] for p in records['USB_P']['disambiguation']], ['USB_1_P', 'USB_2_P'])
        self.assertEqual([p['proposed'] for p in records['USB_N']['disambiguation']], ['USB_1_N', 'USB_2_N'])
        self.assertEqual([p['name'] for p in records['USB_P']['disambiguation']], ['/a/USB_P', '/b/USB_P'])
        self.assertEqual((records['USB_P']['status'], records['USB_P']['severity']), ('informational', 'info'))
        self.assertEqual([n['name'] for n in report['unnamed']], ['unconnected-(J1-P2-Pad2)'])
        self.assertEqual({records[n]['status'] for n in ('VBUS', 'SDA', 'GND', 'LOCAL')}, {'satisfied'})

    def test_disambiguation_skips_existing_names_for_both_pair_halves(self):
        nets = self.control()
        nets['nets']['10'] = {'name':'/USB_1_N', 'terminals':(), 'locator':'net 10'}
        report = evaluate(policy(), levels(USB_1_N='suggested')['levels'], nets)
        records = by_name(report)
        self.assertEqual([p['proposed'] for p in records['USB_P']['disambiguation']], ['USB_2_P', 'USB_3_P'])
        self.assertEqual([p['proposed'] for p in records['USB_N']['disambiguation']], ['USB_2_N', 'USB_3_N'])
        plain = graph({'1':('/a/LED', []), '2':('/b/LED', []), '3':('/c/LED', []), '4':('/LED_2', [])})
        report = evaluate({**policy(), 'required':[]}, {'LED':'suggested', 'LED_2':'suggested'}, plain)
        self.assertEqual([p['proposed'] for p in by_name(report)['LED']['disambiguation']],
                         ['LED_1', 'LED_3', 'LED_4'])
        self.assertEqual(report['outcome'], 'pass')

    def test_required_duplicate_split_conflict_and_level_defects(self):
        split = self.control()
        split['nets']['2'] = {'name':'/a/SDA', 'terminals':(('U1', '1'),), 'locator':'net 2'}
        split['nets']['10'] = {'name':'/b/SDA', 'terminals':(('U3', '1'),), 'locator':'net 10'}
        split['memberships']['U3', '1'] = '10'
        report = evaluate(policy(), levels()['levels'], split)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
        self.assertEqual(by_name(report)['SDA']['violations'], ['required_duplicate', 'required_split'])
        self.assertEqual(by_name(report)['SDA']['witness']['net_ids'], ['2', '10'])
        merged = policy({'name':'5V', 'anchors':[{'reference':'U1', 'pin':'4'}]})
        report = evaluate(merged, levels(**{'5V':'required'})['levels'], self.control())
        records = by_name(report)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(records['VBUS']['violations'], ['required_conflict'])
        self.assertEqual(records['VBUS']['conflicts'], [{'name':'5V', 'net_ids':['1'], 'net_names':['/VBUS']}])
        self.assertEqual(records['5V']['violations'], ['required_absent', 'required_displaced', 'required_conflict'])
        for changes, name, code in (({'SDA':'suggested'}, 'SDA', 'level_downgraded'),
                                    ({'GND':'required'}, 'GND', 'required_unsupported'),
                                    ({'EXTRA':'required'}, 'EXTRA', 'required_unsupported')):
            with self.subTest(changes=changes):
                report = evaluate(policy(), levels(**changes)['levels'], self.control())
                self.assertEqual(report['outcome'], 'defect')
                self.assertEqual(by_name(report)[name]['violations'], [code])
        moved = policy()
        moved['required'][0]['anchors'] = [{'reference':'J1', 'pin':'3'}]
        report = evaluate(moved, levels()['levels'], self.control())
        self.assertEqual(by_name(report)['VBUS']['violations'], ['required_displaced'])

    def test_missing_level_or_unresolved_anchor_is_unknown_never_pass(self):
        for changes in ({'LOCAL':None}, {'VBUS':None}):
            with self.subTest(changes=changes):
                report = evaluate(policy(), levels(**changes)['levels'], self.control())
                self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
                name = next(iter(changes))
                self.assertEqual(by_name(report)[name]['unresolved'], ['level_missing'])
                self.assertEqual(by_name(report)[name]['status'], 'unknown')
        for anchor in ({'reference':'U9', 'pin':'1'}, {'reference':'U1', 'pin':'9'}):
            unresolved = policy()
            unresolved['required'][1]['anchors'].append(anchor)
            report = evaluate(unresolved, levels()['levels'], self.control())
            self.assertEqual(report['outcome'], 'unknown')
            self.assertEqual(by_name(report)['SDA']['unresolved'], ['anchor_unresolved'])
        # A missing annotation keeps the aggregate unknown even beside a known defect.
        report = evaluate(policy(), levels(LOCAL=None, SDA='suggested')['levels'], self.control())
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(by_name(report)['SDA']['status'], 'violation')
        # Generated reserved labels are annotatable; other non-generated names are not.
        reserved = '__agent_ee_' + '0' * 64
        nets = graph({'1':('/a/' + reserved, []), '2':('/odd name', [])})
        report = evaluate({**policy(), 'required':[]}, validate_levels(
            {'schema':'agent-ee.net-name-levels/1', 'levels':{reserved:'suggested'}}), nets)
        self.assertEqual(by_name(report)[reserved]['status'], 'satisfied')
        self.assertEqual(by_name(report)['odd name']['unresolved'], ['level_missing'])

    def test_policy_and_level_shapes_are_strict(self):
        for mutation in (
            lambda p: p['required'].append(copy.deepcopy(p['required'][0])),
            lambda p: p['required'][0].update(anchors=[]),
            lambda p: p['required'][0].update(name='V BUS'),
            lambda p: p['required'][0].update(name='/VBUS'),
            lambda p: p['required'][1]['anchors'].append({'reference':'J1', 'pin':'1'}),
            lambda p: p['required'][0]['anchors'][0].update(sheet='/'),
            lambda p: p.update(schema='agent-ee.net-name-policy/2'),
            lambda p: p.pop('requirement'),
        ):
            value = policy()
            mutation(value)
            with self.subTest(policy=value), self.assertRaises((ValueError, TypeError)):
                validate_policy(value)
        for value in ({'schema':'agent-ee.net-name-levels/1', 'levels':{'SDA':'preferred'}},
                      {'schema':'agent-ee.net-name-levels/1', 'levels':{'SDA':None}},
                      {'schema':'agent-ee.net-name-levels/1', 'levels':['SDA']},
                      {'schema':'agent-ee.net-name-levels/1', 'levels':{'a/b':'suggested'}},
                      {'schema':'agent-ee.net-name-levels/1'}):
            with self.subTest(levels=value), self.assertRaises((ValueError, TypeError)):
                validate_levels(value)


class NativeNetNameTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def run_variant(self, variant='control', name_policy=None, name_levels=None):
        self.counter += 1
        root = self.root / str(self.counter)
        result, files = emit_schematic(intent(variant), captures(intent(variant)), LAYOUT, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = native_project(files, root)
        (root / 'requirements.md').write_text('Net names: owner-declared names VBUS and SDA must survive generation.\n')
        write_json(root / 'policy.json', name_policy or policy())
        write_json(root / 'levels.json', name_levels or levels())
        scope = {'schema':'agent-ee.scope/3', 'schematic':'project/names.kicad_sch',
            'inputs':[{'path':str(project), 'at':'project'}] + [{'path':str(root / p), 'at':p}
                      for p in ('requirements.md', 'policy.json', 'levels.json')],
            'requirements':['requirements.md'],
            'checks':[{'id':'netlist', 'kind':'netlist'}, {'id':'names', 'kind':'net_names',
                'params':{'netlist_check':'netlist', 'policy':'policy.json', 'levels':'levels.json'}}],
            'baseline':{'requirements':[{'id':'names', 'statement':'Owner net names survive generation',
                'source':copy.deepcopy(REQUIREMENT['source']), 'checks':['netlist', 'names'],
                'intent':'acceptance'}], 'targets':['requirements.md'],
                'reference_architectures':['requirements.md']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 120)
        self.assertEqual(checks[0]['execution'], 'ok', checks[0])
        return run, checks

    def rerun(self, run):
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        try:
            check_net_names(ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return ctx.result

    def test_generated_control_passes_and_reports_paired_disambiguation(self):
        run, checks = self.run_variant()
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        records = by_name(result['details'])
        self.assertEqual([n['name'] for n in records['SDA']['native_nets']], ['/SDA'])
        self.assertEqual({a['net_name'] for a in records['SDA']['anchors']}, {'/SDA'})
        self.assertEqual([n['name'] for n in records['VBUS']['native_nets']], ['/VBUS'])
        for name, proposals in (('USB_P', ['USB_1_P', 'USB_2_P']), ('USB_N', ['USB_1_N', 'USB_2_N'])):
            self.assertEqual(records[name]['severity'], 'info')
            self.assertEqual([p['proposed'] for p in records[name]['disambiguation']], proposals)
            self.assertEqual([p['name'] for p in records[name]['disambiguation']],
                             ['/a/' + name, '/b/' + name])
        report = read_json(run / 'work/names/net_names.json')
        self.assertEqual(report['records'], result['details']['records'])
        self.assertIn('work/names/net_names.json', result['evidence'])
        self.assertIn('work/netlist/design.xml', result['evidence'])
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)

    def test_two_native_sda_nets_at_required_level_are_a_defect(self):
        run, checks = self.run_variant('split-sda')
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        sda = by_name(result['details'])['SDA']
        self.assertEqual(sda['violations'], ['required_duplicate', 'required_split'])
        self.assertEqual([n['name'] for n in sda['native_nets']], ['/a/SDA', '/b/SDA'])
        self.assertEqual([a['net_name'] for a in sda['anchors']], ['/a/SDA', '/b/SDA'])
        # The same split at the suggested level is only disambiguated.
        _, checks = self.run_variant('split-sda', {**policy(), 'required':policy()['required'][:1]},
                                     levels(SDA='suggested'))
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual([p['proposed'] for p in by_name(result['details'])['SDA']['disambiguation']],
                         ['SDA_1', 'SDA_2'])

    def test_one_native_net_carrying_required_vbus_and_5v_is_a_defect(self):
        five = {'name':'5V', 'anchors':[{'reference':'U1', 'pin':'4'}, {'reference':'U2', 'pin':'4'}]}
        run, checks = self.run_variant('merged-5v', policy(five), levels(**{'5V':'required'}))
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        records = by_name(result['details'])
        self.assertIn('required_conflict', records['VBUS']['violations'])
        self.assertIn('required_conflict', records['5V']['violations'])
        self.assertEqual(records['VBUS']['conflicts'][0]['name'], '5V')
        # KiCad keeps one of the two labels; the other required name is lost.
        self.assertEqual(records['VBUS']['conflicts'][0]['net_names'], ['/VBUS'])
        self.assertIn('required_absent', records['5V']['violations'])
        freeze_evidence(run, checks)
        witness = [{'requirement_id':'names', 'check_id':'names'}]
        validate_witnesses(run, witness, [{'path':'checks/names.json', 'locator':'VBUS/5V required_conflict'}])
        control, control_checks = self.run_variant()
        freeze_evidence(control, control_checks)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(control, witness, [{'path':'checks/names.json', 'locator':'VBUS'}])

    def test_missing_level_annotation_is_unknown(self):
        _, checks = self.run_variant(name_levels=levels(LOCAL=None))
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertEqual(by_name(result['details'])['LOCAL']['unresolved'], ['level_missing'])

    def test_capability_baseline_and_malformed_or_failed_inputs(self):
        run, checks = self.run_variant()
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        descriptor = describe_check(scope['checks'][1], check_specs=scope['checks'],
                                    files=manifest['files'], scope=scope, inputs=run / 'inputs')
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(descriptor['predicates'], ['net-names:levels', 'net-names:required:SDA',
                                                    'net-names:required:VBUS'])
        self.assertEqual(descriptor['immutable_inputs'], ['policy.json'])
        baseline = load_baseline(run)
        self.assertIn('policy.json', baseline['files'])
        self.assertNotIn('levels.json', baseline['files'])
        levels_path = run / 'inputs/levels.json'
        raw = levels_path.read_bytes()
        for replacement in (b'{', b'{"schema":"agent-ee.net-name-levels/1","levels":{"SDA":"x"}}'):
            levels_path.write_bytes(replacement)
            result = self.rerun(run)
            self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        levels_path.write_bytes(raw)
        xml = run / 'work/netlist/design.xml'
        original = xml.read_bytes()
        xml.write_bytes(original.replace(b'/a/USB_P', b'/a/USB_X', 1))
        result = self.rerun(run)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        xml.write_bytes(original)
        prior = run / 'checks/netlist.json'
        record = read_json(prior)
        write_json(prior, {**record, 'execution':'failed'})
        result = self.rerun(run)
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        write_json(prior, record)
        self.assertEqual(self.rerun(run)['outcome'], 'pass')
        for rel in ('policy.json', 'levels.json'):
            path = run / 'inputs' / rel
            raw = path.read_bytes()
            path.write_bytes(raw + b' ')
            with self.subTest(path=rel), self.assertRaises(ValueError):
                verify_snapshot(run)
            path.write_bytes(raw)
        freeze_evidence(run, checks)
        report = run / 'work/names/net_names.json'
        raw = report.read_bytes()
        report.write_bytes(raw + b' ')
        with self.assertRaises(ValueError):
            verify_evidence(run, checks, required=True)
        report.write_bytes(raw)

    def test_unmapped_policy_or_shared_policy_and_levels_cannot_be_captured(self):
        unmapped = policy()
        unmapped['requirement']['id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'requirement source and check mapping'):
            self.run_variant(name_policy=unmapped)
        with self.assertRaisesRegex(ValueError, 'net-name policy|expected fields'):
            self.run_variant(name_policy=levels())


if __name__ == '__main__':
    unittest.main()
