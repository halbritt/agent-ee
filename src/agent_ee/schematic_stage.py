"""Receipted schematic stage from writer-authored, captured connectivity intent.

The existing emitter and forward annotator own all resolution and native edits.
This module supplies their ordered campaign steps, not an intent compiler.
"""
import argparse
from pathlib import Path

from . import forward_annotation, schematic_emitter
from .schematic_regenerate import regenerate_schematic
from .connectivity_ir import _read_json
from .executor import execute
from .footprint_library import load_document, read_captures
from .stage_receipts import _relative, validate_step
from .storage import write_json


def configuration(source_root, *, board, schematic, intent='generation/connectivity-intent.json',
                  seeds=(), layout='generation/schematic-layout.json', captures='libraries',
                  footprints='generation/footprints.json', regeneration_intent=None):
    """Scope mutable_inputs and campaign implementation_steps for a flat schematic stage.

    Seeds are writer context, captured and bound like the intent; they are not
    interpreted as a second connectivity language. Libraries, layout, parts and
    requirements stay immutable. All files must exist in the initial capture.
    """
    paths = [board, schematic, intent, layout, captures, footprints, *seeds]
    for path in paths:
        _relative(path, 'schematic stage path')
    if len(set([board, schematic, intent, layout, footprints, *seeds])) != len(paths) - 1:
        raise ValueError('schematic stage file paths must be distinct')
    if Path(board).suffix != '.kicad_pcb' or Path(schematic).suffix != '.kicad_sch':
        raise ValueError('schematic stage requires native board and schematic paths')
    args = ['--board', board, '--schematic', schematic, '--intent', intent, '--layout', layout,
            '--captures', captures, '--footprints', footprints]
    if regeneration_intent is not None:
        _relative(regeneration_intent, 'previous emitted intent path')
        if regeneration_intent in paths:
            raise ValueError('previous emitted intent path must be distinct')
        args += ['--regeneration-intent', regeneration_intent]
    steps = []
    for command, name, target, produced, artifacts in (
        ('emit', 'emit-schematic', schematic, 'out/project/' + Path(schematic).name,
         ['out/emission.json', 'out/project']),
        ('annotate', 'forward-annotate', board, 'out/board.kicad_pcb',
         ['out/emission.json', 'out/netlist', 'out/plan.json', 'out/annotation.json']),
    ):
        writes = {target: produced}
        inputs = [intent, *seeds, layout, footprints]
        if regeneration_intent is not None:
            if command == 'emit':
                writes[regeneration_intent] = 'out/effective-intent.json'
                artifacts += ['out/effective-intent.json']
            else:
                inputs += [regeneration_intent]
        steps.append(validate_step({
            'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': name,
            'argv': ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
                     '-m', 'agent_ee.schematic_stage', command, *args],
            'mounts': [{'source': str(Path(source_root).absolute()), 'at': '/tool/agent-ee'}],
            'source': '/tool/agent-ee', 'version_argv': ['/usr/bin/kicad-cli', 'version'],
            'inputs': inputs,
            'writes': writes, 'artifacts': artifacts,
            'adoptable_exits': [0], 'timeout_s': 300}))
    return {'mutable_inputs': [intent, *seeds, schematic, board,
                              *([regeneration_intent] if regeneration_intent is not None else [])],
            'implementation_steps': steps}


def run(command, candidate, out, *, board, schematic, intent, layout, captures, footprints,
        regeneration_intent=None):
    """Return failure with retained findings; never substitute previous native output."""
    candidate, out = Path(candidate), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    raw = (candidate / intent).read_bytes()
    try:
        libraries = read_captures(candidate / captures, [lib['path'] for lib in _read_json(raw)['libraries']])
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        # The existing resolver reports absent or invalid capture coverage.
        libraries = {}
    if regeneration_intent is None:
        emission, files = schematic_emitter.emit_schematic(raw, libraries, (candidate / layout).read_bytes())
    else:
        emission, files = regenerate_schematic((candidate / schematic).read_bytes(),
            (candidate / regeneration_intent).read_bytes(), raw, libraries, (candidate / layout).read_bytes())
    write_json(out / 'emission.json', emission)
    if files is None:
        return 1
    if set(p for p in files if p.endswith('.kicad_sch')) != {Path(schematic).name}:
        raise ValueError('schematic stage currently requires one flat sheet with the configured filename')
    if command == 'emit':
        schematic_emitter.publish(files, out / 'project')
        if regeneration_intent is not None:
            write_json(out / 'effective-intent.json', emission['effective_intent'])
        return 0
    if command != 'annotate':
        raise ValueError('unknown schematic stage command')
    if (candidate / schematic).read_bytes() != files[Path(schematic).name]:
        raise ValueError('forward annotation schematic differs from current intent emission')
    native = execute(candidate, out / 'netlist', ['kicad-cli', 'sch', 'export', 'netlist',
        '--format', 'kicadxml', '-o', '/work/design.xml', '/candidate/' + schematic], timeout=120)
    if native['execution'] != 'ok':
        return 1
    manifest = load_document(candidate / footprints)
    plan = forward_annotation.plan(candidate / board, out / 'netlist/design.xml', manifest,
                                   candidate / captures, out / 'plan-work')
    write_json(out / 'plan.json', plan)
    if plan['execution'] != 'ok':
        return 1
    result = forward_annotation.apply(plan, candidate / board, out / 'netlist/design.xml', manifest,
        candidate / captures, out / 'board.kicad_pcb', out / 'apply-work')
    write_json(out / 'annotation.json', result)
    return 0 if result['execution'] == 'ok' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('emit', 'annotate'))
    for name in ('board', 'schematic', 'intent', 'layout', 'captures', 'footprints'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--regeneration-intent')
    args = vars(parser.parse_args(argv))
    return run(args.pop('command'), '/candidate', '/work/out', **args)


if __name__ == '__main__':
    raise SystemExit(main())
