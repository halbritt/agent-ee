#!/usr/bin/env python3
import glob
import os
import re

sch_files = sorted(glob.glob('/candidate/project/*.kicad_sch'))
for path in sch_files:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    print("=" * 80)
    print(f"FILE: {os.path.basename(path)} ({len(text)} bytes, {text.count(chr(10))+1} lines)")
    
    # Extract text blocks
    for m in re.finditer(r'\(text\s+"((?:[^"\\]|\\.)*)"\s+.*?\(at\s+([\d.-]+)\s+([\d.-]+)', text, re.DOTALL):
        t, x, y = m.group(1), m.group(2), m.group(3)
        line_no = text[:m.start()].count('\n') + 1
        print(f"  [TEXT L{line_no} @ ({x},{y})]: {repr(t)}")
    
    # Extract labels (label, global_label, hierarchical_label)
    for ltype in ['label', 'global_label', 'hierarchical_label']:
        for m in re.finditer(rf'\({ltype}\s+"((?:[^"\\]|\\.)*)"\s+(.*?)\n\t\)', text, re.DOTALL):
            name, body = m.group(1), m.group(2)
            at_m = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)\s*([\d.-]*)\)', body)
            at_str = f"({at_m.group(1)},{at_m.group(2)})" if at_m else ""
            line_no = text[:m.start()].count('\n') + 1
            print(f"  [{ltype.upper()} L{line_no} @ {at_str}]: {name}")

    # Extract sheets
    for m in re.finditer(r'\(sheet\s+(.*?)\n\t\)', text, re.DOTALL):
        body = m.group(1)
        line_no = text[:m.start()].count('\n') + 1
        sname = re.search(r'\(property\s+"Sheetname"\s+"([^"]*)"', body)
        sfile = re.search(r'\(property\s+"Sheetfile"\s+"([^"]*)"', body)
        pins = re.findall(r'\(pin\s+"([^"]*)"\s+(\S+)', body)
        print(f"  [SHEET L{line_no}]: name={sname.group(1) if sname else '?'} file={sfile.group(1) if sfile else '?'} pins={pins}")
