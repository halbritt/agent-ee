"""Original native boards for antenna_placement tests (AGENTEE-453).

A plain four-layer rectangular board with the stock KiCad 10.0.7
ESP32-S3-WROOM-1 footprint captured at runtime (pinned sha256, never
committed). Options move, rotate or flip U1, rotate one of its pads, drop or loosen its
module-owned keepout, or add copper inside it. The vendor PDFs are not available to the
tests: synthetic stand-in bytes are pinned by patching the reviewed hashes,
so the guidance and datasheet *contents* are not exercised here.
"""
from agent_ee.footprint_library import ESP32_FOOTPRINT_ID, ESP32_FOOTPRINT_PATH

WIDTH, HEIGHT = 60.0, 40.0
TOP = dict(start_mm=[0, 0], end_mm=[60, 0])
RIGHT = dict(start_mm=[60, 0], end_mm=[60, 40])
# Feed side (+x) 2 mm from the right corner; antenna/body boundary on the top edge.
GOOD = dict(anchor=(49.0, 6.75), rotation=0)
GUIDANCE = 'sources/esp32-guidelines.pdf'
DATASHEET = 'sources/esp32-s3-wroom-1-datasheet.pdf'
FOOTPRINT = 'cad/' + ESP32_FOOTPRINT_PATH
GUIDANCE_BYTES = b'Owned synthetic stand-in for the Espressif guidelines snapshot.\n'
DATASHEET_BYTES = b'Owned synthetic stand-in for the module datasheet snapshot.\n'


def _mm(value):
    return round(value * 1e6)


def make_board(path, footprint_dir, *, anchor=GOOD['anchor'], rotation=GOOD['rotation'], flipped=False,
               drop_zone=False, loosen_zone=False, intrusion=False, reference='U1', outline=(WIDTH, HEIGHT),
               split_top=None, rotate_pad=None):
    import pcbnew as k
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    w, h = outline
    # split_top draws the top edge as two collinear Edge.Cuts segments meeting at (split_top, 0).
    corners = [(0, 0), *([(split_top, 0)] if split_top else []), (w, 0), (w, h), (0, h)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        seg = k.PCB_SHAPE(board)
        seg.SetShape(k.SHAPE_T_SEGMENT)
        seg.SetStart(k.VECTOR2I(_mm(a[0]), _mm(a[1])))
        seg.SetEnd(k.VECTOR2I(_mm(b[0]), _mm(b[1])))
        seg.SetLayer(k.Edge_Cuts)
        seg.SetWidth(_mm(0.05))
        board.Add(seg)
    fp = k.FootprintLoad(str(footprint_dir), 'ESP32-S3-WROOM-1')
    lib, name = ESP32_FOOTPRINT_ID.split(':')
    fp.SetFPID(k.LIB_ID(lib, name))
    fp.SetReference(reference)
    board.Add(fp)
    fp.SetPosition(k.VECTOR2I(_mm(anchor[0]), _mm(anchor[1])))
    fp.SetOrientationDegrees(rotation)
    if flipped:
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
    if rotate_pad:
        # One pad turned 90 degrees in place: same canonical record, different copper.
        pad = next(p for p in fp.Pads() if p.GetNumber() == rotate_pad)
        pad.SetOrientationDegrees(pad.GetOrientationDegrees() + 90)
    for zone in list(fp.Zones()):
        if drop_zone:
            fp.Remove(zone)
        elif loosen_zone:
            zone.SetDoNotAllowTracks(False)
    if intrusion:
        # A track through the antenna keepout, beyond the top edge above the antenna.
        track = k.PCB_TRACK(board)
        track.SetStart(k.VECTOR2I(_mm(anchor[0] - 3), _mm(-3)))
        track.SetEnd(k.VECTOR2I(_mm(anchor[0] + 3), _mm(-3)))
        track.SetLayer(k.F_Cu)
        track.SetWidth(_mm(0.2))
        board.Add(track)
    board.Save(str(path))


def declaration(*, edge=TOP, guidance=GUIDANCE, policy_case='outside_board', reference='U1', corner_policy=True):
    from agent_ee import footprint_library as fl
    from agent_ee.antenna_placement import CORNER_POLICY
    return dict(schema='agent-ee.antenna-placement-declaration/1', reference=reference,
                policy=fl.esp32_s3_wroom_1_placement_policy(policy_case, pdf_path=guidance),
                module_datasheet=dict(path=DATASHEET, sha256=fl.ESP32_DATASHEET_SHA256),
                footprint=dict(path=FOOTPRINT, sha256=fl.ESP32_FOOTPRINT_SHA256), edge=edge,
                corner_policy=dict(CORNER_POLICY) if corner_policy else None)
