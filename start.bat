@echo off
setlocal
cd /d "%~dp0"

rem Launch ProxyManager (web UI). Tries, in order:
rem   1. the Python launcher at its default install location (works even when
rem      the launcher directory is missing from PATH)
rem   2. pyw / py from PATH
rem   3. the Microsoft Store pythonw alias
rem -3.13 targets the interpreter the dependencies are installed into.

set "LAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher"

if exist "%LAUNCHER%\pyw.exe" (start "" "%LAUNCHER%\pyw.exe" -3.13 "app.py" & exit /b 0)

where pyw >nul 2>nul && (start "" pyw -3.13 "app.py" & exit /b 0)
where py >nul 2>nul && (start /min "" py -3.13 "app.py" & exit /b 0)

if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" "app.py"
    exit /b 0
)

echo Python not found. Install Python 3.13+ (Microsoft Store or python.org) first.
pause
exit /b 1
