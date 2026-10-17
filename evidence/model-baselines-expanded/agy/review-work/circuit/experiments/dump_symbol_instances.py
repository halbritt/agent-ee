#!/usr/bin/env python3
import glob
import os
import re

for path in sorted(glob.glob('/candidate/project/*.kicad_sch')):
    with open(path) as f:
        text = f.read()
    # Find where lib_symbols ends
    lib_end = text.find('\n\t)\n')
    if lib_end == -1:
        lib_end = 0
    rest = text[lib_end:]
    base_line = text[:lib_end].count('\n') + 1
    print("=" * 80)
    print(f"FILE: {os.path.basename(path)}")
    for m in re.finditer(r'\n\t\(symbol\n(.*?)\n\t\)', rest, re.DOTALL):
        body = m.group(1)
        line_no = base_line + rest[:m.start()].count('\n') + 1
        lib_id = re.search(r'\(lib_id\s+"([^"]*)"\)', body)
        at = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)\s*([\d.-]*)\)', body)
        unit = re.search(r'\(unit\s+(\d+)\)', body)
        dnp = re.search(r'\(dnp\s+(\S+)\)', body)
        in_bom = re.search(r'\(in_bom\s+(\S+)\)', body)
        props = dict(re.findall(r'\(property\s+"([^"]*)"\s+"([^"]*)"', body))
        ref = props.get('Reference', '')
        val = props.get('Value', '')
        if ref.startswith('#PWR') or ref.startswith('#FLG'):
            continue
        fp = props.get('Footprint', '')
        lcsc = props.get('LCSC', '')
        pn = props.get('PN', '')
        inst_refs = re.findall(r'\(reference\s+"([^"]*)"\)', body)
        print(f"  L{line_no:5d}: ref={ref:6s} inst={inst_refs} unit={unit.group(1) if unit else '?'} val={val:20s} lib={lib_id.group(1) if lib_id else '?':28s} fp={fp:40s} LCSC={lcsc:10s} PN={pn:15s} dnp={dnp.group(1) if dnp else '?'} bom={in_bom.group(1) if in_bom else '?'}")
