"""Captured plane-plan producer; only planned top-level zone bytes may change."""
import argparse
import hashlib
import math
from pathlib import Path
import shutil
import uuid

from . import benchmark, stage_receipts
from .catalog import strict_json
from .executor import execute
from .tool_source import snapshot
from .design_rule_provenance import advisory_numeric, classify_finding, finding_sources, require_evaluated_limits
from .storage import digest, identity, read_json, write_json

SCHEMA = 'agent-ee.plane-plan/1'
RESULT = 'agent-ee.add-planes/1'
NAMESPACE = uuid.UUID('ac798940-3e63-57eb-9fe4-72405f8101df')
SIBLINGS = ('.kicad_pro', '.kicad_prl', '.kicad_dru')


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite and within [{low}, {high}]')
    return value


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'schema', 'zones'} or plan['schema'] != SCHEMA:
        raise ValueError('expected agent-ee.plane-plan/1 with zones')
    if not isinstance(plan['zones'], list) or not 1 <= len(plan['zones']) <= 64:
        raise ValueError('plane plan requires 1..64 zones')
    for zone in plan['zones']:
        required = {'net', 'layer', 'outline', 'clearance_mm', 'min_width_mm', 'thermal', 'priority', 'island_removal'}
        if not isinstance(zone, dict) or set(zone) != required:
            raise ValueError('plane zone has unknown or missing fields')
        if not isinstance(zone['net'], str) or not zone['net'].strip() or '\x00' in zone['net']:
            raise ValueError('plane net must be an explicit nonempty name')
        if not isinstance(zone['layer'], str) or zone['layer'] not in {'F.Cu', 'B.Cu', *(f'In{i}.Cu' for i in range(1, 31))}:
            raise ValueError('plane layer must name one copper layer')
        _number(zone['clearance_mm'], 'clearance_mm', .001, 100)
        _number(zone['min_width_mm'], 'min_width_mm', .001, 100)
        if isinstance(zone['priority'], bool) or not isinstance(zone['priority'], int) or not 0 <= zone['priority'] <= 100:
            raise ValueError('plane priority must be an integer in [0, 100]')
        if zone['island_removal'] not in ('always', 'never'):
            raise ValueError('island_removal must explicitly be always or never')
        thermal = zone['thermal']
        if not isinstance(thermal, dict) or set(thermal) != {'connection', 'gap_mm', 'spoke_width_mm'}:
            raise ValueError('thermal requires connection, gap_mm and spoke_width_mm')
        if thermal['connection'] not in ('solid', 'thermal', 'tht_thermal'):
            raise ValueError('unsupported plane pad connection')
        _number(thermal['gap_mm'], 'thermal gap_mm', .001, 100)
        _number(thermal['spoke_width_mm'], 'thermal spoke_width_mm', .001, 100)
        outline = zone['outline']
        if outline != 'board':
            if not isinstance(outline, dict) or set(outline) != {'polygon_mm'}:
                raise ValueError('outline must be board or an explicit polygon_mm')
            points = outline['polygon_mm']
            if not isinstance(points, list) or not 3 <= len(points) <= 4096:
                raise ValueError('polygon needs 3..4096 vertices')
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError('polygon vertices must be [x_mm, y_mm]')
                for value in point:
                    _number(value, 'polygon coordinate', -10000, 10000)
            if len({tuple(p) for p in points}) != len(points):
                raise ValueError('polygon vertices must be distinct; closure is implicit')
    return plan


def zone_ids(plan):
    return [str(uuid.uuid5(NAMESPACE, f'{identity(plan)}/{i}')) for i in range(len(plan['zones']))]


# AGENTEE-487 phase interface (stable): the routing-turn add-planes step declares phase 'pre-routing'
# (argv --phase pre-routing; report field 'phase'). Only then may a wholly unconnected planned 'always' zone be
# DEFERRED; it is listed in report['deferred_connectivity'] as {zone, net, layer, island_removal: 'always',
# obligation}. Without the phase input (any other caller, any post-routing use) the zone is refused as before.
PRE_ROUTING = 'pre-routing'


def step(source_root, *, board, plan, phase=None):
    for path in (board, plan):
        stage_receipts._relative(path, 'plane input')
    return stage_receipts.validate_step(dict(schema='agent-ee.stage-step/1', step='add-planes', stage='routing',
        argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P', '-m',
              'agent_ee.add_planes', '--board', board, '--plan', plan, *_phase_argv(phase)],
        mounts=[dict(source=str(snapshot(source_root)), at='/tool/agent-ee')], source='/tool/agent-ee',
        version_argv=['/usr/bin/kicad-cli', 'version'], writes={board:'out/board.kicad_pcb'},
        artifacts=['out'], adoptable_exits=[0], timeout_s=600))


def _phase_argv(phase):
    if phase is None:
        return []
    if phase != PRE_ROUTING:
        raise ValueError(f'unsupported plane phase {phase!r}')
    return ['--phase', PRE_ROUTING]


