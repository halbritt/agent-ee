"""Silkscreen after an automatic placement (AGENTEE-644).

The label step (``silk_labels.settle`` through ``placement_job.place_labels``) and the
silk content capture reserves where a board's own rules make silkscreen overlaps errors.
Every board is a small synthetic board built through pcbnew, and every verdict is the
job's own native DRC. No benchmark or third-party board is used.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee import placement_job as job
from agent_ee import silk_labels
from tests.test_placement_job import MM, Fixture, _box, _v, groups, intent
from tests.test_placement_job_clearance import evaluate
from tests.toolchain import require_toolchain

SILK = ('silk_overlap', 'silk_over_copper', 'silk_edge_clearance')
ERRORS = {'board': {'design_settings': {'rule_severities': {'silk_overlap': 'error', 'silk_over_copper': 'error'}}}}
# The half sizes of the test part's courtyard, and its label's offset above the part.
HALF = (3_200_000, 1_200_000)
ABOVE = (0, -2_200_000)


def part(k, board, ref, x, y, *, label=ABOVE, locked=False, half=HALF, back=False):
    """A two-pad part with a silk outline around its pads, a courtyard and a silkscreen reference.

    ``label`` is the offset of the reference from the part's centre, as a placement leaves it."""
    fp = k.FOOTPRINT(board)
    fp.SetReference(ref)
    fp.SetValue('value')
    board.Add(fp)
    hx, hy = half
    for number, px in (('1', -(hx - 1_200_000)), ('2', hx - 1_200_000)):
        pad = k.PAD(fp)
        pad.SetNumber(number)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PADSTACK.ALL_LAYERS, k.PAD_SHAPE_RECT)
        pad.SetSize(k.PADSTACK.ALL_LAYERS, _v(k, MM, 1_200_000))
        pad.SetLayerSet(pad.SMDMask())
        fp.Add(pad)
        pad.SetFPRelativePosition(_v(k, px, 0))
    for layer, inset in ((k.F_SilkS, 300_000), (k.F_CrtYd, 0)):
        for shape in _box(k, fp, layer, -hx + inset, -hy + inset, hx - inset, hy - inset):
            fp.Add(shape)
    fp.SetPosition(_v(k, x, y))
    reference = fp.Reference()
    reference.SetLayer(k.F_SilkS)
    reference.SetVisible(True)
    reference.SetTextSize(_v(k, MM, MM))
    reference.SetTextThickness(150_000)
    reference.SetPosition(_v(k, x + label[0], y + label[1]))
    fp.Value().SetVisible(False)
    if back:
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetLocked(locked)
    return fp


def silk_text(k, parent, text, x, y, layer=None):
    item = k.PCB_TEXT(parent)
    item.SetText(text)
    item.SetLayer(k.F_SilkS if layer is None else layer)
    item.SetTextSize(_v(k, MM, MM))
    item.SetTextThickness(150_000)
    item.SetPosition(_v(k, x, y))
    parent.Add(item)
    return item


def findings(report, uuid=None, kinds=SILK, severity=None):
    return [v for v in report['violations'] if v['type'] in kinds
            and (severity is None or v['severity'] == severity)
            and (uuid is None or any(i.get('uuid') == uuid for i in v['items']))]


