"""Geometric compilation checks in the captured footprint's local frame."""

import re
from decimal import Decimal

from .symbol_library import children


def connector_edge_gap(node, connector):
    """Inward gap from the rotated front courtyard to the declared edge, in mm."""
    points = []
    for graphic in node:
        if not isinstance(graphic, list) or not graphic:
            continue
        if not any(layer[1:] == ["F.CrtYd"] for layer in children(graphic, "layer")):
            continue
        if graphic[0] not in ("fp_line", "fp_rect"):
            raise ValueError(
                "connector needs a linear front courtyard to establish edge proximity"
            )
        for key in ("start", "end"):
            points.extend(
                (Decimal(p[1]), Decimal(p[2])) for p in children(graphic, key)
            )
    if not points:
        raise ValueError(
            "connector footprint needs a front courtyard to establish edge proximity"
        )
    cosine, sine = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[
        int(connector["rotation_deg"] % 360)
    ]
    rotated = [(x * cosine + y * sine, -x * sine + y * cosine) for x, y in points]
    ox, oy = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[
        connector["edge"]
    ]
    return float(
        Decimal(str(connector["inset_mm"])) - max(x * ox + y * oy for x, y in rotated)
    )


def passive_size_mismatch(part, package):
    """Compare only explicitly marked standard imperial chip sizes, without guessing IC codes."""
    native = re.search(
        r"(?:^|_)(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|$)",
        part["footprint"].split(":")[1],
    )
    supplier = re.fullmatch(
        r"(?:[RCL]_?)?(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:\([^)]*\))?",
        package.strip(),
        re.I,
    )
    return (
        (native[1], supplier[1])
        if native and supplier and native[1] != supplier[1]
        else None
    )