def splice_zones(original, generated, planned):
    """Use existing span parser: retain every byte outside the named zone expressions."""
    before, after = benchmark.parse(original), benchmark.parse(generated)
    if benchmark.tag(before) != 'kicad_pcb' or benchmark.tag(after) != 'kicad_pcb':
        raise ValueError('expected native KiCad boards')
    replacements = {}
    for node in benchmark.children(after, 'zone'):
        uid = benchmark.uid(node)
        if uid in planned:
            if uid in replacements:
                raise ValueError('duplicate planned zone UUID')
            replacements[uid] = generated[node.start:node.end]
    if set(replacements) != set(planned):
        raise ValueError('native producer did not save the exact planned zone inventory')
    edits = []
    for node in benchmark.children(before, 'zone'):
        uid = benchmark.uid(node)
        if uid in replacements:
            edits.append((node.start, node.end, replacements.pop(uid)))
    # No whitespace outside zone spans is added or normalized.
    if replacements:
        edits.append((before.end - 1, before.end - 1, ''.join(replacements[u] for u in planned if u in replacements)))
    return benchmark.replace_spans(original, edits)


def _child(inputs, work, argv, timeout=180):
    record = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.add_planes_probe', *argv], timeout=timeout)
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        raise ValueError(f'native plane child {record["execution"]} exit {record["exit_code"]}: ' +
                         (work/'console.log').read_text(errors='replace')[-2000:])
    return record


def _verify_inputs(candidate, board, plan, generated, out):
    root = out/'verify-input'
    for rel in [board, plan, *(str(Path(board).with_suffix(s)) for s in SIBLINGS)]:
        source = candidate/rel
        if not source.exists():
            continue
        target = root/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (root/board).write_text(generated, encoding='utf-8')
    return root


def _planned_zone_errors(checks, planned, always_ids=(), sources=None):
    """Classify only complete, attributed native records; unknown is a refusal."""
    if not isinstance(checks, dict):
        raise ValueError('native plane DRC report is malformed')
    severities = checks.get('included_severities')
    if (not isinstance(severities, list) or
            any(s not in ('error', 'warning', 'exclusion') for s in severities) or
            set(severities) != {'error', 'warning', 'exclusion'}):
        raise ValueError('native plane DRC report lacks complete severity coverage')
    violations = checks.get('violations')
    if not isinstance(violations, list):
        raise ValueError('native plane DRC report is malformed')
    errors = []
    for violation in violations:
        if (not isinstance(violation, dict) or
                violation.get('severity') not in ('error', 'warning', 'exclusion') or
                not isinstance(violation.get('type'), str) or not violation['type'].strip() or
                not isinstance(violation.get('items'), list)):
            raise ValueError('native plane DRC violation metadata is malformed')
        items = violation['items']
        isolated_always = violation['type'] == 'isolated_copper' and bool(always_ids)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('uuid'), str):
                raise ValueError('native plane DRC item attribution is malformed')
            try:
                valid = str(uuid.UUID(item['uuid'])) == item['uuid']
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('native plane DRC item attribution is malformed')
        classified = classify_finding(dict(violation), sources)
        hard = classified['constraint_provenance']['enforcement'] == 'hard'
        if (violation['severity'] == 'error' or hard or isolated_always) and not items:
            raise ValueError('native plane DRC violation has no item attribution')
        if (((violation['severity'] == 'error' or hard) and any(item['uuid'] in planned for item in items)) or
                (isolated_always and any(item['uuid'] in always_ids for item in items))):
            if not advisory_numeric(classified):
                errors.append(violation)
    return errors


def wholly_unconnected(zone_errors, zones, always_ids, islands=None, phase=None):
    """Planned 'always' zones that may be DEFERRED (AGENTEE-487): only in phase 'pre-routing', and only when the
    native fill marks EVERY polygon of the zone an island AND the isolated_copper findings attribute one-to-one to
    those polygons (distinct positions, count equal to the saved polygon count). Duplicate or unknown evidence
    fails closed (ValueError); any other mismatch is simply not deferred, so the refusal stands."""
    if phase != PRE_ROUTING:
        return set()
    positions = {}
    for violation in zone_errors:
        if violation['type'] != 'isolated_copper':
            continue
        for item in violation['items']:
            if item['uuid'] in always_ids:
                pos = item.get('pos')
                if not (isinstance(pos, dict) and isinstance(pos.get('x'), (int, float)) and isinstance(pos.get('y'), (int, float))):
                    raise ValueError('isolated-copper attribution has no position')
                key = (pos['x'], pos['y'])
                seen = positions.setdefault(item['uuid'], [])
                if key in seen:
                    raise ValueError('duplicate isolated-copper attribution for planned zone ' + item['uuid'])
                seen.append(key)
    saved = {z.get('uuid'): z.get('fill_polygons') for z in zones}
    deferred = set()
    for uid, seen in positions.items():
        native = (islands or {}).get(uid)
        if native is None:
            raise ValueError('no native island record for planned zone ' + uid)
        count = saved.get(uid)
        if (isinstance(count, int) and count > 0 and native.get('polygons') == count and
                len(native.get('islands', [])) == count and all(native['islands']) and len(seen) == count):
            deferred.add(uid)
    return deferred


