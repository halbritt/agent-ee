"""Captured CAN pair software probe; never an actual pod acceptance run."""
import argparse
import json
from pathlib import Path
import shutil
import time

from agent_ee import candidate, net_constraints, routing_adapter, stage_receipts
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.checks import run_checks
from agent_ee.executor import execute
from agent_ee.storage import digest, read_json, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def prepare(root):
    root = Path(root)
    root.mkdir()
    project = root / 'project'
    project.mkdir()
    for name in ('net-constraints.json', 'routing-rules.json', 'requirements.md'):
        shutil.copyfile(HERE / name, root / name)
    produced = net_constraints.emit((root / 'net-constraints.json').read_bytes(),
        (REPO / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
        rules_raw=(HERE / 'gap.kicad_dru').read_bytes())
    if 'project' not in produced:
        raise ValueError('CAN probe constraints did not emit')
    for key, name in [('project', 'project/can.kicad_pro'), ('rules', 'project/can.kicad_dru'),
                      ('record', 'net-constraints.record.json')]:
        (root / name).write_bytes(produced[key])
    made = execute(HERE, root.parent / 'make-board',
        ['/usr/bin/python3', '-I', '-B', '/candidate/board.py', '/work/can.kicad_pcb'], timeout=120)
    if made['exit_code'] != 0:
        raise ValueError('native CAN probe board generation failed')
    shutil.copyfile(root.parent / 'make-board/can.kicad_pcb', project / 'can.kicad_pcb')
    (project / 'can.kicad_sch').write_text('(kicad_sch (version 20250114) (generator "eeschema") '
        '(uuid "cf5ae123-16c3-4a18-9ded-19e55c5a2e24") (paper "A4") (lib_symbols))\n')
    scope = {'schema': 'agent-ee.scope/3', 'name': 'CAN pair routing software probe',
        'scope': 'Native two-terminal CAN lengths and skew only; no actual pod or schematic parity claim.',
        'board': 'project/can.kicad_pcb', 'schematic': 'project/can.kicad_sch',
        'inputs': [{'path': name, 'at': name} for name in ('project', 'requirements.md',
            'routing-rules.json', 'net-constraints.json', 'net-constraints.record.json')],
        'requirements': ['requirements.md'],
        'checks': [{'id': 'can-routing', 'kind': 'routing_rules',
                    'params': {'routing_rules': 'routing-rules.json'}}],
        'baseline': {'requirements': [{'id': 'can-probe-routing', 'intent': 'acceptance',
            'statement': 'Meet the declared native CAN probe length and skew bounds.',
            'source': {'path': 'requirements.md', 'locator': 'can-probe-routing'},
            'checks': ['can-routing']}], 'targets': ['requirements.md', 'net-constraints.json',
            'net-constraints.record.json'], 'reference_architectures': ['requirements.md']},
        'limitations': ['Software probe, not a telemetry pod design or source qualification.',
            'Empty schematic; no schematic/PCB parity acceptance.',
            'No impedance, propagation delay, complete-pod plane topology or bench qualification.']}
    write_json(root / 'scope.json', scope)
    return scope


def evaluate(root, out):
    out = Path(out)
    out.mkdir()
    candidate.capture(Path(root) / 'scope.json', out)
    results = run_checks(out, time.monotonic() + 180)
    freeze_evidence(out, results)
    verify_evidence(out, results, required=True)
    return results[0]


def route(root, receipt_dir, checkout, *, pairs=True):
    spec = routing_adapter.step(project='project', board='can.kicad_pcb',
        schematic='can.kicad_sch', name='can', label='can-pair-probe', checkout=checkout,
        budget=120, **({'diff_pairs': 'auto'} if pairs else {}))
    receipt = stage_receipts.run_step(receipt_dir, root, spec,
        {'implementation': 1, 'receipt_index': 1})
    stage_receipts.validate_receipt(receipt_dir)
    summary = routing_adapter.verify(Path(receipt_dir) / 'run', receipt=receipt)
    if summary['proposal'] in ('eligible', 'eligible_partial'):
        shutil.copyfile(Path(receipt_dir) / 'run/routing/candidate/project/can.kicad_pcb',
                        Path(root) / 'project/can.kicad_pcb')
    return summary


# Replace the routed straight CAN+ leg with a connected path exactly 5 mm longer.
# Preserve its native endpoints inside the pads; two 2.5 mm legs add copper length.
LENGTHEN = r'''
import sys, pcbnew as k
b=k.LoadBoard(sys.argv[1]); tracks=[t for t in b.GetTracks() if t.GetNetname()=='CAN+']
assert len(tracks)==1 and tracks[0].GetClass()=='PCB_TRACK'
track=tracks[0]; old=track.GetLength()
a,c=sorted([track.GetStart(),track.GetEnd()],key=lambda p:p.x)
assert a.y==c.y and c.x-a.x>6000000
x1,x2,y=a.x/1e6,c.x/1e6,a.y/1e6
b.Delete(track)
points=[(x1,y),(x1+2,y),(x1+2,y-2.5),(x2-2,y-2.5),(x2-2,y),(x2,y)]
for a,c in zip(points,points[1:]):
    t=k.PCB_TRACK(b);t.SetNet(b.FindNet('CAN+'));t.SetLayer(k.F_Cu);t.SetWidth(250000)
    t.SetStart(k.VECTOR2I(*(round(v*1e6) for v in a)))
    t.SetEnd(k.VECTOR2I(*(round(v*1e6) for v in c)));b.Add(t)
assert sum(t.GetLength() for t in b.GetTracks() if t.GetNetname()=='CAN+')==old+5000000
k.SaveBoard(sys.argv[2],b)
'''


def run(out, checkout):
    out = Path(out)
    out.mkdir()
    root = out / 'candidate'
    prepare(root)
    before = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*') if p.is_file()}
    summary = route(root, out / 'route', checkout)
    positive = evaluate(root, out / 'positive')
    mutation = execute(root, out / 'lengthen', ['/usr/bin/python3', '-I', '-B', '-c', LENGTHEN,
        '/candidate/project/can.kicad_pcb', '/work/can.kicad_pcb'], timeout=120)
    if mutation['exit_code'] != 0:
        raise ValueError('controlled five-millimetre extension failed')
    shutil.copyfile(out / 'lengthen/can.kicad_pcb', root / 'project/can.kicad_pcb')
    negative = evaluate(root, out / 'negative')
    after = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*') if p.is_file()}
    assert {k for k in before if before[k] != after[k]} == {'project/can.kicad_pcb'}
    pair_result = read_json(out / 'route/run/routing/attempt/result.json')
    record = {'schema': 'agent-ee.can-pair-probe/1', 'routing': summary,
        'pair_result': pair_result.get('pairs'),
        'positive': {'execution': positive['execution'], 'outcome': positive['outcome']},
        'negative': {'execution': negative['execution'], 'outcome': negative['outcome'],
                     'violations': negative['details'].get('violations')},
        'acceptance_ready': False, 'limitations': read_json(root / 'scope.json')['limitations']}
    write_json(out / 'probe-result.json', record)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--checkout', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.checkout), indent=2))
