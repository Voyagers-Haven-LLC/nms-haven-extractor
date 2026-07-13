# /// script
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_exe = true
# ///
"""
Haven Extractor - No Man's Sky Data Extraction Mod

Hooks into NMS via PyMHF to extract solar system and planet data in real-time,
then uploads to the Haven API for community cataloging and mapping.

Features:
- Automatic data capture on system warp (batch mode)
- Per-user API key registration and management
- Three-layer adjective resolution (runtime hooks, PAK/MBIN cache, legacy fallback)
- Community tag routing for multi-community support
- Pre-flight duplicate checking before export
- Special resource detection (Ancient Bones, Storm Crystals, etc.)

Workflow:
1. Launch the extractor - NMS starts with the mod loaded
2. Enter your Discord username and select a community tag
3. Warp to systems - planet data is captured automatically
4. Apply system names via the GUI before warping to the next system
5. Click "Export to Haven" to upload all collected systems

For more information, see README.txt or visit https://havenmap.online
"""


import json
import logging
import time
import ctypes
import re
import urllib.request
import urllib.error
import ssl
import threading
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Optional, Set, List, Dict

from pymhf import Mod
from pymhf.core.memutils import map_struct, get_addressof
from pymhf.core.hooking import get_caller
from pymhf.gui.decorators import gui_button, gui_variable
import pymhf.core._internal as _internal
import nmspy.data.types as nms
import nmspy.data.exported_types as nmse
from nmspy.decorators import on_state_change
from nmspy.common import gameData
from ctypes import c_uint64, c_int64, c_void_p, pointer, sizeof
import nmspy.data.basic_types as basic

logger = logging.getLogger(__name__)

# NMS procedural name generation (vendored from https://github.com/stuart/nms_namegen, MIT license).
# Requires numpy. Auto-install if missing so users who updated via the in-app auto-updater
# (which only swaps the mod/ folder, not the embedded Python's site-packages) don't silently
# lose procgen names.
def _import_nms_namegen():
    try:
        from nms_namegen.system import systemName as _sys
        from nms_namegen.region import regionName as _reg
        from nms_namegen.planet import planetName as _pln
        return _sys, _reg, _pln
    except ImportError:
        return None

_ng = _import_nms_namegen()
if _ng is None:
    logger.info("[NAMEGEN] nms_namegen import failed (likely missing numpy) - attempting auto-install...")
    try:
        import subprocess
        _embedded_python = Path(__file__).resolve().parent.parent / "python" / "python.exe"
        if not _embedded_python.exists():
            raise FileNotFoundError(f"Embedded Python not found at {_embedded_python}")
        # subprocess.run + capture_output (drains pipes safely). Previous check_call+PIPE
        # could deadlock on numpy's verbose install output.
        _proc = subprocess.run(
            [str(_embedded_python), '-m', 'pip', 'install', 'numpy'],
            capture_output=True, text=True, timeout=180
        )
        if _proc.returncode != 0:
            logger.error(f"[NAMEGEN] pip exit code {_proc.returncode}")
            if _proc.stderr:
                # Show last 20 lines of stderr — keeps log readable, captures the actual error
                _err_tail = '\n'.join(_proc.stderr.strip().splitlines()[-20:])
                logger.error(f"[NAMEGEN] pip stderr:\n{_err_tail}")
            raise RuntimeError(f"pip install numpy failed (exit {_proc.returncode})")
        logger.info("[NAMEGEN] numpy auto-installed - retrying nms_namegen import")
        _ng = _import_nms_namegen()
        if _ng is None:
            logger.error("[NAMEGEN] numpy installed but nms_namegen import still fails - check mod/nms_namegen/ exists")
    except Exception as e:
        logger.error(f"[NAMEGEN] numpy auto-install failed: {e}")
        logger.error("[NAMEGEN] Procedural name generation unavailable - re-run FIRST_TIME_SETUP.bat or use UPDATE_HAVEN_EXTRACTOR.bat")

if _ng is not None:
    nms_system_name, nms_region_name, nms_planet_name = _ng
    NMS_NAMEGEN_AVAILABLE = True
else:
    NMS_NAMEGEN_AVAILABLE = False

# Pure data-transform layer (no pymhf/nmspy deps) - galaxy resolution + payload build.
# Kept in a separate import-safe module so the data handling can be smoke-tested
# outside the game. See extraction_core.py.
try:
    from .extraction_core import (
        decide_galaxy, build_planet_entry, build_planet_list,
        build_system_payload, galaxy_is_known,
    )
except ImportError:
    from extraction_core import (
        decide_galaxy, build_planet_entry, build_planet_list,
        build_system_payload, galaxy_is_known, select_captures,
    )

# =============================================================================
# API CONFIGURATION
# =============================================================================
# Per-user API keys: each extractor registers with the Haven backend and
# receives a personal API key tied to their Discord username.
# The old shared key is kept as a fallback for transition but tags submissions
# as "unregistered" on the server side.
# =============================================================================
DEFAULT_API_URL = "https://havenmap.online"
_OLD_SHARED_KEY = "vh_live_HvnXtr8k9Lm2NpQ4rStUvWxYz1A3bC5dE7fG"  # Legacy shared key — kept to detect users who haven't re-registered since v1.5.0 (Feb 2026). Safe to remove once all active users have personal keys (check api_keys table for key_type='shared').
HAVEN_EXTRACTOR_API_KEY = ""  # Per-user key loaded from config; empty = needs registration

# Default user config (populated by config GUI)
DEFAULT_USER_CONFIG = {
    "discord_username": "",
    "personal_id": "",
    "discord_tag": "personal",
    "reality": "Normal",
}


# =============================================================================
# GALAXY NAME LOOKUP - All 256 NMS galaxies (0-indexed, matching game memory)
# Fallback for unmapped indices uses 1-indexed numbering (community convention)
# Source: NMS-Save-Watcher/data/galaxies.json (authoritative)
# =============================================================================
GALAXY_NAMES = {
    0: "Euclid", 1: "Hilbert Dimension", 2: "Calypso", 3: "Hesperius Dimension",
    4: "Hyades", 5: "Ickjamatew", 6: "Budullangr", 7: "Kikolgallr",
    8: "Eltiensleen", 9: "Eissentam", 10: "Elkupalos", 11: "Aptarkaba",
    12: "Ontiniangp", 13: "Odiwagiri", 14: "Ogtialabi", 15: "Muhacksonto",
    16: "Hitonskyer", 17: "Rerasmutul", 18: "Isdoraijung", 19: "Doctinawyra",
    20: "Loychazinq", 21: "Zukasizawa", 22: "Ekwathore", 23: "Yeberhahne",
    24: "Twerbetek", 25: "Sivarates", 26: "Eajerandal", 27: "Aldukesci",
    28: "Wotyarogii", 29: "Sudzerbal", 30: "Maupenzhay", 31: "Sugueziume",
    32: "Brogoweldian", 33: "Ehbogdenbu", 34: "Ijsenufryos", 35: "Nipikulha",
    36: "Autsurabin", 37: "Lusontrygiamh", 38: "Rewmanawa", 39: "Ethiophodhe",
    40: "Urastrykle", 41: "Xobeurindj", 42: "Oniijialdu", 43: "Wucetosucc",
    44: "Ebyeloof", 45: "Odyavanta", 46: "Milekistri", 47: "Waferganh",
    48: "Agnusopwit", 49: "Teyaypilny", 50: "Zalienkosm", 51: "Ladgudiraf",
    52: "Mushonponte", 53: "Amsentisz", 54: "Fladiselm", 55: "Laanawemb",
    56: "Ilkerloor", 57: "Davanossi", 58: "Ploehrliou", 59: "Corpinyaya",
    60: "Leckandmeram", 61: "Quulngais", 62: "Nokokipsechl", 63: "Rinblodesa",
    64: "Loydporpen", 65: "Ibtrevskip", 66: "Elkowaldb", 67: "Heholhofsko",
    68: "Yebrilowisod", 69: "Husalvangewi", 70: "Ovna'uesed", 71: "Bahibusey",
    72: "Nuybeliaure", 73: "Doshawchuc", 74: "Ruckinarkh", 75: "Thorettac",
    76: "Nuponoparau", 77: "Moglaschil", 78: "Uiweupose", 79: "Nasmilete",
    80: "Ekdaluskin", 81: "Hakapanasy", 82: "Dimonimba", 83: "Cajaccari",
    84: "Olonerovo", 85: "Umlanswick", 86: "Henayliszm", 87: "Utzenmate",
    88: "Umirpaiya", 89: "Paholiang", 90: "Iaereznika", 91: "Yudukagath",
    92: "Boealalosnj", 93: "Yaevarcko", 94: "Coellosipp", 95: "Wayndohalou",
    96: "Smoduraykl", 97: "Apmaneessu", 98: "Hicanpaav", 99: "Akvasanta",
    100: "Tuychelisaor", 101: "Rivskimbe", 102: "Daksanquix", 103: "Kissonlin",
    104: "Aediabiel", 105: "Ulosaginyik", 106: "Roclaytonycar", 107: "Kichiaroa",
    108: "Irceauffey", 109: "Nudquathsenfe", 110: "Getaizakaal", 111: "Hansolmien",
    112: "Bloytisagra", 113: "Ladsenlay", 114: "Luyugoslasr", 115: "Ubredhatk",
    116: "Cidoniana", 117: "Jasinessa", 118: "Torweierf", 119: "Saffneckm",
    120: "Thnistner", 121: "Dotusingg", 122: "Luleukous", 123: "Jelmandan",
    124: "Otimanaso", 125: "Enjaxusanto", 126: "Sezviktorew", 127: "Zikehpm",
    128: "Bephembah", 129: "Broomerrai", 130: "Meximicka", 131: "Venessika",
    132: "Gaiteseling", 133: "Zosakasiro", 134: "Drajayanes", 135: "Ooibekuar",
    136: "Urckiansi", 137: "Dozivadido", 138: "Emiekereks", 139: "Meykinunukur",
    140: "Kimycuristh", 141: "Roansfien", 142: "Isgarmeso", 143: "Daitibeli",
    144: "Gucuttarik", 145: "Enlaythie", 146: "Drewweste", 147: "Akbulkabi",
    148: "Homskiw", 149: "Zavainlani", 150: "Jewijkmas", 151: "Itlhotagra",
    152: "Podalicess", 153: "Hiviusauer", 154: "Halsebenk", 155: "Puikitoac",
    156: "Gaybakuaria", 157: "Grbodubhe", 158: "Rycempler", 159: "Indjalala",
    160: "Fontenikk", 161: "Pasycihelwhee", 162: "Ikbaksmit", 163: "Telicianses",
    164: "Oyleyzhan", 165: "Uagerosat", 166: "Impoxectin", 167: "Twoodmand",
    168: "Hilfsesorbs", 169: "Ezdaranit", 170: "Wiensanshe", 171: "Ewheelonc",
    172: "Litzmantufa", 173: "Emarmatosi", 174: "Mufimbomacvi", 175: "Wongquarum",
    176: "Hapirajua", 177: "Igbinduina", 178: "Wepaitvas", 179: "Sthatigudi",
    180: "Yekathsebehn", 181: "Ebedeagurst", 182: "Nolisonia", 183: "Ulexovitab",
    184: "Iodhinxois", 185: "Irroswitzs", 186: "Bifredait", 187: "Beiraghedwe",
    188: "Yeonatlak", 189: "Cugnatachh", 190: "Nozoryenki", 191: "Ebralduri",
    192: "Evcickcandj", 193: "Ziybosswin", 194: "Heperclait", 195: "Sugiuniam",
    196: "Aaseertush", 197: "Uglyestemaa", 198: "Horeroedsh", 199: "Drundemiso",
    200: "Ityanianat", 201: "Purneyrine", 202: "Dokiessmat", 203: "Nupiacheh",
    204: "Dihewsonj", 205: "Rudrailhik", 206: "Tweretnort", 207: "Snatreetze",
    208: "Iwundaracos", 209: "Digarlewena", 210: "Erquagsta", 211: "Logovoloin",
    212: "Boyaghosganh", 213: "Kuolungau", 214: "Pehneldept", 215: "Yevettiiqidcon",
    216: "Sahliacabru", 217: "Noggalterpor", 218: "Chmageaki", 219: "Veticueca",
    220: "Vittesbursul", 221: "Nootanore", 222: "Innebdjerah", 223: "Kisvarcini",
    224: "Cuzcogipper", 225: "Pamanhermonsu", 226: "Brotoghek", 227: "Mibittara",
    228: "Huruahili", 229: "Raldwicarn", 230: "Ezdartlic", 231: "Badesclema",
    232: "Isenkeyan", 233: "Iadoitesu", 234: "Yagrovoisi", 235: "Ewcomechio",
    236: "Inunnunnoda", 237: "Dischiutun", 238: "Yuwarugha", 239: "Ialmendra",
    240: "Reponudrle", 241: "Rinjanagrbo", 242: "Zeziceloh", 243: "Oeileutasc",
    244: "Zicniijinis", 245: "Dugnowarilda", 246: "Neuxoisan", 247: "Ilmenhorn",
    248: "Rukwatsuku", 249: "Nepitzaspru", 250: "Chcehoemig", 251: "Haffneyrin",
    252: "Uliciawai", 253: "Tuhgrespod", 254: "Iousongola", 255: "Odyalutai",
}

