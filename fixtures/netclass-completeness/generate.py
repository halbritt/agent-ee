"""Generate an empty project using the installed KiCad native settings writer.

Run with KiCad 10.0.7's system Python; no design files or source project needed.
"""
from pathlib import Path
import tempfile
import pcbnew

if __name__ == '__main__':
    if not pcbnew.GetBuildVersion().startswith('10.0.7-'):
        raise SystemExit('qualification requires KiCad 10.0.7')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'native.kicad_pro'
        manager = pcbnew.SETTINGS_MANAGER()
        if not manager.LoadProject(str(path)) or not manager.SaveProject():
            raise SystemExit('native project load/save failed')
        Path(__file__).with_name('native.kicad_pro').write_bytes(path.read_bytes())
