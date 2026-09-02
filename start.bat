@echo off
setlocal
cd /d "%~dp0"

rem Launch ProxyManager (web UI). Tries, in order:
rem   1. the Python launcher at its default install location (works even when
rem      the launcher directory is missing from PATH)
rem   2. pyw / py from PATH, preferring 3.13 down to 3.9 then the default
rem   3. the Microsoft Store pythonw alias

set "LAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher"

if exist "%LAUNCHER%\pyw.exe" (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    "%LAUNCHER%\pyw.exe" -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      start "" "%LAUNCHER%\pyw.exe" -%%V "app.py"
      exit /b 0
    )
  )
)

where pyw >nul 2>nul && (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    pyw -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      start "" pyw -%%V "app.py"
      exit /b 0
    )
  )
  start "" pyw "app.py"
  exit /b 0
)

where py >nul 2>nul && (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      start /min "" py -%%V "app.py"
      exit /b 0
    )
  )
  start /min "" py "app.py"
  exit /b 0
)

if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" "app.py"
    exit /b 0
)

echo Python not found. Install Python 3.9+ (Microsoft Store or python.org) first.
pause
exit /b 1
