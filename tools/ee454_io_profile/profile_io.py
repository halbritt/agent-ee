"""AGENTEE-454 profile harness: attribute logical reads of parts-manifest qualification.

Runs one agent-ee workload against a reconstructed capture root (see reconstruct.py)
in one of three modes:

  plain     no instrumentation: wall/CPU time and /proc/self/io rchar delta only
  count     harness-only byte counters on Path.read_bytes/read_text and
            storage._hash_stream, attributed to the agent_ee call chain
  cprofile  cProfile call counts and cumulative times

Wrappers are installed here only; src/agent_ee is imported unmodified and every
wrapped call still performs its original read. Diagnostic tooling, not src/.
"""
import argparse
import collections
import cProfile
import io
import json
import os
from pathlib import Path
import platform
import pstats
import resource
import subprocess
import sys
import time

WORKLOADS = ('closure', 'qualify')


def rchar():
    fields = dict(line.split(': ') for line in Path('/proc/self/io').read_text().splitlines())
    return int(fields['rchar']), int(fields['read_bytes'])


def chain(limit=12):
    """agent_ee frames, outermost first, as module.function names (harness frames dropped)."""
    frame, names = sys._getframe(2), []
    while frame is not None:
        module = frame.f_globals.get('__name__', '')
        if module.startswith('agent_ee.'):
            names.append(module[len('agent_ee.'):] + '.' + frame.f_code.co_name)
        frame = frame.f_back
    names.reverse()
    return ' > '.join(names[-limit:])


class Counter:
    def __init__(self, root):
        self.root = str(Path(root).resolve())
        self.by_chain = collections.defaultdict(lambda: [0, 0])
        self.by_path = collections.defaultdict(lambda: [0, 0])
        self.by_kind = collections.defaultdict(lambda: [0, 0])

    def rel(self, path):
        text = str(Path(path).resolve()) if not isinstance(path, int) else f'<fd {path}>'
        return text[len(self.root) + 1:] if text.startswith(self.root + '/') else text

    def add(self, path, size, primitive):
        rel = self.rel(path)
        kind = ('pdf' if rel.endswith('.pdf') else 'jlc-response' if '/responses/' in rel
                else 'json' if rel.endswith('.json') else 'other')
        for table, key in ((self.by_chain, primitive + ' @ ' + chain()), (self.by_path, rel),
                           (self.by_kind, kind)):
            table[key][0] += 1
            table[key][1] += size


def install(counter):
    from agent_ee import storage
    original_bytes, original_text = Path.read_bytes, Path.read_text
    original_hash = storage._hash_stream

    def read_bytes(self):
        data = original_bytes(self)
        counter.add(self, len(data), 'Path.read_bytes')
        return data

    def read_text(self, *args, **kwargs):
        data = original_text(self, *args, **kwargs)
        counter.add(self, len(data.encode('utf-8', 'surrogateescape')), 'Path.read_text')
        return data

    def hash_stream(stream, checkpoint):
        value = original_hash(stream, checkpoint)
        name = getattr(stream, 'name', None)
        counter.add(name if name is not None else '<stream>', os.fstat(stream.fileno()).st_size,
                    'storage._hash_stream')
        return value

    Path.read_bytes, Path.read_text = read_bytes, read_text
    storage._hash_stream = hash_stream


def workload(name, root, manifest):
    from agent_ee import parts_manifest
    document = parts_manifest.load_manifest(root, manifest)
    if name == 'closure':
        return len(parts_manifest.dependency_closure(document, root))
    result = parts_manifest.qualify_manifest(document, root)
    return sorted(result) if isinstance(result, dict) else type(result).__name__


def identities(root):
    source = Path(__file__).resolve().parents[2]
    git = lambda *a: subprocess.run(['git', '-C', str(source), *a], capture_output=True, text=True).stdout.strip()
    kicad = subprocess.run(['kicad-cli', '--version'], capture_output=True, text=True).stdout.strip()
    return dict(agent_ee_head=git('rev-parse', 'HEAD'), agent_ee_src_dirty=bool(git('status', '--porcelain', '--', 'src')),
                python=sys.version.split()[0], kicad_cli=kicad, platform=platform.platform(),
                host=platform.node(), root=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root')
    parser.add_argument('--manifest', default='parts.json')
    parser.add_argument('--workload', choices=WORKLOADS, default='closure')
    parser.add_argument('--mode', choices=('plain', 'count', 'cprofile'), default='plain')
    parser.add_argument('--out', required=True)
    parser.add_argument('--top', type=int, default=40)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    counter = None
    if args.mode == 'count':
        counter = Counter(root)
        install(counter)
    profiler = cProfile.Profile() if args.mode == 'cprofile' else None
    import agent_ee.parts_manifest  # noqa: F401  (import cost outside the measured window)
    before_io, before_wall, before_cpu = rchar(), time.perf_counter(), time.process_time()
    error = None
    try:
        if profiler is not None:
            profiler.enable()
        outcome = workload(args.workload, root, args.manifest)
    except Exception as exc:  # recorded, never relabelled as success
        outcome, error = None, f'{type(exc).__name__}: {exc}'
    finally:
        if profiler is not None:
            profiler.disable()
    after_io, wall, cpu = rchar(), time.perf_counter() - before_wall, time.process_time() - before_cpu
    record = dict(schema='agent-ee.ee454-io-profile/1', workload=args.workload, mode=args.mode,
                  manifest=args.manifest, outcome=outcome, error=error, wall_s=round(wall, 3),
                  cpu_s=round(cpu, 3), rchar_bytes=after_io[0] - before_io[0],
                  physical_read_bytes=after_io[1] - before_io[1],
                  maxrss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, runtime=identities(root))
    if counter is not None:
        def table(rows, top):
            ordered = sorted(rows.items(), key=lambda kv: -kv[1][1])
            return [dict(key=k, calls=c, bytes=b) for k, (c, b) in ordered[:top]]
        attributed = sum(b for _, b in counter.by_kind.values())
        record.update(attributed_bytes=attributed, by_kind=table(counter.by_kind, 10),
                      by_chain=table(counter.by_chain, args.top), by_path=table(counter.by_path, args.top),
                      distinct_paths=len(counter.by_path),
                      distinct_path_bytes=sum(os.path.getsize(root / p) for p in counter.by_path
                                              if (root / p).is_file()))
    if profiler is not None:
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        rows = []
        for (filename, line, func), (cc, nc, tt, ct, _) in stats.stats.items():
            rows.append(dict(function=f'{Path(filename).name}:{line}:{func}', primitive_calls=cc,
                             calls=nc, tottime=round(tt, 4), cumtime=round(ct, 4)))
        record['cumulative'] = sorted(rows, key=lambda r: -r['cumtime'])[:args.top]
        record['by_calls'] = sorted(rows, key=lambda r: -r['calls'])[:args.top]
    Path(args.out).write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: record[k] for k in ('workload', 'mode', 'error', 'wall_s', 'cpu_s', 'rchar_bytes',
                                             'physical_read_bytes')}))
    return 1 if error else 0


if __name__ == '__main__':
    sys.exit(main())
