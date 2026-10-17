"""Real captured citations remain immutable across a scripted bench review epoch."""
import contextlib
import io
from pathlib import Path
import unittest

import test_campaign as campaign_fixture
from agent_ee.cli import run_review, verify
from agent_ee.empirical import bench_review
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain


class EmpiricalFactsIntegrationTests(unittest.TestCase):
    def test_native_pin_facts_and_measurements_survive_calibration_reconstruction(self):
        require_toolchain(native=True, tools=('pdftotext',))
        helper = campaign_fixture.CampaignIntegrationTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = helper.root
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/datasheet-pins'
        scope = read_json(fixture / 'control.scope.json')
        for item in scope['inputs']:
            item['path'] = str((fixture / item['path']).resolve())
        scope['schema'] = 'agent-ee.scope/3'
        scope['review_profile'] = helper.profile
        scope['inputs'] += helper.scope['inputs']
        scope['checks'] += helper.scope['checks']
        scope['baseline']['requirements'] += helper.scope['baseline']['requirements']
        for requirement in scope['baseline']['requirements']:
            requirement['intent'] = 'acceptance'
        write_json(root / 'source/design.json', {'value': 5})
        write_json(root / 'combined.json', scope)
        # Only model dispatch/runtime admission is scripted; native checks and
        # Poppler extraction execute through the production sandbox.
        from agent_ee.cli import runtime_files
        runtime_files.return_value.update({name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
                                           for name in ('kicad-cli', 'pcbnew')})
        original = root / 'original'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_review(root / 'combined.json', original, 90), 0)
        result = verify(original)
        self.assertEqual({c['id']: c['outcome'] for c in result['checks']}['pin-map'], 'pass')
        sample = {'check_id': 'response', 'metric': 'output', 'unit': 'V',
                  'condition': 'load', 'value': 6, 'uncertainty': 0}
        write_json(root / 'instrument.json', {'samples': [sample]})
        write_json(root / 'bench.json', {'schema': 'agent-ee.bench/1',
            'candidate_id': result['candidate_id'], 'prototype': 'synthetic',
            'instrument': 'synthetic', 'instrument_calibration': 'synthetic',
            'procedure': 'coordinator regression only', 'observed_utc': '2026-10-17',
            'artifacts': [{'path': 'instrument.json', 'sha256': digest(root / 'instrument.json')}],
            'observations': [{**sample, 'artifact': 'instrument.json', 'locator': '0'}]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_review(original, root / 'bench.json', root / 'bench', 90), 0)
        revised = verify(root / 'bench/review')
        by_id = {c['id']: c for c in revised['checks']}
        self.assertEqual(by_id['pin-map']['outcome'], 'pass')
        metric = by_id['response']['details']['metrics']['output']
        self.assertEqual((metric['nominal'], metric['lower'], metric['upper']), (5, 6, 6))
        self.assertEqual(read_json(original / 'baseline.json'), read_json(root / 'bench/review/baseline.json'))
