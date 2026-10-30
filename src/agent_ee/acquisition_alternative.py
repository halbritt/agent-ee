"""Offline ADS1115 DGS carrier selection for the captured acquisition family.

Call only on an owned, unsealed revision. No native edits, receipts or acceptance.
"""
import copy
from dataclasses import dataclass
from pathlib import Path
import re

from . import assembly_readiness, part_record, part_selection, parts_manifest, requirement_map
from .catalog import strict_json
from .storage import json_bytes, identity

PRIMARY = 'C37593'
ALTERNATIVE = 'C468683'
ORDERABLES = {PRIMARY: 'ADS1115IDGSR', ALTERNATIVE: 'ADS1115IDGST'}
PREFIX = 'preparation/alternative'


@dataclass(frozen=True)
class CapturedContext:
    """Pins cover the entire owned root, including all context dependencies.

    as_of is the actual preparation evaluation anchor, never a new capture time.
    Source blobs use existing canonical {path, sha256} captured-file contracts.
    """
    inventory: dict
    catalog: dict
    carrier_page: dict
    ordering_text: dict
    as_of: str


def _read(root, blob):
    return parts_manifest._read_blob(root, blob)


def _blob(root, path):
    return dict(path=path, sha256=parts_manifest.captured_sha256(root, path))


def _assembly_check(document):
    found = []

    def visit(node):
        if isinstance(node, dict):
            if node.get('kind') == 'assembly_readiness':
                found.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(document)
    if len(found) != 1:
        raise ValueError('acquisition family requires exactly one assembly stock policy')
    return found[0]


def _carrier_evidence(root, context):
    page = _read(root, context.carrier_page).decode('utf-8')
    text = _read(root, context.ordering_text).decode('utf-8')
    if ('https://www.ti.com/product/ADS1115/part-details/ADS1115IDGST' not in page or
            'These products are exactly the same but come in a different carrier type' not in page or
            'OPN=ADS1115IDGSR' not in page):
        raise ValueError('missing captured TI same-device carrier correspondence')
    for mpn, quantity, carrier in [('ADS1115IDGSR', '2500', 'LARGE'), ('ADS1115IDGST', '250', 'SMALL')]:
        pattern = (r'^\s*' + mpn + r'\s+Active\s+Production\s+VSSOP \(DGS\)\s*\|\s*10\s+'
                   + quantity + r'\s*\|\s*' + carrier + r' T&R\s+Yes\s+NIPDAU\s+'
                   r'Level-2-260C-1 YEAR\s+-40 to 125\s+BOGI\s*$')
        if not re.search(pattern, text, re.MULTILINE):
            raise ValueError('captured TI ordering/package/grade correspondence missing: ' + mpn)


def _stock(root, context, parts, policy):
    catalog = strict_json(_read(root, context.catalog))
    info = assembly_readiness._verify_jlc_capture(root, context.catalog['path'], catalog)
    needed = {p['lcsc'] for p in parts} | {PRIMARY, ALTERNATIVE}
    assembly_readiness._capture_gate(info, policy, needed - catalog['parts'].keys())
    if needed - catalog['parts'].keys():
        raise ValueError('missing successful stock evidence for family or selected board parts')
    return catalog, info


def _choose(catalog, adc, policy, required_lcsc=None):
    threshold = max(len(adc['instances']) * policy['boards'], policy['stock_floor'])
    decisions = []
    for lcsc in (PRIMARY, ALTERNATIVE):
        row = catalog['parts'][lcsc]
        reasons = []
        if required_lcsc is not None and lcsc != required_lcsc:
            reasons.append('outside_selected_source_allocation')
        if (row['mpn'] != ORDERABLES[lcsc] or row['mfr'] != 'Texas Instruments' or
                row['package'] not in ('VSSOP-10', 'VSSOP-10-0.5mm')):
            reasons.append('incompatible_identity_package')
        # Reuse the selector's stock/unknown-stock rule; electrical eligibility
        # comes only from the narrow TI correspondence and unchanged CAD below.
        rejection, _ = part_selection.evaluate_row(
            {**row, 'normalized': {}, 'is_basic': row['library_tier'] == 'Basic',
             'is_preferred': row['library_tier'] == 'Preferred'},
            dict(category=row['category'], package=row['package'], constraints={}, minimum_stock=threshold), {})
        if rejection:
            reasons += [r['code'] for r in rejection['reasons']]
        decisions.append(dict(lcsc=lcsc, mpn=row['mpn'], stock=row['stock'], reasons=reasons))
    eligible = [d for d in decisions if not d['reasons']]
    if not eligible:
        raise ValueError('no eligible ADS1115 DGS carrier: ' + repr(decisions))
    return eligible[0]['lcsc'], decisions, threshold


