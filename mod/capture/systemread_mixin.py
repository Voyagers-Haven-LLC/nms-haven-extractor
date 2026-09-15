"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.

2.1.0: the system-level read no longer touches raw offsets. It reads the
generated struct (nmse.cGcSolarSystemData) field by field, so a game update
is fixed by bumping mod2/nmspy_pin.py, not by re-deriving numbers.
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

from capture.offsets import *  # noqa: F401,F403


class SystemReadMixin:
    """System-level property reads + snapshot + game-mode detection (typed reads, 2.1.1)."""

    # ---- 2.1.0: struct-based system read (replaces the raw-offset _read_system_data_direct) ----
    @staticmethod
    def _enum_raw(val) -> int:
        """Raw integer behind a pymhf c_enum32 (``.value``), a plain int, or -1."""
        try:
            return int(getattr(val, "value", val))
        except Exception:
            return -1

    @staticmethod
    def _fixed_string(val, max_len: int = 127) -> str:
        """cTkFixedString -> clean printable str (stops at NUL, strips junk)."""
        try:
            text = str(val) if val is not None else ""
        except Exception:
            return ""
        text = text.split("\x00", 1)[0]
        text = "".join(c for c in text if c.isprintable()).strip()
        return text[:max_len]

    def _read_system_data_from_struct(self, sd) -> dict:
        """Read system properties from a mapped nmse.cGcSolarSystemData.

        Field names are the framework's own (Name, Planets, PrimePlanets, StarType,
        TradingData.TradingClass/WealthClass, ConflictData, InhabitingRace, Seed).
        Enum fields are pymhf c_enum32 wrappers: ``.value`` is the raw integer,
        which the display tables translate; an out-of-range raw value renders as
        ``Unknown(<raw>)`` and is what the upload sanity gate refuses.
        """
        result = {
            "system_name": "",
            "star_color": "Unknown",
            "economy_type": "Unknown",
            "economy_strength": "Unknown",
            "conflict_level": "Unknown",
            "dominant_lifeform": "Unknown",
            "system_seed": 0,
            "planet_count": 0,
            "prime_planets": 0,
            "_race_raw": -1,
        }
        if sd is None:
            return result

        try:
            system_name = self._fixed_string(getattr(sd, "Name", None))
            if system_name:
                result["system_name"] = system_name
                logger.debug(f"  [STRUCT] System name: {system_name}")

            result["planet_count"] = int(sd.Planets)
            result["prime_planets"] = int(sd.PrimePlanets)
            logger.debug(f"  [STRUCT] Planet count: {result['planet_count']}, Prime: {result['prime_planets']}")

            star_type_val = self._enum_raw(sd.StarType)
            result["star_color"] = STAR_TYPES.get(star_type_val, f"Unknown({star_type_val})")
            logger.debug(f"  [STRUCT] Star color: {result['star_color']} (raw: {star_type_val})")

            trading_class = self._enum_raw(sd.TradingData.TradingClass)
            wealth_class = self._enum_raw(sd.TradingData.WealthClass)
            result["economy_type"] = TRADING_CLASSES.get(trading_class, f"Unknown({trading_class})")
            result["economy_strength"] = WEALTH_CLASSES.get(wealth_class, f"Unknown({wealth_class})")
            logger.debug(f"  [STRUCT] Economy: {result['economy_type']} / {result['economy_strength']} (raw: {trading_class}/{wealth_class})")

            conflict_val = self._enum_raw(sd.ConflictData)
            result["conflict_level"] = CONFLICT_LEVELS.get(conflict_val, f"Unknown({conflict_val})")
            logger.debug(f"  [STRUCT] Conflict: {result['conflict_level']} (raw: {conflict_val})")

            race_val = self._enum_raw(sd.InhabitingRace)
            result["_race_raw"] = race_val
            result["dominant_lifeform"] = ALIEN_RACES.get(race_val, f"Unknown({race_val})")
            logger.debug(f"  [STRUCT] Race: {result['dominant_lifeform']} (raw: {race_val})")

            # 2.1.1: the system flags ride on every body's generation input; [0] is
            # the first body and every system has one. Abandoned is NOT a race value
            # (the race reads None_), so it is emitted from this flag.
            result["_abandoned"] = False
            result["_pirate"] = False
            try:
                gi0 = sd.PlanetGenerationInputs[0]
                result["_abandoned"] = bool(gi0.InAbandonedSystem)
                result["_pirate"] = bool(gi0.InPirateSystem)
            except Exception as e:
                logger.debug(f"  [STRUCT] system flags unreadable: {e}")

            try:
                result["system_seed"] = int(sd.Seed.Seed)
            except Exception:
                pass

            # 2.0.3: cGcAlienRace runs 0-8 (Traders, Warriors, Explorers, Robots, Atlas,
            # Diplomats, Exotics, None_, Builders). 7 = None_ is the game's own value for
            # uninhabited systems — the DB-wide audit proved the old `> 6` guard was wiping
            # economy/conflict/lifeform on every legitimately uninhabited system. Absence
            # of inhabitants is real data ("None"), matching manual-upload vocabulary.
            # Only values past the enum's end (>8) indicate a garbage/no-data read.
            if race_val == 7:
                logger.debug("  [STRUCT] Uninhabited system (race=None_) — recording absence")
                result["economy_type"] = "None"
                result["economy_strength"] = "None"
                result["conflict_level"] = "None"
                result["dominant_lifeform"] = "None"
            elif race_val > 8 or race_val < 0:
                logger.debug(f"  [STRUCT] Race value out of enum range (race={race_val}) — no-data read")
                result["economy_type"] = "Unknown"
                result["economy_strength"] = "Unknown"
                result["conflict_level"] = "Unknown"
                result["dominant_lifeform"] = "Unknown"

            if result.get("_abandoned"):
                result["dominant_lifeform"] = "Abandoned"
                logger.debug("  [STRUCT] InAbandonedSystem — lifeform 'Abandoned'")

        except Exception as e:
            logger.error(f"Struct system data read failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return result

    def _read_system_data_direct(self, sys_data_addr: int) -> dict:
        """Address-based entry point kept for callers that only hold an address:
        maps the framework struct at that address and reads it. No hand offsets."""
        try:
            sd = map_struct(sys_data_addr, nmse.cGcSolarSystemData)
        except Exception as e:
            logger.debug(f"  [STRUCT] map_struct failed at 0x{sys_data_addr:X}: {e}")
            sd = None
        return self._read_system_data_from_struct(sd)

    # ---- source lines 3207-3405: _extract_system_properties + _snapshot_system_properties ----
    def _extract_system_properties(self, sys_data) -> dict:
        """Extract system-level properties from game memory."""
        result = {
            "system_name": "",
            "star_color": "Unknown",
            "economy_type": "Unknown",
            "economy_strength": "Unknown",
            "conflict_level": "Unknown",
            "dominant_lifeform": "Unknown",
            "system_seed": 0,
        }

        # 2.1.1: the "In the {name} system" notification read (a hand offset of 0x38
        # into game_state) is gone — it never resolved a name in any captured session,
        # and cGcSolarSystemData.Name is read through the generated struct below.
        #
        # The 1.x STAR_COLOR_MAP / LIFEFORM_MAP fallback tables are gone too. They were
        # stale duplicates of capture/offsets.py: LIFEFORM_MAP still said race 6 was
        # "None" after 2.1.1 made it Exotics, and had no entry for 8 (Autophage), while
        # STAR_COLOR_MAP laundered the unrelated cGcSolarSystemClass "Default" into
        # "Yellow". Display vocabulary now has exactly one source.

        # v1.6.10: Direct-offset reads as primary (resilient to NMS struct shifts).
        # Wires up the previously-dead _read_system_data_direct helper.
        sys_data_addr = None
        try:
            sys_data_addr = get_addressof(sys_data)
        except Exception:
            sys_data_addr = None

        def _is_unresolved(val):
            return not val or val == "Unknown" or (isinstance(val, str) and val.startswith("Unknown("))

        # v1.6.14: Track no-data state — when NMS itself reports "-Data Unavailable-"
        # / "Uncharted" for a system (some systems have no economy/conflict/lifeform
        # values regardless of scan progress), we must NOT run the struct fallbacks for
        # those fields. Struct access reads the same memory region the direct reads
        # already rejected and would silently fabricate plausible values.
        system_no_data = False

        if sys_data_addr and sys_data_addr > 0x10000:
            try:
                direct = self._read_system_data_from_struct(sys_data)
                # 2.0.3: no-data signal is a race value past the real enum end (0-8);
                # 7 = None_ is a legitimate uninhabited system, NOT a no-data marker.
                # 2.1.0: the raw race rides along from the same struct read.
                direct_race_raw = direct.get("_race_raw", -1)
                if direct_race_raw > 8 or direct_race_raw < 0:
                    system_no_data = True
                    logger.debug(f"  System has no economy/conflict/lifeform data (race_raw={direct_race_raw})")

                if not _is_unresolved(direct.get("star_color")):
                    result["star_color"] = direct["star_color"]
                if not _is_unresolved(direct.get("economy_type")):
                    result["economy_type"] = direct["economy_type"]
                if not _is_unresolved(direct.get("economy_strength")):
                    result["economy_strength"] = direct["economy_strength"]
                if not _is_unresolved(direct.get("conflict_level")):
                    result["conflict_level"] = direct["conflict_level"]
                if not _is_unresolved(direct.get("dominant_lifeform")):
                    result["dominant_lifeform"] = direct["dominant_lifeform"]
                if direct.get("system_name") and not result["system_name"]:
                    result["system_name"] = direct["system_name"]
                if direct.get("system_seed"):
                    result["system_seed"] = direct["system_seed"]
            except Exception as e:
                logger.debug(f"  Direct system read failed: {e}")

        # 2.0.3: the old star-colour "fallback" read sys_data.Class — that is
        # cGcSolarSystemClass (Default/Initial/Anomaly/GameStart), unrelated to star
        # colour, and its near-universal value 0 ("Default") was mapped to "Yellow",
        # silently laundering every failed/stale read into a wrong answer. Removed:
        # an unresolved star colour now stays "Unknown" (honest absence).

        # 2.1.1: the 1.x economy/conflict/lifeform fallbacks are GONE. They called
        # _safe_enum, which returns the raw enum NAME, so whenever they fired they wrote
        # vocabulary the catalog does not use — "HighTech"/"Fusion" into economy_type,
        # "Poor"/"Wealthy" into a field whose scale is T1-T4, and "Default" into
        # conflict_level. Those are exactly the bugs the 2026-08 audit found and that
        # 2.0.3 was supposed to have fixed; they survived in this path. Since the upload
        # sanity gate refuses any value outside the display tables, a fired fallback
        # could only ever turn a good capture into a refused one.
        #
        # An unresolved field now stays "Unknown", which is honest absence and what the
        # no_trade_data handling below already expects.

        # v1.6.14 (Option B): For systems NMS flags as no-data, omit the four trade/conflict/
        # lifeform fields from the payload entirely instead of sending "Unknown" strings.
        # Backend defaults missing fields to "Unknown" and frontend can render the
        # `no_trade_data` flag as "-Data Unavailable-" / "Uncharted" specifically.
        if system_no_data:
            for key in ("economy_type", "economy_strength", "conflict_level", "dominant_lifeform"):
                result.pop(key, None)
            result["no_trade_data"] = True
            logger.debug("  System properties: economy/conflict/lifeform omitted (no_trade_data=True)")

        return result

    def _snapshot_system_properties(self):
        """v1.9.7: Snapshot system-level data WHILE the current system is fresh in memory.

        Stored on self._current_system_snapshot. _save_current_system_to_batch reads this
        snapshot instead of re-reading sys_data at save time (which by then reflects the
        NEXT system because NMS recycles sys_data memory for the new system before our
        save runs in the next on_system_generate).

        Safe to call multiple times - later calls override earlier ones with whatever
        memory has populated by then. Best to call it both immediately after the new
        solar_system is cached AND from on_creature_roles_generate as a refresh.
        """
        if self._cached_solar_system is None:
            return
        try:
            sys_data = self._cached_solar_system.mSolarSystemData
            props = self._extract_system_properties(sys_data)
            # Also snapshot planet count, prime planets, and the fresh sys_data_addr so
            # the batch save can use the same direct-memory codepath without re-resolving.
            planets_count = None
            prime_planets = None
            try:
                # 2.1.0: counts come from the generated struct fields, not offsets.
                pc = int(sys_data.Planets)
                pp = int(sys_data.PrimePlanets)
                if 0 < pc <= 6:
                    planets_count = pc
                if 0 <= pp <= 6:
                    prime_planets = pp
            except Exception as e:
                logger.debug(f"  [SNAPSHOT] planet counts unavailable: {e}")
            props["_planets_count"] = planets_count
            props["_prime_planets"] = prime_planets

            # 2.0.3: identity gate. NMS recycles this sys_data object for EVERY system it
            # generates, and the creature-roles hook (which refreshes this snapshot) also
            # fires for nearby systems during galaxy-map browsing. Without this check a
            # refresh could stamp a NEIGHBOURING system's star/economy/conflict/lifeform
            # onto the snapshot — the DB-wide audit measured exactly that (~5% of extractor
            # system fields wrong even with correct vocab). Lock the snapshot to the seed
            # of the system we warped into; reject refreshes from any other generation.
            seed = props.get("system_seed") or 0
            identity = getattr(self, "_snapshot_identity_seed", 0)
            if identity and seed and seed != identity:
                # 2.0.4: also flag the state so the capture hook can skip phantom
                # planet captures from this foreign generation. Piggybacks on this
                # existing, production-proven seed read — no new memory access.
                self._foreign_generation_active = True
                logger.warning(
                    f"  [SNAPSHOT] REJECTED refresh: sys_data seed {seed:#x} != current "
                    f"system {identity:#x} (nearby-system generation) — keeping prior snapshot"
                )
                return
            if not identity and seed:
                self._snapshot_identity_seed = seed
            self._foreign_generation_active = False

            self._current_system_snapshot = props
            logger.info(
                f"  [SNAPSHOT] system_props: star={props.get('star_color')}, "
                f"economy={props.get('economy_type')}/{props.get('economy_strength')}, "
                f"conflict={props.get('conflict_level')}, "
                f"lifeform={props.get('dominant_lifeform')}, "
                f"no_data={props.get('no_trade_data', False)}, "
                f"planets={planets_count}"
            )
        except Exception as e:
            logger.warning(f"  [SNAPSHOT] system_props snapshot failed: {e}")

    # ---- 2.1.1: game mode / difficulty from typed reads only ------------------
    #
    # Two typed sources, no literal offsets:
    #   * cGcApplication.meGameMode (nmspy types.py, enum cGcGameMode) — live and
    #     always readable in-game: Normal / Creative / Survival / Permadeath /
    #     Seasonal. The authority for `reality`.
    #   * cGcPlayerStateData.DifficultyState — the save struct the game hands to the
    #     cGcPlayerState.LoadFromData / SaveToData hooks (typed pointer). .Preset is
    #     the difficulty preset (adds Relaxed / Custom); .Settings.GroundCombatTimers
    #     is the index into the per-difficulty planet arrays — exact on Custom.
    # The old path read player_state + 0xE630 + 0x50 + 0x3210: a hand offset into
    # cGcSeasonalGameModeData.DifficultySettingPreset — the SEASON template's preset,
    # not the player's — which is why 34,007 of 34,101 prod rows say Normal.

    def _note_difficulty_state(self, lData, source: str = "") -> bool:
        """Read DifficultyState from a cGcPlayerStateData pointer (a hook argument).
        Returns True when a usable preset was read."""
        try:
            addr = get_addressof(lData)
            if not addr:
                return False
            psd = map_struct(addr, nmse.cGcPlayerStateData)
            ds = psd.DifficultyState
            preset_raw = self._enum_raw(ds.Preset)
            timers_raw = self._enum_raw(ds.Settings.GroundCombatTimers)
        except Exception as e:
            logger.debug(f"[GAME_MODE] DifficultyState unreadable ({source}): {e}")
            return False
        preset = GAME_MODE_PRESETS.get(preset_raw, f"Unknown({preset_raw})")
        if preset == "Invalid" or preset.startswith("Unknown("):
            logger.debug(f"[GAME_MODE] preset raw={preset_raw} ignored ({source})")
            return False
        changed = (preset != self._game_mode_preset) or (timers_raw != self._combat_timer_index)
        self._game_mode_preset = preset
        if 0 <= timers_raw <= 3:
            self._combat_timer_index = timers_raw
        self._game_mode = preset
        if changed:
            logger.info(f"[GAME_MODE] preset={preset} combat_timers={timers_raw} via {source}")
        return True

    def _read_app_game_mode(self) -> str:
        """cGcApplication.meGameMode as its enum name; '' when unreadable."""
        try:
            app = gameData.GcApplication
            if app is None:
                return ""
            raw = int(app.meGameMode)
        except Exception as e:
            logger.debug(f"[GAME_MODE] meGameMode unreadable: {e}")
            return ""
        return APP_GAME_MODES.get(raw, f"Unknown({raw})")

    def _detect_game_mode(self) -> str:
        """Refresh game_mode from the typed sources. Returns the game mode, or ''
        while nothing has been read yet (the upload then omits the field rather
        than claim Normal)."""
        app_mode = self._read_app_game_mode()
        if app_mode and app_mode != self._app_game_mode:
            logger.info(f"[GAME_MODE] application mode: {app_mode}")
            self._app_game_mode = app_mode
        if self._game_mode_preset:
            self._game_mode = self._game_mode_preset
        elif app_mode in GAME_MODE_PRESET_NAMES:
            self._game_mode = app_mode          # Normal / Creative / Survival / Permadeath
        return self._game_mode

    def _reality(self) -> str:
        """Permadeath iff either typed source says so (a Permadeath save under a
        Custom preset is still Permadeath); Normal otherwise."""
        if self._app_game_mode == "Permadeath" or self._game_mode_preset == "Permadeath":
            return "Permadeath"
        return "Normal"

    def _get_difficulty_index(self) -> int:
        """Index into SentinelsPerDifficulty / GroundCombatDataPerDifficulty (4 slots):
        the typed GroundCombatTimers option once read, the preset fallback map before."""
        idx = self._combat_timer_index
        if isinstance(idx, int) and 0 <= idx <= 3:
            return idx
        return GAME_MODE_TO_DIFFICULTY_INDEX.get(self._game_mode, 2)
