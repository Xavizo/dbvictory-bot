@echo off
setlocal
cd /d "%~dp0"
if exist "DBVictory Launcher.exe" (
  start "" "DBVictory Launcher.exe"
  exit /b 0
)
if not exist "venv\Scripts\python.exe" (
  echo Brak srodowiska Python: venv\Scripts\python.exe
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -X utf8 "run_bot.py"
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0
