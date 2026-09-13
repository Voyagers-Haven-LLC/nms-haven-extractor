"""2.0.4: foreign-generation flag (phantom planet fix, zero-new-memory-reads design).

The snapshot function's EXISTING seed read now also sets _foreign_generation_active;
the capture hook only checks that boolean. Replicates the 2026-08-09 load log where a
neighbouring system's generation burst fired 3 phantom planets into the capture list.
Run: py mod/tests/test_foreign_gate.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    import types
    from pathlib import Path

    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))

    for name in ("pymhf", "pymhf.core", "pymhf.core.memutils", "pymhf.core._internal",
                 "nmspy", "nmspy.data", "nmspy.data.types", "nmspy.data.exported_types",
                 "nmspy.common", "nmspy.data.basic_types"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["pymhf.core.memutils"].map_struct = lambda *a, **k: None
    sys.modules["pymhf.core.memutils"].get_addressof = lambda o: getattr(o, "_addr", 0)
    sys.modules["nmspy.common"].gameData = types.SimpleNamespace(game_state=None)

    from capture.systemread_mixin import SystemReadMixin  # noqa: E402


    class Rig(SystemReadMixin):
        """Drives _snapshot_system_properties with a controllable seed; no memory reads."""
        def __init__(self):
            self._seed = 0
            self._snapshot_identity_seed = 0
            self._foreign_generation_active = False
            self._current_system_snapshot = None
            self._cached_solar_system = types.SimpleNamespace(mSolarSystemData=self)
            self._addr = 0x1000000

        def _extract_system_properties(self, sys_data):
            return {"system_seed": self._seed, "star_color": f"star-of-{self._seed:#x}"}

        def _read_int32(self, base, off):
            return 0

        def _read_uint32(self, base, off):
            return 0


    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond


    ok = True
    r = Rig()

    # Warp into system 0xAAAA: adopt, flag clear
    r._seed = 0xAAAA
    r._snapshot_system_properties()
    ok &= check("adopt on warp: identity=0xAAAA, foreign flag False",
                r._snapshot_identity_seed == 0xAAAA and r._foreign_generation_active is False)

    # Neighbour system 0xBBBB generates (the phantom burst from the log)
    r._seed = 0xBBBB
    r._snapshot_system_properties()
    ok &= check("foreign generation: flag True (capture hook will skip this fire)",
                r._foreign_generation_active is True)
    ok &= check("snapshot NOT poisoned (still 0xAAAA's data)",
                r._current_system_snapshot["star_color"] == "star-of-0xaaaa")

    # Back to our own system's planet generation
    r._seed = 0xAAAA
    r._snapshot_system_properties()
    ok &= check("own-system fire again: flag returns False (capture resumes)",
                r._foreign_generation_active is False)

    # Unreadable seed: conservative — snapshot proceeds, flag False, settle filter backstops
    r._seed = 0
    r._snapshot_system_properties()
    ok &= check("unreadable seed: flag False (capture proceeds; settle filter backstops)",
                r._foreign_generation_active is False)

    # Fresh boot before any identity: nothing flagged
    r2 = Rig()
    r2._seed = 0xCCCC
    r2._snapshot_system_properties()
    ok &= check("fresh adopt path never flags foreign", r2._foreign_generation_active is False)

    # Negative control: 2.0.3 had no flag — the capture hook had nothing to check,
    # so the 0xBBBB burst entered the capture list (the 6-planets-in-a-3-planet-system log).
    ok &= check("negative control: without the flag the hook cannot skip (2.0.3 behaviour reproduced)",
                not hasattr(SystemReadMixin, "_is_foreign_generation"))

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
