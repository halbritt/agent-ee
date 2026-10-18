"""Offline Rev C difference report, reusing native capture and block comparison.

The upstream sheet already exists in agent-ee. Its Git blob identity was
confirmed against the public Rev C tree; this tool performs no network I/O.
Differences are unexplained diagnostics, not equivalence or acceptance evidence.
"""
import argparse
import hashlib
from pathlib import Path
import re

from agent_ee.block_inventory import _projection, adapt_connectivity, compare_connectivity
from agent_ee.candidate import capture
from agent_ee.executor import execute
from agent_ee.hardware_interface import read_inventory
from agent_ee.native_netlist import read_components
from agent_ee.reference_blocks import validate_block
from agent_ee.storage import digest, read_json, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CACHED = REPO / 'evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_hardware_board_module_wideband_controller.kicad_sch'
COMMIT = 'ca2adce4aaea1deee66e459cc8949296763a0ad0'
BLOB = 'a7701fc25373e1bcd28ace718cecb35d3b2b5d0e'
SHA256 = 'e54843a1e6ef92a79153d8a7bb491ea1cd8ce8216adbdcd9b8f9d665338a1508'


def verify_upstream(path):
    raw = Path(path).read_bytes()
    blob = hashlib.sha1(b'blob %d\0' % len(raw) + raw).hexdigest()
    if blob != BLOB or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('cached upstream bytes do not match pinned Rev C blob')


def projection(xml, instance, commit):
    native = read_inventory(xml)
    components = read_components(xml)
    members = {ref: {'uuid_path': instance} for ref, c in components.items() if c['sheet'] == instance}
    return _projection(members, native, components, dict(commit=commit, tool='native KiCad XML export'))


def differences(upstream, current):
    before = adapt_connectivity(upstream, dict(reference_offset=300, renames=[], straps=[]))
    after = adapt_connectivity(current, dict(reference_offset=0, renames=[], straps=[]))
    result = compare_connectivity(before, current)
    for item in result:
        item['disposition'] = 'unexplained'
        item['reason'] = None
        ref = item['reference']
        if item['field'] == 'component':
            item['upstream_component'] = upstream['components'].get(
                re.sub(r'[0-9]+$', lambda m: str(int(m[0]) - 300), ref))
            item['fome_component'] = current['components'].get(ref)
        elif item['field'] == 'pin':
            for key, graph in [('upstream_conductor', before), ('fome_conductor', after)]:
                conductor = graph[ref]['pins'].get(item['pin'])
                item[key] = (sorted([list(t) for t in conductor[1]])
                             if conductor and conductor[0] == 'anonymous' else conductor)
    return result


def generate(out):
    verify_upstream(CACHED)
    validate_block(HERE / 'blocks/wbo1')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    run = out / 'candidate'
    run.mkdir()
    capture(HERE / 'reference.scope.json', run)
    current_work, upstream_work = out / 'current', out / 'upstream'
    current_work.mkdir()
    upstream_work.mkdir()
    exports = {}
    for name, inputs, work, schematic in [
            ('current', run / 'inputs', current_work, 'project/reference.kicad_sch'),
            ('upstream', CACHED.parent, upstream_work, CACHED.name)]:
        argv = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                '-o', '/work/native.xml', '/candidate/' + schematic]
        receipt = execute(inputs, work, argv, timeout=180)
        if receipt['execution'] != 'ok' or not (work / 'native.xml').is_file():
            raise ValueError(name + ' native export failed: ' + receipt['execution'])
        exports[name] = dict(argv=argv, execution=receipt['execution'],
                             xml_sha256=digest(work / 'native.xml'))
    block = read_json(HERE / 'blocks/wbo1/block.json')
    source = projection(upstream_work / 'native.xml', '/', COMMIT)
    current = projection(current_work / 'native.xml', block['instances'][0]['uuid_path'], block['source']['commit'])
    changes = differences(source, current)
    report = dict(upstream=dict(repository='mck1117/wideband', commit=COMMIT,
        path='hardware/board_module/wideband_controller.kicad_sch', git_blob=BLOB, sha256=SHA256,
        identity_source='https://api.github.com/repos/mck1117/wideband/git/trees/' + COMMIT + '?recursive=1'),
        fome=dict(commit=block['source']['commit'], sheet_sha256=block['files'][block['sheet']]),
        correspondence='Upstream physical reference number +300; no net or library aliases suppressed.',
        compared_fields=['symbol', 'value', 'footprint', 'populated', 'physical pins and conductors'],
        source_components=len(source['components']), fome_components=len(current['components']),
        exports=exports, differences=changes, unexplained=len(changes),
        limitations=['Every difference is unexplained; DESIGN.md sections 3/5 are context, not inferred reasons.',
                    'No functional, geometry, parts-selection or upstream-equivalence acceptance is claimed.',
                    'Power/flag symbols and terminals outside the wbo1 reference set are not physical block components.'])
    write_json(out / 'parity.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    report = generate(args.out)
    print(f"{report['source_components']} Rev C / {report['fome_components']} fome parts; "
          f"{report['unexplained']} unexplained differences")
