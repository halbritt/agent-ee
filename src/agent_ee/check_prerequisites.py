"""Callable prerequisites declared by each check kind.

Imports are deferred until execution so registering a kind cannot force a
candidate/checks import cycle. Each callback returns whether execution may
continue, and keeps its policy and evidence with the owning module.
"""


def placement_clear(ctx, prior):
    from .placement_defects import before_check
    return before_check(ctx, required=True)


def staged_placement_clear(ctx, prior):
    from .placement_defects import before_check
    return before_check(ctx, required=False)


def stage_progress(ctx, prior):
    from .stages import before_check
    return before_check(ctx, prior)


def fab_output(ctx, prior):
    from .fab_stage import before_fab_check
    before_fab_check(ctx)
    return True
