"""Compare a captured footprint's drilled land positions with the TE 776228 drawing facts.

Expected positions come from the drawing's dimension chain in drawing-776228.json,
measured from the upper-left peg hole; the footprint is read with the shared
footprint index (no second parser). Positions are exact nanometre integers.

    PYTHONPATH=src python3 fixtures/pod/ampseal/compare_land.py \
        fixtures/fome-mc/ampseal/capture --out fixtures/pod/ampseal/land-comparison.json
"""
import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

from agent_ee.footprint_library import index_footprints, load_document
from agent_ee.storage import json_bytes

HERE = Path(__file__).resolve().parent
DRAWING = HERE / 'drawing-776228.json'
TOLERANCE_NM = 50000  # 0.05 mm, the AGENTEE-169 acceptance tolerance.
NAMED = ('1', '8', '16', '23')


def _nm(value):
    return int(Decimal(value) * 1000000)


def expected(drawing):
    """Drawing contact and peg centres (nm) relative to the upper-left peg; diameters (nm)."""
    d = {k: _nm(v) for k, v in drawing['dimensions_mm'].items()}
    middle = d['upper_peg_to_middle_row']
    contacts = {}
    for row in drawing['rows']:
        x0 = d['upper_peg_to_first_column'] + (d['middle_row_offset'] if row['offset'] else 0)
        for number in range(row['first'], row['last'] + 1):
            contacts[str(number)] = (x0 + (number - row['first']) * d['contact_pitch'],
                                     middle + row['row'] * d['row_pitch'])
    centre = d['upper_peg_spacing'] // 2
    pegs = [(0, 0), (d['upper_peg_spacing'], 0),
            (centre - d['lower_peg_spacing'] // 2, d['peg_row_spacing']),
            (centre + d['lower_peg_spacing'] // 2, d['peg_row_spacing'])]
    return contacts, pegs, d


def _mm(nm):
    return str(Decimal(nm) / 1000000)


def compare(manifest, files, lib_id, drawing):
    """``status`` is ``match`` only when every contact, peg and drill agrees with the drawing."""
    index = index_footprints(manifest, files)
    report = dict(schema='agent-ee.pod-land-comparison/1', footprint=lib_id, tolerance_mm=_mm(TOLERANCE_NM),
                  drawing=dict(number=drawing['drawing']['number'], revision=drawing['drawing']['revision'],
                               sha256=drawing['drawing']['copy']['sha256']),
                  status='unknown', problems=[], contacts=[], pegs=[])
    footprint = next((f for f in index['footprints'] if f['lib_id'] == lib_id), None)
    if index['execution'] != 'ok' or footprint is None:
        report['problems'].append('footprint not indexed: ' + json.dumps(index['findings']))
        return report
    report['footprint_source'] = footprint['source']
    contacts, pegs, d = expected(drawing)
    holes = [p for p in footprint['pads'] if p['type'] == 'np_thru_hole']
    plated = {p['number']: p for p in footprint['pads'] if p['role'] == 'electrical'}
    if len(holes) != len(pegs):
        report['problems'].append(f'{len(holes)} peg holes, drawing has {len(pegs)}')
    if set(plated) != set(contacts) or len(plated) != sum(p['role'] == 'electrical' for p in footprint['pads']):
        report['problems'].append('contact numbers differ from drawing 1 to 23')
    if report['problems']:
        report['status'] = 'mismatch'
        return report
    holes.sort(key=lambda p: (p['y_nm'], p['x_nm']))
    datum = holes[0]
    if plated['1']['y_nm'] > plated['16']['y_nm'] or datum['y_nm'] > plated['1']['y_nm']:
        report['problems'].append('upper peg pair is not on the contact 1 to 8 side')

    def row(name, pad, want, diameter, tolerance):
        got = (pad['x_nm'] - datum['x_nm'], pad['y_nm'] - datum['y_nm'])
        delta = max(abs(got[0] - want[0]), abs(got[1] - want[1]))
        drill = _nm(pad['drill'][0])
        entry = dict(number=name, drawing_mm=[_mm(v) for v in want], footprint_mm=[_mm(v) for v in got],
                     delta_mm=_mm(delta), drill_mm=_mm(drill), drawing_drill_mm=_mm(diameter),
                     within=delta <= TOLERANCE_NM and abs(drill - diameter) <= tolerance)
        if not entry['within']:
            report['problems'].append(f'{name} differs from the drawing')
        return entry

    hole_tolerance = d['contact_hole_tolerance']
    report['contacts'] = [row(n, plated[n], contacts[n], d['contact_hole_diameter'], hole_tolerance)
                          for n in sorted(contacts, key=int)]
    report['pegs'] = [row(f'peg {i + 1}', hole, want, d['peg_hole_diameter'], 0)
                      for i, (hole, want) in enumerate(zip(sorted(holes, key=lambda p: (p['y_nm'], p['x_nm'])),
                                                            sorted(pegs, key=lambda p: (p[1], p[0]))))]
    report['named_contacts'] = {e['number']: e['within'] for e in report['contacts'] if e['number'] in NAMED}
    report['status'] = 'mismatch' if report['problems'] else 'match'
    return report


def compare_capture(capture, lib_id='ampseal:ampseal-23', drawing_path=DRAWING):
    capture = Path(capture)
    manifest = load_document(capture / 'footprint-libraries.json')
    files = {m['path']: (capture / m['path']).read_bytes() for lib in manifest['libraries'] for m in lib['members']}
    report = compare(manifest, files, lib_id, load_document(drawing_path))
    report['drawing_facts_sha256'] = hashlib.sha256(Path(drawing_path).read_bytes()).hexdigest()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', help='footprint capture directory (agent_ee.footprint_capture output)')
    parser.add_argument('--out', help='write the report here instead of standard output')
    args = parser.parse_args(argv)
    data = json_bytes(compare_capture(args.capture))
    if args.out:
        Path(args.out).write_bytes(data)
    else:
        sys.stdout.write(data.decode())
    return 0 if json.loads(data)['status'] == 'match' else 1


if __name__ == '__main__':
    sys.exit(main())
