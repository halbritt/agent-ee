"""AGENTEE-179: the placer and adapter honour the scaffold's envelope fixed footprints.

The pod probe board is AGENTEE-177's scaffold output with original stand-ins:
J1 for the AMPSEAL header, J2 for the SMA, U1 for the IMU, U2 for the radio and
four corner holes. The stand-ins receive authored courtyards here; no vendor CAD
bytes enter the tree. Native tools, the sandbox and the registered checks are
real; this is generator mechanics, not part suitability. The generic fixture's
board keepout is its ordinary explicit polygon; the radio's antenna zone comes
only from the explicit captured-CAD retention input (AGENTEE-211), never from a
derived or authored polygon.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.test_scaffold import EnvelopeScaffoldFixture
from tests.test_scaffold_rule_areas import CapturedAntennaFixture
from agent_ee import placement_adapter as pa
from agent_ee.board_envelope import validate
from agent_ee.storage import digest, identity, read_json, write_json

FIXED = ('J1', 'J2', 'U1', 'U2', 'H1', 'H2', 'H3', 'H4')
MOVABLE = ('C5', 'J3', 'R5', 'R6')
# Authored stand-in courtyards (mm), around each footprint's own pads.
COURTYARDS = {'Owned.pretty/R2.kicad_mod': (-0.75, -0.75, 1.75, 0.75),
              'Owned.pretty/PinHeader_1x03.kicad_mod': (-1.1, -1.1, 6.18, 1.1),
              'RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod': (-0.75, -0.75, 1.75, 0.75)}


class PodPlacementFixture(EnvelopeScaffoldFixture):
    def setUp(self):
        super().setUp()
        cad = read_json(self.source / 'cad.json')
        for rel, (x0, y0, x1, y1) in COURTYARDS.items():
            path = self.source / rel
            data = path.read_bytes().rstrip()
            assert data.endswith(b')') and b'CrtYd' not in data
            path.write_bytes(data[:-1] + (f' (fp_rect (start {x0} {y0}) (end {x1} {y1}) '
                                          '(stroke (width 0.05) (type solid)) (layer "F.CrtYd")))\n').encode())
        for lib in cad['libraries']:
            for member in lib['members']:
                member['sha256'] = digest(self.source / member['path'])
        write_json(self.source / 'cad.json', cad)
        self.parts['cad']['manifest']['sha256'] = digest(self.source / 'cad.json')
        members = [m for lib in cad['libraries'] for m in lib['members']]
        self.parts['cad']['members_sha256'] = identity(sorted(
            [*members, dict(path='Owned.kicad_sym', sha256=digest(self.source / 'Owned.kicad_sym'))],
            key=lambda m: m['path']))
        write_json(self.source / 'parts.json', self.parts)
        # Movable parts exist only in connectivity intent; forward annotation adds them.
        ids = {c['reference']: c['id'] for c in self.intent['components']}
        template = {c['reference']: c for c in self.intent['components']}
        for ref, like, lib_id in (('R5', 'U1', 'Owned:R'), ('C5', 'U1', 'Owned:C'), ('R6', 'U1', 'Owned:R'),
                                  ('J3', 'J1', 'Owned:Conn3')):
            self.intent['components'].append(dict(template[like], id=ref.lower(), reference=ref, lib_id=lib_id,
                                                  value=ref))
            ids[ref] = ref.lower()
        nets = {'IMU_INT': [('U1', '1'), ('R5', '1')], 'CAN_H': [('J1', '1'), ('C5', '1'), ('J3', '1')],
                'RF': [('J2', '1'), ('R6', '1')]}
        used = {(ids[r], n) for terminals in nets.values() for r, n in terminals}
        self.intent['nets'] = [dict(id=name.lower(), name=name, terminals=[
            dict(component=ids[r], pin=dict(number=n)) for r, n in terminals]) for name, terminals in nets.items()]
        self.intent['no_connects'] = [nc for nc in self.intent['no_connects']
                                      if (nc['component'], nc['pin']['number']) not in used]
        self.intent['no_connects'] += [dict(component=ids[r], pin=dict(number=n)) for r, n in
                                       (('R5', '2'), ('C5', '2'), ('R6', '2'), ('J3', '2'), ('J3', '3'))]
        write_json(self.source / 'intent.json', self.intent)
        self.save()

    def scaffolded(self):
        """AGENTEE-177's content step, run through its stage receipt."""
        inputs, path, receipt = self.content()
        self.assert_completed(path, receipt)
        return inputs

    def annotated(self):
        """Scaffold content, then native forward annotation that adds the movable parts."""
        from agent_ee import forward_annotation as fa
        from agent_ee.executor import execute
        from agent_ee.schematic_emitter import emit_schematic
        inputs = self.scaffolded()
        result, files = emit_schematic(self.intent, {'Owned.kicad_sym': (inputs / 'Owned.kicad_sym').read_bytes()},
                                       read_json(inputs / 'layout.json'))
        self.assertEqual(result['execution'], 'ok', result)
        (inputs / 'project/design.kicad_sch').write_bytes(files['design.kicad_sch'])
        record = execute(inputs, self.root / 'xml', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                         '-o', '/work/native.xml', '/candidate/project/design.kicad_sch'])
        self.assertEqual(record['execution'], 'ok', record)
        board, xml = inputs / 'project/design.kicad_pcb', self.root / 'xml/native.xml'
        manifest = read_json(inputs / 'cad.json')
        plan = fa.plan(board, xml, manifest, inputs, self.root / 'annotation-plan')
        self.assertEqual(plan['execution'], 'ok', plan)
        self.assertEqual({k: plan['counts'][k] for k in ('retain', 'add', 'remove', 'board_only')},
                         {'retain': 4, 'add': 4, 'remove': 0, 'board_only': 4})
        out = self.root / 'annotated.kicad_pcb'
        result = fa.apply(plan, board, xml, manifest, inputs, out, self.root / 'annotation-apply')
        self.assertEqual(result['execution'], 'ok', result)
        board.write_bytes(out.read_bytes())
        return inputs, xml

    def envelope_record(self, inputs):
        return validate((inputs / 'envelope.json').read_bytes())

    def envelope_checks(self, inputs):
        """AGENTEE-177's registered envelope checks: one per derived requirement."""
        import subprocess
        from agent_ee.board_envelope import derive_requirements, source_inventory
        record = validate((inputs / 'envelope.json').read_bytes(),
                          profile=(inputs / 'fab-profile.json').read_bytes(), captured=source_inventory(inputs))
        requirements = derive_requirements(record, 'envelope.json')['requirements']
        version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True, text=True,
                                 check=True).stderr.splitlines()[0]
        checks = [dict(id='facts', kind='datasheet_facts', params=dict(facts='facts.json', extractor=dict(
            path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'), version=version))),
            dict(id='drc', kind='drc'), dict(id='fab', kind='fab_profile', params=dict(fab_profile='fab-profile.json'))]
        criteria = {'outline': 'outline/1', 'layer_count': 'layers/1', 'stackup': 'stackup/1',
                    'mounting_holes': 'mounting-holes/1', 'keepouts': 'keepouts/1'}
        for req in requirements:
            field, index = req['source']['locator'].split('/')[1], req['source']['locator'].split('/')[-1]
            cid = req['id'].removeprefix('R-envelope-telemetry-pod-').lower()
            if field == 'fab_profile':
                cid = 'fab'
            elif field == 'fixed_footprints':
                checks.append(dict(id=cid, kind='placement', params=dict(criterion='connector-edge/1',
                    envelope='envelope.json', reference=record[field][int(index)]['reference'], drc_check='drc')))
            else:
                params = dict(criterion=criteria[field], envelope='envelope.json')
                if field in ('mounting_holes', 'keepouts'):
                    params['id'] = record[field][int(index)]['id']
                checks.append(dict(id=cid, kind='board_envelope', params=params))
            req['checks'] = [cid]
            del req['field_sha256'], req['check_kinds']
        requirements.append(dict(id='R-facts', statement='Verify cited mechanical dimensions.', intent='informational',
            source=dict(path='targets.md', locator='line 1'), checks=['facts']))
        requirements.append(dict(id='native-drc', statement='Native DRC passes.', intent='acceptance',
            source=dict(path='targets.md', locator='line 1'), checks=['drc']))
        scope = read_json(self.root / 'content/scope.json')
        scope.update(schema='agent-ee.scope/3', envelope=dict(path='envelope.json', profile='fab-profile.json', dialog=None),
                     baseline=dict(requirements=requirements, targets=['targets.md'], reference_architectures=['reference.md']))
        write_json(self.root / 'content/scope.json', scope)
        return checks

    def pad_layers(self, board, name):
        """Pad layer names from a fresh process that only loads the saved board.

        LSET.AllCuMask() is shared native state; a writer that mutated it would
        fold *.Mask into *.Cu on save, which only a fresh-process reload shows."""
        reload = ("import json,sys,pcbnew\n"
            "board=pcbnew.LoadBoard(sys.argv[1])\n"
            "json.dump({fp.GetReference()+'.'+p.GetNumber():sorted(pcbnew.BOARD.GetStandardLayerName(l)"
            " for l in p.GetLayerSet().Seq()) for fp in board.GetFootprints() for p in fp.Pads()},open(sys.argv[2],'w'))\n")
        out = self.root / (name + '-pads.json')
        subprocess.run(['/usr/bin/python3', '-B', '-c', reload, str(board), str(out)],
                       env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src')),
                       capture_output=True, check=True)
        return read_json(out)

    def place(self, inputs, xml, policy_changes=None, name='placer'):
        """Coordinator-derived request; the actual writer runs in the networkless sandbox."""
        from agent_ee.executor import execute
        from test_placer import request
        board = (inputs / 'project/design.kicad_pcb').read_bytes()
        refs = pa.envelope_references(self.envelope_record(inputs))
        policy = request(fixed_refs=[], step_budget=200000, **(policy_changes or {}))
        config = pa.helper_request(board, policy, refs)
        config['authorization']['writable_root'] = '/work'
        staged = self.root / (name + '-inputs')
        staged.mkdir()
        (staged / 'board.kicad_pcb').write_bytes(board)
        (staged / 'netlist.xml').write_bytes(xml.read_bytes())
        write_json(staged / 'request.json', config)
        work = self.root / name
        execution = execute(staged, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
            '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
            '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=300)
        return config, execution, work

    # Board keepouts that forbid footprints, and envelope keepout checks with their outcomes.
    BOARD_KEEPOUTS = ['fixture-clearance']
    # Extra outline-containment/1 params, such as the envelope that declares courtyard overhangs.
    CONTAINMENT = {}
    KEEPOUT_CHECKS = {'keepout-fixture-clearance': 'pass', 'keepout-board-clearance': 'pass'}

    def place_and_grade(self):
        """Fixed parts and holes keep their saved transforms; registered placement checks pass."""
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.placement import intersection_area, qualified_polygons
        from agent_ee.placement_probe import extract
        from agent_ee.symbol_library import children, parse_sexpr
        inputs, xml = self.annotated()
        source = inputs / 'project/design.kicad_pcb'
        initial = extract(source)
        before = {fp['ref']: fp for fp in initial['footprints']}
        self.assertEqual(set(before), {*FIXED, *MOVABLE})
        layers_before = self.pad_layers(source, 'annotated')
        config, execution, work = self.place(inputs, xml)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        self.assertEqual(sorted(config['policy']['fixed_refs']), sorted(FIXED))
        result = read_json(work / 'result.json')
        self.assertEqual((result['status'], result['write_scope']['status'], result['native_courtyard_overlaps']),
                         ('placed', 'allowed', []))
        self.assertEqual(result['judgment']['verdict'], 'pass')
        probe = read_json(work / 'proposal/placement-probe.json')
        after = {fp['ref']: fp for fp in probe['footprints']}
        for ref in FIXED:
            self.assertEqual(after[ref], before[ref], ref)
        moved = {t['reference'] for t in result['write_scope']['transforms']}
        self.assertTrue(moved and moved <= set(MOVABLE), moved)
        for ref in MOVABLE:
            for fixed in FIXED:
                for side in set(after[ref]['courtyards']) & set(after[fixed]['courtyards']):
                    for a in qualified_polygons(after[ref]['courtyards'][side]):
                        for b in qualified_polygons(after[fixed]['courtyards'][side]):
                            self.assertEqual(intersection_area(a, b), 0, (ref, fixed, side))
        # Every rule area survives the writer unchanged; movable courtyards stay out of
        # each footprint keepout on its copper side, board-level or module-owned.
        self.assertEqual(probe['rule_areas'], initial['rule_areas'])
        board_areas = [a for a in probe['rule_areas'] if a['owner'] == 'board' and a['forbid']['footprints']]
        self.assertEqual([a['name'] for a in board_areas], self.BOARD_KEEPOUTS)
        keepouts = [a for a in probe['rule_areas'] if a['forbid']['footprints']]
        for ref in MOVABLE:
            for side, courtyard in after[ref]['courtyards'].items():
                for area in keepouts:
                    if side + '.Cu' not in area['layers']:
                        continue
                    for a in qualified_polygons(courtyard):
                        for b in qualified_polygons(area['outline']):
                            self.assertEqual(intersection_area(a, b), 0, (ref, side, area['name']))
        # Exact pad layer sets after a fresh-process reload of the placed board.
        placed = work / 'proposal/candidate.kicad_pcb'
        layers_after = self.pad_layers(placed, 'placed')
        self.assertEqual(layers_after, layers_before)
        copper = ['F.Cu', 'B.Cu', *[f'In{i}.Cu' for i in range(1, 31)]]
        through = sorted([*copper, 'F.Mask', 'B.Mask'])
        smd = ['F.Cu', 'F.Mask', 'F.Paste']
        self.assertEqual(layers_after, {
            **{f'{ref}.{n}': through for ref in ('J1', 'J3') for n in '123'},
            **{f'H{i}.': through for i in range(1, 5)},
            **{f'{ref}.{n}': smd for ref in ('U1', 'U2', 'R5', 'C5', 'R6') for n in '12'},
            **{f'J2.{n}': ['B.Cu', 'B.Mask', 'B.Paste'] for n in '12'}})
        serialized = {}
        for fp in children(parse_sexpr(placed.read_bytes()), 'footprint'):
            ref = next(p[2] for p in children(fp, 'property') if p[1] == 'Reference')
            for pad in children(fp, 'pad'):
                serialized[ref + '.' + pad[1]] = children(pad, 'layers')[0][1:]
        self.assertEqual(serialized, {k: ['*.Cu', '*.Mask'] if v == through else v for k, v in layers_after.items()})
        # The registered checks judge the placed board as a captured candidate.
        source.write_bytes(placed.read_bytes())
        checks = self.envelope_checks(inputs)
        checks += [dict(id=c, kind='placement', params=dict(criterion=c + '/1', drc_check='drc',
                                                            **(self.CONTAINMENT if c == 'outline-containment' else {})))
                   for c in ('courtyard-overlap', 'outline-containment')]
        scope = read_json(self.root / 'content/scope.json')
        requirements = scope['baseline']['requirements']
        requirements.append(dict(id='R-pod-placement', statement='Placed courtyards stay apart and on the board.',
            intent='acceptance', source=dict(path='targets.md', locator='line 1'),
            checks=['courtyard-overlap', 'outline-containment']))
        scope['checks'] = checks
        write_json(self.root / 'content/scope.json', scope)
        run = self.root / 'checks'
        run.mkdir()
        capture(self.root / 'content/scope.json', run)
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 600)}
        graded = ['courtyard-overlap', 'outline-containment', 'facts', 'outline',
                  *[f'hole-h{i}' for i in range(1, 5)], *[f'fixed-{r}' for r in ('j1', 'j2', 'u1', 'u2')]]
        for cid in graded:
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'pass'), results[cid])
        self.assertEqual({cid: results[cid]['outcome'] for cid in results if cid.startswith('keepout-')},
                         self.KEEPOUT_CHECKS)
        return results, probe


