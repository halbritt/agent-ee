import os, json, urllib.request, subprocess
import xml.etree.ElementTree as ET

os.makedirs("/work/experiments/ref_wideband", exist_ok=True)
os.makedirs("/work/experiments/ref_polygonus", exist_ok=True)

# 1. Fetch mck1117/wideband tree
wb_tree_url = "https://api.github.com/repos/mck1117/wideband/git/trees/master?recursive=1"
wb_tree = json.loads(urllib.request.urlopen(wb_tree_url, timeout=10).read().decode("utf-8"))
for item in wb_tree.get("tree", []):
    p = item["path"]
    if p.startswith("hardware/board_module/") and p.endswith(".kicad_sch"):
        fname = os.path.basename(p)
        url = f"https://raw.githubusercontent.com/mck1117/wideband/master/{p}"
        data = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
        with open(f"/work/experiments/ref_wideband/{fname}", "w") as f:
            f.write(data)
        print("Saved wideband:", fname)
    if p.startswith("firmware/") and any(k in p.lower() for k in ["can", "port", "board", "io", "pin", "main", "config"]):
        print("Wideband firmware file:", p)

# 2. Fetch Polygonus schematics & README
poly_files = [
    "README.md",
    "polygonus-Shortage-Version.kicad_sch",
    "Keep_Alive.kicad_sch",
    "etb-9201.kicad_sch",
    "highside_quad.kicad_sch",
    "ign4.kicad_sch",
    "knock.kicad_sch",
    "lowside_quad.kicad_sch",
    "mcu.kicad_sch",
    "psu.kicad_sch",
    "quad_analog.kicad_sch",
    "quad_analog_pullup.kicad_sch",
    "triggers.kicad_sch",
]
for fname in poly_files:
    url = f"https://raw.githubusercontent.com/FOME-Tech/Polygonus/master/{fname}"
    data = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
    with open(f"/work/experiments/ref_polygonus/{fname}", "w") as f:
        f.write(data)
    print("Saved Polygonus:", fname)

