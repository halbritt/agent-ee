"""Exact native-net membership shared by constraint consumers and router setup."""
import re

if __package__:
    from .symbol_library import children, parse_sexpr
else:  # The routing adapter mounts these two modules beside its standalone script.
    from symbol_library import children, parse_sexpr


def exact_pattern(net):
    """Escape both KiCad's anchored regex and wildcard syntax to literal text."""
    # '/' is literal in KiCad patterns and KRT globs; escaping it makes KRT
    # miss the netclass and route at Default width.
    return ''.join(c if c.isalnum() or c in '_/' else '\\' + c for c in net)


def board_net_names(raw):
    root = parse_sexpr(raw)
    if not root or root[0] != 'kicad_pcb':
        raise ValueError('expected kicad_pcb for net constraint binding')
    # KiCad 10 stores named nets on pads/copper; older boards use a root
    # numeric net table. Walk the shared reader's tree, never scan raw text.
    names, pending = set(), [root]
    while pending:
        node = pending.pop()
        if node and node[0] == 'net':
            if len(node) not in (2, 3) or not isinstance(node[-1], str):
                raise ValueError('malformed native board net')
            if node[-1]:
                names.add(node[-1])
        pending.extend(child for child in node if isinstance(child, list))
    return names


def require_nets(declared, native):
    missing = sorted(set(declared) - set(native))
    if missing:
        raise ValueError('constraint nets absent from board: ' + ', '.join(missing))


def require_project_nets(project, board_raw):
    """Validate canonical literal assignments, including every emitted constraint net.

    General user regex/wildcard policies are outside the exact-name constraint
    contract; do not reinterpret them as literal names.
    """
    if not isinstance(project, dict):
        raise ValueError('project must be an object')
    settings = project.get('net_settings', {})
    if not isinstance(settings, dict) or not isinstance(settings.get('netclass_patterns', []), list):
        raise ValueError('project netclass_patterns must be a list')
    declared = []
    for entry in settings.get('netclass_patterns', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('pattern'), str):
            raise ValueError('project netclass pattern must be text')
        pattern = entry['pattern']
        name = literal_name(pattern)
        if name is not None:
            declared.append(name)
    if declared:
        require_nets(declared, board_net_names(board_raw))


def literal_name(pattern):
    """Decode only the existing canonical literal-name assignment contract."""
    name = re.sub(r'\\(.)', r'\1', pattern)
    return name if exact_pattern(name) == pattern.replace(r'\/', '/') else None


def literal_project_classes(project, native_names):
    """Class membership for exact assignments; general/native composites stay unresolved."""
    settings = project.get('net_settings', {})
    if settings.get('netclass_assignments') or any(c.get('nets') for c in settings.get('classes', [])):
        return {}
    assigned = {}
    for entry in settings.get('netclass_patterns', []):
        name = literal_name(entry['pattern'])
        if name is None or not isinstance(entry.get('netclass'), str):
            return {}
        assigned.setdefault(name, set()).add(entry['netclass'])
    return {name: next(iter(assigned[name])) if name in assigned else 'Default'
            for name in sorted(native_names) if len(assigned.get(name, ())) <= 1}


def board_item_nets(raw):
    """Native conductor UUID/net facts from the shared bounded S-expression reader.

    Duplicate identities and ambiguous legacy numeric-only nets remain unresolved.
    Display descriptions are not identity evidence.
    """
    import uuid
    root = parse_sexpr(raw)
    if root[0] != 'kicad_pcb':
        raise ValueError('expected kicad_pcb for item net binding')
    legacy_codes = {n[1] for n in children(root, 'net') if len(n) == 3}
    result, pending = {}, [root]
    while pending:
        node = pending.pop()
        pending.extend(child for child in node if isinstance(child, list))
        if not node or node[0] not in ('pad', 'segment', 'arc', 'via', 'zone', 'gr_line', 'gr_arc', 'gr_poly'):
            continue
        ids = children(node, 'uuid')
        if len(ids) != 1 or len(ids[0]) != 2 or not isinstance(ids[0][1], str):
            continue
        try:
            identity = str(uuid.UUID(ids[0][1]))
        except ValueError:
            continue
        nets = children(node, 'net')
        name = ''
        if nets:
            name = (nets[0][-1] if len(nets) == 1 and len(nets[0]) in (2, 3) else None)
            if len(nets) == 1 and len(nets[0]) == 2 and name in legacy_codes:
                name = None
        result[identity] = None if identity in result else name
    return result
