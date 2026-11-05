"""Typed final-board decoupling paths from native KiCad connectivity.

Lengths follow copper centrelines and pad-centre contact projections. Vias pay
the full board thickness. Plane hops follow one actual filled island, including
holes, and are never zero-cost net-name links. This is geometry, not inductance.
"""
import argparse
import json
import math
from pathlib import Path

from . import local_loop_geometry as geo
from .local_decoupling import (
    banks, endpoint_key, junction_in_pad, pad, position, validate,
    _separation_floor, _too_close, _separation_shapes, _gap_lower_bound
)
from .storage import digest, read_json, write_json


class CopperGraph:
    def __init__(self, board, net, roots=None):
        import pcbnew as k
        from .rf_geometry import _polyset
        self.board, self.k = board, k
        inventory = {i.m_Uuid.AsString(): i for i in
                      [p for fp in board.GetFootprints() for p in fp.Pads()] + list(board.GetTracks())
                      if i.GetNetname() == net}
        conn = board.GetConnectivity()
        floor = _separation_floor(board)
        sub_floor_pairs = []
        touching_all = {key: {p.m_Uuid.AsString() for p in conn.GetConnectedPads(i)} for key, i in inventory.items()}
        inv_items = list(inventory.items())
        for idx, (key, item) in enumerate(inv_items):
            box_a = item.GetBoundingBox()
            box_a.Inflate(floor)
            for other_key, other in inv_items[idx + 1:]:
                if not box_a.Intersects(other.GetBoundingBox()):
                    continue
                layers = set(item.GetLayerSet().CuStack()) & set(other.GetLayerSet().CuStack())
                if not layers:
                    continue
                shared = [inventory[p] for p in touching_all.get(key, set()) & touching_all.get(other_key, set()) if p in inventory]
                for layer in layers:
                    if _too_close(item, other, layer, shared, floor):
                        is_native_conn = (other in conn.GetConnectedTracks(item) or other in conn.GetConnectedPads(item))
                        if item.GetClass() != 'PAD' and other.GetClass() != 'PAD' and junction_in_pad(item, other, layer, shared):
                            sub_floor_pairs.append((key, other_key, layer, shared))
                        elif not is_native_conn:
                            sub_floor_pairs.append((key, other_key, layer, shared))

        sub_floor_adj = {}
        for k1, k2, _, _ in sub_floor_pairs:
            sub_floor_adj.setdefault(k1, set()).add(k2)
            sub_floor_adj.setdefault(k2, set()).add(k1)

        if roots is None:
            self.items = inventory
        else:
            pending = [pad(board, endpoint) for endpoint in roots]
            reachable = {}
            while pending:
                item = pending.pop()
                key = item.m_Uuid.AsString()
                if key in reachable or key not in inventory:
                    continue
                reachable[key] = inventory[key]
                pending.extend(list(conn.GetConnectedTracks(item)) + list(conn.GetConnectedPads(item)))
                for neighbor_key in sub_floor_adj.get(key, ()):
                    if neighbor_key not in reachable and neighbor_key in inventory:
                        pending.append(inventory[neighbor_key])
            self.items = reachable
        if any(i.GetClass() == 'PCB_ARC' for i in self.items.values()):
            raise ValueError('arc centreline measurement is not supported')
        self.adj, self.ports, self.tags, self.entries = {}, {}, {}, []
        conn = board.GetConnectivity()
        touching = {key: {p.m_Uuid.AsString() for p in conn.GetConnectedPads(i)} for key, i in self.items.items()}
        for key, item in self.items.items():
            for layer in item.GetLayerSet().CuStack():
                if item.GetClass() == 'PCB_TRACK':
                    self.port(key, layer, (item.GetStart().x, item.GetStart().y))
                    self.port(key, layer, (item.GetEnd().x, item.GetEnd().y))
                else:
                    self.port(key, layer, position(item))
            for other in list(conn.GetConnectedTracks(item)) + list(conn.GetConnectedPads(item)):
                target = other.m_Uuid.AsString()
                if target == key or target not in self.items or target < key:
                    continue
                for layer in set(item.GetLayerSet().CuStack()) & set(other.GetLayerSet().CuStack()):
                    a, b = geo.closest(self.line(item), self.line(other))
                    if item.GetClass() != 'PAD' and other.GetClass() != 'PAD' and junction_in_pad(
                            item, other, layer, [self.items[p] for p in touching[key] & touching[target]]):
                        continue
                    self.edge(self.port(key, layer, a), self.port(target, layer, b), math.dist(a, b))
        self.sub_floor_bridges = {}
        for key, target, layer, shared in sub_floor_pairs:
            if key not in self.items or target not in self.items:
                continue
            item = self.items[key]
            other = self.items[target]
            a, b = geo.closest(self.line(item), self.line(other))
            dist = math.dist(a, b)
            node_a = self.port(key, layer, a)
            node_b = self.port(target, layer, b)
            self.edge(node_a, node_b, dist)
            shapes, cross = _separation_shapes(item, other, layer, shared)
            gap_mm = _gap_lower_bound(item, other, shapes)
            bridge_record = dict(
                items=(key, target),
                gap_mm=gap_mm if gap_mm is not None else dist / 1e6,
                loc=((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                layer=layer,
            )
            self.sub_floor_bridges[(node_a, node_b)] = bridge_record
            self.sub_floor_bridges[(node_b, node_a)] = bridge_record
        self.islands = []
        for zone in board.Zones():
            if zone.GetNetname() != net or zone.GetIsRuleArea() or zone.IsTeardropArea():
                continue
            for layer in zone.GetLayerSet().CuStack():
                if not zone.IsFilled() or not zone.HasFilledPolysForLayer(layer):
                    raise ValueError('declared net has an unfilled zone')
                native = zone.GetFilledPolysList(layer)
                for poly in _polyset(native):
                    island = len(self.islands)
                    self.islands.append((layer, poly))
                    for key, item in self.items.items():
                        if not item.IsOnLayer(layer) or (item.GetClass() in ('PAD', 'PCB_VIA') and not item.FlashLayer(layer)):
                            continue
                        shape = item.GetEffectiveShape(layer)
                        if not native.Collide(shape):
                            continue
                        a, b = self.line(item)
                        candidates = [a, b, tuple((x + y) / 2 for x, y in zip(a, b))]
                        for ring in [poly['outline'], *poly['holes']]:
                            for c, d in geo.edges(ring):
                                p, q = geo.closest((a, b), (c, d))
                                candidates.append(q)
                        candidates.sort(key=lambda p: math.dist(a, p))
                        contact = next((p for p in candidates if geo.in_copper(p, poly)
                                        and shape.Collide(k.VECTOR2I(round(p[0]), round(p[1])))), None)
                        if contact is not None:
                            # Account explicitly for travel from the item's
                            # centreline to the thermal/annular plane contact.
                            origin = geo.project(contact, a, b)
                            node = self.port(key, layer, origin)
                            entry = self.node((key, layer, 'plane', island))
                            self.edge(node, entry, math.dist(origin, contact))
                            self.entries.append((entry, island, contact, key))
        self.join_items()

    def node(self, tag):
        if tag not in self.tags:
            self.tags[tag] = len(self.adj)
            self.adj[self.tags[tag]] = {}
        return self.tags[tag]

    def port(self, key, layer, point):
        point = tuple(float(v) for v in point)
        node = self.node((key, int(layer), point))
        self.ports.setdefault((key, int(layer)), {})[point] = node
        return node

    def edge(self, a, b, length):
        self.adj[a][b] = self.adj[b][a] = min(length, self.adj[a].get(b, math.inf))

    @staticmethod
    def line(item):
        if item.GetClass() == 'PCB_TRACK':
            return (item.GetStart().x, item.GetStart().y), (item.GetEnd().x, item.GetEnd().y)
        return position(item), position(item)

    def join_items(self):
        for (key, layer), ports in self.ports.items():
            ordered = sorted(ports, key=lambda p: math.dist(self.line(self.items[key])[0], p))
            for a, b in zip(ordered, ordered[1:]):
                self.edge(ports[a], ports[b], math.dist(a, b))
        # Two-layer scope: each through via/plated pad pays the full vertical
        # distance. Surface pads have no layer bridge.
        for key, item in self.items.items():
            layers = list(item.GetLayerSet().CuStack())
            if len(layers) > 1:
                a = self.port(key, layers[0], position(item))
                b = self.port(key, layers[-1], position(item))
                self.edge(a, b, int(self.board.GetDesignSettings().GetBoardThickness()))

    def terminal(self, terminal):
        layers = list(terminal.GetLayerSet().CuStack())
        return self.port(terminal.m_Uuid.AsString(), layers[0], position(terminal))

    def path(self, first, second, *, plane=False, blocked=()):
        start, end = self.terminal(first), self.terminal(second)
        blocked_nodes = [node for tag, node in self.tags.items() if tag[0] in blocked]
        best = geo.shortest(self.adj, start, end, blocked_nodes)
        record = None if best is None else dict(length_mm=best[0] / 1e6, plane_hop_mm=0., plane_path_nm=None,
                                                copper_path=self.describe_path(best[1]))
        if plane:
            # Only enter and leave the SAME native filled island. Never join
            # separate islands just because KiCad assigned the same net name.
            first_paths = [(entry, island, point, geo.shortest(self.adj, start, entry, blocked_nodes))
                           for entry, island, point, key in self.entries if key not in blocked]
            second_paths = [(entry, island, point, geo.shortest(self.adj, entry, end, blocked_nodes))
                            for entry, island, point, key in self.entries if key not in blocked]
            for _, island, a, left in first_paths:
                if left is None:
                    continue
                for _, other, b, right in second_paths:
                    if other != island or right is None:
                        continue
                    lower = left[0] + math.dist(a, b) + right[0]
                    if record is not None and lower >= record['length_mm'] * 1e6:
                        continue
                    path = geo.plane_path(a, b, self.islands[island][1])
                    if path is None:
                        continue
                    hop = sum(math.dist(p, q) for p, q in zip(path, path[1:]))
                    length = (left[0] + hop + right[0]) / 1e6
                    if record is None or length < record['length_mm']:
                        record = dict(length_mm=length, plane_hop_mm=hop / 1e6, plane_path_nm=path,
                                      copper_path=self.describe_path(left[1]) + self.describe_path(right[1]))
        return record

    def describe_path(self, nodes):
        tags = {node: tag for tag, node in self.tags.items()}
        return [dict(uuid=tags[node][0], layer=self.board.GetLayerName(tags[node][1]),
                     contact_nm=tags[node][2] if isinstance(tags[node][2], tuple) else 'plane contact')
                for node in nodes]

    @staticmethod
    def pad_ids(terminals):
        if not isinstance(terminals, (list, tuple)):
            terminals = [terminals]
        return {p.m_Uuid.AsString() for p in terminals}

    def bypass(self, first, second):
        blocked = {node for tag, node in self.tags.items() if tag[0] in self.pad_ids(second)}
        start = self.terminal(first)
        return any(geo.shortest(self.adj, start, entry, blocked) is not None
                   for entry, _, _, _ in self.entries)

    def bypass_evidence(self, first, second):
        blocked = {node for tag, node in self.tags.items() if tag[0] in self.pad_ids(second)}
        start = self.terminal(first)
        paths = [(path, key) for entry, _, _, key in self.entries
                 if (path := geo.shortest(self.adj, start, entry, blocked)) is not None]
        return [dict(plane_entry=key, copper_path=self.describe_path(path[1]))
                for path, key in sorted(paths, key=lambda row: (row[0][0], row[1]))]

    def bypass_bridges(self, first, second):
        blocked = {node for tag, node in self.tags.items() if tag[0] in self.pad_ids(second)}
        start = self.terminal(first)
        found = []
        for entry, _, _, _ in self.entries:
            path = geo.shortest(self.adj, start, entry, blocked)
            if path is not None:
                for a, b in zip(path[1], path[1][1:]):
                    if (a, b) in self.sub_floor_bridges:
                        found.append(self.sub_floor_bridges[(a, b)])
        return found

    def plane_access(self, terminal, layer, blocked):
        start = self.terminal(terminal)
        blocked_nodes = {node for tag, node in self.tags.items() if tag[0] in self.pad_ids(blocked)}
        paths = [geo.shortest(self.adj, start, entry, blocked_nodes)
                 for entry, island, _, _ in self.entries if self.islands[island][0] == layer]
        found = [p[0] / 1e6 for p in paths if p is not None]
        return min(found) if found else None


def bank_terminals(board, relations, leg, side, net):
    endpoints = {endpoint_key(row[leg][side]): row[leg][side] for row in relations if row[leg][side]['net'] == net}
    return [pad(board, endpoint) for endpoint in endpoints.values()]


def measure_bank(board, bank, relations, graphs):
    row = dict(**bank, outcome='info', reasons=[])
    try:
        for leg in ('supply', 'return'):
            devices = []
            endpoints = {endpoint_key(r[leg][0]): r[leg][0] for r in relations}
            for endpoint in endpoints.values():
                net = endpoint['net']
                if net not in graphs:
                    graphs[net] = CopperGraph(board, net, [endpoint for relation in relations
                        for side in ('supply', 'return') for endpoint in relation[side] if endpoint['net'] == net])
                graph = graphs[net]
                first = pad(board, endpoint)
                capacitors = bank_terminals(board, relations, leg, 1, net)
                bridges = graph.bypass_bridges(first, capacitors)
                devices.append(dict(endpoint=endpoint, pin_bypasses_bank=graph.bypass(first, capacitors),
                                    reaches_plane=graph.bypass(first, []),
                                    bypass_paths=graph.bypass_evidence(first, capacitors),
                                    bypass_bridges=bridges))
            row[leg] = dict(devices=devices, pin_bypasses_bank=any(d['pin_bypasses_bank'] for d in devices),
                            bypass_bridges=[b for d in devices for b in d.get('bypass_bridges', [])])
        row['device_returns_without_plane'] = [d['endpoint'] for d in row['return']['devices'] if not d['reaches_plane']]
        row['positive_statement_status'] = 'not_applicable' if row['device_returns_without_plane'] else 'measured'
        row['device_return_reaches_plane_only_through_bank_capacitors'] = (
            all(not d['pin_bypasses_bank'] for d in row['return']['devices'])
            if not row['device_returns_without_plane'] else None)
    except ValueError as exc:
        row.update(outcome='unknown', reasons=[str(exc)], positive_statement_status='unknown',
                   device_return_reaches_plane_only_through_bank_capacitors=None)
    return row


def measure(board_path, document, *, generation=None):
    import pcbnew as k
    validate(document)
    before = digest(board_path)
    board = k.LoadBoard(str(board_path))
    if board is None or board.GetCopperLayerCount() != 2:
        raise ValueError('loop measurement requires a loaded two-layer board')
    # Grade saved fill; the producer is responsible for native refill/save.
    board.BuildConnectivity()
    graphs, records, warnings = {}, [], []
    generated = {row['id'] for row in generation['relations'] if row['copper']} if generation else set()
    refusals = {row['relation']: row['reason'] for row in generation.get('fallbacks', [])} if generation else {}
    if generation is not None:
        if digest_document(generation['contract']) != digest_document(document):
            raise ValueError('generation receipt differs from captured local contract')
        protected_ids = {row['uuid'] for row in generation['protected']}
        if any(row['id'] not in {r['id'] for r in document['relations']}
               or not set(row['copper']).issubset(protected_ids) for row in generation['relations']):
            raise ValueError('generation relation inventory differs from protected copper')
        if generation['protected']:
            from .local_decoupling import verify
            verify(board_path, generation)
    def warn(relation, bank, kind, reason):
        warnings.append(dict(severity='warning', relation=relation, bank=bank, kind=kind,
                             reason=reason, search_refusal=refusals.get(relation)))
    derived_banks = banks(document)
    by_id = {r['id']: r for r in document['relations']}
    relation_banks = {rid: bank for bank in derived_banks for rid in bank['relations']}
    for relation in document['relations']:
        bank = relation_banks[relation['id']]
        members = [by_id[rid] for rid in bank['relations']]
        row = dict(id=relation['id'], bank=bank['id'], outcome='info', reasons=[])
        if relation['id'] not in generated:
            reason = refusals.get(relation['id'], 'no generation receipt was supplied')
            warn(relation['id'], bank['id'], 'generation_fallback', reason)
        try:
            for leg in ('supply', 'return'):
                first, second = [pad(board, endpoint) for endpoint in relation[leg]]
                net = first.GetNetname()
                if net not in graphs:
                    graphs[net] = CopperGraph(board, net, [endpoint for relation in document['relations']
                        for side in ('supply', 'return') for endpoint in relation[side] if endpoint['net'] == net])
                graph = graphs[net]
                row[leg] = dict(net=net, surface_path=graph.path(first, second),
                                path=graph.path(first, second, plane=True))
                if len(members) == 1:
                    # Preserve the single-cell result fields used before banks.
                    row[leg].update(pin_bypasses_capacitor=graph.bypass(first, second),
                                    bypass_paths=graph.bypass_evidence(first, second))
                for plane in document['planes']:
                    if plane['net'] == net:
                        devices = bank_terminals(board, members, leg, 0, net)
                        access = graph.plane_access(second, board.GetLayerID(plane['layer']), devices)
                        row[leg]['capacitor_to_plane_mm'] = access
                        if access is None:
                            if relation['id'] in generated:
                                row['reasons'].append(leg + ': generated capacitor has no independent plane connection')
                            else:
                                warn(relation['id'], bank['id'], 'capacitor_plane_access',
                                     leg + ': capacitor has no independent plane connection')
                if row[leg]['path'] is None:
                    row['reasons'].append(leg + ': no native-connected copper path')
            row['loop_length_mm'] = (sum(row[leg]['path']['length_mm'] for leg in ('supply', 'return'))
                                     if all(row[leg]['path'] is not None for leg in ('supply', 'return')) else None)
            if 'max_loop_mm' in relation:
                row.update(max_loop_mm=relation['max_loop_mm'], basis=relation['basis'], outcome='pass')
                if row['loop_length_mm'] is not None and row['loop_length_mm'] > relation['max_loop_mm']:
                    row['reasons'].append('loop exceeds declared max_loop_mm')
            if row['reasons']:
                row['outcome'] = 'defect'
        except ValueError as exc:
            row.update(outcome='unknown', reasons=[str(exc)], loop_length_mm=None)
        records.append(row)
    bank_records = [measure_bank(board, bank, [by_id[rid] for rid in bank['relations']], graphs)
                    for bank in derived_banks]
    for bank in bank_records:
        for leg in ('supply', 'return'):
            if bank.get(leg, {}).get('pin_bypasses_bank'):
                bridges = bank.get(leg, {}).get('bypass_bridges', [])
                if bridges:
                    bridge = min(bridges, key=lambda b: b['gap_mm'])
                    gap_um = bridge['gap_mm'] * 1000
                    loc_mm = (bridge['loc'][0] / 1e6, bridge['loc'][1] / 1e6)
                    base_reason = (f"{leg}: device pin reaches plane without bank capacitors via "
                                   f"{gap_um:.1f} µm sub-floor gap at ({loc_mm[0]:.3f}, {loc_mm[1]:.3f}) mm")
                else:
                    base_reason = leg + ': device pin reaches plane without bank capacitors'
                for rid in bank['relations']:
                    if rid in generated:
                        rec = next((r for r in records if r['id'] == rid), None)
                        if rec is not None:
                            rec['reasons'].append(base_reason)
                            rec['outcome'] = 'defect'
                    else:
                        reason = base_reason
                        if rid in refusals:
                            reason += '; generation search refused: ' + refusals[rid]
                        warn(rid, bank['id'], 'bank_bypass', reason)
    if digest(board_path) != before:
        raise ValueError('board changed during loop measurement')
    tracks = list(board.GetTracks())
    return dict(schema='agent-ee.decoupling-loops-result/1', board_sha256=before,
                contract_sha256=digest_document(document), pcbnew_version=k.Version(), records=records, banks=bank_records,
                warnings=warnings, generated_relation_count=len(generated),
                outcome=('defect' if any(r['outcome'] == 'defect' for r in records) else
                         'unknown' if any(r['outcome'] == 'unknown' for r in records + bank_records) else
                         'pass' if records and all(r['outcome'] == 'pass' for r in records) else 'info'),
                nets={net: dict(track_length_mm=sum(float(t.GetLength()) / 1e6 for t in tracks
                                                    if t.GetNetname() == net and t.GetClass() != 'PCB_VIA'),
                                vias=sum(t.GetNetname() == net and t.GetClass() == 'PCB_VIA' for t in tracks))
                      for net in sorted({p['net'] for p in document['planes']})},
                metric='native-connected centreline paths, pad-centre projections, full via barrels and filled-island plane paths',
                limitations=['Geometric path length, not impedance, inductance or current distribution.',
                             'Arcs and plane visibility over 512 vertices or 50000 edges refuse measurement rather than approximate a pass.'])


def digest_document(document):
    from .storage import identity
    return identity(document)


def parameters(params):
    if not isinstance(params, dict) or 'contract' not in params or set(params) - {'contract', 'generation'}:
        raise ValueError('decoupling_loops requires contract')


def describe(ctx):
    parameters(ctx.params)
    ctx.captured(ctx.scope.get('board'))
    ctx.captured(ctx.params['contract'], immutable=True)
    # Placement produces this file after source-only capability preflight.
    if ('generation' in ctx.params and ctx.inputs is not None
            and (Path(ctx.inputs) / ctx.params['generation']).is_file()):
        ctx.captured(ctx.params['generation'])
    if ctx.inputs is not None:
        doc = validate(read_json(Path(ctx.inputs) / ctx.params['contract']))
        ctx.result['predicates'] = ['decoupling-loop:continuity:' + r['id'] for r in doc['relations']]
        ctx.result['predicates'] += ['decoupling-loop:length:' + r['id'] for r in doc['relations'] if 'max_loop_mm' in r]


def check(ctx):
    from .checks import CheckError
    parameters(ctx.params)
    from .local_decoupling import GENERATION_FILE
    generation = ctx.params.get('generation', str(Path(ctx.scope['board']).parent / GENERATION_FILE))
    extra = (['--generation', ctx.candidate_path(generation)]
             if 'generation' in ctx.params or generation in ctx.manifest['files'] else [])
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.decoupling_loops',
                 ctx.candidate_path(ctx.scope['board']), '--contract', ctx.candidate_path(ctx.params['contract']),
                 '--out', '/work/decoupling-loops.json', *extra], timeout=180)
    ctx.evidence('decoupling-loops.json')
    result = ctx.load_json('decoupling-loops.json', 'native decoupling loops')
    if (result.get('board_sha256') != ctx.manifest['files'][ctx.scope['board']]
            or result.get('contract_sha256') != digest_document(read_json(ctx.inputs / ctx.params['contract']))):
        raise CheckError('malformed', 'native loop report does not bind the captured board and contract')
    ctx.result['details'].update(result)
    ctx.result.update(outcome='pass' if result['outcome'] == 'info' else result['outcome'], execution='ok',
                      summary='Native decoupling loops: ' + result['outcome'] + '; ' +
                      '; '.join(r['id'] + ': ' + ', '.join(r['reasons']) for r in result['records'] if r['reasons']))
    if result.get('warnings'):
        ctx.result['summary'] += '; warnings: ' + '; '.join(
            w['relation'] + '/' + w['bank'] + ': ' + w['reason'] for w in result['warnings'])
    ctx.result['limitations'] += result['limitations']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('board')
    parser.add_argument('--contract', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--generation')
    args = parser.parse_args()
    write_json(args.out, measure(args.board, read_json(args.contract), generation=read_json(args.generation) if args.generation else None))
else:
    from .check_registry import register
    register('decoupling_loops', describe, check)
