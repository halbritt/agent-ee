"""Additional complete-request differential cases from the repository's authored fixtures.

Importing this module performs no work. ``cases`` prepares immutable inputs only;
the common parity driver independently executes Python and Rust on each request.
"""
import copy
import json
import unittest


def cases():
    """Return (name, captured facts, complete request, optional native XML text)."""
    from agent_ee import blocks
    from test_block_arrangement import ArrangementFixture, CITE, plan_policy
    from test_blocks import PARTS, SHEETS, RecoveryTests, board, native_xml, policy
    from test_placement import MM, part, rectangle
    from test_placement_plans import board as targeted_board, row, scoring
    from test_placer import request as packing_policy

    class Fixture(ArrangementFixture, unittest.TestCase):
        pass

    fixture = Fixture()
    output = []

    def emit(name, facts, request, raw=None):
        output.append((name, copy.deepcopy(facts), copy.deepcopy(request),
                       raw.decode('utf-8') if isinstance(raw, bytes) else raw))

    def arranged(name, *, facts=None, pol=None, entries=None, rules=None,
                 raw=None, allocation=None):
        section = {'arrangement': rules or plan_policy(),
                   'candidates': fixture.entries if entries is None else entries}
        if allocation is not None:
            section['allocation_text'] = json.dumps(allocation)
        emit(name, fixture.board if facts is None else facts,
             {'policy': fixture.policy if pol is None else pol, 'blocks': section},
             native_xml() if raw is None else raw)

    def packed(facts, pol, raw):
        inv, names, sha = fixture.inventory(raw)
        derivation = blocks.derive(inv, names, facts, inventory_sha256=sha)
        return [{'candidate': blocks.pack_block(facts, inv, derivation, b['id'], pol)['candidate']}
                for b in derivation['blocks']]

    try:
        fixture.setUp()
        keepout = {'id': 'authored-isolation', 'side': 'F',
                   'polygons': [rectangle(-1, -1, 14, 14)],
                   'provenance': {'enforcement': 'hard', 'citation': CITE}}
        arranged('blocks-hard-keepout', rules=plan_policy(keepouts=[keepout]))
        keepout['provenance'] = {'enforcement': 'advisory', 'reason': 'Authored advisory fixture.'}
        arranged('blocks-advisory-keepout', rules=plan_policy(keepouts=[keepout]))
        thermal = {'sources': ['U1', 'U2'], 'min_distance_nm': 8 * MM,
                   'provenance': {'enforcement': 'hard', 'citation': CITE}}
        arranged('blocks-hard-thermal', rules=plan_policy(thermal=thermal))
        thermal['provenance'] = {'enforcement': 'advisory'}
        arranged('blocks-advisory-thermal', rules=plan_policy(thermal=thermal))
        arranged('blocks-thermal-objective', rules=plan_policy(thermal=thermal, objective={
            'terms': ['thermal-spread', 'pad-locality'], 'provenance': {'enforcement': 'advisory'}}))
        thermal['min_distance_nm'] = 10 * MM
        thermal['provenance'] = {'enforcement': 'hard', 'citation': CITE}
        arranged('blocks-thermal-budget', rules=plan_policy(thermal=thermal, step_budget=10))
        arranged('blocks-priority', rules=plan_policy(priority_blocks=['Sensor']))

        back = copy.deepcopy(fixture.board)
        j1 = next(f for f in back['footprints'] if f['ref'] == 'J1')
        j1['side'] = 'B'
        j1['courtyards'] = {'B': j1['courtyards']['F']}
        for pad in j1['pads']:
            pad['layers'] = ['B.Cu']
        shadow = {'references': ['L1'], 'margin_nm': 20 * MM,
                  'provenance': {'enforcement': 'hard', 'citation': CITE}}
        arranged('blocks-hard-shadow', facts=back, rules=plan_policy(no_stacking=shadow))
        shadow['provenance'] = {'enforcement': 'advisory'}
        arranged('blocks-advisory-shadow', facts=back, rules=plan_policy(no_stacking=shadow))

        # These mutations stay structurally valid and must be refused by admission.
        changed = copy.deepcopy(fixture.entries)
        changed[0]['candidate']['members'][0]['pad_geometry_sha256'] = 'f' * 64
        arranged('blocks-identity-land-hash', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        changed[0]['candidate']['members'][0]['value']['text'] = 'wrong fixture value'
        arranged('blocks-identity-value', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        changed[0]['candidate']['sources']['policy_sha256'] = 'f' * 64
        arranged('blocks-source-pin', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        changed[0]['candidate']['search']['compaction']['box_area_nm2'] -= 1
        arranged('blocks-compaction-bound-area', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        for member in changed[0]['candidate']['members']:
            for pin in member['pins']:
                if pin.get('net') == 'VIN':
                    pin['net'] = 'VIN_OTHER'
        arranged('blocks-boundary-name', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        changed[0]['boundary_nets'] = {'n100': 'unknown', 'n200': 'unknown'}
        arranged('blocks-boundary-unknown-roles', entries=changed)
        changed = copy.deepcopy(fixture.entries)
        changed[0]['mapping'] = None
        arranged('blocks-explicit-null-mapping', entries=changed)

        # JSON float spelling is part of a captured candidate's policy hash.
        scientific = policy(min_courtyard_edge_mm=1e-6)
        arranged('blocks-scientific-policy-hash', pol=scientific,
                 entries=packed(fixture.board, scientific, native_xml()))

        # The independent native XML text reader must preserve entity references.
        escaped_parts = [list(p) for p in PARTS]
        escaped_parts[0][4] = 'owned & buck <reference> "quoted"'
        escaped_raw = native_xml(escaped_parts).replace(b'&amp;', b'&#38;')
        escaped_board = board(escaped_parts)
        arranged('blocks-native-xml-entities', facts=escaped_board, raw=escaped_raw,
                 entries=packed(escaped_board, fixture.policy, escaped_raw))

        # Reuse one source layout on a separate derived block. Original sheet parts
        # become explicit residual members so only the mapped candidate is needed.
        extras = [
            ['U11', '/s4/', 'BUCK', 'Owned:QFN4', 'owned-buck',
             {'1': 'VIN', '2': 'GND', '3': 'SW2', '4': 'V2'}],
            ['C11', '/s4/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}],
            ['L11', '/s4/', 'L', 'Owned:L1210', '10uH', {'1': 'SW2', '2': 'V2'}],
            ['C13', '/s4/', 'C', 'Owned:C0805', '22uF', {'1': 'V2', '2': 'GND'}],
            ['R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'V2', '2': 'GND'}],
        ]
        mapping = {'U1': 'U11', 'C1': 'C11', 'L1': 'L11', 'C3': 'C13'}
        allocation = {'schema': blocks.ALLOCATION,
                      'blocks': [{'id': 'Power2', 'references': list(mapping.values())}],
                      'residual': [p[0] for p in PARTS]}
        library = {'candidate': copy.deepcopy(fixture.entries[0]['candidate']), 'mapping': mapping,
                   'boundary_nets': {'n1': 'VIN', 'n2': 'GND', 'n4': 'V2'}}

        def mapped(name, *, changed_parts=None, entry=None):
            parts = list(PARTS) + (extras if changed_parts is None else changed_parts)
            raw = native_xml(parts, sheets={**SHEETS, '/s4/': '/Power2/'})
            facts = board(parts)
            facts['outline']['polygons'] = [rectangle(0, 0, 60, 40)]
            arranged(name, facts=facts, raw=raw, pol=policy(electrical_locality=None),
                     allocation=allocation, entries=[library if entry is None else entry])

        mapped('blocks-mapped-layout')
        undeclared = copy.deepcopy(library)
        del undeclared['boundary_nets']
        mapped('blocks-mapped-boundary-undeclared', entry=undeclared)
        changed = copy.deepcopy(extras)
        changed[2][5] = {'1': 'V2', '2': 'SW2'}
        mapped('blocks-mapped-role-split-merge', changed_parts=changed)
        changed = copy.deepcopy(extras)
        changed[0][2] = 'BUCK2'
        mapped('blocks-mapped-pin-function', changed_parts=changed)
        missing = copy.deepcopy(library)
        missing['mapping']['C3'] = 'C99'
        mapped('blocks-mapped-target-missing', entry=missing)
        tolerance = copy.deepcopy(library)
        member = next(m for m in tolerance['candidate']['members'] if m['reference'] == 'C3')
        member['value'] = {'rule': 'tolerance', 'text': '22uF', 'relative_tolerance': [1, 10],
                           'citation': 'Owned fixture value tolerance.'}
        for value, label in [('24.2uF', 'edge'), ('24.3uF', 'exceeded'), ('22uH', 'unit'),
                             ('22µF', 'unicode'), ('22u0F', 'rkm')]:
            changed = copy.deepcopy(extras)
            changed[3][4] = value
            mapped('blocks-mapped-tolerance-' + label, changed_parts=changed, entry=tolerance)

        recovery = RecoveryTests()
        try:
            recovery.setUp()
            candidate = recovery.stranded({'1': 'VX', '2': 'GND'})['candidate']
            raw = (recovery.dir / 'design.xml').read_bytes()
            sensor = blocks.pack_block(recovery.board, recovery.inv, recovery.derived,
                                       'Sensor', recovery.stranded_policy)['candidate']
            entries = [{'candidate': candidate}, {'candidate': sensor}]
            arranged('blocks-recovery-bound', facts=recovery.board, raw=raw,
                     pol=recovery.stranded_policy, entries=entries)
            changed = copy.deepcopy(entries)
            record = changed[0]['candidate']['search']['recovery']
            record['trials'][0]['max_distance_nm'] *= 2
            record['max_distance_nm'] *= 2
            arranged('blocks-recovery-wrong-start', facts=recovery.board, raw=raw,
                     pol=recovery.stranded_policy, entries=changed)
        finally:
            recovery.doCleanups()
    finally:
        fixture.doCleanups()

    for blocker in ('locked', 'held', 'untargeted'):
        facts, pol = targeted_board(), packing_policy()
        if blocker == 'locked':
            facts['footprints'][1]['locked'] = True
        elif blocker == 'held':
            pol['fixed_refs'].append('C1')
        else:
            facts['footprints'].append(part('R1', 5, 2))
        emit('targeted-' + blocker, facts, {'policy': pol, 'targeted': row()})
    facts = targeted_board()
    bga = facts['footprints'][0]
    bga['pads'] = [{'number': str(i), 'position_nm': [x, y], 'net': 'VCC',
                    'shape': 0, 'attribute': 'SMD', 'rotation_deg': 0}
                   for i, (x, y) in enumerate(((2*MM, 2*MM), (3*MM, 2*MM),
                                              (2*MM, 3*MM), (3*MM, 3*MM)), 1)]
    spec = {'mode': 'bga_fanout', 'bga_ref': 'U1', 'escape_offset_nm': 200_000,
            'judge_policy': scoring()}
    emit('targeted-bga-plan-only', facts, {'policy': packing_policy(), 'targeted': spec})
    for pad in bga['pads']:
        pad['position_nm'][0] -= 2 * MM
    emit('targeted-bga-outside', facts, {'policy': packing_policy(), 'targeted': spec})
    bga['pads'].pop()
    emit('targeted-bga-incomplete-grid', facts, {'policy': packing_policy(), 'targeted': spec})
    return output
