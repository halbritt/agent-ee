#!/usr/bin/env python3
import re
import glob
import os

def parse_pins(sym_block):
    pins = []
    for m in re.finditer(r'\(pin\s+(\S+)\s+(\S+)\s+\(at\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\).*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"', sym_block, re.DOTALL):
        ptype, pshape, x, y, rot, pname, pnum = m.groups()
        pins.append((pnum, pname, ptype, float(x), float(y), float(rot)))
    return sorted(pins, key=lambda x: (int(x[0]) if x[0].isdigit() else 9999, x[0]))

with open('/candidate/project/Polygonus-Universal-Base.kicad_sch') as f:
    base_sch = f.read()

# Find polygonus symbol in base_sch
m = re.search(r'\(symbol\s+"fome_modules:polygonus"(.*?)\n\t\t\)\n', base_sch, re.DOTALL)
if m:
    print("=== EMBEDDED fome_modules:polygonus PINS ===")
    for p in parse_pins(m.group(1)):
        print(f"  Pin {p[0]:4s}: name={p[1]:20s} type={p[2]:15s} at=({p[3]}, {p[4]}, {p[5]})")

with open('/candidate/project/fome-kicad-libs/fome_modules.kicad_sym') as f:
    fome_lib = f.read()

m2 = re.search(r'\(symbol\s+"polygonus"(.*?)\n\t\)\n', fome_lib, re.DOTALL)
if m2:
    print("\n=== LIBRARY fome_modules:polygonus PINS ===")
    for p in parse_pins(m2.group(1)):
        print(f"  Pin {p[0]:4s}: name={p[1]:20s} type={p[2]:15s} at=({p[3]}, {p[4]}, {p[5]})")
