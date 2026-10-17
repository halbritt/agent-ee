"""Independent presence oracle from the KiCad 10.0.7 native writer."""
import copy
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.storage import read_json, write_json
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import run_checks
from agent_ee.baseline import freeze_evidence, verify_evidence, validate_witnesses, load_baseline
from toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/netclass-completeness'


class NetclassTests(unittest.TestCase):
    def run_project(self, project):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root/'native.kicad_pro').write_text(project if isinstance(project, str) else json.dumps(project))
        (root/'requirements.md').write_text('Default netclass must retain complete KiCad 10.0.7 fields.')
        (root/'qualification.json').write_bytes((FIXTURE/'qualification.json').read_bytes())
        paths = ['native.kicad_pro', 'requirements.md', 'qualification.json']
        scope = {'schema':'agent-ee.scope/3', 'inputs':[{'path':p,'at':p} for p in paths],
                 'checks':[{'id':'classes', 'kind':'netclass_completeness', 'params':{
                     'project':'native.kicad_pro', 'qualification':'qualification.json'}}],
                 'requirements':['requirements.md'], 'baseline':{
                     'requirements':[{'id':'complete','statement':'Complete Default',
                         'source':{'path':'requirements.md','locator':'Default'},
                         'checks':['classes'],'intent':'acceptance'}],
                     'targets':['requirements.md'], 'reference_architectures':['requirements.md']}}
        write_json(root/'scope.json', scope)
        run = root/'run'
        run.mkdir()
        capture(root/'scope.json', run)
        return run, run_checks(run, time.monotonic()+30)[0]

    def test_native_default_missing_wire_width_and_zero(self):
        project = read_json(FIXTURE/'native.kicad_pro')
        _, control = self.run_project(project)
        self.assertEqual((control['execution'],control['outcome']), ('ok','pass'), control)
        del project['net_settings']['classes'][0]['wire_width']
        _, defect = self.run_project(project)
        self.assertEqual((defect['execution'],defect['outcome']), ('ok','defect'), defect)
        self.assertEqual(defect['details']['missing'], [{'class':'Default','keys':['wire_width']}])
        project['net_settings']['classes'][0]['wire_width'] = 0
        _, zero = self.run_project(project)
        self.assertEqual(zero['outcome'], 'pass', zero)

    def test_every_native_default_field_is_required_but_custom_inherits(self):
        project = read_json(FIXTURE/'native.kicad_pro')
        native = project['net_settings']['classes'][0]
        for key in native:
            if key == 'name':
                continue
            altered = copy.deepcopy(project)
            del altered['net_settings']['classes'][0][key]
            with self.subTest(key=key):
                _, result = self.run_project(altered)
                self.assertEqual(result['outcome'], 'defect', result)
                self.assertEqual(result['details']['missing'], [{'class':'Default','keys':[key]}])
        project['net_settings']['classes'].insert(0, {'name':'Inherited'})
        _, result = self.run_project(project)
        self.assertEqual(result['outcome'], 'pass', result)

    def test_default_is_selected_by_exact_name_not_position(self):
        project = read_json(FIXTURE/'native.kicad_pro')
        classes = project['net_settings']['classes']
        complete = copy.deepcopy(classes[0])
        del classes[0]['wire_width']
        classes.insert(0, {**complete, 'name':'Power'})
        _, later = self.run_project(project)
        self.assertEqual(later['outcome'], 'defect', later)
        self.assertEqual(later['details']['missing'], [{'class':'Default','keys':['wire_width']}])
        classes[1]['wire_width'] = 0
        classes.insert(0, {'name':'default'})
        _, exact = self.run_project(project)
        self.assertEqual((exact['execution'],exact['outcome']), ('ok','pass'), exact)
        project['net_settings']['classes'] = [{**complete, 'name':'default'}]
        _, renamed = self.run_project(project)
        self.assertEqual(renamed['details']['missing'], [{'class':'Default','keys':sorted(complete)}])

    def test_fixture_is_reproduced_by_native_writer(self):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'native.kicad_pro'
            generated = subprocess.run(['/usr/bin/python3', str(FIXTURE/'generate.py'), str(out)],
                                       capture_output=True, text=True, timeout=120)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual(out.read_bytes(), (FIXTURE/'native.kicad_pro').read_bytes())

    def test_missing_default_duplicate_and_malformed_are_explicit(self):
        project = read_json(FIXTURE/'native.kicad_pro')
        project['net_settings']['classes'] = []
        _, missing = self.run_project(project)
        self.assertEqual(missing['outcome'], 'defect', missing)
        for value in ('{', '[]', '{"net_settings":{}}',
                      '{"net_settings":{},"net_settings":{}}'):
            with self.subTest(value=value):
                _, result = self.run_project(value)
                self.assertEqual((result['execution'],result['outcome']), ('malformed','unknown'), result)
        project = read_json(FIXTURE/'native.kicad_pro')
        project['net_settings']['classes'] *= 2
        _, duplicate = self.run_project(project)
        self.assertEqual((duplicate['execution'],duplicate['outcome']), ('malformed','unknown'), duplicate)
        project['net_settings']['meta']['version'] = 6
        _, unsupported = self.run_project(project)
        self.assertEqual((unsupported['execution'],unsupported['outcome']), ('unsupported','unknown'), unsupported)

    def test_evidence_baseline_and_tamper(self):
        project = read_json(FIXTURE/'native.kicad_pro')
        del project['net_settings']['classes'][0]['wire_width']
        run, result = self.run_project(project)
        self.assertIn('qualification.json', load_baseline(run)['files'])
        self.assertNotIn('native.kicad_pro', load_baseline(run)['files'])
        freeze_evidence(run, [result])
        validate_witnesses(run, [{'requirement_id':'complete','check_id':'classes'}],
                           [{'path':'checks/classes.json','locator':'missing wire_width'}])
        target = run/'work/classes/netclasses.json'
        target.write_bytes(target.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            verify_evidence(run, [result], required=True)
        (run/'inputs/qualification.json').write_text('{}')
        with self.assertRaises(ValueError):
            verify_snapshot(run)

    def test_unreadable_captured_project_is_malformed(self):
        from agent_ee.checks import Ctx, CheckError, check_netclass_completeness
        project = read_json(FIXTURE/'native.kicad_pro')
        run, _ = self.run_project(project)
        (run/'inputs/native.kicad_pro').unlink()
        manifest = read_json(run/'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic()+30)
        with self.assertRaises(CheckError) as raised:
            check_netclass_completeness(ctx)
        self.assertEqual(raised.exception.execution, 'malformed')
