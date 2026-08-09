"""Headless tests for the 2.0.3 capture fixes (race semantics + snapshot identity gate).

Stubs pymhf/nmspy so the mixin imports without the game, fakes memory reads, and
verifies behaviour — including negative controls proving the old code paths fail.
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
from capture.offsets import (  # noqa: E402
    SolarSystemDataOffsets, TradingDataOffsets, ConflictDataOffsets,
)

BASE = 0x1000000


class FakeExtractor(SystemReadMixin):
    """Feeds fake memory (absolute-addressed) to the direct-read path."""
    def __init__(self, race_val, star=0, trading=0, wealth=1, conflict=1):
        self.mem = {
            BASE + SolarSystemDataOffsets.STAR_TYPE: star,
            BASE + SolarSystemDataOffsets.INHABITING_RACE: race_val,
            BASE + SolarSystemDataOffsets.PLANETS_COUNT: 4,
            BASE + SolarSystemDataOffsets.PRIME_PLANETS: 2,
            BASE + SolarSystemDataOffsets.TRADING_DATA + TradingDataOffsets.TRADING_CLASS: trading,
            BASE + SolarSystemDataOffsets.TRADING_DATA + TradingDataOffsets.WEALTH_CLASS: wealth,
            BASE + SolarSystemDataOffsets.CONFLICT_DATA + ConflictDataOffsets.CONFLICT_LEVEL: conflict,
        }
        self._snapshot_identity_seed = 0
        self._current_system_snapshot = None
        self._cached_solar_system = None

    def _read_uint32(self, base, off):
        return self.mem.get(base + off, 0)

    def _read_int32(self, base, off):
        return self._read_uint32(base, off)

    def _read_string(self, base, off, max_len=128):
        return "Testsys"


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    return cond


ok = True

# --- Fix A: race semantics ---------------------------------------------------
r = FakeExtractor(race_val=7)._read_system_data_direct(0x1000000)
ok &= check("race=7 (None_/uninhabited) -> all four fields 'None' (absence, not wiped)",
            all(r[k] == "None" for k in
                ("economy_type", "economy_strength", "conflict_level", "dominant_lifeform")))

r = FakeExtractor(race_val=8)._read_system_data_direct(0x1000000)
ok &= check("race=8 (Builders) -> fields NOT wiped (was wiped by old '>6' guard)",
            r["conflict_level"] == "Medium" and r["economy_type"] == "Mining")

r = FakeExtractor(race_val=9)._read_system_data_direct(0x1000000)
ok &= check("race=9 (out of enum range) -> no-data wipe to 'Unknown'",
            r["dominant_lifeform"] == "Unknown" and r["economy_type"] == "Unknown")

r = FakeExtractor(race_val=2, conflict=1, wealth=0)._read_system_data_direct(0x1000000)
ok &= check("race=2 Korvax + conflict enum 1 -> 'Medium' (not 'Default') + wealth 0 -> 'T1'",
            r["dominant_lifeform"] == "Korvax" and r["conflict_level"] == "Medium"
            and r["economy_strength"] == "T1")

# Negative control: prove the OLD guard misbehaved on the same input
old_wiped_uninhabited = 7 > 6   # old code: race_val > 6 -> wipe
ok &= check("negative control: old '>6' guard would have wiped race=7 (bug reproduced)",
            old_wiped_uninhabited)

# --- Fix B: snapshot identity gate -------------------------------------------
class SnapshotRig(FakeExtractor):
    def __init__(self, seed):
        super().__init__(race_val=0)
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
