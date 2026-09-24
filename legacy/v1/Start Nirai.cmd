@echo off
setlocal
cd /d "%~dp0"
set NIRAI_WORLD_DEV=1
if not exist ".venv\Scripts\python.exe" (
  echo [Nirai] Project runtime is missing.
  echo Run "Setup Nirai Runtime.cmd" first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" nirai_bootstrap.py
if errorlevel 1 pause
