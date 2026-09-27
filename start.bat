@echo off
setlocal
cd /d "%~dp0"

rem Launch ProxyManager (web UI). Tries, in order:
rem   1. the Python launcher at its default install location (works even when
rem      the launcher directory is missing from PATH)
rem   2. pyw / py from PATH, preferring 3.13 down to 3.9 then the default
rem   3. the Microsoft Store pythonw alias
rem Before launching, one interpreter is probed for the runtime dependencies
rem (pywebview et al.); a missing `pip install` used to exit silently, which
rem read as "double-clicked and nothing happened".

set "LAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher"
set "PY_CMD="
set "PY_VER="

if exist "%LAUNCHER%\pyw.exe" (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    "%LAUNCHER%\pyw.exe" -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD="%LAUNCHER%\pyw.exe""
      set "PY_VER= -%%V"
      goto :have_py
    )
  )
)

where pyw >nul 2>nul && (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    pyw -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD=pyw"
      set "PY_VER=-%%V"
      goto :have_py
    )
  )
  set "PY_CMD=pyw"
  set "PY_VER="
  goto :have_py
)

where py >nul 2>nul && (
  for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD=py"
      set "PY_VER=-%%V"
      goto :have_py
    )
  )
  set "PY_CMD=py"
  set "PY_VER="
  goto :have_py
)

if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe"
    set "PY_VER="
    goto :have_py
)

echo Python not found. Install Python 3.9+ (Microsoft Store or python.org) first.
pause
exit /b 1

:have_py
rem Probe the runtime deps once with a console interpreter (pyw hides stderr).
rem PY_CHECK holds a `py [-V]` form when the launcher is on PATH; without it the
rem dependency probe is skipped (the Store alias has no console sibling).
set "PY_CHECK="
if defined PY_VER (
  where py >nul 2>nul && set "PY_CHECK=py%PY_VER%"
)
if not defined PY_CHECK (
  where py >nul 2>nul && set "PY_CHECK=py"
)
if defined PY_CHECK (
  %PY_CHECK% -c "import webview, psutil" >nul 2>nul
  if errorlevel 1 (
    echo.
    echo Runtime dependencies are missing. Run:
    echo   %PY_CHECK:~0,2% %PY_CHECK:~2% -m pip install -r requirements.txt
    echo then start ProxyManager again.
    echo.
    pause
    exit /b 1
  )
)

start "" %PY_CMD%%PY_VER% "app.py"
exit /b 0
