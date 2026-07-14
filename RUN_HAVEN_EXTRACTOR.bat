@echo off
REM The title/banner version is read live from mod\haven_extractor.py so this
REM launcher can never drift from the mod it actually starts.
cd /d "%~dp0"
set "CUR_VER="
if exist "python\python.exe" if exist "%~dp0_haven_updater_helper.py" if exist "mod\haven_extractor.py" for /f "delims=" %%V in ('""python\python.exe" "%~dp0_haven_updater_helper.py" version "mod\haven_extractor.py""') do set "CUR_VER=%%V"
set "VER_LABEL="
if defined CUR_VER set "VER_LABEL= v%CUR_VER%"
title Haven Extractor%VER_LABEL%
echo ============================================================
echo   HAVEN EXTRACTOR%VER_LABEL% - Batch Mode
echo   For No Man's Sky
echo ============================================================
echo.

REM Check if Python is available
if not exist "python\python.exe" (
    echo ERROR: Embedded Python not found!
    echo Please ensure the package was extracted correctly.
    pause
    exit /b 1
)

REM Pre-flight: ensure numpy is installed (required for procedural name generation).
REM The auto-updater (UPDATE_HAVEN_EXTRACTOR.bat) installs numpy too, but this catches the case
REM where someone copied the folder from a friend or restored from a backup without
REM ever running FIRST_TIME_SETUP.bat or UPDATE_HAVEN_EXTRACTOR.bat.
python\python.exe -c "import numpy" >nul 2>&1
if errorlevel 1 (
    echo Numpy missing - installing for procedural name generation...
    python\python.exe -m pip install numpy --quiet
    if errorlevel 1 (
        echo WARNING: numpy install failed. Procedural name generation will be unavailable.
        echo You can install manually: python\python.exe -m pip install numpy
        echo.
    ) else (
        echo Numpy installed.
        echo.
    )
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
echo   3. Warp to systems - data captured automatically!
echo   4. Click "Export Batch" to save all systems
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
