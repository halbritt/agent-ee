import contextlib
import hashlib
import html
from html.parser import HTMLParser
import itertools
import json
import multiprocessing
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from unittest import mock
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError
from urllib.request import urlopen
from agent_ee.candidate import capture
from agent_ee.render3d import render_board
from agent_ee import render_gallery as gallery
from agent_ee.render_gallery import page, publish, serve
from agent_ee.storage import digest
from agent_ee.viewer import _ASSETS, build_viewer
from toolchain import native_board_render_test, require_toolchain

HOST = '/home/someone/secret-project'
SIDES = {'top': ['--side', 'top'], 'bottom': ['--side', 'bottom'], 'isometric': ['--side', 'top', '--rotate', '315,0,45']}


def png_bytes(shade, width=2, height=2):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = (b'\0' + bytes([shade]) * (width * 3)) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def glb_bytes():
    body = json.dumps({'asset': {'version': '2.0'}, 'meshes': [{}]}).encode()
    body += b' ' * (-len(body) % 4)
    return struct.pack('<4sIIII', b'glTF', 2, 20 + len(body), len(body), 0x4E4F534A) + body


# Regression oracle (review rounds 433c-433i). Every host, user, share and directory
# in these texts carries "hidden" or "private-"; only plain local basenames may
# survive publication. The texts compose every operand kind found in review: file
# URIs in every casing with or without an authority (empty or root path, bracketed
# IP literals, user info), UNC hosts and paths, drives with either slash, POSIX
# paths, hosts interrupted by quotes, groups and whitespace, wrapped, punctuated and
# joined as first, second and later operands.
CASINGS = ('file', 'FILE', 'File', 'fIlE')
URI_FORMS = ('{}:///home/alice-hidden/dir-hidden/local.step', '{}:///C:/Users/alice-hidden/drive.step',
             '{}://host-hidden/share-hidden/auth.step', '{}:\\\\unc-host-hidden\\unc-share-hidden\\unc.step',
             '{}://root-host-hidden/', '{}://empty-host-hidden')
PLAIN_FORMS = ('/home/alice-hidden/dir hidden/plain.step', 'C:\\Users\\alice-hidden\\win.step', '\\\\bare-host-hidden')
SEPARATORS = ('', ',', ';', ', ', '; ', ' ', '\t', '"', "'", ')', ']', '>', '|', ' and ', '),(')
UNC_FORMS = ('\\\\later-host-hidden', '//later-host-hidden', '\\\\later-host-hidden\\share-hidden\\later.step',
             '//later-host-hidden/share-hidden/later.step')
LATER_SEPARATORS = (' (', ' [', '),(', ',[', ';(', '" ', "' ", '<', '{', ' {', '}(', ', (', ';[')
IP_URI_FORMS = ('{}://[fd00:1234::1]/share-hidden/v6.step', '{}://[fd00:1234::1]/', '{}://[fd00:1234::1]',
                '{}://user-hidden@[fd00:1234::1]:8/share-hidden/v6u.step',
                '{}://uri-host-hidden "user-hidden name"/share-hidden/us.step')
INTERRUPTED_FORMS = ('\\\\unc-host-hidden(1)\\share-hidden\\grp.step', '\\\\unc-host-hidden[1]\\share-hidden\\brk.step',
                     '\\\\unc-host-hidden "user-hidden"\\share-hidden\\q.step',
                     '\\\\unc-host-hidden (user-hidden)\\share-hidden\\s.step',
                     '\\\\unc-host-hidden "user-hidden name"\\share-hidden\\qs.step',
                     '\\\\unc-host-hidden "user-hidden" \\share-hidden\\qt.step',
                     '\\\\unc-host-hidden (user-hidden name) \\share-hidden\\gs.step')
# The exact texts each review round reported, and what the scrubbing redactor published
# for them (later rounds found host or user text left in those outputs).
REVIEW_EXAMPLES = (
    'C:/Users/Alice-hidden/private-project/board.kicad_pcb', '\\\\fileserver-hidden\\private-share\\c.step',
    '/home/Alice Smith-hidden/private-project/d.step', 'file:///home/alice-hidden/private-project/e.step',
    'FILE:///home/Alice-hidden/private-project/board.kicad_pcb', 'File:///C:/Users/Alice-hidden/private-project/b.step',
    'fIlE://fileserver-hidden/private-share/c.step', 'FILE:\\\\fileserver-hidden\\private-share\\e.step',
    'FILE://private-server/', 'fIlE://private-server', 'FiLe://localhost-hidden/', 'FILE:\\\\private-server\\',
    '\\\\private-server', 'file:////private-server/', 'FILE:///home/alice-hidden/m.step,FILE://private-server/',
    'FILE:///home/alice-hidden/m.step (\\\\private-server)', '/home/alice-hidden/m.step [\\\\private-server]',
    'm.step\\\\private-server', 'FILE://[fd00:1234::1]/private-share/m.step', 'fIlE://[fd00:1234::1]/',
    '\\\\private-host(1)\\private-share\\m.step', '\\\\private-host[1]\\private-share\\m.step',
    '\\\\private-host "private-user"\\private-share\\m.step',
    '\\\\private-host "private-user" \\private-share\\m.step',
    '\\\\private-host "private-user name"\\private-share\\m.step',
    '\\\\private-host (private-user name)\\private-share\\m.step',
    'file://private-host "private-user name"/private-share/m.step',
    '\\\\private-host ( private-user name )\t\\private-share\\m.step',
    "\\\\private-host 'private-user name' \\private-share\\m.step", '\\\\private-host private-user \\private-share\\m.step',
    '\\\\private-host "private-user name\\private-share\\m.step',
    '("\\\\private-host "private-user name" \\private-share\\m.step"), "x"', '\\\\private-host "see /home/a/m.step"',
    '\\\\private-host "a b"\n\\private-share\\m.step',
    # Published by the scrubbing redactor; each still carried host or user text.
    '<redacted>/private-server)', '<redacted>[fd00:1234::1]<redacted>/m.step', '<redacted>(1)\\private-share\\m.step',
    '<redacted> "private-user"\\private-share\\m.step', '<redacted> "private-user name"<redacted>/m.step',
    '<redacted> (private-user name)<redacted>/m.step', '<redacted>/private-server',
    # Review 433k H1: host, user and share words with no path syntax at all.
    'private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
OPERANDS = ([t.format(c) for c in CASINGS for t in URI_FORMS + IP_URI_FORMS] + list(PLAIN_FORMS) + list(UNC_FORMS)
            + list(INTERRUPTED_FORMS) + ['C:/Users/alice-hidden/fwd.step', 'D:/', 'd:\\Users\\alice-hidden\\low.step',
                                         '/home/alice-hidden/posix.step'])
WRAPPERS = ('{}', '({})', '[{}]', '{{{}}}', '"{}"', "'{}'", '(({}))', '["{}"]', '("{}")')
OPERAND_SEPARATORS = (', ', '; ', ',', ';', ' ,\t', '; (', ',[')
LOOSE_SEPARATORS = ('', ' ', '\t', '\n', ' and ', '=', ':', ' | ', ' > ', ' -- ', ')(', '][', '} {')


def operand_triples(separators=OPERAND_SEPARATORS):
    """Yield (operands, wrappers, separators): every operand first and second, and a rotating third."""
    k = 0
    for first in OPERANDS:
        for second in OPERANDS:
            for separator in separators:
                wrappers = [WRAPPERS[(k + n) % len(WRAPPERS)] for n in (0, 1, 2)]
                k += 1
                yield [first, second, OPERANDS[(k - 1) % len(OPERANDS)]], wrappers, (separator, separators[k % len(separators)])


def compose(operands, wrappers, separators):
    return (wrappers[0].format(operands[0]) + separators[0] + wrappers[1].format(operands[1])
            + separators[1] + wrappers[2].format(operands[2]))


def corpus():
    """Every composed text: URI pairs, later UNC/URI operands, review examples and operand triples."""
    for n, casing in enumerate(CASINGS):
        firsts = [t.format(casing) for t in URI_FORMS] + list(PLAIN_FORMS)
        later = [t.format(CASINGS[(n + 1) % len(CASINGS)]) for t in URI_FORMS]
        yield from (first + separator + second for first in firsts for second in later for separator in SEPARATORS)
        operands = [t.format(c) for c in CASINGS for t in URI_FORMS] + list(UNC_FORMS)
        yield from (first + separator + second + tail + ')' for first in firsts for second in operands
                    for separator, tail in zip(LATER_SEPARATORS, LATER_SEPARATORS[n:] + LATER_SEPARATORS[:n]))
    yield from REVIEW_EXAMPLES
    for separators in (OPERAND_SEPARATORS, LOOSE_SEPARATORS):
        yield from (compose(*triple) for triple in operand_triples(separators))


def surface_texts():
    """A compact set with every review example and every operand first, second and third."""
    texts = list(REVIEW_EXAMPLES)
    for n, text in enumerate(OPERANDS):
        others = [OPERANDS[(n + 7) % len(OPERANDS)], OPERANDS[(n + 13) % len(OPERANDS)]]
        for position in range(3):
            parts = others[:position] + [text] + others[position:]
            separator, wrapper = OPERAND_SEPARATORS[n % len(OPERAND_SEPARATORS)], WRAPPERS[(n + position) % len(WRAPPERS)]
            texts.append(separator.join(wrapper.format(t) for t in parts))
    return texts


def random_texts(seed, rounds):
    rng, forms = random.Random(seed), URI_FORMS + IP_URI_FORMS
    plain = [t for t in OPERANDS if not t.lower().startswith('file')]
    separators = SEPARATORS + LATER_SEPARATORS + LOOSE_SEPARATORS + ('<', '(', '[', ' ; ')
    for _ in range(rounds):
        parts = []
        for _ in range(rng.randint(1, 5)):
            if rng.random() < .5:
                parts += [rng.choice(separators), rng.choice(forms).format(rng.choice(CASINGS))]
            else:
                parts += [rng.choice(separators), rng.choice(WRAPPERS).format(rng.choice(plain))]
        yield ''.join(parts + [rng.choice(separators)])


LEAKS = ('hidden', 'file:', 'C:', '\\', 'private-', 'Alice', 'localhost')


# The structural leak oracle (AGENTEE-452; agent-449's positive-oracle redesign after the fourth review).
# Nothing is scanned or recognized. Every published body is parsed (JSON trees; HTML elements, attributes, text
# nodes and <style> content), and every JSON key, string, number and boolean, every HTML tag, attribute value and
# text node, and every route must match an exact grammar from the publisher's own field list in
# src/agent_ee/render_gallery.py; anything that no grammar covers fails with LeakOracleFailure. The record fields
# follow _bundle (render_gallery.py:217), _viewer (:234), _diagnostic (:250), _status_record (:279), _recorded
# (:1173) with _claims (:1000) and _masked (:1018); the pages follow page (:1141), diagnostic_cards (:1120),
# _status_html (:1080), _status_lines (:1033), _supplement_status (:1100) and _with_status (:1181); routes follow
# GalleryHandler.send_head (:1214-1218).
#
# Variable leaves are enumerated or bounded and each has a published witness in the staged gallery
# (~/.local/share/agent-ee-render-gallery/artifacts, checked by ~/agent-ee-diag6-stage/SHA256SUMS; lines below):
#   basenames  REAL_BASENAMES, each from a staged line (served through _basename/_name), plus FIXTURE_BASENAMES:
#              the exact names this module's own fixtures publish; no other name, so "intranet.corp.step" fails;
#   counts     integers 0-999999 (D6 manifest "footprints": 309 at bundles/adfb7668ac0c-86b84f0f741e/manifest.json:13);
#              no float has a witness, so none is covered;
#   designators _DESIGNATOR [A-Z]{1,8}[0-9]{1,6} (D6 warning "TP4: no 3D model is declared", manifest.json:25);
#   hashes, ids, times _HASH, _ID, _TIME (manifest.json:5,8; index.json:3);
#   pixel sizes 1-99999 (manifest.json:52 "KiCad produced 2384×1568 for requested 2400×1600").
# The coordinator's controls (example.org URLs, an email, "user@host", ...) are not grammar: they pass only as the
# exact values a test registers with publication_fixtures() at the place it injects them.
_DESIGNATOR_RE = r'[A-Z]{1,8}[0-9]{1,6}'
_HASH_RE, _ID_RE = r'[a-f0-9]{64}', r'[a-f0-9]{12}-[a-f0-9]{12}'
_TIME_RE = r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)?'
_COUNT_MAX, _PIXELS_RE = 999999, r'[1-9]\d{0,4}'
REAL_BASENAMES = {  # staged file:line whose publication serves the name
    'telemetry-pod.kicad_pcb': 'statuses.json:47', 'top.png': 'index.json:58', 'bottom.png': 'index.json:63',
    'isometric.png': 'index.json:68',
    'render-job-association_adfb7668ac0c_20261022T231757Z.json': 'statuses.json:52',
    'rj_diagnostic-6_305fp+4holes_unrouted_not-qualified_new-board_20261022T231757Z.json': 'statuses.json:57',
    'candidate.kicad_pcb': 'statuses.json:108', 'INDEX.md': 'statuses.json:462', 'RECEIPT.json': 'statuses.json:447',
    'design-diff.json': 'statuses.json:452', 'drc-iter8t.json': 'statuses.json:457',
    'iter8t-association.json': 'statuses.json:472', 'correction-association_2938ee1ee2ca.json': 'statuses.json:299',
    'fome-mc-main-cc0a4045152d_telemetry-pod-README.md': 'statuses.json:372',
    'render-job-association_2938ee1ee2ca_20261022T191106Z.json': 'statuses.json:362',
    'render-job-association_56313982e687_20261022T191820Z.json': 'statuses.json:235',
    'render-job-association_56313982e687_20261022T191947Z.json': 'statuses.json:174',
    'render-job-association_56313982e687_20261022T191954Z.json': 'statuses.json:113',
    'render-job_fome-mc-main-cc0a4045152d_board-changed_not-qualified_20261022T191106Z.json': 'statuses.json:367',
    'rj_iter8t_305fp+4holes_unrouted_not-qualified_board-unchanged_20261022T191947Z.json': 'statuses.json:179',
    'rj_iter8t_305fp+4holes_unrouted_not-qualified_board-unchanged_20261022T191954Z.json': 'statuses.json:118',
    'rj_iter8t_305fp+4holes_unrouted_not-qualified_same-board-as-gallery-56313982e687-0e28e3b235a2_20261022T191820Z.json':
        'statuses.json:240',
    'HISTORICAL_INCORRECT-SELECTION_old-112-footprint-pilot-rendered-by-mistake_retained-as-evidence.json':
        'statuses.json:304',
    'NEO-M9N-00B_visualization-only_datasheet-derived_not-manufacturer-mechanical-qualification.step':
        'statuses.json:467',
    'C_0402_1005Metric.step': 'index.json:1133', 'C_0603_1608Metric.step': 'index.json:229',
    'C_0805_2012Metric.step': 'index.json:324', 'C_1206_3216Metric.step': 'index.json:3954',
    'C_1210_3225Metric.step': 'index.json:425', 'microSD_HC_Hirose_DM3AT-SF-PEJM5.step': 'index.json:5244',
    'SMA_Molex_73251-1153_EdgeMount_Horizontal.step': 'index.json:381',
    'Crystal_SMD_SeikoEpson_FA238-4Pin_3.2x2.5mm.step': 'index.json:2467', 'D_SMC.step': 'index.json:2258',
    'D_SOD-123.step': 'index.json:2201', 'L_0603_1608Metric.step': 'index.json:1772',
    'LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y.step': 'index.json:1342',
    'SOIC-14_3.9x8.7mm_P1.27mm.step': 'index.json:1677', 'SOIC-16_3.9x9.9mm_P1.27mm.step': 'index.json:4536',
    'SOIC-8_3.9x4.9mm_P1.27mm.step': 'index.json:267', 'TSSOP-14_4.4x5mm_P0.65mm.step': 'index.json:5383',
    'TSSOP-16_4.4x5mm_P0.65mm.step': 'index.json:3366',
    'VSON-10-1EP_3x3mm_P0.5mm_EP1.65x2.4mm.step': 'index.json:577', 'SOT-23-5.step': 'index.json:2144',
    'SOT-23-6.step': 'index.json:2619', 'SOT-23.step': 'index.json:1051', 'SOT-323_SC-70.step': 'index.json:1076',
    'ESP32-S3-WROOM-1.step': 'index.json:1323', 'R_0603_1608Metric.step': 'index.json:286',
    'R_0805_2012Metric.step': 'index.json:925', 'R_1206_3216Metric.step': 'index.json:2657',
    'R_2512_6332Metric.step': 'index.json:539', 'c-1-776163-1-t-3d.stp': 'index.json:2676'}
# The exact names this module's own fixtures publish: the captured fixture board (DiagnosticSupplementTests.capture
# and the GalleryTests boards), the supplement image (DiagnosticSupplementTests.setUp) and the model warning d.step
# (GalleryTests.test_host_paths_in_warnings_coverage_and_viewer_are_published_as_basenames_or_redacted).
FIXTURE_BASENAMES = frozenset(('board.kicad_pcb', 'annotated.png', 'd.step'))


class LeakOracleFailure(AssertionError):
    """The one leak-oracle failure: a published leaf that no field grammar covers."""
    def __init__(self, where, path, value):
        self.where, self.path, self.value = where, path, value
        super().__init__(oracle_message(where, path, value))


def oracle_message(where, path, value):
    return f'leak oracle: {path} = {value!r} matches no published field grammar (surface {where!r})'


class Uncovered(Exception):
    def __init__(self, path, value):
        super().__init__(path, value)
        self.path, self.value = path, value


_FIXTURES = []


@contextlib.contextmanager
def publication_fixtures(probe):
    """Within the block, probe (a string or list of strings) is covered exactly where run_real_publication_test
    injects it: as the review_probe value of a JSON object or list item, and as the text after an HTML page."""
    _FIXTURES.append(probe)
    try:
        yield
    finally:
        _FIXTURES.pop()


def _fixture_json(value):
    return bool(_FIXTURES) and value == _FIXTURES[-1]


def _fixture_text(text):
    probe = _FIXTURES[-1] if _FIXTURES else None
    return probe is not None and text == ''.join('\n' + t for t in ([probe] if isinstance(probe, str) else probe))


def _alternation(values):
    return '(?:' + '|'.join(re.escape(v) for v in sorted(values, key=lambda v: (-len(v), v))) + ')'


def _basename_re():
    return _alternation(set(REAL_BASENAMES) | FIXTURE_BASENAMES)


class Leaf:
    def __init__(self, name, accepts):
        self.name, self.accepts = name, accepts


def literal(*values):
    return Leaf('literal', lambda v: any(type(v) is type(x) and v == x for x in values))


def text(pattern, nullable=False):
    compiled = re.compile(pattern)
    return Leaf(pattern, lambda v: (nullable and v is None) or (isinstance(v, str) and compiled.fullmatch(v) is not None))


def lazy_text(build, nullable=False):
    """A text leaf whose pattern is built on first use (basename alternations change with FIXTURE_BASENAMES)."""
    cache = {}

    def accepts(v):
        if 'pattern' not in cache:
            cache['pattern'] = re.compile(build())
        return (nullable and v is None) or (isinstance(v, str) and cache['pattern'].fullmatch(v) is not None)
    return Leaf('lazy', accepts)


def count(nullable=True):
    return Leaf('count', lambda v: (nullable and v is None) or (type(v) is int and 0 <= v <= _COUNT_MAX))


class Record:
    def __init__(self, fields, optional=()):
        self.fields, self.optional = fields, set(optional)


class ListOf:
    def __init__(self, item):
        self.item = item


class Nullable:
    def __init__(self, grammar):
        self.grammar = grammar


