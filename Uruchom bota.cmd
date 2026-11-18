@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo Brak srodowiska Python: venv\Scripts\python.exe
  pause
  exit /b 1
)
"venv\Scripts\python.exe" -X utf8 "run_bot.py"
echo.
echo Sesja bota zakonczona. Nacisnij dowolny klawisz, aby zamknac.
pause >nul
