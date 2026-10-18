"""Owner-recorded, list-approved dispositions for exact native ERC/DRC violations."""
import math
import re
from pathlib import Path
from uuid import UUID

from .candidate import safe_relative
from .catalog import exact_fields, nonempty, strict_json
from .storage import digest, identity, read_json

SCHEMA = 'agent-ee.violation-waivers/1'
SHA256 = re.compile(r'[0-9a-f]{64}')


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('waiver items require canonical UUIDs')
    return value


def validate(record):
    exact_fields(record, ('schema', 'waivers', 'approval'))
    if record['schema'] != SCHEMA or not isinstance(record['waivers'], list):
        raise ValueError('expected ' + SCHEMA)
    approval = record['approval']
    exact_fields(approval, ('owner', 'disposition', 'waivers_sha256', 'reference'))
    nonempty(approval['owner'])
    nonempty(approval['reference'])
    if (approval['disposition'] != 'approved' or
            approval['waivers_sha256'] != identity(record['waivers'])):
        raise ValueError('waiver list lacks owner approval bound to the exact list')
    seen = set()
    for entry in record['waivers']:
        exact_fields(entry, ('check', 'type', 'items', 'location_hash', 'reason', 'owner'))
        if entry['check'] not in ('erc', 'drc'):
            raise ValueError('waivers support only erc and drc')
        for key in ('type', 'reason', 'owner'):
            nonempty(entry[key])
        if entry['owner'] != approval['owner']:
            raise ValueError('waiver owner differs from list approver')
        items = entry['items']
        if not isinstance(items, list) or not items:
            raise ValueError('waiver needs exact nonempty item UUIDs')
        for item in items:
            _uuid(item)
        if items != sorted(set(items)):
            raise ValueError('waiver UUIDs must be unique and sorted')
        if not isinstance(entry['location_hash'], str) or not SHA256.fullmatch(entry['location_hash']):
            raise ValueError('waiver location_hash requires SHA256')
        key = (entry['check'], entry['type'], tuple(items), entry['location_hash'])
        if key in seen:
            raise ValueError('duplicate exact violation waiver')
        seen.add(key)
    return record


def input_path(params, scope, files, inputs=None):
    """Validate authority before capture/checking; a writer cannot mount this namespace RW."""
    if 'waivers' not in params:
        return None
    if scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('waivers require an immutable scope/2 or scope/3 baseline')
    path = str(safe_relative(nonempty(params['waivers'])))
    if path != params['waivers'] or path not in files:
        raise ValueError('waivers must name a captured normalized path')
    root = Path(path).parts[0]
    mutable = scope.get('mutable_inputs', [scope[k] for k in ('board', 'schematic') if k in scope])
    if not isinstance(mutable, list) or any(not isinstance(p, str) for p in mutable):
        raise ValueError('waivers require explicit valid writer authority')
    for item in mutable:
        safe_relative(nonempty(item))
    if any(Path(p).parts[0] == root for p in mutable):
        raise ValueError('waiver namespace is reachable by the writer; use a separate immutable input')
    # Also refuse an alias of the approved file declared as a mutable design input.
    if any(files.get(p) == files[path] for p in mutable):
        raise ValueError('waiver bytes are reachable through a mutable input alias')
    if inputs is not None:
        load(Path(inputs) / path)
    return path


def capture_inputs(scope, files, inputs):
    paths = set()
    for spec in scope.get('checks', []):
        if 'waivers' in spec.get('params', {}):
            if spec.get('kind') not in ('erc', 'drc'):
                raise ValueError('only erc and drc accept violation waivers')
            paths.add(input_path(spec['params'], scope, files, inputs))
    return paths


def load(path):
    return validate(strict_json(Path(path).read_bytes()))


def writer_mounts(run):
    """Whole immutable namespaces overlay the writer copy, including sibling aliases."""
    run = Path(run)
    baseline = read_json(run / 'baseline.json')
    roots = {Path(s['params']['waivers']).parts[0] for s in baseline['criteria']
             if s.get('kind') in ('erc', 'drc') and 'waivers' in s.get('params', {})}
    return [(run / 'inputs' / root, '/work/design/' + root) for root in sorted(roots)]


