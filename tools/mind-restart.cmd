@echo off
rem Restart the mind after a verified full copy of the idea. See mind-restart.ps1.
setlocal
rem Do not pass pwsh 7's PSModulePath to Windows PowerShell 5.1; let 5.1 rebuild its own defaults.
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mind-restart.ps1" %*
