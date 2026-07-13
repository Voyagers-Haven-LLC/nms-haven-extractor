@echo off
echo ==========================================
echo  Haven Extractor - Dump Analyzer
echo ==========================================
echo.

set SCAN_DIR=%USERPROFILE%\Documents\Haven-Extractor\offset_scans

if not exist "%SCAN_DIR%" (
    echo ERROR: No scan directory found at:
    echo   %SCAN_DIR%
    echo.
    echo Run RUN_SCANNER.bat first and scan a system in-game.
    echo.
    pause
    exit /b 1
)

echo Scan directory: %SCAN_DIR%
echo.

REM Check for .bin files
dir /b "%SCAN_DIR%\*.bin" >nul 2>&1
if errorlevel 1 (
    echo No .bin dump files found!
    echo.
    echo Run RUN_SCANNER.bat first, then enter a solar system in NMS.
    echo.
    pause
    exit /b 1
)

echo Found dump files:
echo -----------------
dir /b "%SCAN_DIR%\*.bin"
echo.

echo Running analyzer...
echo.

python "%~dp0analyze_dump.py"

echo.
echo ==========================================
echo Analysis complete! Check the output above.
echo ==========================================
echo.
pause
