"""Fab-package consistency gate over a sealed `fab_export` staging tree (AGENTEE-73).

`fab_consistency` (params `{export_check}`) never regenerates or runs exports. It
reads the earlier export check's `work/EXPORT/fab/` staging tree and its
`agent-ee.fab-manifest/1`, the export's sealed native XML netlist prerequisite,
every earlier sealed `geometry` probe of the captured board, and the captured
board bytes (S-expression parse only). The manifest is a claim to verify, never
authority: its kicad-cli identity must be the pinned binary and its sealed
version run, and its designators must match the native board inventory, which
comes from the sealed pcbnew geometry and is cross-checked against the board
parse and the sealed netlist. Structural package faults and disagreeing sealed
sources are malformed; established cross-file disagreements are defects; inputs
this gate cannot see (uncaptured 3D-model libraries, unattributed plots, an
unrecorded tool version) are unknown coverage, never a pass.
"""

import csv
import io
import json
import math
import os
import re
import shutil
import stat
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal, DecimalException
from pathlib import PurePosixPath

from . import kicad_embedded
from .executor import SANDBOX_PATH
from .storage import digest, read_json, write_json

MANIFEST_SCHEMA = 'agent-ee.fab-manifest/1'
MANIFEST_KEYS = frozenset(('schema', 'candidate_id', 'board_path', 'board_sha256', 'tool', 'files',
                           'copper_layers', 'enabled_fab_layers', 'drill', 'designators',
                           'generated_utc'))
REQUIRED_FILES = ('bom.csv', 'cpl.csv', 'gerbers.zip', 'pcb_summary.json')
BOM_HEADER = ['Designator', 'Footprint', 'Quantity', 'Value', 'Manufacturer', 'Partnumber',
              'LCSC Part #']
CPL_HEADER = ['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation']
NON_COPPER_FAB = frozenset(('F.Mask', 'B.Mask', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS',
                            'Edge.Cuts'))
