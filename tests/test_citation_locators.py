"""Locator formats and bounded correction of native-text citations (AGENTEE-204)."""
import copy
import json
import re
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee.findings import citation_format, validate_citation
from agent_ee.reviewers import build_packet, initial_reviews
from agent_ee.candidate import capture
from agent_ee.storage import digest, json_bytes, read_json, write_json
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


def accepts_path(path, shape, definitions):
    if '$ref' in shape:
        return accepts_path(path, definitions[shape['$ref'].removeprefix('#/$defs/')], definitions)
    if 'anyOf' in shape:
        return any(accepts_path(path, branch, definitions) for branch in shape['anyOf'])
    if 'allOf' in shape:
        return all(accepts_path(path, branch, definitions) for branch in shape['allOf'])
    return (shape.get('minLength', 0) <= len(path) <= shape.get('maxLength', len(path))
            and ('enum' not in shape or path in shape['enum'])
            and ('pattern' not in shape or ecma_search(shape['pattern'], path) is not None))


class CitationLocatorTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review
    _finding = fixture.FindingsTests._finding

    def locator_table(self, prompt):
        return json.loads(prompt.split('CITATION LOCATOR TABLE (JSON):\n', 1)[1].splitlines()[0])

    def test_provider_compaction_keeps_captured_locator_groups_disjoint(self):
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import REVIEW_SCHEMA, DECISIONS_SCHEMA, bind_evidence_schema, review_transport_schema
        inventory = [dict(path=f'inputs/source-{i}.kicad_pcb', locator_kinds=['lines']) for i in range(1100)]
        inventory += [dict(path='inputs/report.json', locator_kinds=['lines', 'pointer']),
                      dict(path='inputs/' + 'j' * 50 + '.json', locator_kinds=['lines', 'pointer']),
                      dict(path='inputs/netlist.xml', locator_kinds=['lines', 'xpath']),
                      dict(path='inputs/' + 'x' * 50 + '.xml', locator_kinds=['lines', 'xpath']),
                      dict(path='inputs/odd.kicad_pcb', locator_kinds=['lines', 'pointer']),
                      dict(path='inputs/other/report.json', locator_kinds=['lines']),
                      dict(path='inputs/.json', locator_kinds=['lines'])]
        for role, collection, template in [('circuit', 'findings', REVIEW_SCHEMA),
                                            ('adjudicator', 'decisions', DECISIONS_SCHEMA)]:
            with self.subTest(role=role):
                local = copy.deepcopy(template)
                bind_evidence_schema(local, collection, self.run_dir, dict(citation_files=inventory), role)
                wire = json.loads(json_bytes(strict_schema(review_transport_schema(local))))
                items = wire['properties'][collection]['items']
                variants = items.get('anyOf', [items])
                for variant in variants:
                    branches = variant['properties']['evidence']['items']['anyOf']
                    field_sets = {frozenset(branch['required']) - {'path', 'locator'} for branch in branches}
                    for row in inventory:
                        for fields in field_sets:
                            matched = any(accepts_path(row['path'], branch['properties']['path'], wire['$defs'])
                                for branch in branches if set(branch['required']) - {'path', 'locator'} == fields)
                            self.assertEqual(matched, fields <= set(row['locator_kinds']), (row, fields))

    def adjudicator_repair(self, correct):
        from agent_ee.reviewers import adjudicator_call
        for number in range(1005):
            (self.run_dir / f'work/rc_transient/observation-{number:04}.txt').write_text('Observation\n')
        (self.run_dir / 'work/rc_transient/odd.txt').write_text('{"value":1}\n')
        (self.run_dir / 'work/rc_transient/export.txt').write_text('<export/>\n')
        (self.run_dir / 'work/rc_transient/image.txt').write_bytes(b'\x00\xff')
        (self.run_dir / 'work/rc_transient/record').write_text('{"value":3}\n')
        peer = self.run_dir / 'review-work/circuit/experiments/memo.txt'
        peer.parent.mkdir(parents=True)
        peer.write_text('{"value":2}\n')
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        citation = dict(path='inputs/project/rc.kicad_pcb', locator='Native board source', pointer='')
        response = dict(schema='agent-ee.decisions/2', candidate_id=packet['candidate_id'],
                        run_id=packet['run_id'], decisions=[dict(finding_id='circuit/f1',
                        disposition='accepted', reason='Native source observation', evidence=[citation])])
        calls = []
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            calls.append(prompt)
            branches = schema['properties']['decisions']['items']['properties']['evidence']['items']['anyOf']
            self.assertFalse(any(accepts_path(citation['path'], branch['properties']['path'], schema['$defs'])
                for branch in branches if 'pointer' in branch['required'] or 'xpath' in branch['required']))
            table = self.locator_table(prompt)
            self.assertEqual(table['suffixes']['.kicad_pcb'], ['lines'])
            self.assertEqual(table['suffixes']['.txt'], ['lines'])
            self.assertEqual(table['path_overrides']['work/rc_transient/odd.txt'], ['lines', 'pointer'])
            self.assertEqual(table['path_overrides']['work/rc_transient/export.txt'], ['lines', 'xpath'])
            self.assertEqual(table['path_overrides']['work/rc_transient/image.txt'], [])
            self.assertEqual(table['suffixes'][''], ['lines', 'pointer'])
            self.assertEqual(table['path_overrides'][peer.relative_to(run).as_posix()], ['lines', 'pointer'])
            self.assertIn('New own experiments require lines only', prompt)
            value = copy.deepcopy(response)
            if len(calls) == 2:
                self.assertTrue(prompt.startswith('OUTPUT REPAIR TURN 1 OF 1'))
                self.assertLess(prompt.index('OUTPUT VALIDATION ERROR:'), prompt.index('ORIGINAL REVIEW CONTEXT:'))
                self.assertIn(read_json(run / 'review-attempts/adjudicator/0/execution.json')['error'], prompt)
                self.assertIn('locator kind(s) used pointer', prompt)
                self.assertIn('requires locator kind(s) lines', prompt)
                self.assertIn('every affected citation', prompt)
                if correct:
                    value['decisions'][0]['evidence'][0] = dict(path=citation['path'], locator=citation['locator'],
                                                             lines=[1, 9999 if correct == 'out-of-range' else 1])
            return {'execution': 'ok'}, value
        with patch('agent_ee.preflight.guard', return_value=({'result': 'ok'}, {}, '/usr/bin/true')), \
             patch('agent_ee.reviewers.dispatch', side_effect=transport):
            record, accepted = adjudicator_call(self.run_dir, packet, [self._finding('circuit/f1')], [],
                                               time.monotonic() + 30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(record['resubmissions_used'], 1)
        first = self.run_dir / record['attempts'][0]['path']
        self.assertEqual(read_json(first / 'response.json'), response)
        self.assertEqual(read_json(first / 'execution.json')['execution'], 'malformed')
        return record, accepted

    def test_adjudicator_wrong_locator_gets_one_repair_and_corrected_response_is_accepted(self):
        record, accepted = self.adjudicator_repair(correct=True)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(accepted['decisions'][0]['evidence'][0]['lines'], [1, 1])
        self.assertNotIn('resubmission_blocked', record)

    def test_adjudicator_second_wrong_locator_stops_with_validation_reason(self):
        record, accepted = self.adjudicator_repair(correct=False)
        self.assertIsNone(accepted)
        self.assertEqual(record['execution'], 'malformed')
        self.assertIn('requires locator kind(s) lines', record['error'])
        self.assertEqual(record['resubmission_blocked'], 'output validation failed after 1 repair turn')
        self.assertEqual([row['execution'] for row in record['attempts']], ['malformed', 'malformed'])

    def test_adjudicator_repair_still_rejects_unresolved_line_ranges(self):
        record, accepted = self.adjudicator_repair(correct='out-of-range')
        self.assertIsNone(accepted)
        self.assertEqual(record['execution'], 'malformed')
        self.assertIn('out of range', record['error'])
        self.assertEqual(record['resubmission_blocked'], 'output validation failed after 1 repair turn')

    def test_initial_reviewers_get_locator_policy_and_the_same_default_repair(self):
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        calls = {}
        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            calls[role] = calls.get(role, 0) + 1
            self.assertEqual(self.locator_table(prompt)['suffixes']['.kicad_sch'], ['lines'])
            citation = dict(path='inputs/project/rc.kicad_sch', locator='Native schematic source')
            if calls[role] == 1:
                citation['pointer'] = ''
            else:
                citation['lines'] = [1, 1]
            if calls[role] == 2:
                self.assertTrue(prompt.startswith('OUTPUT REPAIR TURN 1 OF 1'))
            response = dict(self._review(role, findings=[self._finding(evidence=[citation])]),
                            schema='agent-ee.review/2', **{k: packet[k] for k in ('candidate_id', 'run_id', 'packet_sha256')})
            return {'execution': 'ok'}, response
        with patch('agent_ee.preflight.guard', return_value=({'result': 'ok'}, {}, '/usr/bin/true')), \
             patch('agent_ee.reviewers.dispatch', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
        self.assertEqual(calls, {'circuit': 2, 'physical': 2})
        self.assertEqual(len(reviews), 2, records)
        self.assertTrue(all(row['resubmissions_used'] == 1 for row in records))

    def test_compact_provider_paths_keep_exact_local_membership_and_locator_rules(self):
        from agent_ee.reviewers import (REVIEW_SCHEMA, bind_evidence_schema,
            citation_transport_schema, validate_evidence_paths)
        inventory=[dict(path=f'inputs/s{i}.txt',locator_kinds=['lines']) for i in range(1100)]
        inventory.append(dict(path='inputs/document.json',locator_kinds=['lines','pointer']))
        schema=copy.deepcopy(REVIEW_SCHEMA)
        bind_evidence_schema(schema,'findings',self.run_dir,dict(citation_files=inventory),'circuit')
        original=copy.deepcopy(schema);wire=citation_transport_schema(schema)
        self.assertEqual(schema,original)
        self.assertEqual({k:v for k,v in wire.items() if k!='$defs'},
                         {k:v for k,v in schema.items() if k!='$defs'})
        for definition in wire['$defs'].values():
            self.assertTrue(all(len(shape['pattern']) <= 256 for shape in definition['anyOf']))
        def admit(path,**fields):
            return validate_evidence_paths(dict(findings=[dict(evidence=[dict(path=path,locator='actual source',**fields)])]),schema)
        for row in inventory:
            admit(row['path'],lines=[1,1])
        admit('inputs/document.json',pointer='/value')
        admit('review-work/circuit/experiments/new.json',lines=[1,1])
        for path,fields in [
            ('inputs/unknown.txt',dict(lines=[1,1])),
            ('inputs/s0.txt',dict(pointer='/value')),
            ('review-work/physical/experiments/existing.json',dict(lines=[1,1])),
            ('review-work/circuit/experiments/new.json',dict(pointer='/value')),
            ('review-work/circuit/experiments/../foreign.txt',dict(lines=[1,1])),
            ('inputs/s0.txt\n',dict(lines=[1,1])),
            ('inputs/document.json',dict(xpath='.'))]:
            with self.subTest(path=path,fields=fields),self.assertRaises(ValueError):admit(path,**fields)

    def test_structured_call_enforces_frozen_policy_before_semantic_acceptance(self):
        from agent_ee.reviewers import REVIEW_SCHEMA,bind_evidence_schema
        from agent_ee.review_run import structured_call
        schema=copy.deepcopy(REVIEW_SCHEMA)
        bind_evidence_schema(schema,'findings',self.run_dir,
            dict(citation_files=[dict(path='inputs/known.txt',locator_kinds=['lines'])]),'circuit')
        for index,path in enumerate(('review-work/physical/experiments/peer.txt','work/late-check/result.txt')):
            peer=self.run_dir/path
            peer.parent.mkdir(parents=True);peer.write_text('Existing uncaptured observation\n')
            citation=dict(path=path,locator='existing but not captured',lines=[1,1])
            # Semantic resolution alone accepts the file; producer policy must not.
            validate_citation(citation,self.run_dir,require_structured=True)
            value=dict(findings=[dict(evidence=[citation])])
            with patch('agent_ee.findings.validate_review') as semantic:
                record,accepted=structured_call(self.run_dir,'control-'+str(index),'CPU_MOCK_ONLY',schema,
                    lambda _:({'execution':'ok'},value),semantic,limit=0)
            self.assertEqual(record['execution'],'malformed');self.assertIsNone(accepted)
            semantic.assert_not_called()

    def test_adjudicator_membership_is_frozen_and_unknown_schema_forms_refuse(self):
        from agent_ee.reviewers import DECISIONS_SCHEMA,bind_evidence_schema,validate_evidence_paths
        path='review-work/circuit/experiments/observed.json';target=self.run_dir/path
        target.parent.mkdir(parents=True);target.write_text('{"value":1}\n')
        schema=copy.deepcopy(DECISIONS_SCHEMA)
        bind_evidence_schema(schema,'decisions',self.run_dir,dict(citation_files=[]),'adjudicator')
        value=dict(decisions=[dict(evidence=[dict(path=path,locator='captured peer',pointer='/value')])])
        validate_evidence_paths(value,schema)
        target.with_name('late.json').write_text('{"value":2}\n')
        late=copy.deepcopy(value);late['decisions'][0]['evidence'][0]['path']=path.replace('observed','late')
        with self.assertRaises(ValueError):validate_evidence_paths(late,schema)
        for shape in ({'$ref':'#/$defs/unknown'}, {'type':'string'}, {'anyOf':[]},
                      {'$ref':'#/$defs/citation_paths_0_0'}):
            bad=copy.deepcopy(schema);bad['$defs']['citation_paths_0_0']=shape
            with self.subTest(shape=shape),self.assertRaises(ValueError):
                validate_evidence_paths(value,bad)

    def test_successful_replay_uses_each_archived_exact_citation_schema(self):
        from agent_ee.reviewers import REVIEW_SCHEMA,bind_evidence_schema
        from agent_ee.review_run import structured_call,verify_role_call,tree_hashes
        schema=copy.deepcopy(REVIEW_SCHEMA)
        bind_evidence_schema(schema,'findings',self.run_dir,
            dict(citation_files=[dict(path='inputs/known.txt',locator_kinds=['lines'])]),'circuit')
        value=dict(findings=[dict(evidence=[dict(path='inputs/known.txt',locator='known',lines=[1,1])])])
        record,_=structured_call(self.run_dir,'circuit','CPU_MOCK_ONLY',schema,
            lambda _:({'execution':'ok'},value),lambda item:item,limit=0)
        with patch('agent_ee.preflight.verify_receipt',return_value='CPU_MOCK_ONLY'), \
                patch('agent_ee.reviewers.evidence_schema',side_effect=AssertionError('must not regenerate policy')):
            verify_role_call(self.run_dir,record,{},'circuit','initial',limit=0)
            archive=self.run_dir/record['attempts'][0]['path']
            value['findings'][0]['evidence'][0]['path']='inputs/foreign.txt'
            write_json(archive/'response.json',value)
            # Make the CPU fixture's byte receipts internally consistent: rejection
            # must come from archived membership, not merely the generic hash guard.
            record['attempts'][0]['artifact_hashes']=tree_hashes(archive)
            with self.assertRaisesRegex(ValueError,'frozen producer policy'):
                verify_role_call(self.run_dir,record,{},'circuit','initial',limit=0)

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
                         f'review-work/{role}/experiments/nested/notes.txt',
                         f'review-work/{role}/experiments/source_topology_audit.json']:
                self.assertTrue(fits(dict(path=path, locator='Scratch evidence', lines=[1, 1]), outgoing))
                self.assertFalse(fits(dict(path=path, locator='Scratch evidence', pointer='/v'), outgoing))
                self.assertFalse(fits(dict(path=path, locator='Scratch evidence', xpath='.'), outgoing))
                self.assertFalse(fits(dict(path=path, locator='Scratch evidence', pointer='/v', lines=[1,1]), outgoing))
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

    def test_prompt_distinguishes_captured_json_from_new_own_json_experiment(self):
        packet=build_packet(self.run_dir,[self.erc_check,self.rc_check],self.run_id)
        seen=[]
        def transport(run,role,definition,prompt,schema,timeout,**kwargs):
            self.assertIn('Frozen captured files',prompt)
            self.assertIn('New own experiments require lines only',prompt)
            self.assertIn('including newly created JSON and XML',prompt)
            self.assertNotIn('For new experiments inspect the bytes using the same rules',prompt)
            path=f'review-work/{role}/experiments/source_topology_audit.json'
            write_json(run/path,{'status':'authored CPU evidence, not native qualification'})
            dynamic=schema['properties']['findings']['items']['properties']['evidence']['items']['anyOf'][-1]
            self.assertEqual(set(dynamic['required']),{'path','locator','lines'})
            self.assertTrue(ecma_search(dynamic['properties']['path']['pattern'],path))
            citation=dict(path=path,locator='Authored audit observation',lines=[1,3])
            self.assertEqual(validate_citation(citation,run,require_structured=True),citation)
            self.assertFalse(ecma_search(dynamic['properties']['path']['pattern'],'review-work/peer/experiments/source_topology_audit.json'))
            seen.append(role)
            review=self._review(role,findings=[self._finding(evidence=[citation])])
            review.update(schema='agent-ee.review/2',candidate_id=packet['candidate_id'],run_id=packet['run_id'],packet_sha256=packet['packet_sha256'])
            return {'execution':'ok'},review
        with patch('agent_ee.reviewers.reviewer_call',side_effect=transport):
            reviews,records=initial_reviews(self.run_dir,packet,time.monotonic()+30)
        self.assertEqual(len(reviews),2,records)
        self.assertEqual(set(seen),{'circuit','physical'})

    def test_new_experiment_role_namespace_is_literal_and_rejects_escape_inputs(self):
        from agent_ee.reviewers import evidence_schema
        for role in ('source-topology','source_topology','circuit','adjudicator'):
            schema=evidence_schema(self.run_dir,{'citation_files':[]},role)
            branch=schema['items']['anyOf'][-1];pattern=branch['properties']['path']['pattern']
            # JSON Schema ECMAScript Unicode patterns forbid Python's \- escape
            # outside a character class; the admitted role alphabet is literal.
            self.assertTrue(pattern.startswith('^review-work/'+role+'/experiments/'))
            self.assertEqual(set(branch['required']),{'path','locator','lines'})
            self.assertTrue(ecma_search(pattern,f'review-work/{role}/experiments/nested/audit.json'))
            for path in ('review-work/peer/experiments/audit.json',f'review-work/{role}/experiments/../peer/audit.json',
                         f'review-work/{role}/experiments/audit.json\n'):
                self.assertFalse(ecma_search(pattern,path))
        for role in ('source.topology','peer|source-topology','a/b',r'a\b','x.*','../root','with space','a\nb','',None,'a'*65):
            with self.subTest(role=role),self.assertRaisesRegex(ValueError,'review role'):
                evidence_schema(self.run_dir,{'citation_files':[]},role)

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

    def test_adjudicator_compacts_large_inventory_on_real_transport_path(self):
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import adjudicator_call, validate_evidence_paths
        for number in range(1005):
            (self.run_dir / f'work/rc_transient/observation-{number:04}.txt').write_text('Observation\n')
        packet = build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)
        self.assertGreater(len(packet['citation_files']), 1000)
        # This file exists and resolves, but was created after the inventory froze.
        unknown = 'work/rc_transient/observation-9999.txt'
        (self.run_dir / unknown).write_text('Uncaptured observation\n')
        citation = dict(path=unknown, locator='Observation', lines=[1, 1])
        validate_citation(citation, self.run_dir, require_structured=True)
        response = dict(schema='agent-ee.decisions/2', candidate_id=packet['candidate_id'],
                        run_id=packet['run_id'], decisions=[dict(finding_id='circuit/f1',
                        disposition='accepted', reason='Source observation', evidence=[citation])])

        def patterns(node):
            if isinstance(node, dict):
                if 'pattern' in node:
                    yield node['pattern']
                for child in node.values():
                    yield from patterns(child)
            elif isinstance(node, list):
                for child in node:
                    yield from patterns(child)

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            self.assertEqual(role, 'adjudicator')
            self.assertLessEqual(max(map(len, patterns(strict_schema(schema))), default=0), 256)
            return {'execution': 'ok'}, response

        with patch('agent_ee.preflight.guard', return_value=({'result': 'ok'}, {}, '/usr/bin/true')), \
             patch('agent_ee.reviewers.dispatch', side_effect=transport) as dispatch:
            record, accepted = adjudicator_call(self.run_dir, packet, [self._finding('circuit/f1')], [],
                                               time.monotonic() + 30)
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(record['resubmissions_used'], 1)
        self.assertEqual((record['execution'], record['refusal_kind']), ('malformed', 'structured_output'))
        self.assertIn('path not captured', record['error'])
        self.assertIsNone(accepted)
        local = read_json(self.run_dir / record['attempts'][0]['path'] / 'schema.json')
        self.assertGreater(max(map(len, patterns(local))), 256)
        with self.assertRaisesRegex(ValueError, 'path not captured'):
            validate_evidence_paths(response, local)
        good = copy.deepcopy(response)
        good['decisions'][0]['evidence'][0]['path'] = 'work/rc_transient/observation-0000.txt'
        validate_evidence_paths(good, local)

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
                    for row in inventory:
                        path = row['path']
                        if accepts_path(path, ref, outgoing['$defs']):
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

    def test_repeated_source_directories_fit_every_complete_producer_schema(self):
        import hashlib
        import json
        from agent_ee.harnesses import strict_schema
        from agent_ee.reviewers import REVIEW_SCHEMA, WITNESS_SCHEMA, DECISIONS_SCHEMA, bind_evidence_schema
        from agent_ee.rf_review import ASSESSMENT_KEY, assessment_schema
        # Owned names model repeated complete source captures, not a private task inventory.
        names = [hashlib.sha256(str(i).encode()).hexdigest()+'.data' for i in range(700)]
        kinds = [('lines', 'pointer')]*350 + [('lines',)]*200 + [('lines', 'xpath')]*150
        inventory = [dict(path=f'inputs/retained/source-{d}/complete-capture/'+name,
                          locator_kinds=list(formats))
                     for d in range(8) for name, formats in zip(names, kinds)]
        packet = dict(citation_files=inventory)
        for role in ('circuit', 'physical', 'rf', 'adjudicator'):
            with self.subTest(role=role):
                schema = copy.deepcopy(DECISIONS_SCHEMA if role=='adjudicator' else REVIEW_SCHEMA)
                collection = 'decisions' if role=='adjudicator' else 'findings'
                for key in ('candidate_id', 'run_id'):
                    schema['properties'][key] = dict(type='string', enum=['a'*64])
                if role=='adjudicator':
                    schema['properties'][collection]['items']['properties']['finding_id']['enum'] = [
                        'check/owned-'+str(i) for i in range(5)]
                else:
                    schema['properties']['role'] = dict(type='string', enum=[role])
                    schema['properties']['packet_sha256'] = dict(type='string', enum=['b'*64])
                    schema['properties'][collection]['items']['properties']['witnesses'] = WITNESS_SCHEMA
                    schema['properties'][collection]['items']['required'].append('witnesses')
                if role=='rf':
                    schema['properties'][ASSESSMENT_KEY] = assessment_schema('c'*64)
                    schema['required'].append(ASSESSMENT_KEY)
                bind_evidence_schema(schema, collection, self.run_dir, packet, role)
                for wire in (schema, strict_schema(schema)):
                    self.assertLess(len(json.dumps(wire, separators=(',', ':')).encode()), 120*1024)

    def test_directory_factoring_preserves_exact_combinations_and_formats(self):
        from agent_ee.reviewers import REVIEW_SCHEMA, bind_evidence_schema, literal_path_set
        names = ['a[0].txt', 'b(1)+$.txt', 'µ space.txt', 'line\u2028separator.txt']
        paths = [f'inputs/d{i}/'+name for i in range(12) for name in names]
        paths += ['baseline.json', 'inputs/partial/'+names[0]]
        shape = literal_path_set(paths)
        self.assertEqual(shape, literal_path_set(list(reversed(paths))+paths[:2]))
        def accepts(path):
            return any(p['minLength'] <= len(path) <= p['maxLength']
                       and ecma_search(p['pattern'], path) for p in shape['anyOf'])
        for prefix in ['']+[f'inputs/d{i}/' for i in range(13)]+['inputs/partial/']:
            for name in names+['baseline.json', 'unknown.txt']:
                path = prefix+name
                self.assertEqual(bool(accepts(path)), path in paths, path)
        for path in paths:
            for suffix in ('\n', '\r\n', '\u2028', '\u2029', '/../../escape', '.unknown'):
                self.assertFalse(accepts(path+suffix), path+repr(suffix))
        inventory = [dict(path=path, locator_kinds=['lines']) for path in paths]
        special = 'inputs/d0/'+names[0]
        inventory[0]['locator_kinds'] = ['lines', 'pointer']
        # Force finite-regex transport, retaining the differing format at the same filename.
        inventory += [dict(path=f'inputs/more/{i}.txt', locator_kinds=['lines']) for i in range(1100)]
        schema = copy.deepcopy(REVIEW_SCHEMA)
        bind_evidence_schema(schema, 'findings', self.run_dir, dict(citation_files=inventory), 'circuit')
        branches = schema['properties']['findings']['items']['properties']['evidence']['items']['anyOf']
        pointer = next(b for b in branches if set(b['required'])=={'path','locator','pointer'})
        for row in inventory:
            allowed = accepts_path(row['path'], pointer['properties']['path'], schema['$defs'])
            self.assertEqual(bool(allowed), row['path']==special, row['path'])

    def test_incompressible_citation_inventory_keeps_transport_refusal(self):
        import hashlib
        from agent_ee.reviewers import REVIEW_SCHEMA, bind_evidence_schema
        inventory = [dict(path='inputs/'+hashlib.sha256(str(i).encode()).hexdigest()+'/'+
                               hashlib.sha256(('name'+str(i)).encode()).hexdigest()+'.txt',
                          locator_kinds=['lines']) for i in range(2500)]
        with self.assertRaisesRegex(ValueError, '120 KiB transport limit'):
            bind_evidence_schema(copy.deepcopy(REVIEW_SCHEMA), 'findings', self.run_dir,
                                 dict(citation_files=inventory), 'circuit')

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
                self.assertIn('frozen producer policy requires locator kind(s) lines', prompt)
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
