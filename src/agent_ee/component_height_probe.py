"""Sandboxed native STEP bounding boxes; unsupported transforms remain unknown."""
import argparse
from decimal import Decimal, localcontext
import hashlib
from pathlib import Path

from .storage import digest, read_json, write_json


def transformed_top(bounds, model):
    """Upright model bounds in the footprint's outward local Z frame."""
    scale = [Decimal(str(v)) for v in (model.m_Scale.x, model.m_Scale.y, model.m_Scale.z)]
    offset = [Decimal(str(v)) for v in (model.m_Offset.x, model.m_Offset.y, model.m_Offset.z)]
    rotation = [Decimal(str(v)) for v in (model.m_Rotation.x, model.m_Rotation.y, model.m_Rotation.z)]
    if (any(not v.is_finite() for v in scale + offset + rotation) or min(scale) <= 0 or
            any(v % 360 for v in rotation[:2])):
        raise ValueError('STEP height needs positive scale and upright model X/Y rotations')
    # Footprint placement and front/back flip preserve outward local height.
    top = Decimal(bounds['max_mm'][2])
    product_exponent = top.as_tuple().exponent + scale[2].as_tuple().exponent
    product_digits = len(top.as_tuple().digits) + len(scale[2].as_tuple().digits)
    translation = offset[2].as_tuple()
    with localcontext() as context:
        context.prec = max(product_exponent + product_digits, translation.exponent + len(translation.digits)) \
                       - min(product_exponent, translation.exponent) + 1
        return top * scale[2] + offset[2]


def extract(board_path, requests):
    import pcbnew
    path = Path(board_path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
    records = []
    for request in requests:
        item = {'ref': request['ref'], 'status': 'unknown', 'models': []}
        matches = [fp for fp in board.GetFootprints() if fp.GetReference() == request['ref']]
        try:
            if len(matches) != 1 or matches[0].GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu):
                raise ValueError('native model footprint missing, ambiguous or unsupported side')
            models = list(matches[0].Models())
            if len(models) != len(request['models']):
                raise ValueError('native model inventory differs from saved board')
            heights = []
            for model, declared in zip(models, request['models']):
                source = Path('/candidate') / declared['member']
                if model.m_Filename != declared['path'] or not model.m_Show:
                    raise ValueError('native model is hidden or differs from resolved path')
                if not source.is_file() or digest(source) != declared['sha256']:
                    raise ValueError('resolved STEP is not the captured model')
                shape = pcbnew.UTILS_STEP_MODEL.LoadSTEP(str(source))
                if shape is None:
                    raise ValueError('native STEP loader returned no shape')
                bbox = shape.GetBoundingBox()
                lo, hi = bbox.Min(), bbox.Max()
                bounds = {'min_mm': [str(v) for v in (lo.x, lo.y, lo.z)],
                          'max_mm': [str(v) for v in (hi.x, hi.y, hi.z)]}
                pairs = [(Decimal(a), Decimal(b)) for a, b in zip(bounds['min_mm'], bounds['max_mm'])]
                if any(not a.is_finite() or not b.is_finite() or a >= b for a, b in pairs):
                    raise ValueError('native STEP bounds empty or invalid')
                top = transformed_top(bounds, model)
                heights.append(top)
                item['models'].append({**declared, 'bounds': bounds, 'height_mm': str(top)})
            if not heights or max(heights) <= 0:
                raise ValueError('no positive installed STEP height')
            item.update(status='measured', height_mm=str(max(heights)))
        except (ValueError, RuntimeError) as exc:
            item['reason'] = str(exc)
        records.append(item)
    if path.read_bytes() != before:
        raise ValueError('board changed during native STEP load')
    return {'schema': 'agent-ee.component-height-probe/1', 'board_sha256': hashlib.sha256(before).hexdigest(),
            'pcbnew_version': pcbnew.Version(), 'footprints': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--request', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write_json(args.out, extract(args.board, read_json(args.request)))


if __name__ == '__main__':
    main()
