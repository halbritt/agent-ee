"""Sub-layout reuse of the owned routed regulator module (AGENTEE-56).

The sub-board is the owned routed fixture fixtures/generation/regulator.kicad_pcb;
the top board (fixtures/sublayout/top.kicad_pcb) holds three unplaced
instances of the same module. Positions and nets of the result are judged from
the raw saved S-expressions through the shared reader and the fixture's own net
design, and DRC by a separate native run, never by the helper's own facts.
"""
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_ee import sublayout
from agent_ee.symbol_library import children, nanometres, parse_sexpr
from toolchain import require_toolchain

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
SUB = FIXTURES / 'generation' / 'regulator.kicad_pcb'
TOP = FIXTURES / 'sublayout' / 'top.kicad_pcb'
MM = 1000000
GROUPS = [
    dict(id='reg-a', sub_board='regulator.kicad_pcb', members={'J1': 'J1', 'U1': 'U1', 'C1': 'C1'},
         anchor_at_nm=[120 * MM, 110 * MM]),
    dict(id='reg-b', sub_board='regulator.kicad_pcb', members={'J1': 'J2', 'U1': 'U2', 'C1': 'C2'},
         anchor_at_nm=[160 * MM, 120 * MM], rotation_deg=90),
    dict(id='reg-c', sub_board='regulator.kicad_pcb', members={'J1': 'J3', 'U1': 'U3', 'C1': 'C3'},
         anchor_at_nm=[205 * MM, 120 * MM], rotation_deg=180),
]
# The fixture's net design (fixtures/sublayout/generate.py), independent of the vote.
NETS = {'reg-a': {'/VIN': '/A/VIN', '/VOUT': '/A/VOUT', 'GND': 'GND'},
        'reg-b': {'/VIN': '/B/VIN', '/VOUT': '/B/VOUT', 'GND': 'GND'},
        'reg-c': {'/VOUT': '/C/VOUT', 'GND': 'GND'}}
_RUNS = {}
_TEMPORARY = []
# Owned board graphics added to the module for the graphics test (silk line, drawing arc, silk text).
GRAPHICS = b"""\t(gr_line (start 101 118) (end 106 118) (stroke (width 0.12) (type solid)) (layer "F.SilkS")
\t\t(uuid "56000000-0000-4000-8000-000000000001"))
\t(gr_arc (start 101 102) (mid 102.5 101.5) (end 104 102) (stroke (width 0.1) (type solid)) (layer "Dwgs.User")
\t\t(uuid "56000000-0000-4000-8000-000000000002"))
\t(gr_text "REG" (at 110 118 0) (layer "F.SilkS") (uuid "56000000-0000-4000-8000-000000000003")
\t\t(effects (font (size 1 1) (thickness 0.15))))
"""


