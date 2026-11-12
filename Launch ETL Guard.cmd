@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 scripts\serve_demo.py
) else (
  python scripts\serve_demo.py
)
if errorlevel 1 pause
