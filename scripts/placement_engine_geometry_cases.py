"""Original geometry fixtures for the complete native planner parity corpus.

This module only constructs inputs. The parity harness owns oracle execution,
native execution, limits, comparison and retained evidence.
"""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_placement import capture, part, polygon, rectangle
from test_placer import request
from test_placer_copper_extent import board, custom_pad, offset_part
from test_placer_packing_scope import area, lands_part
from test_access_direction import socket, reviewer_relaxing


def cases():
    """Yield (name, captured facts, complete request, optional native XML)."""
    def case(name, facts, **policy):
        return ("geometry-" + name, facts, {"policy": request(fixed_refs=[], **policy)}, None)

    concave = polygon([(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)])
    yield case("concave-notch", capture([part("U1", 0, 0, shape=concave), part("C1", 4, 4)]), priority_refs=["U1"])
    for side in ("F", "B"):
        yield case("custom-copper-" + side, board([offset_part(side)]))
    yield case("stock-copper", board([lands_part()]))
    pads = board([lands_part()], rectangle(0, 0, 4, 20))
    pads["rule_areas"] = [area("pads", ["F.Cu"], rectangle(1, 0, 2, 20))]
    yield case("pad-keepout-lane", pads)
    for layers, suffix in ((["In1.Cu"], "inner"), (["*.Cu"], "all")):
        facts = board([lands_part(layers=["F.Cu", "In1.Cu", "B.Cu"])])
        facts["rule_areas"] = [area("pads", layers, rectangle(0, 0, 20, 20))]
        yield case("pad-keepout-" + suffix, facts)
    yield case("access-sourced", capture([socket(width=20), part("U1", 8, 8)]))
    relaxing = socket(width=20)
    relaxing["access_direction"] = reviewer_relaxing(relaxing["access_direction"])
    relaxing["courtyards"]["B"]["polygons"] = [rectangle(7, 7, 32, 20)]
    yield case("access-relaxing", capture([relaxing]), capacity_proof=True)

    original = board([lands_part(x=10, y=10)])
    mutations = {
        "bad-version": lambda f: f.update(pcbnew_version="9.0.7"),
        "bad-max-error": lambda f: f.update(max_error_nm="bad"),
        "negative-max-error": lambda f: f.update(max_error_nm=-1),
        "bad-footprint-rotation": lambda f: f["footprints"][0].update(rotation_deg=45),
        "bad-pad-rotation": lambda f: f["footprints"][0]["pads"][0].update(rotation_deg=45),
        "bad-pad-shape": lambda f: f["footprints"][0]["pads"][0].update(shape=3),
        "bad-pad-layers": lambda f: f["footprints"][0]["pads"][0].update(layers=[5]),
        "bad-pad-centroid": lambda f: f["footprints"][0]["pads"][0].update(copper_centroid_offset_nm=[[0, 0], [0, 1]]),
        "bad-pad-size": lambda f: f["footprints"][0]["pads"][0].update(size_nm=[-1, 5]),
        "missing-courtyard": lambda f: f["footprints"][0].update(courtyards={}),
        "unproven-courtyard": lambda f: f["footprints"][0]["courtyards"]["F"].update(status="missing", reason="fixture missing courtyard"),
        "bad-courtyard-point": lambda f: f["footprints"][0]["courtyards"]["F"]["polygons"][0].__setitem__(0, [1.5, 0]),
        "missing-curve-tolerance": lambda f: f["footprints"][0]["courtyards"]["F"].update(inner_polygons=[rectangle(9, 9, 2, 2)]),
        "unproven-native-reservation": lambda f: f["footprints"][0].update(packing_pad_geometry={"status": "unknown", "reason": "fixture native geometry"}),
        "missing-native-copper": lambda f: f["footprints"][0].update(packing_pad_geometry={"status": "proven", "holes": []}),
        "missing-drill-reservation": lambda f: f["footprints"][0]["pads"][0].update(attribute="PTH"),
        "noninteger-drill-truthiness": lambda f: f["footprints"][0]["pads"][0].update(drill_size_nm=[0.5, 0]),
        "malformed-drill-truthiness": lambda f: f["footprints"][0]["pads"][0].update(drill_size_nm=["captured-malformed", 0]),
        "string-drill-truthiness": lambda f: f["footprints"][0]["pads"][0].update(drill_size_nm="captured-malformed"),
        "object-drill-truthiness": lambda f: f["footprints"][0]["pads"][0].update(drill_size_nm={"captured-malformed": 0}),
    }
    for name, change in mutations.items():
        facts = deepcopy(original)
        change(facts)
        yield case(name, facts)
    for value, name in ((None, "null"), (1, "number"), ("yes", "string")):
        facts = deepcopy(original)
        facts["rule_areas"] = [area("pads", ["F.Cu"], rectangle(0, 0, 3, 3), pads=value)]
        yield case("malformed-pad-flag-" + name, facts)
    facts = deepcopy(original)
    facts["rule_areas"] = [area("pads", ["F.Cu"], rectangle(0, 0, 3, 3))]
    del facts["rule_areas"][0]["forbid"]["pads"]
    yield case("missing-pad-flag", facts)
    facts = board([offset_part(pads=[custom_pad()])])
    facts["footprints"][0]["pads"][0]["custom_geometry"]["bbox_nm"] = [0, 0, 0, 5]
    yield case("degenerate-custom-box", facts)
