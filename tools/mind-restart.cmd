@echo off
rem Restart the mind after a verified full copy of the idea. See mind-restart.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mind-restart.ps1" %*
