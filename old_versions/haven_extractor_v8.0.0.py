# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# ///
"""
Haven Extractor v8.0.0 - Remote API Sync

Extracts planet data from NMS and sends it to Haven UI via API.
Works with ngrok for remote connections.

SETUP:
1. Create config.json in Documents/Haven-Extractor/ with your API URL:
   {"api_url": "https://your-ngrok-url.ngrok-free.app"}
2. Or place haven_config.json in the same folder as this mod

WORKFLOW:
1. Warp to system - events logged immediately
2. Click "Check Planet Data" to see what's populated
3. Use freighter scanner room (Activate Planetary Probe)
4. Click "Check Planet Data" again - see if more data appeared
5. Click "Extract Now" when ready

Data extracted per planet:
- biome, biome_subtype, weather
- sentinel_level, flora_level, fauna_level
- common_resource, uncommon_resource, rare_resource
- is_moon, planet_size, planet_name

Data is:
- Sent to Haven UI API (if configured)
- Saved locally as backup in Documents/Haven-Extractor/
"""

import json
import logging
import time
import ctypes
import urllib.request
import urllib.error
import ssl
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

from pymhf import Mod
from pymhf.core.memutils import map_struct, get_addressof
from pymhf.gui.decorators import gui_button
import nmspy.data.types as nms
from nmspy.decorators import on_state_change
from nmspy.common import gameData

logger = logging.getLogger(__name__)

# =============================================================================
# API CONFIGURATION
# =============================================================================
# The extractor will POST extraction data to {API_BASE_URL}/api/extraction
#
# Configuration is loaded from (in order of priority):
# 1. haven_config.json in the same folder as the mod
# 2. %USERPROFILE%\Documents\Haven-Extractor\config.json
# 3. Hardcoded defaults below
#
# Example haven_config.json:
# {
#     "api_url": "https://abc123.ngrok-free.app",
#     "api_key": "your-api-key-here"
# }
# =============================================================================
DEFAULT_API_URL = "https://voyagers-haven-3dmap.ngrok.io"  # Voyagers Haven API
DEFAULT_API_KEY = ""  # Optional: API key for authentication


def load_config() -> dict:
    """Load configuration from config file or use defaults."""
    config = {
        "api_url": DEFAULT_API_URL,
        "api_key": DEFAULT_API_KEY,
    }

    # Try loading from various locations
    config_locations = [
        Path(__file__).parent / "haven_config.json",  # Same folder as mod
        Path.home() / "Documents" / "Haven-Extractor" / "config.json",  # User documents
    ]

    for config_path in config_locations:
        try:
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    file_config = json.load(f)
                    if file_config.get("api_url"):
                        config["api_url"] = file_config["api_url"]
                    if file_config.get("api_key"):
                        config["api_key"] = file_config["api_key"]
                    logger.info(f"Loaded config from: {config_path}")
                    break
        except Exception as e:
            logger.debug(f"Could not load config from {config_path}: {e}")

    return config


# Load config at module level
_config = load_config()
API_BASE_URL = _config["api_url"]
API_KEY = _config["api_key"]

# =============================================================================
# MEMORY OFFSET CONSTANTS (from MBINCompiler / NMS 4.13 PDB)
# These may need adjustment for different game versions
# =============================================================================

# GcSolarSystemData offsets (total size ~0x1F50)
class SolarSystemDataOffsets:
    """Offsets within cGcSolarSystemData struct."""
    PLANETS_COUNT = 0x2264        # int - total planet + moon count
    PRIME_PLANETS = 0x2268        # int - non-moon planet count
    STAR_CLASS = 0x224C           # GcSolarSystemClass enum
    STAR_TYPE = 0x2270            # GcGalaxyStarTypes enum
    TRADING_DATA = 0x2240         # GcPlanetTradingData struct
    CONFLICT_DATA = 0x2250        # GcPlayerConflictData struct
    INHABITING_RACE = 0x2254      # GcAlienRace enum
    SEED = 0x21A0                 # GcSeed struct
    PLANET_GEN_INPUTS = 0x1EA0    # GcPlanetGenerationInputData[6] array

# GcPlanetTradingData offsets (nested at SolarSystemData + 0x2240)
class TradingDataOffsets:
    """Offsets within GcPlanetTradingData struct."""
    TRADING_CLASS = 0x0           # Economy type enum
    WEALTH_CLASS = 0x4            # Economy strength enum

# GcPlayerConflictData offsets (nested at SolarSystemData + 0x2250)
class ConflictDataOffsets:
    """Offsets within GcPlayerConflictData struct."""
    CONFLICT_LEVEL = 0x0          # Conflict level enum

# GcPlanetGenerationInputData offsets (size 0x53 per planet = 83 bytes)
class PlanetGenInputOffsets:
    """Offsets within GcPlanetGenerationInputData struct."""
    STRUCT_SIZE = 0x53            # Size of each planet gen input entry (83 bytes verified from nmspy)
    COMMON_SUBSTANCE = 0x00       # NMSString0x10
    RARE_SUBSTANCE = 0x10         # NMSString0x10
    SEED = 0x20                   # GcSeed
    BIOME = 0x30                  # GcBiomeType enum (4 bytes)
    BIOME_SUBTYPE = 0x34          # GcBiomeSubType enum
    PLANET_CLASS = 0x38           # GcPlanetClass enum
    PLANET_INDEX = 0x3C           # int
    PLANET_SIZE = 0x40            # GcPlanetSize enum (4 bytes)
    REALITY_INDEX = 0x44          # int (galaxy index)
    STAR_TYPE = 0x48              # GcGalaxyStarTypes enum

