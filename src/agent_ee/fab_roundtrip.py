"""Narrow, fail-closed KiCad Gerber/Excellon conservation against sealed geometry.

This is a count/outline check, not an artwork renderer. Unsupported syntax is
reported at its source line, never skipped. All dimensions stay integer nm.
"""
import re
from collections import Counter
from pathlib import PurePosixPath

from . import fab_consistency as package
from .storage import digest, read_json, write_json

PREDICATES = ['fab-package:roundtrip/1']
TOLERANCE_NM = 1000
ATTRIBUTE = re.compile(r'(?:TF\.(?:GenerationSoftware|CreationDate|ProjectId|SameCoordinates|FileFunction|FilePolarity)|TA\.AperFunction|TO\.(?:P|N|C)),[^*%]+')
DECIMAL = r'[+-]?\d{1,9}(?:\.\d{1,6})?'


class ParseError(ValueError):
    def __init__(self, execution, path, line, message):
        self.execution = execution
        self.path = path
        self.line = line
        super().__init__(f'{path}:{line}: {message}')


def lines(data, path):
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ParseError('malformed', path, data[:exc.start].count(b'\n') + 1,
                         'non-ASCII fabrication syntax') from exc
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if line.strip()]


def decimal_nm(text):
    # The caller validates the decimal grammar before conversion.
    sign = -1 if text.startswith('-') else 1
    whole, _, fraction = text.lstrip('+-').partition('.')
    return sign * (int(whole) * 1000000 + int(fraction.ljust(6, '0')))


def parse_gerber(data, path):
    """KiCad absolute 4.6 mm, linear positive plots, C/R/O apertures and X2.

    Regions, arcs, macros, repeats, transforms and clear polarity deliberately
    remain unsupported. Bounds are drawn centreline bounds (no aperture width).
    """
    source = lines(data, path)
    apertures, flashes, points = {}, [], []
    selected = position = function = None
    aperture_function = None
    units = coordinate_format = ended = linear = False
    draws = 0
    for number, line in source:
        def error(message, execution='malformed'):
            raise ParseError(execution, path, number, message)
        if ended:
            error('content after M02')
        if line.startswith('G04 ') and line.endswith('*'):
            if line.startswith('G04 #@! '):
                attr = line[len('G04 #@! '):-1]
                if not ATTRIBUTE.fullmatch(attr) and attr != 'TD':
                    error('unsupported X2 comment attribute', 'unsupported')
                if attr.startswith('TF.FileFunction,'):
                    if function is not None:
                        error('duplicate FileFunction')
                    function = attr.split(',', 1)[1]
            continue
        if line.startswith('%') and line.endswith('*%'):
            command = line[1:-2]
            if command == 'FSLAX46Y46':
                if coordinate_format:
                    error('duplicate coordinate format')
                coordinate_format = True
            elif command == 'MOMM':
                if units:
                    error('duplicate units')
                units = True
            elif command == 'MOIN':
                error('inch units forbidden; expected mm')
            elif command == 'LPD':
                pass
            elif ATTRIBUTE.fullmatch(command):
                if command.startswith('TF.FileFunction,'):
                    if function is not None:
                        error('duplicate FileFunction')
                    function = command.split(',', 1)[1]
                elif command.startswith('TA.AperFunction,'):
                    aperture_function = command.split(',', 1)[1]
            elif command == 'TD':
                aperture_function = None
            elif match := re.fullmatch(r'ADD(\d+)([CRO]),([^*%]+)', command):
                code, shape, dimensions = match.groups()
                code = int(code)
                values = dimensions.split('X')
                if len(values) != (1 if shape == 'C' else 2):
                    error('aperture holes or modifiers unsupported', 'unsupported')
                if not all(re.fullmatch(DECIMAL, v) and decimal_nm(v) > 0 for v in values):
                    error('invalid or non-finite aperture dimensions')
                if code < 10 or code in apertures:
                    error('duplicate or invalid aperture code')
                apertures[code] = {'shape': shape, 'size_nm': [decimal_nm(v) for v in values],
                                   'function': aperture_function}
            else:
                unsupported = command.startswith(('AM', 'ADD', 'SR', 'FS', 'LP', 'LM', 'LR', 'LS', 'AB', 'TF.', 'TA.', 'TO.', 'TD'))
                error(f'unknown or unsupported extended construct {command!r}',
                      'unsupported' if unsupported else 'malformed')
            continue
        if line == 'G01*':
            linear = True
        elif match := re.fullmatch(r'D(\d+)\*', line):
            selected = int(match[1])
            if selected not in apertures:
                error('selection of undefined aperture')
        elif match := re.fullmatch(r'(?:X([+-]?\d{1,10}))?(?:Y([+-]?\d{1,10}))?D0([123])\*', line):
            x, y, operation = match.groups()
            if not units or not coordinate_format or selected is None or not linear:
                error('geometry before mm units, format, linear mode or aperture')
            if position is None and (x is None or y is None):
                error('first position requires X and Y')
            target = (int(x) if x is not None else position[0],
                      int(y) if y is not None else position[1])
            if operation == '1':
                if position is None:
                    error('draw without starting position')
                points.extend((position, target))
                draws += 1
            elif operation == '3':
                flashes.append({'aperture': selected, 'position_nm': list(target)})
                points.append(target)
            position = target
        elif line == 'M02*':
            ended = True
        else:
            unsupported = line.startswith(('G02', 'G03', 'G36', 'G37', 'G74', 'G75', 'G90', 'G91', '%AM'))
            error(f'unknown or unsupported command or construct {line!r}',
                  'unsupported' if unsupported else 'malformed')
    if not ended or not units or not coordinate_format:
        raise ParseError('malformed', path, source[-1][0] if source else 1,
                         'missing M02, mm units or absolute 4.6 format')
    bounds = ([min(p[0] for p in points), min(p[1] for p in points),
               max(p[0] for p in points), max(p[1] for p in points)] if points else None)
    return {'file_function': function, 'apertures': apertures, 'flashes': flashes,
            'draws': draws, 'bounds_nm': bounds, 'units': 'mm'}


