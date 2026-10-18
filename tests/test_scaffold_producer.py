"""Stage-two scaffold producer over original synthetic envelope, profile and parts.

The fab profile values are owned test assumptions, not any real fabricator's
limits. Native tools and seals are real; nothing here establishes owner approval.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from agent_ee.candidate import capture
from agent_ee.scaffold import scaffold
from agent_ee.storage import digest, read_json, write_json
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import ROOT, ScaffoldFixture
from toolchain import require_toolchain

TEMPLATE = ROOT / 'fixtures/netclass-completeness/native.kicad_pro'
LIMITS = [('min_clearance_mm', 0.15, 'minimum'), ('min_trace_mm', 0.127, 'minimum'),
          ('min_trace_mm', 0.2, 'preferred'), ('min_via_pad_mm', 0.45, 'minimum'),
          ('min_via_drill_mm', 0.25, 'minimum'), ('min_edge_clearance_mm', 0.3, 'minimum'),
          ('min_hole_to_hole_mm', 0.5, 'minimum'), ('max_layers', 2, 'minimum'),
          ('max_board_mm', [100, 100], 'minimum'), ('min_clearance_mm', 0.2, 'preferred'),
          ('min_via_pad_mm', 0.6, 'preferred'), ('min_via_drill_mm', 0.3, 'preferred')]


class ProducerFixture(ScaffoldFixture):
    def setUp(self):
        super().setUp()
        # The producer derives the project file; stage one's hand-written rules are removed.
        (self.source / 'project/design.kicad_pro').unlink()
        (self.source / 'templates').mkdir()
        shutil.copyfile(TEMPLATE, self.source / 'templates/native.kicad_pro')
        (self.source / 'native.md').write_text('Owned synthetic requirement: native ERC and DRC pass.\n')
        self.limits = copy.deepcopy(LIMITS)
        self.plan = dict(schema='agent-ee.scaffold-plan/1', name='producer contract',
            scope='Native validity, parts membership and derived fab rules for an original synthetic RC fixture.',
            limitations=['Owned synthetic fab assumptions; no real fabricator capability is claimed.'],
            inputs=[], project='project/design', template='templates/native.kicad_pro',
            fab_profile='fab/profile.json',
            native_requirement=dict(id='native', statement='Native ERC and DRC pass.',
                                    source=dict(path='native.md', locator='line 1')),
            targets=['targets.md'], reference_architectures=['reference.md'], finding_retry_limit=3)
        self.plan_path = self.root / 'planning.plan.json'
        self.write()

    def profile(self):
        (self.source / 'fab').mkdir(exist_ok=True)
        text = 'Owned synthetic capability assumptions, 2026-10-17.\n' + ''.join(
            f'{key}: {json.dumps(value)} ; {tier}\n' for key, value, tier in self.limits)
        (self.source / 'fab/owned-capabilities.txt').write_text(text)
        limits = []
        for index, (key, value, tier) in enumerate(self.limits):
            unit = 'count' if key == 'max_layers' else 'mm'
            limits.append(dict(id=f'limit-{index}', requirement='fabrication', key=key, value=value,
                unit=unit, tier=tier, source=dict(path='fab/owned-capabilities.txt',
                    sha256=digest(self.source / 'fab/owned-capabilities.txt'),
                    source='Owned synthetic assumptions (not a real fab)', as_of='2026-10-17', page=1,
                    quote=f'{key}: {json.dumps(value)} ; {tier}')))
        return dict(schema='agent-ee.fab-profile/1', name='owned_standard_2026-10', as_of='2026-10-17',
                    limits=limits)

    def write(self, profile=None):
        profile = self.profile() if profile is None else profile
        write_json(self.source / 'fab/profile.json', profile)
        if not getattr(self, 'keep_null', False):
            self.envelope['fab_profile'] = dict(name=profile['name'],
                                                sha256=digest(self.source / 'fab/profile.json'))
        write_json(self.source / 'envelope.json', self.envelope)
        self.plan['inputs'] = [dict(path='source/' + p.relative_to(self.source).as_posix(),
            at=p.relative_to(self.source).as_posix()) for p in sorted(self.source.rglob('*')) if p.is_file()]
        write_json(self.plan_path, self.plan)

    def build(self, name='produced'):
        return scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.root / name,
                        plan_path=self.plan_path)

    def refused(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.build()
        self.assertFalse((self.root / 'produced').exists())


class ProducerTests(ProducerFixture, unittest.TestCase):
    def test_derives_checks_mappings_and_rules_byte_identically(self):
        first, second = self.build('a'), self.build('b')
        inventories = [{p.relative_to(self.root / name).as_posix(): p.read_bytes()
                        for p in (self.root / name).rglob('*') if p.is_file()} for name in ('a', 'b')]
        self.assertEqual(inventories[0], inventories[1])
        self.assertEqual(first['schema'], 'agent-ee.scaffold-receipt/2')
        self.assertEqual(first['inputs']['plan_sha256'], digest(self.plan_path))
        scope = read_json(self.root / 'a/scope.json')
        self.assertEqual(scope['schema'], 'agent-ee.scope/2')
        self.assertEqual([(c['id'], c['kind'], c['stage']) for c in scope['checks']], [
            ('erc', 'erc', 'schematic'), ('netlist', 'netlist', 'schematic'), ('parts', 'parts_subset', 'schematic'),
            ('drc', 'drc', 'placement'), ('fab', 'fab_profile', 'routing')])
        self.assertEqual(scope['checks'][2]['params'], dict(manifest='parts.json', netlist_check='netlist',
                                                            requirement_id='parts-membership'))
        self.assertEqual(scope['checks'][4]['params'], dict(fab_profile='fab/profile.json'))
        mapped = {r['id']: (r['source'], r['checks']) for r in scope['baseline']['requirements']}
        self.assertEqual(mapped, {
            'native': (dict(path='native.md', locator='line 1'), ['erc', 'drc']),
            'parts-membership': ({k: self.parts['declaration_source'][k] for k in ('path', 'locator')},
                                 ['netlist', 'parts']),
            'fabrication': (dict(path='fab/profile.json', locator='/limits'), ['fab'])})
        self.assertEqual(scope['baseline']['targets'], ['targets.md'])
        self.assertNotIn('project/design.kicad_pro', scope['mutable_inputs'])
        # Expected inventory is exactly the manifest's required selectors.
        self.assertEqual(scope['stages'], dict(schema='agent-ee.stages/1', order=['schematic', 'placement', 'routing'],
            subjects=dict(schematic=dict(path='project/design.kicad_sch', kind='symbols', references=['C1', 'J1', 'R1']),
                          placement=dict(path='project/design.kicad_pcb', kind='footprints', references=['C1', 'J1', 'R1']),
                          routing=dict(path='project/design.kicad_pcb', kind='tracks', minimum=1)),
            finding_retry_limit=3))
        self.assertEqual(first['derivation']['stages']['status'], 'derived')
        pro = read_json(self.root / 'a/inputs/project/design.kicad_pro')
        self.assertEqual(pro['board']['design_settings']['rules'], dict(
            min_clearance=0.15, min_track_width=0.127, min_via_diameter=0.45, min_through_hole_diameter=0.25,
            min_copper_edge_clearance=0.3, min_hole_to_hole=0.5))
        default, = pro['net_settings']['classes']
        template = read_json(TEMPLATE)['net_settings']['classes'][0]
        # KiCad DRC enforces the class clearance, so it is the fab minimum (owner decision
        # 2026-10-18). Preferred trace and via avoid the surcharge as routing defaults only.
        self.assertEqual(default, {**template, 'clearance': 0.15, 'track_width': 0.2,
                                   'via_diameter': 0.6, 'via_drill': 0.3})
        derivation = first['derivation']
        self.assertEqual([(r['setting'], r['value'], r['tier'], r['limit_id'], r['locator'])
                          for r in derivation['netclasses']], [
            ('net_settings.classes.Default.clearance', 0.15, 'minimum', 'limit-0', '/limits/0'),
            ('net_settings.classes.Default.track_width', 0.2, 'preferred', 'limit-2', '/limits/2'),
            ('net_settings.classes.Default.via_diameter', 0.6, 'preferred', 'limit-10', '/limits/10'),
            ('net_settings.classes.Default.via_drill', 0.3, 'preferred', 'limit-11', '/limits/11')])
        bases = [r['basis'] for r in derivation['netclasses']]
        self.assertTrue(bases[0].startswith('KiCad DRC enforces the Default class value as a hard floor'))
        self.assertTrue(all(b.startswith('routing default only') for b in bases[1:]))
        self.assertTrue(all('owner decision 2026-10-18' in b for b in bases))
        self.assertTrue(all(r['tier'] == 'minimum' for r in derivation['rules']))
        # Every preferred value is a router target; the clearance target is not a DRC floor.
        self.assertEqual([(r['setting'], r['value'], r['tier'], r['limit_id'])
                          for r in derivation['router_targets']], [
            (None, 0.2, 'preferred', 'limit-9'),
            ('net_settings.classes.Default.track_width', 0.2, 'preferred', 'limit-2'),
            ('net_settings.classes.Default.via_diameter', 0.6, 'preferred', 'limit-10'),
            ('net_settings.classes.Default.via_drill', 0.3, 'preferred', 'limit-11')])
        self.assertIn('pinned kicad-route adapter passes no clearance target',
                      derivation['router_targets'][0]['consumer'])
        self.assertEqual(derivation['envelope']['layer_count'], 2)
        self.assertEqual(derivation['fab_profile']['sha256'], self.envelope['fab_profile']['sha256'])
        self.assertEqual(derivation['template']['sha256'], digest(TEMPLATE))
        self.assertEqual({s['predicate'] for s in derivation['envelope']['profile_support']},
                         {'fab_profile_supports_layer_count', 'fab_profile_supports_board_size'})
        for path, pin in first['outputs'].items():
            self.assertEqual(pin, digest(self.root / 'a' / path))
        run = self.root / 'capture'
        run.mkdir()
        manifest = capture(self.root / 'a/scope.json', run)
        self.assertEqual(manifest['candidate_id'], first['candidate_id'])
        baseline = read_json(run / 'baseline.json')
        self.assertEqual(baseline['baseline_id'], first['baseline_id'])
        # Derived rules, the profile and its source are verification criteria a repair cannot relax.
        for path in ('project/design.kicad_pro', 'fab/profile.json', 'fab/owned-capabilities.txt',
                     'parts.json', 'native.md'):
            self.assertIn(path, baseline['files'])
        # The template is provenance only; it stays outside mutable inputs.
        self.assertIn('templates/native.kicad_pro', manifest['files'])
        self.assertNotIn('templates/native.kicad_pro', manifest['scope']['mutable_inputs'])

    def test_changed_profile_limit_changes_only_through_inputs(self):
        before = self.build('a')
        self.limits[0] = ('min_clearance_mm', 0.2, 'minimum')
        self.write()
        after = self.build('b')
        self.assertNotEqual(before['candidate_id'], after['candidate_id'])
        rules = read_json(self.root / 'b/inputs/project/design.kicad_pro')['board']['design_settings']['rules']
        self.assertEqual(rules['min_clearance'], 0.2)
        self.assertEqual(after['derivation']['rules'][0]['value'], 0.2)
        default, = read_json(self.root / 'b/inputs/project/design.kicad_pro')['net_settings']['classes']
        self.assertEqual(default['clearance'], 0.2)
        self.assertEqual(after['derivation']['netclasses'][0]['limit_id'], 'limit-0')

    def test_preferred_clearance_changes_only_the_router_target(self):
        self.build('a')
        self.limits[9] = ('min_clearance_mm', 0.3, 'preferred')
        self.write()
        after = self.build('b')
        # No DRC-enforced value moves with the preferred tier; only the input-derived root UUID differs.
        before, now = (read_json(self.root / name / 'inputs/project/design.kicad_pro') for name in ('a', 'b'))
        self.assertNotEqual(before.pop('sheets'), now.pop('sheets'))
        self.assertNotEqual(before['schematic'].pop('top_level_sheets'), now['schematic'].pop('top_level_sheets'))
        self.assertEqual(before, now)
        self.assertEqual(after['derivation']['router_targets'][0]['value'], 0.3)

    def test_profile_without_preferred_limits_uses_minimum_everywhere(self):
        self.limits = [limit for limit in LIMITS if limit[2] == 'minimum']
        self.write()
        derivation = self.build()['derivation']
        default, = read_json(self.root / 'produced/inputs/project/design.kicad_pro')['net_settings']['classes']
        self.assertEqual({k: default[k] for k in ('clearance', 'track_width', 'via_diameter', 'via_drill')},
                         dict(clearance=0.15, track_width=0.127, via_diameter=0.45, via_drill=0.25))
        self.assertEqual({r['tier'] for r in derivation['netclasses']}, {'minimum'})
        self.assertTrue(all(r['basis'].startswith('no preferred limit')
                            for r in derivation['netclasses'][1:]))
        self.assertEqual(derivation['router_targets'], [])

    def test_envelope_without_profile_is_refused_by_name(self):
        self.keep_null = True
        self.envelope['fab_profile'] = None
        self.write()
        self.refused('envelope is incomplete: fab_profile is null')

    def test_profile_bytes_differing_from_envelope_reference_are_refused(self):
        self.write()
        profile = read_json(self.source / 'fab/profile.json')
        profile['as_of'] = '2026-10-16'
        write_json(self.source / 'fab/profile.json', profile)
        self.refused('do not match the referenced sha256')

    def test_unresolved_layer_support_is_refused(self):
        self.limits = [limit for limit in self.limits if limit[0] != 'max_layers']
        self.write()
        self.refused('unresolved fab_profile_supports_layer_count')

    def test_missing_rule_limit_is_refused_by_key(self):
        self.limits = [limit for limit in self.limits if limit[0] != 'min_hole_to_hole_mm']
        self.write()
        self.refused('no minimum-tier limit for min_hole_to_hole_mm')

    def test_unverified_limit_source_is_refused(self):
        profile = self.profile()
        profile['limits'][1]['source']['quote'] = 'min_trace_mm: 0.1 ; minimum'
        self.write(profile)
        self.refused(r'limits not source-verified: limit-1 \(exact source quote absent')

    def test_preferred_below_minimum_and_degenerate_via_are_refused(self):
        self.limits[2] = ('min_trace_mm', 0.1, 'preferred')
        self.write()
        self.refused('min_trace_mm preferred value is below its minimum')
        # The clearance preferred tier is only a router target, but stays bounded by the minimum.
        self.limits = copy.deepcopy(LIMITS)
        self.limits[9] = ('min_clearance_mm', 0.1, 'preferred')
        self.write()
        self.refused('min_clearance_mm preferred value is below its minimum')
        self.limits = copy.deepcopy(LIMITS)
        self.limits[11] = ('min_via_drill_mm', 0.6, 'preferred')
        self.write()
        self.refused('via pad must exceed via drill')
        self.limits = [('min_via_pad_mm', 0.25, 'minimum') if limit[0] == 'min_via_pad_mm' else limit
                       for limit in LIMITS if limit[2] == 'minimum']
        self.write()
        self.refused('via pad must exceed via drill')

    def test_profile_with_two_requirements_is_refused(self):
        profile = self.profile()
        profile['limits'][0]['requirement'] = 'other'
        self.write(profile)
        self.refused('exactly one requirement, got fabrication, other')

    def test_incomplete_parts_list_is_refused(self):
        self.parts['parts'][0]['datasheet'] = dict(status='unavailable', required=True, reason='not captured')
        write_json(self.source / 'parts.json', self.parts)
        self.write()
        self.refused('parts manifest is incomplete: required datasheet provenance unavailable for resistor')

    def test_fixed_footprint_outside_parts_list_is_refused(self):
        self.envelope['fixed_footprints'][0]['reference'] = 'J9'
        self.write()
        self.refused('envelope fixed footprints are not declared parts instances: J9')

    def test_captured_project_rules_are_refused(self):
        for name in ('design.kicad_pro', 'design.kicad_dru'):
            with self.subTest(name=name):
                path = self.source / 'project' / name
                path.write_text('{}')
                self.write()
                self.refused('derives project rules; refusing captured project/' + name)
                path.unlink()

    def test_template_rule_authority_is_refused(self):
        native = read_json(TEMPLATE)
        cases = {
            'beyond Default need AGENTEE-47': lambda t: t['net_settings']['classes'].append(
                {**t['net_settings']['classes'][0], 'name': 'Power'}),
            'beyond Default need AGENTEE-47 ': lambda t: t['net_settings']['netclass_patterns'].append(
                dict(netclass='Default', pattern='*')),
            'ignore derived rule severities.*clearance': lambda t: t['board'].update(
                design_settings=dict(rule_severities=dict(clearance='ignore'))),
            'exclude DRC findings: drc_exclusions': lambda t: t['board'].update(
                design_settings=dict(drc_exclusions=[['clearance|1|2', '']])),
            'lacks native fields: via_drill': lambda t: t['net_settings']['classes'][0].pop('via_drill'),
            'not the qualified kicad-10.0.7-net-settings-5': lambda t: t['net_settings']['meta'].update(version=4)}
        for pattern, mutate in cases.items():
            with self.subTest(pattern=pattern):
                template = copy.deepcopy(native)
                mutate(template)
                write_json(self.source / 'templates/native.kicad_pro', template)
                self.write()
                self.refused(pattern.strip())

    def test_plan_shape_and_mode_are_exact(self):
        with self.assertRaisesRegex(ValueError, 'exactly one of a stage-one scope or a producer plan'):
            scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.root / 'produced')
        with self.assertRaisesRegex(ValueError, 'exactly one of a stage-one scope or a producer plan'):
            scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.root / 'produced',
                     scope_path=self.plan_path, plan_path=self.plan_path)
        del self.plan['targets']
        self.plan['checks'] = []
        write_json(self.plan_path, self.plan)
        self.refused(r"missing \['targets'\], unknown \['checks'\]")
        self.setUp()
        self.plan['native_requirement']['id'] = 'parts-membership'
        write_json(self.plan_path, self.plan)
        self.refused('derived requirement IDs collide')
        self.setUp()
        self.plan['project'] = 'project/design.kicad_sch'
        write_json(self.plan_path, self.plan)
        self.refused('basename path without suffix')

    def test_hierarchical_required_selectors_leave_stages_underived_by_name(self):
        import uuid
        child = str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-producer-child'))
        self.parts['parts'][1]['instances'][0]['sheet'] = '/' + child + '/'
        write_json(self.source / 'parts.json', self.parts)
        self.write()
        receipt = self.build()
        scope = read_json(self.root / 'produced/scope.json')
        self.assertNotIn('stages', scope)
        self.assertTrue(all('stage' not in c for c in scope['checks']))
        self.assertEqual(receipt['derivation']['stages']['status'], 'not_derived')
        self.assertIn('child sheets: C1', receipt['derivation']['stages']['reason'])
        self.assertIn('/' + child + '/', receipt['sheets'])

    def test_retry_limit_below_two_is_refused(self):
        self.plan['finding_retry_limit'] = 1
        write_json(self.plan_path, self.plan)
        self.refused('finding_retry_limit must be an integer from 2 to 100')

    def test_uncaptured_template_is_refused_before_output(self):
        self.plan['template'] = 'templates/absent.kicad_pro'
        write_json(self.plan_path, self.plan)
        self.refused('producer plan template is not captured')


class NativeProducerTests(ProducerFixture, unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        super().setUp()

    def test_cli_produce_and_review_with_native_rules_loaded(self):
        proc = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'scaffold', str(self.source / 'envelope.json'),
            str(self.source / 'parts.json'), '--plan', str(self.plan_path), '--out', str(self.root / 'cli')],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = read_json(self.root / 'cli/scaffold-receipt.json')
        self.assertEqual(proc.stdout.strip(), receipt['candidate_id'])
        from agent_ee.executor import execute
        work = self.root / 'rules-probe'
        script = ('import json, pcbnew; b=pcbnew.LoadBoard("/candidate/project/design.kicad_pcb"); '
                  's=b.GetDesignSettings(); d=s.m_NetSettings.GetDefaultNetclass(); '
                  'print(json.dumps(dict(clearance=s.m_MinClearance, track=s.m_TrackMinWidth, '
                  'via=s.m_ViasMinSize, drill=s.m_MinThroughDrill, edge=s.m_CopperEdgeClearance, '
                  'hole=s.m_HoleToHoleMin, layers=b.GetCopperLayerCount(), default=[d.GetClearance(), '
                  'd.GetTrackWidth(), d.GetViaDiameter(), d.GetViaDrill()])))')
        result = execute(self.root / 'cli/inputs', work, ['/usr/bin/python3', '-c', script], timeout=60)
        self.assertEqual(result['execution'], 'ok', result)
        native = json.loads((work / 'stdout.log').read_text())
        # pcbnew loads the derived project rules (nanometres), not its built-in defaults.
        self.assertEqual(native, dict(clearance=150000, track=127000, via=450000, drill=250000, edge=300000,
                                      hole=500000, layers=2, default=[150000, 200000, 600000, 300000]))
        proc = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'review', str(self.root / 'cli/scope.json'),
            '--out', str(self.root / 'native'), '--checks-only'], capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 1, proc.stderr)  # No independent review was requested.
        from agent_ee.cli import verify
        verified = verify(self.root / 'native')
        self.assertEqual(verified['candidate_id'], receipt['candidate_id'])
        # Every derived stage observes its absent declared content natively: a named
        # repairable defect, never an unknown netlist or a fab check without a loading witness.
        self.assertEqual([(c['id'], c['execution'], c['outcome'], c['details']['reason'])
                          for c in verified['checks']],
                         [(cid, 'ok', 'defect', 'stage_not_reached')
                          for cid in ('erc', 'netlist', 'parts', 'drc', 'fab')])
        from agent_ee.stages import projection
        self.assertEqual(projection(verified['checks'], read_json(self.root / 'cli/scope.json'))
                         ['earliest_failing_stage'], 'schematic')
    def test_gap_between_fab_minimum_and_preferred_clearance_passes_native_drc(self):
        from agent_ee.executor import execute
        self.build()
        produced = self.root / 'produced/inputs'
        # Two tracks on different nets; KiCad reports the edge-to-edge gap it measures.
        script = ('import sys, pcbnew as k; mm=lambda v: int(round(v*1e6)); '
                  'b=k.LoadBoard("/candidate/project/design.kicad_pcb"); gap=float(sys.argv[1])\n'
                  'for name, y in (("A", 10.0), ("B", 10.0 + 0.2 + gap)):\n'
                  ' n=k.NETINFO_ITEM(b, name); b.Add(n); t=k.PCB_TRACK(b); t.SetNet(n); t.SetLayer(k.F_Cu)\n'
                  ' t.SetStart(k.VECTOR2I(mm(4), mm(y))); t.SetEnd(k.VECTOR2I(mm(12), mm(y))); '
                  't.SetWidth(mm(0.2)); b.Add(t)\n'
                  'k.SaveBoard("/work/design.kicad_pcb", b)')

        def drc(label, gap, clearance=None):
            candidate = self.root / label
            shutil.copytree(produced, candidate)
            work = self.root / (label + '-board')
            made = execute(candidate, work, ['/usr/bin/python3', '-c', script, str(gap)], timeout=60)
            self.assertEqual(made['execution'], 'ok', made)
            shutil.copyfile(work / 'design.kicad_pcb', candidate / 'project/design.kicad_pcb')
            if clearance is not None:
                pro = read_json(candidate / 'project/design.kicad_pro')
                pro['net_settings']['classes'][0]['clearance'] = clearance
                write_json(candidate / 'project/design.kicad_pro', pro)
            work = self.root / (label + '-drc')
            record = execute(candidate, work, ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all',
                '--units', 'mm', '-o', '/work/drc.json', '/candidate/project/design.kicad_pcb'], timeout=120)
            self.assertEqual(record['execution'], 'ok', record)
            return sorted(v['type'] for v in read_json(work / 'drc.json')['violations'])

        # 0.175 mm lies between the fab minimum (0.15) and the preferred clearance (0.2).
        produced_codes = drc('between', 0.175)
        self.assertNotIn('clearance', produced_codes)
        self.assertEqual(set(produced_codes), {'track_dangling'})
        # Control: the preferred clearance as the class value would fail the same board.
        self.assertEqual(drc('preferred-class', 0.175, clearance=0.2).count('clearance'), 1)
        # The fab minimum remains a hard floor.
        self.assertEqual(drc('below-minimum', 0.1).count('clearance'), 1)

if __name__ == '__main__':
    unittest.main()
