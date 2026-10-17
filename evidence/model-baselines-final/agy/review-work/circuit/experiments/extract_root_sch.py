import re

with open('/candidate/project/Polygonus-Universal-Base.kicad_sch') as f:
    lines = f.readlines()

# Find where lib_symbols ends
lib_end = 0
depth = 0
in_lib = False
for i, line in enumerate(lines):
    if '(lib_symbols' in line:
        in_lib = True
        depth = line.count('(') - line.count(')')
    elif in_lib:
        depth += line.count('(') - line.count(')')
        if depth == 0:
            lib_end = i + 1
            break

print(f"lib_symbols ends at line {lib_end + 1}")
with open('/work/experiments/root_sch_placed.txt', 'w') as out:
    out.write(f"lib_symbols ends at line {lib_end + 1} of {len(lines)}\n\n")
    for i in range(lib_end, len(lines)):
        out.write(f"{i+1}: {lines[i]}")
print(f"Wrote {len(lines) - lib_end} lines to /work/experiments/root_sch_placed.txt")
