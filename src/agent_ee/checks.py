"""Native and engineering checks on a captured candidate (Fable-owned).

`run_checks(run_dir, deadline)` loads `candidate.json`, iterates
`scope.checks`, runs every check through `agent_ee.executor.execute` (native
tools see the read-only candidate at /candidate and one writable scratch at
/work), and writes `checks/<id>.json` immediately per check. Validation logic
for report shape, sheet coverage, silent parity skips and ngspice output is
adapted from fome-mc `toolchain/eda.py` (read at fome-mc commit c43ad7b).

Check kinds and parameters:

- `erc`, `netlist`: no parameters; `drc`: optional `projection`. ERC
  validates sheet-instance coverage against the hierarchy; DRC runs with
  `--schematic-parity` on the as-saved board and detects kicad-cli's silent
  parity skip. `projection: "placement-stage/1"` grades violations and parity
  only; the raw report and unconnected count are retained ungraded, and a
  later full DRC must be declared; `netlist`
  exports the native XML netlist, verifies the sheet hierarchy is complete
  (a missing sheet file means the netlist cannot be complete), and derives
  `connectivity.json` with per-component DNP, libsource and sheet/UUID
  provenance.
- `geometry`: reads the saved board through pcbnew inside the sandbox
  (`agent_ee.geometry_probe`). Parameters: `min_track_width_mm` (float,
  optional) — declared fixed minimum copper track segment width;
  `exclude_nets` (list[str], optional) — net names exempt from that minimum.
  Without `min_track_width_mm` the legacy check reports facts only (outcome `info`).
  Opt in to `criterion: "copper-width/1"` with explicit `covered_primitives:
  ["PCB_TRACK", "PCB_ARC"]`, `exclude_nets` and `min_track_width_mm` to check
  straight segments and native arcs. Empty or unsupported eligible geometry is
  incomplete. Vias, pads and zones are outside this width criterion.
- `rc_transient`: generates a SPICE deck from the XML netlist produced by a
  prior `netlist` check (values, connectivity, DNP state and symbol classes
  come from KiCad, never from fixture knowledge) and runs ngspice. DNP
  components are excluded from the deck and recorded; a component is modeled
  only when its native libsource is a known passive symbol class matching its
  reference prefix. Parameters: `netlist_check` (str, required) — id of the
  netlist check; `input_net`, `output_net`, `ground_net` (str, required) —
  net names as exported; `step_v` (finite float > 0, default 1.0) — ideal
  step amplitude; `stop_ms` (finite float > 0, default 5.0); `measurements`
  — list of `{name, at_ms, min?, max?}` with unique names, finite `at_ms`
  within (0, stop_ms], finite limits and min <= max; entries with limits are
  fixed acceptance criteria, entries without are informational.

Result shape (contract `docs/contracts.md`): `{id, kind, candidate_id,
execution, outcome, summary, evidence, details, limitations}` with execution
`ok|failed|unavailable|timeout|malformed|not_run|interrupted|unsupported` and
outcome `pass|defect|unknown|info`. A native tool that ran and reported
design violations is execution `ok`, outcome `defect`; incomplete coverage or
unreadable output is never a clean result.
"""

import json
import math
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path

from . import executor
from .storage import read_json, write_json

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SHEETFILE_RE = re.compile(r'\(property\s+"Sheet ?file"\s+"([^"]+)"')
# kicad-cli pcb drc exits 0 and reports an empty parity list when it could not
# load the schematic; its console message is the only signal (eda.py finding).
PARITY_SKIP_RE = re.compile(r"(?i)failed to fetch schematic netlist[^\n]*|parity tests require[^\n]*")
SPICE_SCALAR = re.compile(r"^(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?(?:meg|[tgkmunpf])?[a-z]*$", re.I)
SPICE_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
                "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
