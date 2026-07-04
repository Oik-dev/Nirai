@echo off
rem Serina GUI launcher (put a shortcut to this file on the desktop)
cd /d "%~dp0"
python app\gui_server.py
if errorlevel 1 pause
