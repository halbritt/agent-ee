"""Saved copper containment through KiCad polygons; native imports stay in children."""
import argparse
from pathlib import Path
import subprocess

from .storage import canonical, read_json, write_json


def findings(board):
    """Subtract the board material (including cutouts) from each flashed copper polygon."""
    import pcbnew as k
    outline = k.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False) or outline.IsEmpty():
        raise ValueError('native copper containment requires a closed board outline')
    layers = list(board.GetEnabledLayers().CuStack())
    items = [*board.GetTracks(), *board.Zones(), *board.GetDrawings()]
    for fp in board.GetFootprints():
        items.extend([*fp.Pads(), *fp.Zones(), *fp.GraphicalItems(), *fp.GetFields()])
    result = []
    for item in items:
        kind = item.GetClass()
        if kind in ('PCB_FIELD', 'PCB_TEXT') and not item.IsVisible():
            continue
        if kind == 'ZONE' and item.GetIsRuleArea():
            continue
        outside_layers = []
        for layer in layers:
            if not item.IsOnLayer(layer):
                continue
            if kind in ('PAD', 'PCB_VIA') and not item.FlashLayer(layer):
                continue
            copper = k.SHAPE_POLY_SET()
            if kind == 'ZONE':
                # Saved fills are physical copper; the requested zone boundary is not.
                if item.HasFilledPolysForLayer(layer):
                    copper.Append(item.GetFilledPolysList(layer))
            else:
                item.TransformShapeToPolygon(copper, layer, 0,
                    int(board.GetDesignSettings().m_MaxError), k.ERROR_OUTSIDE)
            copper.BooleanSubtract(outline)
            if not copper.IsEmpty():
                outside_layers.append(board.GetLayerName(layer))
        if outside_layers:
            pos = item.GetPosition()
            result.append(dict(type='copper_outside_outline', severity='error',
                description='Saved effective copper polygon extends outside board material',
                layers=outside_layers, items=[dict(uuid=item.m_Uuid.AsString(),
                    description=kind, pos=dict(x=k.ToMM(pos.x), y=k.ToMM(pos.y)))]))
    return sorted(result, key=lambda f: f['items'][0]['uuid'])


def run(board, work):
    """Child invocation for producers already running in their isolated workspace."""
    path = Path(work) / 'copper-containment.json'
    try:
        completed = subprocess.run(['/usr/bin/python3', '-B', '-m', 'agent_ee.outline_containment',
            str(board), '-o', str(path)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('native copper containment could not run: ' + str(exc)) from exc
    (Path(work) / 'copper-containment.stdout.txt').write_text(completed.stdout)
    (Path(work) / 'copper-containment.stderr.txt').write_text(completed.stderr)
    if completed.returncode != 0:
        raise ValueError('native copper containment failed: ' + completed.stderr[-2000:])
    return read_json(path)


def sandboxed(inputs, board, work):
    """Read captured board bytes in the existing networkless executor."""
    from .executor import execute
    record = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.outline_containment',
        '/candidate/' + str(board), '-o', '/work/copper-containment.json'], timeout=120)
    # Keep the complete receipt while leaving packet excerpt space for later checks.
    (Path(work) / 'execution.json').write_bytes(canonical(record) + b'\n')
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        raise ValueError('native copper containment failed: ' + str(record))
    return read_json(Path(work) / 'copper-containment.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('-o', '--out', required=True)
    args = parser.parse_args()
    import pcbnew as k
    write_json(args.out, findings(k.LoadBoard(args.board)))


if __name__ == '__main__':
    main()