# Native symbol classes this deck generator can honestly model. The reference
# prefix alone proves nothing: R36 is a resistor because its libsource says so.
PASSIVE_CLASSES = {"R": "R", "C": "C", "CP": "C", "L": "L"}
NGSPICE_FAIL_PATTERNS = [
    r"(?i)timestep too small", r"(?i)simulation\(s\) aborted", r"(?i)singular matrix",
    r"(?i)no convergence", r"(?i)iteration limit reached",
    r"(?i)^\s*error\b", r"(?i)fatal", r"(?i)\bfailed!",
]
MEAS_OUT = re.compile(r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*(\S+)")
MIN_CHECK_SECONDS = 5.0


class CheckError(Exception):
    """Terminates a check with an explicit execution status."""

    def __init__(self, execution, message):
        super().__init__(message)
        self.execution = execution


class Ctx:
    """One check's run context: sandbox invocation, evidence, result record."""

    def __init__(self, run_dir, manifest, spec, deadline):
        self.run_dir = Path(run_dir)
        self.manifest = manifest
        self.scope = manifest["scope"]
        self.spec = spec
        self.cid = spec["id"]
        self.params = spec.get("params", {})
        self.deadline = deadline
        self.inputs = self.run_dir / "inputs"
        self.work = self.run_dir / "work" / self.cid
        self.work.mkdir(parents=True, exist_ok=True)
        self.result = {
            "id": self.cid, "kind": spec.get("kind"),
            "candidate_id": manifest["candidate_id"],
            "execution": "not_run", "outcome": "unknown", "summary": "",
            "evidence": [], "details": {}, "limitations": [],
        }

    def remaining(self):
        return self.deadline - time.monotonic()

    def evidence(self, *names):
        for name in names:
            rel = f"work/{self.cid}/{name}"
            if (self.run_dir / rel).is_file() and rel not in self.result["evidence"]:
                self.result["evidence"].append(rel)

    def execute(self, argv, timeout, *, extra_ro=()):
        remaining = self.remaining()
        if remaining <= 1:
            raise CheckError("interrupted", "deadline exhausted before the tool could run")
        clamped = min(timeout, remaining)
        baseline = self.run_dir / "baseline.json"
        mounts = [(baseline, "/evidence/baseline.json")] if baseline.is_file() else []
        rec = executor.execute(self.inputs, self.work, argv, timeout=clamped, extra_ro=[*mounts, *extra_ro])
        self.evidence("execution.json", "console.log")
        self.result["details"]["tool"] = {
            "argv": rec["argv"], "exit_code": rec["exit_code"],
            "duration_s": rec["duration_s"], "console_sha256": rec["console_sha256"],
        }
        if rec["execution"] == "timeout" and clamped < timeout:
            raise CheckError("interrupted", f"run deadline cut the tool off after {clamped:.0f}s")
        if rec["execution"] != "ok":
            raise CheckError(rec["execution"],
                             f"{argv[0]} {rec['execution']} (exit {rec['exit_code']})")
        return rec

    def console(self):
        log = self.work / "console.log"
        return log.read_text(errors="replace") if log.exists() else ""

    def load_json(self, name, what):
        try:
            return json.loads((self.work / name).read_text())
        except FileNotFoundError:
            raise CheckError("malformed", f"{what}: {name} was not written")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CheckError("malformed", f"{what}: {name} is not valid JSON ({exc})")

    def candidate_path(self, rel):
        """Sandbox-side path for a candidate-relative file, verified to exist."""
        if not (self.inputs / rel).is_file():
            raise CheckError("malformed", f"candidate is missing {rel!r}")
        return f"/candidate/{rel}"

    def seed_kicad_config(self):
        """Stock global library tables for kicad-cli's hermetic config.

        The executor points KICAD_CONFIG_HOME at /work/kicad-config; without
        global tables KiCad reports a missing-table error on every run.
        """
        cfg = self.work / "kicad-config" / "10.0"
        cfg.mkdir(parents=True, exist_ok=True)
        missing = []
        for table in ("sym-lib-table", "fp-lib-table"):
            src = self.inputs / "stock" / "template" / table
            if src.is_file():
                shutil.copyfile(src, cfg / table)
            else:
                missing.append(table)
        if missing:
            self.result["limitations"].append(
                "stock template tables not captured: " + ", ".join(missing))


def count_by(items, key):
    out = {}
    for item in items:
        out[item.get(key, "?")] = out.get(item.get(key, "?"), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def sheet_instances(inputs, root_rel):
    """Sheet files reachable from the root schematic and the instance count.

    Child sheet files resolve relative to their containing sheet (not the
    root). Raises ValueError on a hierarchy cycle or on a reference escaping
    the captured inputs. Returns (files, instances, missing): files and
    missing are inputs-relative/verbatim reference paths.
    """
    inputs = Path(inputs).resolve()
    root = Path(os.path.normpath(inputs / root_rel))
    if not root.is_relative_to(inputs):
        raise ValueError(f"root schematic {root_rel!r} escapes the captured inputs")
    files, missing, children_of = [], [], {}
    state = {"instances": 1}

    def walk(path, stack):
        rel = path.relative_to(inputs).as_posix()
        if rel in stack:
            raise ValueError(f"sheet hierarchy cycle through {rel!r}")
        if rel not in children_of:
            files.append(rel)
            refs = SHEETFILE_RE.findall(path.read_text(errors="replace"))
            children_of[rel] = [(ref, Path(os.path.normpath(path.parent / ref)))
                                for ref in refs]
        for ref, resolved in children_of[rel]:
            state["instances"] += 1
            if not resolved.is_relative_to(inputs):
                raise ValueError(
                    f"sheet {rel!r} references {ref!r}, which escapes the captured inputs")
            if resolved.is_file():
                walk(resolved, stack | {rel})
            elif ref not in missing:
                missing.append(ref)

    walk(root, frozenset())
    return files, state["instances"], missing


def verified_hierarchy(ctx):
    """Sheet hierarchy of the scope schematic; incomplete coverage is fatal."""
    try:
        files, expected, missing = sheet_instances(ctx.inputs, ctx.scope["schematic"])
    except (ValueError, OSError) as exc:
        raise CheckError("malformed", f"sheet hierarchy unusable: {exc}")
    if missing:
        ctx.result["details"]["missing_sheet_files"] = missing
        raise CheckError("malformed",
                         f"hierarchy references missing sheet files: {missing}; coverage is incomplete")
    return files, expected


# ---------------------------------------------------------------------- checks

def check_erc(ctx):
    sch = ctx.candidate_path(ctx.scope["schematic"])
    files, expected = verified_hierarchy(ctx)
    ctx.seed_kicad_config()
    ctx.execute(["kicad-cli", "sch", "erc", "--format", "json", "--severity-all",
                 "--units", "mm", "-o", "/work/erc.json", sch], timeout=900)
    ctx.evidence("erc.json")
    report = ctx.load_json("erc.json", "ERC")
    ctx.result["details"]["kicad_version"] = report.get("kicad_version")
    sheets = report.get("sheets")
    if not isinstance(sheets, list):
        raise CheckError("malformed", "ERC report has no sheets list")
    if len(sheets) != expected:
        raise CheckError("malformed",
                         f"ERC covered {len(sheets)} sheet instances, hierarchy has {expected}")
    violations = [v for s in sheets for v in s.get("violations", [])]
    ctx.result["details"].update({
        "sheets_checked": len(sheets), "schematic_files": files,
        "violations": len(violations),
        "by_severity": count_by(violations, "severity"),
        "by_type": count_by(violations, "type"),
        "ignored_checks": [c.get("key") for c in report.get("ignored_checks", [])],
    })
    ctx.result["execution"] = "ok"
    if violations:
        ctx.result["outcome"] = "defect"
        ctx.result["summary"] = (f"ERC ran on {len(sheets)} sheet instance(s) and reported "
                                 f"{len(violations)} violation(s): {ctx.result['details']['by_severity']}")
    else:
        ctx.result["outcome"] = "pass"
        ctx.result["summary"] = f"ERC ran on {len(sheets)} sheet instance(s) with zero violations"


# One native DRC execution; a projection only changes which raw categories are
# graded. Unrouted connectivity is expected while placing, never globally.
DRC_PROJECTIONS = {"placement-stage/1": ("violations", "schematic_parity")}


def drc_projection(params):
    projection = params.get("projection")
    if projection is not None and projection not in DRC_PROJECTIONS:
        raise ValueError(f"unknown DRC projection {projection!r}")
    return projection


def require_later_full_drc(specs, spec):
    later = specs[specs.index(spec) + 1:]
    if not any(s.get("kind") == "drc" and "projection" not in s.get("params", {}) for s in later):
        raise ValueError("a DRC projection requires a later full drc check")


def check_drc(ctx):
    try:
        projection = drc_projection(ctx.params)
        if projection:
            require_later_full_drc(ctx.scope.get("checks", []), ctx.spec)
    except ValueError as exc:
        raise CheckError("unsupported", str(exc)) from exc
    board = ctx.candidate_path(ctx.scope["board"])
    ctx.candidate_path(ctx.scope["schematic"])  # parity needs it alongside
    ctx.seed_kicad_config()
    ctx.execute(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all",
                 "--schematic-parity", "--units", "mm", "-o", "/work/drc.json", board],
                timeout=900)
    ctx.evidence("drc.json")
    report = ctx.load_json("drc.json", "DRC")
    ctx.result["details"]["kicad_version"] = report.get("kicad_version")
    ctx.result["details"]["board_sha256"] = ctx.manifest["files"].get(ctx.scope["board"])
    ctx.result["limitations"].append(
        "zone fill evaluated as saved; the read-only candidate forbids a refill pass")
    for key in ("violations", "unconnected_items", "schematic_parity"):
        if not isinstance(report.get(key), list):
            raise CheckError("malformed", f"DRC report is missing {key}")
    skip = PARITY_SKIP_RE.search(ctx.console())
    if skip:
        raise CheckError("malformed",
                         f"schematic parity was not performed: {skip.group(0).strip()}")
    v, u, p = report["violations"], report["unconnected_items"], report["schematic_parity"]
    ctx.result["details"].update({
        "violations": len(v), "unconnected_items": len(u), "schematic_parity": len(p),
        "by_severity": count_by(v + u + p, "severity"),
        "by_type": count_by(v + u + p, "type"),
        "ignored_checks": [c.get("key") for c in report.get("ignored_checks", [])],
    })
    ctx.result["execution"] = "ok"
    if projection:
        ctx.result["details"].update(projection=projection, graded=list(DRC_PROJECTIONS[projection]),
                                     ungraded={"unconnected_items": len(u)})
        ctx.result["limitations"].append(
            f"{projection}: unconnected items are retained in drc.json but not graded; "
            "a later full DRC with connectivity remains required")
        failing = v or p
    else:
        failing = v or u or p
    if failing:
        ctx.result["outcome"] = "defect"
        ctx.result["summary"] = (f"DRC with schematic parity reported {len(v)} violation(s), "
                                 f"{len(u)} unconnected item(s), {len(p)} parity issue(s)"
                                 + (f"; {projection} grades violations and parity only" if projection else ""))
    elif projection:
        ctx.result["outcome"] = "pass"
        ctx.result["summary"] = (f"{projection} DRC with schematic parity reported zero violations and "
                                 f"parity issues; {len(u)} unconnected item(s) retained ungraded")
    else:
        ctx.result["outcome"] = "pass"
        ctx.result["summary"] = "DRC with schematic parity reported zero issues on the saved board"


def parse_xml_netlist(path):
    """Components (with DNP, libsource and sheet/UUID provenance) and nets."""
    root = ET.parse(path).getroot()
    components, nets = {}, {}
    for comp in root.iter("comp"):
        ref = comp.get("ref")
        libsource = comp.find("libsource")
        sheetpath = comp.find("sheetpath")
        components[ref] = {
            "value": (comp.findtext("value") or "").strip(),
            "footprint": (comp.findtext("footprint") or "").strip(),
            "dnp": any(p.get("name") == "dnp" for p in comp.findall("property")),
            "libsource": ({"lib": libsource.get("lib", ""), "part": libsource.get("part", "")}
                          if libsource is not None else None),
            "sheet": sheetpath.get("names") if sheetpath is not None else None,
            "sheet_uuid": sheetpath.get("tstamps") if sheetpath is not None else None,
            "uuid": (comp.findtext("tstamps") or "").strip(),
        }
    for net in root.iter("net"):
        nodes = [f"{n.get('ref')}.{n.get('pin')}" for n in net.findall("node")]
        nets[net.get("name")] = sorted(nodes)
    return {"components": components, "nets": nets}


def check_netlist(ctx):
    sch = ctx.candidate_path(ctx.scope["schematic"])
    files, expected = verified_hierarchy(ctx)
    ctx.seed_kicad_config()
    ctx.execute(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                 "-o", "/work/design.xml", sch], timeout=600)
    ctx.evidence("design.xml")
    out = ctx.work / "design.xml"
    if not out.is_file() or out.stat().st_size == 0:
        raise CheckError("malformed", "XML netlist was not written")
    try:
        parsed = parse_xml_netlist(out)
    except ET.ParseError as exc:
        raise CheckError("malformed", f"XML netlist does not parse: {exc}")
    if not parsed["components"] or not parsed["nets"]:
        raise CheckError("malformed", "XML netlist has no components or no nets")
    write_json(ctx.work / "connectivity.json", parsed)
    ctx.evidence("connectivity.json")
    dnp_refs = sorted(r for r, m in parsed["components"].items() if m["dnp"])
    ctx.result["details"] = {"components": len(parsed["components"]),
                             "nets": len(parsed["nets"]),
                             "refs": sorted(parsed["components"]),
                             "dnp_refs": dnp_refs,
                             "schematic_files": files,
                             "sheet_instances": expected}
    ctx.result["execution"] = "ok"
    ctx.result["outcome"] = "info"
    ctx.result["summary"] = (f"native XML netlist exported from a complete {expected}-instance "
                             f"hierarchy: {len(parsed['components'])} component(s), "
                             f"{len(parsed['nets'])} net(s), {len(dnp_refs)} DNP")


