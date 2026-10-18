"""KiCad 10.0.x report-format quirk guards keyed on the pinned kicad-cli (AGENTEE-66).

ERC JSON coordinates. `kicad-cli sch erc --format json` in KiCad 10.0.6 and
earlier 10.0.x releases writes every violation item `pos` at 1/100 of its true
value whatever `--units` says (KiCad issue 25582); the text report is correct.
10.0.7 writes true values. The regime is chosen from the sandboxed
`kicad-cli version` output, never from the report alone:

    10.0.0 .. 10.0.6   scale_x100  positions multiplied by 100
    10.0.7 and later   native      positions used as written
    anything else      withheld    no positions; the reason is recorded

"Anything else" includes other series, release candidates, branch and nightly
builds (`10.0.6-45-g...`), a version run that failed and a report whose own
`kicad_version` disagrees with the sealed version output. Withheld locations
are information loss only: ERC violation counts do not depend on them. Under
scale_x100 the item description text is kept verbatim although lengths inside
it are at the same 1/100 scale; the recorded limitation says so.

BOM variants. `kicad-cli sch export bom --variant NAME` exits 0 and silently
exports the default variant when NAME is undefined, and a schematic whose
variant clause carries `exclude_from_bom` fails to load (the native token is
`in_bom`). `validate_bom_variants` refuses either before kicad-cli runs: every
requested name must be registered in the project file's `schematic.variants`.
"""

import json
import math
import re

from .symbol_library import LibraryError, parse_sexpr

RELEASE_RE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
# Last 10.0.x release whose ERC JSON writer used the 1/100 scale.
LAST_SCALED_PATCH = 6
SCALE = {'scale_x100': 100, 'native': 1}
DESCRIPTION_LIMITATION = ('item descriptions are kicad-cli text verbatim; lengths quoted in them are '
                          'at the uncorrected 1/100 scale')
# Schematic internal unit is 100 nm; corrected values are rounded to it.
MM_DECIMALS = 4
# Variant-clause tokens KiCad 10.0.7 writes; anything else is reported, and
# `exclude_from_bom` is refused because kicad-cli cannot load the schematic.
VARIANT_TOKENS = frozenset(('name', 'dnp', 'exclude_from_sim', 'in_bom', 'on_board', 'in_pos_files', 'field'))


class VariantError(ValueError):
    pass


def erc_coordinate_regime(version):
    """Coordinate regime for one `kicad-cli version` output line."""
    record = {'version': version, 'regime': 'withheld', 'scale': None}
    match = RELEASE_RE.fullmatch(version) if isinstance(version, str) else None
    if not match:
        record['reason'] = (f'kicad-cli version {version!r} is not a MAJOR.MINOR.PATCH release; '
                            'its ERC JSON coordinate scale is unclassified')
        return record
    major, minor, patch = map(int, match.groups())
    if (major, minor) != (10, 0):
        record['reason'] = (f'kicad-cli {version} is outside the classified 10.0.x series; '
                            'its ERC JSON coordinate scale is unclassified')
        return record
    record['regime'] = 'scale_x100' if patch <= LAST_SCALED_PATCH else 'native'
    record['scale'] = SCALE[record['regime']]
    return record


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def erc_locations(report, regime):
    """(locations, coordinates) for a loaded ERC JSON report under `regime`.

    `locations` lists every violation with its items; items carry `pos_mm`
    only when the coordinates are trusted. `coordinates` states the outcome.
    """
    coordinates = {**regime, 'report_kicad_version': report.get('kicad_version'),
                   'coordinate_units': report.get('coordinate_units')}
    reasons = [regime['reason']] if regime['regime'] == 'withheld' else []
    if not reasons and report.get('kicad_version') != regime['version']:
        reasons.append(f"ERC report kicad_version {report.get('kicad_version')!r} disagrees with the "
                       f"sealed kicad-cli version {regime['version']!r}")
    if not reasons and report.get('coordinate_units') != 'mm':
        reasons.append(f"ERC report coordinate_units {report.get('coordinate_units')!r} is not 'mm'")
    locations = []
    for sheet in report.get('sheets', []):
        for violation in sheet.get('violations', []) if isinstance(sheet, dict) else []:
            items = violation.get('items') if isinstance(violation, dict) else None
            entry = {'sheet': sheet.get('path'), 'type': violation.get('type') if isinstance(violation, dict) else None,
                     'severity': violation.get('severity') if isinstance(violation, dict) else None, 'items': []}
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                pos = item.get('pos')
                entry['items'].append({'uuid': item.get('uuid'), 'description': item.get('description'),
                                       'pos': [pos.get('x'), pos.get('y')] if isinstance(pos, dict) else None})
            if not isinstance(items, list) or not entry['items']:
                reasons.append(f"violation {entry['type']!r} on sheet {entry['sheet']!r} has no located items")
            locations.append(entry)
    if not reasons and any(not (isinstance(i['pos'], list) and all(map(_number, i['pos'])))
                           for v in locations for i in v['items']):
        reasons.append('ERC report has an item without a finite numeric x/y position')
    for violation in locations:
        for item in violation['items']:
            pos = item.pop('pos')
            if not reasons:
                item['pos_mm'] = [round(value * regime['scale'], MM_DECIMALS) for value in pos]
    coordinates['status'] = 'withheld' if reasons else 'trusted'
    if not reasons and regime['regime'] == 'scale_x100':
        coordinates['limitation'] = DESCRIPTION_LIMITATION
    if reasons:
        coordinates['scale'] = None
        coordinates['reason'] = '; '.join(dict.fromkeys(reasons))
    return locations, coordinates


