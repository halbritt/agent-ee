"""JLCPCB fab package generation from the captured native board (AGENTEE-23).

Check kind `fab_export` runs the pinned `kicad-cli` inside the executor sandbox
against the captured `.kicad_pcb` and writes the staging layout fixed by the
fab lane interface contract v1 under `work/<check-id>/fab/`:

    gerbers/          one plot per enabled fab layer, Protel (JLC) extensions
    drill/            exactly one PTH and one NPTH Excellon file + Gerber X2 maps
    bom.csv           part-binding properties from the native XML netlist of a
                      preceding `netlist` check for the board's assembled
                      footprints, grouped by LCSC number
    cpl.csv           native position file rewritten to the JLCPCB CPL header,
                      without the parts the declared exclusion rule omits
    gerbers.zip       gerbers/ + drill/, deterministic member order and metadata
    pcb_summary.json  native `kicad-cli pcb export stats` JSON
    manifest.json     agent-ee.fab-manifest/1, written last and only on success

Assembled means board non-DNP and not omitted by the optional declared
`assembly_exclusion` rule (v1: `JLCPCB_Assembly=not_assembled`). The rule is a
check parameter, so it is part of the captured, hashed scope. A part is omitted
only when its netlist field and its board footprint property both carry the
marker; the omitted designators and their rule are listed in the manifest.
Without a declared rule, a populated part that carries the marker is refused.

An optional declared `bom_variant` (AGENTEE-66 composed with AGENTEE-126) is
validated by `kicad_quirks.validate_bom_variants` against the captured project
file before any kicad-cli command, then passed only to a native
`kicad-cli sch export bom --exclude-dnp --variant NAME` step; bom.csv is built
from that export. The CPL stays the default variant, so a variant that changes
the populated (assembled) set is refused by name. Without the parameter a
schematic declaring variant clauses is refused.

No rotation, offset or pin-1 policy is applied to the CPL; rotations are the
native position-file values. The separate `cpl_rotation` check (AGENTEE-72)
derives the corrected JLC CPL from this staging. Outcome `pass` means
only that every export succeeded, validated, agrees on assembled designators
and was hashed into a complete manifest. It does not establish DFM, part or
placement consistency (AGENTEE-73) or orderability. Missing, empty or unparseable
output, an unsupported board feature or a part without a binding is unknown coverage;
a native nonzero exit is execution `failed`. Neither leaves a `fab/` package:
the partial staging is renamed `fab.incomplete/` and no manifest is written.
"""

import csv
import hashlib
import io
import json
import math
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.fab-manifest/1'
TEMPLATE_SCHEMA = 'agent-ee.jlc-templates/1'
JLC_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / 'fixtures/fab-export'
BOM_HEADER = 'Designator,Footprint,Quantity,Comment,Manufacturer,Partnumber,"LCSC Part #"'
BOM_COLUMNS = ['Designator', 'Footprint', 'Quantity', 'Comment', 'Manufacturer', 'Partnumber', 'LCSC Part #']
BOM_HEADER_SHA256 = '6d7edfe25e808edf40eedc3230e306b58e6e04cc91840c54f191c547f489b850'
BOM_TEMPLATE_SHA256 = '52543dd3f30213f999e06bae752e3156f1a9d28ae9bed7a1ff80045dedc6a424'
CPL_HEADER = 'Designator,Mid X,Mid Y,Layer,Rotation'
CPL_COLUMNS = ['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation']
CPL_HEADER_SHA256 = 'a0c9c7a05b6e7da703ac94f05571e80788acd7f407de2447a2d3de5603988d0f'
CPL_TEMPLATE_SHA256 = 'b0a48546b45eb5d72bb22499b5ba3e50311d45cb66a0ca94674027754b8af9a1'
POS_HEADER = ['Ref', 'Val', 'Package', 'PosX', 'PosY', 'Rot', 'Side']
SANDBOX_PATH = '/usr/local/bin:/usr/bin:/bin'
LCSC_RE = re.compile(r'^C[1-9][0-9]*$')
REF_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
NUMBER_RE = re.compile(r'^-?\d+(?:\.\d+)?$')
# Non-copper fab layers: (board layer name, Protel extension, X2 file function).
FAB_LAYERS = (('F.Mask', 'gts', 'Soldermask,Top'), ('B.Mask', 'gbs', 'Soldermask,Bot'),
              ('F.Paste', 'gtp', 'Paste,Top'), ('B.Paste', 'gbp', 'Paste,Bot'),
              ('F.SilkS', 'gto', 'Legend,Top'), ('B.SilkS', 'gbo', 'Legend,Bot'),
              ('Edge.Cuts', 'gm1', 'Profile,NP'))
# Deterministic ASCII substitutions applied before NFKD decomposition.
ASCII_MAP = {'\u00b5': 'u', '\u03bc': 'u', '\u2126': 'ohm', '\u03a9': 'ohm',
             '\u00b1': '+/-', '\u00b0': 'deg'}
FIXED_TIME = '1970-01-01T00:00:00'
TIMESTAMP_RE = re.compile(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d')
GERBER_TIMES = (
    (re.compile(r'^(%TF\.CreationDate,)[^*\n]*(\*%)$', re.M), r'\g<1>' + FIXED_TIME + r'+00:00\2'),
    (re.compile(r'^(G04 Created by KiCad \([^)\n]*\) date )\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\*)$', re.M),
     r'\g<1>1970-01-01 00:00:00\2'))
DRILL_TIMES = (
    (re.compile(r'^(; DRILL file .* date )\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$', re.M), r'\g<1>' + FIXED_TIME),
    (re.compile(r'^(; #@! TF\.CreationDate,)\S+$', re.M), r'\g<1>' + FIXED_TIME + '+00:00'))
STATS_TIME = re.compile(r'^(\s*"date": ")\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(",?)$', re.M)
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
STEP_CEILING = 600
TOKEN_RE = re.compile(r'\s+|;[^\n]*|\(|\)|"(?:[^"\\]|\\.)*"|[^\s();"]+')
# Declared assembly exclusion rules: rule text -> (symbol/footprint field, marker value).
# The rung-1 generator writes this field on hand-soldered parts (AGENTEE-122).
ASSEMBLY_RULES = {'JLCPCB_Assembly=not_assembled': ('JLCPCB_Assembly', 'not_assembled')}
RULE_FIELDS = frozenset(field for field, _ in ASSEMBLY_RULES.values())
# Ungrouped native BOM columns read for a declared bom_variant (field names double as labels).
VARIANT_BOM_FIELDS = ('Reference', 'Value', 'Footprint', 'LCSC', 'Manufacturer', 'Partnumber', 'PartNumber')


def _error(execution, message):
    from .checks import CheckError
    return CheckError(execution, message)


# ------------------------------------------------------------ pure validation

def validate_params(params):
    """v1 interprets `netlist_check`, an optional `assembly_exclusion` rule and an optional `bom_variant`.

    Layer and output overrides are refused; the rule must be one of
    ASSEMBLY_RULES verbatim; the variant must be a nonempty name (its
    registration is checked against the captured project file at run time).
    """
    if (not isinstance(params, dict) or 'netlist_check' not in params or
            not set(params) <= {'netlist_check', 'assembly_exclusion', 'bom_variant', 'probe_manifest', 'copper_features'}):
        raise ValueError('fab_export/1 params must be {"netlist_check"} plus optional '
                         '"assembly_exclusion", "bom_variant", "probe_manifest" and "copper_features"; layer and output overrides are refused in v1')
    if not isinstance(params['netlist_check'], str) or not params['netlist_check']:
        raise ValueError('netlist_check must name the preceding netlist check')
    rule = params.get('assembly_exclusion')
    if 'assembly_exclusion' in params and (not isinstance(rule, str) or rule not in ASSEMBLY_RULES):
        raise ValueError(f'assembly_exclusion {rule!r} is not one of {sorted(ASSEMBLY_RULES)}')
    declared_copper_features(params)
    variant = params.get('bom_variant')
    if 'bom_variant' in params and (not isinstance(variant, str) or not variant or variant != variant.strip()):
        raise ValueError(f'bom_variant {variant!r} must be a nonempty variant name')
    return params


# Binding aliases are checked on both native symbols and footprint properties.
def orderable_field(name):
    name = name or ''
    compact = re.sub(r'[^a-z0-9]', '', name.lower())
    if compact in {'lcsc', 'mpn', 'pn', 'partnumber'}:
        return True
    # Keep compact legacy spellings, while allowing separators and extra tokens
    # in manufacturer/distributor binding fields. Assembly policy is not a binding.
    sources = {'manufacturer', 'mfr', 'mfg', 'lcsc', 'jlcpcb', 'digikey', 'mouser'}
    bindings = {'pn', 'part', 'number', 'mpn', 'stock', 'sku', 'code'}
    tokens = set(re.findall(r'[a-z0-9]+',
                 re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name).lower()
                 .replace('digi-key', 'digikey')))
    return bool((tokens & sources and tokens & bindings) or
                any(compact == source + suffix for source in sources
                    for suffix in ('pn', 'mpn', 'part', 'partnumber', 'number')))


