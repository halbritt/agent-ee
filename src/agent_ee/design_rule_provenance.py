"""Import declared board limits without promoting template defaults to authority."""
import copy

from .catalog import strict_json
from .routing_rules import number

SCHEMA = 'fome-mc.constraint-provenance/1'
CLASSES = frozenset(('owner', 'manufacturer', 'datasheet', 'engineering', 'default'))
# Native fields qualified by the existing producer and the design-owner document.
BOARD_FIELDS = frozenset(('min_clearance', 'min_track_width', 'min_through_hole_diameter',
    'min_via_diameter', 'min_via_annular_width', 'min_hole_to_hole', 'min_hole_clearance',
    'min_copper_edge_clearance', 'min_silk_clearance', 'min_text_height', 'min_text_thickness'))
NET_FIELDS = {'clearance_mm': 'clearance', 'track_width_mm': 'track_width',
              'via_diameter_mm': 'via_diameter', 'via_drill_mm': 'via_drill'}


def declaration(raw):
    """Read the design lane's existing document; a basis is its citation text."""
    document = strict_json(raw)
    if not isinstance(document, dict) or document.get('schema') != SCHEMA:
        raise ValueError('constraint source must use ' + SCHEMA)
    rules = document.get('board_setup_rules')
    if not isinstance(rules, dict):
        raise ValueError('constraint source needs board_setup_rules')
    for name, spec in rules.items():
        if name not in BOARD_FIELDS | {'solder_mask_min_width'}:
            raise ValueError('unqualified native constraint setting: ' + name)
        _spec(spec)
        number(spec.get('value_mm'), 'constraint source millimetres')
    default = document.get('net_class_default')
    if default is not None:
        _spec(default)
        for name in NET_FIELDS:
            if name in default:
                number(default[name], 'constraint source millimetres')
    custom = document.get('custom_rules', {})
    if not isinstance(custom, dict):
        raise ValueError('constraint source custom_rules must be an object')
    for filename, rules in custom.items():
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError('constraint source custom rules need a file name')
        if not isinstance(rules, list):
            raise ValueError('constraint source custom rules must be lists')
        names = set()
        for spec in rules:
            _spec(spec)
            if not isinstance(spec.get('rule'), str) or not spec['rule'].strip():
                raise ValueError('constraint source custom rule needs a native rule name')
            if spec['rule'] in names:
                raise ValueError('constraint source duplicates a native rule name')
            names.add(spec['rule'])
    return document


def _spec(spec):
    if (not isinstance(spec, dict) or not isinstance(spec.get('class'), str)
            or spec['class'] not in CLASSES):
        raise ValueError('constraint source has an invalid class')
    if not isinstance(spec.get('basis'), str) or not spec['basis'].strip():
        raise ValueError('constraint source needs a nonempty citation basis')


def import_constraints(project, raw, path, sha256):
    """Apply only explicitly sourced hard limits; retain other values as advisories.

    Import does not certify the citation's engineering applicability. The captured
    document and the resulting project remain independent immutable inputs.
    """
    document = declaration(raw)
    project = copy.deepcopy(project)
    rules = project.setdefault('board', {}).setdefault('design_settings', {}).setdefault('rules', {})
    for name, spec in document['board_setup_rules'].items():
        # The design lane declares this manufacturing limit but explicitly says
        # it is not a board-setup field. Never invent a native field for it.
        if name in BOARD_FIELDS and spec['class'] != 'default':
            rules[name] = spec['value_mm']
    default = document.get('net_class_default')
    if default is not None and default['class'] != 'default':
        classes = project.get('net_settings', {}).get('classes', [])
        found = [c for c in classes if c.get('name') == 'Default']
        if len(found) != 1:
            raise ValueError('constraint import requires one native Default class')
        for name, field in NET_FIELDS.items():
            if name in default:
                found[0][field] = default[name]
    return project, {'path': path, 'sha256': sha256, 'document': document,
                     'settings': inventory(project, document, path)}


def inventory(project, document=None, path=None):
    """Every emitted numeric setting has an explicit class, including defaults."""
    document = document or {}
    records = []
    rules = project.get('board', {}).get('design_settings', {}).get('rules', {})
    for name, value in sorted(rules.items()):
        spec = document.get('board_setup_rules', {}).get(name, {})
        records.append(_record('board.design_settings.rules.' + name, value, spec, path,
                               '/board_setup_rules/' + name, 'value_mm'))
    for netclass in project.get('net_settings', {}).get('classes', []):
        for name, value in sorted(netclass.items()):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            spec = (document.get('net_class_default') or {}) if netclass.get('name') == 'Default' else {}
            key = next((key for key, field in NET_FIELDS.items() if field == name), None)
            records.append(_record('net_settings.classes.' + netclass['name'] + '.' + name,
                                   value, spec, path, '/net_class_default', key))
    return records


