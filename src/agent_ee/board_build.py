"""Build an unplaced KiCad board from a native XML netlist, then place it.

The board is composed only from captured inputs: the schematic project, its
native ``kicad-cli sch export netlist --format kicadxml`` output, footprint
files under FPDIR, an ``agent-ee.rung1-envelope/0-experimental`` record and a template board
whose setup, layers, stackup and project design rules are kept. Placement uses
the landed deterministic placer through ``placer_runner.place_candidate``; the
placed board is then checked with native ``kicad-cli pcb drc
--schematic-parity``. Nothing is guessed: a missing footprint, a pin without a
pad or an unexplained pad is refused. Run under the networkless executor
sandbox with read-only inputs and a writable OUTDIR.

Private experimental rung-1 stand-in (unqualified). The owned forward
annotation (AGENTEE-52) and placement writer step (AGENTEE-91) supersede it at
integration; the owned board envelope record is AGENTEE-85.
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .storage import digest, read_json, write_json

ENVELOPE_SCHEMA = 'agent-ee.rung1-envelope/0-experimental'
RECEIPT_SCHEMA = 'agent-ee.rung1-board-build-receipt/0-experimental'
WRITE_SCHEMA = 'agent-ee.placement-write/1'
NM = 1_000_000
EDGE_WIDTH_NM = 100_000          # Edge.Cuts stroke, as in the RC fixture board.
STAGING_GAP_NM = 5_080_000       # Unplaced parts wait right of the outline.
STAGING_PITCH_NM = 2_540_000
PROJECT_TABLES = ('sym-lib-table', 'fp-lib-table')
# Netlist properties with board meaning. Anything else is carried as data only.
REFUSED_PROPERTIES = ('exclude_from_board',)
# Parity checks that must be evaluated. KiCad ignores footprint filter
# mismatches by default; that advisory stays in the raw report's ignored list.
PARITY_KEYS = ('extra_footprint', 'missing_footprint', 'net_conflict', 'footprint_symbol_mismatch',
               'duplicate_footprints')


class BoardBuildError(ValueError):
    """An input the builder refuses; nothing is guessed in its place."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _text(node, path):
    found = node.find(path)
    return (found.text or '') if found is not None else None


# --------------------------------------------------------------------------
# Captured inputs


