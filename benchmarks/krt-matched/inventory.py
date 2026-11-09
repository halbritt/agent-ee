#!/usr/bin/env python3
"""Print size facts for candidate boards (used for the preregistered selection).

usage: inventory.py BOARD.kicad_pcb [...]   -> one JSON line per board
"""
import json
import sys

import pcbnew


def facts(path):
    board = pcbnew.LoadBoard(path)
    fps = list(board.GetFootprints())
    netted = [f for f in fps if any(p.GetNetCode() > 0 for p in f.Pads())]
    nets = {p.GetNetname() for f in fps for p in f.Pads() if p.GetNetCode() > 0}
    tracks = list(board.GetTracks())
    box = board.GetBoardEdgesBoundingBox()
    return {
        'board': path,
        'footprints': len(fps),
        'components': len(netted),          # footprints with at least one netted pad
        'pads': sum(len(list(f.Pads())) for f in netted),
        'nets': len(nets),
        'copper_layers': board.GetCopperLayerCount(),
        'zones': len([z for z in board.Zones() if not z.GetIsRuleArea()]),
        'rule_areas': len([z for z in board.Zones() if z.GetIsRuleArea()]),
        'track_items': len(tracks),
        'locked': sum(1 for f in fps if f.IsLocked()),
        'back_side': sum(1 for f in netted if f.IsFlipped()),
        'size_mm': [round(pcbnew.ToMM(box.GetWidth()), 1), round(pcbnew.ToMM(box.GetHeight()), 1)],
    }


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        try:
            print(json.dumps(facts(arg)))
        except Exception as exc:  # an unreadable candidate is itself a fact
            print(json.dumps({'board': arg, 'error': repr(exc)}))
