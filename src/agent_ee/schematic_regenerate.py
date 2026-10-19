"""Update an owned flat wire-free emission using captured prior intent and bytes.

The emitter owns symbol geometry, resolution and serialization. This module owns
correspondence, three-way field updates and placement continuity. No input is
mutated; a refusal returns a diagnostic report and no files.
"""
import copy
import hashlib
import re
import uuid
from pathlib import Path

from . import schematic_emitter as emitter
from .connectivity_ir import _json_bytes, _read_json, _validate_flat_intent, resolve_intent
from .design_diff import compare
from .symbol_library import LibraryError, ParsedLibraries, children

SCHEMA = 'agent-ee.schematic-regeneration/1'
PREFIX = re.compile(r'#?[A-Za-z_]{1,16}')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _input_sha(value):
    return _sha(value.encode('utf-8') if isinstance(value, str) else
                value if isinstance(value, bytes) else _json_bytes(value))


def _one(node, tag):
    found = children(node, tag)
    if len(found) != 1:
        raise emitter.EmissionError('previous_malformed', 'expected exactly one ' + tag)
    return found[0]


def _properties(symbol):
    props = children(symbol, 'property')
    result = {p[1]: p for p in props if len(p) >= 3}
    if len(result) != len(props) or not {'Reference', 'Value', 'Footprint'} <= result.keys():
        raise emitter.EmissionError('previous_malformed', 'duplicate or missing instance fields')
    return result


def _symbols(tree):
    result = {}
    for symbol in children(tree, 'symbol'):
        key = (_properties(symbol)['Reference'][2], _one(symbol, 'unit')[1])
        if key in result:
            raise emitter.EmissionError('previous_malformed', 'duplicate reference/unit')
        result[key] = symbol
    return result


def _finalize(raw, previous=None):
    raw = copy.deepcopy(raw)
    previous = {c['id']: c['reference'] for c in (previous or {}).get('components', [])}
    reserved = set(previous.values()) | {c['reference'] for c in raw['components']
                                        if emitter.REFERENCE.fullmatch(c['reference'])}
    for comp in sorted(raw['components'], key=lambda c: c['id']):
        prefix = comp['reference']
        if not PREFIX.fullmatch(prefix):
            continue
        prior = previous.get(comp['id'])
        if prior and re.fullmatch(re.escape(prefix) + r'[0-9]{1,6}', prior):
            comp['reference'] = prior
        else:
            number = 1
            while prefix + str(number) in reserved:
                number += 1
            comp['reference'] = prefix + str(number)
            reserved.add(comp['reference'])
    return raw


def _signature(resolved, comp):
    pins = {}
    for net in resolved['nets']:
        for term in net['terminals']:
            if term['component'] == comp['id']:
                pins[term['pin']] = ('net', net['name'])
    for term in resolved['no_connects']:
        if term['component'] == comp['id']:
            pins[term['pin']] = ('no_connect',)
    return comp['lib_id'], tuple(comp['units']), tuple(sorted(pins.items()))


def _match(before, after):
    old = {c['id']: c for c in before['components']}
    available, matches = set(old), {}
    for comp in after['components']:
        refs = [cid for cid in available if old[cid]['reference'] == comp['reference']]
        if refs:
            matches[comp['id']] = refs[0]
            available.remove(refs[0])
    # Do not break ties using order or value; that would silently exchange bindings.
    proposals = {}
    for comp in after['components']:
        if comp['id'] in matches:
            continue
        candidates = [cid for cid in available if _signature(before, old[cid]) == _signature(after, comp)]
        if len(candidates) > 1:
            raise emitter.EmissionError('ambiguous_match', 'connectivity matches multiple prior components',
                                        '/components/' + comp['id'])
        if candidates:
            prior = candidates[0]
            if prior in proposals.values():
                raise emitter.EmissionError('ambiguous_match', 'multiple new components match one prior component')
            proposals[comp['id']] = prior
    matches.update(proposals)
    available -= set(proposals.values())
    # A simultaneous explicit rename and wiring repair can change the whole
    # signature. The captured logical ID still proves continuity when neither
    # reference nor connectivity claimed that prior component.
    for comp in after['components']:
        cid = comp['id']
        if cid not in matches and cid in available:
            matches[cid] = cid
            available.remove(cid)
    return matches


def _transform(symbol):
    at = _one(symbol, 'at')
    if len(at) != 4 or at[3] not in ('0', '90', '180', '270'):
        raise emitter.EmissionError('previous_malformed', 'unsupported instance transform')
    mirrors = children(symbol, 'mirror')
    if len(mirrors) > 1 or (mirrors and mirrors[0][1:] not in (['x'], ['y'])):
        raise emitter.EmissionError('previous_malformed', 'unsupported mirror')
    return (emitter._nm(at[1]), emitter._nm(at[2])), int(at[3]), mirrors[0][1] if mirrors else None


