"""Validate explicit per-module input selection before a strict-suite child starts."""

import json
from dataclasses import dataclass
from pathlib import Path
import re


SCHEMA = 'agent-ee.module-env/1'
RESERVED = {'AGENT_EE_REQUIRE_TOOLCHAIN', 'AGENT_EE_MODULE_ENV', 'AGENT_EE_RUN_INPUT_RECEIPT',
            'AGENT_EE_EVIDENCE_DIR', 'AGENT_EE_FOME_MC_CHECKOUT', 'AGENT_EE_J1_DRAWING',
            'AGENT_EE_KICAD_ROUTE_CHECKOUT', 'AGENT_EE_KICAD_HAPPY', 'AGENT_EE_RUST_PLANNER'}


class ModuleInputUnavailable(ValueError):
    """One module has unavailable input/output setup; independent modules can still run."""

    def __init__(self, module, variable, detail):
        self.module, self.variable = module, variable
        super().__init__(f'module {module} required input setup {variable}: {detail}')


@dataclass(frozen=True)
class Selection:
    modules: dict
    unavailable: dict


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate module environment field: {key}')
        result[key] = value
    return result


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
    document = json.loads(Path(selected).read_text(), object_pairs_hook=unique_object)
    if (not isinstance(document, dict) or set(document) != {'schema', 'modules'}
            or document['schema'] != SCHEMA or not isinstance(document['modules'], dict)):
        raise ValueError(f'expected {SCHEMA} with schema and modules fields')
    known = set(known_modules)
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
                if (not re.fullmatch('AGENT_EE_[A-Z0-9_]+|PCBSCHEMAGEN_ROOT', variable)
                        or variable in RESERVED or variable in seen):
                    raise ValueError(f'invalid, reserved or repeated module input variable: {variable}')
                if (not isinstance(value, str) or not Path(value).is_absolute()
                        or any(c in value for c in ('\0', '\n', '\r'))):
                    raise ValueError(f'module {name} variable {variable} needs an absolute path')
                seen.add(variable)
        check_paths(name, selection, input_root, artifact_root, availability=False)
    unavailable = {}
    for name, selection in document['modules'].items():
        try:
            check_paths(name, selection, input_root, artifact_root)
        except ModuleInputUnavailable as exc:
            unavailable[name] = exc
    return Selection(document['modules'], unavailable)


def check_paths(module, selection, input_root, artifact_root, availability=True):
    for variable, value in selection['inputs'].items():
        path = Path(value)
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
    check_paths(module, bindings, input_root, artifact_root)
    result.update(bindings['inputs'])
    result.update(bindings['outputs'])
    return result
