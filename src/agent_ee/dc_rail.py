"""Conservative whole-net DC bounds with explicitly ideal pad/junction contacts.

In a connected passive resistor network every branch current is at most the
total positive current injection. Summing every branch resistance therefore
bounds the voltage difference between any two terminals, including networks
with distributed loads. Native DRC supplies connectivity/isolation, not physics.
"""
import math
import re
from pathlib import Path

from .dc_path import TOOLS, exact_fields, positive
from .measurements import finite, evaluate_limits
from .storage import digest, identity, write_json

SCHEMA = 'agent-ee.dc-path/2'
POLICY = 'whole-net-ideal-copper-bound/1'
# These KiCad defaults cannot hide an open, short or change a conductor in the
# ideal-contact model. Every other ignored check is conservatively refused.
NONCONDUCTIVE_IGNORES = frozenset(('missing_courtyard', 'track_not_centered_on_via',
    'tuning_profile_track_geometries', 'footprint_filters_mismatch', 'footprint_type_mismatch'))
ROUNDING_ALLOWANCE = 1e-12


def qualification():
    return {'schema': 'agent-ee.dc-qualification/2', 'policy': POLICY,
            'pcbnew_version': '10.0.7', 'tools': TOOLS,
            'model_sha256': digest(__file__),
            'probe_sha256': digest(Path(__file__).with_name('dc_geometry_probe.py')),
            'dispatcher_sha256': digest(Path(__file__).with_name('dc_path.py')),
            'rounding_relative': ROUNDING_ALLOWANCE,
            'physics': 'sum rho*L/A; full-height circular barrels; ideal pad/junction contacts',
            'prerequisite': 'same-candidate full native DRC, no findings or ignored conductive checks'}


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def factor(value, label):
    if positive(value, label) > 1:
        raise ValueError(label + ' must not exceed one')
    return value


def validate_contract(c):
    exact_fields(c, 'schema model drc_check material layers width_factor_min barrels paths contact_model', 'contract')
    if c['schema'] != SCHEMA or c['model'] != POLICY or c['contact_model'] != 'ideal-pad-and-junction/1':
        raise ValueError('unsupported whole-net DC schema/model/contact policy')
    if not re.fullmatch('[a-z0-9][a-z0-9_.-]{0,63}', text(c['drc_check'], 'DRC check')):
        raise ValueError('invalid prerequisite DRC check id')
    exact_fields(c['material'], 'rho_upper_ohm_m temperature_c', 'material')
    positive(c['material']['rho_upper_ohm_m'], 'resistivity upper bound')
    finite(c['material']['temperature_c'], 'declared temperature')
    if not isinstance(c['layers'], dict) or not c['layers']:
        raise ValueError('explicit copper layer minima required')
    for layer, values in c['layers'].items():
        if not isinstance(layer, str) or not re.fullmatch(r'(F|B|In[1-9][0-9]?)\.Cu', layer):
            raise ValueError('invalid copper layer')
        exact_fields(values, 'thickness_min_m', 'layer')
        positive(values['thickness_min_m'], 'copper thickness minimum')
    factor(c['width_factor_min'], 'width factor minimum')
    exact_fields(c['barrels'], 'height_max_m plating_min_m hole_factor_min', 'barrel')
    positive(c['barrels']['height_max_m'], 'board height maximum')
    positive(c['barrels']['plating_min_m'], 'barrel plating minimum')
    factor(c['barrels']['hole_factor_min'], 'hole diameter factor minimum')
    if not isinstance(c['paths'], list) or not c['paths']:
        raise ValueError('explicit rail paths required')
    names = set()
    for p in c['paths']:
        exact_fields(p, 'id net source sink max_injected_current_a max_drop_v', 'path')
        name = text(p['id'], 'path id')
        if not re.fullmatch('[a-z][a-z0-9_]{0,47}', name) or name in names:
            raise ValueError('unique path identifiers required')
        names.add(name)
        text(p['net'], 'net')
        for k in ('source', 'sink'):
            exact_fields(p[k], 'ref pad', 'terminal')
            for field in ('ref', 'pad'):
                text(p[k][field], 'terminal ' + field)
        if p['source'] == p['sink']:
            raise ValueError('distinct terminal identities required')
        positive(p['max_injected_current_a'], 'maximum total positive current injection')
        positive(p['max_drop_v'], 'maximum drop')
    return c


