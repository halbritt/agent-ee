"""Rung 1 on the owned pipeline: a staged repair campaign from an EMPTY candidate (decision 1).

Run ``python3 -m agent_ee.rung1_qualified SPEC.md --out RUNDIR [--profile P] [--dry-run]``
(see docs/rung1-qualified.md). It follows ``agent_ee.rung1_campaign`` but replaces every
experimental stand-in with the owned module now on main:

- schematic: ``schematic_emitter`` (AGENTEE-40) on a connectivity intent, layout and captures;
- board population: ``forward_annotation`` (AGENTEE-52) from the native netlist and a
  captured ``agent-ee.footprint-libraries/1`` manifest, into the empty scaffold board;
- placement: the campaign ``placement_adapter`` (AGENTEE-91) with the pinned placer helper
  ``placer_runner`` (AGENTEE-54); no row fallback;
- routing: ``routing_adapter.step`` (AGENTEE-93) on the pinned kicad-route checkout;
- fab: the ``fab_export`` (AGENTEE-23) and ``fab_consistency`` (AGENTEE-73) checks and, with a
  ``--cpl-inputs`` capture, ``cpl_rotation`` with a receipted export step (AGENTEE-126); a
  rotation status outside ``fab_package.DONE_ROTATION_STATUSES`` keeps "done" false.

The driver's own records are ``qualification: experimental-unqualified``; checks and receipts
are the owned ones. Where an owned module cannot do its job headlessly, the exact refusal is
recorded as a gap; nothing is substituted.
"""
import argparse
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from . import executor
from . import generate as g
from . import rung1_campaign as rc
from .storage import canonical, digest, read_json, write_json

QUALIFICATION = 'experimental-unqualified'
RECORD_SCHEMA = 'agent-ee.rung1-qualified-record/0-experimental'
SCAFFOLD_SCHEMA = 'agent-ee.rung1-qualified-scaffold/0-experimental'
PROBE_SCHEMA = 'agent-ee.rung1-qualified-probes/0-experimental'
NAME = g.PROJECT_NAME
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
STEPS_DIR = REPO_ROOT / 'fixtures' / 'rung1' / 'qualified' / 'steps'
IMAGE_KICAD_ROUTE = Path('/opt/kicad-route')
# The pinned kicad-route checkout: AGENT_EE_KICAD_ROUTE_CHECKOUT when set, else the
# standard image path. routing_adapter.preflight still qualifies whatever is named.
DEFAULT_KICAD_ROUTE = Path(os.environ.get('AGENT_EE_KICAD_ROUTE_CHECKOUT') or IMAGE_KICAD_ROUTE)
LAYOUT_NAMESPACE = str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/halbritt/agent-ee/rung1-qualified/1'))
STAGE_ORDER = ['schematic', 'placement', 'routing', 'verification', 'fab']
MOUNT_STEPS, MOUNT_SOURCE = '/tool/steps', '/tool/agent-ee'
# Owned modules per stage, with their Plane items (reported, never asserted as acceptance).
OWNED = {
    'requirements': ('design_dialog.capture_dialog, design_intent.import_record (via agent_ee.generate stage 1)',
                     'AGENTEE-25, AGENTEE-26'),
    'envelope': ('board_envelope.derive / stage_one_requirements / round_trip', 'AGENTEE-85'),
    'schematic': ('schematic_emitter', 'AGENTEE-40'),
    'board_population': ('forward_annotation', 'AGENTEE-52'),
    'placement': ('placement_adapter + placer_runner (agent-ee.placer/1)', 'AGENTEE-91, AGENTEE-54'),
    'routing': ('routing_adapter.step + kicad_route_attempt (kicad-route 08179cd)', 'AGENTEE-93'),
    'parity': ('netlist_parity check (intent, topology, exactly); fixtures/rc/control as recorded reference',
               'AGENTEE-41, AGENTEE-102'),
    'placement_checks': ('placement check kind (courtyard-overlap/1, outline-containment/1)', 'AGENTEE-92'),
    'part_binding': ('part_binding check', 'AGENTEE-32'),
    'fab': ('fab_export + fab_consistency + cpl_rotation checks', 'AGENTEE-23, AGENTEE-73, AGENTEE-126'),
    'staging': ('stages.py staged scope + stage_receipts + campaign step plan', 'AGENTEE-48/88, AGENTEE-95'),
}
FOOTPRINT_LIBRARIES = ('Resistor_SMD', 'Capacitor_SMD', 'Connector_PinHeader_2.54mm')
CATALOG_CATEGORIES = {'Resistor_SMD': ('resistor', 'resistance', 'ohm'),
                      'Capacitor_SMD': ('capacitor', 'capacitance', 'F')}

QUALIFIED_REQUIREMENTS = """# Rung-1 requirements graded by the owned checks

Derived from `spec.md` by `agent_ee.rung1_qualified`. Each line is one requirement that one
coordinator check grades. They add no limit that the spec does not state.

R-parity: The generated schematic must have the connectivity of the rung-1 intent built from the stage-1 record and the stage-2 selections (R1 between /IN and /OUT, C1 between /OUT and GND, J1 pins 1, 2 and 3 on /IN, /OUT and GND, a power:GND symbol and a PWR_FLAG on GND): native netlist topology equal to the captured intent resolved against the captured libraries, component multiset exactly.

R-courtyard: Footprint courtyards on the 2-layer board must not overlap (native DRC courtyard evaluation).

R-outline: Every footprint courtyard must lie inside the 50 mm x 30 mm board outline.

R-binding: R1 and C1 are JLCPCB-assembled SMD parts, so each must carry a valid LCSC, Manufacturer and Partnumber binding, consistent between schematic and board.

R-fab: A JLCPCB fab package (Gerbers, drill, BOM, CPL, manifest) must be exported from the captured board.

R-fab-consistency: The fab package must describe the captured board: inventory, designator parity, LCSC-footprint uniqueness, CPL side and 3D model resolution.

R-assembly: Populated parts must resolve to the captured catalog with complete BOM properties, passive ratings and stock coverage. The assembled-header spec explicitly includes J1.

R-packages: Assigned footprints must agree with catalog packages through the captured package map. Unmapped names remain missing coverage.
"""
# Appended to qualified.md, and the cpl_rotation check declared, only for a --cpl-inputs capture (AGENTEE-126).
CPL_REQUIREMENT_TEXT = ('\nR-cpl: The exported CPL must carry rotations and midpoints checked against captured '
                        'policy and supplier footprints. Physical JLCPCB preview remains required.\n')


class PrepareFailure(Exception):
    """Generation inputs or the scaffold could not be established; nothing runs after it."""


def rel(path, root):
    return rc.rel(path, root)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ owned generation inputs

SYMBOL_CAPTURES = 'stock/symbols'  # the scope's captured stock symbol libraries
POWER_GND = {'id': 'pwr_gnd', 'reference': '#PWR01', 'lib_id': 'power:GND', 'value': 'GND', 'units': [1],
             'footprint': ''}
FLAG = {'id': 'FLG1', 'reference': '#FLG01', 'lib_id': 'power:PWR_FLAG', 'value': 'PWR_FLAG', 'units': [1],
        'footprint': ''}


