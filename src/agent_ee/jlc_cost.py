"""Offline JLCPCB fabrication and assembly cost estimate (AGENTEE-77); information, never a grade.

`jlc_cost_estimate` never re-runs an export and never prices anything from a
live service. It reads an earlier `fab_export` check's verified staging
(`bom.csv`, `cpl.csv`, `pcb_summary.json` and the manifest's copper layer
count), the captured board's footprint pads, a captured offline catalog
snapshot and a captured, versioned pricing table, and does Decimal
arithmetic:

* Part class (Basic, Preferred, Extended) is the catalog snapshot's flag for
  each unique LCSC ID in the BOM. An ID absent from the snapshot, or a snapshot
  row without enough class flags, is counted `unknown`, never Basic. Its loading
  fee is reported as a range over the table's class fees, not a guess.
* Solder joints are the numbered `smd` and `thru_hole` pads of each BOM
  designator's board footprint. A `thru_hole` pad carrying the native
  heatsink/mechanical property (AGENTEE-221 thermal-via contract) whose number
  also names an `smd` pad containing its drill and annulus is treated as a via
  for pricing (AGENTEE-228); it is excluded and recorded. Any other `thru_hole` pad
  of a footprint with `smd` pads, or with such a property, stays counted and
  is reported as unclassified. Assembled sides come from the CPL.
* The PCB line uses the copper layer count and the `pcb_summary.json` bounding
  box. A missing or unusable summary leaves the PCB cost unresolved and the
  outcome `unknown`.
* Every amount names the pricing-table entry, or the catalog row, it came from.
  Nothing missing from the table is filled in; that line stays unresolved and
  the totals become a lower bound (and an upper bound only when every gap is
  bounded).

The outcome is `info` (or `unknown` without a usable summary), never `pass` or
`defect`. Scope/3 coverage declares no predicate, so the check can be mapped
only to an informational requirement. The pricing table's numbers go stale; a
stale entry is reported, not refused. The mechanism (table of setup, stencil,
per-joint and per-extended-part fees, layer and area base prices) follows
atopile's MIT-licensed cost_estimation domain; no code or prices are copied.
"""

import hashlib
import json
import math
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from .assembly_readiness import (AssemblyReadinessExecutionError, ISO_DATE_RE, LCSC_RE,
                                 load_catalog_snapshot)
from .catalog import decimal_number
from .cpl_rotation import read_bom_bindings, read_native_cpl
from .fab_export import natural_key, scan_sexpr, verify_manifest
from .footprint_library import THERMAL_VIA_PROPERTIES, thermal_via_property
from .hardware_interface import unique_object
from .storage import digest, read_json, write_json
from .symbol_library import LibraryError, children, field, nanometres

REPORT = 'agent-ee.jlc-cost-estimate/1'
TABLE_SCHEMA = 'agent-ee.jlc-pricing/1'
PREDICATES = []
PARAMS = frozenset(('export_check', 'catalog', 'catalog_sha256', 'pricing_table', 'assembly_tier', 'boards',
                    'as_of', 'pricing_max_age_days'))
REQUIRED = ('export_check', 'catalog', 'pricing_table', 'assembly_tier', 'boards')
TIERS = ('economic', 'standard')
CLASSES = ('basic', 'preferred', 'extended')
TABLE_KEYS = frozenset(('schema', 'table_id', 'version', 'currency', 'captured_on', 'stale_before',
                        'provenance', 'entries'))
PROVENANCE_KEYS = frozenset(('source_url', 'method', 'values'))
PROVENANCE_VALUES = ('observed', 'owned-test-values')
COMMON_KEYS = ('id', 'kind', 'observed_on')
# kind -> (selector keys, decimal keys); every entry also carries COMMON_KEYS.
ENTRY_KINDS = {
    'pcb_base': (('layers',), ('max_width_mm', 'max_height_mm', 'included_area_mm2', 'value_usd')),
    'pcb_area_surcharge': (('layers',), ('step_area_mm2', 'value_usd')),
    'assembly_tier': (('tier',), ()),
    'assembly_setup': (('tier',), ('value_usd',)),
    'assembly_stencil': (('tier',), ('value_usd',)),
    'assembly_joint': (('tier', 'technology'), ('value_usd',)),
    'part_loading_fee': (('tier', 'part_class'), ('value_usd',)),
}
SELECTORS = {'tier': TIERS, 'technology': ('smt', 'tht'), 'part_class': CLASSES}
PAD_TECHNOLOGY = {'smd': 'smt', 'thru_hole': 'tht'}
MM2 = '²'


def _error(execution, message):
    from .checks import CheckError
    return CheckError(execution, message)


