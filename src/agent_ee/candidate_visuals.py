"""Candidate-bound inspection images; visual availability never changes a verdict."""

import os
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import native_run
from .candidate import safe_relative, verify_snapshot

from .render3d import render_board
from .storage import digest, read_json, write_json


def skip_requested(value):
    if type(value) is not bool:
        raise ValueError('skip_visuals must be a boolean')
    return value


def produce(board, out, *, skip=False, inputs=None):
    """Run native imaging networklessly and retain explicit missing coverage."""
    requested_board, requested_out = Path(board).absolute(), Path(out).absolute()
    requested_inputs = requested_board.parent if inputs is None else Path(inputs).absolute()
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
    enclosed, captured = False, None
    if not skip:
        from .footprint_probe import _enclosing_sandbox
        enclosed = _enclosing_sandbox()
        if enclosed:
            from .stage_receipts import file_tree
            if any(p.is_symlink() for path in (requested_board, requested_inputs, requested_out)
                   for p in (path, *path.parents)):
                raise ValueError('candidate imaging paths must not contain links')
            out = out.resolve()
            if inputs.is_relative_to(out):
                raise ValueError('candidate imaging output must not contain its inputs')
            # Inventory before creating an output below the input root. Copying
            # this finite roster cannot recurse into the newly owned output.
            captured, other = file_tree(inputs)
            if other or relative not in captured:
                raise ValueError('candidate imaging inputs must be regular captured files')
    out.mkdir(parents=True)
    board_hash = digest(board)
    report = {'schema': 'agent-ee.candidate-visuals/1', 'board_sha256': board_hash,
              'status': 'skipped' if skip else 'unavailable', 'images': [],
              'reason': 'explicit skip_visuals/AGENT_EE_SKIP_VISUALS flag' if skip else None}
    if not skip:
        execution = _enclosed_imaging(board, inputs, out, relative, captured) if enclosed else native_run.run(
            native_run.Intent(
                'candidate_visuals',
                ['/usr/bin/python3', '-B', '-m', 'agent_ee.candidate_visuals', relative],
                timeout=180,
            ),
            inputs=inputs,
            work=out / 'native',
        )
        report['execution'] = execution
        if enclosed:
            _check_imaging_paths(out)
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



def _enclosed_imaging(board, inputs, out, relative, captured):
    """One supervised imaging child within the already verified /app executor."""
    from .stage_receipts import file_tree
    work = out / 'native'
    private = work / 'inputs'
    private.mkdir(parents=True)
    copied = None
    try:
        for rel in captured:
            target = private / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inputs / rel, target, follow_symlinks=False)
        copied, other = file_tree(private)
        if other or copied != captured or digest(private / relative) != digest(board):
            raise ValueError('candidate imaging private project copy changed')
        environment = dict(os.environ, PYTHONPATH='/app', HOME=str(work / 'home'),
                           KICAD_CONFIG_HOME=str(work / 'kicad-config'))
        _check_imaging_paths(out)
        command = ('from agent_ee.candidate_visuals import _imaging_main; import sys; '
                   '_imaging_main(*sys.argv[1:])')
        execution = native_run.run(native_run.Intent('candidate_visuals',
            ['/usr/bin/python3', '-B', '-P', '-c', command, str(private / relative), str(work)],
            timeout=180, stop_grace_s=5, isolation='host', env=environment), work=work)
        _check_imaging_paths(out)
        cleanup = work / 'imaging-cleanup.json'
        if (not cleanup.is_file() or cleanup.is_symlink() or read_json(cleanup) !=
                {'schema': 'agent-ee.candidate-imaging-cleanup/1', 'status': 'unwound'}):
            raise ValueError('candidate imaging cleanup is unconfirmed; retain native diagnostics')
        return execution
    finally:
        _check_imaging_paths(out)
        after, other = file_tree(inputs)
        if out.is_relative_to(inputs):
            owned = out.relative_to(inputs).as_posix() + '/'
            after = {k: v for k, v in after.items() if not k.startswith(owned)}
            other = [k for k in other if not k.startswith(owned) and k != owned[:-1]]
        if other or after != captured:
            raise ValueError('candidate imaging changed its captured inputs')
        if copied is not None:
            after, other = file_tree(private)
            preferences = str(Path(relative).with_suffix('.kicad_prl'))
            pref = private / preferences
            if (other or (pref.exists() and not pref.is_file())
                    or (preferences in copied and preferences not in after)
                    or {k: v for k, v in after.items() if k != preferences} !=
                       {k: v for k, v in copied.items() if k != preferences}):
                raise ValueError('candidate imaging changed its private project')


def _check_imaging_paths(out):
    for path in (out, out / 'native', out / 'native/inputs'):
        if (not path.is_dir() or path.resolve() != path
                or any(p.is_symlink() for p in (path, *path.parents))):
            raise ValueError('candidate imaging output ancestry changed; retain native diagnostics')


def _imaging_main(board, root):
    """Let TERM unwind the existing render/plot supervisors before bounded KILL."""
    import signal
    def terminate(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        # Reuse held_interrupt's SIGINT launch guard as well as its unwind.
        signal.raise_signal(signal.SIGINT)
    previous_int = signal.signal(signal.SIGINT, signal.default_int_handler)
    previous = signal.signal(signal.SIGTERM, terminate)
    unwound = False
    try:
        native(Path(board), Path(root))
    except (KeyboardInterrupt, ValueError, OSError):
        unwound = True
        raise
    else:
        unwound = True
    finally:
        # render_board joins its cancelled workers; synchronous _plot/probes reap
        # in native_run before unwinding here. No receipt after forced KILL.
        try:
            if unwound:
                write_json(Path(root) / 'imaging-cleanup.json',
                           {'schema': 'agent-ee.candidate-imaging-cleanup/1', 'status': 'unwound'})
        finally:
            signal.signal(signal.SIGTERM, previous)
            signal.signal(signal.SIGINT, previous_int)


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
        try:
            changed = (any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()
                       or digest(path) != image['sha256'])
        except OSError as exc:
            raise ValueError('candidate visual image changed') from exc
        if changed:
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
        execution = native_run.run(
            native_run.Intent(
                'stage',
                command,
                timeout=150,
                isolation='host',
                env=dict(os.environ, KICAD_CONFIG_HOME=str(work / 'config')),
            ),
            work=None,
            sink=stdout,
            stderr_sink=stderr,
        )
        completed = native_run.completed(execution)
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
                  'is invoked without a bound random seed. An AGENTEE-382 reviewer repeat on KiCad 10.0.7 '
                  'measured PNG ray-trace pixel noise (0.67% of raw pixel bytes on the AGENTEE-382 pod '
                  'board; the magnitude depends on the board and view) and '
                  'SVG <title> creation timestamp changes. SVG metadata is not normalized. '
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
