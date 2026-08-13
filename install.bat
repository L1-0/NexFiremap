@echo off
rem NexFiremap installer - Windows.
rem Finds a Python interpreter and hands off to the real (cross-platform)
rem setup logic in scripts\setup_wizard.py.

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYEXE=python"
    goto :run
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYEXE=py"
    goto :run
)

echo.
echo Python was not found on PATH.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo ^(tick "Add python.exe to PATH" during install^) and run this again.
echo.
pause
exit /b 1

:run
"%PYEXE%" "%~dp0scripts\setup_wizard.py"
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