def _planned(raw, captures, layout):
    receipt, files = emitter.emit_schematic(raw, captures, layout)
    if files is None:
        raise emitter.EmissionError('emission_rejected', str(receipt['findings']))
    resolved = resolve_intent(raw, captures)
    namespace, project, transforms, presentation, _ = emitter._validate_layout(
        layout, {c['id']: c for c in resolved['components']})
    if presentation != 'rows':  # AGENTEE-222 wires and frames are not retained here.
        raise emitter.EmissionError('layout_schema', 'stable regeneration supports the rows presentation only')
    units, globals_ = emitter._plan(resolved, captures, raw['libraries'], transforms, ParsedLibraries(), {})
    return resolved, units, globals_, namespace, project, files


def _validate_uuids(tree):
    # UUIDs may be assigned by KiCad, but must remain unique native identities.
    ids = []
    def visit(node):
        if node[:1] == ['uuid']:
            if len(node) != 2 or str(uuid.UUID(node[1])) != node[1]:
                raise emitter.EmissionError('previous_malformed', 'invalid UUID')
            ids.append(node[1])
        for child in node:
            if isinstance(child, list):
                visit(child)
    visit(tree)
    if len(ids) != len(set(ids)):
        raise emitter.EmissionError('previous_malformed', 'duplicate UUID')


def _validate_previous(data, baseline, units, project):
    actual = emitter._scan(data)
    if actual[0] != 'kicad_sch':
        raise emitter.EmissionError('previous_malformed', 'previous candidate is not a schematic')
    _validate_uuids(actual)
    symbols, original = _symbols(actual), _symbols(emitter._scan(baseline))
    if symbols.keys() != original.keys():
        raise emitter.EmissionError('human_edit_conflict', 'prior intent and candidate reference/unit inventory differ')
    root = _one(actual, 'uuid')[1]
    if _one(actual, 'paper')[1] not in {p[0] for p in emitter.PAPERS}:
        raise emitter.EmissionError('previous_malformed', 'unsupported paper')
    transforms = {}
    for item in units:
        comp, unit = item['comp'], item['unit']
        key = (comp['reference'], str(unit))
        symbol = symbols[key]
        _, rotation, mirror = _transform(symbol)
        transform = dict(rotation=rotation, mirror=mirror)
        if comp['id'] in transforms and transforms[comp['id']] != transform:
            raise emitter.EmissionError('unsupported_unit_transform', 'units of one component need the same orientation')
        transforms[comp['id']] = transform
        path = _one(_one(symbol, 'instances'), 'project')
        if path[1] != project:
            raise emitter.EmissionError('previous_malformed', 'invalid project instance')
        occurrence = _one(path, 'path')
        if occurrence[1] != '/' + root or _one(occurrence, 'reference')[1] != comp['reference']:
            raise emitter.EmissionError('human_edit_conflict', 'instance path/reference disagrees with candidate')
    return actual, symbols, original, transforms


def _merge_fields(old, actual, new):
    before, human, proposed = _properties(old), _properties(actual), _properties(new)
    merged = {}
    for name in sorted(before.keys() | human.keys() | proposed.keys()):
        b, h, n = before.get(name), human.get(name), proposed.get(name)
        text = lambda p: p[2] if p is not None else None
        if text(n) == text(b):
            chosen = h
        elif text(h) not in (text(b), text(n)):
            raise emitter.EmissionError('human_edit_conflict', 'both human and intent changed field ' + name,
                                        '/symbols/' + before['Reference'][2] + '/' + name)
        else:
            chosen = copy.deepcopy(h if h is not None else n)
            if chosen is not None and n is not None:
                chosen[2] = n[2]
            elif n is None:
                chosen = None
        if chosen is not None:
            merged[name] = copy.deepcopy(chosen)
    result = []
    for node in copy.deepcopy(actual):
        if isinstance(node, list) and node[:1] == ['property']:
            if node[1] in merged:
                result.append(merged.pop(node[1]))
        elif isinstance(node, list) and node[:1] == ['instances']:
            result.append(copy.deepcopy(_one(new, 'instances')))
        else:
            result.append(node)
    position = next(i for i, n in enumerate(result) if isinstance(n, list) and n[:1] in (['pin'], ['instances']))
    result[position:position] = merged.values()
    return result


def _rect(item, anchor, symbol=None):
    x0, y0, x1, y1 = item['box']
    box = [anchor[0] + x0, anchor[1] + y0, anchor[0] + x1, anchor[1] + y1]
    if symbol is not None:
        for prop in children(symbol, 'property'):
            if ['hide', 'yes'] in prop or any('hide' in e or ['hide', 'yes'] in e for e in children(prop, 'effects')):
                continue
            for at in children(prop, 'at'):
                x, y = emitter._nm(at[1]), emitter._nm(at[2])
                span = (len(prop[2]) + 1) * emitter.FONT
                box = [min(box[0], x - span), min(box[1], y - span),
                       max(box[2], x + span), max(box[3], y + span)]
    return box


