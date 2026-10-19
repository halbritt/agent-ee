"""AGENTEE-179: the placer and adapter honour the scaffold's envelope fixed footprints.

The pod probe board is AGENTEE-177's scaffold output with original stand-ins:
J1 for the AMPSEAL header, J2 for the SMA, U1 for the IMU, U2 for the radio and
four corner holes. The stand-ins receive authored courtyards here; no vendor CAD
bytes enter the tree. Native tools, the sandbox and the registered checks are
real; this is generator mechanics, not part suitability.
"""
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scaffold import EnvelopeScaffoldFixture
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

    def annotated(self):
        """Scaffold content, then native forward annotation that adds the movable parts."""
        from agent_ee import forward_annotation as fa
        from agent_ee.executor import execute
        from agent_ee.schematic_emitter import emit_schematic
        inputs, path, receipt = self.content()
        self.assert_completed(path, receipt)
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


class PodPlacementTests(PodPlacementFixture, unittest.TestCase):
    def test_fixed_from_envelope_native(self):
        """Fixed parts and holes keep their saved transforms; registered placement checks pass."""
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.placement import intersection_area, qualified_polygons
        from agent_ee.placement_probe import extract
        inputs, xml = self.annotated()
        before = {fp['ref']: fp for fp in extract(inputs / 'project/design.kicad_pcb')['footprints']}
        self.assertEqual(set(before), {*FIXED, *MOVABLE})
        config, execution, work = self.place(inputs, xml)
        self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
        self.assertEqual(sorted(config['policy']['fixed_refs']), sorted(FIXED))
        result = read_json(work / 'result.json')
        self.assertEqual((result['status'], result['write_scope']['status'], result['native_courtyard_overlaps']),
                         ('placed', 'allowed', []))
        self.assertEqual(result['judgment']['verdict'], 'pass')
        after = {fp['ref']: fp for fp in read_json(work / 'proposal/placement-probe.json')['footprints']}
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
        areas = [a for a in read_json(work / 'proposal/placement-probe.json')['rule_areas']
                 if a['owner'] == 'board' and a['forbid']['footprints']]
        self.assertEqual([a['name'] for a in areas], ['antenna'])
        for ref in MOVABLE:
            for side, courtyard in after[ref]['courtyards'].items():
                for a in qualified_polygons(courtyard):
                    for b in qualified_polygons(areas[0]['outline']):
                        self.assertEqual(intersection_area(a, b), 0, (ref, side))
        # The registered checks judge the placed board as a captured candidate.
        (inputs / 'project/design.kicad_pcb').write_bytes((work / 'proposal/candidate.kicad_pcb').read_bytes())
        checks = self.envelope_checks(inputs)
        checks += [dict(id=c, kind='placement', params=dict(criterion=c + '/1', drc_check='drc'))
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
        graded = ['courtyard-overlap', 'outline-containment', 'facts', 'outline', 'keepout-antenna',
                  *[f'hole-h{i}' for i in range(1, 5)], *[f'fixed-{r}' for r in ('j1', 'j2', 'u1', 'u2')]]
        for cid in graded:
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']), ('ok', 'pass'), results[cid])

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


if __name__ == '__main__':
    unittest.main()
