"""Supplemental group annotations over the existing native KiCad SVG plots.

Only a four-line rectangular Edge.Cuts crop is qualified here. Native geometry
is exported, never reconstructed from group positions. Views express membership
and recorded poses; they do not assess functional coherence or orientation.
"""
import copy
import hashlib
import math
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

from . import native_run
from . import candidate_visuals
from .storage import digest, identity, json_bytes, write_json
from .run_record import pcbnew_extension

SCHEMA = 'agent-ee.group-views/1'
SVG = 'http://www.w3.org/2000/svg'
COLORS = ('#1756a9', '#b85b00', '#16815c', '#8a3d9e', '#c22e4c', '#526b12', '#006f85', '#694e37')
LAYERS = {'top': ['F.Cu', 'F.SilkS', 'F.CrtYd', 'Edge.Cuts'],
          'bottom': ['B.Cu', 'B.SilkS', 'B.CrtYd', 'Edge.Cuts']}
ET.register_namespace('', SVG)


def _element(parent, name, text=None, **attributes):
    item = ET.SubElement(parent, '{' + SVG + '}' + name,
                         {key.replace('_', '-'): str(value) for key, value in attributes.items()})
    if text is not None:
        item.text = str(text)
    return item


def _rectangle(board, k):
    edges = [g for g in board.GetDrawings() if g.GetLayer() == k.Edge_Cuts]
    if len(edges) != 4 or any(g.GetShapeStr() != 'Line' for g in edges):
        raise ValueError('group views: native fit-board crop is qualified only for four-line rectangular Edge.Cuts')
    segments = [((g.GetStart().x, g.GetStart().y), (g.GetEnd().x, g.GetEnd().y)) for g in edges]
    xs = {p[0] for segment in segments for p in segment}
    ys = {p[1] for segment in segments for p in segment}
    if len(xs) != 2 or len(ys) != 2:
        raise ValueError('group views: unsupported nonrectangular native crop')
    x0, x1 = sorted(xs)
    y0, y1 = sorted(ys)
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    expected = {frozenset((a, b)) for a, b in zip(corners, corners[1:] + corners[:1])}
    if {frozenset(segment) for segment in segments} != expected:
        raise ValueError('group views: Edge.Cuts does not form one axis-aligned rectangle')
    return [value / 1e6 for value in (x0, y0, x1, y1)]


def _inventory(board, groups, k):
    if (not isinstance(groups, dict) or not isinstance(groups.get('groups'), list)
            or ('schema' in groups and (type(groups['schema']) is not int or groups['schema'] != 1))):
        raise ValueError('group views require a validated groups document with groups[]')
    by_ref, native = {}, []
    for fp in board.GetFootprints():
        if any(g.GetLayer() == k.Edge_Cuts for g in fp.GraphicalItems()):
            raise ValueError('group views: footprint Edge.Cuts crop is unsupported')
        pose = dict(reference=fp.GetReference(), uuid=fp.m_Uuid.AsString(),
                    position_mm=[fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6],
                    rotation_degrees=fp.GetOrientationDegrees(), side='B' if fp.IsFlipped() else 'F')
        if not all(math.isfinite(v) for v in [*pose['position_mm'], pose['rotation_degrees']]):
            raise ValueError('group views: nonfinite native pose')
        by_ref.setdefault(pose['reference'], []).append(pose)
        native.append(pose)
    members, names, used = [], set(), set()
    for index, group in enumerate(groups['groups']):
        if (not isinstance(group, dict) or not isinstance(group.get('name'), str) or not group['name'].strip()
                or group['name'] in names or not isinstance(group.get('refs'), list) or not group['refs']
                or any(not isinstance(ref, str) or not ref.strip() for ref in group['refs'])
                or not isinstance(group.get('anchor'), str) or group['anchor'] not in group['refs']):
            raise ValueError('group views: invalid/duplicate group name, members or anchor')
        names.add(group['name'])
        for ref in group['refs']:
            if ref in used:
                raise ValueError('group views: duplicate membership: ' + ref)
            used.add(ref)
            matches = by_ref.get(ref, [])
            if len(matches) != 1:
                raise ValueError('group views: unknown or ambiguous native reference: ' + ref)
            members.append(dict(matches[0], group=group['name'], anchor=ref == group['anchor'],
                                color=COLORS[index % len(COLORS)], label=len(members) + 1))
    ungrouped = sorted((p for p in native if p['reference'] not in used), key=lambda p: p['uuid'])
    return members, ungrouped