def _iso_date(value, label):
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise ValueError(f'{label} must be a YYYY-MM-DD date')
    return date.fromisoformat(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def validate_params(params):
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    unknown = set(params) - PARAMS
    if unknown:
        raise ValueError(f'unknown jlc_cost_estimate params: {sorted(unknown)}')
    missing = [key for key in REQUIRED if key not in params]
    if missing:
        raise ValueError(f'jlc_cost_estimate requires params {missing}')
    for key in ('export_check', 'catalog', 'pricing_table'):
        _text(params[key], key)
    sha = params.get('catalog_sha256')
    if sha is not None and (not isinstance(sha, str) or len(sha) != 64
                            or any(c not in '0123456789abcdef' for c in sha)):
        raise ValueError('catalog_sha256 must be lowercase hex SHA-256 or null')
    if params['assembly_tier'] not in TIERS:
        raise ValueError(f'assembly_tier must be one of {list(TIERS)}')
    boards = params['boards']
    if isinstance(boards, bool) or not isinstance(boards, int) or boards < 1:
        raise ValueError('boards must be an integer >= 1')
    if params.get('as_of') is not None:
        _iso_date(params['as_of'], 'as_of')
    age = params.get('pricing_max_age_days')
    if age is not None and (isinstance(age, bool) or not isinstance(age, int) or age < 0):
        raise ValueError('pricing_max_age_days must be an integer >= 0 or null')
    return {'catalog_sha256': None, 'as_of': None, 'pricing_max_age_days': None, **params}


def capability(params, files, inputs=None):
    """Static scope/3 contract: no predicate (informational only); catalog and table are immutable."""
    from .assembly_readiness import capability as catalog_capability
    params = validate_params(params)
    if params['pricing_table'] not in files:
        raise ValueError(f"pricing_table {params['pricing_table']!r} must be a captured input")
    if inputs is not None:
        load_pricing_table((Path(inputs) / params['pricing_table']).read_bytes())
    catalog = catalog_capability({'catalog': params['catalog']}, files, inputs)['immutable_inputs']
    return {'immutable_inputs': sorted({params['pricing_table'], *catalog}), 'predicates': list(PREDICATES)}


# ------------------------------------------------------------- pricing table

def _reject_constant(value):
    raise ValueError(f'non-JSON numeric constant {value}')


def load_pricing_table(data):
    """Strict `agent-ee.jlc-pricing/1`: exact keys, decimal-text values, unique ids and selectors."""
    if not isinstance(data, bytes):
        raise ValueError('pricing table must be bytes')
    try:
        doc = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f'pricing table is not strict JSON: {exc}') from exc
    if not isinstance(doc, dict) or set(doc) != TABLE_KEYS:
        raise ValueError(f'pricing table keys must be exactly {sorted(TABLE_KEYS)}')
    if doc['schema'] != TABLE_SCHEMA:
        raise ValueError(f'pricing table schema must be {TABLE_SCHEMA}')
    for key in ('table_id', 'version'):
        _text(doc[key], key)
    if doc['currency'] != 'USD':
        raise ValueError('pricing table currency must be USD')
    captured = _iso_date(doc['captured_on'], 'captured_on')
    stale_before = None
    if doc['stale_before'] is not None:
        stale_before = _iso_date(doc['stale_before'], 'stale_before')
        if stale_before > captured:
            raise ValueError('stale_before cannot be after captured_on')
    provenance = doc['provenance']
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_KEYS:
        raise ValueError(f'provenance keys must be exactly {sorted(PROVENANCE_KEYS)}')
    _text(provenance['source_url'], 'provenance.source_url')
    _text(provenance['method'], 'provenance.method')
    if provenance['values'] not in PROVENANCE_VALUES:
        raise ValueError(f'provenance.values must be one of {list(PROVENANCE_VALUES)}')
    if not isinstance(doc['entries'], list) or not doc['entries']:
        raise ValueError('entries must be a nonempty list')
    entries, ids, selectors = [], set(), set()
    for raw in doc['entries']:
        if not isinstance(raw, dict) or raw.get('kind') not in ENTRY_KINDS:
            raise ValueError(f'entry {raw!r} has an unknown kind')
        keys, decimals = ENTRY_KINDS[raw['kind']]
        extra = ('max_sides',) if raw['kind'] == 'assembly_tier' else ()
        expected = {*COMMON_KEYS, *keys, *decimals, *extra}
        if set(raw) != expected:
            raise ValueError(f"entry {raw.get('id')!r} keys must be exactly {sorted(expected)}")
        entry = {'id': _text(raw['id'], 'entry id'), 'kind': raw['kind']}
        if entry['id'] in ids:
            raise ValueError(f"duplicate entry id {entry['id']!r}")
        ids.add(entry['id'])
        observed = _iso_date(raw['observed_on'], f"{entry['id']}.observed_on")
        if observed > captured:
            raise ValueError(f"{entry['id']}.observed_on is after captured_on")
        entry['observed_on'] = observed
        for key in keys:
            value = raw[key]
            if key == 'layers':
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{entry['id']}.layers must be a positive integer")
            elif value not in SELECTORS[key]:
                raise ValueError(f"{entry['id']}.{key} must be one of {list(SELECTORS[key])}")
            entry[key] = value
        for key in decimals:
            try:
                number = decimal_number(raw[key])
            except ValueError as exc:
                raise ValueError(f"{entry['id']}.{key} must be decimal text: {exc}") from exc
            if number < 0 or (key != 'value_usd' and key != 'included_area_mm2' and number <= 0):
                raise ValueError(f"{entry['id']}.{key} is out of range")
            entry[key] = number
        if extra:
            if raw['max_sides'] not in (1, 2) or isinstance(raw['max_sides'], bool):
                raise ValueError(f"{entry['id']}.max_sides must be 1 or 2")
            entry['max_sides'] = raw['max_sides']
        selector = (entry['kind'], *(entry[key] for key in keys))
        if selector in selectors:
            raise ValueError(f'two entries share selector {selector}')
        selectors.add(selector)
        entries.append(entry)
    return {'table_id': doc['table_id'], 'version': doc['version'], 'currency': 'USD', 'captured_on': captured,
            'stale_before': stale_before, 'provenance': provenance, 'entries': entries}


