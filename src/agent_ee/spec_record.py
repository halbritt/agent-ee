"""Deterministic stage-1 capture: a markdown requirements spec to an agent-ee.spec-record/1.

Run ``python3 -m agent_ee.spec_record capture SPEC.md --out spec-record.json --prefix POD
--code 'Interfaces=IF' ...`` (see docs/spec-record.md). Every table row, bullet and numbered
item becomes a requirement with a byte-exact locator; every paragraph is kept as prose. No
inference: cell text is kept verbatim, IDs come from the declared codes and the spec's own
first cells and numbers, and anything the parser does not support is refused with its byte
offset rather than read some other way.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

from .storage import json_bytes

SCHEMA = 'agent-ee.spec-record/1'
RECEIPT_SCHEMA = 'agent-ee.spec-capture-receipt/1'
KINDS = ('table_row', 'bullet', 'acceptance')
PREFIX = re.compile(r'[A-Z][A-Z0-9]{0,15}')
CODE = re.compile(r'[A-Z][A-Z0-9]{0,7}')
SPACE = b' \t'
ATX = re.compile(rb'( {0,3})(#{1,6})(?:[ \t]+|$)')
BULLET = re.compile(rb'([-*+])(?:[ \t]+|$)')
ORDERED = re.compile(rb'(\d{1,9})[.)](?:[ \t]+|$)')
DELIMITER = re.compile(rb'\|(?:[ \t]*:?-+:?[ \t]*\|)+')
BREAK = re.compile(rb'([-*_])(?:[ \t]*\1){2,}[ \t]*')
SETEXT = re.compile(rb'(=+|-+)[ \t]*')


class SpecRefused(ValueError):
    """The spec (or the declared codes) cannot be captured; ``findings`` give each byte offset."""

    def __init__(self, findings):
        super().__init__('; '.join(f"byte {f['byte']}: {f['message']}" for f in findings))
        self.findings = findings


def refuse(code, byte, message):
    raise SpecRefused([{'code': code, 'byte': byte, 'message': message}])


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def slug(text):
    """Upper-case ASCII letters and digits of a cell, other runs joined by one hyphen."""
    return '-'.join(re.findall(r'[A-Z0-9]+', text.upper()))


class Line:
    __slots__ = ('start', 'end', 'raw')

    def __init__(self, start, end, raw):
        self.start, self.end, self.raw = start, end, raw  # end excludes the line terminator

    @property
    def indent(self):
        return len(self.raw) - len(self.raw.lstrip(b' '))

    @property
    def body(self):
        return self.raw[self.indent:]

    @property
    def blank(self):
        return not self.raw.strip(SPACE)

    def span(self, offset=0):
        """(start, end) of the line's text from offset, surrounding whitespace excluded."""
        text = self.raw[offset:]
        lead = len(text) - len(text.lstrip(SPACE))
        return self.start + offset + lead, self.start + len(self.raw.rstrip(SPACE))


def split_lines(raw):
    if not raw:
        refuse('empty', 0, 'the spec is empty')
    if raw.startswith(b'\xef\xbb\xbf'):
        refuse('encoding', 0, 'a byte-order mark is not supported')
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        refuse('encoding', exc.start, 'the spec is not UTF-8')
    if b'\x00' in raw:
        refuse('encoding', raw.index(b'\x00'), 'NUL byte in the spec')
    lines, start = [], 0
    while start < len(raw):
        stop = raw.find(b'\n', start)
        stop = len(raw) if stop < 0 else stop
        end = stop - 1 if stop > start and raw[stop - 1:stop] == b'\r' else stop
        text = raw[start:end]
        if b'\r' in text:
            refuse('encoding', start + text.index(b'\r'), 'carriage return not followed by a line feed')
        lead = text[:len(text) - len(text.lstrip(SPACE))]
        if b'\t' in lead:
            refuse('unsupported', start + lead.index(b'\t'), 'tab in line indentation')
        lines.append(Line(start, end, text))
        start = stop + 1
    return lines


def heading(line):
    """(level, text) of an ATX heading line, else None."""
    match = ATX.match(line.raw)
    if not match:
        return None
    rest = line.raw[match.end():].rstrip(SPACE)
    closing = re.search(rb'(?:^|[ \t]+)#+$', rest)
    if closing:
        rest = rest[:closing.start()].rstrip(SPACE)
    if not rest:
        refuse('heading', line.start, 'empty heading')
    return len(match[2]), rest.decode()