COPPER_FIELDS = {'Reference', 'Value', 'Footprint', 'Datasheet', 'Description',
                 'ki_keywords', 'ki_description', 'ki_fp_filters', 'ki_locked'}


def copper_field_allowed(name, value):
    """Qualify declaration metadata without changing general part binding."""
    return (isinstance(name, str) and isinstance(value, str) and
            (name in COPPER_FIELDS or value.strip() in {'', '~'}))


def declared_copper_features(params):
    refs = params.get('copper_features', [])
    if (not isinstance(refs, list) or ('copper_features' in params and not refs) or
            any(not isinstance(ref, str) or not REF_RE.fullmatch(ref) for ref in refs) or
            len(set(refs)) != len(refs)):
        raise ValueError('copper_features must be a nonempty list of unique plain designators')
    return set(refs)


def copper_schematic_problems(data, refs):
    """Include fields hidden by native default-netlist or non-BOM/variant export."""
    if not refs:
        return []
    from .symbol_library import parse_sexpr
    root = parse_sexpr(data)
    if root[0] != 'kicad_sch':
        raise ValueError('not a kicad_sch file')
    problems = []
    for symbol in _lists(root, 'symbol'):
        nodes, stack = [], [symbol]
        while stack:
            node = stack.pop()
            nodes.append(node)
            stack.extend(n for n in node[1:] if isinstance(n, list) and n)
        references = {n[1] for n in nodes if n[0] == 'reference' and len(n) == 2 and isinstance(n[1], str)}
        references.update(p[2] for p in _lists(symbol, 'property')
                          if len(p) > 2 and p[1] == 'Reference' and isinstance(p[2], str))
        for ref in sorted(refs & references, key=natural_key):
            for node in nodes:
                if node[0] == 'property':
                    if len(node) < 3:
                        problems.append(f'copper feature {ref}: unqualified schematic field')
                        continue
                    pairs = [(node[1], node[2])]
                elif node[0] == 'field':
                    names, values = _lists(node, 'name'), _lists(node, 'value')
                    if len(names) != 1 or len(values) != 1 or len(names[0]) != 2 or len(values[0]) != 2:
                        problems.append(f'copper feature {ref}: unqualified schematic variant field')
                        continue
                    pairs = [(names[0][1], values[0][1])]
                else:
                    continue
                for name, value in pairs:
                    if not isinstance(name, str) or not isinstance(value, str):
                        problems.append(f'copper feature {ref}: unqualified schematic field')
                    elif not copper_field_allowed(name, value):
                        kind = 'orderable' if orderable_field(name) else 'nonstandard'
                        problems.append(f'copper feature {ref}: has {kind} schematic/variant field {name}')
    return sorted(set(problems))


def copper_pad(node):
    layers = _lists(node, 'layers')
    names = layers[0][1:] if len(layers) == 1 else []
    allowed_layers = {'F.Cu', 'B.Cu', '*.Cu', 'F.Mask', 'B.Mask', '*.Mask',
                      'F.Paste', 'B.Paste', '*.Paste'}
    if (len(node) < 4 or node[2] != 'smd' or not names or
            any(not isinstance(n, str) or n not in allowed_layers for n in names) or
            not any(n.endswith('.Cu') for n in names) or _lists(node, 'drill') or _lists(node, 'padstack')):
        return False
    primitives = _lists(node, 'primitives')
    if node[3] != 'custom':
        return node[3] in {'rect', 'roundrect', 'circle', 'oval', 'trapezoid'} and not primitives
    if len(primitives) != 1 or len(primitives[0]) < 2:
        return False
    # Custom solder-jumper copper inherits the pad layers. No layer overrides
    # or unrecognised physical objects are admitted inside its primitives.
    return all(isinstance(p, list) and p and p[0] in {
        'gr_line', 'gr_arc', 'gr_circle', 'gr_rect', 'gr_poly', 'gr_curve'} and
        not _lists(p, 'layer') and not _lists(p, 'layers') for p in primitives[0][1:])


def copper_feature_problems(data, refs, components=None):
    """Qualify only explicitly declared copper; never infer a component exemption.

    Both fab gates replay this against captured board bytes and native bindings.
    Unknown physical footprint content is refused rather than silently ignored.
    """
    if not refs:
        return []
    from .symbol_library import parse_sexpr
    root = parse_sexpr(data)
    if root[0] != 'kicad_pcb':
        raise ValueError('not a kicad_pcb file')
    by_ref = {}
    for fp in _lists(root, 'footprint'):
        for prop in _lists(fp, 'property'):
            if len(prop) > 2 and prop[1] == 'Reference' and isinstance(prop[2], str):
                by_ref.setdefault(prop[2], []).append(fp)
    problems = []
    metadata = {'layer', 'at', 'uuid', 'tstamp', 'path', 'property', 'attr',
                'descr', 'tags', 'sheetname', 'sheetfile', 'locked',
                'duplicate_pad_numbers_are_jumpers', 'solder_mask_margin',
                'solder_paste_margin', 'solder_paste_ratio', 'clearance',
                'zone_connect', 'thermal_width', 'thermal_gap', 'embedded_fonts'}
    graphics = {'fp_text', 'fp_text_box', 'fp_line', 'fp_rect', 'fp_circle', 'fp_arc', 'fp_poly'}
    graphic_layers = {'F.SilkS', 'B.SilkS', 'F.Fab', 'B.Fab'}
    for ref in sorted(refs, key=natural_key):
        reasons = []
        fps = by_ref.get(ref, [])
        if len(fps) != 1:
            problems.append(f'copper feature {ref} needs exactly one board footprint')
            continue
        fp = fps[0]
        attrs = {a for node in _lists(fp, 'attr') for a in node[1:] if isinstance(a, str)}
        if not {'exclude_from_bom', 'exclude_from_pos_files'} <= attrs:
            reasons.append('needs both native BOM and position exclusions')
        pads = _lists(fp, 'pad')
        if not pads:
            reasons.append('has no copper pads')
        for node in fp[2:]:
            if not isinstance(node, list) or not node:
                reasons.append('unqualified footprint content')
                continue
            head = node[0]
            if head == 'pad':
                if not copper_pad(node):
                    reasons.append('pad is not qualified surface copper/mask/paste')
            elif head in graphics or head == 'property':
                layers = _lists(node, 'layer')
                if head in graphics or layers:
                    if len(layers) != 1 or len(layers[0]) != 2 or layers[0][1] not in graphic_layers:
                        reasons.append('graphics are outside silk/fab layers')
                if head == 'property':
                    if len(node) < 3 or not all(isinstance(value, str) for value in node[1:3]):
                        reasons.append('unqualified footprint content property')
                    elif not copper_field_allowed(node[1], node[2]):
                        kind = 'orderable' if orderable_field(node[1]) else 'nonstandard'
                        reasons.append(f'has {kind} footprint field {node[1]}')
            elif head not in metadata:
                reasons.append(f'unqualified footprint content {head}')
        if components is not None:
            comp = components.get(ref)
            if comp is None or 'exclude_from_board' in comp['excluded']:
                reasons.append('needs a native on-board component')
            elif 'copper_fields' in comp:
                if any(not copper_field_allowed(name, value) for name, value in comp['copper_fields']):
                    reasons.append('has an unqualified native field')
            elif comp.get('orderable') or any(comp.get(k) for k in ('lcsc', 'Partnumber', 'PartNumber')):
                reasons.append('has an orderable native MPN/LCSC field')
            elif comp.get('manufacturer', '').strip() not in {'', '~'}:
                reasons.append('has an unqualified native field')
        problems.extend(f'copper feature {ref}: {reason}' for reason in sorted(set(reasons)))
    return problems


