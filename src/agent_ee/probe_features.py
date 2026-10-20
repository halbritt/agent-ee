"""Captured, required bare copper probes; no purchased identity or inferred exemption."""
import base64
import binascii

from .board_net_names import encode_net
from .catalog import exact_fields, nonempty, strict_json
from .storage import canonical, identity
from .symbol_library import children, field, parse_library, parse_sexpr, resolve_symbol, nanometres

KIND = 'bare_copper_probe'
PROPERTY = 'AGENT_EE_Probe'
MARKER_PREFIX = 'agent-ee-probe-v1-'
PROBE_FIELDS = frozenset(('Reference', 'Value', 'Footprint', 'Datasheet', 'Description',
                         PROPERTY, 'Sheetname', 'Sheetfile', 'exclude_from_bom'))


def is_probe(part):
    return isinstance(part, dict) and part.get('kind') == KIND


def validate_part(part):
    from .parts_manifest import _blob
    exact_fields(part, ('kind', 'id', 'role', 'required', 'instances', 'lib_id', 'footprint', 'probe'))
    if part['kind'] != KIND or part['required'] is not True:
        raise ValueError('bare_copper_probe must be required')
    for key in ('id', 'role', 'lib_id', 'footprint'):
        nonempty(part[key])
    for key in ('lib_id', 'footprint'):
        if part[key].count(':') != 1 or not all(part[key].split(':')):
            raise ValueError('probe requires exact library:member identity')
    exact_fields(part['probe'], ('symbol', 'footprint', 'pin'))
    for key in ('symbol', 'footprint'):
        _blob(part['probe'][key])
    exact_fields(part['probe']['pin'], ('number', 'name', 'electrical_type'))
    for key in ('number', 'name'):
        nonempty(part['probe']['pin'][key])
    if part['probe']['pin']['electrical_type'] != 'passive':
        raise ValueError('probe requires exactly one passive pin')
    if not isinstance(part['instances'], list) or not part['instances']:
        raise ValueError('probe requires explicit instances')
    for selector in part['instances']:
        exact_fields(selector, ('sheet', 'reference', 'net', 'native_net'))
        for value in selector.values():
            nonempty(value)
        if not selector['sheet'].startswith('/') or not selector['sheet'].endswith('/'):
            raise ValueError('probe requires exact native sheet selector')


def properties(node):
    result = {}
    for prop in children(node, 'property'):
        if len(prop) < 3 or prop[1] in result:
            raise ValueError('probe has duplicate/malformed property')
        result[prop[1]] = prop[2]
    return result


def no_supplier(props):
    if set(props) - PROBE_FIELDS:
        raise ValueError('probe carries supplier, DNP, assembly or unsupported property claim')
    if props.get('Datasheet', ''):
        raise ValueError('probe carries a datasheet claim')


def symbol_signature(node, lib_id, pin):
    if children(node, 'extends') or children(node, 'power'):
        raise ValueError('probe cannot inherit or be a power symbol')
    if field(node, 'in_bom', 1) != ['no'] or field(node, 'on_board', 1) != ['yes']:
        raise ValueError('probe symbol requires in_bom=no/on_board=yes')
    no_supplier(properties(node))
    name = node[1]
    pins, _, power = resolve_symbol({name: node}, name, '', '')
    if power or len(pins) != 1 or any(pins[0][k] != pin[k] for k in pin):
        raise ValueError('probe symbol pin identity differs')
    # The emitter changes only the top-level name to the fully qualified id.
    return identity([node[0], lib_id, *node[2:]])


def _one(node, key, optional=None):
    found = children(node, key)
    if not found and optional is not None:
        return optional
    if len(found) != 1:
        raise ValueError('probe requires exactly one ' + key)
    return found[0][1:]