def tearDownModule():
    for path in _TEMPORARY:
        shutil.rmtree(path, ignore_errors=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request(groups):
    return {'schema': sublayout.REQUEST_SCHEMA, 'groups': groups}


def run(groups, board=TOP, subs=None):
    """One reuse in a fresh directory; the captured sub-board root holds only the owned module."""
    tmp = Path(tempfile.mkdtemp(prefix='agent-ee-sublayout-'))
    _TEMPORARY.append(tmp)
    root = tmp / 'subs'
    root.mkdir()
    for name, path in (subs or {'regulator.kicad_pcb': SUB}).items():
        shutil.copyfile(path, root / name)
    out = tmp / 'out.kicad_pcb'
    record = sublayout.reuse(board, request(groups), root, out, tmp / 'work')
    return record, out, tmp


def shared(name, groups):
    if name not in _RUNS:
        _RUNS[name] = run(groups)
    return _RUNS[name]


def at(node):
    xy = children(node, 'at')[0]
    return [nanometres(xy[1]), nanometres(xy[2])], (float(xy[3]) if len(xy) > 3 else 0.0) % 360


def footprints(root):
    return {children(fp, 'property')[0][2]: fp for fp in children(root, 'footprint')}


def rotate(vector, rotation):
    x, y = vector
    for _ in range(rotation // 90):
        x, y = y, -x
    return [x, y]


def points(node):
    keys = {'segment': ('start', 'end'), 'arc': ('start', 'mid', 'end'), 'via': ('at',)}[node[0]]
    return [[nanometres(children(node, k)[0][1]), nanometres(children(node, k)[0][2])] for k in keys]


def net(node):
    found = children(node, 'net')
    return found[0][1] if found else ''


def copper(root, uuids=None):
    """(kind, net, layers, width, points) of raw segments, arcs and vias."""
    found = []
    for node in root[1:]:
        if isinstance(node, list) and node[0] in ('segment', 'arc', 'via'):
            if uuids is not None and children(node, 'uuid')[0][1] not in uuids:
                continue
            layers = children(node, 'layer') or children(node, 'layers')
            width = children(node, 'width') or children(node, 'size')
            found.append((node[0], net(node), tuple(layers[0][1:]), width[0][1], points(node)))
    return found


def group_members(root, name):
    for node in children(root, 'group'):
        if node[1] == name:
            return set(children(node, 'members')[0][1:])
    return None


class SublayoutReuseTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)

    def test_relative_positions_equal_the_sub_board_up_to_rotation(self):
        record, out, _ = shared('three', GROUPS)
        self.assertEqual(record['execution'], 'ok', record['findings'])
        sub, top = footprints(parse_sexpr(SUB.read_bytes())), footprints(parse_sexpr(out.read_bytes()))
        anchor, _ = at(sub['U1'])
        for group in GROUPS:
            rotation = group.get('rotation_deg', 0)
            result = next(g for g in record['groups'] if g['id'] == group['id'])
            self.assertEqual(result['anchor'], {'sub_ref': 'U1', 'top_ref': group['members']['U1']})
            for ref, top_ref in group['members'].items():
                (x, y), angle = at(sub[ref])
                (tx, ty), top_angle = at(top[top_ref])
                ax, ay = group['anchor_at_nm']
                self.assertEqual([tx - ax, ty - ay], rotate([x - anchor[0], y - anchor[1]], rotation), top_ref)
                self.assertEqual(top_angle, (angle + rotation) % 360, top_ref)
            # Pads judged too: each pad's saved local offset is unchanged.
            for ref, top_ref in group['members'].items():
                self.assertEqual([at(p)[0] for p in children(sub[ref], 'pad')],
                                 [at(p)[0] for p in children(top[top_ref], 'pad')])
        self.assertEqual(at(top['J9']), at(footprints(parse_sexpr(TOP.read_bytes()))['J9']))

    def test_copied_copper_lands_on_the_voted_net(self):
        record, out, _ = shared('three', GROUPS)
        sub_root, root = parse_sexpr(SUB.read_bytes()), parse_sexpr(out.read_bytes())
        anchor, _ = at(footprints(sub_root)['U1'])
        sub_uuids = {children(n, 'uuid')[0][1] for n in sub_root[1:] if isinstance(n, list)
                     and n[0] in ('segment', 'arc', 'via', 'zone')}
        for group in GROUPS:
            rotation, (ax, ay) = group.get('rotation_deg', 0), group['anchor_at_nm']
            members = group_members(root, f"sublayout:{group['id']}")
            self.assertIsNotNone(members, group['id'])
            self.assertFalse(members & sub_uuids, 'copies must carry new UUIDs')
            expected = []
            for kind, sub_net, layers, width, pts in copper(sub_root):
                if sub_net in NETS[group['id']]:
                    moved = [[ax + dx, ay + dy] for dx, dy in
                             (rotate([x - anchor[0], y - anchor[1]], rotation) for x, y in pts)]
                    expected.append((kind, NETS[group['id']][sub_net], layers, width, moved))
            self.assertEqual(sorted(copper(root, members)), sorted(expected), group['id'])
            zones = [z for z in children(root, 'zone') if children(z, 'uuid')[0][1] in members]
            self.assertEqual([(net(z), children(z, 'layer')[0][1]) for z in zones], [('GND', 'B.Cu')])
            self.assertTrue(children(zones[0], 'filled_polygon'), 'copied zone was refilled')
            result = next(g for g in record['groups'] if g['id'] == group['id'])
            mapped = {e['sub_net']: e['top_net'] for e in result['net_map'] if e['status'] == 'mapped'}
            self.assertEqual(mapped, NETS[group['id']])
            self.assertEqual(result['unnetted_copper'], [])
        # No copper anywhere on the board was created without a net.
        self.assertFalse([c for c in copper(root) if not c[1]])

    def test_copper_on_an_unmapped_net_is_reported_not_created(self):
        record, out, _ = shared('three', GROUPS)
        result = next(g for g in record['groups'] if g['id'] == 'reg-c')
        self.assertEqual((result['status'], result['outcome']), ('partial', 'unknown'))
        vin = [e for e in result['net_map'] if e['sub_net'] == '/VIN']
        self.assertEqual(vin, [dict(sub_net='/VIN', status='unmapped', top_net=None, votes={}, abstentions=2)])
        self.assertEqual(sorted(w['source_uuid'] for w in result['withheld']),
                         sorted(children(n, 'uuid')[0][1] for n in children(parse_sexpr(SUB.read_bytes()), 'segment')
                                if net(n) == '/VIN'))
        self.assertEqual({w['reason'] for w in result['withheld']}, {'unmapped'})
        self.assertEqual(record['outcome'], 'unknown', 'a partial group never passes')

    def test_drc_introduces_no_new_violation_inside_a_group(self):
        record, out, tmp = shared('three', GROUPS)
        for result in record['groups']:
            self.assertEqual(result['drc'], {'new_violations': []}, result['id'])
        self.assertEqual([g['outcome'] for g in record['groups']], ['pass', 'pass', 'unknown'])
        # A separate native run: only the input's own library-link warnings remain.
        reports = {}
        for name, board in (('before', TOP), ('after', out)):
            work = tmp / f'independent-{name}'
            work.mkdir()
            shutil.copyfile(board, work / 'board.kicad_pcb')
            subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--units', 'mm',
                            '-o', str(work / 'drc.json'), str(work / 'board.kicad_pcb')],
                           check=True, capture_output=True, timeout=300)
            reports[name] = json.loads((work / 'drc.json').read_text())
        types = {name: sorted(v['type'] for v in r['violations']) for name, r in reports.items()}
        self.assertEqual(types['after'], types['before'])
        self.assertEqual(set(types['after']), {'lib_footprint_issues'})

    def test_overlapping_group_is_a_defect_inside_its_box(self):
        overlap = copy.deepcopy(GROUPS[:2])
        overlap[1].update(anchor_at_nm=[123 * MM, 112 * MM], rotation_deg=0)
        record, out, _ = run(overlap)
        self.assertEqual(record['execution'], 'ok', record['findings'])
        self.assertEqual(record['outcome'], 'defect')
        result = next(g for g in record['groups'] if g['id'] == 'reg-b')
        self.assertEqual(result['outcome'], 'defect')
        types = {v['type'] for v in result['drc']['new_violations']}
        self.assertTrue(types & {'shorting_items', 'clearance', 'courtyards_overlap', 'tracks_crossing'}, types)
        self.assertTrue(out.is_file(), 'the defective result is kept for inspection')

    def test_missing_or_changed_sub_board_is_unknown_and_identity_recorded(self):
        groups = copy.deepcopy(GROUPS)
        groups[0]['sub_board'] = 'absent.kicad_pcb'
        groups[2]['sub_board_sha256'] = '0' * 64
        record, out, _ = run(groups)
        self.assertEqual(record['execution'], 'ok', record['findings'])
        by_id = {g['id']: g for g in record['groups']}
        self.assertEqual((by_id['reg-a']['status'], by_id['reg-a']['outcome']), ('unknown', 'unknown'))
        self.assertEqual(by_id['reg-a']['sub_board'],
                         dict(path='absent.kicad_pcb', sha256=None, declared_sha256=None))
        self.assertEqual(by_id['reg-a']['findings'][0]['code'], 'sub_board_missing')
        self.assertEqual(by_id['reg-c']['status'], 'unknown')
        self.assertEqual(by_id['reg-c']['findings'][0]['code'], 'sub_board_changed')
        self.assertEqual(by_id['reg-c']['sub_board']['sha256'], sha(SUB))
        self.assertEqual((by_id['reg-b']['status'], by_id['reg-b']['outcome']), ('populated', 'pass'))
        self.assertEqual(by_id['reg-b']['sub_board']['sha256'], sha(SUB))
        self.assertEqual(record['outcome'], 'unknown')
        # Unknown groups leave their footprints where they were.
        before, after = footprints(parse_sexpr(TOP.read_bytes())), footprints(parse_sexpr(out.read_bytes()))
        for ref in ('J1', 'U1', 'C1', 'J3', 'U3', 'C3'):
            self.assertEqual(at(after[ref]), at(before[ref]), ref)

    def test_layout_is_pulled_only_when_every_footprint_is_new(self):
        _, placed, _ = shared('three', GROUPS)
        record, out, _ = run(GROUPS[:1], board=placed)
        self.assertEqual(record['execution'], 'ok', record['findings'])
        self.assertEqual(record['groups'][0]['status'], 'refused')
        self.assertEqual(record['groups'][0]['findings'][0]['code'], 'not_new')
        self.assertEqual(record['outcome'], 'unknown')
        self.assertFalse(out.exists(), 'nothing was pulled, so nothing is written')

    def test_board_graphics_follow_the_rotation(self):
        tmp = Path(tempfile.mkdtemp(prefix='agent-ee-sublayout-graphics-'))
        _TEMPORARY.append(tmp)
        data = SUB.read_bytes()
        tail = data.rindex(b'\t(embedded_fonts no)')
        variant = tmp / 'graphics.kicad_pcb'
        variant.write_bytes(data[:tail] + GRAPHICS + data[tail:])
        group = dict(GROUPS[1], sub_board='graphics.kicad_pcb')
        record, out, _ = run([group], subs={'graphics.kicad_pcb': variant})
        self.assertEqual(record['execution'], 'ok', record['findings'])
        result = record['groups'][0]
        self.assertEqual((result['status'], result['outcome']), ('populated', 'pass'), result.get('drc'))
        self.assertEqual((result['copied']['PCB_SHAPE'], result['copied']['PCB_TEXT']), (2, 1))
        root = parse_sexpr(out.read_bytes())
        members = group_members(root, 'sublayout:reg-b')
        anchor, (ax, ay) = at(footprints(parse_sexpr(SUB.read_bytes()))['U1'])[0], group['anchor_at_nm']
        def moved(x, y):
            dx, dy = rotate([nanometres(x) - anchor[0], nanometres(y) - anchor[1]], 90)
            return [ax + dx, ay + dy]
        lines = [n for n in children(root, 'gr_line') if children(n, 'uuid')[0][1] in members]
        self.assertEqual([[nanometres(children(n, k)[0][1]), nanometres(children(n, k)[0][2])]
                          for n in lines for k in ('start', 'end')], [moved('101', '118'), moved('106', '118')])
        texts = [n for n in children(root, 'gr_text') if children(n, 'uuid')[0][1] in members]
        self.assertEqual([(n[1], at(n)) for n in texts], [('REG', (moved('110', '118'), 90.0))])

    def test_identical_inputs_save_identical_bytes(self):
        record, out, _ = shared('three', GROUPS)
        again, out2, _ = run(GROUPS)
        self.assertEqual(again['output']['sha256'], record['output']['sha256'])
        self.assertEqual(sha(out2), sha(out))
        self.assertEqual(record['top_board']['sha256'], sha(TOP))


