"""ESP32-S3-WROOM-1 footprint capture, thermal-via declaration, datasheet facts, and owned antenna keepout probe."""
import copy
import hashlib
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import executor, symbol_library
from agent_ee.candidate import capture as capture_candidate
from agent_ee.checks import run_checks
from agent_ee.datasheet_facts import load_facts
from agent_ee.footprint_library import (
    ESP32_DATASHEET_PATH,
    ESP32_DATASHEET_SHA256,
    ESP32_FOOTPRINT_ID,
    ESP32_FOOTPRINT_PATH,
    ESP32_FOOTPRINT_SHA256,
    ESP32_KEEPOUT_FACT_IDS,
    ESP32_SYMBOL_SUBSET_SHA256,
    FootprintError,
    capture_esp32_s3_wroom_1,
    check_assignments,
    derive_antenna_keepout,
    esp32_s3_wroom_1_assignments,
    esp32_s3_wroom_1_datasheet_facts,
    esp32_s3_wroom_1_declarations,
    esp32_s3_wroom_1_manifest,
    index_footprints,
    probe_rule_areas,
    validate_declarations,
)
from agent_ee.storage import digest, read_json, write_json
try:
    from toolchain import require_toolchain
except ModuleNotFoundError:
    from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / 'fixtures/telemetry-pod/esp32-s3-wroom-1'


def multipage_text_pdf(path, page_count, target_page, lines):
    """Build an owned minimal multi-page PDF with numerical tokens on target_page."""
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        None,
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    kids = []
    for page_num in range(1, page_count + 1):
        page_lines = lines if page_num == target_page else [str(page_num)]
        stream_text = ''.join(
            f'BT /F1 10 Tf 20 {180 - 14 * i} Td ({line}) Tj ET\n'
            for i, line in enumerate(page_lines)
        ).encode('latin-1')
        content_obj_num = len(objects) + 2
        page_obj_num = len(objects) + 1
        kids.append(f'{page_obj_num} 0 R')
        objects.append(
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] '
            f'/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj_num} 0 R >>'.encode()
        )
        objects.append(
            b'<< /Length %d >>\nstream\n' % len(stream_text) + stream_text + b'\nendstream'
        )
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {page_count} >>'.encode()
    data = b'%PDF-1.4\n'
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    data += (
        f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
        f'startxref\n{start}\n%%EOF\n'
    ).encode()
    path.write_bytes(data)