def qualified_intent(prep):
    """The rung-1 IR: the generate intent (stage-1 record, stage-2 selections) for the emitter.

    The emitter's label names exclude '/': KiCad prefixes a root-sheet local label with the
    sheet path, so labels IN and OUT export as /IN and /OUT. The ground net is driven by a
    declared power:GND symbol with a power:PWR_FLAG (every other pin is passive, so the flag
    drives the GND power input for ERC). It is a global power net and exports as GND. The
    emitter writes both symbols in_bom/on_board no, KiCad's native convention, so the staged
    presence probe and the native netlist agree on C1, J1 and R1 (AGENTEE-123). Library paths
    name the scope's captured libraries under ``stock/symbols``, so the emitter step and the
    intent-sourced parity check resolve the same bytes."""
    intent = copy.deepcopy(prep['intent'])
    ground = ground_net(prep['params'])
    for library in intent['libraries']:
        library['path'] = f"{SYMBOL_CAPTURES}/{library['path']}"
    for net in intent['nets']:
        net['name'] = net['name'].lstrip('/')
    nets = [n for n in intent['nets'] if n['name'] == ground]
    if ground != 'GND' or len(nets) != 1:
        # The stock power:GND symbol names exactly one net GND; no other ground is approximated.
        raise PrepareFailure(f'ground net {ground!r} has no stock power symbol in the intent')
    for comp in (copy.deepcopy(POWER_GND), copy.deepcopy(FLAG)):
        intent['components'].append(comp)
        nets[0]['terminals'].append({'component': comp['id'], 'pin': {'number': '1'}})
    return intent


def intent_captures(intent):
    """The captured library bytes the intent names, keyed by its own library paths."""
    return {lib['path']: (g.STOCK / 'symbols' / Path(lib['path']).name).read_bytes() for lib in intent['libraries']}


def ground_net(params):
    """The native netlist name of the ground net: a global power net, without a sheet prefix."""
    return params['nets']['ground'].lstrip('/')


def layout():
    return {'schema': 'agent-ee.schematic-layout/1', 'namespace': LAYOUT_NAMESPACE, 'project': NAME,
            'transforms': {}}


def root_sheet_uuid():
    from .schematic_emitter import _uuid
    return _uuid(uuid.UUID(LAYOUT_NAMESPACE), 'sheet', '/')


def envelope_record(params):
    """agent-ee.envelope/1 (AGENTEE-85): outline, stackup and the fixed J1 on the left edge."""
    board, header = params['board'], params['header']
    x0, y0 = 100, 100
    x1, y1 = x0 + board['width_mm'], y0 + board['height_mm']
    pitch = 2.54
    # Pin 1 is the footprint origin; the column of pins is centred vertically, one pitch inside.
    pin1_y = round(y0 + board['height_mm'] / 2 - pitch * (header['pins'] - 1) / 2, 4)
    return {
        'schema': 'agent-ee.envelope/1', 'id': 'rung1-rc',
        'outline': {'edges': [
            {'name': 'top', 'start': [x0, y0], 'end': [x1, y0]},
            {'name': 'right', 'start': [x1, y0], 'end': [x1, y1]},
            {'name': 'bottom', 'start': [x1, y1], 'end': [x0, y1]},
            {'name': 'left', 'start': [x0, y1], 'end': [x0, y0]}]},
        'layer_count': board['copper_layers'],
        'stackup': {'thickness_mm': 1.6, 'layers': [
            {'name': 'F.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1},
            {'name': 'core', 'kind': 'dielectric', 'thickness_mm': 1.53},
            {'name': 'B.Cu', 'kind': 'copper', 'thickness_mm': 0.035, 'copper_weight_oz': 1}]},
        'fab_profile': None, 'mounting_holes': [], 'keepouts': [],
        'fixed_footprints': [{'reference': header['ref'], 'side': 'front', 'edge': 'left',
                              'position_mm': round(y1 - pin1_y, 4), 'inset_mm': pitch, 'rotation_deg': 0,
                              'insertion': 'from_top', 'tolerance_mm': 0.5}]}


def outline_rectangle(validation):
    """The scaffold Edge.Cuts rectangle, read back from the envelope's own validation record."""
    outline = validation.get('outline') or {}
    if not outline.get('axis_aligned_rectangle'):
        raise PrepareFailure('envelope outline is not an axis-aligned rectangle')
    low, high = ([float(v) for v in outline['bounds_mm'][k]] for k in ('min', 'max'))
    return {'x': low[0], 'y': low[1], 'w': high[0] - low[0], 'h': high[1] - low[1]}


def footprint_manifest(parts):
    """agent-ee.footprint-libraries/1: exactly the stock members the parts manifest names."""
    libraries = {}
    for ref, fields in sorted(parts.items()):
        library, name = fields['Footprint'].split(':')
        path = f'{library}.pretty/{name}.kicad_mod'
        member = {'path': path, 'sha256': digest(g.STOCK / 'footprints' / path)}
        members = libraries.setdefault(library, [])
        if member not in members:
            members.append(member)
    return {'schema': 'agent-ee.footprint-libraries/1', 'complete': True,
            'libraries': [{'name': n, 'members': sorted(m, key=lambda x: x['path'])}
                          for n, m in sorted(libraries.items())]}


EDGE_CLEARANCE_NM = 500_000  # the template board's copper-to-edge clearance (board setup, 0.5 mm)


def edge_bands(outline, width=EDGE_CLEARANCE_NM):
    """Four obstacle rectangles along the inside of the outline, per board side.

    The placer grades courtyards against the outline only; copper-to-edge clearance is a native
    DRC rule it does not read. Declaring the rule as obstacles (plus the policy gap) keeps every
    courtyard, and so every pad, clear of it. The bands are captured policy input."""
    nm = lambda v: int(round(v * 1_000_000))
    x0, y0 = nm(outline['x']), nm(outline['y'])
    x1, y1 = x0 + nm(outline['w']), y0 + nm(outline['h'])
    gap = width // 5
    rects = [(x0, y0, x1, y0 + width), (x0, y1 - width, x1, y1),
             (x0, y0 + width + gap, x0 + width, y1 - width - gap),
             (x1 - width, y0 + width + gap, x1, y1 - width - gap)]
    # One obstacle per band: the placer reserves the bounding box of each obstacle's polygons.
    return [{'side': side, 'polygons': [[[a, b], [c, b], [c, d], [a, d]]]}
            for side in ('F', 'B') for a, b, c, d in rects]


def packing_policy(params, outline):
    """agent-ee.packing-policy/1 for the placer helper. J1 is not fixed here: forward annotation
    stages it off-board, and the envelope fixed-footprint binding cannot be captured (see gaps)."""
    return {'schema': 'agent-ee.packing-policy/1', 'step_budget': 200000, 'gap_nm': 1_000_000,
            'fixed_refs': [], 'priority_refs': [], 'ground_nets': [ground_net(params)],
            'obstacles': edge_bands(outline)}


def golden_netlist(work):
    """kicad-cli XML netlist of the reference fixtures/rc/control, exported in the sandbox from a copy."""
    candidate = g.native_candidate(work / 'candidate', g.FIXTURE_PROJECT, FOOTPRINT_LIBRARIES)
    run = work / 'export'
    g.seed_kicad_config(run, candidate)
    argv = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', '/work/design.xml',
            '/candidate/project/rc_filter.kicad_sch']
    record = executor.execute(candidate, run, argv, timeout=600)
    out = run / 'design.xml'
    if record['execution'] != 'ok' or not out.is_file() or out.stat().st_size == 0:
        raise PrepareFailure('reference netlist export failed: ' + json.dumps(
            {k: record.get(k) for k in ('execution', 'exit_code')}))
    return out.read_bytes(), {'argv': argv, 'execution': record['execution'], 'exit_code': record['exit_code'],
                              'source': str(g.FIXTURE_PROJECT / 'rc_filter.kicad_sch'),
                              'source_sha256': digest(g.FIXTURE_PROJECT / 'rc_filter.kicad_sch'),
                              'sha256': digest(out)}


REFERENCE_SCHEMA = 'agent-ee.rung1-reference-parity/0-experimental'
GOLDEN = 'generation/golden/rc_control.xml'


