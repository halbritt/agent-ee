"""Authored AGENTEE-623 fixture; native construction runs only after admission."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

from agent_ee import placement_job as job
from artifact_retention import RetainedTemporaryDirectory


SOURCES = Path(__file__).parent / 'fixtures/portable-placement'


def contract():
    manifest = json.loads((SOURCES / 'sources.json').read_bytes())
    source = {}
    for name, expected in manifest['files'].items():
        raw = (SOURCES / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'authored fixture source changed: {name}')
        source[name] = raw
    return source


def bind(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verified(fixture, key):
    return job.verified_input(fixture['inputs'][key], key)


def save(fixture, name, value):
    (fixture['root'] / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


@contextmanager
def portable_fixture(test, *, native=False):
    with RetainedTemporaryDirectory(test.id(), prefix='ee623-') as tmp:
        root = Path(tmp)
        originals = root / 'original-sources'
        originals.mkdir()
        for name, raw in contract().items():
            (root / name).write_bytes(raw)
            (originals / name).write_bytes(raw)
        (originals / 'sources.json').write_bytes((SOURCES / 'sources.json').read_bytes())
        (root / 'board.kicad_pro').write_text('{}\n')
        fixture = {'root': root, 'inputs': {key: bind(root / name) for key, name in (
            ('intent', 'intent.json'), ('groups', 'groups.json'), ('geometry', 'geometry.json'),
            ('project', 'board.kicad_pro'))}}
        if native:
            from toolchain import require_toolchain
            require_toolchain(native=True)
            build_native(root)
            fixture['inputs'].update(board=bind(root / 'board.kicad_pcb'), final=bind(root / 'final.kicad_pcb'))
        save(fixture, 'fixture-inputs.json', {'provenance': 'synthetic-authored; not historical evidence',
            'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'base_builder_sha256': hashlib.sha256(Path(__file__).with_name('test_placement_job.py').read_bytes()).hexdigest(),
            'source_manifest_sha256': hashlib.sha256((SOURCES / 'sources.json').read_bytes()).hexdigest(),
            'inputs': fixture['inputs']})
        yield fixture


def build_native(root):
    import pcbnew
    from test_placement_job import build_board, _v, _box, MM
    geometry = json.loads((root / 'geometry.json').read_bytes())
    raw = root / 'board.kicad_pcb'
    build_board(raw, locked_hole=False)
    board = pcbnew.LoadBoard(str(raw))
    for fp in board.GetFootprints():
        if fp.GetReference() in geometry['poses_mm']:
            x, y = geometry['poses_mm'][fp.GetReference()]
            fp.SetPosition(_v(pcbnew, int(x * MM), int(y * MM)))
    # Repeated native labels remain distinct through UUID custody, as in the
    # existing authored NativeTransformTests fixture.
    mark = pcbnew.FOOTPRINT(board)
    mark.SetReference('FID')
    mark.Reference().SetVisible(False)
    mark.Value().SetVisible(False)
    pad = pcbnew.PAD(mark)
    pad.SetNumber('1')
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, _v(pcbnew, MM, MM))
    pad.SetLayerSet(pad.SMDMask())
    mark.Add(pad)
    board.Add(mark)
    mark.SetPosition(_v(pcbnew, 6 * MM, 3 * MM))
    artwork = pcbnew.FOOTPRINT(board)
    artwork.SetReference('')
    artwork.Reference().SetVisible(False)
    artwork.Value().SetVisible(False)
    board.Add(artwork)
    for shape in _box(pcbnew, artwork, pcbnew.F_SilkS, -500_000, -500_000, 500_000, 500_000):
        artwork.Add(shape)
    artwork.SetPosition(_v(pcbnew, 18 * MM, 2 * MM))
    pcbnew.SaveBoard(str(raw), board)
    # A physically distinct saved final board tests that views use that board,
    # while the raw input keeps its separate immutable identity.
    final_board = pcbnew.LoadBoard(str(raw))
    for fp in final_board.GetFootprints():
        if fp.GetReference() == 'R1':
            x, y = geometry['final_R1_mm']
            fp.SetPosition(_v(pcbnew, int(x * MM), int(y * MM)))
    pcbnew.SaveBoard(str(root / 'final.kicad_pcb'), final_board)


def command(fixture, argv, name, *, timeout=120, input_text=None):
    if input_text is not None:
        (fixture['root'] / (name + '.stdin')).write_text(input_text)
    with subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(input=input_text.encode('utf-8') if input_text is not None else None,
                                                timeout=timeout)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            (fixture['root'] / (name + '.stdout')).write_bytes(stdout)
            (fixture['root'] / (name + '.stderr')).write_bytes(stderr)
            save(fixture, name + '.execution.json', {'argv': argv, 'status': 'timeout',
                'exit_code': process.returncode, 'timeout_s': timeout})
            raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from error
    (fixture['root'] / (name + '.stdout')).write_bytes(stdout)
    (fixture['root'] / (name + '.stderr')).write_bytes(stderr)
    save(fixture, name + '.execution.json', {'argv': argv, 'exit_code': process.returncode,
        'stdin_sha256': hashlib.sha256(input_text.encode()).hexdigest() if input_text is not None else None})
    return subprocess.CompletedProcess(argv, process.returncode, stdout.decode('utf-8'), stderr.decode('utf-8'))


def rails(fixture, board_key='board'):
    return {'schema': 'agent-ee.group-rails/1',
        'bindings': {'board_sha256': fixture['inputs'][board_key]['sha256'],
                     'groups_sha256': fixture['inputs']['groups']['sha256']},
        'shared': {'GND': {'evidence': 'authored return rail', 'parts': ['R1', 'U1'], 'groups': ['core', 'port']},
                   'A': {'evidence': 'authored scoped rail', 'parts': ['R1', 'C1', 'U1'], 'groups': ['core', 'port']}},
        'scoped': {'A': {'evidence': 'authored group-link control', 'intra_group': 'core',
                         'near_pairs': [['core', 'port'], ['port', 'core']]}}}
