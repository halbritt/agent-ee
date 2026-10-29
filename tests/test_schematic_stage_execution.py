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
from tests.test_custom_pad_index import custom

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
    entries = [('/', 'rw', 'tmpfs'), ('/proc', 'rw', 'proc'), ('/dev', 'rw', 'tmpfs'),
               ('/dev/pts', 'rw', 'devpts'), ('/dev/shm', 'rw', 'tmpfs'), ('/tmp', 'rw', 'tmpfs')]
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
        self.custom_error = None

    def native(self, intent, *, work, inputs=None, **kwargs):
        """External process boundary: write authored facts/bytes, retain exact intent."""
        self.calls.append(intent)
        work = Path(work)
        self.assertFalse(work.exists(), 'the caller precreated Host records scratch')
        if intent.isolation == 'host':
            self.assertIn('-P', intent.argv)
            self.assertEqual(intent.env['HOME'], str(work/'home'))
            self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(work/'kicad-config'))
            self.assertEqual(intent.env['PYTHONPATH'], '/tool/agent-ee')
            for path in ('home', 'kicad-config'):
                self.assertFalse((work/path).exists())
        work.mkdir(parents=True, exist_ok=True)
        argv = list(intent.argv)
        (work / 'console.log').write_text('authored process diagnostic')
        if self.failure:
            return dict(execution=self.failure, exit_code=9 if self.failure == 'failed' else None)
        if 'agent_ee.footprint_probe' in argv or '-c' in argv:
            if intent.isolation == 'host':
                self.assertIn('from agent_ee.footprint_probe import _stage_main', argv[argv.index('-c')+1])
                inputs = Path(argv[-3])
                output = Path(argv[-1])
            else:
                inputs, output = Path(inputs), work/'footprint.json'
            self.assertEqual(len(list(inputs.glob('*.kicad_mod'))), 1)
            self.assert_stock_env(intent, inputs)
            report = dict(pcbnew_version='authored CPU custom facts', pads=[
                dict(number='1', type='smd', shape='custom', x_nm=0, y_nm=0,
                     orientation_microdegrees=0, size_nm=[1000000,1000000], anchor='rect',
                     layer_outline_counts=[1], outline_count=1, bbox_nm=[-1000000,-1000000,2000000,1000000]),
                dict(number='2', type='smd', shape='rect', x_nm=4000000, y_nm=0,
                     orientation_microdegrees=0, size_nm=[1000000,1000000])])
            if self.custom_error == 'timeout':
                return dict(execution='timeout', exit_code=None)
            if self.custom_error == 'missing-output':
                return dict(execution='ok', exit_code=0)
            if self.custom_error == 'islands':
                report['pads'][0]['outline_count'] = 2
            if self.custom_error == 'bbox':
                report['pads'][0]['bbox_nm'] = [0,0,0,0]
            if self.custom_error == 'number':
                report['pads'][0]['number'] = '9'
            if self.custom_error == 'anchor':
                report['pads'][0]['anchor'] = 'circle'
            output.write_text(json.dumps(report))
            if self.custom_error == 'mutation':
                next(inputs.glob('*.kicad_mod')).write_bytes(b'changed custom input')
            return dict(execution='ok', exit_code=0)
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
        self.assert_stock_env(intent, source.parent)
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

    def assert_stock_env(self, intent, inputs):
        if intent.isolation == 'host':
            for suffix, folder in (('SYMBOL', 'symbols'), ('FOOTPRINT', 'footprints'), ('TEMPLATE', 'template')):
                self.assertEqual(intent.env['KICAD10_'+suffix+'_DIR'], str(inputs/'stock'/folder))

    def install_custom(self, text=None, name='CAP_2PAD'):
        data = (text if text is not None else custom()).replace('OwnedCustom', name).encode()
        path = name+'.kicad_mod'
        self.captures.joinpath(path).write_bytes(data)
        member = dict(path=path, sha256=hashlib.sha256(data).hexdigest())
        members = self.manifest['libraries'][0]['members']
        members[:] = [m for m in members if m['path'] != path] + [member]

    def test_qualified_custom_control_replans_applies_and_indexes_unused_members(self):
        self.install_custom()
        self.install_custom(name='Unused')
        before = self.board.read_bytes()
        with patch.object(native_run, 'run', self.native):
            planned = self.plan()
            self.assertEqual(planned['execution'], 'ok', planned)
            result = fa.apply(planned, self.board, self.xml, self.manifest, self.captures,
                self.root/'custom-output', self.root/'custom-apply', _execution=self.execution)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual(self.board.read_bytes(), before)
        geometry = [c for c in self.calls if '-c' in c.argv]
        self.assertEqual([c.argv[-2] for c in geometry], ['CAP_2PAD','Unused']*2)
        self.assertEqual(len({c.env['HOME'] for c in self.calls}), len(self.calls))
        self.assertEqual(len({c.env['KICAD_CONFIG_HOME'] for c in self.calls}), len(self.calls))
        self.assertTrue(all(c.isolation == 'host' for c in self.calls))
        self.assertTrue(all(Path(c.env['HOME']).parent/'footprint.json' in
                            list((self.root/'library-probes').glob('member-*/work/footprint.json'))
                            for c in geometry))

    def test_custom_native_refusals_identify_the_member_and_stay_unknown(self):
        self.install_custom(name='Unused')
        for error in ('islands','bbox','number','anchor','timeout','missing-output','mutation'):
            with self.subTest(error=error):
                self.custom_error = error
                with patch.object(native_run, 'run', self.native):
                    result = self.plan(name='custom-'+error)
                self.assertEqual((result['execution'],result['outcome']), ('failed','unknown'), result)
                self.assertIn('Unused.kicad_mod', result['findings'][0]['message'])
        self.custom_error = None

    def test_custom_bounds_drill_and_unsupported_syntax_refuse_without_geometry_loading(self):
        variants = [custom().replace('(end 2 1)', '(end 3000 1)'),
                    custom().replace('(zone_connect 2)', '(zone_connect 1)'),
                    custom().replace('(size 1 1)', '(size 1 1) (drill 0.5)', 1),
                    'not a footprint (']
        for index, source in enumerate(variants):
            with self.subTest(index=index):
                self.install_custom(source)
                self.calls = []
                with patch.object(native_run, 'run', self.native):
                    result = self.plan(name='unsupported-'+str(index))
                self.assertEqual(result['execution'], 'failed', result)
                self.assertIn('CAP_2PAD.kicad_mod', result['findings'][0]['message'])
                self.assertEqual(len(self.calls), 1)  # Board facts only.

    def test_standalone_custom_index_retains_its_sandbox_protocol(self):
        self.install_custom()
        with patch.object(native_run, 'run', self.native):
            result = self.plan(execution=False)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual([c.isolation for c in self.calls], ['sandbox','sandbox'])
        self.assertEqual(self.calls[1].argv[-2:], ('agent_ee.footprint_probe','CAP_2PAD'))
        self.assertIsNone(self.calls[1].env)

    def test_mount_guard_accepts_only_supported_effective_layouts(self):
        base = mount_view()
        def mount(point, flags='rw', kind='ext4', ident=99):
            return f'\n{ident} 0 0:1 / {point} {flags} - {kind} none {flags}'
        positive = [base, base+mount('/lib','ro'), base+mount('/lib64','ro')+mount('/bin','ro',ident=100),
                    base+mount('/proc/sys','ro','proc'), base+mount('/dev/mqueue','rw','mqueue'),
                    base+mount('/dev/null','rw','devtmpfs'), base+mount('/work/out'),
                    base+mount('/candidate')+mount('/candidate','ro',ident=100)]
        negative = [base+mount(root) for root in ('/lib','/lib64','/bin','/sbin')]
        negative += [base.replace('/dev rw - tmpfs', '/dev rw - devtmpfs'),
                     base+mount('/tmp/hosthome'), base+mount('/tmp/hosthome','ro','tmpfs'),
                     base+mount('/dev/hosthome'), base+mount('/proc/hosthome'),
                     base+mount('/candidate'), base+mount('/usr/lib'),
                     base+mount('/etc'), base+mount('/app'), base+mount('/tool/agent-ee'),
                     base+mount('/lib/x'), base+'\nmalformed',
                     base+mount('/lib','ro',ident=1), base+mount('/candidate','ro,rw')]
        for accepted, views in ((True, positive),(False, negative)):
            for view in views:
                with self.subTest(accepted=accepted,view=view), patch.object(Path,'read_text',return_value=view), \
                     patch.object(fa,'__file__','/tool/agent-ee/agent_ee/forward_annotation.py'):
                    if accepted:
                        fa._StageExecution()
                    else:
                        with self.assertRaises(fa.AnnotationError):
                            fa._StageExecution()

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
                    self.assertFalse(Path(kw['work']).exists())
                    if confined:
                        self.assertEqual(intent.env['HOME'], str(out/'netlist/home'))
                        self.assertEqual(intent.env['KICAD_CONFIG_HOME'], str(out/'netlist/kicad-config'))
                        self.assert_stock_env(intent, candidate)
                        self.assertEqual(intent.env['PYTHONPATH'], '/tool/agent-ee')
                    else:
                        self.assertIsNone(intent.env)
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

    def test_plane_children_inherit_fixed_stage_but_standalone_remains_sandboxed(self):
        from agent_ee import add_planes, outline_containment
        inputs = self.root/'plane-inputs';inputs.mkdir()
        (inputs/'board.kicad_pcb').write_bytes(self.board.read_bytes())
        (inputs/'plan.json').write_text('{}')
        for confined in (True, False):
            calls = []
            def external(intent, *, work, inputs=None, **kwargs):
                calls.append((intent, inputs));work = Path(work);work.mkdir()
                (work/'console.log').write_text('authored plane process boundary')
                (work/'copper-containment.json').write_text('[]')
                return dict(execution='ok', exit_code=0)
            execution = self.execution if confined else None
            with patch.object(native_run, 'run', external):
                for operation in ('apply','inspect'):
                    work = self.root/(operation+str(confined))
                    add_planes._child(inputs, work, [operation, '/candidate/board.kicad_pcb',
                        '/candidate/plan.json', '/work/result.json'], _execution=execution)
                add_planes._drc(inputs, 'board.kicad_pcb', self.root/('drc'+str(confined)), _execution=execution)
                work = self.root/('containment'+str(confined))
                self.assertEqual(outline_containment.sandboxed(inputs, 'board.kicad_pcb', work,
                    _execution=execution), [])
                self.assertTrue((work/'execution.json').is_file())
            self.assertEqual(len(calls), 4)
            for intent, captured in calls:
                self.assertEqual(intent.isolation, 'host' if confined else 'sandbox')
                if confined:
                    self.assertIsNone(captured)
                    self.assertEqual(intent.env['PYTHONPATH'], '/tool/agent-ee')
                    self.assertTrue(Path(intent.env['HOME']).is_relative_to(self.root))
                    self.assertIn(str(inputs/'board.kicad_pcb'), intent.argv)
                    self.assertFalse(any(str(arg).startswith(('/candidate/','/work/')) for arg in intent.argv))
                else:
                    self.assertEqual(captured, inputs)
                    self.assertIsNone(intent.env)
                    self.assertIn('/candidate/board.kicad_pcb', intent.argv)

    def test_plane_stage_entrypoint_refuses_host_and_cannot_choose_input_output_paths(self):
        from agent_ee import add_planes
        argv = ['--board','board.kicad_pcb','--plan','planes.json']
        with patch.object(Path, 'read_text', return_value=mount_view()), \
             patch.object(add_planes, 'generate') as generate:
            with self.assertRaises(fa.AnnotationError):add_planes._stage_main(argv)
            generate.assert_not_called()  # Live source import is not the captured /tool mount.
        with patch.object(Path, 'read_text', return_value=mount_view()), \
             patch.object(fa, '__file__', '/tool/agent-ee/agent_ee/forward_annotation.py'), \
             patch.object(add_planes, 'generate', return_value={'execution':'ok'}) as generate:
            self.assertEqual(add_planes._stage_main(argv), 0)
            args, kw = generate.call_args
            self.assertEqual(args, ('/candidate','board.kicad_pcb','planes.json','/work/out'))
            self.assertIsInstance(kw['_execution'], fa._StageExecution)
            for option in ('--inputs', '--out'):
                generate.reset_mock()
                with patch('sys.stderr'), self.assertRaises(SystemExit) as refused:
                    add_planes._stage_main(argv+[option, '/tmp/elsewhere'])
                self.assertEqual(refused.exception.code, 2)
                generate.assert_not_called()
        with patch.object(add_planes, 'generate', return_value={'execution':'failed'}) as generate:
            self.assertEqual(add_planes.main(argv), 1)
            self.assertNotIn('_execution', generate.call_args.kwargs)

    def test_plane_private_input_mutation_and_stage_child_failure_preserve_refusal(self):
        from agent_ee import add_planes
        inputs=self.root/'private-plane-inputs';inputs.mkdir();member=inputs/'board.kicad_pcb'
        member.write_bytes(self.board.read_bytes());(inputs/'plan.json').write_text('{}')
        def mutate(intent, *, work, **kwargs):
            Path(work).mkdir();(Path(work)/'console.log').write_text('authored boundary mutation')
            member.write_bytes(b'changed private input')
            return dict(execution='ok',exit_code=0)
        with patch.object(native_run, 'run', mutate):
            with self.assertRaisesRegex(fa.AnnotationError,'changed its private inputs'):
                add_planes._child(inputs,self.root/'mutated-apply',['apply','/candidate/board.kicad_pcb',
                    '/candidate/plan.json','/work/result'],_execution=self.execution)
            member.write_bytes(self.board.read_bytes())
            with self.assertRaisesRegex(ValueError,'changed its private inputs'):
                add_planes._drc(inputs,'board.kicad_pcb',self.root/'mutated-drc',_execution=self.execution)
        candidate=self.root/'plane-candidate';candidate.mkdir();board=candidate/'board.kicad_pcb'
        board.write_bytes(self.board.read_bytes())
        plan={'schema':add_planes.SCHEMA,'zones':[dict(net='GND',layer='F.Cu',outline='board',
            clearance_mm=.25,min_width_mm=.2,priority=0,island_removal='always',
            thermal=dict(connection='thermal',gap_mm=.3,spoke_width_mm=.3))]}
        (candidate/'planes.json').write_text(json.dumps(plan));out=self.root/'plane-failed';execution=context(out);calls=[]
        def failed(intent, *, work, **kwargs):
            calls.append(intent);Path(work).mkdir();(Path(work)/'console.log').write_text('authored native failure')
            return dict(execution='failed',exit_code=9)
        with patch.object(native_run,'run',failed):
            report=add_planes.generate(candidate,'board.kicad_pcb','planes.json',out,_execution=execution)
        self.assertEqual((report['execution'],report['outcome']),('failed','unknown'))
        self.assertIn('authored native failure',str(report['findings']))
        self.assertFalse((out/'board.kicad_pcb').exists());self.assertEqual(board.read_bytes(),self.board.read_bytes())
        self.assertEqual(calls[0].isolation,'host');self.assertTrue((out/'native/console.log').is_file())
        self.assertEqual((out/'native-input/board.kicad_pcb').read_bytes(),board.read_bytes())

    def test_private_footprint_helper_passes_the_concrete_library_and_never_clobbers_output(self):
        from agent_ee import footprint_probe
        output = self.root/'native-helper.json'
        def external_native_extract(name, *, _library):
            self.assertEqual(name, 'OwnedCustom')
            self.assertEqual(_library, str(self.captures))
            return {'pcbnew_version':'authored native boundary','pads':[]}
        # extract is the unchanged native pcbnew boundary; no native import occurs.
        with patch.object(footprint_probe, 'extract', external_native_extract):
            footprint_probe._stage_main(str(self.captures), 'OwnedCustom', str(output))
            original = output.read_bytes()
            self.assertEqual(json.loads(original)['pcbnew_version'], 'authored native boundary')
            with self.assertRaises(FileExistsError):
                footprint_probe._stage_main(str(self.captures), 'OwnedCustom', str(output))
        self.assertEqual(output.read_bytes(), original)
