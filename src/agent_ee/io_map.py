"""Compile an owner CSV to the existing declared hardware-interface contract."""
import argparse
import copy
import csv
import hashlib
import io
import json
import re
from pathlib import Path

from .hardware_interface import text, unique_object, validate_contract
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


def compile_io_map(raw, *, csv_path, requirement, source, bindings=None, spec_record=None):
    """Bindings borrow components/terminals from a validated interface/1 contract.

    Connector terminal IDs equal Connector_Pin; MCU IDs are MCU_Ref:MCU_Pin.
    Missing bindings and TBD identities remain explicitly unresolved. Concrete
    MCU pins require MCU_Path=direct; named indirect paths are refused.
    """
    rows = read_rows(raw)
    bindings = validate_contract(copy.deepcopy(bindings)) if bindings is not None else {'components': {}, 'terminals': {}}
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

    for number, row, nc in rows:
        position = row['Connector_Pin']
        ids = sorted(set(re.findall(r'\bPOD-IF-[A-Z0-9-]+\b', row.get('Notes', ''))))
        for rid in ids:
            coverage.setdefault(rid, []).append(position)
        citation = dict(path=csv_path, sha256=sha, row=number, connector_pin=position,
                        signal=row['Signal_Name'], external_destination=row['External_Destination'], requirements=ids)
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
            raise ValueError(f'row {number}: unsupported indirect MCU_Path {path!r} for {row["Signal_Name"]}')
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
    extra = set(bindings['terminals']) - used_bindings
    if extra:
        raise ValueError(f'unused terminal bindings: {sorted(extra)}')
    for cid in contract['components']:
        citation = next(i['io_map'] for i in contract['invariants']
            if any(contract['terminals'][tid].get('component') == cid
                   for tid in i.get('terminals', [i['terminal']] if 'terminal' in i else [])))
        contract['invariants'].append(dict(id=cid + ':populated', kind='component', component=cid,
            populated=True, requirement=requirement, source=copy.deepcopy(source), io_map=citation))
    if spec_record is not None:
        expected = {r['id'] for r in spec_record['requirements'] if r['id'].startswith('POD-IF-')}
        if not expected or expected != set(coverage):
            raise ValueError(f'interface traceability mismatch: missing={sorted(expected - coverage.keys())}; '
                             f'unknown={sorted(coverage.keys() - expected)}')
    return validate_contract(contract)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--csv-path', required=True, help='captured CSV path relative to scope source')
    parser.add_argument('--requirement', required=True)
    parser.add_argument('--source', required=True, help='captured requirement source path')
    parser.add_argument('--locator', required=True)
    parser.add_argument('--bindings', help='interface/1 contract supplying explicit terminal bindings')
    parser.add_argument('--spec-record', help='require complete POD-IF-* row traceability')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    def load(path):
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object) if path else None
    try:
        contract = compile_io_map(Path(args.csv).read_bytes(), csv_path=args.csv_path,
            requirement=text(args.requirement), source=dict(path=args.source, locator=args.locator),
            bindings=load(args.bindings), spec_record=load(args.spec_record))
        write_json(args.out, contract)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(2, f'io-map: {exc}\n')
    print(f'compiled {len(contract["invariants"])} invariants; unresolved declarations remain unknown')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
