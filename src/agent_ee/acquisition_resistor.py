"""Finite captured R1/R2 alternative and public eligibility policy; no fetches.

Source qualification is replayed by the existing role reader. Selection never
changes a raw stock observation, ratings, native receipts or acceptance status.
"""
import copy
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath

from . import (acquisition_alternative, assembly_readiness,
               easyeda_footprint, jlc_stock_capture, land_pattern, part_record,
               parts_manifest, requirement_map, storage)
from .catalog import exact_fields, strict_json
from .storage import json_bytes

SCHEMA = 'agent-ee.acquisition-resistor-context/1'
AT = 'preparation/resistor-context'
PREFIX = 'preparation/resistor'
PRIMARY = 'C22548'
ALTERNATIVE = 'C118155'
MPNS = {PRIMARY: 'RC0603FR-071KL', ALTERNATIVE: 'ERJ3EKF1001V'}
LABEL = 'evaluation scenario; not an observed supplier stockout'
ANALOG = 'acq-ads1115-two-channel'
PDF = 'datasheets/b29db0c4cad7699a57e1e17182f49e77b78470bd85f595dc22f75144e7b780c5.pdf'
EVIDENCE = {
    'catalog/catalog.json': '3df4ccecf88b85dcbafc5219df4af7f0e1d7994fc5ee50d5db01ef81b555eae5',
    'catalog/responses/C118155.json': 'f8102ae3bcb9b306daa088b13acc88d7023af9dc86dbd4f21e001e42a9a8afb1',
    'named/C118155.json': '93bc923fc12f7c13d3f08b9300c8285c6d446610257ac5323abfb92668c1b6e0',
    'datasheets/manifest.json': 'd312593a5aadd9dbb8efa2f86226c4be60590abc0cf8553be25076e66ddf1a7d',
    PDF: '78825b819853a63f57cc18214f321d2f1da9dc205a7e58af7db18ae73563e378',
    'supplier/C118155.raweasy.json': '9267b75fffdc5634caa2eb723f0a89c2d622eae920b581283957011deece27f3',
    'supplier/C118155.provenance.json': 'ef3f234ab1a9679162a37092dc17e9eedba28e2bf8183b2b53b4a4899ab25f87',
    'request-05.body': '784c02d417c8fa86adac7d5f045aab0e6a79fc58bd2cdee8acf9758abac59a4f',
    'requests.json': '964f24857d4f535caf87ae7d3d424a184330af4dc7163136855eecf38f6f7ad7',
    'request-11.body': 'dce85b4894ccf97307f6a6b1b34176ff3ca4ee02850f1271b866f87127521214',
}


# Exact derivatives of the existing two admitted R6 geometry revisions.
MODELS = {'fa010715d48ba3fe6e1843e17744834583b98f03a23fb61e004909a7840507b2': '470',
          '2bb1d93ff3f1cc0420088c02b16278394e3124230b05da950d6838432f049479': '620'}


def geometry_sources(model):
    from . import acquisition_power_repair, acquisition_source_supplement
    if model not in MODELS:
        raise ValueError('unsupported Panasonic body-model revision')
    return {**acquisition_source_supplement.geometry_sources(),
            **acquisition_power_repair.geometry_sources(MODELS[model]),
            AT + '/' + PDF: EVIDENCE[PDF], AT + '/request-11.body': EVIDENCE['request-11.body']}


def policy(raw, catalogs):
    """Validate the public protocol against the common, unchanged catalog union."""
    document = strict_json(raw)
    exact_fields(document, ('schema', 'catalog_policy'))
    if document['schema'] != 'agent-ee.case-context/1':
        raise ValueError('unsupported public case context')
    value = document['catalog_policy']
    if value is None:
        return None
    exact_fields(value, ('kind', 'label', 'exclude'))
    if value['kind'] != 'evaluation_exclusion' or value['label'] != LABEL:
        raise ValueError('public catalog policy kind or label differs')
    if not isinstance(value['exclude'], list) or not value['exclude']:
        raise ValueError('public exclusion must be nonempty')
    identities = {}
    for catalog in catalogs:
        for lcsc, row in catalog['parts'].items():
            if lcsc in identities and identities[lcsc] != row['mpn']:
                raise ValueError('common catalog has contradictory part identities')
            identities[lcsc] = row['mpn']
    seen = set()
    for entry in value['exclude']:
        exact_fields(entry, ('lcsc', 'mpn'))
        if not all(isinstance(entry[k], str) and entry[k] for k in ('lcsc', 'mpn')):
            raise ValueError('public exclusion identities must be nonempty strings')
        key = entry['lcsc'], entry['mpn']
        if key in seen:
            raise ValueError('duplicate public exclusion')
        seen.add(key)
        if identities.get(entry['lcsc']) != entry['mpn']:
            raise ValueError('public exclusion identity absent from common catalog')
    return copy.deepcopy(value)


