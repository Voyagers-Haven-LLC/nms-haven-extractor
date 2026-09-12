"""Framework pin — the ONE place that says which NMSpy build this extractor was
verified against.

NMSpy's package version IS the No Man's Sky build id (178994.0 == the Cosmos
7.01 Steam build). The generated structs inside it (nmspy.data.exported_types)
are only correct for that build, and since 2.1.0 every struct read in this mod
goes through those generated classes — there are no hand-maintained offsets
left to "fix" after a game update. So correctness == "installed nmspy matches
the game", and the pin below is how we make a mismatch LOUD instead of silent:

  * launcher.py upgrades the embedded Python to exactly this pin before the
    game starts (the mod2 patch zip carries this file, so bumping the pin in a
    release is enough to roll the framework to every player);
  * haven_extractor2.py refuses to stage captures while the installed version
    differs from the pin (readiness gate + upload hold);
  * build_release.py refuses to build a Full zip whose embedded Python carries a
    different nmspy than the pin.

Bumping after a game patch: wait for monkeyman192's matching nmspy release
(https://pypi.org/project/nmspy/), set NMSPY_PIN to it, run the smoke tests
against a real capture, release.
"""

NMSPY_PIN = "178994.0"
PYMHF_PIN = "0.2.4"

PINS = {"nmspy": NMSPY_PIN, "pymhf": PYMHF_PIN}


def installed_version(dist_name: str):
    """Version string of an installed distribution, or None when absent."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - py<3.8
        return None
    try:
        return version(dist_name)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def installed_versions() -> dict:
    return {name: installed_version(name) for name in PINS}


def framework_needs(installed: dict = None, pins: dict = None) -> list:
    """Return pip requirement strings for every pinned dist whose installed
    version differs from its pin (missing counts as different). Empty == OK."""
    installed = installed_versions() if installed is None else installed
    pins = PINS if pins is None else pins
    needs = []
    for name, pin in pins.items():
        if (installed.get(name) or "") != pin:
            needs.append(f"{name}=={pin}")
    return needs


def framework_status() -> tuple:
    """(ok, detail) — detail is a flat dict suitable for state.deps_detail."""
    installed = installed_versions()
    needs = framework_needs(installed)
    detail = {
        "nmspy": installed.get("nmspy"),
        "nmspy_pin": NMSPY_PIN,
        "pymhf": installed.get("pymhf"),
        "pymhf_pin": PYMHF_PIN,
        "framework_ok": not needs,
        "framework_needs": needs,
    }
    return (not needs, detail)
