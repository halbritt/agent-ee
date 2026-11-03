"""A finite, retained requirements -> statement -> typed file release loop."""

import contextlib
import copy
import datetime
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import design, executor, harnesses, native_run, review_run
from .spec_record import DELIMITER
from .storage import digest, write_json

SCHEMA = 'agent-ee.design-generate/1'
BENCHMARK_SCHEMA = 'agent-ee.design-benchmark/1'
RETRY_SCHEMA = 'agent-ee.design-generate-retry/1'
DEFAULT_AUTHOR = 'codex:gpt-6.1-sol:high'
DEFAULTS = dict(author=DEFAULT_AUTHOR, max_attempts=4, max_seconds=2400, author_seconds=600,
                search_retries=1, review_profile=None, context_root=None, kicad_root=Path('/usr/share/kicad'))
# Prompt bounds. A larger requirements file is refused. The current statement and the previous
# attempt's feedback are echoed up to these lengths, marked where cut.
MAX_REQUIREMENTS_BYTES = 32768
STATEMENT_ECHO_CHARS = 20000
FEEDBACK_CHARS = 14000
# Pure-provenance fields (where a limit, rule or record came from, not what failed). Author feedback
# omits them so that the violations fit the excerpt; retained job and attempt reports keep them.
FEEDBACK_OMITTED_FIELDS = frozenset({'declaration', 'source_records', 'manufacturing_floor'})
# What each placement-engine violation kind means (crates/placement-engine/src/job.rs violations()).
VIOLATION_TERMS = dict(
    pair='courtyard, copper, hole or mask overlap between the two parts',
    electrical_locality='a decoupling or locality pad distance is over its limit',
    edge_identity='the declared edge is not the nearest board edge',
    edge_setback='farther from its declared edge than the allowed setback',
    edge_overhang_min='overhangs its declared edge less than required',
    occupancy_outside_outline='body outside the board outline',
    copper_edge_clearance='copper closer to the board edge than the edge clearance',
    source_body_outside='declared package body outside the board outline',
    source_body_gap='declared package bodies closer than the minimum gap',
    connector_access='parts in a connector mating access region')
# Phase logs quoted in reasons and feedback: the last bytes of each log only.
LOG_TAIL_BYTES = 6000
REVIEW_EXCERPT_CHARS = 1500
# The author seat's console (stdout and stderr); past it the turn stops as output_limit.
AUTHOR_CONSOLE_BYTES = 32 * 1024 * 1024
# A trace quote holds at least this many whole words, or a whole shorter requirement item.
MIN_QUOTE_WORDS = 3
# design.validate's limit and design_compile's default for placement.seeds.
MAX_SEEDS = 64
DEFAULT_SEEDS = 4
# A stopped or timed-out phase's process group gets SIGTERM, then SIGKILL after this many seconds.
PHASE_STOP_GRACE_S = 5
# Where the author seat sees a KiCad root that is outside the host /usr it already shows.
SEAT_KICAD_ROOT = '/kicad'
ROUTER_PIN = Path(__file__).with_name('data') / 'kicad-route-pin.json'

ITEM_RULE = ('A requirement item is one sentence of REQUIREMENTS.md, or one row of a Markdown table; a '
             'sentence never spans a heading, paragraph, list item or table row. Headings and table header '
             'rows are titles, not items.')
QUOTE_RULE = (f'At least {MIN_QUOTE_WORDS} whole words copied exactly from inside one requirement item, or '
              'the whole item when it is shorter. Line breaks and spacing may differ. A quote whose words '
              'occur in more than one item covers none of them unless item names its item.')
ITEM_NUMBER_RULE = ('The number of the requirement item the quote is taken from, as numbered in the prompt. '
                    'Needed when the quoted words occur in more than one item; otherwise null.')
# Null is valid wherever the item number may be absent: in the schema each harness receives
# (Codex gets strict_schema() of it, Agy gets it as written) and in validate_response.
ITEM_SCHEMA = dict(type=['integer', 'null'], description=ITEM_NUMBER_RULE)
AUTHOR_SCHEMA = dict(type='object', additionalProperties=False,
    required=['status', 'reason', 'statement_json', 'trace', 'untraced', 'agent_selected'], properties=dict(
        status=dict(type='string', enum=['ready', 'unsupported']),
        reason=dict(type='string'), statement_json=dict(type='string'),
        trace=dict(type='array', description='Requirement quotations, each with the design field that carries '
            'it. Intent, every functional[i].text and every requirement item not listed in untraced need one. '
            + ITEM_RULE, items=dict(type='object', additionalProperties=False,
            required=['field', 'quote'], properties=dict(
                field=dict(type='string', description='A design field path such as intent, functional[0].text, '
                    'board.width_mm or bench[1]; never a character position inside a text value.'),
                quote=dict(type='string', description=QUOTE_RULE),
                item=ITEM_SCHEMA))),
        untraced=dict(type='array', description='Each requirement item that no trace quotes, with why the '
            'design does not carry it.', items=dict(type='object', additionalProperties=False,
            required=['quote', 'status', 'reason'], properties=dict(
                quote=dict(type='string', description=QUOTE_RULE),
                item=ITEM_SCHEMA,
                status=dict(type='string', enum=['unsupported', 'not_applicable']),
                reason=dict(type='string')))),
        agent_selected=dict(type='array', items=dict(type='string'))))


def author_definition(text):
    fields = text.split(':') if isinstance(text, str) else []
    if len(fields) != 3 or fields[0] not in ('codex', 'agy') or not fields[1] or fields[2] not in ('low', 'medium', 'high', 'xhigh'):
        raise ValueError('author: use codex:MODEL:EFFORT or agy:MODEL:EFFORT (subscription only)')
    return dict(harness=fields[0], model=fields[1], effort=fields[2], tool_mode='investigate')


def configure_parser(sub):
    for command in ('generate', 'benchmark'):
        p = sub.add_parser(command, help='bounded requirements-to-board ' + command)
        p.add_argument('requirements', type=Path)
        p.add_argument('--out', required=True, type=Path)
        p.add_argument('--author', default=DEFAULTS['author'], help='subscription seat HARNESS:MODEL:EFFORT')
        p.add_argument('--max-attempts', type=int, default=DEFAULTS['max_attempts'], help='per design, 1 to 100')
        p.add_argument('--max-seconds', type=int, default=DEFAULTS['max_seconds'],
                       help='per design, shared by all phases and retries; 1 to 86400')
        p.add_argument('--author-seconds', type=int, default=DEFAULTS['author_seconds'],
                       help='limit of one author turn, 1 to 86400')
        p.add_argument('--search-retries', type=int, default=DEFAULTS['search_retries'],
                       help='automatic extra-seed attempts before another author turn, 0 to 8')
        p.add_argument('--review-profile', type=Path, help='optional full engineering review, charged to the same bounds')
        p.add_argument('--context-root', type=Path, default=Path(__file__).resolve().parents[2],
                       help='checkout containing docs/design-compile.md and fixtures/design-examples')
        p.add_argument('--kicad-root', type=Path, default=DEFAULTS['kicad_root'])


