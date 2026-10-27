"""Networkless native rule runner. Invoked only through the existing executor."""
import json
import math
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from . import native_run
from .storage import digest, identity, write_json

# Leading underscore cannot alias the compiler's agent_ee_ constraint namespace.
LOAD_WITNESS = "_agent_ee_tool_load_witness"


def node(position, layer):
    return (int(position.x), int(position.y), int(layer))


def _path_coverage(board, net):
    """Prove one unbranched path with distinct endpoints inside native pad copper."""
    import pcbnew
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == net]
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net]
    if len(pads) != 2 or not tracks:
        return {'complete': False, 'reason': 'requires exactly two pads and saved routed copper'}
    copper = set(board.GetEnabledLayers().CuStack())
    graphics = [*board.GetDrawings(),
                *(g for fp in board.GetFootprints() for g in fp.GraphicalItems())]
    if any(board.GetLayerName(g.GetLayer()).endswith('.Cu') for g in graphics):
        return {'complete': False, 'reason': 'copper graphics outside routed primitive inventory'}
    adjacency, edges = defaultdict(list), []
    vias = [t for t in tracks if t.GetClass() == 'PCB_VIA']
    junction_contacts = []

    def track_node(track, position):
        layer = track.GetLayer()
        touching = [v for v in vias if v.IsOnLayer(layer) and v.FlashLayer(layer)
                    and v.GetEffectiveShape(layer).Collide(position, 0)]
        if len(touching) > 1:
            raise ValueError('ambiguous native via endpoint contact')
        if not touching:
            return node(position, layer)
        via = touching[0]
        offset = [position.x - via.GetPosition().x, position.y - via.GetPosition().y]
        junction_contacts.append({'via_id': via.m_Uuid.AsString(),
            'track_id': track.m_Uuid.AsString(), 'layer': board.GetLayerName(layer),
            'position_nm': [position.x, position.y], 'offset_nm': offset,
            'offset_distance_nm': math.hypot(*offset)})
        return node(via.GetPosition(), layer)

    for t in tracks:
        cls = t.GetClass()
        if t.GetWidth() <= 0:
            return {'complete': False, 'reason': 'nonpositive copper width unsupported'}
        if cls == 'PCB_VIA':
            if t.GetViaType() != pcbnew.VIATYPE_THROUGH:
                kind = 'micro' if t.GetViaType() == pcbnew.VIATYPE_MICROVIA else 'blind/buried'
                return {'complete': False, 'reason': kind + ' via path unsupported'}
            position = t.GetPosition()
            # Only incident layers bound the measurable barrel path. Unused
            # through-via layers are neither extra graph branches nor shortcuts.
            incident = {other.GetLayer() for other in tracks if other.GetClass() == 'PCB_TRACK' and
                        any(t.GetEffectiveShape(other.GetLayer()).Collide(endpoint, 0)
                            for endpoint in (other.GetStart(), other.GetEnd()))}
            incident.update(layer for p in pads for layer in copper & set(p.GetLayerSet().Seq())
                            if p.GetEffectiveShape(layer).Collide(position, 0))
            if (len(incident) != 2 or not incident <= copper or
                    any(not t.IsOnLayer(layer) or not t.FlashLayer(layer) for layer in incident)):
                return {'complete': False, 'reason': 'through via needs two distinct incident copper layers'}
            a, b = (node(position, layer) for layer in sorted(incident))
        elif cls == 'PCB_TRACK':
            if t.GetLayer() not in copper:
                return {'complete': False, 'reason': 'track is outside enabled copper layers'}
            try:
                a, b = track_node(t, t.GetStart()), track_node(t, t.GetEnd())
            except ValueError as exc:
                return {'complete': False, 'reason': str(exc)}
        else:
            return {'complete': False, 'reason': f'unsupported copper primitive {cls}'}
        index = len(edges)
        edges.append(t.m_Uuid.AsString())
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))
    terminals, contacts = [], []
    for p in pads:
        layers = [l for l in p.GetLayerSet().Seq() if board.GetLayerName(l).endswith('.Cu')]
        if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or len(layers) != 1:
            return {'complete': False, 'reason': 'only single-copper-layer SMD terminal pads qualified'}
        layer = layers[0]
        shape = p.GetEffectiveShape(layer)
        contained = [n for n in adjacency if n[2] == layer and
                     shape.Collide(pcbnew.VECTOR2I(n[0], n[1]), 0)]
        endpoints = [n for n in contained if len(adjacency[n]) == 1]
        if len(endpoints) != 1:
            return {'complete': False, 'reason': 'missing or ambiguous native pad endpoint'}
        # Several vertices are permitted only for one contiguous entry chain.
        # Endpoint containment proves segment containment only for convex pads;
        # custom/concave native shapes keep their previous single-vertex limit.
        if len(contained) > 1 and p.GetShape() not in (
                pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_CIRCLE,
                pcbnew.PAD_SHAPE_OVAL, pcbnew.PAD_SHAPE_ROUNDRECT):
            return {'complete': False, 'reason': 'in-pad entry shape containment unsupported'}
        inside = set(contained)
        visited, pending, internal = set(), [endpoints[0]], set()
        exits = 0
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for neighbor, index in adjacency[current]:
                if neighbor in inside:
                    internal.add(index)
                    pending.append(neighbor)
                else:
                    exits += 1
        if visited != inside or exits != 1:
            return {'complete': False, 'reason': 'ambiguous or re-entering native pad contact'}
        terminals.append(endpoints[0])
        internal_ids = {edges[index] for index in internal}
        internal_tracks = [t for t in tracks if t.m_Uuid.AsString() in internal_ids]
        if any(t.GetClass() != 'PCB_TRACK' or not all(shape.Collide(point, 0)
               for point in (t.GetStart(), t.GetEnd())) for t in internal_tracks):
            return {'complete': False, 'reason': 'in-pad entry containment unsupported'}
        contacts.append({
            'pad_id': p.m_Uuid.AsString(), 'layer': board.GetLayerName(layer),
            'terminal_nm': list(endpoints[0][:2]),
            'vertices': [
                {'position_nm': list(n[:2]),
                 'offset_nm': [n[0] - p.GetPosition().x, n[1] - p.GetPosition().y],
                 'offset_distance_nm': math.hypot(n[0] - p.GetPosition().x,
                                                  n[1] - p.GetPosition().y),
                 'copper_ids': sorted(edges[index] for _, index in adjacency[n])}
                for n in sorted(contained)],
            'fully_in_pad_copper_ids': sorted(internal_ids),
            'fully_in_pad_track_length_nm': sum(t.GetLength() for t in internal_tracks),
        })
    start, end = terminals
    if start == end or any(len(adjacency.get(t, [])) != 1 for t in terminals):
        return {'complete': False, 'reason': 'missing unique pad endpoint or ambiguous terminals'}
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
    reason = physical_contacts(board, tracks, pads)
    if reason:
        return {'complete': False, 'reason': reason}
    if not board.BuildConnectivity():
        return {'complete': False, 'reason': 'native connectivity build failed'}
    connected_ids = {item.m_Uuid.AsString() for item in
                     board.GetConnectivity().GetConnectedItems(pads[0])}
    if not {item.m_Uuid.AsString() for item in [*pads, *tracks]} <= connected_ids:
        return {'complete': False, 'reason': 'native net connectivity is disconnected'}
    return {'complete': True, 'pad_ids': sorted(p.m_Uuid.AsString() for p in pads),
            'copper_ids': sorted(edges), 'pad_contacts': contacts,
            'junction_contacts': junction_contacts,
            'native_connected_ids': sorted(connected_ids),
            'in_pad_length_convention': 'include all saved track copper; no pad-centre extension'}