def excluded(value, lcsc, mpn):
    return value is not None and dict(lcsc=lcsc, mpn=mpn) in value['exclude']


def choose(value):
    for lcsc in (PRIMARY, ALTERNATIVE):
        if not excluded(value, lcsc, MPNS[lcsc]):
            return lcsc
    raise ValueError('no qualified filter resistor candidate remains after public exclusion')


def enforce_selected(value, document):
    for part in document['parts']:
        if excluded(value, part['lcsc'], part['mpn']):
            raise ValueError('no admitted qualified alternative for excluded selected part: ' + part['id'])


def evidence(captures):
    if any(sha256(captures[p]).hexdigest() != h for p, h in EVIDENCE.items()):
        raise ValueError('resistor evidence differs from exact captured bytes')


def supplier_files(captures):
    """Derive only a CPL-relative path; preserve exact HTTP/result-span custody."""
    evidence(captures)
    raw = captures['supplier/C118155.raweasy.json']
    provenance = strict_json(captures['supplier/C118155.provenance.json'])
    land_pattern.validate_provenance(provenance, ALTERNATIVE)
    body = captures['request-05.body']
    requests = strict_json(captures['requests.json'])
    source = [r for r in requests if r['index'] == 5]
    if (len(source) != 1 or source[0]['method'] != 'GET' or source[0]['status'] != 'received'
            or source[0]['http_status'] != 200 or source[0]['sha256'] != sha256(body).hexdigest()
            or source[0]['url'] != provenance['source_url']
            or source[0]['finished_at'] != provenance['retrieved_at']
            or source[0]['bytes'] != len(body) or body[34:21264] != raw
            or provenance['path'] != 'supplier/C118155.raweasy.json'):
        raise ValueError('Panasonic supplier HTTP/result-span custody differs')
    parsed = easyeda_footprint.parse_package(raw, ALTERNATIVE, 200)
    if parsed['identity']['manufacturer_part'] != MPNS[ALTERNATIVE]:
        raise ValueError('Panasonic supplier manufacturer identity differs')
    derived = {**provenance, 'path': 'cpl/supplier/C118155.raweasy.json'}
    return {'supplier/C118155.provenance.json': json_bytes(derived),
            'supplier/C118155.raweasy.json': raw}


def _relative(path):
    if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
            or any(p in ('', '.', '..') for p in path.split('/'))):
        raise ValueError('resistor context requires canonical relative paths')
    return path