def check_value(value, grammar, path):
    if isinstance(grammar, Nullable):
        if value is not None:
            check_value(value, grammar.grammar, path)
    elif isinstance(grammar, Leaf):
        if not grammar.accepts(value):
            raise Uncovered(path, value)
    elif isinstance(grammar, ListOf):
        if not isinstance(value, list):
            raise Uncovered(path, value)
        for n, item in enumerate(value):
            check_value(item, grammar.item, f'{path}[{n}]')
    elif isinstance(grammar, Record):
        if not isinstance(value, dict):
            raise Uncovered(path, value)
        for key, item in value.items():
            if key not in grammar.fields:
                raise Uncovered(f'{path}.{key}', item)
            check_value(item, grammar.fields[key], f'{path}.{key}')
        missing = set(grammar.fields) - set(value) - grammar.optional
        if missing:
            raise Uncovered(f'{path}.{sorted(missing)[0]}', '<absent>')
    else:
        check_value(value, grammar(value), path)  # a dispatcher returns the grammar for value


LABEL_RE = r'(?:(?:Iteration|Revision|Candidate|Milestone|Diagnostic|Fixture) (?:0|[1-9]\d{0,5})|<redacted>)'
STAGES = {'placement': 'Placement', 'routing': 'Routing in progress', 'routed': 'Routed', 'review': 'Design review',
          'diagnostic': 'Diagnostic', 'fixture': 'Test fixture'}  # render_gallery.py:63
NOTICES = (  # render_gallery.py:50
    'File availability is not proof that KiCad loaded a model; inspect native render logs.',
    'The board hash binds the saved PCB; component models may come from current local KiCad libraries and are not '
    'bound by that hash.', 'Geometry is for visual inspection, not electrical or fabrication acceptance.')
LIMITATION = ('Publisher verifies the retained candidate and input bytes, not the analysis or the adoption of any '
              'proposed change.')  # render_gallery.py:55
ROUTING = {'unrouted': 'Unrouted (verified: the retained board has no tracks or vias)',
           'partially-routed': 'Partially routed (publisher statement; tracks or vias present)',
           'routed': 'Routed (publisher statement; completeness not verified)'}  # render_gallery.py:69
ADOPTION = {'not-adopted': 'Proposed change NOT adopted (publisher statement)',
            'adopted': 'Proposed change adopted (publisher statement, not verified)',
            'no-proposal': 'No change proposed (publisher statement)'}  # render_gallery.py:72
RECORDED = {  # render_gallery.py:82
    'routing': {'routed': ('Routed: the bound board has tracks or vias (completeness not verified)', None),
                'unrouted': ('Unrouted: the bound board has no tracks, arcs or vias', 'Unrouted')},
    'adoption': {'adopted': ('Adopted (recorded disposition)', None),
                 'not-adopted': ('Not adopted (recorded disposition)', 'Not adopted')},
    'qualification': {'qualified': ('Qualified (recorded disposition, not verified by the gallery)', None),
                      'unqualified': ('Not qualified (recorded disposition, not machine qualification)', 'Not qualified')},
    'scope': {'full': ('Full design candidate (recorded)', None),
              'models-only': ('Models-only derivative: only 3D model references changed; placement and structure '
                              'unchanged (recorded)', 'Models-only derivative')}}
DEFECTS = {'silk': 'silkscreen defects (native DRC silk_* violations)', 'drc': 'native DRC violations',
           'review': 'defects open in the recorded review'}  # render_gallery.py:93
PROVENANCE = {'manufacturer': 'manufacturer model', 'mechanical': 'mechanical model',
              'datasheet-derived': 'datasheet-derived visualization model, not manufacturer or mechanical model '
                                   'evidence'}  # render_gallery.py:95
EVIDENCE_ROLES = ('board', 'association', 'receipt', 'design-diff', 'drc', 'index', 'model', 'review')  # :99
VIEWS = ('top', 'bottom', 'isometric')  # render_gallery.py:27


def warning_re():
    """_warning (render_gallery.py:171): a notice, or a rebuilt code with allowlisted parameters."""
    views, n = _alternation(VIEWS), _PIXELS_RE
    return ('(?:' + _alternation(NOTICES) + f'|{_DESIGNATOR_RE}: no 3D model is declared'
            f'|{_DESIGNATOR_RE}: (?:missing|unresolved) 3D model (?:{_basename_re()}|<redacted>)'
            f'|{views}: KiCad produced {n}×{n} for requested {n}×{n}|{views}: <redacted>|<redacted>)')


HASH, ID, TIME = text(_HASH_RE, True), text(_ID_RE, True), text(_TIME_RE, True)
NAME = lazy_text(lambda: f'(?:{_basename_re()}|<redacted>)')  # _name, _basename
PATH_NAME = lazy_text(lambda: f'(?:{_basename_re()}|<redacted>)', nullable=True)
WARNINGS = ListOf(lazy_text(warning_re))
EVIDENCE = ListOf(literal(*EVIDENCE_ROLES))
LABEL, STAGE = text(LABEL_RE), literal(*STAGES, '<redacted>')
STATUS_RECORD = Record({
    'id': ID, 'schema': literal('agent-ee.gallery-status/1', None),
    'subject': Record({'area': literal('bundles', 'diagnostics', None), 'id': ID, 'manifest_sha256': HASH,
                       'board_sha256': HASH}),
    'label': Nullable(LABEL), 'stage': Nullable(STAGE),
    **{key: Record({'value': literal(*vocabulary, None), 'evidence': EVIDENCE}) for key, vocabulary in RECORDED.items()},
    'open_defects': Record({'count': count(), 'class': literal(*DEFECTS, None), 'evidence': EVIDENCE}),
    'drc': Record({'violations': count(), 'raw_unconnected_items': count(), 'evidence': EVIDENCE}),
    'models': ListOf(Record({'reference': text(_DESIGNATOR_RE, True), 'provenance': literal(*PROVENANCE, None),
                             'transparency': literal('verified', None), 'sha256': HASH, 'evidence': EVIDENCE})),
    'evidence': ListOf(Record({'role': literal(*EVIDENCE_ROLES, None), 'name': NAME, 'sha256': HASH})),
    'published_at': TIME}, optional=('id',))
CLAIMS = Record({
    **{key: literal(*vocabulary, None) for key, vocabulary in RECORDED.items()},
    'open_defects': Nullable(Record({'count': count(False), 'class': literal(*DEFECTS)})),
    'drc': Nullable(Record({'violations': count(), 'raw_unconnected_items': count()})),
    'models': ListOf(Record({'reference': text(_DESIGNATOR_RE), 'provenance': literal(*PROVENANCE, None),
                             'transparency': literal('verified', None), 'sha256': HASH}))})
RECORDED_STATUS = Record({'subject': Record({'area': literal('bundles', 'diagnostics'), 'id': ID}),
                          'attached': literal('matched', 'mismatched', 'none'), 'record': Nullable(STATUS_RECORD),
                          'shown': Nullable(CLAIMS)})
BUNDLE = Record({
    'id': ID, 'schema': literal('agent-ee.gallery-bundle/3'), 'label': LABEL, 'stage': STAGE, 'board_sha256': HASH,
    'candidate_id': HASH, 'published_at': TIME, 'rendered_at': TIME,
    'model_coverage': Record({'summary': Record({key: count() for key in (
        'footprints', 'models', 'footprints_fully_resolved', 'resolved', 'missing', 'unresolved', 'disabled',
        'no_model')})}),
    'warnings': WARNINGS,
    'images': ListOf(lambda image: Record({'view': literal(image.get('view') if image.get('view') in VIEWS else ()),
                                           'path': literal(f'{image.get("view")}.png'), 'sha256': HASH})
                     if isinstance(image, dict) else literal()),
    'viewer': literal(True, False), 'recorded_status': RECORDED_STATUS}, optional=('id', 'recorded_status'))
VIEWER = Record({
    'schema': literal('agent-ee.viewer/1', None), 'publication': literal('agent-ee.gallery-viewer/2'),
    'board': Record({'name': PATH_NAME, 'path': PATH_NAME, 'sha256': HASH}), 'candidate_id': HASH,
    'captured_board': PATH_NAME, 'model': literal('board.glb'), 'warnings': WARNINGS,
    'coordinates': Record({'up': literal('Y', 'Z', None), 'units': literal('m', None)}), 'model_sha256': HASH,
    'recorded_status': RECORDED_STATUS}, optional=('recorded_status',))  # served with it, render_gallery.py:1254
DIAGNOSTIC = Record({
    'id': ID, 'schema': literal('agent-ee.gallery-diagnostic/4', None), 'kind': literal('annotated-2d-diagnostic'),
    'label': LABEL, 'stage': STAGE, 'candidate_id': HASH, 'captured_board': PATH_NAME, 'board_sha256': HASH,
    'routing': Record({'status': literal(*ROUTING, None), 'copper_items': count()}),
    'adoption': Record({'status': literal(*ADOPTION, None), 'verified': literal(False)}), 'published_at': TIME,
    'source_packet': Record({'name': NAME, 'sha256': HASH}), 'source_image': Record({'name': NAME, 'sha256': HASH}),
    'image': Record({'path': literal('image.png'), 'width': count(), 'height': count(), 'sha256': HASH}),
    'limitation': literal(LIMITATION), 'recorded_status': RECORDED_STATUS}, optional=('id', 'recorded_status'))


def record_grammar(value):
    """The record a published JSON object is, by the fields only that record has."""
    if isinstance(value, dict):
        for key, grammar in (('attached', RECORDED_STATUS), ('publication', VIEWER), ('kind', DIAGNOSTIC),
                             ('subject', STATUS_RECORD), ('images', BUNDLE)):
            if key in value:
                return grammar
        if value:  # no record has this object's fields: its first field is uncovered
            return Record({})
    return literal()


def check_json(data, path='$'):
    """A published JSON surface: one record, or a list of records (a listing)."""
    if isinstance(data, list):
        for n, item in enumerate(data):
            if not (isinstance(item, dict) and set(item) == {'review_probe'} and _fixture_json(item['review_probe'])):
                check_value(item, record_grammar, f'{path}[{n}]')
    elif isinstance(data, dict) and 'review_probe' in data and _fixture_json(data['review_probe']):
        check_value({k: v for k, v in data.items() if k != 'review_probe'}, record_grammar, path)
    else:
        check_value(data, record_grammar, path)


# --- HTML -------------------------------------------------------------------
PAGE_CSS = ('body{font:16px system-ui;background:#111923;color:#e7eef7;margin:auto;padding:24px;max-width:1400px}'
            'a{color:#8fc9ff}li{overflow-wrap:anywhere}h1{margin-bottom:8px}section{background:#1b2735;padding:24px;'
            'margin:24px 0;border-radius:12px}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));'
            'gap:16px;margin:20px 0}figure{margin:0}img{width:100%;border-radius:8px}figcaption{padding:8px}.hash,pre'
            '{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}.badge{display:inline-block;padding:6px 10px;'
            'border-radius:6px;font-weight:600}.incomplete{background:#7a4b00;color:#fff}.unknown{background:#8a1c1c;'
            'color:#fff}.complete{background:#1e5a3a;color:#fff}.recorded{margin:12px 0}.button{display:inline-block;'
            'padding:12px;background:#294968;border-radius:6px}@media(max-width:700px){body{padding:12px}section'
            '{padding:16px}.images{grid-template-columns:1fr}}')  # render_gallery.py:1170
BANNER_STYLE = ('background:#1b2735;color:#e7eef7;padding:8px 16px;font:14px system-ui;max-height:40vh;'
                'overflow:auto')  # render_gallery.py:1183
ROUTE_RE = (rf'/bundles/{_ID_RE}/(?:top\.png|bottom\.png|isometric\.png|manifest\.json|status\.json|index\.html|'
            rf'viewer/index\.html)|/diagnostics/{_ID_RE}/(?:image\.png|index\.html|manifest\.json)')  # :1214-1216
ATTRIBUTES = {  # tag: {attribute: grammar}; any other tag or attribute is uncovered
    'html': {'lang': literal('en')}, 'body': {}, 'title': {}, 'h1': {}, 'h2': {}, 'strong': {}, 'li': {},
    'details': {}, 'summary': {}, 'figure': {}, 'figcaption': {}, 'style': {},
    'meta': {'charset': literal('utf-8'), 'name': literal('viewport'),
             'content': literal('width=device-width, initial-scale=1')},
    'p': {'class': literal('hash', 'coverage', 'badge unknown', 'badge incomplete', 'badge complete'),
          'role': literal('status')},
    'ul': {'class': literal('status')},
    'div': {'class': literal('images', 'recorded'), 'id': literal('diagnostics'), 'style': literal(BANNER_STYLE)},
    'section': {'id': text(rf'diagnostic-{_ID_RE}')},
    'a': {'href': text(rf'/|#diagnostics|{ROUTE_RE}'), 'class': literal('button'), 'style': literal('color:#8fc9ff')},
    'img': {'src': text(rf'/bundles/{_ID_RE}/(?:top|bottom|isometric)\.png|/diagnostics/{_ID_RE}/image\.png'),
            'alt': text(r'(?:top|bottom|isometric) native 3D view|Annotated 2D diagnostic; open full-size image')}}


def page_text_re():
    """Every text node the page templates emit, with their parameters' grammars."""
    count_t, hash_t, time_t = r'(?:0|[1-9]\d{0,5})', rf'(?:{_HASH_RE}|unknown)', rf'(?:{_TIME_RE}|unknown)'
    stage_t = _alternation([*STAGES.values(), '<redacted>'])
    key_t = _alternation(k.title() for k in RECORDED)
    limit = ('(?:' + f'{key_t} unknown|Unrouted|Not adopted|Not qualified|Models-only derivative|Open defects unknown|'
             f'[1-9]\\d{{0,5}} open defects|{_DESIGNATOR_RE} model (?:datasheet-derived|provenance unknown)|'
             f'{_DESIGNATOR_RE} transparency unknown|Other model provenance unknown)')
    recorded = '|'.join(f'{re.escape(key.title())}: {_alternation(t for t, _ in vocabulary.values())}'
                        for key, vocabulary in RECORDED.items())
    item = (rf'(?:{_alternation(EVIDENCE_ROLES)}|unknown): (?:{_basename_re()}|<redacted>) SHA-256 {hash_t}')
    fixed = _alternation([
        'PCB render gallery', 'PCB render bundle', 'PCB diagnostic supplement', 'Back to render gallery',
        'Native KiCad images · Latest candidate and retained history',
        'Visual inspection only. Missing component models can make geometry incomplete; these images do not approve '
        'electrical or fabrication readiness.', 'Diagnostic supplements',
        'No completed render bundles have been published yet.',
        'Model coverage unknown: do not treat this geometry as complete',
        'Incomplete models: geometry is missing components', 'All declared 3D models resolved',
        'Attached status record',
        'Recorded status is a publisher record bound by hash to retained evidence, not new validation by the gallery.',
        'Interactive viewer not published for this candidate.', 'Open interactive 3D viewer', 'Top', 'Bottom',
        'Isometric', 'Publication manifest', ' · ', 'Recorded status', 'Bundle page',
        'Annotated 2D diagnostic supplement', 'Open diagnostic', 'Diagnostic provenance',
        'Status unknown: not bound to a verified retained candidate, or routing and adoption status are not recorded',
        'No Latest render to compare: treat as historical', 'Same board as the Latest render',
        'Historical: not the board in the Latest render', LIMITATION,
        'Other component models: provenance unknown (not recorded)', 'Native DRC report: unknown (not recorded)',
        'Open defects: unknown (not recorded)',
        *(f'{reason}: routing, adoption, qualification, scope, open defects and model provenance are unknown'
          for reason in ("An attached status record does not match this publication's bytes",
                         'No status record attached'))])
    return '(?:' + '|'.join([
        fixed, rf'(?:Latest: )?{LABEL_RE}', rf'Stage: {stage_t}(?: · Rendered {time_t})?', rf'Board SHA-256: {hash_t}',
        rf'Models: {count_t} resolved; {count_t} missing; {count_t} unresolved; {count_t} disabled; '
        rf'{count_t} footprints without a model\.'.replace(count_t, rf'(?:{count_t}|unknown)'),
        rf'Recorded limitations: {limit}(?:; {limit})*', rf'{key_t}: unknown \(not recorded\)', recorded,
        rf'Open defects: {count_t} {_alternation(DEFECTS.values())} \(recorded\)',
        rf'Native DRC \(recorded report, not re-run by the gallery\): (?:{count_t}|unknown) violations; '
        rf'(?:{count_t}|unknown) raw unconnected items \(not an incomplete-net count\)',
        rf'Model {_DESIGNATOR_RE}: (?:{_alternation(PROVENANCE.values())}|provenance unknown \(not recorded\)); '
        rf'(?:transparency verified|transparency unknown \(not verified\))(?:; model SHA-256 {_HASH_RE})?',
        rf'Retained evidence: {item}(?: · {item})*', rf'Model warnings \({count_t}\)', warning_re(),
        rf'Retained candidate {_HASH_RE}', rf'Routing: {_alternation(ROUTING.values())}',
        rf'Adoption: {_alternation(ADOPTION.values())}', rf'Published {time_t}']) + ')'


class PageParser(HTMLParser):
    """The elements, attributes, text nodes and style content of an HTML surface, as events."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        self.events.append(('tag', '/'.join(self.stack + [tag]), tag, attrs))
        if tag not in ('meta', 'img'):
            self.stack.append(tag)

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.events.append(('end', '/'.join(self.stack), tag, None))
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

    def handle_data(self, data):
        self.events.append(('text', '/'.join(self.stack), data, self.stack[-1] if self.stack else None))

    def handle_decl(self, decl):
        self.events.append(('decl', '/'.join(self.stack), decl, None))

    def unknown_decl(self, data):
        self.events.append(('other', '/'.join(self.stack), data, None))

    handle_comment = handle_pi = unknown_decl


_PAGE_TEXT = {}


def check_html(body, path='html'):
    if 'pattern' not in _PAGE_TEXT:
        _PAGE_TEXT['pattern'] = re.compile(page_text_re())
    parser = PageParser()
    parser.feed(body)
    parser.close()
    for kind, where, value, extra in parser.events:
        here = f'{path}:{where or "(top)"}'
        if kind == 'decl':
            if value != 'doctype html':
                raise Uncovered(here + ' <!...>', value)
        elif kind == 'tag':
            grammar = ATTRIBUTES.get(value)
            if grammar is None:
                raise Uncovered(here + ' <tag>', value)
            names = [name for name, _ in extra]
            for name, attribute in extra:
                if name not in grammar or names.count(name) > 1 or not grammar[name].accepts(attribute):
                    raise Uncovered(f'{here} @{name}', attribute)
        elif kind == 'end':
            if value not in ATTRIBUTES:
                raise Uncovered(here + ' </tag>', value)
        elif kind == 'text':
            if extra == 'style':
                if value != PAGE_CSS:
                    raise Uncovered(here + ' style', value)
            elif not (_PAGE_TEXT['pattern'].fullmatch(value) or _fixture_text(value)):
                raise Uncovered(here + ' text', value)
        else:
            raise Uncovered(here + ' markup', value)


_SEGMENT_END = re.compile(r'>(?=[\[{])')


def _unique_pairs(pairs):
    """A JSON object, refused when a key repeats (the parsed tree would keep only one of the published values)."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError('repeated key')
    return dict(pairs)


def _no_constant(name):
    raise ValueError(f'non-JSON constant {name}')


def check_publication(body, where):
    """Parse body (one surface, or surfaces as a test concatenates them) and cover every leaf, else
    LeakOracleFailure naming the first uncovered field path and value."""
    decoder, p, n = json.JSONDecoder(object_pairs_hook=_unique_pairs, parse_constant=_no_constant), 0, 0
    try:
        while p < len(body):
            if body[p].isspace():
                p += 1
                continue
            if body[p] in '[{':
                try:
                    data, end = decoder.raw_decode(body, p)
                except ValueError:
                    raise Uncovered(f'surface[{n}]', body[p:])
                check_json(data, f'surface[{n}]$')
            elif body[p] == '<':
                match = _SEGMENT_END.search(body, p)
                end = match.end() if match else len(body)
                check_html(body[p:end], f'surface[{n}]')
            else:
                raise Uncovered(f'surface[{n}]', body[p:])
            p, n = end, n + 1
    except Uncovered as uncovered:
        raise LeakOracleFailure(where, uncovered.path, uncovered.value) from None


def assert_no_uri_leak(test, body, where):
    check_publication(body, where)
    for leak in LEAKS:
        test.assertNotIn(leak, body.lower() if leak == 'file:' else body, where)


