@echo off
setlocal
cd /d "%~dp0"

rem Launch ProxyManager (web UI). Tries, in order:
rem   1. the Python launcher at its default install location (works even when
rem      the launcher directory is missing from PATH)
rem   2. pyw / py from PATH, preferring 3.13, then 3.12, then 3.11
rem   3. the Microsoft Store pythonw alias, when that install is already 3.11+
rem Pillow 13 and requires-python need 3.11 or newer, so 3.9 and 3.10 are
rem never selected. Before launching, one console interpreter is probed for
rem the runtime dependencies; a missing pip install used to exit silently,
rem which read as "double-clicked and nothing happened".

set "LAUNCHER=%LOCALAPPDATA%\Programs\Python\Launcher"
set "PY_CMD="
set "PY_VER="

if exist "%LAUNCHER%\pyw.exe" (
  for %%V in (3.13 3.12 3.11) do (
    "%LAUNCHER%\pyw.exe" -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD="%LAUNCHER%\pyw.exe""
      set "PY_VER=-%%V"
      goto :have_py
    )
  )
)

where pyw >nul 2>nul && (
  for %%V in (3.13 3.12 3.11) do (
    pyw -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD=pyw"
      set "PY_VER=-%%V"
      goto :have_py
    )
  )
  pyw -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=pyw"
    set "PY_VER="
    goto :have_py
  )
)

where py >nul 2>nul && (
  for %%V in (3.13 3.12 3.11) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD=py"
      set "PY_VER=-%%V"
      goto :have_py
    )
  )
  py -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=py"
    set "PY_VER="
    goto :have_py
  )
)

if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" (
  "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" (
    set "PY_CMD="%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe""
    set "PY_VER="
    rem Probe this same install. The generic probe below would otherwise
    rem pick a different `py` on PATH.
    set "PY_CHECK="%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe""
    goto :have_py
  )
)

echo Python 3.11 or newer was not found.
echo Install it from python.org or the Microsoft Store, then run this again.
pause
exit /b 1

:have_py
rem Probe runtime deps with a console interpreter (pyw hides stderr). Prefer
rem the launcher's py.exe so the check matches the interpreter we launch even
rem when that directory is not on PATH. clr is the pythonnet package.
rem Missing modules are accumulated via a subroutine so this block does not
rem need delayed expansion (a "!" in a user path must survive intact).
if not defined PY_CHECK if exist "%LAUNCHER%\py.exe" if defined PY_VER (
  set "PY_CHECK="%LAUNCHER%\py.exe" %PY_VER%"
)
if not defined PY_CHECK if defined PY_VER (
  where py >nul 2>nul && set "PY_CHECK=py %PY_VER%"
)
if not defined PY_CHECK (
  where py >nul 2>nul && set "PY_CHECK=py"
)
set "MISSING="
if defined PY_CHECK (
  for %%M in (webview psutil pystray PIL clr) do (
    %PY_CHECK% -c "import %%M" >nul 2>nul
    if errorlevel 1 call :add_missing %%M
  )
)
if defined MISSING goto :deps_missing

start "" %PY_CMD% %PY_VER% "app.py"
exit /b 0

:add_missing
set "MISSING=%MISSING% %1"
exit /b 0

:deps_missing
echo.
echo Missing runtime dependencies:%MISSING%
echo Run:
echo   %PY_CHECK% -m pip install -r requirements.txt
echo then start ProxyManager again.
echo.
pause
exit /b 1
