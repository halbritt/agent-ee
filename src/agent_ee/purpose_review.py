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


def check_finding(fid, item):
    """A purpose finding is an info/minor concern; anything else keeps correctness rules."""
    if 'dimension' not in item:
        return False
    if item['dimension'] != DIMENSION:
        raise ValueError(f"finding {fid}: invalid dimension {item['dimension']!r}")
    if item.get('assessment') != 'concern' or item.get('severity') not in ('info', 'minor'):
        raise ValueError(f"finding {fid}: purpose_review findings must be concern with severity info or minor")
    return True


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
