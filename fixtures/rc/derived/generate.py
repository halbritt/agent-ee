#!/usr/bin/env python3
"""Derive RC stage-boundary boards from fixtures/rc/control through pcbnew.

The control project is mounted read-only in the networkless executor. pcbnew
loads its routed board, applies one authored edit set per variant and saves the
result. The schematic, project and library tables are byte copies of the control.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

from agent_ee.executor import execute
from agent_ee.storage import digest, write_json

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parent / 'control'
VARIANTS = ('unplaced', 'placed-unrouted', 'seeded-placement')
NAME = 'rc_filter'
COPIED = ('rc_filter.kicad_sch', 'rc_filter.kicad_pro', 'fp-lib-table', 'sym-lib-table')
# Control outline is 96..116 x 96..108 mm; the staging row lies 9 mm right of it.
STAGING = {'R1': (125, 100), 'C1': (135, 100), 'J1': (145, 100)}
# C1's courtyard reaches into R1's (R1 at 105 mm); J1's pins sit 0.5 mm outside the left edge.
SEEDS = {'C1': (108, 100), 'J1': (95.5, 100)}


def native():
    import pcbnew as k

    out = Path('/work/fixtures')
    out.mkdir()
    control = Path('/control')
    edits = {}
    for variant in VARIANTS:
        project = out / variant
        project.mkdir()
        for name in COPIED:
            shutil.copyfile(control / name, project / name)
        board = k.LoadBoard(str(control / f'{NAME}.kicad_pcb'))
        moves = STAGING if variant == 'unplaced' else SEEDS if variant == 'seeded-placement' else {}
        for fp in board.GetFootprints():
            if fp.GetReference() in moves:
                x, y = moves[fp.GetReference()]
                fp.SetPosition(k.VECTOR2I(round(x * 1e6), round(y * 1e6)))
        tracks = list(board.GetTracks())
        removed = sorted(t.m_Uuid.AsString() for t in tracks)
        for track in tracks:
            board.Delete(track)
        zones = [z.m_Uuid.AsString() for z in board.Zones()]
        for zone in list(board.Zones()):
            if variant == 'unplaced':
                board.Delete(zone)
            else:
                zone.UnFill()  # Kept, deliberately unfilled: saved stage boundary.
        if not k.SaveBoard(str(project / f'{NAME}.kicad_pcb'), board, True):
            raise RuntimeError(f'failed to save {variant}')
        edits[variant] = {'removed_track_and_via_uuids': removed, 'moved_mm': moves,
                          'zones': {'removed' if variant == 'unplaced' else 'unfilled': zones}}
    write_json('/work/edits.json', edits)
    write_json('/work/sources.json', {
        'purpose': 'AGENTEE-103 stage-boundary derivatives of fixtures/rc/control (origin T1).',
        'pcbnew_version': k.Version(),
        'kicad_cli_version': subprocess.check_output(['kicad-cli', 'version'], text=True).strip(),
        'sources': {'generate.py': digest(HERE / 'generate.py'),
                    **{f'control/{p.name}': digest(p) for p in sorted(control.iterdir()) if p.is_file()}},
    })
    write_json('/work/fixture-hashes.json', {
        str(path.relative_to(out)): digest(path) for path in sorted(out.rglob('*')) if path.is_file()})


def generate(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    record = execute(HERE, out / 'generation',
        ['/usr/bin/python3', '-B', '/candidate/generate.py', '--native'], timeout=120,
        extra_ro=[(CONTROL, '/control')])
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        raise RuntimeError(f'generation failed: {record}; see {out / "generation/console.log"}')
    shutil.copytree(out / 'generation/fixtures', out / 'fixtures')
    return out / 'fixtures'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.native:
        native()
    elif args.out:
        print(generate(args.out))
    else:
        parser.error('--out is required')


if __name__ == '__main__':
    main()
