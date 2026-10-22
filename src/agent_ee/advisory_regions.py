"""Named advisory placement regions: reported, never searched (AGENTEE-436).

A packing policy may declare ``advisory_regions``. Search, capacity proofs and
the judge never read them, so an advisory region can never prune a candidate
pose. Each region is graded on a complete placement: the parts whose courtyards
intrude into it (positive-area overlap on the region's side) and the parts within
its ``proximity_nm`` (exact courtyard edge distance), each marked fixed or not.

A region is either absolute geometry (``side`` and ``polygons``, such as the
pod's IMU box and ring) or the pose-relative shadow of a footprint
(``shadow_of``: the owner's mounting-side courtyard box grown by ``margin_nm``
on the opposite side, exactly the block-arrangement ``no_stacking`` shadow). A
shadow is re-derived from the owner's actual pose on every grading and records
the owner's identity, pose and courtyard box, so it cannot go stale the way a
copied obstacle polygon did (the pod's iteration-5 L1/L2 shadows).

``source.class`` records where the region comes from: ``owner``, ``datasheet``,
``manufacturer`` or ``agent-advisory``. It is a declared label, authenticated
nowhere (AGENTEE-348), and never makes a region hard: a sourced keep-out reaches
the board as a native rule area (AGENTEE-365). A finding is review input with
status ``info``; it is never a pass of a physical requirement nor a defect.
Installed-height evidence (``annotate``) only describes intruding and nearby
bodies; with no sourced height or mass threshold their tall/heavy risk stays
``unresolved`` for physical review. A sourced part height (``agent-ee.sourced-heights/1``,
such as a captured datasheet's) is bound by reference and always takes precedence:
a native model probe that cannot measure the body never replaces it.

Grading refuses a capture it cannot qualify: schema, board hash, unique
reference and UUID identities, a mounting-side courtyard on every part and a
proven courtyard on every side. Missing evidence is never graded as clear.
"""
import argparse
import copy
import json
import math
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import placer
from .placement import SHA256_HEX, courtyard_bounds, validate_capture
from .catalog import strict_json
from .storage import digest, write_json


SCHEMA = 'agent-ee.advisory-regions/1'
SOURCE_CLASSES = ('owner', 'datasheet', 'manufacturer', 'agent-advisory')
HEIGHTS = 'agent-ee.component-height-probe/1'
SOURCED_HEIGHTS = 'agent-ee.sourced-heights/1'
_KEYS = {'id', 'source', 'proximity_nm', 'concern', 'subject', 'side', 'polygons', 'shadow_of'}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value


def _source(value, label):
    if not isinstance(value, dict) or value.get('class') not in SOURCE_CLASSES:
        raise ValueError(f'{label} source class must be one of {", ".join(SOURCE_CLASSES)}')
    # An agent rule states its reason; anything attributed to an owner or document cites it.
    field = 'reason' if value['class'] == 'agent-advisory' else 'citation'
    if set(value) != {'class', field}:
        raise ValueError(f'{label} {value["class"]} source needs exactly class and {field}')
    _text(value[field], f'{label} source {field}')


def validate(regions, refs):
    """Shape-check declared advisory regions against the board references; returns them."""
    if not isinstance(regions, list) or len(regions) > 64:
        raise ValueError('advisory_regions must be a list of at most 64 regions')
    seen = set()
    for region in regions:
        if not isinstance(region, dict) or set(region) - _KEYS:
            raise ValueError('advisory region declares only ' + ', '.join(sorted(_KEYS)))
        rid = _text(region.get('id'), 'advisory region id')
        if rid in seen:
            raise ValueError(f'duplicate advisory region {rid}')
        seen.add(rid)
        label = f'advisory region {rid}'
        _source(region.get('source'), label)
        if type(region.get('proximity_nm')) is not int or region['proximity_nm'] < 0:
            raise ValueError(f'{label} proximity_nm must be a nonnegative integer')
        if 'concern' in region:
            _text(region['concern'], f'{label} concern')
        if 'subject' in region and region['subject'] not in refs:
            raise ValueError(f'{label} subject must be a board reference')
        if 'shadow_of' in region:
            if 'side' in region or 'polygons' in region or 'subject' in region:
                raise ValueError(f'{label} is a shadow or absolute geometry, not both; a shadow\'s subject is its owner')
            owner = region['shadow_of']
            if (not isinstance(owner, dict) or set(owner) != {'reference', 'margin_nm'}
                    or owner['reference'] not in refs or type(owner['margin_nm']) is not int
                    or owner['margin_nm'] < 0):
                raise ValueError(f'{label} shadow_of needs a board reference and a nonnegative integer margin_nm')
        else:
            if region.get('side') not in ('F', 'B'):
                raise ValueError(f'{label} side must be F or B')
            try:
                placer._obstacle_polygons(region.get('polygons'))
            except ValueError as exc:
                raise ValueError(f'{label} polygons: {exc}') from exc
    return regions


