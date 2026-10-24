"""Compile captured selected parts into the existing connectivity intent.

Circuit topology, values and decisions are authored engineering inputs. This
producer replays selection/CAD evidence and binds physical instances without
inventing authority or treating a successful binding as circuit acceptance.
"""
import argparse
import hashlib
from decimal import Decimal
from pathlib import Path

from . import parts_manifest
from .catalog import decimal_text, quantity, strict_json
from .connectivity_ir import _read_json, resolve_intent
from .schematic_emitter import native_sheet_paths
from .stage_receipts import _relative, validate_step
from .storage import digest, identity, write_json
from .tool_source import snapshot

SCHEMA = 'agent-ee.parts-production/1'


def _nominal(value, unit):
    try:
        return quantity(value, unit)
    except ValueError:
        return quantity(value + unit, unit)


def _check_nominal(row, component):
    if row is None:
        return
    nominal = {'resistor': ('resistance', 'ohm'), 'capacitor': ('capacitance', 'F'),
               'inductor': ('inductance', 'H')}.get(row['category'])
    if nominal:
        attribute, unit = nominal
        interval = row['normalized'].get(attribute)
        if interval is None or _nominal(component['value'], unit) != \
                (Decimal(interval['min']) + Decimal(interval['max'])) / 2:
            raise ValueError(component['reference'] + ': selected nominal differs from circuit value')


def _check_named_nominal(root, part, component):
    from .assembly_readiness import _passive_kind
    from .part_record import captured_attributes
    kind = _passive_kind(component)
    if kind is None or part.get('selection', {}).get('kind') != 'named':
        return None
    attribute, unit = {'R': ('Resistance', 'ohm'), 'C': ('Capacitance', 'F')}[kind]
    attributes, origin = captured_attributes(root, part)
    matches = [a for a in attributes or [] if a.get('attribute_name_en') == attribute]
    try:
        if len(matches) != 1:
            raise ValueError('one unambiguous captured attribute required')
        nominal = quantity(matches[0].get('attribute_value_name'), unit)
        if nominal < 0 or (kind == 'C' and nominal == 0) or _nominal(component['value'], unit) != nominal:
            raise ValueError('selected nominal differs from circuit value')
    except (ValueError, TypeError) as exc:
        raise ValueError(component['reference'] + ': named passive nominal: ' + str(exc)) from exc
    return dict(reference=component['reference'], sheet=component.get('sheet', 'root'),
                attribute=attribute, raw=matches[0]['attribute_value_name'], unit=unit,
                nominal=decimal_text(nominal), source=origin)


def compile_parts(root, manifest, circuit, layout, *, deviations=None, ratings=None):
    """Return a dependency-bound report and new connectivity intent, or refuse.

    All paths are relative to the captured bundle. The circuit is ordinary
    connectivity-intent/1 or /2; an empty footprint requests the selected CAD
    footprint. A nonempty footprint, symbol or existing supplier property must
    agree with the selected part. Nets and pin selectors remain authored.
    """
    root = Path(root)
    raw = {name: parts_manifest.read_captured(root, name)
           for name in (manifest, circuit, layout, deviations, ratings) if name is not None}
    document = parts_manifest.validate_manifest(strict_json(raw[manifest]))
    qualified = parts_manifest.qualify_manifest(document, root)
    if qualified['missing_provenance']:
        raise ValueError('required parts provenance unavailable: ' + str(qualified['missing_provenance']))
    intent = _read_json(raw[circuit])
    sheets = native_sheet_paths(intent, raw[layout])
    dependencies = dict(qualified['dependencies'])
    dependencies.update({name: hashlib.sha256(data).hexdigest() for name, data in raw.items()})
    libraries = {}
    for lib in intent['libraries']:
        data = parts_manifest.read_captured(root, lib['path'])
        if hashlib.sha256(data).hexdigest() != lib['sha256']:
            raise ValueError('circuit library hash mismatch: ' + lib['path'])
        dependencies[lib['path']] = lib['sha256']
        libraries[lib['path']] = data
    assignments = strict_json(parts_manifest.read_captured(root, document['cad']['assignments']['path']))
    assigned_libraries = {lib['name']: lib for lib in assignments['libraries']}
    supplied_libraries = {lib['name']: lib for lib in intent['libraries']}
    allocated = {(instance['sheet'], instance['reference']): part
                 for part in document['parts'] for instance in part['instances']}
    choices = {entry['id']: entry['chosen'] for entry in (qualified['selection'] or {}).get('parts', [])}
    from ._connectivity_hierarchy import INTENT_SCHEMA, scoped
    refs, used, named_nominals = {}, set(), []
    for component in intent['components']:
        key = sheets[component.get('sheet', 'root')], component['reference']
        part = allocated.get(key)
        if part is None:
            # Power symbols are virtual; only the resolver can establish that.
            continue
        if key in used:
            raise ValueError('duplicate circuit instance: ' + str(key))
        used.add(key)
        if component['lib_id'] != part['lib_id'] or component['footprint'] not in ('', part['footprint']):
            raise ValueError(component['reference'] + ': circuit CAD differs from selected part')
        name = part['lib_id'].split(':')[0]
        if assigned_libraries.get(name) != supplied_libraries.get(name):
            raise ValueError(component['reference'] + ': circuit symbol library differs from qualified CAD')
        _check_nominal(choices.get(part.get('selection_id')), component)
        checked = _check_named_nominal(root, part, component)
        if checked is not None:
            named_nominals.append(checked)
        component['footprint'] = part['footprint']
        cid = scoped(component['sheet'], component['id']) if intent['schema'] == INTENT_SCHEMA else component['id']
        refs[cid] = dict(part_ref=part['id'])
        if key[0] != '/':
            refs[cid]['instances'] = [dict(sheet=key[0], reference=key[1])]
    missing = [key for key, part in allocated.items() if key not in used and part['required']]
    if missing:
        raise ValueError('required part instance missing from circuit: ' + str(missing))
    resolved = resolve_intent(intent, libraries)
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('circuit resolution failed: ' + str(resolved['findings']))
    for component in resolved['components']:
        if not component['power'] and (sheets[component.get('sheet', 'root')], component['reference']) not in used:
            raise ValueError('physical circuit instance has no selected part: ' + component['reference'])
    from .binding_fill import fill
    intent, binding_report = fill(root, intent, document,
        dict(schema='agent-ee.part-refs/1', components=refs),
        strict_json(raw[deviations]) if deviations is not None else
        dict(schema='agent-ee.part-deviations/1', deviations=[]), captures='.',
        ratings=strict_json(raw[ratings]) if ratings is not None else None)
    dependencies.update(binding_report['dependencies'])
    resolved = resolve_intent(intent, libraries)
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('bound circuit resolution failed: ' + str(resolved['findings']))
    # Recheck at the publication boundary; all emitted identities refer to these bytes.
    for name, expected in dependencies.items():
        if hashlib.sha256(parts_manifest.read_captured(root, name)).hexdigest() != expected:
            raise ValueError('parts input changed during compilation: ' + name)
    return dict(schema=SCHEMA, status='produced', acceptance_ready=False,
                dependencies=dict(sorted(dependencies.items())), bindings=binding_report['components'],
                binding_report=binding_report,
                named_nominals=named_nominals,
                deviations_source=deviations, ratings_source=ratings,
                optional_absent=[dict(sheet=s, reference=r) for s, r in sorted(allocated.keys() - used)],
                intent_sha256=identity(intent), resolved_sha256=resolved['canonical_sha256'],
                limitations=[parts_manifest.LIMIT,
                    'Circuit function, ratings applicability, population and final native membership need independent checks.']), intent


