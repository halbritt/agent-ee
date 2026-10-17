"""Deterministic readability and presentation lint over captured schematics (AGENTEE-45).

Pure geometry and topology over the captured ``.kicad_sch`` hierarchy, the
symbol definitions embedded in each sheet's ``lib_symbols`` (what KiCad draws)
and the native XML netlist retained by an earlier ``netlist`` check. No tool is
launched and nothing is repaired. Findings are style advice: ``info`` or
``warning`` unless the scope profile raises a rule to ``defect``. A symbol whose
definition, transform, body graphics or native identity cannot be established
is listed as missing coverage for the rules that need it, never as a pass.
See docs/schematic-lint.md.
"""
import hashlib
import math
import os
import re
from pathlib import Path, PurePosixPath

from .schematic_emit import _hidden
from .schematic_emitter import _apply, _matrix
from .symbol_context import _bounds
from .symbol_library import LibraryError, children, field, nanometres, parse_sexpr, resolve_symbol

SCHEMA = 'agent-ee.schematic-lint/1'
SEVERITIES = ('info', 'warning', 'defect')
# Rule id -> default severity. Original implementations of rules named by
# tscircuit/checks, SchGen and the circuit-synth review checklist; no upstream
# code was copied or consulted.
RULES = {
    'symbol-overlap/1': 'warning', 'label-in-symbol/1': 'warning',
    'wire-crosses-pin/1': 'warning', 'wire-overlap/1': 'warning',
    'two-pin-rails/1': 'warning', 'pull-resistor/1': 'warning', 'divider-order/1': 'warning',
    'regulator-capacitors/1': 'warning', 'led-chain-order/1': 'warning',
    'rail-label/1': 'warning', 'diff-pair-naming/1': 'warning',
    'on-grid/1': 'info', 'hidden-properties/1': 'info', 'reference-visible/1': 'info',
    'reference-prefix/1': 'info', 'title-block/1': 'info'}
BODY_RULES = frozenset(('symbol-overlap/1', 'label-in-symbol/1'))
NET_RULES = frozenset(('wire-crosses-pin/1', 'two-pin-rails/1', 'pull-resistor/1', 'divider-order/1',
                       'regulator-capacitors/1', 'led-chain-order/1'))
LIB_RULES = BODY_RULES | NET_RULES | {'reference-prefix/1'}
NATIVE_RULES = NET_RULES | {'diff-pair-naming/1'}
CLASSES = frozenset(('resistor', 'capacitor', 'led', 'regulator'))
PARAMS = frozenset(('netlist_check', 'rules', 'power_nets', 'ground_nets', 'symbols', 'grid_mm',
                    'title_block', 'diff_pairs', 'hidden_properties'))
TITLE_FIELDS = ('title', 'date', 'rev', 'company')
# _P/_N is opt-in: active-low names such as RESET_N have no _P partner.
DEFAULT_PAIRS = (('_DP', '_DN'), ('D+', 'D-'))
DEFAULT_HIDDEN = ('Footprint', 'Datasheet', 'Description')
LABELS = ('label', 'global_label', 'hierarchical_label')
MAX_SHEETS = 256
MAX_WORK = 4_000_000  # wire x pin and symbol x symbol comparisons per sheet
ORIENT = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}  # library pin direction, y up


def _names(values, label):
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values) \
            or len(set(values)) != len(values):
        raise ValueError(f'{label} must be a list of unique nonempty names')
    return list(values)


def profile(params):
    """Validated rule profile; an unknown rule id or parameter is unsupported."""
    if not isinstance(params, dict) or not set(params) <= PARAMS or 'netlist_check' not in params:
        raise ValueError('schematic_lint accepts ' + ', '.join(sorted(PARAMS)) + ' and requires netlist_check')
    if not isinstance(params['netlist_check'], str) or not params['netlist_check']:
        raise ValueError('netlist_check must name an earlier netlist check')
    rules = params.get('rules', dict(RULES))
    if not isinstance(rules, dict) or not rules:
        raise ValueError('rules must map rule ids to severities')
    for rule, severity in rules.items():
        if rule not in RULES:
            raise ValueError(f'unknown schematic lint rule: {rule!r}')
        if severity not in SEVERITIES:
            raise ValueError(f'{rule}: severity must be one of {", ".join(SEVERITIES)}')
    power, ground = (_names(params.get(k, []), k) for k in ('power_nets', 'ground_nets'))
    if set(power) & set(ground):
        raise ValueError('a net cannot be both power and ground')
    symbols = params.get('symbols', {})
    if not isinstance(symbols, dict):
        raise ValueError('symbols must map lib_id to a declared class')
    for lib_id, entry in symbols.items():
        if not isinstance(lib_id, str) or lib_id.count(':') != 1 or not isinstance(entry, dict):
            raise ValueError('symbol class key must be a lib_id with one colon')
        keys = {'class', 'input', 'output'} if entry.get('class') == 'regulator' else {'class'}
        if set(entry) != keys or entry['class'] not in CLASSES:
            raise ValueError(f'{lib_id}: class must be one of {", ".join(sorted(CLASSES))}; '
                             'a regulator also names its input and output pin numbers')
        if keys != {'class'} and (any(not isinstance(entry[k], str) or not entry[k] for k in ('input', 'output'))
                                  or entry['input'] == entry['output']):
            raise ValueError(f'{lib_id}: distinct input/output pin numbers required')
    grid = params.get('grid_mm', 1.27)
    if isinstance(grid, bool) or not isinstance(grid, (int, float)):
        raise ValueError('grid_mm must be numeric')
    try:
        grid_nm = nanometres(repr(float(grid)))
    except LibraryError as exc:
        raise ValueError('grid_mm must be exact nanometres') from exc
    if grid_nm <= 0:
        raise ValueError('grid_mm must be positive')
    title = _names(params.get('title_block', ['title', 'date', 'rev']), 'title_block')
    if not set(title) <= set(TITLE_FIELDS):
        raise ValueError('title_block fields must be among ' + ', '.join(TITLE_FIELDS))
    pairs = params.get('diff_pairs', [list(p) for p in DEFAULT_PAIRS])
    if not isinstance(pairs, list) or any(not isinstance(p, list) or len(p) != 2 for p in pairs):
        raise ValueError('diff_pairs must be a list of [positive, negative] suffixes')
    for pair in pairs:
        if _names(pair, 'diff pair')[0] == pair[1]:
            raise ValueError('diff pair suffixes must differ')
    hidden = _names(params.get('hidden_properties', list(DEFAULT_HIDDEN)), 'hidden_properties')
    return dict(rules=dict(sorted(rules.items())), power_nets=sorted(power), ground_nets=sorted(ground),
                symbols={k: dict(v) for k, v in sorted(symbols.items())}, grid_nm=grid_nm, title_block=title,
                diff_pairs=[list(p) for p in pairs], hidden_properties=hidden)


