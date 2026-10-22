"""AGENTEE-454 profile harness: attribution table and modelled byte reductions.

Input: a ``profile_io.py --mode count`` record and the capture root it ran on.
The repair rows are arithmetic models over the measured per-site bytes and the
manifest's acquisition sharing; no repair is implemented or measured here.
"""
import argparse
import collections
import json
import os
from pathlib import Path

SITES = {
    'closure-final-loop': 'Path.read_bytes @ parts_manifest.dependency_closure > parts_manifest._read_blob > parts_manifest.read_captured',
    'acquisition-digest': 'storage._hash_stream @ parts_manifest.dependency_closure > part_record.qualify_record > part_record._sources > datasheet_facts.load_acquisition > storage.digest',
    'acquisition-envelope-read': 'Path.read_bytes @ parts_manifest.dependency_closure > part_record.qualify_record > part_record._sources > datasheet_facts.load_acquisition',
    'sources-dependency-reread': 'Path.read_bytes @ parts_manifest.dependency_closure > part_record.qualify_record > part_record._sources > parts_manifest.read_captured',
}


def sharing(root, manifest):
    """Parts per acquisition manifest and that manifest's PDF bytes."""
    root = Path(root)
    document = json.loads((root / manifest).read_text())
    users = collections.Counter()
    for part in document['parts']:
        selection = part.get('selection') or {}
        if selection.get('kind') in ('named', 'external'):
            record = json.loads((root / selection['part_record']).read_text())
            users[record['datasheets']['path']] += 1
    rows = []
    for path, count in users.most_common():
        acquisition = json.loads((root / path).read_text())
        pdfs = [Path(path).parent / r['path'] for r in acquisition['parts'] if r.get('path')]
        rows.append(dict(acquisition=path, parts=count, pdfs=len(pdfs),
                         pdf_bytes=sum(os.path.getsize(root / p) for p in pdfs)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('count')
    parser.add_argument('root')
    parser.add_argument('--manifest', default='parts.json')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    record = json.loads(Path(args.count).read_text())
    total = record['rchar_bytes']
    chains = {row['key']: row for row in record['by_chain']}
    sites = {name: chains[key] for name, key in SITES.items()}
    table = [dict(site=name, call_chain=key, reads=sites[name]['calls'], bytes=sites[name]['bytes'],
                  share=round(sites[name]['bytes'] / total, 4)) for name, key in SITES.items()]
    rest = total - sum(r['bytes'] for r in table)
    table.append(dict(site='all-other', reads=None, bytes=rest, share=round(rest / total, 4)))
    shared = sharing(args.root, args.manifest)
    per_part_pdf = sum(r['parts'] * r['pdf_bytes'] for r in shared)
    distinct_pdf = sum(r['pdf_bytes'] for r in shared)
    distinct = record['distinct_path_bytes']
    b = {name: sites[name]['bytes'] for name in SITES}
    # Each model keeps one current read per distinct (path, sha) where today there are several.
    models = {
        'R1 load_acquisition hashes the bytes it envelope-checks (one read, not two)':
            b['acquisition-envelope-read'],
        'R2 _sources takes PDF digests from the verified acquisition read, not a reread':
            b['sources-dependency-reread'],
        'R3 dependency_closure final loop reads each distinct path once per pass':
            b['closure-final-loop'] - distinct,
        'R4 per-pass memo of load_acquisition per (path, sha256, bindings) with R1+R2':
            (b['acquisition-digest'] + b['acquisition-envelope-read'] + b['sources-dependency-reread'])
            - distinct_pdf,
    }
    combined = models['R3 dependency_closure final loop reads each distinct path once per pass'] + \
        models['R4 per-pass memo of load_acquisition per (path, sha256, bindings) with R1+R2']
    out = dict(schema='agent-ee.ee454-attribution/1', source=args.count, pass_bytes=total,
               distinct_input_bytes=distinct, amplification=round(total / distinct, 1),
               parts_times_acquisition_pdf_bytes=per_part_pdf, distinct_acquisition_pdf_bytes=distinct_pdf,
               attribution=table, acquisitions=shared,
               modelled_saving_per_pass={k: dict(bytes=v, share=round(v / total, 4)) for k, v in models.items()},
               modelled_R3_R4_remaining_per_pass=total - combined)
    Path(args.out).write_text(json.dumps(out, indent=2) + '\n')
    for row in table:
        print(f"{row['site']:28} {row['reads'] or '':>7} {row['bytes']:>14} {100 * row['share']:6.2f}%")
    for k, v in out['modelled_saving_per_pass'].items():
        print(f"{v['bytes']:>14} {100 * v['share']:6.2f}%  {k}")
    print('R3+R4 remaining per pass', out['modelled_R3_R4_remaining_per_pass'], 'amplification', out['amplification'])


if __name__ == '__main__':
    main()
