@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 app\server.py
) else (
  python -X utf8 app\server.py
)
if errorlevel 1 (
  echo Install Python 3.11+ and run: python -m pip install -r requirements.txt
  pause
)
