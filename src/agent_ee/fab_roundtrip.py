"""Narrow, fail-closed KiCad Gerber/Excellon conservation against sealed geometry.

This is a count/outline check, not an artwork renderer. Unsupported syntax is
reported at its source line, never skipped. All dimensions stay integer nm.

Params are exactly one of `{export_check}` (with optional `model_libraries`), the
staging tree of an earlier `fab_export`, or `{package_check}` (AGENTEE-524), the
sealed release of an earlier passing `fab_package`. In package mode the check
follows the package (`fab_package.require_last` admits it) and the package never
reads it. The sealed record is recomputed (`artifact_record`) and must equal the
one the package result recorded. The Gerber and drill inputs are then the members
of the sealed `gerbers.zip`, parsed from the bytes whose sha256 matched the sealed
record; every member must equal its sealed staged file and every sealed drill file
its member. `details.compared` retains those digests. In both modes the expected
geometry is the independent sealed pcbnew `geometry` probe of the captured board.
"""

from .check_registry import register as _register_check
import hashlib
import io
import math
import re
import zipfile
from collections import Counter
from fractions import Fraction
from pathlib import PurePosixPath

from . import fab_consistency as package
from .storage import digest, read_json, write_json

PREDICATES = ['fab-package:roundtrip/1']
PACKAGE_PREDICATES = ['fab-package:sealed-roundtrip/1']
TOLERANCE_NM = 1000
ATTRIBUTE = re.compile(r'(?:TF\.(?:GenerationSoftware|CreationDate|ProjectId|SameCoordinates|FileFunction|FilePolarity)|TA\.AperFunction|TO\.(?:P|N|C)),[^*%]+')
DECIMAL = r'[+-]?\d{1,9}(?:\.\d{1,6})?'
MACRO_NAME = r'[A-Za-z_.$][A-Za-z0-9_.$]{0,126}'
# KiCad 10.0.7 emits exactly these macro primitives (code: parameter count):
# circle 1, vector line 20, center line 21, polygon 5 and outline 4 (variable).
PRIMITIVE_ARITY = {1: (4, 5), 20: (7, 7), 21: (6, 6), 5: (6, 6)}
# X2 aperture functions KiCad gives pcbnew pads (ViaPad is counted separately).
PAD_FUNCTIONS = ('ComponentPad', 'SMDPad', 'BGAPad', 'ConnectorPad', 'HeatsinkPad', 'CastellatedPad',
                 'TestPad', 'FiducialPad', 'WasherPad')


class ParseError(ValueError):
    def __init__(self, execution, path, line, message):
        self.execution = execution
        self.path = path
        self.line = line
        super().__init__(f'{path}:{line}: {message}')


class MacroError(ValueError):
    def __init__(self, execution, line, message):
        self.execution = execution
        self.line = line
        super().__init__(message)


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


def expression(text, variables):
    """Exact Gerber macro arithmetic: + - x / parentheses, unary sign and $n."""
    tokens = [t for t in re.findall(r'\$\d+|\d+(?:\.\d*)?|\.\d+|\S', text)]
    position = 0

    def take(expected=None):
        nonlocal position
        token = tokens[position] if position < len(tokens) else None
        if expected is not None and (token is None or token not in expected):
            return None
        position += token is not None
        return token

    def atom():
        token = take()
        if token in ('+', '-'):
            value = atom()
            return -value if token == '-' else value
        if token == '(':
            value = additive()
            if take((')',)) is None:
                raise ValueError(f'unbalanced parentheses in {text!r}')
            return value
        if token and token[0] == '$':
            if int(token[1:]) not in variables:
                raise ValueError(f'undefined macro variable {token}')
            return variables[int(token[1:])]
        if token and re.fullmatch(r'\d+(?:\.\d*)?|\.\d+', token):
            return Fraction(token)
        raise ValueError(f'invalid macro expression {text!r}')

    def multiplicative():
        value = atom()
        while (operator := take(('x', 'X', '/'))) is not None:
            right = atom()
            if operator == '/' and not right:
                raise ValueError(f'division by zero in {text!r}')
            value = value / right if operator == '/' else value * right
        return value

    def additive():
        value = multiplicative()
        while (operator := take(('+', '-'))) is not None:
            right = multiplicative()
            value = value + right if operator == '+' else value - right
        return value

    value = additive()
    if position != len(tokens):
        raise ValueError(f'invalid macro expression {text!r}')
    return value


