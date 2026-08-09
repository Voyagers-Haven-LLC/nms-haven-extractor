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
from payload.tables import translate_resource, clean_weather_string  # noqa: F401


class CaptureHooksMixin:
    """The capture hook bodies (decorators live on the final class; these are the verbatim implementations)."""

    # ---- source lines 1762-1811: on_system_generate ----
    def _on_system_generate_impl(self, this, lbUseSettingsFile, lSeed):
        """Fires AFTER solar system generation - data is now ready."""
        logger.info("")
        logger.info("--- Warp detected ---")
        self._status_display = "Capturing..."

        # Save previous system to batch BEFORE clearing (preserves data from system we just left)
        if self._batch_mode_enabled and self._captured_planets and not self._system_saved_to_batch:
            self._save_current_system_to_batch()

        addr = get_addressof(this)
        if addr == 0:
            return

        try:
            self._cached_solar_system = map_struct(addr, nms.cGcSolarSystem)
            self._pending_extraction = True

            # v1.9.7: Reset and take initial system-props snapshot for the NEW system.
            # The previous system's snapshot was already consumed by the save_to_batch call
            # above (or wasn't needed). Take the new snapshot while memory is fresh; the
            # planet-capture hook will refresh it as more data populates.
            # 2.0.3: reset the identity seed FIRST — the snapshot function adopts the new
            # system's seed on this fresh read and rejects refreshes from any other system.
            self._current_system_snapshot = None
            self._snapshot_identity_seed = 0
            self._snapshot_system_properties()

            # Coord resolution: mUniverseAddress primary, player_state secondary.
            # System name is procedurally generated from seed, not stored in Name field.
            # The Name field is only populated for user-renamed systems.
            # v1.8.1 (Fix 4): Clear prior coords on new-system event so _maybe_upgrade_coords
            # doesn't hold onto stale Euclid-from-previous-system data if this initial
            # resolution fails.
            self._current_system_name = None
            self._current_system_coords = None
            self._maybe_upgrade_coords()

            if self._current_system_coords is None:
                logger.debug("  No coordinates yet - will try again during planet capture")
            elif self._current_system_coords.get('from_fallback', False):
                logger.debug("  Initial coords from fallback — will retry primary")

            self._captured_planets.clear()
            self._capture_enabled = True
            self._system_saved_to_batch = False

            logger.info(f"  Capturing planets... (batch: {len(self._batch_systems)})")
        except Exception as e:
            logger.error(f"Failed to cache solar system: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # ---- source lines 1819-2317: on_creature_roles_generate (the 499-line planet capture) ----
    def _on_creature_roles_generate_impl(self, this, lPlanetData, lUA):
        """
        Captures planet data when GenerateCreatureRoles is called.

        This hook fires for EACH planet in the system, receiving the full
        cGcPlanetData structure with Flora, Fauna, Sentinels, Weather, etc.

        IMPORTANT: We limit capture to 6 planets max because the hook also
        fires for nearby systems during galaxy discovery. Only the first 6
        belong to the current system.
        """
        # Note: lUA parameter is unreliable. Use mUniverseAddress from planet discovery data instead.

        if not self._capture_enabled:
            return

        # v1.8.1 (Fix 4): Always attempt upgrade — even if we already have coords from the
        # fallback path, try to replace them with primary mUniverseAddress data as soon as
        # it becomes readable. Guard on "not yet captured any planets" removed because the
        # race window can extend past the first capture.
        self._maybe_upgrade_coords()

        # v1.9.7: Refresh system-props snapshot while we KNOW the current system's sys_data
        # is the one in memory (we're inside its planet generation). The first snapshot in
        # on_system_generate may have fired before economy/conflict/lifeform populated.
        self._snapshot_system_properties()

        # v1.6.11: Hook-fire limit enforced below per unique planet name to handle
        # the case where the hook fires for the same planet twice (was filling the
        # 6-slot quota with duplicates and missing a real planet).

        try:
            # Get the planet data pointer
            planet_data_addr = get_addressof(lPlanetData)
            if planet_data_addr == 0:
                logger.debug("GenerateCreatureRoles: lPlanetData is NULL")
                return

            # Map to cGcPlanetData structure (using global imports)
            planet_data = map_struct(planet_data_addr, nmse.cGcPlanetData)

            # planet_key is assigned after name extraction below (name-based dedup)

            # Extract Flora (Life field at offset 0x3458)
            flora_raw = 0
            flora_name = "Unknown"
            try:
                if hasattr(planet_data, 'Life'):
                    life_val = planet_data.Life
                    if hasattr(life_val, 'value'):
                        flora_raw = life_val.value
                    else:
                        flora_raw = int(life_val) if life_val is not None else 0
                    flora_name = self.FLORA_LEVELS.get(flora_raw, f"Unknown({flora_raw})")
            except Exception as e:
                logger.debug(f"Flora extraction failed: {e}")

            # Extract Fauna (CreatureLife field at offset 0x344C)
            fauna_raw = 0
            fauna_name = "Unknown"
            try:
                if hasattr(planet_data, 'CreatureLife'):
                    creature_val = planet_data.CreatureLife
                    if hasattr(creature_val, 'value'):
                        fauna_raw = creature_val.value
                    else:
                        fauna_raw = int(creature_val) if creature_val is not None else 0
                    fauna_name = self.FAUNA_LEVELS.get(fauna_raw, f"Unknown({fauna_raw})")
            except Exception as e:
                logger.debug(f"Fauna extraction failed: {e}")

            # Extract Sentinels from GroundCombatDataPerDifficulty
            # Array index matches reality mode: Normal=[2], Permadeath=[3]
            sentinel_raw = 0
            sentinel_name = "Unknown"
            try:
                if hasattr(planet_data, 'GroundCombatDataPerDifficulty'):
                    combat_data_array = planet_data.GroundCombatDataPerDifficulty
                    if hasattr(combat_data_array, '__getitem__'):
                        combat_data = combat_data_array[self._get_difficulty_index()]
                        if hasattr(combat_data, 'SentinelLevel'):
                            sentinel_val = combat_data.SentinelLevel
                            if hasattr(sentinel_val, 'value'):
                                sentinel_raw = sentinel_val.value
                            else:
                                sentinel_raw = int(sentinel_val) if sentinel_val is not None else 0
                            sentinel_name = self.SENTINEL_LEVELS.get(sentinel_raw, f"Unknown({sentinel_raw})")
                    elif hasattr(combat_data_array, 'SentinelLevel'):
                        # Fallback: maybe it's not an array after all
                        sentinel_val = combat_data_array.SentinelLevel
                        if hasattr(sentinel_val, 'value'):
                            sentinel_raw = sentinel_val.value
                        else:
                            sentinel_raw = int(sentinel_val) if sentinel_val is not None else 0
                        sentinel_name = self.SENTINEL_LEVELS.get(sentinel_raw, f"Unknown({sentinel_raw})")
            except Exception as e:
                logger.debug(f"Sentinel extraction failed: {e}")

            # Extract Biome, BiomeSubType, and Size from GenerationData
            # cGcPlanetData.GenerationData contains cGcPlanetGenerationIntermediateData
            # which has Biome at offset 0x138, BiomeSubType at 0x13C, and Size at 0x144
            biome_raw = -1
            biome_name = "Unknown"
            biome_subtype_raw = -1
            biome_subtype_name = "Unknown"
            planet_size_raw = -1
            planet_size_name = "Unknown"
            is_moon = False
            try:
                if hasattr(planet_data, 'GenerationData'):
                    gen_data = planet_data.GenerationData
                    # Extract Biome
                    if hasattr(gen_data, 'Biome'):
                        biome_val = gen_data.Biome
                        if hasattr(biome_val, 'value'):
                            biome_raw = biome_val.value
                        else:
                            biome_raw = int(biome_val) if biome_val is not None else -1
                        biome_name = BIOME_TYPES.get(biome_raw, f"Unknown({biome_raw})")
                    # Extract BiomeSubType
                    if hasattr(gen_data, 'BiomeSubType'):
                        subtype_val = gen_data.BiomeSubType
                        if hasattr(subtype_val, 'value'):
                            biome_subtype_raw = subtype_val.value
                        else:
                            biome_subtype_raw = int(subtype_val) if subtype_val is not None else -1
                        biome_subtype_name = BIOME_SUBTYPES.get(biome_subtype_raw, f"Unknown({biome_subtype_raw})")
                    # Extract Size from GenerationData (offset 0x144)
                    # This is the RELIABLE source for planet_size - direct memory read gives garbage
                    if hasattr(gen_data, 'Size'):
                        size_val = gen_data.Size
                        if hasattr(size_val, 'value'):
                            planet_size_raw = size_val.value
                        else:
                            planet_size_raw = int(size_val) if size_val is not None else -1
                        is_moon = (planet_size_raw == 3)  # Moon = 3
                        # For moons, use "Small" instead of "Moon" to avoid duplicate badge
                        if is_moon:
                            planet_size_name = "Small"
                        else:
                            planet_size_name = PLANET_SIZES.get(planet_size_raw, f"Unknown({planet_size_raw})")
                    logger.debug(f"    Biome={biome_name}, SubType={biome_subtype_name}, Size={planet_size_name}")
            except Exception as e:
                logger.debug(f"Biome extraction from GenerationData failed: {e}")

            # Extract resources - clean to remove garbage characters
            common_resource = ""
            uncommon_resource = ""
            rare_resource = ""
            try:
                if hasattr(planet_data, 'CommonSubstanceID'):
                    val = str(planet_data.CommonSubstanceID) or ""
                    # Only keep printable ASCII
                    common_resource = ''.join(c for c in val if c.isprintable() and ord(c) < 128)
                    if common_resource and (len(common_resource) < 2 or not common_resource[0].isalpha()):
                        common_resource = ""
                if hasattr(planet_data, 'UncommonSubstanceID'):
                    val = str(planet_data.UncommonSubstanceID) or ""
                    uncommon_resource = ''.join(c for c in val if c.isprintable() and ord(c) < 128)
                    if uncommon_resource and (len(uncommon_resource) < 2 or not uncommon_resource[0].isalpha()):
                        uncommon_resource = ""
                if hasattr(planet_data, 'RareSubstanceID'):
                    val = str(planet_data.RareSubstanceID) or ""
                    rare_resource = ''.join(c for c in val if c.isprintable() and ord(c) < 128)
                    if rare_resource and (len(rare_resource) < 2 or not rare_resource[0].isalpha()):
                        rare_resource = ""
            except Exception as e:
                logger.debug(f"Resource extraction failed: {e}")

            # v1.4.5: Extract special resource flags from ExtraResourceHints + HasScrap
            extra_resource_hints = []
            has_scrap = False
            try:
                if hasattr(planet_data, 'ExtraResourceHints'):
                    hints_arr = planet_data.ExtraResourceHints
                    if hints_arr is not None and hasattr(hints_arr, '__len__'):
                        arr_len = len(hints_arr)
                        if arr_len > 0:
                            # v1.6.12: Hint detail stays at DEBUG to avoid log spam from galaxy-map
                            # hook fires (~60/sec). Actual per-planet hint logging happens inside
                            # the final CAPTURED PLANET block.
                            logger.debug(f"    [HINTS] ExtraResourceHints: {arr_len} entries")
                            for hi in range(arr_len):
                                try:
                                    hint = hints_arr[hi]
                                    if hasattr(hint, 'Hint'):
                                        raw_hint = hint.Hint
                                        hint_id = str(raw_hint) or ""
                                        hint_id = ''.join(c for c in hint_id if c.isprintable() and ord(c) < 128).strip()
                                        logger.debug(f"    [HINTS] [{hi}] Hint='{hint_id}'")
                                        if hint_id and len(hint_id) >= 2:
                                            extra_resource_hints.append(hint_id)
                                except Exception as he:
                                    logger.debug(f"    [HINTS] [{hi}] exception: {he}")
                        else:
                            logger.debug(f"    [HINTS] ExtraResourceHints: empty")
                if hasattr(planet_data, 'HasScrap'):
                    has_scrap = bool(planet_data.HasScrap)
                    if has_scrap:
                        logger.debug(f"    [HINTS] HasScrap=True")
            except Exception as e:
                logger.debug(f"    [HINTS] ExtraResourceHints read failed: {e}")

            # v1.4.6: Direct memory read fallback for ExtraResourceHints
            # ExtraResourceHints is at offset 0x3310 in cGcPlanetData
            # cTkDynamicArray layout: pointer(8) + count(4) + capacity(4) = 16 bytes
            # cGcPlanetDataResourceHint: Hint TkID(16) + Icon TkID(16) = 32 bytes per element
            if not extra_resource_hints and planet_data_addr:
                try:
                    hints_offset = 0x3310  # Confirmed offset from nmspy exported_types
                    arr_ptr = self._read_uint64(planet_data_addr, hints_offset)
                    arr_count = self._read_uint32(planet_data_addr, hints_offset + 8)
                    if arr_ptr and arr_ptr > 0x10000 and 0 < arr_count <= 10:
                        logger.info(f"    [HINTS-DIRECT] Found {arr_count} hints at 0x3310")
                        for hi in range(arr_count):
                            elem_addr = arr_ptr + (hi * 32)  # 32 bytes per element
                            hint_str = self._read_string(elem_addr, 0, max_len=16)
                            if hint_str:
                                logger.info(f"    [HINTS-DIRECT] [{hi}] Hint='{hint_str}'")
                                extra_resource_hints.append(hint_str)
                        if extra_resource_hints:
                            logger.info(f"    [HINTS-DIRECT] Read {len(extra_resource_hints)} hints via direct memory")
                except Exception as e:
                    logger.info(f"    [HINTS-DIRECT] Direct memory fallback failed: {e}")

            # Extract weather from cGcPlanetData.Weather.WeatherType
            # This uses the actual Weather structure (offset 0x1C00) with enum values
            # Works for ALL planets, not just visited ones like PlanetInfo.Weather
            weather = ""
            weather_raw = -1
            storm_frequency = ""
            storm_raw = -1  # Raw value for contextual weather lookup
            try:
                if hasattr(planet_data, 'Weather'):
                    weather_data = planet_data.Weather
                    # Get WeatherType enum
                    if hasattr(weather_data, 'WeatherType'):
                        weather_val = weather_data.WeatherType
                        if hasattr(weather_val, 'value'):
                            weather_raw = weather_val.value
                        else:
                            weather_raw = int(weather_val) if weather_val is not None else -1
                        weather = WEATHER_OPTIONS.get(weather_raw, f"Unknown({weather_raw})")
                        logger.debug(f"    Weather: {weather}")
                    # Also get storm frequency
                    if hasattr(weather_data, 'StormFrequency'):
                        storm_val = weather_data.StormFrequency
                        if hasattr(storm_val, 'value'):
                            storm_raw = storm_val.value
                        else:
                            storm_raw = int(storm_val) if storm_val is not None else -1
                        storm_frequency = STORM_FREQUENCY.get(storm_raw, f"Unknown({storm_raw})")
            except Exception as e:
                logger.debug(f"Weather extraction from Weather struct failed: {e}")

            # Fallback: Try PlanetInfo.Weather display string if Weather struct failed
            if not weather or weather == "Unknown(-1)":
                try:
                    if hasattr(planet_data, 'PlanetInfo'):
                        info = planet_data.PlanetInfo
                        if hasattr(info, 'Weather'):
                            val = str(info.Weather) or ""
                            fallback_weather = ''.join(c for c in val if c.isprintable() and ord(c) < 128)
                            if fallback_weather and len(fallback_weather) >= 2 and fallback_weather != "None":
                                # Clean up raw weather strings like "weather_glitch 6"
                                weather = clean_weather_string(fallback_weather)
                                logger.debug(f"    Weather (fallback): {weather}")
                except Exception as e:
                    logger.debug(f"Weather fallback extraction failed: {e}")

            # Extract planet Name from cGcPlanetData.Name (offset 0x396E)
            # This is a cTkFixedString0x80 (128 char fixed string)
            planet_name = ""
            try:
                if hasattr(planet_data, 'Name'):
                    name_val = planet_data.Name
                    if name_val is not None:
                        name_str = str(name_val) or ""
                        # Clean to printable ASCII only
                        planet_name = ''.join(c for c in name_str if c.isprintable() and ord(c) < 128)
                        # Validate it looks like a real name
                        if planet_name and (len(planet_name) < 2 or planet_name == "None"):
                            planet_name = ""
                        if planet_name:
                            # v1.6.12: Demoted to DEBUG — final capture summary still logs at INFO
                            logger.debug(f"  Planet: '{planet_name}'")
            except Exception as e:
                logger.debug(f"Planet name extraction failed: {e}")

            # =============================================================
            # Extract actual display strings from PlanetInfo
            # These are the EXACT strings the game shows on discovery pages
            # PlanetInfo.Flora (0x280), Fauna (0x200), SentinelsPerDifficulty (0x0)
            # =============================================================
            flora_display = ""
            fauna_display = ""
            sentinel_display = ""
            weather_display = ""
            planet_description = ""       # v1.4.0: Biome adjective text ID
            planet_type_display = ""      # v1.4.0: Planet type display string
            is_weather_extreme = False    # v1.4.0: Extreme weather flag
            try:
                if hasattr(planet_data, 'PlanetInfo'):
                    info = planet_data.PlanetInfo

                    # v1.4.5: Resolve adjectives immediately at capture time
                    # Previously stored raw text IDs and relied on manual button/auto-refresh

                    # Flora display string - cTkFixedString0x80 at offset 0x280
                    if hasattr(info, 'Flora'):
                        val = str(info.Flora) or ""
                        flora_display = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if flora_display and flora_display != "None" and len(flora_display) >= 2:
                            flora_display = self._resolve_adjective(flora_display, 'flora')
                            logger.info(f"    [DISPLAY] Flora: '{flora_display}'")
                        else:
                            flora_display = ""

                    # Fauna display string - cTkFixedString0x80 at offset 0x200
                    if hasattr(info, 'Fauna'):
                        val = str(info.Fauna) or ""
                        fauna_display = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if fauna_display and fauna_display != "None" and len(fauna_display) >= 2:
                            fauna_display = self._resolve_adjective(fauna_display, 'fauna')
                            logger.info(f"    [DISPLAY] Fauna: '{fauna_display}'")
                        else:
                            fauna_display = ""

                    # Sentinel display string from SentinelsPerDifficulty
                    # Index based on reality mode: Normal=[2], Permadeath=[3]
                    if hasattr(info, 'SentinelsPerDifficulty'):
                        sent_arr = info.SentinelsPerDifficulty
                        if hasattr(sent_arr, '__getitem__'):
                            val = str(sent_arr[self._get_difficulty_index()]) or ""
                            sentinel_display = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                            if sentinel_display and sentinel_display != "None" and len(sentinel_display) >= 2:
                                sentinel_display = self._resolve_adjective(sentinel_display, 'sentinel')
                                logger.info(f"    [DISPLAY] Sentinel: '{sentinel_display}'")
                            else:
                                sentinel_display = ""

                    # Weather display string - cTkFixedString0x80 at offset 0x480
                    if hasattr(info, 'Weather'):
                        val = str(info.Weather) or ""
                        weather_display = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if weather_display and weather_display != "None" and len(weather_display) >= 2:
                            weather_display = self._resolve_adjective(weather_display, 'weather')
                            logger.info(f"    [DISPLAY] Weather: '{weather_display}'")
                        else:
                            weather_display = ""

                    # v1.4.0: PlanetDescription - biome adjective text ID (e.g., "Paradise Planet")
                    if hasattr(info, 'PlanetDescription'):
                        val = str(info.PlanetDescription) or ""
                        planet_description = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if planet_description and planet_description != "None" and len(planet_description) >= 2:
                            logger.info(f"    [DISPLAY] PlanetDescription: '{planet_description}'")
                        else:
                            planet_description = ""

                    # v1.4.0: PlanetType - planet type display string
                    if hasattr(info, 'PlanetType'):
                        val = str(info.PlanetType) or ""
                        planet_type_display = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if planet_type_display and planet_type_display != "None" and len(planet_type_display) >= 2:
                            logger.info(f"    [DISPLAY] PlanetType: '{planet_type_display}'")
                        else:
                            planet_type_display = ""

                    # v1.4.0: IsWeatherExtreme - differentiates normal vs extreme weather
                    if hasattr(info, 'IsWeatherExtreme'):
                        try:
                            is_weather_extreme = bool(info.IsWeatherExtreme)
                            if is_weather_extreme:
                                logger.info(f"    [DISPLAY] IsWeatherExtreme: True")
                        except:
                            is_weather_extreme = False

            except Exception as e:
                logger.debug(f"PlanetInfo display string extraction failed: {e}")

            # v1.6.11: Key captures by planet name (handles duplicate hook fires, fixes
            # planet/moon swap). Unnamed planets fall back to a slot key.
            is_named = bool(planet_name and planet_name.strip())
            planet_key = planet_name.strip() if is_named else f"_unnamed_{len(self._captured_planets)}"
            is_update = planet_key in self._captured_planets

            # v1.10.2: Count only NAMED captures toward the 6-body cap. The hook fires many
            # times/sec and occasionally with an unreadable name; those nameless fires are
            # stored as '_unnamed_N' phantoms (default Lush/Small). Part A filters them at
            # build time, but if a phantom fire arrives BEFORE the real planets it must not
            # consume the slot that a real planet needs. So: a real (named) planet is never
            # blocked while < 6 named are held — it evicts a phantom if the dict is full;
            # a nameless fire is only kept when there's physical room and never displaces a real.
            if not is_update:
                named_count = sum(1 for c in self._captured_planets.values()
                                  if c.get('planet_name') and str(c.get('planet_name')).strip())
                if is_named:
                    if named_count >= 6:
                        logger.debug(f"    [CAPTURE] At 6 named-planet limit — skipping '{planet_key}'")
                        return
                    if len(self._captured_planets) >= 6:
                        for k, v in list(self._captured_planets.items()):
                            if not (v.get('planet_name') and str(v.get('planet_name')).strip()):
                                del self._captured_planets[k]
                                logger.debug(f"    [CAPTURE] Evicted phantom '{k}' for real planet '{planet_key}'")
                                break
                elif len(self._captured_planets) >= 6:
                    logger.debug(f"    [CAPTURE] At capacity — skipping nameless capture")
                    return

            if is_update:
                logger.debug(f"    [CAPTURE] Updating existing capture for '{planet_key}' (hook fired again)")

            # Store captured planet data
            self._captured_planets[planet_key] = {
                'flora_raw': flora_raw,
                'flora': flora_name,
                'flora_display': flora_display,  # Actual game display string
                'fauna_raw': fauna_raw,
                'fauna': fauna_name,
                'fauna_display': fauna_display,  # Actual game display string
                'sentinel_raw': sentinel_raw,
                'sentinel': sentinel_name,
                'sentinel_display': sentinel_display,  # Actual game display string
                'weather_display': weather_display,  # Actual game display string
                'biome_raw': biome_raw,
                'biome': biome_name,
                'biome_subtype_raw': biome_subtype_raw,
                'biome_subtype': biome_subtype_name,
                'planet_size_raw': planet_size_raw,
                'planet_size': planet_size_name,
                'is_moon': is_moon,
                'common_resource': common_resource,
                'uncommon_resource': uncommon_resource,
                'rare_resource': rare_resource,
                'weather': weather,
                'weather_raw': weather_raw,
                'storm_frequency': storm_frequency,
                'storm_raw': storm_raw,  # Raw value for contextual weather lookup
                'planet_name': planet_name,
                'planet_description': planet_description,      # v1.4.0: Biome adjective text ID
                'planet_type_display': planet_type_display,    # v1.4.0: Planet type display string
                'is_weather_extreme': is_weather_extreme,      # v1.4.0: Extreme weather flag
                'extra_resource_hints': extra_resource_hints,  # v1.4.5: Special resource hint IDs
                'has_scrap': has_scrap,                        # v1.4.5: HasScrap boolean
            }

            # v1.4.5: Set special resource flags from ExtraResourceHints + HasScrap
            for hint_id in extra_resource_hints:
                hint_upper = hint_id.upper()
                translated = translate_resource(hint_upper)
                translated_lower = translated.lower() if translated else ""
                if "ancient bones" in translated_lower or hint_upper in ("FOSSIL1", "FOSSIL2", "CREATURE1", "BONES", "ANCIENT", "UI_BONES_HINT"):
                    self._captured_planets[planet_key]['ancient_bones'] = 1
                if "salvageable scrap" in translated_lower or hint_upper in ("SALVAGE", "SALVAGE1", "TECHFRAG", "UI_SCRAP_HINT"):
                    self._captured_planets[planet_key]['salvageable_scrap'] = 1
                if "storm crystal" in translated_lower or hint_upper in ("STORM1", "STORM_CRYSTAL", "UI_STORM_HINT"):
                    self._captured_planets[planet_key]['storm_crystals'] = 1
                if "gravitino" in translated_lower or hint_upper in ("GRAVITINO", "GRAV_BALL", "UI_GRAV_HINT"):
                    self._captured_planets[planet_key]['gravitino_balls'] = 1
                if "vile brood" in translated_lower or "whispering egg" in translated_lower or hint_upper in ("INFESTATION", "VILEBROOD", "LARVA", "LARVAL", "UI_BUGS_HINT"):
                    self._captured_planets[planet_key]['vile_brood'] = 1
            # v1.4.6: HasScrap from hook time is unreliable (struct offset may have shifted
            # in Worlds Part 1 update, causing false positives). Scrap detection is now
            # handled at extraction time in _extract_single_planet instead.
            if has_scrap:
                logger.debug(f"    [HINTS] HasScrap=True (hook time, deferred to extraction)")
            # Infested biome subtype
            if biome_subtype_name and biome_subtype_name.lower() == "infested":
                self._captured_planets[planet_key]['infested'] = 1
                self._captured_planets[planet_key]['vile_brood'] = 1

            # Compact capture log — full details print at batch save time
            moon_tag = " (moon)" if is_moon else ""
            count = len(self._captured_planets)
            logger.info(f"  Captured {count}: {planet_name or '(unnamed)'}{moon_tag} — {biome_name}")

            # Verbose details at DEBUG for diagnostics
            logger.debug(f"    BiomeSubType: {biome_subtype_name} ({biome_subtype_raw})")
            logger.debug(f"    Size: {planet_size_name} ({planet_size_raw})")
            logger.debug(f"    Flora: {flora_name} ({flora_raw}), Fauna: {fauna_name} ({fauna_raw})")
            logger.debug(f"    Sentinels: {sentinel_name} ({sentinel_raw})")
            logger.debug(f"    Weather: {weather} (raw: {weather_raw}, storms: {storm_frequency})")
            logger.debug(f"    Resources: {common_resource}, {uncommon_resource}, {rare_resource}")
            if planet_description:
                logger.debug(f"    PlanetDescription: '{planet_description}'")
            if extra_resource_hints:
                logger.debug(f"    ExtraResourceHints: {extra_resource_hints}")

            # v1.8.1 (Fix 4): Coord upgrade attempt after each planet capture. Replaces any
            # from_fallback coords with primary mUniverseAddress data once available.
            self._maybe_upgrade_coords()

        except Exception as e:
            logger.error(f"GenerateCreatureRoles capture failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # ---- source lines 2323-2355: on_appview ----
    def _on_appview_impl(self):
        """
        Fires when entering game view - player_state is now available.
        Auto-saves system to batch when this fires.

        Note: This only fires if nmspy internal mods are loaded.
        If not, systems save on next warp or Export Batch click.
        """
        if not self._pending_extraction:
            return

        self._pending_extraction = False

        logger.info("=" * 40)
        logger.info("=== APPVIEW STATE - SYSTEM READY ===")
        logger.info("=" * 40)

        # v1.8.1 (Fix 4): Coord upgrade at APPVIEW — by this point mUniverseAddress should
        # definitely be populated. Replaces any from_fallback coords with primary data.
        logger.info("[APPVIEW] Resolving / upgrading coordinates...")
        self._maybe_upgrade_coords()
        if self._current_system_coords is None:
            logger.warning("[APPVIEW] Could not resolve coordinates from any source")

        # Auto-save to batch when APPVIEW fires (if not already saved)
        if self._batch_mode_enabled and self._captured_planets and not self._system_saved_to_batch:
            self._auto_refresh_for_export()
            self._save_current_system_to_batch()
            self._system_saved_to_batch = True
            self._capture_enabled = False
        elif self._system_saved_to_batch:
            logger.debug(f"[BATCH] Already saved, {len(self._batch_systems)} in batch")

    # ---- source lines 2622-2718: _auto_refresh_for_export ----
    def _auto_refresh_for_export(self):
        """
        v1.4.1: Silently refresh adjectives from PlanetInfo before export.
        Reads PlanetInfo display strings and resolves adjectives without verbose logging.
        Ensures _captured_planets has the latest text IDs resolved through _resolve_adjective().

        v1.9.7: Also refreshes the system-props snapshot. Called from APPVIEW handler
        (last system in batch) and from _save_current_system_to_batch as a safety net.
        If the cached_solar_system is still the current/active system, the snapshot
        captures correct system-level data; if it's already stale, _extract_system_properties
        will read garbage but that's no worse than what happens today.
        """
        # Always attempt a snapshot refresh - cheap, and helps even if planet refresh below skips.
        self._snapshot_system_properties()

        if not self._captured_planets or not self._cached_solar_system:
            return

        try:
            planets = self._cached_solar_system.maPlanets
            if planets is None:
                return

            refreshed = 0
            for index in range(min(6, len(planets))):
                try:
                    planet = planets[index]
                    if planet is None:
                        continue

                    planet_data = None
                    if hasattr(planet, 'mPlanetData'):
                        planet_data = planet.mPlanetData
                    if planet_data is None:
                        continue

                    # v1.6.11: Match memory slot to captured entry BY NAME (not index).
                    # Hook fire order != memory slot order, so old index-based lookup
                    # applied refreshed adjectives to the wrong planet.
                    memory_name = None
                    try:
                        if hasattr(planet_data, 'Name'):
                            n = str(planet_data.Name)
                            if n and n != "None" and len(n.strip()) > 0:
                                memory_name = n.strip()
                    except Exception:
                        memory_name = None

                    if not memory_name or memory_name not in self._captured_planets:
                        continue

                    if not hasattr(planet_data, 'PlanetInfo'):
                        continue

                    info = planet_data.PlanetInfo
                    captured = self._captured_planets[memory_name]

                    # Flora
                    if hasattr(info, 'Flora'):
                        val = str(info.Flora) or ""
                        raw = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if raw and raw != "None" and len(raw) >= 2:
                            captured['flora_display'] = self._resolve_adjective(raw, 'flora')

                    # Fauna
                    if hasattr(info, 'Fauna'):
                        val = str(info.Fauna) or ""
                        raw = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if raw and raw != "None" and len(raw) >= 2:
                            captured['fauna_display'] = self._resolve_adjective(raw, 'fauna')

                    # Sentinel - index based on reality mode
                    if hasattr(info, 'SentinelsPerDifficulty'):
                        sent_arr = info.SentinelsPerDifficulty
                        if hasattr(sent_arr, '__getitem__'):
                            val = str(sent_arr[self._get_difficulty_index()]) or ""
                            raw = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                            if raw and raw != "None" and len(raw) >= 2:
                                captured['sentinel_display'] = self._resolve_adjective(raw, 'sentinel')

                    # Weather
                    if hasattr(info, 'Weather'):
                        val = str(info.Weather) or ""
                        raw = ''.join(c for c in val if c.isprintable() and ord(c) < 128).strip()
                        if raw and raw != "None" and len(raw) >= 2:
                            captured['weather_raw_string'] = raw
                            captured['weather_display'] = self._resolve_adjective(raw, 'weather')

                    refreshed += 1
                except Exception:
                    pass

            if refreshed > 0:
                logger.info(f"[EXPORT] Auto-refreshed adjectives for {refreshed} planet(s)")

        except Exception as e:
            logger.warning(f"[EXPORT] Auto-refresh failed (non-fatal): {e}")
