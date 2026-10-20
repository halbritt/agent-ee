"""Measured KiCad stroke-text bounds; diagnostic only, never an electrical verdict.

Only native SVG's absolute M/L stroke paths are supported. Unknown geometry,
missing text and ambiguous source associations fail rather than report clean.
"""
import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .symbol_library import children, parse_sexpr

NS = '{http://www.w3.org/2000/svg}'
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
SEGMENT = re.compile(r'([ML])\s*(' + NUMBER + r')[ ,]+(' + NUMBER + r')\s*')


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def stroke_text(svg):
    """Named native text with bounds in SVG millimetres, including stroke radius."""
    root = ET.fromstring(svg)
    if root.tag != NS + 'svg' or not root.get('width', '').endswith('mm'):
        raise ValueError('expected native millimetre SVG')
    try:
        width_mm = float(root.attrib['width'][:-2])
        height_mm = float(root.attrib['height'][:-2])
        view = list(map(float, root.attrib['viewBox'].split()))
    except (KeyError, ValueError) as exc:
        raise ValueError('missing native SVG viewport') from exc
    if (not root.attrib['height'].endswith('mm') or len(view) != 4
            or not all(math.isfinite(v) for v in [width_mm, height_mm, *view])
            or width_mm <= 0 or height_mm <= 0
            or any(abs(a - b) > .0001 for a, b in zip(view, (0, 0, width_mm, height_mm)))):
        raise ValueError('SVG viewport must preserve native millimetres')
    found = []

    def walk(node, width=0, transformed=False):
        style = dict(part.strip().split(':', 1) for part in node.get('style', '').split(';') if ':' in part)
        width = float(style.get('stroke-width', node.get('stroke-width', width)))
        transformed = transformed or bool(node.get('transform'))
        if node.get('class') == 'stroked-text':
            if transformed:
                raise ValueError('transformed stroke text is unsupported')
            desc = node.find(NS + 'desc')
            if desc is not None and not desc.text and len(node) == 1:
                return  # KiCad emits empty groups for empty field values.
            if desc is None or not desc.text:
                raise ValueError('stroke text lacks description')
            points = []
            for path in node:
                if path.tag == NS + 'desc':
                    continue
                if path.tag != NS + 'path' or set(path.attrib) != {'d'}:
                    raise ValueError('unsupported stroke-text geometry')
                data, pos = path.get('d', ''), 0
                while pos < len(data):
                    match = SEGMENT.match(data, pos)
                    if not match:
                        raise ValueError('unsupported stroke path: ' + data[pos:pos + 32])
                    points.append(tuple(map(float, match.groups()[1:])))
                    pos = match.end()
            if not points or not math.isfinite(width) or width <= 0 or not all(math.isfinite(v) for p in points for v in p):
                raise ValueError('missing or invalid stroke extents')
            xs, ys = zip(*points)
            radius = width / 2
            found.append(dict(text=desc.text, box=[min(xs) - radius, min(ys) - radius,
                                                   max(xs) + radius, max(ys) + radius]))
            return
        for child in node:
            walk(child, width, transformed)
    walk(root)
    if not found:
        raise ValueError('no native stroke text')
    # Native plotting can repeat the same field ink in two drawing passes.
    return list({(t["text"], tuple(t["box"])): t for t in found}.values())


def _visible(node):
    return not any(n[1:] in ([], ['yes']) for n in children(node, 'hide') +
                   [n for e in children(node, 'effects') for n in children(e, 'hide')])


