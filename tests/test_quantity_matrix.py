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
# (row, reviewer case, quote, governed millimetres, expected)
LENGTHS = [
    ('L01', 'r3 table: exact control', '3 mm', 3, H),
    ('L02', 'r3 table: 3.0 mm', '3.0 mm', 3, H),
    ('L03', 'r3 table: +3 mm', '+3 mm', 3, H),
    ('L04', 'r3 table: 3e0 mm', '3e0 mm', 3, H),
    ('L05', 'r3 table: 30e-1 mm', '30e-1 mm', 3, H),
    ('L06', 'r2/r3: attached unit 3mm', '3mm', 3, H),
    ('L07', 'r2 H1/r3 table: exponent suffix 1e-3 mm', '1e-3 mm', 3, A),
    ('L08', 'r2 H1/r3 table: dropped sign -3 mm', '-3 mm', 3, A),
    ('L09', 'r2 H1/r3 table: unicode minus U+2212', '−3 mm', 3, A),
    ('L10', 'r3 table: plus-minus ±3 mm', '±3 mm', 3, A),
    ('L11', 'r3 table: tolerance 3 ±0.1 mm', '3 ±0.1 mm', 3, A),
    ('L12', 'r3 table: grouped 3,000 um', '3,000 um', 3, A),
    ('L13', 'r3 table: grouped 3 000 um', '3 000 um', 3, A),
    ('L14', 'r3 table: unit cm 0.3 cm', '0.3 cm', 3, A),
    ('L15', 'r3 table: unit mils 3 mils', '3 mils', 3, A),
    ('L16', 'r3 table: continuation 3 mm2', '3 mm2', 3, A),
    ('L17', 'r3 table: range 3-5 mm', '3-5 mm', 3, A),
    ('L18', 'r3 H1: rate 3 mm/s', 'Captured feed speed or area: 3 mm/s', 3, A),
    ('L19', 'r3 H1: area 3 mm^2', 'Captured feed speed or area: 3 mm^2', 3, A),
    ('L20', 'r3 H1: spaced rate 3 mm / s', 'Captured feed speed or area: 3 mm / s', 3, A),
    ('L21', 'r3 H1: word rate 3 mm/second', 'Captured feed speed or area: 3 mm/second', 3, A),
    ('L22', 'r3 H1 (superscript): 3 mm²', '3 mm²', 3, A),
    ('L23', 'r1 substring: 13 mm', 'Captured length: 13 mm', 3, A),
    ('L24', 'r1 substring: 3.3 mm', 'Captured length: 3.3 mm', 3, A),
    ('L25', 'r2 suffix: x-3 mm', 'Captured length: x-3 mm', 3, A),
    ('L26', 'r2 range: 1-3 mm', 'Captured length: 1-3 mm', 3, A),
    ('L27', 'r2 fraction: 1/3 mm', 'Captured length: 1/3 mm', 3, A),
    ('L28', 'r2 grouping: 1’3 mm', 'Captured length: 1’3 mm', 3, A),
    ('L29', "r2 grouping: 1'3 mm", "Captured length: 1'3 mm", 3, A),
    ('L30', 'r2 grouping: 1 3 mm', 'Captured length: 1 3 mm', 3, A),
    ('L31', 'r2 grouping: thin space 1 3 mm', 'Captured length: 1 3 mm', 3, A),
    ('L32', 'r2 grouping: 1,3 mm', 'Captured length: 1,3 mm', 3, A),
    ('L33', 'range: 3 to 5 mm', 'Captured length: 3 to 5 mm', 3, A),
    ('L34', 'range: between 3 mm and 5 mm', 'Captured length: between 3 mm and 5 mm', 3, A),
    ('L35', 'r2 H3: 1000 um = 1 mm', 'Captured maximum separation: 1000 um', 1, H),
    ('L36', 'r2 H3: 1000 µm = 1 mm', 'Captured: 1000 µm', 1, H),
    ('L37', 'r2 H3: 1000000 nm = 1 mm', 'Captured: 1000000 nm', 1, H),
    ('L38', 'r2 H3: 10 mil = 0.254 mm', 'Captured: 10 mil', Fraction(254, 1000), H),
    ('L39', 'r2 H3: 1e3 um = 1 mm', 'Captured: 1e3 um', 1, H),
    ('L40', 'r2 H3: exact value only (1000 um vs 1.000001 mm)', 'Captured: 1000 um', Fraction(1000001, 10 ** 6), A),
    ('L41', 'r1 H2: decision quotes 0.15 mm, declared 999 mm', 'Review synthetic numeric requirement: 0.15 mm', 999, A),
    ('L42', 'r1 H2 control: 0.15 mm = 0.15 mm', 'Review synthetic numeric requirement: 0.15 mm', Fraction(15, 100), H),
    ('L43', 'r1 H3: nonnumeric source', 'Review synthetic source: there is no numeric design requirement here.', 3, A),
    ('L44', 'unknown unit after numeral: yd', 'within 3 mm and 999 yd', 3, A),
    ('L45', 'two in-grammar lengths, one equal', 'within 3 mm and 4 mm', 3, H),
]
# (row, reviewer case, quote, expected) for a bound thermal_spoke_count (min 3) rule.
COUNTS = [
    ('C01', 'explicit count fact', 'Captured: 3 thermal spokes', H),
    ('C02', 'explicit count head', 'Captured: spoke count: 3', H),
    ('C03', 'explicit count noun', 'Captured: 3 spokes', H),
    ('C04', 'r2 H2: length as count', 'Captured board thickness: 3 mm. This record states no thermal spoke count.', A),
    ('C05', 'r2 H2: attached length as count', 'Captured board thickness: 3mm.', A),
    ('C06', 'r2 H2: bare integer', 'Captured value: 3', A),
    ('C07', 'r2 H2: other count kind', 'Captured: 3 vias', A),
    ('C08', 'r2 H2: other value', 'Captured: 4 thermal spokes', A),
    ('C09', 'r2 H2: non-integer count literal', 'Captured: 3.0 thermal spokes', A),
    ('C10', 'r3 H2: density 3 thermal spokes/mm', 'Captured spoke density: 3 thermal spokes/mm', A),
    ('C11', 'r3 H2: density 3 spokes per mm', 'Captured spoke density: 3 spokes per mm', A),
    ('C12', 'r3 H2: density spoke count: 3 / mm', 'Captured spoke density: spoke count: 3 / mm', A),
    ('C13', 'signed count', 'Captured: +3 thermal spokes', A),
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
    """The lane regression gate: every row must hold in every consumer."""

    def test_length_rows_in_both_consumers(self):
        for row, case, quote, value, expected in LENGTHS:
            with self.subTest(row=row, case=case, quote=quote):
                seen = observe_length(quote, value)
                for cls in CLASSES:
                    enforcement, rows, reason = seen['import:' + cls]
                    self.assertEqual(enforcement, expected, (cls, seen))
                    # A sourced owner/manufacturer/datasheet value leaves the disclosure;
                    # engineering margins and every advisory value stay in it, with a reason.
                    self.assertEqual(rows, 0 if expected == H and cls != 'engineering' else 1, (cls, seen))
                    if expected == A:
                        self.assertTrue(reason, (cls, seen))
                for cls in ('datasheet', 'owner'):
                    if seen['locality:' + cls] is not None:
                        self.assertEqual(seen['locality:' + cls][0], expected, (cls, seen))

    def test_length_rows_grade_in_electrical_locality(self):
        from test_electrical_locality import board, declaration
        for row, case, quote, value, expected in LENGTHS:
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

    def test_count_rows(self):
        for row, case, quote, expected in COUNTS:
            with self.subTest(row=row, case=case, quote=quote):
                enforcement, hard, rows, reason = observe_count(quote)['rule']
                self.assertEqual(enforcement, expected)
                self.assertEqual((hard, rows), (1, 0) if expected == H else (0, 1))
                if expected == A:
                    self.assertTrue(reason)


def report():
    """One JSON line per matrix row: expected versus every consumer's observed outcome."""
    for row, case, quote, value, expected in LENGTHS:
        seen = observe_length(quote, value)
        verdicts = {k: (v[0] if v else None) for k, v in seen.items()}
        ok = all(v in (None, expected) for v in verdicts.values())
        print(json.dumps({'row': row, 'case': case, 'quote': quote, 'expected': expected,
                          'observed': verdicts, 'holds': ok}, ensure_ascii=False), flush=True)
    for row, case, quote, expected in COUNTS:
        enforcement, hard, rows, _ = observe_count(quote)['rule']
        ok = enforcement == expected and (hard, rows) == ((1, 0) if expected == H else (0, 1))
        print(json.dumps({'row': row, 'case': case, 'quote': quote, 'expected': expected,
                          'observed': {'rule': enforcement, 'hard_count_settings': hard, 'geometry_rows': rows},
                          'holds': ok}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if '--report' in sys.argv:
        report()
    else:
        unittest.main()
