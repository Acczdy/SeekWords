@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0seekwords.ps1" -Administrator %*
if errorlevel 1 pause
