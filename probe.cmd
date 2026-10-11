@echo off
REM Double-clickable launcher for the SPR-20 registered-races probe.
REM
REM Exists so the probe can be run without a terminal already open at the right
REM path: "cd" into a deep worktree directory was the step that actually blocked
REM this, not the OAuth flow. Double-click this file in Explorer, or run it from
REM anywhere -- any arguments are passed through, e.g. probe.cmd --show-races
REM
REM "cd /d %~dp0" makes the working directory this file's own directory, so
REM runsignup_oauth.py is importable and the report lands next to the script
REM rather than wherever Explorer happened to start us.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3 and try again,
  echo or run the probe with an explicit interpreter path.
  echo.
  pause
  exit /b 9
)

python probe_registered_races.py %*
set PROBE_EXIT=%ERRORLEVEL%

echo.
echo ---------------------------------------------------------------------------
if "%PROBE_EXIT%"=="0" echo Finished. Exit code 0.
if "%PROBE_EXIT%"=="3" echo Finished. Exit code 3 -- UPCOMING ONLY. See the verdict above.
if not "%PROBE_EXIT%"=="0" if not "%PROBE_EXIT%"=="3" echo Did not complete. Exit code %PROBE_EXIT%.
echo ---------------------------------------------------------------------------

REM Keeps the window open; without this a double-click would flash and vanish,
REM taking the verdict with it.
echo.
pause
exit /b %PROBE_EXIT%
