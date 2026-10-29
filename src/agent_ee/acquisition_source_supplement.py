"""Finite offline exact-part source correction for the captured acquisition recipe.

Raw manufacturer data lives in the coordinator's private capture lane. This
module admits its complete pinned inventory; it neither fetches nor qualifies it.
"""
import json
from pathlib import Path

from . import requirement_map
from .spec_record import sha256
from .storage import json_bytes

AT = 'source-supplement'
PINS = Path(__file__).resolve().parents[2] / 'fixtures/acquisition-typed/source-supplement.json'
ORIGINAL_MODEL = '0badae526e172bd688bc634e1fb2dec040fbca80dab15641bb0ff7e83d7f23f4'
MURATA_PDF = '3f5a339eb51502df7716dfd2d4c4e6d33e837abc0df5cb8e4fc1e6e275d09790'
MURATA_BASIS = ('Murata GRM1885C1H103JA01D exact reference sheet GRM1885C1H103JA01-01A, '
    'physical p2 L/W/T 1.6/0.8/0.8 +/-0.1mm; source-supplement/murata/reference.pdf '
    'sha256 ' + MURATA_PDF + '; reference dimensions, approval sheet pending before ordering')


def pins():
    return json.loads(PINS.read_bytes())


def geometry_sources():
    return {AT + '/' + n: h for n, h in pins()['geometry_sources'].items()}


def read(root):
    """Admit only the captured finite version, including extraction and HTTP custody."""
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('source supplement must not pass through links')
    expected = pins()['files']
    if requirement_map.input_inventory(root) != expected:
        raise ValueError('source supplement missing, changed or unexpected captured file')
    raw = {n: (root / n).read_bytes() for n in expected}
    # Exact span custody stays explicit even though the whole closure is pinned.
    extraction = json.loads(raw['samsung/extraction.json'])
    if raw['samsung/02.body'][extraction['byte_start']:extraction['byte_end']] != raw['samsung/datasheetData.raw.json']:
        raise ValueError('Samsung exact JSON span differs from original HTML')
    manifest = json.loads(raw['manifest.json'])
    if manifest['status'] != 'complete' or manifest['failures']:
        raise ValueError('source supplement PDF extraction incomplete')
    return raw


def corrected_model(raw):
    if sha256(raw) != ORIGINAL_MODEL:
        raise ValueError('source correction needs the exact historical geometry model')
    model = json.loads(raw)
    for ref in ('C4', 'C5'):
        if model['packages'][ref]['size_mm'] != [1.7, .9, .9]:
            raise ValueError('Murata source correction cannot change numerical geometry')
        model['packages'][ref]['basis'] = MURATA_BASIS
    return json_bytes(model)


# This is one verified attribution-only version, never an arbitrary hash supplied
# by a caller. The original checker and all numerical geometry stay unchanged.
CORRECTED_MODEL = '311df5d1398780f8b65e061a512bea2384d15298ba6278d505a6e77e3159cb6c'


