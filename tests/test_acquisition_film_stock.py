"""A fresh assembly observation must not rewrite model-bound purchasing identities."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import acquisition_film as film
from tests.test_acquisition_film import BASE, ROOT

STOCK=Path(os.environ.get('AGENT_EE_FILM_STOCK','/nonexistent/stock'))
C3=Path(os.environ.get('AGENT_EE_FILM_C3_ASSEMBLY','/nonexistent/c3-assembly'))


@unittest.skipUnless((STOCK/'catalog.json').is_file(),'complete raw-backed capture required')
class FilmStockTests(unittest.TestCase):
    def files(self):
        return film.sources(BASE,ROOT/'selected')[0]

    def test_refresh_retains_exact_catalog_time_and_model_bound_inputs(self):
        files=self.files()
        before=dict(files)
        film._stock_catalog(files,STOCK)
        self.assertEqual(files[film.STOCK],(STOCK/'catalog.json').read_bytes())
        self.assertTrue(set(files)>set(before))
        self.assertEqual({name:files[name] for name in before},before)
        film._requirements(files)
        from agent_ee.typed_overlay import _check_pool
        check=_check_pool(json.loads(files['requirement-map.json']))['assembly']
        self.assertEqual(check['params']['catalog'],film.STOCK)
        self.assertEqual(check['params']['max_age_hours'],24)
        self.assertEqual(files['parts.json'],before['parts.json'])
        self.assertEqual(files['circuit.json'],before['circuit.json'])

    def test_changed_selected_identity_or_incomplete_population_refuses(self):
        for fault in ('mpn','package','coverage'):
            with self.subTest(fault=fault):
                files=self.files()
                parts=json.loads(files['parts.json'])
                if fault=='coverage':parts['parts']=[p for p in parts['parts'] if p['id']!='U2']
                else:next(p for p in parts['parts'] if p['id']=='U2')[fault]='different'
                files['parts.json']=json.dumps(parts).encode()
                with self.assertRaisesRegex(ValueError,'selected (identity|LCSC identities)'):
                    film._stock_catalog(files,STOCK)

    def test_changed_raw_response_or_symlink_cannot_borrow_capture(self):
        for fault in ('bytes','alias'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)/'capture'
                shutil.copytree(STOCK,root)
                catalog=json.loads((root/'catalog.json').read_bytes())
                name=catalog['requests'][0]['response']['path']
                path=root/name
                if fault=='bytes':path.write_bytes(path.read_bytes()+b'\n')
                else:
                    path.unlink();path.symlink_to(STOCK/name)
                with self.assertRaises(ValueError):film._stock_catalog(self.files(),root)


@unittest.skipUnless((C3/'capture.json').is_file(),'captured C3 series specification required')
class C3AssemblySourceTests(unittest.TestCase):
    """Regression for the 2026-11-01 negative ACQ-MECH-4 judgment: C3 had no process source."""

    def files(self):
        return film.sources(BASE,ROOT/'selected')[0]

    def test_series_specification_reaches_only_the_assembly_judgment_and_changes_nothing_else(self):
        files=self.files()
        before=dict(files)
        film._c3_assembly_source(files,C3)
        self.assertEqual({name:files[name] for name in before},before)
        self.assertEqual(set(files)-set(before),{film.C3_ASSEMBLY+name for name in ('capture.json','datasheet.pdf','datasheet.txt')})
        film._requirements(files)
        briefs=json.loads(files['preparation/judgment-briefs.json'])
        attached=[brief['requirement'] for brief in briefs.values()
                  if film.C3_ASSEMBLY+'datasheet.txt' in brief['sources']]
        self.assertEqual(attached,['ACQ-MECH-4-ASSEMBLY-APPLICABILITY'])

    def test_without_the_source_the_brief_is_unchanged(self):
        files=self.files()
        film._requirements(files)
        briefs=json.loads(files['preparation/judgment-briefs.json'])
        self.assertFalse(any(film.C3_ASSEMBLY in source for brief in briefs.values() for source in brief['sources']))

    def test_changed_capture_or_different_selected_part_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'capture'
            shutil.copytree(C3,root)
            path=root/'datasheet.txt'
            path.chmod(0o644)
            path.write_bytes(path.read_bytes()+b'\n')
            with self.assertRaises(ValueError):
                film._c3_assembly_source(self.files(),root)
        files=self.files()
        parts=json.loads(files['parts.json'])
        next(part for part in parts['parts'] if part['id']=='C3')['mpn']='different'
        files['parts.json']=json.dumps(parts).encode()
        with self.assertRaisesRegex(ValueError,'different selected part'):
            film._c3_assembly_source(files,C3)
