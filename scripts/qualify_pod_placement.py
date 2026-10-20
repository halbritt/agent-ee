#!/usr/bin/env python3
"""Offline, owned pod-scale placement qualification; no design acceptance claim.

Generate original heterogeneous two-pad mechanical stand-ins, emit a schematic
from the repository's owned CAP symbol, export native XML and forward-annotate.
Both sides are assigned explicitly before running the existing sandboxed writer.
All generated inputs and tool records stay under a fresh --out directory.
"""
import argparse
from collections import Counter
import shutil
import time
from pathlib import Path

from agent_ee import forward_annotation as fa, placement_adapter as adapter
from agent_ee import placement_predicates as predicates
from agent_ee.placement_check import evaluate
from agent_ee.executor import execute
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.storage import digest, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
CASES = ((80, 50, 90), (85, 55, 112), (90, 65, 112))
MM = 1_000_000


def require_ok(record, label):
    if record['execution'] != 'ok':
        raise RuntimeError(f'{label}: {record}')


def footprint(name, width, height):
    """Original mechanical placeholder, never a vendor package model."""
    return (f'(footprint "{name}" (version 20260206) (generator "pcbnew") (layer "F.Cu") '
            '(attr smd) (property "Reference" "REF**" (at 0 0) (layer "F.Fab") '
            '(effects (font (size 1 1)))) '
            f'(fp_rect (start {-width/2} {-height/2}) (end {width/2} {height/2}) '
            '(stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd")) '
            '(pad "1" smd rect (at -0.3 0) (size 0.3 0.3) (layers "F.Cu" "F.Mask" "F.Paste")) '
            '(pad "2" smd rect (at 0.3 0) (size 0.3 0.3) (layers "F.Cu" "F.Mask" "F.Paste")))\n')


def empty_board(path, width, height):
    import pcbnew as k
    board = k.BOARD()
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    for i, (x, y) in enumerate(corners):
        end = corners[(i + 1) % 4]
        shape = k.PCB_SHAPE(board)
        shape.SetShape(k.SHAPE_T_SEGMENT)
        shape.SetLayer(k.Edge_Cuts)
        shape.SetStart(k.VECTOR2I(x * MM, y * MM))
        shape.SetEnd(k.VECTOR2I(end[0] * MM, end[1] * MM))
        shape.SetWidth(50_000)
        board.Add(shape)
    zone = k.ZONE(board)
    zone.SetIsRuleArea(True)
    zone.SetZoneName('owned-48x21-clearance')
    zone.SetDoNotAllowFootprints(True)
    zone.SetDoNotAllowTracks(False)
    zone.SetDoNotAllowVias(False)
    zone.SetDoNotAllowPads(False)
    layers = k.LSET()
    layers.AddLayer(k.F_Cu)
    layers.AddLayer(k.B_Cu)
    zone.SetLayerSet(layers)
    zone.Outline().NewOutline()
    for x, y in ((-24, -10.5), (24, -10.5), (24, 10.5), (-24, 10.5)):
        zone.Outline().Append(round((width/2 + x) * MM), round((height/2 + y) * MM))
    board.Add(zone)
    k.SaveBoard(str(path), board)


def prepare(root, case, oversized=False):
    import pcbnew as k
    width, height, count = case
    inputs = root / 'inputs'
    library = inputs / 'OwnedScale.pretty'
    library.mkdir(parents=True)
    symbol = ROOT / 'fixtures/generation/project/lib/Owned_Gen.kicad_sym'
    shutil.copyfile(symbol, inputs / 'Owned_Gen.kicad_sym')
    sizes = {'small': (2, 1.5), 'medium': (3.5, 2.5), 'large': (7, 5), 'module': (18, 14),
             'connector': (25, 10)}
    if oversized:
        sizes['oversized'] = (100, 2)
    for name, (w, h) in sizes.items():
        (library / (name + '.kicad_mod')).write_text(footprint(name, w, h))
    manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=[dict(
        name='OwnedScale', members=[dict(path=str(p.relative_to(inputs)), sha256=digest(p))
                                   for p in sorted(library.glob('*.kicad_mod'))])])
    components = []
    for i in range(count):
        name = ('connector' if i == 0 else 'module' if i < 3 else 'large' if i < 11 else
                'medium' if i < 31 else 'small')
        if oversized and i == 0:
            name = 'oversized'
        components.append(dict(id=f'c{i+1}', reference=f'C{i+1}', lib_id='OwnedGen:CAP',
                               value='owned-' + name, footprint='OwnedScale:' + name, units=[1]))
    intent = dict(schema='agent-ee.connectivity-intent/1', libraries=[dict(name='OwnedGen',
        path='Owned_Gen.kicad_sym', sha256=digest(symbol))], components=components, no_connects=[],
        nets=[dict(id=net.lower(), name=net, terminals=[dict(component=c['id'], pin=dict(number=pin))
              for c in components]) for net, pin in (('VCC', '1'), ('GND', '2'))])
    layout = dict(schema='agent-ee.schematic-layout/1', namespace='855dbf8e-ebcb-40d0-80f0-f6870144d912',
                  project='pod-scale', transforms={})
    write_json(inputs / 'intent.json', intent)
    write_json(inputs / 'footprints.json', manifest)
    emitted, files = emit_schematic(intent, {'Owned_Gen.kicad_sym': symbol.read_bytes()}, layout)
    write_json(root / 'emission.json', emitted)
    require_ok(emitted, 'emission')
    for name, data in files.items():
        (inputs / name).write_bytes(data)
    execution = execute(inputs, root / 'xml', ['kicad-cli', 'sch', 'export', 'netlist', '--format',
        'kicadxml', '-o', '/work/native.xml', '/candidate/pod-scale.kicad_sch'], timeout=120)
    require_ok(execution, 'native XML export')
    shutil.copyfile(root / 'xml/native.xml', inputs / 'netlist.xml')
    board = inputs / 'board.kicad_pcb'
    empty_board(board, width, height)
    plan = fa.plan(board, inputs / 'netlist.xml', manifest, inputs, root / 'annotation-plan')
    require_ok(plan, 'annotation plan')
    annotated = root / 'annotated.kicad_pcb'
    applied = fa.apply(plan, board, inputs / 'netlist.xml', manifest, inputs, annotated, root / 'annotation-apply')
    require_ok(applied, 'annotation apply')
    native = k.LoadBoard(str(annotated))
    for fp in native.GetFootprints():
        if int(fp.GetReference()[1:]) % 2 == 0:
            fp.Flip(fp.GetPosition(), False)
    k.SaveBoard(str(board), native)
    return inputs


