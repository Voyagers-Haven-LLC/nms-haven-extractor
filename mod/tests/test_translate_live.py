"""Live Translate (language/language_mixin.py _translate_live) - headless proof.

The live path cannot run without the game, so this exercises everything around the
one call. It imports the REAL mixin under the pinned framework, takes pyMHF's REAL
argument types for cTkLanguageManagerBase::Translate_internal, and swaps only the two game
callables for fakes. Every argument the fake receives goes through the real
argtype's from_param, which is the same conversion ctypes performs on the real
call, so a wrong pointer class fails here instead of silently falling back in game.

Needs the pinned framework:
    PYTEST_VERSION=1 dist/python/python.exe mod/tests/test_translate_live.py
Skips cleanly without nmspy.
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import ctypes
    import logging
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("PYTEST_VERSION", "1")
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))

    try:
        import nmspy
        from pymhf import Mod  # noqa: F401  (loads pyMHF before the generated types, as the mod does)
        import nmspy.data.exported_types  # noqa: F401
        import nmspy.data.types as nms
        import nmspy.data.basic_types as basic
        from pymhf.core.functions import _get_funcdef
    except Exception as e:  # pragma: no cover - environment dependent
        print(f"SKIP: framework not importable here ({e.__class__.__name__}: {e})")
        raise SystemExit(0)

    from nmspy_pin import NMSPY_PIN  # noqa: E402
    if nmspy.__version__ != NMSPY_PIN:
        print(f"SKIP: nmspy {nmspy.__version__} here, pin is {NMSPY_PIN}")
        raise SystemExit(0)

    import language.language_mixin as lm  # noqa: E402

    logging.basicConfig(level=logging.CRITICAL)   # keep the run quiet

    LP_BASE, CP64, LP_TKID = _get_funcdef(nms.cTkLanguageManagerBase.Translate_internal._func).arg_types

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    class FakeGame:
        """Stands in for NMS: validates arguments exactly as ctypes would, answers from a table."""

        def __init__(self, words):
            self.words = dict(words)
            self.calls = 0
            self.keep = []
            self.mode = "ok"          # ok | raise | none
            self.seen_this = []
            self.arg_errors = []
            self.instance = 0

        def translate(self, this, lpacText, lpacDefault):
            self.calls += 1
            for argtype, value, name in ((LP_BASE, this, "this"), (CP64, lpacText, "lpacText"),
                                         (LP_TKID, lpacDefault, "lpacDefaultReturnValue")):
                try:
                    argtype.from_param(value)
                except Exception as e:
                    self.arg_errors.append(f"{name}: {e}")
            if self.mode == "raise":
                raise OSError("simulated access violation")
            if self.mode == "none":
                return None
            self.seen_this.append(ctypes.cast(this, ctypes.c_void_p).value)
            text_id = ctypes.cast(lpacText, ctypes.c_char_p).value.decode()
            if text_id in self.words:
                buf = ctypes.create_string_buffer(self.words[text_id].encode())
                self.keep.append(buf)
                return ctypes.addressof(buf)
            # not found: the game hands the default back
            return ctypes.cast(lpacDefault, ctypes.c_void_p).value

        def get_instance(self):
            self.gi_calls = getattr(self, "gi_calls", 0) + 1
            return self.instance

    class Events:
        def __init__(self):
            self.emitted = []

        def emit(self, kind, msg, **kw):
            self.emitted.append((kind, msg))

    def rig(game, disk_cache=None):
        class R(lm.LanguageMixin):
            def _lang_bound_translate(self, mgr):
                # what pyMHF does for a bound call: `this` = the mapped instance
                return lambda text, default: game.translate(ctypes.cast(mgr, LP_BASE), text, default)

            def _lang_getinstance_fn(self):
                return game.get_instance

        r = R()
        r._translation_cache = {}
        r._translation_cache_hits = 0
        r._translation_cache_misses = 0
        r._adjective_file_cache = dict(disk_cache or {})
        r._lang_manager_seen = 0
        r._lang_types = None
        r._lang_live_results = {}
        r._lang_live_failures = 0
        r._lang_live_disabled = False
        r._lang_live_announced = False
        r._lang_in_live_call = False
        r._lang_n_live = r._lang_n_cache = r._lang_n_unresolved = 0
        r.events = Events()
        return r

    # A real block of memory so `this` is a genuine address the fake can read back.
    manager = (ctypes.c_ubyte * 64)()
    MANAGER_ADDR = ctypes.addressof(manager)
    this_from_game = ctypes.cast(MANAGER_ADDR, LP_BASE)

    ok = True

    # 1. the language manager is not up yet (GetInstance -> 0): no live call, the cache answers
    g = FakeGame({"RARITY_HIGH3": "Bountiful"})
    g.instance = 0
    r = rig(g, {"RARITY_HIGH3": "CacheBountiful"})
    ok &= check("manager not up yet (GetInstance 0): live path waits and the cache answers",
                r._resolve_adjective("RARITY_HIGH3", "flora") == "CacheBountiful" and g.calls == 0
                and r._lang_n_cache == 1 and r._lang_n_live == 0)
    ok &= check("a 0 from GetInstance is never stored as the manager", r._lang_manager_seen == 0)

    # 2. 2.2.1: the manager comes from GetInstance (no Translate hook), looked up ONCE
    g.instance = MANAGER_ADDR
    g.gi_calls = 0
    ok &= check("GetInstance supplies the manager address",
                r._lang_ensure_manager() == MANAGER_ADDR and r._lang_manager_seen == MANAGER_ADDR)
    r._lang_ensure_manager()
    r._lang_ensure_manager()
    ok &= check("GetInstance is called once, then the address is remembered", g.gi_calls == 1)

    # 2b. negative control kept from 2.1.2: pyMHF's c_char_p64 is not a pointer to ctypes
    from pymhf.extensions.ctypes import c_char_p64
    id_buf = ctypes.create_string_buffer(b"RARITY_MID5")
    word_buf = ctypes.create_string_buffer(b"Ordinary")
    ok &= check("_cstr reads c_char_p64 text (the shape pyMHF hands over)",
                r._cstr(c_char_p64(ctypes.addressof(word_buf))) == "Ordinary")
    try:
        ctypes.cast(c_char_p64(ctypes.addressof(id_buf)), ctypes.c_char_p)
        cast_rejects = False
    except ctypes.ArgumentError:
        cast_rejects = True
    ok &= check("negative control: ctypes.cast() on a c_char_p64 IS rejected (why _cstr exists)", cast_rejects)

    # 3. live beats the cache, and the arguments are exactly what ctypes accepts
    r._lang_live_results.clear()
    r._lang_n_cache = 0
    out = r._resolve_adjective("RARITY_HIGH3", "flora")
    ok &= check("live Translate wins over the cache (player's language beats the English cache)", out == "Bountiful")
    ok &= check("all three arguments pass the real argtypes' from_param", g.calls == 1 and g.arg_errors == [])
    ok &= check("the call used the game's own `this`", g.seen_this == [MANAGER_ADDR])
    ok &= check("counted as live and the first success is announced", r._lang_n_live == 1 and r._lang_live_announced)

    # 4. a resolved id is not asked for again
    r._resolve_adjective("RARITY_HIGH3", "flora")
    ok &= check("second lookup reuses the live result without another game call", g.calls == 1)

    # 5. the game does not know the id, hands it back, lookup falls through to the cache
    r._adjective_file_cache["WEATHER_COLD7"] = "Frozen Clouds"
    ok &= check("an id the game does not resolve falls through to the cache",
                r._resolve_adjective("WEATHER_COLD7", "weather") == "Frozen Clouds")
    ok &= check("an unresolved id is not stored as a live result, so it can resolve later",
                "WEATHER_COLD7" not in r._lang_live_results)

    # 6. unknown everywhere: the id comes back, counted unresolved
    before = r._lang_n_unresolved
    ok &= check("unknown to game and cache: the id comes back, counted unresolved",
                r._resolve_adjective("UI_FLORA_NOPE9", "flora") == "UI_FLORA_NOPE9"
                and r._lang_n_unresolved == before + 1)

    # 7. display text is never sent to the game
    calls = g.calls
    ok &= check("text that is already display wording passes through with no game call",
                r._resolve_adjective("Bountiful", "flora") == "Bountiful" and g.calls == calls)

    # 8. ids that would not fit the 32-byte default skip the live path
    long_id = "SENTINEL_" + "X" * 30
    r._adjective_file_cache[long_id] = "LongCached"
    calls = g.calls
    ok &= check("an id of 32+ bytes skips the live call and uses the cache",
                r._resolve_adjective(long_id, "sentinel") == "LongCached" and g.calls == calls)
    ok &= check("the longest real id (31 characters) still fits the default",
                len("SENTINEL_ROUTINE_INVESTIGATE_10") < lm._TKID_DEFAULT_SIZE)

    # 9. the breaker: failures switch the live path off, the cache keeps working
    g2 = FakeGame({})
    g2.instance = MANAGER_ADDR
    g2.mode = "raise"
    r2 = rig(g2, {"RARITY_LOW1": "Sparse"})
    r2._lang_manager_seen = MANAGER_ADDR
    for _ in range(lm._LIVE_FAIL_LIMIT):
        r2._lang_live_results.clear()
        r2._resolve_adjective("RARITY_LOW1", "flora")
    ok &= check(f"{lm._LIVE_FAIL_LIMIT} failed calls disable the live path for the session", r2._lang_live_disabled)
    calls = g2.calls
    ok &= check("once disabled there are no more game calls and the cache still answers",
                r2._resolve_adjective("RARITY_LOW1", "flora") == "Sparse" and g2.calls == calls)
    ok &= check("disabling surfaces a HEALTH event", any(k == "HEALTH" for k, _ in r2.events.emitted))

    # 10. a call pyMHF swallowed (returns None) counts as a failure too
    g3 = FakeGame({})
    g3.instance = MANAGER_ADDR
    g3.mode = "none"
    r3 = rig(g3, {})
    r3._lang_manager_seen = MANAGER_ADDR
    for _ in range(lm._LIVE_FAIL_LIMIT):
        r3._translate_live("RARITY_LOW1")
    ok &= check("a None return (pyMHF caught an error) trips the breaker instead of repeating",
                r3._lang_live_disabled)

    # 11. 2.2.1: the game is only called from its own thread. A call from one of the mod's
    #     threads (settle loop, uploader, local API) gets the remembered word or nothing.
    import threading
    g4 = FakeGame({"RARITY_HIGH3": "Bountiful", "RARITY_LOW1": "Sparse"})
    g4.instance = MANAGER_ADDR
    r4 = rig(g4, {})
    r4._game_thread_id = threading.get_ident()          # this thread plays the game thread
    ok &= check("on the game thread the live call runs", r4._translate_live("RARITY_HIGH3") == "Bountiful")
    calls = g4.calls
    box = {}
    t = threading.Thread(target=lambda: box.update(
        known=r4._translate_live("RARITY_HIGH3"), unknown=r4._translate_live("RARITY_LOW1")))
    t.start(); t.join()
    ok &= check("off the game thread: an id already resolved comes back from memory",
                box.get("known") == "Bountiful")
    ok &= check("off the game thread: a new id is refused, never sent to the game",
                box.get("unknown") is None and g4.calls == calls and r4._lang_n_offthread == 1)

    # 11b. the game's language (cTkLanguageManagerBase.meRegion at +0x8), read from real memory
    manager[8] = 4                                       # eLanguageRegion.German
    r4._game_language = ""
    ok &= check("game language read from the manager (German)", r4._lang_refresh_language() == "German")
    r4._lang_live_results["RARITY_HIGH3"] = "Bountiful"
    manager[8] = 2                                       # the player switches to French
    ok &= check("a language switch is noticed", r4._lang_refresh_language() == "French")
    ok &= check("a language switch forgets the old words so they re-resolve",
                "RARITY_HIGH3" not in r4._lang_live_results)
    manager[8] = 0
    box.clear()
    t = threading.Thread(target=lambda: box.update(lang=r4._lang_refresh_language()))
    t.start(); t.join()
    ok &= check("off the game thread the language is never re-read (last value comes back)",
                box.get("lang") == "French")

    # 12. negative controls: the two traps the implementation avoids are real
    tk = basic.TkID[0x20](b"RARITY_HIGH3")
    try:
        LP_TKID.from_param(ctypes.pointer(tk))
        trap_default = False
    except TypeError:
        trap_default = True
    ok &= check("negative control: ctypes.pointer() on the default IS rejected by pyMHF's LP_TkID<0x20>",
                trap_default)
    try:
        LP_BASE.from_param(MANAGER_ADDR)
        trap_this = False
    except TypeError:
        trap_this = True
    ok &= check("negative control: passing `this` as a raw int IS rejected", trap_this)

    # 13. the REAL pyMHF call path. Everything above swaps the game function for a
    #     fake; this runs pyMHF's own binding + FunctionHook.__call__ + _call, which
    #     builds the real ctypes prototype and calls it. Only the final native address
    #     is redirected: to a C-callable Python function with the game's exact
    #     signature. This is what caught nothing in 2.1.1: the class-level call never
    #     reached the game at all.
    import pymhf.core.hooking as hooking
    import pymhf.core._internal as internal
    hook = nms.cTkLanguageManagerBase.Translate_internal
    funcdef = _get_funcdef(hook._func)
    seen = {}
    word = ctypes.create_string_buffer(b"Bountiful")

    def native(this, text, default):
        seen["this"] = ctypes.cast(this, ctypes.c_void_p).value
        # c_char_p64 arrives as its own ctypes type or a plain int address
        addr = text if isinstance(text, int) else getattr(text, "value", None)
        if not isinstance(addr, int):
            addr = ctypes.cast(text, ctypes.c_void_p).value
        seen["text_type"] = type(text).__name__
        seen["text"] = ctypes.string_at(addr) if addr else None
        return ctypes.addressof(word)

    native_c = ctypes.CFUNCTYPE(funcdef.restype, *funcdef.arg_types)(native)
    saved = (hooking.find_pattern_in_binary, internal.BASE_ADDRESS, hook._bound_class)
    hooking.find_pattern_in_binary = lambda *a, **k: 0
    internal.BASE_ADDRESS = ctypes.cast(native_c, ctypes.c_void_p).value
    try:
        # negative control: the 2.1.1 call style (on the class, nothing bound)
        hook._bound_class = None
        try:
            hook(ctypes.cast(MANAGER_ADDR, LP_BASE), b"RARITY_HIGH3", None)
            class_call_raised = False
        except ValueError as e:
            class_call_raised = "Not bound" in str(e)
        ok &= check("negative control: calling Translate on the class raises 'Not bound' (the 2.1.1 bug)",
                    class_call_raised)

        r5 = rig(FakeGame({}))
        del type(r5)._lang_bound_translate          # use the REAL mixin method
        r5._lang_manager_seen = MANAGER_ADDR
        out = r5._translate_live("RARITY_HIGH3")
        ok &= check("real pyMHF bound call reaches the native function and resolves",
                    out == "Bountiful" and seen.get("text") == b"RARITY_HIGH3")
        ok &= check("pyMHF passed the game's own manager as `this`", seen.get("this") == MANAGER_ADDR)
        ok &= check("no failure was counted", r5._lang_live_failures == 0 and not r5._lang_live_disabled)
    finally:
        hooking.find_pattern_in_binary, internal.BASE_ADDRESS, hook._bound_class = saved

    # 14. layer order in source: live, then the cache, then passthrough
    src = (MOD / "language" / "language_mixin.py").read_text(encoding="utf-8")
    body = src[src.index("def _resolve_adjective"):]
    ok &= check("resolver order is live Translate, then the cache, then passthrough",
                body.index("self._translate_live(text_id)")
                < body.index("if text_id in self._adjective_file_cache")
                < body.index("DESCRIPTOR_ID_RE.match(text_id)):\n            return text_id"))

    # 15. 2.2.1: the mod no longer hooks Translate_internal at all (the game calls it for
    #     every string it draws: the lag/freeze reports). pyMHF registers hooks from the
    #     decorated methods in the class __dict__, so no method may carry that hook.
    from pymhf.core.importing import import_file
    hx = import_file(str(MOD / "haven_extractor2.py"))
    hooked = [name for name, fn in hx.HavenExtractor2.__dict__.items()
              if "Translate" in str(getattr(fn, "_hook_func_name", ""))]
    ok &= check("no method of the mod hooks Translate_internal", hooked == [] and
                "on_translate" not in hx.HavenExtractor2.__dict__)
    ok &= check("Translate_internal is not a required hook any more",
                not any("Translate" in h for h in hx.REQUIRED_HOOKS))
    gen = hx.HavenExtractor2.__dict__.get("on_system_generate")
    ok &= check("positive control: the other hooks still carry pyMHF's hook marker",
                gen is not None and "Generate" in str(getattr(gen, "_hook_func_name", "")))

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
