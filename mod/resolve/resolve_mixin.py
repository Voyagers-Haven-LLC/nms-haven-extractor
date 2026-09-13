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

from resolve.galaxydata import GALAXY_NAMES, get_galaxy_name  # noqa: F401
from capture.offsets import *  # noqa: F401,F403
from payload.extraction_core import decide_galaxy
from capture.namegen_probe import (NMS_NAMEGEN_AVAILABLE, nms_system_name,
    nms_region_name, nms_planet_name)


class ResolveMixin:
    """Coordinate/glyph/galaxy resolution + procedural naming (planet namegen REVIVED)."""

    # ---- source lines 4071-4424: galaxy readers + voter + coords + upgrade + proc names ----
    def _galaxy_in_range(self, raw) -> Optional[int]:
        """Return raw if it's a plausible galaxy index (0..255), else None.

        0 is a VALID, positively-read value (Euclid) — only out-of-range / unreadable
        values become None. None means 'this source could not be read', which is a
        different fact from 'galaxy 0', and decide_galaxy() treats them differently.
        """
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return None
        return v if 0 <= v <= 255 else None

    def _read_galaxy_from_location(self) -> Optional[int]:
        """Galaxy candidate 1 (most authoritative): the player's current location.

        player_state.mLocation is a full cGcUniverseAddressData (nmspy: cGcPlayerState
        @0x180) whose RealityIndex (@0x14) is the canonical 'what galaxy is the player
        in' field. Read via the nmspy struct accessor so it stays correct if the game
        struct shifts (nmspy is rebuilt per game version). Returns None on any failure.
        """
        try:
            player_state = gameData.player_state
            if not player_state:
                return None
            raw = self._safe_int(player_state.mLocation.RealityIndex)
            return self._galaxy_in_range(raw)
        except Exception as e:
            logger.debug(f"  [GALAXY] location read failed: {e}")
            return None

    def _read_galaxy_from_planet_geninput(self) -> Optional[int]:
        """Galaxy candidate 2: the per-planet generation input on the live planet.

        cGcPlanet.mPlanetGenerationInputData (nmspy @0x3A60) -> RealityIndex (@0x44).
        This is the per-planet copy, distinct from the solar-system-data scratch copy
        read by _read_galaxy_from_solar_system_direct. Read via nmspy accessor (offset
        safe). Returns None on any failure.
        """
        try:
            if self._cached_solar_system is None:
                return None
            planets = self._cached_solar_system.maPlanets
            gen_input = planets[0].mPlanetGenerationInputData
            raw = self._safe_int(gen_input.RealityIndex)
            return self._galaxy_in_range(raw)
        except Exception as e:
            logger.debug(f"  [GALAXY] planet geninput read failed: {e}")
            return None

    def _read_galaxy_from_solar_system_direct(self) -> Optional[int]:
        """Galaxy candidate 3: the solar-system-data PlanetGenerationInputs[0] scratch copy.

        2.1.0: read through the generated struct — sys_data.PlanetGenerationInputs[0]
        .RealityIndex (nmse.cGcSolarSystemData / cGcPlanetGenerationInputData) — no
        hand offsets (the old 0x1EA0 moved to 0x2180 in Cosmos). Voting semantics are
        UNCHANGED: this is a generation-input scratch buffer that has historically
        read 0 even in non-Euclid galaxies, which is exactly why it is only ONE of
        several candidates and never authoritative on its own. Returns None on
        failure/out-of-range (0 is returned as 0 — a positively-read value).
        """
        sys_data = None
        try:
            if self._cached_solar_system is not None:
                sys_data = self._cached_solar_system.mSolarSystemData
        except Exception as e:
            logger.debug(f"  [GALAXY] sysdata: failed to get struct: {e}")
        if sys_data is None and self._cached_sys_data_addr:
            try:
                sys_data = map_struct(self._cached_sys_data_addr, nmse.cGcSolarSystemData)
            except Exception as e:
                logger.debug(f"  [GALAXY] sysdata: map_struct failed: {e}")
        if sys_data is None:
            return None
        try:
            raw_reality = int(sys_data.PlanetGenerationInputs[0].RealityIndex)
            return self._galaxy_in_range(raw_reality)
        except Exception as e:
            logger.debug(f"  [GALAXY] sysdata read failed: {e}")
            return None

    def _resolve_galaxy_index(self) -> Optional[int]:
        """Resolve the current galaxy index from all independent sources.

        Gathers candidates in trust order and lets the pure decide_galaxy() arbitrate.
        Returns the galaxy index (0..255), or None == UNKNOWN. Callers MUST NOT turn a
        None into Euclid — a galaxy-unknown system is held back from upload instead.

        Fixes the long-standing 'always Euclid' bug: previously a single zeroed scratch
        read was accepted as a definitive Euclid (0 is not None) and the authoritative
        player-location source was never even consulted.
        """
        candidates = [
            (self._read_galaxy_from_location(), "location"),
            (self._read_galaxy_from_planet_geninput(), "planet_geninput"),
            (self._read_galaxy_from_solar_system_direct(), "sysdata"),
        ]
        idx, source = decide_galaxy(candidates)
        raw_summary = ", ".join(f"{s}={v}" for v, s in candidates)
        if idx is None:
            logger.info(f"  [GALAXY] UNKNOWN (no source readable) | candidates: {raw_summary}")
        else:
            logger.info(f"  [GALAXY] resolved={idx} ({get_galaxy_name(idx)}) via={source} | candidates: {raw_summary}")
        return idx

    def _coords_look_valid(self, voxel_x, voxel_y, voxel_z, system_idx, galaxy_idx):
        """Reject all-zero (impossible universe origin) and out-of-range galaxy."""
        if voxel_x == 0 and voxel_y == 0 and voxel_z == 0 and system_idx == 0:
            return False
        if galaxy_idx < 0 or galaxy_idx > 255:
            return False
        return True

    def _decode_universe_address(self, universe_addr):
        """Decode packed uint64 mUniverseAddress into coord dict, or None if invalid.

        Bit layout (cGcDiscoveryData.mUniverseAddress — packed GalacticAddress only):
          0-11:  X region (0-4095)    12-23: Z region (0-4095)
          24-31: Y region (0-255)     40-51: SolarSystemIndex (12 bits)
          52-55: PlanetIndex + 1

        NOTE: Galaxy is NOT in this field. mUniverseAddress is a uint64 containing
        only the packed GalacticAddress. The galaxy (RealityIndex) is a separate
        int32 read by _resolve_galaxy_index() (multi-source voter) instead.
        """
        if universe_addr == 0 or universe_addr == 0xFFFFFFFFFFFFFFFF:
            return None
        x_region = universe_addr & 0xFFF
        z_region = (universe_addr >> 12) & 0xFFF
        y_region = (universe_addr >> 24) & 0xFF
        system_idx = (universe_addr >> 40) & 0xFFF
        planet_idx = max(0, ((universe_addr >> 52) & 0xF) - 1)

        voxel_x = x_region if x_region <= 0x7FF else x_region - 0x1000
        voxel_y = y_region if y_region <= 0x7F else y_region - 0x100
        voxel_z = z_region if z_region <= 0x7FF else z_region - 0x1000

        if voxel_x == 0 and voxel_y == 0 and voxel_z == 0 and system_idx == 0:
            return None

        glyph_code = f"{planet_idx:01X}{system_idx:03X}{y_region:02X}{z_region:03X}{x_region:03X}".upper()
        return {
            "voxel_x": voxel_x, "voxel_y": voxel_y, "voxel_z": voxel_z,
            "region_x": x_region, "region_y": y_region, "region_z": z_region,
            "solar_system_index": system_idx, "planet_index": planet_idx,
            "glyph_code": glyph_code,
        }

    def _get_coords_from_universe_address(self, source_label="mUniverseAddress"):
        """Primary coord source: cached solar system's first planet mUniverseAddress."""
        try:
            if self._cached_solar_system is None:
                return None
            planets = self._cached_solar_system.maPlanets
            discovery_data = planets[0].mPlanetDiscoveryData
            universe_addr = self._safe_int(discovery_data.mUniverseAddress)
            decoded = self._decode_universe_address(universe_addr)
            if not decoded:
                logger.debug(f"  [{source_label}] invalid decode (addr=0x{universe_addr:016X})")
                return None

            # Galaxy is NOT in mUniverseAddress (only the packed GalacticAddress). Resolve it
            # from the multi-source voter. None == UNKNOWN: we keep coords (glyph/region are
            # valid) but flag galaxy_unknown so the export step holds the system back rather
            # than mislabelling it Euclid. namegen needs an index, so use 0 only as a naming
            # seed when unknown (the system won't upload until the galaxy resolves anyway).
            galaxy_idx = self._resolve_galaxy_index()
            galaxy_unknown = galaxy_idx is None
            galaxy_name = None if galaxy_unknown else get_galaxy_name(galaxy_idx)

            # Procedural names are galaxy-DEPENDENT. Never seed them with a fallback galaxy
            # (that produced wrong/Euclid system + region names). When the galaxy is unknown
            # we leave the region name EMPTY and the system name as a placeholder; both are
            # regenerated by _refresh_galaxy_on_current_coords once the galaxy resolves. A
            # held (galaxy-unknown) system never uploads, so these placeholders never submit.
            system_name, region_name = self._make_proc_names(
                decoded['glyph_code'], galaxy_idx,
                decoded['solar_system_index'], decoded['voxel_x'], decoded['voxel_y'], decoded['voxel_z']
            )
            logger.info(f"  [COORDS] '{system_name}' @ {decoded['glyph_code']} ({galaxy_name or 'GALAXY UNKNOWN'}) raw=0x{universe_addr:016X}")
            logger.debug(f"  [NAMEGEN] Region: '{region_name}'")
            return {
                "system_name": system_name,
                "region_name": region_name,
                "glyph_code": decoded["glyph_code"],
                "galaxy_name": galaxy_name,
                "galaxy_index": galaxy_idx,
                "galaxy_unknown": galaxy_unknown,
                "voxel_x": decoded["voxel_x"],
                "voxel_y": decoded["voxel_y"],
                "voxel_z": decoded["voxel_z"],
                "region_x": decoded["region_x"],
                "region_y": decoded["region_y"],
                "region_z": decoded["region_z"],
                "solar_system_index": decoded["solar_system_index"],
            }
        except Exception as e:
            logger.debug(f"  [{source_label}] exception: {e}")
            return None

    def _get_coords_from_player_state(self, source_label="player_state"):
        """Secondary fallback. Vulnerable to NMS struct shifts (Voyagers broke GalacticAddress
        and may have affected RealityIndex too) — only used when mUniverseAddress is unavailable.
        Any result from this path is tagged with from_fallback=True so the caller can retry later.
        """
        try:
            player_state = gameData.player_state
            if not player_state:
                return None
            location = player_state.mLocation
            galactic_addr = location.GalacticAddress
            voxel_x = self._safe_int(galactic_addr.VoxelX)
            voxel_y = self._safe_int(galactic_addr.VoxelY)
            voxel_z = self._safe_int(galactic_addr.VoxelZ)
            system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
            planet_idx = self._safe_int(galactic_addr.PlanetIndex)
            # Galaxy via the multi-source voter. None == UNKNOWN — keep the coords (voxel
            # sanity still applies) but flag galaxy_unknown so export holds it rather than
            # mislabelling Euclid. Coord sanity uses 0 as a stand-in galaxy for the all-zero
            # origin check only.
            galaxy_idx = self._resolve_galaxy_index()
            galaxy_unknown = galaxy_idx is None
            logger.info(f"  [{source_label}] resolved galaxy_idx={galaxy_idx}, voxel=[{voxel_x},{voxel_y},{voxel_z}], sys={system_idx}")
            if not self._coords_look_valid(voxel_x, voxel_y, voxel_z, system_idx, galaxy_idx if galaxy_idx is not None else 0):
                logger.debug(f"  [{source_label}] failed sanity: X={voxel_x},Y={voxel_y},Z={voxel_z},Sys={system_idx},Galaxy={galaxy_idx}")
                return None
            glyph_code = self._coords_to_glyphs(planet_idx, system_idx, voxel_x, voxel_y, voxel_z)
            galaxy_name = None if galaxy_unknown else get_galaxy_name(galaxy_idx)
            # Galaxy-dependent proc names: never seed with a fallback galaxy (see
            # _get_coords_from_universe_address). Empty/placeholder when unknown; regenerated
            # by _refresh_galaxy_on_current_coords once the galaxy resolves.
            system_name, region_name = self._make_proc_names(
                glyph_code, galaxy_idx, system_idx, voxel_x, voxel_y, voxel_z
            )
            logger.debug(f"  [SUCCESS via {source_label}] '{system_name}' @ {glyph_code} ({galaxy_name or 'GALAXY UNKNOWN'}) [FALLBACK]")
            logger.debug(f"  [NAMEGEN] Region: '{region_name}'")
            return {
                "system_name": system_name,
                "region_name": region_name,
                "glyph_code": glyph_code,
                "galaxy_name": galaxy_name,
                "galaxy_index": galaxy_idx,
                "galaxy_unknown": galaxy_unknown,
                "voxel_x": voxel_x,
                "voxel_y": voxel_y,
                "voxel_z": voxel_z,
                "solar_system_index": system_idx,
                "from_fallback": True,  # v1.8.1 (Fix 4): mark as fallback so caller retries
            }
        except Exception as e:
            logger.debug(f"  [{source_label}] exception: {e}")
            return None

    def _resolve_current_coordinates(self):
        """Canonical resolver: mUniverseAddress primary, player_state secondary.
        v1.8.1 (Fix 4): player_state results carry from_fallback=True — callers should keep
        retrying this resolver on subsequent hook fires until a non-fallback result arrives.
        """
        coords = self._get_coords_from_universe_address()
        if coords:
            return coords
        logger.debug("  mUniverseAddress unavailable, trying player_state fallback")
        return self._get_coords_from_player_state()

    def _maybe_upgrade_coords(self):
        """v1.8.1 (Fix 4): try to upgrade self._current_system_coords if we currently have
        None or a from_fallback result. Prefer a primary (mUniverseAddress) result when it
        becomes available. This catches the race where on_system_generate fires before NMS
        has populated mUniverseAddress and we initially only got player_state data with a
        potentially-bogus galaxy. On subsequent hook fires the primary becomes readable and
        we swap in the correct galaxy.
        """
        existing = self._current_system_coords
        if existing is not None and not existing.get('from_fallback', False):
            # Coords are solid, BUT galaxy is a separate read that may have been empty/0 at
            # first resolution (the per-planet/sysdata RealityIndex can populate late). Keep
            # re-resolving the galaxy while the system is live so an early miss self-corrects
            # before we freeze the system. This is the fix for galaxy locking to Euclid.
            self._refresh_galaxy_on_current_coords()
            return  # coords themselves are solid — nothing else to do

        new_coords = self._resolve_current_coordinates()
        if not new_coords:
            return  # neither path worked; keep whatever we had

        if new_coords.get('from_fallback', False):
            # Only accept a fallback if we had nothing at all before — prevents stale
            # fallback from a prior resolution being replaced with equally-unreliable data.
            if existing is None:
                self._current_system_coords = new_coords
            return

        # Primary (non-fallback) result — always use it, and flag the upgrade visibly.
        if existing is not None and existing.get('from_fallback', False):
            logger.info(
                f"  [COORD UPGRADE] Primary mUniverseAddress now available — "
                f"replacing fallback galaxy='{existing.get('galaxy_name')}' "
                f"with '{new_coords.get('galaxy_name')}'"
            )
        self._current_system_coords = new_coords

    def _make_proc_names(self, glyph_code, galaxy_idx, system_idx, x, y, z):
        """Build (system_name, region_name) for the given galaxy.

        Procedural names are galaxy-dependent, so we refuse to seed them with a fallback
        galaxy: when galaxy_idx is None (UNKNOWN) the region name is left EMPTY and the
        system name is a placeholder. An in-game / renamed system name (read from memory)
        always wins over procgen and is galaxy-independent, so it's kept either way.
        Returns ("System_<glyph>"/actual, "") when unknown; real procgen names when known.
        """
        actual = self._get_actual_system_name()
        if galaxy_idx is None:
            return (actual or f"System_{glyph_code}", "")
        system_name = actual or self._generate_system_name(
            glyph_code, galaxy_idx, system_idx=system_idx, x=x, y=y, z=z)
        region_name = self._generate_region_name(
            glyph_code, galaxy_idx, system_idx=system_idx, x=x, y=y, z=z)
        return (system_name, region_name)

    def _refresh_galaxy_on_current_coords(self):
        """Re-resolve the galaxy for the CURRENT (live) system and update cached coords.

        Only ever UPGRADES: if no source is readable (resolver returns None) we keep
        whatever we already had rather than downgrading a known galaxy to unknown. This
        runs on every creature-roles fire while the system is live, so an early empty
        RealityIndex read gets corrected once the field populates — well before the
        system is frozen on warp/export.

        When the galaxy changes/resolves, the galaxy-DEPENDENT procedural region + system
        names are REGENERATED so a stale Euclid-seeded (or empty) name can never be the one
        that gets submitted — the wrong-region-name problem.
        """
        coords = self._current_system_coords
        if not coords:
            return
        try:
            g = self._resolve_galaxy_index()
        except Exception:
            return
        if g is None:
            return  # not readable right now — don't clobber a previously-known galaxy
        prev = coords.get('galaxy_index')
        if prev == g and not coords.get('galaxy_unknown'):
            return  # already correct and known — nothing to do
        coords['galaxy_index'] = g
        coords['galaxy_name'] = get_galaxy_name(g)
        coords['galaxy_unknown'] = False
        system_name, region_name = self._make_proc_names(
            coords.get('glyph_code', ''), g,
            coords.get('solar_system_index'), coords.get('voxel_x'),
            coords.get('voxel_y'), coords.get('voxel_z'))
        coords['region_name'] = region_name
        # Don't clobber a user-applied custom name; otherwise refresh the (proc/actual) name.
        if not coords.get('custom_name_applied'):
            coords['system_name'] = system_name
        logger.info(f"  [GALAXY] current-coords galaxy {prev} -> {g} ({get_galaxy_name(g)}); regenerated region/system proc names")

    # ---- source lines 4491-4659: glyph math + actual name + system/region/planet namegen ----
    def _coords_to_glyphs(self, planet: int, system: int, x: int, y: int, z: int) -> str:
        """Convert signed voxel coordinates to portal glyph code.

        Uses two's complement masking to match NMS portal address encoding:
        - X/Z: signed 12-bit (-2048..+2047) → unsigned 12-bit via & 0xFFF
        - Y: signed 8-bit (-128..+127) → unsigned 8-bit via & 0xFF
        - Positive values (0..+2047) → 0x000..0x7FF
        - Negative values (-1..-2047) → 0xFFF..0x801
        """
        try:
            portal_x = x & 0xFFF
            portal_y = y & 0xFF
            portal_z = z & 0xFFF
            portal_sys = system & 0xFFF
            portal_planet = planet & 0xF
            glyph = f"{portal_planet:01X}{portal_sys:03X}{portal_y:02X}{portal_z:03X}{portal_x:03X}"
            return glyph.upper()
        except Exception:
            return "000000000000"

    def _glyph_to_portal_code(self, glyph: str) -> int:
        """Convert glyph string (e.g., '00940F34B00A') to portal code integer."""
        try:
            return int(glyph, 16)
        except (ValueError, TypeError):
            return 0

    def _coords_to_portal_code(self, system_idx: int, x: int, y: int, z: int, planet: int = 0) -> int:
        """Convert coordinates to full 48-bit portal code for name generation.

        IMPORTANT: Uses full 12-bit system index, not 9-bit glyph version.
        Standard portal glyphs only encode 9 bits (max 511), but NMS uses
        full 12-bit system indices (max 4095) for name generation.
        """
        portal_x = x & 0xFFF      # 12 bits (two's complement)
        portal_y = y & 0xFF       # 8 bits (two's complement)
        portal_z = z & 0xFFF      # 12 bits (two's complement)
        portal_sys = system_idx & 0xFFF    # 12 bits (FULL, not 9-bit truncated!)
        portal_planet = planet & 0xF       # 4 bits

        # Build 48-bit portal code: PSSS YYZZ ZXXX (but with 12-bit system)
        portal_code = (portal_planet << 44) | (portal_sys << 32) | (portal_y << 24) | (portal_z << 12) | portal_x
        return portal_code

    def _get_actual_system_name(self) -> str:
        """Get the actual in-game system name from solar system data.

        Reads directly from cGcSolarSystemData.Name at offset 0x2274.
        Note: This field may be empty during planet generation and
        only gets populated later. Call this during export, not capture.

        Returns:
            System name string, or empty string if not found
        """
        # Try reading from cached solar system's Name field
        if self._cached_solar_system:
            try:
                solar_sys_addr = get_addressof(self._cached_solar_system)
                # Name is at offset 0x2274 from solar system start
                name_addr = solar_sys_addr + 0x2274

                # Read 128 bytes (cTkFixedString<0x80>)
                buffer = (ctypes.c_char * 128)()
                ctypes.memmove(buffer, name_addr, 128)
                raw = bytes(buffer)

                # Find null terminator
                null_idx = raw.find(b'\x00')
                if null_idx > 0:
                    name = raw[:null_idx].decode('utf-8', errors='ignore').strip()
                    if name:
                        logger.info(f"  [SYSNAME] Got: '{name}'")
                        return name

            except Exception as e:
                logger.debug(f"  [SYSNAME] Read failed: {e}")

        return ""

    def _generate_system_name(self, glyph_code: str, galaxy_idx: int = 0,
                               system_idx: int = None, x: int = None, y: int = None, z: int = None) -> str:
        """Generate procedural system name using NMS algorithm.

        Args:
            glyph_code: 12-character hex glyph string (fallback identifier)
            galaxy_idx: Galaxy index (0 = Euclid, 1 = Hilbert, etc.)
            system_idx: Full 12-bit system index (if available)
            x, y, z: Voxel coordinates (if available)

        Returns:
            Procedurally generated system name, or fallback if generation fails
        """
        if not NMS_NAMEGEN_AVAILABLE:
            return f"System_{glyph_code}"

        try:
            # Use full coordinates if provided (preferred - uses 12-bit system index)
            if system_idx is not None and x is not None and y is not None and z is not None:
                portal_code = self._coords_to_portal_code(system_idx, x, y, z)
            else:
                # Fallback to glyph code (only 9-bit system index)
                portal_code = self._glyph_to_portal_code(glyph_code)

            if portal_code == 0:
                return f"System_{glyph_code}"

            name = nms_system_name(portal_code, galaxy_idx)
            logger.debug(f"  Generated system name: '{name}' (sys_idx={system_idx})")
            return name
        except Exception as e:
            logger.debug(f"  System name generation failed: {e}")
            return f"System_{glyph_code}"

    def _generate_region_name(self, glyph_code: str, galaxy_idx: int = 0,
                               system_idx: int = None, x: int = None, y: int = None, z: int = None) -> str:
        """Generate procedural region name using NMS algorithm.

        Args:
            glyph_code: 12-character hex glyph string (fallback identifier)
            galaxy_idx: Galaxy index (0 = Euclid, 1 = Hilbert, etc.)
            system_idx: Full 12-bit system index (if available)
            x, y, z: Voxel coordinates (if available)

        Returns:
            Procedurally generated region name, or fallback if generation fails
        """
        if not NMS_NAMEGEN_AVAILABLE:
            return f"Region_{glyph_code[:8]}"

        try:
            # Use full coordinates if provided (preferred - uses 12-bit system index)
            if system_idx is not None and x is not None and y is not None and z is not None:
                portal_code = self._coords_to_portal_code(system_idx, x, y, z)
            else:
                # Fallback to glyph code (only 9-bit system index)
                portal_code = self._glyph_to_portal_code(glyph_code)

            if portal_code == 0:
                return f"Region_{glyph_code[:8]}"

            name = nms_region_name(portal_code, galaxy_idx)
            logger.debug(f"  Generated region name: '{name}' (sys_idx={system_idx})")
            return name
        except Exception as e:
            logger.debug(f"  Region name generation failed: {e}")
            return f"Region_{glyph_code[:8]}"

    def _generate_planet_name(self, planet_seed: int) -> str:
        """Generate procedural planet name using NMS algorithm.

        Args:
            planet_seed: 64-bit planet seed integer

        Returns:
            Procedurally generated planet name, or empty string if generation fails
        """
        if not NMS_NAMEGEN_AVAILABLE:
            return ""

        try:
            if not planet_seed or planet_seed == 0:
                return ""

            name = nms_planet_name(planet_seed)
            logger.debug(f"  Generated planet name: '{name}' (seed=0x{planet_seed:016X})")
            return name
        except Exception as e:
            logger.debug(f"  Planet name generation failed: {e}")
            return ""