# The Diagnostic 6 bundle as the render job published it (2026-10-22, run 20261022T231757Z): its stored manifest
# and attached status record, rebuilt here and pinned by the digests that name them.
D6_ID, D6_STATUS_ID = 'adfb7668ac0c-86b84f0f741e', '86b84f0f741e-50362385ee3a'
D6_MANIFEST_SHA256 = '86b84f0f741e099d56a6a4c6464844f9a85106860f45df599344b635330c529a'
D6_STATUS_SHA256 = '50362385ee3ab65611767f547bc9ebdfd5a6bc04a3141c3b1cd95d1af6d433a8'
D6_BOARD = 'adfb7668ac0c80dc1b4f1d2f97afa86b193f630c545bf43cbc0de8f49f351a40'
D6_UNMODELLED = ('TP4', 'U19', 'U6', 'H1', 'JP5', 'H3', 'L1', 'U18', 'JP3', 'U5', 'TP6', 'H4', 'JP4', 'U12', 'U7',
                 'TP1', 'JP2', 'D3', 'L2', 'Q1', 'H2', 'U16', 'TP2', 'JP1', 'TP3', 'TP5')
D6_IMAGES = (('top', 'f632ac9658d4af4f3aec4935454d8f16bc053d65b1813bc06dbc3aa4c2a55f0d'),
             ('bottom', '1f6d59bfe554a645a34d233dae2532c4e8df8ac5ee2366542b6efa4f47ced5e5'),
             ('isometric', 'c28e7ba4d1e9c4ff59e01dc651f1ee8900192bde9325f7a49ce5f6b81d359b04'))


def diagnostic_6():
    """(manifest, status record) of the Diagnostic 6 bundle."""
    manifest = {
        'schema': 'agent-ee.gallery-bundle/3', 'label': 'Diagnostic 6', 'stage': 'diagnostic', 'board_sha256': D6_BOARD,
        'candidate_id': None, 'published_at': '2026-10-22T23:18:18.401181+00:00',
        'rendered_at': '2026-10-22T23:18:18.189096+00:00',
        'model_coverage': {'summary': {'footprints': 309, 'models': 283, 'footprints_fully_resolved': 283,
                                       'resolved': 283, 'missing': 0, 'unresolved': 0, 'disabled': 0, 'no_model': 26}},
        'warnings': ['File availability is not proof that KiCad loaded a model; inspect native render logs.']
        + [r + ': no 3D model is declared' for r in D6_UNMODELLED]
        + [v + ': KiCad produced 2384×1568 for requested 2400×1600' for v, _ in D6_IMAGES],
        'images': [{'view': v, 'path': v + '.png', 'sha256': h} for v, h in D6_IMAGES], 'viewer': False}
    status = {
        'schema': 'agent-ee.gallery-status/1',
        'subject': {'area': 'bundles', 'id': D6_ID, 'manifest_sha256': D6_MANIFEST_SHA256, 'board_sha256': D6_BOARD},
        'label': None, 'stage': None, 'routing': {'value': 'unrouted', 'evidence': ['board']},
        'adoption': {'value': None, 'evidence': []}, 'qualification': {'value': 'unqualified', 'evidence': ['receipt']},
        'scope': {'value': None, 'evidence': []}, 'open_defects': {'count': None, 'class': None, 'evidence': []},
        'drc': {'violations': None, 'raw_unconnected_items': None, 'evidence': []}, 'models': [],
        'evidence': [
            {'role': 'board', 'name': 'telemetry-pod.kicad_pcb', 'sha256': D6_BOARD},
            {'role': 'association', 'name': 'render-job-association_adfb7668ac0c_20261022T231757Z.json',
             'sha256': '18aa0244d76e069367c3f6b0d44386027a3e413a7b58707c4020fa627ba595d3'},
            {'role': 'receipt', 'name': 'rj_diagnostic-6_305fp+4holes_unrouted_not-qualified_new-board_20261022T231757Z.json',
             'sha256': '9e49e103cd90ef9221b42f6f54b929b3e4634431696ca60faae7c38ddc71584d'}],
        'published_at': '2026-10-22T23:18:18.509044+00:00'}
    return manifest, status




def bundle_record(**changes):
    """A published bundle record, as _bundle rebuilds it, with changes."""
    record = gallery._bundle({'id': '0' * 12 + '-' + '1' * 12, 'label': 'Iteration 1', 'stage': 'placement',
                              'board_sha256': 'a' * 64, 'published_at': '2026-10-22T23:18:18Z',
                              'warnings': ['R1: missing 3D model board.kicad_pcb'], 'images': [{'view': 'top'}]})
    record.update(changes)
    return record


