@echo off
rem One-click delivery bootstrap for Windows. Double-click this file.
rem All human-readable output (Chinese) comes from bootstrap.py, which writes
rem Unicode to the Windows console; keep this file ASCII-only to avoid codepage issues.
setlocal
cd /d "%~dp0..\.."

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ was not found on PATH.
  echo Please install Python 3.11+ from https://www.python.org/downloads/ and retry.
  echo See README.md - "One-click delivery" section for details.
  pause
  exit /b 1
)

python scripts/A/bootstrap.py --start %*
echo.
echo Bootstrap finished or stopped. Press any key to close this window.
pause >nul
