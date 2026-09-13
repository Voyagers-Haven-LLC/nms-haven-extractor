"""Haven Extractor 2.0 launcher (EXTRACTOR_2_0.md D11, locked 2026-08-07).

Runs BEFORE injection: update-check -> one-tap apply -> launch the game.
Replaces UPDATE_HAVEN_EXTRACTOR.bat, FIRST_TIME_SETUP.bat, and the byte-length
.bat self-heal machinery entirely. Lives inside mod2/ so updates update it;
the root .bat stays a dumb three-liner forever.

Update source: the same /api/extractor/latest the website download page uses
(cached GitHub Releases proxy) — one endpoint, one truth.

2.1.0: also keeps the FRAMEWORK current. The patch zip only ever replaced mod2/;
the embedded python/ (where nmspy lives) was frozen at whatever the Full zip
shipped. Now that every struct read goes through nmspy's generated classes,
the framework version IS the game-compat surface, so after the mod update this
launcher pip-installs exactly the nmspy/pymhf pinned in mod2/nmspy_pin.py
(which the patch zip carries) into the embedded Python before injecting.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MOD_DIR = Path(__file__).resolve().parent
ROOT = MOD_DIR.parent

sys.path.insert(0, str(MOD_DIR))
from config.env_file import load_env  # noqa: E402


def current_version() -> str:
    try:
        text = (MOD_DIR / "haven_extractor2.py").read_text(encoding="utf-8")
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


def vtuple(v: str):
    base = v.split("-")[0].lstrip("v")
    try:
        return tuple(int(x) for x in base.split("."))
    except ValueError:
        return (0, 0, 0)


def fetch_latest(api_url: str):
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/extractor/latest",
        headers={"User-Agent": "HavenExtractor-Launcher"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def apply_update(zip_url: str, version: str) -> bool:
    print(f"  downloading v{version} ...")
    tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
    try:
        urllib.request.urlretrieve(zip_url, tmp)
        if tmp.stat().st_size < 10_000:
            print("  download looks wrong (too small) — skipping update")
            return False
        backup = ROOT / "mod2_backup"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(MOD_DIR, backup,
                        ignore=shutil.ignore_patterns("__pycache__", "logs"))
        with zipfile.ZipFile(tmp) as zf:
            names = zf.namelist()
            # Accept flat contents or a single nested mod2/ folder
            prefix = ""
            if all(n.startswith("mod2/") for n in names if not n.endswith("/")):
                prefix = "mod2/"
            for n in names:
                if n.endswith("/") or not n.startswith(prefix):
                    continue
                target = MOD_DIR / n[len(prefix):]
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        print(f"  updated to v{version} (previous kept in mod2_backup)")
        return True
    except Exception as e:
        print(f"  update failed ({e}) — launching current version")
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def ensure_framework() -> bool:
    """Bring the embedded Python's nmspy/pymhf to the pinned versions.

    Runs AFTER the mod update so a freshly downloaded mod2/nmspy_pin.py is what
    we read. Uses the interpreter we are running under (python\python.exe),
    which ships pip. Failure is loud but non-fatal here: the mod itself refuses
    to upload while the mismatch stands, so nothing wrong reaches Haven.
    """
    try:
        import nmspy_pin
        importlib.reload(nmspy_pin)  # the update step may have replaced the file
    except Exception as e:
        print(f"  (framework pin unreadable: {e} — skipping framework check)")
        return True
    installed = nmspy_pin.installed_versions()
    needs = nmspy_pin.framework_needs(installed)
    if not needs:
        print(f"Framework OK: nmspy {installed.get('nmspy')} / pymhf {installed.get('pymhf')}")
        return True
    print(f"Framework update needed: {', '.join(needs)} "
          f"(installed: nmspy {installed.get('nmspy')}, pymhf {installed.get('pymhf')})")
    print("  installing into the embedded Python (this needs internet, ~30s) ...")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-input",
           "--disable-pip-version-check", *needs]
    try:
        result = subprocess.run(cmd, timeout=900)
    except Exception as e:
        print(f"  WARNING: framework update could not run ({e}).")
        result = None
    if result is None or result.returncode != 0:
        print("  WARNING: framework update FAILED. The extractor will load but HOLD every "
              "capture until nmspy matches the pin. Check your internet connection and "
              "run this launcher again.")
        return False
    print("  framework updated.")
    return True


def main():
    env = load_env(str(ROOT / "haven.env"))
    api_url = env.get("HAVEN_API_URL", "https://havenmap.online")
    cur = current_version()
    print(f"Haven Extractor v{cur}")

    try:
        latest = fetch_latest(api_url)
        latest_v = latest.get("version") or ""
        if latest_v and vtuple(latest_v) > vtuple(cur) and latest.get("patch_zip_url"):
            print(f"Update available: v{cur} -> v{latest_v}")
            if (ROOT / ".git").exists():
                # Dev checkout: mod2/ is the working tree. Never let a release zip
                # overwrite it — pull with git instead.
                print("  (git checkout detected — not applying the release zip over mod2/)")
            else:
                try:
                    answer = input("Update now? [Y/n] ").strip().lower()
                except EOFError:
                    answer = "n"
                if answer in ("", "y", "yes"):
                    apply_update(latest["patch_zip_url"], latest_v)
    except Exception as e:
        print(f"(update check skipped: {e})")

    try:
        ensure_framework()
    except Exception as e:
        print(f"(framework check skipped: {e})")

    print("Launching No Man's Sky ...")
    os.chdir(MOD_DIR)
    sys.argv = ["pymhf", "run", "."]
    from pymhf import run
    run()


if __name__ == "__main__":
    main()
