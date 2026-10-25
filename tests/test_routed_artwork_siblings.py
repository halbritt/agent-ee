"""REC-31: sibling identity alignment must preserve raw write-scope authority."""
import copy
import unittest

from agent_ee import routed_artwork as art
from agent_ee.symbol_library import children, parse_sexpr
from test_routed_artwork import FieldPoseFixture as F, authored_free_graphics


def raw_board(tree):
    def emit(node):
        return '(' + ' '.join(emit(c) if isinstance(c, list) else c for c in node) + ')'
    return emit(tree).encode()


def find(tree, uid):
    for node in tree:
        if isinstance(node, list):
            if children(node, 'uuid') == [['uuid', '"' + uid + '"']]:
                return node
            try:
                return find(node, uid)
            except LookupError:
                pass
    raise LookupError(uid)


def article(**changes):
    tree = parse_sexpr(F.board(**changes), raw=True)
    fp = find(tree, F.uid(10))
    for text in (
        f'(fp_circle (center 0 0) (end 1 0) (layer "B.SilkS") (uuid "{F.uid(51)}"))',
        f'(fp_text user "logo" (at 0 0) (layer "B.SilkS") (uuid "{F.uid(52)}"))',
        '(opaque_metadata (keep "raw spelling"))',
    ):
        fp.insert(-1, parse_sexpr(text.encode(), raw=True))
    tree.insert(-1, parse_sexpr(f'(gr_line (start 1 2) (end 3 2) (layer "F.SilkS") (uuid "{F.uid(61)}"))'.encode(), raw=True))
    tree.insert(-1, parse_sexpr(f'(gr_circle (center 4 4) (end 5 4) (layer "F.SilkS") (uuid "{F.uid(62)}"))'.encode(), raw=True))
    return raw_board(tree)


def reordered(data):
    tree = parse_sexpr(data, raw=True)
    for parent, kinds in [(tree, {'footprint', 'gr_line', 'gr_circle'}),
                          (find(tree, F.uid(10)), {'property', 'fp_line', 'fp_circle', 'fp_text'})]:
        positions = [i for i, c in enumerate(parent) if isinstance(c, list) and c[0] in kinds]
        values = [parent[i] for i in positions][::-1]
        for i, value in zip(positions, values):
            parent[i] = value
    return raw_board(tree)