class PodPlacementTests(PodPlacementFixture, unittest.TestCase):
    def test_fixed_from_envelope_native(self):
        """Fixed parts and holes keep their saved transforms; registered placement checks pass."""
        self.place_and_grade()

    def test_fixed_move_refused(self):
        """A writer move of the AMPSEAL stand-in is refused by name, even when unlocked."""
        import pcbnew
        inputs, _ = self.annotated()
        path = inputs / 'project/design.kicad_pcb'
        board = pcbnew.LoadBoard(str(path))
        for fp in board.GetFootprints():
            if fp.GetReference() == 'J1':
                fp.SetLocked(False)  # Only the captured envelope protects it now.
        pcbnew.SaveBoard(str(path), board)
        source = path.read_bytes()
        manifest = {'scope': {'checks': [dict(id='edge-j1', kind='placement', params=dict(
            criterion='connector-edge/1', envelope='envelope.json', reference='J1', drc_check='drc'))]},
            'files': {'envelope.json': digest(inputs / 'envelope.json')}}
        envelopes = pa.envelope_fixed(manifest, inputs, [])
        self.assertEqual(envelopes[0]['references'], sorted(FIXED))
        protected, refs = pa.protected_footprints(source, [], envelopes[0]['references'])
        self.assertEqual(refs, sorted(FIXED))
        for ref, dx in (('J1', 1_000_000), ('H1', 500_000), ('R5', 1_000_000)):
            with self.subTest(ref=ref):
                board = pcbnew.LoadBoard(str(path))
                fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
                fp.SetPosition(pcbnew.VECTOR2I(fp.GetPosition().x + dx, fp.GetPosition().y))
                moved = self.root / f'moved-{ref}.kicad_pcb'
                pcbnew.SaveBoard(str(moved), board)
                scope = pa.write_scope(source, moved.read_bytes(), protected)
                if ref == 'R5':
                    self.assertEqual(scope['status'], 'allowed', scope['violations'])
                    continue
                self.assertEqual(scope['status'], 'refused')
                self.assertEqual([v for v in scope['violations'] if f'({ref})' in v],
                                 [f'footprint {fp.m_Uuid.AsString()} ({ref}): protected footprint transform changed'])