def route_coverage(board, net, zone_freshness=None):
    """Native connectivity and unique numeric-path qualification are distinct."""
    import pcbnew
    zones = [z for z in board.Zones() if not z.GetIsRuleArea()]
    base = {'net': net, 'complete': False, 'measurement_qualified': False}
    conductor_ids = [item.m_Uuid.AsString() for item in
                     [*board.GetTracks(), *zones, *(p for fp in board.GetFootprints() for p in fp.Pads())]]
    if len(conductor_ids) != len(set(conductor_ids)):
        return {**base, 'reason': 'duplicate native copper identity'}
    if ((zones and (zone_freshness is None or zone_freshness.get('status') != 'fresh')) or
            (zone_freshness is not None and zone_freshness.get('status') not in ('fresh', 'not_required'))):
        return {**base, 'reason': 'zone fill ' + (zone_freshness or {}).get('status', 'unverified')}
    for item in board.GetTracks():
        if item.GetNetname() != net:
            continue
        if item.GetClass() not in ('PCB_TRACK', 'PCB_VIA'):
            return {**base, 'reason': 'unsupported copper primitive ' + item.GetClass()}
        if item.GetClass() == 'PCB_VIA' and item.GetViaType() != pcbnew.VIATYPE_THROUGH:
            kind = 'micro' if item.GetViaType() == pcbnew.VIATYPE_MICROVIA else 'blind/buried'
            return {**base, 'reason': kind + ' via connectivity unsupported'}
    path = _path_coverage(board, net)
    selected_zones = [z for z in zones if z.GetNetname() == net]
    if not selected_zones:
        return {**base, **path, 'measurement_qualified': path['complete']}
    # No polygon geometry or thermal-spoke inference: KiCad's connectivity
    # traversal distinguishes separate islands belonging to the same zone.
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == net]
    tracks = [t for t in board.GetTracks() if t.GetNetname() == net]
    if len(pads) < 2:
        return {**base, 'reason': 'plane connectivity requires at least two native pads'}
    if not board.BuildConnectivity():
        return {**base, 'reason': 'native connectivity build failed'}
    connected = board.GetConnectivity().GetConnectedItems(pads[0])
    connected_ids = {item.m_Uuid.AsString() for item in connected}
    required = {item.m_Uuid.AsString() for item in [*pads, *tracks]}
    complete = required <= connected_ids
    return {**base, 'complete': complete,
            'reason': 'zone-assisted net has no qualified unique numeric path' if complete else
                      'native net connectivity is disconnected',
            'native_connected_ids': sorted(connected_ids), 'required_ids': sorted(required),
            'zone_ids': sorted(z.m_Uuid.AsString() for z in selected_zones)}


