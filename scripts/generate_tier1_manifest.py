#!/usr/bin/env python3
"""Generate Tier-1 Task Manifest & Wrappers for PCBSchemaGen dual-gate benchmark."""

import json
from pathlib import Path

PCBBENCH_PATH = Path("/home/halbritt/git/PCBSchemaGen-Benchmarks/pcbbench/pcbbench.jsonl")
OSE_PATH = Path("/home/halbritt/git/PCBSchemaGen-Benchmarks/open_schematics/open_schematics.jsonl")
OUT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pcbschemagen-tier1"
TASKS_DIR = OUT_DIR / "tasks"

PCBBENCH_TASKS = [
    "3", "5", "6", "13", "14", "25", "27", "28", "29", "30",
    "31", "32", "33", "37", "38", "42", "43", "44",
]
OSE_TASKS = ["104", "146", "176", "181", "208", "218", "246"]

META = {
    "3": {"primary_ic": "OPA328", "package": "SOT-23-5", "domain": "Sensing"},
    "5": {"primary_ic": "TLV1117-33", "package": "SOT-223", "domain": "Power"},
    "6": {"primary_ic": "TPS54302", "package": "SOT-23-6", "domain": "Power"},
    "13": {"primary_ic": "UCC27511", "package": "SOT-23-6", "domain": "Driver"},
    "14": {"primary_ic": "UCC27211", "package": "SOIC-8", "domain": "Driver"},
    "25": {"primary_ic": "OPA328", "package": "SOT-23-5", "domain": "Filter"},
    "27": {"primary_ic": "INA240A1", "package": "TSSOP-8", "domain": "Sensing"},
    "28": {"primary_ic": "INA333", "package": "MSOP-8", "domain": "Sensing"},
    "29": {"primary_ic": "MAX31865", "package": "SSOP-20", "domain": "Sensing"},
    "30": {"primary_ic": "AD8495", "package": "MSOP-8", "domain": "Sensing"},
    "31": {"primary_ic": "ADS7042", "package": "VSSOP-8", "domain": "DataConv"},
    "32": {"primary_ic": "MCP4921 + OPA328", "package": "SOIC-8 + SOT-23-5", "domain": "DataConv"},
    "33": {"primary_ic": "REF3033", "package": "SOT-23-3", "domain": "DataConv"},
    "37": {"primary_ic": "SN65HVD230", "package": "SOIC-8", "domain": "Comm"},
    "38": {"primary_ic": "SP3485", "package": "SOIC-8", "domain": "Comm"},
    "42": {"primary_ic": "W25Q64JV", "package": "SOIC-8", "domain": "Memory"},
    "43": {"primary_ic": "AT24C256", "package": "SOIC-8", "domain": "Memory"},
    "44": {"primary_ic": "INA226", "package": "VSSOP-10", "domain": "Power"},
    "104": {"primary_ic": "CH32V003JxMx + AP2112K", "package": "SOP-8 + SOT-23-5", "domain": "MCU/Power"},
    "146": {"primary_ic": "LM1117MP-5.0", "package": "SOT-223", "domain": "Power"},
    "176": {"primary_ic": "MAX40200AUK_T", "package": "SOT-23-5", "domain": "Power"},
    "181": {"primary_ic": "AP63203WU", "package": "TSOT-26", "domain": "Power"},
    "208": {"primary_ic": "ACS711xEXLT", "package": "SOIC-8", "domain": "Sensing"},
    "218": {"primary_ic": "TPS3839DBZ", "package": "SOT-23-3", "domain": "Supervisor"},
    "246": {"primary_ic": "AP2171W", "package": "SOT-23-5", "domain": "Power"},
}

HEADER_WRAPPER_TEXT = """### Interface Authorization Wrapper (2.54 mm Pin Headers)
For all external signals, bus connections, and power rails (including VIN, VOUT, GND, logic controls, and communication lines):
1. **Connector Footprints**: Terminate all external nets at standard 2.54 mm pitch vertical through-hole or SMT pin headers (KiCad library: `Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical`, `PinHeader_1x03_P2.54mm_Vertical`, `PinHeader_1x04_P2.54mm_Vertical`, etc.).
2. **Physical Edge Placement**: Declare all connectors in the `connectors` array, positioning them along the board outline edges (`top`, `bottom`, `left`, `right`) with courtyard boundaries within 1 mm of the board perimeter.
3. **Standard Pin Ordering**:
   - Power interfaces: `[VIN, GND]` or `[VCC, GND]`
   - Analog / Sense: `[IN+, IN-, GND]` or `[VSENSE, GND]`
   - Serial UART: `[VCC, GND, TX, RX]`
   - I2C bus: `[GND, VCC, SDA, SCL]`
   - CAN bus: `[CANH, CANL, GND]`
   - RS-485 bus: `[A, B, GND]`
   - SPI bus: `[VCC, GND, SCK, MOSI, MISO, CS]`
4. **Stock & Assembly**: Use standard JLCPCB SMT/THT stock part numbers (e.g. C22548, C2897383) or equivalent generic 2.54 mm header pins.
"""