def _binding(fp):
    return {'reference': fp['ref'], 'uuid': fp['uuid'], 'fpid': fp.get('fpid', ''), 'side': fp['side'],
            'position_nm': list(fp['position_nm']), 'rotation_deg': fp['rotation_deg']}


def qualified(capture):
    """Footprints by reference with qualified outer courtyards, or ``ValueError``.

    Shared by every grading path, so no entry point grades missing or unproven
    evidence as clear: the capture's schema, hash and unique reference and UUID
    identities (``placement.validate_capture``), each part's mounting side and
    courtyard there, and every declared courtyard proven (curved courtyards by
    their conservative outer bound). A part absent from a side only because it
    declares no courtyard there is a legitimate opposite-side exclusion.
    """
    validate_capture(capture)
    by_ref = {}
    for fp in capture['footprints']:
        ref = fp['ref']
        courtyards = fp.get('courtyards')
        if fp.get('side') not in ('F', 'B') or not isinstance(courtyards, dict):
            raise ValueError(f'{ref}: mounting side and courtyards required')
        if fp['side'] not in courtyards:
            raise ValueError(f'{ref}: mounting-side courtyard missing')
        proven = {}
        for side, courtyard in sorted(courtyards.items()):
            if side not in ('F', 'B'):
                raise ValueError(f'{ref}: unsupported courtyard side {side!r}')
            try:
                proven[side] = {'status': 'proven', 'polygons': courtyard_bounds(courtyard)[0]}
            except ValueError as exc:
                raise ValueError(f'{ref} {side} courtyard not qualified: {exc}') from None
        by_ref[ref] = {**fp, 'courtyards': proven}
    return by_ref


def geometry(region, by_ref):
    """The region's side and polygons at the current placement, with its bound subject."""
    from .block_arrangement import _shadow
    if 'shadow_of' in region:
        fp = by_ref[region['shadow_of']['reference']]
        if fp['side'] not in fp['courtyards']:
            raise ValueError(f"advisory region {region['id']} owner {fp['ref']} has no mounting-side courtyard")
        shape = _shadow(fp, region['shadow_of']['margin_nm'])
        subject = {**_binding(fp), 'courtyard_box_nm': list(placer.boxes(fp)[fp['side']])}
        return shape['side'], shape['polygons'], subject
    subject = _binding(by_ref[region['subject']]) if 'subject' in region else None
    return region['side'], copy.deepcopy(region['polygons']), subject


def _natural(text):
    head = text.rstrip('0123456789')
    tail = text[len(head):]
    return head, int(tail) if tail else -1, text


def _within(box, other, distance):
    """Box prefilter: the boxes are no farther apart than ``distance`` on either axis."""
    return not (box[2] + distance < other[0] or other[2] + distance < box[0]
                or box[3] + distance < other[1] or other[3] + distance < box[1])


