#!/usr/bin/env python3
"""Materialize this pod's pinned core inputs with existing capture contracts.

This project recipe copies already acquired bytes; it does not fetch, select,
approve, or emit a schematic. Vendor PDFs, stock CAD, and source design bytes
stay in the caller's external output directory.
"""
import argparse
import hashlib
import json
from pathlib import Path

from agent_ee.part_decisions import compile_decisions
from agent_ee.part_record import prepare_record
from agent_ee.parts_manifest import qualify_manifest
from agent_ee.storage import identity
from agent_ee.symbol_library import parse_library, resolve_symbol


def validate_pin_allocation(allocation, selections, root):
    """Check every physical pin against the captured library, without ERC claims."""
    expected = {p['component']: p for p in selections['parts']}
    if len(allocation['components']) != len(expected):
        raise ValueError('core pin allocation component coverage differs')
    seen = set()
    for component in allocation['components']:
        component_id = component['component']
        if component_id in seen or component_id not in expected:
            raise ValueError('duplicate or unknown allocation component')
        seen.add(component_id)
        part = expected[component_id]
        if any(component[k] != part[k] for k in ('reference', 'lib_id')) or component['part_ref'] != part['id']:
            raise ValueError('allocation component identity differs')
        library, symbol = part['lib_id'].split(':')
        path = Path(root) / 'symbols' / (library + '.kicad_sym')
        raw = path.read_bytes()
        pins, _, _ = resolve_symbol(parse_library(raw), symbol, str(path), hashlib.sha256(raw).hexdigest())
        expected_pins = {p['number']: p['name'] for p in pins}
        declared = component['pins']
        if len(declared) != len(expected_pins) or {p['number']: p['name'] for p in declared} != expected_pins:
            raise ValueError('allocation physical pin coverage differs: ' + component_id)
        for pin in declared:
            if pin['disposition'] == 'no_connect' and pin['net'] is None:
                continue
            if pin['disposition'] != 'connect' or not isinstance(pin['net'], str) or not pin['net'].strip():
                raise ValueError('explicit connected net or no-connect required')


def build(output, roots):
    here = Path(__file__).resolve().parent
    lock = json.loads((here / 'source-lock.json').read_text())
    selections = json.loads((here / 'core-selections.json').read_text())
    # Validate every source before publishing any output, including unused
    # members of a capture whose acquisition manifest binds the whole batch.
    inputs = {}
    for item in lock['files']:
        payload = (roots[item['root']] / item['source']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError('source pin mismatch: ' + item['path'])
        if item['path'] in inputs:
            raise ValueError('duplicate output member: ' + item['path'])
        inputs[item['path']] = payload
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in inputs.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def write(path, value):
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x') as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')

    def blob(path):
        return dict(path=path, sha256=hashlib.sha256((output / path).read_bytes()).hexdigest())

    for name in ('design-decisions.md', 'source-lock.json', 'core-selections.json', 'part-decisions.declaration.json',
                 'pin-allocation.json'):
        (output / name).write_bytes((here / name).read_bytes())
    validate_pin_allocation(json.loads((output / 'pin-allocation.json').read_text()), selections, output)
    decision = dict(**blob('design-decisions.md'), locator='Core selection decisions')
    libraries = []
    for name in sorted({p['lib_id'].split(':')[0] for p in selections['parts']}):
        libraries.append(dict(name=name, **blob('symbols/' + name + '.kicad_sym')))
    footprints = {}
    for part in selections['parts']:
        library, member = part['footprint'].split(':')
        footprints.setdefault(library, {})[member] = blob('footprints/' + library + '.pretty/' + member + '.kicad_mod')
    cad = dict(schema='agent-ee.footprint-libraries/1', complete=True,
               libraries=[dict(name=library, members=[members[k] for k in sorted(members)])
                          for library, members in sorted(footprints.items())])
    assignments = dict(schema='agent-ee.footprint-assignments-input/1', libraries=libraries,
                       components=[dict(reference=p['id'], lib_id=p['lib_id'], footprint=p['footprint'],
                                        assembly='smd', connector=None) for p in selections['parts']])
    write('cad/manifest.json', cad)
    write('cad/assignments.json', assignments)
    members = [blob(lib['path']) for lib in libraries] + [m for library in cad['libraries'] for m in library['members']]
    parts = []
    for p in selections['parts']:
        record = prepare_record(output, p['lcsc'], blob(p['catalog']), blob(p['datasheets']), p['datasheet_id'])
        record_path = 'records/' + p['id'] + '.json'
        write(record_path, record)
        parts.append(dict(id=p['id'], role=p['role'], required=True,
            instances=[dict(sheet='/', reference=p['reference'])], selection_id=p['id'],
            lib_id=p['lib_id'], footprint=p['footprint'],
            **{k: record[k] for k in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock')},
            selection=dict(kind='named', lcsc=p['lcsc'], part_record=record_path,
                           part_record_sha256=blob(record_path)['sha256'], decision_ref=decision),
            datasheet=dict(status='captured', required=True, artifact=record['artifact'],
                           provenance=dict(**record['datasheets'], locator='parts[id=' + p['datasheet_id'] + ']'))))
    manifest = dict(schema='agent-ee.parts/1', declaration_source=decision, catalog=None, selection=None,
        cad=dict(manifest=blob('cad/manifest.json'), assignments=blob('cad/assignments.json'),
                 declarations=blob('cad/footprint-declarations.json'),
                 members_sha256=identity(sorted(members, key=lambda m: m['path']))),
        property_fields=dict(lcsc='LCSC', mpn='Partnumber', manufacturer='Manufacturer'),
        population='native-dnp-optional-only/1', parts=parts)
    # Refusal leaves inspectable captured input, but never a qualified manifest.
    result = qualify_manifest(manifest, output)
    write('parts-core.json', manifest)
    write('parts-core-qualification.json', result)
    write('part-decisions.json', compile_decisions((output / 'requirements/spec-record.json').read_bytes(),
          (output / 'requirements/spec.md').read_bytes(),
          json.loads((output / 'part-decisions.declaration.json').read_text())))
    write('part-refs-core.json', dict(schema='agent-ee.part-refs/1',
          components={p['component']: dict(part_ref=p['id']) for p in selections['parts']}))
    write('bundle.json', dict(scope='Eight core parts only; not a complete pod', done=False,
          manifest=blob('parts-core.json'), source_lock_sha256=hashlib.sha256((here / 'source-lock.json').read_bytes()).hexdigest(),
          files=[blob(str(p.relative_to(output))) for p in sorted(output.rglob('*')) if p.is_file()],
          limitations=selections['limitations']))
    return manifest, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--root', action='append', default=[], metavar='NAME=PATH')
    args = parser.parse_args()
    roots = {}
    for value in args.root:
        name, path = value.split('=', 1)
        if name in roots:
            raise ValueError('duplicate input root: ' + name)
        roots[name] = Path(path)
    manifest, _ = build(args.out, roots)
    print(json.dumps(dict(parts=len(manifest['parts']), output=str(args.out), done=False)))


if __name__ == '__main__':
    main()
