"""Selection acceptance uses independently authored synthetic catalog claims."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee.catalog import COLUMNS, capture_catalog
from agent_ee.part_selection import select_parts
from agent_ee.storage import canonical, digest


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def row(self, lcsc='C1', **overrides):
        return dict(lcsc=lcsc, mfr='Owned fixture', mpn='R-' + lcsc, package='0402',
                    category='resistor', stock='1000', price1='0.01', is_basic='1',
                    is_preferred='0', last_seen='2026-10-17',
                    attributes=json.dumps({'resistance': '10 kohm +/-1%', 'voltage': '50 V'}),
                    ) | overrides

    def snapshot(self, rows):
        source = self.root / 'owned.csv'
        with source.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        manifest = dict(schema='agent-ee.catalog-source/1', snapshot_date='2026-10-17',
                        source_url='https://example.invalid/owned', source_commit='owned-v1',
                        currency='USD', stale_before=None, volumes=[dict(path=source.name,
                        sha256=digest(source), size_bytes=source.stat().st_size, format='csv')])
        destination = self.root / 'snapshot'
        capture_catalog(manifest, self.root, destination)
        return destination, digest(destination / 'manifest.json')

    def requirements(self, **overrides):
        part = dict(id='R1', category='resistor', package='0402', quantity_per_board=1,
                    constraints={'resistance': {'kind': 'within', 'nominal': '10 kohm', 'tolerance_percent': '1'},
                                 'voltage': {'kind': 'at_least', 'min': '50 V'}})
        part.update(overrides)
        return dict(schema='agent-ee.part-requirements/1', build_quantity=10, stock_floor=100, parts=[part])

    def test_exact_value_interval_selection_and_determinism(self):
        snapshot, expected = self.snapshot([
            self.row('C1'), self.row('C2', attributes=json.dumps({'resistance': '110 kohm +/-1%', 'voltage': '50 V'})),
            self.row('C3', attributes=json.dumps({'resistance': '510 kohm +/-1%', 'voltage': '50 V'})),
            self.row('C4', attributes=json.dumps({'resistance': '10 kohm +/-5%', 'voltage': '50 V'})),
            self.row('C5', attributes=json.dumps({'resistance': '10 kohm +/-1%', 'voltage': '25 V'}))])
        requirement = self.requirements()
        selected = select_parts(snapshot, expected, requirement)
        self.assertEqual(selected['outcome'], 'pass')
        self.assertEqual(selected['snapshot_date'], '2026-10-17')
        self.assertEqual(selected['snapshot_manifest_sha256'], expected)
        part = selected['parts'][0]
        self.assertEqual(part['chosen']['lcsc'], 'C1')
        self.assertEqual([candidate['row']['lcsc'] for candidate in part['ranked']], ['C1'])
        self.assertEqual(len(part['rejected']), 4)
        self.assertEqual(part['ranked'][0]['matched_constraints']['resistance']['candidate'],
                         {'min': '9900', 'max': '10100', 'unit': 'ohm'})
        self.assertEqual(canonical(selected), canonical(select_parts(snapshot, expected, requirement)))

    def test_missing_catalog_tolerance_is_unknown_not_exact(self):
        snapshot, expected = self.snapshot([self.row(attributes=json.dumps({'resistance': '10 kohm', 'voltage': '50 V'}))])
        selection = select_parts(snapshot, expected, self.requirements())
        self.assertEqual(selection['outcome'], 'unknown')
        self.assertIsNone(selection['parts'][0]['chosen'])
        self.assertEqual(selection['parts'][0]['exclusion_counts'], {'unknown_attribute': 1})

    def test_capacitor_basic_precedence_and_four_low_stock_exclusions(self):
        rows = []
        for number in range(1, 34):
            rows.append(self.row(f'C{number}', category='capacitor',
                        attributes=json.dumps({'capacitance': '100 nF +/-10%', 'voltage': '25 V'}),
                        is_basic='1' if number in (1, 2) else '0',
                        is_preferred='1' if number == 3 else '0',
                        price1='0.1' if number in (1, 2) else '0.001',
                        stock='99' if number in (30, 31, 32, 33) else '1000'))
        snapshot, expected = self.snapshot(rows)
        requirement = self.requirements(id='C1', category='capacitor', constraints={
            'capacitance': {'kind': 'within', 'nominal': '100 nF', 'tolerance_percent': '10'},
            'voltage': {'kind': 'at_least', 'min': '25 V'}})
        selection = select_parts(snapshot, expected, requirement)
        part = selection['parts'][0]
        self.assertEqual([candidate['row']['lcsc'] for candidate in part['ranked'][:3]], ['C1', 'C2', 'C3'])
        self.assertEqual(part['chosen']['lcsc'], 'C1')
        self.assertEqual(part['exclusion_counts'], {'low_stock': 4})
        self.assertEqual(part['tie_break']['deciding_key'], 'lcsc_number:ascending')
        self.assertEqual(part['excluded_rows'], 4)
        self.assertEqual(len(part['ranked']), 29)

    def test_price_then_stock_then_numeric_lcsc_and_unknown_price_last(self):
        snapshot, expected = self.snapshot([
            self.row('C10'), self.row('C2'), self.row('C3', stock='500'),
            self.row('C4', price1='0.02', stock='9999'), self.row('C5', price1='', stock='99999')])
        part = select_parts(snapshot, expected, self.requirements())['parts'][0]
        self.assertEqual([candidate['row']['lcsc'] for candidate in part['ranked']], ['C2', 'C10', 'C3', 'C4', 'C5'])
        self.assertEqual(part['tie_break']['deciding_key'], 'lcsc_number:ascending')

    def test_unknown_attributes_stock_and_empty_query_have_explanations(self):
        snapshot, expected = self.snapshot([
            self.row('C1', stock=''), self.row('C2', attributes='{"resistance":"10 kohm +/-1%","voltage":"unreadable"}'),
            self.row('C3', package='0603'), self.row('C4', attributes='{}')])
        selection = select_parts(snapshot, expected, self.requirements())
        self.assertEqual(selection['execution'], 'ok')
        self.assertEqual(selection['outcome'], 'unknown')
        part = selection['parts'][0]
        self.assertEqual(part['reason'], 'no candidate')
        self.assertIsNone(part['chosen'])
        self.assertEqual(part['ranked'], [])
        self.assertEqual(part['exclusion_counts'], {'package_mismatch': 1, 'unknown_attribute': 3, 'unknown_stock': 1})
        self.assertTrue(part['rejected'][0]['reasons'][0]['unknown'])
        self.assertEqual(selection['allocations'], {})

    def test_singleton_requirements_are_user_errors(self):
        from agent_ee.part_selection import selection_check
        snapshot, expected = self.snapshot([self.row()])
        for constraint in ({'kind': 'within', 'min': '10 kohm', 'max': '10 kohm'},
                           {'kind': 'within', 'nominal': '10 kohm'},
                           {'kind': 'within', 'nominal': '10 kohm', 'tolerance_percent': '0'},
                           {'kind': 'at_least', 'min': '10 kohm'}):
            requirement = self.requirements(constraints={'resistance': constraint})
            with self.subTest(constraint=constraint):
                with self.assertRaises(ValueError):
                    select_parts(snapshot, expected, requirement)
                failure = selection_check(snapshot, expected, requirement)
                self.assertEqual(failure['execution'], 'failed')
                self.assertEqual(failure['outcome'], 'unknown')
                self.assertTrue(failure['details']['error'])

    def test_rating_and_temperature_cover_the_required_operating_range(self):
        snapshot, expected = self.snapshot([
            self.row('C1', attributes=json.dumps({'resistance': '10 kohm +/-1%',
                                                 'voltage': '50 V .. 100 V', 'temperature': '-40 degC .. 125 degC'})),
            self.row('C2', attributes=json.dumps({'resistance': '10 kohm +/-1%',
                                                 'voltage': '25 V .. 100 V', 'temperature': '-40 degC .. 125 degC'})),
            self.row('C3', attributes=json.dumps({'resistance': '10 kohm +/-1%',
                                                 'voltage': '50 V', 'temperature': '0 degC .. 85 degC'}))])
        requirement = self.requirements()
        requirement['parts'][0]['constraints']['temperature'] = {'kind': 'covers', 'min': '-40 degC', 'max': '85 degC'}
        part = select_parts(snapshot, expected, requirement)['parts'][0]
        self.assertEqual([candidate['row']['lcsc'] for candidate in part['ranked']], ['C1'])
        self.assertEqual(part['exclusion_counts'], {'constraint_mismatch': 2})

    def test_most_constrained_first_and_allocations_cannot_reuse_stock(self):
        snapshot, expected = self.snapshot([
            self.row('C1', stock='100'), self.row('C2', stock='100',
                attributes=json.dumps({'resistance': '10 kohm +/-2%', 'voltage': '50 V'}))])
        requirement = self.requirements(id='Z-narrow')
        narrow = requirement['parts'][0]
        broad = {**narrow, 'id': 'A-broad', 'constraints': {
            **narrow['constraints'], 'resistance': {'kind': 'within', 'nominal': '10 kohm', 'tolerance_percent': '5'}}}
        requirement.update(build_quantity=100, stock_floor=0, parts=[broad, narrow])
        selection = select_parts(snapshot, expected, requirement)
        self.assertEqual([part['id'] for part in selection['parts']], ['Z-narrow', 'A-broad'])
        self.assertEqual([part['chosen']['lcsc'] for part in selection['parts']], ['C1', 'C2'])
        self.assertEqual(selection['allocations'], {'C1': 100, 'C2': 100})
        self.assertEqual(selection['parts'][1]['exclusion_counts'], {'low_stock': 1})

    def test_missing_or_corrupt_snapshot_never_selects_fallback(self):
        from agent_ee.part_selection import selection_check
        failure = selection_check(self.root / 'missing', '0' * 64, self.requirements())
        self.assertEqual((failure['execution'], failure['outcome'], failure['parts']), ('failed', 'unknown', []))
        snapshot, expected = self.snapshot([self.row()])
        (snapshot / 'catalog.sqlite3').write_bytes(b'corrupt')
        failure = selection_check(snapshot, expected, self.requirements())
        self.assertEqual((failure['execution'], failure['outcome'], failure['parts']), ('failed', 'unknown', []))

    def test_quantities_and_category_constraints_are_strict(self):
        snapshot, expected = self.snapshot([self.row()])
        for changes in ({'quantity_per_board': 0}, {'quantity_per_board': True}, {'package': ''},
                        {'constraints': {'capacitance': {'kind': 'within', 'nominal': '100 nF', 'tolerance_percent': '10'}}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                select_parts(snapshot, expected, self.requirements(**changes))
        requirements = self.requirements()
        requirements['parts'].append(requirements['parts'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            select_parts(snapshot, expected, requirements)

    def test_selection_is_networkless_in_actual_sandbox(self):
        import subprocess
        from agent_ee.executor import sandbox_argv
        from toolchain import require_toolchain
        require_toolchain()
        snapshot, expected = self.snapshot([self.row()])
        work = self.root / 'work'
        work.mkdir()
        program = ('from agent_ee.part_selection import select_parts; '
                   f'r = select_parts("/candidate", {expected!r}, {self.requirements()!r}); '
                   'assert r["parts"][0]["chosen"]["lcsc"] == "C1"; '
                   'assert r["snapshot_date"] == "2026-10-17"; print(r["outcome"])')
        completed = subprocess.run(sandbox_argv(snapshot, work, ['/usr/bin/python3', '-c', program]),
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'pass')

    def test_requirement_failure_retains_verified_snapshot_date_and_hash(self):
        from agent_ee.part_selection import selection_check
        snapshot, expected = self.snapshot([self.row()])
        failure = selection_check(snapshot, expected, self.requirements(quantity_per_board=0))
        self.assertEqual(failure['snapshot_date'], '2026-10-17')
        self.assertEqual(failure['snapshot_sha256'], json.loads((snapshot / 'manifest.json').read_text())['database']['sha256'])

    def test_repinned_interval_contradicting_raw_tolerance_never_selects(self):
        from agent_ee.part_selection import selection_check
        from catalog_repin import repin, set_derived
        snapshot, expected = self.snapshot([self.row(
            attributes=json.dumps({'resistance': '10 kohm +/-5%', 'voltage': '50 V'}))])
        self.assertEqual(select_parts(snapshot, expected, self.requirements())['outcome'], 'unknown')
        tampered = repin(snapshot, lambda connection: set_derived(connection, 'C1', {
            'resistance': {'min': '9900', 'max': '10100', 'unit': 'ohm'},
            'voltage': {'min': '50', 'max': '50', 'unit': 'V'}}))
        self.assertNotEqual(tampered, expected)
        with self.assertRaisesRegex(ValueError, 'rederiv'):
            select_parts(snapshot, tampered, self.requirements())
        failure = selection_check(snapshot, tampered, self.requirements())
        self.assertEqual((failure['execution'], failure['outcome'], failure['parts']), ('failed', 'unknown', []))
        self.assertRegex(failure['details']['error'], 'rederiv')

    def test_repinned_valid_snapshot_still_selects(self):
        from catalog_repin import repin, set_derived
        snapshot, expected = self.snapshot([self.row()])
        rewritten = repin(snapshot, lambda connection: set_derived(connection, 'C1', {
            'resistance': {'min': '9900', 'max': '10100', 'unit': 'ohm'},
            'voltage': {'min': '50', 'max': '50', 'unit': 'V'}}))
        self.assertNotEqual(rewritten, expected)
        selection = select_parts(snapshot, rewritten, self.requirements())
        self.assertEqual((selection['outcome'], selection['parts'][0]['chosen']['lcsc']), ('pass', 'C1'))

    def test_repinned_invalid_price_is_failed_not_an_uncaught_ranking_error(self):
        from agent_ee.part_selection import selection_check
        from catalog_repin import repin
        snapshot, _ = self.snapshot([self.row('C1'), self.row('C2')])
        tampered = repin(snapshot, lambda connection: [connection.execute(
            f"UPDATE {table} SET price1='abc' WHERE lcsc='C2'") for table in ('component_base', 'resistor')])
        failure = selection_check(snapshot, tampered, self.requirements())
        self.assertEqual((failure['execution'], failure['outcome'], failure['parts']), ('failed', 'unknown', []))
