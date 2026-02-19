@echo off
setlocal

REM Build a Windows executable with PyInstaller (console mode).
REM Run this in Windows cmd.exe from the repository root.

if not exist "bridge_server.py" (
  echo [ERROR] bridge_server.py not found. Run this script at the repository root.
  exit /b 1
)

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python is not available in PATH.
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

python -m pip install pyinstaller
if errorlevel 1 exit /b 1

python -m PyInstaller --noconfirm --clean --onefile --console --name codex2ollama-bridge bridge_server.py
if errorlevel 1 (
  echo [ERROR] Build failed.
  exit /b 1
)

if not exist "dist\codex2ollama-bridge.exe" (
  echo [ERROR] Build output not found: dist\codex2ollama-bridge.exe
  exit /b 1
)

echo [OK] Build complete: dist\codex2ollama-bridge.exe
echo [INFO] Copy windows\start_bridge.bat next to the exe for easy startup.
exit /b 0