def capability(params, scope, files, inputs, check_id):
    lint_profile = profile(params)
    if scope.get('schematic') not in files:
        raise ValueError('schematic lint requires the captured scope schematic')
    predicates = ['schematic-lint:' + r for r, s in lint_profile['rules'].items() if s == 'defect']
    return dict(profile=lint_profile, immutable_inputs=[], predicates=predicates)


# ------------------------------------------------------------------ capture model

def _xy(node, key='at'):
    """Exact (x, y) nanometres and the optional angle atom of one position node."""
    found = children(node, key)
    atoms = [x for x in found[0][1:] if isinstance(x, str)] if len(found) == 1 else []
    if len(atoms) not in (2, 3):
        raise LibraryError(f'expected one {key} position')
    return (nanometres(atoms[0]), nanometres(atoms[1])), (atoms[2] if len(atoms) == 3 else None)


def _uuid(node):
    found = children(node, 'uuid')
    return found[0][1] if len(found) == 1 and len(found[0]) == 2 and isinstance(found[0][1], str) else None


def _properties(node):
    props = {}
    for prop in children(node, 'property'):
        if len(prop) < 3 or not isinstance(prop[1], str) or not isinstance(prop[2], str) or prop[1] in props:
            raise LibraryError('duplicate or malformed property')
        props[prop[1]] = (prop[2], _hidden(prop))
    return props


def load_sheets(inputs, root_rel):
    """Every sheet instance reachable from the root, parsed with the shared reader."""
    inputs = Path(inputs).resolve()
    sheets, cache = [], {}

    def read(rel):
        if rel not in cache:
            path = Path(os.path.normpath(inputs / rel))
            if not path.is_relative_to(inputs) or not path.is_file():
                raise ValueError(f'sheet file {rel!r} is missing or escapes the candidate')
            raw = path.read_bytes()
            tree = parse_sexpr(raw)
            if tree[0] != 'kicad_sch':
                raise ValueError(f'{rel} is not a kicad_sch')
            cache[rel] = (tree, hashlib.sha256(raw).hexdigest())
        return cache[rel]

    root_tree, _ = read(root_rel)
    root_uuid = _uuid(root_tree)
    if not root_uuid:
        raise ValueError('root schematic has no uuid')

    def walk(rel, uuids, names, stack):
        if rel in stack:
            raise ValueError(f'sheet hierarchy cycle through {rel!r}')
        if len(sheets) >= MAX_SHEETS:
            raise ValueError('sheet instance limit exceeded')
        tree, sha = read(rel)
        sheets.append(dict(file=rel, sha256=sha, tree=tree,
                           path='/' + ''.join(u + '/' for u in uuids),
                           names='/' + ''.join(n + '/' for n in names),
                           instance_path='/' + '/'.join([root_uuid, *uuids])))
        for node in children(tree, 'sheet'):
            props = {k: v for k, (v, _) in _properties(node).items()}
            sheet_uuid = _uuid(node)
            name = props.get('Sheetname', props.get('Sheet name'))
            target = props.get('Sheetfile', props.get('Sheet file'))
            if not sheet_uuid or not name or not target:
                raise ValueError(f'{rel}: sheet symbol without uuid, name or file')
            child = os.path.normpath(str(PurePosixPath(rel).parent / target))
            walk(PurePosixPath(child).as_posix(), uuids + [sheet_uuid], names + [name], stack | {rel})

    walk(PurePosixPath(root_rel).as_posix(), [], [], frozenset())
    return sheets


