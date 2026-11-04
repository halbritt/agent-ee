"""Each chosen footprint, alone, under the board's active fabrication rules (tool defect #32).

Design capture runs this right after it retains the footprints and before any download.
For every part, a scratch board holds only that part's footprint, with the reference,
value and pad nets that the built board gives it. Beside it are the rule files that
compile emits for this board (design_compile.envelope), so every setting they leave out
takes KiCad's own default, exactly as on the built board. Native DRC then runs with the
kicad-cli command of the placement job's input DRC, and its report is graded as that job
grades the final board: a finding of error severity whose items are the footprint's own.
Such a finding fails final verification as a pre-existing error whatever the placement, so
capture refuses it under ``parts.<ref>.footprint`` with the rule, the measured value and the
limit. Warnings, and anything the board's own rule files allow, are not refused.
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import native_run
from .design import DesignError, footprint_path
from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.footprint-rules/1'
REQUEST_SCHEMA = 'agent-ee.footprint-rules-request/1'
BUILD_TIMEOUT_S = 180
DRC_TIMEOUT_S = 120
# Findings listed in one refusal line; footprint-rules/report.json keeps every finding.
LISTED = 6
# KiCad ends a numeric finding with "(<rule source> <limit> mm; actual <value> mm)".
MEASURED = re.compile(r'\(([^()]*?) (-?[0-9]+(?:\.[0-9]+)?) mm; actual (-?[0-9]+(?:\.[0-9]+)?) mm\)$')


def rule_files(design):
    """The rule files compile emits for this board, by file name, and compile's derivation record."""
    from .design_compile import envelope

    with tempfile.TemporaryDirectory(prefix='agent-ee-footprint-rules-') as tmp:
        root = Path(tmp)
        envelope(design, root)
        files = {p.name: p.read_bytes() for p in sorted((root / 'project').iterdir())}
        derivation = read_json(root / 'project-rule-derivation.json')
    if [Path(name).suffix for name in files] not in (['.kicad_pro'], ['.kicad_dru', '.kicad_pro']):
        raise ValueError('compiled board rules must be one .kicad_pro and at most one .kicad_dru')
    return files, derivation


def part_nets(design, ref):
    """Pad number -> net name as forward annotation assigns it; other pads keep no net."""
    from .design_compile import native_net

    nets = {}
    for net, terminals in design['nets'].items():
        for terminal in terminals:
            owner, pin = terminal.split('.', 1)
            if owner == ref:
                nets[pin] = native_net(design, net)
    for terminal in design['no_connects']:
        owner, pin = terminal.split('.', 1)
        if owner == ref:
            # One net per unconnected pin, like KiCad's unconnected-(...) nets.
            nets[pin] = f'unconnected-({ref}-Pad{pin})'
    return dict(sorted(nets.items()))


def _source(finding, files, derivation):
    """Where the limit KiCad applied comes from: the setting and its origin, in words."""
    from .design_rule_provenance import NUMERIC_FIELDS

    rule = finding['rule']
    if rule is None:
        return None
    custom = re.match(r"rule '(.*?)'", rule)
    if custom:
        return f"custom rule '{custom[1]}' in the board's .kicad_dru"
    netclass = re.match(r"netclass '(.*?)'", rule)
    if netclass:
        return f"net class '{netclass[1]}' of the board's .kicad_pro"
    if not rule.startswith('board setup constraints'):
        return rule
    project = json.loads(next(raw for name, raw in files.items() if name.endswith('.kicad_pro')))
    rules = project.get('board', {}).get('design_settings', {}).get('rules', {})
    enforced = {item['setting']: item for item in derivation.get('enforced', [])}
    words = []
    for field in NUMERIC_FIELDS.get(finding['type'], ()):
        setting = 'board.design_settings.rules.' + field
        if setting in enforced:
            words.append(f"{setting} from fab profile limit '{enforced[setting]['limit_id']}' "
                         f"({enforced[setting]['key']})")
        elif field in rules:
            words.append(f'{setting}, set by the compiled project')
        elif setting.startswith('board.'):
            words.append(f"{setting}, KiCad's default: the compiled project and the fab profile do not set it")
    return '; '.join(words) or rule


