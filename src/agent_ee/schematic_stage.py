"""Receipted schematic stage from writer-authored, captured connectivity intent.

The existing emitter and forward annotator own all resolution and native edits.
This module supplies their ordered campaign steps, not an intent compiler.
"""
import argparse
from pathlib import Path

from . import native_run
from . import forward_annotation, schematic_emitter
from .schematic_regenerate import regenerate_schematic
from .connectivity_ir import _read_json

from .footprint_library import load_document, read_captures
from .stage_receipts import _relative, validate_step
from .storage import write_json
from .tool_source import snapshot


def _sheets(schematic, child_schematics):
    """Explicit native output set; the emitter currently uses sibling files."""
    if not isinstance(child_schematics, (list, tuple)) or len(child_schematics) > 4095:
        raise ValueError('child schematics must be a bounded sequence of native paths')
    paths = [schematic, *child_schematics]
    for path in paths:
        _relative(path, 'schematic sheet path')
        if Path(path).suffix != '.kicad_sch' or Path(path).parent != Path(schematic).parent:
            raise ValueError('schematic sheets must be sibling native schematic files')
    if len(set(paths)) != len(paths):
        raise ValueError('schematic sheet paths must be distinct')
    return paths


def configuration(source_root, *, board, schematic, intent='generation/connectivity-intent.json',
                  seeds=(), layout='generation/schematic-layout.json', captures='libraries',
                  footprints='generation/footprints.json', regeneration_intent=None,
                  child_schematics=()):
    """Scope mutable_inputs and campaign steps for the declared native sheet set.

    Seeds are writer context, captured and bound like the intent; they are not
    interpreted as a second connectivity language. Libraries, layout, parts and
    requirements stay immutable. All files must exist in the initial capture.
    """
    sheets = _sheets(schematic, child_schematics)
    paths = [board, *sheets, intent, layout, captures, footprints, *seeds]
    for path in [board, *sheets, intent, layout, footprints, *seeds]:
        _relative(path, 'schematic stage path')
    if captures != '.':
        _relative(captures, 'schematic capture root')
    if len(set([board, *sheets, intent, layout, footprints, *seeds])) != len(paths) - 1:
        raise ValueError('schematic stage file paths must be distinct')
    if Path(board).suffix != '.kicad_pcb' or Path(schematic).suffix != '.kicad_sch':
        raise ValueError('schematic stage requires native board and schematic paths')
    args = ['--board', board, '--schematic', schematic, '--intent', intent, '--layout', layout,
            '--captures', captures, '--footprints', footprints]
    for child in child_schematics:
        args += ['--child-schematic', child]
    if regeneration_intent is not None:
        if child_schematics:
            raise ValueError('preserving native edits during regeneration currently requires a flat schematic')
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
        if command == 'emit':
            writes.update({child: 'out/project/' + Path(child).name for child in child_schematics})
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
            'mounts': [{'source': str(snapshot(source_root)), 'at': '/tool/agent-ee'}],
            'source': '/tool/agent-ee', 'version_argv': ['/usr/bin/kicad-cli', 'version'],
            'inputs': inputs,
            'writes': writes, 'artifacts': artifacts,
            'adoptable_exits': [0], 'timeout_s': 300}))
    return {'mutable_inputs': [intent, *seeds, *sheets, board,
                              *([regeneration_intent] if regeneration_intent is not None else [])],
            'implementation_steps': steps}


def run(command, candidate, out, *, board, schematic, intent, layout, captures, footprints,
        regeneration_intent=None, child_schematics=(), _execution=None):
    """Return failure with retained findings; never substitute previous native output."""
    candidate, out = Path(candidate), Path(out)
    sheets = _sheets(schematic, child_schematics)
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
    if set(p for p in files if p.endswith('.kicad_sch')) != {Path(p).name for p in sheets}:
        raise ValueError('emitted native sheet set differs from declared schematic outputs')
    document = _read_json(raw)
    if document['schema'] == 'agent-ee.connectivity-intent/2':
        root = next(sheet['file'] for sheet in document['hierarchy'] if sheet['parent'] is None)
        if root != Path(schematic).name:
            raise ValueError('emitted root sheet differs from configured schematic')
    if command == 'emit':
        schematic_emitter.publish(files, out / 'project')
        if regeneration_intent is not None:
            write_json(out / 'effective-intent.json', emission['effective_intent'])
        return 0
    if command != 'annotate':
        raise ValueError('unknown schematic stage command')
    for sheet in sheets:
        if (candidate / sheet).read_bytes() != files[Path(sheet).name]:
            raise ValueError('forward annotation schematic differs from current intent emission: ' + sheet)
    native = native_run.run(
        native_run.netlist('/candidate/' + schematic,
            str(out / 'netlist/design.xml') if _execution is not None else '/work/design.xml',
            timeout=120, isolation='host' if _execution is not None else 'sandbox'),
        inputs=candidate,
        work=out / 'netlist',
    )
    if native['execution'] != 'ok':
        return 1
    manifest = load_document(candidate / footprints)
    plan = forward_annotation.plan(candidate / board, out / 'netlist/design.xml', manifest,
                                   candidate / captures, out / 'plan-work', _execution=_execution)
    write_json(out / 'plan.json', plan)
    if plan['execution'] != 'ok':
        return 1
    result = forward_annotation.apply(plan, candidate / board, out / 'netlist/design.xml', manifest,
        candidate / captures, out / 'board.kicad_pcb', out / 'apply-work', _execution=_execution)
    write_json(out / 'annotation.json', result)
    return 0 if result['execution'] == 'ok' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('emit', 'annotate'))
    for name in ('board', 'schematic', 'intent', 'layout', 'captures', 'footprints'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--regeneration-intent')
    parser.add_argument('--child-schematic', action='append', default=[], dest='child_schematics')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    # Only this fixed entrypoint selects children that inherit the receipted sandbox.
    execution = forward_annotation._StageExecution() if command == 'annotate' else None
    return run(command, '/candidate', '/work/out', _execution=execution, **args)


if __name__ == '__main__':
    raise SystemExit(main())
