"""Original schematic-lint fixtures; every symbol and coordinate is authored here.

Test-only construction, not a production emitter. ``Lint.kicad_sym`` is owned
and was normalised with ``kicad-cli sym upgrade --force`` (KiCad 10.0.7).
"""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from agent_ee.schematic_emitter import Q, _dump, _scan
from agent_ee.symbol_library import children

HERE = Path(__file__).resolve().parent
LIBRARY = HERE / 'Lint.kicad_sym'
PROJECT = 'lint'


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/schematic-lint/' + name))


ROOT_UUID = uid('root')


def embedded(names=None):
    """Library symbols renamed to ``Lint:NAME`` as KiCad caches them in a schematic."""
    out = []
    for node in children(_scan(LIBRARY.read_bytes()), 'symbol'):
        if names is None or node[1] in names:
            out.append(['symbol', Q('Lint:' + node[1]), *node[2:]])
    return out


def _effects(hide=False):
    return [['at', '0', '0', '0'], *([['hide', 'yes']] if hide else []),
            ['effects', ['font', ['size', '1.27', '1.27']]]]


def _prop(name, value, x, y, hide=False):
    return ['property', Q(name), Q(value), ['at', mm(x), mm(y), '0'], *([['hide', 'yes']] if hide else []),
            ['effects', ['font', ['size', '1.27', '1.27']]]]


def mm(value):
    text = f'{value:.4f}'.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def symbol(ref, lib, x, y, rot=0, mirror=None, path=None, value=None, hide=(), show=(),
           pins=('1', '2', '3')):
    """One placed instance. ``hide``/``show`` override default property visibility."""
    path = path or '/' + ROOT_UUID
    power = ref.startswith('#')
    name = lib.split(':', 1)[1]
    node = ['symbol', ['lib_id', Q(lib)], ['at', mm(x), mm(y), str(rot)]]
    if mirror:
        node.append(['mirror', mirror])
    node += [['unit', '1'], ['exclude_from_sim', 'no'], ['in_bom', 'yes'], ['on_board', 'yes'],
             ['dnp', 'no'], ['uuid', Q(uid(path + '/' + ref))]]
    visible = {'Reference': not power and 'Reference' not in hide, 'Value': 'Value' not in hide,
               'Footprint': 'Footprint' in show, 'Datasheet': 'Datasheet' in show,
               'Description': 'Description' in show}
    values = {'Reference': ref, 'Value': value or name, 'Footprint': 'LintFP:Generic' if not power else '',
              'Datasheet': '', 'Description': 'owned fixture part'}
    for index, key in enumerate(('Reference', 'Value', 'Footprint', 'Datasheet', 'Description')):
        node.append(_prop(key, values[key], x + 3.81, y - 1.27 + 2.54 * index, hide=not visible[key]))
    for number in pins:
        node.append(['pin', Q(number), ['uuid', Q(uid(path + '/' + ref + '/' + number))]])
    node.append(['instances', ['project', Q(PROJECT), ['path', Q(path), ['reference', Q(ref)], ['unit', '1']]]])
    return node


def wire(a, b, key):
    return ['wire', ['pts', ['xy', mm(a[0]), mm(a[1])], ['xy', mm(b[0]), mm(b[1])]],
            ['stroke', ['width', '0'], ['type', 'default']], ['uuid', Q(uid('wire/' + key))]]


def label(kind, text, x, y, key, angle=0):
    node = [kind, Q(text)]
    if kind != 'label':
        node.append(['shape', 'input'])
    node += [['at', mm(x), mm(y), str(angle)], ['effects', ['font', ['size', '1.27', '1.27']],
             ['justify', 'left']], ['uuid', Q(uid('label/' + key))]]
    return node


def junction(x, y, key):
    return ['junction', ['at', mm(x), mm(y)], ['diameter', '0'], ['color', '0', '0', '0', '0'],
            ['uuid', Q(uid('junction/' + key))]]


