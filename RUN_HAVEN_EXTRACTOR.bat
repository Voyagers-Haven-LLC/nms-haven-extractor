@echo off
REM Haven Extractor - dev launcher for this checkout. Same as the player bat except the
REM embedded Python lives in dist\python here instead of a sibling python\ folder.
setlocal
cd /d "%~dp0"
if not exist "dist\python\python.exe" ( echo ERROR: embedded python missing at dist\python\ - see distuild_release.py & pause & exit /b 1 )
if not exist "mod\launcher.py" ( echo ERROR: mod\launcher.py missing. & pause & exit /b 1 )
set "PATH=%~dp0dist\python;%~dp0dist\python\Scripts;%PATH%"
cd mod
"..\dist\python\python.exe" launcher.py
echo.
pause