def get_galaxy_name(galaxy_idx: int) -> str:
    """Get galaxy name from 0-indexed game ID. Fallback uses 1-indexed numbering."""
    return GALAXY_NAMES.get(galaxy_idx, f"Galaxy_{galaxy_idx + 1}")


# Note: Config GUI now uses pymhf's native DearPyGUI via gui_variable.ENUM and gui_variable.STRING decorators
# in the HavenExtractorMod class. See the class definition for config fields.


def load_config_from_file() -> dict:
    """
    Load configuration from config file only - NO GUI.
    This is safe to call at module load time.
    """
    config = {
        "api_url": DEFAULT_API_URL,
        "api_key": HAVEN_EXTRACTOR_API_KEY,
        "discord_username": "",
        "personal_id": "",
        "discord_tag": "personal",
        "reality": "Normal",
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
                    # Load all config fields
                    for key in config:
                        if key in file_config and file_config[key]:
                            config[key] = file_config[key]
                    logger.info(f"Loaded config from: {config_path}")
                    break
        except Exception as e:
            logger.debug(f"Could not load config from {config_path}: {e}")

    return config


def save_config(config: dict):
    """Save configuration to file."""
    save_path = Path.home() / "Documents" / "Haven-Extractor" / "config.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
    logger.info(f"Saved user config to: {save_path}")


def config_needs_setup(config: dict) -> bool:
    """Check if configuration needs user setup (missing required fields)."""
    return not config.get("discord_username")


# Load config at module level - NO GUI (deferred to button/export)
_config = load_config_from_file()
API_BASE_URL = _config["api_url"]
API_KEY = _config["api_key"]
USER_DISCORD_USERNAME = _config.get("discord_username", "")
USER_PERSONAL_ID = _config.get("personal_id", "")
# Strip any leftover "RealityMode." / "CommunityTag." class prefix that an
# earlier (≤1.9.7) build may have persisted into config.json. Helpers
# `_normalize_reality` / `_normalize_community_tag` are defined later in the
# file (after the enum classes), so this is an inline equivalent that only
# runs at module init. The full validators kick in on every setter call.
_loaded_discord_tag = _config.get("discord_tag", "personal")
if isinstance(_loaded_discord_tag, str) and "." in _loaded_discord_tag:
    _loaded_discord_tag = _loaded_discord_tag.split(".", 1)[1]
USER_DISCORD_TAG = _loaded_discord_tag or "personal"

_loaded_reality = _config.get("reality", "Normal")
if isinstance(_loaded_reality, str) and "." in _loaded_reality:
    _loaded_reality = _loaded_reality.split(".", 1)[1]
USER_REALITY = _loaded_reality if _loaded_reality in ("Normal", "Permadeath") else "Normal"

# =============================================================================
# DYNAMIC COMMUNITY LIST
# Fetched from server on startup, cached locally, hardcoded fallback
# =============================================================================

_DEFAULT_COMMUNITY_TAGS = [
    "personal", "Haven", "AGT", "ARCH", "AA", "AP", "B.E.S", "YGS", "CR",
    "EVRN", "GHUB", "IEA", "NEO", "O.Q", "Ph-Z0", "QRR", "RwR", "SHDW",
    "Veil1", "TBH", "INDM", "TMA", "UFE", "VCTH", "ZBA",
]

def _fetch_communities_list() -> list:
    """
    Fetch community tags from Haven API, with local cache fallback.
    Priority: live server → cached file → hardcoded defaults.
    """
    cache_path = Path.home() / "Documents" / "Haven-Extractor" / "communities_cache.json"

    # Try fetching from server
    try:
        url = f"{API_BASE_URL}/api/communities"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            'User-Agent': f'HavenExtractor',
        })
        with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
            data = json.loads(response.read().decode('utf-8'))

        communities = data.get('communities', [])
        tags = [c['tag'] for c in communities if c.get('tag')]

        if tags:
            # Normalize "Personal" → "personal" (API returns capital P)
            tags = ["personal" if t.lower() == "personal" else t for t in tags]
            # Ensure "personal" is always first
            if "personal" in tags:
                tags.remove("personal")
            tags.insert(0, "personal")

            # Cache to disk
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({"tags": tags}, f, indent=2)

            logger.info(f"[COMMUNITIES] Fetched {len(tags)} communities from server")
            return tags
    except Exception as e:
        logger.debug(f"[COMMUNITIES] Could not fetch from server: {e}")

    # Try loading from cache
    try:
        if cache_path.exists():
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            tags = cached.get('tags', [])
            if tags:
                logger.info(f"[COMMUNITIES] Using cached list ({len(tags)} communities)")
                return tags
    except Exception as e:
        logger.debug(f"[COMMUNITIES] Could not read cache: {e}")

    # Hardcoded fallback
    logger.info(f"[COMMUNITIES] Using default list ({len(_DEFAULT_COMMUNITY_TAGS)} communities)")
    return list(_DEFAULT_COMMUNITY_TAGS)


def _make_enum_name(tag: str) -> str:
    """Convert a community tag string to a valid Python enum member name."""
    name = tag.replace('.', '_').replace('-', '_')
    if name[0].isdigit():
        name = '_' + name
    return name


# Fetch community list at module load (before class definition)
_COMMUNITY_TAGS = _fetch_communities_list()

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

# Resource ID to human-readable name mapping
# NOTE: The game displays base stellar metals (Copper, Cadmium, etc.) even when
# the internal ID is the "2" variant. We map YELLOW2->Copper etc. to match game display.
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










# =========================================================================
# GUI Enum types for pymhf 0.2.2+ (replaces deprecated gui_combobox)
# =========================================================================

# CommunityTag enum built dynamically from server/cache/fallback list
CommunityTag = Enum('CommunityTag', {_make_enum_name(tag): tag for tag in _COMMUNITY_TAGS})


class RealityMode(Enum):
    Normal = "Normal"
    Permadeath = "Permadeath"


# 2026-05-12: pymhf's DearPyGUI sometimes round-trips the ENUM gui_variable
# as the Python enum repr ("RealityMode.Normal") instead of as a RealityMode
# instance. Previously the setter's `str(value)` fallback persisted that bad
# string verbatim into config.json and every submission payload, surfacing
# as a phantom "RealityMode.Normal" reality card in the Haven UI Systems
# Browser. Strip any enum-class prefix and validate against the known values.
def _normalize_reality(value) -> str:
    if value is None:
        return "Normal"
    if isinstance(value, RealityMode):
        return value.value
    s = str(value).strip()
    if "." in s:
        s = s.split(".", 1)[1]
    return s if s in ("Normal", "Permadeath") else "Normal"


# Same defense for CommunityTag — its setter has the identical str()-fallback
# shape. Strips "CommunityTag." prefix if pymhf round-trips an enum repr.
def _normalize_community_tag(value, default: str = "personal") -> str:
    if value is None:
        return default
    if isinstance(value, CommunityTag):
        return value.value
    s = str(value).strip()
    if "." in s:
        s = s.split(".", 1)[1]
    valid_values = {m.value for m in CommunityTag}
    return s if s in valid_values else default

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


