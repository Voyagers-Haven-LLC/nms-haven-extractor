@echo off
echo ==========================================
echo  Haven Extractor - Offset Scanner Setup
echo ==========================================
echo.

set PYTHON_HOME=C:\Users\parke\AppData\Local\Programs\Python\Python311
set PYMHF_EXE=%PYTHON_HOME%\Scripts\pymhf.exe

REM NMSpy mod directory (from pymhf.toml)
set "NMS_MOD_DIR=C:\Program Files (x86)\Steam\steamapps\common\No Man's Sky\GAMEDATA\MODS"

echo Checking pymhf...
if not exist "%PYMHF_EXE%" (
    echo ERROR: pymhf.exe not found at:
    echo   %PYMHF_EXE%
    echo.
    pause
    exit /b 1
)
echo Found: %PYMHF_EXE%
echo.

echo Checking NMS mod folder...
if not exist "%NMS_MOD_DIR%" (
    echo ERROR: NMS mod folder not found at:
    echo   %NMS_MOD_DIR%
    echo.
    echo If your NMS is installed elsewhere, edit this batch file.
    pause
    exit /b 1
)
echo Found: %NMS_MOD_DIR%
echo.

echo Checking scanner source file...
if not exist "%~dp0offset_scanner.py" (
    echo ERROR: offset_scanner.py not found in:
    echo   %~dp0
    echo.
    pause
    exit /b 1
)
echo Found: %~dp0offset_scanner.py
echo.

echo Copying offset_scanner.py to NMS mod folder...
echo From: %~dp0offset_scanner.py
echo To:   %NMS_MOD_DIR%\offset_scanner.py
echo.

copy /Y "%~dp0offset_scanner.py" "%NMS_MOD_DIR%\offset_scanner.py"
if errorlevel 1 (
    echo.
    echo ERROR: Copy failed!
    echo Try running this batch file as Administrator.
    echo Right-click the .bat file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo  SUCCESS! Scanner mod installed.
echo ==========================================
echo.
echo Output will be saved to:
echo   %USERPROFILE%\Documents\Haven-Extractor\offset_scans\
echo.
echo To run NMS with pyMHF, open cmd.exe and run:
echo   "%PYMHF_EXE%" run nmspy
echo.
echo ==========================================
pause