def grade(capture, regions, fixed):
    """Every region graded on a complete placement: intruders and nearby parts, reported only."""
    from .block_arrangement import _overlaps
    by_ref = qualified(capture)
    rows = []
    for region in regions:
        side, polygons, subject = geometry(region, by_ref)
        exact = placer._exact(side, polygons)
        skip = subject['reference'] if subject else None
        intruders, near = [], []
        for ref in sorted(by_ref, key=_natural):
            fp = by_ref[ref]
            if ref == skip or side not in fp['courtyards']:
                continue
            item = {'reference': ref, 'fixed': ref in fixed}
            if _overlaps(fp, [exact]):
                intruders.append(item)
            elif _within(placer.boxes(fp)[side], exact['box'], region['proximity_nm']):
                distance2 = placer._spacing(fp, exact)
                if distance2 <= region['proximity_nm'] ** 2:
                    near.append({**item, 'distance_nm': math.isqrt(math.floor(distance2))})  # Floored nm.
        row = {'id': region['id'], 'kind': 'shadow' if 'shadow_of' in region else 'absolute',
               'source': copy.deepcopy(region['source']), 'enforcement': 'advisory', 'side': side,
               'polygons': polygons, 'proximity_nm': region['proximity_nm'],
               'status': 'info' if intruders or near else 'pass', 'intruders': intruders, 'near': near}
        if 'concern' in region:
            row['concern'] = region['concern']
        if subject is not None:
            row['subject'] = subject
        if 'shadow_of' in region:
            row['margin_nm'] = region['shadow_of']['margin_nm']
        rows.append(row)
    return {'schema': SCHEMA, 'board_sha256': capture['board_sha256'], 'search': 'never-pruned', 'regions': rows}


def _millimetres(value, label, positive):
    try:
        number = Decimal(value) if isinstance(value, str) else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f'{label} must be a {"positive" if positive else "nonnegative"} decimal string in mm')
    return number


def sourced_heights(document, refs):
    """Qualified ``agent-ee.sourced-heights/1`` records by board reference.

    Each record binds one board reference to a height and tolerance in mm (decimal
    strings) from a ``datasheet``, ``manufacturer`` or ``owner`` source with a
    citation; a datasheet or manufacturer document also names its captured file's
    sha256. The label is declared, not authenticated (AGENTEE-348).
    """
    if (not isinstance(document, dict) or set(document) != {'schema', 'parts'}
            or document['schema'] != SOURCED_HEIGHTS or not isinstance(document['parts'], list)):
        raise ValueError(f'sourced heights must be {SOURCED_HEIGHTS} with exactly schema and parts')
    out = {}
    for record in document['parts']:
        if (not isinstance(record, dict) or not {'reference', 'height_mm', 'tolerance_mm', 'source'} <= set(record)
                or set(record) - {'reference', 'height_mm', 'tolerance_mm', 'source', 'part'}):
            raise ValueError('sourced height needs reference, height_mm, tolerance_mm and source (optional part)')
        ref = record['reference']
        if ref not in refs or ref in out:
            raise ValueError(f'sourced height reference {ref!r} is not a unique board reference')
        if 'part' in record:
            _text(record['part'], f'{ref} sourced part')
        height = _millimetres(record['height_mm'], f'{ref} height_mm', True)
        _millimetres(record['tolerance_mm'], f'{ref} tolerance_mm', False)
        source = record['source']
        kind = source.get('class') if isinstance(source, dict) else None
        if kind not in ('datasheet', 'manufacturer', 'owner'):
            raise ValueError(f'{ref} height source class must be datasheet, manufacturer or owner')
        document_kind = kind != 'owner'
        if set(source) != {'class', 'citation'} | ({'sha256'} if document_kind else set()):
            raise ValueError(f'{ref} {kind} height source needs exactly class, citation'
                             + (' and sha256' if document_kind else ''))
        _text(source['citation'], f'{ref} height source citation')
        if document_kind and (not isinstance(source['sha256'], str) or len(source['sha256']) != 64
                              or set(source['sha256']) - SHA256_HEX):
            raise ValueError(f'{ref} height source sha256 must be 64 lowercase hex')
        out[ref] = copy.deepcopy(record)
        out[ref]['_height'] = height
    return out


