"""Original two-channel hierarchical design for block inventory tests.

Authored for agent-ee; no donor sheets, symbols or footprints. Channel 2 is
channel 1 with references +100, a CH2 suffix and the strap moved from U101
pin 3 (to GND) to U201 pin 4 (to VDD), like a strapped second copy of a channel.
"""
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT_NAME = 'owned.kicad_sch'


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-original-block-inventory/' + name))


def _prop(key, value, hide=True):
    return (f'(property "{key}" "{value}" (at 0 0 0) (effects (font (size 1 1))'
            + (' (hide yes)' if hide else '') + '))')


def _definition(name, count):
    pins = ''.join(f'(pin passive line (at 0 {-2.54 * i:g} 0) (length 2.54) '
                   f'(name "{i + 1}" (effects (font (size 1 1)))) (number "{i + 1}" (effects (font (size 1 1)))))'
                   for i in range(count))
    lib, part = name.split(':')
    return (f'(symbol "{name}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + _prop('Reference', part[0]) + _prop('Value', part)
            + f'(symbol "{part}_1_1" {pins}))')


LIBRARY = ''.join(_definition(n, c) for n, c in
                  (('Owned:MCU', 6), ('Device:R', 2), ('Device:C', 2), ('Owned:Port', 4)))


def _symbol(ref, lib, value, x, y, footprint, instance, lcsc=None):
    count = {'Owned:MCU': 6, 'Owned:Port': 4}.get(lib, 2)
    pins = ''.join(f'(pin "{i + 1}" (uuid "{uid(instance + ref + str(i))}"))' for i in range(count))
    return (f'(symbol (lib_id "{lib}") (at {x} {y} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) '
            f'(uuid "{uid(instance + ref)}")' + _prop('Reference', ref) + _prop('Value', value)
            + _prop('Footprint', footprint) + (_prop('LCSC', lcsc) if lcsc else '') + pins
            + f'(instances (project "owned" (path "{instance}" (reference "{ref}") (unit 1)))))')


def _label(kind, name, x, y, extra=''):
    return (f'({kind} "{name}" {extra}(at {x:g} {y:g} 0) (effects (font (size 1 1)) (justify left)) '
            f'(uuid "{uid(kind + name + str(x) + str(y))}"))')


def _wire(a, b):
    return (f'(wire (pts (xy {a[0]:g} {a[1]:g}) (xy {b[0]:g} {b[1]:g})) (stroke (width 0) (type default)) '
            f'(uuid "{uid("wire" + str(a) + str(b))}"))')


def channel(index):
    """One channel sheet: SIG -> R -> MID -> MCU, MCU OUT -> R -> anonymous -> C -> GND."""
    base = index * 100
    instance = f'/{uid("root")}/{uid("sheet" + str(index))}'
    u, r1, r2, c = (f'{p}{base + 1}' for p in ('U', 'R', 'R', 'C'))
    r2 = f'R{base + 2}'
    items = [_symbol(u, 'Owned:MCU', 'OWNED-MCU', 100, 50, 'owned_fp:QFN-6', instance),
             _symbol(r1, 'Device:R', '10k', 60, 50, 'Resistor_SMD:R_0603_1608Metric', instance, 'C25804'),
             _symbol(r2, 'Device:R', '1k', 140, 50, 'Resistor_SMD:R_0603_1608Metric', instance, 'C21190'),
             _symbol(c, 'Device:C', '100n', 160, 50, '', instance)]
    # MCU pins at (100, 50 + 2.54 i): 1 VDD, 2 GND, 3 PB1 strap, 4 PA8 strap, 5 IN, 6 OUT.
    items += [_label('global_label', 'VDD', 100, 50, '(shape input) '),
              _label('global_label', 'GND', 100, 52.54, '(shape input) '),
              _label('label', 'MID', 100, 60.16), _label('label', 'MID', 60, 52.54),
              _label('hierarchical_label', 'SIG', 40, 50, '(shape input) '), _wire((40, 50), (60, 50)),
              _wire((100, 62.7), (140, 50)), _wire((140, 52.54), (160, 50)),
              _label('global_label', 'GND', 160, 52.54, '(shape input) '),
              _label('hierarchical_label', 'OUT', 140, 40, '(shape output) '), _wire((140, 40), (140, 50))]
    strap, open_pin = (55.08, 57.62) if index == 1 else (57.62, 55.08)
    items += [_label('global_label', 'GND' if index == 1 else 'VDD', 100, strap, '(shape input) '),
              f'(no_connect (at 100 {open_pin:g}) (uuid "{uid("nc" + str(index))}"))']
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uid("chan" + str(index))}") '
            f'(paper "A4") (lib_symbols {LIBRARY}) {"".join(items)})\n')