def _finding(violation, files, derivation):
    measured = MEASURED.search(violation.get('description', ''))
    finding = dict(type=violation.get('type'), description=violation.get('description'),
                   items=[item.get('description') for item in violation.get('items', [])],
                   uuids=[item.get('uuid') for item in violation.get('items', [])],
                   rule=measured[1] if measured else None,
                   limit_mm=measured[2] if measured else None,
                   actual_mm=measured[3] if measured else None)
    finding['source'] = _source(finding, files, derivation)
    return finding


def _words(finding):
    text = finding['type'] + ': ' + ' and '.join(finding['items'])
    if finding['actual_mm'] is not None:
        return (f"{text} measure {finding['actual_mm']} mm against the {finding['limit_mm']} mm limit "
                f"of {finding['rule']} ({finding['source']})")
    return text + ': ' + finding['description']


def refusal(part, findings):
    """One actionable line per part, under the design field the author changes."""
    listed = '; '.join(_words(f) for f in findings[:LISTED])
    more = f'; and {len(findings) - LISTED} more' if len(findings) > LISTED else ''
    count = f"{len(findings)} native DRC error{'s' if len(findings) != 1 else ''}"
    return (f"parts.{part['reference']}.footprint: {part['library']}:{part['footprint']} breaks this "
            f"board's fabrication rules on its own, before placement or routing: {count} inside the "
            f"footprint, which final verification fails as pre-existing whatever the placement. "
            f"{listed}{more}. Choose another footprint for {part['reference']} whose holes and pads "
            "meet these rules, for example one whose holes are farther from its pads; "
            "footprint-rules/report.json in the capture directory lists every finding")


def check(design, cad, out):
    """Check every part's footprint alone; write out/report.json; refuse per part.

    cad is the capture's retained CAD directory; out must not exist. Returns the report.
    A native run that does not complete refuses under captures.footprint-rules, never passes.
    """
    out, cad = Path(out), Path(cad)
    inputs = out / 'inputs'
    inputs.mkdir(parents=True)
    files, derivation = rule_files(design)
    for name, raw in files.items():
        (inputs / name).write_bytes(raw)
    parts = []
    for ref, part in sorted(design['parts'].items()):
        rel = footprint_path(part)
        (inputs / rel).parent.mkdir(exist_ok=True)
        shutil.copyfile(cad / rel, inputs / rel)
        library, name = part['footprint'].split(':')
        parts.append(dict(reference=ref, value=part['value'], library=library, footprint=name,
                          footprint_sha256=digest(inputs / rel), nets=part_nets(design, ref)))
    write_json(inputs / 'request.json', dict(schema=REQUEST_SCHEMA, rule_files=sorted(files), parts=parts))

    def unavailable(what, record, work):
        console = work / 'console.log'
        tail = console.read_text(errors='replace')[-600:].strip() if console.is_file() else ''
        return DesignError([f'captures.footprint-rules: the native footprint rule check did not complete '
                            f"({what}: {record.get('execution')}); inspect footprint-rules/ in the capture "
                            f'directory and run design capture again into a new directory. {tail}'.strip()])

    build = out / 'build'
    build.mkdir()
    record = native_run.run(native_run.Intent('pcbnew_script', native_run.python_module_argv(
        'agent_ee.footprint_rules', '/candidate/request.json', '/work/boards'), timeout=BUILD_TIMEOUT_S),
        inputs=inputs, work=build)
    if record['execution'] != 'ok':
        raise unavailable('scratch boards', record, build)
    results, refused = [], []
    for part in parts:
        board = build / 'boards' / part['reference']
        work = out / 'drc' / part['reference']
        work.mkdir(parents=True)
        record = native_run.run(native_run.Intent('drc', native_run.drc_argv(
            '/candidate/board.kicad_pcb', '/work/native-drc.json', output_flag='--output'),
            timeout=DRC_TIMEOUT_S), inputs=board, work=work)
        report = work / 'native-drc.json'
        if record['execution'] != 'ok' or not report.is_file():
            raise unavailable('native DRC of ' + part['reference'], record, work)
        data = read_json(report)
        owners = read_json(board / 'owners.json')
        if (not isinstance(data.get('violations'), list) or not isinstance(owners.get('items'), list)
                or any(not isinstance(v, dict) for v in data['violations'])):
            raise unavailable('native DRC report of ' + part['reference'], dict(execution='malformed'), work)
        # Final verification's pre-existing rule: every item of the error is the footprint's own.
        # Errors of the scratch board itself (it has no outline) are kept as other_errors.
        owned = set(owners['items'])
        errors = [v for v in data['violations'] if v.get('severity') == 'error']
        mine = [v for v in errors if v.get('items') and all(i.get('uuid') in owned for i in v['items'])]
        own = [_finding(v, files, derivation) for v in mine]
        results.append(dict(reference=part['reference'], footprint=f"{part['library']}:{part['footprint']}",
                            footprint_sha256=part['footprint_sha256'], board_sha256=digest(board / 'board.kicad_pcb'),
                            kicad_version=data.get('kicad_version'), status='refused' if own else 'pass',
                            errors=own, other_errors=[v for v in errors if v not in mine],
                            warnings=sorted({v.get('type') for v in data['violations']
                                             if v.get('severity') == 'warning'})))
        if own:
            refused.append(refusal(part, own))
    result = dict(schema=SCHEMA, rule_files={name: digest(inputs / name) for name in files},
                  grading='native DRC findings of error severity naming the footprint\'s items; '
                          'warnings and unconnected items are not graded',
                  status='refused' if refused else 'pass', parts=results)
    write_json(out / 'report.json', result)
    if refused:
        raise DesignError(refused)
    return result


