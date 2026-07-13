@echo off
setlocal enabledelayedexpansion
title Haven Extractor - Update Checker
cd /d "%~dp0"

REM Always use Windows' native tools by absolute path. PATH lookup can find
REM GNU/Cygwin/Git versions first, which behave differently or don't support
REM the features we need (tar with no zip support; find with different flags).
set "TAR_EXE=%SystemRoot%\System32\tar.exe"
set "FINDSTR_EXE=%SystemRoot%\System32\findstr.exe"
set "FIND_EXE=%SystemRoot%\System32\find.exe"
set "TASKLIST_EXE=%SystemRoot%\System32\tasklist.exe"

echo ============================================================
echo   HAVEN EXTRACTOR - Update Checker
echo ============================================================
echo.
echo   Folder: %CD%
echo.

REM ============================================================
REM Step 1: Required tools
REM ============================================================
where curl.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: curl.exe not found.
    echo Requires Windows 10 1803 or newer.
    pause
    exit /b 1
)
if not exist "%TAR_EXE%" (
    echo ERROR: %TAR_EXE% not found.
    echo Requires Windows 10 1803 or newer.
    pause
    exit /b 1
)
if not exist "python\python.exe" (
    echo ERROR: embedded Python not found at python\python.exe
    echo Make sure you are running this from inside the HavenExtractor folder.
    pause
    exit /b 1
)
if not exist "mod\haven_extractor.py" (
    echo ERROR: mod\haven_extractor.py not found.
    echo Make sure you are running this from inside the HavenExtractor folder.
    pause
    exit /b 1
)

REM ============================================================
REM Step 2: Helper script for JSON parsing (ships alongside this .bat)
REM ============================================================
set "HELPER=%~dp0_haven_updater_helper.py"
if not exist "%HELPER%" (
    echo ERROR: _haven_updater_helper.py not found alongside this .bat.
    echo Re-download the latest UPDATE_HAVEN_EXTRACTOR.bat and the helper file from:
    echo   https://github.com/Voyagers-Haven-LLC/nms-haven-extractor/releases/latest
    pause
    exit /b 1
)

REM ============================================================
REM Step 3: Read current version from haven_extractor.py
REM ============================================================
set "CUR_VER="
for /f "delims=" %%V in ('""python\python.exe" "%HELPER%" version "mod\haven_extractor.py""') do set "CUR_VER=%%V"
if "!CUR_VER!"=="" (
    echo ERROR: Could not read __version__ from mod\haven_extractor.py
    pause
    exit /b 1
)
echo Current version: !CUR_VER!
echo Checking for updates...
echo.

REM ============================================================
REM Step 4: Fetch latest release info from GitHub
REM ============================================================
set "RELEASE_JSON=%TEMP%\haven_release.json"
if exist "!RELEASE_JSON!" del /f /q "!RELEASE_JSON!"

curl.exe -sS -L --max-time 20 -H "User-Agent: HavenExtractor-Updater" "https://api.github.com/repos/Voyagers-Haven-LLC/nms-haven-extractor/releases/latest" -o "!RELEASE_JSON!"
if errorlevel 1 (
    echo ERROR: Could not reach GitHub.
    echo Check your internet connection and try again.
    pause
    exit /b 1
)
if not exist "!RELEASE_JSON!" (
    echo ERROR: GitHub API returned no data.
    pause
    exit /b 1
)

set "LATEST_TAG="
for /f "delims=" %%V in ('""python\python.exe" "%HELPER%" tag "!RELEASE_JSON!""') do set "LATEST_TAG=%%V"
set "DL_URL="
for /f "delims=" %%U in ('""python\python.exe" "%HELPER%" url "!RELEASE_JSON!""') do set "DL_URL=%%U"

if "!LATEST_TAG!"=="" (
    echo ERROR: Could not parse latest version tag from GitHub response.
    pause
    exit /b 1
)
if "!DL_URL!"=="" (
    echo ERROR: No HavenExtractor-mod-*.zip asset found in latest release.
    pause
    exit /b 1
)

echo Latest version:  !LATEST_TAG!
echo.

