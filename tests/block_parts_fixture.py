"""Structurally valid synthetic parts declarations; no qualification is implied."""


def parts_manifest():
    def blob(path, locator=False):
        result = dict(path=path, sha256='a' * 64)
        if locator:
            result['locator'] = 'Owned synthetic declaration'
        return result
    return dict(schema='agent-ee.parts/1',
        declaration_source=blob('declaration.txt', True),
        catalog=dict(snapshot='snapshot', manifest_sha256='a' * 64,
                     database_sha256='b' * 64, snapshot_date='2026-10-17'),
        selection=dict(requirements=blob('selection-requirements.json'), result=blob('selection.json')),
        cad=dict(manifest=blob('cad.json'), assignments=blob('assignments.json'), members_sha256='c' * 64),
        property_fields=dict(lcsc='LCSC', mpn='MPN', manufacturer='Manufacturer'),
        population='native-dnp-optional-only/1',
        parts=[dict(id='resistor', role='fixture', required=True,
            instances=[dict(sheet='/', reference='R1')], selection_id='R1',
            lib_id='Owned:R', footprint='Owned:R2', lcsc='C1', mpn='R-C1',
            manufacturer='Owned fixture', stock=None,
            datasheet=dict(status='unavailable', required=False, reason='Structural fixture only'))])
