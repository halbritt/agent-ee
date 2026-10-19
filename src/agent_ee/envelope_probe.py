"""Read-only native load corroboration for exact saved envelope facts."""
import argparse
import hashlib
import json
from pathlib import Path

from .placement_probe import extract as placement_extract
from .placement_probe import vector_nm
from .geometry_probe import pad_vec


def extract(path):
    import pcbnew
    path = Path(path)
    before = path.read_bytes()
    placement = placement_extract(path)
    board = pcbnew.LoadBoard(str(path))
    footprints = list(board.GetFootprints())
    if len({fp.m_Uuid.AsString() for fp in footprints}) != len(footprints):
        raise ValueError('duplicate native footprint UUID makes rule-area ownership ambiguous')
    drills = {}
    for fp in footprints:
        for pad in fp.Pads():
            uid = pad.m_Uuid.AsString()
            if uid in drills:
                raise ValueError('duplicate native pad UUID makes drill inventory ambiguous')
            drills[uid] = {'drill_nm': vector_nm(pad_vec(pad, pad.GetDrillSize)),
                           'offset_nm': vector_nm(pad_vec(pad, pad.GetOffset))}
    for fp in placement['footprints']:
        for pad in fp['pads']:
            pad.update(drills[pad['uuid']])
    copper = [{'id': int(layer), 'name': board.GetLayerName(layer)}
              for layer in board.GetEnabledLayers().CuStack()]
    if path.read_bytes() != before or placement['board_sha256'] != hashlib.sha256(before).hexdigest():
        raise ValueError('board changed during native envelope probe')
    return {'schema': 'agent-ee.envelope-probe/1', 'board_sha256': placement['board_sha256'],
            'pcbnew_version': pcbnew.Version(), 'copper': copper,
            'has_stackup': bool(board.GetDesignSettings().m_HasStackup),
            'thickness_nm': int(board.GetDesignSettings().GetBoardThickness()),
            'outline': placement['outline'], 'footprints': placement['footprints'],
            'rule_areas': placement['rule_areas']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if Path(args.out).resolve() == Path(args.board).resolve():
        raise ValueError('native envelope probe cannot overwrite source board')
    Path(args.out).write_text(json.dumps(extract(args.board), sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
