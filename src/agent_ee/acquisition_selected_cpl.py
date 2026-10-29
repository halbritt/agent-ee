"""Selected acquisition purchasing inputs before the existing typed overlay seal."""
from pathlib import Path
from hashlib import sha256
import shutil

from . import cpl_rotation, easyeda_footprint, land_pattern, parts_manifest, requirement_map
from .catalog import exact_fields, strict_json
from .storage import digest, json_bytes


def supplier_files(context, captures):
    capture = context.get('supplier_capture')
    if capture is None:
        return {}
    exact_fields(capture, ('provenance', 'raw', 'http_response', 'receipt'))
    provenance = strict_json(captures[capture['provenance']['path']])
    land_pattern.validate_provenance(provenance, 'C468683')
    raw = captures[capture['raw']['path']]
    if provenance['path'] != 'cpl/supplier/C468683.raweasy.json':
        raise ValueError('alternative supplier raw path must bind its exact C468683 CPL input')
    supplier = cpl_rotation.load_suppliers(lambda path: raw if path == provenance['path'] else
        captures[capture['provenance']['path']], ['cpl/supplier/C468683.provenance.json'])
    parsed = easyeda_footprint.parse_package(supplier['C468683']['raw'], 'C468683', 200)
    if parsed['identity']['manufacturer_part'] != 'ADS1115IDGST':
        raise ValueError('alternative supplier capture names an incompatible manufacturer part')
    receipt = strict_json(captures[capture['receipt']['path']])
    body = captures[capture['http_response']['path']]
    start, end = receipt['result_span_bytes']
    if (receipt['outcome'] != 'captured_and_parsed' or body[start:end] != raw or
            sha256(body).hexdigest() != receipt['requests'][0]['sha256'] or
            receipt['requests'][0]['url'] != provenance['source_url'] or
            receipt['requests'][0]['ended_at'] != provenance['retrieved_at']):
        raise ValueError('alternative supplier capture custody differs from retained HTTP span')
    return {'supplier/C468683.provenance.json': captures[capture['provenance']['path']],
            'supplier/C468683.raweasy.json': raw}


def projected_inventory(original, context, captures):
    projected = dict(original)
    for path, raw in supplier_files(context, captures).items():
        if path in projected:
            raise ValueError('alternative supplier source would overwrite admitted CPL input')
        projected[path] = sha256(raw).hexdigest()
    return projected


def materialize(original, destination, context, captures, expected, *, power_repair_captures=None):
    shutil.copytree(original, destination)
    files = supplier_files(context, captures)
    if power_repair_captures is not None:
        from . import acquisition_power_repair
        files.update(acquisition_power_repair.supplier_files(power_repair_captures))
    for name, raw in files.items():
        path = Path(destination) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if requirement_map.input_inventory(destination) != expected:
        raise ValueError('derived CPL input closure differs from its pre-seal identity')


def finalize(revised, overlay, cpl_root):
    """Select exact supplier provenance and update the allocation roster, not approval."""
    revised = Path(revised)
    document = parts_manifest.load_manifest(revised, 'parts.json')
    parts_manifest.qualify_manifest(document, revised)
    parts = document['parts']
    selected = {p['lcsc'] for p in parts}
    checks = [c for c in overlay['check_additions']['declare'] if c['kind'] == 'cpl_rotation']
    if len(checks) != 1:
        raise ValueError('selected acquisition needs exactly one CPL rotation check')
    check = checks[0]
    paths = list(check['params']['supplier_footprints'])
    for selected_id in ('C468683', 'C137695', 'C114669'):
        source = 'cpl/supplier/' + selected_id + '.provenance.json'
        if (Path(cpl_root) / source.removeprefix('cpl/')).is_file() and source not in paths:
            paths.append(source)
    def read(path):
        if not path.startswith('cpl/'):
            raise ValueError('CPL supplier source is outside the admitted CPL input')
        return parts_manifest.read_captured(cpl_root, path.removeprefix('cpl/'))
    suppliers = cpl_rotation.load_suppliers(read, paths)
    missing = selected - suppliers.keys()
    if missing:
        raise ValueError('selected parts lack exact supplier footprint coverage: ' + repr(sorted(missing)))
    for lcsc in sorted(selected):
        easyeda_footprint.parse_package(suppliers[lcsc]['raw'], lcsc, check['params']['source_tolerance_nm'])
    check['params']['supplier_footprints'] = [suppliers[k]['provenance'] for k in sorted(selected)]
    cpl_rotation.validate_params(check['params'])
    roster_path = revised / 'assembly-release-ids.json'
    before = roster_path.read_bytes()
    old = strict_json(before)
    expected = (selected - {'C468683'}) | {'C37593'} if 'C468683' in selected else selected
    if selected & {'C137695', 'C114669'}:
        expected = expected - {'C137695', 'C114669'}
    if not isinstance(old, list) or len(old) != len(set(old)) or set(old) != expected:
        raise ValueError('historical assembly allocation roster differs from admitted original selections')
    roster_path.write_bytes(json_bytes(sorted(selected)))
    question = overlay['judgments']['judgments/acq-mech-4-assembly-applicability.json']
    question['questions'] = [q.replace('assembly-release ids',
        'the operative assembly-release-ids.json selected-allocation roster (not supplier approval)') for q in question['questions']]
    question['sources'] = [s for s in question['sources'] if s not in ('assembly-release-ids.json', 'stock-assembly-release')]
    question['sources'] += ['assembly-release-ids.json SHA256 ' + digest(roster_path),
                            'parts.json and preparation/alternative/decision.json (operative selected purchasing identities)']
    from .acquisition_alternative import _assembly_check
    mapping = strict_json((revised / 'requirement-map.json').read_bytes())
    question['sources'].append(_assembly_check(mapping)['params']['catalog'] + ' (operative stock capture; freshness remains run-bound)')
    if selected & {'C137695', 'C114669'}:
        question['sources'].append('preparation/power-repair/decision.json (explicit R6 exception, not supplier approval)')
    if roster_path.read_bytes() != before:
        question['sources'].append('history/acquisition-preparation-original/assembly-release-ids.json (historical choices only)')
    report = dict(roster=dict(path='assembly-release-ids.json', before_sha256=sha256(before).hexdigest(),
        sha256=digest(roster_path), added=sorted(selected-set(old)), removed=sorted(set(old)-selected),
        meaning='Selected purchasing allocation roster only; supplier approval and physical validation pending'),
        supplier_footprints={lcsc:{k:suppliers[lcsc][k] for k in ('provenance','path','sha256')} for lcsc in sorted(selected)},
        policy='Existing CPL policy, tolerances and native pad/pin rotation rules unchanged')
    (revised / 'preparation/selected-part-input-closure.json').write_bytes(json_bytes(report))
    return report