def rotate(point, degrees):
    """Counter-clockwise about the macro origin; quarter turns stay exact."""
    x, y = point
    quarter = degrees / 90
    if quarter == int(quarter):
        return [(x, y), (-y, x), (-x, -y), (y, -x)][int(quarter) % 4]
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    return (x * c - y * s, x * s + y * c)


def macro_statements(name, body, path, closing_line):
    """Split a macro body into (line, code, parameter texts); refuse what KiCad never emits."""
    statements = []
    for number, text in body:
        if text.startswith('0') and (len(text) == 1 or text[1] == ' '):
            continue
        if text.startswith('$'):
            raise ParseError('unsupported', path, number, f'macro {name}: variable definition unsupported')
        code, *params = text.split(',')
        if not re.fullmatch(r'\d+', code):
            raise ParseError('malformed', path, number, f'macro {name}: invalid primitive {text!r}')
        code = int(code)
        if code not in PRIMITIVE_ARITY and code != 4:
            raise ParseError('unsupported', path, number,
                             f'macro {name}: primitive {code} unsupported (KiCad set is 1, 4, 5, 20, 21)')
        statements.append((number, code, params))
    if not statements:
        raise ParseError('malformed', path, closing_line, f'macro {name} has no primitives')
    return statements


def macro_shapes(name, statements, values):
    """Evaluate primitives with exact parameters into circles and polygons (aperture mm)."""
    variables = {i: value for i, value in enumerate(values, 1)}
    shapes = []
    for number, code, texts in statements:
        def fail(message, execution='malformed'):
            raise MacroError(execution, number, f'macro {name} primitive {code}: {message}')
        try:
            params = [expression(t, variables) for t in texts]
        except ValueError as exc:
            fail(str(exc))
        if code == 4:
            count = params[1] if len(params) >= 2 else None
            if count is None or count.denominator != 1 or count < 3 or len(params) != 2 * int(count) + 5:
                fail('outline vertex count does not match its coordinates')
        elif not PRIMITIVE_ARITY[code][0] <= len(params) <= PRIMITIVE_ARITY[code][1]:
            fail(f'{len(params)} parameters')
        if params[0] == 0:
            fail('clear exposure unsupported', 'unsupported')
        if params[0] != 1:
            fail('exposure must be 0 or 1')
        rotation = params[-1] if code != 1 or len(params) == 5 else Fraction(0)
        if code == 1:
            diameter, cx, cy = params[1:4]
            if diameter < 0:
                fail('negative diameter')
            shapes.append(('circle', rotate((cx, cy), rotation), diameter / 2))
            continue
        if code == 20:
            width, sx, sy, ex, ey = params[1:6]
            length = math.hypot(ex - sx, ey - sy)
            if width < 0:
                fail('negative width')
            if not length:
                shapes.append(('polygon', []))
                continue
            nx, ny = -(ey - sy) / length * width / 2, (ex - sx) / length * width / 2
            points = [(sx + nx, sy + ny), (ex + nx, ey + ny), (ex - nx, ey - ny), (sx - nx, sy - ny)]
        elif code == 21:
            width, height, cx, cy = params[1:5]
            if width < 0 or height < 0:
                fail('negative size')
            points = [(cx + dx * width / 2, cy + dy * height / 2) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        elif code == 5:
            vertices, cx, cy, diameter = params[1:5]
            if vertices.denominator != 1 or not 3 <= vertices <= 12 or diameter < 0:
                fail('polygon needs 3..12 vertices and a non-negative diameter')
            points = [(cx + diameter / 2 * math.cos(2 * math.pi * k / vertices),
                       cy + diameter / 2 * math.sin(2 * math.pi * k / vertices)) for k in range(int(vertices))]
        else:
            coordinates = params[2:-1]
            points = list(zip(coordinates[0::2], coordinates[1::2]))
            if points[0] != points[-1]:
                fail('outline is not closed')
            points.pop()
        shapes.append(('polygon', [rotate(p, rotation) for p in points]))
    return shapes


def has_image(shape):
    """A positive-radius circle or a polygon with nonzero (shoelace) area."""
    if shape[0] == 'circle':
        return shape[2] > 0
    points = shape[1]
    return bool(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])))


def shapes_bbox_nm(shapes):
    """Integer-nm bounds of the shapes that have an image (degenerate primitives draw nothing)."""
    xs, ys = [], []
    for shape in filter(has_image, shapes):
        if shape[0] == 'circle':
            (cx, cy), r = shape[1], shape[2]
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        else:
            xs += [p[0] for p in shape[1]]
            ys += [p[1] for p in shape[1]]
    if not xs:
        return None  # only KiCad's zero-diameter region aperture, which never flashes
    return [round(v * 1000000) for v in (min(xs), min(ys), max(xs), max(ys))]


