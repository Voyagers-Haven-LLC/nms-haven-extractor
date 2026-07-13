# NMS Haven Extractor

A live game mod for No Man's Sky that extracts planet and system data for Voyagers Haven.

## How It Works

This mod uses [NMS.py](https://github.com/monkeyman192/NMS.py) to hook into the running game and extract system/planet data when you enter a new solar system. The data is written to JSON files that the Haven Watcher can monitor and submit to the Control Room.

## Requirements

- Windows 10/11
- Python 3.11 or 3.12 (NOT 3.14, NOT Windows Store version)
- No Man's Sky (Steam version recommended)
- [NMS.py](https://github.com/monkeyman192/NMS.py) and [pyMHF](https://github.com/monkeyman192/pyMHF)

## Installation

### 1. Install Python

Download Python 3.11 or 3.12 from [python.org](https://www.python.org/downloads/). **Do NOT use the Windows Store version.**

During installation, check "Add Python to PATH".

### 2. Install NMS.py

Open a terminal and run:

```bash
pip install nmspy
```

This will also install pyMHF and all dependencies.

### 3. Configure pyMHF

First time running, pyMHF will ask for configuration:

```bash
pymhf run nmspy
```

When prompted:
- **Binary name**: `NMS.exe`
- **Mods folder**: Point to your NMS `GAMEDATA\MODS` folder (e.g., `C:\Games\No Man's Sky\GAMEDATA\MODS`)

### 4. Install Haven Extractor

Copy the Haven Extractor files to your mods folder:

```
GAMEDATA\MODS\
├── haven_extractor.py
├── structs.py
└── extraction_watcher.py (optional, for testing)
```

Or run it as a standalone mod:

```bash
pymhf run path\to\nms-haven-extractor\mod\haven_extractor.py
```

## Usage

### Running the Mod

1. Start No Man's Sky
2. In a terminal, run:
   ```bash
   pymhf run nmspy
   ```
3. The pyMHF GUI will appear showing your mods
4. Play the game - when you enter a new system, data will be extracted

### Output Location

Extracted data is written to:
```
%USERPROFILE%\Documents\Haven-Extractor\
├── latest.json          # Most recent extraction
└── extraction_*.json    # Historical extractions
```

You can change this by setting the `HAVEN_EXTRACTOR_OUTPUT` environment variable.

### Example Output

```json
{
  "system_name": "Eissentam Alpha",
  "galaxy_name": "Eissentam",
  "star_type": "Yellow",
  "economy_type": "Mining",
  "economy_strength": "High",
  "conflict_level": "Low",
  "dominant_lifeform": "Gek",
  "planet_count": 4,
  "planets": [
    {
      "planet_index": 0,
      "planet_name": "Verdant Prime",
      "biome": "Lush",
      "weather": "Humid",
      "sentinel_level": "Low",
      "flora_level": "Bountiful",
      "fauna_level": "Generous",
      "common_resource": "Ferrite Dust",
      "uncommon_resource": "Sodium",
      "rare_resource": "Activated Copper"
    }
  ]
}
```

## Integration with Haven Watcher

The `extraction_watcher.py` module can be integrated into the main NMS-Save-Watcher:

```python
from extraction_watcher import ExtractionWatcher, convert_extraction_to_haven_payload

def on_extraction(data):
    payload = convert_extraction_to_haven_payload(data)
    # Submit to Haven Control Room API
    api_client.submit_system(payload)

watcher = ExtractionWatcher(callback=on_extraction)
watcher.start()
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'nmspy'"

Make sure you installed nmspy:
```bash
pip install nmspy
```

### Mod doesn't load

Check that:
1. NMS is running
2. pyMHF is configured correctly
3. Python is from python.org (not Windows Store)

### No data being extracted

Check the pyMHF console for errors. The mod may need updating if NMS was recently patched - NMS.py hooks can break with game updates.

### Data is incomplete

Some fields may be "Unknown" if:
- The game hasn't fully loaded the system yet
- The field names in the game have changed
- We're reading from the wrong memory offset

## Development

### Project Structure

```
NMS-Haven-Extractor/
├── haven_extractor.py    # Main pyMHF mod
├── structs.py            # Data structures and helpers
├── extraction_watcher.py # File watcher for integration
├── pyproject.toml        # Project configuration
└── README.md
```

### Updating for NMS Patches

When NMS updates, you may need to:

1. Wait for NMS.py to update their patterns (`tools/data.json`)
2. Check if struct field names/offsets changed in MBINCompiler
3. Test that hooks still trigger correctly

### Adding New Data Fields

1. Find the struct field in [MBINCompiler](https://github.com/monkeyman192/MBINCompiler)
2. Add it to `structs.py`
3. Extract it in `haven_extractor.py`

## License

MIT License - Based on NMS.py which is also MIT licensed.

## Credits

- [NMS.py](https://github.com/monkeyman192/NMS.py) by monkeyman192
- [pyMHF](https://github.com/monkeyman192/pyMHF) by monkeyman192
- [MBINCompiler](https://github.com/monkeyman192/MBINCompiler) for struct definitions