def footprint_signature(node, number):
    """Qualify actual geometry, including saved native footprints, without trusting a marker."""
    if node[0] != 'footprint' or _one(node, 'layer') != ['F.Cu']:
        raise ValueError('probe requires a front surface footprint')
    allowed = {'version', 'generator', 'generator_version', 'layer', 'uuid', 'tstamp', 'at',
        'property', 'fp_text', 'attr', 'fp_circle', 'fp_line', 'fp_rect', 'fp_arc', 'fp_poly',
        'pad', 'path', 'sheetname', 'sheetfile', 'embedded_fonts', 'locked',
        'duplicate_pad_numbers_are_jumpers'}
    for child in node[2:]:
        if not isinstance(child, list) or child[0] not in allowed:
            raise ValueError('probe has unsupported footprint geometry or flags')
        if child[0].startswith('fp_') or child[0] == 'property':
            layers = children(child, 'layer')
            if any(len(x) < 2 or x[1] not in ('F.SilkS', 'F.Fab', 'F.CrtYd', 'Cmts.User') for x in layers):
                raise ValueError('probe has extra copper/mask/paste graphics')
            if not layers:
                raise ValueError('probe graphic/property needs explicit non-copper layer')
    attrs = _one(node, 'attr')
    if set(attrs) != {'smd', 'exclude_from_bom', 'exclude_from_pos_files'} or len(attrs) != 3:
        raise ValueError('probe requires exact native smd BOM/position exclusions; no DNP/off-board')
    no_supplier(properties(node))
    pads = children(node, 'pad')
    if len(pads) != 1 or pads[0][1:4] != [number, 'smd', 'circle']:
        raise ValueError('probe requires exactly one circular surface pad')
    pad = pads[0]
    allowed_pad = {'at', 'size', 'layers', 'solder_mask_margin', 'uuid', 'tstamp', 'net',
                   'pinfunction', 'pintype'}
    if any(not isinstance(c, list) or c[0] not in allowed_pad for c in pad[4:]):
        raise ValueError('probe pad has drill, paste, hidden copper or unsupported geometry')
    at = _one(pad, 'at')
    if len(at) not in (2, 3) or any(nanometres(x) for x in at):
        raise ValueError('probe pad must be at the footprint origin')
    size = _one(pad, 'size')
    if len(size) != 2 or nanometres(size[0]) <= 0 or nanometres(size[0]) != nanometres(size[1]):
        raise ValueError('probe pad must be a positive circle')
    if _one(pad, 'layers') != ['F.Cu', 'F.Mask']:
        raise ValueError('probe pad requires only exposed F.Cu/F.Mask, no paste')
    margin = _one(pad, 'solder_mask_margin', ['0'])
    if len(margin) != 1 or nanometres(margin[0]) < 0:
        raise ValueError('probe pad mask opening must expose all copper')
    return {'diameter_nm': nanometres(size[0]), 'mask_margin_nm': nanometres(margin[0])}


def qualify(part, root):
    from .parts_manifest import _read_blob
    validate_part(part)
    symbols = parse_library(_read_blob(root, part['probe']['symbol']))
    name = part['lib_id'].split(':')[1]
    if name not in symbols:
        raise ValueError('probe captured symbol missing: ' + part['id'])
    symbol = symbol_signature(symbols[name], part['lib_id'], part['probe']['pin'])
    fp = parse_sexpr(_read_blob(root, part['probe']['footprint']))
    if fp[1] != part['footprint'].split(':')[1]:
        raise ValueError('probe captured footprint identity differs: ' + part['id'])
    geometry = footprint_signature(fp, part['probe']['pin']['number'])
    return {'schema': 'agent-ee.bare-copper-probe/1', 'part_id': part['id'],
            'lib_id': part['lib_id'], 'footprint': part['footprint'], 'pin': part['probe']['pin'],
            'symbol': part['probe']['symbol'], 'footprint_source': part['probe']['footprint'],
            'symbol_body_sha256': symbol, 'geometry': geometry}