def read(root, raw, capacitance):
    """Admit both common qualified cohorts, not caller assertions of qualification."""
    from . import acquisition_roles
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError('resistor source root contains an alias')
    doc = strict_json(raw)
    exact_fields(doc, ('schema', 'reference', 'files', 'role_contexts'))
    if doc['schema'] != SCHEMA or doc['reference'] != 'acquisition-control':
        raise ValueError('unsupported acquisition resistor context')
    if not isinstance(doc['files'], dict) or not 1 <= len(doc['files']) <= 8192:
        raise ValueError('resistor context requires a finite captured closure')
    if any(doc['files'].get(p) != h for p, h in EVIDENCE.items()):
        raise ValueError('resistor context differs from exact captured Panasonic evidence')
    captures = {p: parts_manifest._read_blob(root, dict(path=p, sha256=h)) for p, h in doc['files'].items()}
    exact_fields(doc['role_contexts'], (PRIMARY, ALTERNATIVE))
    needed = set(EVIDENCE)
    choices = {}
    for entry in doc['role_contexts'].values():
        exact_fields(entry, ('root', 'context'))
    roots = [PurePosixPath(_relative(e['root'])) for e in doc['role_contexts'].values()]
    if roots[0] == roots[1] or roots[0] in roots[1].parents or roots[1] in roots[0].parents:
        raise ValueError('common cohort roots must be prefix-isolated')
    for lcsc, entry in doc['role_contexts'].items():
        at = _relative(entry['root'])
        parts_manifest._blob(entry['context'])
        context_path = entry['context']['path']
        if doc['files'].get(context_path) != entry['context']['sha256']:
            raise ValueError('common role context is outside the pinned closure')
        role_raw = captures[context_path]
        role_doc, role_captures, reports = acquisition_roles.read(root / at, role_raw, capacitance)
        needed.add(context_path)
        for p, h in role_doc['files'].items():
            joined = at + '/' + p
            if doc['files'].get(joined) != h or captures.get(joined) != role_captures[p]:
                raise ValueError('common qualified role source closure differs')
            needed.add(joined)
        reconcile(root / at, role_doc, lcsc)
        choices[lcsc] = (role_doc, role_captures, reports)
    for rid in ('acq-tps7a2033-power', 'acq-jst-ph-i2c'):
        if choices[PRIMARY][0]['roles'][rid]['block_sha256'] != choices[ALTERNATIVE][0]['roles'][rid]['block_sha256']:
            raise ValueError('resistor cohort must reuse exact qualified v5 power/host sources')
    if needed != set(captures):
        raise ValueError('resistor context has missing or unrelated files')
    supplier_files(captures)
    part_record.qualify_record(strict_json(captures['named/C118155.json']), root)
    return doc, captures, choices


def reconcile(root, doc, selected, operative=None):
    """Match literal selected purchasing allocations to an actually admitted cohort."""
    expected = {}
    for rid, entry in doc['roles'].items():
        block = strict_json(parts_manifest.read_captured(root, entry['block'] + '/block.json'))
        expected_version = '6' if selected == ALTERNATIVE and rid == ANALOG else '5'
        if block['id'] != rid or block['version'] != expected_version:
            raise ValueError('resistor context requires base5/5/5 or alternate5/6/5 cohort')
        manifest = parts_manifest.load_manifest(Path(root) / entry['block'], block['parts_manifest'])
        for part in manifest['parts']:
            if part['id'] in expected:
                raise ValueError('qualified cohort purchasing allocation overlaps')
            expected[part['id']] = part
    for ref in ('R1', 'R2'):
        part = expected[ref]
        if (selected == ALTERNATIVE and part['datasheet']['artifact']['sha256'] != EVIDENCE[PDF]):
            raise ValueError('qualified Panasonic manufacturer source differs: ' + ref)
        if (part['lcsc'] != selected or part['mpn'] != MPNS[selected]
                or part['manufacturer'] != ('PANASONIC' if selected == ALTERNATIVE else 'YAGEO')
                or part['role'] != 'analog/' + ref or part['package'] != '0603'
                or part['lib_id'] != 'Device:R' or part['footprint'] != 'Resistor_SMD:R_0603_1608Metric'
                or part['instances'] != [dict(sheet='/', reference=ref)]):
            raise ValueError('qualified resistor source allocation differs: ' + ref)
    if selected == ALTERNATIVE:
        analog_entry = doc['roles'][ANALOG]
        model = strict_json(parts_manifest.read_captured(Path(root) / analog_entry['block'], 'analysis/geometry-model.json'))
        for ref in ('R1', 'R2'):
            if model['packages'][ref] != panasonic_body():
                raise ValueError('qualified analog6 Panasonic body attribution differs')
    if operative is not None:
        from .block_adaptation import _part_allocations
        if _part_allocations(dict(parts=list(expected.values())), {}) != _part_allocations(operative, {}):
            raise ValueError('operative full selected union differs from qualified cohort')
    entry = doc['roles'][ANALOG]
    block = strict_json(parts_manifest.read_captured(root, entry['block'] + '/block.json'))
    return dict(id=block['id'] + '@' + block['version'] + ':' + entry['block_sha256'],
                block_sha256=entry['block_sha256'], report=entry['report'])


