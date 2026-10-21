"""Native local fixture for gallery browser checks; never uses production data."""
import argparse
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_ee.render3d import render_board
from agent_ee.viewer import build_viewer
from agent_ee.render_gallery import publish, serve

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
board = Path(__file__).resolve().parents[1] / 'fixtures/rc/control/rc_filter.kicad_pcb'
with tempfile.TemporaryDirectory(prefix='gallery-ui-') as temporary:
    root = Path(temporary)
    render_board(board, root/'renders', width=800, height=600, quality='basic', jobs=1)
    build_viewer(board, root/'viewer')
    for label in ('Previous fixture', 'Latest fixture'):
        publish(root/'public', root/'renders', label=label, stage='Synthetic RC test fixture', viewer=root/'viewer')
    with serve(root/'public', '127.0.0.1', args.port) as httpd:
        httpd.serve_forever()
