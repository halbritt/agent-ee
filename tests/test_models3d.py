import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from agent_ee.models3d import (
    STOCK_REPOSITORY,
    STOCK_REVISIONS,
    ModelFetchError,
    fetch_stock_models,
    inspect_models,
    parse_stock_receipt,
    stock_reference,
    copy_project_models,
    verify_project_models,
    _kicad_model_defaults,
)


class ModelResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class ModelInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.board = self.root / "board.kicad_pcb"

    def write_board(self, footprints):
        self.board.write_text(f"(kicad_pcb {footprints})")

    def test_every_footprint_including_legacy_and_disabled_models(self):
        model = self.root / "present.step"
        model.write_bytes(b"configured local model")
        self.write_board('''
            (footprint "Lib:NoModel" (property "Reference" "TP1"))
            (footprint "Lib:Hidden" (property "Reference" "U1")
                (model "absent.step" hide))
            (footprint "Lib:Hidden2" (property "Reference" "U2")
                (model "absent.step" (hide yes)))
            (module "Lib:Legacy" (fp_text reference "R1" (effects (font (size 1 1))))
                (model "present.step" (hide no)))
            (footprint "Lib:Partial" (property "Reference" "J1")
                (model "present.step") (model "missing.step"))
        ''')
        inventory = inspect_models(self.board, {})
        footprints = {footprint["reference"]: footprint for footprint in inventory["footprints"]}
        self.assertEqual({reference: footprint["status"] for reference, footprint in footprints.items()},
                         {"TP1": "no_model", "U1": "disabled", "U2": "disabled",
                          "R1": "resolved", "J1": "partial"})
        resolved = footprints["R1"]["models"][0]
        self.assertEqual(resolved["resolved_path"], str(model))
        self.assertEqual(resolved["sha256"], hashlib.sha256(model.read_bytes()).hexdigest())
        self.assertEqual(resolved["size_bytes"], len(model.read_bytes()))
        self.assertNotIn("sha256", footprints["U1"]["models"][0])
        self.assertEqual(inventory["summary"], {"footprints": 5, "models": 5, "no_model": 1,
                         "footprints_fully_resolved": 1, "resolved": 2, "missing": 1,
                         "unresolved": 0, "disabled": 2})

    def test_nested_escaped_strings_and_exact_filenames(self):
        filename = 'shape (version "A") \\ body.step'
        model = self.root / filename
        model.write_bytes(b"exact")
        quoted = filename.replace("\\", "\\\\").replace('"', '\\"')
        self.write_board(f'''(footprint "Lib:Escaped" (property "Reference" "U(1)\\\"x")
            (property "Description" "literal (model fake) and \\\\ path")
            (model "{quoted}" (offset (xyz 1 2 3)) (scale (xyz 1 1 1))))''')
        footprint = inspect_models(self.board, {})["footprints"][0]
        self.assertEqual(footprint["reference"], 'U(1)"x')
        self.assertEqual(footprint["models"][0]["path"], filename)
        self.assertEqual(footprint["models"][0]["resolved_path"], str(model))
        self.assertEqual(footprint["status"], "resolved")

    def test_deep_nested_non_footprint_geometry_is_not_inventory(self):
        nested = "(extra " * 1500 + '"(footprint fake)"' + ")" * 1500
        self.write_board(f'{nested} (footprint "Real" (property "Reference" "X1"))')
        self.assertEqual(inspect_models(self.board, {})["summary"]["footprints"], 1)

    def test_project_variables_kiprjmod_and_explicit_environment(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "part.step").write_bytes(b"part")
        project = self.board.with_suffix(".kicad_pro")
        project.write_text(json.dumps({"text_variables": {"BASE": "${KIPRJMOD}/assets", "MODEL": "${BASE}/part.step"}}))
        self.write_board('''(footprint "Lib:Part" (property "Reference" "U1")
                            (model "${MODEL}"))''')
        inventory = inspect_models(self.board, {})
        self.assertEqual(inventory["footprints"][0]["models"][0]["resolved_path"], str(assets / "part.step"))
        self.assertEqual(inventory["project"]["sha256"], hashlib.sha256(project.read_bytes()).hexdigest())
        overridden = inspect_models(self.board, {"BASE": str(self.root / "missing")})
        self.assertEqual(overridden["footprints"][0]["status"], "missing")

    def test_unknown_cyclic_embedded_alias_and_windows_paths_are_explicit(self):
        self.write_board('''(footprint "Lib:Part" (property "Reference" "U1")
            (model "${UNKNOWN}/part.step") (model "${A}/part.step")
            (model "kicad-embed://body.step") (model ":MYALIAS:body.step")
            (model "C:/models/body.step"))''')
        with patch.dict(os.environ, {"UNKNOWN": str(self.root)}):
            models = inspect_models(self.board, {"A": "${B}", "B": "${A}"})["footprints"][0]["models"]
        self.assertEqual([model["status"] for model in models], ["unresolved"] * 5)
        self.assertEqual(models[0]["unresolved_variables"], ["UNKNOWN"])
        self.assertTrue(models[1]["unresolved_variables"])
        self.assertTrue(all("sha256" not in model for model in models))

    def test_kicad_stock_variables_are_resolved_without_guessing_alternates(self):
        models = self.root / "models/Resistor_SMD.3dshapes"
        models.mkdir(parents=True)
        (models / "part.step").write_bytes(b"a STEP file is not a WRL replacement")
        self.write_board('''(footprint "Lib:R" (property "Reference" "R1")
            (model "${KISYS3DMOD}/Resistor_SMD.3dshapes/part.wrl"))''')
        inventory = inspect_models(self.board, {"KISYS3DMOD": str(models.parent)})
        self.assertEqual(inventory["footprints"][0]["models"][0]["status"], "missing")

    def test_malformed_board_and_project_fail(self):
        for text in ('(kicad_pcb (footprint "unterminated))', '(kicad_pcb', '(kicad_pcb))',
                     '(kicad_pcb) (kicad_pcb)', '(footprint "standalone")'):
            with self.subTest(text=text):
                self.board.write_text(text)
                with self.assertRaises(ValueError):
                    inspect_models(self.board, {})
        self.write_board("")
        for project in ('{broken', '[]', '{"text_variables":{"X":123}}'):
            with self.subTest(project=project):
                self.board.with_suffix(".kicad_pro").write_text(project)
                with self.assertRaisesRegex(ValueError, "project"):
                    inspect_models(self.board, {})

    def test_real_board_inventory_has_exact_references_and_missing_variables(self):
        board = Path(__file__).parents[1] / "fixtures/rc/control/rc_filter.kicad_pcb"
        inventory = inspect_models(board, {}, kicad_defaults={})
        self.assertEqual({footprint["reference"] for footprint in inventory["footprints"]}, {"C1", "R1", "J1"})
        self.assertEqual(inventory["summary"]["models"], 3)
        self.assertEqual(inventory["summary"]["unresolved"], 3)
        for footprint in inventory["footprints"]:
            self.assertIn("${KICAD10_3DMODEL_DIR}/", footprint["models"][0]["path"])

    def test_real_board_inventory_resolves_injected_stock_defaults(self):
        board = Path(__file__).parents[1] / "fixtures/rc/control/rc_filter.kicad_pcb"
        absent = inspect_models(board, {}, kicad_defaults={})
        library = self.root / 'stock'
        for footprint in absent['footprints']:
            relative = footprint['models'][0]['path'].removeprefix('${KICAD10_3DMODEL_DIR}/')
            model = library / relative
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(b'original inventory fixture for ' + relative.encode())
        with patch('agent_ee.models3d._kicad_model_defaults',
                   side_effect=AssertionError('injected defaults must bypass host discovery')):
            inventory = inspect_models(board, {}, kicad_defaults={'KICAD10_3DMODEL_DIR': str(library)})
            self.assertEqual(inspect_models(board, {}, kicad_defaults={}), absent)
            overridden = inspect_models(board, {'KICAD10_3DMODEL_DIR': str(self.root / 'absent')},
                                        kicad_defaults={'KICAD10_3DMODEL_DIR': str(library)})
            self.assertEqual(overridden['summary']['missing'], 3)
            for footprint in overridden['footprints']:
                self.assertEqual(footprint['models'][0]['resolution_source'], 'env')
                self.assertNotIn('sha256', footprint['models'][0])
        self.assertIsNone(inventory['kicad_default'])
        self.assertEqual(inventory['summary']['resolved'], 3)
        self.assertEqual(inventory['summary']['unresolved'], 0)
        self.assertEqual(inventory['summary']['missing'], 0)
        for footprint in inventory['footprints']:
            model = footprint['models'][0]
            self.assertEqual(model['resolution_source'], 'kicad-default')
            self.assertEqual(model['variable_sources'], {'KICAD10_3DMODEL_DIR': 'kicad-default'})
            self.assertEqual(model['sha256'], hashlib.sha256(Path(model['resolved_path']).read_bytes()).hexdigest())