def sheet(name, file, x, y, w, h, pins=(), page='2'):
    node = ['sheet', ['at', mm(x), mm(y)], ['size', mm(w), mm(h)], ['exclude_from_sim', 'no'],
            ['in_bom', 'yes'], ['on_board', 'yes'], ['dnp', 'no'],
            ['stroke', ['width', '0.1524'], ['type', 'solid']], ['fill', ['color', '0', '0', '0', '0.0000']],
            ['uuid', Q(uid('sheet/' + name))],
            _prop('Sheetname', name, x, y - 0.7),
            _prop('Sheetfile', file, x, y + h + 0.6)]
    for pin_name, px, py, angle in pins:
        node.append(['pin', Q(pin_name), 'input', ['at', mm(px), mm(py), str(angle)],
                     ['uuid', Q(uid('sheet/' + name + '/pin/' + pin_name))],
                     ['effects', ['font', ['size', '1.27', '1.27']], ['justify', 'left' if angle == 180 else 'right']]])
    node.append(['instances', ['project', Q(PROJECT), ['path', Q('/' + ROOT_UUID), ['page', Q(page)]]]])
    return node


def schematic(items, file_uuid=ROOT_UUID, title=None, lib_names=None):
    """Serialised KiCad 10 schematic bytes; ``title`` maps title/date/rev/company."""
    title = {'title': 'Lint fixture', 'date': '2026-10-17', 'rev': 'A'} if title is None else title
    root = ['kicad_sch', ['version', '20260306'], ['generator', Q('eeschema')], ['generator_version', Q('10.0')],
            ['uuid', Q(file_uuid)], ['paper', Q('A4')]]
    block = ['title_block']
    for key in ('title', 'date', 'rev', 'company'):
        if key in title:
            block.append([key, Q(title[key])])
    if len(block) > 1:
        root.append(block)
    root.append(['lib_symbols', *embedded(lib_names)])
    root += list(items)
    root.append(['sheet_instances', ['path', Q('/'), ['page', Q('1')]]])
    root.append(['embedded_fonts', 'no'])
    return ('\n'.join(_dump(root)) + '\n').encode()


def child_path(name):
    return '/' + ROOT_UUID + '/' + uid('sheet/' + name)


# Pin connection offsets on the sheet (y down) for rotation 0 instances.
R_PINS = {'1': (0, -3.81), '2': (0, 3.81)}
C_PINS = R_PINS
LDO_PINS = {'1': (-7.62, 0), '2': (0, 5.08), '3': (7.62, 0)}
LED_PINS = {'1': (-3.81, 0), '2': (3.81, 0)}


def at(origin, offset):
    return (round(origin[0] + offset[0], 4), round(origin[1] + offset[1], 4))


def stub(point, name, key, kind='label'):
    return label(kind, name, point[0], point[1], key)


def power(ref, lib, point, rot=0):
    """Power symbol whose single pin sits exactly on ``point``."""
    return symbol(ref, lib, point[0], point[1], rot=rot, pins=('1',))


def divider(inverted=False):
    """+3V3 / R1 / MID / R2 / GND, drawn with power symbols and two MID wires.

    Correct: supply resistor R1 above ground resistor R2. Inverted: R2 drawn
    above R1, with the ground symbol on top and the supply below. The two MID
    wires share an endpoint at the MID label.
    """
    top, bottom = (50.8, 50.8), (50.8, 66.04)
    r_supply, r_ground = (bottom, top) if inverted else (top, bottom)
    supply_pin, mid_supply = (at(r_supply, R_PINS['2']), at(r_supply, R_PINS['1'])) if inverted else \
        (at(r_supply, R_PINS['1']), at(r_supply, R_PINS['2']))
    ground_pin, mid_ground = (at(r_ground, R_PINS['1']), at(r_ground, R_PINS['2'])) if inverted else \
        (at(r_ground, R_PINS['2']), at(r_ground, R_PINS['1']))
    items = [symbol('R1', 'Lint:R', *r_supply, value='10k', pins=('1', '2')),
             symbol('R2', 'Lint:R', *r_ground, value='4k7', pins=('1', '2'))]
    # Inverted: the power symbols are turned 180 degrees so their graphics point
    # away from the resistors; only the divider order is wrong.
    rot = 180 if inverted else 0
    items += [power('#PWR01', 'Lint:+3V3', supply_pin, rot), power('#PWR02', 'Lint:GND', ground_pin, rot)]
    middle = (50.8, 58.42)
    items += [wire(mid_supply, middle, 'mid-a'), wire(middle, mid_ground, 'mid-b'),
              label('label', 'MID', *middle, 'mid')]
    return items


