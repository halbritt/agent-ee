"""fab_consistency gate over test-staged fab_export trees and real native evidence.

The malformed-package cases use a stub that stages synthetic package bytes.
The stub still records a real sandboxed `kicad-cli
version` execution, and its manifest names the actual pinned kicad-cli. Footprint
evidence always comes from the real `geometry` check (pcbnew in the sandbox) over
the captured board. Synthetic boards have no schematic, so their `netlist` is a
stub that the gate cross-checks against that native geometry. The parity
ground-truth cases run the real `netlist` check on owned schematics. Astra's
saved AGENTEE-73 probes are reproduced as regressions (`test_astra73_*`).
"""
import base64
import copy
import csv
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest import mock
from urllib.parse import quote

from agent_ee import checks, executor, fab_consistency
from agent_ee.baseline import freeze_evidence, load_baseline, validate_witnesses, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import parse_xml_netlist, run_checks
from agent_ee.executor import SANDBOX_PATH, execute
from agent_ee.fab_export import header_records
from agent_ee.findings import adjudicate, collect_findings
from agent_ee.kicad_embedded import decompress, kicad_checksum, records, verify
from agent_ee.models3d import STOCK_REPOSITORY, STOCK_REVISIONS
from agent_ee.storage import digest, read_json, write_json
from agent_ee.symbol_library import parse_sexpr
try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
RC_CONTROL = REPO / 'fixtures/rc/control'
RC_EMBEDDED = REPO / 'fixtures/rc/embedded-models/rc_filter.kicad_pcb'
PINS_SCHEMATIC = REPO / 'fixtures/datasheet-pins/control/pins.kicad_sch'
LAYERS = ['F.Cu', 'B.Cu', 'F.Mask', 'B.Mask', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'Edge.Cuts']
FUNCTIONS = {'F.Cu': 'Copper,L1,Top', 'B.Cu': 'Copper,L2,Bot', 'F.Mask': 'Soldermask,Top',
             'B.Mask': 'Soldermask,Bot', 'F.Paste': 'Paste,Top', 'B.Paste': 'Paste,Bot',
             'F.SilkS': 'Legend,Top', 'B.SilkS': 'Legend,Bot', 'Edge.Cuts': 'Profile,NP'}
EXTENSIONS = {'F.Cu': 'gtl', 'B.Cu': 'gbl', 'F.Mask': 'gts', 'B.Mask': 'gbs', 'F.Paste': 'gtp',
              'B.Paste': 'gbp', 'F.SilkS': 'gto', 'B.SilkS': 'gbo', 'Edge.Cuts': 'gm1'}
BOM_HEAD = 'Designator,Footprint,Quantity,Comment,Manufacturer,Partnumber,"LCSC Part #"\n'
BOM = BOM_HEAD + ('"R1,R2",R_0603,2,10k,SynthCo,SR-10K,C100001\n'
                  'C1,C_0603,1,100n,SynthCo,SC-100N,C100002\n'
                  'U1,SOT5,1,REG,SynthCo,SU-REG,C100003\n')
CPL_HEAD = 'Designator,Mid X,Mid Y,Layer,Rotation\n'
CPL = CPL_HEAD + 'R1,10,-10,Top,0\nR2,14,-10,Top,0\nC1,18mm,-10mm,Bottom,90\nU1,22,-10,Top,180\n'
# (lib_id, ref, side layer, attr tokens, model paths)
FOOTPRINTS = [('Synth:R_0603', 'R1', 'F.Cu', 'smd', ['models/r.step']),
              ('Synth:R_0603', 'R2', 'F.Cu', 'smd', ['${KIPRJMOD}/models/r.step']),
              ('Synth:C_0603', 'C1', 'B.Cu', 'smd', []),
              ('Synth:SOT5', 'U1', 'F.Cu', 'smd', ['kicad-embed://sot5.step']),
              ('Synth:D_0603', 'D1', 'F.Cu', 'smd dnp', []),
              ('Synth:TP', 'TP1', 'F.Cu', 'smd exclude_from_pos_files exclude_from_bom', [])]
NETLIST_COMPONENTS = {'R1': [], 'R2': [], 'C1': [], 'U1': [], 'D1': ['dnp'],
                      'TP1': ['exclude_from_bom'], '#PWR01': []}
NON_DNP = ['C1', 'R1', 'R2', 'TP1', 'U1']
RC_BOM = BOM_HEAD + ('C1,C_0805,1,100n,SynthCo,SC-100N,C100002\n'
                     'J1,PinHeader_1x03,1,Conn,SynthCo,SJ-3,C100005\n'
                     'R1,R_0805,1,10k,SynthCo,SR-10K,C100001\n')
RC_CPL = CPL_HEAD + 'C1,110,-100,Top,0\nJ1,100,-100,Top,0\nR1,105,-100,Top,0\n'
RC_MODELS = {'Capacitor_SMD.3dshapes/C_0805_2012Metric.step': 'c1.step',
             'Resistor_SMD.3dshapes/R_0805_2012Metric.step': 'r1.step',
             'Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x03_P2.54mm_Vertical.step': 'j1.step'}
STOCK_RECEIPT = 'lib3d/.agent-ee-models.json'
STOCK_ROOTS = {'KICAD10_3DMODEL_DIR': STOCK_RECEIPT}
RESAVE = 'import pcbnew, sys\nboard = pcbnew.LoadBoard(sys.argv[1])\npcbnew.SaveBoard(sys.argv[2], board)\n'


def owned_step(name):
    """Owned placeholder STEP text (compressible): presence evidence only, not model geometry."""
    points = ''.join(f"#{i + 1}=CARTESIAN_POINT('{name} p{i}',({i % 4}.,{i // 4 % 4}.,{i // 16}.));\n"
                     for i in range(64))
    return (f"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('agent-ee owned placeholder solid {name}'),'2;1');\n"
            f"FILE_NAME('{name}','2026-10-17T00:00:00',('agent-ee'),('agent-ee'),'','','');\n"
            f"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n{points}ENDSEC;\nEND-ISO-10303-21;\n").encode()


def stock_library(paths, *, root='lib3d', listed=None, captured=None, provenance='pinned', content=None):
    """Captured stock 3D-model library: a `models3d` receipt plus member files (AGENTEE-124).

    Members are owned placeholder STEP bytes, not KiCad library content. Pinned
    entries carry the receipt fields `fetch_stock_models` writes for a download at
    a pinned kicad-packages3D revision; the gate checks only the captured bytes
    against that hash. `listed`, `captured` and `content` inject faults.
    """
    data = {rel: (content or {}).get(rel, owned_step(PurePosixPath(rel).name)) for rel in paths}
    entries = {}
    for rel in (paths if listed is None else listed):
        entry = {'sha256': hashlib.sha256(owned_step(PurePosixPath(rel).name)).hexdigest(),
                 'size_bytes': len(owned_step(PurePosixPath(rel).name))}
        if provenance == 'pinned':
            entry.update(commit=STOCK_REVISIONS[0],
                         source_url=f'{STOCK_REPOSITORY}/-/raw/{STOCK_REVISIONS[0]}/{quote(rel, safe="/")}')
        else:
            entry['source'] = 'existing-local'
        entries[rel] = entry
    receipt = {'schema': 'agent-ee.stock-models/1', 'repository': STOCK_REPOSITORY, 'files': entries}
    files = {f'{root}/.agent-ee-models.json': json.dumps(receipt, indent=2, sort_keys=True).encode()}
    files.update({f'{root}/{rel}': data[rel] for rel in (paths if captured is None else captured)})
    return files


def raw_frame(payload):
    """One zstd frame of raw blocks that declares its size (valid zstd, no compression)."""
    chunks = [payload[i:i + 131072] for i in range(0, len(payload), 131072)] or [b'']
    blocks = b''.join(((len(c) << 3) | (i == len(chunks) - 1)).to_bytes(3, 'little') + c
                      for i, c in enumerate(chunks))
    return b'\x28\xb5\x2f\xfd\xa0' + len(payload).to_bytes(4, 'little') + blocks


def embedded_file(name, payload=None, *, data=None, checksum=None, kind='model'):
    payload = owned_step(name) if payload is None else payload
    data = '|' + base64.b64encode(raw_frame(payload)).decode() + '|' if data is None else data
    checksum = kicad_checksum(payload) if checksum is None else checksum
    fields = [f'(name "{name}")', f'(type {kind})'] + ([f'(data {data})'] if data != '' else []) + (
        [f'(checksum "{checksum}")'] if checksum != '' else [])
    return '\n\t\t(file ' + ' '.join(fields) + ')'


def board_text(footprints=FOOTPRINTS, layers=LAYERS, embedded=None, extra=''):
    table = '\n'.join(f'\t\t({i} "{name}" {"signal" if name.endswith(".Cu") else "user"})'
                      for i, name in enumerate(layers))
    blocks = []
    for index, (lib, ref, side, attrs, models) in enumerate(footprints):
        model = ''.join(f'\n\t\t(model "{m}"\n\t\t\t(offset\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n\t\t)'
                        for m in models)
        silk = 'F.SilkS' if side == 'F.Cu' else 'B.SilkS'
        blocks.append(f'''\t(footprint "{lib}"
\t\t(layer "{side}")
\t\t(uuid "00000000-0000-4000-8000-{index:012d}")
\t\t(at {10 + 4 * index} 10)
\t\t(property "Reference" "{ref}"
\t\t\t(at 0 -1 0)
\t\t\t(layer "{silk}")
\t\t)
\t\t(attr {attrs})
\t\t(pad "1" smd rect
\t\t\t(at 0 0)
\t\t\t(size 1 1)
\t\t\t(layers "{side}")
\t\t){model}
\t)''')
    files = embedded_file('sot5.step') if embedded is None else embedded
    return (f'(kicad_pcb\n\t(version 20260206)\n\t(generator "agent-ee-test")\n\t(layers\n{table}\n\t)\n'
            + '\n'.join(blocks) + extra + f'\n\t(embedded_files{files}\n\t)\n)\n')


def netlist_xml(components=NETLIST_COMPONENTS):
    comps = ''.join(f'    <comp ref="{ref}">\n      <value>x</value>\n'
                    + ''.join(f'      <property name="{flag}"/>\n' for flag in flags)
                    + '    </comp>\n' for ref, flags in components.items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<export version="E">\n  <components>\n'
            f'{comps}  </components>\n  <nets>\n    <net code="1" name="GND"/>\n  </nets>\n</export>\n')


def gerber(function):
    if function is None:
        return '%FSLAX46Y46*%\n%MOMM*%\nM02*\n'
    return f'G04 #@! TF.FileFunction,{function}*\n%FSLAX46Y46*%\n%MOMM*%\nM02*\n'


def excellon(function):
    head = f'; #@! TF.FileFunction,{function}\n' if function else ''
    return f'M48\n; DRILL file synthetic\n{head}METRIC\n%\nG90\nM30\n'


def package(bom=BOM, cpl=CPL):
    files = {f'gerbers/board-{n.replace(".", "_")}.{EXTENSIONS[n]}': gerber(FUNCTIONS[n]) for n in LAYERS}
    files.update({'drill/board-PTH.drl': excellon('Plated,1,2,PTH'),
                  'drill/board-NPTH.drl': excellon('NonPlated,1,2,NPTH'),
                  'drill/board-PTH-drl_map.gbr': '%TF.FileFunction,Drillmap*%\n%FSLAX45Y45*%\nM02*\n',
                  'bom.csv': bom, 'cpl.csv': cpl,
                  'pcb_summary.json': json.dumps({'copper_layers': 2, 'footprints': 6})})
    return {k: v.encode() if isinstance(v, str) else v for k, v in files.items()}


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(files):
            if rel.startswith(('gerbers/', 'drill/')):
                archive.writestr(zipfile.ZipInfo(rel, (1980, 1, 1, 0, 0, 0)), files[rel])
    return buffer.getvalue()


