"""Pure cleanup probes: native test bodies and imports never execute here."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tests.artifact_retention import RetainedTemporaryDirectory, retain


ROOT = Path(__file__).parent
CONTEXTS = (
    ('test_acquisition_geometry_contact', 'SavedBoardGeometryTests',
     'test_corrected_witnesses_meet_original_limits_without_changing_the_board'),
    ('test_acquisition_geometry_contact', 'SeededGroundReferenceTests', 'setUp'),
    ('test_acquisition_reverse_blocking', 'NativeFootprintTests',
     'test_complete_supplier_matches_native_saved_copper_holes_and_pin_frame'),
    ('test_acquisition_reverse_blocking', 'NativeFootprintTests',
     'test_generated_footprint_preserves_all_copper_and_drills'),
    ('test_acquisition_reverse_blocking', 'NativeFootprintTests',
     'test_generated_diode_preserves_supplier_copper_and_polarity'),
    ('test_acquisition_reverse_blocking', 'NativeAcquisitionTests',
     'test_generated_seventeen_part_schematic_has_native_pinout_and_clean_erc'),
    ('test_acquisition_stencil', 'NativeStencilTests',
     'test_exported_paste_is_segmented_and_clear_of_every_drill'),
)


def selected_cleanup(site):
    module, owner, method = site
    tree = ast.parse((ROOT / (module + '.py')).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
    function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
    constructors = [node for node in ast.walk(function) if isinstance(node, ast.Call) and (
        isinstance(node.func, ast.Name) and node.func.id == 'RetainedTemporaryDirectory' or
        isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'tempfile' and node.func.attr == 'TemporaryDirectory')]
    if len(constructors) != 1:
        raise AssertionError((site, 'expected one owned context', len(constructors)))
    cleanups = [node for node in ast.walk(function) if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'self'
        and node.value.func.attr == 'addCleanup' and node.value.args and (
            isinstance(node.value.args[0], ast.Name) and node.value.args[0].id == 'retain' or
            isinstance(node.value.args[0], ast.Attribute) and isinstance(node.value.args[0].value, ast.Name)
            and node.value.args[0].value.id == 'shutil' and node.value.args[0].attr == 'rmtree')]
    enter_context = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'enterContext' for node in ast.walk(function))
    return constructors[0], cleanups, enter_context


class AcquisitionRetentionTests(unittest.TestCase):
    def exercise(self, site, error_type, *, sibling):
        constructor, cleanup_nodes, enter_context = selected_cleanup(site)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receipt = root / 'receipt.json'
            receipt.write_bytes(b'{"schema":"authored-pure-cleanup-receipt/1"}\n')
            evidence = root / 'evidence'
            owned = []
            binary = b'original diagnostic\x00\xff\r\n'
            namespace = {'tempfile': tempfile, 'RetainedTemporaryDirectory': RetainedTemporaryDirectory,
                         'retain': retain, 'shutil': shutil}

            class Probe(unittest.TestCase):
                def id(self):
                    return 'pure-cleanup-probe.' + '.'.join(site) + '.' + error_type.__name__

                def body(self, path):
                    source = Path(path)
                    owned.append(source)
                    (source / 'input.json').write_text('{"provenance":"harmless cleanup probe, not native evidence"}')
                    (source / 'stderr.bin').write_bytes(binary)
                    if sibling:
                        work = root / 'native-work-probe'
                        work.mkdir()
                        owned.append(work)
                        (work / 'report.json').write_text('{"status":"deliberate refusal"}')
                        (work / 'stdout.bin').write_bytes(binary)
                        # Execute only the source's actual registrations in their original order.
                        code = ast.fix_missing_locations(ast.Module(body=copy.deepcopy(cleanup_nodes), type_ignores=[]))
                        exec(compile(code, '<selected cleanup registrations>', 'exec'), namespace,
                             {'self': self, 'work': work})
                    raise error_type('original deliberate probe failure')

                def runTest(self):
                    expression = ast.Expression(copy.deepcopy(constructor))
                    context = eval(compile(ast.fix_missing_locations(expression), '<selected context>', 'eval'),
                                   namespace, {'self': self})
                    if enter_context:
                        self.body(self.enterContext(context))
                    else:
                        with context as path:
                            self.body(path)

            with patch.dict(os.environ, {'AGENT_EE_EVIDENCE_DIR': str(evidence),
                                        'AGENT_EE_RUN_INPUT_RECEIPT': str(receipt)}):
                result = unittest.TestResult()
                probe = Probe()
                probe.run(result)
            diagnostics = result.failures if error_type is AssertionError else result.errors
            self.assertEqual(len(diagnostics), 1)
            self.assertIn('original deliberate probe failure', diagnostics[0][1])
            self.assertEqual(len(result.errors), int(error_type is RuntimeError))
            self.assertEqual(len(result.failures), int(error_type is AssertionError))
            self.assertTrue(all(not path.exists() for path in owned))
            records = list(evidence.rglob('evidence.json'))
            self.assertEqual(len(records), 2 if sibling else 1)
            retained = {}
            for path in records:
                record = json.loads(path.read_bytes())
                self.assertEqual(record['test_id'], probe.id())
                self.assertEqual(record['run_input_receipt_sha256'], hashlib.sha256(receipt.read_bytes()).hexdigest())
                self.assertEqual((path.parent / 'run-inputs.json').read_bytes(), receipt.read_bytes())
                for row in record['roots']:
                    retained[Path(row['source'])] = path.parent / row['retained']
            self.assertEqual((retained[owned[0]] / 'stderr.bin').read_bytes(), binary)
            if sibling:
                self.assertEqual((retained[owned[1]] / 'stdout.bin').read_bytes(), binary)
                self.assertEqual(json.loads((retained[owned[1]] / 'report.json').read_bytes()),
                                 {'status': 'deliberate refusal'})
            retain(self.id(), {'scope': 'pure cleanup proof, not native execution',
                              'source_site': '.'.join(site), 'failure': error_type.__name__}, roots=[evidence])

    def test_source_contexts_retain_on_assertion_and_exception(self):
        for site in CONTEXTS:
            for error in (AssertionError, RuntimeError):
                with self.subTest(site=site, error=error.__name__):
                    self.exercise(site, error, sibling=False)

    def test_actual_sibling_cleanup_order_retains_before_delete(self):
        for site in (CONTEXTS[2], CONTEXTS[-1]):
            for error in (AssertionError, RuntimeError):
                with self.subTest(site=site, error=error.__name__):
                    self.exercise(site, error, sibling=True)
