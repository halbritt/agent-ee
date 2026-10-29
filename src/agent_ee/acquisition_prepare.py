"""Prepare the captured acquisition recipe from an explicit capacitance-only brief.

This source producer runs no native tools and grants no engineering acceptance.
The supported reference, prose grammar and unchanged inputs are documented in
docs/acquisition-prepare.md. All output is staged before publication.
"""
import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, Inexact, localcontext
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from . import acquisition_selected_cpl, acquisition_alternative, acquisition_alternative_context, acquisition_local_return, acquisition_mechanics, build_plan, catalog, profiles, requirement_map, spec_record, storage, typed_overlay
from .storage import json_bytes, rename_noreplace

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = 'acquisition-control'
REVIEW_PROFILE = 'preparation/review-profile.json'
ADOPTION = 'Use acquisition reference acquisition-control unchanged.'
CAPACITANCE = re.compile(
    r'Total I2C line capacitance shall be at most (?P<quantity>(?P<value>[0-9]+(?:\.[0-9]+)?)'
    r'\s+pF) per line, including board, host and cable\.')
REVISION_BASIS = {
    'spec.md': 'Apply only the explicit total-line capacitance bound',
    'spec-record.json': 'Recapture the actual revised UTF-8 spec bytes',
    'requirement-map.json': 'Rebind source record/partitions and source-selected stock catalog; declare mechanics and preparation provenance',
    'parts.json': 'Rebind operative spec or source-selected purchasing/stock records without changing CAD or limits',
    'circuit.json': 'Update only the source-selected ADC purchasing display value',
    'assembly-release-ids.json': 'Regenerate operative selected-allocation roster; archive historical choices, no supplier approval',
    'component-selections.json': 'Record automatic primary/carrier purchasing selection; retain historical original',
    'electrical-locality.json': 'Rebind unchanged locality citations and embedded spec',
    'placement.json': 'Rebind embedded locality; preserve placement geometry and policy',
    'analysis/electrical-intervals.json': 'Update only bus capacitance and operative source bindings',
    'OPERATING.md': 'Apply selected total bus capacitance to operating instructions',
    'engineering-calculations.json': 'Recompute the retained RC model with the selected operand',
    'ENGINEERING-REVIEW.md': 'Transcribe changed arithmetic and withhold inherited review credit',
}


class PreparationRefused(ValueError):
    """A source-bound unresolved or incompatible brief; no bundle was published."""

    def __init__(self, code, message, *, start=0, end=0, outcome='unresolved'):
        super().__init__(message)
        self.report = dict(outcome=outcome, findings=[dict(
            code=code, message=message, byte_start=start, byte_end=end)])