def physical_contacts(board, tracks, pads):
    """Qualify graph incidence against native finite-width copper shapes.

    Adjacent track corners and track/via endpoints are expected junctions.
    Nonincident contact (including with other nets) invalidates this path proof.
    Arcs/zones are refused before this function: their junction geometry has
    not been qualified. Pad geometry comes from KiCad, not a bounding box.
    """
    import pcbnew as k
    selected = {t.m_Uuid.AsString() for t in tracks}
    # KiCad uses strict clearance comparison: one IU also refuses exact tangency.
    contact_guard = 1

    def layers(item):
        if item.GetClass() == 'PCB_VIA':
            return {layer for layer in board.GetEnabledLayers().CuStack() if item.IsOnLayer(layer)}
        return {item.GetLayer()}

    def endpoints(item, layer):
        if item.GetClass() == 'PCB_VIA':
            return {node(item.GetPosition(), layer)}
        return {node(item.GetStart(), layer), node(item.GetEnd(), layer)}

    inventory = list(board.GetTracks())
    for i, first in enumerate(inventory):
        for second in inventory[i+1:]:
            if not selected.intersection((first.m_Uuid.AsString(), second.m_Uuid.AsString())):
                continue
            for layer in layers(first) & layers(second):
                if not first.GetEffectiveShape(layer).Collide(second.GetEffectiveShape(layer), contact_guard):
                    continue
                common = endpoints(first, layer) & endpoints(second, layer)
                via_contact = False
                if {first.GetClass(), second.GetClass()} == {'PCB_TRACK', 'PCB_VIA'}:
                    via, track = ((first, second) if first.GetClass() == 'PCB_VIA'
                                  else (second, first))
                    via_contact = via.FlashLayer(layer) and any(
                        via.GetEffectiveShape(layer).Collide(point, 0)
                        for point in (track.GetStart(), track.GetEnd()))
                if (not common and not via_contact) or first.GetNetCode() != second.GetNetCode():
                    return 'nonincident copper contact or interior crossing/overlap'
                if first.GetClass() == second.GetClass() == 'PCB_TRACK':
                    origin = next(iter(common))
                    a = next((p for p in endpoints(first, layer) if p != origin), origin)
                    b = next((p for p in endpoints(second, layer) if p != origin), origin)
                    ax, ay = a[0]-origin[0], a[1]-origin[1]
                    bx, by = b[0]-origin[0], b[1]-origin[1]
                    if ax*bx + ay*by > 0:
                        return 'backtracking or acute copper junction overlap unsupported'
    all_pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    terminals = {p.m_Uuid.AsString() for p in pads}
    for track in tracks:
        for pad in all_pads:
            for layer in layers(track) & set(pad.GetLayerSet().Seq()):
                if track.GetEffectiveShape(layer).Collide(pad.GetEffectiveShape(layer), contact_guard):
                    if (pad.m_Uuid.AsString() not in terminals or
                            not any(pad.GetEffectiveShape(layer).Collide(k.VECTOR2I(p[0], p[1]), 0)
                                    for p in endpoints(track, layer))):
                        return 'nonincident pad copper contact'
    for pad in pads:
        for other in all_pads:
            if pad.m_Uuid.AsString() == other.m_Uuid.AsString():
                continue
            for layer in set(pad.GetLayerSet().Seq()) & set(other.GetLayerSet().Seq()):
                if board.GetLayerName(layer).endswith('.Cu') and pad.GetEffectiveShape(layer).Collide(
                        other.GetEffectiveShape(layer), contact_guard):
                    return 'terminal pad copper overlaps another pad'
    return None