def derive_rc_embedded(text, frames):
    """RC control board text with each stock model replaced by an owned embedded model.

    `frames` maps model name to its zstd frame. The committed fixture used `zstd -19`
    frames; records carry SHA-256 checksums so pcbnew writes its own MMH3 checksum.
    """
    for path, name in RC_MODELS.items():
        old = f'(model "${{KICAD10_3DMODEL_DIR}}/{path}"'
        if text.count(old) != 1:
            raise AssertionError(f'RC control no longer has exactly one {path} model')
        text = text.replace(old, f'(model "kicad-embed://{name}"')
    files = ''.join(embedded_file(name, data='|' + base64.b64encode(frames[name]).decode() + '|',
                                  checksum=hashlib.sha256(owned_step(name)).hexdigest())
                    for name in sorted(RC_MODELS.values()))
    end = text.rstrip().rindex(')')
    return text[:end] + f'\t(embedded_files{files}\n\t)\n)\n'


def pcbnew_resave(source, target):
    run = subprocess.run(['/usr/bin/python3', '-c', RESAVE, str(source), str(target)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise AssertionError(f'pcbnew resave failed: {run.stderr}')
    return run


def fixture_frames():
    found = records(parse_sexpr(RC_EMBEDDED.read_bytes()))
    return {name: base64.b64decode(''.join(c[0]['data'][0])[1:-1]) for name, c in found.items()}


def rc_project(board):
    """RC control project with a replacement board; stock libraries as in control.scope.json."""
    files = {f'project/{p.name}': p.read_bytes() for p in RC_CONTROL.iterdir()
             if p.is_file() and p.name != 'rc_filter.kicad_pcb' and p.suffix != '.kicad_prl'}
    files['project/rc_filter.kicad_pcb'] = board
    stock = [i for i in read_json(REPO / 'fixtures/rc/control.scope.json')['inputs']
             if Path(i['path']).is_absolute()]
    return {'files': files, 'board': 'project/rc_filter.kicad_pcb',
            'schematic': 'project/rc_filter.kicad_sch', 'stock': stock}


class FabCase(unittest.TestCase):
    def run_case(self, *, files=None, board=None, netlist=None, manifest_edit=None, post=None,
                 export_execution='ok', order=None, export_params=None, consistency_params=None,
                 extra_inputs=None, seal_all=True, zip_bytes=None, schematic=None, project=None,
                 native_netlist=False, non_dnp=None, version_record=True, geometry_post=None):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'requirements.md').write_text('The fab package must describe the verified board.\n')
        inputs = [{'path': 'requirements.md', 'at': 'requirements.md'}]
        if project is None:
            (root / 'board.kicad_pcb').write_text(board if board is not None else board_text())
            (root / 'board.kicad_sch').write_text(schematic if schematic is not None
                                                  else '(kicad_sch (version 20260206))\n')
            (root / 'models').mkdir()
            (root / 'models/r.step').write_text('ISO-10303-21; synthetic\n')
            inputs += [{'path': p, 'at': p} for p in ('board.kicad_pcb', 'board.kicad_sch', 'models/r.step')]
            board_rel, schematic_rel = 'board.kicad_pcb', 'board.kicad_sch'
        else:
            for rel, data in project['files'].items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            inputs += [{'path': 'project', 'at': 'project'}] + project['stock']
            board_rel, schematic_rel = project['board'], project['schematic']
        for rel, data in (extra_inputs or {}).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
            inputs.append({'path': rel, 'at': rel})
        specs = {'netlist': {'id': 'netlist', 'kind': 'netlist'},
                 'geometry': {'id': 'geometry', 'kind': 'geometry'},
                 'export': {'id': 'export', 'kind': 'fab_export',
                            'params': export_params if export_params is not None else {'netlist_check': 'netlist'}},
                 'fab': {'id': 'fab', 'kind': 'fab_consistency',
                         'params': consistency_params or {'export_check': 'export'}}}
        chosen = order or ('netlist', 'geometry', 'export', 'fab')
        scope = {'schema': 'agent-ee.scope/2', 'board': board_rel, 'schematic': schematic_rel,
                 'inputs': inputs, 'requirements': ['requirements.md'],
                 'checks': [specs[k] for k in chosen],
                 'baseline': {'requirements': [{'id': 'fab-package', 'statement': 'Consistent fab package',
                                                'source': {'path': 'requirements.md', 'locator': 'line 1'},
                                                'checks': list(chosen)}],
                              'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        manifest = capture(root / 'scope.json', run)
        staged = package() if files is None else files
        xml = netlist_xml() if netlist is None else netlist
        claimed = list(NON_DNP if non_dnp is None else non_dnp)

        def stub_netlist(ctx):
            (ctx.work / 'design.xml').write_text(xml)
            write_json(ctx.work / 'connectivity.json', parse_xml_netlist(ctx.work / 'design.xml'))
            ctx.evidence('design.xml', 'connectivity.json')
            ctx.result.update(execution='ok', outcome='info', summary='synthetic netlist stub')

        def stub_export(ctx):
            # A real sandboxed version record, as the pinned-tool contract requires.
            record = execute(ctx.inputs, ctx.work / 'steps/version', ['kicad-cli', 'version'], timeout=60)
            if version_record:
                ctx.evidence(*(f'steps/version/{n}' for n in ('execution.json', 'stdout.log', 'stderr.log',
                                                              'console.log')))
            path = shutil.which('kicad-cli', path=SANDBOX_PATH)
            tool = {'path': path, 'version': (ctx.work / 'steps/version/stdout.log').read_text().strip(),
                    'sha256': digest(path)}
            self.assertEqual(record['execution'], 'ok')
            stage = ctx.work / 'fab'
            content = dict(staged)
            if 'gerbers.zip' not in content:
                content['gerbers.zip'] = zip_bytes if zip_bytes is not None else zipped(content)
            for rel, data in content.items():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                (stage / rel).write_bytes(data)
            value = {'schema': 'agent-ee.fab-manifest/1', 'candidate_id': ctx.manifest['candidate_id'],
                     'board_path': board_rel, 'board_sha256': ctx.manifest['files'][board_rel], 'tool': tool,
                     'files': {rel: hashlib.sha256(data).hexdigest() for rel, data in content.items()},
                     'headers': header_records(),
                     'copper_layers': 2, 'enabled_fab_layers': list(LAYERS),
                     'drill': {'pth': 'board-PTH.drl', 'npth': 'board-NPTH.drl'},
                     'designators': {'board_non_dnp': list(claimed), 'excluded': [], 'exclusion_rule': None},
                     'generated_utc': '2026-10-17T00:00:00Z'}
            if manifest_edit:
                manifest_edit(value)
            write_json(stage / 'manifest.json', value)
            if geometry_post:
                geometry_post(ctx.run_dir / 'work/geometry/geometry.json')
            if post:
                post(stage)
            sealed = ['manifest.json'] + (sorted(content) if seal_all else [])
            ctx.evidence(*(f'fab/{rel}' for rel in sealed))
            ctx.result.update(execution=export_execution,
                              outcome='pass' if export_execution == 'ok' else 'unknown',
                              summary='synthetic fab_export stub')

        stubs = {'fab_export': stub_export}
        if not native_netlist:
            stubs['netlist'] = stub_netlist
        with mock.patch.dict(checks.CHECK_FUNCS, stubs):
            results = run_checks(run, time.monotonic() + 600)
        self.run_dir, self.manifest, self.results = run, manifest, results
        return next(r for r in results if r['id'] == 'fab')

    def result(self, cid):
        return next(r for r in self.results if r['id'] == cid)

    def assertStatus(self, result, execution, outcome, text=None):
        self.assertEqual((result['execution'], result['outcome']), (execution, outcome), result)
        if text:
            self.assertIn(text, result['summary'])

    def defects(self, result):
        return {(d['code'], d['locator']) for d in result['details']['defects']}

    def codes(self, result):
        return {d['code'] for d in result['details']['defects']}


class FabConsistencyTests(FabCase):
    # ---------------------------------------------------------------- success

    def test_consistent_synthetic_package_passes_and_seals(self):
        result = self.run_case()
        self.assertStatus(result, 'ok', 'pass')
        details = result['details']
        self.assertEqual(len(details['plots']), 9)
        self.assertEqual({p['layer'] for p in details['plots']}, set(LAYERS))
        self.assertEqual(sorted(d['role'] for d in details['drill']), ['npth', 'pth'])
        self.assertEqual(details['designators']['board_assembled'], 4)
        self.assertEqual(details['designators']['parity_violations'], [])
        self.assertEqual({m['ref']: m['status'] for m in details['models']['records']},
                         {'R1': 'resolved', 'R2': 'resolved', 'U1': 'resolved'})
        self.assertEqual(details['tool']['status'], 'verified')
        self.assertIn('work/export/fab/manifest.json', result['evidence'])
        self.assertIn('work/netlist/design.xml', result['evidence'])
        self.assertIn('work/geometry/geometry.json', result['evidence'])
        self.assertIn('work/export/steps/version/stdout.log', result['evidence'])
        self.assertIn('work/fab/fab-consistency.json', result['evidence'])
        self.assertTrue(all(not isinstance(e, str) or e.startswith(('work/fab/', 'work/export/', 'work/netlist/',
                                                                    'work/geometry/'))
                            for e in result['evidence']))
        self.assertEqual(sorted(p.name for p in (self.run_dir / 'work/fab').iterdir()), ['fab-consistency.json'])
        freeze_evidence(self.run_dir, self.results)
        verify_evidence(self.run_dir, self.results, required=True)
        bom = self.run_dir / 'work/export/fab/bom.csv'
        bom.write_bytes(bom.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            verify_evidence(self.run_dir, self.results, required=True)

    def test_defect_citations_stay_sealable_when_export_seals_only_manifest(self):
        files = package()
        files['bom.csv'] = files['bom.csv'].replace(b'"R1,R2"', b'"R1,R1"')
        result = self.run_case(files=files, seal_all=False)
        self.assertStatus(result, 'ok', 'defect')
        paths = {e['path'] for e in result['evidence'] if isinstance(e, dict)}
        self.assertEqual(paths, {'work/fab/fab-consistency.json'})
        freeze_evidence(self.run_dir, self.results)
        verify_evidence(self.run_dir, self.results, required=True)

    # --------------------------------------------------------- prerequisites

    def test_prerequisite_not_ok_wrong_order_and_params_are_unsupported(self):
        self.assertStatus(self.run_case(export_execution='failed'), 'unsupported', 'unknown', 'unverified')
        self.assertStatus(self.run_case(order=('netlist', 'geometry', 'fab', 'export')), 'unsupported', 'unknown',
                          'later in scope order')
        self.assertStatus(self.run_case(consistency_params={'export_check': 'netlist'}), 'unsupported', 'unknown',
                          'fab_export prerequisite missing')
        self.assertStatus(self.run_case(consistency_params={'export_check': 'export', 'regenerate': True}),
                          'unsupported', 'unknown', 'with optional model_libraries')
        # A scope/2 baseline freezes the declared receipts, so capture refuses an invalid map.
        with self.assertRaisesRegex(ValueError, 'not a KiCad stock 3D-model variable'):
            self.run_case(consistency_params={'export_check': 'export', 'model_libraries': {'KIPRJMOD': STOCK_RECEIPT}})
        self.assertStatus(self.run_case(export_params={}), 'unsupported', 'unknown', 'netlist')

    def test_geometry_evidence_is_required_and_must_precede_the_gate(self):
        self.assertStatus(self.run_case(order=('netlist', 'export', 'fab')), 'unsupported', 'unknown',
                          'no earlier sealed geometry')
        self.assertStatus(self.run_case(order=('netlist', 'export', 'fab', 'geometry')), 'unsupported', 'unknown',
                          'no earlier sealed geometry')

    def test_unsealed_manifest_and_empty_staging_directory(self):
        def unseal(stage):
            (stage / 'manifest.json').rename(stage / 'manifest.moved')
        moved = self.run_case(post=unseal)
        self.assertStatus(moved, 'unsupported', 'unknown', 'did not seal')

        def empty(stage):
            for path in sorted(stage.rglob('*'), reverse=True):
                path.rmdir() if path.is_dir() else path.unlink()
        self.assertStatus(self.run_case(post=empty), 'malformed', 'unknown', 'absent or empty')

    # ----------------------------------------------------- inventory / manifest

    def test_inventory_extra_missing_hash_empty_and_link_are_malformed(self):
        cases = {
            'stale extra': lambda s: (s / 'gerbers/old-F_Cu.gtl').write_text(gerber('Copper,L1,Top')),
            'missing': lambda s: (s / 'cpl.csv').unlink(),
            'hash': lambda s: (s / 'bom.csv').write_bytes((s / 'bom.csv').read_bytes() + b' '),
            'link': lambda s: ((s / 'cpl.csv').unlink(), (s / 'cpl.csv').symlink_to(s / 'bom.csv')),
        }
        expected = {'stale extra': 'not in manifest', 'missing': 'absent', 'hash': 'differ from manifest',
                    'link': 'symbolic link'}
        for name, post in cases.items():
            with self.subTest(name):
                self.assertStatus(self.run_case(post=post), 'malformed', 'unknown', expected[name])
        files = package()
        files['pcb_summary.json'] = b''
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'empty staged artifact')
        files = package()
        del files['pcb_summary.json']
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'required package file')

    def test_manifest_tampering_is_malformed(self):
        edits = {
            'candidate': (lambda m: m.update(candidate_id='0' * 64), 'different candidate'),
            'board digest': (lambda m: m.update(board_sha256='0' * 64), 'board_sha256'),
            'board path': (lambda m: m.update(board_path='other.kicad_pcb'), 'board_path'),
            'schema': (lambda m: m.update(schema='agent-ee.fab-manifest/2'), 'fields'),
            'extra key': (lambda m: m.update(note='x'), 'fields'),
            'traversal': (lambda m: m['files'].update({'../escape': 'b' * 64}), 'unsafe'),
            'tool': (lambda m: m['tool'].update(sha256='short'), 'tool needs'),
            'drill': (lambda m: m.update(drill={'pth': 'board-PTH.drl', 'npth': 'board-PTH.drl'}), 'distinct'),
            'board_non_dnp stale': (lambda m: m['designators'].update(board_non_dnp=['C1', 'R1', 'R2', 'U1']),
                                    "board_non_dnp ['TP1']"),
            'designators key': (lambda m: m['designators'].pop('excluded'), 'designators must hold'),
            'unknown rule': (lambda m: m['designators'].update(exclusion_rule='Value=10k'),
                             'not a known assembly rule'),
            'excluded off board': (lambda m: m['designators'].update(excluded=[{'designator': 'ZZ9', 'rule': None}]),
                                   'designators.excluded must be'),
            'rule not captured': (lambda m: m['designators'].update(exclusion_rule='JLCPCB_Assembly=not_assembled'),
                                  'not the captured export rule'),
            'excluded stale': (lambda m: m['designators'].update(excluded=[{'designator': 'R1', 'rule': None}]),
                               'stale: manifest excluded'),
        }
        for name, (edit, text) in edits.items():
            with self.subTest(name):
                self.assertStatus(self.run_case(manifest_edit=edit), 'malformed', 'unknown', text)
        garbage = self.run_case(post=lambda s: (s / 'manifest.json').write_text('{'))
        self.assertStatus(garbage, 'malformed', 'unknown', 'not JSON')

        def duplicate(stage):
            text = (stage / 'manifest.json').read_text()
            (stage / 'manifest.json').write_text(text.replace('{', '{"copper_layers": 2, ', 1))
        self.assertStatus(self.run_case(post=duplicate), 'malformed', 'unknown', 'duplicate JSON key')

    def test_tampered_captured_board_is_stale(self):
        def tamper(stage):
            board = stage.parents[2] / 'inputs/board.kicad_pcb'
            board.write_text(board.read_text() + '\n')
        self.assertStatus(self.run_case(post=tamper), 'malformed', 'unknown', 'board_sha256')

    # ------------------------------------------------------ tool identity (probe 1)

    def test_astra73_fictional_tool_identity_is_refused(self):
        # Astra's saved probe: invented path, version and digest previously gave ok/pass.
        result = self.run_case(manifest_edit=lambda m: m['tool'].update(
            path='/not-a-native-tool', version='fictional', sha256='f' * 64))
        self.assertStatus(result, 'malformed', 'unknown', 'pinned kicad-cli')

    def test_each_fictional_tool_field_is_refused(self):
        real = shutil.which('kicad-cli', path=SANDBOX_PATH)
        self.read_manifest = lambda: read_json(self.run_dir / 'work/export/fab/manifest.json')
        cases = {'path': ({'path': '/usr/bin/true'}, 'pinned kicad-cli'),
                 'digest': ({'sha256': 'f' * 64}, 'digest'),
                 'version': ({'version': 'fictional'}, 'sealed kicad-cli version')}
        for name, (change, text) in cases.items():
            with self.subTest(name):
                result = self.run_case(manifest_edit=lambda m, c=change: m['tool'].update(c))
                self.assertStatus(result, 'malformed', 'unknown', text)
        self.assertEqual(self.read_manifest()['tool']['sha256'], digest(real))

    def test_unverifiable_tool_identity_is_never_pass(self):
        unrecorded = self.run_case(version_record=False)
        self.assertStatus(unrecorded, 'unsupported', 'unknown', 'no sealed kicad-cli version execution')
        self.assertEqual(unrecorded['details']['tool']['status'], 'unverified')
        files = package()
        files['bom.csv'] = files['bom.csv'].replace(b'"R1,R2"', b'"R1,R1"')
        defect = self.run_case(files=files, version_record=False)
        self.assertStatus(defect, 'ok', 'defect')
        self.assertIn('tool_identity', {g['code'] for g in defect['details']['coverage_gaps']})
        with mock.patch.object(fab_consistency, 'pinned_kicad_cli', return_value=None):
            self.assertStatus(self.run_case(), 'unsupported', 'unknown', 'pinned kicad-cli unavailable')

    # ------------------------------------------------------- layers and plots

    def test_plot_count_below_enabled_layers_is_malformed(self):
        files = package()
        del files['gerbers/board-B_SilkS.gbo']
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', '8 plot file(s) for 9')

    def test_missing_layer_plot_wrong_function_and_layer_reconciliation(self):
        files = package()
        files['gerbers/board-B_SilkS.gbo'] = gerber('Other,User').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', "no plot for enabled fab layer(s) ['B.SilkS']")
        files = package()
        files['gerbers/board-B_Cu.gbl'] = gerber('Copper,L4,Bot').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'contradicts 2 copper')
        files = package()
        files['gerbers/board-B_Mask.gbs'] = gerber('Soldermask,Top').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'several plots claim one layer')
        self.assertStatus(self.run_case(manifest_edit=lambda m: m.update(copper_layers=4)),
                          'malformed', 'unknown', 'copper_layers 4')
        self.assertStatus(self.run_case(manifest_edit=lambda m: m['enabled_fab_layers'].remove('Edge.Cuts')),
                          'malformed', 'unknown', 'omit required')
        self.assertStatus(self.run_case(manifest_edit=lambda m: m['enabled_fab_layers'].append('F.Fab')),
                          'malformed', 'unknown', 'non-fab')
        board = board_text(layers=[n for n in LAYERS if n != 'B.Paste'])
        self.assertStatus(self.run_case(board=board), 'malformed', 'unknown', 'absent from the board layer table')

    def test_unattributed_plot_is_unknown_coverage(self):
        files = package()
        files['gerbers/board-B_SilkS.gbo'] = gerber(None).encode()
        result = self.run_case(files=files)
        self.assertStatus(result, 'unsupported', 'unknown', 'lack coverage')
        self.assertEqual(result['details']['coverage_gaps'][0]['code'], 'plot_identity')

    # ------------------------------------------------------------------- drill

    def test_drill_must_be_one_pth_and_one_npth(self):
        files = package()
        del files['drill/board-NPTH.drl']
        files['drill/board-PTH.drl'] = excellon('MixedPlating,1,2').encode()
        mixed = self.run_case(files=files, manifest_edit=lambda m: m['drill'].update(npth='board.drl'))
        self.assertStatus(mixed, 'malformed', 'unknown', 'drill')
        files = package()
        files['drill/board.drl'] = excellon('MixedPlating,1,2').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'exactly the PTH and NPTH')
        files = package()
        files['drill/board-PTH.drl'] = excellon('NonPlated,1,2,NPTH').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'plating attribute')
        files = package()
        files['drill/board-NPTH.drl'] = excellon('MixedPlating,1,2').encode()
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'MixedPlating')
        files = package()
        files['drill/notes.txt'] = b'free text'
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'neither Excellon')
        files = package()
        files['drill/board-NPTH.drl'] = excellon(None).encode()
        self.assertStatus(self.run_case(files=files), 'unsupported', 'unknown', 'plating FileFunction')

    def test_gerbers_zip_must_mirror_staged_plots_and_drills(self):
        files = package()
        trimmed = {k: v for k, v in files.items() if k != 'drill/board-NPTH.drl'}
        self.assertStatus(self.run_case(zip_bytes=zipped(trimmed)), 'malformed', 'unknown', 'omits')
        altered = dict(files)
        altered['bom.csv'] = b'not a plot'
        altered['gerbers/board-F_Cu.gtl'] = gerber('Copper,L1,Top').encode().replace(b'MOMM', b'MOIN')
        self.assertStatus(self.run_case(zip_bytes=zipped(altered)), 'malformed', 'unknown', 'differs')
        self.assertStatus(self.run_case(zip_bytes=b'PK\x03\x04broken'), 'malformed', 'unknown', 'unreadable')

    # --------------------------------------------------------- designator parity

    def test_compressed_range_duplicate_and_empty_designators_are_defects(self):
        cases = {
            'compressed_range': BOM.replace('"R1,R2",R_0603,2', '"R1-R2",R_0603,1'),
            'duplicate_designator': BOM.replace('C1,C_0603', 'R1,C_0603'),
            'empty_designator': BOM.replace('"R1,R2",R_0603,2', '"R1,,R2",R_0603,3'),
        }
        for code, bom in cases.items():
            with self.subTest(code):
                files = package()
                files['bom.csv'] = bom.encode()
                result = self.run_case(files=files)
                self.assertStatus(result, 'ok', 'defect')
                self.assertIn(code, self.codes(result))
        files = package()
        files['cpl.csv'] = (CPL + 'R1,10,10,Top,0\n').encode()
        self.assertIn('duplicate_designator', self.codes(self.run_case(files=files)))

    def test_parity_names_missing_designator_in_each_direction(self):
        files = package()
        files['cpl.csv'] = CPL.replace('C1,18mm,-10mm,Bottom,90\n', '').encode()
        result = self.run_case(files=files)
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn(('designator_parity', 'designator C1 absent from cpl'), self.defects(result))
        self.assertIn('C1', result['location'])
        files = package()
        files['bom.csv'] = (BOM + 'R9,R_0603,1,1k,SynthCo,SR-1K,C100004\n').encode()
        files['cpl.csv'] = (CPL + 'R9,30,10,Top,0\n').encode()
        result = self.run_case(files=files)
        self.assertIn(('designator_parity', 'designator R9 absent from board'), self.defects(result))
        files = package()
        files['bom.csv'] = BOM.replace('U1,SOT5,1,REG,SynthCo,SU-REG,C100003\n', '').encode()
        result = self.run_case(files=files)
        self.assertIn(('designator_parity', 'designator U1 absent from bom'), self.defects(result))
        files = package()
        files['bom.csv'] = (BOM + 'D1,D_0603,1,LED,SynthCo,SD,C100005\n').encode()
        files['cpl.csv'] = (CPL + 'D1,26,10,Top,0\n').encode()
        result = self.run_case(files=files)
        self.assertIn(('designator_parity', 'designator D1 absent from board'), self.defects(result))

    def test_empty_lcsc_quantity_and_lcsc_footprint_conflicts(self):
        files = package()
        files['bom.csv'] = BOM.replace(',C100002', ',').encode()
        result = self.run_case(files=files)
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn('empty_lcsc', self.codes(result))
        files['bom.csv'] = BOM.replace('"R1,R2",R_0603,2', '"R1,R2",R_0603,3').encode()
        self.assertIn('quantity_mismatch', self.codes(self.run_case(files=files)))
        files['bom.csv'] = BOM.replace(',C100002', ',C100003').encode()
        result = self.run_case(files=files)
        self.assertIn(('lcsc_multiple_footprints', "LCSC C100003 maps to footprints "
                       "['C_0603', 'SOT5', 'Synth:C_0603', 'Synth:SOT5']"), self.defects(result))
        native = [list(fp) for fp in FOOTPRINTS]
        native[1][0] = 'Synth:R_0805'
        result = self.run_case(board=board_text([tuple(fp) for fp in native]))
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn(('lcsc_multiple_footprints', "LCSC C100001 maps to footprints "
                       "['R_0603', 'Synth:R_0603', 'Synth:R_0805']"), self.defects(result))

    def test_cpl_layer_must_match_board_side(self):
        files = package()
        files['cpl.csv'] = CPL.replace('C1,18mm,-10mm,Bottom', 'C1,18mm,-10mm,Top').encode()
        result = self.run_case(files=files)
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn(('cpl_side', 'cpl.csv row 4 designator C1 Layer top but board footprint is bottom'),
                      self.defects(result))
        files['cpl.csv'] = CPL.replace('Bottom', 'Underside').encode()
        self.assertIn('invalid_cpl_layer', self.codes(self.run_case(files=files)))

    def test_netlist_dnp_assembled_part_is_defect(self):
        components = dict(NETLIST_COMPONENTS, C1=['dnp'])
        result = self.run_case(netlist=netlist_xml(components))
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn('netlist_exclusion_assembled', self.codes(result))

    def test_unparseable_csv_is_malformed(self):
        variants = {
            'header': ('bom.csv', BOM.replace('"LCSC Part #"', 'LCSC'), 'header must be exactly'),
            'value_header': ('bom.csv', BOM.replace('Comment', 'Value'), 'header must be exactly'),
            'cells': ('cpl.csv', CPL + 'U9,1,2,Top\n', 'has 4 cells'),
            'number': ('cpl.csv', CPL.replace('22,-10,Top', 'twenty,10,Top'), 'not a finite number'),
            'infinite': ('cpl.csv', CPL.replace('22,-10,Top', '1e999,10,Top'), 'not a finite number'),
            'quote': ('bom.csv', BOM + '"R9,R_0603\n', 'does not parse'),
            'no rows': ('cpl.csv', CPL_HEAD, 'has no rows'),
        }
        for name, (rel, text, reason) in variants.items():
            with self.subTest(name):
                files = package()
                files[rel] = text.encode()
                self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', reason)
        files = package()
        files['bom.csv'] = BOM.encode().replace(b'SynthCo,SR-10K', b'SynthCo\xff,SR-10K')
        self.assertStatus(self.run_case(files=files), 'malformed', 'unknown', 'does not parse')
        self.assertStatus(self.run_case(manifest_edit=lambda m: m['headers']['bom'].update(sha256='0' * 64)),
                          'malformed', 'unknown', 'manifest headers do not match captured JLC templates')

    def test_non_ascii_bom_cell_is_defect(self):
        files = package()
        files['bom.csv'] = BOM.replace('100n', '100µ').encode()
        self.assertIn('non_ascii_bom_cell', self.codes(self.run_case(files=files)))

    # ----------------------------------------- parity ground truth (probe 2)

    def test_astra73_unrelated_netlist_is_refused(self):
        # Astra's saved probe: a U99-only netlist previously gave ok/pass.
        result = self.run_case(netlist=netlist_xml({'U99': []}))
        self.assertStatus(result, 'malformed', 'unknown', 'absent from the sealed native netlist')

    def test_astra73_actual_native_inventory_mismatch_is_refused(self):
        # Astra's saved probe: the real netlist check on the captured owned pins
        # schematic exports only U1, while the board, BOM and CPL carry R1, R2, C1, U1.
        result = self.run_case(schematic=PINS_SCHEMATIC.read_text(), native_netlist=True)
        producer = self.result('netlist')
        self.assertEqual(producer['execution'], 'ok')
        self.assertEqual(producer['details']['refs'], ['U1'])
        self.assertStatus(result, 'malformed', 'unknown', 'absent from the sealed native netlist')

    def test_rc_native_netlist_and_geometry_parity_passes(self):
        result = self.run_case(project=rc_project(RC_EMBEDDED.read_bytes()), native_netlist=True,
                               files=package(RC_BOM, RC_CPL), non_dnp=['C1', 'J1', 'R1'])
        self.assertEqual(self.result('netlist')['details']['refs'], ['C1', 'J1', 'R1'])
        geometry = read_json(self.run_dir / 'work/geometry/geometry.json')
        self.assertEqual(sorted(f['ref'] for f in geometry['footprints']), ['C1', 'J1', 'R1'])
        self.assertStatus(result, 'ok', 'pass')
        self.assertEqual(result['details']['designators']['board_assembled'], 3)
        self.assertEqual(result['details']['designators']['sources'],
                         {'geometry': ['geometry'], 'netlist': 'netlist'})
        self.assertEqual({m['ref']: m['status'] for m in result['details']['models']['records']},
                         {'C1': 'resolved', 'J1': 'resolved', 'R1': 'resolved'})

    def test_rc_claims_beyond_native_inventory_are_refused(self):
        project = rc_project(RC_EMBEDDED.read_bytes())
        claimed = self.run_case(project=project, native_netlist=True, files=package(RC_BOM, RC_CPL),
                                non_dnp=['C1', 'R1', 'R2', 'U1'])
        self.assertStatus(claimed, 'malformed', 'unknown', 'board_non_dnp')
        bom = RC_BOM + 'R2,R_0805,1,10k,SynthCo,SR-10K,C100001\nU1,SOT5,1,REG,SynthCo,SU-REG,C100003\n'
        cpl = RC_CPL + 'R2,0,0,Top,0\nU1,0,0,Top,0\n'
        extra = self.run_case(project=project, native_netlist=True, files=package(bom, cpl),
                              non_dnp=['C1', 'J1', 'R1'])
        self.assertStatus(extra, 'ok', 'defect')
        self.assertIn(('designator_parity', 'designator R2 absent from board'), self.defects(extra))
        self.assertIn(('designator_parity', 'designator U1 absent from board'), self.defects(extra))

    def test_netlist_and_board_membership_must_agree(self):
        orphan = self.run_case(netlist=netlist_xml(dict(NETLIST_COMPONENTS, R7=[])))
        self.assertStatus(orphan, 'malformed', 'unknown', 'netlist component R7 has no board footprint')
        excluded = self.run_case(netlist=netlist_xml(dict(NETLIST_COMPONENTS, R7=['exclude_from_board'])))
        self.assertStatus(excluded, 'ok', 'pass')
        duplicate = netlist_xml().replace('<comp ref="R2">', '<comp ref="R1">')
        self.assertStatus(self.run_case(netlist=duplicate), 'malformed', 'unknown', 'duplicate')

    def test_board_only_footprints_need_no_netlist_entry_unless_assembled(self):
        mechanical = FOOTPRINTS + [('Synth:Logo', 'G1', 'F.Cu', 'smd board_only exclude_from_pos_files '
                                    'exclude_from_bom', [])]
        result = self.run_case(board=board_text(mechanical), non_dnp=NON_DNP + ['G1'])
        self.assertStatus(result, 'ok', 'pass')
        assembled = FOOTPRINTS + [('Synth:Logo', 'G1', 'F.Cu', 'smd board_only', [])]
        result = self.run_case(board=board_text(assembled), non_dnp=NON_DNP + ['G1'])
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn('board_only_assembled', {g['code'] for g in result['details']['coverage_gaps']})

    def test_sealed_geometry_must_agree_with_captured_board_and_carry_flags(self):
        def flip(path):
            geo = read_json(path)
            next(f for f in geo['footprints'] if f['ref'] == 'C1')['layer'] = 'F.Cu'
            write_json(path, geo)
        self.assertStatus(self.run_case(geometry_post=flip), 'malformed', 'unknown',
                          'disagrees with the sealed geometry')

        def legacy(path):
            geo = read_json(path)
            for footprint in geo['footprints']:
                del footprint['exclude_from_bom']
            write_json(path, geo)
        self.assertStatus(self.run_case(geometry_post=legacy), 'unsupported', 'unknown',
                          'lacks footprint exclusion attributes')

    # --------------------------------------------------- 3D models (probe 3)

    def test_astra73_name_only_embedded_record_is_defect(self):
        # Astra's saved probe: a (file (name) (type model)) record without data
        # previously resolved; a native pcbnew reload and save discards it.
        board = board_text(embedded='\n\t\t(file\n\t\t\t(name "sot5.step")\n\t\t\t(type model)\n\t\t)')
        result = self.run_case(board=board)
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn(('model_unresolved', "footprint U1 model 'kicad-embed://sot5.step': embedded "
                       "'sot5.step' has no data; native save discards an empty record"), self.defects(result))
        with tempfile.TemporaryDirectory() as tmp:
            source, saved = Path(tmp) / 'b.kicad_pcb', Path(tmp) / 'saved.kicad_pcb'
            source.write_text(board)
            pcbnew_resave(source, saved)
            self.assertEqual(records(parse_sexpr(saved.read_bytes())), {})

    def test_embedded_record_failures_are_defects_or_unknown(self):
        payload = owned_step('sot5.step')
        frame = base64.b64encode(raw_frame(payload)).decode()
        # A valid zstd frame (window descriptor, one raw block) that omits its content size.
        unsized = base64.b64encode(b'\x28\xb5\x2f\xfd\x00\x00' + ((len(payload) << 3) | 1).to_bytes(3, 'little')
                                   + payload).decode()
        cases = {
            'empty data': (embedded_file('sot5.step', data='||'), 'model_unresolved', 'has no data'),
            'not base64': (embedded_file('sot5.step', data='|@@@@|'), 'model_unresolved', 'not base64'),
            'corrupt': (embedded_file('sot5.step', data='|AAAAAAAA' + frame + '|'), 'model_unresolved',
                        'does not decompress'),
            'truncated': (embedded_file('sot5.step', data='|' + frame[:40] + '|'), 'model_unresolved',
                          'does not decompress'),
            'unsized': (embedded_file('sot5.step', data='|' + unsized + '|'), 'model_unresolved',
                        'does not declare its content size'),
            'checksum': (embedded_file('sot5.step', checksum='0' * 32), 'model_unresolved', 'checksum mismatch'),
            'no checksum': (embedded_file('sot5.step', checksum=''), 'model_unresolved', 'lacks one checksum'),
            'absent': (embedded_file('other.step'), 'model_unresolved', "no embedded file 'sot5.step'"),
        }
        for name, (records_text, code, text) in cases.items():
            with self.subTest(name):
                result = self.run_case(board=board_text(embedded=records_text))
                self.assertStatus(result, 'ok', 'defect')
                found = [d for d in result['details']['defects'] if d['code'] == code]
                self.assertTrue(found and text in found[0]['locator'], result['details']['defects'])
        differing = embedded_file('sot5.step') + embedded_file('sot5.step', b'other content\n')
        result = self.run_case(board=board_text(embedded=differing))
        self.assertStatus(result, 'unsupported', 'unknown', 'differing embedded records')
        legacy = embedded_file('sot5.step', checksum=hashlib.sha256(payload).hexdigest())
        self.assertStatus(self.run_case(board=board_text(embedded=legacy)), 'ok', 'pass')

    def test_unresolved_model_is_defect_and_uncaptured_library_is_unknown(self):
        native = [list(fp) for fp in FOOTPRINTS]
        native[2][4] = ['${KIPRJMOD}/models/c.step']
        result = self.run_case(board=board_text([tuple(fp) for fp in native]))
        self.assertStatus(result, 'ok', 'defect')
        self.assertIn(('model_unresolved', "footprint C1 model '${KIPRJMOD}/models/c.step': "
                       "models/c.step is not a captured input"), self.defects(result))
        native[2][4] = ['kicad-embed://c.step']
        self.assertIn('model_unresolved', self.codes(self.run_case(board=board_text([tuple(fp) for fp in native]))))
        native[2][4] = ['${KICAD10_3DMODEL_DIR}/Synth.3dshapes/C_0603.step']
        library = self.run_case(board=board_text([tuple(fp) for fp in native]))
        self.assertStatus(library, 'unsupported', 'unknown', 'KICAD10_3DMODEL_DIR')
        native[2][4] = ['/opt/elsewhere/c.step']
        self.assertStatus(self.run_case(board=board_text([tuple(fp) for fp in native])), 'unsupported', 'unknown')
        native[2][4] = ['../../outside/c.step']
        self.assertStatus(self.run_case(board=board_text([tuple(fp) for fp in native])), 'unsupported', 'unknown')

    def test_model_defect_with_other_gap_stays_defect(self):
        native = [list(fp) for fp in FOOTPRINTS]
        native[2][4] = ['models/missing.step', '${KICAD10_3DMODEL_DIR}/x.step']
        result = self.run_case(board=board_text([tuple(fp) for fp in native]))
        self.assertStatus(result, 'ok', 'defect')
        self.assertEqual(result['details']['coverage_gaps'][0]['code'], 'model_coverage')

    # ------------------------------------------- stock model libraries (AGENTEE-124)

    def test_stock_footprints_resolve_against_the_captured_pinned_library(self):
        # The owned RC control board places real stock KiCad 10 footprints whose
        # models are ${KICAD10_3DMODEL_DIR} paths; no KiCad 3D models are captured.
        project = rc_project((RC_CONTROL / 'rc_filter.kicad_pcb').read_bytes())
        paths = sorted(RC_MODELS)

        def run(**kwargs):
            return self.run_case(project=project, native_netlist=True, files=package(RC_BOM, RC_CPL),
                                 non_dnp=['C1', 'J1', 'R1'], **kwargs)
        missing_root = run()
        self.assertStatus(missing_root, 'unsupported', 'unknown', 'KICAD10_3DMODEL_DIR')
        self.assertEqual({m['status'] for m in missing_root['details']['models']['records']}, {'uncovered'})
        self.assertEqual(missing_root['details']['models']['libraries'], {})

        result = run(consistency_params={'export_check': 'export', 'model_libraries': STOCK_ROOTS},
                     extra_inputs=stock_library(paths))
        self.assertStatus(result, 'ok', 'pass', '3 3D model path(s) resolved')
        records_by_ref = {m['ref']: m for m in result['details']['models']['records']}
        self.assertEqual(set(records_by_ref), {'C1', 'J1', 'R1'})
        for ref, record in records_by_ref.items():
            relative = record['path'][len('${KICAD10_3DMODEL_DIR}/'):]
            self.assertIn(relative, RC_MODELS)
            self.assertEqual(record['status'], 'resolved', record)
            self.assertEqual(record['sha256'], hashlib.sha256(owned_step(PurePosixPath(relative).name)).hexdigest())
            self.assertEqual(record['library'], {'variable': 'KICAD10_3DMODEL_DIR', 'receipt': STOCK_RECEIPT,
                                                 'member': f'lib3d/{relative}', 'commit': STOCK_REVISIONS[0]})
        library = result['details']['models']['libraries']['KICAD10_3DMODEL_DIR']
        self.assertEqual(library, {'receipt': STOCK_RECEIPT, 'receipt_sha256': self.manifest['files'][STOCK_RECEIPT]})
        # The receipt is an immutable baseline dependency; members are bound by its hashes.
        self.assertIn(STOCK_RECEIPT, load_baseline(self.run_dir)['files'])
        freeze_evidence(self.run_dir, self.results)
        verify_evidence(self.run_dir, self.results, required=True)

    def test_unmapped_unpinned_or_mismatched_stock_models_stay_unknown(self):
        model = 'Synth.3dshapes/C_0603.step'
        native = [list(fp) for fp in FOOTPRINTS]
        native[2][4] = [f'${{KICAD10_3DMODEL_DIR}}/{model}']
        board = board_text([tuple(fp) for fp in native])
        cases = {
            'other root only': ({'KICAD9_3DMODEL_DIR': STOCK_RECEIPT}, stock_library([model]),
                                "['KICAD10_3DMODEL_DIR'] not mapped to a captured pinned model library"),
            'not in receipt': (STOCK_ROOTS, stock_library([model], listed=[]),
                               f'{model} is not in the captured KICAD10_3DMODEL_DIR library'),
            'member not captured': (STOCK_ROOTS, stock_library([model], captured=[]),
                                    f'lib3d/{model} listed by KICAD10_3DMODEL_DIR library'),
            'hash mismatch': (STOCK_ROOTS, stock_library([model], content={model: b'ISO-10303-21; other\n'}),
                              f'lib3d/{model} differs from its pinned hash'),
            'unpinned': (STOCK_ROOTS, stock_library([model], provenance='existing-local'),
                         'has no pinned upstream revision'),
            'malformed receipt': (STOCK_ROOTS, {**stock_library([model]), STOCK_RECEIPT: b'{"schema": "x"}'},
                                  'unsupported model cache receipt'),
        }
        for name, (roots, library, reason) in cases.items():
            with self.subTest(name):
                result = self.run_case(board=board, extra_inputs=library,
                                       consistency_params={'export_check': 'export', 'model_libraries': roots})
                self.assertStatus(result, 'unsupported', 'unknown')
                record = next(m for m in result['details']['models']['records'] if m['ref'] == 'C1')
                self.assertEqual(record['status'], 'uncovered')
                self.assertNotIn('sha256', record)
                self.assertIn(reason, record['reason'])
                self.assertEqual(result['details']['coverage_gaps'][0]['code'], 'model_coverage')

    def test_legacy_stock_variable_resolves_only_through_its_own_declared_root(self):
        model = 'Synth.3dshapes/C_0603.wrl'
        native = [list(fp) for fp in FOOTPRINTS]
        native[2][4] = [f'${{KICAD9_3DMODEL_DIR}}/{model}']
        library = stock_library([model], root='lib9')
        params = {'export_check': 'export', 'model_libraries': {'KICAD9_3DMODEL_DIR': 'lib9/.agent-ee-models.json'}}
        result = self.run_case(board=board_text([tuple(fp) for fp in native]), extra_inputs=library,
                               consistency_params=params)
        self.assertStatus(result, 'ok', 'pass')
        record = next(m for m in result['details']['models']['records'] if m['ref'] == 'C1')
        self.assertEqual(record['library']['member'], f'lib9/{model}')
        # A stock model that is absent from its pinned library does not hide another model's defect.
        native[2][4] = ['models/missing.step', f'${{KICAD9_3DMODEL_DIR}}/Synth.3dshapes/absent.step']
        result = self.run_case(board=board_text([tuple(fp) for fp in native]), extra_inputs=library,
                               consistency_params=params)
        self.assertStatus(result, 'ok', 'defect')
        self.assertEqual(self.codes(result), {'model_unresolved'})
        self.assertEqual([g['code'] for g in result['details']['coverage_gaps']], ['model_coverage'])

    # ---------------------------------------------------------------- capability

    def test_capability_requires_ordered_export_netlist_and_geometry(self):
        specs = [{'id': 'netlist', 'kind': 'netlist'},
                 {'id': 'geometry', 'kind': 'geometry'},
                 {'id': 'export', 'kind': 'fab_export', 'params': {'netlist_check': 'netlist'}},
                 {'id': 'fab', 'kind': 'fab_consistency', 'params': {'export_check': 'export'}}]
        scope = {'board': 'board.kicad_pcb', 'schematic': 'board.kicad_sch', 'checks': specs}
        files = {'board.kicad_pcb': 'a' * 64, 'board.kicad_sch': 'b' * 64}

        def describe(checks):
            fab = next(c for c in checks if c['id'] == 'fab')
            return describe_check(fab, check_specs=checks, files=files, scope=dict(scope, checks=checks))
        good = describe(specs)
        self.assertEqual(good['status'], 'supported', good)
        self.assertTrue(good['can_evaluate_bound'])
        self.assertEqual([p['check_id'] for p in good['prerequisites']], ['export', 'netlist', 'geometry'])
        self.assertEqual(describe([specs[3], specs[0], specs[1], specs[2]])['status'], 'unsupported')
        extra = copy.deepcopy(specs)
        extra[3]['params']['regenerate'] = True
        self.assertEqual(describe(extra)['status'], 'unsupported')
        no_netlist = copy.deepcopy(specs)
        no_netlist[2]['params'] = {}
        self.assertEqual(describe(no_netlist)['status'], 'unsupported')
        self.assertEqual(describe([specs[0], specs[2], specs[3]])['status'], 'unsupported')
        self.assertEqual(describe([specs[0], specs[2], specs[3], specs[1]])['status'], 'unsupported')

        files[STOCK_RECEIPT] = 'c' * 64
        stock = copy.deepcopy(specs)
        stock[3]['params']['model_libraries'] = dict(STOCK_ROOTS)
        declared = describe(stock)
        self.assertEqual(declared['status'], 'supported', declared)
        self.assertEqual(declared['immutable_inputs'], [STOCK_RECEIPT])
        self.assertEqual(good['immutable_inputs'], [])
        for roots in ({}, [STOCK_RECEIPT], {'KIPRJMOD': STOCK_RECEIPT}, {'MY_MODELS': STOCK_RECEIPT},
                      {'KICAD10_3DMODEL_DIR': 'lib3d/receipt.json'}, {'KICAD10_3DMODEL_DIR': 'other/.agent-ee-models.json'},
                      {'KICAD10_3DMODEL_DIR': '/abs/.agent-ee-models.json'}):
            with self.subTest(roots=roots):
                stock[3]['params']['model_libraries'] = roots
                self.assertEqual(describe(stock)['status'], 'unsupported')


