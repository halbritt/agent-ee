"""Optional source-bound schematic images; visual drift never produces a verdict."""

from . import native_run
from .check_registry import register as _register_check
from . import check_prerequisites as _requires

import argparse
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from .candidate import safe_relative, verify_snapshot
from .schematic_lint import load_sheets
from .storage import digest, identity, read_json, write_json
from .symbol_library import children

SCHEMA = 'agent-ee.schematic-render/1'
DPI = 127  # Exactly five pixels per schematic millimetre, without cropping.
PIXELS_PER_MM = 5
GRID_MM = 20
MAX_PIXELS = 16_000_000
PDF_OPTIONS = ['--black-and-white', '--exclude-drawing-sheet',
               '--exclude-pdf-property-popups', '--exclude-pdf-hierarchical-links',
               '--exclude-pdf-metadata']
RASTER_OPTIONS = ['-r', str(DPI), '-aa', 'no', '-aaVector', 'no', '-singlefile']
# Owned 3 by 5 glyphs, kept independent of installed fonts.
GLYPHS = dict(zip('0123456789mxy', (
    '111101101101111', '010110010010111', '111001111100111', '111001111001111',
    '101101111001001', '111100111001111', '111100111101111', '111001001001001',
    '111101111101111', '111101111001111', '000111111101101', '000101010101000',
    '000101111001110')))


def parameters(params, files):
    if not isinstance(params, dict) or set(params) - {'baseline'}:
        raise ValueError('schematic_render accepts only an optional captured baseline directory')
    if 'baseline' not in params:
        return []
    prefix = params['baseline']
    if not isinstance(prefix, str) or str(safe_relative(prefix)) != prefix:
        raise ValueError('visual baseline must be a normalized captured directory')
    paths = sorted(p for p in files if p.startswith(prefix + '/'))
    if prefix + '/manifest.json' not in paths:
        raise ValueError('visual baseline manifest is not captured')
    return paths


def _one(node, key):
    found = children(node, key)
    if len(found) != 1:
        raise ValueError(f'expected one schematic {key}')
    return found[0]


def sheet_pages(sheets, project):
    """Resolve KiCad's explicit page selectors without guessing export order."""
    pages, by_instance = {}, {s['instance_path']: s for s in sheets}
    for sheet in sheets:
        if sheet['path'] == '/':
            paths = children(_one(sheet['tree'], 'sheet_instances'), 'path')
            matches = [p for p in paths if len(p) >= 2 and p[1] == '/']
        else:
            parent_path, uuid = sheet['instance_path'].rsplit('/', 1)
            parent = by_instance[parent_path]
            nodes = [n for n in children(parent['tree'], 'sheet') if _one(n, 'uuid')[1] == uuid]
            instances = _one(nodes[0], 'instances') if len(nodes) == 1 else []
            projects = [p for p in children(instances, 'project') if len(p) >= 2 and p[1] == project]
            matches = [p for pr in projects for p in children(pr, 'path') if len(p) >= 2 and p[1] == parent_path]
        if len(matches) != 1:
            raise ValueError('missing or ambiguous sheet page instance')
        page = _one(matches[0], 'page')
        if len(page) != 2 or not isinstance(page[1], str) or not re.fullmatch(r'[1-9][0-9]{0,4}', page[1]) or page[1] in pages.values():
            raise ValueError('missing, duplicate or unsupported sheet page number')
        pages[sheet['path']] = page[1]
    return pages


def read_ppm(path):
    if Path(path).stat().st_size > MAX_PIXELS * 3 + 128:
        raise ValueError('render file outside supported bounds')
    data = Path(path).read_bytes()
    match = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255[\r\n ]', data)
    if not match:
        raise ValueError('renderer did not write an 8-bit RGB PPM')
    width, height = map(int, match.groups())
    if not 0 < width * height <= MAX_PIXELS or not width or not height:
        raise ValueError('render dimensions outside supported bounds')
    pixels = data[match.end():]
    if len(pixels) != 3 * width * height:
        raise ValueError('truncated or trailing PPM pixels')
    return width, height, pixels