def _filled_zones(board):
    """Canonical exact native fill contours, including holes; no geometry inference."""
    from .routing_equivalence import canonical_ring
    result = {}
    for zone in board.Zones():
        if zone.GetIsRuleArea():
            continue
        uid = zone.m_Uuid.AsString()
        if uid in result:
            raise ValueError('duplicate native copper zone identity')
        layers = {}
        for layer in board.GetEnabledLayers().CuStack():
            if not zone.IsOnLayer(layer):
                continue
            poly = zone.GetFilledPolysList(layer)
            if poly.ArcCount():
                raise ValueError('curved filled zone contours unsupported')
            ring = lambda line: canonical_ring([(int(line.CPoint(j).x), int(line.CPoint(j).y))
                                                for j in range(line.PointCount())])
            layers[board.GetLayerName(layer)] = sorted(
                (ring(poly.Outline(i)), tuple(sorted(ring(poly.Hole(i, h))
                 for h in range(poly.HoleCount(i))))) for i in range(poly.OutlineCount()))
        result[uid] = {'net': zone.GetNetname(), 'layers': layers}
    return result


def qualify_zone_fills(path, board, work):
    """Compare saved copper with native refills on private captured-sibling copies."""
    import pcbnew
    zones = [z for z in board.Zones() if not z.GetIsRuleArea()]
    if any(not z.GetIsRuleArea() for fp in board.GetFootprints() for z in fp.Zones()):
        return {'status': 'unsupported', 'reason': 'footprint-owned copper zones unsupported'}
    if not zones:
        return {'status': 'not_required'}
    saved = _filled_zones(board)
    if any(not z.IsFilled() or not all(saved[z.m_Uuid.AsString()]['layers'].values()) for z in zones):
        return {'status': 'unfilled', 'saved': saved}
    if any(z.NeedRefill() for z in zones):
        return {'status': 'stale', 'reason': 'native NeedRefill flag', 'saved': saved}
    work = Path(work); work.mkdir()
    source = Path(path)
    siblings = [source, source.with_suffix('.kicad_pro'), source.with_suffix('.kicad_dru')]
    originals = {p.name: p.read_bytes() for p in siblings if p.is_file()}
    source_hashes = {p.name: digest(p) for p in siblings if p.is_file()}
    private = work / source.name
    for name, raw in originals.items():
        (work / name).write_bytes(raw)
    observations = []
    for _ in range(2):
        refreshed = pcbnew.LoadBoard(str(private))
        if not refreshed.BuildConnectivity() or not pcbnew.ZONE_FILLER(refreshed).Fill(refreshed.Zones()):
            raise ValueError('native zone refill failed')
        if not pcbnew.SaveBoard(str(private), refreshed):
            raise ValueError('native zone refill save failed')
        # Native save may rewrite project siblings. Each qualification uses the
        # exact captured settings/rules, never those incidental save products.
        for suffix in ('.kicad_pro', '.kicad_dru'):
            sibling = private.with_suffix(suffix)
            if sibling.name in originals:
                sibling.write_bytes(originals[sibling.name])
            else:
                sibling.unlink(missing_ok=True)
        observations.append(_filled_zones(pcbnew.LoadBoard(str(private))))
    if any(digest(p) != source_hashes[p.name] for p in siblings if p.name in source_hashes):
        raise ValueError('native zone qualification changed captured input')
    stable = observations[0] == observations[1]
    status = 'unstable' if not stable else ('fresh' if saved == observations[0] else 'stale')
    return {'status': status, 'source_hashes': source_hashes, 'saved': saved,
            'refilled': observations[0], 'repeat': observations[1],
            'saved_sha256': identity(saved), 'refilled_sha256': identity(observations[0]),
            'repeat_sha256': identity(observations[1])}