REQUIRED_NON_COPPER = frozenset(('F.Mask', 'B.Mask', 'Edge.Cuts'))
SIDE_FUNCTIONS = {'Soldermask': 'Mask', 'Paste': 'Paste', 'Legend': 'SilkS'}
FILE_FUNCTION = re.compile(r'%TF\.FileFunction,([^*%]*)\*%|G04 #@! TF\.FileFunction,([^*]*)\*')
DRILL_FUNCTION = re.compile(r'(?m)^; #@! TF\.FileFunction,([^\r\n]*)$')
SHA256 = re.compile(r'[0-9a-f]{64}')
DESIGNATOR = re.compile(r'[A-Za-z_][A-Za-z0-9_.]*')
RANGE = re.compile(r'([A-Za-z_]+)(\d+)\s*[-–:]\s*(?:[A-Za-z_]+)?(\d+)')
LCSC = re.compile(r'C\d+')
NUMBER = re.compile(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')
VARIABLE = re.compile(r'\$\{([^}]*)\}|\$\(([^)]*)\)')
EMBED = 'kicad-embed://'
MAX_FILES = 4096
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_CITATIONS = 50
COORDINATE_TOLERANCE = Decimal('0.01')
LOG_DIAGNOSTIC = re.compile(r'(?:^\s*(?:warning|error|fatal)\b|\b(?:warning|error|fatal)\s*:)', re.I)


GEOMETRY_FLAGS = ('dnp', 'exclude_from_bom', 'exclude_from_pos_files', 'board_only')
PREDICATES = ['fab-package:inventory', 'fab-package:designator-parity', 'fab-package:lcsc-footprint',
              'fab-package:cpl-side', 'fab-package:cpl-coordinates', 'fab-package:3d-models',
              'fab-package:build-log']


def capability(params, scope, prerequisite, captured):
    """Static scope/3 contract: exact params, earlier export, its earlier netlist and an earlier geometry."""
    if not isinstance(params, dict) or set(params) != {'export_check'}:
        raise ValueError('fab_consistency params must be exactly {export_check}')
    export = prerequisite(params['export_check'], ('fab_export',))
    export_params = export.get('params', {})
    prerequisite(export_params.get('netlist_check') if isinstance(export_params, dict) else None,
                 ('netlist',))
    earlier = []
    for spec in scope.get('checks', []):
        if spec.get('kind') == 'geometry':
            try:
                earlier.append(prerequisite(spec.get('id'), ('geometry',)))
            except ValueError:
                pass  # a later geometry check cannot supply this gate's evidence
    if not earlier:
        raise ValueError('fab_consistency needs an earlier geometry check for native footprint evidence')
    captured(scope.get('board'))
    return list(PREDICATES)


class Malformed(ValueError):
    """Package structure or required evidence is invalid."""


def fail(code, message):
    raise Malformed(f'{code}: {message}')


# ------------------------------------------------------------------ prerequisites

def export_prerequisite(ctx):
    """The earlier same-candidate `fab_export` result and its staging directory."""
    from .checks import CheckError, SAFE_ID
    eid = ctx.params.get('export_check')
    if set(ctx.params) != {'export_check'} or not isinstance(eid, str) or not SAFE_ID.fullmatch(eid):
        raise CheckError('unsupported', 'fab_consistency params must be exactly {export_check: <safe id>}')
    specs = ctx.scope['checks']
    position = next(i for i, s in enumerate(specs) if s is ctx.spec or s.get('id') == ctx.cid)
    spec = next((s for s in specs[:position] if s.get('id') == eid), None)
    path = ctx.run_dir / 'checks' / f'{eid}.json'
    if spec is None or spec.get('kind') != 'fab_export' or not path.is_file():
        raise CheckError('unsupported', 'fab_export prerequisite missing, later in scope order or not yet run')
    result = read_json(path)
    if result.get('candidate_id') != ctx.manifest['candidate_id'] or result.get('execution') != 'ok':
        raise CheckError('unsupported', f'fab_export prerequisite {eid!r} unverified '
                         f'(execution {result.get("execution")!r})')
    stage = ctx.run_dir / 'work' / eid / 'fab'
    if stage.is_symlink() or not stage.is_dir() or not any(stage.iterdir()):
        raise CheckError('malformed', f'fab_export {eid!r} reported ok but its staging directory is absent or empty')
    rel = f'work/{eid}/fab/manifest.json'
    if rel not in result.get('evidence', []):
        raise CheckError('unsupported', f'fab_export prerequisite {eid!r} did not seal fab/manifest.json')
    ctx.result['evidence'].append(rel)
    return eid, spec, result, stage


def netlist_components(ctx, export_spec):
    """Sealed native XML netlist flags named by the export's own prerequisite.

    The XML must agree with the netlist check's sealed strict reading
    (`connectivity.json`), and every reference must be unique.
    """
    from .checks import CheckError, parse_xml_netlist
    from .datasheet_facts import prerequisite
    nid = (export_spec.get('params') or {}).get('netlist_check')
    xml_path = prerequisite(ctx, nid, 'netlist', 'design.xml')
    connectivity = prerequisite(ctx, nid, 'netlist', 'connectivity.json')
    try:
        root = ET.parse(xml_path).getroot()
        agrees = parse_xml_netlist(xml_path) == read_json(connectivity)
    except (ET.ParseError, OSError, ValueError) as exc:
        raise CheckError('malformed', f'sealed netlist does not parse: {exc}') from exc
    if not agrees:
        raise CheckError('malformed', 'sealed native XML netlist and its connectivity record disagree')
    components = {}
    for comp in root.iter('comp'):
        ref = comp.get('ref')
        if not ref:
            raise CheckError('malformed', 'sealed netlist has a component without a reference')
        if ref in components:
            raise CheckError('malformed', f'sealed netlist has duplicate reference {ref}')
        names = {p.get('name') for p in comp.findall('property')}
        components[ref] = {flag: flag in names for flag in
                           ('dnp', 'exclude_from_bom', 'exclude_from_pos_files', 'exclude_from_board')}
    components = {ref: flags for ref, flags in components.items() if not ref.startswith('#')}
    if not components:
        raise CheckError('malformed', 'sealed netlist has no physical components')
    return nid, components


def geometry_footprints(ctx):
    """Native footprint inventory from every earlier sealed `geometry` check of the captured board."""
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    specs = ctx.scope['checks']
    position = next(i for i, s in enumerate(specs) if s is ctx.spec or s.get('id') == ctx.cid)
    ids = [s['id'] for s in specs[:position] if s.get('kind') == 'geometry']
    if not ids:
        raise CheckError('unsupported', 'no earlier sealed geometry check supplies native footprint '
                                        'evidence for the captured board')
    board_sha = ctx.manifest['files'].get(ctx.scope.get('board'))
    inventories = []
    for gid in ids:
        path = prerequisite(ctx, gid, 'geometry', 'geometry.json')
        if read_json(ctx.run_dir / 'checks' / f'{gid}.json').get('details', {}).get('board_sha256') != board_sha:
            raise CheckError('unsupported', f'geometry check {gid!r} is not bound to the captured board digest')
        try:
            footprints = read_json(path)['footprints']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CheckError('malformed', f'sealed geometry {gid!r} is unreadable: {exc}') from exc
        if not isinstance(footprints, list) or not all(isinstance(fp, dict) for fp in footprints):
            raise CheckError('malformed', f'sealed geometry {gid!r} footprints are not records')
        inventory = []
        for fp in footprints:
            if any(not isinstance(fp.get(flag), bool) for flag in GEOMETRY_FLAGS):
                raise CheckError('unsupported', f'sealed geometry {gid!r} lacks footprint exclusion attributes; '
                                                'rerun the geometry probe')
            side = {'F.Cu': 'top', 'B.Cu': 'bottom'}.get(fp.get('layer'))
            if not isinstance(fp.get('ref'), str) or side is None:
                raise CheckError('malformed', f'sealed geometry {gid!r} footprint lacks a reference or side')
            position = fp.get('position_nm')
            if (not isinstance(position, list) or len(position) != 2 or
                    any(type(n) is not int for n in position)):
                raise CheckError('unsupported', f'sealed geometry {gid!r} lacks exact native footprint '
                                                'positions; rerun the geometry probe')
            inventory.append((fp['ref'], side, *(fp[flag] for flag in GEOMETRY_FLAGS), tuple(position)))
        inventories.append(sorted(inventory))
    if any(inventory != inventories[0] for inventory in inventories[1:]):
        raise CheckError('malformed', 'earlier geometry checks disagree on the board footprint inventory')
    return ids, inventories[0]


def pinned_kicad_cli():
    """(path, sha256) of the kicad-cli the executor sandbox resolves, or None."""
    path = shutil.which('kicad-cli', path=SANDBOX_PATH)
    return (path, digest(path)) if path else None


def sealed_versions(ctx, eid, export_result):
    """{record: stdout version} for the export's sealed `kicad-cli version` executions."""
    sealed = {e for e in export_result.get('evidence', []) if isinstance(e, str)}
    found = {}
    for rel in sorted(sealed):
        if not rel.startswith(f'work/{eid}/') or not rel.endswith('/execution.json'):
            continue
        try:
            record = read_json(ctx.run_dir / rel)
        except (OSError, ValueError) as exc:
            fail('tool', f'sealed execution record {rel} is unreadable ({exc})')
        if not isinstance(record, dict) or record.get('argv') != ['kicad-cli', 'version']:
            continue
        stdout = rel[:-len('execution.json')] + 'stdout.log'
        if record.get('execution') != 'ok' or record.get('exit_code') != 0 or stdout not in sealed:
            fail('tool', f'{rel} is not a completed kicad-cli version run with sealed output')
        if digest(ctx.run_dir / stdout) != record.get('stdout_sha256'):
            fail('tool', f'{stdout} differs from its execution record')
        found[rel] = (ctx.run_dir / stdout).read_text(errors='replace').strip()
    return found


def verify_tool(ctx, eid, export_result, tool, gaps):
    """The manifest's kicad-cli identity must be the pinned binary and its sealed version run."""
    from .checks import CheckError
    pinned = pinned_kicad_cli()
    if pinned is None:
        raise CheckError('unsupported', 'pinned kicad-cli unavailable on the sandbox PATH; the manifest '
                                        'tool identity cannot be verified')
    path, sha = pinned
    if tool['path'] != path or os.path.realpath(tool['path']) != os.path.realpath(path):
        fail('tool', f'manifest tool path {tool["path"]!r} is not the pinned kicad-cli {path!r}')
    if tool['sha256'] != sha:
        fail('tool', 'manifest tool sha256 differs from the pinned kicad-cli digest')
    versions = sealed_versions(ctx, eid, export_result)
    record = {'path': path, 'sha256': sha, 'version': tool['version'], 'version_records': sorted(versions),
              'status': 'verified'}
    if not versions:
        record['status'] = 'unverified'
        gaps.append({'code': 'tool_identity', 'detail': f'no sealed kicad-cli version execution in {eid!r}; '
                                                         f'manifest version {tool["version"]!r} unverified'})
    elif set(versions.values()) != {tool['version']}:
        fail('tool', f'manifest tool version {tool["version"]!r} disagrees with the sealed kicad-cli version '
                     f'output {sorted(set(versions.values()))}')
    return record


def check_build_logs(ctx, eid, export_result, defects, gaps):
    """Inspect hash-bound console logs from every sealed native producer execution."""
    sealed = {e for e in export_result.get('evidence', []) if isinstance(e, str)}
    logs = []
    for rel in sorted(sealed):
        if not rel.startswith(f'work/{eid}/') or not rel.endswith('/execution.json'):
            continue
        try:
            record = read_json(ctx.run_dir / rel)
        except (OSError, ValueError) as exc:
            fail('build_log', f'sealed execution record {rel} is unreadable ({exc})')
        argv = record.get('argv', []) if isinstance(record, dict) else []
        if not argv or argv[0] != 'kicad-cli':
            continue
        if record.get('execution') != 'ok' or record.get('exit_code') != 0:
            fail('build_log', f'{rel} is not a completed native producer execution')
        log = rel[:-len('execution.json')] + 'console.log'
        if log not in sealed:
            gaps.append({'code': 'build_log', 'detail': f'{rel} has no sealed console.log'})
            continue
        path = ctx.run_dir / log
        if digest(path) != record.get('console_sha256'):
            fail('build_log', f'{log} differs from its execution record')
        logs.append(log)
        ctx.result['evidence'].extend([rel, log])
        for line, text in enumerate(read_text(ctx.run_dir, log, 'build_log').decode('utf-8').splitlines(), 1):
            if LOG_DIAGNOSTIC.search(text):
                defects.append({'code': 'build_log_warning', 'locator': f'{log}:{line}: {text}'})
    if not logs:
        gaps.append({'code': 'build_log', 'detail': 'no sealed native producer console logs'})
    return logs


# ----------------------------------------------------------------------- manifest

def unique_object(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        fail('manifest', 'duplicate JSON key')
    return dict(pairs)


def load_manifest(ctx, stage):
    path = stage / 'manifest.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail('manifest', 'manifest.json is absent, empty or not a regular file')
    try:
        data = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=unique_object,
                          parse_constant=lambda token: fail('manifest', f'non-finite {token}'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail('manifest', f'manifest.json is not JSON ({exc})')
    if not isinstance(data, dict) or set(data) != MANIFEST_KEYS or data['schema'] != MANIFEST_SCHEMA:
        fail('manifest', f'expected exactly the {MANIFEST_SCHEMA} fields')
    board = ctx.scope.get('board')
    captured = ctx.manifest['files'].get(board)
    if data['candidate_id'] != ctx.manifest['candidate_id']:
        fail('stale', 'manifest names a different candidate')
    if data['board_path'] != board or not isinstance(captured, str):
        fail('stale', f'manifest board_path {data["board_path"]!r} is not the captured scope board')
    if data['board_sha256'] != captured or digest(ctx.inputs / board) != captured:
        fail('stale', 'manifest board_sha256 differs from the captured board digest')
    tool = data['tool']
    if (not isinstance(tool, dict) or set(tool) != {'path', 'version', 'sha256'} or
            not all(isinstance(tool[k], str) and tool[k] for k in tool) or
            not SHA256.fullmatch(tool['sha256'])):
        fail('manifest', 'tool needs path, version and sha256')
    if not isinstance(data['generated_utc'], str) or not data['generated_utc']:
        fail('manifest', 'generated_utc must be text')
    files = data['files']
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        fail('manifest', 'files must be a nonempty bounded mapping')
    for rel, sha in files.items():
        parts = PurePosixPath(rel).parts
        if (not isinstance(rel, str) or rel != PurePosixPath(rel).as_posix() or not parts or
                PurePosixPath(rel).is_absolute() or '\\' in rel or
                any(p in ('', '.', '..') for p in parts) or rel == 'manifest.json' or
                not isinstance(sha, str) or not SHA256.fullmatch(sha)):
            fail('manifest', f'unsafe or unhashed file entry {rel!r}')
    copper = data['copper_layers']
    layers = data['enabled_fab_layers']
    if isinstance(copper, bool) or not isinstance(copper, int) or copper < 1:
        fail('manifest', 'copper_layers must be a positive integer')
    if (not isinstance(layers, list) or not layers or
            any(not isinstance(n, str) or not n for n in layers) or len(set(layers)) != len(layers)):
        fail('manifest', 'enabled_fab_layers must be unique layer names')
    drill = data['drill']
    if (not isinstance(drill, dict) or set(drill) != {'pth', 'npth'} or
            not all(isinstance(drill[k], str) and drill[k] for k in drill) or
            drill['pth'] == drill['npth']):
        fail('manifest', 'drill must name distinct pth and npth files')
    designators = data['designators']
    non_dnp = designators.get('board_non_dnp') if isinstance(designators, dict) else None
    if (set(designators or {}) != {'board_non_dnp'} or not isinstance(non_dnp, list) or
            any(not isinstance(r, str) or not r for r in non_dnp) or
            len(set(non_dnp)) != len(non_dnp)):
        fail('manifest', 'designators.board_non_dnp must be unique references')
    return data


def inventory(stage, manifest, missing_files=None):
    """Verify regular, nonempty, hash-bound files against the exact manifest.

    Roundtrip may collect absent files as named defects through missing_files;
    the assembly gate retains its strict missing-file refusal.
    """
    found = {}
    pending = [stage]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                rel = os.path.relpath(entry.path, stage).replace(os.sep, '/')
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    fail('inventory', f'{rel} is a symbolic link')
                if stat.S_ISDIR(mode):
                    pending.append(entry.path)
                elif not stat.S_ISREG(mode):
                    fail('inventory', f'{rel} is not a regular file')
                elif rel != 'manifest.json':
                    found[rel] = entry.stat(follow_symlinks=False).st_size
                if len(found) > MAX_FILES:
                    fail('inventory', 'staging tree exceeds the file limit')
    declared = manifest['files']
    extra, missing = sorted(set(found) - set(declared)), sorted(set(declared) - set(found))
    if extra or (missing and missing_files is None):
        fail('inventory', f'staging files not in manifest {extra}; manifest files absent {missing}')
    if missing_files is not None:
        missing_files.extend(missing)
    empty = sorted(rel for rel, size in found.items() if size == 0)
    if empty:
        fail('empty', f'empty staged artifact(s) {empty}')
    stale = sorted(rel for rel in found if digest(stage / rel) != declared[rel])
    if stale:
        fail('hash', f'staged bytes differ from manifest hashes {stale}')
    absent = [name for name in REQUIRED_FILES if name not in found]
    if absent:
        fail('inventory', f'required package file(s) absent {absent}')
    return found


def read_text(stage, rel, label):
    if (stage / rel).stat().st_size > MAX_TEXT_BYTES:
        fail(label, f'{rel} exceeds the read limit')
    return (stage / rel).read_bytes()


# --------------------------------------------------------------------------- board

def board_facts(ctx):
    from .symbol_library import LibraryError, children, parse_sexpr
    rel = ctx.scope['board']
    try:
        root = parse_sexpr((ctx.inputs / rel).read_bytes())
    except (LibraryError, OSError) as exc:
        fail('board', f'captured board does not parse: {exc}')
    if root[0] != 'kicad_pcb':
        fail('board', 'captured board is not kicad_pcb')

    def atoms(node):
        return [x for x in node[1:] if isinstance(x, str)]

    layer_nodes = children(root, 'layers')
    if len(layer_nodes) != 1:
        fail('board', 'board needs one layers table')
    layer_names = set()
    copper = []
    for layer in layer_nodes[0][1:]:
        values = [x for x in layer if isinstance(x, str)] if isinstance(layer, list) else []
        if len(values) < 2:
            fail('board', 'unreadable board layer entry')
        layer_names.add(values[1])
        if values[1].endswith('.Cu'):
            copper.append(values[1])
    footprints = []
    for fp in children(root, 'footprint'):
        lib_id = atoms(fp)[0] if atoms(fp) else ''
        layer = children(fp, 'layer')
        side = {'F.Cu': 'top', 'B.Cu': 'bottom'}.get(atoms(layer[0])[0] if layer and atoms(layer[0]) else None)
        refs = [atoms(p)[1] for p in children(fp, 'property')
                if len(atoms(p)) >= 2 and atoms(p)[0] == 'Reference']
        refs += [atoms(t)[1] for t in children(fp, 'fp_text')
                 if len(atoms(t)) >= 2 and atoms(t)[0] == 'reference']
        if len(refs) != 1 or side is None:
            fail('board', f'footprint {lib_id!r} lacks one reference or a top/bottom side')
        attrs = {a for node in children(fp, 'attr') for a in atoms(node)}
        models = [atoms(m)[0] for m in children(fp, 'model') if atoms(m)]
        footprints.append({'ref': refs[0], 'lib_id': lib_id, 'side': side,
                           **{flag: flag in attrs for flag in GEOMETRY_FLAGS},
                           'models': models, 'embedded': kicad_embedded.records(fp)})
    return {'copper': copper, 'layers': layer_names, 'footprints': footprints,
            'embedded': kicad_embedded.records(root)}


def assembled(fp):
    return not (fp['dnp'] or fp['exclude_from_bom'] or fp['exclude_from_pos_files'])


def reconcile_native(board, geometry, netlist, gaps):
    """Board parse == sealed pcbnew geometry; board and netlist membership agree."""
    parsed = sorted((fp['ref'], fp['side'], *(fp[flag] for flag in GEOMETRY_FLAGS)) for fp in board['footprints'])
    inventory = [fp[:-1] for fp in geometry]
    if parsed != inventory:
        differences = sorted(set(parsed) ^ set(inventory), key=str)
        fail('native', f'captured board parse disagrees with the sealed geometry footprints {differences[:6]}')
    for fp in board['footprints']:
        if fp['ref'] in netlist:
            continue
        if not fp['board_only']:
            fail('native', f'board footprint {fp["ref"]} is absent from the sealed native netlist')
        if assembled(fp):
            gaps.append({'code': 'board_only_assembled', 'detail': f'board-only footprint {fp["ref"]} is '
                                                                   'assembled without a schematic part'})
    on_board = {fp['ref'] for fp in board['footprints']}
    for ref, flags in sorted(netlist.items()):
        if ref not in on_board and not flags['exclude_from_board']:
            fail('native', f'netlist component {ref} has no board footprint and is not excluded from the board')


def reconcile_layers(manifest, board):
    enabled = set(manifest['enabled_fab_layers'])
    copper = set(board['copper'])
    if manifest['copper_layers'] != len(copper):
        fail('layers', f'manifest copper_layers {manifest["copper_layers"]} but board has {len(copper)}')
    if {n for n in enabled if n.endswith('.Cu')} != copper:
        fail('layers', 'enabled copper layers differ from the board copper stack')
    if not REQUIRED_NON_COPPER <= enabled or not enabled <= copper | NON_COPPER_FAB:
        fail('layers', f'enabled fab layers {sorted(enabled)} omit required or name non-fab layers')
    if not enabled <= board['layers']:
        fail('layers', 'enabled fab layer absent from the board layer table')
    return enabled


def plot_layer(function, copper_count):
    fields = function.split(',')
    if fields[0] == 'Copper' and len(fields) >= 3 and re.fullmatch(r'L\d+', fields[1]):
        index = int(fields[1][1:])
        if fields[2] == 'Top' and index == 1:
            return 'F.Cu'
        if fields[2] == 'Bot' and index == copper_count:
            return 'B.Cu'
        if fields[2] == 'Inr' and 1 < index < copper_count:
            return f'In{index - 1}.Cu'
        fail('layers', f'copper plot function {function!r} contradicts {copper_count} copper layers')
    if fields[0] in SIDE_FUNCTIONS and len(fields) >= 2 and fields[1] in ('Top', 'Bot'):
        return ('F.' if fields[1] == 'Top' else 'B.') + SIDE_FUNCTIONS[fields[0]]
    if fields[0] == 'Profile':
        return 'Edge.Cuts'
    return None


def check_plots(stage, found, manifest, enabled, gaps):
    plots = []
    for rel in sorted(r for r in found if r.startswith('gerbers/') and r.count('/') == 1):
        text = read_text(stage, rel, 'plot').decode('latin-1')
        if '%FS' not in text or 'M02*' not in text:
            continue
        functions = [a or b for a, b in FILE_FUNCTION.findall(text)]
        layer = plot_layer(functions[0], manifest['copper_layers']) if len(functions) == 1 else None
        plots.append({'path': rel, 'file_function': functions[0] if len(functions) == 1 else None,
                      'layer': layer})
    if len(plots) < len(enabled):
        fail('plots', f'{len(plots)} plot file(s) for {len(enabled)} enabled fab layer(s)')
    by_layer = {}
    for plot in plots:
        if plot['layer']:
            by_layer.setdefault(plot['layer'], []).append(plot['path'])
    doubled = {k: v for k, v in by_layer.items() if len(v) > 1}
    if doubled:
        fail('plots', f'several plots claim one layer {doubled}')
    absent = sorted(enabled - set(by_layer))
    if absent:
        if any(p['file_function'] is None for p in plots):
            gaps.append({'code': 'plot_identity', 'detail':
                         f'layer(s) {absent} unmatched while some plots lack one FileFunction attribute'})
        else:
            fail('plots', f'no plot for enabled fab layer(s) {absent}')
    return plots


def check_drill(stage, found, manifest, gaps):
    names = {}
    for key in ('pth', 'npth'):
        name = manifest['drill'][key]
        rel = name if '/' in name else f'drill/{name}'
        if PurePosixPath(rel).parent.as_posix() != 'drill' or rel not in found:
            fail('drill', f'manifest {key} file {name!r} is not a staged drill/ file')
        names[rel] = key
    excellon = []
    for rel in sorted(r for r in found if r.startswith('drill/')):
        text = read_text(stage, rel, 'drill').decode('latin-1')
        if text.lstrip().startswith('M48'):
            excellon.append(rel)
        elif '%FS' not in text:
            fail('drill', f'{rel} is neither Excellon nor a Gerber drill map')
    if sorted(excellon) != sorted(names):
        fail('drill', f'expected exactly the PTH and NPTH Excellon files, found {excellon}')
    records = []
    for rel in excellon:
        functions = DRILL_FUNCTION.findall(read_text(stage, rel, 'drill').decode('latin-1'))
        expected = 'Plated' if names[rel] == 'pth' else 'NonPlated'
        if any(f.startswith('MixedPlating') for f in functions):
            fail('drill', f'{rel} is a MixedPlating drill file')
        if len(functions) != 1:
            gaps.append({'code': 'drill_plating', 'detail': f'{rel} lacks one plating FileFunction'})
        elif functions[0].split(',')[0] != expected or functions[0].split(',')[-1] != names[rel].upper():
            fail('drill', f'{rel} declared {names[rel].upper()} but its plating attribute is {functions[0]!r}')
        records.append({'path': rel, 'role': names[rel], 'file_function': functions[0] if functions else None})
    return records


def check_zip(stage, found):
    expected = {rel: found[rel] for rel in found if rel.startswith(('gerbers/', 'drill/'))}
    basenames = {}
    for rel in expected:
        basenames.setdefault(PurePosixPath(rel).name, []).append(rel)
    matched = set()
    try:
        with zipfile.ZipFile(stage / 'gerbers.zip') as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            for info in members:
                name = info.filename
                parts = PurePosixPath(name).parts
                if (PurePosixPath(name).is_absolute() or '\\' in name or
                        any(p in ('', '.', '..') for p in parts)):
                    fail('zip', f'unsafe archive member {name!r}')
                rel = name if name in expected else (
                    basenames[name][0] if '/' not in name and len(basenames.get(name, ())) == 1 else None)
                if rel is None or rel in matched:
                    fail('zip', f'archive member {name!r} is extra or duplicated')
                if info.file_size != expected[rel]:
                    fail('zip', f'archive member {name!r} differs in size from {rel}')
                if (stage / rel).read_bytes() != archive.read(info):
                    fail('zip', f'archive member {name!r} differs from {rel}')
                matched.add(rel)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError) as exc:
        fail('zip', f'gerbers.zip unreadable ({exc})')
    if matched != set(expected):
        fail('zip', f'gerbers.zip omits {sorted(set(expected) - matched)}')
    return len(matched)


# ---------------------------------------------------------------------------- CSV

def read_csv(stage, rel, header):
    try:
        text = read_text(stage, rel, 'csv').decode('utf-8')
        rows = [r for r in csv.reader(io.StringIO(text, newline=''), strict=True)
                if any(cell.strip() for cell in r)]
    except (UnicodeDecodeError, csv.Error) as exc:
        fail('csv', f'{rel} does not parse ({exc})')
    if not rows or rows[0] != header:
        fail('csv', f'{rel} header must be exactly {header}')
    if len(rows) == 1:
        fail('csv', f'{rel} has no rows')
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            fail('csv', f'{rel} row {index} has {len(row)} cells, expected {len(header)}')
    return rows[1:]


def designator_defect(token):
    if not token:
        return 'empty_designator'
    if RANGE.fullmatch(token):
        return 'compressed_range'
    if not DESIGNATOR.fullmatch(token):
        return 'invalid_designator'
    return None


def coordinate(value, label, rel, ref):
    text = value.strip()
    if text.lower().endswith('mm'):
        text = text[:-2].strip()
    if not NUMBER.fullmatch(text) or not math.isfinite(float(text)):
        fail('csv', f'{rel} {ref}: {label} {value!r} is not a finite number')
    try:
        return Decimal(text)
    except DecimalException as exc:
        fail('csv', f'{rel} {ref}: {label} {value!r} is not a supported decimal ({exc})')


def check_bom(rows, defects):
    designators, seen, lcsc_rows = [], set(), {}
    for index, row in enumerate(rows, start=2):
        cells = dict(zip(BOM_HEADER, row))
        locator = f'bom.csv row {index}'
        if any(not cell.isascii() for cell in row):
            defects.append({'code': 'non_ascii_bom_cell', 'locator': locator})
        tokens = [t.strip() for t in cells['Designator'].split(',')]
        for token in tokens:
            code = designator_defect(token)
            if code:
                defects.append({'code': code, 'locator': f'{locator} designator {token!r}', 'file': 'bom.csv'})
            elif token in seen:
                defects.append({'code': 'duplicate_designator', 'locator': f'{locator} designator {token}',
                                'file': 'bom.csv'})
            else:
                seen.add(token)
                designators.append((token, index))
        quantity = cells['Quantity'].strip()
        if not quantity.isdigit() or int(quantity) != len(tokens):
            defects.append({'code': 'quantity_mismatch', 'file': 'bom.csv',
                            'locator': f'{locator} quantity {quantity!r} for {len(tokens)} designator(s)'})
        lcsc = cells['LCSC Part #'].strip()
        if not lcsc:
            defects.append({'code': 'empty_lcsc', 'file': 'bom.csv',
                            'locator': f'{locator} designators {cells["Designator"]!r}'})
        elif not LCSC.fullmatch(lcsc):
            defects.append({'code': 'invalid_lcsc', 'file': 'bom.csv', 'locator': f'{locator} LCSC {lcsc!r}'})
        else:
            lcsc_rows.setdefault(lcsc, []).append((index, cells['Footprint'].strip(),
                                                   [t for t in tokens if t]))
    return designators, lcsc_rows


def check_cpl(rows, defects):
    placements, seen = {}, set()
    for index, row in enumerate(rows, start=2):
        cells = dict(zip(CPL_HEADER, row))
        ref = cells['Designator'].strip()
        locator = f'cpl.csv row {index} designator {ref!r}'
        code = designator_defect(ref)
        if code:
            defects.append({'code': code, 'locator': locator, 'file': 'cpl.csv'})
            continue
        if ref in seen:
            defects.append({'code': 'duplicate_designator', 'locator': locator, 'file': 'cpl.csv'})
            continue
        seen.add(ref)
        position = [coordinate(cells[label], label, 'cpl.csv', ref) for label in ('Mid X', 'Mid Y')]
        coordinate(cells['Rotation'], 'Rotation', 'cpl.csv', ref)
        layer = cells['Layer'].strip().lower()
        if layer not in ('top', 'bottom'):
            defects.append({'code': 'invalid_cpl_layer', 'locator': f'{locator} Layer {cells["Layer"]!r}',
                            'file': 'cpl.csv'})
            layer = None
        placements[ref] = {'row': index, 'layer': layer, 'position_mm': position}
    return placements


def check_coordinates(cpl, geometry, by_ref, defects):
    """fab_export/1 uses the footprint anchor, absolute X and negated board Y on both sides."""
    native_positions = {fp[0]: fp[-1] for fp in geometry}
    records = []
    for ref, placement in sorted(cpl.items()):
        if len(by_ref.get(ref, ())) != 1:
            continue  # reference parity/duplicates are reported separately
        x, y = native_positions[ref]
        expected = [Decimal(x) / 1_000_000, -Decimal(y) / 1_000_000]
        delta = [abs(actual - native) for actual, native in zip(placement['position_mm'], expected)]
        # Compare to exact bounds: subtracting an arbitrarily precise CSV value
        # first could round a just-outside coordinate onto the tolerance boundary.
        exceeds = any(actual < native - COORDINATE_TOLERANCE or actual > native + COORDINATE_TOLERANCE
                      for actual, native in zip(placement['position_mm'], expected))
        records.append({'ref': ref, 'expected_mm': [float(n) for n in expected],
                        'actual_mm': [float(n) for n in placement['position_mm']],
                        'delta_mm': [float(n) for n in delta], 'outcome': 'defect' if exceeds else 'pass'})
        if exceeds:
            actual_text = ', '.join(str(n) for n in placement['position_mm'])
            expected_text = ', '.join(str(n) for n in expected)
            defects.append({'code': 'cpl_coordinates', 'file': 'cpl.csv', 'locator':
                            f'cpl.csv row {placement["row"]} designator {ref} coordinates '
                            f'({actual_text}) differ from native ({expected_text}) '
                            f'by more than {COORDINATE_TOLERANCE} mm'})
    return {'convention': 'absolute-board-x-negative-y', 'tolerance_mm': float(COORDINATE_TOLERANCE),
            'records': records}


# ------------------------------------------------------------------------ models

def resolve_model(ctx, path, footprint, board):
    """('resolved'|'unresolved'|'uncovered', reason) for one footprint model path."""
    if path.startswith(EMBED):
        # KiCad resolves the footprint's own embedded files before the board's.
        name = path[len(EMBED):]
        scope = footprint['embedded'] if name in footprint['embedded'] else board['embedded']
        return kicad_embedded.verify(scope.get(name, []), name)
    board_dir = PurePosixPath(ctx.scope['board']).parent
    variables = [a or b for a, b in VARIABLE.findall(path)]
    if any(name != 'KIPRJMOD' for name in variables):
        names = sorted(set(variables) - {'KIPRJMOD'})
        return 'uncovered', f'library variable(s) {names} not mapped to captured inputs'
    if '\\' in path:
        return 'uncovered', 'non-POSIX model path'
    expanded = path
    if variables:
        leading = VARIABLE.match(path)
        if leading is None or len(variables) != 1:
            return 'uncovered', 'KIPRJMOD must lead the model path'
        expanded = path[leading.end():].lstrip('/')
    elif PurePosixPath(path).is_absolute():
        originals = {src: rel for rel, src in ctx.manifest.get('sources', {}).items()}
        if os.path.normpath(path) in originals:
            return 'resolved', f'captured as {originals[os.path.normpath(path)]}'
        return 'uncovered', 'absolute model path outside captured inputs'
    rel = os.path.normpath((board_dir / expanded).as_posix()).replace(os.sep, '/')
    if rel == '..' or rel.startswith('../') or PurePosixPath(rel).is_absolute():
        return 'uncovered', 'model path escapes the captured inputs'
    if rel in ctx.manifest['files'] and (ctx.inputs / rel).is_file():
        return 'resolved', f'captured as {rel}'
    return 'unresolved', f'{rel} is not a captured input'


# ------------------------------------------------------------------------- check

def check_fab_consistency(ctx):
    from .checks import CheckError
    eid, export_spec, export_result, stage = export_prerequisite(ctx)
    nid, netlist = netlist_components(ctx, export_spec)
    geometry_ids, geometry = geometry_footprints(ctx)
    details = ctx.result['details']
    details.update(export_check=eid, netlist_check=nid, geometry_checks=geometry_ids, staging=f'work/{eid}/fab')
    defects, gaps = [], []
    try:
        manifest = load_manifest(ctx, stage)
        details['manifest_sha256'] = digest(stage / 'manifest.json')
        details['tool'] = verify_tool(ctx, eid, export_result, manifest['tool'], gaps)
        details['build_logs'] = check_build_logs(ctx, eid, export_result, defects, gaps)
        ctx.result['evidence'] += [rel for record in details['tool']['version_records']
                                   for rel in (record, record[:-len('execution.json')] + 'stdout.log')]
        found = inventory(stage, manifest)
        details['inventory'] = {'files': len(found), 'bytes': sum(found.values())}
        board = board_facts(ctx)
        reconcile_native(board, geometry, netlist, gaps)
        enabled = reconcile_layers(manifest, board)
        plots = check_plots(stage, found, manifest, enabled, gaps)
        drill = check_drill(stage, found, manifest, gaps)
        zipped = check_zip(stage, found)
        try:
            summary = json.loads(read_text(stage, 'pcb_summary.json', 'summary').decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail('summary', f'pcb_summary.json is not JSON ({exc})')
        if not isinstance(summary, dict):
            fail('summary', 'pcb_summary.json must be an object')
        bom_rows = read_csv(stage, 'bom.csv', BOM_HEADER)
        cpl_rows = read_csv(stage, 'cpl.csv', CPL_HEADER)
        bom, lcsc_rows = check_bom(bom_rows, defects)
        cpl = check_cpl(cpl_rows, defects)
    except Malformed as exc:
        raise CheckError('malformed', str(exc)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError('malformed', f'staging read failed: {exc}') from exc
    details.update(enabled_fab_layers=sorted(enabled), plots=plots, drill=drill, zip_members=zipped)

    footprints = board['footprints']
    by_ref = {}
    for fp in footprints:
        by_ref.setdefault(fp['ref'], []).append(fp)
    non_dnp = sorted({fp['ref'] for fp in footprints if not fp['dnp']})
    if non_dnp != sorted(manifest['designators']['board_non_dnp']):
        raise CheckError('malformed', 'stale: manifest board_non_dnp '
                         f'{sorted(set(manifest["designators"]["board_non_dnp"]) ^ set(non_dnp))} '
                         'disagrees with the sealed geometry')
    assembled_refs = {fp['ref'] for fp in footprints if assembled(fp)}
    for ref in sorted(assembled_refs):
        if len(by_ref[ref]) > 1 or designator_defect(ref):
            defects.append({'code': 'board_designator', 'locator': f'board footprint {ref!r} '
                            f'is duplicated or not a usable designator'})
        flags = [flag for flag, value in netlist.get(ref, {}).items() if value and flag != 'exclude_from_board']
        if flags:
            defects.append({'code': 'netlist_exclusion_assembled',
                            'locator': f'board footprint {ref} is assembled but the sealed netlist marks {flags}'})

    bom_refs = {ref for ref, _ in bom}
    parity = []
    for ref in sorted(bom_refs | set(cpl) | assembled_refs):
        membership = {'bom': ref in bom_refs, 'cpl': ref in cpl, 'board': ref in assembled_refs}
        if not all(membership.values()):
            parity.append({'designator': ref, **membership})
            absent = [k for k, v in membership.items() if not v]
            defects.append({'code': 'designator_parity', 'file': 'cpl.csv' if not membership['cpl'] else 'bom.csv',
                            'locator': f'designator {ref} absent from {", ".join(absent)}'})
    # The package follows the board DNP flags; a schematic that disagrees is a
    # parity defect between the two sources, in either direction.
    dnp_disagreements = []
    for ref in sorted(set(netlist) & set(by_ref)):
        board_dnp = any(fp['dnp'] for fp in by_ref[ref])
        if netlist[ref]['dnp'] != board_dnp:
            dnp_disagreements.append({'designator': ref, 'schematic_dnp': netlist[ref]['dnp'],
                                      'board_dnp': board_dnp})
            defects.append({'code': 'designator_parity', 'locator':
                            f'designator {ref} is {"DNP" if netlist[ref]["dnp"] else "populated"} in the '
                            f'sealed netlist but {"DNP" if board_dnp else "populated"} on the board'})
    details['designators'] = {'bom': len(bom_refs), 'cpl': len(cpl), 'board_assembled': len(assembled_refs),
                              'parity_violations': parity, 'dnp_disagreements': dnp_disagreements,
                              'sources': {'geometry': geometry_ids, 'netlist': nid}}

    for lcsc, rows in sorted(lcsc_rows.items()):
        cells = {footprint for _, footprint, _ in rows}
        native = {fp['lib_id'] for _, _, refs in rows for ref in refs for fp in by_ref.get(ref, ())}
        if len(cells) > 1 or len(native) > 1:
            defects.append({'code': 'lcsc_multiple_footprints', 'file': 'bom.csv',
                            'locator': f'LCSC {lcsc} maps to footprints {sorted(cells | native)}'})

    for ref, placement in sorted(cpl.items()):
        boards = by_ref.get(ref, [])
        if placement['layer'] and len(boards) == 1 and boards[0]['side'] != placement['layer']:
            defects.append({'code': 'cpl_side', 'file': 'cpl.csv', 'locator':
                            f'cpl.csv row {placement["row"]} designator {ref} Layer {placement["layer"]} '
                            f'but board footprint is {boards[0]["side"]}'})

    details['coordinates'] = check_coordinates(cpl, geometry, by_ref, defects)

    models = []
    for fp in footprints:
        for path in fp['models']:
            status, reason = resolve_model(ctx, path, fp, board)
            models.append({'ref': fp['ref'], 'path': path, 'status': status, 'reason': reason})
            if status == 'unresolved':
                defects.append({'code': 'model_unresolved',
                                'locator': f'footprint {fp["ref"]} model {path!r}: {reason}'})
            elif status == 'uncovered':
                gaps.append({'code': 'model_coverage', 'detail': f'footprint {fp["ref"]} model {path!r}: {reason}'})
    details['models'] = {'checked': len(models),
                         'footprints_without_model': sorted(fp['ref'] for fp in footprints if not fp['models']),
                         'records': models}

    report = {'schema': 'agent-ee.fab-consistency/1', 'candidate_id': ctx.manifest['candidate_id'],
              'export_check': eid, 'netlist_check': nid, 'manifest_sha256': details['manifest_sha256'],
              'defects': defects, 'coverage_gaps': gaps, 'details': details}
    write_json(ctx.work / 'fab-consistency.json', report)
    ctx.evidence('fab-consistency.json')
    details.update(defects=defects, coverage_gaps=gaps)
    ctx.result['limitations'] += [
        'CPL coordinates use the fab_export/1 native footprint anchor and absolute export origin; '
        'assembly centroid corrections and rotation policy belong to the rotation lane.',
        'No Gerber geometry or DFM; plot identity comes from FileFunction attributes.',
        '3D model resolution establishes captured-file presence only: embedded models are decoded '
        'and checksum-verified like a native load, but model content and fit are not assessed.',
        'The board inventory is the sealed pcbnew geometry; board-only parts that would be assembled '
        'have no package policy and stay unknown.']
    sealed = set(export_result.get('evidence', []))
    if defects:
        own = f'work/{ctx.cid}/fab-consistency.json'
        for defect in defects[:MAX_CITATIONS]:
            staged = f'work/{eid}/fab/{defect["file"]}' if defect.get('file') else None
            path = staged if staged in sealed else own
            ctx.result['evidence'].append({'path': path, 'locator': f'{defect["code"]}: {defect["locator"]}'})
        ctx.result['location'] = '; '.join(d['locator'] for d in defects[:MAX_CITATIONS])
        ctx.result.update(execution='ok', outcome='defect',
                          summary=f'{len(defects)} fab-package defect(s): '
                                  + ', '.join(sorted({d["code"] for d in defects})))
        if gaps:
            ctx.result['limitations'].append(f'{len(gaps)} invariant(s) also lacked coverage; see details.coverage_gaps')
        return
    if gaps:
        raise CheckError('unsupported', f'{len(gaps)} fab-package invariant(s) lack coverage: '
                         + '; '.join(g['detail'] for g in gaps[:5]))
    ctx.result.update(execution='ok', outcome='pass',
                      summary=f'fab package consistent: {len(found)} hash-bound files, {len(plots)} plots for '
                              f'{len(enabled)} fab layers, PTH+NPTH drill, {len(assembled_refs)} assembled '
                              f'designators in BOM/CPL/board parity, {len(models)} 3D model path(s) resolved')
