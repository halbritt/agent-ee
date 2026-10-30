"""Admission boundary for generated rule areas, before protected-node filtering."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import placement_job as job, acquisition_local_routing as routing
from agent_ee.storage import digest


class LocalRoutingAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        source=self.root/'source';source.mkdir()
        (source/'board.kicad_pcb').write_bytes(b'immutable placed board')
        (source/'board.kicad_pro').write_bytes(b'{}')
        self.board=self.root/'routed.kicad_pcb';self.board.write_bytes(b'new routed board')
        self.placed=dict(directory=str(source),board=str(source/'board.kicad_pcb'),
                         board_sha256=digest(source/'board.kicad_pcb'))
        self.normalized={'mechanics_binding':{'binding_sha256':'a'*64}}
        self.record=dict(inputs=routing.local._identity(routing.local._capture(source)),
                         mechanics_binding_sha256='a'*64,planes_raw='{}')
        self.verify=self.enterContext(patch.object(routing,'verify',return_value=dict(
            status='pass',board_sha256=digest(self.board),owned_zone_uuids=['verified-area'])))

    def check(self,record=None):
        return job._verify_local_routing(self.board,self.placed,self.normalized,b'{}',
            self.record if record is None else record,clock=job.Clock(10),cancel_dir=None)

    def test_only_bound_native_verification_supplies_owned_zone_ids(self):
        result=self.check()
        self.assertEqual(result['owned_zone_uuids'],['verified-area'])
        self.assertEqual(self.verify.call_args.args,(self.board,self.record))

    def test_changed_placement_planes_or_mechanics_refuse_before_verification(self):
        for field in ('placement','project','planes','mechanics'):
            with self.subTest(field=field):
                record=copy.deepcopy(self.record)
                if field=='placement':record['inputs']['board.kicad_pcb']='b'*64
                elif field=='project':record['inputs']['board.kicad_pro']='b'*64
                elif field=='planes':record['planes_raw']='changed'
                else:record['mechanics_binding_sha256']='b'*64
                with self.assertRaises(job.JobRefusal):self.check(record)
                self.verify.assert_not_called()

    def test_verification_bound_to_another_board_never_grants_ownership(self):
        self.verify.return_value['board_sha256']='b'*64
        with self.assertRaises(job.JobRefusal):self.check()

    def test_missing_mechanics_binding_cannot_match_an_unbound_record(self):
        self.normalized.clear();self.record.pop('mechanics_binding_sha256')
        with self.assertRaises(job.JobRefusal):self.check()
        self.verify.assert_not_called()

    def test_native_refusal_cannot_fall_back_to_an_unverified_zone_list(self):
        self.verify.side_effect=job.JobRefusal('native reservation changed')
        with self.assertRaisesRegex(job.JobRefusal,'native reservation changed'):self.check()