def annotate(report, heights=None, sourced=None):
    """Attach installed-height evidence to each intruding or nearby part.

    ``heights`` is a native ``agent-ee.component-height-probe/1`` result for the
    same board; ``sourced`` is the output of ``sourced_heights``. A sourced height
    is the body's height whatever the probe says: a probe that could not measure
    the model never replaces it, and a measured model outside the sourced
    tolerance is reported as a ``conflict``. Without a source, a part the probe
    did not measure is ``unknown``. Tall/heavy risk is ``unresolved`` for physical
    review either way, because no sourced threshold turns a height into a verdict.
    """
    by_ref = {}
    if heights is not None:
        if (not isinstance(heights, dict) or heights.get('schema') != HEIGHTS
                or not isinstance(heights.get('footprints'), list)):
            raise ValueError(f'height evidence must be {HEIGHTS}')
        if heights.get('board_sha256') != report['board_sha256']:
            raise ValueError('height evidence belongs to another board')
        for item in heights['footprints']:
            if not isinstance(item, dict) or not isinstance(item.get('ref'), str) or item['ref'] in by_ref:
                raise ValueError('height evidence repeats or omits a reference')
            by_ref[item['ref']] = item
    sourced = sourced or {}
    out = copy.deepcopy(report)
    if heights is not None:
        out['height_evidence'] = {'schema': HEIGHTS, 'pcbnew_version': heights.get('pcbnew_version')}
    if sourced:
        out['sourced_heights'] = {'schema': SOURCED_HEIGHTS, 'references': sorted(sourced, key=_natural)}
    for row in out['regions']:
        for item in row['intruders'] + row['near']:
            found = by_ref.get(item['reference'])
            measured = found is not None and found.get('status') == 'measured'
            record = sourced.get(item['reference'])
            if record is not None:
                body = {'status': 'sourced', 'height_mm': record['height_mm'],
                        'tolerance_mm': record['tolerance_mm'], 'source': copy.deepcopy(record['source'])}
                if 'part' in record:
                    body['part'] = record['part']
                if measured:
                    body['model_probe'] = {'status': 'measured', 'height_mm': found['height_mm']}
                    probe = _millimetres(found['height_mm'], f"{item['reference']} probe height_mm", False)
                    if abs(probe - record['_height']) > Decimal(record['tolerance_mm']):
                        body['status'] = 'conflict'
                elif found is not None:
                    body['model_probe'] = {'status': 'unknown', 'reason': found.get('reason', 'no measured height')}
                item['body'] = body
            elif measured:
                item['body'] = {'status': 'measured', 'height_mm': found['height_mm'], 'source': 'native-step-bounds'}
            else:
                item['body'] = {'status': 'unknown',
                                'reason': found.get('reason', 'no measured height') if found else 'not measured'}
            item['tall_heavy_risk'] = 'unresolved'
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--probe', required=True, type=Path, help='placement-probe capture of the placed board')
    parser.add_argument('--policy', required=True, type=Path, help='packing policy declaring advisory_regions')
    parser.add_argument('--heights', type=Path, help='optional component-height-probe/1 result for the same board')
    parser.add_argument('--sourced-heights', type=Path, help=f'optional {SOURCED_HEIGHTS} part heights')
    parser.add_argument('--out', required=True, type=Path, help='new output JSON file')
    args = parser.parse_args(argv)
    try:
        capture = strict_json(args.probe.read_bytes())
        policy = strict_json(args.policy.read_bytes())
        refs = set(qualified(capture))
        regions = validate(policy.get('advisory_regions', []), refs)
        report = grade(capture, regions, placer._fixed_refs(capture, {'fixed_refs': policy.get('fixed_refs', [])}))
        report['policy_sha256'] = digest(args.policy)
        heights = strict_json(args.heights.read_bytes()) if args.heights is not None else None
        sourced = None
        if args.sourced_heights is not None:
            sourced = sourced_heights(strict_json(args.sourced_heights.read_bytes()), refs)
        if heights is not None or sourced is not None:
            report = annotate(report, heights, sourced)
            if sourced is not None:
                report['sourced_heights']['sha256'] = digest(args.sourced_heights)
        if args.out.exists():
            raise ValueError(f'{args.out} already exists')
        write_json(args.out, report)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        return 1
    print(json.dumps({'status': 'graded', 'out': str(args.out),
                      'regions': {r['id']: r['status'] for r in report['regions']}}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
