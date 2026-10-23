"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.
"""

import json
import logging
import time
import ctypes
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, List, Dict
from ctypes import c_uint64, c_int64, c_void_p, pointer, sizeof

from pymhf.core.memutils import map_struct, get_addressof
import pymhf.core._internal as _internal
import nmspy.data.types as nms
import nmspy.data.exported_types as nmse
from nmspy.common import gameData
import nmspy.data.basic_types as basic

logger = logging.getLogger("haven_extractor2")

from nms_language import ADJECTIVE_PREFIXES  # noqa: E402

# Ids that carry no adjective prefix but are still ids, not display text: the
# planet-type descriptor pool ("LUSH4", "INFESTEDLUSH1", "UI_PARADISE_PLANET")
# and the class words ("PLANETCLASS1").
DESCRIPTOR_ID_RE = re.compile(r'^(?:[A-Z]+\d{1,2}|UI_[A-Z_]+_PLANET|PLANETCLASS\d+)$')


# 2.1.1 live Translate (see LanguageMixin._translate_live).
_TKID_DEFAULT_SIZE = 0x20   # lpacDefaultReturnValue is TkID<0x20>: 31 characters + NUL
_LIVE_FAIL_LIMIT = 5        # consecutive failed live calls before the session falls back to the cache


class LanguageMixin:
    """Live game Translate + Translate-hook cache + adjective cache + layered adjective resolution."""

    # 2.2.1: the passive Translate hook (_on_translate_impl, transplanted from 1.x) is gone.
    # It ran for every string the game draws to learn one address; see _lang_ensure_manager.
    # _translation_cache stays as an (empty) resolver layer so _resolve_adjective is unchanged.

    # ---- 2.1.1: live Translate - the game resolves ids itself ----------------
    #
    # Resolution used to be English-only: nms_language.py cracked the game's language
    # paks with hgpaktool and parsed them into adjective_cache.json. Calling the game's
    # own cTkLanguageManagerBase::Translate returns the text in whatever language the
    # player runs, and covers ids the game has not rendered yet.
    #
    # Call contract (pyMHF 0.2.4 / NMSpy 181442, proved headless in
    # tests/test_translate_live.py against the real generated types):
    #   Translate_internal(this: LP_cTkLanguageManagerBase, lpacText: c_char_p64,
    #             lpacDefaultReturnValue: LP_TkID<0x20>) -> c_char_p64 (address of UTF-8 text)
    # NMSpy 180383 renamed the hook from Translate (2.1.1 fails to import on it) and
    # added a static cTkLanguageManager.Translate(str) helper. That helper passes a
    # NULL default, which is exactly what the safety notes below avoid, so the mod
    # keeps its own call and only follows the rename.
    # Two traps, both silent without the cache fallback: `this` must be cast to pyMHF's
    # pointer class (a raw int is rejected), and the default must be cast to pyMHF's
    # LP_TkID<0x20> (ctypes.pointer() builds a different class with the same name).
    #
    # Safety:
    #   * `this` is the language manager from cTkLanguageManager::GetInstance (2.2.1; until
    #     2.1.2 it was captured by a passive Translate hook, and the 2.1.2 logs show both
    #     addresses are the same). Only ever called on the game thread.
    #   * the default is a real 32-byte TkID holding the id, never NULL, so the game never
    #     dereferences a null pointer. Ids of 32+ bytes skip the live path; none exist
    #     (the longest known id is 31 characters).
    #   * a failed or pyMHF-swallowed call counts toward a breaker. Five in a row turn the
    #     live path off for the session and every word resolves from the cache exactly as
    #     it did before 2.1.1.
    #   * nothing here raises into the game.

    def _lang_bound_translate(self, mgr: int):
        """The game's Translate, bound to the language manager at ``mgr``.

        Translate is NOT static. Called on the class, pyMHF raises "Not bound to
        anything..." on every call (FunctionHook.__call__), so the 2.1.1 build fell
        back to the English cache every time without anyone seeing why. Reaching it
        through a mapped instance binds it (pyMHF's Structure.__getattribute__), and
        pyMHF then passes ``this`` itself.
        """
        from pymhf.core.memutils import map_struct
        return map_struct(mgr, nms.cTkLanguageManagerBase).Translate_internal

    def _lang_getinstance_fn(self):
        return nms.cTkLanguageManager.GetInstance

    def _lang_arg_types(self):
        """The exact pointer classes pyMHF builds the Translate call with (lru-cached funcdef)."""
        if getattr(self, "_lang_types", None) is None:
            from pymhf.core.functions import _get_funcdef
            self._lang_types = tuple(_get_funcdef(nms.cTkLanguageManagerBase.Translate_internal._func).arg_types)
        return self._lang_types

    @staticmethod
    def _addr_of(val) -> int:
        """Integer address behind a ctypes pointer, a c_uint64-style value, or an int. 0 when absent."""
        try:
            if val is None:
                return 0
            if isinstance(val, int):
                return val
            inner = getattr(val, "value", None)
            if isinstance(inner, int):
                return inner
            return ctypes.cast(val, ctypes.c_void_p).value or 0
        except Exception:
            return 0

    @classmethod
    def _cstr(cls, val) -> str:
        """UTF-8 text behind a C string handed over as bytes, an int address, a ctypes
        pointer, or pyMHF's c_char_p64. '' when absent or unreadable. Never raises.
        Addresses inside the first 64 KB are treated as absent (never read the null page)."""
        try:
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="ignore")
            addr = cls._addr_of(val)
            if addr <= 0x10000:
                return ""
            value = ctypes.cast(addr, ctypes.c_char_p).value
            return value.decode("utf-8", errors="ignore") if value else ""
        except Exception:
            return ""

    def _lang_count(self, which: str):
        attr = f"_lang_n_{which}"
        setattr(self, attr, getattr(self, attr, 0) + 1)

    def _translate_live(self, text_id: str):
        """Ask the game to translate text_id now. Returns the display text, or None when the
        live path is off, not ready yet, or the id did not resolve. Never raises."""
        if getattr(self, "_lang_live_disabled", False) or getattr(self, "_lang_in_live_call", False):
            return None
        cached = (getattr(self, "_lang_live_results", None) or {}).get(text_id)
        if cached is not None:
            return cached
        try:
            raw = text_id.encode("utf-8")
        except Exception:
            return None
        if not raw or len(raw) >= _TKID_DEFAULT_SIZE:
            return None
        # 2.2.1: the game is only ever called from its own thread (the hooks). A call from
        # one of ours (settle loop, local API, uploader) gets the remembered answer or None.
        game_tid = getattr(self, "_game_thread_id", None)
        if game_tid is not None and threading.get_ident() != game_tid:
            self._lang_n_offthread = getattr(self, "_lang_n_offthread", 0) + 1
            return None
        mgr = self._lang_ensure_manager()
        if not mgr:
            return None   # the language manager is not up yet: early, not a failure
        self._lang_in_live_call = True
        try:
            _lp_base, _cp64, lp_tkid = self._lang_arg_types()
            text_buf = ctypes.create_string_buffer(raw)
            default = basic.TkID[_TKID_DEFAULT_SIZE](raw)
            default_ptr = ctypes.cast(ctypes.pointer(default), lp_tkid)
            result = self._lang_bound_translate(mgr)(ctypes.addressof(text_buf), default_ptr)
            if result is None:
                # a real call returns a c_char_p64 (even for NULL); None means pyMHF caught a failure
                raise RuntimeError("Translate call failed inside pyMHF")
            addr = self._addr_of(result)
            value = ctypes.cast(addr, ctypes.c_char_p).value if addr else None
            text = value.decode("utf-8", errors="ignore").strip() if value else ""
        except Exception as e:
            self._lang_live_failures = getattr(self, "_lang_live_failures", 0) + 1
            # The FIRST failure is logged at WARNING: player logs filter this module's
            # DEBUG, which is why the 2.1.1 build's failure reason was never seen.
            log = logger.warning if self._lang_live_failures == 1 else logger.debug
            log(f"[TRANSLATE-LIVE] call failed for {text_id!r}: {e.__class__.__name__}: {e}")
            if self._lang_live_failures >= _LIVE_FAIL_LIMIT:
                self._lang_live_disabled = True
                logger.warning(f"[TRANSLATE-LIVE] disabled for this session after "
                               f"{self._lang_live_failures} failed calls - resolving from the adjective cache")
                events = getattr(self, "events", None)
                if events is not None:
                    try:
                        events.emit("HEALTH", "Live translation unavailable this session - planet words "
                                              "come from the English adjective cache instead.",
                                    level="warning", coalesce_key="translate-live")
                    except Exception:
                        pass
            return None
        finally:
            self._lang_in_live_call = False

        self._lang_live_failures = 0
        if not text or text == text_id or text.startswith(ADJECTIVE_PREFIXES) or DESCRIPTOR_ID_RE.match(text):
            return None   # the game did not resolve it (it usually hands the id back)
        results = getattr(self, "_lang_live_results", None)
        if results is None:
            results = self._lang_live_results = {}
        results[text_id] = text
        if not getattr(self, "_lang_live_announced", False):
            self._lang_live_announced = True
            logger.info(f"[TRANSLATE-LIVE] working: {text_id!r} -> {text!r} "
                        f"(language manager 0x{mgr:X}, game language {getattr(self, '_game_language', '') or '?'})")
        return text

    # ---- 2.2.1: the language manager without a Translate hook -----------------------

    def _lang_ensure_manager(self) -> int:
        """The game's language manager address, looked up ONCE via GetInstance.

        2.1.2 learned it from a passive hook on Translate_internal, which the game calls for
        every string it draws; the 2.1.2 logs show GetInstance returns the same address.
        Only called on the game thread (_translate_live checks). 0 while not available."""
        mgr = getattr(self, "_lang_manager_seen", 0)
        if mgr:
            return mgr
        try:
            gi = self._addr_of(self._lang_getinstance_fn()())
        except Exception as e:
            if not getattr(self, "_lang_gi_warned", False):
                self._lang_gi_warned = True
                logger.warning(f"[TRANSLATE-LIVE] GetInstance failed: {e.__class__.__name__}: {e}")
            return 0
        if gi > 0x10000:
            self._lang_manager_seen = gi
            logger.info(f"[TRANSLATE-LIVE] language manager via GetInstance: 0x{gi:X}")
            try:
                self._lang_refresh_language()
            except Exception:
                pass
            return gi
        return 0

    def _lang_refresh_language(self) -> str:
        """Read the game's current language (cTkLanguageManagerBase.meRegion) on the game thread.
        A change clears the remembered live words so the next system resolves in the new language."""
        game_tid = getattr(self, "_game_thread_id", None)
        if game_tid is not None and threading.get_ident() != game_tid:
            return getattr(self, "_game_language", "")
        mgr = getattr(self, "_lang_manager_seen", 0) or self._lang_ensure_manager()
        if not mgr:
            return getattr(self, "_game_language", "")
        from pymhf.core.memutils import map_struct
        region = map_struct(mgr, nms.cTkLanguageManagerBase).meRegion
        name = getattr(region, "name", None) or str(region)
        if name and name != getattr(self, "_game_language", ""):
            if getattr(self, "_game_language", ""):
                logger.info(f"[LANG] game language changed {self._game_language} -> {name}; "
                            f"live words re-resolve")
                (getattr(self, "_lang_live_results", None) or {}).clear()
            else:
                logger.info(f"[LANG] game language: {name}")
            self._game_language = name
        return self._game_language

    # ---- source lines 1292-1388: _load_adjective_cache + _resolve_adjective ----
    def _load_adjective_cache(self):
        """Load adjective cache from disk, or build it from game PAK files in background.

        Priority:
        1. User's existing cache in ~/Documents/Haven-Extractor/
        2. Bundled cache shipped with the mod (copied to user dir on first use)
        3. Background build from game PAK files
        """
        try:
            try:
                from .nms_language import AdjectiveCacheBuilder
            except ImportError:
                from nms_language import AdjectiveCacheBuilder

            builder = AdjectiveCacheBuilder(cache_dir=self._output_dir)

            # Try loading user's existing cache
            cached = builder.load_cache()
            if cached:
                self._adjective_file_cache = cached
                logger.info(f"[INIT] Loaded {len(cached)} adjective mappings from cache")
                return

            # No user cache — check for bundled cache shipped with the mod
            bundled_cache = Path(__file__).parent / "adjective_cache.json"
            if bundled_cache.exists():
                try:
                    import shutil
                    shutil.copy2(str(bundled_cache), str(builder.cache_path))
                    cached = builder.load_cache()
                    if cached:
                        self._adjective_file_cache = cached
                        logger.info(f"[INIT] Loaded {len(cached)} adjective mappings from bundled cache")
                        return
                except Exception as e:
                    logger.warning(f"[INIT] Failed to copy bundled cache: {e}")

            # No cache available — try building from game PAK files in background
            if not builder.nms_path:
                logger.info("[INIT] NMS installation not found - using legacy adjective tables")
                return

            logger.info("[INIT] Building adjective cache from game files (background)...")
            import threading

            def build_async():
                try:
                    mappings = builder.build_cache()
                    self._adjective_file_cache = mappings
                    logger.info(f"[INIT] Background cache build complete: {len(mappings)} entries")
                except Exception as e:
                    logger.warning(f"[INIT] Background cache build failed: {e}")

            threading.Thread(target=build_async, daemon=True).start()

        except Exception as e:
            logger.warning(f"[INIT] Failed to load adjective cache: {e}")
            import traceback
            logger.warning(traceback.format_exc())

    def _resolve_adjective(self, text_id: str, field_type: str = 'flora') -> str:
        """
        Resolve a text ID to its display adjective using layered lookup:
        0. The game's own Translate, live (2.1.1 - player's language, any id)
        1. Disk-based PAK/MBIN cache (adjective_cache.json - fallback, English)
        2. In-memory translation cache (from game's Translate hook - backup)
        3. Original value as last resort

        Args:
            text_id: The raw text ID (e.g., 'RARITY_HIGH3', 'WEATHER_COLD7')
            field_type: 'flora', 'fauna', 'sentinel', or 'weather'

        Returns:
            The resolved display adjective
        """
        if not text_id or text_id == "None":
            return "Unknown"

        # Layer 0 (2.1.1): ask the game. Resolves in the player's own language and covers
        # ids the game has not rendered yet. Off / not ready / unresolved -> None, and the
        # lookup falls through to the cache exactly as it did before.
        if text_id.startswith(ADJECTIVE_PREFIXES) or DESCRIPTOR_ID_RE.match(text_id):
            live = self._translate_live(text_id)
            if live is not None:
                self._lang_count("live")
                return live

        # Layer 1: Disk-based PAK/MBIN cache (fallback - built from game files).
        # An exact hit wins regardless of shape: planet-type descriptors ("LUSH4",
        # "UI_PARADISE_PLANET") and class words ("PLANETCLASS1") have no prefix.
        if text_id in self._adjective_file_cache:
            self._lang_count("cache")
            return self._adjective_file_cache[text_id]

        # Layer 2: In-memory translation cache (backup - from Translate hook)
        if text_id in self._translation_cache:
            self._translation_cache_hits += 1
            self._lang_count("cache")
            return self._translation_cache[text_id]

        # Already a display string? (doesn't match any internal ID pattern)
        if not (text_id.startswith(ADJECTIVE_PREFIXES) or DESCRIPTOR_ID_RE.match(text_id)):
            return text_id

        # Unresolved - return original text ID
        self._translation_cache_misses += 1
        self._lang_count("unresolved")
        return text_id
