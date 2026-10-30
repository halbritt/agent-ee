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

from . import acquisition_resistor, acquisition_expected_intent, acquisition_power_repair, acquisition_source_supplement, acquisition_selected_cpl, acquisition_alternative, acquisition_alternative_context, acquisition_local_return, acquisition_mechanics, build_plan, catalog, profiles, requirement_map, spec_record, storage, typed_overlay
from . import acquisition_local_geometry, acquisition_local_routing, acquisition_connector_geometry
from .storage import json_bytes, rename_noreplace

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = 'acquisition-control'
REVIEW_PROFILE = 'preparation/review-profile.json'
INHERITED_REVIEW = 'ENGINEERING-REVIEW.md'
REVIEW_SOURCE_REBINDINGS = {
    'acq-elec-1-temperature-applicability': ('ACQ-ELEC-1', ('OPERATING.md', 'engineering-calculations.json conditions')),
    'acq-elec-1-unqualified-conditions': ('ACQ-ELEC-1', ('OPERATING.md sequencing',)),
    'acq-elec-3-back-power-contract': ('ACQ-ELEC-3', ('OPERATING.md', 'circuit.json')),
    'acq-elec-4-error-evaluation': ('ACQ-ELEC-4', ('engineering-calculations.json', 'OPERATING.md')),
    'acq-elec-5-configuration': ('ACQ-ELEC-5', ('OPERATING.md', 'engineering-calculations.json')),
    'acq-elec-6-high-impedance-contract': ('ACQ-ELEC-6', ('OPERATING.md', 'circuit.json')),
    'acq-mech-3-limit-rationale': ('ACQ-MECH-3', ('requirement-map.json engineering-geometry limits', 'analysis/geometry-model.json')),
    'acq-mech-3-return-paths': ('ACQ-MECH-3', ('requirement-map.json engineering-geometry limits', 'analysis/geometry_model.py')),
}
INHERITED_SCOPE_DESCRIPTION = (
    'ENGINEERING-REVIEW.md records source-based judgments, model applicability and assembly targets; '
    'native checks and explicit model calculations verify their declared predicates on this generated candidate.')
SOURCE_SCOPE_DESCRIPTION = (
    'Captured calculations, models and operating instructions declare source assumptions and assembly targets; '
    'native checks evaluate their declared predicates on this generated candidate. '
    'Fresh candidate-bound engineering judgments remain required.')
ADOPTION = 'Use acquisition reference acquisition-control unchanged.'
CAPACITANCE = re.compile(
    r'Total I2C line capacitance shall be at most (?P<quantity>(?P<value>[0-9]+(?:\.[0-9]+)?)'
    r'\s+pF) per line, including board, host and cable\.')