def unsupported(line):
    """The name of a construct this parser refuses at a block start, else None."""
    body = line.body
    if line.indent >= 4:
        return 'indented code block'
    if body.startswith((b'```', b'~~~')):
        return 'fenced code block'
    if body.startswith(b'>'):
        return 'block quote'
    if body.startswith(b'<'):
        return 'HTML block or autolink at a block start'
    if BREAK.fullmatch(body):
        return 'thematic break or setext underline'
    return None


def list_marker(line):
    """(ordered, number, content_offset) when the line starts a list item, else None."""
    match = BULLET.match(line.body) or ORDERED.match(line.body)
    if not match:
        return None
    ordered = match.re is ORDERED
    return ordered, int(match[1]) if ordered else None, line.indent + match.end()


def is_delimiter(line):
    return line is not None and line.indent < 4 and bool(DELIMITER.fullmatch(line.body.rstrip(SPACE)))


def cells_of(line):
    """[(start, end)] of each cell of a pipe-bounded row, whitespace trimmed; refuses unclosed rows."""
    body = line.raw.rstrip(SPACE)
    offset = line.indent
    if line.indent >= 4 or not body[offset:].startswith(b'|'):
        refuse('table', line.start, 'table row does not begin with |')
    pipes, index, escape = [offset], offset + 1, None
    while index < len(body):
        byte = body[index:index + 1]
        if byte == b'\\':
            if index + 1 >= len(body):
                refuse('table', line.start + index, 'backslash escape at the end of a table row is not closed')
            escape = index
            index += 2
            continue
        if byte == b'|':
            pipes.append(index)
        index += 1
    if pipes[-1] != len(body) - 1:
        at = escape if escape == len(body) - 2 else len(body)
        refuse('table', line.start + at, 'table row does not end with an unescaped |')
    spans = []
    for left, right in zip(pipes, pipes[1:]):
        text = body[left + 1:right]
        lead = len(text) - len(text.lstrip(SPACE))
        trimmed = text.strip(SPACE)
        start = line.start + left + 1 + lead
        spans.append((start, start + len(trimmed)))
    return spans


