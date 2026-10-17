"""Generate an empty project using the installed KiCad native settings writer.

Run with KiCad 10.0.7's system Python; no design files or source project needed.
An optional argument names the output path; the default replaces the fixture.
"""
from pathlib import Path
import sys
import tempfile
import pcbnew

if __name__ == '__main__':
    if not pcbnew.GetBuildVersion().startswith('10.0.7-'):
        raise SystemExit('qualification requires KiCad 10.0.7')
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('native.kicad_pro')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'native.kicad_pro'
        manager = pcbnew.SETTINGS_MANAGER()
        # LoadProject reports False for a new, absent project; it still becomes active.
        manager.LoadProject(str(path))
        if not manager.SaveProject() or not path.is_file():
            raise SystemExit('native project save failed')
        target.write_bytes(path.read_bytes())