def copper_width_coverage(geo, params):
    """Evaluate only explicit track/arc widths; unknown track items block coverage."""
    if (params.get("criterion") != "copper-width/1" or
            not isinstance(params.get("covered_primitives"), list) or
            any(not isinstance(c, str) for c in params["covered_primitives"]) or
            sorted(params["covered_primitives"]) != ["PCB_ARC", "PCB_TRACK"]):
        raise CheckError("unsupported", "copper-width/1 requires covered_primitives PCB_TRACK and PCB_ARC")
    limit = params.get("min_track_width_mm")
    excluded = params.get("exclude_nets")
    if (isinstance(limit, bool) or not isinstance(limit, (int, float)) or
            not math.isfinite(limit) or limit <= 0 or
            not isinstance(excluded, list) or any(not isinstance(n, str) for n in excluded)):
        raise CheckError("unsupported", "copper-width/1 needs a finite positive minimum and explicit exclude_nets")
    tracks = geo.get("tracks")
    if not isinstance(tracks, list):
        raise CheckError("malformed", "geometry tracks must be a list")
    counts = dict(eligible=0, checked=0, excluded=0, unsupported=0)
    records, offenders = [], []
    for item in tracks:
        primitive = item if isinstance(item, dict) else {}
        cls = primitive.get("native_class")
        if cls == "PCB_VIA":
            continue
        record = {key: primitive.get(key) for key in ("uuid", "net", "layer")}
        width = primitive.get("width_nm")
        valid_width = isinstance(width, int) and not isinstance(width, bool) and width > 0
        record.update(type=cls, width_mm=width / 1_000_000 if valid_width else None,
                      limit_mm=limit)
        records.append(record)
        identity_ok = all(isinstance(record[k], str) and record[k]
                          for k in ("uuid", "layer")) and isinstance(record["net"], str)
        if identity_ok and record["net"] in excluded:
            counts["excluded"] += 1
            record["status"] = "excluded"
            continue
        counts["eligible"] += 1
        if cls not in ("PCB_TRACK", "PCB_ARC") or not identity_ok or not valid_width:
            counts["unsupported"] += 1
            record["status"] = "unsupported"
            continue
        record["status"] = "checked"
        counts["checked"] += 1
        if record["width_mm"] < limit:
            offenders.append(record)
    return {"criterion": "copper-width/1", "covered_primitives": ["PCB_TRACK", "PCB_ARC"],
            "exclude_nets": sorted(set(excluded)), "counts": counts, "primitives": records,
            "offending_primitives": offenders,
            "out_of_scope": {"PCB_VIA": sum(isinstance(t, dict) and t.get("native_class") == "PCB_VIA"
                                            for t in tracks), "zones": len(geo["zones"])}}


