"""AGENTEE-442 positive-c: recompute the router runtime actually selected in the CI image (no routing).

Records, for AGENT_EE_KICAD_ROUTE_CHECKOUT: the checkout commit (agent-ee's own reader), the
production pin, engine source / venv packages / Python / manifest drift (the checkout's own code,
via the v1 harness helper), the extension module digest, the manifest generation and fields, the
interpreter, and whether the selected cli accepts --experiment-policy (its own argparse, --help only;
the router never routes). Prints one JSON object.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import k91_native_positive as v1  # noqa: E402
from k91_native_positive import attempt  # noqa: E402

checkout = Path(os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'])
manifest = json.loads((checkout / 'manifest.json').read_text())
k = manifest['krt']
engine = v1.engine_identity(checkout)
python = checkout / '.venv-krt/bin/python'
ext = checkout / k['extension_module']['path']
helps = {}
for sub in ('route',):
    r = subprocess.run([str(checkout / 'bin/kicad-route'), sub, '--help'], capture_output=True, text=True,
                       timeout=120, env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'KICAD_ROUTE_STATE': str(checkout)})
    helps[sub] = {'exit': r.returncode, 'mentions_experiment_policy': '--experiment-policy' in r.stdout + r.stderr}
r = subprocess.run([str(checkout / 'bin/kicad-route'), 'route', '--experiment-policy', '/nonexistent', '--help'],
                   capture_output=True, text=True, timeout=120,
                   env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'KICAD_ROUTE_STATE': str(checkout)})
src = checkout / 'src/kicad_route'
print(json.dumps({
    'checkout': str(checkout), 'checkout_commit': attempt.checkout_commit(checkout), 'production_pin': attempt.PIN,
    'source_sha256': engine['source_sha256'], 'packages_sha256': engine['packages_sha256'],
    'python_version': engine['python_version'], 'manifest_drift': engine['drift'],
    'extension': {'path': k['extension_module']['path'], 'sha256': v1.sha256(ext),
                  'harness_expected_path': v1.EXTENSION, 'harness_path_sha256':
                  v1.sha256(checkout / v1.EXTENSION) if (checkout / v1.EXTENSION).is_file() else None},
    'manifest': {'sha256': v1.sha256(checkout / 'manifest.json'), 'state_dir': manifest.get('state_dir'),
                 'generation': k.get('generation'), 'commit': k.get('commit'), 'source_sha256': k.get('source_sha256'),
                 'extension_sha256': k['extension_module'].get('sha256'),
                 'packages_sha256': (k.get('venv') or {}).get('packages_sha256')},
    'interpreter': {'lexical': str(python), 'link': os.readlink(python) if python.is_symlink() else None,
                    'resolved': str(python.resolve())},
    'cli_help': helps,
    'cli_experiment_policy_flag': {'exit': r.returncode, 'stderr_tail': r.stderr.strip().splitlines()[-3:]},
    'producer_modules': {'experiment.py': (src / 'experiment.py').is_file(),
                         'experiment_policy.py': (src / 'experiment_policy.py').is_file()},
}, indent=2))