def parse_excellon(data, path):
    """KiCad separated plating, metric decimal, absolute round drill hits only."""
    source = lines(data, path)
    tools, hits = {}, Counter()
    header = units = absolute = drill_mode = ended = False
    selected = function = None
    for number, line in source:
        def error(message, execution='malformed'):
            raise ParseError(execution, path, number, message)
        if ended:
            error('content after M30')
        if line.startswith(';'):
            if line.startswith('; #@! '):
                attr = line[len('; #@! '):]
                if not ATTRIBUTE.fullmatch(attr):
                    error('unsupported X2 drill attribute', 'unsupported')
                if attr.startswith('TF.FileFunction,'):
                    if function is not None:
                        error('duplicate FileFunction')
                    function = attr.split(',', 1)[1]
            continue
        if line == 'M48' and not header and not units:
            header = True
        elif line == 'FMAT,2' and header:
            pass
        elif line == 'METRIC' and header and not units:
            units = True
        elif line.startswith('INCH') or line == 'M72':
            error('inch units forbidden; expected mm')
        elif match := re.fullmatch(r'T(\d+)C(' + DECIMAL + ')', line):
            code, diameter = int(match[1]), decimal_nm(match[2])
            if not header or not units or diameter <= 0 or code in tools or code < 1:
                error('invalid or duplicate drill tool declaration')
            tools[code] = diameter
        elif line == '%' and header and units:
            header = False
        elif line == 'G90' and not header and units:
            absolute = True
        elif line == 'G05' and absolute:
            drill_mode = True
        elif match := re.fullmatch(r'T(\d+)', line):
            selected = int(match[1])
            if header or selected not in tools:
                error('undefined drill tool or selection in header')
        elif match := re.fullmatch(r'X(' + DECIMAL + ')Y(' + DECIMAL + ')', line):
            if header or not units or not absolute or not drill_mode or selected is None:
                error('hit before metric absolute drill mode or tool')
            decimal_nm(match[1]), decimal_nm(match[2])
            hits[tools[selected]] += 1
        elif line == 'M30' and not header and units and absolute and drill_mode:
            ended = True
        else:
            unsupported = (line.startswith(('G00', 'G01', 'G85', 'G91', 'M15', 'M16', 'R', 'ICI,'))
                           or 'G85' in line)
            error(f'unknown or unsupported command or construct {line!r}',
                  'unsupported' if unsupported else 'malformed')
    if not ended:
        raise ParseError('malformed', path, source[-1][0] if source else 1, 'missing M30')
    return {'file_function': function, 'tools_nm': tools, 'histogram': hits, 'units': 'mm'}


