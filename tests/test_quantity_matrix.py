"""AGENTEE-348 lane regression gate: the closed-grammar value matrix across both consumers.

Every value case raised by the round 1-3 reviews is one row. Each row is checked end to end:

- Length rows are declared as a board minimum through the design-rule import, for the
  owner, manufacturer, datasheet and engineering classes, with the geometry disclosure.
  They are also declared as a locality bound through ``locality_provenance.classify``
  (datasheet and owner) and ``electrical_locality.grade``.
- Count rows qualify a bound native ``thermal_spoke_count`` rule through
  ``finding_sources`` and the geometry inventory.

Only interfaces that already existed at aacbe15d are used, so the same file shows the
round-3 failures there. Run ``python3 -m tests.test_quantity_matrix --report`` to print
the per-row outcome table.
"""
import json
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from agent_ee import electrical_locality, locality_provenance
from agent_ee.design_rule_provenance import (bind_project, finding_sources, geometry_defaults,
                                             import_constraints)
from agent_ee.storage import json_bytes, write_json
from owner_fixture import citation

SCHEMA = 'fome-mc.constraint-provenance/1'
H, A = 'hard', 'advisory'
# (row, reviewer case, quote, governed millimetres, expected, exact advisory reason or None)
LENGTHS = [
    ('L01', 'r3 table: exact control', '3 mm', 3, H, None),
    ('L02', 'r3 table: 3.0 mm', '3.0 mm', 3, H, None),
    ('L03', 'r3 table: +3 mm', '+3 mm', 3, H, None),
    ('L04', 'r3 table: 3e0 mm', '3e0 mm', 3, H, None),
    ('L05', 'r3 table: 30e-1 mm', '30e-1 mm', 3, H, None),
    ('L06', 'r2/r3: attached unit 3mm', '3mm', 3, H, None),
    ('L07', 'r2 H1/r3 table: exponent suffix 1e-3 mm', '1e-3 mm', 3, A,
     'cited quote does not state the declared value'),
    ('L08', 'r2 H1/r3 table: dropped sign -3 mm', '-3 mm', 3, A,
     'cited quote does not state the declared value'),
    ('L09', 'r2 H1/r3 table: unicode minus U+2212', '−3 mm', 3, A,
     'cited quote does not state the declared value'),
    ('L10', 'r3 table: plus-minus ±3 mm', '±3 mm', 3, A,
     'quote has a quantity outside the closed grammar: quote states a tolerance (±) and no explicit tolerance fact exists'),
    ('L11', 'r3 table: tolerance 3 ±0.1 mm', '3 ±0.1 mm', 3, A,
     'quote has a quantity outside the closed grammar: quote states a tolerance (±) and no explicit tolerance fact exists'),
    ('L12', 'r3 table: grouped 3,000 um', '3,000 um', 3, A,
     "quote has a quantity outside the closed grammar: ',' joined to a quantity at '3,000 um'"),
    ('L13', 'r3 table: grouped 3 000 um', '3 000 um', 3, A,
     "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at '3 000 um'"),
    ('L14', 'r3 table: unit cm 0.3 cm', '0.3 cm', 3, A,
     "quote has a quantity outside the closed grammar: unit term 'cm' outside the grammar at '0.3 cm'"),
    ('L15', 'r3 table: unit mils 3 mils', '3 mils', 3, A,
     "quote has a quantity outside the closed grammar: unit term 'mils' outside the grammar at '3 mils'"),
    ('L16', 'r3 table: continuation 3 mm2', '3 mm2', 3, A,
     "quote has a quantity outside the closed grammar: unit term 'mm2' outside the grammar at '3 mm2'"),
    ('L17', 'r3 table: range 3-5 mm', '3-5 mm', 3, A,
     "quote has a quantity outside the closed grammar: '-' joined to a quantity at '3-5 mm'"),
    ('L18', 'r3 H1: rate 3 mm/s', 'Captured feed speed or area: 3 mm/s', 3, A,
     'cited quote does not state the declared value'),
    ('L19', 'r3 H1: area 3 mm^2', 'Captured feed speed or area: 3 mm^2', 3, A,
     'cited quote does not state the declared value'),
    ('L20', 'r3 H1: spaced rate 3 mm / s', 'Captured feed speed or area: 3 mm / s', 3, A,
     'cited quote does not state the declared value'),
    ('L21', 'r3 H1: word rate 3 mm/second', 'Captured feed speed or area: 3 mm/second', 3, A,
     'cited quote does not state the declared value'),
    ('L22', 'r3 H1 (superscript): 3 mm²', '3 mm²', 3, A,
     'cited quote does not state the declared value'),
    ('L23', 'r1 substring: 13 mm', 'Captured length: 13 mm', 3, A,
     'cited quote does not state the declared value'),
    ('L24', 'r1 substring: 3.3 mm', 'Captured length: 3.3 mm', 3, A,
     'cited quote does not state the declared value'),
    ('L25', 'r2 suffix: x-3 mm', 'Captured length: x-3 mm', 3, A,
     "quote has a quantity outside the closed grammar: unit term after a designator at 'th: x-3 mm'"),
    ('L26', 'r2 range: 1-3 mm', 'Captured length: 1-3 mm', 3, A,
     "quote has a quantity outside the closed grammar: '-' joined to a quantity at 'ength: 1-3 mm'"),
    ('L27', 'r2 fraction: 1/3 mm', 'Captured length: 1/3 mm', 3, A,
     "quote has a quantity outside the closed grammar: operator without a unit term at 'ngth: 1/3 mm'"),
    ('L28', 'r2 grouping: 1’3 mm', 'Captured length: 1’3 mm', 3, A,
     "quote has a quantity outside the closed grammar: '’' joined to a quantity at 'ength: 1’3 mm'"),
    ('L29', "r2 grouping: 1'3 mm", "Captured length: 1'3 mm", 3, A,
     'quote has a quantity outside the closed grammar: "\'" joined to a quantity at "ength: 1\'3 mm"'),
    ('L30', 'r2 grouping: 1 3 mm', 'Captured length: 1 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at 'ngth: 1 3 mm'"),
    ('L31', 'r2 grouping: thin space 1 3 mm', 'Captured length: 1 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: juxtaposed numerals (grouping) at 'ngth: 1\\u20093 mm'"),
    ('L32', 'r2 grouping: 1,3 mm', 'Captured length: 1,3 mm', 3, A,
     "quote has a quantity outside the closed grammar: ',' joined to a quantity at 'ength: 1,3 mm'"),
    ('L33', 'range: 3 to 5 mm', 'Captured length: 3 to 5 mm', 3, A,
     "quote has a quantity outside the closed grammar: unit term 'to' outside the grammar at 'ngth: 3 to 5 mm'"),
    ('L34', 'range: between 3 mm and 5 mm', 'Captured length: between 3 mm and 5 mm', 3, A,
     "quote has a quantity outside the closed grammar: range (between) at 'between 3 mm and 5 m'"),
    ('L35', 'r2 H3: 1000 um = 1 mm', 'Captured maximum separation: 1000 um', 1, H, None),
    ('L36', 'r2 H3: 1000 µm = 1 mm', 'Captured: 1000 µm', 1, H, None),
    ('L37', 'r2 H3: 1000000 nm = 1 mm', 'Captured: 1000000 nm', 1, H, None),
    ('L38', 'r2 H3: 10 mil = 0.254 mm', 'Captured: 10 mil', Fraction(254, 1000), H, None),
    ('L39', 'r2 H3: 1e3 um = 1 mm', 'Captured: 1e3 um', 1, H, None),
    ('L40', 'r2 H3: exact value only (1000 um vs 1.000001 mm)', 'Captured: 1000 um', Fraction(1000001, 10 ** 6), A,
     'cited quote does not state the declared value'),
    ('L41', 'r1 H2: decision quotes 0.15 mm, declared 999 mm', 'Review synthetic numeric requirement: 0.15 mm', 999, A,
     'cited quote does not state the declared value'),
    ('L42', 'r1 H2 control: 0.15 mm = 0.15 mm', 'Review synthetic numeric requirement: 0.15 mm', Fraction(15, 100), H, None),
    ('L43', 'r1 H3: nonnumeric source', 'Review synthetic source: there is no numeric design requirement here.', 3, A,
     'cited quote does not state the declared value'),
    ('L44', 'unknown unit after numeral: yd', 'within 3 mm and 999 yd', 3, A,
     "quote has a quantity outside the closed grammar: unit term 'yd' outside the grammar at 'and 999 yd'"),
    ('L45', 'two in-grammar lengths, one equal', 'within 3 mm and 4 mm', 3, H, None),
    # Post-redesign conformance round 1 (449 ruling): the reviewer's extra_matrix.py rows.
    ('L46', 'conf H1: spaced ASCII sign', 'Captured offset: - 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: '-' outside the grammar at 'offset: - 3 mm'"),
    ('L47', 'conf H1: spaced unicode minus', 'Captured offset: \u2212 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: '\u2212' outside the grammar at 'offset: \u2212 3 mm'"),
    ('L48', 'conf H1: spaced superscript', 'Captured inverse length: 3 mm \u207b\u00b9', 3, A,
     "quote has a quantity outside the closed grammar: '\u207b' after a quantity outside the grammar at 'h: 3 mm \u207b\xb9'"),
    # Conformance round 2 (final, agent-521 GO): designator components and reciprocal powers.
    ('L49', 'conf2 H1: superscript designator component before the fact', 'Captured: D-1.\xb9, 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: superscript in '\xb9' outside the grammar at 'ed: D-1.\xb9, 3 mm'"),
    ('L50', 'conf2 H1: superscript word designator component before the fact', 'Captured: D-1.foo\xb9, 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: superscript in 'foo\xb9' outside the grammar at 'ed: D-1.foo\xb9, 3 mm'"),
    ('L51', 'conf2 H1: superscript designator component after the fact', 'Captured: 3 mm ; D-1.\xb9', 3, A,
     "quote has a quantity outside the closed grammar: superscript in '\xb9' outside the grammar at 'm ; D-1.\xb9'"),
    ('L52', 'conf2 H1: superscript in the designator initial word', 'Captured: D\xb9-1, 3 mm', 3, A,
     "quote has a quantity outside the closed grammar: superscript in 'D\xb9' outside the grammar at 'ptured: D\xb9-1, 3 mm'"),
    ('L53', 'conf2 H1 control: supported designator before the fact', 'Captured: D-1, 3 mm', 3, H,
     None),
    ('L54', 'conf2 H1 control: supported designator R12.3 before the fact', 'Captured: R12.3, 3 mm', 3, H,
     None),
    ('L55', 'conf2 H1 control: supported identifier U5-A before the fact', 'Captured: U5-A, 3 mm', 3, H,
     None),
    ('L56', 'conf2 H1 control: supported designator after the fact', 'Captured: 3 mm ; D-1', 3, H,
     None),
    ('L57', 'conf2 H1 control: supported designator U5-A.2 after the fact', 'Captured: 3 mm ; U5-A.2', 3, H,
     None),
    ('L58', 'conf2 H2 control: positive power on a length', 'Captured: 3 mm^1', 3, H,
     None),
    ('L59', 'conf2 H2 control: length reciprocal power cancels (not count-only rule)', 'Captured: 3 mm*mm^-1*mm', 3, H,
     None),
    ('L60', 'conf2 H2 control: length reciprocal U+2212 power cancels', 'Captured: 3 mm*s^\u22121*s', 3, H,
     None),
]
# (row, reviewer case, quote, expected, exact advisory reason or None) for a bound
# thermal_spoke_count (min 3) rule.
COUNTS = [
    ('C01', 'explicit count fact', 'Captured: 3 thermal spokes', H, None),
    ('C02', 'explicit count head', 'Captured: spoke count: 3', H, None),
    ('C03', 'explicit count noun', 'Captured: 3 spokes', H, None),
    ('C04', 'r2 H2: length as count', 'Captured board thickness: 3 mm. This record states no thermal spoke count.', A,
     'cited quote does not state the declared value'),
    ('C05', 'r2 H2: attached length as count', 'Captured board thickness: 3mm.', A,
     'cited quote does not state the declared value'),
    ('C06', 'r2 H2: bare integer', 'Captured value: 3', A,
     'cited quote does not state the declared value'),
    ('C07', 'r2 H2: other count kind', 'Captured: 3 vias', A,
     'cited quote does not state the declared value'),
    ('C08', 'r2 H2: other value', 'Captured: 4 thermal spokes', A,
     'cited quote does not state the declared value'),
    ('C09', 'r2 H2: non-integer count literal', 'Captured: 3.0 thermal spokes', A,
     "quote has a quantity outside the closed grammar: a count must be an unsigned integer literal at 'ptured: 3.0 thermal '"),
    ('C10', 'r3 H2: density 3 thermal spokes/mm', 'Captured spoke density: 3 thermal spokes/mm', A,
     'cited quote does not state the declared value'),
    ('C11', 'r3 H2: density 3 spokes per mm', 'Captured spoke density: 3 spokes per mm', A,
     'cited quote does not state the declared value'),
    ('C12', 'r3 H2: density spoke count: 3 / mm', 'Captured spoke density: spoke count: 3 / mm', A,
     'cited quote does not state the declared value'),
    ('C13', 'signed count', 'Captured: +3 thermal spokes', A,
     "quote has a quantity outside the closed grammar: a count must be an unsigned integer literal at 'tured: +3 thermal sp'"),
    # Post-redesign conformance round 1 (449 ruling): denominators that cancel still refuse.
    ('C14', 'conf H2: cancelled length denominator', '3 spokes/mm*mm', A, 'count fact has a denominator'),
    ('C15', 'conf H2: head form, cancelled denominator', 'spoke count: 3 /mm*mm', A, 'count fact has a denominator'),
    ('C16', 'conf H2: cancelled time denominator', '3 spokes /s*s', A, 'count fact has a denominator'),
    # Conformance round 2 (final): a reciprocal power is a denominator before cancellation.
    ('C17', 'conf2 H2: reciprocal ASCII power, length', 'Captured: 3 spokes*mm^-1*mm', A, 'count fact has a denominator'),
    ('C18', 'conf2 H2: reciprocal U+2212 power, length', 'Captured: 3 spokes*mm^\u22121*mm', A, 'count fact has a denominator'),
    ('C19', 'conf2 H2: reciprocal ASCII power, time', 'Captured: 3 spokes*s^-1*s', A, 'count fact has a denominator'),
    ('C20', 'conf2 H2: reciprocal U+2212 power, time', 'Captured: 3 spokes*s^\u22121*s', A, 'count fact has a denominator'),
    ('C21', 'conf2 H2 control: positive power on a count', 'Captured: 3 spokes^1', H, None),
]
CLASSES = ('owner', 'manufacturer', 'datasheet', 'engineering')
COUNT_RULE = b'(version 1) (rule "matrix count" (constraint thermal_spoke_count (min 3)))'


def mm(value):
    return value if isinstance(value, Fraction) else Fraction(value)


def import_row(cls, quote, value):
    path = 'matrix/source.txt'
    declared = float(value) if isinstance(value, Fraction) else value
    document = {'schema': SCHEMA, 'source_records': {path: quote}, 'board_setup_rules': {'min_clearance': {
        'value_mm': declared, 'class': cls, 'basis': 'matrix', 'citation': citation(cls, quote, path)}}}
    project, record = import_constraints({}, json_bytes(document), 'matrix/limits.json', 'matrix')
    setting = record['settings'][0]
    return setting, geometry_defaults(project, record)


def locality_row(cls, quote, value):
    path = 'matrix/source.txt'
    nm = mm(value) * 10 ** 6
    if nm.denominator != 1:
        return None
    nm = int(nm)
    if cls == 'datasheet':
        provenance = {'class': 'datasheet', 'basis': 'matrix', 'derivation': {
            'kind': 'numeric', 'value_nm': nm, 'source': citation('datasheet', quote, path)}}
    else:
        reference = citation('owner', quote, path)
        reference.pop('kind')
        provenance = {'class': 'owner', 'basis': 'matrix', 'decision_reference': reference}
    return locality_provenance.classify({'max_distance_nm': nm, 'provenance': provenance}, {path: quote})


def count_row(quote):
    path = 'matrix/count.txt'
    spec = {'rule': 'matrix count', 'class': 'owner', 'basis': 'matrix', 'citation': citation('owner', quote, path)}
    document = {'schema': SCHEMA, 'source_records': {path: quote}, 'board_setup_rules': {},
                'custom_rules': {'candidate.kicad_dru': [spec]}}
    project, record = import_constraints({}, json_bytes(document), 'matrix/limits.json', 'matrix')
    bind_project(project, record, COUNT_RULE)
    with tempfile.TemporaryDirectory() as root:
        board = Path(root) / 'candidate.kicad_pcb'
        board.with_suffix('.kicad_dru').write_bytes(COUNT_RULE)
        write_json(board.with_suffix('.kicad_pro'), project)
        sources = finding_sources(board)
    hard = [s for s in sources['settings'] if s['setting'].startswith('native_rules.') and s['enforcement'] == 'hard']
    return sources['rules']['matrix count'], hard, geometry_defaults(project, record, COUNT_RULE)


def observe_length(quote, value):
    out = {}
    for cls in CLASSES:
        setting, rows = import_row(cls, quote, value)
        out['import:' + cls] = (setting['enforcement'], len(rows), setting.get('unsourced_reason'))
    for cls in ('datasheet', 'owner'):
        local = locality_row(cls, quote, value)
        out['locality:' + cls] = (local['enforcement'], local['reason']) if local else None
    return out


def observe_count(quote):
    rule, hard, rows = count_row(quote)
    return {'rule': ('hard' if rule['citation'] is not None else 'advisory', len(hard), len(rows),
                     rule.get('unsourced_reason'))}


class QuantityMatrixTest(unittest.TestCase):
    """The lane regression gate: every row must hold in every consumer, with its exact reason."""

    def test_length_rows_in_both_consumers(self):
        for row, case, quote, value, expected, reason in LENGTHS:
            with self.subTest(row=row, case=case, quote=quote):
                seen = observe_length(quote, value)
                for cls in CLASSES:
                    enforcement, rows, observed = seen['import:' + cls]
                    self.assertEqual(enforcement, expected, (cls, seen))
                    # A sourced owner/manufacturer/datasheet value leaves the disclosure;
                    # engineering margins and every advisory value stay in it, with a reason.
                    self.assertEqual(rows, 0 if expected == H and cls != 'engineering' else 1, (cls, seen))
                    self.assertEqual(observed, reason, (cls, seen))
                for cls in ('datasheet', 'owner'):
                    if seen['locality:' + cls] is not None:
                        self.assertEqual(seen['locality:' + cls][0], expected, (cls, seen))
                        if expected == A:
                            self.assertEqual(seen['locality:' + cls][1], reason, (cls, seen))

    def test_length_rows_grade_in_electrical_locality(self):
        from test_electrical_locality import board, declaration
        for row, case, quote, value, expected, reason in LENGTHS:
            nm = mm(value) * 10 ** 6
            if nm.denominator != 1:
                continue
            with self.subTest(row=row, case=case, quote=quote):
                facts, doc = board(), declaration(limit=int(nm))
                doc['source_records'] = {'matrix/source.txt': quote}
                for role in ('supply', 'return'):
                    doc['obligations'][0][role]['provenance'] = {'class': 'datasheet', 'basis': 'matrix', 'derivation': {
                        'kind': 'numeric', 'value_nm': int(nm), 'source': citation('datasheet', quote, 'matrix/source.txt')}}
                graded = electrical_locality.grade(facts, doc)
                kinds = {m['constraint_provenance']['enforcement'] for m in graded['measured']}
                self.assertEqual(kinds, {expected}, graded)
                if expected == A:
                    self.assertFalse(graded['defects'])
                    self.assertEqual({m['constraint_provenance']['reason'] for m in graded['measured']}, {reason})

    def test_count_rows(self):
        for row, case, quote, expected, reason in COUNTS:
            with self.subTest(row=row, case=case, quote=quote):
                enforcement, hard, rows, observed = observe_count(quote)['rule']
                self.assertEqual(enforcement, expected)
                self.assertEqual((hard, rows), (1, 0) if expected == H else (0, 1))
                self.assertEqual(observed, reason)

    def test_every_unhandled_token_class_is_unsupported(self):
        """Conformance H1: consume or reject. Each class is advisory, disclosed, exact reason."""
        outside = 'quote has a quantity outside the closed grammar: '
        lengths = (
            ('spaced ASCII sign', 'Captured: - 3 mm',
             "quote has a quantity outside the closed grammar: '-' outside the grammar at 'ptured: - 3 mm'"),
            ('spaced U+2212', 'Captured: \u2212 3 mm',
             "quote has a quantity outside the closed grammar: '\u2212' outside the grammar at 'ptured: \u2212 3 mm'"),
            ('superscript after WS', 'Captured: 3 mm \u207b\xb9',
             "quote has a quantity outside the closed grammar: '\u207b' after a quantity outside the grammar at 'd: 3 mm \u207b\xb9'"),
            ('superscript attached', 'Captured: 3 mm\u207b\xb9',
             "quote has a quantity outside the closed grammar: '\u207b' joined to a quantity at 'ed: 3 mm\u207b\xb9'"),
            ('superscript word', 'Captured: 3 mm \xb9',
             "quote has a quantity outside the closed grammar: superscript in '\xb9' outside the grammar at 'd: 3 mm \xb9'"),
            ('stray percent', 'Captured: 3 mm %',
             "quote has a quantity outside the closed grammar: '%' after a quantity outside the grammar at 'd: 3 mm %'"),
            ('stray degree', 'Captured: 3 mm \xb0',
             "quote has a quantity outside the closed grammar: '\xb0' after a quantity outside the grammar at 'd: 3 mm \xb0'"),
            ('stray hash', 'Captured # 3 mm',
             "quote has a quantity outside the closed grammar: '#' outside the grammar at 'aptured # 3 mm'"),
            ('stray zero-width space', 'Captured: 3 mm\u200b',
             "quote has a quantity outside the closed grammar: '\\u200b' joined to a quantity at 'ed: 3 mm\\u200b'"),
            ('stray operator', 'Captured * 3 mm',
             "quote has a quantity outside the closed grammar: '*' outside the grammar at 'aptured * 3 mm'"),
        )
        for name, quote, reason in lengths:
            with self.subTest(token_class=name):
                seen = observe_length(quote, 3)
                for cls in CLASSES:
                    self.assertEqual(seen['import:' + cls], (A, 1, reason), (cls, seen))
                for cls in ('datasheet', 'owner'):
                    self.assertEqual(seen['locality:' + cls], (A, reason), (cls, seen))
        counts = (
            ('spaced ASCII sign (count head)', 'spoke count: - 3',
             "quote has a quantity outside the closed grammar: '-' outside the grammar at ' count: - 3'"),
            ('spaced U+2212 (count)', '\u2212 3 spokes',
             "quote has a quantity outside the closed grammar: '\u2212' outside the grammar at '\u2212 3 spokes'"),
            ('superscript (count)', '3 spokes \u207b\xb9',
             "quote has a quantity outside the closed grammar: '\u207b' after a quantity outside the grammar at ' spokes \u207b\xb9'"),
        )
        for name, quote, reason in counts:
            with self.subTest(token_class=name):
                self.assertEqual(observe_count(quote)['rule'], (A, 0, 1, reason))


def report():
    """One JSON line per matrix row: expected versus every consumer's observed outcome."""
    for row, case, quote, value, expected, reason in LENGTHS:
        seen = observe_length(quote, value)
        verdicts = {k: (v[0] if v else None) for k, v in seen.items()}
        ok = (all(v in (None, expected) for v in verdicts.values())
              and all(seen['import:' + cls][2] == reason for cls in CLASSES))
        print(json.dumps({'row': row, 'case': case, 'quote': quote, 'expected': expected,
                          'observed': verdicts, 'holds': ok}, ensure_ascii=False), flush=True)
    for row, case, quote, expected, reason in COUNTS:
        enforcement, hard, rows, observed = observe_count(quote)['rule']
        ok = (enforcement == expected and (hard, rows) == ((1, 0) if expected == H else (0, 1))
              and observed == reason)
        print(json.dumps({'row': row, 'case': case, 'quote': quote, 'expected': expected,
                          'observed': {'rule': enforcement, 'hard_count_settings': hard, 'geometry_rows': rows},
                          'holds': ok}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if '--report' in sys.argv:
        report()
    else:
        unittest.main()
