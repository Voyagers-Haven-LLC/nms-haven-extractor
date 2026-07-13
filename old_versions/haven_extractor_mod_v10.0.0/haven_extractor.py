# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# ///
"""
Haven Extractor v10.0.0 - Remote Region Enumeration

Extracts planet data from NMS and sends it to Haven UI via API.
Works with ngrok for remote connections.

v10.0.0 - REMOTE REGION ENUMERATION:
- Scan ALL 512 systems in current region WITHOUT visiting them!
- Uses ClassifyStarSystem static function to query remote systems
- Captures: star type, economy, conflict, race, planet count, planet sizes
- Marks data as "remote" (enumerated) vs "visited" (full detail)

v9.0.0 - STREAMLINED:
- Removed terrain/color extraction (research complete)
- Core planet data: biome, weather, resources, flora/fauna/sentinels

BATCH MODE:
- Visit MULTIPLE star systems, all data stored in memory
- Click "Export Batch" to save ALL systems to a single JSON

REGION SCAN MODE (NEW):
- Click "Scan Region" to enumerate all 512 systems in current region
- Captures system-level data remotely (no warp required!)
- Click "Export Region" to save the enumerated region data
- Data marked as "remote" to distinguish from visited systems

SETUP:
1. Create config.json in Documents/Haven-Extractor/ with your API URL:
   {"api_url": "https://your-ngrok-url.ngrok-free.app"}
2. Or place haven_config.json in the same folder as this mod

Data extracted remotely (per system):
- star_type, economy_type, economy_strength, conflict_level
- dominant_lifeform, planet_count, planet_sizes
- is_abandoned, is_pirate, is_gas_giant

Data requires visiting (per planet):
- weather, flora_level, fauna_level, sentinel_level
- planet_name (procedurally generated but needs visit for display)

Data is:
- Saved locally as backup in Documents/Haven-Extractor/
- NOT auto-sent to Haven UI (manual upload via Save Watcher)
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
import nmspy.data.exported_types as nmse
from nmspy.decorators import on_state_change
from nmspy.common import gameData
from ctypes import c_uint64, pointer, sizeof

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

# GcBiomeSubType enum values (from MBINCompiler/libMBIN - full 32 value enum)
BIOME_SUBTYPES = {
    0: "None",          # None_
    1: "Standard",      # Standard
    2: "HighQuality",   # HighQuality
    3: "Structure",     # Structure
    4: "Beam",          # Beam
    5: "Hexagon",       # Hexagon
    6: "FractCube",     # FractCube
    7: "Bubble",        # Bubble
    8: "Shards",        # Shards
    9: "Contour",       # Contour
    10: "Shell",        # Shell
    11: "BoneSpire",    # BoneSpire
    12: "WireCell",     # WireCell
    13: "HydroGarden",  # HydroGarden
    14: "HugePlant",    # HugePlant
    15: "HugeLush",     # HugeLush
    16: "HugeRing",     # HugeRing
    17: "HugeRock",     # HugeRock
    18: "HugeScorch",   # HugeScorch
    19: "HugeToxic",    # HugeToxic
    20: "Variant_A",    # Variant_A
    21: "Variant_B",    # Variant_B
    22: "Variant_C",    # Variant_C
    23: "Variant_D",    # Variant_D
    24: "Infested",     # Infested
    25: "Swamp",        # Swamp
    26: "Lava",         # Lava
    27: "Worlds",       # Worlds
    28: "Remix_A",      # Remix_A
    29: "Remix_B",      # Remix_B
    30: "Remix_C",      # Remix_C
    31: "Remix_D",      # Remix_D
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

# cGcWeatherOptions enum values (from nmspy/libMBIN - 17 values)
# This is used for planet_data.Weather.WeatherType (reliable for all planets)
WEATHER_OPTIONS = {
    0: "Clear",
    1: "Dust",
    2: "Humid",
    3: "Snow",
    4: "Toxic",
    5: "Scorched",
    6: "Radioactive",
    7: "RedWeather",
    8: "GreenWeather",
    9: "BlueWeather",
    10: "Swamp",
    11: "Lava",
    12: "Bubble",
    13: "Weird",
    14: "Fire",
    15: "ClearCold",
    16: "GasGiant",
}

# Storm frequency enum for weather data
STORM_FREQUENCY = {
    0: "None",
    1: "Low",
    2: "High",
    3: "Always",
}

# Resource ID to human-readable name mapping
RESOURCE_NAMES = {
    # Stellar metals (found in deposits)
    "YELLOW": "Copper",
    "YELLOW2": "Chromatic Metal",
    "RED": "Cadmium",
    "RED2": "Chromatic Metal",
    "GREEN": "Emeril",
    "GREEN2": "Chromatic Metal",
    "BLUE": "Indium",
    "BLUE2": "Chromatic Metal",
    "PURPLE": "Indium",
    "PURPLE2": "Indium",
    # Activated stellar metals (extreme weather planets)
    "EX_YELLOW": "Activated Copper",
    "EX_RED": "Activated Cadmium",
    "EX_GREEN": "Activated Emeril",
    "EX_BLUE": "Activated Indium",
    "EX_PURPLE": "Activated Indium",
    # Biome-specific resources
    "COLD1": "Dioxite",
    "SNOW1": "Dioxite",
    "HOT1": "Phosphorus",
    "LUSH1": "Paraffinium",
    "DUSTY1": "Pyrite",
    "TOXIC1": "Ammonia",
    "RADIO1": "Uranium",
    "SWAMP1": "Faecium",
    "LAVA1": "Basalt",
    "WEIRD1": "Magnetised Ferrite",
    # Common elements
    "FUEL1": "Carbon",
    "FUEL2": "Condensed Carbon",
    "LAND1": "Ferrite Dust",
    "LAND2": "Pure Ferrite",
    "LAND3": "Magnetised Ferrite",
    "OXYGEN": "Oxygen",
    "CATALYST1": "Sodium",
    "CATALYST2": "Sodium Nitrate",
    "LAUNCHSUB": "Di-hydrogen",
    "LAUNCHSUB2": "Di-hydrogen Jelly",
    "CAVE1": "Cobalt",
    "CAVE2": "Ionised Cobalt",
    "WATER1": "Salt",
    "WATER2": "Chlorine",
    "ASTEROID1": "Silver",
    "ASTEROID2": "Gold",
    "ASTEROID3": "Platinum",
    # Plant/Flora resources
    "PLANT_POOP": "Mordite",
    "PLANT_TOXIC": "Fungal Mould",
    "PLANT_SNOW": "Frost Crystal",
    "PLANT_HOT": "Solanium",
    "PLANT_RADIO": "Gamma Root",
    "PLANT_DUST": "Cactus Flesh",
    "PLANT_LUSH": "Star Bulb",
    "PLANT_CAVE": "Marrow Bulb",
    "PLANT_WATER": "Kelp Sac",
    # Rare resources
    "RARE1": "Rusted Metal",
    "RARE2": "Living Pearl",
    # Space/Anomaly resources
    "SPACEGUNK1": "Residual Goop",
    "SPACEGUNK2": "Runaway Mould",
    "SPACEGUNK3": "Living Slime",
    "SPACEGUNK4": "Viscous Fluids",
    "SPACEGUNK5": "Tainted Metal",
    # Special biome resources
    "ROBOT1": "Pugneum",
    "GAS1": "Nitrogen",
    "GAS2": "Sulphurine",
    "GAS3": "Radon",
}


def translate_resource(resource_id: str) -> str:
    """Translate a resource ID to human-readable name."""
    if not resource_id or resource_id == "Unknown" or resource_id == "":
        return resource_id
    # Direct lookup
    if resource_id in RESOURCE_NAMES:
        return RESOURCE_NAMES[resource_id]
    # Try uppercase
    if resource_id.upper() in RESOURCE_NAMES:
        return RESOURCE_NAMES[resource_id.upper()]
    # Return original if no mapping found
    return resource_id


def clean_weather_string(weather_str: str) -> str:
    """Clean raw weather strings like 'weather_glitch 6' to readable names."""
    if not weather_str or weather_str == "Unknown" or weather_str == "":
        return weather_str

    # Already a clean name from WEATHER_OPTIONS
    clean_names = ["Clear", "Dust", "Humid", "Snow", "Toxic", "Scorched",
                   "Radioactive", "RedWeather", "GreenWeather", "BlueWeather",
                   "Swamp", "Lava", "Bubble", "Weird", "Fire", "ClearCold", "GasGiant"]
    if weather_str in clean_names:
        return weather_str

    # Map raw weather string prefixes to readable names
    weather_mappings = {
        "weather_glitch": "Glitch",
        "weather_lava": "Lava",
        "weather_frozen": "Frozen",
        "weather_cold": "Cold",
        "weather_hot": "Scorched",
        "weather_toxic": "Toxic",
        "weather_radioactive": "Radioactive",
        "weather_dust": "Dusty",
        "weather_humid": "Humid",
        "weather_scorched": "Scorched",
        "weather_swamp": "Swamp",
        "weather_bubble": "Bubble",
        "weather_weird": "Weird",
        "weather_fire": "Fire",
        "weather_storm": "Stormy",
        "weather_clear": "Clear",
        "weather_normal": "Normal",
        "weather_snow": "Snow",
        "weather_blizzard": "Blizzard",
        "weather_extreme": "Extreme",
        "lush": "Lush",
        "toxic": "Toxic",
        "scorched": "Scorched",
        "frozen": "Frozen",
        "barren": "Barren",
        "dead": "Dead",
        "weird": "Weird",
        "red": "Red",
        "green": "Green",
        "blue": "Blue",
    }

    # Convert to lowercase for matching
    lower_weather = weather_str.lower()

    # Try to match prefix
    for prefix, readable in weather_mappings.items():
        if lower_weather.startswith(prefix):
            return readable

    # Try to extract meaningful part (remove numbers and underscores)
    import re
    cleaned = re.sub(r'[_\d]+$', '', weather_str)  # Remove trailing numbers and underscores
    cleaned = cleaned.replace('_', ' ').title()
    if cleaned and cleaned != weather_str:
        return cleaned

    return weather_str


class HavenExtractorMod(Mod):
    __author__ = "Voyagers Haven"
    __version__ = "10.0.0"
    __description__ = "Remote region enumeration + core planet data extraction"

    # Mapping for flora/fauna levels (NMS adjectives)
    FLORA_LEVELS = {0: "None", 1: "Sparse", 2: "Average", 3: "Bountiful"}
    FAUNA_LEVELS = {0: "None", 1: "Scarce", 2: "Regular", 3: "Copious"}
    # Legacy mapping for compatibility
    LIFE_LEVELS = {0: "None", 1: "Sparse", 2: "Average", 3: "Abundant"}
    # Sentinel activity levels
    SENTINEL_LEVELS = {0: "Minimal", 1: "Limited", 2: "High", 3: "Aggressive"}

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

        # =====================================================
        # CRITICAL v8.1.0: Captured planet data from GenerateCreatureRoles hook
        # This dictionary stores Flora, Fauna, Sentinels captured by the hook
        # Key: planet_index, Value: dict with captured data
        # =====================================================
        self._captured_planets = {}
        self._capture_enabled = False  # Only capture after system generates

        # =====================================================
        # v8.2.0: BATCH MODE - Store multiple star systems
        # Allows collecting data from many systems before export
        # =====================================================
        self._batch_systems = []  # List of completed system extractions
        self._current_system_coords = None  # Coords captured when entering system
        self._current_system_props = None  # System properties captured when entering
        self._batch_mode_enabled = True  # Enable batch collection by default

        # =====================================================
        # Track if current system has been saved to batch
        # Prevents duplicate saves
        # =====================================================
        self._system_saved_to_batch = False

        # =====================================================
        # v10.0.0: REGION ENUMERATION - Remote system scanning
        # Stores data for all 512 systems in current region
        # =====================================================
        self._region_systems = []  # List of remotely enumerated systems
        self._current_region_coords = None  # Voxel coords of current region
        self._region_scan_in_progress = False
        self._region_scan_progress = 0

        logger.info("=" * 60)
        logger.info("Haven Extractor v10.0.0 - Remote Region Enumeration")
        logger.info("Extracts: system data remotely + planet data on visit")
        logger.info(f"Local backup: {self._output_dir}")
        logger.info("=" * 60)
        logger.info("")
        logger.info("*** NEW: REGION SCAN MODE ***")
        logger.info("Click 'Scan Region' to enumerate ALL 512 systems remotely!")
        logger.info("No warping required - captures system-level data from anywhere")
        logger.info("")
        logger.info("*** BATCH MODE (for visited systems) ***")
        logger.info("1. Warp to system - data captured AND SAVED to batch automatically")
        logger.info("2. Warp to more systems - each saved to batch when ready")
        logger.info("3. Click 'Export Batch' to export ALL systems to JSON")
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
            "biome_subtype": "Unknown",
            "biome_subtype_raw": -1,
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

            # Read biome subtype
            biome_subtype_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME_SUBTYPE)
            result["biome_subtype_raw"] = biome_subtype_val
            result["biome_subtype"] = BIOME_SUBTYPES.get(biome_subtype_val, f"Unknown({biome_subtype_val})")
            logger.info(f"    [DIRECT] BiomeSubType: {result['biome_subtype']} (raw: {biome_subtype_val})")

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

    # =========================================================================
    # UTILITY FUNCTIONS
    # =========================================================================

    def _safe_float(self, val, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _safe_enum(self, val, default: str = "Unknown") -> str:
        """Safely get enum name."""
        if val is None:
            return default
        if hasattr(val, 'name'):
            return str(val.name)
        if hasattr(val, 'value'):
            return str(val.value)
        return str(val)

    # =========================================================================
    # v8.2.0: BATCH MODE - Save current system to batch storage
    # =========================================================================

    def _save_current_system_to_batch(self):
        """
        Save the current system's captured data to batch storage.
        Called automatically when warping to a new system.
        """
        # Skip if batch mode disabled or no captured data
        if not self._batch_mode_enabled:
            return
        if not self._captured_planets:
            logger.debug("[BATCH] No captured planets to save")
            return
        if not self._cached_solar_system:
            logger.debug("[BATCH] No cached solar system to save")
            return

        try:
            # Get coordinates for the system we're leaving
            coords = self._get_current_coordinates()
            if not coords:
                logger.warning("[BATCH] Could not get coordinates for batch save")
                return

            # Check if this system is already in batch (by glyph code)
            glyph_code = coords.get('glyph_code', '')
            for existing in self._batch_systems:
                if existing.get('glyph_code') == glyph_code:
                    logger.info(f"[BATCH] System {glyph_code} already in batch, skipping duplicate")
                    return

            # Get system data
            sys_data = self._cached_solar_system.mSolarSystemData

            # Build the system extraction
            data_source = "captured_hook" if len(self._captured_planets) > 0 else "memory_read"

            system_data = {
                "extraction_time": datetime.now().isoformat(),
                "extractor_version": "8.3.0",
                "trigger": "batch_auto_save",
                "source": "live_extraction",
                "data_source": data_source,
                "captured_planet_count": len(self._captured_planets),
                "discoverer_name": "HavenExtractor",
                "discovery_timestamp": int(datetime.now().timestamp()),
                **coords,
                **self._extract_system_properties(sys_data),
                "planets": self._extract_planets(self._cached_solar_system),
            }
            system_data["planet_count"] = len(system_data["planets"])

            # Add to batch
            self._batch_systems.append(system_data)

            logger.info("")
            logger.info("*" * 60)
            logger.info(f">>> [BATCH] SYSTEM SAVED TO BATCH! <<<")
            logger.info(f"    Glyph: {glyph_code}")
            logger.info(f"    Planets: {system_data['planet_count']}")
            logger.info(f"    Total systems in batch: {len(self._batch_systems)}")
            logger.info("*" * 60)
            logger.info("")

        except Exception as e:
            logger.error(f"[BATCH] Failed to save system to batch: {e}")
            import traceback
            logger.error(traceback.format_exc())

    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
        """Fires AFTER solar system generation - data is now ready."""
        logger.info("=" * 40)
        logger.info("=== SYSTEM GENERATE COMPLETE ===")
        logger.info(f"  this pointer: {this}")
        logger.info(f"  lSeed: {lSeed}")
        logger.info("=" * 40)

        # =====================================================
        # v8.2.0: BATCH MODE - Save previous system BEFORE clearing!
        # This preserves the data from the system we just left
        # =====================================================
        if self._batch_mode_enabled and self._captured_planets and not self._system_saved_to_batch:
            logger.info("  [BATCH] Saving previous system to batch before clearing...")
            self._save_current_system_to_batch()

        addr = get_addressof(this)
        if addr == 0:
            logger.warning("Generate hook: this pointer is NULL")
            return

        logger.info(f"  this address: 0x{addr:X}")

        try:
            self._cached_solar_system = map_struct(addr, nms.cGcSolarSystem)
            logger.info(f"  Cached solar system: {self._cached_solar_system}")
            self._pending_extraction = True

            # =====================================================
            # v8.1.0: Clear captured planets for new system
            # =====================================================
            self._captured_planets.clear()
            self._capture_enabled = True
            self._system_saved_to_batch = False  # v8.2.2: Reset save flag for new system
            logger.info("  [v8.1.0] Captured planet data cleared for new system")
            logger.info("  [v8.1.0] Capture ENABLED - GenerateCreatureRoles hook active")

            logger.info("")
            logger.info("=" * 60)
            logger.info(">>> NEW SYSTEM - Planet data will be captured automatically <<<")
            logger.info(f">>> Systems in batch: {len(self._batch_systems)} <<<")
            logger.info(">>> System saves on next warp or Export Batch <<<")
            logger.info("=" * 60)
        except Exception as e:
            logger.error(f"Failed to cache solar system: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # =========================================================================
    # CRITICAL v8.1.0: GenerateCreatureRoles HOOK - Captures Flora/Fauna/Sentinels
    # This hook fires for EVERY planet as the game generates creature roles.
    # The lPlanetData parameter contains the actual planet data we need!
    # =========================================================================

    @nms.cGcPlanetGenerator.GenerateCreatureRoles.after
    def on_creature_roles_generate(self, this, lPlanetData, lUA):
        """
        Captures planet data when GenerateCreatureRoles is called.

        This hook fires for EACH planet in the system, receiving the full
        cGcPlanetData structure with Flora, Fauna, Sentinels, Weather, etc.

        This is the CRITICAL hook that was working in v7.9.6!

        IMPORTANT: We limit capture to 6 planets max because the hook also
        fires for nearby systems during galaxy discovery. Only the first 6
        belong to the current system.
        """
        # v10.0.1: CRITICAL - Cache coordinates from lUA for region scan feature FIRST
        # This runs even when _capture_enabled is False (e.g., loading into a save)
        if not self._current_system_coords:
            try:
                # lUA is a GcUniverseAddressData struct (same structure as player_state.mLocation)
                # It has .GalacticAddress and .RealityIndex properties
                galaxy_idx = 0
                voxel_x = voxel_y = voxel_z = system_idx = 0

                # Try to access the struct directly (lUA might be already mapped)
                if hasattr(lUA, 'GalacticAddress'):
                    ga = lUA.GalacticAddress
                    voxel_x = self._safe_int(ga.VoxelX)
                    voxel_y = self._safe_int(ga.VoxelY)
                    voxel_z = self._safe_int(ga.VoxelZ)
                    system_idx = self._safe_int(ga.SolarSystemIndex)
                    if hasattr(lUA, 'RealityIndex'):
                        galaxy_idx = self._safe_int(lUA.RealityIndex)
                else:
                    # Try mapping the pointer to the struct
                    ua_addr = get_addressof(lUA)
                    if ua_addr != 0:
                        # Use nmse.cGcUniverseAddressData from exported_types
                        ua_struct = map_struct(ua_addr, nmse.cGcUniverseAddressData)
                        if hasattr(ua_struct, 'GalacticAddress'):
                            ga = ua_struct.GalacticAddress
                            voxel_x = self._safe_int(ga.VoxelX)
                            voxel_y = self._safe_int(ga.VoxelY)
                            voxel_z = self._safe_int(ga.VoxelZ)
                            system_idx = self._safe_int(ga.SolarSystemIndex)
                        if hasattr(ua_struct, 'RealityIndex'):
                            galaxy_idx = self._safe_int(ua_struct.RealityIndex)

                # Only cache if we got valid data
                if voxel_x != 0 or voxel_y != 0 or voxel_z != 0:
                    galaxy_names = {
                        0: "Euclid", 1: "Hilbert Dimension", 2: "Calypso",
                        3: "Hesperius Dimension", 4: "Hyades", 5: "Ickjamatew",
                    }
                    galaxy_name = galaxy_names.get(galaxy_idx, f"Galaxy_{galaxy_idx}")
                    glyph_code = self._coords_to_glyphs(0, system_idx, voxel_x, voxel_y, voxel_z)

                    self._current_system_coords = {
                        "voxel_x": voxel_x,
                        "voxel_y": voxel_y,
                        "voxel_z": voxel_z,
                        "system_index": system_idx,
                        "planet_index": 0,
                        "galaxy_index": galaxy_idx,
                        "galaxy_name": galaxy_name,
                        "glyph_code": glyph_code,
                    }
                    logger.info(f"[v10.0.1] Cached coords from GenerateCreatureRoles: {glyph_code} in {galaxy_name}")
            except Exception as e:
                logger.info(f"[v10.0.1] Could not cache coords from lUA: {e}")

        if not self._capture_enabled:
            return

        # CRITICAL: Limit to 6 planets max (NMS max planets per system)
        # After APPVIEW, the hook fires for nearby systems - ignore those!
        if len(self._captured_planets) >= 6:
            return

        try:
            # Get the planet data pointer
            planet_data_addr = get_addressof(lPlanetData)
            if planet_data_addr == 0:
                logger.debug("GenerateCreatureRoles: lPlanetData is NULL")
                return

            # Map to cGcPlanetData structure (using global imports)
            planet_data = map_struct(planet_data_addr, nmse.cGcPlanetData)

            # Determine planet index - use length of captured dict
            planet_index = len(self._captured_planets)

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

            # Extract Sentinels (from GroundCombatDataPerDifficulty[0].SentinelLevel)
            # GroundCombatDataPerDifficulty is an array with 4 entries (one per difficulty)
            # We use index 0 for normal difficulty
            sentinel_raw = 0
            sentinel_name = "Unknown"
            try:
                if hasattr(planet_data, 'GroundCombatDataPerDifficulty'):
                    combat_data_array = planet_data.GroundCombatDataPerDifficulty
                    # Access the first element (normal difficulty)
                    if hasattr(combat_data_array, '__getitem__'):
                        combat_data = combat_data_array[0]
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

            # CRITICAL v8.1.6: Extract Biome, BiomeSubType, and Size from GenerationData
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
                    # CRITICAL v8.1.9: Extract Size from GenerationData (offset 0x144)
                    # This is the RELIABLE source for planet_size - direct memory read gives garbage
                    if hasattr(gen_data, 'Size'):
                        size_val = gen_data.Size
                        if hasattr(size_val, 'value'):
                            planet_size_raw = size_val.value
                        else:
                            planet_size_raw = int(size_val) if size_val is not None else -1
                        planet_size_name = PLANET_SIZES.get(planet_size_raw, f"Unknown({planet_size_raw})")
                        is_moon = (planet_size_raw == 3)  # Moon = 3
                    logger.info(f"    [HOOK] GenerationData: Biome={biome_name}({biome_raw}), SubType={biome_subtype_name}({biome_subtype_raw}), Size={planet_size_name}({planet_size_raw})")
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

            # CRITICAL v8.1.8: Extract weather from cGcPlanetData.Weather.WeatherType
            # This uses the actual Weather structure (offset 0x1C00) with enum values
            # Works for ALL planets, not just visited ones like PlanetInfo.Weather
            weather = ""
            weather_raw = -1
            storm_frequency = ""
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
                        logger.info(f"    [HOOK] Weather from cGcPlanetWeatherData: {weather} (raw: {weather_raw})")
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
                                weather = fallback_weather
                                logger.info(f"    [HOOK] Weather fallback from PlanetInfo: {weather}")
                except Exception as e:
                    logger.debug(f"Weather fallback extraction failed: {e}")

            # CRITICAL v8.1.7: Extract planet Name from cGcPlanetData.Name (offset 0x396E)
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
                            logger.info(f"    [HOOK] Planet Name from cGcPlanetData: '{planet_name}'")
            except Exception as e:
                logger.debug(f"Planet name extraction failed: {e}")

            # Store captured planet data
            self._captured_planets[planet_index] = {
                'flora_raw': flora_raw,
                'flora': flora_name,
                'fauna_raw': fauna_raw,
                'fauna': fauna_name,
                'sentinel_raw': sentinel_raw,
                'sentinel': sentinel_name,
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
                'planet_name': planet_name,
            }

            logger.info("")
            logger.info("*" * 60)
            logger.info(f">>> CAPTURED PLANET {planet_index} DATA! <<<")
            logger.info(f"    Name: {planet_name or '(not set)'}")
            logger.info(f"    Biome: {biome_name} ({biome_raw})")
            logger.info(f"    BiomeSubType: {biome_subtype_name} ({biome_subtype_raw})")
            logger.info(f"    Size: {planet_size_name} ({planet_size_raw}) [is_moon={is_moon}]")
            logger.info(f"    Flora: {flora_name} ({flora_raw})")
            logger.info(f"    Fauna: {fauna_name} ({fauna_raw})")
            logger.info(f"    Sentinels: {sentinel_name} ({sentinel_raw})")
            logger.info(f"    Weather: {weather} (raw: {weather_raw}, storms: {storm_frequency})")
            logger.info(f"    Resources: {common_resource}, {uncommon_resource}, {rare_resource}")
            logger.info(f"    Total captured: {len(self._captured_planets)} planets")
            logger.info("*" * 60)
            logger.info("")

        except Exception as e:
            logger.error(f"GenerateCreatureRoles capture failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # =========================================================================
    # APPVIEW - Marks system ready for extraction
    # =========================================================================

    @on_state_change("APPVIEW")
    def on_appview(self):
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

        # Auto-save to batch when APPVIEW fires (if not already saved)
        if self._batch_mode_enabled and self._captured_planets and not self._system_saved_to_batch:
            logger.info("[BATCH] Auto-saving system to batch...")
            self._save_current_system_to_batch()
            self._system_saved_to_batch = True
            logger.info(f"[BATCH] Systems in batch: {len(self._batch_systems)}")
        elif self._system_saved_to_batch:
            logger.info("[BATCH] System already saved")
            logger.info(f"[BATCH] Systems in batch: {len(self._batch_systems)}")

        logger.info(">>> Click 'Export Batch' to save all systems <<<")
        logger.info("=" * 40)

    # =========================================================================
    # GUI BUTTONS
    # =========================================================================

    @gui_button("Check Planet Data")
    def check_planet_data(self):
        """
        Check planet data status - shows both mPlanetData AND captured hook data.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> PLANET DATA STATUS <<<")
        logger.info(f">>> Captured planets via hook: {len(self._captured_planets)} <<<")
        logger.info("=" * 60)

        # First, show captured hook data summary
        if self._captured_planets:
            logger.info("")
            logger.info("=== CAPTURED HOOK DATA SUMMARY ===")
            for idx, captured in sorted(self._captured_planets.items()):
                moon_tag = " [MOON]" if captured.get('is_moon') else ""
                name = captured.get('planet_name') or f"Planet_{idx+1}"
                logger.info(f"  Planet {idx}: {name}{moon_tag}")
                logger.info(f"    Biome: {captured.get('biome')}, SubType: {captured.get('biome_subtype')}, Size: {captured.get('planet_size')}")
                logger.info(f"    Weather: {captured.get('weather')}, Flora: {captured.get('flora')}, Fauna: {captured.get('fauna')}, Sentinels: {captured.get('sentinel')}")
        else:
            logger.info("  No captured data yet - warp to a system first")

        logger.info("")
        logger.info("=== DETAILED PLANET STATUS ===")

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

                # v8.1.9: Use CAPTURED data from hook (reliable) instead of direct memory (garbage)
                biome = "NOT_READ"
                biome_subtype = "NOT_READ"
                planet_size = "NOT_READ"
                is_moon = False
                weather = "NOT_READ"

                # First check if we have captured data from hook (RELIABLE source)
                if i in self._captured_planets:
                    cap = self._captured_planets[i]
                    biome = cap.get('biome', 'Unknown')
                    biome_subtype = cap.get('biome_subtype', 'Unknown')
                    planet_size = cap.get('planet_size', 'Unknown')
                    is_moon = cap.get('is_moon', False)
                    weather = cap.get('weather', 'Unknown')
                else:
                    # Fallback to direct memory read (often gives garbage for planets 1-5)
                    try:
                        sys_data_addr = get_addressof(sys_data)
                        if sys_data_addr != 0:
                            direct_data = self._read_planet_gen_input_direct(sys_data_addr, i)
                            biome = direct_data.get("biome", "Unknown")
                            planet_size = direct_data.get("planet_size", "Unknown")
                            is_moon = direct_data.get("is_moon", False)
                    except Exception as e:
                        logger.debug(f"Direct read failed for {i}: {e}")

                # v8.1.9: Use captured data from hook for name/flora/fauna/sentinel if available
                name = f"Planet_{i+1}"
                sentinels = "NOT_READ"
                flora = "NOT_READ"
                fauna = "NOT_READ"

                # Use captured data from hook (RELIABLE source)
                if i in self._captured_planets:
                    cap = self._captured_planets[i]
                    if cap.get('planet_name'):
                        name = cap.get('planet_name')
                    flora = cap.get('flora', 'Unknown')
                    fauna = cap.get('fauna', 'Unknown')
                    sentinels = cap.get('sentinel', 'Unknown')
                else:
                    # Fallback: Try mPlanetData (only works for visited/populated planets)
                    try:
                        if hasattr(planet, 'mPlanetData'):
                            pd = planet.mPlanetData
                            if hasattr(pd, 'Name'):
                                n = str(pd.Name)
                                if n and n != "None" and len(n) > 0:
                                    name = n
                            if hasattr(pd, 'PlanetInfo'):
                                info = pd.PlanetInfo
                                if weather == "NOT_READ" and hasattr(info, 'Weather'):
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

                # Log status - v8.1.9: Shows captured data from hook (reliable)
                has_captured = i in self._captured_planets
                status = "CAPTURED" if has_captured else "NO_HOOK_DATA"
                moon_str = " [MOON]" if is_moon else ""
                logger.info(f"  Planet {i} [{status}]: {name}{moon_str}")
                logger.info(f"    Biome: {biome}, SubType: {biome_subtype}")
                logger.info(f"    Size: {planet_size}, is_moon: {is_moon}")
                logger.info(f"    Weather: {weather}")
                logger.info(f"    Flora: {flora}, Fauna: {fauna}, Sentinels: {sentinels}")

        except Exception as e:
            logger.error(f"Check planet data failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info("=" * 60)

    @gui_button("Extract Now")
    def manual_extract(self):
        """
        Click this button to extract planet data.
        v8.1.4: Uses direct memory reads + captured hook data + weather.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> EXTRACTION TRIGGERED (v8.1.4) <<<")
        logger.info(f">>> Using direct memory read + {len(self._captured_planets)} captured planets <<<")
        logger.info("=" * 60)

        self._do_extraction(force=True, trigger=f"manual_extract_{len(self._captured_planets)}_captured")

    # =========================================================================
    # v8.2.0: BATCH MODE GUI BUTTONS
    # =========================================================================

    @gui_button("Export Batch")
    def export_batch(self):
        """
        Export ALL systems collected in batch to a single JSON file.
        v8.2.0: Allows multi-system collection before export.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> BATCH EXPORT TRIGGERED (v8.2.0) <<<")
        logger.info("=" * 60)

        # First, save the current system to batch if there's captured data
        if self._captured_planets:
            logger.info("[BATCH] Saving current system to batch before export...")
            self._save_current_system_to_batch()

        if not self._batch_systems:
            logger.warning("[BATCH] No systems in batch to export!")
            logger.info("[BATCH] Visit some systems first, then click 'Export Batch'")
            return

        # Build batch export data
        total_planets = sum(sys.get('planet_count', 0) for sys in self._batch_systems)

        batch_data = {
            "batch_mode": True,
            "extraction_time": datetime.now().isoformat(),
            "extractor_version": "8.3.0",
            "trigger": "batch_export",
            "total_systems": len(self._batch_systems),
            "total_planets": total_planets,
            "discoverer_name": "HavenExtractor",
            "discovery_timestamp": int(datetime.now().timestamp()),
            "systems": self._batch_systems,
        }

        self._write_batch_extraction(batch_data)

        logger.info("")
        logger.info("*" * 60)
        logger.info(f">>> BATCH EXPORT COMPLETE! <<<")
        logger.info(f"    Systems exported: {len(self._batch_systems)}")
        logger.info(f"    Total planets: {total_planets}")
        logger.info("*" * 60)
        logger.info("")

    @gui_button("Clear Batch")
    def clear_batch(self):
        """
        Clear all systems from batch storage.
        Use this to start a fresh batch collection.
        """
        count = len(self._batch_systems)
        self._batch_systems.clear()

        logger.info("")
        logger.info("=" * 60)
        logger.info(f">>> BATCH CLEARED! <<<")
        logger.info(f"    Removed {count} systems from batch")
        logger.info("    Ready to start fresh collection")
        logger.info("=" * 60)
        logger.info("")

    @gui_button("Batch Status")
    def batch_status(self):
        """
        Show current batch status - how many systems are stored.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> BATCH STATUS (v8.2.0) <<<")
        logger.info("=" * 60)

        logger.info(f"  Batch mode enabled: {self._batch_mode_enabled}")
        logger.info(f"  Systems in batch: {len(self._batch_systems)}")

        if self._batch_systems:
            total_planets = sum(sys.get('planet_count', 0) for sys in self._batch_systems)
            logger.info(f"  Total planets stored: {total_planets}")
            logger.info("")
            logger.info("  Systems in batch:")
            for i, sys in enumerate(self._batch_systems):
                glyph = sys.get('glyph_code', 'Unknown')
                galaxy = sys.get('galaxy_name', 'Unknown')
                planets = sys.get('planet_count', 0)
                logger.info(f"    {i+1}. [{glyph}] in {galaxy} - {planets} planets")
        else:
            logger.info("")
            logger.info("  Batch is empty - warp to systems to collect data")

        # Also show current system info
        logger.info("")
        logger.info("  Current system:")
        logger.info(f"    Captured planets: {len(self._captured_planets)}")
        if self._captured_planets:
            logger.info("    (Will be added to batch on next warp or Export Batch)")

        logger.info("=" * 60)
        logger.info("")

    # =========================================================================
    # v10.0.0: REGION ENUMERATION - Remote System Scanning
    # =========================================================================

    def _build_universal_address(self, voxel_x: int, voxel_y: int, voxel_z: int,
                                   system_index: int, galaxy_index: int = 0) -> int:
        """
        Build a Universal Address (UA) from coordinates.

        UA format (64-bit):
        - Bits 0-11:   Voxel X (signed, offset by 2047)
        - Bits 12-23:  Voxel Z (signed, offset by 2047)
        - Bits 24-31:  Voxel Y (signed, offset by 127)
        - Bits 32-40:  System Index (0-511)
        - Bits 41-47:  Galaxy Index
        - Bits 48-63:  Reserved/Planet (usually 0 for system-level queries)
        """
        # Convert signed voxel coords to unsigned with offset
        ux = (voxel_x + 2047) & 0xFFF  # 12 bits
        uz = (voxel_z + 2047) & 0xFFF  # 12 bits
        uy = (voxel_y + 127) & 0xFF    # 8 bits
        us = system_index & 0x1FF       # 9 bits
        ug = galaxy_index & 0x7F        # 7 bits

        # Pack into 64-bit UA
        ua = (ux) | (uz << 12) | (uy << 24) | (us << 32) | (ug << 41)
        return ua

    def _parse_universal_address(self, ua: int) -> dict:
        """
        Parse a Universal Address (UA) to extract coordinates.

        UA format (64-bit):
        - Bits 0-11:   Voxel X (signed, offset by 2047)
        - Bits 12-23:  Voxel Z (signed, offset by 2047)
        - Bits 24-31:  Voxel Y (signed, offset by 127)
        - Bits 32-40:  System Index (0-511)
        - Bits 41-47:  Galaxy Index
        - Bits 48-63:  Reserved/Planet
        """
        # Extract each field
        ux = ua & 0xFFF           # 12 bits
        uz = (ua >> 12) & 0xFFF   # 12 bits
        uy = (ua >> 24) & 0xFF    # 8 bits
        us = (ua >> 32) & 0x1FF   # 9 bits
        ug = (ua >> 41) & 0x7F    # 7 bits
        planet_idx = (ua >> 48) & 0xFF  # 8 bits

        # Convert back to signed values
        voxel_x = ux - 2047
        voxel_z = uz - 2047
        voxel_y = uy - 127

        return {
            "voxel_x": voxel_x,
            "voxel_y": voxel_y,
            "voxel_z": voxel_z,
            "system_index": us,
            "galaxy_index": ug,
            "planet_index": planet_idx,
        }

    def _classify_remote_system(self, ua: int) -> Optional[dict]:
        """
        Call ClassifyStarSystem to get star system attributes for a remote system.

        Returns dict with:
        - star_type, economy_type, economy_strength, conflict_level
        - dominant_lifeform, planet_count, planet_sizes
        - is_abandoned, is_pirate, is_gas_giant, etc.

        Returns None if the call fails.
        """
        try:
            # Create output structure for star attributes
            star_attrs = nmse.cGcGalaxyStarAttributesData()

            # Call the static ClassifyStarSystem function
            ua_val = c_uint64(ua)
            nms.cGcGalaxyAttributeGenerator.ClassifyStarSystem(ua_val, pointer(star_attrs))

            # Extract data from the output structure
            result = {
                "data_source": "remote",
                "universal_address": hex(ua),

                # Star type
                "star_type": STAR_TYPES.get(
                    star_attrs.Type.value if hasattr(star_attrs.Type, 'value') else int(star_attrs.Type),
                    "Unknown"
                ),

                # Economy from TradingData
                "economy_type": TRADING_CLASSES.get(
                    star_attrs.TradingData.TradingClass.value if hasattr(star_attrs.TradingData.TradingClass, 'value') else 0,
                    "Unknown"
                ),
                "economy_strength": WEALTH_CLASSES.get(
                    star_attrs.TradingData.WealthClass.value if hasattr(star_attrs.TradingData.WealthClass, 'value') else 0,
                    "Unknown"
                ),

                # Conflict level
                "conflict_level": CONFLICT_LEVELS.get(
                    star_attrs.ConflictData.value if hasattr(star_attrs.ConflictData, 'value') else int(star_attrs.ConflictData),
                    "Unknown"
                ),

                # Dominant race
                "dominant_lifeform": ALIEN_RACES.get(
                    star_attrs.Race.value if hasattr(star_attrs.Race, 'value') else int(star_attrs.Race),
                    "Unknown"
                ),

                # Planet counts
                "planet_count": int(star_attrs.NumberOfPlanets),
                "prime_planets": int(star_attrs.NumberOfPrimePlanets),

                # System flags
                "is_abandoned": bool(star_attrs.AbandonedSystem),
                "is_pirate": bool(star_attrs.IsPirateSystem),
                "is_gas_giant_system": bool(star_attrs.IsGasGiantSystem),
                "is_giant_system": bool(star_attrs.IsGiantSystem),
                "is_safe": bool(star_attrs.IsSystemSafe),

                # Planet data (sizes available remotely)
                "planets": []
            }

            # Extract planet sizes for each planet
            for i in range(min(result["planet_count"], 16)):
                planet_size_val = star_attrs.PlanetSizes[i]
                size_int = planet_size_val.value if hasattr(planet_size_val, 'value') else int(planet_size_val)
                is_moon = (size_int == 3)

                planet_info = {
                    "planet_index": i,
                    "planet_size": PLANET_SIZES.get(size_int, f"Unknown({size_int})"),
                    "is_moon": is_moon,
                    "data_source": "remote",
                    # These require visiting to get full data
                    "biome": "Unknown (visit required)",
                    "weather": "Unknown (visit required)",
                    "flora_level": "Unknown (visit required)",
                    "fauna_level": "Unknown (visit required)",
                    "sentinel_level": "Unknown (visit required)",
                }
                result["planets"].append(planet_info)

            return result

        except Exception as e:
            logger.debug(f"ClassifyStarSystem failed for UA {hex(ua)}: {e}")
            return None

    @gui_button("Scan Region")
    def scan_region(self):
        """
        Scan ALL 512 systems in the current region remotely.
        No warping required - uses ClassifyStarSystem to query each system.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> REGION SCAN STARTED (v10.0.2) <<<")
        logger.info("=" * 60)

        # Get current coordinates to determine region
        coords = self._get_current_coordinates()

        # Fallback: use cached coordinates from last system warp
        if not coords and self._current_system_coords:
            logger.info("Using cached coordinates from last system warp...")
            coords = self._current_system_coords

        if not coords:
            logger.error("Cannot scan region - no coordinates available")
            logger.error("")
            logger.error("To fix this:")
            logger.error("  1. Make sure you're fully loaded into a star system")
            logger.error("  2. Try warping to a new system first (this captures coords)")
            logger.error("  3. Then click 'Scan Region' again")
            logger.error("")
            logger.error("If you just started, warp to any system to initialize.")
            return

        voxel_x = coords.get('voxel_x', 0)
        voxel_y = coords.get('voxel_y', 0)
        voxel_z = coords.get('voxel_z', 0)
        galaxy_idx = coords.get('galaxy_index', 0)

        logger.info(f"  Current region: X={voxel_x}, Y={voxel_y}, Z={voxel_z}")
        logger.info(f"  Galaxy: {coords.get('galaxy_name', 'Unknown')}")
        logger.info(f"  Scanning all 512 systems in this region...")
        logger.info("")

        # Store region coords
        self._current_region_coords = {
            'voxel_x': voxel_x,
            'voxel_y': voxel_y,
            'voxel_z': voxel_z,
            'galaxy_index': galaxy_idx,
            'galaxy_name': coords.get('galaxy_name', 'Unknown'),
        }

        # Clear previous region data
        self._region_systems = []
        self._region_scan_in_progress = True
        self._region_scan_progress = 0

        # Scan all 512 systems (indices 0-511)
        success_count = 0
        fail_count = 0

        for sys_idx in range(512):
            self._region_scan_progress = sys_idx

            # Build UA for this system
            ua = self._build_universal_address(voxel_x, voxel_y, voxel_z, sys_idx, galaxy_idx)

            # Get system data
            system_data = self._classify_remote_system(ua)

            if system_data:
                # Add coordinate info
                system_data['system_index'] = sys_idx
                system_data['voxel_x'] = voxel_x
                system_data['voxel_y'] = voxel_y
                system_data['voxel_z'] = voxel_z
                system_data['galaxy_index'] = galaxy_idx
                system_data['galaxy_name'] = coords.get('galaxy_name', 'Unknown')

                # Generate glyph code
                glyph_code = self._coords_to_glyphs(0, sys_idx, voxel_x, voxel_y, voxel_z)
                system_data['glyph_code'] = glyph_code
                system_data['system_name'] = f"System_{glyph_code}"

                self._region_systems.append(system_data)
                success_count += 1
            else:
                fail_count += 1

            # Progress logging every 50 systems
            if (sys_idx + 1) % 50 == 0:
                logger.info(f"  Progress: {sys_idx + 1}/512 systems scanned...")

        self._region_scan_in_progress = False
        self._region_scan_progress = 512

        # Summary
        total_planets = sum(s.get('planet_count', 0) for s in self._region_systems)

        logger.info("")
        logger.info("*" * 60)
        logger.info(f">>> REGION SCAN COMPLETE! <<<")
        logger.info(f"    Systems enumerated: {success_count}")
        logger.info(f"    Failed queries: {fail_count}")
        logger.info(f"    Total planets found: {total_planets}")
        logger.info(f"")
        logger.info(f"    Click 'Export Region' to save this data")
        logger.info(f"    Click 'Region Status' to see details")
        logger.info("*" * 60)
        logger.info("")

    @gui_button("Region Status")
    def region_status(self):
        """Show status of current region scan."""
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> REGION STATUS (v10.0.0) <<<")
        logger.info("=" * 60)

        if self._region_scan_in_progress:
            logger.info(f"  Scan in progress: {self._region_scan_progress}/512")
            return

        if not self._region_systems:
            logger.info("  No region data - click 'Scan Region' first")
            logger.info("=" * 60)
            return

        if self._current_region_coords:
            logger.info(f"  Region: X={self._current_region_coords['voxel_x']}, "
                       f"Y={self._current_region_coords['voxel_y']}, "
                       f"Z={self._current_region_coords['voxel_z']}")
            logger.info(f"  Galaxy: {self._current_region_coords.get('galaxy_name', 'Unknown')}")

        logger.info(f"  Systems scanned: {len(self._region_systems)}")

        total_planets = sum(s.get('planet_count', 0) for s in self._region_systems)
        total_moons = sum(
            sum(1 for p in s.get('planets', []) if p.get('is_moon'))
            for s in self._region_systems
        )
        logger.info(f"  Total planets: {total_planets}")
        logger.info(f"  Total moons: {total_moons}")

        # Count by star type
        star_counts = {}
        for sys in self._region_systems:
            st = sys.get('star_type', 'Unknown')
            star_counts[st] = star_counts.get(st, 0) + 1

        logger.info("")
        logger.info("  Star types:")
        for st, count in sorted(star_counts.items()):
            logger.info(f"    {st}: {count}")

        # Show sample systems
        logger.info("")
        logger.info("  Sample systems (first 5):")
        for i, sys in enumerate(self._region_systems[:5]):
            glyph = sys.get('glyph_code', 'Unknown')
            planets = sys.get('planet_count', 0)
            star = sys.get('star_type', '?')
            economy = sys.get('economy_type', '?')
            logger.info(f"    {i+1}. [{glyph}] - {star} star, {economy}, {planets} planets")

        logger.info("")
        logger.info("  Click 'Export Region' to save all data to JSON")
        logger.info("=" * 60)
        logger.info("")

    @gui_button("Export Region")
    def export_region(self):
        """Export all enumerated region data to a JSON file."""
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> EXPORT REGION DATA <<<")
        logger.info("=" * 60)

        if not self._region_systems:
            logger.warning("No region data to export - run 'Scan Region' first")
            return

        total_planets = sum(s.get('planet_count', 0) for s in self._region_systems)

        region_data = {
            "export_type": "region_enumeration",
            "extraction_time": datetime.now().isoformat(),
            "extractor_version": "10.0.0",
            "data_source": "remote_enumeration",

            # Region coordinates
            "region": self._current_region_coords or {},

            # Summary stats
            "total_systems": len(self._region_systems),
            "total_planets": total_planets,

            # All systems
            "systems": self._region_systems
        }

        # Save to file
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            region_file = self._output_dir / f"region_{len(self._region_systems)}sys_{ts}.json"

            with open(region_file, 'w', encoding='utf-8') as f:
                json.dump(region_data, f, indent=2, default=str)

            # Also update latest.json
            latest = self._output_dir / "latest_region.json"
            with open(latest, 'w', encoding='utf-8') as f:
                json.dump(region_data, f, indent=2, default=str)

            logger.info(f"")
            logger.info(f"*" * 60)
            logger.info(f">>> REGION EXPORT COMPLETE! <<<")
            logger.info(f"    File: {region_file}")
            logger.info(f"    Systems: {len(self._region_systems)}")
            logger.info(f"    Planets: {total_planets}")
            logger.info(f"")
            logger.info(f"    Data marked as 'remote' - visit systems for full detail")
            logger.info(f"*" * 60)
            logger.info(f"")

        except Exception as e:
            logger.error(f"Region export failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

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

        # CRITICAL: Cache coordinates for region scan feature (v10.0.0)
        self._current_system_coords = coords
        logger.info(f"Coordinates cached for region scan: {coords.get('glyph_code')} in {coords.get('galaxy_name')}")

        # Determine data source based on whether we have captured data
        data_source = "captured_hook" if len(self._captured_planets) > 0 else "memory_read"

        extraction = {
            "extraction_time": datetime.now().isoformat(),
            "extractor_version": "10.0.0",
            "trigger": trigger,
            "source": "live_extraction",
            "data_source": data_source,
            "captured_planet_count": len(self._captured_planets),
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
        """Get current galactic coordinates from player state or solar system."""
        galaxy_names = {
            0: "Euclid", 1: "Hilbert Dimension", 2: "Calypso",
            3: "Hesperius Dimension", 4: "Hyades", 5: "Ickjamatew",
            6: "Budullangr", 7: "Kikolgallr", 8: "Eltiensleen",
            9: "Eissentam", 10: "Elkupalos"
        }

        # Method 1: Try player_state (most reliable when available)
        logger.info("  Checking coordinate sources...")
        try:
            player_state = gameData.player_state
            logger.info(f"  Method 1 - player_state: {player_state}")
            if player_state:
                location = player_state.mLocation
                galactic_addr = location.GalacticAddress

                voxel_x = self._safe_int(galactic_addr.VoxelX)
                voxel_y = self._safe_int(galactic_addr.VoxelY)
                voxel_z = self._safe_int(galactic_addr.VoxelZ)
                system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
                planet_idx = self._safe_int(galactic_addr.PlanetIndex)
                galaxy_idx = self._safe_int(location.RealityIndex)

                logger.info(f"  [player_state] SUCCESS: X={voxel_x}, Y={voxel_y}, Z={voxel_z}, Sys={system_idx}")

                glyph_code = self._coords_to_glyphs(
                    planet_idx, system_idx, voxel_x, voxel_y, voxel_z
                )

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
            logger.info(f"  [player_state] EXCEPTION: {e}")

        # Method 2: Try getting from simulation/solar system discovery data
        try:
            simulation = gameData.simulation
            logger.info(f"  Method 2 - simulation: {simulation}")
            if simulation:
                solar_system_ptr = simulation.mpSolarSystem
                logger.info(f"  Method 2 - mpSolarSystem: {solar_system_ptr}")
                if solar_system_ptr:
                    addr = get_addressof(solar_system_ptr)
                    logger.info(f"  Method 2 - solar system addr: 0x{addr:X}")
                    if addr != 0:
                        solar_system = map_struct(addr, nms.cGcSolarSystem)
                        discovery_data = solar_system.mSolarSystemDiscoveryData

                        # Get galactic address from discovery data
                        galactic_addr = discovery_data.UniverseLocation.GalacticAddress

                        voxel_x = self._safe_int(galactic_addr.VoxelX)
                        voxel_y = self._safe_int(galactic_addr.VoxelY)
                        voxel_z = self._safe_int(galactic_addr.VoxelZ)
                        system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
                        planet_idx = self._safe_int(galactic_addr.PlanetIndex)
                        galaxy_idx = self._safe_int(discovery_data.UniverseLocation.RealityIndex)

                        logger.info(f"  [simulation] SUCCESS: X={voxel_x}, Y={voxel_y}, Z={voxel_z}, Sys={system_idx}")

                        glyph_code = self._coords_to_glyphs(
                            planet_idx, system_idx, voxel_x, voxel_y, voxel_z
                        )

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
            logger.info(f"  [simulation] EXCEPTION: {e}")

        # Method 3: Check if we have cached coordinates from previous extraction
        logger.info(f"  Method 3 - cached coords: {self._current_system_coords}")
        if self._current_system_coords:
            logger.info("  [cached] SUCCESS: Using cached coordinates from previous extraction")
            return self._current_system_coords

        # Method 4: Try cGcApplication global (should always be available)
        logger.info("  Method 4 - trying cGcApplication global...")
        try:
            # Try to access the global GcApplication instance
            if hasattr(nms, 'cGcApplication') and hasattr(nms.cGcApplication, 'instance'):
                app = nms.cGcApplication.instance()
                logger.info(f"  Method 4 - cGcApplication.instance(): {app}")
                if app:
                    # Try to get simulation from app
                    if hasattr(app, 'mpSimulation'):
                        sim = app.mpSimulation
                        logger.info(f"  Method 4 - mpSimulation: {sim}")
                        if sim:
                            sim_addr = get_addressof(sim)
                            if sim_addr != 0:
                                simulation = map_struct(sim_addr, nms.cGcSimulation)
                                if hasattr(simulation, 'mpSolarSystem'):
                                    ss_ptr = simulation.mpSolarSystem
                                    ss_addr = get_addressof(ss_ptr)
                                    if ss_addr != 0:
                                        solar_system = map_struct(ss_addr, nms.cGcSolarSystem)
                                        discovery_data = solar_system.mSolarSystemDiscoveryData
                                        galactic_addr = discovery_data.UniverseLocation.GalacticAddress

                                        voxel_x = self._safe_int(galactic_addr.VoxelX)
                                        voxel_y = self._safe_int(galactic_addr.VoxelY)
                                        voxel_z = self._safe_int(galactic_addr.VoxelZ)
                                        system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
                                        planet_idx = self._safe_int(galactic_addr.PlanetIndex)
                                        galaxy_idx = self._safe_int(discovery_data.UniverseLocation.RealityIndex)

                                        logger.info(f"  [cGcApplication] SUCCESS: X={voxel_x}, Y={voxel_y}, Z={voxel_z}")

                                        glyph_code = self._coords_to_glyphs(
                                            planet_idx, system_idx, voxel_x, voxel_y, voxel_z
                                        )

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
            logger.info(f"  [cGcApplication] EXCEPTION: {e}")

        # Method 5: Try gameData.game (alternative singleton access)
        logger.info("  Method 5 - trying gameData.game...")
        try:
            if hasattr(gameData, 'game') and gameData.game:
                game = gameData.game
                logger.info(f"  Method 5 - gameData.game: {game}")
                if hasattr(game, 'simulation') and game.simulation:
                    sim = game.simulation
                    if hasattr(sim, 'mpSolarSystem') and sim.mpSolarSystem:
                        ss_addr = get_addressof(sim.mpSolarSystem)
                        if ss_addr != 0:
                            solar_system = map_struct(ss_addr, nms.cGcSolarSystem)
                            discovery_data = solar_system.mSolarSystemDiscoveryData
                            galactic_addr = discovery_data.UniverseLocation.GalacticAddress

                            voxel_x = self._safe_int(galactic_addr.VoxelX)
                            voxel_y = self._safe_int(galactic_addr.VoxelY)
                            voxel_z = self._safe_int(galactic_addr.VoxelZ)
                            system_idx = self._safe_int(galactic_addr.SolarSystemIndex)
                            planet_idx = self._safe_int(galactic_addr.PlanetIndex)
                            galaxy_idx = self._safe_int(discovery_data.UniverseLocation.RealityIndex)

                            logger.info(f"  [gameData.game] SUCCESS: X={voxel_x}, Y={voxel_y}, Z={voxel_z}")

                            glyph_code = self._coords_to_glyphs(
                                planet_idx, system_idx, voxel_x, voxel_y, voxel_z
                            )

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
            logger.info(f"  [gameData.game] EXCEPTION: {e}")

        logger.warning("  All coordinate methods failed - are you in a star system?")
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
        """Extract data from a single planet using DIRECT MEMORY READ as primary source."""
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
            # CRITICAL v8.1.1: DIRECT MEMORY READ as PRIMARY SOURCE
            # Read from SolarSystemData + 0x1EA0 (planet gen input array)
            # Each planet entry is 0x53 bytes (83 bytes)
            # This is the CORRECT memory location - struct mapping was unreliable!
            # =====================================================
            if self._cached_sys_data_addr:
                logger.info(f"    [DIRECT] Using direct memory read for planet {index}")
                direct_data = self._read_planet_gen_input_direct(self._cached_sys_data_addr, index)

                # Apply direct-read biome (CRITICAL - this is the fix!)
                raw_biome = direct_data.get("biome_raw", -1)
                if raw_biome >= 0:
                    result["biome"] = direct_data["biome"]
                    logger.info(f"    [DIRECT] Biome = {result['biome']} (raw: {raw_biome})")

                # Apply direct-read planet_size (CRITICAL - this is the fix!)
                raw_size = direct_data.get("planet_size_raw", -1)
                if raw_size >= 0:
                    result["planet_size"] = direct_data["planet_size"]
                    result["is_moon"] = direct_data.get("is_moon", False)
                    logger.info(f"    [DIRECT] PlanetSize = {result['planet_size']} (raw: {raw_size}, is_moon: {result['is_moon']})")

                # Apply direct-read biome_subtype
                raw_subtype = direct_data.get("biome_subtype_raw", -1)
                if raw_subtype >= 0:
                    result["biome_subtype"] = direct_data["biome_subtype"]
                    logger.info(f"    [DIRECT] BiomeSubType = {result['biome_subtype']} (raw: {raw_subtype})")

                # Apply direct-read resources
                if direct_data.get("common_resource"):
                    result["common_resource"] = direct_data["common_resource"]
                if direct_data.get("rare_resource"):
                    result["rare_resource"] = direct_data["rare_resource"]
            else:
                logger.warning(f"    [DIRECT] No cached sys_data_addr - cannot use direct memory read!")

            # =====================================================
            # CAPTURED DATA: Use GenerateCreatureRoles hook data
            # v8.1.6: NOW INCLUDES BIOME from GenerationData - this is RELIABLE!
            # =====================================================
            if index in self._captured_planets:
                captured = self._captured_planets[index]
                logger.info(f"    [CAPTURED] Using captured data for planet {index}")

                # CRITICAL v8.1.6: Apply captured BIOME data (from GenerationData - reliable!)
                # This overrides the unreliable direct memory read
                if captured.get('biome_raw', -1) >= 0:
                    result["biome"] = captured.get('biome', result["biome"])
                    logger.info(f"    [CAPTURED] Biome = {result['biome']} (raw: {captured.get('biome_raw')})")
                if captured.get('biome_subtype_raw', -1) >= 0:
                    result["biome_subtype"] = captured.get('biome_subtype', result["biome_subtype"])
                    logger.info(f"    [CAPTURED] BiomeSubType = {result['biome_subtype']} (raw: {captured.get('biome_subtype_raw')})")

                # CRITICAL v8.1.9: Apply captured PLANET SIZE data (from GenerationData.Size - reliable!)
                # This overrides the GARBAGE values from direct memory read (was showing 256, 33554432, etc)
                if captured.get('planet_size_raw', -1) >= 0:
                    result["planet_size"] = captured.get('planet_size', result["planet_size"])
                    result["is_moon"] = captured.get('is_moon', False)
                    logger.info(f"    [CAPTURED] PlanetSize = {result['planet_size']} (raw: {captured.get('planet_size_raw')}, is_moon: {result['is_moon']})")

                # Apply captured flora/fauna/sentinel data
                result["flora_level"] = captured.get('flora', 'Unknown')
                result["fauna_level"] = captured.get('fauna', 'Unknown')
                result["sentinel_level"] = captured.get('sentinel', 'Unknown')

                # Apply captured resources if not already set from direct read
                if result["common_resource"] == "Unknown" and captured.get('common_resource'):
                    result["common_resource"] = captured['common_resource']
                if captured.get('uncommon_resource'):
                    result["uncommon_resource"] = captured['uncommon_resource']
                if result["rare_resource"] == "Unknown" and captured.get('rare_resource'):
                    result["rare_resource"] = captured['rare_resource']

                # v8.1.8: Apply captured weather from cGcPlanetWeatherData.WeatherType
                # This is reliable for ALL planets (not just visited ones)
                if captured.get('weather') and captured.get('weather_raw', -1) >= 0:
                    result["weather"] = captured['weather']
                    storm_info = captured.get('storm_frequency', '')
                    if storm_info:
                        logger.info(f"    [CAPTURED] Weather = {result['weather']} (storms: {storm_info})")
                    else:
                        logger.info(f"    [CAPTURED] Weather = {result['weather']}")
                elif result["weather"] == "Unknown" and captured.get('weather'):
                    # Fallback to any weather value if raw not available
                    result["weather"] = captured['weather']
                    logger.info(f"    [CAPTURED] Weather = {result['weather']} (fallback)")

                # v8.1.7: Apply captured planet name from cGcPlanetData.Name
                # This is the RELIABLE source - captures all planet names when hook fires
                if captured.get('planet_name'):
                    result["planet_name"] = captured['planet_name']
                    logger.info(f"    [CAPTURED] PlanetName = {result['planet_name']}")

                logger.info(f"    [CAPTURED] Applied: flora={result['flora_level']}, fauna={result['fauna_level']}, sentinel={result['sentinel_level']}")
            else:
                # Planet not in captured data - hook didn't fire for this planet
                logger.warning(f"    [NOCAPTURE] Planet {index} not in captured data - hook may not have fired")

            # NOTE: miPlanetIndex often returns garbage values - we use array index instead
            try:
                if hasattr(planet, 'miPlanetIndex'):
                    mi_idx = self._safe_int(planet.miPlanetIndex)
                    logger.debug(f"    [DEBUG] miPlanetIndex = {mi_idx} (ignoring, using array index {index})")
            except Exception as e:
                logger.debug(f"    miPlanetIndex access failed: {e}")

            # FALLBACK: NMS.py struct mapping (only if direct read failed)
            # This is unreliable but kept as a last resort
            if result["biome"] == "Unknown" or result["planet_size"] == "Unknown":
                logger.info(f"    [FALLBACK] Direct read incomplete, trying NMS.py struct mapping...")
                try:
                    if hasattr(planet, 'mPlanetGenerationInputData'):
                        gen_input = planet.mPlanetGenerationInputData

                        # PlanetSize - only if not already set
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

                            if raw_size is not None and 0 <= raw_size <= 4:
                                size_name = self._safe_enum(size_val)
                                result["planet_size"] = size_name
                                result["is_moon"] = (raw_size == 3)
                                logger.info(f"    [FALLBACK] PlanetSize = {size_name} (raw: {raw_size})")

                        # Biome - only if not already set
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

                            if raw_biome is not None and 0 <= raw_biome <= 16:
                                result["biome"] = self._validate_biome(biome_val, raw_biome)
                                logger.info(f"    [FALLBACK] Biome = {result['biome']} (raw: {raw_biome})")

                        # BiomeSubType
                        if result["biome_subtype"] == "Unknown" and hasattr(gen_input, 'BiomeSubType'):
                            result["biome_subtype"] = self._safe_enum(gen_input.BiomeSubType)
                            logger.info(f"    [FALLBACK] BiomeSubType = {result['biome_subtype']}")

                except Exception as e:
                    logger.warning(f"    [FALLBACK] NMS.py struct mapping failed: {e}")

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

                        # NOTE: SentinelsPerDifficulty, Flora, Fauna are array types
                        # We already get these from the GenerateCreatureRoles hook
                        # Skip these fallbacks to avoid overwriting good data with object references
                except Exception:
                    pass

                # Resources from PlanetData if not already set - clean garbage
                try:
                    if result["common_resource"] == "Unknown" and hasattr(planet_data, 'CommonSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.CommonSubstanceID))
                        if val:
                            result["common_resource"] = val

                    if hasattr(planet_data, 'UncommonSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.UncommonSubstanceID))
                        if val:
                            result["uncommon_resource"] = val

                    if result["rare_resource"] == "Unknown" and hasattr(planet_data, 'RareSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.RareSubstanceID))
                        if val:
                            result["rare_resource"] = val
                except Exception:
                    pass

            return result

        except Exception as e:
            logger.debug(f"Planet {index} data extraction failed: {e}")
            return None

    def _safe_enum(self, val, default: str = "Unknown") -> str:
        """Safely convert enum to string, with normalization."""
        try:
            if val is None:
                return default
            if hasattr(val, 'name'):
                name = val.name
            elif hasattr(val, 'value'):
                name = str(val.value)
            else:
                name = str(val)
            # Normalize: strip trailing underscores (None_ -> None)
            return name.rstrip('_') if name else default
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

    def _clean_resource_string(self, val: str) -> str:
        """Clean a resource string, removing garbage characters."""
        if not val or val == "None":
            return ""
        # Only keep printable ASCII characters
        cleaned = ''.join(c for c in str(val) if c.isprintable() and ord(c) < 128)
        # Validate it looks like a valid resource ID
        if cleaned and len(cleaned) >= 2 and cleaned[0].isalpha():
            return cleaned
        return ""

    def _validate_biome(self, biome_val, raw_val) -> str:
        """Validate biome value, returning 'Unknown' for garbage."""
        biome_name = self._safe_enum(biome_val)
        # Check if raw value is a valid biome index (0-16)
        if raw_val is not None:
            if isinstance(raw_val, int) and 0 <= raw_val <= 16:
                return biome_name
            # Invalid raw value
            return "Unknown"
        return biome_name

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
        """Save extraction data locally (NO auto-send to API - use Save Watcher for manual upload)."""
        # Save local backup
        try:
            latest = self._output_dir / "latest.json"
            with open(latest, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            timestamped = self._output_dir / f"extraction_{ts}.json"
            with open(timestamped, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(f"=" * 50)
            logger.info(f">>> EXTRACTION SAVED <<<")
            logger.info(f"  Latest: {latest}")
            logger.info(f"  Backup: {timestamped}")
            logger.info(f"")
            logger.info(f">>> Use Save Watcher to manually upload to Haven UI <<<")
            logger.info(f"=" * 50)

        except Exception as e:
            logger.error(f"Local save failed: {e}")

    def _write_batch_extraction(self, data: dict):
        """
        Save BATCH extraction data locally.
        v8.2.0: Saves all systems in batch to separate files.
        """
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            system_count = data.get('total_systems', 0)

            # Save batch file with all systems
            batch_file = self._output_dir / f"batch_{system_count}systems_{ts}.json"
            with open(batch_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            # Also update latest.json with the batch data
            latest = self._output_dir / "latest.json"
            with open(latest, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)

            logger.info(f"=" * 50)
            logger.info(f">>> BATCH EXTRACTION SAVED <<<")
            logger.info(f"  Batch file: {batch_file}")
            logger.info(f"  Latest: {latest}")
            logger.info(f"  Systems: {system_count}")
            logger.info(f"  Planets: {data.get('total_planets', 0)}")
            logger.info(f"")
            logger.info(f">>> Use Save Watcher to manually upload to Haven UI <<<")
            logger.info(f"=" * 50)

        except Exception as e:
            logger.error(f"Batch save failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

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
