@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "dist\ProxyManager.exe" (
  echo dist\ProxyManager.exe is missing. Run build.bat first.
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo python was not found on PATH.
  exit /b 1
)

set "APP_VERSION="
for /f "delims=" %%V in ('python scripts\app_version.py') do set "APP_VERSION=%%V"
if not defined APP_VERSION (
  echo Could not read APP_VERSION from config_store.py
  exit /b 1
)

copy /y "dist\ProxyManager.exe" "dist\ProxyManager-portable.exe" >nul
if errorlevel 1 exit /b 1

set "ISCC="
for %%P in (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
  "%ProgramFiles%\Inno Setup 7\ISCC.exe"
) do (
  if not defined ISCC if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
  echo Inno Setup 6 or 7 ^(ISCC.exe^) was not found.
  echo Install it from https://jrsoftware.org/isdl.php and rerun package.bat.
  exit /b 1
)

echo === Building installer %APP_VERSION% ===
"%ISCC%" /DMyAppVersion=%APP_VERSION% "%~dp0installer.iss"
if errorlevel 1 exit /b 1

echo.
echo Portable: dist\ProxyManager-portable.exe
echo Installer: dist\ProxyManager-setup.exe
exit /b 0
