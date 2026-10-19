"""Markdown requirements spec to agent-ee.spec-record/1 capture (AGENTEE-158)."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_spec_record.

from agent_ee import generate as g
from agent_ee import spec_record
from agent_ee.spec_record import SpecRefused, capture, main, verify
from agent_ee.storage import json_bytes

ROOT = Path(__file__).resolve().parents[1]
POD_SPEC = ROOT / 'fixtures/telemetry-pod/spec.md'
POD_RECORD = ROOT / 'fixtures/telemetry-pod/spec-record.json'
RC_SPEC = ROOT / 'fixtures/rung1/spec.md'
RC_GOLDEN = ROOT / 'fixtures/rung1/parse-spec.golden.json'
POD_CODES = {'Telemetry pod requirements': 'DOC', 'Purpose': 'PU', 'Function': 'FN', 'Interfaces': 'IF',
             'Component decisions': 'CD', 'Mechanical and environment': 'ME', 'Acceptance criteria': 'AC'}
SMALL = '''# Board

## Interfaces

| Interface | Requirement |
| --- | --- |
| CAN1 | 500 kbit/s |
| Power in | 9 V to 16 V |

## Checks

- First bullet
- Second bullet

## Acceptance

1. Numbered item
2. Second item
'''
SMALL_CODES = {'Interfaces': 'IF', 'Checks': 'CK', 'Acceptance': 'AC'}


def pod_argv(spec, out):
    argv = ['capture', str(spec), '--out', str(out), '--prefix', 'POD']
    for heading, code in POD_CODES.items():
        argv += ['--code', f'{heading}={code}']
    return argv


def run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


class PodSpecTests(unittest.TestCase):
    def setUp(self):
        self.raw = POD_SPEC.read_bytes()
        self.record = capture(self.raw, 'POD', POD_CODES)

    def test_pod_spec_tables(self):
        self.assertEqual(self.record['spec'], {'name': 'spec.md', 'bytes': len(self.raw),
                                               'sha256': hashlib.sha256(self.raw).hexdigest()})
        per_section = {}
        for requirement in self.record['requirements']:
            key = (requirement['section'], requirement['kind'])
            per_section[key] = per_section.get(key, 0) + 1
        self.assertEqual(per_section, {('Function', 'table_row'): 7, ('Interfaces', 'table_row'): 8,
                                       ('Component decisions', 'table_row'): 8,
                                       ('Mechanical and environment', 'bullet'): 6,
                                       ('Acceptance criteria', 'acceptance'): 9})
        ids = [r['id'] for r in self.record['requirements']]
        for rid in ('POD-IF-CAN1', 'POD-CD-IMU', 'POD-ME-1', 'POD-AC-4', 'POD-FN-FRONT-FORK-TRAVEL'):
            self.assertIn(rid, ids)
        self.assertEqual(len(ids), len(set(ids)))
        for requirement in self.record['requirements']:
            locator = requirement['locator']
            sliced = self.raw[locator['byte_start']:locator['byte_end']]
            self.assertEqual(sliced.decode(), requirement['text'], requirement['id'])
            self.assertEqual(hashlib.sha256(sliced).hexdigest(), requirement['text_sha256'])
            self.assertEqual(locator['heading_path'][-1], requirement['section'])
            for header, (start, end) in locator['cells'].items():
                self.assertEqual(self.raw[start:end].decode(), requirement['cells'][header])
                self.assertTrue(locator['byte_start'] <= start <= end <= locator['byte_end'])
        can = next(r for r in self.record['requirements'] if r['id'] == 'POD-IF-CAN1')
        self.assertEqual(can['cells']['Interface'], 'CAN1')
        self.assertTrue(can['cells']['Requirement'].startswith('ISO 11898-2, 500 kbit/s'))
        self.assertEqual((can['locator']['table'], can['locator']['row']), (1, 1))
        harness = next(r for r in self.record['requirements'] if r['id'] == 'POD-IF-HARNESS-CONNECTOR')
        self.assertIn(r'CAN\_H', harness['cells']['Requirement'])  # verbatim, escapes kept
        imu = next(r for r in self.record['requirements'] if r['id'] == 'POD-CD-IMU')
        self.assertEqual(sorted(imu['cells']), ['Decision', 'Function', 'Not chosen', 'Why'])
        self.assertEqual(imu['cells']['Decision'], 'LSM6DSO32, SPI')
        ac4 = next(r for r in self.record['requirements'] if r['id'] == 'POD-AC-4')
        self.assertTrue(ac4['text'].startswith('The kicad-happy schematic, PCB, cross-analysis and EMC analyzers'))
        self.assertEqual((ac4['locator']['list'], ac4['locator']['item'], ac4['cells']), (1, 4, {}))
        self.assertEqual([t['headers'] for t in self.record['tables']][2],
                         ['Function', 'Decision', 'Why', 'Not chosen'])

    def test_prose_and_coverage(self):
        relay = next(p for p in self.record['prose'] if p['id'] == 'POD-FN-P2')
        self.assertTrue(relay['text'].startswith('The pod also relays every CAN1 frame'))
        for block in self.record['prose']:
            locator = block['locator']
            self.assertEqual(self.raw[locator['byte_start']:locator['byte_end']].decode(), block['text'])
        covered = bytearray(len(self.raw))
        spans = [(h['byte_start'], h['byte_end']) for h in self.record['headings']]
        spans += [(t['byte_start'], t['byte_end']) for t in self.record['tables'] + self.record['lists']]
        spans += [(p['locator']['byte_start'], p['locator']['byte_end']) for p in self.record['prose']]
        for start, end in spans:
            covered[start:end] = b'\x01' * (end - start)
        missed = [i for i, byte in enumerate(self.raw) if not covered[i] and byte not in b' \t\n']
        self.assertEqual(missed, [])  # no spec byte is dropped silently

    def test_committed_record_is_the_capture(self):
        self.assertEqual(json_bytes(self.record), POD_RECORD.read_bytes())
        self.assertEqual(verify(POD_RECORD.read_bytes(), self.raw)['counts'],
                         {'acceptance': 9, 'bullet': 6, 'prose': 10, 'requirements': 38, 'table_row': 23})

    def test_capture_twice_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for name in ('a.json', 'b.json'):
                code, stdout, _ = run(pod_argv(POD_SPEC, Path(tmp) / name))
                self.assertEqual(code, 0, stdout)
                outputs.append((Path(tmp) / name).read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], POD_RECORD.read_bytes())
            receipt = json.loads((Path(tmp) / 'a.receipt.json').read_text())
            module = Path(spec_record.__file__).read_bytes()
            self.assertEqual(receipt['tool']['module_sha256'], hashlib.sha256(module).hexdigest())
            self.assertEqual(receipt['tool']['python'], platform.python_version())
            self.assertEqual(receipt['record'], {'bytes': len(outputs[0]),
                                                 'sha256': hashlib.sha256(outputs[0]).hexdigest()})
            self.assertEqual(receipt['spec']['sha256'], hashlib.sha256(self.raw).hexdigest())

    def test_byte_change_changes_hash(self):
        target = next(r for r in self.record['requirements'] if r['id'] == 'POD-IF-CAN1')
        start, _ = target['locator']['cells']['Requirement']
        at = start + self.raw[start:].index(b'500')
        changed = self.raw[:at] + b'6' + self.raw[at + 1:]  # 500 kbit/s -> 600 kbit/s, same length
        after = capture(changed, 'POD', POD_CODES)
        before_hashes = {r['id']: r['text_sha256'] for r in self.record['requirements']}
        after_hashes = {r['id']: r['text_sha256'] for r in after['requirements']}
        self.assertEqual(set(before_hashes), set(after_hashes))
        self.assertEqual([rid for rid in before_hashes if before_hashes[rid] != after_hashes[rid]], ['POD-IF-CAN1'])
        self.assertNotEqual(hashlib.sha256(json_bytes(after)).hexdigest(),
                            hashlib.sha256(json_bytes(self.record)).hexdigest())
        self.assertNotEqual(after['spec']['sha256'], self.record['spec']['sha256'])
        with self.assertRaises(ValueError):
            verify(POD_RECORD.read_bytes(), changed)

    def test_verify_refuses_an_edited_record(self):
        record = json.loads(POD_RECORD.read_text())
        record['requirements'][0]['cells']['Range'] = '±32 g minimum'
        with self.assertRaisesRegex(ValueError, 'byte-identical'):
            verify(json_bytes(record), self.raw)

    def test_crlf_spec_keeps_texts(self):
        crlf = capture(self.raw.replace(b'\n', b'\r\n'), 'POD', POD_CODES)
        self.assertEqual([(r['id'], r['text']) for r in crlf['requirements']],
                         [(r['id'], r['text']) for r in self.record['requirements']])


class RefusalTests(unittest.TestCase):
    def assert_cli_refused(self, text, byte):
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / 'spec.md'
            spec.write_text(text)
            out = Path(tmp) / 'out' / 'record.json'
            out.parent.mkdir()
            argv = ['capture', str(spec), '--out', str(out), '--prefix', 'P']
            for heading, code in SMALL_CODES.items():
                argv += ['--code', f'{heading}={code}']
            code, stdout, _ = run(argv)
            self.assertEqual(code, 1)
            self.assertEqual(list(out.parent.iterdir()), [])  # writes nothing
            findings = json.loads(stdout)['findings']
            self.assertIn(byte, [f['byte'] for f in findings])
            self.assertIn(f'byte {byte}', str(SpecRefused(findings)))
            return findings

    def test_small_spec_captures(self):
        record = capture(SMALL.encode(), 'P', SMALL_CODES)
        self.assertEqual([(r['id'], r['kind']) for r in record['requirements']],
                         [('P-IF-CAN1', 'table_row'), ('P-IF-POWER-IN', 'table_row'), ('P-CK-1', 'bullet'),
                          ('P-CK-2', 'bullet'), ('P-AC-1', 'acceptance'), ('P-AC-2', 'acceptance')])
        self.assertEqual(record['prose'], [])
        mixed = SMALL.replace('## Acceptance\n\n', '')  # bullet 1 and item 1 in one section collide
        with self.assertRaisesRegex(SpecRefused, 'P-CK-1 collides'):
            capture(mixed.encode(), 'P', {'Interfaces': 'IF', 'Checks': 'CK'})

    def test_ragged_table_refused(self):
        text = SMALL.replace('| Power in | 9 V to 16 V |', '| Power in | 9 V to 16 V | extra |')
        byte = text.encode().index(b'| Power in')
        findings = self.assert_cli_refused(text, byte)
        self.assertIn('ragged', findings[0]['message'])
        short = SMALL.replace('| --- | --- |', '| --- |')
        self.assert_cli_refused(short, short.encode().index(b'| --- |'))

    def test_table_without_leading_pipes_refused(self):
        for trailing in (False, True):
            for newline in ('\n', '\r\n'):
                for indent in ('', '   '):
                    with self.subTest(trailing=trailing, newline=repr(newline), indent=len(indent)):
                        lines = []
                        for line in SMALL.replace('# Board', '# Board µ').splitlines():
                            if line.startswith('|'):
                                line = indent + line[1:].lstrip()
                                if not trailing:
                                    line = line[:-1].rstrip()
                            lines.append(line)
                        text = newline.join(lines) + newline
                        byte = text.encode().index((indent + '--- | ---').encode())
                        findings = self.assert_cli_refused(text, byte)
                        self.assertIn('table delimiter', findings[0]['message'])

    def test_table_delimiter_without_leading_pipe_refused(self):
        for delimiter in ('--- | --- |', ':--- | ---:'):
            with self.subTest(delimiter=delimiter):
                text = SMALL.replace('| --- | --- |', delimiter)
                findings = self.assert_cli_refused(text, text.encode().index(delimiter.encode()))
                self.assertEqual(findings[0]['code'], 'table')
                self.assertIn('does not begin with |', findings[0]['message'])

    def test_table_without_leading_pipes_inside_list_refused(self):
        text = SMALL.replace('- Second bullet', '- Second bullet\n  Name | Requirement\n  --- | ---\n  CAN1 | bus')
        byte = text.encode().index(b'  --- | ---')
        findings = self.assert_cli_refused(text, byte)
        self.assertEqual(findings[0]['code'], 'list')
        self.assertIn('table inside a list', findings[0]['message'])

    def test_pipe_prose_and_single_column_table_capture(self):
        text = '# Interfaces\n\nCAN1 | CAN2 are bus names.\nKeep both.\n\n| Name |\n| :---: |\n| CAN1 |\n'
        record = capture(text.encode(), 'P', {'Interfaces': 'IF'})
        self.assertEqual([p['text'] for p in record['prose']], ['CAN1 | CAN2 are bus names.\nKeep both.'])
        self.assertEqual([r['cells'] for r in record['requirements']], [{'Name': 'CAN1'}])

    def test_record_hash_depends_on_spec_filename(self):
        first = capture(SMALL.encode(), 'P', SMALL_CODES, 'spec.md')
        renamed = capture(SMALL.encode(), 'P', SMALL_CODES, 'renamed.md')
        self.assertEqual(first['spec']['sha256'], renamed['spec']['sha256'])
        self.assertNotEqual(hashlib.sha256(json_bytes(first)).hexdigest(),
                            hashlib.sha256(json_bytes(renamed)).hexdigest())
        renamed['spec']['name'] = first['spec']['name']
        self.assertEqual(first, renamed)

    def test_ids_follow_first_cell_and_bullet_position(self):
        before = capture(SMALL.encode(), 'P', SMALL_CODES)
        text = SMALL.replace('| CAN1 |', '| CAN2 |').replace('- First bullet', '- Inserted bullet\n- First bullet')
        after = capture(text.encode(), 'P', SMALL_CODES)
        self.assertEqual([r['id'] for r in after['requirements'] if r['kind'] == 'table_row'],
                         ['P-IF-CAN2', 'P-IF-POWER-IN'])
        for record, expected in ((before, 'P-CK-2'), (after, 'P-CK-3')):
            self.assertEqual(next(r['id'] for r in record['requirements'] if r['text'] == 'Second bullet'),
                             expected)

    def test_duplicate_id_refused(self):
        text = SMALL.replace('| Power in |', '| can1 |')  # slug CAN1 collides with the first row
        findings = self.assert_cli_refused(text, text.encode().index(b'can1'))
        self.assertEqual(findings[0]['code'], 'duplicate_id')
        self.assertIn('P-IF-CAN1', findings[0]['message'])
        numbered = SMALL.replace('2. Second item', '1. Second item')  # never renumbered silently
        self.assert_cli_refused(numbered, numbered.encode().index(b'1. Second'))

    def test_duplicate_header_refused(self):
        text = SMALL.replace('| Interface | Requirement |', '| Interface | Interface |')
        self.assert_cli_refused(text, text.encode().index(b'Interface |', text.encode().index(b'| Interface') + 3))

    def test_escaped_pipe_not_closed_refused(self):
        text = SMALL.replace('| 9 V to 16 V |', '| 9 V to 16 V \\|')
        self.assert_cli_refused(text, text.encode().index(b'\\|'))
        text = SMALL.replace('| 500 kbit/s |', '| 500 kbit/s')
        line = text.encode().index(b'| CAN1')
        self.assert_cli_refused(text, line + len('| CAN1 | 500 kbit/s'))

    def test_escaped_pipe_inside_cell_is_kept(self):
        text = SMALL.replace('| 500 kbit/s |', '| 500 \\| 250 kbit/s |')
        record = capture(text.encode(), 'P', SMALL_CODES)
        self.assertEqual(record['requirements'][0]['cells']['Requirement'], '500 \\| 250 kbit/s')

    def test_unsupported_markdown_refused(self):
        cases = {
            'fence': (SMALL + '\n```\ncode\n```\n', b'```'),
            'quote': (SMALL + '\n> quoted\n', b'> quoted'),
            'setext': (SMALL.replace('## Checks\n\n', '## Checks\n\nTitle\n=====\n\n'), b'====='),
            'nested list': (SMALL.replace('- Second bullet', '- Second bullet\n  - nested'), b'  - nested'),
            'pipe line without delimiter': (SMALL + '\n| a | b |\n', b'| a | b |\n'),
            'list after paragraph': (SMALL.replace('- First bullet', 'Intro line\n- First bullet'),
                                     b'- First bullet'),
            'table after paragraph': (SMALL.replace('| Interface |', 'Intro\n| Interface |'), b'| Interface |'),
            'indented code': (SMALL + '\n    code\n', b'    code'),
            'break inside list': (SMALL.replace('- Second bullet', '- Second bullet\n* * *'), b'* * *'),
            'empty item': (SMALL.replace('- Second bullet', '-'), b'-\n'),
            'content before heading': ('Preface\n\n' + SMALL, b'Preface'),
            'carriage return': (SMALL.replace('First bullet', 'First\rbullet'), b'\rbullet'),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name), self.assertRaises(SpecRefused) as caught:
                capture(text.encode(), 'P', SMALL_CODES)
            self.assertIn(text.encode().index(needle), [f['byte'] for f in caught.exception.findings], name)

    def test_section_codes_are_explicit(self):
        with self.assertRaises(SpecRefused) as caught:
            capture(SMALL.encode(), 'P', {'Interfaces': 'IF', 'Acceptance': 'AC'})
        self.assertIn("section 'Checks' has content but no --code", str(caught.exception))
        with self.assertRaises(SpecRefused) as caught:
            capture(SMALL.encode(), 'P', {**SMALL_CODES, 'Interface': 'XX'})
        self.assertIn("'Interface' names no heading", str(caught.exception))
        with self.assertRaises(SpecRefused):
            capture((SMALL + '\n## Checks\n\nAgain.\n').encode(), 'P', SMALL_CODES)
        for prefix, codes in (('pod', SMALL_CODES), ('P', {**SMALL_CODES, 'Checks': 'IF'}),
                              ('P', {**SMALL_CODES, 'Interfaces': 'if'})):
            with self.subTest(prefix=prefix, codes=codes), self.assertRaises(ValueError):
                capture(SMALL.encode(), prefix, codes)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'record.json'
            out.write_text('keep')
            code, _, stderr = run(pod_argv(POD_SPEC, out))
            self.assertEqual(code, 2)
            self.assertIn('exists', stderr)
            self.assertEqual(out.read_text(), 'keep')
            self.assertFalse((Path(tmp) / 'record.receipt.json').exists())


class RungOneTests(unittest.TestCase):
    def test_rc_spec_unchanged(self):
        # The rung-1 RC spec keeps the existing regex path; its parameters are byte-identical to the
        # golden captured from main at ea8009e before spec_record existed.
        self.assertEqual(hashlib.sha256(RC_SPEC.read_bytes()).hexdigest(),
                         '1346de781cadffc4f3fe5da003f2dde193d8962f8d6591e3dca99721255e67a8')
        self.assertEqual(json_bytes(g.parse_spec(RC_SPEC.read_text())), RC_GOLDEN.read_bytes())
        self.assertNotIn('spec_record', Path(g.__file__).read_text())
        record = capture(RC_SPEC.read_bytes(), 'RC', {'Rung 1: RC low-pass board': 'RC'})
        self.assertEqual(record['requirements'], [])
        self.assertEqual([p['id'] for p in record['prose']], ['RC-RC-P1'])


if __name__ == '__main__':
    unittest.main()