def _capacitance_text(value):
    """Remove spelling-only padding without context-sensitive Decimal.normalize."""
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def parse_brief(raw):
    """Consume every nonblank line; never infer adoption or discard material text."""
    try:
        text = raw.decode('utf-8')
    except UnicodeError as exc:
        raise PreparationRefused('encoding', 'brief must be UTF-8', start=exc.start, end=exc.end) from exc
    adoption, bounds, offset = [], [], 0
    for line in text.splitlines(keepends=True):
        content = line.strip()
        start = offset + len(line[:len(line) - len(line.lstrip())].encode())
        end = start + len(content.encode())
        offset += len(line.encode())
        if not content:
            continue
        if content == ADOPTION:
            adoption.append([start, end])
        elif match := CAPACITANCE.fullmatch(content):
            bounds.append(dict(value=Decimal(match['value']), quantity=match['quantity'], span=[start, end]))
        else:
            raise PreparationRefused('unsupported_clause', 'unsupported or ambiguous brief clause: ' + content,
                                     start=start, end=end)
    if not adoption:
        raise PreparationRefused('missing_reference_adoption', 'explicit named reference adoption is required')
    if len(adoption) != 1:
        raise PreparationRefused('duplicate_reference_adoption', 'reference adoption must occur exactly once')
    if not bounds:
        raise PreparationRefused('missing_capacitance', 'total line capacitance including board, host and cable is required')
    if len(bounds) != 1:
        code = 'contradictory_capacitance' if len({b['value'] for b in bounds}) > 1 else 'duplicate_capacitance'
        raise PreparationRefused(code, 'supply exactly one total-line capacitance bound',
                                 start=bounds[0]['span'][0], end=bounds[-1]['span'][1], outcome='refused')
    selected = bounds[0]
    if not Decimal(100) <= selected['value'] <= Decimal(150):
        raise PreparationRefused('unsupported_fixed_recipe_bound',
            'this captured fixed-pullup recipe supports total line capacitance from 100 through 150 pF inclusive; '
            'no alternative part or broader model applicability is inferred',
            start=selected['span'][0], end=selected['span'][1], outcome='refused')
    try:
        catalog.decimal_number(_capacitance_text(selected['value']))
    except ValueError as exc:
        raise PreparationRefused('unsupported_fixed_recipe_bound',
            'capacitance exceeds the existing interval checker precision of 28 canonical decimal digits; '
            'leading integer zeros and trailing fractional zeros do not count',
            start=selected['span'][0], end=selected['span'][1], outcome='refused') from exc
    return dict(selected, adoption_span=adoption[0])


def _render_spec(raw, quantity):
    marker = b'at most 100 pF total line capacitance'
    if raw.count(marker) != 1:
        raise ValueError('captured capacitance clause differs from this recipe')
    start = raw.index(marker) + len(b'at most ')
    end = start + len(b'100 pF')
    replacement = quantity.encode('utf-8')
    revised = raw[:start] + replacement + raw[end:]

    def position(byte):
        if byte <= start:
            return byte
        if byte >= end:
            return byte + len(replacement) - (end - start)
        raise ValueError('a source partition splits the replaced capacitance quantity')
    return revised, position


def _move_spans(document, position):
    """Move declared byte endpoints, preserving all other authored policy."""
    if isinstance(document, list):
        return [_move_spans(row, position) for row in document]
    if not isinstance(document, dict):
        return document
    result = {key: _move_spans(value, position) for key, value in document.items()}
    for key in ('byte_start', 'byte_end'):
        if key in result:
            result[key] = position(result[key])
    return result


def _source_bindings(document, old_spec, new_spec, position):
    """Rebind only citations to this spec, not arbitrary matching digest strings."""
    if isinstance(document, list):
        return [_source_bindings(row, old_spec, new_spec, position) for row in document]
    if not isinstance(document, dict):
        return document
    result = {key: _source_bindings(value, old_spec, new_spec, position) for key, value in document.items()}
    if result.get('path') in ('spec.md', 'requirements-map/spec.md'):
        if result.get('sha256') != spec_record.sha256(old_spec):
            raise ValueError('operative spec citation does not bind the captured original')
        result['sha256'] = spec_record.sha256(new_spec)
        locator = result.get('locator')
        if isinstance(locator, str) and locator.startswith('{'):
            result['locator'] = json.dumps(_move_spans(json.loads(locator), position), sort_keys=True)
    records = result.get('source_records', {})
    if 'spec.md' in records:
        if records['spec.md'] != old_spec.decode():
            raise ValueError('embedded spec differs from captured original')
        records['spec.md'] = new_spec.decode()
    return result


def _revise_overlay(overlay, position, capacitance):
    revised = _move_spans(overlay, position)
    for brief in revised['judgments'].values():
        brief['span'] = [position(byte) for byte in brief['span']]
        brief['sources'] = [re.sub(r'spec\.md bytes (\d+):(\d+)',
            lambda m: f'spec.md bytes {position(int(m[1]))}:{position(int(m[2]))}', source)
            for source in brief['sources']]
    host = revised['judgments']['judgments/acq-elec-5-host-bus-contract.json']
    host['questions'] = [question.replace('100 pF', capacitance + ' pF') for question in host['questions']]
    return revised


