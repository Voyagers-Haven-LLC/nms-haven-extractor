@echo off
echo ==========================================
echo  pyMHF + NMSpy Setup for Haven Extractor
echo ==========================================
echo.

set PYTHON_HOME=C:\Users\parke\AppData\Local\Programs\Python\Python311
set PYMHF_EXE=%PYTHON_HOME%\Scripts\pymhf.exe
set PYMHF_DIR=%USERPROFILE%\.pymhf

echo Step 1: Creating pyMHF directories...
if not exist "%PYMHF_DIR%" mkdir "%PYMHF_DIR%"
if not exist "%PYMHF_DIR%\mods" mkdir "%PYMHF_DIR%\mods"

echo Step 2: Initializing pyMHF for NMS...
echo.
echo You need to run: pymhf init NMS
echo This will ask for your NMS installation path.
echo.
echo Common NMS paths:
echo   Steam: C:\Program Files (x86)\Steam\steamapps\common\No Man's Sky
echo   GOG:   C:\GOG Games\No Man's Sky
echo.
echo ==========================================
echo  IMPORTANT: Run this in cmd.exe
echo ==========================================
echo.
echo Open Command Prompt (cmd.exe) and run:
echo.
echo   "%PYMHF_EXE%" init NMS
echo.
echo When prompted, enter your NMS game folder path.
echo.
echo After init completes, run:
echo.
echo   "%PYMHF_EXE%" run NMS
echo.
echo ==========================================
pause