def _record(setting, value, spec, path, locator, key):
    sourced = key is not None and key in spec and spec[key] == value
    return {'setting': setting, 'value': value,
            'class': spec['class'] if sourced else 'default',
            'citation': {'path': path, 'locator': locator, 'basis': spec['basis']} if sourced else None,
            'enforcement': 'hard' if sourced and spec['class'] != 'default' else 'advisory'}


# This binding travels with the immutable project into each native staging area.
PROJECT_KEY = 'agent_ee_constraint_provenance'
STRUCTURAL = frozenset(('shorting_items', 'pth_inside_courtyard', 'solder_mask_bridge', 'holes_co_located'))
NUMERIC_FIELDS = {
    'clearance': ('min_clearance', 'clearance'),
    'track_width': ('min_track_width', 'track_width'),
    'drill_out_of_range': ('min_through_hole_diameter', 'via_drill'),
    'via_diameter': ('min_via_diameter', 'via_diameter'),
    'annular_width': ('min_via_annular_width',),
    'hole_clearance': ('min_hole_clearance',),
    'hole_to_hole': ('min_hole_to_hole',),
    'copper_edge_clearance': ('min_copper_edge_clearance',),
    'silk_overlap': ('min_silk_clearance',),
    'silk_over_copper': ('min_silk_clearance',),
    'silk_edge_clearance': ('min_silk_clearance',),
    'text_height': ('min_text_height',),
    'text_thickness': ('min_text_thickness',),
    'starved_thermal': ('min_resolved_spokes',),
}


def bind_project(project, record):
    """Bind emitted settings to their receipt without introducing a second schema."""
    project[PROJECT_KEY] = copy.deepcopy(record)


class OwnerRuleReclassification(ValueError):
    """A constraint declaration attempted to demote a captured owner rule."""


def require_owner_rule_classes(record, filename, owner_names):
    for spec in (record.get('document') or {}).get('custom_rules', {}).get(filename, []):
        if spec['rule'] in owner_names and spec['class'] != 'owner':
            raise OwnerRuleReclassification('owner rule cannot be reclassified: ' + spec['rule'])


def _spoke_rule_settings(nodes, rules):
    """Native count reports omit rule names; qualify unconditional integer minima."""
    settings, conditional = [], []
    for node in nodes:
        if not isinstance(node, list) or len(node) < 2 or node[0] != 'rule':
            continue
        name = str(node[1])
        source = rules[name]
        hard = (isinstance(source.get('class'), str) and source['class'] in CLASSES - {'default'}
                and isinstance(source.get('citation'), dict) and source['citation'].get('path'))
        for constraint in node[2:]:
            if (not isinstance(constraint, list) or len(constraint) < 2 or
                    [str(c).lower() for c in constraint[:2]] != ['constraint', 'thermal_spoke_count']):
                continue
            if any(isinstance(c, list) and c and str(c[0]).lower() in ('condition', 'layer') for c in node[2:]):
                if hard:
                    conditional.append(name)
                continue
            if not hard:
                settings = []
                continue
            minima = [c for c in constraint[2:] if isinstance(c, list) and c and str(c[0]).lower() == 'min']
            if (len(minima) != 1 or len(minima[0]) != 2 or not str(minima[0][1]).isdigit()):
                raise ValueError('unsupported native thermal-spoke integer minimum: ' + name)
            # Native custom-rule precedence is last rule first.
            settings = [{**copy.deepcopy(source),
                         'setting': 'native_rules.' + name + '.min_resolved_spokes',
                         'value': int(minima[0][1]), 'enforcement': 'hard'}]
    return settings, conditional