def _write_revision(base, revised, selected, overlay):
    capacitance = _capacitance_text(selected['value'])
    old_spec = (base / 'spec.md').read_bytes()
    new_spec, position = _render_spec(old_spec, selected['quantity'])
    old_record = spec_record.verify((base / 'spec-record.json').read_bytes(), old_spec)
    record = spec_record.capture(new_spec, old_record['ids']['prefix'], old_record['ids']['codes'])
    (revised / 'spec.md').write_bytes(new_spec)
    (revised / 'spec-record.json').write_bytes(json_bytes(record))
    declaration = _move_spans(requirement_map.map_declaration((base / 'requirement-map.json').read_bytes()), position)
    declaration['spec_record_sha256'] = spec_record.sha256(json_bytes(record))
    declaration['scope']['inputs'].append(dict(path='preparation', at='preparation'))
    declaration['scope']['inputs'].append(dict(path='placement-mechanics.json', at='placement-mechanics.json'))
    (revised / 'requirement-map.json').write_bytes(json_bytes(declaration))
    for name in ('parts.json', 'electrical-locality.json', 'placement.json', 'analysis/electrical-intervals.json'):
        document = _source_bindings(json.loads((base / name).read_bytes()), old_spec, new_spec, position)
        if name.startswith('analysis/'):
            document['parameters']['bus_c'].update(min=capacitance + 'e-12 F', max=capacitance + 'e-12 F')
        (revised / name).write_bytes(json_bytes(document))
    operating = (base / 'OPERATING.md').read_text()
    if operating.count('100 pF per line') != 1:
        raise ValueError('captured operating capacitance instruction differs from this recipe')
    (revised / 'OPERATING.md').write_text(operating.replace('100 pF per line', selected['quantity'] + ' per line'))
    # 28 admitted coefficient digits plus the two four-digit factors need at most 36.
    with localcontext() as context:
        context.prec = 40
        context.traps[Inexact] = True
        rise = Decimal('.8473') * Decimal('2222') * Decimal(capacitance) * Decimal('1e-12')
        rise_ns = rise * Decimal('1e9')
    calculations = json.loads((base / 'engineering-calculations.json').read_bytes())
    calculations['values']['i2c_rise_time'] = dict(value=str(rise), unit='s',
        basis=f'Captured 30..70 percent RC model, total {capacitance} pF, Rmax 2222 ohm; not a waveform measurement')
    calculations['sources'] = {('spec.md' if sha == spec_record.sha256(old_spec) else path):
        (spec_record.sha256(new_spec) if sha == spec_record.sha256(old_spec) else sha)
        for path, sha in calculations['sources'].items()}
    (revised / 'engineering-calculations.json').write_bytes(json_bytes(calculations))
    review = (base / 'ENGINEERING-REVIEW.md').read_text()
    if review.count('At100pF') != 1 or review.count('188.27ns') != 1:
        raise ValueError('captured engineering narrative differs from this recipe')
    review = review.replace('At100pF', f'At{capacitance}pF').replace('188.27ns', f'{rise_ns:f}ns')
    (revised / 'ENGINEERING-REVIEW.md').write_text(
        'Preparation transcription only: this inherited narrative is not independent review, judgment or acceptance.\n\n' + review)
    overlay = _revise_overlay(overlay, position, capacitance)
    overlay['base'] = dict(spec_sha256=spec_record.sha256(new_spec),
        spec_record_sha256=spec_record.sha256((revised / 'spec-record.json').read_bytes()),
        requirement_map_sha256=spec_record.sha256((revised / 'requirement-map.json').read_bytes()))
    return overlay, old_record, record


