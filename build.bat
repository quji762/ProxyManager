@echo off
echo === Installing dependencies ===
pip install pywebview psutil pystray Pillow pyinstaller -q
echo.
echo === Building ProxyManager ===
REM pystray / PIL are imported lazily inside functions (deferred to tray use),
REM so PyInstaller's static analysis can't see them. --hidden-import keeps them
REM bundled; dropping them here will crash the app the moment it hides to tray.
REM --collect-all webview bundles pywebview's injected JS + platform backends;
REM --add-data ships the web UI (ui/) and the tray icon inside the exe.
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
echo.
echo === Done ===
echo Output: dist\ProxyManager.exe
pause