def check_geometry(ctx):
    board = ctx.candidate_path(ctx.scope["board"])
    ctx.execute(["/usr/bin/python3", "-B", "-m", "agent_ee.geometry_probe",
                 board, "-o", "/work/geometry.json"], timeout=600)
    ctx.evidence("geometry.json")
    geo = ctx.load_json("geometry.json", "geometry probe")
    for key in ("pcbnew_version", "tracks", "pads", "zones", "nets", "footprints"):
        if key not in geo:
            raise CheckError("malformed", f"geometry report is missing {key}")
    ctx.result["limitations"].append(
        "geometry read from the saved board file; zone fill state as saved")
    if geo.get("unconnected_items") is None:
        ctx.result["details"]["unconnected_items_error"] = geo.get("unconnected_items_error")
        ctx.result["limitations"].append(
            "connectivity unconnected count unavailable from pcbnew: "
            + str(geo.get("unconnected_items_error")))
    if "criterion" in ctx.params or "covered_primitives" in ctx.params:
        coverage = copper_width_coverage(geo, ctx.params)
        coverage.update(candidate_id=ctx.manifest["candidate_id"],
                        board_sha256=ctx.manifest["files"].get(ctx.scope["board"]),
                        pcbnew_version=geo["pcbnew_version"])
        write_json(ctx.work / "copper-width.json", coverage)
        ctx.evidence("copper-width.json")
        ctx.result["details"].update(coverage)
        counts = coverage["counts"]
        if not counts["eligible"] or counts["unsupported"]:
            raise CheckError("unsupported", "copper-width/1 coverage incomplete: empty eligible set or unsupported primitive")
        ctx.result["execution"] = "ok"
        ctx.result["outcome"] = "defect" if coverage["offending_primitives"] else "pass"
        ctx.result["summary"] = (f"copper-width/1: {counts['checked']} track/arc primitive(s) checked, "
                                 f"{len(coverage['offending_primitives'])} below {ctx.params['min_track_width_mm']} mm")
        return
    ctx.result["details"].update({
        "pcbnew_version": geo["pcbnew_version"],
        "board_sha256": ctx.manifest["files"].get(ctx.scope["board"]),
        "nets": len(geo["nets"]), "pads": len(geo["pads"]),
        "dnp_refs": sorted(fp["ref"] for fp in geo["footprints"] if fp.get("dnp")),
        "track_segments": sum(1 for t in geo["tracks"] if t["type"] == "track"),
        "vias": sum(1 for t in geo["tracks"] if t["type"] == "via"),
        "zones": len(geo["zones"]),
        "unconnected_items": geo.get("unconnected_items"),
    })
    limit = ctx.params.get("min_track_width_mm")
    ctx.result["execution"] = "ok"
    if limit is None:
        ctx.result["outcome"] = "info"
        ctx.result["summary"] = ("saved-board geometry extracted "
                                 "(no declared geometric requirement applied)")
        return
    exclude = set(ctx.params.get("exclude_nets", []))
    segments = [t for t in geo["tracks"] if t["type"] == "track" and t["net"] not in exclude]
    if not segments:
        raise CheckError("malformed", "board has no track segments to check against the declared minimum width")
    offenders = [t for t in segments if t["width_mm"] < float(limit) - 1e-9]
    widths = sorted({t["width_mm"] for t in segments})
    ctx.result["details"].update({
        "min_track_width_mm_required": float(limit),
        "exclude_nets": sorted(exclude),
        "segment_widths_mm": widths,
        "narrowest_mm": widths[0],
        "offending_segments": offenders[:50],
    })
    if offenders:
        ctx.result["outcome"] = "defect"
        ctx.result["summary"] = (f"{len(offenders)} of {len(segments)} track segment(s) are "
                                 f"narrower than the declared {limit} mm minimum "
                                 f"(narrowest {widths[0]} mm)")
    else:
        ctx.result["outcome"] = "pass"
        ctx.result["summary"] = (f"all {len(segments)} checked track segment(s) meet the declared "
                                 f"{limit} mm minimum width (narrowest {widths[0]} mm)")