def _build(request, boards):
    """Sandbox child: one saved scratch board per part, with the compiled rule files beside it."""
    import pcbnew

    request = Path(request)
    document = json.loads(request.read_bytes())
    if document.get('schema') != REQUEST_SCHEMA:
        raise ValueError('expected ' + REQUEST_SCHEMA)
    for part in document['parts']:
        directory = Path(boards) / part['reference']
        directory.mkdir(parents=True)
        footprint = pcbnew.FootprintLoad(str(request.parent / (part['library'] + '.pretty')), part['footprint'])
        if footprint is None:
            raise ValueError(part['reference'] + ': captured footprint did not load natively')
        board = pcbnew.BOARD()
        footprint.SetReference(part['reference'])
        footprint.SetValue(part['value'])
        board.Add(footprint)
        nets = {}
        for pad in footprint.Pads():
            name = part['nets'].get(pad.GetNumber())
            if name is None:
                continue
            if name not in nets:
                nets[name] = pcbnew.NETINFO_ITEM(board, name)
                board.Add(nets[name])
            pad.SetNet(nets[name])
        target = directory / 'board.kicad_pcb'
        if not pcbnew.SaveBoard(str(target), board):
            raise ValueError(part['reference'] + ': scratch board SaveBoard failed')
        # SaveBoard wrote its own project; the board is judged under the compiled rule files.
        for name in document['rule_files']:
            target.with_suffix(Path(name).suffix).write_bytes((request.parent / name).read_bytes())
        saved = pcbnew.LoadBoard(str(target))
        items = set()
        for fp in saved.GetFootprints():
            items.add(fp.m_Uuid.AsString())
            for item in [*fp.Pads(), *fp.GraphicalItems(), *fp.GetFields(), *fp.Zones()]:
                items.add(item.m_Uuid.AsString())
        (directory / 'owners.json').write_text(json.dumps(
            dict(pcbnew_version=pcbnew.Version(), items=sorted(items)), sort_keys=True), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: python3 -m agent_ee.footprint_rules REQUEST BOARDS')
    _build(sys.argv[1], sys.argv[2])
