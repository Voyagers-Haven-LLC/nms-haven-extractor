# Haven Extractor (nms-haven-extractor)

The in-game No Man's Sky mod that extracts live system/planet data and uploads it to [Haven](https://havenmap.online).

## For players

- **Install**: download the full HavenExtractor package (embedded Python included), extract anywhere, run `RUN_HAVEN_EXTRACTOR.bat`.
- **Update**: run `UPDATE_HAVEN_EXTRACTOR.bat` — it fetches the latest mod zip (~60 KB) from this repo's [Releases](https://github.com/Voyagers-Haven-LLC/nms-haven-extractor/releases), preserves your config, and backs up the previous version.
- Full usage instructions ship in `README.txt` inside the package.

> Releases up to 1.10.5 were published on the old `Parker1920/Master-Haven` repo. From 1.10.6 they live here; existing installs migrate automatically on their next update + game launch.

## Stack
Python mod running under **PyMHF / NMS.py** (hooks into `NMS.exe`). Runs **in-game on the player's Windows machine**, not on a server.

## Repo layout
- `mod/` — the shipping mod code (contents of the release zip)
- root `.bat` files + `_haven_updater_helper.py` — the distributable's launcher/updater scripts
- `utility_scripts/` — offset scanners / dump analyzers
- `tests/` — smoke tests for the pure extraction core
- `docs/` — technical reference (hooks, memory offsets, payload shapes)

> ⚠️ This repo stays **public** — the client auto-updater fetches `releases/latest` unauthenticated.
