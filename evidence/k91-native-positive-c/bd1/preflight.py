"""AGENTEE-442 positive-c bd1 PREFLIGHT, run inside the selected ae-ci-exec container. It never routes.

    preflight.py PACKET OUT

PACKET is the copied agent-507 packet (runtime-L, frozen-router). Every item is compared with the
dispatched value; the output is one JSON object (OUT/preflight.json and stdout); exit 0 only when every
item is exact. Items: selected-container runtime identities by the v1/v2 harness's own readers (commit,
engine source in L and in the frozen router, extension, venv packages, pyvenv Python, manifest drift and
generation), the container's actual interpreter and its import of the extension (ABI), KiCad, the
producer (experiment.py digest, --experiment-policy in `kicad-route run --help`), the production
preflight of L under the bd1 pin, the production campaign binding (L mounted read-only at /opt/kicad-route,
the frozen input and policy), the fixed input hashes, and the mismatch runtime M's own identity.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import k91_consumer as consumer  # noqa: E402
import k91_native_positive as v1  # noqa: E402
import k91_native_positive_v2 as v2  # noqa: E402
from k91_native_positive import attempt, routing_adapter  # noqa: E402

EXPECT = {'commit': 'bd1e633f2e585f4fc931ceab001299b78d0d8bb7',
          'source_sha256': 'cb003b30f75f7b97859dcaaae6097ff1cbdeca00e7e38c2b1ab4b4a11e4790c2',
          'extension_sha256': '610b3acdfa0b5a2ee95fedd2a3526ff23b457c115c2f93bbac9fedf2ffcd99d8',
          'packages_sha256': '0dc7c913210ed2d7f0afe996b4f936eaee539c19ca5e6e5d76949d13eaf304ae',
          'python_version': '3.12.3', 'kicad_version': '10.0.7',
          'producer_sha256': '2fe4ed96615e0b8a5e321b647fd71b0a54408a941d3e72bcca1c1887a521ef23',
          'manifest_sha256': '33cd344e95733941590ffc6cc63e6002ca638077450c0568d3a6ae30f82a3212',
          'code_entries': 313,
          'input_tree_sha256': '0fdad94e4d260be007b438926d80e7d7ad087a4792b93aae3a0ab4a38d7d87b9',
          'board_sha256': 'f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de',
          'rule_areas_sha256': '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
          'policy_canonical_sha256': 'eac2b8ec630bd62a5c6356ea323285a9dc4488cfa7e084009fa03cd3a25c8056'}
MISMATCH = '/opt/kicad-route'


def run(argv, **kw):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=300, **kw)
    return {'argv': [str(a) for a in argv], 'exit': r.returncode, 'stdout': r.stdout[-4000:], 'stderr': r.stderr[-2000:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('packet')
    ap.add_argument('out')
    a = ap.parse_args()
    packet, out = Path(a.packet).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True)
    L, F = packet / 'runtime-L', packet / 'frozen-router'
    rec, bad = {'expected': EXPECT, 'packet': str(packet)}, {}

    def check(key, found, want):
        rec.setdefault('items', {})[key] = {'expected': want, 'found': found, 'exact': found == want}
        if found != want:
            bad[key] = rec['items'][key]

    # 1. Runtime identities by the harness's own readers (the same calls the v2 harness makes).
    args = argparse.Namespace(checkout=str(L), frozen_source=str(F), commit=EXPECT['commit'],
                              source_sha256=EXPECT['source_sha256'], extension_sha256=EXPECT['extension_sha256'],
                              packages_sha256=EXPECT['packages_sha256'], python_version=EXPECT['python_version'])
    found, mismatch = v1.identities(args)
    mismatch = v2.runtime_mismatch(args, found, mismatch)
    rec['runtime'] = found
    rec['harness_runtime_mismatch'] = mismatch
    check('harness_runtime_mismatch', mismatch, {})
    check('manifest_sha256', found['manifest']['sha256'], EXPECT['manifest_sha256'])
    check('manifest_state_dir', found['manifest']['state_dir'], attempt.SANDBOX['checkout'])
    # 2. The container's actual interpreter and the extension's ABI: L's venv python imports grid_router.
    py = L / '.venv-krt/bin/python'
    actual = run([py, '-I', '-c', 'import sys, json, sysconfig, grid_router; print(json.dumps({"version": sys.version.split()[0], '
                  '"executable": sys.executable, "soabi": sysconfig.get_config_var("SOABI"), "module": grid_router.__file__}))'],
                 env={'PATH': '/usr/bin:/bin'})
    rec['interpreter_import'] = actual
    info = json.loads(actual['stdout']) if actual['exit'] == 0 else {}
    check('container_python_version', info.get('version'), EXPECT['python_version'])
    check('extension_import_module', info.get('module'),
          str(L / '.venv-krt/lib/python3.12/site-packages/grid_router.so'))
    rec['interpreter_resolved_in_container'] = str(py.resolve())
    # 3. KiCad.
    kicad = run(['kicad-cli', 'version'])
    rec['kicad_cli'] = kicad
    check('kicad_version', kicad['stdout'].strip(), EXPECT['kicad_version'])
    # 4. The producer.
    check('producer_sha256', v1.sha256(L / 'src/kicad_route/experiment.py'), EXPECT['producer_sha256'])
    with tempfile.TemporaryDirectory() as cwd:
        helped = run([L / 'bin/kicad-route', 'run', '--help'], cwd=cwd, env={'PATH': '/usr/bin:/bin', 'HOME': cwd})
    rec['run_help'] = {'exit': helped['exit'], 'has_experiment_policy': '--experiment-policy' in helped['stdout']}
    check('run_help_experiment_policy', rec['run_help'], {'exit': 0, 'has_experiment_policy': True})
    # 5. Fixed input and policy.
    with tempfile.TemporaryDirectory() as t:
        design = v1.design_copy(Path(t))
        tree, board = v1.tree_record(design), v1.sha256(design / 'project' / v1.BOARD)
    check('input_tree_sha256', tree['sha256'], EXPECT['input_tree_sha256'])
    check('board_sha256', board, EXPECT['board_sha256'])
    policy = {'schema': 'routability-policy/1', 'experiment_class': 'current-policy',
              'allowed_trace_layers': ['F.Cu', 'B.Cu'], 'allowed_via_spans': [['F.Cu', 'B.Cu']],
              'rule_areas_sha256': EXPECT['rule_areas_sha256']}
    check('policy_canonical_sha256', hashlib.sha256(attempt.canonical_json(policy).encode()).hexdigest(),
          EXPECT['policy_canonical_sha256'])
    # 6. The bd1 pin in the production shape: code map, production preflight, production binding.
    code = v1.code_map(L, EXPECT['commit'])
    check('code_entries', len(code), EXPECT['code_entries'])
    check('code_matches_packet', code, json.loads((packet / 'code-map.json').read_text()))
    check('verify_code', attempt.verify_code(L, code), [])
    with consumer.pinned(out, EXPECT['commit'], code) as adapter:
        rec['pinned_adapter'] = adapter
        pre = routing_adapter.preflight(L)
        rec['production_preflight'] = pre
        check('production_preflight_problems', pre['problems'], [])
        decl = consumer.declaration(L, project='project', board=v1.BOARD, schematic=v1.SCHEMATIC, name=v1.NAME,
                                    budget=300, timeout=900, policy=policy)
        bind = consumer.observe(lambda: consumer.binding(decl))
        rec['binding'] = bind if not bind['accepted'] else {'accepted': True}
        check('binding_accepted', bind['accepted'], True)
        if bind['accepted']:
            spec = bind['value']['spec']
            mounts = [m for m in spec['mounts'] if m['at'] == attempt.SANDBOX['checkout']]
            rec['checkout_mounts'] = mounts
            rec['step_argv'] = spec['argv']
            check('checkout_mount', [m['source'] for m in mounts], [str(L)])  # every step mount is --ro-bind-fd (executor extra_ro)
            check('adapter_mount_is_pinned_copy',
                  [m['source'] for m in spec['mounts'] if m['at'] == routing_adapter.ADAPTER_AT], [adapter['path']])
            rec['step_mounts'] = spec['mounts']
    # 7. The mismatch control M: its own identities, captured before use.
    m_manifest = json.loads((Path(MISMATCH) / 'manifest.json').read_text())
    rec['mismatch_M'] = {'path': MISMATCH, 'commit': attempt.checkout_commit(Path(MISMATCH)),
                         **v1.engine_identity(Path(MISMATCH)),
                         'extension_sha256': v1.sha256(Path(MISMATCH) / m_manifest['krt']['extension_module']['path']),
                         'manifest_sha256': v1.sha256(Path(MISMATCH) / 'manifest.json'),
                         'generation': m_manifest['krt'].get('generation')}
    check('mismatch_M_differs', rec['mismatch_M']['commit'] != EXPECT['commit']
          and rec['mismatch_M']['extension_sha256'] != EXPECT['extension_sha256'], True)
    rec['problems'] = bad
    rec['exact'] = not bad
    text = json.dumps(rec, indent=2, default=str) + '\n'
    (out / 'preflight.json').write_text(text)
    print(text)
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