def _refresh_parts(root, document, context, chosen, decision_blob):
    for part in document['parts']:
        lcsc = chosen if part['lcsc'] == PRIMARY else part['lcsc']
        original_record = strict_json(_read(root, dict(path=part['selection']['part_record'],
                                                      sha256=part['selection']['part_record_sha256'])))
        record = part_record.prepare_record(root, lcsc, context.catalog, original_record['datasheets'],
                                            original_record['datasheet_id'],
                                            source_selection=original_record.get('source_selection'))
        if lcsc != chosen and any(record[k] != part[k] for k in ('lcsc', 'mpn', 'manufacturer')):
            raise ValueError('unrelated purchasing identity drift: ' + part['id'])
        name = PREFIX + '/named/' + lcsc + '.json'
        payload = json_bytes(record)
        if (Path(root) / name).exists():
            if (Path(root) / name).read_bytes() != payload:
                raise ValueError('contradictory shared named record: ' + lcsc)
        else:
            (Path(root) / name).parent.mkdir(parents=True, exist_ok=True)
            (Path(root) / name).write_bytes(payload)
        for key in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock'):
            part[key] = record[key]
        part['selection'] = dict(kind='named', lcsc=lcsc, part_record=name,
            part_record_sha256=_blob(root, name)['sha256'],
            decision_ref={**decision_blob, 'locator': 'parts[id=' + part['id'] + ']'})
    # Purchasing decisions do not replace the authored membership requirement.
    return parts_manifest.prepare_manifest(document, root)


