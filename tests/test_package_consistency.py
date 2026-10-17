"""Native footprint/LCSC package names against a pinned catalog and captured table."""
import copy
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from agent_ee.baseline import freeze_evidence, load_baseline, validate_witnesses, verify_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.catalog import capture_catalog
from agent_ee.checks import CheckError, Ctx, parse_xml_netlist, run_checks
from agent_ee.package_consistency import check_package_consistency, evaluate, load_table, validate_table
from agent_ee.storage import canonical, digest, read_json, write_json
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'references/package-map/kicad-10.0.7-lcsc-1.json'
FIXTURE = ROOT / 'fixtures/package-consistency'


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


generator = module(FIXTURE / 'generate.py', 'package_fixture')
table_generator = module(ROOT / 'references/package-map/generate.py', 'package_map_generator')


class TableTests(unittest.TestCase):
    def test_committed_table_names_ticket_examples_explicitly(self):
        table = load_table(TABLE)
        footprints, packages = table['footprints'], table['packages']
        self.assertEqual(footprints['Capacitor_SMD:C_0402_1005Metric'],
            {'rule': 'chip-imperial', 'classes': ['capacitor'], 'package': {'family': 'chip', 'pins': 2, 'body': '0402'}})
        self.assertEqual(packages['0402']['package'], footprints['Capacitor_SMD:C_0402_1005Metric']['package'])
        self.assertEqual(footprints['Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.6x5.6mm']['package'],
                         packages['UFQFPN-48(7x7)']['package'])
        self.assertEqual(footprints['Package_TO_SOT_SMD:SOT-23']['package']['pins'], 3)
        self.assertEqual(packages['SOT-23-5']['package']['pins'], 5)
        self.assertEqual(footprints['Resistor_SMD:R_0603_1608Metric']['classes'], ['resistor'])
        for observed in table['derivation']['observations']:
            self.assertIn(observed['package'], packages)
        # No name-based guess: a body without width/pitch identity is absent.
        for absent in ('SOP-8', 'QFN-16(4x4)', 'SOT-23W'):
            self.assertNotIn(absent, packages)
        self.assertNotIn('Package_TO_SOT_SMD:SOT-23W', footprints)
        self.assertNotIn('Package_DFN_QFN:QFN-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm', footprints)

    def test_committed_table_is_generated_from_pinned_library_names(self):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'table.json'
            table_generator.main('/usr/share/kicad/footprints', output)
            self.assertEqual(output.read_bytes(), TABLE.read_bytes(),
                             'installed footprint library differs from the pinned table derivation')

    def test_invalid_tables_are_refused(self):
        base = json.loads(TABLE.read_text())
        chip = 'Capacitor_SMD:C_0402_1005Metric'
        for name, mutate in (
            ('unknown class', lambda t: t['footprints'][chip].update(classes=['mcu'])),
            ('chip without class', lambda t: t['footprints'][chip].update(classes=None)),
            ('empty classes', lambda t: t['footprints'][chip].update(classes=[])),
            ('duplicate class', lambda t: t['footprints'][chip].update(classes=['capacitor', 'capacitor'])),
            ('undeclared rule', lambda t: t['footprints'][chip].update(rule='fuzzy')),
            ('family outside rule', lambda t: t['packages']['SOT-23']['package'].update(family='chip')),
            ('boolean pins', lambda t: t['packages']['SOT-23']['package'].update(pins=True)),
            ('chip pins', lambda t: t['packages']['0402']['package'].update(pins=3)),
            ('missing body', lambda t: t['packages']['UFQFPN-48(7x7)']['package'].update(body=None)),
            ('extra field', lambda t: t['packages']['0402'].update(alias='0402-ish')),
            ('unqualified footprint', lambda t: t['footprints'].update(R_0603=t['footprints'][chip])),
            ('schema', lambda t: t.update(schema='agent-ee.package-map/2')),
            ('no derivation', lambda t: t.update(derivation={})),
            ('unknown rule name', lambda t: t['rules'].update(fuzzy='nearest name')),
        ):
            table = copy.deepcopy(base)
            mutate(table)
            with self.subTest(name), self.assertRaises((ValueError, TypeError)):
                validate_table(table)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dup.json'
            path.write_text('{"schema": "agent-ee.package-map/1", "schema": "agent-ee.package-map/1"}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                load_table(path)


def component(ref, footprint, lcsc, **flags):
    return {'reference': ref, 'sheet': '/', 'uuid': ref.lower(), 'lib_id': 'Owned:X', 'footprint': footprint,
            'properties': {} if lcsc is None else {'LCSC': lcsc}, 'populated': flags.get('populated', True),
            'on_board': flags.get('on_board', True), 'in_bom': flags.get('in_bom', True), 'locator': ref}


class EvaluateTests(unittest.TestCase):
    table = load_table(TABLE)
    rows = {'C14663': {'lcsc': 'C14663', 'package': '0603', 'category': 'capacitor'},
            'C9055': {'lcsc': 'C9055', 'package': 'SOT-23-5', 'category': 'regulator'}}

    def outcome(self, components, policy='info'):
        return evaluate({'components': {c['reference']: c for c in components}}, self.rows, self.table, 'LCSC', policy)

    def test_class_mismatch_follows_explicit_policy(self):
        parts = [component('C2', 'Resistor_SMD:R_0603_1608Metric', 'C14663')]
        info = self.outcome(parts, 'info')
        self.assertEqual((info['outcome'], info['advisories']), ('pass', ['C2']))
        self.assertEqual(info['records'][0]['severity'], 'info')
        defect = self.outcome(parts, 'defect')
        self.assertEqual(defect['outcome'], 'defect')
        self.assertEqual(defect['records'][0]['reason'], 'catalog category capacitor is not a footprint class')

    def test_known_violation_does_not_hide_missing_coverage(self):
        report = self.outcome([component('U2', 'Package_TO_SOT_SMD:SOT-23', 'C9055'),
                               component('X1', 'Package_SO:SOP-8_3.9x4.9mm_P1.27mm', 'C9055')])
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'))
        self.assertEqual([r['status'] for r in report['records']], ['mismatch', 'unknown'])

    def test_no_eligible_component_or_malformed_identity_is_unknown(self):
        self.assertEqual(self.outcome([component('C1', '', 'C14663', populated=False)])['outcome'], 'unknown')
        for lcsc in (None, '', ' C14663', 'c14663', 'C014663'):
            with self.subTest(lcsc=lcsc):
                report = self.outcome([component('C2', 'Capacitor_SMD:C_0603_1608Metric', lcsc)])
                self.assertEqual(report['outcome'], 'unknown', report)


class NativePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.class_tmp = tempfile.TemporaryDirectory()
        source = Path(cls.class_tmp.name) / 'source'
        source.mkdir()
        payload, manifest = generator.catalog_source()
        (source / 'owned.csv').write_bytes(payload)
        cls.catalog = Path(cls.class_tmp.name) / 'catalog'
        capture_catalog(manifest, source, cls.catalog)

    @classmethod
    def tearDownClass(cls):
        cls.class_tmp.cleanup()

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.counter = 0

    def scope(self, root, *, policy='info', requirements=None, checks=None, catalog_sha=None):
        params = {'netlist_check': 'netlist', 'catalog': 'catalog', 'mapping': 'package-map.json',
                  'catalog_manifest_sha256': catalog_sha or digest(root / 'catalog/manifest.json'),
                  'lcsc_field': 'LCSC', 'class_mismatch': policy}
        paths = ['packages.kicad_sch', 'ch.kicad_sch', 'requirements.md', 'package-map.json',
                 'catalog/manifest.json', 'catalog/catalog.sqlite3']
        return {'schema': 'agent-ee.scope/3', 'schematic': 'packages.kicad_sch',
            'inputs': [{'path': p, 'at': p} for p in paths], 'requirements': ['requirements.md'],
            'checks': checks or [{'id': 'netlist', 'kind': 'netlist'},
                                 {'id': 'packages', 'kind': 'package_consistency', 'params': params}],
            'baseline': {'requirements': requirements or [{'id': 'packages', 'statement': 'BOM package matches footprint',
                'source': {'path': 'requirements.md', 'locator': 'Packages'}, 'checks': ['netlist', 'packages'],
                'intent': 'acceptance'}], 'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}

    def prepare(self, variant='control', table=None, catalog=None):
        self.counter += 1
        root = self.root / str(self.counter)
        root.mkdir()
        (root / 'packages.kicad_sch').write_text(generator.schematic(variant))
        (root / 'ch.kicad_sch').write_text(generator.schematic(variant, child=True))
        (root / 'requirements.md').write_text('Packages: each populated BOM part package must match its footprint.')
        if table is None:
            shutil.copyfile(TABLE, root / 'package-map.json')
        else:
            write_json(root / 'package-map.json', table)
        shutil.copytree(catalog or self.catalog, root / 'catalog')
        return root

    def run_variant(self, variant='control', *, table=None, catalog=None, **scope):
        root = self.prepare(variant, table, catalog)
        write_json(root / 'scope.json', self.scope(root, **scope))
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 120)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        return run, checks

    def rerun(self, run):
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 60)
        try:
            check_package_consistency(ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return ctx.result

    @staticmethod
    def statuses(result):
        return {r['reference']: r['status'] for r in result['details']['records']}

    def test_native_control_matches_ticket_examples_and_records_hashes(self):
        run, checks = self.run_variant()
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(self.statuses(result), {'C1': 'match', 'C2': 'match', 'U1': 'match', 'U2': 'match',
            'R1': 'match', 'R2': 'match', 'C101': 'match', 'C201': 'match', 'JDBG': 'excluded', 'H1': 'excluded'})
        records = {r['reference']: r for r in result['details']['records']}
        self.assertEqual(records['C1']['rule'], {'footprint': 'chip-imperial', 'package': 'chip-imperial'})
        self.assertEqual((records['C1']['lcsc'], records['C1']['catalog']['package']), ('C1525', '0402'))
        self.assertEqual(records['U1']['rule'], {'footprint': 'body-size', 'package': 'body-size'})
        self.assertEqual(records['U1']['catalog']['package'], 'UFQFPN-48(7x7)')
        self.assertEqual(records['U2']['rule']['package'], 'named-sot23')
        # Repeated sheet instances stay distinct obligations.
        self.assertEqual(len({records[r]['sheet'] for r in ('C101', 'C201')}), 2)
        # An authored DNP field is data; only native flags exclude.
        self.assertEqual(records['R2']['status'], 'match')
        self.assertEqual(records['JDBG']['reason'], 'native dnp')
        self.assertEqual(records['H1']['reason'], 'native exclude_from_bom')
        details = result['details']
        self.assertEqual(details['mapping']['sha256'], hashlib.sha256(TABLE.read_bytes()).hexdigest())
        self.assertEqual(details['mapping']['version'], 'kicad-10.0.7-lcsc-1')
        self.assertEqual(details['catalog']['snapshot_manifest_sha256'], digest(self.catalog / 'manifest.json'))
        self.assertEqual(details['native_xml_sha256'], digest(run / 'work/netlist/design.xml'))
        self.assertEqual(read_json(run / 'work/packages/packages.json')['mapping'], details['mapping'])
        baseline = load_baseline(run)
        for rel in ('package-map.json', 'catalog/manifest.json', 'catalog/catalog.sqlite3'):
            self.assertIn(rel, baseline['files'])
        self.assertNotIn('packages.kicad_sch', baseline['files'])
        self.assertEqual(baseline['coverage']['checks']['packages']['predicates'],
                         ['package-consistency:footprint-package'])
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id': 'packages', 'check_id': 'packages'}],
                               [{'path': 'checks/packages.json', 'locator': 'U2'}])

    def test_sot23_footprint_with_five_lead_part_is_sealed_defect(self):
        run, checks = self.run_variant('sot-mismatch')
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        record = next(r for r in result['details']['records'] if r['reference'] == 'U2')
        self.assertEqual((record['status'], record['differences']), ('mismatch', ['pins']))
        self.assertEqual((record['footprint_package']['pins'], record['catalog_package']['pins']), (3, 5))
        self.assertIn('never crossed', record['reason'])
        freeze_evidence(run, checks)
        witness = [{'requirement_id': 'packages', 'check_id': 'packages'}]
        citation = [{'path': 'checks/packages.json', 'locator': 'U2'}]
        validate_witnesses(run, witness, citation)
        for rel in ('work/packages/packages.json', 'work/packages/catalog-rows.json', 'work/netlist/design.xml'):
            path = run / rel
            raw = path.read_bytes()
            path.write_bytes(raw + b' ')
            with self.subTest(path=rel), self.assertRaises(ValueError):
                verify_evidence(run, checks, required=True)
            with self.assertRaises(ValueError):
                validate_witnesses(run, witness, citation)
            path.write_bytes(raw)
        for rel in ('package-map.json', 'catalog/catalog.sqlite3'):
            path = run / 'inputs' / rel
            raw = path.read_bytes()
            path.write_bytes(raw + b' ')
            with self.subTest(path=rel), self.assertRaises(ValueError):
                verify_snapshot(run)
            path.write_bytes(raw)

    def test_resistor_footprint_on_capacitor_part_follows_class_policy(self):
        run, checks = self.run_variant('class')
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        record = next(r for r in result['details']['records'] if r['reference'] == 'C2')
        self.assertEqual((record['status'], record['severity']), ('class_mismatch', 'info'))
        self.assertEqual(record['catalog']['category'], 'capacitor')
        self.assertEqual(result['details']['advisories'], ['C2'])
        freeze_evidence(run, checks)
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id': 'packages', 'check_id': 'packages'}],
                               [{'path': 'checks/packages.json', 'locator': 'C2'}])
        run, checks = self.run_variant('class', policy='defect')
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks[1])
        self.assertIn('package-consistency:component-class',
                      load_baseline(run)['coverage']['checks']['packages']['predicates'])
        freeze_evidence(run, checks)
        validate_witnesses(run, [{'requirement_id': 'packages', 'check_id': 'packages'}],
                           [{'path': 'checks/packages.json', 'locator': 'C2'}])

    def test_names_fields_or_parts_outside_coverage_are_unknown(self):
        for variant, ref, reason in (
                ('unmapped-footprint', 'U2', 'footprint outside the mapping table'),
                ('unmapped-package', 'U2', 'catalog package outside the mapping table'),
                ('absent-part', 'R1', 'LCSC part absent from catalog snapshot'),
                ('authored-dnp-only', 'R2', 'no LCSC field value'),
                ('mismatch-and-unknown', 'R1', 'LCSC part absent from catalog snapshot')):
            with self.subTest(variant=variant):
                _, checks = self.run_variant(variant)
                result = checks[1]
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                unknown = [r for r in result['details']['records'] if r['status'] == 'unknown']
                self.assertEqual([(r['reference'], r['reason']) for r in unknown], [(ref, reason)])
                if variant == 'mismatch-and-unknown':
                    self.assertEqual(self.statuses(result)['U2'], 'mismatch')

    def test_mapping_change_rebinds_baseline_and_report(self):
        control, control_checks = self.run_variant()
        table = json.loads(TABLE.read_text())
        del table['footprints']['Resistor_SMD:R_2512_6332Metric']
        changed, changed_checks = self.run_variant(table=table)
        self.assertEqual(changed_checks[1]['outcome'], 'pass')
        before, after = load_baseline(control), load_baseline(changed)
        self.assertNotEqual(before['baseline_id'], after['baseline_id'])
        self.assertNotEqual(before['files']['package-map.json'], after['files']['package-map.json'])
        self.assertEqual(after['files']['package-map.json'], changed_checks[1]['details']['mapping']['sha256'])
        self.assertNotEqual(control_checks[1]['details']['mapping']['sha256'],
                            changed_checks[1]['details']['mapping']['sha256'])
        root = self.prepare()
        (root / 'package-map.json').write_text('{"schema": "agent-ee.package-map/1"}')
        write_json(root / 'scope.json', self.scope(root))
        (root / 'run').mkdir()
        with self.assertRaisesRegex(ValueError, 'expected fields'):
            capture(root / 'scope.json', root / 'run')

    def test_requirement_isolation_and_missing_prerequisite_refuse_capture(self):
        shared = [{'id': req, 'statement': req, 'source': {'path': 'requirements.md', 'locator': 'Packages'},
                   'checks': ['netlist', 'packages'], 'intent': 'acceptance'} for req in ('packages', 'other')]
        root = self.prepare()
        for name, scope, message in (
                ('two requirements', self.scope(root, requirements=shared), 'exactly one requirement per check'),
                ('no netlist', {**self.scope(root), 'checks': self.scope(root)['checks'][1:]},
                 'native netlist prerequisite'),
                ('pinned hash', self.scope(root, catalog_sha='0' * 64), 'differs from the pinned hash')):
            if name == 'no netlist':
                for req in scope['baseline']['requirements']:
                    req['checks'] = ['packages']
            with self.subTest(name):
                write_json(root / 'scope.json', scope)
                run = root / f'run-{name.replace(" ", "-")}'
                run.mkdir()
                with self.assertRaisesRegex(ValueError, message):
                    capture(root / 'scope.json', run)
        separate = [{'id': 'packages', 'statement': 'packages', 'source': {'path': 'requirements.md', 'locator': 'Packages'},
                     'checks': ['packages'], 'intent': 'acceptance'},
                    {'id': 'native', 'statement': 'native', 'source': {'path': 'requirements.md', 'locator': 'Packages'},
                     'checks': ['netlist'], 'intent': 'informational'}]
        run, checks = self.run_variant('sot-mismatch', requirements=separate)
        freeze_evidence(run, checks)
        with self.assertRaisesRegex(ValueError, 'does not trace'):
            validate_witnesses(run, [{'requirement_id': 'native', 'check_id': 'packages'}],
                               [{'path': 'checks/packages.json', 'locator': 'U2'}])
        validate_witnesses(run, [{'requirement_id': 'packages', 'check_id': 'packages'}],
                           [{'path': 'checks/packages.json', 'locator': 'U2'}])

    def test_failed_or_changed_prerequisite_is_failed(self):
        run, _ = self.run_variant()
        path = run / 'work/netlist/connectivity.json'
        raw = path.read_bytes()
        for replacement in (None, b'{', b'{}'):
            if replacement is None:
                path.unlink()
            else:
                path.write_bytes(replacement)
            result = self.rerun(run)
            self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
            path.write_bytes(raw)
        prior = run / 'checks/netlist.json'
        original = read_json(prior)
        for changed in ({**original, 'execution': 'failed'}, {**original, 'candidate_id': 'other'}):
            write_json(prior, changed)
            result = self.rerun(run)
            self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        write_json(prior, original)
        self.assertEqual(self.rerun(run)['outcome'], 'pass')

    def test_duplicate_native_properties_or_identities_are_failed(self):
        run, _ = self.run_variant()
        xml = run / 'work/netlist/design.xml'
        original = xml.read_bytes()

        def comp(root, ref):
            return root.find(f"./components/comp[@ref='{ref}']")

        def duplicate_field(root):
            fields = comp(root, 'C1').find('fields')
            fields.append(copy.deepcopy(fields.find("field[@name='LCSC']")))

        for name, change in (
                ('duplicate property', lambda root: comp(root, 'C1').append(
                    copy.deepcopy(comp(root, 'C1').find("property[@name='LCSC']")))),
                ('duplicate field', duplicate_field),
                ('valued native dnp', lambda root: comp(root, 'R1').append(ET.fromstring('<property name="dnp" value="no"/>'))),
                ('duplicate reference', lambda root: root.find('components').append(copy.deepcopy(comp(root, 'C1'))))):
            tree = ET.ElementTree(ET.fromstring(original))
            change(tree.getroot())
            tree.write(xml)
            # Even a matching lossy projection cannot authorize ambiguous XML.
            write_json(run / 'work/netlist/connectivity.json', parse_xml_netlist(xml))
            with self.subTest(name):
                result = self.rerun(run)
                self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        xml.write_bytes(original)

    def test_repinned_catalog_contradicting_raw_claims_is_missing_coverage(self):
        forged = self.root / 'forged'
        shutil.copytree(self.catalog, forged)
        database = forged / 'catalog.sqlite3'
        with sqlite3.connect(database) as connection:
            # Canonical JSON for a +/-1% interval; the raw claim is +/-10%.
            claim = canonical({'capacitance': {'max': '0.000000101', 'min': '0.000000099', 'unit': 'F'}}).decode()
            connection.execute("UPDATE component_base SET normalized=? WHERE lcsc='C1525'", (claim,))
            connection.execute("UPDATE capacitor SET normalized=?, capacitance_min='0.000000099', "
                               "capacitance_max='0.000000101' WHERE lcsc='C1525'", (claim,))
        manifest = read_json(forged / 'manifest.json')
        manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
        write_json(forged / 'manifest.json', manifest)
        run, checks = self.run_variant(catalog=forged)
        result = checks[1]
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        self.assertIn('rederivation', (run / 'work/packages/console.log').read_text())
        self.assertNotIn('records', result['details'])


if __name__ == '__main__':
    unittest.main()