def capability(params):
    validate_params(params)
    return {'predicates': ['fab-export:jlcpcb-package/1']}


def natural_key(text):
    return [(0, int(part), '') if part.isdigit() else (1, 0, part)
            for part in re.split(r'(\d+)', text) if part]


def ascii_text(value, label):
    """Deterministic ASCII form of a CSV value; unmappable text is refused."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f'{label} contains control characters')
    mapped = ''.join(ASCII_MAP.get(ch, ch) for ch in value)
    decomposed = ''.join(ch for ch in unicodedata.normalize('NFKD', mapped)
                         if not unicodedata.combining(ch))
    if not decomposed.isascii():
        bad = sorted({ch for ch in decomposed if not ch.isascii()})
        raise ValueError(f'{label} {value!r} has characters without a deterministic ASCII form: {bad}')
    return decomposed


def scan_sexpr(data, want, *, limit=256 * 1024 * 1024):
    """Selectively materialise one KiCad S-expression without a node ceiling.

    `want(path)` receives the tuple of list heads from the root and decides
    whether that list is kept. Skipped lists are still tokenised and balanced.
    Returns (tree, heads), where heads is every list head seen at any depth.
    """
    if not isinstance(data, bytes) or len(data) > limit:
        raise ValueError('native file must be bytes within the size limit')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError('native file is not UTF-8') from exc
    stack, root, heads, pos = [], None, set(), 0
    for match in TOKEN_RE.finditer(text):
        if match.start() != pos:
            raise ValueError(f'invalid token at offset {pos}')
        pos = match.end()
        token = match.group(0)
        if token[0].isspace() or token[0] == ';':
            continue
        if token == '(':
            if root is not None:
                raise ValueError('more than one root expression')
            if stack and stack[-1][1] is None:
                raise ValueError('list without a head atom')
            if len(stack) >= 256:
                raise ValueError('nesting limit exceeded')
            stack.append([[], None, None, None])  # node, head, keep, path
            continue
        if token == ')':
            if not stack:
                raise ValueError('unmatched closing parenthesis')
            node, head, keep, _ = stack.pop()
            if head is None:
                raise ValueError('empty list')
            if not stack:
                root = node
            elif keep and stack[-1][2]:
                stack[-1][0].append(node)
            continue
        if not stack:
            raise ValueError('atom outside the root expression')
        if token[0] == '"':
            atom = re.sub(r'\\(.)', lambda m: {'n': '\n', 'r': '\r', 't': '\t'}.get(m.group(1), m.group(1)),
                          token[1:-1])
        else:
            atom = token
        entry = stack[-1]
        if entry[1] is None:
            entry[1] = atom
            heads.add(atom)
            parent_keep = stack[-2][2] if len(stack) > 1 else True
            entry[3] = (stack[-2][3] if len(stack) > 1 else ()) + (atom,)
            entry[2] = parent_keep and want(entry[3])
        if entry[2]:
            entry[0].append(atom)
    if pos != len(text):
        raise ValueError('unterminated quoted string or trailing data')
    if stack or root is None:
        raise ValueError('incomplete S-expression')
    return root, heads


def _board_want(path):
    if len(path) == 1:
        return path[0] == 'kicad_pcb'
    if path[1] in ('layers', 'variants', 'version'):
        return True
    if path[1] != 'footprint':
        return False
    return len(path) == 2 or path[2] in ('layer', 'property', 'attr', 'variant')


def _lists(node, head):
    return [x for x in node[1:] if isinstance(x, list) and x and x[0] == head]


def board_facts(data, probes=None, copper_features=()):
    """Layer table and footprint inventory of a captured board.

    Raises ValueError for unparseable bytes and returns `unsupported` reasons
    for features this v1 exporter cannot represent honestly.
    """
    root, heads = scan_sexpr(data, _board_want)
    from .probe_features import PROPERTY, validate_board
    probe_refs = validate_board(data, probes or {}) if probes or PROPERTY.encode() in data else set()
    if root[0] != 'kicad_pcb':
        raise ValueError('not a kicad_pcb file')
    unsupported = copper_feature_problems(data, copper_features)
    layer_tables = _lists(root, 'layers')
    if len(layer_tables) != 1:
        raise ValueError('board needs exactly one layers table')
    names = []
    for entry in layer_tables[0][1:]:
        if not isinstance(entry, list) or len(entry) < 3:
            raise ValueError('malformed layer table entry')
        names.append(entry[1])
    copper = [n for n in names if n.endswith('.Cu')]
    count = len(copper)
    expected = ['F.Cu'] + [f'In{i}.Cu' for i in range(1, count - 1)] + ['B.Cu']
    if count < 2 or count % 2 or count > 32 or sorted(copper, key=natural_key) != sorted(expected, key=natural_key):
        unsupported.append(f'copper layers {copper} are not F.Cu, contiguous In*.Cu and B.Cu '
                           'with an even count from 2 to 32')
    missing = [name for name, _, _ in FAB_LAYERS if name not in names]
    if missing:
        unsupported.append(f'board layer table lacks fab layers {missing}')
    if 'variant' in heads:
        unsupported.append('board declares assembly variants; v1 exports only an unvaried board')
    footprints, seen = [], set()
    for fp in _lists(root, 'footprint'):
        properties = _lists(fp, 'property')
        if any(len(p) > 1 and (not isinstance(p[1], str) or
               (p[1] == 'Reference' and (len(p) < 3 or not isinstance(p[2], str))))
               for p in properties):
            raise ValueError(f'footprint {fp[1:2]} has unqualified footprint content property')
        refs = [p[2] for p in properties if len(p) > 2 and p[1] == 'Reference']
        layer = _lists(fp, 'layer')
        attrs = set(a for attr in _lists(fp, 'attr') for a in attr[1:] if isinstance(a, str))
        if len(refs) != 1 or len(layer) != 1 or len(layer[0]) != 2:
            raise ValueError(f'footprint {fp[1:2]} lacks exactly one Reference and layer')
        ref, side = refs[0], layer[0][1]
        if side not in ('F.Cu', 'B.Cu'):
            raise ValueError(f'footprint {ref} is on {side}, not F.Cu or B.Cu')
        dnp = 'dnp' in attrs
        fields = [p[1:3] for p in _lists(fp, 'property') if len(p) > 2 and p[1] in RULE_FIELDS]
        if len({name for name, _ in fields}) != len(fields):
            raise ValueError(f'footprint {ref} repeats an assembly rule property')
        record = {'ref': ref, 'side': 'Top' if side == 'F.Cu' else 'Bottom', 'dnp': dnp,
                  'attributes': sorted(attrs), 'fields': dict(fields)}
        if ref in probe_refs:
            record['probe'] = True
        footprints.append(record)
        if not REF_RE.match(ref):
            unsupported.append(f'footprint reference {ref!r} is not a plain designator')
            continue
        if ref in seen:
            unsupported.append(f'duplicate footprint reference {ref}')
        seen.add(ref)
        excluded = attrs & {'board_only', 'exclude_from_bom', 'exclude_from_pos_files'}
        mechanical = excluded == {'board_only', 'exclude_from_bom', 'exclude_from_pos_files'}
        if excluded and not dnp and ref not in probe_refs and not mechanical and ref not in copper_features:
            unsupported.append(f'populated footprint {ref} carries {sorted(excluded)}; '
                               'v1 refuses non-DNP exclusions')
    populated = sorted((f['ref'] for f in footprints if not f['dnp']), key=natural_key)
    if not populated:
        unsupported.append('board has no populated footprints; an assembly package would be vacuous')
    return {'copper_layers': count, 'copper': expected if count >= 2 else copper,
            'layers': names, 'footprints': footprints, 'board_non_dnp': populated,
            'unsupported': unsupported}


def schematic_variants(data):
    _, heads = scan_sexpr(data, lambda path: len(path) == 1)
    return 'variant' in heads


def part_bindings(xml_path, probes=None):
    """Binding inputs from the native XML netlist for every physical component.

    Returns (components, unsupported). Each component has its schematic `dnp`
    flag, schematic BOM/board exclusions, value, footprint, the `LCSC`,
    `Manufacturer`, `Partnumber` and `PartNumber` symbol fields verbatim, and
    any assembly-rule field (RULE_FIELDS) it carries. copper_fields retains
    every native field occurrence for declaration qualification, including
    duplicates and malformed-field markers. Which components are assembled is
    decided by `assembly_exclusions` and `assembled_parts`.
    """
    from .probe_features import PROPERTY, native_component
    root = ET.parse(xml_path).getroot()
    probes = probes or {}
    if probes:
        from .native_netlist import read_native_inventory
        graph = read_native_inventory(xml_path)
        for ref, record in probes.items():
            if ref not in graph['components']:
                raise ValueError(ref + ': missing probe feature')
            native_component(record, graph['components'][ref], graph)
    components, unsupported = {}, []
    for comp in root.findall('./components/comp'):
        ref = comp.get('ref') or ''
        if ref.startswith('#'):
            continue  # power symbols are virtual
        if ref in components:
            unsupported.append(f'netlist repeats component {ref}')
            continue
        props = {p.get('name'): p.get('value') for p in comp.findall('property')}
        fields = {}
        for field in comp.findall('./fields/field'):
            fields[field.get('name')] = (field.text or '').strip()
        if PROPERTY in fields and ref not in probes:
            raise ValueError(ref + ': probe has no immutable source declaration')
        components[ref] = {'dnp': 'dnp' in props,
                           'excluded': sorted(set(props) & {'exclude_from_bom', 'exclude_from_board'}),
                           'value': (comp.findtext('value') or '').strip(),
                           'footprint': (comp.findtext('footprint') or '').strip(),
                           'lcsc': fields.get('LCSC', ''), 'manufacturer': fields.get('Manufacturer', ''),
                           'Partnumber': fields.get('Partnumber', ''), 'PartNumber': fields.get('PartNumber', ''),
                           'fields': {name: fields[name] for name in sorted(RULE_FIELDS) if name in fields},
                           'copper_fields': [(field.get('name'), field.text or '')
                                             if not len(field) and set(field.attrib) == {'name'}
                                             else (None, None)
                                             for field in comp.findall('./fields/field')],
                           'orderable': sorted({field.get('name') for field in comp.findall('./fields/field')
                                                if orderable_field(field.get('name')) and (field.text or '').strip()})}
        if ref in probes:
            components[ref]['probe'] = True
    return components, unsupported


def assembly_exclusions(rule, components, footprints):
    """Populated parts the declared rule omits from BOM and CPL.

    Returns (excluded, unsupported); `excluded` is a natural-ordered list of
    {"designator", "rule"}. Board DNP stays authoritative: a board-DNP part is
    never listed, whatever it carries. For a populated part, the netlist field
    and the board footprint property must agree exactly (both absent or equal);
    a disagreement, an unrecognised marker value, a marked part with no board
    footprint, or any marked part when no rule is declared is refused rather
    than resolved. `rule` is an ASSEMBLY_RULES key or None.
    """
    board = {}
    for fp in footprints:
        board.setdefault(fp['ref'], []).append(fp)
    excluded, unsupported = [], []
    markers = {value for _, value in ASSEMBLY_RULES.values()}
    for ref in sorted(set(components) | set(board), key=natural_key):
        fps = board.get(ref, [])
        if any(fp['dnp'] for fp in fps) or (not fps and components[ref]['dnp']):
            continue
        on_board = [fp.get('fields', {}) for fp in fps]
        in_netlist = components[ref].get('fields', {}) if ref in components else {}
        sources = on_board + [in_netlist]
        if not any(sources):
            continue
        if any(source != in_netlist for source in on_board):
            unsupported.append(f'{ref} assembly fields disagree: netlist '
                               f'{in_netlist if ref in components else "has no component"}, board {on_board}')
            continue
        for name, value in sorted(sources[0].items()):
            if value not in markers:
                unsupported.append(f'{ref} {name}={value!r} is not a recognised assembly marker')
            elif rule is None or ASSEMBLY_RULES[rule] != (name, value):
                unsupported.append(f'populated {ref} carries {name}={value} but the scope declares '
                                   f'no matching assembly_exclusion rule')
            elif not fps:
                unsupported.append(f'{ref} is excluded by {rule} but has no board footprint')
            else:
                excluded.append({'designator': ref, 'rule': rule})
    return excluded, unsupported


def assembled_parts(components, footprints, excluded=(), copper_features=()):
    """BOM parts under the board DNP authority, less rule-excluded designators.

    Returns (parts, disagreements, unsupported). The board footprint `dnp`
    attribute decides assembly for every component that has a footprint, so
    the BOM, the `--exclude-dnp` position file and `board_non_dnp` derive from
    the same flags. A component without a footprint keeps its schematic state:
    a populated schematic part missing from the board still reaches the BOM and
    fails designator agreement. A component whose schematic and board DNP flags
    differ is listed in `disagreements`, never resolved silently; the
    fab_consistency gate reports it as a `designator_parity` defect. A
    designator in `excluded` (from `assembly_exclusions`) is populated but not
    assembled, so it needs no binding.
    """
    board_dnp = {}
    for fp in footprints:
        board_dnp[fp['ref']] = board_dnp.get(fp['ref'], False) or fp['dnp']
    parts, disagreements, unsupported = {}, [], []
    for ref in sorted(components, key=natural_key):
        comp = components[ref]
        if comp.get('probe'):
            matched = [fp for fp in footprints if fp['ref'] == ref and fp.get('probe')]
            if len(matched) != 1:
                unsupported.append(ref + ': missing or swapped probe feature on board')
            continue
        if ref in board_dnp and board_dnp[ref] != comp['dnp']:
            disagreements.append({'designator': ref, 'schematic_dnp': comp['dnp'], 'board_dnp': board_dnp[ref]})
        assembled = not board_dnp.get(ref, comp['dnp'])
        if ref in copper_features:
            continue  # Both captured native sources were qualified by the caller.
        if comp['excluded'] and (assembled or not comp['dnp']):
            unsupported.append(f'populated component {ref} carries {comp["excluded"]}; '
                               'v1 refuses non-DNP exclusions')
            continue
        if not assembled or ref in excluded:
            continue
        if not REF_RE.match(ref):
            unsupported.append(f'component reference {ref!r} is not a plain designator')
            continue
        partnumber, camel = comp['Partnumber'], comp['PartNumber']
        if partnumber and camel and partnumber != camel:
            unsupported.append(f'{ref} has conflicting Partnumber {partnumber!r} and PartNumber {camel!r}')
            continue
        parts[ref] = {key: comp[key] for key in ('value', 'footprint', 'lcsc', 'manufacturer')}
        parts[ref]['partnumber'] = partnumber or camel
    return parts, disagreements, unsupported


def bom_rows(parts):
    """JLC BOM rows grouped by LCSC; returns (rows, flagged).

    Members of one LCSC number whose sanitised footprint, value, manufacturer
    or part number differ stay on separate rows that share the LCSC number,
    so the consistency gate can see the conflict instead of a merged row.
    A part with a missing or malformed binding is emitted with an empty LCSC
    cell and flagged; any flag keeps the package incomplete.
    """
    groups, flagged = {}, []
    for ref in sorted(parts, key=natural_key):
        part = parts[ref]
        missing = [label for label, key in (('LCSC', 'lcsc'), ('Manufacturer', 'manufacturer'),
                                            ('Partnumber', 'partnumber'), ('Value', 'value'),
                                            ('Footprint', 'footprint')) if not part[key]]
        lcsc = part['lcsc']
        if lcsc and not LCSC_RE.match(lcsc):
            flagged.append({'reference': ref, 'reason': f'LCSC {lcsc!r} is not C<digits>'})
            lcsc = ''
        if missing:
            flagged.append({'reference': ref, 'reason': 'missing part-binding properties: ' + ', '.join(missing)})
            if 'LCSC' in missing:
                lcsc = ''
        cells = tuple(ascii_text(part[key], f'{ref} {key}')
                      for key in ('footprint', 'value', 'manufacturer', 'partnumber'))
        key = (lcsc or '\0' + ref,) + cells  # unbound parts never merge
        groups.setdefault(key, []).append(ref)
    rows = []
    for key, refs in groups.items():
        refs = sorted(refs, key=natural_key)
        lcsc = '' if key[0].startswith('\0') else key[0]
        rows.append([','.join(refs), key[1], str(len(refs)), key[2], key[3], key[4], lcsc])
    rows.sort(key=lambda row: natural_key(row[0].split(',')[0]))
    return rows, flagged


def variant_parts(text, excluded=()):
    """Assembled parts of one ungrouped native `sch export bom --exclude-dnp --variant` CSV.

    Returns (parts, unsupported) shaped like `assembled_parts` output, without
    the rule-excluded designators. Raises ValueError for a header other than
    VARIANT_BOM_FIELDS, a short row, or a repeated or non-plain reference.
    """
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError('variant BOM is empty')
    if tuple(header) != VARIANT_BOM_FIELDS:
        raise ValueError(f'variant BOM header {header} is not {list(VARIANT_BOM_FIELDS)}')
    parts, unsupported = {}, []
    for line in reader:
        if len(line) != len(VARIANT_BOM_FIELDS):
            raise ValueError(f'variant BOM row {line} does not have {len(VARIANT_BOM_FIELDS)} columns')
        cells = dict(zip(VARIANT_BOM_FIELDS, (cell.strip() for cell in line)))
        ref = cells['Reference']
        if not REF_RE.match(ref) or ref in parts:
            raise ValueError(f'variant BOM reference {ref!r} is invalid or repeated')
        partnumber, camel = cells['Partnumber'], cells['PartNumber']
        if partnumber and camel and partnumber != camel:
            unsupported.append(f'{ref} has conflicting Partnumber {partnumber!r} and PartNumber {camel!r} '
                               'in the variant BOM')
        parts[ref] = {'value': cells['Value'], 'footprint': cells['Footprint'], 'lcsc': cells['LCSC'],
                      'manufacturer': cells['Manufacturer'], 'partnumber': partnumber or camel}
    return {ref: part for ref, part in parts.items() if ref not in excluded}, unsupported


def render_bom(rows):
    out = io.StringIO()
    out.write(BOM_HEADER + '\n')
    csv.writer(out, lineterminator='\n').writerows(rows)
    return out.getvalue()


def cpl_rows(pos_text):
    """Rewrite native `pos --format csv --units mm` output to JLC CPL rows."""
    reader = csv.reader(io.StringIO(pos_text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError('position file is empty')
    if header != POS_HEADER:
        raise ValueError(f'position file header {header} is not {POS_HEADER}')
    rows, seen = [], set()
    for line in reader:
        if len(line) != len(POS_HEADER):
            raise ValueError(f'position row {line} does not have {len(POS_HEADER)} columns')
        ref, _, _, x, y, rot, side = line
        if not REF_RE.match(ref) or ref in seen:
            raise ValueError(f'position row reference {ref!r} is invalid or repeated')
        seen.add(ref)
        for label, token in (('PosX', x), ('PosY', y), ('Rot', rot)):
            if not NUMBER_RE.match(token) or not math.isfinite(float(token)):
                raise ValueError(f'{ref} {label} {token!r} is not a finite millimetre number')
        if side not in ('top', 'bottom'):
            raise ValueError(f'{ref} side {side!r} is not top or bottom')
        rows.append([ref, x, y, 'Top' if side == 'top' else 'Bottom', rot])
    rows.sort(key=lambda row: natural_key(row[0]))
    return rows


def render_cpl(rows):
    out = io.StringIO()
    out.write(CPL_HEADER + '\n')
    csv.writer(out, lineterminator='\n').writerows(rows)
    return out.getvalue()


def normalise(text, patterns, label):
    """Replace each native timestamp exactly once; refuse any other date text."""
    for pattern, replacement in patterns:
        text, count = pattern.subn(replacement, text)
        if count != 1:
            raise ValueError(f'{label}: expected one native timestamp for {pattern.pattern!r}, found {count}')
    leftover = [m.group(0) for m in TIMESTAMP_RE.finditer(text)
                if not m.group(0).startswith(('1970-01-01T00:00', '1970-01-01 00:00'))]
    if leftover:
        raise ValueError(f'{label}: unnormalised timestamp text {leftover[:3]}')
    return text


def gerber_text(data, function, label):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError(f'{label} is not UTF-8') from exc
    if not text.rstrip('\n').endswith('M02*'):
        raise ValueError(f'{label} does not end with M02*')
    functions = re.findall(r'^%TF\.FileFunction,([^*\n]*)\*%$', text, re.M)
    if functions != [function]:
        raise ValueError(f'{label} file function {functions} is not [{function!r}]')
    if not re.search(r'^%FSLAX\d\dY\d\d\*%$', text, re.M):
        raise ValueError(f'{label} has no Gerber coordinate format')
    return normalise(text, GERBER_TIMES, label)


def excellon_text(data, function, label):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError(f'{label} is not UTF-8') from exc
    lines = text.splitlines()
    if not lines or lines[0] != 'M48' or lines[-1] != 'M30':
        raise ValueError(f'{label} is not a complete Excellon file (M48 ... M30)')
    functions = re.findall(r'^; #@! TF\.FileFunction,(.*)$', text, re.M)
    if functions != [function]:
        raise ValueError(f'{label} file function {functions} is not [{function!r}]')
    return normalise(text, DRILL_TIMES, label)


def write_zip(path, members):
    """Deterministic zip: fixed order, timestamps, permissions and compression."""
    with zipfile.ZipFile(path, 'w') as archive:
        for name, source in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, Path(source).read_bytes(), compresslevel=9)


def jlc_template_header(source):
    """Extract and hash the header and raw bytes of a captured JLCPCB CSV template."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if not data:
        raise ValueError('JLC template is empty')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise ValueError('JLC template is not UTF-8') from exc
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError('JLC template must contain a header line and at least one sample row')
    header = lines[0]
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or not rows[0] or any(not col for col in rows[0]):
        raise ValueError('JLC template header has empty column names')
    columns = rows[0]
    if any(len(row) != len(columns) for row in rows[1:]):
        raise ValueError('JLC template sample row column count does not match header')
    return {
        'header': header,
        'columns': columns,
        'sha256': hashlib.sha256(header.encode('utf-8')).hexdigest(),
        'template_sha256': hashlib.sha256(data).hexdigest(),
    }