def resolve_options(options):
    """Every option with its default, validated before any output exists."""
    unknown = sorted(set(options) - set(DEFAULTS))
    if unknown:
        raise TypeError('unknown design generate options: ' + ', '.join(unknown))
    options = {**DEFAULTS, **options}
    author_definition(options['author'])
    for key, maximum in (('max_attempts', 100), ('max_seconds', 86400), ('author_seconds', 86400)):
        value = options[key]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{key}: must be an integer from 1 to {maximum}')
    search_retries = options['search_retries']
    if type(search_retries) is not int or not 0 <= search_retries <= 8:
        raise ValueError('search_retries: must be an integer from 0 to 8')
    if options['review_profile'] is not None:
        options['review_profile'] = Path(options['review_profile']).resolve()
        if not options['review_profile'].is_file():
            raise ValueError('review_profile: supply an existing profile')
    options['kicad_root'] = Path(options['kicad_root'])
    if seat_kicad_root(options['kicad_root'])[1] and not options['kicad_root'].is_dir():
        raise ValueError('kicad_root: supply an existing KiCad library directory')
    return options


def seat_kicad_root(kicad_root):
    """The KiCad root as the author seat sees it, and the read-only bind that needs.

    The seat sandbox shows the host /usr; a root elsewhere is bound read-only at /kicad."""
    resolved = Path(kicad_root).resolve()
    if resolved.is_relative_to('/usr'):
        return str(resolved), ()
    return SEAT_KICAD_ROOT, ((resolved, SEAT_KICAD_ROOT),)


def entries(rows, keys):
    """A list of objects with string keys and an optional item number (null when absent)."""
    return isinstance(rows, list) and all(isinstance(t, dict) and keys <= set(t) <= keys | {'item'}
        and all(isinstance(t[k], str) for k in keys)
        and (t.get('item') is None or (type(t['item']) is int and t['item'] >= 1)) for t in rows)


def validate_response(value):
    if not isinstance(value, dict) or set(value) != set(AUTHOR_SCHEMA['properties']):
        raise ValueError('author response: supply all documented envelope fields')
    if value['status'] not in ('ready', 'unsupported'):
        raise ValueError('author response.status: use ready or unsupported')
    if any(not isinstance(value[k], str) for k in ('reason', 'statement_json')):
        raise ValueError('author response: reason and statement_json must be strings')
    if not entries(value['trace'], {'field', 'quote'}):
        raise ValueError('author response.trace: supply field/quote objects (item: null or a positive integer)')
    if not entries(value['untraced'], {'quote', 'status', 'reason'}):
        raise ValueError('author response.untraced: supply quote/status/reason objects (item: null or a positive integer)')
    if any(t['status'] not in ('unsupported', 'not_applicable') or not t['reason'].strip() for t in value['untraced']):
        raise ValueError('author response.untraced: use status unsupported or not_applicable, with a reason')
    if not isinstance(value['agent_selected'], list) or any(not isinstance(t, str) for t in value['agent_selected']):
        raise ValueError('author response.agent_selected: supply strings')
    if value['status'] == 'unsupported' and not value['reason'].strip():
        raise ValueError('author response.reason: explain the unsupported requirement')
    return value


_WORD = re.compile(r'\w+')
_LIST_MARKER = re.compile(r'(?:[-*+]|\d{1,9}[.)])\s+')
_SENTENCE_BREAK = re.compile(r'[.!?]["\')\]]*( )(?=[^a-z])')


def normalize(text):
    return ' '.join(text.split())


def split_sentences(text):
    """Sentences of whitespace-normalized text: each ends at . ! or ? (and closing quotes or
    brackets) followed by a space and a character that is not a lowercase letter."""
    parts, start = [], 0
    for match in _SENTENCE_BREAK.finditer(text):
        parts.append(text[start:match.start(1)])
        start = match.end(1)
    parts.append(text[start:])
    return [part for part in parts if _WORD.search(part)]


def is_delimiter(line):
    """A table delimiter row by the spec parser's rule (spec_record.DELIMITER): cells of dashes
    with optional colons, at least one pipe, outer pipes optional."""
    return bool(DELIMITER.fullmatch(line.encode()))


def requirement_items(text):
    """[(text, required)] for the requirement text.

    Every sentence of every paragraph and list item is a required item; a sentence never
    spans a blank line, heading, list item or table row. A table is a line followed by a
    delimiter row (outer pipes optional, as spec_record reads them), or lines that start with
    a pipe; it runs to a blank line, heading or list item, and each row is one required item
    without its outer pipes. Headings, and the header rows above a table's delimiter row, are
    titles: they may be quoted but need no trace and cover no other item."""
    items, block, table = [], [], []

    def flush():
        items.extend((sentence, True) for sentence in split_sentences(normalize(' '.join(block))))
        block.clear()
        rules = [n for n, row in enumerate(table) if is_delimiter(row)]
        for n, row in enumerate(table):
            if n not in rules and _WORD.search(row):
                items.append((normalize(row.strip('|')), not rules or n > rules[0]))
        table.clear()

    for line in text.splitlines():
        stripped = line.strip()
        marker = _LIST_MARKER.match(stripped)
        ends_block = not stripped or stripped.startswith('#') or bool(marker)
        if table and not ends_block:
            table.append(stripped)
            continue
        if block and not ends_block and is_delimiter(stripped):
            # The line above a delimiter row is the table's header, even without outer pipes.
            header = block.pop()
            flush()
            table.extend((header, stripped))
            continue
        if ends_block or stripped.startswith('|'):
            flush()
        if stripped.startswith('#'):
            items.extend((sentence, False) for sentence in split_sentences(normalize(stripped.lstrip('#'))))
        elif stripped.startswith('|'):
            table.append(stripped)
        elif marker:
            block.append(stripped[marker.end():])
        elif stripped:
            block.append(stripped)
    flush()
    return items


def excerpt(text, limit=80):
    return text if len(text) <= limit else text[:limit - 3] + '...'


def quote_items(quote, items, whole, label, number=None):
    """(problem or None, the index of the required item this quote covers, if any).

    A quote covers a required item only when it lies inside exactly one required item, or
    when number names the item it is taken from: a shared quote never covers two items."""
    text = normalize(quote)
    if not text or text not in whole:
        return f'{label} must be an exact nonempty span of REQUIREMENTS.md', set()
    found = re.compile(r'(?<!\w)' + re.escape(text) + r'(?!\w)')
    within = [index for index, (item, _) in enumerate(items) if found.search(item)]
    if not within:
        return (f'{label} must start and end on word boundaries inside one requirement item '
                '(a sentence, list item or table row)'), set()
    words = len(_WORD.findall(text))
    fits = [index for index in within if words >= min(MIN_QUOTE_WORDS, len(_WORD.findall(items[index][0])))]
    if not fits:
        return f'{label} must hold at least {MIN_QUOTE_WORDS} whole words, or a whole shorter requirement item', set()
    numbers = [n for n, (item, required) in enumerate(items) if required]
    if number is not None:
        if not 1 <= number <= len(numbers) or numbers[number - 1] not in fits:
            return f'{label} must lie inside requirement item {number}, the item it names', set()
        return None, {numbers[number - 1]}
    shared = [numbers.index(index) + 1 for index in fits if items[index][1]]
    if len(shared) > 1:
        return (f'{label} occurs in requirement items {", ".join(map(str, shared))}; quote words that occur in '
                'one item only, or name the item in item'), set()
    return None, {numbers[shared[0] - 1]} if shared else set()


