"""Circuit-bound manufacturer-model evidence for the ADP7104 development reference.

Generate one declared operating-state sensitivity, then measure complete native
LTspice output. Neither successful model predicates nor source hashes establish
device bounds, candidate acceptance or qualification of the encrypted equations.
"""
from hashlib import sha256
import math
import re

MODEL_SHA256 = '23d5d9774f3a2508df39266749f8b3ae93bc655651cb2826faf37ee9d32748c9'
SYMBOL_SHA256 = '2729608ea88c85aa76feba64772916b9f749a066084c58d90ab3d4a4f118e9bc'
CASE = 'high-z-removal'
VARIABLES = ('time', 'V(vin)', 'V(vout)', 'I(Bsource)', 'I(Bbus)', 'I(Bload)', 'I(R6)')


def inputs(circuit_raw, parts_raw, model, symbol):
    """Return an exact finite deck and its source/application declaration.

    The normal production parts compiler must independently qualify the manifest
    and native netlist parity must establish the board's actual connectivity.
    This producer performs neither operation and does not infer their success.
    """
    from . import reverse_blocking as variant
    from .. import parts_manifest
    from ..catalog import strict_json
    from ..storage import json_bytes
    expected = strict_json(variant.circuit_bytes((variant.DATA / 'base-circuit.json').read_bytes()))
    next(c for c in expected['components'] if c['reference'] == 'U2')['value'] = 'ADS1115IDGST'
    if circuit_raw != json_bytes(expected):
        raise ValueError('power analysis requires the exact generated production circuit')
    manifest = parts_manifest.validate_manifest(strict_json(parts_raw))
    parts = {p['id']: p for p in manifest['parts']}
    if set(parts) != {c['reference'] for c in expected['components'] if not c['reference'].startswith('#')}:
        raise ValueError('power analysis requires the complete physical part roster')
    selections = {ref: {k: value[k] for k in ('mpn', 'lcsc')} for ref, value in variant.SELECTIONS.items()}
    selections.update(U2=dict(mpn='ADS1115IDGST', lcsc='C468683'),
        C1=dict(mpn='CL21A106KAYNNNE', lcsc='C15850'), C2=dict(mpn='CL21A106KAYNNNE', lcsc='C15850'),
        C3=dict(mpn='CC0603KRX7R9BB104', lcsc='C14663'),
        R3=dict(mpn='RC0603FR-072K2L', lcsc='C114662'), R4=dict(mpn='RC0603FR-072K2L', lcsc='C114662'))
    for ref, selection in selections.items():
        if any(parts[ref][key] != value for key, value in selection.items()):
            raise ValueError(ref + ': power-model purchasing application differs')
    if sha256(model).hexdigest() != MODEL_SHA256 or sha256(symbol).hexdigest() != SYMBOL_SHA256:
        raise ValueError('power analysis requires the captured ADI model and its exact pin-order symbol')
    deck = b'''* Generated acquisition-control-reverse-blocking-r1 operating-state sensitivity
* Model predictions only; no physical or board acceptance
.lib ADP7104-3.3.sub
Vtarget target 0 PWL(0 0 100u 0 110u 4.355 20m 4.355 20.001m 0)
Bsource 0 vin I=limit((V(target)-V(vin))*100,0,0.02)
Vhost host_active 0 PWL(0 0 10m 0 10.001m 1 20m 1 20.001m 0)
R5 vin 0 990
Rcin vin cin_node 0.05
C1 cin_node 0 2u
XU1 vin vout vin pg vout 0 ADP7104-3.3
Rcout vout cout_node 0.05
C2 cout_node 0 12.65u
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
    files = {CASE + '.cir': deck, 'ADP7104-3.3.sub': model, 'ADP7104-3.3.asy': symbol}
    declaration = dict(schema='agent-ee.acquisition-power-application/1', reference=variant.REFERENCE,
        case=CASE, circuit_sha256=sha256(circuit_raw).hexdigest(), parts_sha256=sha256(parts_raw).hexdigest(),
        files={path: sha256(raw).hexdigest() for path, raw in files.items()}, selections=selections,
        model_pin_map=[dict(order=i, name=name, native_pins=pins, net=net) for i, (name, pins, net) in enumerate([
            ('Vin', ['8'], 'VIN_PROTECTED'), ('Sense', ['2'], 'V3V3'), ('EN', ['5'], 'VIN_PROTECTED'),
            ('PG', ['7'], None), ('Vout', ['1'], 'V3V3'), ('GND', ['3', '6', '9'], 'GND')], 1)],
        conditions=dict(temperature_c=25, j2_external_load_a=0, source_target_v='4.355', source_limit_a='0.02',
            r5_ohm='990', r6_ohm='2222', pullup_ohm='2178', cin_f='0.000002',
            c2_f='0.00001265', c3_f='0.0000001265', capacitor_esr_ohm='0.05',
            loaded_interval_s=['0.010001', '0.020'], removal_complete_s='0.020001', end_s='0.08'),
        interpretation=[
            'R5 minimum and R6 maximum with small input/large output capacitance exercise slow output discharge.',
            'After removal the non-sinking source has a zero-volt target; it can clamp negative VIN, '
            'so this is not a physically disconnected input. It supplies no positive power at a zero target.',
            'Both host pullups are high impedance before 10ms and after 20.001ms; loaded between these times.',
            'The 300uA ADC plus 1mA engineering reserve is applied only during the loaded interval; zero after removal '
            'is a conservative slow-discharge assumption, not an ADC current model or external-load permission.',
            'C2/C3 are separate. 10uF and 100nF multiplied by 1.1*1.15 give 12.65uF and 0.1265uF; '
            'Cin 2uF and constant 50mohm ESR are explicit sensitivity inputs, not certified joint capacitor bounds.',
            'D1/path drop is represented by the protected-node 4.355V source target; diode reversal/leakage is not simulated.',
            '55mV reverse-blocking threshold is typical, not a clamp guarantee. The old TPS 0.3V differential predicate does not apply.',
            'Final voltage is reported; no arbitrary discharge-time requirement is introduced.'
        ], accepted=False, file_release='not_assessed')
    files['power-application.json'] = json_bytes(declaration)
    return files, declaration


def read_trace(raw):
    """Read this finite native ASCII trace; refuse incomplete or malformed output."""
    if not 1 <= len(raw) <= 128 * 1024 * 1024:
        raise ValueError('LTspice trace byte limit')
    text = raw.decode('ascii').replace('\r\n', '\n')
    if text.count('Values:\n') != 1 or text.count('Variables:\n') != 1:
        raise ValueError('LTspice trace requires one complete header and value block')
    header, values = text.split('Values:\n')
    if ('Plotname: Transient Analysis\n' not in header
            or 'Flags: real double forward nocompression\n' not in header):
        raise ValueError('unsupported LTspice trace type')
    def count(label):
        matches = re.findall(r'^' + re.escape(label) + r':\s*(\d+)\s*$', header, re.M)
        if len(matches) != 1:
            raise ValueError('missing or duplicate LTspice ' + label)
        return int(matches[0])
    n = count('No. Points')
    if not 2 <= n <= 1000000 or count('No. Variables') != len(VARIABLES):
        raise ValueError('LTspice trace point/variable limits differ')
    variables = [line.split() for line in header.split('Variables:\n')[1].splitlines() if line.strip()]
    types = dict(zip(VARIABLES, ['time', 'voltage', 'voltage', *(['device_current'] * 4)]))
    if (len(variables) != len(VARIABLES) or any(len(row) != 3 for row in variables)
            or [row[0] for row in variables] != [str(i) for i in range(len(VARIABLES))]
            or variables[0][1] != 'time' or {row[1] for row in variables} != set(VARIABLES)
            or any(row[2] != types[row[1]] for row in variables)):
        raise ValueError('LTspice trace variables differ from the generated measurement set')
    # LTspice orders device currents by name, independently of .save ordering.
    # Bind by declared identity, retaining a stable order for the measurements.
    names = [row[1] for row in variables]
    order = [names.index(name) for name in VARIABLES]
    lines = values.rstrip().splitlines()
    if len(lines) != n * len(VARIABLES):
        raise ValueError('LTspice trace is truncated or contains extra values')
    rows = []
    for i in range(n):
        at = i * len(VARIABLES)
        first = lines[at].split()
        if len(first) != 2 or first[0] != str(i):
            raise ValueError('LTspice trace sample index differs')
        native = [float(first[1]), *(float(lines[at + k]) for k in range(1, len(VARIABLES)))]
        row = [native[k] for k in order]
        if not all(math.isfinite(value) for value in row) or (rows and row[0] <= rows[-1][0]):
            raise ValueError('LTspice trace contains nonfinite values or nonincreasing time')
        rows.append(row)
    if rows[0][0] != 0 or abs(rows[-1][0] - .08) > 1e-12:
        raise ValueError('LTspice trace does not cover the complete generated 80ms interval')
    if max(b[0] - a[0] for a, b in zip(rows, rows[1:])) > .000002000001:
        raise ValueError('LTspice trace exceeds the generated maximum time step')
    return rows


def completed_log(raw):
    """Accept the observed successful Gmin recovery, retaining all fatal guards."""
    log = raw.decode('ascii').replace('\r\n', '\n')
    recovery = ('Direct Newton iteration failed to find operating point.\n'
                'Starting Gmin stepping\n'
                'Gmin stepping succeeded in finding the operating point.\n')
    # ADI documents Newton, Gmin, source stepping and pseudo-transient as
    # successive operating-point methods. A failed first method is recoverable
    # only with its explicit success before completion, not from exit0 alone.
    # https://github.com/analogdevicesinc/ltspice-reference/blob/main/ai_ref/SIMULATION-COMMANDS-REFERENCE.md#op--dc-operating-point
    recovered = (log.count(recovery) == 1 and 'Total elapsed time:' in log
                 and log.index(recovery) < log.index('Total elapsed time:'))
    diagnostic = log.replace(recovery, '', 1) if recovered else log
    if ('temp = 25\n' not in log or 'Total elapsed time:' not in log
            or re.search(r'(?i)\b(fatal|error|failed|singular matrix|timestep too small)\b', diagnostic)):
        raise ValueError('LTspice log lacks successful nominal 25C completion or reports a solver failure')
    return 'gmin-after-newton' if recovered else 'not_observed'


def measurements(raw, log):
    """Report measured model predicates; process/circuit custody stays separate."""
    log_raw = log
    recovery = completed_log(log_raw)
    rows = read_trace(raw)
    steady = [r for r in rows if .011 <= r[0] <= .019]
    removal = [r for r in rows if r[0] >= .020002]
    if not steady or not removal:
        raise ValueError('LTspice trace lacks operating/removal windows')
    low, high = min(r[2] for r in steady), max(r[2] for r in steady)
    observation = dict(samples=len(rows), time_range_s=[rows[0][0], rows[-1][0]],
        steady_vout_range_v=[low, high], steady_source_max_a=max(r[3] for r in steady),
        vin_range_v=[min(r[1] for r in rows), max(r[1] for r in rows)],
        vout_range_v=[min(r[2] for r in rows), max(r[2] for r in rows)],
        source_range_a=[min(r[3] for r in rows), max(r[3] for r in rows)],
        post_removal_bus_max_abs_a=max(abs(r[4]) for r in removal),
        post_removal_reserve_max_abs_a=max(abs(r[5]) for r in removal),
        post_removal_source_range_a=[min(r[3] for r in removal), max(r[3] for r in removal)],
        post_removal_source_power_range_w=[min(r[1] * r[3] for r in removal), max(r[1] * r[3] for r in removal)],
        removal_max_out_minus_in_v=max(r[2] - r[1] for r in removal),
        final_vin_v=rows[-1][1], final_vout_v=rows[-1][2],
        first_rail_floor_s=next((r[0] for r in rows if r[2] >= 3.234), None),
        post_removal_bleed_range_a=[min(r[6] for r in removal), max(r[6] for r in removal)])
    return dict(schema='agent-ee.acquisition-power-model-measurements/1', case=CASE,
        trace_sha256=sha256(raw).hexdigest(), log_sha256=sha256(log_raw).hexdigest(),
        operating_point_recovery=recovery,
        observations=observation,
        model_predicates=dict(steady_regulator_rail=3.234 <= low <= high <= 3.366,
            no_output_overshoot=max(r[2] for r in rows) <= 3.366,
            input_absolute_rating=min(r[1] for r in rows) >= -.3 and max(r[1] for r in rows) <= 22,
            output_absolute_rating=min(r[2] for r in rows) >= -.3 and max(r[2] for r in rows) <= 20,
            source_current_limit=min(r[3] for r in rows) >= -1e-9 and max(r[3] for r in rows) <= .020000001,
            host_high_impedance_on_removal=max(abs(r[4]) for r in removal) <= 1e-12,
            no_reserve_load_on_removal=max(abs(r[5]) for r in removal) <= 1e-12,
            no_positive_source_power_on_removal=max(r[1] * r[3] for r in removal) <= 0),
        accepted=False, file_release='not_assessed',
        limitations=['Sampled encrypted-model result only; no continuous-time or guaranteed device bounds.',
                    'The zero-target non-sinking source can supply current at negative VIN; '
                    'this ground-clamp termination is not physical disconnection.',
                    'Model predicates do not establish source/runtime custody, capacitor/thermal applicability, '
                    'final-board voltage delivery, source-role qualification or independent engineering acceptance.'])


def film_measurements(raw, log):
    result = measurements(raw, log)
    rows = read_trace(raw)
    prior = [r for r in rows if .019502 <= r[0] <= .020000]
    removal = [r for r in rows if r[0] >= .020002]
    if not prior:
        raise ValueError('film trace lacks the pre-removal high-impedance interval')
    result['observations']['pre_removal_host_max_abs_a'] = max(abs(r[4]) for r in prior)
    result['observations']['disconnected_source_max_abs_a'] = max(abs(r[3]) for r in removal)
    result['model_predicates']['host_released_before_removal'] = max(abs(r[4]) for r in prior) <= 1e-12
    result['model_predicates']['source_physically_open'] = max(abs(r[3]) for r in removal) <= 1e-12
    result['condition'] = 'adp7104-film-25c-physical-open-sensitivity'
    result['limitations'] = [
        'Sampled reference-model sensitivity, not guaranteed continuous-time device behavior.',
        'The source is disconnected and host released first in this model; actual sequencing is an operating condition.',
        'MU R/L values are20C/DC0V references; all C values scaled by0.8. Nominal25C applicability needs source-backed judgment.',
        'No final-board, thermal, source-role or independent file acceptance follows from model predicates.']
    return result
