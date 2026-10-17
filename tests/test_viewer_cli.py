"""CLI export and server lifetime without a graphical desktop."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_ee.cli import main


class ViewerCliTests(unittest.TestCase):
    def test_export_only_does_not_open_browser_or_listen(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "index.html"
            with patch("agent_ee.viewer.build_viewer", return_value=page) as build, \
                    patch("agent_ee.viewer.serve_viewer") as serve, \
                    patch("webbrowser.open") as browser, contextlib.redirect_stdout(io.StringIO()):
                result = main(["view3d", "board.kicad_pcb", "--out", directory, "--export-only"])
            self.assertEqual(result, 0)
            build.assert_called_once_with("board.kicad_pcb", directory)
            serve.assert_not_called()
            browser.assert_not_called()

    def test_interrupt_closes_server_and_no_browser_supports_headless_use(self):
        server = MagicMock()
        server.__enter__.return_value = server
        server.server_address = ("127.0.0.1", 14832)
        server.serve_forever.side_effect = KeyboardInterrupt
        output = io.StringIO()
        with patch("agent_ee.viewer.build_viewer", return_value=Path("/tmp/pcb-preview/index.html")), \
                patch("agent_ee.viewer.serve_viewer", return_value=server), \
                patch("webbrowser.open") as browser, contextlib.redirect_stdout(output):
            result = main(["view3d", "board.kicad_pcb", "--out", "/tmp/pcb-preview", "--no-browser"])
        self.assertEqual(result, 0)
        self.assertIn("http://127.0.0.1:14832/", output.getvalue())
        server.__exit__.assert_called_once()
        browser.assert_not_called()

    def test_bad_port_fails_before_export(self):
        with patch("agent_ee.viewer.build_viewer") as build, contextlib.redirect_stderr(io.StringIO()):
            result = main(["view3d", "board.kicad_pcb", "--out", "unused", "--port", "65536"])
        self.assertEqual(result, 2)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
