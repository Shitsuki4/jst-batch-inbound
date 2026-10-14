@echo off
setlocal
cd /d "%~dp0"
echo First install dependencies: python -m pip install -r requirements.txt pyinstaller
python -X utf8 -m PyInstaller build_exe.spec --noconfirm
if errorlevel 1 (
  echo Build failed. See errors above.
) else (
  echo Build complete. Output is in dist.
)
pause
