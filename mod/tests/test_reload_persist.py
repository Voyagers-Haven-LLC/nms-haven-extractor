"""Reload persistence + shutdown, against the real mod class under the pinned framework.

Simulates exactly what pyMHF's ModManager.reload() does: instantiate a fresh mod,
then setattr the previous instance's ModState onto it. Needs pymhf + nmspy
importable (the pinned embedded Python); skips cleanly elsewhere.
Run: set PYTEST_VERSION=1 && dist\\python\\python.exe mod\\tests\\test_reload_persist.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import os
    import sys
    import logging
    from pathlib import Path
    os.environ.setdefault("PYTEST_VERSION", "1")
    os.environ.setdefault("HAVEN_LOCAL_PORT_TEST", "1")
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    logging.disable(logging.CRITICAL)
    try:
        import pymhf  # noqa: F401
        import nmspy  # noqa: F401
    except Exception as e:
        print(f"SKIP: framework not importable here ({e.__class__.__name__})")
        raise SystemExit(0)

    import haven_extractor2 as h
    from pymhf.core.mod_loader import ModState

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    # keep the test from binding the real local API port / touching the real env
    os.environ["HAVEN_API_URL"] = "http://127.0.0.1:9"
    a = h.HavenExtractor2()
    try:
        ok &= check("mod exposes a ModState (what pyMHF captures on first load)",
                    isinstance(a.persist, ModState))
        ok &= check("persisted attributes read/write through self.persist",
                    a._batch_systems is a.persist.batch_systems and a.events is a.persist.events)
        a._batch_systems.append({"glyph_code": "0123456789AB", "system_name": "Keep Me"})
        a._enqueued_glyphs.add("0123456789AB")
        a._game_mode = "Permadeath"
        a._captured_planets["Planet X"] = {"planet_name": "Planet X", "biome": "Lush"}
        a.events.emit("SESSION", "history line that must survive")

        # --- what pyMHF does on reload: new instance, then re-attach the old state
        b = h.HavenExtractor2()
        fresh_batch_empty = (b._batch_systems == [])
        setattr(b, "persist", a.persist)
        ok &= check("fresh instance starts empty, then inherits batch/glyphs/mode/capture after the swap",
                    fresh_batch_empty and b._batch_systems[0]["system_name"] == "Keep Me"
                    and "0123456789AB" in b._enqueued_glyphs and b._game_mode == "Permadeath"
                    and "Planet X" in b._captured_planets)
        ok &= check("terminal history survives the swap",
                    any("history line" in e.get("message", "") for e in b.events.snapshot()) if hasattr(b.events, "snapshot")
                    else b.events is a.events)
        ok &= check("per-instance plumbing is NOT shared (stop event, readiness flag)",
                    b._stop is not a._stop and b._readiness_reported is False)

        # --- shutdown frees the old instance: threads stop, local API port released
        port_a = getattr(a.local_api, "port", None)
        a._shutdown_background()
        import time
        time.sleep(1.0)
        ok &= check("shutdown sets the stop event and the shut_down flag", a._stop.is_set() and a._shut_down)
        import socket
        released = True
        if port_a:
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", int(port_a)))
            except OSError:
                released = False
            finally:
                s.close()
        ok &= check(f"old local API port {port_a} is free after shutdown", released)
        ok &= check("reload shim satisfies pyMHF's reload() contract",
                    h._ReloadShim.module_reload_enabled is True and callable(getattr(h._ReloadShim(), "reload_tab", None)))
        ok &= check("framework self-upgrade helper locates a python.exe under the framework",
                    __import__("nmspy_pin").embedded_python_exe() is not None)
    finally:
        for inst in (locals().get("a"), locals().get("b")):
            try:
                if inst is not None:
                    inst._shutdown_background()
            except Exception:
                pass

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