def verify_jlc_templates(template_dir=JLC_TEMPLATE_DIR):
    """Verify captured JLCPCB BOM and CPL template files against their recorded hashes."""
    root = Path(template_dir)
    meta_path = root / 'jlc-templates.json'
    if not meta_path.is_file() or meta_path.is_symlink():
        raise ValueError(f'missing JLC template metadata: {meta_path}')
    meta = read_json(meta_path)
    if not isinstance(meta, dict) or meta.get('schema') != TEMPLATE_SCHEMA:
        raise ValueError(f'JLC template metadata must have schema {TEMPLATE_SCHEMA!r}')
    templates = meta.get('templates')
    if not isinstance(templates, dict) or set(templates) != {'bom', 'cpl'}:
        raise ValueError('JLC template metadata must declare exactly bom and cpl templates')
    verified = {}
    for kind in ('bom', 'cpl'):
        entry = templates[kind]
        if not isinstance(entry, dict) or set(entry) != {'path', 'header', 'columns', 'sha256', 'template_sha256'}:
            raise ValueError(f'JLC {kind} template metadata has missing or extra keys')
        rel = entry['path']
        if not isinstance(rel, str) or '/' in rel or '\\' in rel or rel in ('.', '..'):
            raise ValueError(f'JLC {kind} template path {rel!r} must be a plain filename')
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'JLC {kind} template file is missing: {path}')
        parsed = jlc_template_header(path.read_bytes())
        if parsed['template_sha256'] != entry['template_sha256']:
            raise ValueError(f"JLC {kind} template sha256 {parsed['template_sha256']} != "
                             f"expected {entry['template_sha256']}")
        if parsed['sha256'] != entry['sha256']:
            raise ValueError(f"JLC {kind} header sha256 {parsed['sha256']} != expected {entry['sha256']}")
        if parsed['header'] != entry['header'] or parsed['columns'] != entry['columns']:
            raise ValueError(f'JLC {kind} template header {parsed["header"]!r} disagrees with metadata')
        verified[kind] = {'path': rel, **parsed}
    return verified


