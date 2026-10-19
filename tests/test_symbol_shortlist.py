"""Two-stage library-restricted symbol shortlist (AGENTEE-37) over owned captured libraries.

Model calls use a scripted transport at the reviewer harness seam or a recorded
provider console through the real Claude adapter; nothing reaches a provider.
"""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import symbol_library
from agent_ee.footprint_library import canonical, digest
from agent_ee.symbol_shortlist import (APPROVAL_SCHEMA, LIBRARY_PICK, SYMBOL_PICK, ShortlistError,
                                       approved_symbols, build_index, main, plan_chunks, select, verify)

DEFINITION = {'harness': 'claude', 'model': 'declared-model', 'effort': 'low', 'timeout_s': 60}
STOCK = Path('/usr/share/kicad/symbols')


def symbol(name, description='', keywords='', extends=None, private=False):
    body = f'(symbol "{name}"' + (f' (extends "{extends}")' if extends else '')
    body += ' (property "Reference" "U" (at 0 0 0))'
    if description:
        body += f' (property "Description" "{description}" (at 0 0 0))'
    if keywords:
        body += f' (property "ki_keywords" "{keywords}" (at 0 0 0))'
    if private:
        body += ' (property private "KLC_S3.3" "owned note" (at 0 0 0))'
        body += ' (property private "KLC_S4.1" "second owned note" (at 0 0 0))'
    if not extends:
        body += (f' (symbol "{name}_1_1" (pin passive line (at 0 0 0) (length 2)'
                 ' (name "A") (number "1")))')
    return body + ')'


def library(*symbols):
    return ('(kicad_symbol_lib (version 20231120) (generator "owned")\n'
            + '\n'.join(symbols) + ')').encode()


PASSIVE = library(symbol('R_0402', 'Owned thick-film resistor, 0402', 'resistor R'),
                  symbol('C_0402', 'Owned ceramic capacitor, 0402', 'capacitor C'),
                  symbol('C_0402_X7R', '', '', extends='C_0402'),
                  symbol('L_0603', 'Owned inductor, 0603', 'inductor L', private=True))
POWER = library(symbol('GND', 'Owned ground port', 'power ground'),
                symbol('VBUS', 'Owned bus supply port', 'power'))
LOGIC = library(symbol('INV1', 'Owned single inverter', 'logic inverter'))


def captured(*named):
    files, libraries = {}, []
    for name, data in named:
        path = f'symbols/{name}.kicad_sym'
        files[path] = data
        libraries.append(dict(name=name, path=path, sha256=hashlib.sha256(data).hexdigest()))
    return libraries, files


def request(libraries, budget=4096, always=('Owned_Power',)):
    return {'schema': 'agent-ee.symbol-shortlist-request/1',
            'requirements': [{'id': 'R-divider', 'text': 'Divide the 5 V bus to 2.5 V for the ADC.'},
                             {'id': 'R-decouple', 'text': 'Decouple the ADC supply.'}],
            'libraries': libraries, 'always_libraries': list(always), 'chunk_budget_chars': budget}


def ok(value):
    return {'execution': 'ok', 'backend': 'claude', 'model_requested': DEFINITION['model'],
            'billing_route': 'subscription'}, value


def library_pick(*names):
    return {'schema': LIBRARY_PICK, 'libraries': [{'name': n, 'reason': 'needed'} for n in names]}


def symbol_pick(*items):
    return {'schema': SYMBOL_PICK, 'symbols': [
        {'lib_id': lib_id, 'requirement_ids': list(ids), 'reason': 'fits'} for lib_id, ids in items]}