def violation_key(kind, subject, category, report, violation, sheet=None):
    """Hash raw native positions exactly, including sheet instance, category and version.

    No tolerance, UUID subset, description match or inferred missing location is admitted.
    Invalid or unlocated native items are deliberately ineligible for waiver.
    """
    if (report.get('coordinate_units') != 'mm' or not report.get('kicad_version') or
            violation.get('severity') not in ('error', 'warning') or
            not isinstance(violation.get('type'), str) or not violation['type'] or
            not isinstance(violation.get('description'), str) or not violation['description']):
        return None
    items = violation.get('items')
    if not isinstance(items, list) or not items:
        return None
    located = []
    for item in items:
        if not isinstance(item, dict):
            return None
        if not isinstance(item.get('description'), str) or not item['description']:
            return None
        try:
            uuid = _uuid(item.get('uuid'))
        except ValueError:
            return None
        pos = item.get('pos')
        if (not isinstance(pos, dict) or set(pos) != {'x', 'y'} or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in pos.values())):
            return None
        located.append(dict(uuid=uuid, pos=pos, description=item.get('description')))
    located.sort(key=lambda i: i['uuid'])
    uuids = [i['uuid'] for i in located]
    if len(set(uuids)) != len(uuids):
        return None
    if kind == 'erc' and (not isinstance(sheet, dict) or
                          not isinstance(sheet.get('uuid_path'), str) or not sheet['uuid_path']):
        return None
    location = dict(check=kind, subject=subject, category=category,
                    sheet_uuid_path=sheet['uuid_path'] if kind == 'erc' else None,
                    sheet_path=sheet.get('path') if kind == 'erc' else None,
                    coordinate_units=report['coordinate_units'],
                    kicad_version=report['kicad_version'], items=located,
                    severity=violation['severity'], description=violation.get('description'))
    return dict(check=kind, type=violation['type'], items=uuids, location_hash=identity(location))


def classify(ctx, report, *, locations_trusted=True):
    from .checks import CheckError
    try:
        return _classify(ctx, report, locations_trusted=locations_trusted)
    except (ValueError, OSError) as exc:
        raise CheckError('malformed', f'violation waivers: {exc}') from exc


def _classify(ctx, report, *, locations_trusted):
    """Retain raw counts; separately report exact waived and unwaived native items."""
    kind = ctx.spec['kind']
    path = input_path(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
    record = load(ctx.inputs / path) if path else None
    if path:
        if digest(ctx.inputs / path) != ctx.manifest['files'][path]:
            raise ValueError('captured waiver file changed')
        # Check-owned evidence is sealed with the native report by the existing pipeline.
        from .storage import write_json
        write_json(ctx.work / 'waivers.json', record)
        ctx.evidence('waivers.json')
        ctx.result['details']['waivers_source'] = dict(path=path, sha256=ctx.manifest['files'][path],
                                                       approval=record['approval'])
    entries = record['waivers'] if record else []
    waived, unwaived = [], []
    groups = ([(f'/sheets/{i}/violations', s['violations'], s) for i, s in enumerate(report['sheets'])]
              if kind == 'erc' else [(f'/{c}', report[c], None)
                                    for c in ('violations', 'unconnected_items', 'schematic_parity')])
    for category, violations, sheet in groups:
        for index, violation in enumerate(violations):
            key = violation_key(kind, ctx.scope['schematic' if kind == 'erc' else 'board'],
                                'violations' if kind == 'erc' else category[1:], report, violation, sheet)
            match = next((e for e in entries if key and all(e[k] == v for k, v in key.items())), None)
            if not locations_trusted:
                match = None
            item = dict(violation, raw_locator=f'{category}/{index}', waiver_key=key)
            if match:
                waived.append(dict(item, status='waived', reason=match['reason'], owner=match['owner']))
            else:
                unwaived.append(item)
    from .checks import count_by
    ctx.result['details'].update(waived=waived, unwaived=len(unwaived),
                                 waived_by_severity=count_by(waived, 'severity'),
                                 unwaived_by_severity=count_by(unwaived, 'severity'))
    return unwaived
