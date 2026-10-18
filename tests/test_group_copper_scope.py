"""Group-local copper in the placement write scope (AGENTEE-129).

A placement declaration that names a captured AGENTEE-56 sub-layout request
admits exactly the copper of each whole reused group: planned copies of a group
whose members are all new, bound to the captured sub-board bytes, on the voted
nets and inside the group region, as a fresh sandboxed pcbnew read shows. Any
other copper is refused.

The scope tests reuse the owned rotated instance B of fixtures/sublayout/top.kicad_pcb
with the owned routed module fixtures/generation/regulator.kicad_pcb, and edit
the result with real pcbnew saves. The campaign tests run the real campaign on an
actual AGENTEE-52 forward-annotated regulator scaffold: the scripted writer
applies the unchanged sub-layout helper, and fresh captured checks (native DRC
with schematic parity, courtyard and outline) grade the proposal. Only the agent
transport is scripted.
"""
import contextlib
import copy
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_ee import forward_annotation as fa
from agent_ee import placement_adapter as pa
from agent_ee import sublayout
from agent_ee.campaign import run_campaign, verify_campaign
from agent_ee.cli import verify
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.storage import digest, identity, read_json, write_json
from test_placement_campaign import STRIP, pcbnew, prompt_context
from test_schematic_emitter import PROJECT, captures, load, run_native, tables
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / 'fixtures/generation/regulator.kicad_pcb'
TOP = REPO / 'fixtures/sublayout/top.kicad_pcb'
MM = 1000000
SUBLAYOUT = {'request': 'subs/reuse.json', 'sub_boards': 'subs'}
# Instance B of the owned top board, rotated a quarter turn (tests/test_sublayout.py).
GROUP_B = dict(id='reg-b', sub_board='regulator.kicad_pcb', members={'J1': 'J2', 'U1': 'U2', 'C1': 'C2'},
               anchor_at_nm=[160 * MM, 120 * MM], rotation_deg=90)
# Real pcbnew edits of a saved board, saved without touching project settings.
EDIT = """import json, sys
import pcbnew as k
path, edit = sys.argv[1], json.loads(sys.argv[2])
board = k.LoadBoard(path)
items = {i.m_Uuid.AsString(): i for i in [*board.GetTracks(), *board.Zones(), *board.GetDrawings()]}
fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
for key, (dx, dy) in edit.get('shift', {}).items():
    items[key].Move(k.VECTOR2I(dx, dy))
for ref, (dx, dy) in edit.get('shift_footprint', {}).items():
    fps[ref].Move(k.VECTOR2I(dx, dy))
for ref, (dx, dy) in edit.get('shift_reference', {}).items():
    fps[ref].Reference().Move(k.VECTOR2I(dx, dy))
for ref, (dx, dy) in edit.get('shift_value', {}).items():
    fps[ref].Value().Move(k.VECTOR2I(dx, dy))
if edit.get('track'):
    (x0, y0), (x1, y1), net = edit['track']
    track = k.PCB_TRACK(board)
    track.SetStart(k.VECTOR2I(x0, y0))
    track.SetEnd(k.VECTOR2I(x1, y1))
    track.SetWidth(250000)
    track.SetLayer(k.F_Cu)
    track.SetNet(board.FindNet(net))
    board.Add(track)
for key in edit.get('lock', []):
    items[key].SetLocked(True)
for key in edit.get('remove', []):
    board.Remove(items[key])
k.SaveBoard(path, board, True)
"""


def edit_board(path, **edit):
    pcbnew(EDIT, path, json.dumps(edit))


def node_span(text, key):
    """Start and end of the top-level board node of a saved board that holds (uuid "key")."""
    start = text.rindex('\n\t(', 0, text.index(f'(uuid "{key}")')) + 2
    depth = 0
    for end in range(start, len(text)):
        depth += {'(': 1, ')': -1}.get(text[end], 0)
        if depth == 0 and text[end] == ')':
            return start, end + 1
    raise AssertionError(f'unterminated node {key}')


def shifted(node, dx_mm):
    """The saved node text with every point moved by dx_mm, as a writer's text edit would."""
    return re.sub(r'\((start|end|mid|xy|at) ([-0-9.]+) ([-0-9.]+)',
                  lambda m: f'({m.group(1)} {float(m.group(2)) + dx_mm:g} {m.group(3)}', node)


def request(*groups):
    return {'schema': sublayout.REQUEST_SCHEMA, 'groups': list(groups)}


