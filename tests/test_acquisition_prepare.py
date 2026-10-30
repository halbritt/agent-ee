"""Source-only public acquisition preparation; no native tools or stage execution."""
import contextlib
from decimal import Decimal, localcontext
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_prepare, build_workflow, requirement_map, spec_record, storage, interval_assertions

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', ROOT / 'build/acquisition-release/inputs'))
PLAN = Path(os.environ.get('AGENT_EE_ACQUISITION_PLAN', BASE.parent / 'build.json'))
CPL = Path(os.environ.get('AGENT_EE_ACQUISITION_CPL_INPUTS', ROOT / 'build/acquisition-release/cpl-inputs'))
TEXT = Path(os.environ.get('AGENT_EE_ACQUISITION_TEXT_INPUTS', ROOT / 'build/acquisition-release/manufacturer-text'))


def brief(cap='150'):
    return ('Use acquisition reference acquisition-control unchanged.\n'
            f'Total I2C line capacitance shall be at most {cap} pF per line, '
            'including board, host and cable.\n').encode()


class BriefTests(unittest.TestCase):
    def test_operating_instruction_distinguishes_seven_bit_address_from_wire_bytes(self):
        original=('100 pF per line.\nADDR is grounded, so the eight-bit I2C address is '
                  '0x48 (write address byte\n0x90, read address byte 0x91).\n')
        actual=acquisition_prepare._revise_operating(original,'150 pF')
        self.assertEqual(actual,original.replace('100 pF','150 pF').replace('eight-bit','seven-bit'))
        with self.assertRaisesRegex(ValueError,'address instruction'):
            acquisition_prepare._revise_operating(original.replace('0x48','0x49'),'150 pF')

    def test_missing_ambiguous_contradictory_and_unsupported_requests_refuse(self):
        cases = [
            (b'', 'missing_reference_adoption'),
            (brief().split(b'\n', 1)[1], 'missing_reference_adoption'),
            ((acquisition_prepare.ADOPTION + '\n').encode(), 'missing_capacitance'),
            (brief() + b'Use 5 V push-pull I2C.\n', 'unsupported_clause'),
            (brief().replace(b'Total I2C', b'Cable I2C'), 'unsupported_clause'),
            (brief().replace(b'150 pF', b'100 or 150 pF'), 'unsupported_clause'),
            (brief() + brief('100').split(b'\n', 1)[1], 'contradictory_capacitance'),
            (brief('200'), 'unsupported_fixed_recipe_bound'),
            (brief('0'), 'unsupported_fixed_recipe_bound'),
            (brief('99.999'), 'unsupported_fixed_recipe_bound'),
            (brief('150.001'), 'unsupported_fixed_recipe_bound'),
            (brief('125') + brief('150').split(b'\n', 1)[1], 'contradictory_capacitance'),
            (brief().replace(b'acquisition-control', b'another-reference'), 'unsupported_clause'),
        ]
        for raw, code in cases:
            with self.subTest(code=code, raw=raw):
                with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
                    acquisition_prepare.parse_brief(raw)
                self.assertEqual(caught.exception.report['findings'][0]['code'], code)

    def test_range_precision_and_zero_padding_are_exact(self):
        for text in ('100', '125', '150', '000100.' + '0' * 80, '000150.' + '0' * 80,
                     '100.' + '0' * 24 + '1'):
            with self.subTest(text=text), localcontext() as context:
                context.prec = 6
                selected = acquisition_prepare.parse_brief(brief(text))
                self.assertEqual(selected['value'], Decimal(text))
                self.assertIn(text + ' pF', selected['quantity'])
        raw = brief('100.' + '0' * 25 + '1')
        with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
            acquisition_prepare.parse_brief(raw)
        report = caught.exception.report
        self.assertEqual(report['findings'][0]['code'], 'unsupported_fixed_recipe_bound')
        self.assertIn('precision', report['findings'][0]['message'])
        self.assertEqual(report['outcome'], 'refused')


class FinalOverlayTests(unittest.TestCase):
    def test_late_role_rows_bind_the_exact_sealed_map_and_overlay_drift_refuses(self):
        # Public calibration 2026-10-29: role rows arrive after the earlier
        # selected-CPL normalization. Exercise real role and typed producers;
        # this authored fixture makes no retained-run qualification claim.
        from agent_ee import acquisition_roles, typed_overlay
        from test_acquisition_roles import authored
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            revised = root / 'revision'
            revised.mkdir()
            doc, captures, overlay, _, _, _, _ = authored(self, revised)
            acquisition_roles.apply(revised, {'parts': {'manifest': 'parts.json'}},
                                    overlay, doc, captures)
            for key in ('acq-mech-3-return-paths', 'acq-elec-2-source-evaluation',
                        'acq-elec-5-host-bus-contract'):
                overlay['judgments'].pop('judgments/' + key + '.json')
            live_order = list(overlay['block_requirements'])
            self.assertNotEqual(live_order, sorted(live_order))
            source_map = (revised / 'requirement-map.json').read_bytes()
            overlay_path = root / 'overlay.json'
            final = acquisition_prepare._write_final_overlay(overlay_path, overlay)
            predicted = typed_overlay.derive_map(requirement_map.map_declaration(source_map), final)
            predicted_bytes = storage.json_bytes(predicted)
            sealed = root / 'sealed'
            result = typed_overlay.prepare(revised, overlay_path, sealed, added_sources={})
            self.assertEqual((sealed / 'requirement-map.json').read_bytes(), predicted_bytes)
            self.assertEqual(result['manifest']['overlay_sha256'],
                             spec_record.sha256(overlay_path.read_bytes()))
            self.assertEqual(result['manifest']['derived']['requirement_map_sha256'],
                             spec_record.sha256(predicted_bytes))
            self.assertEqual([r['id'] for r in predicted['requirements']][-len(live_order):],
                             sorted(live_order))
            self.assertEqual((revised / 'requirement-map.json').read_bytes(), source_map)
            self.assertEqual(result['manifest']['accounting']['judgment_records_present'], [])
            drift = json.loads(overlay_path.read_bytes())
            drift['base']['requirement_map_sha256'] = '0' * 64
            overlay_path.write_bytes(storage.json_bytes(drift))
            with self.assertRaisesRegex(ValueError, 'base bytes differ'):
                typed_overlay.prepare(revised, overlay_path, root / 'refused', added_sources={})
            self.assertFalse((root / 'refused').exists())


@unittest.skipUnless(PLAN.is_file() and CPL.is_dir() and TEXT.is_dir(),
                     'retained acquisition plan and additive captures required')
class AcquisitionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = {'cpl': CPL, 'manufacturer-text': TEXT}

    def prepare(self, raw, name='prepared', sources=None, **context):
        path = self.root / (name + '.md')
        path.write_bytes(raw)
        out = self.root / name
        report = acquisition_prepare.prepare(path, PLAN, out,
            added_sources=self.sources if sources is None else sources, **context)
        return out, report

    def test_prior_board_review_is_omitted_before_source_capture_with_original_custody(self):
        from agent_ee import candidate, build_stages
        before = requirement_map.input_inventory(BASE)
        out, report = self.prepare(brief('100'))
        inputs = out / 'inputs'
        self.assertEqual(requirement_map.input_inventory(BASE), before)
        self.assertEqual(report['changed']['ENGINEERING-REVIEW.md']['before'], before['ENGINEERING-REVIEW.md'])
        self.assertIsNone(report['changed']['ENGINEERING-REVIEW.md']['after'])
        ledger = storage.read_json(inputs / 'preparation/edit-ledger.json')
        self.assertEqual(ledger['original_inventory'], before)
        self.assertEqual(ledger['changed'], report['changed'])
        self.assertIn('Original captured recipe only', ledger['changed']['ENGINEERING-REVIEW.md']['retention'])
        scope = build_stages.candidate_scope({}, inputs)
        selected = [name for entry in scope['inputs'] for _, name in candidate.selected_files(entry, inputs)]
        self.assertEqual(set(selected), set(report['inputs']))
        self.assertFalse(any(Path(name).name == 'ENGINEERING-REVIEW.md' for name in selected))
        self.assertNotIn(before['ENGINEERING-REVIEW.md'], report['inputs'].values())
        overlay = storage.read_json(out / 'overlay.json')
        original_overlay = storage.read_json(ROOT / 'fixtures/acquisition-typed/overlay.json')
        self.assertEqual(set(overlay['judgments']), set(original_overlay['judgments']))
        for name, row in overlay['judgments'].items():
            self.assertEqual(row['requirement'], original_overlay['judgments'][name]['requirement'])
            self.assertEqual(row['questions'], original_overlay['judgments'][name]['questions'])
            self.assertFalse(any('ENGINEERING-REVIEW.md' in source for source in row['sources']))
        for name in ('spec.md', 'OPERATING.md', 'analysis/geometry-model.json', 'analysis/geometry_model.py',
                     'analysis/power-startup/corners.lib', 'analysis/power-removal/corners.lib',
                     'circuit.json', 'layout.json', 'groups.json', 'ratings.json', 'adc-ties.json'):
            self.assertEqual((inputs / name).read_bytes(), (BASE / name).read_bytes(), name)
        declaration = storage.read_json(inputs / 'requirement-map.json')
        original = storage.read_json(BASE / 'requirement-map.json')
        self.assertNotIn('ENGINEERING-REVIEW', declaration['scope']['scope'])
        self.assertEqual(declaration['scope']['scope'].split('Captured calculations')[0],
                         original['scope']['scope'].split('ENGINEERING-REVIEW')[0])
        original_record = storage.read_json(BASE / 'spec-record.json')
        record = storage.read_json(inputs / 'spec-record.json')
        self.assertEqual(record['requirements'], original_record['requirements'])
        self.assertEqual(record['prose'], original_record['prose'])
        def limits(document):
            found = {}
            def visit(value):
                if isinstance(value, dict):
                    if 'id' in value and 'params' in value and 'limits' in value['params']:
                        found[value['id']] = value['params']['limits']
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            visit(document)
            return found
        self.assertTrue(limits(original))
        self.assertEqual({name: limits(declaration)[name] for name in limits(original)}, limits(original))
        self.assertEqual(report['accounting']['obligations']['judgment'], 14)
        self.assertEqual(report['accounting']['judgment_records_present'], [])

    def test_nested_role_archived_or_renamed_prior_review_refuses_without_publication(self):
        copied = shutil.copyfile
        before = requirement_map.input_inventory(BASE)
        for index, name in enumerate(('reference-roles/blocks/power/ENGINEERING-REVIEW.md',
                'reference-roles/blocks/power/history/acquisition-preparation-original/ENGINEERING-REVIEW.md',
                'history/acquisition-preparation-original/ENGINEERING-REVIEW.md',
                'reference-roles/blocks/host/renamed-review.txt')):
            def inject(src, dst, *args, **kwargs):
                result = copied(src, dst, *args, **kwargs)
                if Path(src) == BASE / 'ENGINEERING-REVIEW.md' and Path(dst).parent.name == 'revision':
                    target = Path(dst).parent / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(Path(src).read_bytes())
                return result
            output = 'inherited-' + str(index)
            with self.subTest(path=name), patch('shutil.copyfile', inject), self.assertRaisesRegex(
                    ValueError, 'inherited board review must not enter generated source: ' + name):
                self.prepare(brief('125'), output)
            self.assertFalse((self.root / output).exists())
        self.assertEqual(requirement_map.input_inventory(BASE), before)

    @unittest.skipUnless(all(os.environ.get(name) for name in ('AGENT_EE_ADDR_CONTEXT',
        'AGENT_EE_ADDR_SOURCE', 'AGENT_EE_ADDR_SUPPLEMENT', 'AGENT_EE_ADDR_POWER')),
        'explicit captured model/source corrections required')
    def test_model_correction_sources_survive_review_isolation(self):
        sources = {**self.sources, 'source-supplement': Path(os.environ['AGENT_EE_ADDR_SUPPLEMENT']),
                   'power-repair': Path(os.environ['AGENT_EE_ADDR_POWER'])}
        context = Path(os.environ['AGENT_EE_ADDR_CONTEXT'])
        originals = {name: requirement_map.input_inventory(root) for name, root in sources.items()}
        out, report = self.prepare(brief('125'), sources=sources, final_groups=True,
            alternative_context=context, alternative_context_sha256=storage.digest(context),
            alternative_source=Path(os.environ['AGENT_EE_ADDR_SOURCE']))
        inputs = out / 'inputs'
        self.assertFalse(any(Path(name).name == 'ENGINEERING-REVIEW.md' for name in report['inputs']))
        for name in ('source-supplement', 'power-repair'):
            self.assertEqual(requirement_map.input_inventory(inputs / name), originals[name])
            self.assertEqual(requirement_map.input_inventory(sources[name]), originals[name])
        ledger = storage.read_json(inputs / 'preparation/edit-ledger.json')
        self.assertIn('source_supplement', ledger)
        self.assertIn('power_repair', report)
        calculations = storage.read_json(inputs / 'engineering-calculations.json')
        self.assertIn('adc_applicability', calculations)
        self.assertIn('128SPS', calculations['adc_applicability']['operating'])
        self.assertTrue(any('power-repair/' in name for name in calculations['sources']))
        self.assertEqual(report['accounting']['obligations']['judgment'], 14)
        self.assertEqual((inputs / 'analysis/geometry_model.py').read_bytes(),
                         (BASE / 'analysis/geometry_model.py').read_bytes())

    def test_unknown_missing_or_duplicate_review_source_reference_refuses(self):
        path = ROOT / 'fixtures/acquisition-typed/overlay.json'
        original = storage.read_json(path)
        read = Path.read_bytes
        key = 'judgments/acq-elec-1-temperature-applicability.json'
        for index, change in enumerate(('unknown', 'missing', 'duplicate')):
            overlay = json.loads(json.dumps(original))
            sources = overlay['judgments'][key]['sources']
            inherited = next(source for source in sources if 'ENGINEERING-REVIEW.md' in source)
            if change == 'unknown':
                sources[sources.index(inherited)] += ' unknown-section'
            elif change == 'missing':
                sources.remove(inherited)
            else:
                sources.append(inherited)
            def changed_read(target):
                return storage.json_bytes(overlay) if target == path else read(target)
            output = 'unknown-citation-' + str(index)
            with self.subTest(change=change), patch.object(Path, 'read_bytes', changed_read), self.assertRaisesRegex(
                    ValueError, 'inherited review judgment source references differ'):
                self.prepare(brief('125'), output)
            self.assertFalse((self.root / output).exists())

    def test_unknown_scope_description_refuses_even_when_known_sentence_remains(self):
        write = Path.write_bytes
        for index, append in enumerate((False, True)):
            def changed_write(path, raw):
                if path.name == 'requirement-map.json' and path.parent.name == 'revision':
                    document = json.loads(raw)
                    document['scope']['scope'] = ((document['scope']['scope'] if append else '') +
                                                  ' Unexpected inherited board judgment description')
                    raw = storage.json_bytes(document)
                return write(path, raw)
            output = 'unknown-description-' + str(index)
            with self.subTest(append=append), patch.object(Path, 'write_bytes', changed_write), self.assertRaisesRegex(
                    ValueError, 'inherited review scope description differs'):
                self.prepare(brief('125'), output)
            self.assertFalse((self.root / output).exists())

    def test_late_review_copy_cannot_publish_a_source_bundle(self):
        copytree = shutil.copytree
        def late_copy(src, dst, *args, **kwargs):
            result = copytree(src, dst, *args, **kwargs)
            if Path(src).name == 'revision':
                (Path(dst) / 'ENGINEERING-REVIEW.md').write_bytes((BASE / 'ENGINEERING-REVIEW.md').read_bytes())
            return result
        with patch('shutil.copytree', late_copy), self.assertRaisesRegex(
                ValueError, 'inherited board review must not enter generated source: ENGINEERING-REVIEW.md'):
            self.prepare(brief('125'), 'late-review-copy')
        self.assertFalse((self.root / 'late-review-copy').exists())

    def test_final_groups_opt_in_seals_admitted_source_and_mechanics_map(self):
        from agent_ee import acquisition_mechanics, block_source_stage, placement_contract
        out, report = self.prepare(brief('125'), final_groups=True)
        inputs = out / 'inputs'
        plan = json.loads((out / 'build-plan.json').read_bytes())
        path = plan['placement']['block_source']
        source = json.loads((inputs / path).read_bytes())
        self.assertEqual(source['schema'], 'agent-ee.block-contract-source/1')
        self.assertEqual(source['allocation']['blocks'], [
            dict(id=g['name'], references=g['refs'], anchor={'member':g['anchor']}, interfaces_to_fixed=None)
            for g in json.loads((BASE / 'groups.json').read_bytes())['groups']])
        self.assertEqual([len(b['references']) for b in source['allocation']['blocks']], [7,7,3])
        self.assertEqual(source['allocation']['fixed'], [])
        self.assertEqual(source['allocation']['shared'], [])
        self.assertEqual(source['eligibility']['default'], {'faces':['F']})
        self.assertEqual(source['thickness_nm'], 1200000)
        self.assertEqual(source['relationships'], json.loads((inputs / 'electrical-locality.json').read_bytes()))
        pairs = [(o['id'], r, o[r]) for o in source['relationships']['obligations']
                 for r in ('supply','return','leg') if r in o]
        self.assertEqual(len(pairs), 18)
        self.assertTrue(all(p['max_distance_nm'] == 3000000 for _,_,p in pairs[:8]))
        from agent_ee import locality_provenance
        self.assertTrue(all(locality_provenance.classify(p, source['relationships']['source_records'])['enforcement'] == 'hard' for _,_,p in pairs))
        self.assertEqual(source['templates'], [dict(relationship=i, role=r, template='same-face-only') for i,r,_ in pairs])
        self.assertNotIn('board_sha256', source['allocation'])
        self.assertNotIn('board_sha256', source['eligibility']['sources'])
        placement_contract.validate_source(source)
        block_source_stage.admit(inputs, plan, report['inputs'])
        admitted = build_workflow.preflight_build(out / 'build-plan.json', self.root / 'not-run')
        self.assertEqual(admitted[3], plan)
        mechanics = acquisition_mechanics.load(inputs / 'placement-mechanics.json')
        self.assertEqual(mechanics['declaration']['sources']['requirement-map.json'], storage.digest(inputs / 'requirement-map.json'))
        declaration = json.loads((inputs / 'requirement-map.json').read_bytes())
        self.assertIn({'path':path, 'at':path}, declaration['scope']['inputs'])
        self.assertEqual(report['inputs'][path], storage.digest(inputs / path))
        self.assertIn('agent_ee.acquisition_block_source', report['recipe']['helpers'])
        from agent_ee import acquisition_block_source
        self.assertEqual(report['recipe']['helpers']['agent_ee.acquisition_block_source'],
                         storage.digest(Path(acquisition_block_source.__file__)))
        ledger = json.loads((inputs / 'preparation/edit-ledger.json').read_bytes())
        self.assertEqual(ledger['final_groups']['sha256'], report['inputs'][path])
        self.assertNotIn('requirement-map.json', source['eligibility']['sources'])
        self.assertEqual(report['qualification'], 'unverified')
        legacy, old = self.prepare(brief('125'), name='legacy')
        self.assertNotIn('block_source', json.loads((legacy / 'build-plan.json').read_bytes())['placement'])
        self.assertNotIn(path, old['inputs'])
        self.assertNotEqual(old['identity'], report['identity'])
        original = json.loads((legacy / 'inputs/electrical-locality.json').read_bytes())
        self.assertEqual(source['relationships']['obligations'][:5], original['obligations'])
        self.assertEqual((inputs / 'spec.md').read_bytes(), (legacy / 'inputs/spec.md').read_bytes())
        for name in ('groups.json','circuit.json','layout.json','adc-ties.json'):
            self.assertEqual((inputs / name).read_bytes(), (legacy / 'inputs' / name).read_bytes())

    def test_final_groups_refuse_owned_source_drift_and_publish_nothing_on_late_copy_drift(self):
        from agent_ee import acquisition_block_source
        out, _ = self.prepare(brief('100'), final_groups=True)
        plan = json.loads((out / 'build-plan.json').read_bytes())
        mutations = [
            ('groups.json', lambda d:d['groups'][0].update(anchor='U1')),
            ('groups.json', lambda d:d['groups'][0]['refs'].append('J1')),
            ('parts.json', lambda d:d['parts'][0]['instances'].clear()),
            ('circuit.json', lambda d:d['nets'][0]['terminals'].remove({'component':'U2','pin':{'number':'3'}})),
            ('electrical-locality.json', lambda d:d['obligations'][0]['supply']['from'].update(pad='2')),
            ('electrical-locality.json', lambda d:d['obligations'][0].pop('return')),
            ('electrical-locality.json', lambda d:d['obligations'][0]['supply'].update(max_distance_nm=3000001)),
            ('electrical-locality.json', lambda d:d['source_records'].update({'spec.md':'foreign source'})),
            ('electrical-locality.json', lambda d:d['obligations'][0]['supply']['provenance']['citation'].update(sha256='0'*64)),
            ('envelope.json', lambda d:d['stackup'].update(thickness_mm=1.6)),
        ]
        for index, (name, mutate) in enumerate(mutations):
            with self.subTest(name=name, index=index):
                root = self.root / ('drift-' + str(index))
                shutil.copytree(out / 'inputs', root)
                document = json.loads((root / name).read_bytes())
                mutate(document)
                storage.write_json(root / name, document)
                with self.assertRaises(ValueError):
                    acquisition_block_source.derive(root, plan)
        copied = shutil.copyfile
        def drift_copy(src, dst, *args, **kwargs):
            result = copied(src, dst, *args, **kwargs)
            if Path(src) == BASE / 'groups.json' and Path(dst).parent.name == 'revision':
                data = json.loads(Path(dst).read_bytes())
                data['groups'][0]['anchor'] = 'U1'
                storage.write_json(dst, data)
            return result
        before = requirement_map.input_inventory(BASE)
        with patch('shutil.copyfile', drift_copy), self.assertRaisesRegex(ValueError, 'allocation/anchors'):
            self.prepare(brief('100'), name='late-groups-drift', final_groups=True)
        self.assertFalse((self.root / 'late-groups-drift').exists())
        self.assertEqual(requirement_map.input_inventory(BASE), before)
        for name in ('groups.json', 'Device.kicad_sym'):
            root = self.root / ('linked-' + name)
            shutil.copytree(out / 'inputs', root)
            target = root / name
            original = target.read_bytes()
            target.unlink()
            target.symlink_to(out / 'inputs' / name)
            with self.subTest(link=name), self.assertRaises(ValueError):
                acquisition_block_source.derive(root, plan)
            target.unlink()
            target.write_bytes(original + b'\n')
            if name.endswith('.kicad_sym'):
                with self.assertRaisesRegex(ValueError, 'pin resolution'):
                    acquisition_block_source.derive(root, plan)
        root = self.root / 'during-derivation'
        shutil.copytree(out / 'inputs', root)
        read = acquisition_block_source.parts_manifest.read_captured
        changed = []
        def drift_read(directory, name):
            raw = read(directory, name)
            if Path(directory) == root and name == 'Device.kicad_sym' and not changed:
                (root / name).write_bytes(raw + b'\n')
                changed.append(name)
            return raw
        with patch('agent_ee.parts_manifest.read_captured', drift_read), self.assertRaisesRegex(ValueError, 'source changed during derivation'):
            acquisition_block_source.derive(root, plan)

    def test_final_groups_refuse_record_drift_in_the_actual_typed_copy_before_publication(self):
        from agent_ee import acquisition_block_source
        copytree = shutil.copytree
        def drift_copy(src, dst, *args, **kwargs):
            result = copytree(src, dst, *args, **kwargs)
            if Path(src).name == 'revision':
                path = Path(dst) / acquisition_block_source.PATH
                if path.exists():
                    record = json.loads(path.read_bytes())
                    record['eligibility']['sources']['spec.md'] = '0' * 64
                    storage.write_json(path, record)
            return result
        with patch('shutil.copytree', drift_copy), self.assertRaisesRegex(ValueError, 'sealed acquisition final-group source differs'):
            self.prepare(brief('100'), name='typed-copy-drift', final_groups=True)
        self.assertFalse((self.root / 'typed-copy-drift').exists())

    @unittest.skipUnless(os.environ.get('AGENT_EE_GROUP_FACTS'), 'explicit retained acquisition native facts required')
    def test_all_eighteen_legs_grade_complete_and_original_eight_remain_unknown(self):
        import copy
        from agent_ee import acquisition_block_source, block_contract, placement_contract, locality_provenance
        out, _ = self.prepare(brief('100'), final_groups=True)
        root = out / 'inputs'
        plan = json.loads((out / 'build-plan.json').read_bytes())
        source = json.loads((root / acquisition_block_source.PATH).read_bytes())
        facts = json.loads(Path(os.environ['AGENT_EE_GROUP_FACTS']).read_bytes())
        self.assertEqual(len(facts['footprints']), 17)
        # Authored pure transform of retained native facts, not a saved board or routing proof.
        targets = {'D1':(106600000,123300000,180), 'R6':(109700000,120400000,180),
                   'R1':(121900000,109800000,-90), 'C4':(123500000,110250000,90)}
        for fp in facts['footprints']:
            if fp['ref'] not in targets:
                continue
            x,y,angle = targets[fp['ref']]
            ox,oy = fp['position_nm']
            turns = int((angle - fp['rotation_deg']) / 90) % 4
            def move(point):
                dx,dy = point[0]-ox,point[1]-oy
                for _ in range(turns): dx,dy = dy,-dx
                return [x+dx,y+dy]
            for pad in fp['pads']:
                pad['position_nm'] = move(pad['position_nm'])
                pad['rotation_deg'] = (pad['rotation_deg'] + 90*turns) % 360
            for side in fp['courtyards'].values():
                side['polygons'] = [[move(point) for point in poly] for poly in side.get('polygons',[])]
                for primitive in side.get('primitives',[]):
                    for key in ('start','end','center'):
                        if key in primitive: primitive[key] = move(primitive[key])
            fp['position_nm'],fp['rotation_deg'] = [x,y],angle
        facts['board_sha256'] = storage.identity(facts)
        facts['pcbnew_version'] = 'Pure transformed retained facts; no saved native board'
        contract = placement_contract.materialize_source(source, facts['board_sha256'])
        full = block_contract.judge(facts, contract, original_capture=facts)
        complete = block_contract.check_complete(facts, contract)
        for result in (full, complete):
            self.assertEqual(result['status'], 'pass', result)
            self.assertEqual(result['unknown'], [])
            self.assertEqual(result['defects'], [])
            self.assertEqual(result['advisories'], [])
            self.assertEqual(len(result['relationships']), 18)
        old = copy.deepcopy(contract)
        old['relationships']['obligations'] = old['relationships']['obligations'][:5]
        old['templates'] = old['templates'][:8]
        for result in (block_contract.judge(facts, old, original_capture=facts), block_contract.check_complete(facts, old)):
            self.assertEqual(result['status'], 'unknown')
            missing = {u['block']:u['references'] for u in result['unknown'] if u['kind']=='block_pin_relationships_missing'}
            self.assertEqual(missing, {'power':['D1','J1','R5','R6'], 'analog':['J3','R1','R2'], 'host':['J2','R3','R4']})
        original = (root / 'electrical-locality.json').read_bytes()
        placement_raw = (root / 'placement.json').read_bytes()
        for alteration in ('missing-top-citation','unqualified-quote','wrong-bound','missing-leg'):
            locality = json.loads(original)
            pair = locality['obligations'][5]['leg']
            if alteration == 'missing-top-citation': del pair['citation']
            if alteration == 'unqualified-quote':
                pair['citation'] = pair['provenance']['citation']['quote'] = 'J1 pin 1 to D1 pin 2 <= 5 mm.'
                locality['source_records'][acquisition_block_source.ENGINEERING_PATH] = pair['citation']
                pair['provenance']['citation']['sha256'] = spec_record.sha256(pair['citation'].encode())
                self.assertNotEqual(locality_provenance.classify(pair, locality['source_records'])['enforcement'], 'hard')
            if alteration == 'wrong-bound': pair['max_distance_nm'] += 1
            if alteration == 'missing-leg': locality['obligations'].pop()
            placement = json.loads(placement_raw); placement['electrical_locality'] = locality
            storage.write_json(root / 'electrical-locality.json', locality)
            storage.write_json(root / 'placement.json', placement)
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                acquisition_block_source.derive(root, plan)
        (root / 'electrical-locality.json').write_bytes(original)
        (root / 'placement.json').write_bytes(placement_raw)
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            retained = Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);retained.mkdir(exist_ok=True, parents=True)
            for name,record in (('full-18-judge.json',full),('full-18-check-complete.json',complete),('authored-17-facts.json',facts)):
                storage.write_json(retained / name, record)

    def test_changed_brief_prepares_complete_typed_source_for_public_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'brief.md'
            source.write_bytes(brief())
            out = root / 'prepared'
            report = acquisition_prepare.prepare(source, PLAN, out,
                added_sources={'cpl': CPL, 'manufacturer-text': TEXT})
            self.assertEqual(report['outcome'], 'prepared')
            prepared = out / 'inputs'
            interval = json.loads((prepared / 'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(interval['parameters']['bus_c']['max'], '150e-12 F')
            self.assertIn(b'at most 150 pF total line capacitance', (prepared / 'spec.md').read_bytes())
            self.assertIn(b'150 pF per line', (prepared / 'OPERATING.md').read_bytes())
            self.assertIn(b'seven-bit I2C address is 0x48', (prepared / 'OPERATING.md').read_bytes())
            self.assertNotIn(b'eight-bit I2C address is 0x48', (prepared / 'OPERATING.md').read_bytes())
            record = spec_record.verify((prepared / 'spec-record.json').read_bytes(),
                                        (prepared / 'spec.md').read_bytes())
            admitted = build_workflow.preflight_build(out / 'build-plan.json', root / 'native-not-run')
            self.assertEqual(admitted[3]['schema'], 'agent-ee.build-plan/2')
            self.assertFalse((root / 'native-not-run').exists())
            self.assertEqual(record['spec']['sha256'], interval['requirement_sources']['ACQ-ELEC-5-PULLUPS']['sha256'])
            for name in ('circuit.json', 'layout.json', 'groups.json', 'ratings.json', 'adc-ties.json'):
                self.assertEqual((prepared / name).read_bytes(), (BASE / name).read_bytes(), name)
            original_parts = json.loads((BASE / 'parts.json').read_bytes())
            revised_parts = json.loads((prepared / 'parts.json').read_bytes())
            original_parts.pop('declaration_source')
            revised_parts.pop('declaration_source')
            self.assertEqual(revised_parts, original_parts)
            original_plan = json.loads(PLAN.read_bytes())
            self.assertEqual(admitted[3]['placement'],
                             {**original_plan['placement'], 'mechanics': 'placement-mechanics.json'})
            from agent_ee import acquisition_mechanics
            mechanics = acquisition_mechanics.load(prepared / 'placement-mechanics.json')
            self.assertEqual(mechanics['declaration']['sources'],
                             acquisition_mechanics.declaration(prepared)['sources'])
            self.assertEqual(report['inputs']['placement-mechanics.json'],
                             spec_record.sha256((prepared / 'placement-mechanics.json').read_bytes()))
            self.assertEqual(admitted[3]['verification_timeout_s'], original_plan['verification_timeout_s'])
            self.assertFalse((prepared / 'judgments').exists())
            self.assertEqual(report['qualification'], 'unverified')

    def test_control_and_changed_bound_keep_equations_limits_and_source_inventory(self):
        before = requirement_map.input_inventory(BASE)
        original = json.loads((BASE / 'analysis/electrical-intervals.json').read_bytes())
        for cap, expected in [('100', '1.88270060000E-7'), ('150', '2.82405090000E-7')]:
            with self.subTest(cap=cap):
                out, report = self.prepare(brief(cap), name='case-' + cap)
                inputs = out / 'inputs'
                intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
                self.assertEqual({key:{k:v for k,v in row.items() if k != 'requirement'}
                                  for key,row in intervals['parameters'].items() if key != 'bus_c'},
                                 {key:{k:v for k,v in row.items() if k != 'requirement'}
                                  for key,row in original['parameters'].items() if key != 'bus_c'})
                self.assertEqual([{k:v for k,v in row.items() if k != 'requirement'} for row in intervals['assertions']],
                                 [{k:v for k,v in row.items() if k != 'requirement'} for row in original['assertions']])
                calculations = json.loads((inputs / 'engineering-calculations.json').read_bytes())
                self.assertEqual(Decimal(calculations['values']['i2c_rise_time']['value']), Decimal(expected))
                self.assertEqual((inputs / 'operating-supplement/ADS1115-OPERATING.md').read_bytes(),
                    (ROOT / 'fixtures/acquisition-typed/operating-supplement/ADS1115-OPERATING.md').read_bytes())
                self.assertEqual(report['accounting']['obligations']['judgment'], 14)
                self.assertEqual(report['accounting']['judgment_records_present'], [])
        self.assertEqual(requirement_map.input_inventory(BASE), before)

    def test_read_only_capture_prepares_without_changing_source_bytes_or_modes(self):
        capture = self.root / 'captured'
        capture.mkdir()
        source = capture / 'inputs'
        shutil.copytree(BASE, source)
        plan = capture / 'build.json'
        plan.write_bytes(PLAN.read_bytes())
        files = [p for p in source.rglob('*') if p.is_file()]
        directories = [source, *[p for p in source.rglob('*') if p.is_dir()]]
        for path in files:
            path.chmod(0o444)
        for path in directories:
            path.chmod(0o555)
        before = requirement_map.input_inventory(source)
        raw = self.root / 'readonly.md'
        raw.write_bytes(brief('150'))
        out = self.root / 'readonly-prepared'
        try:
            report = acquisition_prepare.prepare(raw, plan, out, added_sources=self.sources)
            self.assertEqual(report['outcome'], 'prepared')
            self.assertIn(b'at most 150 pF total line capacitance',
                          (out / 'inputs/spec.md').read_bytes())
            self.assertEqual(requirement_map.input_inventory(source), before)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o444 for p in files))
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o555 for p in directories))
            self.assertEqual(plan.read_bytes(), PLAN.read_bytes())
        finally:
            for path in directories:
                path.chmod(0o700)

    def test_multibyte_non_equal_length_quantity_relocates_all_source_spans(self):
        raw = brief('150.00').replace(b'150.00 pF', '150.00\u00a0pF'.encode())
        out, _ = self.prepare(raw)
        inputs = out / 'inputs'
        spec = (inputs / 'spec.md').read_bytes()
        self.assertIn('150.00\u00a0pF'.encode(), spec)
        record = spec_record.verify((inputs / 'spec-record.json').read_bytes(), spec)
        obligations = requirement_map.load_obligations((inputs / 'obligations.json').read_bytes(), record, spec)
        declaration = requirement_map.map_declaration((inputs / 'requirement-map.json').read_bytes())
        _, rows = requirement_map.account_requirements(declaration, record, spec,
            files=requirement_map.input_inventory(inputs), inputs=inputs, obligations=obligations)
        for row in rows:
            locator = row['locator']
            if isinstance(locator, dict) and 'byte_start' in locator:
                quote = spec[locator['byte_start']:locator['byte_end']]
                self.assertEqual(spec_record.sha256(quote), row['text_sha256'])
                quote.decode('utf-8')
        crosswalk = json.loads((inputs / 'preparation/source-map.json').read_bytes())
        start, end = crosswalk['capacitance_span']
        self.assertEqual(raw[start:end], raw.splitlines()[1])
        intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
        for source in intervals['requirement_sources'].values():
            locator = json.loads(source['locator'])
            self.assertEqual(source['sha256'], spec_record.sha256(spec))
            self.assertTrue(spec[locator['byte_start']:locator['byte_end']].decode())
        parts = json.loads((inputs / 'parts.json').read_bytes())
        self.assertEqual(parts['declaration_source']['sha256'], spec_record.sha256(spec))
        locality = json.loads((inputs / 'electrical-locality.json').read_bytes())
        self.assertEqual(locality['source_records']['spec.md'].encode(), spec)
        for clause in record['requirements']:
            if clause['id'] == 'ACQ-ELEC-6':
                old = next(r for r in json.loads((BASE / 'spec-record.json').read_bytes())['requirements']
                           if r['id'] == clause['id'])
                self.assertEqual(clause['locator']['byte_start'] - old['locator']['byte_start'], 4)

    def test_raw_brief_bytes_change_identity_and_build_input_binding(self):
        first, a = self.prepare(brief(), 'first')
        second, b = self.prepare(b'\n' + brief() + b'\n', 'second')
        self.assertNotEqual(a['identity'], b['identity'])
        self.assertEqual((first / 'inputs/spec.md').read_bytes(), (second / 'inputs/spec.md').read_bytes())
        self.assertNotEqual(a['inputs']['preparation/brief.md'], b['inputs']['preparation/brief.md'])

    def test_refusals_preserve_existing_outputs_and_publish_no_partial_bundle(self):
        source = self.root / 'brief.md'
        source.write_bytes(brief())
        out = self.root / 'existing'
        out.mkdir()
        (out / 'sentinel').write_bytes(b'keep')
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, out, added_sources=self.sources)
        self.assertEqual((out / 'sentinel').read_bytes(), b'keep')
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, BASE / 'nested-output', added_sources=self.sources)
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, self.root / 'missing-cpl', added_sources={})
        self.assertFalse((self.root / 'missing-cpl').exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        for raw in [brief('200'), brief() + b'Add WiFi.\n']:
            source.write_bytes(raw)
            with self.assertRaises(acquisition_prepare.PreparationRefused):
                acquisition_prepare.prepare(source, PLAN, self.root / 'refused', added_sources=self.sources)
            self.assertFalse((self.root / 'refused').exists())

    def test_cli_consumes_brief_without_an_intermediate_engineering_script(self):
        source = self.root / 'brief.md'
        source.write_bytes(brief())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(self.root / 'cli-output'), '--added-input', 'cpl=' + str(CPL),
                '--added-input', 'manufacturer-text=' + str(TEXT)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())['outcome'], 'prepared')
        self.assertTrue((self.root / 'cli-output/build-plan.json').is_file())
        source.write_bytes(brief() + 'Unknown μ directive.\n'.encode())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(self.root / 'cli-refused')])
        diagnostic = json.loads(stdout.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(diagnostic['brief_sha256'], spec_record.sha256(source.read_bytes()))
        start = diagnostic['findings'][0]['byte_start']
        end = diagnostic['findings'][0]['byte_end']
        self.assertEqual(source.read_bytes()[start:end].decode(), 'Unknown μ directive.')
        self.assertFalse((self.root / 'cli-refused').exists())

    def test_late_filesystem_failure_removes_owned_staging_without_publishing(self):
        before = requirement_map.input_inventory(BASE)
        write = Path.write_bytes

        def full_disk(path, raw):
            if path.name == 'preparation.json':
                raise OSError('injected full disk at final report write')
            return write(path, raw)

        # The external filesystem is the fault boundary; all preparation runs for real.
        with patch.object(Path, 'write_bytes', full_disk):
            with self.assertRaisesRegex(OSError, 'full disk'):
                self.prepare(brief(), 'late-failure')
        self.assertFalse((self.root / 'late-failure').exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        self.assertEqual(requirement_map.input_inventory(BASE), before)

    def profile(self):
        path = self.root / 'profile.json'
        path.write_bytes((ROOT / 'profiles/pod-review.json').read_bytes())
        return path

    def test_requested_review_captures_exact_bytes_and_public_build_input_identity(self):
        profile = self.profile()
        ordinary, old = self.prepare(brief(), 'ordinary')
        out, report = self.prepare(brief(), 'reviewed', review_profile=profile)
        plan = json.loads((out / 'build-plan.json').read_bytes())
        captured = out / 'inputs' / plan['review_profile']
        self.assertEqual(captured.read_bytes(), profile.read_bytes())
        admitted = build_workflow.preflight_build(out / 'build-plan.json', self.root / 'native-not-run')
        self.assertEqual(admitted[-1][plan['review_profile']], spec_record.sha256(profile.read_bytes()))
        self.assertEqual(plan['verification_timeout_s'], 1800)
        self.assertEqual(report['recipe']['review_context']['verification_timeout_s'],
                         {'requested': None, 'effective': 1800})
        self.assertEqual(report['recipe']['helpers']['agent_ee.profiles'],
                         spec_record.sha256((ROOT / 'src/agent_ee/profiles.py').read_bytes()))
        ledger = json.loads((out / 'inputs/preparation/edit-ledger.json').read_bytes())
        self.assertEqual(ledger['review_context'], report['recipe']['review_context'])
        self.assertNotEqual(report['identity'], old['identity'])
        for name in ('spec.md', 'circuit.json', 'placement.json', 'layout.json', 'groups.json',
                     'analysis/electrical-intervals.json', 'analysis/geometry_model.py'):
            self.assertEqual((out / 'inputs' / name).read_bytes(), (ordinary / 'inputs' / name).read_bytes(), name)
        self.assertFalse((out / 'inputs/judgments').exists())
        self.assertEqual(report['qualification'], 'unverified')
        profile.write_bytes(profile.read_bytes() + b'\n')
        changed, second = self.prepare(brief(), 'changed-profile', review_profile=profile)
        self.assertNotEqual(report['identity'], second['identity'])
        other = build_workflow.preflight_build(changed / 'build-plan.json', self.root / 'also-not-run')
        self.assertNotEqual(storage.identity(admitted[-1]), storage.identity(other[-1]))
        self.assertFalse((self.root / 'native-not-run').exists())

    def test_explicit_review_timeout_is_bounded_context_without_automatic_increase(self):
        profile = self.profile()
        first, a = self.prepare(brief(), 'inherited', review_profile=profile)
        second, b = self.prepare(brief(), 'explicit', review_profile=profile, verification_timeout_s=1800)
        third, c = self.prepare(brief(), 'longer', review_profile=profile, verification_timeout_s=3600)
        self.assertNotEqual(a['identity'], b['identity'])
        self.assertEqual(b['recipe']['review_context']['verification_timeout_s'],
                         {'requested': 1800, 'effective': 1800})
        self.assertEqual(json.loads((third / 'build-plan.json').read_bytes())['verification_timeout_s'], 3600)
        self.assertNotEqual(b['identity'], c['identity'])
        for index, seconds in enumerate((0, 86401, True, 1.5)):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.prepare(brief(), 'invalid-timeout-' + str(index), review_profile=profile,
                             verification_timeout_s=seconds)
            self.assertFalse((self.root / ('invalid-timeout-' + str(index))).exists())
        with self.assertRaisesRegex(ValueError, 'review_profile'):
            self.prepare(brief(), 'timeout-only', verification_timeout_s=3600)
        self.assertFalse((self.root / 'timeout-only').exists())

    def test_bad_or_linked_profile_refuses_without_a_bundle(self):
        profile = self.profile()
        missing = self.root / 'missing-profile.json'
        file_link = self.root / 'profile-link.json'
        file_link.symlink_to(profile)
        folder = self.root / 'profile-folder'
        folder.mkdir()
        nested = folder / 'profile.json'
        nested.write_bytes(profile.read_bytes())
        directory_link = self.root / 'directory-link'
        directory_link.symlink_to(folder, target_is_directory=True)
        hard_link = self.root / 'hard-profile.json'
        hard_target = self.root / 'hard-target.json'
        hard_target.write_bytes(profile.read_bytes())
        os.link(hard_target, hard_link)
        malformed = self.root / 'malformed.json'
        malformed.write_bytes(b'{"schema":"agent-ee.profile/1","name":"bad","reviewers":[1,2]}')
        duplicate = self.root / 'duplicate.json'
        duplicate.write_bytes(b'{"schema":"agent-ee.profile/1","schema":"other"}')
        for index, path in enumerate((missing, file_link, directory_link / 'profile.json',
                                      hard_link, malformed, duplicate, folder)):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.prepare(brief(), 'bad-profile-' + str(index), review_profile=path)
            self.assertFalse((self.root / ('bad-profile-' + str(index))).exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        existing = self.root / 'existing-profile-output'
        existing.mkdir()
        (existing / 'sentinel').write_text('keep')
        with self.assertRaises(ValueError):
            self.prepare(brief(), existing.name, review_profile=profile)
        self.assertEqual((existing / 'sentinel').read_text(), 'keep')

    def test_profile_drift_at_publication_refuses_and_removes_owned_staging(self):
        profile = self.profile()
        write = Path.write_bytes
        for mode in ('bytes', 'replacement', 'capture'):
            def drift(path, raw):
                if path.name == 'preparation.json':
                    if mode == 'bytes':
                        write(profile, profile.read_bytes() + b'\n')
                    elif mode == 'replacement':
                        replacement = self.root / 'replacement.json'
                        write(replacement, profile.read_bytes())
                        replacement.replace(profile)
                    else:
                        captured = path.parent / 'inputs/preparation/review-profile.json'
                        write(captured, captured.read_bytes() + b'\n')
                return write(path, raw)
            with self.subTest(mode=mode), patch.object(Path, 'write_bytes', drift):
                with self.assertRaisesRegex(ValueError, 'profile.*changed'):
                    self.prepare(brief(), 'drift-' + mode, review_profile=profile)
            self.assertFalse((self.root / ('drift-' + mode)).exists())
            self.assertEqual(list(self.root.glob('*.prepare-*')), [])

    def test_cli_accepts_explicit_review_profile_and_verification_timeout(self):
        source = self.root / 'cli-brief.md'
        source.write_bytes(brief())
        profile = self.profile()
        out = self.root / 'cli-review'
        with contextlib.redirect_stdout(io.StringIO()):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(out), '--review-profile', str(profile), '--verification-timeout-s', '3600',
                '--added-input', 'cpl=' + str(CPL), '--added-input', 'manufacturer-text=' + str(TEXT)])
        self.assertEqual(status, 0)
        plan = json.loads((out / 'build-plan.json').read_bytes())
        self.assertEqual((out / 'inputs' / plan['review_profile']).read_bytes(), profile.read_bytes())
        self.assertEqual(plan['verification_timeout_s'], 3600)

    def test_public_range_drives_real_operands_prose_spans_and_interval_verdicts(self):
        original = json.loads((BASE / 'analysis/electrical-intervals.json').read_bytes())
        rise_source = next(a for a in original['assertions'] if a['id'] == 'i2c-rise')
        box = {'pullup_r': interval_assertions.declared_interval(
            {k: original['parameters']['pullup_r'][k] for k in ('min', 'max')}, 'ohm', 'pullups'),
            'bus_c': (Fraction(100, 10**12), Fraction(150, 10**12))}
        enclosure, monotonicity = interval_assertions.enclose(
            interval_assertions.parse_expression(rise_source['lhs']), box)
        self.assertEqual(monotonicity['bus_c'], 'nondecreasing')
        self.assertEqual(enclosure[1], Fraction(8473, 10000) * 2222 * Fraction(150, 10**12))
        self.assertLess(enclosure[1], Fraction(300, 10**9))
        identities = set()
        for cap in ('100', '125', '150'):
            raw = brief(cap + '.00').replace((cap + '.00 pF').encode(), (cap + '.00\u00a0pF').encode())
            out, report = self.prepare(raw, 'range-' + cap)
            identities.add(report['identity'])
            inputs = out / 'inputs'
            intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(intervals['parameters']['bus_c']['max'], cap + 'e-12 F')
            for parameter in ('pullup_r', 'rise_limit'):
                self.assertEqual({k:v for k,v in intervals['parameters'][parameter].items() if k != 'requirement'},
                                 {k:v for k,v in original['parameters'][parameter].items() if k != 'requirement'})
            # The requirements stage captures this exact spec before checks execute.
            provenance = inputs / 'requirements-map'
            provenance.mkdir()
            (provenance / 'spec.md').write_bytes((inputs / 'spec.md').read_bytes())
            evaluated = interval_assertions.evaluate_inputs(inputs, {'assertions': 'analysis/electrical-intervals.json'})
            self.assertEqual(evaluated['execution'], 'ok', evaluated)
            rise = next(a for a in evaluated['assertions'] if a['id'] == 'i2c-rise')
            self.assertEqual(rise['verdict'], 'pass')
            self.assertEqual([{k:v for k,v in a.items() if k != 'requirement'} for a in intervals['assertions']],
                             [{k:v for k,v in a.items() if k != 'requirement'} for a in original['assertions']])
            expected = Fraction(8473, 10000) * 2222 * Fraction(int(cap), 10**12)
            calculated = json.loads((inputs / 'engineering-calculations.json').read_bytes())
            self.assertEqual(Fraction(calculated['values']['i2c_rise_time']['value']), expected)
            self.assertIn((cap + '.00\u00a0pF').encode(), (inputs / 'spec.md').read_bytes())
            self.assertIn((cap + '.00\u00a0pF per line').encode(), (inputs / 'OPERATING.md').read_bytes())
            crosswalk = json.loads((inputs / 'preparation/source-map.json').read_bytes())
            self.assertEqual(crosswalk['capacitance_pf'], cap)
            self.assertEqual(crosswalk['operating_supplement']['status'],
                             'retained_bound' if cap == '100' else 'superseded_bound')
            spec = (inputs / 'spec.md').read_bytes()
            record = spec_record.verify((inputs / 'spec-record.json').read_bytes(), spec)
            for row in record['requirements'] + record['prose']:
                locator = row['locator']
                self.assertEqual(spec_record.sha256(spec[locator['byte_start']:locator['byte_end']]),
                                 row['text_sha256'])
            for name in ('circuit.json', 'layout.json', 'groups.json', 'analysis/geometry_model.py'):
                self.assertEqual((inputs / name).read_bytes(), (BASE / name).read_bytes())
        self.assertEqual(len(identities), 3)

    def test_canonical_precision_limit_does_not_round_calculation_or_source(self):
        cap = '100.' + '0' * 24 + '1'
        with localcontext() as context:
            context.prec = 6
            out, report = self.prepare(brief(cap), 'precision')
        inputs = out / 'inputs'
        operand = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())['parameters']['bus_c']
        self.assertEqual(operand['max'], cap + 'e-12 F')
        expected = Fraction(8473, 10000) * 2222 * Fraction(Decimal(cap)) / 10**12
        calculated = json.loads((inputs / 'engineering-calculations.json').read_bytes())
        self.assertEqual(Fraction(calculated['values']['i2c_rise_time']['value']), expected)
        self.assertIn(cap.encode(), (inputs / 'spec.md').read_bytes())
        self.assertEqual(report['recipe']['helpers']['agent_ee.catalog'],
                         spec_record.sha256((ROOT / 'src/agent_ee/catalog.py').read_bytes()))


