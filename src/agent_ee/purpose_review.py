"""Advisory purpose questions and navigation for independent candidate reviewers.

This dimension supplies no check verdict or geometric proof. Reviewers use the
existing captured policies, native inventories, measurements and renders.
"""

BRIEF = (
    "Perform the purpose_review dimension independently as well as correctness review. "
    "Read the complete captured policy and arrangement, native geometry measurements and renders; "
    "DRC and obligation tables alone cannot establish useful placement. For EVERY declared obstacle, "
    "keep-out, lane, fixed pose and advisory rule, explain its purpose and whether another edge/inboard "
    "position, side or orientation would cost less. For EVERY empty field above the packet's advisory "
    "area threshold on F and B, identify what reserves it and whether that reserve is justified. "
    "Do not count overlapping reserves twice or infer usable area from a bounding box alone. "
    "For EVERY part with an access direction or external interface (connectors, sockets, antennas, "
    "test points, LEDs and mounting holes), examine cable exit, card insertion, probe access, "
    "visibility and enclosure fit as applicable. Identify the single change with the best area or "
    "routability return, or explain with evidence why no improvement is supported. Compare the "
    "current pose with the edge/side/orientation alternative using the measurements and renders. "
    "Record purpose coverage (including justified constraints and reserves) in coverage, and each "
    "supported waste observation beside correctness findings in findings as assessment=concern, "
    "severity=info or minor. In its reasoning state the proposed change, estimated saving in mm2 "
    "or an explicit routability measure, calculation/basis and uncertainty; if a saving cannot be "
    "quantified, say unknown and name the missing measurement. Cite captured text measurements "
    "with structured locators; images supplement them and are not text citations. Missing renders, "
    "measurements, purpose declarations or inaccessible images go in limitations, never a clean "
    "purpose conclusion. This is advisory: an agent-chosen threshold or estimated saving cannot "
    "prune placement or block acceptance. Never downgrade an independently established correctness "
    "defect to an advisory merely because a purpose alternative exists. Mark each purpose waste "
    "finding dimension=purpose_review (only for concern with severity info or minor; correctness "
    "findings never carry it), and begin each purpose coverage and limitation entry with "
    "'purpose_review:'. A review with neither is recorded as purpose coverage unknown. "
)

DIMENSION = 'purpose_review'
COVERAGE_PREFIX = 'purpose_review:'
# AGENTEE-382 render records (agent-ee.candidate-visuals/1): 3D views top/bottom, layer plots
# front/back and one plot per inner copper layer. Inner layers never cover F or B.
SIDE_VIEWS = {'F': ('top', 'front'), 'B': ('bottom', 'back')}


def render_sides(visuals, admitted):
    """Per-side board render predicate (seam for AGENTEE-382 admitted render records).

    A side is covered only by an image record of an available coordinator visuals
    report whose view names that side and whose path is in the packet citation
    inventory. Any other image, such as a schematic SVG, covers no side.
    """
    sides = {side: [] for side in SIDE_VIEWS}
    if not isinstance(visuals, dict) or visuals.get('status') != 'available':
        return sides
    for image in visuals.get('images', []):
        for side, views in SIDE_VIEWS.items():
            if image.get('view') in views and image.get('path') in admitted:
                sides[side].append(image['path'])
    return {side: sorted(paths) for side, paths in sides.items()}


def eligible(item):
    """A reviewer's info/minor concern that never was a defect; anything else keeps correctness rules."""
    return (item.get('dimension') == DIMENSION and item.get('source', 'review') == 'review'
            and item.get('assessment') == 'concern' and item.get('severity') in ('info', 'minor')
            and 'downgraded_from' not in item)


def check_finding(fid, item):
    """Whether item is a purpose finding; a purpose tag on any other finding is refused."""
    if 'dimension' not in item:
        return False
    if item['dimension'] != DIMENSION:
        raise ValueError(f"finding {fid}: invalid dimension {item['dimension']!r}")
    if 'downgraded_from' in item:
        # A validator-downgraded defect stays correctness uncertainty (AGENTEE-385 H2).
        raise ValueError(f"finding {fid}: a downgraded defect cannot be a purpose_review finding")
    if not eligible(item):
        raise ValueError(f"finding {fid}: purpose_review findings must be concern with severity info or minor, raised by a reviewer")
    return True


def advisory_deferral(finding):
    """The only finding the run outcome may set aside: an adjudicated, deferred purpose finding."""
    return (eligible(finding) and finding.get('purpose_status') == 'advisory'
            and finding.get('disposition', finding.get('status')) == 'deferred')


def coverage(reviews):
    """Whether each initial review explicitly addressed the purpose dimension; never inferred."""
    records = []
    for review in reviews:
        entries = [text for key in ('coverage', 'limitations') for text in review.get(key, [])
                   if text.strip().lower().startswith(COVERAGE_PREFIX)]
        findings = [f['id'] for f in review.get('findings', []) if f.get('dimension') == DIMENSION]
        records.append({'role': review['role'],
                        'status': 'addressed' if entries or findings else 'unknown',
                        'entries': len(entries), 'findings': findings})
    return records


