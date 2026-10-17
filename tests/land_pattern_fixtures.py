"""Owned synthetic EasyEDA-format and KiCad land patterns (no supplier data)."""
from fractions import Fraction
import hashlib
import json

from agent_ee.easyeda_footprint import text


def _f(value):
    return text(Fraction(str(value)))


def pad(shape, cx, cy, w, h, number, *, layer='1', rotation='0', hole_r='0', hole_len='0', hole_pts='',
        plated='Y', ident='g1', points=None):
    cx, cy, w, h = (Fraction(str(v)) for v in (cx, cy, w, h))
    if points is None:
        if shape == 'RECT':
            turn = Fraction(rotation) % 360 if rotation not in ('', 'NaN') else 0
            hw, hh = (w / 2, h / 2) if turn in (0, 180) else (h / 2, w / 2)
            corners = [(cx - hw, cy + hh), (cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh)]
            points = ' '.join(_f(c) for corner in corners for c in corner)
        elif shape == 'OVAL':
            span = abs(w - h) / 2
            turn = Fraction(rotation) % 360 if rotation not in ('', 'NaN') else 0
            if (w > h) == (turn in (0, 180)):
                points = f'{_f(cx - span)} {_f(cy)} {_f(cx + span)} {_f(cy)}'
            else:
                points = f'{_f(cx)} {_f(cy - span)} {_f(cx)} {_f(cy + span)}'
        else:
            points = ''
    fields = ['PAD', shape, _f(cx), _f(cy), _f(w), _f(h), layer, '', number, str(hole_r), points, rotation,
              ident, str(hole_len), hole_pts, plated, '0', '0', '0.2', f'{_f(cx)},{_f(cy)}']
    return '~'.join(fields)


SILK = 'TRACK~0.6~3~~3990 3010 4010 3010~gge90~0'
MODEL = 'SVGNODE~' + json.dumps(dict(gId='g1_outline', nodeName='g', nodeType=1, layerid='19',
                                     attrs=dict(c_etype='outline3D'), childNodes=[]))


def component(lcsc, shapes, *, origin=(4000, 3000), package='OWNED-PKG', puuid='p0000000000000000000000000000001',
              **override):
    head = dict(docType='2', c_para={'package': package, 'Supplier Part': lcsc, 'Manufacturer Part': 'OWNED'},
                x=20, y=0, puuid=puuid, uuid='s0000000000000000000000000000001')
    value = dict(uuid='c0000000000000000000000000000001', title='OWNED', docType=2,
                 lcsc=dict(id=1, number=lcsc), szlcsc=dict(id=1, number=lcsc),
                 dataStr=dict(head=head, shape=[]),
                 packageDetail=dict(uuid=puuid, title=package, docType=4,
                                    dataStr=dict(head=dict(docType='4', uuid=puuid, x=origin[0], y=origin[1]),
                                                 shape=list(shapes))))
    value.update(override)
    return json.dumps(value, sort_keys=True).encode()


def provenance(lcsc, path, data):
    return json.dumps(dict(schema='agent-ee.supplier-footprint/1', lcsc=lcsc, format='easyeda-std-component/1',
                           path=path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                           source_url='https://example.invalid/owned-fixture', retrieved_at='2026-10-17T00:00:00Z',
                           retrieval='owned synthetic fixture; not fetched'), sort_keys=True).encode()


def kicad_footprint(name, pads, *, extra=''):
    return f'(footprint "{name}" (version 20260206) (layer "F.Cu") (attr smd) {extra} {pads})\n'.encode()


def manifest(members, library='Owned'):
    return dict(schema='agent-ee.footprint-libraries/1', complete=True,
                libraries=[dict(name=library, members=[dict(path=path, sha256=hashlib.sha256(data).hexdigest())
                                                       for path, data in sorted(members.items())])])


# Owned SOT-23-like geometry in exact EasyEDA units (1 unit = 0.254 mm). KiCad
# side uses rotation 0; the EasyEDA side is drawn a quarter turn away so the
# declared registration rotation (90) is exercised.
SOT_KICAD = ('(pad "1" smd rect (at -0.9525 -0.9525) (size 1.3208 0.6096) (layers "F.Cu" "F.Paste" "F.Mask")) '
             '(pad "2" smd rect (at -0.9525 0.9525) (size 1.3208 0.6096) (layers "F.Cu" "F.Paste" "F.Mask")) '
             '(pad "3" smd rect (at 0.9525 0) (size 1.3208 0.6096) (layers "F.Cu" "F.Paste" "F.Mask"))')


def sot_supplier(numbers=('1', '2', '3')):
    # Supplier point s maps to KiCad R(90)s; s = R(270)k = (-y, x).
    return [pad('RECT', 4000 + 3.75, 3000 - 3.75, 5.2, 2.4, numbers[0], rotation='270', ident='a'),
            pad('RECT', 4000 - 3.75, 3000 - 3.75, 5.2, 2.4, numbers[1], rotation='270', ident='b'),
            pad('RECT', 4000, 3000 + 3.75, 5.2, 2.4, numbers[2], rotation='270', ident='c'), SILK, MODEL]


def chip(pitch_units, w_units, h_units):
    return [pad('RECT', 4000 - pitch_units, 3000, w_units, h_units, '1', ident='a'),
            pad('RECT', 4000 + pitch_units, 3000, w_units, h_units, '2', ident='b'), SILK]


def chip_kicad(pitch_mm, w_mm, h_mm):
    return (f'(pad "1" smd rect (at -{pitch_mm} 0) (size {w_mm} {h_mm}) (layers "F.Cu" "F.Mask")) '
            f'(pad "2" smd rect (at {pitch_mm} 0) (size {w_mm} {h_mm}) (layers "F.Cu" "F.Mask"))')


def chip_roundrect_kicad(pitch_mm, w_mm, h_mm, rratio='0.25'):
    return ''.join(f'(pad "{n}" smd roundrect (at {sign}{pitch_mm} 0) (size {w_mm} {h_mm}) '
                   f'(layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio {rratio})) '
                   for n, sign in (('1', '-'), ('2', '')))