class StructuralOracleTests(unittest.TestCase):
    def test_diagnostic_6_publication_is_covered_as_published(self):
        """F1: the real Diagnostic 6 JSON and pages, stored and served, are covered with no spelling workaround."""
        manifest, status = diagnostic_6()
        manifest_bytes, status_bytes = gallery._encode(manifest), gallery._encode(status)
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(), D6_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256(status_bytes).hexdigest(), D6_STATUS_SHA256)
        self.assertEqual(D6_STATUS_ID, D6_MANIFEST_SHA256[:12] + '-' + D6_STATUS_SHA256[:12])
        self.assertIn(b'2384\\u00d71568', manifest_bytes)  # the stored spelling of "2384×1568", checked as is
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bundles' / D6_ID).mkdir(parents=True)
            (root / 'statuses' / D6_STATUS_ID).mkdir(parents=True)
            (root / 'bundles' / D6_ID / 'manifest.json').write_bytes(manifest_bytes)
            (root / 'index.json').write_bytes(gallery._encode([{'id': D6_ID, **manifest}]))
            (root / 'statuses' / D6_STATUS_ID / 'status.json').write_bytes(status_bytes)
            (root / 'statuses.json').write_bytes(gallery._encode([{'id': D6_STATUS_ID, **status}]))
            stored = [p.read_text() for p in sorted(root.rglob('*.json'))]
            server = serve(root, '127.0.0.1', 0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                served = [urlopen(base + path).read().decode() for path in (
                    '/', f'/bundles/{D6_ID}/manifest.json', f'/bundles/{D6_ID}/index.html',
                    f'/bundles/{D6_ID}/status.json', f'/statuses/{D6_STATUS_ID}/status.json')]
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
        self.assertEqual((len(stored), json.loads(served[3])['attached']), (4, 'matched'))
        self.assertIn('Retained evidence: board: telemetry-pod.kicad_pcb', served[0])
        for n, body in enumerate(stored + served):
            check_publication(body, f'diagnostic 6 surface {n}')

    def test_every_variable_allowance_has_a_staged_witness(self):
        for name, witness in REAL_BASENAMES.items():
            self.assertRegex(witness, r'^(index|statuses)\.json:[1-9]\d*$', name)
        self.assertEqual(set(REAL_BASENAMES) & FIXTURE_BASENAMES, set())
        for value in ('example.org', 'example.com', 'user@host', 'Hidden.step', 'intranet.corp.step'):
            self.assertNotIn(value, set(REAL_BASENAMES) | FIXTURE_BASENAMES)

    def test_an_uncovered_leaf_fails_with_the_one_message_naming_path_and_value(self):
        record = bundle_record(warnings=['R1: missing 3D model intranet.corp.step'])
        with self.assertRaises(LeakOracleFailure) as caught:
            check_publication(json.dumps(record), 'probe')
        self.assertEqual(str(caught.exception), "leak oracle: surface[0]$.warnings[0] = "
                         "'R1: missing 3D model intranet.corp.step' matches no published field grammar "
                         "(surface 'probe')")
        for changes, path in (({'board_sha256': '0x0a000001'}, '$.board_sha256'),
                              ({'model_coverage': {'summary': {**record['model_coverage']['summary'], 'models': 0.5}}},
                               '$.model_coverage.summary.models'),
                              ({'review_probe': 'x'}, '$.review_probe'), ({'label': 'Iteration 01'}, '$.label')):
            with self.assertRaises(LeakOracleFailure) as caught:
                check_publication(json.dumps(bundle_record(**changes)), 'probe')
            self.assertEqual(caught.exception.path, 'surface[0]' + path)

    def test_generated_ids_and_hashes_containing_fd00_are_covered(self):
        record = bundle_record(id='a1fd00b2c3d4-e5f6fd00a1b2', board_sha256='fd00' + '0' * 60,
                               candidate_id='0' * 12 + 'fd00' + '0' * 48)
        check_publication(json.dumps(record), 'hex ids')
        rng = random.Random(452)
        for _ in range(200):
            value = ''.join(rng.choices('0123456789abcdef', k=12)) + 'fd00' + ''.join(rng.choices('0123456789abcdef', k=48))
            check_publication(json.dumps(bundle_record(board_sha256=value)), value)

    def test_pages_are_covered_and_every_markup_change_fails(self):
        manifest, status = diagnostic_6()
        body = page([{'id': D6_ID, **manifest}]).decode()
        check_publication(body, 'page')
        for old, new in (('<h2>', '<h2 title="x">'), ('href="/bundles/', 'href="https://example.org/bundles/'),
                         (PAGE_CSS, PAGE_CSS.replace('}', ':root{--endpoint:10.0.0.1}', 1)),
                         ('<p>Visual', '<!-- x --><p>Visual'), ('</html>', '<script>x</script></html>'),
                         ('Board SHA-256: ', 'Board SHA-256: 0x0a000001 ')):
            self.assertIn(old, body)
            with self.assertRaises(LeakOracleFailure, msg=new):
                check_publication(body.replace(old, new, 1), new)

    def test_concatenated_surfaces_are_each_parsed(self):
        assert_no_uri_leak(self, surfaces(next(corpus())), 'composed')
        for extra in ('x', '{"export": 1}', '<b>x</b>', ' 0.5', ' NaN', '[Infinity]'):
            with self.assertRaises(LeakOracleFailure, msg=extra):
                check_publication(surfaces('m.step') + extra, extra)
        record = json.dumps(bundle_record())
        repeated = record.replace('"label": "Iteration 1"', '"label": "intranet.corp", "label": "Iteration 1"')
        self.assertNotEqual(repeated, record)
        with self.assertRaises(LeakOracleFailure):  # json.loads would keep only the covered value
            check_publication(repeated, 'repeated key')


REAL_PUBLICATION_TEST = 'test_real_packet_and_image_names_are_published_as_plain_basenames_or_redacted'


def inject(body, probe, mode):
    """body with probe placed as review rounds 1-3 did (append: a review_probe field in each JSON object, an item in
    each JSON list, text after each page), into a published field (field: source_packet.name, the card label), or
    into an attribute value (attribute: captured_board, the first href). A number goes in image.width."""
    try:
        data = json.loads(body)
    except ValueError:
        shown = html.escape(str(probe))
        if mode == 'append':
            return body + ''.join('\n' + str(t) for t in (probe if isinstance(probe, list) else [probe]))
        if mode == 'field':
            changed = re.sub(r'<h2>[^<]*</h2>', lambda _: '<h2>' + shown + '</h2>', body, count=1)
        else:
            changed = re.sub(r'href="[^"]*"', lambda _: 'href="' + shown + '"', body, count=1)
        assert changed != body, (mode, probe)
        return changed
    records = data if isinstance(data, list) else [data]
    for record in records:
        if mode == 'append':
            continue
        if not isinstance(probe, str):
            record['image']['width'] = probe
        elif mode == 'field':
            record['source_packet']['name'] = probe
        else:
            record['captured_board'] = probe
    if mode == 'append':
        if isinstance(data, list):
            data.append({'review_probe': probe})
        else:
            data['review_probe'] = probe
    return json.dumps(data)


def run_real_publication_test(probe, mode='append', fixture=False):
    """(testsRun, failures, errors, skips, failure text) of DiagnosticSupplementTests.REAL_PUBLICATION_TEST with probe
    injected into every surface it checks; with fixture, probe is registered as the exact control value."""
    original = DiagnosticSupplementTests.surfaces

    def surfaces(self, *args, **kwargs):
        return [inject(body, probe, mode) for body in original(self, *args, **kwargs)]
    result = unittest.TestResult()
    with mock.patch.object(DiagnosticSupplementTests, 'surfaces', surfaces), (
            publication_fixtures(probe) if fixture else contextlib.nullcontext()):
        DiagnosticSupplementTests(REAL_PUBLICATION_TEST).run(result)
    detail = ''.join(text for _, text in result.failures + result.errors)
    return result.testsRun, len(result.failures), len(result.errors), len(result.skipped), detail


class LeakRegressionTests(unittest.TestCase):
    """The committed regression set: every probe case of review rounds 1-4, each with its round and finding, and
    the coordinator's required cases. Round 1: reviews/ee452b-gallery-fd00-probes (adversarial.py,
    compare_oracle.py); round 2: ee452c-tokenize-probes (adversarial.py, parser_boundaries.py); round 3:
    ee452d-uri-failclosed-probes (adversarial.py, additional.py, boundaries.py); round 4: ee452e-allowlist-oracle-probes
    (independent.py, supplemental.py; findings H1-H5); g: the allow-list round's own grammar rows; req: the
    coordinator's mandatory list. Each case must fail the real publication test in every injection mode with the
    leak-oracle message."""
    CASES = (
        ('r1-01', 'r1 H1', 'fd00::1'), ('r1-02', 'r1 H1', 'host:fd00::1'), ('r1-03', 'r1 H1', 'fd00::1:'),
        ('r1-04', 'r1', 'http://[fd00::1]/x'), ('r1-05', 'r1', 'fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'),
        ('r1-06', 'r1', 'http://localhost/x'), ('r1-07', 'r1', 'file:///m.step'), ('r1-08', 'r1', 'server-hidden'),
        ('r1-09', 'r1', 'http://server.example/x'), ('r1-10', 'r1 H2', 'fcff::1'), ('r1-11', 'r1 H2', 'fe00::1'),
        ('r2-01', 'r2', '[fd00::1]:8080'), ('r2-02', 'r2', 'beef:fd00::1'), ('r2-03', 'r2', 'fd00::1%eth0'),
        ('r2-04', 'r2', 'http://[fd00::1%25eth0]/'), ('r2-05', 'r2', 'FD00::1'), ('r2-06', 'r2', '::ffff:192.168.1.2'),
        ('r2-07', 'r2', 'http://[::ffff:192.168.1.2]/'), ('r2-08', 'r2', 'http://[::ffff:127.0.0.1]/'),
        ('r2-09', 'r2 H3', 'http://[broken/'), ('r2-10', 'r2 H3', 'http://example.org:bad/'), ('r2-11', 'r2 H3', 'http://'),
        ('r2-12', 'r2', 'FILE:///x'), ('r2-13', 'r2', 'smb://bob@srv/x'), ('r2-14', 'r2', 'http://LocalHost:80/x'),
        ('r2-15', 'r2', 'http://PRIVATE-host/x'), ('r2-16', 'r2', 'http://HIDDEN/x'), ('r2-17', 'r2 H2', 'LOCALHOST'),
        ('r2-18', 'r2 H2', '//LOCALHOST/x'), ('r2-19', 'r2 H2', 'http://%6cocalhost/x'), ('r2-20', 'r2 H2', 'http://%68idden/x'),
        ('r2-21', 'r2', 'http://%66%64%30%30%3a%3a1/'), ('r2-22', 'r2 H1', 'https://bob;secret@example.org/x'),
        ('r2-23', 'r2 H1', 'https://bob,secret@example.org/x'), ('r2-24', 'r2', 'https://bob@example.org/x'),
        ('r2-25', 'r2 H3', 'http://example.org:65536/'),
        ('r3-01', 'r3 H1', "https://bob'pass@example.org/x"), ('r3-02', 'r3 H1', "https://bob'pass:secret@example.org/x"),
        ('r3-03', 'r3 H2', 'LOCALHOST:80'), ('r3-04', 'r3', 'http://[::1]junk/'), ('r3-05', 'r3', 'http:/example.org/x'),
        ('r3-06', 'r3', 'http://example.org:-1/'), ('r3-07', 'r3', 'https:///x'), ('r3-08', 'r3', 'https://'),
        ('r3-09', 'r3 H3', 'http:/LOCALHOST/x'), ('r3-10', 'r3 H3', 'http:LOCALHOST'),
        ('r3-11', 'r3', 'http://%6C%6F%63%61%6C%68%6F%73%74:80/'), ('r3-12', 'r3', '%6c%6f%63%61%6c%68%6f%73%74'),
        ('r3-13', 'r3', 'HIDDEN'), ('r3-14', 'r3 gap', 'Hidden component count is zero'),
        ('r4-01', 'r4 H1', '10.23.45.67'), ('r4-02', 'r4 H1', '192.168.23.45'), ('r4-03', 'r4 H1', '172.16.23.45'),
        ('r4-04', 'r4 H1', '169.254.23.45'), ('r4-05', 'r4 H1', 'fe80::23%eth0'), ('r4-06', 'r4 H1', '::ffff:10.0.0.1'),
        ('r4-07', 'r4 H1', '012.000.000.001'), ('r4-08', 'r4 H1', '0x0a000001'), ('r4-09', 'r4 H1', '167772161'),
        ('r4-10', 'r4 H1', 'https://012.000.000.001/'), ('r4-11', 'r4 H1', 'https://0x0a000001/'),
        ('r4-12', 'r4 H1', 'LOCALHOST.'), ('r4-13', 'r4 H1', 'https://l\u043ecalhost/'),
        ('r4-14', 'r4 H1', 'data:text/plain,secret'), ('r4-15', 'r4 H1', 'mailto:bob@buildbox'),
        ('r4-16', 'r4 H1', 'git@buildbox:repo'), ('r4-17', 'r4 H1', 'smb://buildbox/share'),
        ('r4-18', 'r4 H1', '\\\\server\\share'), ('r4-19', 'r4 H1', '/home/bob/projects/board'),
        ('r4-20', 'r4 H1', '/home/bob/projects/board.step'), ('r4-21', 'r4 H2', 'https://example.org/?token=LOCALHOST'),
        ('r4-22', 'r4 H2', 'https://example.org/?token=/home/bob/projects/board'),
        ('r4-23', 'r4 H2', 'https://example.org/?token=intranet.corp'), ('r4-24', 'r4 H1', 'https:'),
        ('r4-25', 'r4 H1', 'http:'), ('r4-26', 'r4 H3', '<style>.10.0.0.1{color:red}</style>'),
        ('r4-27', 'r4 H3', '<div style="--endpoint:fe80::1">'), ('r4-28', 'r4 H1', 'intranet.corp.step'),
        ('r4-29', 'r4 H1', '%252525256cocalhost'), ('r4-30', 'r4 H3', '<style>:root{--endpoint:10.0.0.1}</style>'),
        ('r4-31', 'r4 H5', 'user@buildbox'), ('r4-32', 'r4 H5', '0.5'), ('r4-33', 'r4 H5', '1e20'),
        ('g-01', 'g', 'https:example.org'), ('g-02', 'g', 'HTTPS://LOCALHOST/'), ('g-03', 'g', 'mailto:bob@example.org'),
        ('g-04', 'g', 'https://bob!x@example.org/'), ('g-05', 'g', 'https://b%40b@example.org/'),
        ('g-06', 'g', 'intranet.corp:443'), ('g-07', 'g', 'https://example.org.evil.test/'), ('g-08', 'g', '10.0.0.1'),
        ('req-01', 'req', 'https://example.org/?q=%2Fhome%2Fbob'), ('req-02', 'req', 'https://%2525252e/'),
        ('req-03', 'req', 'bob@intranet.corp'), ('req-04', 'req H5', 0.5), ('req-05', 'req H5', 1e20))
    # The coordinator's clean controls: exact fixture values, covered only where a test registers them.
    CONTROLS = ('email@example.com in prose', 'commit: user@host', 'version fd00a1b', 'a1fd00b2c3d4e5f6 plain prose',
                'https://example.org/x', 'https://example.org:0/x', 'https://example.org:65535/x',
                'https://example.org:65535', 'https://example.org:8443/a;b,c', 'see (https://example.org)',
                'see (https://example.org:8443)', '(https://example.org:65535)', "'https://example.org/x'",
                'Hidden.step', 'Use // for integer division', 'a//')
    MESSAGE = re.compile(r"LeakOracleFailure: leak oracle: \S.* = .+ matches no published field grammar \(surface .+\)")

    def test_each_case_fails_the_real_publication_test_in_every_injection_mode(self):
        for row, finding, value in self.CASES:
            for mode in ('append', 'field', 'attribute'):
                ran, failures, errors, skips, detail = run_real_publication_test(value, mode)
                self.assertEqual((ran, failures, errors, skips), (1, 1, 0, 0), (row, finding, value, mode, detail))
                self.assertRegex(detail, self.MESSAGE, (row, mode))

    def test_registered_controls_pass_the_real_publication_test(self):
        ran, failures, errors, skips, detail = run_real_publication_test(list(self.CONTROLS), fixture=True)
        self.assertEqual((ran, failures, errors, skips), (1, 0, 0, 0), detail)

    def test_controls_are_exact_fixtures_not_allowances(self):
        """Unregistered, or registered at another value, a control fails like any uncovered leaf."""
        for probe, fixture in ((list(self.CONTROLS), False), (list(self.CONTROLS) + ['user@buildbox'], False),
                               ('Hidden.step', False), ('example.org', False)):
            ran, failures, errors, skips, detail = run_real_publication_test(probe, fixture=fixture)
            self.assertEqual((ran, failures, errors, skips), (1, 1, 0, 0))
            self.assertRegex(detail, self.MESSAGE)
        with publication_fixtures(['https://example.org/x']):
            with self.assertRaises(LeakOracleFailure):
                check_publication(json.dumps(bundle_record(review_probe=['https://example.org/y'])), 'other value')
            with self.assertRaises(LeakOracleFailure):  # an exact control is covered only where it was injected
                check_publication(json.dumps(bundle_record(label='https://example.org/x')), 'other place')


def records(text):
    """The published records of a bundle, viewer and supplement with text in every field and key."""
    warnings = [text, 'R1: missing 3D model ' + text, 'R2: unresolved 3D model ' + text, 'top: ' + text,
                text + ': no 3D model is declared', 'bottom: KiCad produced 1×1 for requested ' + text]
    bundle = gallery._bundle({
        'id': '0' * 12 + '-' + '1' * 12, 'label': text, 'stage': text, 'board_sha256': text, 'candidate_id': text,
        'published_at': text, 'rendered_at': text, 'warnings': warnings, 'viewer': text, text: text,
        'model_coverage': {'board': {'path': text}, 'footprints': [{'reference': text}], text: text,
                           'summary': {'resolved': text, text: 1}},
        'images': [{'view': 'top', 'path': text, 'sha256': text}, {'view': text, 'path': 'x'}]})
    viewer = gallery._viewer({
        'schema': 'agent-ee.viewer/1', 'board': {'name': text, 'path': text, 'sha256': text, text: text},
        'candidate_id': text, 'captured_board': text, 'model': text, 'warnings': warnings, 'model_sha256': text,
        'coordinates': {'up': text, 'units': text}, 'export': {'command': [text], 'project_directory': text}, text: text})
    diagnostic = gallery._diagnostic({
        'id': '2' * 12 + '-' + '3' * 12, 'schema': 'agent-ee.gallery-diagnostic/2', 'kind': text, 'label': text,
        'stage': text, 'candidate_id': text, 'captured_board': text, 'board_sha256': text, 'published_at': text,
        'routing': {'status': text, 'copper_items': text}, 'adoption': {'status': text, 'verified': text},
        'source_packet': {'name': text, 'sha256': text, text: text}, 'source_image': {'name': text},
        'image': {'path': text, 'width': text}, 'limitation': text, text: text})
    return bundle, viewer, diagnostic


def status_record(text):
    """An attached status record with text in every field, key and evidence name, as projected."""
    cited = {'value': text, 'evidence': [text, 'board']}
    return gallery._status_record({
        'id': '4' * 12 + '-' + '5' * 12, 'schema': 'agent-ee.gallery-status/1', text: text,
        'subject': {'area': text, 'id': text, 'manifest_sha256': text, 'board_sha256': text, text: text},
        'label': text, 'stage': text, 'routing': cited, 'adoption': cited, 'qualification': cited, 'scope': cited,
        'open_defects': {'count': text, 'class': text, 'evidence': [text]},
        'drc': {'violations': text, 'raw_unconnected_items': text, 'evidence': [text], text: text},
        'models': [{'reference': text, 'provenance': text, 'transparency': text, 'sha256': text, 'evidence': [text]},
                   {'reference': 'U3', 'provenance': text, 'evidence': ['model']}, text],
        'evidence': [{'role': text, 'name': text, 'sha256': text, 'path': text}, {'role': 'model', 'name': text}],
        'published_at': text})


def surfaces(text):
    """Everything publication or the server can emit for text: stored and served JSON and both pages."""
    bundle, viewer, diagnostic = records(text)
    status = status_record(text)
    statuses = {('bundles', bundle['id']): status, ('diagnostics', diagnostic['id']): status}
    return (json.dumps([bundle, viewer, diagnostic, status], ensure_ascii=False)
            + page([bundle], [diagnostic], statuses=statuses).decode()
            + page([], [diagnostic], diagnostic_only=True, latest=bundle, statuses=statuses).decode()
            + page([bundle], statuses=statuses, detail=True).decode()
            + gallery._with_status(b'<body>', bundle['id'], status).decode()
            + json.dumps(gallery._recorded('bundles', bundle['id'], status), ensure_ascii=False))


def assert_leak_free(test, published, seed=433, rounds=20000, stride=1):
    """The oracle: no composed or seeded random text leaves a host marker in what is published."""
    for text in itertools.islice(itertools.chain(corpus(), random_texts(seed, rounds)), 0, None, stride):
        assert_no_uri_leak(test, published(text), text)


def assert_leak_detected(test, published, **options):
    """The negative-control contract: assert_leak_free(published) fails with the one leak-oracle failure, whose
    message is exactly the oracle's line for the surface, field path and value it names. Returns the failure."""
    with test.assertRaises(LeakOracleFailure) as caught:
        assert_leak_free(test, published, **options)
    failure = caught.exception
    test.assertEqual(str(failure), oracle_message(failure.where, failure.path, failure.value))
    return failure


def publish_in_process(root, renders, label):
    publish(Path(root), Path(renders), label=label, stage='placement')


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'gallery'
        self.renders = Path(self.tmp.name) / 'renders'
        self.renders.mkdir()
        self.board_hash = 'a' * 64
        (self.renders / 'snapshot').mkdir()
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        staged.write_text('staged board')
        images, views = [], {}
        for shade, view in enumerate(('top', 'bottom', 'isometric'), 1):
            p = self.renders / f'board-3d-{view}.png'
            p.write_bytes(png_bytes(shade * 40))
            images.append(dict(view=view, path=p.name, width=2, height=2, sha256=digest(p)))
            views[view] = dict(images[-1], status='completed')
            self.write_receipt(view, views[view])
        self.manifest = dict(
            schema='agent-ee.render3d/1', status='completed', errors=[], source_unchanged=True,
            board={'name': 'board.kicad_pcb', 'path': HOST + '/board.kicad_pcb', 'sha256': self.board_hash},
            candidate_id=None, captured_board=None, images=images, views=views, settings={'width': 2, 'height': 2},
            staged_board_sha256=digest(staged),
            snapshot={'board': {'path': 'snapshot/board.kicad_pcb', 'sha256': digest(staged)}},
            warnings=['R1: missing 3D model ' + HOST + '/models/r.step'],
            model_coverage={'board': {'path': HOST + '/board.kicad_pcb'},
                            'summary': {'resolved': 3, 'missing': 1, 'unresolved': 2, 'disabled': 4, 'no_model': 5}})
        self.save()

    def write_receipt(self, view, record):
        command = ['kicad-cli', 'pcb', 'render', '--width', '2', '--height', '2', *SIDES[view],
                   '--output', HOST + '/renders/' + record['path'], HOST + '/work/' + view + '/board.kicad_pcb']
        (self.renders / f'{view}.json').write_text(json.dumps(dict(record, command=command)))

    def save(self):
        (self.renders / 'render-manifest.json').write_text(json.dumps(self.manifest))

    def add(self):
        return publish(self.root, self.renders, label='Candidate 1', stage='diagnostic')

    def test_history_and_failed_import_preserves_latest(self):
        first = self.add()
        second = self.add()
        self.assertNotEqual(first, second)
        before = (self.root/'index.json').read_bytes()
        (self.renders/'board-3d-top.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.add()
        self.assertEqual(before, (self.root/'index.json').read_bytes())
        self.assertTrue((self.root/'bundles'/first/'top.png').is_file())
        self.assertEqual(list(self.root.glob('.publish-*')), [])

    def test_refuses_incomplete_and_traversal_and_symlinks(self):
        self.manifest['status'] = 'running'
        self.save()
        with self.assertRaises(ValueError): self.add()
        self.manifest['status'] = 'completed'
        self.manifest['images'][0]['path'] = '../secret'
        self.save()
        with self.assertRaises(ValueError): self.add()
        self.manifest['images'][0]['path'] = 'board-3d-top.png'
        self.save()
        (self.renders/'board-3d-top.png').unlink()
        (self.renders/'board-3d-top.png').symlink_to(self.renders/'board-3d-bottom.png')
        with self.assertRaises(ValueError): self.add()

    def test_mismatched_viewer_does_not_publish(self):
        viewer = Path(self.tmp.name)/'viewer'
        viewer.mkdir()
        (viewer/'viewer.json').write_text(json.dumps({'schema':'agent-ee.viewer/1', 'board':{'sha256':'b'*64}}))
        with self.assertRaisesRegex(ValueError, 'different board'):
            publish(self.root, self.renders, label='Iteration 2', stage='placement', viewer=viewer)
        self.assertFalse((self.root/'index.json').exists())
        self.assertEqual(list((self.root/'bundles').iterdir()), [])

    def test_http_scope_and_escaped_labels(self):
        bundle = self.add()
        httpd = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        url = f'http://127.0.0.1:{httpd.server_port}'
        with urlopen(url) as response:
            body = response.read()
        self.assertIn(b'Latest: Candidate 1</h2><p>Stage: Diagnostic', body)
        listing = json.loads((self.root / 'index.json').read_text())
        (self.root / 'index.json').write_text(json.dumps([dict(listing[0], label='<b>x</b>', stage='<i>')]))
        with urlopen(url) as response:
            body = response.read()
        self.assertIn(b'Latest: &lt;redacted&gt;</h2><p>Stage: &lt;redacted&gt;', body)
        self.assertNotIn(b'<b>', body)
        with urlopen(url+'/bundles/'+bundle+'/top.png') as response:
            self.assertEqual(response.status, 200)
        for path in ('/.publish.lock','/index.json','/bundles/','/../etc/passwd', '/bundles/'+bundle+'/secret.txt'):
            with self.assertRaises(HTTPError) as error: urlopen(url+path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(ValueError): serve(self.root, '0.0.0.0', 0)

    # --- G1: coverage visible outside the collapsed details -----------------
    def visible_html(self, coverage):
        item = dict(id='0'*12+'-'+'1'*12, label='L', stage='S', board_sha256='a'*64, rendered_at='t',
                    model_coverage=coverage, warnings=['W1: no 3D model is declared'], images=[], viewer=False)
        text = page([item]).decode()
        return text.split('<details>')[0], text

    def test_incomplete_coverage_badge_is_outside_details(self):
        visible, text = self.visible_html({'summary': {'resolved': 3, 'missing': 1, 'unresolved': 2, 'disabled': 4, 'no_model': 5}})
        self.assertIn('Incomplete models', visible)
        for expected in ('3 resolved', '1 missing', '2 unresolved', '4 disabled', '5 footprints without a model'):
            self.assertIn(expected, visible)
        self.assertNotIn('W1', visible)
        self.assertIn('W1', text)

    def test_unknown_or_absent_coverage_is_never_shown_complete(self):
        for coverage in (None, {}, {'summary': {'resolved': 3, 'missing': 0, 'unresolved': 0, 'no_model': 0}},
                         {'summary': {'resolved': 3, 'missing': 'x', 'unresolved': 0, 'disabled': 0, 'no_model': 0}}):
            visible, _ = self.visible_html(coverage)
            self.assertIn('Model coverage unknown', visible)
            self.assertNotIn('All declared 3D models resolved', visible)
            self.assertIn('unknown', visible)

    def test_fully_resolved_coverage_badge(self):
        visible, _ = self.visible_html({'summary': {'resolved': 3, 'missing': 0, 'unresolved': 0, 'disabled': 0, 'no_model': 0}})
        self.assertIn('All declared 3D models resolved', visible)

    # --- G2: manifest provenance and candidate binding ----------------------
    def refuses(self, pattern, **kwargs):
        before = (self.root / 'index.json').read_bytes() if (self.root / 'index.json').exists() else None
        with self.assertRaisesRegex(ValueError, pattern):
            publish(self.root, self.renders, label='Iteration 1', stage='placement', **kwargs)
        after = (self.root / 'index.json').read_bytes() if (self.root / 'index.json').exists() else None
        self.assertEqual(before, after)
        self.assertEqual(list(self.root.glob('.publish-*')), [])

    def test_self_consistent_edited_manifest_pointing_top_at_bottom_is_refused(self):
        bottom = self.renders / 'board-3d-bottom.png'
        self.manifest['images'][0] = dict(self.manifest['images'][1], view='top')
        self.manifest['views']['top'] = dict(self.manifest['images'][0], status='completed')
        self.save()
        self.refuses('not bound to its native view record')
        # Same bytes under the expected filename is also refused (not distinct).
        (self.renders / 'board-3d-top.png').write_bytes(bottom.read_bytes())
        self.manifest['images'][0]['path'] = 'board-3d-top.png'
        self.manifest['views']['top'] = dict(self.manifest['images'][0], status='completed')
        self.save()
        self.refuses('distinct')

    def test_manifest_recording_errors_changed_source_or_bad_view_is_refused(self):
        self.manifest['errors'] = ['boom']; self.save()
        self.refuses('errors or a changed source')
        self.manifest['errors'] = []; self.manifest['source_unchanged'] = False; self.save()
        self.refuses('errors or a changed source')
        self.manifest['source_unchanged'] = True; self.manifest['views']['top']['status'] = 'failed'; self.save()
        self.refuses('native view record')

    def test_dimension_and_png_forgery_is_refused(self):
        self.manifest['images'][0]['width'] = 99; self.save()
        self.refuses('recorded dimensions')
        self.manifest['images'][0]['width'] = 2
        garbage = self.renders / 'board-3d-top.png'
        garbage.write_bytes(b'\x89PNG\r\n\x1a\nnot really')
        self.manifest['images'][0]['sha256'] = self.manifest['views']['top']['sha256'] = digest(garbage)
        self.save()
        self.refuses('not a valid PNG')

    def test_missing_or_altered_snapshot_is_refused(self):
        (self.renders / 'snapshot' / 'board.kicad_pcb').write_text('altered')
        self.refuses('snapshot board')

    def make_candidate(self):
        source = Path(self.tmp.name) / 'source'
        source.mkdir()
        (source / 'board.kicad_pcb').write_text('board text')
        scope = Path(self.tmp.name) / 'scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/board.kicad_pcb',
                                     'inputs': [{'path': 'source', 'at': 'project'}]}))
        run = Path(self.tmp.name) / 'run'
        run.mkdir()
        return run, capture(scope, run)

    def bind(self, run, candidate):
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        shutil.copyfile(run / 'inputs' / 'project' / 'board.kicad_pcb', staged)
        self.manifest['staged_board_sha256'] = self.manifest['snapshot']['board']['sha256'] = digest(staged)
        self.manifest['candidate_id'] = candidate['candidate_id']
        self.manifest['captured_board'] = 'project/board.kicad_pcb'
        self.manifest['board']['sha256'] = candidate['files']['project/board.kicad_pcb']
        self.save()

    def test_candidate_binding(self):
        run, candidate = self.make_candidate()
        # A manifest that is unbound cannot be passed off as the retained candidate.
        self.refuses('different candidate', candidate=run)
        self.bind(run, candidate)
        # A bound manifest cannot be published without verifying the candidate.
        self.refuses('pass --candidate')
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', candidate=run)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(record['candidate_id'], candidate['candidate_id'])
        self.manifest['candidate_id'] = '0' * 64
        self.save()
        self.refuses('different candidate', candidate=run)
        self.bind(run, candidate)
        self.manifest['board']['sha256'] = 'c' * 64
        self.save()
        self.refuses('not the retained candidate board', candidate=run)
        self.bind(run, candidate)
        (run / 'inputs' / 'project' / 'board.kicad_pcb').write_text('mutated')
        self.refuses('stale', candidate=run)

    # --- G3: no absolute host paths are published ---------------------------
    def make_viewer(self, **extra):
        viewer = Path(self.tmp.name) / 'viewer'
        viewer.mkdir(exist_ok=True)
        (viewer / 'board.glb').write_bytes(glb_bytes())
        metadata = {'schema': 'agent-ee.viewer/1', 'board': {'name': 'board.kicad_pcb', 'path': HOST + '/board.kicad_pcb',
                    'sha256': self.board_hash}, 'candidate_id': None, 'captured_board': None, 'model': 'board.glb',
                    'model_sha256': digest(viewer / 'board.glb'), 'warnings': ['see ' + HOST + '/x.log'],
                    'export': {'command': ['kicad-cli', '--define-var', 'KIPRJMOD=' + HOST, '--output', HOST + '/o.glb'],
                               'project_directory': HOST}, **extra}
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        return viewer

    def test_published_files_contain_no_absolute_host_paths(self):
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=self.make_viewer())
        for path in (self.root / 'index.json', *(self.root / 'bundles' / bundle).rglob('*.json')):
            if 'vendor' in path.parts:
                continue
            text = path.read_text()
            self.assertNotIn('/home/', text, path)
            self.assertNotIn(HOST, text, path)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(record['schema'], 'agent-ee.gallery-bundle/3')
        self.assertEqual(record['model_coverage'], {'summary': dict(
            resolved=3, missing=1, unresolved=2, disabled=4, no_model=5, footprints=None, models=None,
            footprints_fully_resolved=None)})
        self.assertEqual(record['warnings'], ['R1: missing 3D model r.step'])
        metadata = json.loads((self.root / 'bundles' / bundle / 'viewer' / 'viewer.json').read_text())
        self.assertEqual(metadata, {
            'schema': 'agent-ee.viewer/1', 'publication': 'agent-ee.gallery-viewer/2',
            'board': {'name': 'board.kicad_pcb', 'path': 'board.kicad_pcb', 'sha256': self.board_hash},
            'candidate_id': None, 'captured_board': None, 'model': 'board.glb', 'warnings': ['<redacted>'],
            'coordinates': {'up': None, 'units': None}, 'model_sha256': digest(self.root / 'bundles' / bundle / 'viewer' / 'board.glb')})

    def test_basename_is_kept_only_for_an_unambiguous_local_path(self):
        kept = {'/home/a/m.step': 'm.step', '/home/A B/p q/m.step': 'm.step', '/home/Smith, Alice/m.step': 'm.step',
                'C:/Users/A/m.step': 'm.step', 'C:\\Users\\A B\\m.step': 'm.step', 'd:\\x\\R_0603.wrl': 'R_0603.wrl',
                '${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step': 'R_0603.step',
                'models/r.step': 'r.step', 'r.step': 'r.step', None: None}
        redacted = ('\\\\srv\\share\\m.step', '//srv/share/m.step', 'file:///home/a/m.step', 'FILE:///home/a/m.step',
                    'file:C:/Users/A/m.step', 'https://x.org/a/m.step', ':alias:m.step', 'C:m.step', '\\share\\m.step', '${KIPRJMOD}\\models\\r.step',
                    '/home/a//host/m.step', '/a/m.step\\\\host', 'C:\\a\\\\host\\m.step', '/home/a/', 'C:\\', 'D:/', '',
                    '/home/a/"m.step"', "/home/a/m'step", '/home/a/<m>.step', '/home/a/m.step|x', '/home/a/m\tb.step',
                    '/home/a/m\n.step', '/home/a/m step', '/home/a/.hidden', '/home/a/..', '/home/a/m.step)',
                    '<redacted>', '<redacted>/', '<redacted>/a/b', 'x<redacted>/m.step', 3, ['m.step'], {'a': 1},
                    # Review 433k H2: an earlier redactor's output is not known to end in a file name.
                    '<redacted>/m.step', '<redacted>/private-server', '<redacted>\\private-server')
        for value, expected in kept.items():
            self.assertEqual(gallery._basename(value), expected, value)
        for value in redacted:
            self.assertEqual(gallery._basename(value), '<redacted>', value)
        for value in REVIEW_EXAMPLES:  # only the local drive and POSIX examples keep a basename
            self.assertRegex(gallery._basename(value), r'^(<redacted>|board\.kicad_pcb|[a-e]\.step)$', value)
            self.assertEqual(gallery._name(value), '<redacted>', value)
        for value in (*kept, *redacted, *REVIEW_EXAMPLES):
            once = gallery._basename(value)
            self.assertEqual(gallery._basename(once), once, value)

    def test_warnings_are_rebuilt_from_codes_and_allowlisted_parameters(self):
        notice = 'Geometry is for visual inspection, not electrical or fabrication acceptance.'
        cases = {'R1: missing 3D model ' + HOST + '/r.step': 'R1: missing 3D model r.step',
                 'U$1: unresolved 3D model ${X}/u.step': '<redacted>',
                 'U1: unresolved 3D model \\\\srv\\share\\u.step': 'U1: unresolved 3D model <redacted>',
                 'U1: unresolved 3D model None': 'U1: unresolved 3D model None',
                 'C3: no 3D model is declared': 'C3: no 3D model is declared',
                 'C3: no 3D model is declared ' + HOST: '<redacted>',
                 'top: KiCad produced 10×20 for requested 30×40': 'top: KiCad produced 10×20 for requested 30×40',
                 'isometric: Warning: could not load ' + HOST + '/r.step': 'isometric: <redacted>',
                 'see ' + HOST + '/x.log': '<redacted>', notice: notice, notice + ' ' + HOST: '<redacted>',
                 'R1: missing 3D model <redacted>/r.step': 'R1: missing 3D model <redacted>',
                 'R1: missing 3D model <redacted>/private-server': 'R1: missing 3D model <redacted>', 7: '<redacted>'}
        for text, expected in cases.items():
            self.assertEqual(gallery._warning(text), expected, text)
            self.assertEqual(gallery._warning(expected), expected, expected)

    def test_labels_and_stages_are_rebuilt_from_their_vocabulary_or_redacted_whole(self):
        """Review 433k H1: no publisher free text is published, with or without path syntax."""
        for value in ('Iteration 8', 'Revision 0', 'Candidate 12', 'Milestone 999999', 'Diagnostic 1', 'Fixture 2'):
            self.assertEqual(gallery._label(value), value)
        self.assertEqual(gallery._label('Iteration 08'), 'Iteration 8')
        for value in ('placement', 'routing', 'routed', 'review', 'diagnostic', 'fixture'):
            self.assertEqual(gallery._stage(value), value)
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        refused = (*exact, 'Telemetry pod iteration 8', 'Iteration', 'Iteration ', 'Iteration 8 ', 'iteration 8',
                   'Iteration 1000000', 'Iteration -1', 'Iteration 8.0', 'Iteration \uff18', 'Iteration private-host',
                   'Candidate <one>', 'Placement', 'see ' + HOST, 'C:\\x', 'user@host', '', '<redacted>',
                   '<redacted>/private-server', None, 3, ['placement'], {'a': 1}, *REVIEW_EXAMPLES)
        for value in refused:
            self.assertEqual(gallery._label(value), '<redacted>', value)
            self.assertEqual(gallery._stage(value), '<redacted>', value)
            if isinstance(value, str):
                with self.assertRaisesRegex(ValueError, 'label must be KIND NUMBER'):
                    publish(self.root, self.renders, label=value, stage='placement')
                with self.assertRaisesRegex(ValueError, 'stage must be one of'):
                    publish(self.root, self.renders, label='Iteration 1', stage=value)
        with self.assertRaisesRegex(ValueError, 'label must be KIND NUMBER'):
            publish(self.root, self.renders, label='Iteration 08', stage='placement')
        self.assertFalse((self.root / 'index.json').exists())

    def test_reviewed_host_text_in_labels_stages_and_marker_paths_is_never_published(self):
        """Review 433k H1/H2 exact inputs, through real publication, legacy records and HTTP."""
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        marker = '<redacted>/private-server'
        self.manifest['warnings'] = ['R1: missing 3D model ' + marker, 'R2: unresolved 3D model ' + marker]
        self.save()
        viewer = self.make_viewer(warnings=['R1: missing 3D model ' + marker])
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board'].update(name=marker, path=marker)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        stored = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            served = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            served.append(urlopen(url).read().decode())
            bodies = []
            for label, stage in zip(exact, exact[1:] + exact[:1]):
                for n in names:  # an earlier publisher stored the text as given
                    path = self.root / 'bundles' / bundle / n
                    record = json.loads(path.read_text())
                    record.update(label=label, stage=stage, captured_board=marker,
                                  board=dict(record.get('board', {}), name=marker, path=marker),
                                  warnings=['R1: missing 3D model ' + marker, label, stage])
                    if n == 'manifest.json':
                        record['schema'] = 'agent-ee.gallery-bundle/2'
                    path.write_text(json.dumps(record))
                listing = json.loads((self.root / 'index.json').read_text())
                listing[0].update(label=label, stage=stage, warnings=['R1: missing 3D model ' + marker])
                (self.root / 'index.json').write_text(json.dumps(listing))
                bodies += [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
                bodies.append(urlopen(url).read().decode())
        for n, body in enumerate(stored + served + bodies):
            assert_no_uri_leak(self, body, n)
            self.assertNotIn('private', body, n)
        record, viewer_record = json.loads(served[0]), json.loads(served[1])
        self.assertEqual((record['label'], record['stage']), ('Iteration 1', 'placement'))
        self.assertEqual(record['warnings'], ['R1: missing 3D model <redacted>', 'R2: unresolved 3D model <redacted>'])
        self.assertEqual((viewer_record['board']['name'], viewer_record['board']['path']), ('<redacted>', '<redacted>'))
        self.assertEqual(viewer_record['warnings'], ['R1: missing 3D model <redacted>'])
        legacy = json.loads(bodies[-3])
        self.assertEqual((legacy['label'], legacy['stage'], legacy['schema']),
                         ('<redacted>', '<redacted>', 'agent-ee.gallery-bundle/3'))

    def test_legacy_bundle_is_projected_when_served(self):
        bundle = self.add()
        legacy = self.root / 'bundles' / bundle / 'manifest.json'
        record = json.loads(legacy.read_text())
        record.update(warnings=['leak ' + HOST + '/m.step', 'R1: missing 3D model ' + HOST + '/m.step'],
                      label='see ' + HOST, private=HOST)
        del record['schema']
        legacy.write_text(json.dumps(record))
        with self.serving() as url:
            with urlopen(url + '/bundles/' + bundle + '/manifest.json') as response:
                body = response.read().decode()
        self.assertNotIn(HOST, body)
        served = json.loads(body)
        self.assertEqual(served['warnings'], ['<redacted>', 'R1: missing 3D model m.step'])
        self.assertEqual((served['label'], served['schema']), ('<redacted>', 'agent-ee.gallery-bundle/3'))
        self.assertNotIn('private', served)
        self.assertEqual(json.loads(legacy.read_text()), record)  # immutable: served, not rewritten

    # --- G4: viewer happy path, vendor assets, concurrency ------------------
    def serving(self):
        httpd = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()

        class Context:
            def __enter__(inner):
                return f'http://127.0.0.1:{httpd.server_port}'

            def __exit__(inner, *exc):
                httpd.shutdown()
                thread.join()
                httpd.server_close()
        return Context()

    def test_viewer_happy_path_and_vendor_assets_are_served(self):
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=self.make_viewer())
        with self.serving() as url:
            base = url + '/bundles/' + bundle + '/viewer/'
            for name in ('index.html', 'viewer.js', 'viewer.css', 'board.glb', 'viewer.json',
                         'vendor/three.module.js', 'vendor/GLTFLoader.js'):
                with urlopen(base + name) as response:
                    self.assertEqual(response.status, 200, name)
            with urlopen(base + 'board.glb') as response:
                self.assertEqual(response.read(4), b'glTF')
            with urlopen(base + 'vendor/three.module.js') as response:
                self.assertEqual(response.read(), (_ASSETS / 'vendor' / 'three.module.js').read_bytes())
            with urlopen(url) as response:
                self.assertIn(b'Open interactive 3D viewer', response.read())
            for name in ('vendor/nonexistent.js', 'vendor/../../index.json', 'export.json'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + name)
                self.assertEqual(error.exception.code, 404)

    def test_viewer_for_other_candidate_is_refused(self):
        viewer = self.make_viewer(candidate_id='f' * 64)
        with self.assertRaisesRegex(ValueError, 'different candidate'):
            publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)

    def test_concurrent_publishers_keep_every_bundle_and_a_valid_index(self):
        context = multiprocessing.get_context('fork')
        workers = [context.Process(target=publish_in_process, args=(str(self.root), str(self.renders), f'Iteration {n}'))
                   for n in range(6)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(120)
            self.assertEqual(w.exitcode, 0)
        index = json.loads((self.root / 'index.json').read_text())
        self.assertEqual(len(index), 6)
        self.assertEqual(len({item['id'] for item in index}), 6)
        self.assertEqual(sorted(item['label'] for item in index), [f'Iteration {n}' for n in range(6)])
        for item in index:
            self.assertTrue((self.root / 'bundles' / item['id'] / 'top.png').is_file())
        self.assertEqual(len(list((self.root / 'bundles').iterdir())), 6)
        self.assertEqual(list(self.root.glob('.publish-*')), [])

    # --- HOLD follow-up: snapshot, receipts, path forms, malformed counts ----
    def test_unrelated_or_rehashed_snapshot_with_verified_candidate_is_refused(self):
        run, candidate = self.make_candidate()
        self.bind(run, candidate)
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        for text in ('unrelated staged board', 'board text (thickness 2.0)'):
            staged.write_text(text)
            self.manifest['staged_board_sha256'] = self.manifest['snapshot']['board']['sha256'] = digest(staged)
            self.save()
            self.refuses('not the retained candidate board', candidate=run)
        self.assertEqual((run / 'inputs' / 'project' / 'board.kicad_pcb').read_text(), 'board text')
        self.bind(run, candidate)
        publish(self.root, self.renders, label='Iteration 1', stage='placement', candidate=run)
        self.manifest['snapshot']['project'] = {'path': 'snapshot/board.kicad_pro', 'sha256': digest(staged)}
        shutil.copyfile(staged, self.renders / 'snapshot' / 'board.kicad_pro')
        self.save()
        self.refuses('snapshot project is not the retained', candidate=run)

    def test_snapshot_path_must_be_a_contained_regular_file(self):
        staged = self.renders / 'snapshot' / 'board.kicad_pcb'
        outside = Path(self.tmp.name) / 'board.kicad_pcb'
        shutil.copyfile(staged, outside)
        for path in ('../board.kicad_pcb', HOST + '/board.kicad_pcb', 'snapshot/other.kicad_pcb', None):
            self.manifest['snapshot']['board']['path'] = path
            self.save()
            self.refuses('snapshot board')
        self.manifest['snapshot']['board']['path'] = 'snapshot/board.kicad_pcb'
        staged.unlink()
        staged.symlink_to(outside)
        self.save()
        self.refuses('snapshot board')

    def test_full_permutation_of_distinct_images_is_refused_by_native_receipts(self):
        top, bottom = (self.renders / f'board-3d-{v}.png' for v in ('top', 'bottom'))
        a, b = top.read_bytes(), bottom.read_bytes()
        top.write_bytes(b)
        bottom.write_bytes(a)
        for item in self.manifest['images'][:2]:
            item['sha256'] = self.manifest['views'][item['view']]['sha256'] = digest(self.renders / item['path'])
        self.save()
        self.refuses('native render receipt')

    def test_receipt_for_another_view_or_missing_receipt_is_refused(self):
        receipt = json.loads((self.renders / 'top.json').read_text())
        receipt['command'][receipt['command'].index('--side') + 1] = 'bottom'
        (self.renders / 'top.json').write_text(json.dumps(receipt))
        self.refuses('native render receipt')
        (self.renders / 'top.json').unlink()
        self.refuses('receipt is missing')

    def test_host_paths_in_warnings_coverage_and_viewer_are_published_as_basenames_or_redacted(self):
        paths = ('C:/Users/Alice/private-project/board.kicad_pcb', 'C:\\Users\\Alice\\private-project\\b.step',
                 '\\\\fileserver\\private-share\\c.step', '/home/Alice Smith/private-project/d.step',
                 'file:///home/alice/private-project/e.step', 'FILE://fileserver/private-share/f.step')
        self.manifest['model_coverage']['board']['path'] = paths[0]
        self.manifest['model_coverage']['extra'] = {p: p for p in paths}
        self.manifest['warnings'] = ['R9: missing 3D model ' + p for p in paths]
        self.save()
        viewer = self.make_viewer()
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = paths[2]
        metadata['warnings'] = list(paths)
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        bodies = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            bodies += [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            bodies.append(urlopen(url).read().decode())
        for body in bodies:
            for leak in ('Alice', 'private-project', 'fileserver', 'private-share', 'C:', 'file:', 'FILE'):
                self.assertNotIn(leak, body)
        record = json.loads(bodies[3])
        self.assertNotIn('extra', record['model_coverage'])
        self.assertEqual(record['warnings'], ['R9: missing 3D model ' + n for n in
                                              ('board.kicad_pcb', 'b.step', '<redacted>', 'd.step', '<redacted>', '<redacted>')])
        metadata = json.loads(bodies[4])
        self.assertEqual(metadata['warnings'], ['<redacted>'] * len(paths))
        self.assertEqual(metadata['board']['path'], '<redacted>')
        self.assertNotIn('export', metadata)
        self.assertIn('R9: missing 3D model d.step', bodies[5])

    def test_projection_is_idempotent_and_drops_unlisted_fields(self):
        for text in itertools.islice(corpus(), 0, None, 37):
            for project, record in zip((gallery._bundle, gallery._viewer, gallery._diagnostic), records(text)):
                self.assertEqual(project(record), record, text)
                self.assertNotIn(text, record)
        bundle, viewer, diagnostic = records('x')
        self.assertEqual(set(bundle), {'id', 'schema', 'label', 'stage', 'board_sha256', 'candidate_id', 'published_at',
                                       'rendered_at', 'model_coverage', 'warnings', 'images', 'viewer'})
        self.assertEqual(set(viewer), {'schema', 'publication', 'board', 'candidate_id', 'captured_board', 'model',
                                       'warnings', 'coordinates', 'model_sha256'})
        self.assertEqual(set(diagnostic), {'id', 'schema', 'kind', 'label', 'stage', 'candidate_id', 'captured_board',
                                           'board_sha256', 'routing', 'adoption', 'published_at', 'source_packet',
                                           'source_image', 'image', 'limitation'})
        self.assertEqual(bundle['images'], [{'view': 'top', 'path': 'top.png', 'sha256': None}])
        self.assertEqual((bundle['viewer'], diagnostic['adoption']['verified']), (False, False))

    def test_property_generator_covers_every_operand_position(self):
        """Review 433h H2: bare drives (both separators), POSIX and UNC operands at every position."""
        triples = [t for t, *_ in operand_triples()]
        kinds = {'forward-drive': r'(?<![/\\])C:/Users', 'back-drive': r'(?<![/\\])[Cd]:\\Users',
                 'posix': r'(?<![/\\:])/home/', 'unc': r'(?<![:\\])\\\\[a-z]',
                 'ipv6': r'(?i)file://(user-hidden@)?\[', 'interrupted-unc': r'hidden[( ]',
                 'interrupted-space': r'user-hidden name|hidden[")] \\'}
        for kind, pattern in kinds.items():
            for position in range(3):
                found = [t for t in triples if re.search(pattern, t[position])]
                self.assertTrue(found, (kind, position))
        surface = surface_texts()
        for kind, pattern in kinds.items():
            self.assertTrue([t for t in surface if re.search(pattern, t)], kind)

    def test_oracle_holds_for_every_composed_text_on_every_surface(self):
        assert_leak_free(self, surfaces)

    def test_oracle_fails_on_deliberately_leaky_publication(self):
        """Negative control: the oracle itself fails when publication leaks host text."""
        def scrubbed(text):  # the rejected design: a regex scrubber on free text (433c-433i)
            return surfaces(text) + re.sub(r'(?:[A-Za-z]:)?[\\/][^\s"\'()]*[\\/]', '<redacted>/', text)

        def unlisted(text):  # an unknown field passed through
            return surfaces(text) + json.dumps({'export': {'command': [text]}})

        def loose_basename(text):  # a basename without the local-path checks
            return surfaces(text) + PureWindowsPath(text).name

        def legacy_marker(text):  # text already starting with the marker trusted as redacted
            return surfaces(text) + (text if text.startswith('<redacted>') else '')

        def slash_only(text):  # a label allowlist that only refuses "/"
            return surfaces(text) + (text if '/' not in text else '')

        character_filter = re.compile(r"(?:[A-Za-z0-9 _.,;!?'\"()\[\]{}<>&=*#+|·–—’“”×-]|:(?= |$)){1,200}")

        def plain_text(text):  # review 433k H1: a label kept when it is plain text without path syntax
            return surfaces(text) + (text if character_filter.fullmatch(text) else '')

        def marker_name(text):  # review 433k H2: "<redacted>/NAME" trusted as an earlier basename
            name = text[len('<redacted>/'):] if text.startswith('<redacted>/') else ''
            return surfaces(text) + (name if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*', name) else '')

        exact = (legacy_marker, slash_only, plain_text, marker_name)
        for leaky in (lambda text: text, scrubbed, unlisted, loose_basename, *exact):
            failure = assert_leak_detected(self, leaky, rounds=200, stride=1 if leaky in exact else 7)
            check_publication(surfaces(failure.where), failure.where)  # the leaky addition, not the clean surfaces
        assert_leak_free(self, surfaces, seed=1, rounds=200, stride=50)

    def test_composed_texts_are_never_published_on_any_core_surface(self):
        texts = surface_texts() + list(itertools.islice(corpus(), 0, None, 97))
        self.manifest['model_coverage']['board']['path'] = texts[0]
        self.manifest['model_coverage']['extra'] = {t: t for t in texts}
        self.manifest['warnings'] = texts + ['R1: missing 3D model ' + t for t in texts]
        self.save()
        viewer = self.make_viewer(private_location=texts[1], warnings=texts)
        metadata = json.loads((viewer / 'viewer.json').read_text())
        metadata['board']['path'] = texts[2]
        (viewer / 'viewer.json').write_text(json.dumps(metadata))
        bundle = publish(self.root, self.renders, label='Iteration 1', stage='placement', viewer=viewer)
        names = ('manifest.json', 'viewer/viewer.json')
        stored = [(self.root / 'bundles' / bundle / n).read_text() for n in names] + [(self.root / 'index.json').read_text()]
        with self.serving() as url:
            served = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            served.append(urlopen(url).read().decode())
            # Records written before the allowlist are projected when served.
            for n in names:
                path = self.root / 'bundles' / bundle / n
                record = json.loads(path.read_text())
                record.update(warnings=texts, label=texts[3], stage=texts[4], board={'path': texts[5], 'name': texts[6]},
                              captured_board=texts[7], **{t: t for t in texts})
                path.write_text(json.dumps(record))
            listing = json.loads((self.root / 'index.json').read_text())
            listing[0].update(warnings=texts, label=texts[3], stage=texts[4], **{t: t for t in texts})
            (self.root / 'index.json').write_text(json.dumps(listing))
            legacy = [urlopen(url + '/bundles/' + bundle + '/' + n).read().decode() for n in names]
            legacy.append(urlopen(url).read().decode())
        for where, body in zip(('stored manifest', 'stored viewer', 'stored index', 'served manifest',
                                'served viewer', 'served html', 'legacy manifest', 'legacy viewer', 'legacy html'),
                               stored + served + legacy):
            assert_no_uri_leak(self, body, where)
        record = json.loads(served[0])
        self.assertEqual(record['warnings'][:len(texts)], ['<redacted>'] * len(texts))
        for warning in record['warnings'][len(texts):]:  # a basename only when the whole text is a local path
            self.assertRegex(warning, r'^R1: missing 3D model (<redacted>|[A-Za-z0-9_][A-Za-z0-9_.+-]*)$')
        self.assertEqual(json.loads(served[1])['warnings'], ['<redacted>'] * len(texts))
        self.assertEqual(json.loads(legacy[0])['label'], '<redacted>')

    def test_malformed_counts_or_coverage_are_never_complete(self):
        good = dict(resolved=3, missing=0, unresolved=0, disabled=0, no_model=0)
        malformed = [{'summary': dict(good, resolved=bad)} for bad in (-1, True, None, 3.0, '3')]
        malformed += [{'summary': dict(good, disabled=-2)}, {'summary': []}, [], ['bad'], 'bad', 7]
        for coverage in malformed:
            visible, _ = self.visible_html(coverage)
            self.assertIn('Model coverage unknown', visible, coverage)
            self.assertNotIn('All declared 3D models resolved', visible, coverage)

    @native_board_render_test
    def test_native_render_binding_refuses_snapshot_and_permutation_tampering(self):
        require_toolchain(native=True)
        source = Path(self.tmp.name) / 'native-source'
        shutil.copytree(Path(__file__).resolve().parents[1] / 'fixtures/rc/control', source)
        scope = Path(self.tmp.name) / 'native-scope.json'
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/rc_filter.kicad_pcb',
                                     'inputs': [{'path': 'native-source', 'at': 'project'}]}))
        run = Path(self.tmp.name) / 'native-run'
        run.mkdir()
        capture(scope, run)
        out = Path(self.tmp.name) / 'native-renders'
        manifest_path = render_board(run, out, width=128, height=128, quality='basic', jobs=1, timeout=120)
        viewer = Path(self.tmp.name) / 'native-viewer'
        build_viewer(run, viewer)
        bundle = publish(self.root, out, label='Iteration 1', stage='placement', viewer=viewer, candidate=run)
        record = json.loads((self.root / 'bundles' / bundle / 'manifest.json').read_text())
        self.assertEqual(set(record['model_coverage']['summary']) >= {'resolved', 'disabled', 'no_model'}, True)
        published = ''.join(p.read_text() for p in (self.root / 'bundles' / bundle).rglob('*.json') if 'vendor' not in p.parts)
        self.assertNotIn(self.tmp.name, published)
        pristine = manifest_path.read_text()
        manifest = json.loads(pristine)
        staged = out / manifest['snapshot']['board']['path']
        original = staged.read_bytes()
        changed = original.replace(b'(thickness 1.6)', b'(thickness 2.0)', 1)
        self.assertNotEqual(changed, original)
        staged.write_bytes(changed)
        manifest['snapshot']['board']['sha256'] = manifest['staged_board_sha256'] = digest(staged)
        manifest_path.write_text(json.dumps(manifest))
        self.renders = out
        self.refuses('not the retained candidate board', candidate=run)
        staged.write_bytes(original)
        manifest = json.loads(pristine)
        images = {item['view']: item for item in manifest['images']}
        top, bottom = out / images['top']['path'], out / images['bottom']['path']
        a, b = top.read_bytes(), bottom.read_bytes()
        self.assertNotEqual(a, b)
        top.write_bytes(b)
        bottom.write_bytes(a)
        for view in ('top', 'bottom'):
            images[view]['sha256'] = manifest['views'][view]['sha256'] = digest(out / images[view]['path'])
        manifest_path.write_text(json.dumps(manifest))
        self.refuses('native render receipt', candidate=run)

