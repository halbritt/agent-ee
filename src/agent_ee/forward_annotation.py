"""Forward annotation: push a native netlist into an existing placed board (AGENTEE-52).

The complete component inventory comes from the actual native KiCad XML export,
read by the AGENTEE-41 adapter (``agent_ee.native_netlist``). Footprints bind
to components only through their native sheet path and symbol UUID (any unit
of a multi-unit symbol), never by reference. Every footprint must be an exact
member of the captured footprint manifest (AGENTEE-36 index) with a pad set
equal to the component's pins. ``plan`` reports adds, removals, field updates,
in-place net renames, new nets, pad net changes and stale copper nets;
``apply`` recomputes the plan from the same inputs, refuses any difference,
runs pcbnew in a sandboxed child process on a copy and verifies the saved
result before publishing it. Nothing here places or routes; native DRC with
schematic parity and the coordinator's checks judge the result.
"""
import argparse
import errno
import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import board_net_names, native_netlist
from .footprint_library import FootprintError, index_footprints, load_document

PLAN_SCHEMA = 'agent-ee.forward-annotation-plan/1'
RESULT_SCHEMA = 'agent-ee.forward-annotation-result/1'
STAGING_GAP = 5080000      # Clearance right of the board outline (nm).
STAGING_GRID = 1270000     # Staged anchors snap to this grid (nm).
STAGING_MARGIN = 2540000   # Space between staged footprints (nm).


class AnnotationError(ValueError):
    def __init__(self, code, message, location='/'):
        super().__init__(message)
        self.code, self.location = code, location


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _seal(record):
    record.pop('canonical_sha256', None)
    record['canonical_sha256'] = hashlib.sha256(_canonical(record)).hexdigest()
    return record


def _natural(text):
    head = text.rstrip('0123456789')
    tail = text[len(head):]
    return (head, int(tail) if tail else -1, text)


