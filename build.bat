@echo off
echo === Installing dependencies ===
pip install customtkinter psutil pystray Pillow pyinstaller -q
echo.
echo === Building ProxyManager ===
pyinstaller --onefile --windowed --name ProxyManager --clean proxy_manager.py
echo.
echo === Done ===
echo Output: dist\ProxyManager.exe
pause
