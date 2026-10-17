"""Read-only native load corroboration for exact saved envelope facts."""
import argparse
import hashlib
import json
from pathlib import Path

from .placement_probe import extract as placement_extract


def extract(path):
    import pcbnew
    path = Path(path)
    before = path.read_bytes()
    placement = placement_extract(path)
    board = pcbnew.LoadBoard(str(path))
    copper = [{'id': int(layer), 'name': board.GetLayerName(layer)}
              for layer in board.GetEnabledLayers().CuStack()]
    if path.read_bytes() != before or placement['board_sha256'] != hashlib.sha256(before).hexdigest():
        raise ValueError('board changed during native envelope probe')
    return {'schema': 'agent-ee.envelope-probe/1', 'board_sha256': placement['board_sha256'],
            'pcbnew_version': pcbnew.Version(), 'copper': copper,
            'has_stackup': bool(board.GetDesignSettings().m_HasStackup),
            'thickness_nm': int(board.GetDesignSettings().GetBoardThickness()),
            'outline': placement['outline']}


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
