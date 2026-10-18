#!/usr/bin/env python3
"""Generate original RC stage fixtures through the networkless executor.

The unresolved FOME-MC control is deliberately not an input. Native schematic
emission and board construction use the existing agent-ee implementations.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess

from agent_ee.executor import execute
from agent_ee.storage import digest, write_json

HERE = Path(__file__).resolve().parent
VARIANTS = ('unplaced', 'placed-unrouted', 'seeded-placement')
NAME = 'rc_stage'
POSITIONS = {'R1': (20, 15), 'C1': (30, 15), 'J1': (5, 10)}


def native():
    import pcbnew as k
    from agent_ee.board_build import construct, read_netlist
    from agent_ee.schematic_emitter import emit_schematic

    out = Path('/work/fixtures')
    out.mkdir()
    libraries = {'lib/StageRC.kicad_sym': (HERE / 'StageRC.kicad_sym').read_bytes(),
                 'lib/Owned_Gen.kicad_sym': Path('/owned-libraries/Owned_Gen.kicad_sym').read_bytes()}
    intent = {
        'schema': 'agent-ee.connectivity-intent/1',
        'libraries': [{'name': name, 'path': path, 'sha256': hashlib.sha256(data).hexdigest()}
                      for (path, data), name in zip(libraries.items(), ('StageRC', 'OwnedGen'))],
        'components': [dict(id=ref, reference=ref, lib_id=lib, value=value, footprint=fp, units=[1])
                       for ref, lib, value, fp in (
                           ('R1', 'StageRC:R', '10k', 'OwnedFP:CAP_2PAD'),
                           ('C1', 'OwnedGen:CAP', '100n', 'OwnedFP:CAP_2PAD'),
                           ('J1', 'OwnedGen:CONN3', 'RC port', 'OwnedFP:CONN_1x3'))],
        'nets': [dict(id=name.lower(), name=name,
                      terminals=[dict(component=ref, pin={'number': pin}) for ref, pin in terminals])
                 for name, terminals in (
                     ('IN', [('J1', '1'), ('R1', '1')]),
                     ('OUT', [('R1', '2'), ('C1', '1'), ('J1', '2')]),
                     ('GND', [('C1', '2'), ('J1', '3')]))],
        'no_connects': [],
    }
    layout = dict(schema='agent-ee.schematic-layout/1', project=NAME,
                  namespace='27229de7-b4c2-4e83-a8b6-d7d2f0a8bef9', transforms={})
    result, files = emit_schematic(intent, libraries, layout,
        symbol_table={'StageRC': '${KIPRJMOD}/lib/StageRC.kicad_sym',
                      'OwnedGen': '${KIPRJMOD}/lib/Owned_Gen.kicad_sym'},
        footprint_table={'OwnedFP': '${KIPRJMOD}/lib/OwnedFP.pretty'})
    if result['execution'] != 'ok':
        raise RuntimeError(result['findings'])
    common = out / 'common'
    for path, data in {**files, **libraries}.items():
        destination = common / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    footprints = {}
    for name in ('CAP_2PAD', 'CONN_1x3'):
        rel = f'lib/OwnedFP.pretty/{name}.kicad_mod'
        destination = common / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path('/owned-libraries') / f'OwnedFP.pretty/{name}.kicad_mod', destination)
        footprints['OwnedFP:' + name] = destination
    subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                    '-o', '/work/native.xml', str(common / f'{NAME}.kicad_sch')], check=True)
    netlist = read_netlist(Path('/work/native.xml').read_bytes())
    k.SaveBoard('/work/template.kicad_pcb', k.BOARD())
    envelope = dict(copper_layers=2, fixed_refs=[],
                    outline_nm=dict(x=0, y=0, w=50_000_000, h=30_000_000))
    construct(netlist, envelope, Path('/work/template.kicad_pcb'), footprints,
              Path('/work/base.kicad_pcb'), [digest(HERE / 'generate.py'), digest(HERE / 'StageRC.kicad_sym')])

    def vec(x, y):
        return k.VECTOR2I(round(x * 1e6), round(y * 1e6))

    for variant in VARIANTS:
        project = out / variant
        shutil.copytree(common, project)
        board = k.LoadBoard('/work/base.kicad_pcb')
        for index, fp in enumerate(sorted(board.GetFootprints(), key=lambda f: f.GetReference())):
            xy = (60 + index * 10, 10) if variant == 'unplaced' else POSITIONS[fp.GetReference()]
            if variant == 'seeded-placement':
                xy = {'C1': (20, 16.5), 'J1': (-.5, 10)}.get(fp.GetReference(), xy)
            fp.SetPosition(vec(*xy))
        if variant != 'unplaced':
            zone = k.ZONE(board)
            zone.SetLayer(k.B_Cu)
            zone.SetNet(board.FindNet('/GND'))
            zone.SetLocalClearance(200000)
            zone.SetPadConnection(k.ZONE_CONNECTION_FULL)
            zone.Outline().NewOutline()
            for xy in ((1, 1), (49, 1), (49, 29), (1, 29)):
                zone.Outline().Append(vec(*xy))
            board.Add(zone)  # Intentionally unfilled: saved stage boundary.
        if not k.SaveBoard(str(project / f'{NAME}.kicad_pcb'), board, True):
            raise RuntimeError(f'failed to save {variant}')
    shutil.rmtree(common)
    write_json('/work/emission.json', result)
    write_json('/work/sources.json', {
        'purpose': 'Original alternative; control-derived AGENTEE-103 fixtures remain owner-gated.',
        'pcbnew_version': k.Version(),
        'kicad_cli_version': subprocess.check_output(['kicad-cli', 'version'], text=True).strip(),
        'sources': {'generate.py': digest(HERE / 'generate.py'),
                    **{path: hashlib.sha256(data).hexdigest() for path, data in libraries.items()},
                    **{f'agent_ee/{module}.py': digest(Path('/app/agent_ee') / f'{module}.py')
                       for module in ('schematic_emitter', 'connectivity_ir', 'board_build', 'symbol_library')}},
    })
    write_json('/work/fixture-hashes.json', {
        str(path.relative_to(out)): digest(path) for path in sorted(out.rglob('*')) if path.is_file()})


def generate(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    record = execute(HERE, out / 'generation',
        ['/usr/bin/python3', '-B', '/candidate/generate.py', '--native'], timeout=120,
        extra_ro=[(HERE.parents[2] / 'fixtures/generation/project/lib', '/owned-libraries')])
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
