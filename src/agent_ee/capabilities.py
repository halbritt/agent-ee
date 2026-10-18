"""Pure, parameter-aware capability descriptions for explicit scope/3 audits.

These predicates establish executable coverage, not physics qualification or
runtime success. No tool execution, model inference, or uncaptured input reads occur.
"""

import copy
import math
import re
from pathlib import Path, PurePosixPath

from .storage import identity


KINDS = frozenset(('jlc_dfm', 'fab_package', 'cpl_rotation', 'jlc_cost_estimate', 'block_provenance', 'fab_roundtrip', 'parts_subset', 'fab_consistency', 'fab_export', 'package_consistency', 'part_binding', 'catalog', 'part_selection', 'land_pattern', 'erc', 'drc', 'netlist', 'geometry', 'rc_transient',
                   'measurements', 'coupled_margins', 'datasheet_facts', 'pin_map', 'active_spice',
                   'kicad_happy', 'hardware_interface', 'passive_corners', 'dc_path',
                   'netclass_completeness', 'footprint_assignment', 'symbol_pins', 'routing_equivalence', 'routing_rules', 'netlist_parity', 'netlist_extra', 'fab_profile', 'placement', 'board_envelope', 'assembly_readiness', 'schematic_lint', 'dfa_dft', 'dfm_geometry', 'net_names', 'regeneration_determinism', 'design_rules'))
DOMAINS = frozenset(('electrical', 'thermal', 'parasitic', 'manufacturing', 'supply_chain'))


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be finite numeric')
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def bounds(limits):
    if not isinstance(limits, dict) or not limits:
        raise ValueError('missing nonempty limits')
    predicates = []
    for name, limit in limits.items():
        text(name, 'metric name')
        if not isinstance(limit, dict):
            raise ValueError('limit must be an object')
        for key in ('unit', 'condition'):
            text(limit.get(key), key)
        keys = [key for key in ('min', 'max') if key in limit]
        if not keys:
            raise ValueError(f'{name}: missing bounds')
        for key in keys:
            finite(limit[key], f'{name}:{key}')
            predicates.append(f'measurement:{name}:{key}')
        if len(keys) == 2 and limit['min'] > limit['max']:
            raise ValueError(f'{name}: reversed bounds')
    return predicates


def _geometry(params):
    predicates = []
    if 'criterion' in params or 'covered_primitives' in params:
        if params.get('criterion') != 'copper-width/1':
            raise ValueError('unknown geometry criterion')
        classes = params.get('covered_primitives')
        if not isinstance(classes, list) or sorted(classes) != ['PCB_ARC', 'PCB_TRACK']:
            raise ValueError('copper-width/1 requires exact track/arc coverage')
        if 'exclude_nets' not in params:
            raise ValueError('copper-width/1 requires explicit exclude_nets')
        if 'min_track_width_mm' not in params:
            raise ValueError('missing width limit')
    elif 'net_constraints' in params:
        raise ValueError('net_constraints requires copper-width/1')
    excluded = params.get('exclude_nets', [])
    if not isinstance(excluded, list) or any(not isinstance(n, str) for n in excluded):
        raise ValueError('exclude_nets must be a list of net names')
    if 'min_track_width_mm' in params:
        if finite(params['min_track_width_mm'], 'width limit') <= 0:
            raise ValueError('width limit must be positive')
        predicates = ['geometry:' + params.get('criterion', 'straight-width/1')]
    return predicates


def _rc_transient(params, prerequisite):
    predicates = []
    prerequisite(params.get('netlist_check'), ('netlist',))
    for key in ('input_net', 'output_net', 'ground_net'):
        text(params.get(key), key)
    step, stop = params.get('step_v', 1.0), params.get('stop_ms', 5.0)
    for label, value in (('step_v', step), ('stop_ms', stop)):
        if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f'{label} must be finite positive')
    measurements = params.get('measurements', [])
    if not isinstance(measurements, list):
        raise ValueError('measurements must be a list')
    names = set()
    for measurement in measurements:
        name = text(measurement.get('name'), 'measurement name').lower()
        if not re.fullmatch('[a-z][a-z0-9_]{0,31}', name) or name in names:
            raise ValueError('invalid/duplicate measurement name')
        names.add(name)
        at = measurement['at_ms']
        if isinstance(at, bool) or not 0 < float(at) <= float(stop):
            raise ValueError('measurement time outside simulation interval')
        limit = {key: measurement[key] for key in ('min', 'max') if measurement.get(key) is not None}
        if limit:
            predicates.extend(bounds({name: {**limit, 'unit': 'V', 'condition': 'step'}}))
    return predicates