class Parser:
    def __init__(self, raw):
        self.raw = raw
        self.lines = split_lines(raw)
        self.headings, self.tables, self.lists, self.prose, self.items = [], [], [], [], []
        self.path = []  # [(level, text)]
        self.section = None  # index into headings

    def text(self, start, end):
        return self.raw[start:end].decode()

    def where(self):
        if self.section is None:
            return None
        entry = self.headings[self.section]
        entry['content'] = True
        return entry

    def owner(self, line):
        entry = self.where()
        if entry is None:
            refuse('structure', line.start, 'content before the first heading')
        return entry

    def parse(self):
        lines, index = self.lines, 0
        while index < len(lines):
            line = lines[index]
            if line.blank:
                index += 1
                continue
            found = heading(line)
            if found:
                self.open_heading(line, *found)
                index += 1
                continue
            construct = unsupported(line)
            if construct:
                refuse('unsupported', line.start + min(line.indent, 3), construct + ' is not supported')
            if list_marker(line):
                if line.indent:
                    refuse('list', line.start, 'list marker must start in the first column')
                index = self.parse_list(index)
            elif line.body.startswith(b'|'):
                index = self.parse_table(index)
            else:
                index = self.parse_paragraph(index)
        return self

    def open_heading(self, line, level, text):
        while self.path and self.path[-1][0] >= level:
            self.path.pop()
        self.path.append((level, text))
        self.section = len(self.headings)
        self.headings.append({'level': level, 'text': text, 'heading_path': [t for _, t in self.path],
                              'byte_start': line.start + line.indent,
                              'byte_end': line.start + len(line.raw.rstrip(SPACE)), 'content': False})

    def counter(self, entry, key):
        entry[key] = entry.get(key, 0) + 1
        return entry[key]

    def parse_paragraph(self, index):
        lines, first = self.lines, self.lines[index]
        entry = self.owner(first)
        last = index
        index += 1
        while index < len(lines) and not lines[index].blank:
            line = lines[index]
            if heading(line):
                break
            if line.indent < 4:
                if SETEXT.fullmatch(line.body) or is_delimiter(line):
                    refuse('unsupported', line.start, 'setext heading underline or table delimiter after a '
                           'paragraph line; separate tables with a blank line and use # headings')
                if list_marker(line) or line.body.startswith(b'|'):
                    refuse('structure', line.start, 'list item or table row directly after a paragraph line; '
                           'separate it with a blank line')
                construct = unsupported(line)
                if construct:
                    refuse('unsupported', line.start + line.indent, construct + ' is not supported')
            last = index
            index += 1
        start, end = first.span()[0], lines[last].span()[1]
        self.prose.append({'section': entry, 'paragraph': self.counter(entry, 'paragraphs'),
                           'byte_start': start, 'byte_end': end})
        return index

    def parse_table(self, index):
        lines, header = self.lines, self.lines[index]
        entry = self.owner(header)
        delimiter = lines[index + 1] if index + 1 < len(lines) else None
        if not is_delimiter(delimiter):
            refuse('table', header.start, 'a line starting with | must be a table header followed by a '
                   'delimiter row such as | --- | --- |')
        spans = cells_of(header)
        headers = [self.text(*s) for s in spans]
        for (start, _), name in zip(spans, headers):
            if not name:
                refuse('table', start, 'empty table header cell')
        for position, name in enumerate(headers):
            if name in headers[:position]:
                refuse('table', spans[position][0], f'duplicate table header {name!r}')
        width = len(cells_of(delimiter))
        if width != len(headers):
            refuse('table', delimiter.start,
                   f'ragged table: the delimiter row has {width} cells, the header {len(headers)}')
        number = self.counter(entry, 'tables')
        rows, index = [], index + 2
        while index < len(lines) and not lines[index].blank and not heading(lines[index]):
            row = lines[index]
            row_spans = cells_of(row)
            if len(row_spans) != len(headers):
                refuse('table', row.start,
                       f'ragged table row: {len(row_spans)} cells, the header has {len(headers)}')
            rows.append((row, row_spans))
            index += 1
        last = rows[-1][0] if rows else delimiter
        self.tables.append({'section': entry, 'table': number, 'headers': headers,
                            'byte_start': header.span()[0], 'byte_end': last.span()[1], 'rows': len(rows)})
        for position, (row, row_spans) in enumerate(rows, 1):
            start, end = row.span()
            self.items.append({'section': entry, 'kind': 'table_row', 'key': slug(self.text(*row_spans[0])),
                               'key_byte': row_spans[0][0], 'table': number, 'row': position, 'list': None,
                               'item': None, 'byte_start': start, 'byte_end': end,
                               'cells': dict(zip(headers, row_spans))})
        return index

    def parse_list(self, index):
        lines, first = self.lines, self.lines[index]
        entry = self.owner(first)
        ordered = list_marker(first)[0]
        number = self.counter(entry, 'lists')
        items, after_blank = [], False
        while index < len(lines):
            line = lines[index]
            if line.blank:
                after_blank = True
                index += 1
                continue
            if line.indent < 4 and BREAK.fullmatch(line.body):
                refuse('unsupported', line.start + line.indent, 'thematic break or setext underline is not supported')
            marker = list_marker(line) if line.indent < 4 else None
            if after_blank and not marker:
                if line.indent:
                    refuse('list', line.start, 'indented text after a blank line in a list (a multi-paragraph '
                           'item or nested block) is not supported')
                break
            if marker:
                if line.indent:
                    refuse('list', line.start, 'nested or indented list item is not supported')
                if marker[0] != ordered:
                    break
                start = line.start + marker[2]
                if start >= line.start + len(line.raw.rstrip(SPACE)):
                    refuse('list', line.start, 'empty list item')
                items.append({'number': marker[1], 'byte_start': start, 'last': line, 'marker': line.start})
            else:
                if heading(line):
                    break
                inner = Line(line.start, line.end, line.body)
                if list_marker(inner):
                    refuse('list', line.start, 'nested or indented list item is not supported')
                if inner.body.startswith(b'|') or is_delimiter(inner):
                    refuse('list', line.start, 'table inside a list item is not supported')
                construct = unsupported(inner)
                if not construct and SETEXT.fullmatch(inner.body):
                    construct = 'setext heading underline'
                if construct:
                    refuse('unsupported', line.start + line.indent, construct + ' inside a list item is not supported')
                items[-1]['last'] = line
            after_blank = False
            index += 1
        self.lists.append({'section': entry, 'list': number, 'ordered': ordered, 'items': len(items),
                           'byte_start': items[0]['marker'], 'byte_end': items[-1]['last'].span()[1]})
        for position, item in enumerate(items, 1):
            if ordered:
                key = str(item['number'])
            else:
                key = str(self.counter(entry, 'bullets'))
            self.items.append({'section': entry, 'kind': 'acceptance' if ordered else 'bullet', 'key': key,
                               'key_byte': item['marker'], 'table': None, 'row': None, 'list': number,
                               'item': position, 'byte_start': item['byte_start'],
                               'byte_end': item['last'].span()[1], 'cells': {}})
        return index