class Transport:
    """Scripted responses per call label; records every prompt and schema it was given."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, run, label, definition, prompt, schema, timeout, **kwargs):
        self.calls.append(dict(label=label, prompt=prompt, schema=schema, definition=definition))
        answer = self.script[label]
        return copy.deepcopy(answer(prompt) if callable(answer) else answer)


def listed(prompt):
    return prompt.split('\nSYMBOLS:\n', 1)[1].rstrip('\n').split('\n')


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-shortlist-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp))
        self.libraries, self.files = captured(('Owned_Passive', PASSIVE), ('Owned_Power', POWER),
                                              ('Owned_Logic', LOGIC))

    def run_selection(self, script, value=None, name='run'):
        transport = Transport(script)
        with patch('agent_ee.reviewers.dispatch', side_effect=transport):
            result = select(value or request(self.libraries), self.files, DEFINITION, self.tmp / name)
        return result, transport

    def test_index_reads_each_capture_once_with_inherited_and_private_properties(self):
        with patch.object(symbol_library, 'parse_sexpr', wraps=symbol_library.parse_sexpr) as parse:
            index = build_index(self.libraries, self.files)
        self.assertEqual(parse.call_count, 3)
        passive = index['libraries'][0]
        self.assertEqual([s['name'] for s in passive['symbols']], ['C_0402', 'C_0402_X7R', 'L_0603', 'R_0402'])
        derived = passive['symbols'][1]
        self.assertEqual((derived['description'], derived['keywords']),
                         ('Owned ceramic capacitor, 0402', 'capacitor C'))
        self.assertEqual(passive['symbols'][2]['description'], 'Owned inductor, 0603')
        self.assertEqual(index['canonical_sha256'], build_index(self.libraries, self.files)['canonical_sha256'])

    def test_index_refuses_changed_unsafe_or_ambiguous_captures(self):
        changed = dict(self.files, **{'symbols/Owned_Logic.kicad_sym': LOGIC + b'\n'})
        with self.assertRaisesRegex(ShortlistError, 'hash-mismatched'):
            build_index(self.libraries, changed)
        for data, message in ((library(symbol('A:B', 'x')), 'cannot be listed'),
                              (library(symbol('D', 'one').replace(' (symbol "D_1_1"', ' (property "Description" "two") (symbol "D_1_1"')),
                               'ambiguous Description'),
                              (library(symbol('E', 'x', extends='Missing')), 'missing parent')):
            libraries, files = captured(('Bad', data))
            with self.assertRaisesRegex(ShortlistError, message):
                build_index(libraries, files)

    def test_hallucinated_names_are_dropped_with_reasons_and_survivors_exist(self):
        result, transport = self.run_selection({
            'shortlist-libraries': ok(library_pick('Owned_Passive', 'Invented_Lib')),
            'shortlist-symbols-0000': ok(symbol_pick(
                ('Owned_Passive:R_0402', ['R-divider']), ('Owned_Passive:C_0402_X7R', ['R-decouple']),
                ('Owned_Power:GND', ['R-divider', 'R-decouple']), ('Owned_Passive:R_Magic', ['R-divider']),
                ('Invented_Lib:X1', ['R-divider']), ('Owned_Logic:INV1', ['R-divider']),
                ('R_0402', ['R-divider']), ('Owned_Passive:R_0402', ['R-decouple'])))})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'))
        self.assertEqual([c['label'] for c in transport.calls], ['shortlist-libraries', 'shortlist-symbols-0000'])
        self.assertEqual([d['name'] for d in result['libraries']], ['Owned_Passive', 'Owned_Power'])
        self.assertEqual(result['libraries'][1]['source'], 'always_libraries')
        self.assertEqual(result['dropped_libraries'], [{'name': 'Invented_Lib', 'reason': 'library_not_in_index',
                                                        'model_reason': 'needed'}])
        self.assertEqual({d['lib_id']: d['reason'] for d in result['dropped']}, {
            'Owned_Passive:R_Magic': 'symbol_not_in_library', 'Invented_Lib:X1': 'library_not_in_index',
            'Owned_Logic:INV1': 'library_not_selected', 'R_0402': 'not_a_lib_id'})
        self.assertEqual([s['lib_id'] for s in result['symbols']],
                         ['Owned_Passive:C_0402_X7R', 'Owned_Passive:R_0402', 'Owned_Power:GND'])
        self.assertEqual(result['symbols'][1]['requirement_ids'], ['R-decouple', 'R-divider'])
        index = build_index(self.libraries, self.files)
        names = {lib['name'] + ':' + s['name'] for lib in index['libraries'] for s in lib['symbols']}
        for entry in result['symbols']:
            self.assertIn(entry['lib_id'], names)
            self.assertEqual(entry['library_sha256'], hashlib.sha256(self.files[entry['library_path']]).hexdigest())
        # Only the selected libraries were listed, and the schema enumerates the request IDs.
        self.assertNotIn('Owned_Logic:INV1', transport.calls[1]['prompt'])
        self.assertEqual(transport.calls[1]['schema']['properties']['symbols']['items']['properties']
                         ['requirement_ids']['items']['enum'], ['R-divider', 'R-decouple'])
        self.assertEqual(transport.calls[0]['definition'], {**DEFINITION, 'tool_mode': 'fixed_packet'})
        self.assertEqual(result['decision'], {'status': 'pending_owner_approval', 'approval_schema': APPROVAL_SCHEMA})
        self.assertEqual(verify(self.tmp / 'run', self.files), result)
        stored = json.loads((self.tmp / 'run/shortlist.json').read_bytes())
        self.assertEqual(stored['canonical_sha256'], digest({k: v for k, v in stored.items() if k != 'canonical_sha256'}))

    def test_libraries_over_budget_split_across_calls_without_loss(self):
        many = library(*(symbol(f'R_{n:03d}', f'Owned resistor value {n} with a deliberately long description',
                                'resistor') for n in range(60)))
        libraries, files = captured(('Owned_Many', many), ('Owned_Power', POWER))
        self.libraries, self.files = libraries, files
        value = request(libraries, budget=256)
        script = {'shortlist-libraries': ok(library_pick('Owned_Many'))}
        for number in range(40):
            script[f'shortlist-symbols-{number:04d}'] = (
                lambda prompt: ok(symbol_pick(*((line.split(' | ')[0], ['R-divider']) for line in listed(prompt)
                                                if line.startswith('Owned_Many:R_059')))))
        result, transport = self.run_selection(script, value)
        prompts = [c['prompt'] for c in transport.calls[1:]]
        self.assertGreater(len(prompts), 10)
        lines = [line for prompt in prompts for line in listed(prompt)]
        index = build_index(libraries, files)
        expected = [lib['name'] + ':' + s['name'] for lib in index['libraries'] for s in lib['symbols']]
        self.assertEqual([line.split(' | ')[0] for line in lines], expected)
        self.assertEqual(len(set(lines)), len(lines))
        self.assertTrue(all(len('\n'.join(listed(prompt))) <= 256 for prompt in prompts))
        self.assertEqual(result['coverage']['selected_entries'], 62)
        self.assertEqual(result['coverage']['chunked_entries'], 62)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(sum(c['entries'] for c in result['chunks']), 62)
        self.assertEqual([s['lib_id'] for s in result['symbols']], ['Owned_Many:R_059'])
        self.assertEqual(verify(self.tmp / 'run', files)['canonical_sha256'], result['canonical_sha256'])

    def test_stock_device_and_power_chunk_plan_keeps_every_entry(self):
        libraries, files = captured(*((name, (STOCK / f'{name}.kicad_sym').read_bytes()) for name in ('Device', 'power')))
        index = build_index(libraries, files)
        chunks, coverage = plan_chunks(index, ['Device', 'power'], 20000)
        names = [lib['name'] + ':' + s['name'] for lib in index['libraries'] for s in lib['symbols']]
        self.assertGreater(len(chunks), 1)
        self.assertEqual([lib_id for chunk in chunks for lib_id, _ in chunk], names)
        self.assertTrue(all(len('\n'.join(line for _, line in chunk)) <= 20000 for chunk in chunks))
        self.assertEqual(coverage['selected_entries_sha256'], digest(names))
        self.assertIn('Device:R', names)
        self.assertIn('power:GND', names)

    def test_entry_larger_than_budget_is_refused_before_any_call(self):
        libraries, files = captured(('Owned_Wide', library(symbol('W', 'x' * 300))))
        transport = Transport({})
        with patch('agent_ee.reviewers.dispatch', side_effect=transport), \
                self.assertRaisesRegex(ShortlistError, 'index entry exceeds chunk budget: Owned_Wide:W'):
            select(request(libraries, budget=256, always=()), files, DEFINITION, self.tmp / 'wide')
        self.assertEqual(transport.calls, [])
        self.assertFalse((self.tmp / 'wide').exists())
        for change in ({'chunk_budget_chars': 255}, {'chunk_budget_chars': True}, {'always_libraries': ['Nope']},
                       {'requirements': []}, {'extra': 1}):
            with self.assertRaises(ShortlistError):
                select({**request(libraries, always=()), **change}, files, DEFINITION, self.tmp / 'bad')
        with self.assertRaisesRegex(ValueError, 'explicit native model name'):
            select(request(libraries, always=()), files, {**DEFINITION, 'model': ''}, self.tmp / 'bad')
        self.assertFalse((self.tmp / 'bad').exists())

    def assert_failed_closed(self, result, transport, execution, label, message):
        self.assertEqual((result['execution'], result['outcome']), (execution, 'unknown'))
        self.assertEqual((result['symbols'], result['dropped'], result['libraries']), ([], [], []))
        self.assertEqual(result['decision'], {'status': 'not_approvable'})
        self.assertTrue(result['error'].startswith(label + ': '), result['error'])
        self.assertIn(message, result['error'])
        self.assertEqual(transport.calls[-1]['label'], label)
        self.assertEqual([c['label'] for c in transport.calls].count(label), 1)

    def test_malformed_or_failed_responses_fail_closed_without_default_list(self):
        good_library = ok(library_pick('Owned_Passive'))
        cases = [
            ({'shortlist-libraries': ok({'schema': LIBRARY_PICK, 'libraries': [{'name': 'Owned_Passive'}]})},
             'malformed', 'shortlist-libraries', 'malformed library pick entry'),
            ({'shortlist-libraries': ok({'schema': 'other', 'libraries': []})},
             'malformed', 'shortlist-libraries', 'malformed library pick'),
            ({'shortlist-libraries': ok(['Owned_Passive'])}, 'malformed', 'shortlist-libraries', 'malformed library pick'),
            ({'shortlist-libraries': ok(None)}, 'malformed', 'shortlist-libraries', 'missing structured output'),
            ({'shortlist-libraries': ({'execution': 'failed', 'error': 'provider did not finish'}, None)},
             'failed', 'shortlist-libraries', 'provider did not finish'),
            ({'shortlist-libraries': ({'execution': 'unavailable', 'error': 'credential unavailable'}, None)},
             'unavailable', 'shortlist-libraries', 'credential unavailable'),
            ({'shortlist-libraries': good_library,
              'shortlist-symbols-0000': ok(symbol_pick(('Owned_Passive:R_0402', ['R-invented'])))},
             'malformed', 'shortlist-symbols-0000', "unknown requirement id 'R-invented'"),
            ({'shortlist-libraries': good_library,
              'shortlist-symbols-0000': ok({'schema': SYMBOL_PICK, 'symbols': [
                  {'lib_id': 'Owned_Passive:R_0402', 'requirement_ids': ['R-divider'], 'reason': 'x', 'extra': 1}]})},
             'malformed', 'shortlist-symbols-0000', 'malformed symbol pick entry'),
            ({'shortlist-libraries': good_library,
              'shortlist-symbols-0000': ({'execution': 'timeout', 'error': 'reviewer timed out'}, None)},
             'timeout', 'shortlist-symbols-0000', 'reviewer timed out'),
        ]
        for number, (script, execution, label, message) in enumerate(cases):
            with self.subTest(case=number):
                result, transport = self.run_selection(script, name=f'case{number}')
                self.assert_failed_closed(result, transport, execution, label, message)
                run = self.tmp / f'case{number}'
                self.assertEqual(verify(run, self.files), result)
                retained = json.loads((run / 'review-executions' / f'{label}.json').read_text())
                self.assertEqual((retained['max_schema_resubmissions'], retained['resubmissions_used']), (0, 0))
                with self.assertRaisesRegex(ShortlistError, 'not approvable'):
                    approved_symbols(run, self.files, self.approval(result))

    def test_failure_in_one_chunk_stops_later_chunks(self):
        many = library(*(symbol(f'R_{n:03d}', f'Owned resistor value {n} with a deliberately long description')
                         for n in range(30)))
        libraries, files = captured(('Owned_Many', many))
        self.files = files
        script = {'shortlist-libraries': ok(library_pick('Owned_Many')),
                  'shortlist-symbols-0000': ok(symbol_pick(('Owned_Many:R_000', ['R-divider']))),
                  'shortlist-symbols-0001': ok({'schema': SYMBOL_PICK})}
        result, transport = self.run_selection(script, request(libraries, budget=256, always=()))
        self.assert_failed_closed(result, transport, 'malformed', 'shortlist-symbols-0001', 'malformed symbol pick')
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(verify(self.tmp / 'run', files), result)

    def test_no_surviving_symbol_is_unknown_and_not_approvable(self):
        result, _ = self.run_selection({'shortlist-libraries': ok(library_pick('Owned_Passive')),
                                        'shortlist-symbols-0000': ok(symbol_pick(('Owned_Passive:Nope', ['R-divider'])))})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'unknown'))
        self.assertEqual(result['error'], 'no returned symbol passed the existence gate')
        self.assertEqual(result['dropped'][0]['reason'], 'symbol_not_in_library')
        self.assertEqual(result['decision'], {'status': 'not_approvable'})
        with self.assertRaisesRegex(ShortlistError, 'not approvable'):
            approved_symbols(self.tmp / 'run', self.files, self.approval(result))

    def approval(self, shortlist, **change):
        return {'schema': APPROVAL_SCHEMA, 'shortlist_sha256': shortlist['canonical_sha256'],
                'owner': 'synthetic test owner', 'disposition': 'approved',
                'reference': 'synthetic test approval; not an owner record', **change}

    def selected(self, name='run'):
        return self.run_selection({'shortlist-libraries': ok(library_pick('Owned_Passive')),
                                   'shortlist-symbols-0000': ok(symbol_pick(('Owned_Passive:R_0402', ['R-divider']),
                                                                            ('Owned_Power:GND', ['R-divider'])))},
                                  name=name)[0]

    def test_generation_needs_owner_approval_bound_to_the_shortlist_hash(self):
        result = self.selected()
        run = self.tmp / 'run'
        self.assertFalse(any(run.rglob('*approval*')))
        self.assertEqual(approved_symbols(run, self.files, self.approval(result)),
                         ['Owned_Passive:R_0402', 'Owned_Power:GND'])
        for change, message in (({'shortlist_sha256': '0' * 64}, 'different shortlist'),
                                ({'disposition': 'rejected'}, 'owner rejected'),
                                ({'disposition': 'pending'}, 'approved or rejected'),
                                ({'owner': ''}, 'approval owner'),
                                ({'schema': 'agent-ee.violation-waivers/1'}, APPROVAL_SCHEMA)):
            with self.assertRaisesRegex(ShortlistError, message):
                approved_symbols(run, self.files, self.approval(result, **change))
        with self.assertRaisesRegex(ShortlistError, 'expected fields'):
            approved_symbols(run, self.files, {**self.approval(result), 'auto': True})

    def test_offline_verification_refuses_tampering(self):
        def tampered(name, edit, message):
            self.selected(name)
            run = self.tmp / name
            edit(run)
            with self.assertRaisesRegex(ShortlistError, message):
                verify(run, self.files)

        def reseal(run, change):
            path = run / 'shortlist.json'
            value = json.loads(path.read_bytes())
            change(value)
            value.pop('canonical_sha256')
            value['canonical_sha256'] = digest(value)
            path.write_bytes(canonical(value) + b'\n')

        tampered('added', lambda run: reseal(run, lambda v: v['symbols'].append(
            dict(v['symbols'][0], lib_id='Owned_Logic:INV1'))), 'replayed existence gate: symbols')
        tampered('dropped', lambda run: reseal(run, lambda v: v['dropped'].clear() or v['symbols'].pop()),
                 'replayed existence gate')
        tampered('unsealed', lambda run: (run / 'shortlist.json').write_text(
            (run / 'shortlist.json').read_text().replace('Owned_Power:GND', 'Owned_Power:VBUS')), 'hash does not match')
        tampered('response', lambda run: (run / 'review-attempts/shortlist-symbols-0000/0/response.json').write_text(
            json.dumps(symbol_pick(('Owned_Passive:R_0402', ['R-divider'])))), 'replayed existence gate')
        tampered('prompt', lambda run: (run / 'review-attempts/shortlist-symbols-0000/0/prompt.txt').write_text(
            'SYMBOLS:\n'), 'retained prompt for chunk 0')
        tampered('record', lambda run: (run / 'review-executions/shortlist-symbols-0000.json').write_text('{}'),
                 'retained call record changed')
        self.selected('capture')
        changed = dict(self.files, **{'symbols/Owned_Passive.kicad_sym': PASSIVE + b'\n'})
        with self.assertRaisesRegex(ShortlistError, 'hash-mismatched'):
            verify(self.tmp / 'capture', changed)

    def test_recorded_provider_console_through_the_claude_adapter(self):
        home = self.tmp / 'profile'
        (home / '.claude').mkdir(parents=True)
        (home / '.claude/.credentials.json').write_text('test placeholder; not used by recorded console')
        consoles = {'shortlist-libraries': library_pick('Owned_Passive'),
                    'shortlist-symbols-0000': symbol_pick(('Owned_Passive:R_0402', ['R-divider']),
                                                          ('Owned_Passive:Phantom', ['R-divider']))}
        launches = []

        def recorded(inputs, work, argv, **kwargs):
            launches.append(dict(argv=argv, network=kwargs.get('network'), stdin=kwargs.get('stdin')))
            (work / 'stdout.log').write_text(json.dumps({'subtype': 'success', 'is_error': False,
                                                         'structured_output': consoles[work.name]}))
            return {'execution': 'ok', 'exit_code': 0}

        with patch('agent_ee.reviewers.Path.home', return_value=home), \
                patch('agent_ee.reviewers.shutil.which', return_value='/usr/bin/true'), \
                patch('agent_ee.reviewers.execute', side_effect=recorded):
            result = select(request(self.libraries, always=()), self.files, DEFINITION, self.tmp / 'run')
        self.assertEqual([s['lib_id'] for s in result['symbols']], ['Owned_Passive:R_0402'])
        self.assertEqual(result['dropped'][0]['reason'], 'symbol_not_in_library')
        self.assertEqual(len(launches), 2)
        for launch in launches:
            argv = launch['argv']
            self.assertEqual(argv[argv.index('--tools') + 1], '')
            self.assertEqual(argv[argv.index('--model') + 1], 'declared-model')
            self.assertIn('--json-schema', argv)
            self.assertTrue(launch['network'])
        self.assertIn('Owned_Passive:R_0402 | Owned thick-film resistor, 0402', launches[1]['stdin'])
        record = json.loads((self.tmp / 'run/review-executions/shortlist-libraries.json').read_text())
        self.assertEqual((record['backend'], record['billing_route']), ('claude', 'subscription'))
        self.assertFalse((self.tmp / 'run/review-work/shortlist-libraries/home').exists())
        self.assertEqual(verify(self.tmp / 'run', self.files), result)

    def test_cli_select_verify_and_approval(self):
        captures = self.tmp / 'captures'
        for path, data in self.files.items():
            (captures / path).parent.mkdir(parents=True, exist_ok=True)
            (captures / path).write_bytes(data)
        (self.tmp / 'request.json').write_text(json.dumps(request(self.libraries)))
        (self.tmp / 'definition.json').write_text(json.dumps(DEFINITION))
        transport = Transport({'shortlist-libraries': ok(library_pick('Owned_Passive')),
                               'shortlist-symbols-0000': ok(symbol_pick(('Owned_Passive:R_0402', ['R-divider'])))})

        def cli(*argv):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(list(map(str, argv)))
            return code, json.loads(output.getvalue())

        with patch('agent_ee.reviewers.dispatch', side_effect=transport):
            code, summary = cli('select', self.tmp / 'request.json', '--captures', captures,
                                '--definition', self.tmp / 'definition.json', '--out', self.tmp / 'cli')
        self.assertEqual((code, summary['symbols']), (0, ['Owned_Passive:R_0402']))
        self.assertEqual(summary['decision']['status'], 'pending_owner_approval')
        code, verified = cli('verify', self.tmp / 'cli', '--captures', captures)
        self.assertEqual((code, verified['verified']), (0, True))
        approval = self.tmp / 'approval.json'
        approval.write_text(json.dumps(self.approval(summary)))
        self.assertEqual(cli('verify', self.tmp / 'cli', '--captures', captures, '--approval', approval),
                         (0, {'approved': True, 'symbols': ['Owned_Passive:R_0402']}))
        approval.write_text(json.dumps(self.approval(summary, disposition='rejected')))
        code, refused = cli('verify', self.tmp / 'cli', '--captures', captures, '--approval', approval)
        self.assertEqual((code, refused['approved']), (1, False))
        with patch('agent_ee.reviewers.dispatch', side_effect=transport), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_:
            main(['select', str(self.tmp / 'request.json'), '--captures', str(captures),
                  '--definition', str(self.tmp / 'definition.json'), '--out', str(self.tmp / 'cli')])
        self.assertEqual(exit_.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