class SublayoutProvenanceTests(unittest.TestCase):
    """AGENTEE-399: the DRC delta reads the top project's rules and grades through their provenance.

    The regulator's copper includes 0.25 mm tracks. Each case puts a 1 mm track-width
    minimum beside a copy of the owned top board, once per authority, and judges reg-a.
    """
    LIMIT = 1.0

    def setUp(self):
        require_toolchain(native=True)

    def project(self, files):
        tmp = Path(tempfile.mkdtemp(prefix='agent-ee-sublayout-project-'))
        _TEMPORARY.append(tmp)
        board = tmp / 'top.kicad_pcb'
        shutil.copyfile(TOP, board)
        for suffix, data in files.items():
            board.with_suffix(suffix).write_bytes(data)
        return board

    def reg_a(self, board):
        record, out, _ = run(GROUPS[:1], board=board)
        self.assertEqual(record['execution'], 'ok', record['findings'])
        self.assertTrue(out.is_file())
        return record, record['groups'][0]

    def widths(self, group):
        found = [v for v in group['drc']['new_violations'] if v['type'] == 'track_width']
        self.assertTrue(found, group['drc'])
        return found

    @staticmethod
    def minimum_project(spec=None):
        from agent_ee import design_rule_provenance as drp
        project = {'board': {'design_settings': {'rules': {'min_track_width': SublayoutProvenanceTests.LIMIT}}}}
        if spec is not None:
            raw = json.dumps({'schema': drp.SCHEMA, 'board_setup_rules': {'min_track_width': dict(
                spec, value_mm=SublayoutProvenanceTests.LIMIT)}}).encode()
            project, record = drp.import_constraints(project, raw, 'constraints/ee399.json',
                                                     hashlib.sha256(raw).hexdigest())
            drp.bind_project(project, record)
        return json.dumps(project).encode()

    def test_project_custom_rule_reaches_drc_and_grades_as_owner(self):
        rules = b"(version 1)\n(rule ee399_wide\n\t(constraint track_width (min 1mm)))\n"
        board = self.project({'.kicad_dru': rules})
        record, group = self.reg_a(board)
        for finding in self.widths(group):
            provenance = finding['constraint_provenance']
            self.assertEqual((provenance['class'], provenance['enforcement']), ('owner', 'hard'), provenance)
            self.assertEqual(provenance['citation']['rule'], 'ee399_wide')
            self.assertEqual(provenance['citation']['sha256'], hashlib.sha256(rules).hexdigest())
        self.assertNotIn('advisory_findings', group['drc'])
        self.assertEqual((group['outcome'], record['outcome']), ('defect', 'defect'))

    def test_sourced_board_minimum_grades_as_its_class(self):
        board = self.project({'.kicad_pro': self.minimum_project(
            {'class': 'manufacturer', 'basis': 'ee399 fabricator minimum'})})
        record, group = self.reg_a(board)
        for finding in self.widths(group):
            provenance = finding['constraint_provenance']
            self.assertEqual((provenance['class'], provenance['enforcement']), ('manufacturer', 'hard'), provenance)
            self.assertEqual(provenance['citation']['path'], 'constraints/ee399.json')
        self.assertEqual((group['outcome'], record['outcome']), ('defect', 'defect'))

    def test_unsourced_numeric_minimum_is_advisory_and_never_gates(self):
        board = self.project({'.kicad_pro': self.minimum_project()})
        record, group = self.reg_a(board)
        widths = self.widths(group)
        for finding in widths:
            provenance = finding['constraint_provenance']
            self.assertEqual((provenance['class'], provenance['citation'], provenance['enforcement']),
                             ('default', None, 'advisory'), provenance)
        self.assertEqual(group['drc']['advisory_findings'], [v for v in group['drc']['new_violations']
                                                              if v['type'] == 'track_width'])
        self.assertEqual(group['drc']['advisory_findings'], group['drc']['new_violations'], 'nothing else is new')
        self.assertEqual((group['status'], group['outcome'], record['outcome']), ('populated', 'pass', 'pass'))

    def test_unreadable_provenance_binding_is_unknown_not_pass(self):
        project = json.loads(self.minimum_project())
        project['agent_ee_constraint_provenance'] = 'not an object'
        board = self.project({'.kicad_pro': json.dumps(project).encode()})
        record, group = self.reg_a(board)
        self.assertEqual(record['findings'][0]['code'], 'drc_unavailable', record['findings'])
        self.assertEqual((group['drc'], group['outcome'], record['outcome']), ('not_evaluated', 'unknown', 'unknown'))