def _annotate(native_path, target, *, side, rectangle, members, ungrouped, board_hash):
    raw = ET.parse(native_path).getroot()
    x0, y0, x1, y1 = rectangle
    width, height = x1 - x0, y1 - y0
    viewport = [float(v) for v in raw.attrib['viewBox'].split()]
    # KiCad fit-board page dimensions are quantized to integer mils. Preserve
    # all native children so that quantization cannot clip an outline edge.
    if (raw.tag != '{' + SVG + '}svg' or viewport[:2] != [0, 0]
            or abs(viewport[2] - width) > 0.026 or abs(viewport[3] - height) > 0.026):
        raise ValueError('group views: native SVG crop differs from qualified board frame')
    scale, left, top = 95 / width, 7, 24
    canvas_height = max(35 + height * scale, 30 + 2.8 * (len(members) + len(ungrouped) + len({m['group'] for m in members})))
    root = ET.Element('{' + SVG + '}svg', dict(viewBox=f'0 0 195 {canvas_height}', width='1600',
                                             height=str(round(1600 * canvas_height / 195))))
    _element(root, 'rect', x=0, y=0, width=195, height=canvas_height, fill='white')
    _element(root, 'text', f'{side.upper()} — native geometry + functional membership',
             x=7, y=5, font_size=3.1, font_family='sans-serif', fill='#172334')
    _element(root, 'text', 'Supplement only: no coherence, orientation, electrical or manufacturing acceptance',
             x=7, y=9, font_size=1.8, font_family='sans-serif', fill='#8b2434')
    _element(root, 'text', 'Board SHA256 ' + board_hash, x=7, y=12, font_size=1.55, font_family='monospace')
    _element(root, 'text', f'Native crop ({x0:g}, {y0:g}) mm; ' + ('X mirrored; opposite-face markers dashed' if side == 'bottom'
             else 'X right, Y down; opposite-face markers dashed'), x=7, y=16, font_size=1.8, font_family='sans-serif')
    scene = _element(root, 'g', transform=f'translate({left} {top}) scale({scale})')
    definitions = _element(root, 'defs')
    clipping = _element(definitions, 'clipPath', id='board-window')
    _element(clipping, 'rect', x=-2, y=-2, width=width + 4, height=height + 4)
    native_transform = f'matrix(-1 0 0 1 {width} 0)' if side == 'bottom' else 'matrix(1 0 0 1 0 0)'
    window = _element(scene, 'g', clip_path='url(#board-window)')
    native = _element(window, 'g', id='native-board', transform=native_transform)
    for child in raw:
        native.append(copy.deepcopy(child))
    face = 'F' if side == 'top' else 'B'
    for member in members:
        if member['outside_view']:
            continue
        x, y = member['position_mm'][0] - x0, member['position_mm'][1] - y0
        if side == 'bottom':
            x = width - x
        on_face = member['side'] == face
        if member['anchor']:
            _element(scene, 'circle', cx=x, cy=y, r=0.61, fill='none', stroke=member['color'], stroke_width=0.07,
                     stroke_dasharray='none' if on_face else '0.15 0.12')
        _element(scene, 'circle', cx=x, cy=y, r=0.48 if member['anchor'] else 0.38,
                 fill='white', stroke=member['color'], stroke_width=0.12,
                 stroke_dasharray='none' if on_face else '0.15 0.12',
                 data_ref=member['reference'], data_uuid=member['uuid'], data_side=member['side'],
                 data_view_x_mm=x, data_view_y_mm=y)
        _element(scene, 'text', member['label'], x=x, y=y + 0.13, fill=member['color'],
                 text_anchor='middle', font_size=0.38, font_family='sans-serif', font_weight='bold')
    y = 21
    group_name = None
    for member in members:
        if member['group'] != group_name:
            group_name = member['group']
            anchor = next(m['reference'] for m in members if m['group'] == group_name and m['anchor'])
            _element(root, 'text', f'{group_name} / anchor {anchor}', x=110, y=y,
                     fill=member['color'], font_size=2.1, font_family='sans-serif', font_weight='bold')
            y += 3
        x_mm, y_mm = member['position_mm']
        text = (f"{member['label']:02d} {member['reference']} [{member['side']}] "
                f"({x_mm:.3f}, {y_mm:.3f}) {member['rotation_degrees']:g}°"
                + (' ANCHOR' if member['anchor'] else '')
                + (' OUTSIDE VIEW' if member['outside_view'] else '')
                + (' opposite face' if member['side'] != face else ''))
        _element(root, 'text', text, x=110, y=y, fill='#253344', font_size=1.65, font_family='sans-serif')
        y += 2.8
    if ungrouped:
        y += 2
        _element(root, 'text', f'UNRESOLVED: {len(ungrouped)} ungrouped native items', x=110, y=y,
                 fill='#9c293a', font_size=1.9, font_family='sans-serif')
        for member in ungrouped:
            y += 2.8
            text = (f"{member['reference'] or '<no ref>'} [{member['side']}] {member['uuid'][-12:]} "
                    f"({member['position_mm'][0]:.3f}, {member['position_mm'][1]:.3f})")
            _element(root, 'text', text, x=110, y=y, fill='#9c293a', font_size=1.65, font_family='sans-serif')
    ET.ElementTree(root).write(target, encoding='utf-8', xml_declaration=True)
    return dict(native_crop_origin_mm=[x0, y0], native_size_mm=[width, height],
                bottom_mirror_x=side == 'bottom', page_scale=scale, page_offset=[left, top], crop_margin_mm=2,
                frame='native x right/y down; bottom mirrors X for plot and annotations together')


