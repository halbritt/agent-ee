"""Networkless native rule runner. Invoked only through the existing executor."""
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from .storage import digest, write_json


def node(position, layer):
    return (int(position.x), int(position.y), int(layer))


def route_coverage(board, net):
    """Prove one unbranched pad-centre path; unsupported topology is unknown."""
    import pcbnew
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == net]
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net]
    if len(pads) != 2 or not tracks:
        return {'complete': False, 'reason': 'requires exactly two pads and saved routed copper'}
    if any(z.GetNetname() == net for z in board.Zones()):
        return {'complete': False, 'reason': 'zone-connected route unsupported'}
    adjacency, edges = defaultdict(list), []
    for t in tracks:
        cls = t.GetClass()
        if cls == 'PCB_VIA':
            if board.GetCopperLayerCount() != 2 or t.GetViaType() != pcbnew.VIATYPE_THROUGH:
                return {'complete': False, 'reason': 'only two-layer through vias qualified'}
            a, b = node(t.GetPosition(), pcbnew.F_Cu), node(t.GetPosition(), pcbnew.B_Cu)
        elif cls in ('PCB_TRACK', 'PCB_ARC'):
            a, b = node(t.GetStart(), t.GetLayer()), node(t.GetEnd(), t.GetLayer())
        else:
            return {'complete': False, 'reason': f'unsupported copper primitive {cls}'}
        index = len(edges)
        edges.append(t.m_Uuid.AsString())
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))
    terminals = []
    for p in pads:
        layers = [l for l in p.GetLayerSet().Seq() if board.GetLayerName(l).endswith('.Cu')]
        if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or len(layers) != 1:
            return {'complete': False, 'reason': 'only single-copper-layer SMD terminal pads qualified'}
        terminals.append(node(p.GetPosition(), layers[0]))
    start, end = terminals
    if start == end or any(len(adjacency.get(t, [])) != 1 for t in terminals):
        return {'complete': False, 'reason': 'missing pad-centre endpoint or ambiguous terminals'}
    if any(len(neighbors) != (1 if n in terminals else 2) for n, neighbors in adjacency.items()):
        return {'complete': False, 'reason': 'branch or dangling copper; no unique complete route'}
    seen, pending, visited_edges = set(), [start], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for neighbor, index in adjacency[current]:
            visited_edges.add(index)
            pending.append(neighbor)
    if end not in seen or len(visited_edges) != len(edges):
        return {'complete': False, 'reason': 'disconnected route or isolated copper'}
    return {'complete': True, 'pad_ids': sorted(p.m_Uuid.AsString() for p in pads),
            'copper_ids': sorted(edges)}


def probe_board(path, constraints):
    import pcbnew
    board = pcbnew.LoadBoard(str(path))
    nets = sorted({net for c in constraints for net in c.get('nets', [])})
    return {'pcbnew_version': pcbnew.Version(), 'board_sha256': digest(path),
            'routes': {n: route_coverage(board, n) for n in nets}}


def stage_board(source, directory, constraint):
    directory.mkdir()
    target = directory / 'board.kicad_pcb'
    shutil.copyfile(source, target)
    pro = source.with_suffix('.kicad_pro')
    project = json.loads(pro.read_text(encoding='utf-8')) if pro.is_file() else {}
    settings = project.setdefault('board', {}).setdefault('design_settings', {})
    settings['drc_exclusions'] = []
    severities = settings.setdefault('rule_severities', {})
    for kind in constraint['native_types']:
        severities[kind] = 'error'
    write_json(target.with_suffix('.kicad_pro'), project)
    original = source.with_suffix('.kicad_dru')
    text = original.read_text(encoding='utf-8') if original.is_file() else '(version 1)\n'
    # Keep captured rules first; generated rule is last and isolated by constraint.
    if constraint['marker'] in text:
        raise ValueError('captured rules collide with generated rule identity')
    generated = constraint['rules'].split('\n', 1)[1]
    target.with_suffix('.kicad_dru').write_text(text + '\n' + generated, encoding='utf-8')
    return target


def run_constraint(source, root, constraint, probe):
    record = {k: constraint[k] for k in ('id', 'requirement', 'expected')}
    record.update(status='not_evaluated', execution='unsupported')
    if constraint['execution'] != 'ready':
        return {**record, 'reason': constraint['reason']}
    directory = root / constraint['id']
    target = stage_board(source, directory, constraint)
    record['rules_sha256'] = digest(target.with_suffix('.kicad_dru'))
    record['generated_rules_sha256'] = constraint['rules_sha256']
    record['project_sha256'] = digest(target.with_suffix('.kicad_pro'))
    record['board_sha256'] = digest(target)
    argv = ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--units', 'mm',
            '-o', str(directory / 'drc.json'), str(target)]
    proc = subprocess.run(argv, capture_output=True, timeout=600)
    (directory / 'stdout.log').write_bytes(proc.stdout)
    (directory / 'stderr.log').write_bytes(proc.stderr)
    write_json(directory / 'invocation.json', {'argv': argv, 'exit_code': proc.returncode})
    record['native_report'] = str((directory / 'drc.json').relative_to(root.parent))
    if proc.returncode != 0:
        return {**record, 'execution': 'failed', 'reason': 'native DRC failed'}
    if digest(target) != record['board_sha256'] or digest(source) != probe['board_sha256']:
        raise ValueError('native execution changed saved board bytes')
    report = json.loads((directory / 'drc.json').read_text(encoding='utf-8'))
    for key in ('violations', 'unconnected_items', 'ignored_checks'):
        if not isinstance(report.get(key), list):
            raise ValueError(f'DRC report missing {key}')
    if report.get('coordinate_units') != 'mm' or not report.get('kicad_version', '').startswith('10.'):
        return {**record, 'reason': 'only KiCad 10 mm reports qualified'}
    ignored = {c['key'] for c in report['ignored_checks']}
    if ignored.intersection(constraint['native_types']):
        return {**record, 'reason': 'required native check ignored'}
    console = (proc.stdout + proc.stderr).decode('utf-8', errors='replace')
    if 'error' in console.lower() or any(v['type'] == 'rule_compile_error' for v in report['violations']):
        return {**record, 'execution': 'malformed', 'reason': 'native rule compilation or tool diagnostic'}
    if constraint.get('nets'):
        coverage = {n: probe['routes'][n] for n in constraint['nets']}
        record['route_coverage'] = coverage
        if not all(c['complete'] for c in coverage.values()):
            return {**record, 'reason': 'missing complete pad-to-pad route coverage'}
    marker = f"rule '{constraint['marker']}'"
    failures = [v for v in report['violations'] if marker in v['description']]
    if any(v['severity'] != 'error' for v in failures):
        return {**record, 'reason': 'generated rule was excluded or severity overridden'}
    return {**record, 'execution': 'ok', 'status': 'violation' if failures else 'satisfied',
            'native_findings': failures, 'native_report_sha256': digest(directory / 'drc.json')}


def main(argv=None):
    request_path = Path((argv or sys.argv[1:])[0])
    request = json.loads(request_path.read_text(encoding='utf-8'))
    root = request_path.parent
    source = Path(request['board'])
    constraints = request['compiled']['constraints']
    probe = probe_board(source, constraints)
    write_json(root / 'routing-probe.json', probe)
    native = root / 'native-rules'
    native.mkdir()
    records = [run_constraint(source, native, c, probe) for c in constraints]
    write_json(root / 'rule-results.json', {'schema': 'agent-ee.native-rules-result/1',
                                           'constraints': records})
    return 0


if __name__ == '__main__':
    sys.exit(main())
