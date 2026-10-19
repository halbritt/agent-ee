"""AGENTEE-169: AMPSEAL-23 footprint captured from fome-mc, bound to Conn_01x23 and the TE 776228 drawing."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_pod_ampseal.
sys.path.insert(0, str(ROOT / 'fixtures/pod/ampseal'))

import compare_land  # noqa: E402
from agent_ee.executor import execute  # noqa: E402
from agent_ee.footprint_capture import CaptureError, capture_footprints, verify_capture  # noqa: E402
from agent_ee.footprint_library import check_assignments, load_document  # noqa: E402
from agent_ee.storage import json_bytes  # noqa: E402
from toolchain import fome_mc_store, require_toolchain  # noqa: E402

CAPTURE = ROOT / 'fixtures/fome-mc/ampseal/capture'
REQUEST = ROOT / 'fixtures/fome-mc/ampseal/request.json'
POD = ROOT / 'fixtures/pod/ampseal'
MEMBER = 'ampseal.pretty/ampseal-23.kicad_mod'
STOCK_SYMBOLS = Path('/usr/share/kicad/symbols')
STOCK_AMPSEAL = Path('/usr/share/kicad/footprints/Connector_TE-Connectivity.pretty/'
                     'TE_AMPSEAL_1-776087-x_3Rows_23_P0.4mm_Horizontal.kicad_mod')
UUID = re.compile(rb'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"')
# An owned KiCad-5 style footprint written for these tests; not derived from any library.
LEGACY = b'''(module owned-pair (layer F.Cu) (tedit 5ED9717A)
  (fp_text reference REF** (at 0 3) (layer F.SilkS)
    (effects (font (size 1 1) (thickness 0.15)))
  )
  (fp_text value owned-pair (at 0 -3) (layer F.Fab)
    (effects (font (size 1 1) (thickness 0.15)))
  )
  (pad "" np_thru_hole circle (at 0 2) (size 1 1) (drill 1) (layers *.Cu *.Mask))
  (pad 1 thru_hole circle (at -1 0) (size 1.6 1.6) (drill 0.9) (layers *.Cu *.Mask))
  (pad 2 thru_hole circle (at 1 0) (size 1.6 1.6) (drill 0.9) (layers *.Cu *.Mask))
)
'''


def fome_mc():
    """The pinned fome-mc object store (tests/toolchain.py fome_mc_store)."""
    repository = Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(Path.home() / 'git/fome-mc')))
    return fome_mc_store(repository, load_document(REQUEST)['commit'])


def git(repo, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=agent-ee test', '-c', 'user.email=test@invalid',
                           *args], capture_output=True, check=True, env=env).stdout.decode().strip()


def assignment_files(footprint=None):
    """Captured bytes for the pure assignment check: the pod manifest member and the stock symbols."""
    manifest = load_document(POD / 'footprint-libraries.json')
    files = {'ampseal-capture/' + MEMBER: footprint if footprint is not None else (CAPTURE / MEMBER).read_bytes(),
             'stock/symbols/Connector_Generic.kicad_sym': (STOCK_SYMBOLS / 'Connector_Generic.kicad_sym').read_bytes()}
    return manifest, files


class CapturedFootprintTests(unittest.TestCase):
    def test_capture_record_binds_the_pinned_fome_mc_blob(self):
        record = load_document(CAPTURE / 'capture.json')
        request = load_document(REQUEST)
        source = record['members'][0]['source']
        self.assertEqual((source['repository'], source['commit'], source['path']),
                         ('fome-mc', request['commit'], request['members'][0]))
        self.assertEqual(source['blob'], '2f09e3a5c9bd66421dee3223cb12596307f8181a')
        self.assertEqual(record['kicad_version'], '10.0.7')
        self.assertEqual(record['conversion']['second_conversion'], 'byte-identical')
        # Without an object store the bytes still verify, but the source is not credited.
        self.assertEqual(verify_capture(CAPTURE)['status'], 'unverified')
        report = verify_capture(CAPTURE, fome_mc())
        self.assertEqual(report['status'], 'verified', report)
        self.assertEqual(report['footprints'], ['ampseal:ampseal-23'])

    def test_capture_tamper_and_unrecorded_files_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'capture'
            shutil.copytree(CAPTURE, copy)
            (copy / 'stray.kicad_mod').write_text('(footprint "stray")')
            with self.assertRaisesRegex(CaptureError, 'unrecorded'):
                verify_capture(copy)
            (copy / 'stray.kicad_mod').unlink()
            path = copy / MEMBER
            path.write_bytes(path.read_bytes().replace(b'(at 14 4)', b'(at 14.1 4)'))
            with self.assertRaisesRegex(CaptureError, 'changed'):
                verify_capture(copy)

    def test_second_conversion_of_the_captured_file_is_byte_identical(self):
        require_toolchain(tools=('kicad-cli',))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'in').mkdir()
            shutil.copytree(CAPTURE / 'ampseal.pretty', root / 'in/ampseal.pretty')
            for name, flags in (('forced', ['--force']), ('plain', [])):
                (root / name).mkdir()
                record = execute(root / 'in', root / name, ['kicad-cli', 'fp', 'upgrade', *flags,
                                 '--output', '/work/out.pretty', '/candidate/ampseal.pretty'], timeout=120)
                self.assertEqual(record['execution'], 'ok', (root / name / 'console.log').read_text())
            self.assertEqual((root / 'forced/out.pretty/ampseal-23.kicad_mod').read_bytes(),
                             (CAPTURE / MEMBER).read_bytes())
            # Already current: an unforced upgrade leaves the converted file alone.
            self.assertIn('not updated', (root / 'plain/console.log').read_text())
            self.assertFalse((root / 'plain/out.pretty').exists())

    def test_fresh_capture_reproduces_the_committed_one_except_new_uuids(self):
        require_toolchain(tools=('kicad-cli',))
        repository = fome_mc()
        with tempfile.TemporaryDirectory() as tmp:
            report = capture_footprints(load_document(REQUEST), repository, Path(tmp) / 'capture')
            self.assertEqual(report['footprints'], ['ampseal:ampseal-23'])
            fresh = (Path(tmp) / 'capture' / MEMBER).read_bytes()
            committed = (CAPTURE / MEMBER).read_bytes()
            # KiCad gives legacy items new random UUIDs on each first conversion; nothing else moves.
            self.assertEqual(UUID.sub(b'"U"', fresh), UUID.sub(b'"U"', committed))
            ours = load_document(Path(tmp) / 'capture/capture.json')
            self.assertEqual(ours['members'][0]['source'], load_document(CAPTURE / 'capture.json')['members'][0]['source'])


class OfflineGitCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'lib.pretty').mkdir()
        (self.repo / 'lib.pretty/owned-pair.kicad_mod').write_bytes(LEGACY)
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'owned legacy footprint')
        self.commit = git(self.repo, 'rev-parse', 'HEAD')
        self.request = dict(schema='agent-ee.footprint-capture-request/1', library='owned', repository='owned-repo',
                            commit=self.commit, kicad_version='10.0.7', members=['lib.pretty/owned-pair.kicad_mod'])
        self.count = 0

    def capture(self, **changes):
        self.count += 1
        return capture_footprints(dict(self.request, **changes), self.repo, self.root / f'out{self.count}')

    def test_committed_blob_is_converted_and_dirty_edits_never_enter(self):
        require_toolchain(tools=('kicad-cli',))
        (self.repo / 'lib.pretty/owned-pair.kicad_mod').write_bytes(LEGACY.replace(b'(at 1 0)', b'(at 9 0)'))
        report = self.capture()
        self.assertEqual(report['footprints'], ['owned:owned-pair'])
        out = self.root / 'out1'
        record = load_document(out / 'capture.json')
        self.assertEqual(record['members'][0]['source']['sha256'], hashlib.sha256(LEGACY).hexdigest())
        converted = (out / 'owned.pretty/owned-pair.kicad_mod').read_bytes()
        self.assertTrue(converted.startswith(b'(footprint "owned-pair"'))
        self.assertIn(b'(at 1 0)', converted)
        self.assertNotIn(b'(at 9 0)', converted)
        self.assertIn(b'(attr through_hole)', converted)
        self.assertEqual(verify_capture(out, self.repo)['status'], 'verified')

    def test_unpinned_or_uncommitted_sources_are_refused(self):
        tree = git(self.repo, 'rev-parse', 'HEAD^{tree}')
        for commit, message in (('HEAD', 'full 40-hex'), ('main', 'full 40-hex'), (self.commit[:12], 'full 40-hex'),
                                (self.commit.upper(), 'full 40-hex'), ('0' * 40, 'not a commit in the local object store'),
                                (tree, 'not a commit in the local object store')):
            with self.subTest(commit=commit), self.assertRaisesRegex(CaptureError, message):
                self.capture(commit=commit)
        (self.repo / 'lib.pretty/untracked.kicad_mod').write_bytes(LEGACY)
        (self.repo / 'lib.pretty/staged.kicad_mod').write_bytes(LEGACY)
        git(self.repo, 'add', 'lib.pretty/staged.kicad_mod')
        for member in ('lib.pretty/untracked.kicad_mod', 'lib.pretty/staged.kicad_mod'):
            with self.subTest(member=member), self.assertRaisesRegex(CaptureError, 'not one regular committed file'):
                self.capture(members=[member])
        os.symlink('owned-pair.kicad_mod', self.repo / 'lib.pretty/link.kicad_mod')
        git(self.repo, 'add', 'lib.pretty')
        git(self.repo, 'commit', '-q', '-m', 'link and others')
        with self.assertRaisesRegex(CaptureError, 'not one regular committed file'):
            self.capture(commit=git(self.repo, 'rev-parse', 'HEAD'), members=['lib.pretty/link.kicad_mod'])
        for changes, message in ((dict(members=['../x.kicad_mod']), 'unsafe'), (dict(members=['a/b.txt']), 'unsafe'),
                                 (dict(members=['a/x.kicad_mod', 'b/x.kicad_mod']), 'unique'),
                                 (dict(library='owned.pretty'), 'without .pretty'), (dict(schema='other'), 'schema')):
            with self.subTest(changes=changes), self.assertRaisesRegex(CaptureError, message):
                self.capture(**changes)
        self.assertFalse(any(self.root.glob('out*')), 'a refused capture writes nothing')

    def test_destination_and_converter_version_are_fail_closed(self):
        require_toolchain(tools=('kicad-cli',))
        (self.root / 'taken').mkdir()
        with self.assertRaisesRegex(CaptureError, 'must be new'):
            capture_footprints(self.request, self.repo, self.root / 'taken')
        with self.assertRaisesRegex(CaptureError, 'is not the declared'):
            self.capture(kicad_version='9.0.0')
        self.assertFalse((self.root / f'out{self.count}').exists())


class DrawingComparisonTests(unittest.TestCase):
    def test_chosen_footprint_matches_the_776228_pcb_layout(self):
        report = compare_land.compare_capture(CAPTURE)
        self.assertEqual(report['status'], 'match', report['problems'])
        self.assertEqual(report['named_contacts'], {'1': True, '8': True, '16': True, '23': True})
        positions = {e['number']: e['footprint_mm'] for e in report['contacts']}
        self.assertEqual([positions[n] for n in ('1', '8', '16', '23')],
                         [['9.7', '6.8'], ['37.7', '6.8'], ['9.7', '14.8'], ['37.7', '14.8']])
        self.assertEqual(len(report['contacts']), 23)
        self.assertEqual(len(report['pegs']), 4)
        self.assertEqual((POD / 'land-comparison.json').read_bytes(), json_bytes(report),
                         'regenerate with fixtures/pod/ampseal/compare_land.py')

    def compare_bytes(self, data):
        manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=[dict(
            name='ampseal', members=[dict(path=MEMBER, sha256=hashlib.sha256(data).hexdigest())])])
        return compare_land.compare(manifest, {MEMBER: data}, 'ampseal:ampseal-23',
                                    load_document(compare_land.DRAWING))

    def test_tolerance_boundary_and_wrong_land_patterns(self):
        original = (CAPTURE / MEMBER).read_bytes()
        self.assertEqual(self.compare_bytes(original)['status'], 'match')
        inside = self.compare_bytes(original.replace(b'(at 14 4)', b'(at 14.05 4)'))
        self.assertEqual(inside['status'], 'match')
        outside = self.compare_bytes(original.replace(b'(at 14 4)', b'(at 14.051 4)'))
        self.assertEqual((outside['status'], outside['named_contacts']['23']), ('mismatch', False))
        swapped = original.replace(b'(pad "1" ', b'(pad "X" ').replace(b'(pad "8" ', b'(pad "1" ').replace(
            b'(pad "X" ', b'(pad "8" ')
        self.assertEqual(self.compare_bytes(swapped)['status'], 'mismatch')
        drill = self.compare_bytes(original.replace(b'(drill 1.75)', b'(drill 1.9)', 1))
        self.assertEqual(drill['status'], 'mismatch')
        # Turned 180 degrees: pad 1 would sit beside the 50.4 mm peg pair.
        turned = re.sub(rb'\(at (-?[0-9.]+) (-?[0-9.]+)\)\n(\t\t\(size)',
                        lambda m: b'(at %s %s)\n%s' % (str(-float(m[1]) + 0.0).encode(),
                                                       str(-float(m[2]) + 0.0).encode(), m[3]), original)
        self.assertEqual(turned.count(b'(at 14.0 4.0)'), 1)
        self.assertEqual(self.compare_bytes(turned)['status'], 'mismatch')

    def test_stock_horizontal_776087_footprint_is_not_this_header(self):
        require_toolchain(native=True)
        data = STOCK_AMPSEAL.read_bytes()
        name = STOCK_AMPSEAL.name
        manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=[dict(
            name='Connector_TE-Connectivity', members=[dict(path=name, sha256=hashlib.sha256(data).hexdigest())])])
        report = compare_land.compare(manifest, {name: data}, 'Connector_TE-Connectivity:' + STOCK_AMPSEAL.stem,
                                      load_document(compare_land.DRAWING))
        self.assertEqual(report['status'], 'mismatch')
        self.assertEqual(report['problems'], ['2 peg holes, drawing has 4'])


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)

    def test_conn_01x23_has_23_pin_pad_parity_with_mechanical_pegs(self):
        manifest, files = assignment_files()
        result = check_assignments(manifest, files, load_document(POD / 'assignment.json'))
        self.assertEqual((result['execution'], result['outcome'], result['coverage']), ('ok', 'pass', 'complete'),
                         result['findings'])
        assignment = result['assignments'][0]
        self.assertEqual(assignment['status'], 'pass')
        self.assertEqual(sorted(assignment['pin_numbers'], key=int), [str(n) for n in range(1, 24)])
        footprint = result['index']['footprints'][0]
        self.assertEqual(sorted(footprint['electrical_numbers'], key=int), [str(n) for n in range(1, 24)])
        pegs = [p for p in footprint['pads'] if p['type'] == 'np_thru_hole']
        self.assertEqual([(p['number'], p['role']) for p in pegs], [('', 'mechanical')] * 4)
        self.assertEqual(footprint['attributes'], ['through_hole'])

    def test_wrong_pin_counts_and_stale_peg_declaration_fail(self):
        manifest, files = assignment_files()
        declaration = load_document(POD / 'assignment.json')
        for symbol, missing, extra in (('Conn_01x22', [], ['23']), ('Conn_01x24', ['24'], [])):
            with self.subTest(symbol=symbol):
                changed = json.loads(json.dumps(declaration))
                changed['components'][0]['lib_id'] = 'Connector_Generic:' + symbol
                result = check_assignments(manifest, files, changed)
                self.assertEqual(result['outcome'], 'defect')
                parity = [f for f in result['findings'] if f['code'] == 'pad_pin_parity']
                self.assertEqual((parity[0]['missing_pads'], parity[0]['extra_pads']), (missing, extra))
        # Unnumbered NPTH pegs are mechanical natively (AGENTEE-116); declaring them is a stale declaration.
        stale = dict(schema='agent-ee.footprint-declarations/1', pinless_symbols=[], footprints=[dict(
            footprint='ampseal:ampseal-23', mechanical_pads=[''], thermal_via_pads=[], custom_pads=[], assembly=None,
            rationale='test: unnumbered pegs')])
        result = check_assignments(manifest, files, declaration, declarations=stale)
        self.assertEqual((result['outcome'], result['coverage']), ('unknown', 'missing'))
        # A numbered peg would need a declaration: undeclared it refuses coverage, declared it leaves parity.
        numbered = (CAPTURE / MEMBER).read_bytes().replace(b'(pad "" np_thru_hole circle\n\t\t(at -25.2 7.3)',
                                                           b'(pad "MP" np_thru_hole circle\n\t\t(at -25.2 7.3)')
        manifest, files = assignment_files(numbered)
        manifest['libraries'][0]['members'][0]['sha256'] = hashlib.sha256(numbered).hexdigest()
        self.assertEqual(check_assignments(manifest, files, declaration)['outcome'], 'unknown')
        stale['footprints'][0]['mechanical_pads'] = ['MP']
        declared = check_assignments(manifest, files, declaration, declarations=stale)
        self.assertEqual(declared['outcome'], 'pass', declared['findings'])
        self.assertEqual(declared['assignments'][0]['mechanical_pads'], ['MP'])

    def test_footprint_assignment_check_through_the_sandboxed_pipeline(self):
        from agent_ee.candidate import capture as capture_candidate
        from agent_ee.checks import run_checks
        from agent_ee.storage import read_json, write_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'placeholder').write_text('AGENTEE-169 assignment fixture; no board or schematic is checked.\n')
            params = dict(manifest='pod/footprint-libraries.json', declaration='pod/assignment.json',
                          assembly_intent=[dict(reference='J1', assembly='through_hole',
                                                connector=dict(rows=1, columns=23))])
            scope = dict(schema='agent-ee.scope/3', name='pod-ampseal-assignment',
                         scope='Declared AMPSEAL-23 assignment only',
                         board='placeholder', schematic='placeholder',
                         inputs=[dict(path=str(POD), at='pod', include=['footprint-libraries.json', 'assignment.json',
                                                                         'README.md']),
                                 dict(path=str(CAPTURE), at='ampseal-capture', include=[MEMBER]),
                                 dict(path=str(STOCK_SYMBOLS), at='stock/symbols',
                                      include=['Connector_Generic.kicad_sym']),
                                 dict(path=str(root / 'placeholder'), at='placeholder')],
                         checks=[dict(id='fp', kind='footprint_assignment', params=params)],
                         baseline=dict(requirements=[dict(
                             id='pod-ampseal', intent='acceptance',
                             statement='J1 Conn_01x23 has one pad per pin on the captured AMPSEAL-23 footprint',
                             source=dict(path='pod/README.md', locator='Acceptance'), checks=['fp'])],
                             targets=['pod/README.md'], reference_architectures=['pod/README.md']))
            write_json(root / 'scope.json', scope)
            run = root / 'run'
            run.mkdir()
            capture_candidate(root / 'scope.json', run)
            results = run_checks(run, time.monotonic() + 120)
            self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', 'pass'), results[0])
            report = read_json(run / 'work/fp/assignments.json')
            self.assertEqual(report['assignments'][0]['footprint'], 'ampseal:ampseal-23')
            self.assertEqual(len(report['assignments'][0]['pin_numbers']), 23)


if __name__ == '__main__':
    unittest.main()
