"""Measure the generated regulator stencil in real KiCad Gerber output."""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import unittest

from tests.artifact_retention import RetainedTemporaryDirectory, retain

from agent_ee.acquisition import reverse_blocking as variant
from agent_ee import executor
from tests.test_acquisition_reverse_blocking import CAPTURES, require_captures
from tests.toolchain import require_toolchain


def ep_rectangles(gerber):
    """Read this fixture's rectangular flashes/regions; refuse unknown EP shapes."""
    if '%FSLAX46Y46*%' not in gerber or '%MOMM*%' not in gerber:
        raise ValueError('unexpected native Gerber units/precision')
    apertures = {int(n): (Fraction(w), Fraction(h)) for n, w, h in re.findall(
        r'%ADD(\d+)R,([\d.]+)X([\d.]+)\*%', gerber)}
    rectangles, region, aperture = [], None, None
    # The native board places U1 at (10,10); Gerber Y increases upwards.
    def local(x, y):
        return Fraction(x, 1000000) - 10, Fraction(y, 1000000) + 10
    for line in gerber.splitlines():
        if line == 'G36*':
            if region is not None:
                raise ValueError('nested native stencil region')
            region = []
        elif line == 'G37*':
            if region is None or len(region) != 5 or region[0] != region[-1]:
                raise ValueError('unexpected native region contour')
            xs, ys = zip(*region)
            box = min(xs), min(ys), max(xs), max(ys)
            if set(region) != {(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])}:
                raise ValueError('nonrectangular stencil region')
            rectangles.append(box)
            region = None
        elif re.fullmatch(r'D\d+\*', line):
            aperture = int(line[1:-1])
        else:
            point = re.fullmatch(r'(?:G01)?X(-?\d+)Y(-?\d+)D(0[123])\*', line)
            if point:
                x, y = local(int(point[1]), int(point[2]))
                if region is not None:
                    region.append((x, y))
                elif point[3] == '01':
                    raise ValueError('unexpected stencil stroke outside a region')
                elif point[3] == '03' and abs(x) < Fraction('1.3') and abs(y) < Fraction('1.6'):
                    if aperture not in apertures:
                        raise ValueError('nonrectangular flashed EP aperture')
                    w, h = apertures[aperture]
                    rectangles.append((x - w/2, y - h/2, x + w/2, y + h/2))
            elif re.match(r'(?:G0?[123])?[XY]|G0?[23]|%LPC', line):
                raise ValueError('unsupported native stencil geometry')
    if region is not None:
        raise ValueError('unfinished native stencil region')
    return rectangles