def _box(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class _Symbol:
    """One placed instance: identity, properties and (when proven) sheet geometry."""

    def __init__(self, node, sheet, embedded, natives, graph, rules):
        self.uuid = _uuid(node)
        self.lib_id = field(node, 'lib_id', 1)[0]
        self.props = _properties(node)
        self.reference = self._reference(node, sheet)
        self.sheet = sheet
        self.gaps = []
        self.pins, self.body, self.power, self.lib_reference, self.native = [], None, False, None, None
        (self.x, self.y), angle = _xy(node)
        self.unit = int(field(node, 'unit', 1)[0]) if children(node, 'unit') else 1
        try:
            rotation = nanometres(angle or '0')
            mirror = field(node, 'mirror', 1)[0] if children(node, 'mirror') else None
            style = [field(node, k, 1)[0] for k in ('body_style', 'convert') if children(node, k)]
            if rotation % 90000000 or mirror not in (None, 'x', 'y') or any(s != '1' for s in style):
                raise LibraryError('unsupported instance transform or alternate body style')
            self.matrix = _matrix((rotation // 1000000) % 360, mirror)
            if self.lib_id not in embedded:
                raise LibraryError('library definition not resolved in the sheet lib_symbols')
            pins, sources, self.power = resolve_symbol(embedded, self.lib_id, sheet['file'], sheet['sha256'])
        except (LibraryError, ValueError, KeyError) as exc:
            self.gap(LIB_RULES, str(exc), rules)
            return
        units = [u for src in sources for u in children(embedded[src['symbol']], 'symbol')]
        lib_props = {}
        for src in sources:
            lib_props.update(_properties(embedded[src['symbol']]))
        self.lib_reference = lib_props.get('Reference', (None, False))[0]
        lengths = {}
        for unit_node in units:
            for pin in children(unit_node, 'pin'):
                (px, py), _ = _xy(pin)
                hidden = 'hide' in pin[1:] or ['hide', 'yes'] in pin[1:]
                lengths[(unit_node[1], field(pin, 'number', 1)[0], px, py)] = (
                    nanometres(field(pin, 'length', 1)[0]), hidden)
        for pin in pins:
            if pin['unit'] not in (0, self.unit):
                continue
            length, hidden = lengths[(pin['source']['unit_symbol'], pin['number'], pin['x_nm'], pin['y_nm'])]
            dx, dy = _apply(self.matrix, pin['x_nm'], pin['y_nm'])
            ux, uy = ORIENT[pin['orientation']]
            ix, iy = _apply(self.matrix, ux * length, uy * length)
            point = (self.x + dx, self.y + dy)
            self.pins.append(dict(number=pin['number'], name=pin['name'], point=point, hidden=hidden,
                                  inner=(point[0] + ix, point[1] + iy), net=None))
        body_units = [[u[0], u[1], *[g for g in u[2:] if not (isinstance(g, list) and g[:1] == ['pin'])]]
                      for u in units]
        bounds = _bounds(body_units, [], self.unit)
        if bounds['coverage'] == 'complete':
            x0, y0, x1, y1 = bounds['bounds_nm']
            corners = [_apply(self.matrix, x, y) for x, y in ((x0, y0), (x1, y1))]
            self.body = _box([(self.x + cx, self.y + cy) for cx, cy in corners])
        else:
            self.gap(BODY_RULES, 'body graphics: ' + bounds['reason'], rules)
        if self.power:
            # A power symbol is not a netlist component; its pins carry its Value net.
            for pin in self.pins:
                pin['net'] = self.props.get('Value', ('', False))[0]
            return
        if graph is None:
            return  # The design-level inventory gap already covers every net rule.
        ref = natives.get((sheet['path'], self.uuid))
        if ref is None:
            self.gap(NET_RULES, 'no native netlist component for this sheet instance', rules)
            return
        self.native = ref
        for pin in self.pins:
            nid = graph['memberships'].get((ref, pin['number']))
            pin['net'] = graph['nets'][nid]['name'] if nid is not None else None
        if any(p['net'] is None for p in self.pins):
            self.gap(NET_RULES, 'pin without native net membership', rules)

    def _reference(self, node, sheet):
        for inst in children(node, 'instances'):
            for project in children(inst, 'project'):
                for path in children(project, 'path'):
                    if len(path) > 1 and path[1] == sheet['instance_path'] and children(path, 'reference'):
                        return field(path, 'reference', 1)[0]
        return self.props.get('Reference', ('?', False))[0]

    def gap(self, affected, reason, rules):
        affected = sorted(set(affected) & set(rules))
        if affected:
            self.gaps.append(dict(sheet=self.sheet['names'], sheet_path=self.sheet['path'],
                                  file=self.sheet['file'], uuid=self.uuid, reference=self.reference,
                                  lib_id=self.lib_id, reason=reason, rules=affected))

    def proven(self, rule):
        if rule in NET_RULES and not self.power and self.native is None:
            return False
        return not any(rule in g['rules'] for g in self.gaps)

    def item(self):
        return dict(kind='symbol', reference=self.reference, uuid=self.uuid, lib_id=self.lib_id,
                    at_mm=[_mm(self.x), _mm(self.y)])

    def centre(self):
        return (sum(p['point'][0] for p in self.pins) / len(self.pins),
                sum(p['point'][1] for p in self.pins) / len(self.pins))


def _mm(nm):
    return nm / 1000000


def _base(name):
    return name.rsplit('/', 1)[-1] if name and name.startswith('/') else name


def _on_segment(p, a, b):
    return ((b[0] - a[0]) * (p[1] - a[1]) == (b[1] - a[1]) * (p[0] - a[0]) and
            min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _touch(a, b, c, d):
    """Closed segments ab and cd share at least one point (exact integers)."""
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return ((d1 == 0 and _on_segment(a, c, d)) or (d2 == 0 and _on_segment(b, c, d)) or
            (d3 == 0 and _on_segment(c, a, b)) or (d4 == 0 and _on_segment(d, a, b)))


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


# ------------------------------------------------------------------ rules

class _Lint:
    def __init__(self, sheets, graph, lint_profile, inventory_error=None):
        self.profile = lint_profile
        self.rules = lint_profile['rules']
        self.graph = graph
        self.findings, self.coverage = [], []
        self.power, self.ground = set(lint_profile['power_nets']), set(lint_profile['ground_nets'])
        self.classes = {k: v for k, v in lint_profile['symbols'].items()}
        self.natives = {}
        if graph is None:
            affected = sorted(NATIVE_RULES & set(self.rules))
            if affected:
                self.coverage.append(dict(sheet='/', sheet_path='/', file=sheets[0]['file'], rules=affected,
                                          reason=f'native netlist inventory unusable: {inventory_error}'))
        else:
            for ref, comp in graph['components'].items():
                for uid in comp['uuid'].split():
                    self.natives[(comp['sheet'], uid)] = ref
        self.sheets = []
        for sheet in sheets:
            tree = sheet['tree']
            embedded = {}
            for lib in children(tree, 'lib_symbols'):
                for node in children(lib, 'symbol'):
                    if len(node) < 2 or not isinstance(node[1], str) or node[1] in embedded:
                        raise LibraryError(f"{sheet['file']}: invalid or duplicate embedded symbol")
                    embedded[node[1]] = node
            symbols = [_Symbol(n, sheet, embedded, self.natives, graph, self.rules) for n in children(tree, 'symbol')]
            for sym in symbols:
                self.coverage.extend(sym.gaps)
            wires = []
            for node in children(tree, 'wire'):
                pts = children(node, 'pts')
                if len(pts) != 1 or len(children(pts[0], 'xy')) != 2:
                    raise LibraryError(f"{sheet['file']}: wire needs exactly two points")
                a, b = (_xy(['pt', xy], 'xy')[0] for xy in children(pts[0], 'xy'))
                wires.append(dict(uuid=_uuid(node), a=a, b=b))
            labels = [dict(kind=n[0], text=n[1], uuid=_uuid(n), at=_xy(n)[0])
                      for kind in LABELS for n in children(tree, kind) if len(n) > 1 and isinstance(n[1], str)]
            sheet_symbols = []
            for node in children(tree, 'sheet'):
                (x, y), _ = _xy(node)
                w, h = (nanometres(v) for v in field(node, 'size', 2))
                pins = [dict(name=p[1], uuid=_uuid(p), at=_xy(p)[0]) for p in children(node, 'pin')
                        if len(p) > 1 and isinstance(p[1], str)]
                props = _properties(node)
                sheet_symbols.append(dict(uuid=_uuid(node), at=(x, y), size=(w, h), pins=pins,
                                          name=props.get('Sheetname', props.get('Sheet name', ('', False)))[0]))
            self.sheets.append(dict(meta=sheet, symbols=symbols, wires=wires, labels=labels,
                                    sheet_symbols=sheet_symbols,
                                    junctions=[dict(uuid=_uuid(n), at=_xy(n)[0]) for n in children(tree, 'junction')],
                                    no_connects=[dict(uuid=_uuid(n), at=_xy(n)[0]) for n in children(tree, 'no_connect')],
                                    title=children(tree, 'title_block')))

    def emit(self, rule, sheet, items, message, action, at=None):
        finding = dict(rule=rule, severity=self.rules[rule], sheet=sheet['names'], sheet_path=sheet['path'],
                       file=sheet['file'], items=items, message=message,
                       action=action + '; preserve every existing connection.')
        if at is not None:
            finding['at_mm'] = [_mm(at[0]), _mm(at[1])]
        self.findings.append(finding)

    def missing(self, rule, sheet, reason):
        self.coverage.append(dict(sheet=sheet['names'], sheet_path=sheet['path'], file=sheet['file'],
                                  reason=reason, rules=[rule]))

    def run(self):
        for model in self.sheets:
            for rule in self.rules:
                method = getattr(self, '_' + rule.split('/')[0].replace('-', '_'))
                if rule != 'diff-pair-naming/1':
                    method(model)
        if 'diff-pair-naming/1' in self.rules:
            self._diff_pair_naming()
        order = lambda f: (f['sheet_path'], f['file'], f['rule'], [(i.get('reference', ''), i.get('uuid') or '')
                                                                    for i in f['items']], f['message'])
        self.findings.sort(key=order)
        self.coverage.sort(key=lambda g: (g['sheet_path'], g['file'], g.get('uuid') or '', g['reason']))
        return self.findings, self.coverage

    # --- geometry

    def _symbol_overlap(self, model):
        rule, sheet = 'symbol-overlap/1', model['meta']
        boxed = sorted((s for s in model['symbols'] if s.body), key=lambda s: (s.body, s.uuid or ''))
        if len(boxed) ** 2 > MAX_WORK:
            return self.missing(rule, sheet, 'symbol comparison work limit')
        for i, first in enumerate(boxed):
            for second in boxed[i + 1:]:
                if second.body[0] >= first.body[2]:
                    break
                x0, y0 = max(first.body[0], second.body[0]), max(first.body[1], second.body[1])
                x1, y1 = min(first.body[2], second.body[2]), min(first.body[3], second.body[3])
                if x1 > x0 and y1 > y0:
                    self.emit(rule, sheet, [first.item(), second.item()],
                              f'{first.reference} and {second.reference} bodies overlap by '
                              f'{_mm(x1 - x0):g} x {_mm(y1 - y0):g} mm',
                              f'Move {second.reference} clear of {first.reference}', ((x0 + x1) // 2, (y0 + y1) // 2))

    def _label_in_symbol(self, model):
        rule, sheet = 'label-in-symbol/1', model['meta']
        for label in model['labels']:
            x, y = label['at']
            for sym in model['symbols']:
                if sym.body and sym.body[0] < x < sym.body[2] and sym.body[1] < y < sym.body[3]:
                    self.emit(rule, sheet, [dict(kind=label['kind'], text=label['text'], uuid=label['uuid']), sym.item()],
                              f'{label["kind"]} {label["text"]!r} is inside the body of {sym.reference}',
                              f'Move {label["kind"]} {label["text"]!r} outside {sym.reference}', label['at'])

    def _groups(self, model):
        """Wire conductors joined at shared endpoints, junctions, labels and sheet pins."""
        union, points = _Union(), set()
        for index, wire in enumerate(model['wires']):
            for end in (wire['a'], wire['b']):
                union.join(('w', index), ('p', end))
                points.add(end)
        attach = [j['at'] for j in model['junctions']] + [l['at'] for l in model['labels']] + \
                 [p['at'] for s in model['sheet_symbols'] for p in s['pins']]
        for point in attach:
            for index, wire in enumerate(model['wires']):
                if _on_segment(point, wire['a'], wire['b']):
                    union.join(('w', index), ('p', point))
                    points.add(point)
        names = {}
        def add(point, name):
            if point in points and name:
                names.setdefault(union.find(('p', point)), set()).add(_base(name))
        for sym in model['symbols']:
            for pin in sym.pins:
                add(pin['point'], pin['net'])
        for label in model['labels']:
            add(label['at'], label['text'])
        for sheet_symbol in model['sheet_symbols']:
            for pin in sheet_symbol['pins']:
                add(pin['at'], pin['name'])
        return [names.get(union.find(('w', i)), set()) for i in range(len(model['wires']))]

    def _wire_crosses_pin(self, model):
        rule, sheet = 'wire-crosses-pin/1', model['meta']
        # Hidden pins are not drawn; a power port is, so its hidden pin still counts.
        candidates = [(s, p) for s in model['symbols'] if s.proven(rule) for p in s.pins
                      if s.power or not p['hidden']]
        if len(candidates) * len(model['wires']) > MAX_WORK:
            return self.missing(rule, sheet, 'wire/pin comparison work limit')
        groups = self._groups(model)
        for index, wire in enumerate(model['wires']):
            box = _box([wire['a'], wire['b']])
            for sym, pin in candidates:
                a, b = pin['point'], pin['inner']
                if max(a[0], b[0]) < box[0] or min(a[0], b[0]) > box[2] or \
                        max(a[1], b[1]) < box[1] or min(a[1], b[1]) > box[3]:
                    continue
                if a in (wire['a'], wire['b']) or not _touch(a, b, wire['a'], wire['b']):
                    continue
                if _base(pin['net']) in groups[index]:
                    continue
                wire_net = ', '.join(sorted(groups[index])) or 'an unnamed net'
                self.emit(rule, sheet, [dict(kind='wire', uuid=wire['uuid']), dict(sym.item(), pin=pin['number'])],
                          f'wire on {wire_net} crosses {sym.reference} pin {pin["number"]} on {pin["net"]}; '
                          'KiCad does not connect a pin on a wire interior',
                          f'Reroute the wire around {sym.reference} pin {pin["number"]} or move {sym.reference}', a)

    def _wire_overlap(self, model):
        rule, sheet = 'wire-overlap/1', model['meta']
        lines = {}
        for wire in model['wires']:
            (ax, ay), (bx, by) = wire['a'], wire['b']
            dx, dy = bx - ax, by - ay
            if dx == dy == 0:
                continue
            g = math.gcd(dx, dy)
            dx, dy = dx // g, dy // g
            if dx < 0 or (dx == 0 and dy < 0):
                dx, dy = -dx, -dy
            key = (dx, dy, dy * ax - dx * ay)
            t0, t1 = sorted((ax * dx + ay * dy, bx * dx + by * dy))
            lines.setdefault(key, []).append((t0, t1, wire))
        for spans in lines.values():
            spans.sort(key=lambda s: (s[0], s[1], s[2]['uuid'] or ''))
            for i, (t0, t1, first) in enumerate(spans):
                for u0, u1, second in spans[i + 1:]:
                    if u0 >= t1:
                        break
                    self.emit(rule, sheet, [dict(kind='wire', uuid=first['uuid']), dict(kind='wire', uuid=second['uuid'])],
                              'collinear wires overlap along part of their length',
                              'Merge or shorten the overlapping wires so they meet only at endpoints',
                              second['a'])

    # --- placement topology

    def _two_pin(self, model, rule, cls=None):
        for sym in model['symbols']:
            if sym.power or len(sym.pins) != 2 or not sym.proven(rule):
                continue
            if cls is not None and self.classes.get(sym.lib_id, {}).get('class') != cls:
                continue
            yield sym

    def _two_pin_rails(self, model):
        rule, sheet = 'two-pin-rails/1', model['meta']
        for sym in self._two_pin(model, rule):
            first, second = sym.pins
            for top, bottom in ((first, second), (second, first)):
                if top['net'] in self.power and bottom['net'] in self.ground and top['point'][1] > bottom['point'][1]:
                    self.emit(rule, sheet, [sym.item()],
                              f'{sym.reference} has its {top["net"]} pin below its {bottom["net"]} pin',
                              f'Rotate or flip {sym.reference} so the power pin is above the ground pin', top['point'])

    def _dividers(self, model, rule):
        pairs = []
        resistors = list(self._two_pin(model, rule, 'resistor'))
        for supply in resistors:
            for ground in resistors:
                if supply is ground:
                    continue
                s_rail = [p for p in supply.pins if p['net'] in self.power]
                g_rail = [p for p in ground.pins if p['net'] in self.ground]
                if len(s_rail) != 1 or len(g_rail) != 1:
                    continue
                s_mid = next(p for p in supply.pins if p is not s_rail[0])['net']
                g_mid = next(p for p in ground.pins if p is not g_rail[0])['net']
                if s_mid == g_mid and s_mid not in self.power | self.ground:
                    pairs.append((supply, ground, s_mid))
        return pairs

    def _divider_order(self, model):
        rule, sheet = 'divider-order/1', model['meta']
        for supply, ground, mid in self._dividers(model, rule):
            if supply.centre()[1] > ground.centre()[1]:
                self.emit(rule, sheet, [supply.item(), ground.item()],
                          f'divider on {mid}: supply resistor {supply.reference} is drawn below ground '
                          f'resistor {ground.reference}',
                          f'Swap {supply.reference} and {ground.reference} so the supply resistor is on top',
                          supply.pins[0]['point'])

    def _pull_resistor(self, model):
        rule, sheet = 'pull-resistor/1', model['meta']
        members = {id(s) for pair in self._dividers(model, rule) for s in pair[:2]}
        for sym in self._two_pin(model, rule, 'resistor'):
            if id(sym) in members:
                continue
            rails = [p for p in sym.pins if p['net'] in self.power | self.ground]
            if len(rails) != 1:
                continue
            rail = rails[0]
            other = next(p for p in sym.pins if p is not rail)
            up = rail['net'] in self.power
            if (up and rail['point'][1] > other['point'][1]) or (not up and rail['point'][1] < other['point'][1]):
                self.emit(rule, sheet, [sym.item()],
                          f'pull-{"up" if up else "down"} {sym.reference} has its {rail["net"]} pin '
                          f'{"below" if up else "above"} the {other["net"]} pin',
                          f'Flip {sym.reference} so its rail pin points toward {rail["net"]}', rail['point'])

    def _regulator_capacitors(self, model):
        rule, sheet = 'regulator-capacitors/1', model['meta']
        caps = list(self._two_pin(model, rule, 'capacitor'))
        for reg in model['symbols']:
            entry = self.classes.get(reg.lib_id, {})
            if entry.get('class') != 'regulator' or not reg.proven(rule):
                continue
            pins = {p['number']: p for p in reg.pins}
            if entry['input'] not in pins or entry['output'] not in pins:
                self.missing(rule, sheet, f'{reg.reference}: declared regulator input/output pin not in the symbol')
                continue
            vin, vout = pins[entry['input']], pins[entry['output']]
            dx, dy = vout['point'][0] - vin['point'][0], vout['point'][1] - vin['point'][1]
            if dx == dy == 0:
                self.missing(rule, sheet, f'{reg.reference}: input and output pins coincide')
                continue
            axis = 0 if abs(dx) >= abs(dy) else 1
            mid = (vin['point'][axis] + vout['point'][axis]) / 2
            sign = 1 if vout['point'][axis] > vin['point'][axis] else -1
            for cap in caps:
                nets = {p['net'] for p in cap.pins}
                for pin, side, name in ((vin, -1, 'input'), (vout, 1, 'output')):
                    if pin['net'] in nets and nets - {pin['net']} <= self.ground and len(nets) == 2:
                        if (cap.centre()[axis] - mid) * sign * side <= 0:
                            self.emit(rule, sheet, [cap.item(), dict(reg.item(), pin=pin['number'])],
                                      f'{name} capacitor {cap.reference} ({pin["net"]}) is drawn on the '
                                      f'{"output" if side < 0 else "input"} side of {reg.reference}',
                                      f'Move {cap.reference} beside {reg.reference} pin {pin["number"]} ({pin["name"]})',
                                      cap.pins[0]['point'])

    def _led_chain_order(self, model):
        rule, sheet = 'led-chain-order/1', model['meta']
        leds = {s.native: s for s in self._two_pin(model, rule, 'led')}
        if not leds:
            return
        links = {}
        for net in self.graph['nets'].values():
            refs = [ref for ref, _ in net['terminals']]
            if len(refs) == 2 and all(r in leds for r in refs) and refs[0] != refs[1] and \
                    net['name'] not in self.power | self.ground:
                for a, b in (refs, refs[::-1]):
                    links.setdefault(a, set()).add(b)
        seen = set()
        for start in sorted(leds):
            if start in seen or len(links.get(start, ())) > 1:
                continue
            chain, prev = [start], None
            while True:
                nxt = sorted(links.get(chain[-1], set()) - {prev} - set(chain))
                if len(nxt) != 1:
                    break
                prev = chain[-1]
                chain.append(nxt[0])
            seen.update(chain)
            if len(chain) < 3:
                continue
            centres = [leds[r].centre() for r in chain]
            axis = 0 if abs(centres[-1][0] - centres[0][0]) >= abs(centres[-1][1] - centres[0][1]) else 1
            steps = [b[axis] - a[axis] for a, b in zip(centres, centres[1:])]
            if not (all(s > 0 for s in steps) or all(s < 0 for s in steps)):
                names = ' -> '.join(leds[r].reference for r in chain)
                self.emit(rule, sheet, [leds[r].item() for r in chain],
                          f'series LED chain {names} is not drawn in connected order',
                          f'Reorder the LEDs along one axis as {names}', leds[chain[0]].pins[0]['point'])

    # --- naming and presentation

    def _rails(self):
        rails = {_base(n) for n in self.power | self.ground}
        for model in self.sheets:
            for sym in model['symbols']:
                if sym.power:
                    rails.add(_base(sym.props.get('Value', ('', False))[0]))
        return rails - {''}

    def _rail_label(self, model):
        rule, sheet = 'rail-label/1', model['meta']
        rails = self._rails()
        for label in model['labels']:
            if label['kind'] == 'hierarchical_label' and label['text'] in rails:
                self.emit(rule, sheet, [dict(kind=label['kind'], text=label['text'], uuid=label['uuid'])],
                          f'rail {label["text"]} enters the sheet through a hierarchical label',
                          f'Replace hierarchical label {label["text"]!r} (and its parent sheet pin) with '
                          f'{label["text"]} power symbols', label['at'])

    def _on_grid(self, model):
        rule, sheet, grid = 'on-grid/1', model['meta'], self.profile['grid_nm']
        def check(kind, uuid, points, extra=None):
            bad = [p for p in points if p[0] % grid or p[1] % grid]
            if bad:
                item = dict(kind=kind, uuid=uuid, **(extra or {}))
                self.emit(rule, sheet, [item], f'{kind} {uuid} is off the {_mm(grid):g} mm grid at '
                          + ', '.join(f'({_mm(x):g}, {_mm(y):g})' for x, y in bad),
                          f'Snap {kind} {uuid} to the {_mm(grid):g} mm grid', bad[0])
        for sym in model['symbols']:
            check('symbol', sym.uuid, [(sym.x, sym.y)], dict(reference=sym.reference))
        for wire in model['wires']:
            check('wire', wire['uuid'], [wire['a'], wire['b']])
        for kind in ('junctions', 'no_connects'):
            for node in model[kind]:
                check(kind[:-1], node['uuid'], [node['at']])
        for label in model['labels']:
            check(label['kind'], label['uuid'], [label['at']], dict(text=label['text']))
        for node in model['sheet_symbols']:
            (x, y), (w, h) = node['at'], node['size']
            check('sheet', node['uuid'], [(x, y), (x + w, y + h)], dict(name=node['name']))
            for pin in node['pins']:
                check('sheet_pin', pin['uuid'], [pin['at']], dict(name=pin['name']))

    def _hidden_properties(self, model):
        rule, sheet = 'hidden-properties/1', model['meta']
        for sym in model['symbols']:
            if sym.reference.startswith('#'):
                continue
            for key in self.profile['hidden_properties']:
                value, hidden = sym.props.get(key, ('', True))
                if value.strip() and not hidden:
                    self.emit(rule, sheet, [dict(sym.item(), property=key)],
                              f'{sym.reference} shows its internal {key} property',
                              f'Hide the {key} property of {sym.reference}', (sym.x, sym.y))

    def _reference_visible(self, model):
        rule, sheet = 'reference-visible/1', model['meta']
        for sym in model['symbols']:
            value, hidden = sym.props.get('Reference', ('', True))
            if not sym.reference.startswith('#') and (hidden or not value.strip()):
                self.emit(rule, sheet, [sym.item()], f'{sym.reference} has no visible reference designator',
                          f'Show the Reference property of {sym.reference}', (sym.x, sym.y))

    def _reference_prefix(self, model):
        rule, sheet = 'reference-prefix/1', model['meta']
        for sym in model['symbols']:
            if not sym.proven(rule) or sym.lib_reference is None:
                if sym.proven(rule):
                    self.missing(rule, sheet, f'{sym.reference}: library symbol has no Reference property')
                continue
            expected = re.match(r'#?[A-Za-z_]*', sym.lib_reference).group(0)
            actual = re.match(r'#?[A-Za-z_]*', sym.reference).group(0)
            if expected and actual != expected:
                self.emit(rule, sheet, [sym.item()],
                          f'{sym.reference} uses prefix {actual!r}; its library symbol class is {expected!r}',
                          f'Re-annotate {sym.reference} with the {expected} prefix', (sym.x, sym.y))

    def _title_block(self, model):
        rule, sheet = 'title-block/1', model['meta']
        values = {}
        for block in model['title']:
            for node in block[1:]:
                if isinstance(node, list) and len(node) == 2 and isinstance(node[1], str):
                    values[node[0]] = node[1]
        for key in self.profile['title_block']:
            if not values.get(key, '').strip():
                self.emit(rule, sheet, [dict(kind='title_block', field=key)],
                          f'title block field {key!r} is empty', f'Fill in the title block {key}')

    def _diff_pair_naming(self):
        rule = 'diff-pair-naming/1'
        names = {}
        for net in (self.graph or {'nets': {}})['nets'].values():
            names.setdefault(_base(net['name']), []).append(dict(kind='net', name=net['name']))
        for model in self.sheets:
            for label in model['labels']:
                names.setdefault(label['text'], []).append(dict(kind=label['kind'], text=label['text'],
                                                                 uuid=label['uuid'], sheet=model['meta']['names']))
        root = self.sheets[0]['meta']
        for name in sorted(names):
            for positive, negative in self.profile['diff_pairs']:
                for own, other in ((positive, negative), (negative, positive)):
                    if name.endswith(own) and len(name) > len(own):
                        partner = name[:-len(own)] + other
                        if partner not in names:
                            self.emit(rule, root, names[name],
                                      f'{name} has no {partner} partner',
                                      f'Rename {name} or add its {partner} partner with a matched suffix')


def lint(sheets, graph, lint_profile, inventory_error=None):
    """Pure evaluation; returns findings, coverage gaps and the outcome.

    ``graph`` is the strict native inventory, or None when it was refused
    (``inventory_error``); every net-dependent rule is then missing coverage.
    """
    findings, coverage = _Lint(sheets, graph, lint_profile, inventory_error).run()
    if any(f['severity'] == 'defect' for f in findings):
        outcome = 'defect'
    elif coverage:
        outcome = 'unknown'
    else:
        outcome = 'info' if findings else 'pass'
    counts = {}
    for finding in findings:
        counts[finding['rule']] = counts.get(finding['rule'], 0) + 1
    return dict(outcome=outcome, findings=findings, coverage=coverage, counts=dict(sorted(counts.items())),
                sheets=[dict(path=s['path'], names=s['names'], file=s['file'], sha256=s['sha256']) for s in sheets])


def check_schematic_lint(ctx):
    from .checks import CheckError, verified_hierarchy
    from .datasheet_facts import prerequisite
    from .native_netlist import read_native_inventory
    from .netlist_parity import Unsupported
    from .storage import digest, read_json, write_json
    from xml.etree.ElementTree import ParseError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        specs = ctx.scope['checks']
        prior = next((s for s in specs[:specs.index(ctx.spec)]
                      if s['id'] == ctx.params['netlist_check'] and s['kind'] == 'netlist'), None)
        if prior is None:
            raise ValueError('native netlist prerequisite must precede the schematic lint')
    except (ValueError, TypeError, KeyError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        xml = prerequisite(ctx, prior['id'], 'netlist', 'design.xml')
        producer = read_json(ctx.run_dir / 'checks' / f"{prior['id']}.json")
    except CheckError as exc:
        raise CheckError('failed', str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CheckError('failed', str(exc)) from exc
    graph, inventory_error = None, None
    try:
        # KiCad still exports a netlist around an unresolved symbol (no component,
        # invented pin nets); the strict reader refuses it. Geometry and naming
        # rules still run; every net rule becomes missing coverage.
        graph = read_native_inventory(xml)
        if len(graph['sheets']) != producer['details']['sheet_instances']:
            raise ValueError('native XML hierarchy differs from producer coverage')
    except (Unsupported, OSError, ValueError, TypeError, KeyError, ParseError) as exc:
        graph, inventory_error = None, str(exc)
    _, expected = verified_hierarchy(ctx)
    try:
        sheets = load_sheets(ctx.inputs, ctx.scope['schematic'])
        if len(sheets) != expected or (graph is not None and {s['path'] for s in sheets} != set(graph['sheets'])):
            raise ValueError('captured sheet instances differ from the native hierarchy')
        report = lint(sheets, graph, descriptor['profile'], inventory_error)
    except (LibraryError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise CheckError('malformed', f'schematic lint input unusable: {exc}') from exc
    report.update(schema=SCHEMA, candidate_id=ctx.manifest['candidate_id'], netlist_check=prior['id'],
                  native_xml_sha256=digest(xml), native_inventory='refused' if graph is None else 'complete',
                  profile=descriptor['profile'])
    write_json(ctx.work / 'lint.json', report)
    ctx.evidence('lint.json')
    ctx.result['details'].update(report)
    ctx.result.update(execution='ok', outcome=report['outcome'],
                      summary=f"schematic lint: {len(report['findings'])} finding(s), "
                              f"{len(report['coverage'])} coverage gap(s) over {len(sheets)} sheet instance(s)")
    ctx.result['limitations'].append(
        'Presentation lint over embedded lib_symbols and native net membership; style findings never '
        'establish an electrical defect unless the profile raises a rule to defect. Text extents, arcs, '
        'property positions and rendered fonts are not measured. Repeated sheet files are linted per instance.')
