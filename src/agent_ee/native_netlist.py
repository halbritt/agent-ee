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