# ------------------------------------------------------------------- simulation

def spice_value(token):
    m = re.match(r"^(\d+\.?\d*|\.\d+)(?:e(-?\d+))?\s*([a-z]*)$", token.strip(), re.I)
    if not m:
        return None
    base = float(m.group(1)) * (10.0 ** int(m.group(2)) if m.group(2) else 1.0)
    suffix = m.group(3).lower()
    if suffix.startswith("meg"):
        return base * 1e6
    if suffix and suffix[0] in SPICE_SUFFIX:
        return base * SPICE_SUFFIX[suffix[0]]
    return base


def sanitize_node(net):
    node = re.sub(r"[^A-Za-z0-9_]", "_", net).strip("_")
    if not node:
        raise CheckError("unsupported", f"net name {net!r} cannot map to a SPICE node")
    return node


def element_class(meta):
    """Native symbol class of a component, or None when it is not a known passive."""
    libsource = meta.get("libsource") or {}
    part = (libsource.get("part") or "").strip()
    if not part:
        return None
    return PASSIVE_CLASSES.get(part.split("_")[0].upper())


def is_connector(meta):
    libsource = meta.get("libsource") or {}
    return (libsource.get("lib", "").startswith("Connector")
            or libsource.get("part", "").startswith("Conn"))


def validate_sim_params(params):
    """Finite positive stimulus/stop, unique measurement names, sane limits."""
    for key in ("input_net", "output_net", "ground_net"):
        if not params.get(key):
            raise CheckError("unsupported", f"rc_transient requires parameter {key!r}")
    try:
        step_v = float(params.get("step_v", 1.0))
        stop_ms = float(params.get("stop_ms", 5.0))
    except (TypeError, ValueError) as exc:
        raise CheckError("unsupported", f"step_v/stop_ms are not numbers: {exc}")
    if not (math.isfinite(step_v) and step_v > 0):
        raise CheckError("unsupported", f"step_v must be finite and positive, got {step_v!r}")
    if not (math.isfinite(stop_ms) and stop_ms > 0):
        raise CheckError("unsupported", f"stop_ms must be finite and positive, got {stop_ms!r}")
    wanted, names = [], set()
    for meas in params.get("measurements", []):
        name = str(meas.get("name", "")).lower()
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", name):
            raise CheckError("unsupported", f"measurement name {meas.get('name')!r} is not usable")
        if name in names:
            raise CheckError("unsupported", f"duplicate measurement name {name!r}")
        names.add(name)
        try:
            at_ms = float(meas["at_ms"])
        except (KeyError, TypeError, ValueError):
            raise CheckError("unsupported", f"measurement {name!r} needs a numeric at_ms")
        if not (math.isfinite(at_ms) and 0 < at_ms <= stop_ms):
            raise CheckError("unsupported",
                             f"measurement {name!r} at_ms={at_ms!r} is outside the simulated "
                             f"interval (0, {stop_ms}] ms")
        lo, hi = meas.get("min"), meas.get("max")
        for label, bound in (("min", lo), ("max", hi)):
            if bound is not None and not (isinstance(bound, (int, float))
                                          and math.isfinite(float(bound))):
                raise CheckError("unsupported", f"measurement {name!r} {label}={bound!r} is not finite")
        if lo is not None and hi is not None and float(lo) > float(hi):
            raise CheckError("unsupported", f"measurement {name!r} has min {lo} > max {hi}")
        wanted.append({"name": name, "at_ms": at_ms, "min": lo, "max": hi})
    return {**params, "step_v": step_v, "stop_ms": stop_ms, "measurements": wanted,
            "condition_step_v": str(params.get("step_v", 1.0))}