def regulator(swapped=False):
    """U1 with input capacitor C1 beside VI and output capacitor C2 beside VO."""
    reg = (101.6, 50.8)
    left, right = (88.9, 58.42), (114.3, 58.42)
    c_in, c_out = (right, left) if swapped else (left, right)
    items = [symbol('U1', 'Lint:LDO', *reg, value='LDO'),
             symbol('C1', 'Lint:C', *c_in, value='1u', pins=('1', '2')),
             symbol('C2', 'Lint:C', *c_out, value='1u', pins=('1', '2')),
             label('label', 'VIN', *at(reg, LDO_PINS['1']), 'vin-u1'),
             label('label', 'VOUT', *at(reg, LDO_PINS['3']), 'vout-u1'),
             label('label', 'VIN', *at(c_in, C_PINS['1']), 'vin-c1'),
             label('label', 'VOUT', *at(c_out, C_PINS['1']), 'vout-c2'),
             power('#PWR03', 'Lint:GND', at(reg, LDO_PINS['2'])),
             power('#PWR04', 'Lint:GND', at(c_in, C_PINS['2'])),
             power('#PWR05', 'Lint:GND', at(c_out, C_PINS['2']))]
    return items


def leds(order=('D1', 'D2', 'D3')):
    """Series chain D1 -> D2 -> D3 (K of one to A of the next), placed left to right in ``order``."""
    items = []
    for index, ref in enumerate(order):
        items.append(symbol(ref, 'Lint:LED', 50.8 + 12.7 * index, 81.28, value='red', pins=('1', '2')))
    places = {ref: (50.8 + 12.7 * index, 81.28) for index, ref in enumerate(order)}
    nets = {('D1', '2'): 'LED_A', ('D1', '1'): 'LED_1', ('D2', '2'): 'LED_1',
            ('D2', '1'): 'LED_2', ('D3', '2'): 'LED_2', ('D3', '1'): 'LED_K'}
    for (ref, number), name in sorted(nets.items()):
        items.append(label('label', name, *at(places[ref], LED_PINS[number]), f'led-{ref}-{number}'))
    return items


def usb(negative='USB_DN'):
    """Matched series resistors on USB_DP and its partner, horizontal."""
    items = []
    for index, (ref, name) in enumerate((('R4', 'USB_DP'), ('R5', negative))):
        origin = (139.7 + 12.7 * index, 81.28)
        items += [symbol(ref, 'Lint:R', *origin, rot=90, value='22', pins=('1', '2')),
                  label('label', name, origin[0] + 3.81, origin[1], f'usb-{ref}-1'),
                  label('label', name + '_C', origin[0] - 3.81, origin[1], f'usb-{ref}-2')]
    return items


SHEET_AT, SHEET_SIZE = (152.4, 101.6), (25.4, 12.7)
RAIL_PIN = (152.4, 106.68)


def child(rail_label=False, title=None):
    """Child sheet: R6 between VCC_3V3 (top) and GND (bottom)."""
    path = child_path('power')
    origin = (63.5, 63.5)
    items = [symbol('R6', 'Lint:R', *origin, path=path, value='1k', pins=('1', '2')),
             symbol('#PWR11', 'Lint:GND', *at(origin, R_PINS['2']), path=path, pins=('1',))]
    if rail_label:
        items.append(label('hierarchical_label', 'VCC_3V3', *at(origin, R_PINS['1']), 'child-rail', angle=90))
    else:
        items.append(symbol('#PWR10', 'Lint:VCC_3V3', *at(origin, R_PINS['1']), path=path, pins=('1',)))
    return schematic(items, file_uuid=uid('child-file'), title=title)