def record_coverage(result):
    """Record per-role purpose coverage and an explicit limitation for each unassessed role."""
    result['purpose_coverage'] = coverage(result['reviews'])
    result['limitations'] += [text for text in coverage_limitations(result['purpose_coverage'])
                              if text not in result['limitations']]
    return result


def verify_records(run, result):
    """Re-derive purpose coverage and advisory status from retained reviews and adjudication records.

    The result projection is not trusted: coverage records, their unknown limitations, and each
    finding's dimension, purpose_status and decision events must equal what the retained initial
    reviews and adjudication event files grant.
    """
    from .storage import read_json
    reviews = [read_json(p) for p in sorted((run / 'reviews').glob('*.json'))]
    expected = coverage(reviews)
    stored = result.get('purpose_coverage')
    if stored is not None or expected:
        if not isinstance(stored, list) or (sorted(stored, key=lambda r: str(r.get('role')))
                                            != sorted(expected, key=lambda r: r['role'])):
            raise ValueError('purpose coverage differs from the retained initial reviews')
    if any(text not in result.get('limitations', []) for text in coverage_limitations(expected)):
        raise ValueError('result omits a purpose coverage unknown limitation')
    retained = {f"{r['role']}/{f['id']}": f for r in reviews for f in r.get('findings', [])}
    granted = {}
    records = [(read_json(p), p.name) for p in (run / 'events').glob('*adjudication*.json')]
    for record, name in sorted(records, key=lambda pair: (str(pair[0].get('at', '')), pair[1])):
        try:
            for decision in record['decisions']['decisions']:
                granted.setdefault(decision['finding_id'], []).append(
                    (decision['disposition'], decision['reason'], decision.get('duplicate_of'), 'advisory'))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f'retained adjudication record {name} is malformed: {exc!r}') from exc
    for finding in result.get('findings', []):
        fid = finding.get('id')
        item = retained.get(fid) if finding.get('source') == 'review' else None
        events = finding.get('events', [])
        if item is None or not check_finding(fid, item):
            if ('dimension' in finding or 'purpose_status' in finding
                    or any('purpose_status' in event for event in events)):
                raise ValueError(f'finding {fid}: purpose status not granted by its retained initial review')
            continue
        decisions = granted.get(fid, [])
        if (any(finding.get(key) != item.get(key)
                for key in ('dimension', 'assessment', 'severity', 'downgraded_from'))
                or [(e.get('disposition'), e.get('reason'), e.get('duplicate_of'), e.get('purpose_status'))
                    for e in events] != decisions
                or finding.get('purpose_status') != ('advisory' if decisions else None)
                or finding.get('disposition', 'unresolved') != (decisions[-1][0] if decisions else 'unresolved')
                or finding.get('status', 'unresolved') != finding.get('disposition', 'unresolved')
                or (events and finding.get('decision') != events[-1])):
            raise ValueError(f'finding {fid}: purpose finding projection differs from its retained '
                             'initial review and adjudication records')
    return result


def coverage_limitations(records):
    return [f"{r['role']}: purpose coverage unknown; the review recorded no {COVERAGE_PREFIX} coverage, "
            "limitation or purpose finding, so the purpose dimension is unassessed, not passed."
            for r in records if r['status'] == 'unknown']


def dimension(manifest, citation_files, checks, visuals=None):
    """Index only sources already admitted to the sealed packet; never peer work."""
    available = {item['path'] for item in citation_files}
    inputs = [{'path': 'inputs/' + rel, 'sha256': value}
              for rel, value in sorted(manifest['files'].items())
              if 'inputs/' + rel in available]
    images = [item['path'] for item in citation_files
              if item['path'].lower().endswith(('.png', '.svg', '.jpg', '.jpeg', '.webp'))]
    measurements = [{'id': check['id'], 'kind': check['kind'],
                     'execution': check.get('execution'), 'outcome': check.get('outcome'),
                     'evidence': [rel for entry in check.get('evidence', [])
                                  if (rel := entry['path'] if isinstance(entry, dict) else entry) in available]}
                    for check in checks]
    renders = render_sides(visuals, available)
    return {'authority': 'advisory',
            'empty_field_threshold': {'area_mm2': 100, 'enforcement': 'advisory',
                'provenance': 'agent-chosen review focus; not an acceptance limit or minimum useful area'},
            'candidate_sources': inputs, 'measurements': measurements, 'images': images,
            'questions': ['constraint purpose and least-cost position/side/orientation',
                          'empty fields and justified reserves on F and B',
                          'external-interface and access-direction use',
                          'single best area or routability change with estimated saving'],
            'board_renders': renders,
            'limitations': [f'No admitted {side} board render ({"/".join(SIDE_VIEWS[side])}); '
                            f'visual purpose coverage is missing for {side}.'
                            for side, paths in renders.items() if not paths]}
