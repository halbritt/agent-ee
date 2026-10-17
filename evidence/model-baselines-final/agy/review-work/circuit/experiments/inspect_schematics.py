#!/usr/bin/env python3
"""
Inspect all .kicad_sch files in /candidate/project/ to extract text annotations,
labels, global_labels, hierarchical_labels, sub-sheets, and symbol placements.
"""
import glob
import os
import re

def parse_sexp(text):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|[^\s()"]+', text)
    stack = [[]]
    for tok in tokens:
        if tok == '(':
            new_list = []
            stack[-1].append(new_list)
            stack.append(new_list)
        elif tok == ')':
            stack.pop()
        else:
            if tok.startswith('"') and tok.endswith('"'):
                tok = tok[1:-1].replace('\\"', '"').replace('\\\\', '\\')
            stack[-1].append(tok)
    return stack[0][0] if stack[0] else []

def get_prop(node, prop_name):
    for item in node:
        if isinstance(item, list) and len(item) >= 3 and item[0] == 'property' and item[1] == prop_name:
            return item[2]
    return None

def get_sub(node, tag):
    for item in node:
        if isinstance(item, list) and len(item) >= 1 and item[0] == tag:
            return item[1:]
    return None

def main():
    sch_files = sorted(glob.glob("/candidate/project/*.kicad_sch"))
    for path in sch_files:
        fname = os.path.basename(path)
        print(f"==================================================")
        print(f"FILE: {fname}")
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = parse_sexp(content)
        for item in tree:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0]
            if tag in ('text', 'label', 'global_label', 'hierarchical_label', 'netclass_flag', 'directive_label'):
                at = get_sub(item, 'at')
                print(f"  [{tag}] '{item[1]}' @ {at}")
            elif tag == 'sheet':
                at = get_sub(item, 'at')
                sname = get_prop(item, 'Sheetname')
                sfile = get_prop(item, 'Sheetfile')
                pins = []
                for sub in item:
                    if isinstance(sub, list) and sub and sub[0] == 'pin':
                        pins.append(f"{sub[1]}({sub[2]})")
                print(f"  [sheet] '{sname}' -> '{sfile}' @ {at} pins={pins}")
            elif tag == 'symbol':
                lib_id = get_sub(item, 'lib_id')
                at = get_sub(item, 'at')
                dnp = get_sub(item, 'dnp')
                ref = get_prop(item, 'Reference')
                val = get_prop(item, 'Value')
                fp = get_prop(item, 'Footprint')
                lcsc = get_prop(item, 'LCSC')
                pn = get_prop(item, 'PN')
                if ref and not ref.startswith('#PWR') and not ref.startswith('#FLG'):
                    print(f"  [symbol] {ref:6s} val={val:20s} lib={lib_id[0] if lib_id else ''} at={at} dnp={dnp[0] if dnp else ''} fp={fp} LCSC={lcsc} PN={pn}")

if __name__ == '__main__':
    main()