def attempt(inputs, work):
    policy = dict(schema='agent-ee.packing-policy/1', step_budget=200000, gap_nm=250000,
                  min_courtyard_edge_mm=0.5, fixed_refs=[], priority_refs=[], ground_nets=['GND'], obstacles=[])
    request = adapter.helper_request((inputs / 'board.kicad_pcb').read_bytes(), policy, [])
    request['authorization']['writable_root'] = '/work'
    write_json(inputs / 'request.json', request)
    start = time.monotonic()
    execution = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.placer_runner',
        '/candidate/board.kicad_pcb', '/candidate/netlist.xml', '--request', '/candidate/request.json',
        '--destination', '/work/proposal', '--result', '/work/result.json'], timeout=600)
    elapsed = time.monotonic() - start
    # Even typed not-placed results return nonzero: retain their actual execution.
    report = read_json(work / 'result.json') if (work / 'result.json').is_file() else None
    row = dict(runtime_seconds=elapsed, execution=execution, result=report, placed_count=0,
               verdicts=None, candidate_sha256=None, placement=None)
    if report is not None and report['status'] == 'placed' and execution['execution'] == 'ok':
        probe = read_json(work / 'proposal/placement-probe.json')
        drc = predicates.validate_drc(read_json(work / 'proposal/native-drc.json'))
        grades = [evaluate(criterion, probe, drc, {'min_courtyard_edge_mm': 0.5}) for criterion in (
            'courtyard-overlap/1', 'outline-containment/1', 'edge-clearance/1', 'footprint-keepout/1')]
        write_json(work / 'placement-verdicts.json', grades)
        row.update(placed_count=len(probe['footprints']), sides=dict(Counter(f['side'] for f in probe['footprints'])),
                   verdicts={g['criterion']: g['status'] for g in grades},
                   candidate_sha256=digest(work / 'proposal/candidate.kicad_pcb'),
                   placement=[{key: fp[key] for key in ('ref', 'uuid', 'side', 'position_nm', 'rotation_deg')}
                              for fp in sorted(probe['footprints'], key=lambda f: f['ref'])])
    return row


def qualify(out, cases=CASES):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    record = dict(meaning='Owned mechanical stand-ins only; not telemetry-pod design acceptance.', cases=[])
    for width, height, count in cases:
        root = out / f'{width}x{height}-{count}'
        inputs = prepare(root, (width, height, count))
        before = {str(p.relative_to(inputs)): digest(p) for p in inputs.rglob('*') if p.is_file()}
        runs = [attempt(inputs, root / f'run-{i}') for i in (1, 2)]
        # request.json is coordinator output, not a generated design input.
        unchanged = all(digest(inputs / p) == sha for p, sha in before.items())
        deterministic = (runs[0]['placement'] is not None and runs[0]['placement'] == runs[1]['placement']
                         and runs[0]['candidate_sha256'] == runs[1]['candidate_sha256'])
        row = dict(outline_mm=[width, height], footprint_count=count, keepout_mm=[48, 21], runs=runs,
                   deterministic=deterministic, inputs_unchanged=unchanged)
        record['cases'].append(row)
        write_json(out / 'qualification.json', record)
    inputs = prepare(out / 'oversized', (80, 50, 90), oversized=True)
    record['oversized'] = attempt(inputs, out / 'oversized/run')
    failure = record['oversized']['result']
    record['qualified'] = (all(c['deterministic'] and c['inputs_unchanged'] and all(
        r['placed_count'] == c['footprint_count'] and set(r['verdicts'].values()) == {'pass'}
        and set(r['sides']) == {'F', 'B'} for r in c['runs']) for c in record['cases'])
        and failure is not None and failure['status'] == 'packing_infeasible'
        and 'C1' in failure['reason'] and not (out / 'oversized/run/proposal').exists())
    write_json(out / 'qualification.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    record = qualify(args.out)
    print(f"qualified={record['qualified']}; record={args.out / 'qualification.json'}")
    return 0 if record['qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