def capability(params, scope, prerequisite, captured):
    package.capability(params, scope, prerequisite, captured)
    return list(PREDICATES)


def probe_facts(ctx):
    from .checks import CheckError
    ids, _ = package.geometry_footprints(ctx)
    facts = [{key: read_json(ctx.run_dir / 'work' / gid / 'geometry.json').get(key)
              for key in ('copper_layers', 'outline', 'pads', 'tracks')} for gid in ids]
    if any(fact != facts[0] for fact in facts[1:]):
        raise CheckError('malformed', 'sealed geometry checks disagree on fabrication geometry')
    fact = facts[0]
    if (not isinstance(fact['copper_layers'], list) or not fact['copper_layers'] or
            not isinstance(fact['outline'], dict) or not isinstance(fact['pads'], list) or
            not isinstance(fact['tracks'], list)):
        raise CheckError('unsupported', 'sealed geometry lacks fabrication facts; rerun the geometry probe')
    return ids, fact


def expected_counts(facts):
    """Refuse incomplete probes and slots; never round a hole to a circle."""
    copper = facts['copper_layers']
    pads, vias = Counter(), Counter()
    holes = {'pth': Counter(), 'npth': Counter()}
    for pad in facts['pads']:
        if ('flash_layers' not in pad or 'drill_nm' not in pad or
                not isinstance(pad['flash_layers'], list) or
                not isinstance(pad['drill_nm'], list) or len(pad['drill_nm']) != 2):
            raise ValueError('probe pad lacks exact drill or flash-layer facts')
        if not set(pad['flash_layers']) <= set(copper):
            raise ValueError('probe pad flashes on an unknown copper layer')
        pads.update(pad['flash_layers'])
        dx, dy = pad['drill_nm']
        if any(type(n) is not int or n < 0 for n in (dx, dy)):
            raise ValueError('invalid probe drill dimensions')
        if dx or dy:
            if dx != dy:
                raise ValueError('slotted holes unsupported')
            if pad.get('attribute') not in ('PTH', 'NPTH'):
                raise ValueError('unknown drilled pad plating')
            holes[pad['attribute'].lower()][dx] += 1
    for track in facts['tracks']:
        if track.get('type') != 'via':
            continue
        if (track.get('layers') != [copper[0], copper[-1]] or
                type(track.get('drill_nm')) is not int or track['drill_nm'] <= 0 or
                not isinstance(track.get('flash_layers'), list) or
                not set(track['flash_layers']) <= set(copper)):
            raise ValueError('unsupported via span or missing exact drill/flash-layer facts')
        holes['pth'][track['drill_nm']] += 1
        vias.update(track['flash_layers'])
    outline = facts['outline']
    if outline.get('status') != 'proven' or not outline.get('polygons'):
        raise ValueError('board outline is missing or unsupported')
    points = [p for poly in outline['polygons'] for p in poly]
    if not all(isinstance(p, list) and len(p) == 2 and all(type(n) is int for n in p) for p in points):
        raise ValueError('invalid probe outline coordinates')
    bounds = [min(p[0] for p in points), -max(p[1] for p in points),
              max(p[0] for p in points), -min(p[1] for p in points)]
    return pads, vias, holes, bounds