def clean_nodes(s):
    if not s:
        return []
    if isinstance(s, list):
        return s
    return [x.strip() for x in str(s).split(",") if x.strip()]


def build_manifest():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)

    raw_records = {}
    with open(PCBBENCH_PATH, "r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            tid = str(r.get("id"))
            if tid in PCBBENCH_TASKS:
                r["dataset"] = "pcbbench"
                raw_records[tid] = r

    with open(OSE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            tid = str(r.get("id"))
            if tid in OSE_TASKS:
                r["dataset"] = "open_schematics"
                raw_records[tid] = r

    task_list = []
    for tid in sorted(raw_records.keys(), key=lambda x: (raw_records[x]["dataset"], int(x))):
        r = raw_records[tid]
        m = META.get(tid, {})
        in_nodes = clean_nodes(r.get("input_nodes"))
        out_nodes = clean_nodes(r.get("output_nodes"))
        base_prompt = r.get("task", "").strip()
        wrapped_prompt = f"{base_prompt}\n\n{HEADER_WRAPPER_TEXT}".strip()

        conn_list = []
        if in_nodes:
            n_in = max(2, len(in_nodes))
            ref_in = "J1"
            fp_in = f"Connector_PinHeader_2.54mm:PinHeader_1x{n_in:02d}_P2.54mm_Vertical"
            lib_in = f"Connector_Generic:Conn_01x{n_in:02d}"
            conn_list.append({
                "ref": ref_in,
                "description": "External input/power interface",
                "footprint": fp_in,
                "lib_id": lib_in,
                "edge": "left",
                "pin_count": n_in,
                "nets": in_nodes,
            })
        if out_nodes:
            n_out = max(2, len(out_nodes))
            ref_out = "J2" if in_nodes else "J1"
            fp_out = f"Connector_PinHeader_2.54mm:PinHeader_1x{n_out:02d}_P2.54mm_Vertical"
            lib_out = f"Connector_Generic:Conn_01x{n_out:02d}"
            conn_list.append({
                "ref": ref_out,
                "description": "External output/signal interface",
                "footprint": fp_out,
                "lib_id": lib_out,
                "edge": "right",
                "pin_count": n_out,
                "nets": out_nodes,
            })

        task_obj = {
            "id": str(tid),
            "dataset": r["dataset"],
            "sub_module_name": r.get("sub_module_name", ""),
            "level": r.get("level", "Medium"),
            "type": r.get("type", m.get("domain", "General")),
            "primary_ic": m.get("primary_ic", str(r.get("components", ""))),
            "package": m.get("package", "SMD"),
            "input_nodes": in_nodes,
            "output_nodes": out_nodes,
            "input_voltage": str(r.get("input_voltage", "")),
            "output_voltage": str(r.get("output_voltage", "")),
            "components": r.get("components"),
            "task_prompt": base_prompt,
            "wrapped_prompt": wrapped_prompt,
            "authorized_headers": conn_list,
        }
        task_list.append(task_obj)

        (TASKS_DIR / f"{tid}.json").write_text(json.dumps(task_obj, indent=2), encoding="utf-8")
        md_content = f"""# Task {tid}: {task_obj['sub_module_name']} ({task_obj['dataset']})

- **Dataset**: {task_obj['dataset']}
- **Level**: {task_obj['level']}
- **Domain**: {task_obj['type']}
- **Primary IC**: {task_obj['primary_ic']} ({task_obj['package']})
- **Input Nodes**: {', '.join(in_nodes) or 'None'}
- **Output Nodes**: {', '.join(out_nodes) or 'None'}
- **Input Voltage**: {task_obj['input_voltage']}
- **Output Voltage**: {task_obj['output_voltage']}

## Engineering Specification

{wrapped_prompt}
"""
        (TASKS_DIR / f"{tid}.md").write_text(md_content, encoding="utf-8")

    manifest = {
        "schema": "agent-ee.pcbschemagen-tier1-manifest/1",
        "suite": "PCBSchemaGen-Tier1",
        "version": "1.0.0",
        "description": "Tier-1 25-task discrete-IC benchmark suite for PCBSchemaGen dual-gate evaluation",
        "total_tasks": len(task_list),
        "counts": {
            "pcbbench": sum(1 for t in task_list if t["dataset"] == "pcbbench"),
            "open_schematics": sum(1 for t in task_list if t["dataset"] == "open_schematics"),
        },
        "wrapper_spec": {
            "header_pitch_mm": 2.54,
            "footprint_pattern": "Connector_PinHeader_2.54mm:PinHeader_1x..._P2.54mm_Vertical",
            "symbol_pattern": "Connector_Generic:Conn_01x...",
            "edge_rule": "Edge-mounted along outline perimeter (within 1 mm of board edge)",
            "text": HEADER_WRAPPER_TEXT,
        },
        "tasks": task_list,
    }

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Manifest generated successfully!")
    print(f"Total tasks: {len(task_list)}")
    print("PCBBench:", manifest["counts"]["pcbbench"])
    print("OpenSchematics:", manifest["counts"]["open_schematics"])


if __name__ == "__main__":
    build_manifest()
