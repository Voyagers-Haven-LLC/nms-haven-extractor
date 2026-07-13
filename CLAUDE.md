# nms-haven-extractor — Claude context

The in-game NMS mod (PyMHF / NMS.py) that extracts live system data and uploads it to Haven (havenmap.online). **Runs on the player's Windows machine — not on the Pi.**

## ⚠️ This repo is PUBLIC
The client auto-updater fetches `releases/latest` **unauthenticated**, so this repo must stay public. **Never commit operational details** (server IPs, SSH/deploy info, tailnet addresses, keys, org internals) — that context lives in the private super-repo. The `vh_live_...` string in `haven_extractor.py` is the *retired* legacy shared key, kept only to detect pre-v1.5.0 installs that must re-register; it is not a live credential.

## Layout
- `mod/` — the shipping mod code. **This is exactly what goes in the release zip** (minus `star_position_research.py` + `adjective_cache.json`, which are dev/cache extras).
- Root `.bat` files + `_haven_updater_helper.py` + `README.txt` — the distributable's root files (they sit next to `mod/` and an embedded `python/` in the full package).
- `utility_scripts/`, `tests/`, `old_versions/`, `docs/` — dev tooling, smoke tests, history.
- `build_distributable.py` — legacy full-package builder (v8-era; needs a refresh before reuse).

## Distribution (releases ARE the deploy mechanism)
- Mod-only zip: `HavenExtractor-mod-v<X.Y.Z>.zip`, **flat layout** (mod files at zip root — the updater accepts flat or nested `mod/`). Asset name must start with `HavenExtractor-mod-` (that's what `_haven_updater_helper.py` matches).
- Tag = bare semver (`1.10.6`, no `v` prefix — helper strips a leading `v` either way). The updater compares the tag against `__version__` in `mod/haven_extractor.py`.
- Full distributable (~112 MB, embedded Python) is built locally and attached to releases as needed — **never committed**.

## Release checklist
1. Bump `__version__` in `mod/haven_extractor.py` (+ `pyproject.toml`, docs header).
2. Build the flat mod zip from `mod/`.
3. `gh release create <ver> HavenExtractor-mod-v<ver>.zip -R Voyagers-Haven-LLC/nms-haven-extractor`.

## History / migration (July 2026)
- Releases ≤1.10.5 lived on `Parker1920/Master-Haven` (old public monorepo). v1.10.6 moved them here.
- The update zip only overwrites `mod/`, so installed `UPDATE_HAVEN_EXTRACTOR.bat` files couldn't be repointed by an update — instead the mod itself rewrites the sibling `.bat` at startup (`_repoint_updater_bat()`, added v1.10.6).
- A bridge 1.10.6 release was published on the old repo so pre-existing installs pull the self-healing version from the old URL. **Don't archive the old repo** until its release download counts go quiet.

## Key facts
- Adjective resolution: PAK/MBIN disk cache → in-memory Translate hook → raw text ID.
- `nms_namegen/` (vendored, MIT) generates procedural system/region/planet names; requires numpy (updater auto-installs it).
- Per-user API keys: auto-registered on first export via `POST /api/extractor/register`; stored in the user's `haven_config.json` (gitignored).
- `docs/CLAUDE.md` is the deep technical reference (hooks, offsets, payload shapes) — from the monorepo era, paths normalized to this layout.
