"""Deterministic producer policy; native DRC still grades every saved marking."""
import re

# Exact standard metric chip identities, not reference prefixes or size guesses.
DENSE_PASSIVE = re.compile(r'(?:Resistor_SMD:R|Capacitor_SMD:C|Inductor_SMD:L)_(?:0402_1005|0603_1608)Metric\Z')


def prepare_footprint(fp, *, overhang=False):
    """Keep dense reference labels and overhanging graphics on fabrication layers."""
    import pcbnew as k
    if DENSE_PASSIVE.fullmatch(fp.GetFPIDAsString()):
        reference = fp.Reference()
        if reference.GetLayer() in (k.F_SilkS, k.B_SilkS):
            reference.SetLayer(k.B_Fab if reference.GetLayer() == k.B_SilkS else k.F_Fab)
    if overhang:
        for item in [*fp.GraphicalItems(), *fp.GetFields()]:
            if item.GetLayer() in (k.F_SilkS, k.B_SilkS):
                item.SetLayer(k.B_Fab if item.GetLayer() == k.B_SilkS else k.F_Fab)


def reserve_imu_markings(board, policy):
    """Reserve each visible board IMU label's native bounding box plus 0.2 mm.

    The effective packing policy retains the UUID and exact reservation geometry.
    The board itself and the captured policy are not modified.
    """
    import pcbnew as k
    # Leave malformed policies to pack's existing fail-closed validation.
    if not isinstance(policy, dict) or not isinstance(policy.get('obstacles'), list):
        return policy
    obstacles = list(policy['obstacles'])
    for item in sorted(board.GetDrawings(), key=lambda x: x.m_Uuid.AsString()):
        if (item.GetClass() != 'PCB_TEXT' or not item.IsVisible() or
                item.GetText().split()[:1] != ['IMU_AXES'] or
                item.GetLayer() not in (k.F_SilkS, k.B_SilkS)):
            continue
        box = item.GetBoundingBox()
        margin = 200000
        x0, y0 = box.GetLeft() - margin, box.GetTop() - margin
        x1, y1 = box.GetRight() + margin, box.GetBottom() + margin
        obstacles.append(dict(side='F' if item.GetLayer() == k.F_SilkS else 'B',
            polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
            source='IMU_AXES/' + item.m_Uuid.AsString()))
    return {**policy, 'obstacles': obstacles}