class Boards(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.dir = Path(tempfile.mkdtemp(prefix='ee644-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.board = self.dir / 'board.kicad_pcb'
        self.project = b'{}\n'

    def build(self, make, *, size=(40, 30), project=None):
        """Save a board ``size`` mm with what ``make(k, board)`` adds, and its project file."""
        import pcbnew as k
        board = k.BOARD()
        for edge in _box(k, board, k.Edge_Cuts, 0, 0, size[0] * MM, size[1] * MM):
            board.Add(edge)
        make(k, board)
        self.assertTrue(k.SaveBoard(str(self.board), board))
        if project is not None:
            self.project = json.dumps(project).encode()
        self.board.with_suffix('.kicad_pro').write_bytes(self.project)
        self.board.with_suffix('.kicad_prl').unlink(missing_ok=True)

    def labels(self):
        """reference -> the facts of its reference label on the saved board."""
        import pcbnew as k
        rows = {}
        for fp in k.LoadBoard(str(self.board)).GetFootprints():
            field = fp.Reference()
            ink = field.GetEffectiveShape(field.GetLayer()).BBox()
            court = fp.GetCourtyard(k.B_CrtYd if fp.IsFlipped() else k.F_CrtYd).BBox()
            rows[fp.GetReference()] = {
                'uuid': field.m_Uuid.AsString(), 'footprint': fp.m_Uuid.AsString(),
                'at': (field.GetPosition().x, field.GetPosition().y), 'angle': field.GetTextAngle().AsDegrees(),
                'size': (field.GetTextSize().x, field.GetTextSize().y), 'thickness': field.GetTextThickness(),
                'visible': field.IsVisible(), 'layer': field.GetLayer(), 'text': field.GetText(),
                'mirrored': field.IsMirrored(), 'justify': (field.GetHorizJustify(), field.GetVertJustify()),
                'ink': [ink.GetLeft(), ink.GetTop(), ink.GetRight(), ink.GetBottom()],
                'court': [court.GetLeft(), court.GetTop(), court.GetRight(), court.GetBottom()]}
        return rows

    def step(self, moved):
        """The job's native DRC, then the label step for the ``moved`` references."""
        before = self.labels()
        report, execution = job.drc(self.board, self.dir, refill=True, label='native-drc')
        check = {'board': str(self.board), 'board_sha256': job.digest(self.board),
                 'moved': [before[ref]['footprint'] for ref in moved]}
        after, ran, record = job.place_labels(check, report, execution, self.project, None, clock=job.Clock(300))
        return {'before': before, 'after': self.labels(), 'first': report, 'report': after, 'record': record,
                'check': check, 'execution': (execution, ran)}

    @staticmethod
    def gap(row):
        """How far the label's ink is from its part's courtyard box (0 when they overlap)."""
        ink, court = row['ink'], row['court']
        dx = max(court[0] - ink[2], ink[0] - court[2], 0)
        dy = max(court[1] - ink[3], ink[1] - court[3], 0)
        return max(dx, dy)

    @staticmethod
    def overlap(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class LabelStepTests(Boards):
    def neighbours(self, k, board):
        """R102 sits directly above R101, where R101's label is: the label lies on R102's pads and outline."""
        part(k, board, 'R101', 20 * MM, 15 * MM)
        part(k, board, 'R102', 20 * MM, 12_200_000)

    def test_label_on_a_neighbour_moves_to_a_free_place_beside_its_own_part(self):
        self.build(self.neighbours)
        done = self.step(['R101', 'R102'])
        before, after, record = done['before'], done['after'], done['record']
        named = before['R101']['uuid']
        self.assertEqual({v['type'] for v in findings(done['first'], named)}, {'silk_overlap', 'silk_over_copper'})
        self.assertEqual(findings(done['report'], named), [])
        self.assertEqual(findings(done['report']), [], 'the judged board has no silkscreen finding left')
        self.assertEqual((record['status'], record['named'], record['rounds'], record['unplaced']),
                         ('placed', 1, 1, []))
        by_type = {}
        for finding in findings(done['first'], named):
            by_type[finding['type']] = by_type.get(finding['type'], 0) + 1
        self.assertEqual(record['findings'], {'before': by_type, 'after': {}})
        moved, = record['moved']
        self.assertEqual((moved['reference'], moved['field'], moved['uuid']), ('R101', 'Reference', named))
        self.assertIn(moved['place'], ('above', 'below', 'left', 'right'))
        self.assertEqual((moved['to']['x_nm'], moved['to']['y_nm'], moved['to']['angle_deg']),
                         (*after['R101']['at'], after['R101']['angle']))
        self.assertNotEqual(after['R101']['at'], before['R101']['at'])
        # Beside its own part, readable, and under no other part.
        self.assertIn(after['R101']['angle'], (0.0, 90.0))
        height = min(after['R101']['ink'][2] - after['R101']['ink'][0], after['R101']['ink'][3] - after['R101']['ink'][1])
        self.assertLessEqual(self.gap(after['R101']), height)
        self.assertFalse(self.overlap(after['R101']['ink'], after['R102']['court']))
        # Nothing about the label but its place changed, and the clean label of R102 did not move.
        for key in ('size', 'thickness', 'visible', 'layer', 'text', 'mirrored', 'justify'):
            self.assertEqual(after['R101'][key], before['R101'][key], key)
        self.assertEqual(after['R102'], before['R102'])
        # The candidate the job goes on with is the board with its labels placed.
        self.assertEqual(done['check']['board_sha256'], job.digest(self.board))
        self.assertIsNot(done['execution'][1], done['execution'][0])
        self.assertEqual(json.loads((self.dir / 'native-drc.json').read_text()), done['report'])
        self.assertEqual(json.loads((self.dir / 'native-drc-before-labels.json').read_text()), done['first'])
        self.assertEqual(self.board.with_suffix('.kicad_pro').read_bytes(), self.project)

    def test_only_the_label_changes_in_the_saved_board(self):
        from agent_ee.symbol_library import parse_sexpr
        self.build(self.neighbours)
        import pcbnew as k
        self.assertTrue(k.SaveBoard(str(self.board), k.LoadBoard(str(self.board))))    # as a job's candidate is saved
        self.board.with_suffix('.kicad_pro').write_bytes(self.project)
        self.board.with_suffix('.kicad_prl').unlink(missing_ok=True)
        saved = self.board.read_bytes()
        done = self.step(['R101', 'R102'])
        moved = done['before']['R101']['uuid']

        def without_moved_place(data):
            tree = parse_sexpr(data)
            for node in tree:
                if isinstance(node, list) and node[:1] == ['footprint']:
                    for child in node:
                        if (isinstance(child, list) and child[:1] == ['property']
                                and any(c == ['uuid', moved] for c in child if isinstance(c, list))):
                            child[:] = [c for c in child if not (isinstance(c, list) and c[:1] == ['at'])]
            return tree
        self.assertNotEqual(self.board.read_bytes(), saved)
        self.assertEqual(without_moved_place(self.board.read_bytes()), without_moved_place(saved))

    def test_a_board_without_findings_is_not_touched(self):
        self.build(lambda k, board: part(k, board, 'R101', 20 * MM, 15 * MM))
        saved = self.board.read_bytes()
        done = self.step(['R101'])
        self.assertEqual(findings(done['first']), [])
        self.assertEqual(done['record']['status'], 'not_needed')
        self.assertEqual((done['record']['named'], done['record']['moved'], done['record']['rounds']), (0, [], 0))
        self.assertEqual(self.board.read_bytes(), saved)
        self.assertIs(done['execution'][1], done['execution'][0], 'no second DRC run')
        self.assertIs(done['report'], done['first'])
        self.assertFalse((self.dir / 'native-drc-before-labels.json').exists())

    def test_labels_of_footprints_the_job_did_not_move_stay(self):
        self.build(self.neighbours)
        saved = self.board.read_bytes()
        done = self.step(['R102'])                    # R101's label is in the findings, but R101 was not moved
        self.assertTrue(findings(done['first'], done['before']['R101']['uuid']))
        self.assertEqual((done['record']['status'], done['record']['named']), ('not_needed', 0))
        self.assertEqual(self.board.read_bytes(), saved)
        self.assertEqual(done['after'], done['before'])

    def test_of_two_labels_on_each_other_only_one_moves(self):
        def make(k, board):
            part(k, board, 'R101', 14 * MM, 15 * MM)
            part(k, board, 'R102', 22 * MM, 15 * MM, label=(-5_600_000, -2_200_000))     # onto R101's label
        self.build(make)
        done = self.step(['R101', 'R102'])
        before, after = done['before'], done['after']
        both = findings(done['first'])
        self.assertEqual(len(both), 1)
        self.assertEqual({i['uuid'] for i in both[0]['items']}, {before['R101']['uuid'], before['R102']['uuid']})
        self.assertEqual(findings(done['report']), [])
        self.assertEqual([row['reference'] for row in done['record']['moved']], ['R101'])
        self.assertEqual(done['record']['named'], 2)
        self.assertEqual(after['R102'], before['R102'])
        # The moved label keeps half its height from the label it left: they do not read as one.
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        one, two = (board.FindFootprintByReference(ref).Reference() for ref in ('R101', 'R102'))
        ink = after['R101']['ink']
        height = min(ink[2] - ink[0], ink[3] - ink[1])
        self.assertFalse(one.GetEffectiveShape(k.F_SilkS).Collide(two.GetEffectiveShape(k.F_SilkS), height // 2 - 1))

    def test_label_without_a_free_place_is_reported_by_name_and_stays_as_it_is(self):
        # The board is the part's courtyard plus 0.3 mm: nothing fits beside the part, and its
        # own pads and outline leave no room inside. The label starts on the part's own pads.
        def make(k, board):
            part(k, board, 'R101', 3_500_000, 1_500_000, label=(0, 0))
        self.build(make, size=(7, 3))
        saved = self.board.read_bytes()
        done = self.step(['R101'])
        named = done['before']['R101']['uuid']
        self.assertTrue(findings(done['first'], named))
        record = done['record']
        self.assertEqual((record['status'], record['moved'], record['rounds']), ('incomplete', [], 0))
        row, = record['unplaced']
        self.assertEqual((row['reference'], row['field'], row['uuid']), ('R101', 'Reference', named))
        self.assertEqual(row['findings'], len(findings(done['first'], named)))
        self.assertIn('no free place', row['reason'])
        self.assertEqual(self.board.read_bytes(), saved, 'the label is not hidden, resized or moved')
        self.assertTrue(done['after']['R101']['visible'])
        self.assertEqual(record['findings']['after'], record['findings']['before'])

    def test_large_part_takes_its_label_inside_its_own_outline_when_no_side_is_free(self):
        half = (8 * MM, 4 * MM)

        def make(k, board):
            part(k, board, 'R101', 8_300_000, 4_300_000, label=(-6_800_000, 0), half=half)     # on its own pad 1
        self.build(make, size=(16.6, 8.6))
        done = self.step(['R101'])
        after = done['after']['R101']
        self.assertEqual(findings(done['report']), [])
        self.assertEqual([row['place'] for row in done['record']['moved']], ['inside'])
        self.assertTrue(after['court'][0] < after['ink'][0] and after['ink'][2] < after['court'][2]
                        and after['court'][1] < after['ink'][1] and after['ink'][3] < after['court'][3])

    def test_back_side_label_is_placed_on_the_back_and_stays_mirrored(self):
        def make(k, board):
            part(k, board, 'R101', 20 * MM, 15 * MM, back=True)
            part(k, board, 'R102', 20 * MM, 12_600_000, back=True)
        self.build(make)
        import pcbnew as k
        done = self.step(['R101', 'R102'])
        before, after = done['before']['R101'], done['after']['R101']
        self.assertEqual((before['layer'], before['mirrored']), (k.B_SilkS, True))
        self.assertTrue(findings(done['first'], before['uuid']))
        self.assertEqual(findings(done['report']), [])
        self.assertEqual([row['reference'] for row in done['record']['moved']], ['R101'])
        self.assertEqual((after['layer'], after['mirrored'], after['size']), (k.B_SilkS, True, before['size']))
        self.assertIn(after['angle'], (0.0, 90.0))
        self.assertFalse(self.overlap(after['ink'], done['after']['R102']['court']))

    def test_error_severity_findings_are_cleared_the_same_way(self):
        self.build(self.neighbours, project=ERRORS)
        done = self.step(['R101', 'R102'])
        self.assertEqual({v['type'] for v in findings(done['first'], severity='error')},
                         {'silk_overlap', 'silk_over_copper'})
        self.assertEqual(findings(done['report']), [])
        self.assertEqual(done['record']['status'], 'placed')

    def test_deadline_stops_the_step_before_it_changes_the_board(self):
        self.build(self.neighbours)
        saved = self.board.read_bytes()
        report, execution = job.drc(self.board, self.dir, refill=True, label='native-drc')
        check = {'board': str(self.board), 'board_sha256': job.digest(self.board),
                 'moved': [row['footprint'] for row in self.labels().values()]}
        with self.assertRaises(job.JobStopped):
            job.place_labels(check, report, execution, self.project, None, clock=job.Clock(0))
        self.assertEqual(self.board.read_bytes(), saved)


class ReserveTests(Boards):
    def test_only_an_error_severity_in_the_boards_own_project_reserves(self):
        import pcbnew as k
        self.build(lambda k, board: part(k, board, 'R101', 20 * MM, 15 * MM))
        cases = [({}, None), ({'board': {}}, None), ({'board': {'design_settings': {'rule_severities': {}}}}, None)]
        for a in ('error', 'warning', 'ignore'):
            for b in ('error', 'warning', 'ignore'):
                project = {'board': {'design_settings': {'rule_severities': {'silk_overlap': a, 'silk_over_copper': b}}}}
                cases.append((project, 'error' in (a, b)))
        cases.append(({'board': {'design_settings': {'rule_severities': {'silk_edge_clearance': 'error'}}}}, None))
        for project, reserves in cases:
            with self.subTest(project=project):
                self.board.with_suffix('.kicad_pro').write_text(json.dumps(project))
                grow = silk_labels.reserve(k.LoadBoard(str(self.board)))
                self.assertEqual(grow is not None, bool(reserves))
                if reserves:
                    settings = k.LoadBoard(str(self.board)).GetDesignSettings()
                    half = (settings.m_SilkClearance + 1) // 2
                    self.assertEqual(grow, (half + settings.m_MaxError, half))
        self.board.with_suffix('.kicad_pro').write_text('not json')
        self.assertIsNone(silk_labels.reserve(k.LoadBoard(str(self.board))))
        self.board.with_suffix('.kicad_pro').unlink()
        self.assertIsNone(silk_labels.reserve(k.LoadBoard(str(self.board))))
        self.assertIsNone(silk_labels.reserve(k.BOARD()))


class CaptureTests(Fixture):
    """The fixture board of test_placement_job with silk content added: U1 gets a "K" mark outside
    its outline, the board a silkscreen text, the locked hole H1 and the movable R1 visible labels."""

    def setUp(self):
        super().setUp()
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        for ref, offset in (('H1', (0, -2 * MM)), ('R1', (0, -1_500_000))):
            fp = board.FindFootprintByReference(ref)
            field = fp.Reference()
            field.SetLayer(k.F_SilkS)
            field.SetVisible(True)
            field.SetTextSize(_v(k, MM, MM))
            field.SetTextThickness(150_000)
            field.SetPosition(_v(k, fp.GetPosition().x + offset[0], fp.GetPosition().y + offset[1]))
        u1 = board.FindFootprintByReference('U1')
        self.mark = silk_text(k, u1, 'K', u1.GetPosition().x + 3 * MM, u1.GetPosition().y).m_Uuid.AsString()
        self.title = silk_text(k, board, 'TITLE', 4 * MM, 10 * MM).m_Uuid.AsString()
        silk_text(k, board, 'CU', 15 * MM, 4 * MM, layer=k.F_Cu)
        silk_text(k, board, ' ', 15 * MM, 2 * MM)
        self.h1_label = board.FindFootprintByReference('H1').Reference().m_Uuid.AsString()
        self.r1_label = board.FindFootprintByReference('R1').Reference().m_Uuid.AsString()
        self.assertTrue(k.SaveBoard(str(self.board), board))

    def capture(self, project, declared=None, grouped=None):
        self.project.write_text(json.dumps(project))
        problem, record = job.capture(self.board, job.normalize_intent(
            intent() if declared is None else declared, groups() if grouped is None else grouped))
        return problem, {row['native_ref']: row for row in problem['footprints']}, \
            {row['native_ref']: row for row in record['footprints']}

    def test_capture_is_the_same_where_silk_findings_are_not_errors(self):
        plain, rows, records = self.capture({})
        self.assertFalse([ref for ref in rows if ref.startswith('board-text:')])
        self.assertFalse([ref for ref, row in records.items() if 'silk-content' in row['occupancy'].get('sources', [])])
        for severity in ('warning', 'ignore'):
            project = {'board': {'design_settings': {'rule_severities': {'silk_overlap': severity,
                                                                         'silk_over_copper': severity}}}}
            self.assertEqual(self.capture(project)[0], plain, severity)

    def test_silk_content_joins_the_occupancy_where_silk_findings_are_errors(self):
        _, plain, _ = self.capture({})
        problem, rows, records = self.capture(ERRORS)
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        grow, _ = silk_labels.reserve(board)
        u1 = board.FindFootprintByReference('U1')
        mark = next(g for g in u1.GraphicalItems() if g.m_Uuid.AsString() == self.mark)
        ink = mark.GetEffectiveShape(mark.GetLayer()).BBox()
        at = u1.GetPosition()
        # U1 is unrotated, so its local frame is the board frame less its position.
        was, now = plain['U1']['occupancy']['local_rect_nm'], rows['U1']['occupancy']['local_rect_nm']
        self.assertGreater(ink.GetRight() - at.x, was[2], 'the mark is outside the plain occupancy')
        self.assertEqual(now[2], ink.GetRight() - at.x + grow)
        self.assertTrue(now[0] <= was[0] and now[1] <= was[1] and now[3] >= was[3])
        self.assertIn('silk-content', records['U1']['occupancy']['sources'])
        self.assertEqual(rows['U1']['occupancy']['label'], 'derived_occupancy')
        # A part whose silk and pad openings its occupancy already holds is captured as before.
        for ref in ('R1', 'C1', 'FID', ''):
            self.assertEqual(rows[ref], plain[ref], ref)
        self.assertNotIn('silk-content', records['R1']['occupancy'].get('sources', []))
        # Seats keep the part's own outline: the reservation is packing room, not the body.
        self.assertEqual({k: v for k, v in rows['U1'].items() if k != 'occupancy'},
                         {k: v for k, v in plain['U1'].items() if k != 'occupancy'})

    def test_board_silk_text_and_labels_of_fixed_footprints_are_obstacles(self):
        problem, rows, _ = self.capture(ERRORS)
        obstacles = {ref.split(':', 1)[1]: row for ref, row in rows.items() if ref.startswith('board-text:')}
        # The board's silkscreen text and the label of the locked H1; not the movable R1's label,
        # a text on copper or a text that prints nothing.
        self.assertEqual(set(obstacles), {self.title, self.h1_label})
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        grow, _ = silk_labels.reserve(board)
        title = next(d for d in board.GetDrawings() if d.m_Uuid.AsString() == self.title)
        ink = title.GetEffectiveShape(title.GetLayer()).BBox()
        row = obstacles[self.title]
        self.assertEqual((row['role'], row['rotations'], row['pads'], row['copper_local_nm']), ('fixed_body', [0], [], []))
        x, y = row['pose']['x_nm'], row['pose']['y_nm']
        rect = row['occupancy']['local_rect_nm']
        self.assertEqual([rect[0] + x, rect[1] + y, rect[2] + x, rect[3] + y],
                         [ink.GetLeft() - grow, ink.GetTop() - grow, ink.GetRight() + grow, ink.GetBottom() + grow])
        # The engine keeps a part off the text: on it is a hard violation, beside it is not.
        r1 = rows['R1']['uuid']
        on, off = evaluate(problem, [{r1: [4 * MM, 10 * MM, 0]}, {r1: [10 * MM, 3 * MM, 0]}])
        self.assertTrue([v for v in on['violations'] if v['kind'] == 'pair'], on['violations'])
        self.assertFalse([v for v in off['violations'] if v['kind'] == 'pair'
                          and 'board-text:' + self.title in json.dumps(v)], off['violations'])

    def test_declared_fixed_pose_makes_a_label_fixed_text_too(self):
        declared = intent()
        declared['fixed_poses'].append({'ref': 'R1', 'x': 10.0, 'y': 6.0, 'rot': 0.0, 'side': 'F', 'basis': 'mechanical'})
        _, rows, _ = self.capture(ERRORS, declared=declared, grouped={'groups': []})
        self.assertEqual(rows['R1']['role'], 'fixed_body')
        self.assertIn('board-text:' + self.r1_label, rows)

    def test_a_board_where_nothing_can_move_reserves_nothing(self):
        import pcbnew as k
        board = k.LoadBoard(str(self.board))
        for fp in board.GetFootprints():
            fp.SetLocked(True)
        self.assertTrue(k.SaveBoard(str(self.board), board))
        problem, rows, _ = self.capture(ERRORS, grouped={'groups': []})
        self.assertFalse([ref for ref in rows if ref.startswith('board-text:')])
        self.assertEqual(problem, self.capture({}, grouped={'groups': []})[0])

    def test_search_and_native_save_keep_parts_off_reserved_silk(self):
        problem, rows, _ = self.capture(ERRORS)
        normalized = job.normalize_intent(intent(), groups())
        out, _ = job.solve(problem, self.search(2, 20000), self.dir, deadline_s=60)
        self.assertTrue(out['shortlist'], out.get('diagnostic_shortlist'))
        candidate = out['shortlist'][0]
        self.assertEqual(candidate['terms']['hard_count'], 0)
        placed = job.materialize(self.board, candidate['poses'], self.dir / 'placed', self.project.read_bytes(),
                                 normalized=normalized)
        self.assertEqual(placed['integrity'], 'preserved', placed['problems'])
        report, execution = job.drc(placed['board'], self.dir / 'placed', refill=True, label='native-drc')
        # No part is on silk that stays where it is. A label that came with its part may be; the
        # label step moves it, and then nothing on the silkscreen is in a finding.
        fixed = {self.title, self.h1_label, self.mark}
        labels = {self.r1_label}
        self.assertEqual([v for v in findings(report)
                          if {i.get('uuid') for i in v['items']} & fixed and not {i.get('uuid') for i in v['items']} & labels], [])
        report, _, record = job.place_labels(placed, report, execution, self.project.read_bytes(), None, clock=job.Clock(300))
        self.assertEqual(findings(report), [], record)
        # A candidate that moves an obstacle is refused as before.
        moved = json.loads(json.dumps(candidate['poses']))
        next(row for row in moved if row['uuid'] == self.title)['x_nm'] += 1
        with self.assertRaisesRegex(job.JobRefusal, 'fixed board text or rule-area obstacle'):
            job.materialize(self.board, moved, self.dir / 'refused', self.project.read_bytes(), normalized=normalized)


if __name__ == '__main__':
    unittest.main()