def design(variant='control'):
    """``{filename: bytes}`` for the control design or one named mutation."""
    items = divider(inverted=variant == 'divider-inverted') + regulator(swapped=variant == 'caps-swapped')
    items += leds(('D1', 'D3', 'D2') if variant == 'led-order' else ('D1', 'D2', 'D3'))
    items += usb('USB_DM' if variant == 'unpaired' else 'USB_DN')
    sheet_at = (152.5, 101.6) if variant == 'off-grid-sheet' else SHEET_AT
    pins = [('VCC_3V3', RAIL_PIN[0], RAIL_PIN[1], 180)] if variant == 'rail-label' else []
    items.append(sheet('power', 'power.kicad_sch', *sheet_at, *SHEET_SIZE, pins=pins))
    if variant == 'rail-label':
        items.append(symbol('#PWR12', 'Lint:VCC_3V3', *RAIL_PIN, pins=('1',)))
    if variant == 'overlap':
        items.append(symbol('R7', 'Lint:R', 50.8, 50.8, value='10k', pins=('1', '2')))
    if variant == 'wire-cross':
        # SENSE wire passes straight through R8 pin 1's connection point.
        items += [symbol('R8', 'Lint:R', 127, 101.6, value='1k', pins=('1', '2')),
                  wire((121.92, 97.79), (132.08, 97.79), 'sense'),
                  label('label', 'SENSE', 121.92, 97.79, 'sense-a'),
                  label('label', 'SENSE_R', 127, 105.41, 'sense-r8')]
    if variant == 'wire-overlap':
        items.append(wire((50.8, 55.88), (50.8, 60.96), 'mid-overlap'))
    title = {'title': 'Lint fixture', 'date': '2026-10-17', 'rev': ''} if variant == 'no-revision' else None
    names = None
    if variant == 'unresolved':
        names = [n for n in ('R', 'C', 'LDO', 'ASYM', '+3V3', 'VCC_3V3', 'GND')]
    return {'lint.kicad_sch': schematic(items, title=title, lib_names=names),
            'power.kicad_sch': child(rail_label=variant == 'rail-label')}


# Hand-derived from KiCad's convention (library y up, sheet y down, rotation
# counter-clockwise, mirror on the sheet after rotation): ASYM's body is the
# library rectangle (0, 0)-(7.62, 2.54); pin 1 sits at (-2.54, 1.27) and
# pin 2 at (2.54, 5.08). Offsets are relative to the instance origin, in mm.
TRANSFORMS = [
    # (rotation, mirror, body (x0, y0, x1, y1), pin 1, pin 2)
    (0, None, (0, -2.54, 7.62, 0), (-2.54, -1.27), (2.54, -5.08)),
    (90, None, (-2.54, -7.62, 0, 0), (-1.27, 2.54), (-5.08, -2.54)),
    (180, None, (-7.62, 0, 0, 2.54), (2.54, 1.27), (-2.54, 5.08)),
    (270, None, (0, 0, 2.54, 7.62), (1.27, -2.54), (5.08, 2.54)),
    (0, 'x', (0, 0, 7.62, 2.54), (-2.54, 1.27), (2.54, 5.08)),
    (0, 'y', (-7.62, -2.54, 0, 0), (2.54, -1.27), (-2.54, -5.08)),
    (90, 'x', (-2.54, 0, 0, 7.62), (-1.27, -2.54), (-5.08, 2.54)),
    (270, 'y', (-2.54, 0, 0, 7.62), (-1.27, -2.54), (-5.08, 2.54)),
]
TRANSFORM_ORIGIN = (30.48, 101.6)
TRANSFORM_STEP = 20.32


def transforms():
    """ASYM at every transform, a label at each expected body centre and a decoy outside it."""
    items = []
    for index, (rot, mirror, body, _, _) in enumerate(TRANSFORMS):
        ox, oy = TRANSFORM_ORIGIN[0] + TRANSFORM_STEP * index, TRANSFORM_ORIGIN[1]
        items.append(symbol(f'U{index + 1}', 'Lint:ASYM', ox, oy, rot=rot, mirror=mirror, pins=('1', '2')))
        cx, cy = ox + (body[0] + body[2]) / 2, oy + (body[1] + body[3]) / 2
        items.append(label('label', f'IN{index + 1}', round(cx, 4), round(cy, 4), f'in-{index}'))
        # The untransformed body centre lies outside every other transform's body.
        decoy = (ox - 3.81, oy + 1.27) if index == 0 else (ox + 3.81, oy - 1.27)
        items.append(label('label', f'OUT{index + 1}', round(decoy[0], 4), round(decoy[1], 4), f'out-{index}'))
    return {'transforms.kicad_sch': schematic(items)}
