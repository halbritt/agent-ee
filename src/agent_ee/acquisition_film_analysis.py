"""One circuit-bound physical-open sensitivity using the captured MU model.

Rubycon's model is a 20 C/DC 0 V reference, not a product guarantee. Changing
LTspice's temperature to 25 C does not supply missing temperature coefficients.
"""
from decimal import Decimal
from hashlib import sha256

from . import acquisition_power_analysis as power
from .catalog import strict_json
from .storage import json_bytes

CIRCUIT_SHA256 = '44a6f38c1bb764ca9ad257b9c6f6e1da6722bd384f74b7cb804e244b0ddcdfd4'
PARTS_SHA256 = '44a05242208806564a409ddd936c48b74b26c490a0dfec6d8587762890a2bc4a'
RUBYCON_SHA256 = '3c76560bdddc22d7e7c2def881184c00f3565fa6df34a5217ffa97db6140c321'
CASE = 'high-z-removal'
CONDITION = 'adp7104-film-25c-physical-open-sensitivity'


def inputs(circuit, parts, regulator, symbol, film):
    pins = [(circuit, CIRCUIT_SHA256), (parts, PARTS_SHA256),
            (regulator, power.MODEL_SHA256), (symbol, power.SYMBOL_SHA256), (film, RUBYCON_SHA256)]
    if any(sha256(raw).hexdigest() != pin for raw, pin in pins):
        raise ValueError('film application requires the exact generated circuit, parts and vendor models')
    lines, capacitors = [], 0
    for line in film.decode('cp932').splitlines():
        fields = line.split()
        if fields and fields[0].startswith('C'):
            if len(fields) != 4:
                raise ValueError('captured film capacitor record changed')
            fields[3] = str(Decimal(fields[3]) * Decimal('0.8'))
            line = ' '.join(fields)
            capacitors += 1
        if fields and fields[0].lower() in ('.subckt', '.ends'):
            line = line.replace('25MU225MB23225', 'FILM_0P8')
        if not line.startswith('*'):
            lines.append(line)
    if capacitors != 5:
        raise ValueError('captured film model topology changed')
    derived = ('* Local sensitivity: every vendor capacitance x0.8; R/L unchanged.\n' +
               '\n'.join(lines) + '\n').encode('ascii')
    deck = b'''* Generated acquisition-control-film-r1; model sensitivity, not acceptance
.lib ADP7104-3.3.sub
.lib film-0p8.sub
Vtarget target 0 PWL(0 0 100u 0 110u 4.355)
Venable source_active 0 PWL(0 1 20m 1 20.001m 0)
Bsource 0 vin I=if(V(source_active)>0.5,limit((V(target)-V(vin))*100,0,0.02),0)
Vhost host_active 0 PWL(0 0 10m 0 10.001m 1 19.5m 1 19.501m 0)
R5 vin 0 990
XC1 vin 0 FILM_0P8
XU1 vin vout vin pg vout 0 ADP7104-3.3
XC2 vout 0 FILM_0P8
Rc3 vout c3_node 0.05
C3 c3_node 0 0.1265u
R6 vout 0 2222
Bbus vout 0 I=V(host_active)*2*V(vout)/2178
Bload vout 0 I=V(host_active)*0.0013*tanh(max(0,V(vout))/0.01)
.temp 25
.tran 0 80m 0 2u
.options plotwinsize=0 numdgt=15
.save V(vin) V(vout) I(Bsource) I(Bbus) I(Bload) I(R6)
.end
'''
    files = {CASE + '.cir': deck, 'ADP7104-3.3.sub': regulator,
             'ADP7104-3.3.asy': symbol, 'film-0p8.sub': derived}
    selections = {p['id']: {k: p[k] for k in ('mpn', 'lcsc')}
                  for p in strict_json(parts)['parts']}
    declaration = dict(schema='agent-ee.acquisition-power-application/1',
        reference='acquisition-control-film-r1', case=CASE,
        circuit_sha256=CIRCUIT_SHA256, parts_sha256=PARTS_SHA256,
        files={p: sha256(raw).hexdigest() for p, raw in files.items()}, selections=selections,
        source_model_sha256=RUBYCON_SHA256,
        conditions=dict(regulator_temperature_c=25, capacitor_model_reference_temperature_c=20,
            capacitor_model_reference_bias_v=0, capacitance_scale='0.8',
            resistance_inductance='Unchanged vendor reference values', c3_f='0.0000001265',
            c3_esr_ohm='0.05', j2_external_load_a=0, source_target_v='4.355', source_limit_a='0.02',
            r5_ohm='990', r6_ohm='2222', pullup_ohm='2178',
            host_release_complete_s='0.019501', source_open_complete_s='0.020001', end_s='0.08'),
        changed_hypothesis='Actual zero source current after disconnection, including negative VIN; '
            'host and ADC/reserve load released 499us before source removal starts. '
            'The previous experiment instead used a zero-target clamp and concurrent release.',
        limitations=[
            'All five capacitances scaled together by0.8 is a sensitivity, not a guaranteed joint RLC corner.',
            'Rubycon reference model is20C/DC0V. No ESR temperature law or universal product bound inferred.',
            'Nominal25C capacitor applicability requires primary-source engineering judgment; this run alone cannot supply it.',
            'C3 remains the previous separate X7R sensitivity; source series diode/path is represented by4.355V.',
            'Host release is an explicit external operating condition, not circuitry implemented on this board.',
            'The reserve is not permitted external loading; its removal conservatively slows discharge.',
            'Redistribution restrictions apply to the Rubycon source and derived model; retained local evidence only.'
        ], accepted=False, file_release='not_assessed')
    files['power-application.json'] = json_bytes(declaration)
    return files, declaration


measurements = power.film_measurements