def declaration(sub=SUBLAYOUT, fixed=()):
    value = {'schema': pa.ADAPTER_SCHEMA, 'helper': None, 'policy': None, 'fixed_footprints': list(fixed),
             'timeout_s': 300}
    if sub is not None:
        value['sublayout'] = sub
    return value


def inventory(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}


class DeclarationTest(unittest.TestCase):
    """Declaration and binding rules; no native tool is needed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-group-copper-binding-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / 'project').mkdir()
        shutil.copyfile(TOP, self.tmp / 'project/top.kicad_pcb')
        (self.tmp / 'subs').mkdir()
        shutil.copyfile(MODULE, self.tmp / 'subs/regulator.kicad_pcb')
        write_json(self.tmp / 'subs/reuse.json', request(dict(GROUP_B, sub_board_sha256=digest(MODULE))))

    def manifest(self):
        return {'scope': {'board': 'project/top.kicad_pcb', 'schematic': None, 'checks': []},
                'files': inventory(self.tmp)}

    def test_sublayout_is_optional_and_leaves_existing_identities_unchanged(self):
        plain = declaration(sub=None)
        self.assertNotIn('sublayout', pa.validate_declaration(plain))
        self.assertEqual(pa.validate_declaration({**plain, 'sublayout': SUBLAYOUT})['sublayout'], SUBLAYOUT)
        record = pa.binding(plain, self.manifest(), self.tmp, ['project/top.kicad_pcb'])[0]
        self.assertNotIn('sublayout', record)
        for bad in ({'request': 'subs/reuse.json'}, {**SUBLAYOUT, 'extra': 1}, {**SUBLAYOUT, 'request': '../r.json'},
                    {**SUBLAYOUT, 'request': '/abs/reuse.json'}, {**SUBLAYOUT, 'request': 'subs/reuse.txt'},
                    {**SUBLAYOUT, 'sub_boards': 'subs/../x'}, 'subs'):
            with self.assertRaises(ValueError, msg=bad):
                pa.validate_declaration({**plain, 'sublayout': bad})
        with self.assertRaises(ValueError):
            pa.validate_declaration({**plain, 'copper': SUBLAYOUT})

    def test_a_sublayout_with_a_helper_is_refused_before_any_iteration(self):
        # The pinned placer refuses boards with copper (SublayoutCampaignTest shows it), so a
        # helper after a reuse could only fail; the pair is refused by name instead.
        helped = {**declaration(), 'helper': 'agent-ee.placer/1', 'policy': 'project/placement-policy.json'}
        with self.assertRaisesRegex(ValueError, 'sublayout cannot be declared with a helper: agent-ee.placer/1 '
                                    'refuses boards that hold tracks, vias or zones'):
            pa.validate_declaration(helped)
        with self.assertRaisesRegex(ValueError, 'sublayout cannot be declared with a helper'):
            pa.binding(helped, self.manifest(), self.tmp, ['project/top.kicad_pcb'])
        # The same helper without a sub-layout is declared as before.
        del helped['sublayout']
        self.assertEqual(pa.validate_declaration(helped)['helper'], 'agent-ee.placer/1')

    def test_binding_records_the_captured_request_and_sub_board_hashes(self):
        record = pa.binding(declaration(), self.manifest(), self.tmp, ['project/top.kicad_pcb'])[0]
        self.assertEqual(record['sublayout'], {
            'request': {'path': 'subs/reuse.json', 'sha256': digest(self.tmp / 'subs/reuse.json')},
            'sub_boards': 'subs',
            'groups': [{'id': 'reg-b', 'sub_board': 'subs/regulator.kicad_pcb', 'sha256': digest(MODULE)}]})

    def test_binding_refuses_mutable_missing_or_changed_sub_layout_inputs(self):
        board = 'project/top.kicad_pcb'
        cases = {'request must be a captured immutable input': [board, 'subs/reuse.json'],
                 'sub-boards must be captured immutable inputs': [board, 'subs/regulator.kicad_pcb']}
        for fragment, mutable in cases.items():
            with self.assertRaisesRegex(ValueError, fragment):
                pa.binding(declaration(), self.manifest(), self.tmp, mutable)
        manifest = self.manifest()
        write_json(self.tmp / 'subs/reuse.json', request(dict(GROUP_B, sub_board_sha256='0' * 64)))
        with self.assertRaisesRegex(ValueError, 'captured sub-layout request changed'):
            pa.binding(declaration(), manifest, self.tmp, [board])
        with self.assertRaisesRegex(ValueError, 'differs from the sha256 that group reg-b declares'):
            pa.binding(declaration(), self.manifest(), self.tmp, [board])
        write_json(self.tmp / 'subs/reuse.json', request(dict(GROUP_B, sub_board='absent.kicad_pcb')))
        with self.assertRaisesRegex(ValueError, 'subs/absent.kicad_pcb of group reg-b must be a captured'):
            pa.binding(declaration(), self.manifest(), self.tmp, [board])
        write_json(self.tmp / 'subs/reuse.json', {'schema': sublayout.REQUEST_SCHEMA, 'groups': []})
        with self.assertRaisesRegex(ValueError, 'captured sub-layout request is invalid'):
            pa.binding(declaration(), self.manifest(), self.tmp, [board])


class GroupCopperScopeTest(unittest.TestCase):
    """The scope on a real rotated reuse, judged against real pcbnew edits of its output."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-group-copper-'))
        cls.source = cls.make_source(cls.tmp / 'source', MODULE)
        cls.bound = {'board': 'project/top.kicad_pcb', 'sublayout': SUBLAYOUT}
        cls.admission = pa.group_admission(cls.source, cls.bound)
        cls.reused = cls.reuse(cls.source, cls.tmp / 'reused')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def make_source(root, module):
        (root / 'project').mkdir(parents=True)
        shutil.copyfile(TOP, root / 'project/top.kicad_pcb')
        (root / 'subs').mkdir()
        shutil.copyfile(module, root / 'subs/regulator.kicad_pcb')
        write_json(root / 'subs/reuse.json', request(GROUP_B))
        return root

    @staticmethod
    def reuse(source, design):
        """The unchanged AGENTEE-56 helper, as a writer would run it, on a copy of the source."""
        shutil.copytree(source, design)
        scratch = Path(tempfile.mkdtemp(prefix='agent-ee-group-copper-reuse-'))
        try:
            record = sublayout.reuse(design / 'project/top.kicad_pcb', read_json(design / 'subs/reuse.json'),
                                     design / 'subs', scratch / 'out.kicad_pcb', scratch / 'work')
            if record['outcome'] != 'pass':
                raise AssertionError(record)
            shutil.copyfile(scratch / 'out.kicad_pcb', design / 'project/top.kicad_pcb')
        finally:
            shutil.rmtree(scratch)
        return design

    def design(self, **edit):
        design = Path(tempfile.mkdtemp(prefix='agent-ee-group-copper-design-', dir=self.tmp))
        shutil.rmtree(design)
        shutil.copytree(self.reused, design)
        if edit:
            edit_board(design / 'project/top.kicad_pcb', **edit)
        return design

    def scope(self, design, admission='default', source=None):
        return pa.proposal_scope(source or self.source, design, 'project/top.kicad_pcb', [],
                                 self.admission if admission == 'default' else admission)

    def plan(self):
        group, = self.admission['groups']
        return group['plan']

    def copied(self, kind):
        return sorted(c['uuid'] for c in self.plan()['copy'] if c['expected']['kind'] == kind)

    def assertRefused(self, report, fragment):
        self.assertEqual(report['status'], 'refused', report)
        self.assertTrue(any(fragment in v for v in report['violations']), report['violations'])

    def test_admission_plans_the_declared_group_from_captured_bytes(self):
        group, = self.admission['groups']
        self.assertEqual((group['id'], group['name'], group['sub_board']['sha256']),
                         ('reg-b', 'sublayout:reg-b', digest(MODULE)))
        self.assertEqual(self.admission['request_sha256'], digest(self.source / 'subs/reuse.json'))
        # Every copy UUID is derived from the group, the captured sub-board bytes and the source item.
        facts = sublayout.board_facts(MODULE, self.tmp / 'module-facts')
        sources = {i['uuid'] for i in facts['items'] if 'Edge.Cuts' not in i['layers']}
        self.assertEqual({c['source_uuid'] for c in group['plan']['copy']}, sources)
        for item in group['plan']['copy']:
            self.assertEqual(item['uuid'], str(uuid.uuid5(sublayout.NAMESPACE, json.dumps(
                ['reg-b', digest(MODULE), 'item', item['source_uuid']], separators=(',', ':')))))

    def test_exact_rotated_reuse_is_admitted_with_its_footprint_transforms(self):
        report = self.scope(self.reused)
        self.assertEqual((report['status'], report['violations']), ('allowed', []), report['violations'])
        self.assertEqual(sorted((t['reference'], t['rotation_deg']) for t in report['transforms']),
                         [('C2', 90), ('J2', 90), ('U2', 90)])
        group, = report['copper']['groups']
        self.assertEqual((group['status'], group['members'], group['rotation_deg']), ('reused', ['C2', 'J2', 'U2'], 90))
        self.assertEqual(group['nets'], {'/VIN': '/B/VIN', '/VOUT': '/B/VOUT', 'GND': 'GND'})
        self.assertEqual(group['items'], sorted(c['uuid'] for c in self.plan()['copy']))
        self.assertEqual(sum(group['copied'].values()), len(group['items']))
        self.assertTrue(group['copied']['ZONE'] and group['copied']['PCB_VIA'] and group['copied']['PCB_TRACK'])
        # The report is deterministic, so public verification can recompute it.
        self.assertEqual(identity(self.scope(self.reused)), identity(report))

    def test_without_a_declared_sub_layout_the_same_copper_is_refused(self):
        report = self.scope(self.reused, admission=None)
        self.assertRefused(report, 'board: added or changed segment')
        self.assertRefused(report, 'board: added or changed group')
        self.assertNotIn('copper', report)

    def test_unchanged_source_admits_nothing_and_reports_the_group_absent(self):
        report = self.scope(self.source)
        self.assertEqual(report['status'], 'allowed', report['violations'])
        self.assertEqual([g['status'] for g in report['copper']['groups']], ['absent'])

    def test_copper_outside_the_group_is_refused_even_inside_its_region(self):
        region = self.admission['groups'][0]['region_nm']
        middle = ((region[0] + region[2]) // 2, (region[1] + region[3]) // 2)
        report = self.scope(self.design(track=[middle, (middle[0] + MM, middle[1]), '/B/VIN']))
        self.assertRefused(report, 'board: added or changed segment')
        self.assertEqual(report['copper']['groups'][0]['status'], 'reused')

    def test_a_changed_copy_is_refused(self):
        track = self.copied('PCB_TRACK')[0]
        report = self.scope(self.design(shift={track: [MM, 0]}))
        self.assertRefused(report, f'PCB_TRACK {track} differs from the captured sub-board transform')
        self.assertEqual(report['copper']['groups'][0]['status'], 'refused')

    def test_a_copy_changed_beyond_geometry_and_net_is_refused(self):
        # pcbnew facts do not report the lock; the saved tokens are compared with the sub-board node.
        track = self.copied('PCB_TRACK')[0]
        report = self.scope(self.design(lock=[track]))
        self.assertRefused(report, f'segment {track} changed beyond the sub-board copy')

    def test_a_group_is_admitted_whole_or_not_at_all(self):
        via = self.copied('PCB_VIA')[0]
        self.assertRefused(self.scope(self.design(remove=[via])),
                           f'PCB_VIA {via} missing; a group is admitted whole or not at all')

    def test_a_member_moved_off_its_copper_is_refused(self):
        self.assertRefused(self.scope(self.design(shift_footprint={'C2': [2 * MM, 0]})),
                           'C2 pose differs from the sub-board transform')

    def test_a_member_field_off_the_plan_is_refused(self):
        self.assertRefused(self.scope(self.design(shift_value={'U2': [MM, 0]})),
                           'U2 Value differs from the sub-board')

    def test_copy_outside_the_group_region_is_refused(self):
        narrowed = copy.deepcopy(self.admission)
        region = narrowed['groups'][0]['region_nm']
        narrowed['groups'][0]['region_nm'] = [region[0] + MM, region[1] + MM, region[2] - MM, region[3] - MM]
        self.assertRefused(self.scope(self.reused, admission=narrowed), 'lies outside the group region')

    def test_placed_group_admits_no_new_copper_and_its_copper_stays_fixed(self):
        # Next iteration: the reused board is the source; its members are no longer new.
        later = pa.group_admission(self.reused, self.bound)
        group, = later['groups']
        self.assertNotIn('plan', group)
        self.assertEqual(group['findings'][0]['code'], 'not_new')
        self.assertEqual(self.scope(self.reused, later, source=self.reused)['status'], 'allowed')
        zone = self.copied('ZONE')[0]
        design = self.design(shift={zone: [MM, 0]})
        report = self.scope(design, later, source=self.reused)
        self.assertRefused(report, 'board: removed or changed zone')
        self.assertEqual(report['copper']['groups'][0]['status'], 'not_admissible')

    def test_sub_board_field_layout_is_followed_exactly(self):
        # A sub-board whose regulator Reference was moved by its designer: the reused member's
        # field follows the sub-board, which a footprint-only transform would refuse.
        module = self.tmp / 'moved-reference' / 'regulator.kicad_pcb'
        module.parent.mkdir()
        shutil.copyfile(MODULE, module)
        edit_board(module, shift_reference={'U1': [0, -MM]})
        source = self.make_source(self.tmp / 'moved-source', module)
        admission = pa.group_admission(source, self.bound)
        design = self.reuse(source, self.tmp / 'moved-design')
        plain = pa.write_scope((source / 'project/top.kicad_pcb').read_bytes(),
                               (design / 'project/top.kicad_pcb').read_bytes())
        self.assertTrue(any('U2' in v and 'property' in v for v in plain['violations']), plain['violations'])
        report = pa.proposal_scope(source, design, 'project/top.kicad_pcb', [], admission)
        self.assertEqual((report['status'], report['violations']), ('allowed', []), report['violations'])
        edit_board(design / 'project/top.kicad_pcb', shift_reference={'U2': [MM, 0]})
        report = pa.proposal_scope(source, design, 'project/top.kicad_pcb', [], admission)
        self.assertRefused(report, 'U2 Reference differs from the sub-board')

    def edited(self, text):
        design = self.design()
        (design / 'project/top.kicad_pcb').write_text(text)
        return design

    def test_a_repeated_copy_uuid_is_refused_in_either_order(self):
        # pcbnew keeps both nodes of a reused UUID, and a fresh read sees only one of them.
        text = (self.reused / 'project/top.kicad_pcb').read_text()
        track = self.copied('PCB_TRACK')[0]
        a, b = node_span(text, track)
        stray = shifted(text[a:b], 30)
        for name, edit in (('stray after the copy', text[:b] + '\n' + stray + text[b:]),
                           ('stray before the copy', text[:a] + stray + '\n' + text[a:])):
            with self.subTest(name):
                report = self.scope(self.edited(edit))
                self.assertRefused(report, f'board item {track} appears 2 times')
                if name == 'stray before the copy':
                    self.assertRefused(report, f'segment {track} repeats an admitted copy UUID')

    def test_zone_fill_must_be_exactly_a_coordinator_refill(self):
        text = (self.reused / 'project/top.kicad_pcb').read_text()
        zone, = self.copied('ZONE')
        report = self.scope(self.reused)
        self.assertEqual(list(report['copper']['groups'][0]['zone_fills']), [zone])
        a, b = node_span(text, zone)
        node = text[a:b]
        layer = re.search(r'\(layers? "([^"]+)"', node).group(1)
        self.assertIn('filled_polygon', node)
        region = [v / MM for v in self.admission['groups'][0]['region_nm']]

        def forged(on, x0, y0):
            fill = (f'\n\t\t(filled_polygon\n\t\t\t(layer "{on}")\n\t\t\t(pts\n\t\t\t\t(xy {x0} {y0}) '
                    f'(xy {x0 + 15} {y0}) (xy {x0 + 15} {y0 + 15}) (xy {x0} {y0 + 15})\n\t\t\t)\n\t\t)\n\t')
            return text[:a] + node[:-1].rstrip() + fill + ')' + text[b:]
        unfilled = re.sub(r'\n\t\t\(filled_polygon\n.*?\n\t\t\)', '', node, flags=re.S)
        self.assertNotIn('filled_polygon', unfilled)
        cases = {'a forged fill far outside the group region': forged(layer, region[2] + 20, region[1]),
                 'a forged fill inside the zone outline': forged(layer, region[0] + 1, region[1] + 1),
                 'a forged fill on a layer the zone is not on': forged('F.Cu' if layer != 'F.Cu' else 'B.Cu',
                                                                     region[0] + 1, region[1] + 1),
                 'an unfilled copy': text[:a] + unfilled + text[b:]}
        for name, edit in cases.items():
            with self.subTest(name):
                report = self.scope(self.edited(edit))
                self.assertRefused(report, f'ZONE {zone} saved fill is not exactly a coordinator refill')
                self.assertEqual(report['copper']['groups'][0]['status'], 'refused')

    def test_protected_member_refuses_the_group(self):
        protected = pa.protected_footprints(TOP.read_bytes(), [], ['U2'])[0]
        report = pa.proposal_scope(self.source, self.reused, 'project/top.kicad_pcb', protected, self.admission)
        self.assertRefused(report, 'protected footprint transform changed')


BOARD = 'project/regulator.kicad_pcb'
SCHEMATIC = 'project/regulator.kicad_sch'
REQUEST = 'subs/reuse.json'
TEMPLATE = [{'path': '/usr/share/kicad/template', 'at': 'stock/template', 'include': ['sym-lib-table', 'fp-lib-table']}]
# The anchor U1 of the owned routed module sits at (115, 107) mm on its 30 x 20 mm outline.
GROUP = dict(id='reg', sub_board='regulator.kicad_pcb', members={'J1': 'J1', 'U1': 'U1', 'C1': 'C1'},
             anchor_at_nm=[115 * MM, 107 * MM])
CHECKS = [{'id': 'drc-placement', 'kind': 'drc', 'stage': 'placement', 'params': {'projection': 'placement-stage/1'}},
          {'id': 'courtyard', 'kind': 'placement', 'stage': 'placement',
           'params': {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc-placement'}},
          {'id': 'outline', 'kind': 'placement', 'stage': 'placement',
           'params': {'criterion': 'outline-containment/1', 'drc_check': 'drc-placement'}},
          {'id': 'drc-final', 'kind': 'drc', 'stage': 'routing'}]


def regulator_scaffold(root):
    """An actual AGENTEE-52 forward annotation of the emitted regulator schematic onto the owned
    module's outline, with the owned routed module captured as the sub-board of one request."""
    base = Path(root) / 'base'
    project = base / 'project'
    intent = load('regulator.intent.json')
    result, files = emit_schematic(intent, captures(intent), load('regulator.layout.json'), **tables())
    if result['execution'] != 'ok':
        raise AssertionError(result['findings'])
    shutil.copytree(PROJECT / 'lib', project / 'lib')
    for name, data in files.items():
        (project / name).write_bytes(data)
    # The scaffold: the module's own outline and setup with every footprint, track and zone
    # removed, then the emitted schematic's native netlist forward-annotated onto it.
    board = base / BOARD
    shutil.copyfile(MODULE, board)
    pcbnew(STRIP, board, 'footprints')
    work = Path(root) / 'annotation'
    run_native(project, work / 'xml', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
               '-o', '/work/design.xml', '/candidate/regulator.kicad_sch')
    xml = work / 'xml' / 'design.xml'
    manifest = load('footprints.manifest.json')
    plan = fa.plan(board, xml, manifest, PROJECT, work / 'plan')
    if plan['execution'] != 'ok':
        raise AssertionError(plan['findings'])
    applied = fa.apply(plan, board, xml, manifest, PROJECT, board, work / 'apply', backup_dir=work / 'backup')
    if applied['execution'] != 'ok':
        raise AssertionError(applied['findings'])
    for generated in project.glob('*.kicad_prl'):
        generated.unlink()
    (base / 'subs').mkdir()
    shutil.copyfile(MODULE, base / 'subs/regulator.kicad_pcb')
    write_json(base / REQUEST, request(dict(GROUP, sub_board_sha256=digest(MODULE))))
    (base / 'requirements').mkdir()
    (base / 'requirements/placement.md').write_text('Owned AGENTEE-129 sub-layout campaign requirements.\n')
    return base


class SublayoutCampaignTest(unittest.TestCase):
    """A staged campaign whose placement iteration reuses one group with its copper."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-group-copper-campaign-')
        cls.base = regulator_scaffold(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='agent-ee-group-copper-campaign-run-')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.source = self.root / 'source'
        shutil.copytree(self.base, self.source)
        self.writes, self.prompts, self.reuse_records = {}, {}, []
        self.addCleanup(patch.stopall)
        patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3', 'kicad-cli')}).start()

    def scope(self):
        requirements = [{'id': f"R-{c['id']}", 'statement': f"Owned requirement for {c['id']}",
                         'source': {'path': 'requirements/placement.md', 'locator': c['id']},
                         'checks': [c['id']], 'intent': 'acceptance'} for c in CHECKS]
        return {'schema': 'agent-ee.scope/3', 'name': 'sublayout-campaign', 'scope': 'owned regulator reuse campaign',
                'board': BOARD, 'schematic': SCHEMATIC,
                'inputs': [{'path': str(self.source / name), 'at': name} for name in ('project', 'subs', 'requirements')]
                + TEMPLATE,
                'requirements': ['requirements/placement.md'], 'mutable_inputs': [BOARD, SCHEMATIC],
                'checks': [dict(c) for c in CHECKS],
                'stages': {'schema': 'agent-ee.stages/1', 'order': ['placement', 'routing'],
                           'subjects': {'placement': {'path': BOARD, 'kind': 'footprints',
                                                      'references': ['C1', 'J1', 'U1']}},
                           'finding_retry_limit': 4},
                'baseline': {'requirements': requirements, 'targets': ['requirements/placement.md'],
                             'reference_architectures': ['requirements/placement.md']}}

    def campaign(self, sub=SUBLAYOUT, iterations=2, adapter=None):
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 600}
        profile = {'schema': 'agent-ee.profile/1', 'name': 'sublayout-campaign-test',
                   'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
                   'adjudicator': definition}
        write_json(self.root / 'scope.json', self.scope())
        write_json(self.root / 'campaign.json', {
            'schema': 'agent-ee.campaign/1', 'scope': 'scope.json', 'review_profile': profile,
            'implementation': definition, 'max_iterations': iterations, 'max_seconds': 3600,
            'placement_adapter': adapter or declaration(sub)})
        self.out = self.root / 'campaign'
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', self.out)
        return status, read_json(self.out / 'state.json')

    def reuse(self, design, **edit):
        """The scripted writer applies the unchanged AGENTEE-56 helper; its scratch stays outside the design."""
        scratch = Path(tempfile.mkdtemp(prefix='writer-reuse-', dir=self.root))
        record = sublayout.reuse(design / BOARD, read_json(design / REQUEST), design / 'subs',
                                 scratch / 'out.kicad_pcb', scratch / 'work')
        self.reuse_records.append(record)
        if record['execution'] != 'ok' or record['output'] is None:
            raise AssertionError(record)
        shutil.copyfile(scratch / 'out.kicad_pcb', design / BOARD)
        if edit:
            edit_board(design / BOARD, **edit)

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        if label == 'implementation':
            number = int(run.name)
            self.prompts[number] = prompt
            write = self.writes.get(number)
            if write:
                write(run / 'review-work/implementation/design')
            return {'execution': 'ok'}, {'summary': 'Scripted sub-layout reuse; not evidence'}
        packet = read_json(run / 'packet.json')
        ident = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            checks = [read_json(p) for p in (run / 'checks').glob('*.json')]
            decisions = [{'finding_id': 'check/' + c['id'], 'disposition': 'accepted', 'reason': 'check defect',
                          'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome', 'pointer': '/outcome'}]}
                         for c in checks if c['outcome'] == 'defect']
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', **ident, 'decisions': decisions}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/2', **ident,
            'packet_sha256': packet['packet_sha256'], 'role': label,
            'coverage': ['placement protocol'], 'limitations': ['scripted test response'], 'findings': []}

    def outcomes(self, number):
        return {c['id']: c['outcome'] for c in verify(self.out / 'runs' / str(number))['checks']}

    def test_reused_group_with_its_copper_is_captured_and_passes_fresh_drc(self):
        self.writes = {1: self.reuse}
        status, state = self.campaign()
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state)
        first = state['history'][0]
        self.assertEqual((first['earliest_failing_stage'], first['stage_failures']['placement']),
                         ('placement', ['outline']))
        self.assertEqual(self.outcomes(1)['outline'], 'defect')
        # Fresh captured checks of the next candidate grade the reused copper, including full DRC.
        self.assertEqual(self.outcomes(2), {'drc-placement': 'pass', 'courtyard': 'pass', 'outline': 'pass',
                                            'drc-final': 'pass'})
        drc = read_json(self.out / 'runs/2/work/drc-final/drc.json')
        self.assertEqual((drc['violations'], drc['unconnected_items'], drc['schematic_parity']), ([], [], []))
        # The helper's own pass is not the grade; it only produced the proposal.
        self.assertEqual(self.reuse_records[0]['outcome'], 'pass')
        record = read_json(self.out / 'implementations/1/placement-adapter.json')
        self.assertEqual((record['status'], record['helper']), ('proposed', None))
        self.assertEqual(record['writer_scope'], record['proposal_scope'])
        scope = record['proposal_scope']
        self.assertEqual(sorted(t['reference'] for t in scope['transforms']), ['C1', 'J1', 'U1'])
        group, = scope['copper']['groups']
        self.assertEqual((group['id'], group['status'], group['sub_board']['sha256']), ('reg', 'reused', digest(MODULE)))
        self.assertEqual(scope['copper']['request_sha256'], digest(self.source / REQUEST))
        candidate = read_json(self.out / 'runs/2/candidate.json')
        self.assertEqual(candidate['files'][BOARD], scope['after_sha256'])
        self.assertEqual(candidate['files']['subs/regulator.kicad_pcb'], digest(MODULE))
        binding = state['placement_binding']['sublayout']
        self.assertEqual(binding['groups'], [{'id': 'reg', 'sub_board': 'subs/regulator.kicad_pcb',
                                              'sha256': digest(MODULE)}])
        self.assertIn('group-local copper of the captured sub-layout request subs/reuse.json', self.prompts[1])
        self.assertIn('This is a placement iteration', self.prompts[1])
        self.assertEqual(prompt_context(self.prompts[1])['placement']['findings'][0]['local_id'], 'outline')
        self.assertEqual(verify_campaign(self.out)['state'], 'criteria_satisfied')
        # The captured source tree is never written.
        self.assertEqual(inventory(self.source), inventory(self.base))

    def test_stray_copper_beside_the_reused_group_refuses_the_iteration(self):
        self.writes = {1: lambda design: self.reuse(design, track=[(110 * MM, 103 * MM), (112 * MM, 103 * MM), 'GND'])}
        status, state = self.campaign()
        self.assertEqual((status, state['state']), (1, 'incomplete'), state)
        self.assertIn('placement writer proposal changed more than footprint transforms or admitted group copper',
                      state['error'])
        self.assertIn('board: added or changed segment', state['error'])
        self.assertEqual(len(state['history']), 1)
        self.assertFalse((self.out / 'runs/2').exists())
        self.assertFalse((self.out / 'implementations/1/proposal.scope.json').exists())
        record = read_json(self.out / 'implementations/1/placement-adapter.json')
        self.assertEqual((record['status'], record['writer_scope']['copper']['groups'][0]['status']),
                         ('refused', 'reused'))
        # Public verification recomputes the refused scope proof, copper section included.
        self.assertEqual(verify_campaign(self.out)['state'], 'incomplete')

    def test_a_helper_beside_a_sub_layout_refuses_the_campaign_before_any_writer(self):
        self.writes = {1: self.reuse}
        adapter = {**declaration(), 'helper': 'agent-ee.placer/1', 'policy': 'project/placement-policy.json'}
        with self.assertRaisesRegex(ValueError, 'sublayout cannot be declared with a helper'):
            self.campaign(adapter=adapter)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.prompts, {})

    def test_the_pinned_placer_refuses_a_board_holding_reused_copper(self):
        # Why the pair is refused: even with every reused member fixed, the coordinator-run
        # placer step refuses the writer's board once it holds the group's copper.
        from agent_ee import stage_receipts
        design = self.root / 'design'
        shutil.copytree(self.source, design)
        self.reuse(design)
        policy = {'schema': 'agent-ee.packing-policy/1', 'step_budget': 100000, 'gap_nm': 0, 'fixed_refs': [],
                  'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': []}
        bound = {'board': BOARD, 'schematic': SCHEMATIC, 'timeout_s': 300}
        spec, request = pa.prepare_step(self.root / 'placement/1', (design / BOARD).read_bytes(), policy, bound,
                                        ['C1', 'J1', 'U1'])
        self.assertEqual(request['policy']['fixed_refs'], ['C1', 'J1', 'U1'])
        receipt_dir = self.root / 'receipts/1-agent-ee-placer'
        receipt = stage_receipts.run_step(receipt_dir, design, spec, {'implementation': 1})
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('initial placer refuses existing tracks, vias or zones',
                      (receipt_dir / 'run/stderr.log').read_text())
        with self.assertRaisesRegex(ValueError, 'placement helper failed'):
            pa.qualify_receipt(receipt_dir, receipt, BOARD, [])

    def test_without_a_declared_sub_layout_the_reuse_is_refused(self):
        self.writes = {1: self.reuse}
        status, state = self.campaign(sub=None)
        self.assertEqual((status, state['state']), (1, 'incomplete'), state)
        self.assertIn('board: added or changed zone', state['error'])
        self.assertNotIn('sublayout', state['placement_binding'])
        self.assertNotIn('group-local copper', self.prompts[1])
        self.assertFalse((self.out / 'runs/2').exists())
        self.assertEqual(verify_campaign(self.out)['state'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