def measured_text(schematic, svg):
    """Associate native labels and visible fields with measured rendered boxes.

    Match by exact text and distance from the source anchor to the ink box.
    A source anchor must be within two em of its ink. A tie is refused. Each
    native group is consumed once; missing fields/labels are not clean evidence.
    """
    root = parse_sexpr(schematic)
    sources = []
    for sym in children(root, 'symbol'):
        props = children(sym, 'property')
        ref = next(p[2] for p in props if p[1] == 'Reference')
        lib_id = children(sym, 'lib_id')[0][1]
        library = next(n for lib in children(root, 'lib_symbols') for n in children(lib, 'symbol') if n[1] == lib_id)
        units = [int(n[1].rsplit('_', 2)[1]) for n in children(library, 'symbol')]
        suffix = chr(64 + int(children(sym, 'unit')[0][1])) if max(units, default=1) > 1 else ''
        uid = children(sym, 'uuid')[0][1]
        for prop in props:
            if _visible(prop) and prop[2] not in ('', '~'):
                sources.append(dict(id=uid + '/' + prop[1], kind='field', reference=ref,
                                    field=prop[1], text=prop[2] + (suffix if prop[1] == 'Reference' else ''), node=prop))
    for kind in ('label', 'global_label', 'hierarchical_label'):
        for node in children(root, kind):
            sources.append(dict(id=children(node, 'uuid')[0][1], kind='label', text=node[1], node=node))
    rendered = stroke_text(svg)
    available = set(range(len(rendered)))
    # All sources compete together, so a distant source cannot consume a
    # repeated value's nearer ink group before its actual owner is matched.
    choices = []
    for index, source in enumerate(sources):
        node = source['node']
        at = children(node, 'at')[0]
        x, y = map(float, at[1:3])
        font = children(children(node, 'effects')[0], 'font')[0]
        size = max(map(float, children(font, 'size')[0][1:]))
        for j, item in enumerate(rendered):
            if item['text'] != source['text']:
                continue
            x0, y0, x1, y1 = item['box']
            distance = math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
            if distance <= 2 * size:
                choices.append((distance, index, j))
    matched = {}
    for distance, i, j in sorted(choices):
        if i in matched or j not in available:
            continue
        ties = [c for c in choices if c[1] == i and c[2] in available and abs(c[0] - distance) < 1e-8]
        if len(ties) != 1:
            raise ValueError('ambiguous native text: ' + sources[i]['id'])
        matched[i] = j
        available.remove(j)
    if len(matched) != len(sources):
        raise ValueError('missing rendered text: ' + ', '.join(s['id'] for i, s in enumerate(sources) if i not in matched))
    result = [dict({k: v for k, v in source.items() if k != 'node'}, box=rendered[matched[i]]['box'])
              for i, source in enumerate(sources)]
    # Include pin names/numbers and group titles in the whole-sheet diagnostic.
    counts = {}
    for j, item in enumerate(rendered):
        if j in available:
            n = counts.get(item['text'], 0)
            counts[item['text']] = n + 1
            result.append(dict(item, kind='other', id=f'other/{item["text"]}/{n}'))
    return result


def collisions(texts):
    """Every measured label-vs-text overlap, with stable source identities."""
    return [dict(label=a['id'], label_text=a['text'], text=b['id'], text_value=b['text'],
                 reference=b.get('reference'), field=b.get('field'))
            for a in texts if a['kind'] == 'label'
            for b in texts if b['kind'] != 'label' and overlap(a['box'], b['box'])]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('schematic', type=Path)
    parser.add_argument('svg', type=Path)
    args = parser.parse_args(argv)
    try:
        texts = measured_text(args.schematic.read_bytes(), args.svg.read_bytes())
        report = dict(texts=texts, overlaps=collisions(texts))
    except (ValueError, ET.ParseError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps(report, indent=2))
    return bool(report['overlaps'])


