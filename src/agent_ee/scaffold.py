"""Create an empty native candidate from captured planning and verification inputs.

Stage one: the caller supplies the scope and project rules; scaffolding preserves
its baseline and checks. Stage two: a producer plan supplies only what neither
record defines (native-validity requirement, targets, references, a native
project template); checks, mappings and design rules are derived from the
envelope, its fabricator profile and the parts manifest. Neither mode can choose
acceptance criteria or waive uncovered board-envelope obligations. No
owner-approval claim is made here. Stage three writes declared board content
on an isolated native copy through an ee139 scaffold pre-receipt.
"""
import argparse
import copy
import hashlib
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import uuid

from . import board_envelope
from .candidate import safe_relative, source_manifest
from .catalog import strict_json
from .fab_profile import NATIVE, compile_profile
from .footprint_library import _members
from .parts_manifest import load_manifest, qualify_manifest, read_captured
from .schematic_emit import Quoted as Q, format_sexpr
from .silkscreen import prepare_footprint, overhang_orientation_mark
from .storage import digest, identity, json_bytes
from .tool_source import snapshot
from .symbol_library import children, nanometres, parse_sexpr

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/halbritt/agent-ee/scaffold/1')
SCHEMA = 'agent-ee.scaffold-receipt/1'
PRODUCED_SCHEMA = 'agent-ee.scaffold-receipt/2'
PLAN_SCHEMA = 'agent-ee.scaffold-plan/1'
PLAN_FIELDS = ('schema', 'name', 'scope', 'limitations', 'inputs', 'project', 'template', 'fab_profile',
               'native_requirement', 'targets', 'reference_architectures', 'finding_retry_limit')
NET_PLAN_FIELDS = frozenset({'net_constraints', 'netclass_qualification'})
# Every fab-profile key with a native KiCad project rule; each is required.
RULE_KEYS = ('min_clearance_mm', 'min_trace_mm', 'min_via_pad_mm', 'min_via_drill_mm',
             'min_edge_clearance_mm', 'min_hole_to_hole_mm')
NETCLASS_FIELDS = {'min_clearance_mm': 'clearance', 'min_trace_mm': 'track_width',
                   'min_via_pad_mm': 'via_diameter', 'min_via_drill_mm': 'via_drill'}
# Default class fields KiCad DRC enforces as a hard floor. The class track width and
# via size are routing defaults; DRC grades them against the board minimum rules.
DRC_ENFORCED = frozenset({'min_clearance_mm'})
DECISION = 'owner decision 2026-10-18: Default clearance = fab minimum; router targets preferred where it can'
ENFORCED_BASIS = 'KiCad DRC enforces the Default class value as a hard floor; ' + DECISION
ROUTING_BASIS = ('routing default only: KiCad DRC enforces the board minimum rule, not the Default class value; '
                 + DECISION)
JOINT_BASIS = ('routing default only: the Default via drill is inside a joint small-via surcharge band, so the '
               'diameter takes that rule\'s bound; ' + DECISION)
# The pinned kicad-route adapter fixes default_clearance_mm and clearance_margin_mm to null.
UNCONSUMED_TARGET = ('not applied: a preferred Default clearance would become a DRC floor above the fab minimum, '
                     'and the pinned kicad-route adapter passes no clearance target')
PARTS_REQUIREMENT = 'parts-membership'
PRODUCER_LIMITATIONS = [
    'Producer-derived rules: six project rules and the DRC-enforced Default class clearance come from '
    'the captured fab profile minimum tier; the Default track width and via size take the preferred tier as '
    'routing defaults; other KiCad rule settings keep the captured template values.',
    'Stage subjects are the parts manifest required root-sheet selectors; no fab_package done gate, '
    'scope/3 envelope acceptance is derived. Per-net classes require captured net constraints.']
# Stage of each derived check. The fab check's native loading witness needs a saved track.
STAGES = (('erc', 'schematic'), ('netlist', 'schematic'), ('parts', 'schematic'),
          ('drc', 'placement'), ('fab', 'routing'))


def sexpr(node):
    return ('\n'.join(format_sexpr(node)) + '\n').encode('utf-8')


def sheet_plan(document, root_path):
    """Preserve native instance-path UUIDs; create every ancestor before capture."""
    plans = {'/': (root_path, None)}
    used = set()
    for part in document['parts']:
        for selector in part['instances']:
            trail = selector['sheet'].strip('/').split('/') if selector['sheet'] != '/' else []
            parent = '/'
            for token in trail:
                if str(uuid.UUID(token)) != token:
                    raise ValueError('sheet selectors must use canonical native UUIDs')
                path = parent + token + '/'
                if path not in plans:
                    if token in used:
                        raise ValueError('repeated sheet UUID is unsupported by the scaffold')
                    used.add(token)
                    filename = str(PurePosixPath(root_path).parent / ('block-' + token + '.kicad_sch'))
                    plans[path] = (filename, parent)
                parent = path
    if len(plans) > 128:
        raise ValueError('scaffold supports at most 128 sheets')
    return dict(sorted(plans.items()))


def schematic(root_uuid, path, plans, title):
    ident = root_uuid if path == '/' else path.strip('/').split('/')[-1]
    node = ['kicad_sch', ['version', '20260306'], ['generator', Q('eeschema')],
            ['generator_version', Q('10.0')], ['uuid', Q(ident)], ['paper', Q('A4')],
            ['title_block', ['title', Q(title)], ['comment', '1', Q('Empty generation candidate')]],
            ['lib_symbols']]
    children = [(key, value) for key, value in plans.items() if value[1] == path]
    for index, (key, (filename, _)) in enumerate(children):
        child = key.strip('/').split('/')[-1]
        x, y = 25 + (index % 5) * 50, 25 + (index // 5) * 25
        if y > 175:
            raise ValueError('more child sheets than fit on the scaffold A4 page')
        effects = ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'left']]
        node.append(['sheet', ['at', str(x), str(y)], ['size', '40', '15'],
                     ['stroke', ['width', '0'], ['type', 'default']],
                     ['fill', ['color', '0', '0', '0', '0']], ['uuid', Q(child)],
                     ['property', Q('Sheetname'), Q('Block ' + child),
                      ['at', str(x), str(y - 1), '0'], copy.deepcopy(effects)],
                     ['property', Q('Sheetfile'), Q(PurePosixPath(filename).name),
                      ['at', str(x), str(y + 16), '0'], copy.deepcopy(effects)],
                     ['instances', ['project', Q(title),
                                    ['path', Q('/' + root_uuid + path.rstrip('/')),
                                     ['page', Q(str(list(plans).index(key) + 1))]]]]])
    if path == '/':
        node.append(['sheet_instances', ['path', Q('/'), ['page', Q('1')]]])
    node.append(['embedded_fonts', 'no'])
    return sexpr(node)