def standard_shapes(shape, size):
    """C/R/O apertures as the same circle/polygon shapes (mm, centred)."""
    def box(ax, ay):
        return ('polygon', [(-ax, -ay), (ax, -ay), (ax, ay), (-ax, ay)])
    if shape == 'C':
        return [('circle', (0, 0), size[0] / 2)]
    w, h = size
    if shape == 'R':
        return [box(w / 2, h / 2)]
    # Obround: two end circles joined by the rectangle between their centres.
    r = min(w, h) / 2
    if w == h:
        return [('circle', (0, 0), r)]
    dx, dy = w / 2 - r, h / 2 - r
    return [('circle', (-dx, -dy), r), ('circle', (dx, dy), r), box(dx or r, dy or r)]


def arc_bounds(start, end, offset, clockwise):
    """Bounding points of a G75 arc (start == end is a full circle)."""
    cx, cy = start[0] + offset[0], start[1] + offset[1]
    r0 = math.hypot(start[0] - cx, start[1] - cy)
    r1 = math.hypot(end[0] - cx, end[1] - cy)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)
    sweep = ((a0 - a1) if clockwise else (a1 - a0)) % (2 * math.pi)
    if start == end:
        sweep = 2 * math.pi
    points = [start, end]
    for quarter in range(4):
        angle = quarter * math.pi / 2
        travelled = ((a0 - angle) if clockwise else (angle - a0)) % (2 * math.pi)
        if travelled <= sweep:
            points.append((round(cx + r0 * math.cos(angle)), round(cy + r0 * math.sin(angle))))
    return r0, r1, points


