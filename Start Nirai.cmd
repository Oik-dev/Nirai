@echo off
cd /d "D:\Products\Nirai\world"
call npm run dev
if errorlevel 1 pause