def variant_registry(project_bytes):
    """Variant names registered in a `.kicad_pro` (`schematic.variants[].name`)."""
    try:
        project = json.loads(project_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise VariantError(f'project file is not valid JSON ({exc})') from exc
    schematic = project.get('schematic', {}) if isinstance(project, dict) else None
    if not isinstance(schematic, dict):
        raise VariantError('project file has no schematic settings object')
    entries = schematic.get('variants', [])
    if not isinstance(entries, list):
        raise VariantError('project schematic.variants is not a list')
    names = []
    for entry in entries:
        name = entry.get('name') if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or name in names:
            raise VariantError(f'project schematic.variants has a missing, empty or duplicate name: {entry!r}')
        names.append(name)
    return names


def schematic_variant_clauses(data):
    """(names, problems) from every `(variant (name ...) ...)` clause in one schematic."""
    try:
        root = parse_sexpr(data)
    except LibraryError as exc:
        raise VariantError(f'schematic is unreadable ({exc})') from exc
    names, problems, stack = set(), [], [root]
    while stack:
        node = stack.pop()
        if node and node[0] == 'variant':
            name = [c for c in node[1:] if isinstance(c, list) and c and c[0] == 'name']
            label = name[0][1] if len(name) == 1 and len(name[0]) == 2 and isinstance(name[0][1], str) else None
            if label is None:
                problems.append('a variant clause has no single name')
            else:
                names.add(label)
            for child in node[1:]:
                token = child[0] if isinstance(child, list) and child else child
                if token == 'exclude_from_bom':
                    problems.append(f'variant {label!r} uses exclude_from_bom, which kicad-cli cannot load '
                                    '(the variant token is in_bom)')
                elif token not in VARIANT_TOKENS:
                    problems.append(f'variant {label!r} has unrecognised token {token!r}')
        stack.extend(c for c in node[1:] if isinstance(c, list))
    return names, problems


def validate_bom_variants(project_path, schematic_paths, requested):
    """Refuse requested BOM variants unless every one is registered; return the record.

    Raises VariantError naming each undefined variant or unloadable clause,
    so the caller can refuse before any kicad-cli BOM export runs.
    """
    if (not isinstance(requested, (list, tuple)) or not requested or
            any(not isinstance(n, str) or not n for n in requested) or len(set(requested)) != len(requested)):
        raise VariantError(f'requested variants must be distinct nonempty names, got {requested!r}')
    try:
        registered = variant_registry(project_path.read_bytes())
    except OSError as exc:
        raise VariantError(f'project file {project_path.name} unreadable ({exc}); variants cannot be validated') from exc
    used, problems = set(), []
    for path in schematic_paths:
        try:
            names, found = schematic_variant_clauses(path.read_bytes())
        except OSError as exc:
            raise VariantError(f'schematic {path.name} unreadable ({exc})') from exc
        used |= names
        problems += [f'{path.name}: {p}' for p in found]
    unregistered = sorted(used - set(registered))
    undefined = [n for n in requested if n not in registered]
    if undefined:
        problems.insert(0, f'variant(s) {undefined} are not registered in {project_path.name} '
                           f'(registered: {registered}); kicad-cli would silently export the default variant')
    if problems:
        raise VariantError('; '.join(problems))
    return {'requested': list(requested), 'registered': registered,
            'schematic_clause_variants': sorted(used), 'unregistered_clause_variants': unregistered}
