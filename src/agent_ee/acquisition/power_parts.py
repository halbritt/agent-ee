"""Bind the generated ADP7104 reference to captured production parts and CAD.

This source producer returns new bytes. The ordinary parts compiler remains
the consumer and authority for source/CAD binding. No old power-role checks,
reviews, models or acceptance records are copied into this source bundle.
"""
import copy
from hashlib import sha256
from pathlib import Path

from . import reverse_blocking as variant
from .. import part_record, parts_manifest
from ..catalog import exact_fields, strict_json
from ..footprint_library import _members
from ..storage import identity, json_bytes

SCHEMA = 'agent-ee.acquisition-power-parts-sources/1'
AT = 'power-reference'
DATASHEETS = {
    'U1': '958844711dc322d705b9a22c04d50633fb86d62184e638b591d251cd3160ddef',  # ADI Rev. I, supplier copy
    'D1': 'af204fbee5194f301b15b9b6c1a4751c8484cc6ed93c3cd11aac964c757f7579',  # DS30051 Rev.18-2
}
# Exact retained public-control purchasing inputs; these pins grant no acceptance.
BASE = {
    'parts.json': 'c35edc3a63f0e9bfd99bce6cf86b92d47cdf1e8d8f482590bf61eaaa320e657a',
    'layout.json': '27e18ee65fe8a037a1faff625eb7bf1ba48e68e1eb3f0c051e9abb6669991d3c',
    'spec.md': 'fbf659b7909af7af4c370ce309c6138c661029261a9a4906237f86be45444373',
    'electrical-locality.json': '8157776baeb375a92b11296c30283ac0dd92c1f9359b8b95c79ef573a8dc4b1f',
}


def _read(root, path, pin):
    return parts_manifest._read_blob(root, dict(path=path, sha256=pin))


def _sources(root, raw):
    """Replay the two new named records and the complete declared source closure."""
    doc = strict_json(raw)
    exact_fields(doc, ('schema', 'reference', 'parts', 'supplier', 'files'))
    if (doc['schema'] != SCHEMA or doc['reference'] != variant.REFERENCE
            or not isinstance(doc['parts'], dict) or set(doc['parts']) != {'U1', 'D1'}
            or not isinstance(doc['supplier'], dict) or set(doc['supplier']) != {'U1', 'D1'}
            or not isinstance(doc['files'], dict) or not 1 <= len(doc['files']) <= 128):
        raise ValueError('power sources require exactly U1/D1 and a finite captured closure')
    files = {}
    for path, pin in doc['files'].items():
        if not path.startswith(AT + '/'):
            raise ValueError('power source must use its owned namespace')
        files[path] = _read(root, path, pin)
    needed, records = {}, {}
    for ref in ('U1', 'D1'):
        for blob in (doc['parts'][ref], doc['supplier'][ref]):
            parts_manifest._blob(blob)
            if doc['files'].get(blob['path']) != blob['sha256']:
                raise ValueError('power source reference differs from its closure')
            needed[blob['path']] = blob['sha256']
        record = strict_json(files[doc['parts'][ref]['path']])
        needed.update(part_record.qualify_record(record, root))
        selected = variant.SELECTIONS[ref]
        if any(record[k] != selected[k] for k in ('mpn', 'lcsc', 'manufacturer')):
            raise ValueError(ref + ': selected power identity differs from generated recipe')
        if record['artifact']['sha256'] != DATASHEETS[ref]:
            raise ValueError(ref + ': captured datasheet differs from the selected source revision')
        records[ref] = record
    if needed != doc['files']:
        raise ValueError('power source closure contains missing or unrelated files')
    return doc, files, records