def generate(candidate, board, plan_path, out, *, phase=None):
    candidate, out = Path(candidate).resolve(), Path(out).absolute()
    for path in (board, plan_path):
        stage_receipts._relative(path, 'plane input')
    plan = validate_plan(strict_json((candidate/plan_path).read_bytes()))
    original = (candidate/board).read_bytes()
    if out.exists() or out.is_symlink() or candidate in out.resolve().parents:
        raise ValueError('plane output must be new and outside captured inputs')
    out.mkdir(parents=True)
    report = dict(schema=RESULT, execution='failed', outcome='unknown', plan_sha256=digest(candidate/plan_path),
                  board_before=hashlib.sha256(original).hexdigest(), planned_zone_ids=zone_ids(plan), findings=[],
                  phase=phase, deferred_connectivity=[])
    try:
        _child(candidate, out/'native', ['apply', '/candidate/'+board, '/candidate/'+plan_path, '/work/board.kicad_pcb'])
        raw = (out/'native/board.kicad_pcb').read_text(encoding='utf-8')
        proposed = splice_zones(original.decode('utf-8'), raw, zone_ids(plan))
        verified = _verify_inputs(candidate, board, plan_path, proposed, out)
        _child(verified, out/'verify', ['inspect', '/candidate/'+board, '/candidate/'+plan_path, '/work/facts.json'])
        facts = read_json(out/'verify/facts.json')
        drc = execute(verified, out/'drc', ['/usr/bin/kicad-cli', 'pcb', 'drc', '--format', 'json',
                      '--severity-all', '-o', '/work/report.json', '/candidate/'+board], timeout=180)
        if drc['execution'] != 'ok' or drc['exit_code'] != 0:
            raise ValueError('native plane DRC execution failed')
        checks = read_json(out/'drc/report.json')
        from .outline_containment import sandboxed
        containment = sandboxed(verified, board, out/'containment')
        planned = zone_ids(plan)
        always_ids = {uid for uid, spec in zip(planned, plan['zones']) if spec['island_removal']=='always'}
        _planned_zone_errors(checks, planned, always_ids)
        native_count = len(checks['violations'])
        checks['violations'].extend(containment)
        sources = finding_sources(verified/board, checks['violations'])
        require_evaluated_limits(checks, sources)
        zone_errors = _planned_zone_errors(checks, planned, always_ids, sources)
        # AGENTEE-487: before routing an inner-layer zone can have no connection yet. A planned 'always' zone whose
        # EVERY filled polygon is reported isolated is wholly unconnected: its definition is kept with the final
        # 'always' policy and its connectivity becomes an explicit post-routing obligation, not a refusal. Partial
        # isolation (some fill connected) and every other planned-zone error still refuse.
        islands = read_json(out/'native/islands.json') if phase == PRE_ROUTING else None
        deferred = wholly_unconnected(zone_errors, facts['zones'], always_ids, islands, phase)
        zone_errors = [v for v in zone_errors if not (v['type'] == 'isolated_copper' and
                                                      {i['uuid'] for i in v['items']} & deferred)]
        classified = [classify_finding(dict(v), sources) for v in checks['violations']]
        write_json(out/'drc/constraints.json', {'sources': sources, 'violations': classified})
        report.update(pcbnew_version=facts['pcbnew_version'], zones=facts['zones'],
                      drc=dict(violations=native_count, planned_zone_errors=zone_errors,
                               classified_findings=classified,
                               report_sha256=digest(out/'drc/report.json')))
        if containment:
            raise ValueError('saved copper outside board outline')
        if zone_errors:
            refused_ids = sorted({item['uuid'] for violation in zone_errors for item in violation['items']
                                  if item['uuid'] in planned})
            raise ValueError('native DRC found an error involving planned zones: ' + ', '.join(refused_ids))
        if (candidate/board).read_bytes() != original or digest(candidate/plan_path) != report['plan_sha256']:
            raise ValueError('captured plane inputs changed during production')
        (out/'board.kicad_pcb').write_bytes(proposed.encode('utf-8'))
        report.update(execution='ok', outcome='info', board_after=digest(out/'board.kicad_pcb'),
                      deferred_connectivity=[dict(zone=z['uuid'], net=z['net'], layer=z['layer'], island_removal='always',
                                                  obligation='connected fill under island_removal always after routing')
                                             for z in facts['zones'] if z['uuid'] in deferred],
                      limitation='Plane production is not electrical acceptance; full post-routing DRC and pour integrity remain required.')
    except (OSError, ValueError) as exc:
        report['findings'].append(dict(code='plane_production_refused', message=str(exc)))
    write_json(out/'planes.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--phase', choices=(PRE_ROUTING,), default=None,
                        help='declared by the routing-turn step only; enables pre-routing deferral')
    args = parser.parse_args(argv)
    report = generate('/candidate', args.board, args.plan, '/work/out', phase=args.phase)
    return 0 if report['execution'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
