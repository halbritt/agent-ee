"""Installed component height from verified datasheet facts, captured DigiKey seated height or native STEP bounds."""
from decimal import Decimal, InvalidOperation
import os
from pathlib import PurePosixPath

from .placement_check import _captured
from .storage import read_json, write_json


def parameters(params):
    required = {'criterion', 'envelope'}
    optional = {'facts_check', 'height_facts', 'model_libraries', 'digikey_heights'}
    if required - params.keys() or params.keys() - required - optional:
        raise ValueError('component-height/1 needs envelope and optional facts_check, height_facts, '
                         'digikey_heights, model_libraries')
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
    digikey = []
    if 'digikey_heights' in params:
        from .digikey_record import evidence_declaration
        declared = evidence_declaration(params['digikey_heights'], 'digikey_heights')
        if set(declared['parts']) & set(mapping):
            raise ValueError('a reference takes its height from height_facts or digikey_heights, not both')
        from .digikey_record import PACKAGE_MAP_KEY
        digikey = [declared['capture'], *([declared[PACKAGE_MAP_KEY]['path']] if PACKAGE_MAP_KEY in declared else [])]
    from .fab_consistency import model_libraries
    return [*model_libraries(params).values(), *digikey]


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


def saved_binding(bindings, ref, fp):
    binding = bindings[ref]
    if (binding['duplicate_disagreements'] or binding['uuid'] != fp['uuid'] or
            binding['lib_id'] != fp['fpid'] or binding['value'] != fp['value'].strip()):
        raise ValueError('saved part binding ambiguous or differs from native footprint')
    return binding


def digikey_height(evidence, binding, maximum):
    """Decisive height bound from a DigiKey 'Height - Seated (Max)' bound to the saved part; ValueError is unknown."""
    digikey_mpn(evidence, binding)
    if evidence['value'] is None:
        raise ValueError(f'DigiKey record {evidence["key"]} lists no {evidence["parameter"]!r}')
    from .digikey_record import seated_height_bounds
    # Printed figures are rounded: the upper bound must fit, or the lower bound must exceed.
    low, high = seated_height_bounds(evidence['value'])
    if high <= maximum:
        return high
    if low > maximum:
        return low
    raise ValueError(f'DigiKey seated height {evidence["value"]!r} ({low}..{high} mm after rounding) '
                     f'straddles {maximum} mm')


def digikey_mpn(evidence, binding):
    """The DigiKey record must name the saved part number and, when saved, manufacturer; ValueError otherwise."""
    if not binding['partnumber'] or binding['partnumber'] != evidence['mpn']:
        raise ValueError(f'DigiKey record {evidence["key"]} MPN {evidence["mpn"]!r} differs from saved part number '
                         f'{binding["partnumber"]!r}')
    if binding['manufacturer'] and binding['manufacturer'].casefold() != evidence['manufacturer'].casefold():
        raise ValueError(f'DigiKey record {evidence["key"]} manufacturer {evidence["manufacturer"]!r} differs from '
                         f'saved {binding["manufacturer"]!r}')


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
    digikey = ctx.params.get('digikey_heights', {'parts': {}})
    facts, bindings = [], {}
    if mappings:
        path = prerequisite(ctx, ctx.params['facts_check'], 'datasheet_facts', 'facts.json', allow_unsupported=True)
        facts = read_json(path)['facts']
    if mappings or digikey['parts']:
        from .part_binding import parse_board_bindings
        bindings = parse_board_bindings(_captured(ctx, ctx.scope['board']), defer_param_errors=True)
    capture, capture_error, package_map = None, None, None
    if digikey['parts']:
        from .digikey_record import load_package_map, replay_evidence
        try:
            capture = replay_evidence(ctx.inputs, digikey, ctx.manifest['files'])
            package_map = load_package_map(ctx.inputs, digikey, ctx.manifest['files'])
        except (OSError, ValueError, KeyError) as exc:
            capture = None
            # An unreadable or unpinned capture is unknown height, never a pass.
            capture_error = f'DigiKey capture unavailable: {exc}'
    refs = [fp['ref'] for fp in native['footprints']]
    unknown = [f'{ref}: height fact selects missing footprint' for ref in sorted(set(mappings) - set(refs))]
    unknown += [f'{ref}: DigiKey height selects missing footprint'
                for ref in sorted(set(digikey['parts']) - set(refs))]
    measured, defects = [], []
    model_refs = set(refs) - set(mappings) - set(digikey['parts'])
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
                binding = saved_binding(bindings, ref, fp)
                part_number = binding['partnumber'] or binding['value']
                height = fact_height(matches[0], fp, part_number)
                item.update(source='datasheet_fact', fact=matches[0], part_number=part_number)
            elif ref in digikey['parts']:
                if capture is None:
                    raise ValueError(capture_error)
                from .digikey_record import part_evidence
                doc, dependencies = capture
                evidence = part_evidence(doc, digikey, dependencies, digikey['parts'][ref], 'seated_height')
                item.update(source='digikey_seated_height', digikey=evidence)
                binding = saved_binding(bindings, ref, fp)
                if package_map is not None:
                    from .digikey_record import package_verdict
                    digikey_mpn(evidence, binding)
                    verdict, reason = package_verdict(evidence, fp['fpid'], package_map)
                    item['package'] = {'status': verdict, 'reason': reason, 'footprint': fp['fpid'],
                                       'digikey_package': evidence['package'],
                                       'package_source': evidence['package_source']}
                    if verdict == 'defect':
                        # A record for another package is a determinate binding defect, never a height.
                        item.update(status='defect', reason=reason)
                        defects.append(f'{ref}: {reason}')
                        measured.append(item)
                        continue
                    if verdict == 'unknown':
                        raise ValueError(reason)
                height = digikey_height(evidence, binding, maximum)
                item.update(part_number=binding['partnumber'])
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