def _place_new(item, occupied):
    x0, y0, x1, y1 = item['box']
    for _, width, height in emitter.PAPERS:
        for y in range(emitter.BORDER, height - emitter.TITLE_RESERVE - emitter.BORDER - (y1 - y0), emitter.GRID):
            for x in range(emitter.BORDER, width - emitter.BORDER - (x1 - x0), emitter.GRID):
                anchor = (-(-(x - x0) // emitter.GRID) * emitter.GRID,
                          -(-(y - y0) // emitter.GRID) * emitter.GRID)
                box = _rect(item, anchor)
                if box[2] > width - emitter.BORDER or box[3] > height - emitter.TITLE_RESERVE - emitter.BORDER:
                    continue
                if all(box[2] + emitter.GAP <= b[0] or b[2] + emitter.GAP <= box[0] or
                       box[3] + emitter.GAP <= b[1] or b[3] + emitter.GAP <= box[1] for b in occupied):
                    return anchor
    raise emitter.EmissionError('paper_overflow', 'no collision-free cell for added component')


def regenerate_schematic(previous_schematic, previous_intent, intent, captured_libraries, layout):
    """Pure flat update; return (hash-bound diagnostic report, files or None).

    ``previous_intent`` is the last adopted ``effective_intent``. Captured libraries
    and layout are immutable across this update; changed captures fail closed.
    """
    result = dict(schema=SCHEMA, execution='failed', outcome='unknown', findings=[], changes=[],
                  previous_schematic_sha256=None, previous_intent_sha256=None,
                  intent_sha256=None, layout_sha256=None, inputs={}, diff=None, effective_intent=None, files=[],
                  implementation=dict(module=__name__, sha256=_sha(Path(__file__).read_bytes())))
    try:
        result['previous_schematic_sha256'] = _sha(previous_schematic)
        result['inputs'] = dict(previous_intent=_input_sha(previous_intent), intent=_input_sha(intent),
                                layout=_input_sha(layout))
        previous_raw, raw, layout = map(_read_json, (previous_intent, intent, layout))
        result.update(previous_intent_sha256=_sha(_json_bytes(previous_raw)), intent_sha256=_sha(_json_bytes(raw)),
                      layout_sha256=_sha(_json_bytes(layout)))
        if not isinstance(previous_raw, dict) or not isinstance(raw, dict):
            raise emitter.EmissionError('regeneration_input', 'intent must be an object')
        if previous_raw['schema'] != 'agent-ee.connectivity-intent/1' or raw['schema'] != previous_raw['schema']:
            raise emitter.EmissionError('unsupported_hierarchy', 'regeneration currently requires flat intent/1')
        _validate_flat_intent(previous_raw)
        _validate_flat_intent(raw)
        before = _finalize(previous_raw)
        after = _finalize(raw, before)
        old_res, old_units, _, namespace, project, old_files = _planned(before, captured_libraries, layout)
        filename = project + '.kicad_sch'
        tree, actual, original, transforms = _validate_previous(previous_schematic, old_files[filename], old_units,
                                                               project)
        # Recreate the old emission in the human's transforms; only fields and
        # identities may differ. Any wire, label, library or flag edit is reported.
        old_layout = {**layout, 'transforms': transforms}
        _, _, _, _, _, transformed = _planned(before, captured_libraries, old_layout)
        expected = emitter._scan(transformed[filename])
        expected_symbols = _symbols(expected)
        for key, node in expected_symbols.items():
            anchor, _, _ = _transform(actual[key])
            initial, _, _ = _transform(node)
            dx, dy = anchor[0] - initial[0], anchor[1] - initial[1]
            # Shift this unit and its generated pin decorations together.
            comp = next(c for c in old_res['components'] if c['reference'] == key[0])
            unit = int(key[1])
            owned = {emitter._uuid(namespace, kind, comp['id'], unit, p['number'])
                     for kind in ('label', 'global_label', 'no_connect') for p in comp['pins']}
            for decoration in expected[1:]:
                if isinstance(decoration, list) and children(decoration, 'uuid') and emitter._item_uuid(decoration) in owned:
                    for at in children(decoration, 'at'):
                        at[1:3] = [emitter._mm(emitter._nm(at[1]) + dx), emitter._mm(emitter._nm(at[2]) + dy)]
                    for prop in children(decoration, 'property'):
                        for at in children(prop, 'at'):
                            at[1:3] = [emitter._mm(emitter._nm(at[1]) + dx), emitter._mm(emitter._nm(at[2]) + dy)]
            _one(node, 'at')[1:3] = [emitter._mm(anchor[0]), emitter._mm(anchor[1])]
            node[:] = [x for x in node if not (isinstance(x, list) and x[:1] == ['property'])] + children(actual[key], 'property')
        _one(expected, 'paper')[1] = _one(tree, 'paper')[1]
        expected_bytes = ('\n'.join(emitter._dump(expected)) + '\n').encode()
        conflict = compare(expected_bytes, previous_schematic, 'schematic', 6)
        if not conflict['equivalent']:
            result['conflict_diff'] = conflict
            raise emitter.EmissionError('human_edit_conflict', 'candidate has edits outside retained transforms, UUIDs and fields')
        current_ids = {c['id'] for c in after['components']}
        current_layout = {**layout, 'transforms': {cid: t for cid, t in layout.get('transforms', {}).items()
                                                if cid in current_ids}}
        new_res = _planned(after, captured_libraries, current_layout)[0]
        matches = _match(old_res, new_res)
        old_by_id = {c['id']: c for c in old_res['components']}
        raw_by_id = {c['id']: c for c in raw['components']}
        for comp in after['components']:
            if comp['id'] in matches and PREFIX.fullmatch(raw_by_id[comp['id']]['reference']):
                comp['reference'] = old_by_id[matches[comp['id']]]['reference']
        new_transforms = {cid: transforms[old] for cid, old in matches.items()}
        new_layout = {**current_layout, 'transforms': {**current_layout['transforms'], **new_transforms}}
        _, units, globals_, _, _, files = _planned(after, captured_libraries, new_layout)
        root = _one(tree, 'uuid')[1]
        endpoints, libs, symbols, labels, markers, occupied = {}, {}, [], [], [], []
        # Reserve all retained units before looking for any new cell.
        for item in sorted(units, key=lambda i: i['comp']['id'] not in matches):
            comp, unit = item['comp'], item['unit']
            cid = comp['id']
            if cid in matches:
                old = old_by_id[matches[cid]]
                if old['lib_id'] != comp['lib_id'] or old['units'] != comp['units']:
                    raise emitter.EmissionError('incompatible_match', 'matched symbol library identity or units changed')
                key = old['reference'], str(unit)
                anchor = _transform(actual[key])[0]
            else:
                prototype, _, _ = emitter._symbol(item, (0, 0), namespace, (cid,), project,
                                                  '/' + root, {}, globals_)
                anchor = _place_new({**item, 'box': _rect(item, (0, 0), prototype)}, occupied)
            symbol, unit_labels, unit_markers = emitter._symbol(item, anchor, namespace, (cid,), project,
                                                                '/' + root, endpoints, globals_)
            if cid in matches:
                old = old_by_id[matches[cid]]
                key = old['reference'], str(unit)
                symbol = _merge_fields(original[key], actual[key], symbol)
                action = 'renamed' if old['reference'] != comp['reference'] else 'kept'
                result['changes'].append(dict(action=action, before=old['reference'], after=comp['reference'], unit=unit))
            else:
                result['changes'].append(dict(action='added', before=None, after=comp['reference'], unit=unit))
            libs[comp['lib_id']] = item['embedded']
            occupied.append(_rect(item, anchor, symbol))
            symbols.append(symbol)
            labels += unit_labels
            markers += unit_markers
        for old in old_res['components']:
            if old['id'] not in matches.values():
                result['changes'].append(dict(action='removed', before=old['reference'], after=None))
        # Keep the prior paper unless the added cells need a larger one.
        start = next(i for i, p in enumerate(emitter.PAPERS) if p[0] == _one(tree, 'paper')[1])
        paper = None
        for name, width, height in emitter.PAPERS[start:]:
            if all(b[2] <= width - emitter.BORDER and b[3] <= height - emitter.TITLE_RESERVE - emitter.BORDER for b in occupied):
                paper = name
                break
        if paper is None:
            raise emitter.EmissionError('paper_overflow', 'retained placement exceeds supported paper')
        files[filename] = emitter._document(root, paper, libs, markers, labels, symbols, root=True)
        _validate_uuids(emitter._scan(files[filename]))
        files[project + '.kicad_pro'] = emitter._project_file(project, root, [[root, 'Root']])
        result.update(execution='ok', outcome='info', effective_intent=after,
                      diff=compare(previous_schematic, files[filename], 'schematic', 6),
                      files=[dict(path=p, sha256=_sha(data)) for p, data in sorted(files.items())])
    except (emitter.EmissionError, LibraryError, ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        result['changes'] = []
        result['findings'].append(dict(code=getattr(exc, 'code', 'regeneration_input'),
                                       location=getattr(exc, 'location', '/'), message=str(exc)))
        files = None
    result['canonical_sha256'] = _sha(_json_bytes(result))
    return result, files
