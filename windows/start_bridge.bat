@echo off
setlocal

REM Simple launcher for codex2ollama-bridge on Windows.
REM Optional environment variables before launch:
REM   set BRIDGE_PORT=11435
REM   set CODEX_MODEL=gpt-5
REM   set CODEX_MODEL_VERBOSITY=high

if "%BRIDGE_PORT%"=="" set BRIDGE_PORT=11435

if exist "%~dp0codex2ollama-bridge.exe" (
  echo [INFO] Starting codex2ollama-bridge.exe on port %BRIDGE_PORT%...
  "%~dp0codex2ollama-bridge.exe"
  exit /b %errorlevel%
)

if exist "%~dp0..\dist\codex2ollama-bridge.exe" (
  echo [INFO] Starting ..\dist\codex2ollama-bridge.exe on port %BRIDGE_PORT%...
  "%~dp0..\dist\codex2ollama-bridge.exe"
  exit /b %errorlevel%
)

echo [ERROR] codex2ollama-bridge.exe not found.
echo [HINT] Build first: windows\build_windows_exe.bat
exit /b 1
