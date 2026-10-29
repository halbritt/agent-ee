"""Versioned topology-only test derivation; the archived CAN capture stays untouched."""
import copy
from pathlib import Path
import shutil
from agent_ee.reference_blocks import block_contract
from agent_ee.storage import digest,read_json,write_json

VERSION='1-topology-regression-2'
LINEAGE='lineage/pod-can-standalone.json'

def derive_topology_fixture(archived,destination):
    archived,destination=Path(archived),Path(destination)
    parent=read_json(archived/'block.json')
    pending=parent['pending_requirements']
    if len(pending)!=1 or pending[0]['id']!='pod-can-standalone' or pending[0]['blocked_by'] != (
            'block_adaptation supports only one source instance; no selected-reference extraction/composition producer or multi-instance provenance binding is available.'):
        raise ValueError('unknown pending CAN obligation; no implicit fixture waiver')
    shutil.copytree(archived,destination)
    record=copy.deepcopy(parent)
    disposition=dict(schema='agent-ee.test-fixture-disposition/1',version=VERSION,
        parent_block_sha256=digest(archived/'block.json'),parent_contract_sha256=digest(archived/'block-contract.json'),
        original_pending=pending[0],scope='Software topology regression only; not a qualified reference or electrical/supplier/physical acceptance',
        disposition='The historical producer-capability blocker is exercised by existing selected-reference composition and two-source native provenance; the original standalone obligation remains required by the joint fresh interface and provenance acceptance controls.',
        required=dict(parts=['C308','D4','R9','U307'],ports=['+3V3','+5V','CAN+','CAN-','GND','RXD','S','TXD'],
            alias='S=GND',checks=['interface','provenance'],intent='acceptance',both_original_source_exports=True),
        predicates='Preserve all original flat/hierarchical topology, ERC, determinism, tamper and native defect assertions. No sole netlist/provenance discharge.',
        limitations='Original unrelated primary-sheet ports describe archived whole-sheet inspection, not the selected four-part output. Current limits, independent source review, supplier/physical and containing-board qualification remain unverified.')
    write_json(destination/LINEAGE,disposition)
    record['version']=VERSION;record['pending_requirements']=[]
    record['files'][LINEAGE]=digest(destination/LINEAGE)
    interface=next(r for r in record['requirements'] if r['id']=='pod-can-interface')
    interface.setdefault('conditions',[]).append('Topology fixture standalone obligation requires joint fresh interface and generated two-source provenance acceptance; see '+LINEAGE+'. No electrical, supplier or physical qualification.')
    write_json(destination/'block-contract.json',block_contract(record));record['files']['block-contract.json']=digest(destination/'block-contract.json')
    write_json(destination/'block.json',record)
    return record

def assert_topology_acceptance(root,scope):
    record=read_json(Path(root)/'block/block.json')
    if record['version']!=VERSION:
        raise ValueError('explicit topology regression version required')
    kinds={c['id']:c['kind'] for c in scope['checks']}
    requirements=scope['baseline']['requirements']
    if kinds.get('interface')!='hardware_interface' or kinds.get('provenance')!='block_provenance':
        raise ValueError('standalone fixture needs both real interface and provenance checks')
    for cid in ('interface','provenance'):
        if not any(r.get('intent')=='acceptance' and cid in r['checks'] for r in requirements):
            raise ValueError('standalone fixture must preserve both fresh acceptance obligations')
