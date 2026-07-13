# NMS-Haven-Extractor - In-Game Mod

PyMHF-based mod that extracts system and planet data from No Man's Sky in real-time.

> **Version**: 1.10.6 | **Updated**: 2026-05-25
>
> **After making changes to this component:**
> 1. Bump `__version__` in `mod/haven_extractor.py` (PATCH: bug fix, MINOR: feature, MAJOR: breaking)
> 2. Update `pyproject.toml` version to match
> 3. Update `/CLAUDE.md` → "Current Versions" table
> 4. Add changelog entry in `/CLAUDE.md`

## Quick Reference

- **Framework**: PyMHF (Python Modding Hook Framework) + NMS.py
- **Version**: 1.10.6
- **Python**: 3.11-3.12 (NOT 3.14, NOT Windows Store)
- **Output**: `~/Documents/Haven-Extractor/`

## Key Files

| File | Purpose |
|------|---------|
| `mod/haven_extractor.py` | Main mod (game hooks, memory reads, GUI, upload) |
| `mod/extraction_core.py` | **Pure data-transform layer (no pymhf/nmspy): galaxy voter `decide_galaxy()`, payload builders. Smoke-tested by `tests/test_extraction_core.py`.** |
| `mod/nms_language.py` | PSARC/PAK reader, language MBIN parser, adjective cache (429 lines) |
| `mod/structs.py` | Data structures and enum mappings |
| `mod/__init__.py` | Package init |
| `mod/haven_config.json.example` | Configuration template |
| `mod/pymhf.toml` | PyMHF mod configuration |
| `./_haven_updater_helper.py` | Auto-updater JSON/version helper (stdlib only) |
| `./UPDATE_HAVEN_EXTRACTOR.bat` | Auto-updater batch launcher |

## Architecture

```
┌────────────────────────────────────────────────────┐
│              No Man's Sky Process                   │
│  ┌──────────────────────────────────────────────┐  │
│  │           PyMHF Injection                     │  │
│  │  ┌────────────────────────────────────────┐  │  │
│  │  │        Haven Extractor Mod              │  │  │
│  │  │  ┌──────────┐  ┌──────────────────┐   │  │  │
│  │  │  │  Hooks   │  │  Direct Memory   │   │  │  │
│  │  │  │ Generate │  │  Offset Reads    │   │  │  │
│  │  │  │Translate │  │  (ctypes)        │   │  │  │
│  │  │  │CreatRole │  │                  │   │  │  │
│  │  │  └────┬─────┘  └────────┬─────────┘   │  │  │
│  │  │       │                 │              │  │  │
│  │  │       └────────┬────────┘              │  │  │
│  │  │                ▼                       │  │  │
│  │  │         Extraction Data                │  │  │
│  │  └────────────────┬───────────────────────┘  │  │
│  └───────────────────┼──────────────────────────┘  │
└──────────────────────┼─────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
        ▼                             ▼
┌───────────────────┐    ┌────────────────────────┐
│   Local Files     │    │    Haven API           │
│ ~/Documents/      │    │ POST /api/extraction   │
│ Haven-Extractor/  │    │ (per-user API key)     │
│ - latest.json     │    │                        │
│ - extraction_*.json│   │ POST /api/extractor/   │
│ - batch_*.json    │    │   register             │
└───────────────────┘    └────────────────────────┘
```

## Game Hooks

### @nms.cGcSolarSystem.Generate.after
- **Trigger**: After warping to new system
- **Action**: Caches solar system struct pointer, resets scan counter

### @nms.cTkLanguageManagerBase.Translate.after
- **Trigger**: Every time the game translates a text ID to display text
- **Action**: Captures (text_id → display_text) pairs into in-memory cache for adjective resolution
- **Purpose**: Runtime backup layer for adjective resolution when PAK/MBIN cache misses

### @nms.cGcPlanetGenerator.GenerateCreatureRoles.after
- **Trigger**: When game generates creature roles for a planet (during system load)
- **Action**: Captures planet data (biome, weather, flora, fauna, sentinel, resources, special features) and resolves adjectives immediately at capture time via `_resolve_adjective()`
- **Key**: Adjectives are resolved here, not deferred — no manual button needed

