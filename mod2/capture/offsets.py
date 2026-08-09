"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.
"""

# ---- source lines 375-529 (+880-900 game-mode maps) ----
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
    NAME = 0x2274                 # cTkFixedString0x80 - system name (128 bytes)
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
    STRUCT_SIZE = 0x58            # Size of each planet gen input entry (88 bytes; array spans 0x1EA0..0x2160 = 0x2C0 / 8 = 0x58 per nmspy cGcSolarSystemData). Prior 0x53 mis-strided slots 1-5.
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
# Mapped to user-friendly display names
BIOME_SUBTYPES = {
    0: "Standard",      # None_ -> Standard for display
    1: "Standard",      # Standard
    2: "High Quality",  # HighQuality
    3: "Exotic",        # Structure (exotic planet type)
    4: "Exotic",        # Beam (exotic planet type)
    5: "Exotic",        # Hexagon (exotic planet type)
    6: "Exotic",        # FractCube (exotic planet type)
    7: "Exotic",        # Bubble (exotic planet type)
    8: "Exotic",        # Shards (exotic planet type)
    9: "Exotic",        # Contour (exotic planet type)
    10: "Exotic",       # Shell (exotic planet type)
    11: "Exotic",       # BoneSpire (exotic planet type)
    12: "Exotic",       # WireCell (exotic planet type)
    13: "Exotic",       # HydroGarden (exotic planet type)
    14: "Mega Flora",   # HugePlant - large plants
    15: "Mega Flora",   # HugeLush - large lush vegetation
    16: "Mega Fauna",   # HugeRing - large ring formations
    17: "Mega Terrain", # HugeRock - large rock formations
    18: "Mega Terrain", # HugeScorch - large scorched terrain
    19: "Mega Toxic",   # HugeToxic - large toxic formations
    20: "Variant A",    # Variant_A
    21: "Variant B",    # Variant_B
    22: "Variant C",    # Variant_C
    23: "Variant D",    # Variant_D
    24: "Infested",     # Infested
    25: "Swamp",        # Swamp
    26: "Lava",         # Lava
    27: "Worlds",       # Worlds
    28: "Remix A",      # Remix_A
    29: "Remix B",      # Remix_B
    30: "Remix C",      # Remix_C
    31: "Remix D",      # Remix_D
}

PLANET_SIZES = {
    0: "Large", 1: "Medium", 2: "Small", 3: "Moon", 4: "Giant"
}

# Values must match haven-ui optionCatalog.json economy_types — the DB stores these verbatim.
# cGcTradingClass: HighTech->Technology, Fusion->Advanced Materials, PowerGeneration->Power Generation
TRADING_CLASSES = {
    0: "Mining", 1: "Technology", 2: "Trading", 3: "Manufacturing",
    4: "Advanced Materials", 5: "Scientific", 6: "Power Generation"
}

# Catalog economy_levels scale is T1-T4 (wealth enum: Poor/Average/Wealthy/Pirate)
WEALTH_CLASSES = {
    0: "T1", 1: "T2", 2: "T3", 3: "T4"
}

# cGcPlayerConflictData enum name for 1 is "Default" but the display/catalog term is Medium
CONFLICT_LEVELS = {
    0: "Low", 1: "Medium", 2: "High", 3: "Pirate"
}

ALIEN_RACES = {
    0: "Gek",
    1: "Vy'keen",
    2: "Korvax",
    3: "None",       # Robots/Sentinel systems
    4: "None",       # Atlas
    5: "None",       # Diplomats (unused)
    6: "None",       # Uninhabited
    7: "None",       # v1.6.12: post-Voyagers — observed raw=7 for abandoned/no-race systems
    8: "None",       # Reserved
}

# cGcGalaxyStarTypes enum (from nmspy cGcGalaxyStarTypes IntEnum)
STAR_TYPES = {
    0: "Yellow", 1: "Green", 2: "Blue", 3: "Red", 4: "Purple"
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

# v1.6.8: Game mode / difficulty preset enum (cGcDifficultyPresetType)
# Read from memory at runtime to track which mode produced the adjective data
GAME_MODE_PRESETS = {
    0: "Invalid",
    1: "Custom",
    2: "Normal",
    3: "Creative",
    4: "Relaxed",
    5: "Survival",
    6: "Permadeath",
}

# Map game mode preset to SentinelsPerDifficulty array index
GAME_MODE_TO_DIFFICULTY_INDEX = {
    "Creative": 0,    # Casual
    "Relaxed": 1,     # Relaxed
    "Normal": 2,      # Normal
    "Custom": 2,      # Custom defaults to Normal index
    "Survival": 3,    # Survival/Permadeath
    "Permadeath": 3,  # Survival/Permadeath
}