def panasonic_body():
    return dict(size_mm=[1.75, .95, .55], offset_mm=[0, 0],
                basis='PANASONIC ERJ3EK maximum;0.1W;1percent; manufacturer May2025 p4')


def model_variant(raw):
    model = strict_json(raw)
    for ref in ('R1', 'R2'):
        row = model['packages'][ref]
        if row != dict(basis='YAGEO RC0603 maximum;0.1W;1percent', offset_mm=[0, 0], size_mm=[1.7, .9, .55]):
            raise ValueError('original filter resistor body model differs')
        row.update(panasonic_body())
    return json_bytes(model)


def _write(root, name, raw):
    path = Path(root) / name
    if path.exists():
        raise ValueError('resistor preparation would overwrite captured source: ' + name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return dict(path=name, sha256=storage.digest(path))


def _union(root, captures, old_blob):
    old = strict_json(parts_manifest._read_blob(root, old_blob))
    new = strict_json(captures['catalog/catalog.json'])
    if old['source'] != new['source'] or set(old['parts']) & set(new['parts']):
        raise ValueError('resistor catalog source mismatch or overlapping observations')
    old_parent = str(PurePosixPath(old_blob['path']).parent)
    for document, reader in ((old, lambda p: parts_manifest.read_captured(root, old_parent + '/' + p)),
                             (new, lambda p: captures['catalog/' + p])):
        jlc_stock_capture.validate_capture(document, reader)
        for request in document['requests']:
            _write(root, PREFIX + '/catalog/' + request['response']['path'], reader(request['response']['path']))
    union = dict(schema=jlc_stock_capture.SCHEMA,
        as_of=min((old['as_of'], new['as_of']), key=lambda t: jlc_stock_capture._utc(t, 'as_of')),
        completed_at=max((old['completed_at'], new['completed_at']), key=lambda t: jlc_stock_capture._utc(t, 'completed_at')),
        stale_before=None, source=old['source'], requests=old['requests'] + new['requests'],
        parts={**old['parts'], **new['parts']})
    blob = _write(root, PREFIX + '/catalog/catalog.json', json_bytes(union))
    _write(root, PREFIX + '/catalog/derivation.json', json_bytes(dict(
        kind='Offline conservative union; NOT a fresh capture', parents=[old_blob,
            dict(path=AT + '/catalog/catalog.json', sha256=EVIDENCE['catalog/catalog.json'])])))
    return blob, union


def apply(root, captures, value, *, role_source, role_context, capacitance, as_of, policy_blob):
    """Public admission replays real source qualification before any revision."""
    from . import acquisition_roles
    doc, _, _ = acquisition_roles.read(role_source, role_context, capacitance)
    qualification = reconcile(role_source, doc, ALTERNATIVE)
    result = _revise(root, captures, value, qualification, as_of, policy_blob)
    reconcile(role_source, doc, ALTERNATIVE, parts_manifest.load_manifest(root, 'parts.json'))
    return result


def bind_judgments(overlay, selection):
    sources = [selection['decision']['path'] + ' SHA256 ' + selection['decision']['sha256'],
               AT + '/' + PDF + ' SHA256 ' + EVIDENCE[PDF],
               AT + '/request-11.body SHA256 ' + EVIDENCE['request-11.body']]
    for ident in ('acq-elec-2-source-evaluation', 'acq-elec-4-filter-scope',
                  'acq-mech-3-limit-rationale', 'acq-mech-3-return-paths'):
        brief = overlay['judgments']['judgments/' + ident + '.json']
        brief['sources'] += sources
        brief['questions'].append('Evaluate both selected R1/R2 PANASONIC ERJ3EKF1001V/C118155, '
            'unchanged1k/1percent filter/current/rating limits and1.2mm board, '
            'and manufacturer maximum1.75x0.95x0.55mm bodies. Original YAGEO references '
            'do not qualify these two selected parts; source qualification does not discharge '
            'fresh board/supplier/physical obligations. ' + LABEL)


def _revise(root, captures, value, qualification, as_of, policy_blob, *, source_derivation=False):
    """Apply only the admitted two-instance substitution to an owned unsealed root."""
    root = Path(root)
    if not source_derivation and choose(value) != ALTERNATIVE:
        raise ValueError('resistor revision requires excluded primary and admitted alternate')
    evidence(captures)
    original = requirement_map.input_inventory(root)
    document = parts_manifest.load_manifest(root, 'parts.json')
    parts_manifest.qualify_manifest(document, root)
    selected = {p['id']: p for p in document['parts'] if p['id'] in ('R1', 'R2')}
    if len(document['parts']) != 17 or set(selected) != {'R1', 'R2'}:
        raise ValueError('resistor revision requires exact original17 allocation')
    for ref, part in selected.items():
        if (part['lcsc'] != PRIMARY or part['mpn'] != MPNS[PRIMARY] or part['manufacturer'] != 'YAGEO'
                or part['lib_id'] != 'Device:R' or part['footprint'] != 'Resistor_SMD:R_0603_1608Metric'
                or part['role'] != 'analog/' + ref or part['instances'] != [dict(sheet='/', reference=ref)]):
            raise ValueError('original filter resistor allocation differs: ' + ref)
    mapping = strict_json((root / 'requirement-map.json').read_bytes())
    check = acquisition_alternative._assembly_check(mapping)
    catblob, union = _union(root, captures, dict(path=check['params']['catalog'], sha256=check['params']['catalog_sha256']))
    if not source_derivation and policy(parts_manifest._read_blob(root, policy_blob), [union]) != value:
        raise ValueError('operative public policy differs from its admitted bytes')
    stock_policy = assembly_readiness.validate_params({**check['params'], 'as_of': as_of})
    info = assembly_readiness._verify_jlc_capture(root, catblob['path'], union)
    needed = {ALTERNATIVE if p['id'] in selected else p['lcsc'] for p in document['parts']}
    if not source_derivation:
        assembly_readiness._capture_gate(info, stock_policy, needed - union['parts'].keys())
    counts = {}
    for part in document['parts']:
        lcsc = ALTERNATIVE if part['id'] in selected else part['lcsc']
        counts[lcsc] = counts.get(lcsc, 0) + len(part['instances'])
    for lcsc, count in counts.items():
        if lcsc not in union['parts'] or (not source_derivation and
                union['parts'][lcsc]['stock'] < max(count * stock_policy['boards'], stock_policy['stock_floor'])):
            raise ValueError('selected full-board stock unavailable: ' + lcsc)
    decision = _write(root, PREFIX + '/decision.json', json_bytes(dict(
        schema='agent-ee.acquisition-resistor-source-derivation/1' if source_derivation else 'agent-ee.acquisition-resistor-decision/1',
        source_derivation=source_derivation, policy=policy_blob, catalog_policy=value,
        qualification_record=qualification, catalog=catblob, evaluation_as_of=as_of,
        original_inventory=original, parts=[dict(id=p['id'], instances=p['instances'],
            lcsc=ALTERNATIVE if p['id'] in selected else p['lcsc']) for p in document['parts']],
        affected=[dict(instance=dict(sheet='/', reference=ref), excluded=dict(lcsc=PRIMARY, mpn=MPNS[PRIMARY]),
            selected=dict(lcsc=ALTERNATIVE, mpn=MPNS[ALTERNATIVE]), qualification_record_id=qualification['id'])
            for ref in ('R1', 'R2')] if not source_derivation else [],
        derived_instances=['R1', 'R2'] if source_derivation else [],
        qualification='Unqualified source derivation; dated stock documentary only' if source_derivation else
            'Source selection only; fresh adaptation/supplier/physical validation required')))
    for part in document['parts']:
        old = strict_json(parts_manifest.read_captured(root, part['selection']['part_record']))
        changing = part['id'] in selected
        datasheets = dict(path=AT + '/datasheets/manifest.json', sha256=EVIDENCE['datasheets/manifest.json']) if changing else old['datasheets']
        record = part_record.prepare_record(root, ALTERNATIVE if changing else part['lcsc'], catblob,
            datasheets, ALTERNATIVE if changing else old['datasheet_id'], source_selection=None if changing else old.get('source_selection'))
        if not changing and any(record[k] != part[k] for k in ('lcsc', 'mpn', 'manufacturer', 'package')):
            raise ValueError('unrelated resistor selection identity drift: ' + part['id'])
        named_path = PREFIX + '/named/' + record['lcsc'] + '.json'
        named_raw = json_bytes(record)
        if (root / named_path).exists():
            if (root / named_path).read_bytes() != named_raw:
                raise ValueError('contradictory shared resistor named record')
            named = dict(path=named_path, sha256=storage.digest(root / named_path))
        else:
            named = _write(root, named_path, named_raw)
        for key in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock'):
            part[key] = record[key]
        part['selection'] = dict(kind='named', lcsc=part['lcsc'], part_record=named['path'],
            part_record_sha256=named['sha256'], decision_ref={**decision, 'locator':'parts[id=' + part['id'] + ']'})
        part['datasheet'].update(artifact=record['artifact'], provenance={**record['datasheets'],
            'locator':'parts[id=' + record['datasheet_id'] + ']'}, status='captured')
    enforce_selected(value, document)
    parts_manifest.prepare_manifest(document, root)
    (root / 'parts.json').write_bytes(json_bytes(document))
    selections = strict_json((root / 'component-selections.json').read_bytes())
    for ref in selected:
        rows = [p for p in selections if p['reference'] == ref]
        if len(rows) != 1 or rows[0]['lcsc'] != PRIMARY:
            raise ValueError('original resistor display allocation differs')
        rows[0]['lcsc'] = ALTERNATIVE
    (root / 'component-selections.json').write_bytes(json_bytes(selections))
    check['params'].update(catalog=catblob['path'], catalog_sha256=catblob['sha256'])
    (root / 'requirement-map.json').write_bytes(json_bytes(mapping))
    model_path = root / 'analysis/geometry-model.json'
    _write(root, PREFIX + '/geometry-original.json', model_path.read_bytes())
    new_model = model_variant(model_path.read_bytes())
    if sha256(new_model).hexdigest() not in MODELS:
        raise ValueError('unsupported finite Panasonic geometry derivative')
    model_path.write_bytes(new_model)
    calculations = strict_json((root / 'engineering-calculations.json').read_bytes())
    calculations['sources']['analysis/geometry-model.json'] = storage.digest(model_path)
    calculations['sources'][AT + '/' + PDF] = EVIDENCE[PDF]
    (root / 'engineering-calculations.json').write_bytes(json_bytes(calculations))
    note = ('Source selection: R1/R2 are PANASONIC ERJ3EKF1001V/C118155, '
        '1kohm1percent0603, with manufacturer maximum1.75x0.95x0.55mm bodies. '
        'Retained YAGEO R1/R2 identities and1.70x0.90x0.55mm bodies describe only the historical reference. '
        'All electrical/board constraints remain; current sources are preparation/resistor/decision.json '
        'and preparation/resistor-context/datasheets/manifest.json. Fresh board/supplier/physical validation remains required. '
        + ('Unqualified source derivation; dated stock documentary only' if source_derivation else LABEL) + '.\n')
    for name in ('OPERATING.md', 'ENGINEERING-REVIEW.md'):
        path = root / name
        if source_derivation and name == 'ENGINEERING-REVIEW.md' and not path.exists() and not path.is_symlink():
            continue
        path.write_bytes(note.encode() + b'\n' + path.read_bytes())
    return dict(selected=ALTERNATIVE, decision=decision, catalog=catblob, qualification_record=qualification,
                model=dict(path='analysis/geometry-model.json', sha256=storage.digest(model_path)),
                policy=policy_blob, label=LABEL if not source_derivation else 'Unqualified source derivation')


def derive_source(source, out, *, source_inventory_sha256, capture_source):
    """Emit an unqualified analog6 source seed without requiring its future proof.

    Exact seed/capture pins are required. This entry never admits a target,
    qualification report, native export or stock freshness. Native qualification
    and any regenerated PCB remain root-owned later steps.
    """
    from . import parts_stage, reference_blocks, schematic_emitter, typed_overlay
    parts_manifest._sha(source_inventory_sha256)
    source, capture_source, out = Path(source).absolute(), Path(capture_source).absolute(), Path(out).absolute()
    if source.resolve(strict=True) != source or capture_source.resolve(strict=True) != capture_source:
        raise ValueError('source derivation roots contain aliases')
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError('source derivation needs a new output and existing parent')
    typed_overlay._disjoint('source output', out.resolve(), {'seed': source, 'capture': capture_source})
    before = requirement_map.input_inventory(source)
    if storage.identity(before) != source_inventory_sha256:
        raise ValueError('source-generation seed inventory differs from its exact pin')
    captures = {p: parts_manifest._read_blob(capture_source, dict(path=p, sha256=h)) for p,h in EVIDENCE.items()}
    evidence(captures)
    with tempfile.TemporaryDirectory(prefix=out.name+'.derive-', dir=out.parent) as temporary:
        revised = Path(temporary)/'source';revised.mkdir()
        for p in before:
            target=revised/p;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(parts_manifest._read_blob(source,dict(path=p,sha256=before[p])))
        for p,raw in captures.items():
            target=revised/AT/p
            if target.exists():
                if target.read_bytes()!=raw:
                    raise ValueError('source-generation capture namespace collision')
            else:
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        anchor = strict_json(captures['catalog/catalog.json'])['completed_at']
        revision = _revise(revised,captures,None,None,anchor,None,source_derivation=True)
        if strict_json(parts_manifest.read_captured(revised,'ratings.json')) != dict(
                schema='agent-ee.passive-rating-sources/1',catalog=True,extractor=None,facts=None):
            raise ValueError('source generation requires the captured pure catalog rating source')
        if strict_json(parts_manifest.read_captured(revised,'envelope.json'))['stackup']['thickness_mm'] != 1.2:
            raise ValueError('source generation requires unchanged1.2mm board')
        production,intent = parts_stage.compile_parts(revised,'parts.json','circuit.json','layout.json',ratings='ratings.json')
        libraries = {lib['path']:parts_manifest._read_blob(revised,dict(path=lib['path'],sha256=lib['sha256']))
                     for lib in intent['libraries']}
        layout=parts_manifest.read_captured(revised,'layout.json')
        if strict_json(layout)['project']!='acquisition':
            raise ValueError('finite source generation requires original acquisition project identity')
        emission,outputs = schematic_emitter.emit_schematic(intent,libraries,layout)
        if emission['execution']!='ok' or emission['outcome']!='info' or outputs is None:
            raise ValueError('source schematic emitter refused: '+str(emission['findings']))
        sheet='project/acquisition.kicad_sch'
        old_symbols=reference_blocks.sheet_symbols(parts_manifest.read_captured(revised,sheet),'/')
        new_symbols=reference_blocks.sheet_symbols(outputs['acquisition.kicad_sch'],'/')
        if old_symbols!=new_symbols or len(new_symbols)!=17:
            raise ValueError('source generation changed physical17 UUID/CAD allocation')
        (revised/sheet).write_bytes(outputs['acquisition.kicad_sch'])
        _write(revised,PREFIX+'/source-intent.json',json_bytes(intent))
        _write(revised,PREFIX+'/source-parts-production.json',json_bytes(production))
        _write(revised,PREFIX+'/source-emission.json',json_bytes(emission))
        report=dict(schema='agent-ee.acquisition-resistor-source-generation/1',qualification='unqualified',
            seed_inventory_sha256=source_inventory_sha256,capture_files=EVIDENCE,revision=revision,
            schematic=dict(path=sheet,sha256=storage.digest(revised/sheet)),
            retained_native_seed=['project/acquisition.kicad_pro','project/acquisition.kicad_pcb'],
            native_invocations=0,remaining=['Root-owned native CAD/PCB generation and source qualification',
                'Refresh analog6 own interface/interval/application/composition/file citations',
                'Freeze and verify both common source cohorts; no target admission from this entry'])
        _write(revised,PREFIX+'/source-generation.json',json_bytes(report))
        if requirement_map.input_inventory(source)!=before:
            raise ValueError('source-generation seed changed during derivation')
        for p,raw in captures.items():
            if parts_manifest._read_blob(capture_source,dict(path=p,sha256=EVIDENCE[p]))!=raw:
                raise ValueError('source-generation capture changed before publication')
        storage.rename_noreplace(revised,out)
    return report