def place_native_text(files, work):
    """Measure an unpublished flat emission and place colliding native fields.

    Every native export gets an immutable draft directory and its own executor
    scratch. The caller publishes only the returned bytes. A failed export,
    unsupported drawing, unplaceable field or remaining pin-text overlap refuses
    the emission; none is a clean result. Existing retained output is never edited.
    """
    from .executor import execute
    from .schematic_emitter import PAPERS, EmissionError, _dump, _mm, _scan, publish
    from .schematic_lint import BODY_RULES, _Symbol

    work = Path(work)
    work.mkdir(parents=True, exist_ok=False)
    names = [p for p in files if p.endswith('.kicad_sch')]
    if len(names) != 1:
        raise EmissionError('presentation_text', 'native text placement requires one flat schematic')
    name = names[0]

    def measure(draft, step):
        candidate, output = work / ('draft-' + str(step)), work / ('svg-' + str(step))
        publish(draft, candidate)
        result = execute(candidate, output, ['kicad-cli', 'sch', 'export', 'svg', '--exclude-drawing-sheet',
                                             '-o', '/work/', '/candidate/' + name], timeout=120)
        if result['execution'] != 'ok':
            raise EmissionError('presentation_text', 'native SVG export did not complete: ' + result['execution'])
        try:
            return measured_text(draft[name], (output / (Path(name).stem + '.svg')).read_bytes())
        except (ValueError, OSError, ET.ParseError) as exc:
            raise EmissionError('presentation_text', 'native text coverage: ' + str(exc)) from exc

    texts = measure(files, 0)
    initial = collisions(texts)
    moved = []
    if initial:
        tree = _scan(files[name])
        symbols = children(tree, 'symbol')
        embedded = {n[1]: n for lib in children(tree, 'lib_symbols') for n in children(lib, 'symbol')}
        bodies = []
        sheet = dict(file=name, sha256=hashlib.sha256(files[name]).hexdigest(), path='/', instance_path='/')
        for node in symbols:
            symbol = _Symbol(node, sheet, embedded, BODY_RULES)
            if symbol.body is None:
                raise EmissionError('presentation_text', 'native field placement lacks symbol body bounds')
            bodies.append([v / 1e6 for v in symbol.body])
        wires = []
        for wire in children(tree, 'wire'):
            points = [tuple(map(float, p[1:])) for pts in children(wire, 'pts') for p in children(pts, 'xy')]
            wires.append([min(p[0] for p in points) - .15, min(p[1] for p in points) - .15,
                          max(p[0] for p in points) + .15, max(p[1] for p in points) + .15])
        paper = children(tree, 'paper')[0][1]
        page = next((w / 1e6, h / 1e6) for n, w, h in PAPERS if n == paper)
        frames = [[*map(float, children(f, 'start')[0][1:]), *map(float, children(f, 'end')[0][1:])]
                  for f in children(tree, 'rectangle')]
        by_id = {t['id']: t for t in texts}
        for identity in sorted({c['text'] for c in initial}):
            text = by_id[identity]
            if text['kind'] != 'field' or text['field'] not in ('Reference', 'Value'):
                raise EmissionError('presentation_text', 'unresolved native label overlap: ' + identity)
            uid, field = identity.split('/', 1)
            node = next(n for n in symbols if children(n, 'uuid')[0][1] == uid)
            prop = next(p for p in children(node, 'property') if p[1] == field)
            ax, ay = map(float, children(node, 'at')[0][1:3])
            enclosing = [f for f in frames if f[0] <= ax <= f[2] and f[1] <= ay <= f[3]]
            frame = min(enclosing, key=lambda f: (f[2] - f[0]) * (f[3] - f[1])) if enclosing else None
            obstacles = [t['box'] for t in texts if t['id'] != identity] + bodies + wires
            choice = None
            for step in range(1, 25):
                for dx, dy in ((2.54 * step, 0), (-2.54 * step, 0), (0, 2.54 * step), (0, -2.54 * step)):
                    box = [v + (dx if i % 2 == 0 else dy) for i, v in enumerate(text['box'])]
                    if frame and not (frame[0] < box[0] < box[2] < frame[2] and frame[1] < box[1] < box[3] < frame[3]):
                        continue
                    if box[0] < 0 or box[1] < 0 or box[2] > page[0] or box[3] > page[1] or any(overlap(box, obstacle) for obstacle in obstacles):
                        continue
                    choice = dx, dy, box
                    break
                if choice:
                    break
            if choice is None:
                raise EmissionError('presentation_text', 'no measured field placement within 24 steps: ' + identity)
            dx, dy, box = choice
            at = children(prop, 'at')[0]
            at[1:3] = [_mm(round((float(at[1]) + dx) * 1e6)), _mm(round((float(at[2]) + dy) * 1e6))]
            text['box'] = box
            moved.append(dict(id=identity, dx_mm=dx, dy_mm=dy))
        files = dict(files, **{name: ('\n'.join(_dump(tree)) + '\n').encode('utf-8')})
        texts = measure(files, 1)
        if collisions(texts):
            raise EmissionError('presentation_text', 'native text overlaps remain after measured placement')
    (work / 'text-placement.json').write_text(json.dumps(dict(before=initial, moved=moved, after=collisions(texts)), indent=2) + '\n')
    return files


if __name__ == '__main__':
    raise SystemExit(main())
