@echo off
rem NexFiremap launcher - Windows.
rem Prefers the project virtual environment (created by install.bat) and
rem falls back to the system Python if there isn't one.

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
    goto :run
)

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
echo Python was not found. Run install.bat first, or install Python 3.10+.
echo.
pause
exit /b 1

:run
"%PYEXE%" run.py --open %*
echo.
pause
