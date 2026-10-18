"""Networkless sandbox entry point; execute installed scripts without vendoring."""
import contextlib
import hashlib
import runpy
import sys
from pathlib import Path

from .kicad_happy import ANALYZERS, SEVERITY_ANALYZERS, normalize
from .checks import CheckError
from .netclass_completeness import load_json
from .storage import read_json, write_json


def main():
    request = read_json('/work/request.json')
    analyzers = SEVERITY_ANALYZERS if request.get('profile') == 'severity_gate' else ANALYZERS
    root = Path('/analyzer')
    executed = {}
    seen = set()

    def trace(frame, event, arg):
        if event == 'call' and frame.f_code.co_filename not in seen:
            seen.add(frame.f_code.co_filename)
            path = Path(frame.f_code.co_filename)
            if path.is_absolute() and path.is_relative_to(root) and path.is_file():
                rel = path.relative_to(root).as_posix()
                if rel not in executed:
                    executed[rel] = hashlib.sha256(path.read_bytes()).hexdigest()

    # Ignore pre-existing installed bytecode: evidence identifies source bytes.
    sys.pycache_prefix = "/work/bytecode"
    sys.dont_write_bytecode = True
    sys.setprofile(trace)
    try:
        for name, script in analyzers.items():
            path = root / script
            sys.path.insert(0, str(path.parent))
            args = [str(path)]
            if name == 'schematic':
                args += [request['schematic']]
            elif name == 'pcb':
                args += [request['pcb'], '--full']
            else:
                args += ['--schematic', '/work/schematic.json', '--pcb', '/work/pcb.json']
            if name == 'thermal':
                args += ['--ambient', str(request['ambient_c'])]
            args += ['--output', f'/work/{name}.json']
            if name != 'cross_analysis':
                args += ['--config', '/work/config.json']
            sys.argv = args
            write_json(f'/work/{name}-argv.json', args)
            with open(f'/work/{name}-stdout.log', 'w') as out, open(f'/work/{name}-stderr.log', 'w') as err:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        runpy.run_path(str(path), run_name='__main__')
                    except SystemExit as exc:
                        if exc.code not in (None, 0):
                            raise
            try:
                report = (load_json(f'/work/{name}.json') if request.get('profile') == 'severity_gate'
                          else read_json(f'/work/{name}.json'))
            except (ValueError, OSError) as exc:
                write_json('/work/stage-status.json', {'execution': 'malformed', 'summary': str(exc)})
                return
            try:
                normalize({name: report}, [])
            except CheckError as exc:
                write_json('/work/stage-status.json', {'execution': exc.execution, 'summary': str(exc)})
                return
        write_json('/work/stage-status.json', {'execution': 'ok'})
    finally:
        sys.setprofile(None)
        write_json('/work/executed.json', executed)


if __name__ == '__main__':
    main()