def read_netlist(xml_bytes):
    """Return native XML components and nets encoded for the board writer."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise BoardBuildError(f'netlist is not XML: {exc}') from exc
    if root.tag != 'export' or root.find('components') is None or root.find('nets') is None:
        raise BoardBuildError('native XML export with components and nets required')
    components, refs = [], set()
    for comp in root.findall('components/comp'):
        ref = comp.get('ref')
        if not ref or ref in refs:
            raise BoardBuildError(f'missing or duplicate component reference {ref!r}')
        refs.add(ref)
        footprint = _text(comp, 'footprint')
        if not footprint or footprint.count(':') != 1 or not all(footprint.split(':')):
            raise BoardBuildError(f'{ref}: footprint must be an explicit LIB:NAME, got {footprint!r}')
        properties = {p.get('name'): p.get('value') for p in comp.findall('property')}
        for name in REFUSED_PROPERTIES:
            if name in properties:
                raise BoardBuildError(f'{ref}: unsupported netlist property {name}')
        fields = {}
        for field in comp.findall('fields/field'):
            name = field.get('name')
            if not name or name in fields:
                raise BoardBuildError(f'{ref}: missing or duplicate field name')
            fields[name] = field.text or ''
        if 'Footprint' in fields and fields['Footprint'] != footprint:
            raise BoardBuildError(f'{ref}: Footprint field differs from footprint element')
        stamps = (_text(comp, 'tstamps') or '').split()
        sheet = comp.find('sheetpath')
        if len(stamps) != 1 or sheet is None:
            raise BoardBuildError(f'{ref}: exactly one symbol UUID and a sheet path required')
        pins = [p.get('num') for p in comp.findall('units/unit/pins/pin')]
        components.append({'ref': ref, 'value': _text(comp, 'value') or '', 'footprint': footprint,
                           'fields': fields, 'properties': properties, 'uuid': stamps[0],
                           'sheet_uuids': sheet.get('tstamps') or '/', 'pins': pins})
    from .board_net_names import from_xml
    try:
        board_names = from_xml(root)
    except ValueError as exc:
        raise BoardBuildError(str(exc)) from exc
    nets, names, terminals = [], set(), {}
    for net in root.findall('nets/net'):
        name = board_names.get(net.get('name'))
        if not name or name in names:
            raise BoardBuildError(f'missing or duplicate net name {name!r}')
        names.add(name)
        nodes = []
        for node in net.findall('node'):
            terminal = node.get('ref'), node.get('pin')
            if terminal[0] not in refs or not terminal[1]:
                raise BoardBuildError(f'net {name}: node names unknown component or empty pin {terminal}')
            if terminal in terminals:
                raise BoardBuildError(f'{terminal[0]} pin {terminal[1]} is on nets {terminals[terminal]} and {name}')
            terminals[terminal] = name
            nodes.append(terminal)
        nets.append({'name': name, 'class': net.get('class') or 'Default', 'nodes': nodes})
    for comp in components:
        declared = set(comp['pins'])
        connected = {pin for ref, pin in terminals if ref == comp['ref']}
        if declared and declared != connected:
            raise BoardBuildError(f"{comp['ref']}: symbol pins {sorted(declared)} differ from net nodes {sorted(connected)}")
    source = _text(root, 'design/source')
    return {'components': sorted(components, key=lambda c: c['ref']), 'nets': nets,
            'source': source}


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BoardBuildError(f'{label} must be a finite number')
    if positive and value <= 0:
        raise BoardBuildError(f'{label} must be positive')
    return value


def _exact_keys(record, required, label, optional=()):
    if not isinstance(record, dict):
        raise BoardBuildError(f'{label} must be an object')
    missing = set(required) - set(record)
    unknown = set(record) - set(required) - set(optional)
    if missing or unknown:
        raise BoardBuildError(f'{label}: missing {sorted(missing)}, unknown {sorted(unknown)}')


def read_envelope(envelope):
    """Validate ``agent-ee.rung1-envelope/0-experimental`` and convert it to nanometres."""
    _exact_keys(envelope, ('schema', 'outline_mm', 'copper_layers', 'fixed_refs', 'stackup'), 'envelope')
    if envelope['schema'] != ENVELOPE_SCHEMA:
        raise BoardBuildError(f'envelope schema must be {ENVELOPE_SCHEMA}')
    outline = envelope['outline_mm']
    _exact_keys(outline, ('x', 'y', 'w', 'h'), 'outline_mm')
    box = {k: round(_number(outline[k], f'outline_mm.{k}', positive=k in 'wh') * NM) for k in 'xywh'}
    if type(envelope['copper_layers']) is not int or envelope['copper_layers'] < 1:
        raise BoardBuildError('copper_layers must be a positive integer')
    if not isinstance(envelope['stackup'], str) or not envelope['stackup']:
        raise BoardBuildError('stackup must name the declared stackup')
    if not isinstance(envelope['fixed_refs'], list):
        raise BoardBuildError('fixed_refs must be a list')
    fixed, seen = [], set()
    for i, item in enumerate(envelope['fixed_refs']):
        _exact_keys(item, ('ref', 'x_mm', 'y_mm', 'rotation_deg', 'side'), f'fixed_refs[{i}]')
        if not isinstance(item['ref'], str) or not item['ref'] or item['ref'] in seen:
            raise BoardBuildError(f'fixed_refs[{i}]: missing or duplicate ref')
        seen.add(item['ref'])
        rotation = _number(item['rotation_deg'], f"{item['ref']}.rotation_deg")
        if rotation % 90:
            raise BoardBuildError(f"{item['ref']}: rotation must be a quarter turn (the placer's domain)")
        if item['side'] not in ('F', 'B'):
            raise BoardBuildError(f"{item['ref']}: side must be F or B")
        fixed.append({'ref': item['ref'], 'position_nm': [round(_number(item['x_mm'], 'x_mm') * NM),
                      round(_number(item['y_mm'], 'y_mm') * NM)],
                      'rotation_deg': rotation % 360, 'side': item['side']})
    return {'outline_nm': box, 'copper_layers': envelope['copper_layers'],
            'stackup': envelope['stackup'], 'fixed_refs': fixed}


def read_request(request, envelope):
    """The caller's placement policy and explicit writer authorization.

    The caller authorizes a single design writer; the builder binds that
    authorization to the board it built (hash) and its own scratch root.
    """
    _exact_keys(request, ('policy', 'authorization'), 'placement request')
    authorization = request['authorization']
    _exact_keys(authorization, ('schema', 'single_design_writer'), 'authorization')
    if authorization['schema'] != WRITE_SCHEMA or authorization['single_design_writer'] is not True:
        raise BoardBuildError('explicit single-design-writer placement authorization required')
    policy = request['policy']
    if not isinstance(policy, dict) or policy.get('schema') != 'agent-ee.packing-policy/1':
        raise BoardBuildError('policy must be agent-ee.packing-policy/1')
    declared = policy.get('fixed_refs')
    if not isinstance(declared, list) or sorted(declared) != sorted(f['ref'] for f in envelope['fixed_refs']):
        raise BoardBuildError('policy fixed_refs must equal the envelope fixed_refs')
    return request


def footprint_path(fpdir, lib_id):
    """``LIB:NAME`` -> ``FPDIR/LIB.pretty/NAME.kicad_mod``; absence is refused."""
    lib, name = lib_id.split(':')
    for part in (lib, name):
        if part in ('.', '..') or '/' in part or '\0' in part:
            raise BoardBuildError(f'unsafe footprint identity {lib_id!r}')
    path = Path(fpdir) / f'{lib}.pretty' / f'{name}.kicad_mod'
    if not path.is_file():
        raise BoardBuildError(f'footprint {lib_id} not found at {path} (no fallback library is searched)')
    return path


def project_files(project_dir):
    """The schematic project's files; exactly one ``.kicad_pro`` names it."""
    project_dir = Path(project_dir)
    projects = sorted(project_dir.glob('*.kicad_pro'))
    if len(projects) != 1:
        raise BoardBuildError(f'{project_dir}: exactly one .kicad_pro required, found {len(projects)}')
    name = projects[0].stem
    files = [projects[0]] + sorted(project_dir.glob('*.kicad_sch'))
    if not (project_dir / f'{name}.kicad_sch').is_file():
        raise BoardBuildError(f'{project_dir}: root schematic {name}.kicad_sch missing')
    files += [project_dir / t for t in PROJECT_TABLES if (project_dir / t).is_file()]
    return name, files


