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

from capture.offsets import *  # noqa: F401,F403


class SystemReadMixin:
    """System-level property reads + snapshot + gamemode detection (REVIVED — dead code in 1.x)."""

    # ---- source lines 1433-1506: _read_system_data_direct ----
    def _read_system_data_direct(self, sys_data_addr: int) -> dict:
        """Read solar system data using direct memory offsets."""
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
        }

        try:
            # Read system name (128-byte fixed string at offset 0x2274)
            system_name = self._read_string(sys_data_addr, SolarSystemDataOffsets.NAME, max_len=128)
            if system_name:
                result["system_name"] = system_name
                logger.debug(f"  [DIRECT] System name: {system_name}")

            # Read planet counts
            result["planet_count"] = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
            result["prime_planets"] = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
            logger.debug(f"  [DIRECT] Planet count: {result['planet_count']}, Prime: {result['prime_planets']}")

            # Read star type (now called star_color)
            star_type_val = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.STAR_TYPE)
            result["star_color"] = STAR_TYPES.get(star_type_val, f"Unknown({star_type_val})")
            logger.debug(f"  [DIRECT] Star color: {result['star_color']} (raw: {star_type_val})")

            # Read trading data (economy)
            trading_addr = sys_data_addr + SolarSystemDataOffsets.TRADING_DATA
            trading_class = self._read_uint32(trading_addr, TradingDataOffsets.TRADING_CLASS)
            wealth_class = self._read_uint32(trading_addr, TradingDataOffsets.WEALTH_CLASS)
            result["economy_type"] = TRADING_CLASSES.get(trading_class, f"Unknown({trading_class})")
            result["economy_strength"] = WEALTH_CLASSES.get(wealth_class, f"Unknown({wealth_class})")
            logger.debug(f"  [DIRECT] Economy: {result['economy_type']} / {result['economy_strength']} (raw: {trading_class}/{wealth_class})")

            # Read conflict data
            conflict_addr = sys_data_addr + SolarSystemDataOffsets.CONFLICT_DATA
            conflict_val = self._read_uint32(conflict_addr, ConflictDataOffsets.CONFLICT_LEVEL)
            result["conflict_level"] = CONFLICT_LEVELS.get(conflict_val, f"Unknown({conflict_val})")
            logger.debug(f"  [DIRECT] Conflict: {result['conflict_level']} (raw: {conflict_val})")

            # Read dominant race
            race_val = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
            result["dominant_lifeform"] = ALIEN_RACES.get(race_val, f"Unknown({race_val})")
            logger.debug(f"  [DIRECT] Race: {result['dominant_lifeform']} (raw: {race_val})")

            # 2.0.3: cGcAlienRace runs 0-8 (Traders, Warriors, Explorers, Robots, Atlas,
            # Diplomats, Exotics, None_, Builders). 7 = None_ is the game's own value for
            # uninhabited systems — the DB-wide audit proved the old `> 6` guard was wiping
            # economy/conflict/lifeform on every legitimately uninhabited system. Absence
            # of inhabitants is real data ("None"), matching manual-upload vocabulary.
            # Only values past the enum's end (>8) indicate a garbage/no-data read.
            if race_val == 7:
                logger.debug("  [DIRECT] Uninhabited system (race=None_) — recording absence")
                result["economy_type"] = "None"
                result["economy_strength"] = "None"
                result["conflict_level"] = "None"
                result["dominant_lifeform"] = "None"
            elif race_val > 8:
                logger.debug(f"  [DIRECT] Race value out of enum range (race={race_val}) — no-data read")
                result["economy_type"] = "Unknown"
                result["economy_strength"] = "Unknown"
                result["conflict_level"] = "Unknown"
                result["dominant_lifeform"] = "Unknown"

        except Exception as e:
            logger.error(f"Direct system data read failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return result

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

        # =====================================================
        # Get system name from mGameState notification string
        # The game stores "In the {system_name} system" at offset 0x38
        # =====================================================
        try:
            game_state = gameData.game_state
            if game_state:
                game_state_addr = get_addressof(game_state)
                if game_state_addr and game_state_addr != 0:
                    notification_str = self._read_string(game_state_addr, 0x38, max_len=256)
                    if notification_str:
                        match = re.match(r"In the (.+) system", notification_str)
                        if match:
                            extracted_name = match.group(1).strip()
                            if extracted_name:
                                result["system_name"] = extracted_name
                                logger.info(f"  System name: '{extracted_name}'")
        except Exception as e:
            logger.debug(f"System name extraction failed: {e}")

        # Star color mapping (enum names to clean values)
        # Struct fallback mapping (enum name strings from NMS.py → clean color names)
        # Numeric keys match cGcGalaxyStarTypes enum: Yellow=0, Green=1, Blue=2, Red=3, Purple=4
        STAR_COLOR_MAP = {
            'Yellow': 'Yellow', 'Yellow_': 'Yellow', 'yellow': 'Yellow', '0': 'Yellow',
            'Green': 'Green', 'Green_': 'Green', 'green': 'Green', '1': 'Green',
            'Blue': 'Blue', 'Blue_': 'Blue', 'blue': 'Blue', '2': 'Blue',
            'Red': 'Red', 'Red_': 'Red', 'red': 'Red', '3': 'Red',
            'Purple': 'Purple', 'Purple_': 'Purple', 'purple': 'Purple', '4': 'Purple',
            'Default': 'Yellow', 'Default_': 'Yellow',  # Default is Yellow
        }

        # Dominant lifeform mapping (enum names to clean values)
        LIFEFORM_MAP = {
            'Traders': 'Gek', 'Traders_': 'Gek', 'Gek': 'Gek', '0': 'Gek',
            'Warriors': "Vy'keen", 'Warriors_': "Vy'keen", "Vy'keen": "Vy'keen", 'Vykeen': "Vy'keen", '1': "Vy'keen",
            'Explorers': 'Korvax', 'Explorers_': 'Korvax', 'Korvax': 'Korvax', '2': 'Korvax',
            'Robots': 'None', 'Robots_': 'None', '3': 'None',
            'Atlas': 'None', 'Atlas_': 'None', '4': 'None',
            'Diplomats': 'None', 'Diplomats_': 'None', '5': 'None',
            'None': 'None', 'None_': 'None', '6': 'None',
        }

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
                direct = self._read_system_data_direct(sys_data_addr)
                # 2.0.3: no-data signal is a race value past the real enum end (0-8);
                # 7 = None_ is a legitimate uninhabited system, NOT a no-data marker.
                direct_race_raw = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
                if direct_race_raw > 8:
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
            except Exception as e:
                logger.debug(f"  Direct system read failed: {e}")

        # 2.0.3: the old star-colour "fallback" read sys_data.Class — that is
        # cGcSolarSystemClass (Default/Initial/Anomaly/GameStart), unrelated to star
        # colour, and its near-universal value 0 ("Default") was mapped to "Yellow",
        # silently laundering every failed/stale read into a wrong answer. Removed:
        # an unresolved star colour now stays "Unknown" (honest absence).

        # Economy / conflict / lifeform struct fallbacks are SKIPPED for no-data systems —
        # the struct fields read the same memory and would fabricate plausible-looking values.
        if not system_no_data:
            try:
                if hasattr(sys_data, 'TradingData'):
                    trading = sys_data.TradingData
                    if _is_unresolved(result["economy_type"]) and hasattr(trading, 'TradingClass'):
                        result["economy_type"] = self._safe_enum(trading.TradingClass)
                    if _is_unresolved(result["economy_strength"]) and hasattr(trading, 'WealthClass'):
                        result["economy_strength"] = self._safe_enum(trading.WealthClass)
                    if _is_unresolved(result["conflict_level"]) and hasattr(trading, 'ConflictLevel'):
                        result["conflict_level"] = self._safe_enum(trading.ConflictLevel)
            except Exception:
                pass

            try:
                if _is_unresolved(result["conflict_level"]) and hasattr(sys_data, 'ConflictData'):
                    result["conflict_level"] = self._safe_enum(sys_data.ConflictData)
            except Exception:
                pass

            try:
                if hasattr(sys_data, 'InhabitingRace') and _is_unresolved(result["dominant_lifeform"]):
                    raw_race = self._safe_enum(sys_data.InhabitingRace)
                    result["dominant_lifeform"] = LIFEFORM_MAP.get(raw_race, LIFEFORM_MAP.get(raw_race.rstrip('_'), 'None'))
                    logger.debug(f"  Dominant lifeform: raw='{raw_race}' -> '{result['dominant_lifeform']}'")
            except Exception:
                pass

        try:
            if hasattr(sys_data, 'Seed') and hasattr(sys_data.Seed, 'Seed'):
                result["system_seed"] = self._safe_int(sys_data.Seed.Seed)
        except Exception:
            pass

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
            sys_data_addr = get_addressof(sys_data)
            planets_count = None
            prime_planets = None
            if sys_data_addr and sys_data_addr > 0x10000:
                pc = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
                pp = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
                if 0 < pc <= 6:
                    planets_count = pc
                if 0 <= pp <= 6:
                    prime_planets = pp
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

    # ---- source lines 4000-4050: _detect_game_mode (revived) + _get_difficulty_index ----
    def _detect_game_mode(self) -> str:
        """Auto-detect the player's game mode / difficulty preset from memory.

        Reads cGcDifficultySettingPreset from the player state's SeasonData.
        Path: player_state base + 0xE630 (mPhotoModeSettings/CommonStateData)
              + 0x50 (SeasonData) + 0x3210 (DifficultySettingPreset)
        Total offset from player_state: 0x11890

        Returns: "Normal", "Creative", "Relaxed", "Survival", "Permadeath", or "Custom"
        """
        try:
            player_state = gameData.player_state
            if not player_state:
                logger.debug("[GAME_MODE] No player_state available")
                return self._game_mode  # Keep last known

            ps_addr = get_addressof(player_state)
            if not ps_addr:
                logger.debug("[GAME_MODE] Could not get player_state address")
                return self._game_mode

            # Read DifficultySettingPreset enum (uint32)
            # Offset: 0xE630 (PhotoModeSettings) + 0x50 (SeasonData) + 0x3210 (DifficultySettingPreset)
            preset_val = self._read_uint32(ps_addr, 0xE630 + 0x50 + 0x3210)
            mode = GAME_MODE_PRESETS.get(preset_val, "Unknown")

            if mode in ("Invalid", "Unknown"):
                logger.debug(f"[GAME_MODE] Got preset_val={preset_val} ({mode}), keeping {self._game_mode}")
                return self._game_mode

            if mode != self._game_mode:
                logger.info(f"[GAME_MODE] Detected: {mode} (was {self._game_mode})")
            self._game_mode = mode
            return mode

        except Exception as e:
            logger.debug(f"[GAME_MODE] Detection failed: {e}")
            return self._game_mode

    def _get_difficulty_index(self) -> int:
        """Get the per-difficulty array index based on detected game mode.

        Post-Worlds Part 1 index mapping:
          [0] = Casual/Creative
          [1] = Relaxed
          [2] = Normal (also Custom)
          [3] = Survival/Permadeath

        Used for SentinelsPerDifficulty and GroundCombatDataPerDifficulty arrays.
        """
        return GAME_MODE_TO_DIFFICULTY_INDEX.get(self._game_mode, 2)