def apply(revised, overlay, captures, source_files):
    """Revise an owned source tree before archive, mechanics binding and seal."""
    revised = Path(revised)
    if {n: sha256(b) for n, b in captures.items()} != pins()['files']:
        raise ValueError('source supplement capture changed before revision')
    model = revised / 'analysis/geometry-model.json'
    corrected = corrected_model(model.read_bytes())
    if sha256(corrected) != CORRECTED_MODEL:
        raise ValueError('unsupported corrected geometry version')
    model.write_bytes(corrected)
    path = revised / 'engineering-calculations.json'
    calc = json.loads(path.read_bytes())
    # Replace obsolete absolute source locations only by identical captured bytes.
    source_files = {**source_files, **{AT + '/' + n: sha256(raw) for n, raw in captures.items()}}
    by_sha = {}
    for name, pin in sorted(source_files.items()):
        by_sha.setdefault(pin, name)
    if any(pin not in by_sha for pin in calc['sources'].values()):
        raise ValueError('engineering calculation source is not captured in the revised input closure')
    calc['sources'] = {by_sha[pin]: pin for pin in calc['sources'].values()}
    calc['sources'].update({AT + '/' + n: sha256(raw) for n, raw in captures.items()})
    calc['sources']['manufacturer-text/ads1115.txt'] = source_files['manufacturer-text/ads1115.txt']
    values = calc['values']
    values['adc_gain_error_2V_datasheet_test_limit']['basis'] = (
        'TI SBAS444E section5.5 physical pp5-6: 0.15percent max at25C, VDD3.3V, DR8SPS, '
        'FSR+/-2.048V; 3mV at2V only under those test conditions; NOT a128SPS bound')
    values['adc_single_ended_offset_typical']['basis'] = (
        'TI section5.5 physical pp5-6: single-ended3LSB TYPICAL at8SPS/25C, '
        'no single-ended maximum; neither typical nor maximum specified here at128SPS')
    values['adc_128sps_typical_noise']['basis'] = (
        'TI table6-1 physical p11:128SPS,3.3V,+/-2.048V,25C shorted-input62.5uVrms '
        'representative typical, not maximum/board bound; includes conversion/quantization behavior, '
        'do not add independent quantization RMS again')
    values['adc_ideal_quantization_half_step'] = dict(value='0.00003125', unit='V',
        basis='Ideal round-to-nearest half of62.5uV nominal code step; not offset/INL bound or independent noise RMS')
    calc['adc_applicability'] = dict(
        operating='Unchanged single-shot128SPS,+/-2.048V,single-ended0..2V, nominal25C',
        source='manufacturer-text/ads1115.txt physical pp5-6,11-15,18,29-30; supplemental adc/EVIDENCE.md and CLAUSE-INTERPRETATION.md',
        unavailable='No applicable manufacturer maximum for128SPS gain, single-ended offset or transferred8SPS INL; not zero',
        loading='Typical switched-capacitor impedance model, not guaranteed minimum; supply/common-mode/temperature and source effects remain',
        accounting='Gain already includes PGA/reference. Typical shorted-input noise includes quantization. No double-counted reference or quantization RMS.',
        dynamic='Conversion interval is not host cadence; alternating channels, digital-filter response/settling, aliasing and external-source error remain separate.',
        conclusion='Evaluation with explicit unavailable terms; no guaranteed128SPS total accuracy, confidence interval or calibration result')
    path.write_bytes(json_bytes(calc))
    note = ('\n\nSource applicability correction (preparation, no acceptance):128SPS remains the operating rate. '
        'TI section5.5 gain0.15% maximum and single-ended3LSB typical are conditioned on8SPS/25C; '
        'single-ended offset has no maximum. No applicable128SPS gain/offset/INL maximum is established. '
        'The62.5uVrms128SPS shorted-input value is typical and includes conversion/quantization behavior; '
        'do not add an independent quantization RMS term. Ideal half-code31.25uV is only a transfer-model '
        'quantity. Gain includes PGA/reference effects. Typical loading, host cadence, digital filter/dynamic '
        'response, external-source error and physical characterization remain distinct. No guaranteed128SPS '
        'total accuracy is claimed. See engineering-calculations.json and source-supplement/adc/.\n'
        'Exact Samsung curves in source-supplement/samsung/ are typical design-reference data, not '
        'guaranteed effective capacitance or biased ESR limits. New startup/removal power-model evidence '
        'is still required; no corner selection or pass follows from these captures.\n')
    for name in ('OPERATING.md', 'ENGINEERING-REVIEW.md'):
        target = revised / name
        text = target.read_text()
        if name == 'ENGINEERING-REVIEW.md':
            text = text.replace('TI lists3LSB typical single-ended offset without a\nmaximum in that row;',
                'TI section5.5 lists3LSB typical single-ended offset at8SPS/25C, with no\nmaximum in that row; that typical offset is not characterized here at128SPS;')
            text = text.replace('under the electrical table\'s test conditions;',
                'under the electrical table\'s8SPS/25C test conditions;')
        target.write_text(text + note)
    citations = [AT + '/manifest.json', AT + '/murata-exact.txt physical p2',
                 AT + '/samsung/02.body', AT + '/samsung/datasheetData.raw.json',
                 AT + '/samsung/03.body', AT + '/adc/EVIDENCE.md', AT + '/adc/CLAUSE-INTERPRETATION.md']
    for brief in overlay['judgments'].values():
        if brief['requirement'] in ('ACQ-ELEC-1','ACQ-ELEC-2','ACQ-ELEC-3','ACQ-ELEC-4','ACQ-MECH-4'):
            brief['sources'].extend(citations)
            brief['questions'].append('Evaluate the newly captured exact sources and rate qualifiers; inherited conclusions grant no credit. Power-model correction remains pending; no guaranteed capacitor or128SPS total-accuracy bound is asserted.')
    return dict(captured_sources={AT + '/' + n: sha256(raw) for n, raw in captures.items()},
                geometry_model_sha256=CORRECTED_MODEL,
                scope='Source attribution and ADC applicability only; numerical power models unchanged; fresh judgments required')
