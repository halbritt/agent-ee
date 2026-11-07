"""Pure admission check for the byte-bound historical removal reader."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.acquisition import film_removal as removal
from agent_ee.storage import json_bytes


class ExecutedRemovalReaderTests(unittest.TestCase):
    def test_attach_preserves_executed_reader_and_refuses_one_byte_drift(self):
        # Minimize the other admission inputs; this is not native execution evidence.
        declaration = json.loads((removal.DATA / 'removal-evidence.json').read_bytes())
        self.assertEqual(declaration['power_analysis_sha256'],
                         '0d9cc96a15de733da82d2bd5dbf67ee556c0c5ecc33a23655d0122d4b56c56b9')
        circuit, parts = b'{"synthetic":"circuit"}', b'{"synthetic":"parts"}'
        matrix = json_bytes(dict(execution='ok', completed=[
            dict(case=name) for name in declaration['cases']]))
        document = b'Synthetic removal basis for the reader admission check.\n'
        captures = {removal.AT + 'matrix-completion.json': matrix,
                    'power-reference/removal-bounds.md': document}
        declaration.update(circuit_sha256=sha256(circuit).hexdigest(),
                           parts_sha256=sha256(parts).hexdigest(),
                           files={name: sha256(raw).hexdigest() for name, raw in captures.items()})
        expected = (removal.DATA / 'executed-power-analysis.py').read_bytes()
        self.assertEqual(sha256(expected).hexdigest(), declaration['power_analysis_sha256'])

        def inputs():
            return {'circuit.json': circuit, 'parts.json': parts,
                    'power-reference/engineering-basis.json': json_bytes(dict(sources={})),
                    'engineering-calculations.json': json_bytes(dict(sources={})),
                    'OPERATING.md': b'Synthetic admission inputs.\n'}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, capture = root / 'fixture', root / 'capture'
            fixture.mkdir()
            (fixture / 'removal-evidence.json').write_bytes(json_bytes(declaration))
            for name in ('removal_replay.py', 'power_adapter.py', 'executed-power-analysis.py'):
                shutil.copyfile(removal.DATA / name, fixture / name)
            for name, raw in captures.items():
                path = capture / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            with patch.object(removal, 'DATA', fixture):
                attached = inputs()
                removal.attach(attached, capture)
                self.assertEqual(attached[removal.AT + 'power_analysis.py'], expected)
                manifest = json.loads(attached[removal.AT + 'evidence-manifest.json'])
                self.assertFalse(manifest['accepted'])
                self.assertEqual(manifest['file_release'], 'not_assessed')
                (fixture / 'executed-power-analysis.py').write_bytes(expected + b'\n')
                with self.assertRaisesRegex(ValueError, 'reader differs from the executed investigation'):
                    removal.attach(inputs(), capture)