def reference_parity(native_xml, golden_xml):
    """Descriptive comparison of one sealed native netlist with the fixtures/rc/control export.

    The control is a reference, not an oracle: R-parity is graded by the scope's intent-sourced
    netlist_parity check. This reuses the owned inventory reader and exact comparison and only
    reports what they found; a read failure is recorded, never treated as agreement."""
    from .native_netlist import read_native_inventory
    from .netlist_parity import compare, validate_policy
    record = {'schema': REFERENCE_SCHEMA, 'qualification': QUALIFICATION, 'role': 'reference_not_oracle',
              'reference': GOLDEN, 'predicate': 'topology', 'multiset': 'exactly'}
    try:
        record.update(reference_sha256=digest(golden_xml), native_xml_sha256=digest(native_xml))
        reference, native = read_native_inventory(golden_xml), read_native_inventory(native_xml)
        report = compare(reference, native, validate_policy({
            'source': {'kind': 'golden_xml', 'path': GOLDEN}, 'netlist_check': 'netlist',
            'predicate': 'topology', 'multiset': 'exactly'}))
    except Exception as exc:  # noqa: BLE001 - recorded as observed; never agreement
        return {**record, 'execution': 'failed', 'outcome': 'unknown', 'error': f'{type(exc).__name__}: {exc}'}
    def inventory(graph):
        return {'components': sorted(c['reference'] for c in graph['components'].values()),
                'nets': sorted(n['name'] for n in graph['nets'].values())}
    return {**record, 'execution': report['execution'], 'outcome': report['outcome'],
            'reference_inventory': inventory(reference), 'native_inventory': inventory(native),
            'missing_components': report['missing_components'], 'extra_components': report['extra_components'],
            'matched_nets': report.get('matched_nets'), 'missing_nets': report.get('missing_nets'),
            'extra_nets': report.get('extra_nets'), 'reason': report.get('reason')}


def catalog_attempt(prep, work):
    """Try the owned offline catalog capture (AGENTEE-27) on the fome-mc BOM; never invent fields.

    The BOM has no manufacturer, MPN, stock, price, Basic/Preferred or tolerance columns, so
    those cells stay empty. The owned capture decides; its refusal is recorded verbatim."""
    from .catalog import capture_catalog
    copies = sorted(prep['parts_path'].parent.glob('catalog/*.csv'))
    if len(copies) != 1:
        raise PrepareFailure('expected one catalog copy beside the parts manifest')
    raw = copies[0].read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    out_rows, seen = [], set()
    for row in rows:
        library, _, footprint = row['Footprint'].partition(':')
        if library not in CATALOG_CATEGORIES or not row['LCSC Part #'] or row['LCSC Part #'] in seen:
            continue
        seen.add(row['LCSC Part #'])
        category, attribute, unit = CATALOG_CATEGORIES[library]
        package = next((size for size in g.CHIP_SIZES if f'_{size}_' in footprint), footprint)
        out_rows.append({'lcsc': row['LCSC Part #'], 'mfr': '', 'mpn': '', 'package': package,
                         'category': category, 'stock': '', 'price1': '', 'is_basic': '', 'is_preferred': '',
                         'last_seen': '', 'attributes': json.dumps({attribute: row['Comment']})})
    work.mkdir(parents=True)
    source = work / 'source'
    source.mkdir()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=['lcsc', 'mfr', 'mpn', 'package', 'category', 'stock', 'price1',
                                                'is_basic', 'is_preferred', 'last_seen', 'attributes'],
                            lineterminator='\n')
    writer.writeheader()
    writer.writerows(out_rows)
    volume = buffer.getvalue().encode('utf-8')
    (source / 'fome-mc-bom.csv').write_bytes(volume)
    manifest = {'schema': 'agent-ee.catalog-source/1', 'snapshot_date': '2026-10-17',
                'source_url': prep['catalog']['source'].get('remote') if prep['catalog'].get('source') else None,
                'source_commit': (prep['catalog'].get('source') or {}).get('commit'),
                'currency': 'USD', 'stale_before': None,
                'volumes': [{'path': 'fome-mc-bom.csv', 'sha256': sha(volume), 'size_bytes': len(volume),
                             'format': 'csv'}]}
    write_json(work / 'catalog-source.json', manifest)
    result = {'module': 'agent_ee.catalog.capture_catalog', 'plane': 'AGENTEE-27',
              'source_rows': len(rows), 'catalog_rows': len(out_rows),
              'derivation': ('fome-mc BOM rows of Resistor_SMD/Capacitor_SMD with an LCSC number; lcsc, package '
                             '(from the footprint name), category and the Comment value copied; mfr, mpn, stock, '
                             'price1, is_basic, is_preferred and last_seen are not in the BOM and are left empty'),
              'source_manifest': rel(work / 'catalog-source.json', work.parent.parent),
              'volume_sha256': sha(volume)}
    try:
        capture_catalog(manifest, source, work / 'snapshot')
    except Exception as exc:  # noqa: BLE001 - the owned refusal is the result, verbatim
        result.update(status='refused', call='capture_catalog(catalog-source.json, source, snapshot)',
                      error=f'{type(exc).__name__}: {exc}')
    else:
        result.update(status='captured', snapshot=rel(work / 'snapshot', work.parent.parent))
    return result


# ------------------------------------------------------------------ scaffold

