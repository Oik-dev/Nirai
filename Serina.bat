@echo off
rem Serina GUI launcher (put a shortcut to this file on the desktop)
cd /d "%~dp0"
rem Start SearXNG (Serina web-search engine). Skip with a warning if Docker is off.
docker compose -f infra\searxng\docker-compose.yml up -d
if errorlevel 1 echo [warn] SearXNG not started (is Docker running). Serina runs without web search.
python app\gui_server.py
if errorlevel 1 pause
