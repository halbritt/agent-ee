"""UUID-insensitive canonical design diff and regeneration determinism (AGENTEE-51).

The board fixture is original (fixtures/design-diff/README.md) and saved by
KiCad's own writer. Native tests re-save and export through sandboxed kicad-cli,
so UUID churn, write order and export headers come from the real tool.
"""
import io
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_design_diff.

from agent_ee import executor
from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import KINDS, describe_check
from agent_ee.checks import CHECK_FUNCS, Ctx, CheckError, run_checks
from agent_ee.design_diff import Malformed, check_regeneration_determinism, compare, main, parameters
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
PAIR = REPO / 'fixtures/design-diff/pair.kicad_pcb'
RC = REPO / 'fixtures/rc/control'
UUID = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def churn(data):
    """Every UUID replaced by a fresh random one, consistently within the file."""
    fresh = {}
    return UUID.sub(lambda m: fresh.setdefault(m.group(), str(uuid.uuid4()).encode()), data)


def swap_footprints(data):
    """The two top-level footprint blocks written in the opposite order."""
    text = data.decode()
    starts = [m.start() for m in re.finditer(r'(?m)^\t\(footprint ', text)]
    end = text.index('\n\t)\n', starts[1]) + 4
    first, second = text[starts[0]:starts[1]], text[starts[1]:end]
    return (text[:starts[0]] + second + first + text[end:]).encode()


def netlist_xml(date, code_offset=0, out='/OUT'):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <design><source>/tmp/{date}/a.kicad_sch</source><date>{date}</date><tool>Eeschema 10.0.7</tool>
    <sheet number="1" name="/" tstamps="/"><title_block><title>T</title><date/></title_block></sheet></design>
  <components>
    <comp ref="R1"><value>1k</value><tstamps>{uuid.uuid4()}</tstamps></comp>
    <comp ref="C1"><value>100n</value><tstamps>{uuid.uuid4()}</tstamps></comp>
  </components>
  <nets>
    <net code="{1 + code_offset}" name="{out}" class="Default"><node ref="R1" pin="2"/><node ref="C1" pin="1"/></net>
    <net code="{2 + code_offset}" name="GND" class="Default"><node ref="C1" pin="2"/></net>
  </nets>