def _snap(value):
    return -(-value // STAGING_GRID) * STAGING_GRID


def _inventory(xml_bytes):
    """Native inventory and parsed component elements from exact bytes.

    Both come from the AGENTEE-41 reader and its own bounded parser; the
    elements supply only the symbol fields that the reader does not expose.
    The board-name projection uses that parsed root; inventory names stay raw.
    """
    with tempfile.TemporaryDirectory(prefix='agent-ee-annotation-xml-') as tmp:
        path = Path(tmp) / 'netlist.xml'
        path.write_bytes(xml_bytes)
        try:
            inventory = native_netlist.read_native_inventory(path)
            root = native_netlist._parse(path)
            names = board_net_names.from_xml(root)
        except (ValueError, TypeError, UnicodeError, ET.ParseError) as exc:
            raise AnnotationError('native_netlist', str(exc)) from exc
    elements = {}
    for comp in root.findall('./components/comp'):
        elements[comp.get('ref')] = comp
    if set(elements) != set(inventory['components']) or len(root.findall('./components/comp')) != len(elements):
        raise AnnotationError('native_netlist', 'parsed components differ from the native inventory')
    return inventory, elements, names


def _fields(elements):
    """Symbol fields per reference from the reader's parsed component elements.

    The 41 adapter does not expose fields, which native schematic parity
    compares on every footprint.
    """
    fields = {}
    for ref, comp in elements.items():
        values = {}
        for field in comp.findall('./fields/field'):
            name = field.get('name')
            if not name or name in values:
                raise AnnotationError('native_netlist', 'ambiguous symbol field', '/components/' + str(ref))
            values[name] = field.text or ''
        values.pop('Footprint', None)  # Bound as the footprint identity itself.
        fields[ref] = values
    return fields


def build_plan(xml_bytes, board_sha256, facts, manifest, captured_footprints):
    """Plan from captured bytes; supported custom geometry requires a native child."""
    plan = dict(schema=PLAN_SCHEMA, execution='failed', outcome='unknown', findings=[],
                inputs=dict(board_sha256=board_sha256, xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
                            manifest_sha256=hashlib.sha256(_canonical(manifest)).hexdigest(),
                            facts_sha256=hashlib.sha256(_canonical(facts)).hexdigest(), footprints=[]),
                add=[], remove=[], update=[], retain=[], board_only=[], renames=[], new_nets=[], pads=[],
                stale_nets=[], counts={})
    try:
        inventory, elements, names = _inventory(xml_bytes)
        from .probe_features import PROPERTY, parse_marker, native_component, footprint_signature
        from .symbol_library import parse_sexpr
        for ref, fields in _fields(elements).items():
            if PROPERTY in fields:
                record = parse_marker(fields[PROPERTY])
                try:
                    native_component(record, inventory['components'][ref], inventory, allow_legacy=True)
                    data = captured_footprints[record['footprint_source']['path']]
                    if hashlib.sha256(data).hexdigest() != record['footprint_source']['sha256']:
                        raise ValueError('probe captured footprint bytes changed')
                    if footprint_signature(parse_sexpr(data), record['pin']['number']) != record['geometry']:
                        raise ValueError('probe captured pad geometry changed')
                except (ValueError, KeyError) as exc:
                    raise AnnotationError('probe_feature_mismatch', str(exc), '/components/' + ref) from exc
        for ref in sorted(inventory['components'], key=_natural):
            # The reader's parsed flag: KiCad 10.0.7 exports on_board no as a
            # valueless exclude_from_board property, whatever its byte spelling.
            if not inventory['components'][ref]['on_board']:
                raise AnnotationError('board_exclusion_unsupported',
                                      'native export excludes this symbol from the board', '/components/' + ref)
        index = index_footprints(manifest, captured_footprints)
        if index['coverage'] != 'complete':
            raise AnnotationError('footprint_library_coverage',
                                  '; '.join(f.get('message', f['code']) for f in index['findings']))
        library = {fp['lib_id']: fp for fp in index['footprints']}
        _plan(plan, inventory, _fields(elements), facts, library, names)
    except AnnotationError as exc:
        plan['findings'].append(dict(code=exc.code, location=exc.location, message=str(exc)))
        return _seal(plan)
    except FootprintError as exc:
        plan['findings'].append(dict(code='footprint_library_coverage', location='/', message=str(exc)))
        return _seal(plan)
    plan.update(execution='ok', outcome='info')
    plan['counts'] = {key: len(plan[key]) for key in ('add', 'remove', 'update', 'retain', 'board_only',
                                                      'renames', 'new_nets', 'stale_nets')}
    plan['counts']['pad_changes'] = sum(1 for p in plan['pads'] if p['previous'] != p['net'])
    return _seal(plan)


def _plan(plan, inventory, fields, facts, library, names):
    components = inventory['components']
    pad_net = {}
    for net in inventory['nets'].values():
        for ref, pin in net['terminals']:
            pad_net[ref, pin] = names[net['name']]
    netlist_names = {names[net['name']] for net in inventory['nets'].values()}
    # Native identity: sheet path plus any unit UUID of the symbol.
    owner = {}
    for ref, comp in components.items():
        location = '/components/' + ref
        if not comp['populated']:
            raise AnnotationError('dnp_unsupported', 'do-not-populate symbols are not qualified', location)
        if not comp['footprint']:
            raise AnnotationError('missing_footprint', 'symbol has no footprint assignment', location)
        fp = library.get(comp['footprint'])
        if fp is None:
            raise AnnotationError('missing_footprint', 'footprint is not an exact captured manifest member', location)
        if sorted(fp['electrical_numbers']) != sorted(comp['pins']):
            raise AnnotationError('pad_pin_mismatch', 'footprint pads differ from the symbol pins', location)
        for unit_uuid in comp['uuid'].split():
            owner[comp['sheet'] + unit_uuid] = ref
    paths, bound = {}, {}
    for fp in facts['footprints']:
        if not fp['path']:
            plan['board_only'].append(dict(footprint_uuid=fp['uuid'], reference=fp['reference']))
            continue
        if fp['path'] in paths:
            raise AnnotationError('duplicate_native_path', 'two board footprints share a native path',
                                  '/board/' + fp['path'])
        paths[fp['path']] = fp
        ref = owner.get(fp['path'])
        if ref is None:
            plan['remove'].append(dict(footprint_uuid=fp['uuid'], path=fp['path'], reference=fp['reference']))
            continue
        if ref in bound:
            raise AnnotationError('multiunit_binding_ambiguous', 'several footprints bind one symbol',
                                  '/components/' + ref)
        bound[ref] = fp
    retained = set(bound)
    old_sets = {}
    for ref, fp in bound.items():
        comp = components[ref]
        if fp['fpid'] != comp['footprint']:
            raise AnnotationError('footprint_change_unsupported', 'bound footprint identity differs from the symbol',
                                  '/components/' + ref)
        numbers = sorted({p['number'] for p in fp['pads'] if p['number']})
        if numbers != sorted(comp['pins']):
            raise AnnotationError('pad_pin_mismatch', 'placed footprint pads differ from the symbol pins',
                                  '/components/' + ref)
        plan['retain'].append(dict(footprint_uuid=fp['uuid'], path=fp['path'], reference=ref))
        if (fp['reference'], fp['value']) != (ref, comp['value']) or \
                any(fp['fields'].get(k) != v for k, v in fields[ref].items()):
            plan['update'].append(dict(footprint_uuid=fp['uuid'], path=fp['path'], reference=ref,
                                       value=comp['value'], fields=fields[ref]))
        for pad in fp['pads']:
            if pad['net'] and pad['number']:
                old_sets.setdefault(pad['net'], set()).add((ref, pad['number']))
    board_names = set(old_sets) | {x['net'] for x in facts['tracks'] + facts['zones'] if x['net']} | \
        {p['net'] for fp in facts['footprints'] for p in fp['pads'] if p['net']}
    new_sets = {}
    for (ref, pin), name in pad_net.items():
        if ref in retained:
            new_sets.setdefault(name, set()).add((ref, pin))
    # In-place rename only for an exact one-to-one pad membership correspondence.
    for old, members in sorted(old_sets.items()):
        if old in netlist_names:
            continue
        targets = [new for new, terms in new_sets.items() if terms == members]
        if len(targets) == 1 and targets[0] not in board_names and \
                sum(1 for terms in old_sets.values() if terms == members) == 1:
            plan['renames'].append({'from': old, 'to': targets[0]})
    renamed = {r['from']: r['to'] for r in plan['renames']}
    present = {renamed.get(name, name) for name in board_names}
    plan['new_nets'] = sorted(netlist_names - present)
    for item in facts['tracks'] + facts['zones']:
        name = renamed.get(item['net'], item['net'])
        if name and name not in netlist_names:
            stale = next((s for s in plan['stale_nets'] if s['name'] == name), None)
            if stale is None:
                stale = dict(name=name, items=[])
                plan['stale_nets'].append(stale)
            stale['items'].append(item['uuid'])
    plan['stale_nets'].sort(key=lambda s: s['name'])
    # Deterministic staging column right of the outline, ordered by reference.
    left, top, right, _ = facts['edge_bbox_nm']
    x, y = _snap(right + STAGING_GAP), _snap(top)
    for ref in sorted(set(components) - retained, key=_natural):
        comp = components[ref]
        fp = library[comp['footprint']]
        extents = [(p['native_geometry']['bbox_nm'][1], p['native_geometry']['bbox_nm'][3])
                   if p.get('geometry') == 'native_custom' else
                   (p['y_nm'] - p['size_nm'][1] // 2, p['y_nm'] + p['size_nm'][1] // 2) for p in fp['pads']]
        low, high = min(e[0] for e in extents), max(e[1] for e in extents)
        anchor_y = _snap(y - low)
        library_name, name = comp['footprint'].split(':')
        plan['add'].append(dict(reference=ref, value=comp['value'], fpid=comp['footprint'], library=library_name,
                                name=name, path=comp['sheet'] + comp['uuid'].split()[0], fields=fields[ref],
                                position_nm=[x, anchor_y], member=fp['source']))
        plan['inputs']['footprints'].append(fp['source'])
        y = anchor_y + high + STAGING_MARGIN
    for ref in retained:
        plan['inputs']['footprints'].append(library[components[ref]['footprint']]['source'])
    plan['inputs']['footprints'] = sorted({json.dumps(s, sort_keys=True) for s in plan['inputs']['footprints']})
    plan['inputs']['footprints'] = [json.loads(s) for s in plan['inputs']['footprints']]
    previous = {(ref, p['number']): renamed.get(p['net'], p['net'])
                for ref, fp in bound.items() for p in fp['pads'] if p['number']}
    for ref in sorted(components, key=_natural):
        path = bound[ref]['path'] if ref in bound else next(a['path'] for a in plan['add'] if a['reference'] == ref)
        for pin in sorted(components[ref]['pins'], key=_natural):
            plan['pads'].append(dict(path=path, reference=ref, pad=pin, net=pad_net[ref, pin],
                                     previous=previous.get((ref, pin))))
    for key in ('remove', 'update', 'retain'):
        plan[key].sort(key=lambda x: (x['path'], x['footprint_uuid']))
    plan['board_only'].sort(key=lambda x: x['footprint_uuid'])


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _captures(manifest, root):
    from .footprint_library import read_captures
    paths = [m['path'] for lib in manifest.get('libraries', []) if isinstance(lib, dict)
             for m in lib.get('members', []) if isinstance(m, dict) and isinstance(m.get('path'), str)]
    return read_captures(root, paths)


def _child(inputs, work, argv, timeout):
    from .executor import execute
    record = execute(inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.forward_annotation_probe', *argv],
                     timeout=timeout)
    if record['execution'] != 'ok':
        console = (Path(work) / 'console.log').read_text(errors='replace')[-2000:]
        raise AnnotationError('native_child', f'pcbnew child {record["execution"]} '
                              f'(exit {record["exit_code"]}): {console}')
    return record


def board_facts(board, work, timeout=300):
    """Facts from a private read-only copy of the board, through the sandboxed child."""
    work = Path(work)
    inputs = work / 'facts-input'
    inputs.mkdir(parents=True)
    shutil.copyfile(board, inputs / 'board.kicad_pcb')
    if _sha256(inputs / 'board.kicad_pcb') != _sha256(board):
        raise AnnotationError('stale_input', 'board changed while it was copied')
    _child(inputs, work / 'facts', ['facts', '/candidate/board.kicad_pcb', '-o', '/work/facts.json'], timeout)
    return json.loads((work / 'facts' / 'facts.json').read_text())


def _failed(schema, code, message, location='/', **fields):
    return _seal(dict(schema=schema, execution='failed', outcome='unknown', **fields,
                      findings=[dict(code=code, location=location, message=message)]))


def plan(board, xml, manifest, footprint_root, work):
    """Plan for exact current bytes; ``work`` must be a new private directory."""
    work = Path(work)
    work.mkdir(mode=0o700, parents=False, exist_ok=False)
    board_sha = _sha256(board)
    xml_bytes = Path(xml).read_bytes()
    try:
        captures = _captures(manifest, footprint_root)
    except (OSError, ValueError, TypeError) as exc:
        # An unreadable or escaping manifest member is missing coverage.
        return _failed(PLAN_SCHEMA, 'footprint_library_coverage', str(exc))
    try:
        from .probe_features import PROPERTY, parse_marker, validate_board
        _, elements, _ = _inventory(xml_bytes)
        probes = {ref: parse_marker(fields[PROPERTY]) for ref, fields in _fields(elements).items()
                  if PROPERTY in fields}
        validate_board(Path(board).read_bytes(), probes, require_all=False, allow_legacy=True)
        facts = board_facts(board, work)
        if _sha256(board) != board_sha:
            raise AnnotationError('stale_input', 'board changed during planning')
    except AnnotationError as exc:
        return _failed(PLAN_SCHEMA, exc.code, str(exc), exc.location)
    except ValueError as exc:
        return _failed(PLAN_SCHEMA, 'probe_feature_mismatch', str(exc))
    return build_plan(xml_bytes, board_sha, facts, manifest, captures)


def _verify(plan_record, before, after):
    """The saved board must show exactly the planned change and nothing else."""
    renamed = {r['from']: r['to'] for r in plan_record['renames']}
    removed = {r['footprint_uuid'] for r in plan_record['remove']}
    old = {fp['uuid']: fp for fp in before['footprints']}
    new = {fp['uuid']: fp for fp in after['footprints']}
    added = {a['path']: a for a in plan_record['add']}
    expected_count = len(old) - len(removed) + len(added)
    if len(new) != expected_count:
        raise AnnotationError('verification', 'footprint inventory differs from the plan')
    for uuid, fp in old.items():
        if uuid in removed:
            if uuid in new:
                raise AnnotationError('verification', 'planned removal still present')
            continue
        now = new.get(uuid)
        if now is None or any(now[k] != fp[k] for k in ('path', 'fpid', 'position_nm', 'orientation_deg', 'layer')):
            raise AnnotationError('verification', 'retained footprint moved or changed identity')
        if [p['position_nm'] for p in now['pads']] != [p['position_nm'] for p in fp['pads']]:
            raise AnnotationError('verification', 'retained pad geometry changed')
    paths = {fp['path']: fp for fp in after['footprints'] if fp['path']}
    for path, item in added.items():
        fp = paths.get(path)
        if fp is None or (fp['fpid'], fp['position_nm'], fp['reference'], fp['value']) != \
                (item['fpid'], item['position_nm'], item['reference'], item['value']) or \
                any(fp['fields'].get(k) != v for k, v in item['fields'].items()):
            raise AnnotationError('verification', 'added footprint differs from the plan')
    for item in plan_record['update']:
        fp = new[item['footprint_uuid']]
        if (fp['reference'], fp['value']) != (item['reference'], item['value']) or \
                any(fp['fields'].get(k) != v for k, v in item['fields'].items()):
            raise AnnotationError('verification', 'updated footprint fields differ from the plan')
    for pad in plan_record['pads']:
        nets = {p['net'] for p in paths[pad['path']]['pads'] if p['number'] == pad['pad']}
        if nets != {pad['net']}:
            raise AnnotationError('verification', 'pad net differs from the plan')
    for key in ('tracks', 'zones'):
        if [(x['uuid'], renamed.get(x['net'], x['net'])) for x in before[key]] != \
                [(x['uuid'], x['net']) for x in after[key]]:
            raise AnnotationError('verification', key + ' inventory or net binding changed')


def _absolute(path):
    """Absolute lexical path; ``..`` is refused because it would resolve a link first."""
    if '..' in Path(path).parts:
        raise AnnotationError('unsafe_path', 'path must not contain parent references', str(path))
    return Path(os.path.abspath(path))


def _directory(path, create=False):
    """File descriptor of a directory reached from ``/`` without following links.

    Every component is opened with ``O_NOFOLLOW``, so a link anywhere in the
    path is refused rather than resolved; later steps act through the
    descriptor, so a component swapped meanwhile cannot redirect a write. With
    ``create``, missing components are made private (0700). Without it, a
    missing directory returns ``None``.
    """
    path = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise AnnotationError('unsafe_path', 'a path component is a link or not a directory',
                                          str(path)) from exc
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _entry(directory, name):
    """``lstat`` of one directory entry, or ``None`` when it does not exist."""
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read_regular(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AnnotationError('unsafe_path', 'published entry is not a regular file', name)
        return stream.read()


def _place(directory, name, data, mode, replace):
    """Write ``data`` to a new private file beside ``name``, then publish it.

    The staging name is random and created exclusively without following
    links. Publication either replaces an existing regular file (``replace``)
    or hard-links without clobbering, so an entry that already exists, even a
    dangling link, is never overwritten. The staged entry is always removed.
    """
    temp = f'.{name}.{secrets.token_hex(16)}.annotation'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                 dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        if replace:
            current = _entry(directory, name)
            if current is None or not stat.S_ISREG(current.st_mode):
                raise AnnotationError('unsafe_path', 'in-place target is not a regular file', name)
            os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        else:
            try:
                os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError as exc:
                raise AnnotationError('output_exists', 'refusing to overwrite an existing entry', name) from exc
    finally:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass
    if _read_regular(directory, name) != data:
        raise AnnotationError('verification', 'published bytes differ from the staged bytes', name)


def _backup(backup_dir, source, sha256, mode):
    """Content-addressed backup: an existing entry must be a regular file holding ``source``."""
    name = sha256 + '.kicad_pcb'
    directory = _directory(backup_dir, create=True)
    try:
        current = _entry(directory, name)
        if current is None:
            try:
                _place(directory, name, source, mode, replace=False)
                return
            except AnnotationError as exc:
                if exc.code != 'output_exists':
                    raise
            current = _entry(directory, name)
        if current is None or not stat.S_ISREG(current.st_mode) or \
                hashlib.sha256(_read_regular(directory, name)).hexdigest() != sha256:
            raise AnnotationError('backup_location', 'backup entry is not a regular file holding the source bytes',
                                  str(Path(backup_dir) / name))
    finally:
        os.close(directory)


def _preflight(board, out, backup_dir):
    """Refuse unsafe or occupied destinations before any work or write.

    Returns whether ``out`` is the board itself. Ancestors of the output and of
    the backup directory must contain no links; an in-place target must be a
    regular file, and any other output name must not exist in any form.
    """
    board, out = _absolute(board), _absolute(out)
    in_place = out == board
    directory = _directory(out.parent)
    if directory is None:
        raise AnnotationError('unsafe_path', 'output directory does not exist', str(out.parent))
    try:
        current = _entry(directory, out.name)
    finally:
        os.close(directory)
    if in_place:
        if current is None or not stat.S_ISREG(current.st_mode):
            raise AnnotationError('unsafe_path', 'in-place target is not a regular file', str(out))
        if backup_dir is None or _absolute(backup_dir).is_relative_to(out.parent):
            raise AnnotationError('backup_location', 'in-place apply needs a backup outside the board directory')
        existing = _directory(backup_dir)
        if existing is not None:
            os.close(existing)
    elif current is not None:
        raise AnnotationError('output_exists', 'refusing to overwrite an existing output')
    return in_place


def apply(plan_record, board, xml, manifest, footprint_root, out, work, *, backup_dir=None):
    """Recompute, compare, apply on a copy, verify, then publish ``out`` atomically.

    The supplied plan is a declaration of intent only: it must equal a fresh plan
    over the same current bytes. When ``out`` is the source board, its prior bytes
    are first copied into ``backup_dir``, which must lie outside the board's
    directory so that no file is added to a captured candidate inventory.
    """
    work = Path(work)
    work.mkdir(mode=0o700, parents=False, exist_ok=False)
    result = dict(schema=RESULT_SCHEMA, execution='failed', outcome='unknown', findings=[],
                  plan_sha256=plan_record.get('canonical_sha256'), output=None, backup=None)
    try:
        if plan_record.get('execution') != 'ok':
            raise AnnotationError('plan_rejected', 'the plan did not complete')
        fresh = plan(board, xml, manifest, footprint_root, work / 'replan')
        if _canonical(fresh) != _canonical(plan_record):
            raise AnnotationError('stale_plan', 'plan differs from a fresh plan over the current inputs')
        board, out = Path(board), Path(out)
        in_place = _preflight(board, out, backup_dir)
        changes = sum(plan_record['counts'][k] for k in ('add', 'remove', 'update', 'renames', 'new_nets'))
        changes += plan_record['counts']['pad_changes']
        source = board.read_bytes()
        if changes == 0:
            output = source  # Re-applying identical inputs is a byte-for-byte no-op.
        else:
            inputs = work / 'apply-input'
            (inputs / 'libraries').mkdir(parents=True)
            (inputs / 'board.kicad_pcb').write_bytes(source)
            captures = _captures(manifest, footprint_root)
            for item in plan_record['add']:
                data = captures[item['member']['path']]
                if hashlib.sha256(data).hexdigest() != item['member']['sha256']:
                    raise AnnotationError('stale_input', 'captured footprint changed')
                target = inputs / 'libraries' / (item['library'] + '.pretty') / (item['name'] + '.kicad_mod')
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)
            (inputs / 'plan.json').write_bytes(_canonical(plan_record))
            _child(inputs, work / 'apply', ['apply', '/candidate/board.kicad_pcb', '--plan', '/candidate/plan.json',
                                            '--libraries', '/candidate/libraries', '-o', '/work/board.kicad_pcb'], 300)
            before = json.loads((work / 'replan' / 'facts' / 'facts.json').read_text())
            # A fresh child reads the saved output; the writer's process is not trusted for it.
            after = board_facts(work / 'apply' / 'board.kicad_pcb', work / 'verify')
            _verify(plan_record, before, after)
            output = (work / 'apply' / 'board.kicad_pcb').read_bytes()
            if hashlib.sha256(output).hexdigest() != _sha256(work / 'verify' / 'facts-input' / 'board.kicad_pcb'):
                raise AnnotationError('verification', 'saved board changed after verification')
        from .probe_features import PROPERTY, parse_marker, validate_board
        _, elements, _ = _inventory(Path(xml).read_bytes())
        probes = {ref: parse_marker(fields[PROPERTY]) for ref, fields in _fields(elements).items()
                  if PROPERTY in fields}
        try:
            validate_board(output, probes)
        except ValueError as exc:
            raise AnnotationError('probe_feature_mismatch', str(exc)) from exc
        if hashlib.sha256(source).hexdigest() != plan_record['inputs']['board_sha256'] or \
                _sha256(board) != plan_record['inputs']['board_sha256'] or \
                hashlib.sha256(Path(xml).read_bytes()).hexdigest() != plan_record['inputs']['xml_sha256']:
            raise AnnotationError('stale_input', 'inputs changed during apply')
        mode = stat.S_IMODE(os.stat(board).st_mode)
        if in_place:
            _backup(backup_dir, source, plan_record['inputs']['board_sha256'], mode)
            result['backup'] = str(Path(backup_dir) / (plan_record['inputs']['board_sha256'] + '.kicad_pcb'))
        directory = _directory(out.parent)
        if directory is None:
            raise AnnotationError('unsafe_path', 'output directory does not exist', str(out.parent))
        try:
            _place(directory, out.name, output, mode, replace=in_place)
        finally:
            os.close(directory)
        result.update(execution='ok', outcome='info',
                      output=dict(path=str(out), sha256=hashlib.sha256(output).hexdigest()))
    except AnnotationError as exc:
        result['findings'].append(dict(code=exc.code, location=exc.location, message=str(exc)))
    return _seal(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'apply'):
        cmd = sub.add_parser(name)
        cmd.add_argument('board')
        cmd.add_argument('--netlist', required=True, help='native kicad-cli XML export')
        cmd.add_argument('--manifest', required=True, help='agent-ee.footprint-libraries/1 manifest')
        cmd.add_argument('--captures', required=True, help='root of the captured footprint files')
        cmd.add_argument('--work', required=True, help='new private scratch directory')
        cmd.add_argument('--record', required=True, help='new plan/result JSON file')
        if name == 'apply':
            cmd.add_argument('--plan', required=True)
            cmd.add_argument('--out', required=True)
            cmd.add_argument('--backup-dir')
    args = parser.parse_args(argv)
    manifest = load_document(args.manifest)
    try:
        if args.command == 'plan':
            record = plan(args.board, args.netlist, manifest, args.captures, args.work)
        else:
            record = apply(load_document(args.plan), args.board, args.netlist, manifest, args.captures, args.out,
                           args.work, backup_dir=args.backup_dir)
    except AnnotationError as exc:
        record = _failed(RESULT_SCHEMA, exc.code, str(exc), exc.location)
    with Path(args.record).open('xb') as stream:
        stream.write(_canonical(record) + b'\n')
    return 0 if record['execution'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