def finding_sources(board, findings=()):
    """Read only the saved project's binding and same-stem captured native rules.

    Project and custom rules are immutable candidate inputs. A generated rule
    must be inventoried explicitly, so composition cannot turn it into owner
    authority merely by placing it in the native custom-rule file.
    """
    from pathlib import Path
    from .storage import digest
    from .net_constraints import rule_forms, template_rules
    board = Path(board)
    project_path = board.with_suffix('.kicad_pro')
    project = strict_json(project_path.read_bytes()) if project_path.is_file() else {}
    record = project.get(PROJECT_KEY, {})
    if not isinstance(record, dict):
        raise ValueError('constraint provenance binding must be an object')
    document = record.get('document')
    if document is not None:
        from .storage import json_bytes
        declaration(json_bytes(document))
    settings = inventory(project, document, record.get('path'))
    for setting in settings:
        for derived in record.get('derived_settings', []):
            if (setting['setting'] == derived['setting'] and setting['value'] == derived['value']
                    and setting['citation'] is None):
                setting.update(copy.deepcopy(derived))
    if record:
        bound = record.get('settings')
        if (not isinstance(bound, list) or any(not isinstance(s, dict) or
                not isinstance(s.get('setting'), str) for s in bound)):
            raise ValueError('constraint provenance binding has no valid settings inventory')
        names = {s['setting'] for s in bound}
        # KiCad materializes previously absent native defaults when saving a
        # board. Report those additions as advisory; never assign new authority
        # or excuse a change to any originally bound setting.
        if ([s for s in settings if s['setting'] in names] != bound or
                any(s['class'] != 'default' for s in settings if s['setting'] not in names)):
            raise ValueError('constraint provenance binding differs from saved project settings')
    for item in settings:
        if item['citation'] is not None:
            item['citation'].setdefault('sha256', record.get('sha256'))
    rules, rule_constraints, ignored_rules = {}, {}, []
    native_rules = []
    path = board.with_suffix('.kicad_dru')
    if path.is_file():
        raw = path.read_bytes()
        template_rules(raw, allow_generated=True)
        native_rules = rule_forms(raw, native=True)
        for node in native_rules:
            if isinstance(node, list) and len(node) > 1 and node[0] == 'rule':
                name = str(node[1])
                rule_constraints[name] = [str(c[1]).lower() for c in node[2:]
                                          if isinstance(c, list) and len(c) > 1 and str(c[0]).lower() == 'constraint']
                if any(isinstance(c, list) and len(c) > 1 and str(c[0]).lower() == 'severity'
                       and str(c[1]).lower() == 'ignore' for c in node[2:]):
                    ignored_rules.append(name)
                owner = record.get('owner_rules', {'path': str(path), 'sha256': digest(path)})
                rules[name] = {'class': 'owner', 'citation': {**owner, 'rule': name}}
        generated = record.get('generated_rules', {})
        require_owner_rule_classes(record, path.name, rules.keys() - generated.keys())
        for name, source in record.get('generated_rules', {}).items():
            if name in rules:
                rules[name] = source
    spoke_settings, conditional_spokes = _spoke_rule_settings(native_rules, rules)
    settings.extend(spoke_settings)
    from .net_binding import board_item_nets, board_net_names, literal_project_classes
    needed = {item['uuid'] for finding in findings
              if set(NUMERIC_FIELDS.get(finding.get('type'), ())) & set(NET_FIELDS.values())
              for item in finding.get('items', []) if isinstance(item.get('uuid'), str)}
    item_classes = {}
    if (needed and board.is_file() and any(s['setting'].startswith('net_settings.') and
            s['class'] in CLASSES - {'default'} for s in settings)):
        raw = board.read_bytes()
        item_nets = board_item_nets(raw)
        classes = literal_project_classes(project, board_net_names(raw) | {''})
        item_classes = {key: classes.get(item_nets[key]) for key in sorted(needed) if key in item_nets}
    return {'settings': settings, 'rules': rules, 'item_classes': item_classes,
            'rule_constraints': rule_constraints, 'ignored_rules': ignored_rules,
            'conditional_spoke_rules': conditional_spokes}


