"""Native synthetic regression for the build's dangling-track policy end to end."""

import hashlib
import json
import os

from agent_ee import placement_job as job
from tests.test_placement_job import Fixture, groups, intent, MM
from tests.toolchain import require_kicad_route


class PackageCopperPolicyTests(Fixture):
    def test_job_forwards_package_rejection_to_final_native_drc(self):
        require_kicad_route()
        import pcbnew

        board = pcbnew.LoadBoard(str(self.board))
        stub = pcbnew.PCB_TRACK(board)
        stub.SetStart(pcbnew.VECTOR2I(MM, MM))
        stub.SetEnd(pcbnew.VECTOR2I(2 * MM, MM))
        stub.SetWidth(250000)
        stub.SetLayer(pcbnew.F_Cu)
        board.Add(stub)
        pcbnew.SaveBoard(str(self.board), board)
        files = dict(
            board=self.board,
            project=self.project,
            intent=self.dir / "intent.json",
            groups=self.dir / "groups.json",
        )
        files["intent"].write_text(json.dumps(intent()))
        files["groups"].write_text(json.dumps(groups()))
        config = dict(
            schema=job.CONFIG_SCHEMA,
            budget_s=180,
            reject_track_dangling=True,
            inputs={
                k: dict(path=str(v), sha256=hashlib.sha256(v.read_bytes()).hexdigest())
                for k, v in files.items()
            },
            search=self.search(2, 10000),
            router=dict(checkout=os.environ["AGENT_EE_KICAD_ROUTE_CHECKOUT"]),
        )
        report = job.run_job(config, self.dir / "job")
        finals = [
            trial["final"]
            for trial in report.get("routing_trials", [])
            if "final" in trial
        ]
        self.assertTrue(finals, report)
        conflicts = [
            c for final in finals for c in final.get("track_dangling_conflicts", [])
        ]
        self.assertTrue(conflicts, finals)
        self.assertTrue(all(c["type"] == "track_dangling" for c in conflicts))
        self.assertTrue(all(final["eligibility"] == "INELIGIBLE" for final in finals))
        self.assertNotEqual(report["status"], "completed")
