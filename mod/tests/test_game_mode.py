"""game_mode / reality / difficulty index from the typed sources — headless.

Stubs pymhf/nmspy like test_capture_fixes.py, feeds a fake cGcPlayerStateData
(DifficultyState.Preset + Settings.GroundCombatTimers) through the hook-side
reader and a fake cGcApplication.meGameMode through the live reader.
Run: py mod/tests/test_game_mode.py
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
    STRUCTS = {}
    sys.modules["pymhf.core.memutils"].map_struct = lambda addr, cls=None: STRUCTS[addr]
    sys.modules["pymhf.core.memutils"].get_addressof = lambda o: getattr(o, "_addr", 0)
    game = types.SimpleNamespace(game_state=None, GcApplication=None)
    sys.modules["nmspy.common"].gameData = game
    sys.modules["nmspy.data.exported_types"].cGcPlayerStateData = object

    from capture.systemread_mixin import SystemReadMixin  # noqa: E402

    class E:
        def __init__(self, v):
            self.value = v

    def state_data(addr, preset, timers):
        sd = types.SimpleNamespace(DifficultyState=types.SimpleNamespace(
            Preset=E(preset), Settings=types.SimpleNamespace(GroundCombatTimers=E(timers))))
        STRUCTS[addr] = sd
        return types.SimpleNamespace(_addr=addr)

    class Rig(SystemReadMixin):
        def __init__(self):
            self._game_mode = ""
            self._game_mode_preset = ""
            self._app_game_mode = ""
            self._combat_timer_index = None

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True

    # nothing read yet: unknown, not Normal
    r = Rig()
    ok &= check("before any read: game_mode '' (unknown), reality Normal, index falls back to 2",
                r._detect_game_mode() == "" and r._reality() == "Normal" and r._get_difficulty_index() == 2)

    # live application mode alone (no save-data hook yet)
    game.GcApplication = types.SimpleNamespace(meGameMode=5)   # cGcGameMode.Permadeath
    ok &= check("meGameMode=Permadeath -> game_mode Permadeath, reality Permadeath",
                r._detect_game_mode() == "Permadeath" and r._reality() == "Permadeath")
    game.GcApplication = types.SimpleNamespace(meGameMode=2)   # Creative
    ok &= check("meGameMode=Creative -> game_mode Creative, index fallback 0",
                r._detect_game_mode() == "Creative" and r._get_difficulty_index() == 0)
    game.GcApplication = types.SimpleNamespace(meGameMode=6)   # Seasonal: not a preset name
    r2 = Rig()
    ok &= check("meGameMode=Seasonal alone -> game_mode stays '' (no preset word for it)",
                r2._detect_game_mode() == "" and r2._app_game_mode == "Seasonal")

    # save-data hook: Custom preset with Fast combat timers
    ok &= check("LoadFromData: Custom preset + timers Fast(3) read",
                r._note_difficulty_state(state_data(0x1000, 1, 3), "LoadFromData") is True
                and r._game_mode_preset == "Custom" and r._combat_timer_index == 3)
    game.GcApplication = types.SimpleNamespace(meGameMode=1)   # Normal
    ok &= check("preset wins over app mode for game_mode; index is the typed timers value (3), not the Custom fallback (2)",
                r._detect_game_mode() == "Custom" and r._get_difficulty_index() == 3)
    ok &= check("Custom preset on a Normal save -> reality Normal", r._reality() == "Normal")

    # Permadeath save with Custom preset stays Permadeath
    game.GcApplication = types.SimpleNamespace(meGameMode=5)
    r._detect_game_mode()
    ok &= check("Custom preset on a Permadeath save -> reality Permadeath", r._reality() == "Permadeath")

    # Invalid / out-of-range presets are ignored, previous values kept
    ok &= check("Invalid preset ignored", r._note_difficulty_state(state_data(0x2000, 0, 2), "SaveToData") is False
                and r._game_mode_preset == "Custom")
    ok &= check("garbage preset ignored", r._note_difficulty_state(state_data(0x3000, 1065353472, 9), "SaveToData") is False
                and r._combat_timer_index == 3)
    # SaveToData mid-session change: Relaxed + Slow
    ok &= check("SaveToData: Relaxed + Slow(1) updates both",
                r._note_difficulty_state(state_data(0x4000, 4, 1), "SaveToData") is True
                and r._detect_game_mode() == "Relaxed" and r._get_difficulty_index() == 1)
    # NULL pointer
    ok &= check("null pointer -> False, nothing changes",
                r._note_difficulty_state(types.SimpleNamespace(_addr=0), "x") is False and r._game_mode_preset == "Relaxed")
    # unreadable app mode never raises
    game.GcApplication = None
    ok &= check("GcApplication None -> app mode '' kept from before, no exception",
                r._read_app_game_mode() == "" and r._detect_game_mode() == "Relaxed")

    # negative control: the OLD code hard-coded reality from game_mode only
    old_reality = "Permadeath" if r._game_mode == "Permadeath" else "Normal"
    ok &= check("negative control: old rule says Normal for a Permadeath save under a Relaxed preset",
                old_reality == "Normal" and r._reality() == "Permadeath")

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
