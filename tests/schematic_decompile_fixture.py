"""Test-only construction of original native controls; not a production emitter."""
import hashlib
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from agent_ee.symbol_library import children, parse_sexpr

FIXTURE = Path(__file__).parent / 'fixtures' / 'schematic-decompile'
LIB = (FIXTURE / 'Owned.kicad_sym').read_bytes()
MANIFEST = [dict(name='Owned', path='Owned.kicad_sym', sha256=hashlib.sha256(LIB).hexdigest())]
CAPTURES = {'Owned.kicad_sym': LIB}
ROOT_UUID = '3f052147-c99b-50e2-a696-9a8456151c6e'


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-42-control/' + name))


def serialize(node):
    if isinstance(node, list):
        if node[0] in ('number', 'name', 'reference'):
            return '(' + node[0] + ' ' + ' '.join(serialize(x) if isinstance(x, list) else json.dumps(x) for x in node[1:]) + ')'
        return '(' + str(node[0]) + (' ' if len(node) > 1 else '') + ' '.join(serialize(x) for x in node[1:]) + ')'
    return node if (re.fullmatch(r'-?\d+(?:\.\d+)?|yes|no', node) or node in {'passive', 'line', 'input', 'default', 'x', 'y'}) else json.dumps(node, ensure_ascii=False)


def control(points, wires=(), labels=(), junctions=(), symbol='Terminal', rotations=None, mirrors=None):
    embedded = children(parse_sexpr(LIB), 'symbol')
    embedded = json.loads(json.dumps(embedded))
    for node in embedded:
        node[1] = 'Owned:' + node[1]
    root = ['kicad_sch', ['version', '20250114'], ['generator', 'eeschema'],
            ['uuid', ROOT_UUID], ['paper', 'A4'], ['lib_symbols', *embedded]]
    for i, (x, y) in enumerate(points):
        ref = 'U' + str(i + 1)
        rotation = str((rotations or {}).get(i, 0))
        node = ['symbol', ['lib_id', 'Owned:' + symbol], ['at', str(x), str(y), rotation],
            ['unit', '1'], ['in_bom', 'yes'], ['on_board', 'yes'], ['dnp', 'no'], ['uuid', uid(ref)],
            ['property', 'Reference', ref, ['at', str(x), str(y), '0']],
            ['property', 'Value', 'unique-' + ref, ['at', str(x), str(y), '0']],
            ['instances', ['project', 'control', ['path', '/' + ROOT_UUID,
                ['reference', ref], ['unit', '1']]]]]
        if mirrors and i in mirrors:
            node.append(['mirror', mirrors[i]])
        root.append(node)
    for i, (a, b) in enumerate(wires):
        root.append(['wire', ['pts', ['xy', *map(str, a)], ['xy', *map(str, b)]],
            ['stroke', ['width', '0'], ['type', 'default']], ['uuid', uid('w' + str(i))]])
    for i, (name, point) in enumerate(labels):
        root.append(['global_label', name, ['shape', 'input'], ['at', *map(str, point), '0'],
            ['effects', ['font', ['size', '1', '1']]], ['uuid', uid('label' + str(i))]])
    for i, point in enumerate(junctions):
        root.append(['junction', ['at', *map(str, point)], ['diameter', '0'],
            ['color', '0', '0', '0', '0'], ['uuid', uid('j' + str(i))]])
    return (serialize(root) + '\n').encode()