def header_records(template_dir=JLC_TEMPLATE_DIR):
    """Return the verified BOM and CPL header records written into the fab manifest."""
    templates = verify_jlc_templates(template_dir)
    expected = {
        'bom': (BOM_HEADER, BOM_COLUMNS, BOM_HEADER_SHA256, BOM_TEMPLATE_SHA256),
        'cpl': (CPL_HEADER, CPL_COLUMNS, CPL_HEADER_SHA256, CPL_TEMPLATE_SHA256),
    }
    records = {}
    for kind, (header, columns, header_sha, template_sha) in expected.items():
        actual = templates[kind]
        if (actual['header'] != header or actual['columns'] != columns
                or actual['sha256'] != header_sha or actual['template_sha256'] != template_sha):
            raise ValueError(f'exported {kind.upper()} header disagrees with captured JLC template')
        records[kind] = {k: actual[k] for k in ('header', 'columns', 'sha256', 'template_sha256')}
    return records


def inventory(fab):
    """sha256 of every regular file under fab/ except manifest.json."""
    files = {}
    for path in sorted(Path(fab).rglob('*')):
        rel = path.relative_to(fab).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'fab staging contains a non-regular entry {rel}')
        if path.is_file() and rel != 'manifest.json':
            if path.stat().st_size == 0:
                raise ValueError(f'fab staging file {rel} is empty')
            files[rel] = digest(path)
    return files


