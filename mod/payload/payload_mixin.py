"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional, Set, List, Dict

logger = logging.getLogger("haven_extractor2")

from payload.extraction_core import (build_planet_entry, build_planet_list,
    build_system_payload, galaxy_is_known)
from payload.tables import (translate_resource, clean_weather_string,
    BIOME_PLANT_RESOURCE, BIOME_SUBTYPE_PLANT_OVERRIDE,
    HIDDEN_SUBSTANCE_IDS, HIDDEN_SUBSTANCE_NAMES)


class PayloadMixin:
    """Batch freeze + planet payload adapters + the system summary block."""

    # ---- source lines 1602-1687: _save_current_system_to_batch ----
    def _save_current_system_to_batch(self, force_update=False):
        """Freeze the current system into the batch as an immutable payload dict.

        v1.10.0: This is now a PURE freeze of data already captured WHILE THE SYSTEM WAS
        LIVE. It reads NOTHING from game memory. The three live accumulators —
        _current_system_snapshot (system props), _current_system_coords (glyph/galaxy/
        region/name) and _captured_planets (per-planet data) — are all populated by the
        Generate / GenerateCreatureRoles hooks while the system is the active one. The
        payload is assembled by the pure extraction_core.build_system_payload().

        Why this fixes the batch data-loss bug: the old code called _auto_refresh_for_export()
        and _get_current_coordinates() at save time. When the save ran from the NEXT warp's
        on_system_generate, NMS had already recycled the single solar-system object, so those
        live re-reads pulled the NEXT system's star/economy/lifeform/glyph/galaxy into the
        outgoing system. Freezing from cache makes that impossible: a system is finalized
        only from its own captured data, and a frozen entry can never be mutated by a later
        warp (the smoke test asserts this).
        """
        if not self._batch_mode_enabled:
            return
        if not self._captured_planets and not force_update:
            logger.debug("[BATCH] No captured planets to save")
            return

        coords = self._current_system_coords
        if not coords:
            logger.warning("[BATCH] No cached coordinates for current system - cannot freeze")
            return

        try:
            coords = dict(coords)  # defensive copy so later warps can't mutate the frozen entry
            glyph_code = coords.get('glyph_code', '')

            snapshot = dict(self._current_system_snapshot) if self._current_system_snapshot else None
            if snapshot is None:
                logger.warning("[BATCH] No system-props snapshot for current system - star/economy/lifeform may be missing")

            planets = self._planets_from_captured()
            has_captured = len(self._captured_planets) > 0

            # Procedural name (pure namegen, no game memory). galaxy_index may be None when the
            # galaxy is still unknown - use 0 only as a naming seed (the system won't upload
            # until the galaxy resolves; see the export hard-stop).
            procgen_name = self._generate_system_name(
                glyph_code, coords.get('galaxy_index') or 0,
                system_idx=coords.get('solar_system_index'),
                x=coords.get('voxel_x'), y=coords.get('voxel_y'), z=coords.get('voxel_z')
            )

            system_data = build_system_payload(
                snapshot=snapshot,
                coords=coords,
                planets=planets,
                extractor_version=self.__version__,
                procedural_name=procgen_name,
                has_captured=has_captured,
                now_iso=datetime.now().isoformat(),
                now_ts=int(datetime.now().timestamp()),
            )

            # Dedup by glyph. force_update replaces the existing frozen entry in place.
            existing_index = None
            for i, existing in enumerate(self._batch_systems):
                if existing.get('glyph_code') == glyph_code:
                    existing_index = i
                    break

            if existing_index is not None:
                existing = self._batch_systems[existing_index]
                # Skip a true duplicate, BUT always refresh an entry whose galaxy was still
                # unknown when it was first frozen — a re-warp may now resolve it.
                if not force_update and galaxy_is_known(existing):
                    logger.debug(f"[BATCH] System {glyph_code} already in batch, skipping duplicate")
                    return
                self._batch_systems[existing_index] = system_data
                logger.debug(f"[BATCH] Updated frozen entry for {glyph_code}")
            else:
                self._batch_systems.append(system_data)

            self._log_system_summary(system_data)
            self._status_display = f"Batch: {len(self._batch_systems)} system(s)"

        except Exception as e:
            logger.error(f"[BATCH] Failed to freeze system to batch: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # ---- source lines 3407-3445: _planet_from_captured + _planets_from_captured ----
    def _planet_from_captured(self, captured: dict, index: int) -> dict:
        """Build one planet payload entry from captured hook data only.

        Thin wrapper over the pure extraction_core.build_planet_entry() (which the smoke
        test exercises directly). Captured data was gathered while the system was active in
        memory, so it's authoritative for the batched system's planets; no live read here.
        Resource translation, hidden-substance fix and plant-resource derivation are carried
        over verbatim by the core function.
        """
        return build_planet_entry(
            captured, index,
            translate_resource=translate_resource,
            biome_plant_resource=BIOME_PLANT_RESOURCE,
            biome_subtype_plant_override=BIOME_SUBTYPE_PLANT_OVERRIDE,
            hidden_substance_names=HIDDEN_SUBSTANCE_NAMES,
            hidden_substance_ids=HIDDEN_SUBSTANCE_IDS,
            clean_weather=clean_weather_string,
        )

    def _planets_from_captured(self) -> list:
        """Build the full planet list from _captured_planets, preserving insertion order
        (= slot order at capture time). Used by _save_current_system_to_batch.
        """
        # Authoritative live body count (planets+moons, 1..6) snapshotted while the system
        # was fresh; used only as an UPPER cap by build_planet_list (never to pad). None
        # when the read was unavailable/untrustworthy.
        snap = self._current_system_snapshot or {}
        count_hint = snap.get('_planets_count')
        planets = build_planet_list(
            self._captured_planets,
            planet_builder=self._planet_from_captured,
            count_hint=count_hint,
        )
        moon_count = sum(1 for p in planets if p.get('is_moon', False))
        planet_count = len(planets) - moon_count
        dropped = len(self._captured_planets) - len(planets)
        drop_note = f" (dropped {dropped} phantom capture(s))" if dropped > 0 else ""
        logger.info(f"  [CAPTURED-ONLY] {planet_count} planets + {moon_count} moons from {len(self._captured_planets)} captures{drop_note}")
        return planets

    # ---- source lines 4426-4467: _log_system_summary ----
    def _log_system_summary(self, system_data: dict):
        """Print a clean, aligned summary block for a saved system."""
        name = system_data.get('system_name', 'Unknown')
        glyph = system_data.get('glyph_code', '????????????')
        galaxy = system_data.get('galaxy_name', 'Unknown')
        region = system_data.get('region_name', '')
        star = system_data.get('star_color', 'Unknown')
        no_data = system_data.get('no_trade_data', False)

        if no_data:
            economy = '-Data Unavailable-'
            conflict = '-Data Unavailable-'
        else:
            eco_type = system_data.get('economy_type', 'Unknown')
            eco_str = system_data.get('economy_strength', '')
            economy = f"{eco_type} ({eco_str})" if eco_str and eco_str != 'Unknown' else eco_type
            conflict = system_data.get('conflict_level', 'Unknown')

        planets = system_data.get('planets', [])
        total = len(planets)
        batch_count = len(self._batch_systems)

        logger.info("")
        logger.info(f"=== SYSTEM: {name} ===")
        logger.info(f"  Glyph: {glyph} | Galaxy: {galaxy} | Region: {region}")
        logger.info(f"  Star: {star} | Economy: {economy} | Conflict: {conflict}")
        logger.info("")

        for i, p in enumerate(planets):
            p_name = p.get('planet_name', f'Planet_{i+1}')
            moon = " (moon)" if p.get('is_moon', False) else ""
            biome = p.get('biome', 'Unknown')
            flora = p.get('flora_level', '?')
            fauna = p.get('fauna_level', '?')
            sentinel = p.get('sentinel_level', '?')

            # Align columns
            label = f"{p_name}{moon}"
            logger.info(f"  [{i+1}/{total}] {label:<24s} {biome:<14s} Flora: {flora:<12s} Fauna: {fauna:<12s} Sentinel: {sentinel}")

        logger.info(f"=== Saved to batch ({batch_count} total) ===")
        logger.info("")