def parse_gerber(data, path):
    """KiCad 10 absolute 4.6 mm plots: C/R/O and macro apertures, linear and G75
    arc draws, G36/G37 regions, positive polarity and the listed X2 attributes.

    Aperture macros (primitives 1, 4, 5, 20, 21) are evaluated with exact
    parameter arithmetic into circles/polygons; every flash carries the bounding
    box of that geometry. Repeats, transforms, clear polarity or exposure,
    single-quadrant arcs and other primitives stay unsupported. Bounds are drawn
    centreline bounds (no aperture width).
    """
    source = lines(data, path)
    apertures, shapes, macros, flashes, points, strokes, regions = {}, {}, {}, [], [], [], []
    selected = position = function = None
    aperture_function = None
    units = coordinate_format = ended = False
    mode = multi_quadrant = None
    macro = region = contour = None
    for number, line in source:
        def error(message, execution='malformed'):
            raise ParseError(execution, path, number, message)
        if ended:
            error('content after M02')
        if macro is not None or (match := re.fullmatch(r'%AM(' + MACRO_NAME + r')\*(.*)', line)):
            # %AM name, then '*'-terminated statements on this and later lines up to '%'.
            if macro is None:
                if region is not None:
                    error('aperture macro inside a region')
                if match[1] in macros:
                    error(f'duplicate aperture macro {match[1]}')
                macro, line = (match[1], []), match[2]
            closing = line.endswith('%')
            statement = line[:-1] if closing else line
            if statement and not statement.endswith('*'):
                error(f'macro {macro[0]}: statement without terminating *')
            macro[1].extend((number, text) for text in statement[:-1].split('*') if statement)
            if closing:
                macros[macro[0]] = macro_statements(macro[0], macro[1], path, number)
                macro = None
            continue
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
            elif command in ('TD', 'TD.AperFunction'):
                aperture_function = None
            elif region is not None and command.startswith('AD'):
                error('aperture definition inside a region')
            elif match := re.fullmatch(r'ADD(\d+)([CRO]),([^*%]+)', command):
                code, shape, dimensions = match.groups()
                code = int(code)
                values = dimensions.split('X')
                if len(values) != (1 if shape == 'C' else 2):
                    error('aperture holes or modifiers unsupported', 'unsupported')
                # KiCad selects a zero-diameter circle before regions; it may never flash.
                if not all(re.fullmatch(DECIMAL, v) and decimal_nm(v) >= (shape != 'C') for v in values):
                    error('invalid or non-finite aperture dimensions')
                if code < 10 or code in apertures:
                    error('duplicate or invalid aperture code')
                shapes[code] = standard_shapes(shape, [Fraction(v) for v in values])
                apertures[code] = {'shape': shape, 'size_nm': [decimal_nm(v) for v in values],
                                   'function': aperture_function, 'bbox_nm': shapes_bbox_nm(shapes[code])}
            elif match := re.fullmatch(r'ADD(\d+)(' + MACRO_NAME + r')(?:,([^*%]+))?', command):
                code, name, arguments = int(match[1]), match[2], match[3]
                if name not in macros:
                    # P (regular polygon) is a standard aperture KiCad does not emit.
                    error(f'aperture {code} uses undefined macro {name!r}',
                          'unsupported' if name == 'P' else 'malformed')
                if code < 10 or code in apertures:
                    error('duplicate or invalid aperture code')
                values = arguments.split('X') if arguments else []
                if not all(re.fullmatch(DECIMAL, v) for v in values):
                    error('invalid or non-finite macro parameters')
                try:
                    evaluated = macro_shapes(name, macros[name], [Fraction(v) for v in values])
                except MacroError as exc:
                    error(f'{exc} (macro line {exc.line})', exc.execution)
                if not any(has_image(s) for s in evaluated):
                    error(f'macro {name} evaluates to an empty image')
                shapes[code] = evaluated
                apertures[code] = {'shape': 'macro', 'macro': name, 'parameters': values,
                                   'primitives': len(evaluated), 'function': aperture_function,
                                   'bbox_nm': shapes_bbox_nm(evaluated)}
            else:
                unsupported = command.startswith(('AM', 'ADD', 'SR', 'FS', 'LP', 'LM', 'LR', 'LS', 'AB', 'TF.', 'TA.', 'TO.', 'TD'))
                error(f'unknown or unsupported extended construct {command!r}',
                      'unsupported' if unsupported else 'malformed')
            continue
        if line in ('G01*', 'G02*', 'G03*'):
            mode = line[1:3]
        elif line == 'G75*':
            multi_quadrant = True
        elif line == 'G36*':
            if region is not None:
                error('nested region')
            region, contour = {'function': aperture_function, 'line': number, 'contours': 0}, None
        elif line == 'G37*':
            if region is None:
                error('region end without G36')
            if contour is not None:
                close_contour(contour, region, regions, error)
            if not region['contours']:
                error('region without a closed contour')
            region = contour = None
        elif match := re.fullmatch(r'D(\d+)\*', line):
            if region is not None:
                error('aperture selection inside a region')
            selected = int(match[1])
            if selected not in apertures:
                error('selection of undefined aperture')
        elif match := re.fullmatch(r'(?:X([+-]?\d{1,10}))?(?:Y([+-]?\d{1,10}))?'
                                   r'(?:I([+-]?\d{1,10}))?(?:J([+-]?\d{1,10}))?D0([123])\*', line):
            x, y, i, j, operation = match.groups()
            if not units or not coordinate_format or mode is None or (region is None and selected is None):
                error('geometry before mm units, format, interpolation mode or aperture')
            if position is None and (x is None or y is None):
                error('first position requires X and Y')
            target = (int(x) if x is not None else position[0],
                      int(y) if y is not None else position[1])
            arc = operation == '1' and mode != '01'
            if (i is not None or j is not None) and not arc:
                error('I/J offsets outside a circular draw')
            if operation == '1':
                if position is None:
                    error('draw without starting position')
                if region is not None and contour is None:
                    error('region draw without a contour start')
                if arc:
                    if not multi_quadrant:
                        error('arc without G75 multi-quadrant mode', 'unsupported')
                    r0, r1, drawn = arc_bounds(position, target, (int(i or 0), int(j or 0)), mode == '02')
                    if not r0 or abs(r0 - r1) > TOLERANCE_NM:
                        error(f'arc radii {r0:.0f}/{r1:.0f} nm are inconsistent')
                else:
                    drawn = [position, target]
                points.extend(drawn)
                if region is not None:
                    contour['draws'] += 1
                    contour['points'].extend(drawn)
                else:
                    strokes.append((selected, 'arc' if arc else 'linear'))
            elif operation == '2':
                if region is not None:
                    if contour is not None:
                        close_contour(contour, region, regions, error)
                    contour = {'start': target, 'draws': 0, 'points': [target], 'line': number}
            else:
                if region is not None:
                    error('flash inside a region')
                if not any(has_image(shape) for shape in shapes[selected]):
                    error(f'flash of aperture D{selected} without an image')
                bbox = apertures[selected]['bbox_nm']
                flashes.append({'aperture': selected, 'position_nm': list(target),
                                'bbox_nm': [bbox[0] + target[0], bbox[1] + target[1],
                                            bbox[2] + target[0], bbox[3] + target[1]]})
                points.append(target)
            position = target
            if region is not None and contour is not None and operation == '1':
                contour['end'] = target
        elif line == 'M02*':
            if region is not None:
                error('M02 inside an open region')
            ended = True
        else:
            unsupported = line.startswith(('G74', 'G90', 'G91', '%AM', 'G02', 'G03'))
            error(f'unknown or unsupported command or construct {line!r}',
                  'unsupported' if unsupported else 'malformed')
    if not ended or not units or not coordinate_format:
        raise ParseError('malformed', path, source[-1][0] if source else 1,
                         'missing M02, mm units or absolute 4.6 format')
    bounds = ([min(p[0] for p in points), min(p[1] for p in points),
               max(p[0] for p in points), max(p[1] for p in points)] if points else None)
    return {'file_function': function, 'apertures': apertures, 'flashes': flashes,
            'draws': len(strokes), 'strokes': strokes, 'regions': regions,
            'bounds_nm': bounds, 'units': 'mm'}