# =============================================================================
# ENUM VALUE MAPPINGS (from MBINCompiler)
# =============================================================================

BIOME_TYPES = {
    0: "Lush", 1: "Toxic", 2: "Scorched", 3: "Radioactive", 4: "Frozen",
    5: "Barren", 6: "Dead", 7: "Weird", 8: "Red", 9: "Green", 10: "Blue",
    11: "Test", 12: "Swamp", 13: "Lava", 14: "Waterworld", 15: "GasGiant", 16: "All"
}

PLANET_SIZES = {
    0: "Large", 1: "Medium", 2: "Small", 3: "Moon", 4: "Giant"
}

TRADING_CLASSES = {
    0: "Mining", 1: "HighTech", 2: "Trading", 3: "Manufacturing",
    4: "Fusion", 5: "Scientific", 6: "PowerGeneration"
}

WEALTH_CLASSES = {
    0: "Poor", 1: "Average", 2: "Wealthy", 3: "Pirate"
}

CONFLICT_LEVELS = {
    0: "Low", 1: "Default", 2: "High", 3: "Pirate"
}

ALIEN_RACES = {
    0: "Traders",    # Gek
    1: "Warriors",   # Vy'keen
    2: "Explorers",  # Korvax
    3: "Robots",     # Sentinel/Atlas
    4: "Atlas",
    5: "Diplomats",
    6: "None"
}

STAR_TYPES = {
    0: "Yellow", 1: "Red", 2: "Green", 3: "Blue"
}