REM ============================================================
REM Step 5: Compare versions
REM ============================================================
call :CmpVer "!CUR_VER!" "!LATEST_TAG!"
if !VER_CMP! GEQ 0 (
    echo You are already up to date.
    pause
    exit /b 0
)

echo ============================================================
echo   Update available: !CUR_VER! --^> !LATEST_TAG!
echo ============================================================
echo.

REM Soft warning if NMS is running
"%TASKLIST_EXE%" /FI "IMAGENAME eq NMS.exe" 2>nul | "%FIND_EXE%" /I "NMS.exe" >nul
if not errorlevel 1 (
    echo WARNING: No Man's Sky appears to be running.
    echo          Close NMS completely ^(check the system tray^) before continuing,
    echo          or the file overwrite will fail.
    echo.
)

set "CONFIRM="
set /p CONFIRM="Download and install update? (Y/N): "
if /i not "!CONFIRM!"=="Y" (
    echo Update cancelled.
    pause
    exit /b 0
)
echo.

REM ============================================================
REM Step 6: Download zip
REM ============================================================
set "ZIP_PATH=%TEMP%\haven_update.zip"
if exist "!ZIP_PATH!" del /f /q "!ZIP_PATH!"

echo Downloading...
curl.exe -sS -L --max-time 180 -H "User-Agent: HavenExtractor-Updater" "!DL_URL!" -o "!ZIP_PATH!"
if errorlevel 1 (
    echo ERROR: Download failed.
    pause
    exit /b 1
)
if not exist "!ZIP_PATH!" (
    echo ERROR: Download finished but the zip file is missing.
    pause
    exit /b 1
)
for %%F in ("!ZIP_PATH!") do set "ZIP_SIZE=%%~zF"
if !ZIP_SIZE! LSS 10000 (
    echo ERROR: Downloaded file is only !ZIP_SIZE! bytes - likely an error page.
    del /f /q "!ZIP_PATH!"
    pause
    exit /b 1
)
echo Download complete ^(!ZIP_SIZE! bytes^).
echo.

REM ============================================================
REM Step 7: Backup current mod folder
REM ============================================================
echo Backing up current mod folder...
if exist "mod_backup" rmdir /s /q "mod_backup"
xcopy "mod" "mod_backup" /E /I /Y /Q
if not exist "mod_backup\haven_extractor.py" (
    echo ERROR: Backup did not complete. Aborting before touching the mod folder.
    pause
    exit /b 1
)
echo.

REM ============================================================
REM Step 8: Preserve user config + cache
REM ============================================================
set "USER_CFG=%TEMP%\haven_user_cfg"
if exist "!USER_CFG!" rmdir /s /q "!USER_CFG!"
mkdir "!USER_CFG!"
if exist "mod\haven_config.json"    copy /Y "mod\haven_config.json"    "!USER_CFG!\haven_config.json"    >nul
if exist "mod\config.json"          copy /Y "mod\config.json"          "!USER_CFG!\config.json"          >nul
if exist "mod\adjective_cache.json" copy /Y "mod\adjective_cache.json" "!USER_CFG!\adjective_cache.json" >nul

REM ============================================================
REM Step 9: Extract zip + auto-detect layout (flat vs nested mod/)
REM ============================================================
echo Extracting update...
set "EXTRACT_DIR=%TEMP%\haven_update_extract"
if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
mkdir "!EXTRACT_DIR!"
"%TAR_EXE%" -xf "!ZIP_PATH!" -C "!EXTRACT_DIR!"
if errorlevel 1 (
    echo ERROR: Zip extraction failed. The download may be corrupted.
    pause
    exit /b 1
)

set "SRC_DIR="
if exist "!EXTRACT_DIR!\haven_extractor.py" (
    set "SRC_DIR=!EXTRACT_DIR!"
    echo Zip layout: flat
) else if exist "!EXTRACT_DIR!\mod\haven_extractor.py" (
    set "SRC_DIR=!EXTRACT_DIR!\mod"
    echo Zip layout: nested ^(mod/^)
) else (
    echo ERROR: Zip does not contain haven_extractor.py at the expected location.
    pause
    exit /b 1
)

