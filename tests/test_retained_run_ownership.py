"""AGENTEE-548: build custody owns records; scaffold products remain inputs."""
import ast
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from agent_ee import requirement_map, retained_run
from agent_ee.storage import digest, read_json, write_json

ROOT = Path(retained_run.__file__).resolve().parents[2]
RECORDS = {'scope.json', 'planning.scope.json', 'accounting.json', 'build-report.json',
           'qualification.json', 'requirements.json', 'review'}


def record_writes(source):
    """Find ordinary filesystem writes to the declared build-record paths."""
    tree = ast.parse(source)
    imports = {alias.asname or alias.name: alias.name
               for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    blocks = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    blocks.append(ast.Module(body=[node for node in tree.body
                                  if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[]))
    found = []
    for block in blocks:
        bindings = {}
        for node in ast.walk(block):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings.setdefault(target.id, []).append(node.value)

        def strings(node, seen=frozenset()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return {node.value}
            if isinstance(node, ast.Name) and node.id not in seen:
                return set().union(*(strings(value, seen | {node.id}) for value in bindings.get(node.id, [])))
            if isinstance(node, ast.Call) and getattr(node.func, 'attr', getattr(node.func, 'id', '')) == 'scope_path':
                product = any(k.arg == 'product' and not (isinstance(k.value, ast.Constant)
                                                         and k.value.value is False) for k in node.keywords)
                return {'product/scope.json' if product else 'scope.json'}
            return set().union(*(strings(child, seen) for child in ast.iter_child_nodes(node)))

        for call in (node for node in ast.walk(block) if isinstance(node, ast.Call)):
            name = getattr(call.func, 'attr', getattr(call.func, 'id', ''))
            name = imports.get(name, name)
            target = None
            if name in ('write_json', 'publish', 'copyfile', 'copytree', 'copy2', 'move', 'run_review'):
                index = 1 if name in ('publish', 'copyfile', 'copytree', 'copy2', 'move', 'run_review') else 0
                if len(call.args) > index:
                    target = call.args[index]
            elif name in ('write_bytes', 'write_text') and isinstance(call.func, ast.Attribute):
                target = call.func.value
            elif name in ('open', 'replace', 'rename'):
                mode = next((k.value for k in call.keywords if k.arg == 'mode'),
                            call.args[1] if len(call.args) > 1 else ast.Constant('r'))
                writing = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in 'wax+')
                if name == 'open' and writing:
                    target = call.func.value if isinstance(call.func, ast.Attribute) else call.args[0]
                elif name != 'open' and call.args:
                    target = call.args[0] if isinstance(call.func, ast.Attribute) and len(call.args) == 1 else call.args[-1]
            paths = strings(target) if target is not None else set()
            if any(RECORDS.intersection(PurePosixPath(value).parts) for value in paths):
                found.append((call.lineno, paths))
            if name == 'write_scope' and any(k.arg == 'product' for k in call.keywords):
                found.append((call.lineno, {'product/scope.json'}))
    return found


class RetainedRunOwnershipTests(unittest.TestCase):
    def test_variable_product_path_cannot_bypass_the_guard(self):
        writes = record_writes('''
def forbidden(directory, flag):
    path = scope_path(directory, product=flag)
    write_json(path, {})
''')
        self.assertTrue(any('product/scope.json' in paths for _, paths in writes))

    def test_build_modules_delegate_record_writes_to_the_custody_owner(self):
        modules = sorted((ROOT / 'src/agent_ee').glob('build_*.py'))
        modules.append(ROOT / 'src/agent_ee/qualification.py')
        for path in modules:
            with self.subTest(module=path.name):
                self.assertEqual(record_writes(path.read_text()), [], str(path))
        owner = ROOT / 'src/agent_ee/retained_run.py'
        producer_writes = [(line, paths) for line, paths in record_writes(owner.read_text())
                           if {'product', 'scope.json'} <= {part for value in paths
                                                           for part in PurePosixPath(value).parts}]
        self.assertEqual(producer_writes, [], 'The scaffold receipt owns product/scope.json')

    def test_receipted_scaffold_scope_is_read_without_becoming_a_build_owned_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'inputs'
            source.mkdir()
            (source / 'native.txt').write_text('Synthetic producer input; no native acceptance claim.\n')
            inventory = requirement_map.input_inventory(source)
            report = retained_run.initialize(root, dict(plan_sha256='a' * 64, source=inventory, runtime={}))
            retained_run.capture_source(root, source, {}, inventory)
            previous = root / 'source'
            for name in retained_run.STAGES[:3]:
                attempt = retained_run.begin_stage(root, name)
                candidate = attempt.capture(previous)
                if name == 'requirements':
                    retained_run.write_scope(attempt.directory, dict(board='requirements.kicad_pcb'))
                    retained_run.write_accounting(attempt.directory, dict(requirements=[]))
                if name == 'scaffold':
                    # The one producer-owned scope input and its producer receipt.
                    producer_scope = attempt.directory / 'product/scope.json'
                    producer_receipt = attempt.directory / 'product/scaffold-receipt.json'
                    producer_scope.parent.mkdir()
                    write_json(producer_scope, dict(board='producer.kicad_pcb', checks=[]))
                    write_json(producer_receipt, dict(outputs={'scope.json': digest(producer_scope)}))
                    sealed = {path: path.read_bytes() for path in (producer_scope, producer_receipt)}
                    retained_run.write_scope(attempt.directory, dict(board='build-owned.kicad_pcb'))
                    with self.assertRaises(TypeError):
                        retained_run.write_scope(attempt.directory, dict(board='overwrite.kicad_pcb'), product=True)
                    planning = retained_run.write_planning_scope(attempt.directory, dict(board='planning.kicad_pcb'))
                    self.assertEqual(planning, attempt.directory / 'planning.scope.json')
                    self.assertEqual(read_json(planning), dict(board='planning.kicad_pcb'))
                    retained_run.write_requirements(attempt.directory, dict(requirements=[]))
                retained_run.record_completion(root, report, attempt, candidate)
                previous = candidate
            retained_run.write_report(root, report)
            retained_run.write_qualification(root, dict(accepted=False))
            selected = retained_run.scope(root)
            self.assertEqual(selected.path, producer_scope)
            self.assertEqual(selected.document, dict(board='producer.kicad_pcb', checks=[]))
            self.assertEqual(retained_run.read_scope(attempt.directory, product=True), selected.document)
            self.assertEqual({path: path.read_bytes() for path in sealed}, sealed)
            self.assertEqual(retained_run.snapshot(root), report)
            self.assertEqual(retained_run.qualification(root), dict(accepted=False))


if __name__ == '__main__':
    unittest.main()