def _admit(plan_path, out, added_sources):
    plan_path, out = Path(plan_path).resolve(), Path(out).absolute()
    plan_raw = plan_path.read_bytes()
    plan = build_plan.validate_plan(requirement_map.decode(plan_raw))
    base = (plan_path.parent / plan['source']).resolve()
    manifest = json.loads((ROOT / 'fixtures/recovery/inputs.json').read_bytes())['files']
    if spec_record.sha256(plan_raw) != manifest['build/acquisition-release/build.json']['sha256']:
        raise ValueError('build plan differs from the captured acquisition-control recipe')
    files = requirement_map.input_inventory(base)
    expected = {name.removeprefix('build/acquisition-release/inputs/'): pin['sha256']
                for name, pin in manifest.items() if name.startswith('build/acquisition-release/inputs/')}
    if files != expected:
        raise ValueError('acquisition-control inputs differ from the captured recovery inventory')
    if os.path.lexists(out):
        raise ValueError(f'{out} exists')
    trees = dict(base=base, **{key: Path(path).resolve() for key, path in added_sources.items()})
    typed_overlay._disjoint('output', out.resolve(), trees)
    if not out.parent.is_dir():
        raise ValueError('output parent must already exist')
    return plan_raw, plan, base, files


def _load_overlay(before, sources):
    raw = (ROOT / 'fixtures/acquisition-typed/overlay.json').read_bytes()
    overlay = typed_overlay.load_overlay(raw)
    for filename, key in [('spec.md', 'spec_sha256'), ('spec-record.json', 'spec_record_sha256'),
                          ('requirement-map.json', 'requirement_map_sha256')]:
        if before[filename] != overlay['base'][key]:
            raise ValueError(f'{filename}: captured base differs from the selected typed overlay')
    if sources.keys() != overlay['added_inputs'].keys():
        raise ValueError('added inputs must supply exactly cpl, manufacturer-text and operating-supplement')
    additions = {key: requirement_map.input_inventory(path) for key, path in sources.items()}
    for key, files in additions.items():
        if files != overlay['added_inputs'][key]['files']:
            raise ValueError(f'{key}: additive capture differs from the typed overlay inventory')
    return raw, overlay, additions


def _source_map(raw, selected, original_record, record):
    originals = {r['id']: r for r in original_record['requirements'] + original_record['prose']}
    supplement = (ROOT / 'fixtures/acquisition-typed/operating-supplement/ADS1115-OPERATING.md').read_bytes()
    old_bound = b'100 pF capacitance'
    start = supplement.index(old_bound)
    return dict(reference=REFERENCE, brief_sha256=spec_record.sha256(raw),
        original_spec=original_record['spec'], derived_spec=record['spec'],
        adoption_span=selected['adoption_span'], capacitance_span=selected['span'],
        capacitance_pf=_capacitance_text(selected['value']),
        changed_requirement='ACQ-ELEC-5', fields=['analysis/electrical-intervals.json#/parameters/bus_c'],
        operating_supplement=dict(path='operating-supplement/ADS1115-OPERATING.md',
            sha256=spec_record.sha256(supplement), byte_start=start, byte_end=start + len(old_bound),
            status='superseded_bound' if selected['value'] != 100 else 'retained_bound',
            preservation='Power-up and conversion timing/sequence instructions remain in force'),
        requirements=[dict(id=r['id'], original=originals[r['id']]['locator'],
                           derived=r['locator'], text_sha256=r['text_sha256'])
                      for r in record['requirements'] + record['prose']])