def trace_problems(response, statement, requirements):
    items = requirement_items(requirements)
    whole = normalize(requirements)
    needed = {'intent'} | {f'functional[{i}].text' for i in range(len(statement['functional']))}
    seen, covered, problems = set(), set(), []
    for row in response['trace']:
        if not trace_field_exists(statement, row['field']):
            problems.append('trace.' + row['field'] + ': cite an existing design field, never a position inside a text value')
            continue
        problem, quoted = quote_items(row['quote'], items, whole, 'trace.quote', row.get('item'))
        if problem:
            problems.append(row['field'] + ': ' + problem)
        else:
            seen.add(row['field'])
            covered |= quoted
    for index, row in enumerate(response['untraced']):
        problem, quoted = quote_items(row['quote'], items, whole, 'untraced.quote', row.get('item'))
        if problem:
            problems.append(f'untraced[{index}]: ' + problem)
        else:
            covered |= quoted
    problems += [field + ': supply an exact requirement quote in trace' for field in sorted(needed - seen)]
    number = 0
    for index, (item, required) in enumerate(items):
        if required:
            number += 1
            if index not in covered:
                problems.append(f'requirements: item {number} ("{excerpt(item)}") has no trace quote and no untraced entry')
    if not response['agent_selected'] or any(not choice.strip() for choice in response['agent_selected']):
        problems.append('agent_selected: label the choices you made, separately from requirement and datasheet facts')
    return problems


def trace_field_exists(statement, path):
    """A path through the statement's objects and lists; never a position inside a string."""
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*(?:\[\d+\]|\.[A-Za-z_][A-Za-z0-9_-]*)*', path):
        return False
    value = statement
    for key, index in re.findall(r'([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]', path):
        if key and isinstance(value, dict) and key in value:
            value = value[key]
        elif not key and isinstance(value, list) and int(index) < len(value):
            value = value[int(index)]
        else:
            return False
    return True


def prepare_context(root, out):
    out.mkdir()
    shutil.copyfile(root / 'docs/design-compile.md', out / 'design-compile.md')
    shutil.copytree(root / 'fixtures/design-examples', out / 'examples')
    for name in ('design.py', 'design_shape.py'):
        shutil.copyfile(Path(__file__).with_name(name), out / name)
    write_json(out / 'author-schema.json', AUTHOR_SCHEMA)


def statement_text(current):
    return current if isinstance(current, str) else json.dumps(current, ensure_ascii=False)


def statement_echo(current):
    """The current statement for the prompt, complete up to STATEMENT_ECHO_CHARS, else cut and marked."""
    text = statement_text(current)
    if len(text) <= STATEMENT_ECHO_CHARS:
        return text
    return (text[:STATEMENT_ECHO_CHARS] + f'\n[Statement excerpt cut at {STATEMENT_ECHO_CHARS} of {len(text)} '
            'characters; the complete text is /candidate/current-statement.txt]')


def prompt_text(requirements, items, feedback, current, kicad_root):
    numbered = '\n'.join(f'{n}. {text}' for n, text in enumerate((t for t, required in items if required), 1))
    return f'''You are the author of a small KiCad design. Produce agent-ee.design/1 from the exact
requirements below. Read /candidate/design-compile.md, /candidate/examples/*.json,
/candidate/design_shape.py and /candidate/design.py: these are the current statement
schema/validators. Return the response envelope from /candidate/author-schema.json;
statement_json contains the complete design as JSON text. Never write a build bundle.
Use status=unsupported and explain the requirement when this compiler subset cannot
satisfy it. Never silently weaken or change a requirement (e.g. four layers).

Choose a compact practical outline unless requirements constrain it. Keep functional
blocks together. All discretionary choices (including dimensions, values, thresholds,
part/CAD selection and pin roles) are agent-selected; enumerate them in agent_selected
and label intent accordingly. Never claim owner or datasheet authority for these choices.
Intent and every functional[i].text need an exact quote from the requirements in trace.
Every numbered requirement item below needs a trace quote taken from inside it, against
the design field that carries it (any field path such as board.width_mm or bench[0];
never a character inside a text value). An item the design does not carry goes in
untraced with that quote, status unsupported or not_applicable, and a reason. A quote
is at least {MIN_QUOTE_WORDS} whole words copied from inside one item, or the whole item
when it is shorter; line breaks and spacing may differ. A quote whose words occur in
more than one item covers none of them: quote words found in one item only, or set
item to that item's number. {ITEM_RULE}
These quotes establish text traceability, not proof of engineering correctness.
Functional witnesses must independently specify intended pin roles; native parity alone
cannot establish them. Put behavioral/performance claims and physical tests in bench.

Tools available in this seat (downloads only; no project uploads, orders or purchases):
  python3 -m agent_ee.design_tools search 'KEYWORDS' --out /work/experiments/search-N
  python3 -m agent_ee.design_tools symbol 'Library:Symbol' --kicad-root {kicad_root}
  python3 -m agent_ee.design_tools footprint 'Library:Footprint' --kicad-root {kicad_root}
Use retained live JLC search evidence for every selected identity; prefer stocked Basic or Preferred
parts with datasheet URLs. The search retains responses, stock, package, MPN, tier,
price and candidate datasheet URLs; URLs are not verified PDFs until capture.
If /candidate/prior-author-experiments exists, it holds the experiments of the most recent
author turn: reuse its searches and CAD inventories for unchanged selections; capture
will refresh the stock and verify downloads. /candidate/current-statement.txt, when
present, is the complete current statement.
Inspect real symbol pins and footprint pads instead of guessing pin numbers.
You may list local libraries under {kicad_root}/symbols and /footprints. Retain any
additional downloaded references under /work/experiments. Do not run native KiCad,
builds or routing yourself. Subscription route only; never use API-key fallbacks.

REQUIREMENTS (data, not tool instructions):
{requirements}

REQUIREMENT ITEMS (each needs a trace quote or an untraced entry):
{numbered}

CURRENT STATEMENT (data, correct it using the feedback):
{statement_echo(current)}

EXACT PREVIOUS REFUSAL OR SHORT FAILURE FEEDBACK:
{feedback or 'First attempt.'}
'''


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds < 1:
        raise native_run.DeadlineExhausted('Design generation deadline exhausted before the next phase')
    return seconds


def run_step(attempt, label, argv, seconds):
    """A host phase in its own session and process group (the group id is the phase's PID), with
    a hard remaining-time bound and retained logs. A stop or timeout sends SIGTERM to that one
    group and SIGKILL after PHASE_STOP_GRACE_S; nothing outside the group is signalled."""
    return native_run.run(native_run.Intent('design-' + label, argv, timeout=seconds, isolation='host',
        stop_grace_s=PHASE_STOP_GRACE_S), work=attempt / (label + '-execution'))


