"""Candidate-bound inspection images; visual availability never changes a verdict."""

import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .candidate import safe_relative, verify_snapshot
from .executor import execute
from .render3d import render_board
from .storage import digest, read_json, write_json


def skip_requested(value):
    if type(value) is not bool:
        raise ValueError('skip_visuals must be a boolean')
    return value


def produce(board, out, *, skip=False, inputs=None):
    """Run native imaging networklessly and retain explicit missing coverage."""
    board, out = Path(board).resolve(), Path(out)
    inputs = board.parent if inputs is None else Path(inputs).resolve()
    relative = board.relative_to(inputs).as_posix()
    skip_requested(skip)
    environment_skip = os.environ.get('AGENT_EE_SKIP_VISUALS', '0')
    if environment_skip not in ('0', '1'):
        raise ValueError('AGENT_EE_SKIP_VISUALS must be 0 or 1')
    skip = skip or environment_skip == '1'
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    board_hash = digest(board)
    report = {'schema': 'agent-ee.candidate-visuals/1', 'board_sha256': board_hash,
              'status': 'skipped' if skip else 'unavailable', 'images': [],
              'reason': 'explicit skip_visuals/AGENT_EE_SKIP_VISUALS flag' if skip else None}
    if not skip:
        execution = execute(inputs, out / 'native',
                            ['/usr/bin/python3', '-B', '-m', 'agent_ee.candidate_visuals', relative],
                            timeout=180)
        report['execution'] = execution
        manifest = out / 'native' / 'visuals.json'
        if execution['execution'] == 'ok' and manifest.is_file():
            report.update(read_json(manifest))
            for image in report['images']:
                image['path'] = 'native/' + image['path']
        else:
            report['reason'] = 'native imaging failed; inspect native execution and console logs'
    if digest(board) != board_hash or report['board_sha256'] != board_hash:
        raise ValueError('board changed during candidate imaging')
    write_json(out / 'manifest.json', report)
    return report


def capture_visuals(run_dir):
    """Attach coordinator imaging after pure input capture and scope admission."""
    run = Path(run_dir)
    manifest = verify_snapshot(run)
    inputs = run / 'inputs'
    board = inputs / safe_relative(manifest['scope']['board'])
    visuals = produce(board, run / 'work' / 'candidate.visuals',
                      skip=manifest['scope'].get('skip_visuals', False), inputs=inputs)
    for image in visuals['images']:
        image['path'] = 'work/candidate.visuals/' + image['path']
    verify_snapshot(run)
    manifest['visuals'] = visuals
    write_json(run / 'candidate.json', manifest)
    return manifest


def packet_visuals(run_dir, manifest):
    """Verify coordinator-produced images before exposing paths to reviewers."""
    report = manifest.get('visuals')
    if report is None:
        return {'status': 'unavailable', 'reason': 'capture has no coordinator imaging record', 'images': []}
    root = Path(run_dir)
    board = manifest['scope']['board']
    if report['board_sha256'] != manifest['files'][board]:
        raise ValueError('candidate visuals bind a different board')
    for image in report['images']:
        rel = safe_relative(image['path'])
        path = root / rel
        if any(p.is_symlink() for p in (path, *path.parents)) or digest(path) != image['sha256']:
            raise ValueError('candidate visual image changed')
    return report


def _plot(board, root, name, layers):
    work = root / name
    work.mkdir()
    path = work / (name + '.svg')
    command = ['kicad-cli', 'pcb', 'export', 'svg', '--layers', ','.join(layers),
               '--mode-single', '--exclude-drawing-sheet', '--page-size-mode', '2',
               '--define-var', 'KIPRJMOD=' + str(board.parent), '--output', str(path), str(board)]
    with (work / 'stdout.log').open('wb') as stdout, (work / 'stderr.log').open('wb') as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=150,
                                   env=dict(os.environ, KICAD_CONFIG_HOME=str(work / 'config')))
    write_json(work / 'command.json', {'argv': command, 'returncode': completed.returncode})
    if completed.returncode:
        raise ValueError(f'{name}: kicad-cli exited {completed.returncode}')
    if ET.parse(path).getroot().tag != '{http://www.w3.org/2000/svg}svg':
        raise ValueError('native plot is not SVG')
    return {'view': name, 'layers': layers, 'path': path.relative_to(root).as_posix(),
            'sha256': digest(path)}


def native(board, root):
    """Native entry point; only called within the existing executor sandbox."""
    import pcbnew
    started = time.monotonic()
    saved = pcbnew.LoadBoard(str(board))
    copper = [pcbnew.LayerName(layer) for layer in saved.GetEnabledLayers().CuStack()]
    plots = {'front': ['F.Cu', 'F.SilkS', 'F.CrtYd', 'Edge.Cuts'],
             'back': ['B.Cu', 'B.SilkS', 'B.CrtYd', 'Edge.Cuts']}
    plots.update({layer: [layer, 'Edge.Cuts'] for layer in copper if layer not in ('F.Cu', 'B.Cu')})
    renders = read_json(render_board(board, root / 'renders', width=1600, height=900,
                                     jobs=2, timeout=150, views=('top', 'bottom')))
    report = {'board_sha256': digest(board), 'status': 'available', 'images': [],
              'kicad_version': renders['kicad']['version'], 'kicad_sha256': digest(Path(shutil.which('kicad-cli'))),
              'model_coverage': renders['model_coverage'], 'warnings': renders['warnings'],
              'reproducibility': {'status': 'not_verified', 'reason':
                  'No repeated-render comparison was performed for this capture. Native ray tracing '
                  'is invoked without a bound random seed; SVG metadata can vary. '
                  'Exact image bytes are hashed, but byte determinism is unqualified.'}}
    for image in renders['images']:
        report['images'].append({**image, 'layers': None, 'path': 'renders/' + image['path']})
    for name, layers in plots.items():
        report['images'].append(_plot(board, root, name, layers))
    report['duration_seconds'] = round(time.monotonic() - started, 3)
    write_json(root / 'visuals.json', report)


if __name__ == '__main__':
    import sys
    native(Path('/candidate') / safe_relative(sys.argv[1]), Path('/work'))
