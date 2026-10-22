"""Explicit opt-in Rust placement scoring, with an exact Python verification mode.

The prepared scorer owns its input and is valid for one placement generation.
Loading/building is explicit; normal placement remains the Python implementation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
from dataclasses import dataclass
from pathlib import Path
import subprocess
import weakref

ROTATIONS = (0, 90, 180, 270)
LIMIT = (1 << 63) - 1
_ACTIVE = ContextVar('placement_score_experiment', default=None)


@dataclass(frozen=True)
class Snapshot:
    generation: object
    pads: tuple
    peers: tuple

    @classmethod
    def capture(cls, generation, pads, peers):
        if set(pads) != set(map(str, ROTATIONS)):
            raise ValueError('four quarter-turn pad sets required')
        frozen = tuple(tuple(tuple(p) for p in pads[str(r)]) for r in ROTATIONS)
        nets = {net for rotation in frozen for net, _, _ in rotation}
        frozen_peers = tuple((net, tuple(tuple(p) for p in peers[net])) for net in sorted(nets & peers.keys()))
        if any(len(point) != 2 for _, points in frozen_peers for point in points):
            raise ValueError('peer points require exactly two coordinates')
        return cls(generation, frozen, frozen_peers)


def reference(snapshot, poses):
    from .placer import _wire_cost
    peers = dict(snapshot.peers)
    return [_wire_cost([(net, ox+x, oy+y) for net, ox, oy in snapshot.pads[r // 90]], peers)
            for x, y, r in poses]


def _integer(value):
    if type(value) is not int or not -LIMIT-1 <= value <= LIMIT:
        raise ValueError('coordinate outside signed int64')
    return value


def _array(values):
    values = [_integer(v) for v in values]
    return (ctypes.c_int64 * len(values))(*values)


def _pose(x, y, rotation):
    if type(x) is not int or type(y) is not int or type(rotation) is not int or rotation not in ROTATIONS:
        raise ValueError('integer poses at supported quarter turns required')


class _Prepared:
    def __init__(self, native, handle):
        self.native = native
        self.handle = handle
        # Readers hold this object until their FFI call returns. No explicit
        # close can race a batch running with the GIL released.
        self._release = weakref.finalize(self, native.free, handle)


class Native:
    """Owned ABI inputs; scalar calls avoid allocation and releasing the GIL.

    A scalar kernel is short: releasing/reacquiring the GIL can cost more than
    scoring itself. Batches and preparation use CDLL and release it normally.
    """
    def __init__(self, path):
        path = str(Path(path).resolve())
        self.library = ctypes.CDLL(path)
        self.scalar_library = ctypes.PyDLL(path)
        integer, pointer = ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)
        abi = self.library.agent_ee_score_abi_v1
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise ValueError('unsupported placement scorer ABI')
        self.prepare_function = self.library.agent_ee_score_prepare_v1
        self.prepare_function.argtypes = [pointer, pointer, integer, pointer, integer, pointer]
        self.prepare_function.restype = ctypes.c_void_p
        self.one_function = self.scalar_library.agent_ee_score_one_v1
        self.one_function.argtypes = [ctypes.c_void_p, integer, integer, integer]
        self.one_function.restype = integer
        self.batch_function = self.library.agent_ee_score_batch_v1
        self.batch_function.argtypes = [ctypes.c_void_p, pointer, integer, pointer]
        self.batch_function.restype = ctypes.c_int32
        self.free = self.library.agent_ee_score_free_v1
        self.free.argtypes, self.free.restype = [ctypes.c_void_p], None

    def prepare(self, snapshot):
        if len(snapshot.pads) != 4:
            raise ValueError('four quarter-turn pad sets required')
        peers = dict(snapshot.peers)
        offsets, points, starts, rotations = [], [], [0], [0]
        for pads in snapshot.pads:
            for net, x, y in pads:
                if net not in peers:
                    continue
                if not peers[net]:
                    raise ValueError('empty peer span')
                offsets.extend((x, y))
                points.extend(v for px, py in peers[net] for v in (px, py))
                starts.append(len(points) // 2)
            rotations.append(len(starts) - 1)
        if len(rotations) != 5:
            raise ValueError('four quarter-turn pad sets required')
        handle = self.prepare_function(_array(offsets), _array(starts), len(starts)-1,
                                       _array(points), len(points)//2, _array(rotations))
        if not handle:
            raise ValueError('Rust scorer refused prepared inputs')
        return _Prepared(self, handle)

    def one(self, packed, x, y, rotation):
        _pose(x, y, rotation)
        score = self.one_function(packed.handle, _integer(x), _integer(y), rotation)
        if score < 0:
            raise OverflowError('Rust score exceeds signed int64')
        return score

    def score(self, packed, poses):
        xy = _array(v for x, y, rotation in poses for v in (x, y, rotation))
        count = len(xy) // 3
        scores = (ctypes.c_int64 * count)()
        status = self.batch_function(packed.handle, xy, count, scores)
        if status:
            raise OverflowError(f'Rust batch refused ({status}); score exceeds signed int64 or invalid pose')
        return list(scores)


def build(destination, compiler='rustc', *, native_cpu=False):
    """Explicit dependency-free build; host CPU instructions are opt-in."""
    destination = Path(destination).resolve()
    command = [compiler, '--edition=2021', '--crate-type=cdylib', '-C', 'opt-level=3',
               '-C', 'lto=fat', '-C', 'codegen-units=1', '-C', 'panic=abort']
    if native_cpu:
        command += ['-C', 'target-cpu=native']
    subprocess.run([*command, str(Path(__file__).parent / 'data/placement_score.rs'),
                    '-o', str(destination)], check=True)
    return destination


class Session:
    def __init__(self, library, *, check=False):
        self.events, self.native, self.check = [], None, check
        try:
            self.native = Native(library)
        except (OSError, AttributeError, ValueError) as exc:
            self.rollback(f'optional library unavailable: {exc}')

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
        if generation != self.snapshot.generation:
            raise ValueError('stale placement scoring generation')
        _pose(x, y, rotation)
        native = self.session.native
        if native is None:
            return reference(self.snapshot, [(x, y, rotation)])[0]
        try:
            actual = native.one(self.packed, x, y, rotation)
        except (ValueError, OverflowError, OSError) as exc:
            self.session.rollback(f'Rust score refused: {exc}')
            return reference(self.snapshot, [(x, y, rotation)])[0]
        if self.session.check:
            expected = reference(self.snapshot, [(x, y, rotation)])[0]
            if actual != expected:
                self.session.rollback('exact score mismatch; entire compiled batch discarded')
                return expected
        return actual

    def score_batch(self, poses, *, generation):
        if generation != self.snapshot.generation:
            raise ValueError('stale placement scoring generation')
        poses = tuple(tuple(p) for p in poses)
        for pose in poses:
            _pose(*pose)
        native = self.session.native
        if native is None:
            return reference(self.snapshot, poses)
        try:
            actual = native.score(self.packed, poses)
        except (ValueError, OverflowError, OSError) as exc:
            self.session.rollback(f'Rust batch refused: {exc}')
            return reference(self.snapshot, poses)
        if self.session.check:
            expected = reference(self.snapshot, poses)
            if actual != expected:
                self.session.rollback('exact score mismatch; entire compiled batch discarded')
                return expected
        return actual


def active():
    return _ACTIVE.get()


@contextmanager
def experimental(library, *, check=False):
    """Opt in around pack(); check=True also runs the full Python oracle."""
    session = Session(library, check=check)
    token = _ACTIVE.set(session)
    try:
        yield session
    finally:
        _ACTIVE.reset(token)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Explicit offline Rust placement scorer build')
    parser.add_argument('destination', type=Path)
    parser.add_argument('--compiler', default='rustc')
    parser.add_argument('--native-cpu', action='store_true')
    args = parser.parse_args()
    print(build(args.destination, args.compiler, native_cpu=args.native_cpu))
