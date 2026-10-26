"""Check-kind registrations and the compatibility views used by scope readers."""

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, replace
from importlib import import_module


@dataclass(frozen=True)
class Registration:
    kind: str
    capability: object
    check: object
    accepts_criterion: bool = False


class Registry(Mapping):
    def __init__(self):
        self._registrations = {}
        self._loaded = False

    def _load(self):
        if not self._loaded:
            # checks imports the built-in check modules after defining native
            # handlers. Delay this until use so candidate/baseline imports finish.
            import_module('.checks', __package__)
            self._loaded = True

    def __getitem__(self, kind):
        self._load()
        return self._registrations[kind]

    def __iter__(self):
        self._load()
        return iter(self._registrations)

    def __len__(self):
        self._load()
        return len(self._registrations)

    def register(self, kind, capability, check, *, accepts_criterion=False):
        if kind in self._registrations:
            raise ValueError(f'check kind already registered: {kind}')
        if not callable(capability) or not callable(check):
            raise TypeError('check kind requires capability and check functions')
        record = Registration(kind, capability, check, accepts_criterion)
        self._registrations[kind] = record
        return record


registry = Registry()
register = registry.register


class CheckFunctions(MutableMapping):
    """Writable check-function projection; patch.dict preserves registrations."""

    def __getitem__(self, kind):
        check = registry[kind].check
        if check is None:
            raise KeyError(kind)
        return check

    def __setitem__(self, kind, check):
        record = registry[kind]
        registry._registrations[kind] = replace(record, check=check)

    def __delitem__(self, kind):
        self[kind]
        registry._registrations[kind] = replace(registry[kind], check=None)

    def __iter__(self):
        return (kind for kind in registry if registry[kind].check is not None)

    def __len__(self):
        return sum(1 for _ in self)


@dataclass
class Description:
    """One capability's captured inputs and descriptor under construction."""

    spec: dict
    check_specs: list
    files: object
    scope: dict
    inputs: object
    result: dict
    captured: object
    prerequisite: object

    @property
    def kind(self):
        return self.spec.get('kind')

    @property
    def params(self):
        return self.spec.get('params', {})

    def apply(self, descriptor):
        for path in descriptor['immutable_inputs']:
            self.captured(path, immutable=True)
        self.result['predicates'] = descriptor['predicates']
