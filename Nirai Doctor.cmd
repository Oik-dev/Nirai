@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [Nirai] Project runtime is missing.
  echo Run "Setup Nirai Runtime.cmd" first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" nirai_bootstrap.py --doctor
set EXITCODE=%errorlevel%
echo.
if not %EXITCODE%==0 echo [Nirai] One or more startup requirements are missing.
pause
exit /b %EXITCODE%
