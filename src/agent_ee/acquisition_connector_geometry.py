"""Finite PH body correction before preparation seals either geometry consumer."""
from pathlib import Path

from .catalog import strict_json
from .spec_record import sha256
from .storage import digest, json_bytes

AT = 'preparation/connector-geometry'
ORIGINAL = AT + '/original-model.json'
SOURCES = {
    'manufacturer-text/jst-ph.txt': '9553878588234be82b72e0a89774ac67bbc0022c4aef14cfdbac1383b32d8df1',
    'manufacturer-text/manifest.json': 'ee1c4d695cdd0ba20527a50bd73516dc2e955559a9bd59829edd5d35748fe43f',
    'datasheets-connectors-bulk/manifest.json': '0851bd57b2b559e831d97b583c8218ac3f02ed190c5a362d63f76ad3afe9a685',
    'datasheets-connectors-bulk/bb0b7c344f87ea4a2eb01dbf02c4541bd9482608f57c1dc3ca2f5e333a0dd2a4.pdf':
        '447624f4f2f7d37c58c1eaa7ee314ad757fe7aff48f6186491ef6f69fbc00b96',
    'Connector_JST.pretty/JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical.kicad_mod':
        'dd59286748b9a6fa9174237072608f811cf3169dddf00eab54a5c196fcb6f7e8',
    'Connector_JST.pretty/JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical.kicad_mod':
        'f5488d79bbafc8c929bcfacf8e960e81dcfc126c598e03083de317607937b58e',
}
BASIS = ('JST PH top-entry reference body: depth4.5mm, pin-row offset1.7mm, '
    'localY-1.7..2.8mm; manufacturer-text/jst-ph.txt physicalp1 and captured '
    'Connector_JST F.Fab body rectangles; molded-ear widths retained; '
    'reference dimensions, no guaranteed mated height')
# Corrected hash -> exact preceding model. Historical captures remain unchanged.
MODELS = {
    '4155f201ba2efb2c74a44a3f8ffe30187a28eac4884b7b34a729e76d15c40e52':
        '0badae526e172bd688bc634e1fb2dec040fbca80dab15641bb0ff7e83d7f23f4',
    '4aaad94d06dfc4d838e0992259219c51ce1f3d364948cc2a3929e741a150d21f':
        '311df5d1398780f8b65e061a512bea2384d15298ba6278d505a6e77e3159cb6c',
    '2c2b46cbfa91cf19e9e56b4c3f1ff4b1161d9ca1822f5f73432bc96ad53e3646':
        '43be6345262e5eeb0f54b9214009d13168fa5889333b006315f489dac7c8fa8f',
    '38669f433c5005587b3f310af6bb51cc91fa89ba301d348799421bf83fa5cb64':
        '2bb1d93ff3f1cc0420088c02b16278394e3124230b05da950d6838432f049479',
    '53187855ce84e712e539eed040dc741c2ffe0c8d7d331fc9b37203f033be4d0b':
        '02292f70aca8b8a07b2dbbbab42515824b1c2c6d40ba49103a2ef3d7b23b5ffa',
    'c6ad86348e39e9a2be7d0f6a23b00b0a08b436847e3f691f320511a2d02f82e1':
        'fa010715d48ba3fe6e1843e17744834583b98f03a23fb61e004909a7840507b2',
}


def corrected_model(raw):
    before = sha256(raw)
    if before not in MODELS.values():
        raise ValueError('PH geometry correction requires an exact admitted preceding model')
    model = strict_json(raw)
    for ref in ('J1', 'J2', 'J3'):
        model['packages'][ref]['offset_mm'][1] = .55
        model['packages'][ref]['basis'] = BASIS
    corrected = json_bytes(model)
    if MODELS.get(sha256(corrected)) != before:
        raise ValueError('unsupported finite PH geometry derivative')
    return corrected


def geometry_sources(model):
    if model not in MODELS:
        raise ValueError('unsupported PH body-model revision')
    return {**SOURCES, ORIGINAL: MODELS[model]}


def apply(root, source_files):
    """Revise one owned source; the later ordinary seal rechecks added captures."""
    root = Path(root)
    if any(source_files.get(name) != pin for name, pin in SOURCES.items()):
        raise ValueError('PH geometry correction source custody is missing or changed')
    path = root / 'analysis/geometry-model.json'
    original = path.read_bytes()
    corrected = corrected_model(original)
    retained = root / ORIGINAL
    retained.parent.mkdir(parents=True, exist_ok=True)
    with retained.open('xb') as stream:
        stream.write(original)
    path.write_bytes(corrected)
    calculations_path = root / 'engineering-calculations.json'
    calculations = strict_json(calculations_path.read_bytes())
    calculations['sources'].update(SOURCES)
    calculations['sources']['analysis/geometry-model.json'] = digest(path)
    calculations_path.write_bytes(json_bytes(calculations))
    return dict(schema='agent-ee.connector-geometry-correction/1',
        before=dict(path=ORIGINAL, sha256=sha256(original)),
        after=dict(path='analysis/geometry-model.json', sha256=sha256(corrected)),
        sources=dict(SOURCES), references=['J1', 'J2', 'J3'],
        change='Local Y center +0.25 to +0.55mm; source attribution corrected',
        preserved='Widths, depth, height, all other packages, checker and limits',
        qualification='unverified; fresh native geometry and judgments required')