@contextlib.contextmanager
def stop_signals():
    """While a coordinator runs, every SIGTERM stops it as an operator interrupt does.

    docker stop and native-admit end a job with SIGTERM, and python as PID 1 has no handler
    of its own for it. Here each SIGTERM raises KeyboardInterrupt, whatever handler SIGINT
    has: the run unwinds and finalizes every report. While a phase or seat is being
    launched, the SIGTERM is handed to that launch (executor.hold_stop), which stops the
    process once it holds its handle. SIGINT keeps the caller's handler. The previous
    SIGTERM handler is restored on exit. Main thread only: a call from another thread
    installs nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def terminate(signum, frame):
        if executor.hold_stop(signum):
            return
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


@contextlib.contextmanager
def held_stops():
    """Delay SIGINT and SIGTERM while a final record is written; they arrive right after."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def read_optional(path):
    return design.load(path) if path.is_file() else None


def read_requirements(path):
    """The exact bytes of a requirements file of at most MAX_REQUIREMENTS_BYTES."""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_REQUIREMENTS_BYTES + 1)
    if len(raw) > MAX_REQUIREMENTS_BYTES:
        raise ValueError(f'requirements: {path} is larger than the {MAX_REQUIREMENTS_BYTES}-byte limit; '
                         'shorten it or split the design')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'requirements: supply UTF-8 text ({exc.reason} at byte {exc.start})') from exc
    if not text.strip():
        raise ValueError('requirements: supply nonempty text')
    if not any(required for _, required in requirement_items(text)):
        raise ValueError('requirements: supply at least one requirement sentence outside headings')
    return raw, text


def failed_checks(root):
    failures = []
    for path in sorted(root.glob('stages/*/review/result.json')):
        result = design.load(path)
        for row in result.get('checks', []):
            if row.get('execution') != 'ok' or row.get('outcome') not in ('pass', 'info'):
                failures.append({k: row[k] for k in ('id', 'kind', 'execution', 'outcome', 'findings') if k in row})
    return failures


def placement_findings(root):
    """Expose the job's concrete rejection rather than only its stage status."""
    result = []
    for path in sorted(root.glob('stages/*/placement/job-report.json')):
        job = design.load(path)
        result.append(dict(path=str(path), status=job['status'], reason=job.get('reason'),
            engine_feasible=job.get('engine_feasible'), validity=job.get('validity'),
            routed_candidate=job.get('routed_candidate'), best_diagnostic=job.get('best_diagnostic'),
            final={k: job.get('final', {}).get(k) for k in
                   ('opens', 'errors', 'warnings', 'integrity', 'edge_intent', 'locality')},
            shortlist=[{k: row[k] for k in ('seed', 'error', 'engine_violations', 'errors',
                'locality', 'edge_intent', 'integrity_problems') if k in row}
                for row in job.get('shortlist', [])[:8]],
            routing_trials=[{k: row[k] for k in ('seed', 'status', 'error', 'final') if k in row}
                for row in job.get('routing_trials', [])[:8]]))
    return result


def without_provenance(value):
    """A copy for author feedback without FEEDBACK_OMITTED_FIELDS; retained reports keep them."""
    if isinstance(value, dict):
        return {k: without_provenance(v) for k, v in value.items() if k not in FEEDBACK_OMITTED_FIELDS}
    if isinstance(value, list):
        return [without_provenance(v) for v in value]
    return value


def _locality_leg(record):
    """One graded locality leg by part, pad and distance, without its provenance."""
    row = {k: record[k] for k in ('kind', 'obligation', 'role', 'citation', 'reason') if k in record}
    for key in ('from', 'to'):
        end = record.get(key)
        if isinstance(end, dict):
            row[key] = f'{end.get("reference")}.{end.get("pad")}'
    if isinstance(record.get('distance_mm'), (int, float)):
        row['distance_mm'] = round(record['distance_mm'], 3)
    if isinstance(record.get('max_distance_nm'), int):
        row['max_distance_mm'] = record['max_distance_nm'] / 1000000
    return row


def placement_summary(findings):
    """Per placement job: engine feasibility, validity and the hard violations by part and term.

    Engine violations are merged across the checked candidates, with the seeds that showed each."""
    summary = []
    for job in findings:
        merged = {}
        for candidate in [job.get('best_diagnostic') or {}, *(job.get('shortlist') or [])]:
            for violation in candidate.get('engine_violations') or []:
                named = {k: violation[k] for k in ('kind', 'ref', 'with', 'obligation', 'role', 'detail')
                         if k in violation}
                row = merged.setdefault(json.dumps(named, sort_keys=True), dict(named,
                    term=VIOLATION_TERMS.get(named.get('kind'), 'see the engine violation record'), seeds=[]))
                if candidate.get('seed') is not None and candidate['seed'] not in row['seeds']:
                    row['seeds'].append(candidate['seed'])
        final = job.get('final') or {}
        errors = {split: ((final.get('errors') or {}).get(split) or {}).get('by_type')
                  for split in ('placement_introduced', 'routing_introduced', 'pre_existing')}
        locality = final.get('locality') or {}
        edge = final.get('edge_intent') or {}
        summary.append(dict(status=job.get('status'), engine_feasible=job.get('engine_feasible'),
            validity=job.get('validity'), engine_violations=list(merged.values()),
            native_errors={k: v for k, v in errors.items() if v}, opens=final.get('opens'),
            integrity=final.get('integrity'),
            edge_intent=dict(status=edge.get('status'), violations=edge.get('violations') or []),
            locality=dict(status=locality.get('status'),
                          defects=[_locality_leg(d) for d in locality.get('defects') or [] if isinstance(d, dict)],
                          unknown=[_locality_leg(d) for d in locality.get('unknown') or [] if isinstance(d, dict)])))
    return summary


def phase_feedback(stage, reason, row):
    """The next author turn's feedback after a failed phase, before the FEEDBACK_CHARS cut.

    Short, actionable sections come first, so the cut falls in the bulky placement detail."""
    feedback = f'Failing stage: {stage}\nReason: {reason}\n'
    if row.get('missing_models'):
        feedback += ("Stock 3D models not found (the not-found rows of the capture's models-report.json): "
                     + json.dumps(row['missing_models']) + '\n')
    if 'search_retry_skipped' in row:
        feedback += 'No automatic search retry: ' + row['search_retry_skipped'] + '\n'
    findings = row.get('placement_findings', [])
    if findings:
        feedback += ('Placement summary (engine feasibility, validity and hard violations): '
                     + json.dumps(placement_summary(findings)) + '\n')
    feedback += 'Check findings: ' + json.dumps(row.get('failed_checks', []))
    if findings:
        feedback += ('\nPlacement findings (provenance fields omitted here; the job reports keep them): '
                     + json.dumps(without_provenance(findings)))
    return feedback


def review_feedback(result):
    from .run_record import finding_status
    from .purpose_review import advisory_deferral

    rows = [row for row in result.get('findings', []) if not advisory_deferral(row)
            and finding_status(row) in ('accepted', 'unresolved', 'deferred')]
    fields = ('id', 'title', 'assessment', 'severity', 'condition', 'failure_mechanism',
              'recommendation', 'disposition', 'disposition_reason')
    return [{key: row[key][:1200] if isinstance(row[key], str) else row[key]
             for key in fields if key in row} for row in rows]