def classify_finding(finding, sources=None):
    """Resolve one native finding; unknown numeric authority remains visible advisory."""
    import re
    from decimal import Decimal
    sources = sources or {'settings': [], 'rules': {}}
    kind = finding.get('type', 'unknown')
    source = {'class': 'default', 'citation': None}
    reason = 'No unambiguous captured source for the native limit'
    actual_pattern = (r'actual ([0-9]+)(?=[); ]|$)' if kind == 'starved_thermal' else
                      r'actual (-?[0-9]+(?:\.[0-9]+)?) mm')
    limit_pattern = (r'min spoke count ([0-9]+); actual' if kind == 'starved_thermal' else
                     r' ([0-9]+(?:\.[0-9]+)?) mm; actual')
    actual = re.search(actual_pattern, finding.get('description', ''))
    if kind == 'starved_thermal' and (not actual or not re.search(limit_pattern, finding.get('description', ''))):
        raise ValueError('native thermal-spoke finding lacks a qualified count measurement')
    outside = kind == 'copper_edge_clearance' and actual and Decimal(actual[1]) < 0
    if kind in STRUCTURAL or outside:
        source = {'class': 'engineering', 'citation': {
            'path': 'docs/placer.md', 'locator': 'native DRC publication gate',
            'basis': 'Coordinator decisions AGENTEE-318/325 and AGENTEE-331: '
                     'shorts, co-located drills, copper beyond the outline, PTH courtyard intrusion '
                     'and introduced inter-footprint mask bridges'}}
        reason = 'Structural invariant; numeric provenance does not relax it'
    elif kind in ('courtyards_overlap', 'missing_courtyard', 'malformed_courtyard'):
        source = {'class': 'engineering', 'citation': {
            'path': 'docs/placer.md', 'locator': 'native courtyard qualification',
            'basis': 'Existing saved-board courtyard qualification before publication'}}
        reason = 'Structural courtyard qualification'
    else:
        description = finding.get('description', '')
        custom = re.search(r"\(rule '(.*?)' ", description)
        if custom:
            source = sources['rules'].get(custom[1], source)
            reason = 'Named native custom rule' if source['citation'] else reason
        elif kind in NUMERIC_FIELDS:
            fields = NUMERIC_FIELDS[kind]
            candidates = [s for s in sources['settings'] if
                          s['setting'].rsplit('.', 1)[-1] in fields and
                          type(s['value']) in (int, float)]
            if '(board setup constraints ' in description:
                candidates = [s for s in candidates if s['setting'].startswith('board.')]
            else:
                netclass = re.search(r"\(netclass '(.*?)' ", description)
                if netclass:
                    candidates = [s for s in candidates if s['setting'].rsplit('.', 1)[0] == 'net_settings.classes.' + netclass[1]]
                else:
                    classes = {sources.get('item_classes', {}).get(item.get('uuid'))
                               for item in finding.get('items', [])} - {None}
                    candidates = [s for s in candidates if s['setting'].startswith(('board.', 'native_rules.')) or
                                  any(s['setting'].rsplit('.', 1)[0] == 'net_settings.classes.' + cls
                                      for cls in classes)]
            # KiCad prints four decimal places. Do not attach a citation for a
            # different value just because the finding category is the same.
            limit = re.search(limit_pattern, description)
            if limit:
                candidates = [s for s in candidates if
                    abs(Decimal(str(s['value'])) - Decimal(limit[1])) < Decimal('0.00005')]
            elif len(fields) > 1:
                candidates = []
            hard = [s for s in candidates if s['class'] in CLASSES - {'default'} and s['citation']]
            authorities = {(s['class'], str(s['citation'])) for s in candidates}
            if hard or (len(authorities) == 1 and candidates):
                selected = hard or candidates
                source = {key: selected[0][key] for key in ('class', 'citation')}
                source['limits'] = [{key: s[key] for key in ('setting', 'value', 'class', 'citation')}
                                    for s in selected]
                reason = 'Native limit matched applicable emitted constraints'
    if kind in NUMERIC_FIELDS and actual and not outside:
        # KiCad names the effective limit, which can be an advisory default.
        # Evaluate the measured value against all applicable sourced minima.
        description = finding.get('description', '')
        netclass = re.search(r"\(netclass '(.*?)' ", description)
        classes = {sources.get('item_classes', {}).get(item.get('uuid'))
                   for item in finding.get('items', [])} - {None}
        if netclass:
            classes.add(netclass[1])
        violated = [s for s in sources['settings'] if
                    s['setting'].rsplit('.', 1)[-1] in NUMERIC_FIELDS[kind] and
                    type(s['value']) in (int, float) and
                    s['class'] in CLASSES - {'default'} and s['citation'] and
                    (s['setting'].startswith(('board.', 'native_rules.')) or any(
                        s['setting'].rsplit('.', 1)[0] == 'net_settings.classes.' + cls for cls in classes)) and
                    Decimal(actual[1]) < Decimal(str(s['value'])) - (Decimal(0) if kind == 'starved_thermal' else Decimal('0.00005'))]
        # Include the sourced limit KiCad named, including a generated custom
        # rule, so a weaker board minimum cannot replace a stricter source.
        if isinstance(source.get('class'), str) and source['class'] in CLASSES - {'default'} and source['citation']:
            named = re.search(r"\(rule '(.*?)' ", description)
            printed = re.search(limit_pattern, description)
            if named and printed:
                violated.append(dict(setting='custom_rules.' + named[1], value=float(printed[1]),
                                     **{key: source[key] for key in ('class', 'citation')}))
        if violated:
            priority = ('owner', 'manufacturer', 'datasheet', 'engineering')
            violated.sort(key=lambda s: (-s['value'], priority.index(s['class']), s['setting']))
            source = {key: violated[0][key] for key in ('class', 'citation')}
            source['limits'] = [{key: s[key] for key in ('setting', 'value', 'class', 'citation')}
                                for s in violated]
            reason = 'Actual value violates applicable sourced constraints'
    if (not isinstance(source.get('class'), str) or source['class'] not in CLASSES or
            (source['class'] != 'default' and
             (not isinstance(source.get('citation'), dict) or not source['citation'].get('path')))):
        source = {'class': 'default', 'citation': None}
        reason = 'Unresolved class or citation; never hard authority'
    finding['constraint_provenance'] = {**copy.deepcopy(source), 'reason': reason,
        'enforcement': 'advisory' if source['class'] == 'default' else 'hard'}
    return finding


