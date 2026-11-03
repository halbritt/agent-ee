"""Opt-in reviewer mutation harness, adapted only for changed source anchors.

Original: goal-20261103/local-loops-review/probe-code/tests/review_mutations.py.
Run explicitly as tests.review_local_loop_mutations through native-admit.
G5 now removes a redundant own-land guard: all-pad obstacles still enforce it.
C7, F6 and R2 retain the reviewer's equivalent-mutant classification.
"""
import json
import os
import shutil
import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path('/opt/agent-ee')
WORK = Path('/tmp/review-mutants')
OUT = Path('/phase/mutations')
LD = 'src/agent_ee/local_decoupling.py'
DL = 'src/agent_ee/decoupling_loops.py'
PJ = 'src/agent_ee/placement_job.py'
LR = 'src/agent_ee/local_return_seed.py'
T_LOCAL = ['test_local_decoupling']
T_CTRL = ['test_local_decoupling', 'test_placement_job_guards.ControllerBoundaryTests']
T_SEED = ['test_local_return_seed']

# The same 31 faults, with source anchors updated for this repair.
MUTANTS = {'baseline': ([],
              ['test_local_decoupling',
               'test_placement_job_guards.ControllerBoundaryTests',
               'test_local_return_seed']),
 'G1_no_capacitor_tie_via': ([('src/agent_ee/local_decoupling.py',
                               'def _tie(board, terminal, plane, *, clock, cancel_dir, away_from=None, '
                               'reusable=()):\n'
                               '    import pcbnew as k\n',
                               'def _tie(board, terminal, plane, *, clock, cancel_dir, away_from=None, '
                               'reusable=()):\n'
                               '    return []\n'
                               '    import pcbnew as k\n')],
                             ['test_local_decoupling']),
 'G2_leg_ignores_obstacles': ([('src/agent_ee/local_decoupling.py',
                                "    boxes = _obstacles(board, first.GetNetCode(), layer, rules['clearance'] "
                                '+ width // 2 + 25_000, (first, second))\n',
                                '    boxes = []\n')],
                              ['test_local_decoupling']),
 'G3_leg_tracks_unlocked': ([('src/agent_ee/local_decoupling.py',
                              '        item.SetLocked(True)\n        board.Add(item)\n',
                              '        board.Add(item)\n')],
                            ['test_local_decoupling']),
 'G4_failure_omits_relation': ([('src/agent_ee/local_decoupling.py',
                                 "raise JobRefusal('local decoupling ' + relation['id'] + ': ' + str(exc)) "
                                 'from exc',
                                 "raise JobRefusal('local decoupling: ' + str(exc)) from exc")],
                               ['test_local_decoupling']),
 'G5_via_may_sit_in_own_land': ([('src/agent_ee/local_decoupling.py',
                                  '        if _via_pad_collision(board, via, terminal, rules):\n'
                                  '            continue\n',
                                  '')],
                                ['test_local_decoupling']),
 'G6_no_board_edge_margin': ([('src/agent_ee/local_decoupling.py',
                               '        if not (edge.GetLeft() + margin < b[0] < edge.GetRight() - margin\n'
                               '                and edge.GetTop() + margin < b[1] < edge.GetBottom() - '
                               'margin):\n'
                               '            continue\n',
                               '')],
                             ['test_local_decoupling']),
 'G7_verify_ignores_changed_copper': ([('src/agent_ee/local_decoupling.py',
                                        'if actual is None or json.loads(json.dumps(actual)) != row:',
                                        'if actual is None:')],
                                      ['test_local_decoupling']),
 'G8_leg_width_quarter_class': ([('src/agent_ee/local_decoupling.py',
                                  "min(rules['width'], *(min(p.GetSize().x, p.GetSize().y) for p in (first, "
                                  'second))))',
                                  "min(rules['width'] // 4, *(min(p.GetSize().x, p.GetSize().y) for p in "
                                  '(first, second))))')],
                                ['test_local_decoupling']),
 'G9_leg_obstacle_margin_zero': ([('src/agent_ee/local_decoupling.py',
                                   '    boxes = _obstacles(board, first.GetNetCode(), layer, '
                                   "rules['clearance'] + width // 2 + 25_000, (first, second))\n",
                                   '    boxes = _obstacles(board, first.GetNetCode(), layer, 0, (first, '
                                   'second))\n')],
                                 ['test_local_decoupling']),
 'G10_tie_ignores_other_net_obstacles': ([('src/agent_ee/local_decoupling.py',
                                           '        if _via_site_blocked(board, via, terminal, rules):\n'
                                           '            continue\n',
                                           '')],
                                         ['test_local_decoupling']),
 'C1_bypass_always_false': ([('src/agent_ee/decoupling_loops.py',
                              '        return any(geo.shortest(self.adj, start, entry, blocked) is not None\n'
                              '                   for entry, _, _, _ in self.entries)\n',
                              '        return False\n')],
                            ['test_local_decoupling']),
 'C2_no_junction_through_pad': ([('src/agent_ee/decoupling_loops.py',
                                  "                    if item.GetClass() != 'PAD' and other.GetClass() != "
                                  "'PAD' and any(\n"
                                  '                            self.items[p].IsOnLayer(layer) and '
                                  'all(self.items[p].GetEffectiveShape(layer).Collide(\n'
                                  '                                k.VECTOR2I(round(point[0]), '
                                  'round(point[1])), 0) for point in (a, b))\n'
                                  '                            for p in touching[key] & touching[target]):\n'
                                  '                        continue\n',
                                  '')],
                                ['test_local_decoupling']),
 'C3_plane_hop_free': ([('src/agent_ee/decoupling_loops.py',
                         '                    hop = sum(math.dist(p, q) for p, q in zip(path, path[1:]))\n',
                         '                    hop = 0\n')],
                       ['test_local_decoupling']),
 'C4_via_barrel_free': ([('src/agent_ee/decoupling_loops.py',
                          '                self.edge(a, b, '
                          'int(self.board.GetDesignSettings().GetBoardThickness()))\n',
                          '                self.edge(a, b, 0)\n')],
                        ['test_local_decoupling']),
 'C5_open_path_not_a_defect': ([('src/agent_ee/decoupling_loops.py',
                                 "                if row[leg]['path'] is None:\n"
                                 "                    row['reasons'].append(leg + ': no native-connected "
                                 "copper path')\n",
                                 '')],
                               ['test_local_decoupling']),
 'C6_limit_not_enforced': ([('src/agent_ee/decoupling_loops.py',
                             "if row['loop_length_mm'] is not None and row['loop_length_mm'] > "
                             "relation['max_loop_mm']:",
                             'if False:')],
                           ['test_local_decoupling']),
 'C7_islands_merged': ([('src/agent_ee/decoupling_loops.py',
                         '                    if other != island or right is None:\n',
                         '                    if right is None:\n')],
                       ['test_local_decoupling']),
 'C8_check_unknown_passes': ([('src/agent_ee/decoupling_loops.py',
                               "outcome='pass' if result['outcome'] == 'info' else result['outcome'],",
                               "outcome='pass' if result['outcome'] in ('info', 'unknown') else "
                               "result['outcome'],")],
                             ['test_local_decoupling']),
 'C9_check_always_passes': ([('src/agent_ee/decoupling_loops.py',
                              "outcome='pass' if result['outcome'] == 'info' else result['outcome'],",
                              "outcome='pass',")],
                            ['test_local_decoupling']),
 'C10_unknown_aggregates_to_info': ([('src/agent_ee/decoupling_loops.py',
                                      "                         'unknown' if any(r['outcome'] == 'unknown' "
                                      'for r in records + bank_records) else\n',
                                      '')],
                                    ['test_local_decoupling']),
 'C11_cap_plane_access_ignored': ([('src/agent_ee/decoupling_loops.py',
                                    '                        if access is None:\n'
                                    "                            row['reasons'].append(leg + ': capacitor "
                                    "has no native plane connection without the device pin')\n",
                                    '')],
                                  ['test_local_decoupling']),
 'C12_unfilled_zone_skipped': ([('src/agent_ee/decoupling_loops.py',
                                 "                    raise ValueError('declared net has an unfilled "
                                 "zone')\n",
                                 '                    continue\n')],
                               ['test_local_decoupling']),
 'C13_check_binding_skipped': ([('src/agent_ee/decoupling_loops.py',
                                 "    if (result.get('board_sha256') != "
                                 "ctx.manifest['files'][ctx.scope['board']]\n",
                                 '    if (False\n')],
                               ['test_local_decoupling']),
 'F1_no_fallthrough_after_local_failure': ([('src/agent_ee/placement_job.py',
                                             '                retain_routing_trial(trial, suffix)\n'
                                             '                continue\n'
                                             "            report.pop('reason', None)\n"
                                             "        with clock.phase('final_validation'):",
                                             '                retain_routing_trial(trial, suffix)\n'
                                             '                break\n'
                                             "            report.pop('reason', None)\n"
                                             "        with clock.phase('final_validation'):")],
                                           ['test_local_decoupling',
                                            'test_placement_job_guards.ControllerBoundaryTests']),
 'F2_seed_drc_errors_ignored': ([('src/agent_ee/placement_job.py',
                                  '            if errors:\n'
                                  "                error_items = {item['uuid'] for error in errors for item "
                                  "in error.get('items', []) if 'uuid' in item}\n",
                                  '            if False:\n'
                                  "                error_items = {item['uuid'] for error in errors for item "
                                  "in error.get('items', []) if 'uuid' in item}\n")],
                                ['test_local_decoupling',
                                 'test_placement_job_guards.ControllerBoundaryTests']),
 'F3_final_loop_outcome_ignored': ([('src/agent_ee/placement_job.py',
                                     '              or general_loops is not None and '
                                     "general_loops['outcome'] not in ('pass', 'info')\n",
                                     '')],
                                   ['test_local_decoupling',
                                    'test_placement_job_guards.ControllerBoundaryTests']),
 'F4_final_protected_copper_unverified': ([('src/agent_ee/placement_job.py',
                                            '            general_local_check = verify_local(board, '
                                            "routed['local_decoupling'])\n",
                                            "            general_local_check = {'status': 'pass'}\n")],
                                          ['test_local_decoupling',
                                           'test_placement_job_guards.ControllerBoundaryTests']),
 'F5_seed_refusal_escapes_candidate': ([('src/agent_ee/placement_job.py',
                                         '        except JobRefusal as exc:\n'
                                         "            write_json(local_dir / 'failure.json', {'reason': "
                                         'str(exc)})\n',
                                         '        except ZeroDivisionError as exc:\n'
                                         "            write_json(local_dir / 'failure.json', {'reason': "
                                         'str(exc)})\n')],
                                       ['test_local_decoupling',
                                        'test_placement_job_guards.ControllerBoundaryTests']),
 'F6_route_unlocked_policy': ([('src/agent_ee/placement_job.py',
                                '    owned_copper = planes is not None and not '
                                "list(pcbnew.LoadBoard(str(staged / 'board.kicad_pcb')).GetTracks())\n",
                                '    owned_copper = planes is not None\n')],
                              ['test_local_decoupling', 'test_placement_job_guards.ControllerBoundaryTests']),
 'R1_applies_without_refusal': ([('src/agent_ee/local_return_seed.py',
                                  '    _refuse_declared_loops(recipe)\n'
                                  "    model = recipe.get('model', {})\n"
                                  '    if any(',
                                  "    model = recipe.get('model', {})\n    if any(")],
                                ['test_local_return_seed']),
 'R2_has_loops_from_fields_again': ([('src/agent_ee/local_return_seed.py',
                                      '        has_loops = '
                                      "mechanics.known_revision(recipe['declaration']['sources']['analysis/geometry-model.json'])\n",
                                      "        has_loops = ('local_loops' in model or 'connections' in model "
                                      'or '
                                      "mechanics.known_revision(recipe['declaration']['sources']['analysis/geometry-model.json']))\n")],
                                    ['test_local_return_seed']),
 'B1_partition_ignores_shared_return': ([('src/agent_ee/local_decoupling.py',
                                          "terminals = {endpoint_key(relation[leg][0]) for leg in ('supply', "
                                          "'return')}",
                                          "terminals = {endpoint_key(relation['supply'][0])}")],
                                        ['test_local_decoupling']),
 'B2_partition_ignores_shared_supply': ([('src/agent_ee/local_decoupling.py',
                                          "terminals = {endpoint_key(relation[leg][0]) for leg in ('supply', "
                                          "'return')}",
                                          "terminals = {endpoint_key(relation['return'][0])}")],
                                        ['test_local_decoupling']),
 'B3_block_only_one_bank_capacitor': ([('src/agent_ee/decoupling_loops.py',
                                        'return [pad(board, endpoint) for endpoint in endpoints.values()]',
                                        'return [pad(board, endpoint) for endpoint in '
                                        'endpoints.values()][:1]')],
                                      ['test_local_decoupling']),
 'B4_bank_bypass_always_false': ([('src/agent_ee/decoupling_loops.py',
                                   'pin_bypasses_bank=graph.bypass(first, capacitors),',
                                   'pin_bypasses_bank=False,')],
                                 ['test_local_decoupling']),
 'X1_flag_ignores_reaches_plane': ([('src/agent_ee/decoupling_loops.py',
                                     "if not row['device_returns_without_plane'] else None)",
                                     'if True else None)')],
                                   ['test_local_decoupling']),
 'X2_banks_join_only_first_component': ([('src/agent_ee/local_decoupling.py',
                                          'joined = [group for group in components if terminals & group[1]]',
                                          'joined = [group for group in components if terminals & '
                                          'group[1]][:1]')],
                                        ['test_local_decoupling']),
 'X3_plane_access_blocks_only_own_device': ([('src/agent_ee/decoupling_loops.py',
                                              'access = graph.plane_access(second, '
                                              "board.GetLayerID(plane['layer']), devices)",
                                              'access = graph.plane_access(second, '
                                              "board.GetLayerID(plane['layer']), first)")],
                                            ['test_local_decoupling']),
 'X4_escape_tracks_join_bank_group': ([('src/agent_ee/local_decoupling.py',
                                        "                if leg['kind'] in ('supply', 'return'):\n"
                                        "                    groups[uid].add(('bank-leg', "
                                        "relation_banks[relation['id']], leg['kind'], "
                                        'tracks[uid].GetNetname()))\n',
                                        '                if True:\n'
                                        "                    groups[uid].add(('bank-leg', "
                                        "relation_banks[relation['id']], 'return' if leg['kind'] == "
                                        "'plane_tie' else leg['kind'], tracks[uid].GetNetname()))\n")],
                                      ['test_local_decoupling']),
 'X5_bank_grades_first_device_only': ([('src/agent_ee/decoupling_loops.py',
                                        'endpoints = {endpoint_key(r[leg][0]): r[leg][0] for r in relations}',
                                        'endpoints = {endpoint_key(r[leg][0]): r[leg][0] for r in '
                                        'relations[:1]}')],
                                      ['test_local_decoupling']),
 'X6_topology_refusal_disabled': ([('src/agent_ee/local_decoupling.py',
                                    '            if key in entries:\n',
                                    '            if False:\n')],
                                  ['test_local_decoupling']),
 'X7_tie_ignores_device_direction': ([('src/agent_ee/local_decoupling.py',
                                       'away_from=position(first), reusable=reusable)',
                                       'away_from=None, reusable=reusable)')],
                                     ['test_local_decoupling']),
 'X8_bank_leg_group_ignores_bank': ([('src/agent_ee/local_decoupling.py',
                                      "groups[uid].add(('bank-leg', relation_banks[relation['id']], "
                                      "leg['kind'], tracks[uid].GetNetname()))",
                                      "groups[uid].add(('bank-leg', leg['kind'], "
                                      'tracks[uid].GetNetname()))')],
                                    ['test_local_decoupling']),
 'Y1_same_net_uses_different_net_clearance': ([('src/agent_ee/local_decoupling.py',
                                                '            track_margin = max(0, margin - _rules(board, '
                                                "item)['clearance'])\n",
                                                '            track_margin = margin\n')],
                                              ['test_local_decoupling']),
 'Y2_escape_omits_grid_windows': ([('src/agent_ee/local_decoupling.py',
                                    '    grid = [(x, y) for x in range(-20, 21) for y in range(-20, 21) if 0 '
                                    '< x*x + y*y <= 400]\n',
                                    '    grid = []\n')],
                                  ['test_local_decoupling'])}