def write_scaffold(rundir, prep):
    """RUNDIR/scaffold/{project,requirements,generation} from owned records; returns its record."""
    from .board_envelope import derive, round_trip, stage_one_requirements
    from .connectivity_ir import resolve_intent
    rundir = Path(rundir)
    scaffold = rundir / 'scaffold'
    project, generation = scaffold / 'project', scaffold / 'generation'
    project.mkdir(parents=True)
    generation.mkdir()
    # Envelope (AGENTEE-85): validated, obligations derived; the scaffold outline comes from it.
    envelope = envelope_record(prep['params'])
    envelope_raw = canonical(envelope) + b'\n'
    try:
        artifacts = derive(envelope_raw, source_path='generation/envelope/envelope.json')
        dialog = prep['dialog_requirements'].read_bytes()
        artifacts['stage-one-requirements.json'] = stage_one_requirements(dialog,
                                                                          artifacts['dialog-requirements.json'])
    except ValueError as exc:
        raise PrepareFailure('envelope refused: ' + str(exc)) from exc
    (generation / 'envelope').mkdir()
    for name, data in sorted(artifacts.items()):
        (generation / 'envelope' / name).write_bytes(data)
    validation = json.loads(artifacts['envelope-validation.json'])
    outline = outline_rectangle(validation)
    root = root_sheet_uuid()
    template_pro = (g.FIXTURE_PROJECT / 'rc_filter.kicad_pro').read_bytes()
    pro = rc.project_bytes(template_pro, root)
    board, dropped = rc.empty_board((g.FIXTURE_PROJECT / 'rc_filter.kicad_pcb').read_text(),
                                    {'outline_mm': outline})
    (project / f'{NAME}.kicad_pro').write_bytes(pro)
    (project / f'{NAME}.kicad_sch').write_bytes(rc.empty_schematic(root))
    (project / f'{NAME}.kicad_pcb').write_bytes(board)
    for table in ('sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(g.FIXTURE_PROJECT / table, project / table)
    rc.lib_table_check(project)
    trip = round_trip(envelope_raw, board)
    # Requirements.
    shutil.copytree(prep['requirements'], scaffold / 'requirements')
    (scaffold / 'requirements' / 'review-conventions.md').write_text(rc.REVIEW_CONVENTIONS)
    (scaffold / 'requirements' / 'qualified.md').write_text(
        QUALIFIED_REQUIREMENTS + (CPL_REQUIREMENT_TEXT if prep.get('cpl_inputs') else ''))
    # Generation inputs (immutable in the scope).
    intent = qualified_intent(prep)
    footprints = footprint_manifest(prep['parts']['parts'])
    if 'approved_bundle' in prep['parts']:
        from .parts_manifest import load_manifest, qualify_manifest
        from .rung1_parts import MANIFEST, check_cad_inputs
        stock = {f'{lib}.kicad_sym': (g.STOCK / 'symbols' / f'{lib}.kicad_sym').read_bytes()
                 for lib in g.SYMBOL_LIBRARIES}
        bundle = prep['parts_path'].parent / 'approved'
        declared = load_manifest(bundle, MANIFEST)
        check_cad_inputs(qualify_manifest(declared, bundle), bundle, stock, footprints)
    resolved = resolve_intent(intent, intent_captures(intent))
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        raise PrepareFailure('qualified intent did not resolve: ' + json.dumps(resolved['findings'])[:2000])
    if prep.get('cpl_inputs'):
        shutil.copytree(prep['cpl_inputs'], generation / 'cpl')
    write_json(generation / 'intent.json', intent)
    write_json(generation / 'layout.json', layout())
    shutil.copyfile(prep['parts_path'], generation / 'parts-manifest.json')
    if 'approved_bundle' in prep['parts']:
        bundle = prep['parts_path'].parent / 'approved'
        for path in sorted(p for p in bundle.rglob('*') if p.is_file()):
            relative = path.relative_to(bundle)
            target = scaffold / relative
            if relative.parts[0] in {'project', 'requirements', 'generation', 'stock'} or target.exists():
                raise PrepareFailure('parts bundle collides with scaffold input: ' + str(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    write_json(generation / 'footprints.json', footprints)
    write_json(generation / 'placement-policy.json', packing_policy(prep['params'], outline))
    golden, golden_record = golden_netlist(rundir / 'golden-export')
    (generation / 'golden').mkdir()
    (generation / 'golden' / 'rc_control.xml').write_bytes(golden)
    files = {p.relative_to(scaffold).as_posix(): digest(p) for p in sorted(scaffold.rglob('*')) if p.is_file()}
    record = {'schema': SCAFFOLD_SCHEMA, 'qualification': QUALIFICATION, 'root_sheet_uuid': root,
              'layout_namespace': LAYOUT_NAMESPACE, 'outline_mm': outline,
              'envelope': {'path': 'generation/envelope/envelope.json', 'sha256': sha(envelope_raw),
                           'unresolved': validation.get('unresolved'),
                           'obligations': [r['id'] for r in json.loads(
                               artifacts['envelope-requirements.json'])['requirements']],
                           'scaffold_round_trip': trip,
                           'consumed_by_scope': False,
                           'why_not_consumed': ('capture refuses an envelope a scope consumes unless every '
                                                'obligation is bound; see probes.envelope_scope')},
              'resolved_intent_sha256': resolved['canonical_sha256'],
              'golden_netlist': golden_record,
              'template_project': {'path': str(g.FIXTURE_PROJECT), 'sha256': {
                  n: digest(g.FIXTURE_PROJECT / n) for n in ('rc_filter.kicad_pro', 'rc_filter.kicad_pcb')}},
              'board_items_dropped_from_template': dropped, 'files': files}
    write_json(rundir / 'scaffold.json', record)
    return scaffold, record


# ------------------------------------------------------------------ staged scope

def compile_staged_scope(params, scope_dir, scaffold):
    """scope/2 from the closed-loop control template plus the owned check kinds and stages/1."""
    from .fab_stage import CPL_ROOT, rotation_check
    template = json.loads(g.SCOPE_TEMPLATE.read_text())
    scope = g.compile_scope(template, params, NAME)
    base = Path(scope_dir)
    inputs = []
    for item in template['inputs']:
        if item['at'] == 'project':
            item = {'path': os.path.relpath(scaffold / 'project', base), 'at': 'project'}
        elif item['at'] == 'requirements':
            item = {'path': os.path.relpath(scaffold / 'requirements', base), 'at': 'requirements'}
        inputs.append(item)
    inputs.append({'path': os.path.relpath(scaffold / 'generation', base), 'at': 'generation'})
    scope['inputs'] = inputs
    scope['name'] = 'Rung-1 RC low-pass on the owned pipeline, staged repair campaign from an empty candidate'
    scope['requirements'] = list(scope.get('requirements', [])) + ['requirements/review-conventions.md',
                                                                   'requirements/qualified.md']
    scope['scope'] = ('Review the rung-1 RC low-pass candidate against the requirements compiled from its '
                      'one-paragraph spec. The campaign starts from an empty native project (no symbols, '
                      'footprints or copper). On each implementation turn the coordinator runs only the '
                      'declared steps of the earliest failing stage on the delivered design: the owned '
                      'schematic emitter and forward annotation (schematic), the placement adapter with the '
                      'pinned placer (placement) and the kicad-route adapter (routing). Fab readiness is graded '
                      'by the fab_export and fab_consistency checks. Only fresh checks and reviews of the '
                      'captured result decide acceptance. Immutable generation inputs are under generation/. '
                      'This is a disposable benchmark board.')
    by_id = {c['id']: c for c in scope['checks']}
    rc_step = copy.deepcopy(by_id['rc-step'])
    # The spec's GND, the global power net of the declared power:GND symbol.
    rc_step['params']['ground_net'] = ground_net(params)
    scope['checks'] = [
        {'id': 'erc', 'kind': 'erc', 'stage': 'schematic'},
        {'id': 'netlist', 'kind': 'netlist', 'stage': 'schematic'},
        {'id': 'parity', 'kind': 'netlist_parity', 'stage': 'schematic',
         'params': {'source': {'kind': 'intent', 'path': 'generation/intent.json'},
                    'netlist_check': 'netlist', 'predicate': 'topology', 'multiset': 'exactly'}},
        {'id': 'drc-placement', 'kind': 'drc', 'stage': 'placement', 'params': {'projection': 'placement-stage/1'}},
        {'id': 'courtyard', 'kind': 'placement', 'stage': 'placement',
         'params': {'criterion': 'courtyard-overlap/1', 'drc_check': 'drc-placement'}},
        {'id': 'outline', 'kind': 'placement', 'stage': 'placement',
         'params': {'criterion': 'outline-containment/1', 'drc_check': 'drc-placement'}},
        {'id': 'drc', 'kind': 'drc', 'stage': 'routing'},
        {**by_id['geometry'], 'stage': 'routing'},
        {**rc_step, 'stage': 'verification'},
        {'id': 'binding', 'kind': 'part_binding', 'stage': 'fab'},
        {'id': 'fab', 'kind': 'fab_export', 'stage': 'fab', 'params': {'netlist_check': 'netlist'}},
        {'id': 'fab-consistency', 'kind': 'fab_consistency', 'stage': 'fab', 'params': {'export_check': 'fab'}},
    ]
    source = {'path': 'requirements/qualified.md'}
    extra = [('R-parity', ['parity'], 'Schematic connectivity equals the captured rung-1 intent'),
             ('R-courtyard', ['courtyard'], 'Footprint courtyards do not overlap'),
             ('R-outline', ['outline'], 'Footprint courtyards lie inside the board outline'),
             ('R-binding', ['binding'], 'R1 and C1 carry a valid, consistent LCSC binding'),
             ('R-fab', ['fab'], 'A JLCPCB fab package is exported from the captured board'),
             ('R-fab-consistency', ['fab-consistency'], 'The fab package describes the captured board')]
    if (scaffold / CPL_ROOT).is_dir():
        # Decision C: the correction, and its captured inputs, are required only when declared.
        scope['checks'].append({**rotation_check(scaffold), 'stage': 'fab'})
        scope['scope'] += (' The CPL is corrected by cpl_rotation from the captured policy and supplier '
                           'footprints; physical preview remains required.')
        extra.append(('R-cpl', ['cpl'], 'The CPL carries policy- and supplier-frame-checked rotations; '
                                        'preview required'))
    requirements = scope['baseline']['requirements']
    for requirement in requirements:
        if requirement['id'] == 'R-native':
            requirement['checks'] = ['erc', 'netlist', 'drc-placement', 'drc']
    requirements += [{'id': rid, 'statement': text, 'checks': ids, 'source': {**source, 'locator': rid}}
                     for rid, ids, text in extra]
    refs = [params['resistor']['ref'], params['capacitor']['ref'], params['header']['ref']]
    scope['stages'] = {
        'schema': 'agent-ee.stages/1', 'order': list(STAGE_ORDER),
        'subjects': {
            'schematic': {'path': scope['schematic'], 'kind': 'symbols', 'references': refs},
            'placement': {'path': scope['board'], 'kind': 'footprints', 'references': refs},
            'routing': {'path': scope['board'], 'kind': 'tracks', 'minimum': 1}},
        'finding_retry_limit': 3}
    scope['mutable_inputs'] = [scope['schematic'], scope['board']]
    scope['limitations'] = list(scope.get('limitations', [])) + [
        'The board envelope (outline, layer count, stackup, fixed J1 on the left edge) is captured under '
        'generation/envelope but not consumed by any check: capture refuses an envelope scope until every '
        'obligation has an executable binding.']
    if (scaffold / 'approved-parts.json').is_file():
        add_parts_checks(scope, scaffold, base)
    else:
        scope['limitations'].append('Missing assembly_readiness and package_consistency coverage: '
                                   'no captured supplier parts/1 bundle was supplied.')
    return scope


def add_parts_checks(scope, scaffold, base):
    """Bind canonical parts, assembly and package coverage to the captured bundle."""
    from .parts_manifest import load_manifest, qualify_manifest
    manifest = load_manifest(scaffold, 'approved-parts.json')
    qualified = qualify_manifest(manifest, scaffold)
    for path in ['approved-parts.json', *qualified['dependencies']]:
        scope['inputs'].append({'path': os.path.relpath(scaffold / path, base), 'at': path})
    mapping = scaffold / 'rung1-package-map.json'
    if mapping.exists():
        raise PrepareFailure('parts bundle uses reserved package-map path')
    shutil.copyfile(REPO_ROOT / 'references/package-map/kicad-10.0.7-lcsc-1.json', mapping)
    scope['inputs'].append({'path': os.path.relpath(mapping, base), 'at': mapping.name})
    catalog = manifest['catalog']
    additions = [
        {'id': 'parts-membership', 'kind': 'parts_subset', 'stage': 'fab',
         'params': {'manifest': 'approved-parts.json', 'netlist_check': 'netlist',
                    'requirement_id': 'R-parts-membership'}},
        {'id': 'assembly', 'kind': 'assembly_readiness', 'stage': 'fab',
         'params': {'catalog': catalog['snapshot'] + '/manifest.json',
                    'catalog_sha256': catalog['manifest_sha256'],
                    'boards': qualified['selection']['build_quantity'],
                    'stock_floor': qualified['selection']['stock_floor']}},
        {'id': 'packages', 'kind': 'package_consistency', 'stage': 'fab',
         'params': {'netlist_check': 'netlist', 'catalog': catalog['snapshot'],
                    'catalog_manifest_sha256': catalog['manifest_sha256'], 'mapping': mapping.name,
                    'lcsc_field': 'LCSC', 'class_mismatch': 'defect'}},
    ]
    scope['checks'].extend(additions)
    declaration = manifest['declaration_source']
    for check in additions:
        rid = 'R-parts-membership' if check['id'] == 'parts-membership' else 'R-' + check['id']
        source = ({'path': declaration['path'], 'locator': declaration['locator']}
                  if check['id'] == 'parts-membership' else {'path': 'requirements/qualified.md', 'locator': rid})
        scope['baseline']['requirements'].append({'id': rid, 'statement': {
            'parts-membership': 'Physical parts match the captured catalog selection and CAD declaration',
            'assembly': 'Populated parts satisfy the assembly-readiness policy',
            'packages': 'Footprints agree with the catalog package under the captured package map',
        }[check['id']], 'checks': [check['id']], 'source': source})


def materialize_inputs(scope_path, inputs_dir):
    """Copy a scope's declared inputs into a new directory, as capture lays them out."""
    from .candidate import source_manifest
    scope = read_json(scope_path)
    files, sources, _ = source_manifest(scope, Path(scope_path).parent)
    Path(inputs_dir).mkdir(parents=True)
    for target, source in sources.items():
        destination = Path(inputs_dir) / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return scope, files


def validate_scope_file(scope_path, inputs_dir):
    """The real loaders, as capture runs them: inputs copied, then scope fields, stage declaration
    and baseline compilation with those inputs (parity and envelope capabilities read them)."""
    from .baseline import compile_baseline
    from .cli import validate_scope
    from .stages import declaration
    from .storage import identity
    scope_path, inputs_dir = Path(scope_path), Path(inputs_dir)
    scope, files = materialize_inputs(scope_path, inputs_dir)
    validate_scope(scope)
    stages = declaration(scope, files)
    baseline = compile_baseline(scope, files, inputs_dir)
    mutable = scope['mutable_inputs']
    if sorted(mutable) != sorted([scope['schematic'], scope['board']]):
        raise PrepareFailure('mutable_inputs must be exactly the schematic and board')
    immutable = [p for p in files if p.startswith(('generation/', 'requirements/'))]
    if set(mutable) & set(immutable) or set(mutable) & set(baseline['files']):
        raise PrepareFailure('mutable inputs overlap immutable generation or baseline sources')
    shutil.rmtree(inputs_dir)
    return {'files': len(files), 'baseline_id': baseline['baseline_id'], 'stage_policy_id': identity(stages),
            'mutable_inputs': mutable, 'immutable_generation_inputs': sorted(immutable),
            'baseline_files': sorted(baseline['files'])}


# ------------------------------------------------------------------ campaign configuration

def schematic_step_specs(*, source_root=PACKAGE_ROOT, steps_dir=STEPS_DIR):
    """The schematic turn: the owned emitter on the captured IR, then forward annotation."""
    source = {'source': str(Path(source_root)), 'at': MOUNT_SOURCE}
    steps = {'source': str(Path(steps_dir)), 'at': MOUNT_STEPS}
    board, sch = f'project/{NAME}.kicad_pcb', f'project/{NAME}.kicad_sch'
    return [
        {'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'emit-schematic',
         'argv': ['/bin/bash', f'{MOUNT_STEPS}/emit.sh'],
         'mounts': [steps, source], 'source': MOUNT_SOURCE,
         'version_argv': ['/usr/bin/python3', '--version'],
         'writes': {sch: f'out/sch/{NAME}.kicad_sch'},
         'artifacts': ['out/emit-receipt.json', f'out/sch/{NAME}.kicad_pro'],
         'adoptable_exits': [0], 'timeout_s': 600},
        {'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'forward-annotate',
         'argv': ['/bin/bash', f'{MOUNT_STEPS}/annotate.sh', NAME],
         'mounts': [steps, source], 'source': MOUNT_SOURCE,
         'version_argv': ['/usr/bin/kicad-cli', 'version'],
         'writes': {board: f'out/board/{NAME}.kicad_pcb'},
         'artifacts': ['out/netlist.xml', 'out/plan.json', 'out/apply.json'],
         'adoptable_exits': [0], 'timeout_s': 1800},
    ]


def step_specs(kicad_route, *, source_root=PACKAGE_ROOT, steps_dir=STEPS_DIR, fab=False):
    """Coordinator steps: the schematic turn, the routing adapter and, with a declared cpl_rotation
    check (`fab`), the receipted fab export and CPL correction."""
    from .routing_adapter import step as routing_step
    from .fab_stage import step as fab_step
    steps = schematic_step_specs(source_root=source_root, steps_dir=steps_dir) + [
        routing_step(project='project', board=f'{NAME}.kicad_pcb', schematic=f'{NAME}.kicad_sch', name=NAME,
                     label='krt', checkout=kicad_route, budget=300)]
    if fab:
        steps.append(fab_step(source_root, f'project/{NAME}.kicad_pcb', f'project/{NAME}.kicad_sch'))
    return steps


def placement_declaration():
    return {'schema': 'agent-ee.placement-adapter/1', 'helper': 'agent-ee.placer/1',
            'policy': 'generation/placement-policy.json', 'fixed_footprints': [], 'timeout_s': 1800}


def check_steps(steps, mutable):
    from .stage_receipts import validate_step
    normalized = []
    for spec in steps:
        value = validate_step(spec)
        if value['stage'] == 'placement':
            raise ValueError('placement runs through the placement adapter, not an implementation step')
        if 0 not in value['adoptable_exits'] or any(e in (1, 3, 4) for e in value['adoptable_exits']):
            raise ValueError(f"step {value['step']}: exits 1, 3 and 4 are never adoptable")
        outside = sorted(set(value['writes']) - set(mutable))
        if outside:
            raise ValueError(f"step {value['step']} writes outside mutable_inputs: {outside}")
        normalized.append(value)
    return normalized


def campaign_config(scope_path, profile, steps, *, max_seconds, max_iterations):
    adjudicator = profile['adjudicator']
    config = {'schema': 'agent-ee.campaign/1', 'scope': Path(scope_path).name, 'review_profile': profile,
              'implementation': {'harness': adjudicator['harness'], 'model': adjudicator['model'],
                                 'effort': adjudicator['effort'], 'timeout_s': adjudicator['timeout_s']},
              'max_iterations': max_iterations, 'max_seconds': max_seconds, 'implementation_steps': steps,
              'placement_adapter': placement_declaration()}
    from .placement_adapter import validate_declaration
    validate_declaration(config['placement_adapter'])
    return config


# ------------------------------------------------------------------ registration probes (no inference)

def probe_scope(rundir, base_scope, label, mutate):
    """Validate a variant scope with the real loaders; record the exact refusal or success."""
    scope = mutate(copy.deepcopy(base_scope))
    path = Path(rundir) / 'probes' / f'{label}.scope.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Inputs are relative to RUNDIR; the probe scope sits one level down.
    scope['inputs'] = [{**i, 'path': i['path'] if os.path.isabs(i['path']) else os.path.join('..', i['path'])}
                       for i in scope['inputs']]
    write_json(path, scope)
    try:
        result = validate_scope_file(path, path.parent / (label + '-inputs'))
    except Exception as exc:  # noqa: BLE001 - the owned refusal is the finding
        return {'scope': rel(path, rundir), 'status': 'refused', 'error': f'{type(exc).__name__}: {exc}'}
    return {'scope': rel(path, rundir), 'status': 'accepted', 'baseline_id': result['baseline_id']}


def registration_probes(rundir, scope):
    """Would the owned envelope bindings capture? Two scope/3 variants through the real loaders."""
    envelope = 'generation/envelope/envelope.json'
    derived = json.loads((Path(rundir) / 'scaffold/generation/envelope/envelope-requirements.json').read_text())

    def scope3(s, bind):
        s['schema'] = 'agent-ee.scope/3'
        s['envelope'] = {'path': envelope, 'profile': None, 'dialog': None}
        for requirement in s['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        position = next(i for i, c in enumerate(s['checks']) if c['id'] == 'drc')
        added = []
        for item in derived['requirements']:
            field = item['source']['locator'].split('/')[1]
            check = bind.get(field)
            entry = {'id': item['id'], 'statement': item['statement'], 'source': item['source'],
                     'intent': 'acceptance', 'checks': []}
            if check is not None:
                cid = 'envelope-' + field.replace('_', '-')
                added.append({'id': cid, 'stage': 'placement', **check})
                entry['checks'] = [cid]
            s['baseline']['requirements'].append(entry)
        s['checks'][position:position] = added
        return s

    connector = {'kind': 'placement', 'params': {'criterion': 'connector-edge/1', 'drc_check': 'drc-placement',
                                                 'envelope': envelope}}
    native = {field: {'kind': 'board_envelope', 'params': {'criterion': criterion, 'envelope': envelope}}
              for field, criterion in (('outline', 'outline/1'), ('layer_count', 'layers/1'),
                                       ('stackup', 'stackup/1'))}
    return {'schema': PROBE_SCHEMA, 'qualification': QUALIFICATION,
            'note': ('scope/3 variants of the run scope that declare and consume the captured envelope; '
                     'validated with the real loaders, never run'),
            'envelope_connector_only': probe_scope(
                rundir, scope, 'envelope-connector-only', lambda s: scope3(s, {'fixed_footprints': connector})),
            'envelope_all_bindings': probe_scope(
                rundir, scope, 'envelope-all-bindings', lambda s: scope3(s, {'fixed_footprints': connector, **native}))}


# ------------------------------------------------------------------ preparation

def prepare(spec, rundir, catalog_checkout=None, parts_manifest=None, cpl_inputs=None):
    prep = rc.prepare(spec, rundir, catalog_checkout, parts_manifest, cpl_inputs)
    dialog = Path(rundir) / 'prep' / 'stages' / '1-requirements' / 'dialog' / 'requirements.json'
    if not dialog.is_file():
        found = sorted((Path(rundir) / 'prep').rglob('dialog/requirements.json'))
        if len(found) != 1:
            raise PrepareFailure('stage-1 dialog requirements.json not found')
        dialog = found[0]
    prep['dialog_requirements'] = dialog
    return prep


def build(spec, rundir, *, profile_path, kicad_route, max_seconds, max_iterations, parts_manifest=None,
          cpl_inputs=None):
    rundir = Path(rundir).absolute()
    prep = prepare(spec, rundir, parts_manifest=parts_manifest, cpl_inputs=cpl_inputs)
    catalog = ({'status': 'captured', 'catalog': prep['parts']['catalog']}
               if 'approved_bundle' in prep['parts'] else catalog_attempt(prep, rundir / 'parts-catalog'))
    scaffold, scaffold_record = write_scaffold(rundir, prep)
    loads = rc.scaffold_load_checks(rundir, scaffold)
    if not all(entry['loaded'] for entry in loads.values()):
        raise PrepareFailure('kicad-cli did not load the empty scaffold: ' + json.dumps(loads))
    scope = compile_staged_scope(prep['params'], rundir, scaffold)
    scope_path = rundir / 'scope.json'
    write_json(scope_path, scope)
    validation = validate_scope_file(scope_path, rundir / 'scope-validation-inputs')
    probes = registration_probes(rundir, scope)
    write_json(rundir / 'probes.json', probes)
    profile = read_json(profile_path)
    steps = check_steps(step_specs(kicad_route, fab=any(c['kind'] == 'cpl_rotation' for c in scope['checks'])),
                        scope['mutable_inputs'])
    config = campaign_config(scope_path, profile, steps, max_seconds=max_seconds, max_iterations=max_iterations)
    from .profiles import validate_profile
    validate_profile(config['review_profile'])
    validate_profile({**config['review_profile'], 'adjudicator': config['implementation']})
    config_path = rundir / 'campaign.json'
    write_json(config_path, config)
    from .routing_adapter import preflight
    base = {'spec': {'path': str(Path(spec).absolute()), 'sha256': digest(spec)},
            'generator': g.tool_identity('agent_ee'),
            'base_commit': git_head(REPO_ROOT),
            'owned_modules': {k: {'module': v[0], 'plane': v[1]} for k, v in OWNED.items()},
            'kicad_route': {**rc.route_identity(kicad_route), 'preflight': preflight(kicad_route),
                            'note': ('clone of ~/git/kicad-route checked out at the adapter pin, vendor/ and '
                                     '.venv-krt copied from build/kicad-route-snapshot; mounted read-only at '
                                     '/opt/kicad-route as checkout and state by routing_adapter.step')},
            'prep_receipts': prep['prep_receipts'],
            'parts': {'manifest': 'scaffold/generation/parts-manifest.json',
                      'selection': ('canonical parts/1 replay' if 'approved_bundle' in prep['parts']
                                    else 'agent_ee.generate stage 2 (experimental; owned catalog capture refused)'),
                      'catalog_capture': catalog,
                      'missing_coverage': ([] if 'approved_bundle' in prep['parts'] else
                                           ['assembly_readiness', 'package_consistency', 'parts_subset'])},
            'scaffold': {'path': 'scaffold.json', 'sha256': digest(rundir / 'scaffold.json'),
                         'root_sheet_uuid': scaffold_record['root_sheet_uuid'], 'load_checks': loads,
                         'envelope': scaffold_record['envelope']},
            'scope': {'path': 'scope.json', 'sha256': digest(scope_path), **validation,
                      'checks': [{'id': c['id'], 'kind': c['kind'], 'stage': c['stage']} for c in scope['checks']]},
            'probes': probes,
            'campaign_config': {'path': 'campaign.json', 'sha256': digest(config_path),
                                'profile': str(Path(profile_path).absolute()), 'profile_sha256': digest(profile_path),
                                'max_iterations': max_iterations, 'max_seconds': max_seconds,
                                'steps': [{'step': s['step'], 'stage': s['stage'],
                                           'adoptable_exits': s['adoptable_exits']} for s in steps],
                                'placement_adapter': config['placement_adapter']}}
    return base, scope_path, config_path


def git_head(path):
    import subprocess
    proc = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=60)
    status = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'],
                            capture_output=True, text=True, timeout=60)
    return {'commit': proc.stdout.strip() or None, 'tracked_changes': status.stdout.strip().splitlines()}


# ------------------------------------------------------------------ dry run: checks-only reviews and rehearsal

def review_checks_only(rundir, scope_path, label):
    out = Path(rundir) / 'dry-run' / label
    command = rc.run_logged(rc.agent_ee_command('review', scope_path, '--out', out, '--checks-only'),
                            Path(rundir) / 'logs', f'dry-run-{label}', 3600)
    result = rc.summarize_checks(out) if (out / 'result.json').is_file() else None
    return out, {'command': {k: command[k] for k in ('argv', 'exit_code', 'stderr_tail')}, 'result': result}


def rehearsal_scope(base_scope_path, design, path):
    scope = read_json(base_scope_path)
    scope['inputs'] = [{'path': str(p), 'at': p.name} for p in sorted(Path(design).iterdir())]
    write_json(path, scope)
    return path


def run_steps(base, design, specs, start):
    from .stage_receipts import adopt, run_step
    receipts = []
    for k, spec in enumerate(specs, start):
        receipt_dir = base / 'receipts' / f"{k}-{spec['step']}"
        receipt = run_step(receipt_dir, design, spec, {'implementation': 0, 'rehearsal': True, 'receipt_index': k})
        summary = rc.receipt_summary(receipt_dir)
        receipts.append(summary)
        if receipt['status'] not in ('completed', 'partial'):
            summary['stopped'] = f"status {receipt['status']} (exit {receipt['exit_code']}); not adopted"
            summary['stderr_tail'] = tail(receipt_dir / 'run' / 'stderr.log')
            summary['console_tail'] = tail(receipt_dir / 'run' / 'console.log')
            return receipts, False
        if spec['stage'] == 'routing':
            from .routing_adapter import verify
            try:
                summary['routing_verify'] = verify(receipt_dir / 'run', receipt=receipt)
            except ValueError as exc:
                summary['routing_verify'] = {'error': str(exc)}
                return receipts, False
        try:
            adopt(receipt_dir, receipt, design)
        except ValueError as exc:
            summary['adoption_error'] = str(exc)
            return receipts, False
    return receipts, True


def tail(path, size=3000):
    try:
        return Path(path).read_text(errors='replace')[-size:]
    except OSError:
        return None


def rehearse_placement(base, design, review_run, scope):
    """The placement adapter's helper step, exactly as the campaign declares it, on the rehearsal design."""
    from .placement_adapter import binding, prepare_step, qualify_receipt
    from .stage_receipts import adopt, run_step
    manifest = read_json(Path(review_run) / 'candidate.json')
    record, bound, policy, protected, refs = binding(placement_declaration(), manifest, Path(review_run) / 'inputs',
                                                     scope['mutable_inputs'])
    step_dir = base / 'placement' / '1'
    spec, request = prepare_step(step_dir, (design / bound['board']).read_bytes(), policy, bound, refs)
    receipt_dir = base / 'receipts' / '3-agent-ee-placer'
    receipt = run_step(receipt_dir, design, spec, {'implementation': 0, 'rehearsal': True, 'receipt_index': 3})
    summary = rc.receipt_summary(receipt_dir)
    summary['binding'] = record
    try:
        summary['qualified'] = qualify_receipt(receipt_dir, receipt, bound['board'], protected)
        adopt(receipt_dir, receipt, design)
    except ValueError as exc:
        summary['refused'] = str(exc)
        summary['result'] = tail(receipt_dir / 'run' / 'placement' / 'placed' / 'placement-result.json')
        summary['stderr_tail'] = tail(receipt_dir / 'run' / 'stderr.log')
        return summary, False
    return summary, True


def rehearse_schematic(rundir, scope_path, design, steps):
    """One schematic turn outside any campaign: the declared schematic steps on ``design`` as the
    coordinator runs them, a checks-only review of the result and the reference comparison."""
    design = Path(design)
    base = design.parent
    receipts, ok = run_steps(base, design, steps, 1)
    stages = {'schematic_steps': receipts}
    if not ok:
        return None, stages
    run, stages['after_schematic'] = review_checks_only(
        rundir, rehearsal_scope(scope_path, design, base / 'after-schematic.scope.json'), 'after-schematic')
    if stages['after_schematic']['result'] is None:
        return None, stages
    stages['reference_after_schematic'] = reference_parity(run / 'work' / 'netlist' / 'design.xml', design / GOLDEN)
    return run, stages


def dry_run(rundir, scope_path, config_path):
    rundir = Path(rundir)
    scope = read_json(scope_path)
    config = read_json(config_path)
    empty_dir, empty = review_checks_only(rundir, scope_path, 'empty-checks-only')
    stages = {'empty_review': empty}
    if empty['result'] is None:
        return stages
    base = rundir / 'dry-run' / 'rehearsal'
    design = base / 'design'
    shutil.copytree(empty_dir / 'inputs', design)
    steps = config['implementation_steps']
    schematic_steps = [s for s in steps if s['stage'] == 'schematic']
    routing_steps = [s for s in steps if s['stage'] == 'routing']
    run1, turn = rehearse_schematic(rundir, scope_path, design, schematic_steps)
    stages.update(turn)
    if run1 is None:
        return stages
    summary, ok = rehearse_placement(base, design, run1, scope)
    stages['placement_step'] = summary
    if not ok:
        return stages
    _, stages['after_placement'] = review_checks_only(
        rundir, rehearsal_scope(scope_path, design, base / 'after-placement.scope.json'), 'after-placement')
    receipts, ok = run_steps(base, design, routing_steps, 4)
    stages['routing_step'] = receipts
    if not ok:
        return stages
    _, stages['after_routing'] = review_checks_only(
        rundir, rehearsal_scope(scope_path, design, base / 'after-routing.scope.json'), 'after-routing')
    from .board_envelope import round_trip
    stages['envelope_round_trip_after_routing'] = round_trip(
        (rundir / 'scaffold/generation/envelope/envelope.json').read_bytes(),
        (design / f'project/{NAME}.kicad_pcb').read_bytes())
    stages['note'] = 'rehearsal outside any campaign; not acceptance evidence'
    return stages


# ------------------------------------------------------------------ records

def fab_outcome(run):
    """The fab outcome of one review run.

    Without a declared cpl_rotation check: the fab_export check's manifest, verified with the owned
    verify_manifest, and rotation status `not_declared`. With one: the sealed CPL report, and a
    corrected package derived only from complete passing coverage.
    """
    run = Path(run)
    scope = read_json(run / 'candidate.json')['scope']
    if not any(c.get('kind') == 'cpl_rotation' for c in scope['checks']):
        return native_fab_outcome(run)
    from .fab_stage import package
    report_path = run / 'work/cpl/cpl-rotation.json'
    if not report_path.is_file():
        return {'manifest': None, 'rotation_status': 'unknown', 'error': 'CPL rotation evidence is missing'}
    report = read_json(report_path)
    entry = {'manifest': None, 'rotation_status': report['status'],
             'physical_validation': report['physical_validation'],
             'execution': report['execution'], 'outcome': report['outcome']}
    if (report['execution'], report['outcome'], report['coverage']) != ('ok', 'pass', 'complete'):
        return entry
    out = run.parent.parent.parent / 'fab-packages' / run.name
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'fab-manifest.json'
    if not manifest_path.is_file():
        package(run, out / 'fab', manifest_path)
    data = read_json(manifest_path)
    files = [{'path': p.relative_to(out / 'fab').as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted((out / 'fab').rglob('*')) if p.is_file()]
    if (data['files'] != files or data['rotation']['report_sha256'] != digest(report_path)
            or data['status'] != report['status']
            or data['physical_validation'] is not report['physical_validation']):
        raise ValueError('derived fab package differs from its checked rotation evidence')
    entry.update(manifest=str(manifest_path), sha256=digest(manifest_path), files=len(data['files']),
                 board_sha256=data['board']['sha256'], rotation=data['rotation'])
    return entry


def native_fab_outcome(run):
    """The fab_export check's manifest in one review run, verified with the owned verify_manifest."""
    manifest = run / 'work' / 'fab' / 'fab' / 'manifest.json'
    if not manifest.is_file():
        incomplete = run / 'work' / 'fab' / 'fab.incomplete'
        return {'manifest': None, 'rotation_status': 'not_declared',
                'incomplete_staging': incomplete.is_dir() and sorted(
                    p.relative_to(incomplete).as_posix() for p in incomplete.rglob('*') if p.is_file())}
    data = read_json(manifest)
    entry = {'manifest': str(manifest), 'sha256': digest(manifest), 'files': len(data.get('files', {})),
             'board_sha256': data.get('board_sha256'), 'designators': data.get('designators'),
             'rotation_status': 'not_declared'}
    try:
        from .fab_export import verify_manifest
        verify_manifest(manifest.parent)
        entry['verify_manifest'] = 'ok'
    except Exception as exc:  # noqa: BLE001 - recorded as observed
        entry['verify_manifest'] = f'{type(exc).__name__}: {exc}'
    return entry


def campaign_record(rundir, base, command):
    rundir = Path(rundir)
    root = rundir / 'campaign'
    verify = rc.run_logged(rc.agent_ee_command('verify-campaign', root), rundir / 'logs', 'verify-campaign', 3600)
    state, iterations, implementations = rc.campaign_summary(root)
    for entry in iterations:
        entry['fab'] = fab_outcome(entry['run']) if entry.get('checks') is not None else None
        netlist = Path(entry['run']) / 'work' / 'netlist' / 'design.xml'
        entry['reference_parity'] = (reference_parity(netlist, rundir / 'scaffold' / GOLDEN)
                                     if netlist.is_file() else None)
    for implementation in implementations:
        record = root / 'implementations' / implementation['implementation'] / 'placement-adapter.json'
        implementation['placement_adapter'] = read_json(record) if record.is_file() else None
        implementation.pop('fab', None)
    verdict = state.get('state') if state else None
    base.update(mode='campaign', campaign={
        'root': str(root), 'command': {k: command[k] for k in ('argv', 'exit_code', 'stdout_tail', 'stderr_tail')},
        'state': verdict, 'error': state.get('error') if state else None,
        'diagnostic': state.get('diagnostic') if state else None,
        'iterations': iterations, 'implementations': implementations},
        verify_campaign={'exit_code': verify['exit_code'], 'stdout_tail': verify['stdout_tail'],
                         'stderr_tail': verify['stderr_tail']})
    final_fab = iterations[-1]['fab'] if iterations else None
    return write_record(rundir, base, verdict=verdict, verify_exit=verify['exit_code'], fab=final_fab)


def write_record(rundir, base, *, verdict=None, verify_exit=None, fab=None):
    from .fab_package import rotation_blocker
    blocker = None
    if verdict == 'criteria_satisfied' and verify_exit == 0:
        status = (fab or {}).get('rotation_status')
        blocker = rotation_blocker(status)
        if blocker is None and status != 'not_declared' and fab.get('manifest') is None:
            blocker = 'fab: the corrected fab package was not derived'
    done = verdict == 'criteria_satisfied' and verify_exit == 0 and blocker is None
    record = {'schema': RECORD_SCHEMA, 'qualification': QUALIFICATION, **base, 'verdict': verdict,
              'fab_manifest': fab, 'fab_rotation_blocker': blocker, 'done': done,
              'done_rule': ('campaign verdict criteria_satisfied and verify-campaign exit 0; the fab package is '
                            'graded inside the check set by fab_export and fab_consistency, and by cpl_rotation '
                            'when declared; the rotation status must be in fab_package.DONE_ROTATION_STATUSES')}
    write_json(Path(rundir) / 'rung1-qualified-record.json', record)
    return record


def run(spec, rundir, *, profile_path, kicad_route, dry_run_only, max_seconds, max_iterations, parts_manifest=None,
        cpl_inputs=None):
    rundir = Path(rundir).absolute()
    if rundir.exists():
        raise FileExistsError(f'{rundir} exists; choose a fresh run directory')
    rundir.mkdir(parents=True)
    try:
        base, scope_path, config_path = build(spec, rundir, profile_path=profile_path, kicad_route=kicad_route,
                                              max_seconds=max_seconds, max_iterations=max_iterations,
                                              parts_manifest=parts_manifest, cpl_inputs=cpl_inputs)
    except (PrepareFailure, rc.PrepareFailure, ValueError, OSError) as exc:
        return write_record(rundir, {'mode': 'dry_run' if dry_run_only else 'campaign',
                                     'error': f'{type(exc).__name__}: {exc}'})
    if dry_run_only:
        base.update(mode='dry_run', dry_run=dry_run(rundir, scope_path, config_path))
        return write_record(rundir, base)
    root = rundir / 'campaign'
    command = rc.run_logged(rc.agent_ee_command('campaign', config_path, '--out', root), rundir / 'logs',
                            'campaign', max_seconds + 3600)
    return campaign_record(rundir, base, command)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--profile', type=Path, default=REPO_ROOT / 'profiles' / 'claude-baseline.json')
    parser.add_argument('--kicad-route', type=Path, default=DEFAULT_KICAD_ROUTE,
                        help='kicad-route checkout at the adapter pin 08179cd with vendor/krt and .venv-krt '
                             '(default: $AGENT_EE_KICAD_ROUTE_CHECKOUT, else /opt/kicad-route)')
    parser.add_argument('--cpl-inputs', type=Path, help='explicit CPL policy and supplier capture tree')
    parser.add_argument('--dry-run', action='store_true',
                        help='scaffold, scope, config and registration probes; checks-only reviews and a step '
                             'rehearsal; no inference')
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--max-iterations', type=int, default=4)
    parser.add_argument('--parts-manifest', type=Path,
                        help='captured parts/1 manifest at the root of its dependency bundle; requires assembled J1')
    args = parser.parse_args(argv)
    try:
        record = run(args.spec, args.out, profile_path=args.profile, kicad_route=args.kicad_route.absolute(),
                     dry_run_only=args.dry_run, max_seconds=args.max_seconds, max_iterations=args.max_iterations,
                     parts_manifest=args.parts_manifest, cpl_inputs=args.cpl_inputs)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f'rung1_qualified refused: {exc}\n')
    print(json.dumps({k: record.get(k) for k in ('mode', 'error', 'verdict', 'done')}))
    return 0 if record['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
