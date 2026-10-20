"""Native footprint pad identity and copper extents; sandbox child process only."""
import json
import sys

from .geometry_probe import box_nm, pad_attribute, pad_vec


def extract(name):
    import pcbnew
    footprint = pcbnew.FootprintLoad('/candidate', name)
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
            # Other layer sets can appear beside qualified pads under numbering-only declarations.
            if len(copper) == 1:
                merged = pcbnew.SHAPE_POLY_SET()
                pad.MergePrimitivesAsPolygon(copper[0], merged)
                # Recover pcbnew hole contours from its fractured merged representation.
                merged.Unfracture()
                record['outline_count'] = merged.OutlineCount()
        pads.append(record)
    return dict(pcbnew_version=pcbnew.Version(), pads=pads)


if __name__ == '__main__':
    with open('/work/footprint.json', 'x') as stream:
        json.dump(extract(sys.argv[1]), stream, sort_keys=True)
