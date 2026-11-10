@echo off
setlocal
cd /d "%~dp0"

echo === Installing dependencies ===
REM Single source of dependency truth: same file the CI and users install from.
pip install -r requirements-build.txt -q
if errorlevel 1 (
  echo.
  echo pip install failed -- see the output above. Build aborted.
  exit /b 1
)
echo.
echo === Building ProxyManager ===
REM pystray / PIL are imported lazily inside functions (deferred to tray use),
REM so PyInstaller's static analysis can't see them. --hidden-import keeps them
REM bundled; dropping them here will crash the app the moment it hides to tray.
REM --collect-all webview bundles pywebview's injected JS + platform backends;
REM --add-data ships the web UI (ui/) and the tray icon inside the exe.
REM Drop any EXE from a previous build first: a failed run must not leave the
REM old binary in dist\ for the release job to publish (CI checks it exists).
if exist dist\ProxyManager.exe del /f /q dist\ProxyManager.exe
pyinstaller --onefile --windowed --name ProxyManager --clean ^
  --icon assets\icon.ico ^
  --hidden-import pystray ^
  --hidden-import PIL ^
  --hidden-import PIL.Image ^
  --hidden-import PIL.ImageDraw ^
  --collect-all webview ^
  --add-data "ui;ui" ^
  --add-data "assets;assets" ^
  app.py
if errorlevel 1 (
  echo.
  echo PyInstaller failed -- see the output above. Build aborted.
  exit /b 1
)
echo.
echo === Done ===
echo Output: dist\ProxyManager.exe
REM CI has no interactive console; skip the pause there.
if "%CI%"=="" pause
exit /b 0
