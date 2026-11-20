"""Extend a connected copper path on a disposable copy of a native project."""
from pathlib import Path
import shutil

from agent_ee import placement_job as job


def copy_project(source, directory, rules=None):
    directory.mkdir()
    board = directory / source.name
    for suffix in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
        source_file = rules if suffix == '.kicad_dru' and rules else source.with_suffix(suffix)
        shutil.copyfile(source_file, board.with_suffix(suffix))
    return board


def lengthen_horizontal_trace(report, directory, net):
    import pcbnew as k

    source = Path(report['final']['board'])
    board = copy_project(source, directory)
    native = k.LoadBoard(str(board))
    track = max((t for t in native.GetTracks() if t.GetNetname() == net and t.GetClass() == 'PCB_TRACK'
                 and t.GetStart().y == t.GetEnd().y), key=lambda t: t.GetLength())
    start, end = sorted((track.GetStart(), track.GetEnd()), key=lambda point: point.x)
    mid = (start.x + end.x) // 2
    step = k.FromMM(2)
    assert end.x - start.x > 4 * step
    points = [start, k.VECTOR2I(mid - step, start.y), k.VECTOR2I(mid, start.y - step),
              k.VECTOR2I(mid + step, start.y), end]
    for a, b in zip(points, points[1:]):
        segment = k.PCB_TRACK(native)
        segment.SetNetCode(track.GetNetCode())
        segment.SetLayer(track.GetLayer())
        segment.SetWidth(track.GetWidth())
        segment.SetStart(a)
        segment.SetEnd(b)
        native.Add(segment)
    native.Remove(track)
    assert k.SaveBoard(str(board), native, True)
    shutil.copyfile(source.with_suffix('.kicad_pro'), board.with_suffix('.kicad_pro'))
    return board


def measure(board, route, checkout, kind, spec, directory):
    return job._measure_paths(board, route, checkout, kind, spec, directory,
                              clock=job.Clock(120), cancel_dir=None)
