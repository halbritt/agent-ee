"""Native footprint pad identity and copper extents; sandbox child process only."""
import json
import os
from pathlib import Path
import sys
import tempfile

from .geometry_probe import box_nm, pad_attribute, pad_vec


def _enclosing_sandbox():
    """Recognize only the executor's fixed /app filesystem view.

    The trusted executor owns PID/network isolation and mount-source identity.
    As with the private schematic-stage probe, an unknown mount view refuses
    supervised children. Ordinary standalone imports keep the sandbox default.
    """
    if Path(__file__).resolve() != Path('/app/agent_ee/footprint_probe.py'):
        return False
    try:
        mounts, ids = {}, set()
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            view, filesystem = line.split(' - ')
            fields, native = view.split(), filesystem.split()
            if (len(fields) < 6 or len(native) != 3
                    or not fields[0].isdigit() or int(fields[0]) <= 0
                    or str(int(fields[0])) != fields[0] or fields[0] in ids
                    or not fields[1].isdigit() or str(int(fields[1])) != fields[1]
                    or len(fields[2].split(':')) != 2
                    or any(not n.isdigit() for n in fields[2].split(':'))
                    or any(not p.startswith('/') or '\\' in p
                           or str(Path(p)) != p or '..' in Path(p).parts
                           for p in fields[3:5])):
                raise ValueError('malformed mountinfo')
            flags = set(fields[5].split(','))
            if len(flags & {'ro', 'rw'}) != 1:
                raise ValueError('ambiguous mount permissions')
            ids.add(fields[0])
            mounts[fields[4]] = (flags, native[0])
        readonly = ('/candidate', '/app', '/usr', '/etc', '/lib', '/lib64',
                    '/bin', '/sbin', '/evidence')
        required = {'/candidate': 'ro', '/app': 'ro', '/work': 'rw', '/usr': 'ro', '/etc': 'ro'}
        special = {'/': 'tmpfs', '/proc': 'proc', '/dev': 'tmpfs', '/tmp': 'tmpfs',
                   '/dev/pts': 'devpts', '/dev/shm': 'tmpfs', '/dev/mqueue': 'mqueue'}
        devices = {'/dev/' + name for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty')}
        def supported(path, flags, filesystem):
            if path in special:
                return filesystem == special[path]
            if path in devices:
                return filesystem in ('tmpfs', 'devtmpfs')
            if Path(path).is_relative_to('/proc'):
                return filesystem == 'proc'
            if Path(path).is_relative_to('/work'):
                return True
            return any(Path(path).is_relative_to(root) for root in readonly) and 'ro' in flags
        if (any(path not in mounts for path in ('/', '/proc', '/dev', '/tmp'))
                or any(mode not in mounts.get(path, (set(), None))[0] for path, mode in required.items())
                or any(not supported(path, flags, filesystem) for path, (flags, filesystem) in mounts.items())):
            raise ValueError('unsupported mount view')
    except (OSError, ValueError, IndexError) as exc:
        raise ValueError('custom-pad probe requires the fixed executor sandbox') from exc
    return True


def _enclosed_geometry(data, footprint):
    """Use the existing supervised child only inside the verified outer sandbox."""
    if not _enclosing_sandbox():
        return None
    from . import native_run
    from .stage_receipts import file_tree
    parent = Path('/work/library-probes')
    parent.mkdir(exist_ok=True)
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError('custom-pad scratch must stay in outer work')
    root = Path(tempfile.mkdtemp(prefix='member-', dir=parent))
    inputs, work = root / 'inputs', root / 'work'
    inputs.mkdir()
    member = inputs / (footprint['name'] + '.kicad_mod')
    member.write_bytes(data)
    member.chmod(0o400)
    before, other = file_tree(inputs)
    if other:
        raise ValueError('custom-pad inputs must be regular captured files')
    environment = {**os.environ, 'PYTHONPATH': '/app', 'HOME': str(work / 'home'),
                   'KICAD_CONFIG_HOME': str(work / 'kicad-config'),
                   'KICAD10_SYMBOL_DIR': '/candidate/stock/symbols',
                   'KICAD10_FOOTPRINT_DIR': '/candidate/stock/footprints',
                   'KICAD10_TEMPLATE_DIR': '/candidate/stock/template'}
    command = ('from agent_ee.footprint_probe import _stage_main; import sys; '
               '_stage_main(*sys.argv[1:])')
    record = native_run.run(native_run.Intent('pcbnew_script',
        ['/usr/bin/python3', '-B', '-P', '-c', command, str(inputs), footprint['name'],
         str(work / 'footprint.json')], timeout=60, isolation='host', env=environment), work=work)
    after, other = file_tree(inputs)
    if other or after != before:
        raise ValueError('custom-pad child changed its captured inputs')
    if record['execution'] != 'ok':
        console = (work / 'console.log').read_text(errors='replace')[-2000:]
        raise ValueError('native custom-pad load ' + record['execution'] + ': ' + console)
    # Keep the inputs, native records and output in outer work on every outcome.
    report = json.loads((work / 'footprint.json').read_bytes())
    if not isinstance(report, dict):
        raise ValueError('native custom-pad report must be an object')
    return report


def extract(name, *, _library="/candidate"):
    import pcbnew
    footprint = pcbnew.FootprintLoad(_library, name)
    if footprint is None:
        raise ValueError('captured footprint did not load')
    shapes = {pcbnew.PAD_SHAPE_RECT: 'rect', pcbnew.PAD_SHAPE_CIRCLE: 'circle',
              pcbnew.PAD_SHAPE_OVAL: 'oval', pcbnew.PAD_SHAPE_ROUNDRECT: 'roundrect',
              pcbnew.PAD_SHAPE_CUSTOM: 'custom', pcbnew.PAD_SHAPE_TRAPEZOID: 'trapezoid'}
    types = {'SMD': 'smd', 'PTH': 'thru_hole', 'NPTH': 'np_thru_hole'}
    pads = []
    for pad in footprint.Pads():
        pos = pad.GetPosition()
        size = pad_vec(pad, pad.GetSize)
        record = dict(number=pad.GetNumber(), type=types[pad_attribute(pad)],
                         shape=shapes[pad.GetShape(pcbnew.PADSTACK.ALL_LAYERS)],
                         x_nm=int(pos.x), y_nm=int(pos.y), size_nm=[int(size.x), int(size.y)],
                         orientation_microdegrees=round(pad.GetOrientationDegrees() * 1000000),
                         bbox_nm=box_nm(pad.GetBoundingBox()))
        if record['shape'] == 'custom':
            record['anchor'] = shapes[pad.GetAnchorPadShape(pcbnew.PADSTACK.ALL_LAYERS)]
            copper = list(pad.GetLayerSet().CuStack())
            counts = []
            for layer in copper:
                merged = pcbnew.SHAPE_POLY_SET()
                pad.MergePrimitivesAsPolygon(layer, merged)
                # Recover pcbnew hole contours from its fractured merged representation.
                merged.Unfracture()
                counts.append(merged.OutlineCount())
            record['layer_outline_counts'] = counts
            if len(counts) == 1:
                record['outline_count'] = counts[0]
        pads.append(record)
    return dict(pcbnew_version=pcbnew.Version(), pads=pads)


def _stage_main(library, name, output):
    """Fixed schematic-stage child with concrete paths inside its outer sandbox."""
    with open(output, 'x') as stream:
        json.dump(extract(name, _library=library), stream, sort_keys=True)


if __name__ == '__main__':
    with open('/work/footprint.json', 'x') as stream:
        json.dump(extract(sys.argv[1]), stream, sort_keys=True)