def declarations(document, root):
    """Call only after qualify_manifest: source custody is the manifest's immutable closure."""
    result = {}
    for part in document['parts']:
        if is_probe(part):
            base = qualify(part, root)
            for selector in part['instances']:
                if selector['reference'] in result:
                    raise ValueError('probe references must be globally unique')
                result[selector['reference']] = {**base, **selector}
    return result


def marker(record):
    # KiBot treats colons in footprint properties as library separators.
    return MARKER_PREFIX + base64.b64encode(canonical(record)).decode('ascii')


def parse_marker(text):
    if text.startswith(MARKER_PREFIX):
        try:
            payload = base64.b64decode(text[len(MARKER_PREFIX):], validate=True)
            decoded = payload.decode('utf-8')
        except (ValueError, binascii.Error) as exc:
            raise ValueError('invalid encoded probe identity') from exc
        if MARKER_PREFIX + base64.b64encode(payload).decode('ascii') != text:
            raise ValueError('noncanonical encoded probe identity')
        text = decoded
    value = strict_json(text)
    exact_fields(value, ('schema', 'part_id', 'lib_id', 'footprint', 'pin', 'symbol',
        'footprint_source', 'symbol_body_sha256', 'geometry', 'sheet', 'reference', 'net', 'native_net'))
    if value['schema'] != 'agent-ee.bare-copper-probe/1':
        raise ValueError('unsupported probe identity')
    return value


def matches_marker(text, record):
    # Preserve exact legacy JSON identity checks while new writers use base64.
    return text in (marker(record), canonical(record).decode())


def native_component(record, comp, graph):
    ref = record['reference']
    if (comp['lib_id'] != record['lib_id'] or comp['footprint'] != record['footprint'] or
            comp['sheet'] != record['sheet'] or not comp['populated'] or
            not comp['on_board'] or comp['in_bom']):
        raise ValueError(ref + ': probe native identity/flags differ')
    no_supplier(comp['properties'])
    if not matches_marker(comp['properties'].get(PROPERTY), record):
        raise ValueError(ref + ': probe declaration identity differs')
    pin = record['pin']['number']
    if set(comp['pins']) != {pin} or any(comp['pin_metadata'][pin][k] != record['pin'][k] for k in ('name', 'electrical_type')):
        raise ValueError(ref + ': probe native pin identity differs')
    names = [n['name'] for n in graph['nets'].values() if (ref, pin) in n['terminals']]
    if names != [record['native_net']]:
        raise ValueError(ref + ': probe native net differs')


def validate_schematic(data, records):
    if not records and PROPERTY.encode() not in data:
        return set()
    root = parse_sexpr(data)
    libraries = children(root, 'lib_symbols')
    embedded = {s[1]: s for lib in libraries for s in children(lib, 'symbol')}
    found = set()
    for sym in children(root, 'symbol'):
        props = properties(sym)
        ref = props.get('Reference')
        if PROPERTY not in props and ref not in records:
            continue
        if ref not in records:
            raise ValueError(str(ref) + ': probe has no immutable source declaration')
        r = records[ref]
        if not matches_marker(props.get(PROPERTY), r) or field(sym, 'lib_id', 1) != [r['lib_id']]:
            raise ValueError(ref + ': probe declaration identity differs')
        if (field(sym, 'in_bom', 1) != ['no'] or field(sym, 'on_board', 1) != ['yes'] or
                _one(sym, 'dnp', ['no']) != ['no'] or props.get('Footprint') != r['footprint']):
            raise ValueError(ref + ': probe schematic flags/footprint differ')
        no_supplier(props)
        if symbol_signature(embedded[r['lib_id']], r['lib_id'], r['pin']) != r['symbol_body_sha256']:
            raise ValueError(ref + ': probe embedded symbol changed')
        if ref in found:
            raise ValueError(ref + ': duplicate probe')
        found.add(ref)
    return found


