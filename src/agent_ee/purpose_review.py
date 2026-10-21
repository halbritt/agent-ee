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
    "defect to an advisory merely because a purpose alternative exists. "
)


def dimension(manifest, citation_files, checks):
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
    return {'authority': 'advisory',
            'empty_field_threshold': {'area_mm2': 100, 'enforcement': 'advisory',
                'provenance': 'agent-chosen review focus; not an acceptance limit or minimum useful area'},
            'candidate_sources': inputs, 'measurements': measurements, 'images': images,
            'questions': ['constraint purpose and least-cost position/side/orientation',
                          'empty fields and justified reserves on F and B',
                          'external-interface and access-direction use',
                          'single best area or routability change with estimated saving'],
            'limitations': ([] if images else ['No retained images; visual purpose coverage is missing.'])}