def build_deck(conn, params):
    """SPICE deck from the native XML netlist.

    Only components whose native libsource is a known passive class matching
    their reference prefix are modeled. DNP components are excluded and
    returned separately; connectors and power flags carry no two-terminal
    element and are ignored. Anything else is honestly unsupported.
    """
    return _build_deck(conn, validate_sim_params(params))


def _build_deck(conn, params):
    """Build from validated parameters, shared with measured-result generation."""
    step_v, stop_ms, wanted = params["step_v"], params["stop_ms"], params["measurements"]
    nets = conn["nets"]
    for key in ("input_net", "output_net", "ground_net"):
        if params[key] not in nets:
            raise CheckError("malformed",
                             f"{key} {params[key]!r} is not in the exported netlist "
                             f"(nets: {sorted(nets)})")
    ground = params["ground_net"]
    nodes = {}
    for net in nets:
        nodes[net] = "0" if net == ground else sanitize_node(net)
    if len(set(nodes.values())) != len(nodes):
        raise CheckError("unsupported", "net names collide after SPICE node sanitisation")

    pins = {}
    for net, node_list in nets.items():
        for entry in node_list:
            ref, _, pin = entry.rpartition(".")
            pins.setdefault(ref, {})[pin] = net

    lines, elements, ignored, excluded_dnp = [], {}, [], []
    for ref, meta in sorted(conn["components"].items()):
        if ref.startswith("#"):
            ignored.append(ref)  # power flags are virtual
            continue
        if meta.get("dnp"):
            excluded_dnp.append(ref)  # not populated on the real board
            continue
        if is_connector(meta):
            ignored.append(ref)  # connectors carry no 2-terminal element
            continue
        cls = element_class(meta)
        libsource = meta.get("libsource") or {}
        if cls is None:
            raise CheckError("unsupported",
                             f"component {ref} (libsource {libsource.get('lib')!r}:"
                             f"{libsource.get('part')!r}) is not a supported passive symbol "
                             "class; this deck generator cannot honestly simulate it")
        if not ref[:1].upper() == cls:
            raise CheckError("unsupported",
                             f"component {ref} has libsource class {cls} but a mismatched "
                             "reference prefix; refusing to guess its element type")
        comp_pins = pins.get(ref, {})
        if len(comp_pins) != 2:
            raise CheckError("malformed",
                             f"{ref} has {len(comp_pins)} connected pin(s) in the netlist, expected 2")
        value = meta.get("value", "")
        if not SPICE_SCALAR.match(value):
            raise CheckError("unsupported",
                             f"{ref} value {value!r} is not a plain SPICE scalar")
        a, b = (nodes[comp_pins[p]] for p in sorted(comp_pins))
        lines.append(f"{ref} {a} {b} {value}")
        elements[ref] = {"value": value, "parsed": spice_value(value),
                         "class": cls, "nets": sorted(set(comp_pins.values())),
                         "sheet": meta.get("sheet"), "uuid": meta.get("uuid")}

    if not elements:
        raise CheckError("unsupported", "netlist contains no populated R/C/L elements to simulate")
    lines.append(f"VSTEP {nodes[params['input_net']]} 0 PWL(0 0 1n {step_v:g})")
    lines.append(f".tran {stop_ms / 1000.0:g}m {stop_ms:g}m")
    out_node = nodes[params["output_net"]]
    for meas in wanted:
        lines.append(f".meas tran {meas['name']} FIND v({out_node}) AT={meas['at_ms']:g}m")
    deck = ("* agent-ee rc_transient deck, generated from the native XML netlist\n"
            + "\n".join(lines) + "\n.end\n")
    return deck, elements, ignored, excluded_dnp, nodes, wanted


def read_raw(path):
    """Parse an ngspice binary raw file without numpy (adapted from eda.py)."""
    data = Path(path).read_bytes()
    marker = b"Binary:\n"
    idx = data.find(marker)
    if idx < 0:
        raise ValueError("not a binary raw file")
    header = data[:idx].decode("latin-1")
    nvars = int(re.search(r"No\. Variables:\s*(\d+)", header).group(1))
    npts = int(re.search(r"No\. Points:\s*(\d+)", header).group(1))
    is_complex = "complex" in re.search(r"Flags:\s*(.*)", header).group(1)
    names = re.findall(r"^[ \t]+\d+[ \t]+(\S+)[ \t]+\S+",
                       header.split("\nVariables:", 1)[1], re.M)[:nvars]
    if len(names) != nvars:
        raise ValueError(f"raw header lists {len(names)} variables, expected {nvars}")
    values = array("d")
    tail = data[idx + len(marker):]
    if len(tail) % 8:
        raise ValueError(f"raw data length {len(tail)} is not a whole number of float64 values")
    values.frombytes(tail)
    expected = nvars * npts * (2 if is_complex else 1)
    if len(values) != expected:
        raise ValueError(f"raw data has {len(values)} values, header promises {expected}")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("raw file contains non-finite values")
    return names, npts


