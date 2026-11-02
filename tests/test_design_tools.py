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

    def test_failed_exchange_retained_before_error(self):
        raw = b'upstream unavailable'
        exchange = self.exchange(raw)
        exchange.update(http_status=503, reason='HTTP 503')
        with patch.object(tools.stock, '_query', return_value=exchange):
            with self.assertRaisesRegex(ValueError, '503'):
                tools.search_parts('timer', self.root / 'search')
        self.assertEqual((self.root / 'search/response.json').read_bytes(), raw)

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


if __name__ == '__main__':
    unittest.main()