def close_contour(contour, region, regions, error):
    """A contour must end where it started; an empty D02 contour has no image."""
    if not contour['draws']:
        return
    if contour.get('end') != contour['start']:
        error(f'region contour from line {contour["line"]} is not closed')
    xs, ys = [p[0] for p in contour['points']], [p[1] for p in contour['points']]
    region['contours'] += 1
    regions.append({'function': region['function'], 'line': contour['line'], 'draws': contour['draws'],
                    'bbox_nm': [min(xs), min(ys), max(xs), max(ys)]})


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
    if not isinstance(params, dict) or 'package_check' not in params:
        package.capability(params, scope, prerequisite, captured)
        return list(PREDICATES)
    if set(params) != {'package_check'}:
        raise ValueError('fab_roundtrip params must be {export_check} or exactly {package_check}')
    prerequisite(params['package_check'], ('fab_package',))
    package.earlier_geometry(scope, prerequisite, 'fab_roundtrip')
    captured(scope.get('board'))
    return PREDICATES + PACKAGE_PREDICATES


def package_prerequisite(ctx):
    """The earlier passing same-candidate `fab_package`, its staging and its recomputed sealed record."""
    from .checks import CheckError, SAFE_ID
    from .fab_package import artifact_record
    pid = ctx.params.get('package_check')
    if set(ctx.params) != {'package_check'} or not isinstance(pid, str) or not SAFE_ID.fullmatch(pid):
        raise CheckError('unsupported', 'fab_roundtrip params must be {export_check} or exactly '
                         '{package_check: <safe id>}')
    specs = ctx.scope['checks']
    position = next(i for i, s in enumerate(specs) if s is ctx.spec or s.get('id') == ctx.cid)
    spec = next((s for s in specs[:position] if s.get('id') == pid), None)
    path = ctx.run_dir / 'checks' / f'{pid}.json'
    if spec is None or spec.get('kind') != 'fab_package' or not path.is_file():
        raise CheckError('unsupported', 'fab_package prerequisite missing, later in scope order or not yet run')
    result = read_json(path)
    if (result.get('candidate_id') != ctx.manifest['candidate_id']
            or (result.get('execution'), result.get('outcome')) != ('ok', 'pass')):
        raise CheckError('unsupported', f'fab_package prerequisite {pid!r} did not pass '
                         f'({result.get("execution")}/{result.get("outcome")})')
    try:
        artifacts = artifact_record(ctx.run_dir, result)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'sealed package {pid!r} does not verify: {exc}') from exc
    if artifacts != result.get('details', {}).get('artifacts'):
        raise CheckError('malformed', f'sealed package {pid!r} differs from the record its result sealed '
                         f'(artifacts {artifacts["artifacts_id"][:12]})')
    stage = ctx.run_dir / 'work' / pid / 'fab'
    rel = f'work/{pid}/fab/manifest.json'
    if rel not in ctx.result['evidence']:
        ctx.result['evidence'].append(rel)
    return pid, result, stage, artifacts


def zip_target(name, expected, basenames):
    """The staged path a `gerbers.zip` member stands for, under the `check_zip` naming rules."""
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).is_absolute() or '\\' in name or any(p in ('', '.', '..') for p in parts):
        package.fail('zip', f'unsafe archive member {name!r}')
    if name in expected:
        return name
    return basenames[name][0] if '/' not in name and len(basenames.get(name, ())) == 1 else None


