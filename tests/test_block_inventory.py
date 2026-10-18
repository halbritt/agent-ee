"""Block inventory capture and its private qualification over an owned hierarchical design."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import block_inventory_fixture as fixture
from agent_ee.block_inventory import (adapt_connectivity, capture_inventory, compare_connectivity,
                                      validate_inventory)
from agent_ee.reference_blocks import block_contract, hierarchy_ports, validate_block
from agent_ee.storage import digest, read_json, write_json

REPO = Path(__file__).resolve().parents[1]
OWNED_STRAPS = [dict(reference='U201', pin='3', net=None), dict(reference='U201', pin='4', net='VDD')]
OWNED_EXPECTED = [dict(reference='U201', field='pin', pin='3', adapted='named:GND', captured='disconnected'),
                  dict(reference='U201', field='pin', pin='4', adapted='disconnected', captured='named:VDD')]


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file()}


class HierarchyPortTests(unittest.TestCase):
    def test_repeated_label_is_one_port_and_conflicting_shape_is_refused(self):
        label = '(hierarchical_label "VIN" (shape {}) (at {} 10 0) (effects (font (size 1 1))) (uuid "{}"))'
        same = ('(kicad_sch (version 20250114) ' + label.format('input', 10, 'a') +
                label.format('input', 20, 'b') + ')').encode()
        self.assertEqual(hierarchy_ports(same), {'VIN': 'input'})
        conflict = same.replace(b'(shape input) (at 20', b'(shape output) (at 20')
        with self.assertRaisesRegex(ValueError, 'conflicting hierarchical port'):
            hierarchy_ports(conflict)


class InventoryStructureTests(unittest.TestCase):
    def test_inventory_boundaries(self):
        valid = fixture.inventory('a' * 40)
        validate_inventory(valid)
        cases = [
            ('short commit', lambda d: d['design'].update(commit='abc123')),
            ('unknown field', lambda d: d.update(status='verified')),
            ('missing plan', lambda d: d.update(documents={})),
            ('unsafe document', lambda d: d['documents'].update(plan='../escape.md')),
            ('duplicate block', lambda d: d['blocks'].append(copy.deepcopy(d['blocks'][0]))),
            ('bad slug', lambda d: d['blocks'][0].update(id='Has Space')),
            ('no instances', lambda d: d['blocks'][0].update(instances=[])),
            ('unknown document', lambda d: d['blocks'][0]['pending'][0].update(document='invented')),
            ('reserved requirement id', lambda d: d['blocks'][0]['pending'][0].update(id='ch1-interface')),
            ('external name', lambda d: d['design'].update(external_libraries={'../x': 'a' * 40})),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                document = copy.deepcopy(valid)
                mutate(document)
                with self.assertRaises(ValueError):
                    validate_inventory(document)


class InventoryCaptureTests(unittest.TestCase):
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-105-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'design').mkdir()
        self.commit = fixture.write_repo(self.root / 'design')
        self.inventory = fixture.inventory(self.commit)

    def capture(self, name='capture', inventory=None, **kwargs):
        return capture_inventory(inventory or self.inventory, self.root / 'design', self.root / name, **kwargs)

    def test_capture_is_unverified_and_reproducible(self):
        dirty = self.root / 'design' / 'sheets/chan1.kicad_sch'
        dirty.write_text('uncommitted edits must not be captured')
        first = self.capture('first')
        second = self.capture('second')
        self.assertEqual(tree(self.root / 'first'), tree(self.root / 'second'))
        self.assertEqual([b['status'] for b in first['blocks']], ['unverified', 'unverified'])
        self.assertEqual(first, second)
        ch1 = self.root / 'first/ch1'
        report = validate_block(ch1)
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['pending_requirements'], ['ch1-function'])
        self.assertTrue(report['draft_parts'])
        record = read_json(ch1 / 'block.json')
        self.assertEqual(record['source']['commit'], self.commit)
        self.assertEqual({p['name']: p['net_name'] for p in record['boundary_ports']},
                         {'SIG': '/CH1_SIG', 'OUT': '/CH1_OUT'})
        self.assertEqual(record['instances'][0]['references'], ['C101', 'R101', 'R102', 'U101'])
        origins = {l['lib_id']: l['origin'] for l in record['libraries']}
        self.assertEqual(origins['owned_fp:QFN-6'], 'source')
        self.assertEqual(origins['Device:R'], 'kicad-stock')
        self.assertIn('libs/owned_fp.pretty/QFN-6.kicad_mod', record['files'])
        self.assertNotEqual((ch1 / 'sheets/chan1.kicad_sch').read_text(), dirty.read_text())
        contract = read_json(ch1 / 'interface.json')
        names = {i['terminal']: i['names'] for i in contract['invariants'] if i['kind'] == 'named_net'}
        self.assertEqual(names['R101.1'], ['/CH1_SIG'])
        self.assertEqual(names['U101.3'], ['GND'])
        self.assertNotIn('U101.4', names)  # disconnected strap pin
        self.assertNotIn('U101.5', names)  # /CH1/MID never leaves the block
        parts = {p['reference']: p for p in read_json(ch1 / 'parts.json')['parts']}
        self.assertEqual(parts['R101']['lcsc'], 'C25804')
        self.assertIsNone(parts['C101']['lcsc'])
        self.assertIsNone(read_json(ch1 / 'parts.json')['catalog'])
        with self.assertRaises(FileExistsError):
            self.capture('first')

    def test_adaptation_reproduces_second_channel_except_straps(self):
        self.capture()
        source = read_json(self.root / 'capture/ch1/connectivity.json')
        target = read_json(self.root / 'capture/ch2/connectivity.json')
        parameters = dict(reference_offset=100, renames=[['CH1', 'CH2']], straps=[])
        self.assertEqual(compare_connectivity(adapt_connectivity(source, parameters), target), OWNED_EXPECTED)
        parameters['straps'] = OWNED_STRAPS
        self.assertEqual(compare_connectivity(adapt_connectivity(source, parameters), target), [])
        # A wrong offset or a missed rename is a visible difference, not silently equal.
        for wrong in (dict(parameters, reference_offset=200, straps=[]), dict(parameters, renames=[])):
            self.assertNotEqual(compare_connectivity(adapt_connectivity(source, wrong), target), [])
        for bad in (dict(parameters, renames=[['CH1']]), dict(parameters, reference_offset='100'),
                    dict(parameters, unknown=True)):
            with self.assertRaises(ValueError):
                adapt_connectivity(source, bad)
        with self.assertRaisesRegex(ValueError, 'unknown adapted terminal'):
            adapt_connectivity(source, dict(parameters, straps=[dict(reference='U999', pin='1', net=None)]))

    def test_catalog_identity_and_presence_are_recorded(self):
        import csv
        from agent_ee.catalog import capture_catalog
        path = self.root / 'owned.csv'
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['lcsc', 'mfr', 'mpn', 'package', 'category', 'stock',
                                                        'price1', 'is_basic', 'is_preferred', 'last_seen',
                                                        'attributes'])
            writer.writeheader()
            writer.writerow(dict(lcsc='C25804', mfr='Owned', mpn='R10K', package='0603', category='resistor',
                                 stock='1', price1='0.01', is_basic='1', is_preferred='0',
                                 last_seen='2026-10-17', attributes='{}'))
        source = dict(schema='agent-ee.catalog-source/1', snapshot_date='2026-10-17',
                      source_url='https://example.invalid/owned', source_commit='owned-v1', currency='USD',
                      stale_before=None, volumes=[dict(path=path.name, sha256=digest(path),
                                                      size_bytes=path.stat().st_size, format='csv')])
        capture_catalog(source, self.root, self.root / 'catalog')
        sha = digest(self.root / 'catalog/manifest.json')
        self.capture(catalog=self.root / 'catalog', catalog_manifest_sha256=sha)
        parts = read_json(self.root / 'capture/ch1/parts.json')
        self.assertEqual(parts['catalog']['manifest_sha256'], sha)
        self.assertEqual({p['reference']: p['catalog'] for p in parts['parts']},
                         {'C101': 'absent', 'R101': 'present', 'R102': 'absent', 'U101': 'absent'})
        with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
            self.capture('bad', catalog=self.root / 'catalog', catalog_manifest_sha256='0' * 64)
        self.assertFalse((self.root / 'bad').exists())

    def test_capture_refusals_publish_nothing(self):
        cases = [
            ('unassigned component', lambda d: d.update(unassigned=[])),
            ('double assignment', lambda d: d['blocks'][1]['instances'].append(dict(path='/CH1/', references=['R101']))),
            ('reference outside instance', lambda d: d['blocks'][1]['instances'][0].update(references=['R101'])),
            ('unknown instance', lambda d: d['blocks'][0]['instances'][0].update(path='/CH9/')),
            ('unassigned outside instance', lambda d: d['unassigned'][0].update(references=['X1', 'R101'])),
            ('missing document', lambda d: d['documents'].update(plan='docs/ABSENT.md')),
            ('unknown commit', lambda d: d['design'].update(commit='b' * 40)),
            ('undeclared library repo', None),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                document = copy.deepcopy(self.inventory)
                kwargs = {}
                if mutate is None:
                    kwargs['library_repos'] = {'kicad-libraries': str(self.root / 'design')}
                else:
                    mutate(document)
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.capture('refused', inventory=document, **kwargs)
                self.assertFalse((self.root / 'refused').exists())

    def test_extended_block_fields_are_bound_to_captured_sheets(self):
        self.capture()
        original = self.root / 'capture/ch1'
        cases = [
            ('reference not in sheet', lambda r: r['instances'][0].update(references=['C101', 'R101', 'R102', 'U999'])),
            ('unsorted references', lambda r: r['instances'][0]['references'].reverse()),
            ('library omitted', lambda r: r['libraries'].pop()),
            ('library hash', lambda r: r['libraries'][1].update(sha256='0' * 64)),
            ('unresolved claims file', lambda r: r['libraries'][0].update(origin='unresolved')),
            ('unknown check kind', lambda r: r['check_set'][0].update(kind='invented')),
            ('untraced check', lambda r: r['check_set'].append(dict(id='extra', kind='erc', params={}))),
            ('interface not named', lambda r: r['check_set'][2]['params'].update(contract='parts.json')),
            ('pending duplicate', lambda r: r['pending_requirements'][0].update(id='ch1-native')),
            ('pending source outside files', lambda r: r['pending_requirements'][0]['source'].update(path='absent.md')),
            ('requirement source differs from interface', lambda r: r['requirements'][1]['source'].update(locator='other')),
            ('instances removed', lambda r: r.pop('instances')),
        ]
        import shutil
        for name, mutate in cases:
            with self.subTest(case=name):
                target = self.root / 'mutated'
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(original, target)
                record = read_json(target / 'block.json')
                mutate(record)
                write_json(target / 'block-contract.json', block_contract(record))
                record['files']['block-contract.json'] = digest(target / 'block-contract.json')
                write_json(target / 'block.json', record)
                with self.assertRaises((ValueError, KeyError)):
                    validate_block(target)
        for document, edit in (('parts.json', lambda p: p['parts'].pop()),
                               ('interface.json', lambda c: c['components']['U101']['locations'][0].update(reference='X1'))):
            with self.subTest(document=document):
                target = self.root / 'mutated'
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(original, target)
                data = read_json(target / document)
                edit(data)
                write_json(target / document, data)
                record = read_json(target / 'block.json')
                record['files'][document] = digest(target / document)
                write_json(target / 'block.json', record)
                with self.assertRaises(ValueError):
                    validate_block(target)


class QualificationScriptTests(unittest.TestCase):
    """The opt-in private-inventory qualification, exercised on the owned design only."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-105-qualify-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'design').mkdir()
        self.commit = fixture.write_repo(self.root / 'design')
        self.private = self.root / 'private'
        self.private.mkdir()
        write_json(self.private / 'block-inventory.json', fixture.inventory(self.commit))
        self.adaptation = dict(schema='agent-ee.block-adaptation-fixture/1', design='owned', source='ch1',
                               target='ch2', description='Owned channel copy',
                               parameters=dict(reference_offset=100, renames=[['CH1', 'CH2']], straps=OWNED_STRAPS),
                               expected_without_straps=OWNED_EXPECTED)
        write_json(self.private / 'ch1-to-ch2.json', self.adaptation)

    def run_script(self, *args):
        env = dict(os.environ, PYTHONPATH=str(REPO / 'src'), AGENT_EE_FOME_MC_CAPTURE=str(self.private),
                   AGENT_EE_FOME_MC_CHECKOUT=str(self.root / 'design'))
        result = subprocess.run([sys.executable, str(REPO / 'scripts/qualify_block_inventory.py'), *args],
                                capture_output=True, text=True, env=env, timeout=1800)
        return result.returncode, json.loads(result.stdout.splitlines()[-1])

    def test_default_private_layout_qualifies_and_reproduces(self):
        code, report = self.run_script()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'qualified')
        self.assertEqual(report['blocks'], {'ch1': 'unverified', 'ch2': 'unverified'})
        self.assertEqual(report['adaptations'][0]['without_straps'], OWNED_EXPECTED)
        self.assertEqual(report['adaptations'][0]['with_straps'], [])
        out = self.private / 'captures' / (self.commit[:12] + '-1')
        self.assertEqual(Path(report['out']), out)
        self.assertEqual(tree(out / 'first'), tree(out / 'second'))
        self.assertEqual(read_json(out / 'qualification.json')['status'], 'qualified')
        code, again = self.run_script()
        self.assertEqual((code, Path(again['out']).name), (0, self.commit[:12] + '-2'))

    def test_wrong_expectation_fails_and_tracked_output_is_refused(self):
        wrong = dict(self.adaptation, expected_without_straps=OWNED_EXPECTED[:1])
        write_json(self.private / 'ch1-to-ch2.json', wrong)
        code, report = self.run_script('--out', str(self.root / 'wrong'))
        self.assertEqual((code, report['status']), (1, 'failed'))
        self.assertEqual(len(report['failures']), 1)
        write_json(self.private / 'ch1-to-ch2.json', dict(self.adaptation, design='other'))
        code, report = self.run_script('--out', str(self.root / 'other'))
        self.assertEqual((code, report['status']), (2, 'refused'))
        self.assertFalse((self.root / 'other').exists())
        write_json(self.private / 'ch1-to-ch2.json', self.adaptation)
        # Captured design files must never land where a Git work tree would track them.
        tracked = self.root / 'design' / 'captured'
        code, report = self.run_script('--out', str(tracked))
        self.assertEqual((code, report['status']), (2, 'refused'))
        self.assertIn('tracked by a Git work tree', report['error'])
        self.assertFalse(tracked.exists())
        (self.root / 'design' / '.gitignore').write_text('captured/\n')
        code, report = self.run_script('--out', str(tracked))
        self.assertEqual((code, report['status']), (0, 'qualified'))


if __name__ == '__main__':
    unittest.main()
