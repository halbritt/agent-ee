"""Campaign placement blocks requests, pinned package data and netlist stamp identity (AGENTEE-439).

Every fixture is original: authored XML text, the owned RC design of test_placement_adapter and
the owned block fixtures of test_blocks.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import block_arrangement, blocks, placement_adapter as pa
from agent_ee.native_netlist import STAMP_FIELDS, read_native_inventory, read_sheet_names, stamp_identity
from agent_ee.storage import digest, read_json, write_json
from test_blocks import board as block_board, native_xml, policy as block_policy
from test_placement_adapter import BOARD, POLICY, SCHEMATIC, owned_design, stage_receipts, uuid_of
from toolchain import require_toolchain

FP_J = '22222222-2222-4222-8222-222222222222'
HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n<export version="E">\n  <design>\n'
          '    <source>/candidate/project/owned.kicad_sch</source>\n'
          '    <date>{date}</date>\n    <tool>{tool}</tool>\n'
          '    <sheet number="1" name="/" tstamps="/">\n      <title_block>\n        <date>{title}</date>\n'
          '      </title_block>\n    </sheet>\n  </design>\n')
BODY = ('  <components>\n    <comp ref="R1">\n      <value>{value}</value>\n      <libsource lib="Owned" part="R"/>\n'
        '      <sheetpath names="/" tstamps="/"/>\n      <tstamps>uuid-r1</tstamps>\n'
        '      <units><unit name="A"><pins><pin num="1"/><pin num="2"/></pins></unit></units>\n'
        '    </comp>\n  </components>\n  <libparts>\n    <libpart lib="Owned" part="R">\n'
        '      <pins><pin num="1" name="~" type="passive"/><pin num="2" name="~" type="passive"/></pins>\n'
        '    </libpart>\n  </libparts>\n  <nets>\n    <net code="1" name="A"><node ref="R1" pin="1"/></net>\n'
        '    <net code="2" name="B"><node ref="R1" pin="2"/></net>\n  </nets>\n</export>\n')


def netlist_text(date='2026-10-21T10:00:00', tool='Eeschema 10.0.7', title='', value='10k', spacing=''):
    return HEADER.format(date=date, tool=tool, title=title) + spacing + BODY.format(value=value)


def with_stamps(raw, date='2001-02-03T04:05:06'):
    """The same export with only its design-header date replaced, as a later regeneration writes it."""
    text = raw.decode('utf-8')
    start = text.index('<date>', text.index('<design>')) + len('<date>')
    return (text[:start] + date + text[text.index('</date>', start):]).encode('utf-8')


class StampIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-stamp-identity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def identity(self, text, name='n.xml'):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return stamp_identity(path)

    def test_only_the_declared_header_stamps_are_masked(self):
        self.assertEqual(STAMP_FIELDS, ('/export/design/date', '/export/design/tool'))
        base = self.identity(netlist_text())
        self.assertEqual(base['stamps'], {'date': '2026-10-21T10:00:00', 'tool': 'Eeschema 10.0.7'})
        self.assertEqual(base['sha256'], hashlib.sha256(netlist_text().encode()).hexdigest())
        for name, changes in {'date': {'date': '2030-01-01T00:00:00'}, 'tool': {'tool': 'Eeschema 10.0.8'},
                              'both': {'date': '', 'tool': 'other'}}.items():
            with self.subTest(name):
                other = self.identity(netlist_text(**changes))
                self.assertNotEqual(other['sha256'], base['sha256'])
                self.assertEqual(other['semantic_sha256'], base['semantic_sha256'])
        for name, changes in {'title-block date': {'title': '2026-10-21'}, 'component value': {'value': '22k'},
                              'whitespace': {'spacing': '\n'}, 'comment': {'spacing': '<!-- note -->\n'}}.items():
            with self.subTest(name):
                self.assertNotEqual(self.identity(netlist_text(**changes))['semantic_sha256'], base['semantic_sha256'])

    def test_a_header_without_the_declared_stamps_is_refused(self):
        text = netlist_text()
        for name, broken in {
                'no tool': text.replace('    <tool>Eeschema 10.0.7</tool>\n', ''),
                'no source': text.replace('    <source>/candidate/project/owned.kicad_sch</source>\n', ''),
                'date after tool': text.replace('<date>2026-10-21T10:00:00</date>\n    <tool>Eeschema 10.0.7</tool>',
                                                '<tool>Eeschema 10.0.7</tool>\n    <date>2026-10-21T10:00:00</date>'),
                'two dates': text.replace('    <tool>', '    <date>x</date>\n    <tool>'),
                'markup in stamp': text.replace('<date>2026-10-21T10:00:00</date>', '<date><b/></date>'),
                'byte order mark': '﻿' + text}.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.identity(broken)


class BlocksDeclarationTest(unittest.TestCase):
    BLOCKS = {'netlist': 'project/blocks/netlist.xml', 'arrangement': 'project/blocks/arrangement.json',
              'candidates': ['project/blocks/RC.json']}

    def declaration(self, **changes):
        value = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1', 'policy': POLICY,
                 'fixed_footprints': [FP_J], 'timeout_s': 600, 'blocks': dict(self.BLOCKS)}
        value.update(changes)
        return value

    def test_declaration_boundaries(self):
        normalized = pa.validate_declaration(self.declaration())
        self.assertEqual(normalized['blocks'], self.BLOCKS)
        plain = {k: v for k, v in self.declaration().items() if k != 'blocks'}
        self.assertNotIn('blocks', pa.validate_declaration(plain))
        allocation = {**self.BLOCKS, 'allocation': 'project/blocks/allocation.json'}
        self.assertEqual(pa.validate_declaration(self.declaration(blocks=allocation))['blocks'], allocation)
        for name, changes, message in (
                ('without helper', {'helper': None, 'policy': None}, 'require a placement helper'),
                ('with targeted plans', {'targeted_plans': 'project/plans.json'}, 'cannot be combined'),
                ('with sublayout', {'sublayout': {'request': 'r.json', 'sub_boards': 'sub'}}, 'sublayout'),
                ('missing netlist', {'blocks': {k: v for k, v in self.BLOCKS.items() if k != 'netlist'}}, 'exactly'),
                ('extra key', {'blocks': {**self.BLOCKS, 'policy': 'p.json'}}, 'exactly'),
                ('absolute netlist', {'blocks': {**self.BLOCKS, 'netlist': '/etc/netlist.xml'}}, 'relative'),
                ('escaping candidate', {'blocks': {**self.BLOCKS, 'candidates': ['../RC.json']}}, 'relative'),
                ('non-xml netlist', {'blocks': {**self.BLOCKS, 'netlist': 'project/netlist.json'}}, r'\.xml'),
                ('no candidates', {'blocks': {**self.BLOCKS, 'candidates': []}}, 'nonempty'),
                ('repeated file', {'blocks': {**self.BLOCKS, 'candidates': ['project/blocks/RC.json'] * 2}},
                 'distinct')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                pa.validate_declaration(self.declaration(**changes))

    def test_block_files_must_be_captured_immutable_inputs(self):
        files = {BOARD: 'a', SCHEMATIC: 'b', POLICY: 'c', **{rel: rel[-8:] for rel in
                 [self.BLOCKS['netlist'], self.BLOCKS['arrangement'], *self.BLOCKS['candidates']]}}
        manifest = {'scope': {'board': BOARD, 'schematic': SCHEMATIC}, 'files': files}
        bound = pa.bind_declaration(self.declaration(), manifest, [BOARD])
        self.assertEqual(bound['blocks']['netlist_sha256'], files[self.BLOCKS['netlist']])
        for rel in (self.BLOCKS['netlist'], self.BLOCKS['arrangement'], self.BLOCKS['candidates'][0]):
            with self.subTest(rel):
                with self.assertRaisesRegex(ValueError, 'captured immutable input'):
                    pa.bind_declaration(self.declaration(), manifest, [BOARD, rel])
                with self.assertRaisesRegex(ValueError, 'captured immutable input'):
                    pa.bind_declaration(self.declaration(), {**manifest, 'files': {
                        k: v for k, v in files.items() if k != rel}}, [BOARD])

    def test_step_argv_names_the_bound_netlist_only_for_a_blocks_request(self):
        bound = {'board': BOARD, 'schematic': SCHEMATIC, 'timeout_s': 60}
        plain = pa.step_declaration('/step', bound)['argv']
        self.assertNotIn('--netlist', plain)
        blocks_bound = {**bound, 'blocks': {**self.BLOCKS, 'netlist_sha256': 'f' * 64}}
        self.assertEqual(pa.step_declaration('/step', blocks_bound)['argv'],
                         plain + ['--netlist', f"/candidate/{self.BLOCKS['netlist']}", '--netlist-sha256', 'f' * 64])

    def test_request_carries_blocks_beside_the_exact_request_policy(self):
        section = {'allocation_text': None, 'arrangement': {'schema': block_arrangement.POLICY}, 'candidates': []}
        policy = {'fixed_refs': ['H1'], 'gap_nm': 1, 'blocks': section}
        request = pa.helper_request(b'board', policy, ['J1'])
        self.assertEqual(request['blocks'], section)
        self.assertEqual(request['policy'], {'fixed_refs': ['H1', 'J1'], 'gap_nm': 1})
        self.assertEqual(request['policy'], pa.request_policy(policy, ['J1']))
        self.assertEqual(policy['fixed_refs'], ['H1'])
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / 'project').mkdir()
            write_json(inputs / POLICY, {'fixed_refs': [], 'blocks': section})
            manifest = {'scope': {}, 'files': {POLICY: digest(inputs / POLICY)}}
            with self.assertRaisesRegex(ValueError, 'blocks must come from the captured declaration'):
                pa._policy(inputs, {'policy': POLICY}, manifest)


class BlocksBindingTest(unittest.TestCase):
    """Candidates packed by the owned test_blocks fixture against a stamped netlist."""
    NETLIST, ARRANGEMENT, ALLOCATION = 'blocks/netlist.xml', 'blocks/arrangement.json', 'blocks/allocation.json'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-blocks-binding-')
        self.addCleanup(self.tmp.cleanup)
        self.inputs = Path(self.tmp.name)
        (self.inputs / 'blocks').mkdir()
        raw = native_xml().decode('utf-8').replace(
            '<design>', '<design><source>/candidate/owned.kicad_sch</source><date>2026-10-21T10:00:00</date>'
                        '<tool>Eeschema 10.0.7</tool>', 1).encode('utf-8')
        (self.inputs / self.NETLIST).write_bytes(raw)
        self.policy = block_policy()
        self.board = block_board()
        self.write_json(self.ALLOCATION, {'schema': blocks.ALLOCATION, 'blocks': [
            {'id': 'Power', 'sheets': ['/Power/']}, {'id': 'Sensor', 'sheets': ['/Sensor/']}]})
        self.write_json(self.ARRANGEMENT, {'schema': block_arrangement.POLICY, 'step_budget': 20000})
        self.candidates = [self.pack(block, f'blocks/{block}.json') for block in ('Power', 'Sensor')]
        self.section = {'netlist': self.NETLIST, 'arrangement': self.ARRANGEMENT, 'allocation': self.ALLOCATION,
                        'candidates': self.candidates}

    def write_json(self, rel, value):
        (self.inputs / rel).write_text(json.dumps(value))

    def pack(self, block, rel, policy=None, allocation=None):
        path = self.inputs / self.NETLIST
        allocation = (self.inputs / self.ALLOCATION).read_bytes() if allocation is None else allocation
        derivation = blocks.derive(read_native_inventory(path), read_sheet_names(path), self.board,
                                   inventory_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                   allocation_raw=allocation)
        packed = blocks.pack_block(self.board, read_native_inventory(path), derivation, block, policy or self.policy)
        self.assertEqual(packed['status'], 'packed', packed.get('reason'))
        target = self.inputs / rel
        if target.exists():
            target.unlink()
        blocks._write(target, packed['candidate'])
        return rel

    def manifest(self):
        return {'files': {p.relative_to(self.inputs).as_posix(): digest(p)
                          for p in self.inputs.rglob('*') if p.is_file()}}

    def bind(self, section=None, policy=None):
        return pa.blocks_binding(section or self.section, self.manifest(), self.inputs, policy or self.policy)

    def test_bound_candidates_record_digests_and_feed_the_placer_section(self):
        record, request = self.bind()
        files = self.manifest()['files']
        self.assertEqual(record['netlist'], {'path': self.NETLIST, 'sha256': files[self.NETLIST],
                                             'semantic_sha256': stamp_identity(self.inputs / self.NETLIST)['semantic_sha256'],
                                             'stamp_fields': list(STAMP_FIELDS)})
        self.assertEqual(record['request_policy_sha256'], blocks.digest(self.policy))
        self.assertEqual([c['block_id'] for c in record['candidates']], ['Power', 'Sensor'])
        self.assertEqual(record['allocation'], {'path': self.ALLOCATION, 'sha256': files[self.ALLOCATION]})
        self.assertEqual(request, block_arrangement.request_section(
            self.inputs / self.ALLOCATION, self.inputs / self.ARRANGEMENT, [self.inputs / c for c in self.candidates]))

    def test_changed_or_stale_block_files_refuse_before_any_iteration(self):
        manifest = self.manifest()
        (self.inputs / self.candidates[0]).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'captured placement block file blocks/Power.json changed'):
            pa.blocks_binding(self.section, manifest, self.inputs, self.policy)
        self.pack('Power', self.candidates[0])
        with self.assertRaisesRegex(ValueError, 'was not packed for this request.*policy_sha256'):
            self.bind(policy={**self.policy, 'gap_nm': 300000})
        self.pack('Power', self.candidates[0], policy={**self.policy, 'gap_nm': 300000})
        with self.assertRaisesRegex(ValueError, 'blocks/Power.json was not packed for this request.*policy_sha256'):
            self.bind()
        self.pack('Power', self.candidates[0])
        # A regenerated netlist is a different capture: candidates packed against the old bytes refuse.
        netlist = self.inputs / self.NETLIST
        netlist.write_bytes(with_stamps(netlist.read_bytes()))
        with self.assertRaisesRegex(ValueError, 'was not packed for this request.*inventory_sha256'):
            self.bind()

    def test_unstamped_netlist_invalid_candidate_and_duplicate_block_refuse(self):
        with self.assertRaisesRegex(ValueError, 'blocks/broken.json is invalid'):
            document = json.loads((self.inputs / self.candidates[0]).read_text())
            document['members'] = []
            self.write_json('blocks/broken.json', document)
            self.bind({**self.section, 'candidates': ['blocks/broken.json']})
        with self.assertRaisesRegex(ValueError, 'several candidates'):
            shutil.copyfile(self.inputs / self.candidates[0], self.inputs / 'blocks/again.json')
            self.bind({**self.section, 'candidates': [self.candidates[0], 'blocks/again.json']})
        (self.inputs / self.NETLIST).write_bytes(native_xml())
        with self.assertRaisesRegex(ValueError, 'no stamp-masked identity'):
            self.bind()


class PinnedPackageDataTest(unittest.TestCase):
    def test_policy_pin_and_snapshot_include_package_data(self):
        from agent_ee.cli import policy_files
        pins = policy_files()
        root = Path(pa.__file__).resolve().parent
        data = sorted(p.relative_to(root).as_posix() for p in (root / 'data').rglob('*') if p.is_file())
        self.assertIn('data/jlcpcb-locality.json', data)
        self.assertEqual({k: v for k, v in pins.items() if k.startswith('data/')},
                         {rel: digest(root / rel) for rel in data})
        with tempfile.TemporaryDirectory() as tmp:
            tool = pa.tool_snapshot(Path(tmp) / 'tool')
            pa.verify_snapshot(tool, pins)
            self.assertEqual(digest(tool / 'agent_ee/data/jlcpcb-locality.json'),
                             pins['data/jlcpcb-locality.json'])

    def test_snapshot_missing_or_changed_data_is_refused_and_cannot_admit_locality(self):
        from agent_ee.cli import policy_files
        probe = ('import sys; sys.path.insert(0, sys.argv[1]); '
                 'from agent_ee.locality_provenance import manufacturing_record; manufacturing_record(); print("ok")')
        with tempfile.TemporaryDirectory() as tmp:
            tool = pa.tool_snapshot(Path(tmp) / 'tool')
            done = subprocess.run([sys.executable, '-B', '-I', '-c', probe, str(tool)], capture_output=True,
                                  text=True, timeout=120)
            self.assertEqual((done.returncode, done.stdout.strip()), (0, 'ok'), done.stderr)
            record = tool / 'agent_ee/data/jlcpcb-locality.json'
            original = record.read_bytes()
            record.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'differs from the campaign source pin'):
                pa.verify_snapshot(tool, policy_files())
            record.unlink()
            with self.assertRaisesRegex(ValueError, 'differs from the campaign source pin'):
                pa.verify_snapshot(tool, policy_files())
            done = subprocess.run([sys.executable, '-B', '-I', '-c', probe, str(tool)], capture_output=True,
                                  text=True, timeout=120)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn('captured JLCPCB numeric record unavailable', done.stderr)


def sandboxed_netlist(design, work):
    """The captured netlist exactly as the helper step's sandbox exports it (/candidate, cwd /work)."""
    from agent_ee.executor import execute
    record = execute(design, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                    '-o', '/work/netlist.xml', f'/candidate/{SCHEMATIC}'], timeout=120)
    if record['execution'] != 'ok':
        raise AssertionError((Path(work) / 'console.log').read_text()[-2000:])
    return (Path(work) / 'netlist.xml').read_bytes()


