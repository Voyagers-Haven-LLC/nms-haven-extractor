"""Readiness gate (readiness.py) against a fake pyMHF registry.

Reproduces the June-16 / Cosmos-day failure: a required hook whose pattern did
not resolve is NOT in hook_manager.hooks and NOT in failed_hooks either. The old
gate (failed_hooks-based) said READY; the new one must say BROKEN.
Run: py mod2/tests/test_readiness_gate.py
"""
import sys
import types
from pathlib import Path

MOD2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOD2))

from readiness import registry_names, unbound_required_hooks  # noqa: E402

REQUIRED = ("cTkLanguageManagerBase.Translate", "cGcSolarSystem.Generate",
            "cGcPlanetGenerator.GenerateCreatureRoles")


class FunctionIdentifier:
    """Shape of pymhf.core.hooking.FunctionIdentifier (hashable, .name)."""
    def __init__(self, name, offset=0x1000):
        self.name = name
        self.offset = offset

    def __hash__(self):
        return hash((self.name, self.offset))

    def __eq__(self, other):
        return (self.name, self.offset) == (other.name, other.offset)


def fake_manager(bound_names):
    hm = types.SimpleNamespace()
    hm.hooks = {FunctionIdentifier(n): object() for n in bound_names}
    hm.failed_hooks = {}   # pyMHF leaves this EMPTY for an unresolved pattern
    return hm


def old_gate_failed(hm):
    """The 2.0.0-2.0.4 check, verbatim logic: only looks at failed_hooks."""
    failed_names = set(getattr(hm, "failed_hooks", {}) or {})
    return [n for n in REQUIRED if any(n in f for f in failed_names)]


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    return cond


ok = True

hm = fake_manager(REQUIRED + ("cGcSolarSystem.Update", "cGcPlanet.Generate"))
names = registry_names(hm)
ok &= check("all three required hooks registered -> nothing unbound",
            unbound_required_hooks(names, REQUIRED) == [])

hm = fake_manager(("cTkLanguageManagerBase.Translate", "cGcPlanetGenerator.GenerateCreatureRoles"))
names = registry_names(hm)
ok &= check("Generate's pattern unresolved -> reported unbound",
            unbound_required_hooks(names, REQUIRED) == ["cGcSolarSystem.Generate"])
ok &= check("negative control: the OLD failed_hooks gate saw nothing wrong (bug reproduced)",
            old_gate_failed(hm) == [])

hm = fake_manager(("cGcSolarSystem.Generate(overloadA)", "cTkLanguageManagerBase.Translate",
                   "cGcPlanetGenerator.GenerateCreatureRoles"))
ok &= check("overload suffix '(...)' is stripped when matching",
            unbound_required_hooks(registry_names(hm), REQUIRED) == [])

ok &= check("empty registry -> every required hook unbound",
            unbound_required_hooks(registry_names(fake_manager(())), REQUIRED) == list(REQUIRED))

ok &= check("unreadable registry -> None (caller falls back to its watchdog, never asserts READY from it)",
            registry_names(types.SimpleNamespace()) is None
            and unbound_required_hooks(None, REQUIRED) is None)

# FunctionIdentifier without .name falls back to str()
class NamelessId:
    def __init__(self, s): self.s = s
    def __str__(self): return self.s
    def __hash__(self): return hash(self.s)
    def __eq__(self, o): return self.s == o.s

hm = types.SimpleNamespace(hooks={NamelessId(n): 1 for n in REQUIRED}, failed_hooks={})
ok &= check("identifiers without .name are matched by str()",
            unbound_required_hooks(registry_names(hm), REQUIRED) == [])

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
raise SystemExit(0 if ok else 1)
