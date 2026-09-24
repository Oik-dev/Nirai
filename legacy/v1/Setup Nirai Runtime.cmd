@echo off
setlocal
cd /d "%~dp0"

echo [Nirai] Preparing project-local Python runtime...
py -3.12 -V >nul 2>nul
if %errorlevel%==0 (
  py -3.12 -m venv --clear .venv
) else (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
  if errorlevel 1 goto :python_missing
  python -m venv --clear .venv
)
if errorlevel 1 goto :fail

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [Nirai] Runtime setup completed.
".venv\Scripts\python.exe" nirai_bootstrap.py --doctor
pause
exit /b 0

:python_missing
echo.
echo [Nirai] Python 3.12.x was not found.
echo Install Python 3.12.x, then run this file again.
pause
exit /b 2

:fail
echo.
echo [Nirai] Runtime setup failed.
pause
exit /b 1