def verify_manifest(fab, manifest):
    """Recompute the staging inventory; raise ValueError on any disagreement."""
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise ValueError('not an agent-ee.fab-manifest/1')
    if 'headers' in manifest and manifest['headers'] != header_records():
        raise ValueError('manifest headers do not match captured JLC templates')
    actual = inventory(fab)
    if actual != manifest.get('files'):
        listed = manifest.get('files') or {}
        raise ValueError(f'manifest inventory mismatch: missing={sorted(set(listed) - set(actual))} '
                         f'extra={sorted(set(actual) - set(listed))} '
                         f'changed={sorted(k for k in set(actual) & set(listed) if actual[k] != listed[k])}')
    return actual


# ---------------------------------------------------------------- the check

def _prerequisite(ctx, nid):
    from .checks import parse_xml_netlist
    specs = ctx.scope['checks']
    earlier = specs[:next(i for i, spec in enumerate(specs) if spec.get('id') == ctx.cid)]
    if not any(s.get('id') == nid and s.get('kind') == 'netlist' for s in earlier):
        raise _error('not_run', f'fab_export requires a preceding netlist check {nid!r}')
    path = ctx.run_dir / 'checks' / f'{nid}.json'
    if not path.is_file():
        raise _error('not_run', f'netlist prerequisite {nid!r} has not run')
    prior = read_json(path)
    if (not isinstance(prior, dict) or prior.get('execution') != 'ok' or prior.get('kind') != 'netlist'
            or prior.get('candidate_id') != ctx.manifest['candidate_id']):
        raise _error('not_run', f'netlist prerequisite {nid!r} is unsuccessful or belongs to another candidate')
    xml = ctx.run_dir / 'work' / nid / 'design.xml'
    for rel in (f'work/{nid}/design.xml', f'work/{nid}/connectivity.json'):
        if rel not in prior.get('evidence', []) or not (ctx.run_dir / rel).is_file():
            raise _error('not_run', f'netlist prerequisite {nid!r} did not retain {rel}')
    try:
        if parse_xml_netlist(xml) != read_json(xml.with_name('connectivity.json')):
            raise ValueError('native XML and prerequisite connectivity disagree')
    except (ValueError, OSError, ET.ParseError) as exc:
        raise _error('malformed', f'invalid retained native netlist evidence: {exc}') from exc
    ctx.result['evidence'].extend([f'work/{nid}/design.xml', f'work/{nid}/connectivity.json'])
    return prior, xml


def _step(ctx, name, argv):
    """Run one native step with its own /work so each record is retained."""
    from .executor import execute
    directory = ctx.work / 'steps' / name
    if (directory / 'out').exists() or (directory / 'out').is_symlink():
        raise _error('malformed', f'kicad-cli {name} output directory already exists; refusing stale outputs')
    config = ctx.work / 'kicad-config'
    if config.is_dir():
        shutil.copytree(config, directory / 'kicad-config', dirs_exist_ok=True)
    remaining = ctx.remaining()
    if remaining <= 1:
        raise _error('interrupted', f'deadline exhausted before kicad-cli {name}')
    clamped = min(STEP_CEILING, remaining)
    try:
        record = execute(ctx.inputs, directory, argv, timeout=clamped)
    finally:
        for rel in ('execution.json', 'console.log', 'stdout.log', 'stderr.log'):
            ctx.evidence(f'steps/{name}/{rel}')
        # Raw native outputs stay beside the normalised staging for audit.
        for path in sorted((directory / 'out').rglob('*')) if (directory / 'out').is_dir() else ():
            if path.is_file() and not path.is_symlink():
                ctx.evidence(path.relative_to(ctx.work).as_posix())
    ctx.result['details'].setdefault('tools', {})[name] = {
        'argv': record['argv'], 'exit_code': record['exit_code'],
        'duration_s': record['duration_s'], 'console_sha256': record['console_sha256']}
    if record['execution'] == 'timeout' and clamped < STEP_CEILING:
        raise _error('interrupted', f'run deadline cut kicad-cli {name} off after {clamped:.0f}s')
    if record['execution'] != 'ok':
        raise _error(record['execution'], f'kicad-cli {name} {record["execution"]} '
                                          f'(exit {record["exit_code"]}); no package was marked complete')
    return directory


def _outputs(directory, label):
    out = directory / 'out'
    if not out.is_dir():
        raise _error('malformed', f'kicad-cli {label} wrote no output directory')
    found = {p.name: p for p in out.iterdir()}
    for name, path in found.items():
        if path.is_symlink() or not path.is_file():
            raise _error('malformed', f'kicad-cli {label} output {name} is not a regular file')
        if path.stat().st_size == 0:
            raise _error('failed', f'kicad-cli {label} output {name} is empty')
    return found


def check_fab_export(ctx):
    try:
        params = validate_params(ctx.params)
    except ValueError as exc:
        raise _error('unsupported', str(exc)) from exc
    prior, xml = _prerequisite(ctx, params['netlist_check'])
    board_rel = ctx.scope.get('board')
    if not isinstance(board_rel, str):
        raise _error('unsupported', 'scope declares no board')
    board = ctx.candidate_path(board_rel)
    board_sha = ctx.manifest['files'].get(board_rel)
    if not board_sha or digest(ctx.inputs / board_rel) != board_sha:
        raise _error('malformed', 'captured board bytes differ from the candidate manifest digest')
    details = ctx.result['details']
    details.update(board_path=board_rel, board_sha256=board_sha)
    try:
        from .probe_features import immutable_inputs, load_for_check, validate_schematic
        immutable_inputs(params, ctx.manifest['files'], ctx.inputs, ctx.scope)
        probes = load_for_check(ctx) if 'probe_manifest' in params else {}
        copper_refs = declared_copper_features(params)
        facts = board_facts((ctx.inputs / board_rel).read_bytes(), probes, copper_refs)
        seen = set()
        for rel in prior.get('details', {}).get('schematic_files', [ctx.scope['schematic']]):
            data = (ctx.inputs / rel).read_bytes()
            seen.update(validate_schematic(data, probes))
            facts['unsupported'] += copper_schematic_problems(data, copper_refs)
        if seen != set(probes):
            raise ValueError('missing probe feature(s): ' + ', '.join(sorted(set(probes) - seen)))
    except ValueError as exc:
        raise _error('malformed', f'board unreadable for fab export: {exc}') from exc
    details['board'] = {k: facts[k] for k in ('copper_layers', 'board_non_dnp', 'footprints')}
    unsupported = list(facts['unsupported'])
    variant = params.get('bom_variant')
    if variant is not None:
        details['bom_variant'] = _variant_record(ctx, prior, variant)
    for rel in prior.get('details', {}).get('schematic_files', []):
        try:
            if schematic_variants((ctx.inputs / rel).read_bytes()) and variant is None:
                unsupported.append(f'schematic {rel} declares assembly variants and the check declares '
                                   'no bom_variant')
        except (OSError, ValueError) as exc:
            raise _error('malformed', f'schematic {rel} unreadable: {exc}') from exc
    try:
        components, part_problems = part_bindings(xml, probes)
    except (ET.ParseError, OSError, ValueError) as exc:
        raise _error('malformed', f'native XML netlist unreadable: {exc}') from exc
    copper_problems = copper_feature_problems((ctx.inputs / board_rel).read_bytes(), copper_refs, components)
    details['copper_features'] = sorted(copper_refs, key=natural_key)
    rule = params.get('assembly_exclusion')
    excluded, exclusion_problems = assembly_exclusions(rule, components, facts['footprints'])
    details['assembly_exclusion'] = {'rule': rule, 'declared_by': f'scope check {ctx.cid!r} params',
                                     'scope_sha256': ctx.manifest.get('scope_sha256'), 'excluded': excluded}
    parts, disagreements, assembly_problems = assembled_parts(
        components, facts['footprints'], {e['designator'] for e in excluded}, copper_refs)
    unsupported += copper_problems + part_problems + exclusion_problems + assembly_problems
    try:
        rows, flagged = bom_rows(parts)
    except ValueError as exc:
        unsupported.append(str(exc))
        rows, flagged = [], []
    if not parts:
        unsupported.append('netlist has no populated components to place in a BOM')
    details['schematic_dnp'] = sorted((r for r, c in components.items() if c['dnp']), key=natural_key)
    details['dnp_disagreements'] = disagreements
    if unsupported:
        unsupported = list(dict.fromkeys(unsupported))
        details['unsupported'] = unsupported
        raise _error('unsupported', 'fab export refused: ' + '; '.join(unsupported))

    copper = facts['copper']
    layers = copper + [name for name, _, _ in FAB_LAYERS]
    ctx.seed_kicad_config()
    fab = ctx.work / 'fab'
    if fab.exists() or fab.is_symlink():
        raise _error('malformed', 'fab staging directory already exists; refusing stale outputs')
    try:
        _export(ctx, board, board_rel, board_sha, facts, layers, rows, flagged, fab, rule, excluded, parts, probes)
    except BaseException:
        if fab.is_dir():
            shutil.rmtree(ctx.work / 'fab.incomplete', ignore_errors=True)
            fab.rename(ctx.work / 'fab.incomplete')
            ctx.result['evidence'] = [e for e in ctx.result['evidence']
                                      if not e.startswith(f'work/{ctx.cid}/fab/')]
            for path in sorted((ctx.work / 'fab.incomplete').rglob('*')):
                if path.is_file():
                    ctx.evidence(path.relative_to(ctx.work).as_posix())
        raise