def packing_clearances(board, sources, project):
    """Use only sourced numeric margins in a scaffold's in-memory packing probe.

    Physical copper/drill shapes and captured native custom rules remain intact.
    This changes no saved board or project; publication reruns unchanged rules.
    """
    from .net_binding import literal_project_classes
    import pcbnew
    hard = {s['setting']: s['value'] for s in sources['settings']
            if s['class'] in CLASSES - {'default'} and s['citation']}
    settings = board.GetDesignSettings()
    for field, native in (('min_clearance', 'm_MinClearance'),
                          ('min_hole_clearance', 'm_HoleClearance'),
                          ('min_hole_to_hole', 'm_HoleToHoleMin')):
        if 'board.design_settings.rules.' + field not in hard:
            setattr(settings, native, 0)
    pads = [pad for fp in board.GetFootprints() for pad in fp.Pads()]
    classes = literal_project_classes(project, {pad.GetNetname() for pad in pads})
    # KiCad's cached effective NETCLASS can retain a previous clearance after
    # recomputation. Supply sourced margins explicitly to native polygon calls,
    # rather than treating that cache or an uncited local override as authority.
    return {pad.m_Uuid.AsString(): int(pcbnew.FromMM(hard.get(
        'net_settings.classes.' + classes.get(pad.GetNetname(), '') + '.clearance', 0)))
        for pad in pads}


def numeric_finding(finding):
    """Recognize numeric native limits without assigning them source authority."""
    import re
    return (finding.get('type') in NUMERIC_FIELDS or
            re.search(r' [0-9]+(?:\.[0-9]+)? mm; actual', finding.get('description', '')) is not None)


def advisory_numeric(finding):
    """Only numeric rule findings can leave native grading through this policy."""
    return (finding.get('constraint_provenance', {}).get('enforcement') == 'advisory'
            and numeric_finding(finding))


NATIVE_CONSTRAINT_TYPES = {**{kind: kind for kind in NUMERIC_FIELDS},
    'starved_thermal': 'thermal_spoke_count',
    'drill_out_of_range': 'hole_size', 'copper_edge_clearance': 'edge_clearance',
    'silk_overlap': 'silk_clearance', 'silk_over_copper': 'silk_clearance',
    'silk_edge_clearance': 'silk_clearance'}


def require_evaluated_limits(report, sources):
    """An ignored sourced numeric limit is unavailable evaluation, never a pass."""
    if sources.get('conditional_spoke_rules') and any(
            v.get('type') == 'starved_thermal' for v in report.get('violations', [])):
        raise ValueError('conditional sourced thermal-spoke provenance unavailable: ' + str(
            {name: sources['rules'].get(name) for name in sources['conditional_spoke_rules']}))
    numeric = set(NATIVE_CONSTRAINT_TYPES.values())
    for name in sources.get('ignored_rules', []):
        source = sources['rules'][name]
        if (source['class'] in CLASSES - {'default'} and
                numeric.intersection(sources['rule_constraints'][name])):
            raise ValueError('native DRC evaluation explicitly ignored by sourced rule '
                             + name + ': ' + str(source))
    for ignored in report.get('ignored_checks', []):
        key = ignored.get('key')
        if key == 'holes_co_located':
            raise ValueError('native DRC evaluation explicitly ignored for structural ' + key)
        fields = NUMERIC_FIELDS.get(key, ())
        hard = [s for s in sources['settings'] if s['setting'].rsplit('.', 1)[-1] in fields
                and s['class'] in CLASSES - {'default'} and s['citation']]
        hard_rules = [dict(rule=name, **source) for name, source in sources['rules'].items()
                      if source['class'] in CLASSES - {'default'} and key in NATIVE_CONSTRAINT_TYPES
                      and NATIVE_CONSTRAINT_TYPES[key] in sources['rule_constraints'].get(name, [])]
        if hard or hard_rules:
            raise ValueError('native DRC evaluation explicitly ignored for sourced '
                             + key + ': ' + str(hard + hard_rules))