def condition(c, path):
    return f'{POLICY};contract={identity(c)};path={path["id"]};T={c["material"]["temperature_c"]}C'


def limits(c):
    return {p['id'] + '_drop': {'max': p['max_drop_v'], 'unit': 'V',
            'condition': condition(c, p)} for p in c['paths']}


def barrel_resistance(drill_nm, c):
    """Use the declared hole lower bound as the barrel OUTER diameter.

    This is conservative for both a drilled diameter and a finished hole: the
    actual copper annulus lies at least this far out. Do not substitute solid
    copper when the declared plating is inconsistent with the hole diameter.
    """
    b = c['barrels']
    outer = positive(drill_nm, 'native drill') * 1e-9 * b['hole_factor_min']
    plating = b['plating_min_m']
    if 2 * plating >= outer:
        raise ValueError('barrel plating is inconsistent with hole diameter')
    area = math.pi * plating * (outer - plating)
    return positive(c['material']['rho_upper_ohm_m'] * b['height_max_m'] / area, 'barrel resistance')


def validate_geometry(g, c):
    exact_fields(g, 'schema pcbnew_version enabled_copper_layers tracks pads zones copper_graphics', 'geometry')
    if g['schema'] != 'agent-ee.dc-rail-geometry/1' or g['pcbnew_version'] != '10.0.7':
        raise ValueError('native DC geometry requires requalification')
    layers = g['enabled_copper_layers']
    if not isinstance(layers, list) or len(set(layers)) != len(layers) or set(layers) != set(c['layers']):
        raise ValueError('declared layers differ from enabled native copper layers')
    for key in ('tracks', 'pads', 'zones', 'copper_graphics'):
        if not isinstance(g[key], list):
            raise ValueError('missing native ' + key + ' inventory')
    if g['zones'] or g['copper_graphics']:
        raise ValueError('zones or copper graphics require a different qualified model')
    uuids = [text(item['uuid'], 'native UUID') for key in ('tracks', 'pads') for item in g[key]]
    if len(set(uuids)) != len(uuids):
        raise ValueError('duplicate native conductor identity')


def track_resistance(track, c):
    cls = track['native_class']
    if cls in ('PCB_TRACK', 'PCB_ARC'):
        width = positive(track['width_nm'], 'native track width') * 1e-9 * c['width_factor_min']
        length = positive(track['length_nm'], 'native track length') * 1e-9
        thickness = c['layers'][track['layer']]['thickness_min_m']
        r = positive(c['material']['rho_upper_ohm_m'] * length / (width * thickness), 'trace resistance')
        return r, 'trace'
    if cls == 'PCB_VIA':
        if track['via_type'] != 'THROUGH' or set(track['copper_layers']) != set(c['layers']):
            raise ValueError('only full-height through vias are qualified')
        return barrel_resistance(track['drill_nm'], c), 'barrel'
    raise ValueError('unsupported native conductor: ' + str(cls))


def pad_resistance(pad, c):
    layers = set(c['layers'])
    if not pad['copper_layers'] or not set(pad['copper_layers']) <= layers:
        raise ValueError('rail pad has unresolved copper layers')
    drill = pad['drill_nm']
    if not isinstance(drill, list) or len(drill) != 2:
        raise ValueError('native pad lacks drill geometry')
    if pad['attribute'] in ('SMD', 'CONN') and drill == [0, 0]:
        return 0., 'ideal-contact'
    if pad['attribute'] == 'PTH' and drill[0] == drill[1] and set(pad['copper_layers']) == layers:
        return barrel_resistance(drill[0], c), 'barrel'
    raise ValueError('unsupported rail pad or slotted/nonplated hole')


def inventory(g, c):
    nets = {p['net'] for p in c['paths']}
    modeled = {net: [] for net in nets}
    excluded = []
    for items, measure in ((g['tracks'], track_resistance), (g['pads'], pad_resistance)):
        for item in items:
            if item['net'] not in nets:
                excluded.append(item['uuid'])
                continue
            r, kind = measure(item, c)
            modeled[item['net']].append({'uuid': item['uuid'], 'kind': kind, 'resistance_ohm': r})
    return modeled, excluded