def revise(root, context, *, required_lcsc=None):
    """Select and regenerate source bindings before typed_overlay.prepare.

    The caller owns staging/publication/cleanup. Failure leaves only that caller's
    disposable revision; no original source or previously sealed run is changed.
    The complete input inventory must match the caller's immutable context.
    required_lcsc only restricts selection; it grants no source qualification.
    """
    if required_lcsc is not None and (type(required_lcsc) is not str or required_lcsc not in ORDERABLES):
        raise ValueError('required ADC carrier must be a supported literal LCSC id')
    root = Path(root)
    before = requirement_map.input_inventory(root)
    if before != context.inventory:
        raise ValueError('source closure drift before alternative selection')
    if any(name.startswith(PREFIX + '/') for name in before):
        raise ValueError('alternative selection already exists; require a fresh revision')
    document = parts_manifest.load_manifest(root, 'parts.json')
    parts_manifest.qualify_manifest(document, root)
    candidates = [p for p in document['parts'] if p['lcsc'] == PRIMARY]
    if len(candidates) != 1:
        raise ValueError('expected one original ADC allocation')
    adc = candidates[0]
    if (adc['mpn'] != ORDERABLES[PRIMARY] or adc['lib_id'] != 'Analog_ADC:ADS1115IDGS' or
            adc['footprint'] != 'Package_SO:TSSOP-10_3x3mm_P0.5mm' or len(adc['instances']) != 1):
        raise ValueError('unsupported acquisition ADC CAD/pad or purchasing identity')
    if any(p.get('selection', {}).get('kind') != 'named' for p in document['parts']):
        raise ValueError('acquisition family requires captured named parts')
    _carrier_evidence(root, context)
    mapping = strict_json(parts_manifest.read_captured(root, 'requirement-map.json'))
    check = _assembly_check(mapping)
    policy = assembly_readiness.validate_params({**check['params'], 'as_of': context.as_of})
    if (policy['operating_temperature'] is not None or policy['hand_fit_policy'] is not None or
            policy['max_extended_parts'] is not None):
        raise ValueError('extended assembly policies are outside this acquisition family')
    catalog, info = _stock(root, context, document['parts'], policy)
    chosen, decisions, threshold = _choose(catalog, adc, policy, required_lcsc)
    quantities = {}
    for part in document['parts']:
        lcsc = chosen if part['lcsc'] == PRIMARY else part['lcsc']
        quantities[lcsc] = quantities.get(lcsc, 0) + len(part['instances'])
    for lcsc, count in quantities.items():
        if catalog['parts'][lcsc]['stock'] < max(count * policy['boards'], policy['stock_floor']):
            raise ValueError('selected full-board stock exhausted: ' + lcsc)
    if requirement_map.input_inventory(root) != before:
        raise ValueError('source closure drift during alternative selection')
    decision = dict(schema='agent-ee.acquisition-carrier-decision/1', selected=chosen,
        reason='retain eligible primary, otherwise exact TI carrier alternative',
        candidates=decisions, minimum_stock=threshold, evaluation_as_of=context.as_of,
        source_inventory=before, catalog=context.catalog, capture=info,
        carrier_page=context.carrier_page, ordering_text=context.ordering_text,
        parts=[dict(id=p['id'], instances=p['instances'], lcsc=chosen if p is adc else p['lcsc'])
               for p in document['parts']],
        qualification='source preparation only; supplier/physical and electrical/native review pending')
    if required_lcsc is not None:
        decision.update(required_lcsc=required_lcsc,
            reason='retain the selected source allocation only when its captured carrier identity and stock are eligible')
    target = root / PREFIX
    target.mkdir(parents=True)
    (target / 'decision.json').write_bytes(json_bytes(decision))
    decision_blob = _blob(root, PREFIX + '/decision.json')
    revised = _refresh_parts(root, copy.deepcopy(document), context, chosen, decision_blob)
    (root / 'parts.json').write_bytes(json_bytes(revised))
    circuit = strict_json(parts_manifest.read_captured(root, 'circuit.json'))
    instances = {(i['sheet'], i['reference']) for i in adc['instances']}
    matches = [c for c in circuit['components'] if ('/', c['reference']) in instances]
    if len(matches) != 1 or matches[0]['lib_id'] != adc['lib_id']:
        raise ValueError('circuit ADC allocation does not match authored CAD')
    matches[0]['value'] = ORDERABLES[chosen]
    (root / 'circuit.json').write_bytes(json_bytes(circuit))
    refs = {c['id']: {'part_ref': p['id']} for c in circuit['components'] for p in revised['parts']
            if any(i == {'sheet': '/', 'reference': c['reference']} for i in p['instances'])}
    if len(refs) != sum(len(p['instances']) for p in revised['parts']):
        raise ValueError('part-reference coverage differs from circuit components')
    (target / 'part-refs.json').write_bytes(json_bytes(dict(schema='agent-ee.part-refs/1', components=refs)))
    selections = strict_json(parts_manifest.read_captured(root, 'component-selections.json'))
    entries = [p for p in selections if p['reference'] == adc['instances'][0]['reference']]
    if len(entries) != 1 or entries[0]['lcsc'] != PRIMARY:
        raise ValueError('source component-selection allocation differs')
    entries[0].update(lcsc=chosen, value=ORDERABLES[chosen])
    (root / 'component-selections.json').write_bytes(json_bytes(selections))
    check['params'].update(catalog=context.catalog['path'], catalog_sha256=context.catalog['sha256'])
    (root / 'requirement-map.json').write_bytes(json_bytes(mapping))
    after = requirement_map.input_inventory(root)
    changed = {n: dict(before=h, after=after[n]) for n, h in before.items() if after[n] != h}
    for name in changed:
        # All previous bytes already have their hash in the selected decision;
        # caller's original recipe remains the custody source for archived bytes.
        if name not in ('parts.json', 'circuit.json', 'component-selections.json', 'requirement-map.json'):
            raise ValueError('unexpected changed source: ' + name)
    report = dict(schema='agent-ee.acquisition-carrier-revision/1', selected=chosen,
        decision=decision_blob, changed=changed, added={n: h for n, h in after.items() if n not in before},
        catalog=context.catalog, sources=[context.carrier_page, context.ordering_text],
        original_inventory_sha256=identity(before), resulting_inventory=after,
        remaining=['Rebind typed overlay base map hash and capture source inputs before seal',
                   'Fresh parts compile/binding-fill/baseline/native/build and electrical/package review',
                   'Run-bound stock freshness and supplier/physical validation'])
    audit_selection(root, report)
    return report


def audit_selection(root, report):
    """Reread selected capture and record pins; changed stock cannot reuse success."""
    for blob in [report['catalog'], report['decision'], *report['sources']]:
        _read(root, blob)
    document = parts_manifest.load_manifest(root, 'parts.json')
    parts_manifest.qualify_manifest(document, root)
    selected = [p for p in document['parts'] if p['lcsc'] == report['selected']]
    if len(selected) != 1:
        raise ValueError('selected ADC purchase identity changed after selection')
    if requirement_map.input_inventory(root) != report['resulting_inventory']:
        raise ValueError('source closure changed after alternative selection')
