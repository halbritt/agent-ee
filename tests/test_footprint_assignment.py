"""Pod owned symbols against the exact stock KiCad 10.0.7 packages."""
import unittest
from pathlib import Path

from agent_ee.footprint_library import check_assignments
from agent_ee.storage import read_json
from test_pod_symbols import POD, query_inputs


class PodFootprintAssignmentTests(unittest.TestCase):
    def inputs(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        manifest = read_json(POD / 'symbols/footprint-libraries.json')
        _, files = query_inputs()
        for lib in manifest['libraries']:
            for member in lib['members']:
                path = Path('/usr/share/kicad/footprints') / Path(member['path']).relative_to('stock')
                self.assertTrue(path.is_file(), path)
                files[member['path']] = path.read_bytes()
        return manifest, files, read_json(POD / 'symbols/assignments.json')

    def test_both_stock_packages_have_exact_pad_pin_parity(self):
        manifest, files, declaration = self.inputs()
        result = check_assignments(manifest, files, declaration)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual([(a['reference'], a['status']) for a in result['assignments']], [('D1', 'pass'), ('U1', 'pass')])
        self.assertEqual([a['pin_numbers'] for a in result['assignments']],
                         [['1', '2', '3'], sorted(str(n) for n in range(1, 15))])

    def test_missing_pad_is_defect_and_missing_footprint_is_unknown(self):
        import hashlib
        manifest, files, declaration = self.inputs()
        member = manifest['libraries'][0]['members'][0]
        files[member['path']] = files[member['path']].replace(b'(pad "1"', b'(pad "99"', 1)
        member['sha256'] = hashlib.sha256(files[member['path']]).hexdigest()
        result = check_assignments(manifest, files, declaration)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('1', result['findings'][0]['missing_pads'])
        del files[member['path']]
        self.assertEqual(check_assignments(manifest, files, declaration)['outcome'], 'unknown')

    def test_mounting_pads_do_not_count_as_connector_positions(self):
        import hashlib
        manifest, files, declaration = self.inputs()
        d1 = next(c for c in declaration['components'] if c['reference'] == 'D1')
        path = next(m['path'] for lib in manifest['libraries'] for m in lib['members'] if m['path'].endswith(
            d1['footprint'].split(':')[1] + '.kicad_mod'))
        files[path] = files[path].replace(b'(pad "3"', b'(pad "MP"', 1)
        next(m for lib in manifest['libraries'] for m in lib['members'] if m['path'] == path).update(
            sha256=hashlib.sha256(files[path]).hexdigest())
        for columns, expected in ((2, []), (3, [dict(expected=3, actual=2)])):
            d1['connector'] = dict(rows=1, columns=columns)
            findings = [row for row in check_assignments(manifest, files, declaration)['findings']
                        if row['code'] == 'connector_count']
            self.assertEqual([{k: row[k] for k in ('expected', 'actual')} for row in findings], expected)
