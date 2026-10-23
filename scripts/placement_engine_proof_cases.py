"""Additional complete-plan parity inputs from the repository's owned fixtures.

``cases()`` only constructs independent JSON inputs. It does not run a planner,
generate expected answers, invoke KiCad, or change the comparison harness.
Each tuple is (name, captured facts, complete request, optional netlist XML),
the same shape consumed by ``placement_engine_parity.fixtures``.

The cases exercise proof provenance and retries, source-bound locality and exact
centroids, public legacy-first cluster behavior, and advisory regions. They are
synthetic regression fixtures, not additional performance benchmark workloads.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import random
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]


def cases() -> list[tuple[str, dict, dict, str | None]]:
    """Return fresh cases without executing either planning implementation."""
    from agent_ee import electrical_locality
    from agent_ee.placer import transform
    from test_advisory_regions import region
    from test_electrical_locality import board as locality_board, declaration, bound_output_loop
    from test_locality_provenance import source as numeric_source, numeric
    from test_placement import MM, capture, part, rectangle, polygon
    from test_placer import request, authored_gap, authored_groups
    from test_placer_capacity import columns_case, authored_margin
    from test_placer_clusters import cluster_board, random_cluster_case, three_cap_case
    from test_placer_residuals import box, GroupRegionTest
    from test_placer_search import board, RegionCapacityTest

    out: list[tuple[str, dict, dict, str | None]] = []

    def add(name, facts, policy):
        # Deep snapshots prevent variants from mutating an earlier case or shared
        # constants such as test_electrical_locality.FIXTURE_SOURCES.
        out.append((name, copy.deepcopy(facts), {'policy': copy.deepcopy(policy)}, None))

    # tests/test_placer_capacity.py: free area, fragmentation, and removing only
    # advisory constraints from an otherwise conclusive geometric proof.
    for count in (6, 7):
        facts, policy = columns_case(count, capacity_proof=True)
        add(f'proof-columns-{count}-sourced', facts, policy)
    facts, policy = columns_case(7, capacity_proof=True)
    policy['constraint_provenance'].pop('gap_nm')
    add('proof-columns-7-unsourced-gap', facts, policy)
    for count in (7, 8, 10):
        facts, policy = columns_case(count, capacity_proof=True)
        policy.pop('constraint_provenance')
        add(f'proof-columns-{count}-unsourced-gap-and-margin', facts, policy)
    facts, policy = columns_case(7)
    policy['step_budget'] = 40
    add('proof-columns-no-opt-in-budget-40', facts, policy)

    facts = capture([part('C1', 5, 5)], rectangle(0, 0, 4, 20))
    policy = request(fixed_refs=[], gap_nm=250000, min_courtyard_edge_mm=1.5,
                     capacity_proof=True)
    add('proof-margin-sourced-gap-retry', facts, authored_margin(policy))
    policy['gap_nm'] = 0
    add('proof-margin-unsourced', facts, policy)
    add('proof-margin-sourced', facts, authored_margin(policy))
    facts = board([part(f'C{i}', 0, 0) for i in range(16)], rectangle(0, 0, 10, 10))
    policy = authored_gap(request(fixed_refs=[], gap_nm=MM, min_courtyard_edge_mm=0.01,
                                  capacity_proof=True),
                          'Original fixture: tests/test_placer_capacity.py, 1 mm gap')
    add('proof-gap-sourced-margin-retry', facts, policy)

    for label, polygons in (
        ('single', [rectangle(4, 4, 6, 4)]),
        ('touching', [rectangle(4, 4, 3, 4), rectangle(7, 4, 3, 4)]),
        ('overlapping', [rectangle(4, 4, 4, 4), rectangle(6, 4, 4, 4)]),
        ('duplicate', [rectangle(4, 4, 6, 4), rectangle(4, 4, 6, 4), rectangle(5, 5, 1, 1)]),
    ):
        add(f'proof-obstacle-union-{label}', capture([part('C1', 0, 0)]),
            request(fixed_refs=[], gap_nm=250000, capacity_proof=True,
                    obstacles=[{'side': 'F', 'polygons': polygons}]))

    # The intrinsic extent proof is distinct from area and packing capacity.
    facts = capture([part('C1', 0, 0, shape=rectangle(0, 0, 3, 3))], rectangle(0, 0, 2, 20))
    add('proof-intrinsic-minimum-width', facts, request(fixed_refs=[], gap_nm=0))
    soft_outline = {'outline': {'class': 'assumed', 'enforcement': 'soft',
                                'soft_reason': 'Original fixture: envelope size is unconfirmed'}}
    add('proof-intrinsic-soft-outline', facts,
        request(fixed_refs=[], gap_nm=0, constraint_provenance=soft_outline))
    notch = polygon([(0, 0), (8, 0), (8, 5), (5, 5), (5, 8), (0, 8)])
    facts = capture([part('U1', 0, 0, shape=notch), part('C1', 1, 1)], rectangle(0, 0, 8, 8))
    policy = authored_gap(authored_margin(request(gap_nm=1500000, capacity_proof=True)),
                          'Original fixture: tests/test_placer_capacity.py, 1.5 mm gap')
    add('proof-rectilinear-notch-fragmentation', facts, policy)

    # tests/test_placer_search.py: exact region boundaries, nested regions,
    # named blockers, and a later structural proof after a soft first proof.
    region_fixture = RegionCapacityTest()
    for label, side in (('exact-fill', 5), ('one-nm-overflow', 5.000001)):
        facts, groups = region_fixture.squares(side, 4)
        add(f'groups-region-{label}', facts, request(fixed_refs=[], gap_nm=0, groups=groups))
    facts, groups = region_fixture.squares(4.6, 4, fixed=(('F', 0, 0, 4),))
    add('groups-region-fixed-blocker', facts, request(fixed_refs=['F'], gap_nm=0, groups=groups))
    facts, groups = region_fixture.squares(5, 4)
    add('groups-region-soft-gap', facts, request(fixed_refs=[], gap_nm=500000, groups=groups))
    add('groups-region-sourced-gap', facts,
        authored_gap(request(fixed_refs=[], gap_nm=500000, groups=groups),
                     'Original fixture: tests/test_placer_search.py, 0.5 mm gap'))
    facts['footprints'].append(part('X', 12, 12, shape=rectangle(12, 12, 3, 3)))
    groups.append({'id': 'structural', 'references': ['X'],
                   'region': {'polygons': [box(10, 0, 12, 2)]}})
    groups = authored_groups(groups, 'Original fixture: tests/test_placer_search.py structural region')
    add('groups-region-zero-gap-finds-later-proof', facts,
        request(fixed_refs=[], gap_nm=500000, groups=groups))
    facts = board([part('M1', 12, 0, shape=rectangle(12, 0, 8, 8)),
                   part('M2', 12, 12, shape=rectangle(12, 12, 4.5, 4.5)),
                   part('M3', 17, 12, shape=rectangle(17, 12, 4.5, 4.5))],
                  rectangle(0, 0, 30, 30))
    groups = [{'id': 'outer', 'references': ['M1'], 'region': {'polygons': [box(0, 0, 10, 10)]}},
              {'id': 'inner', 'references': ['M2', 'M3'], 'region': {'polygons': [box(0, 0, 5, 10)]}}]
    add('groups-nested-region-sourced', facts,
        request(fixed_refs=[], gap_nm=0, groups=authored_groups(groups,
                'Original fixture: tests/test_placer_search.py nested regions')))
    add('groups-nested-region-unsourced', facts, request(fixed_refs=[], gap_nm=0, groups=groups))
    group_fixture = GroupRegionTest()
    add('groups-anchor-point-and-reference', group_fixture.board(),
        request(fixed_refs=[], step_budget=200000, groups=group_fixture.GROUPS))

    # Numeric provenance refusals must preserve explicit source errors, not
    # silently convert a malformed source into an assumed hard limit.
    facts, policy = columns_case(7, capacity_proof=True)
    policy['constraint_provenance']['gap_nm']['value_nm'] = 1
    add('proof-refusal-gap-source-value-mismatch', facts, policy)
    facts, policy = columns_case(7, capacity_proof=True)
    policy['constraint_provenance']['gap_nm']['citation'] = ' '
    add('proof-refusal-gap-source-empty-citation', facts, policy)
    add('proof-refusal-assumed-hard-outline', capture([part('U1', 2, 2)]),
        request(constraint_provenance={'outline': {'class': 'assumed', 'enforcement': 'hard'}}))

    # tests/test_electrical_locality.py and test_locality_provenance.py.
    add('locality-hard-guidance', locality_board(), request(electrical_locality=declaration()))
    facts, doc = locality_board(), declaration()
    facts['footprints'][1]['pads'][0]['copper_centroid_offset_nm'] = [[1, 3], [-2, 7]]
    add('locality-fractional-centroid', facts, request(electrical_locality=doc))
    add('locality-fixed-hard-distance-refusal', locality_board(),
        request(fixed_refs=['U1', 'C1'], electrical_locality=declaration(limit=MM)))

    facts, doc = locality_board(), declaration(limit=0)
    for role in electrical_locality.roles(doc['obligations'][0]):
        doc['obligations'][0][role].pop('provenance')
        doc['obligations'][0][role].pop('citation')
    add('locality-uncited-soft-zero-limit', facts, request(electrical_locality=doc))
    doc['obligations'][0]['supply'].pop('max_distance_nm')
    add('locality-missing-maximum-refusal', facts, request(electrical_locality=doc))

    for label, mutate in (
        ('source-integrity-refusal', lambda d: d['source_records'].update(
            {'fixture-decisions/EE322.txt': 'Changed source bytes'})),
        ('missing-hard-citation-refusal', lambda d: d['obligations'][0]['supply'].pop('citation')),
        ('agent-owner-label-advisory', lambda d: [d['obligations'][0][role]['provenance']
            ['decision_reference'].update(owner='agent-524') for role in ('supply', 'return')]),
    ):
        facts, doc = locality_board(), copy.deepcopy(declaration(limit=MM))
        mutate(doc)
        add(f'locality-{label}', facts,
            request(fixed_refs=['U1', 'C1'], electrical_locality=doc))

    for label, corrupt in (('numeric-datasheet-hard', False), ('numeric-datasheet-corrupt-source', True)):
        facts, doc = locality_board(), copy.deepcopy(declaration(limit=MM))
        source = numeric_source()
        doc['source_records'][source['path']] = 'Changed source bytes' if corrupt else source['quote']
        for role in ('supply', 'return'):
            numeric(doc['obligations'][0][role])
        add(f'locality-{label}', facts,
            request(fixed_refs=['U1', 'C1'], electrical_locality=doc))

    # Known necessary copper floor; the assembly estimate remains advisory.
    for kind in ('owner', 'datasheet', 'default'):
        facts, doc = locality_board(), copy.deepcopy(declaration(limit=1000))
        facts['footprints'][0]['pads'][0]['size_nm'] = [1500000, 1500000]
        facts['footprints'][1]['pads'][1]['size_nm'] = [1500000, 1500000]
        source = numeric_source('Original numeric requirement: 1000 nm')
        doc['source_records'][source['path']] = source['quote']
        for role in ('supply', 'return'):
            pair = doc['obligations'][0][role]
            if kind == 'owner':
                pair['provenance'] = {'class': 'owner', 'basis': 'fixture requirement',
                    'decision_reference': {**source, 'owner': 'fixture author'}}
            elif kind == 'datasheet':
                pair['provenance'] = {'class': 'datasheet', 'basis': 'fixture number',
                    'derivation': {'kind': 'numeric', 'value_nm': 1000, 'source': source}}
            else:
                pair.pop('provenance')
        add(f'locality-copper-floor-{kind}', facts,
            request(electrical_locality=doc, step_budget=1000))

    facts, doc = bound_output_loop()
    for role in electrical_locality.roles(doc['obligations'][0]):
        doc['obligations'][0][role]['max_distance_nm'] = 4 * MM
    add('locality-bound-buck-output-loop', facts,
        request(electrical_locality=doc, priority_refs=['C1', 'L1', 'U1'], step_budget=1000))

    # Public _plan_local uses legacy-first packing. These cases deliberately do
    # not invoke joint_pack directly: retries and diagnostic fields must agree.
    facts, policy = random_cluster_case(random.Random(1))
    doc = policy['electrical_locality']
    doc.pop('source_records')
    for obligation in doc['obligations']:
        for role in electrical_locality.roles(obligation):
            obligation[role].pop('provenance')
            obligation[role]['max_distance_nm'] = 1
    add('locality-cluster-advisory-no-pruning', facts, policy)
    facts, doc = cluster_board()
    for role in ('supply', 'return'):
        doc['obligations'][1][role]['max_distance_nm'] = 3 * MM
    add('locality-cluster-tightest-member', facts,
        request(electrical_locality=doc, priority_refs=['C1'], step_budget=1000))
    facts, policy = three_cap_case()
    add('locality-cluster-three-capacitors', facts, policy)
    facts, doc = cluster_board(limit=0)
    add('locality-cluster-finite-exhaustion', facts,
        request(electrical_locality=doc, step_budget=1000))

    # tests/test_advisory_regions.py: region reporting never enters search
    # constraints, and shadows bind their owner at the resulting pose.
    facts = capture([part('U1', 0, 0), part('C1', 3, 0)], rectangle(0, 0, 6, 2))
    right = [rectangle(2, 0, 4, 2)]
    add('groups-advisory-region-never-prunes', facts,
        request(gap_nm=0, advisory_regions=[region('right-half', side='F', polygons=right)]))
    add('groups-same-geometry-obstacle-refuses', facts,
        request(gap_nm=0, obstacles=[{'side': 'F', 'polygons': right}]))
    facts = capture([part('L1', 2, 2), part('J3', 3, 3, 'B'), part('C1', 10, 10)])
    add('groups-advisory-shadow-fixed-intruder', facts,
        request(fixed_refs=['L1', 'J3'], advisory_regions=[
            region('L1-shadow', shadow_of={'reference': 'L1', 'margin_nm': 500000})]))
    facts = capture([part('U2', 5, 5), part('C1', 8, 5), part('C2', 15, 15)])
    add('groups-advisory-absolute-subject-and-near', facts,
        request(fixed_refs=['U2', 'C1', 'C2'], advisory_regions=[region('imu-ring', side='F',
            polygons=[rectangle(4, 4, 4, 4)], subject='U2', proximity_nm=MM,
            concern='Original fixture: tall or heavy parts away from the IMU')]))
    facts = capture([part('L1', 2, 2), part('R1', 3, 3, 'B'), part('R2', 13, 13, 'B')])
    facts['footprints'][0] = transform(facts['footprints'][0], [12 * MM, 10 * MM], 0)
    add('groups-advisory-shadow-proximity-boundary', facts,
        request(fixed_refs=['L1', 'R1', 'R2'], advisory_regions=[region('L1-shadow',
            shadow_of={'reference': 'L1', 'margin_nm': 0}, proximity_nm=MM)]))
    add('groups-refusal-advisory-source-shape', capture([part('U1', 2, 2)]),
        request(advisory_regions=[region('bad', side='F', polygons=[rectangle(0, 0, 1, 1)],
                                         source={'class': 'board', 'citation': 'not an advisory source class'})]))
    add('groups-refusal-region-polygon', capture([part('U1', 2, 2)]),
        request(groups=[{'id': 'bad', 'references': ['U1'],
                         'region': {'polygons': [[[0, 0], [1, 0]]]}}]))

    names = [row[0] for row in out]
    if len(names) != len(set(names)):
        raise ValueError('duplicate proof parity case name')
    # Reject accidental non-JSON fixture payloads at construction, before either
    # implementation receives them. This does not compute an expected plan.
    json.dumps(out, allow_nan=False)
    return out


if __name__ == '__main__':
    print(json.dumps([name for name, *_ in cases()], indent=2))