def library_identities(design):
    """Set each owned footprint's library identity to the one the native export names."""
    import pcbnew as k
    import xml.etree.ElementTree as ET
    export = ET.fromstring(sandboxed_netlist(design, design.parent / 'identity-export'))
    names = {c.get('ref'): c.findtext('footprint') for c in export.findall('components/comp')}
    board = k.LoadBoard(str(design / BOARD))
    for fp in board.GetFootprints():
        lib, name = names[fp.GetReference()].split(':')
        fp.SetFPID(k.LIB_ID(lib, name))
    k.SaveBoard(str(design / BOARD), board)
    for generated in design.rglob('*'):
        if generated.suffix in ('.kicad_pro', '.kicad_prl'):
            generated.unlink()
    return design


class NativeNetlistIdentityDriverTest(unittest.TestCase):
    """The helper step argv with a captured netlist, run directly by the production executor."""
    CAPTURED = 'project/captured-netlist.xml'

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-netlist-identity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.design = owned_design(self.root / 'design')
        self.source = (self.design / BOARD).read_bytes()
        self.exported = sandboxed_netlist(self.design, self.root / 'export')

    def execute(self, name, captured, sha256=None):
        from agent_ee.executor import execute
        (self.design / self.CAPTURED).write_bytes(captured)
        bound = {'board': BOARD, 'schematic': SCHEMATIC, 'timeout_s': 300,
                 'blocks': {'netlist': self.CAPTURED, 'netlist_sha256': sha256 or hashlib.sha256(captured).hexdigest()}}
        spec, _ = pa.prepare_step(self.root / name / 'step', self.source, read_json(self.design / POLICY), bound, ['J1'])
        request_path = self.root / name / 'step/request/request.json'
        write_json(request_path, {**read_json(request_path), 'skip_visuals': True})
        work = self.root / name / 'work'
        record = execute(self.design, work, spec['argv'], timeout=300, network=False,
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        return record, work, read_json(work / 'placement/driver.json')

    def test_date_only_regeneration_is_used_with_its_identity_recorded(self):
        captured = with_stamps(self.exported)
        self.assertNotEqual(captured, self.exported)
        record, work, driver = self.execute('date', captured)
        self.assertEqual((record['execution'], driver['status']), ('ok', 'placed'),
                         (work / 'console.log').read_text()[-3000:])
        identity = driver['netlist_identity']
        self.assertEqual(identity['status'], 'semantic_match')
        self.assertEqual(identity['stamp_fields'], list(STAMP_FIELDS))
        self.assertEqual(identity['captured']['sha256'], hashlib.sha256(captured).hexdigest())
        self.assertEqual(identity['captured']['stamps']['date'], '2001-02-03T04:05:06')
        self.assertNotEqual(identity['exported']['stamps']['date'], '2001-02-03T04:05:06')
        self.assertEqual(identity['exported']['sha256'], digest(work / 'placement/netlist.xml'))
        self.assertEqual(identity['captured']['semantic_sha256'], identity['exported']['semantic_sha256'])
        # The placer consumed the captured bytes, not the fresh export.
        result = read_json(work / 'placement/placement-result.json')
        self.assertEqual(result['netlist_sha256'], hashlib.sha256(captured).hexdigest())

    def test_content_change_or_unbound_bytes_refuse_without_a_board(self):
        changed = with_stamps(self.exported).replace(b'<value>', b'<value>x', 1)
        for name, captured, sha256, status in (
                ('content', changed, None, 'netlist_identity_mismatch'),
                ('unbound', with_stamps(self.exported), 'e' * 64, 'netlist_identity_refused'),
                ('unstamped', self.exported.replace(b'<tool>', b'<tool-x>').replace(b'</tool>', b'</tool-x>'), None,
                 'netlist_identity_refused')):
            with self.subTest(name):
                record, work, driver = self.execute(name, captured, sha256)
                self.assertEqual((record['exit_code'], driver['status']), (4, status),
                                 (work / 'console.log').read_text()[-2000:])
                self.assertFalse((work / pa.OUTPUT).exists())
                self.assertFalse((work / 'placement/placement-result.json').exists())
        self.assertEqual(driver['netlist_identity'], None)
        _, _, driver = self.execute('content-again', changed)
        self.assertEqual(driver['netlist_identity']['status'], 'mismatch')
        self.assertNotEqual(driver['netlist_identity']['captured']['semantic_sha256'],
                            driver['netlist_identity']['exported']['semantic_sha256'])


class NativeBlocksIterationTest(unittest.TestCase):
    """A campaign placement iteration with a captured blocks request, through real stage receipts."""
    NETLIST, ALLOCATION = 'project/blocks/netlist.xml', 'project/blocks/allocation.json'
    ARRANGEMENT, CANDIDATE = 'project/blocks/arrangement.json', 'project/blocks/RC.json'

    @classmethod
    def setUpClass(cls):
        cls.receipts = stage_receipts()
        require_toolchain(native=True)

    def setUp(self):
        from agent_ee.placement_probe import extract
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-blocks-iteration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = library_identities(owned_design(self.root / 'runs/1/inputs'))
        self.fixed = uuid_of(self.source / BOARD, 'J1')
        # A regenerated capture: the sandbox export with an older date stamp.
        captured = with_stamps(sandboxed_netlist(self.source, self.root / 'capture'))
        (self.source / 'project/blocks').mkdir()
        (self.source / self.NETLIST).write_bytes(captured)
        allocation = json.dumps({'schema': blocks.ALLOCATION, 'blocks': [{'id': 'RC', 'references': ['C1', 'R1']}]})
        (self.source / self.ALLOCATION).write_text(allocation)
        write_json(self.source / self.ARRANGEMENT, {'schema': block_arrangement.POLICY, 'step_budget': 20000})
        self.state = {'history': [{'earliest_failing_stage': 'placement'}], 'mutable_inputs': [BOARD, SCHEMATIC],
                      'stage_receipts': {}}
        plain = {'schema': pa.ADAPTER_SCHEMA, 'helper': 'agent-ee.placer/1', 'policy': POLICY,
                 'fixed_footprints': [self.fixed], 'timeout_s': 300}
        self.manifest = self.capture()
        # Candidates are packed against the exact request policy this declaration derives.
        _, _, policy, _, refs = pa.binding(plain, self.manifest, self.source, self.state['mutable_inputs'])
        netlist = self.source / self.NETLIST
        facts = extract(self.source / BOARD)
        derivation = blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist), facts,
                                   inventory_sha256=hashlib.sha256(captured).hexdigest(),
                                   allocation_raw=allocation.encode())
        packed = blocks.pack_block(facts, read_native_inventory(netlist), derivation, 'RC',
                                   pa.request_policy(policy, refs))
        self.assertEqual(packed['status'], 'packed', packed.get('reason'))
        blocks._write(self.source / self.CANDIDATE, packed['candidate'])
        self.manifest = self.capture()
        self.declaration = {**plain, 'blocks': {'netlist': self.NETLIST, 'allocation': self.ALLOCATION,
                                                'arrangement': self.ARRANGEMENT, 'candidates': [self.CANDIDATE]}}
        self.work = self.root / 'implementations/1'
        self.design = self.work / 'review-work/implementation/design'
        shutil.copytree(self.source, self.design)

    def capture(self):
        files = {p.relative_to(self.source).as_posix(): digest(p) for p in self.source.rglob('*') if p.is_file()}
        return {'candidate_id': 'c' * 64, 'files': files,
                'scope': {'board': BOARD, 'schematic': SCHEMATIC, 'stages': {'schema': 'agent-ee.stages/1'},
                          'checks': []}}

    def iterate(self):
        return pa.run_placement_iteration(self.work, self.design, self.source, self.manifest, self.state,
                                          self.declaration, time.monotonic() + 900, receipts=self.receipts)

    def test_captured_blocks_request_is_placed_receipted_and_reverifiable(self):
        record = self.iterate()
        self.assertEqual((record['status'], record['helper']['status']), ('proposed', 'completed'), record['error'])
        receipt_dir = self.work / record['helper']['receipt']
        receipt = self.receipts.validate_receipt(receipt_dir)
        self.assertEqual(receipt['spec']['argv'][-4:], ['--netlist', f'/candidate/{self.NETLIST}', '--netlist-sha256',
                                                        self.manifest['files'][self.NETLIST]])
        # The package data the block admission reads is part of the receipted tool tree.
        self.assertEqual(digest(self.work / 'placement/1/tool/agent_ee/data/jlcpcb-locality.json'),
                         digest(Path(pa.__file__).resolve().parent / 'data/jlcpcb-locality.json'))
        driver = read_json(receipt_dir / 'run/placement/driver.json')
        self.assertEqual((driver['status'], driver['netlist_identity']['status']), ('placed', 'semantic_match'))
        self.assertEqual(driver['netlist_identity']['captured']['sha256'], self.manifest['files'][self.NETLIST])
        self.assertEqual(driver['netlist_identity']['captured']['stamps']['date'], '2001-02-03T04:05:06')
        result = read_json(receipt_dir / pa.RESULT)
        self.assertEqual(result['netlist_sha256'], self.manifest['files'][self.NETLIST])
        self.assertEqual(result['plan']['block_arrangement']['order'], ['RC'])
        self.assertEqual(record['proposal_scope']['status'], 'allowed')
        self.assertNotIn(self.fixed, {t['uuid'] for t in record['proposal_scope']['transforms']})
        request = read_json(self.work / 'placement/1/request/request.json')
        self.assertEqual([e['candidate']['block_id'] for e in request['blocks']['candidates']], ['RC'])
        self.assertNotIn('blocks', request['policy'])
        captured = self.root / 'runs/2/inputs'
        shutil.copytree(self.design, captured)
        args = (self.work, self.source, captured, self.manifest, self.declaration, self.state['mutable_inputs'])
        self.assertEqual(pa.verify_record(*args, receipts=self.receipts), record)
        # A different captured candidate set no longer re-derives the retained request.
        changed = {**self.declaration, 'blocks': {k: v for k, v in self.declaration['blocks'].items()
                                                  if k != 'allocation'}}
        with self.assertRaisesRegex(ValueError, 'was not packed for this request'):
            pa.verify_record(self.work, self.source, captured, self.manifest, changed, self.state['mutable_inputs'],
                             receipts=self.receipts)


if __name__ == '__main__':
    unittest.main()
