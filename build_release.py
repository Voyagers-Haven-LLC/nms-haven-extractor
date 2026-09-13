"""Extractor 2.0 release packager (EXTRACTOR_2_0.md D10, phase 4).

Replaces the stale v8-era build_distributable.py. Produces the two GitHub
Release assets the website hub + launcher self-update consume:

    HavenExtractor-mod2-v<X.Y.Z>.zip   mod2 contents, flat (the patch zip; the -mod2- name is deliberate: the 1.x updater matches HavenExtractor-mod- with a trailing dash and must never see 2.0 zips)
    HavenExtractor-Full-v<X.Y.Z>.zip   full package: launcher bat + README +
                                       haven.env defaults + mod2/ + python/

Usage:
    py build_release.py --version 2.0.0 [--mod-only]
       [--python-dir "<repo>\\dist\\HavenExtractor\\python"]   (default; dist/ is gitignored)
       [--out dist_out]

The version stamps mod2/haven_extractor2.py's __version__ AND
mod2/sync/client.py's USER_AGENT_VERSION inside the zips (the working tree is
left untouched). 2.1.0: a Full zip is refused unless the embedded python/ carries
exactly the nmspy pinned in mod2/nmspy_pin.py — the framework is the
game-compat surface now, so a Full zip with the wrong one is a broken release.
"""

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
MOD2 = REPO / "mod2"

EXCLUDE_NAMES = {"__pycache__", "logs", "sim_haven.env", "mod2_backup", "tests"}

LAUNCHER_BAT = """@echo off
REM Haven Extractor 2.0 - control it at https://havenmap.online/extractor
setlocal
cd /d "%~dp0"
if not exist "python\\python.exe" ( echo ERROR: run from the HavenExtractor folder. & pause & exit /b 1 )
set "PATH=%~dp0python;%~dp0python\\Scripts;%PATH%"
cd mod2
"..\\python\\python.exe" launcher.py
echo.
pause
"""

README_TXT = """HAVEN EXTRACTOR 2.0
===================

1. Extract this folder anywhere.
2. Run RUN_HAVEN_EXTRACTOR.bat - it checks for updates and starts the game.
3. Open https://havenmap.online/extractor, log in, and hit "Link this PC".
4. Play. Every system you warp to appears on your Batch page automatically -
   rename what you like and submit for review from any device, even your phone.

Used the extractor before 2.0? Claim your account on the same page with the
vh_live_ key from your old Documents\\Haven-Extractor\\config.json.

Your settings live in haven.env next to this file (updates never touch it).
"""

HAVEN_ENV = """# Haven Extractor configuration - survives updates
HAVEN_API_URL=https://havenmap.online
HAVEN_API_KEY=
HAVEN_LOCAL_PORT=8770
"""


def iter_mod2_files():
    for path in sorted(MOD2.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(MOD2)
        if any(part in EXCLUDE_NAMES for part in rel.parts):
            continue
        yield path, rel


def read_pin(name: str = "NMSPY_PIN") -> str:
    text = (MOD2 / "nmspy_pin.py").read_text(encoding="utf-8")
    m = re.search(rf'^{name}\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not m:
        raise SystemExit(f"{name} not found in mod2/nmspy_pin.py")
    return m.group(1)


def embedded_dist_version(python_dir: Path, dist: str):
    """Version of `dist` installed in the embedded python (from its dist-info dir)."""
    site = python_dir / "Lib" / "site-packages"
    for p in site.glob("*.dist-info"):
        stem = p.name[: -len(".dist-info")]
        pkg, _, ver = stem.rpartition("-")
        if pkg.lower().replace("_", "-") == dist.lower():
            return ver
    return None


def stamped(path: Path, version: str) -> bytes:
    data = path.read_bytes()
    if path.name == "haven_extractor2.py":
        text = data.decode("utf-8")
        text = re.sub(r"__version__\s*=\s*['\"][^'\"]+['\"]",
                      f'__version__ = "{version}"', text, count=1)
        text = re.sub(r"USER_AGENT_VERSION\s*=\s*['\"][^'\"]+['\"]",
                      f"USER_AGENT_VERSION = '{version}'", text, count=1)
        data = text.encode("utf-8")
    return data


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_mod_zip(version: str, out: Path) -> Path:
    target = out / f"HavenExtractor-mod2-v{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in iter_mod2_files():
            zf.writestr(str(rel).replace("\\", "/"), stamped(path, version))
    return target


def build_full_zip(version: str, out: Path, python_dir: Path) -> Path:
    if not (python_dir / "python.exe").exists():
        raise SystemExit(f"embedded python not found at {python_dir}")
    pin = read_pin("NMSPY_PIN")
    pymhf_pin = read_pin("PYMHF_PIN")
    have = embedded_dist_version(python_dir, "nmspy")
    have_pymhf = embedded_dist_version(python_dir, "pymhf")
    if have != pin or have_pymhf != pymhf_pin:
        raise SystemExit(
            f"embedded python has nmspy {have} / pymhf {have_pymhf}, but mod2/nmspy_pin.py "
            f"pins nmspy {pin} / pymhf {pymhf_pin}. Refusing to build a Full zip that "
            f"ships the wrong framework. Fix with:\n"
            f'  "{python_dir / "python.exe"}" -m pip install --upgrade '
            f"nmspy=={pin} pymhf=={pymhf_pin}"
        )
    target = out / f"HavenExtractor-Full-v{version}.zip"
    root = "HavenExtractor"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/RUN_HAVEN_EXTRACTOR.bat", LAUNCHER_BAT)
        zf.writestr(f"{root}/README.txt", README_TXT)
        zf.writestr(f"{root}/haven.env", HAVEN_ENV)
        for path, rel in iter_mod2_files():
            zf.writestr(f"{root}/mod2/{str(rel).replace(chr(92), '/')}",
                        stamped(path, version))
        for path in sorted(python_dir.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(python_dir)
            zf.write(path, f"{root}/python/{str(rel).replace(chr(92), '/')}")
    return target


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--mod-only", action="store_true")
    # The player image (embedded Python, ~110 MB, never committed) lives in this
    # repo's ignored dist/ since 2026-09-12 — the old C:\Master-Haven tree is retired.
    ap.add_argument("--python-dir",
                    default=str(REPO / "dist" / "HavenExtractor" / "python"))
    ap.add_argument("--out", default="dist_out")
    args = ap.parse_args()

    out = REPO / args.out
    out.mkdir(exist_ok=True)

    mod_zip = build_mod_zip(args.version, out)
    print(f"built {mod_zip.name}  ({mod_zip.stat().st_size // 1024} KB)")
    print(f"  sha256 {sha256(mod_zip)}")

    if not args.mod_only:
        full_zip = build_full_zip(args.version, out, Path(args.python_dir))
        print(f"built {full_zip.name}  ({full_zip.stat().st_size // (1 << 20)} MB)")
        print(f"  sha256 {sha256(full_zip)}")

    print(f"\nframework pin: nmspy {read_pin('NMSPY_PIN')} / pymhf {read_pin('PYMHF_PIN')} "
          f"(launcher installs it on players' machines)")
    print("\nRelease checklist:")
    mod_path = str(out / f"HavenExtractor-mod2-v{args.version}.zip")
    full_path = "" if args.mod_only else " " + str(out / f"HavenExtractor-Full-v{args.version}.zip")
    print(f"  gh release create {args.version} {mod_path}{full_path}"
          f" -R Voyagers-Haven-LLC/nms-haven-extractor")


if __name__ == "__main__":
    main()
