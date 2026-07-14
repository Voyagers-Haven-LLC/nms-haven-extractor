@echo off
REM The title/banner version is read live from mod\haven_extractor.py so this
REM script can never drift from the installed mod.
cd /d "%~dp0"
set "CUR_VER="
if exist "python\python.exe" if exist "%~dp0_haven_updater_helper.py" if exist "mod\haven_extractor.py" for /f "delims=" %%V in ('""python\python.exe" "%~dp0_haven_updater_helper.py" version "mod\haven_extractor.py""') do set "CUR_VER=%%V"
set "VER_LABEL="
if defined CUR_VER set "VER_LABEL= v%CUR_VER%"
title Haven Extractor%VER_LABEL% - First Time Setup
echo ============================================================
echo   HAVEN EXTRACTOR%VER_LABEL% - Installation Verification
echo ============================================================
echo.

echo [1/7] Checking Python installation...
if not exist "python\python.exe" (
    echo ERROR: Embedded Python not found!
    echo Make sure you extracted the ENTIRE folder, not just some files.
    pause
    exit /b 1
)
echo       Python found!

echo.
echo [2/7] Testing Python works...
python\python.exe --version
if errorlevel 1 (
    echo ERROR: Python failed to run!
    pause
    exit /b 1
)

echo.
echo [3/7] Checking mod files...
if not exist "mod\haven_extractor.py" (
    echo ERROR: mod\haven_extractor.py not found!
    pause
    exit /b 1
)
if not exist "mod\pymhf.toml" (
    echo ERROR: mod\pymhf.toml not found!
    pause
    exit /b 1
)
echo       All mod files present!

echo.
echo [4/7] Testing nmspy import...
python\python.exe -c "import nmspy; print('       nmspy version:', nmspy.__version__)"
if errorlevel 1 (
    echo ERROR: nmspy import failed!
    pause
    exit /b 1
)

echo.
echo [5/7] Testing pymhf import...
python\python.exe -c "import pymhf; print('       pymhf version:', pymhf.__version__)"
if errorlevel 1 (
    echo ERROR: pymhf import failed!
    pause
    exit /b 1
)

echo.
echo [6/7] Checking hgpaktool (adjective cache builder)...
python\python.exe -c "from hgpaktool import HGPAKFile; print('       hgpaktool ready!')" 2>nul
if errorlevel 1 (
    echo       hgpaktool not found - installing...
    python\python.exe -m pip install hgpaktool --quiet
    if errorlevel 1 (
        echo       WARNING: hgpaktool install failed. Adjective cache will use bundled data.
    ) else (
        echo       hgpaktool installed successfully!
    )
)

echo.
echo [7/8] Checking numpy (procedural name generation)...
python\python.exe -c "import numpy; print('       numpy version:', numpy.__version__)" 2>nul
if errorlevel 1 (
    echo       numpy not found - installing...
    python\python.exe -m pip install numpy --quiet
    if errorlevel 1 (
        echo       WARNING: numpy install failed. Procedural name generation will be unavailable.
    ) else (
        echo       numpy installed successfully!
    )
)

echo.
echo [8/8] Checking output directory...
python\python.exe -c "import pathlib; p = pathlib.Path.home() / 'Documents' / 'Haven-Extractor'; print('       Output will go to:', p)"

echo.
echo ============================================================
echo   ALL CHECKS PASSED! Installation is correct.
echo ============================================================
echo.
echo You can now run RUN_HAVEN_EXTRACTOR.bat to start extracting.
echo.
echo QUICK GUIDE:
echo   1. Run RUN_HAVEN_EXTRACTOR.bat
echo   2. NMS will start with the mod loaded
echo   3. Warp to systems - data captured automatically!
echo   4. Click "Batch Status" to see collected systems
echo   5. Click "Export to Haven" to upload all data
echo.
pause
