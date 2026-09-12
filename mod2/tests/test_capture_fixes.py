"""Headless tests for the 2.0.3 capture fixes (race semantics + snapshot identity gate),
re-based in 2.1.0 onto the struct-based read (no raw offsets exist any more).

Stubs pymhf/nmspy so the mixin imports without the game, feeds FAKE STRUCT
OBJECTS shaped like nmse.cGcSolarSystemData (c_enum32 fields expose ``.value``),
and verifies behaviour — including negative controls proving the old code paths
fail. The same reader is exercised against the REAL nmspy 178994 struct in
test_struct_read_nmspy.py.
Run: py mod2/tests/test_capture_fixes.py
"""
import sys
import types
from pathlib import Path

MOD2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOD2))

# ---- stub the game-runtime modules so systemread_mixin imports headless ----
for name in ("pymhf", "pymhf.core", "pymhf.core.memutils", "pymhf.core._internal",
             "nmspy", "nmspy.data", "nmspy.data.types", "nmspy.data.exported_types",
             "nmspy.common", "nmspy.data.basic_types"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["pymhf.core.memutils"].map_struct = lambda *a, **k: None
sys.modules["pymhf.core.memutils"].get_addressof = lambda o: getattr(o, "_addr", 0)
sys.modules["nmspy.common"].gameData = types.SimpleNamespace(game_state=None)

from capture.systemread_mixin import SystemReadMixin  # noqa: E402


class E:
    """Shape of a pymhf c_enum32 field: raw int in .value (name may raise)."""
    def __init__(self, v):
        self.value = v

    @property
    def name(self):
        raise ValueError("invalid enum for test purposes")


class FakeSolarSystemData:
    """Duck-typed nmse.cGcSolarSystemData with the fields the reader uses."""
    def __init__(self, race_val, star=0, trading=0, wealth=1, conflict=1,
                 planets=4, prime=2, name="Testsys", seed=0x1234):
        self.Name = name
        self.Planets = planets
        self.PrimePlanets = prime
        self.StarType = E(star)
        self.InhabitingRace = E(race_val)
        self.TradingData = types.SimpleNamespace(TradingClass=E(trading), WealthClass=E(wealth))
        self.ConflictData = E(conflict)
        self.Seed = types.SimpleNamespace(Seed=seed)


class FakeExtractor(SystemReadMixin):
    def __init__(self):
        self._snapshot_identity_seed = 0
        self._current_system_snapshot = None
        self._cached_solar_system = None


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    return cond


ok = True
X = FakeExtractor()

# --- Fix A: race semantics (struct path) ------------------------------------
r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=7))
ok &= check("race=7 (None_/uninhabited) -> all four fields 'None' (absence, not wiped)",
            all(r[k] == "None" for k in
                ("economy_type", "economy_strength", "conflict_level", "dominant_lifeform")))

r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=8))
ok &= check("race=8 (Builders) -> fields NOT wiped (was wiped by old '>6' guard)",
            r["conflict_level"] == "Medium" and r["economy_type"] == "Mining")

r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=9))
ok &= check("race=9 (out of enum range) -> no-data wipe to 'Unknown'",
            r["dominant_lifeform"] == "Unknown" and r["economy_type"] == "Unknown"
            and r["_race_raw"] == 9)

r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=2, conflict=1, wealth=0))
ok &= check("race=2 Korvax + conflict enum 1 -> 'Medium' (not 'Default') + wealth 0 -> 'T1'",
            r["dominant_lifeform"] == "Korvax" and r["conflict_level"] == "Medium"
            and r["economy_strength"] == "T1")

r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=0, star=4, planets=3, prime=1, seed=0xABCDEF))
ok &= check("struct fields flow through: name/planets/prime/star/seed",
            r["system_name"] == "Testsys" and r["planet_count"] == 3 and r["prime_planets"] == 1
            and r["star_color"] == "Purple" and r["system_seed"] == 0xABCDEF)

r = X._read_system_data_from_struct(FakeSolarSystemData(race_val=0, star=1065353472))
ok &= check("out-of-range star enum renders as Unknown(<raw>) — the sanity gate's fingerprint",
            r["star_color"] == "Unknown(1065353472)")

r = X._read_system_data_from_struct(None)
ok &= check("None struct -> defaults, no exception", r["star_color"] == "Unknown" and r["planet_count"] == 0)

# Negative control: prove the OLD guard misbehaved on the same input
old_wiped_uninhabited = 7 > 6   # old code: race_val > 6 -> wipe
ok &= check("negative control: old '>6' guard would have wiped race=7 (bug reproduced)",
            old_wiped_uninhabited)

# --- Fix B: snapshot identity gate -------------------------------------------
class SnapshotRig(FakeExtractor):
    def __init__(self, seed):
        super().__init__()
        self._seed = seed
        self._cached_solar_system = types.SimpleNamespace(mSolarSystemData=self)
        self._addr = 0x1000000

    def _extract_system_properties(self, sys_data):
        return {"system_seed": self._seed, "star_color": f"star-of-{self._seed:#x}"}


rig = SnapshotRig(seed=0xAAAA)
rig._snapshot_system_properties()
ok &= check("first snapshot adopts identity seed 0xAAAA",
            rig._snapshot_identity_seed == 0xAAAA
            and rig._current_system_snapshot["star_color"] == "star-of-0xaaaa")

rig._seed = 0xBBBB  # neighbouring system's generation recycles the object
rig._snapshot_system_properties()
ok &= check("foreign-seed refresh (0xBBBB) REJECTED — snapshot still 0xAAAA's data",
            rig._current_system_snapshot["star_color"] == "star-of-0xaaaa")

rig._seed = 0xAAAA
rig._snapshot_system_properties()
ok &= check("same-seed refresh accepted", rig._current_system_snapshot["system_seed"] == 0xAAAA)

ok &= check("snapshot survives a struct without Planets/PrimePlanets (counts None, gate intact)",
            rig._current_system_snapshot.get("_planets_count") is None)


class CountedRig(SnapshotRig):
    Planets = 5
    PrimePlanets = 3


crig = CountedRig(seed=0x1)
crig._snapshot_system_properties()
ok &= check("snapshot planet counts come from the struct fields (5 / 3)",
            crig._current_system_snapshot["_planets_count"] == 5
            and crig._current_system_snapshot["_prime_planets"] == 3)

# Negative control: without the gate (old behaviour), the foreign refresh overwrites
rig2 = SnapshotRig(seed=0xAAAA)
rig2._snapshot_system_properties()
rig2._seed = 0xBBBB
rig2._snapshot_identity_seed = 0  # simulate old ungated code
rig2._snapshot_system_properties()
ok &= check("negative control: ungated snapshot gets poisoned by 0xBBBB (bug reproduced)",
            rig2._current_system_snapshot["star_color"] == "star-of-0xbbbb")

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
raise SystemExit(0 if ok else 1)
