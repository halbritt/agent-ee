"""AGENTEE-442 positive-c bd1: verify the copied agent-507 packet (read-only hashing; nothing runs).

    verify_packet.py PACKET [ORIGINAL]

Checks runtime-L's 313 code-map files on disk and as committed blobs, the canonical
code-map digest, all 4293 .venv-krt entries (bytes/sha256 or symlink target, and no extra
entries), the private manifest digest, git HEAD/cleanliness/no alternates, and, with
ORIGINAL, that every path in the copy is the same file type, mode, target and bytes.
Prints one JSON object; exit 0 only when every check holds.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

packet = Path(sys.argv[1])
L = packet / 'runtime-L'
want = {'commit': 'bd1e633f2e585f4fc931ceab001299b78d0d8bb7', 'code_entries': 313, 'venv_entries': 4293,
        'code_canonical': '63939cc4e806e458c2afe5ce7935c49490b15dbf5ae6f1a9c846eb4ab2d331b1',
        'manifest': '33cd344e95733941590ffc6cc63e6002ca638077450c0568d3a6ae30f82a3212',
        'venv_files_json': '48df7e0789aa54eaa278e0768bd2eee9aae11ac82d4c709c1939724964408320'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


SCRATCH_INDEX = Path(os.environ.get('TMPDIR', '/tmp')) / 'verify-packet-index'


def git(*args):
    # status refreshes the index stat cache; it writes a scratch copy, never runtime-L/.git/index.
    SCRATCH_INDEX.write_bytes((L / '.git/index').read_bytes())
    return subprocess.run(['git', '-C', str(L), *args], capture_output=True, check=True,
                          env={**os.environ, 'GIT_INDEX_FILE': str(SCRATCH_INDEX)}).stdout


out, problems = {}, []
code = json.loads((packet / 'code-map.json').read_text())
out['code_entries'] = len(code)
canon = {'sort_keys_default': hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest(),
         'sort_keys_compact': hashlib.sha256(json.dumps(code, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
out['code_canonical'] = canon
if want['code_canonical'] not in canon.values():
    problems.append('code map canonical digest not reproduced')
disk_bad = [p for p, h in code.items() if not (L / p).is_file() or (L / p).is_symlink() or sha(L / p) != h]
out['code_disk_mismatch'] = disk_bad
head = git('rev-parse', 'HEAD').decode().strip()
out['head'] = head
listing = git('ls-tree', '-r', '-z', head, '--', 'bin', 'src').split(b'\0')
blobs = {}
for line in filter(None, listing):
    meta, path = line.split(b'\t', 1)
    blobs[path.decode()] = hashlib.sha256(git('cat-file', 'blob', meta.split()[2].decode())).hexdigest()
out['code_blob_mismatch'] = sorted(set(blobs.items()) ^ set(code.items()))[:10]
out['git_status_porcelain'] = git('status', '--porcelain', '--untracked-files=no').decode().splitlines()
out['alternates'] = (L / '.git/objects/info/alternates').is_file()
out['git_is_dir'] = (L / '.git').is_dir()
venv = json.loads((packet / 'venv-files.json').read_text())
out['venv_entries'] = len(venv)
out['venv_files_json_sha256'] = sha(packet / 'venv-files.json')
found = {}
for root, dirs, files in os.walk(L / '.venv-krt'):
    for name in dirs + files:
        p = Path(root) / name
        rel = str(p.relative_to(L))
        if p.is_symlink():
            found[rel] = {'symlink': os.readlink(p)}
        elif p.is_file():
            found[rel] = {'bytes': p.stat().st_size, 'sha256': sha(p)}
out['venv_found'] = len(found)
out['venv_mismatch'] = sorted(k for k in set(venv) | set(found) if venv.get(k) != found.get(k))[:10]
out['venv_symlinks'] = {k: v['symlink'] for k, v in found.items() if 'symlink' in v}
out['manifest_sha256'] = sha(L / 'manifest.json')
m = json.loads((L / 'manifest.json').read_text())
out['manifest'] = {'state_dir': m.get('state_dir'), 'generation': m['krt'].get('generation'),
                   'commit': m['krt'].get('commit'), 'source_sha256': m['krt'].get('source_sha256'),
                   'extension': m['krt']['extension_module'], 'packages_sha256': m['krt']['venv'].get('packages_sha256')}
out['producer'] = {'experiment.py': sha(L / 'src/kicad_route/experiment.py') if (L / 'src/kicad_route/experiment.py').is_file() else None,
                   'cli_mentions_experiment_policy': b'--experiment-policy' in (L / 'src/kicad_route/cli.py').read_bytes()}
if len(sys.argv) > 2:
    orig, diffs, n = Path(sys.argv[2]), [], 0
    def walk(top):
        rec = {}
        for root, dirs, files in os.walk(top):
            for name in dirs + files:
                p = Path(root) / name
                st = p.lstat()
                rel = str(p.relative_to(top))
                if p.is_symlink():
                    rec[rel] = ('l', os.readlink(p))
                elif p.is_dir():
                    rec[rel] = ('d', st.st_mode)
                else:
                    rec[rel] = ('f', st.st_mode, sha(p))
        return rec
    a, b = walk(orig), walk(packet)
    out['copy_entries'] = {'original': len(a), 'copy': len(b)}
    out['copy_mismatch'] = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))[:10]
    if out['copy_mismatch']:
        problems.append('copy differs from the original packet')
for key, cond in (('commit', head == want['commit']), ('code_entries', len(code) == want['code_entries']),
                  ('code_disk', not disk_bad), ('code_blobs', not out['code_blob_mismatch']),
                  ('git_clean', not out['git_status_porcelain']), ('no_alternates', not out['alternates']),
                  ('venv_entries', len(venv) == want['venv_entries'] == len(found)), ('venv_exact', not out['venv_mismatch']),
                  ('venv_files_json', out['venv_files_json_sha256'] == want['venv_files_json']),
                  ('manifest', out['manifest_sha256'] == want['manifest']),
                  ('producer', out['producer']['experiment.py'] is not None and out['producer']['cli_mentions_experiment_policy'])):
    if not cond:
        problems.append(key)
out['problems'] = problems
print(json.dumps(out, indent=2))
sys.exit(1 if problems else 0)
