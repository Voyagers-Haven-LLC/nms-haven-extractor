@echo off
REM Haven Extractor - update the mod and its framework without starting the game.
setlocal
cd /d "%~dp0"
if not exist "dist\python\python.exe" ( echo ERROR: embedded python missing at dist\python\ - see distuild_release.py & pause & exit /b 1 )
set "PATH=%~dp0dist\python;%~dp0dist\python\Scripts;%PATH%"
cd mod
"..\dist\python\python.exe" launcher.py --update-only
echo.
pause
