"""Component-side through-hole board-thickness fit (AGENTEE-448).

A soldered through-hole part declares the board thickness its manufacturer
recommends, with a citation, and optionally explicit hard limits or a wider
documented fit range. The board side is the envelope's NOMINAL thickness only.
The fabricator's finished-thickness tolerance (board_envelope
finished_thickness_tolerance) is a different tolerance and is never read here.

Precedence per bound: an explicit hard limit governs (outside is a defect);
otherwise the default screening band of +/-10% around the recommended nominal,
inclusive, widened on that side by a documented fit range reaching further. A
fit range never narrows the band; only a hard limit can. Outside the band is
unknown (needs feature geometry or evidence), never a defect.
"""
from decimal import Decimal
from fractions import Fraction

from . import board_envelope as envelope

SCHEMA = 'agent-ee.tht-thickness-fit/1'
SOURCE_CLASSES = ('manufacturer', 'datasheet')
# Owner engineering assumption (2026-10-22): a soldered through-hole part fits boards
# within +/-10% of its recommended nominal. A screening default, not a hard ceiling.
SCREENING_BAND = Fraction(1, 10)
MAX_PARTS = 256


def source(value, label):
    """``{class, citation}``: a missing or blank citation is refused, never defaulted."""
    envelope.fields(value, label, ['class', 'citation'])
    if value['class'] not in SOURCE_CLASSES:
        raise ValueError(f'{label} class must be one of {list(SOURCE_CLASSES)}')
    if not isinstance(value['citation'], str) or not value['citation'].strip():
        raise ValueError(f'{label} citation must be a nonempty string')
    return {'class': value['class'], 'citation': value['citation']}


def limits(value, label, *, both):
    """Exact-nm bounds with their source; ``both`` requires min and max."""
    required = ['min_mm', 'max_mm', 'source'] if both else ['source']
    envelope.fields(value, label, required, [] if both else ['min_mm', 'max_mm'])
    bounds = {k: envelope.length(value[k], f'{label} {k}', positive=True)
              for k in ('min_mm', 'max_mm') if k in value}
    if not bounds:
        raise ValueError(f'{label} needs min_mm or max_mm')
    if len(bounds) == 2 and bounds['min_mm'] > bounds['max_mm']:
        raise ValueError(f'{label} min_mm exceeds max_mm')
    return {**bounds, 'source': source(value['source'], f'{label} source')}


def declarations(raw):
    """Validate captured declaration bytes; raise ValueError, never a partial set."""
    value = envelope.load(raw)
    envelope.fields(value, 'thickness fit declaration', ['schema', 'parts'])
    if value['schema'] != SCHEMA:
        raise ValueError(f'thickness fit schema must be {SCHEMA}')
    parts = value['parts']
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise ValueError(f'thickness fit parts must be a nonempty list of at most {MAX_PARTS}')
    records, seen = [], set()
    for part in parts:
        ref = part.get('reference') if isinstance(part, dict) else None
        label = f'thickness fit {ref!r}'
        envelope.fields(part, label, ['reference', 'part_number', 'footprint_id', 'recommended_mm',
                                      'recommended_source'], ['hard_limits', 'fit_range'])
        for key in ('reference', 'part_number', 'footprint_id'):
            if not isinstance(part[key], str) or not part[key].strip() or part[key] != part[key].strip():
                raise ValueError(f'{label} {key} must be a nonempty trimmed string')
        if ref in seen:
            raise ValueError(f'thickness fit reference {ref} is declared twice')
        seen.add(ref)
        recommended = envelope.length(part['recommended_mm'], f'{label} recommended_mm', positive=True)
        record = {'reference': ref, 'part_number': part['part_number'], 'footprint_id': part['footprint_id'],
                  'recommended_nm': recommended,
                  'recommended_source': source(part['recommended_source'], f'{label} recommended_source')}
        for key, both in (('hard_limits', False), ('fit_range', True)):
            if key in part:
                bounds = limits(part[key], f'{label} {key}', both=both)
                if not bounds.get('min_mm', recommended) <= recommended <= bounds.get('max_mm', recommended):
                    raise ValueError(f'{label} {key} excludes the recommended nominal')
                record[key] = bounds
        records.append(record)
    return records


def board_nominal_nm(record):
    """The envelope's nominal thickness; physical totals and fab tolerance are not component fit."""
    return envelope.length(Decimal(record['stackup']['thickness_mm']), 'envelope nominal thickness', positive=True)


