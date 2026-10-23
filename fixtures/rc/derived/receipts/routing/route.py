import json, os, sys
from pathlib import Path
from agent_ee import candidate, routing_adapter, stage_receipts
root = Path(sys.argv[1]); root.mkdir(parents=True)
(root / 'captured').mkdir()
candidate.capture('fixtures/rc/derived/placed-unrouted.scope.json', root / 'captured')
spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
    name='rc_filter', label='ee103', checkout=os.environ['AGENT_EE_KICAD_ROUTE_CHECKOUT'], budget=60)
receipt = stage_receipts.run_step(root / 'receipt', root / 'captured/inputs', spec, {'implementation': 1, 'receipt_index': 1})
assert stage_receipts.validate_receipt(root / 'receipt') == receipt
summary = routing_adapter.verify(root / 'receipt/run', receipt=receipt)
(root / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
print(json.dumps(summary, indent=1)[:3000])
