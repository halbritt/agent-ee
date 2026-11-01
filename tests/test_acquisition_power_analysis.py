"""Replay exact generated inputs and an actual retained LTspice removal trace."""
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from agent_ee import acquisition_power_analysis as power
from agent_ee.storage import json_bytes

SOURCE = Path(os.environ.get('AGENT_EE_POWER_PRODUCTION', '/nonexistent/production'))
MODELS = Path(os.environ.get('AGENT_EE_ADI_MODEL_LIBRARY', '/nonexistent/models'))
TRACE = Path(os.environ.get('AGENT_EE_POWER_REMOVAL_TRACE', '/nonexistent/removal'))
sources_available = (SOURCE / 'circuit.json').is_file() and (MODELS / 'sub/ADP7104-3.3.sub').is_file()
trace_available = (TRACE / 'high-z-removal.raw').is_file()


@unittest.skipUnless(sources_available, 'exact generated production and captured ADI library required')
class PowerApplicationInputsTests(unittest.TestCase):
    def setUp(self):
        self.args = [(SOURCE / 'circuit.json').read_bytes(), (SOURCE / 'parts.json').read_bytes(),
                     (MODELS / 'sub/ADP7104-3.3.sub').read_bytes(),
                     (MODELS / 'sym/PowerProducts/ADP7104-3.3.asy').read_bytes()]

    def test_model_order_agrees_with_vendor_symbol_and_native_circuit(self):
        before = list(self.args)
        files, declaration = power.inputs(*self.args)
        self.assertEqual(self.args, before)
        self.assertFalse(declaration['accepted'])
        self.assertEqual(declaration['file_release'], 'not_assessed')
        self.assertEqual(declaration['circuit_sha256'], sha256(self.args[0]).hexdigest())
        self.assertEqual(declaration['parts_sha256'], sha256(self.args[1]).hexdigest())
        names, orders = [], []
        for line in self.args[3].decode('ascii').splitlines():
            if line.startswith('PINATTR PinName '):
                names.append(line.split()[-1])
            if line.startswith('PINATTR SpiceOrder '):
                orders.append(int(line.split()[-1]))
        self.assertEqual([(p['order'], p['name']) for p in declaration['model_pin_map']], list(zip(orders, names)))
        circuit = json.loads(self.args[0])
        for pin in declaration['model_pin_map']:
            for number in pin['native_pins']:
                terminal = dict(component='U1', pin=dict(number=number))
                nets = [net['name'] for net in circuit['nets'] if terminal in net['terminals']]
                self.assertEqual(nets, [] if pin['net'] is None else [pin['net']])
        self.assertEqual(files['ADP7104-3.3.sub'], self.args[2])
        self.assertEqual(files['ADP7104-3.3.asy'], self.args[3])

    def test_changed_circuit_and_model_custody_refuse(self):
        for index in (0, 2, 3):
            args = list(self.args)
            args[index] += b'\n'
            with self.subTest(input=index), self.assertRaises(ValueError):
                power.inputs(*args)

    def test_different_purchased_regulator_capacitor_or_bleeder_refuses(self):
        original = json.loads(self.args[1])
        for ref in ('U1', 'C1', 'C2', 'C3', 'R6'):
            changed = copy.deepcopy(original)
            next(p for p in changed['parts'] if p['id'] == ref)['mpn'] = 'different-device'
            args = list(self.args)
            args[1] = json_bytes(changed)
            with self.subTest(reference=ref), self.assertRaisesRegex(ValueError, 'purchasing application differs'):
                power.inputs(*args)


@unittest.skipUnless(trace_available, 'actual retained native high-impedance removal trace required')
class PowerRemovalTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (TRACE / 'high-z-removal.raw').read_bytes()
        cls.log = (TRACE / 'high-z-removal.log').read_bytes()

    def test_actual_complete_trace_keeps_model_and_board_acceptance_separate(self):
        result = power.measurements(self.raw, self.log)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['file_release'], 'not_assessed')
        self.assertEqual(result['trace_sha256'], sha256(self.raw).hexdigest())
        self.assertEqual(result['log_sha256'], sha256(self.log).hexdigest())
        self.assertEqual(result['observations']['time_range_s'], [0, .08])
        self.assertTrue(all(result['model_predicates'].values()), result)
        self.assertAlmostEqual(result['observations']['steady_vout_range_v'][0], 3.299676134, places=7)
        self.assertAlmostEqual(result['observations']['steady_source_max_a'], .010666365827, places=9)
        self.assertAlmostEqual(result['observations']['final_vout_v'], .03390823453, places=7)
        self.assertEqual(result['observations']['post_removal_bus_max_abs_a'], 0)
        self.assertEqual(result['observations']['post_removal_reserve_max_abs_a'], 0)
        self.assertAlmostEqual(result['observations']['post_removal_source_range_a'][1], .0000182664357167, places=12)
        self.assertEqual(result['observations']['post_removal_source_power_range_w'][1], 0)

    def test_truncated_wrong_variables_and_nonfinite_samples_refuse(self):
        text = self.raw.decode('ascii').replace('\r\n', '\n')
        header, values = text.split('Values:\n')
        first = values.splitlines()[0]
        changed = header + 'Values:\n' + values.replace(first, first.split()[0] + '\tNaN', 1)
        cases = [self.raw[:-100], self.raw.replace(b'V(vout)', b'V(wrong)', 1), changed.encode('ascii')]
        for raw in cases:
            with self.subTest(hash=sha256(raw).hexdigest()), self.assertRaises(ValueError):
                power.read_trace(raw)

    def test_incomplete_solver_log_or_wrong_temperature_refuses(self):
        for log in (self.log.replace(b'Total elapsed time:', b'No completion:'),
                    self.log.replace(b'temp = 25', b'temp = 50'), self.log + b'\nFatal solver error\n'):
            with self.subTest(log=sha256(log).hexdigest()), self.assertRaisesRegex(ValueError, 'completion'):
                power.measurements(self.raw, log)
