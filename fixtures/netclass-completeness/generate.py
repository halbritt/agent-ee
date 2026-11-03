"""Generate an empty project using the installed KiCad native settings writer.

Run with the installed KiCad's system Python (any 10.x release); no design files or
source project needed. It prints the writer's build version, which a qualification
records as native_writer. An optional argument names the output path; the default
replaces the fixture.
"""
from pathlib import Path
import sys
import tempfile
import pcbnew

if __name__ == '__main__':
    writer = pcbnew.GetBuildVersion()
    if not writer.startswith('10.'):
        raise SystemExit(f'qualification requires a KiCad 10.x writer, not {writer!r}')
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('native.kicad_pro')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'native.kicad_pro'
        manager = pcbnew.SETTINGS_MANAGER()
        # LoadProject reports False for a new, absent project; it still becomes active.
        manager.LoadProject(str(path))
        if not manager.SaveProject() or not path.is_file():
            raise SystemExit('native project save failed')
        target.write_bytes(path.read_bytes())
    print(writer)