### @on_state_change("APPVIEW")
- **Trigger**: Player enters game view
- **Action**: Runs `_auto_refresh_for_export()` to re-resolve adjectives from the now-populated Translate hook cache, then auto-saves current system to batch

## GUI Buttons

| Button | Action |
|--------|--------|
| "Apply Name" | Set custom system name |
| "System Data" | View current extraction data |
| "Batch Status" | View batch collection status |
| "Config Status" | Show configuration details |
| "Export to Haven" | Send data to Haven API |

Note: "Check Planet Data", "Extract Now", "Get Coordinates", "Refresh Adjectives", and "Rebuild Cache" have all been removed.

## Adjective Resolution System

Two-layer system for resolving game text IDs (e.g., `RARITY_HIGH3`) to display names (e.g., `Ample`):

1. **PAK/MBIN disk cache** (primary): `nms_language.py` reads game PAK files, parses language MBINs, builds adjective cache at `~/Documents/Haven-Extractor/adjective_cache.json`. Auto-rebuilds when NMS updates (timestamp-based invalidation).
2. **Runtime Translate hook** (backup): Hooks `cTkLanguageManagerBase.Translate` to capture text_id → display_text pairs during gameplay. Used when PAK cache misses a value.
3. **Fallback**: Raw text_id returned if neither layer resolves.

- Central method: `_resolve_adjective(text_id, field_type)`
- Adjectives resolved at capture time in `on_creature_roles_generate` hook (no manual button needed)
- All hardcoded Layer 3 mapping tables were removed in v1.6.1 (~500 lines)
- Integer enum mappings kept for capture-time: `FLORA_LEVELS`, `FAUNA_LEVELS`, `SENTINEL_LEVELS`, `LIFE_LEVELS`

## nms_language.py Module

Standalone module for reading NMS language data from game files:

- **PSARC/PAK file reader**: Reads HGPAK (.pak) archives using `hgpaktool` package
- **Language MBIN parser**: Extracts text ID → display string mappings from language MBINs
- **Adjective cache builder**: `AdjectiveCacheBuilder` class builds and caches adjective mappings
- **NMS install auto-detection**: Finds NMS install path automatically
- **Cache location**: `~/Documents/Haven-Extractor/adjective_cache.json`
- **Invalidation**: Timestamp-based — rebuilds after NMS game update
- **Dependency**: Requires `hgpaktool` (auto-installed via embedded Python if missing)

## Per-User API Keys

- Auto-registers on first Export via `_register_api_key()` → `POST /api/extractor/register`
- Personal key tied to Discord username, saved to config file
- Old shared API key (`_OLD_SHARED_KEY`) removed from active use — triggers re-registration if found
- Transparent migration: existing users with old shared key auto-register on next Export
- Key stored in `haven_config.json` as `api_key` field

## Dynamic Community List

- Fetches from `/api/communities` on startup via `_fetch_communities_list()`
- Caches locally at `~/Documents/Haven-Extractor/communities_cache.json`
- `CommunityTag` enum built dynamically from server response at module load time
- Falls back to hardcoded defaults if API unreachable and no cache exists
- New communities added via partner dashboard appear in extractor dropdown automatically
- GUI uses `gui_variable.ENUM` decorator for community tag selection

## Auto-Updater

- `UPDATE_HAVEN_EXTRACTOR.bat` + `_haven_updater_helper.py` at repo root
- Checks current version against latest GitHub Release
- Downloads mod-only zip (~60KB) matching `HavenExtractor-mod-*` asset name
- Backs up current `mod/` folder before replacing
- Preserves user config (`haven_config.json`)

## Data Extracted

### System Level
```json
{
  "system_name": "System_XXXX",
  "glyph_code": "XXXXXXXXXXXX",
  "galaxy_name": "Euclid",
  "galaxy_index": 0,
  "star_color": "Yellow|Green|Blue|Red|Purple",
  "economy_type": "Mining|HighTech|Trading|...",
  "economy_strength": "Poor|Average|Wealthy|Pirate",
  "conflict_level": "Low|Default|High|Pirate",
  "dominant_lifeform": "Gek|Vy'keen|Korvax|...",
  "system_seed": 12345,
  "planet_count": 6,
  "voxel_x": -123, "voxel_y": 45, "voxel_z": 678,
  "solar_system_index": 114
}
```