def entry_freshness(table, as_of, max_age_days):
    """{entry id: [reasons]}; an empty list is fresh. No declared policy makes every entry stale."""
    stale = {}
    for entry in table['entries']:
        reasons = []
        observed = entry['observed_on']
        if observed > as_of:
            reasons.append(f'observed_on {observed} is after the evaluation date {as_of}')
        if table['stale_before'] is not None and observed < table['stale_before']:
            reasons.append(f"observed_on {observed} is before the table stale_before {table['stale_before']}")
        if max_age_days is not None and (as_of - observed).days > max_age_days:
            reasons.append(f'observed_on {observed} is older than pricing_max_age_days {max_age_days}')
        if table['stale_before'] is None and max_age_days is None:
            reasons.append('no staleness policy: the table has no stale_before and no pricing_max_age_days')
        stale[entry['id']] = reasons
    return stale


def find_entry(table, kind, **selector):
    for entry in table['entries']:
        if entry['kind'] == kind and all(entry.get(k) == v for k, v in selector.items()):
            return entry
    return None


# ------------------------------------------------------------- staged inputs

def _pad_want(path):
    if len(path) == 1:
        return path[0] == 'kicad_pcb'
    return path[1] == 'footprint' and (len(path) == 2 or (len(path) == 3 and path[2] in ('property', 'pad'))
                                       or (len(path) >= 4 and path[2] == 'pad')
                                       or path[2:] == ('at',))


def _pad_groups(groups):
    return [{'pad': pad, 'count': n, **dict(extra)}
            for (pad, *extra), n in sorted(groups.items(), key=lambda g: (natural_key(g[0][0]), g[0][1:]))]


def _via_inside_smd(via, smd, footprint_angle):
    """Prove containment using integer nm, with closed boundaries and no tolerance.

    Only circular, centred round drills/annuli and rectangular or circular SMD
    copper are qualified. Slots, custom stacks, rounded/oval SMD shapes and
    non-cardinal rectangle rotations remain counted rather than approximated.
    Pad positions are footprint-local; saved pad angles include the footprint pose.
    """
    try:
        if len(via) < 4 or len(smd) < 4 or via[3] != 'circle' or smd[3] not in ('rect', 'circle'):
            return False
        for pad in (via, smd):
            if any(children(pad, key) for key in ('padstack', 'primitives', 'options', 'offset')):
                return False
        via_layer_nodes, smd_layers = children(via, 'layers'), children(smd, 'layers')
        if len(via_layer_nodes) != 1:
            return False
        via_layers = via_layer_nodes[0][1:]
        if len(smd_layers) != 1:
            return False
        copper = set(smd_layers[0][1:]) & {'F.Cu', 'B.Cu'}
        if not copper or not all(layer in via_layers or '*.Cu' in via_layers for layer in copper):
            return False
        diameter, other = map(nanometres, field(via, 'size', 2))
        drills = children(via, 'drill')
        if len(drills) != 1 or len(drills[0]) != 2:
            return False
        drill = nanometres(field(via, 'drill', 1)[0])
        if diameter != other or not 0 < drill <= diameter:
            return False
        positions = []
        angles = []
        for pad in (via, smd):
            at = children(pad, 'at')
            if len(at) != 1 or len(at[0]) not in (3, 4):
                return False
            positions.append(tuple(map(nanometres, at[0][1:3])))
            angles.append(nanometres(at[0][3]) if len(at[0]) == 4 else 0)
        width, height = map(nanometres, field(smd, 'size', 2))
        if width <= 0 or height <= 0:
            return False
        # Doubled distances avoid rounding half-nanometre radii or dimensions.
        dx, dy = (2 * abs(a - b) for a, b in zip(*positions))
        if smd[3] == 'circle':
            return width == height and diameter <= width and dx * dx + dy * dy <= (width - diameter) ** 2
        angle = (angles[1] - footprint_angle) % 360000000
        if angle % 90000000:
            return False
        if angle in (90000000, 270000000):
            width, height = height, width
        return dx + diameter <= width and dy + diameter <= height
    except (LibraryError, IndexError):
        # Unsupported/malformed geometry never justifies excluding a priced joint.
        return False


