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

# ---- source lines 534-826 ----
RESOURCE_NAMES = {
    # Stellar metals (found in deposits) - map "2" variants to base resource for display
    "YELLOW": "Copper",
    "YELLOW2": "Copper",        # Game shows Copper, not Chromatic Metal
    "RED": "Cadmium",
    "RED2": "Cadmium",          # Game shows Cadmium, not Chromatic Metal
    "GREEN": "Emeril",
    "GREEN2": "Emeril",         # Game shows Emeril, not Chromatic Metal
    "BLUE": "Indium",
    "BLUE2": "Indium",          # Game shows Indium, not Chromatic Metal
    "PURPLE": "Quartzite",
    "PURPLE2": "Quartzite",
    # Activated stellar metals (extreme weather planets)
    "EX_YELLOW": "Activated Copper",
    "EX_RED": "Activated Cadmium",
    "EX_GREEN": "Activated Emeril",
    "EX_BLUE": "Activated Indium",
    "EX_PURPLE": "Activated Quartzite",
    # Biome-specific resources
    "COLD1": "Dioxite",
    "SNOW1": "Dioxite",
    "HOT1": "Phosphorus",
    "LUSH1": "Paraffinium",
    "DUSTY1": "Pyrite",
    "TOXIC1": "Ammonia",
    "RADIO1": "Uranium",
    "SWAMP1": "Faecium",
    "PLANT_POOP": "Faecium",       # Alternate internal ID for Swamp biome resource
    "PLANT_SWAMP": "Faecium",      # Another possible swamp plant ID
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
    "GAS1": "Sulphurine",
    "GAS2": "Radon",
    "GAS3": "Nitrogen",
    # Buried/excavation resources
    "FOSSIL1": "Ancient Bones",
    "FOSSIL2": "Ancient Bones",
    "CREATURE1": "Ancient Bones",
    "BONES": "Ancient Bones",
    "ANCIENT": "Ancient Bones",
    "SALVAGE": "Salvageable Scrap",
    "SALVAGE1": "Salvageable Scrap",
    "TECHFRAG": "Salvageable Scrap",
    "BURIED": "Buried Technology",
    "BURIED1": "Buried Technology",
    # Infestation indicators (shown as resources)
    "INFESTATION": "Vile Brood Detected",
    "VILEBROOD": "Vile Brood Detected",
    "LARVA": "Whispering Eggs",
    "LARVAL": "Whispering Eggs",
    # Gas Giant resources
    "GASGIANT1": "Activated Indium",
    "GASGIANT": "Hydrogen",
    # Storm crystals
    "STORM1": "Storm Crystals",
    "STORM_CRYSTAL": "Storm Crystals",
    # v1.4.6: ExtraResourceHints UI hint IDs (actual game hint text IDs)
    "UI_BONES_HINT": "Ancient Bones",
    "UI_SCRAP_HINT": "Salvageable Scrap",
    "UI_BUGS_HINT": "Vile Brood Detected",
    "UI_STORM_HINT": "Storm Crystals",
    "UI_GRAV_HINT": "Gravitino Balls",
}

# v1.4.5: Biome -> plant resource mapping (what the game discovery screen shows)
# Dead, Airless, Exotic, and Weird biomes have no plant resource
BIOME_PLANT_RESOURCE = {
    "Frozen": "Frost Crystal",
    "Barren": "Cactus Flesh",
    "Scorched": "Solanium",
    "Toxic": "Fungal Mould",
    "Radioactive": "Gamma Root",
    "Lush": "Star Bulb",
    "Swamp": "Faecium",
    "Lava": "Solanium",
    "Waterworld": "Kelp Sac",
}

# v1.6.8: Biome subtypes that override the main biome's plant resource
# e.g., a Lush planet with Swamp subtype should get Faecium, not Star Bulb
BIOME_SUBTYPE_PLANT_OVERRIDE = {
    "Swamp": "Faecium",
    "Lava": "Solanium",
}