def inputs(base, selected, context):
    """Return source files and provenance for the normal production parts stage.

    ``base`` holds the exact retained public-control source. ``selected`` holds
    only the two new named-part closures and pinned supplier CAD, identified by
    ``context`` bytes. The original intent is the committed finite recipe.
    Neither input directory is modified; no native tool or network is invoked.
    """
    base, selected = Path(base), Path(selected)
    before = {path: _read(base, path, pin) for path, pin in BASE.items()}
    doc, additions, records = _sources(selected, context)
    manifest = parts_manifest.validate_manifest(strict_json(before['parts.json']))
    citation = manifest['declaration_source']
    if citation['path'] != 'requirements-map/spec.md' or citation['sha256'] != BASE['spec.md']:
        raise ValueError('retained parts declaration differs from operative specification')
    # The requirements compiler has not yet created its namespace. This is the
    # same exact specification, not a substitute declaration or approval.
    citation['path'] = 'spec.md'
    dependencies = parts_manifest.dependency_closure(manifest, base)
    files = {path: _read(base, path, pin) for path, pin in dependencies.items()}
    files.update(before)
    for path, raw in additions.items():
        if path in files:
            raise ValueError('new power sources overlap retained source')
        files[path] = raw
    generated, generation = variant.inputs((variant.DATA / 'base-circuit.json').read_bytes(),
        additions[doc['supplier']['U1']['path']], additions[doc['supplier']['D1']['path']])
    files.update(generated)
    circuit = strict_json(files['circuit.json'])
    parts = {p['id']: p for p in manifest['parts']}
    if (len(parts) != 17 or parts['U2']['mpn'] != 'ADS1115IDGST'
            or parts['U2']['lcsc'] != 'C468683'):
        raise ValueError('retained 17-part roster or qualified ADC carrier differs')
    # R1 and R3 already select the exact same 1k and 2.2k purchasing records.
    # Reuse their source evidence; allocate separate physical R5/R6 instances.
    for ref, donor in (('R5', 'R1'), ('R6', 'R3')):
        old = parts[ref]
        replacement = copy.deepcopy(parts[donor])
        for key in ('id', 'role', 'instances', 'selection_id', 'required'):
            replacement[key] = old[key]
        if any(replacement[k] != variant.SELECTIONS[ref][k]
               for k in ('mpn', 'lcsc', 'manufacturer', 'lib_id', 'footprint')):
            raise ValueError(ref + ': retained resistor selection differs from recipe')
        parts[ref] = replacement
    for ref in ('U1', 'D1'):
        part, record = parts[ref], records[ref]
        for key in ('mpn', 'lcsc', 'manufacturer', 'package', 'stock'):
            part[key] = record[key]
        for key in ('lib_id', 'footprint'):
            part[key] = variant.SELECTIONS[ref][key]
        part['datasheet'] = dict(required=True, status='captured', artifact=record['artifact'],
            provenance={**record['datasheets'], 'locator': 'parts[id=' + record['datasheet_id'] + ']'})
        if 'source_selection' in record:
            part['datasheet']['source_selection'] = record['source_selection']
        part['selection'] = dict(kind='named', lcsc=record['lcsc'],
            part_record=doc['parts'][ref]['path'], part_record_sha256=doc['parts'][ref]['sha256'])
    manifest['parts'] = [parts[p['id']] for p in manifest['parts']]
    decisions = dict(schema='agent-ee.acquisition-power-parts/1', reference=variant.REFERENCE,
        original_parts_sha256=BASE['parts.json'], source_context_sha256=sha256(context).hexdigest(),
        parts=[dict(id=p['id'], mpn=p['mpn'], lcsc=p['lcsc'],
            basis='Generated power replacement' if p['id'] in variant.SELECTIONS else 'Retained selected part')
            for p in manifest['parts']], accepted=False)
    decision_path = AT + '/decisions.json'
    files[decision_path] = json_bytes(decisions)
    for part in manifest['parts']:
        part['selection']['decision_ref'] = dict(path=decision_path,
            sha256=sha256(files[decision_path]).hexdigest(), locator='parts[id=' + part['id'] + ']')
    # Retain the selected reel carrier instead of restoring the original IDGSR
    # display from the base circuit. Connectivity and all other values are kept.
    next(c for c in circuit['components'] if c['reference'] == 'U2')['value'] = parts['U2']['mpn']
    files['circuit.json'] = json_bytes(circuit)
    assignments = strict_json(files[manifest['cad']['assignments']['path']])
    assignments['libraries'] = circuit['libraries']
    for row in assignments['components']:
        part = parts[row['reference']]
        for key in ('lib_id', 'footprint'):
            row[key] = part[key]
    # Select exactly the active footprint members; no host-library lookup.
    old_members = {lib + ':' + Path(blob['path']).stem: blob
        for lib, blob in _members(strict_json(files[manifest['cad']['manifest']['path']]))}
    for name in (variant.FOOTPRINT, variant.DIODE_FOOTPRINT):
        path = variant.LIBRARY + '.pretty/' + name + '.kicad_mod'
        old_members[variant.LIBRARY + ':' + name] = dict(path=path, sha256=sha256(files[path]).hexdigest())
    libraries = {}
    for footprint in sorted({p['footprint'] for p in manifest['parts']}):
        libraries.setdefault(footprint.split(':')[0], []).append(old_members[footprint])
    cad = dict(schema='agent-ee.footprint-libraries/1', complete=True,
        libraries=[dict(name=name, members=members) for name, members in libraries.items()])
    for key, document in (('assignments', assignments), ('manifest', cad)):
        path = manifest['cad'][key]['path']
        files[path] = json_bytes(document)
        manifest['cad'][key]['sha256'] = sha256(files[path]).hexdigest()
    declaration_path = AT + '/footprint-declarations.json'
    files[declaration_path] = json_bytes(dict(schema='agent-ee.footprint-declarations/1',
        footprints=[dict(footprint=variant.LIBRARY + ':' + variant.FOOTPRINT,
            mechanical_pads=[], thermal_via_pads=['9'], custom_pads=[], assembly=None,
            rationale='ADP7104 exposed ground pad9 includes one SMD land and four plated thermal vias; '
                      'all share ground. The native heatsink properties retain SMD assembly.')],
        pinless_symbols=[]))
    manifest['cad']['declarations'] = dict(path=declaration_path,
        sha256=sha256(files[declaration_path]).hexdigest())
    members = [blob for _, blob in _members(cad)] + [
        {k: lib[k] for k in ('path', 'sha256')} for lib in circuit['libraries']]
    manifest['cad']['members_sha256'] = identity(sorted(members, key=lambda m: m['path']))
    files['parts.json'] = json_bytes(parts_manifest.validate_manifest(manifest))
    files['electrical-locality.json'] = json_bytes(variant.locality(strict_json(before['electrical-locality.json'])))
    files['ratings.json'] = json_bytes(dict(schema='agent-ee.passive-rating-sources/1', catalog=True,
                                           extractor=None, facts=None))
    files[AT + '/context.json'] = context
    # Return only selected dependency bytes, never the old check/review tree.
    report = dict(schema='agent-ee.acquisition-power-parts-preparation/1', reference=variant.REFERENCE,
        base=BASE.copy(), source_context_sha256=sha256(context).hexdigest(),
        original_dependencies=dependencies, generation=generation,
        files={path: sha256(raw).hexdigest() for path, raw in sorted(files.items())},
        accepted=False, file_release='not_assessed',
        remaining=['Normal production parts compilation and native generation',
                   'New power models, source-role qualification and typed requirement/check integration',
                   'Final placement/routing and fresh independent engineering/file acceptance'])
    return files, report