def designator_joints(data):
    """{reference: {'smt', 'tht', 'tht_excluded', 'tht_unclassified'}} from each board footprint's pads.

    Numbered smd/thru_hole pads count as SMT/THT joints. A thru_hole pad with
    a native thermal-via property geometrically contained in same-numbered
    SMD copper is a via in that pad: excluded from `tht` and listed in
    `tht_excluded`. Other thru_hole pads of a footprint with smd pads, and
    propertied pads without proved containment (e.g. soldered shield anchors), stay counted and are
    listed in `tht_unclassified`.
    """
    root, _ = scan_sexpr(data, _pad_want)
    if root[0] != 'kicad_pcb':
        raise ValueError('not a kicad_pcb file')
    joints = {}
    for fp in (x for x in root[1:] if isinstance(x, list) and x and x[0] == 'footprint'):
        refs = [p[2] for p in fp[1:] if isinstance(p, list) and p[0] == 'property' and len(p) > 2
                and p[1] == 'Reference']
        if len(refs) != 1:
            raise ValueError(f'footprint {fp[1:2]} lacks exactly one Reference')
        if refs[0] in joints:
            raise ValueError(f'board repeats footprint reference {refs[0]}')
        count, excluded, unclassified = Counter(), Counter(), Counter()
        pads = [p for p in fp[1:] if isinstance(p, list) and p[0] == 'pad']
        if any(len(pad) < 3 for pad in pads):
            raise ValueError(f'footprint {refs[0]} has a malformed pad')
        numbered = [pad for pad in pads if pad[1] != '' and pad[2] in PAD_TECHNOLOGY]
        smd_pads = [pad for pad in numbered if pad[2] == 'smd']
        smd = {pad[1] for pad in smd_pads}
        pose = children(fp, 'at')
        try:
            if not pose:
                footprint_angle = 0
            elif len(pose) == 1 and len(pose[0]) in (3, 4):
                footprint_angle = nanometres(pose[0][3]) if len(pose[0]) == 4 else 0
            else:
                footprint_angle = None
        except LibraryError:
            footprint_angle = None
        for pad in numbered:
            prop = thermal_via_property(pad) if pad[2] == 'thru_hole' else None
            if prop and footprint_angle is not None and any(
                    other[1] == pad[1] and _via_inside_smd(pad, other, footprint_angle) for other in smd_pads):
                excluded[pad[1], ('property', prop), ('reason', f'native {prop} via in SMD pad {pad[1]}; '
                                                      'not a soldered THT joint')] += 1
                continue
            count[PAD_TECHNOLOGY[pad[2]]] += 1
            if prop:
                unclassified[pad[1], ('reason', f'native {prop} but containment in same-numbered SMD copper '
                                      'is unproved; may be a soldered anchor, counted as a THT joint')] += 1
            elif pad[2] == 'thru_hole' and smd:
                unclassified[pad[1], ('reason', f'thru_hole pad in a footprint with SMD pads lacks native '
                                      f'{" or ".join(THERMAL_VIA_PROPERTIES)}; counted as a THT joint')] += 1
        joints[refs[0]] = {'smt': count['smt'], 'tht': count['tht'], 'tht_excluded': _pad_groups(excluded),
                           'tht_unclassified': _pad_groups(unclassified)}
    return joints


def _millimetres(text, suffix, label):
    if not isinstance(text, str) or not text.endswith(suffix):
        raise ValueError(f'pcb_summary {label} {text!r} is not a "{suffix.strip()}" value')
    number = decimal_number(text[:-len(suffix)].strip())
    if number <= 0:
        raise ValueError(f'pcb_summary {label} must be positive')
    return number