class HavenExtractorMod(Mod):
    __author__ = "Voyagers Haven"
    __version__ = "1.10.6"
    __description__ = "Releases have moved from Parker1920/Master-Haven to Voyagers-Haven-LLC/nms-haven-extractor. The updater (UPDATE_HAVEN_EXTRACTOR.bat) can only replace files inside mod/, so it can never fix its own release URL — instead the mod rewrites the sibling .bat at startup (_repoint_updater_bat) if it still points at the old repo. No extraction/gameplay changes."

    # ==========================================================================
    # VALID ADJECTIVE LISTS FROM adjectives.js
    # ALL values MUST come from Haven UI's adjectives.js - curated from in-game
    # Uses lists per level for variety, selected by planet_index % len(list)
    # ==========================================================================




    # Fallback mappings (simple level-based, used when list selection fails)
    FLORA_LEVELS = {0: "None", 1: "Sparse", 2: "Average", 3: "Bountiful"}
    FAUNA_LEVELS = {0: "None", 1: "Sparse", 2: "Regular", 3: "Copious"}
    LIFE_LEVELS = {0: "None", 1: "Sparse", 2: "Average", 3: "Abundant"}
    SENTINEL_LEVELS = {0: "Minimal", 1: "Limited", 2: "High", 3: "Aggressive"}

    # =========================================================================
    # CONFIG GUI FIELDS - Using pymhf's native DearPyGUI
    # These appear as editable fields in the mod's GUI tab
    # =========================================================================

    @property
    @gui_variable.STRING(label="Discord Username")
    def discord_username(self) -> str:
        return self._discord_username

    @discord_username.setter
    def discord_username(self, value: str):
        global USER_DISCORD_USERNAME
        self._discord_username = value
        USER_DISCORD_USERNAME = value
        self._save_config_to_file()

    # Community tags - dynamically fetched from server (see _fetch_communities_list)
    COMMUNITY_TAGS = _COMMUNITY_TAGS

    @property
    @gui_variable.ENUM("Community Tag", enum=CommunityTag)
    def community_tag(self) -> CommunityTag:
        tag_str = getattr(self, '_discord_tag', 'personal')
        try:
            return CommunityTag(tag_str)
        except ValueError:
            return CommunityTag.personal

    @community_tag.setter
    def community_tag(self, value: CommunityTag):
        global USER_DISCORD_TAG
        tag = _normalize_community_tag(value)
        self._discord_tag = tag
        USER_DISCORD_TAG = tag
        self._save_config_to_file()
        logger.info(f"[CONFIG] Community tag set to: {tag}")

    @property
    @gui_variable.ENUM("Reality Mode", enum=RealityMode)
    def reality_mode(self) -> RealityMode:
        reality_str = getattr(self, '_reality', 'Normal')
        try:
            return RealityMode(reality_str)
        except ValueError:
            return RealityMode.Normal

    @reality_mode.setter
    def reality_mode(self, value: RealityMode):
        global USER_REALITY
        reality = _normalize_reality(value)
        self._reality = reality
        USER_REALITY = reality
        self._save_config_to_file()
        logger.info(f"[CONFIG] Reality mode set to: {reality}")

    # =========================================================================
    # CUSTOM SYSTEM NAME
    # User-entered name for renamed systems (overrides procgen fallback).
    # Applies to the current system via the "Apply Custom Name" button.
    # =========================================================================

    @property
    @gui_variable.STRING(label="Custom System Name (for renamed systems)")
    def custom_system_name(self) -> str:
        return getattr(self, '_custom_system_name', '')

    @custom_system_name.setter
    def custom_system_name(self, value: str):
        self._custom_system_name = value or ''

    # =========================================================================
    # STATUS NOTIFICATION
    # Read-only field showing last export result or current status.
    # =========================================================================

    @property
    @gui_variable.STRING(label="Status", readonly=True)
    def status_display(self) -> str:
        return getattr(self, '_status_display', 'Ready')

    @status_display.setter
    def status_display(self, value: str):
        self._status_display = value

    def _save_config_to_file(self):
        """Save current config to file."""
        try:
            config = {
                "api_url": DEFAULT_API_URL,
                "api_key": API_KEY,  # Saves per-user key (not the old shared key)
                "discord_username": self._discord_username,
                "personal_id": self._personal_id,
                "discord_tag": self._discord_tag,
                "reality": self._reality,
            }
            save_config(config)
        except Exception as e:
            logger.debug(f"Could not save config: {e}")

    def _register_api_key(self) -> bool:
        """Register this extractor instance and get a per-user API key."""
        if not self._discord_username:
            logger.error("[REGISTER] Discord username is required. Set it in the field above first.")
            return False

        logger.info(f"[REGISTER] Registering API key for '{self._discord_username}'...")

        try:
            url = f"{API_BASE_URL}/api/extractor/register"
            payload = json.dumps({"discord_username": self._discord_username}).encode('utf-8')

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'User-Agent': f'HavenExtractor/{self.__version__}',
            })

            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                result = json.loads(response.read().decode('utf-8'))

            if result.get('status') == 'registered' and result.get('key'):
                global API_KEY
                API_KEY = result['key']
                self._save_config_to_file()
                logger.info("")
                logger.info("=" * 60)
                logger.info(">>> REGISTRATION SUCCESSFUL <<<")
                logger.info(f"    Your personal API key has been saved.")
                logger.info(f"    Key prefix: {result.get('key_prefix', 'unknown')}...")
                logger.info(f"    Rate limit: {result.get('rate_limit', 100)} requests/hour")
                logger.info("=" * 60)
                logger.info("")
                return True
            elif result.get('status') == 'already_registered':
                logger.warning("")
                logger.warning("=" * 60)
                logger.warning(">>> ALREADY REGISTERED <<<")
                logger.warning(f"    An API key already exists for '{self._discord_username}'.")
                logger.warning(f"    Key prefix: {result.get('key_prefix', 'unknown')}...")
                logger.warning("    If you lost your key, contact a Haven admin.")
                logger.warning("=" * 60)
                logger.warning("")
                return False
            else:
                msg = result.get('message') or result.get('detail') or 'Unknown error'
                logger.error(f"[REGISTER] Registration failed: {msg}")
                return False

        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode('utf-8'))
                detail = error_body.get('detail', str(e))
            except Exception:
                detail = str(e)
            logger.error(f"[REGISTER] Registration failed: {detail}")
            return False
        except Exception as e:
            logger.error(f"[REGISTER] Failed to connect to Haven API: {e}")
            return False

    def __init__(self):
        super().__init__()
        self._output_dir = Path.home() / "Documents" / "Haven-Extractor"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize config fields from loaded config
        self._discord_username = USER_DISCORD_USERNAME
        self._personal_id = USER_PERSONAL_ID  # Discord snowflake ID for tracking
        self._discord_tag = USER_DISCORD_TAG
        self._reality = USER_REALITY
        self._game_mode = "Normal"  # Auto-detected from memory at extraction time
        self._status_display = 'Ready'  # GUI status notification
        self._custom_system_name = ''  # User-entered custom name for renamed systems

        self._pending_extraction = False
        self._last_extracted_seed = None
        self._cached_solar_system = None
        self._cached_sys_data_addr = None  # Cache the sys_data address for direct reads

        # Debouncing - don't re-extract more often than this
        self._last_extraction_time = 0
        self._extraction_debounce_seconds = 3

        # =====================================================
        # Captured planet data from GenerateCreatureRoles hook
        # This dictionary stores Flora, Fauna, Sentinels captured by the hook
        # Key: planet_index, Value: dict with captured data
        # =====================================================
        self._captured_planets = {}
        self._capture_enabled = False  # Only capture after system generates

        # v1.9.7: System-level properties snapshot, captured WHILE the current system is
        # the active system in memory. Used by _save_current_system_to_batch instead of
        # re-reading sys_data at save time. Save runs from the NEXT system's
        # on_system_generate, by which point NMS has begun populating new sys_data into
        # the same memory pool - re-reading there pulled the wrong system's lifeform /
        # star_type / economy / planet count for every batched-non-last system.
        self._current_system_snapshot = None

        # =====================================================
        # BATCH MODE - Store multiple star systems
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
        # v1.4.0: Translation cache for adjective resolution
        # Populated by cTkLanguageManagerBase.Translate hook
        # =====================================================
        self._translation_cache = {}   # text_id -> display_text (from game's Translate)
        self._adjective_file_cache = {}  # text_id -> display_text (from PAK/MBIN parsing)
        self._translation_cache_hits = 0
        self._translation_cache_misses = 0

        # Load adjective cache from disk (if exists)
        self._load_adjective_cache()

        # v1.10.6: migrate installs whose updater still fetches from the old repo
        self._repoint_updater_bat()

        logger.info(f"Haven Extractor v{self.__version__} ready")
        logger.info("  Warp to systems → data captured automatically → Export when ready")


    def _repoint_updater_bat(self):
        """One-time migration (v1.10.6): releases moved from Parker1920/Master-Haven
        to Voyagers-Haven-LLC/nms-haven-extractor. UPDATE_HAVEN_EXTRACTOR.bat lives
        one level ABOVE mod/ and the update zip only ever overwrites mod/, so the
        .bat can't fix itself — rewrite its release URLs from here instead.
        Binary read/write so line endings and encoding pass through untouched."""
        try:
            bat = Path(__file__).resolve().parent.parent / "UPDATE_HAVEN_EXTRACTOR.bat"
            if not bat.is_file():
                return
            data = bat.read_bytes()
            if b"Parker1920/Master-Haven" not in data:
                return
            bat.write_bytes(data.replace(b"Parker1920/Master-Haven",
                                         b"Voyagers-Haven-LLC/nms-haven-extractor"))
            logger.info("[UPDATER] Repointed UPDATE_HAVEN_EXTRACTOR.bat to Voyagers-Haven-LLC/nms-haven-extractor")
        except Exception as e:
            logger.warning(f"[UPDATER] Could not repoint UPDATE_HAVEN_EXTRACTOR.bat: {e}")


    # =========================================================================
    # v1.4.0: LANGUAGE TRANSLATION - Game's own text resolution
    # =========================================================================

    @nms.cTkLanguageManagerBase.Translate.after
    def on_translate(self, this, lpacText, lpacDefaultReturnValue, _result_):
        """
        Passive hook on the game's Translate function.
        Captures (text_id -> display_text) pairs as the game resolves them.
        This fires whenever the game renders text (UI, discovery pages, scanner).
        """
        try:
            if not lpacText or not _result_:
                return

            # Decode the text ID
            if isinstance(lpacText, bytes):
                text_id = lpacText.decode('utf-8', errors='ignore')
            else:
                text_id = str(ctypes.cast(lpacText, ctypes.c_char_p).value or b'', 'utf-8', errors='ignore')

            if not text_id:
                return

            # Only capture adjective-related text IDs
            if text_id.startswith(('RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_BIOME_', 'BIOME_',
                                   'UI_PLANET_', 'UI_SENTINEL_', 'UI_WEATHER_', 'UI_FLORA_',
                                   'UI_FAUNA_', 'UI_RARITY_')):
                # Read the result string from the return value
                try:
                    result_ptr = ctypes.cast(_result_, ctypes.c_char_p)
                    if result_ptr.value:
                        display_text = result_ptr.value.decode('utf-8', errors='ignore')
                        # Only store if it looks like valid display text (not another ID)
                        if (display_text and len(display_text) >= 2 and
                            not display_text.startswith(('RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_'))):
                            if text_id not in self._translation_cache:
                                self._translation_cache[text_id] = display_text
                                logger.debug(f"    [TRANSLATE] Captured: '{text_id}' -> '{display_text}'")
                except Exception:
                    pass
        except Exception:
            pass  # Never crash the game from a translation hook

    def _load_adjective_cache(self):
        """Load adjective cache from disk, or build it from game PAK files in background.

        Priority:
        1. User's existing cache in ~/Documents/Haven-Extractor/
        2. Bundled cache shipped with the mod (copied to user dir on first use)
        3. Background build from game PAK files
        """
        try:
            try:
                from .nms_language import AdjectiveCacheBuilder
            except ImportError:
                from nms_language import AdjectiveCacheBuilder

            builder = AdjectiveCacheBuilder(cache_dir=self._output_dir)

            # Try loading user's existing cache
            cached = builder.load_cache()
            if cached:
                self._adjective_file_cache = cached
                logger.info(f"[INIT] Loaded {len(cached)} adjective mappings from cache")
                return

            # No user cache — check for bundled cache shipped with the mod
            bundled_cache = Path(__file__).parent / "adjective_cache.json"
            if bundled_cache.exists():
                try:
                    import shutil
                    shutil.copy2(str(bundled_cache), str(builder.cache_path))
                    cached = builder.load_cache()
                    if cached:
                        self._adjective_file_cache = cached
                        logger.info(f"[INIT] Loaded {len(cached)} adjective mappings from bundled cache")
                        return
                except Exception as e:
                    logger.warning(f"[INIT] Failed to copy bundled cache: {e}")

            # No cache available — try building from game PAK files in background
            if not builder.nms_path:
                logger.info("[INIT] NMS installation not found - using legacy adjective tables")
                return

            logger.info("[INIT] Building adjective cache from game files (background)...")
            import threading

            def build_async():
                try:
                    mappings = builder.build_cache()
                    self._adjective_file_cache = mappings
                    logger.info(f"[INIT] Background cache build complete: {len(mappings)} entries")
                except Exception as e:
                    logger.warning(f"[INIT] Background cache build failed: {e}")

            threading.Thread(target=build_async, daemon=True).start()

        except Exception as e:
            logger.warning(f"[INIT] Failed to load adjective cache: {e}")
            import traceback
            logger.warning(traceback.format_exc())

    def _resolve_adjective(self, text_id: str, field_type: str = 'flora') -> str:
        """
        Resolve a text ID to its display adjective using layered lookup:
        1. Disk-based PAK/MBIN cache (adjective_cache.json - primary)
        2. In-memory translation cache (from game's Translate hook - backup)
        3. Original value as last resort

        Args:
            text_id: The raw text ID (e.g., 'RARITY_HIGH3', 'WEATHER_COLD7')
            field_type: 'flora', 'fauna', 'sentinel', or 'weather'

        Returns:
            The resolved display adjective
        """
        if not text_id or text_id == "None":
            return "Unknown"

        # Already a display string? (doesn't match internal ID patterns)
        if not any(text_id.startswith(p) for p in [
            'RARITY_', 'SENTINEL_', 'WEATHER_', 'UI_BIOME_', 'BIOME_',
            'UI_PLANET_', 'UI_SENTINEL_', 'UI_WEATHER_', 'UI_FLORA_',
            'UI_FAUNA_', 'UI_RARITY_'
        ]):
            return text_id

        # Layer 1: Disk-based PAK/MBIN cache (primary - built from game files)
        if text_id in self._adjective_file_cache:
            return self._adjective_file_cache[text_id]

        # Layer 2: In-memory translation cache (backup - from Translate hook)
        if text_id in self._translation_cache:
            self._translation_cache_hits += 1
            return self._translation_cache[text_id]

        # Unresolved - return original text ID
        self._translation_cache_misses += 1
        return text_id

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

            # v1.6.14: Detect systems where NMS itself reports no data. Some systems
            # are legitimately "-Data Unavailable-" for economy/conflict and "Uncharted"
            # for lifeform — even after a full freighter scanner-room scan, the game
            # has no values to show for these fields. The memory at these offsets still
            # decodes to real enum values (Mining/Poor/Low/Gek) which we'd otherwise
            # submit as fake data.
            # The signal: INHABITING_RACE raw value outside the valid 0-6 range.
            # Real races are 0-6 (Gek, Vy'keen, Korvax, Robots, Atlas, Diplomats,
            # Uninhabited). Value 7+ only appears for no-data systems. When we see
            # that, clear the co-located fields so Haven shows them as missing rather
            # than fabricated.
            if race_val > 6:
                logger.debug(f"  [DIRECT] System reports no economy/conflict/lifeform data (race={race_val})")
                result["economy_type"] = "Unknown"
                result["economy_strength"] = "Unknown"
                result["conflict_level"] = "Unknown"
                result["dominant_lifeform"] = "Unknown"

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
            "planet_seed": 0,
        }

        try:
            # Calculate address for this planet's gen input data
            # Array starts at PLANET_GEN_INPUTS, each entry is STRUCT_SIZE bytes
            planet_gen_addr = sys_data_addr + SolarSystemDataOffsets.PLANET_GEN_INPUTS
            planet_gen_addr += planet_index * PlanetGenInputOffsets.STRUCT_SIZE

            logger.debug(f"    [DIRECT] Planet {planet_index} gen input at 0x{planet_gen_addr:X}")

            # Read biome
            biome_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME)
            result["biome_raw"] = biome_val
            result["biome"] = BIOME_TYPES.get(biome_val, f"Unknown({biome_val})")
            logger.debug(f"    [DIRECT] Biome: {result['biome']} (raw: {biome_val})")

            # Read biome subtype
            biome_subtype_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME_SUBTYPE)
            result["biome_subtype_raw"] = biome_subtype_val
            result["biome_subtype"] = BIOME_SUBTYPES.get(biome_subtype_val, f"Unknown({biome_subtype_val})")
            logger.debug(f"    [DIRECT] BiomeSubType: {result['biome_subtype']} (raw: {biome_subtype_val})")

            # Read planet size (critical for moon detection)
            size_val = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.PLANET_SIZE)
            result["planet_size_raw"] = size_val
            result["is_moon"] = (size_val == 3)  # Moon = 3
            # For moons, don't set planet_size to "Moon" - use "Small" to avoid duplicate badge
            # (is_moon already indicates it's a moon)
            if result["is_moon"]:
                result["planet_size"] = "Small"
            else:
                result["planet_size"] = PLANET_SIZES.get(size_val, f"Unknown({size_val})")
            logger.debug(f"    [DIRECT] Size: {result['planet_size']} (raw: {size_val}, is_moon: {result['is_moon']})")

            # Read resources (16-byte strings), clean, and translate to human-readable names
            raw_common = self._clean_resource_string(self._read_string(planet_gen_addr, PlanetGenInputOffsets.COMMON_SUBSTANCE, 16))
            raw_rare = self._clean_resource_string(self._read_string(planet_gen_addr, PlanetGenInputOffsets.RARE_SUBSTANCE, 16))
            result["common_resource"] = translate_resource(raw_common) if raw_common else ""
            result["rare_resource"] = translate_resource(raw_rare) if raw_rare else ""
            logger.debug(f"    [DIRECT] Resources: common={result['common_resource']} ({raw_common}), rare={result['rare_resource']} ({raw_rare})")

            # Read planet seed (GcSeed at offset 0x20, 8 bytes)
            seed_val = self._read_uint64(planet_gen_addr, PlanetGenInputOffsets.SEED)
            if seed_val and seed_val != 0:
                result["planet_seed"] = seed_val
                logger.debug(f"    [DIRECT] Planet seed: 0x{seed_val:016X}")

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
    # BATCH MODE - Save current system to batch storage
    # =========================================================================

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

    # v1.10.5: Option C REMOVED — the `cGcSolarSystem.Generate.before` hook fails to register
    # on some pyMHF/nmspy builds ("cGcSolarSystem ... not found" at mod load), which breaks the
    # ENTIRE mod after an update. This codebase has only ever used `.after`; `.before` was
    # unverified. The method below is left UNHOOKED (no decorator = never called = harmless dead
    # code) for reference. Batch display-adjectives will be redone via the proven creature-roles
    # hook (`.after`) in a follow-up, not a `.before` hook.
    def _disabled_on_system_generate_before(self, this, lbUseSettingsFile, lSeed):
        """v1.10.4 (Option C — batch display-adjective fix): freeze the OUTGOING system at
        warp-OUT, while its live PlanetInfo is still readable, BEFORE NMS recycles the
        solar-system object to generate the incoming system.

        Why this is the right moment: the exact Weather/Sentinel/Flora/Fauna adjectives only
        populate in the live PlanetInfo array after entry, and the Generate.after save freezes
        purely from cache (no re-read) — so every batched (non-last) system shipped the generic
        enum tier. The only window a system's adjectives are readable is while it is the live
        one; the last such instant is warp-out, here at Generate.before (the overwrite that
        recycles the object happens DURING Generate, i.e. between .before and .after).

        BLEED-PROOF: read the live planet names FIRST and only refresh+freeze if they still
        overlap the OUTGOING system's captured names (procgen names never collide across
        systems). If the names don't overlap, the object was already recycled, so we do
        NOTHING and let the existing Generate.after path save from cache exactly as before —
        this can never stamp the incoming system's adjectives/props onto the outgoing one.
        Verbose [GEN-BEFORE] logging makes the chosen path visible in the log.
        """
        if not self._batch_mode_enabled or not self._capture_enabled:
            return
        if self._system_saved_to_batch or not self._captured_planets or self._cached_solar_system is None:
            return
        try:
            captured_names = {str(k) for k in self._captured_planets if not str(k).startswith('_unnamed_')}
            live_names = []
            try:
                planets = self._cached_solar_system.maPlanets
                for i in range(min(6, len(planets) if planets is not None else 0)):
                    try:
                        planet = planets[i]
                        pd = planet.mPlanetData if (planet is not None and hasattr(planet, 'mPlanetData')) else None
                        if pd is None or not hasattr(pd, 'Name'):
                            continue
                        n = str(pd.Name).strip()
                        if n and n != "None":
                            live_names.append(n)
                    except Exception:
                        continue
            except Exception as e:
                logger.info(f"[GEN-BEFORE] could not read live planet names: {e}")

            overlap = captured_names & set(live_names)
            logger.info(
                f"[GEN-BEFORE] outgoing captures={sorted(captured_names)} | "
                f"live slots now={live_names} | overlap={len(overlap)}/{len(captured_names)}"
            )

            if captured_names and len(overlap) >= 1:
                # Confirmed: the live array is STILL the outgoing system (names match), so its
                # PlanetInfo + sysdata are intact -> safe to refresh adjectives and freeze here.
                self._auto_refresh_for_export()
                self._save_current_system_to_batch()
                self._system_saved_to_batch = True
                logger.info(
                    "[GEN-BEFORE] OPTION-C ACTIVE: froze outgoing system WITH refreshed "
                    "adjectives (memory still intact at Generate.before)."
                )
            else:
                logger.info(
                    "[GEN-BEFORE] live array no longer matches outgoing captures (object already "
                    "recycled) — NOT acting here; Generate.after saves from cache as before. "
                    "Option C not viable on this build."
                )
        except Exception as e:
            logger.info(f"[GEN-BEFORE] non-fatal error: {e}")

    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
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
            self._current_system_snapshot = None
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

    # =========================================================================
    # GenerateCreatureRoles HOOK - Captures Flora/Fauna/Sentinels
    # This hook fires for EVERY planet as the game generates creature roles.
    # The lPlanetData parameter contains the actual planet data we need!
    # =========================================================================

    @nms.cGcPlanetGenerator.GenerateCreatureRoles.after
    def on_creature_roles_generate(self, this, lPlanetData, lUA):
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

    # =========================================================================
    # GUI BUTTONS
    # =========================================================================

    @gui_button("System Data")
    def check_system_data(self):
        """
        Check planet data status - shows both mPlanetData AND captured hook data.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> PLANET DATA STATUS <<<")
        logger.info(f">>> Captured planets via hook: {len(self._captured_planets)} <<<")
        logger.info("=" * 60)

        # First, show captured hook data summary (name-keyed as of v1.6.11)
        if self._captured_planets:
            logger.info("")
            logger.info("=== CAPTURED HOOK DATA SUMMARY ===")
            for key, captured in sorted(self._captured_planets.items(), key=lambda kv: str(kv[0])):
                moon_tag = " [MOON]" if captured.get('is_moon') else ""
                logger.info(f"  '{key}'{moon_tag}")
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

                # v1.6.12: Match memory slot to captured entry by NAME (hook order != slot order)
                name = f"Planet_{i+1}"
                try:
                    if hasattr(planet, 'mPlanetData'):
                        pd = planet.mPlanetData
                        if hasattr(pd, 'Name'):
                            n = str(pd.Name)
                            if n and n != "None" and len(n.strip()) > 0:
                                name = n.strip()
                except Exception:
                    pass

                cap = self._captured_planets.get(name)
                if cap is not None:
                    status = "CAPTURED"
                    biome = cap.get('biome', 'Unknown')
                    biome_subtype = cap.get('biome_subtype', 'Unknown')
                    planet_size = cap.get('planet_size', 'Unknown')
                    is_moon = cap.get('is_moon', False)
                    weather = cap.get('weather', 'Unknown')
                    flora = cap.get('flora', 'Unknown')
                    fauna = cap.get('fauna', 'Unknown')
                    sentinels = cap.get('sentinel', 'Unknown')
                else:
                    status = "NO_HOOK_DATA"
                    biome = biome_subtype = planet_size = weather = flora = fauna = sentinels = "Unknown"
                    is_moon = False

                moon_str = " [MOON]" if is_moon else ""
                logger.info(f"  Slot {i} [{status}]: {name}{moon_str}")
                logger.info(f"    Biome: {biome}, SubType: {biome_subtype}")
                logger.info(f"    Size: {planet_size}, is_moon: {is_moon}")
                logger.info(f"    Weather: {weather}")
                logger.info(f"    Flora: {flora}, Fauna: {fauna}, Sentinels: {sentinels}")

        except Exception as e:
            logger.error(f"Check planet data failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

        logger.info("=" * 60)

    def _read_bytes(self, base_addr: int, offset: int, size: int) -> bytes:
        """Read raw bytes from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            buffer = (ctypes.c_char * size)()
            ctypes.memmove(buffer, addr, size)
            return bytes(buffer)
        except Exception:
            return None

    def _read_uint64(self, base_addr: int, offset: int) -> int:
        """Read uint64 from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint64)).contents.value
        except Exception:
            return 0

    def _read_uint32(self, base_addr: int, offset: int) -> int:
        """Read uint32 from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint32)).contents.value
        except Exception:
            return 0

    def _read_string(self, base_addr: int, offset: int, max_len: int = 128) -> str:
        """Read null-terminated string from memory."""
        try:
            raw = self._read_bytes(base_addr, offset, max_len)
            if raw:
                null_pos = raw.find(b'\x00')
                if null_pos > 0:
                    return raw[:null_pos].decode('utf-8', errors='ignore')
            return ""
        except Exception:
            return ""

    @gui_button("Batch Status")
    def check_batch_data(self):
        """
        Show current batch status - how many systems are stored.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> BATCH DATA STATUS <<<")
        logger.info("=" * 60)

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
            logger.info("    (Will be added to batch on Export)")

        logger.info("")
        logger.info("  User config:")
        logger.info(f"    Discord: {USER_DISCORD_USERNAME}")
        logger.info(f"    Community: {USER_DISCORD_TAG}")
        logger.info(f"    Reality: {USER_REALITY}")

        logger.info("=" * 60)
        logger.info("")

    @gui_button("Apply Custom Name")
    def apply_custom_system_name(self):
        """
        Apply the custom system name to the current system.
        Overrides the procgen name for renamed systems. The procgen name is
        preserved in the submission's description field so the info isn't lost.
        """
        name = (getattr(self, '_custom_system_name', '') or '').strip()
        if not name:
            self._status_display = "Enter a custom name first"
            logger.warning("[CUSTOM NAME] No name entered — type in the field above and click again.")
            return

        # Must have a current system
        if not self._current_system_coords:
            self._status_display = "No current system - warp first"
            logger.warning("[CUSTOM NAME] No current system detected. Warp into a system first.")
            return

        glyph = self._current_system_coords.get('glyph_code', '')
        if not glyph:
            self._status_display = "Current system has no glyph"
            logger.warning("[CUSTOM NAME] Current system has no glyph code — cannot apply.")
            return

        # Set on current coords so the next _save_current_system_to_batch picks it up
        self._current_system_coords['system_name'] = name
        self._current_system_coords['custom_name_applied'] = True

        # If this system is already in the batch, patch it in place
        patched = False
        for entry in self._batch_systems:
            if entry.get('glyph_code') == glyph:
                old_name = entry.get('system_name', '')
                entry['system_name'] = name
                entry['custom_name_applied'] = True

                # Preserve procgen name in description so the info isn't lost on approval
                procgen = entry.get('procedural_name') or self._generate_system_name(
                    glyph,
                    entry.get('galaxy_index', 0),
                    system_idx=entry.get('solar_system_index'),
                    x=entry.get('voxel_x'), y=entry.get('voxel_y'), z=entry.get('voxel_z'),
                )
                entry['procedural_name'] = procgen
                existing_desc = entry.get('description', '') or ''
                marker = f"Procedural name: {procgen}"
                if marker not in existing_desc:
                    entry['description'] = (existing_desc + ("\n" if existing_desc else "") + marker).strip()

                logger.info(f"[CUSTOM NAME] Updated batch entry: '{old_name}' -> '{name}' (procgen '{procgen}' preserved in description)")
                patched = True
                break

        if not patched:
            # Not yet saved — will be picked up on next batch save via _current_system_coords
            logger.info(f"[CUSTOM NAME] Queued '{name}' for current system (glyph {glyph}). Will apply at next batch save.")

        self._status_display = f"Custom name: {name}"
        # Clear the input so the next system starts fresh
        self._custom_system_name = ''

    @gui_button("Config Status")
    def show_config_status(self):
        """
        Display current configuration in log.
        Config fields are editable in the GUI above (Discord Username, Discord ID, dropdowns).
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> CURRENT CONFIGURATION <<<")
        logger.info("=" * 60)
        logger.info(f"  Discord Username: {self._discord_username or '(not set)'}")
        logger.info(f"  Community Tag:    {self._discord_tag}")
        logger.info(f"  Reality Mode:     {self._reality}")
        logger.info("=" * 60)
        if not self._discord_username:
            logger.warning("[CONFIG] Discord Username is required for export!")
            logger.info("[CONFIG] Enter your Discord username in the text field above.")
        else:
            logger.info("[CONFIG] Configuration is complete. Ready to export!")
        logger.info("")

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

    @gui_button("Export to Haven")
    def export_to_haven_ui(self):
        """
        Export systems directly to Haven UI with duplicate checking.
        Uploads all collected systems in batch with progress logging.
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info(">>> EXPORT TO HAVEN UI <<<")
        logger.info("=" * 60)

        # Check if config is complete
        if not self._discord_username:
            self._status_display = "Set Discord Username first"
            logger.error("Set your Discord Username before exporting.")
            return

        # Auto-register if no personal API key or still using the old shared key
        if not API_KEY or API_KEY == _OLD_SHARED_KEY:
            logger.info("Registering with Haven...")
            if not self._register_api_key():
                self._status_display = "Registration failed"
                logger.error("Registration failed — check internet connection.")
                return

        # Auto-refresh adjectives from game memory if planets are captured
        if self._captured_planets:
            self._auto_refresh_for_export()

        # Always force-update the current system's batch entry (applies manual name even if
        # _captured_planets was cleared by a game event like on_system_generate re-firing)
        if self._cached_solar_system:
            self._save_current_system_to_batch(force_update=True)

        if not self._batch_systems:
            self._status_display = "No systems in batch"
            logger.warning("No systems to export! Visit some systems first.")
            return

        total = len(self._batch_systems)
        self._status_display = f"Exporting {total} system(s)..."
        logger.info(f"Exporting {total} system(s)...")

        # Run export in a thread to avoid blocking the game
        def run_export():
            try:
                self._run_export_flow()
            except Exception as e:
                logger.error(f"Export failed: {e}")
                import traceback
                logger.error(traceback.format_exc())

        thread = threading.Thread(target=run_export, daemon=True)
        thread.start()

    def _run_export_flow(self):
        """
        Run the full export flow with log-based progress.
        No tkinter dialogs - all output goes to the pymhf log window.
        """
        systems_to_export = self._batch_systems.copy()
        total_systems = len(systems_to_export)

        if total_systems == 0:
            logger.warning("[EXPORT] No systems to export!")
            return

        logger.info(f"[EXPORT] Starting export of {total_systems} system(s)...")
        logger.info("")

        # Hard stop: filter out systems with zero/placeholder glyphs before submitting
        # Prevents uploading bad data if coord resolution ever fails silently
        bad_glyph_systems = [s for s in systems_to_export
                             if not s.get('glyph_code') or s.get('glyph_code') == '000000000000']
        if bad_glyph_systems:
            logger.error(f"[EXPORT] ABORTED: {len(bad_glyph_systems)} system(s) have invalid glyphs (000000000000 or empty)")
            logger.error(f"[EXPORT] This usually means coordinate resolution failed — likely an NMS update broke struct offsets.")
            logger.error(f"[EXPORT] Try warping to another system and back, then retry. If it persists, check extractor logs for coord errors.")
            for bad in bad_glyph_systems:
                logger.error(f"[EXPORT]   Dropping: {bad.get('system_name', '?')} (glyph: '{bad.get('glyph_code', '')}')")
            # Drop bad-glyph entries from the persistent batch too — a 000000000000 glyph
            # never dedup-matches a real re-warp, so leaving them in would leak forever.
            _bad_ids = {id(s) for s in bad_glyph_systems}
            self._batch_systems[:] = [s for s in self._batch_systems if id(s) not in _bad_ids]
            systems_to_export = [s for s in systems_to_export
                                 if s.get('glyph_code') and s.get('glyph_code') != '000000000000']
            total_systems = len(systems_to_export)
            if total_systems == 0:
                logger.error("[EXPORT] No valid systems remain after filtering — aborting.")
                return

        # Hard stop: HOLD systems whose galaxy could not be resolved. The backend defaults a
        # missing galaxy to Euclid, so uploading a galaxy-unknown system would silently
        # mislabel it. Holding it (not uploading) is the only place this can be prevented.
        # _held_systems survives the post-upload batch cleanup so the user can re-export
        # once the galaxy resolves (warp out/in).
        galaxy_unknown_systems = [s for s in systems_to_export if not galaxy_is_known(s)]
        self._held_systems = list(galaxy_unknown_systems)
        if galaxy_unknown_systems:
            n_held = len(galaxy_unknown_systems)
            logger.error(f"[EXPORT] HELD: {n_held} system(s) have an unresolved galaxy — NOT uploading them as Euclid.")
            logger.error(f"[EXPORT] Warp out and back into each, or move around the system so NMS populates the galaxy, then re-export.")
            for held in galaxy_unknown_systems:
                logger.error(f"[EXPORT]   Holding: {held.get('system_name', '?')} (glyph: {held.get('glyph_code', '')})")
            # Make the hold OBVIOUS in the GUI Status field — never a silent disappearance.
            self._status_display = f"HELD {n_held}: galaxy unknown - re-warp & re-export (NOT uploaded)"
            held_glyphs = {id(s) for s in galaxy_unknown_systems}
            systems_to_export = [s for s in systems_to_export if id(s) not in held_glyphs]
            total_systems = len(systems_to_export)
            if total_systems == 0:
                logger.error("[EXPORT] No systems with a known galaxy remain — held systems stay in the batch for re-export.")
                return

        # Step 1: Pre-flight duplicate check
        logger.info("[EXPORT] Running pre-flight duplicate check...")
        glyph_codes = [sys.get('glyph_code') for sys in systems_to_export if sys.get('glyph_code')]

        # v1.8.1 (Fix 2): Pass the actual galaxy from the batch so dedup is scoped correctly.
        # The prior default of "Euclid" meant non-Euclid uploads were checked against the wrong
        # galaxy, which could mask duplicates or fabricate false collisions.
        batch_galaxies = {s.get('galaxy_name') for s in systems_to_export if s.get('galaxy_name')}
        if len(batch_galaxies) == 1:
            batch_galaxy = next(iter(batch_galaxies))
        else:
            batch_galaxy = None  # mixed-galaxy batch — backend will per-system scope via payload
            if len(batch_galaxies) > 1:
                logger.info(f"[EXPORT] Batch spans multiple galaxies: {sorted(batch_galaxies)} — dedup check uses per-system galaxy")
        check_result = self._check_duplicates(glyph_codes, galaxy=batch_galaxy)
        if not check_result:
            logger.warning("[EXPORT] Could not verify duplicates with Haven UI")
            logger.info("[EXPORT] Proceeding with export anyway...")
            check_result = {"results": {}, "summary": {"available": len(glyph_codes), "already_charted": 0, "pending_review": 0}}

        summary = check_result.get("summary", {})
        results = check_result.get("results", {})

        charted_count = summary.get("already_charted", 0)
        pending_count = summary.get("pending_review", 0)

        # Show duplicate check results only if there are duplicates
        if charted_count > 0 or pending_count > 0:
            logger.info(f"  Duplicates: {charted_count} charted, {pending_count} pending")
            for glyph, info in results.items():
                status = info.get('status', 'unknown')
                if status == 'already_charted':
                    logger.info(f"    [SKIP] {glyph} — already charted as \"{info.get('system_name', '?')}\"")
                elif status == 'pending_review':
                    logger.info(f"    [UPDATE] {glyph} — pending, will merge")

        # Filter out already_charted systems
        if charted_count > 0:
            systems_to_export = [
                sys for sys in systems_to_export
                if results.get(sys.get('glyph_code'), {}).get('status') != 'already_charted'
            ]

        # Step 3: Upload systems
        if not systems_to_export:
            logger.info("[EXPORT] No new systems to export after filtering duplicates")
            return

        self._upload_systems_to_api_log(systems_to_export)

    def _check_duplicates(self, glyph_codes: list, galaxy: str = None, reality: str = None) -> dict:
        """Check which systems already exist in Haven.
        Uses canonical dedup: last-11 glyph chars + galaxy + reality.
        """
        try:
            url = f"{API_BASE_URL}/api/check_glyph_codes"
            payload = json.dumps({
                "glyph_codes": glyph_codes,
                "galaxy": galaxy or getattr(self, '_current_galaxy', 'Euclid'),
                "reality": reality or getattr(self, '_reality', 'Normal'),
            }).encode('utf-8')

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'X-API-Key': API_KEY,
                'User-Agent': f'HavenExtractor/{self.__version__}',
            })

            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                return json.loads(response.read().decode('utf-8'))

        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")
            return None

    def _upload_systems_to_api_log(self, systems: list):
        """Upload systems to Haven UI API with log-based progress (no tkinter)."""
        total = len(systems)
        results = {"submitted": 0, "skipped": 0, "failed": 0, "errors": []}

        logger.info("")
        logger.info("--- UPLOADING TO HAVEN UI ---")
        logger.info("")

        for i, system in enumerate(systems):
            glyph = system.get('glyph_code', 'Unknown')
            logger.info(f"[{i+1}/{total}] Uploading {glyph}...")

            try:
                # Add user config to system data
                system['discord_username'] = self._discord_username
                system['personal_id'] = self._personal_id
                system['discord_tag'] = self._discord_tag
                system['reality'] = self._reality
                system['game_mode'] = self._game_mode

                result = self._send_single_system_to_api(system)
                if result.get('status') == 'ok':
                    logger.info(f"  [OK] {glyph} - submitted")
                    results["submitted"] += 1
                elif result.get('status') == 'updated':
                    logger.info(f"  [OK] {glyph} - updated")
                    results["submitted"] += 1
                elif result.get('status') == 'already_charted':
                    logger.info(f"  [SKIP] {glyph} - already charted")
                    results["skipped"] += 1
                else:
                    error_msg = result.get('message', 'unknown error')
                    logger.warning(f"  [FAIL] {glyph} - {error_msg}")
                    results["failed"] += 1
                    results["errors"].append(f"{glyph}: {error_msg}")

            except Exception as e:
                logger.error(f"  [FAIL] {glyph} - {str(e)}")
                results["failed"] += 1
                results["errors"].append(f"{glyph}: {str(e)}")

        # Final results
        logger.info("")
        logger.info(f"=== EXPORT: {results['submitted']} submitted, {results['skipped']} skipped, {results['failed']} failed ===")

        # Update status notification
        if results["failed"] > 0:
            errors = results.get("errors", [])
            first_err = errors[0] if errors else "unknown error"
            self._status_display = f"Failed: {first_err}"
        elif results["submitted"] > 0:
            self._status_display = f"Uploaded {results['submitted']} system(s)"
        else:
            self._status_display = "Nothing to upload"

        # Submit procedural region names for any new regions
        if results["submitted"] > 0:
            self._submit_region_names(systems)
            # Keep ONLY held-back entries (galaxy still unknown) so they survive for a later
            # re-export; everything that was a candidate this run (uploaded / charted / failed)
            # is dropped, matching the prior clear-on-submit behaviour.
            held = list(getattr(self, '_held_systems', []))
            self._batch_systems[:] = held
            if held:
                logger.info(f"Uploaded systems cleared; {len(held)} held system(s) remain (resolve their galaxy and re-export).")
                # Keep the hold visible in the GUI even when some systems uploaded fine.
                if results["failed"] == 0:
                    self._status_display = f"Uploaded {results['submitted']}; HELD {len(held)} (galaxy unknown)"
            else:
                logger.info("Batch cleared. Submissions pending admin review.")
        logger.info("")

    def _submit_region_names(self, systems: list):
        """Submit procedural region names for regions that don't have names yet.

        Collects unique regions from the exported systems, checks if each already
        has a name or pending submission, and submits the nms_namegen-generated name
        to the pending_region_names queue for admin approval.
        """
        if not NMS_NAMEGEN_AVAILABLE:
            return

        # Collect unique regions from exported systems
        regions = {}
        for sys in systems:
            region_name = sys.get('region_name', '')
            rx = sys.get('region_x')
            ry = sys.get('region_y')
            rz = sys.get('region_z')
            if not region_name or rx is None or ry is None or rz is None:
                continue
            # Skip placeholder names
            if region_name.startswith('Region_'):
                continue
            key = (rx, ry, rz, sys.get('galaxy_name', 'Euclid'), sys.get('reality', 'Normal'))
            if key not in regions:
                regions[key] = region_name

        if not regions:
            return

        logger.info(f"[REGIONS] Submitting {len(regions)} procedural region name(s)...")

        for (rx, ry, rz, galaxy, reality), name in regions.items():
            try:
                url = f"{API_BASE_URL}/api/regions/{rx}/{ry}/{rz}/submit"
                payload = json.dumps({
                    "proposed_name": name,
                    "submitted_by": self._discord_username or "HavenExtractor",
                    "personal_discord_username": self._discord_username,
                    "discord_tag": self._discord_tag,
                    "reality": reality,
                    "galaxy": galaxy,
                }).encode('utf-8')

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                req = urllib.request.Request(url, data=payload, headers={
                    'Content-Type': 'application/json',
                    'X-API-Key': API_KEY,
                    'User-Agent': f'HavenExtractor/{self.__version__}',
                })

                with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    logger.info(f"  [REGION] '{name}' [{rx},{ry},{rz}] — submitted for approval")

            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read().decode('utf-8'))
                    detail = err.get('detail', f'HTTP {e.code}')
                except Exception:
                    detail = f'HTTP {e.code}'
                # 409 = already named or pending — not an error
                if e.code == 409:
                    logger.info(f"  [REGION] '{name}' [{rx},{ry},{rz}] — {detail}")
                else:
                    logger.warning(f"  [REGION] '{name}' [{rx},{ry},{rz}] — failed: {detail}")
            except Exception as e:
                logger.warning(f"  [REGION] '{name}' [{rx},{ry},{rz}] — failed: {e}")

    def _send_single_system_to_api(self, system: dict) -> dict:
        """Send a single system to the Haven UI API."""
        try:
            url = f"{API_BASE_URL}/api/extraction"
            payload = json.dumps(system, default=str).encode('utf-8')

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'X-API-Key': API_KEY,
                'User-Agent': f'HavenExtractor/{self.__version__}',
            })

            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                return json.loads(response.read().decode('utf-8'))

        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode('utf-8'))
                return error_body
            except:
                return {"status": "error", "message": f"HTTP {e.code}"}

        except Exception as e:
            return {"status": "error", "message": str(e)}

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

        # Cache coordinates for batch mode
        self._current_system_coords = coords
        logger.info(f"Coordinates cached: {coords.get('glyph_code')} in {coords.get('galaxy_name')}")

        # Determine data source based on whether we have captured data
        data_source = "captured_hook" if len(self._captured_planets) > 0 else "memory_read"

        # v1.6.8: Auto-detect game mode before extraction
        self._detect_game_mode()
        logger.info(f"  Game mode: {self._game_mode}")

        extraction = {
            "extraction_time": datetime.now().isoformat(),
            "extractor_version": self.__version__,
            "trigger": trigger,
            "source": "live_extraction",
            "data_source": data_source,
            "captured_planet_count": len(self._captured_planets),
            "discoverer_name": "HavenExtractor",
            "discovery_timestamp": int(datetime.now().timestamp()),
            "game_mode": self._game_mode,
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
        """Get current galactic coordinates. mUniverseAddress primary, player_state secondary, cached tertiary."""
        logger.info("  Resolving coordinates...")

        # Try live resolution first (mUniverseAddress -> player_state)
        coords = self._resolve_current_coordinates()
        if coords:
            return coords

        # Last resort: previously cached coords from on_system_generate
        if self._current_system_coords:
            cached_glyph = self._current_system_coords.get('glyph_code', 'Unknown')
            cached_name = self._current_system_coords.get('system_name', 'Unknown')
            logger.warning(f"  [cached] Falling back to cached coords: '{cached_name}' @ {cached_glyph}")
            return self._current_system_coords

        logger.warning("  All coordinate sources failed - are you in a star system?")
        return None

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
                # No-data signal: INHABITING_RACE raw value outside the valid 0-6 enum range.
                # Only the direct read exposes the raw value; struct access masks it.
                direct_race_raw = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
                if direct_race_raw > 6:
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

        # Fallback to NMS.py struct access if direct read didn't produce a clean value.
        # Star color is always safe to fallback on (separate signal from scan state).
        try:
            if _is_unresolved(result["star_color"]) and hasattr(sys_data, 'Class'):
                raw_star = self._safe_enum(sys_data.Class)
                mapped = STAR_COLOR_MAP.get(raw_star, STAR_COLOR_MAP.get(raw_star.rstrip('_'), None))
                if mapped:
                    result["star_color"] = mapped
                    logger.debug(f"  Star color (struct fallback): raw='{raw_star}' -> '{mapped}'")
        except Exception:
            pass

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

    def _extract_planets(self, solar_system) -> list:
        """Extract planet data - ONLY valid slots based on Planets count."""
        planets = []

        logger.debug("Extracting planets from memory...")

        # Get actual planet count from system data - THIS IS CRITICAL
        # maPlanets is always a 6-slot array, but only first N have valid data
        actual_planet_count = 6  # Default max
        prime_planet_count = 0
        try:
            sys_data = solar_system.mSolarSystemData
            logger.debug(f"  sys_data object: {sys_data}")

            # v1.6.10: Direct-offset reads (resilient to NMS struct shifts that broke
            # sys_data.Planets / sys_data.PrimePlanets after Voyagers)
            sys_data_addr = None
            try:
                sys_data_addr = get_addressof(sys_data)
            except Exception:
                sys_data_addr = None

            if sys_data_addr and sys_data_addr > 0x10000:
                direct_count = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
                direct_prime = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
                if 0 < direct_count <= 6:
                    actual_planet_count = direct_count
                if 0 <= direct_prime <= 6:
                    prime_planet_count = direct_prime
                logger.debug(f"  [DIRECT] Planets={direct_count}, PrimePlanets={direct_prime}")

            # Struct fallback if direct read didn't populate
            if actual_planet_count == 6 and hasattr(sys_data, 'Planets'):
                planets_raw = sys_data.Planets
                fallback_count = self._safe_int(planets_raw)
                if 0 < fallback_count <= 6:
                    actual_planet_count = fallback_count
                    logger.debug(f"  [STRUCT] VALID PLANET COUNT: {actual_planet_count}")

            if prime_planet_count == 0 and hasattr(sys_data, 'PrimePlanets'):
                prime_raw = sys_data.PrimePlanets
                fallback_prime = self._safe_int(prime_raw)
                if 0 <= fallback_prime <= 6:
                    prime_planet_count = fallback_prime
                    logger.debug(f"  [STRUCT] PRIME PLANETS: {prime_planet_count}")
        except Exception as e:
            logger.debug(f"  Could not get planet count: {e}")

        # Calculate expected moons: total - prime = moons
        expected_moons = actual_planet_count - prime_planet_count
        logger.debug(f"  EXPECTED MOONS: {expected_moons}")

        try:
            planets_array = solar_system.maPlanets
            logger.debug(f"  planets_array object: {planets_array}")

            # CRITICAL FIX: Only iterate through VALID planet slots
            # Remaining slots (N to 5) contain default/empty data
            for i in range(min(actual_planet_count, 6)):
                try:
                    planet = planets_array[i]
                    logger.debug(f"  --- Processing slot {i} ---")

                    if planet is None:
                        logger.debug(f"  Slot {i}: None (unexpected for valid slot)")
                        continue

                    planet_addr = get_addressof(planet)
                    if planet_addr == 0:
                        logger.debug(f"  Slot {i}: NULL pointer (unexpected for valid slot)")
                        continue

                    logger.debug(f"  Slot {i}: address 0x{planet_addr:X}")

                    planet_data = self._extract_single_planet(planet, i)
                    if planet_data:
                        planets.append(planet_data)
                        body_type = "MOON" if planet_data.get('is_moon', False) else "PLANET"
                        logger.debug(f"  Slot {i}: [{body_type}] {planet_data.get('planet_name', 'Unknown')} - {planet_data.get('biome', 'Unknown')}")
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

        moon_count = sum(1 for p in planets if p.get('is_moon', False))
        planet_count = len(planets) - moon_count
        logger.debug(f"Extraction done: {planet_count} planets + {moon_count} moons = {len(planets)} total")
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
            # DIRECT MEMORY READ as PRIMARY SOURCE
            # Read from SolarSystemData + 0x1EA0 (planet gen input array)
            # Each planet entry is 0x53 bytes (83 bytes)
            # This is the CORRECT memory location - struct mapping was unreliable!
            # =====================================================
            # v1.6.11: Direct reads from PLANET_GEN_INPUTS only trusted when their mapped
            # enum values are valid. Voyagers shifted the per-slot stride so slots 1-5 give
            # garbage like Unknown(254). When values look valid we use them, otherwise the
            # name-matched captured block below is authoritative.
            direct_data = {}
            if self._cached_sys_data_addr:
                direct_data = self._read_planet_gen_input_direct(self._cached_sys_data_addr, index)

                def _valid_mapped(val):
                    return val and not (isinstance(val, str) and val.startswith("Unknown("))

                if _valid_mapped(direct_data.get("biome")):
                    result["biome"] = direct_data["biome"]
                if _valid_mapped(direct_data.get("planet_size")):
                    result["planet_size"] = direct_data["planet_size"]
                    result["is_moon"] = direct_data.get("is_moon", False)
                if _valid_mapped(direct_data.get("biome_subtype")):
                    result["biome_subtype"] = direct_data["biome_subtype"]
                if direct_data.get("common_resource"):
                    result["common_resource"] = direct_data["common_resource"]
                if direct_data.get("rare_resource"):
                    result["rare_resource"] = direct_data["rare_resource"]
            else:
                logger.warning(f"    [DIRECT] No cached sys_data_addr - cannot use direct memory read!")

            # =====================================================
            # CAPTURED DATA: Use GenerateCreatureRoles hook data
            # NOW INCLUDES BIOME from GenerationData - this is RELIABLE!
            # =====================================================
            # v1.6.11: Look up captured data by PLANET NAME (reliable), not by array index.
            # Read memory planet name first so we can do the lookup.
            memory_name = None
            try:
                pd = getattr(planet, 'mPlanetData', None)
                if pd is not None and hasattr(pd, 'Name'):
                    n = str(pd.Name)
                    if n and n != "None" and len(n.strip()) > 0:
                        memory_name = n.strip()
            except Exception:
                memory_name = None

            captured = None
            if memory_name and memory_name in self._captured_planets:
                captured = self._captured_planets[memory_name]
                result["planet_name"] = memory_name
                logger.debug(f"    [CAPTURED] Name-matched '{memory_name}' for slot {index}")

            if captured is not None:
                # v1.6.11: Restore captured biome/subtype/size/is_moon overrides. These
                # come from GenerationData in the hook and are per-planet correct. The
                # direct memory read at PLANET_GEN_INPUTS had its stride shifted in
                # Voyagers so it garbles slots 1-5 — captured (name-matched) wins.
                if captured.get('biome_raw', -1) >= 0:
                    result["biome"] = captured.get('biome', result["biome"])
                if captured.get('biome_subtype_raw', -1) >= 0:
                    result["biome_subtype"] = captured.get('biome_subtype', result["biome_subtype"])
                if captured.get('planet_size_raw', -1) >= 0:
                    result["planet_size"] = captured.get('planet_size', result["planet_size"])
                    result["is_moon"] = captured.get('is_moon', False)

                # Apply flora/fauna/sentinel - prefer display strings from PlanetInfo
                # (exact game text). Fall back to list-based selection if unavailable.

                # =============================================================
                # v1.4.0: Use _resolve_adjective() for layered text ID resolution
                # Priority: Translate hook cache > PAK/MBIN cache > hardcoded maps > list fallback
                # =============================================================

                # Flora - prefer display string from PlanetInfo.Flora
                flora_display = captured.get('flora_display', '')
                if flora_display:
                    result["flora_level"] = self._resolve_adjective(flora_display, 'flora')
                else:
                    flora_raw = captured.get('flora_raw', -1)
                    result["flora_level"] = self.FLORA_LEVELS.get(flora_raw, captured.get('flora', 'Unknown'))

                fauna_display = captured.get('fauna_display', '')
                if fauna_display:
                    result["fauna_level"] = self._resolve_adjective(fauna_display, 'fauna')
                else:
                    fauna_raw = captured.get('fauna_raw', -1)
                    result["fauna_level"] = self.FAUNA_LEVELS.get(fauna_raw, captured.get('fauna', 'Unknown'))

                sentinel_display = captured.get('sentinel_display', '')
                if sentinel_display:
                    result["sentinel_level"] = self._resolve_adjective(sentinel_display, 'sentinel')
                else:
                    sentinel_raw = captured.get('sentinel_raw', -1)
                    result["sentinel_level"] = self.SENTINEL_LEVELS.get(sentinel_raw, captured.get('sentinel', 'Unknown'))

                # v1.4.2: Store PlanetDescription as informational field (do NOT override biome)
                # PlanetDescription text IDs (DEAD6, TOXIC2, LUSH8, WIRECELLSBIOME1, etc.)
                # are not resolvable and were incorrectly replacing good biome names
                planet_desc = captured.get('planet_description', '')
                if planet_desc:
                    result["planet_description"] = planet_desc
                    logger.debug(f"    [PLANET_DESC] {planet_desc}")

                # v1.4.2: Include planet type classification and extreme weather flag
                planet_type = captured.get('planet_type_display', '')
                if planet_type:
                    result["planet_type"] = planet_type
                if captured.get('is_weather_extreme', False):
                    result["is_weather_extreme"] = True

                # Apply captured resources if not already set from direct read (translate to readable names)
                # v1.4.6: Also trigger when direct read returned empty string (not just "Unknown")
                if result["common_resource"] in ("Unknown", "") and captured.get('common_resource'):
                    result["common_resource"] = translate_resource(captured['common_resource'])
                if captured.get('uncommon_resource'):
                    result["uncommon_resource"] = translate_resource(captured['uncommon_resource'])
                if result["rare_resource"] in ("Unknown", "") and captured.get('rare_resource'):
                    result["rare_resource"] = translate_resource(captured['rare_resource'])

                # v1.4.5: Apply special resource flags from captured hook data
                # These were detected from ExtraResourceHints + HasScrap at capture time
                for flag_key in ["ancient_bones", "salvageable_scrap", "storm_crystals",
                                 "gravitino_balls", "vile_brood", "infested"]:
                    if captured.get(flag_key):
                        result[flag_key] = 1

                # Infested biome subtype
                if result.get("biome_subtype", "").lower() == "infested":
                    result["infested"] = 1
                    result["vile_brood"] = 1

                # Dissonant/Corrupt sentinels
                sentinel_val = result.get("sentinel_level", "")
                if sentinel_val:
                    sentinel_lower = sentinel_val.lower()
                    if any(w in sentinel_lower for w in ["corrupt", "dissonant", "de-harmonis"]):
                        result["dissonance"] = 1

                if any(result.get(k) for k in ["ancient_bones", "salvageable_scrap", "storm_crystals",
                                                 "vile_brood", "gravitino_balls", "infested", "dissonance"]):
                    flags = [k for k in ["ancient_bones", "salvageable_scrap", "storm_crystals",
                                         "vile_brood", "gravitino_balls", "infested", "dissonance"]
                             if result.get(k)]
                    logger.info(f"    [SPECIAL] Detected flags: {', '.join(flags)}")

                # Apply weather - prefer display string from PlanetInfo.Weather
                weather_display = captured.get('weather_display', '')
                if weather_display:
                    resolved = self._resolve_adjective(weather_display, 'weather')
                    result["weather"] = clean_weather_string(resolved)
                elif captured.get('weather'):
                    result["weather"] = clean_weather_string(captured['weather'])

                # v1.6.12: planet_name is already set from memory name at the top of the lookup
                # block (source of truth). Captured planet_name would be identical for name-matched
                # entries, so the redundant assignment is removed.

                logger.debug(f"    [CAPTURED] Applied: flora={result['flora_level']}, fauna={result['fauna_level']}, sentinel={result['sentinel_level']}")
            else:
                # Planet not in captured data - hook didn't fire for this planet
                logger.warning(f"    [NOCAPTURE] Slot {index} name='{memory_name or 'unknown'}' - hook never fired for this planet")

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
                logger.debug(f"    [FALLBACK] Direct read incomplete, trying NMS.py struct mapping...")
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
                                result["is_moon"] = (raw_size == 3)
                                # For moons, use "Small" instead of "Moon" to avoid duplicate badge
                                if result["is_moon"]:
                                    result["planet_size"] = "Small"
                                else:
                                    result["planet_size"] = self._safe_enum(size_val)
                                logger.debug(f"    [FALLBACK] PlanetSize = {result['planet_size']} (raw: {raw_size})")

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
                                logger.debug(f"    [FALLBACK] Biome = {result['biome']} (raw: {raw_biome})")

                        # BiomeSubType
                        if result["biome_subtype"] == "Unknown" and hasattr(gen_input, 'BiomeSubType'):
                            result["biome_subtype"] = self._safe_enum(gen_input.BiomeSubType)
                            logger.debug(f"    [FALLBACK] BiomeSubType = {result['biome_subtype']}")

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

                        # Only use fallback weather if not already set
                        # The captured data has CORRECT adjectives; PlanetInfo.Weather has raw lookup keys
                        if result["weather"] == "Unknown" and hasattr(info, 'Weather'):
                            val = str(info.Weather)
                            if val and val != "None":
                                result["weather"] = clean_weather_string(val)

                        # NOTE: SentinelsPerDifficulty, Flora, Fauna are array types
                        # We already get these from the GenerateCreatureRoles hook
                        # Skip these fallbacks to avoid overwriting good data with object references
                except Exception:
                    pass

                # Resources from PlanetData if not already set - clean and translate to readable names
                # v1.4.6: Also trigger when resource is empty string (not just "Unknown")
                try:
                    if result["common_resource"] in ("Unknown", "") and hasattr(planet_data, 'CommonSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.CommonSubstanceID))
                        if val:
                            result["common_resource"] = translate_resource(val)

                    if hasattr(planet_data, 'UncommonSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.UncommonSubstanceID))
                        if val:
                            result["uncommon_resource"] = translate_resource(val)

                    if result["rare_resource"] in ("Unknown", "") and hasattr(planet_data, 'RareSubstanceID'):
                        val = self._clean_resource_string(str(planet_data.RareSubstanceID))
                        if val:
                            result["rare_resource"] = translate_resource(val)
                except Exception:
                    pass

                # v1.4.6: Read ExtraResourceHints + HasScrap from mPlanetData at EXTRACTION time
                # The hook-based read (applied above via captured flags) fires too early —
                # ExtraResourceHints isn't populated yet during GenerateCreatureRoles.
                # At APPVIEW/extraction time, the data IS fully populated.
                extraction_hints = []
                try:
                    # Method 1: nmspy struct wrapper
                    if hasattr(planet_data, 'ExtraResourceHints'):
                        hints_arr = planet_data.ExtraResourceHints
                        if hints_arr is not None and hasattr(hints_arr, '__len__') and len(hints_arr) > 0:
                            logger.debug(f"    [HINTS-EXTRACT] Struct read: {len(hints_arr)} hints")
                            for hi in range(len(hints_arr)):
                                try:
                                    hint = hints_arr[hi]
                                    if hasattr(hint, 'Hint'):
                                        hint_id = str(hint.Hint) or ""
                                        hint_id = ''.join(c for c in hint_id if c.isprintable() and ord(c) < 128).strip().upper()
                                        if hint_id and len(hint_id) >= 2:
                                            extraction_hints.append(hint_id)
                                            logger.debug(f"    [HINTS-EXTRACT] [{hi}] Hint='{hint_id}'")
                                except Exception:
                                    pass

                    # Method 2: Direct memory read at confirmed offset 0x3310
                    if not extraction_hints:
                        try:
                            pd_addr = get_addressof(planet_data)
                            if pd_addr and pd_addr > 0x10000:
                                arr_ptr = self._read_uint64(pd_addr, 0x3310)
                                arr_count = self._read_uint32(pd_addr, 0x3310 + 8)
                                if arr_ptr and arr_ptr > 0x10000 and 0 < arr_count <= 10:
                                    logger.debug(f"    [HINTS-EXTRACT] Direct read: {arr_count} hints at 0x3310")
                                    for hi in range(arr_count):
                                        elem_addr = arr_ptr + (hi * 32)
                                        hint_str = self._read_string(elem_addr, 0, max_len=16)
                                        if hint_str:
                                            hint_str = hint_str.upper().strip()
                                            extraction_hints.append(hint_str)
                                            logger.debug(f"    [HINTS-EXTRACT] [{hi}] Direct Hint='{hint_str}'")
                                else:
                                    logger.debug(f"    [HINTS-EXTRACT] Direct read at 0x3310: ptr=0x{arr_ptr:X if arr_ptr else 0}, count={arr_count if arr_count else 0} (empty)")
                        except Exception as e:
                            logger.debug(f"    [HINTS-EXTRACT] Direct read failed: {e}")

                    # Apply detected hints to result flags
                    for hint_id in extraction_hints:
                        translated = translate_resource(hint_id)
                        tl = translated.lower() if translated else ""
                        if "ancient bones" in tl or hint_id in ("FOSSIL1", "FOSSIL2", "CREATURE1", "BONES", "ANCIENT", "UI_BONES_HINT"):
                            result["ancient_bones"] = 1
                        if "salvageable scrap" in tl or hint_id in ("SALVAGE", "SALVAGE1", "TECHFRAG", "UI_SCRAP_HINT"):
                            result["salvageable_scrap"] = 1
                        if "storm crystal" in tl or hint_id in ("STORM1", "STORM_CRYSTAL", "UI_STORM_HINT"):
                            result["storm_crystals"] = 1
                        if "gravitino" in tl or hint_id in ("GRAVITINO", "GRAV_BALL", "UI_GRAV_HINT"):
                            result["gravitino_balls"] = 1
                        if "vile brood" in tl or "whispering egg" in tl or hint_id in ("INFESTATION", "VILEBROOD", "LARVA", "LARVAL", "UI_BUGS_HINT"):
                            result["vile_brood"] = 1

                    # HasScrap boolean - read at extraction time (more reliable than hook time)
                    # Also log nearby booleans to verify struct alignment
                    try:
                        pd_addr2 = get_addressof(planet_data)
                        if pd_addr2 and pd_addr2 > 0x10000:
                            has_scrap_byte = self._read_bytes(pd_addr2, 0x39EE, 1)
                            in_abandoned = self._read_bytes(pd_addr2, 0x39EF, 1)
                            in_empty = self._read_bytes(pd_addr2, 0x39F0, 1)
                            in_gas_giant = self._read_bytes(pd_addr2, 0x39F1, 1)
                            hs = bool(has_scrap_byte[0]) if has_scrap_byte else False
                            ab = bool(in_abandoned[0]) if in_abandoned else False
                            em = bool(in_empty[0]) if in_empty else False
                            gg = bool(in_gas_giant[0]) if in_gas_giant else False
                            if hs or ab or em or gg:
                                logger.debug(f"    [HINTS-EXTRACT] Bools@0x39EE: HasScrap={hs}, Abandoned={ab}, Empty={em}, GasGiant={gg}")
                            if hs:
                                result["salvageable_scrap"] = 1
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"    [HINTS-EXTRACT] Failed: {e}")

            # v1.4.5: Derive plant resource from biome type
            # v1.6.6: Only assign if flora actually exists (flora_raw > 0)
            # v1.6.8: Check biome subtype first (Swamp/Lava override parent biome)
            biome = result.get("biome", "Unknown")
            biome_subtype = result.get("biome_subtype", "Unknown")
            plant_resource = BIOME_SUBTYPE_PLANT_OVERRIDE.get(biome_subtype, "")
            if not plant_resource:
                plant_resource = BIOME_PLANT_RESOURCE.get(biome, "")
            if plant_resource:
                flora_raw_val = captured.get('flora_raw', -1) if captured is not None else -1
                if flora_raw_val > 0:
                    result["plant_resource"] = plant_resource
                    logger.debug(f"    [PLANT] {biome}/{biome_subtype} -> {plant_resource} (flora_raw={flora_raw_val})")
                else:
                    logger.debug(f"    [PLANT] Skipped {biome}/{biome_subtype} -> {plant_resource}: flora_raw={flora_raw_val} (no flora)")

            # v1.4.5: Fix dead/airless moon resources - replace hidden SPACEGUNK
            # with Rusted Metal (what the discovery screen actually shows)
            # Check BOTH translated display names AND raw internal IDs
            for res_key in ("common_resource", "uncommon_resource", "rare_resource"):
                res_val = result.get(res_key, "")
                if res_val in HIDDEN_SUBSTANCE_NAMES or res_val in HIDDEN_SUBSTANCE_IDS:
                    result[res_key] = "Rusted Metal"
                    logger.debug(f"    [RESOURCE FIX] {res_key}: '{res_val}' -> 'Rusted Metal'")

            logger.debug(f"    [RESOURCES] common={result.get('common_resource')}, "
                         f"uncommon={result.get('uncommon_resource')}, "
                         f"rare={result.get('rare_resource')}, "
                         f"plant={result.get('plant_resource', 'N/A')}")

            # Log final special flags (includes both hook-captured AND extraction-time flags)
            all_flags = [k for k in ["ancient_bones", "salvageable_scrap", "storm_crystals",
                                     "vile_brood", "gravitino_balls", "infested", "dissonance"]
                         if result.get(k)]
            if all_flags:
                logger.debug(f"    [SPECIAL-FINAL] {', '.join(all_flags)}")

            # Procedural name fallback: if planet name is still a placeholder, generate
            # from the planet seed read during direct memory access
            if result["planet_name"].startswith("Planet_"):
                planet_seed = direct_data.get("planet_seed", 0)
                if planet_seed:
                    proc_name = self._generate_planet_name(planet_seed)
                    if proc_name:
                        result["planet_name"] = proc_name
                        logger.debug(f"    [NAMEGEN] Procedural planet name: '{proc_name}'")

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

    # =========================================================================
    # Coordinate resolution (v1.6.9: mUniverseAddress primary, player_state fallback)
    # =========================================================================
    # player_state.mLocation.GalacticAddress.* broke after NMS Voyagers update
    # (nested struct offsets shifted, all five fields returned 0). mUniverseAddress
    # is a single packed uint64 on mPlanetDiscoveryData — one offset vs. five, so
    # far more resilient to NMS updates.

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

        Raw read of sys_data + 0x1EA0 (PLANET_GEN_INPUTS) + 0x44 (REALITY_INDEX), slot 0.
        Offsets are nmspy-confirmed (PlanetGenerationInputs @0x1EA0, RealityIndex @0x44).
        This is a generation-input scratch buffer; historically it has read 0 even in
        non-Euclid galaxies, which is exactly why it is now only ONE of several voting
        candidates and never authoritative on its own. Returns None on failure/out-of-range
        (0 is returned as 0 — a positively-read value).
        """
        sys_data_addr = None
        try:
            if self._cached_solar_system is not None:
                sys_data = self._cached_solar_system.mSolarSystemData
                sys_data_addr = get_addressof(sys_data)
        except Exception as e:
            logger.debug(f"  [GALAXY] sysdata: failed to get addr: {e}")
        if not sys_data_addr:
            sys_data_addr = self._cached_sys_data_addr
        if not sys_data_addr:
            return None
        try:
            reality_addr_offset = SolarSystemDataOffsets.PLANET_GEN_INPUTS + PlanetGenInputOffsets.REALITY_INDEX
            raw_reality = self._read_int32(sys_data_addr, reality_addr_offset)
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
        Save batch extraction data locally. Each system is written to a separate file.
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
