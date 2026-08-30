@echo off
cd /d "D:\Products\Nirai"
set NIRAI_WORLD_DEV=1
python -m core
if errorlevel 1 pause