def histogram_rows(histogram):
    return [{'diameter_nm': diameter, 'count': count} for diameter, count in sorted(histogram.items())]


def histograms_match(expected, actual):
    """Pair sorted hole diameters within 1 um, preserving multiplicity.

    KiCad prints Excellon tools to three decimals, so distinct native diameters
    can share an output tool. Sorted matching supports that loss without
    allocating one entry per hole or accepting a dropped hit.
    """
    if sum(expected.values()) != sum(actual.values()):
        return False
    pending = iter(sorted(expected.items()))
    diameter, count = next(pending, (0, 0))
    for output, hits in sorted(actual.items()):
        while hits:
            if not count or abs(diameter - output) > TOLERANCE_NM:
                return False
            matched = min(count, hits)
            count -= matched
            hits -= matched
            if not count:
                diameter, count = next(pending, (0, 0))
    return True


def check_fab_roundtrip(ctx):
    from .checks import CheckError
    eid, _, export_result, stage = package.export_prerequisite(ctx)
    ids, facts = probe_facts(ctx)
    details = ctx.result['details']
    defects, gaps = [], []
    details.update(export_check=eid, geometry_checks=ids, defects=defects, coverage_gaps=gaps,
                   tolerance_nm=TOLERANCE_NM)

    def defect(code, locator):
        defects.append({'code': code, 'locator': locator})

    try:
        manifest = package.load_manifest(ctx, stage)
        details['manifest_sha256'] = digest(stage / 'manifest.json')
        details['tool'] = package.verify_tool(ctx, eid, export_result, manifest['tool'], gaps)
        missing = []
        found = package.inventory(stage, manifest, missing)
        board = package.board_facts(ctx)
        enabled = package.reconcile_layers(manifest, board)
        if facts['copper_layers'] != board['copper']:
            package.fail('native', 'sealed probe copper stack differs from the captured board')
        try:
            pads, vias, holes, bounds = expected_counts(facts)
        except (ValueError, KeyError, TypeError) as exc:
            raise CheckError('unsupported', str(exc)) from exc
        for rel in missing:
            defect('missing_file', f'missing package file {rel}')
        plots, by_layer = [], {}
        for rel in sorted(r for r in found if r.startswith('gerbers/')):
            plot = parse_gerber(package.read_text(stage, rel, 'plot'), rel)
            layer = package.plot_layer(plot['file_function'], len(board['copper'])) if plot['file_function'] else None
            if layer is None:
                gaps.append({'detail': f'{rel}: no supported FileFunction layer identity'})
            elif layer not in enabled or layer in by_layer:
                package.fail('plots', f'{rel}: duplicate or unexpected plot layer {layer}')
            else:
                by_layer[layer] = rel
            record = {'path': rel, 'layer': layer, 'bounds_nm': plot['bounds_nm'],
                      'aperture_count': len(plot['apertures']), 'flash_count': len(plot['flashes']),
                      'apertures': [{'code': code, **aperture} for code, aperture in sorted(plot['apertures'].items())]}
            plots.append(record)
            if layer in board['copper']:
                counts = Counter()
                classified = True
                for flash in plot['flashes']:
                    function = plot['apertures'][flash['aperture']]['function']
                    kind = function.split(',')[0] if function else None
                    if kind not in ('ComponentPad', 'SMDPad', 'ViaPad'):
                        classified = False
                        gaps.append({'detail': f'{rel}: flash aperture lacks a supported pad/via function'})
                    else:
                        counts['via' if kind == 'ViaPad' else 'pad'] += 1
                record.update(expected_pad_flashes=pads[layer], expected_via_flashes=vias[layer],
                              pad_flashes=counts['pad'], via_flashes=counts['via'])
                if classified and (counts['pad'], counts['via']) != (pads[layer], vias[layer]):
                    defect('flash_count', f'{rel} ({layer}): pad/via flashes {counts["pad"]}/{counts["via"]}; '
                           f'probe requires {pads[layer]}/{vias[layer]}')
            if layer == 'Edge.Cuts':
                record['expected_bounds_nm'] = bounds
                if plot['bounds_nm'] is None or any(abs(a-b) > TOLERANCE_NM for a, b in zip(plot['bounds_nm'], bounds)):
                    defect('outline_bounds', f'{rel} (Edge.Cuts): bounds {plot["bounds_nm"]}; probe {bounds}, tolerance 1000 nm')
        details['plots'] = plots
        if all(plot['layer'] is not None for plot in plots):
            for layer in sorted(enabled - set(by_layer)):
                defect('missing_layer', f'missing plot for {layer} ({layer.replace(".", "_")})')
        drills = []
        expected_drills = set()
        for role, name in manifest['drill'].items():
            rel = name if '/' in name else f'drill/{name}'
            if PurePosixPath(rel).parent.as_posix() != 'drill':
                package.fail('drill', f'{role} drill path outside drill/: {rel}')
            expected_drills.add(rel)
            if rel not in found:
                if rel not in missing:
                    defect('missing_file', f'missing {role.upper()} drill file {rel}')
                continue
            drill = parse_excellon(package.read_text(stage, rel, 'drill'), rel)
            expected_function = f'{"Plated" if role == "pth" else "NonPlated"},1,{len(board["copper"])},{role.upper()}'
            if drill['file_function'] != expected_function:
                package.fail('drill', f'{rel}: plating/span attribute differs from {expected_function}')
            actual = drill['histogram']
            drills.append({'path': rel, 'role': role, 'histogram': histogram_rows(actual),
                           'expected_histogram': histogram_rows(holes[role]),
                           'tools': [{'code': code, 'diameter_nm': diameter}
                                     for code, diameter in sorted(drill['tools_nm'].items())]})
            if not histograms_match(holes[role], actual):
                defect('drill_histogram', f'{rel} ({role.upper()}): drill diameter/plating histogram differs from probe')
        if {r for r in found if r.startswith('drill/') and r.endswith('.drl')} - expected_drills:
            package.fail('drill', 'unexpected Excellon file outside manifest PTH/NPTH roles')
        details['drill'] = drills
        # Missing files are already established defects. For an intact staging
        # tree require the zip to contain these exact parsed bytes as well.
        if not missing and not any(d['code'] in ('missing_file', 'missing_layer') for d in defects):
            details['zip_members'] = package.check_zip(stage, found)
    except ParseError as exc:
        details['parser_error'] = {'path': exc.path, 'line': exc.line, 'message': str(exc)}
        write_json(ctx.work / 'fab-roundtrip.json', details)
        ctx.evidence('fab-roundtrip.json')
        raise CheckError(exc.execution, str(exc)) from exc
    except package.Malformed as exc:
        raise CheckError('malformed', str(exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise CheckError('malformed', f'fab roundtrip read failed: {exc}') from exc
    write_json(ctx.work / 'fab-roundtrip.json', details)
    ctx.evidence('fab-roundtrip.json')
    ctx.result['limitations'].append('Count/diameter/plating conservation and straight outline bounds only; '
                                     'artwork, hole positions, copper geometry, DFM and drill maps are not compared.')
    if defects:
        ctx.result.update(execution='ok', outcome='defect',
                          location='; '.join(d['locator'] for d in defects[:50]),
                          summary=f'{len(defects)} fab roundtrip conservation defect(s)')
    elif gaps:
        raise CheckError('unsupported', '; '.join(g['detail'] for g in gaps[:5]))
    else:
        ctx.result.update(execution='ok', outcome='pass',
                          summary=f'fab roundtrip conserved {len(plots)} layers, PTH/NPTH drill histograms, '
                                  'pad/via flashes and outline bounds within 1 um')
