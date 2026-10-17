#!/usr/bin/env python3
import xml.etree.ElementTree as ET
import json
import os
import re

tree = ET.parse('/evidence/work/netlist/design.xml')
root = tree.getroot()

print("=== SHEETS IN DESIGN.XML ===")
for sheet in root.findall('.//sheet'):
    num = sheet.get('number')
    name = sheet.get('name')
    tstamps = sheet.get('tstamps')
    src = sheet.find('.//source')
    src_text = src.text if src is not None else ''
    print(f"Sheet {num}: {name} (tstamps={tstamps}) -> {src_text}")

print("\n=== COMPONENTS IN DESIGN.XML ===")
comps = {}
for comp in root.findall('.//comp'):
    ref = comp.get('ref')
    val = comp.findtext('value', '')
    fp = comp.findtext('footprint', '')
    ds = comp.findtext('datasheet', '')
    ls = comp.find('libsource')
    lib = ls.get('lib', '') if ls is not None else ''
    part = ls.get('part', '') if ls is not None else ''
    props = {}
    for prop in comp.findall('property'):
        props[prop.get('name')] = prop.get('value')
    fields = {}
    for f in comp.findall('.//field'):
        fields[f.get('name')] = f.text
    sheetpath = comp.find('sheetpath')
    sname = sheetpath.get('names', '') if sheetpath is not None else ''
    dnp = props.get('dnp') is not None or comp.find('dnp') is not None
    comps[ref] = {
        'ref': ref, 'val': val, 'fp': fp, 'ds': ds,
        'lib': lib, 'part': part, 'props': props, 'fields': fields,
        'sheet': sname, 'dnp': dnp
    }
    lcsc = fields.get('LCSC') or props.get('LCSC') or ''
    print(f"{ref:6s} | val={str(val):22s} | part={lib}:{str(part):22s} | fp={str(fp):45s} | LCSC={str(lcsc):10s} | dnp={dnp} | sheet={sname}")

print("\n=== NETS AND PIN DETAILS ===")
nets = {}
for net in root.findall('.//net'):
    code = net.get('code')
    name = net.get('name')
    nodes = []
    for node in net.findall('node'):
        ref = node.get('ref')
        pin = node.get('pin')
        pfunc = node.get('pinfunction', '')
        ptype = node.get('pintype', '')
        nodes.append((ref, pin, pfunc, ptype))
    nets[name] = nodes

for name, nodes in sorted(nets.items()):
    node_str = ", ".join([f"{r}.{p}({pf}:{pt})" for r, p, pf, pt in nodes])
    print(f"{name:32s}: {node_str}")