def _bind_parts_membership(revised, scope):
    """Bind the retained authored citation to the normal requirement compiler source."""
    path = Path(revised) / 'parts.json'
    document = json.loads(path.read_bytes())
    checks = [c for c in scope['checks'] if c['kind'] == 'parts_subset']
    if len(checks) != 1:
        raise ValueError('acquisition parts membership needs exactly one authored check')
    check = checks[0]
    mapped = [r for r in scope['baseline']['requirements'] if check['id'] in r['checks']]
    citation = document['declaration_source']
    expected_sha = spec_record.sha256((Path(revised) / 'spec.md').read_bytes())
    if (len(mapped) != 1 or mapped[0]['id'] != check['params']['requirement_id'] or
            mapped[0]['source']['path'] != requirement_map.PROVENANCE + '/spec.md' or
            citation['path'] not in ('spec.md', mapped[0]['source']['path']) or
            citation['sha256'] != expected_sha or citation['locator'] != mapped[0]['source']['locator']):
        raise ValueError('authored parts declaration differs from its mapped membership requirement')
    before = dict(citation)
    citation['path'] = mapped[0]['source']['path']
    path.write_bytes(json_bytes(document))
    return dict(requirement_id=mapped[0]['id'], before=before, after=dict(citation),
                basis='Normal requirements compiler captures this same operative specification; no decision replaces it')


def _archive_revision(base, revised, before, identity):
    changed = {}
    for name, original in before.items():
        digest = spec_record.sha256((revised / name).read_bytes())
        if digest == original:
            continue
        changed[name] = dict(before=original, after=digest, basis=REVISION_BASIS[name])
        target = revised / 'history/acquisition-preparation-original' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / name, target)
    (revised / 'preparation/edit-ledger.json').write_bytes(json_bytes(dict(
        identity=identity, original_inventory=before, changed=changed,
        qualification='unverified; preparation only, no native work or independent engineering review')))
    return changed