class CoordinatePrecisionTests(unittest.TestCase):
    def test_csv_precision_cannot_round_an_outside_coordinate_onto_the_boundary(self):
        geometry = [('R1', 'top', False, False, False, False, (116123456, 106654321))]
        cases = [('116.133456', 'pass'), ('116.1334559999999999999999999999', 'pass'),
                 ('116.1334560000000000000000000001', 'defect'),
                 ('116.1134559999999999999999999999', 'defect')]
        for x, outcome in cases:
            with self.subTest(x=x):
                defects = []
                cpl = fab_consistency.check_cpl([['R1', x, '-106.654321', 'Top', '0']], defects)
                report = fab_consistency.check_coordinates(cpl, geometry, {'R1': [{}]}, defects)
                self.assertEqual(report['records'][0]['outcome'], outcome)
                self.assertEqual(bool(defects), outcome == 'defect')


class NativeFabPipelineTests(unittest.TestCase):
    """Owned native 23→73 pipeline and sealed scope/3 witnesses, with file fault injection."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-consistency-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(REPO / 'fixtures/fab-export', self.source)
        (self.source / 'owned.step').write_bytes(owned_step('owned.step'))
        for name in ('fab.kicad_pcb', 'fab4.kicad_pcb'):
            board = self.source / name
            board.write_text(board.read_text().replace(
                '(attr smd)', '(attr smd)\n\t\t(model "${KIPRJMOD}/owned.step")', 1))
        self.runs = 0

    def run_case(self, *, board='fab.kicad_pcb', mutate=None, before=None, rule=None, model_libraries=None, copper_features=None, bom_variant=None):
        scope = read_json(REPO / 'fixtures/fab-export/control.scope.json')
        scope.update(schema='agent-ee.scope/3', board='design/' + board)
        if copper_features is not None:
            scope['checks'][1]['params']['copper_features'] = copper_features
        if bom_variant is not None:
            scope['checks'][1]['params']['bom_variant'] = bom_variant
            scope['inputs'][0]['include'].append('fab.kicad_pro')
        if rule:
            scope['checks'][1]['params']['assembly_exclusion'] = rule
        scope['inputs'][0]['path'] = str(self.source)
        scope['inputs'][0]['include'] += ['owned.step', 'lib3d/*']
        scope['checks'].insert(1, {'id': 'geometry', 'kind': 'geometry'})
        scope['checks'].append({'id': 'consistency', 'kind': 'fab_consistency',
                                'params': {'export_check': 'fab', **(
                                    {'model_libraries': model_libraries} if model_libraries else {})}})
        req = scope['baseline']['requirements'][0]
        req.update(intent='acceptance', checks=['fab', 'consistency'])
        scope['baseline']['requirements'].append({**req, 'id': 'I-native', 'intent': 'informational',
            'statement': 'Retain native board and schematic observations', 'checks': ['netlist', 'geometry']})
        self.runs += 1
        case = self.root / str(self.runs)
        case.mkdir()
        write_json(case / 'scope.json', scope)
        self.run_dir = case / 'run'
        self.run_dir.mkdir()
        capture(case / 'scope.json', self.run_dir)
        if before:
            before(self.run_dir)

        # The control runs the unmodified production registry. Defect cases
        # change producer files before coordinator sealing, retaining real tool runs.
        if mutate:
            producer = checks.CHECK_FUNCS['fab_export']

            def damaged_export(ctx):
                producer(ctx)
                stage = ctx.work / 'fab'
                mutate(ctx, stage)
                manifest = read_json(stage / 'manifest.json')
                manifest['files'] = {rel: digest(stage / rel) for rel in manifest['files']
                                      if (stage / rel).is_file()}
                write_json(stage / 'manifest.json', manifest)

            with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': damaged_export}):
                self.results = run_checks(self.run_dir, time.monotonic() + 300)
        else:
            self.results = run_checks(self.run_dir, time.monotonic() + 300)
        self.assertTrue(all(r['execution'] == 'ok' for r in self.results[:2]), self.results)
        freeze_evidence(self.run_dir, self.results)
        verify_evidence(self.run_dir, self.results, required=True)
        return self.results[-1]

    def test_owned_copper_jumper_passes_both_fab_gates(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        result = self.run_case(copper_features=['JP1'])
        self.assertEqual([(r['execution'], r['outcome']) for r in self.results[2:]],
                         [('ok', 'pass'), ('ok', 'pass')], self.results)
        self.assertEqual(result['details']['copper_features'], ['JP1'])
        self.assert_witness(result, accepted=False)

    def test_stock_description_and_datasheet_pass_both_fab_gates(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        for rel in ('fab.kicad_pcb', 'fab.kicad_sch'):
            path = self.source / rel
            fields = ''.join(f'(property "{name}" "{value}" (at 0 0 0) '
                             '(effects (font (size 1 1))))' for name, value in
                             [('Description', 'Solder jumper, 2-pad, open'),
                              ('Datasheet', 'https://example.invalid/owned-jumper')])
            if rel.endswith('pcb'):
                fields = fields.replace('(at 0 0 0)', '(at 0 0) (layer "F.Fab")')
            path.write_text(path.read_text().replace('(property "Reference" "JP1"',
                fields + '(property "Reference" "JP1"'))
        result = self.run_case(copper_features=['JP1'])
        self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)

    def test_nonstandard_fields_refused_and_consistency_replays_allowlist(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        board = self.source / 'fab.kicad_pcb'
        schematic = self.source / 'fab.kicad_sch'
        original_board, original_sch = board.read_text(), schematic.read_text()
        producer = checks.CHECK_FUNCS['fab_export']

        def bypass(ctx):
            with mock.patch('agent_ee.fab_export.copper_feature_problems', return_value=[]), \
                    mock.patch('agent_ee.fab_export.copper_schematic_problems', return_value=[]):
                producer(ctx)

        for source in ('board', 'hidden symbol', 'variant'):
            with self.subTest(source=source):
                field = '(property "Supplier Part Number" "BUY-ME" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'
                board.write_text(original_board if source != 'board' else original_board.replace(
                    '(property "Reference" "JP1"', field.replace('(at 0 0 0)', '(at 0 0) (layer "F.Fab")') +
                    '(property "Reference" "JP1"'))
                sch = original_sch
                if source == 'hidden symbol':
                    sch = sch.replace('(property "Reference" "JP1"', field + '(property "Reference" "JP1"')
                elif source == 'variant':
                    sch = sch.replace('(reference "JP1") (unit 1)',
                        '(reference "JP1") (unit 1) (variant (name "ALT") '
                        '(field (name "Supplier Part Number") (value "BUY-ME")))')
                schematic.write_text(sch)
                variant = 'ALT' if source == 'variant' else None
                if variant:
                    write_json(self.source / 'fab.kicad_pro', {'meta': {'version': 3},
                               'schematic': {'variants': [{'name': variant}]}})
                self.run_case(copper_features=['JP1'], bom_variant=variant)
                self.assertEqual((self.results[2]['execution'], self.results[2]['outcome']),
                                 ('unsupported', 'unknown'), self.results[2])
                with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': bypass}):
                    result = self.run_case(copper_features=['JP1'], bom_variant=variant)
                self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('Supplier Part Number', result['summary'])

    def test_owned_custom_pad_solder_jumper_passes_both_fab_gates(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source, custom=True)
        result = self.run_case(copper_features=['JP1'])
        self.assertEqual([(r['execution'], r['outcome']) for r in self.results[2:]],
                         [('ok', 'pass'), ('ok', 'pass')], self.results)
        self.assertEqual(result['details']['copper_features'], ['JP1'])

    def test_reviewer_dnp_and_missing_native_component_probes(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        board = self.source / 'fab.kicad_pcb'
        schematic = self.source / 'fab.kicad_sch'
        original_board, original_sch = board.read_text(), schematic.read_text()
        schematic.write_text('\n'.join(
            line.replace('(dnp no)', '(dnp yes)') if '(property "Reference" "JP1"' in line else line
            for line in original_sch.splitlines()) + '\n')
        board.write_text(original_board.replace('(attr smd exclude_from_bom exclude_from_pos_files)',
            '(attr smd dnp exclude_from_bom exclude_from_pos_files)'))
        self.run_case(copper_features=['JP1'])
        self.assertEqual([(r['execution'], r['outcome']) for r in self.results[2:]],
                         [('ok', 'pass'), ('ok', 'pass')], self.results)
        board.write_text(original_board)
        schematic.write_text(original_sch.replace('"JP1"', '"JP2"'))
        self.run_case(copper_features=['JP1'])
        export = self.results[2]
        self.assertEqual((export['execution'], export['outcome']), ('unsupported', 'unknown'), export)
        self.assertIn('needs a native on-board component', export['summary'])

    def test_consistency_rejects_reviewer_geometry_probes_after_producer_bypass(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        board = self.source / 'fab.kicad_pcb'
        original = board.read_text()
        producer = checks.CHECK_FUNCS['fab_export']

        def bypass_copper_qualification(ctx):
            with mock.patch('agent_ee.fab_export.copper_feature_problems', return_value=[]):
                producer(ctx)

        attr = '(attr smd exclude_from_bom exclude_from_pos_files)'
        for fault in ('zone', 'copper graphic', 'inner pad'):
            with self.subTest(fault=fault):
                text = original
                if fault == 'zone':
                    text = text.replace(attr, attr + '(zone (net 0) (layer "F.Cu"))')
                elif fault == 'copper graphic':
                    text = text.replace(attr, attr + '(fp_poly (pts (xy 0 0) (xy 1 0) (xy 0 1)) '
                                        '(stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))')
                else:
                    at = text.index('(property "Reference" "JP1"')
                    text = text[:at] + text[at:].replace('"F.Cu" "F.Mask" "F.Paste"', '"In1.Cu"', 1)
                board.write_text(text)
                with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': bypass_copper_qualification}):
                    result = self.run_case(copper_features=['JP1'])
                self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)

    def test_mfr_part_number_is_refused_by_export_and_independent_consistency(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        schematic = self.source / 'fab.kicad_sch'
        schematic.write_text(schematic.read_text().replace('(property "Reference" "JP1"',
            '(property "Mfr Part Number" "BUY-ME" (at 0 0 0) (effects (font (size 1 1)))) '
            '(property "Reference" "JP1"'))
        self.run_case(copper_features=['JP1'])
        export = self.results[2]
        self.assertEqual((export['execution'], export['outcome']), ('unsupported', 'unknown'), export)
        self.assertIn('Mfr Part Number', export['summary'])
        producer = checks.CHECK_FUNCS['fab_export']

        def bypass_schematic_qualification(ctx):
            with mock.patch('agent_ee.fab_export.copper_schematic_problems', return_value=[]), \
                    mock.patch('agent_ee.fab_export.copper_feature_problems', return_value=[]):
                producer(ctx)

        with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': bypass_schematic_qualification}):
            result = self.run_case(copper_features=['JP1'])
        self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('Mfr Part Number', result['summary'])

    def test_copper_jumper_undeclared_model_and_mpn_are_refused(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        board = self.source / 'fab.kicad_pcb'
        schematic = self.source / 'fab.kicad_sch'
        original_board, original_sch = board.read_text(), schematic.read_text()
        for fault in ('undeclared', 'model', 'board MPN', 'native MPN', 'native LCSC'):
            with self.subTest(fault=fault):
                text, sch = original_board, original_sch
                if fault == 'model':
                    text = text.replace('(attr smd exclude_from_bom exclude_from_pos_files)',
                                        '(attr smd exclude_from_bom exclude_from_pos_files) (model "${KIPRJMOD}/owned.step")')
                elif fault == 'board MPN':
                    text = text.replace('(property "Reference" "JP1"',
                                        '(property "MPN" "BUY-ME" (at 0 0) (layer "F.Fab")) (property "Reference" "JP1"')
                elif fault.startswith('native'):
                    field = 'MPN' if fault == 'native MPN' else 'LCSC'
                    sch = sch.replace('(property "Reference" "JP1"',
                                      f'(property "{field}" "BUY-ME" (at 0 0 0) (effects (font (size 1 1)))) '
                                      '(property "Reference" "JP1"')
                board.write_text(text)
                schematic.write_text(sch)
                self.run_case(copper_features=None if fault == 'undeclared' else ['JP1'])
                export = self.results[2]
                self.assertEqual((export['execution'], export['outcome']), ('unsupported', 'unknown'), export)
                self.assertNotEqual(self.results[-1]['outcome'], 'pass')

    def test_consistency_requalifies_copper_even_when_producer_reports_pass(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        board = self.source / 'fab.kicad_pcb'
        schematic = self.source / 'fab.kicad_sch'
        original_board, original_sch = board.read_text(), schematic.read_text()
        producer = checks.CHECK_FUNCS['fab_export']

        def bypass_copper_qualification(ctx):
            # Fault injection only in the producer; real native exports and sealing
            # still run. The gate must qualify captured bytes independently.
            with mock.patch('agent_ee.fab_export.copper_feature_problems', return_value=[]), \
                    mock.patch('agent_ee.fab_export.copper_schematic_problems', return_value=[]):
                producer(ctx)

        for fault in ('model', 'board MPN', 'native MPN', 'native LCSC'):
            with self.subTest(fault=fault):
                addition = ('(model "${KIPRJMOD}/owned.step")' if fault == 'model' else
                            '(property "MPN" "BUY-ME" (at 0 0) (layer "F.Fab"))' if fault == 'board MPN' else '')
                board.write_text(original_board.replace('(attr smd exclude_from_bom exclude_from_pos_files)',
                    '(attr smd exclude_from_bom exclude_from_pos_files)' + addition))
                field = 'LCSC' if fault == 'native LCSC' else 'MPN'
                sch = original_sch if not fault.startswith('native') else original_sch.replace(
                    '(property "Reference" "JP1"',
                    f'(property "{field}" "BUY-ME" (at 0 0 0) (effects (font (size 1 1)))) '
                    '(property "Reference" "JP1"')
                schematic.write_text(sch)
                with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': bypass_copper_qualification}):
                    result = self.run_case(copper_features=['JP1'])
                self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('copper feature JP1', result['summary'])

    def test_variant_only_mpn_is_refused_by_both_gates(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        schematic = self.source / 'fab.kicad_sch'
        schematic.write_text(schematic.read_text().replace('(reference "JP1") (unit 1)',
            '(reference "JP1") (unit 1) (variant (name "ALT") '
            '(field (name "Partnumber") (value "BUY-ME")))'))
        write_json(self.source / 'fab.kicad_pro', {'meta': {'version': 3},
                   'schematic': {'variants': [{'name': 'ALT'}]}})
        self.run_case(copper_features=['JP1'], bom_variant='ALT')
        self.assertEqual((self.results[2]['execution'], self.results[2]['outcome']),
                         ('unsupported', 'unknown'), self.results[2])
        producer = checks.CHECK_FUNCS['fab_export']

        def bypass_schematic_qualification(ctx):
            with mock.patch('agent_ee.fab_export.copper_schematic_problems', return_value=[]):
                producer(ctx)

        with mock.patch.dict(checks.CHECK_FUNCS, {'fab_export': bypass_schematic_qualification}):
            result = self.run_case(copper_features=['JP1'], bom_variant='ALT')
        self.assertEqual(self.results[2]['outcome'], 'pass', self.results[2])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn('schematic/variant field Partnumber', result['summary'])

    def test_copper_jumper_in_bom_or_cpl_is_a_parity_defect(self):
        from tests.test_fab_export import copper_fixture
        copper_fixture(self.source)
        for name, row in (('bom.csv', 'JP1,SolderJumper,1,Jumper,Owned,JP,C900123\n'),
                          ('cpl.csv', 'JP1,120,-100,Top,0\n')):
            with self.subTest(file=name):
                def inject(ctx, stage):
                    path = stage / name
                    path.write_text(path.read_text() + row)
                result = self.run_case(copper_features=['JP1'], mutate=inject)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertIn('designator_parity', {d['code'] for d in result['details']['defects']})

    def assert_witness(self, result, *, accepted):
        evidence = [{'path': 'checks/consistency.json', 'locator': 'details.defects'}]
        witness = [{'requirement_id': 'R-fab', 'check_id': 'consistency'}]
        if not accepted:
            self.assertEqual(collect_findings([], [result]), [])
            with self.assertRaisesRegex(ValueError, 'freshly failed criterion'):
                validate_witnesses(self.run_dir, witness, evidence)
            return
        validate_witnesses(self.run_dir, witness, evidence)
        findings = collect_findings([], [result])
        self.assertEqual(len(findings), 1)
        decision = {'schema': 'agent-ee.decisions/1', 'candidate_id': result['candidate_id'],
                    'run_id': 'native-fab-qualification', 'decisions': [{
                        'finding_id': 'check/consistency', 'disposition': 'accepted',
                        'reason': 'Sealed package invariant violation', 'evidence': evidence}]}
        self.assertEqual(adjudicate(findings, decision, self.run_dir)[0]['status'], 'accepted')
        # The same bytes cannot support an unrelated informational obligation.
        with self.assertRaisesRegex(ValueError, 'trace to a baseline requirement'):
            validate_witnesses(self.run_dir, [{'requirement_id': 'I-native', 'check_id': 'consistency'}], evidence)

    @staticmethod
    def edit_csv(stage, name, edit):
        rows = list(csv.reader(io.StringIO((stage / name).read_text())))
        edit(rows)
        output = io.StringIO(newline='')
        csv.writer(output, lineterminator='\n').writerows(rows)
        (stage / name).write_text(output.getvalue())

    def test_native_scope3_two_and_four_layer_packages_pass_and_seal(self):
        for board, count in (('fab.kicad_pcb', 9), ('fab4.kicad_pcb', 11)):
            with self.subTest(board=board):
                result = self.run_case(board=board)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
                self.assertEqual(len(result['details']['plots']), count)
                self.assertEqual(len(result['details']['build_logs']), 5)
                self.assertEqual(result['details']['models']['records'][0]['status'], 'resolved')
                self.assertEqual(len(result['details']['coordinates']['records']), 6)
                predicates = load_baseline(self.run_dir)['coverage']['checks']['consistency']['predicates']
                self.assertIn('fab-package:cpl-coordinates', predicates)
                self.assertIn('fab-package:build-log', predicates)
                self.assert_witness(result, accepted=False)

    def test_native_scope3_stock_model_resolves_through_frozen_library_receipt(self):
        model = 'Synth.3dshapes/owned.step'
        board = self.source / 'fab.kicad_pcb'
        board.write_text(board.read_text().replace('${KIPRJMOD}/owned.step', f'${{KICAD10_3DMODEL_DIR}}/{model}'))
        for rel, data in stock_library([model]).items():
            (self.source / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source / rel).write_bytes(data)
        receipt = 'design/' + STOCK_RECEIPT
        self.assertEqual(self.run_case()['execution'], 'unsupported')
        result = self.run_case(model_libraries={'KICAD10_3DMODEL_DIR': receipt})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        record = result['details']['models']['records'][0]
        self.assertEqual((record['status'], record['library']['member'], record['sha256']),
                         ('resolved', f'design/lib3d/{model}', hashlib.sha256(owned_step('owned.step')).hexdigest()))
        baseline = load_baseline(self.run_dir)
        self.assertIn(receipt, baseline['files'])
        self.assertEqual(baseline['coverage']['checks']['consistency']['immutable_inputs'], [receipt])
        self.assert_witness(result, accepted=False)

    def test_native_package_subpredicate_defects_qualify_sealed_witnesses(self):
        def csv_mutation(name, edit):
            return lambda ctx, stage: self.edit_csv(stage, name, edit)

        cases = {
            'range': ('compressed_range', csv_mutation('bom.csv', lambda r: r[1].__setitem__(0, 'C1-C10'))),
            'duplicate': ('duplicate_designator', csv_mutation('bom.csv', lambda r: r[2].__setitem__(0, 'C1'))),
            'empty ref': ('empty_designator', csv_mutation('bom.csv', lambda r: r[2].__setitem__(0, ''))),
            'missing cpl': ('designator_parity', csv_mutation('cpl.csv', lambda r: r.pop(1))),
            'missing bom': ('designator_parity', csv_mutation('bom.csv', lambda r: r.pop(1))),
            'not on board': ('designator_parity', csv_mutation('cpl.csv', lambda r: r.append(
                ['R99', '120', '-110', 'Top', '0']))),
            'empty lcsc': ('empty_lcsc', csv_mutation('bom.csv', lambda r: r[1].__setitem__(6, ''))),
            'lcsc footprint': ('lcsc_multiple_footprints', csv_mutation('bom.csv',
                lambda r: r[2].__setitem__(6, r[1][6]))),
            'side': ('cpl_side', csv_mutation('cpl.csv',
                lambda r: next(row for row in r if row[0] == 'R2').__setitem__(3, 'Top'))),
            'coordinates': ('cpl_coordinates', csv_mutation('cpl.csv',
                lambda r: next(row for row in r if row[0] == 'R2').__setitem__(1, '116.010001'))),
        }
        for name, (code, mutate) in cases.items():
            with self.subTest(invariant=name):
                result = self.run_case(mutate=mutate)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertIn(code, {d['code'] for d in result['details']['defects']})
                self.assert_witness(result, accepted=True)

        (self.source / 'owned.step').unlink()
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('model_unresolved', {d['code'] for d in result['details']['defects']})
        self.assert_witness(result, accepted=True)

    def test_native_schematic_board_dnp_disagreement_is_a_named_parity_defect(self):
        schematic = self.source / 'fab.kicad_sch'
        board = self.source / 'fab.kicad_pcb'
        original_schematic, original_board = schematic.read_text(), board.read_text()
        for dnp_source in ('schematic', 'board'):
            with self.subTest(dnp_source=dnp_source):
                # J1 is the fixture's first schematic instance and only through-hole footprint.
                schematic.write_text(original_schematic.replace('(dnp no)', '(dnp yes)', 1)
                                     if dnp_source == 'schematic' else original_schematic)
                board.write_text(original_board.replace('(attr through_hole)', '(attr through_hole dnp)', 1)
                                 if dnp_source == 'board' else original_board)
                result = self.run_case()
                self.assertEqual((self.results[-2]['execution'], self.results[-2]['outcome']), ('ok', 'pass'))
                manifest = read_json(self.run_dir / 'work/fab/fab/manifest.json')
                self.assertEqual('J1' in manifest['designators']['board_non_dnp'], dnp_source == 'schematic')
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertIn('J1', result['location'])
                self.assertIn('designator_parity', {d['code'] for d in result['details']['defects']})
                # The package follows the board flags, so BOM, CPL and board agree; the named
                # defect is the schematic/board disagreement itself.
                self.assertEqual(result['details']['designators']['parity_violations'], [])
                self.assertEqual(result['details']['designators']['dnp_disagreements'], [
                    {'designator': 'J1', 'schematic_dnp': dnp_source == 'schematic',
                     'board_dnp': dnp_source == 'board'}])
                self.assertEqual(self.results[-2]['details']['dnp_disagreements'],
                                 result['details']['designators']['dnp_disagreements'])
                if dnp_source == 'schematic':
                    self.assertIn('netlist_exclusion_assembled', {d['code'] for d in result['details']['defects']})
                self.assert_witness(result, accepted=True)

    def test_native_rule_exclusion_is_rederived_and_held_to_parity(self):
        from tests.test_fab_export import RULE, mark_board, mark_schematic
        schematic, board = self.source / 'fab.kicad_sch', self.source / 'fab.kicad_pcb'
        schematic.write_text(mark_schematic(schematic.read_text(), 'J1', unbind=True))
        board.write_text(mark_board(board.read_text(), 'J1'))
        result = self.run_case(rule=RULE)
        self.assertEqual((self.results[-2]['execution'], self.results[-2]['outcome']), ('ok', 'pass'))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        designators = result['details']['designators']
        self.assertEqual((designators['excluded'], designators['exclusion_rule'], designators['board_assembled']),
                         ([{'designator': 'J1', 'rule': RULE}], RULE, 5))
        self.assertEqual(designators['parity_violations'], [])
        self.assertNotIn('J1', [r['ref'] for r in result['details']['coordinates']['records']])

        # An excluded designator that reappears in the CPL is not assembled on the board: parity defect.
        def restore_row(ctx, stage):
            native = list(csv.reader(io.StringIO((ctx.work / 'steps/pos/out/pos.csv').read_text())))
            row = next(r for r in native if r[0] == 'J1')
            self.edit_csv(stage, 'cpl.csv', lambda rows: rows.append(
                ['J1', row[3], row[4], 'Top', row[5]]))
        result = self.run_case(rule=RULE, mutate=restore_row)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['designators']['parity_violations'],
                         [{'designator': 'J1', 'bom': False, 'cpl': True, 'board': False}])

        # The manifest claim is checked against the sealed sources, never trusted.
        def forget(ctx, stage):
            manifest = read_json(stage / 'manifest.json')
            manifest['designators']['excluded'] = []
            write_json(stage / 'manifest.json', manifest)
        result = self.run_case(rule=RULE, mutate=forget)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.assertIn('stale: manifest excluded', result['summary'])

    def test_native_coordinates_include_the_exact_tolerance_on_both_sides(self):
        def boundary(ctx, stage):
            def edit(rows):
                for row in rows[1:]:
                    # Top and bottom use the same X sign and negative board Y.
                    if row[0] == 'R1':
                        row[1:3] = ['116.01', '-106.01']
                    elif row[0] == 'R2':
                        row[1:3] = ['115.99', '-113.99']
            self.edit_csv(stage, 'cpl.csv', edit)
        result = self.run_case(mutate=boundary)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assert_witness(result, accepted=False)
        for axis, number in ((1, '115.989999'), (2, '-114.010001')):
            with self.subTest(axis=axis):
                def beyond(ctx, stage):
                    self.edit_csv(stage, 'cpl.csv', lambda rows: next(
                        row for row in rows if row[0] == 'R2').__setitem__(axis, number))
                result = self.run_case(mutate=beyond)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertIn('cpl_coordinates', {d['code'] for d in result['details']['defects']})
                self.assertIn('R2', result['location'])
                self.assert_witness(result, accepted=True)

    def test_fractional_native_anchor_ignores_aux_origin_and_preserves_boundary(self):
        board = self.source / 'fab.kicad_pcb'
        board.write_text(board.read_text().replace('(at 116 106)', '(at 116.123456 106.654321)')
                         .replace('(setup', '(setup\n\t\t(aux_axis_origin 20 30)', 1))
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        native = next(r for r in result['details']['coordinates']['records'] if r['ref'] == 'R1')
        self.assertEqual(native['expected_mm'], [116.123456, -106.654321])
        self.assertEqual(native['actual_mm'], native['expected_mm'])

        def shifted(ctx, stage):
            # Four-decimal geometry would round X up to 116.1235, hiding this violation.
            self.edit_csv(stage, 'cpl.csv', lambda rows: next(
                r for r in rows if r[0] == 'R1').__setitem__(1, '116.133457'))
        result = self.run_case(mutate=shifted)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('cpl_coordinates', {d['code'] for d in result['details']['defects']})
        self.assert_witness(result, accepted=True)

    def test_malformed_packages_and_missing_native_positions_cannot_witness(self):
        def missing_positions(ctx, stage):
            path = ctx.run_dir / 'work/geometry/geometry.json'
            geometry = read_json(path)
            del geometry['footprints'][0]['position_nm']
            write_json(path, geometry)
        cases = {
            'empty artifact': ('malformed', lambda ctx, stage: (stage / 'bom.csv').write_bytes(b'')),
            'broken CSV': ('malformed', lambda ctx, stage: (stage / 'bom.csv').write_text('"broken')),
            'missing positions': ('unsupported', missing_positions),
        }
        for name, (execution, mutate) in cases.items():
            with self.subTest(case=name):
                result = self.run_case(mutate=mutate)
                self.assertEqual((result['execution'], result['outcome']), (execution, 'unknown'), result)
                self.assert_witness(result, accepted=False)

    def test_export_log_warning_and_missing_or_changed_log_are_fail_closed(self):
        def inject_diagnostic(ctx, stage):
            # Synthetic warning injected at the retained-file boundary; this is
            # a parser/adjudication fault test, not evidence of a native warning.
            directory = ctx.work / 'steps/gerbers'
            log = directory / 'console.log'
            log.write_bytes(log.read_bytes() + b'Warning: synthetic export diagnostic\n')
            record = read_json(directory / 'execution.json')
            record['console_sha256'] = digest(log)
            write_json(directory / 'execution.json', record)
        result = self.run_case(mutate=inject_diagnostic)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('build_log_warning', {d['code'] for d in result['details']['defects']})
        self.assert_witness(result, accepted=True)

        def unseal(ctx, stage):
            ctx.result['evidence'].remove('work/fab/steps/gerbers/console.log')
        result = self.run_case(mutate=unseal)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assert_witness(result, accepted=False)

        def change(ctx, stage):
            path = ctx.work / 'steps/gerbers/console.log'
            path.write_bytes(path.read_bytes() + b'changed log\n')
        result = self.run_case(mutate=change)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.assert_witness(result, accepted=False)

    def test_native_producer_refuses_stale_outputs_without_publishing_them(self):
        for directory in ('work/fab/fab', 'work/fab/steps/gerbers/out'):
            with self.subTest(directory=directory):
                def stale(run):
                    out = run / directory
                    out.mkdir(parents=True)
                    (out / 'stale.gtl').write_text('old export')
                result = self.run_case(before=stale)
                producer = self.results[-2]
                self.assertEqual((producer['execution'], producer['outcome']), ('malformed', 'unknown'), producer)
                self.assertIn('stale outputs', producer['summary'])
                self.assertFalse((self.run_dir / 'work/fab/fab/manifest.json').exists())
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
                self.assert_witness(result, accepted=False)

    def test_empty_native_export_is_failed_and_cannot_qualify_a_witness(self):
        native_execute = executor.execute

        def empty_output(inputs, work, argv, **kwargs):
            record = native_execute(inputs, work, argv, **kwargs)
            if argv[:4] == ['kicad-cli', 'pcb', 'export', 'gerbers']:
                next((Path(work) / 'out').glob('*F_Cu.gtl')).write_bytes(b'')
            return record
        with mock.patch.object(executor, 'execute', side_effect=empty_output):
            result = self.run_case()
        producer = self.results[-2]
        self.assertEqual((producer['execution'], producer['outcome']), ('failed', 'unknown'), producer)
        self.assertFalse((self.run_dir / 'work/fab/fab/manifest.json').exists())
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assert_witness(result, accepted=False)


class EmbeddedRecordTests(unittest.TestCase):
    """Byte-level embedded-file verification against native pcbnew output."""

    def test_committed_rc_fixture_records_are_native_and_verify(self):
        root = parse_sexpr(RC_EMBEDDED.read_bytes())
        found = records(root)
        self.assertEqual(sorted(found), sorted(RC_MODELS.values()))
        frames = fixture_frames()
        for name, candidates in found.items():
            # libzstd frames (compressed blocks, not raw) under the checksum pcbnew wrote.
            frame = frames[name]
            descriptor = frame[4]
            start = 5 + (not descriptor & 32) + (0, 1, 2, 4)[descriptor & 3] + (
                (1 if descriptor & 32 else 0), 2, 4, 8)[descriptor >> 6]
            self.assertEqual((frame[start] >> 1) & 3, 2, name)
            self.assertLess(len(frame), len(owned_step(name)) // 2)
            self.assertEqual(decompress(frame), owned_step(name))
            self.assertEqual(candidates[0]['checksum'], [[kicad_checksum(owned_step(name))]])
            self.assertEqual(verify(candidates, name)[0], 'resolved')

    def test_kicad_checksum_matches_native_for_every_tail_length(self):
        # Records carry independent SHA-256 checksums (accepted on load); pcbnew then
        # writes its own MMH3 checksum, which the Python port must reproduce exactly.
        require_toolchain(native=True)
        payloads = {f'p{n:02d}.step': bytes((7 * i + n) & 255 for i in range(n)) for n in range(1, 50)}
        text = board_text(embedded=''.join(embedded_file(name, data, checksum=hashlib.sha256(data).hexdigest())
                                           for name, data in payloads.items()))
        with tempfile.TemporaryDirectory() as tmp:
            source, saved = Path(tmp) / 'b.kicad_pcb', Path(tmp) / 'saved.kicad_pcb'
            source.write_text(text)
            pcbnew_resave(source, saved)
            native = records(parse_sexpr(saved.read_bytes()))
        self.assertEqual(sorted(native), sorted(payloads))
        for name, data in payloads.items():
            self.assertEqual(native[name][0]['checksum'], [[kicad_checksum(data)]], name)
            self.assertEqual(verify(native[name], name)[0], 'resolved')

    def test_decoder_matches_libzstd_frames(self):
        if shutil.which('zstd') is None:
            self.skipTest('zstd CLI unavailable to produce reference frames')
        corpus = [b'x', b'A' * 300000, bytes(range(256)) * 300, owned_step('a.step') * 900,
                  (REPO / 'src/agent_ee/fab_consistency.py').read_bytes()]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'in.bin'
            for data in corpus:
                for level in ('-1', '-9', '-19'):
                    with self.subTest(size=len(data), level=level):
                        source.write_bytes(data)
                        frame = subprocess.run(['zstd', '-q', '-f', '-c', level, str(source)],
                                               capture_output=True, check=True).stdout
                        self.assertEqual(decompress(frame), data)

    def test_committed_rc_fixture_is_reproduced_by_pcbnew(self):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            derived, saved = Path(tmp) / 'derived.kicad_pcb', Path(tmp) / 'saved.kicad_pcb'
            derived.write_text(derive_rc_embedded((RC_CONTROL / 'rc_filter.kicad_pcb').read_text(),
                                                  fixture_frames()))
            pcbnew_resave(derived, saved)
            self.assertEqual(saved.read_bytes(), RC_EMBEDDED.read_bytes())
            # Native load keeps a verified record and drops a checksum mismatch.
            broken = Path(tmp) / 'broken.kicad_pcb'
            text = saved.read_text()
            checksum = kicad_checksum(owned_step('r1.step'))
            broken.write_text(text.replace(checksum, '0' * 32))
            pcbnew_resave(broken, Path(tmp) / 'broken-saved.kicad_pcb')
            kept = records(parse_sexpr((Path(tmp) / 'broken-saved.kicad_pcb').read_bytes()))
            self.assertEqual(sorted(kept), ['c1.step', 'j1.step'])
            self.assertEqual(verify(records(parse_sexpr(broken.read_bytes()))['r1.step'], 'r1.step')[0],
                             'unresolved')


class NativeDrillQualificationTests(FabCase):
    """Real KiCad Excellon/Gerber headers from a synthetic board satisfy the classifier."""

    def test_kicad_drill_and_plot_attributes_classify(self):
        require_toolchain(tools=('kicad-cli',))
        native = board_text(extra='''
\t(footprint "Synth:Hole"
\t\t(layer "F.Cu")
\t\t(uuid "00000000-0000-4000-8000-000000000099")
\t\t(at 40 10)
\t\t(property "Reference" "H1"
\t\t\t(at 0 -2 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(attr through_hole exclude_from_pos_files exclude_from_bom)
\t\t(pad "1" thru_hole circle
\t\t\t(at 0 0)
\t\t\t(size 2 2)
\t\t\t(drill 1)
\t\t\t(layers "*.Cu" "*.Mask")
\t\t)
\t\t(pad "" np_thru_hole circle
\t\t\t(at 4 0)
\t\t\t(size 1.5 1.5)
\t\t\t(drill 1.5)
\t\t\t(layers "*.Cu" "*.Mask")
\t\t)
\t)''')
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'board.kicad_pcb').write_text(native)
            out = work / 'out'
            out.mkdir()
            argv = ['/bin/sh', '-c', 'kicad-cli pcb export drill --format excellon --excellon-separate-th '
                    '-o /work/ /candidate/board.kicad_pcb && kicad-cli pcb export gerbers --no-x2 '
                    '-l F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.SilkS,B.SilkS,Edge.Cuts '
                    '-o /work/ /candidate/board.kicad_pcb']
            inputs = work / 'inputs'
            inputs.mkdir()
            (inputs / 'board.kicad_pcb').write_text(native)
            record = execute(inputs, out, argv, timeout=120)
            self.assertEqual(record['execution'], 'ok', (out / 'console.log').read_text())
            files = {}
            for path in sorted(out.iterdir()):
                if path.suffix == '.drl':
                    files[f'drill/{path.name}'] = path.read_bytes()
                elif path.suffix[1:] in EXTENSIONS.values():
                    files[f'gerbers/{path.name}'] = path.read_bytes()
        pth = next(k for k in files if k.endswith('-PTH.drl')).split('/')[1]
        npth = next(k for k in files if k.endswith('-NPTH.drl')).split('/')[1]
        self.assertEqual(len([k for k in files if k.startswith('gerbers/')]), 9, sorted(files))
        files.update({k: v for k, v in package().items() if not k.startswith(('gerbers/', 'drill/'))})
        result = self.run_case(files=files, board=native,
                               netlist=netlist_xml(dict(NETLIST_COMPONENTS, H1=['exclude_from_bom'])),
                               non_dnp=['C1', 'H1', 'R1', 'R2', 'TP1', 'U1'],
                               manifest_edit=lambda m: m['drill'].update(pth=pth, npth=npth))
        self.assertStatus(result, 'ok', 'pass')
        self.assertEqual({p['layer'] for p in result['details']['plots']}, set(LAYERS))
        self.assertEqual({d['file_function'].split(',')[-1] for d in result['details']['drill']}, {'PTH', 'NPTH'})


if __name__ == '__main__':
    unittest.main()