@unittest.skipUnless(all(os.environ.get(n) for n in ('AGENT_EE_ADDR_CONTEXT','AGENT_EE_ADDR_SOURCE',
                     'AGENT_EE_ADDR_SUPPLEMENT','AGENT_EE_ADDR_POWER','AGENT_EE_ADDR_POWER620')) and PLAN.is_file() and CPL.is_dir() and TEXT.is_dir(),
                     'explicit captured acquisition ADDR source/context required')
class AddrPreparationTests(unittest.TestCase):
    """Actual preparation and compiler calls; no native production/qualification."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='addr-source-pure-'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brief = self.root/'brief.md'; self.brief.write_bytes(brief('125'))
        self.context = Path(os.environ['AGENT_EE_ADDR_CONTEXT'])
        self.options = dict(added_sources={'cpl':CPL,'manufacturer-text':TEXT,
            'source-supplement':Path(os.environ['AGENT_EE_ADDR_SUPPLEMENT']),
            'power-repair':Path(os.environ['AGENT_EE_ADDR_POWER'])},
            review_profile=ROOT/'profiles/pod-review.json', alternative_context=self.context,
            alternative_source=Path(os.environ['AGENT_EE_ADDR_SOURCE']),
            alternative_context_sha256=storage.digest(self.context))
        self.before = requirement_map.input_inventory(BASE)
        self.seed = self.root/'seed'
        acquisition_prepare.prepare(self.brief,PLAN,self.seed,**self.options)
        self.replay = dict(evaluation_context=self.seed/'preparation.json',
                           evaluation_context_sha256=storage.digest(self.seed/'preparation.json'))
        self.control_inputs=self.root/'control-inputs';shutil.copytree(self.seed/'inputs',self.control_inputs)
        self.control_receipt,self.control = self.parts_receipt(self.control_inputs,'independent-control')
        self.expected = self.control_receipt/'run/out/connectivity-intent.json'
        self.fixed = dict(expected_intent=self.expected,expected_intent_sha256=storage.digest(self.expected),
            expected_parts_receipt=self.control_receipt/'receipt.json',
            expected_parts_receipt_sha256=storage.digest(self.control_receipt/'receipt.json'))

    def parts_receipt(self,source,label):
        # The actual pure compiler produces every intent/report byte. Only process
        # execution and fixture-owned cache locking are simulated, never a verdict.
        from agent_ee import native_run,parts_stage,stage_receipts,build_stages
        plan=json.loads((self.seed/'build-plan.json').read_bytes())
        if not (source/'requirements-map').exists():
            stage=self.root/('requirements-'+label);stage.mkdir()
            build_stages._requirements(plan,source,stage)
        def respond(call):
            if call.intent.tool=='version':return native_run.Outcome(stdout=b'Python fixture; not a native run\n')
            report,intent=parts_stage.compile_parts(call.inputs,**plan['parts'])
            raw=storage.json_bytes(intent);report['output_sha256']=spec_record.sha256(raw)
            return native_run.Outcome(files={'out/connectivity-intent.json':raw,'out/report.json':storage.json_bytes(report)})
        adapter=native_run.Recorded(respond)
        path=self.root/('receipt-'+label)
        with patch.dict(os.environ,AGENT_EE_TOOL_SOURCE_CACHE=str(self.root/'owned-tool-cache')), \
                patch('fcntl.flock',lambda *args:None),native_run.using(adapter):
            step=parts_stage.configuration(ROOT/'src',bundle='.',output='build-generated/connectivity-intent.json',**plan['parts'])
            receipt=stage_receipts.run_step(path,source,step,dict(implementation=1,
                source_candidate_id=stage_receipts.tree_identity(source)['sha256'],receipt_index=0))
        self.assertEqual((receipt['status'],receipt['exit_code']),('completed',0))
        self.assertEqual(stage_receipts.validate_receipt(path),receipt)
        self.assertEqual(len(adapter.calls),2)
        return path,receipt

    def prepare(self,name,choice='GND',**options):
        out=self.root/name
        report=acquisition_prepare.prepare(self.brief,PLAN,out,
            **{**self.options,**self.replay,**self.fixed,'addr_tie':choice,**options})
        return out,report

    def test_final_groups_compose_with_fixed_oracle_and_current_captured_parts(self):
        self.options['final_groups'] = True
        self.seed = self.root / 'final-group-seed'
        acquisition_prepare.prepare(self.brief, PLAN, self.seed, **self.options)
        self.replay = dict(evaluation_context=self.seed / 'preparation.json',
                          evaluation_context_sha256=storage.digest(self.seed / 'preparation.json'))
        source = self.root / 'final-group-control'
        shutil.copytree(self.seed / 'inputs', source)
        receipt, _ = self.parts_receipt(source, 'final-group-control')
        expected = receipt / 'run/out/connectivity-intent.json'
        self.fixed = dict(expected_intent=expected, expected_intent_sha256=storage.digest(expected),
                         expected_parts_receipt=receipt / 'receipt.json',
                         expected_parts_receipt_sha256=storage.digest(receipt / 'receipt.json'))
        ground, _ = self.prepare('final-group-ground')
        wrong, _ = self.prepare('final-group-wrong', choice='V3V3')
        self.assertEqual(self.compiled(ground, 'final-group-ground')[1], self.compiled(wrong, 'final-group-wrong')[1])
        from agent_ee import acquisition_block_source, acquisition_mechanics, block_source_stage
        for out in (ground, wrong):
            plan = json.loads((out / 'build-plan.json').read_bytes())
            inputs = out / 'inputs'
            block_source_stage.admit(inputs, plan, requirement_map.input_inventory(inputs))
            acquisition_mechanics.load(inputs / 'placement-mechanics.json')
            self.assertEqual((inputs / acquisition_block_source.PATH).read_bytes(),
                             (ground / 'inputs' / acquisition_block_source.PATH).read_bytes())

    def compiled(self,out,label):
        from agent_ee.baseline import compile_baseline
        source=self.root/('compiler-'+label); shutil.copytree(out/'inputs',source)
        raw={n:(source/n).read_bytes() for n in ('requirement-map.json','spec-record.json','spec.md','obligations.json')}
        template=requirement_map.map_declaration(raw['requirement-map.json'])['scope']
        # Declared-subject capability admission only: these are not emitted CAD.
        for key in ('board','schematic'):
            path=source/template[key]; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'Authored declared subject placeholder; no native qualification.\n')
        from agent_ee import build_stages,stage_receipts,rating_lineage
        parts_dir,parts_receipt=self.parts_receipt(source,label)
        stage_receipts.adopt(parts_dir,parts_receipt,source)
        build_stages._capture_ratings(parts_dir,parts_receipt,source)
        files={n:h for n,h in requirement_map.input_inventory(source).items() if not n.startswith('requirements-map/')}
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            shutil.copytree(source,evidence/('compiled-inputs-'+label))
        scope,receipt=requirement_map.compile_map(raw['requirement-map.json'],raw['spec-record.json'],raw['spec.md'],
            files=files,inputs=source,obligations_raw=raw['obligations.json'])
        provenance={'spec.md':raw['spec.md'],'spec-record.json':raw['spec-record.json'],
            'requirement-map.json':raw['requirement-map.json'],'obligations.json':raw['obligations.json']}
        files.update({'requirements-map/'+k:spec_record.sha256(v) for k,v in provenance.items()})
        with requirement_map.compilation_inputs(source,files,provenance) as captured:
            baseline=compile_baseline(scope,files,captured)
        self.assertEqual(receipt['baseline_id'],baseline['baseline_id'])
        self.assertFalse(set(baseline['files']) & {template['board'],template['schematic']})
        plan=json.loads((self.seed/'build-plan.json').read_bytes())
        derived=build_stages._rated_scope(plan,source,scope)
        self.assertEqual(rating_lineage.strip(derived),scope)
        with requirement_map.compilation_inputs(source,files,provenance) as captured:
            derived_baseline=compile_baseline(derived,files,captured)
            self.assertEqual(compile_baseline(rating_lineage.strip(derived),files,captured),baseline)
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            storage.write_json(evidence/('derived-'+label+'.json'),derived_baseline)
            storage.write_json(evidence/('authored-'+label+'.json'),baseline)
            shutil.copytree(parts_dir,evidence/('parts-receipt-'+label))
        return scope,baseline

    def test_actual_variants_keep_complete_baseline_parts_and_only_one_authored_terminal_delta(self):
        import copy
        from agent_ee import parts_manifest,parts_stage
        ground,a=self.prepare('ground'); wrong,b=self.prepare('wrong','V3V3'); replay,c=self.prepare('replay')
        inventories=[requirement_map.input_inventory(p/'inputs') for p in (ground,wrong,replay)]
        self.assertEqual(inventories[0].keys(),inventories[1].keys())
        changed=sorted(n for n in inventories[0] if inventories[0][n]!=inventories[1][n])
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            for name,path in (('prepared-ground',ground),('prepared-wrong',wrong),('prepared-replay',replay)):
                shutil.copytree(path,evidence/name)
            shutil.copytree(self.control_receipt,evidence/'independent-control-receipt')
            shutil.copyfile(self.seed/'preparation.json',evidence/'independent-control-preparation.json')
        pairs=[self.compiled(ground,'ground'),self.compiled(wrong,'wrong'),self.compiled(replay,'replay')]
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            for name,value in [('baseline-ground.json',pairs[0][1]),('baseline-wrong.json',pairs[1][1]),
                    ('inventory-ground.json',inventories[0]),('inventory-wrong.json',inventories[1]),
                    ('source-differences.json',changed),('preparation-ground.json',a),('preparation-wrong.json',b)]:
                storage.write_json(evidence/name,value)
        self.assertEqual(pairs[0][1],pairs[1][1])  # COMPLETE objects, not selected fields.
        self.assertEqual(pairs[0][1],pairs[2][1])
        fixed='fixedexpected/connectivity-intent-addr-gnd.json'
        self.assertEqual(pairs[0][1]['files'][fixed],storage.digest(self.expected))
        self.assertFalse(any(n.startswith('build-generated/') for n in pairs[0][1]['files']))
        original=json.loads((self.seed/'inputs/requirement-map.json').read_bytes())
        old=acquisition_prepare.typed_overlay._check_pool(original)
        for spec in pairs[0][0]['checks']:
            if spec['kind']=='netlist_parity':
                self.assertEqual(spec,{**old[spec['id']], 'params':{**old[spec['id']]['params'],
                    'source':dict(kind='intent',path=fixed)}})
        self.assertEqual(sum(c['kind']=='netlist_parity' for c in pairs[0][0]['checks']),6)
        self.assertEqual(pairs[0][0],pairs[1][0])
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            ids=[json.loads((Path(os.environ['AGENT_EE_ADDR_EVIDENCE'])/('derived-'+n+'.json')).read_bytes())['baseline_id']
                 for n in ('ground','wrong','replay')]
            self.assertEqual(len(set(ids)),3)

        self.assertEqual(changed,['circuit.json','overlay-manifest.json','preparation/addr-selection.json','preparation/edit-ledger.json'])
        compiled_roots=[self.root/('compiler-'+n) for n in ('ground','wrong','replay')]
        deps=[parts_manifest.dependency_closure(parts_manifest.load_manifest(p,'parts.json'),p) for p in compiled_roots]
        self.assertEqual(deps[0],deps[1]);self.assertEqual(deps[0],deps[2])
        for name in deps[0]:
            self.assertEqual((compiled_roots[0]/name).read_bytes(),(compiled_roots[1]/name).read_bytes(),name)
        plan=json.loads((ground/'build-plan.json').read_bytes())
        produced=[parts_stage.compile_parts(p,**plan['parts']) for p in compiled_roots[:2]]
        self.assertEqual(storage.json_bytes(produced[0][1]),self.expected.read_bytes())
        self.assertEqual(produced[0][0]['bindings'],produced[1][0]['bindings'])
        self.assertEqual(produced[0][0]['named_nominals'],produced[1][0]['named_nominals'])
        self.assertEqual([n for n,v in produced[0][0]['dependencies'].items() if produced[1][0]['dependencies'][n]!=v],['circuit.json'])
        expected=copy.deepcopy(produced[0][1]);terminal={'component':'U2','pin':{'number':'1'}}
        next(n for n in expected['nets'] if n['id']=='GND')['terminals'].remove(terminal)
        next(n for n in expected['nets'] if n['id']=='V3V3')['terminals'].append(terminal)
        self.assertEqual(expected,produced[1][1])
        self.assertEqual((ground/'inputs/adc-ties.json').read_bytes(),(BASE/'adc-ties.json').read_bytes())
        self.assertEqual(a['recipe'],c['recipe']);self.assertEqual(a['identity'],c['identity'])
        repeated=sorted(n for n in inventories[0] if inventories[0][n]!=inventories[2][n])
        self.assertEqual(repeated,['overlay-manifest.json'])
        left=json.loads((ground/'inputs/overlay-manifest.json').read_bytes())
        right=json.loads((replay/'inputs/overlay-manifest.json').read_bytes())
        # Existing observation of the owned temporary selected-CPL directory.
        left['derived']['added_inputs']['cpl']['source']='<owned selected-CPL path>'
        right['derived']['added_inputs']['cpl']['source']='<owned selected-CPL path>'
        self.assertEqual(left,right)
        seed=json.loads((self.seed/'preparation.json').read_bytes())
        self.assertNotIn('addr_selection',seed);self.assertNotIn('addr_tie',seed['recipe'])
        self.assertEqual(requirement_map.input_inventory(BASE),self.before)

    def test_changed_or_ambiguous_original_addr_identity_refuses_without_writes(self):
        import copy
        original=json.loads((BASE/'circuit.json').read_bytes());terminal={'component':'U2','pin':{'number':'1'}}
        changes=[lambda d:d['components'].append(copy.deepcopy(next(c for c in d['components'] if c['id']=='U2'))),
            lambda d:next(c for c in d['components'] if c['id']=='U2').update(lib_id='Other:ADC'),
            lambda d:next(n for n in d['nets'] if n['id']=='GND')['terminals'].remove(terminal),
            lambda d:next(n for n in d['nets'] if n['id']=='V3V3')['terminals'].append(terminal),
            lambda d:next(n for n in d['nets'] if n['id']=='GND')['terminals'].append(terminal),
            lambda d:d['no_connects'].append(terminal)]
        for index,change in enumerate(changes):
            owned=self.root/str(index);owned.mkdir();doc=copy.deepcopy(original);change(doc)
            storage.write_json(owned/'circuit.json',doc);before=(owned/'circuit.json').read_bytes()
            with self.subTest(index=index),self.assertRaises(ValueError):acquisition_prepare._select_addr(owned,'V3V3')
            self.assertEqual((owned/'circuit.json').read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'addr_tie'):self.prepare('unknown','SDA')
        self.assertFalse((self.root/'unknown').exists())

    def test_context_pin_recipe_and_anchor_refusals_publish_nothing(self):
        import copy
        for index,options in enumerate(({'evaluation_context_sha256':'0'*64},{'evaluation_context_sha256':None},
                {'alternative_context':None},{'review_profile':None})):
            with self.subTest(options=options),self.assertRaises(ValueError):self.prepare('bad-'+str(index),**options)
            self.assertFalse((self.root/('bad-'+str(index))).exists())
        prior=json.loads((self.seed/'preparation.json').read_bytes())
        for index,anchor in enumerate(('nonsense','2999-01-01T00:00:00+00:00','2026-10-29T03:10:37.069958+00:00')):
            report=copy.deepcopy(prior);report['recipe']['alternative_context']['evaluation_as_of']=anchor
            report['identity']=spec_record.sha256(storage.json_bytes(report['recipe']))
            context=self.root/('context-'+str(index)+'.json');storage.write_json(context,report)
            with self.subTest(anchor=anchor),self.assertRaises(ValueError):
                self.prepare('bad-anchor-'+str(index),evaluation_context=context,evaluation_context_sha256=storage.digest(context))
            self.assertFalse((self.root/('bad-anchor-'+str(index))).exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')),[])

    def test_replayed_preparation_context_does_not_backdate_run_bound_stock_freshness(self):
        from agent_ee import assembly_readiness as stock
        from types import SimpleNamespace
        from datetime import datetime,timedelta
        out,report=self.prepare('run-time');source=out/'inputs'
        params=acquisition_prepare.acquisition_alternative._assembly_check(json.loads((source/'requirement-map.json').read_bytes()))['params']
        work=self.root/'stock-work';work.mkdir();run=self.root/'stock-run';run.mkdir()
        later=(datetime.fromisoformat(report['recipe']['alternative_context']['evaluation_as_of'])+timedelta(days=2)).isoformat()
        storage.write_json(run/'result.json',{'started_utc':later})
        ctx=SimpleNamespace(params=params,inputs=source,context={},run_dir=run,work=work,result={'details':{}},evidence=lambda p:None)
        actual=stock.stock_evaluation_params(ctx);self.assertEqual(actual['as_of'],later);self.assertLessEqual(actual['max_age_hours'],24)
        catalog=json.loads((source/actual['catalog']).read_bytes())
        info=stock._verify_jlc_capture(source,actual['catalog'],catalog)
        with self.assertRaisesRegex(ValueError,'stale'):stock._capture_gate(info,stock.validate_params(actual),set())

    def test_context_filesystem_drift_or_existing_output_preserves_originals(self):
        context=self.root/'owned-context.json';context.write_bytes((self.seed/'preparation.json').read_bytes())
        write=Path.write_bytes
        def drift(path,raw):
            if path.name=='preparation.json':write(context,context.read_bytes()+b' ')
            return write(path,raw)
        with patch.object(Path,'write_bytes',drift),self.assertRaisesRegex(ValueError,'context changed'):
            self.prepare('drift',evaluation_context=context,evaluation_context_sha256=storage.digest(context))
        self.assertFalse((self.root/'drift').exists());self.assertEqual(list(self.root.glob('*.prepare-*')),[])
        with self.assertRaises(ValueError):self.prepare('seed','V3V3')
        self.assertEqual(requirement_map.input_inventory(BASE),self.before)

    def test_compiled_baseline_detects_mutation_of_each_fixed_dependency_family(self):
        out,_=self.prepare('sensitivity');_,baseline=self.compiled(out,'sensitivity')
        document=json.loads((out/'inputs/parts.json').read_bytes())
        decisions=sorted({part['selection']['decision_ref']['path'] for part in document['parts']
                          if 'decision_ref' in part.get('selection',{})})
        self.assertTrue(decisions);self.assertTrue(set(decisions)<=baseline['files'].keys())
        paths=['adc-ties.json','analysis/electrical-intervals.json',
               'fixedexpected/connectivity-intent-addr-gnd.json',*decisions]
        paths.append(next(p for p in baseline['files'] if p.endswith('.lib')))
        for index,name in enumerate(paths):
            path=out/'inputs'/name;before=path.read_bytes();path.write_bytes(before+b'\n')
            try:
                with self.subTest(path=name):
                    try:_,changed=self.compiled(out,'mutated-'+str(index))
                    except ValueError:pass
                    else:self.assertNotEqual(changed,baseline)
            finally:path.write_bytes(before)

    def test_fixed_expected_pins_mutable_paths_and_control_context_refuse(self):
        import copy
        for index,opts in enumerate(({'expected_intent_sha256':'0'*64},
                {'expected_parts_receipt_sha256':'0'*64},{'expected_parts_receipt':None},
                {'expected_intent':self.seed/'inputs/circuit.json',
                 'expected_intent_sha256':storage.digest(self.seed/'inputs/circuit.json')})):
            with self.subTest(options=opts),self.assertRaises(ValueError):self.prepare('bad-fixed-'+str(index),**opts)
            self.assertFalse((self.root/('bad-fixed-'+str(index))).exists())
        for index,prefix in enumerate(('project','build-generated')):
            path=self.root/prefix/'intent.json';path.parent.mkdir();path.write_bytes(self.expected.read_bytes())
            with self.assertRaisesRegex(ValueError,'exact successful ordinary control parts output'):
                self.prepare('mutable-'+str(index),expected_intent=path)
            self.assertFalse((self.root/('mutable-'+str(index))).exists())
        foreign=self.root/'foreign-control';shutil.copytree(self.control_inputs,foreign)
        acquisition_prepare._select_addr(foreign,'V3V3')
        retained,receipt=self.parts_receipt(foreign,'foreign-control')
        with self.assertRaisesRegex(ValueError,'control dependency mismatch: circuit.json'):
            self.prepare('wrong-control',expected_intent=retained/'run/out/connectivity-intent.json',
                expected_intent_sha256=receipt['outputs']['build-generated/connectivity-intent.json'],
                expected_parts_receipt=retained/'receipt.json',expected_parts_receipt_sha256=storage.digest(retained/'receipt.json'))
        self.assertFalse((self.root/'wrong-control').exists())
        report=json.loads((self.seed/'preparation.json').read_bytes())
        report['inputs']['parts.json']='0'*64  # changed independently pinned control witness
        changed=self.root/'different-control.json';storage.write_json(changed,report)
        with self.assertRaisesRegex(ValueError,'different captured preparation context'):
            self.prepare('foreign',evaluation_context=changed,evaluation_context_sha256=storage.digest(changed))
        self.assertFalse((self.root/'foreign').exists())
        raw=self.expected.read_bytes();self.expected.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError,'expected intent hash mismatch'):self.prepare('tampered')
        self.assertFalse((self.root/'tampered').exists());self.expected.write_bytes(raw)
        linked=self.root/'link.json';linked.symlink_to(self.expected)
        with self.assertRaises((ValueError,OSError)):self.prepare('linked',expected_intent=linked)
        self.assertFalse((self.root/'linked').exists())
        write=Path.write_bytes
        def late_drift(path,data):
            if path.name=='preparation.json':write(self.expected,self.expected.read_bytes()+b' ')
            return write(path,data)
        with patch.object(Path,'write_bytes',late_drift),self.assertRaisesRegex(ValueError,'expected intent hash mismatch'):
            self.prepare('late-expected-drift')
        self.assertFalse((self.root/'late-expected-drift').exists());self.expected.write_bytes(raw)

    def test_fresh_thermal_source_phase_binds_admitted_text_before_overlay_copy(self):
        from agent_ee import acquisition_power_repair as repair
        self.assertFalse((BASE/'manufacturer-text').exists())
        output620=self.root/'fresh620'
        sources={**self.options['added_sources'],'power-repair':Path(os.environ['AGENT_EE_ADDR_POWER620'])}
        acquisition_prepare.prepare(self.brief,PLAN,output620,**{**self.options,'added_sources':sources})
        for out in (self.seed,output620):
            with self.subTest(out=out.name):
                inputs=out/'inputs'
                calc=json.loads((inputs/'engineering-calculations.json').read_bytes())
                for name,sha in repair.THERMAL_SOURCES.items():
                    self.assertEqual(storage.digest(inputs/name),sha)
                    self.assertEqual(calc['sources'][name],sha)
                interval=json.loads((inputs/'analysis/electrical-intervals.json').read_bytes())
                self.assertEqual(len(interval['assertions']),14)
                screens=[a for a in interval['assertions'] if a['id']==repair.THERMAL_SCREEN]
                self.assertEqual(len(screens),1);self.assertEqual(screens[0]['requirement'],'ACQ-ELEC-2-RAIL')
                self.assertEqual(screens[0]['target'],dict(min='0 W',max='0.53447 W'))
                question=json.loads((out/'overlay.json').read_bytes())['judgments']['judgments/acq-elec-2-source-evaluation.json']
                self.assertTrue(set(repair.THERMAL_SOURCES)<=set(question['sources']))
                declaration=json.loads((inputs/'requirement-map.json').read_bytes())
                spans=[span['mapping'] for entry in declaration['requirements'] for span in entry.get('spans',[])]
                self.assertEqual([s for s in spans if s['id']=='ACQ-ELEC-2-SOURCE-EVALUATION'],
                    [dict(id='ACQ-ELEC-2-SOURCE-EVALUATION',judgment=dict(record='judgments/acq-elec-2-source-evaluation.json'))])
                if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
                    evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
                    shutil.copytree(out,evidence/('thermal-'+out.name))
        admitted={n.removeprefix('manufacturer-text/'):sha for n,sha in repair.THERMAL_SOURCES.items()
                  if n.startswith('manufacturer-text/')}
        for changed in ({}, {**admitted,'tps7a20.txt':'0'*64}):
            with self.assertRaisesRegex(ValueError,'captured TI thermal source differs'):
                repair._thermal_sources(BASE,changed)

        owned_text=self.root/'owned-text';shutil.copytree(TEXT,owned_text)
        text=owned_text/'tps7a20.txt';write=Path.write_bytes;changed=[]
        def drift_after_admission(path,data):
            if path.name=='engineering-calculations.json' and not changed:
                changed.append(True);write(text,text.read_bytes()+b' ')
            return write(path,data)
        additions={**self.options['added_sources'],'manufacturer-text':owned_text}
        with patch.object(Path,'write_bytes',drift_after_admission),self.assertRaisesRegex(ValueError,'differs from the declared sha256'):
            acquisition_prepare.prepare(self.brief,PLAN,self.root/'late-text-drift',
                **{**self.options,'added_sources':additions})
        self.assertTrue(changed);self.assertFalse((self.root/'late-text-drift').exists())
        self.assertEqual(requirement_map.input_inventory(BASE),self.before)
