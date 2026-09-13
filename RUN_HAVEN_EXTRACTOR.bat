@echo off
REM Haven Extractor 2.x - DEV launcher for this git checkout.
REM Runs THIS repo's mod2\ with the embedded Python image in dist\HavenExtractor\python
REM (the same image the Full release zip ships; dist\ is gitignored). haven.env lives
REM next to this file. Players get the same three lines via build_release.LAUNCHER_BAT.
setlocal
cd /d "%~dp0"
if not exist "dist\HavenExtractor\python\python.exe" (
  echo ERROR: embedded python missing at dist\HavenExtractor\python\ - see build_release.py --python-dir
  pause & exit /b 1
)
if not exist "mod2\launcher.py" ( echo ERROR: mod2\launcher.py missing. & pause & exit /b 1 )
set "PATH=%~dp0dist\HavenExtractor\python;%~dp0dist\HavenExtractor\python\Scripts;%PATH%"
cd mod2
"..\dist\HavenExtractor\python\python.exe" launcher.py
echo.
pause
