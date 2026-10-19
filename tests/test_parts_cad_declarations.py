"""Real ESP32 CAD composition; supplier identity remains a synthetic fixture."""
import copy
from pathlib import Path
import tempfile
import unittest

from agent_ee.footprint_library import capture_esp32_s3_wroom_1
from agent_ee.parts_manifest import dependency_closure, qualify_manifest
from agent_ee.storage import digest, identity, write_json
from test_parts_subset import captured_fixture
from test_approved_parts import named_fixture
from toolchain import require_toolchain


class PartsCadDeclarationsTests(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = captured_fixture(self, self.root)
        named_fixture(self.root, self.document, mpn='synthetic ESP32 identity')
        cap = capture_esp32_s3_wroom_1(self.root / 'esp32-source')
        for path, raw in cap['captured_files'].items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        assignments = copy.deepcopy(cap['assignments'])
        assignments['components'][0]['reference'] = 'resistor'
        self.document['parts'][0].update(lib_id='RF_Module:ESP32-S3-WROOM-1',
                                        footprint='RF_Module:ESP32-S3-WROOM-1')
        members = [m for lib in cap['manifest']['libraries'] for m in lib['members']]
        members += [{k: lib[k] for k in ('path', 'sha256')} for lib in assignments['libraries']]
        self.document['cad'] = dict(manifest=self.put('esp32-manifest.json', cap['manifest']),
            assignments=self.put('esp32-assignments.json', assignments),
            declarations=self.put('esp32-declarations.json', cap['declarations']),
            members_sha256=identity(sorted(members, key=lambda m: m['path'])))
        self.declarations = cap['declarations']

    def put(self, path, value):
        write_json(self.root / path, value)
        return dict(path=path, sha256=digest(self.root / path))

    def test_actual_pad41_composes_and_missing_declaration_still_refuses(self):
        before = copy.deepcopy(self.document)
        result = qualify_manifest(self.document, self.root)
        self.assertEqual(self.document, before)
        self.assertEqual((result['cad']['execution'], result['cad']['outcome'], result['cad']['coverage']),
                         ('ok', 'pass', 'complete'))
        fp = result['cad']['index']['footprints'][0]
        vias = [p for p in fp['pads'] if p.get('thermal_via')]
        self.assertEqual(len(vias), 12)
        self.assertEqual({p['number'] for p in vias}, {'41'})
        self.assertEqual(result['cad']['assignments'][0]['pin_numbers'], sorted(str(i) for i in range(1, 42)))
        self.assertEqual(result['dependencies']['esp32-declarations.json'],
                         self.document['cad']['declarations']['sha256'])
        del self.document['cad']['declarations']
        with self.assertRaisesRegex(ValueError, 'captured CAD assignments'):
            qualify_manifest(self.document, self.root)

    def test_declaration_custody_is_replayed_and_missing_bytes_refuse(self):
        expected = dependency_closure(self.document, self.root)
        path = self.root / 'esp32-declarations.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            dependency_closure(self.document, self.root)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            qualify_manifest(self.document, self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            qualify_manifest(self.document, self.root)
        path.write_bytes(original)
        self.assertEqual(dependency_closure(self.document, self.root), expected)

    def test_repinned_false_declaration_does_not_exempt_copper(self):
        for pads in ([], ['42']):
            with self.subTest(pads=pads):
                altered = copy.deepcopy(self.declarations)
                altered['footprints'][0]['thermal_via_pads'] = pads
                self.document['cad']['declarations'] = self.put('esp32-declarations.json', altered)
                with self.assertRaisesRegex(ValueError, 'captured CAD assignments'):
                    qualify_manifest(self.document, self.root)
        self.document['cad']['declarations'] = None
        with self.assertRaises(ValueError):
            qualify_manifest(self.document, self.root)