class ModelClosureTests(unittest.TestCase):
    setUp = ModelInventoryTests.setUp
    write_board = ModelInventoryTests.write_board
    def test_default_identity_unknown_version_and_absent_library(self):
        from subprocess import CompletedProcess
        with patch('agent_ee.models3d.shutil.which', return_value='/usr/bin/kicad-cli'), \
                patch('agent_ee.models3d.subprocess.run', return_value=CompletedProcess([], 0, '10.0.7\n', '')):
            defaults, identity = _kicad_model_defaults({})
            self.assertEqual(identity['version'], '10.0.7')
            self.assertEqual(identity['source'], 'kicad-default')
            self.assertEqual(identity['directory'], '/usr/share/kicad/3dmodels')
            # Both library branches are injected, so neither depends on the host's packages.
            library = self.root / 'stock'
            library.mkdir()
            with patch('agent_ee.models3d.KICAD10_LINUX_MODEL_DIR', library):
                defaults, identity = _kicad_model_defaults({})
                self.assertEqual(defaults, {'KICAD10_3DMODEL_DIR': str(library)})
                self.assertEqual((identity['directory'], identity['available']), (str(library), True))
            with patch('agent_ee.models3d.KICAD10_LINUX_MODEL_DIR', self.root / 'absent'):
                defaults, identity = _kicad_model_defaults({})
                self.assertEqual(defaults, {})
                self.assertEqual((identity['directory'], identity['available']), (str(self.root / 'absent'), False))
            with patch('agent_ee.models3d.Path.is_dir', return_value=False):
                self.assertEqual(_kicad_model_defaults({})[0], {})
        for version, code in (('9.0.7', 0), ('11.0.0', 0), ('10.0.7', 1), ('bad', 0)):
            with self.subTest(version=version, code=code), \
                    patch('agent_ee.models3d.shutil.which', return_value='/usr/bin/kicad-cli'), \
                    patch('agent_ee.models3d.subprocess.run', return_value=CompletedProcess([], code, version, '')):
                self.assertEqual(_kicad_model_defaults({}), ({}, None))

    def test_default_sources_env_precedence_and_missing_default(self):
        library = self.root / 'stock'
        library.mkdir()
        (library / 'body.step').write_bytes(b'owned model')
        self.write_board('(footprint "Owned" (model "${KICAD10_3DMODEL_DIR}/body.step"))')
        defaults = {'KICAD10_3DMODEL_DIR': str(library)}
        with patch('agent_ee.models3d._kicad_model_defaults', return_value=(defaults, None)):
            model = inspect_models(self.board, {})['footprints'][0]['models'][0]
            self.assertEqual((model['status'], model['resolution_source']), ('resolved', 'kicad-default'))
            self.assertEqual(model['sha256'], hashlib.sha256(b'owned model').hexdigest())
            model = inspect_models(self.board, {'KICAD10_3DMODEL_DIR': str(self.root / 'absent')})['footprints'][0]['models'][0]
            self.assertEqual((model['status'], model['resolution_source']), ('missing', 'env'))
            self.assertNotIn('sha256', model)
        with patch('agent_ee.models3d._kicad_model_defaults', return_value=({}, None)):
            model = inspect_models(self.board, {})['footprints'][0]['models'][0]
            self.assertEqual((model['status'], model['resolution_source']), ('unresolved', 'unresolved'))

    def test_legacy_kisys3dmod_follows_kicad10_model_dir_like_kicad_cli(self):
        # Observed with the pinned kicad-cli 10.0.7 (AGENTEE-428): its own value
        # wins, else the process KICAD10_3DMODEL_DIR (environment, then built-in
        # default); a project text variable KICAD10_3DMODEL_DIR does not feed it.
        library, alternate = self.root / 'stock', self.root / 'alternate'
        for directory in (library, alternate):
            (directory / 'R.3dshapes').mkdir(parents=True)
            (directory / 'R.3dshapes/r.step').write_bytes(b'model in ' + directory.name.encode())
        self.write_board('(footprint "R" (model "${KISYS3DMOD}/R.3dshapes/r.step"))')
        defaults = {'KICAD10_3DMODEL_DIR': str(library)}

        def model(env, kicad_defaults=defaults):
            inventory = inspect_models(self.board, env, kicad_defaults=kicad_defaults)
            return inventory['footprints'][0]['models'][0], inventory['variable_aliases']
        record, aliases = model({})
        self.assertEqual((record['status'], record['resolution_source']), ('resolved', 'kicad-default'))
        self.assertEqual(record['resolved_path'], str(library / 'R.3dshapes/r.step'))
        self.assertEqual(record['variable_sources'], {'KISYS3DMOD': 'kicad-default'})
        self.assertEqual(record['sha256'], hashlib.sha256(b'model in stock').hexdigest())
        self.assertEqual(aliases, {'KISYS3DMOD': {'variable': 'KICAD10_3DMODEL_DIR', 'source': 'kicad-default'}})
        record, aliases = model({'KICAD10_3DMODEL_DIR': str(alternate)})
        self.assertEqual((record['status'], record['resolution_source']), ('resolved', 'env'))
        self.assertEqual(record['resolved_path'], str(alternate / 'R.3dshapes/r.step'))
        self.assertEqual(aliases['KISYS3DMOD']['source'], 'env')
        # An explicit missing path never falls back to the default.
        record, _ = model({'KICAD10_3DMODEL_DIR': str(self.root / 'absent')})
        self.assertEqual((record['status'], record['resolution_source']), ('missing', 'env'))
        self.assertNotIn('sha256', record)
        record, aliases = model({'KISYS3DMOD': str(self.root / 'absent'), 'KICAD10_3DMODEL_DIR': str(alternate)})
        self.assertEqual((record['status'], record['resolution_source'], aliases), ('missing', 'env', {}))
        project = self.board.with_suffix('.kicad_pro')
        project.write_text(json.dumps({'text_variables': {'KICAD10_3DMODEL_DIR': str(alternate)}}))
        record, _ = model({})
        self.assertEqual(record['resolved_path'], str(library / 'R.3dshapes/r.step'))
        project.write_text(json.dumps({'text_variables': {'KISYS3DMOD': str(self.root / 'absent')}}))
        record, aliases = model({'KICAD10_3DMODEL_DIR': str(alternate)})
        self.assertEqual((record['status'], record['resolution_source'], aliases), ('missing', 'project', {}))
        project.unlink()
        # Absent library: unresolved, never claimed.
        record, aliases = model({}, {})
        self.assertEqual((record['status'], record['resolution_source'], aliases), ('unresolved', 'unresolved', {}))
        # No qualified KiCad 10.0.x CLI: the alias is KiCad behaviour and is not inferred.
        with patch('agent_ee.models3d._kicad_model_defaults', return_value=({}, None)):
            record, aliases = model({'KICAD10_3DMODEL_DIR': str(alternate)}, None)
        self.assertEqual((record['status'], record['unresolved_variables'], aliases), ('unresolved', ['KISYS3DMOD'], {}))
        identity = {'version': '10.0.7', 'available': False}
        with patch('agent_ee.models3d._kicad_model_defaults', return_value=({}, identity)):
            record, aliases = model({'KICAD10_3DMODEL_DIR': str(alternate)}, None)
        self.assertEqual((record['status'], record['resolution_source']), ('resolved', 'env'))
        with patch('agent_ee.models3d._kicad_model_defaults', return_value=(defaults, identity)):
            record, _ = model({}, None)
        self.assertEqual((record['status'], record['resolution_source']), ('resolved', 'kicad-default'))

    def test_project_closure_hashes_aliases_duplicates_and_missing(self):
        models = self.root / 'inputs/models'
        models.mkdir(parents=True)
        (models / 'owned.stp').write_bytes(b'owned STEP fixture')
        self.board.with_suffix('.kicad_pro').write_text(json.dumps({'text_variables': {
            'BODY': '${KIPRJMOD}/inputs/models/owned.stp'}}))
        self.write_board('''(footprint "Owned" (property "Reference" "J1")
            (model "${BODY}") (model "${KIPRJMOD}/inputs/models/owned.stp")
            (model "${KIPRJMOD}/inputs/models/missing.stp")
            (model "${KIPRJMOD}/inputs/models/disabled.stp" hide))''')
        destination = self.root / 'proposal'
        destination.mkdir()
        closure = copy_project_models(self.board, destination)
        self.assertEqual(list(closure['files']), ['inputs/models/owned.stp'])
        target = destination / 'inputs/models/owned.stp'
        self.assertEqual(closure['files']['inputs/models/owned.stp']['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual([r['status'] for r in closure['references']], ['resolved', 'resolved', 'missing'])
        self.assertFalse((destination / 'inputs/models/missing.stp').exists())
        verify_project_models(self.board, destination, closure)
        target.write_bytes(b'changed proposal')
        with self.assertRaisesRegex(ValueError, 'project model changed'):
            verify_project_models(self.board, destination, closure)
        target.write_bytes(b'owned STEP fixture')
        (models / 'missing.stp').write_bytes(b'appeared')
        with self.assertRaisesRegex(ValueError, 'missing.*appeared'):
            verify_project_models(self.board, destination, closure)

    def test_project_closure_escape_symlink_and_collision_refuse(self):
        destination = self.root / 'proposal'
        destination.mkdir()
        outside = self.root / 'outside.step'
        outside.write_bytes(b'owned outside fixture')
        project = self.root / 'project'
        project.mkdir()
        self.board = project / 'board.kicad_pcb'
        for path in ('${KIPRJMOD}/../outside.step', 'body.step'):
            if path == 'body.step':
                (project / path).symlink_to(outside)
            self.write_board(f'(footprint "Owned" (model "{path}"))')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'escapes source project'):
                copy_project_models(self.board, destination)
        (project / 'part.step').write_bytes(b'owned')
        (destination / 'part.step').write_bytes(b'protected proposal')
        self.write_board('(footprint "Owned" (model "${KIPRJMOD}/part.step"))')
        with self.assertRaisesRegex(ValueError, 'collides'):
            copy_project_models(self.board, destination)
        self.assertEqual((destination / 'part.step').read_bytes(), b'protected proposal')

        # Even a missing source must not resolve to an unrelated proposal artifact.
        (project / 'part.step').unlink()
        with self.assertRaisesRegex(ValueError, 'collides'):
            copy_project_models(self.board, destination)

    def test_project_closure_preserves_internal_symlink_spelling(self):
        model = self.root / 'body.step'
        model.write_bytes(b'owned')
        (self.root / 'alias.step').symlink_to(model)
        self.write_board('(footprint "Owned" (model "${KIPRJMOD}/alias.step"))')
        destination = self.root / 'proposal'
        destination.mkdir()
        closure = copy_project_models(self.board, destination)
        self.assertEqual(list(closure['files']), ['alias.step'])
        self.assertFalse((destination / 'alias.step').is_symlink())
        verify_project_models(self.board, destination, closure)
        (destination / 'alias.step').unlink()
        (destination / 'alias.step').symlink_to(model)
        with self.assertRaisesRegex(ValueError, 'became a symlink'):
            verify_project_models(self.board, destination, closure)


    def test_project_closure_carries_absolute_in_project_paths(self):
        project = self.root / 'project'
        (project / 'inputs/models').mkdir(parents=True)
        (project / 'inputs/models/abs.step').write_bytes(b'owned absolute fixture')
        external = self.root / 'external.step'
        external.write_bytes(b'owned external fixture')
        self.board = project / 'board.kicad_pcb'
        self.write_board(f'''(footprint "Owned" (property "Reference" "J1")
            (model "{project}/inputs/models/abs.step")
            (model "{project}/inputs/models/../models/abs.step")
            (model "{project}/inputs/models/gone.step")
            (model "{external}"))''')
        destination = project / 'proposal'
        destination.mkdir()
        closure = copy_project_models(self.board, destination)
        self.assertEqual(closure['files'], {'inputs/models/abs.step': {
            'sha256': hashlib.sha256(b'owned absolute fixture').hexdigest(), 'size_bytes': 22}})
        self.assertEqual((destination / 'inputs/models/abs.step').read_bytes(), b'owned absolute fixture')
        self.assertEqual([(r['status'], r['proposal_path'], r['absolute']) for r in closure['references']],
                         [('resolved', 'inputs/models/abs.step', True)] * 2
                         + [('missing', 'inputs/models/gone.step', True)])
        self.assertNotIn(str(external), [r['path'] for r in closure['references']])
        verify_project_models(self.board, destination, closure)
        (project / 'inputs/models/abs.step').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'project model changed'):
            verify_project_models(self.board, destination, closure)
        (project / 'inputs/models/abs.step').write_bytes(b'owned absolute fixture')
        (project / 'inputs/models/gone.step').write_bytes(b'appeared')
        with self.assertRaisesRegex(ValueError, 'missing.*appeared'):
            verify_project_models(self.board, destination, closure)

    def test_project_closure_absolute_paths_escape_alias_and_collision(self):
        project = self.root / 'project'
        project.mkdir()
        outside = self.root / 'outside.step'
        outside.write_bytes(b'owned outside fixture')
        (project / 'link.step').symlink_to(outside)
        self.board = project / 'board.kicad_pcb'
        destination = project / 'proposal'
        destination.mkdir()
        self.write_board(f'(footprint "Owned" (model "{project}/link.step"))')
        with self.assertRaisesRegex(ValueError, 'escapes source project'):
            copy_project_models(self.board, destination)
        (project / 'visuals').mkdir()
        (project / 'visuals/m.step').write_bytes(b'owned')
        self.write_board(f'(footprint "Owned" (model "{project}/visuals/m.step"))')
        with self.assertRaisesRegex(ValueError, 'collides'):
            copy_project_models(self.board, destination)
        # A spelling of the project through an outside symlink is carried by its real location.
        (project / 'body.step').write_bytes(b'owned body')
        (self.root / 'alias').symlink_to(project)
        self.write_board(f'(footprint "Owned" (model "{self.root}/alias/body.step"))')
        closure = copy_project_models(self.board, destination)
        self.assertEqual(list(closure['files']), ['body.step'])
        self.assertEqual(closure['references'][0]['proposal_path'], 'body.step')
        verify_project_models(self.board, destination, closure)


class StockModelFetchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.board = self.root / "board.kicad_pcb"
        self.cache = self.root / "models"
        self.relative = "Resistor_SMD.3dshapes/R_0805_2012Metric.step"
        self.payload = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
        self.write_refs(["${KICAD10_3DMODEL_DIR}/" + self.relative])

    def write_refs(self, refs):
        footprints = " ".join(f'(footprint "Part" (property "Reference" "R{i}") (model "{ref}"))'
                              for i, ref in enumerate(refs, 1))
        self.board.write_text(f"(kicad_pcb {footprints})")

    def not_found(self, request, **kwargs):
        raise HTTPError(request.full_url, 404, "not found", {}, None)

    def test_exact_pinned_download_deduplicates_and_persists_verified_provenance(self):
        self.write_refs(["${KICAD10_3DMODEL_DIR}/" + self.relative, "${KISYS3DMOD}/" + self.relative])
        with patch("agent_ee.models3d.urlopen", return_value=ModelResponse(self.payload)) as request:
            manifest = fetch_stock_models(self.board, self.cache)
        self.assertEqual(request.call_count, 1)
        url = request.call_args.args[0].full_url
        self.assertEqual(url, f"{STOCK_REPOSITORY}/-/raw/{STOCK_REVISIONS[0]}/{self.relative}")
        self.assertEqual(request.call_args.kwargs["timeout"], 30)
        self.assertEqual((self.cache / self.relative).read_bytes(), self.payload)
        self.assertEqual(manifest["files"][0]["references"], ["R1", "R2"])
        self.assertEqual(manifest["downloaded"], 1)
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(self.payload).hexdigest())
        receipt = json.loads((self.cache / ".agent-ee-models.json").read_text())
        self.assertEqual(receipt["files"][self.relative]["commit"], STOCK_REVISIONS[0])
        with patch("agent_ee.models3d.urlopen") as request:
            reused = fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(reused["reused"], 1)
        inventory = inspect_models(self.board, {"KICAD10_3DMODEL_DIR": str(self.cache), "KISYS3DMOD": str(self.cache)})
        self.assertEqual(inventory["summary"]["resolved"], 2)

    def test_legacy_same_filename_fallback_only_after_404(self):
        def response(request, **kwargs):
            if STOCK_REVISIONS[0] in request.full_url:
                return self.not_found(request)
            return ModelResponse(self.payload)
        with patch("agent_ee.models3d.urlopen", side_effect=response) as request:
            manifest = fetch_stock_models(self.board, self.cache)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(manifest["files"][0]["commit"], STOCK_REVISIONS[1])
        self.assertTrue(all(call.args[0].full_url.endswith(self.relative) for call in request.call_args_list))

    def test_404_at_both_revisions_remains_missing(self):
        with patch("agent_ee.models3d.urlopen", side_effect=self.not_found):
            manifest = fetch_stock_models(self.board, self.cache)
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["missing"], 1)
        self.assertFalse((self.cache / self.relative).exists())
        self.assertEqual(len(manifest["files"][0]["attempts"]), 2)

    def test_non_404_network_errors_do_not_fallback(self):
        for error in (URLError("network unavailable"), TimeoutError("timed out"),
                      HTTPError("https://official", 403, "forbidden", {}, None)):
            with self.subTest(error=type(error).__name__):
                with patch("agent_ee.models3d.urlopen", side_effect=error) as request:
                    with self.assertRaisesRegex(ModelFetchError, "request failed"):
                        fetch_stock_models(self.board, self.cache)
                self.assertEqual(request.call_count, 1)
                self.assertFalse((self.cache / self.relative).exists())

    def test_private_project_models_and_disabled_refs_never_fetch(self):
        self.write_refs(["${KIPRJMOD}/secret.step", "relative/local.step", "https://private/body.step"])
        with patch("agent_ee.models3d.urlopen") as request:
            manifest = fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(manifest["downloaded"], 0)
        self.assertEqual(len(manifest["skipped"]), 3)
        self.board.write_text(f'(kicad_pcb (footprint "R" (model "${{KISYS3DMOD}}/{self.relative}" hide)))')
        with patch("agent_ee.models3d.urlopen") as request:
            manifest = fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(manifest["files"], [])

    def test_unsafe_stock_paths_refused_before_any_network_request(self):
        for relative in ("../outside.step", "/absolute.step", "Resistor_SMD.3dshapes/../x.step",
                         "Resistor_SMD.3dshapes/a.step?query", "Resistor_SMD.3dshapes/nested/a.step",
                         "Resistor_SMD.3dshapes/%2e%2e.step", "Resistor_SMD.3dshapes/body.exe"):
            with self.subTest(relative=relative):
                self.write_refs(["${KISYS3DMOD}/" + self.relative, "${KISYS3DMOD}/" + relative])
                with patch("agent_ee.models3d.urlopen") as request:
                    with self.assertRaisesRegex(ModelFetchError, "unsafe or unsupported"):
                        fetch_stock_models(self.board, self.cache)
                request.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_cache_symlinks_do_not_write_outside_cache(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.cache.mkdir()
        (self.cache / "Resistor_SMD.3dshapes").symlink_to(outside, target_is_directory=True)
        with patch("agent_ee.models3d.urlopen") as request:
            with self.assertRaisesRegex(ModelFetchError, "escapes"):
                fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_preexisting_local_files_preserved_and_changed_receipts_rejected(self):
        path = self.cache / self.relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"local model")
        with patch("agent_ee.models3d.urlopen") as request:
            manifest = fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(manifest["files"][0]["status"], "existing-local")
        self.assertNotIn("commit", manifest["files"][0])
        self.assertEqual(path.read_bytes(), b"local model")
        path.write_bytes(b"changed local model")
        with patch("agent_ee.models3d.urlopen") as request:
            with self.assertRaisesRegex(ModelFetchError, "cached model changed"):
                fetch_stock_models(self.board, self.cache)
        request.assert_not_called()
        self.assertEqual(path.read_bytes(), b"changed local model")

    def test_oversized_html_or_truncated_download_never_installed(self):
        class BrokenStream(ModelResponse):
            def read(self, size=-1):
                raise OSError("transfer interrupted")
        for stream, limit, reason in ((ModelResponse(self.payload), 4, "exceeds"),
                                      (ModelResponse(b"<html>access denied</html>"), 1024, "not the referenced"),
                                      (BrokenStream(self.payload), 1024, "transfer interrupted")):
            with self.subTest(reason=reason):
                with patch("agent_ee.models3d.urlopen", return_value=stream):
                    with self.assertRaisesRegex(ModelFetchError, reason):
                        fetch_stock_models(self.board, self.cache, max_file_bytes=limit)
                self.assertFalse((self.cache / self.relative).exists())
                self.assertEqual(list(self.cache.rglob(".model-download-*")), [])

    def test_wrl_signature_and_encoded_filename_are_preserved(self):
        relative = "Connector.3dshapes/Socket (test)+1.wrl"
        self.write_refs(["${KISYS3DMOD}/" + relative])
        payload = b"#VRML V2.0 utf8\nShape {}\n"
        with patch("agent_ee.models3d.urlopen", return_value=ModelResponse(payload)) as request:
            manifest = fetch_stock_models(self.board, self.cache)
        self.assertIn("Socket%20%28test%29%2B1.wrl", request.call_args.args[0].full_url)
        self.assertEqual((self.cache / relative).read_bytes(), payload)
        self.assertEqual(manifest["status"], "complete")

    def test_response_is_closed_when_local_download_setup_fails(self):
        response = ModelResponse(self.payload)
        with patch("agent_ee.models3d.urlopen", return_value=response):
            with patch("agent_ee.models3d.tempfile.mkstemp", side_effect=OSError("cache is full")):
                with self.assertRaisesRegex(ModelFetchError, "cache is full"):
                    fetch_stock_models(self.board, self.cache)
        self.assertTrue(response.closed)
        self.assertFalse((self.cache / self.relative).exists())

    def test_early_eof_with_content_length_never_installs_partial_model(self):
        response = ModelResponse(self.payload)
        response.headers["Content-Length"] = str(len(self.payload) + 100)
        with patch("agent_ee.models3d.urlopen", return_value=response):
            with self.assertRaisesRegex(ModelFetchError, "download was truncated"):
                fetch_stock_models(self.board, self.cache)
        self.assertTrue(response.closed)
        self.assertFalse((self.cache / self.relative).exists())
        self.assertEqual(list(self.cache.rglob(".model-download-*")), [])

    def test_malformed_receipt_entries_are_actionable_errors(self):
        self.cache.mkdir()
        for entry in ("bad", {}, {"sha256": "bad", "size_bytes": 3},
                      {"sha256": "a" * 64, "size_bytes": 3, "commit": STOCK_REVISIONS[0],
                       "source_url": "https://unrelated/model.step"}):
            with self.subTest(entry=entry):
                receipt = {"schema": "agent-ee.stock-models/1", "repository": STOCK_REPOSITORY,
                           "files": {self.relative: entry}}
                (self.cache / ".agent-ee-models.json").write_text(json.dumps(receipt))
                with patch("agent_ee.models3d.urlopen") as request:
                    with self.assertRaisesRegex(ModelFetchError, "invalid model cache"):
                        fetch_stock_models(self.board, self.cache)
                request.assert_not_called()


