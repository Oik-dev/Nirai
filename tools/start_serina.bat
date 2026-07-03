@echo off
rem Serina launcher: start Ollama if needed, wait for it, then run the REPL.
rem NOTE: keep this file ASCII-only. cmd.exe misparses multibyte batch files
rem       after "chcp 65001" (labels/goto silently break).
chcp 65001 >nul
title Serina
cd /d D:\Products
set PYTHONIOENCODING=utf-8
rem Halve KV-cache VRAM so num_ctx 8192 fits in 8GB (only effective when
rem this bat starts Ollama; setx user env covers the auto-start case).
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_KV_CACHE_TYPE=q8_0

curl -s -o nul --max-time 2 http://127.0.0.1:11434/api/version
if not errorlevel 1 goto ollama_ready

echo Starting Ollama...
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
) else (
    start "" /min cmd /c "ollama serve"
)

set _tries=0
:wait_ollama
set /a _tries+=1
if %_tries% gtr 30 (
    echo Ollama did not respond within 60 seconds. Start it manually and retry.
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
curl -s -o nul --max-time 2 http://127.0.0.1:11434/api/version
if errorlevel 1 goto wait_ollama

:ollama_ready
python dev\serina\app\repl.py
echo.
pause
