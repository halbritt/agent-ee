"""Fixed expected-GND input for the finite acquisition ADDR development case.

A root-pinned ordinary control receipt is trusted local admission, not
authentication against a host able to forge both that receipt and its pin.
"""
import copy
from pathlib import Path
from hashlib import sha256

from . import parts_manifest, parts_stage, stage_receipts, storage, tool_source, typed_overlay, requirement_map
from .catalog import strict_json

EXPECTED = 'fixedexpected/connectivity-intent-addr-gnd.json'
GENERATED = 'build-generated/connectivity-intent.json'
CHECKS = {'topology', 'review-acq-elec-1-topology', 'review-acq-elec-3-topology',
          'review-acq-elec-4-topology', 'review-acq-elec-5-topology',
          'review-acq-elec-6-remaining-topology'}


def admit(intent_path, intent_sha256, receipt_path, receipt_sha256, read):
    """Replay the existing complete receipt; never execute its tool or version probe."""
    intent_raw, intent_stamp = read(intent_path, 'expected intent')
    receipt_raw, receipt_stamp = read(receipt_path, 'expected parts receipt')
    if sha256(intent_raw).hexdigest() != intent_sha256:
        raise ValueError('expected intent hash mismatch')
    if sha256(receipt_raw).hexdigest() != receipt_sha256:
        raise ValueError('expected parts receipt hash mismatch')
    pinned = strict_json(receipt_raw)
    if not isinstance(pinned, dict):
        raise ValueError('expected a pinned ordinary parts receipt')
    link = dict(path=str(Path(receipt_path).absolute()), sha256=receipt_sha256,
                binding=pinned.get('binding'))
    stage_receipts.validate_pre_receipts([link])
    receipt = stage_receipts.validate_pre_receipt(Path('/'), link)
    spec = receipt['spec']
    prefix = ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
              '-m', 'agent_ee.parts_stage']
    if (receipt['status'] != 'completed' or receipt['exit_code'] != 0
            or spec['stage'] != 'parts' or spec['step'] != 'compile-parts'
            or spec['argv'][:7] != prefix or spec['source'] != '/tool/agent-ee'
            or spec['writes'] != {GENERATED: 'out/connectivity-intent.json'}
            or receipt['outputs'] != {GENERATED: intent_sha256}
            or Path(intent_path).absolute() != Path(receipt_path).absolute().parent / 'run/out/connectivity-intent.json'):
        raise ValueError('expected intent must be the exact successful ordinary control parts output')
    current = tool_source._files(Path(parts_stage.__file__).resolve().parents[1])
    source = receipt['tool']['source_tree']
    if (source != dict(path='/tool/agent-ee', sha256=storage.identity(current), files=len(current))
            or receipt['tool_after']['source_tree'] != source
            or receipt['binding'].get('source_candidate_id') != storage.identity(receipt['inputs'])):
        raise ValueError('expected parts receipt producer or control input identity mismatch')
    report_path = Path(receipt_path).absolute().parent / 'run/out/report.json'
    report_raw, report_stamp = read(report_path, 'expected parts report')
    report = strict_json(report_raw)
    if (receipt['artifacts'].get('out/report.json') != sha256(report_raw).hexdigest()
            or report.get('schema') != parts_stage.SCHEMA or report.get('status') != 'produced'
            or report.get('output_sha256') != intent_sha256
            or report.get('intent_sha256') != storage.identity(strict_json(intent_raw))):
        raise ValueError('expected intent differs from the receipted parts report')
    return dict(raw=intent_raw, receipt_raw=receipt_raw, report_raw=report_raw,
                receipt=receipt, report=report, intent_stamp=intent_stamp,
                receipt_stamp=receipt_stamp, report_stamp=report_stamp, report_path=report_path)


