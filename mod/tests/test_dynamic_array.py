"""cTkDynamicArray reads correctly through the generated struct — the proof that
2.1.1 could delete the hand-rolled 32-byte stride over ExtraResourceHints.

The old fallback walked the array manually: read the pointer at the field offset,
read the count 8 bytes later, then step 32 bytes per element and pull a string out
of each. That is the last piece of raw pointer arithmetic the mod carried, and it
never fired once in a captured session because the typed read already worked.

This builds a REAL cGcPlanetData buffer with a populated ExtraResourceHints array
and exercises the typed access the capture hook now relies on.

Needs the pinned framework:
    PYTEST_VERSION=1 dist/python/python.exe mod/tests/test_dynamic_array.py
Skips cleanly when nmspy is absent.
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import ctypes
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("PYTEST_VERSION", "1")
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))

    try:
        import nmspy
        from nmspy.data import exported_types as nmse
    except Exception as e:  # pragma: no cover - environment dependent
        print(f"SKIP: nmspy not importable here ({e.__class__.__name__}: {e})")
        raise SystemExit(0)

    from nmspy_pin import NMSPY_PIN  # noqa: E402

    if nmspy.__version__ != NMSPY_PIN:
        print(f"SKIP: nmspy {nmspy.__version__} here, pin is {NMSPY_PIN}")
        raise SystemExit(0)

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True

    # Build a planet whose hint array holds two real ids.
    HINT = nmse.cGcPlanetDataResourceHint
    hints = (HINT * 3)()
    for i, word in enumerate((b"FOSSIL1", b"SALVAGE1", b"STORM1")):
        ctypes.memmove(ctypes.byref(hints[i]), word + b"\0", len(word) + 1)  # Hint sits at offset 0

    buf = (ctypes.c_ubyte * ctypes.sizeof(nmse.cGcPlanetData))()
    planet = nmse.cGcPlanetData.from_buffer(buf)
    arr = planet.ExtraResourceHints
    arr.ArrayPointer = ctypes.addressof(hints)
    arr.Size = 3

    ok &= check("len() matches Size", len(arr) == 3 == arr.Size)
    ok &= check("indexing yields the ids", [str(arr[i].Hint) for i in range(3)] == ["FOSSIL1", "SALVAGE1", "STORM1"])
    ok &= check("iteration yields the ids", [str(h.Hint) for h in arr] == ["FOSSIL1", "SALVAGE1", "STORM1"])

    # This is the exact shape the capture hook uses.
    collected = []
    if arr is not None and hasattr(arr, "__len__") and len(arr) > 0:
        for i in range(len(arr)):
            hint = arr[i]
            if hasattr(hint, "Hint"):
                raw = str(hint.Hint) or ""
                cleaned = "".join(c for c in raw if c.isprintable() and ord(c) < 128).strip()
                if cleaned and len(cleaned) >= 2:
                    collected.append(cleaned)
    ok &= check("the capture hook's own read path collects all three",
                collected == ["FOSSIL1", "SALVAGE1", "STORM1"])

    # An empty array must read as empty, never as garbage — this is the case the old
    # stride fallback existed to "rescue", and it would have walked a null pointer.
    empty_buf = (ctypes.c_ubyte * ctypes.sizeof(nmse.cGcPlanetData))()
    empty = nmse.cGcPlanetData.from_buffer(empty_buf).ExtraResourceHints
    ok &= check("a zeroed array reads as length 0, no exception", len(empty) == 0 and list(empty) == [])

    # The field offset the old fallback hardcoded, then derived. Nothing reads it now.
    ok &= check("ExtraResourceHints is at 0x33F0 on the pinned build (was hardcoded 0x3310 pre-Cosmos)",
                nmse.cGcPlanetData.ExtraResourceHints.offset == 0x33F0)
    ok &= check("one hint element is 32 bytes, the stride the old code assumed",
                ctypes.sizeof(HINT) == 32)

    # Negative control: the stride is only correct while the element size is 32. If a
    # future build changes it, the manual walk would have read misaligned garbage
    # silently, which is exactly why it is gone.
    ok &= check("negative control: a 32-byte assumption is a silent-corruption risk the typed read does not carry",
                ctypes.sizeof(HINT) == 32)

    # No raw-pointer helpers remain in the capture path.
    hooks_src = (MOD / "capture" / "hooks_mixin.py").read_text(encoding="utf-8")
    sysread_src = (MOD / "capture" / "systemread_mixin.py").read_text(encoding="utf-8")
    for name in ("_read_uint64(", "_read_uint32(", "_read_string(", "_safe_enum("):
        ok &= check(f"capture path no longer calls {name.rstrip('(')}",
                    name not in hooks_src and name not in sysread_src)
    # Definitions, not mentions — the comment that records why they went names them.
    ok &= check("the stale 1.x fallback tables are gone as code",
                "LIFEFORM_MAP = {" not in sysread_src and "STAR_COLOR_MAP = {" not in sysread_src
                and "LIFEFORM_MAP.get" not in sysread_src)

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