class ArtworkSiblingOrderTests(unittest.TestCase):
    def test_known_siblings_reorder_without_widening_translation_targets(self):
        before = article()
        self.assertEqual(art.write_scope(before, reordered(before))['moved_identities'], [])
        after = reordered(article(u31='1 2.4 90', c128='1 1.85 0', line='(start 1 2) (end 2 2)'))
        proof = art.write_scope(before, after, artwork_policy=authored_free_graphics(before))
        self.assertEqual(proof['moved_identities'], sorted([F.uid(1), F.uid(4), F.uid(11)]))
        fields_only = reordered(article(u31='1 2.4 90', c128='1 1.85 0'))
        targets = [('U31', 'Reference'), ('C128', 'Reference')]
        self.assertEqual(art.write_scope(before, fields_only, targets=targets)['moved_identities'],
                         sorted([F.uid(1), F.uid(4)]))
        with self.assertRaisesRegex(ValueError, 'outside the artwork target'):
            art.write_scope(before, after, targets=targets)

    def test_forbidden_edits_still_refuse_when_siblings_reorder(self):
        before = article()
        allowed = reordered(article(u31='1 2.4 90'))
        for mutation in ('pad', 'track', 'footprint', 'uuid', 'kind', 'text', 'font', 'layer',
                         'angle', 'reshape', 'parent', 'added', 'deleted', 'unknown', 'unknown-slot', 'spelling'):
            tree = parse_sexpr(allowed, raw=True)
            fp = find(tree, F.uid(10))
            field = find(tree, F.uid(1))
            line = find(tree, F.uid(11))
            if mutation == 'pad':
                children(find(tree, F.uid(12)), 'size')[0][1] = '1.1'
            elif mutation == 'track':
                children(find(tree, F.uid(40)), 'end')[0][1] = '5.1'
            elif mutation == 'footprint':
                children(fp, 'at')[0][1] = '10.1'
            elif mutation == 'uuid':
                children(line, 'uuid')[0][1] = '"' + F.uid(99) + '"'
            elif mutation == 'kind':
                line[0] = 'fp_circle'
            elif mutation == 'text':
                field[2] = '"other"'
            elif mutation == 'font':
                children(children(children(field, 'effects')[0], 'font')[0], 'size')[0][1] = '1.1'
            elif mutation == 'layer':
                children(field, 'layer')[0][1] = '"B.Fab"'
            elif mutation == 'angle':
                children(field, 'at')[0][3] = '0'
            elif mutation == 'reshape':
                children(line, 'end')[0][2] = '1.1'
            elif mutation == 'parent':
                fp.remove(line)
                find(tree, F.uid(20)).append(line)
            elif mutation == 'added':
                added = copy.deepcopy(line)
                children(added, 'uuid')[0][1] = '"' + F.uid(99) + '"'
                fp.append(added)
            elif mutation == 'deleted':
                fp.remove(line)
            elif mutation == 'unknown':
                children(fp, 'opaque_metadata')[0][1][1] = '"changed"'
            elif mutation == 'unknown-slot':
                at = fp.index(children(fp, 'opaque_metadata')[0])
                other = fp.index(field)
                fp[at], fp[other] = fp[other], fp[at]
            elif mutation == 'spelling':
                find(tree, F.uid(3))[2] = 'd.pdf'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                art.write_scope(before, raw_board(tree), artwork_policy=authored_free_graphics(before))

    def test_cross_parent_swap_same_inventory_and_duplicates_refuse(self):
        before = article()
        tree = parse_sexpr(reordered(before), raw=True)
        a, b = find(tree, F.uid(10)), find(tree, F.uid(20))
        left, right = find(tree, F.uid(2)), find(tree, F.uid(4))
        i, j = a.index(left), b.index(right)
        a[i], b[j] = right, left
        with self.assertRaises(ValueError):
            art.write_scope(before, raw_board(tree), artwork_policy=authored_free_graphics(before))
        for duplicate in ('existing', 'two-uuid-children'):
            tree = parse_sexpr(reordered(before), raw=True)
            line = find(tree, F.uid(11))
            if duplicate == 'existing':
                children(line, 'uuid')[0][1] = '"' + F.uid(51) + '"'
            else:
                line.append(['uuid', '"' + F.uid(99) + '"'])
            data = raw_board(tree)
            with self.subTest(duplicate=duplicate):
                for source in (before, data):
                    with self.assertRaisesRegex(ValueError, 'ambiguous artwork identity'):
                        art.write_scope(source, data)

    def test_unknown_uuid_siblings_and_unknown_parents_are_not_aligned(self):
        for prefix in ('opaque', 'fp_line'):
            before = (f'(kicad_pcb (opaque_parent ({prefix} (uuid "a") (at 1 2)) '
                      f'({prefix} (uuid "b") (at 3 4))))').encode()
            tree = parse_sexpr(before, raw=True)
            tree[1][1:] = tree[1][1:][::-1]
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                art.write_scope(before, raw_board(tree), artwork_policy=authored_free_graphics(before))
        before = b'(kicad_pcb (opaque (uuid "a")) (opaque (uuid "b")))'
        with self.assertRaises(ValueError):
            art.write_scope(before, b'(kicad_pcb (opaque (uuid "b")) (opaque (uuid "a")))')

    def test_exact_field_pose_destinations_remain_bound_after_reordering(self):
        before = article()
        after = reordered(article(**F.MOVED))
        request = F.request(before)
        proof = art.write_scope(before, after, field_poses=request)
        self.assertEqual(proof['moved_identities'], sorted([F.uid(1), F.uid(4)]))
        for mutation in ('destination', 'parent', 'pad', 'style', 'unselected-field'):
            tree = parse_sexpr(after, raw=True)
            field = find(tree, F.uid(1))
            if mutation == 'destination':
                children(field, 'at')[0][1] = '2.426'
            elif mutation == 'parent':
                find(tree, F.uid(10)).remove(field)
                find(tree, F.uid(20)).append(field)
            elif mutation == 'pad':
                children(find(tree, F.uid(12)), 'at')[0][1] = '0.1'
            elif mutation == 'style':
                field[2] = '"wrong"'
            else:
                children(find(tree, F.uid(31)), 'at')[0][1] = '0.5'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                art.write_scope(before, raw_board(tree), field_poses=request)
