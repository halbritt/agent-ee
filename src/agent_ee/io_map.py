"""Compile an owner CSV to the existing declared hardware-interface contract."""
import argparse
import copy
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from .hardware_interface import (exact_keys, requirement_authority, text, unique_object,
                                validate_contract, validate_requirement_entry)
from .storage import write_json

REQUIRED = ('Connector_Pin', 'Signal_Name', 'Net', 'MCU_Ref', 'MCU_Pin', 'External_Destination')


def read_rows(raw):
    reader = csv.reader(io.StringIO(raw.decode('utf-8'), newline=''), strict=True)
    try:
        fields = next(reader, None)
    except csv.Error as exc:
        raise ValueError(f'row 1: {exc}') from exc
    if not fields or len(fields) != len(set(fields)) or not set(REQUIRED) <= set(fields):
        raise ValueError('row 1: missing or duplicate declared column (including Net)')
    rows, positions = [], set()
    try:
        for cells in reader:
            number = reader.line_num
            if len(cells) != len(fields) or any(not v.strip() for v in cells):
                raise ValueError(f'row {number}: missing column or empty cell')
            row = dict(zip(fields, cells))
            position = row['Connector_Pin']
            if position in positions:
                raise ValueError(f'row {number}: duplicate connector pin {position}')
            positions.add(position)
            nc = row.get('Direction_Class') == 'No_Connect' or row['Signal_Name'] == 'NO_CONNECT'
            if nc and (row['Net'] != 'N/C' or row['MCU_Ref'] != 'N/A' or row['MCU_Pin'] != 'N/A'):
                raise ValueError(f'row {number}: wired no-connect')
            if not nc and row['Net'] == 'N/C':
                raise ValueError(f'row {number}: N/C requires explicit No_Connect')
            rows.append((number, row, nc))
    except csv.Error as exc:
        raise ValueError(f'row {reader.line_num}: {exc}') from exc
    if not rows:
        raise ValueError('row 2: empty io-map')
    return rows


PATH_FIELDS = 'terminals pin_names devices directions'


def read_paths(paths):
    """Owner-declared indirect paths; shape is checked again in the compiled contract."""
    exact_keys(paths, 'schema paths')
    if paths['schema'] != 'agent-ee.io-map-paths/1' or not isinstance(paths['paths'], dict) or not paths['paths']:
        raise ValueError('expected agent-ee.io-map-paths/1 with declared paths')
    for pid, path in paths['paths'].items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', pid) or pid in ('direct', 'TBD'):
            raise ValueError(f'invalid indirect path id {pid!r}')
        exact_keys(path, PATH_FIELDS)
    return paths['paths']


