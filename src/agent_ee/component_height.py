"""Installed component height from verified datasheet facts or native STEP bounds."""
from decimal import Decimal, InvalidOperation
import os
from pathlib import PurePosixPath

from .placement_check import _captured
from .storage import read_json, write_json


def parameters(params):
    required = {'criterion', 'envelope'}
    optional = {'facts_check', 'height_facts', 'model_libraries'}
    if required - params.keys() or params.keys() - required - optional:
        raise ValueError('component-height/1 needs envelope and optional facts_check, height_facts, model_libraries')
    mapping = params.get('height_facts', {})
    if (not isinstance(mapping, dict) or any(not isinstance(k, str) or not k.strip() or
            not isinstance(v, str) or not v.strip() for k, v in mapping.items())):
        raise ValueError('height_facts must map reference to fact id')
    if ('facts_check' in params) != bool(mapping):
        raise ValueError('facts_check and nonempty height_facts must be declared together')
    if 'facts_check' in params:
        from .checks import SAFE_ID
        if not isinstance(params['facts_check'], str) or not SAFE_ID.fullmatch(params['facts_check']):
            raise ValueError('facts_check must be a safe check id')
    from .fab_consistency import model_libraries
    return list(model_libraries(params).values())


def height_number(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('height must be finite positive millimetres') from exc
    if isinstance(value, bool) or not number.is_finite() or number <= 0:
        raise ValueError('height must be finite positive millimetres')
    return number


def fact_height(fact, footprint, part_number):
    if (not isinstance(fact, dict) or fact.get('kind') != 'component_height' or fact.get('unit') != 'mm'
            or fact.get('part_number') != part_number or not footprint['fpid']
            or fact.get('footprint_id') != footprint['fpid']
            or fact.get('measurement') != 'installed_maximum_above_board'):
        raise ValueError('height fact unverified or inapplicable to installed native part/footprint')
    return height_number(fact['value'])


def model_requests(ctx, footprints):
    """Reuse the fab resolver's pinned stock/local paths; embedded bounds remain unqualified."""
    from .fab_consistency import board_facts, load_model_libraries, resolve_model, VARIABLE, EMBED
    if not footprints:
        return [], {}
    board = board_facts(ctx)
    libraries = load_model_libraries(ctx)
    requests, missing = [], {}
    for fp in board['footprints']:
        if fp['ref'] not in footprints:
            continue
        models = []
        for path in fp['models']:
            if PurePosixPath(path).is_absolute():
                missing[fp['ref']] = 'absolute model paths have no immutable height binding'
                break
            status, reason, extra = resolve_model(ctx, path, fp, board, libraries)
            if status != 'resolved' or path.startswith(EMBED):
                missing[fp['ref']] = reason if status != 'resolved' else 'embedded STEP bounds unqualified'
                break
            if 'library' in extra:
                member = extra['library']['member']
            else:
                expanded = VARIABLE.sub('', path).lstrip('/') if VARIABLE.match(path) else path
                member = os.path.normpath(str(PurePosixPath(ctx.scope['board']).parent / expanded))
            _captured(ctx, member)
            if PurePosixPath(member).suffix.lower() not in ('.step', '.stp'):
                missing[fp['ref']] = 'only native STEP bounds are qualified'
                break
            models.append({'path': path, 'member': member, 'sha256': ctx.manifest['files'][member]})
        else:
            if models:
                requests.append({'ref': fp['ref'], 'models': models})
                continue
            missing[fp['ref']] = 'no height source or model'
    return requests, missing


def model_input_paths(files):
    """Freeze captured STEP assets before mutable candidates gain model-bearing footprints."""
    return {path for path in files if PurePosixPath(path).suffix.lower() in ('.step', '.stp')}


def evaluate(ctx, envelope, native):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    if 'max_component_height_mm' not in envelope:
        raise CheckError('unsupported', 'envelope has no max_component_height_mm')
    maximum = height_number(envelope['max_component_height_mm'])
    mappings = ctx.params.get('height_facts', {})
    facts, bindings = [], {}
    if mappings:
        from .part_binding import parse_board_bindings
        path = prerequisite(ctx, ctx.params['facts_check'], 'datasheet_facts', 'facts.json', allow_unsupported=True)
        facts = read_json(path)['facts']
        bindings = parse_board_bindings(_captured(ctx, ctx.scope['board']), defer_param_errors=True)
    refs = [fp['ref'] for fp in native['footprints']]
    unknown = [f'{ref}: height fact selects missing footprint' for ref in sorted(set(mappings) - set(refs))]
    measured, defects = [], []
    model_refs = set(refs) - set(mappings)
    requests, missing = model_requests(ctx, model_refs)
    models = {}
    if requests:
        # Ctx.execute replaces the current logs; keep the preceding native load receipt.
        for name in ('execution.json', 'console.log'):
            retained = 'envelope-probe-' + name
            (ctx.work / retained).write_bytes((ctx.work / name).read_bytes())
            ctx.evidence(retained)
        ctx.result['details']['envelope_probe_tool'] = ctx.result['details']['tool']
        write_json(ctx.work / 'height-model-request.json', requests)
        ctx.evidence('height-model-request.json')
        ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.component_height_probe',
                     ctx.candidate_path(ctx.scope['board']), '--request', '/work/height-model-request.json',
                     '--out', '/work/height-model-bounds.json'], timeout=600)
        ctx.evidence('height-model-bounds.json')
        result = ctx.load_json('height-model-bounds.json', 'native STEP height bounds')
        if (result.get('schema') != 'agent-ee.component-height-probe/1' or
                result.get('board_sha256') != native['board_sha256'] or
                result.get('pcbnew_version') != native['pcbnew_version']):
            raise CheckError('malformed', 'native STEP bounds differ from captured board/version')
        expected = {r['ref'] for r in requests}
        if (not isinstance(result.get('footprints'), list) or len(result['footprints']) != len(expected) or
                {item['ref'] for item in result['footprints']} != expected):
            raise CheckError('malformed', 'native STEP bounds omit or repeat requested footprints')
        models = {item['ref']: item for item in result['footprints']}
    for fp in native['footprints']:
        ref = fp['ref']
        item = {'ref': ref, 'status': 'unknown'}
        try:
            if refs.count(ref) != 1:
                raise ValueError('ambiguous native footprint reference')
            if ref in mappings:
                matches = [r['fact'] for r in facts if r.get('status') == 'verified' and r['id'] == mappings[ref]]
                if len(matches) != 1:
                    raise ValueError('no unique verified height fact')
                binding = bindings[ref]
                if (binding['duplicate_disagreements'] or binding['uuid'] != fp['uuid'] or
                        binding['lib_id'] != fp['fpid'] or binding['value'] != fp['value'].strip()):
                    raise ValueError('saved part binding ambiguous or differs from native footprint')
                part_number = binding['partnumber'] or binding['value']
                height = fact_height(matches[0], fp, part_number)
                item.update(source='datasheet_fact', fact=matches[0], part_number=part_number)
            else:
                model = models.get(ref)
                if model is None or model['status'] != 'measured':
                    raise ValueError(missing.get(ref, (model or {}).get('reason', 'model bounds missing')))
                height = height_number(model['height_mm'])
                item.update(source='native_STEP_bounds', models=model['models'])
            item.update(height_mm=str(height), maximum_mm=str(maximum),
                        status='defect' if height > maximum else 'pass')
            if item['status'] == 'defect':
                defects.append(f'{ref}: installed height {height} mm exceeds {maximum} mm')
        except (ValueError, KeyError) as exc:
            item['reason'] = str(exc)
            unknown.append(f'{ref}: {exc}')
        measured.append(item)
    if not measured:
        unknown.append('empty native footprint inventory')
    return {'criterion': 'component-height/1', 'status': 'unknown' if unknown else 'defect' if defects else 'pass',
            'defects': defects, 'unknown': unknown, 'measured': measured}
