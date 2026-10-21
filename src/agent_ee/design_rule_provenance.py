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
NET_FIELDS = {'clearance_mm': 'clearance', 'track_width_mm': 'track_width'}


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
    for rules in custom.values():
        if not isinstance(rules, list):
            raise ValueError('constraint source custom rules must be lists')
        for spec in rules:
            _spec(spec)
            if not isinstance(spec.get('rule'), str) or not spec['rule'].strip():
                raise ValueError('constraint source custom rule needs a native rule name')
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