def bind(root, plan, context_report, overlay, captured):
    """Bind this new case's unchanged control sources before the optional terminal move."""
    root = Path(root)
    receipt, report = captured['receipt'], captured['report']
    optional = [arg for name in ('deviations', 'ratings') if name in plan['parts']
                for arg in ('--' + name, plan['parts'][name])]
    argv = ['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
            '-m', 'agent_ee.parts_stage', '--bundle', '/candidate',
            '--manifest', plan['parts']['manifest'], '--circuit', plan['parts']['circuit'],
            '--layout', plan['parts']['layout'], '--out', '/work/out', *optional, '--adopt-as', GENERATED]
    if receipt['spec']['argv'] != argv:
        raise ValueError('expected parts receipt has a different control parts declaration')
    dependencies = report.get('dependencies')
    if not isinstance(dependencies, dict) or not set(plan['parts'].values()) <= dependencies.keys():
        raise ValueError('expected parts receipt lacks complete control dependencies')
    provenance = {requirement_map.PROVENANCE + '/' + n:n for n in
                  ('spec.md','spec-record.json','requirement-map.json','obligations.json')}
    for name, expected in dependencies.items():
        actual = sha256(parts_manifest.read_captured(root, provenance.get(name, name))).hexdigest()
        if receipt['inputs'].get(name) != expected or actual != expected:
            raise ValueError('expected parts control dependency mismatch: ' + name)
    # Context/decision bytes bind the clock and the entire admitted capture closure.
    context = dict(context_report['inputs'])
    # The existing requirements stage captures exactly these source copies before parts.
    context.update({dest:context[src] for dest,src in provenance.items()})
    if GENERATED in receipt['inputs']:
        context[GENERATED] = sha256(b'{}\n').hexdigest()  # Existing build parts-stage target placeholder.
    if receipt['inputs'] != context:
        raise ValueError('expected parts receipt has a different captured preparation context')
    declaration = strict_json((root / 'requirement-map.json').read_bytes())
    scope = declaration['scope']
    forbidden = [scope['board'], scope['schematic'], *scope.get('mutable_inputs', []),
                 *(s['path'] for s in scope.get('stages', {}).get('subjects', {}).values())]
    if any(EXPECTED == p or EXPECTED.startswith(p + '/') or p.startswith('fixedexpected/') for p in forbidden):
        raise ValueError('expected intent cannot be a native or mutable stage path')
    pool = typed_overlay._check_pool(typed_overlay.derive_map(copy.deepcopy(declaration), overlay))
    parity = {name for name, spec in pool.items() if spec['kind'] == 'netlist_parity'}
    if parity != CHECKS or any(pool[n]['params']['source'] != dict(kind='intent', path=GENERATED) for n in parity):
        raise ValueError('expected exactly the six unchanged acquisition parity source declarations')
    # All occurrences, including repeated equal declarations, retain every other field.
    def replace(value):
        if isinstance(value, dict):
            if value.get('kind') == 'netlist_parity' and value.get('id') in CHECKS:
                value['params']['source']['path'] = EXPECTED
            for child in value.values():
                replace(child)
        elif isinstance(value, list):
            for child in value:
                replace(child)
    replace(declaration)
    (root / 'fixedexpected').mkdir()
    for name, raw in ((Path(EXPECTED).name, captured['raw']),
                      ('parts.receipt.json', captured['receipt_raw']), ('parts.report.json', captured['report_raw'])):
        (root / 'fixedexpected' / name).write_bytes(raw)
    declaration['scope']['inputs'].append(dict(path='fixedexpected', at='fixedexpected'))
    (root / 'requirement-map.json').write_bytes(storage.json_bytes(declaration))
    overlay['base']['requirement_map_sha256'] = storage.digest(root / 'requirement-map.json')
    return dict(path=EXPECTED, sha256=sha256(captured['raw']).hexdigest(),
                receipt_sha256=sha256(captured['receipt_raw']).hexdigest(),
                receipt_identity=receipt['identity'], binding=receipt['binding'], context_sha256=storage.identity(context),
                checks=sorted(parity), trust='Root-pinned local control receipt; not host-forgery authentication')
