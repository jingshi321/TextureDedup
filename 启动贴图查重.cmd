@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set TEMP=%~dp0save\tmp
set TMP=%~dp0save\tmp
if not exist "%TEMP%" mkdir "%TEMP%"
py -3 -B server.py
if errorlevel 1 python -B server.py
if errorlevel 1 pause