def mm_text(nm):
    """Exact millimetre text of a rational nanometre value; the screening band is never rounded."""
    value = Fraction(nm) / 10 ** 6
    for places in range(13):
        if (value * 10 ** places).denominator == 1:
            return envelope.fixed_point(int(value * 10 ** places), places)
    return str(value)


def bound(part, side):
    """One side's governing bound: (limit_nm, basis, violated status)."""
    key = 'min_mm' if side == 'min' else 'max_mm'
    if key in part.get('hard_limits', {}):
        return part['hard_limits'][key], 'manufacturer hard limit', 'defect'
    factor = 1 - SCREENING_BAND if side == 'min' else 1 + SCREENING_BAND
    screening = part['recommended_nm'] * factor
    documented = part.get('fit_range', {}).get(key)
    # A documented range only widens the screening band; it never narrows it.
    if documented is not None and (documented < screening if side == 'min' else documented > screening):
        return documented, 'documented fit range', 'unknown'
    return screening, 'default +/-10% component fit', 'unknown'


def judge(part, board):
    """Per-part verdict against the board nominal, in exact rational arithmetic."""
    item = {'reference': part['reference'], 'part_number': part['part_number'],
            'footprint_id': part['footprint_id'], 'recommended_mm': envelope.mm(part['recommended_nm']),
            'recommended_source': part['recommended_source'], 'board_nominal_mm': envelope.mm(board),
            **{k: {**{b: envelope.mm(v) for b, v in part[k].items() if b != 'source'}, 'source': part[k]['source']}
               for k in ('hard_limits', 'fit_range') if k in part}}
    low, high = bound(part, 'min'), bound(part, 'max')
    item['band_mm'] = [mm_text(low[0]), mm_text(high[0])]
    item['basis'] = sorted({low[1], high[1]})
    violated = [(limit, basis, status, word) for (limit, basis, status), word, out in
                ((low, 'below', board < low[0]), (high, 'above', board > high[0])) if out]
    if not violated:
        item['status'] = 'pass'
        item['reason'] = 'screened (default +/-10% component fit)' if item['basis'] == [
            'default +/-10% component fit'] else 'within ' + ' and '.join(item['basis'])
        return item
    limit, basis, status, word = violated[0]
    item['status'] = status
    item['reason'] = (f"board nominal {envelope.mm(board)} mm is {word} the {basis} "
                      f"{mm_text(limit)} mm" +
                      ('' if status == 'defect' else '; needs feature geometry or evidence'))
    return item


def evaluate(parts, record, native, bindings):
    """Grade every declared part against the envelope nominal and its native installed footprint.

    ``native`` is the envelope probe; ``bindings`` the saved part bindings. A
    declaration that cannot be bound to exactly one installed plated through-hole
    footprint of the declared part is unknown, never a pass.
    """
    from .component_height import saved_binding
    board = board_nominal_nm(record)
    defects, unknown, measured = [], [], []
    if native.get('thickness_nm') != board:
        unknown.append('native board thickness differs from the envelope nominal thickness')
    footprints = native['footprints']
    for part in parts:
        ref = part['reference']
        matches = [fp for fp in footprints if fp['ref'] == ref]
        try:
            if len(matches) != 1:
                raise ValueError('expected exactly one native footprint with this reference')
            fp = matches[0]
            if fp['fpid'] != part['footprint_id']:
                raise ValueError(f"native footprint {fp['fpid']!r} differs from declared {part['footprint_id']!r}")
            if not any(pad['attribute'] == 'PTH' for pad in fp['pads']):
                raise ValueError('declared through-hole part has no plated through-hole pad')
            binding = saved_binding(bindings, ref, fp)
            if (binding['partnumber'] or binding['value']) != part['part_number']:
                raise ValueError(f"saved part number {binding['partnumber'] or binding['value']!r} differs "
                                 f"from declared {part['part_number']!r}")
        except (ValueError, KeyError) as exc:
            unknown.append(f'{ref}: {exc}')
            measured.append({'reference': ref, 'status': 'unknown', 'reason': str(exc)})
            continue
        item = judge(part, board)
        if item['status'] == 'defect':
            defects.append(f"{ref}: {item['reason']}")
        elif item['status'] == 'unknown':
            unknown.append(f"{ref}: {item['reason']}")
        measured.append(item)
    return {'criterion': 'tht-thickness-fit/1', 'board_nominal_mm': envelope.mm(board),
            'status': 'unknown' if unknown else 'defect' if defects else 'pass',
            'defects': defects, 'unknown': unknown, 'measured': measured}