# --------------------------------------------------------------------------
# Native construction


def _vector(xy):
    import pcbnew
    return pcbnew.VECTOR2I(int(xy[0]), int(xy[1]))


def _seed(*hashes):
    # KiCad's own deterministic KIID generator (used by its QA): same inputs
    # give the same UUID stream, so identical inputs save identical bytes.
    import pcbnew
    pcbnew.KIID.SeedGenerator(int(_sha(''.join(hashes).encode())[:8], 16))


def _strip_template(board):
    # RemoveAll is the native bulk removal; removing items one at a time
    # through the bindings corrupts later plugin lookups on KiCad 10.
    board.RemoveAll()
    left = (len(board.GetFootprints()), len(board.GetTracks()), len(board.Zones()),
            len(board.GetDrawings()), len(board.Groups()), len(board.Markers()))
    if any(left):
        raise BoardBuildError(f'template items survived stripping: {left}')


def _outline(board, box):
    import pcbnew
    x0, y0 = box['x'], box['y']
    x1, y1 = x0 + box['w'], y0 + box['h']
    for start, end in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetWidth(EDGE_WIDTH_NM)
        shape.SetStart(_vector(start))
        shape.SetEnd(_vector(end))
        board.Add(shape)


def _footprint(board, comp, path, nets):
    import pcbnew
    from .probe_features import PROPERTY, parse_marker, footprint_signature
    if PROPERTY in comp['fields']:
        from .symbol_library import parse_sexpr
        record = parse_marker(comp['fields'][PROPERTY])
        if digest(path) != record['footprint_source']['sha256']:
            raise BoardBuildError(comp['ref'] + ': probe captured footprint bytes changed')
        if footprint_signature(parse_sexpr(path.read_bytes()), record['pin']['number']) != record['geometry']:
            raise BoardBuildError(comp['ref'] + ': probe captured pad geometry changed')
    lib, name = comp['footprint'].split(':')
    fp = pcbnew.FootprintLoad(str(path.parent), name)
    if fp is None:
        raise BoardBuildError(f"{comp['ref']}: pcbnew could not load {path}")
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    fp.SetReference(comp['ref'])
    fp.SetValue(comp['value'])
    for field, value in sorted(comp['fields'].items()):
        if field in ('Footprint', 'Reference', 'Value'):
            continue
        fp.SetField(field, value)
        fp.GetField(field).SetVisible(False)
    sheet = '' if comp['sheet_uuids'] == '/' else comp['sheet_uuids'].rstrip('/')
    fp.SetPath(pcbnew.KIID_PATH(f"{sheet}/{comp['uuid']}"))
    fp.SetSheetname(comp['properties'].get('Sheetname') or '')
    fp.SetSheetfile(comp['properties'].get('Sheetfile') or '')
    if 'dnp' in comp['properties']:
        fp.SetDNP(True)
    if 'exclude_from_bom' in comp['properties']:
        fp.SetExcludedFromBOM(True)
    pads = {}
    for pad in fp.Pads():
        pads.setdefault(pad.GetNumber(), []).append(pad)
    wanted = {pin: net for (ref, pin), net in nets.items() if ref == comp['ref']}
    absent = sorted(set(wanted) - set(pads))
    if absent:
        raise BoardBuildError(f"{comp['ref']}: pin(s) {absent} have no pad in {comp['footprint']} "
                              f"(pads: {sorted(p for p in pads if p)})")
    unexplained = sorted(p for p in pads if p and p not in wanted)
    if unexplained:
        raise BoardBuildError(f"{comp['ref']}: pad(s) {unexplained} of {comp['footprint']} "
                              'are not pins in the netlist')
    for number, net in wanted.items():
        for pad in pads[number]:
            pad.SetNet(net)
    return fp


