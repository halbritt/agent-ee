"""Retain explicit diagnostic stage inputs through the ordinary record interface."""
from agent_ee import requirement_map, retained_run


def requirements(root, inputs, scope, accounting=None):
    root.mkdir(parents=True, exist_ok=True)
    inventory = requirement_map.input_inventory(inputs)
    pin = dict(plan_sha256='a' * 64, source=inventory, runtime=dict(fixture='diagnostic stage input'))
    report = retained_run.initialize(root, pin)
    retained_run.capture_source(root, inputs, {}, inventory)
    first = retained_run.begin_stage(root, 'requirements')
    candidate = first.capture(inputs)
    retained_run.write_scope(first.directory, scope)
    if accounting is not None:
        retained_run.write_accounting(first.directory, accounting)
    retained_run.record_completion(root, report, first, candidate)
    report['status'] = 'checkpoint'
    retained_run.write_report(root, report)
    return retained_run.begin_stage(root, 'artwork' if accounting is None else 'placement-routing')