class SublayoutPlanTests(unittest.TestCase):
    """Planning boundaries over native facts of the fixtures."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        tmp = Path(tempfile.mkdtemp(prefix='agent-ee-sublayout-facts-'))
        _TEMPORARY.append(tmp)
        cls.top = sublayout.board_facts(TOP, tmp / 'top')
        cls.sub = sublayout.board_facts(SUB, tmp / 'sub')

    def plan(self, group=None, top=None, sub=None):
        group = group or GROUPS[0]
        return sublayout.plan_group(group, top or self.top, sub or self.sub, sha(SUB))

    def test_contested_vote_withholds_copper_instead_of_shorting(self):
        top = copy.deepcopy(self.top)
        u1 = next(fp for fp in top['footprints'] if fp['reference'] == 'U1')
        next(p for p in u1['pads'] if p['number'] == '1')['net'] = '/A/OTHER'
        plan = self.plan(top=top)
        vin = next(e for e in plan['net_map'] if e['sub_net'] == '/VIN')
        self.assertEqual((vin['status'], vin['votes']), ('contested', {'/A/OTHER': 1, '/A/VIN': 1}))
        self.assertEqual({w['reason'] for w in plan['withheld']}, {'contested'})
        self.assertFalse([c for c in plan['copy'] if c['expected']['net'] in ('/A/VIN', '/A/OTHER')])

    def test_abstaining_pads_do_not_veto_a_vote(self):
        top = copy.deepcopy(self.top)
        j1 = next(fp for fp in top['footprints'] if fp['reference'] == 'J1')
        next(p for p in j1['pads'] if p['number'] == '1')['net'] = ''
        vin = next(e for e in self.plan(top=top)['net_map'] if e['sub_net'] == '/VIN')
        self.assertEqual((vin['status'], vin['top_net'], vin['abstentions']), ('mapped', '/A/VIN', 1))

    def test_refusals(self):
        cases = {
            'member_coverage': dict(GROUPS[0], members={'J1': 'J1', 'U1': 'U1'}),
            'footprint_mismatch': dict(GROUPS[0], members={'J1': 'J1', 'U1': 'U1', 'C1': 'J2'}),
            'top_reference': dict(GROUPS[0], members={'J1': 'J1', 'U1': 'U1', 'C1': 'C7'}),
        }
        for code, group in cases.items():
            plan = self.plan(group)
            self.assertEqual((plan['status'], plan['findings'][0]['code']), ('refused', code), code)
        locked = copy.deepcopy(self.top)
        next(fp for fp in locked['footprints'] if fp['reference'] == 'C1')['locked'] = True
        self.assertEqual(self.plan(top=locked)['findings'][0]['code'], 'not_new')
        odd = copy.deepcopy(self.sub)
        next(fp for fp in odd['footprints'] if fp['reference'] == 'C1')['orientation_deg'] = 45.0
        self.assertEqual(self.plan(sub=odd)['findings'][0]['code'], 'orientation_unqualified')
        alien = copy.deepcopy(self.sub)
        alien['items'].append(dict(uuid='x', kind='PCB_DIMENSION_ALIGNED', net='', supported=False, layers=[]))
        self.assertEqual(self.plan(sub=alien)['findings'][0]['code'], 'unsupported_item')
        bare = dict(copy.deepcopy(self.top), edge_items=0)
        self.assertEqual(self.plan(top=bare)['findings'][0]['code'], 'no_outline')

    def test_plan_is_deterministic_and_uuids_are_new(self):
        first, second = self.plan(), self.plan()
        self.assertEqual(first, second)
        sources = {i['uuid'] for i in self.sub['items']}
        self.assertFalse({c['uuid'] for c in first['copy']} & sources)
        other = self.plan(dict(GROUPS[0], id='reg-z'))
        self.assertFalse({c['uuid'] for c in first['copy']} & {c['uuid'] for c in other['copy']})
        self.assertEqual(len(first['copy']), 14, 'tracks, vias and the zone; never the sub-board outline')


class SublayoutRequestTests(unittest.TestCase):
    def test_request_boundaries(self):
        sublayout.read_request(request(copy.deepcopy(GROUPS)))
        bad = [
            dict(GROUPS[0], sub_board='/abs/regulator.kicad_pcb'),
            dict(GROUPS[0], sub_board='../regulator.kicad_pcb'),
            dict(GROUPS[0], sub_board='regulator.kicad_sch'),
            dict(GROUPS[0], rotation_deg=45),
            dict(GROUPS[0], rotation_deg=90.0),
            dict(GROUPS[0], anchor_at_nm=[1.5, 2]),
            dict(GROUPS[0], sub_board_sha256='ABC'),
            dict(GROUPS[0], extra=1),
            dict(GROUPS[0], id='bad id'),
        ]
        for group in bad:
            with self.assertRaises(sublayout.SublayoutError, msg=group):
                sublayout.read_request(request([group]))
        with self.assertRaises(sublayout.SublayoutError):
            sublayout.read_request(request([GROUPS[0], dict(GROUPS[1], members={'J1': 'J1'})]))
        with self.assertRaises(sublayout.SublayoutError):
            sublayout.read_request(request([GROUPS[0], GROUPS[0]]))
        with self.assertRaises(sublayout.SublayoutError):
            sublayout.read_request({'schema': 'agent-ee.sublayout-request/0', 'groups': GROUPS})

    def test_invalid_request_is_a_failed_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = sublayout.reuse(TOP, {'schema': 'nope'}, tmp, Path(tmp) / 'out.kicad_pcb', Path(tmp) / 'work')
        self.assertEqual((record['execution'], record['outcome']), ('failed', 'unknown'))
        self.assertEqual(record['findings'][0]['code'], 'request')

    def test_drc_delta_keys_on_items_not_positions(self):
        before = {'violations': [{'type': 'lib_footprint_issues', 'items': [{'uuid': 'u1', 'pos': {'x': 300, 'y': 1}}]}]}
        after = {'violations': [
            {'type': 'lib_footprint_issues', 'items': [{'uuid': 'u1', 'pos': {'x': 120, 'y': 110}}]},
            {'type': 'clearance', 'items': [{'uuid': 'other', 'pos': {'x': 121, 'y': 111}}]},
            {'type': 'clearance', 'items': [{'uuid': 'far', 'pos': {'x': 10, 'y': 10}}]},
            {'type': 'shorting_items', 'items': [{'uuid': 'mine', 'pos': {'x': 10, 'y': 10}}]}]}
        new = sublayout.drc_delta(before, after, [100 * MM, 100 * MM, 130 * MM, 130 * MM], {'mine'})
        self.assertEqual([(v['type'], v['items'][0]['uuid']) for v in new],
                         [('clearance', 'other'), ('shorting_items', 'mine')])


if __name__ == '__main__':
    unittest.main()
