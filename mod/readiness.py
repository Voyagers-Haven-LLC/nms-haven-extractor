"""Readiness helpers (2.1.0) — pure functions the gate in haven_extractor2.py
calls, split out so they can be tested against a fake pyMHF registry.

Why membership, not ``failed_hooks``:
    pyMHF fills ``hook_manager.failed_hooks`` only when *constructing* a
    FuncHook raises. The failure a game update actually produces — the byte
    pattern no longer matches — is handled earlier in ``register_hook``: it
    logs ``Unable to find offset for <name>. Hook will not be registered.`` and
    returns. Nothing is recorded anywhere. The 2.0.0-2.0.4 gate read
    ``failed_hooks``, so it reported READY with dead hooks (June 16 2026, and
    again on Cosmos day). A FunctionIdentifier only enters
    ``hook_manager.hooks`` once its offset resolved, so membership there is
    the truth.
"""

from typing import Iterable, List, Optional, Set


def registry_names(hook_manager) -> Optional[Set[str]]:
    """Qualified names ("cGcSolarSystem.Generate") of every hook pyMHF actually
    registered. None when the registry cannot be read at all."""
    try:
        hooks = getattr(hook_manager, "hooks", None)
        if hooks is None:
            return None
        names: Set[str] = set()
        for fid in list(hooks.keys()):
            name = getattr(fid, "name", None)
            if name is None:
                name = str(fid)
            # overloads are registered as "Class.Func(overload)" — strip that
            names.add(str(name).split("(")[0])
        return names
    except Exception:
        return None


def unbound_required_hooks(names: Optional[Set[str]], required: Iterable[str]) -> Optional[List[str]]:
    """Required hooks missing from the registry. None == registry unreadable
    (the caller cannot disprove binding and must fall back to its watchdog)."""
    if names is None:
        return None
    return [name for name in required if name not in names]
