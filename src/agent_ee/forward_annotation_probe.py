"""pcbnew child process for forward annotation (runs inside the sandbox).

``facts`` reports the saved board's footprint identities, transforms, pad nets
and copper net references. ``apply`` executes a plan already validated by
``agent_ee.forward_annotation`` on a copy of the board and saves a new file.
It makes no binding decisions of its own; any disagreement with the plan
stops before saving. pcbnew is imported only in this process.
"""
import argparse
import hashlib
import json
import sys
import uuid

from .silkscreen import prepare_footprint

# Fixed namespace for generated footprint and field identities.
FOOTPRINT_NAMESPACE = uuid.UUID('7b1f3c52-8a0e-5d4b-9c61-2e7f0a9d3b85')
# Destroying a removed FOOTPRINT corrupts pcbnew's SWIG type registry for the
# rest of the process, so removed proxies live until exit and output facts are
# extracted by a separate child process.
_REMOVED = []


def _sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def facts(board):
    import pcbnew
    bbox = board.GetBoardEdgesBoundingBox()
    report = dict(pcbnew_version=pcbnew.Version(), footprints=[], tracks=[], zones=[],
                  edge_bbox_nm=[bbox.GetLeft(), bbox.GetTop(), bbox.GetRight(), bbox.GetBottom()])
    for fp in board.GetFootprints():
        pos = fp.GetPosition()
        report['footprints'].append(dict(
            uuid=fp.m_Uuid.AsString(), path=fp.GetPath().AsString(), reference=fp.GetReference(),
            value=fp.GetValue(), fpid=fp.GetFPIDAsString(), position_nm=[pos.x, pos.y],
            orientation_deg=fp.GetOrientationDegrees(), layer=board.GetLayerName(fp.GetLayer()),
            fields={f.GetName(): f.GetText() for f in fp.GetFields() if f.GetName() not in ('Reference', 'Value')},
            pads=[dict(number=p.GetNumber(), net=p.GetNetname(), position_nm=[p.GetPosition().x, p.GetPosition().y])
                  for p in fp.Pads()]))
    for item in board.GetTracks():
        report['tracks'].append(dict(uuid=item.m_Uuid.AsString(), net=item.GetNetname(), kind=item.GetClass()))
    for zone in board.Zones():
        report['zones'].append(dict(uuid=zone.m_Uuid.AsString(), net=zone.GetNetname()))
    report['footprints'].sort(key=lambda x: x['uuid'])
    report['tracks'].sort(key=lambda x: x['uuid'])
    report['zones'].sort(key=lambda x: x['uuid'])
    return report


def _stable_uuids(fp, path):
    import pcbnew
    def kiid(*parts):
        return pcbnew.KIID(str(uuid.uuid5(FOOTPRINT_NAMESPACE, json.dumps([path, *parts], separators=(',', ':')))))
    fp.SetUuidDirect(kiid('footprint'))
    for index, item in enumerate(fp.Pads()):
        item.SetUuidDirect(kiid('pad', index))
    for index, item in enumerate(fp.GetFields()):
        item.SetUuidDirect(kiid('field', index))
    for index, item in enumerate(fp.GraphicalItems()):
        item.SetUuidDirect(kiid('graphic', index))


def _set_fields(fp, fields):
    import pcbnew
    for name, text in sorted(fields.items()):
        known = fp.HasField(name)
        fp.SetField(name, text)
        if not known:
            field = next(f for f in fp.GetFields() if f.GetName() == name)
            # Retained footprints must keep all prior UUIDs. Seed only the new
            # field, independently of field order, values and the output path.
            seed = json.dumps([fp.m_Uuid.AsString(), 'field', name], separators=(',', ':'))
            field.SetUuidDirect(pcbnew.KIID(str(uuid.uuid5(FOOTPRINT_NAMESPACE, seed))))
            # Symbol fields copied to a footprint stay hidden, as native update does.
            field.SetVisible(False)
    from .probe_features import PROPERTY
    if PROPERTY in fields:
        fp.SetExcludedFromBOM(True)
        fp.SetExcludedFromPosFiles(True)


def apply(board_path, plan, library_dir, out):
    import pcbnew
    if _sha256(board_path) != plan['inputs']['board_sha256']:
        raise SystemExit('board differs from the planned input')
    board = pcbnew.LoadBoard(board_path)
    footprints = {fp.m_Uuid.AsString(): fp for fp in board.GetFootprints()}
    # Look nets up by name before renaming: NETINFO_LIST keeps its old name map
    # after SetNetname, and iterating GetNetsByName() corrupts SWIG proxy types.
    renamed = {r['from']: r['to'] for r in plan['renames']}
    targets = set(renamed.values()) | set(plan['new_nets'])
    nets = {}
    for name in sorted({p['net'] for p in plan['pads']} - targets | set(renamed)):
        net = board.FindNet(name)
        if net is None:
            raise SystemExit('planned existing net is absent: ' + name)
        nets[name] = net
    # Renames keep each NETINFO_ITEM, so tracks, vias and zones follow it.
    for old, new in sorted(renamed.items()):
        net = nets.pop(old)
        net.SetNetname(new)
        nets[new] = net
    for name in plan['new_nets']:
        if name in nets:
            raise SystemExit('planned new net already exists: ' + name)
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net
    for item in plan['remove']:
        _REMOVED.append(footprints.pop(item['footprint_uuid']))
        board.Remove(_REMOVED[-1])
    for item in plan['update']:
        fp = footprints[item['footprint_uuid']]
        fp.SetReference(item['reference'])
        fp.SetValue(item['value'])
        _set_fields(fp, item['fields'])
    for item in plan['add']:
        fp = pcbnew.FootprintLoad(library_dir + '/' + item['library'] + '.pretty', item['name'])
        if fp is None:
            raise SystemExit('captured footprint did not load: ' + item['fpid'])
        fp.SetFPID(pcbnew.LIB_ID(item['library'], item['name']))
        fp.SetPath(pcbnew.KIID_PATH(item['path']))
        fp.SetReference(item['reference'])
        fp.SetValue(item['value'])
        fp.SetPosition(pcbnew.VECTOR2I(*item['position_nm']))
        _set_fields(fp, item['fields'])
        prepare_footprint(fp)
        _stable_uuids(fp, item['path'])
        board.Add(fp)
        footprints[fp.m_Uuid.AsString()] = fp
    by_path = {fp.GetPath().AsString(): fp for fp in board.GetFootprints()}
    for assignment in plan['pads']:
        fp = by_path[assignment['path']]
        pads = [p for p in fp.Pads() if p.GetNumber() == assignment['pad']]
        if not pads:
            raise SystemExit('planned pad is absent: ' + assignment['path'] + ' ' + assignment['pad'])
        for pad in pads:
            pad.SetNet(nets[assignment['net']])
    pcbnew.SaveBoard(out, board)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('facts')
    one.add_argument('board')
    one.add_argument('-o', '--out', required=True)
    two = sub.add_parser('apply')
    two.add_argument('board')
    two.add_argument('--plan', required=True)
    two.add_argument('--libraries', required=True, help='directory holding <library>.pretty captures')
    two.add_argument('-o', '--out', required=True)
    args = parser.parse_args(argv)
    try:
        import pcbnew
    except ImportError as exc:
        print(f'pcbnew bindings unavailable: {exc}', file=sys.stderr)
        return 3
    if args.command == 'apply':
        with open(args.plan, 'rb') as stream:
            apply(args.board, json.load(stream), args.libraries, args.out)
        return 0
    report = facts(pcbnew.LoadBoard(args.board))
    with open(args.out, 'x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
