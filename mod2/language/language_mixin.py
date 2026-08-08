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


class LanguageMixin:
    """Translate-hook cache + adjective cache + layered adjective resolution."""

    # ---- source lines 1251-1290: on_translate ----

    def _on_translate_impl(self, this, lpacText, lpacDefaultReturnValue, _result_):
        """
        Passive hook on the game's Translate function.
        Captures (text_id -> display_text) pairs as the game resolves them.
        This fires whenever the game renders text (UI, discovery pages, scanner).
        """
        try:
            if not lpacText or not _result_:
                return

            # Decode the text ID
            if isinstance(lpacText, bytes):
                text_id = lpacText.decode('utf-8', errors='ignore')
            else:
                text_id = str(ctypes.cast(lpacText, ctypes.c_char_p).value or b'', 'utf-8', errors='ignore')

            if not text_id:
                return

            # Only capture adjective-related text IDs
            if text_id.startswith(('RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_BIOME_', 'BIOME_',
                                   'UI_PLANET_', 'UI_SENTINEL_', 'UI_WEATHER_', 'UI_FLORA_',
                                   'UI_FAUNA_', 'UI_RARITY_')):
                # Read the result string from the return value
                try:
                    result_ptr = ctypes.cast(_result_, ctypes.c_char_p)
                    if result_ptr.value:
                        display_text = result_ptr.value.decode('utf-8', errors='ignore')
                        # Only store if it looks like valid display text (not another ID)
                        if (display_text and len(display_text) >= 2 and
                            not display_text.startswith(('RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_'))):
                            if text_id not in self._translation_cache:
                                self._translation_cache[text_id] = display_text
                                logger.debug(f"    [TRANSLATE] Captured: '{text_id}' -> '{display_text}'")
                except Exception:
                    pass
        except Exception:
            pass  # Never crash the game from a translation hook

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
        1. Disk-based PAK/MBIN cache (adjective_cache.json - primary)
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

        # Already a display string? (doesn't match internal ID patterns)
        if not any(text_id.startswith(p) for p in [
            'RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_BIOME_', 'BIOME_',
            'UI_PLANET_', 'UI_SENTINEL_', 'UI_WEATHER_', 'UI_FLORA_',
            'UI_FAUNA_', 'UI_RARITY_'
        ]):
            return text_id

        # Layer 1: Disk-based PAK/MBIN cache (primary - built from game files)
        if text_id in self._adjective_file_cache:
            return self._adjective_file_cache[text_id]

        # Layer 2: In-memory translation cache (backup - from Translate hook)
        if text_id in self._translation_cache:
            self._translation_cache_hits += 1
            return self._translation_cache[text_id]

        # Unresolved - return original text ID
        self._translation_cache_misses += 1
        return text_id