def _measurements(params, captured):
    predicates = []
    captured(params['adapter'], immutable=True)
    tool = params['tool']
    argv = tool['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise ValueError('tool requires explicit argv')
    executable = PurePosixPath(argv[0])
    if not executable.is_absolute() or '..' in executable.parts or not executable.is_relative_to('/usr'):
        raise ValueError('tool executable must reside under /usr')
    if not isinstance(tool['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', tool['sha256']):
        raise ValueError('tool needs pinned sha256')
    if tool['domain'] not in DOMAINS:
        raise ValueError('unsupported tool domain')
    inputs = tool.get('inputs', [])
    if not isinstance(inputs, list):
        raise ValueError('tool inputs must be a list')
    for path in inputs:
        captured(path, immutable=True)
    # Candidate program arguments must be owned immutable dependencies.
    for arg in argv:
        if arg.startswith('/candidate/') and arg[len('/candidate/'):] not in inputs:
            raise ValueError('candidate tool argument missing from immutable inputs')
    predicates = bounds(params['limits'])
    return predicates


def _coupled_margins(params, prerequisite, files, inputs):
    members = params['members']
    if not isinstance(members, list) or len(members) < 2:
        raise ValueError('coupling requires at least two members')
    condition = text(params['condition'], 'coupling condition')
    domains, names = set(), set()
    for member in members:
        source = prerequisite(member['check'], ('measurements', 'rc_transient', 'passive_corners', 'dc_path'))
        source_params = source.get('params', {})
        if source['kind'] == 'dc_path':
            from .dc_path import load_inputs, limits
            contract, _ = load_inputs(source_params, files, inputs)
            metric = limits(contract)[member['metric']]
            domain = 'electrical'
        elif source['kind'] == 'measurements':
            metric = source_params['limits'][member['metric']]
            domain = source_params['tool']['domain']
        else:
            m = next(m for m in source_params['measurements'] if m['name'].lower() == member['metric'])
            from .passive_corners import condition as corner_condition
            metric = {'unit': 'V', 'condition': (corner_condition(source_params, m)
                if source['kind'] == 'passive_corners' else
                f"ideal-unloaded-{source_params.get('step_v', 1.0)}V-step-at-{float(m['at_ms'])}ms")}
            domain = 'electrical'
        if metric['condition'] != condition:
            raise ValueError('coupled source condition mismatch')
        name = text(member['name'], 'member name')
        if name in names:
            raise ValueError('duplicate coupling name')
        names.add(name)
        domains.add(domain)
        target = params['limits'][name]
        if target['unit'] != metric['unit'] or target['condition'] != condition:
            raise ValueError('coupled limit identity mismatch')
    if len(domains) < 2 or names != set(params['limits']):
        raise ValueError('coupling needs distinct domains and complete limits')
    predicates = bounds(params['limits'])
    return predicates


def describe_check(spec, *, check_specs, files, scope, inputs=None):
    """Return a supported descriptor or explicit unsupported reasons.

    Immutable input names must resolve in the captured inventory. Dependencies
    must precede consumers; no automatic sorting can change execution semantics.
    """
    kind = spec.get('kind')
    result = {'check_id': spec['id'], 'kind': kind, 'capability_version': f'{kind}/1',
        'status': 'supported', 'can_evaluate_bound': False, 'predicates': [],
        'prerequisites': [], 'immutable_inputs': [], 'unsupported_reasons': []}
    params = spec.get('params', {})

    def captured(path, *, immutable=False):
        if not isinstance(path, str):
            raise ValueError('captured path must be text')
        rel = PurePosixPath(path)
        if rel.is_absolute() or '..' in rel.parts or not rel.parts or str(rel) != path or path not in files:
            raise ValueError(f'input not captured or unsafe: {path}')
        if immutable:
            result['immutable_inputs'].append(path)

    def prerequisite(cid, expected):
        prior = check_specs[:check_specs.index(spec)]
        source = next((s for s in prior if s['id'] == cid), None)
        if source is None or source.get('kind') not in expected:
            raise ValueError(f'unresolved/forward/wrong-kind prerequisite: {cid}')
        result['prerequisites'].append({'check_id': cid, 'kind': source['kind']})
        return source

    try:
        if kind not in KINDS:
            raise ValueError(f'unknown capability kind: {kind}')
        if not isinstance(params, dict):
            raise ValueError('params must be an object')
        if kind not in ('geometry', 'placement', 'board_envelope', 'dfa_dft') and 'criterion' in params:
            raise ValueError('unknown criterion for check kind')
        if not isinstance(params.get('calibration_inputs', []), list):
            raise ValueError('calibration_inputs must be a list')
        for path in params.get('calibration_inputs', []):
            captured(path, immutable=True)
        if kind in ('erc', 'drc', 'netlist'):
            captured(scope.get('schematic'))
            if kind == 'drc':
                captured(scope.get('board'))
            if kind != 'netlist':
                native_files = [scope['schematic']] + ([scope['board']] if kind == 'drc' else [])
                for native in native_files:
                    for suffix in ('.kicad_pro', '.kicad_dru'):
                        dependency = str(PurePosixPath(native).with_suffix(suffix))
                        if dependency in files:
                            captured(dependency, immutable=True)
                result['predicates'] = [f'native:{kind}:violations']
            if 'projection' in params:
                from .checks import drc_projection, require_later_full_drc
                if kind != 'drc' or drc_projection(params) is None:
                    raise ValueError('only drc supports a declared projection')
                require_later_full_drc(check_specs, spec)
                result['predicates'] = [f"native:drc:{params['projection']}"]
        elif kind == 'geometry':
            captured(scope.get('board'))
            result['predicates'] = _geometry(params)
            if 'net_constraints' in params:
                # The record and the netclasses it binds are acceptance policy, never repairable.
                captured(params['net_constraints'], immutable=True)
                project = str(PurePosixPath(scope['board']).with_suffix('.kicad_pro'))
                captured(project, immutable=True)
                if inputs is not None:
                    from .net_constraints import net_minimums
                    net_minimums(Path(inputs) / params['net_constraints'], Path(inputs) / project)
        elif kind == 'rc_transient':
            result['predicates'] = _rc_transient(params, prerequisite)
        elif kind == 'board_envelope':
            from .envelope_check import parameters, predicate_names
            captured(scope.get('board'))
            for path in parameters(params):
                captured(path, immutable=True)
            if params['criterion'] == 'stackup/1':
                raise ValueError('stackup copper_weight_oz has no qualified captured manufacturing authority')
            result['predicates'] = predicate_names(params)
        elif kind == 'placement':
            from .placement_check import parameters, predicate_names
            captured(scope.get('board'))
            for path in parameters(params):
                captured(path, immutable=True)
            prerequisite(params.get('drc_check'), ('drc',))
            if params['criterion'] == 'connector-edge/1':
                try:
                    from .board_envelope import validate
                except ImportError:
                    raise ValueError('connector-edge/1 needs the AGENTEE-85 envelope validator') from None
                if inputs is not None:
                    from .placement_predicates import envelope_declarations
                    if not envelope_declarations(validate((Path(inputs) / params['envelope']).read_bytes())):
                        raise ValueError('envelope declares no fixed connectors')
            result['predicates'] = predicate_names(params)
        elif kind == 'dfa_dft':
            from .dfa_dft import capability as dfa_dft_capability
            captured(scope.get('board'))
            result['predicates'] = dfa_dft_capability(params, scope, spec, prerequisite)
        elif kind == 'kicad_happy':
            from .kicad_happy import describe_capability
            captured(scope.get('schematic'))
            captured(scope.get('board'))
            descriptor = describe_capability(params, files)
            if descriptor['status'] != 'supported':
                raise ValueError('; '.join(descriptor['unsupported_reasons']))
            for dependency in descriptor['immutable_inputs']:
                path = dependency['path']
                if path not in scope.get('requirements', []):
                    raise ValueError('adapter assumptions source must be a captured immutable requirement')
                captured(path, immutable=True)
            result['capability_version'] = descriptor['capability_version']
            result['predicates'] = descriptor['predicates']
        elif kind in ('catalog', 'part_selection'):
            from .catalog_checks import capability
            descriptor = capability(kind, params, scope, files, inputs, spec['id'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'land_pattern':
            from .land_pattern import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'symbol_pins':
            from .symbol_checks import capability
            descriptor = capability(params, files, inputs)
            captured(params['declaration'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'footprint_assignment':
            from .library_checks import capability
            descriptor = capability(params, files, inputs, scope, spec['id'])
            captured(params['declaration'])
            if 'netlist_check' in params:
                prerequisite(params['netlist_check'], ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'parts_subset':
            from .parts_subset import capability
            descriptor = capability(params, files, inputs, scope, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'netlist_extra':
            from .netlist_extra import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'block_provenance':
            from .block_adaptation import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'net_names':
            from .net_names import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            captured(params['levels'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'regeneration_determinism':
            from .design_diff import capability
            descriptor = capability(params, scope, files)
            for path in descriptor['mutable_inputs']:
                captured(path)
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            for cid in descriptor['producers']:
                prerequisite(cid, KINDS)
            result['predicates'] = descriptor['predicates']
        elif kind == 'design_rules':
            from .design_rules import capability as rule_capability
            descriptor = rule_capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            if 'facts_check' in params:
                prerequisite(params['facts_check'], ('datasheet_facts',))
            for delegation in params.get('delegated', {}).values():
                prerequisite(delegation['check'], KINDS)
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'schematic_lint':
            from .schematic_lint import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            captured(scope.get('schematic'))
            prerequisite(params.get('netlist_check'), ('netlist',))
            result['predicates'] = descriptor['predicates']
        elif kind == 'netclass_completeness':
            from .netclass_completeness import capability
            descriptor = capability(params, files, inputs)
            captured(params['project'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'routing_equivalence':
            from .routing_equivalence import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            captured(scope.get('board'))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'routing_rules':
            from .routing_rules import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'netlist_parity':
            from .netlist_parity import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'fab_profile':
            from .fab_profile import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'dfm_geometry':
            from .dfm_geometry import capability
            captured(scope.get('board'))
            descriptor = capability(params, scope, files, inputs)
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'assembly_readiness':
            from .assembly_readiness import capability
            captured(scope.get('schematic'))
            descriptor = capability(params, files, inputs)
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'part_binding':
            from .part_binding import capability
            descriptor = capability(params, scope, files, inputs)
            sch = params.get('schematic', scope.get('schematic'))
            pcb = params.get('board', scope.get('board'))
            if sch is not None:
                captured(sch)
            if pcb is not None:
                captured(pcb)
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'package_consistency':
            from .package_consistency import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'fab_export':
            from .fab_export import capability
            descriptor = capability(params)
            prerequisite(params['netlist_check'], ('netlist',))
            captured(scope.get('board'))
            captured(scope.get('schematic'))
            project = str(PurePosixPath(scope['board']).with_suffix('.kicad_pro'))
            if project in files:
                captured(project, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'fab_roundtrip':
            from .fab_roundtrip import capability
            result['predicates'] = capability(params, scope, prerequisite, captured)
        elif kind == 'fab_consistency':
            from .fab_consistency import capability
            result['predicates'] = capability(params, scope, prerequisite, captured)
        elif kind == 'cpl_rotation':
            from .cpl_rotation import capability
            descriptor = capability(params, files, inputs)
            prerequisite(params['export_check'], ('fab_export',))
            prerequisite(params['geometry_check'], ('geometry',))
            captured(scope.get('board'))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'jlc_cost_estimate':
            from .jlc_cost import capability
            descriptor = capability(params, files, inputs)
            prerequisite(params['export_check'], ('fab_export',))
            captured(scope.get('board'))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'jlc_dfm':
            from .jlc_dfm import capability
            descriptor = capability(params, scope, spec, files, inputs)
            prerequisite(params['fab_check'], ('fab_package',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'fab_package':
            from .fab_package import capability
            result['predicates'] = capability(params, scope, spec, prerequisite, captured)
            captured(scope.get('board'))
            captured(scope.get('schematic'))
            project = str(PurePosixPath(scope['board']).with_suffix('.kicad_pro'))
            if project in files:
                captured(project, immutable=True)
        elif kind == 'hardware_interface':
            from .hardware_interface import capability
            descriptor = capability(params, scope, files, inputs, spec['id'])
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['predicates'] = descriptor['predicates']
        elif kind == 'active_spice':
            from .active_spice import capability
            descriptor = capability(params, files)
            prerequisite(params.get('netlist_check'), ('netlist',))
            for path in descriptor['immutable_inputs']:
                captured(path, immutable=True)
            result['capability_version'] = descriptor['contract']
            result['predicates'] = [f'active-spice:{name}' for name in descriptor['bounded_metrics']]
        elif kind == 'passive_corners':
            from .passive_corners import validate_contract
            validate_contract(params)
            captured(params['qualification'], immutable=True)
            result['predicates'] = _rc_transient(params, prerequisite)
        elif kind == 'dc_path':
            from .dc_path import load_inputs, limits
            captured(scope.get('board'))
            contract, dependencies = load_inputs(params, files, inputs)
            for path in dependencies:
                captured(path, immutable=True)
            result['predicates'] = bounds(limits(contract))
        elif kind == 'measurements':
            result['predicates'] = _measurements(params, captured)
        elif kind == 'coupled_margins':
            result['predicates'] = _coupled_margins(params, prerequisite, files, inputs)
        elif kind in ('datasheet_facts', 'pin_map'):
            from .datasheet_facts import load_facts, load_obligations, validate_extractor
            if inputs is None:
                raise ValueError('datasheet capability requires captured inputs')
            source = spec if kind == 'datasheet_facts' else prerequisite(
                params.get('facts_check'), ('datasheet_facts',))
            source_params = source['params']
            captured(source_params['facts'], immutable=True)
            validate_extractor(source_params['extractor'])
            facts = load_facts(inputs, source_params['facts'])
            obligations = load_obligations(inputs, source_params['facts'])
            if obligations is not None:
                for path in obligations['dependencies']:
                    captured(path, immutable=True)
            for fact in facts:
                captured(fact['source']['path'], immutable=True)
            if kind == 'pin_map':
                prerequisite(params.get('netlist_check'), ('netlist',))
                symbols = params.get('symbols')
                if not isinstance(symbols, list) or not symbols:
                    raise ValueError('pin_map requires explicit symbol applicability')
                by_id = {f['id']: f for f in facts}
                refs = set()
                for symbol in symbols:
                    if not isinstance(symbol, dict) or set(symbol) != {'reference', 'part_number', 'facts'}:
                        raise ValueError('symbol needs reference, part_number and facts')
                    ref = text(symbol['reference'], 'reference')
                    part = text(symbol['part_number'], 'part_number')
                    if ref in refs:
                        raise ValueError('duplicate symbol applicability')
                    refs.add(ref)
                    selected = symbol['facts']
                    if not isinstance(selected, list) or not selected:
                        raise ValueError('symbol requires pin facts')
                    pins = set()
                    for fid in selected:
                        fact = by_id[text(fid, 'fact id')]
                        if fact['kind'] != 'pin_function' or fact['part_number'] != part:
                            raise ValueError('pin fact is inapplicable')
                        if fact['pin'] in pins:
                            raise ValueError('duplicate pin fact')
                        pins.add(fact['pin'])
                        result['predicates'].append(f"pin-map:{ref}:{fact['pin']}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, OverflowError) as exc:
        result.update(status='unsupported', predicates=[], unsupported_reasons=[{
            'code': 'unknown_kind' if kind not in KINDS else 'invalid_contract',
            'message': str(exc)}])
    result['immutable_inputs'] = sorted(set(result['immutable_inputs']))
    result['can_evaluate_bound'] = result['status'] == 'supported' and bool(result['predicates'])
    return result


def compile_coverage(scope, files, inputs=None):
    specs = scope['checks']
    checks = {s['id']: describe_check(s, check_specs=specs, files=files, scope=scope, inputs=inputs) for s in specs}
    for cid, descriptor in checks.items():
        if descriptor['status'] != 'supported':
            raise ValueError(f"unsupported capability {cid}: {descriptor['unsupported_reasons']}")
    requirements, acceptance_mapped = {}, set()
    for req in scope['baseline']['requirements']:
        intent = req['intent']
        if intent not in ('acceptance', 'informational'):
            raise ValueError('requirement intent must be acceptance or informational')
        eligible = [cid for cid in req['checks'] if checks[cid]['can_evaluate_bound']] if intent == 'acceptance' else []
        if intent == 'acceptance' and not eligible:
            raise ValueError(f"acceptance requirement {req['id']} has no executable bound coverage")
        acceptance_mapped.update(eligible)
        requirements[req['id']] = {'intent': intent, 'mapped_checks': req['checks'], 'acceptance_checks': eligible}
    if not acceptance_mapped:
        raise ValueError('scope/3 requires at least one acceptance requirement')
    if any(c['can_evaluate_bound'] and cid not in acceptance_mapped for cid, c in checks.items()):
        raise ValueError('defect-capable check requires an acceptance mapping')
    report = {'schema': 'agent-ee.coverage/1', 'policy': 'executable-acceptance/1',
              'checks': checks, 'requirements': requirements}
    return {**report, 'coverage_id': identity(report)}


def convert_scope(scope):
    """Return a NEW scope/3; preserve every historical obligation as acceptance.

    Conversion does not certify coverage and never edits retained evidence.
    Scope/1 needs an owner-authored baseline before conversion.
    """
    if scope.get('schema') != 'agent-ee.scope/2':
        raise ValueError('conversion requires scope/2 with explicit baseline requirements')
    converted = copy.deepcopy(scope)
    converted['schema'] = 'agent-ee.scope/3'
    for requirement in converted['baseline']['requirements']:
        if 'intent' in requirement:
            raise ValueError('historical requirement unexpectedly has intent')
        requirement['intent'] = 'acceptance'
    return converted