def compile_io_map(raw, *, csv_path, requirement, source, bindings=None, spec_record=None, paths=None,
                   source_bytes=None, require_source_sha256=False, qualifications=None):
    """Bindings borrow components/terminals from a validated interface/1 contract.

    Connector terminal IDs equal Connector_Pin; MCU IDs are MCU_Ref:MCU_Pin.
    Missing bindings and TBD identities remain explicitly unresolved. Concrete
    MCU pins require MCU_Path=direct or ';'-separated IDs of declared indirect
    paths, one per ';'-separated MCU_Pin; anything else is refused.
    """
    source = copy.deepcopy(source)
    if source_bytes is not None:
        sha = hashlib.sha256(source_bytes).hexdigest()
        if 'sha256' in source and source['sha256'] != sha:
            raise ValueError('requirement source sha256 mismatch')
        source['sha256'] = sha
    elif require_source_sha256:
        raise ValueError('requirement source bytes are required to compute sha256')
    rows = read_rows(raw)
    if qualifications is not None:
        if not isinstance(qualifications, dict):
            raise ValueError('qualifications must map requirement IDs to source references')
        for rid, ref in qualifications.items():
            validate_requirement_entry(dict(id=rid, qualification='sourced', source=copy.deepcopy(ref)))
    bindings = validate_contract(copy.deepcopy(bindings)) if bindings is not None else {'components': {}, 'terminals': {}}
    paths = read_paths(copy.deepcopy(paths)) if paths is not None else None
    used_paths = set()
    contract = dict(schema='agent-ee.interface/1', components={}, terminals={}, allowed_edits=[], invariants=[])
    coverage = {}
    sha = hashlib.sha256(raw).hexdigest()
    used_bindings, mcu_nets = set(), {}

    def terminal(tid, reason, *, pending=False):
        if tid not in contract['terminals']:
            declaration = (None if pending else bindings['terminals'].get(tid))
            contract['terminals'][tid] = copy.deepcopy(declaration) if declaration else {'unresolved': reason}
            if declaration:
                used_bindings.add(tid)
                if 'component' in declaration:
                    cid = declaration['component']
                    contract['components'][cid] = copy.deepcopy(bindings['components'][cid])
        return tid

    def invariant(iid, kind, citation, **fields):
        contract['invariants'].append(dict(id=iid, kind=kind, requirement=requirement,
                                          source=copy.deepcopy(source), io_map=citation, **fields))

    def indirect(number, row, citation, path):
        position, ref = row['Connector_Pin'], row['MCU_Ref']
        pids, pins = path.split(';'), row['MCU_Pin'].split(';')
        undeclared = [p for p in pids if p not in paths]
        if undeclared:
            raise ValueError(f'row {number}: undeclared indirect MCU_Path {undeclared} for {row["Signal_Name"]}')
        if 'TBD' in (ref, *pins) or row['Net'] in ('N/A', 'TBD', 'N/C') or position not in contract['terminals']:
            raise ValueError(f'row {number}: indirect MCU_Path requires concrete MCU pins and a connector net')
        if len(pids) != len(pins) or len(set(pids)) != len(pids) or used_paths & set(pids):
            raise ValueError(f'row {number}: indirect MCU_Path must name one unused path per MCU_Pin')
        for pid, pin in zip(pids, pins):
            declared = copy.deepcopy(paths[pid])
            tids = declared['terminals']
            if not isinstance(tids, list) or len(tids) < 4 or tids[0] != position or tids[-1] != f'{ref}:{pin}':
                raise ValueError(f'row {number}: path {pid} must run from {position} to MCU {ref}:{pin}')
            used_paths.add(pid)
            for tid in tids:
                if not isinstance(tid, str):
                    raise ValueError(f'row {number}: path {pid} terminal IDs must be text')
                terminal(tid, f'row {number}: path {pid} has unbound terminal {tid}')
            if not isinstance(declared['devices'], list):
                raise ValueError(f'row {number}: path {pid} devices must be a list')
            for device in declared['devices']:
                cid = device.get('component') if isinstance(device, dict) else None
                if cid not in bindings['components']:
                    raise ValueError(f'row {number}: path {pid} names undeclared intermediate device {cid!r}')
                contract['components'][cid] = copy.deepcopy(bindings['components'][cid])
            mapped = contract['terminals'][tids[-1]]
            if 'component' in mapped:
                locations = contract['components'][mapped['component']]['locations']
                if mapped['pin'] != pin or any(loc['reference'] != ref for loc in locations):
                    raise ValueError(f'row {number}: MCU binding disagrees with {ref}:{pin}')
            invariant(f'{position}:mcu-path:{pid}', 'indirect_path', citation, **declared)

    for number, row, nc in rows:
        position = row['Connector_Pin']
        ids = sorted(set(re.findall(r'\bPOD-IF-[A-Z0-9-]+\b', row.get('Notes', ''))))
        for rid in ids:
            coverage.setdefault(rid, []).append(position)
        if qualifications is not None:
            cited = [dict(id=rid, qualification='sourced', source=copy.deepcopy(qualifications[rid]))
                     if rid in qualifications else dict(id=rid, qualification='unqualified-carryover')
                     for rid in ids]
        else:
            cited = ids
        citation = dict(path=csv_path, sha256=sha, row=number, connector_pin=position,
                        signal=row['Signal_Name'], external_destination=row['External_Destination'], requirements=cited)
        if row['Net'] in ('N/A', 'TBD'):
            invariant(position + ':net', 'unresolved', citation,
                      reason=f'row {number}: no electrical net declared ({row["Net"]})')
        else:
            tid = terminal(position, f'row {number}: unbound connector position {position}')
            invariant(position + ':net', 'no_connect' if nc else 'named_net', citation,
                      **({'terminal': tid} if nc else {'terminal': tid, 'names': [row['Net']]}))
        ref, pin = row['MCU_Ref'], row['MCU_Pin']
        if (ref, pin) == ('N/A', 'N/A') or pin == 'N/A':
            continue
        if 'N/A' in (ref, pin):
            raise ValueError(f'row {number}: inconsistent MCU identity')
        pending = 'TBD' in (ref, pin)
        mid = f'{position}:MCU:TBD' if pending else f'{ref}:{pin}'
        path = row.get('MCU_Path')
        if path not in (None, 'TBD', 'direct'):
            if paths is None:
                raise ValueError(f'row {number}: unsupported indirect MCU_Path {path!r} for {row["Signal_Name"]}')
            indirect(number, row, citation, path)
            continue
        if not pending and path != 'direct':
            raise ValueError(f'row {number}: {row["Signal_Name"]} requires explicit MCU_Path=direct; '
                             'MCU-to-connector connectivity cannot be inferred through intermediate parts')
        if pending or row['Net'] in ('N/A', 'TBD'):
            reason = f'row {number}: unresolved MCU {ref}:{pin}; net or MCU_Path not established'
            terminal(mid, reason, pending=True)
            invariant(position + ':mcu-net', 'unresolved', citation, reason=reason)
            continue
        if mid in mcu_nets and mcu_nets[mid] != row['Net']:
            raise ValueError(f'row {number}: conflicting net for MCU pin {mid}')
        mcu_nets[mid] = row['Net']
        terminal(mid, f'row {number}: unbound MCU {ref}:{pin}')
        mapped = contract['terminals'][mid]
        if 'component' in mapped:
            locations = contract['components'][mapped['component']]['locations']
            if mapped['pin'] != pin or any(loc['reference'] != ref for loc in locations):
                raise ValueError(f'row {number}: MCU binding disagrees with {ref}:{pin}')
        invariant(position + ':mcu-net', 'named_net', citation, terminal=mid, names=[row['Net']])
        if position in contract['terminals']:
            invariant(position + ':same-net', 'same_net', citation, terminals=[position, mid])
    if paths is not None and set(paths) - used_paths:
        raise ValueError(f'unused indirect paths: {sorted(set(paths) - used_paths)}')
    extra = set(bindings['terminals']) - used_bindings
    if extra:
        raise ValueError(f'unused terminal bindings: {sorted(extra)}')
    for cid in contract['components']:
        citation = next(i['io_map'] for i in contract['invariants']
            if any(contract['terminals'][tid].get('component') == cid
                   for tid in i.get('terminals', [i['terminal']] if 'terminal' in i else []))
            or any(d['component'] == cid for d in i.get('devices', [])))
        contract['invariants'].append(dict(id=cid + ':populated', kind='component', component=cid,
            populated=True, requirement=requirement, source=copy.deepcopy(source), io_map=citation))
    if qualifications is not None and set(qualifications) - set(coverage):
        raise ValueError(f'qualification for uncited requirement: {sorted(set(qualifications) - set(coverage))}')
    if spec_record is not None:
        expected = {r['id'] for r in spec_record['requirements'] if r['id'].startswith('POD-IF-')}
        if not expected or expected != set(coverage):
            raise ValueError(f'interface traceability mismatch: missing={sorted(expected - coverage.keys())}; '
                             f'unknown={sorted(coverage.keys() - expected)}')
    return validate_contract(contract, require_source_sha256=require_source_sha256,
                             source_bytes={source['path']: source_bytes} if source_bytes is not None else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--csv-path', required=True, help='captured CSV path relative to scope source')
    parser.add_argument('--requirement', required=True)
    parser.add_argument('--source', required=True, help='captured requirement source path')
    parser.add_argument('--source-file', help='file supplying exact source bytes (defaults to --source in cwd)')
    parser.add_argument('--require-source-sha256', action='store_true',
                        help='require source digests (the CLI always requires readable source bytes)')
    parser.add_argument('--locator', required=True)
    parser.add_argument('--bindings', help='interface/1 contract supplying explicit terminal bindings')
    parser.add_argument('--spec-record', help='require complete POD-IF-* row traceability')
    parser.add_argument('--paths', help='agent-ee.io-map-paths/1 declaring indirect MCU paths')
    parser.add_argument('--qualifications', help='JSON {requirement id: {path, sha256, locator}} marking cited '
                        'requirements sourced; all other cited IDs are emitted unqualified-carryover')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    def load(path):
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object) if path else None
    try:
        try:
            source_bytes = Path(args.source_file if args.source_file is not None else args.source).read_bytes()
        except FileNotFoundError as exc:
            parser.exit(2, f'io-map: missing-source (FileNotFoundError): {exc}\n')
        contract = compile_io_map(Path(args.csv).read_bytes(), csv_path=args.csv_path,
            requirement=text(args.requirement), source=dict(path=args.source, locator=args.locator),
            bindings=load(args.bindings), spec_record=load(args.spec_record), paths=load(args.paths), qualifications=load(args.qualifications),
            source_bytes=source_bytes, require_source_sha256=args.require_source_sha256)
        write_json(args.out, contract)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(2, f'io-map: {exc}\n')
    print(f'compiled {len(contract["invariants"])} invariants; unresolved declarations remain unknown')
    authority = requirement_authority(contract)
    if authority['unqualified-carryover']:
        print('unqualified carry-over requirements (reported, never authority): '
              + ', '.join(authority['unqualified-carryover']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
