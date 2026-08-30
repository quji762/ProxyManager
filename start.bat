@echo off
setlocal
cd /d "%~dp0"

rem Default: Prototype 2.0 web UI (prototype\app.py).
rem Pass "tk" as the first argument to start the classic tkinter UI instead:
rem   start.bat      -> web UI (recommended)
rem   start.bat tk   -> classic tkinter UI
set "TARGET=prototype\app.py"
if /i "%~1"=="tk" set "TARGET=proxy_manager.py"

rem Preferred: windowless launch via the pyw launcher (no console window).
where pyw >nul 2>nul && (start "" pyw -3.13 "%TARGET%" & exit /b 0)

rem Fallback: py launcher with a minimized console window.
where py >nul 2>nul && (start /min "" py -3.13 "%TARGET%" & exit /b 0)

echo Python not found. Install Python 3.13+ (Microsoft Store or python.org) first.
pause
exit /b 1