def analyze_sim(work, wanted):
    """Validate the ngspice log, .meas values and raw trace of a finished run.

    Returns (measurement checks, limit failures, raw summary); raises
    CheckError on solver problems, missing/non-finite measurements or an
    unreadable/invalid trace. Separated from check_rc_transient so failure
    modes are directly testable.
    """
    work = Path(work)
    log_path = work / "ngspice.log"
    if not log_path.is_file():
        raise CheckError("malformed", "ngspice wrote no log")
    log_text = log_path.read_text(errors="replace")
    for pattern in NGSPICE_FAIL_PATTERNS:
        m = re.search(pattern, log_text, re.M)
        if m:
            raise CheckError("failed", f"ngspice log reports a problem: {m.group(0).strip()!r}")
    section = log_text.lower().split("measurements for", 1)
    if wanted and len(section) < 2:
        raise CheckError("malformed", "ngspice ran no measurements")
    measured = {}
    for name, value in MEAS_OUT.findall(section[-1] if wanted else ""):
        try:
            measured[name.lower()] = float(value)
        except ValueError:
            measured[name.lower()] = value

    try:
        var_names, points = read_raw(work / "result.raw")
    except (OSError, ValueError) as exc:
        raise CheckError("malformed", f"raw trace unreadable or invalid: {exc}")
    if points < 2:
        raise CheckError("malformed", "raw trace has fewer than 2 points")

    checks, failures = {}, []
    for meas in wanted:
        got = measured.get(meas["name"])
        if not isinstance(got, float) or not math.isfinite(got):
            raise CheckError("malformed",
                             f"measurement {meas['name']} missing or not finite: {got!r}")
        entry = {"at_ms": meas["at_ms"], "measured": got,
                 "min": meas["min"], "max": meas["max"]}
        if meas["min"] is not None or meas["max"] is not None:
            ok = ((meas["min"] is None or got >= float(meas["min"])) and
                  (meas["max"] is None or got <= float(meas["max"])))
            entry["within_limits"] = ok
            if not ok:
                failures.append(f"{meas['name']}={got:.6g} outside "
                                f"[{meas['min']}, {meas['max']}]")
        checks[meas["name"]] = entry
    return checks, failures, {"variables": var_names, "points": points}


def check_rc_transient(ctx):
    nid = ctx.params.get("netlist_check")
    if not nid:
        raise CheckError("unsupported", "rc_transient requires parameter 'netlist_check'")
    prior = ctx.run_dir / "checks" / f"{nid}.json"
    if not prior.is_file():
        raise CheckError("not_run", f"prerequisite netlist check {nid!r} has no result yet")
    prior_result = read_json(prior)
    if prior_result.get("execution") != "ok":
        raise CheckError("not_run",
                         f"prerequisite netlist check {nid!r} was {prior_result.get('execution')}")
    conn_path = ctx.run_dir / "work" / nid / "connectivity.json"
    if not conn_path.is_file():
        raise CheckError("not_run", f"prerequisite netlist check {nid!r} left no connectivity.json")
    conn = read_json(conn_path)
    ctx.result["evidence"].append(f"work/{nid}/design.xml")
    ctx.result["evidence"].append(f"work/{nid}/connectivity.json")

    params = validate_sim_params(ctx.params)
    deck, elements, ignored, excluded_dnp, nodes, wanted = _build_deck(conn, params)
    (ctx.work / "deck.cir").write_text(deck)
    (ctx.work / "control.sp").write_text(
        "* agent-ee generated ngspice control script\n"
        ".control\n"
        "set num_threads=1\n"
        "set noaskquit\n"
        "source /work/deck.cir\n"
        "run\n"
        "write /work/result.raw\n"
        "quit\n"
        ".endc\n"
        ".end\n")
    ctx.result["details"].update({
        "elements": elements, "ignored_components": sorted(ignored),
        "excluded_dnp": sorted(excluded_dnp),
        "node_map": nodes, "step_v": params["step_v"],
        "method": "ngspice -n -b -o <log> <control.sp>; -b -r is not used because it "
                  "silently skips .meas",
    })
    ctx.result["limitations"].append(
        "ideal unloaded step response from populated R/C/L values only; tolerance, "
        "temperature, loading and hardware behavior are untested")
    if ignored:
        ctx.result["limitations"].append(
            "connectors/power flags carried no element and were excluded: " + ", ".join(sorted(ignored)))
    if excluded_dnp:
        ctx.result["limitations"].append(
            "DNP components excluded from the deck (not populated): " + ", ".join(sorted(excluded_dnp)))

    ctx.execute(["ngspice", "-n", "-b", "-o", "/work/ngspice.log", "/work/control.sp"],
                timeout=600)
    ctx.evidence("deck.cir", "control.sp", "ngspice.log", "result.raw")
    checks, failures, raw = analyze_sim(ctx.work, wanted)
    ctx.result["details"]["raw"] = raw
    ctx.result["details"]["measurements"] = checks
    metrics = {name: {"value": m["measured"], "unit": "V",
        "condition": f"ideal-unloaded-{params['condition_step_v']}V-step-at-{m['at_ms']}ms"}
        for name, m in checks.items()}
    ctx.result["details"].update(metrics=metrics, producer={"tool": "ngspice", "domain": "electrical"})
    ctx.result["execution"] = "ok"
    if failures:
        ctx.result["outcome"] = "defect"
        ctx.result["summary"] = ("transient response violates declared limits: "
                                 + "; ".join(failures))
    elif any(m["min"] is not None or m["max"] is not None for m in wanted):
        ctx.result["outcome"] = "pass"
        ctx.result["summary"] = "transient response meets all declared measurement limits"
    else:
        ctx.result["outcome"] = "info"
        ctx.result["summary"] = "transient simulation completed; no limits were declared"

    if ctx.scope.get("schema") in ("agent-ee.scope/2", "agent-ee.scope/3"):
        from .measurements import finish
        limits = {name: {"unit": "V", "condition": metrics[name]["condition"],
            **{key: m[key] for key in ("min", "max") if m[key] is not None}}
            for name, m in checks.items() if m["min"] is not None or m["max"] is not None}
        if limits:
            try:
                finish(ctx, metrics, limits)
            except (ValueError, KeyError, TypeError) as exc:
                raise CheckError("malformed", str(exc)) from exc


