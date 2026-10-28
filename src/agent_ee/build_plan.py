"""Build plan admission and the source inventory it declares."""
import os
from pathlib import Path
import stat

from . import requirement_map
from .catalog import strict_json
from .stage_receipts import _relative
from .storage import digest_fd

SCHEMA = 'agent-ee.build-plan/1'
TYPED_SCHEMA = 'agent-ee.build-plan/2'


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('build declaration fields differ from ' + ', '.join(required))


def validate_plan(plan):
    _keys(plan, ('schema', 'source', 'requirements', 'parts', 'envelope', 'placement'),
          ('verification_timeout_s', 'judgments'))
    if plan['schema'] not in (SCHEMA, TYPED_SCHEMA) or not isinstance(plan['source'], str) or not plan['source']:
        raise ValueError('expected agent-ee.build-plan/1 or /2 with a source directory')
    typed = plan['schema'] == TYPED_SCHEMA
    _keys(plan['requirements'], ('map', 'record', 'spec', *(['obligations'] if typed else [])))
    if 'judgments' in plan:
        if not typed:
            raise ValueError('judgments require a typed build-plan/2')
        _relative(plan['judgments'], 'judgments directory')
        if plan['judgments'] == '.' or any(
                plan['judgments'] == reserved or plan['judgments'].startswith(reserved + '/')
                or reserved.startswith(plan['judgments'] + '/') for reserved in ('build-generated', requirement_map.PROVENANCE)):
            raise ValueError('judgments directory overlaps the source root or generated namespace')
    _keys(plan['parts'], ('manifest', 'circuit', 'layout'), ('deviations', 'ratings'))
    _keys(plan['placement'], ('intent', 'groups', 'budget_s', 'search', 'router'), ('planes', 'block_contract', 'block_source'))
    if {'block_contract', 'block_source'} <= plan['placement'].keys():
        raise ValueError('block_contract and block_source are mutually exclusive')
    paths = [plan['envelope'], *plan['requirements'].values(), *plan['parts'].values(),
             plan['placement']['intent'], plan['placement']['groups']]
    if 'planes' in plan['placement']:
        paths.append(plan['placement']['planes'])
    for key in ('block_contract', 'block_source'):
        if key in plan['placement']:
            paths.append(plan['placement'][key])
    for path in paths:
        _relative(path, 'build input')
        if 'block_source' in plan['placement'] and (path == 'build-generated/block-contract' or
                path.startswith('build-generated/block-contract/') or
                'build-generated/block-contract'.startswith(path + '/')):
            raise ValueError('build input occupies block source output namespace')
        if 'judgments' in plan and (path == plan['judgments'] or path.startswith(plan['judgments'] + '/')):
            raise ValueError('pre-package build input occupies the judgments directory')
    timeout = plan.get('verification_timeout_s', 3600)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('verification_timeout_s must be an integer from 1 to 86400')
    return plan


def source_inventory(root, plan):
    prefix = plan.get('judgments')
    if prefix is None:
        return requirement_map.input_inventory(root)
    root = Path(root)
    files = {}
    def walk_error(exc):
        raise exc
    # Prune before opening any late-bound file. Hashing and then filtering would
    # still read editable testimony during pre-package stages.
    for current, directories, names, descriptor in os.fwalk(root, follow_symlinks=False, onerror=walk_error):
        relative = Path(current).relative_to(root)
        if len(relative.parts) > 128:
            raise ValueError('build input directory nesting exceeds 128 levels')
        for name in list(directories):
            mode = os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode
            if not stat.S_ISDIR(mode):
                raise ValueError('build inputs contain links or special files')
            if str(relative / name) == prefix:
                directories.remove(name)
        for name in names:
            path = str(relative / name)
            if path == prefix:
                raise ValueError('judgments directory must be a directory')
            if not stat.S_ISREG(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                raise ValueError('build inputs contain links or special files')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('build inputs contain links or special files')
                files[path] = digest_fd(os.dup(fd))
            finally:
                os.close(fd)
    return dict(sorted(files.items()))


def admit_source(plan_path, out):
    """Admit plan and source location without creating a run directory."""
    plan_path, out = Path(plan_path).resolve(), Path(out).resolve()
    raw = plan_path.read_bytes()
    plan = validate_plan(strict_json(raw))
    source = (plan_path.parent / plan['source']).resolve()
    if out == source or source in out.parents:
        raise ValueError('build output must be outside the source bundle')
    return plan_path, out, raw, plan, source, source_inventory(source, plan)
