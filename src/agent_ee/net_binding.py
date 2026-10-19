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
        name = re.sub(r'\\(.)', r'\1', pattern)
        if exact_pattern(name) == pattern.replace(r'\/', '/'):
            declared.append(name)
    if declared:
        require_nets(declared, board_net_names(board_raw))