class HavenExtractorMod(Mod):
    __author__ = "Voyagers Haven"
    __version__ = "8.0.0"
    __description__ = "Planet extraction with remote API sync"

    # Scan table type for freighter scanner room
    SCAN_TABLE_FREIGHTER = 3

    def __init__(self):
        super().__init__()
        self._output_dir = Path.home() / "Documents" / "Haven-Extractor"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._pending_extraction = False
        self._last_extracted_seed = None
        self._cached_solar_system = None
        self._cached_sys_data_addr = None  # Cache the sys_data address for direct reads

        # Debouncing - don't re-extract more often than this
        self._last_extraction_time = 0
        self._extraction_debounce_seconds = 3

        # Simple event counter - NO quiet period, NO suppression
        self._scan_event_count = 0

        logger.info("=" * 60)
        logger.info("Haven Extractor v8.0.0 - Remote API Sync")
        logger.info(f"Local backup: {self._output_dir}")
        logger.info("=" * 60)

        # Log API configuration status
        if API_BASE_URL:
            logger.info(f"API Endpoint: {API_BASE_URL}/api/extraction")
            logger.info("Extractions will be sent to Haven UI automatically!")
        else:
            logger.warning("API NOT CONFIGURED - Local save only!")
            logger.warning("To enable remote sync:")
            logger.warning(f"  1. Create: {self._output_dir / 'config.json'}")
            logger.warning('  2. Add: {"api_url": "https://your-ngrok-url.ngrok-free.app"}')

        logger.info("")
        logger.info("*** WORKFLOW ***")
        logger.info("1. Warp to system - events logged immediately")
        logger.info("2. Click 'Check Planet Data' to see what's populated")
        logger.info("3. Use freighter scanner room")
        logger.info("4. Click 'Check Planet Data' again")
        logger.info("5. Click 'Extract Now' when ready")
        logger.info("=" * 60)

    # =========================================================================
    # DIRECT MEMORY READ UTILITIES
    # =========================================================================

    def _read_int32(self, base_addr: int, offset: int) -> int:
        """Read a 32-bit integer from memory at base + offset."""
        try:
            addr = base_addr + offset
            # Use ctypes to read from process memory
            value = ctypes.c_int32()
            ctypes.memmove(ctypes.addressof(value), addr, 4)
            return value.value
        except Exception as e:
            logger.debug(f"Failed to read int32 at 0x{base_addr:X}+0x{offset:X}: {e}")
            return 0

    def _read_uint32(self, base_addr: int, offset: int) -> int:
        """Read a 32-bit unsigned integer from memory at base + offset."""
        try:
            addr = base_addr + offset
            value = ctypes.c_uint32()
            ctypes.memmove(ctypes.addressof(value), addr, 4)
            return value.value
        except Exception as e:
            logger.debug(f"Failed to read uint32 at 0x{base_addr:X}+0x{offset:X}: {e}")
            return 0

    def _read_string(self, base_addr: int, offset: int, max_len: int = 16) -> str:
        """Read a null-terminated string from memory."""
        try:
            addr = base_addr + offset
            buffer = ctypes.create_string_buffer(max_len)
            ctypes.memmove(buffer, addr, max_len)
            # Decode and strip null terminator
            raw = buffer.raw
            null_pos = raw.find(b'\x00')
            if null_pos >= 0:
                raw = raw[:null_pos]
            return raw.decode('utf-8', errors='ignore').strip()
        except Exception as e:
            logger.debug(f"Failed to read string at 0x{base_addr:X}+0x{offset:X}: {e}")
            return ""

    def _read_system_data_direct(self, sys_data_addr: int) -> dict:
        """Read solar system data using direct memory offsets."""
        result = {
            "star_type": "Unknown",
            "economy_type": "Unknown",
            "economy_strength": "Unknown",
            "conflict_level": "Unknown",
            "dominant_lifeform": "Unknown",
            "system_seed": 0,
            "planet_count": 0,
            "prime_planets": 0,
        }

        try:
            # Read planet counts
            result["planet_count"] = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
            result["prime_planets"] = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
            logger.info(f"  [DIRECT] Planet count: {result['planet_count']}, Prime: {result['prime_planets']}")

            # Read star type
            star_type_val = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.STAR_TYPE)
            result["star_type"] = STAR_TYPES.get(star_type_val, f"Unknown({star_type_val})")
            logger.info(f"  [DIRECT] Star type: {result['star_type']} (raw: {star_type_val})")

            # Read trading data (economy)
            trading_addr = sys_data_addr + SolarSystemDataOffsets.TRADING_DATA
            trading_class = self._read_uint32(trading_addr, TradingDataOffsets.TRADING_CLASS)
            wealth_class = self._read_uint32(trading_addr, TradingDataOffsets.WEALTH_CLASS)
            result["economy_type"] = TRADING_CLASSES.get(trading_class, f"Unknown({trading_class})")
            result["economy_strength"] = WEALTH_CLASSES.get(wealth_class, f"Unknown({wealth_class})")
            logger.info(f"  [DIRECT] Economy: {result['economy_type']} / {result['economy_strength']} (raw: {trading_class}/{wealth_class})")

            # Read conflict data
            conflict_addr = sys_data_addr + SolarSystemDataOffsets.CONFLICT_DATA
            conflict_val = self._read_uint32(conflict_addr, ConflictDataOffsets.CONFLICT_LEVEL)
            result["conflict_level"] = CONFLICT_LEVELS.get(conflict_val, f"Unknown({conflict_val})")
            logger.info(f"  [DIRECT] Conflict: {result['conflict_level']} (raw: {conflict_val})")

            # Read dominant race
            race_val = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
            result["dominant_lifeform"] = ALIEN_RACES.get(race_val, f"Unknown({race_val})")
            logger.info(f"  [DIRECT] Race: {result['dominant_lifeform']} (raw: {race_val})")

        except Exception as e:
            logger.error(f"Direct system data read failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return result

    def _read_planet_gen_input_direct(self, sys_data_addr: int, planet_index: int) -> dict:
        """Read planet generation input data using direct offsets."""
        result = {
            "biome": "Unknown",
            "biome_raw": -1,
            "planet_size": "Unknown",
            "planet_size_raw": -1,
            "is_moon": False,
            "common_resource": "",
            "rare_resource": "",
        }

        try:
            # Calculate address for this planet's gen input data
            # Array starts at PLANET_GEN_INPUTS, each entry is STRUCT_SIZE bytes
            planet_gen_addr = sys_data_addr + SolarSystemDataOffsets.PLANET_GEN_INPUTS
            planet_gen_addr += planet_index * PlanetGenInputOffsets.STRUCT_SIZE

            logger.info(f"    [DIRECT] Planet {planet_index} gen input at 0x{planet_gen_addr:X}")

            # Read biome
            biome_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME)
            result["biome_raw"] = biome_val
            result["biome"] = BIOME_TYPES.get(biome_val, f"Unknown({biome_val})")
            logger.info(f"    [DIRECT] Biome: {result['biome']} (raw: {biome_val})")

            # Read planet size (critical for moon detection)
            size_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.PLANET_SIZE)
            result["planet_size_raw"] = size_val
            result["planet_size"] = PLANET_SIZES.get(size_val, f"Unknown({size_val})")
            result["is_moon"] = (size_val == 3)  # Moon = 3
            logger.info(f"    [DIRECT] Size: {result['planet_size']} (raw: {size_val}, is_moon: {result['is_moon']})")

            # Read resources (16-byte strings)
            result["common_resource"] = self._read_string(planet_gen_addr, PlanetGenInputOffsets.COMMON_SUBSTANCE, 16)
            result["rare_resource"] = self._read_string(planet_gen_addr, PlanetGenInputOffsets.RARE_SUBSTANCE, 16)
            logger.info(f"    [DIRECT] Resources: common={result['common_resource']}, rare={result['rare_resource']}")

        except Exception as e:
            logger.error(f"Direct planet gen input read failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return result

    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
        """Fires AFTER solar system generation - data is now ready."""
        logger.info("=" * 40)
        logger.info("=== SYSTEM GENERATE COMPLETE ===")
        logger.info(f"  this pointer: {this}")
        logger.info(f"  lSeed: {lSeed}")
        logger.info("=" * 40)

        addr = get_addressof(this)
        if addr == 0:
            logger.warning("Generate hook: this pointer is NULL")
            return

        logger.info(f"  this address: 0x{addr:X}")

        try:
            self._cached_solar_system = map_struct(addr, nms.cGcSolarSystem)
            logger.info(f"  Cached solar system: {self._cached_solar_system}")
            self._pending_extraction = True

            # Reset event counter for new system
            self._scan_event_count = 0

            logger.info("")
            logger.info("=" * 60)
            logger.info(">>> NEW SYSTEM - Logging ALL scan events <<<")
            logger.info(">>> Use scanner room, watch for events, then Extract <<<")
            logger.info("=" * 60)
        except Exception as e:
            logger.error(f"Failed to cache solar system: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # =========================================================================
    # FREIGHTER SCANNER ROOM HOOK - NO QUIET PERIOD, NO SUPPRESSION
    # =========================================================================

    @nms.cGcScanEvent.Construct.after
    def on_scan_event_construct(self, this, leTable, lpEvent, lpBuilding, lfStartTime, lbMostImportant, lpMission):
        """
        Logs ALL leTable=3 events - NO filtering, NO early return.
        This tests if the hook survives without suppression.
        """
        # Only log leTable=3 (freighter scanner) events
        if leTable == self.SCAN_TABLE_FREIGHTER:
            self._scan_event_count += 1
            logger.info("=" * 50)
            logger.info(f">>> SCAN EVENT #{self._scan_event_count} (leTable=3) <<<")
            logger.info(f"  lbMostImportant={lbMostImportant}")
            logger.info(f"  lfStartTime={lfStartTime}")
            logger.info(f"  lpEvent=0x{lpEvent:X}" if lpEvent else "  lpEvent=NULL")
            logger.info("=" * 50)
        # NO return statement - let hook continue normally for ALL events

    # =========================================================================
    # APPVIEW - Just marks ready, does NOT extract (wait for scanner room)
    # =========================================================================

    @on_state_change("APPVIEW")
    def on_appview(self):
        """
        Fires when entering game view - player_state is now available.
        We do NOT extract here - we wait for the freighter scanner room.
        """
        if not self._pending_extraction:
            return

        self._pending_extraction = False

        logger.info("=" * 40)
        logger.info("=== APPVIEW STATE - READY ===")
        logger.info("=== Waiting for freighter scanner room... ===")
        logger.info("=" * 40)

    # =========================================================================
    # GUI BUTTONS
    # =========================================================================

    @gui_button("Check Planet Data")
    def check_planet_data(self):
        """
        Check if planet data is populated yet.
        Click BEFORE and AFTER using scanner room to see difference.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> CHECKING PLANET DATA POPULATION <<<")
        logger.info(f">>> Total scan events so far: {self._scan_event_count} <<<")
        logger.info("=" * 60)

        # Get solar system
        solar_system = self._cached_solar_system
        if not solar_system:
            simulation = gameData.simulation
            if simulation and simulation.mpSolarSystem:
                addr = get_addressof(simulation.mpSolarSystem)
                if addr != 0:
                    solar_system = map_struct(addr, nms.cGcSolarSystem)

        if not solar_system:
            logger.warning("No solar system available")
            return

        try:
            planets_array = solar_system.maPlanets
            sys_data = solar_system.mSolarSystemData
            planet_count = self._safe_int(sys_data.Planets) if hasattr(sys_data, 'Planets') else 6

            for i in range(min(planet_count, 6)):
                planet = planets_array[i]
                if planet is None:
                    continue

                planet_addr = get_addressof(planet)
                if planet_addr == 0:
                    continue

                # Check mPlanetData population
                weather = "NOT_POPULATED"
                name = f"Planet_{i+1}"
                sentinels = "NOT_POPULATED"
                flora = "NOT_POPULATED"
                fauna = "NOT_POPULATED"

                try:
                    if hasattr(planet, 'mPlanetData'):
                        pd = planet.mPlanetData
                        if hasattr(pd, 'Name'):
                            n = str(pd.Name)
                            if n and n != "None" and len(n) > 0:
                                name = n
                        if hasattr(pd, 'PlanetInfo'):
                            info = pd.PlanetInfo
                            if hasattr(info, 'Weather'):
                                w = str(info.Weather)
                                if w and w != "None":
                                    weather = w
                            if hasattr(info, 'SentinelsPerDifficulty'):
                                s = str(info.SentinelsPerDifficulty)
                                if s and s != "None":
                                    sentinels = s
                            if hasattr(info, 'Flora'):
                                f = str(info.Flora)
                                if f and f != "None":
                                    flora = f
                            if hasattr(info, 'Fauna'):
                                fa = str(info.Fauna)
                                if fa and fa != "None":
                                    fauna = fa
                except Exception as e:
                    logger.debug(f"Error checking planet {i}: {e}")

                # Log status
                populated = weather != "NOT_POPULATED"
                status = "POPULATED" if populated else "EMPTY"
                logger.info(f"  Planet {i} [{status}]: {name}")
                logger.info(f"    Weather: {weather}")
                logger.info(f"    Sentinels: {sentinels}")
                logger.info(f"    Flora: {flora}, Fauna: {fauna}")

        except Exception as e:
            logger.error(f"Check planet data failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info("=" * 60)

    @gui_button("Extract Now")
    def manual_extract(self):
        """
        Click this button AFTER using the freighter scanner room.
        This triggers extraction with complete planet data.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> MANUAL EXTRACTION TRIGGERED <<<")
        logger.info(f">>> Total scan events detected: {self._scan_event_count} <<<")
        logger.info("=" * 60)

        self._do_extraction(force=True, trigger=f"manual_button_{self._scan_event_count}_events")

    # =========================================================================
    # EXTRACTION LOGIC
    # =========================================================================

    def _do_extraction(self, force: bool = False, trigger: str = "unknown"):
        """Perform the actual extraction using cached or live solar system."""
        # Update extraction timestamp
        self._last_extraction_time = time.time()

        # Try cached solar system first
        solar_system = self._cached_solar_system
        if not solar_system:
            logger.info("No cached solar system - getting from gameData")
            simulation = gameData.simulation
            if not simulation:
                logger.warning("No simulation available")
                return

            ptr = simulation.mpSolarSystem
            if not ptr:
                logger.warning("No solar system pointer")
                return

            addr = get_addressof(ptr)
            if addr == 0:
                logger.warning("Solar system pointer is NULL")
                return

            solar_system = map_struct(addr, nms.cGcSolarSystem)

        logger.info(f"Solar system: {solar_system}")

        # Get system data
        sys_data = solar_system.mSolarSystemData
        logger.info(f"System data: {sys_data}")

        # CRITICAL: Cache the sys_data address for direct memory reads
        try:
            self._cached_sys_data_addr = get_addressof(sys_data)
            logger.info(f"  Cached sys_data address: 0x{self._cached_sys_data_addr:X}")
        except Exception as e:
            logger.warning(f"  Could not get sys_data address: {e}")
            self._cached_sys_data_addr = None

        # Check if we already extracted this system (skip if force=True)
        if not force:
            try:
                current_seed = self._safe_int(sys_data.Seed.Seed) if hasattr(sys_data, 'Seed') else 0
                if current_seed == self._last_extracted_seed and current_seed != 0:
                    logger.info(f"Already extracted system with seed {current_seed}")
                    return
                self._last_extracted_seed = current_seed
                logger.info(f"New system seed: {current_seed}")
            except Exception as e:
                logger.debug(f"Seed check failed: {e}")
        else:
            logger.info("Force extraction - skipping seed check")
            try:
                current_seed = self._safe_int(sys_data.Seed.Seed) if hasattr(sys_data, 'Seed') else 0
                self._last_extracted_seed = current_seed
            except Exception:
                pass

        # Get player coordinates
        coords = self._get_current_coordinates()
        if not coords:
            logger.warning("Could not get player coordinates")
            return

        logger.info(f"Coordinates: {coords.get('glyph_code')} in {coords.get('galaxy_name')}")

        extraction = {
            "extraction_time": datetime.now().isoformat(),
            "extractor_version": "7.6.0",
            "trigger": trigger,
            "source": "live_extraction",
            "scan_events_detected": self._scan_event_count,
            "discoverer_name": "HavenExtractor",
            "discovery_timestamp": int(datetime.now().timestamp()),
            **coords,
            **self._extract_system_properties(sys_data),
            "planets": self._extract_planets(solar_system),
        }

        extraction["planet_count"] = len(extraction["planets"])
        self._write_extraction(extraction)

        logger.info(f"Extraction complete: {extraction['glyph_code']} - {extraction['planet_count']} planets")

        # Clear cache after extraction
        self._cached_solar_system = None

    def _get_current_coordinates(self) -> Optional[dict]:
        """Get current galactic coordinates from player state."""
        try:
            player_state = gameData.player_state
            if not player_state:
                logger.warning("No player_state available")
                return None

            location = player_state.mLocation
            galactic_addr = location.GalacticAddress

            voxel_x = self._safe_int(galactic_addr.VoxelX)
            voxel_y = self._safe_int(galactic_addr.VoxelY)
            voxel_z = self._safe_int(galactic_addr.VoxelZ)
            system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
            planet_idx = self._safe_int(galactic_addr.PlanetIndex)
            galaxy_idx = self._safe_int(location.RealityIndex)

            logger.info(f"Raw coords: X={voxel_x}, Y={voxel_y}, Z={voxel_z}, Sys={system_idx}, Galaxy={galaxy_idx}")

            glyph_code = self._coords_to_glyphs(
                planet_idx, system_idx, voxel_x, voxel_y, voxel_z
            )

            galaxy_names = {
                0: "Euclid", 1: "Hilbert Dimension", 2: "Calypso",
                3: "Hesperius Dimension", 4: "Hyades", 5: "Ickjamatew",
                6: "Budullangr", 7: "Kikolgallr", 8: "Eltiensleen",
                9: "Eissentam", 10: "Elkupalos"
            }

            return {
                "system_name": f"System_{glyph_code}",
                "glyph_code": glyph_code,
                "galaxy_name": galaxy_names.get(galaxy_idx, f"Galaxy_{galaxy_idx}"),
                "galaxy_index": galaxy_idx,
                "voxel_x": voxel_x,
                "voxel_y": voxel_y,
                "voxel_z": voxel_z,
                "solar_system_index": system_idx,
            }
        except Exception as e:
            logger.error(f"Coordinate extraction failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _extract_system_properties(self, sys_data) -> dict:
        """Extract system-level properties using direct memory + NMS.py fallback."""
        result = {
            "star_type": "Unknown",
            "economy_type": "Unknown",
            "economy_strength": "Unknown",
            "conflict_level": "Unknown",
            "dominant_lifeform": "Unknown",
            "system_seed": 0,
        }

        # Try direct memory reads first (most reliable)
        if self._cached_sys_data_addr:
            logger.info("  Attempting DIRECT memory read for system properties...")
            direct_data = self._read_system_data_direct(self._cached_sys_data_addr)

            # Use direct data if we got valid results
            if direct_data.get("economy_type") != "Unknown":
                result["economy_type"] = direct_data["economy_type"]
            if direct_data.get("economy_strength") != "Unknown":
                result["economy_strength"] = direct_data["economy_strength"]
            if direct_data.get("conflict_level") != "Unknown":
                result["conflict_level"] = direct_data["conflict_level"]
            if direct_data.get("dominant_lifeform") != "Unknown":
                result["dominant_lifeform"] = direct_data["dominant_lifeform"]
            if direct_data.get("star_type") != "Unknown":
                result["star_type"] = direct_data["star_type"]

        # Fallback to NMS.py struct access for anything still Unknown
        logger.info("  Attempting NMS.py struct access for remaining properties...")

        try:
            if result["star_type"] == "Unknown" and hasattr(sys_data, 'Class'):
                result["star_type"] = self._safe_enum(sys_data.Class)
                logger.info(f"  [NMSPY] Star class: {result['star_type']}")
        except Exception as e:
            logger.debug(f"Class extraction failed: {e}")

        try:
            if hasattr(sys_data, 'TradingData'):
                trading = sys_data.TradingData
                if result["economy_type"] == "Unknown" and hasattr(trading, 'TradingClass'):
                    result["economy_type"] = self._safe_enum(trading.TradingClass)
                    logger.info(f"  [NMSPY] Economy type: {result['economy_type']}")
                if result["economy_strength"] == "Unknown" and hasattr(trading, 'WealthClass'):
                    result["economy_strength"] = self._safe_enum(trading.WealthClass)
                    logger.info(f"  [NMSPY] Wealth class: {result['economy_strength']}")
                if result["conflict_level"] == "Unknown" and hasattr(trading, 'ConflictLevel'):
                    result["conflict_level"] = self._safe_enum(trading.ConflictLevel)
        except Exception as e:
            logger.debug(f"TradingData extraction failed: {e}")

        try:
            if result["conflict_level"] == "Unknown" and hasattr(sys_data, 'ConflictData'):
                result["conflict_level"] = self._safe_enum(sys_data.ConflictData)
                logger.info(f"  [NMSPY] Conflict: {result['conflict_level']}")
        except Exception as e:
            logger.debug(f"ConflictData extraction failed: {e}")

        try:
            if hasattr(sys_data, 'InhabitingRace') and result["dominant_lifeform"] == "Unknown":
                result["dominant_lifeform"] = self._safe_enum(sys_data.InhabitingRace)
                logger.info(f"  [NMSPY] Race: {result['dominant_lifeform']}")
        except Exception as e:
            logger.debug(f"InhabitingRace extraction failed: {e}")

        try:
            if hasattr(sys_data, 'Seed') and hasattr(sys_data.Seed, 'Seed'):
                result["system_seed"] = self._safe_int(sys_data.Seed.Seed)
        except Exception as e:
            logger.debug(f"Seed extraction failed: {e}")

        logger.info(f"  FINAL system properties: {result}")
        return result

    def _extract_planets(self, solar_system) -> list:
        """Extract planet data - ONLY valid slots based on Planets count."""
        planets = []

        logger.info("=" * 30)
        logger.info("EXTRACTING PLANETS")
        logger.info("=" * 30)

        # Get actual planet count from system data - THIS IS CRITICAL
        # maPlanets is always a 6-slot array, but only first N have valid data
        actual_planet_count = 6  # Default max
        prime_planet_count = 0
        try:
            sys_data = solar_system.mSolarSystemData
            logger.info(f"  sys_data object: {sys_data}")

            if hasattr(sys_data, 'Planets'):
                planets_raw = sys_data.Planets
                actual_planet_count = self._safe_int(planets_raw)
                logger.info(f"  *** VALID PLANET COUNT: {actual_planet_count} (raw: {planets_raw}, type: {type(planets_raw).__name__})")

            if hasattr(sys_data, 'PrimePlanets'):
                prime_raw = sys_data.PrimePlanets
                prime_planet_count = self._safe_int(prime_raw)
                logger.info(f"  *** PRIME PLANETS: {prime_planet_count} (raw: {prime_raw}, type: {type(prime_raw).__name__})")
            else:
                logger.warning(f"  *** PrimePlanets attribute not found!")
        except Exception as e:
            logger.warning(f"  Could not get planet count: {e}")
            import traceback
            logger.warning(traceback.format_exc())

        # Calculate expected moons: total - prime = moons
        expected_moons = actual_planet_count - prime_planet_count
        logger.info(f"  *** EXPECTED MOONS: {expected_moons}")

        try:
            planets_array = solar_system.maPlanets
            logger.info(f"  planets_array object: {planets_array}")

            # CRITICAL FIX: Only iterate through VALID planet slots
            # Remaining slots (N to 5) contain default/empty data
            for i in range(min(actual_planet_count, 6)):
                try:
                    planet = planets_array[i]
                    logger.info(f"  --- Processing slot {i} ---")

                    if planet is None:
                        logger.warning(f"  Slot {i}: None (unexpected for valid slot)")
                        continue

                    planet_addr = get_addressof(planet)
                    if planet_addr == 0:
                        logger.warning(f"  Slot {i}: NULL pointer (unexpected for valid slot)")
                        continue

                    logger.info(f"  Slot {i}: address 0x{planet_addr:X}")

                    planet_data = self._extract_single_planet(planet, i)
                    if planet_data:
                        planets.append(planet_data)
                        body_type = "MOON" if planet_data.get('is_moon', False) else "PLANET"
                        logger.info(f"  Slot {i}: [{body_type}] {planet_data.get('planet_name', 'Unknown')} - {planet_data.get('biome', 'Unknown')}")
                    else:
                        logger.warning(f"  Slot {i}: Failed to extract data")

                except Exception as e:
                    logger.error(f"  Slot {i} extraction failed: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    continue

        except Exception as e:
            logger.error(f"Planet array access failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # Count results
        moon_count = sum(1 for p in planets if p.get('is_moon', False))
        planet_count = len(planets) - moon_count
        logger.info("=" * 30)
        logger.info(f"EXTRACTION COMPLETE: {planet_count} planets + {moon_count} moons = {len(planets)} total")
        logger.info(f"Expected: {prime_planet_count} planets + {expected_moons} moons = {actual_planet_count} total")
        logger.info("=" * 30)
        return planets

    def _extract_single_planet(self, planet, index: int) -> Optional[dict]:
        """Extract data from a single planet using direct memory + NMS.py fallback."""
        try:
            result = {
                "planet_index": index,
                "planet_name": f"Planet_{index + 1}",
                "biome": "Unknown",
                "biome_subtype": "Unknown",
                "weather": "Unknown",
                "sentinel_level": "Unknown",
                "flora_level": "Unknown",
                "fauna_level": "Unknown",
                "common_resource": "Unknown",
                "uncommon_resource": "Unknown",
                "rare_resource": "Unknown",
                "is_moon": False,
                "planet_size": "Unknown",
            }

            # =====================================================
            # FIRST: Try direct memory read from PlanetGenerationInputData
            # This is more reliable than NMS.py struct access
            # =====================================================
            if self._cached_sys_data_addr:
                logger.info(f"    Attempting DIRECT memory read for planet {index}...")
                direct_data = self._read_planet_gen_input_direct(self._cached_sys_data_addr, index)

                # Use direct data for critical fields
                if direct_data.get("biome") != "Unknown":
                    result["biome"] = direct_data["biome"]
                    result["biome_raw"] = direct_data.get("biome_raw", -1)
                if direct_data.get("planet_size") != "Unknown":
                    result["planet_size"] = direct_data["planet_size"]
                    result["is_moon"] = direct_data.get("is_moon", False)
                if direct_data.get("common_resource"):
                    result["common_resource"] = direct_data["common_resource"]
                if direct_data.get("rare_resource"):
                    result["rare_resource"] = direct_data["rare_resource"]

            # =====================================================
            # SECOND: NMS.py struct access for remaining data
            # =====================================================

            # Get actual planet index from the cGcPlanet struct
            try:
                if hasattr(planet, 'miPlanetIndex'):
                    actual_idx = self._safe_int(planet.miPlanetIndex)
                    result["planet_index"] = actual_idx
                    logger.info(f"    [NMSPY] miPlanetIndex = {actual_idx}")
            except Exception as e:
                logger.debug(f"    miPlanetIndex access failed: {e}")

            # NMS.PY FALLBACK: mPlanetGenerationInputData (only for fields still Unknown)
            try:
                if hasattr(planet, 'mPlanetGenerationInputData'):
                    gen_input = planet.mPlanetGenerationInputData
                    logger.info(f"    [NMSPY] gen_input: {gen_input}")

                    # PlanetSize - only if direct read failed
                    if result["planet_size"] == "Unknown" and hasattr(gen_input, 'PlanetSize'):
                        size_val = gen_input.PlanetSize
                        raw_size = None
                        try:
                            if hasattr(size_val, 'value'):
                                raw_size = size_val.value
                            else:
                                raw_size = int(size_val)
                        except:
                            pass

                        size_name = self._safe_enum(size_val)
                        result["planet_size"] = size_name
                        result["is_moon"] = (raw_size == 3) if raw_size is not None else (size_name == "Moon")
                        logger.info(f"    [NMSPY] PlanetSize = {size_name} (raw: {raw_size})")

                    # Biome - only if direct read failed
                    if result["biome"] == "Unknown" and hasattr(gen_input, 'Biome'):
                        biome_val = gen_input.Biome
                        raw_biome = None
                        try:
                            if hasattr(biome_val, 'value'):
                                raw_biome = biome_val.value
                            else:
                                raw_biome = int(biome_val)
                        except:
                            pass
                        result["biome"] = self._safe_enum(biome_val)
                        logger.info(f"    [NMSPY] Biome = {result['biome']} (raw: {raw_biome})")

                    # BiomeSubType
                    if result["biome_subtype"] == "Unknown" and hasattr(gen_input, 'BiomeSubType'):
                        result["biome_subtype"] = self._safe_enum(gen_input.BiomeSubType)
                        logger.info(f"    [NMSPY] BiomeSubType = {result['biome_subtype']}")

                    # Resources - only if direct read failed
                    if result["common_resource"] == "Unknown" and hasattr(gen_input, 'CommonSubstance'):
                        val = str(gen_input.CommonSubstance)
                        if val and val != "None" and len(val) > 0:
                            result["common_resource"] = val
                            logger.info(f"    [NMSPY] CommonSubstance = {val}")

                    if result["rare_resource"] == "Unknown" and hasattr(gen_input, 'RareSubstance'):
                        val = str(gen_input.RareSubstance)
                        if val and val != "None" and len(val) > 0:
                            result["rare_resource"] = val
                            logger.info(f"    [NMSPY] RareSubstance = {val}")
            except Exception as e:
                logger.warning(f"    [NMSPY] mPlanetGenerationInputData access failed: {e}")

            # SECONDARY SOURCE: mPlanetData (has name, weather, sentinels, etc.)
            planet_data = None
            try:
                if hasattr(planet, 'mPlanetData'):
                    planet_data = planet.mPlanetData
            except Exception:
                pass

            if planet_data is not None:
                # Planet name
                try:
                    if hasattr(planet_data, 'Name'):
                        name = str(planet_data.Name)
                        if name and len(name) > 0 and name != "None":
                            result["planet_name"] = name
                except Exception:
                    pass

                # PlanetInfo - display strings
                try:
                    if hasattr(planet_data, 'PlanetInfo'):
                        info = planet_data.PlanetInfo

                        # If biome still unknown, try PlanetType string
                        if result["biome"] == "Unknown" and hasattr(info, 'PlanetType'):
                            pt = str(info.PlanetType)
                            if pt and pt != "None" and len(pt) > 0:
                                result["biome"] = pt

                        if hasattr(info, 'Weather'):
                            val = str(info.Weather)
                            if val and val != "None":
                                result["weather"] = val

                        if hasattr(info, 'SentinelsPerDifficulty'):
                            val = str(info.SentinelsPerDifficulty)
                            if val and val != "None":
                                result["sentinel_level"] = val

                        if hasattr(info, 'Flora'):
                            val = str(info.Flora)
                            if val and val != "None":
                                result["flora_level"] = val

                        if hasattr(info, 'Fauna'):
                            val = str(info.Fauna)
                            if val and val != "None":
                                result["fauna_level"] = val
                except Exception:
                    pass

                # Resources from PlanetData if not already set
                try:
                    if result["common_resource"] == "Unknown" and hasattr(planet_data, 'CommonSubstanceID'):
                        val = str(planet_data.CommonSubstanceID)
                        if val and val != "None":
                            result["common_resource"] = val

                    if hasattr(planet_data, 'UncommonSubstanceID'):
                        val = str(planet_data.UncommonSubstanceID)
                        if val and val != "None":
                            result["uncommon_resource"] = val

                    if result["rare_resource"] == "Unknown" and hasattr(planet_data, 'RareSubstanceID'):
                        val = str(planet_data.RareSubstanceID)
                        if val and val != "None":
                            result["rare_resource"] = val
                except Exception:
                    pass

            return result

        except Exception as e:
            logger.debug(f"Planet {index} data extraction failed: {e}")
            return None

    def _safe_enum(self, val, default: str = "Unknown") -> str:
        """Safely convert enum to string."""
        try:
            if val is None:
                return default
            if hasattr(val, 'name'):
                return val.name
            if hasattr(val, 'value'):
                return str(val.value)
            return str(val)
        except Exception:
            return default

    def _safe_int(self, val, default: int = 0) -> int:
        """Safely convert value to int."""
        try:
            if val is None:
                return default
            if hasattr(val, 'value'):
                return int(val.value)
            return int(val)
        except Exception:
            return default

    def _coords_to_glyphs(self, planet: int, system: int, x: int, y: int, z: int) -> str:
        """Convert coordinates to portal glyph code."""
        try:
            portal_x = (x + 2047) & 0xFFF
            portal_y = (y + 127) & 0xFF
            portal_z = (z + 2047) & 0xFFF
            portal_sys = system & 0x1FF
            portal_planet = planet & 0xF
            glyph = f"{portal_planet:01X}{portal_sys:03X}{portal_y:02X}{portal_z:03X}{portal_x:03X}"
            return glyph.upper()
        except Exception:
            return "000000000000"

    def _write_extraction(self, data: dict):
        """Send extraction to Haven UI API and save local backup."""
        # Always save local backup first
        try:
            latest = self._output_dir / "latest.json"
            with open(latest, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            timestamped = self._output_dir / f"extraction_{ts}.json"
            with open(timestamped, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(f"Local backup saved to: {latest}")

        except Exception as e:
            logger.error(f"Local save failed: {e}")

        # Send to Haven UI API if configured
        if API_BASE_URL:
            self._send_to_api(data)
        else:
            logger.warning("API_BASE_URL not configured - data saved locally only")
            logger.warning("To enable remote sync, set API_BASE_URL in haven_extractor.py")

    def _send_to_api(self, data: dict):
        """Send extraction data to Haven UI via HTTP POST."""
        api_url = f"{API_BASE_URL.rstrip('/')}/api/extraction"

        try:
            # Prepare JSON payload
            json_data = json.dumps(data, default=str).encode('utf-8')

            # Create request
            req = urllib.request.Request(
                api_url,
                data=json_data,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': f'HavenExtractor/{self.__version__}',
                }
            )

            # Add API key if configured
            if API_KEY:
                req.add_header('X-API-Key', API_KEY)

            # Create SSL context that doesn't verify certificates (for ngrok)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            # Send request
            logger.info(f"Sending extraction to: {api_url}")
            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                response_data = json.loads(response.read().decode('utf-8'))

                if response.status in (200, 201):
                    logger.info("=" * 50)
                    logger.info(">>> EXTRACTION SENT TO HAVEN UI <<<")
                    logger.info(f"  Status: {response_data.get('status', 'ok')}")
                    logger.info(f"  Message: {response_data.get('message', '')}")
                    logger.info(f"  Submission ID: {response_data.get('submission_id', 'N/A')}")
                    logger.info(f"  Planets: {response_data.get('planet_count', 0)}")
                    logger.info(f"  Moons: {response_data.get('moon_count', 0)}")
                    logger.info("=" * 50)
                else:
                    logger.warning(f"API returned status {response.status}: {response_data}")

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
            except:
                pass
            logger.error(f"API request failed (HTTP {e.code}): {error_body}")
            logger.error("Data saved locally - you can manually submit later")

        except urllib.error.URLError as e:
            logger.error(f"Cannot connect to API: {e.reason}")
            logger.error(f"Check that API_BASE_URL is correct: {API_BASE_URL}")
            logger.error("Data saved locally - you can manually submit later")

        except Exception as e:
            logger.error(f"API send failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            logger.error("Data saved locally - you can manually submit later")
