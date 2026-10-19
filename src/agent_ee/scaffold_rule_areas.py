"""Explicit captured CAD retention for scaffold generation, never qualification."""
import hashlib
from pathlib import PurePosixPath

from .catalog import exact_fields, nonempty, strict_json
from .footprint_library import (ESP32_FOOTPRINT_ID, _members, antenna_placement_context,
                                probe_rule_areas, validate_declarations)
from .parts_manifest import _blob, read_captured
from .symbol_library import nanometres

SCHEMA = 'agent-ee.captured-rule-areas/1'


def load_retention(inputs, path, envelope_rel, parts_rel, envelope, document):
    """Recompute records from captured bytes; caller already qualified parts/CAD closure."""
    dependencies = {}

    def read(rel):
        raw = read_captured(inputs, rel)
        pin = hashlib.sha256(raw).hexdigest()
        if rel in dependencies and dependencies[rel] != pin:
            raise ValueError('captured rule-area dependency changed: ' + rel)
        dependencies[rel] = pin
        return raw

    def blob(value):
        _blob(value)
        raw = read(value['path'])
        if dependencies[value['path']] != value['sha256']:
            raise ValueError('captured rule-area dependency hash mismatch: ' + value['path'])
        return raw

    record = strict_json(read(path))
    exact_fields(record, ('schema', 'purpose', 'envelope', 'parts', 'items'))
    if record['schema'] != SCHEMA or record['purpose'] != 'retain-captured-cad-only':
        raise ValueError('explicit captured CAD retention purpose required')
    for key, expected in (('envelope', envelope_rel), ('parts', parts_rel)):
        blob(record[key])
        if record[key]['path'] != expected:
            raise ValueError('captured rule-area ' + key + ' differs from writer input')
    areas = {a['id']: a for a in envelope['keepouts'] if 'antenna' in a}
    fixed = {f['reference']: f for f in envelope['fixed_footprints']}
    if not isinstance(record['items'], list) or not record['items']:
        raise ValueError('nonempty explicit retained rule-area items required')
    members = _members(strict_json(blob(document['cad']['manifest'])))
    declarations = (validate_declarations(strict_json(blob(document['cad']['declarations'])))[0]
                    if 'declarations' in document['cad'] else None)
    items, references = {}, set()
    for item in record['items']:
        exact_fields(item, ('keepout_id', 'reference', 'footprint', 'pose', 'source',
                            'rule_areas', 'policy', 'context'))
        area_id = nonempty(item['keepout_id'])
        ref = nonempty(item['reference'])
        if area_id in items or area_id not in areas or ref in references:
            raise ValueError('duplicate or undeclared dynamic rule-area item')
        if areas[area_id]['antenna']['reference'] != ref or ref not in fixed:
            raise ValueError('retained rule area requires the exact fixed antenna reference')
        exact_fields(item['pose'], ('anchor_mm', 'rotation_deg', 'side'))
        pose = {k: fixed[ref][k] for k in item['pose']}
        if item['pose'] != pose:
            raise ValueError('retained rule-area pose differs from fixed envelope pose')
        if pose['side'] != 'front' or nanometres(pose['rotation_deg']) != 0:
            raise ValueError('captured antenna retention currently supports front zero-degree pose only')
        parts = [p for p in document['parts'] for i in p['instances'] if i['reference'] == ref]
        if len(parts) != 1 or parts[0]['footprint'] != item['footprint'] or item['footprint'] != ESP32_FOOTPRINT_ID:
            raise ValueError('retained rule-area part/footprint membership differs')
        matching = [m for lib, m in members
                    if lib + ':' + PurePosixPath(m['path']).stem == item['footprint']]
        if len(matching) != 1 or matching[0] != item['source']:
            raise ValueError('retained rule-area CAD member is missing, ambiguous or different')
        raw = blob(item['source'])
        probe = probe_rule_areas(raw, library='RF_Module', path=item['source']['path'], declared=declarations)
        if item['rule_areas'] != probe or len(probe['rule_areas']) != 1:
            raise ValueError('retained rule-area probe differs or source zone is missing/ambiguous')
        source_area = probe['rule_areas'][0]
        if (set(source_area['layers']) & set(envelope['copper_layers']) != set(areas[area_id]['layers'])
                or set(source_area['forbid']) != set(areas[area_id]['forbid'])):
            raise ValueError('retained rule-area layers or forbid flags differ from envelope')
        # Context validates its exact fields, case, locator, source pin and source bytes.
        policy = item['policy']
        exact_fields(policy, ('schema', 'part_number', 'placement_case', 'source'))
        exact_fields(policy['source'], ('path', 'sha256', 'page', 'locator'))
        context = antenna_placement_context(policy, {policy['source']['path']: read(policy['source']['path'])})
        if policy['placement_case'] != 'outside_board' or item['context'] != context:
            raise ValueError('retained rule-area context must match the unknown outside-board policy')
        items[area_id] = item
        references.add(ref)
    if set(items) != set(areas):
        raise ValueError('explicit retention must cover every dynamic antenna item')
    return dict(items=items, dependencies=dependencies)


def verify_native_retention(board, items, inputs, *, copper_layers):
    """Compare saved native zones to separately loaded captured CAD at the same pose.

    This is generation fidelity only. No manufacturer minimum or outside-board
    acceptance is inferred from the source zone or the declared transform.
    """
    import pcbnew
    from .placement_probe import rule_area
    enabled = sorted(pcbnew.LayerName(layer) for layer in board.GetEnabledLayers().CuStack())
    if enabled != sorted(copper_layers):
        raise ValueError('saved native copper layers differ from the captured envelope')

    def geometry(fp):
        result = []
        for zone in fp.Zones():
            if not zone.GetIsRuleArea():
                raise ValueError('retained footprint contains a non-rule zone')
            area = rule_area(zone, 'captured-footprint')
            del area['uuid']
            if area['placement_area'] or area['outline']['status'] != 'proven':
                raise ValueError('retained rule-area native geometry unsupported')
            result.append(area)
        return result

    for item in items.values():
        matches = [fp for fp in board.GetFootprints() if fp.GetReference() == item['reference']]
        if len(matches) != 1:
            raise ValueError('retained native footprint missing or ambiguous')
        fp = matches[0]
        pose = item['pose']
        point = pcbnew.VECTOR2I(*(nanometres(x) for x in pose['anchor_mm']))
        if (fp.GetFPIDAsString() != item['footprint'] or fp.IsFlipped()
                or fp.GetOrientationDegrees() != 0 or fp.GetPosition() != point):
            raise ValueError('retained native footprint identity or pose differs')
        path = PurePosixPath(item['source']['path'])
        raw = read_captured(inputs, str(path))
        if hashlib.sha256(raw).hexdigest() != item['source']['sha256']:
            raise ValueError('retained CAD changed during native generation')
        expected = pcbnew.FootprintLoad(str(inputs / path.parent), path.stem)
        if expected is None:
            raise ValueError('retained CAD failed native load')
        expected.SetPosition(point)
        actual, baseline = geometry(fp), geometry(expected)
        # KiCad serializes a library's all-copper zone onto enabled board layers.
        # Keep the raw probe unchanged; only this expected native applicability
        # is projected. Never filter the actual saved zone or its restrictions.
        for area in baseline:
            area['layers'] = sorted(set(area['layers']) & set(enabled))
        if len(actual) != 1 or actual != baseline:
            raise ValueError('saved native rule area differs from captured CAD geometry')