class Esp32FootprintKeepoutTests(unittest.TestCase):
    def test_committed_metadata_fixtures_match_canonical_generators(self):
        self.assertEqual(read_json(FIXTURE_DIR / 'footprints.manifest.json'), esp32_s3_wroom_1_manifest())
        self.assertEqual(read_json(FIXTURE_DIR / 'footprint-declarations.json'), esp32_s3_wroom_1_declarations())
        self.assertEqual(read_json(FIXTURE_DIR / 'assignments.json'), esp32_s3_wroom_1_assignments())
        self.assertEqual(read_json(FIXTURE_DIR / 'datasheet-facts.json'), esp32_s3_wroom_1_datasheet_facts())
        stock_fp = Path('/usr/share/kicad/footprints') / ESP32_FOOTPRINT_PATH
        self.assertTrue(stock_fp.is_file())
        declared = validate_declarations(esp32_s3_wroom_1_declarations())[0]
        expected_probe = probe_rule_areas(stock_fp.read_bytes(), declared=declared)
        self.assertEqual(read_json(FIXTURE_DIR / 'rule-area.json'), expected_probe)

    def test_footprint_assignment_passes_with_declaration_and_reproduces_failure_without(self):
        require_toolchain()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap_dir = root / 'captured'
            captured = capture_esp32_s3_wroom_1(cap_dir)

            # Criterion 1(a): Direct check_assignments passes with thermal_via_pads=["41"] declaration
            passed = check_assignments(
                captured['manifest'],
                captured['captured_files'],
                captured['assignments'],
                declarations=captured['declarations'],
            )
            self.assertEqual((passed['execution'], passed['outcome'], passed['coverage']), ('ok', 'pass', 'complete'))
            self.assertEqual(passed['findings'], [])
            assignment = passed['assignments'][0]
            self.assertEqual(assignment['reference'], 'U1')
            self.assertEqual(assignment['lib_id'], ESP32_FOOTPRINT_ID)
            self.assertEqual(assignment['footprint'], ESP32_FOOTPRINT_ID)
            self.assertEqual(assignment['status'], 'pass')
            self.assertEqual(assignment['footprint_declaration']['thermal_via_pads'], ['41'])
            self.assertEqual(assignment['mechanical_pads'], [])
            expected_pins = sorted(str(i) for i in range(1, 42))
            self.assertEqual(assignment['pin_numbers'], expected_pins)
            self.assertEqual(len(assignment['rule_areas']), 1)

            fp = passed['index']['footprints'][0]
            self.assertEqual(fp['electrical_numbers'], expected_pins)
            self.assertEqual(len(fp['pads']), 62)
            vias = [p for p in fp['pads'] if p.get('thermal_via')]
            self.assertEqual(len(vias), 12)
            self.assertTrue(all(p['number'] == '41' and p['type'] == 'thru_hole' and p['role'] == 'electrical' for p in vias))
            paste = [p for p in fp['pads'] if p['role'] == 'paste']
            self.assertEqual(len(paste), 9)
            self.assertTrue(all(p['number'] == '' and p['type'] == 'smd' for p in paste))
            smd_elec = [p for p in fp['pads'] if p['role'] == 'electrical' and p['type'] == 'smd']
            self.assertEqual(len(smd_elec), 41)

            # Criterion 1(b): Without the thermal_via_pads declaration, the existing failure is reproduced
            failed = check_assignments(
                captured['manifest'],
                captured['captured_files'],
                captured['assignments'],
            )
            self.assertEqual((failed['execution'], failed['outcome'], failed['coverage']), ('failed', 'unknown', 'missing'))
            self.assertEqual(len(failed['findings']), 1)
            self.assertEqual(failed['findings'][0]['code'], 'coverage')
            self.assertIn('mixed assembly semantics are not qualified', failed['findings'][0]['message'])

            # Criterion 1(c): Sandboxed footprint_assignment check passes with declaration and fails without
            overlay = root / 'app'
            shutil.copytree(ROOT / 'src', overlay)
            shutil.copyfile(symbol_library.__file__, overlay / 'agent_ee/symbol_library.py')
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)

            source = root / 'source'
            shutil.copytree(cap_dir, source)
            manifest = copy.deepcopy(captured['manifest'])
            for member in manifest['libraries'][0]['members']:
                member['path'] = 'data/' + member['path']
            assignments = copy.deepcopy(captured['assignments'])
            assignments['libraries'][0]['path'] = 'data/' + assignments['libraries'][0]['path']
            write_json(source / 'footprints.manifest.json', manifest)
            write_json(source / 'assignments.json', assignments)
            for name in ('board', 'schematic', 'req', 'target', 'reference'):
                (source / name).write_text('Owned ESP32 assignment fixture: ' + name)

            base_params = dict(
                manifest='data/footprints.manifest.json',
                declaration='data/assignments.json',
                assembly_intent=[dict(reference='U1', assembly='smd', connector=None)],
            )
            for label, extra, expected in (
                ('with_decl', {'declarations': 'data/footprint-declarations.json'}, ('ok', 'pass')),
                ('without_decl', {}, ('failed', 'unknown')),
            ):
                scope = dict(
                    schema='agent-ee.scope/3',
                    name=f'esp32-{label}',
                    scope='ESP32-S3-WROOM-1 footprint assignment parity',
                    board='data/board',
                    schematic='data/schematic',
                    inputs=[dict(path='source', at='data')],
                    checks=[dict(id='fp', kind='footprint_assignment', params=dict(base_params, **extra))],
                    baseline=dict(
                        requirements=[dict(
                            id='R1',
                            intent='acceptance',
                            statement='ESP32-S3-WROOM-1 footprint satisfies pin parity and thermal-via declaration',
                            source=dict(path='data/req', locator='R1'),
                            checks=['fp'],
                        )],
                        targets=['data/target'],
                        reference_architectures=['data/reference'],
                    ),
                )
                write_json(root / f'scope_{label}.json', scope)
                run_dir = root / f'run_{label}'
                run_dir.mkdir()
                capture_candidate(root / f'scope_{label}.json', run_dir)
                results = run_checks(run_dir, time.monotonic() + 60)
                self.assertEqual((results[0]['execution'], results[0]['outcome']), expected, results)

    def test_datasheet_facts_cite_page_and_figure_and_derive_keepout_polygon(self):
        require_toolchain(tools=('pdftotext',))
        raw = read_json(FIXTURE_DIR / 'datasheet-facts.json')
        loaded = load_facts(FIXTURE_DIR, 'datasheet-facts.json')
        self.assertEqual([f['id'] for f in loaded], list(ESP32_KEEPOUT_FACT_IDS))
        for fact in loaded:
            self.assertEqual(fact['part_number'], 'ESP32-S3-WROOM-1')
            self.assertEqual(fact['kind'], 'keepout_dimension')
            self.assertEqual(fact['unit'], 'mm')
            self.assertEqual(fact['source']['path'], ESP32_DATASHEET_PATH)
            self.assertEqual(fact['source']['sha256'], ESP32_DATASHEET_SHA256)
            self.assertEqual(fact['source']['page'], 45)
            self.assertIn('Section 11.1 PCB Land Pattern', fact['source']['locator'])
            self.assertIn('Figure 11-1', fact['source']['locator'])
            self.assertEqual(fact['source']['quote'], fact['value'])

        derived = derive_antenna_keepout(raw)
        self.assertEqual(derived['schema'], 'agent-ee.antenna-keepout-derivation/1')
        self.assertEqual(derived['part_number'], 'ESP32-S3-WROOM-1')
        self.assertEqual(derived['page'], 45)
        self.assertEqual(derived['figure'], 'Figure 11-1')
        self.assertEqual(derived['source_sha256'], ESP32_DATASHEET_SHA256)
        self.assertEqual(
            derived['dimensions_mm'],
            {'body_width': '18', 'body_length': '25.5', 'antenna_depth': '6', 'keepout_clearance': '15'},
        )
        self.assertEqual(
            derived['polygon_mm'],
            [['-24', '-6.75'], ['24', '-6.75'], ['24', '-27.75'], ['-24', '-27.75']],
        )
        self.assertEqual(
            derived['polygon_nm'],
            [[-24000000, -6750000], [24000000, -6750000], [24000000, -27750000], [-24000000, -27750000]],
        )
        self.assertEqual(derived['bounds_mm'], {'min': ['-24', '-27.75'], 'max': ['24', '-6.75']})

        # Verify sandboxed datasheet_facts check and derivation from verified fact report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / 'esp32_page45.pdf'
            multipage_text_pdf(pdf_path, 45, 45, ['18', '25.5', '6', '15'])
            pdf_sha = digest(pdf_path)
            facts_doc = esp32_s3_wroom_1_datasheet_facts(
                pdf_path='datasheets/esp32_page45.pdf',
                pdf_sha256=pdf_sha,
            )
            write_json(root / 'facts.json', facts_doc)
            version = subprocess.run(
                ['/usr/bin/pdftotext', '-v'], capture_output=True, text=True, check=True
            ).stderr.splitlines()[0]
            scope = {
                'schema': 'agent-ee.scope/1',
                'name': 'esp32 keepout datasheet facts',
                'scope': 'ESP32-S3-WROOM-1 keepout dimensions on page 45 Figure 11-1',
                'inputs': [
                    {'path': str(pdf_path), 'at': 'datasheets/esp32_page45.pdf'},
                    {'path': str(root / 'facts.json'), 'at': 'facts.json'},
                ],
                'checks': [{
                    'id': 'facts',
                    'kind': 'datasheet_facts',
                    'params': {
                        'facts': 'facts.json',
                        'extractor': {
                            'path': '/usr/bin/pdftotext',
                            'sha256': digest('/usr/bin/pdftotext'),
                            'version': version,
                        },
                    },
                }],
            }
            write_json(root / 'scope.json', scope)
            run_dir = root / 'run'
            run_dir.mkdir()
            capture_candidate(root / 'scope.json', run_dir)
            results = run_checks(run_dir, time.monotonic() + 60)
            self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', 'pass'), results)
            self.assertEqual(derive_antenna_keepout(results[0]['details']['facts'])['polygon_mm'], derived['polygon_mm'])

        # Failure boundaries for derive_antenna_keepout
        missing = copy.deepcopy(raw)
        missing['facts'] = missing['facts'][:-1]
        with self.assertRaisesRegex(FootprintError, 'missing ESP32 antenna keepout facts'):
            derive_antenna_keepout(missing)

        no_fig = copy.deepcopy(raw)
        no_fig['facts'][0]['source']['locator'] = 'Section 11.1 PCB Land Pattern'
        with self.assertRaisesRegex(FootprintError, 'Figure 11-1'):
            derive_antenna_keepout(no_fig)

        diff_page = copy.deepcopy(raw)
        diff_page['facts'][0]['source']['page'] = 44
        with self.assertRaisesRegex(FootprintError, 'one datasheet page'):
            derive_antenna_keepout(diff_page)

        unverified = [{'id': f['id'], 'status': 'mismatch', 'fact': f} for f in raw['facts']]
        with self.assertRaisesRegex(FootprintError, 'not verified'):
            derive_antenna_keepout(unverified)

    def test_probe_lists_footprint_owned_rule_area_and_matches_pcbnew_and_datasheet(self):
        require_toolchain(native=True)
        import pcbnew

        stock_fp = Path('/usr/share/kicad/footprints') / ESP32_FOOTPRINT_PATH
        fp_bytes = stock_fp.read_bytes()
        self.assertEqual(hashlib.sha256(fp_bytes).hexdigest(), ESP32_FOOTPRINT_SHA256)

        declared = validate_declarations(esp32_s3_wroom_1_declarations())[0]
        probe = probe_rule_areas(fp_bytes, declared=declared)
        self.assertEqual(probe['schema'], 'agent-ee.footprint-rule-areas/1')
        self.assertEqual(probe['lib_id'], ESP32_FOOTPRINT_ID)
        self.assertEqual(probe['name'], 'ESP32-S3-WROOM-1')
        self.assertEqual(probe['source'], {'path': ESP32_FOOTPRINT_PATH, 'sha256': ESP32_FOOTPRINT_SHA256})
        self.assertEqual(len(probe['rule_areas']), 1)

        area = probe['rule_areas'][0]
        expected_layers = ['F.Cu', 'B.Cu'] + [f'In{i}.Cu' for i in range(1, 31)]
        self.assertEqual(area['uuid'], '18bbecf2-2977-41e2-b949-929efe1bdbf7')
        self.assertEqual(area['layers'], expected_layers)
        self.assertEqual(area['forbid'], ['footprints', 'tracks', 'vias', 'pads', 'copper_pours'])
        self.assertEqual(
            area['keepout'],
            {
                'tracks': 'not_allowed',
                'vias': 'not_allowed',
                'pads': 'not_allowed',
                'copperpour': 'not_allowed',
                'footprints': 'not_allowed',
            },
        )
        self.assertEqual(
            area['polygon_mm'],
            [['-24', '-6.75'], ['24', '-6.75'], ['24', '-27.75'], ['-24', '-27.75']],
        )
        self.assertEqual(area['bounds_mm'], {'min': ['-24', '-27.75'], 'max': ['24', '-6.75']})

        # Verify exact match with datasheet-derived keepout polygon
        derived = derive_antenna_keepout(read_json(FIXTURE_DIR / 'datasheet-facts.json'))
        self.assertEqual(area['polygon_nm'], derived['polygon_nm'])
        self.assertEqual(area['polygon_mm'], derived['polygon_mm'])
        self.assertEqual(area['bounds_mm'], derived['bounds_mm'])

        # Cross-check against native pcbnew FootprintLoad
        native = pcbnew.FootprintLoad(str(stock_fp.parent), 'ESP32-S3-WROOM-1')
        self.assertIsNotNone(native)
        zones = list(native.Zones())
        self.assertEqual(len(zones), 1)
        z = zones[0]
        self.assertTrue(z.GetIsRuleArea())
        self.assertTrue(z.GetDoNotAllowTracks())
        self.assertTrue(z.GetDoNotAllowVias())
        self.assertTrue(z.GetDoNotAllowPads())
        self.assertTrue(z.GetDoNotAllowZoneFills())
        self.assertTrue(z.GetDoNotAllowFootprints())
        outline = z.Outline()
        self.assertEqual(outline.OutlineCount(), 1)
        pts = outline.Outline(0)
        native_pts_nm = [[pts.CPoint(i).x, pts.CPoint(i).y] for i in range(pts.PointCount())]
        self.assertEqual(area['polygon_nm'], native_pts_nm)


if __name__ == '__main__':
    unittest.main()