def run(name):
    edits, tests = MUTANTS[name]
    work = WORK / name
    work.mkdir(parents=True)
    for child in ROOT.iterdir():
        if child.name in ('src', 'review-data'):
            continue
        (work / child.name).symlink_to(child)
    shutil.copytree(ROOT / 'src', work / 'src')
    for rel, old, new in edits:
        path = work / rel
        text = path.read_text()
        if text.count(old) != 1:
            return dict(name=name, status='mutation-did-not-apply', file=rel, count=text.count(old))
        path.write_text(text.replace(old, new))
    env = dict(os.environ, PYTHONPATH=f'{work}:{work}/src:{work}/tests', PYTHONDONTWRITEBYTECODE='1')
    start = time.time()
    done = subprocess.run(['/usr/bin/python3', '-B', '-m', 'unittest', *tests], cwd=work, env=env,
                          capture_output=True, text=True, timeout=1500)
    log = done.stdout + done.stderr
    (OUT / (name + '.log')).write_text(log)
    tail = [line for line in log.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED'))]
    failing = sorted({line.split(':', 1)[1].strip() for line in log.splitlines() if line.startswith(('FAIL:', 'ERROR:'))})
    return dict(name=name, tests=tests, exit_code=done.returncode, seconds=round(time.time() - start, 1),
                status=('KILLED' if done.returncode else 'SURVIVED') if name != 'baseline' else
                ('GREEN' if done.returncode == 0 else 'BASELINE-RED'), summary=tail, failing=failing)


class Mutations(unittest.TestCase):
    def test_mutants(self):
        OUT.mkdir(parents=True, exist_ok=True)
        if WORK.exists():
            shutil.rmtree(WORK)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(run, MUTANTS))
        (OUT / 'results.json').write_text(json.dumps(results, indent=1))
        self.assertEqual(next(r for r in results if r['name'] == 'baseline')['status'], 'GREEN')
        equivalent = {'G5_via_may_sit_in_own_land', 'C7_islands_merged',
                      'F6_route_unlocked_policy', 'R2_has_loops_from_fields_again'}
        for row in results:
            if row['name'] != 'baseline':
                allowed = ('KILLED', 'SURVIVED') if row['name'] in equivalent else ('KILLED',)
                self.assertIn(row['status'], allowed, row)