UNROUTED_BOARD = '(kicad_pcb (version 20240108) (generator "fixture") (gr_line (start 0 0) (end 1 0)))\n'


class DiagnosticSupplementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'public'
        self.root.mkdir()
        self.image = self.base/'annotated.png'
        self.image.write_bytes(png_bytes(100))
        self.packet = self.base/'INDEX.md'
        self.packet.write_text('Historical diagnostic. Proposed ADC move NOT adopted.')
        self.run, self.candidate = self.capture('unrouted', UNROUTED_BOARD)
        self.board_hash = self.candidate['files']['project/board.kicad_pcb']

    def capture(self, name, text):
        source = self.base/('source-' + name)
        source.mkdir()
        (source/'board.kicad_pcb').write_text(text)
        scope = self.base/('scope-' + name + '.json')
        scope.write_text(json.dumps({'schema': 'agent-ee.scope/1', 'board': 'project/board.kicad_pcb',
                                     'inputs': [{'path': source.name, 'at': 'project'}]}))
        run = self.base/('run-' + name)
        run.mkdir()
        return run, capture(scope, run)

    def add(self, **overrides):
        from agent_ee.render_gallery import publish_supplement
        arguments = dict(candidate=self.run, board_sha256=self.board_hash, label='Diagnostic 1',
                         stage='diagnostic', routing='unrouted', adoption='not-adopted')
        arguments.update(overrides)
        return publish_supplement(self.root, overrides.pop('image', self.image), self.packet,
                                  **{k: v for k, v in arguments.items() if k != 'image'})

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink() and p.name != '.publish.lock'}

    def refuses(self, pattern, **overrides):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, pattern):
            self.add(**overrides)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.root.glob('.diagnostic-*')) + list(self.root.glob('.diagnostics.json-*')), [])

    def get(self, url):
        with urlopen(url) as response:
            return response.read().decode()

    def serving(self):
        server = serve(self.root, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_independent_immutable_history_preserves_latest_bytes(self):
        latest = b'[{"id":"unchanged current milestone"}]\n'
        (self.root/'index.json').write_bytes(latest)
        first = self.add()
        old = {p.name:p.read_bytes() for p in (self.root/'diagnostics'/first).iterdir()}
        self.image.write_bytes(png_bytes(150))
        second = self.add()
        self.assertNotEqual(first, second)
        self.assertEqual(latest, (self.root/'index.json').read_bytes())
        self.assertEqual(old, {p.name:p.read_bytes() for p in (self.root/'diagnostics'/first).iterdir()})
        manifest = json.loads(old['manifest.json'])
        self.assertEqual(manifest['board_sha256'], self.board_hash)
        self.assertEqual(manifest['candidate_id'], self.candidate['candidate_id'])
        self.assertEqual(manifest['captured_board'], 'board.kicad_pcb')  # a basename, like every published path
        self.assertEqual(manifest['schema'], 'agent-ee.gallery-diagnostic/4')
        self.assertEqual(manifest['routing'], {'status': 'unrouted', 'copper_items': 0})
        self.assertEqual(manifest['adoption'], {'status': 'not-adopted', 'verified': False})
        self.assertEqual(manifest['source_packet']['sha256'], digest(self.packet))
        self.assertEqual(manifest['source_image']['sha256'], manifest['image']['sha256'])
        self.assertEqual(set(old), {'image.png','manifest.json'})

    def test_wrong_board_hash_symlink_and_invalid_png_leave_listing_unchanged(self):
        self.add()
        self.refuses('hash mismatch', board_sha256=digest(self.packet))
        self.refuses('invalid board hash', board_sha256='A' * 64)
        self.image.unlink()
        self.image.symlink_to(self.packet)
        self.refuses('symlink')
        self.image.unlink()
        for data in (b'not a PNG', b'<svg xmlns="http://www.w3.org/2000/svg"/>', png_bytes(100) + b'trailing',
                     png_bytes(100)[:-6]):
            self.image.write_bytes(data)
            self.refuses('PNG')

    def test_http_only_serves_allowlisted_diagnostic_artifacts(self):
        identifier = self.add()
        base = self.serving()
        prefix = '/diagnostics/'+identifier+'/'
        body = self.get(base+prefix+'index.html')
        self.assertIn('<h2>Diagnostic 1</h2>', body)
        self.assertIn('NOT adopted', body)
        self.assertNotIn('Latest:', body)
        with urlopen(base+prefix+'image.png') as result:
            self.assertEqual(result.read(), self.image.read_bytes())
        for path in ('diagnostics.json', prefix+'source-board', prefix+'source-packet', prefix+'../image.png', prefix):
            with self.assertRaises(HTTPError) as error: urlopen(base+'/'+path.lstrip('/'))
            self.assertEqual(error.exception.code, 404)
        image = self.root/'diagnostics'/identifier/'image.png'
        image.unlink()
        image.symlink_to(self.image)
        with self.assertRaises(HTTPError): urlopen(base+prefix+'image.png')

    # --- supplement review H1: destination symlinks ---------------------------
    def test_destination_symlinks_refuse_without_writing_outside_root(self):
        outside = self.base/'outside'
        outside.mkdir()
        (self.root/'diagnostics').symlink_to(outside, target_is_directory=True)
        self.refuses('must be a directory, not a symlink')
        self.assertEqual(list(outside.iterdir()), [])
        (self.root/'diagnostics').unlink()
        linked = self.base/'linked-root'
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'not a symlink'):
            from agent_ee.render_gallery import publish_supplement
            publish_supplement(linked/'nested', self.image, self.packet, candidate=self.run,
                               board_sha256=self.board_hash, label='Iteration 1', stage='placement', routing='unrouted',
                               adoption='not-adopted')
        self.assertFalse((self.root/'nested').exists())

    def test_planted_listing_and_staging_links_never_overwrite_a_publication(self):
        first = self.add()
        manifest = self.root/'diagnostics'/first/'manifest.json'
        old = manifest.read_bytes()
        # The pre-fix staging name, and every guessable alternative, is now irrelevant.
        (self.root/'.diagnostics.next').symlink_to(manifest)
        self.image.write_bytes(png_bytes(150))
        second = self.add()
        self.assertEqual(old, manifest.read_bytes())
        self.assertTrue((self.root/'.diagnostics.next').is_symlink())
        listing = json.loads((self.root/'diagnostics.json').read_text())
        self.assertEqual([item['id'] for item in listing], [second, first])
        # A listing replaced by a link is refused rather than followed.
        saved = self.root/'saved.json'
        (self.root/'diagnostics.json').rename(saved)
        (self.root/'diagnostics.json').symlink_to(manifest)
        self.image.write_bytes(png_bytes(200))
        self.refuses('must be a regular file, not a symlink')
        self.assertEqual(old, manifest.read_bytes())
        lock = self.root/'.publish.lock'
        (self.root/'diagnostics.json').unlink()
        lock.unlink()
        lock.symlink_to(manifest)
        self.refuses('publication lock')
        self.assertEqual(old, manifest.read_bytes())

    def test_identifier_collision_never_replaces_an_existing_supplement(self):
        from unittest import mock
        import agent_ee.render_gallery as gallery
        fixed = gallery.datetime(2026, 10, 22, tzinfo=gallery.timezone.utc)
        with mock.patch.object(gallery, 'datetime', wraps=gallery.datetime) as clock:
            clock.now.return_value = fixed
            first = self.add()
            target = self.root/'diagnostics'/first
            old = {p.name: p.read_bytes() for p in target.iterdir()}
            for entry in target.iterdir():
                entry.unlink()
            self.refuses('already exists; immutable publications are never replaced')
            for name, data in old.items():
                (target/name).write_bytes(data)
            self.refuses('already exists; immutable publications are never replaced')
        self.assertEqual(old, {p.name: p.read_bytes() for p in target.iterdir()})

    def test_bundle_destination_symlinks_refuse(self):
        outside = self.base/'outside-bundles'
        outside.mkdir()
        (self.root/'bundles').symlink_to(outside, target_is_directory=True)
        renders = self.base/'renders'
        renders.mkdir()
        (renders/'render-manifest.json').write_text(json.dumps({
            'schema': 'agent-ee.render3d/1', 'status': 'completed', 'board': {'sha256': 'a' * 64},
            'images': [{'view': v} for v in ('top', 'bottom', 'isometric')]}))
        from agent_ee import render_gallery as gallery
        from unittest import mock
        with mock.patch.object(gallery, '_verify_provenance'):
            with self.assertRaisesRegex(ValueError, 'must be a directory, not a symlink'):
                publish(self.root, renders, label='Iteration 1', stage='placement')
        self.assertEqual(list(outside.iterdir()), [])

    # --- supplement review H2: bound to a verified retained candidate ---------
    def test_supplement_requires_a_verified_retained_candidate(self):
        self.refuses('retained candidate run directory is required', candidate=self.base/'missing')
        self.refuses('retained candidate run directory is required', candidate=self.base)
        junk, record = self.capture('junk', 'not a KiCad board or retained candidate')
        self.refuses('not a parseable KiCad board', candidate=junk, board_sha256=record['files']['project/board.kicad_pcb'])
        other, record = self.capture('other', UNROUTED_BOARD.replace('fixture', 'other'))
        self.refuses('hash mismatch', candidate=other)
        (self.run/'inputs'/'project'/'board.kicad_pcb').write_text(UNROUTED_BOARD + ' ')
        self.refuses('stale')
        self.assertFalse((self.root/'diagnostics.json').exists())

    def test_status_is_explicit_and_unrouted_is_checked_against_the_board(self):
        for routing in ('', 'unknown', 'UNROUTED', None):
            self.refuses('routing status must be one of', routing=routing)
        for adoption in ('', 'unknown', 'maybe', None):
            self.refuses('adoption status must be one of', adoption=adoption)
        self.refuses('contradicts the retained board', routing='routed')
        routed = UNROUTED_BOARD.replace('(gr_line', '(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 0)) (gr_line')
        run, record = self.capture('routed', routed)
        digest_ = record['files']['project/board.kicad_pcb']
        self.refuses('contradicts the retained board', candidate=run, board_sha256=digest_)
        identifier = self.add(candidate=run, board_sha256=digest_, routing='partially-routed', adoption='adopted')
        manifest = json.loads((self.root/'diagnostics'/identifier/'manifest.json').read_text())
        self.assertEqual(manifest['routing'], {'status': 'partially-routed', 'copper_items': 1})
        self.assertEqual(manifest['candidate_id'], record['candidate_id'])

    def test_served_status_labels_historical_and_fails_closed_on_unknown(self):
        identifier = self.add()
        base = self.serving()
        page_ = self.get(base)
        self.assertIn('No Latest render to compare', page_)
        self.assertIn('Retained candidate ' + self.candidate['candidate_id'], page_)
        self.assertIn('Unrouted (verified: the retained board has no tracks or vias)', page_)
        self.assertIn('Proposed change NOT adopted (publisher statement)', page_)
        (self.root/'index.json').write_text(json.dumps([{'id': '0' * 12 + '-' + '1' * 12, 'label': 'L', 'stage': 'S',
                                                          'board_sha256': 'b' * 64, 'rendered_at': 't', 'images': [],
                                                          'warnings': [], 'viewer': False}]))
        for body in (self.get(base), self.get(base + '/diagnostics/' + identifier + '/index.html')):
            self.assertIn('Historical: not the board in the Latest render', body)
        latest = json.loads((self.root/'index.json').read_text())
        (self.root/'index.json').write_text(json.dumps([dict(latest[0], board_sha256=self.board_hash)]))
        for body in (self.get(base), self.get(base + '/diagnostics/' + identifier + '/index.html')):
            self.assertIn('Same board as the Latest render', body)
            self.assertNotIn('Historical:', body)
        # A supplement from before candidate binding, or a tampered status, is unknown.
        listing = json.loads((self.root/'diagnostics.json').read_text())
        legacy = {k: v for k, v in listing[0].items() if k not in ('candidate_id', 'routing', 'adoption')}
        legacy.update(schema='agent-ee.gallery-diagnostic/1', id='2' * 12 + '-' + '3' * 12)
        tampered = dict(listing[0], id='4' * 12 + '-' + '5' * 12, routing={'status': 'mostly'})
        (self.root/'diagnostics.json').write_text(json.dumps([legacy, tampered, listing[0]]))
        body = self.get(base)
        self.assertEqual(body.count('Status unknown: not bound to a verified retained candidate'), 2)
        self.assertEqual(body.count('Unrouted (verified'), 1)

    # --- supplement review H3 and 433d-433i: host text in supplement surfaces --
    def legacy(self, identifier, **fields):
        """Store a supplement as an earlier publisher might have, beside the real one."""
        listing = json.loads((self.root/'diagnostics.json').read_text())
        record = dict(listing[0], id=identifier, **fields)
        (self.root/'diagnostics'/identifier).mkdir()
        (self.root/'diagnostics'/identifier/'manifest.json').write_text(json.dumps(record))
        (self.root/'diagnostics.json').write_text(json.dumps([record, *listing]))

    def surfaces(self, *identifiers, stored=True):
        """Stored manifest and listing (when publication wrote them), then everything served."""
        stored = [(self.root/'diagnostics'/identifiers[0]/'manifest.json').read_text(),
                  (self.root/'diagnostics.json').read_text()] if stored else []
        base = self.serving()
        served = [self.get(base + '/diagnostics/' + i + suffix) for i in identifiers
                  for suffix in ('/manifest.json', '/index.html')]
        return stored + served + [self.get(base)]

    def test_labels_and_stages_outside_the_vocabulary_are_refused(self):
        for value in ('see C:/Users/secret/project/board.kicad_pcb', '\\\\fileserver\\private-share\\c.png',
                      'FILE://private-server/', 'Historic analysis', 'Diagnostic', *REVIEW_EXAMPLES):
            self.refuses('label must be KIND NUMBER', label=value)
            self.refuses('stage must be one of', stage=value)
        self.assertFalse((self.root/'diagnostics.json').exists())

    def test_reviewed_host_text_and_marker_board_in_earlier_supplements_is_never_served(self):
        """Review 433k H1/H2 exact inputs in a legacy supplement's label, stage and captured_board."""
        identifier = self.add()
        exact = ('private-host private-user private-share', 'FILE: private-host', 'private-host; private-user')
        ids = []
        for n, (label, stage) in enumerate(zip(exact, exact[1:] + exact[:1])):
            ids.append(f'{n + 6}' * 12 + '-' + '7' * 12)
            self.legacy(ids[-1], label=label, stage=stage, captured_board='<redacted>/private-server',
                        schema='agent-ee.gallery-diagnostic/3')
        bodies = self.surfaces(*ids, identifier, stored=False)
        for n, body in enumerate(bodies):
            assert_no_uri_leak(self, body, n)
            self.assertNotIn('private', body, n)
        for body in bodies[0:6:2]:
            record = json.loads(body)
            self.assertEqual((record['label'], record['stage'], record['captured_board'], record['schema']),
                             ('<redacted>', '<redacted>', '<redacted>', 'agent-ee.gallery-diagnostic/4'))
        record = json.loads(bodies[6])
        self.assertEqual((record['label'], record['stage'], record['captured_board']),
                         ('Diagnostic 1', 'diagnostic', 'board.kicad_pcb'))
        self.assertIn('<h2>Diagnostic 1</h2>', bodies[-1])
        self.assertIn('Stage: Diagnostic', bodies[-1])

    def test_malformed_earlier_statuses_are_served_as_unknown(self):
        """Review 433k gap: list or mapping statuses render unknown instead of failing the request."""
        identifier = self.add()
        self.legacy('8' * 12 + '-' + '9' * 12, routing={'status': ['unrouted']}, adoption={'status': {'a': 1}})
        bodies = self.surfaces('8' * 12 + '-' + '9' * 12, identifier, stored=False)
        record = json.loads(bodies[0])
        self.assertEqual((record['routing']['status'], record['adoption']['status']), (None, None))
        self.assertIn('Status unknown: not bound to a verified retained candidate', bodies[1])
        self.assertEqual(bodies[-1].count('Status unknown: not bound'), 1)

    def test_host_text_in_earlier_supplement_records_is_never_served(self):
        identifier = self.add()
        texts = surface_texts()
        legacy_id = '6' * 12 + '-' + '7' * 12
        self.legacy(legacy_id, label=texts[0], stage=texts[1], captured_board=texts[2], limitation=texts[3],
                    source_packet={'name': texts[4], 'sha256': 'c' * 64, 'path': texts[5]}, notes=texts,
                    source_image={'name': texts[6]}, **{t: t for t in texts})
        bodies = self.surfaces(legacy_id, identifier, stored=False)
        for where, body in zip(('legacy manifest', 'legacy html', 'manifest', 'html', 'listing'), bodies):
            assert_no_uri_leak(self, body, where)
        record = json.loads(bodies[0])
        self.assertEqual((record['label'], record['stage'], record['source_packet'], record['source_image']['name']),
                         ('<redacted>', '<redacted>', {'name': '<redacted>', 'sha256': 'c' * 64}, '<redacted>'))
        self.assertEqual(record['captured_board'], gallery._basename(texts[2]))
        self.assertNotIn('notes', record)
        self.assertEqual(record['limitation'], json.loads(bodies[2])['limitation'])
        self.assertEqual(json.loads(bodies[2])['source_packet']['name'], 'INDEX.md')

    def test_real_packet_and_image_names_are_published_as_plain_basenames_or_redacted(self):
        # A file name cannot hold "/", so URIs use the backslash spelling. Every review
        # example and composed operand that fits a file name is a real packet file.
        names = {n.replace('/', '\\') for n in (*REVIEW_EXAMPLES, *surface_texts()[:90])}
        names = sorted(n for n in names if '\n' not in n and '\0' not in n and len(n.encode()) < 255)
        self.assertGreater(len(names), 60)
        for n, name in enumerate(names):
            directory = self.base / ('packet-' + str(n))
            directory.mkdir()
            self.packet = directory / name
            self.packet.write_text('diagnostic ' + str(n))
            identifier = self.add()
            bodies = self.surfaces(identifier)
            for where, body in zip(('stored manifest', 'stored listing', 'served manifest', 'served html', 'listing'),
                                   bodies):
                assert_no_uri_leak(self, body, where + ' ' + name)
            for body in bodies[:1] + bodies[2:3]:
                self.assertEqual(json.loads(body)['source_packet']['name'], '<redacted>', name)
        directory = self.base / 'plain'
        directory.mkdir()
        self.packet, image = directory / 'Free-space_analysis.v2.md', directory / 'annotated rev B.png'
        self.packet.write_text('plain')
        image.write_bytes(png_bytes(120))
        record = json.loads(self.surfaces(self.add(image=image))[0])
        self.assertEqual((record['source_packet']['name'], record['source_image']['name'], record['captured_board']),
                         ('Free-space_analysis.v2.md', '<redacted>', 'board.kicad_pcb'))