def probe_board(path, constraints, work=None):
    import pcbnew
    board = pcbnew.LoadBoard(str(path))
    freshness = qualify_zone_fills(path, board, work or Path(path).parent / 'zone-freshness')
    nets = sorted({net for c in constraints for net in c.get('nets', [])})
    probe = {'pcbnew_version': pcbnew.Version(), 'board_sha256': digest(path),
             'zone_freshness': freshness,
             'routes': {n: route_coverage(board, n, freshness) for n in nets}}
    if any(c.get('kind') == 'fab' for c in constraints):
        from .fab_profile import native_inventory
        probe['fabrication'] = native_inventory(path)
    return probe


def stage_board(source, directory, constraint):
    directory.mkdir()
    target = directory / 'board.kicad_pcb'
    shutil.copyfile(source, target)
    pro = source.with_suffix('.kicad_pro')
    project = json.loads(pro.read_text(encoding='utf-8')) if pro.is_file() else {}
    settings = project.setdefault('board', {}).setdefault('design_settings', {})
    settings['drc_exclusions'] = []
    # Only fabrication limits stage board minima; routing keeps captured settings.
    settings.setdefault('rules', {}).update(constraint.get('project_rules', {}))
    severities = settings.setdefault('rule_severities', {})
    for kind in [*constraint['native_types'], 'items_not_allowed']:
        severities[kind] = 'error'
    write_json(target.with_suffix('.kicad_pro'), project)
    original = source.with_suffix('.kicad_dru')
    text = original.read_text(encoding='utf-8') if original.is_file() else '(version 1)\n'
    # Keep captured rules first; generated rule is last and isolated by constraint.
    if constraint['marker'] in text or LOAD_WITNESS in text:
        raise ValueError('captured rules collide with generated rule identity')
    generated = constraint['rules'].split('\n', 1)[1]
    # This deliberate tool-only finding proves KiCad loaded the rules file.
    # KiCad can silently discard malformed custom rules with exit code zero.
    witness = f'(rule "{LOAD_WITNESS}" (constraint disallow track))\n'
    target.with_suffix('.kicad_dru').write_text(text + '\n' + generated + witness, encoding='utf-8')
    return target