def configuration(source_root, *, bundle, manifest, circuit, layout, output, deviations=None, ratings=None):
    if bundle != '.':
        _relative(bundle, 'parts bundle')
    for path in (manifest, circuit, layout, output, deviations, ratings):
        if path is None:
            continue
        _relative(path, 'parts stage path')
    if bundle != '.' and (output == bundle or output.startswith(bundle + '/')):
        raise ValueError('parts output must be outside immutable bundle')
    optional = [arg for name, path in (('deviations', deviations), ('ratings', ratings)) if path is not None
                for arg in ('--' + name, path)]
    if bundle == '.':
        optional += ['--adopt-as', output]
    return validate_step(dict(schema='agent-ee.stage-step/1', stage='parts', step='compile-parts',
        argv=['/usr/bin/env', 'PYTHONPATH=/tool/agent-ee', '/usr/bin/python3', '-B', '-P',
              '-m', 'agent_ee.parts_stage', '--bundle', '/candidate' if bundle == '.' else '/candidate/' + bundle,
              '--manifest', manifest, '--circuit', circuit, '--layout', layout, '--out', '/work/out', *optional],
        mounts=[dict(source=str(snapshot(source_root)), at='/tool/agent-ee')], source='/tool/agent-ee',
        version_argv=['/usr/bin/python3', '--version'],
        inputs=[p if bundle == '.' else bundle + '/' + p
                for p in (manifest, circuit, layout, deviations, ratings) if p is not None],
        writes={output: 'out/connectivity-intent.json'}, artifacts=['out/report.json'],
        adoptable_exits=[0], timeout_s=300))


def _arguments(parser):
    parser.add_argument('--bundle', type=Path, required=True)
    for name in ('manifest', 'circuit', 'layout'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--deviations', help='captured explicit assembly deviations; omitted means none')
    parser.add_argument('--ratings', help='captured passive rating sources for the existing derivation checks')
    parser.add_argument('--adopt-as', help='receipted output path; must not overwrite an immutable dependency')
    parser.add_argument('--out', type=Path, required=True)


def configure_parser(parser):
    sub = parser.add_subparsers(dest='parts_command', required=True)
    _arguments(sub.add_parser('compile', help='bind selected parts to captured circuit intent'))


def execute(args):
    args.out.mkdir(parents=True, exist_ok=False)
    try:
        report, intent = compile_parts(args.bundle, args.manifest, args.circuit, args.layout,
                                      deviations=args.deviations, ratings=args.ratings)
        if args.adopt_as is not None:
            _relative(args.adopt_as, 'adopted parts output')
            if any(path == args.adopt_as or path.startswith(args.adopt_as + '/') or
                   args.adopt_as.startswith(path + '/') for path in report['dependencies']):
                raise ValueError('parts output would overwrite immutable dependency: ' + args.adopt_as)
    except (ValueError, OSError) as exc:
        write_json(args.out / 'report.json', dict(schema=SCHEMA, status='refused', acceptance_ready=False,
                                                 reason=str(exc)))
        print('parts not produced: ' + str(exc))
        return 1
    write_json(args.out / 'connectivity-intent.json', intent)
    # Report binds the actual serialized output, including its final newline.
    report['output_sha256'] = digest(args.out / 'connectivity-intent.json')
    write_json(args.out / 'report.json', report)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    _arguments(parser)
    try:
        return execute(parser.parse_args(argv))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'parts stage unavailable: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