from .measurements import check_measurements, check_coupled_margins
from .datasheet_facts import check_datasheet_facts, check_pin_map
from .active_spice import check_active_spice
from .kicad_happy import check_kicad_happy
from .hardware_interface import check_hardware_interface
from .passive_corners import check_passive_corners
from .netlist_parity import check_netlist_parity
from .routing_rules import check_routing_rules
from .fab_profile import check_fab_profile
from .library_checks import check_footprint_assignment
from .symbol_checks import check_symbol_pins
from .dc_path import check_dc_path
from .netclass_completeness import check_netclass_completeness
from .netlist_extra import check_netlist_extra
from .placement_check import check_placement
from .assembly_readiness import check_assembly_readiness


from .catalog_checks import check_catalog
from .land_pattern import check_land_pattern

from .part_binding import check_part_binding

from .package_consistency import check_package_consistency

from .fab_export import check_fab_export

from .fab_consistency import check_fab_consistency
from .fab_roundtrip import check_fab_roundtrip

CHECK_FUNCS = {'fab_roundtrip': check_fab_roundtrip, 'fab_consistency': check_fab_consistency, 'fab_export': check_fab_export, 'package_consistency': check_package_consistency, 'part_binding': check_part_binding, "catalog": check_catalog, "land_pattern": check_land_pattern, "part_selection": check_catalog, "netlist_extra": check_netlist_extra, "netlist_parity": check_netlist_parity, "routing_rules": check_routing_rules, "fab_profile": check_fab_profile, "symbol_pins": check_symbol_pins, "footprint_assignment": check_footprint_assignment, "netclass_completeness": check_netclass_completeness, "kicad_happy": check_kicad_happy, "hardware_interface": check_hardware_interface,
               "datasheet_facts": check_datasheet_facts, "pin_map": check_pin_map,
               "active_spice": check_active_spice, "measurements": check_measurements,
               "coupled_margins": check_coupled_margins, "erc": check_erc,
               "drc": check_drc, "netlist": check_netlist,

               "geometry": check_geometry, "rc_transient": check_rc_transient,
               "passive_corners": check_passive_corners, "dc_path": check_dc_path,
               "placement": check_placement, "assembly_readiness": check_assembly_readiness}


# ------------------------------------------------------------------ entry point

def run_checks(run_dir, deadline):
    """Run every scope check; write checks/<id>.json immediately; return results."""
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "candidate.json")
    scope = manifest["scope"]
    (run_dir / "checks").mkdir(exist_ok=True)
    results, seen = [], set()
    for index, spec in enumerate(scope.get("checks", [])):
        cid = spec.get("id")
        if not isinstance(cid, str) or not SAFE_ID.match(cid) or cid in seen:
            record = {"id": cid, "kind": spec.get("kind"),
                      "candidate_id": manifest["candidate_id"],
                      "execution": "malformed", "outcome": "unknown",
                      "summary": f"check #{index} has a missing, unsafe or duplicate id: {cid!r}",
                      "evidence": [], "details": {"spec": spec}, "limitations": []}
            write_json(run_dir / "checks" / f"invalid-{index}.json", record)
            results.append(record)
            continue
        seen.add(cid)
        ctx = Ctx(run_dir, manifest, spec, deadline)
        func = CHECK_FUNCS.get(spec.get("kind"))
        try:
            if ctx.remaining() < MIN_CHECK_SECONDS:
                raise CheckError("interrupted", "run deadline exhausted before this check started")
            if func is None:
                raise CheckError("unsupported", f"unknown check kind {spec.get('kind')!r}")
            from .stages import before_check
            if before_check(ctx, results):
                func(ctx)
            from .stages import validate_gate_findings
            try:
                validate_gate_findings(ctx.result)
            except ValueError as exc:
                ctx.result['details'].pop('gate_findings', None)
                raise CheckError('malformed', str(exc)) from exc
        except CheckError as exc:
            ctx.result["execution"] = exc.execution
            ctx.result["outcome"] = "unknown"
            ctx.result["summary"] = str(exc)
        write_json(run_dir / "checks" / f"{cid}.json", ctx.result)
        results.append(ctx.result)
    return results
