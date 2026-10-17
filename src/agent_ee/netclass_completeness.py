"""Presence-only Default netclass qualification over captured project bytes."""
import json
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, unique_object
from .storage import digest, write_json

PROFILE = 'kicad-10.0.7-net-settings-5'
# Native NETCLASS(name, true) + saveNetclass, KiCad tag 10.0.7.
DEFAULT_KEYS = frozenset('name priority schematic_color pcb_color tuning_profile '
    'wire_width bus_width line_style clearance track_width via_diameter via_drill '
    'microvia_diameter microvia_drill diff_pair_width diff_pair_gap diff_pair_via_gap'.split())
SOURCES = {
    'common/project/net_settings.cpp': '3e720bafcaee71c8201c520d34406cf4453f038b9218a509fb444a8383112a65',
    'common/netclass.cpp': '7c7919828f9c0300e44fd6d360db7aa464704803457ddafeb13c7021a6f2a767'}


def reject_constant(value):
    raise ValueError(f'non-JSON number: {value}')


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def capability(params, files, inputs):
    exact_keys(params, 'project qualification')
    for key in ('project', 'qualification'):
        rel = str(safe_relative(params[key]))
        if rel != params[key] or rel not in files:
            raise ValueError(f'{key} must be captured')
    if not params['project'].endswith('.kicad_pro') or params['project'] == params['qualification']:
        raise ValueError('expected distinct project and qualification')
    if inputs is None:
        raise ValueError('qualification requires captured inputs')
    qualification = load_json(Path(inputs) / params['qualification'])
    exact_keys(qualification, 'schema profile native_writer fixture_sha256 sources')
    if (qualification['schema'] != 'agent-ee.netclass-qualification/1' or
            qualification['profile'] != PROFILE or qualification['sources'] != SOURCES or
            qualification['native_writer'] != '10.0.7-10.0.7~ubuntu24.04.1' or
            qualification['fixture_sha256'] != 'b6926d42f788c322d057974fd76feb970434914950aee8d01d66303706c1b9ff'):
        raise ValueError('unsupported netclass qualification')
    return {'immutable_inputs':[params['qualification']], 'predicates':['netclass:default-completeness']}


def evaluate(project):
    if not isinstance(project, dict) or not isinstance(project.get('net_settings'), dict):
        raise ValueError('missing net_settings object')
    settings = project['net_settings']
    meta = settings.get('meta')
    if not isinstance(meta, dict) or type(meta.get('version')) is not int:
        raise ValueError('missing net_settings schema version')
    if meta['version'] != 5:
        return None
    classes = settings.get('classes')
    if not isinstance(classes, list):
        raise ValueError('classes must be an array')
    names = set()
    for cls in classes:
        if not isinstance(cls, dict) or not isinstance(cls.get('name'), str) or not cls['name']:
            raise ValueError('class requires a nonempty name')
        if cls['name'] in names:
            raise ValueError('duplicate netclass name')
        names.add(cls['name'])
    default = next((c for c in classes if c['name'] == 'Default'), None)
    if default is None:
        return [{'class':'Default', 'keys':sorted(DEFAULT_KEYS)}]
    missing = sorted(DEFAULT_KEYS - default.keys())
    return [{'class':'Default', 'keys':missing}] if missing else []


def check_netclass_completeness(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.manifest['files'], ctx.inputs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    path = ctx.inputs / ctx.params['project']
    try:
        # Re-read captured bytes; source projects are never repaired by this check.
        missing = evaluate(load_json(path))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    report = {'schema':'agent-ee.netclass-result/1', 'candidate_id':ctx.manifest['candidate_id'],
              'project_sha256':digest(path),
              'qualification_sha256':digest(ctx.inputs / ctx.params['qualification']),
              'profile':PROFILE, 'missing':missing}
    write_json(ctx.work / 'netclasses.json', report)
    ctx.evidence('netclasses.json')
    ctx.result['details'].update(report)
    ctx.result['limitations'].append('Presence-only Default fields; no value/type, junction, plotting or electrical validation. '
        'Custom class omissions inherit and are not defects. Only net_settings schema 5 is qualified.')
    ctx.result.update(execution='unsupported' if missing is None else 'ok',
        outcome='unknown' if missing is None else ('defect' if missing else 'pass'),
        summary='Unsupported net_settings schema' if missing is None else f'{len(missing)} incomplete Default class(es)')