REVISION_BASIS = {
    'spec.md': 'Apply only the explicit total-line capacitance bound',
    'spec-record.json': 'Recapture the actual revised UTF-8 spec bytes',
    'requirement-map.json': 'Rebind source record/partitions and stock catalog; optional independently captured fixed-GND parity oracle; declare mechanics/preparation provenance',
    'parts.json': 'Rebind operative spec or source-selected purchasing/stock records without changing CAD or limits',
    'circuit.json': 'Update the source-selected ADC purchasing display and optional finite U2 ADDR pin1 GND/V3V3 tie',
    'assembly-release-ids.json': 'Regenerate operative selected-allocation roster; archive historical choices, no supplier approval',
    'component-selections.json': 'Record automatic ADC and optional finite R1/R2 purchasing selection; retain historical original',
    'electrical-locality.json': 'Rebind citations/spec; optional root706 engineering final-group additions preserve original eight legs',
    'placement.json': 'Rebind embedded locality and optional root706 final-group additions; preserve geometry and policy',
    'analysis/electrical-intervals.json': 'Update only bus capacitance and operative source bindings',
    'OPERATING.md': 'Bind selected capacitance, ADC applicability and optional captured Panasonic R1/R2 source attribution',
    'analysis/power-startup/corners.lib': 'Explicit R6+1% adverse626.2ohm; all other original corner values retained',
    'analysis/power-removal/corners.lib': 'Explicit R6+1% adverse626.2ohm; unused historical model remains documentary',
    'analysis/power-startup/README.md': 'Declare seven reviewed source cases, assumptions and convergence history without inherited pass',
    'analysis/power-removal/README.md': 'Declare corrected source-only removal derivative, zero ADC minimum and retained200uA stress',
    'analysis/geometry-model.json': 'Bind exact Murata/R6 and optional Panasonic lineage; correct PH body centers from captured source geometry',
    'engineering-calculations.json': 'Recompute RC operand; bind captured sources and rate-qualified ADC terms',
    'ENGINEERING-REVIEW.md': 'Omit inherited board testimony; retain the exact original only in the captured recipe',
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
    original_sources = {k: v for k, v in sources.items() if k not in (acquisition_source_supplement.AT, acquisition_power_repair.AT)}
    if original_sources.keys() != overlay['added_inputs'].keys():
        raise ValueError('added inputs must supply exactly cpl, manufacturer-text and operating-supplement')
    additions = {key: requirement_map.input_inventory(path) for key, path in original_sources.items()}
    for key, files in additions.items():
        if files != overlay['added_inputs'][key]['files']:
            raise ValueError(f'{key}: additive capture differs from the typed overlay inventory')
    if acquisition_source_supplement.AT in sources:
        captured = acquisition_source_supplement.read(sources[acquisition_source_supplement.AT])
        files = {name: spec_record.sha256(raw) for name, raw in captured.items()}
        additions[acquisition_source_supplement.AT] = files
        overlay['added_inputs'][acquisition_source_supplement.AT] = dict(files=files)
    if acquisition_power_repair.AT in sources:
        captures = acquisition_power_repair.read(sources[acquisition_power_repair.AT])
        files = {n: spec_record.sha256(b) for n, b in captures.items()}
        additions[acquisition_power_repair.AT] = files
        overlay['added_inputs'][acquisition_power_repair.AT] = dict(files=files)
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


def _refuse_inherited_review(root, original_sha256, *, allow_revision=False):
    files = requirement_map.input_inventory(root)
    for name, sha256 in files.items():
        if allow_revision and name == INHERITED_REVIEW:
            continue
        if Path(name).name == INHERITED_REVIEW or sha256 == original_sha256:
            raise ValueError('inherited board review must not enter generated source: ' + name)
    return files


def _isolate_inherited_review(base, revised, overlay, before):
    """Helpers may transcribe the pinned recipe; fresh seats must never receive its verdict."""
    files = _refuse_inherited_review(revised, before[INHERITED_REVIEW], allow_revision=True)
    if INHERITED_REVIEW not in files:
        raise ValueError('missing inherited review in the owned source revision')
    expected = {('judgments/' + name + '.json', INHERITED_REVIEW + ' ' + section): sources
                for name, (section, sources) in REVIEW_SOURCE_REBINDINGS.items()}
    observed = [(name, source) for name, brief in overlay['judgments'].items()
                for source in brief['sources'] if INHERITED_REVIEW in source]
    if sorted(observed) != sorted(expected):
        raise ValueError('inherited review judgment source references differ from the captured recipe')
    path = revised / 'requirement-map.json'
    declaration = requirement_map.map_declaration(path.read_bytes())
    description = declaration['scope']['scope']
    original_description = requirement_map.map_declaration((base / 'requirement-map.json').read_bytes())['scope']['scope']
    if description != original_description or description.count(INHERITED_SCOPE_DESCRIPTION) != 1:
        raise ValueError('inherited review scope description differs from the captured recipe')
    declaration['scope']['scope'] = description.replace(INHERITED_SCOPE_DESCRIPTION, SOURCE_SCOPE_DESCRIPTION)
    for name, brief in overlay['judgments'].items():
        brief['sources'] = list(dict.fromkeys(replacement for source in brief['sources']
            for replacement in expected.get((name, source), (source,))))
    (revised / INHERITED_REVIEW).unlink()
    path.write_bytes(json_bytes(declaration))
    overlay['base']['requirement_map_sha256'] = storage.digest(path)


def _archive_revision(base, revised, before, identity):
    changed = {}
    for name, original in before.items():
        if name == INHERITED_REVIEW:
            if (revised / name).exists() or (revised / 'history/acquisition-preparation-original' / name).exists():
                raise ValueError('inherited board review was not isolated before archive')
            changed[name] = dict(before=original, after=None, basis=REVISION_BASIS[name],
                                 retention='Original captured recipe only; no generated or archived body')
            continue
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


def _write_final_overlay(path, overlay):
    """Bind pre-seal derivations to the exact final overlay consumed by the seal."""
    path.write_bytes(json_bytes(overlay))
    return typed_overlay.load_overlay(path.read_bytes())


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


def _select_addr(root, selected):
    """One finite authored terminal move, after common purchasing/source decisions."""
    if selected not in ('GND', 'V3V3'):
        raise ValueError('addr_tie must be GND or V3V3')
    path = Path(root) / 'circuit.json'
    before = path.read_bytes()
    circuit = catalog.strict_json(before)
    components = [c for c in circuit['components'] if c.get('id') == 'U2' or c.get('reference') == 'U2']
    if (circuit['schema'] != 'agent-ee.connectivity-intent/1' or len(components) != 1
            or components[0]['id'] != 'U2' or components[0]['reference'] != 'U2'
            or components[0]['lib_id'] != 'Analog_ADC:ADS1115IDGS'
            or components[0]['value'] not in acquisition_alternative.ORDERABLES.values()):
        raise ValueError('ADDR selection requires the original unique U2 ADS1115 identity')
    nets = circuit['nets']
    if (len({n['id'] for n in nets}) != len(nets) or len({n['name'] for n in nets}) != len(nets)):
        raise ValueError('ADDR selection requires unique original nets')
    targets = {name: [n for n in nets if n['id'] == name and n['name'] == name] for name in ('GND','V3V3')}
    if any(len(value) != 1 for value in targets.values()):
        raise ValueError('ADDR selection requires existing GND and V3V3 nets')
    terminal = {'component':'U2', 'pin':{'number':'1'}}
    def addr(t):
        return t.get('component') == 'U2' and (t.get('pin', {}).get('number') == '1'
                                                  or t.get('pin', {}).get('name') == 'ADDR')
    occurrences = [(n, t) for n in nets for t in n['terminals'] if addr(t)]
    if (len(occurrences) != 1 or occurrences[0][0] is not targets['GND'][0]
            or occurrences[0][1] != terminal or any(addr(t) for t in circuit['no_connects'])):
        raise ValueError('ADDR selection requires exactly original U2 pin1 on GND')
    if selected == 'V3V3':
        targets['GND'][0]['terminals'].remove(terminal)
        targets['V3V3'][0]['terminals'].append(terminal)
        path.write_bytes(json_bytes(circuit))
    return dict(component='U2', pin={'number':'1'}, original_net='GND', selected_net=selected,
        before_sha256=spec_record.sha256(before), after_sha256=storage.digest(path),
        qualification='Source intent only; unchanged adc-ties requires GND/address0x48; V3V3 is a development defect')


def _evaluation_replay(path, expected_sha):
    """Replay a pinned prior preparation decision, never accept a free as_of date."""
    raw, stamp = _read_context_file(path, 'preparation evaluation context')
    if spec_record.sha256(raw) != expected_sha:
        raise ValueError('preparation evaluation context hash mismatch')
    report = catalog.strict_json(raw)
    if not isinstance(report, dict) or not isinstance(report.get('recipe'), dict):
        raise ValueError('expected a prior preparation report with a recipe')
    recipe = report['recipe']
    if (report.get('schema') != 'agent-ee.acquisition-preparation/1'
            or report.get('outcome') != 'prepared' or report.get('reference') != REFERENCE
            or report.get('qualification') != 'unverified'
            or report.get('identity') != spec_record.sha256(json_bytes(recipe))
            or not isinstance(recipe.get('alternative_context'), dict)
            or recipe.get('addr_tie') not in (None, 'GND', 'V3V3')):
        raise ValueError('expected a pinned compatible prior preparation report')
    from .jlc_stock_capture import _utc
    anchor = recipe['alternative_context']['evaluation_as_of']
    if _utc(anchor, 'preparation evaluation anchor') > datetime.now(timezone.utc):
        raise ValueError('preparation evaluation anchor is in the future')
    return recipe, raw, stamp, anchor


def prepare(brief, plan_path, out, *, added_sources, review_profile=None, verification_timeout_s=None,
            alternative_context=None, alternative_source=None, alternative_context_sha256=None,
            addr_tie=None, evaluation_context=None, evaluation_context_sha256=None,
            expected_intent=None, expected_intent_sha256=None, expected_parts_receipt=None,
            expected_parts_receipt_sha256=None, final_groups=False,
            role_context=None, role_context_sha256=None, role_source=None,
            resistor_context=None, resistor_context_sha256=None, resistor_source=None,
            public_context=None, public_context_sha256=None):
    """Publish a complete typed source bundle for the captured acquisition recipe.

    ``plan_path`` is the existing captured build plan; its source directory,
    manufacturing profile and finite native budgets are retained. Additions map
    ``cpl`` and ``manufacturer-text`` to their exact captured directories. The
    committed operating supplement is used unless explicitly supplied. Explicit
    review context is captured separately from the unchanged circuit recipe.
    """
    if any(v is not None for v in (role_context,role_context_sha256,role_source)) and any(
            v is None for v in (role_context,role_context_sha256,role_source)):
        raise ValueError('role context requires source root and exact context SHA-256')
    resistor_args = (resistor_context, resistor_context_sha256, resistor_source)
    if any(v is not None for v in resistor_args):
        if any(v is None for v in resistor_args):
            raise ValueError('resistor context requires source root and exact context SHA-256')
        if alternative_context is None or any(v is not None for v in (role_context, role_context_sha256, role_source)):
            raise ValueError('common resistor context requires ADC context and owns its qualified role selection')
    if (public_context is None) != (public_context_sha256 is None):
        raise ValueError('public context requires its exact SHA-256')
    if type(final_groups) is not bool:
        raise ValueError('final_groups must be an explicit boolean')
    if addr_tie is not None and addr_tie not in ('GND', 'V3V3'):
        raise ValueError('addr_tie must be GND or V3V3')
    if (evaluation_context is None) != (evaluation_context_sha256 is None):
        raise ValueError('evaluation context requires its independently retained SHA-256')
    if evaluation_context is not None and alternative_context is None:
        raise ValueError('evaluation context requires the same captured alternative context')
    replay = (_evaluation_replay(evaluation_context, evaluation_context_sha256)
              if evaluation_context is not None else None)
    expected_args = (expected_intent, expected_intent_sha256, expected_parts_receipt,
                     expected_parts_receipt_sha256)
    if any(value is not None for value in expected_args):
        if any(value is None for value in expected_args) or replay is None or addr_tie is None:
            raise ValueError('fixed expected intent requires all four pins, captured control context and explicit addr_tie')
        expectation = acquisition_expected_intent.admit(*expected_args, _read_context_file)
    else:
        expectation = None
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
            evaluation_as_of=replay[3] if replay is not None else datetime.now(timezone.utc).isoformat())
    elif alternative_source is not None or alternative_context_sha256 is not None:
        raise ValueError('alternative source/hash require alternative_context')
    resistor = None
    if resistor_context is not None:
        resistor_raw, resistor_stamp = _read_context_file(resistor_context, 'resistor context')
        if spec_record.sha256(resistor_raw) != resistor_context_sha256:
            raise ValueError('resistor context hash mismatch')
        resistor_doc, resistor_captures, resistor_roles = acquisition_resistor.read(
            resistor_source, resistor_raw, selected['value'])
        typed_overlay._disjoint('output', out.resolve(), {'resistor source': Path(resistor_source).resolve(),
            'resistor context': Path(resistor_context).resolve()})
        resistor = dict(path='preparation/resistor-context.json', sha256=resistor_context_sha256,
                        files=resistor_doc['files'])
    public = None
    if public_context is not None:
        public_raw, public_stamp = _read_context_file(public_context, 'public context')
        if spec_record.sha256(public_raw) != public_context_sha256:
            raise ValueError('public context hash mismatch')
        typed_overlay._disjoint('output', out.resolve(), {'public context': Path(public_context).resolve()})
        public = dict(path='preparation/public-context.json', sha256=public_context_sha256)
    build_plan.validate_plan(plan)
    roles = None
    role_args=(role_context,role_context_sha256,role_source)
    if any(v is not None for v in role_args):
        if any(v is None for v in role_args):
            raise ValueError('role context requires source root and exact context SHA-256')
        from . import acquisition_roles, tool_source
        role_raw,role_stamp=_read_context_file(role_context,'role context')
        if spec_record.sha256(role_raw)!=role_context_sha256:
            raise ValueError('role context hash mismatch')
        typed_overlay._disjoint('output',out.resolve(),{'role source':Path(role_source).resolve(),'role context':Path(role_context).resolve()})
        role_doc,role_captures,role_reports=acquisition_roles.read(role_source,role_raw,selected['value'])
        roles=dict(context_sha256=role_context_sha256,files=role_doc['files'],application=role_doc['application'],
            producer_source_sha256=storage.identity(tool_source._files(Path(__file__).parent)))
    overlay_raw, overlay, additions = _load_overlay(before, sources)
    recipe = dict(brief_sha256=spec_record.sha256(raw), plan_sha256=spec_record.sha256(plan_raw),
        source_inventory_sha256=spec_record.sha256(json_bytes(before)),
        overlay_sha256=spec_record.sha256(overlay_raw), producer_sha256=spec_record.sha256(Path(__file__).read_bytes()),
        helpers={module.__name__: spec_record.sha256(Path(module.__file__).read_bytes())
                 for module in (acquisition_expected_intent, acquisition_local_return, acquisition_local_geometry,
                                acquisition_local_routing, acquisition_mechanics, acquisition_connector_geometry, acquisition_source_supplement,
                                acquisition_power_repair, build_plan, catalog, requirement_map, spec_record, storage, typed_overlay)},
        additions={key: spec_record.sha256(json_bytes(files)) for key, files in additions.items()})
    if roles is not None:
        recipe['roles']=roles
    if review_context is not None:
        recipe['review_context'] = review_context
        recipe['helpers'][profiles.__name__] = spec_record.sha256(Path(profiles.__file__).read_bytes())
    if alternative is not None:
        recipe['alternative_context'] = alternative
        recipe['selected_cpl_inventory'] = acquisition_selected_cpl.projected_inventory(
            additions['cpl'], context_doc, context_captures,
            resistor_captures=resistor_captures if resistor is not None else None)
        for module in (acquisition_alternative, acquisition_alternative_context, acquisition_selected_cpl,
                       acquisition_selected_cpl.cpl_rotation, acquisition_selected_cpl.easyeda_footprint,
                       acquisition_selected_cpl.land_pattern,
                       acquisition_alternative.assembly_readiness, acquisition_alternative.part_record,
                       acquisition_alternative.part_selection, acquisition_alternative.parts_manifest,
                       acquisition_alternative_context.jlc_stock_capture):
            recipe['helpers'][module.__name__] = spec_record.sha256(Path(module.__file__).read_bytes())
    supplement_captures = None
    if acquisition_source_supplement.AT in sources:
        supplement_captures = acquisition_source_supplement.read(sources[acquisition_source_supplement.AT])
        recipe['source_supplement_pins_sha256'] = storage.digest(acquisition_source_supplement.PINS)
        recipe['helpers'][acquisition_source_supplement.__name__] = storage.digest(
            Path(acquisition_source_supplement.__file__))
    repair_captures = None
    if acquisition_power_repair.AT in sources:
        if alternative is None or supplement_captures is None:
            raise ValueError('explicit R6 power repair requires captured ADC context and exact source supplement')
        repair_captures = acquisition_power_repair.read(sources[acquisition_power_repair.AT])
        recipe['power_repair_pins_sha256'] = storage.digest(acquisition_power_repair.pin_path(acquisition_power_repair.revision(repair_captures)))
        for module in (acquisition_power_repair, acquisition_power_repair.active_spice):
            recipe['helpers'][module.__name__] = storage.digest(Path(module.__file__))
        for name, captured in acquisition_power_repair.supplier_files(repair_captures).items():
            if name in recipe['selected_cpl_inventory']:
                raise ValueError('R6 supplier capture would overwrite original CPL input')
            recipe['selected_cpl_inventory'][name] = spec_record.sha256(captured)
    catalog_policy = None
    if public is not None:
        base_check = acquisition_alternative._assembly_check(catalog.strict_json((base / 'requirement-map.json').read_bytes()))
        common_catalogs = [catalog.strict_json((base / base_check['params']['catalog']).read_bytes())]
        if alternative is not None:
            common_catalogs.append(catalog.strict_json(context_captures[context_doc['catalog']['path']]))
        if repair_captures is not None:
            common_catalogs.append(catalog.strict_json(repair_captures['catalog/catalog.json']))
        if resistor is not None:
            common_catalogs.append(catalog.strict_json(resistor_captures['catalog/catalog.json']))
        catalog_policy = acquisition_resistor.policy(public_raw, common_catalogs)
        recipe['public_context'] = {**public, 'catalog_policy': catalog_policy}
        recipe['helpers'][acquisition_resistor.__name__] = storage.digest(acquisition_resistor.__file__)
    if resistor is not None:
        from . import acquisition_roles, tool_source
        resistor_choice = acquisition_resistor.choose(catalog_policy)
        role_doc, role_captures, role_reports = resistor_roles[resistor_choice]
        entry = resistor_doc['role_contexts'][resistor_choice]
        role_source = Path(resistor_source) / entry['root']
        role_context = Path(resistor_source) / entry['context']['path']
        role_context_sha256 = entry['context']['sha256']
        role_raw, role_stamp = _read_context_file(role_context, 'selected common role context')
        roles = dict(context_sha256=role_context_sha256, files=role_doc['files'], application=role_doc['application'],
            producer_source_sha256=storage.identity(tool_source._files(Path(__file__).parent)))
        recipe['roles'] = roles
        recipe['resistor_context'] = resistor
        recipe['helpers'][acquisition_resistor.__name__] = storage.digest(acquisition_resistor.__file__)
    if roles is not None and alternative is not None:
        # Both direct roles and the selected resistor cohort have been admitted.
        # Stock changes cannot silently choose a carrier outside that source.
        alternative['required_lcsc'] = acquisition_roles.adc_carrier(role_doc, role_captures)
    if addr_tie is not None:
        recipe['addr_tie'] = addr_tie
    if final_groups:
        from . import acquisition_block_source
        recipe['final_groups'] = dict(path=acquisition_block_source.PATH,
            engineering_source=dict(path=acquisition_block_source.ENGINEERING_PATH,
                sha256=spec_record.sha256(acquisition_block_source.engineering_text())),
            qualification='unverified; existing acquisition allocation and constraints only')
        for module in (acquisition_block_source, acquisition_block_source.connectivity_ir,
                       acquisition_block_source.electrical_locality, acquisition_block_source.parts_manifest,
                       acquisition_block_source.placement_contract, acquisition_block_source.locality_provenance):
            recipe['helpers'][module.__name__] = storage.digest(Path(module.__file__))
    if expectation is not None:
        recipe['expected_intent'] = dict(path=acquisition_expected_intent.EXPECTED, sha256=expected_intent_sha256,
            receipt_sha256=expected_parts_receipt_sha256, receipt_identity=expectation['receipt']['identity'],
            binding=expectation['receipt']['binding'])
    if replay is not None:
        def common(value):
            return {k: v for k, v in value.items() if k not in ('addr_tie', 'evaluation_context', 'expected_intent')}
        if common(replay[0]) != common(recipe):
            raise ValueError('preparation evaluation context differs from the captured recipe/context')
        recipe['evaluation_context'] = dict(report_sha256=evaluation_context_sha256,
            claim='Replay of a captured preparation decision; NOT a fresh stock observation')
    identity = spec_record.sha256(json_bytes(recipe))
    with tempfile.TemporaryDirectory(prefix=out.name + '.prepare-', dir=out.parent) as scratch:
        scratch = Path(scratch)
        revised, bundle = scratch / 'revision', scratch / 'bundle'
        # Captures may be read-only; the owned revision needs writable copies.
        revised.mkdir()
        for name in before:
            target = revised / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / name, target)
        overlay, original_record, record = _write_revision(base, revised, selected, overlay)
        provenance = revised / 'preparation'
        provenance.mkdir()
        (provenance / 'brief.md').write_bytes(raw)
        (provenance / 'build-plan.original.json').write_bytes(plan_raw)
        (provenance / 'overlay.original.json').write_bytes(overlay_raw)
        (provenance / 'source-map.json').write_bytes(json_bytes(_source_map(raw, selected, original_record, record)))
        if resistor is not None:
            (revised / resistor['path']).write_bytes(resistor_raw)
            for name, captured in resistor_captures.items():
                target = revised / acquisition_resistor.AT / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(captured)
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
                revised, context_doc, context_captures, alternative['evaluation_as_of'],
                required_lcsc=alternative.get('required_lcsc'))
            selected_cpl = scratch / 'selected-cpl'
            acquisition_selected_cpl.materialize(sources['cpl'], selected_cpl, context_doc,
                context_captures, recipe['selected_cpl_inventory'], power_repair_captures=repair_captures,
                resistor_captures=resistor_captures if resistor is not None else None)
            sources['cpl'] = selected_cpl
            overlay['added_inputs']['cpl']['files'] = recipe['selected_cpl_inventory']
        repair_report = None
        if repair_captures is not None:
            repair_report = acquisition_power_repair.apply(
                revised, overlay, repair_captures, alternative['evaluation_as_of'])
            (provenance / 'power-repair-pins.json').write_bytes(acquisition_power_repair.pin_path(acquisition_power_repair.revision(repair_captures)).read_bytes())
        supplement_report = None
        if supplement_captures is not None:
            source_files = requirement_map.input_inventory(revised)
            source_files.update({at + '/' + n: h for at, files in additions.items() for n, h in files.items()})
            # A selected purchasing display label may change circuit bytes; the
            # authored calculation source remains captured in the original archive.
            source_files.update({'history/acquisition-preparation-original/' + n: h
                                 for n, h in before.items() if n != INHERITED_REVIEW and source_files.get(n) != h})
            supplement_report = acquisition_source_supplement.apply(
                revised, overlay, supplement_captures, source_files)
            (provenance / 'source-supplement-pins.json').write_bytes(
                acquisition_source_supplement.PINS.read_bytes())
        if repair_report is not None:
            acquisition_power_repair.finalize_geometry(revised, acquisition_power_repair.revision(repair_captures))
            for brief in overlay['judgments'].values():
                brief['questions'] = [q.replace('Power-model correction remains pending;',
                    'Reviewed power-model source correction is admitted; fresh native evaluation remains pending;')
                    for q in brief['questions']]
        if public is not None:
            (revised / public['path']).write_bytes(public_raw)
        resistor_report = None
        public_policy = ({**public, **catalog_policy} if catalog_policy is not None else None)
        if resistor is not None and resistor_choice == acquisition_resistor.ALTERNATIVE:
            resistor_report = acquisition_resistor.apply(revised, resistor_captures, catalog_policy,
                role_source=role_source, role_context=role_raw, capacitance=selected['value'],
                as_of=alternative['evaluation_as_of'], policy_blob=public)
            acquisition_resistor.bind_judgments(overlay, resistor_report)
        operative_parts = acquisition_resistor.parts_manifest.load_manifest(revised, 'parts.json')
        acquisition_resistor.enforce_selected(catalog_policy, operative_parts)
        if resistor is not None:
            acquisition_resistor.reconcile(role_source, role_doc, resistor_choice, operative_parts)
        if public_policy is not None:
            storage.write_json(provenance / 'catalog-policy.json', dict(policy=public_policy,
                affected=[] if resistor_report is None else catalog.strict_json(
                    (revised / resistor_report['decision']['path']).read_bytes())['affected']))
        # Derive against the same serialized overlay the seal consumer reads;
        # added-input object order must not change the predicted map bytes.
        overlay = typed_overlay.load_overlay(json_bytes(overlay))
        if alternative_report is not None:
            selected_closure = acquisition_selected_cpl.finalize(revised, overlay, selected_cpl,
                resistor_selection=resistor_report, public_policy=public_policy)
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
        # Selection evidence must not enter the common purchasing decision inventory.
        expected_report = None
        if expectation is not None:
            _select_addr(revised, 'GND')  # The independent producer's control must remain the admitted GND circuit.
            expected_report = acquisition_expected_intent.bind(revised, plan, catalog.strict_json(replay[1]), overlay, expectation)
        addr_report = _select_addr(revised, addr_tie) if addr_tie is not None else None
        if addr_report is not None:
            (provenance / 'addr-selection.json').write_bytes(json_bytes(addr_report))
        if replay is not None:
            (provenance / 'evaluation-context.json').write_bytes(replay[1])
        geometry_sources = requirement_map.input_inventory(revised)
        geometry_sources.update({at + '/' + name: pin for at, files in additions.items()
                                 for name, pin in files.items()})
        connector_geometry = acquisition_connector_geometry.apply(revised, geometry_sources)
        if final_groups:
            from . import block_source_stage
            acquisition_block_source.apply(revised, plan)
            group_source = acquisition_block_source.derive(revised, plan)
            group_path = acquisition_block_source.PATH
            (revised / group_path).write_bytes(json_bytes(group_source))
            declaration = requirement_map.map_declaration((revised / 'requirement-map.json').read_bytes())
            declaration['scope']['inputs'].append(dict(path=group_path, at=group_path))
            (revised / 'requirement-map.json').write_bytes(json_bytes(declaration))
            overlay['base']['requirement_map_sha256'] = storage.digest(revised / 'requirement-map.json')
            plan['placement']['block_source'] = group_path
            block_source_stage.admit(revised, plan, requirement_map.input_inventory(revised))
        role_report=None
        if roles is not None:
            role_report=acquisition_roles.apply(revised,plan,overlay,role_doc,role_captures)
            (revised/acquisition_roles.AT/'context-input.json').write_bytes(role_raw)
        _isolate_inherited_review(base, revised, overlay, before)
        changed = _archive_revision(base, revised, before, identity)
        ledger_path = provenance / 'edit-ledger.json'
        ledger = catalog.strict_json(ledger_path.read_bytes())
        ledger['connector_geometry'] = connector_geometry
        ledger_path.write_bytes(json_bytes(ledger))
        if resistor_report is not None or public is not None:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = catalog.strict_json(ledger_path.read_bytes())
            if resistor_report is not None:
                ledger['resistor'] = resistor_report
            if public is not None:
                ledger['public_context'] = recipe['public_context']
            ledger_path.write_bytes(json_bytes(ledger))
        if role_report is not None:
            ledger_path=provenance/'edit-ledger.json'
            ledger=json.loads(ledger_path.read_bytes());ledger['roles']=role_report
            ledger_path.write_bytes(json_bytes(ledger))
        if final_groups:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            ledger['final_groups'] = dict(path=group_path, sha256=storage.digest(revised / group_path),
                sources=group_source['eligibility']['sources'], qualification='unverified; native identity remains unbound')
            ledger_path.write_bytes(json_bytes(ledger))
        if addr_report is not None or replay is not None or expected_report is not None:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            if expected_report is not None:
                ledger['expected_intent'] = expected_report
            if addr_report is not None:
                ledger['addr_selection'] = addr_report
            if replay is not None:
                ledger['evaluation_context'] = recipe['evaluation_context']
            ledger_path.write_bytes(json_bytes(ledger))
        if repair_report is not None:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            ledger['power_repair'] = repair_report
            ledger_path.write_bytes(json_bytes(ledger))
        if supplement_report is not None:
            ledger_path = provenance / 'edit-ledger.json'
            ledger = json.loads(ledger_path.read_bytes())
            ledger['source_supplement'] = supplement_report
            ledger_path.write_bytes(json_bytes(ledger))
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
        overlay = _write_final_overlay(overlay_file, overlay)
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
        _refuse_inherited_review(bundle / 'inputs', before[INHERITED_REVIEW])
        acquisition_mechanics.load(bundle / 'inputs/placement-mechanics.json')
        if final_groups:
            sealed = bundle / 'inputs'
            if (catalog.strict_json((sealed / group_path).read_bytes()) != group_source
                    or acquisition_block_source.derive(sealed, plan) != group_source):
                raise ValueError('sealed acquisition final-group source differs from admitted derivation')
            block_source_stage.admit(sealed, plan, requirement_map.input_inventory(sealed))
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
                       ('Native strict final-group and artwork/package revalidation' if final_groups else
                        'Strict block-source selection and final artwork/package revalidation if strict grouping is requested'),
                       'Supplier and physical validation'])
        if expected_report is not None:
            report['expected_intent'] = expected_report
        if addr_report is not None:
            report['addr_selection'] = addr_report
        if alternative_report is not None:
            report['alternative'] = dict(selected=alternative_report['selected'],
                decision=alternative_report['decision'], catalog=alternative_report['catalog'],
                context=alternative)
        if repair_report is not None:
            report['power_repair'] = repair_report
        if role_report is not None:
            report['roles']=role_report
        if resistor_report is not None:
            report['resistor'] = resistor_report
        if public is not None:
            report['public_context'] = recipe['public_context']
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
            _, current_captures = acquisition_alternative_context.read(alternative_source, context_raw)
            if current_captures != context_captures:
                raise ValueError('alternative source changed during preparation')
            for name, captured in context_captures.items():
                if (bundle / 'inputs' / acquisition_alternative_context.AT / name).read_bytes() != captured:
                    raise ValueError('captured alternative source changed before publication')
            if (bundle / 'inputs' / alternative['path']).read_bytes() != context_raw:
                raise ValueError('captured alternative context changed before publication')
        if supplement_captures is not None:
            if (storage.digest(acquisition_source_supplement.PINS) != recipe['source_supplement_pins_sha256']
                    or acquisition_source_supplement.read(sources[acquisition_source_supplement.AT]) != supplement_captures):
                raise ValueError('source supplement changed during preparation')
            for name, captured in supplement_captures.items():
                if (bundle / 'inputs' / acquisition_source_supplement.AT / name).read_bytes() != captured:
                    raise ValueError('sealed source supplement differs from admitted bytes')
        if repair_captures is not None:
            if (storage.digest(acquisition_power_repair.pin_path(acquisition_power_repair.revision(repair_captures))) != recipe['power_repair_pins_sha256']
                    or acquisition_power_repair.read(sources[acquisition_power_repair.AT]) != repair_captures):
                raise ValueError('power repair source changed before publication')
            for name, captured in repair_captures.items():
                if (bundle / 'inputs' / acquisition_power_repair.AT / name).read_bytes() != captured:
                    raise ValueError('sealed power repair source differs from admitted capture')
        if replay is not None and _read_context_file(evaluation_context, 'preparation evaluation context') != (replay[1], replay[2]):
            raise ValueError('preparation evaluation context changed before publication')
        if expectation is not None:
            current = acquisition_expected_intent.admit(*expected_args, _read_context_file)
            if current != expectation:
                raise ValueError('expected intent or receipt changed before publication')
        if roles is not None:
            if _read_context_file(role_context,'role context')!=(role_raw,role_stamp):
                raise ValueError('role context changed during preparation')
            if acquisition_roles.read(role_source,role_raw,selected['value'])!=(role_doc,role_captures,role_reports):
                raise ValueError('role source qualification changed during preparation')
            for name,raw_capture in role_captures.items():
                if (bundle/'inputs'/acquisition_roles.AT/name).read_bytes()!=raw_capture:
                    raise ValueError('sealed role capture differs from admitted source')
        if public is not None:
            if _read_context_file(public_context, 'public context') != (public_raw, public_stamp):
                raise ValueError('public context changed before publication')
            if (bundle / 'inputs' / public['path']).read_bytes() != public_raw:
                raise ValueError('sealed public context differs')
        if resistor is not None:
            if _read_context_file(resistor_context, 'resistor context') != (resistor_raw, resistor_stamp):
                raise ValueError('resistor context changed before publication')
            if acquisition_resistor.read(resistor_source, resistor_raw, selected['value']) != (resistor_doc, resistor_captures, resistor_roles):
                raise ValueError('common resistor evidence or source qualification changed')
            for name, captured in resistor_captures.items():
                if (bundle / 'inputs' / acquisition_resistor.AT / name).read_bytes() != captured:
                    raise ValueError('sealed resistor capture differs')
        rename_noreplace(bundle, out)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief', type=Path, required=True)
    parser.add_argument('--build-plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--review-profile', type=Path, help='explicit full engineering review profile to capture')
    parser.add_argument('--role-context',type=Path,help='pinned finite acquisition v4 role context JSON')
    parser.add_argument('--role-context-sha256')
    parser.add_argument('--role-source',type=Path,help='root of immutable role context files')
    parser.add_argument('--resistor-context', type=Path, help='common exact Panasonic evidence and qualified role pool')
    parser.add_argument('--resistor-context-sha256')
    parser.add_argument('--resistor-source', type=Path)
    parser.add_argument('--public-context', type=Path, help='public agent-ee.case-context/1 policy, separate from observed stock')
    parser.add_argument('--public-context-sha256')
    parser.add_argument('--final-groups', action='store_true', help='derive strict final-group source from the captured acquisition allocation')
    parser.add_argument('--verification-timeout-s', type=int,
                        help='explicit build verification budget; requires --review-profile, no automatic increase')
    parser.add_argument('--alternative-context', type=Path, help='pinned finite acquisition carrier context JSON')
    parser.add_argument('--alternative-source', type=Path, help='read-only root of the context file closure')
    parser.add_argument('--alternative-context-sha256', help='exact SHA-256 of the admitted context JSON')
    parser.add_argument('--addr-tie', choices=('GND','V3V3'), help='finite U2 ADDR pin1 source tie; V3V3 is a development defect')
    parser.add_argument('--evaluation-context', type=Path, help='prior preparation.json to replay the captured evaluation decision')
    parser.add_argument('--evaluation-context-sha256', help='independently retained digest of that prior preparation report')
    parser.add_argument('--expected-intent', type=Path, help='exact output of an independently captured control parts receipt')
    parser.add_argument('--expected-intent-sha256')
    parser.add_argument('--expected-parts-receipt', type=Path, help='root-pinned ordinary control receipt.json')
    parser.add_argument('--expected-parts-receipt-sha256')
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
                         alternative_context_sha256=args.alternative_context_sha256,
                         addr_tie=args.addr_tie, evaluation_context=args.evaluation_context,
                         evaluation_context_sha256=args.evaluation_context_sha256,
                         expected_intent=args.expected_intent, expected_intent_sha256=args.expected_intent_sha256,
                         expected_parts_receipt=args.expected_parts_receipt,
                         expected_parts_receipt_sha256=args.expected_parts_receipt_sha256,
                         final_groups=args.final_groups,role_context=args.role_context,
                         role_context_sha256=args.role_context_sha256,role_source=args.role_source,
                         resistor_context=args.resistor_context, resistor_context_sha256=args.resistor_context_sha256,
                         resistor_source=args.resistor_source, public_context=args.public_context,
                         public_context_sha256=args.public_context_sha256)
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
