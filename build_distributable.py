"""
Build script to create a distributable Haven Extractor package.

This creates a self-contained folder with:
- Embedded Python 3.11
- All dependencies (nmspy, pymhf, etc.)
- Haven Extractor mod files
- Launcher scripts

Your friend can simply extract this folder and run the launcher!
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

# Configuration
PYTHON_VERSION = "3.11.9"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

BUILD_DIR = Path(__file__).parent / "dist" / "HavenExtractor"
EMBED_DIR = BUILD_DIR / "python"
MOD_DIR = BUILD_DIR / "mod"

def download_file(url: str, dest: Path):
    """Download a file from URL to destination."""
    print(f"Downloading: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print(f"  -> Saved to: {dest}")

def extract_zip(zip_path: Path, dest_dir: Path):
    """Extract a zip file."""
    print(f"Extracting: {zip_path} -> {dest_dir}")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(dest_dir)

def setup_embedded_python():
    """Download and setup embedded Python."""
    print("\n=== Setting up Embedded Python ===")

    # Create directories
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    EMBED_DIR.mkdir(parents=True, exist_ok=True)

    # Download Python embedded
    zip_path = BUILD_DIR / "python_embed.zip"
    if not zip_path.exists():
        download_file(PYTHON_EMBED_URL, zip_path)

    # Extract
    extract_zip(zip_path, EMBED_DIR)

    # Enable site-packages by modifying python311._pth
    pth_file = EMBED_DIR / "python311._pth"
    if pth_file.exists():
        print("Enabling site-packages in _pth file...")
        content = pth_file.read_text()
        # Uncomment import site
        content = content.replace("#import site", "import site")
        # Add Lib/site-packages
        if "Lib/site-packages" not in content:
            content += "\nLib/site-packages\n"
        pth_file.write_text(content)

    # Download and run get-pip.py
    print("\nInstalling pip...")
    pip_script = BUILD_DIR / "get-pip.py"
    if not pip_script.exists():
        download_file(PIP_URL, pip_script)

    python_exe = EMBED_DIR / "python.exe"
    subprocess.run([str(python_exe), str(pip_script)], check=True)

    print("Embedded Python setup complete!")

def install_dependencies():
    """Install nmspy and other dependencies."""
    print("\n=== Installing Dependencies ===")

    python_exe = EMBED_DIR / "python.exe"
    pip_exe = EMBED_DIR / "Scripts" / "pip.exe"

    # Use python -m pip if Scripts/pip doesn't exist
    if pip_exe.exists():
        pip_cmd = [str(pip_exe)]
    else:
        pip_cmd = [str(python_exe), "-m", "pip"]

    # Install nmspy (which pulls in pymhf and all dependencies)
    print("Installing nmspy...")
    subprocess.run([*pip_cmd, "install", "nmspy"], check=True)

    # Install requests (for future API integration)
    print("Installing requests...")
    subprocess.run([*pip_cmd, "install", "requests"], check=True)

    print("Dependencies installed!")

def copy_extractor_files():
    """Copy the Haven Extractor mod files."""
    print("\n=== Copying Extractor Files ===")

    MOD_DIR.mkdir(parents=True, exist_ok=True)

    source_dir = Path(__file__).parent

    # Copy main mod files
    files_to_copy = [
        "haven_extractor_mod/haven_extractor.py",
        "haven_extractor_mod/pymhf.toml",
        "haven_extractor_mod/__init__.py",
        "structs.py",
    ]

    for file in files_to_copy:
        src = source_dir / file
        if src.exists():
            dest = MOD_DIR / Path(file).name
            print(f"  Copying: {file}")
            shutil.copy2(src, dest)

    # Copy config example file
    config_example_src = source_dir / "haven_config.json.example"
    if config_example_src.exists():
        config_example_dest = MOD_DIR / "haven_config.json.example"
        print(f"  Copying: haven_config.json.example")
        shutil.copy2(config_example_src, config_example_dest)

    # Create __init__.py if needed
    init_file = MOD_DIR / "__init__.py"
    if not init_file.exists():
        init_file.write_text("")

    # Update pymhf.toml for the distributable
    pymhf_toml = MOD_DIR / "pymhf.toml"
    pymhf_toml.write_text('''[pymhf]
exe = "NMS.exe"
steam_gameid = 275850
start_exe = true
mod_dir = "."
''')

    print("Extractor files copied!")

def create_launcher_scripts():
    """Create launcher batch files and scripts."""
    print("\n=== Creating Launcher Scripts ===")

    # Main launcher batch file
    launcher_bat = BUILD_DIR / "RUN_HAVEN_EXTRACTOR.bat"
    launcher_bat.write_text(r'''@echo off
title Haven Extractor v8.0.0
echo ============================================================
echo   HAVEN EXTRACTOR v8.0.0 - Remote API Sync
echo   For No Man's Sky
echo ============================================================
echo.

REM Change to the script directory
cd /d "%~dp0"

REM Check if Python is available
if not exist "python\python.exe" (
    echo ERROR: Embedded Python not found!
    echo Please ensure the package was extracted correctly.
    pause
    exit /b 1
)

REM API URL is hardcoded in the mod - always enabled!
echo API Config: HARDCODED - havenmap.online
echo Remote sync is enabled by default!
echo.

echo Starting Haven Extractor...
echo.
echo This will:
echo   1. Start No Man's Sky
echo   2. Inject the Haven Extractor mod
echo   3. Extract system data and sync to Haven UI
echo.
echo Press any key to continue or Ctrl+C to cancel...
pause > nul

REM Add embedded Python to PATH so pymhf subprocesses can find it
set "PATH=%~dp0python;%~dp0python\Scripts;%PATH%"

REM Run pymhf by invoking its run function directly (pymhf.exe has hardcoded paths)
cd mod
..\python\python.exe -c "import sys; sys.argv = ['pymhf', 'run', '.']; from pymhf import run; run()"

echo.
echo Haven Extractor has finished.
pause
''')

    # First-time setup script
    setup_bat = BUILD_DIR / "FIRST_TIME_SETUP.bat"
    setup_bat.write_text(r'''@echo off
title Haven Extractor - First Time Setup
echo ============================================================
echo   HAVEN EXTRACTOR - First Time Setup
echo ============================================================
echo.

cd /d "%~dp0"

if not exist "python\python.exe" (
    echo ERROR: Embedded Python not found!
    pause
    exit /b 1
)

echo Verifying installation...
python\python.exe -c "import nmspy; print('nmspy:', nmspy.__version__)"
python\python.exe -c "import pymhf; print('pymhf:', pymhf.__version__)"

echo.
echo If you see version numbers above, the installation is correct!
echo.
echo You can now run RUN_HAVEN_EXTRACTOR.bat to start extracting.
echo.
pause
''')

    # README for the package
    readme = BUILD_DIR / "README.txt"
    readme.write_text('''============================================================
  HAVEN EXTRACTOR v8.0.0 - Remote API Sync
  For No Man's Sky
============================================================

QUICK START:
1. Extract this entire folder to a location of your choice
2. Run "RUN_HAVEN_EXTRACTOR.bat"
3. The game will start with the extractor mod loaded
4. Warp to a solar system
5. Click "Extract Now" in the pyMHF GUI window
6. Data is automatically sent to Haven UI + saved locally!

API SYNC:
Remote sync is ENABLED BY DEFAULT!
The API URL (havenmap.online) is hardcoded in the mod.
No configuration needed - just run and extract!

If you need to use a DIFFERENT API URL:
1. Go to mod/ folder
2. Copy "haven_config.json.example" to "haven_config.json"
3. Edit haven_config.json and set your custom URL:
   {"api_url": "https://your-custom-url.example.com"}

REQUIREMENTS:
- No Man's Sky (Steam version recommended)
- Windows 10/11
- No additional Python installation required!

FILES:
- RUN_HAVEN_EXTRACTOR.bat       - Main launcher (run this!)
- FIRST_TIME_SETUP.bat          - Verify installation
- python/                       - Embedded Python (don't modify)
- mod/                          - Extractor mod files

TROUBLESHOOTING:
- If the game doesn't start, try running as Administrator
- Make sure No Man's Sky is installed via Steam
- Check that antivirus isn't blocking the launcher
- If API sync fails, make sure Haven UI host is accessible at havenmap.online

LOCAL OUTPUT (always saved as backup):
  %USERPROFILE%\Documents\Haven-Extractor\latest.json
  %USERPROFILE%\Documents\Haven-Extractor\extraction_YYYYMMDD_HHMMSS.json

For support, visit: https://github.com/voyagershaven
============================================================
''')

    print("Launcher scripts created!")

def create_cleanup_script():
    """Create script to clean up the zip after extraction."""
    cleanup = BUILD_DIR / "cleanup_temp.bat"
    cleanup.write_text(r'''@echo off
REM Clean up temporary files
if exist "python_embed.zip" del "python_embed.zip"
if exist "get-pip.py" del "get-pip.py"
echo Cleanup complete!
''')

def main():
    print("=" * 60)
    print("  HAVEN EXTRACTOR - Build Distributable Package")
    print("=" * 60)

    # Clean previous build
    if BUILD_DIR.exists():
        print(f"\nRemoving previous build: {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR)

    try:
        setup_embedded_python()
        install_dependencies()
        copy_extractor_files()
        create_launcher_scripts()
        create_cleanup_script()

        # Calculate size
        total_size = sum(f.stat().st_size for f in BUILD_DIR.rglob('*') if f.is_file())
        size_mb = total_size / (1024 * 1024)

        print("\n" + "=" * 60)
        print("  BUILD COMPLETE!")
        print("=" * 60)
        print(f"\nOutput folder: {BUILD_DIR}")
        print(f"Total size: {size_mb:.1f} MB")
        print("\nTo distribute:")
        print("  1. Zip the entire 'HavenExtractor' folder")
        print("  2. Send to your friend")
        print("  3. They extract and run RUN_HAVEN_EXTRACTOR.bat")
        print("=" * 60)

    except Exception as e:
        print(f"\nBUILD FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