### Per Planet
```json
{
  "planet_index": 0,
  "planet_name": "Planet 1",
  "biome": "Lush|Toxic|Scorched|...",
  "biome_subtype": "Unknown|Infested|...",
  "biome_adjective": "Paradise|Overgrown|Verdant|...",
  "weather": "Humid|Radioactive Storms|...",
  "weather_adjective": "Balmy|Superheated Drizzle|...",
  "sentinel_level": "Low|Standard|High|Aggressive",
  "sentinel_adjective": "Minimal|Regular|High Security|...",
  "flora_level": "None|Sparse|Low|Average|...",
  "flora_adjective": "Copious|Bountiful|Ample|...",
  "fauna_level": "None|Sparse|Low|Average|...",
  "fauna_adjective": "Rich|Generous|Ample|...",
  "common_resource": "Ferrite Dust|Carbon|...",
  "uncommon_resource": "Sodium|Phosphorus|...",
  "rare_resource": "Gold|Silver|...",
  "plant_resource": "Star Bulb|Frost Crystal|...",
  "is_moon": false,
  "planet_size": "Large|Medium|Small|Moon|Giant",
  "ancient_bones": 0,
  "salvageable_scrap": 0,
  "storm_crystals": 0,
  "gravitino_balls": 0,
  "vile_brood": 0,
  "infested": 0,
  "dissonance": 0,
  "exotic_trophy": ""
}
```

## Memory Offset System

Based on NMS 4.13 PDB symbols from Fractal413 debug version:

```python
class SolarSystemDataOffsets:
    PLANETS_COUNT = 0x2264
    PRIME_PLANETS = 0x2268
    STAR_CLASS = 0x224C
    STAR_TYPE = 0x2270
    TRADING_DATA = 0x2240
    CONFLICT_DATA = 0x2250
    INHABITING_RACE = 0x2254
    SEED = 0x21A0
    PLANET_GEN_INPUTS = 0x1EA0

class PlanetGenInputOffsets:
    STRUCT_SIZE = 0x53  # 83 bytes per planet
    BIOME = 0x30
    PLANET_SIZE = 0x40
    COMMON_SUBSTANCE = 0x00
    RARE_SUBSTANCE = 0x10
    # Note: No uncommon offset — UncommonSubstanceID read via NMS.py struct
```

### Hybrid Extraction
1. **Primary**: Direct memory offset reads via ctypes
2. **Fallback**: NMS.py struct access if direct reads fail

## Glyph Encoding

Portal glyph codes use two's complement masking:
- X/Z: signed 12-bit (-2048..+2047) → unsigned 12-bit via `x & 0xFFF`
- Y: signed 8-bit (-128..+127) → unsigned 8-bit via `y & 0xFF`
- System: `system & 0xFFF` (12 bits)
- Planet: `planet & 0xF` (4 bits)
- Format: `{planet:1X}{system:03X}{y:02X}{z:03X}{x:03X}`

Note: Prior to v1.4.6, incorrect `(x + 2047) & 0xFFF` was used — all Method 1 glyph codes before that version had inverted XYZ coordinates.

## Configuration

### haven_config.json
```json
{
  "api_url": "https://havenmap.online",
  "api_key": "vh_user_...",
  "discord_username": "MyDiscordName",
  "personal_id": "",
  "discord_tag": "personal",
  "reality": "Normal"
}
```

### Config Search Order
1. `haven_config.json` in mod directory
2. `%USERPROFILE%\Documents\Haven-Extractor\config.json`
3. Hardcoded defaults

## API Communication

### Endpoints
- `POST {api_url}/api/extraction` — Submit extracted system data
- `POST {api_url}/api/extractor/register` — Register per-user API key
- `GET {api_url}/api/communities` — Fetch community list

### Headers
```
Content-Type: application/json
User-Agent: HavenExtractor/1.6.7
X-API-Key: {api_key}
```

### SSL
Disabled verification for compatibility:
```python
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
```

### Timeout
30 seconds

### Response Format
```json
{
  "status": "ok",
  "message": "Extraction submitted",
  "submission_id": "...",
  "planet_count": 4,
  "moon_count": 1
}
```

## Output Files

Location: `~/Documents/Haven-Extractor/`

