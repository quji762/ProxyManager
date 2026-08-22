@echo off
echo === Installing dependencies ===
pip install customtkinter pyinstaller -q
echo.
echo === Building ProxyManager ===
pyinstaller --onefile --windowed --name ProxyManager --clean proxy_manager.py
echo.
echo === Done ===
echo Output: dist\ProxyManager.exe
pause
