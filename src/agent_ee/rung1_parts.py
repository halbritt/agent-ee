"""Adapt a captured parts/1 bundle to Rung 1; no sourcing or approval inference."""
from decimal import Decimal
from pathlib import Path

from . import parts_manifest
from .catalog import quantity, strict_json
from .storage import canonical, digest, write_json


MANIFEST = 'approved-parts.json'


def check_cad_inputs(qualified, root, symbols, footprints):
    """The driver must emit the same CAD members that its parts bundle qualified."""
    from .symbol_library import parse_library
    from .footprint_library import _members
    expected_footprints = {library + ':' + Path(blob['path']).stem: blob['sha256']
                           for library, blob in _members(footprints)}
    source_trees, emitted_trees = {}, {}
    for assignment in qualified['cad']['assignments']:
        library = assignment['lib_id'].split(':')[0]
        if library not in emitted_trees:
            emitted_trees[library] = parse_library(symbols[library + '.kicad_sym'])
        for source in assignment['symbol_sources']:
            path = source['path']
            if path not in source_trees:
                source_trees[path] = parse_library(parts_manifest.read_captured(root, path))
            if source_trees[path][source['symbol']] != emitted_trees[library].get(source['symbol']):
                raise ValueError('emitted symbol differs from captured parts CAD: ' + assignment['lib_id'])
        if expected_footprints.get(assignment['footprint']) != assignment['footprint_source']['sha256']:
            raise ValueError('emitted footprint differs from captured parts CAD: ' + assignment['footprint'])


def fields_for_rung(document, qualified, params):
    """Bind the three exact physical roles, retaining canonical selection evidence."""
    if params['header']['assembly'] != 'assembled':
        raise ValueError('captured-parts route requires an explicit JLCPCB-assembled header in the spec')
    if (document['property_fields']['lcsc'] != 'LCSC' or
            document['property_fields']['manufacturer'] != 'Manufacturer' or
            document['property_fields']['mpn'] not in ('MPN', 'Partnumber', 'PartNumber')):
        raise ValueError('rung 1 requires LCSC/Manufacturer and a supported part-number field')
    roles = {
        params['resistor']['ref']: ('Device:R', 'Resistor_SMD:R_0805_2012Metric', 'resistor'),
        params['capacitor']['ref']: ('Device:C', 'Capacitor_SMD:C_0805_2012Metric', 'capacitor'),
        params['header']['ref']: ('Connector_Generic:Conn_01x03',
                                'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', 'connector'),
    }
    from .probe_features import is_probe
    if any(is_probe(p) for p in document['parts']):
        raise ValueError('rung-1 RC producer does not support bare_copper_probe features')
    from .copper_features import require_purchased
    for part in document['parts']:
        require_purchased(part, 'rung-1 RC producer')
    selections = {p['id']: p for p in qualified['selection']['parts']}
    fields, records = {}, {}
    for part in document['parts']:
        if len(part['instances']) != 1 or part['instances'][0]['sheet'] != '/' or not part['required']:
            raise ValueError('rung 1 requires one required root-sheet instance per part')
        ref = part['instances'][0]['reference']
        if ref not in roles or ref in fields:
            raise ValueError('rung-1 parts must name exactly R, C and header roles from the spec')
        symbol, footprint, category = roles[ref]
        selection = selections[part['selection_id']]
        row = selection['chosen']
        if (part['lib_id'], part['footprint'], row['category']) != (symbol, footprint, category):
            raise ValueError('rung-1 CAD/category mismatch: ' + ref)
        attribute = {'resistor': 'resistance', 'capacitor': 'capacitance'}.get(category)
        if attribute:
            if row['package'] != '0805':
                raise ValueError('rung-1 passive package must be exactly 0805: ' + ref)
            nominal = quantity(params[category]['value'] + ('ohm' if category == 'resistor' else 'F'),
                               'ohm' if category == 'resistor' else 'F')
            interval = row['normalized'][attribute]
            if (Decimal(interval['min']) + Decimal(interval['max'])) / 2 != nominal:
                raise ValueError('selected passive nominal differs from the spec: ' + ref)
        fields[ref] = {'Footprint': footprint, 'LCSC': part['lcsc'], 'Manufacturer': part['manufacturer'],
                       'Partnumber': part['mpn'], document['property_fields']['mpn']: part['mpn']}
        for name, interval in row['normalized'].items():
            fields[ref]['PARAM_' + name] = canonical(interval).decode('utf-8')
        records[ref] = {'selection_id': part['selection_id'], 'chosen': row,
                        'tie_break': selection['tie_break'], 'rank_order': qualified['selection']['rank_order']}
    if set(fields) != set(roles):
        raise ValueError('rung-1 parts must cover all three physical roles')
    return fields, records


def stage_parts(stage, state, source_manifest):
    """Freeze and replay a supplied bundle before any native design is written."""
    source = Path(source_manifest).absolute()
    source_raw = parts_manifest.read_captured(source.parent, source.name)
    document = parts_manifest.validate_manifest(strict_json(source_raw))
    qualified = parts_manifest.qualify_manifest(document, source.parent)
    fields, selections = fields_for_rung(document, qualified, state['params'])
    captured = stage.dir / 'approved'
    captured.mkdir()
    if MANIFEST in qualified['dependencies']:
        raise ValueError('reserved bundle manifest path: ' + MANIFEST)
    for path in qualified['dependencies']:
        target = captured / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(parts_manifest.read_captured(source.parent, path))
    (captured / MANIFEST).write_bytes(source_raw)
    # Replay captured bytes, catching a source change during copying as well as stale pins.
    replay = parts_manifest.qualify_manifest(parts_manifest.load_manifest(captured, MANIFEST), captured)
    if replay != qualified:
        raise ValueError('parts bundle changed during capture')
    wrapper = {'schema': 'agent-ee.rung1-parts-manifest/0-experimental',
               'qualification': 'experimental-unqualified', 'parts': fields, 'selections': selections,
               'catalog': document['catalog'], 'assembly_exclusions': [],
               'approved_bundle': {'manifest': MANIFEST, 'sha256': digest(captured / MANIFEST),
                                   'dependencies': replay['dependencies']},
               'assembly_decision': 'All three catalog-bound parts, including J1, are assembled as stated in spec.',
               'missing_provenance': replay['missing_provenance']}
    path = stage.dir / 'parts-manifest.json'
    write_json(path, wrapper)
    stage.input(captured, 'captured parts/1 declaration, catalog snapshot, selection and CAD closure')
    stage.output(captured, 'immutable parts/1 bundle')
    stage.output(path, 'rung-1 property projection of the replayed parts/1 bundle')
    stage.receipt['details']['parts'] = fields
    stage.receipt['details']['approved_bundle'] = wrapper['approved_bundle']
    stage.receipt['limitations'].append(parts_manifest.LIMIT)
    state['parts'] = path