class CapturedAntennaPodPlacementTests(PodPlacementFixture, CapturedAntennaFixture, unittest.TestCase):
    """The radio's footprint-owned antenna zone comes from the explicit captured-CAD input.

    AGENTEE-211: keepouts/1 stays unknown for that declaration; the placer still
    treats the retained zone as an obstacle and the writer keeps it unchanged."""
    BOARD_KEEPOUTS = []
    KEEPOUT_CHECKS = {'keepout-antenna': 'unknown', 'keepout-board-clearance': 'pass'}

    def setUp(self):
        super().setUp()
        self.write_retention()  # The stand-in courtyards changed the captured member bytes.

    def scaffolded(self):
        # In-process writer, as in test_scaffold_rule_areas: the synthetic guideline
        # pin is patched in this process only, so the sandboxed stage step would refuse.
        return self.produce('content')

    def test_fixed_from_envelope_captured_antenna_native(self):
        results, probe = self.place_and_grade()
        u2 = next(fp for fp in probe['footprints'] if fp['ref'] == 'U2')
        owned = [a for a in probe['rule_areas'] if a['owner'] == u2['uuid']]
        self.assertEqual([a['forbid']['footprints'] for a in owned], [True])
        antenna = results['keepout-antenna']
        self.assertEqual((antenna['execution'], antenna['outcome']), ('unsupported', 'unknown'))
        self.assertIn('antenna placement qualification unresolved', antenna['summary'])


