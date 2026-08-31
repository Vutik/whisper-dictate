"""Backend implementations.

Every module is imported for its side effect of registering classes. A module
whose dependencies are missing on this platform is skipped rather than fatal,
so the same tree works on machines with different toolchains.
"""
import importlib
import pkgutil

from ..log import log

_FAILED: dict[str, str] = {}


def load_all() -> dict[str, str]:
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:
            _FAILED[info.name] = str(exc)
            log(f"backend module {info.name} unavailable: {exc}")
    return _FAILED


load_all()