def root():
    instance = '/' + uid('root')
    items = [_symbol('X1', 'Owned:Port', 'PORT', 20, 120, '', instance)]
    # X1 pins: 1 CH1_SIG, 2 CH2_SIG, 3 GND, 4 VDD.
    items += [_label('label', 'CH1_SIG', 20, 120), _label('label', 'CH2_SIG', 20, 122.54),
              _label('global_label', 'GND', 20, 125.08, '(shape input) '),
              _label('global_label', 'VDD', 20, 127.62, '(shape input) ')]
    for index in (1, 2):
        x = 60 + 60 * index
        items.append(f'''(sheet (at {x} 100) (size 30 30) (stroke (width 0) (type default))
            (fill (color 0 0 0 0)) (uuid "{uid('sheet' + str(index))}")
            (property "Sheetname" "CH{index}" (at {x} 99 0) (effects (font (size 1 1))))
            (property "Sheetfile" "sheets/chan{index}.kicad_sch" (at {x} 131 0) (effects (font (size 1 1))))
            (pin "SIG" input (at {x} 105 180) (effects (font (size 1 1))) (uuid "{uid('pin' + str(index))}"))
            (pin "OUT" output (at {x + 30} 105 0) (effects (font (size 1 1))) (uuid "{uid('out' + str(index))}")))''')
        items += [_label('label', f'CH{index}_SIG', x, 105), _label('label', f'CH{index}_OUT', x + 30, 105)]
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uid("root")}") (paper "A4") '
            f'(lib_symbols {LIBRARY}) {"".join(items)})\n')


FOOTPRINT = '(footprint "QFN-6" (layer "F.Cu") (attr smd) (fp_text reference "REF**" (at 0 0) (layer "F.SilkS")))\n'


def write_repo(path, *, extra=None):
    """Create a committed owned design; returns the full commit."""
    path = Path(path)
    files = {ROOT_NAME: root(), 'sheets/chan1.kicad_sch': channel(1), 'sheets/chan2.kicad_sch': channel(2),
             'libs/owned_fp.pretty/QFN-6.kicad_mod': FOOTPRINT,
             'libs/Owned.kicad_sym': '(kicad_symbol_lib (version 20241209) (generator "owned"))\n',
             'fp-lib-table': '(fp_lib_table (version 7) (lib (name "owned_fp")(type "KiCad")'
                             '(uri "${KIPRJMOD}/libs/owned_fp.pretty")(options "")(descr "")))\n',
             'sym-lib-table': '(sym_lib_table (version 7) (lib (name "Owned")(type "KiCad")'
                              '(uri "${KIPRJMOD}/libs/Owned.kicad_sym")(options "")(descr "")))\n',
             'docs/PLAN.md': '# Owned plan\n\n## Channel\n\nSIG enters, OUT leaves.\n'}
    files.update(extra or {})
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git = lambda *args: subprocess.run(['git', '-C', str(path), *args], check=True,
                                       capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=Owned', '-c', 'user.email=owned@example.invalid', 'commit', '-qm', 'Owned design')
    return git('rev-parse', 'HEAD')


def inventory(commit):
    pending = [dict(id='ch1-function', statement='The channel conditions SIG.', document='plan',
                    locator='## Channel', conditions=['Bench'], blocked_by='No applicable check family')]
    return dict(schema='agent-ee.block-inventory/1',
                design=dict(name='owned', commit=commit, root=ROOT_NAME, license='Owned test fixture', plan='plan'),
                documents=dict(plan='docs/PLAN.md'),
                unassigned=[dict(instance='/', references=['X1'], reason='Root connector')],
                blocks=[dict(id='ch1', role='channel', title='Channel 1', origin='Owned',
                             upstream_license='Owned test fixture (MIT proposed)', pending=pending,
                             instances=[dict(path='/CH1/', references='all')],
                             boundary=dict(document='plan', locator='## Channel')),
                        dict(id='ch2', role='channel', title='Channel 2', origin='Owned',
                             upstream_license='Owned test fixture (MIT proposed)', pending=[],
                             instances=[dict(path='/CH2/', references=['C201', 'R201', 'R202', 'U201'])],
                             boundary=dict(document='plan', locator='## Channel'))])