def check_ids(prefix, codes):
    if not isinstance(prefix, str) or not PREFIX.fullmatch(prefix):
        raise ValueError(f'prefix must match {PREFIX.pattern}')
    if not isinstance(codes, dict) or not codes:
        raise ValueError('codes must map at least one heading to a code')
    for name, code in codes.items():
        if not isinstance(name, str) or not name or not isinstance(code, str) or not CODE.fullmatch(code):
            raise ValueError(f'code for heading {name!r} must match {CODE.pattern}')
    repeated = sorted({c for c in codes.values() if list(codes.values()).count(c) > 1})
    if repeated:
        raise ValueError('codes assigned to more than one heading: ' + ', '.join(repeated))


def capture(raw, prefix, codes, name='spec.md'):
    """The agent-ee.spec-record/1 for the spec bytes; raises SpecRefused (with byte offsets) or ValueError."""
    check_ids(prefix, codes)
    parser = Parser(raw).parse()
    findings, seen_sections = [], {}
    for entry in parser.headings:
        entry['code'] = codes.get(entry['text']) if entry['content'] else None
        if not entry['content']:
            continue
        if entry['text'] in seen_sections:
            findings.append({'code': 'section', 'byte': entry['byte_start'],
                             'message': f"heading {entry['text']!r} repeats a section with content at byte "
                                        f"{seen_sections[entry['text']]}; codes are keyed by heading text"})
            continue
        seen_sections[entry['text']] = entry['byte_start']
        if entry['code'] is None:
            findings.append({'code': 'section', 'byte': entry['byte_start'],
                             'message': f"section {entry['text']!r} has content but no --code"})
    for name_ in sorted(set(codes) - set(seen_sections)):
        findings.append({'code': 'section', 'byte': len(raw),
                         'message': f'--code for {name_!r} names no heading with content'})
    if findings:
        raise SpecRefused(findings)

    def section_id(entry):
        return f"{prefix}-{entry['code']}"

    def where(entry):
        return {'section': entry['text'], 'heading_path': entry['heading_path']}

    claimed, requirements, prose = {}, [], []
    for item in parser.items:
        entry = item['section']
        if not item['key']:
            findings.append({'code': 'id', 'byte': item['key_byte'],
                             'message': 'first cell has no ASCII letter or digit to derive a requirement ID from'})
            continue
        rid = f"{section_id(entry)}-{item['key']}"
        if rid in claimed:
            findings.append({'code': 'duplicate_id', 'byte': item['key_byte'],
                             'message': f'requirement ID {rid} collides with the one at byte {claimed[rid]}'})
            continue
        claimed[rid] = item['key_byte']
        start, end = item['byte_start'], item['byte_end']
        requirements.append({
            'id': rid, 'section': entry['text'], 'kind': item['kind'],
            'cells': {h: parser.text(*s) for h, s in item['cells'].items()},
            'text': parser.text(start, end), 'text_sha256': sha256(raw[start:end]),
            'locator': {'heading_path': entry['heading_path'], 'table': item['table'], 'row': item['row'],
                        'list': item['list'], 'item': item['item'], 'byte_start': start, 'byte_end': end,
                        'cells': {h: [s, e] for h, (s, e) in item['cells'].items()}}})
    for block in parser.prose:
        entry = block['section']
        pid = f"{section_id(entry)}-P{block['paragraph']}"
        if pid in claimed:
            findings.append({'code': 'duplicate_id', 'byte': block['byte_start'],
                             'message': f'prose ID {pid} collides with the requirement at byte {claimed[pid]}'})
            continue
        claimed[pid] = block['byte_start']
        start, end = block['byte_start'], block['byte_end']
        prose.append({'id': pid, 'section': entry['text'], 'text': parser.text(start, end),
                      'text_sha256': sha256(raw[start:end]),
                      'locator': {'heading_path': entry['heading_path'], 'paragraph': block['paragraph'],
                                  'byte_start': start, 'byte_end': end}})
    if findings:
        raise SpecRefused(findings)
    counts = {kind: sum(r['kind'] == kind for r in requirements) for kind in KINDS}
    return {
        'schema': SCHEMA,
        'spec': {'name': name, 'sha256': sha256(raw), 'bytes': len(raw)},
        'ids': {'prefix': prefix, 'codes': dict(codes)},
        'headings': [{'level': e['level'], 'text': e['text'], 'heading_path': e['heading_path'], 'code': e['code'],
                      'byte_start': e['byte_start'], 'byte_end': e['byte_end']} for e in parser.headings],
        'tables': [{**where(t['section']), 'table': t['table'], 'headers': t['headers'], 'rows': t['rows'],
                    'byte_start': t['byte_start'], 'byte_end': t['byte_end']} for t in parser.tables],
        'lists': [{**where(entry['section']), 'list': entry['list'], 'ordered': entry['ordered'],
                   'items': entry['items'], 'byte_start': entry['byte_start'], 'byte_end': entry['byte_end']}
                  for entry in parser.lists],
        'requirements': requirements,
        'prose': prose,
        'counts': {**counts, 'requirements': len(requirements), 'prose': len(prose)},
    }


