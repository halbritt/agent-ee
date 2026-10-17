import os, urllib.request, subprocess
import xml.etree.ElementTree as ET

poly_files = [
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
    target = f"/work/experiments/ref_polygonus/{fname}"
    if os.path.exists(target) and os.path.getsize(target) > 0:
        continue
    url = f"https://raw.githubusercontent.com/FOME-Tech/Polygonus/master/{fname}"
    for attempt in range(3):
        try:
            data = urllib.request.urlopen(url, timeout=15).read().decode("utf-8")
            with open(target, "w") as f:
                f.write(data)
            print("Saved Polygonus:", fname)
            break
        except Exception as e:
            print(f"Retry {fname}: {e}")

wb_fw_files = [
    "firmware/boards/f0_module/io/io_pins.h",
    "firmware/boards/f0_module/port.cpp",
    "firmware/boards/f0_module/wideband_board_config.h",
    "firmware/can.cpp",
]
os.makedirs("/work/experiments/ref_wideband/fw", exist_ok=True)
for p in wb_fw_files:
    fname = os.path.basename(p)
    url = f"https://raw.githubusercontent.com/mck1117/wideband/master/{p}"
    try:
        data = urllib.request.urlopen(url, timeout=15).read().decode("utf-8")
        with open(f"/work/experiments/ref_wideband/fw/{fname}", "w") as f:
            f.write(data)
        print("Saved WB fw:", fname)
    except Exception as e:
        print(f"WB fw {p}: {e}")