REM ============================================================
REM Step 10: Install new files into mod\
REM ============================================================
echo Installing...
xcopy "!SRC_DIR!\*" "mod\" /E /Y /I /Q
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   ERROR: File copy failed.
    echo ============================================================
    echo Common causes:
    echo   - NMS is still running ^(close it completely, including system tray^)
    echo   - Antivirus / Windows Defender Controlled Folder Access blocking writes
    echo   - Mod folder is read-only or in a protected Windows location
    echo.
    echo Your previous mod folder is preserved at: mod_backup\
    pause
    exit /b 1
)
echo.

REM ============================================================
REM Step 11: Restore user config + cache
REM ============================================================
if exist "!USER_CFG!\haven_config.json"    copy /Y "!USER_CFG!\haven_config.json"    "mod\haven_config.json"    >nul
if exist "!USER_CFG!\config.json"          copy /Y "!USER_CFG!\config.json"          "mod\config.json"          >nul
if exist "!USER_CFG!\adjective_cache.json" copy /Y "!USER_CFG!\adjective_cache.json" "mod\adjective_cache.json" >nul

REM ============================================================
REM Step 12: Install numpy if missing (nms_namegen dependency)
REM ============================================================
echo Checking numpy dependency...
"python\python.exe" -c "import numpy" >nul 2>&1
if errorlevel 1 (
    echo   numpy not found - installing...
    "python\python.exe" -m pip install numpy --quiet
    if errorlevel 1 (
        echo   WARNING: numpy install failed. Procedural names may be unavailable.
    ) else (
        echo   numpy installed.
    )
) else (
    echo   numpy present.
)

REM ============================================================
REM Step 13: Verify version on disk matches release tag
REM ============================================================
set "NEW_VER="
for /f "delims=" %%V in ('""python\python.exe" "%HELPER%" version "mod\haven_extractor.py""') do set "NEW_VER=%%V"

echo.
if "!NEW_VER!"=="!LATEST_TAG!" (
    echo ============================================================
    echo   Update complete.  !CUR_VER! --^> !NEW_VER!
    echo ============================================================
    echo.
    echo   Disk version verified: !NEW_VER!
    echo   Backup at: mod_backup\
    echo   Your config and adjective cache have been preserved.
    echo.
    echo   Run RUN_HAVEN_EXTRACTOR.bat to start the extractor.
) else (
    echo ============================================================
    echo   UPDATE VERIFICATION FAILED
    echo ============================================================
    echo   Expected: !LATEST_TAG!
    echo   On disk:  !NEW_VER!
    echo.
    echo   Files were copied but the version string on disk does not match
    echo   the release tag. This usually means the extractor is being run
    echo   from a DIFFERENT folder than the one being updated.
    echo   Folder updated: %CD%\mod
    echo   Backup at: mod_backup\
)

REM Cleanup temp files
if exist "!ZIP_PATH!"     del /f /q "!ZIP_PATH!"
if exist "!EXTRACT_DIR!"  rmdir /s /q "!EXTRACT_DIR!"
if exist "!USER_CFG!"     rmdir /s /q "!USER_CFG!"
if exist "!RELEASE_JSON!" del /f /q "!RELEASE_JSON!"

echo.
pause
exit /b 0

REM ============================================================
REM Subroutine: Compare two semver versions
REM Args: %1 = first, %2 = second
REM Sets VER_CMP to -1 if first < second, 0 if equal, 1 if greater
REM ============================================================
:CmpVer
setlocal
for /f "tokens=1-3 delims=." %%a in ("%~1") do (
    set "A1=%%a" & set "A2=%%b" & set "A3=%%c"
)
for /f "tokens=1-3 delims=." %%a in ("%~2") do (
    set "B1=%%a" & set "B2=%%b" & set "B3=%%c"
)
set "RC=0"
if %A1% LSS %B1% set "RC=-1"
if %A1% GTR %B1% set "RC=1"
if %A1% EQU %B1% (
    if %A2% LSS %B2% set "RC=-1"
    if %A2% GTR %B2% set "RC=1"
    if %A2% EQU %B2% (
        if %A3% LSS %B3% set "RC=-1"
        if %A3% GTR %B3% set "RC=1"
    )
)
endlocal & set "VER_CMP=%RC%"
goto :eof
