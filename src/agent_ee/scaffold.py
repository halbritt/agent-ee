"""Create an empty native candidate from captured planning and verification inputs.

The caller supplies the stage-one scope and project rules. Scaffolding preserves
its baseline and checks; it cannot choose acceptance criteria or waive uncovered
board-envelope obligations. No native-tool or owner-approval claim is made here.
"""
import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import uuid

from . import board_envelope
from .candidate import safe_relative, source_manifest
from .catalog import strict_json
from .footprint_library import _members
from .parts_manifest import load_manifest, qualify_manifest, read_captured
from .schematic_emit import Quoted as Q, format_sexpr
from .storage import digest, identity, json_bytes

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/halbritt/agent-ee/scaffold/1')
SCHEMA = 'agent-ee.scaffold-receipt/1'


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
    """Emit only declared outline and stackup; project rules remain in .kicad_pro."""
    count = record['layer_count']
    if count % 2:
        raise ValueError('native scaffold requires an even copper layer count')
    copper = record['copper_layers']
    layers = [['0', Q('F.Cu'), 'signal']]
    layers += [[str(4 + 2 * i), Q(name), 'signal'] for i, name in enumerate(copper[1:-1])]
    layers.append(['2', Q('B.Cu'), 'signal'])
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


def library_tables(document, inputs, project):
    """Build project-local tables pointing exclusively at captured CAD members."""
    assignments = strict_json(read_captured(inputs, document['cad']['assignments']['path']))
    manifest = strict_json(read_captured(inputs, document['cad']['manifest']['path']))
    libraries = {'sym-lib-table': [(item['name'], item['path']) for item in assignments['libraries']]}
    footprints = {}
    for name, member in _members(manifest):
        parent = str(PurePosixPath(member['path']).parent)
        if name in footprints and footprints[name] != parent:
            raise ValueError('one captured footprint library must have one directory')
        footprints[name] = parent
    libraries['fp-lib-table'] = list(footprints.items())
    result = {}
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


def scaffold(envelope_path, parts_path, out, *, scope_path):
    """Copy explicit inputs, replace native design with emptiness, validate, publish.

    scope_path is required: neither an envelope nor a parts manifest defines the
    stage-one requirement/check mappings, targets, reference architectures or
    qualified project rules. We preserve these rather than inventing them.
    """
    from .baseline import compile_baseline
    from .cli import policy_files, validate_scope
    from .stages import declaration

    scope_path, out = Path(scope_path).resolve(), Path(out).absolute()
    scope_raw = scope_path.read_bytes()
    scope = strict_json(scope_raw)
    if scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('scaffold requires a stage-one scope/2 or scope/3')
    validate_scope(scope)
    files, sources, _ = source_manifest(scope, scope_path.parent)
    envelope_rel = captured_name(envelope_path, sources)
    parts_rel = captured_name(parts_path, sources)
    sch, pcb = (str(safe_relative(scope[key])) for key in ('schematic', 'board'))
    if (PurePosixPath(sch).suffix != '.kicad_sch' or PurePosixPath(pcb).suffix != '.kicad_pcb' or
            PurePosixPath(sch).with_suffix('') != PurePosixPath(pcb).with_suffix('')):
        raise ValueError('schematic and board must share a project basename')
    if sch != scope['schematic'] or pcb != scope['board']:
        raise ValueError('canonical native design paths required')
    pro = str(PurePosixPath(sch).with_suffix('.kicad_pro'))
    if pro not in files:
        raise ValueError('capture a project template with stage-one design rules and net classes')
    if out.exists() or out.is_symlink():
        raise ValueError('scaffold output must not exist')
    out = out.resolve()
    for item in scope['inputs']:
        source = Path(item['path']).expanduser()
        source = (scope_path.parent / source).resolve() if not source.is_absolute() else source.resolve()
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
        record = board_envelope.validate(read_captured(inputs, envelope_rel))
        document = load_manifest(inputs, parts_rel)
        qualification = qualify_manifest(document, inputs)
        plans = sheet_plan(document, sch)
        generated = {path for path, _ in plans.values()} | {pcb}
        if set(scope.get('mutable_inputs', [])) - generated:
            raise ValueError('scaffold mutable inputs may contain only planned native design files')
        protected = set(qualification['dependencies']) | {parts_rel, envelope_rel}
        if protected & (generated | {pro}):
            raise ValueError('native scaffold collides with immutable planning inputs')
        seed = identity({'envelope': files[envelope_rel], 'parts': files[parts_rel]})
        root = str(uuid.uuid5(NAMESPACE, seed + ':root'))
        project_name = PurePosixPath(sch).stem
        outputs = {path: schematic(root, key, plans, project_name)
                   for key, (path, _) in plans.items()}
        outputs[pcb] = board(record, seed)
        project = strict_json(read_captured(inputs, pro))
        project.setdefault('meta', {})['filename'] = PurePosixPath(pro).name
        project['sheets'] = [[root, 'Root']] + [
            [key.strip('/').split('/')[-1], PurePosixPath(path).stem]
            for key, (path, _) in plans.items() if key != '/']
        project.setdefault('schematic', {})['top_level_sheets'] = [
            dict(filename=PurePosixPath(sch).name, name=project_name, uuid=root)]
        outputs[pro] = json_bytes(project)
        outputs.update(library_tables(document, inputs, str(PurePosixPath(sch).parent)))
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
        after, _, _ = source_manifest(strict_json(scope_raw), scope_path.parent)
        if after != files or scope_path.read_bytes() != scope_raw:
            raise ValueError('source or scope changed during scaffolding')
        receipt = dict(schema=SCHEMA, status='completed',
            candidate_id=identity(dict(scope=scope, files=captured)), baseline_id=baseline['baseline_id'],
            root_sheet_uuid=root, sheets={key: path for key, (path, _) in plans.items()},
            inputs=dict(scope_sha256=hashlib.sha256(scope_raw).hexdigest(), files=files),
            outputs={**{'inputs/' + rel: sha for rel, sha in captured.items()},
                     'scope.json': digest(out / 'scope.json')},
            tool=dict(module='agent_ee.scaffold', python=sys.version.split()[0],
                      policy_files=policy_files()),
            limitations=['Scaffold receipt is provenance, not native execution or design acceptance.',
                         'Stage-one scope owns requirement mappings, project rules and envelope coverage.',
                         'Hierarchical staged presence remains unsupported by the existing stage checker.'],
            missing_parts_provenance=qualification['missing_provenance'])
        (out / 'scaffold-receipt.json').write_bytes(json_bytes(receipt))
        return receipt
    except BaseException:
        # Only this invocation created out; failed preparation publishes no candidate.
        shutil.rmtree(out)
        raise
