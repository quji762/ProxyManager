@echo off
setlocal
cd /d "%~dp0"

rem Launch ProxyManager (web UI). No arguments needed.
if not exist "app.py" (
    echo app.py not found next to start.bat - run this script from the repo root.
    pause
    exit /b 1
)

rem Preferred: windowless launch via the pyw launcher (no console window).
where pyw >nul 2>nul && (start "" pyw -3.13 "app.py" & exit /b 0)

rem Fallback: py launcher with a minimized console window.
where py >nul 2>nul && (start /min "" py -3.13 "app.py" & exit /b 0)

echo Python not found. Install Python 3.13+ (Microsoft Store or python.org) first.
pause
exit /b 1