def _variant_record(ctx, prior, variant):
    """Refuse an unregistered or unloadable variant before kicad-cli runs (kicad_quirks guard)."""
    from .kicad_quirks import VariantError, validate_bom_variants
    schematic = ctx.scope.get('schematic')
    if not isinstance(schematic, str) or not schematic.endswith('.kicad_sch'):
        raise _error('unsupported', f'bom_variant {variant!r} needs the scope root schematic')
    project = schematic[:-len('.kicad_sch')] + '.kicad_pro'
    project_sha = ctx.manifest['files'].get(project)
    if not project_sha or not (ctx.inputs / project).is_file() or digest(ctx.inputs / project) != project_sha:
        raise _error('unsupported', f'BOM variant {variant!r} refused before kicad-cli ran: the variant '
                                    f'registry {project} is not a captured candidate file')
    files = prior.get('details', {}).get('schematic_files') or [schematic]
    try:
        record = validate_bom_variants(ctx.inputs / project, [ctx.inputs / rel for rel in files], [variant])
    except VariantError as exc:
        raise _error('unsupported', f'BOM variant {variant!r} refused before kicad-cli ran: {exc}') from exc
    return {'name': variant, 'project': {'path': project, 'sha256': project_sha}, 'schematic': schematic,
            **record}


def _export(ctx, board, board_rel, board_sha, facts, layers, rows, flagged, fab, rule, excluded, parts, probes):
    details = ctx.result['details']
    variant = details.get('bom_variant')
    version_dir = _step(ctx, 'version', ['kicad-cli', 'version'])
    version = (version_dir / 'stdout.log').read_text(errors='replace').strip()
    if not version or '\n' in version:
        raise _error('malformed', f'kicad-cli version output {version!r} is not one version line')
    tool_path = shutil.which('kicad-cli', path=SANDBOX_PATH)
    if not tool_path:
        raise _error('unavailable', 'kicad-cli is not on the sandbox PATH')
    tool = {'path': tool_path, 'version': version, 'sha256': digest(tool_path)}
    details['kicad_cli'] = tool

    stats_dir = _step(ctx, 'stats', ['kicad-cli', 'pcb', 'export', 'stats', '--format', 'json',
                                     '--units', 'mm', '-o', '/work/out/stats.json', board])
    stats_path = _outputs(stats_dir, 'stats').get('stats.json')
    if stats_path is None:
        raise _error('malformed', 'kicad-cli stats wrote no stats.json')
    try:
        stats_text = stats_path.read_text()
        summary = STATS_TIME.subn(r'\g<1>' + FIXED_TIME + r'\2', stats_text)
        if summary[1] != 1:
            raise ValueError('expected one metadata date')
        stats = json.loads(summary[0])
        vias, pads, board_stats = stats['vias'], stats['pads'], stats['board']
        holes = stats['drill_holes']
        features = [f'{count} {kind} via(s)' for kind in ('blind', 'buried', 'micro')
                    if (count := vias[kind]) != 0]
        features += [f'{count} {kind} pad(s)' for kind in ('castellated', 'press_fit')
                     if (count := pads[kind]) != 0]
        features += [f'drill span {h["start_layer"]}..{h["stop_layer"]}' for h in holes
                     if (h['start_layer'], h['stop_layer']) != ('F.Cu', 'B.Cu')]
        if board_stats['has_outline'] is not True:
            features.append('no closed board outline')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise _error('malformed', f'native board statistics unusable: {exc}') from exc
    if features:
        details['unsupported'] = features
        raise _error('unsupported', 'unsupported board features for the v1 JLC package: ' + ', '.join(features))

    gerber_dir = _step(ctx, 'gerbers', ['kicad-cli', 'pcb', 'export', 'gerbers', '-o', '/work/out/',
                                        '--layers', ','.join(layers), board])
    drill_dir = _step(ctx, 'drill', ['kicad-cli', 'pcb', 'export', 'drill', '-o', '/work/out/',
                                     '--format', 'excellon', '--excellon-units', 'mm',
                                     '--excellon-separate-th', '--generate-map',
                                     '--map-format', 'gerberx2', board])
    pos_dir = _step(ctx, 'pos', ['kicad-cli', 'pcb', 'export', 'pos', '-o', '/work/out/pos.csv',
                                 '--side', 'both', '--format', 'csv', '--units', 'mm',
                                 '--exclude-dnp', board])
    if variant is not None:
        # The variant reaches only the BOM export; positions stay the default variant.
        bom_dir = _step(ctx, 'bom', ['kicad-cli', 'sch', 'export', 'bom', '--fields', ','.join(VARIANT_BOM_FIELDS),
                                     '--labels', ','.join(VARIANT_BOM_FIELDS), '--ref-range-delimiter', '',
                                     '--exclude-dnp', '-o', '/work/out/bom.csv',
                                     '--variant', variant['name'], ctx.candidate_path(variant['schematic'])])

    fab.mkdir()
    (fab / 'gerbers').mkdir()
    (fab / 'drill').mkdir()
    plots = _outputs(gerber_dir, 'gerbers')
    try:
        jobs = [p for n, p in plots.items() if n.endswith('.gbrjob')]
        if len(jobs) != 1:
            raise ValueError(f'expected one Gerber job file, found {len(jobs)}')
        job = json.loads(jobs[0].read_text())
        if job['GeneralSpecs']['LayerNumber'] != facts['copper_layers']:
            raise ValueError(f"native job file reports {job['GeneralSpecs']['LayerNumber']} copper "
                             f"layers, board table has {facts['copper_layers']}")
        expected = []
        count = facts['copper_layers']
        for index, name in enumerate(facts['copper']):
            ext = 'gtl' if name == 'F.Cu' else 'gbl' if name == 'B.Cu' else f'g{index}'
            position = 'Top' if name == 'F.Cu' else 'Bot' if name == 'B.Cu' else 'Inr'
            expected.append((name, ext, f'Copper,L{index + 1},{position}'))
        expected += list(FAB_LAYERS)
        gerbers = [n for n in plots if not n.endswith('.gbrjob')]
        if len(gerbers) != len(expected):
            raise ValueError(f'expected {len(expected)} layer plots, kicad-cli wrote {sorted(gerbers)}')
        for name, ext, function in expected:
            match = [n for n in gerbers if n.lower().endswith('.' + ext)]
            if len(match) != 1:
                raise ValueError(f'layer {name}: expected one .{ext} plot, found {match}')
            text = gerber_text(plots[match[0]].read_bytes(), function, match[0])
            (fab / 'gerbers' / match[0]).write_bytes(text.encode('utf-8'))
        drills = _outputs(drill_dir, 'drill')
        roles = {}
        for role, function in (('pth', 'PTH'), ('npth', 'NPTH')):
            found = [n for n in drills if n.endswith(f'-{function}.drl')]
            maps = [n for n in drills if n.endswith(f'-{function}-drl_map.gbr')]
            if len(found) != 1 or len(maps) != 1:
                raise ValueError(f'expected one {function} drill and map, found {found} {maps}')
            text = excellon_text(drills[found[0]].read_bytes(),
                                 f'Plated,1,{count},PTH' if role == 'pth' else
                                 f'NonPlated,1,{count},NPTH', found[0])
            (fab / 'drill' / found[0]).write_bytes(text.encode('utf-8'))
            text = gerber_text(drills[maps[0]].read_bytes(), 'Drillmap', maps[0])
            (fab / 'drill' / maps[0]).write_bytes(text.encode('utf-8'))
            roles[role] = found[0]
        if len(drills) != 4:
            raise ValueError(f'drill export wrote more than PTH/NPTH files and maps: {sorted(drills)}')
        pos = _outputs(pos_dir, 'pos').get('pos.csv')
        if pos is None:
            raise ValueError('kicad-cli pos wrote no pos.csv')
        native = cpl_rows(pos.read_text(encoding='utf-8'))
        if variant is not None:
            exported = _outputs(bom_dir, 'bom').get('bom.csv')
            if exported is None:
                raise ValueError('kicad-cli bom wrote no bom.csv')
            varied, variant_problems = variant_parts(exported.read_text(encoding='utf-8'),
                                                     {e['designator'] for e in excluded} |
                                                     declared_copper_features(ctx.spec.get('params') or {}))
    except (ValueError, KeyError, TypeError, UnicodeError, OSError) as exc:
        raise _error('malformed', f'native fab output unusable: {exc}') from exc
    if variant is not None:
        default_refs = sorted(parts, key=natural_key)
        variant_refs = sorted(varied, key=natural_key)
        variant.update(native_bom_sha256=digest(exported), assembled=variant_refs,
                       binding_changes=sorted((r for r in varied if r in parts and varied[r] != parts[r]),
                                              key=natural_key))
        if variant_refs != default_refs:
            variant['populated_set_change'] = {
                'added': [r for r in variant_refs if r not in parts],
                'removed': [r for r in default_refs if r not in varied]}
            details['unsupported'] = [f"BOM variant {variant['name']!r} changes the populated set"]
            raise _error('unsupported', f"BOM variant {variant['name']!r} changes the populated set "
                                        f"(adds {variant['populated_set_change']['added']}, removes "
                                        f"{variant['populated_set_change']['removed']}); the CPL stays the "
                                        'default variant, so BOM/CPL designator parity refuses it')
        try:
            rows, flagged = bom_rows(varied)
        except ValueError as exc:
            raise _error('unsupported', f'variant BOM unusable: {exc}') from exc
        if variant_problems:
            details['unsupported'] = variant_problems
            raise _error('unsupported', 'variant BOM refused: ' + '; '.join(variant_problems))
    # Rule-excluded parts are populated, so the native position file lists them;
    # they are dropped here only after the native set matched the board.
    excluded_refs = [e['designator'] for e in excluded]
    cpl = [row for row in native if row[0] not in excluded_refs]
    (fab / 'cpl.csv').write_text(render_cpl(cpl))
    (fab / 'bom.csv').write_text(render_bom(rows))
    (fab / 'pcb_summary.json').write_text(summary[0])
    members = ([(p.name, p) for p in sorted((fab / 'gerbers').iterdir(), key=lambda p: p.name)] +
               [(p.name, p) for p in sorted((fab / 'drill').iterdir(), key=lambda p: p.name)])
    if len({name for name, _ in members}) != len(members):
        raise _error('malformed', 'Gerber and drill file names collide in the flat zip')
    write_zip(fab / 'gerbers.zip', members)

    native_refs = sorted((r[0] for r in native), key=natural_key)
    cpl_refs = sorted((r[0] for r in cpl), key=natural_key)
    bom_refs = sorted((ref for row in rows for ref in row[0].split(',')), key=natural_key)
    position_expected = sorted((fp['ref'] for fp in facts['footprints']
                                if not fp['dnp'] and 'exclude_from_pos_files' not in fp['attributes']),
                               key=natural_key)
    assembled = [ref for ref in position_expected if ref not in excluded_refs]
    details['designators'] = {'board_non_dnp': facts['board_non_dnp'], 'excluded': excluded_refs,
                              'assembled': assembled, 'native_pos': native_refs, 'cpl': cpl_refs,
                              'bom': bom_refs, 'agree': native_refs == position_expected and
                              assembled == cpl_refs == bom_refs}
    details['bom_rows'] = len(rows)
    details['cpl_rows'] = len(cpl)
    for path in sorted(fab.rglob('*')):
        if path.is_file():
            ctx.evidence(path.relative_to(ctx.work).as_posix())
    if flagged:
        details['flagged_parts'] = flagged
        raise _error('unsupported', 'BOM has parts without a complete LCSC/manufacturer/part-number '
                                    'binding: ' + ', '.join(f['reference'] for f in flagged))
    if native_refs != position_expected:
        raise _error('malformed', f'native position file designators {native_refs} differ from the board '
                                  f'non-DNP footprints not excluded from position files {position_expected}')
    if bom_refs != assembled or cpl_refs != assembled:
        raise _error('malformed', f'BOM designators {bom_refs} or CPL designators {cpl_refs} differ from the '
                                  f'board assembled footprints {assembled} (non-DNP less excluded {excluded_refs})')
    try:
        headers = header_records()
        files = inventory(fab)
    except ValueError as exc:
        raise _error('malformed', str(exc)) from exc
    details['headers'] = headers
    manifest = {
        'schema': SCHEMA, 'candidate_id': ctx.manifest['candidate_id'],
        'board_path': board_rel, 'board_sha256': board_sha, 'tool': tool, 'files': files,
        'headers': headers,
        'copper_layers': facts['copper_layers'], 'enabled_fab_layers': layers,
        'drill': roles, 'designators': {'board_non_dnp': facts['board_non_dnp'], 'excluded': excluded,
                                        'exclusion_rule': rule},
        'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    if variant is not None:
        manifest['bom_variant'] = {key: variant[key] for key in
                                   ('name', 'project', 'registered', 'native_bom_sha256', 'binding_changes')}
    if probes:
        manifest['probe_features'] = probes
    write_json(fab / 'manifest.json', manifest)
    try:
        verify_manifest(fab, read_json(fab / 'manifest.json'))
    except ValueError as exc:
        raise _error('malformed', f'manifest does not verify: {exc}') from exc
    ctx.evidence('fab/manifest.json')
    details['manifest_sha256'] = digest(fab / 'manifest.json')
    ctx.result['limitations'] += [
        'CPL rotations are the native KiCad position-file values; no JLC rotation, offset or '
        'pin-1 policy was applied; the cpl_rotation check (AGENTEE-72) corrects them',
        'pass means the exports exist, parsed, agree on designators and are hashed; '
        'part, side and model consistency require fab_consistency (AGENTEE-73)',
        'no DFM, panelisation, stock, price or orderability evaluation',
    ]
    if excluded:
        ctx.result['limitations'].append(
            f'{", ".join(excluded_refs)} populated but excluded from BOM and CPL by the declared rule '
            f'{rule}; the parts remain on the board, Gerbers and drill and must be fitted outside '
            'JLC assembly')
    if details['dnp_disagreements']:
        ctx.result['limitations'].append(
            'schematic and board DNP flags disagree on ' +
            ', '.join(d['designator'] for d in details['dnp_disagreements']) +
            '; the board flags define the BOM, CPL and board_non_dnp, and fab_consistency '
            'reports the disagreement as a designator_parity defect')
    ctx.result['execution'] = 'ok'
    ctx.result['outcome'] = 'pass'
    ctx.result['summary'] = (f"JLCPCB fab package exported by kicad-cli {version}: "
                             f"{len(expected)} layer plots ({facts['copper_layers']} copper), PTH/NPTH "
                             f"drills, {len(rows)} BOM row(s), {len(cpl)} CPL row(s), "
                             f"{len(excluded)} rule-excluded part(s), manifest bound to "
                             f"board {board_sha[:12]}")
    from .fab_stage import grade_native
    grade_native(ctx)