def bound(g, c):
    """Evaluate a native inventory; caller must first establish full native DRC."""
    validate_contract(c)
    validate_geometry(g, c)
    modeled, excluded = inventory(g, c)
    report = {'policy': POLICY, 'paths': {}, 'conductors_by_net': modeled,
              'excluded_other_net_uuids': sorted(excluded)}
    for p in c['paths']:
        terminals = []
        for k in ('source', 'sink'):
            matches = [pad for pad in g['pads'] if all(pad[field] == p[k][field] for field in ('ref', 'pad'))]
            if len(matches) != 1 or matches[0]['net'] != p['net']:
                raise ValueError('terminal does not resolve uniquely to declared net: ' + str(p[k]))
            terminals.append(matches[0]['uuid'])
        conductors = modeled[p['net']]
        trace = math.fsum(e['resistance_ohm'] for e in conductors if e['kind'] == 'trace')
        barrel = math.fsum(e['resistance_ohm'] for e in conductors if e['kind'] == 'barrel')
        resistance = positive((trace + barrel) * (1 + ROUNDING_ALLOWANCE), 'whole-net resistance bound')
        # This is a model bound, not a nominal measurement or a physical
        # contact/spreading guarantee. Round upward after accumulation.
        drop = math.nextafter(positive(resistance * p['max_injected_current_a'], 'drop bound'), math.inf)
        finite(drop, 'rounded drop bound')
        report['paths'][p['id']] = {'net': p['net'], 'terminals': terminals,
            'trace_resistance_ohm': trace, 'barrel_resistance_ohm': barrel,
            'resistance_bound_ohm': resistance, 'drop_bound_v': drop,
            'modeled_uuids': sorted(e['uuid'] for e in conductors if e['kind'] != 'ideal-contact'),
            'ideal_contact_uuids': sorted(e['uuid'] for e in conductors if e['kind'] == 'ideal-contact')}
    return report


def check(ctx, c, receipt):
    from .placement_check import prerequisite_drc
    report, prior = prerequisite_drc(ctx, c['drc_check'])
    receipt['prerequisite'] = prior
    if prior['projection'] or prior['outcome'] != 'pass':
        raise ValueError('whole-net DC bound requires a passing full native DRC')
    if prior['kicad_version'] != '10.0.7':
        raise ValueError('native DRC version requires DC requalification')
    if any(report[key] for key in ('violations', 'unconnected_items', 'schematic_parity')):
        raise ValueError('whole-net DC bound requires native DRC without findings')
    if any(item.get('key') not in NONCONDUCTIVE_IGNORES for item in report['ignored_checks']):
        raise ValueError('whole-net DC bound requires all conductive checks enabled')
    receipt['ignored_nonconductive_checks'] = report['ignored_checks']
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.dc_geometry_probe', '--rail',
                 ctx.candidate_path(ctx.scope['board']), '--out', '/work/geometry.json'], timeout=60)
    for path, sha in TOOLS.items():
        if digest(path) != sha:
            raise ValueError('DC native tool changed during extraction: ' + path)
    analysis = bound(ctx.load_json('geometry.json', 'native rail geometry'), c)
    metrics = {p['id'] + '_drop': {'value': analysis['paths'][p['id']]['drop_bound_v'],
        'lower': 0., 'upper': analysis['paths'][p['id']]['drop_bound_v'], 'unit': 'V',
        'condition': condition(c, p), 'envelope_policy': POLICY} for p in c['paths']}
    receipt.update(complete=True, network=analysis, metrics=metrics)
    write_json(ctx.work / 'analysis.json', receipt)
    margins = evaluate_limits(metrics, limits(c))
    ctx.result['details'].update(metrics=metrics, margins=margins, analysis=receipt,
        producer={'domain': 'electrical', 'policy': POLICY, 'qualification': qualification()})
    failed = any(v < 0 for m in margins.values() for v in m.values())
    ctx.result.update(execution='ok', outcome='defect' if failed else 'pass',
        summary='whole-net ideal copper bound ' + ('exceeds limits' if failed else 'meets limits'))
    ctx.result['limitations'].append('Ideal 1D copper and circular full-height barrels; ideal pad/junction contacts. '
        'No pad spreading, package/contact resistance, self-heating, AC or hardware validation. '
        'Physical material/dimension/current bounds are explicit declarations, not independently verified supplier guarantees.')
