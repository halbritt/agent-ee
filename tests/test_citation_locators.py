"""Locator formats and bounded correction of native-text citations (AGENTEE-204)."""
import copy
import re
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee.findings import citation_format, validate_citation
from agent_ee.reviewers import build_packet, initial_reviews
from agent_ee.candidate import capture
from agent_ee.storage import digest, read_json, write_json
from tests import test_findings as fixture


def ecma_search(pattern, text):
    """Emulate only ECMAScript's final-$ behavior in these flag-free patterns.

    Python $ also matches before a final LF; ECMAScript $ without m does not.
    This is a test adapter for that dialect difference, not a provider probe.
    """
    if pattern.endswith('$'):
        backslashes = len(pattern[:-1]) - len(pattern[:-1].rstrip('\\'))
        if backslashes % 2 == 0:
            pattern = pattern[:-1] + r'\Z'
    return re.search(pattern, text)


class CitationLocatorTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review
    _finding = fixture.FindingsTests._finding

    def test_outgoing_codex_schema_binds_locator_kinds_to_actual_bytes(self):
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import EVIDENCE_SCHEMA
        (self.source / 'spec.md').write_text('Bench source declaration.\n')
        self.scope_data['inputs'].append(dict(path='source/spec.md', at='spec.md'))
        write_json(self.scope_path, self.scope_data)
        previous = self.run_dir
        self.run_dir = self.root / 'schema-bound'
        self.run_dir.mkdir()
        capture(self.scope_path, self.run_dir)
        shutil.copytree(previous / 'checks', self.run_dir / 'checks')
        shutil.copytree(previous / 'work', self.run_dir / 'work')
        (self.run_dir / 'work/rc_transient/data.txt').write_text('{"v": 1}\n')
        (self.run_dir / 'work/rc_transient/export.xml').write_text('<export/>\n')
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        retained = read_json(Path(__file__).parent / 'fixtures/reviewer-citations/r2-response.json')
        bad = retained['findings'][0]['evidence'][0]

        definitions = {}
        def accepts_path(path, node):
            if not node.get('minLength', 0) <= len(path) <= node.get('maxLength', len(path)):
                return False
            if '$ref' in node:
                return accepts_path(path, definitions[node['$ref'].removeprefix('#/$defs/')])
            if 'anyOf' in node:
                return any(accepts_path(path, branch) for branch in node['anyOf'])
            if 'enum' in node:
                return path in node['enum']
            if 'const' in node:
                return path == node['const']
            if 'pattern' in node:
                return ecma_search(node['pattern'], path) is not None
            return True  # The old unrestricted path shape, used only by the red control.

        def fits(citation, schema):
            for branch in schema['items']['anyOf']:
                if set(citation) != set(branch['required']):
                    continue
                path = branch['properties']['path']
                if not accepts_path(citation['path'], path):
                    continue
                return True
            return False

        self.assertTrue(fits(bad, EVIDENCE_SCHEMA))  # The retained R2 failure shape.
        seen = {}
        counts = {}
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            nonlocal definitions
            strict = strict_schema(schema)
            definitions = strict['$defs']
            outgoing = strict['properties']['findings']['items']['properties']['evidence']
            if role in seen:
                self.assertEqual(seen[role], outgoing)
            seen[role] = outgoing
            counts[role] = counts.get(role, 0) + 1
            self.assertFalse(fits(bad, outgoing))
            good = {'path': 'inputs/spec.md', 'locator': 'Bench declaration', 'lines': [1, 1]}
            self.assertTrue(fits(good, outgoing))
            self.assertEqual(validate_citation(good, run, require_structured=True), good)
            for path, field, value in [('work/rc_transient/data.txt', 'pointer', '/v'),
                                        ('work/rc_transient/export.xml', 'xpath', '.')]:
                cite = dict(path=path, locator='Actual document', **{field: value})
                self.assertTrue(fits(cite, outgoing))
                validate_citation(cite, run, require_structured=True)
                self.assertTrue(fits({**cite, 'lines': [1, 1]}, outgoing))
            for path in [f'review-work/{role}/experiments/notes.txt',
                         f'review-work/{role}/experiments/nested/notes.txt']:
                self.assertTrue(fits(dict(path=path, locator='Scratch evidence', lines=[1, 1]), outgoing))
                self.assertFalse(fits(dict(path=path, locator='Scratch evidence', pointer='/v'), outgoing))
            for path in ['inputs/unknown.txt', 'review-work/peer/experiments/notes.txt',
                         f'review-work/{role}/experiments/../../inputs/spec.md',
                         f'review-work/{role}/experiments/./notes.txt', '/inputs/spec.md',
                         f'review-work/{role}/experiments/notes.txt\n']:
                self.assertFalse(fits(dict(path=path, locator='Escape', lines=[1, 1]), outgoing))
            response = {**copy.deepcopy(retained), **{k: packet[k] for k in
                        ('candidate_id', 'run_id', 'packet_sha256')}, 'role': role}
            response['findings'][0]['evidence'] = [bad if counts[role] == 1 else good]
            return {'execution': 'ok'}, response
        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30,
                                              launch_context={'max_schema_resubmissions': 1})
        self.assertEqual(len(reviews), 2, records)
        self.assertEqual(set(seen), {'circuit', 'physical'})
        self.assertEqual(counts, {'circuit': 2, 'physical': 2})
        for record in records:
            first = read_json(self.run_dir / record['attempts'][0]['path'] / 'response.json')
            self.assertEqual(first['findings'], retained['findings'])
            self.assertEqual([a['execution'] for a in record['attempts']], ['malformed', 'ok'])

    def test_large_inventory_is_shared_once_in_complete_outgoing_schemas(self):
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import adjudicator_call
        expected = set()
        for number in range(900):
            path = 'work/rc_transient/' + 'retained-source-' * 3 + str(number) + '.txt'
            data = '{"v": 1}\n' if number < 480 else '<export/>\n' if number < 500 else 'Native text\n'
            (self.run_dir / path).write_text(data)
            expected.add(path)
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        seen = set()
        def enum_nodes(node):
            if isinstance(node, dict):
                if 'enum' in node:
                    yield node['enum']
                for child in node.values():
                    yield from enum_nodes(child)
            elif isinstance(node, list):
                for child in node:
                    yield from enum_nodes(child)
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            outgoing = strict_schema(schema)
            groups = list(enum_nodes(outgoing))
            self.assertLessEqual(sum(map(len, groups)), 1000)
            paths = [path for value in outgoing['$defs'].values() for path in value['enum']]
            self.assertEqual(len(paths), len(set(paths)))
            self.assertTrue(expected <= set(paths))
            self.assertTrue(all(len(value['enum']) <= 250 for value in outgoing['$defs'].values()))
            key = 'decisions' if role == 'adjudicator' else 'findings'
            evidence = outgoing['properties'][key]['items']['properties']['evidence']
            for branch in evidence['items']['anyOf']:
                for ref in branch['properties']['path'].get('anyOf', []):
                    self.assertIn(ref['$ref'].removeprefix('#/$defs/'), outgoing['$defs'])
            seen.add(role)
            if role == 'adjudicator':
                return {'execution': 'ok'}, dict(schema='agent-ee.decisions/2', candidate_id=packet['candidate_id'],
                                                run_id=packet['run_id'], decisions=[])
            return {'execution': 'ok'}, {**self._review(role=role, findings=[]), 'schema': 'agent-ee.review/2',
                **{key: packet[key] for key in ('candidate_id', 'run_id', 'packet_sha256')}}
        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
            execution, decisions = adjudicator_call(self.run_dir, packet, [], reviews, time.monotonic() + 30)
        self.assertEqual(len(reviews), 2, records)
        self.assertEqual(execution['execution'], 'ok')
        self.assertEqual(seen, {'circuit', 'physical', 'adjudicator'})

    def test_over_enum_limit_keeps_exact_literal_paths_and_format_binding(self):
        from agent_ee.reviewers import REVIEW_SCHEMA, bind_evidence_schema
        from agent_ee.harnesses import strict_schema
        inventory = [dict(path=f'inputs/s{i}.txt', locator_kinds=['lines']) for i in range(1100)]
        inventory.append(dict(path='inputs/document.txt', locator_kinds=['pointer', 'lines']))
        schema = copy.deepcopy(REVIEW_SCHEMA)
        bind_evidence_schema(schema, 'findings', self.run_dir, dict(citation_files=inventory), 'circuit')
        outgoing = strict_schema(schema)
        branches = outgoing['properties']['findings']['items']['properties']['evidence']['items']['anyOf']
        def allowed(fields):
            paths = set()
            for branch in branches:
                if set(branch['required']) - {'path', 'locator'} != fields:
                    continue
                for ref in branch['properties']['path'].get('anyOf', []):
                    patterns = outgoing['$defs'][ref['$ref'].removeprefix('#/$defs/')]['anyOf']
                    for row in inventory:
                        path = row['path']
                        if any(p['minLength'] <= len(path) <= p['maxLength'] and ecma_search(p['pattern'], path)
                               for p in patterns):
                            paths.add(path)
            return paths
        self.assertEqual(allowed({'lines'}), {row['path'] for row in inventory})
        self.assertIn('inputs/s0.txt', allowed({'lines'}))
        self.assertNotIn('inputs/s0.txt', allowed({'pointer'}))
        self.assertNotIn('inputs/unknown.txt', allowed({'lines'}))
        self.assertEqual(allowed({'pointer'}), {'inputs/document.txt'})
        self.assertEqual(allowed({'pointer', 'lines'}), {'inputs/document.txt'})

    def test_compact_literal_regex_escapes_and_rejects_newline_and_traversal(self):
        from agent_ee.reviewers import literal_path_set
        paths = ['inputs/a[0].txt', 'inputs/a(1)+$.json', 'inputs/a?.txt', 'inputs/a|b^.txt',
                 'inputs/a{1,2}.txt', 'inputs/a\\b.txt', 'inputs/µ space.txt', 'inputs/a.txt']
        shape = literal_path_set(paths)
        def accepts(path):
            return any(p['minLength'] <= len(path) <= p['maxLength'] and ecma_search(p['pattern'], path)
                       for p in shape['anyOf'])
        for path in paths:
            self.assertTrue(accepts(path), path)
            for suffix in ('\n', '\r', '\r\n', '\u2028', '\u2029', '/../../escape', '.unknown'):
                self.assertFalse(accepts(path + suffix), path + repr(suffix))
        for unknown in ('inputs/a0.txt', 'inputs/a11.json', 'inputs/ab.txt', 'inputs/a.txt/../a.txt',
                        '/inputs/a.txt', 'inputs/unknown.txt', 'inputs/./a.txt', 'inputs/a.txt\x00'):
            self.assertFalse(accepts(unknown), unknown)

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
