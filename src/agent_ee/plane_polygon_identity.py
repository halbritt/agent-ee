"""Saved-fill identity: SHA256 of JSON integer-nanometre outline and hole chains."""
import hashlib
import json
import math


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _validate_chain(chain):
    if (not isinstance(chain, list) or len(chain) < 3 or
            any(not isinstance(p, list) or len(p) != 2 or
                any(type(c) is not int for c in p) for p in chain)):
        raise ValueError('native polygon needs a nonempty integer-nanometre chain')
    edges = list(zip(chain, chain[1:] + chain[:1]))
    if sum(a[0]*b[1]-a[1]*b[0] for a, b in edges) == 0:
        raise ValueError('native polygon has zero area')
    for i, (a, b) in enumerate(edges):
        if a == b:
            raise ValueError('native polygon has a zero-length edge')
        c = chain[(i+2) % len(chain)]
        if _cross(a, b, c) == 0 and (b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1]) < 0:
            raise ValueError('native polygon edge reverses')
        for j in range(i+2, len(edges)):
            if i == 0 and j == len(edges)-1:
                continue
            c, d = edges[j]
            if not all(max(min(a[k], b[k]), min(c[k], d[k])) <=
                       min(max(a[k], b[k]), max(c[k], d[k])) for k in (0, 1)):
                continue
            # Saved KiCad fills fracture holes into the outline with a bridge
            # traversed once in each direction. Preserve those exact nm bytes.
            if a == d and b == c:
                continue
            shared = next((p for p in (a, b) if p == c or p == d), None)
            if shared is not None:
                u = b if shared == a else a
                v = d if shared == c else c
                if (_cross(shared, u, v) == 0 and
                        (u[0]-shared[0])*(v[0]-shared[0])+(u[1]-shared[1])*(v[1]-shared[1]) > 0):
                    raise ValueError('native polygon edges overlap')
                continue
            if (_cross(a, b, c)*_cross(a, b, d) <= 0 and
                    _cross(c, d, a)*_cross(c, d, b) <= 0):
                raise ValueError('native polygon self-intersects')



def polygon_digest(chains_nm):
    """Keep native vertex/chain order and json.dumps defaults: the original wire identity."""
    if not isinstance(chains_nm, list) or not chains_nm:
        raise ValueError('native polygon has no outline')
    for chain in chains_nm:
        _validate_chain(chain)
    return hashlib.sha256(json.dumps(chains_nm).encode()).hexdigest()


def polygon_nm(outline_mm, holes_mm):
    """Recover KiCad integer coordinates without silently snapping off-grid evidence."""
    if not isinstance(holes_mm, list):
        raise ValueError('native polygon holes must be a list')
    chains = []
    for chain in [outline_mm, *holes_mm]:
        if not isinstance(chain, list):
            raise ValueError('native polygon chain must be a list')
        points = []
        for point in chain:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('native polygon vertex must be a coordinate pair')
            nm = []
            for coordinate in point:
                if type(coordinate) not in (int, float) or not -2148 <= coordinate <= 2148:
                    raise ValueError('native polygon coordinate must be finite and within KiCad range')
                scaled = coordinate * 1_000_000
                if not math.isfinite(scaled) or not -2**31 <= scaled <= 2**31-1:
                    raise ValueError('native polygon coordinate exceeds KiCad integer range')
                value = round(scaled)
                # ToMM/JSON binary-float round trips can lose sub-micro-nanometres.
                if not math.isclose(scaled, value, rel_tol=0, abs_tol=1e-6):
                    raise ValueError('native polygon coordinate is not on the nanometre grid')
                nm.append(value)
            points.append(nm)
        chains.append(points)
    return chains
