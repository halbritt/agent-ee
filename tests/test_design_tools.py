"""Discovery helpers consume real-shaped scripted bytes without network or KiCad."""

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import design_tools as tools
from tests.test_jlc_stock_capture import row, search
from tests.test_native_binding import GEN
from tests.land_pattern_fixtures import chip_kicad, kicad_footprint


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def exchange(self, raw):
        return dict(http_status=200, reason=None, body=base64.b64encode(raw).decode())

    def test_search_retains_response_and_prefers_stocked_basic_with_datasheet(self):
        raw = search(row('C2', 100, componentLibraryType='expand', dataManualOfficialLink='https://example.org/a.pdf'),
                     row('C1', 50, dataManualFileAccessIdUrl='https://example.org/b.pdf'),
                     row('C3', 0))
        with patch.object(tools.stock, '_query', return_value=self.exchange(raw)) as query:
            result = tools.search_parts('resistor', self.root / 'search')
        self.assertEqual(query.call_args.args[1], 'resistor')
        self.assertEqual([p['lcsc'] for p in result['parts']], ['C1', 'C2', 'C3'])
        self.assertEqual((self.root / 'search/response.json').read_bytes(), raw)
        self.assertEqual(result['parts'][2]['datasheet_status'], 'unavailable')
        self.assertIn('PDF not yet verified', result['parts'][0]['datasheet_status'])
        self.assertIn('price1', result['parts'][0])

    def test_out_of_stock_parts_sort_after_every_stocked_part(self):
        raw = search(row('C10', 0, dataManualFileAccessIdUrl='https://example.org/a.pdf'),
                     row('C11', 5, componentLibraryType='expand'))
        with patch.object(tools.stock, '_query', return_value=self.exchange(raw)):
            result = tools.search_parts('resistor', self.root / 'search')
        # A stocked Extended part without a datasheet still precedes an out-of-stock Basic one.
        self.assertEqual([p['lcsc'] for p in result['parts']], ['C11', 'C10'])

    def test_failed_exchange_retained_before_error(self):
        raw = b'upstream unavailable'
        exchange = self.exchange(raw)
        exchange.update(http_status=503, reason='HTTP 503')
        with patch.object(tools.stock, '_query', return_value=exchange):
            with self.assertRaisesRegex(ValueError, '503'):
                tools.search_parts('timer', self.root / 'search')
        self.assertEqual((self.root / 'search/response.json').read_bytes(), raw)
        self.assertEqual(json.loads((self.root / 'search/exchange.json').read_text())['http_status'], 503)

    def test_keyword_is_one_to_120_characters(self):
        with patch.object(tools.stock, '_query') as query:
            for n, keyword in enumerate(('', '   ', 'x' * 121, None)):
                with self.assertRaisesRegex(ValueError, 'keyword: supply 1 to 120 characters'):
                    tools.search_parts(keyword, self.root / f'search-{n}')
            query.assert_not_called()
        self.assertFalse(any(self.root.iterdir()))

    def test_successive_searches_are_spaced_by_the_catalog_interval(self):
        raw = search(row('C1', 10))
        with patch.object(tools.stock, '_query', return_value=self.exchange(raw)), \
             patch.object(tools.time, 'sleep') as sleep:
            tools.search_parts('first', self.root / 'search-1')
            sleep.assert_not_called()
            tools.search_parts('second', self.root / 'search-2')
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args.args[0], tools.stock.DEFAULT_INTERVAL - 1)
        self.assertLessEqual(sleep.call_args.args[0], tools.stock.DEFAULT_INTERVAL)

    def test_custom_pad_inventory_refuses_without_starting_native_geometry(self):
        footprints = self.root / 'footprints/Owned.pretty'
        footprints.mkdir(parents=True)
        (footprints / 'Custom.kicad_mod').write_text(
            '(footprint "Custom" (layer "F.Cu") (pad "1" smd custom (at 0 0)))')
        with patch('agent_ee.footprint_library.index_footprints') as native_capable_reader:
            with self.assertRaisesRegex(ValueError, 'custom pads'):
                tools.inspect_footprint('Owned:Custom', self.root)
        native_capable_reader.assert_not_called()

    def test_local_inventory_exposes_actual_pin_and_pad_numbers(self):
        symbols = self.root / 'symbols'
        symbols.mkdir()
        (symbols / 'Owned.kicad_sym').write_bytes(GEN.library())
        footprints = self.root / 'footprints/Owned.pretty'
        footprints.mkdir(parents=True)
        (footprints / 'R2.kicad_mod').write_bytes(kicad_footprint('R2', chip_kicad(1, 1, 1)))
        symbol = tools.inspect_symbol('Owned:R', self.root)
        footprint = tools.inspect_footprint('Owned:R2', self.root)
        self.assertEqual({p['number'] for p in symbol['pins']}, {'1', '2'})
        self.assertEqual(footprint['footprints'][0]['electrical_numbers'], ['1', '2'])
        with self.assertRaises(ValueError):
            tools.inspect_symbol('../Owned:R', self.root)

    def test_inspect_courtyard_extracts_dimensions_and_sums_area(self):
        footprints = self.root / 'footprints/Owned.pretty'
        footprints.mkdir(parents=True)
        (footprints / 'R1.kicad_mod').write_text(
            '(footprint "R1" (layer "F.Cu")\n'
            '  (fp_line (start -1.5 -1.0) (end 1.5 -1.0) (layer "F.CrtYd"))\n'
            '  (fp_line (start 1.5 -1.0) (end 1.5 1.0) (layer "F.CrtYd"))\n'
            '  (fp_line (start 1.5 1.0) (end -1.5 1.0) (layer "F.CrtYd"))\n'
            '  (fp_line (start -1.5 1.0) (end -1.5 -1.0) (layer "F.CrtYd"))\n'
            '  (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu"))\n'
            '  (pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu")))'
        )
        (footprints / 'R2.kicad_mod').write_text(
            '(footprint "R2" (layer "F.Cu")\n'
            '  (fp_rect (start -2.0 -0.5) (end 2.0 0.5) (layer "F.CrtYd"))\n'
            '  (pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu")))'
        )
        result = tools.inspect_courtyard(['Owned:R1', 'Owned:R2'], self.root)
        self.assertEqual(result['schema'], 'agent-ee.design-courtyard/1')
        self.assertEqual(len(result['footprints']), 2)
        r1_c = result['footprints'][0]['courtyard']
        self.assertEqual(r1_c['status'], 'linear_courtyard')
        self.assertEqual(r1_c['bbox_mm'], [-1.5, -1.0, 1.5, 1.0])
        self.assertEqual(r1_c['width_mm'], 3.0)
        self.assertEqual(r1_c['height_mm'], 2.0)
        self.assertEqual(r1_c['area_mm2'], 6.0)
        r2_c = result['footprints'][1]['courtyard']
        self.assertEqual(r2_c['status'], 'linear_courtyard')
        self.assertEqual(r2_c['bbox_mm'], [-2.0, -0.5, 2.0, 0.5])
        self.assertEqual(r2_c['width_mm'], 4.0)
        self.assertEqual(r2_c['height_mm'], 1.0)
        self.assertEqual(r2_c['area_mm2'], 4.0)
        self.assertEqual(result['courtyard_area_sum_mm2'], 10.0)

    def test_inspect_courtyard_handles_missing_courtyard(self):
        footprints = self.root / 'footprints/Owned.pretty'
        footprints.mkdir(parents=True)
        (footprints / 'NoCrt.kicad_mod').write_text(
            '(footprint "NoCrt" (layer "F.Cu")\n'
            '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'
        )
        result = tools.inspect_courtyard(['Owned:NoCrt'], self.root)
        self.assertEqual(result['footprints'][0]['courtyard']['status'], 'none')
        self.assertIsNone(result['footprints'][0]['courtyard']['area_mm2'])
        self.assertEqual(result['courtyard_area_sum_mm2'], 0.0)


if __name__ == '__main__':
    unittest.main()
