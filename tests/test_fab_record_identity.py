"""AGENTEE-544/556: retained fab records stay byte-identical through real composition."""

from datetime import datetime
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from agent_ee import fab_consistency, fab_export, fab_package, native_run
from agent_ee.checks import CheckError, Ctx
from agent_ee.storage import json_bytes, read_json, write_json

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/fab-records/retained.zip'


class FabRecordIdentityTests(unittest.TestCase):
    def test_retained_package_and_consistency_records_keep_exact_bytes(self):
        # The oracle predates AGENTEE-544. Only the native process and wall clock
        # boundaries are replayed; no check, parser, gate or record writer is patched.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(FIXTURE) as archive:
                archive.extractall(root)
            run = root / 'run'
            saved = root / 'native'
            manifest = read_json(root / 'manifest.json')

            class Replay(native_run.Recorded):
                def run(self, intent, **kwargs):
                    result = super().run(intent, **kwargs)
                    source = saved / Path(kwargs['work']).relative_to(run)
                    result['duration_s'] = read_json(source / 'execution.json')['duration_s']
                    write_json(Path(kwargs['work']) / 'execution.json', result)
                    return result

            def respond(call):
                source = saved / call.work.relative_to(run)
                record = read_json(source / 'execution.json')
                self.assertEqual(list(call.intent.argv), record['argv'])
                return native_run.Outcome(
                    exit_code=record['exit_code'], execution=record['execution'],
                    stdout=(source / 'stdout.log').read_bytes(),
                    stderr=(source / 'stderr.log').read_bytes(),
                    files={str(p.relative_to(source)): p.read_bytes()
                           for p in source.rglob('*') if p.is_file()
                           and p.name not in ('execution.json', 'stdout.log', 'stderr.log', 'console.log')})

            for kind in ('package', 'consistency'):
                for case in (('pass',) if kind == 'package' else ('pass', 'missing-netlist')):
                    with self.subTest(kind=kind, case=case):
                        candidate = read_json(run / 'candidate.json')
                        spec = candidate['scope']['checks'][-1]
                        if kind == 'consistency':
                            spec['kind'] = 'fab_export'
                            spec = {'id': 'consistency', 'kind': 'fab_consistency',
                                    'params': {'export_check': 'fab'}}
                            candidate['scope']['checks'].append(spec)
                            write_json(run / 'checks/fab.json', read_json(root / 'native-export.json'))
                        prior = run / 'checks/netlist.json'
                        if case == 'missing-netlist':
                            prior.rename(prior.with_suffix('.saved'))
                        ctx = Ctx(run, candidate, spec, time.monotonic() + 60)
                        handler = (fab_package.check_fab_package if kind == 'package'
                                   else fab_consistency.check_fab_consistency)
                        with patch.object(fab_export, 'datetime') as clock, native_run.using(Replay(respond)):
                            clock.now.return_value = datetime.fromisoformat(manifest['generated_utc'])
                            try:
                                handler(ctx)
                                ctx.validate_result()
                            except CheckError as exc:
                                ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
                        expected = root / 'expected' / f'{case}-{kind}-before.json'
                        self.assertEqual(json_bytes(ctx.result), expected.read_bytes())
                        if case == 'missing-netlist':
                            prior.with_suffix('.saved').rename(prior)


if __name__ == '__main__':
    unittest.main()