def _read_context_file(path, label):
    """Read one regular context file through no-follow directory descriptors."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError(label + ' path contains traversal')
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            os.close(descriptor)
            raise ValueError(label + ' must be a regular file without link aliases')
        with os.fdopen(descriptor, 'rb') as stream:
            raw = stream.read()
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            stamp = tuple(getattr(before, field) for field in fields)
            if stamp != tuple(getattr(os.fstat(stream.fileno()), field) for field in fields):
                raise ValueError(label + ' changed while reading')
        return raw, stamp
    finally:
        os.close(parent)


def _read_review_profile(path):
    return _read_context_file(path, 'review profile')


def prepare(brief, plan_path, out, *, added_sources, review_profile=None, verification_timeout_s=None,
            alternative_context=None, alternative_source=None, alternative_context_sha256=None):
    """Publish a complete typed source bundle for the captured acquisition recipe.

    ``plan_path`` is the existing captured build plan; its source directory,
    manufacturing profile and finite native budgets are retained. Additions map
    ``cpl`` and ``manufacturer-text`` to their exact captured directories. The
    committed operating supplement is used unless explicitly supplied. Explicit
    review context is captured separately from the unchanged circuit recipe.
    """
    brief, out = Path(brief), Path(out).absolute()
    raw = brief.read_bytes()
    try:
        selected = parse_brief(raw)
    except PreparationRefused as exc:
        exc.report['brief_sha256'] = spec_record.sha256(raw)
        raise
    sources = {'operating-supplement': ROOT / 'fixtures/acquisition-typed/operating-supplement', **added_sources}
    plan_raw, plan, base, before = _admit(plan_path, out, sources)
    typed_overlay._disjoint('output', out.resolve(), {'brief': brief.resolve(), 'plan': Path(plan_path).resolve()})
    if verification_timeout_s is not None and review_profile is None:
        raise ValueError('verification_timeout_s requires review_profile')
    plan = copy.deepcopy(plan)
    plan.update(schema=build_plan.TYPED_SCHEMA, source='inputs', judgments='judgments')
    plan['requirements']['obligations'] = 'obligations.json'
    review_context = None
    if review_profile is not None:
        profile_raw, profile_stamp = _read_review_profile(review_profile)
        try:
            profiles.validate_profile(requirement_map.decode(profile_raw))
        except (AttributeError, TypeError) as exc:
            raise ValueError('malformed review profile') from exc
        typed_overlay._disjoint('output', out.resolve(), {'review profile': Path(review_profile).absolute()})
        plan['review_profile'] = REVIEW_PROFILE
        if verification_timeout_s is not None:
            plan['verification_timeout_s'] = verification_timeout_s
        review_context = dict(profile=dict(path=REVIEW_PROFILE, sha256=spec_record.sha256(profile_raw)),
            verification_timeout_s=dict(requested=verification_timeout_s,
                                        effective=plan.get('verification_timeout_s', 3600)))
    alternative = None
    if alternative_context is not None:
        if alternative_source is None or alternative_context_sha256 is None:
            raise ValueError('alternative context requires source root and exact context SHA-256')
        context_raw, context_stamp = _read_context_file(alternative_context, 'alternative context')
        if spec_record.sha256(context_raw) != alternative_context_sha256:
            raise ValueError('alternative context hash mismatch')
        context_doc, context_captures = acquisition_alternative_context.read(alternative_source, context_raw)
        typed_overlay._disjoint('output', out.resolve(), {
            'alternative source': Path(alternative_source).absolute(),
            'alternative context': Path(alternative_context).absolute()})
        alternative = dict(path='preparation/alternative-context.json',
            sha256=alternative_context_sha256, files=context_doc['files'],
            evaluation_as_of=datetime.now(timezone.utc).isoformat())
    elif alternative_source is not None or alternative_context_sha256 is not None:
        raise ValueError('alternative source/hash require alternative_context')
    build_plan.validate_plan(plan)
    overlay_raw, overlay, additions = _load_overlay(before, sources)
    recipe = dict(brief_sha256=spec_record.sha256(raw), plan_sha256=spec_record.sha256(plan_raw),
        source_inventory_sha256=spec_record.sha256(json_bytes(before)),
        overlay_sha256=spec_record.sha256(overlay_raw), producer_sha256=spec_record.sha256(Path(__file__).read_bytes()),
        helpers={module.__name__: spec_record.sha256(Path(module.__file__).read_bytes())
                 for module in (acquisition_local_return, acquisition_mechanics, build_plan, catalog, requirement_map, spec_record, storage, typed_overlay)},
        additions={key: spec_record.sha256(json_bytes(files)) for key, files in additions.items()})
    if review_context is not None:
        recipe['review_context'] = review_context
        recipe['helpers'][profiles.__name__] = spec_record.sha256(Path(profiles.__file__).read_bytes())
    if alternative is not None:
        recipe['alternative_context'] = alternative
        recipe['selected_cpl_inventory'] = acquisition_selected_cpl.projected_inventory(
            additions['cpl'], context_doc, context_captures)
        for module in (acquisition_alternative, acquisition_alternative_context, acquisition_selected_cpl,
                       acquisition_selected_cpl.cpl_rotation, acquisition_selected_cpl.easyeda_footprint,
                       acquisition_selected_cpl.land_pattern,
                       acquisition_alternative.assembly_readiness, acquisition_alternative.part_record,
                       acquisition_alternative.part_selection, acquisition_alternative.parts_manifest,
                       acquisition_alternative_context.jlc_stock_capture):
            recipe['helpers'][module.__name__] = spec_record.sha256(Path(module.__file__).read_bytes())
    identity = spec_record.sha256(json_bytes(recipe))
    with tempfile.TemporaryDirectory(prefix=out.name + '.prepare-', dir=out.parent) as scratch:
        scratch = Path(scratch)
        revised, bundle = scratch / 'revision', scratch / 'bundle'
        shutil.copytree(base, revised)
        overlay, original_record, record = _write_revision(base, revised, selected, overlay)
        provenance = revised / 'preparation'
        provenance.mkdir()
        (provenance / 'brief.md').write_bytes(raw)
        (provenance / 'build-plan.original.json').write_bytes(plan_raw)
        (provenance / 'overlay.original.json').write_bytes(overlay_raw)
        (provenance / 'source-map.json').write_bytes(json_bytes(_source_map(raw, selected, original_record, record)))
        alternative_report = None
        if alternative is not None:
            # The requirements stage has not produced its namespace yet. Bind
            # this same authored citation to the actual operative spec bytes.
            parts_path = revised / 'parts.json'
            authored = json.loads(parts_path.read_bytes())
            citation = authored['declaration_source']
            if citation['path'] == 'requirements-map/spec.md':
                if citation['sha256'] != spec_record.sha256((revised / 'spec.md').read_bytes()):
                    raise ValueError('parts source citation differs from the operative spec')
                citation['path'] = 'spec.md'
                parts_path.write_bytes(json_bytes(authored))
            (revised / alternative['path']).write_bytes(context_raw)
            alternative_report = acquisition_alternative_context.apply(
                revised, context_doc, context_captures, alternative['evaluation_as_of'])
            selected_cpl = scratch / 'selected-cpl'
            acquisition_selected_cpl.materialize(sources['cpl'], selected_cpl, context_doc,
                context_captures, recipe['selected_cpl_inventory'])
            selected_closure = acquisition_selected_cpl.finalize(revised, overlay, selected_cpl)
            sources['cpl'] = selected_cpl
            overlay['added_inputs']['cpl']['files'] = recipe['selected_cpl_inventory']
            # Match the ordinary compiler's actual authored requirement mapping,
            # while it still remains a source revision, before archive and seal.
            operative = typed_overlay.derive_map(
                requirement_map.map_declaration((revised / 'requirement-map.json').read_bytes()), overlay)
            spec = (revised / 'spec.md').read_bytes()
            obligations = requirement_map.load_obligations(
                json_bytes(typed_overlay.derive_obligations(record, spec, overlay)), record, spec)
            membership_scope, _ = requirement_map.account_requirements(
                operative, record, spec, files=requirement_map.input_inventory(revised),
                inputs=revised, obligations=obligations)
            membership_binding = _bind_parts_membership(revised, membership_scope)
            overlay['base']['requirement_map_sha256'] = spec_record.sha256(
                (revised / 'requirement-map.json').read_bytes())
        changed = _archive_revision(base, revised, before, identity)
        if alternative_report is not None:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            ledger['alternative'] = dict(selected=alternative_report['selected'], context=alternative,
                changed=alternative_report['changed'], added=alternative_report['added'],
                catalog=alternative_report['catalog'], selected_part_inputs=selected_closure,
                membership_binding=membership_binding,
                captured_sources={acquisition_alternative_context.AT + '/' + n: h
                                  for n, h in context_doc['files'].items()})
            ledger_path.write_bytes(json_bytes(ledger))
        if review_context is not None:
            (revised / REVIEW_PROFILE).write_bytes(profile_raw)
            (provenance / 'review-context.json').write_bytes(json_bytes(review_context))
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            ledger['review_context'] = review_context
            ledger_path.write_bytes(json_bytes(ledger))
        overlay_file = scratch / 'overlay.json'
        overlay_file.write_bytes(json_bytes(overlay))
        bundle.mkdir()
        # Bind the operative typed map before sealing the source bundle. The
        # overlay will write these exact bytes; do not append inputs after seal.
        mechanics = acquisition_mechanics.declaration(revised)
        operative = typed_overlay.derive_map(
            requirement_map.map_declaration((revised / 'requirement-map.json').read_bytes()), overlay)
        mechanics['sources']['requirement-map.json'] = spec_record.sha256(json_bytes(operative))
        (revised / 'placement-mechanics.json').write_bytes(json_bytes(mechanics))
        ledger_path = provenance / 'edit-ledger.json'
        ledger = json.loads(ledger_path.read_bytes())
        ledger['mechanics'] = dict(path='placement-mechanics.json', sha256=spec_record.sha256(json_bytes(mechanics)),
            basis='Captured source bodies and mating columns; fresh original native identity is bound at placement capture')
        ledger_path.write_bytes(json_bytes(ledger))
        plan['placement']['mechanics'] = 'placement-mechanics.json'
        result = typed_overlay.prepare(revised, overlay_file, bundle / 'inputs', added_sources=sources)
        acquisition_mechanics.load(bundle / 'inputs/placement-mechanics.json')
        (bundle / 'build-plan.json').write_bytes(json_bytes(plan))
        (bundle / 'overlay.json').write_bytes(json_bytes(overlay))
        if requirement_map.input_inventory(base) != before:
            raise ValueError('original source changed during preparation')
        report = dict(schema='agent-ee.acquisition-preparation/1', outcome='prepared',
            identity=identity, recipe=recipe, reference=REFERENCE, qualification='unverified',
            brief_sha256=spec_record.sha256(raw), changed=changed,
            accounting=result['manifest']['accounting'],
            inputs=requirement_map.input_inventory(bundle / 'inputs'),
            remaining=['Native public build and file qualification', 'Independent judgments and engineering review',
                       'Strict block-source selection and final artwork/package revalidation if strict grouping is requested',
                       'Supplier and physical validation'])
        if alternative_report is not None:
            report['alternative'] = dict(selected=alternative_report['selected'],
                decision=alternative_report['decision'], catalog=alternative_report['catalog'],
                context=alternative)
        (bundle / 'preparation.json').write_bytes(json_bytes(report))
        if review_context is not None:
            if ((bundle / 'inputs' / REVIEW_PROFILE).read_bytes() != profile_raw
                    or report['inputs'][REVIEW_PROFILE] != review_context['profile']['sha256']):
                raise ValueError('captured review profile changed during preparation')
            if _read_review_profile(review_profile) != (profile_raw, profile_stamp):
                raise ValueError('review profile changed during preparation')
        if alternative is not None:
            if _read_context_file(alternative_context, 'alternative context') != (context_raw, context_stamp):
                raise ValueError('alternative context changed during preparation')
            _, replay = acquisition_alternative_context.read(alternative_source, context_raw)
            if replay != context_captures:
                raise ValueError('alternative source changed during preparation')
            for name, captured in context_captures.items():
                if (bundle / 'inputs' / acquisition_alternative_context.AT / name).read_bytes() != captured:
                    raise ValueError('captured alternative source changed before publication')
            if (bundle / 'inputs' / alternative['path']).read_bytes() != context_raw:
                raise ValueError('captured alternative context changed before publication')
        rename_noreplace(bundle, out)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief', type=Path, required=True)
    parser.add_argument('--build-plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--review-profile', type=Path, help='explicit full engineering review profile to capture')
    parser.add_argument('--verification-timeout-s', type=int,
                        help='explicit build verification budget; requires --review-profile, no automatic increase')
    parser.add_argument('--alternative-context', type=Path, help='pinned finite acquisition carrier context JSON')
    parser.add_argument('--alternative-source', type=Path, help='read-only root of the context file closure')
    parser.add_argument('--alternative-context-sha256', help='exact SHA-256 of the admitted context JSON')
    parser.add_argument('--added-input', action='append', default=[], metavar='AT=DIR')
    args = parser.parse_args(argv)
    sources = {}
    for value in args.added_input:
        key, sep, path = value.partition('=')
        if not sep or not key or not path or key in sources:
            parser.error('--added-input requires a distinct AT=DIR')
        sources[key] = Path(path)
    try:
        report = prepare(args.brief, args.build_plan, args.out, added_sources=sources,
                         review_profile=args.review_profile, verification_timeout_s=args.verification_timeout_s,
                         alternative_context=args.alternative_context, alternative_source=args.alternative_source,
                         alternative_context_sha256=args.alternative_context_sha256)
    except PreparationRefused as exc:
        print(json.dumps(exc.report, ensure_ascii=False))
        return 1
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(outcome='unavailable', findings=[dict(code='source_preparation', message=str(exc))])))
        return 2
    print(json.dumps({key: report[key] for key in ('outcome', 'identity', 'qualification')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