def construct(netlist, envelope, template, footprints, output, seed_hashes):
    """Save the unplaced board. ``footprints`` maps LIB:NAME to its file path."""
    import pcbnew
    _seed(*seed_hashes)
    board = pcbnew.LoadBoard(str(template))
    if board.GetCopperLayerCount() != envelope['copper_layers']:
        raise BoardBuildError(f"template has {board.GetCopperLayerCount()} copper layers, "
                              f"envelope declares {envelope['copper_layers']}")
    _strip_template(board)
    _outline(board, envelope['outline_nm'])
    native_nets = {}
    for net in sorted(netlist['nets'], key=lambda n: n['name']):
        item = pcbnew.NETINFO_ITEM(board, net['name'])
        board.Add(item)
        native_nets[net['name']] = item
    terminals = {tuple(node): native_nets[net['name']] for net in netlist['nets'] for node in net['nodes']}
    fixed = {f['ref']: f for f in envelope['fixed_refs']}
    unknown = sorted(set(fixed) - {c['ref'] for c in netlist['components']})
    if unknown:
        raise BoardBuildError(f'envelope fixes unknown reference(s) {unknown}')
    box = envelope['outline_nm']
    staging_x = box['x'] + box['w'] + STAGING_GAP_NM
    for comp in netlist['components']:
        fp = _footprint(board, comp, footprints[comp['footprint']], terminals)
        board.Add(fp)
        if comp['ref'] in fixed:
            spec = fixed[comp['ref']]
            if spec['side'] == 'B':
                fp.SetLayerAndFlip(pcbnew.B_Cu)
            fp.SetOrientationDegrees(spec['rotation_deg'])
            fp.SetPosition(_vector(spec['position_nm']))
            fp.SetLocked(True)
        else:
            # Off-board staging row in reference order; the placer moves these.
            bbox = fp.GetBoundingBox(False)
            left = fp.GetPosition().x - bbox.GetX()
            fp.SetPosition(_vector([staging_x + left, box['y'] + bbox.GetHeight()]))
            staging_x += -(-bbox.GetWidth() // STAGING_PITCH_NM) * STAGING_PITCH_NM + STAGING_PITCH_NM
    output.parent.mkdir(parents=True, exist_ok=True)
    # Project settings are written separately from the template's project.
    if not pcbnew.SaveBoard(str(output), board, True):
        raise BoardBuildError(f'pcbnew failed to save {output}')
    from .probe_features import PROPERTY, parse_marker, validate_board
    probes = {c['ref']: parse_marker(c['fields'][PROPERTY]) for c in netlist['components']
              if PROPERTY in c['fields']}
    try:
        validate_board(output.read_bytes(), probes)
    except ValueError as exc:
        raise BoardBuildError(str(exc)) from exc


def construct_isolated(spec, scratch, mode='--construct'):
    """Run ``construct`` (or ``apply_row``) in a fresh interpreter.

    KiCad's seeded KIID generator is process-global and its footprint plugin
    caches parsed libraries, so a second build in one process would not repeat
    the UUID stream. A fresh child gives identical bytes for identical inputs
    and leaves the caller's process unseeded.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    spec_path = scratch / f'{mode[2:]}-spec.json'
    write_json(spec_path, spec)
    argv = [sys.executable, '-B', '-m', 'agent_ee.board_build', mode, str(spec_path)]
    try:
        # The child imports this same package source, whatever the caller's path.
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
            [str(Path(__file__).resolve().parents[1])] + [p for p in [os.environ.get('PYTHONPATH')] if p])}
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolFailure(f'pcbnew construction could not run: {exc}') from exc
    (scratch / f'{mode[2:]}.stderr.txt').write_text(completed.stderr)
    if completed.returncode == CONSTRUCT_REFUSED:
        raise BoardBuildError(completed.stdout.strip())
    if completed.returncode != 0 or not Path(spec['output']).is_file():
        raise ToolFailure(f'pcbnew {mode[2:]} failed (exit {completed.returncode}); '
                          f'see {scratch.name}/{mode[2:]}.stderr.txt')


CONSTRUCT_REFUSED = 3


def _construct_main(spec_path):
    spec = read_json(spec_path)
    try:
        construct(spec['netlist'], spec['envelope'], Path(spec['template']),
                  {k: Path(v) for k, v in spec['footprints'].items()}, Path(spec['output']), spec['seed_hashes'])
    except BoardBuildError as exc:
        print(exc)
        return CONSTRUCT_REFUSED
    return 0


# --------------------------------------------------------------------------
# Row stand-in placement (unqualified; AGENTEE-109 is the owned fix)

ROW_PLACER = 'rung1-row-standin-experimental'
ROW_REASON = 'placer.pack refused stock pad shapes (AGENTEE-109)'
PAD_SHAPE_REFUSAL = 'only axis-aligned rectangular pads supported'


def row_plan(facts, envelope, gap_nm):
    """One deterministic row on the outline's horizontal centre line.

    Movable footprints keep their side, get rotation 0 and follow the fixed
    references left to right in reference order, spaced by courtyard width
    plus the request's gap. Returns (placements, None) or (None, reason).
    """
    box = envelope['outline_nm']
    centre_y = box['y'] + box['h'] // 2
    fixed = {f['ref'] for f in envelope['fixed_refs']}

    def bounds(fp):
        courtyard = fp['courtyards'].get(fp['side'], {})
        if courtyard.get('status') != 'proven':
            return None
        points = [p for poly in courtyard['polygons'] for p in poly]
        return (min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points))

    left = box['x']
    for fp in facts['footprints']:
        if fp['ref'] in fixed:
            edges = bounds(fp)
            if edges is None:
                return None, f"fixed {fp['ref']} has no proven courtyard on its side"
            left = max(left, edges[2])
    left += gap_nm
    placements = []
    for fp in sorted((f for f in facts['footprints'] if f['ref'] not in fixed), key=lambda f: f['ref']):
        edges = bounds(fp)
        if edges is None or fp['rotation_deg'] % 360:
            return None, f"{fp['ref']}: needs a proven mounting-side courtyard at rotation 0"
        x, y = fp['position_nm']
        width = edges[2] - edges[0]
        placements.append({'ref': fp['ref'], 'side': fp['side'], 'rotation_deg': 0,
                           'position_nm': [left - (edges[0] - x),
                                           centre_y - ((edges[1] - y) + (edges[3] - y)) // 2]})
        left += width + gap_nm
    return placements, None


def apply_row(board_path, placements, output):
    import pcbnew
    board = pcbnew.LoadBoard(str(board_path))
    for item in placements:
        fp = board.FindFootprintByReference(item['ref'])
        if fp is None or fp.IsLocked():
            raise BoardBuildError(f"row stand-in cannot move {item['ref']}")
        fp.SetOrientationDegrees(item['rotation_deg'])
        fp.SetPosition(_vector(item['position_nm']))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not pcbnew.SaveBoard(str(output), board, True):
        raise BoardBuildError(f'pcbnew failed to save {output}')


def _row_main(spec_path):
    spec = read_json(spec_path)
    try:
        apply_row(Path(spec['board']), spec['placements'], Path(spec['output']))
    except BoardBuildError as exc:
        print(exc)
        return CONSTRUCT_REFUSED
    return 0


# --------------------------------------------------------------------------
# Project and native checks


def write_project(name, files, template_project, destination):
    """Copy the schematic project; take board rules and net classes from the template."""
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination / path.name
        if path.suffix == '.kicad_pro':
            project = json.loads(path.read_bytes())
            template = json.loads(template_project.read_bytes())
            merged = dict(project)
            for key in ('board', 'net_settings'):
                if key in template:
                    merged[key] = template[key]
                else:
                    merged.pop(key, None)
            if merged == project:
                shutil.copyfile(path, target)
            else:
                target.write_text(json.dumps(merged, indent=2) + '\n')
        else:
            shutil.copyfile(path, target)


def _netclasses(netlist, template_project):
    template = json.loads(template_project.read_bytes())
    declared = {c.get('name') for c in template.get('net_settings', {}).get('classes', [])}
    for net in netlist['nets']:
        if net['class'] != 'Default':
            raise BoardBuildError(f"net {net['name']}: netclass {net['class']} assignment from the "
                                  'schematic is not implemented by this builder'
                                  + ('' if net['class'] in declared else ' (and absent from the template)'))


class ToolFailure(RuntimeError):
    """A native tool did not produce evidence; this is missing coverage, never a pass."""


def parity_drc(board, scratch):
    """Native DRC with schematic parity on a copy of the board's project.

    kicad-cli writes project-local state (``.kicad_prl``) beside the board, so
    it runs on a copy and the published project stays byte-stable.
    """
    project = scratch / 'project'
    shutil.copytree(board.parent, project)
    copy = project / board.name
    report_path = scratch / 'drc-parity.json'
    argv = ['kicad-cli', 'pcb', 'drc', '--schematic-parity', '--format', 'json', '--severity-all',
            '--units', 'mm', '--output', str(report_path), str(copy)]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolFailure(f'native parity DRC could not run: {exc}') from exc
    (scratch / 'drc-parity.stdout.txt').write_text(completed.stdout)
    (scratch / 'drc-parity.stderr.txt').write_text(completed.stderr)
    if completed.returncode != 0 or not report_path.is_file():
        raise ToolFailure(f'native parity DRC failed (exit {completed.returncode}): {completed.stderr.strip()}')
    if digest(copy) != digest(board):
        raise ToolFailure('native DRC changed the board copy it evaluated')
    report = read_json(report_path)
    for key in ('violations', 'unconnected_items', 'schematic_parity', 'ignored_checks'):
        if not isinstance(report.get(key), list):
            raise ToolFailure(f'native DRC report lacks {key}; parity not evaluated')
    ignored = sorted(c.get('key') for c in report['ignored_checks'] if isinstance(c, dict))
    if set(ignored) & set(PARITY_KEYS):
        raise ToolFailure(f'parity checks explicitly ignored by the project: {sorted(set(ignored) & set(PARITY_KEYS))}')
    types = {}
    for item in report['violations']:
        types[item.get('type')] = types.get(item.get('type'), 0) + 1
    return {'argv': argv, 'exit_code': completed.returncode, 'report': report_path,
            'report_sha256': digest(report_path), 'kicad_version': report.get('kicad_version'),
            'schematic_parity': len(report['schematic_parity']),
            'parity_items': [{'type': i.get('type'), 'description': i.get('description')}
                             for i in report['schematic_parity']],
            'unconnected_items': len(report['unconnected_items']),
            'violations': len(report['violations']), 'violation_types': dict(sorted(types.items())),
            'ignored_checks': ignored}


def tool_identity():
    import _pcbnew
    import pcbnew
    cli = shutil.which('kicad-cli')
    version = subprocess.run([cli, 'version'], capture_output=True, text=True, timeout=30) if cli else None
    return {'pcbnew': {'version': pcbnew.Version(), 'build_version': pcbnew.GetBuildVersion(),
                       'binary': _pcbnew.__file__, 'sha256': digest(_pcbnew.__file__)},
            'kicad-cli': {'path': cli, 'sha256': digest(cli) if cli else None,
                          'version_output': version.stdout.strip() if version else None},
            'board_build': {'module': 'agent_ee.board_build', 'sha256': digest(__file__)},
            'python': {'executable': sys.executable, 'version': sys.version.split()[0]}}


# --------------------------------------------------------------------------
# Driver


def _fresh_out(out):
    out = Path(out).absolute()
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise BoardBuildError(f'output directory {out} must be absent or empty')
    out.mkdir(parents=True, exist_ok=True)
    return out.resolve()


def build(project_dir, netlist_path, fpdir, envelope_path, template_board, request_path, out,
          placement_fallback=None):
    """Build, place and parity-check; return the receipt (also written to OUTDIR)."""
    if placement_fallback not in (None, 'row'):
        raise BoardBuildError(f'unknown placement fallback {placement_fallback!r}')
    from .placement import judge
    from .placement_probe import extract
    from .placer_runner import place_candidate
    out = _fresh_out(out)
    paths = {'netlist': Path(netlist_path), 'template_board': Path(template_board),
             'envelope': Path(envelope_path), 'placement_request': Path(request_path)}
    paths['template_project'] = paths['template_board'].with_suffix('.kicad_pro')
    for label, path in paths.items():
        if not path.is_file():
            raise BoardBuildError(f'{label} {path} is not a file')
    data = {label: path.read_bytes() for label, path in paths.items()}
    inputs = {label: {'path': str(path), 'sha256': _sha(data[label])} for label, path in paths.items()}
    netlist = read_netlist(data['netlist'])
    envelope = read_envelope(json.loads(data['envelope']))
    request = read_request(json.loads(data['placement_request']), envelope)
    unknown = sorted({f['ref'] for f in envelope['fixed_refs']} - {c['ref'] for c in netlist['components']})
    if unknown:
        raise BoardBuildError(f'envelope fixes unknown reference(s) {unknown}')
    name, files = project_files(project_dir)
    if netlist['source'] is not None and Path(netlist['source']).name not in {f.name for f in files}:
        raise BoardBuildError(f"netlist source {netlist['source']} is not a schematic of project {name}")
    _netclasses(netlist, paths['template_project'])
    footprints = {lib_id: footprint_path(fpdir, lib_id)
                  for lib_id in sorted({c['footprint'] for c in netlist['components']})}
    inputs['project_files'] = {p.name: {'path': str(p), 'sha256': digest(p)} for p in files}
    inputs['footprints'] = {lib_id: {'path': str(p), 'sha256': digest(p)} for lib_id, p in footprints.items()}
    receipt = {'schema': RECEIPT_SCHEMA, 'qualification': 'experimental-unqualified',
               'name': name, 'inputs': inputs, 'tools': tool_identity(),
               'stackup': {'declared': envelope['stackup'], 'source': 'template board setup and project'},
               'envelope_nm': envelope}

    unplaced_dir = out / 'unplaced'
    write_project(name, files, paths['template_project'], unplaced_dir)
    unplaced = unplaced_dir / f'{name}.kicad_pcb'
    seeds = ([inputs[k]['sha256'] for k in ('netlist', 'template_board', 'envelope')]
             + [entry['sha256'] for entry in inputs['footprints'].values()])
    construct_isolated({'netlist': netlist, 'envelope': envelope, 'template': str(paths['template_board']),
                        'footprints': {k: str(v) for k, v in footprints.items()},
                        'output': str(unplaced), 'seed_hashes': seeds}, out / 'construct')
    receipt['unplaced'] = {'board': str(unplaced.relative_to(out)), 'sha256': digest(unplaced),
                           'parity': _relative(parity_drc(unplaced, out / 'checks' / 'unplaced'), out)}

    root = out / 'placement'
    root.mkdir()
    effective = {'policy': request['policy'], 'authorization': {
        **request['authorization'], 'source_sha256': digest(unplaced), 'writable_root': str(root)}}
    write_json(out / 'placement-request.json', effective)
    receipt['placement_request'] = {'path': 'placement-request.json', 'sha256': digest(out / 'placement-request.json')}
    try:
        report = place_candidate(unplaced, paths['netlist'], root / 'proposal', effective)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # The runner retains its own NAME-failure-* diagnostics under the root.
        receipt['placement'] = {'status': 'not_placed', 'execution': 'failed', 'outcome': 'unknown',
                                'error': str(exc)}
        receipt.update(status='placement_failed', outcome='unknown')
        return _finish(receipt, out)
    plan = report.get('plan', report)
    receipt['placement'] = {'status': report.get('status'), 'outcome': report.get('outcome'),
                            'reason': report.get('reason'), 'steps_used': plan.get('steps_used'),
                            'step_budget': plan.get('step_budget')}
    receipt['placer'] = 'agent_ee.placer.pack (AGENTEE-54)'
    if report['status'] != 'placed':
        write_json(root / 'placement-plan.json', report)
        receipt['placement']['plan'] = 'placement/placement-plan.json'
        if (placement_fallback == 'row' and report['status'] == 'unsupported_geometry'
                and report.get('reason') == PAD_SHAPE_REFUSAL):
            return _row_standin(receipt, out, name, files, paths, unplaced, envelope, request)
        receipt.update(status='not_placed', outcome=report.get('outcome', 'unknown'))
        return _finish(receipt, out)

    placed_dir = out / 'placed'
    write_project(name, files, paths['template_project'], placed_dir)
    placed = placed_dir / f'{name}.kicad_pcb'
    shutil.copyfile(root / 'proposal' / 'candidate.kicad_pcb', placed)
    shutil.copyfile(root / 'proposal' / 'placement-result.json', placed_dir / 'placement-result.json')
    if digest(placed) != report['board_sha256']:
        raise BoardBuildError('placed board differs from the placer-published candidate')
    judgment = judge(extract(placed))
    write_json(out / 'checks' / 'placed' / 'placement-judgment.json', judgment)
    parity = _relative(parity_drc(placed, out / 'checks' / 'placed'), out)
    receipt['placed'] = {'board': str(placed.relative_to(out)), 'sha256': digest(placed),
                         'placement_result': 'placed/placement-result.json',
                         'judgment': {'verdict': judgment['verdict'], 'outcome': judgment['outcome'],
                                      'path': 'checks/placed/placement-judgment.json'},
                         'parity': parity}
    clean = judgment['verdict'] == 'pass' and parity['schematic_parity'] == 0
    receipt.update(status='placed' if clean else 'placed_with_defects', outcome='info' if clean else 'defect')
    return _finish(receipt, out)


def _row_standin(receipt, out, name, files, paths, unplaced, envelope, request):
    """Unqualified row placement; published as placed only when every check is clean."""
    from .placement import judge
    from .placement_probe import extract
    receipt.update(placer=ROW_PLACER, placer_reason=ROW_REASON)
    placements, refusal = row_plan(extract(unplaced), envelope, request['policy']['gap_nm'])
    if placements is None:
        receipt['row_standin'] = {'status': 'not_placed', 'reason': refusal}
        receipt.update(status='not_placed', outcome='unknown')
        return _finish(receipt, out)
    staging = out / 'placement' / 'row-standin'
    write_project(name, files, paths['template_project'], staging)
    board = staging / f'{name}.kicad_pcb'
    construct_isolated({'board': str(unplaced), 'placements': placements, 'output': str(board)},
                       out / 'placement' / 'row-apply', mode='--row-apply')
    judgment = judge(extract(board))
    write_json(out / 'checks' / 'row-standin' / 'placement-judgment.json', judgment)
    parity = _relative(parity_drc(board, out / 'checks' / 'row-standin'), out)
    result = {'schema': 'agent-ee.rung1-row-standin-result/0-experimental',
              'qualification': 'experimental-unqualified', 'placer': ROW_PLACER, 'placer_reason': ROW_REASON,
              'source_sha256': digest(unplaced), 'board_sha256': digest(board),
              'gap_nm': request['policy']['gap_nm'], 'placements': placements,
              'judgment': {'verdict': judgment['verdict'], 'outcome': judgment['outcome']}}
    write_json(staging / 'placement-result.json', result)
    clean = (judgment['verdict'] == 'pass' and parity['schematic_parity'] == 0
             and parity['violations'] == 0)
    record = {'board': str(board.relative_to(out)), 'sha256': digest(board),
              'placements': placements, 'placement_result': str((staging / 'placement-result.json').relative_to(out)),
              'judgment': {'verdict': judgment['verdict'], 'outcome': judgment['outcome'],
                           'path': 'checks/row-standin/placement-judgment.json'},
              'parity': parity}
    receipt['row_standin'] = record
    receipt['judge_verdict'] = judgment['verdict']
    if not clean:
        receipt.update(status='placed_with_defects', outcome='defect')
        return _finish(receipt, out)
    shutil.copytree(staging, out / 'placed')
    receipt['placed'] = {**record, 'board': f'placed/{name}.kicad_pcb', 'placement_result': 'placed/placement-result.json'}
    receipt.update(status='placed', outcome='info')
    return _finish(receipt, out)


def _relative(record, out):
    record['report'] = str(Path(record['report']).relative_to(out))
    return record


def _finish(receipt, out):
    receipt['outputs'] = {str(p.relative_to(out)): digest(p) for p in sorted(out.rglob('*'))
                          if p.is_file() and not p.name.endswith('.writer.lock')}
    write_json(out / 'build-receipt.json', receipt)
    return receipt


EXIT = {'placed': 0, 'not_placed': 2, 'placed_with_defects': 3, 'placement_failed': 4}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--construct'] and len(argv) == 2:
        return _construct_main(argv[1])
    if argv[:1] == ['--row-apply'] and len(argv) == 2:
        return _row_main(argv[1])
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('project_dir')
    parser.add_argument('--netlist', required=True)
    parser.add_argument('--footprints', required=True)
    parser.add_argument('--envelope', required=True)
    parser.add_argument('--template-board', required=True)
    parser.add_argument('--placement-request', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--placement-fallback', choices=['row'],
                        help='unqualified row stand-in when the placer refuses stock pad shapes (AGENTEE-109)')
    args = parser.parse_args(argv)
    try:
        receipt = build(args.project_dir, args.netlist, args.footprints, args.envelope,
                        args.template_board, args.placement_request, args.out, args.placement_fallback)
    except BoardBuildError as exc:
        print(f'board_build: refused: {exc}', file=sys.stderr)
        return 1
    except ToolFailure as exc:
        print(f'board_build: tool failure (missing coverage, not a pass): {exc}', file=sys.stderr)
        return 5
    print(json.dumps({k: receipt.get(k) for k in ('status', 'outcome', 'placement')}, sort_keys=True))
    return EXIT[receipt['status']]


if __name__ == '__main__':
    raise SystemExit(main())
