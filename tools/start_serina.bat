@echo off
chcp 65001 >nul
title Serina
cd /d D:\Products

rem ---- Ollama が起動していなければ起動する ----
curl -s -o nul --max-time 2 http://127.0.0.1:11434/api/version
if not errorlevel 1 goto ollama_ready

echo Ollama を起動しています...
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
) else (
    start "" /min cmd /c "ollama serve"
)

set /a _tries=0
:wait_ollama
set /a _tries+=1
if %_tries% gtr 30 (
    echo Ollama の起動を60秒待ちましたが応答がありません。手動で起動してから再実行してください。
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
