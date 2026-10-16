import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.reviewers import initial_reviews
from agent_ee.storage import write_json


class ReviewerIdentityTests(unittest.TestCase):
    def test_schema_binds_identity_and_unconforming_role_remains_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source").mkdir()
            (root / "source/design.kicad_sch").write_text("saved native source")
            scope = {"schema": "agent-ee.scope/1", "name": "identity", "scope": "protocol test",
                "inputs": [{"path": "source", "at": "project"}]}
            write_json(root / "scope.json", scope)
            run = root / "run"
            run.mkdir()
            manifest = capture(root / "scope.json", run)
            packet = {"candidate_id": manifest["candidate_id"], "run_id": "current-run",
                      "packet_sha256": "current-packet"}
            write_json(run / "packet.json", packet)

            def reviewer(run_dir, role, model, prompt, schema, timeout):
                self.assertEqual(schema["properties"]["role"]["enum"], [role])
                for key in ("candidate_id", "run_id", "packet_sha256"):
                    self.assertEqual(schema["properties"][key]["enum"], [packet[key]])
                response = {"schema": "agent-ee.review/1", **packet,
                    "role": role if role == "circuit" else "descriptive physical reviewer role",
                    "coverage": ["identity protocol"], "limitations": [], "findings": []}
                return {"execution": "ok"}, response

            with patch("agent_ee.reviewers.claude_call", side_effect=reviewer):
                reviews, records = initial_reviews(run, packet, time.monotonic() + 30)
            self.assertEqual([r["role"] for r in reviews], ["circuit"])
            self.assertEqual([r["execution"] for r in records], ["ok", "malformed"])


if __name__ == "__main__":
    unittest.main()
