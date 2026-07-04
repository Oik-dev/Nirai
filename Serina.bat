@echo off
rem Serina GUI ランチャー（このファイルへのショートカットをデスクトップ等に置く）
cd /d "%~dp0"
python app\gui_server.py
if errorlevel 1 pause
