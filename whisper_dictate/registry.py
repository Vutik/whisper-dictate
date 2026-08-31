"""Backend registry and factory.

Backends announce themselves with @register(kind, name). The daemon asks for a
kind by name, or by "auto" — in which case the highest-priority backend whose
is_available() says yes on this machine wins.
"""
from __future__ import annotations

from typing import Any, Iterable

from .log import log

KINDS = ("stt", "audio", "inject", "hotkey", "notify", "sound", "postprocess")

_REGISTRY: dict[str, dict[str, type]] = {k: {} for k in KINDS}


class BackendError(RuntimeError):
    pass


def register(kind: str, name: str, priority: int = 0):
    """Class decorator that adds an implementation to the registry."""
    if kind not in _REGISTRY:
        raise BackendError(f"unknown backend kind {kind!r}; expected one of {KINDS}")

    def decorator(cls):
        cls.backend_name = name
        cls.backend_kind = kind
        cls.backend_priority = priority
        _REGISTRY[kind][name] = cls
        return cls

    return decorator


def names(kind: str) -> list[str]:
    return sorted(_REGISTRY[kind])


def available(kind: str, cfg: dict) -> list[str]:
    return sorted(n for n, c in _REGISTRY[kind].items() if _safe_available(c, cfg))


def _safe_available(cls, cfg: dict) -> bool:
    try:
        return bool(cls.is_available(cfg))
    except Exception as exc:
        log(f"registry: {cls.backend_kind}/{cls.backend_name} availability check failed: {exc}")
        return False


def resolve(kind: str, name: str, cfg: dict) -> type:
    """Return the class for ``name``, or the best available one for "auto"."""
    table = _REGISTRY[kind]
    if not table:
        raise BackendError(f"no {kind} backends registered")

    if name and name != "auto":
        if name not in table:
            raise BackendError(
                f"unknown {kind} backend {name!r}; registered: {', '.join(names(kind))}")
        return table[name]

    candidates = [c for c in table.values() if _safe_available(c, cfg)]
    if not candidates:
        raise BackendError(
            f"no {kind} backend is usable here; registered: {', '.join(names(kind))}")
    return max(candidates, key=lambda c: c.backend_priority)


def create(kind: str, cfg: dict, *args: Any, **kwargs: Any):
    """Instantiate the configured backend of ``kind``."""
    requested = (cfg.get("backends") or {}).get(kind, "auto")
    cls = resolve(kind, requested, cfg)
    log(f"backend {kind}: {cls.backend_name}"
        + ("" if requested not in ("", "auto") else " (auto)"))
    return cls(cfg, *args, **kwargs)


def report(cfg: dict) -> str:
    lines = []
    for kind in KINDS:
        rows = []
        for name in names(kind):
            cls = _REGISTRY[kind][name]
            mark = "+" if _safe_available(cls, cfg) else "-"
            rows.append(f"{mark}{name}")
        lines.append(f"  {kind:7} {' '.join(rows) or '(none)'}")
    return "\n".join(lines)