def review_seat_failures(result, adjudicator=None):
    """Review seats that failed as a subscription seat fails: the reviewers in the review result
    and the adjudicator, whose execution the review keeps on its own. Any execution that is not
    ok counts, except a timeout, a seat the stopped run never reached, and a structured-output
    refusal (as for the author, those are not login, quota or transport failures)."""
    rows = [*(result or {}).get('review_executions', [])]
    if isinstance(adjudicator, dict):
        rows.append(dict(adjudicator, role='adjudicator'))
    return [{key: row.get(key) for key in ('role', 'execution', 'error')} for row in rows
            if isinstance(row, dict) and row.get('execution') != 'ok'
            and row.get('execution') not in ('timeout', 'not_run', 'interrupted')
            and not (row.get('execution') == 'malformed' and row.get('refusal_kind') == 'structured_output')]


def review_reason(findings, reviewed, attempt, record):
    """Blocking findings first, then a short excerpt of the retained review report."""
    lines = ['Blocking engineering review findings (accepted, deferred or unresolved):']
    lines += [f'{n}. {f.get("id", "?")}: {f.get("title", "")} [{f.get("disposition", "unresolved")}]'
              for n, f in enumerate(findings, 1)] or ['None listed; inspect the retained review result.']
    if findings:
        lines.append('Finding details: ' + json.dumps(findings, ensure_ascii=False))
    summary = {}
    if isinstance(reviewed, dict):
        summary = dict(outcome=reviewed.get('outcome'), accepted=reviewed.get('accepted'),
            dimensions=(reviewed.get('qualification') or {}).get('dimensions'),
            checks_not_passing=[{k: c.get(k) for k in ('id', 'execution', 'outcome')} for c in reviewed.get('checks', [])
                if isinstance(c, dict) and (c.get('execution') != 'ok' or c.get('outcome') not in ('pass', 'info'))])
    text = json.dumps(summary, ensure_ascii=False)
    if len(text) > REVIEW_EXCERPT_CHARS:
        text = text[:REVIEW_EXCERPT_CHARS] + ' [excerpt cut]'
    lines.append(f'Review report retained at {attempt.name}/review/report.json; excerpt: {text}')
    log = execution_error(record, attempt, 'review', names=('stderr.log',))
    lines.append('Review execution: ' + log[-REVIEW_EXCERPT_CHARS:])
    return '\n'.join(lines)