def _ppm(width, height, pixels):
    return f'P6\n{width} {height}\n255\n'.encode() + pixels


def grid(width, height, pixels):
    image = bytearray(pixels)

    def dot(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            at = 3 * (y * width + x)
            image[at:at + 3] = color

    def label(x, y, text):
        for index, char in enumerate(text):
            for row in range(5):
                for col in range(3):
                    # Double size glyphs: legible 6 by 10 pixel numerals.
                    color = b'\x00\x50\xa0' if GLYPHS[char][row * 3 + col] == '1' else b'\xff\xff\xff'
                    for dx in range(2):
                        for dy in range(2):
                            dot(x + index * 8 + col * 2 + dx, y + row * 2 + dy, color)

    step = GRID_MM * PIXELS_PER_MM
    for x in range(0, width, step):
        for y in range(height):
            dot(x, y, b'\xc0\xd8\xf0')
    for y in range(0, height, step):
        for x in range(width):
            dot(x, y, b'\xc0\xd8\xf0')
    for x in range(0, width - 35, step):
        label(x + 3, 3, str(x // PIXELS_PER_MM))
    for y in range(step, height - 13, step):
        label(3, y + 3, str(y // PIXELS_PER_MM))
    label(3, 18, 'xymm')
    return bytes(image)


def png_bytes(width, height, pixels):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    rows = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def _command(argv, out, label):
    execution = native_run.run(native_run.Intent('stage', argv, timeout=90, isolation='host'), work=None)
    if execution['execution'] == 'unavailable':
        raise FileNotFoundError(execution.stdout.decode())
    if execution['execution'] == 'timeout':
        raise subprocess.TimeoutExpired(argv, 90, output=execution.stdout, stderr=execution.stderr)
    result = subprocess.CompletedProcess(argv, execution.returncode, execution.stdout, execution.stderr)
    (out / (label + '.stdout.log')).write_bytes(result.stdout)
    (out / (label + '.stderr.log')).write_bytes(result.stderr)
    if result.returncode:
        raise ValueError(f'{label} exited {result.returncode}; missing visual evidence')
    return (result.stdout + result.stderr).decode('utf-8', 'replace').strip()


def render(inputs, root, out, candidate_id):
    """Run only in the executor's networkless sandbox. Keep failures inspectable."""
    out.mkdir()
    manifest = {'schema': SCHEMA, 'candidate_id': candidate_id, 'status': 'missing',
                'sheets': {}, 'limitations': ['Visual evidence only; no engineering verdict.']}
    try:
        tools = {}
        for name, args, version in (('kicad-cli', ['version'], '10.0.7'),
                                    ('pdftoppm', ['-v'], '24.02.0')):
            path = shutil.which(name)
            if path is None:
                raise ValueError(f'{name} unavailable; missing visual evidence')
            actual = _command([path, *args], out, name + '-version')
            expected = version if name == 'kicad-cli' else 'pdftoppm version ' + version
            if not actual or actual.splitlines()[0] != expected:
                raise ValueError(f'{name} requires pinned version {version}, observed {actual!r}')
            tools[name] = {'path': path, 'version': version, 'binary_sha256': digest(path)}
        renderer = {'tools': tools, 'pdf_options': PDF_OPTIONS, 'raster_options': RASTER_OPTIONS,
                    'implementation_sha256': digest(__file__), 'python': sys.version,
                    'zlib': zlib.ZLIB_RUNTIME_VERSION, 'grid_mm': GRID_MM, 'pixels_per_mm': PIXELS_PER_MM}
        manifest.update(renderer=renderer, renderer_id=identity(renderer),
                        coordinates={'origin_px': [0, 0], 'origin_mm': [0, 0],
                                     'pixels_per_mm': PIXELS_PER_MM, 'grid_mm': GRID_MM,
                                     'x_direction': 'right', 'y_direction': 'down',
                                     'legend': 'x y mm; labels are schematic millimetres; no crop or rotation'})
        sheets = load_sheets(inputs, root)
        pages = sheet_pages(sheets, Path(root).stem)
        manifest['sources'] = {s['file']: s['sha256'] for s in sheets}
        for sheet in sheets:
            key = sheet['path']
            prefix = identity(key)[:20]
            pdf = out / (prefix + '.pdf')
            command = ['kicad-cli', 'sch', 'export', 'pdf', *PDF_OPTIONS, '--pages', pages[key],
                       '--output', str(pdf), str(inputs / root)]
            _command(command, out, prefix + '-export')
            ppm = out / (prefix + '.ppm')
            _command(['pdftoppm', *RASTER_OPTIONS, str(pdf), str(out / prefix)], out, prefix + '-raster')
            width, height, pixels = read_ppm(ppm)
            pixels = grid(width, height, pixels)
            ppm.write_bytes(_ppm(width, height, pixels))
            png = out / (prefix + '.png')
            png.write_bytes(png_bytes(width, height, pixels))
            manifest['sheets'][key] = {'file': sheet['file'], 'source_sha256': sheet['sha256'],
                'instance_path': sheet['instance_path'], 'page': pages[key], 'width': width, 'height': height,
                'ppm': ppm.name, 'ppm_sha256': digest(ppm), 'png': png.name, 'png_sha256': digest(png)}
        manifest['status'] = 'available'
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        manifest['error'] = f'{type(exc).__name__}: {exc}'
    write_json(out / 'manifest.json', manifest)
    return manifest


def _image(directory, sheet):
    paths = {}
    for kind in ('ppm', 'png'):
        name = sheet[kind]
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('unsafe visual artifact path')
        path = directory / name
        if path.is_symlink() or digest(path) != sheet[kind + '_sha256']:
            raise ValueError('visual artifact missing or changed')
        paths[kind] = path
    width, height, pixels = read_ppm(paths['ppm'])
    if (width, height) != (sheet['width'], sheet['height']):
        raise ValueError('visual dimensions changed')
    if paths['png'].read_bytes() != png_bytes(width, height, pixels):
        raise ValueError('PNG does not match retained pixels')
    return width, height, pixels


def _validate_manifest(manifest):
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA
            or manifest.get('status') != 'available'
            or not isinstance(manifest.get('sheets'), dict) or not manifest['sheets']
            or not isinstance(manifest.get('sources'), dict)
            or not isinstance(manifest.get('renderer'), dict)):
        raise ValueError('missing or malformed visual evidence manifest')
    if (not isinstance(manifest.get('candidate_id'), str)
            or not re.fullmatch('[0-9a-f]{64}', manifest['candidate_id'])):
        raise ValueError('missing visual candidate identity')
    if manifest.get('renderer_id') != identity(manifest['renderer']):
        raise ValueError('renderer identity changed')
    for key, sheet in manifest['sheets'].items():
        if not isinstance(key, str) or not isinstance(sheet, dict):
            raise ValueError('malformed sheet inventory')
        source = sheet.get('file')
        sha = sheet.get('source_sha256')
        if (not isinstance(source, str) or str(safe_relative(source)) != source
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)
                or manifest['sources'].get(source) != sha):
            raise ValueError('missing visual source binding')
        if any(type(sheet.get(axis)) is not int or sheet[axis] <= 0 for axis in ('width', 'height')):
            raise ValueError('invalid sheet dimensions')


def compare(baseline, current):
    """Compare sealed visual artifacts. Unknown is never pass; no threshold verdict."""
    result = {'status': 'unknown', 'reason': 'no_baseline', 'sheets': {}}
    if baseline is None:
        return result
    try:
        baseline, current = Path(baseline), Path(current)
        before, after = (read_json(p / 'manifest.json') for p in (baseline, current))
        for manifest in (before, after):
            _validate_manifest(manifest)
        if before['renderer_id'] != after['renderer_id']:
            result['reason'] = 'baseline_stale_renderer'
            return result
        if before['sheets'].keys() != after['sheets'].keys():
            result['reason'] = 'sheet_inventory_changed'
            return result
        for key, sheet in after['sheets'].items():
            width, height, old = _image(baseline, before['sheets'][key])
            w, h, new = _image(current, sheet)
            if (width, height) != (w, h):
                raise ValueError('sheet dimensions changed')
            count, left, top, right, bottom = 0, width, height, -1, -1
            for offset in range(0, len(old), 3):
                if old[offset:offset + 3] != new[offset:offset + 3]:
                    count += 1
                    x, y = offset // 3 % width, offset // 3 // width
                    left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
            result['sheets'][key] = {'changed_pixels': count, 'drift_percent': 100 * count / (width * height),
                'changed_bbox_px': [left, top, right + 1, bottom + 1] if count else None,
                'baseline_source_sha256': before['sheets'][key]['source_sha256'],
                'source_sha256': sheet['source_sha256']}
        result.update(status='compared', reason=None, baseline_candidate_id=before['candidate_id'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(reason=f'missing_or_invalid_visual_evidence: {exc}', sheets={})
    return result


def check_schematic_render(ctx):
    from .checks import CheckError
    # The optional renderer must never turn missing visual coverage into acceptance.
    try:
        verify_snapshot(ctx.run_dir)
        parameters(ctx.params, ctx.manifest['files'])
        root = str(safe_relative(ctx.scope['schematic']))
        if root not in ctx.manifest['files']:
            raise ValueError('schematic is not captured')
        try:
            ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.schematic_render',
                         '--root', root, '--candidate-id', ctx.manifest['candidate_id']], timeout=300)
        finally:
            ctx.evidence(*(p.relative_to(ctx.work).as_posix() for p in sorted(ctx.work.rglob('*')) if p.is_file()))
        manifest = ctx.load_json('render/manifest.json', 'visual evidence')
        ctx.result['details']['visual_evidence'] = manifest
        if not isinstance(manifest, dict):
            raise ValueError('visual manifest must be an object')
        baseline = ctx.inputs / ctx.params['baseline'] if 'baseline' in ctx.params else None
        drift = compare(baseline, ctx.work / 'render')
        write_json(ctx.work / 'drift.json', drift)
        ctx.evidence('drift.json')
        ctx.result['details']['visual_drift'] = drift
        if manifest.get('status') != 'available':
            raise CheckError('failed', 'missing visual evidence: ' + manifest.get('error', 'render incomplete'))
        _validate_manifest(manifest)
        if (manifest['candidate_id'] != ctx.manifest['candidate_id']
                or any(ctx.manifest['files'].get(p) != sha for p, sha in manifest['sources'].items())):
            raise ValueError('render does not bind the captured candidate')
        for sheet in manifest['sheets'].values():
            _image(ctx.work / 'render', sheet)
        ctx.result.update(execution='ok', outcome='info', summary='Schematic visual evidence available; no verdict')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'missing visual evidence: {exc}') from exc
    finally:
        if ctx.result['outcome'] != 'info':
            ctx.result['limitations'].append('Missing visual evidence; independent review may continue without images.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--candidate-id', required=True)
    args = parser.parse_args()
    render(Path('/candidate'), args.root, Path('/work/render'), args.candidate_id)


if __name__ == '__main__':
    main()


def describe_schematic_render(ctx):
    ctx.captured(ctx.scope.get('schematic'))
    for path in parameters(ctx.params, ctx.files):
        ctx.captured(path, immutable=True)


if __name__ != '__main__':
    _register_check('schematic_render', describe_schematic_render, check_schematic_render,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
