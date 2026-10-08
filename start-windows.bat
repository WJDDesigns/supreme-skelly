@echo off
REM Supreme Skelly for Windows: double-click to install (first time) and start.
REM Needs Python 3.11+ from https://www.python.org/downloads/ ("Add python.exe to PATH" ticked).
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python isn't installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Setting up for the first time, this takes a minute...
  %PY% -m venv .venv || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet . || (pause & exit /b 1)

set SKELLY_WEB_DIR=%~dp0web
echo.
echo Supreme Skelly is starting. Opening http://localhost:8420 ...
echo Other devices on your network can use http://%COMPUTERNAME%:8420
echo Close this window to stop it.
start "" http://localhost:8420
".venv\Scripts\python.exe" -m skelly
pause