def read_pcb_summary(data):
    """Bounding width/height (mm) and native area (mm^2) of the native `pcb export stats` JSON."""
    doc = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=_reject_constant)
    board = doc.get('board') if isinstance(doc, dict) else None
    if not isinstance(board, dict):
        raise ValueError('pcb_summary has no board object')
    if board.get('has_outline') is not True:
        raise ValueError('pcb_summary reports no board outline')
    return {'width_mm': _millimetres(board.get('width'), ' mm', 'width'),
            'height_mm': _millimetres(board.get('height'), ' mm', 'height'),
            'native_area_mm2': _millimetres(board.get('area'), ' mm' + MM2, 'area')}


def classify(lcsc, catalog):
    part = catalog['parts'].get(lcsc) if LCSC_RE.fullmatch(lcsc or '') else None
    if part is None:
        return 'unknown', 'absent from the catalog snapshot', None
    if not part['class_known']:
        return 'unknown', 'catalog row lacks is_basic/is_preferred class flags', part
    return part['tier'].lower(), 'catalog snapshot flags', part


# ------------------------------------------------------------- arithmetic

def _d(number):
    return format(number, 'f')


class Estimate:
    """Line items and unresolved gaps, grouped; amounts are exact Decimals."""

    def __init__(self, freshness):
        self.freshness = freshness
        self.lines, self.unresolved = [], []

    def line(self, group, item, entry, quantity, unit, key='value_usd'):
        unit_value = entry[key] if entry is not None else unit
        record = {'group': group, 'item': item, 'quantity': _d(Decimal(quantity)), 'unit_usd': _d(unit_value),
                  'amount_usd': _d(Decimal(quantity) * unit_value)}
        if entry is not None:
            record.update(entry=entry['id'], entry_stale=bool(self.freshness[entry['id']]))
        self.lines.append(record)
        return record

    def gap(self, group, item, reason, low=Decimal(0), high=None, entries=()):
        self.unresolved.append({'group': group, 'item': item, 'reason': reason, 'min_usd': _d(low),
                                'max_usd': None if high is None else _d(high), 'entries': list(entries)})

    def totals(self, groups):
        result = {}
        for group in (*groups, None):
            lines = [l for l in self.lines if group in (None, l['group'])]
            gaps = [g for g in self.unresolved if group in (None, g['group'])]
            known = sum((Decimal(l['amount_usd']) for l in lines), Decimal(0))
            low = known + sum((Decimal(g['min_usd']) for g in gaps), Decimal(0))
            bounded = all(g['max_usd'] is not None for g in gaps)
            high = known + sum((Decimal(g['max_usd']) for g in gaps), Decimal(0)) if bounded else None
            result[group or 'total'] = {'min_usd': _d(low), 'max_usd': None if high is None else _d(high),
                                        'complete': not gaps}
        return result


