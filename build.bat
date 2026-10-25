@echo off
REM ===========================================================
REM  build.bat - Build Windows .exe (relay / agent / console)
REM  Put this next to the *.py sources and run it.
REM  ASCII-only to avoid cmd codepage (mojibake) problems.
REM  Output: dist\relay.exe dist\agent.exe dist\console.exe
REM ===========================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/3] Checking Python...
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo [ERROR] Python was not found on PATH.
  echo   - Install Python 3.11 or 3.12 ^(64-bit^) from python.org
  echo   - During install, CHECK "Add python.exe to PATH"
  echo   - Then open a NEW command window and run build.bat again
  echo.
  pause
  exit /b 1
)
%PY% --version

echo [2/3] Installing dependencies ^(pyinstaller, cryptography^)...
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade pyinstaller cryptography
if errorlevel 1 (
  echo [ERROR] Dependency install failed. Check your internet connection.
  pause
  exit /b 1
)

echo [3/3] Building executables...
%PY% -m PyInstaller --onefile --clean --noconfirm --name relay   --paths . relay_server.py
%PY% -m PyInstaller --onefile --clean --noconfirm --name agent   --paths . agent.py
%PY% -m PyInstaller --onefile --clean --noconfirm --name console --paths . console.py
%PY% -m PyInstaller --onefile --clean --noconfirm --name remote-support-demo --paths . run_demo.py

echo.
echo Build done. Output files:
dir /b dist
echo.
pause
endlocal
