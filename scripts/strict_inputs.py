"""Validate explicit per-module input selection before a strict-suite child starts."""

import json
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


SCHEMA = 'agent-ee.module-env/1'
POLICY_BYTES = Path(__file__).with_name('native-input-policy.json').read_bytes()
POLICY = json.loads(POLICY_BYTES)


class ModuleInputUnavailable(ValueError):
    """One module has unavailable input/output setup; independent modules can still run."""

    def __init__(self, module, variable, detail):
        self.module, self.variable = module, variable
        super().__init__(f'module {module} required input setup {variable}: {detail}')


@dataclass(frozen=True)
class Selection:
    modules: dict
    unavailable: dict
    historical: dict = field(default_factory=dict)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate module environment field: {key}')
        result[key] = value
    return result


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular_inventory(root):
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError(f'historical input uses a symlink: {root}')
    result, pending = {}, [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.is_symlink():
                raise ValueError(f'historical input contains a symlink: {path}')
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = file_sha(path)
            else:
                raise ValueError(f'historical input contains a special entry: {path}')
    return result


def historical_bindings(base_env, raw_selection, modules, input_root):
    """A fixed policy alias is usable only through this run's readonly input receipt."""
    result = {}
    for module, selection in modules.items():
        for variable, value in selection['inputs'].items():
            if Path(value).is_relative_to(input_root):
                continue
            layouts = [(name, layout) for name, layout in POLICY.get('layouts', {}).items()
                       if layout['env'] == variable and layout['at'] == value and module in layout['modules']]
            if len(layouts) != 1:
                continue  # Ordinary confinement check reports unapproved paths.
            name, layout = layouts[0]
            receipt = input_root / 'receipt.json'
            if base_env.get('AGENT_EE_RUN_INPUT_RECEIPT') != str(receipt) or receipt.is_symlink():
                raise ValueError('historical input needs the authoritative confined run receipt')
            raw_receipt = receipt.read_bytes()
            record = json.loads(raw_receipt, object_pairs_hook=unique_object)
            if (not isinstance(record, dict) or record.get('schema') != 'agent-ee.native-inputs/1'
                    or record.get('status') not in ('prepared', 'prepared_with_unavailable_inputs')
                    or record.get('policy_sha256') != hashlib.sha256(POLICY_BYTES).hexdigest()
                    or record.get('module_env_sha256') != hashlib.sha256(raw_selection).hexdigest()
                    or not isinstance(record.get('inputs'), list)):
                raise ValueError('historical input run receipt does not bind this policy and module selection')
            matches = [item for item in record['inputs'] if isinstance(item, dict)
                       and item.get('env') == variable and isinstance(item.get('modules'), list)
                       and module in item['modules']]
            if len(matches) != 1:
                raise ValueError('historical input has no unique module-scoped receipt binding')
            item = matches[0]
            if (not isinstance(item.get('name'), str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', item['name'])
                    or item['name'] in ('receipt', 'module-env') or item.get('layout') != name
                    or item.get('container_path') != value or item.get('read_only') is not True
                    or item.get('select') is not None
                    or item.get('mount_path') != '/opt/agent-ee-inputs/' + item['name']
                    or not isinstance(item.get('modules'), list)
                    or any(not isinstance(module_name, str) for module_name in item['modules'])
                    or not set(item['modules']) <= set(layout['modules'])
                    or not isinstance(item.get('files'), dict) or not item['files']
                    or item['files'].get('model-r1/input-pins.json') != layout['pins_sha256']
                    or item['files'].get('model-r1/execution.json') != layout['execution_sha256']):
                raise ValueError('historical input receipt violates the reviewed layout')
            result[module, variable] = {'path': Path(value), 'canonical': input_root / item['name'],
                'files': item['files'], 'receipt': receipt,
                'receipt_sha256': hashlib.sha256(raw_receipt).hexdigest()}
    return result


def check_historical_view(module, variable, binding):
    if file_sha(binding['receipt']) != binding['receipt_sha256']:
        raise ValueError('historical input receipt changed after selection')
    for root in (binding['path'], binding['canonical']):
        if root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError(f'historical input uses a symlink: {root}')
        if not root.exists():
            raise ModuleInputUnavailable(module, variable, f'historical input is missing: {root}')
        if regular_inventory(root) != binding['files']:
            raise ValueError('historical input differs from the exact receipt inventory')
    root, files = binding['path'], binding['files']
    pins = json.loads((root / 'model-r1/input-pins.json').read_bytes(), object_pairs_hook=unique_object)
    execution = json.loads((root / 'model-r1/execution.json').read_bytes(), object_pairs_hook=unique_object)
    if (execution.get('input_pins_sha256') != files['model-r1/input-pins.json']
            or not isinstance(pins.get('generated_files'), dict) or not pins['generated_files']
            or not isinstance(execution.get('outputs'), dict)):
        raise ValueError('historical input receipt chain is incomplete')
    for absolute, sha in pins['generated_files'].items():
        path = Path(absolute)
        if (not path.is_relative_to(root / 'model-r1/probe') or '..' in path.parts or str(path) != absolute
                or files.get(path.relative_to(root).as_posix()) != sha):
            raise ValueError('historical input generated file is outside or missing from its snapshot')
    for relative, identity in execution['outputs'].items():
        if (not isinstance(identity, dict) or not relative.startswith('probe/') or '..' in Path(relative).parts
                or Path(relative).as_posix() != relative
                or files.get('model-r1/' + relative) != identity.get('sha256')):
            raise ValueError('historical input execution output is outside or missing from its snapshot')


def validate(base_env, known_modules, *, input_root=Path('/opt/agent-ee-inputs'),
             artifact_root=Path('/artifacts')):
    """Validate the whole selection once before dispatch; return its module bindings.

    Explicit policy roots also permit filesystem tests without writing into /opt.
    The strict-suite consumer uses the container defaults.
    """
    selected = base_env.get('AGENT_EE_MODULE_ENV')
    if selected is None:
        return Selection({}, {})
    if not selected or not Path(selected).is_absolute():
        raise ValueError('AGENT_EE_MODULE_ENV must name an absolute manifest path')
    raw_selection = Path(selected).read_bytes()
    document = json.loads(raw_selection, object_pairs_hook=unique_object)
    if (not isinstance(document, dict) or not {'schema', 'modules'} <= set(document) <= {'schema', 'modules', 'unavailable'}
            or document['schema'] != SCHEMA or not isinstance(document['modules'], dict)):
        raise ValueError(f'expected {SCHEMA} with schema and modules fields')
    known = set(known_modules)
    # Bind aliases after validating the map shape below, before any child is dispatched.
    for name, selection in document['modules'].items():
        if name not in known:
            raise ValueError(f'unknown module in input manifest: {name}')
        if not isinstance(selection, dict) or set(selection) != {'inputs', 'outputs'}:
            raise ValueError(f'module {name} needs inputs and outputs maps')
        seen = set()
        for kind in ('inputs', 'outputs'):
            bindings = selection[kind]
            if not isinstance(bindings, dict):
                raise ValueError(f'module {name} {kind} must be a variable-to-path map')
            for variable, value in bindings.items():
                if variable not in POLICY[kind] or variable in seen:
                    raise ValueError(f'undeclared or repeated module input variable: {variable}')
                if (not isinstance(value, str) or not Path(value).is_absolute()
                        or any(c in value for c in ('\0', '\n', '\r'))):
                    raise ValueError(f'module {name} variable {variable} needs an absolute path')
                seen.add(variable)
    historical = historical_bindings(base_env, raw_selection, document['modules'], input_root)
    for name, selection in document['modules'].items():
        check_paths(name, selection, input_root, artifact_root, availability=False, historical=historical)
    declared = document.get('unavailable', {})
    if not isinstance(declared, dict):
        raise ValueError('module unavailable declarations must be a map')
    unavailable = {}
    for name, errors in declared.items():
        if name not in document['modules'] or not isinstance(errors, dict) or not errors:
            raise ValueError(f'invalid unavailable module declaration: {name}')
        for variable, detail in errors.items():
            if variable not in document['modules'][name]['inputs'] or not isinstance(detail, str) or not detail:
                raise ValueError(f'invalid unavailable input declaration: {name}/{variable}')
            unavailable.setdefault(name, ModuleInputUnavailable(name, variable, detail))
    for name, selection in document['modules'].items():
        try:
            check_paths(name, selection, input_root, artifact_root, historical=historical)
        except ModuleInputUnavailable as exc:
            unavailable.setdefault(name, exc)
    return Selection(document['modules'], unavailable, historical)


def check_paths(module, selection, input_root, artifact_root, availability=True, historical=None):
    for variable, value in selection['inputs'].items():
        path = Path(value)
        binding = (historical or {}).get((module, variable))
        if binding is not None:
            if path != binding['path']:
                raise ValueError('historical input selection changed after validation')
            if availability:
                check_historical_view(module, variable, binding)
            continue
        if '..' in path.parts or not path.resolve().is_relative_to(input_root.resolve()):
            raise ValueError(f'module {module} input {variable} must stay under {input_root}')
        if availability and not path.is_file() and not path.is_dir():
            raise ModuleInputUnavailable(module, variable, f'input is missing: {value}')
    output_root = artifact_root.resolve() / 'modules' / module
    for variable, value in selection['outputs'].items():
        path = Path(value)
        if '..' in path.parts or not path.resolve().is_relative_to(output_root) or path.resolve() == output_root:
            raise ValueError(f'module {module} output {variable} must stay under {output_root}')
        if availability:
            if path.exists() or path.is_symlink():
                raise ModuleInputUnavailable(module, variable, f'output must start absent: {value}')
            if not path.parent.is_dir():
                raise ModuleInputUnavailable(module, variable, f'output parent is missing: {path.parent}')


def for_module(module, base_env, known_modules, *, selection=None,
               input_root=Path('/opt/agent-ee-inputs'), artifact_root=Path('/artifacts')):
    """Copy the parent env and apply only this module's prevalidated bindings.

    Child path checks catch disappearance or a reused output leaf after dispatch.
    Pass the suite-start selection to avoid rechecking completed siblings' outputs.
    """
    if module not in known_modules:
        raise ValueError(f'unknown strict-suite module: {module}')
    result = dict(base_env)
    if selection is None:
        selection = validate(base_env, known_modules, input_root=input_root, artifact_root=artifact_root)
    if module in selection.unavailable:
        raise selection.unavailable[module]
    bindings = selection.modules.get(module, {'inputs': {}, 'outputs': {}})
    check_paths(module, bindings, input_root, artifact_root, historical=selection.historical)
    result.update(bindings['inputs'])
    result.update(bindings['outputs'])
    return result