def board(record, seed):
    """Emit declared copper roles; legacy envelopes keep power inner layers."""
    count = record['layer_count']
    if count % 2:
        raise ValueError('native scaffold requires an even copper layer count')
    copper = record['copper_layers']
    roles = {layer['name']: ('power' if layer['role'] == 'plane' else 'signal')
             for layer in record['stackup']['layers'] if 'role' in layer}
    layers = [['0', Q('F.Cu'), roles.get('F.Cu', 'signal')]]
    layers += [[str(4 + 2 * i), Q(name), roles.get(name, 'power')] for i, name in enumerate(copper[1:-1])]
    layers.append(['2', Q('B.Cu'), roles.get('B.Cu', 'signal')])
    # KiCad 10 technical-layer numbers, independent of copper layer count.
    layers += [[str(i), Q(name), 'user'] for i, name in (
        (1, 'F.Mask'), (3, 'B.Mask'), (5, 'F.SilkS'), (7, 'B.SilkS'),
        (9, 'F.Adhes'), (11, 'B.Adhes'), (13, 'F.Paste'), (15, 'B.Paste'),
        (17, 'Dwgs.User'), (19, 'Cmts.User'), (21, 'Eco1.User'), (23, 'Eco2.User'),
        (25, 'Edge.Cuts'), (27, 'Margin'), (29, 'B.CrtYd'), (31, 'F.CrtYd'),
        (33, 'B.Fab'), (35, 'F.Fab'))]
    stackup = ['stackup']
    for index, layer in enumerate(record['stackup']['layers']):
        label = layer['name'] if layer['kind'] == 'copper' else 'dielectric ' + str((index + 1) // 2)
        stackup.append(['layer', Q(label), ['type', Q('copper' if layer['kind'] == 'copper' else 'core')],
                        ['thickness', layer['thickness_mm']]])
    node = ['kicad_pcb', ['version', '20260206'], ['generator', Q('pcbnew')],
            ['generator_version', Q('10.0')],
            ['general', ['thickness', record['stackup']['thickness_mm']]], ['paper', Q('A4')],
            ['layers', *layers], ['setup', stackup]]
    for edge in record['outline']['edges']:
        node.append(['gr_line', ['start', *edge['start_mm']], ['end', *edge['end_mm']],
                     ['stroke', ['width', '0.1'], ['type', 'default']], ['layer', Q('Edge.Cuts')],
                     ['uuid', Q(str(uuid.uuid5(NAMESPACE, seed + ':edge:' + edge['name'])))]])
    node.append(['embedded_fonts', 'no'])
    return sexpr(node)


def native_files(record, sch, project, root, seed, plans=None):
    """The one empty-scaffold builder: schematics, board and .kicad_pro by candidate path.

    record is a board_envelope.validate record; project is the parsed .kicad_pro template,
    of which only filename and sheet metadata change. plans defaults to a flat root sheet.
    """
    plans = plans or {'/': (sch, None)}
    pcb, pro = (str(PurePosixPath(sch).with_suffix(suffix)) for suffix in ('.kicad_pcb', '.kicad_pro'))
    name = PurePosixPath(sch).stem
    outputs = {path: schematic(root, key, plans, name) for key, (path, _) in plans.items()}
    outputs[pcb] = board(record, seed)
    project = copy.deepcopy(project)
    project.setdefault('meta', {})['filename'] = PurePosixPath(pro).name
    project['sheets'] = [[root, 'Root']] + [
        [key.strip('/').split('/')[-1], PurePosixPath(path).stem]
        for key, (path, _) in plans.items() if key != '/']
    project.setdefault('schematic', {})['top_level_sheets'] = [
        dict(filename=PurePosixPath(sch).name, name=name, uuid=root)]
    outputs[pro] = json_bytes(project)
    return outputs


def emission_paths(inputs, intent_rel, layout_rel, document):
    """Ask the existing emitter for native paths; never reproduce its UUID recipe."""
    from .schematic_emitter import emit_schematic
    intent = strict_json(read_captured(inputs, intent_rel))
    layout = strict_json(read_captured(inputs, layout_rel))
    captures = {lib['path']: read_captured(inputs, lib['path']) for lib in intent['libraries']}
    result, files = emit_schematic(intent, captures, layout)
    if result['execution'] != 'ok':
        raise ValueError('fixed footprint emission unavailable: ' + str(result['findings']))
    paths, identities = {}, {}
    for filename, raw in files.items():
        if not filename.endswith('.kicad_sch'):
            continue
        for symbol in children(parse_sexpr(raw), 'symbol'):
            uid = children(symbol, 'uuid')[0][1]
            for project in children(children(symbol, 'instances')[0], 'project'):
                for path in children(project, 'path'):
                    ref = children(path, 'reference')[0][1]
                    properties = {p[1]: p[2] for p in children(symbol, 'property')}
                    identities[ref] = (children(symbol, 'lib_id')[0][1], properties.get('Footprint'))
                    # Multiple units share a physical reference; any unit is a native binding.
                    # KiCad XML/board paths omit the document's root UUID.
                    sheet = path[1].removeprefix('/' + result['root_uuid']) + '/'
                    paths.setdefault(ref, sheet + uid)
    for part in document['parts']:
        for instance in part['instances']:
            ref = instance['reference']
            native = paths.get(ref)
            if native is None or native.rsplit('/', 1)[0] + '/' != instance['sheet']:
                raise ValueError('parts selector differs from emitted native path: ' + ref)
            if identities[ref] != (part['lib_id'], part['footprint']):
                raise ValueError('parts CAD identity differs from emitter inputs: ' + ref)
    return paths


def write_envelope_content(inputs, envelope_rel, parts_rel, board_rel, *, intent_rel=None,
                           layout_rel=None, imu_reference=None, antenna_facts_rel=None,
                           captured_rule_areas_rel=None):
    """Stage three, in place on a run_step copy; no nets or schematic hole symbols.

    Parts and emitter inputs are captured authority for identities, not component
    choices. Datasheet facts guide generation; the independent checks must verify
    their citations and the saved board. Only a pristine outline scaffold is accepted.
    """
    import pcbnew
    from .forward_annotation_probe import _stable_uuids
    from .footprint_library import ESP32_FOOTPRINT_ID
    from .envelope_check import antenna_polygon

    inputs = Path(inputs)
    raw = read_captured(inputs, envelope_rel)
    record = board_envelope.validate(raw)
    document = load_manifest(inputs, parts_rel)
    from .copper_features import require_purchased
    fixed_refs = {item['reference'] for item in record['fixed_footprints']}
    for part in document['parts']:
        if any(s['reference'] in fixed_refs for s in part['instances']):
            require_purchased(part, 'fixed-footprint scaffold')
    qualification = qualify_manifest(document, inputs)
    retention = None
    if captured_rule_areas_rel is not None:
        from .scaffold_rule_areas import load_retention
        retention = load_retention(inputs, captured_rule_areas_rel, envelope_rel, parts_rel, record, document)
        if board_rel in retention['dependencies']:
            raise ValueError('board output collides with a captured rule-area dependency')
    source = read_captured(inputs, board_rel)
    if board_rel in qualification['dependencies'] or board_rel in {
            envelope_rel, parts_rel, intent_rel, layout_rel, antenna_facts_rel}:
        raise ValueError('board content output collides with a planning input')
    trip = board_envelope.round_trip(raw, source)
    if any(f['status'] == 'mismatch' for f in trip['fields'] if not f['field'].startswith('/fixed_footprints/')):
        raise ValueError('board outline or stackup differs from the envelope')
    native = pcbnew.LoadBoard(str(inputs / board_rel))
    if list(native.GetFootprints()) or list(native.GetTracks()) or list(native.Zones()) or any(
            item.GetLayer() != pcbnew.Edge_Cuts for item in native.GetDrawings()):
        raise ValueError('board content requires a pristine outline scaffold')
    fixed = {item['reference']: item for item in record['fixed_footprints']}
    parts = {}
    for part in document['parts']:
        for selector in part['instances']:
            ref = selector['reference']
            if ref in parts:
                raise ValueError('board references must be unique across sheets: ' + ref)
            parts[ref] = part
    if set(fixed) - parts.keys():
        raise ValueError('fixed footprints are not declared parts instances: ' + ', '.join(sorted(set(fixed) - parts.keys())))
    if fixed and (intent_rel is None or layout_rel is None):
        raise ValueError('fixed footprints require captured intent and emitter layout')
    paths = emission_paths(inputs, intent_rel, layout_rel, document) if fixed else {}
    if imu_reference is not None and imu_reference not in fixed:
        raise ValueError('IMU reference must select a declared fixed footprint')
    members = _members(strict_json(read_captured(inputs, document['cad']['manifest']['path'])))
    library = {lib + ':' + PurePosixPath(member['path']).stem: member for lib, member in members}
    seed = hashlib.sha256(raw).hexdigest()

    def kiid(label):
        return pcbnew.KIID(str(uuid.uuid5(NAMESPACE, seed + ':' + label)))

    def position(values):
        return pcbnew.VECTOR2I(*(nanometres(v) for v in values))

    footprints = {}
    for ref, declaration in fixed.items():
        part = parts[ref]
        member = library.get(part['footprint'])
        if member is None:
            raise ValueError('fixed footprint is not an exact captured member: ' + ref)
        # qualify_manifest verified the full closure, including this member's hash.
        path = inputs / member['path']
        fp = pcbnew.FootprintLoad(str(path.parent), path.stem)
        if fp is None:
            raise ValueError('captured fixed footprint failed native load: ' + ref)
        fp.SetFPID(pcbnew.LIB_ID(*part['footprint'].split(':')))
        fp.SetReference(ref)
        fp.SetValue(part['mpn'])
        fp.SetPath(pcbnew.KIID_PATH(paths[ref]))
        _stable_uuids(fp, paths[ref])
        for index, zone in enumerate(fp.Zones()):
            zone.SetUuidDirect(kiid(f'fixed:{ref}:zone:{index}'))
        native.Add(fp)  # Flip needs the board's layer stack, including on pad-only footprints.
        if declaration['side'] == 'back':
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
        fp.SetOrientationDegrees(float(declaration['rotation_deg']))
        fp.SetPosition(position(declaration['anchor_mm']))
        fp.SetLocked(True)
        prepare_footprint(fp, overhang=bool(declaration.get('courtyard_overhangs')))
        footprints[ref] = fp

    for hole in record['mounting_holes']:
        if hole['id'] in parts:
            raise ValueError('mounting holes are board-only; remove the pinless symbol/parts instance: ' + hole['id'])
        fp = pcbnew.FOOTPRINT(native)
        fp.SetReference(hole['id'])
        fp.Reference().SetLayer(pcbnew.F_Fab)
        fp.SetFPID(pcbnew.LIB_ID('',
            'AgentEE_Mechanical_MountingHole_PTH' if hole['plated'] else 'AgentEE_Mechanical_MountingHole_NPTH'))
        fp.SetValue('MountingHole')
        fp.SetAttributes(pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        fp.SetPosition(position(hole['at_mm']))
        fp.SetLocked(True)
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH if hole['plated'] else pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        drill = nanometres(hole['drill_mm'])
        pad.SetDrillSize(pcbnew.VECTOR2I(drill, drill))
        size = drill + (600000 if hole['plated'] else 0)
        pad.SetSize(pcbnew.VECTOR2I(size, size))
        # AllCuMask() is KiCad's shared static set; mutating it in place makes
        # every later save fold *.Mask into *.Cu, dropping pad masks on reload.
        layers = pcbnew.LSET(pcbnew.LSET.AllCuMask())
        layers.AddLayer(pcbnew.F_Mask)
        layers.AddLayer(pcbnew.B_Mask)
        pad.SetLayerSet(layers)
        pad.SetPosition(fp.GetPosition())
        fp.Add(pad)
        # Straight courtyards on both sides, the square around the pad circle, make the
        # hole a fixed occupant for the placer and courtyard checks (AGENTEE-179). No
        # fastener head or standoff size is declared, so none is reserved.
        half, at = (size + 1) // 2, fp.GetPosition()
        for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            courtyard = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
            courtyard.SetLayer(layer)
            courtyard.SetStart(pcbnew.VECTOR2I(at.x - half, at.y - half))
            courtyard.SetEnd(pcbnew.VECTOR2I(at.x + half, at.y + half))
            courtyard.SetWidth(50000)
            fp.Add(courtyard)
        _stable_uuids(fp, 'board-only:' + seed + ':' + hole['id'])
        native.Add(fp)

    for area in record['keepouts']:
        if retention is not None and area['id'] in retention['items']:
            # Already loaded from the exact captured member. No derived/minimum zone.
            continue
        if 'antenna' in area:
            if antenna_facts_rel is None:
                raise ValueError('antenna keepout requires captured antenna facts')
            ref = area['antenna']['reference']
            if ref not in footprints or parts[ref]['footprint'] != ESP32_FOOTPRINT_ID:
                raise ValueError('antenna requires a declared fixed ESP32 module transform')
            facts = strict_json(read_captured(inputs, antenna_facts_rel))
            fp = footprints[ref]
            # Share the check's exact native front/back/quarter-turn transform.
            pos = fp.GetPosition()
            polygon, _, _ = antenna_polygon(area['antenna'], {'footprints': [dict(
                ref=ref, fpid=fp.GetFPIDAsString(), uuid=fp.m_Uuid.AsString(),
                rotation_deg=fp.GetOrientationDegrees(), side='B' if fp.IsFlipped() else 'F',
                position_nm=[pos.x, pos.y])]}, facts)
            # The check also requires the module-owned rule area. Retain it from
            # the captured footprint and adding the declared restriction. Native
            # footprint zones use board coordinates in memory.
            fp.Add(rule_area(fp, area, polygon, native, kiid('owned:' + area['id'])))
        else:
            polygon = [[nanometres(v) for v in p] for p in area['polygon_mm']]
        native.Add(rule_area(native, area, polygon, native, kiid('area:' + area['id'])))

    if imu_reference is not None:
        text = pcbnew.PCB_TEXT(native)
        text.SetText('IMU_AXES  X forward  Y left  Z up')
        text.SetLayer(pcbnew.F_SilkS)
        anchor = footprints[imu_reference].GetPosition()
        text.SetPosition(pcbnew.VECTOR2I(anchor.x, anchor.y + 4000000))
        text.SetTextSize(pcbnew.VECTOR2I(800000, 800000))
        text.SetTextThickness(120000)
        text.SetUuidDirect(kiid('IMU_AXES'))
        native.Add(text)
    for ref, declaration in fixed.items():
        if declaration.get('courtyard_overhangs'):
            mark = overhang_orientation_mark(native, footprints[ref], record, declaration['rotation_deg'])
            mark.SetUuidDirect(kiid('assembly-orientation:' + ref))
            native.Add(mark)
    target = inputs / board_rel
    with tempfile.TemporaryDirectory(prefix='.envelope-', dir=target.parent) as tmp:
        staged = Path(tmp) / target.name
        if not pcbnew.SaveBoard(str(staged), native):
            raise ValueError('native envelope content save failed')
        if retention is not None:
            from .scaffold_rule_areas import verify_native_retention
            for rel, pin in retention['dependencies'].items():
                if hashlib.sha256(read_captured(inputs, rel)).hexdigest() != pin:
                    raise ValueError('captured rule-area dependency changed during generation: ' + rel)
            verify_native_retention(pcbnew.LoadBoard(str(staged)), retention['items'], inputs,
                                    copper_layers=record['copper_layers'])
        if read_captured(inputs, board_rel) != source:
            raise ValueError('scaffold board changed during content generation')
        os.replace(staged, target)


def rule_area(parent, declaration, polygon, native, uid):
    """A native rule area, with only the envelope's declared restrictions."""
    import pcbnew
    zone = pcbnew.ZONE(parent)
    zone.SetUuidDirect(uid)
    zone.SetZoneName(declaration['id'])
    zone.SetIsRuleArea(True)
    layers = pcbnew.LSET()
    for layer in declaration['layers']:
        layers.AddLayer(native.GetLayerID(layer))
    zone.SetLayerSet(layers)
    for key, setter in (('tracks', zone.SetDoNotAllowTracks), ('vias', zone.SetDoNotAllowVias),
                        ('pads', zone.SetDoNotAllowPads), ('footprints', zone.SetDoNotAllowFootprints),
                        ('copper_pours', zone.SetDoNotAllowZoneFills)):
        setter(key in declaration['forbid'])
    zone.Outline().NewOutline()
    for x, y in polygon:
        zone.Outline().Append(x, y)
    return zone


def content_step(source_root, envelope, parts, board, *, intent=None, layout=None,
                 imu_reference=None, antenna_facts=None, captured_rule_areas=None):
    """ee139 pre-receipt declaration; the board is the sole in-place write."""
    for path in (envelope, parts, board, intent, layout, antenna_facts, captured_rule_areas):
        if path is not None and str(safe_relative(path)) != path:
            raise ValueError('board content paths must be canonical captured paths')
    argv = ['/usr/bin/python3', '-B', '-X', 'faulthandler', '-c',
            "import runpy,sys; sys.path.insert(0, '/tool/agent-ee'); "
            "runpy.run_module('agent_ee.scaffold', run_name='__main__')",
            '--inputs', '/work/design', '--envelope', envelope, '--parts', parts, '--board', board]
    for key, value in (('intent', intent), ('layout', layout), ('imu-reference', imu_reference),
                       ('antenna-facts', antenna_facts), ('captured-rule-areas', captured_rule_areas)):
        if value is not None:
            argv.extend(['--' + key, value])
    return dict(schema='agent-ee.stage-step/1', stage='scaffold', step='envelope-content', argv=argv,
                mounts=[dict(source=str(snapshot(Path(source_root).resolve() / 'src')), at='/tool/agent-ee')],
                source='/tool/agent-ee', writes={board: 'design/' + board}, timeout_s=300,
                **({'inputs': [captured_rule_areas]} if captured_rule_areas is not None else {}))


def library_tables(document, inputs, project):
    """Build project-local tables pointing exclusively at captured CAD members."""
    captures = {}
    for key in ('assignments', 'manifest'):
        declaration = document['cad'][key]
        raw = read_captured(inputs, declaration['path'])
        if hashlib.sha256(raw).hexdigest() != declaration['sha256']:
            raise ValueError('captured CAD ' + key + ' hash mismatch')
        captures[key] = strict_json(raw)
    assignments, manifest = captures['assignments'], captures['manifest']
    libraries = {'sym-lib-table': [(item['name'], item['path']) for item in assignments['libraries']]}
    footprints, identities = {}, set()
    for name, member in _members(manifest):
        path = PurePosixPath(member['path'])
        fpid = name + ':' + path.stem
        if fpid in identities:
            raise ValueError('duplicate captured footprint identity: ' + fpid)
        identities.add(fpid)
        raw = read_captured(inputs, member['path'])
        if hashlib.sha256(raw).hexdigest() != member['sha256']:
            raise ValueError('captured footprint hash mismatch: ' + member['path'])
        footprints.setdefault(name, []).append((member, raw))
    result, rows, copies = {}, [], []
    copy_root = PurePosixPath(project) / 'captured-footprints'
    for name, members in sorted(footprints.items()):
        parents = {str(PurePosixPath(member['path']).parent) for member, _ in members}
        if len(parents) == 1:
            rows.append((name, next(iter(parents))))
            continue
        # Nicknames remain unchanged; only their generated directory is new.
        if name in ('.', '..') or '/' in name or '\\' in name:
            raise ValueError('split footprint library nickname is not a directory name: ' + name)
        directory = copy_root / (name + '.pretty')
        rows.append((name, str(directory)))
        for member, raw in members:
            path = str(directory / PurePosixPath(member['path']).name)
            if path in result:
                raise ValueError('generated footprint path collision: ' + path)
            result[path] = raw
            copies.append(dict(footprint=name + ':' + PurePosixPath(path).stem,
                               path=path, source=dict(member)))
    if copies:
        # Reserve the whole namespace: an unlisted captured member must not be
        # made loadable alongside the qualified copies by the generated table.
        target = inputs / copy_root
        if target.exists() or target.is_symlink() or any(
                p.is_symlink() or (p.exists() and not p.is_dir())
                for p in target.parents if p != inputs and inputs in p.parents):
            raise ValueError('generated footprint library namespace collides with captured inputs')
        result[str(copy_root / 'manifest.json')] = json_bytes(dict(
            schema='agent-ee.scaffold-library-copies/1',
            manifest=dict(document['cad']['manifest']), copies=copies))
    libraries['fp-lib-table'] = rows
    for kind, rows in libraries.items():
        node = [kind.replace('-', '_'), ['version', '7']]
        for name, path in sorted(rows):
            relative = os.path.relpath(path, project)
            node.append(['lib', ['name', Q(name)], ['type', Q('KiCad')],
                         ['uri', Q('${KIPRJMOD}/' + relative)], ['options', Q('')], ['descr', Q('')]])
        result[str(PurePosixPath(project) / kind)] = sexpr(node)
    return result


def captured_name(path, sources):
    matches = [rel for rel, source in sources.items() if Path(source) == Path(path).resolve()]
    if len(matches) != 1:
        raise ValueError('planning input must be captured exactly once by the supplied scope: ' + str(path))
    return matches[0]


def canonical(value, label):
    if not isinstance(value, str) or str(safe_relative(value)) != value:
        raise ValueError(f'producer plan {label} must be a canonical relative path')
    return value


def validate_plan(plan):
    """Exact producer plan fields; nothing the envelope or parts manifest defines."""
    if not isinstance(plan, dict) or plan.get('schema') != PLAN_SCHEMA:
        raise ValueError('producer plan must be ' + PLAN_SCHEMA)
    missing = sorted(set(PLAN_FIELDS) - plan.keys())
    unknown = sorted(plan.keys() - set(PLAN_FIELDS) - NET_PLAN_FIELDS - {'constraint_source'})
    if missing or unknown:
        raise ValueError(f'producer plan fields: missing {missing}, unknown {unknown}')
    if 'constraint_source' in plan:
        canonical(plan['constraint_source'], 'constraint_source')
    if NET_PLAN_FIELDS & plan.keys():
        if not NET_PLAN_FIELDS <= plan.keys():
            raise ValueError('producer plan net_constraints and netclass_qualification are required together')
        for field in sorted(NET_PLAN_FIELDS):
            canonical(plan[field], field)
    for field in ('name', 'scope'):
        if not isinstance(plan[field], str) or not plan[field].strip():
            raise ValueError(f'producer plan requires nonempty {field}')
    if (not isinstance(plan['limitations'], list) or
            any(not isinstance(item, str) or not item.strip() for item in plan['limitations'])):
        raise ValueError('producer plan limitations must be a list of nonempty strings')
    if (not isinstance(plan['inputs'], list) or not plan['inputs'] or
            any(not isinstance(item, dict) or set(item) != {'path', 'at'} for item in plan['inputs'])):
        raise ValueError('producer plan inputs must be a nonempty list of {path, at}')
    project = canonical(plan['project'], 'project')
    if PurePosixPath(project).suffix or not PurePosixPath(project).name.replace('-', '').replace('_', '').isalnum():
        raise ValueError('producer plan project is a basename path without suffix, e.g. project/design')
    if not canonical(plan['template'], 'template').endswith('.kicad_pro'):
        raise ValueError('producer plan template must be a captured .kicad_pro')
    canonical(plan['fab_profile'], 'fab_profile')
    native = plan['native_requirement']
    if (not isinstance(native, dict) or set(native) != {'id', 'statement', 'source'} or
            not isinstance(native['source'], dict) or set(native['source']) != {'path', 'locator'}):
        raise ValueError('native_requirement needs exactly id, statement and source {path, locator}')
    canonical(native['source']['path'], 'native_requirement source')
    for field in ('targets', 'reference_architectures'):
        if not isinstance(plan[field], list) or not plan[field]:
            raise ValueError(f'producer plan {field} must name captured sources')
        for item in plan[field]:
            canonical(item, field)
    limit = plan['finding_retry_limit']
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 100:
        raise ValueError('producer plan finding_retry_limit must be an integer from 2 to 100')
    return plan


def produced_envelope(plan, files, inputs, envelope_rel):
    """Validate the envelope with its referenced profile against the capture; refuse anything unresolved."""
    raw = read_captured(inputs, envelope_rel)
    envelope = board_envelope.load(raw)
    if isinstance(envelope, dict) and 'fab_profile' in envelope and envelope['fab_profile'] is None:
        raise ValueError('envelope is incomplete: fab_profile is null, so design rules cannot be derived')
    profile = read_captured(inputs, plan['fab_profile'])
    record = board_envelope.validate(raw, profile=profile, captured=(files, inputs))
    if record['unresolved']:
        raise ValueError('envelope is incomplete: unresolved ' + '; '.join(
            f"{item['predicate']} ({item['reason']})" for item in record['unresolved']))
    return record


def produced_rules(plan, files, inputs):
    """Map source-verified fab limits onto KiCad project rules and the Default class."""
    profile = strict_json(read_captured(inputs, plan['fab_profile']))
    compiled = compile_profile(profile, files, inputs)
    unverified = [f"{c['id']} ({c['reason']})" for c in compiled['constraints'] if c['execution'] != 'ready']
    if unverified:
        # The derived fab_profile check would stay unknown for these forever.
        raise ValueError('fab profile is incomplete: limits not source-verified: ' + '; '.join(unverified))
    ready = {(c['expected']['key'], c['expected']['tier']): c for c in compiled['constraints']
             if 'key' in c['expected']}
    joint = [c for c in compiled['constraints'] if 'all' in c['expected']]
    missing = [key for key in RULE_KEYS if (key, 'minimum') not in ready]
    if missing:
        raise ValueError('fab profile is incomplete: no minimum-tier limit for ' + ', '.join(missing))
    requirements = sorted({c['requirement'] for c in compiled['constraints']})
    if len(requirements) != 1:
        raise ValueError('fab profile limits must name exactly one requirement, got ' + ', '.join(requirements))
    locators = {limit['id']: f'/limits/{index}' for index, limit in enumerate(profile['limits'])}

    def cite(constraint, setting, value, key=None, **extra):
        return dict(setting=setting, value=value, key=key or constraint['expected']['key'],
                    tier=constraint['expected']['tier'], limit_id=constraint['id'],
                    locator=locators[constraint['id']], **extra)

    rules, netclass, targets = [], [], []
    for key in RULE_KEYS:
        minimum = ready[key, 'minimum']
        (setting, value), = minimum['project_rules'].items()
        rules.append(cite(minimum, 'board.design_settings.rules.' + setting, value))
        if key not in NETCLASS_FIELDS:
            continue
        field = 'net_settings.classes.Default.' + NETCLASS_FIELDS[key]
        preferred = ready.get((key, 'preferred'))
        if preferred is not None and preferred['expected']['value'] < value:
            raise ValueError(f'fab profile {key} preferred value is below its minimum')
        if key in DRC_ENFORCED or preferred is None:
            basis = ENFORCED_BASIS if key in DRC_ENFORCED else 'no preferred limit; ' + ROUTING_BASIS
            netclass.append(cite(minimum, field, value, basis=basis))
        else:
            # A preferred limit avoids a surcharge; DRC still grades the board minimum.
            netclass.append(cite(preferred, field, preferred['expected']['value'], basis=ROUTING_BASIS))
        if preferred is not None:
            applied = key not in DRC_ENFORCED
            targets.append(cite(preferred, field if applied else None, preferred['expected']['value'],
                                consumer='Default class routing default' if applied else UNCONSUMED_TARGET))
    values = {item['setting'].rsplit('.', 1)[1]: item['value'] for item in netclass}
    for rule in joint:
        # A Default via strictly under every term bound is surcharged; KiCad's native min
        # leaves a via at the bound outside the band, so raise the diameter to its bound.
        bounds = {t['key']: t['value'] for t in rule['expected']['all']}
        if all(values[NETCLASS_FIELDS[key]] < bound for key, bound in bounds.items()):
            field = 'net_settings.classes.Default.via_diameter'
            index = next(i for i, item in enumerate(netclass) if item['setting'] == field)
            bound = bounds['min_via_pad_mm']
            netclass[index] = cite(rule, field, bound, key='min_via_pad_mm', basis=JOINT_BASIS)
            targets.append(cite(rule, field, bound, key='min_via_pad_mm', consumer='Default class routing default'))
            values['via_diameter'] = bound
    minimum_values = {key: ready[key, 'minimum']['expected']['value'] for key in NETCLASS_FIELDS}
    if minimum_values['min_via_pad_mm'] <= minimum_values['min_via_drill_mm']:
        raise ValueError('fab profile minimum via pad must exceed via drill, independently of preferred values')
    if values['via_diameter'] <= values['via_drill']:
        raise ValueError('fab profile via pad must exceed via drill for the Default class')
    return compiled, requirements[0], rules, netclass, targets


def produced_project(inputs, template_rel, rules, netclass):
    """Apply derived rules to a native template that carries no other rule authority."""
    from .netclass_completeness import PROFILE, evaluate
    project = strict_json(read_captured(inputs, template_rel))
    absent = evaluate(project)
    if absent is None:
        raise ValueError('template net settings are not the qualified ' + PROFILE)
    if absent:
        raise ValueError('template Default net class lacks native fields: ' + ', '.join(absent[0]['keys']))
    settings = project['net_settings']
    if ([c['name'] for c in settings['classes']] != ['Default'] or settings.get('netclass_patterns') or
            settings.get('netclass_assignments')):
        raise ValueError('template net classes beyond Default are refused; supply captured net constraints instead')
    design = project.get('board', {})
    design = design.get('design_settings', {}) if isinstance(design, dict) else None
    severities = design.get('rule_severities', {}) if isinstance(design, dict) else None
    if not isinstance(severities, dict) or not isinstance(design.get('rules', {}), dict):
        raise ValueError('template board design settings must be JSON objects')
    silenced = sorted(kind for _, kind, _ in NATIVE.values() if severities.get(kind) == 'ignore')
    if silenced or design.get('drc_exclusions'):
        raise ValueError('template must not ignore derived rule severities or exclude DRC findings: '
                         + ', '.join(silenced or ['drc_exclusions']))
    target = project.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    for item in rules:
        target[item['setting'].rsplit('.', 1)[1]] = item['value']
    default = settings['classes'][0]
    for item in netclass:
        default[item['setting'].rsplit('.', 1)[1]] = item['value']
    return project


def produced_netclasses(plan, inputs, envelope_rel, project, rules):
    """Reuse the net-constraint emitter, then enforce the captured fabrication floors."""
    from .net_constraints import emit, parse_document
    raw = read_captured(inputs, plan['net_constraints'])
    # Check declarations before the emitter rounds widths up to its 0.001 mm grid.
    _, declared = parse_document(raw)
    minima = {r['key']: r['value'] for r in rules}
    for spec in declared:
        for field, key in (('track_width_mm', 'min_trace_mm'), ('clearance_mm', 'min_clearance_mm')):
            if field in spec and spec[field] < minima[key]:
                raise ValueError(f"net class {spec['name']} {field} is below fab minimum {key}")
        for field in ('gap_mm', 'via_gap_mm'):
            if field in spec.get('diff_pair', {}) and spec['diff_pair'][field] < minima['min_clearance_mm']:
                raise ValueError(f"net class {spec['name']} {field} is below fab minimum min_clearance_mm")
    artifacts = emit(raw, json_bytes(project), envelope_raw=read_captured(inputs, envelope_rel))
    report = strict_json(artifacts['record'])
    if report['outcome'] != 'pass':
        raise ValueError('net constraints unresolved: ' + str(report['findings']))
    project = strict_json(artifacts['project'])
    for cls in project['net_settings']['classes']:
        for key, field in NETCLASS_FIELDS.items():
            if cls[field] < minima[key]:
                raise ValueError(f"net class {cls['name']} {field} is below fab minimum {key}")
        if cls['via_diameter'] <= cls['via_drill']:
            raise ValueError(f"net class {cls['name']} via pad must exceed via drill")
    return project, artifacts, report


def produce(plan, files, inputs, envelope_rel, parts_rel, document, qualification, sch, pcb):
    """Derive the scope/2 checks, mappings and project rules; refuse incomplete inputs by name."""
    record = produced_envelope(plan, files, inputs, envelope_rel)
    if qualification['missing_provenance']:
        raise ValueError('parts manifest is incomplete: required datasheet provenance unavailable for ' +
                         ', '.join(item['part_id'] for item in qualification['missing_provenance']))
    references = {s['reference'] for part in document['parts'] for s in part['instances']}
    stray = sorted(item['reference'] for item in record['fixed_footprints'] if item['reference'] not in references)
    if stray:
        raise ValueError('envelope fixed footprints are not declared parts instances: ' + ', '.join(stray))
    compiled, fab_requirement, rules, netclass, targets = produced_rules(plan, files, inputs)
    project = produced_project(inputs, plan['template'], rules, netclass)
    net_artifacts, net_report = {}, None
    if 'net_constraints' in plan:
        project, net_artifacts, net_report = produced_netclasses(plan, inputs, envelope_rel, project, rules)
    native = plan['native_requirement']
    ids = [native['id'], PARTS_REQUIREMENT, fab_requirement]
    if len(set(ids)) != len(ids):
        raise ValueError('derived requirement IDs collide: ' + ', '.join(ids))
    declaration = document['declaration_source']
    profile_rel = plan['fab_profile']
    requirements = [
        dict(id=native['id'], statement=native['statement'], source=dict(native['source']), checks=['erc', 'drc']),
        dict(id=PARTS_REQUIREMENT, statement=f'Every populated native instance matches the approved parts '
             f'manifest {parts_rel}, and every required selector is present.',
             source=dict(path=declaration['path'], locator=declaration['locator']), checks=['netlist', 'parts']),
        dict(id=fab_requirement, statement=f"The saved board satisfies every source-verified minimum limit "
             f"of fabricator profile {compiled['name']}.",
             source=dict(path=profile_rel, locator='/limits'), checks=['fab'])]
    specs = dict(erc=dict(id='erc', kind='erc'), netlist=dict(id='netlist', kind='netlist'),
                 parts=dict(id='parts', kind='parts_subset', params=dict(
                     manifest=parts_rel, netlist_check='netlist', requirement_id=PARTS_REQUIREMENT)),
                 drc=dict(id='drc', kind='drc'), fab=dict(id='fab', kind='fab_profile',
                                                         params=dict(fab_profile=profile_rel)))
    if net_report is not None:
        specs['netclasses'] = dict(id='netclasses', kind='netclass_completeness', params=dict(
            project=sch.removesuffix('.kicad_sch') + '.kicad_pro', qualification=plan['netclass_qualification']))
        requirements[0]['checks'].append('netclasses')
    required = [s for part in document['parts'] if part['required'] for s in part['instances']]
    hierarchical = sorted(s['reference'] for s in required if s['sheet'] != '/')
    stages = None
    if hierarchical:
        checks = [specs[cid] for cid, _ in STAGES]
        staging = dict(status='not_derived', reason='the staged presence checker refuses hierarchy; '
                       'required selectors on child sheets: ' + ', '.join(hierarchical))
    else:
        # Expected inventory is the manifest's explicit required selectors, never inferred.
        references = sorted(s['reference'] for s in required)
        checks = [dict(specs[cid], stage=stage) for cid, stage in STAGES]
        stages = dict(schema='agent-ee.stages/1', order=['schematic', 'placement', 'routing'],
                      subjects=dict(schematic=dict(path=sch, kind='symbols', references=references),
                                    placement=dict(path=pcb, kind='footprints', references=references),
                                    routing=dict(path=pcb, kind='tracks', minimum=1)),
                      finding_retry_limit=plan['finding_retry_limit'])
        staging = dict(status='derived', source=parts_rel + ' required selectors',
                       routing='fab_profile needs a saved track for its native rule-loading witness')
    if net_report is not None:
        check = specs['netclasses'] if stages is None else dict(specs['netclasses'], stage='placement')
        checks.insert(len(checks) - 2, check)
    scope = dict(schema='agent-ee.scope/2', name=plan['name'], scope=plan['scope'],
                 limitations=[*plan['limitations'], *PRODUCER_LIMITATIONS], inputs=plan['inputs'],
                 schematic=sch, board=pcb, mutable_inputs=[], checks=checks,
                 requirements=sorted({r['source']['path'] for r in requirements}),
                 baseline=dict(requirements=requirements, targets=plan['targets'],
                               reference_architectures=plan['reference_architectures']))
    if stages is not None:
        scope['stages'] = stages
    derivation = dict(
        envelope=dict(path=envelope_rel, sha256=record['envelope_sha256'], layer_count=record['layer_count'],
                      copper_layers=record['copper_layers'], profile_support=record['fab_profile']['support']),
        fab_profile=dict(path=profile_rel, sha256=files[profile_rel], name=compiled['name'],
                         as_of=compiled['as_of'], requirement=fab_requirement),
        template=dict(path=plan['template'], sha256=files[plan['template']]),
        parts=dict(path=parts_rel, sha256=files[parts_rel], declaration_source=declaration),
        rules=rules, netclasses=netclass, router_targets=targets, stages=staging,
        checks=[dict(id='erc', kind='erc', source='plan native_requirement'),
                dict(id='netlist', kind='netlist', source=parts_rel),
                dict(id='parts', kind='parts_subset', source=parts_rel + ' declaration_source'),
                dict(id='drc', kind='drc', source='plan native_requirement; project rules from ' + profile_rel),
                dict(id='fab', kind='fab_profile', source=envelope_rel + ' /fab_profile')],
        not_derived=['KiCad rule settings outside the six fab-profile keys keep the template values.',
                     'fab_package done gate and envelope obligation coverage.'])
    protected = {profile_rel, plan['template'], native['source']['path'], *compiled['immutable_inputs']}
    if net_report is not None:
        protected.update(plan[field] for field in NET_PLAN_FIELDS)
        derivation['net_constraints'] = dict(path=plan['net_constraints'], sha256=files[plan['net_constraints']],
                                            classes=net_report['classes'], nets=net_report['nets'])
        derivation['checks'].append(dict(id='netclasses', kind='netclass_completeness',
                                         source=plan['netclass_qualification']))
    else:
        derivation['not_derived'].append('Per-net classes: no net_constraints capture supplied.')
    return record, scope, project, derivation, protected, net_artifacts


def scaffold(envelope_path, parts_path, out, *, scope_path=None, plan_path=None, constraint_source=None):
    """Copy explicit inputs, replace native design with emptiness, validate, publish.

    Exactly one of scope_path (stage one) or plan_path (producer) is required.
    Stage one preserves the caller's requirement/check mappings and project
    rules. The producer derives them from the envelope, its referenced fab
    profile and the parts manifest, and refuses rather than inventing anything
    those records do not state.
    """
    from .baseline import compile_baseline
    from .cli import policy_files, validate_scope
    from .stages import declaration

    if (scope_path is None) == (plan_path is None):
        raise ValueError('scaffold requires exactly one of a stage-one scope or a producer plan')
    planning_path, out = Path(scope_path or plan_path).resolve(), Path(out).absolute()
    scope_raw = planning_path.read_bytes()
    scope = strict_json(scope_raw)
    if plan_path is not None:
        validate_plan(scope)
        sch, pcb = scope['project'] + '.kicad_sch', scope['project'] + '.kicad_pcb'
    elif scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('scaffold requires a stage-one scope/2 or scope/3')
    else:
        validate_scope(scope)
    files, sources, _ = source_manifest(scope, planning_path.parent)
    envelope_rel = captured_name(envelope_path, sources)
    parts_rel = captured_name(parts_path, sources)
    constraint_rel = captured_name(constraint_source, sources) if constraint_source is not None else None
    if plan_path is not None and 'constraint_source' in scope:
        if constraint_rel is not None and constraint_rel != scope['constraint_source']:
            raise ValueError('conflicting declared constraint sources')
        constraint_rel = scope['constraint_source']
        if constraint_rel not in files:
            raise ValueError('producer plan constraint_source is not captured: ' + constraint_rel)
    if plan_path is None:
        sch, pcb = (str(safe_relative(scope[key])) for key in ('schematic', 'board'))
        if (PurePosixPath(sch).suffix != '.kicad_sch' or PurePosixPath(pcb).suffix != '.kicad_pcb' or
                PurePosixPath(sch).with_suffix('') != PurePosixPath(pcb).with_suffix('')):
            raise ValueError('schematic and board must share a project basename')
        if sch != scope['schematic'] or pcb != scope['board']:
            raise ValueError('canonical native design paths required')
    pro = str(PurePosixPath(sch).with_suffix('.kicad_pro'))
    if plan_path is None and pro not in files:
        raise ValueError('capture a project template with stage-one design rules and net classes')
    if plan_path is not None:
        for rel in (pro, str(PurePosixPath(sch).with_suffix('.kicad_dru'))):
            if rel in files and not (rel.endswith('.kicad_dru') and constraint_rel is not None):
                raise ValueError('the producer derives project rules; refusing captured ' + rel)
        for field in ('template', 'fab_profile', *sorted(NET_PLAN_FIELDS & scope.keys())):
            if scope[field] not in files:
                raise ValueError(f'producer plan {field} is not captured: {scope[field]}')
    if out.exists() or out.is_symlink():
        raise ValueError('scaffold output must not exist')
    out = out.resolve()
    for item in scope['inputs']:
        source = Path(item['path']).expanduser()
        source = (planning_path.parent / source).resolve() if not source.is_absolute() else source.resolve()
        if source.is_dir() and (out == source or source in out.parents):
            raise ValueError('scaffold output must be outside every source input directory')
    out.mkdir(parents=True)
    try:
        inputs = out / 'inputs'
        for rel, source in sources.items():
            target = inputs / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if digest(target) != files[rel]:
                raise ValueError('input changed during scaffolding: ' + rel)
        plan = scope if plan_path is not None else None
        if plan is None:
            record = board_envelope.validate(read_captured(inputs, envelope_rel))
        document = load_manifest(inputs, parts_rel)
        qualification = qualify_manifest(document, inputs)
        protected = set(qualification['dependencies']) | {parts_rel, envelope_rel}
        if plan is None:
            project, derivation = strict_json(read_captured(inputs, pro)), None
            net_artifacts = {}
        else:
            record, scope, project, derivation, extra, net_artifacts = produce(
                plan, files, inputs, envelope_rel, parts_rel, document, qualification, sch, pcb)
            validate_scope(scope)
            protected |= extra
        from .design_rule_provenance import (import_constraints, inventory, bind_project,
                                             require_owner_rule_classes, rule_authority)
        constraint_record = {'settings': inventory(project)}
        if constraint_rel is not None:
            project, constraint_record = import_constraints(
                project, read_captured(inputs, constraint_rel), constraint_rel, files[constraint_rel])
            protected.add(constraint_rel)
            owner_rules = str(PurePosixPath(pcb).with_suffix('.kicad_dru'))
            if owner_rules in files:
                protected.add(owner_rules)
                scope['requirements'] = sorted({*scope.get('requirements', []), owner_rules})
            scope['requirements'] = sorted({*scope.get('requirements', []), constraint_rel})
        if derivation is not None:
            derived_settings = []
            for emitted in constraint_record['settings']:
                for item in [*derivation['rules'], *derivation['netclasses']]:
                    if (emitted['setting'] == item['setting'] and emitted['value'] == item['value']
                            and emitted['citation'] is None):
                        emitted.pop('unsourced_reason', None)
                        emitted.update({'class': 'manufacturer', 'enforcement': 'hard',
                            'citation': dict(path=plan['fab_profile'], sha256=files[plan['fab_profile']],
                                             locator=item['locator'], basis='Source-verified fabricator limit')})
                        derived_settings.append(copy.deepcopy(emitted))
            constraint_record['derived_settings'] = derived_settings
        from .net_constraints import rule_forms, template_rules
        owner_rules = str(PurePosixPath(pcb).with_suffix('.kicad_dru'))
        composed_rules = None
        owner_copy = None
        if 'rules' in net_artifacts:
            generated_forms = {str(n[1]): n for n in rule_forms(net_artifacts['rules']) if n[0] == 'rule'}
            generated_names = list(generated_forms)
            declared = {spec['rule']: spec for spec in
                        constraint_record.get('document', {}).get('custom_rules', {}).get(
                            PurePosixPath(owner_rules).name, [])}
            constraint_record['generated_rules'] = {}
            records = constraint_record.get('document', {}).get('source_records', {})
            for name in generated_names:
                spec = declared.get(name)
                source = {'class': spec['class'] if spec else 'default', 'citation': None}
                # H3: qualify the emitted rule's actual numeric payload, not the empty declaration.
                resolved = (rule_authority(spec, generated_forms[name], records)
                            if spec and spec['class'] != 'default' else None)
                if spec and spec['class'] == 'default':
                    # Located but never hard: the default class carries no authority.
                    source['citation'] = dict(path=constraint_rel, sha256=files[constraint_rel],
                                              rule=name, basis=spec['basis'])
                elif resolved and resolved['enforcement'] == 'hard':
                    source['citation'] = dict(path=constraint_rel, sha256=files[constraint_rel], rule=name,
                                              basis=spec['basis'], source=copy.deepcopy(spec['citation']))
                elif resolved:
                    # A label without a resolvable citation is disclosed, never hard (AGENTEE-348).
                    source['unsourced_reason'] = resolved['reason']
                constraint_record['generated_rules'][name] = source
            composed_rules = net_artifacts['rules']
            if owner_rules in files:
                raw = read_captured(inputs, owner_rules)
                template_rules(raw, allow_generated=True)
                owner_forms = rule_forms(raw, native=True)
                owner_names = {str(n[1]) for n in owner_forms[1:] if len(n) > 1 and n[0] == 'rule'}
                require_owner_rule_classes(constraint_record, PurePosixPath(owner_rules).name, owner_names)
                # An adopted owner rule keeps its authority when its name also
                # occurs in the generated prefix; native owner clauses stay last.
                # Preserve positional sources for geometry disclosure separately
                # from the existing name-based DRC authority.
                constraint_record['shadowed_generated_rules'] = [
                    dict(index=index, name=str(node[1]),
                         **constraint_record['generated_rules'][str(node[1])])
                    for index, node in enumerate(rule_forms(net_artifacts['rules']))
                    if node[0] == 'rule' and str(node[1]) in owner_names]
                for name in owner_names:
                    constraint_record['generated_rules'].pop(name, None)
                owner_copy = str(PurePosixPath(pcb).with_suffix('.owner.kicad_dru'))
                if owner_copy in files:
                    raise ValueError('owner rule preservation path already captured: ' + owner_copy)
                # Insert generated forms before the owner rules, preserving
                # native last-rule precedence and original owner bytes separately.
                generated_text = net_artifacts['rules'].split(b'\n', 1)[1]
                # Re-emission would lose owner quoting/comments; locate the first
                # version form with the native version grammar already validated.
                version = re.search(rb'(?m)^[ \t]*\(\s*version\s+1\s*\)', raw)
                if owner_forms:
                    if version is None:
                        raise ValueError('owner rule version form unavailable')
                    composed_rules = raw[:version.end()] + b'\n' + generated_text + raw[version.end():]
                else:
                    composed_rules = net_artifacts['rules'] + b'\n' + raw
                forms = rule_forms(composed_rules, native=True)
                if forms[0] != ['version', '1'] or any(n[0] == 'version' for n in forms[1:]):
                    raise ValueError('composed rules must have one leading native version')
                constraint_record['owner_rules'] = dict(path=owner_copy, sha256=files[owner_rules])
                protected.discard(owner_rules)
                scope['requirements'] = sorted({*scope.get('requirements', []), owner_copy, owner_rules})
        if owner_rules in files and owner_copy is None:
            owner_names = {str(n[1]) for n in rule_forms(read_captured(inputs, owner_rules), native=True)
                           if len(n) > 1 and n[0] == 'rule'}
            require_owner_rule_classes(constraint_record, PurePosixPath(owner_rules).name, owner_names)
        plans = sheet_plan(document, sch)
        generated = {path for path, _ in plans.values()} | {pcb}
        if set(scope.get('mutable_inputs', [])) - generated:
            raise ValueError('scaffold mutable inputs may contain only planned native design files')
        if protected & (generated | {pro}):
            raise ValueError('native scaffold collides with immutable planning inputs')
        seed = identity({'envelope': files[envelope_rel], 'parts': files[parts_rel]})
        root = str(uuid.uuid5(NAMESPACE, seed + ':root'))
        outputs = native_files(record, sch, project, root, seed, plans)
        # Bind after the shared builder adds final filename and sheet metadata:
        # the emission witness must describe emitted bytes, not the template.
        project = strict_json(outputs[pro])
        bind_project(project, constraint_record, composed_rules)
        outputs[pro] = json_bytes(project)
        if net_artifacts:
            record_rel = str(PurePosixPath(pro).with_suffix('.net-constraints.record.json'))
            report = strict_json(net_artifacts['record'])
            # The shared builder has now added the final sheet and filename metadata.
            report['project_sha256'] = hashlib.sha256(outputs[pro]).hexdigest()
            if composed_rules is not None:
                report['rules_sha256'] = hashlib.sha256(composed_rules).hexdigest()
            net_outputs = {record_rel: json_bytes(report)}
            if 'rules' in net_artifacts:
                net_outputs[str(PurePosixPath(pro).with_suffix('.kicad_dru'))] = composed_rules
            if net_outputs.keys() & (files.keys() - ({owner_rules} if owner_copy else set())):
                raise ValueError('derived net constraint artifacts collide with captured inputs')
            outputs.update(net_outputs)
            if owner_copy:
                outputs[owner_copy] = read_captured(inputs, owner_rules)
            derivation['net_constraints']['record'] = record_rel
            scope['requirements'] = sorted({*scope['requirements'], plan['net_constraints'], *net_outputs})
        library_outputs = library_tables(document, inputs, str(PurePosixPath(sch).parent))
        if library_outputs.keys() & (outputs.keys() | files.keys()):
            raise ValueError('generated library artifacts collide with captured or native outputs')
        outputs.update(library_outputs)
        if protected & outputs.keys():
            raise ValueError('scaffold library tables collide with immutable planning inputs')
        for rel, data in outputs.items():
            target = inputs / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        scope['inputs'] = [dict(path='inputs/' + name, at=name)
                           for name in sorted(p.name for p in inputs.iterdir())]
        scope['mutable_inputs'] = sorted(generated)
        (out / 'scope.json').write_bytes(json_bytes(scope))
        captured, _, _ = source_manifest(scope, out)
        declaration(scope, captured)
        baseline = compile_baseline(scope, captured, inputs)
        if generated & baseline['files'].keys():
            raise ValueError('native scaffold is an immutable verification dependency')
        after, _, _ = source_manifest(strict_json(scope_raw), planning_path.parent)
        if after != files or planning_path.read_bytes() != scope_raw:
            raise ValueError('source or scope changed during scaffolding')
        planning_key = 'scope_sha256' if plan is None else 'plan_sha256'
        receipt = dict(schema=SCHEMA if plan is None else PRODUCED_SCHEMA, status='completed',
            candidate_id=identity(dict(scope=scope, files=captured)), baseline_id=baseline['baseline_id'],
            root_sheet_uuid=root, sheets={key: path for key, (path, _) in plans.items()},
            inputs={planning_key: hashlib.sha256(scope_raw).hexdigest(), 'files': files},
            outputs={**{'inputs/' + rel: sha for rel, sha in captured.items()},
                     'scope.json': digest(out / 'scope.json')},
            tool=dict(module='agent_ee.scaffold', python=sys.version.split()[0],
                      policy_files=policy_files()),
            limitations=['Scaffold receipt is provenance, not native execution or design acceptance.',
                         'Stage-one scope owns requirement mappings, project rules and envelope coverage.'
                         if plan is None else
                         'Derived checks, mappings and project rules cite their captured sources in derivation; '
                         'envelope obligation coverage is not derived.',
                         'Hierarchical staged presence remains unsupported by the existing stage checker.',
                         'Native numeric limits without a captured source are advisory; structural gates remain mandatory.'],
            missing_parts_provenance=qualification['missing_provenance'],
            constraint_provenance=constraint_record)
        if derivation is not None:
            receipt['derivation'] = derivation
        (out / 'scaffold-receipt.json').write_bytes(json_bytes(receipt))
        return receipt
    except BaseException:
        # Only this invocation created out; failed preparation publishes no candidate.
        shutil.rmtree(out)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write captured envelope content into an outline scaffold.')
    parser.add_argument('--inputs', required=True, type=Path)
    for name in ('envelope', 'parts', 'board'):
        parser.add_argument('--' + name, required=True)
    for name in ('intent', 'layout', 'imu-reference', 'antenna-facts', 'captured-rule-areas'):
        parser.add_argument('--' + name)
    args = parser.parse_args(argv)
    try:
        write_envelope_content(args.inputs, args.envelope, args.parts, args.board,
            intent_rel=args.intent, layout_rel=args.layout, imu_reference=args.imu_reference,
            antenna_facts_rel=args.antenna_facts, captured_rule_areas_rel=args.captured_rule_areas)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'envelope content refused: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