def run_constraint(source, root, constraint, probe, directory=None):
    record = {k: constraint[k] for k in ('id', 'requirement', 'expected')}
    if 'applicability' in constraint:
        # A silk minimum's verified supplier scope, checked against the saved stackup.
        record['applicability'] = constraint['applicability']
    record.update(status='not_evaluated', execution='unsupported')
    if constraint['execution'] != 'ready':
        return {**record, 'reason': constraint['reason'],
                **({'code': constraint['code']} if 'code' in constraint else {})}
    if 'geometry' in constraint:
        from .fab_profile import evaluate_geometry
        return evaluate_geometry(constraint, probe['fabrication'])
    if 'terms' in constraint:
        (root / constraint['id']).mkdir()
        terms = [run_constraint(source, root, term, probe, root / constraint['id'] / term['expected']['key'])
                 for term in constraint['terms']]
        from .fab_profile import evaluate_conjunctive
        return evaluate_conjunctive(record, terms)
    directory = directory or root / constraint['id']
    target = stage_board(source, directory, constraint)
    record['rules_sha256'] = digest(target.with_suffix('.kicad_dru'))
    record['generated_rules_sha256'] = constraint['rules_sha256']
    record['project_sha256'] = digest(target.with_suffix('.kicad_pro'))
    record['board_sha256'] = digest(target)
    intent = native_run.drc(target, directory / 'drc.json', units='mm', timeout=600, isolation='host')
    argv = list(intent.argv)
    proc = native_run.completed(native_run.run(intent, work=None))
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
    if ignored.intersection([*constraint['native_types'], 'items_not_allowed']):
        return {**record, 'reason': 'required native check ignored'}
    console = (proc.stdout + proc.stderr).decode('utf-8', errors='replace')
    diagnostic = re.search(r'(?im)^(?:\d{2}:\d{2}:\d{2}:\s*)?(?:fatal\s+)?error\b', console)
    if diagnostic or any(v['type'] == 'rule_compile_error' for v in report['violations']):
        return {**record, 'execution': 'malformed', 'reason': 'native rule compilation or tool diagnostic'}
    loading = [v for v in report['violations']
               if f"rule '{LOAD_WITNESS}'" in v['description']
               and v['type'] == 'items_not_allowed' and v['severity'] == 'error']
    if not loading:
        return {**record, 'execution': 'malformed', 'reason': 'native rule loading witness missing'}
    record['tool_load_witness'] = loading
    if constraint.get('nets'):
        coverage = {n: probe['routes'][n] for n in constraint['nets']}
        record['route_coverage'] = coverage
        if not all(c['complete'] and c['measurement_qualified'] for c in coverage.values()):
            return {**record, 'reason': 'missing qualified unique pad-to-pad measurement path'}
    load_witness = [v for v in report['violations'] if f"rule '{LOAD_WITNESS}'" in v['description']]
    record['tool_load_witness'] = load_witness
    if not load_witness or any(v['severity'] != 'error' for v in load_witness):
        return {**record, 'execution': 'malformed', 'reason': 'native custom-rule loading not established'}
    marker = f"rule '{constraint['marker']}'"
    failures = [v for v in report['violations'] if marker in v['description']]
    if any(v['type'] not in constraint['native_types'] for v in failures):
        return {**record, 'execution': 'malformed', 'reason': 'generated rule finding type mismatch'}
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
    probe = probe_board(source, constraints, work=root / 'zone-freshness')
    write_json(root / 'routing-probe.json', probe)
    native = root / 'native-rules'
    native.mkdir()
    records = [run_constraint(source, native, c, probe) for c in constraints]
    if any(c.get('kind') == 'fab' for c in constraints):
        from .fab_profile import classify_preferred
        # The inventory gates edge clearance: no qualified outline proves nothing.
        records = classify_preferred(records, probe['fabrication'])
    write_json(root / 'rule-results.json', {'schema': 'agent-ee.native-rules-result/1',
                                           'constraints': records})
    return 0


if __name__ == '__main__':
    sys.exit(main())