def produce(board_path, groups_document, out, *, input_board_path=None):
    """Write two supplemental native SVG views in a new owned directory.

    The caller validates group intent; this helper additionally refuses missing,
    ambiguous or multiply grouped native references. Extra native items remain
    unresolved rather than acquiring invented membership. All hashes describe
    exact captured bytes or explicitly named canonical JSON identities.
    """
    import pcbnew
    board_path, out = Path(board_path).resolve(), Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    board_bytes = board_path.read_bytes()
    board_hash = hashlib.sha256(board_bytes).hexdigest()
    groups = copy.deepcopy(groups_document)
    group_bytes = json_bytes(groups)
    input_bytes = Path(input_board_path).read_bytes() if input_board_path is not None else None
    input_hash = hashlib.sha256(input_bytes).hexdigest() if input_bytes is not None else None
    project = board_path.with_suffix('.kicad_pro')
    project_bytes = project.read_bytes() if project.exists() else None
    with tempfile.TemporaryDirectory(prefix='agent-ee-group-views-') as temporary:
        captured = Path(temporary) / board_path.name
        captured.write_bytes(board_bytes)
        if project_bytes is not None:
            captured.with_suffix('.kicad_pro').write_bytes(project_bytes)
        saved = pcbnew.LoadBoard(str(captured))
        rectangle = _rectangle(saved, pcbnew)
        members, ungrouped = _inventory(saved, groups, pcbnew)
        x0, y0, x1, y1 = rectangle
        for member in members:
            x, y = member['position_mm']
            member['outside_view'] = not (x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2)
    binary = Path(shutil.which('kicad-cli') or '')
    extension, diagnostic = pcbnew_extension()
    if not binary.is_file() or extension is None:
        raise ValueError('group views: native runtime unavailable: ' + str(diagnostic))
    runtime = dict(kicad_cli_sha256=digest(binary), pcbnew_sha256=digest(extension),
                   pcbnew_version=pcbnew.Version(), python_sha256=digest(sys.executable),
                   helper_sha256=digest(__file__), native_export_helper_sha256=digest(candidate_visuals.__file__))
    execution = native_run.run(
        native_run.Intent('probe', [str(binary), 'version'], timeout=30, isolation='host'), work=None
    )
    version = native_run.completed(execution, text=True)
    version.check_returncode()
    runtime['kicad_version'] = version.stdout.strip()
    out.mkdir(parents=True, exist_ok=False)
    source = out / 'source'
    source.mkdir()
    snapshot = source / board_path.name
    snapshot.write_bytes(board_bytes)
    if project_bytes is not None:
        snapshot.with_suffix('.kicad_pro').write_bytes(project_bytes)
    (source / 'groups.json').write_bytes(group_bytes)
    if input_bytes is not None:
        (source / 'input-board').mkdir()
        (source / 'input-board/board.kicad_pcb').write_bytes(input_bytes)
    views = {}
    for side, layers in LAYERS.items():
        native = candidate_visuals._plot(snapshot.resolve(), out.resolve(), side + '-native', layers)
        native['evidence'] = {str((Path(native['path']).parent / name).as_posix()):
                              digest(out / Path(native['path']).parent / name)
                              for name in ('command.json', 'stdout.log', 'stderr.log')}
        path = out / (side + '.svg')
        transform = _annotate(out / native['path'], path, side=side, rectangle=rectangle,
                              members=members, ungrouped=ungrouped, board_hash=board_hash)
        views[side] = dict(path=path.name, sha256=digest(path), native=native, transform=transform,
                           layers=layers, face='F' if side == 'top' else 'B')
    if (digest(board_path) != board_hash or digest(snapshot) != board_hash
            or (input_board_path is not None and digest(input_board_path) != input_hash)
            or (input_bytes is not None and (source / 'input-board/board.kicad_pcb').read_bytes() != input_bytes)
            or (source / 'groups.json').read_bytes() != group_bytes
            or (project_bytes is not None and (project.read_bytes() != project_bytes
                                               or snapshot.with_suffix('.kicad_pro').read_bytes() != project_bytes))
            or digest(binary) != runtime['kicad_cli_sha256'] or digest(extension) != runtime['pcbnew_sha256']
            or digest(__file__) != runtime['helper_sha256']
            or digest(candidate_visuals.__file__) != runtime['native_export_helper_sha256']):
        raise ValueError('group views: source, snapshot or runtime changed during rendering')
    report = dict(schema=SCHEMA, board_sha256=board_hash, input_board_sha256=input_hash,
                  groups_sha256=hashlib.sha256(group_bytes).hexdigest(), canonical_groups_sha256=identity(groups),
                  project_sha256=hashlib.sha256(project_bytes).hexdigest() if project_bytes is not None else None,
                  snapshots=dict(board='source/' + board_path.name, groups='source/groups.json',
                      project='source/' + project.name if project_bytes is not None else None,
                      input_board='source/input-board/board.kicad_pcb' if input_bytes is not None else None),
                  runtime=runtime, members=members, ungrouped=ungrouped,
                  unresolved=[dict(kind='ungrouped_native_item', **p) for p in ungrouped]
                      + [dict(kind='member_outside_view', **p) for p in members if p['outside_view']],
                  views=views, acceptance_assessed=False,
                  limitations=['Rectangle crop only; views supplement independent final-coordinate measurements.',
                               'Recorded native rotation/side is not an orientation assessment.',
                               'Annotated drawing window is outline plus 2 mm; full native SVG is retained.',
                               'Opposite-face member origins are projected with dashed markers.',
                               'Native SVG timestamps are retained; byte determinism is not claimed.'])
    write_json(out / 'manifest.json', report)
    return report


def main(argv=None):
    """Narrow child entry used by placement_job.final_checks: produce one view set, print the manifest."""
    import argparse
    import json
    parser = argparse.ArgumentParser(prog='agent_ee.group_views', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    produce_parser = sub.add_parser('produce', help='write top/bottom views and manifest.json into a new directory')
    produce_parser.add_argument('--board', required=True, help='saved final native board (.kicad_pcb)')
    produce_parser.add_argument('--groups', required=True, help='JSON file holding the validated groups document')
    produce_parser.add_argument('--out', required=True, help='new output directory (must not exist)')
    produce_parser.add_argument('--input-board', default=None, help='original raw input board, hashed independently')
    args = parser.parse_args(argv)
    groups = json.loads(Path(args.groups).read_bytes())
    report = produce(args.board, groups, args.out, input_board_path=args.input_board)
    sys.stdout.write(json.dumps({'schema': report['schema'], 'board_sha256': report['board_sha256'],
                                 'input_board_sha256': report['input_board_sha256'],
                                 'canonical_groups_sha256': report['canonical_groups_sha256']}) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
