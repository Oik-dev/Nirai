@echo off
rem Serina all-in-one launcher (Ollama brain + GUI + Sunday weekly eval)
cd /d "%~dp0"

rem --- 0) Serina's soul, and the mind's own Python (see requirements.txt) ---
set "NIRAI_SOUL=D:\Products\Residents\Serina"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [error] %PY% not found. In this folder, run:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)

rem --- 1) Ensure Ollama (Serina brain) is running ---
curl -s http://127.0.0.1:11434/api/version >nul 2>&1
if not errorlevel 1 goto ollama_ready
echo [info] Starting Ollama...
where ollama >nul 2>&1
if errorlevel 1 (
  set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
) else (
  set "OLLAMA_EXE=ollama"
)
start "" /b "%OLLAMA_EXE%" serve
set /a tries=0
:wait_ollama
curl -s http://127.0.0.1:11434/api/version >nul 2>&1
if not errorlevel 1 goto ollama_ready
set /a tries+=1
if %tries% geq 30 (
  echo [warn] Ollama did not respond in time. Serina may fail to start.
  goto ollama_ready
)
timeout /t 1 /nobreak >nul
goto wait_ollama
:ollama_ready

rem --- 2) Sunday weekly eval in background (script skips non-Sunday / already-ran)
start "SerinaWeeklyEval" /b "%PY%" tools\run_weekly_eval.py >nul 2>&1

rem --- 3) Launch Serina GUI ---
"%PY%" app\gui_server.py
if errorlevel 1 pause