class OverhangPodPlacementTests(PodPlacementFixture, unittest.TestCase):
    """AGENTEE-212: the SMA stand-in's courtyard leaves the right edge by a declared allowance.

    The envelope declares the allowance for J2 alone; the coordinator-derived request carries
    it, and outline-containment/1 reads it from the same captured envelope bytes."""
    CONTAINMENT = {'envelope': 'envelope.json'}

    def setUp(self):
        super().setUp()
        j2 = next(f for f in self.envelope['fixed_footprints'] if f['reference'] == 'J2')
        j2.update(inset_mm=0.5, courtyard_overhangs=[dict(edge='right', max_mm=1.5)])
        self.save()

    def place(self, inputs, xml, policy_changes=None, name='placer'):
        overhangs = pa.envelope_overhangs(self.envelope_record(inputs))
        self.assertEqual([o['reference'] for o in overhangs], ['J2'])
        return super().place(inputs, xml, {'fixed_overhangs': overhangs, **(policy_changes or {})}, name)

    def test_declared_overhang_native(self):
        results, probe = self.place_and_grade()
        containment = results['outline-containment']['details']
        self.assertEqual(containment['envelope']['path'], 'envelope.json')
        declared = containment['declared_overhangs']
        self.assertEqual(sorted(d['ref'] for d in declared), ['J2'])
        self.assertGreater(declared[0]['overhang_area_mm2'], 0)

    def test_undeclared_overhang_refused_by_name(self):
        inputs, xml = self.annotated()
        _, execution, work = PodPlacementFixture.place(self, inputs, xml)
        result = read_json(work / 'result.json')
        self.assertEqual((result['status'], result['outcome'], result['placements']), ('fixed_conflict', 'unknown', []))
        self.assertIn('fixed placement J2 conflicts', result['reason'])
        self.assertFalse((work / 'proposal').exists())


if __name__ == '__main__':
    unittest.main()