def estimate(table, freshness, *, tier, boards, layers, summary, bindings, sides, joints, catalog,
             summary_problem='pcb_summary.json is missing or unusable'):
    """Pure arithmetic over verified inputs; returns (report fields, Estimate)."""
    est = Estimate(freshness)
    refs_by_lcsc = {}
    for ref, lcsc in bindings.items():
        refs_by_lcsc.setdefault(lcsc, []).append(ref)
    parts = []
    for lcsc in sorted(refs_by_lcsc, key=lambda key: natural_key(refs_by_lcsc[key][0])):
        refs = sorted(refs_by_lcsc[lcsc], key=natural_key)
        part_class, source, row = classify(lcsc, catalog)
        parts.append({'lcsc': lcsc, 'designators': refs, 'quantity_per_board': len(refs), 'class': part_class,
                      'class_source': source, 'price1_usd': None if row is None or row['price1'] is None
                      else _d(Decimal(repr(row['price1']))), 'stock': None if row is None else row['stock']})
    counts = {name: sum(1 for p in parts if p['class'] == name) for name in (*CLASSES, 'unknown')}

    # PCB: layer base price within its box, plus a stepped surcharge on total ordered area.
    if summary is None:
        est.gap('pcb', 'pcb', summary_problem)
    else:
        base = find_entry(table, 'pcb_base', layers=layers)
        width, height = summary['width_mm'], summary['height_mm']
        if base is None:
            est.gap('pcb', 'pcb', f'pricing table has no pcb_base entry for {layers} layers')
        elif not ((width <= base['max_width_mm'] and height <= base['max_height_mm'])
                  or (height <= base['max_width_mm'] and width <= base['max_height_mm'])):
            est.gap('pcb', 'pcb', f"board {_d(width)} x {_d(height)} mm exceeds {base['id']} "
                    f"({_d(base['max_width_mm'])} x {_d(base['max_height_mm'])} mm)", entries=[base['id']])
        else:
            est.line('pcb', f'{layers}-layer base price', base, 1, None)
            excess = width * height * boards - base['included_area_mm2']
            if excess > 0:
                surcharge = find_entry(table, 'pcb_area_surcharge', layers=layers)
                if surcharge is None:
                    est.gap('pcb', 'area surcharge', f'ordered area exceeds {base["id"]} included area by '
                            f'{_d(excess)} mm2 and the table has no pcb_area_surcharge for {layers} layers',
                            entries=[base['id']])
                else:
                    steps = math.ceil(excess / surcharge['step_area_mm2'])
                    est.line('pcb', f'area surcharge: {steps} started step(s) of '
                             f'{_d(surcharge["step_area_mm2"])} mm2 above {_d(base["included_area_mm2"])} mm2',
                             surcharge, steps, None)

    # Assembly: tier side limit, per-side setup and stencil, per-joint and per-unique-part loading fees.
    tier_entry = find_entry(table, 'assembly_tier', tier=tier)
    used_sides = sorted(set(sides.values()))
    smt = sum(joints[ref]['smt'] for ref in bindings) * boards
    tht = sum(joints[ref]['tht'] for ref in bindings) * boards
    tht_excluded = sum(g['count'] for ref in bindings for g in joints[ref]['tht_excluded'])
    tht_unclassified = sum(g['count'] for ref in bindings for g in joints[ref]['tht_unclassified'])
    if tier_entry is None:
        est.gap('assembly', 'assembly', f'pricing table has no assembly_tier entry for {tier}')
    elif len(used_sides) > tier_entry['max_sides']:
        est.gap('assembly', 'assembly', f"CPL places parts on {used_sides}; {tier_entry['id']} assembles at most "
                f"{tier_entry['max_sides']} side(s)", entries=[tier_entry['id']])
    else:
        for kind, label in (('assembly_setup', 'setup'), ('assembly_stencil', 'stencil')):
            entry = find_entry(table, kind, tier=tier)
            if entry is None:
                est.gap('assembly', label, f'pricing table has no {kind} entry for {tier}')
            else:
                est.line('assembly', f'{label} per assembled side ({", ".join(used_sides)})', entry,
                         len(used_sides), None)
        for technology, count in (('smt', smt), ('tht', tht)):
            if not count:
                continue
            entry = find_entry(table, 'assembly_joint', tier=tier, technology=technology)
            if entry is None:
                est.gap('assembly', f'{technology} joints', f'{count} {technology} joints but the table has no '
                        f'assembly_joint entry for {tier}/{technology}')
            else:
                est.line('assembly', f'{technology} solder joints ({count // boards} per board x {boards})',
                         entry, count, None)
        fees = {name: find_entry(table, 'part_loading_fee', tier=tier, part_class=name) for name in CLASSES}
        for name in CLASSES:
            if not counts[name]:
                continue
            if fees[name] is None:
                est.gap('assembly', f'{name} part loading', f'{counts[name]} unique {name} parts but the table '
                        f'has no part_loading_fee entry for {tier}/{name}')
            else:
                est.line('assembly', f'{name} part loading fee ({counts[name]} unique LCSC)', fees[name],
                         counts[name], None)
        if counts['unknown']:
            known = [fees[name] for name in CLASSES if fees[name] is not None]
            values = [fee['value_usd'] for fee in known]
            # A class without a fee entry bounds nothing: the range is then [0, unbounded).
            complete = len(known) == len(CLASSES)
            low = counts['unknown'] * min(values) if complete else Decimal(0)
            high = counts['unknown'] * max(values) if complete else None
            est.gap('assembly', 'unknown-class part loading',
                    f"{counts['unknown']} unique LCSC with unknown class: "
                    f"{[p['lcsc'] for p in parts if p['class'] == 'unknown']}", low, high,
                    entries=[fee['id'] for fee in known])

    # Components: catalog price1 times quantity; price breaks are not modelled.
    for part in parts:
        if part['price1_usd'] is None:
            est.gap('components', part['lcsc'], 'no catalog price1 for this LCSC ID')
        else:
            quantity = part['quantity_per_board'] * boards
            record = est.line('components', f"{part['lcsc']} x {quantity}", None, quantity,
                              Decimal(part['price1_usd']))
            record['catalog_row'] = part['lcsc']
    fields = {'parts': parts, 'class_counts': counts, 'sides': used_sides,
              'joints': {'smt': smt, 'tht': tht, 'smt_per_board': smt // boards, 'tht_per_board': tht // boards,
                         'tht_excluded_per_board': tht_excluded, 'tht_unclassified_per_board': tht_unclassified,
                         'per_designator': {ref: joints[ref] for ref in sorted(bindings, key=natural_key)}}}
    return fields, est


# ------------------------------------------------------------- the check

