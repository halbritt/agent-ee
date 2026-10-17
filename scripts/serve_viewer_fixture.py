#!/usr/bin/python3
"""Serve a disposable native board export for local browser checks."""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_ee.viewer import build_viewer, serve_viewer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent-ee-viewer-fixture-") as temporary:
        page = build_viewer(ROOT / "fixtures/rc/control/rc_filter.kicad_pcb", Path(temporary) / "viewer")
        with serve_viewer(page.parent, port=args.port) as server:
            print(f"Viewer fixture ready on port {server.server_address[1]}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
