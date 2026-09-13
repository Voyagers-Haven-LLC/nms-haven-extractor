"""Display-label tables for the capture layer. NO MEMORY OFFSETS LIVE HERE.

2.1.0: the four hand-maintained offset classes (SolarSystemDataOffsets,
TradingDataOffsets, ConflictDataOffsets, PlanetGenInputOffsets) are gone. Every
struct read now goes through NMSpy's generated classes
(nmspy.data.exported_types / nmspy.data.types), which are regenerated per game
build. Cosmos (2026-09-09) moved cGcSolarSystemData by +0x2E0 and the hand
offsets kept returning plausible garbage for four days without a single error
— that is the failure mode this change removes. The pinned framework version
lives in mod2/nmspy_pin.py.

What stays: the integer -> display label tables below. They translate the raw
enum values into the vocabulary haven-ui's option catalog stores, and the
upload sanity gate (payload/sanity.py) uses them as the set of plausible
values. Raw enums themselves come from nmspy.data.enums.
"""

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