@require_captures
class NativeStencilTests(unittest.TestCase):
    def test_exported_paste_is_segmented_and_clear_of_every_drill(self):
        require_toolchain(native=True)
        with RetainedTemporaryDirectory(self.id()) as tmp:
            root = Path(tmp)
            lib = root / 'power.pretty'
            lib.mkdir()
            generated, _ = variant.inputs((variant.DATA / 'base-circuit.json').read_bytes(),
                (CAPTURES / 'C2903961.raweasy.json').read_bytes(),
                (CAPTURES / 'C155367.raweasy.json').read_bytes())
            (lib / (variant.FOOTPRINT + '.kicad_mod')).write_bytes(
                generated[variant.LIBRARY + '.pretty/' + variant.FOOTPRINT + '.kicad_mod'])
            (root / 'power-assembly.json').write_bytes(generated['power-assembly.json'])
            design = json.loads(generated['power-assembly.json'])
            thickness = Fraction(design['stencil_thickness_mm'])
            self.assertGreater(thickness, 0)
            (root / 'export.py').write_text('''import json, subprocess, sys
from pathlib import Path
import pcbnew as k
fp = k.PCB_IO_KICAD_SEXPR().FootprintLoad('/candidate/power.pretty', sys.argv[1])
assert fp is not None
board = k.BOARD()
board.Add(fp)
fp.SetReference('U1')
fp.SetPosition(k.VECTOR2I(10000000, 10000000))
k.SaveBoard('/work/u1.kicad_pcb', board)
holes = []
for pad in fp.Pads():
    if pad.GetAttribute() == k.PAD_ATTRIB_PTH:
        p, d = pad.GetPosition(), pad.GetDrillSize()
        holes.append([p.x-10000000, -(p.y-10000000), d.x, d.y])
Path('/work/holes.json').write_text(json.dumps(holes))
subprocess.run(['kicad-cli', 'pcb', 'export', 'gerbers', '--layers',
    'F.Paste,F.Mask,B.Mask', '--output', '/work/gerbers/', '/work/u1.kicad_pcb'], check=True)
subprocess.run(['kicad-cli', 'pcb', 'export', 'drill', '--output',
    '/work/drills/', '/work/u1.kicad_pcb'], check=True)
''')
            work = root.parent / (root.name + '-native')
            self.addCleanup(shutil.rmtree, work, True)
            self.addCleanup(retain, self.id(), None, roots=[work])
            result = executor.execute(root, work,
                ['/usr/bin/python3', '/candidate/export.py', variant.FOOTPRINT], timeout=45)
            capture = os.environ.get('AGENT_EE_STENCIL_ARTIFACTS')
            if capture:
                shutil.copytree(work, capture)
            self.assertEqual(result['execution'], 'ok', result)
            paste = (work / 'gerbers/u1-F_Paste.gtp').read_text()
            rectangles = ep_rectangles(paste)
            holes = json.loads((work / 'holes.json').read_text())
            area = sum((r-l)*(t-b) for l,b,r,t in rectangles)
            ep_area = Fraction(2410003, 1000000) * Fraction(3098013, 1000000)
            clearance = min(math.hypot(max(l-x/1e6, 0, x/1e6-r),
                max(b-y/1e6, 0, y/1e6-t)) - diameter/2e6
                for l,b,r,t in rectangles for x,y,diameter,_ in holes)
            report = dict(apertures=len(rectangles), paste_area_mm2=float(area),
                ep_copper_area_mm2=float(ep_area), fraction_of_ep_copper=float(area/ep_area),
                minimum_drill_clearance_mm=clearance, holes=holes,
                rectangles_mm=[[float(v) for v in row] for row in rectangles])
            if capture:
                (Path(capture) / 'measurements.json').write_text(json.dumps(report, indent=2)+'\n')
            self.assertEqual(len(holes), 4)
            self.assertTrue(all(row[2:] == [304800, 304800] for row in holes))
            declared = {(Fraction(l), -Fraction(t), Fraction(r), -Fraction(b))
                        for l,b,r,t in design['paste_rectangles_mm']}
            self.assertEqual(set(rectangles), declared)
            self.assertGreater(clearance, .1, report)
            self.assertEqual(len(rectangles), 9, report)
            self.assertGreaterEqual(area/ep_area, Fraction('0.5'), report)
            self.assertLessEqual(area/ep_area, Fraction('0.7'), report)
            for i, (l,b,r,t) in enumerate(rectangles):
                with self.subTest(aperture=i):
                    self.assertTrue(-1.205 <= l < r <= 1.205 and -1.549 <= b < t <= 1.549)
                    # Area ratio remains above JLC's 0.66 guidance even when both
                    # edges are cut 0.003 mm inward at 0.15 mm stencil thickness.
                    w, h = r-l, t-b
                    self.assertGreaterEqual(min(w,h)/thickness, Fraction('1.5'))
                    nw, nh = w-Fraction('.006'), h-Fraction('.006')
                    self.assertGreater(nw*nh/(2*thickness*(nw+nh)), Fraction('.66'))
                    for other in rectangles[i+1:]:
                        a,c,d,e = other
                        separation = math.hypot(max(l-d, a-r, 0), max(b-e, c-t, 0))
                        self.assertGreaterEqual(separation - .006, .1)