</export>
'''.encode()


class DesignDiffTests(unittest.TestCase):
    def setUp(self):
        self.board = PAIR.read_bytes()

    def test_uuid_churn_and_write_order_give_empty_diff_and_identical_hashes(self):
        other = swap_footprints(churn(self.board))
        self.assertNotEqual(other, self.board)
        report = compare(self.board, other, 'board', 2)
        self.assertTrue(report['equivalent'], report['entries'])
        self.assertEqual(report['entries'], [])
        self.assertEqual(report['hashes']['changed'], [])
        self.assertEqual(report['hashes']['reference'], report['hashes']['subject'])
        # Raw byte differences are recorded, never graded.
        self.assertFalse(report['raw']['identical'])
        self.assertGreater(report['raw']['lines_only_in_subject'], 20)
        self.assertEqual(report['typed'], {'counts': {}, 'net_names': {'removed': [], 'added': []},
                                           'transforms': {}, 'assembly_exclusions': {}})

    def test_footprint_move_above_and_below_declared_precision(self):
        moved = self.board.replace(b'(at 105 100)', b'(at 105.1 100)', 1)
        report = compare(self.board, moved, 'board', 2)
        self.assertEqual(report['entries'], [{'path': 'footprint[R2]/at', 'before': '105 100',
                                              'after': '105.1 100'}])
        self.assertEqual(report['hashes']['changed'], ['placement'])
        self.assertEqual(report['typed']['transforms'], {'footprint[R2]': {
            'before': {'at': '105 100', 'layer': 'F.Cu'}, 'after': {'at': '105.1 100', 'layer': 'F.Cu'}}})
        tiny = self.board.replace(b'(at 105 100)', b'(at 105.001 100)', 1)
        report = compare(self.board, tiny, 'board', 2)
        self.assertTrue(report['equivalent'])
        self.assertEqual(report['hashes']['changed'], [])
        self.assertFalse(report['raw']['identical'])
        # The precision is declared, not fixed: three decimals see the same move.
        self.assertEqual([e['path'] for e in compare(self.board, tiny, 'board', 3)['entries']],
                         ['footprint[R2]/at'])

    def test_net_rename_changes_structure_and_names_both_names(self):
        renamed = self.board.replace(b'"/B"', b'"/BUS"')
        report = compare(self.board, renamed, 'board', 2)
        self.assertEqual(report['hashes']['changed'], ['identity', 'structural'])
        self.assertEqual(report['typed']['net_names'], {'removed': ['/B'], 'added': ['/BUS']})
        pads = [e for e in report['entries'] if e['path'].endswith('/net')]
        self.assertEqual(pads, [{'path': 'footprint[R1]/pad[2]/net', 'before': '/B', 'after': '/BUS'},
                                {'path': 'footprint[R2]/pad[1]/net', 'before': '/B', 'after': '/BUS'}])
        segments = [e for e in report['entries'] if e['path'].startswith('segment@')]
        self.assertEqual(len(segments), 2)
        self.assertIn('/B)', next(e['before'] for e in segments if e['before']))
        self.assertIn('/BUS)', next(e['after'] for e in segments if e['after']))

    def test_value_count_and_assembly_exclusion_changes_are_typed(self):
        changed = self.board.replace(b'"2k2"', b'"4k7"').replace(b'(attr smd)', b'(attr smd exclude_from_bom)', 1)
        start = changed.index(b'\t(segment')
        changed = changed[:start] + changed[changed.index(b'\n\t)\n', start) + 4:]
        report = compare(self.board, changed, 'board', 2)
        self.assertIn('structural', report['hashes']['changed'])
        self.assertNotIn('placement', report['hashes']['changed'])
        self.assertEqual(report['typed']['counts'], {'segment': {'before': 1, 'after': 0}})
        self.assertEqual(report['typed']['assembly_exclusions'],
                         {'footprint[R1]': {'before': [], 'after': ['exclude_from_bom']}})
        paths = [e['path'] for e in report['entries']]
        self.assertIn('footprint[R2]/property[Value]', paths)
        self.assertIn('footprint[R1]/attr', paths)

    def test_numbered_nets_compare_by_name(self):
        def legacy(codes):
            table = ''.join(f'\n\t(net {code} "{name}")' for name, code in sorted(codes.items(), key=lambda i: i[1]))
            return (f'(kicad_pcb (version 20221018) (generator pcbnew) (net 0 ""){table}\n'
                    f'\t(footprint "X:Y" (layer "F.Cu") (at 1 2) (fp_text reference "R1" (at 0 0))\n'
                    f'\t\t(pad "1" smd rect (at 0 0) (size 1 1) (net {codes["/A"]} "/A")))\n'
                    f'\t(segment (start 0 0) (end 1 0) (width 0.25) (layer "F.Cu") (net {codes["/A"]}))\n'
                    f'\t(via (at 1 0) (size 0.6) (drill 0.3) (net {codes["GND"]})))\n').encode()
        report = compare(legacy({'/A': 1, 'GND': 2}), legacy({'/A': 2, 'GND': 1}), 'board', 2)
        self.assertTrue(report['equivalent'], report['entries'])
        self.assertEqual(report['typed']['transforms'], {})
        renamed = compare(legacy({'/A': 1, 'GND': 2}), legacy({'/A': 1, 'GND': 2}).replace(b'"GND"', b'"VSS"'),
                          'board', 2)
        self.assertEqual(renamed['typed']['net_names'], {'removed': ['GND'], 'added': ['VSS']})
        self.assertIn({'path': 'net[GND]', 'before': '(net GND)', 'after': None}, renamed['entries'])

    def test_schematic_symbol_move_is_placement_and_label_rename_is_structural(self):
        sch = (RC / 'rc_filter.kicad_sch').read_text()
        end = sch.index('(reference "R1")')
        start = sch.rfind('\t(symbol\n', 0, end)
        moved = re.sub(r'\(at ([\d.]+) ', lambda m: f'(at {float(m.group(1)) + 2.54:g} ', sch[start:end])
        report = compare(sch.encode(), (sch[:start] + moved + sch[end:]).encode(), 'schematic', 2)
        self.assertEqual(report['hashes']['changed'], ['placement'])
        self.assertIn('symbol[R1]/at', [e['path'] for e in report['entries']])
        self.assertTrue(all(e['path'].startswith('symbol[R1]/') for e in report['entries']))
        renamed = compare(sch.encode(), sch.replace('(label "OUT"', '(label "VOUT"').encode(), 'schematic', 2)
        self.assertEqual(renamed['hashes']['changed'], ['identity', 'structural'])
        self.assertEqual(renamed['typed']['net_names'], {'removed': ['OUT'], 'added': ['VOUT']})
        self.assertTrue(compare(sch.encode(), churn(sch.encode()), 'schematic', 2)['equivalent'])

    def test_netlist_identity_ignores_date_tool_source_codes_and_timestamps(self):
        first, second = netlist_xml('2026-10-18T10:00:00'), netlist_xml('2026-10-18T10:00:01', code_offset=5)
        report = compare(first, second, 'netlist')
        self.assertTrue(report['equivalent'], report['entries'])
        self.assertEqual(report['hashes']['reference']['identity'], report['hashes']['subject']['identity'])
        self.assertIsNone(report['hashes']['reference']['placement'])
        self.assertEqual(report['headers']['subject']['date'], '2026-10-18T10:00:01')
        renamed = compare(first, netlist_xml('2026-10-18T10:00:00', out='/VOUT'), 'netlist')
        self.assertEqual(renamed['hashes']['changed'], ['identity', 'structural'])
        self.assertEqual(renamed['typed']['net_names'], {'removed': ['/OUT'], 'added': ['/VOUT']})

    def test_json_probe_outputs_compare_without_ids(self):
        left = {'components': {'R1': {'uuid': str(uuid.uuid4()), 'value': '1k'}}, 'nets': {'/OUT': ['R1.2']}}
        right = json.loads(json.dumps(left))
        right['components']['R1']['uuid'] = str(uuid.uuid4())
        self.assertTrue(compare(json.dumps(left).encode(), json.dumps(right).encode(), 'json')['equivalent'])
        right['nets']['/OUT'].append('C1.1')
        report = compare(json.dumps(left).encode(), json.dumps(right).encode(), 'json')
        self.assertEqual([e['path'] for e in report['entries']], ['nets//OUT/[1]'])
        rounded = compare(b'{"x": 1.0001}', b'{"x": 1.0004}', 'json', 3)
        self.assertTrue(rounded['equivalent'])
        deep = b'[' * 200 + b']' * 200
        with self.assertRaisesRegex(Malformed, 'nesting limit'):
            compare(deep, deep, 'json')

    def test_parse_failure_on_either_side_is_malformed(self):
        for reference, subject, side in ((self.board[:-20], self.board, 'reference'),
                                         (self.board, b'(kicad_pcb (version 1)', 'subject'),
                                         (self.board, b'(kicad_sch (version 1))', 'subject'),
                                         (self.board, b'\xff\xfe', 'subject')):
            with self.subTest(side=side), self.assertRaisesRegex(Malformed, f'^{side}: '):
                compare(reference, subject, 'board', 2)
        with self.assertRaisesRegex(Malformed, '^subject: not valid XML'):
            compare(netlist_xml('d'), b'<export version="E"><nets>', 'netlist')
        with self.assertRaisesRegex(Malformed, '^subject: XML is not UTF-8'):
            compare(netlist_xml('d'), b'<export version="E">\xff</export>', 'netlist')
        with self.assertRaisesRegex(Malformed, '^reference: not valid JSON'):
            compare(b'{', b'{}', 'json')

    def test_parameters_are_strict(self):
        board = {'path': 'b.kicad_pcb'}
        good = {'format': 'board', 'decimals': 2, 'reference': {'path': 'ref/b.kicad_pcb'}, 'subject': board}
        self.assertEqual(parameters(good)[:2], ('board', 2))
        for bad in ({**good, 'format': 'gerber'}, {k: v for k, v in good.items() if k != 'decimals'},
                    {**good, 'decimals': True}, {**good, 'decimals': 7}, {**good, 'decimals': '2'},
                    {**good, 'format': 'netlist'}, {**good, 'reference': board},
                    {**good, 'reference': {'path': '../b'}}, {**good, 'reference': {'check': 'n'}},
                    {**good, 'subject': {'check': 'n', 'artifact': '/abs'}}, {**good, 'extra': 1}):
            with self.subTest(params=bad), self.assertRaises(ValueError):
                parameters(bad)

    def test_registration(self):
        self.assertIn('regeneration_determinism', KINDS)
        self.assertIs(CHECK_FUNCS['regeneration_determinism'], check_regeneration_determinism)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, bad = Path(tmp) / 'a.kicad_pcb', Path(tmp) / 'b.kicad_pcb', Path(tmp) / 'bad.kicad_pcb'
            a.write_bytes(self.board)
            b.write_bytes(churn(self.board))
            bad.write_bytes(b'(kicad_pcb')
            out = Path(tmp) / 'diff.json'
            self.assertEqual(main([str(a), str(b), '--format', 'board', '--decimals', '2', '--out', str(out)]), 0)
            self.assertTrue(read_json(out)['equivalent'])
            b.write_bytes(self.board.replace(b'(at 105 100)', b'(at 106 100)', 1))
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main([str(a), str(b), '--format', 'board', '--decimals', '2']), 1)
            self.assertEqual(len(json.loads(stdout.getvalue())['entries']), 1)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(a), str(bad), '--format', 'board', '--decimals', '2']), 2)
                self.assertEqual(main([str(a), str(b), '--format', 'board']), 2)

    def test_writer_mutable_reference_or_forward_producer_is_unsupported(self):
        files = {'project/rc_filter.kicad_pcb': {}, 'committed/rc_filter.kicad_pcb': {}}
        scope = {'board': 'project/rc_filter.kicad_pcb', 'schematic': 'project/rc_filter.kicad_sch',
                 'checks': []}
        mutable = {'id': 'regen', 'kind': 'regeneration_determinism',
                   'params': {'format': 'board', 'decimals': 2, 'reference': {'path': 'project/rc_filter.kicad_pcb'},
                              'subject': {'path': 'committed/rc_filter.kicad_pcb'}}}
        forward = {'id': 'regen', 'kind': 'regeneration_determinism',
                   'params': {'format': 'netlist', 'reference': {'path': 'committed/rc_filter.kicad_pcb'},
                              'subject': {'check': 'netlist', 'artifact': 'design.xml'}}}
        uncaptured = {'id': 'regen', 'kind': 'regeneration_determinism',
                      'params': {'format': 'board', 'decimals': 2, 'reference': {'path': 'missing.kicad_pcb'},
                                 'subject': {'path': 'project/rc_filter.kicad_pcb'}}}
        for spec, message in ((mutable, 'writer-mutable'), (forward, 'prerequisite'), (uncaptured, 'not captured')):
            checks = [spec, {'id': 'netlist', 'kind': 'netlist'}]
            described = describe_check(spec, check_specs=checks, files=files, scope={**scope, 'checks': checks})
            with self.subTest(message=message):
                self.assertEqual(described['status'], 'unsupported', described)
                self.assertIn(message, described['unsupported_reasons'][0]['message'])



class NativeDesignDiffTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sandboxed(self, name, files, argv):
        """Run kicad-cli on copies under /work; returns the work directory."""
        inputs, work = self.root / name / 'inputs', self.root / name / 'work'
        inputs.mkdir(parents=True)
        work.mkdir(parents=True)
        for rel, data in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(data)
        record = executor.execute(inputs, work, argv, timeout=120)
        self.assertEqual(record['execution'], 'ok', (work / 'console.log').read_text(errors='replace'))
        return work

    def resave(self, name, data):
        work = self.sandboxed(name, {'board.kicad_pcb': data},
                              ['kicad-cli', 'pcb', 'upgrade', '--force', '/work/board.kicad_pcb'])
        return (work / 'board.kicad_pcb').read_bytes()

    def export(self, name, fmt):
        files = {f'project/{p.name}': p.read_bytes() for p in RC.iterdir() if p.is_file()}
        work = self.sandboxed(name, files, ['kicad-cli', 'sch', 'export', 'netlist', '--format', fmt,
                                            '-o', '/work/design.net', '/work/project/rc_filter.kicad_sch'])
        return (work / 'design.net').read_bytes()

    def test_kicad_resave_after_uuid_churn_is_equivalent(self):
        original = self.resave('a', PAIR.read_bytes())
        churned = self.resave('b', churn(PAIR.read_bytes()))
        self.assertNotEqual(original, churned)
        report = compare(original, churned, 'board', 2)
        self.assertEqual((report['equivalent'], report['hashes']['changed']), (True, []), report['entries'])
        # KiCad 10 writes top-level footprints in UUID order, so churn usually reorders them.
        rc = self.resave('c', (RC / 'rc_filter.kicad_pcb').read_bytes())
        rc_churned = self.resave('d', churn((RC / 'rc_filter.kicad_pcb').read_bytes()))
        order = [re.findall(rb'\(property "Reference" "(\w+)"', data) for data in (rc, rc_churned)]
        report = compare(rc, rc_churned, 'board', 2)
        self.assertTrue(report['equivalent'], report['entries'])
        self.assertEqual(sorted(order[0]), sorted(order[1]))

    def test_two_netlist_exports_one_second_apart_share_identity(self):
        for fmt in ('kicadxml', 'kicadsexpr'):
            first = self.export(f'{fmt}-1', fmt)
            time.sleep(1.1)
            second = self.export(f'{fmt}-2', fmt)
            with self.subTest(fmt=fmt):
                self.assertNotEqual(first, second)
                report = compare(first, second, 'netlist')
                self.assertTrue(report['equivalent'], report['entries'])
                self.assertEqual(report['hashes']['reference'], report['hashes']['subject'])
                self.assertNotEqual(report['headers']['reference']['date'], report['headers']['subject']['date'])

    def scope(self):
        project = self.root / 'source/project'
        shutil.copytree(RC, project)
        committed = self.root / 'source/committed'
        committed.mkdir()
        (committed / 'rc_filter.kicad_pcb').write_bytes(
            self.resave('committed', churn((RC / 'rc_filter.kicad_pcb').read_bytes())))
        golden = self.root / 'source/golden'
        golden.mkdir()
        (golden / 'design.xml').write_bytes(self.export('golden', 'kicadxml'))
        time.sleep(1.1)
        (self.root / 'source/requirements.md').write_text('Regeneration: the board and netlist reproduce '
                                                          'the committed design.\n')
        template = read_json(REPO / 'fixtures/rc/control.scope.json')
        stock = [item for item in template['inputs'] if item['path'].startswith('/usr/share/kicad')]
        source = {'path': 'requirements.md', 'locator': 'Regeneration'}
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'project/rc_filter.kicad_sch',
                 'board': 'project/rc_filter.kicad_pcb',
                 'inputs': [{'path': str(project), 'at': 'project'}, {'path': str(committed), 'at': 'committed'},
                            {'path': str(golden), 'at': 'golden'},
                            {'path': str(self.root / 'source/requirements.md'), 'at': 'requirements.md'}] + stock,
                 'requirements': ['requirements.md'],
                 'checks': [{'id': 'netlist', 'kind': 'netlist'},
                            {'id': 'board-regen', 'kind': 'regeneration_determinism',
                             'params': {'format': 'board', 'decimals': 2,
                                        'reference': {'path': 'committed/rc_filter.kicad_pcb'},
                                        'subject': {'path': 'project/rc_filter.kicad_pcb'}}},
                            {'id': 'netlist-regen', 'kind': 'regeneration_determinism',
                             'params': {'format': 'netlist', 'reference': {'path': 'golden/design.xml'},
                                        'subject': {'check': 'netlist', 'artifact': 'design.xml'}}}],
                 'baseline': {'requirements': [{'id': 'regen', 'statement': 'Regeneration is reproducible',
                                                'source': source, 'checks': ['netlist', 'board-regen',
                                                                             'netlist-regen'],
                                                'intent': 'acceptance'}],
                              'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        return run

    def rerun(self, run, index):
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][index], time.monotonic() + 30)
        try:
            check_regeneration_determinism(ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return ctx.result

    def test_check_pipeline_capability_baseline_and_fail_closed_inputs(self):
        run = self.scope()
        checks = run_checks(run, time.monotonic() + 300)
        self.assertEqual(checks[0]['execution'], 'ok', checks[0])
        board, netlist = checks[1], checks[2]
        self.assertEqual((board['execution'], board['outcome']), ('ok', 'pass'), board)
        self.assertFalse(board['details']['raw']['identical'])
        self.assertIn('byte differences recorded, not graded', board['summary'])
        self.assertEqual((netlist['execution'], netlist['outcome']), ('ok', 'pass'), netlist)
        self.assertNotEqual(*(netlist['details']['headers'][side]['date'] for side in ('reference', 'subject')))
        self.assertEqual(netlist['details']['subject'], 'work/netlist/design.xml')
        self.assertIn('work/netlist/design.xml', netlist['evidence'])
        self.assertIn('work/netlist-regen/design_diff.json', netlist['evidence'])
        manifest = read_json(run / 'candidate.json')
        scope = manifest['scope']
        described = [describe_check(spec, check_specs=scope['checks'], files=manifest['files'], scope=scope,
                                    inputs=run / 'inputs') for spec in scope['checks'][1:]]
        self.assertEqual([d['status'] for d in described], ['supported', 'supported'], described)
        self.assertEqual(described[0]['immutable_inputs'], ['committed/rc_filter.kicad_pcb'])
        self.assertEqual(described[1]['prerequisites'], [{'check_id': 'netlist', 'kind': 'netlist'}])
        self.assertEqual(described[1]['predicates'], ['design-diff:netlist:equivalent'])
        baseline = load_baseline(run)
        self.assertIn('committed/rc_filter.kicad_pcb', baseline['files'])
        self.assertIn('golden/design.xml', baseline['files'])
        self.assertNotIn('project/rc_filter.kicad_pcb', baseline['files'])
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)

        candidate = run / 'inputs/project/rc_filter.kicad_pcb'
        raw = candidate.read_bytes()
        candidate.write_bytes(raw.replace(b'(at 110 100)', b'(at 110.1 100)', 1))
        result = self.rerun(run, 1)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual([e['path'] for e in result['details']['entries']], ['footprint[C1]/at'])
        self.assertEqual(result['details']['hashes']['changed'], ['placement'])
        candidate.write_bytes(raw[:len(raw) // 2])
        result = self.rerun(run, 1)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.assertIn('subject:', result['summary'])
        candidate.unlink()
        self.assertEqual(self.rerun(run, 1)['execution'], 'malformed')
        candidate.write_bytes(raw)
        self.assertEqual(self.rerun(run, 1)['outcome'], 'pass')

        # A missing or unverified probe output is unknown, never "no change".
        xml = run / 'work/netlist/design.xml'
        exported = xml.read_bytes()
        xml.unlink()
        result = self.rerun(run, 2)
        self.assertEqual((result['execution'], result['outcome']), ('not_run', 'unknown'), result)
        xml.write_bytes(exported)
        producer = run / 'checks/netlist.json'
        record = read_json(producer)
        write_json(producer, {**record, 'execution': 'failed'})
        result = self.rerun(run, 2)
        self.assertEqual((result['execution'], result['outcome']), ('not_run', 'unknown'), result)
        producer.unlink()
        result = self.rerun(run, 2)
        self.assertEqual((result['execution'], result['outcome']), ('not_run', 'unknown'), result)
        write_json(producer, record)
        xml.write_bytes(b'<export version="E"><design>')
        result = self.rerun(run, 2)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        xml.write_bytes(exported)
        self.assertEqual(self.rerun(run, 2)['outcome'], 'pass')


if __name__ == '__main__':
    unittest.main()