def sealed_members(stage, found, manifest, artifacts, pid):
    """{staged path: exact released bytes} from the sealed zip, plus the retained equality proof.

    The zip is read once and must match its sealed digest; each member must be a
    distinct staged Gerber/drill file with the manifest digest, and each sealed
    drill file must equal its member byte for byte. Raises Malformed otherwise.
    """
    sealed = artifacts['files']
    zip_rel = f'work/{pid}/fab/gerbers.zip'
    data = package.read_text(stage, 'gerbers.zip', 'zip')
    zip_sha = hashlib.sha256(data).hexdigest()
    if sealed.get(zip_rel) != zip_sha:
        package.fail('hash', f'{zip_rel} differs from its sealed digest')
    expected = {rel for rel in found if rel.startswith(('gerbers/', 'drill/'))}
    basenames = {}
    for rel in expected:
        basenames.setdefault(PurePosixPath(rel).name, []).append(rel)
    members, compared = {}, []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                rel = zip_target(info.filename, expected, basenames)
                if rel is None or rel in members:
                    package.fail('zip', f'archive member {info.filename!r} is extra or duplicated')
                body = archive.read(info)
                member_sha = hashlib.sha256(body).hexdigest()
                if member_sha != manifest['files'].get(rel):
                    package.fail('zip', f'archive member {info.filename!r} differs from sealed {rel}')
                record = {'member': info.filename, 'member_sha256': member_sha, 'staged_path': rel}
                sealed_path = f'work/{pid}/fab/{rel}'
                if sealed_path in sealed:
                    if (stage / rel).read_bytes() != body or sealed[sealed_path] != member_sha:
                        package.fail('zip', f'sealed {sealed_path} differs from archive member {info.filename!r}')
                    record.update(sealed_path=sealed_path, sealed_sha256=sealed[sealed_path])
                members[rel] = body
                compared.append(record)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        package.fail('zip', f'gerbers.zip unreadable ({exc})')
    if set(members) != expected:
        package.fail('zip', f'gerbers.zip omits {sorted(expected - set(members))}')
    unzipped = sorted(p for p in sealed if p.startswith(f'work/{pid}/fab/drill/')
                      and p[len(f'work/{pid}/fab/'):] not in members)
    if unzipped:
        package.fail('zip', f'sealed drill files absent from gerbers.zip {unzipped}')
    return members, {'gerbers_zip': zip_rel, 'gerbers_zip_sha256': zip_sha,
                     'members': sorted(compared, key=lambda r: r['staged_path'])}


def probe_facts(ctx):
    from .checks import CheckError
    ids, _ = package.geometry_footprints(ctx)
    facts = [{key: read_json(ctx.run_dir / 'work' / gid / 'geometry.json').get(key)
              for key in ('copper_layers', 'outline', 'pads', 'tracks', 'zone_fill_outlines',
                          'copper_conductor_shapes')} for gid in ids]
    if any(fact != facts[0] for fact in facts[1:]):
        raise CheckError('malformed', 'sealed geometry checks disagree on fabrication geometry')
    fact = facts[0]
    if (not isinstance(fact['copper_layers'], list) or not fact['copper_layers'] or
            not isinstance(fact['outline'], dict) or not isinstance(fact['pads'], list) or
            not isinstance(fact['tracks'], list) or not isinstance(fact['zone_fill_outlines'], dict) or
            not isinstance(fact['copper_conductor_shapes'], dict)):
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


def expected_copper(facts):
    """Per copper layer: track segments, arc tracks, zone fill outlines, netted
    copper shapes and per-layer pad extents in the Gerber frame (Y negated)."""
    copper = facts['copper_layers']
    segments, arcs = Counter(), Counter()
    for track in facts['tracks']:
        if track.get('type') == 'via':
            continue
        if track.get('type') not in ('track', 'pcb_arc') or track.get('layer') not in copper:
            raise ValueError('probe track of an unknown class or on an unknown copper layer')
        if track['type'] == 'track' and track.get('start_mm') == track.get('end_mm'):
            raise ValueError('zero-length track unsupported')
        (arcs if track['type'] == 'pcb_arc' else segments)[track['layer']] += 1
    fills, shapes = facts['zone_fill_outlines'], facts['copper_conductor_shapes']
    if (set(fills) != set(copper) or set(shapes) != set(copper) or
            not all(type(n) is int and n >= 0 for n in (*fills.values(), *shapes.values()))):
        raise ValueError('invalid probe zone fill or copper shape facts')
    extents = {layer: [] for layer in copper}
    for pad in facts['pads']:
        boxes = pad.get('flash_bbox_nm')
        if (not isinstance(boxes, dict) or set(boxes) != set(pad['flash_layers']) or
                not all(isinstance(b, list) and len(b) == 4 and all(type(n) is int for n in b)
                        for b in boxes.values())):
            raise ValueError('probe pad lacks exact per-layer flash extents')
        for layer, (x0, y0, x1, y1) in boxes.items():
            extents[layer].append(([x0, -y1, x1, -y0], f"{pad.get('ref')}.{pad.get('pad')}"))
    return segments, arcs, fills, shapes, extents


