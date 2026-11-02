"""Native footprint pad identity and copper extents; sandbox child process only."""
import json
import os
from pathlib import Path
import sys
import tempfile

from .geometry_probe import box_nm, pad_attribute, pad_vec


def _enclosing_sandbox(*, _placement=False):
    """Recognize the fixed /app view, with an exact private placement opt-in.

    The trusted executor owns PID/network isolation and mount-source identity.
    As with the private schematic-stage probe, an unknown mount view refuses
    supervised children. Ordinary standalone imports keep the sandbox default.
    """
    from .stage_execution import ConfinementView
    source = Path(__file__).resolve()
    placement = _placement and source == Path('/placement-tool/agent_ee/footprint_probe.py')
    if source != Path('/app/agent_ee/footprint_probe.py') and not placement:
        if source == Path('/tool/agent-ee/agent_ee/footprint_probe.py'):
            try:
                ConfinementView.STAGE_TOOL.verify(source_path=source)
                return True
            except Exception as exc:
                raise ValueError('custom-pad probe requires the fixed executor sandbox') from exc
        return False
    view = ConfinementView.PLACEMENT_TOOL if placement else ConfinementView.CHECK_APP
    try:
        view.verify(source_path=source)
    except Exception as exc:
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
    source = Path(__file__).resolve()
    pythonpath = '/tool/agent-ee' if source == Path('/tool/agent-ee/agent_ee/footprint_probe.py') else (
        '/placement-tool' if source == Path('/placement-tool/agent_ee/footprint_probe.py') else '/app'
    )
    environment = {**os.environ, 'PYTHONPATH': pythonpath, 'HOME': str(work / 'home'),
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
