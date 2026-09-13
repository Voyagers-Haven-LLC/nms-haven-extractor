"""The struct reader against the REAL nmspy generated struct (no stubs).

Builds a raw cGcSolarSystemData buffer at the field offsets nmspy publishes for
the pinned build, maps it with the real ctypes class, and runs the production
reader over it. This is the headless proof that the port reads the framework's
layout correctly — the in-game gate (capture a known system, diff vs prod) is the
other half.

Needs the pinned framework importable (pymhf prompts at import unless
PYTEST_VERSION is set — we set it). Skips cleanly when nmspy is absent so the
plain `py` runner stays green; run it for real with the pinned interpreter:
    PYTEST_VERSION=1 <python-with-nmspy-178994> mod/tests/test_struct_read_nmspy.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import ctypes
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("PYTEST_VERSION", "1")   # silences pymhf's import-time prompts

    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))

    try:
        import nmspy
        import nmspy.data.exported_types as nmse
    except Exception as e:  # pragma: no cover - environment-dependent
        print(f"SKIP: nmspy not importable here ({e.__class__.__name__}: {e})")
        raise SystemExit(0)

    from capture.systemread_mixin import SystemReadMixin  # noqa: E402
    from nmspy_pin import NMSPY_PIN  # noqa: E402

    if nmspy.__version__ != NMSPY_PIN:
        # This is a pinned-environment test: the field layout (and even the field
        # shapes — e.g. PlanetGenerationInputs is not an array in 156355) is only
        # asserted for the build the mod is verified against. In production the
        # readiness gate holds uploads on exactly this mismatch.
        print(f"SKIP: nmspy {nmspy.__version__} installed here, pin is {NMSPY_PIN} — "
              f"run with the pinned interpreter")
        raise SystemExit(0)


    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond


    class Rig(SystemReadMixin):
        def __init__(self):
            self._snapshot_identity_seed = 0
            self._current_system_snapshot = None
            self._cached_solar_system = None


    S = nmse.cGcSolarSystemData
    buf = bytearray(ctypes.sizeof(S))


    def put(off, ctype, val):
        ctypes.memmove(ctypes.addressof(ctypes.c_char.from_buffer(buf, off)),
                       ctypes.byref(ctype(val)), ctypes.sizeof(ctype))


    put(S.Planets.offset, ctypes.c_int32, 4)
    put(S.PrimePlanets.offset, ctypes.c_int32, 2)
    put(S.StarType.offset, ctypes.c_uint32, 2)                 # Blue
    put(S.InhabitingRace.offset, ctypes.c_uint32, 6)           # Exotics
    put(S.TradingData.offset + 0, ctypes.c_uint32, 1)          # HighTech -> "Technology"
    put(S.TradingData.offset + 4, ctypes.c_uint32, 3)          # Pirate -> "T4"
    put(S.ConflictData.offset, ctypes.c_uint32, 1)             # Default -> "Medium"
    put(S.Seed.offset, ctypes.c_int64, 0xABCDEF)
    put(S.PlanetGenerationInputs.offset + nmse.cGcPlanetGenerationInputData.RealityIndex.offset,
        ctypes.c_int32, 255)                                   # Odyalutai in slot 0
    name = b"Struct Test\x00"
    buf[S.Name.offset:S.Name.offset + len(name)] = name

    sd = S.from_buffer(buf)
    r = Rig()._read_system_data_from_struct(sd)

    ok = True
    print(f"nmspy {nmspy.__version__}  (pin {NMSPY_PIN})  sizeof(cGcSolarSystemData)={ctypes.sizeof(S):#x}")
    ok &= check("installed nmspy == pin (this interpreter is the verified environment)",
                nmspy.__version__ == NMSPY_PIN)
    ok &= check("name read through cTkFixedString", r["system_name"] == "Struct Test")
    ok &= check("planet counts", r["planet_count"] == 4 and r["prime_planets"] == 2)
    ok &= check("star Blue via c_enum32.value", r["star_color"] == "Blue")
    ok &= check("economy HighTech -> catalog 'Technology', wealth Pirate -> 'T4'",
                r["economy_type"] == "Technology" and r["economy_strength"] == "T4")
    ok &= check("conflict Default -> 'Medium'", r["conflict_level"] == "Medium")
    ok &= check("race 6 = Exotics is NOT a no-data read (raw kept, label per table)",
                r["_race_raw"] == 6 and r["dominant_lifeform"] in ("None", "Exotics"))
    ok &= check("seed via GcSeed.Seed", r["system_seed"] == 0xABCDEF)
    ok &= check("gen-input slot 0 RealityIndex readable through the struct (galaxy candidate 3)",
                int(sd.PlanetGenerationInputs[0].RealityIndex) == 255)

    # The Cosmos layout facts this port depends on — if nmspy moves them again the
    # reader follows automatically, but the pin must be bumped consciously.
    ok &= check("layout: Name @0x2554, StarType @0x2550, Seed @0x2480 (Cosmos +0x2E0)",
                S.Name.offset == 0x2554 and S.StarType.offset == 0x2550 and S.Seed.offset == 0x2480)
    ok &= check("layout: PlanetGenerationInputs @0x2180 (was 0x1EA0 in the hand table)",
                S.PlanetGenerationInputs.offset == 0x2180)
    ok &= check("layout: cGcPlanetData.ExtraResourceHints @0x33F0 (hooks_mixin derives it)",
                nmse.cGcPlanetData.ExtraResourceHints.offset == 0x33F0)

    # Garbage fingerprint: an out-of-range enum must surface as Unknown(<raw>)
    put(S.StarType.offset, ctypes.c_uint32, 1065353472)
    r2 = Rig()._read_system_data_from_struct(S.from_buffer(buf))
    ok &= check("out-of-range enum -> 'Unknown(1065353472)' (what the sanity gate refuses)",
                r2["star_color"] == "Unknown(1065353472)")

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