| File | Purpose |
|------|---------|
| `latest.json` | Most recent extraction |
| `extraction_YYYYMMDD_HHMMSS.json` | Timestamped backups |
| `batch_YYYYMMDD_HHMMSS.json` | Batch extractions |
| `config.json` | Persisted user config (API key, username, community) |
| `communities_cache.json` | Cached community list from server |
| `adjective_cache.json` | PAK/MBIN adjective mappings cache |

## Enum Mappings

### Biome Types (17)
Lush, Toxic, Scorched, Radioactive, Frozen, Barren, Dead, Weird, Swamp, Lava, Waterworld, GasGiant, etc.

### Planet Sizes (5)
Large, Medium, Small, Moon, Giant

### Star Types (5)
Yellow (0), Green (1), Blue (2), Red (3), Purple (4) — matching `cGcGalaxyStarTypes` game enum order

### Alien Races (7)
Traders (Gek), Warriors (Vy'keen), Explorers (Korvax), Robots, Atlas, Diplomats, None

### Resource Mappings
- **Gas resources**: GAS1=Sulphurine, GAS2=Radon, GAS3=Nitrogen
- **Purple stellar metal**: PURPLE/PURPLE2=Quartzite, EX_PURPLE=Activated Quartzite (Worlds Part II, NOT Indium)
- **Plant resource**: Gated on `flora_level > 0` — planets with no flora do not get a plant resource assigned
- **Resource names**: `RESOURCE_NAMES` dict is hardcoded (not from PAK cache). PAK cache is adjectives only.

## Extraction Flow

1. Player warps to system → `Generate` hook caches pointer
2. Game loads planet data → `on_creature_roles_generate` captures planet info + resolves adjectives immediately
3. Player enters game view → `APPVIEW` handler runs `_auto_refresh_for_export()` then auto-saves to batch
4. Player clicks "Export to Haven" → auto-refreshes adjectives, sends batch to API with per-user key
5. If first export: auto-registers API key via `POST /api/extractor/register`
6. JSON saved locally + sent to API

## Dependencies

```toml
[project]
requires-python = ">=3.11,<3.14"
dependencies = [
    "nmspy>=0.1.0",     # NMS.py framework
    "requests>=2.28.0", # HTTP (optional, using urllib)
    "hgpaktool",        # PAK file reading for adjective cache
]
```

## Common Issues

### Offsets Shifted
After NMS update, offsets may change:
1. Run offset scanner mod
2. Analyze dump files
3. Update offset constants in `haven_extractor.py`

### API Connection Failed
- Check API URL is current (havenmap.online)
- Verify API key is valid (per-user key in config)
- Check Haven server is running

### Adjectives Not Resolving
- PAK cache may need rebuild after NMS update (happens automatically)
- Check `adjective_cache.json` exists and has recent timestamp
- Translate hook cache populates during gameplay — visit planets to warm up
- `hgpaktool` must be installed (auto-installs on first run)

### Data Not Populated
- Planet data captured automatically via `on_creature_roles_generate` hook
- Some fields only populate after the game generates creature roles for each planet
- Check "Batch Status" to see which planets have been captured

## File Structure

```
nms-haven-extractor/
├── mod/                         # Shipping mod code (contents of the release zip)
│   ├── haven_extractor.py       # Main mod (game hooks, memory reads, GUI, upload)
│   ├── extraction_core.py       # Pure data-transform layer (tested)
│   ├── nms_language.py          # PAK reader + adjective cache
│   ├── structs.py               # Data structures
│   ├── nms_namegen/             # Vendored procedural name generator (needs numpy)
│   ├── __init__.py              # Package init
│   ├── haven_config.json.example  # Config template
│   └── pymhf.toml               # Mod config
├── UPDATE_HAVEN_EXTRACTOR.bat   # Auto-updater launcher (ships at distributable root)
├── _haven_updater_helper.py     # Auto-updater helper (stdlib only)
├── RUN_HAVEN_EXTRACTOR.bat      # Distributable launcher
├── FIRST_TIME_SETUP.bat         # Distributable install check
├── README.txt                   # User documentation (ships in distributable)
├── utility_scripts/             # Offset scanners, dump analyzers
├── tests/                       # Smoke tests for extraction_core
├── old_versions/                # Historical mod snapshots
├── build_distributable.py       # Package builder (legacy, needs refresh)
├── pyproject.toml               # Python project
└── README.md                    # Documentation
```