# v1.4.5: Internal substance IDs that don't appear on the discovery screen
# Dead/Airless moons have SPACEGUNK internally but show Rusted Metal to the player
HIDDEN_SUBSTANCE_IDS = {
    "SPACEGUNK1", "SPACEGUNK2", "SPACEGUNK3", "SPACEGUNK4", "SPACEGUNK5",
}
HIDDEN_SUBSTANCE_NAMES = {
    "Residual Goop", "Runaway Mould", "Living Slime", "Viscous Fluids", "Tainted Metal",
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
    """Clean raw weather strings like 'weather_glitch 6' to readable names.

    Maps raw game weather values to EXACT adjectives from Haven UI's
    weatherAdjectives list (adjectives.js) for consistent display.

    Valid weatherAdjectives include: Pleasant, Temperate, Hot, Extreme Heat,
    Humid, Frozen, Freezing, Radioactive, Anomalous, Arid, Airless, Clear, etc.
    """
    if not weather_str or weather_str == "Unknown" or weather_str == "":
        return weather_str

    # Values that are already valid weatherAdjectives (from adjectives.js)
    # Only include values that ACTUALLY exist in the weatherAdjectives list
    valid_adjectives = [
        "Clear", "Humid", "Radioactive", "Pleasant", "Temperate", "Mild",
        "Beautiful", "Blissful", "Balmy", "Frozen", "Freezing", "Cold", "Icy",
        "Arid", "Parched", "Hot", "Heated", "Extreme Heat", "Anomalous",
        "Airless", "No Atmosphere", "Inferno", "Toxic Rain", "Extreme Toxicity"
    ]
    if weather_str in valid_adjectives:
        return weather_str

    # Normalize: lowercase and replace spaces with underscores for matching
    normalized = weather_str.lower().replace(' ', '_')

    # Map raw weather values to EXACT weatherAdjectives from adjectives.js
    # These must match entries in Haven-UI/src/data/adjectives.js weatherAdjectives
    exact_mappings = {
        # Lush planet weather
        "weather_lush": "Pleasant",
        "weather lush": "Pleasant",
        "lush": "Pleasant",
        # Toxic planet weather
        "weather_toxic": "Toxic Rain",
        "toxic": "Toxic Rain",
        # Scorched/Hot planet weather
        "weather_scorched": "Extreme Heat",
        "weather_hot": "Extreme Heat",
        "weather_fire": "Inferno",
        "scorched": "Extreme Heat",
        # Radioactive planet weather
        "weather_radioactive": "Radioactive",
        "radioactive": "Radioactive",
        # Frozen/Cold planet weather
        "weather_frozen": "Frozen",
        "weather_cold": "Freezing",
        "weather_snow": "Frozen",
        "weather_blizzard": "Freezing",
        "frozen": "Frozen",
        "cold": "Freezing",
        # Barren/Dust planet weather
        "weather_barren": "Arid",
        "weather_dust": "Arid",
        "barren": "Arid",
        "dust": "Arid",
        # Dead planet weather
        "weather_dead": "Airless",
        "dead": "Airless",
        # Weird/Exotic planet weather
        "weather_weird": "Anomalous",
        "weather_glitch": "Anomalous",
        "weather_bubble": "Anomalous",
        "weird": "Anomalous",
        "glitch": "Anomalous",
        # Swamp planet weather
        "weather_swamp": "Humid",
        "swamp": "Humid",
        # Lava planet weather
        "weather_lava": "Inferno",
        "lava": "Inferno",
        # Humid weather
        "weather_humid": "Humid",
        "humid": "Humid",
        # Clear/Normal weather
        "weather_clear": "Clear",
        "weather_normal": "Temperate",
        "clear": "Clear",
        "normal": "Temperate",
        # Extreme weather
        "weather_extreme": "Extreme Heat",
        # Color-based exotic weather
        "redweather": "Anomalous",
        "greenweather": "Anomalous",
        "blueweather": "Anomalous",
    }

    if normalized in exact_mappings:
        return exact_mappings[normalized]

    # Try prefix matching for partial matches
    weather_prefix_mappings = {
        "weather_glitch": "Anomalous",
        "weather_lava": "Inferno",
        "weather_frozen": "Frozen",
        "weather_cold": "Freezing",
        "weather_hot": "Extreme Heat",
        "weather_toxic": "Toxic Rain",
        "weather_radioactive": "Radioactive",
        "weather_dust": "Arid",
        "weather_humid": "Humid",
        "weather_scorched": "Extreme Heat",
        "weather_swamp": "Humid",
        "weather_bubble": "Anomalous",
        "weather_weird": "Anomalous",
        "weather_fire": "Inferno",
        "weather_clear": "Clear",
        "weather_normal": "Temperate",
        "weather_snow": "Frozen",
        "weather_blizzard": "Freezing",
        "weather_extreme": "Extreme Heat",
        "weather_lush": "Pleasant",
    }

    for prefix, readable in weather_prefix_mappings.items():
        if normalized.startswith(prefix):
            return readable

    # Biome-based weather fallbacks using valid weatherAdjectives
    biome_weather_defaults = {
        "lush": "Pleasant",
        "toxic": "Toxic Rain",
        "scorched": "Extreme Heat",
        "radioactive": "Radioactive",
        "frozen": "Frozen",
        "barren": "Arid",
        "dead": "Airless",
        "weird": "Anomalous",
        "swamp": "Humid",
        "lava": "Inferno",
    }

    for biome, weather in biome_weather_defaults.items():
        if biome in normalized:
            return weather

    # Try to extract meaningful part (remove numbers and underscores)
    import re
    cleaned = re.sub(r'[_\d]+$', '', weather_str)  # Remove trailing numbers and underscores
    cleaned = cleaned.replace('_', ' ').strip()

    # Title case and return if we got something different
    if cleaned and cleaned.lower() != weather_str.lower():
        return cleaned.title()

    return weather_str
