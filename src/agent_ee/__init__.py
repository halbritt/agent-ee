"""Reusable electrical engineering review execution and evidence records."""

__version__ = "0.1.0"


def __getattr__(name):
    if name.startswith("acquisition_"):
        sub = name[len("acquisition_"):]
        import importlib
        try:
            mod = importlib.import_module(f".acquisition.{sub}", __name__)
            return mod
        except ModuleNotFoundError:
            pass
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
