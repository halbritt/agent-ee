"""Shared source qualification for packing and saved-board placement constraints.

Extends the packing policy's sourced-hard record (AGENTEE-338/358). Declared
citations locate evidence; authenticating their authority is AGENTEE-348.
"""


GEOMETRY = object()


def sourced(entry, label, value_nm=GEOMETRY):
    """Validate a declared source, including exact numeric binding when applicable."""
    keys = {'class', 'enforcement', 'citation'} | ({'value_nm'} if value_nm is not GEOMETRY else set())
    classes = ('fixture_author', 'manufacturer') if value_nm is not GEOMETRY else (
        'fixture_author', 'manufacturer', 'datasheet', 'board')
    if (not isinstance(entry, dict) or set(entry) != keys or entry.get('class') not in classes
            or entry.get('enforcement') != 'hard'
            or not isinstance(entry.get('citation'), str) or not entry['citation'].strip()
            or (value_nm is not GEOMETRY and (type(entry.get('value_nm')) is not int
                or entry['value_nm'] < 0 or entry['value_nm'] != value_nm))):
        raise ValueError(f'{label} needs a sourced hard value and citation matching the constraint')
    return dict(entry)


def classify(container, key, label, value_nm=GEOMETRY):
    """Absent sources are advisory, never implicit owner or manufacturer statements."""
    if key in container:
        return sourced(container[key], label, value_nm)
    return {'class': 'unknown', 'enforcement': 'soft', 'soft_reason': f'unsourced {label} is advisory'}
