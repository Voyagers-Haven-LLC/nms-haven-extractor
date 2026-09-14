"""Every display table mirrors the nmspy enum it claims to (capture/offsets.py
ENUM_SOURCES) — key sets must match the pinned build EXACTLY, so a member the game
adds fails here instead of shipping as Unknown(<raw>) in someone's upload.

Needs the pinned framework: PYTEST_VERSION=1 dist/python/python.exe mod/tests/test_enum_tables.py
Skips cleanly without nmspy.
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("PYTEST_VERSION", "1")
    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))

    try:
        import nmspy
        from nmspy.data import enums as E
    except Exception as e:  # pragma: no cover
        print(f"SKIP: nmspy not importable here ({e.__class__.__name__}: {e})")
        raise SystemExit(0)

    import capture.offsets as offsets
    from nmspy_pin import NMSPY_PIN
    if nmspy.__version__ != NMSPY_PIN:
        print(f"SKIP: nmspy {nmspy.__version__} here, pin is {NMSPY_PIN}")
        raise SystemExit(0)

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    for table_name, enum_name in offsets.ENUM_SOURCES.items():
        table = getattr(offsets, table_name)
        enum = getattr(E, enum_name, None)
        if enum is None:
            ok &= check(f"{enum_name} exists in nmspy.data.enums", False)
            continue
        want = {int(m.value) for m in enum}
        have = set(table)
        ok &= check(f"{table_name} keys == {enum_name} values ({len(want)} members)", have == want) \
            or check(f"   missing {sorted(want - have)} extra {sorted(have - want)}", False)

    ok &= check("ALIEN_RACES[6] is Exotics (enum member name)", offsets.ALIEN_RACES[6] == "Exotics"
                and E.cGcAlienRace(6).name == "Exotics")
    ok &= check("ALIEN_RACES[8] is Autophage (enum member Builders)", offsets.ALIEN_RACES[8] == "Autophage"
                and E.cGcAlienRace(8).name == "Builders")
    ok &= check("SENTINEL_CORRUPT is the Corrupt member", E.cGcPlanetSentinelLevel(offsets.SENTINEL_CORRUPT).name == "Corrupt"
                and offsets.SENTINEL_LEVELS[offsets.SENTINEL_CORRUPT] == "Corrupted")
    ok &= check("GAME_MODE_PRESETS labels are the enum member names",
                all(E.cGcDifficultyPresetType(k).name == v for k, v in offsets.GAME_MODE_PRESETS.items()))
    ok &= check("APP_GAME_MODES labels are the enum member names",
                all(E.cGcGameMode(k).name == v for k, v in offsets.APP_GAME_MODES.items()))
    # the per-difficulty arrays have 4 slots; the combat-timer option has 4 members
    from nmspy.data import exported_types as nmse
    slots = len(nmse.cGcPlanetInfo().SentinelsPerDifficulty)
    ok &= check(f"SentinelsPerDifficulty has {slots} slots == cGcCombatTimerDifficultyOption members",
                slots == len(list(E.cGcCombatTimerDifficultyOption)) == 4)
    ok &= check("cGcApplication.meGameMode is a typed field (no literal offset in our code)",
                hasattr(__import__("nmspy.data.types", fromlist=["x"]).cGcApplication, "meGameMode"))
    ok &= check("cGcPlayerStateData.DifficultyState.Preset / Settings.GroundCombatTimers exist",
                hasattr(nmse.cGcDifficultyStateData, "Preset")
                and hasattr(nmse.cGcDifficultySettingsData, "GroundCombatTimers"))

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
