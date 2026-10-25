"""Hash-pinned group-rails sidecar (AGENTEE-479 S2): evidence-bound shared rails for one design.

A rails document names the exact board and groups bytes it applies to and
lists, per net, the captured evidence that makes it a shared rail or a scoped
rail. Nothing here classifies nets by name: a net absent from the document is
not a rail, and a multi-group net outside the document is surfaced by the
report as unknown. Stale or unknown bindings refuse.
"""
SCHEMA = 'agent-ee.group-rails/1'


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{path}: expected nonempty text')


def _object(value, path, required=(), optional=()):
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected object')
    for key in value:
        if key not in {*required, *optional}:
            raise ValueError(f'{path}.{key}: unsupported group-rails field')
    for key in required:
        if key not in value:
            raise ValueError(f'{path}.{key}: required field is missing')


def _unique_texts(value, path):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f'{path}: expected a nonempty list of text')
    if len(set(value)) != len(value):
        raise ValueError(f'{path}: expected distinct entries')


def validate(document, *, board_sha256=None, groups_sha256=None, group_names=None, nets=None,
             net_groups=None, net_parts=None):
    """Validate the sidecar and, when the captured identities are given, bind it to them.

    ``net_groups[net]`` and ``net_parts[net]`` are the captured board's actual
    group and reference membership per net; declared ``groups``/``parts``
    annotations must be real members, so stale or unknown provenance refuses.
    Annotations are optional and never required to be exhaustive.
    """
    _object(document, 'group_rails', ('schema', 'bindings', 'shared', 'scoped'), ('source',))
    if document['schema'] != SCHEMA:
        raise ValueError(f'group_rails.schema: expected {SCHEMA}')
    bindings = document['bindings']
    _object(bindings, 'group_rails.bindings', ('board_sha256', 'groups_sha256'))
    for key in ('board_sha256', 'groups_sha256'):
        value = bindings[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError(f'group_rails.bindings.{key}: expected a sha256 hex digest')
    if board_sha256 is not None and bindings['board_sha256'] != board_sha256:
        raise ValueError('group_rails.bindings.board_sha256: sidecar is bound to a different board')
    if groups_sha256 is not None and bindings['groups_sha256'] != groups_sha256:
        raise ValueError('group_rails.bindings.groups_sha256: sidecar is bound to different groups')
    if 'source' in document:
        _object(document['source'], 'group_rails.source', optional=('contract', 'commit', 'section', 'note'))
        for key, value in document['source'].items():
            _text(value, 'group_rails.source.' + key)
    _object(document['shared'], 'group_rails.shared', optional=tuple(document['shared']) if isinstance(document['shared'], dict) else ())
    for net, row in document['shared'].items():
        _text(net, 'group_rails.shared')
        _object(row, f'group_rails.shared.{net}', ('evidence',), ('parts', 'groups'))
        _text(row['evidence'], f'group_rails.shared.{net}.evidence')
        for key in ('parts', 'groups'):
            if key in row:
                _unique_texts(row[key], f'group_rails.shared.{net}.{key}')
        if nets is not None and net not in nets:
            raise ValueError(f'group_rails.shared.{net}: net is not on the captured board')
        if 'groups' in row:
            if group_names is not None:
                unknown = [g for g in row['groups'] if g not in group_names]
                if unknown:
                    raise ValueError(f'group_rails.shared.{net}.groups: unknown group {unknown[0]!r}')
            if net_groups is not None:
                absent = [g for g in row['groups'] if g not in net_groups.get(net, set())]
                if absent:
                    raise ValueError(f'group_rails.shared.{net}.groups: {absent[0]!r} has no pad on this net')
        if 'parts' in row and net_parts is not None:
            absent = [p for p in row['parts'] if p not in net_parts.get(net, set())]
            if absent:
                raise ValueError(f'group_rails.shared.{net}.parts: {absent[0]!r} has no pad on this net')
    _object(document['scoped'], 'group_rails.scoped', optional=tuple(document['scoped']) if isinstance(document['scoped'], dict) else ())
    for net, row in document['scoped'].items():
        _object(row, f'group_rails.scoped.{net}', ('evidence',), ('intra_group', 'near_pairs'))
        _text(row['evidence'], f'group_rails.scoped.{net}.evidence')
        if net not in document['shared']:
            raise ValueError(f'group_rails.scoped.{net}: a scoped net must also be a declared shared rail')
        if 'intra_group' in row:
            _text(row['intra_group'], f'group_rails.scoped.{net}.intra_group')
            if group_names is not None and row['intra_group'] not in group_names:
                raise ValueError(f'group_rails.scoped.{net}.intra_group: unknown group {row["intra_group"]!r}')
        if 'near_pairs' in row and not isinstance(row['near_pairs'], list):
            raise ValueError(f'group_rails.scoped.{net}.near_pairs: expected a list of [from_group, to_group]')
        seen_pairs = set()
        for index, pair in enumerate(row.get('near_pairs', [])):
            path = f'group_rails.scoped.{net}.near_pairs[{index}]'
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, str) and v for v in pair) or pair[0] == pair[1]:
                raise ValueError(path + ': expected [from_group, to_group]')
            if group_names is not None and any(v not in group_names for v in pair):
                raise ValueError(path + ': unknown group name')
            if tuple(pair) in seen_pairs:
                raise ValueError(path + ': duplicate pair')
            seen_pairs.add(tuple(pair))
            if net_groups is not None and any(v not in net_groups.get(net, set()) for v in pair):
                raise ValueError(path + ': a named group has no pad on this net')
        if 'intra_group' in row and net_groups is not None and row['intra_group'] not in net_groups.get(net, set()):
            raise ValueError(f'group_rails.scoped.{net}.intra_group: {row["intra_group"]!r} has no pad on this net')
    return canonical(document)


def canonical(document):
    """The one rails shape consumed by the solver, the report and the receipts."""
    return {'shared': {net: row['evidence'] for net, row in document['shared'].items()},
            'scoped': {net: {k: v for k, v in row.items() if k in ('intra_group', 'near_pairs')}
                       for net, row in document['scoped'].items()},
            'bindings': dict(document['bindings'])}


EMPTY = {'shared': {}, 'scoped': {}, 'bindings': None}