class StockReceiptHelperTests(unittest.TestCase):
    """Helpers that fab_consistency reuses to read a captured stock library (AGENTEE-124)."""

    def test_stock_reference_is_exact_and_refuses_unsafe_paths(self):
        self.assertEqual(stock_reference("${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.wrl"),
                         ("KICAD9_3DMODEL_DIR", "Resistor_SMD.3dshapes/R_0603_1608Metric.wrl"))
        self.assertEqual(stock_reference("${KISYS3DMOD}/Lib.3dshapes/a.step"), ("KISYS3DMOD", "Lib.3dshapes/a.step"))
        for path in ("${KIPRJMOD}/Lib.3dshapes/a.step", "$(KICAD10_3DMODEL_DIR)/Lib.3dshapes/a.step",
                     "/usr/share/kicad/3dmodels/Lib.3dshapes/a.step", None):
            with self.subTest(path=path):
                self.assertIsNone(stock_reference(path))
        for path in ("${KICAD10_3DMODEL_DIR}/../Lib.3dshapes/a.step", "${KICAD10_3DMODEL_DIR}/Lib/a.step",
                     "${KICAD10_3DMODEL_DIR}/Lib.3dshapes/sub/a.step"):
            with self.subTest(path=path), self.assertRaisesRegex(ModelFetchError, "unsafe or unsupported"):
                stock_reference(path)

    def test_parse_stock_receipt_validates_schema_and_provenance(self):
        relative = "Lib.3dshapes/a.step"
        entry = {"sha256": "a" * 64, "size_bytes": 3, "commit": STOCK_REVISIONS[0],
                 "source_url": f"{STOCK_REPOSITORY}/-/raw/{STOCK_REVISIONS[0]}/{relative}"}
        receipt = {"schema": "agent-ee.stock-models/1", "repository": STOCK_REPOSITORY, "files": {relative: entry}}
        self.assertEqual(parse_stock_receipt(json.dumps(receipt).encode(), "r")["files"], {relative: entry})
        for raw, message in ((b"\xff", "invalid model cache receipt"), (b"[]", "unsupported model cache receipt"),
                             (json.dumps({**receipt, "repository": "https://example.invalid"}).encode(),
                              "unsupported model cache receipt"),
                             (json.dumps({**receipt, "files": {relative: {**entry, "commit": "0" * 40}}}).encode(),
                              "invalid model cache provenance")):
            with self.subTest(raw=raw[:40]), self.assertRaisesRegex(ModelFetchError, message):
                parse_stock_receipt(raw, "r")


if __name__ == "__main__":
    unittest.main()