def unmatched_extents(expected, flashes):
    """Pair probe pad extents with flashed geometry extents within 1 um.

    Exact pairs are taken first; the remainder pairs greedily. Returns the
    unpaired probe pads and the positions of unpaired flashes.
    """
    exact, used = {}, set()
    for index, (box, _) in enumerate(flashes):
        exact.setdefault(tuple(box), []).append(index)
    missing = []
    for box, label in expected:
        candidates = exact.get(tuple(box))
        if candidates:
            used.add(candidates.pop())
        else:
            missing.append((box, label))
    leftover = [i for i in range(len(flashes)) if i not in used]
    unpaired = []
    for box, label in missing:
        match = next((i for i in leftover if max(abs(a - b) for a, b in zip(box, flashes[i][0])) <= TOLERANCE_NM),
                     None)
        if match is None:
            unpaired.append(label)
        else:
            leftover.remove(match)
    return unpaired, [flashes[i][1] for i in leftover]


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


def compare_copper(plot, rel, layer, record, defect, gaps, expected):
    """Pad/via flashes and extents, conductor tracks/arcs and zone regions of one copper plot."""
    def kind(function):
        return function.split(',')[0] if function else None
    counts, conductor, other = Counter(), Counter(), Counter()
    pad_flashes, classified = [], True
    for flash in plot['flashes']:
        aperture = plot['apertures'][flash['aperture']]
        function = kind(aperture['function'])
        if function == 'ViaPad':
            counts['via'] += 1
        elif function in PAD_FUNCTIONS:
            counts['pad'] += 1
            counts['macro'] += aperture['shape'] == 'macro'
            pad_flashes.append((flash['bbox_nm'], flash['position_nm']))
        else:
            classified = False
            gaps.append({'detail': f'{rel}: flash aperture lacks a supported pad/via function'})
    for code, stroke in plot['strokes']:
        (conductor if kind(plot['apertures'][code]['function']) == 'Conductor' else other)[stroke] += 1
    regions = Counter('conductor' if kind(r['function']) == 'Conductor' else
                      'pad' if kind(r['function']) in (*PAD_FUNCTIONS, 'ViaPad') else 'other'
                      for r in plot['regions'])
    record.update(expected_pad_flashes=expected['pads'], expected_via_flashes=expected['vias'],
                  pad_flashes=counts['pad'], via_flashes=counts['via'], macro_pad_flashes=counts['macro'],
                  track_segments=conductor['linear'], expected_track_segments=expected['segments'],
                  track_arcs=conductor['arc'], expected_track_arcs=expected['arcs'],
                  conductor_regions=regions['conductor'], expected_conductor_regions=expected['regions'],
                  other_draws=sum(other.values()), other_regions=regions['other'])
    if regions['pad']:
        classified = False
        gaps.append({'detail': f'{rel}: {regions["pad"]} pad region(s) are not flashes; pad conservation unsupported'})
    if classified and (counts['pad'], counts['via']) != (expected['pads'], expected['vias']):
        defect('flash_count', f'{rel} ({layer}): pad/via flashes {counts["pad"]}/{counts["via"]}; '
               f'probe requires {expected["pads"]}/{expected["vias"]}')
    elif classified:
        pads, flashes = unmatched_extents(expected['extents'], pad_flashes)
        record['pad_extent_mismatches'] = {'probe_pads': pads[:20], 'flash_positions_nm': flashes[:20]}
        if pads or flashes:
            defect('pad_extent', f'{rel} ({layer}): {len(pads)} pad extent(s) differ from flashed geometry '
                   f'by more than 1000 nm ({", ".join(pads[:5])})')
    if expected['shapes']:
        gaps.append({'detail': f'{rel}: {expected["shapes"]} netted copper shape(s) make Conductor artwork '
                               'unattributable to tracks and zones'})
        return
    if (conductor['linear'], conductor['arc']) != (expected['segments'], expected['arcs']):
        defect('track_count', f'{rel} ({layer}): Conductor draws {conductor["linear"]} linear/{conductor["arc"]} arc; '
               f'probe requires {expected["segments"]} segments/{expected["arcs"]} arcs')
    if regions['conductor'] != expected['regions']:
        defect('region_count', f'{rel} ({layer}): {regions["conductor"]} Conductor regions; '
               f'probe zone fills have {expected["regions"]} outlines')


