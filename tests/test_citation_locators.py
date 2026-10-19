"""Locator formats and bounded correction of native-text citations (AGENTEE-204)."""
import copy
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee.findings import citation_format, validate_citation
from agent_ee.reviewers import build_packet, initial_reviews
from agent_ee.candidate import capture
from agent_ee.storage import digest, read_json, write_json
from tests import test_findings as fixture


class CitationLocatorTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review
    _finding = fixture.FindingsTests._finding

    def test_byte_formats_and_kind_diagnostics(self):
        exp = self.run_dir / 'review-work/circuit/experiments'
        exp.mkdir(parents=True)
        cases = [
            ('native.kicad_sch', b'(kicad_sch)\n', 'UTF-8 text', ['lines']),
            ('log.txt', 'voltage: \u03bcV\n'.encode(), 'UTF-8 text', ['lines']),
            ('data.txt', b'{"value": 1}\n', 'JSON', ['pointer', 'lines']),
            ('netlist.xml', b'<export/>\n', 'XML', ['xpath', 'lines']),
            ('invalid.json', b'not JSON\n', 'UTF-8 text', ['lines']),
            ('pdf', b'%' + b'PDF-1.4\n', 'binary', []),
            ('binary', b'\x00\xff', 'binary', []),
        ]
        for name, data, format_name, kinds in cases:
            with self.subTest(name=name):
                self.assertEqual(citation_format(data), {'format': format_name, 'locator_kinds': kinds})
                path = 'review-work/circuit/experiments/' + name
                (self.run_dir / path).write_bytes(data)
                with self.assertRaises(ValueError) as error:
                    validate_citation({'path': path, 'locator': 'subject', 'pointer': '/missing'}, self.run_dir)
                self.assertIn(path, str(error.exception))
                self.assertIn('kind(s) used pointer', str(error.exception))
                self.assertIn(format_name + ' requires locator kind(s)', str(error.exception))

    def test_inventory_includes_files_beyond_excerpts_and_excludes_peers(self):
        write_json(self.run_dir / 'review-work/physical/experiments/private.json', {'peer': 'private'})
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        inventory = {entry['path']: entry for entry in packet['citation_files']}
        self.assertEqual(inventory['inputs/project/rc.kicad_sch']['locator_kinds'], ['lines'])
        self.assertEqual(inventory['checks/erc.json']['locator_kinds'], ['pointer', 'lines'])
        self.assertEqual(inventory['work/rc_transient/spice.log']['format'], 'UTF-8 text')
        self.assertFalse(any(path.startswith(('review-work/', 'reviews/')) for path in inventory))
        self.scope_data['packet_max_bytes'] = 0
        write_json(self.scope_path, self.scope_data)
        limited_run = self.root / 'limited'
        limited_run.mkdir()
        capture(self.scope_path, limited_run)
        shutil.copytree(self.run_dir / 'checks', limited_run / 'checks')
        shutil.copytree(self.run_dir / 'work/rc_transient', limited_run / 'work/rc_transient')
        limited = build_packet(limited_run, [self.erc_check, self.rc_check], self.run_id)
        self.assertTrue(all(a['bytes_included'] == 0 for a in limited['artifacts']))
        self.assertEqual(limited['citation_files'], packet['citation_files'])

    def test_unknown_xml_encoding_remains_text_and_requests_lines(self):
        path = 'work/rc_transient/unknown-encoding.xml'
        data = b'<?xml version="1.0" encoding="unknown-encoding"?><export/>\n'
        (self.run_dir / path).write_bytes(data)
        self.assertEqual(citation_format(data), {'format': 'UTF-8 text', 'locator_kinds': ['lines']})
        citation = {'path': path, 'locator': 'export', 'xpath': '.'}
        with self.assertRaisesRegex(ValueError, 'UTF-8 text requires locator kind.s. lines'):
            validate_citation(citation, self.run_dir, require_structured=True)
        citation.pop('xpath')
        citation['lines'] = [1, 1]
        self.assertEqual(validate_citation(citation, self.run_dir, require_structured=True), citation)

    def test_inventory_omits_symlink_targets_with_explicit_limit(self):
        path = 'work/rc_transient/linked.txt'
        (self.run_dir / path).symlink_to(self.run_dir / 'inputs/project/rc.kicad_sch')
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        self.assertNotIn(path, [entry['path'] for entry in packet['citation_files']])
        self.assertTrue(any(path in limit and 'symlink' in limit for limit in packet['packet_limits']))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            validate_citation({'path': path, 'locator': 'linked source', 'lines': [1, 1]}, self.run_dir)

    def run_native_correction(self, correction):
        packet = {'candidate_id': self.candidate_id, 'run_id': self.run_id,
                  'packet_sha256': self.packet_sha256}
        counts = {}
        original = {}
        original_hash = digest(self.run_dir / 'inputs/project/rc.kicad_sch')
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            counts[role] = counts.get(role, 0) + 1
            self.assertNotIn('initial_reviews', prompt)
            self.assertIn('OMIT unused fields', prompt)
            if counts[role] == 1:
                citation = {'path': 'inputs/project/rc.kicad_sch', 'locator': 'R1 native symbol',
                            'pointer': '', 'xpath': './unused'}
            else:
                self.assertIn('OUTPUT VALIDATION ERROR', prompt)
                self.assertIn('inputs/project/rc.kicad_sch', prompt)
                self.assertIn('kind(s) used pointer, xpath', prompt)
                self.assertIn('UTF-8 text requires locator kind(s) lines', prompt)
                self.assertIn('every affected citation', prompt)
                citation = {'path': 'inputs/project/rc.kicad_sch', 'locator': 'R1 native symbol', **correction}
            value = {**self._review(role=role, findings=[self._finding(evidence=[citation])]),
                     'schema': 'agent-ee.review/2'}
            original.setdefault(role, copy.deepcopy(value))
            return {'execution': 'ok'}, value
        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30,
                                              launch_context={'max_schema_resubmissions': 1})
        self.assertEqual(counts, {'circuit': 2, 'physical': 2})
        self.assertEqual(digest(self.run_dir / 'inputs/project/rc.kicad_sch'), original_hash)
        for record in records:
            self.assertEqual(record['resubmissions_used'], 1)
            first = self.run_dir / record['attempts'][0]['path']
            self.assertEqual(read_json(first / 'response.json'), original[record['role']])
            self.assertEqual(read_json(first / 'execution.json')['execution'], 'malformed')
        return reviews, records

    def test_native_pointer_and_xpath_corrected_to_lines_after_resubmission(self):
        reviews, records = self.run_native_correction({'lines': [1, 1]})
        self.assertEqual(len(reviews), 2)
        self.assertEqual([r['execution'] for r in records], ['ok', 'ok'])
        for review in reviews:
            self.assertEqual(review['findings'][0]['id'], 'r1-tau-too-slow')
            self.assertEqual(review['findings'][0]['evidence'][0]['lines'], [1, 1])

    def test_adding_lines_does_not_hide_bad_placeholder(self):
        reviews, records = self.run_native_correction({'lines': [1, 1], 'pointer': ''})
        self.assertEqual(reviews, [])
        self.assertEqual([r['execution'] for r in records], ['malformed', 'malformed'])

    def test_out_of_range_correction_stays_refused(self):
        reviews, records = self.run_native_correction({'lines': [1, 999999]})
        self.assertEqual(reviews, [])
        self.assertTrue(all('out of range' in r['error'] for r in records))
