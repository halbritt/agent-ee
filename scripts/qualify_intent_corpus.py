#!/usr/bin/env python3
"""Privately fetch or verify the pinned intent corpus; never writes repository data.

Fetch is an explicit outer-host action. Verification is offline and requires the
source and license hashes below; this is importer qualification, not circuit evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

from agent_ee.design_intent import component_counts, import_record

REVISION = 'baf1235ae6270419b8444ffe618632f17621a3f9'
FILES = {
    'README.md': ('README.md', '29a50f9604016d931b36d5d14ae3e8368272ca2840128b1c75bf7fdc83462e01'),
    'LICENSE': ('LICENSE', '5e7d274da39749f875e90d2e6075355034bd32f71296baeeb34a3ac441df702d'),
    'pcbbench.jsonl': ('pcbbench/pcbbench.jsonl', 'e90d4bbf3b6185dc8488a232e38e73b0b088db7700741f9c72d507d0d0770399'),
    'ose_165_task_specs.json': ('open_schematics/ose_165_task_specs.json', '600365d642c3484d4a2f9f006ec104944f2f2df781852a91e56332518e9d0279'),
}
OUT_IDS = {'201', '205', '208', '209', '210', '213', '215', '222', '224', '227',
           '228', '230', '236', '239', '241', '243', '253', '255', '258', '259'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer_numbers_only(raw):
    def refuse(token):
        raise ValueError(f'corpus number {token} would not reserialize exactly')
    # Records are reserialized for import; a binary float could change a source number.
    return json.loads(raw, parse_float=refuse)


def qualify(directory):
    source = {}
    for name, (_, expected) in FILES.items():
        raw = (directory / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, f'changed source/license: {name}')
        source[name] = raw
    groups = [[integer_numbers_only(line) for line in source['pcbbench.jsonl'].splitlines()],
              integer_numbers_only(source['ose_165_task_specs.json'])]
    require([len(g) for g in groups] == [62, 165], 'unexpected corpus counts')
    ids, unknown, placeholders, waivers = set(), [], set(), []
    for group in groups:
        for record in group:
            rid = str(record['id'])
            require(rid not in ids, 'duplicate corpus ID')
            ids.add(rid)
            raw = json.dumps(record, ensure_ascii=False).encode()
            waiver = ('OUT is the declared isolated gate-driver output; the task specifies '
                      'a gate resistor and UCC5390E, not a generic placeholder.') if rid == '15' else None
            artifacts = import_record(raw, out_justification=waiver)
            require(artifacts == import_record(raw, out_justification=waiver), 'unstable artifact bytes')
            report = json.loads(artifacts['intent.json'])
            if report['outcome'] == 'unknown':
                unknown.append(rid)
            if any(f['code'] == 'placeholder_interface_net' and f.get('net') == 'OUT'
                   for f in report['findings']):
                placeholders.add(rid)
            counts = component_counts(record['components'])
            # All supported encodings must preserve each record's exact multiset.
            names = [name for name, count in counts.items() for _ in range(count)]
            encoded = ', '.join(f'{count}x {name}' for name, count in counts.items())
            require(component_counts(names) == counts == component_counts(encoded), 'encoding disagreement')
            if rid == '37':
                stub = json.loads(artifacts['hardware-interface.stub.json'])
                require(stub['required_nets'] == ['CANH', 'CANL', 'RXD', 'TXD'], 'id37 interface')
                require(counts == {'SN65HVD230': 1} and report['findings'] == [], 'id37 components/lint')
            if rid == '182':
                require(counts.get('TDA1517P/N3,112') == 1 and '112' not in counts, 'comma-in-name split')
            if rid == '196':
                require(any(f['code'] == 'component_count_mismatch' and f['part'] == 'Q_NPN_CBE'
                            for f in report['findings']), 'id196 mismatch missed')
            if rid == '15':
                require(report['waivers'] and not report['findings'], 'id15 waiver missing or invalid')
                waivers.append({'record_id': rid, 'source_sha256': report['source_sha256'], 'waivers': report['waivers']})
    require(OUT_IDS <= placeholders, 'OUT negative corpus was not completely flagged')
    return {'schema': 'agent-ee.intent-corpus-qualification/1', 'revision': REVISION,
            'records_imported': len(ids), 'recorded_waivers': waivers, 'out_placeholder_ids': sorted(OUT_IDS), 'additional_out_warnings': sorted(placeholders - OUT_IDS),
            'unknown_ids': sorted(unknown), 'source_hashes': {n: spec[1] for n, spec in FILES.items()},
            'license': 'MIT source declaration and pinned LICENSE verified; no publication authorized',
            'outcome': 'pass', 'meaning': 'import/lint qualification only; not design acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--fetch', action='store_true', help='explicit outer-host private download into NEW directory')
    args = parser.parse_args()
    try:
        if args.fetch:
            if args.directory.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
                raise ValueError('private corpus destination must be outside the source checkout')
            args.directory.mkdir(exist_ok=False)
            for name, (remote, expected) in FILES.items():
                url = f'https://huggingface.co/datasets/Hzou9/PCBSchemaGen-Benchmarks/resolve/{REVISION}/{remote}'
                with urllib.request.urlopen(url, timeout=60) as response:
                    raw = response.read(2_000_001)
                require(len(raw) <= 2_000_000 and hashlib.sha256(raw).hexdigest() == expected,
                        f'download hash/size mismatch: {name}')
                (args.directory / name).write_bytes(raw)
        print(json.dumps(qualify(args.directory), sort_keys=True, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'corpus qualification unavailable: {exc}\n')


if __name__ == '__main__':
    main()
