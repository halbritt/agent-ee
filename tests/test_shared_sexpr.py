"""Shared captured-byte parser contract; consumer format rules stay separate."""
import unittest
from agent_ee.symbol_library import LibraryError, parse_library, parse_sexpr

class SharedSexprTests(unittest.TestCase):
    def test_footprint_ast_keeps_duplicate_pads_and_quoted_text(self):
        data = b'(footprint "Example" ; comment\n (pad "1" smd rect) (pad "1" smd rect) (property "note" "two words"))'
        self.assertEqual(parse_sexpr(data), ['footprint', 'Example', ['pad', '1', 'smd', 'rect'], ['pad', '1', 'smd', 'rect'], ['property', 'note', 'two words']])

    def test_library_wrapper_retains_format_and_symbol_guards(self):
        self.assertEqual(parse_library(b'(kicad_symbol_lib (symbol "R"))'), {'R': ['symbol', 'R']})
        for data in (b'(footprint "R")', b'(kicad_symbol_lib)', b'(kicad_symbol_lib (symbol "R") (symbol "R"))'):
            with self.subTest(data=data), self.assertRaises(LibraryError):
                parse_library(data)

    def test_invalid_incomplete_or_multiple_roots_refuse(self):
        for data in (b'', b'()', b'atom', b'(a)(b)', b'(a', b'a(b)', b'(a))', b'(a "open)', b'(a \xff)'):
            with self.subTest(data=data), self.assertRaises(LibraryError):
                parse_sexpr(data)

    def test_parser_keeps_resource_bounds(self):
        for data in ('(a)', b'('*129+b'a'+b')'*129, b'x'*(32*1024*1024+1)):
            with self.subTest(kind=type(data).__name__, size=len(data)), self.assertRaises(LibraryError):
                parse_sexpr(data)
