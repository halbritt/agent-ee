"""Bounded captured-subcircuit simulation; model data cannot supply control code."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import math
import re
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path, PurePosixPath

from .candidate import safe_relative
from .storage import digest, identity, read_json, write_json

TOKEN = re.compile(r'^[A-Za-z0-9_+.-]+$')
FUNCTIONS = {'v', 'i', 'abs', 'sqrt', 'exp', 'ln', 'log', 'log10', 'pow', 'pwr',
             'min', 'max', 'limit', 'if', 'sin', 'cos', 'tan', 'tanh'}
MODEL_TYPES = {'d', 'npn', 'pnp', 'njf', 'pjf', 'nmos', 'pmos', 'r', 'res', 'c', 'sw', 'vswitch'}


def model_path(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
        raise ValueError('model path must be a literal captured relative path')
    return str(safe_relative(value))


def logical_lines(text):
    """Normalize continuations before applying directive and expression policy."""
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('*'):
            continue
        # Reject alternate comment/delimiter syntax instead of disagreeing with SPICE.
        if any(c in line for c in (';', '$', '\\', '`', '\x00')):
            raise ValueError(f'unsupported model delimiter at line {number}')
        if line.startswith('+'):
            if not lines:
                raise ValueError('orphan model continuation')
            start, previous = lines.pop()
            lines.append((start, previous + ' ' + line[1:].strip()))
        else:
            lines.append((number, line))
    return lines


def validate_statement(line):
    """A deliberately narrow non-file device dialect, including PSpice VALUE/TABLE."""
    if not re.fullmatch(r'[A-Za-z0-9_+.,:=(){}*/^<>!|&\s-]+', line):
        raise ValueError('unsupported model character or quoted expression')
    if re.search(r'\b(file|pwl|sffm|am|trnoise|trrandom|external|code|load|write|read|shell|source)\b', line, re.I):
        raise ValueError('file-driven or unsupported model statement')
    head, *rest = line.split(None, 1)
    if rest and re.search(r'(?:^|\s)\.[A-Za-z]', rest[0]):
        raise ValueError('embedded model directive')
    directive = head.lower()
    if not rest and directive.startswith('.') and directive != '.ends':
        raise ValueError(f'incomplete model directive {directive}')
    if directive.startswith('.'):
        if directive not in ('.subckt', '.ends', '.model', '.param'):
            raise ValueError(f'unsupported model directive {directive}')
        if directive == '.model':
            m = re.match(r'^\.model\s+\S+\s+([A-Za-z]+)(?=\s|\(|$)', line, re.I)
            if not m or m[1].lower() not in MODEL_TYPES:
                raise ValueError('unsupported model type')
            if m[1].lower() == 'd' and re.search(r'\blevel\b', line[m.end():], re.I):
                raise ValueError('explicit diode model level is unsupported; use the native default')
    elif directive[0] not in 'rclviefghdqjmsx':
        raise ValueError(f'unsupported model device {directive}')
    # Function calls are the expression escape boundary (e.g. read(), vector(), etc.).
    for name in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', line):
        if name.lower() not in FUNCTIONS | MODEL_TYPES:
            raise ValueError(f'unsupported model function {name}')
    if directive[0] in 'vi' and not directive.startswith('.'):
        if not re.fullmatch(r'\S+\s+\S+\s+\S+\s+(?:DC\s+)?[+-]?(?:\d*\.)?\d+(?:[eE][+-]?\d+)?[A-Za-z]*', line, re.I):
            raise ValueError('model voltage/current sources must be literal DC')


def capture_models(inputs, files, declared):
    """Resolve only explicitly captured immutable files, never host search paths."""
    if not isinstance(declared, list) or not declared or len(declared) > 64:
        raise ValueError('model_files must list 1..64 immutable model files including all includes')
    paths = [model_path(p) for p in declared]
    if len(set(paths)) != len(paths):
        raise ValueError('duplicate model file')
    inputs = Path(inputs)
    parsed, subcircuits, total, originals = {}, {}, 0, {}
    for rel in paths:
        path = inputs / rel
        if (rel not in files or any(p.is_symlink() for p in [path, *path.parents]) or
                not path.is_file() or digest(path) != files[rel]):
            raise ValueError(f'model file is missing, linked or changed: {rel}')
        total += path.stat().st_size
        if total > 2_000_000:
            raise ValueError('model closure exceeds 2 MB')
        # Vendor headers often contain legacy punctuation. Escape it only on
        # full comment lines; executable statements remain strictly ASCII.
        originals[rel] = ''.join(line.decode('ascii', errors='backslashreplace'
            if line.lstrip(b' \t').startswith(b'*') else 'strict')
            for line in path.read_bytes().splitlines(keepends=True))
        parsed[rel] = logical_lines(originals[rel])
    includes, staged, model_names = {}, {}, set()
    for rel, lines in parsed.items():
        includes[rel], replacements = [], {}
        current = None
        for number, line in lines:
            if line.split()[0].lower() == '.include':
                match = re.fullmatch(r'\.include\s+(?:"([A-Za-z0-9_./-]+)"|([A-Za-z0-9_./-]+))', line, re.I)
                if not match:
                    raise ValueError('unsupported include syntax')
                target = str(PurePosixPath(rel).parent / model_path(match[1] or match[2]))
                if target not in parsed:
                    raise ValueError(f'include outside declared captured model_files: {target}')
                if current:
                    raise ValueError('include within subcircuit is unsupported')
                includes[rel].append(target)
                replacements[number] = f'.include /work/models/{target}\n'
                continue
            validate_statement(line)
            tokens = line.split()
            op = tokens[0].lower()
            if op == '.subckt':
                if current or len(tokens) < 3 or not all(TOKEN.fullmatch(t) for t in tokens[1:2]):
                    raise ValueError('invalid or nested subcircuit header')
                end = next((i for i, t in enumerate(tokens[2:], 2)
                            if t.lower() == 'params:' or '=' in t), len(tokens))
                formals = tokens[2:end]
                current = tokens[1].lower()
                if (current in subcircuits or not formals or
                        not all(TOKEN.fullmatch(t) for t in formals) or
                        len({t.lower() for t in formals}) != len(formals)):
                    raise ValueError('duplicate subcircuit or invalid/duplicate formal ports')
                subcircuits[current] = {'name': tokens[1], 'formals': formals,
                                        'path': rel, 'line': number}
            elif op == '.model':
                if len(tokens) < 3 or not TOKEN.fullmatch(tokens[1]):
                    raise ValueError('invalid model name/header')
                key = (current, tokens[1].lower())
                if key in model_names:
                    raise ValueError('duplicate model name in the same scope')
                model_names.add(key)
            elif op == '.ends':
                if not current or len(tokens) > 2 or (len(tokens) == 2 and tokens[1].lower() != current):
                    raise ValueError('unmatched subcircuit end')
                current = None
            elif not op.startswith('.') and not current:
                raise ValueError('model device outside subcircuit')
        if current:
            raise ValueError('unterminated subcircuit')
        rewritten, replacing = [], False
        for number, physical in enumerate(originals[rel].splitlines(keepends=True), 1):
            if number in replacements:
                rewritten.append(replacements[number])
                replacing = True
            elif replacing and physical.lstrip().startswith('+'):
                rewritten.append('\n')
            else:
                rewritten.append(physical)
                if physical.strip() and not physical.lstrip().startswith('*'):
                    replacing = False
        staged[rel] = ''.join(rewritten)
    def visit(rel, stack):
        if rel in stack or len(stack) >= 16:
            raise ValueError('model include cycle or depth over 16')
        for target in includes[rel]:
            visit(target, stack + [rel])
    dependencies = [target for children in includes.values() for target in children]
    if len(set(dependencies)) != len(dependencies):
        raise ValueError('repeated model include expansion is unsupported')
    for rel in paths:
        visit(rel, [])
    # Include each root once; nested files stay at their declared relative location.
    nested = {p for dependencies in includes.values() for p in dependencies}
    roots = sorted(set(paths) - nested)
    return {'files': {p: files[p] for p in sorted(paths)}, 'subcircuits': subcircuits,
            'includes': includes, 'roots': roots,
            'staged': staged}


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number')
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ValueError(f'{label} exceeds finite numeric range') from exc
    if not math.isfinite(numeric) or (positive and numeric <= 0):
        raise ValueError(f'{label} must be finite' + (' and positive' if positive else ''))
    return numeric


def named_list(entries, label, maximum):
    if not isinstance(entries, list) or not 1 <= len(entries) <= maximum:
        raise ValueError(f'{label} needs 1..{maximum} entries')
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f'{label} entries must be objects')
        name = entry.get('id' if label != 'measurements' else 'name')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name) or name in names:
            raise ValueError(f'{label} needs unique safe identifiers')
        names.add(name)


def fields(record, required, optional=()):
    if not isinstance(record, dict) or not set(required) <= record.keys() or set(record) - set(required) - set(optional):
        raise ValueError('declaration has missing or unsupported fields')


# (kind, observable) -> scalar locator fields; nets follow the analysis kind.
OBSERVABLES = {('transient', 'voltage'): ('at_s',), ('transient', 'voltage_change'): ('from_s', 'to_s'),
               ('transient', 'voltage_minimum'): ('from_s', 'to_s'),
               ('transient', 'voltage_maximum'): ('from_s', 'to_s'),
               ('dc', 'voltage'): ('at_v',), ('dc', 'voltage_change'): ('from_v', 'to_v'),
               ('ac', 'transfer_magnitude'): ('at_hz',)}
BASE_FIELDS = ('contract', 'dialect', 'model_files', 'active_parts', 'ground_net', 'temperature_c', 'netlist_check')


def validate_step(step, unit):
    keys = (f'initial_{unit}', f'final_{unit}', 'delay_s', 'rise_s')
    fields(step, keys)
    for key in keys:
        number(step[key], key, positive=key == 'rise_s')
    number(step['delay_s'] + step['rise_s'], 'step end time', positive=True)
    if step['delay_s'] < 0:
        raise ValueError('step delay must be nonnegative')


def validate_measurement(kind, meas, start, stop):
    observable = meas.get('observable') if isinstance(meas, dict) else None
    locators = OBSERVABLES.get((kind, observable), ())
    nets = (('output_positive_net', 'output_negative_net', 'input_positive_net', 'input_negative_net')
            if kind == 'ac' else ('positive_net', 'negative_net'))
    fields(meas, ('name', 'observable', 'unit', *nets, *locators), ('min', 'max', 'informational'))
    if any(not isinstance(meas[k], str) for k in nets):
        raise ValueError('measurement nets must be explicit native names')
    if not locators or meas['unit'] != ('V/V' if kind == 'ac' else 'V'):
        raise ValueError('unsupported observable or wrong measurement unit')
    points = [number(meas[k], k, positive=k in ('at_s', 'at_hz')) for k in locators]
    if not all(start <= at <= stop for at in points):
        raise ValueError('measurement lies outside analysis interval')
    if observable in ('voltage_minimum', 'voltage_maximum') and not points[0] < points[1]:
        raise ValueError('measurement window must be increasing')
    bounds = [number(meas[key], key) for key in ('min', 'max') if key in meas]
    if len(bounds) == 2 and bounds[0] > bounds[1]:
        raise ValueError('reversed measurement bounds')
    if not bounds and meas.get('informational') is not True:
        raise ValueError('unbounded measurement must explicitly be informational')


def validate_params(params):
    if isinstance(params, dict) and 'template' in params:
        # Role-template mode: sources and analyses are generated from captured data.
        fields(params, (*BASE_FIELDS, 'template'), ('calibration_inputs',))
        from .spice_templates import validate_reference
        validate_reference(params['template'])
    else:
        fields(params, (*BASE_FIELDS, 'sources', 'analyses'), ('calibration_inputs', 'current_sources'))
    if params['contract'] != 'active-spice/1' or params['dialect'] not in ('ngspice', 'ngspice-ps'):
        raise ValueError('requires active-spice/1 and explicit ngspice or ngspice-ps dialect')
    model_files = params['model_files']
    if not isinstance(model_files, list) or not model_files or len(model_files) > 64:
        raise ValueError('model_files must declare the complete immutable closure')
    if len({model_path(p) for p in model_files}) != len(model_files):
        raise ValueError('duplicate model files')
    if not isinstance(params['active_parts'], dict) or not params['active_parts']:
        raise ValueError('active_parts must explicitly bind at least one populated component')
    for ref, part in params['active_parts'].items():
        fields(part, ('model_file', 'subcircuit', 'pin_map'))
        if part['model_file'] not in model_files or not TOKEN.fullmatch(part['subcircuit']):
            raise ValueError(f'{ref}: invalid model declaration')
        mapping = part['pin_map']
        if (not isinstance(mapping, dict) or not mapping or
                any(not isinstance(k, str) or not isinstance(v, str) or not TOKEN.fullmatch(v)
                    for k, v in mapping.items())):
            raise ValueError('pin_map must explicitly map symbol pins to formal nodes')
    number(params['temperature_c'], 'temperature_c')
    if not -273.15 < params['temperature_c'] <= 1000:
        raise ValueError('temperature outside supported range')
    if not isinstance(params['ground_net'], str) or not isinstance(params['netlist_check'], str):
        raise ValueError('ground_net and netlist_check must be explicit strings')
    if 'template' in params:
        return params
    named_list(params['sources'], 'sources', 64)
    for source in params['sources']:
        fields(source, ('id', 'positive_net', 'negative_net', 'dc_v'), ('ac_magnitude_v', 'step', 'current_limit'))
        if not all(isinstance(source[k], str) for k in ('positive_net', 'negative_net')):
            raise ValueError('source nets must be explicit native names')
        number(source['dc_v'], 'dc_v')
        if 'current_limit' in source:
            limit = source['current_limit']
            fields(limit, ('source_a', 'sink_a', 'resistance_ohm'))
            for key in ('source_a', 'sink_a'):
                if number(limit[key], key) < 0:
                    raise ValueError('source current limits must be nonnegative')
            if not (limit['source_a'] or limit['sink_a']):
                raise ValueError('at least one source current limit must be positive')
            number(limit['resistance_ohm'], 'resistance_ohm', positive=True)
        if 'ac_magnitude_v' in source:
            number(source['ac_magnitude_v'], 'ac_magnitude_v', positive=True)
        if 'step' in source:
            validate_step(source['step'], 'v')
            if source['dc_v'] != source['step']['initial_v']:
                raise ValueError('step initial voltage must equal explicit DC bias')
    if 'current_sources' in params:
        named_list(params['current_sources'], 'current_sources', 64)
        for source in params['current_sources']:
            fields(source, ('id', 'positive_net', 'negative_net', 'dc_a'), ('step',))
            if not all(isinstance(source[k], str) for k in ('positive_net', 'negative_net')):
                raise ValueError('current source nets must be explicit native names')
            number(source['dc_a'], 'dc_a')
            if 'step' in source:
                validate_step(source['step'], 'a')
                if source['dc_a'] != source['step']['initial_a']:
                    raise ValueError('step initial current must equal explicit DC bias')
        if {s['id'] for s in params['sources']} & {s['id'] for s in params['current_sources']}:
            raise ValueError('source identifiers must be unique across voltage and current sources')
    named_list(params['analyses'], 'analyses', 16)
    metric_names = set()
    for analysis in params['analyses']:
        if analysis['id'] in ('models', 'version'):
            raise ValueError('analysis identifier collides with a reserved workspace')
        kind = analysis['kind']
        if kind == 'transient':
            fields(analysis, ('id', 'kind', 'step_s', 'stop_s', 'measurements'))
            start, stop = 0, number(analysis['stop_s'], 'stop_s', positive=True)
            step = number(analysis['step_s'], 'step_s', positive=True)
            if step >= stop or stop / step > 1_000_000:
                raise ValueError('transient step/stop range unsupported')
        elif kind == 'ac':
            fields(analysis, ('id', 'kind', 'sweep', 'points_per_decade', 'start_hz', 'stop_hz', 'measurements'))
            start = number(analysis['start_hz'], 'start_hz', positive=True)
            stop = number(analysis['stop_hz'], 'stop_hz', positive=True)
            points = analysis['points_per_decade']
            if (analysis['sweep'] != 'dec' or type(points) is not int or not 1 <= points <= 10000
                    or not start < stop or math.log10(stop / start) * points > 1_000_000):
                raise ValueError('unsupported AC sweep')
            if not any('ac_magnitude_v' in s for s in params['sources']):
                raise ValueError('AC analysis requires an explicit AC source')
        elif kind == 'dc':
            fields(analysis, ('id', 'kind', 'source', 'start_v', 'stop_v', 'points', 'measurements'))
            start, stop = number(analysis['start_v'], 'start_v'), number(analysis['stop_v'], 'stop_v')
            if (type(analysis['points']) is not int or not 2 <= analysis['points'] <= 100_001
                    or not start < stop):
                raise ValueError('unsupported DC sweep')
            number((stop - start) / (analysis['points'] - 1), 'DC sweep step', positive=True)
            if analysis['source'] not in {s['id'] for s in params['sources']}:
                raise ValueError('DC sweep must name a declared voltage source')
        else:
            raise ValueError('analysis must be transient, ac or dc')
        named_list(analysis['measurements'], 'measurements', 64)
        for meas in analysis['measurements']:
            if meas['name'] in metric_names:
                raise ValueError('measurement names must be unique across analyses')
            metric_names.add(meas['name'])
            validate_measurement(kind, meas, start, stop)
    return params


def capability(params, files, inputs=None, scope=None):
    """Pure configuration capability for registry/baseline callers; no solver claim.

    Role-template mode reads only captured template, architecture, target and
    interface bytes. Unsupported template measurements contribute no predicate.
    """
    try:
        validate_params(params)
    except (KeyError, TypeError) as exc:
        raise ValueError(f'invalid active SPICE configuration: {exc}') from exc
    if not set(params['model_files']) <= files.keys():
        raise ValueError('active SPICE model files are not captured')
    immutable = set(params['model_files'])
    if 'template' in params:
        from .spice_templates import expand
        params, report = expand(params, scope, files, inputs)
        immutable.update(report['immutable_inputs'])
        if params is None:
            return {'contract': 'active-spice/1', 'immutable_inputs': sorted(immutable), 'bounded_metrics': []}
    return {'contract': 'active-spice/1', 'immutable_inputs': sorted(immutable),
            'bounded_metrics': [m['name'] for a in params['analyses'] for m in a['measurements']
                                if 'min' in m or 'max' in m]}


def native_inventory(xml_path, conn):
    """Use native libpart pins, including disconnected terminals, not just net nodes."""
    root = ET.parse(xml_path).getroot()
    for tag, attribute in (('./components/comp', 'ref'), ('./nets/net', 'name')):
        names = [item.get(attribute) for item in root.findall(tag)]
        if None in names or len(set(names)) != len(names):
            raise ValueError('duplicate or missing native component/net identity')
    libraries = {}
    for libpart in root.findall('./libparts/libpart'):
        key = (libpart.get('lib'), libpart.get('part'))
        terminals = [p.get('num') for p in libpart.findall('./pins/pin')]
        if key in libraries or not terminals or None in terminals or len(set(terminals)) != len(terminals):
            raise ValueError('ambiguous or empty native symbol pin inventory')
        libraries[key] = terminals
    inventory = {}
    for ref, meta in conn['components'].items():
        source = meta.get('libsource') or {}
        inventory[ref] = libraries.get((source.get('lib'), source.get('part')))
    return inventory


def bind_design(conn, inventory, params, models):
    from .checks import element_class, is_connector, SPICE_SCALAR, spice_value
    nets = conn['nets']
    if params['ground_net'] not in nets:
        raise ValueError('declared ground is absent from native connectivity')
    nodes = {net: '0' if net == params['ground_net'] else f'n{i}' for i, net in enumerate(sorted(nets), 1)}
    pins = {}
    for net, members in nets.items():
        for member in members:
            ref, _, pin = member.rpartition('.')
            if pin in pins.setdefault(ref, {}):
                raise ValueError(f'duplicate native pin connection {member}')
            pins[ref][pin] = net
    lines, bound, excluded, modeled = [], {}, {}, set()
    for index, (ref, meta) in enumerate(sorted(conn['components'].items())):
        if ref.startswith('#') or meta.get('dnp') or is_connector(meta):
            excluded[ref] = 'virtual' if ref.startswith('#') else 'DNP' if meta.get('dnp') else 'connector'
            continue
        terminals = inventory.get(ref)
        connected = pins.get(ref, {})
        if terminals is None or set(terminals) != set(connected):
            raise ValueError(f'{ref}: missing complete connected native pin inventory; NC/omitted pins unsupported')
        cls = element_class(meta)
        if cls is not None and ref[:1].upper() == cls and ref not in params['active_parts']:
            value = meta['value']
            if len(terminals) != 2 or not SPICE_SCALAR.fullmatch(value):
                raise ValueError(f'{ref}: unsupported passive value or terminals')
            try:
                number(spice_value(value), f'{ref} passive value', positive=True)
            except OverflowError as exc:
                raise ValueError(f'{ref}: passive value exceeds finite range') from exc
            lines.append(f'{cls}{index} ' + ' '.join(nodes[connected[p]] for p in sorted(terminals)) + f' {value}')
            bound[ref] = {'class': cls, 'value': value, 'pins': connected, 'uuid': meta.get('uuid'), 'sheet': meta.get('sheet')}
            continue
        if ref not in params['active_parts']:
            raise ValueError(f'{ref}: populated non-passive component lacks an explicit model')
        part = params['active_parts'][ref]
        sub = models['subcircuits'].get(part['subcircuit'].lower())
        if not sub or sub['path'] != part['model_file']:
            raise ValueError(f'{ref}: named subcircuit absent from declared model file')
        mapping = part['pin_map']
        if (set(mapping) != set(terminals) or len(set(mapping.values())) != len(mapping)
                or set(mapping.values()) != set(sub['formals'])):
            raise ValueError(f'{ref}: pin map must name every native pin and every existing formal exactly once')
        inverse = {formal: pin for pin, formal in mapping.items()}
        ordered = [nodes[connected[inverse[formal]]] for formal in sub['formals']]
        lines.append(f'X{index} ' + ' '.join(ordered) + f" {sub['name']}")
        bound[ref] = {'component': meta, 'model': sub, 'sha256': models['files'][sub['path']],
                      'mapping': [{'formal': f, 'pin': inverse[f], 'net': connected[inverse[f]],
                                   'node': nodes[connected[inverse[f]]]} for f in sub['formals']]}
        modeled.add(ref)
    if set(params['active_parts']) != modeled:
        raise ValueError('active_parts names missing, excluded or unmodeled components')
    return lines, {'components': bound, 'excluded': excluded, 'node_map': nodes, 'native_pins': inventory}


def source_lines(params, nodes):
    lines = []
    for i, source in enumerate(params['sources']):
        positive, negative = nodes[source['positive_net']], nodes[source['negative_net']]
        if positive == negative:
            raise ValueError('source terminals must be different native nets')
        driver = f'source_driver{i}' if 'current_limit' in source else positive
        line = f'V{i} {driver} {negative} DC {source["dc_v"]:.17g}'
        if 'ac_magnitude_v' in source:
            line += f' AC {source["ac_magnitude_v"]:.17g}'
        if 'step' in source:
            step = source['step']
            points = [(0, step['initial_v'])]
            if step['delay_s'] > 0:
                points.append((step['delay_s'], step['initial_v']))
            points.append((step['delay_s'] + step['rise_s'], step['final_v']))
            line += ' PWL(' + ' '.join(f'{t:.17g} {v:.17g}' for t, v in points) + ')'
        lines.append(line)
        if 'current_limit' in source:
            limit = source['current_limit']
            # Positive current is delivered into the declared positive terminal.
            lines.append(f'Blimit{i} {negative} {positive} I={{min(max('
                f'V({driver},{positive})/{limit["resistance_ohm"]:.17g},'
                f'-{limit["sink_a"]:.17g}),{limit["source_a"]:.17g})}}')
    for i, source in enumerate(params.get('current_sources', [])):
        positive, negative = nodes[source['positive_net']], nodes[source['negative_net']]
        if positive == negative:
            raise ValueError('source terminals must be different native nets')
        # SPICE current flows from the positive node through the source: a load sinks it.
        line = f'I{i} {positive} {negative} DC {source["dc_a"]:.17g}'
        if 'step' in source:
            step = source['step']
            points = [(0, step['initial_a'])]
            if step['delay_s'] > 0:
                points.append((step['delay_s'], step['initial_a']))
            points.append((step['delay_s'] + step['rise_s'], step['final_a']))
            line += ' PWL(' + ' '.join(f'{t:.17g} {v:.17g}' for t, v in points) + ')'
        lines.append(line)
    return lines


def sweep_source(params, analysis):
    return f"V{[s['id'] for s in params['sources']].index(analysis['source'])}"


def analysis_command(params, analysis):
    if analysis['kind'] == 'transient':
        return f'.tran {analysis["step_s"]:.17g} {analysis["stop_s"]:.17g}'
    if analysis['kind'] == 'dc':
        step = (analysis['stop_v'] - analysis['start_v']) / (analysis['points'] - 1)
        return (f'.dc {sweep_source(params, analysis)} {analysis["start_v"]:.17g} '
                f'{analysis["stop_v"]:.17g} {step:.17g}')
    return f'.ac dec {analysis["points_per_decade"]} {analysis["start_hz"]:.17g} {analysis["stop_hz"]:.17g}'


# ngspice names the swept source axis v(v-sweep) in DC transfer traces.
AXES = {'transient': ('time', 'time'), 'ac': ('frequency', 'frequency'), 'dc': ('v(v-sweep)', 'voltage')}


def read_trace(path, kind):
    """Validate ngspice native float64 real/complex binary shape and axis."""
    if Path(path).stat().st_size > 128_000_000:
        raise ValueError('raw trace exceeds 128 MB')
    payload = Path(path).read_bytes()
    header_bytes, separator, tail = payload.partition(b'Binary:\n')
    if not separator:
        raise ValueError('missing binary raw trace')
    header = header_bytes.decode('ascii')
    def field(name):
        match = re.search(r'^' + re.escape(name) + r':\s*(.+)$', header, re.M)
        if not match:
            raise ValueError(f'missing raw {name}')
        return match[1].strip()
    complex_data = kind == 'ac'
    if field('Flags') != ('complex' if complex_data else 'real'):
        raise ValueError('wrong real/complex trace flags')
    expected_plot = {'ac': 'AC Analysis', 'dc': 'DC transfer characteristic'}.get(kind, 'Transient Analysis')
    if field('Plotname') != expected_plot:
        raise ValueError('wrong raw analysis')
    nvars, npts = int(field('No. Variables')), int(field('No. Points'))
    if not 2 <= npts <= 1_000_001 or not 2 <= nvars <= 10000 or npts * nvars > 8_000_000:
        raise ValueError('raw dimensions outside bounds')
    variables = re.findall(r'^[ \t]+(\d+)[ \t]+(\S+)[ \t]+(\S+)(?:[ \t]+grid=3)?[ \t]*$', header.split('Variables:\n', 1)[-1], re.M)
    if len(variables) != nvars or [int(v[0]) for v in variables] != list(range(nvars)):
        raise ValueError('invalid raw variable inventory')
    names = [v[1].lower() for v in variables]
    axis_name, axis_type = AXES[kind]
    if len(set(names)) != nvars or names[0] != axis_name:
        raise ValueError('invalid raw axis or duplicate vectors')
    if variables[0][2] != axis_type:
        raise ValueError('wrong raw axis units')
    if any(v[2] != 'voltage' for v in variables[1:]):
        raise ValueError('unexpected raw vector type')
    stride = nvars * (2 if complex_data else 1)
    if len(tail) != 8 * stride * npts:
        raise ValueError('raw data length contradicts header')
    values = array('d')
    values.frombytes(tail)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('nonfinite raw sample')
    columns = {}
    for col, name in enumerate(names):
        offset = col * (2 if complex_data else 1)
        columns[name] = [complex(values[row * stride + offset], values[row * stride + offset + 1])
                         if complex_data else values[row * stride + offset] for row in range(npts)]
    axis = columns[names[0]]
    axis = [v.real for v in axis] if complex_data else axis
    if any(b <= a for a, b in zip(axis, axis[1:])):
        raise ValueError('raw axis is not strictly increasing')
    return axis, columns


def interpolate(axis, samples, at):
    from bisect import bisect_left
    if not axis[0] <= at <= axis[-1]:
        raise ValueError('measurement outside retained trace')
    i = bisect_left(axis, at)
    if axis[i] == at:
        return samples[i]
    fraction = (at - axis[i-1]) / (axis[i] - axis[i-1])
    return samples[i-1] + fraction * (samples[i] - samples[i-1])


def extract_metrics(path, analysis, nodes, condition):
    axis, columns = read_trace(path, analysis['kind'])
    expected = {f'v({n})' for n in nodes.values() if n != '0'}
    if set(columns) - {AXES[analysis['kind']][0]} != expected:
        raise ValueError('raw voltage vectors differ from complete native node inventory')
    if analysis['kind'] == 'dc':
        start, stop, count = analysis['start_v'], analysis['stop_v'], analysis['points']
        step, tolerance = (stop - start) / (count - 1), 1e-9 * (stop - start)
        if len(axis) != count or any(abs(v - (start + i * step)) > tolerance for i, v in enumerate(axis)):
            raise ValueError('DC sweep samples differ from declared sweep')
        # Accumulated solver rounding stays within tolerance; measure on the declared grid.
        axis = [start + i * step for i in range(count - 1)] + [stop]
    if 'stop_s' in analysis and (axis[0] != 0 or not math.isclose(axis[-1], analysis['stop_s'], rel_tol=1e-9)):
        raise ValueError('incomplete transient trace interval')
    if 'stop_s' in analysis:
        # The final solver time rounds to within 1e-9 of stop_s; a declared at stop_s is in the trace.
        axis = [*axis[:-1], analysis['stop_s']]
    if 'points_per_decade' in analysis:
        count = math.floor(math.log10(analysis['stop_hz'] / analysis['start_hz']) * analysis['points_per_decade'] + 1e-7) + 1
        if len(axis) != count or not math.isclose(axis[0], analysis['start_hz'], rel_tol=1e-9):
            raise ValueError('incomplete AC trace interval')
        ratio = 10 ** (1 / analysis['points_per_decade'])
        if any(not math.isclose(b/a, ratio, rel_tol=1e-9) for a, b in zip(axis, axis[1:])):
            raise ValueError('AC frequency samples differ from declared sweep')
    def voltage(positive, negative, at):
        def terminal(net):
            node = nodes[net]
            return 0 if node == '0' else interpolate(axis, columns[f'v({node})'], at)
        return terminal(positive) - terminal(negative)
    def window(positive, negative, low, high):
        inside = [x for x in axis if low < x < high]
        return [voltage(positive, negative, x) for x in (low, *inside, high)]
    metrics, limits = {}, {}
    for meas in analysis['measurements']:
        observable, nets = meas['observable'], (meas.get('positive_net'), meas.get('negative_net'))
        if observable == 'voltage':
            value = voltage(*nets, meas['at_s' if analysis['kind'] == 'transient' else 'at_v'])
        elif observable == 'voltage_change':
            low, high = ('from_s', 'to_s') if analysis['kind'] == 'transient' else ('from_v', 'to_v')
            value = voltage(*nets, meas[high]) - voltage(*nets, meas[low])
        elif observable in ('voltage_minimum', 'voltage_maximum'):
            # Extremes of the linear interpolant lie at window ends or retained samples.
            samples = window(*nets, meas['from_s'], meas['to_s'])
            value = min(samples) if observable == 'voltage_minimum' else max(samples)
        else:
            at = meas['at_hz']
            output = voltage(meas['output_positive_net'], meas['output_negative_net'], at)
            input_v = voltage(meas['input_positive_net'], meas['input_negative_net'], at)
            if abs(input_v) <= 1e-30:
                raise ValueError('zero AC transfer denominator')
            value = abs(output / input_v)
        if not math.isfinite(value):
            raise ValueError('nonfinite extracted measurement')
        operating = 'active-spice/1:' + identity({'configuration': condition, 'analysis': analysis,
            'measurement': {k: v for k, v in meas.items() if k not in ('min', 'max', 'informational')}})
        metrics[meas['name']] = {'value': value, 'unit': meas['unit'], 'condition': operating}
        if 'min' in meas or 'max' in meas:
            limits[meas['name']] = {key: value for key, value in metrics[meas['name']].items() if key != 'value'}
            limits[meas['name']].update({k: meas[k] for k in ('min', 'max') if k in meas})
    return metrics, limits, {'points': len(axis), 'variables': sorted(columns), 'interval': [axis[0], axis[-1]]}


def run_analysis(ctx, directory, argv, mounts=()):
    from .checks import CheckError
    from .executor import execute
    remaining = ctx.remaining()
    if remaining <= 1:
        raise CheckError('interrupted', 'deadline exhausted before active SPICE analysis')
    try:
        record = execute(ctx.inputs, directory, argv, timeout=min(600, remaining), extra_ro=mounts)
    finally:
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                ctx.evidence(path.relative_to(ctx.work).as_posix())
    if record['execution'] != 'ok':
        status = record['execution']
        if status == 'timeout' and remaining < 600:
            status = 'interrupted'
        raise CheckError(status, 'ngspice execution did not complete; retained execution/logs')
    return record


def native_prerequisite(ctx, nid):
    from .checks import CheckError, parse_xml_netlist
    specs = ctx.scope['checks']
    earlier = specs[:next(i for i, spec in enumerate(specs) if spec['id'] == ctx.cid)]
    if not any(s['id'] == nid and s['kind'] == 'netlist' for s in earlier):
        raise CheckError('not_run', 'requires a preceding native netlist check')
    prior_path = ctx.run_dir / 'checks' / f'{nid}.json'
    if not prior_path.is_file():
        raise CheckError('not_run', 'native netlist prerequisite has not run')
    try:
        prior = read_json(prior_path)
        if not isinstance(prior, dict):
            raise ValueError('native prerequisite result must be an object')
        if (prior.get('execution') != 'ok' or prior.get('kind') != 'netlist' or
                prior.get('candidate_id') != ctx.manifest['candidate_id']):
            raise CheckError('not_run', 'native netlist prerequisite is unsuccessful or belongs to another candidate')
        xml = ctx.run_dir / 'work' / nid / 'design.xml'
        conn = parse_xml_netlist(xml)
        if conn != read_json(xml.with_name('connectivity.json')):
            raise ValueError('native XML and prerequisite connectivity disagree')
        inventory = native_inventory(xml, conn)
    except (ValueError, KeyError, TypeError, OSError, ET.ParseError) as exc:
        raise CheckError('malformed', f'invalid retained native netlist evidence: {exc}') from exc
    ctx.result['evidence'].extend([f'work/{nid}/design.xml', f'work/{nid}/connectivity.json'])
    return conn, inventory


def check_active_spice(ctx):
    from .checks import CheckError, NGSPICE_FAIL_PATTERNS
    from .measurements import finish
    report = None
    try:
        params = validate_params(ctx.params)
        if 'template' in params:
            from .spice_templates import expand
            params, report = expand(params, ctx.scope, ctx.manifest['files'], ctx.inputs)
            write_json(ctx.work / 'template-expansion.json', report)
            ctx.evidence('template-expansion.json')
            ctx.result['details']['template'] = report
            if params is None:
                raise ValueError(report['summary'])
        conn, inventory = native_prerequisite(ctx, params['netlist_check'])
        if report is not None:
            from .spice_templates import confirm_terminals
            confirm_terminals(report, conn)
        models = capture_models(ctx.inputs, ctx.manifest['files'], params['model_files'])
        lines, binding = bind_design(conn, inventory, params, models)
        nodes = binding['node_map']
        lines += source_lines(params, nodes)
        # Check every observable locator before invoking the solver.
        for analysis in params['analyses']:
            for measurement in analysis['measurements']:
                for key, net in measurement.items():
                    if key.endswith('_net') and net not in nodes:
                        raise ValueError(f'unknown native measurement net {net!r}')
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, ET.ParseError) as exc:
        raise CheckError('unsupported', f'active SPICE declaration/model unsupported: {exc}') from exc
    model_dir = ctx.work / 'models'
    for rel, text in models['staged'].items():
        path = model_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        ctx.evidence(f'models/{rel}')
    manifest = {k: v for k, v in models.items() if k != 'staged'}
    manifest['generated_sha256'] = {p: digest(model_dir / p) for p in models['files']}
    write_json(ctx.work / 'model-manifest.json', manifest)
    write_json(ctx.work / 'binding.json', {'candidate_id': ctx.manifest['candidate_id'], **binding})
    ctx.evidence('model-manifest.json', 'binding.json')
    ctx.result['details'].update(binding=binding, models=manifest)
    ctx.result['limitations'].append(
        'Only declared captured models, connected complete native pin inventories, ideal R/C/L, '
        'explicit sources and declared transient/AC/DC scalar conditions are covered. No NC/multi-unit '
        'decomposition, tolerance, thermal, PCB parasitics, EMC or hardware qualification is inferred. '
        'Model provenance does not establish accuracy; effects absent from supplied models remain untested.')
    executable = Path('/usr/bin/ngspice')
    if not executable.is_file():
        raise CheckError('unavailable', '/usr/bin/ngspice is unavailable')
    executable_hash = digest(executable)
    run_analysis(ctx, ctx.work / 'version', [str(executable), '--version'])
    ctx.result['details']['producer'] = {'tool': str(executable), 'sha256': executable_hash,
        'domain': 'electrical', 'models': models['files'], 'dialect': params['dialect']}
    common = ['* agent-ee active-spice/1 native connectivity', f'.temp {params["temperature_c"]:.17g}']
    common += [f'.include /work/models/{p}' for p in models['roots']]
    common += lines
    common += ['.save ' + ' '.join(f'v({n})' for n in nodes.values() if n != '0')]
    metrics, limits, traces = {}, {}, {}
    configuration = {'temperature_c': params['temperature_c'], 'sources': params['sources'],
        'models': models['files'], 'active_parts': params['active_parts'], 'dialect': params['dialect']}
    if 'current_sources' in params:
        configuration['current_sources'] = params['current_sources']
    if report is not None:
        configuration['template'] = {key: report[key] for key in ('library', 'block', 'role')}
    for analysis in params['analyses']:
        directory = ctx.work / analysis['id']
        directory.mkdir()
        command = analysis_command(params, analysis)
        (directory / 'deck.cir').write_text('\n'.join([*common, command, '.end', '']))
        dialect = 'set ngbehavior=ps\n' if params['dialect'] == 'ngspice-ps' else ''
        (directory / 'control.sp').write_text('* coordinator control\n.control\nset num_threads=1\n'
            'set noaskquit\n' + dialect + 'source /work/deck.cir\nrun\nwrite /work/result.raw\nquit\n.endc\n.end\n')
        ctx.evidence(f'{analysis["id"]}/deck.cir', f'{analysis["id"]}/control.sp')
        run_analysis(ctx, directory, [str(executable), '-n', '-b', '-o', '/work/ngspice.log', '/work/control.sp'],
                     [(model_dir, '/work/models')])
        if digest(executable) != executable_hash:
            raise CheckError('malformed', 'ngspice executable changed during simulation')
        log = directory / 'ngspice.log'
        if not log.is_file():
            raise CheckError('malformed', 'ngspice produced no log')
        diagnostics = '\n'.join(path.read_text(errors='replace') for path in
            (log, directory / 'stdout.log', directory / 'stderr.log') if path.is_file())
        for pattern in NGSPICE_FAIL_PATTERNS:
            match = re.search(pattern, diagnostics, re.M)
            if match:
                raise CheckError('failed', f'ngspice solver reported {match[0]}')
        # ngspice can exit successfully and write complete traces after changing a
        # model. No warning is qualified as harmless by this bounded contract.
        semantic_issue = re.search(
            r'^.*(?:\bwarning\b|\bunrecognized\b|\bunknown parameter\b|'
            r'\bignored\b|\bunsupported\b|\bsubstitut(?:ed|ing)\b|'
            r'\breplaced\b|\breset to\b|\bclamped\b|\bout of range\b).*$',
            diagnostics, re.I | re.M)
        if semantic_issue:
            raise CheckError('unsupported', 'ngspice model/device semantics unqualified: '
                             + semantic_issue[0].strip())
        try:
            measured, bounded, raw = extract_metrics(directory / 'result.raw', analysis, nodes, configuration)
        except (ValueError, KeyError, OSError, UnicodeError, OverflowError) as exc:
            raise CheckError('malformed', f'invalid active SPICE trace/measurement: {exc}') from exc
        metrics.update(measured)
        limits.update(bounded)
        traces[analysis['id']] = raw
    # No partial metrics escape as passing evidence when any analysis fails.
    write_json(ctx.work / 'measurements.json', {'metrics': metrics, 'limits': limits, 'traces': traces})
    ctx.evidence('measurements.json')
    ctx.result['details'].update(metrics=metrics, traces=traces)
    if limits:
        try:
            finish(ctx, metrics, limits)
        except (ValueError, KeyError, TypeError) as exc:
            raise CheckError('malformed', str(exc)) from exc
    else:
        ctx.result.update(execution='ok', outcome='info', summary='active SPICE analyses completed with informational measurements')
    unsupported = [m['name'] for m in (report or {}).get('measurements', []) if m['execution'] == 'unsupported']
    if unsupported and ctx.result['outcome'] != 'defect':
        # A template measurement that could not be expanded is missing coverage; the
        # completed ones cannot stand in for it. A complete bound violation stays a defect.
        ctx.result.update(execution='unsupported', outcome='unknown',
                          summary='template measurements unsupported: ' + ', '.join(unsupported))
    elif unsupported:
        ctx.result['summary'] += '; template measurements unsupported: ' + ', '.join(unsupported)


def describe_active_spice(ctx):
    descriptor = capability(ctx.params, ctx.files, ctx.inputs, ctx.scope)
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    for path in descriptor['immutable_inputs']:
        ctx.captured(path, immutable=True)
    ctx.result['capability_version'] = descriptor['contract']
    ctx.result['predicates'] = [f'active-spice:{name}' for name in descriptor['bounded_metrics']]


if __name__ != '__main__':
    _register_check('active_spice', describe_active_spice, check_active_spice,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