def validate_board(data, records, *, require_all=True):
    if not records and PROPERTY.encode() not in data:
        return set()
    root = parse_sexpr(data)
    found = set()
    for fp in children(root, 'footprint'):
        props = properties(fp)
        ref = props.get('Reference')
        if PROPERTY not in props and ref not in records:
            continue
        if ref not in records:
            raise ValueError(str(ref) + ': probe has no immutable source declaration')
        r = records[ref]
        if not matches_marker(props.get(PROPERTY), r) or fp[1] != r['footprint']:
            raise ValueError(ref + ': probe declaration/footprint identity differs')
        if footprint_signature(fp, r['pin']['number']) != r['geometry']:
            raise ValueError(ref + ': probe saved pad geometry changed')
        nets = _one(children(fp, 'pad')[0], 'net')
        if (len(nets) not in (1, 2) or nets[-1] != encode_net(r['native_net']) or
                (len(nets) == 2 and not nets[0].isdigit())):
            raise ValueError(ref + ': probe saved pad net differs')
        pad = children(fp, 'pad')[0]
        if (_one(pad, 'pinfunction', [r['pin']['name']]) != [r['pin']['name']] or
                _one(pad, 'pintype', [r['pin']['electrical_type']]) != [r['pin']['electrical_type']]):
            raise ValueError(ref + ': probe saved pin metadata differs')
        if ref in found:
            raise ValueError(ref + ': duplicate probe')
        found.add(ref)
    if require_all and found != set(records):
        raise ValueError('missing probe feature(s): ' + ', '.join(sorted(set(records) - found)))
    return found


def load_declarations(root, path):
    from .parts_manifest import load_manifest, qualify_manifest
    document = load_manifest(root, path)
    qualified = qualify_manifest(document, root)
    return declarations(document, root), qualified['dependencies']


def membership_check(params, scope):
    """The feature source must be the same manifest and netlist as parts_subset."""
    matches = [spec['id'] for spec in scope.get('checks', []) if spec['kind'] == 'parts_subset' and
               spec.get('params', {}).get('manifest') == params['probe_manifest'] and
               spec.get('params', {}).get('netlist_check') == params['netlist_check']]
    if len(matches) != 1:
        raise ValueError('probe admission requires one matching parts_subset source check')
    return matches[0]


def load_for_check(ctx, params=None):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    from .storage import digest, read_json
    params = ctx.params if params is None else params
    try:
        cid = membership_check(params, ctx.scope)
    except ValueError as exc:
        raise CheckError('unsupported', str(exc)) from exc
    path = prerequisite(ctx, cid, 'parts_subset', 'parts-subset.json')
    xml = prerequisite(ctx, params['netlist_check'], 'netlist', 'design.xml')
    report = read_json(path)
    result = read_json(ctx.run_dir / 'checks' / (cid + '.json'))
    if (result.get('outcome') != 'pass' or report.get('execution') != 'ok' or
            report.get('outcome') != 'pass' or report.get('coverage') != 'complete' or
            report.get('manifest_sha256') != digest(ctx.inputs / params['probe_manifest']) or
            report.get('native_xml_sha256') != digest(xml)):
        raise CheckError('unsupported', 'probe admission requires passing, source-bound parts_subset evidence')
    return load_declarations(ctx.inputs, params['probe_manifest'])[0]


def immutable_inputs(params, files, inputs, scope):
    if 'probe_manifest' not in params:
        return []
    from .parts_manifest import dependency_closure, load_manifest, _path
    path = _path(params['probe_manifest'])
    doc = load_manifest(inputs, path)
    paths = sorted({path, *dependency_closure(doc, inputs)})
    for rel in paths:
        if rel not in files or any(rel == m or rel.startswith(m.rstrip('/') + '/') for m in scope.get('mutable_inputs', [])):
            raise ValueError('probe source must be captured and immutable: ' + rel)
    return paths
