"""Strict package admission must refuse before runtime work or retained writes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import build_plan, build_workflow, requirement_map
from test_build_plan import declaration


def source_plan(root, *, typed=True, strict=True):
    plan = declaration()
    if typed:
        plan['schema'] = build_plan.TYPED_SCHEMA
        plan['requirements']['obligations'] = 'obligations.json'
    if strict:
        plan['placement']['block_contract'] = 'contract.json'
    source = root / 'inputs'
    source.mkdir()
    for name, content in (('intent.json', {}), ('groups.json', {'groups': []}),
                          ('map.json', {'schema': requirement_map.SCHEMA_2 if typed else requirement_map.SCHEMA}),
                          ('contract.json', {})):
        (source / name).write_text(json.dumps(content))
    path = root / 'plan.json'
    path.write_text(json.dumps(plan))
    return path


class StrictBuildPreflightTests(unittest.TestCase):
    def test_default_package_refuses_fresh_and_resume_without_runtime_or_writes(self):
        for typed in (False, True):
            with self.subTest(typed=typed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                plan = source_plan(root, typed=typed)
                out = root / 'build'
                # Native runtime is an external boundary. Any probe here is already too late.
                with patch.object(build_workflow, '_runtime', side_effect=AssertionError('runtime probed')):
                    with self.assertRaisesRegex(ValueError, 'strict block contract package acceptance requires final artwork revalidation'):
                        build_workflow.run(plan, out)
                    self.assertFalse(out.exists())
                    out.mkdir()
                    (out / 'retained-sentinel').write_bytes(b'unchanged retained build')
                    before = {p.name: p.read_bytes() for p in out.iterdir()}
                    with self.assertRaisesRegex(ValueError, 'strict block contract package acceptance requires final artwork revalidation'):
                        build_workflow.run(plan, out, resume=True)
                    self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, before)

    def test_legacy_package_and_strict_diagnostic_keep_the_runtime_boundary(self):
        for strict, target in ((False, 'package'), (True, 'artwork')):
            with self.subTest(strict=strict, target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                plan = source_plan(root, strict=strict)
                with patch.object(build_workflow, '_runtime',
                                  side_effect=RuntimeError('native runtime boundary reached')):
                    with self.assertRaisesRegex(RuntimeError, 'native runtime boundary reached'):
                        build_workflow.run(plan, root / 'build', through=target)
                self.assertFalse((root / 'build').exists())