def check_fab_roundtrip(ctx):
    from .checks import CheckError
    details = ctx.result['details']
    if isinstance(ctx.params, dict) and 'package_check' in ctx.params:
        eid, export_result, stage, artifacts = package_prerequisite(ctx)
        details.update(package_check=eid, artifacts_id=artifacts['artifacts_id'])
    else:
        eid, _, export_result, stage = package.export_prerequisite(ctx)
        artifacts = None
    ids, facts = probe_facts(ctx)
    defects, gaps = [], []
    details.update(export_check=eid, geometry_checks=ids, defects=defects, coverage_gaps=gaps,
                   tolerance_nm=TOLERANCE_NM)

    def defect(code, locator):
        defects.append({'code': code, 'locator': locator})

    try:
        missing = []
        verified = package.load_verified_manifest(stage, context=ctx, missing_files=missing)
        manifest, found = verified['manifest'], verified['files']
        details['manifest_sha256'] = digest(stage / 'manifest.json')
        details['tool'] = package.verify_tool(ctx, eid, export_result, manifest['tool'], gaps)
        board = package.board_facts(ctx)
        enabled = package.reconcile_layers(manifest, board)
        if facts['copper_layers'] != board['copper']:
            package.fail('native', 'sealed probe copper stack differs from the captured board')
        try:
            pads, vias, holes, bounds = expected_counts(facts)
            segments, arcs, fills, shapes, extents = expected_copper(facts)
        except (ValueError, KeyError, TypeError) as exc:
            raise CheckError('unsupported', str(exc)) from exc
        for rel in missing:
            defect('missing_file', f'missing package file {rel}')
        if artifacts is None:
            def read(rel, label):
                return package.read_text(stage, rel, label)
        else:
            # Only the exact released bytes are compared: the sealed zip's members.
            if missing:
                package.fail('inventory', f'sealed package lacks {missing}')
            members, compared = sealed_members(stage, found, manifest, artifacts, eid)
            details['compared'] = compared

            def read(rel, label):
                return members[rel]
        plots, by_layer = [], {}
        for rel in sorted(r for r in found if r.startswith('gerbers/')):
            plot = parse_gerber(read(rel, 'plot'), rel)
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
                compare_copper(plot, rel, layer, record, defect, gaps, {
                    'pads': pads[layer], 'vias': vias[layer], 'segments': segments[layer], 'arcs': arcs[layer],
                    'regions': fills[layer], 'shapes': shapes[layer], 'extents': extents[layer]})
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
            drill = parse_excellon(read(rel, 'drill'), rel)
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
        if artifacts is not None:
            details['zip_members'] = len(details['compared']['members'])
        elif not missing and not any(d['code'] in ('missing_file', 'missing_layer') for d in defects):
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
    ctx.result['limitations'].append('Count/diameter/plating, pad-extent, track/arc and zone-region count conservation '
                                     'and straight outline bounds only; artwork rendering, hole positions, track and '
                                     'zone geometry, DFM and drill maps are not compared.')
    if defects:
        ctx.result.update(execution='ok', outcome='defect',
                          location='; '.join(d['locator'] for d in defects[:50]),
                          summary=f'{len(defects)} fab roundtrip conservation defect(s)')
    elif gaps:
        raise CheckError('unsupported', '; '.join(g['detail'] for g in gaps[:5]))
    else:
        ctx.result.update(execution='ok', outcome='pass',
                          summary=f'fab roundtrip conserved {len(plots)} layers, PTH/NPTH drill histograms, '
                                  'pad/via flashes and pad extents, track/arc draws, zone regions and '
                                  'outline bounds within 1 um')


def describe_fab_roundtrip(ctx):
    ctx.result['predicates'] = capability(ctx.params, ctx.scope, ctx.prerequisite, ctx.captured)


if __name__ != '__main__':
    _register_check('fab_roundtrip', describe_fab_roundtrip, check_fab_roundtrip)