def drc_report(silk=2, clearance=1, unconnected=3):
    violations = ([{'type': 'silk_over_copper', 'severity': 'warning'}] * (silk - silk // 2)
                  + [{'type': 'silk_overlap', 'severity': 'warning'}] * (silk // 2)
                  + [{'type': 'clearance', 'severity': 'error'}] * clearance)
    return {'$schema': 'https://schemas.kicad.org/drc.v1.json', 'source': 'board.kicad_pcb', 'violations': violations,
            'unconnected_items': [{'type': 'unconnected_items'}] * unconnected, 'schematic_parity': []}


# The status fixture board: unrouted, with footprint U3 whose model is the fixture STEP.
STATUS_BOARD = ('(kicad_pcb (version 20240108) (generator "fixture") (footprint "Package:U3" (layer "F.Cu") '
                '(property "Reference" "U3") (model "${KIPRJMOD}/models/u3.step")) (gr_line (start 0 0) (end 1 0)))\n')
STATUS_BOARD_SHA256 = hashlib.sha256(STATUS_BOARD.encode()).hexdigest()


def design_diff(placement='p' * 64, paths=('footprint[U3]/model@0a1b2c', 'footprint[U3]/model@3d4e5f'),
                subject=STATUS_BOARD_SHA256):
    return {'schema': 'agent-ee.design-diff/1', 'format': 'board', 'equivalent': False,
            'entries': [{'path': path} for path in paths], 'raw': {'subject_sha256': subject},
            'hashes': {'changed': ['identity'], 'reference': {'placement': 'p' * 64, 'structural': 's' * 64},
                       'subject': {'placement': placement, 'structural': 's' * 64}}}


def iter8_record(**changes):
    """A status record of the kind the iter8t migration attaches; see docs/render-gallery.md."""
    record = {
        'routing': {'value': 'unrouted', 'evidence': ['board', 'index']},
        'adoption': {'value': 'not-adopted', 'evidence': ['index']},
        'qualification': {'value': 'unqualified', 'evidence': ['index']},
        'scope': {'value': 'models-only', 'evidence': ['receipt', 'design-diff']},
        'open_defects': {'count': 2, 'class': 'silk', 'evidence': ['drc']},
        'drc': {'violations': 3, 'raw_unconnected_items': 3, 'evidence': ['drc']},
        'models': [{'reference': 'U3', 'provenance': 'datasheet-derived', 'transparency': None,
                    'sha256': hashlib.sha256(b'original fixture STEP\n').hexdigest(), 'evidence': ['receipt', 'model']}]}
    record.update(changes)
    return record


class StatusRecordTests(unittest.TestCase):
    """Attached, hash-bound status records (433 deployment compatibility follow-up)."""
    maxDiff = None

    def setUp(self):
        GalleryTests.setUp(self)
        self.evidence_dir = Path(self.tmp.name) / 'evidence'
        self.evidence_dir.mkdir()
        files = {'board.kicad_pcb': STATUS_BOARD, 'INDEX.md': 'Status: UNROUTED placement candidate, not qualified.\n',
                 'RECEIPT.json': json.dumps({'purpose': 'models-only derivation'}),
                 'drc.json': json.dumps(drc_report()), 'design-diff.json': json.dumps(design_diff()),
                 'u3.step': 'original fixture STEP\n'}
        for name, text in files.items():
            (self.evidence_dir / name).write_text(text)
        self.board_hash = digest(self.evidence_dir / 'board.kicad_pcb')
        self.manifest['board']['sha256'] = self.board_hash
        self.save()
        self.bundle = publish(self.root, self.renders, label='Iteration 8', stage='placement', viewer=self.make_viewer())

    make_viewer = GalleryTests.make_viewer
    serving = GalleryTests.serving
    write_receipt = GalleryTests.write_receipt
    save = GalleryTests.save

    U3 = {'reference': 'U3', 'sha256': hashlib.sha256(b'original fixture STEP\n').hexdigest(),
          'provenance': 'datasheet-derived', 'transparency': None}

    def evidence(self, association='association.json', models=None, board_sha256=None, **replace):
        """The evidence list, with a reviewed association covering every other file supplied.

        association=None supplies none; a name other than association.json supplies that file as is.
        """
        roles = {'board': 'board.kicad_pcb', 'index': 'INDEX.md', 'receipt': 'RECEIPT.json', 'drc': 'drc.json',
                 'design-diff': 'design-diff.json', 'model': 'u3.step', **replace}
        evidence = [(role, self.evidence_dir / name) for role, name in roles.items() if name]
        if association == 'association.json':
            self.associate(evidence, models, board_sha256)
        return evidence + ([('association', self.evidence_dir / association)] if association else [])

    def associate(self, evidence, models=None, board_sha256=None, name='association.json'):
        mapping = {'schema': 'agent-ee.gallery-association/1', 'board_sha256': board_sha256 or self.board_hash,
                   'evidence': [{'role': role, 'sha256': digest(path)} for role, path in evidence if role != 'board'],
                   'models': [self.U3] if models is None else models}
        (self.evidence_dir / name).write_text(json.dumps(mapping))
        return mapping

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and p.name != '.publish.lock'}

    def attach(self, record=None, evidence=None, subject=None):
        return gallery.attach_status(self.root, subject or 'bundles/' + self.bundle,
                                     iter8_record() if record is None else record,
                                     self.evidence() if evidence is None else evidence)

    def refuses(self, pattern, **kwargs):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, pattern):
            self.attach(**kwargs)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.root.glob('.status-*')) + list(self.root.glob('.statuses.json-*')), [])

    def served(self, url, bundle=None):
        base = url + '/bundles/' + (bundle or self.bundle) + '/'
        return [urlopen(target).read().decode() for target in
                (url, base + 'index.html', base + 'viewer/index.html', base + 'status.json')]

    LIMITATIONS = ('Unrouted: the bound board has no tracks, arcs or vias', 'Not adopted (recorded disposition)',
                   'Not qualified (recorded disposition, not machine qualification)', 'Models-only derivative',
                   'Open defects: 2 silkscreen defects', '3 violations; 3 raw unconnected items (not an incomplete-net count)',
                   'Model U3: datasheet-derived visualization model, not manufacturer or mechanical model evidence; '
                   'transparency unknown (not verified)', 'Other component models: provenance unknown (not recorded)')

    def test_attached_record_keeps_bytes_and_latest_and_is_visible_on_every_served_surface(self):
        before = self.snapshot()
        status = self.attach()
        after = self.snapshot()
        self.assertEqual({k: v for k, v in after.items() if not k.startswith('statuses')}, before)
        self.assertEqual(json.loads(after['statuses.json'])[0]['id'], status)
        stored = json.loads(after[f'statuses/{status}/status.json'])
        self.assertEqual(stored['subject'], {'area': 'bundles', 'id': self.bundle, 'board_sha256': self.board_hash,
                                             'manifest_sha256': hashlib.sha256(before[f'bundles/{self.bundle}/manifest.json']).hexdigest()})
        self.assertEqual({e['role']: e['sha256'] for e in stored['evidence']},
                         {role: digest(path) for role, path in self.evidence()})
        with self.serving() as url:
            index, detail, viewer, record = self.served(url)
            self.assertEqual(json.loads(urlopen(url + '/statuses/' + status + '/status.json').read()), stored)
            base = url + '/bundles/' + self.bundle + '/'
            served = [json.loads(urlopen(base + name).read()) for name in ('manifest.json', 'viewer/viewer.json')]
        for body in served:  # both JSON endpoints carry the record beside the unchanged stored fields
            self.assertEqual(body['recorded_status'], json.loads(record))
        self.assertEqual({k: v for k, v in served[0].items() if k != 'recorded_status'},
                         json.loads(before[f'bundles/{self.bundle}/manifest.json']))
        for where, body in (('index', index), ('detail', detail), ('viewer', viewer)):
            visible = html.unescape(body.split('<details>')[0])
            for text in self.LIMITATIONS:
                self.assertIn(text, visible, where)
            self.assertIn('Recorded limitations: Unrouted; Not adopted; Not qualified; Models-only derivative; '
                          '2 open defects; U3 model datasheet-derived; U3 transparency unknown', visible, where)
        self.assertIn('Latest: Iteration 8', index)
        self.assertIn('Latest: Iteration 8', detail)
        self.assertIn('Model warnings (1)', index)  # limitations are never model warnings
        for text in ('Unrouted', 'datasheet', 'Not adopted'):
            self.assertNotIn(text, index.split('<details>')[1].split('</details>')[0])
        self.assertIn('viewer.js', viewer)
        self.assertEqual(json.loads(record)['attached'], 'matched')
        self.assertEqual(json.loads(record)['record'], {'id': status, **stored})
        self.assertEqual(json.loads(record)['shown'], {
            'routing': 'unrouted', 'adoption': 'not-adopted', 'qualification': 'unqualified', 'scope': 'models-only',
            'open_defects': {'count': 2, 'class': 'silk'}, 'drc': {'violations': 3, 'raw_unconnected_items': 3},
            'models': [{'reference': 'U3', 'provenance': 'datasheet-derived', 'transparency': None,
                        'sha256': self.U3['sha256']}]})

    def test_without_a_record_every_status_is_explicitly_unknown(self):
        with self.serving() as url:
            bodies = self.served(url)
        for body in bodies[:3]:
            self.assertIn('No status record attached: routing, adoption, qualification, scope, open defects and '
                          'model provenance are unknown', body)
            for key in ('Routing', 'Adoption', 'Qualification', 'Scope', 'Open defects'):
                self.assertIn(f'<li>{key}: unknown (not recorded)</li>', body)
            self.assertNotIn('Unrouted', body)
        self.assertEqual(json.loads(bodies[3]), {'subject': {'area': 'bundles', 'id': self.bundle},
                                                 'attached': 'none', 'record': None, 'shown': None})

    def test_record_not_matching_the_publication_bytes_is_unknown(self):
        self.attach()
        manifest = self.root / 'bundles' / self.bundle / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes().replace(b'Iteration 8', b'Iteration 9'))
        with self.serving() as url:
            bodies = self.served(url)
        for body in bodies[1:3]:
            self.assertIn("An attached status record does not match this publication&#x27;s bytes", body)
            self.assertNotIn('Unrouted', body)
        self.assertEqual(json.loads(bodies[3])['attached'], 'mismatched')

    def test_newest_matching_record_is_shown_and_history_is_retained(self):
        first = self.attach()
        second = self.attach(iter8_record(adoption={'value': None, 'evidence': []}))
        listing = json.loads((self.root / 'statuses.json').read_text())
        self.assertEqual([item['id'] for item in listing], [second, first])
        with self.serving() as url:
            detail = self.served(url)[1]
        self.assertIn('<li>Adoption: unknown (not recorded)</li>', detail)
        self.assertIn('Adoption unknown', detail)

    def plant(self, record):
        """Publish a stored status record as is (an earlier or foreign writer), with its identity and listing entry."""
        data = gallery._encode(gallery._status_record(record))
        status = record['subject']['manifest_sha256'][:12] + '-' + hashlib.sha256(data).hexdigest()[:12]
        (self.root / 'statuses' / status).mkdir(parents=True)
        (self.root / 'statuses' / status / 'status.json').write_bytes(data)
        listing = self.root / 'statuses.json'
        entries = json.loads(listing.read_text()) if listing.is_file() else []
        listing.write_text(json.dumps([{'id': status, **json.loads(data)}] + entries))
        return status

    def sealed(self, status):
        return json.loads((self.root / 'statuses' / status / 'status.json').read_text())

    def test_old_missing_or_uncited_fields_are_served_as_unknown(self):
        entry = self.sealed(self.attach())
        for key in ('qualification', 'scope', 'open_defects', 'drc', 'models'):
            del entry[key]
        entry['routing'] = {'value': 'unrouted', 'evidence': ['review']}  # cites evidence that is not bound
        entry['adoption'] = {'value': 'not-adopted'}
        status = self.plant(entry)
        with self.serving() as url:
            detail = self.served(url)[1]
            self.assertEqual(json.loads(self.served(url)[3])['record']['id'], status)
        for key in ('Routing', 'Adoption', 'Qualification', 'Scope', 'Open defects'):
            self.assertIn(f'<li>{key}: unknown (not recorded)</li>', detail)
        self.assertIn('<li>Native DRC report: unknown (not recorded)</li>', detail)
        self.assertNotIn('Model U3', detail)

    def test_a_record_without_a_reviewed_association_serves_only_board_checked_claims(self):
        """An earlier record (433m shape) binds no association: every claim citing other evidence is unknown."""
        entry = self.sealed(self.attach())
        entry['evidence'] = [e for e in entry['evidence'] if e['role'] != 'association']
        entry['routing']['evidence'] = ['board']
        status = self.plant(entry)
        with self.serving() as url:
            detail = html.unescape(self.served(url)[1])
            report = json.loads(self.served(url)[3])
        self.assertEqual((report['attached'], report['record']['id']), ('matched', status))
        self.assertEqual(report['shown'], {'routing': 'unrouted', 'adoption': None, 'qualification': None, 'scope': None,
                                           'open_defects': None, 'drc': None,
                                           'models': [{'reference': 'U3', 'provenance': None, 'transparency': None,
                                                       'sha256': None}]})
        self.assertIn('Unrouted: the bound board has no tracks, arcs or vias', detail)
        for key in ('Adoption', 'Qualification', 'Scope', 'Open defects'):
            self.assertIn(f'<li>{key}: unknown (not recorded)</li>', detail)
        self.assertIn('<li>Native DRC report: unknown (not recorded)</li>', detail)
        self.assertIn('Model U3: provenance unknown (not recorded); transparency unknown (not verified)', detail)
        self.assertNotIn('datasheet-derived visualization', detail)
        # Board evidence counts only as the subject's own board.
        entry['evidence'] = [dict(e, sha256='f' * 64) if e['role'] == 'board' else e for e in entry['evidence']]
        self.plant(entry)
        with self.serving() as url:
            self.assertIn('<li>Routing: unknown (not recorded)</li>', self.served(url)[1])

    def test_claims_need_a_reviewed_association_with_the_subject_board(self):
        report = drc_report(silk=0, clearance=0, unconnected=0)
        report['source'] = 'unrelated-candidate.kicad_pcb'
        (self.evidence_dir / 'foreign.json').write_text(json.dumps(report))
        zero = {'drc': {'violations': 0, 'raw_unconnected_items': 0, 'evidence': ['drc']},
                'open_defects': {'count': 0, 'class': 'silk', 'evidence': ['drc']}}
        self.refuses('drc evidence is not in a reviewed association', record=zero,
                     evidence=[('drc', self.evidence_dir / 'foreign.json')])
        # An association covering the other files does not cover a substituted report.
        current = self.evidence()
        self.refuses('drc evidence is not in a reviewed association', record=iter8_record(),
                     evidence=[(r, self.evidence_dir / 'foreign.json' if r == 'drc' else p) for r, p in current])
        self.associate(self.evidence(association=None), board_sha256='e' * 64, name='other-board.json')
        self.refuses('association evidence is for another board', evidence=self.evidence(association='other-board.json'))
        for name, mapping in (('extra.json', {'schema': 'agent-ee.gallery-association/1', 'board_sha256': self.board_hash,
                                              'evidence': [], 'models': [], 'notes': 'x'}),
                              ('board-role.json', {'schema': 'agent-ee.gallery-association/1', 'board_sha256': self.board_hash,
                                                   'evidence': [{'role': 'board', 'sha256': self.board_hash}], 'models': []}),
                              ('twice.json', {'schema': 'agent-ee.gallery-association/1', 'board_sha256': self.board_hash,
                                              'evidence': [], 'models': [self.U3, self.U3]}),
                              ('list.json', [])):
            (self.evidence_dir / name).write_text(json.dumps(mapping))
            self.refuses('association evidence is not', evidence=self.evidence(association=name))
        self.refuses('at most one association', evidence=self.evidence() + [('association', self.evidence_dir / 'twice.json')])
        # A diff of another board is not this candidate's models-only evidence.
        (self.evidence_dir / 'foreign-diff.json').write_text(json.dumps(design_diff(subject='d' * 64)))
        self.refuses('design-diff subject is not the board', evidence=self.evidence(**{'design-diff': 'foreign-diff.json'}))
        # Board evidence alone needs no association: routing is checked against the board itself.
        board_only = {'routing': {'value': 'unrouted', 'evidence': ['board']}}
        self.attach(board_only, [('board', self.evidence_dir / 'board.kicad_pcb')])

    def test_model_claims_must_match_the_association_and_the_bound_board(self):
        (self.evidence_dir / 'foreign.step').write_text('unrelated model never associated with this board')
        foreign = {'reference': 'U999999', 'provenance': 'manufacturer', 'transparency': 'verified',
                   'sha256': digest(self.evidence_dir / 'foreign.step'), 'evidence': ['model']}
        self.refuses('must be the ones in the reviewed association', record={'models': [foreign]},
                     evidence=self.evidence(model='foreign.step', models=[]))
        self.refuses('not a model of footprint U999999 on the bound board', record={'models': [foreign]},
                     evidence=self.evidence(model='foreign.step', models=[{k: foreign[k] for k in self.U3}]))
        u3 = iter8_record()['models'][0]
        for change in ({'provenance': 'manufacturer'}, {'transparency': 'verified'}):
            self.refuses('must be the ones in the reviewed association', record=iter8_record(models=[dict(u3, **change)]))
        self.refuses('must be the ones in the reviewed association', evidence=self.evidence(models=[dict(self.U3, reference='U4')]))
        self.refuses('needs the bound board evidence', record={'models': [u3]}, evidence=self.evidence(board=None))
        self.refuses('hash must be the hash of cited model evidence',
                     record={'models': [dict(u3, sha256=None)]})
        # The right STEP bytes under a name the board does not reference are not U3's model.
        (self.evidence_dir / 'renamed.step').write_text('original fixture STEP\n')
        self.refuses('not a model of footprint U3', evidence=self.evidence(model='renamed.step'))
        self.attach(iter8_record())

    def test_served_status_is_the_verified_immutable_record(self):
        """The listing only orders immutable records; a changed, missing, corrupt or swapped record is mismatched."""
        status = self.attach()
        published = self.snapshot()
        sealed = self.root / 'statuses' / status / 'status.json'
        listing = self.root / 'statuses.json'
        original = listing.read_bytes()

        def served():
            with self.serving() as url:
                bodies = self.served(url)
                bodies.append(urlopen(url + '/bundles/' + self.bundle + '/manifest.json').read().decode())
            return [html.unescape(body) for body in bodies]

        def mismatched(why):
            bodies = served()
            for body in bodies[:3]:
                self.assertIn("An attached status record does not match this publication's bytes", body, why)
                self.assertNotIn('Not qualified', body, why)
                self.assertNotIn('Qualified', body, why)
                self.assertNotIn('manufacturer model;', body, why)
            for body in (bodies[3], bodies[4]):
                report = json.loads(body) if body is bodies[3] else json.loads(body)['recorded_status']
                self.assertEqual((report['attached'], report['record']), ('mismatched', None), why)
            self.assertEqual({k: v for k, v in self.snapshot().items() if not k.startswith('statuses')},
                             {k: v for k, v in published.items() if not k.startswith('statuses')}, why)

        entries = json.loads(original)
        entries[0]['qualification']['value'] = 'qualified'
        entries[0]['models'][0]['provenance'] = 'manufacturer'
        listing.write_text(json.dumps(entries))
        mismatched('listing changed')
        self.assertEqual(sealed.read_bytes(), published[f'statuses/{status}/status.json'])
        listing.write_bytes(original)
        self.assertEqual(json.loads(served()[3])['attached'], 'matched')

        for why, change in (('deleted', lambda: sealed.unlink()),
                            ('corrupt', lambda: sealed.write_bytes(b'{')),
                            ('rewritten', lambda: sealed.write_bytes(sealed.read_bytes().replace(b'"unqualified"', b'"qualified"'))),
                            ('re-encoded', lambda: sealed.write_bytes(json.dumps(json.loads(sealed.read_bytes())).encode())),
                            ('symlink', lambda: (sealed.rename(sealed.with_name('real.json')), sealed.symlink_to('real.json')))):
            data = sealed.read_bytes()
            change()
            mismatched(why)
            with contextlib.suppress(FileNotFoundError):
                sealed.unlink()
            with contextlib.suppress(FileNotFoundError):
                sealed.with_name('real.json').unlink()
            sealed.write_bytes(data)
        self.assertEqual(json.loads(served()[3])['attached'], 'matched')

        # Swapped: a listing entry naming this bundle, pointing at another subject's sealed record.
        identifier = '9' * 12 + '-' + '8' * 12
        legacy = {'schema': 'agent-ee.gallery-diagnostic/1', 'label': 'Diagnostic 1', 'stage': 'diagnostic',
                  'board_sha256': self.board_hash, 'image': {'width': 2, 'height': 2}}
        (self.root / 'diagnostics' / identifier).mkdir(parents=True)
        (self.root / 'diagnostics' / identifier / 'manifest.json').write_text(json.dumps(legacy))
        other = self.attach({'routing': {'value': 'unrouted', 'evidence': ['board']}},
                            [('board', self.evidence_dir / 'board.kicad_pcb')], subject='diagnostics/' + identifier)
        published = self.snapshot()
        entries = json.loads(listing.read_text())
        entries[0] = dict(entries[1], id=other)
        listing.write_text(json.dumps(entries))
        mismatched('swapped')

    DISPOSITIONS = {'qualification': {'value': 'qualified', 'evidence': ['board']},
                    'adoption': {'value': 'adopted', 'evidence': ['board']},
                    'scope': {'value': 'full', 'evidence': ['board']},
                    'open_defects': {'count': 0, 'class': 'review', 'evidence': ['board']}}

    def cite(self, roles, routing='routed'):
        """DISPOSITIONS, plus a routing claim, each citing only roles."""
        record = {key: dict(value, evidence=list(roles)) for key, value in self.DISPOSITIONS.items()}
        return dict(record, routing={'value': routing, 'evidence': list(roles)})

    def every_projection(self, url):
        """index, detail and viewer HTML, then the bundle status, manifest and viewer JSON status reports."""
        base = url + '/bundles/' + self.bundle + '/'
        bodies = [html.unescape(body) for body in self.served(url)]
        reports = [json.loads(bodies[3])] + [json.loads(urlopen(base + name).read())['recorded_status']
                                             for name in ('manifest.json', 'viewer/viewer.json')]
        return bodies[:3], reports

    def test_board_or_association_alone_supports_no_disposition_claim(self):
        """Review 433n H1: only unrouted is checked against the board; a disposition needs its retained report."""
        board = [('board', self.evidence_dir / 'board.kicad_pcb')]
        for key, claim in self.DISPOSITIONS.items():
            self.refuses(f'{key} must cite (receipt or review or index|review) evidence', record={key: claim}, evidence=board)
        # An empty reviewed association is a mapping, not a disposition, whether cited or not.
        self.associate([], models=[], name='empty.json')
        mapping = board + [('association', self.evidence_dir / 'empty.json')]
        for key, claim in self.DISPOSITIONS.items():
            for roles in (['association'], ['board', 'association']):
                self.refuses(f'{key} must cite', record={key: dict(claim, evidence=roles)}, evidence=mapping)
        # Review defects need the review report; a DRC report or index does not support them.
        self.refuses('open_defects must cite review evidence',
                     record={'open_defects': {'count': 0, 'class': 'review', 'evidence': ['index', 'drc']}})
        # Only unrouted stands on the board alone.
        self.attach({'routing': {'value': 'unrouted', 'evidence': ['board']}}, board)

        # Records sealed before this rule (planted as an earlier writer would have) are unknown on every surface.
        sealed = self.sealed(self.attach())
        association = next(e for e in sealed['evidence'] if e['role'] == 'association')
        for why, roles, evidence in (
                ('board only', ['board'], [e for e in sealed['evidence'] if e['role'] == 'board']),
                ('association only', ['association'], sealed['evidence']),
                ('board and association', ['board', 'association'],
                 [e for e in sealed['evidence'] if e['role'] == 'board'] + [association])):
            status = self.plant(dict(sealed, **self.cite(roles), evidence=evidence))
            with self.serving() as url:
                pages, reports = self.every_projection(url)
                direct = json.loads(urlopen(url + '/statuses/' + status + '/status.json').read())
            for report in reports:
                self.assertEqual((report['attached'], report['record']['id']), ('matched', status), why)
                self.assertEqual({key: report['shown'][key] for key in ('routing', 'adoption', 'qualification', 'scope',
                                                                        'open_defects')},
                                 dict.fromkeys(('routing', 'adoption', 'qualification', 'scope', 'open_defects')), why)
            for record in [direct] + [report['record'] for report in reports]:
                self.assertEqual([record[key]['value'] for key in ('routing', 'adoption', 'qualification', 'scope')],
                                 [None] * 4, why)
                self.assertEqual((record['open_defects']['count'], record['open_defects']['class']), (None, None), why)
            for where, body in zip(('index', 'detail', 'viewer'), pages):
                for key in ('Routing', 'Adoption', 'Qualification', 'Scope', 'Open defects'):
                    self.assertIn(f'<li>{key}: unknown (not recorded)</li>', body, (why, where))
                for text in ('Qualified (', 'Adopted (', 'Full design candidate', 'defects open in the recorded review',
                             'Routed: the bound board'):
                    self.assertNotIn(text, body, (why, where))

    def test_dispositions_cite_retained_reports_under_the_association(self):
        """The supported counterpart: each disposition cites a receipt, review or index the association covers."""
        (self.evidence_dir / 'REVIEW.md').write_text('Review: zero open defects; qualified; adopted.\n')
        record = iter8_record(**{key: dict(claim, evidence=['receipt', 'review', 'index'])
                                 for key, claim in self.DISPOSITIONS.items()})
        status = self.attach(record, self.evidence(review='REVIEW.md'))
        with self.serving() as url:
            pages, reports = self.every_projection(url)
            direct = json.loads(urlopen(url + '/statuses/' + status + '/status.json').read())
        self.assertEqual(direct, self.sealed(status))
        for report in reports:
            self.assertEqual(report['record'], {'id': status, **direct})
            self.assertEqual({key: report['shown'][key] for key in ('adoption', 'qualification', 'scope', 'open_defects')},
                             {'adoption': 'adopted', 'qualification': 'qualified', 'scope': 'full',
                              'open_defects': {'count': 0, 'class': 'review'}})
        for body in pages:
            for text in ('Qualified (recorded disposition', 'Adopted (recorded disposition)', 'Full design candidate',
                         'Open defects: 0 defects open in the recorded review'):
                self.assertIn(text, body)
        # Each disposition still needs the report it cites to be in the association.
        files = self.evidence(association=None, review='REVIEW.md')
        self.associate([e for e in files if e[0] != 'review'], name='no-review.json')
        self.refuses('review evidence is not in a reviewed association', record=record,
                     evidence=files + [('association', self.evidence_dir / 'no-review.json')])

    def test_direct_status_endpoint_serves_only_the_verified_record(self):
        """Review 433n H2: /statuses/ID/status.json verifies identity and subject like every other surface."""
        status = self.attach()
        stored = self.sealed(status)
        sealed = self.root / 'statuses' / status / 'status.json'
        manifest = self.root / 'bundles' / self.bundle / 'manifest.json'
        other = self.attach(iter8_record(adoption={'value': None, 'evidence': []}))
        target = '/statuses/' + status + '/status.json'

        def direct():
            with self.serving() as url:
                try:
                    return 200, json.loads(urlopen(url + target).read())
                except HTTPError as error:
                    return error.code, json.loads(error.read())

        self.assertEqual(direct(), (200, stored))
        refused = (404, {'id': status, 'attached': 'mismatched', 'record': None})
        data = sealed.read_bytes()
        for why, change in (('deleted', lambda: sealed.unlink()),
                            ('corrupt', lambda: sealed.write_bytes(b'{')),
                            ('rewritten', lambda: sealed.write_bytes(data.replace(b'"unqualified"', b'"qualified"'))),
                            ('re-encoded', lambda: sealed.write_bytes(json.dumps(json.loads(data)).encode())),
                            ('swapped', lambda: sealed.write_bytes((self.root / 'statuses' / other / 'status.json').read_bytes())),
                            ('symlink', lambda: (sealed.rename(sealed.with_name('real.json')), sealed.symlink_to('real.json')))):
            change()
            self.assertEqual(direct(), refused, why)
            for path in (sealed, sealed.with_name('real.json')):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            sealed.write_bytes(data)
            self.assertEqual(direct(), (200, stored), why)
        # A record no longer bound to its subject's stored bytes is refused too.
        original = manifest.read_bytes()
        manifest.write_bytes(original.replace(b'Iteration 8', b'Iteration 9'))
        self.assertEqual(direct(), refused)
        manifest.unlink()
        self.assertEqual(direct(), refused)
        manifest.write_bytes(original)
        self.assertEqual(direct(), (200, stored))

    def test_canonical_label_and_stage_are_shown_for_an_earlier_publication(self):
        manifest = self.root / 'bundles' / self.bundle / 'manifest.json'
        record = json.loads(manifest.read_text())
        record.update(label='iter8t models-only (UNROUTED) private-host', stage='models only', schema='agent-ee.gallery-bundle/2')
        manifest.write_text(json.dumps(record))
        listing = json.loads((self.root / 'index.json').read_text())
        listing[0].update(label=record['label'], stage=record['stage'])
        (self.root / 'index.json').write_text(json.dumps(listing))
        self.refuses('outside its vocabulary', record=iter8_record(label='iter8t'))
        self.refuses('outside its vocabulary', record=iter8_record(stage='models-only'))
        self.attach(iter8_record(label='Iteration 8', stage='placement'))
        with self.serving() as url:
            index, detail, viewer, _ = self.served(url)
        for body in (index, detail):
            self.assertIn('<h2>Latest: Iteration 8</h2><p>Stage: Placement', body)
            self.assertNotIn('private', body)
        self.assertIn('Unrouted', viewer)

    def test_historical_supplement_maps_to_diagnostic_1(self):
        identifier = '9' * 12 + '-' + '8' * 12
        legacy = {'schema': 'agent-ee.gallery-diagnostic/1', 'label': 'Historical free-space analysis',
                  'stage': 'historical', 'board_sha256': self.board_hash, 'image': {'width': 2, 'height': 2}}
        (self.root / 'diagnostics' / identifier).mkdir(parents=True)
        (self.root / 'diagnostics' / identifier / 'manifest.json').write_text(json.dumps(legacy))
        (self.root / 'diagnostics.json').write_text(json.dumps([dict(legacy, id=identifier)]))
        record = {'label': 'Diagnostic 1', 'stage': 'diagnostic',
                  'routing': {'value': 'unrouted', 'evidence': ['board', 'index']},
                  'adoption': {'value': 'not-adopted', 'evidence': ['index']}}
        self.attach(record, self.evidence(receipt=None, drc=None, model=None, **{'design-diff': None}),
                    subject='diagnostics/' + identifier)
        with self.serving() as url:
            bodies = [urlopen(url + path).read().decode() for path in ('/', f'/diagnostics/{identifier}/index.html')]
            self.assertEqual(json.loads(urlopen(url + f'/diagnostics/{identifier}/status.json').read())['attached'], 'matched')
        for body in bodies:
            section = body.split(f'id="diagnostic-{identifier}"')[1]
            self.assertIn('<h2>Diagnostic 1</h2>', section)
            self.assertIn('Stage: Diagnostic', section)
            self.assertIn('Unrouted: the bound board has no tracks, arcs or vias', section)
            self.assertIn('Not adopted (recorded disposition)', section)
            self.assertIn('Qualification: unknown (not recorded)', section)
            self.assertNotIn('free-space', section)
        self.assertIn('Latest: Iteration 8', bodies[0])

    def test_unsupported_values_and_unbound_evidence_are_refused(self):
        free = iter8_record(routing={'value': 'unrouted; see /home/private-host', 'evidence': ['board']})
        self.refuses('outside its vocabulary', record=free)
        self.refuses('outside its vocabulary', record=iter8_record(routing={'value': 'unknown', 'evidence': []}))
        self.refuses('outside its vocabulary', record=iter8_record(open_defects={'count': '9', 'class': 'silk',
                                                                                  'evidence': ['drc']}))
        self.refuses('outside its vocabulary', record=iter8_record(models=[{'reference': 'U3 GPS', 'provenance': None,
                                                                            'transparency': None, 'sha256': None,
                                                                            'evidence': []}]))
        self.refuses('fields must be among', record=dict(iter8_record(), notes='free text'))
        self.refuses('must cite the retained evidence', record=iter8_record(adoption={'value': 'not-adopted', 'evidence': []}))
        self.refuses('cites evidence that was not supplied', evidence=self.evidence(index=None))
        self.refuses('evidence role must be one of', evidence=self.evidence() + [('notes', self.evidence_dir / 'INDEX.md')])
        self.refuses('both a count and a class', record=iter8_record(open_defects={'count': 2, 'class': None,
                                                                                    'evidence': ['drc']}))
        self.refuses('subject must be', subject='bundles/../x')
        self.refuses('not a readable publication', subject='bundles/' + '0' * 12 + '-' + '0' * 12)
        (self.evidence_dir / 'INDEX copy.md').write_text('x')
        self.refuses('plain file name', evidence=self.evidence(index='INDEX copy.md'))

    def test_values_are_checked_against_the_bound_evidence(self):
        routed = STATUS_BOARD.replace('(gr_line', '(via (at 0 0) (size 0.6) (drill 0.3) (net 0)) (gr_line')
        (self.evidence_dir / 'other.kicad_pcb').write_text(routed)
        self.refuses('not the board this publication is bound to', evidence=self.evidence(board='other.kicad_pcb'))
        self.refuses('routing must cite the bound board', record=iter8_record(routing={'value': 'unrouted', 'evidence': ['index']}))
        self.refuses('contradicts the bound board', record=iter8_record(routing={'value': 'routed', 'evidence': ['board']}))
        self.refuses('drc violations 4 is not the 3', record=iter8_record(drc={'violations': 4, 'raw_unconnected_items': 3,
                                                                               'evidence': ['drc']}))
        self.refuses('raw_unconnected_items 9 is not the 3', record=iter8_record(
            drc={'violations': None, 'raw_unconnected_items': 9, 'evidence': ['drc']}))
        self.refuses('open_defects 3 is not the 2 silk', record=iter8_record(open_defects={'count': 3, 'class': 'silk',
                                                                                             'evidence': ['drc']}))
        self.refuses('silk open defects must cite the DRC', record=iter8_record(open_defects={'count': 2, 'class': 'silk',
                                                                                                 'evidence': ['index']}))
        self.refuses('models-only must cite the design-diff', record=iter8_record(scope={'value': 'models-only',
                                                                                          'evidence': ['receipt']}))
        for name, diff in (('moved.json', design_diff(placement='q' * 64)),
                           ('pad.json', design_diff(paths=('footprint[U3]/model@0a1b2c', 'footprint[U3]/pad[1]')))):
            (self.evidence_dir / name).write_text(json.dumps(diff))
            self.refuses('does not show only footprint model changes', evidence=self.evidence(**{'design-diff': name}))
        (self.evidence_dir / 'other.step').write_text('another model')
        self.refuses('hash must be the hash of cited model evidence', evidence=self.evidence(model='other.step'))
        (self.evidence_dir / 'bad-drc.json').write_text('[]')
        self.refuses('drc evidence is not a JSON object', evidence=self.evidence(drc='bad-drc.json'))
        self.refuses('already has a canonical label', record=iter8_record(label='Iteration 8'))

    def test_unknown_values_need_no_evidence_and_stay_unknown(self):
        unknown = {key: {'value': None, 'evidence': []} for key in ('routing', 'adoption', 'qualification', 'scope')}
        self.attach(dict(unknown, open_defects={'count': None, 'class': None, 'evidence': []}), evidence=[])
        with self.serving() as url:
            detail = self.served(url)[1]
        self.assertIn('Recorded limitations: Routing unknown; Adoption unknown; Qualification unknown; Scope unknown; '
                      'Open defects unknown; Other model provenance unknown', detail)

    def test_command_line_attaches_a_record(self):
        record = Path(self.tmp.name) / 'status.json'
        record.write_text(json.dumps(iter8_record()))
        arguments = ['--root', str(self.root), 'status', '--subject', 'bundles/' + self.bundle, '--record', str(record)]
        for role, path in self.evidence():
            arguments += ['--evidence', f'{role}={path}']
        result = subprocess.run([sys.executable, '-m', 'agent_ee.render_gallery', *arguments], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'statuses.json').read_text())[0]['id'], result.stdout.strip())

    def test_status_projection_is_idempotent(self):
        for text in itertools.islice(corpus(), 0, None, 37):
            record = status_record(text)
            self.assertEqual(gallery._status_record(record), record, text)
            self.assertNotIn(text, record)
        record = status_record('x')
        self.assertEqual(record['routing'], {'value': None, 'evidence': ['board']})
        self.assertEqual([m['reference'] for m in record['models']], [None, 'U3'])

    def test_oracle_fails_when_status_records_leak(self):
        """Negative control: a status projection that keeps free-text references is caught by the oracle."""
        from unittest import mock
        with mock.patch.object(gallery, '_DESIGNATOR', re.compile(r'.*', re.S)):
            failure = assert_leak_detected(self, surfaces, rounds=200, stride=7)
        self.assertIn('.models[', failure.path)
        check_publication(surfaces(failure.where), failure.where)  # the unpatched projection covers that text
