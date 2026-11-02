"""Shared fixtures and opaque-box test utilities for E2E campaign test suites."""
import json
import os
import shutil
import tempfile
from pathlib import Path

MINIMAL_BOARD_SEXPR = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user)
  )
  (setup (pad_to_mask_clearance 0.05))
  (gr_line (start 0 0) (end 50 0) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))
  (gr_line (start 50 0) (end 50 50) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))
  (gr_line (start 50 50) (end 0 50) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))
  (gr_line (start 0 50) (end 0 0) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))
)
"""

def make_test_bundle(dest: Path, board_name: str = "test_board.kicad_pcb") -> Path:
    """Create a minimal valid candidate project bundle directory."""
    dest.mkdir(parents=True, exist_ok=True)
    board_file = dest / board_name
    board_file.write_text(MINIMAL_BOARD_SEXPR, encoding="utf-8")
    pro_file = dest / board_file.with_suffix(".kicad_pro").name
    pro_file.write_text(json.dumps({"meta": {"version": 1}}), encoding="utf-8")
    prl_file = dest / board_file.with_suffix(".kicad_prl").name
    prl_file.write_text(json.dumps({"meta": {"version": 1}}), encoding="utf-8")
    return board_file

def make_test_packet(work_dir: Path, candidate_id: str = "cand-01", run_id: str = "run-01") -> dict:
    """Create a standard packet dictionary and packet.json in work_dir."""
    packet = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "packet_sha256": "0" * 64,
        "inputs": {"project": "test_board.kicad_pcb"}
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    return packet

