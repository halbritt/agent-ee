"""Original bounded symbols for text-placement regression; no stock CAD bytes."""
import hashlib

from agent_ee.schematic_emitter import Q, _dump


def inputs(rotation=0, mirror=None):
    font = ['effects', ['font', ['size', '1.27', '1.27']]]

    def prop(name, text, x, y, angle):
        return ['property', Q(name), Q(text), ['at', str(x), str(y), str(angle)], font]

    def pin(number, name, y, angle, length):
        return ['pin', 'passive', 'line', ['at', '0', str(y), str(angle)], ['length', str(length)],
                ['name', Q(name), font], ['number', Q(number), font]]

    resistor = ['symbol', Q('TwoPin'), ['in_bom', 'yes'], ['on_board', 'yes'],
                prop('Reference', 'R', -1.5, 0, 90), prop('Value', 'TwoPin', -3, 0, 90),
                ['symbol', Q('TwoPin_1_1'),
                 ['rectangle', ['start', '-0.8', '2'], ['end', '0.8', '-2'],
                  ['stroke', ['width', '0.2'], ['type', 'default']], ['fill', ['type', 'none']]],
                 pin('1', 'A', 3.81, 270, 1.81), pin('2', 'B', -3.81, 90, 1.81)]]
    probe = ['symbol', Q('Probe'), ['pin_names', ['offset', '0']], ['in_bom', 'no'], ['on_board', 'yes'],
             prop('Reference', 'TP', 0, 2.8, 0), prop('Value', 'Probe', 0, 5.4, 0),
             ['symbol', Q('Probe_1_1'),
              ['circle', ['center', '0', '0'], ['radius', '0.85'],
               ['stroke', ['width', '0.2'], ['type', 'default']], ['fill', ['type', 'none']]],
              pin('1', 'CONTACT', -2.54, 90, 1.69)]]
    data = ('\n'.join(_dump(['kicad_symbol_lib', ['version', '20251024'],
                              ['generator', 'agent-ee-owned-test'], resistor, probe])) + '\n').encode()
    intent = dict(schema='agent-ee.connectivity-intent/1',
                  libraries=[dict(name='OwnedText', path='text.kicad_sym', sha256=hashlib.sha256(data).hexdigest())],
                  components=[dict(id='r', reference='R18', value='499 kohm 0.1% 25ppm/C', lib_id='OwnedText:TwoPin',
                                   footprint='', units=[1]),
                              dict(id='tp', reference='TP1', value='TEST_POINT', lib_id='OwnedText:Probe',
                                   footprint='', units=[1]),
                              dict(id='tp2', reference='TP2', value='SECOND_POINT', lib_id='OwnedText:Probe',
                                   footprint='', units=[1])],
                  nets=[dict(id='a', name='INPUT_VOLTAGE', terminals=[dict(component='r', pin=dict(number='1')),
                                                                          dict(component='tp', pin=dict(number='1'))]),
                        dict(id='b', name='DIVIDER_UPPER', terminals=[dict(component='r', pin=dict(number='2')), dict(component='tp2', pin=dict(number='1'))])],
                  no_connects=[])
    layout = dict(schema='agent-ee.schematic-layout/1', namespace='ae41cb86-c046-5da2-86f5-d03af3c855e9',
                  project='text', presentation='functional',
                  transforms={'r': dict(rotation=rotation, mirror=mirror)})
    return intent, {'text.kicad_sym': data}, layout
