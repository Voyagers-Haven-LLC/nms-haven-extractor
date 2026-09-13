# Haven Extractor

In-game mod for No Man's Sky that captures the systems you warp to and stages
them to [havenmap.online](https://havenmap.online) for review. Built on
[pyMHF](https://github.com/monkeyman192/pyMHF) and
[NMSpy](https://github.com/monkeyman192/NMS.py). Every struct read goes through
NMSpy's generated classes; there are no hand-maintained memory offsets.

> Releases up to 1.10.5 were published on the old `Parker1920/Master-Haven`
> repo. From 1.10.6 they live here. The client auto-updater fetches
> `releases/latest` unauthenticated, so this repo stays public.
> **Never commit operational details or keys.**

## Layout

The repo root mirrors a player install.

```
README.md                    this file
RUN_HAVEN_EXTRACTOR.bat      update check -> framework check -> start the game with the mod
UPDATE_HAVEN_EXTRACTOR.bat   the update and framework steps only, no game
haven.env                    per-machine config + key (gitignored; players have the same file)
mod/                         THE mod. Exactly what the patch zip contains.
  haven_extractor2.py        entry: hooks, readiness gate, staging, heartbeat
  launcher.py                runs before injection: self-update, framework pin, pymhf run
  nmspy_pin.py               the ONE place that says which nmspy/pymhf build we are verified against
  capture/ resolve/ payload/ language/ sync/ api_local/ telemetry/ config/ nms_namegen/
  tests/                     headless tests + simulate.py + tools/ (never ship)
dist/                        the build distributable
  build_release.py           packager (writes the two release zips here)
  python/                    embedded Python 3.11 image with the pinned framework (gitignored)
```

Players get `RUN_HAVEN_EXTRACTOR.bat`, `UPDATE_HAVEN_EXTRACTOR.bat`, `README.txt`,
`haven.env`, `mod/` and `python/` side by side. The repo-root bats are the same
three lines with `dist\python` in place of the sibling `python\`.

## Run it from this checkout

`RUN_HAVEN_EXTRACTOR.bat`. It needs the embedded Python image at
`dist\python\` (build it once from any Full release zip, or copy an existing
install's `python\` folder). If the game is already running, pyMHF injects into
it; otherwise it launches the game through Steam.

What you should see in the console and on the website Terminal:

- `Framework OK: nmspy <pin> / pymhf <pin>` from the launcher.
- `READY — hooks 3/3 bound, nmspy <pin> == pin` after the mode selector.
- `Staged to Haven: <system> (Np+Mm)` after a warp.

`MOD BROKEN — hook(s) failed to bind`, `FRAMEWORK MISMATCH` or `REFUSED
upload` are the gate doing its job: the game build, the framework or a struct
read is off, and nothing is uploaded until it is fixed.

## Tests

```
py mod/tests/test_extraction_core.py      payload builders + galaxy voter
py mod/tests/test_capture_fixes.py        struct read semantics, snapshot identity gate
py mod/tests/test_sanity_gate.py          upload sanity gate
py mod/tests/test_readiness_gate.py       hook registry check
py mod/tests/test_framework_pin.py        pin + launcher order
py mod/tests/test_vocab_mappings.py       enum labels vs haven-ui's option catalog
py mod/tests/test_payload_and_merge.py    needs the sibling haven-ui checkout
```

`test_struct_read_nmspy.py` parses a synthetic `cGcSolarSystemData` buffer
through the real NMSpy struct and must run with the pinned interpreter:
`set PYTEST_VERSION=1 && dist\python\python.exe mod\tests\test_struct_read_nmspy.py`.
Every other test runs with any Python 3.11+; none need the game.

## Release

1. Bump `__version__` in `mod/haven_extractor2.py` (the packager stamps
   `mod/sync/client.py` to match).
2. `py dist/build_release.py --version X.Y.Z` (add `--mod-only` to skip the
   110 MB Full zip). The Full build refuses to run unless `dist/python` carries
   exactly the nmspy/pymhf pinned in `mod/nmspy_pin.py`.
3. `gh release create X.Y.Z dist/HavenExtractor-mod2-vX.Y.Z.zip dist/HavenExtractor-Full-vX.Y.Z.zip -R Voyagers-Haven-LLC/nms-haven-extractor`

The patch asset is named `HavenExtractor-mod2-v…` on purpose even though the
folder is `mod/`: havenmap.online's latest-release endpoint looks for that
prefix, and the 1.x updater still in the wild matches `HavenExtractor-mod-`
with a trailing dash and must never see a 2.x zip. Tag is bare semver, no `v`.

## After a game patch

1. Wait for the matching NMSpy release on PyPI (its version is the NMS build id).
2. Set `NMSPY_PIN` (and `PYMHF_PIN` if needed) in `mod/nmspy_pin.py`.
3. `dist\python\python.exe -m pip install --upgrade nmspy==<pin> pymhf==<pin>`,
   run the tests above with the pinned interpreter, capture one known system
   in-game and diff it against its havenmap row.
4. Release. The launcher installs the new pin on every player's machine on
   their next start; until then their mod holds uploads instead of guessing.