def log_tail(path, limit=LOG_TAIL_BYTES):
    """The last limit bytes of a retained log, marked when cut; empty when absent."""
    try:
        with open(path, 'rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            stream.seek(max(0, size - limit))
            data = stream.read(limit)
    except OSError:
        return ''
    text = data.decode('utf-8', errors='replace').strip()
    if size > limit:
        text = f'[{size - limit} earlier bytes of {Path(path).name} omitted; the full log is retained]\n' + text
    return text


def execution_error(record, attempt, label, names=('stderr.log', 'stdout.log')):
    directory = attempt / (label + '-execution')
    text = '\n'.join(tail for tail in (log_tail(directory / name) for name in names) if tail)
    return text or json.dumps(dict(record), sort_keys=True)


def refusal_classes(text):
    """Stable field paths; keep the exact text separately, never relabel a gap."""
    return sorted(set(re.findall(r'(?m)^- ([A-Za-z_][A-Za-z0-9_.\[\]-]*):', text)))


def search_failure(build, run):
    """A placement search failure that gets a seed retry. ``placement_infeasible`` (no
    hard-feasible engine state) is excluded: that statement goes back to the author."""
    if not build or not build.get('attempts') or build['attempts'][-1].get('stage') != 'placement-routing':
        return False
    jobs = list(run.glob('stages/*/placement/job-report.json'))
    return bool(jobs) and any(design.load(p).get('status') in
        ('route_failed', 'placement_failed', 'no_checked_candidate', 'verification_failed') for p in jobs)


def outline_utilisation(run, statement):
    """Final-coordinate courtyard bounding-box union / outline area; explicitly a proxy."""
    from .symbol_library import children, parse_sexpr, field
    report = read_optional(run / 'build-report.json')
    if not report or not report.get('candidate'):
        return dict(status='unavailable', reason='No completed native candidate')
    if not any(row['stage'] == 'placement-routing' for row in report.get('completed', [])):
        return dict(status='unavailable', reason='No completed routed candidate; schematic positions are not final placement')
    boards = sorted((run / report['candidate']).rglob('*.kicad_pcb'))
    if len(boards) != 1:
        return dict(status='unavailable', reason='No unique native board in selected candidate')
    node = parse_sexpr(boards[0].read_bytes())
    boxes = []
    footprints = children(node, 'footprint')
    for fp in footprints:
        at = children(fp, 'at')[0][1:]
        x, y = map(float, at[:2])
        angle = math.radians(float(at[2]) if len(at) > 2 else 0)
        points = []
        for shape in children(fp, 'fp_line') + children(fp, 'fp_rect'):
            if field(shape, 'layer', 1)[0] not in ('F.CrtYd', 'B.CrtYd'):
                continue
            for key in ('start', 'end'):
                a, b = map(float, field(shape, key, 2)[:2])
                points.append((x + a * math.cos(angle) + b * math.sin(angle),
                               y - a * math.sin(angle) + b * math.cos(angle)))
        if points:
            boxes.append((min(p[0] for p in points), min(p[1] for p in points),
                          max(p[0] for p in points), max(p[1] for p in points)))
    xs = sorted({p for box in boxes for p in (box[0], box[2])})
    area = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        end, height = -math.inf, 0
        for low, high in intervals:
            height += max(0, high - max(end, low))
            end = max(end, high)
        area += (right - left) * height
    outline = statement['board']['width_mm'] * statement['board']['height_mm']
    return dict(status='measured_proxy' if len(boxes) == len(footprints) else 'partial_proxy',
        method='union of final-coordinate linear courtyard bounding boxes; sides projected together',
        courtyard_bbox_union_mm2=area, outline_mm2=outline, fraction=area / outline,
        footprints=len(footprints), footprints_with_linear_courtyard=len(boxes),
        board_path=str(boards[0]), board_sha256=digest(boards[0]),
        limitations=['Not exact polygon area, routing density, thermal utilisation, or proof the outline can shrink.'])


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


@functools.cache
def code_identity():
    """The imported agent_ee package: a digest of every file, and the HEAD of the agent-ee
    checkout it sits in, if any (git_dirty: the package differs from that HEAD)."""
    package = Path(__file__).resolve().parent
    tree = hashlib.sha256()
    files = sorted(p for p in package.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for path in files:
        tree.update(f'{path.relative_to(package).as_posix()}\0{digest(path)}\n'.encode())
    commit = dirty = None
    try:
        head = subprocess.run(['git', '--no-optional-locks', '-C', str(package), 'rev-parse', '--show-toplevel',
                               'HEAD'], capture_output=True, text=True, timeout=30)
        lines = head.stdout.split()
        if (head.returncode == 0 and len(lines) == 2 and Path(lines[0]).resolve() == package.parents[1]
                and re.fullmatch(r'[0-9a-f]{40}', lines[1])):
            commit = lines[1]
            status = subprocess.run(['git', '--no-optional-locks', '-C', str(package), 'status', '--porcelain',
                                     '--', '.'], capture_output=True, text=True, timeout=30)
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return dict(package=str(package), files=len(files), tree_sha256=tree.hexdigest(), git_commit=commit,
                git_dirty=dirty)


def router_pin():
    """The packaged kicad-route pin record the build checks the router against."""
    try:
        return dict(path=str(ROUTER_PIN), sha256=digest(ROUTER_PIN), pin=json.loads(ROUTER_PIN.read_bytes())['pin'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(path=str(ROUTER_PIN), error=f'{type(exc).__name__}: {exc}')


def run_identity(options, requirements):
    """What produced a report: tool source, router pin, author, bounds and UTC start time."""
    profile = options['review_profile']
    return dict(started_at=utc_now(), agent_ee=code_identity(), router_pin=router_pin(),
        author=options['author'], bounds={k: options[k] for k in ('max_attempts', 'max_seconds', 'author_seconds',
            'search_retries')},
        review_profile=None if profile is None else dict(path=str(profile), sha256=digest(profile)),
        kicad_root=str(options['kicad_root']), context_root=None if options['context_root'] is None
            else str(options['context_root']), requirements=str(Path(requirements).resolve()))


def generate(requirements_path, out, **options):
    """Run one design to a final state. SIGTERM interrupts it like SIGINT; every report is final."""
    with stop_signals():
        return _generate(requirements_path, out, resolve_options(options))


def _generate(requirements_path, out, options, identity=None):
    author, max_attempts, max_seconds = options['author'], options['max_attempts'], options['max_seconds']
    author_seconds, search_retries = options['author_seconds'], options['search_retries']
    review_profile, kicad_root = options['review_profile'], options['kicad_root']
    definition = author_definition(author)
    requirements_path, out = Path(requirements_path), Path(out).resolve()
    requirements_raw, requirements = read_requirements(requirements_path)
    items = requirement_items(requirements)
    identity = identity or run_identity(options, requirements_path)
    seat_root, seat_mounts = seat_kicad_root(kicad_root)
    root = Path(options['context_root']) if options['context_root'] else Path(__file__).resolve().parents[2]
    out.mkdir(parents=True, exist_ok=False)
    prepare_context(root, out / 'context')
    (out / 'requirements.md').write_bytes(requirements_raw)
    if review_profile:
        shutil.copyfile(review_profile, out / 'review-profile.json')
        review_profile = out / 'review-profile.json'
    start = time.monotonic()
    deadline = start + max_seconds
    report = dict(schema=SCHEMA, requirements_sha256=digest(out / 'requirements.md'),
        author=definition, bounds=dict(max_attempts=max_attempts, max_seconds=max_seconds,
        author_seconds=author_seconds, search_retries=search_retries), run=identity,
        status='running', accepted=False, attempts=[], duration_s=0,
        claims=dict(file_release=False, engineering_review='not_run', supplier_preview=False, physical_test=False),
        limitations=['Accepted means typed software file obligations only.',
            'Requirement quotes and topology witnesses do not prove engineering correctness.',
            'No supplier preview, purchase, fabrication order, or physical test.'])
    current, feedback, retry = None, '', None
    retries_used = 0
    experiments_from = None  # the most recent author turn's experiments, offered to the next turn

    def save():
        report['duration_s'] = time.monotonic() - start
        write_json(out / 'generate-report.json', report)

    save()
    try:
        for number in range(1, max_attempts + 1):
            if deadline - time.monotonic() < 1:
                report['status'] = 'time_exhausted'
                break
            attempt = out / f'attempt-{number:03}'
            attempt.mkdir()
            row = dict(number=number, kind='search_retry' if retry else 'author',
                path=attempt.name, status='running', duration_s=0, refusal_classes=[], failed_checks=[])
            began = time.monotonic()
            try:
                # Listed inside the try: every way out of it gives the row a final status.
                report['attempts'].append(row)
                save()
                if retry:
                    # No model call: the source attempt's response and captures with more seeds.
                    capture, response = retry['capture'], retry['response']
                    text = json.dumps(current)
                    before, after = retry['seeds']
                    row['automatic_retry'] = (f'Placement seeds {before} -> {after} after the search failure in '
                        f'{retry["source"]}; circuit, parts and rules unchanged; no model call.')
                    write_json(attempt / 'automatic-retry.json', dict(schema=RETRY_SCHEMA, kind='search_retry',
                        model_call=False, source_attempt=retry['source'],
                        reason='Placement search failed; the same statement is rebuilt with more placement seeds.',
                        changed={'placement.seeds': dict(before=before, after=after)},
                        unchanged='Circuit, parts, rules and trace of the source attempt; its immutable captures are reused.',
                        capture=str(capture)))
                    retry = None
                else:
                    shutil.copytree(out / 'context', attempt / 'inputs')
                    if current is not None:
                        (attempt / 'inputs/current-statement.txt').write_text(statement_text(current), encoding='utf-8')
                    prior = experiments_from
                    if prior is not None and prior.is_dir() and not prior.is_symlink():
                        shutil.copytree(prior, attempt / 'inputs/prior-author-experiments',
                            ignore=lambda directory, names: [name for name in names
                                if (Path(directory) / name).is_symlink()])
                        row['prior_experiments'] = prior.relative_to(out).parts[0]
                    timeout = min(author_seconds, remaining(deadline))
                    prompt = prompt_text(requirements, items, feedback, current, seat_root)
                    prompt += f'\nReturn the final envelope within {int(timeout)} seconds. Full capture and native validation happen after this turn; keep exploration focused.\n'
                    (attempt / 'prompt.txt').write_text(prompt, encoding='utf-8')
                    experiments_from = attempt / 'review-work/author/experiments'
                    record, response = review_run.structured_call(attempt, 'author', prompt, AUTHOR_SCHEMA,
                        lambda supplied: harnesses.native_call(attempt, 'author', definition, supplied, AUTHOR_SCHEMA,
                            min(timeout, remaining(deadline)), log_bytes=AUTHOR_CONSOLE_BYTES,
                            extra_mounts=seat_mounts), validate_response)
                    row['author_execution'] = record['execution']
                    write_json(attempt / 'response.json', response)
                    if response is None:
                        if record['execution'] == 'malformed' and record.get('refusal_kind') == 'structured_output':
                            raw = read_optional(attempt / 'review-attempts/author/0/response.json')
                            if isinstance(raw, dict) and isinstance(raw.get('statement_json'), str):
                                current = raw['statement_json']
                            raise design.DesignError(['author_response: ' + record.get('error', 'Malformed structured output')])
                        if record['execution'] == 'timeout':
                            # Another author turn follows while attempts and time remain.
                            row.update(status='author_timeout', reason=f'Author time limit ({int(timeout)} s) reached '
                                'before a final structured response; its experiments go to the next author turn')
                            feedback = (f'Your previous author turn reached its {int(timeout)}-second limit before it '
                                'returned the envelope. Its retained experiments are in /candidate/prior-author-experiments: '
                                'reuse them, limit new exploration and return the envelope early.')
                            continue
                        if record['execution'] == 'output_limit':
                            raise PhaseFailure('author', f'The author console passed the {AUTHOR_CONSOLE_BYTES}-byte '
                                'limit and the turn was stopped; keep command output short.', record)
                        row.update(status='seat_failed', reason=record.get('error', 'Author returned no valid response'))
                        report['status'] = 'seat_failed'
                        break  # Auth/quota/transport failures never trigger a fallback or another seat.
                    if response['status'] == 'unsupported':
                        row.update(status='unsupported', reason=response['reason'])
                        report['status'] = 'unsupported'
                        break
                    capture, text = attempt / 'capture', response['statement_json']
                (attempt / 'statement.json').write_text(text, encoding='utf-8')
                current = text
                try:
                    current = design.load(attempt / 'statement.json')
                except ValueError as exc:
                    raise design.DesignError(['statement_json: supply strict JSON; ' + str(exc)]) from exc
                design.validate(current)
                problems = trace_problems(response, current, requirements)
                if problems:
                    raise design.DesignError(problems)
                row['untraced'] = response['untraced']
                write_json(attempt / 'provenance.json', dict(requirements_sha256=report['requirements_sha256'],
                    trace=response['trace'], untraced=response['untraced'], agent_selected=response['agent_selected'],
                    authority='Agent-authored choices and requirement quotations; not owner or datasheet rulings.'))
                row['capture'] = str(capture)
                if not capture.exists():
                    record = run_step(attempt, 'capture', [sys.executable, '-B', '-m', 'agent_ee.cli',
                        'design', 'capture', str(attempt / 'statement.json'), '--out', str(capture),
                        '--kicad-root', str(kicad_root)], remaining(deadline))
                    if record['execution'] != 'ok' or record.get('exit_code') != 0:
                        raise PhaseFailure('capture', execution_error(record, attempt, 'capture'), record)
                bundle = attempt / 'bundle'
                record = run_step(attempt, 'compile', [sys.executable, '-B', '-m', 'agent_ee.cli',
                    'design', 'compile', str(attempt / 'statement.json'), '--captures', str(capture),
                    '--out', str(bundle)], remaining(deadline))
                if record['execution'] != 'ok' or record.get('exit_code') != 0:
                    raise PhaseFailure('compile', execution_error(record, attempt, 'compile'), record)
                run = attempt / 'build'
                record = run_step(attempt, 'build', [sys.executable, '-B', '-m', 'agent_ee.cli',
                    'build', 'run', str(bundle / 'build-plan.json'), '--out', str(run), '--through', 'package'],
                    remaining(deadline))
                build = read_optional(run / 'build-report.json')
                row.update(bundle=str(bundle), run=str(run), failed_checks=failed_checks(run),
                    placement_findings=placement_findings(run), outline_utilisation=outline_utilisation(run, current))
                qualification = read_optional(run / 'qualification.json')
                file_pass = bool(build and build.get('accepted') is True and qualification
                    and qualification.get('mode') == 'typed'
                    and qualification.get('dimensions', {}).get('file_release', {}).get('status') == 'pass')
                row['file_release'] = file_pass
                if record['execution'] != 'ok' or record.get('exit_code') != 0 or not file_pass:
                    row['failing_stage'] = build['attempts'][-1]['stage'] if build and build.get('attempts') else 'build'
                    if search_failure(build, run) and retries_used < search_retries:
                        seeds = current.get('placement', {}).get('seeds', DEFAULT_SEEDS)
                        more = min(MAX_SEEDS, seeds * 2)
                        if more > seeds:
                            retries_used += 1
                            current = copy.deepcopy(current)
                            current.setdefault('placement', {})['seeds'] = more
                            retry = dict(capture=capture, response=response, source=attempt.name, seeds=(seeds, more))
                        else:
                            row['search_retry_skipped'] = (f'placement.seeds is already {seeds}, the most the '
                                'compiler accepts; a rebuild would repeat the same search')
                    elif any(p.get('status') == 'placement_infeasible' for p in row['placement_findings']):
                        row['search_retry_skipped'] = ('the placement engine found no placement that meets every '
                            'hard constraint (placement_infeasible), so the statement comes back to you; the '
                            'engine violations in the placement summary name the parts and terms to change')
                    reason = (build or {}).get('reason') or execution_error(record, attempt, 'build')
                    raise PhaseFailure(row['failing_stage'], reason, record)
                row['engineering_review'] = 'not_run'
                if review_profile is not None:
                    record = run_step(attempt, 'review', [sys.executable, '-B', '-m', 'agent_ee.cli',
                        'build', 'review', str(run), '--out', str(attempt / 'review'),
                        '--profile', str(review_profile), '--max-seconds', str(max(1, int(deadline - time.monotonic())))],
                        remaining(deadline))
                    reviewed = read_optional(attempt / 'review/report.json')
                    dimension = ((reviewed or {}).get('qualification', {}).get('dimensions', {}).get('engineering_review', 'incomplete'))
                    row['engineering_review'] = dimension
                    if record['execution'] != 'ok' or record.get('exit_code') != 0 or dimension != 'pass':
                        result = read_optional(attempt / 'review/review/result.json')
                        row['review_findings'] = review_feedback(result or {})
                        failed = review_seat_failures(result, read_optional(attempt / 'review/review/adjudicator-execution.json'))
                        if failed:
                            # As for the author: no fallback, no further seat, and the benchmark stops.
                            row.update(status='seat_failed', failing_stage='review', review_seat_failures=failed,
                                reason='Engineering review seat failed (' + ', '.join(
                                    f'{r["role"]}: {r["execution"]}' for r in failed) + '); no fallback attempted')
                            report['status'] = 'seat_failed'
                            break
                        if dimension not in ('pass', 'failed'):
                            row.update(status='review_incomplete', reason='Engineering review did not complete; inspect retained execution records. No further seat called.')
                            report['status'] = 'review_incomplete'
                            break
                        raise PhaseFailure('review', review_reason(row['review_findings'], reviewed, attempt, record), record)
                row['status'] = 'accepted'
                report.update(status='accepted', accepted=True, selected_attempt=number)
                report['claims'].update(file_release=True, engineering_review=row['engineering_review'])
                break
            except native_run.DeadlineExhausted as exc:
                row.update(status='time_exhausted', reason=str(exc))
                report['status'] = 'time_exhausted'
                break
            except design.DesignError as exc:
                feedback = str(exc)
                row.update(status='refused', reason=feedback, refusal_classes=refusal_classes(feedback))
                (attempt / 'refusal.txt').write_text(feedback, encoding='utf-8')
            except PhaseFailure as exc:
                row.update(status='failed', failing_stage=exc.stage, reason=str(exc),
                    refusal_classes=refusal_classes(str(exc)))
                models = None
                if exc.stage == 'capture' and 'capture' in row:
                    with contextlib.suppress(OSError, ValueError):
                        models = read_optional(Path(row['capture']) / 'models-report.json')
                if isinstance(models, dict):
                    row['missing_models'] = [r for r in models.get('files') or []
                                             if isinstance(r, dict) and r.get('status') == 'not-found']
                feedback = phase_feedback(exc.stage, str(exc), row)
                (attempt / 'refusal.txt').write_text(str(exc), encoding='utf-8')
                if exc.stage in ('capture', 'compile') and not row['refusal_classes']:
                    row['tool_gap'] = 'Refusal does not name a design field'
                if exc.stage in ('build', 'placement-routing') and any(s in feedback for s in
                        ('is not the pinned', 'kicad-route pin', 'pinned code', 'router checkout')):
                    report['status'] = 'runtime_refused'
                    break
            except KeyboardInterrupt:
                row.update(status='interrupted', reason='Coordinator interrupted; partial artifacts retained')
                raise
            except BaseException as exc:
                row.update(status='error', reason=f'{type(exc).__name__}: {exc}')
                raise
            finally:
                with held_stops():
                    row['duration_s'] = time.monotonic() - began
                    # Full feedback is retained. Only the bounded excerpt goes into the next prompt.
                    if len(feedback) > FEEDBACK_CHARS:
                        feedback = feedback[:FEEDBACK_CHARS] + '\n[Feedback excerpt ends; full refusal retained in preceding attempt.]'
                    write_json(attempt / 'attempt.json', row)
                    save()
        if report['status'] in ('running', 'time_exhausted') and report['attempts'][-1:] and \
                report['attempts'][-1]['status'] == 'author_timeout':
            # The last author turn timed out and no attempt or time is left for another.
            report['status'] = 'author_timeout'
        elif report['status'] == 'running':
            report['status'] = 'time_exhausted' if time.monotonic() >= deadline else 'attempts_exhausted'
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        raise
    except BaseException as exc:
        # Preserve an unexpected coordinator failure and propagate it to the caller.
        report.update(status='error', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        with held_stops():
            if not report['accepted']:
                # Claims describe the final outcome: nothing is claimed without an accepted attempt.
                reviewed = any(r.get('engineering_review', 'not_run') != 'not_run' for r in report['attempts'])
                report['claims'].update(file_release=False, engineering_review='not_claimed' if reviewed else 'not_run')
            save()
    return report


class PhaseFailure(Exception):
    def __init__(self, stage, reason, record):
        self.stage, self.record = stage, record
        super().__init__(reason)


# Every benchmark row has exactly these keys; null where a value does not apply.
ROW_KEYS = ('name', 'requirements_sha256', 'status', 'reason', 'attempts', 'attempts_to_accepted', 'duration_s',
            'report', 'refusal_classes', 'failed_checks', 'placement_findings', 'failing_stages',
            'untraced_requirements', 'outline_utilisation')


def design_row(path, run, status=None, reason=None):
    """The benchmark row of one design, from its generate report when that is readable."""
    try:
        generated = read_optional(run / 'generate-report.json')
        if generated is not None:
            rows = generated['attempts']
            final = None if generated['accepted'] else (rows[-1].get('reason') if rows else generated.get('error'))
            return dict(name=path.stem, requirements_sha256=generated['requirements_sha256'],
                status=status or generated['status'], reason=reason if reason is not None else final,
                attempts=len(rows), attempts_to_accepted=len(rows) if generated['accepted'] else None,
                duration_s=generated['duration_s'], report=str(run / 'generate-report.json'),
                refusal_classes=sorted({c for r in rows for c in r['refusal_classes']}),
                failed_checks=[dict(attempt=r['number'], checks=r['failed_checks']) for r in rows if r['failed_checks']],
                placement_findings=[dict(attempt=r['number'], findings=r['placement_findings'])
                    for r in rows if r.get('placement_findings') and r['status'] != 'accepted'],
                failing_stages=sorted({r['failing_stage'] for r in rows if 'failing_stage' in r}),
                untraced_requirements=next((r['untraced'] for r in reversed(rows) if 'untraced' in r), None),
                outline_utilisation=next((r['outline_utilisation'] for r in reversed(rows) if 'outline_utilisation' in r),
                    dict(status='unavailable', reason='No native build reached')))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        status, reason = 'error', (reason + '; ' if reason else '') + f'unreadable generate report: {type(exc).__name__}: {exc}'
    try:
        sha = digest(path)
    except OSError:
        sha = None
    return dict(name=path.stem, requirements_sha256=sha, status=status, reason=reason, attempts=0,
        attempts_to_accepted=None, duration_s=0, report=None, refusal_classes=[], failed_checks=[],
        placement_findings=[], failing_stages=[], untraced_requirements=None,
        outline_utilisation=dict(status='unavailable', reason='No generate report for this design'))


def benchmark(directory, out, **options):
    """Every *.md design in filename order. SIGTERM interrupts it like SIGINT; every report is final."""
    with stop_signals():
        return _benchmark(directory, out, resolve_options(options))


def _benchmark(directory, out, options):
    paths = sorted(Path(directory).glob('*.md'))
    if not paths:
        raise ValueError('benchmark: no *.md requirement documents')
    out = Path(out).resolve()
    identity = run_identity(options, directory)
    out.mkdir(parents=True, exist_ok=False)
    report = dict(schema=BENCHMARK_SCHEMA, run=identity, status='running', designs=[], duration_s=0)
    start = time.monotonic()
    in_flight = None

    def save():
        report['duration_s'] = time.monotonic() - start
        write_json(out / 'benchmark-report.json', report)

    def list_rest(stop, current_status, current_reason):
        """Rows for the design in flight and for every design not started."""
        listed = {row['name'] for row in report['designs']}
        for path in paths:
            if path.stem in listed:
                continue
            if path == in_flight:
                row = design_row(path, out / path.stem)
                if row['status'] in (None, 'running'):
                    # No final report of its own: the stop decides its state.
                    row = design_row(path, out / path.stem, current_status, current_reason)
                report['designs'].append(row)
            else:
                report['designs'].append(design_row(path, out / path.stem, 'not_run', stop))

    save()
    try:
        for path in paths:
            in_flight = path
            failure = None
            try:
                _generate(path, out / path.stem, options, identity)
            except Exception as exc:
                # One design's error never stops or drops the others.
                failure = f'{type(exc).__name__}: {exc}'
            row = design_row(path, out / path.stem, 'error' if failure else None, failure)
            report['designs'].append(row)
            in_flight = None
            save()
            if row['status'] in ('seat_failed', 'runtime_refused'):
                report['status'] = 'stopped_' + row['status']
                list_rest(report['status'], None, None)
                break
        else:
            report['status'] = 'complete'
    except KeyboardInterrupt:
        with held_stops():
            report['status'] = 'interrupted'
            list_rest('interrupted', 'interrupted', 'Coordinator interrupted during this design; partial artifacts retained')
        raise
    except BaseException as exc:
        with held_stops():
            report.update(status='error', error=f'{type(exc).__name__}: {exc}')
            list_rest('error', 'error', report['error'])
        raise
    finally:
        with held_stops():
            save()
    return report


def execute(args):
    options = {key: getattr(args, key) for key in DEFAULTS}
    try:
        report = (benchmark if args.design_command == 'benchmark' else generate)(args.requirements, args.out, **options)
    except KeyboardInterrupt:
        print(f'agent-ee: design {args.design_command} interrupted; its reports under {args.out} are final',
              file=sys.stderr)
        return 130
    print(json.dumps(report, indent=2))
    if args.design_command == 'benchmark':
        return 0 if report['status'] == 'complete' and all(r['status'] == 'accepted' for r in report['designs']) else 1
    return 0 if report['accepted'] else 1
