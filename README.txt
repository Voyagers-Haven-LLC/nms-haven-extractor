============================================================
  HAVEN EXTRACTOR
  For No Man's Sky
============================================================

QUICK START:
1. Extract this entire folder to a location of your choice
2. Run "FIRST_TIME_SETUP.bat" (first time only)
3. Run "RUN_HAVEN_EXTRACTOR.bat" to start NMS with the mod
4. In the mod GUI, enter your Discord Username
5. Pick your Community Tag from the dropdown
6. Warp to solar systems - data is captured automatically!
7. Click "Export to Haven" when ready to upload

UPDATING:
Run "UPDATE_HAVEN_EXTRACTOR.bat" to check for and install
updates. The updater downloads only the mod files (~60 KB),
preserves your config, and backs up the previous version.

IMPORTANT - SYSTEM NAMES:
The extractor captures system data when you warp in, but
the game-generated name (e.g. "System_XXXXXXX") may not be
what you want. Before warping to the next system:

1. Discover/rename the system in-game as usual
2. Type the system name in the "System Name" field in the GUI
3. Click "Apply Name"
4. THEN warp to the next system

If you forget, you can still Apply Name for the current
system before clicking Export - it will update the batch.

BATCH MODE WORKFLOW:
1. Warp to a new system - data captured automatically
2. Apply the system name before warping to the next one
3. Repeat for as many systems as you want
4. Click "Batch Status" to see collected systems
5. Click "Export to Haven" to upload all systems at once
6. Your submissions go to the approval queue for review

API REGISTRATION:
On your first Export, the mod automatically registers your
Discord username with the Haven API and receives a personal
API key. This key is saved to your config and reused for all
future uploads. No manual setup needed.

Your config is saved at:
  %USERPROFILE%\Documents\Haven-Extractor\config.json

COMMUNITY DROPDOWN:
The community tag dropdown is fetched from the Haven server
on each launch. New communities added by partners will appear
automatically. If the server is unreachable, a cached or
default list is used.

GUI FIELDS:
- Discord Username  - Your Discord name (required)
- Community Tag     - Which community you're submitting to
- Reality Mode      - Normal or Permadeath
- System Name       - Enter name, then click "Apply Name"

GUI BUTTONS:
- "Apply Name"      - Apply typed name to current system
- "System Data"     - View captured planet data
- "Batch Status"    - Show collected systems count
- "Config Status"   - Verify your settings
- "Export to Haven"  - Upload all systems to Haven

DATA CAPTURED PER SYSTEM:
- Star color, economy type, economy strength
- Conflict level, dominant lifeform
- Spectral class, planet count

DATA CAPTURED PER PLANET:
- Biome, biome subtype, weather
- Flora, fauna, sentinel levels
- Resources/materials
- Planet size, planet name
- Special features (Ancient Bones, Storm Crystals, etc.)
- Moon detection

REQUIREMENTS:
- No Man's Sky (Steam version)
- Windows 10/11
- No additional Python installation required!

FILES:
- RUN_HAVEN_EXTRACTOR.bat      - Main launcher (run this!)
- FIRST_TIME_SETUP.bat         - Verify installation (first time)
- UPDATE_HAVEN_EXTRACTOR.bat   - Check for and install updates
- python/                      - Embedded Python (don't modify)
- mod/                         - Extractor mod files

TROUBLESHOOTING:
- If the game doesn't start, try running as Administrator
- Make sure No Man's Sky is installed via Steam
- Check that antivirus isn't blocking the launcher
- If API connection fails, check your internet connection
- Config saved at: %USERPROFILE%\Documents\Haven-Extractor\

Haven Map: https://havenmap.online
============================================================
