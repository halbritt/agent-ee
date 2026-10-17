"""Generate an empty project using the installed KiCad native settings writer.

Run with KiCad 10.0.7's system Python; no design files or source project needed.
"""
from pathlib import Path
import hashlib
import json
import tempfile
import pcbnew

if __name__ == '__main__':
    if not pcbnew.GetBuildVersion().startswith('10.0.7-'):
        raise SystemExit('qualification requires KiCad 10.0.7')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'native.kicad_pro'
        manager = pcbnew.SETTINGS_MANAGER()
        # LoadProject registers defaults even when a fresh path cannot be read.
        # KiCad SETTINGS_MANAGER::loadProjectFile returns LoadFromFile's False.
        if manager.LoadProject(str(path)) is not False:
            raise SystemExit('fresh project unexpectedly loaded an existing file')
        if not manager.SaveProject():
            raise SystemExit('native project save failed')
        raw = path.read_bytes()
        expected = json.loads(Path(__file__).with_name('qualification.json').read_text())['fixture_sha256']
        if hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('native writer bytes differ from qualified fixture')
        Path(__file__).with_name('native.kicad_pro').write_bytes(raw)