def verify(record_raw, spec_raw):
    """Load a spec record and prove it is exactly the capture of spec_raw; returns the record."""
    record = json.loads(record_raw)
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError(f'not an {SCHEMA} record')
    spec, ids = record.get('spec'), record.get('ids')
    if not isinstance(spec, dict) or not isinstance(ids, dict):
        raise ValueError('record needs spec and ids objects')
    if spec.get('sha256') != sha256(spec_raw) or spec.get('bytes') != len(spec_raw):
        raise ValueError('spec bytes differ from the record (sha256 or size)')
    expected = json_bytes(capture(spec_raw, ids.get('prefix'), ids.get('codes'), spec.get('name')))
    if expected != record_raw:
        raise ValueError('record is not the byte-identical capture of the spec')
    return record


def receipt(record, record_raw, argv):
    module = Path(__file__)
    return {
        'schema': RECEIPT_SCHEMA,
        'tool': {'module': 'agent_ee.spec_record', 'module_sha256': sha256(module.read_bytes()),
                 'record_schema': SCHEMA, 'python': platform.python_version(),
                 'implementation': platform.python_implementation()},
        'argv': argv,
        'spec': record['spec'],
        'record': {'sha256': sha256(record_raw), 'bytes': len(record_raw)},
        'counts': record['counts'],
    }


def write_new(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o666)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def parse_codes(values):
    codes = {}
    for value in values:
        name, sep, code = value.rpartition('=')
        if not sep or not name:
            raise ValueError(f'--code {value!r} must be HEADING=CODE')
        if name in codes:
            raise ValueError(f'--code repeats heading {name!r}')
        codes[name] = code
    return codes


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.spec_record', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    cap = commands.add_parser('capture', help='capture SPEC into a new spec-record file and receipt')
    cap.add_argument('spec', type=Path)
    cap.add_argument('--out', required=True, type=Path, help='new spec-record file')
    cap.add_argument('--receipt', type=Path, help='new receipt file (default: OUT with .receipt.json)')
    cap.add_argument('--prefix', required=True, help='requirement ID prefix, for example POD')
    cap.add_argument('--code', action='append', default=[], metavar='HEADING=CODE',
                     help='section code for a heading with content (repeat per section)')
    check = commands.add_parser('verify', help='prove RECORD is the byte-identical capture of SPEC')
    check.add_argument('record', type=Path)
    check.add_argument('spec', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'verify':
        try:
            record = verify(args.record.read_bytes(), args.spec.read_bytes())
        except (ValueError, OSError) as exc:  # SpecRefused is a ValueError
            parser.exit(1, f'spec record not verified: {exc}\n')
        print(json.dumps({'outcome': 'verified', 'spec_sha256': record['spec']['sha256'],
                          'counts': record['counts']}, sort_keys=True))
        return 0
    receipt_path = args.receipt or args.out.with_name(args.out.name.removesuffix('.json') + '.receipt.json')
    try:
        raw = args.spec.read_bytes()
        record = capture(raw, args.prefix, parse_codes(args.code), args.spec.name)
    except SpecRefused as exc:
        json.dump({'outcome': 'refused', 'findings': exc.findings}, sys.stdout, indent=2, sort_keys=True)
        print()
        return 1
    except (ValueError, OSError) as exc:
        parser.exit(2, f'spec not captured: {exc}\n')
    record_raw = json_bytes(record)
    for path in (args.out, receipt_path):
        if os.path.lexists(path):
            parser.exit(2, f'outputs not written: {path} exists\n')
    try:
        write_new(args.out, record_raw)
        try:
            write_new(receipt_path, json_bytes(receipt(record, record_raw, ['capture', *argv[1:]])))
        except BaseException:
            os.unlink(args.out)
            raise
    except OSError as exc:
        parser.exit(2, f'outputs not written: {exc}\n')
    print(json.dumps({'outcome': 'captured', 'record_sha256': sha256(record_raw), 'counts': record['counts']},
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
