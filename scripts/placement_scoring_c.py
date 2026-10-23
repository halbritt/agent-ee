"""Benchmark-only adapter for the unchanged AGENTEE-431 C kernel.

Packing, rotation grouping, CDLL calls and scalar single-pose batching retain the
original behavior. The Python oracle is optional so timed fast calls do not also
run Python scoring. This helper never enables a production backend by default.
"""
from contextlib import contextmanager
import ctypes
import hashlib
from pathlib import Path
import subprocess

from agent_ee import placement_scoring as scoring

ORIGINAL_COMMIT = '50e586818acf6265039a3509acc910eb0158f357'
SOURCE = Path(__file__).parent / 'data' / 'placement_score_original.c'
SOURCE_SHA256 = '1828e54cc2b44e4187a23f48ecf19a0b4bd3b61b1ad31969535ff86dec04248c'
ROTATIONS = scoring.ROTATIONS
Snapshot = scoring.Snapshot
reference = scoring.reference


class Native:
    def __init__(self, path):
        self.library = ctypes.CDLL(str(Path(path).resolve()))
        self.function = self.library.agent_ee_score_v1
        pointer = ctypes.POINTER(ctypes.c_int64)
        self.function.argtypes = [pointer, pointer, pointer, ctypes.c_int64,
                                  pointer, ctypes.c_int64, pointer]
        self.function.restype = ctypes.c_int

    def prepare(self, snapshot):
        if len(snapshot.pads) != 4:
            raise ValueError('four quarter-turn pad sets required')
        peers, packed = dict(snapshot.peers), []
        for pads in snapshot.pads:
            offsets, points, starts = [], [], [0]
            for net, x, y in pads:
                if net not in peers:
                    continue
                if not peers[net]:
                    raise ValueError('empty peer span')
                offsets.extend((x, y))
                # Exact pairs prevent a malformed row from crossing the C ABI.
                points.extend(v for px, py in peers[net] for v in (px, py))
                starts.append(len(points) // 2)
            packed.append((scoring._array(offsets), scoring._array(points),
                           scoring._array(starts), len(starts)-1))
        return tuple(packed)

    def score(self, packed, poses):
        poses = tuple(tuple(pose) for pose in poses)
        for x, y, rotation in poses:
            scoring._pose(x, y, rotation)
        out = [None] * len(poses)
        for i, rotation in enumerate(ROTATIONS):
            rows = [(j, x, y) for j, (x, y, r) in enumerate(poses) if r == rotation]
            if not rows:
                continue
            xy = scoring._array(v for _, x, y in rows for v in (x, y))
            scores = (ctypes.c_int64 * len(rows))()
            offsets, peers, starts, pads = packed[i]
            if self.function(offsets, peers, starts, pads, xy, len(rows), scores):
                raise OverflowError('C score exceeds signed int64; batch discarded')
            for (j, _, _), score in zip(rows, scores):
                out[j] = score
        return out

    def one(self, packed, x, y, rotation):
        return self.score(packed, [(x, y, rotation)])[0]


def build(destination, compiler='cc'):
    """Use the original compiler flags and refuse a changed historical source."""
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('historical C source hash mismatch')
    destination = Path(destination).resolve()
    subprocess.run([compiler, '-O3', '-std=c11', '-shared', '-fPIC',
                    str(SOURCE), '-o', str(destination)], check=True)
    return destination


class Session:
    def __init__(self, library, *, check=False):
        self.events, self.native, self.check = [], None, check
        try:
            self.native = Native(library)
        except (OSError, AttributeError, ValueError) as exc:
            self.rollback(f'optional C library unavailable: {exc}')

    def rollback(self, reason):
        self.native = None
        self.events.append({'backend': 'reference', 'reason': reason})

    def scorer(self, snapshot):
        return Scorer(self, snapshot)


class Scorer:
    def __init__(self, session, snapshot):
        self.session, self.snapshot, self.packed = session, snapshot, None
        if session.native is not None:
            try:
                self.packed = session.native.prepare(snapshot)
            except ValueError as exc:
                session.rollback(str(exc))

    def score(self, x, y, rotation, *, generation):
        return self.score_batch([(x, y, rotation)], generation=generation)[0]

    def score_batch(self, poses, *, generation):
        if generation != self.snapshot.generation:
            raise ValueError('stale placement scoring generation')
        poses = tuple(tuple(pose) for pose in poses)
        for x, y, rotation in poses:
            scoring._pose(x, y, rotation)
        native = self.session.native
        if native is None:
            return reference(self.snapshot, poses)
        try:
            actual = native.score(self.packed, poses)
        except (ValueError, OverflowError, OSError) as exc:
            self.session.rollback(f'C batch refused: {exc}')
            return reference(self.snapshot, poses)
        if self.session.check:
            expected = reference(self.snapshot, poses)
            if actual != expected:
                self.session.rollback('exact score mismatch; entire compiled batch discarded')
                return expected
        return actual


@contextmanager
def experimental(library, *, check=False):
    """Use C only for this context; callers must fail their benchmark on events."""
    session = Session(library, check=check)
    token = scoring._ACTIVE.set(session)
    try:
        yield session
    finally:
        scoring._ACTIVE.reset(token)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Build the unchanged historical C benchmark kernel')
    parser.add_argument('destination', type=Path)
    parser.add_argument('--compiler', default='cc')
    args = parser.parse_args()
    print(build(args.destination, args.compiler))
