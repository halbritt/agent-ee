"""Strict full KiCad XML inventory, independent of flattened connectivity exports.

The returned graph preserves each instance and selected physical pin. Callers may
use this read-only inventory for BOM checks without reparsing lossy JSON readers.
See docs/native-binding.md for the board, BOM and population fields.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .hardware_interface import text
from .netlist_parity import MAX_BYTES, Unsupported, terminal_inventory

# Valueless instance flags observed in KiCad 10.0.7 XML exports. Board and BOM
# membership are independent; DNP keeps a component on the board.
FLAGS = frozenset(('dnp', 'exclude_from_board', 'exclude_from_bom'))


def _one(parent, tag):
    nodes = parent.findall(tag)
    if len(nodes) != 1:
        raise ValueError(f'expected one native {tag}')
    return nodes[0]


def _parse(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Unsupported('native XML byte limit exceeded')
    decoded = raw.decode('utf-8-sig')
    if '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
        raise ValueError('XML declarations/entities are not supported')
    root = ET.fromstring(decoded)
    if root.tag != 'export' or root.get('version') not in ('E',):
        raise ValueError('unsupported native XML export schema')
    for tag in ('design', 'components', 'libparts', 'nets'):
        _one(root, tag)
    pending, count = [(root, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100_000:
            raise Unsupported('native XML structure limit exceeded')
        pending.extend((child, depth + 1) for child in node)
    return root


def _parts(root):
    parts = {}
    for part in root.findall('./libparts/libpart'):
        symbol = (text(part.get('lib')), text(part.get('part')))
        pins = {}
        for pin in _one(part, 'pins').findall('pin'):
            number = text(pin.get('num'))
            if number in pins:
                raise ValueError('duplicate native library pin')
            pins[number] = {'name': pin.get('name', ''), 'electrical_type': text(pin.get('type'))}
        if symbol in parts or not pins:
            raise ValueError('duplicate or empty native library part')
        parts[symbol] = pins
    return parts


def _components(root, parts, sheets):
    components, identities = {}, set()
    for comp in root.findall('./components/comp'):
        ref = text(comp.get('ref'))
        lib = _one(comp, 'libsource')
        symbol = text(lib.get('lib')), text(lib.get('part'))
        sheet = text(_one(comp, 'sheetpath').get('tstamps'))
        uuid = text(_one(comp, 'tstamps').text)
        instance_uuids = uuid.split()
        if (ref in components or sheet not in sheets or len(instance_uuids) != len(set(instance_uuids))
                or any((sheet, uid) in identities for uid in instance_uuids)):
            raise ValueError('duplicate or undeclared native component identity')
        if symbol not in parts:
            raise ValueError('missing native library inventory')
        identities.update((sheet, uid) for uid in instance_uuids)
        units, selected = [], []
        for unit in _one(comp, 'units').findall('unit'):
            name = text(unit.get('name'))
            pins = [text(p.get('num')) for p in _one(unit, 'pins').findall('pin')]
            if name in units or not pins or len(pins) != len(set(pins)):
                raise ValueError('ambiguous native selected unit inventory')
            units.append(name)
            selected.extend(pins)
        # Common physical pins can occur in more than one graphical unit; only
        # libpart supplies their unique metadata, never a last-writer override.
        selected = sorted(set(selected))
        if not selected or not set(selected) <= parts[symbol].keys():
            raise ValueError('missing native selected physical pin inventory')
        properties = {}
        for prop in comp.findall('property'):
            name = text(prop.get('name'))
            if name in properties:
                raise ValueError('duplicate native component property')
            if name in FLAGS and (prop.attrib.keys() != {'name'} or len(prop) or (prop.text or '').strip()):
                raise ValueError('unqualified native instance flag form')
            properties[name] = prop.get('value')
        components[ref] = {'reference': ref, 'sheet': sheet, 'uuid': uuid,
            'lib_id': ':'.join(symbol), 'value': _one(comp, 'value').text or '',
            'datasheet': _datasheet(comp, properties), 'footprint': _footprint(comp), 'populated': 'dnp' not in properties,
            'on_board': 'exclude_from_board' not in properties,
            'in_bom': 'exclude_from_bom' not in properties, 'properties': properties,
            'pins': tuple(selected), 'units': tuple(units),
            'pin_metadata': {pin: parts[symbol][pin] for pin in selected},
            'library_pins': tuple(sorted(parts[symbol])),
            'locator': f'components/comp[@ref={ref!r}], sheet={sheet}, UUID={uuid}'}
    return components


def _datasheet(comp, properties):
    """Preserve absent versus empty; reject contradictory native representations."""
    nodes = comp.findall('datasheet')
    if len(nodes) > 1 or any(n.attrib or len(n) for n in nodes):
        raise ValueError('duplicate or structured native datasheet')
    values = [(n.text or '') for n in nodes]
    for field in comp.findall("./fields/field[@name='Datasheet']"):
        if set(field.attrib) != {'name'} or len(field):
            raise ValueError('structured native Datasheet field')
        values.append(field.text or '')
    if 'Datasheet' in properties:
        prop = comp.find("./property[@name='Datasheet']")
        if set(prop.attrib) != {'name', 'value'} or len(prop) or (prop.text or '').strip():
            raise ValueError('unqualified native Datasheet property')
        values.append(properties['Datasheet'])
    if values and any(value != values[0] for value in values):
        raise ValueError('contradictory native Datasheet representations')
    return values[0] if values else None


def _footprint(comp):
    """Zero or one plain element; an absent or empty one is a meaningful empty value."""
    nodes = comp.findall('footprint')
    if len(nodes) > 1 or any(len(n) or n.attrib for n in nodes):
        raise ValueError('duplicate or structured native footprint')
    value = (nodes[0].text or '') if nodes else ''
    containers = comp.findall('fields')
    if len(containers) > 1:
        raise ValueError('duplicate native field container')
    fields = containers[0].findall('field') if containers else []
    names = [text(f.get('name')) for f in fields]
    if len(names) != len(set(names)):
        raise ValueError('duplicate native component field')
    for field in fields:
        if field.get('name') == 'Footprint' and (field.text or '') != value:
            raise ValueError('native Footprint field contradicts footprint element')
    return value


def read_native_inventory(path):
    """Read bounded captured bytes; reject ambiguous identities and memberships."""
    root = _parse(path)
    sheets = [text(s.get('tstamps')) for s in root.findall('./design/sheet')]
    if ('/' not in sheets or len(sheets) != len(set(sheets)) or
            any(not s.startswith('/') or not s.endswith('/') for s in sheets)):
        raise ValueError('missing or ambiguous native hierarchy inventory')
    parts = _parts(root)
    components = _components(root, parts, sheets)
    nets, names = {}, set()
    for net in root.findall('./nets/net'):
        nid, name = text(net.get('code')), text(net.get('name'))
        if nid in nets or name in names:
            raise ValueError('duplicate native net identity')
        names.add(name)
        terminals = [(text(n.get('ref')), text(n.get('pin'))) for n in net.findall('node')]
        nodes = net.findall('node')
        marked_nc = (len(nodes) == 1 and 'no_connect' in nodes[0].get('pintype', '').split('+')[1:])
        nets[nid] = {'name': name, 'terminals': tuple(terminals),
            'no_connect_marker': marked_nc,
            'auto_unconnected_singleton': len(nodes) == 1 and name.startswith('unconnected-(') and name.endswith(')'),
            'locator': f'nets/net[@code={nid!r}][@name={name!r}]'}
    graph = terminal_inventory(components, nets)
    graph['sheets'] = sheets
    # Complete per-symbol inventory, including pins of unplaced graphical units.
    graph['library_parts'] = {':'.join(symbol): pins for symbol, pins in parts.items()}
    graph['exporter'] = root.findtext('./design/tool') or ''
    return graph