def check_jlc_cost_estimate(ctx):
    from .datasheet_facts import prerequisite
    try:
        params = validate_params(ctx.params)
    except ValueError as exc:
        raise _error('unsupported', str(exc)) from exc
    details = ctx.result['details']
    board_rel = ctx.scope.get('board')
    board_sha = ctx.manifest['files'].get(board_rel) if isinstance(board_rel, str) else None
    if not board_sha:
        raise _error('unsupported', 'scope declares no captured board')

    def read(path):
        if path not in ctx.manifest['files']:
            raise ValueError(f'{path} is not a captured input')
        data = (ctx.inputs / path).read_bytes()
        if digest_bytes(data) != ctx.manifest['files'][path]:
            raise ValueError(f'{path} differs from its captured digest')
        return data
    try:
        board_bytes = read(board_rel)
        table_bytes = read(params['pricing_table'])
    except (OSError, ValueError) as exc:
        raise _error('malformed', str(exc)) from exc
    try:
        table = load_pricing_table(table_bytes)
    except ValueError as exc:
        raise _error('malformed', f'pricing table refused: {exc}') from exc
    try:
        catalog = load_catalog_snapshot(ctx.inputs, params['catalog'], expected_sha256=params['catalog_sha256'])
    except AssemblyReadinessExecutionError as exc:
        raise _error(exc.execution, f'catalog snapshot refused: {exc}') from exc
    if catalog['catalog_sha256'] != ctx.manifest['files'].get(params['catalog']):
        raise _error('malformed', 'catalog bytes differ from the candidate manifest digest')

    eid = params['export_check']
    manifest_path = prerequisite(ctx, eid, 'fab_export', 'fab/manifest.json')
    stage = manifest_path.parent
    try:
        manifest = read_json(manifest_path)
        verify_manifest(stage, manifest)
        if manifest.get('board_sha256') != board_sha:
            raise ValueError('export manifest is bound to another board digest')
        layers = manifest.get('copper_layers')
        if isinstance(layers, bool) or not isinstance(layers, int) or layers < 2:
            raise ValueError('export manifest copper_layers is not an integer >= 2')
        bindings = read_bom_bindings((stage / 'bom.csv').read_text(encoding='utf-8'))
        sides = {row[0]: row[3] for row in read_native_cpl((stage / 'cpl.csv').read_text(encoding='utf-8'))}
        if sorted(sides, key=natural_key) != sorted(bindings, key=natural_key):
            raise ValueError(f'BOM designators {sorted(bindings, key=natural_key)} differ from CPL designators '
                             f'{sorted(sides, key=natural_key)}')
        joints = designator_joints(board_bytes)
        unplaced = [ref for ref in bindings if ref not in joints]
        if unplaced:
            raise ValueError(f'BOM designators without a board footprint: {sorted(unplaced, key=natural_key)}')
    except (OSError, ValueError, UnicodeError) as exc:
        raise _error('malformed', f'fab_export {eid!r} staging is unusable: {exc}') from exc
    ctx.result['evidence'] += [f'work/{eid}/fab/bom.csv', f'work/{eid}/fab/cpl.csv']

    summary, summary_problem, summary_sha = None, None, manifest['files'].get('pcb_summary.json')
    if summary_sha is None:
        summary_problem = 'export manifest lists no pcb_summary.json'
    else:
        try:
            summary = read_pcb_summary((stage / 'pcb_summary.json').read_bytes())
            ctx.result['evidence'].append(f'work/{eid}/fab/pcb_summary.json')
        except (OSError, ValueError, UnicodeError) as exc:
            summary_problem = f'pcb_summary.json is unusable: {exc}'

    as_of = date.fromisoformat(params['as_of'] or catalog['snapshot_date'][:10])
    freshness = entry_freshness(table, as_of, params['pricing_max_age_days'])
    fields, est = estimate(table, freshness, tier=params['assembly_tier'], boards=params['boards'],
                           layers=layers, summary=summary, bindings=bindings, sides=sides, joints=joints,
                           catalog=catalog, summary_problem=summary_problem)
    totals = est.totals(('pcb', 'assembly', 'components'))
    outcome = 'unknown' if summary is None else 'info'
    stale = {key: reasons for key, reasons in freshness.items() if reasons}
    cited = sorted({l['entry'] for l in est.lines if 'entry' in l}
                   | {e for g in est.unresolved for e in g['entries']})
    table_record = {'path': params['pricing_table'], 'sha256': digest_bytes(table_bytes),
                    'table_id': table['table_id'], 'version': table['version'],
                    'captured_on': table['captured_on'].isoformat(),
                    'stale_before': table['stale_before'] and table['stale_before'].isoformat(),
                    'provenance': table['provenance'], 'evaluated_as_of': as_of.isoformat(),
                    'pricing_max_age_days': params['pricing_max_age_days'],
                    'cited_entries': cited, 'stale_entries': {k: stale[k] for k in cited if k in stale}}
    inputs = {'board': {'path': board_rel, 'sha256': board_sha},
              'export': {'check': eid, 'manifest_sha256': digest(manifest_path),
                         'bom_sha256': manifest['files'].get('bom.csv'),
                         'cpl_sha256': manifest['files'].get('cpl.csv'), 'pcb_summary_sha256': summary_sha},
              'catalog': {'path': catalog['catalog_rel'], 'sha256': catalog['catalog_sha256'],
                          'database_sha256': catalog['database_sha256'],
                          'snapshot_date': catalog['snapshot_date']}}
    report = {'schema': REPORT, 'candidate_id': ctx.manifest['candidate_id'], 'not_a_quote': True,
              'execution': 'ok', 'outcome': outcome, 'assembly_tier': params['assembly_tier'],
              'boards': params['boards'], 'pricing_table': table_record, 'inputs': inputs,
              'pcb': None if summary is None else {'copper_layers': layers, 'width_mm': _d(summary['width_mm']),
                                                   'height_mm': _d(summary['height_mm']),
                                                   'billing_area_mm2': _d(summary['width_mm'] * summary['height_mm']),
                                                   'native_area_mm2': _d(summary['native_area_mm2'])},
              'pcb_summary_problem': summary_problem, **fields,
              'selection_objective': {'unique_extended_parts': fields['class_counts']['extended'],
                                      'unique_unknown_class_parts': fields['class_counts']['unknown']},
              'line_items': est.lines, 'unresolved': est.unresolved, 'totals': totals}
    write_json(ctx.work / 'jlc-cost-estimate.json', report)
    ctx.evidence('jlc-cost-estimate.json')
    total = totals['total']
    details.update(not_a_quote=True, pricing_table={k: table_record[k] for k in
                                                    ('path', 'sha256', 'table_id', 'version', 'captured_on')},
                   inputs=inputs, class_counts=fields['class_counts'],
                   joints={k: fields['joints'][k] for k in ('smt', 'tht', 'tht_excluded_per_board',
                                                            'tht_unclassified_per_board')},
                   sides=fields['sides'], totals=totals, unresolved=len(est.unresolved),
                   stale_entries=sorted(table_record['stale_entries']),
                   report_sha256=digest(ctx.work / 'jlc-cost-estimate.json'))
    ctx.result.update(execution='ok', outcome=outcome)
    span = (f"{total['min_usd']} USD" if total['complete'] else
            f"at least {total['min_usd']} USD" if total['max_usd'] is None else
            f"{total['min_usd']}..{total['max_usd']} USD")
    counts = fields['class_counts']
    ctx.result['summary'] = (
        f"informational estimate, not a quote: {span} for {params['boards']} board(s), "
        f"{params['assembly_tier']} assembly; {counts['basic']} basic, {counts['preferred']} preferred, "
        f"{counts['extended']} extended, {counts['unknown']} unknown-class unique parts; "
        f"table {table['table_id']} v{table['version']} ({table['captured_on']})"
        + (f'; {summary_problem}' if summary_problem else '')
        + (f"; stale pricing entries {sorted(table_record['stale_entries'])}" if table_record['stale_entries'] else ''))
    ctx.result['limitations'] += [
        'not a quote: arithmetic over a captured, versioned pricing table and an offline catalog snapshot; '
        'JLCPCB prices, fees and promotions change and are not queried',
        'component cost uses catalog price1 per unit; quantity price breaks, reel and attrition fees are not modelled',
        'solder joints count numbered smd/thru_hole board pads of BOM designators; shipping, tax, '
        'panelisation, surface finish, colour and other options are outside the table',
    ]
    joints = fields['joints']
    if joints['tht_excluded_per_board']:
        ctx.result['limitations'].append(
            f"{joints['tht_excluded_per_board']} thru_hole via pad(s) per board with a native "
            f"{'/'.join(THERMAL_VIA_PROPERTIES)} property inside a same-numbered SMD pad are excluded from THT joints "
            '(see joints.per_designator tht_excluded); this is an unconfirmed pricing assumption, '
            'not verified against a JLCPCB quote')
    if joints['tht_unclassified_per_board']:
        ctx.result['limitations'].append(
            f"{joints['tht_unclassified_per_board']} thru_hole pad(s) per board in footprints with SMD pads, or "
            'with a via property but unproved containment in same-numbered SMD copper, are counted as THT joints '
            '(see joints.per_designator tht_unclassified)')
    if table['provenance']['values'] != 'observed':
        ctx.result['limitations'].append(f"pricing table values are {table['provenance']['values']}, "
                                         'not observed fabricator prices')


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()
