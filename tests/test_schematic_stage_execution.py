"""CPU contracts for the 2026-10-28 nested-stage refusal repair.

Only the external native process and kernel mount observations are substituted.
No native verdict, DRC result or actual sandbox isolation is asserted here.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import forward_annotation as fa, native_run, schematic_stage

ROOT = Path(__file__).resolve().parents[1]
XML = b'''<export version="E"><design><sheet name="/" tstamps="/"/></design>
<components><comp ref="R1"><value>100</value><footprint>OwnedFP:CAP_2PAD</footprint>
<libsource lib="OwnedGen" part="CAP"/><sheetpath tstamps="/"/><tstamps>symbol-1</tstamps>
<units><unit name="A"><pins><pin num="1"/><pin num="2"/></pins></unit></units></comp></components>
<libparts><libpart lib="OwnedGen" part="CAP"><pins><pin num="1" name="a" type="passive"/>
<pin num="2" name="b" type="passive"/></pins></libpart></libparts>
<nets><net code="1" name="GND"><node ref="R1" pin="1"/></net>
<net code="2" name="VCC"><node ref="R1" pin="2"/></net></nets></export>'''
EMPTY = dict(pcbnew_version='authored CPU facts', footprints=[], tracks=[], zones=[], edge_bbox_nm=[0,0,0,0])


def mount_view():
    entries = [('/', 'rw', 'tmpfs'), ('/proc', 'rw', 'proc')]
    entries += [(path, 'rw' if path == '/work' else 'ro', 'ext4')
                for path in ('/candidate', '/app', '/tool/agent-ee', '/work', '/usr', '/etc')]
    return '\n'.join(f'{i} 0 0:1 / {path} {mode} - {kind} none {mode}'
                     for i, (path, mode, kind) in enumerate(entries, 1))


def context(scratch):
    # Authored kernel observation, then a real context with a temporary scratch
    # mapping for CPU filesystem tests. Production construction has no path option.
    original = Path.read_text
    def read(path, *a, **kw):
        return mount_view() if str(path) == '/proc/self/mountinfo' else original(path, *a, **kw)
    with patch.object(Path, 'read_text', read), patch.object(fa, '__file__', '/tool/agent-ee/agent_ee/forward_annotation.py'):
        execution = fa._StageExecution()
    execution.scratch = scratch
    return execution


class StageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.board = self.root / 'source.kicad_pcb'
        self.board.write_bytes((ROOT / 'fixtures/stages/empty.kicad_pcb').read_bytes())
        self.xml = self.root / 'netlist.xml'
        self.xml.write_bytes(XML)
        data = (ROOT / 'fixtures/generation/project/lib/OwnedFP.pretty/CAP_2PAD.kicad_mod').read_bytes()
        self.captures = self.root / 'captures'
        self.captures.mkdir()
        (self.captures / 'CAP_2PAD.kicad_mod').write_bytes(data)
        self.manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True,
            libraries=[dict(name='OwnedFP', members=[dict(path='CAP_2PAD.kicad_mod',
                sha256=hashlib.sha256(data).hexdigest())])])
        self.execution = context(self.root)
        self.calls = []
        self.after = deepcopy(EMPTY)
        self.failure = None
        self.mutate_copy = False
        self.bad_pose = False
        self.change_xml_on_apply = False

    def native(self, intent, *, work, inputs=None, **kwargs):
        """External process boundary: write authored facts/bytes, retain exact intent."""
        self.calls.append(intent)
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        argv = list(intent.argv)
        (work / 'console.log').write_text('authored process diagnostic')
        if self.failure:
            return dict(execution=self.failure, exit_code=9 if self.failure == 'failed' else None)
        operation = argv[argv.index('agent_ee.forward_annotation_probe') + 1]
        if intent.isolation == 'sandbox':
            source = Path(inputs) / 'board.kicad_pcb'
            output = work / ('facts.json' if operation == 'facts' else 'board.kicad_pcb')
        else:
            source = Path(argv[argv.index(operation) + 1])
            output = Path(argv[argv.index('-o') + 1])
            self.assertNotEqual(output.parent, source.parent)
            self.assertTrue(source.is_relative_to(self.root))
            self.assertTrue(output.is_relative_to(work))
        if operation == 'facts':
            facts = self.after if source.read_bytes() == b'authored changed board' else EMPTY
            facts = deepcopy(facts)
            if self.bad_pose and facts['footprints']:
                facts['footprints'][0]['position_nm'][0] += 1
            output.write_text(json.dumps(facts))
            if self.mutate_copy:
                source.write_bytes(b'changed private input')
        else:
            plan = json.loads(Path(argv[argv.index('--plan') + 1]).read_bytes())
            added = plan['add'][0]
            self.after['footprints'] = [dict(uuid='new-fp', path=added['path'], fpid=added['fpid'],
                reference='R1', value='100', fields={}, position_nm=added['position_nm'],
                orientation_deg=0, layer='F.Cu', pads=[dict(number=n, net=net, position_nm=[0,0])
                    for n, net in (('1','GND'), ('2','VCC'))])]
            output.write_bytes(b'authored changed board')
            if self.change_xml_on_apply:
                self.xml.write_bytes(XML.replace(b'<value>100', b'<value>200'))
        return dict(execution='ok', exit_code=0)

    def plan(self, name='plan', execution=True):
        return fa.plan(self.board, self.xml, self.manifest, self.captures, self.root / name,
                       **({'_execution':self.execution} if execution else {}))

    def test_confined_plan_replan_apply_and_fresh_verification_use_concrete_private_paths(self):
        before = self.board.read_bytes()
        with patch.object(native_run, 'run', self.native):
            plan = self.plan()
            self.assertEqual(plan['execution'], 'ok', plan)
            result = fa.apply(plan, self.board, self.xml, self.manifest, self.captures,
                              self.root / 'published.kicad_pcb', self.root / 'apply', _execution=self.execution)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual((self.root / 'published.kicad_pcb').read_bytes(), b'authored changed board')
        self.assertEqual(self.board.read_bytes(), before)
        self.assertEqual([c.isolation for c in self.calls], ['host'] * 4)
        self.assertEqual([c.argv[c.argv.index('agent_ee.forward_annotation_probe')+1] for c in self.calls],
                         ['facts','facts','apply','facts'])
        self.assertIn(str(self.root/'apply/replan/facts-input/board.kicad_pcb'), self.calls[1].argv)
        self.assertIn(str(self.root/'apply/apply-input/plan.json'), self.calls[2].argv)
        self.assertIn(str(self.root/'apply/verify/facts-input/board.kicad_pcb'), self.calls[3].argv)
        self.assertEqual(plan['counts']['add'], 1)

    def test_standalone_planning_keeps_sandbox_and_virtual_paths(self):
        with patch.object(native_run, 'run', self.native):
            plan = self.plan(execution=False)
        self.assertEqual(plan['execution'], 'ok', plan)
        self.assertEqual(self.calls[0].isolation, 'sandbox')
        self.assertIn('/candidate/board.kicad_pcb', self.calls[0].argv)
        self.assertIn('/work/facts.json', self.calls[0].argv)

    def test_child_failures_and_changed_private_input_remain_failed_unknown(self):
        for status in ('failed', 'timeout', 'unavailable', 'mutation'):
            with self.subTest(status=status):
                self.failure = None if status == 'mutation' else status
                self.mutate_copy = status == 'mutation'
                with patch.object(native_run, 'run', self.native):
                    result = self.plan(name=status)
                self.assertEqual((result['execution'], result['outcome']), ('failed','unknown'))
                self.assertEqual(result['findings'][0]['code'], 'stale_input' if status == 'mutation' else 'native_child')
                if status != 'mutation':
                    self.assertIn(status, result['findings'][0]['message'])

    def test_stale_replan_and_independent_verifier_refuse_publication(self):
        with patch.object(native_run, 'run', self.native):
            plan = self.plan()
            self.xml.write_bytes(XML.replace(b'<value>100', b'<value>200'))
            stale = fa.apply(plan, self.board, self.xml, self.manifest, self.captures,
                self.root/'stale-output', self.root/'stale', _execution=self.execution)
            self.assertEqual(stale['findings'][0]['code'], 'stale_plan')
            self.xml.write_bytes(XML)
            self.bad_pose = True
            result = fa.apply(plan, self.board, self.xml, self.manifest, self.captures,
                self.root/'bad-output', self.root/'bad', _execution=self.execution)
        self.assertEqual(result['findings'][0]['code'], 'verification')
        self.assertFalse((self.root/'bad-output').exists())
        self.assertFalse((self.root/'stale-output').exists())
        self.bad_pose = False
        self.change_xml_on_apply = True
        with patch.object(native_run, 'run', self.native):
            changed = fa.apply(plan, self.board, self.xml, self.manifest, self.captures,
                self.root/'changed-output', self.root/'changed', _execution=self.execution)
        self.assertEqual(changed['findings'][0]['code'], 'stale_input')
        self.assertFalse((self.root/'changed-output').exists())

    def test_custom_pad_stage_is_explicitly_pending_before_a_nested_probe(self):
        self.captures.joinpath('CAP_2PAD.kicad_mod').write_bytes(b'(footprint "CAP_2PAD" (pad "1" smd custom))')
        self.manifest['libraries'][0]['members'][0]['sha256'] = hashlib.sha256(
            self.captures.joinpath('CAP_2PAD.kicad_mod').read_bytes()).hexdigest()
        with patch.object(native_run, 'run', self.native):
            result = self.plan()
        self.assertEqual(result['findings'][0]['code'], 'native_custom_pad_stage_pending')
        self.assertEqual(len(self.calls), 1)  # Only the facts child, no custom-pad subprocess.
        self.captures.joinpath('CAP_2PAD.kicad_mod').write_bytes(b'not a footprint (')
        with patch.object(native_run, 'run', self.native):
            malformed = self.plan(name='malformed-library')
        self.assertEqual(malformed['findings'][0]['code'], 'footprint_library_coverage')

    def test_stage_entrypoint_refuses_a_host_view_and_incomplete_mount_declarations(self):
        args = ['annotate', '--board','a.kicad_pcb','--schematic','a.kicad_sch',
                '--intent','intent.json','--layout','layout.json','--captures','lib','--footprints','fp.json']
        for view in (mount_view().replace('/candidate ro', '/candidate rw'),
                     mount_view().replace(' - tmpfs ', ' - overlay '),
                     mount_view()+'\n99 0 0:1 / /home/owner rw - ext4 none rw',
                     mount_view()+'\n99 0 0:1 / /usr/lib rw - ext4 none rw'):
            with self.subTest(view=view), patch.object(Path, 'read_text', return_value=view), \
                 patch.object(fa, '__file__', '/tool/agent-ee/agent_ee/forward_annotation.py'):
                with self.assertRaisesRegex(fa.AnnotationError, 'fixed receipted stage sandbox'):
                    schematic_stage.main(args)
        with patch.object(Path, 'read_text', return_value=mount_view()):
            with self.assertRaisesRegex(fa.AnnotationError, 'fixed receipted stage sandbox'):
                fa._StageExecution()  # Actual source checkout is not the fixed captured mount.

    def test_stage_netlist_paths_and_failure_are_retained_for_flat_and_hierarchy(self):
        import shutil
        for name, confined in (('regulator', True), ('two_level', True), ('regulator', False)):
            with self.subTest(name=name, confined=confined):
                case = self.root / (name + str(confined))
                candidate = case / 'candidate'
                shutil.copytree(ROOT/'fixtures/generation/project', candidate)
                fixture = ROOT/'fixtures/generation'
                intent = json.loads((fixture/(name+'.intent.json')).read_bytes())
                layout = json.loads((fixture/(name+'.layout.json')).read_bytes())
                (candidate/'intent.json').write_text(json.dumps(intent))
                (candidate/'layout.json').write_text(json.dumps(layout))
                libraries = {lib['path']:(candidate/lib['path']).read_bytes() for lib in intent['libraries']}
                report, files = schematic_stage.schematic_emitter.emit_schematic(intent, libraries,
                    (candidate/'layout.json').read_bytes())
                self.assertEqual(report['execution'], 'ok', report)
                for path, raw in files.items():
                    (candidate/path).write_bytes(raw)
                root = layout['project']+'.kicad_sch'
                children = [path for path in files if path.endswith('.kicad_sch') and path != root]
                (candidate/'board.kicad_pcb').write_bytes(self.board.read_bytes())
                out = case/'out'
                observed = []
                def fail(intent, **kw):
                    observed.append(intent)
                    Path(kw['work']).mkdir(parents=True)
                    (Path(kw['work'])/'console.log').write_text('authored netlist timeout')
                    return dict(execution='timeout', exit_code=None)
                with patch.object(native_run, 'run', fail):
                    status = schematic_stage.run('annotate', candidate, out, board='board.kicad_pcb',
                        schematic=root, intent='intent.json', layout='layout.json', captures='.',
                        footprints='unreached.json', child_schematics=children,
                        _execution=self.execution if confined else None)
                self.assertEqual(status, 1)
                self.assertTrue((out/'emission.json').is_file())
                self.assertTrue((out/'netlist/console.log').is_file())
                self.assertFalse((out/'plan.json').exists())
                self.assertFalse((out/'board.kicad_pcb').exists())
                self.assertEqual((candidate/'board.kicad_pcb').read_bytes(), self.board.read_bytes())
                self.assertEqual(observed[0].isolation, 'host' if confined else 'sandbox')
                self.assertEqual(list(observed[0].argv), ['kicad-cli', 'sch', 'export', 'netlist',
                    '--format', 'kicadxml', '-o', str(out/'netlist/design.xml') if confined else '/work/design.xml',
                    '/candidate/'+root])
